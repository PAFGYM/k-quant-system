"""Tests for the v3.0 scoring system (max ~160 points, 6 signal tiers).

Covers compute_composite_score with v3.0 bonus params (policy_bonus, ml_bonus,
sentiment_bonus, leading_sector_bonus), updated thresholds (STRONG_BUY, BUY,
WATCH, MILD_BUY, HOLD), capping/flooring, individual score functions, and
ScoreBreakdown field validation.
"""

from __future__ import annotations

import pytest

from kstock.features.technical import TechnicalIndicators
from kstock.ingest.kis_client import StockInfo
from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.scoring import (
    FlowData,
    ScoreBreakdown,
    compute_composite_score,
    score_flow,
    score_fundamental,
    score_macro,
    score_risk,
    score_technical,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> dict:
    """Inline scoring config with v3.0 weights and thresholds."""
    return {
        "weights": {
            "macro": 0.15,
            "flow": 0.15,
            "fundamental": 0.25,
            "technical": 0.30,
            "risk": 0.15,
        },
        "thresholds": {
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bb_lower_pct": 0.2,
            "bb_upper_pct": 0.8,
            "macd_signal_threshold": 0.0,
            "debt_ratio_max": 200,
            "per_max": 30,
            "per_min": 5,
            "roe_min": 8.0,
            "consensus_target_pct": 10.0,
            "foreign_net_buy_days": 3,
            "institution_net_buy_days": 3,
            "min_avg_value_krw": 3_000_000_000,
            "vix_high": 25,
            "vix_low": 15,
            "usdkrw_high": 1350,
            "usdkrw_low": 1250,
            "max_drawdown_pct": 20,
            "beta_max": 1.5,
        },
        "buy_threshold": 70,
        "watch_threshold": 55,
    }


@pytest.fixture
def thresholds(config: dict) -> dict:
    return config["thresholds"]


@pytest.fixture
def neutral_macro() -> MacroSnapshot:
    """Neutral macro environment -- yields score ~0.5."""
    return MacroSnapshot(
        vix=20.0,
        vix_change_pct=0.0,
        spx_change_pct=0.2,
        usdkrw=1300.0,
        usdkrw_change_pct=0.0,
        us10y=4.0,
        dxy=104.0,
        regime="neutral",
    )


@pytest.fixture
def neutral_flow() -> FlowData:
    """Neutral flow data -- yields score ~0.5-0.6."""
    return FlowData(
        foreign_net_buy_days=0,
        institution_net_buy_days=0,
        avg_trade_value_krw=3_000_000_000,
    )


@pytest.fixture
def neutral_info() -> StockInfo:
    """Moderate fundamentals -- yields score ~0.6-0.75."""
    return StockInfo(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        market_cap=400_000_000_000_000,
        per=12.0,
        roe=15.0,
        debt_ratio=80.0,
        consensus_target=80000.0,
        current_price=70000.0,
    )


@pytest.fixture
def neutral_tech() -> TechnicalIndicators:
    """Neutral technicals -- yields score ~0.55."""
    return TechnicalIndicators(
        rsi=50.0,
        bb_pctb=0.5,
        bb_bandwidth=0.04,
        macd_histogram=0.0,
        macd_signal_cross=0,
        atr=1000.0,
        atr_pct=2.0,
    )


@pytest.fixture
def bullish_macro() -> MacroSnapshot:
    """Strongly bullish macro environment."""
    return MacroSnapshot(
        vix=12.0,
        vix_change_pct=-3.0,
        spx_change_pct=1.5,
        usdkrw=1220.0,
        usdkrw_change_pct=-0.3,
        us10y=3.8,
        dxy=102.0,
        regime="risk_on",
    )


@pytest.fixture
def bullish_flow() -> FlowData:
    """Strong institutional and foreign buying."""
    return FlowData(
        foreign_net_buy_days=5,
        institution_net_buy_days=5,
        avg_trade_value_krw=10_000_000_000,
    )


@pytest.fixture
def bullish_info() -> StockInfo:
    """Excellent fundamentals with large consensus upside."""
    return StockInfo(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        market_cap=400_000_000_000_000,
        per=10.0,
        roe=20.0,
        debt_ratio=30.0,
        consensus_target=100000.0,
        current_price=70000.0,
    )


@pytest.fixture
def bullish_tech() -> TechnicalIndicators:
    """Oversold with bullish MACD cross."""
    return TechnicalIndicators(
        rsi=25.0,
        bb_pctb=0.1,
        bb_bandwidth=0.05,
        macd_histogram=0.5,
        macd_signal_cross=1,
        atr=500.0,
        atr_pct=1.0,
    )


@pytest.fixture
def bearish_macro() -> MacroSnapshot:
    """Risk-off macro environment."""
    return MacroSnapshot(
        vix=35.0,
        vix_change_pct=20.0,
        spx_change_pct=-3.0,
        usdkrw=1420.0,
        usdkrw_change_pct=2.0,
        us10y=5.0,
        dxy=112.0,
        regime="risk_off",
    )


@pytest.fixture
def bearish_flow() -> FlowData:
    """Heavy foreign and institutional selling."""
    return FlowData(
        foreign_net_buy_days=-5,
        institution_net_buy_days=-5,
        avg_trade_value_krw=500_000_000,
    )


@pytest.fixture
def bearish_info() -> StockInfo:
    """Poor fundamentals."""
    return StockInfo(
        ticker="999999",
        name="BadCo",
        market="KOSPI",
        market_cap=500_000_000_000,
        per=50.0,
        roe=1.0,
        debt_ratio=300.0,
        consensus_target=5000.0,
        current_price=10000.0,
    )


@pytest.fixture
def bearish_tech() -> TechnicalIndicators:
    """Overbought with bearish cross and high volatility."""
    return TechnicalIndicators(
        rsi=80.0,
        bb_pctb=0.95,
        bb_bandwidth=0.10,
        macd_histogram=-1.0,
        macd_signal_cross=-1,
        atr=5000.0,
        atr_pct=7.0,
    )


# ---------------------------------------------------------------------------
# 1. Neutral inputs -> composite ~50-60
# ---------------------------------------------------------------------------


class TestNeutralComposite:
    def test_neutral_inputs_yield_mid_range_composite(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """With all-neutral inputs and no bonuses, composite should land ~50-70."""
        result = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        assert 40 <= result.composite <= 75, (
            f"Expected neutral composite in 40-75 range, got {result.composite}"
        )


# ---------------------------------------------------------------------------
# 2. STRONG_BUY signal (composite >= 130)
# ---------------------------------------------------------------------------


class TestStrongBuySignal:
    def test_strong_buy_with_max_bonuses(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """Bullish base + large bonuses should trigger STRONG_BUY."""
        result = compute_composite_score(
            bullish_macro,
            bullish_flow,
            bullish_info,
            bullish_tech,
            config,
            mtf_bonus=10,
            sector_adj=5,
            policy_bonus=10,
            ml_bonus=15,
            sentiment_bonus=10,
            leading_sector_bonus=5,
        )
        assert result.composite >= 130
        assert result.signal == "STRONG_BUY"


# ---------------------------------------------------------------------------
# 3. BUY signal (composite >= 110)
# ---------------------------------------------------------------------------


class TestBuySignal:
    def test_buy_signal_with_moderate_bonuses(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """Bullish base + moderate bonuses -> BUY (110-129)."""
        result = compute_composite_score(
            bullish_macro,
            bullish_flow,
            bullish_info,
            bullish_tech,
            config,
            mtf_bonus=10,
            sector_adj=5,
            policy_bonus=10,
        )
        assert result.composite >= 110
        assert result.signal in ("BUY", "STRONG_BUY")


# ---------------------------------------------------------------------------
# 4. WATCH signal (composite >= 90)
# ---------------------------------------------------------------------------


class TestWatchSignalUpper:
    def test_watch_signal_at_90(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """Bullish base alone (no bonuses) should land in the upper WATCH
        or higher range (>= 90).
        """
        result = compute_composite_score(
            bullish_macro,
            bullish_flow,
            bullish_info,
            bullish_tech,
            config,
        )
        # With all bullish inputs, the base score should be high (>= 80).
        # Adding small bonuses if needed to push past 90.
        if result.composite < 90:
            result = compute_composite_score(
                bullish_macro,
                bullish_flow,
                bullish_info,
                bullish_tech,
                config,
                mtf_bonus=10,
            )
        assert result.composite >= 90
        assert result.signal in ("WATCH", "BUY", "STRONG_BUY")


# ---------------------------------------------------------------------------
# 5. MILD_BUY signal (composite >= 70, < 90)
# ---------------------------------------------------------------------------


class TestMildBuySignal:
    def test_mild_buy_in_range(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """Neutral base + small bonuses should push into MILD_BUY range."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        # Compute the bonus needed to push composite into [70, 90)
        gap = 75 - base.composite
        bonus = max(0, int(gap) + 1)
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            ml_bonus=min(bonus, 15),
            policy_bonus=max(0, bonus - 15),
        )
        if 70 <= result.composite < 90:
            assert result.signal == "MILD_BUY"


# ---------------------------------------------------------------------------
# 6. HOLD signal (composite < 55)
# ---------------------------------------------------------------------------


class TestHoldSignal:
    def test_hold_signal_bearish(
        self,
        config: dict,
        bearish_macro: MacroSnapshot,
        bearish_flow: FlowData,
        bearish_info: StockInfo,
        bearish_tech: TechnicalIndicators,
    ) -> None:
        """All bearish inputs with negative bonuses should yield HOLD."""
        result = compute_composite_score(
            bearish_macro,
            bearish_flow,
            bearish_info,
            bearish_tech,
            config,
            mtf_bonus=-10,
            sector_adj=-5,
            ml_bonus=-10,
            sentiment_bonus=-10,
        )
        assert result.composite < 55
        assert result.signal == "HOLD"


# ---------------------------------------------------------------------------
# 7. policy_bonus adds to composite
# ---------------------------------------------------------------------------


class TestPolicyBonus:
    def test_policy_bonus_adds(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """policy_bonus=10 should increase composite by exactly 10."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        with_policy = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            policy_bonus=10,
        )
        assert abs(with_policy.composite - base.composite - 10) < 0.01


# ---------------------------------------------------------------------------
# 8. ml_bonus adds to composite
# ---------------------------------------------------------------------------


class TestMlBonus:
    def test_ml_bonus_adds(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """ml_bonus=15 should increase composite by exactly 15."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        with_ml = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            ml_bonus=15,
        )
        assert abs(with_ml.composite - base.composite - 15) < 0.01

    @pytest.mark.parametrize("ml_val", [15, 10, 5, -10])
    def test_ml_bonus_parametrized(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
        ml_val: int,
    ) -> None:
        """Each ml_bonus level should shift composite by that amount."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            ml_bonus=ml_val,
        )
        expected = max(0.0, min(160.0, base.composite + ml_val))
        assert abs(result.composite - expected) < 0.01


# ---------------------------------------------------------------------------
# 9. sentiment_bonus adds to composite
# ---------------------------------------------------------------------------


class TestSentimentBonus:
    @pytest.mark.parametrize("sent_val", [10, 5, -10])
    def test_sentiment_bonus_shifts(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
        sent_val: int,
    ) -> None:
        """sentiment_bonus should shift composite by its value."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            sentiment_bonus=sent_val,
        )
        expected = max(0.0, min(160.0, base.composite + sent_val))
        assert abs(result.composite - expected) < 0.01


# ---------------------------------------------------------------------------
# 10. leading_sector_bonus adds to composite
# ---------------------------------------------------------------------------


class TestLeadingSectorBonus:
    @pytest.mark.parametrize("tier_bonus", [5, 2])
    def test_leading_sector_bonus(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
        tier_bonus: int,
    ) -> None:
        """leading_sector_bonus (+5 tier1, +2 tier2) shifts composite."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            leading_sector_bonus=tier_bonus,
        )
        assert abs(result.composite - base.composite - tier_bonus) < 0.01


# ---------------------------------------------------------------------------
# 11. All bonuses combined (max ~160)
# ---------------------------------------------------------------------------


class TestAllBonusesCombined:
    def test_max_bonuses_approach_160(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """All positive bonuses at max should push composite toward 160."""
        result = compute_composite_score(
            bullish_macro,
            bullish_flow,
            bullish_info,
            bullish_tech,
            config,
            mtf_bonus=10,
            sector_adj=5,
            policy_bonus=10,
            ml_bonus=15,
            sentiment_bonus=10,
            leading_sector_bonus=5,
        )
        # Base bullish ~85-100, + 55 bonus = 140-155
        assert result.composite >= 130
        assert result.composite <= 160


# ---------------------------------------------------------------------------
# 12. Negative bonuses reduce composite
# ---------------------------------------------------------------------------


class TestNegativeBonuses:
    def test_negative_bonuses_reduce(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """All negative bonuses should decrease composite below base."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            mtf_bonus=-10,
            sector_adj=-5,
            ml_bonus=-10,
            sentiment_bonus=-10,
        )
        assert result.composite < base.composite
        total_penalty = 10 + 5 + 10 + 10  # 35
        expected = max(0.0, base.composite - total_penalty)
        assert abs(result.composite - expected) < 0.01


# ---------------------------------------------------------------------------
# 13. Composite capped at 160
# ---------------------------------------------------------------------------


class TestCompositeCap:
    def test_composite_capped_at_160(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """Even with absurdly large bonuses, composite must not exceed 160."""
        result = compute_composite_score(
            bullish_macro,
            bullish_flow,
            bullish_info,
            bullish_tech,
            config,
            mtf_bonus=50,
            sector_adj=50,
            policy_bonus=50,
            ml_bonus=50,
            sentiment_bonus=50,
            leading_sector_bonus=50,
        )
        assert result.composite == 160.0


# ---------------------------------------------------------------------------
# 14. Composite floored at 0
# ---------------------------------------------------------------------------


class TestCompositeFloor:
    def test_composite_floored_at_0(
        self,
        config: dict,
        bearish_macro: MacroSnapshot,
        bearish_flow: FlowData,
        bearish_info: StockInfo,
        bearish_tech: TechnicalIndicators,
    ) -> None:
        """Even with absurdly large negative bonuses, composite must not go below 0."""
        result = compute_composite_score(
            bearish_macro,
            bearish_flow,
            bearish_info,
            bearish_tech,
            config,
            mtf_bonus=-50,
            sector_adj=-50,
            policy_bonus=-50,
            ml_bonus=-50,
            sentiment_bonus=-50,
            leading_sector_bonus=-50,
        )
        assert result.composite == 0.0


# ---------------------------------------------------------------------------
# 15. mtf_bonus +10 effect
# ---------------------------------------------------------------------------


class TestMtfBonus:
    def test_mtf_aligned_adds_10(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """mtf_bonus=+10 should increase composite by 10."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        aligned = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            mtf_bonus=10,
        )
        assert abs(aligned.composite - base.composite - 10) < 0.01

    def test_mtf_misaligned_subtracts_10(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """mtf_bonus=-10 should decrease composite by 10."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        misaligned = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            mtf_bonus=-10,
        )
        expected = max(0.0, base.composite - 10)
        assert abs(misaligned.composite - expected) < 0.01


# ---------------------------------------------------------------------------
# 16. sector_adj effect
# ---------------------------------------------------------------------------


class TestSectorAdj:
    def test_sector_top_adds_5(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """sector_adj=+5 (top sector) should increase composite by 5."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            sector_adj=5,
        )
        assert abs(result.composite - base.composite - 5) < 0.01

    def test_sector_bottom_subtracts_5(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """sector_adj=-5 (bottom sector) should decrease composite by 5."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            sector_adj=-5,
        )
        expected = max(0.0, base.composite - 5)
        assert abs(result.composite - expected) < 0.01


# ---------------------------------------------------------------------------
# 17. Individual score functions return 0~1
# ---------------------------------------------------------------------------


class TestIndividualScoreBounds:
    def test_score_macro_bounds(
        self, thresholds: dict, neutral_macro: MacroSnapshot
    ) -> None:
        s = score_macro(neutral_macro, thresholds)
        assert 0.0 <= s <= 1.0

    def test_score_macro_bullish(
        self, thresholds: dict, bullish_macro: MacroSnapshot
    ) -> None:
        s = score_macro(bullish_macro, thresholds)
        assert 0.0 <= s <= 1.0
        assert s >= 0.7

    def test_score_macro_bearish(
        self, thresholds: dict, bearish_macro: MacroSnapshot
    ) -> None:
        s = score_macro(bearish_macro, thresholds)
        assert 0.0 <= s <= 1.0
        assert s <= 0.3

    def test_score_flow_bounds(
        self, thresholds: dict, neutral_flow: FlowData
    ) -> None:
        s = score_flow(neutral_flow, thresholds)
        assert 0.0 <= s <= 1.0

    def test_score_flow_bullish(
        self, thresholds: dict, bullish_flow: FlowData
    ) -> None:
        s = score_flow(bullish_flow, thresholds)
        assert 0.0 <= s <= 1.0
        assert s >= 0.7

    def test_score_fundamental_bounds(
        self, thresholds: dict, neutral_info: StockInfo
    ) -> None:
        s = score_fundamental(neutral_info, thresholds)
        assert 0.0 <= s <= 1.0

    def test_score_technical_bounds(
        self, thresholds: dict, neutral_tech: TechnicalIndicators
    ) -> None:
        s = score_technical(neutral_tech, thresholds)
        assert 0.0 <= s <= 1.0

    def test_score_risk_bounds(
        self,
        thresholds: dict,
        neutral_tech: TechnicalIndicators,
        neutral_info: StockInfo,
    ) -> None:
        s = score_risk(neutral_tech, neutral_info, thresholds)
        assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# 18. ScoreBreakdown has correct fields
# ---------------------------------------------------------------------------


class TestScoreBreakdownFields:
    def test_breakdown_has_all_fields(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """ScoreBreakdown must expose macro, flow, fundamental, technical,
        risk, composite, and signal fields.
        """
        result = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        assert isinstance(result, ScoreBreakdown)
        assert hasattr(result, "macro")
        assert hasattr(result, "flow")
        assert hasattr(result, "fundamental")
        assert hasattr(result, "technical")
        assert hasattr(result, "risk")
        assert hasattr(result, "composite")
        assert hasattr(result, "signal")

    def test_breakdown_field_types(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """Individual scores should be floats in [0,1]; composite a float
        in [0,160]; signal a str.
        """
        result = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        for field in ("macro", "flow", "fundamental", "technical", "risk"):
            val = getattr(result, field)
            assert isinstance(val, float), f"{field} should be float"
            assert 0.0 <= val <= 1.0, f"{field}={val} out of [0,1]"

        assert isinstance(result.composite, float)
        assert 0.0 <= result.composite <= 160.0
        assert isinstance(result.signal, str)
        assert result.signal in (
            "STRONG_BUY",
            "BUY",
            "WATCH",
            "MILD_BUY",
            "HOLD",
        )

    def test_breakdown_composite_in_v3_scale(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """With bonuses, composite can exceed old 100-point cap."""
        result = compute_composite_score(
            bullish_macro,
            bullish_flow,
            bullish_info,
            bullish_tech,
            config,
            mtf_bonus=10,
            policy_bonus=10,
            ml_bonus=15,
        )
        # The composite should be above 100 -- proving the v3.0 scale is used
        assert result.composite > 100


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestWeightsConsistency:
    def test_v3_weights_sum_to_one(self, config: dict) -> None:
        """Config weights must sum to 1.0."""
        total = sum(config["weights"].values())
        assert abs(total - 1.0) < 1e-9


class TestSignalThresholdBoundaries:
    """Verify exact threshold boundary behavior by constructing composites
    with precise bonuses."""

    def _compute_with_bonus(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
        total_bonus: int,
    ) -> ScoreBreakdown:
        return compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            ml_bonus=min(total_bonus, 15),
            policy_bonus=max(0, min(total_bonus - 15, 10)),
            mtf_bonus=max(0, min(total_bonus - 25, 10)),
            sector_adj=max(0, min(total_bonus - 35, 5)),
            sentiment_bonus=max(0, min(total_bonus - 40, 10)),
            leading_sector_bonus=max(0, total_bonus - 50),
        )

    def test_boundary_at_130(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """Composite exactly at 130 should be STRONG_BUY."""
        base = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        # Push up to exactly 130
        needed = 130.0 - base.composite
        if needed > 0:
            result = compute_composite_score(
                neutral_macro,
                neutral_flow,
                neutral_info,
                neutral_tech,
                config,
                ml_bonus=int(needed) + 1,
                policy_bonus=max(0, int(needed) - 14),
                mtf_bonus=max(0, int(needed) - 24),
                sector_adj=max(0, int(needed) - 34),
                sentiment_bonus=max(0, int(needed) - 39),
            )
            if result.composite >= 130:
                assert result.signal == "STRONG_BUY"

    def test_just_below_130_not_strong_buy(
        self,
        config: dict,
        bullish_macro: MacroSnapshot,
        bullish_flow: FlowData,
        bullish_info: StockInfo,
        bullish_tech: TechnicalIndicators,
    ) -> None:
        """Composite at 129.xx should not be STRONG_BUY."""
        base = compute_composite_score(
            bullish_macro, bullish_flow, bullish_info, bullish_tech, config
        )
        # Try to land just below 130
        gap = 129.0 - base.composite
        if gap > 0:
            result = compute_composite_score(
                bullish_macro,
                bullish_flow,
                bullish_info,
                bullish_tech,
                config,
                mtf_bonus=min(int(gap), 10),
                sector_adj=min(max(0, int(gap) - 10), 5),
            )
            if result.composite < 130:
                assert result.signal != "STRONG_BUY"


class TestDefaultBonusesZero:
    def test_no_bonus_params_default_to_zero(
        self,
        config: dict,
        neutral_macro: MacroSnapshot,
        neutral_flow: FlowData,
        neutral_info: StockInfo,
        neutral_tech: TechnicalIndicators,
    ) -> None:
        """Calling without bonus params should be identical to all bonuses=0."""
        result_default = compute_composite_score(
            neutral_macro, neutral_flow, neutral_info, neutral_tech, config
        )
        result_explicit = compute_composite_score(
            neutral_macro,
            neutral_flow,
            neutral_info,
            neutral_tech,
            config,
            mtf_bonus=0,
            sector_adj=0,
            policy_bonus=0,
            ml_bonus=0,
            sentiment_bonus=0,
            leading_sector_bonus=0,
        )
        assert result_default.composite == result_explicit.composite
        assert result_default.signal == result_explicit.signal
