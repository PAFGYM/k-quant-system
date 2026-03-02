"""Tests for kstock.signal.options_analytics module.

Covers: Black-Scholes pricing, Greeks computation, implied volatility,
IV analysis, option chain analysis, volatility surface, strategy analysis,
and format functions.
"""

from __future__ import annotations

import math

import pytest

from kstock.signal.options_analytics import (
    OptionGreeks,
    OptionPrice,
    ImpliedVolatility,
    OptionChainAnalysis,
    VolatilitySurface,
    OptionStrategy,
    black_scholes,
    compute_greeks,
    implied_volatility,
    analyze_iv,
    analyze_option_chain,
    build_volatility_surface,
    analyze_strategy,
    format_greeks,
    format_option_analysis,
)


# ---------------------------------------------------------------------------
# TestBlackScholes
# ---------------------------------------------------------------------------
class TestBlackScholes:
    """Black-Scholes 가격 산정 테스트."""

    def test_call_put_parity(self):
        """Call-Put parity: C - P = S - K*e^(-rT)."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        call = black_scholes(S, K, T, r, sigma, "call")
        put = black_scholes(S, K, T, r, sigma, "put")
        lhs = call.theoretical_price - put.theoretical_price
        rhs = S - K * math.exp(-r * T)
        assert abs(lhs - rhs) < 0.0001

    def test_atm_call_approx_put(self):
        """ATM에서 r=0이면 call 가격 ~ put 가격."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.0, 0.2
        call = black_scholes(S, K, T, r, sigma, "call")
        put = black_scholes(S, K, T, r, sigma, "put")
        assert abs(call.theoretical_price - put.theoretical_price) < 0.01

    def test_deep_itm_call(self):
        """깊은 ITM 콜: 내재가치에 수렴."""
        S, K, T, r, sigma = 200, 100, 0.25, 0.03, 0.2
        result = black_scholes(S, K, T, r, sigma, "call")
        assert result.theoretical_price > 99.0
        assert result.moneyness == "ITM"
        assert result.intrinsic_value == pytest.approx(100.0, abs=0.01)

    def test_deep_otm_call(self):
        """깊은 OTM 콜: 가격 0에 수렴."""
        S, K, T, r, sigma = 50, 200, 0.1, 0.03, 0.2
        result = black_scholes(S, K, T, r, sigma, "call")
        assert result.theoretical_price < 0.01
        assert result.moneyness == "OTM"

    def test_put_price_positive(self):
        """풋 옵션 가격 양수."""
        result = black_scholes(100, 100, 0.5, 0.03, 0.25, "put")
        assert result.theoretical_price > 0

    def test_moneyness_atm(self):
        """ATM 판별."""
        result = black_scholes(100, 100.5, 0.25, 0.03, 0.2, "call")
        assert result.moneyness == "ATM"

    def test_time_value_nonnegative(self):
        """시간가치 >= 0."""
        result = black_scholes(100, 100, 0.5, 0.03, 0.2, "call")
        assert result.time_value >= 0

    def test_invalid_inputs(self):
        """잘못된 입력 검증."""
        with pytest.raises(ValueError):
            black_scholes(-100, 100, 0.25, 0.03, 0.2)
        with pytest.raises(ValueError):
            black_scholes(100, 100, -0.25, 0.03, 0.2)
        with pytest.raises(ValueError):
            black_scholes(100, 100, 0.25, 0.03, -0.2)

    def test_invalid_option_type(self):
        with pytest.raises(ValueError, match="option_type"):
            black_scholes(100, 100, 0.25, 0.03, 0.2, "straddle")

    def test_returns_option_price(self):
        """반환 타입 확인."""
        result = black_scholes(100, 100, 0.25, 0.03, 0.2)
        assert isinstance(result, OptionPrice)


