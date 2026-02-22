"""Tests for 30억 goal aggressive mode (Section 43)."""

from __future__ import annotations

import pytest

from kstock.signal.aggressive_mode import (
    AggressiveConfig,
    GoalProgress,
    _build_aggressive_config,
    _format_krw,
    _make_progress_bar,
    check_safety_limits,
    compute_goal_progress,
    format_goal_dashboard,
    get_scoring_override,
    load_goal_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config() -> dict:
    """Return the default config dict (same as load_goal_config with no file)."""
    return load_goal_config()  # will use defaults since file likely missing


# ===========================================================================
# GoalProgress dataclass
# ===========================================================================

class TestGoalProgressDataclass:
    """GoalProgress dataclass structure."""

    def test_fields(self) -> None:
        gp = GoalProgress(
            start_asset=175_000_000,
            current_asset=200_000_000,
            target_asset=3_000_000_000,
            progress_pct=0.9,
            current_milestone="1차 목표",
            milestone_progress_pct=7.1,
            monthly_return_pct=1.5,
            needed_monthly_pct=8.2,
        )
        assert gp.start_asset == 175_000_000
        assert gp.target_asset == 3_000_000_000
        assert gp.progress_pct == 0.9

    def test_all_fields_accessible(self) -> None:
        gp = GoalProgress(
            start_asset=1, current_asset=2, target_asset=3,
            progress_pct=50.0, current_milestone="m",
            milestone_progress_pct=60.0,
            monthly_return_pct=10.0, needed_monthly_pct=5.0,
        )
        assert gp.monthly_return_pct == 10.0
        assert gp.needed_monthly_pct == 5.0


# ===========================================================================
# AggressiveConfig dataclass
# ===========================================================================

class TestAggressiveConfigDataclass:
    """AggressiveConfig defaults and construction."""

    def test_defaults(self) -> None:
        ac = AggressiveConfig()
        assert ac.max_positions == 5
        assert ac.max_single_pct == 50
        assert ac.stop_loss_single == -10
        assert ac.stop_loss_portfolio == -15
        assert ac.daily_loss_halt == -5

    def test_custom_values(self) -> None:
        ac = AggressiveConfig(max_positions=3, min_cash_pct=10)
        assert ac.max_positions == 3
        assert ac.min_cash_pct == 10


# ===========================================================================
# load_goal_config
# ===========================================================================

class TestLoadGoalConfig:
    """Loading and defaulting of user goal config."""

    def test_returns_dict_with_goal_key(self) -> None:
        config = load_goal_config()
        assert isinstance(config, dict)
        assert "goal" in config

    def test_goal_has_current_asset(self) -> None:
        config = load_goal_config()
        assert "current_asset" in config["goal"]

    def test_goal_has_target_asset(self) -> None:
        config = load_goal_config()
        assert config["goal"]["target_asset"] == 3_000_000_000

    def test_portfolio_rules_present(self) -> None:
        config = load_goal_config()
        assert "portfolio_rules" in config

    def test_scoring_override_present(self) -> None:
        config = load_goal_config()
        assert "scoring_override" in config


# ===========================================================================
# compute_goal_progress
# ===========================================================================

class TestComputeGoalProgress:
    """Goal progress computation."""

    def test_175m_progress_near_zero(self) -> None:
        """Starting value 1.75억 -> progress ~0%."""
        config = _default_config()
        gp = compute_goal_progress(175_000_000, config=config)
        assert gp.progress_pct == 0.0

    def test_175m_progress_roughly_5_8_pct(self) -> None:
        """1.75억 + some gains -> progress ~5.8%.

        range = 30억 - 1.75억 = 28.25억
        gained = 1.75억 * 0.058 = ~1_015_000 => low
        Instead: current = 175M + 163.5M = 338.5M => (338.5-175)/2825 = 5.79%
        """
        config = _default_config()
        current = 175_000_000 + 163_500_000  # 338.5M
        gp = compute_goal_progress(current, config=config)
        assert 5.0 <= gp.progress_pct <= 6.5

    def test_300m_higher_progress(self) -> None:
        """3억 -> progress = (3 - 1.75) / 28.25 * 100 = 4.42%."""
        config = _default_config()
        gp = compute_goal_progress(300_000_000, config=config)
        assert gp.progress_pct > 0
        assert gp.progress_pct > compute_goal_progress(
            175_000_000, config=config
        ).progress_pct

    def test_target_reached_100_pct(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(3_000_000_000, config=config)
        assert gp.progress_pct == 100.0

    def test_above_target_capped_at_100(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(5_000_000_000, config=config)
        assert gp.progress_pct == 100.0

    def test_returns_goal_progress_instance(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        assert isinstance(gp, GoalProgress)

    def test_start_asset_from_config(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        assert gp.start_asset == 175_000_000


# ===========================================================================
# get_scoring_override
# ===========================================================================

class TestGetScoringOverride:
    """Scoring override dict."""

    def test_returns_dict_with_tenbagger_bonus(self) -> None:
        result = get_scoring_override()
        assert isinstance(result, dict)
        assert "tenbagger_bonus" in result
        assert result["tenbagger_bonus"] == 20

    def test_has_momentum_weight(self) -> None:
        result = get_scoring_override()
        assert "momentum_weight" in result
        assert result["momentum_weight"] == 2.0

    def test_has_breakout_weight(self) -> None:
        result = get_scoring_override()
        assert "breakout_weight" in result
        assert result["breakout_weight"] == 1.5

    def test_has_ml_min_probability(self) -> None:
        result = get_scoring_override()
        assert result["ml_min_probability"] == 70


# ===========================================================================
# check_safety_limits
# ===========================================================================

class TestCheckSafetyLimits:
    """Safety limit checks with mocked config."""

    def test_single_stock_minus_10_triggers_warning(self) -> None:
        holdings = [
            {"name": "삼성전자", "ticker": "005930", "pnl_pct": -12.0, "eval": 10_000_000},
        ]
        warnings = check_safety_limits(holdings, total_eval=9_000_000, daily_pnl_pct=-2.0)
        assert any("손실" in w and "005930" in w for w in warnings)

    def test_portfolio_minus_15_triggers_warning(self) -> None:
        """Portfolio-level -15% should trigger '전종목 50% 현금화'."""
        holdings = [
            {"name": "A", "ticker": "001", "pnl_pct": -20.0, "eval": 8_000_000},
        ]
        # total_eval much less than total cost => portfolio pnl very negative
        warnings = check_safety_limits(holdings, total_eval=6_500_000, daily_pnl_pct=-1.0)
        assert any("현금화" in w for w in warnings)

    def test_daily_minus_5_triggers_warning(self) -> None:
        holdings = [
            {"name": "B", "ticker": "002", "pnl_pct": 5.0, "eval": 10_000_000},
        ]
        warnings = check_safety_limits(holdings, total_eval=10_000_000, daily_pnl_pct=-6.0)
        assert any("당일" in w and "매매 중단" in w for w in warnings)

    def test_leverage_etf_minus_7_triggers_warning(self) -> None:
        holdings = [
            {
                "name": "KODEX 레버리지",
                "ticker": "122630",
                "pnl_pct": -8.0,
                "eval": 5_000_000,
                "is_leverage": True,
            },
        ]
        warnings = check_safety_limits(holdings, total_eval=5_000_000, daily_pnl_pct=-1.0)
        assert any("레버리지" in w for w in warnings)

    def test_no_issues_returns_empty(self) -> None:
        holdings = [
            {"name": "삼성전자", "ticker": "005930", "pnl_pct": 5.0, "eval": 10_000_000},
        ]
        warnings = check_safety_limits(holdings, total_eval=10_500_000, daily_pnl_pct=-1.0)
        assert warnings == []

    def test_empty_holdings_no_crash(self) -> None:
        warnings = check_safety_limits([], total_eval=0, daily_pnl_pct=0.0)
        assert isinstance(warnings, list)

    def test_multiple_warnings_at_once(self) -> None:
        """Daily halt + single stock stop can trigger simultaneously."""
        holdings = [
            {"name": "X", "ticker": "100", "pnl_pct": -15.0, "eval": 5_000_000},
        ]
        warnings = check_safety_limits(holdings, total_eval=4_000_000, daily_pnl_pct=-6.0)
        assert len(warnings) >= 2


# ===========================================================================
# format_goal_dashboard
# ===========================================================================

class TestFormatGoalDashboard:
    """Dashboard formatting."""

    def test_returns_string_with_30억(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        text = format_goal_dashboard(gp)
        assert isinstance(text, str)
        assert "30억" in text

    def test_contains_progress_pct(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        text = format_goal_dashboard(gp)
        assert "진행률" in text

    def test_contains_milestone(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        text = format_goal_dashboard(gp)
        assert "마일스톤" in text

    def test_with_holdings(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        holdings = [{"name": "삼성전자", "pnl_pct": 3.5}]
        text = format_goal_dashboard(gp, holdings=holdings)
        assert "삼성전자" in text

    def test_with_tenbagger_count(self) -> None:
        config = _default_config()
        gp = compute_goal_progress(200_000_000, config=config)
        text = format_goal_dashboard(gp, tenbagger_count=2)
        assert "텐배거" in text


# ===========================================================================
# Internal helpers
# ===========================================================================

class TestInternalHelpers:
    """_format_krw and _make_progress_bar."""

    def test_format_krw_small(self) -> None:
        assert _format_krw(175_000_000) == "1.75억"

    def test_format_krw_medium(self) -> None:
        assert _format_krw(1_500_000_000) == "15.0억"

    def test_format_krw_large(self) -> None:
        assert _format_krw(3_000_000_000) == "30.0억"

    def test_progress_bar_0_pct(self) -> None:
        bar = _make_progress_bar(0.0)
        assert len(bar) == 10

    def test_progress_bar_100_pct(self) -> None:
        bar = _make_progress_bar(100.0)
        assert "\u2588" * 10 == bar
