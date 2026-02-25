# 신규 기능: 매수 플래너 + 실시간 급등 감지 + 매도 가이드

## 핵심 철학

이 시스템의 본질은 하나다: **사용자가 투자금을 입력하면, 매수부터 매도까지 완전한 케어를 제공한다.**

단순히 "이 종목 사세요"가 아니라:
- 왜 이 종목인지 (근거)
- 얼마에 사는지 (진입가)
- 잘 되면 언제 파는지 (목표가)
- 안 되면 언제 파는지 (손절가)
- 보유 중에 무엇을 확인해야 하는지 (모니터링)
- 상황 변화 시 어떻게 대응하는지 (알림)

**수익 = 좋은 진입 x 적시 이탈.** 진입만 잘해도 실패하고, 이탈을 관리하지 않으면 반드시 실패한다.

---

## 경제학적 추론: 왜 이 구조인가

### 1. 기대수익(Expected Return) 극대화

주식 매수의 기대수익:
```
E[R] = P(성공) × 목표수익률 + P(실패) × 손절률
```

예를 들어 초단기 B등급:
- 과거 유사 패턴 기반 성공 확률 60%로 가정
- E[R] = 0.6 × (+7%) + 0.4 × (-3%) = +3.0%

이 기대수익이 양수(+)인 경우에만 추천. 음수이거나 거래비용(왕복 0.3~0.5%) 이하면 "오늘은 관망" 권고.

**핵심: 시스템이 제공하는 모든 추천은 E[R] > 0 조건을 충족해야 한다.**

### 2. 켈리 기준(Kelly Criterion) — 적정 투자 비율

```
f* = (p × b - q) / b
```
- p: 성공 확률, q = 1-p: 실패 확률
- b: 성공 시 수익 / 실패 시 손실 (odds ratio)

이 공식으로 하나의 종목에 전체 자금 중 몇 %를 배분할지 결정.
- 단, 실전에서는 Full Kelly의 절반(Half Kelly)을 적용하여 리스크 관리

예: 성공률 60%, 목표 +7%, 손절 -3%
- b = 7/3 = 2.33
- f* = (0.6 × 2.33 - 0.4) / 2.33 = 0.43 (43%)
- Half Kelly = 21.5% → 300만원 중 약 65만원을 이 종목에

**결론: AI 분석 시 종목당 배분 비율도 제시해야 한다. 3종목이라도 균등 분배가 아닐 수 있다.**

### 3. 손실 비대칭성 — 손절이 수익보다 중요한 이유

-50% 손실을 복구하려면 +100% 수익이 필요하다.
- -10% → +11.1% 필요 (소폭, 관리 가능)
- -30% → +42.9% 필요 (심각)
- -50% → +100% 필요 (사실상 불가)

**그래서 초단기(-2~5%)와 단기(-3%)의 손절 기준이 타이트한 것이다.**
한 번의 큰 손실이 10번의 작은 수익을 무력화한다.

이 시스템에서 매도 가이드가 매수보다 더 중요한 이유:
- 매수 시점의 분석이 틀릴 수 있다 (확률의 문제)
- 하지만 손절 실행은 선택의 문제다 (실행만 하면 된다)
- 시스템이 손절 시점을 자동으로 알려주면, 사용자는 실행만 하면 된다

### 4. 시간 가치(Time Value of Money) — 보유 기간이 비용이다

자금이 묶여 있는 시간도 비용이다:
- 초단기: 1일 묶임 → 기회비용 낮음, 하지만 당일 안에 청산해야 함
- 단기: 3~5일 묶임 → 3일 지나도 수익 못 내면, 다른 기회를 놓치고 있는 것
- 중기/장기: 기회비용이 크지만, 목표 수익도 크므로 균형

**이것이 "3일 미달 시 본전 매도 검토" 알림의 경제학적 근거다.**
수익도 손실도 아닌 상태가 가장 위험하다 — 의사결정을 미루게 만들기 때문.

---

## 기술적 추론: 어떻게 구현 가능한가

### KIS WebSocket이 핵심 인프라

현재 시스템 상태:
- `KISWebSocket`이 이미 구현되어 있고, 08:50에 자동 연결됨 (scheduler.py:1369)
- 유니버스 62종목 전체가 구독됨
- 체결 데이터(`RealtimePrice`) 수신: 현재가, 등락률, 거래량, 매수/매도 잔량, 체결시간
- **`on_update(callback)` 인터페이스가 존재하지만 아무도 등록하지 않았음** (kis_websocket.py:485)
- 콜백 시그니처: `callback(event_type: str, ticker: str, data: RealtimePrice|Orderbook)`
- `_callbacks` 리스트로 관리, 체결/호가 수신마다 자동 호출 (kis_websocket.py:410, 462)

**이것은 이미 깔려 있는 파이프라인에 밸브만 연결하면 되는 상황이다.**

### 스코어링 시스템 (100점 만점)

가중치: 매크로(10%) + 수급(30%) + 펀더멘털(30%) + 기술적(20%) + 리스크(10%)
- 70점 이상: BUY 신호
- 55~69점: WATCH 신호
- 55점 미만: 대기

**주의: 수급(flow) 30%의 데이터 신뢰도 문제**
- `macro_client.py`의 `_generate_mock_snapshot()`에서 수급 데이터가 mock/random일 수 있음
- 실제 외인/기관 수급은 KIS REST API로 보완 필요 (향후 과제)
- 현재는 기술적 지표 + 매크로 기반 판단이 더 신뢰성 높음

### 7가지 전략 (STRATEGY_META)

| 전략 | 이름 | 목표 | 손절 | 보유기간 | 적합 기간 |
|------|------|------|------|----------|----------|
| A | 단기 반등 | +5% | -5% | 3~10일 | 단기 |
| B | ETF 레버리지 | +3.5% | -3% | 1~3일 | 초단기 |
| C | 장기 우량주 | +15% | -10% | 6개월~1년 | 장기 |
| D | 섹터 로테이션 | +10% | -7% | 1~3개월 | 중기 |
| E | 글로벌 분산 | +12% | -8% | 장기 | 장기 |
| F | 모멘텀 | +7% | -5% | 2~8주 | 단기/중기 |
| G | 돌파 | +5% | -2% | 3~10일 | 초단기/단기 |

### 시장 레짐과 전략 배분 (get_regime_mode)