# ---------------------------------------------------------------------------
# TestGreeks
# ---------------------------------------------------------------------------
class TestGreeks:
    """Greeks 계산 테스트."""

    def test_call_delta_range(self):
        """콜 delta는 0 ~ 1."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2, "call")
        assert 0 <= g.delta <= 1

    def test_put_delta_range(self):
        """풋 delta는 -1 ~ 0."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2, "put")
        assert -1 <= g.delta <= 0

    def test_atm_delta_near_half(self):
        """ATM에서 콜 delta ~ 0.5."""
        g = compute_greeks(100, 100, 0.25, 0.0, 0.2, "call")
        assert abs(g.delta - 0.5) < 0.1

    def test_gamma_positive(self):
        """Gamma > 0 (call/put 동일)."""
        gc = compute_greeks(100, 100, 0.25, 0.03, 0.2, "call")
        gp = compute_greeks(100, 100, 0.25, 0.03, 0.2, "put")
        assert gc.gamma > 0
        assert gp.gamma > 0
        assert abs(gc.gamma - gp.gamma) < 1e-10

    def test_vega_positive(self):
        """Vega > 0."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2, "call")
        assert g.vega > 0

    def test_call_theta_negative(self):
        """콜 Theta < 0 (시간가치 감소)."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2, "call")
        assert g.theta < 0

    def test_deep_itm_call_delta_near_one(self):
        """깊은 ITM 콜 delta ~ 1."""
        g = compute_greeks(200, 100, 0.5, 0.03, 0.2, "call")
        assert g.delta > 0.95

    def test_second_order_greeks_exist(self):
        """2차 Greeks 값 존재."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2, "call")
        assert isinstance(g.charm, float)
        assert isinstance(g.vanna, float)
        assert isinstance(g.volga, float)

    def test_returns_option_greeks(self):
        """반환 타입 확인."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2)
        assert isinstance(g, OptionGreeks)


# ---------------------------------------------------------------------------
# TestImpliedVol
# ---------------------------------------------------------------------------
class TestImpliedVol:
    """내재변동성 역추산 테스트."""

    @pytest.mark.parametrize("sigma_true", [0.1, 0.2, 0.3, 0.5])
    def test_roundtrip(self, sigma_true):
        """BS로 가격 계산 -> IV 역추산 -> 원래 sigma 일치."""
        S, K, T, r = 100, 100, 0.25, 0.03
        bs = black_scholes(S, K, T, r, sigma_true, "call")
        iv = implied_volatility(bs.theoretical_price, S, K, T, r, "call")
        assert abs(iv - sigma_true) < 0.001

    def test_put_roundtrip(self):
        """풋 옵션 IV 역추산."""
        S, K, T, r, sigma_true = 100, 110, 0.5, 0.03, 0.25
        bs = black_scholes(S, K, T, r, sigma_true, "put")
        iv = implied_volatility(bs.theoretical_price, S, K, T, r, "put")
        assert abs(iv - sigma_true) < 0.001

    def test_otm_roundtrip(self):
        """OTM 옵션 IV 역추산."""
        S, K, T, r, sigma_true = 100, 130, 0.25, 0.03, 0.3
        bs = black_scholes(S, K, T, r, sigma_true, "call")
        iv = implied_volatility(bs.theoretical_price, S, K, T, r, "call")
        assert abs(iv - sigma_true) < 0.01

    def test_invalid_price(self):
        """음수 시장 가격."""
        with pytest.raises(ValueError):
            implied_volatility(-1.0, 100, 100, 0.25, 0.03)

    def test_returns_float(self):
        """반환 타입 확인."""
        bs = black_scholes(100, 100, 0.25, 0.03, 0.2)
        iv = implied_volatility(bs.theoretical_price, 100, 100, 0.25, 0.03)
        assert isinstance(iv, float)


