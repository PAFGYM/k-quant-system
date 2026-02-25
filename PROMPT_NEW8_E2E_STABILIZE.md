# 프롬프트 8: 통합 테스트 + 안정화 + 실전 투입 준비

## 목표

프롬프트 1~7까지 구현된 모든 기능을 **실전 투입 가능한 상태**로 안정화.

1. 전체 흐름 E2E 테스트
2. 엣지 케이스 + 에러 핸들링 보강
3. 봇 시작 → 아침 브리핑 → 장바구니 매수 → 실시간 모니터링 → 매도 알림 전체 흐름 검증
4. 기존 2,081+ 테스트 유지하면서 E2E 테스트 추가
5. 로깅/디버깅 도구 보강

---

## 기존 인프라 점검

먼저 전체 테스트 통과 확인:
```bash
PYTHONPATH=src python3 -m pytest tests/ -x -q
```

모든 테스트 통과 후 아래 작업 시작.

---

## 작업 1: 봇 시작 → 전체 초기화 검증

### 확인 사항

1. **봇 시작 시 모든 import 성공하는지:**
```bash
PYTHONPATH=src python3 -c "from kstock.bot.bot import StockBot; print('OK')"
```

2. **새로 추가된 모듈 import 확인:**
```bash
PYTHONPATH=src python3 -c "
from kstock.backtest.engine import TradeCosts, run_portfolio_backtest
from kstock.core.risk_engine import calculate_historical_var, run_monte_carlo, run_stress_test
from kstock.ml.lstm_predictor import get_lstm_enhanced_prediction
print('All new modules imported OK')
"
```

3. **DB 마이그레이션 확인:**
```bash
PYTHONPATH=src python3 -c "
from kstock.store.sqlite import SQLiteStore
db = SQLiteStore(':memory:')
h = db.get_active_holdings()
print(f'Holdings columns: {list(h[0].keys()) if h else \"empty\"}')
# holding_type 컬럼이 있어야 함
"
```

실패 시: 해당 모듈의 import 에러 수정.

## 작업 2: 아침 브리핑 흐름 테스트

### E2E 테스트 시나리오

```python
# tests/test_e2e_morning.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_morning_briefing_sends_message():
    """07:50 아침 브리핑이 메시지를 보내는지."""
    # Mock: context.bot.send_message
    # Mock: self.db.get_active_holdings() → 보유종목 2개
    # Mock: self.macro_client.get_snapshot() → VIX 18, SPX +0.5%
    # Mock: self._scan_all_stocks() → 스캔 결과
    # 실행: job_morning_briefing(context)
    # 검증: send_message 호출됨, 메시지에 보유종목 현황 포함


@pytest.mark.asyncio
async def test_morning_briefing_no_holdings():
    """보유종목 없을 때 아침 브리핑 정상 동작."""
    # 보유종목 0개여도 에러 안 나고 시장 상황만 보내는지


@pytest.mark.asyncio
async def test_morning_briefing_weekend_skip():
    """주말에는 브리핑 안 보내는지."""
```

## 작업 3: 장바구니 매수 흐름 테스트

```python
# tests/test_e2e_buy_cart.py

@pytest.mark.asyncio
async def test_buy_cart_full_flow():
    """매수 시작 → 금액 입력 → 종목 담기 → 확정 전체 흐름."""
    # 1. bp:start → "금액 입력" 메시지
    # 2. "300" 텍스트 → 장바구니 모드 진입 (300만원)
    # 3. bp:view:scalp → 단타 종목 리스트
    # 4. bp:add:122630:scalp → 장바구니에 추가
    # 5. bp:done → 최종 확인 화면
    # 6. bp:confirm → 보유종목 등록
    # 검증: db.add_holding() 호출됨, holding_type 맞는지


@pytest.mark.asyncio
async def test_buy_cart_ai_recommendation():
    """AI 추천 버튼이 동작하는지."""
    # bp:ai → Claude API 호출 → 추천 결과 표시
    # Mock: Claude API


@pytest.mark.asyncio
async def test_buy_cart_cancel():
    """취소 시 장바구니 초기화."""
    # bp:cancel → user_data["buy_cart"] 삭제됨


@pytest.mark.asyncio
async def test_buy_cart_budget_overflow():
    """예산 초과 시 경고."""
    # 300만원 예산인데 500만원짜리 종목 담기 시도
```

## 작업 4: 실시간 모니터링 흐름 테스트

