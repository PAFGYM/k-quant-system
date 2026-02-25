# 프롬프트 5: 고급 백테스팅 엔진 업그레이드

## 현재 문제

`src/kstock/backtest/engine.py`의 `run_backtest()`:
- ❌ 수수료/세금 없음 → 수익률 뻥튀기
- ❌ 슬리피지 없음 → 실제보다 좋은 체결가 가정
- ❌ 단일 종목만 → 포트폴리오 레벨 백테스트 불가
- ❌ 기간 1년 고정 → 유연하지 않음
- ❌ 시간가중 수익률 없음 → 정확한 성과 비교 불가

## 목표

기존 `run_backtest()`는 건드리지 말고, **새로운 함수들을 추가**하여 프로급 백테스팅 지원.

---

## 기존 인프라 (건드리지 말 것)

- `BacktestTrade`, `BacktestResult` 데이터클래스 — 그대로 유지
- `run_backtest()` — 기존 기능 그대로 유지 (하위 호환)
- `format_backtest_result()` — 기존 포맷 유지
- `optimizer.py` — 파라미터 최적화 그대로

---

## 작업 1: 거래 비용 모델 추가

`engine.py`에 추가:

```python
@dataclass
class TradeCosts:
    """한국 주식 거래 비용 모델."""
    commission_rate: float = 0.00015    # KIS 수수료 0.015%
    sell_tax_rate: float = 0.0023       # 매도세 0.23% (코스피)
    slippage_rate: float = 0.001        # 슬리피지 0.1%

    def buy_cost(self, price: float, quantity: int) -> float:
        """매수 총비용: 수수료 + 슬리피지."""
        amount = price * quantity
        return amount * (self.commission_rate + self.slippage_rate)

    def sell_cost(self, price: float, quantity: int) -> float:
        """매도 총비용: 수수료 + 세금 + 슬리피지."""
        amount = price * quantity
        return amount * (self.commission_rate + self.sell_tax_rate + self.slippage_rate)

    def net_pnl(self, buy_price: float, sell_price: float, quantity: int) -> float:
        """수수료/세금/슬리피지 차감 후 순손익."""
        gross = (sell_price - buy_price) * quantity
        costs = self.buy_cost(buy_price, quantity) + self.sell_cost(sell_price, quantity)
        return gross - costs

    def net_pnl_pct(self, buy_price: float, sell_price: float) -> float:
        """비용 차감 후 순수익률(%)."""
        gross_pct = (sell_price - buy_price) / buy_price * 100
        cost_pct = (self.commission_rate * 2 + self.sell_tax_rate + self.slippage_rate * 2) * 100
        return gross_pct - cost_pct
```

## 작업 2: run_backtest()에 비용 적용

기존 `run_backtest()` 시그니처에 `costs: TradeCosts | None = None` 파라미터 추가.

- `costs`가 None이면 기존 로직 그대로 (하위 호환)
- `costs`가 주어지면 pnl_pct 계산 시 `costs.net_pnl_pct()` 사용
- `BacktestResult`에 `total_cost_pct: float = 0.0` 필드 추가 (누적 비용)

```python
# 기존 pnl 계산 부분 수정
if costs:
    pnl = costs.net_pnl_pct(entry_price, current)
else:
    pnl = (current - entry_price) / entry_price * 100
```

## 작업 3: 포트폴리오 레벨 백테스트

새 함수 `run_portfolio_backtest()`:

```python
@dataclass
class PortfolioBacktestResult:
    """포트폴리오 레벨 백테스트 결과."""
    period: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float          # 하방 변동성만 사용
    calmar_ratio: float           # 연 수익률 / MDD
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_holding_days: float
    total_cost_pct: float         # 총 거래비용
    per_stock_results: list[BacktestResult]  # 종목별 상세
    equity_curve: list[float]     # 일별 자산가치


def run_portfolio_backtest(
    tickers: list[dict],  # [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "weight": 0.4}, ...]
    period: str = "1y",
    initial_capital: float = 10_000_000,
    costs: TradeCosts | None = None,
    rebalance_days: int = 0,  # 0이면 리밸런싱 안 함
) -> PortfolioBacktestResult | None:
```

**구현 로직:**
1. 각 종목 히스토리 다운로드 (yfinance)
2. weight에 따라 자본 배분
3. 각 종목별 `run_backtest()` 실행 (costs 전달)
4. 일별 equity curve 합산
5. 포트폴리오 레벨 MDD, Sharpe, Sortino, Calmar 계산
6. `rebalance_days > 0`이면 N일마다 초기 비중으로 리밸런싱