| VIX | 레짐 | 현금비중 | 공격전략(F,G) | 방어전략(C,E) |
|-----|------|---------|-------------|-------------|
| < 15 | 공격 | 5% | 25% | 20% |
| 15~25 | 균형 | 15% | 15% | 35% |
| > 25 | 방어 | 35% | 0% | 30% |

**이 레짐이 초단기 추천 여부를 결정한다.** VIX > 25 방어 레짐에서 초단기 추천은 원칙적으로 안 함.

---

## 투자 기간 정의

| 기간 | 라벨 | 보유일 | 목표 수익 | 손절 | 비고 |
|------|------|--------|----------|------|------|
| 초단기 | ⚡ 당일~1일 | 당일 종가 매도 | +3~20% (ATR별) | ATR별 -2~5% | 리스크 A/B/C 등급 |
| 단기 | 🔥 3~5일 | 3~5 거래일 | +5~10% | -3% | 3일 미달 시 본전 매도 검토 |
| 중기 | 📊 1~3개월 | 20~60 거래일 | +10~15% | -7% | 주 1회 점검 |
| 장기 | 💎 6개월+ | 120+ 거래일 | +15~30% | -10% | 분기 실적 기준 |

### 초단기 리스크 등급 (ATR 기반)

변동성이 큰 종목일수록 목표도 크지만 리스크도 크다. ATR(Average True Range)로 분류:

| 등급 | ATR(20) | 목표 수익 | 손절 | 대상 | 성격 |
|------|---------|----------|------|------|------|
| A (안정) | < 2% | +3~5% | -2% | 대형주, 우량 ETF | 작지만 확실한 수익 |
| B (보통) | 2~4% | +5~10% | -3% | 중형주 | 리스크/리워드 균형 |
| C (공격) | > 4% | +10~20% | -5% | 소형주, 테마주 | 고위험. 경고 필수 |

---

## 전체 시스템 흐름

```
[07:50] 매수 플래너
    ↓ 사용자: 투자금 + 기간 선택
    ↓ 시스템: 스캔 → 필터 → AI 분석 → 추천 (매수가/목표가/손절가)
    ↓
[09:00~15:30] 실시간 모니터링 (KIS WebSocket)
    ├── 급등 감지 (+3% 이상) → 알림
    ├── 목표가 도달 → "수익 실현 검토" 알림
    ├── 손절가 도달 → "포지션 정리 검토" 알림
    └── [14:30] 초단기 미청산 → "종가 매도 검토" 알림
    ↓
[08:00 다음날] 단기 종목 3일 점검
    └── 수익률 3% 미만 → "본전 매도 검토" 알림
```

---

## Part A: 07:50 장 시작 전 매수 플래너

### UX 흐름

**Step 1: 자동 발송 (07:50 KST 평일)**
```
☀️ 주호님, 좋은 아침이에요

오늘 추가 매수 계획이 있으신가요?

[📈 매수 계획 있음]  [🏖️ 오늘은 쉴게]
```

**Step 2: "매수 계획 있음" → 금액 입력**
```
💰 투자 금액을 입력해주세요
(만원 단위 숫자만 입력)

예: 50 → 50만원, 300 → 300만원
```
→ `user_data["awaiting_buy_amount"] = True` 플래그

**Step 3: 금액 입력 후 → 기간 선택**
```
⏰ 300만원으로 투자 기간을 선택하세요

[⚡ 초단기 (당일~1일)]  [🔥 단기 (3~5일)]
[📊 중기 (1~3개월)]     [💎 장기 (6개월+)]
```

**Step 4: 기간 선택 → "분석 중..." → AI 추천**

**Step 5: 최종 추천 결과** (예시 — 초단기)
```
📋 주호님 맞춤 매수 추천

💰 예산: 300만원 | ⚡ 초단기 (당일~1일)
📊 VIX: 18.2 (안정) | 나스닥 +0.8%
📈 시장 레짐: 균형 모드 → 초단기 적정

━━━━━━━━━━━━━━━━━━━━

1️⃣ 에코프로 (247540) [리스크 B]
   현재가: 58,200원 | 점수: 85점
   ATR(20) 3.2% | RSI 32 (과매도) | 거래량 +45%
   🟢 매수: 57,800~58,500원 (51주, 약 297만원)
   🎯 목표: 62,000원 (+6.5%)
   🔴 손절: 56,400원 (-3%)
   💡 09:10 이전 거래량 확인 후 진입. 갭업 시 추격 금지
   📊 배분: 투자금의 65% (Kelly 기준)

2️⃣ ...

━━━━━━━━━━━━━━━━━━━━
⚠️ 참고용 분석이며 투자 지시가 아닙니다
📌 당일 종가 매도 목표. 14:30까지 목표 미달 시 종가 청산
💡 E[R] = +3.0% | 실패 시 최대 손실: 8,700원

[🔍 1번 상세분석] [🔍 2번 상세분석]
[⭐ 관심종목 추가] [❌ 패스]
```

### 작업 지시

#### 작업 A-1: core_handlers.py — schedule_jobs()에 잡 등록

`schedule_jobs()` 메서드(core_handlers.py L124-231)에 추가:

```python
# 매수 플래너 (07:50 평일)
jq.run_daily(
    self.job_premarket_buy_planner,
    time=dt_time(hour=7, minute=50, tzinfo=KST),
    days=(0, 1, 2, 3, 4),
    name="premarket_buy_planner",
)
```

같은 곳에 Application 참조도 저장 (Part B에서 사용):
```python
self._application = app  # WebSocket 콜백에서 bot 접근용
```

로그 메시지에 "buy_planner(weekday 07:50)" 추가.

#### 작업 A-2: scheduler.py — job_premarket_buy_planner 구현

```python
async def job_premarket_buy_planner(
    self, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """매일 07:50 장 시작 전 매수 플래너 질문."""
    if not self.chat_id:
        return
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 매수 계획 있음", callback_data="bp:yes"),
            InlineKeyboardButton("🏖️ 오늘은 쉴게", callback_data="bp:no"),
        ],
    ])
    await context.bot.send_message(
        chat_id=self.chat_id,
        text=(
            "☀️ 주호님, 좋은 아침이에요\n\n"
            "오늘 추가 매수 계획이 있으신가요?"
        ),
        reply_markup=keyboard,
    )
    self.db.upsert_job_run("premarket_buy_planner", _today(), status="success")
    logger.info("Premarket buy planner sent")
```

