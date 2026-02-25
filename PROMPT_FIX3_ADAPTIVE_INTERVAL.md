# 수정 3: VIX 기반 적응형 모니터링 interval

## 문제
- `intraday_monitor`와 `market_pulse` 잡이 모두 60초 고정 interval
- VIX 30 이상 극공포장에서도, VIX 15 안정장에서도 동일한 주기 → 급변장에서 느리고, 안정장에서 API 낭비
- VIX 기반 로직(market_pulse.py, market_regime.py)이 이미 존재하지만 스케줄러 interval에 연결 안 됨

## 기존 구조 파악

### schedule_jobs() (core_handlers.py L124-231)
```python
jq.run_repeating(self.job_intraday_monitor, interval=60, first=30, name="intraday_monitor")
jq.run_repeating(self.job_market_pulse, interval=60, first=60, name="market_pulse")
jq.run_repeating(self.job_macro_refresh, interval=60, first=10, name="macro_refresh")
```

### job_macro_refresh (scheduler.py L515-520)
```python
async def job_macro_refresh(self, context):
    try:
        await self.macro_client.refresh_now()
    except Exception as e:
        logger.debug("Macro refresh job error: %s", e)
```

## 작업 지시

### 작업 1: scheduler.py — 적응형 interval 상수 및 상태 변수

SchedulerMixin 클래스(또는 파일 상단)에 다음을 추가:

```python
# VIX 기반 적응형 모니터링 interval (초)
_ADAPTIVE_INTERVALS = {
    "calm":    {"intraday": 120, "pulse": 180},  # VIX < 18: 안정장
    "normal":  {"intraday": 60,  "pulse": 60},   # VIX 18~25: 보통
    "fear":    {"intraday": 30,  "pulse": 30},    # VIX 25~30: 공포장
    "panic":   {"intraday": 15,  "pulse": 15},    # VIX > 30: 극공포
}
```

SchedulerMixin의 `__init__` 또는 클래스 변수로:
```python
_current_vix_regime: str = "normal"
```

### 작업 2: scheduler.py — _get_vix_regime() 헬퍼 메서드

```python
@staticmethod
def _get_vix_regime(vix: float) -> str:
    """VIX 레벨에 따른 모니터링 레짐 분류."""
    if vix < 18:
        return "calm"
    elif vix < 25:
        return "normal"
    elif vix < 30:
        return "fear"
    else:
        return "panic"
```

### 작업 3: scheduler.py — _reschedule_monitors() 메서드

telegram.ext의 JobQueue는 `run_repeating`으로 등록한 잡을 직접 interval 변경할 수 없음.
따라서 기존 잡 제거 → 새 interval로 재등록하는 방식:

```python
def _reschedule_monitors(self, job_queue, new_regime: str) -> None:
    """VIX 레짐 변경 시 모니터링 잡 interval 재설정."""
    intervals = self._ADAPTIVE_INTERVALS[new_regime]

    # 기존 잡 제거
    for name in ("intraday_monitor", "market_pulse"):
        current_jobs = job_queue.get_jobs_by_name(name)
        for job in current_jobs:
            job.schedule_removal()

    # 새 interval로 재등록
    job_queue.run_repeating(
        self.job_intraday_monitor,
        interval=intervals["intraday"],
        first=intervals["intraday"],
        name="intraday_monitor",
    )
    job_queue.run_repeating(
        self.job_market_pulse,
        interval=intervals["pulse"],
        first=intervals["pulse"],
        name="market_pulse",
    )

    self._current_vix_regime = new_regime
    logger.info(
        "📊 모니터링 주기 변경: %s → intraday %ds, pulse %ds",
        new_regime, intervals["intraday"], intervals["pulse"],
    )
```

### 작업 4: scheduler.py — job_macro_refresh 수정

기존 job_macro_refresh(L515-520)를 수정하여 VIX 확인 + interval 조정 로직 추가:

```python
async def job_macro_refresh(self, context: ContextTypes.DEFAULT_TYPE) -> None:
    """매크로 데이터 갱신 + VIX 기반 적응형 모니터링 주기 조정."""
    try:
        await self.macro_client.refresh_now()

        # 적응형 interval 조정
        snap = await self.macro_client.get_snapshot()
        vix = getattr(snap, 'vix', 20)
        new_regime = self._get_vix_regime(vix)

        if new_regime != self._current_vix_regime:
            old_regime = self._current_vix_regime
            self._reschedule_monitors(context.job.job.scheduler.bot.job_queue, new_regime)
            # 주기 변경 알림 (공포/극공포 진입 시에만)
            if new_regime in ("fear", "panic") and self.chat_id:
                labels = {"calm": "안정", "normal": "보통", "fear": "⚠️ 공포", "panic": "🚨 극공포"}
                intervals = self._ADAPTIVE_INTERVALS[new_regime]
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        f"📊 시장 감시 강화\n\n"
                        f"VIX: {vix:.1f} ({labels[new_regime]})\n"
                        f"모니터링 주기: {intervals['intraday']}초\n"
                        f"({labels[old_regime]} → {labels[new_regime]})"
                    ),
                )
    except Exception as e:
        logger.debug("Macro refresh job error: %s", e)
```

주의: `context.job.job.scheduler.bot.job_queue` 경로가 정확하지 않을 수 있음.
telegram.ext에서 job_queue 접근은 보통 `context.job_queue` 또는 Application 객체를 통해서임.
실제 접근 방법은 기존 코드에서 `jq = app.job_queue` 패턴(core_handlers.py L125-126)을 참고하여:
- `self` 에 `_job_queue` 참조를 저장하거나
- `context` 객체에서 접근

가장 안전한 방법: `schedule_jobs()`에서 `self._job_queue = jq`로 저장해두고, `_reschedule_monitors(self._job_queue, new_regime)` 호출.

### 작업 5: core_handlers.py — schedule_jobs()에서 job_queue 참조 저장

```python
def schedule_jobs(self, app: Application) -> None:
    jq = app.job_queue
    self._job_queue = jq  # 적응형 interval용 참조 저장
    # ... 나머지 기존 코드
```

## VIX 레짐별 동작 요약

| VIX | 레짐 | intraday | pulse | 알림 |
|-----|------|----------|-------|------|
| < 18 | calm | 120초 | 180초 | 없음 |
| 18~25 | normal | 60초 | 60초 | 없음 |
| 25~30 | fear | 30초 | 30초 | "시장 감시 강화" 알림 |
| > 30 | panic | 15초 | 15초 | "시장 감시 강화" 알림 |

## 검증
1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. 봇 시작 로그에서 `_current_vix_regime` 초기값 확인
3. VIX 변동 시 로그: `📊 모니터링 주기 변경: normal → intraday 30s, pulse 30s` 확인
4. 공포장 진입 시 텔레그램에 "시장 감시 강화" 메시지 수신 확인
