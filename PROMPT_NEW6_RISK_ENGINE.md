# 프롬프트 6: 고급 리스크 엔진 (VaR + Monte Carlo + 스트레스 테스트)

## 현재 문제

`src/kstock/core/risk_manager.py`:
- ✅ MDD, 집중도, 섹터, 상관관계, 마진 — 잘 되어 있음
- ❌ VaR (Value at Risk) 없음 → "최대 얼마 잃을 수 있는지" 모름
- ❌ Monte Carlo 시뮬레이션 없음 → 미래 수익 분포 모름
- ❌ 상관관계가 섹터 프록시(0.9/0.3) → 실제 가격 상관관계 아님
- ❌ 스트레스 테스트가 `scenario_analyzer.py`에 고정 시나리오만 → 동적 스트레스 없음

`src/kstock/core/scenario_analyzer.py`:
- ✅ 4개 시나리오 (관세/금리/MSCI/폭락) 정의됨
- ❌ 실제 과거 데이터 기반이 아닌 수동 설정 충격값

## 목표

기존 `risk_manager.py`와 `scenario_analyzer.py`는 건드리지 말고, **새 파일 `src/kstock/core/risk_engine.py`** 생성.

---

## 기존 인프라 (건드리지 말 것)

- `risk_manager.py` — 기존 리스크 체크 그대로 유지
- `scenario_analyzer.py` — 기존 시나리오 그대로 유지
- `RISK_LIMITS` 상수 — 기존 한도값 유지
- `RiskReport`, `RiskViolation` 데이터클래스 — 유지

---

## 작업 1: 새 파일 risk_engine.py 생성

`src/kstock/core/risk_engine.py`:

```python
"""고급 리스크 엔진: VaR, Monte Carlo, 스트레스 테스트.

기존 risk_manager.py의 기본 리스크 체크를 보완하는 고급 분석 모듈.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
```

## 작업 2: 실제 상관관계 계산

기존 `risk_manager.py`의 섹터 프록시(0.9/0.3)를 대체하는 실제 가격 기반 상관관계:

```python
def calculate_real_correlation(
    price_histories: dict[str, pd.Series],  # ticker → close prices (DatetimeIndex)
    window: int = 60,  # 60일 수익률 기준
) -> pd.DataFrame:
    """실제 가격 데이터 기반 상관관계 행렬 계산.

    Args:
        price_histories: 종목별 종가 시리즈 (최소 60일)
        window: 수익률 계산 윈도우

    Returns:
        상관관계 행렬 (DataFrame)
    """
```

**로직:**
1. 각 종목 일일 수익률 계산 (pct_change)
2. 최근 `window`일 수익률로 상관행렬 계산
3. NaN은 섹터 프록시로 대체 (기존 risk_manager.py의 방식 fallback)

**yfinance로 가격 히스토리 가져오기:**
```python
async def _fetch_price_histories(
    tickers: list[dict],  # [{"ticker": "005930", "market": "KOSPI"}, ...]
    period: str = "6mo",
) -> dict[str, pd.Series]:
    """yfinance에서 종목별 종가 히스토리 가져오기."""
```

## 작업 3: Historical VaR (역사적 VaR)

```python
@dataclass
class VaRResult:
    """VaR 계산 결과."""
    var_95: float          # 95% VaR (금액, 음수)
    var_99: float          # 99% VaR (금액, 음수)
    var_95_pct: float      # 95% VaR (%, 음수)
    var_99_pct: float      # 99% VaR (%, 음수)
    cvar_95: float         # 95% CVaR (Conditional VaR, 평균 꼬리 손실)
    cvar_95_pct: float     # 95% CVaR (%)
    method: str            # "historical" | "parametric" | "monte_carlo"
    holding_period_days: int  # 보유기간 (기본 1일)
    confidence_text: str   # 텔레그램용 요약


def calculate_historical_var(
    portfolio_value: float,
    holdings: list[dict],  # [{"ticker": "005930", "weight": 0.4, "returns": [...]}]
    confidence: float = 0.95,
    holding_period: int = 1,  # 일
) -> VaRResult:
    """역사적 시뮬레이션 VaR.

    과거 수익률 분포에서 직접 백분위수 추출.
    """
```