# ---------------------------------------------------------------------------
# TestIVAnalysis
# ---------------------------------------------------------------------------
class TestIVAnalysis:
    """IV 분석 테스트."""

    def test_percentile_range(self):
        """IV percentile 0 ~ 1."""
        history = [0.15, 0.18, 0.20, 0.22, 0.25, 0.30]
        result = analyze_iv(history, 0.22)
        assert 0 <= result.iv_percentile_1y <= 1

    def test_rank_range(self):
        """IV rank 0 ~ 1."""
        history = [0.15, 0.18, 0.20, 0.22, 0.25, 0.30]
        result = analyze_iv(history, 0.22)
        assert 0 <= result.iv_rank_1y <= 1

    def test_rank_at_min(self):
        """최소값에서 rank = 0."""
        history = [0.10, 0.15, 0.20, 0.25, 0.30]
        result = analyze_iv(history, 0.10)
        assert result.iv_rank_1y == pytest.approx(0.0)

    def test_rank_at_max(self):
        """최대값에서 rank = 1."""
        history = [0.10, 0.15, 0.20, 0.25, 0.30]
        result = analyze_iv(history, 0.30)
        assert result.iv_rank_1y == pytest.approx(1.0)

    def test_percentile_high_iv(self):
        """높은 IV -> 높은 percentile."""
        history = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
        result = analyze_iv(history, 0.25)
        assert result.iv_percentile_1y == 1.0

    def test_skew_calculation(self):
        """25-delta skew 계산."""
        result = analyze_iv([0.2], 0.2, put_25d_iv=0.28, call_25d_iv=0.22)
        assert result.skew_25d == pytest.approx(0.06, abs=0.001)

    def test_empty_history(self):
        """빈 히스토리."""
        result = analyze_iv([], 0.2)
        assert result.iv == 0.2
        assert isinstance(result, ImpliedVolatility)

    def test_returns_implied_volatility(self):
        """반환 타입 확인."""
        result = analyze_iv([0.2, 0.25], 0.22)
        assert isinstance(result, ImpliedVolatility)


# ---------------------------------------------------------------------------
# TestOptionChain
# ---------------------------------------------------------------------------
class TestOptionChain:
    """옵션 체인 분석 테스트."""

    @pytest.fixture
    def sample_chain(self):
        return [
            {"strike": 95, "type": "call", "oi": 1000, "volume": 50, "iv": 0.22},
            {"strike": 100, "type": "call", "oi": 2000, "volume": 100, "iv": 0.20},
            {"strike": 105, "type": "call", "oi": 1500, "volume": 80, "iv": 0.21},
            {"strike": 95, "type": "put", "oi": 500, "volume": 30, "iv": 0.25},
            {"strike": 100, "type": "put", "oi": 3000, "volume": 150, "iv": 0.23},
            {"strike": 105, "type": "put", "oi": 4000, "volume": 200, "iv": 0.26},
        ]

    def test_pc_ratio_bearish(self, sample_chain):
        """P/C ratio > 1 -> bearish."""
        result = analyze_option_chain(sample_chain)
        # puts: 500+3000+4000=7500, calls: 1000+2000+1500=4500
        assert result.put_call_ratio > 1.0
        assert result.dominant_sentiment == "bearish"

    def test_pc_ratio_bullish(self):
        """P/C ratio < 0.8 -> bullish."""
        chain = [
            {"strike": 100, "type": "call", "oi": 5000, "volume": 100},
            {"strike": 100, "type": "put", "oi": 1000, "volume": 50},
        ]
        result = analyze_option_chain(chain)
        assert result.put_call_ratio < 0.8
        assert result.dominant_sentiment == "bullish"

    def test_max_pain_calculation(self, sample_chain):
        """Max Pain 행사가 존재."""
        result = analyze_option_chain(sample_chain)
        assert result.max_pain_strike in [95, 100, 105]

    def test_total_oi(self, sample_chain):
        """총 미결제약정 합산."""
        result = analyze_option_chain(sample_chain)
        assert result.total_oi_calls == 4500
        assert result.total_oi_puts == 7500

    def test_unusual_activity_detection(self):
        """이상 거래 감지 (volume > 3x avg)."""
        chain = [
            {"strike": 100, "type": "call", "oi": 1000, "volume": 10},
            {"strike": 105, "type": "call", "oi": 1000, "volume": 10},
            {"strike": 108, "type": "call", "oi": 1000, "volume": 10},
            {"strike": 110, "type": "call", "oi": 1000, "volume": 10},
            {"strike": 115, "type": "call", "oi": 1000, "volume": 1000},
        ]
        # avg = (10+10+10+10+1000)/5 = 208, 3x avg = 624, 1000 > 624
        result = analyze_option_chain(chain)
        assert len(result.unusual_activity) >= 1
        assert result.unusual_activity[0]["strike"] == 115

    def test_empty_chain(self):
        """빈 체인."""
        result = analyze_option_chain([])
        assert result.put_call_ratio == 0.0
        assert isinstance(result, OptionChainAnalysis)

    def test_returns_chain_analysis(self, sample_chain):
        """반환 타입 확인."""
        result = analyze_option_chain(sample_chain)
        assert isinstance(result, OptionChainAnalysis)