필요 import: `InlineKeyboardButton`, `InlineKeyboardMarkup` — scheduler.py 상단에 있는지 확인. 없으면 추가.

#### 작업 A-3: core_handlers.py — 콜백 dispatch에 "bp" 추가

handle_callback의 dispatch 딕셔너리(L1069-1117)에:
```python
"bp": self._action_buy_plan,
```

#### 작업 A-4: trading.py — _action_buy_plan 메인 콜백 핸들러

```python
async def _action_buy_plan(self, query, context, payload: str) -> None:
    """매수 플래너 콜백 핸들러.

    콜백: bp:yes, bp:no, bp:hz:{horizon}:{amount}, bp:dismiss
    """
    if payload == "yes":
        context.user_data["awaiting_buy_amount"] = True
        await query.edit_message_text(
            "💰 투자 금액을 입력해주세요\n"
            "(만원 단위 숫자만 입력)\n\n"
            "예: 50 → 50만원\n"
            "예: 300 → 300만원"
        )
        return

    if payload == "no":
        await query.edit_message_text(
            "🏖️ 알겠습니다!\n"
            "좋은 하루 보내세요, 주호님\n\n"
            "매수 계획이 생기면 언제든 말씀하세요"
        )
        return

    if payload == "dismiss":
        await query.edit_message_text("👋 확인했습니다.")
        return

    if payload.startswith("hz:"):
        parts = payload.split(":")
        if len(parts) < 3:
            return
        horizon = parts[1]   # scalp, short, mid, long
        amount = int(parts[2]) * 10000  # 만원 → 원

        await query.edit_message_text(
            "💭 주호님 맞춤 종목을 분석하고 있습니다...\n(약 30초 소요)"
        )

        try:
            result_text, buttons = await self._generate_buy_recommendations(
                horizon, amount,
            )
            keyboard = InlineKeyboardMarkup(buttons) if buttons else None
            await query.message.reply_text(result_text, reply_markup=keyboard)
        except Exception as e:
            logger.error("Buy planner error: %s", e, exc_info=True)
            await query.message.reply_text(
                "⚠️ 종목 분석 중 오류가 발생했습니다.\n잠시 후 다시 시도해주세요."
            )
```

#### 작업 A-5: core_handlers.py — 직접 금액 입력 처리

`handle_menu_text` 메서드 **가장 앞부분**에 추가 (다른 처리보다 먼저 체크):

```python
# 매수 플래너: 금액 입력 대기 중
if context.user_data.get("awaiting_buy_amount"):
    text = update.message.text.strip()
    import re
    nums = re.findall(r'\d+', text)
    if nums:
        amount_만원 = int(nums[0])
        context.user_data["awaiting_buy_amount"] = False
        context.user_data["buy_plan_amount"] = amount_만원

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚡ 초단기 (당일~1일)", callback_data=f"bp:hz:scalp:{amount_만원}"),
                InlineKeyboardButton("🔥 단기 (3~5일)", callback_data=f"bp:hz:short:{amount_만원}"),
            ],
            [
                InlineKeyboardButton("📊 중기 (1~3개월)", callback_data=f"bp:hz:mid:{amount_만원}"),
                InlineKeyboardButton("💎 장기 (6개월+)", callback_data=f"bp:hz:long:{amount_만원}"),
            ],
        ])
        await update.message.reply_text(
            f"⏰ {amount_만원}만원으로 투자 기간을 선택하세요",
            reply_markup=keyboard,
        )
        return
    else:
        await update.message.reply_text("숫자를 입력해주세요 (예: 100)")
        return
```

#### 작업 A-6: trading.py — 핵심 추천 로직 (_generate_buy_recommendations)

이 메서드가 시스템의 두뇌다. 기존 `_scan_all_stocks()` → 전략 필터 → ATR 등급 → Kelly 배분 → Claude Sonnet AI 분석.