**로직:**
1. 포트폴리오 일일 수익률 = Σ(weight_i × return_i)
2. 95% VaR = np.percentile(portfolio_returns, 5)  # 하위 5%
3. 99% VaR = np.percentile(portfolio_returns, 1)
4. CVaR = VaR 이하 수익률들의 평균
5. holding_period > 1이면 √T 스케일링: VaR × √(holding_period)

```python
# 핵심 계산
portfolio_returns = sum(w * r for w, r in zip(weights, return_arrays))
var_95_pct = float(np.percentile(portfolio_returns, (1 - confidence) * 100))
cvar_mask = portfolio_returns <= var_95_pct
cvar_95_pct = float(np.mean(portfolio_returns[cvar_mask])) if cvar_mask.any() else var_95_pct
```

## 작업 4: Parametric VaR (분산-공분산)

```python
def calculate_parametric_var(
    portfolio_value: float,
    weights: np.ndarray,         # 종목별 비중
    mean_returns: np.ndarray,    # 종목별 평균 수익률
    cov_matrix: np.ndarray,      # 공분산 행렬
    confidence: float = 0.95,
    holding_period: int = 1,
) -> VaRResult:
    """분산-공분산(Parametric) VaR.

    정규분포 가정. 상관관계 반영.
    """
```

**로직:**
```python
from scipy import stats  # scipy 없으면 수동 z-score

portfolio_std = np.sqrt(weights @ cov_matrix @ weights)
z_score = stats.norm.ppf(1 - confidence)  # 95% → -1.645
var_pct = z_score * portfolio_std * np.sqrt(holding_period)
```

**주의:** scipy가 없을 수 있음. z-score 직접 계산 fallback:
```python
_Z_SCORES = {0.95: -1.6449, 0.99: -2.3263}
```

## 작업 5: Monte Carlo VaR

```python
@dataclass
class MonteCarloResult:
    """Monte Carlo 시뮬레이션 결과."""
    var_95: float
    var_99: float
    var_95_pct: float
    var_99_pct: float
    cvar_95_pct: float
    expected_return_pct: float     # 기대 수익률 (중앙값)
    best_case_pct: float           # 95퍼센타일 (좋은 시나리오)
    worst_case_pct: float          # 5퍼센타일 (나쁜 시나리오)
    simulations: int               # 시뮬레이션 횟수
    distribution: list[float]      # 최종 수익률 분포 (히스토그램용, 100개 bin)


def run_monte_carlo(
    portfolio_value: float,
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    days: int = 20,           # 시뮬레이션 기간 (거래일)
    simulations: int = 10000,  # 시뮬레이션 횟수
) -> MonteCarloResult:
    """Monte Carlo 시뮬레이션으로 포트폴리오 수익 분포 예측."""
```

**로직:**
```python
# Cholesky 분해로 상관관계 반영된 랜덤 수익률 생성
L = np.linalg.cholesky(cov_matrix)

results = np.zeros(simulations)
for i in range(simulations):
    daily_returns = np.zeros(len(weights))
    portfolio_value_sim = portfolio_value

    for d in range(days):
        z = np.random.standard_normal(len(weights))
        correlated_z = L @ z
        daily_r = mean_returns + correlated_z  # 상관관계 반영
        portfolio_r = np.dot(weights, daily_r)
        portfolio_value_sim *= (1 + portfolio_r)

    results[i] = (portfolio_value_sim - portfolio_value) / portfolio_value * 100

# 벡터화 버전 (성능):
# np.random.multivariate_normal(mean_returns, cov_matrix, (simulations, days))
```

**성능 최적화:** 10,000번 시뮬레이션은 벡터화하면 1초 이내:
```python
# 벡터화 (권장)
random_returns = np.random.multivariate_normal(
    mean_returns, cov_matrix, (simulations, days)
)  # shape: (simulations, days, n_stocks)
portfolio_daily = np.tensordot(random_returns, weights, axes=(2, 0))  # (simulations, days)
cumulative = np.prod(1 + portfolio_daily, axis=1)  # (simulations,)
results = (cumulative - 1) * 100
```

## 작업 6: 동적 스트레스 테스트

기존 `scenario_analyzer.py`의 고정 시나리오를 보완:

