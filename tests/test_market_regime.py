"""Tests for the market regime detection module."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.market_regime import RegimeResult, detect_regime


def _macro(
    vix: float = 18.0,
    regime: str = "neutral",
    spx_change_pct: float = 0.1,
    usdkrw: float = 1300.0,
    usdkrw_change_pct: float = 0.0,
    vix_change_pct: float = 0.0,
) -> MacroSnapshot:
    """Create a MacroSnapshot with sensible defaults for testing."""
    return MacroSnapshot(
        vix=vix,
        vix_change_pct=vix_change_pct,
        spx_change_pct=spx_change_pct,
        usdkrw=usdkrw,
        usdkrw_change_pct=usdkrw_change_pct,
        us10y=4.0,
        dxy=104.0,
        regime=regime,
    )


POLICY_PATCH = "kstock.signal.market_regime.has_bullish_policy"


# ---------------------------------------------------------------------------
# RegimeResult dataclass
# ---------------------------------------------------------------------------


class TestRegimeResult:
    def test_default_values(self) -> None:
        """RegimeResult should use default profit/stop values when not supplied."""
        result = RegimeResult(
            mode="balanced",
            emoji="x",
            label="test",
            message="msg",
            allocations={},
        )
        assert result.profit_target_pct == 5.0
        assert result.trailing_stop_pct == -5.0

    def test_custom_values(self) -> None:
        """RegimeResult should accept custom profit/stop values."""
        result = RegimeResult(
            mode="defense",
            emoji="x",
            label="test",
            message="msg",
            allocations={"cash": 50},
            profit_target_pct=3.0,
            trailing_stop_pct=-3.0,
        )
        assert result.profit_target_pct == 3.0
        assert result.trailing_stop_pct == -3.0
        assert result.allocations == {"cash": 50}


# ---------------------------------------------------------------------------
# Defense mode
# ---------------------------------------------------------------------------


class TestDefenseMode:
    @patch(POLICY_PATCH, return_value=False)
    def test_high_vix_triggers_defense(self, mock_policy) -> None:
        """VIX >= 25 should trigger defense mode regardless of other factors."""
        result = detect_regime(_macro(vix=25.0))
        assert result.mode == "defense"
        assert result.profit_target_pct == 3.0
        assert result.trailing_stop_pct == -3.0

    @patch(POLICY_PATCH, return_value=False)
    def test_very_high_vix_triggers_defense(self, mock_policy) -> None:
        """VIX well above 25 should still be defense."""
        result = detect_regime(_macro(vix=40.0))
        assert result.mode == "defense"

    @patch(POLICY_PATCH, return_value=False)
    def test_risk_off_regime_triggers_defense(self, mock_policy) -> None:
        """regime == 'risk_off' should trigger defense even if VIX is low."""
        result = detect_regime(_macro(vix=14.0, regime="risk_off"))
        assert result.mode == "defense"

    @patch(POLICY_PATCH, return_value=False)
    def test_defense_allocations(self, mock_policy) -> None:
        """Defense mode should have the correct allocation structure."""
        result = detect_regime(_macro(vix=30.0))
        assert result.allocations["cash"] == 35
        assert result.allocations["F"] == 0
        assert result.allocations["G"] == 0

    @patch(POLICY_PATCH, return_value=False)
    def test_defense_takes_priority_over_kospi_drop(self, mock_policy) -> None:
        """VIX >= 25 defense check comes before the KOSPI daily drop check."""
        result = detect_regime(
            _macro(vix=26.0),
            kospi_daily_drop=-4.0,
        )
        assert result.mode == "defense"


# ---------------------------------------------------------------------------
# Balanced mode (emergency -- KOSPI daily drop)
# ---------------------------------------------------------------------------


class TestBalancedEmergency:
    @patch(POLICY_PATCH, return_value=False)
    def test_kospi_drop_triggers_balanced(self, mock_policy) -> None:
        """KOSPI daily drop <= -3% should trigger emergency balanced mode."""
        result = detect_regime(
            _macro(vix=18.0),
            kospi_daily_drop=-3.0,
        )
        assert result.mode == "balanced"
        assert result.profit_target_pct == 3.0
        assert result.trailing_stop_pct == -5.0

    @patch(POLICY_PATCH, return_value=False)
    def test_kospi_severe_drop(self, mock_policy) -> None:
        """KOSPI daily drop worse than -3% should also trigger balanced."""
        result = detect_regime(
            _macro(vix=18.0),
            kospi_daily_drop=-5.0,
        )
        assert result.mode == "balanced"
        assert "KOSPI" in result.message

    @patch(POLICY_PATCH, return_value=False)
    def test_kospi_drop_just_above_threshold(self, mock_policy) -> None:
        """KOSPI daily drop of -2.99% should NOT trigger emergency balanced."""
        result = detect_regime(
            _macro(vix=18.0),
            kospi_daily_drop=-2.99,
        )
        assert result.mode != "defense"
        # Should fall through to later checks, not emergency balanced


# ---------------------------------------------------------------------------
# Bubble Attack mode
# ---------------------------------------------------------------------------


class TestBubbleAttackMode:
    @patch(POLICY_PATCH, return_value=True)
    def test_bubble_attack_all_conditions_met(self, mock_policy) -> None:
        """All three bubble conditions met: 60d return > 15%, bullish policy, VIX < 20."""
        result = detect_regime(
            _macro(vix=17.0),
            kospi_60d_return=16.0,
            today=date(2025, 6, 1),
        )
        assert result.mode == "bubble_attack"
        assert result.profit_target_pct == 8.0
        assert result.trailing_stop_pct == -7.0

    @patch(POLICY_PATCH, return_value=True)
    def test_bubble_attack_allocations(self, mock_policy) -> None:
        """Bubble attack should allocate heavily to momentum (F, G)."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=20.0,
        )
        assert result.mode == "bubble_attack"
        assert result.allocations["F"] == 30
        assert result.allocations["G"] == 20
        assert result.allocations["cash"] == 5
        assert result.allocations["trailing_mode"] is True

    @patch(POLICY_PATCH, return_value=False)
    def test_no_bubble_without_bullish_policy(self, mock_policy) -> None:
        """Without bullish policy, bubble attack should not trigger."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=20.0,
        )
        assert result.mode != "bubble_attack"

    @patch(POLICY_PATCH, return_value=True)
    def test_no_bubble_with_vix_at_20(self, mock_policy) -> None:
        """VIX must be strictly < 20 for bubble attack; VIX == 20 should not qualify."""
        result = detect_regime(
            _macro(vix=20.0),
            kospi_60d_return=20.0,
        )
        assert result.mode != "bubble_attack"

    @patch(POLICY_PATCH, return_value=True)
    def test_no_bubble_with_low_60d_return(self, mock_policy) -> None:
        """KOSPI 60d return must be strictly > 15%; exactly 15 should not qualify."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=15.0,
        )
        assert result.mode != "bubble_attack"

    @patch(POLICY_PATCH, return_value=True)
    def test_bubble_attack_with_foreign_sell_warning(self, mock_policy) -> None:
        """3+ day foreign sell + usdkrw spike should append warning to message."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=20.0,
            foreign_consecutive_sell_days=3,
            usdkrw_spike=True,
        )
        assert result.mode == "bubble_attack"
        assert "\u26a0\ufe0f" in result.message

    @patch(POLICY_PATCH, return_value=True)
    def test_bubble_attack_no_warning_without_fx_spike(self, mock_policy) -> None:
        """Foreign sell days >= 3 but no FX spike should not trigger warning."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=20.0,
            foreign_consecutive_sell_days=5,
            usdkrw_spike=False,
        )
        assert result.mode == "bubble_attack"
        assert "\u26a0\ufe0f" not in result.message

    @patch(POLICY_PATCH, return_value=True)
    def test_bubble_attack_no_warning_with_low_sell_days(self, mock_policy) -> None:
        """Foreign sell days < 3 even with FX spike should not trigger warning."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=20.0,
            foreign_consecutive_sell_days=2,
            usdkrw_spike=True,
        )
        assert result.mode == "bubble_attack"
        assert "\u26a0\ufe0f" not in result.message


# ---------------------------------------------------------------------------
# Attack mode
# ---------------------------------------------------------------------------


class TestAttackMode:
    @patch(POLICY_PATCH, return_value=False)
    def test_risk_on_triggers_attack(self, mock_policy) -> None:
        """regime == 'risk_on' should trigger attack mode."""
        result = detect_regime(_macro(vix=18.0, regime="risk_on"))
        assert result.mode == "attack"
        assert result.profit_target_pct == 5.0
        assert result.trailing_stop_pct == -5.0

    @patch(POLICY_PATCH, return_value=False)
    def test_low_vix_triggers_attack(self, mock_policy) -> None:
        """VIX < 15 should trigger attack mode even in neutral regime."""
        result = detect_regime(_macro(vix=14.0, regime="neutral"))
        assert result.mode == "attack"

    @patch(POLICY_PATCH, return_value=False)
    def test_attack_allocations(self, mock_policy) -> None:
        """Attack mode should have the correct allocation structure."""
        result = detect_regime(_macro(vix=14.0, regime="neutral"))
        assert result.allocations["A"] == 20
        assert result.allocations["F"] == 20
        assert result.allocations["cash"] == 5


# ---------------------------------------------------------------------------
# Default balanced mode
# ---------------------------------------------------------------------------


class TestDefaultBalanced:
    @patch(POLICY_PATCH, return_value=False)
    def test_neutral_regime_moderate_vix(self, mock_policy) -> None:
        """Neutral regime with VIX between 15-25 should fall through to balanced."""
        result = detect_regime(_macro(vix=18.0, regime="neutral"))
        assert result.mode == "balanced"
        assert result.profit_target_pct == 5.0
        assert result.trailing_stop_pct == -5.0

    @patch(POLICY_PATCH, return_value=False)
    def test_balanced_allocations(self, mock_policy) -> None:
        """Default balanced mode should have the correct allocation structure."""
        result = detect_regime(_macro(vix=20.0, regime="neutral"))
        assert result.allocations["cash"] == 15
        assert result.allocations["C"] == 20

    @patch(POLICY_PATCH, return_value=False)
    def test_vix_exactly_15_is_not_attack(self, mock_policy) -> None:
        """VIX == 15 should NOT trigger attack (needs VIX < 15)."""
        result = detect_regime(_macro(vix=15.0, regime="neutral"))
        assert result.mode == "balanced"


# ---------------------------------------------------------------------------
# Priority / edge cases
# ---------------------------------------------------------------------------


class TestPriorityAndEdgeCases:
    @patch(POLICY_PATCH, return_value=True)
    def test_defense_overrides_bubble_conditions(self, mock_policy) -> None:
        """Defense (VIX >= 25) should override even if all bubble conditions hold."""
        result = detect_regime(
            _macro(vix=26.0),
            kospi_60d_return=20.0,
        )
        assert result.mode == "defense"

    @patch(POLICY_PATCH, return_value=True)
    def test_kospi_drop_overrides_bubble(self, mock_policy) -> None:
        """KOSPI daily drop should override bubble attack when VIX is safe."""
        result = detect_regime(
            _macro(vix=15.0),
            kospi_60d_return=20.0,
            kospi_daily_drop=-4.0,
        )
        assert result.mode == "balanced"
        assert "KOSPI" in result.message

    @patch(POLICY_PATCH, return_value=True)
    def test_bubble_over_attack(self, mock_policy) -> None:
        """Bubble attack should take priority over plain attack when conditions met."""
        result = detect_regime(
            _macro(vix=14.0, regime="risk_on"),
            kospi_60d_return=20.0,
        )
        assert result.mode == "bubble_attack"

    @patch(POLICY_PATCH, return_value=False)
    def test_today_passed_to_policy(self, mock_policy) -> None:
        """The today parameter should be forwarded to has_bullish_policy."""
        test_date = date(2025, 7, 15)
        detect_regime(_macro(vix=18.0), today=test_date)
        mock_policy.assert_called_once_with(test_date)

    @patch(POLICY_PATCH, return_value=False)
    def test_default_parameters(self, mock_policy) -> None:
        """With all default parameters (zeros/None) and moderate VIX, should be balanced."""
        result = detect_regime(_macro(vix=20.0, regime="neutral"))
        assert result.mode == "balanced"

    @patch(POLICY_PATCH, return_value=False)
    def test_vix_boundary_24_99_not_defense(self, mock_policy) -> None:
        """VIX just below 25 should not trigger defense."""
        result = detect_regime(_macro(vix=24.99, regime="neutral"))
        assert result.mode != "defense"

    @patch(POLICY_PATCH, return_value=False)
    def test_regime_result_fields_populated(self, mock_policy) -> None:
        """All RegimeResult fields should be non-empty for any mode."""
        for vix, regime in [(30, "neutral"), (18, "neutral"), (14, "risk_on")]:
            result = detect_regime(_macro(vix=vix, regime=regime))
            assert result.mode in ("defense", "balanced", "attack", "bubble_attack")
            assert len(result.emoji) > 0
            assert len(result.label) > 0
            assert len(result.message) > 0
            assert isinstance(result.allocations, dict)
            assert len(result.allocations) > 0