```python
# 기간별 전략 매핑 (클래스 상수)
_HORIZON_STRATEGIES = {
    "scalp": {
        "strategies": {"B", "G"},          # ETF레버리지, 돌파
        "label": "⚡ 초단기 (당일~1일)",
        "hold_desc": "당일 종가 매도 목표. 14:30까지 목표 미달 시 종가 청산.",
    },
    "short": {
        "strategies": {"A", "G", "F"},     # 단기반등, 돌파, 모멘텀
        "label": "🔥 단기 (3~5일)",
        "hold_desc": "3~5 거래일 보유. 3일 내 +3% 미만이면 본전 매도 검토.",
    },
    "mid": {
        "strategies": {"D", "F"},          # 섹터로테이션, 모멘텀
        "label": "📊 중기 (1~3개월)",
        "hold_desc": "1~3개월 보유. 주 1회 기술지표 점검.",
    },
    "long": {
        "strategies": {"C", "E"},          # 장기우량주, 글로벌분산
        "label": "💎 장기 (6개월+)",
        "hold_desc": "6개월 이상. 분기 실적 기준 판단. 배당 수익 포함.",
    },
}

# 초단기 ATR 기반 리스크 등급
_SCALP_RISK_GRADES = {
    "A": {"atr_max": 2.0, "target_min": 3, "target_max": 5, "stop": -2, "label": "A (안정)", "win_rate": 0.65},
    "B": {"atr_max": 4.0, "target_min": 5, "target_max": 10, "stop": -3, "label": "B (보통)", "win_rate": 0.55},
    "C": {"atr_max": 999, "target_min": 10, "target_max": 20, "stop": -5, "label": "C (공격)", "win_rate": 0.45},
}

def _get_scalp_risk_grade(self, atr_pct: float) -> dict:
    """ATR(20) 비율로 초단기 리스크 등급 결정."""
    if atr_pct < 2.0:
        return self._SCALP_RISK_GRADES["A"]
    elif atr_pct < 4.0:
        return self._SCALP_RISK_GRADES["B"]
    else:
        return self._SCALP_RISK_GRADES["C"]

def _calculate_kelly_fraction(self, win_rate: float, target_pct: float, stop_pct: float) -> float:
    """Half Kelly 기준 적정 투자 비율 계산.

    Kelly: f* = (p*b - q) / b
    여기서 b = target/|stop| (odds ratio)
    Half Kelly 적용하여 실전 리스크 관리.
    """
    if stop_pct >= 0 or target_pct <= 0:
        return 0.1  # 기본 10%
    b = target_pct / abs(stop_pct)  # odds ratio
    q = 1 - win_rate
    kelly = (win_rate * b - q) / b
    half_kelly = max(0.05, min(kelly / 2, 0.40))  # 5%~40% 범위
    return round(half_kelly, 2)

def _calculate_expected_return(self, win_rate: float, target_pct: float, stop_pct: float) -> float:
    """기대수익률 계산. E[R] = P(win)*target + P(lose)*stop"""
    return win_rate * target_pct + (1 - win_rate) * stop_pct

async def _generate_buy_recommendations(
    self, horizon: str, amount_won: int,
) -> tuple[str, list]:
    """투자 기간 + 예산에 맞는 종목 추천 생성.

    핵심 로직:
    1. 전체 종목 스캔 (5분 캐시)
    2. 해당 기간 전략 필터링
    3. ATR 리스크 등급 (초단기)
    4. Kelly 비율로 종목별 배분
    5. E[R] > 0 검증
    6. Claude Sonnet AI로 정교한 분석
    """
    config = self._HORIZON_STRATEGIES.get(horizon)
    if not config:
        return "⚠️ 잘못된 투자 기간입니다.", []

    amount_만원 = amount_won // 10000

    # 0. 시장 레짐 확인 — 방어 모드에서 초단기 제한
    macro = await self.macro_client.get_snapshot()
    from kstock.signal.strategies import get_regime_mode
    regime = get_regime_mode(macro)

    if horizon == "scalp" and regime["mode"] == "defense":
        return (
            "🛡️ 현재 방어 모드 (VIX {:.1f})\n\n"
            "시장 변동성이 높아 초단기 매매는 권장하지 않습니다.\n"
            "단기 이상 기간을 선택하시거나, 시장 안정 후 재시도해주세요.\n\n"
            "💡 방어 모드에서는 현금 비중 35% 권장".format(macro.vix),
            [],
        )

    # 1. 전체 종목 스캔 (5분 캐시)
    now = datetime.now(KST)
    if (
        hasattr(self, '_scan_cache_time')
        and self._scan_cache_time
        and (now - self._scan_cache_time).total_seconds() < 300
        and self._last_scan_results
    ):
        results = self._last_scan_results
    else:
        results = await self._scan_all_stocks()
        self._last_scan_results = results
        self._scan_cache_time = now

    # 2. 전략 필터링
    target_strategies = config["strategies"]
    filtered = []
    for r in results:
        for sig in (r.strategy_signals or []):
            if sig.strategy in target_strategies and sig.action in ("BUY", "WATCH"):
                filtered.append((r, sig))
                break

    # BUY 우선, 점수 높은 순
    filtered.sort(key=lambda x: (0 if x[1].action == "BUY" else 1, -x[0].score.composite))
    top_picks = filtered[:5]

    if not top_picks:
        return (
            f"📋 {config['label']} 조건에 맞는 종목이 현재 없습니다.\n\n"
            "시장 상황이 해당 전략에 맞지 않을 수 있습니다.\n"
            "다른 기간을 선택하거나 장 시작 후 다시 확인해보세요.",
            [],
        )

    # 3. 종목 데이터 + ATR 등급 + Kelly 배분 + E[R] 계산
    picks_data = []
    for r, sig in top_picks:
        price = getattr(r.info, 'current_price', 0)
        atr_pct = getattr(r.tech, 'atr_pct', 3.0)

        if horizon == "scalp":
            risk_grade = self._get_scalp_risk_grade(atr_pct)
            target_pct = (risk_grade["target_min"] + risk_grade["target_max"]) / 2
            stop_pct = risk_grade["stop"]
            win_rate = risk_grade["win_rate"]
        else:
            risk_grade = None
            target_pct = sig.target_pct
            stop_pct = sig.stop_pct
            win_rate = min(sig.confidence, 0.7)  # confidence를 win_rate 근사값으로

        kelly_frac = self._calculate_kelly_fraction(win_rate, target_pct, stop_pct)
        expected_return = self._calculate_expected_return(win_rate, target_pct, stop_pct)

        # E[R] < 거래비용(0.5%)이면 스킵
        if expected_return < 0.5:
            continue

        allocated_won = int(amount_won * kelly_frac)
        qty = int(allocated_won / price) if price > 0 else 0
        invest_amount = qty * price

        picks_data.append({
            "name": r.name,
            "ticker": r.ticker,
            "price": price,
            "score": r.score.composite,
            "rsi": r.tech.rsi,
            "macd": r.tech.macd,
            "bb_pct": r.tech.bb_pct,
            "ma5": r.tech.ma5,
            "ma20": r.tech.ma20,
            "ma60": r.tech.ma60,
            "atr_pct": atr_pct,
            "risk_grade": risk_grade,
            "strategy": sig.strategy,
            "strategy_name": sig.strategy_name,
            "signal": sig.action,
            "confidence": sig.confidence,
            "reasons": sig.reasons,
            "quantity": qty,
            "invest_amount": invest_amount,
            "kelly_frac": kelly_frac,
            "expected_return": expected_return,
            "target_pct": target_pct,
            "stop_pct": stop_pct,
            "win_rate": win_rate,
        })

    if not picks_data:
        return (
            f"📋 {config['label']} 기간에 기대수익이 양수인 종목이 없습니다.\n\n"
            f"현재 시장에서 해당 전략의 수익 기대가 거래비용보다 낮습니다.\n"
            "💡 오늘은 관망하시는 것이 합리적입니다.",
            [],
        )

    # 4. Claude Sonnet AI 분석
    analysis = await self._ai_analyze_buy_picks(
        picks_data, config, horizon, amount_만원, macro, regime,
    )

    # 5. 결과 메시지
    regime_emoji = regime.get("emoji", "")
    regime_label = regime.get("label", "")
    header = (
        f"📋 주호님 맞춤 매수 추천\n\n"
        f"💰 예산: {amount_만원}만원 | {config['label']}\n"
        f"📊 VIX: {macro.vix:.1f} | 나스닥: {macro.nasdaq_change_pct:+.1f}%\n"
        f"{regime_emoji} 시장 레짐: {regime_label}\n\n"
        f"{'━' * 22}\n\n"
    )

    # E[R] 요약
    avg_er = sum(p["expected_return"] for p in picks_data[:3]) / min(len(picks_data), 3)
    max_loss = sum(
        abs(p["stop_pct"]) / 100 * p["invest_amount"]
        for p in picks_data[:3]
    )

    footer = (
        f"\n{'━' * 22}\n"
        f"⚠️ 참고용 분석이며 투자 지시가 아닙니다\n"
        f"📌 {config['hold_desc']}\n"
        f"💡 평균 E[R]: {avg_er:+.1f}% | 최대 손실: {max_loss:,.0f}원"
    )

    text = header + analysis + footer

    # 6. 버튼
    buttons = []
    for i, p in enumerate(picks_data[:3]):
        buttons.append([
            InlineKeyboardButton(f"🔍 {i+1}번 상세분석", callback_data=f"detail:{p['ticker']}"),
            InlineKeyboardButton("⭐ 즐겨찾기", callback_data=f"fav:add:{p['ticker']}:{p['name']}"),
        ])
    buttons.append([InlineKeyboardButton("❌ 패스", callback_data="bp:dismiss")])

    return text, buttons
```