**Sortino 계산:**
```python
downside_returns = [r for r in daily_returns if r < 0]
downside_std = np.std(downside_returns) if downside_returns else 1e-6
sortino = (annualized_return / 100) / (downside_std * np.sqrt(252))
```

**Calmar 계산:**
```python
calmar = annualized_return_pct / abs(max_drawdown_pct) if max_drawdown_pct != 0 else 0
```

## 작업 4: 텔레그램 포맷

```python
def format_portfolio_backtest(result: PortfolioBacktestResult) -> str:
```

출력 형식:
```
📊 포트폴리오 백테스트 결과
━━━━━━━━━━━━━━━━━━
기간: 2024-03-01 ~ 2025-02-25
초기 자본: 1,000만원 → 최종: 1,156만원

🟢 총 수익률: +15.6% (연환산 +15.6%)
📉 최대 낙폭: -8.3%
📊 샤프비율: 1.42
📊 소르티노: 1.85
📊 칼마비율: 1.88
⚖️ Profit Factor: 1.65

💰 총 거래비용: 2.3% (수수료+세금+슬리피지)
🔄 순수익률: +13.3% (비용 차감 후)

종목별:
  🟢 삼성전자 (40%): +8.2% (5승 2패)
  🟢 SK하이닉스 (35%): +22.1% (7승 3패)
  🔴 NAVER (25%): -3.5% (2승 4패)
```

## 작업 5: 기존 메뉴에 연결

**trading.py** 또는 **commands.py** — 기존 백테스트 메뉴에 "포트폴리오 백테스트" 옵션 추가:

```python
# 기존 백테스트 결과 표시 후 추가 버튼
[📊 포트폴리오 백테스트]  [📊 비용 포함 재실행]
```

- "비용 포함 재실행" → 같은 종목을 `TradeCosts()` 포함해서 다시 실행
- "포트폴리오 백테스트" → 보유종목 전체를 포트폴리오로 백테스트

**콜백:**
- `bt:portfolio` → 보유종목 기반 포트폴리오 백테스트
- `bt:withcost:{ticker}` → 비용 포함 재실행

## 검증

1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. 비용 있을 때 수익률이 비용 없을 때보다 항상 낮은지 확인
3. 포트폴리오 백테스트에서 종목별 합산이 전체와 일치하는지 확인
4. equity_curve 길이가 거래일수와 맞는지 확인

## 테스트 추가

`tests/test_backtest_pro.py`:

```python
def test_trade_costs_buy_sell():
    """수수료/세금 계산 정확성."""
    costs = TradeCosts()
    # 10,000원 100주 매수 비용
    buy_cost = costs.buy_cost(10000, 100)
    assert buy_cost > 0
    # 매도 비용 > 매수 비용 (세금 때문)
    sell_cost = costs.sell_cost(10000, 100)
    assert sell_cost > buy_cost

def test_net_pnl_pct_less_than_gross():
    """비용 차감 후 수익률이 총수익률보다 낮은지."""
    costs = TradeCosts()
    net = costs.net_pnl_pct(10000, 10300)  # +3% 총수익
    assert 0 < net < 3.0  # 비용 차감 후 3% 미만

def test_portfolio_backtest_basic():
    """포트폴리오 백테스트 기본 실행."""
    # 실제 데이터 다운로드가 필요하므로 mock 사용
    pass

def test_backtest_backward_compatible():
    """기존 run_backtest() costs=None일 때 기존과 동일."""
    pass
```

## 주의사항

| 항목 | 주의 |
|------|------|
| 하위 호환 | 기존 `run_backtest()` 시그니처 유지. costs=None이 기본값 |
| 코스닥 세금 | 코스닥은 매도세 0% → market 파라미터로 분기 |
| ETF 세금 | ETF는 매도세 없음 → ticker로 판별 (6자리 숫자가 아닌 경우) |
| yfinance 호출 | 포트폴리오 백테스트 시 여러 종목 동시 다운로드 → 속도 주의 |
| equity_curve | NaN 처리 필수 (일부 종목 상장일 다를 수 있음) |
| PYTHONPATH=src | 테스트 실행 시 반드시 설정 |
| load_dotenv | 있으면 사용, 없어도 동작해야 함 |