# ---------------------------------------------------------------------------
# TestVolSurface
# ---------------------------------------------------------------------------
class TestVolSurface:
    """변동성 표면 테스트."""

    @pytest.fixture
    def sample_surface_data(self):
        """샘플 체인 데이터 (만기별)."""
        S, r, sigma = 100.0, 0.03, 0.2
        data = {}
        for exp_days in [30, 60, 90]:
            T = exp_days / 365.0
            items = []
            for K in [90, 95, 100, 105, 110]:
                bs = black_scholes(S, K, T, r, sigma + (K - 100) * 0.001)
                items.append({
                    "strike": K,
                    "type": "call",
                    "price": bs.theoretical_price,
                })
            data[exp_days] = items
        return data, S, r

    def test_valid_matrix(self, sample_surface_data):
        """유효한 IV 행렬."""
        data, S, r = sample_surface_data
        result = build_volatility_surface(data, S, r)
        assert len(result.iv_matrix) == 3  # 3 expirations
        assert len(result.iv_matrix[0]) == 5  # 5 strikes
        for row in result.iv_matrix:
            for iv in row:
                assert iv >= 0

    def test_atm_vol_exists(self, sample_surface_data):
        """ATM 변동성 존재."""
        data, S, r = sample_surface_data
        result = build_volatility_surface(data, S, r)
        assert result.atm_vol > 0

    def test_expirations_sorted(self, sample_surface_data):
        """만기 정렬."""
        data, S, r = sample_surface_data
        result = build_volatility_surface(data, S, r)
        assert result.expirations == [30, 60, 90]

    def test_empty_data(self):
        """빈 데이터."""
        result = build_volatility_surface({}, 100, 0.03)
        assert isinstance(result, VolatilitySurface)
        assert result.atm_vol == 0.0

    def test_returns_volatility_surface(self, sample_surface_data):
        """반환 타입 확인."""
        data, S, r = sample_surface_data
        result = build_volatility_surface(data, S, r)
        assert isinstance(result, VolatilitySurface)