#### 작업 A-7: trading.py — _ai_analyze_buy_picks AI 분석

Claude Sonnet으로 정교한 분석. 종목별 Kelly 배분, E[R], 리스크 등급까지 포함.

```python
async def _ai_analyze_buy_picks(
    self, picks: list[dict], config: dict, horizon: str,
    amount_만원: int, macro, regime: dict,
) -> str:
    """Claude Sonnet으로 매수 추천 종목 정교한 분석."""
    if not self.anthropic_key:
        return self._format_picks_basic(picks, config, horizon)

    # 종목 데이터 텍스트
    picks_text = ""
    for i, p in enumerate(picks, 1):
        risk_info = ""
        if p.get("risk_grade"):
            rg = p["risk_grade"]
            risk_info = (
                f"  ATR(20): {p['atr_pct']:.1f}% | 리스크: {rg['label']}\n"
                f"  등급별 목표: +{rg['target_min']}~{rg['target_max']}% | 손절: {rg['stop']}%\n"
            )
        picks_text += (
            f"\n종목 {i}: {p['name']} ({p['ticker']})\n"
            f"  현재가: {p['price']:,.0f}원 | 스코어: {p['score']:.0f}점\n"
            f"  RSI: {p['rsi']:.0f} | MACD: {p['macd']:+.0f} | BB%: {p['bb_pct']:.2f}\n"
            f"  5일선: {p['ma5']:,.0f} | 20일선: {p['ma20']:,.0f} | 60일선: {p['ma60']:,.0f}\n"
            f"{risk_info}"
            f"  전략: {p['strategy_name']} ({p['strategy']}) | 신호: {p['signal']}\n"
            f"  매수근거: {', '.join(p['reasons'][:3])}\n"
            f"  Kelly 배분: {p['kelly_frac']:.0%} ({p['invest_amount']:,.0f}원, {p['quantity']}주)\n"
            f"  E[R]: {p['expected_return']:+.1f}% | 승률: {p['win_rate']:.0%}\n"
            f"  목표: +{p['target_pct']:.1f}% | 손절: {p['stop_pct']:.1f}%\n"
        )

    horizon_rules = {
        "scalp": (
            "초단기 당일 매매 전략이다.\n"
            "- 종목별 ATR 기반 리스크 등급(A/B/C)이 제공됨. 등급별 목표/손절이 다름\n"
            "- A등급(안정): +3~5% 목표, -2% 손절. 대형주 위주\n"
            "- B등급(보통): +5~10% 목표, -3% 손절. 중형주\n"
            "- C등급(공격): +10~20% 목표, -5% 손절. 고위험 경고 필수\n"
            "- Kelly 배분 비율이 종목별로 다를 수 있음. 제공된 비율 참고\n"
            "- 장 시작 30분 내 거래량 확인 후 진입\n"
            "- 갭업 5% 이상 종목 추격 매수 금지\n"
            "- 14:30까지 목표 미달 시 종가 청산 (오버나잇 금지)\n"
            "- RSI 70+ 종목 제외. 거래량 전일 대비 +30% 이상 확인"
        ),
        "short": (
            "단기 3~5일 보유 전략이다.\n"
            "- 목표: +5~10%. 손절: -3%\n"
            "- 3거래일 내 +3% 미만이면 본전 매도 검토 (자금 기회비용)\n"
            "- 외인/기관 수급 전환 시 즉시 매도 검토\n"
            "- 이동평균선(5일, 20일) 지지/저항 기준으로 매수 범위 제시"
        ),
        "mid": (
            "중기 1~3개월 보유 전략이다.\n"
            "- 목표: +10~15%. 손절: -7%\n"
            "- 60일 이동평균선 위에 있는 종목 우선\n"
            "- 섹터 로테이션 흐름과 매크로 환경 중심 판단"
        ),
        "long": (
            "장기 6개월+ 보유 전략이다.\n"
            "- 목표: +15~30%. 손절: -10% (분기 실적 기준)\n"
            "- 펀더멘털(PER, ROE, 배당수익률) 중심\n"
            "- 분할 매수 계획 제시 (1/3씩 3회)"
        ),
    }

    rules = horizon_rules.get(horizon, "")

    prompt = (
        f"주호님이 오늘 {amount_만원}만원으로 {config['label']} 매수를 계획하고 있다.\n\n"
        f"[시장 상황]\n"
        f"VIX: {macro.vix:.1f} | S&P500: {macro.spx_change_pct:+.2f}% | "
        f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
        f"원/달러: {macro.usdkrw:,.0f}원 | 시장 레짐: {regime['label']}\n\n"
        f"[투자 기간 규칙]\n{rules}\n\n"
        f"[후보 종목 데이터 — Kelly 배분 및 E[R] 사전 계산됨]\n{picks_text}\n\n"
        f"위 후보 중 최적 3종목을 선정하여 아래 형식으로 추천하라.\n"
        f"후보가 3개 미만이면 있는 만큼만.\n"
        f"E[R]이 가장 높은 종목을 우선. 단, 리스크 분산도 고려.\n"
        f"시장이 불안하면 솔직히 '오늘은 관망' 권고.\n\n"
        f"형식 (종목당):\n"
        f"[번호 이모지] 종목명 (코드) [리스크 등급 — 초단기만]\n"
        f"   현재가: X원 | 점수: X점\n"
        f"   [핵심 기술지표 1줄]\n"
        f"   🟢 매수: 가격범위 (수량, 금액)\n"
        f"   🎯 목표: 가격 (+수익률%)\n"
        f"   🔴 손절: 가격 (-하락률%)\n"
        f"   📊 배분: X% (Kelly 기준) | E[R]: +X.X%\n"
        f"   💡 실전 팁 (1줄: 매수 타이밍, 주의사항)\n\n"
        f"볼드(**) 사용 금지. 한 문장 25자 이내. 이모지로 구분."
    )

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            temperature=0.2,
            system=(
                "너는 주호님의 전속 투자 참모 '퀀트봇'이다.\n"
                "CFA/CAIA 자격 + 계량금융(Quantitative Finance) 전문가.\n\n"
                "[절대 규칙]\n"
                "1. 매도/매수 '지시' 금지. '검토해보세요' 식으로\n"
                "2. 공포 유발 표현 금지\n"
                "3. 제공된 데이터만 사용. 학습 데이터의 과거 가격 사용 금지\n"
                "4. 볼드(**) 사용 금지. 이모지로 구분\n"
                "5. 초단기는 반드시 당일 청산 전제. 오버나잇 경고\n"
                "6. 단기는 3~5일 내 수익 실현 전제. 3일 후 본전 매도 검토\n"
                "7. Kelly 배분 비율과 E[R]이 제공됨. 이를 근거로 배분\n"
                "8. 시장이 불안하면 솔직히 '관망' 권고. 무리한 추천 금지\n"
                "9. C등급(ATR>4%) 종목은 '고위험' 경고 필수\n"
                "10. 갭업 5%+ 종목 추천 제외 또는 경고\n"
                "11. 손실 비대칭성 인지: -10%는 +11.1% 필요. 손절 기준 엄수 강조"
            ),
            messages=[{"role": "user", "content": prompt}],
        )

        from kstock.bot.chat_handler import _sanitize_response
        return _sanitize_response(response.content[0].text)

    except Exception as e:
        logger.error("Buy planner AI error: %s", e)
        return self._format_picks_basic(picks, config, horizon)

def _format_picks_basic(self, picks: list[dict], config: dict, horizon: str) -> str:
    """AI 없을 때 기본 포맷."""
    lines = []
    for i, p in enumerate(picks[:3], 1):
        rg = p.get("risk_grade")
        risk_label = f" [{rg['label']}]" if rg else ""
        target_price = int(p['price'] * (1 + p['target_pct'] / 100))
        stop_price = int(p['price'] * (1 + p['stop_pct'] / 100))
        lines.append(
            f"{['1️⃣','2️⃣','3️⃣'][i-1]} {p['name']} ({p['ticker']}){risk_label}\n"
            f"   현재가: {p['price']:,.0f}원 | 점수: {p['score']:.0f}점\n"
            f"   RSI {p['rsi']:.0f} | ATR {p['atr_pct']:.1f}%\n"
            f"   🟢 매수: {p['price']:,.0f}원 ({p['quantity']}주)\n"
            f"   🎯 목표: {target_price:,.0f}원 (+{p['target_pct']:.1f}%)\n"
            f"   🔴 손절: {stop_price:,.0f}원 ({p['stop_pct']:.1f}%)\n"
            f"   📊 배분: {p['kelly_frac']:.0%} | E[R]: {p['expected_return']:+.1f}%"
        )
    return "\n\n".join(lines)
```