```python
# tests/test_e2e_realtime.py

def test_on_realtime_update_surge():
    """급등 감지 (+3%) 알림 발송."""
    # Mock: WebSocket 콜백으로 change_pct=3.5% 데이터 전달
    # 검증: _send_surge_alert 호출됨


def test_on_realtime_update_cooldown():
    """30분 쿨다운 내 중복 알림 방지."""
    # 1분 간격으로 같은 종목 급등 → 두 번째는 무시


def test_sell_target_reached():
    """목표가 도달 알림."""
    # 보유종목 목표가 5% → 현재가 매수가 대비 +5.1%
    # 검증: 알림 메시지 발송


def test_stop_loss_reached():
    """손절가 도달 알림."""


def test_scalp_close_reminder():
    """14:30 단타 청산 리마인더."""
    # 단타 종목 보유 중 → 14:30 알림


def test_swing_review_3days():
    """스윙 3일 미달 검토 알림."""
    # 스윙 종목 3일 보유 + 수익률 2% → 본전 매도 알림
```

## 작업 5: 백테스팅 통합 테스트

```python
# tests/test_e2e_backtest.py

def test_backtest_with_costs():
    """비용 포함 백테스트 수익률 < 비용 없는 수익률."""
    # 동일 종목 백테스트: costs=None vs costs=TradeCosts()
    # 검증: 비용 포함 시 수익률이 낮음


def test_portfolio_backtest_weights():
    """포트폴리오 비중 합산 = 100%."""


def test_backtest_kosdaq_no_tax():
    """코스닥 종목 매도세 0% 적용."""
```

## 작업 6: 리스크 엔진 통합 테스트

```python
# tests/test_e2e_risk.py

@pytest.mark.asyncio
async def test_advanced_risk_report_full():
    """고급 리스크 리포트 전체 생성."""
    # Mock: yfinance 가격 데이터
    # 검증: VaR, Monte Carlo, 스트레스 테스트 모두 포함


def test_var_with_single_stock():
    """종목 1개로 VaR 계산."""


def test_stress_test_all_scenarios():
    """5개 스트레스 시나리오 전체 실행."""


def test_risk_grade_bounds():
    """리스크 등급 A~F 범위 내."""
```

## 작업 7: 에러 핸들링 강화

### 모든 새 기능에 try-except 래핑 확인

```python
# 체크리스트:
# 1. 모든 async 함수에 try-except + logger.exception()
# 2. 모든 텔레그램 메시지 전송에 try-except
# 3. yfinance 호출 실패 시 graceful degradation
# 4. Claude API 호출 실패 시 fallback 메시지
# 5. WebSocket 콜백 에러가 전체 시스템 크래시 안 시키는지
# 6. DB 쿼리 실패 시 빈 결과 반환
```

### 중요 에러 시나리오

```python
# tests/test_error_handling.py

@pytest.mark.asyncio
async def test_yfinance_timeout():
    """yfinance 타임아웃 시 에러 안 나는지."""
    # Mock: yfinance가 Exception 던짐
    # 검증: 함수가 None/빈값 반환, 봇 크래시 안 함

@pytest.mark.asyncio
async def test_claude_api_failure():
    """Claude API 실패 시 fallback."""
    # Mock: anthropic.APIError
    # 검증: 사용자에게 "분석 실패" 메시지, 봇 계속 동작

def test_websocket_callback_error():
    """WebSocket 콜백 에러 시 다른 콜백 영향 없음."""

def test_empty_holdings_all_features():
    """보유종목 0개일 때 모든 기능 정상."""
    # 아침 브리핑, 리스크 리포트, 장바구니 등

def test_db_locked():
    """SQLite 동시 접근 시 재시도."""
```

## 작업 8: 로깅 강화

### 중요 이벤트 로깅 확인

```python
# 각 주요 이벤트에 INFO 로그가 있는지 확인:
logger.info("아침 브리핑 발송 (보유종목 %d개, 추천 %d개)", ...)
logger.info("장바구니 확정: %d종목, 총 %s원", ...)
logger.info("급등 감지: %s +%.1f%% (쿨다운 OK)", ...)
logger.info("매도 알림: %s %s 도달", ...)
logger.info("VaR 계산 완료: 95%%=%.2f%%", ...)
logger.info("LSTM 학습 완료: val_auc=%.4f, epochs=%d", ...)
logger.info("포트폴리오 백테스트 완료: %d종목, 수익률 %.1f%%", ...)
```