```python
@dataclass
class StressTestResult:
    """스트레스 테스트 결과."""
    scenario_name: str
    portfolio_impact_pct: float     # 포트폴리오 전체 영향 (%)
    portfolio_impact_amount: float  # 금액
    per_stock_impact: list[dict]    # [{"ticker": ..., "name": ..., "impact_pct": ...}]
    recovery_days_estimate: int     # 예상 회복 기간
    historical_reference: str       # "2020 코로나: -33%" 등


HISTORICAL_STRESS_SCENARIOS = {
    "covid_crash": {
        "name": "코로나 폭락 (2020.03)",
        "market_impact": -0.33,
        "sector_multiplier": {
            "반도체": 0.8, "2차전지": 1.2, "자동차": 1.1,
            "바이오": 0.6, "금융": 1.3, "통신": 0.7,
            "엔터": 1.5, "조선": 1.0, "방산": 0.8,
            "기타": 1.0,
        },
        "recovery_days": 120,
    },
    "lehman_crisis": {
        "name": "리먼 사태 (2008)",
        "market_impact": -0.45,
        "sector_multiplier": {
            "금융": 1.8, "반도체": 1.2, "자동차": 1.5,
            "기타": 1.0,
        },
        "recovery_days": 365,
    },
    "china_shock": {
        "name": "중국 경기 둔화",
        "market_impact": -0.15,
        "sector_multiplier": {
            "2차전지": 1.5, "반도체": 1.3, "철강": 1.8, "화학": 1.6,
            "기타": 0.8,
        },
        "recovery_days": 60,
    },
    "rate_surge": {
        "name": "미국 금리 급등 (+1%p)",
        "market_impact": -0.12,
        "sector_multiplier": {
            "반도체": 1.3, "바이오": 1.5, "금융": 0.5,
            "기타": 1.0,
        },
        "recovery_days": 90,
    },
    "won_crisis": {
        "name": "원화 급락 (USD/KRW 1,500원)",
        "market_impact": -0.18,
        "sector_multiplier": {
            "자동차": 0.5, "조선": 0.4,  # 수출주는 오히려 이득
            "바이오": 1.2, "통신": 0.8,
            "기타": 1.0,
        },
        "recovery_days": 45,
    },
}


def run_stress_test(
    portfolio_value: float,
    holdings: list[dict],  # [{"ticker": ..., "name": ..., "eval_amount": ..., "sector": ...}]
    scenario_key: str = "all",  # "all"이면 전체 시나리오 실행
) -> list[StressTestResult]:
    """과거 위기 시나리오로 포트폴리오 스트레스 테스트."""
```

**로직:**
1. 각 시나리오 × 종목: `impact = market_impact × sector_multiplier × weight`
2. 포트폴리오 합산: `Σ(impact_i × weight_i)`
3. 회복 기간 추정: 시나리오별 고정값 + 포트폴리오 특성 보정

## 작업 7: 통합 리스크 리포트

모든 분석을 합쳐서 하나의 리포트로:

```python
@dataclass
class AdvancedRiskReport:
    """고급 리스크 통합 리포트."""
    date: str
    portfolio_value: float
    # 기존 리스크
    basic_report: RiskReport  # risk_manager.py에서
    # VaR
    historical_var: VaRResult | None
    parametric_var: VaRResult | None
    monte_carlo: MonteCarloResult | None
    # 상관관계
    correlation_matrix: dict | None  # {(ticker_a, ticker_b): corr}
    high_correlation_pairs: list[tuple[str, str, float]]  # corr > 0.7
    # 스트레스 테스트
    stress_results: list[StressTestResult]
    # 종합 등급
    risk_grade: str  # "A" (안전) ~ "F" (위험)
    risk_score: int  # 0~100 (높을수록 위험)


async def generate_advanced_risk_report(
    portfolio_value: float,
    holdings: list[dict],
    peak_value: float,
    daily_pnl_pct: float,
    yf_client=None,  # yfinance Korean client
) -> AdvancedRiskReport:
    """고급 리스크 통합 리포트 생성.

    1. 기존 risk_manager.check_risk_limits() 실행
    2. yfinance에서 가격 히스토리 가져오기
    3. 실제 상관관계 계산
    4. Historical VaR 계산
    5. Parametric VaR 계산
    6. Monte Carlo (10,000회) 실행
    7. 스트레스 테스트 (5개 시나리오) 실행
    8. 종합 리스크 등급 산출
    """
```