---

## Part B: 장중 실시간 급등 감지 (KIS WebSocket)

### 왜 WebSocket 기반인가

yfinance는 15~20분 지연. 3% 급등을 포착하려면 실시간 데이터가 필수.
KIS WebSocket은 이미 62종목을 구독하고 있고, `on_update` 콜백만 연결하면 됨.

### 급등 감지 논리

```
1차 필터 (실시간, 모든 체결에 대해):
  - change_pct >= +3% (전일 종가 대비)
  - 30분 이내 동일 종목 중복 알림 없음
  - 장중(09:00~15:20)에만 동작

2차 필터 (알림 전 확인):
  - 기존 스캔 캐시에서 스코어 50점 이상
  - 스코어 미확인 종목도 일단 알림 (FOMO 방지보다 정보 제공 우선)
```

### 알림 형식
```
🚀 급등 감지: 에코프로 (247540)

현재가: 62,100원 (+5.8%)
매수세: 강한 매수세
📊 스코어: 78점 | RSI: 52
🆕 미보유

[🔍 상세분석] [⭐ 즐겨찾기 추가]
```

### 작업 지시

#### 작업 B-1: scheduler.py — 급등 감지 상태 변수

SchedulerMixin 클래스에 추가:

```python
# 급등 감지 + 매도 가이드 상태
_surge_cooldown: dict[str, float] = {}  # key → 마지막 알림 timestamp
_SURGE_COOLDOWN_SEC = 1800  # 급등 알림 30분 쿨다운
_SELL_TARGET_COOLDOWN_SEC = 3600  # 매도 알림 1시간 쿨다운
_SURGE_THRESHOLD_PCT = 3.0  # +3% 이상 감지
_surge_callback_registered: bool = False
_holdings_cache: list = []  # 보유종목 캐시 (매도 가이드용)
```

#### 작업 B-2: scheduler.py — job_ws_connect에 콜백 등록

기존 `job_ws_connect`(scheduler.py:1369)에서, `ok = await self.ws.connect()` 성공 후 추가:

```python
# 급등 감지 + 매도 가이드 콜백 등록 (최초 1회)
if not self._surge_callback_registered:
    self.ws.on_update(self._on_realtime_update)
    self._surge_callback_registered = True
    logger.info("Realtime surge/sell-guide callback registered")
```

**중요: `on_update` 콜백은 동기 함수여야 한다.** 비동기 작업은 내부에서 `asyncio.create_task()` 사용.

#### 작업 B-3: scheduler.py — _on_realtime_update 통합 콜백

하나의 콜백에서 급등 감지 + 매도 가이드를 모두 처리:

```python
def _on_realtime_update(self, event_type: str, ticker: str, data) -> None:
    """KIS WebSocket 실시간 업데이트 콜백.

    동기 함수. 비동기 작업은 asyncio.create_task() 사용.
    """
    if event_type != "price":
        return

    now = time.time()
    now_kst = datetime.now(KST)

    # 장중 시간 체크 (09:00 ~ 15:20)
    if now_kst.hour < 9 or (now_kst.hour >= 15 and now_kst.minute > 20):
        return

    # 1. 급등 감지 (+3% 이상)
    if hasattr(data, 'change_pct') and data.change_pct >= self._SURGE_THRESHOLD_PCT:
        last_alert = self._surge_cooldown.get(f"surge:{ticker}", 0)
        if now - last_alert >= self._SURGE_COOLDOWN_SEC:
            self._surge_cooldown[f"surge:{ticker}"] = now
            asyncio.create_task(self._send_surge_alert(ticker, data))

    # 2. 보유종목 목표가/손절가 체크
    self._check_sell_targets(ticker, data, now)
```

#### 작업 B-4: scheduler.py — _send_surge_alert

```python
async def _send_surge_alert(self, ticker: str, data) -> None:
    """급등 감지 알림 발송."""
    if not self.chat_id or not hasattr(self, '_application'):
        return
    try:
        # 종목명 조회
        name = ticker
        for item in self.all_tickers:
            if item.get("code") == ticker:
                name = item.get("name", ticker)
                break

        # 보유 여부
        is_held = any(h.get("ticker") == ticker for h in self._holdings_cache)

        # 스캔 캐시에서 스코어 확인 (50점 미만이면 스킵)
        score_info = ""
        if hasattr(self, '_last_scan_results') and self._last_scan_results:
            for r in self._last_scan_results:
                if r.ticker == ticker:
                    if r.score.composite < 50:
                        logger.debug("Surge skipped (low score): %s", ticker)
                        return
                    score_info = f"📊 스코어: {r.score.composite:.0f}점 | RSI: {r.tech.rsi:.0f}"
                    break

        held_tag = "📦 보유중" if is_held else "🆕 미보유"
        pressure = getattr(data, 'pressure', '중립')

        text = (
            f"🚀 급등 감지: {name} ({ticker})\n\n"
            f"현재가: {data.price:,.0f}원 ({data.change_pct:+.1f}%)\n"
            f"매수세: {pressure}\n"
            f"{score_info}\n"
            f"{held_tag}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔍 상세분석", callback_data=f"detail:{ticker}"),
                InlineKeyboardButton("⭐ 즐겨찾기", callback_data=f"fav:add:{ticker}:{name}"),
            ],
        ])

        await self._application.bot.send_message(
            chat_id=self.chat_id, text=text, reply_markup=keyboard,
        )
        logger.info("Surge alert: %s %+.1f%%", ticker, data.change_pct)
    except Exception as e:
        logger.error("Surge alert error %s: %s", ticker, e)
```

---

## Part C: 매도 가이드 시스템

### 왜 매도 가이드가 가장 중요한가

경제학적으로:
- 행동경제학의 "처분 효과(Disposition Effect)": 사람은 수익 종목을 너무 일찍 팔고, 손실 종목을 너무 늦게 판다
- 시스템이 자동으로 "지금 팔아야 할 때"를 알려주면 이 편향을 극복할 수 있다
- 매수는 기대수익(E[R])의 문제, 매도는 행동 편향 극복의 문제

### 3가지 매도 트리거

1. **목표가/손절가 도달** (실시간, WebSocket)
   - 보유종목 현재가가 매수가 대비 목표% 또는 손절% 도달 시 알림
   - holding_type별 기준 다름 (scalp: +3%/-2%, swing: +5%/-3%, 등)

2. **14:30 초단기 청산 리마인더** (일일 스케줄)
   - holding_type == 'scalp'인 종목이 있으면 14:30에 "종가 청산 검토" 알림
   - 현재 손익 표시

3. **단기 3일 미달 검토** (일일 스케줄, 08:00)
   - holding_type == 'swing'인 종목이 3거래일 경과 + 수익률 3% 미만이면 알림
   - "자금의 기회비용" 관점

### 작업 지시

#### 작업 C-1: scheduler.py — _check_sell_targets (실시간)

```python
def _check_sell_targets(self, ticker: str, data, now: float) -> None:
    """보유종목 목표가/손절가 도달 여부 확인. 동기 함수."""
    if not self._holdings_cache:
        return

    for h in self._holdings_cache:
        if h.get("ticker") != ticker:
            continue

        buy_price = h.get("buy_price", 0)
        if buy_price <= 0:
            continue

        change_from_buy = (data.price - buy_price) / buy_price * 100
        holding_type = h.get("holding_type", "auto")
        name = h.get("name", ticker)

        # 쿨다운
        alert_key = f"sell:{ticker}"
        if now - self._surge_cooldown.get(alert_key, 0) < self._SELL_TARGET_COOLDOWN_SEC:
            return

        # holding_type별 목표가/손절가
        targets = {
            "scalp":     {"target": 3.0,  "stop": -2.0},
            "swing":     {"target": 5.0,  "stop": -3.0},
            "position":  {"target": 12.0, "stop": -7.0},
            "long_term": {"target": 20.0, "stop": -10.0},
            "auto":      {"target": 5.0,  "stop": -3.0},
        }
        t = targets.get(holding_type, targets["auto"])

        if change_from_buy >= t["target"]:
            self._surge_cooldown[alert_key] = now
            asyncio.create_task(self._send_sell_guide(
                name, ticker, data.price, buy_price,
                change_from_buy, "target", holding_type,
            ))
        elif change_from_buy <= t["stop"]:
            self._surge_cooldown[alert_key] = now
            asyncio.create_task(self._send_sell_guide(
                name, ticker, data.price, buy_price,
                change_from_buy, "stop", holding_type,
            ))
```

#### 작업 C-2: scheduler.py — _send_sell_guide 알림