# ---------------------------------------------------------------------------
# TestStrategy
# ---------------------------------------------------------------------------
class TestStrategy:
    """옵션 전략 분석 테스트."""

    def test_covered_call_max_profit(self):
        """Covered call: 유한한 최대 수익."""
        legs = [{"type": "call", "strike": 110, "expiry_days": 30,
                 "action": "sell", "qty": 1}]
        result = analyze_strategy(legs, S=100, r=0.03, sigma=0.2)
        assert result.name == "covered_call"
        assert result.max_profit < float("inf")

    def test_straddle_two_breakevens(self):
        """Straddle: 2개 손익분기점."""
        legs = [
            {"type": "call", "strike": 100, "expiry_days": 30,
             "action": "buy", "qty": 1},
            {"type": "put", "strike": 100, "expiry_days": 30,
             "action": "buy", "qty": 1},
        ]
        result = analyze_strategy(legs, S=100, r=0.03, sigma=0.2)
        assert result.name == "straddle"
        assert len(result.breakeven) == 2

    def test_straddle_breakevens_symmetric(self):
        """Straddle: 손익분기점이 행사가 양쪽에 대칭."""
        legs = [
            {"type": "call", "strike": 100, "expiry_days": 30,
             "action": "buy", "qty": 1},
            {"type": "put", "strike": 100, "expiry_days": 30,
             "action": "buy", "qty": 1},
        ]
        result = analyze_strategy(legs, S=100, r=0.0, sigma=0.2)
        if len(result.breakeven) == 2:
            lower, upper = result.breakeven
            assert lower < 100 < upper

    def test_strategy_net_greeks(self):
        """전략 순 Greeks 합산."""
        legs = [
            {"type": "call", "strike": 100, "expiry_days": 30,
             "action": "buy", "qty": 1},
            {"type": "put", "strike": 100, "expiry_days": 30,
             "action": "buy", "qty": 1},
        ]
        result = analyze_strategy(legs, S=100, r=0.03, sigma=0.2)
        # Straddle: call delta + put delta ≈ 0
        assert abs(result.greeks_net.delta) < 0.2

    def test_butterfly_identification(self):
        """Butterfly 전략 인식."""
        legs = [
            {"type": "call", "strike": 95, "expiry_days": 30,
             "action": "buy", "qty": 1},
            {"type": "call", "strike": 100, "expiry_days": 30,
             "action": "sell", "qty": 2},
            {"type": "call", "strike": 105, "expiry_days": 30,
             "action": "buy", "qty": 1},
        ]
        result = analyze_strategy(legs, S=100, r=0.03, sigma=0.2)
        assert result.name == "butterfly"

    def test_iron_condor_identification(self):
        """Iron condor 전략 인식."""
        legs = [
            {"type": "put", "strike": 90, "expiry_days": 30,
             "action": "buy", "qty": 1},
            {"type": "put", "strike": 95, "expiry_days": 30,
             "action": "sell", "qty": 1},
            {"type": "call", "strike": 105, "expiry_days": 30,
             "action": "sell", "qty": 1},
            {"type": "call", "strike": 110, "expiry_days": 30,
             "action": "buy", "qty": 1},
        ]
        result = analyze_strategy(legs, S=100, r=0.03, sigma=0.2)
        assert result.name == "iron_condor"

    def test_empty_legs(self):
        """빈 다리."""
        result = analyze_strategy([], S=100, r=0.03, sigma=0.2)
        assert result.name == ""
        assert isinstance(result, OptionStrategy)

    def test_returns_option_strategy(self):
        """반환 타입 확인."""
        legs = [{"type": "call", "strike": 100, "expiry_days": 30,
                 "action": "buy", "qty": 1}]
        result = analyze_strategy(legs, S=100, r=0.03, sigma=0.2)
        assert isinstance(result, OptionStrategy)


# ---------------------------------------------------------------------------
# TestFormat
# ---------------------------------------------------------------------------
class TestFormat:
    """포맷 함수 테스트."""

    def test_format_greeks_returns_str(self):
        """format_greeks 반환 타입."""
        g = compute_greeks(100, 100, 0.25, 0.03, 0.2)
        result = format_greeks(g, "005930")
        assert isinstance(result, str)
        assert "Delta" in result
        assert "005930" in result

    def test_format_greeks_without_ticker(self):
        """ticker 없이 호출."""
        g = OptionGreeks(delta=0.5, gamma=0.03, vega=0.15)
        result = format_greeks(g)
        assert isinstance(result, str)
        assert "Greeks" in result

    def test_format_option_analysis_returns_str(self):
        """format_option_analysis 반환 타입."""
        chain = OptionChainAnalysis(
            put_call_ratio=1.5,
            max_pain_strike=100,
            total_oi_calls=5000,
            total_oi_puts=7500,
            dominant_sentiment="bearish",
        )
        iv = ImpliedVolatility(iv=0.25, iv_percentile_1y=0.75, iv_rank_1y=0.6)
        result = format_option_analysis(chain, iv)
        assert isinstance(result, str)
        assert "P/C Ratio" in result
        assert "bearish" in result

    def test_format_with_unusual_activity(self):
        """이상거래 포함 포맷."""
        chain = OptionChainAnalysis(
            put_call_ratio=0.8,
            max_pain_strike=100,
            total_oi_calls=5000,
            total_oi_puts=4000,
            dominant_sentiment="neutral",
            unusual_activity=[
                {"type": "call", "strike": 110, "volume": 5000,
                 "oi": 1000, "volume_ratio": 5.0},
            ],
        )
        iv = ImpliedVolatility(iv=0.2)
        result = format_option_analysis(chain, iv)
        assert "이상거래" in result