### 에러 로그

```python
# 모든 except 블록에 logger.exception() 또는 logger.error()
# logger.warning()은 치명적이지 않은 경우에만
```

## 작업 9: 봇 건강 체크 보강

기존 `job_daily_self_report` (21:00)에 새 기능 상태 추가:

```python
# 건강 체크 항목 추가:
health_items = {
    "WebSocket 콜백": self._surge_callback_registered,
    "LSTM 모델": os.path.exists("models/lstm_stock.pt"),
    "오늘 브리핑": self.db.get_last_job_run("morning_briefing") == today,
    "VaR 계산": True,  # 마지막 실행 시간 체크
    "단타 모니터링": len([h for h in holdings if h.get("holding_type") == "scalp"]) == 0 or self._current_monitor_interval <= 15,
}
```

## 작업 10: .gitignore 업데이트

```
# 기존에 이미 있을 수 있지만 확인:
models/
*.pt
*.pth
__pycache__/
*.pyc
.env
```

## 작업 11: 실전 전 최종 체크리스트

이 작업을 모두 완료한 후 **아래 명령을 순서대로 실행**하여 최종 검증:

```bash
# 1. 전체 테스트
PYTHONPATH=src python3 -m pytest tests/ -x -q

# 2. 새 모듈 import 확인
PYTHONPATH=src python3 -c "
from kstock.backtest.engine import TradeCosts
from kstock.core.risk_engine import calculate_historical_var, run_monte_carlo
from kstock.ml.lstm_predictor import get_lstm_enhanced_prediction
print('All imports OK')
"

# 3. 봇 시작 테스트 (5초 후 종료)
timeout 5 bash -c 'PYTHONPATH=src python3 -c "
from kstock.bot.bot import StockBot
print(\"Bot init OK\")
"' || true

# 4. DB 마이그레이션 확인
PYTHONPATH=src python3 -c "
from kstock.store.sqlite import SQLiteStore
db = SQLiteStore('kquant.db')
holdings = db.get_active_holdings()
print(f'Holdings: {len(holdings)}')
for h in holdings[:3]:
    print(f'  {h.get(\"name\", \"?\")} type={h.get(\"holding_type\", \"unknown\")}')
"

# 5. 코드 품질 (선택)
PYTHONPATH=src python3 -m py_compile src/kstock/backtest/engine.py
PYTHONPATH=src python3 -m py_compile src/kstock/core/risk_engine.py
PYTHONPATH=src python3 -m py_compile src/kstock/ml/lstm_predictor.py
echo "All compilations OK"
```

## 테스트 파일 목록

| 파일 | 테스트 범위 |
|------|----------|
| `tests/test_e2e_morning.py` | 아침 브리핑 전체 흐름 |
| `tests/test_e2e_buy_cart.py` | 장바구니 매수 전체 흐름 |
| `tests/test_e2e_realtime.py` | 실시간 모니터링 + 알림 |
| `tests/test_e2e_backtest.py` | 백테스팅 통합 |
| `tests/test_e2e_risk.py` | 리스크 엔진 통합 |
| `tests/test_error_handling.py` | 에러 시나리오 |

## 성공 기준

1. ✅ 기존 2,081+ 테스트 전부 통과
2. ✅ 새 E2E 테스트 전부 통과
3. ✅ 모든 새 모듈 import 성공
4. ✅ 봇 시작 → 크래시 없음
5. ✅ DB 마이그레이션 정상 (holding_type 컬럼 존재)
6. ✅ 로그에 에러 없음
7. ✅ 텔레그램에서 주요 기능 수동 테스트 통과

## 주의사항

| 항목 | 주의 |
|------|------|
| 기존 테스트 깨뜨리지 말 것 | 새 기능 추가 시 기존 테스트 영향 확인 |
| Mock 사용 | 외부 API(yfinance, KIS, Claude)는 반드시 Mock |
| 비동기 테스트 | `@pytest.mark.asyncio` + `pytest-asyncio` |
| 시간 의존 테스트 | `freezegun` 또는 수동 datetime mock |
| 텔레그램 메시지 길이 | 4096자 제한 — 긴 메시지 자동 분할 |
| PYTHONPATH=src | 모든 실행에 반드시 설정 |
| load_dotenv(override=True) | 필요한 곳에 있는지 확인 |