```python
async def _send_sell_guide(
    self, name: str, ticker: str, current_price: float,
    buy_price: float, change_pct: float,
    alert_type: str, holding_type: str,
) -> None:
    """매도 가이드 알림."""
    if not self.chat_id or not hasattr(self, '_application'):
        return

    type_labels = {
        "scalp": "⚡ 초단기", "swing": "🔥 단기",
        "position": "📊 중기", "long_term": "💎 장기", "auto": "📌 자동",
    }
    type_label = type_labels.get(holding_type, "📌")

    if alert_type == "target":
        emoji, title = "🎯", "목표가 도달"
        action = "수익 실현을 검토해보세요"
    else:
        emoji, title = "🔴", "손절가 도달"
        action = "포지션 정리를 검토해보세요"

    profit_loss = (current_price - buy_price) * 1  # 1주 기준
    text = (
        f"{emoji} {title}: {name} ({ticker})\n\n"
        f"현재가: {current_price:,.0f}원 ({change_pct:+.1f}%)\n"
        f"매수가: {buy_price:,.0f}원\n"
        f"유형: {type_label}\n\n"
        f"💡 {action}"
    )

    try:
        await self._application.bot.send_message(chat_id=self.chat_id, text=text)
        logger.info("Sell guide: %s %s %.1f%%", ticker, alert_type, change_pct)
    except Exception as e:
        logger.error("Sell guide error: %s", e)
```

#### 작업 C-3: schedule_jobs()에 매도 가이드 잡 등록

```python
# 14:30 초단기 청산 리마인더
jq.run_daily(
    self.job_scalp_close_reminder,
    time=dt_time(hour=14, minute=30, tzinfo=KST),
    days=(0, 1, 2, 3, 4),
    name="scalp_close_reminder",
)

# 08:00 단기 종목 3일 미달 검토
jq.run_daily(
    self.job_short_term_review,
    time=dt_time(hour=8, minute=0, tzinfo=KST),
    days=(0, 1, 2, 3, 4),
    name="short_term_review",
)
```

#### 작업 C-4: scheduler.py — job_scalp_close_reminder

```python
async def job_scalp_close_reminder(self, context: ContextTypes.DEFAULT_TYPE) -> None:
    """14:30 초단기 보유종목 청산 리마인더."""
    if not self.chat_id:
        return

    holdings = self.db.get_active_holdings()
    scalp_holdings = [h for h in holdings if h.get("holding_type") == "scalp"]
    if not scalp_holdings:
        return

    lines = ["⏰ 초단기 종목 청산 점검 (14:30)\n"]
    for h in scalp_holdings:
        name = h.get("name", "")
        ticker = h.get("ticker", "")
        buy_price = h.get("buy_price", 0)
        rt = self.ws.get_price(ticker)
        if rt and buy_price > 0:
            pnl = (rt.price - buy_price) / buy_price * 100
            lines.append(f"  {name}: {rt.price:,.0f}원 ({pnl:+.1f}%)")
        else:
            lines.append(f"  {name}: 실시간 가격 미수신")

    lines.append("\n💡 당일 청산 전제. 오버나잇 리스크 유의.")
    await context.bot.send_message(chat_id=self.chat_id, text="\n".join(lines))
```

#### 작업 C-5: scheduler.py — job_short_term_review

```python
async def job_short_term_review(self, context: ContextTypes.DEFAULT_TYPE) -> None:
    """단기 보유종목 3거래일 경과 + 수익률 미달 점검."""
    if not self.chat_id:
        return

    holdings = self.db.get_active_holdings()
    now = datetime.now(KST)
    alerts = []

    for h in holdings:
        if h.get("holding_type") != "swing":
            continue
        buy_date_str = h.get("buy_date") or h.get("created_at", "")
        if not buy_date_str:
            continue
        try:
            buy_date = datetime.fromisoformat(buy_date_str[:10])
        except (ValueError, TypeError):
            continue

        days_held = (now.date() - buy_date.date()).days
        if days_held < 4:  # 약 3거래일 미만
            continue

        buy_price = h.get("buy_price", 0)
        name, ticker = h.get("name", ""), h.get("ticker", "")
        current_price = 0
        rt = self.ws.get_price(ticker) if self.ws.is_connected else None
        if rt:
            current_price = rt.price
        if current_price > 0 and buy_price > 0:
            pnl = (current_price - buy_price) / buy_price * 100
            if pnl < 3.0:
                alerts.append(f"  {name}: {current_price:,.0f}원 ({pnl:+.1f}%) [{days_held}일 보유]")

    if not alerts:
        return

    text = (
        "📋 단기 종목 검토 알림\n\n"
        "3거래일 경과 + 수익률 3% 미만:\n"
        + "\n".join(alerts)
        + "\n\n💡 본전 매도를 검토해보세요\n"
        "📊 자금이 묶여 있는 시간도 비용입니다 (기회비용)"
    )
    await context.bot.send_message(chat_id=self.chat_id, text=text)
```

#### 작업 C-6: 보유종목 캐시 갱신

기존 `job_intraday_monitor`의 시작 부분에 추가:
```python
# 보유종목 캐시 갱신 (매도 가이드용)
self._holdings_cache = self.db.get_active_holdings()
```

---

## 검증

1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. `grep -rn "premarket_buy_planner" src/` → schedule_jobs 등록 + 잡 구현 확인
3. `grep -rn '"bp"' src/kstock/bot/mixins/core_handlers.py` → dispatch 등록
4. `grep -rn "awaiting_buy_amount" src/` → 금액 입력 처리
5. `grep -rn "on_update\|_on_realtime_update" src/` → 콜백 등록
6. `grep -rn "scalp_close_reminder\|short_term_review" src/` → 매도 가이드 잡
7. `grep -rn "_application" src/kstock/bot/mixins/` → bot 참조

## 주의사항 요약

| 항목 | 주의 |
|------|------|
| on_update 콜백 | 동기 함수. async 작업은 `asyncio.create_task()` |
| bot 접근 | `self._application = app`을 schedule_jobs()에서 저장 |
| 급등 감지 | 장중(09:00~15:20)에만. 30분 쿨다운 |
| 매도 알림 | 1시간 쿨다운 (동일 종목) |
| 초단기 | 반드시 당일 청산. 14:30 리마인더. C등급은 고위험 경고 |
| 단기 | 3일 미달 시 본전 매도 검토 (기회비용 논거) |
| Kelly 배분 | Half Kelly 적용. 5~40% 범위 제한 |
| E[R] 검증 | 기대수익 < 0.5%(거래비용)이면 추천 안 함 |
| 시장 레짐 | 방어 모드(VIX>25)에서 초단기 제한 |
| 수급 데이터 | mock 가능성 있음. 기술적 지표 우선 신뢰 |