**리스크 등급 계산:**
```python
def _calculate_risk_grade(report: AdvancedRiskReport) -> tuple[str, int]:
    """종합 리스크 등급과 점수 계산.

    점수 요소 (0~100):
    - VaR 95% 크기: 0~25점 (큰 손실 = 높은 점수 = 위험)
    - MDD 수준: 0~25점
    - 집중도: 0~15점
    - 상관관계: 0~15점
    - 스트레스 테스트 최악: 0~20점

    등급:
    - A (0~20): 매우 안전
    - B (21~40): 안전
    - C (41~60): 보통
    - D (61~80): 주의
    - F (81~100): 위험
    """
```

## 작업 8: 텔레그램 포맷

```python
def format_advanced_risk_report(report: AdvancedRiskReport) -> str:
```

출력:
```
🛡️ 고급 리스크 리포트 (2025-02-25)
━━━━━━━━━━━━━━━━━━━━

📊 리스크 등급: B (안전) — 35점/100

💰 VaR (1일, 95%): -42,000원 (-0.42%)
   "95% 확률로 하루 최대 42,000원 손실"
💰 CVaR (95%): -68,000원 (-0.68%)
   "최악의 5% 상황 평균 68,000원 손실"

🎲 Monte Carlo (20일, 10,000회)
   기대 수익: +2.3%
   최선: +8.5% | 최악: -6.2%

📉 스트레스 테스트
   코로나 폭락: -28% (-280만원)
   리먼 사태: -38% (-380만원)
   원화 급락: -10% (-100만원)

🔗 고상관 종목: 삼성전자↔SK하이닉스 (0.82)

⚠️ 위반 사항: 없음
```

## 작업 9: 아침 브리핑에 VaR 추가

기존 `job_morning_briefing`의 AI 프롬프트에 VaR 데이터 추가:

```python
# scheduler.py job_morning_briefing에서
# 기존 holdings_text 만든 후 추가:
try:
    from kstock.core.risk_engine import calculate_historical_var
    # ... VaR 계산 후
    risk_text = f"VaR(95%): {var.var_95_pct:+.2f}% | 리스크등급: {grade}"
except Exception:
    risk_text = ""
```

## 작업 10: 메뉴 연결

기존 메뉴에 "고급 리스크" 접근점:

```python
# 기존 리스크 리포트 버튼 옆에
[🛡️ 고급 리스크 분석]
```

콜백: `risk:advanced` → `generate_advanced_risk_report()` 실행 → 결과 표시

## 검증

1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. VaR 95% < VaR 99% (99%가 더 큰 손실)
3. CVaR >= VaR (꼬리 평균은 VaR 이상)
4. Monte Carlo 기대수익이 과거 평균과 유사한지
5. 스트레스 테스트에서 수출주가 원화 급락 시 덜 영향받는지

## 테스트

`tests/test_risk_engine.py`:

```python
def test_var_95_less_than_99():
    """VaR 99%가 95%보다 더 큰 손실."""

def test_cvar_gte_var():
    """CVaR는 VaR 이상의 손실."""

def test_monte_carlo_distribution():
    """시뮬레이션 결과가 정규분포에 가까운지."""

def test_stress_test_all_scenarios():
    """5개 시나리오 전부 실행되는지."""

def test_risk_grade_calculation():
    """등급 A~F가 점수 범위에 맞는지."""
```

## 주의사항

| 항목 | 주의 |
|------|------|
| scipy | scipy 없을 수 있음. z-score는 상수 fallback 사용 |
| np.linalg.cholesky | 공분산 행렬이 양정치가 아닐 수 있음 → `nearPD` 보정 또는 대각 + epsilon |
| yfinance 호출 | 비동기로. 6개월 히스토리 가져오는데 시간 걸림 → 캐싱 고려 |
| 기존 risk_manager.py | 절대 수정 안 함. import해서 사용만 |
| 텔레그램 메시지 길이 | 4096자 제한. 긴 리포트는 분할 전송 |
| SECTOR_MAP | risk_manager.py에서 import |
| Monte Carlo 성능 | 벡터화 필수. for-loop 10,000회는 느림 |
| 상관관계 캐싱 | 60일 데이터 기반이므로 하루 1번 갱신이면 충분 |
| PYTHONPATH=src | 반드시 설정 |
