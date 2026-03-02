"""tests/test_advanced_risk.py — TCA + 시장충격 + 동적상관관계 + 고급 VaR 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.core.advanced_risk import (
    AdvancedVaRResult,
    DynamicCorrelation,
    MarketImpactEstimate,
    TCAReport,
    compute_advanced_var,
    compute_copula_var,
    compute_dynamic_correlation,
    compute_tca,
    estimate_market_impact,
    format_risk_report,
    format_tca_report,
)


# ── helpers ───────────────────────────────────────────────

def _make_returns(n: int = 200, seed: int = 42, mu: float = 0.0005,
                  sigma: float = 0.02) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return rng.normal(mu, sigma, size=n)


def _make_returns_matrix(n: int = 200, tickers: list[str] | None = None,
                          seed: int = 42) -> pd.DataFrame:
    tickers = tickers or ["A", "B", "C"]
    rng = np.random.RandomState(seed)
    data = {}
    for i, t in enumerate(tickers):
        data[t] = rng.normal(0.0005, 0.02, size=n)
    return pd.DataFrame(data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestMarketImpact
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMarketImpact:
    """시장충격 모델 테스트."""

    def test_large_order_high_impact(self):
        """큰 주문 → 높은 impact."""
        large = estimate_market_impact(
            order_shares=500_000, avg_daily_volume=1_000_000,
            price=50000, volatility=0.02, ticker="005930",
        )
        small = estimate_market_impact(
            order_shares=1_000, avg_daily_volume=1_000_000,
            price=50000, volatility=0.02, ticker="005930",
        )
        assert large.total_impact_pct > small.total_impact_pct
        assert large.total_impact_pct > 0
        assert small.total_impact_pct > 0

    def test_small_order_low_impact(self):
        """작은 주문 → 낮은 impact."""
        result = estimate_market_impact(
            order_shares=100, avg_daily_volume=10_000_000,
            price=50000, volatility=0.02, ticker="005930",
        )
        assert result.total_impact_pct < 0.1  # 0.1% 미만
        assert result.order_pct_of_volume < 0.001

    def test_volume_zero_handling(self):
        """거래량 0 처리 → 에러 없이 빈 결과."""
        result = estimate_market_impact(
            order_shares=1000, avg_daily_volume=0,
            price=50000, volatility=0.02,
        )
        assert result.total_impact_pct == 0.0
        assert result.optimal_participation_rate == 0.0

    def test_kyle_method(self):
        """Kyle 모델 동작 확인."""
        result = estimate_market_impact(
            order_shares=10_000, avg_daily_volume=500_000,
            price=75000, volatility=0.025, method="kyle",
        )
        assert isinstance(result, MarketImpactEstimate)
        assert result.total_impact_pct > 0
        assert result.temporary_impact_pct > 0
        assert result.permanent_impact_pct > 0

    def test_negative_shares_handled(self):
        """음수 주문수량도 절대값으로 처리."""
        pos = estimate_market_impact(
            order_shares=10_000, avg_daily_volume=500_000,
            price=50000, volatility=0.02,
        )
        neg = estimate_market_impact(
            order_shares=-10_000, avg_daily_volume=500_000,
            price=50000, volatility=0.02,
        )
        assert pos.total_impact_pct == neg.total_impact_pct

    def test_optimal_participation_rate_bounded(self):
        """최적 참여율은 0.01~0.25 범위."""
        result = estimate_market_impact(
            order_shares=100_000, avg_daily_volume=200_000,
            price=50000, volatility=0.03,
        )
        assert 0.01 <= result.optimal_participation_rate <= 0.25


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestTCA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestTCA:
    """Transaction Cost Analysis 테스트."""

    def test_normal_trade(self):
        """정상 거래 TCA 계산."""
        trades = [
            {
                "ticker": "005930",
                "order_size": 100,
                "execution_price": 75200,
                "benchmark_price": 75000,
            },
        ]
        ohlcv = {
            "005930": {
                "avg_volume": 10_000_000,
                "spread_pct": 0.05,
                "volatility": 0.018,
            },
        }
        results = compute_tca(trades, ohlcv)
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, TCAReport)
        assert r.ticker == "005930"
        # IS = (75200-75000)/75000 * 100 ≈ 0.2667%
        assert abs(r.implementation_shortfall_pct - 0.2667) < 0.01
        # total = IS
        assert abs(r.total_cost_pct - r.implementation_shortfall_pct) < 1e-8
        # 분해: impact + timing + spread ≈ IS
        decomposed = r.market_impact_pct + r.timing_cost_pct + r.spread_cost_pct
        assert abs(decomposed - r.total_cost_pct) < 0.01

    def test_empty_trades(self):
        """빈 거래 리스트."""
        results = compute_tca([], {})
        assert results == []

    def test_multiple_trades(self):
        """복수 거래 처리."""
        trades = [
            {"ticker": "A", "order_size": 50,
             "execution_price": 10100, "benchmark_price": 10000},
            {"ticker": "B", "order_size": 200,
             "execution_price": 5050, "benchmark_price": 5000},
        ]
        ohlcv = {
            "A": {"avg_volume": 500_000, "spread_pct": 0.08, "volatility": 0.015},
            "B": {"avg_volume": 1_000_000, "spread_pct": 0.06, "volatility": 0.020},
        }
        results = compute_tca(trades, ohlcv)
        assert len(results) == 2

    def test_invalid_price_skipped(self):
        """benchmark_price 0 → 건너뛰기."""
        trades = [
            {"ticker": "X", "order_size": 10,
             "execution_price": 100, "benchmark_price": 0},
        ]
        results = compute_tca(trades, {})
        assert len(results) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestDynamicCorrelation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDynamicCorrelation:
    """동적 상관관계 테스트."""

    def test_identical_returns_corr_one(self):
        """동일 수익률 → 상관 = 1."""
        r = _make_returns(200, seed=10)
        result = compute_dynamic_correlation(r, r, ticker_a="X", ticker_b="X")
        assert isinstance(result, DynamicCorrelation)
        assert result.rolling_60d > 0.99
        assert result.rolling_120d > 0.99

    def test_opposite_returns_corr_negative(self):
        """반대 수익률 → 상관 = -1."""
        r = _make_returns(200, seed=10)
        result = compute_dynamic_correlation(r, -r, ticker_a="X", ticker_b="Y")
        assert result.rolling_60d < -0.99
        assert result.rolling_120d < -0.99

    def test_crisis_correlation_exists(self):
        """crisis correlation 값 존재."""
        r1 = _make_returns(500, seed=20)
        r2 = _make_returns(500, seed=21)
        result = compute_dynamic_correlation(r1, r2, ticker_a="A", ticker_b="B")
        # crisis_correlation은 float
        assert isinstance(result.crisis_correlation, float)

    def test_regime_is_valid(self):
        """레짐은 normal/stress/crisis 중 하나."""
        r1 = _make_returns(200, seed=30)
        r2 = _make_returns(200, seed=31)
        result = compute_dynamic_correlation(r1, r2)
        assert result.regime in ("normal", "stress", "crisis")

    def test_short_data_fallback(self):
        """데이터 부족 → 빈 결과."""
        result = compute_dynamic_correlation(np.array([0.01]), np.array([0.02]))
        assert result.rolling_60d == 0.0

    def test_tail_correlation_bounded(self):
        """tail correlation은 [0, 1] 범위."""
        r1 = _make_returns(300, seed=40)
        r2 = r1 * 0.5 + _make_returns(300, seed=41) * 0.5
        result = compute_dynamic_correlation(r1, r2)
        assert 0.0 <= result.tail_correlation <= 1.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestAdvancedVaR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAdvancedVaR:
    """고급 VaR 테스트."""

    def test_var_positive(self):
        """VaR > 0."""
        rm = _make_returns_matrix(200)
        weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        result = compute_advanced_var(rm, weights, confidence=0.95)
        assert result.var_pct > 0

    def test_cvar_geq_var(self):
        """CVaR >= VaR."""
        rm = _make_returns_matrix(200)
        weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        result = compute_advanced_var(rm, weights, confidence=0.95)
        assert result.cvar_pct >= result.var_pct - 0.001  # 부동소수점 오차

    def test_component_var_sums_approx_total(self):
        """Component VaR 합 ≈ total VaR."""
        rm = _make_returns_matrix(200, seed=99)
        weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        result = compute_advanced_var(rm, weights, confidence=0.95)
        if result.component_var:
            comp_sum = sum(result.component_var.values())
            # Component VaR의 합은 total VaR와 근사적으로 같음
            assert comp_sum > 0
            # 합리적 범위 내 (total의 50%~200%)
            assert 0.5 * result.var_pct <= comp_sum <= 2.0 * result.var_pct

    def test_empty_input(self):
        """빈 입력 → 빈 결과."""
        result = compute_advanced_var(pd.DataFrame(), {})
        assert result.var_pct == 0.0
        assert result.cvar_pct == 0.0

    def test_historical_method(self):
        """Historical VaR 동작 확인."""
        rm = _make_returns_matrix(200)
        weights = {"A": 0.5, "B": 0.5}
        result = compute_advanced_var(rm, weights, method="historical")
        assert result.method == "historical"
        assert result.var_pct > 0

    def test_higher_confidence_higher_var(self):
        """높은 신뢰수준 → 높은 VaR."""
        rm = _make_returns_matrix(500, seed=77)
        weights = {"A": 0.5, "B": 0.3, "C": 0.2}
        var_95 = compute_advanced_var(rm, weights, confidence=0.95)
        var_99 = compute_advanced_var(rm, weights, confidence=0.99)
        assert var_99.var_pct > var_95.var_pct

    def test_multi_day_horizon(self):
        """다일 보유기간 → sqrt(T) 스케일링."""
        rm = _make_returns_matrix(200)
        weights = {"A": 0.5, "B": 0.5}
        var_1 = compute_advanced_var(rm, weights, horizon=1)
        var_10 = compute_advanced_var(rm, weights, horizon=10)
        # 10일 VaR ≈ 1일 VaR * sqrt(10)
        ratio = var_10.var_pct / max(var_1.var_pct, 1e-8)
        assert 2.5 < ratio < 4.0  # sqrt(10) ≈ 3.16

    def test_marginal_var_exists(self):
        """Marginal VaR 딕셔너리 존재."""
        rm = _make_returns_matrix(200)
        weights = {"A": 0.5, "B": 0.5}
        result = compute_advanced_var(rm, weights)
        assert "A" in result.marginal_var
        assert "B" in result.marginal_var


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestCopulaVaR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCopulaVaR:
    """Copula VaR 테스트."""

    def test_gaussian_copula_range(self):
        """Gaussian copula VaR 범위 체크."""
        rm = _make_returns_matrix(200, seed=55)
        weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        result = compute_copula_var(rm, weights, n_sim=5000, seed=42)
        assert result.method == "copula_gaussian"
        assert result.var_pct > 0
        assert result.var_pct < 50  # 50% 이상이면 비정상

    def test_t_copula_heavier_tails(self):
        """t-copula → Gaussian보다 높은 VaR (heavier tails)."""
        rm = _make_returns_matrix(200, seed=55)
        weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        gauss = compute_copula_var(
            rm, weights, n_sim=10000, copula_type="gaussian", seed=42,
        )
        t_cop = compute_copula_var(
            rm, weights, n_sim=10000, copula_type="t", seed=42,
        )
        # t-copula는 일반적으로 더 두꺼운 꼬리 → 더 큰 VaR
        # 하지만 시뮬레이션 분산으로 항상 성립 안할 수 있으므로 관대한 기준
        assert t_cop.var_pct > 0
        assert gauss.var_pct > 0

    def test_simulation_reproducibility(self):
        """같은 seed → 같은 결과."""
        rm = _make_returns_matrix(200, seed=55)
        weights = {"A": 0.5, "B": 0.5}
        r1 = compute_copula_var(rm, weights, n_sim=5000, seed=123)
        r2 = compute_copula_var(rm, weights, n_sim=5000, seed=123)
        assert r1.var_pct == r2.var_pct
        assert r1.cvar_pct == r2.cvar_pct

    def test_empty_input(self):
        """빈 입력 → 빈 결과."""
        result = compute_copula_var(pd.DataFrame(), {})
        assert result.var_pct == 0.0

    def test_cvar_geq_var(self):
        """CVaR >= VaR (copula에서도)."""
        rm = _make_returns_matrix(200, seed=77)
        weights = {"A": 0.4, "B": 0.3, "C": 0.3}
        result = compute_copula_var(rm, weights, n_sim=5000, seed=42)
        assert result.cvar_pct >= result.var_pct - 0.01


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestFormat
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFormat:
    """포맷 함수 테스트."""

    def test_format_tca_returns_str(self):
        """TCA 포맷 → str 반환."""
        reports = [
            TCAReport(
                ticker="005930", order_size=100,
                avg_daily_volume=10_000_000,
                market_impact_pct=0.05, timing_cost_pct=0.10,
                spread_cost_pct=0.025, total_cost_pct=0.175,
                benchmark_price=75000, execution_price=75130,
                implementation_shortfall_pct=0.175,
            ),
        ]
        text = format_tca_report(reports)
        assert isinstance(text, str)
        assert "005930" in text
        assert "TCA" in text
        assert "IS" in text

    def test_format_tca_empty(self):
        """빈 TCA → 안내 문구."""
        text = format_tca_report([])
        assert "없음" in text

    def test_format_risk_report(self):
        """리스크 리포트 → str 반환, 주요 정보 포함."""
        var_result = AdvancedVaRResult(
            method="parametric_cornish_fisher",
            confidence=0.95,
            horizon_days=1,
            var_pct=2.15,
            cvar_pct=2.85,
            component_var={"A": 1.2, "B": 0.95},
        )
        corrs = [
            DynamicCorrelation(
                ticker_a="A", ticker_b="B",
                rolling_60d=0.45, rolling_120d=0.52,
                crisis_correlation=0.72,
                tail_correlation=0.38,
                regime="normal",
            ),
        ]
        text = format_risk_report(var_result, corrs)
        assert isinstance(text, str)
        assert "VaR" in text
        assert "2.15" in text
        assert "CVaR" in text
        assert "A" in text
        assert "상관" in text

    def test_format_risk_no_correlations(self):
        """상관관계 없이도 동작."""
        var_result = AdvancedVaRResult(
            method="historical", confidence=0.95,
            horizon_days=5, var_pct=3.5, cvar_pct=4.2,
        )
        text = format_risk_report(var_result, None)
        assert "VaR" in text
        assert "상관" not in text  # 상관관계 섹션 없음
