"""Tests for the short selling analysis modules.

Covers: short_selling, short_pattern, margin_balance,
        margin_calibrator, rebalance_engine, DB CRUD.
"""

from __future__ import annotations

import json

import pytest

from kstock.signal.short_selling import (
    ShortSellingData,
    ShortSellingSignal,
    analyze_short_selling,
    compute_short_score,
    detect_overheated,
    format_short_alert,
    get_all_inverse_etfs,
    get_inverse_etf_for_sector,
    INVERSE_ETF_SECTORS,
)
from kstock.signal.short_pattern import (
    ShortPattern,
    ShortPatternResult,
    detect_all_patterns,
    format_pattern_report,
)
from kstock.signal.margin_balance import (
    MarginData,
    MarginPattern,
    MarginSignal,
    compute_combined_leverage_score,
    detect_margin_patterns,
    format_margin_alert,
)
from kstock.signal.margin_calibrator import (
    CalibrationResult,
    calibrate_all_metrics,
    calibrate_metric,
    format_calibration_report,
    is_anomalous,
)
from kstock.signal.rebalance_engine import (
    DEFAULT_MILESTONES,
    RebalanceAction,
    RebalanceResult,
    evaluate_rebalance_triggers,
    format_rebalance_alert,
    get_milestones_with_status,
)
from kstock.store.sqlite import SQLiteStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(db_path=tmp_path / "test.db")


def _make_short_history(days=10, base_ratio=5.0, base_balance=10000, trend=0.0):
    """Generate synthetic short selling history."""
    result = []
    for i in range(days):
        ratio = base_ratio + trend * i
        balance = int(base_balance * (1 + trend * i / 100))
        result.append({
            "date": f"2026-02-{10 + i:02d}",
            "short_volume": 50000 + i * 1000,
            "total_volume": 500000 + i * 5000,
            "short_ratio": max(0, ratio),
            "short_balance": max(0, balance),
            "short_balance_ratio": max(0, ratio * 0.5),
        })
    return result


def _make_price_history(days=10, base_price=100000, trend=0.0):
    """Generate synthetic price history."""
    result = []
    for i in range(days):
        price = base_price * (1 + trend * i / 100)
        result.append({
            "date": f"2026-02-{10 + i:02d}",
            "close": price,
            "volume": 1000000 + i * 10000,
        })
    return result


def _make_margin_history(days=10, base_ratio=2.0, base_balance=5000, trend=0.0):
    """Generate synthetic margin balance history."""
    result = []
    for i in range(days):
        ratio = base_ratio + trend * i
        balance = int(base_balance * (1 + trend * i / 50))
        result.append({
            "date": f"2026-02-{10 + i:02d}",
            "credit_buy": 1000 + i * 100,
            "credit_sell": 800 + i * 50,
            "credit_balance": max(0, balance),
            "credit_ratio": max(0, ratio),
            "collateral_balance": 2000,
        })
    return result


# ===========================================================================
# TestShortSellingData
# ===========================================================================

class TestShortSellingData:
    """Verify ShortSellingData dataclass."""

    def test_create_data(self):
        data = ShortSellingData(
            ticker="005930", name="삼성전자", date="2026-02-23",
            short_volume=100000, total_volume=500000,
            short_ratio=20.0, short_balance=50000,
            short_balance_ratio=5.0,
        )
        assert data.ticker == "005930"
        assert data.short_ratio == 20.0

    def test_default_values(self):
        data = ShortSellingData(ticker="", name="", date="")
        assert data.short_volume == 0
        assert data.short_balance_ratio == 0.0


# ===========================================================================
# TestAnalyzeShortSelling
# ===========================================================================

class TestAnalyzeShortSelling:
    """Verify short selling analysis function."""

    def test_empty_history(self):
        signal = analyze_short_selling([], "005930", "삼성전자")
        assert signal.score_adj == 0
        assert signal.patterns == []
        assert "데이터 없음" in signal.message

    def test_normal_ratio(self):
        history = _make_short_history(days=10, base_ratio=3.0)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert not signal.is_overheated

    def test_overheated_detection(self):
        history = _make_short_history(days=10, base_ratio=25.0)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert signal.is_overheated
        assert signal.score_adj < 0
        assert any("과열" in p for p in signal.patterns)

    def test_high_short_ratio_warning(self):
        history = _make_short_history(days=10, base_ratio=12.0)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert signal.score_adj < 0
        assert any("주의" in p for p in signal.patterns)

    def test_balance_surge(self):
        history = _make_short_history(days=10, base_balance=10000, trend=35.0)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert any("급증" in p for p in signal.patterns)

    def test_balance_drop(self):
        history = _make_short_history(days=10, base_balance=100000, trend=-12.0)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert any("급감" in p for p in signal.patterns)

    def test_volume_surge(self):
        history = _make_short_history(days=10, base_ratio=3.0)
        # Make last day volume 3x average
        history[-1]["short_volume"] = history[-1]["short_volume"] * 5
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert any("폭증" in p for p in signal.patterns)

    def test_score_capped(self):
        # Overheated + balance surge + volume surge → capped at -15
        history = _make_short_history(days=10, base_ratio=25.0, base_balance=10000, trend=12.0)
        history[-1]["short_volume"] = 500000
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert signal.score_adj >= -15

    def test_overheated_days_count(self):
        history = _make_short_history(days=5, base_ratio=25.0)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        assert signal.overheated_days == 5


class TestDetectOverheated:
    """Verify overheated detection helper."""

    def test_by_short_ratio(self):
        history = [{"short_ratio": 22.0, "short_balance_ratio": 3.0}]
        assert detect_overheated(history) is True

    def test_by_balance_ratio(self):
        history = [{"short_ratio": 5.0, "short_balance_ratio": 12.0}]
        assert detect_overheated(history) is True

    def test_not_overheated(self):
        history = [{"short_ratio": 5.0, "short_balance_ratio": 3.0}]
        assert detect_overheated(history) is False

    def test_empty_history(self):
        assert detect_overheated([]) is False


class TestComputeShortScore:
    """Verify score capping."""

    def test_score_cap_positive(self):
        signal = ShortSellingSignal(ticker="", name="", score_adj=20)
        assert compute_short_score(signal) == 15

    def test_score_cap_negative(self):
        signal = ShortSellingSignal(ticker="", name="", score_adj=-20)
        assert compute_short_score(signal) == -15

    def test_score_within_range(self):
        signal = ShortSellingSignal(ticker="", name="", score_adj=5)
        assert compute_short_score(signal) == 5


class TestFormatShortAlert:
    """Verify short selling alert formatting."""

    def test_no_bold_markers(self):
        signal = analyze_short_selling(
            _make_short_history(days=5, base_ratio=25.0), "005930", "삼성전자",
        )
        text = format_short_alert(signal, _make_short_history(days=5))
        assert "**" not in text
        assert "삼성전자" in text

    def test_overheated_siren(self):
        signal = ShortSellingSignal(
            ticker="005930", name="삼성전자",
            is_overheated=True, overheated_days=3,
            patterns=["공매도 과열"], score_adj=-10,
        )
        text = format_short_alert(signal)
        assert "\U0001f6a8" in text

    def test_includes_balance_info(self):
        history = _make_short_history(days=5, base_ratio=5.0, base_balance=50000)
        signal = analyze_short_selling(history, "005930", "삼성전자")
        text = format_short_alert(signal, history)
        assert "잔고" in text


# ===========================================================================
# TestInverseETF
# ===========================================================================

class TestInverseETF:
    """Verify inverse ETF sector mapping."""

    def test_kospi_inverse_exists(self):
        etfs = get_inverse_etf_for_sector("코스피")
        assert len(etfs) >= 1
        assert any("114800" in e["ticker"] for e in etfs)

    def test_unknown_sector_empty(self):
        etfs = get_inverse_etf_for_sector("존재하지않는섹터")
        assert etfs == []

    def test_get_all_inverse_etfs(self):
        all_etfs = get_all_inverse_etfs()
        assert len(all_etfs) >= 4
        assert all("sector" in e for e in all_etfs)

    def test_sectors_defined(self):
        assert "코스피" in INVERSE_ETF_SECTORS
        assert "코스닥" in INVERSE_ETF_SECTORS


# ===========================================================================
# TestShortPattern
# ===========================================================================

class TestShortPatternDataclass:
    """Verify ShortPattern dataclass defaults."""

    def test_defaults(self):
        p = ShortPattern(name="테스트", code="test", description="설명")
        assert p.detected is False
        assert p.score_adj == 0
        assert p.confidence == 0.0


class TestDetectAllPatterns:
    """Verify 5-pattern detection engine."""

    def test_empty_history(self):
        result = detect_all_patterns([], ticker="005930", name="삼성전자")
        assert len(result.patterns) == 0
        assert result.total_score_adj == 0

    def test_real_buy_pattern(self):
        # Short balance dropping, price rising
        short_h = _make_short_history(days=10, base_balance=100000, trend=-3.0)
        price_h = _make_price_history(days=10, base_price=100000, trend=1.0)
        result = detect_all_patterns(short_h, price_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in result.patterns]
        assert "real_buy" in detected_codes

    def test_short_covering_rally(self):
        # Sharp short balance decrease + volume spike
        short_h = _make_short_history(days=10, base_balance=100000, trend=-8.0)
        price_h = _make_price_history(days=10, base_price=100000)
        # Spike volume
        price_h[-1]["volume"] = price_h[-2]["volume"] * 3
        result = detect_all_patterns(short_h, price_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in result.patterns]
        assert "short_covering" in detected_codes

    def test_short_buildup(self):
        # Gradual short ratio increase
        short_h = _make_short_history(days=10, base_ratio=3.0, trend=0.7)
        result = detect_all_patterns(short_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in result.patterns]
        assert "short_buildup" in detected_codes

    def test_short_squeeze(self):
        # High short ratio + price surge
        short_h = _make_short_history(days=10, base_ratio=12.0)
        price_h = _make_price_history(days=10, base_price=100000)
        price_h[-1]["close"] = price_h[-2]["close"] * 1.08  # 8% surge
        result = detect_all_patterns(short_h, price_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in result.patterns]
        assert "short_squeeze" in detected_codes

    def test_inverse_contrarian(self):
        # Inverse ETF volume surge without market decline
        inv_h = []
        for i in range(10):
            inv_h.append({"date": f"2026-02-{10+i:02d}", "volume": 100000})
        inv_h[-1]["volume"] = 400000  # 4x surge
        result = detect_all_patterns(
            [], inverse_etf_history=inv_h, market_change_pct=0.3,
            ticker="005930", name="삼성전자",
        )
        detected_codes = [p.code for p in result.patterns]
        assert "inverse_contrarian" in detected_codes

    def test_score_capped(self):
        # Multiple positive patterns
        short_h = _make_short_history(days=10, base_ratio=12.0, base_balance=100000, trend=-3.0)
        price_h = _make_price_history(days=10, base_price=100000, trend=1.5)
        price_h[-1]["close"] = price_h[-2]["close"] * 1.08
        price_h[-1]["volume"] = price_h[-2]["volume"] * 3
        result = detect_all_patterns(short_h, price_h, ticker="005930", name="삼성전자")
        assert result.total_score_adj <= 15
        assert result.total_score_adj >= -15

    def test_no_patterns_message(self):
        short_h = _make_short_history(days=10, base_ratio=3.0)
        price_h = _make_price_history(days=10, base_price=100000)
        result = detect_all_patterns(short_h, price_h, ticker="005930", name="삼성전자")
        if not result.patterns:
            assert "미감지" in result.message


class TestFormatPatternReport:
    """Verify pattern report formatting."""

    def test_no_bold(self):
        result = ShortPatternResult(
            ticker="005930", name="삼성전자",
            patterns=[ShortPattern(
                name="숏스퀴즈", code="short_squeeze",
                description="테스트", detected=True, score_adj=10, confidence=0.8,
            )],
            total_score_adj=10,
        )
        text = format_pattern_report(result)
        assert "**" not in text
        assert "삼성전자" in text
        assert "숏스퀴즈" in text

    def test_empty_patterns(self):
        result = ShortPatternResult(
            ticker="005930", name="삼성전자",
        )
        text = format_pattern_report(result)
        assert "감지된 패턴 없음" in text


# ===========================================================================
# TestMarginBalance
# ===========================================================================

class TestMarginDataclass:
    """Verify MarginData dataclass."""

    def test_create_data(self):
        data = MarginData(
            ticker="005930", name="삼성전자", date="2026-02-23",
            credit_balance=5000, credit_ratio=3.5,
        )
        assert data.credit_ratio == 3.5


class TestDetectMarginPatterns:
    """Verify 4 margin pattern detection."""

    def test_empty_history(self):
        signal = detect_margin_patterns([], ticker="005930", name="삼성전자")
        assert len(signal.patterns) == 0

    def test_forced_liquidation(self):
        margin_h = _make_margin_history(days=10, base_ratio=6.0)
        price_h = _make_price_history(days=10, base_price=100000, trend=-2.0)
        signal = detect_margin_patterns(margin_h, price_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in signal.patterns]
        assert "forced_liquidation" in detected_codes
        assert signal.is_dangerous

    def test_credit_clearing(self):
        # Peak credit ratio then sharp drop
        margin_h = _make_margin_history(days=20, base_ratio=8.0, trend=-0.3)
        signal = detect_margin_patterns(margin_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in signal.patterns]
        assert "credit_clearing" in detected_codes

    def test_retail_overheated(self):
        margin_h = _make_margin_history(days=10, base_ratio=4.0, base_balance=5000, trend=20.0)
        signal = detect_margin_patterns(margin_h, ticker="005930", name="삼성전자")
        detected_codes = [p.code for p in signal.patterns]
        assert "retail_overheated" in detected_codes

    def test_dual_leverage(self):
        margin_h = _make_margin_history(days=5, base_ratio=4.0)
        short_h = _make_short_history(days=5, base_ratio=6.0)
        signal = detect_margin_patterns(
            margin_h, short_history=short_h, ticker="005930", name="삼성전자",
        )
        detected_codes = [p.code for p in signal.patterns]
        assert "dual_leverage" in detected_codes

    def test_normal_no_patterns(self):
        margin_h = _make_margin_history(days=5, base_ratio=1.0)
        signal = detect_margin_patterns(margin_h, ticker="005930", name="삼성전자")
        assert len(signal.patterns) == 0
        assert not signal.is_dangerous

    def test_score_capped(self):
        # Multiple negative patterns
        margin_h = _make_margin_history(days=10, base_ratio=6.0, base_balance=5000, trend=3.0)
        price_h = _make_price_history(days=10, base_price=100000, trend=-2.0)
        short_h = _make_short_history(days=10, base_ratio=6.0)
        signal = detect_margin_patterns(margin_h, price_h, short_h, "005930", "삼성전자")
        assert signal.total_score_adj >= -15
        assert signal.total_score_adj <= 15


class TestCombinedLeverageScore:
    """Verify combined short + margin scoring."""

    def test_combined_positive(self):
        assert compute_combined_leverage_score(10, 10) == 20

    def test_combined_negative(self):
        assert compute_combined_leverage_score(-15, -15) == -30

    def test_capped_at_30(self):
        assert compute_combined_leverage_score(20, 20) == 30

    def test_capped_at_negative_30(self):
        assert compute_combined_leverage_score(-20, -20) == -30

    def test_mixed(self):
        assert compute_combined_leverage_score(10, -5) == 5


class TestFormatMarginAlert:
    """Verify margin alert formatting."""

    def test_no_bold(self):
        signal = MarginSignal(
            ticker="005930", name="삼성전자",
            patterns=[MarginPattern(
                name="반대매매 폭탄", code="forced_liquidation",
                description="테스트", detected=True, score_adj=-12,
                severity="danger",
            )],
            total_score_adj=-12, is_dangerous=True,
        )
        text = format_margin_alert(signal)
        assert "**" not in text
        assert "삼성전자" in text
        assert "반대매매" in text


# ===========================================================================
# TestMarginCalibrator
# ===========================================================================

class TestCalibrateMetric:
    """Verify adaptive threshold calibration."""

    def test_insufficient_data(self):
        history = _make_short_history(days=5)
        result = calibrate_metric(history, "short_ratio", "005930")
        assert result is None

    def test_normal_range(self):
        history = _make_short_history(days=30, base_ratio=5.0)
        result = calibrate_metric(history, "short_ratio", "005930")
        assert result is not None
        assert result.alert_level == "normal"
        assert result.mean_60d > 0

    def test_elevated_range(self):
        history = _make_short_history(days=30, base_ratio=5.0, trend=0.1)
        # Force a spike
        history[-1]["short_ratio"] = 15.0
        result = calibrate_metric(history, "short_ratio", "005930")
        assert result is not None
        # The spike may push z-score above 1.0
        if result.z_score >= 1.0:
            assert result.alert_level in ("elevated", "extreme")

    def test_z_score_computation(self):
        history = _make_short_history(days=30, base_ratio=5.0)
        result = calibrate_metric(history, "short_ratio", "005930")
        assert result is not None
        # z_score should be reasonable
        assert -5.0 <= result.z_score <= 5.0

    def test_sigma_boundaries(self):
        history = _make_short_history(days=30, base_ratio=5.0)
        result = calibrate_metric(history, "short_ratio", "005930")
        assert result is not None
        assert result.upper_1sigma >= result.mean_60d
        assert result.lower_1sigma <= result.mean_60d
        assert result.upper_2sigma >= result.upper_1sigma


class TestCalibrateAllMetrics:
    """Verify multi-metric calibration."""

    def test_short_only(self):
        short_h = _make_short_history(days=30, base_ratio=5.0)
        results = calibrate_all_metrics(short_h, ticker="005930")
        assert len(results) >= 1

    def test_with_margin(self):
        short_h = _make_short_history(days=30, base_ratio=5.0)
        margin_h = _make_margin_history(days=30, base_ratio=2.0)
        results = calibrate_all_metrics(short_h, margin_h, "005930")
        assert len(results) >= 2
        metrics = [r.metric for r in results]
        assert "credit_ratio" in metrics

    def test_empty_data(self):
        results = calibrate_all_metrics([], ticker="005930")
        assert results == []


class TestIsAnomalous:
    """Verify anomaly detection."""

    def test_anomalous(self):
        cal = CalibrationResult(
            ticker="005930", metric="short_ratio",
            mean_60d=5.0, std_60d=1.0,
            upper_1sigma=6.0, lower_1sigma=4.0,
            upper_2sigma=7.0, lower_2sigma=3.0,
            current_value=8.0, z_score=3.0, alert_level="extreme",
        )
        assert is_anomalous(cal) is True

    def test_not_anomalous(self):
        cal = CalibrationResult(
            ticker="005930", metric="short_ratio",
            mean_60d=5.0, std_60d=1.0,
            upper_1sigma=6.0, lower_1sigma=4.0,
            upper_2sigma=7.0, lower_2sigma=3.0,
            current_value=5.5, z_score=0.5, alert_level="normal",
        )
        assert is_anomalous(cal) is False

    def test_custom_threshold(self):
        cal = CalibrationResult(
            ticker="005930", metric="short_ratio",
            mean_60d=5.0, std_60d=1.0,
            upper_1sigma=6.0, lower_1sigma=4.0,
            upper_2sigma=7.0, lower_2sigma=3.0,
            current_value=6.5, z_score=1.5, alert_level="elevated",
        )
        assert is_anomalous(cal, sigma_threshold=1.0) is True
        assert is_anomalous(cal, sigma_threshold=2.0) is False


class TestFormatCalibrationReport:
    """Verify calibration report formatting."""

    def test_no_bold(self):
        results = [CalibrationResult(
            ticker="005930", metric="short_ratio",
            mean_60d=5.0, std_60d=1.0,
            upper_1sigma=6.0, lower_1sigma=4.0,
            upper_2sigma=7.0, lower_2sigma=3.0,
            current_value=5.5, z_score=0.5, alert_level="normal",
        )]
        text = format_calibration_report(results, "삼성전자")
        assert "**" not in text
        assert "삼성전자" in text
        assert "정상" in text


# ===========================================================================
# TestRebalanceEngine
# ===========================================================================

class TestEvaluateRebalanceTriggers:
    """Verify 6 rebalance triggers."""

    def test_no_triggers(self):
        result = evaluate_rebalance_triggers()
        assert not result.needs_rebalance
        assert len(result.actions) == 0

    def test_concentration_trigger(self):
        holdings = [
            {"name": "삼성전자", "ticker": "005930", "eval_amount": 90_000_000},
            {"name": "SK하이닉스", "ticker": "000660", "eval_amount": 10_000_000},
        ]
        result = evaluate_rebalance_triggers(
            holdings=holdings, total_eval=100_000_000,
        )
        assert result.needs_rebalance
        trigger_types = [a.trigger_type for a in result.actions]
        assert "concentration" in trigger_types

    def test_portfolio_stop_loss_trigger(self):
        result = evaluate_rebalance_triggers(
            total_eval=80_000_000, total_invested=100_000_000,
        )
        assert result.needs_rebalance
        trigger_types = [a.trigger_type for a in result.actions]
        assert "portfolio_stop" in trigger_types

    def test_milestone_trigger(self):
        milestones = [
            {"name": "5억 돌파", "target": 500_000_000, "reached": False},
        ]
        result = evaluate_rebalance_triggers(
            current_asset=550_000_000, milestones=milestones,
        )
        assert result.needs_rebalance
        trigger_types = [a.trigger_type for a in result.actions]
        assert "milestone" in trigger_types

    def test_correlation_trigger(self):
        holdings = [
            {"name": "A", "ticker": "001", "sector": "2차전지"},
            {"name": "B", "ticker": "002", "sector": "2차전지"},
            {"name": "C", "ticker": "003", "sector": "2차전지"},
        ]
        result = evaluate_rebalance_triggers(holdings=holdings)
        trigger_types = [a.trigger_type for a in result.actions]
        assert "correlation" in trigger_types

    def test_leverage_excess_trigger(self):
        result = evaluate_rebalance_triggers(credit_ratio=20.0, margin_ratio=15.0)
        assert result.needs_rebalance
        trigger_types = [a.trigger_type for a in result.actions]
        assert "leverage_excess" in trigger_types

    def test_short_squeeze_trigger(self):
        signals = [
            {
                "ticker": "005930", "name": "삼성전자",
                "patterns": [
                    {"code": "short_squeeze", "detected": True},
                ],
            },
        ]
        result = evaluate_rebalance_triggers(short_signals=signals)
        trigger_types = [a.trigger_type for a in result.actions]
        assert "short_squeeze" in trigger_types


class TestGetMilestones:
    """Verify milestone status tracking."""

    def test_default_milestones(self):
        assert len(DEFAULT_MILESTONES) == 6

    def test_milestones_with_small_asset(self):
        ms = get_milestones_with_status(100_000_000)
        assert all(m["reached"] is False for m in ms)

    def test_milestones_with_large_asset(self):
        ms = get_milestones_with_status(3_500_000_000)
        assert all(m["reached"] is True for m in ms)

    def test_milestones_partial(self):
        ms = get_milestones_with_status(750_000_000)
        reached = [m for m in ms if m["reached"]]
        not_reached = [m for m in ms if not m["reached"]]
        assert len(reached) == 1  # 5억 reached
        assert len(not_reached) == 5


class TestFormatRebalanceAlert:
    """Verify rebalance alert formatting."""

    def test_no_rebalance_needed(self):
        result = RebalanceResult(actions=[], needs_rebalance=False)
        text = format_rebalance_alert(result)
        assert "**" not in text
        assert "불필요" in text

    def test_with_actions(self):
        result = RebalanceResult(
            actions=[
                RebalanceAction(
                    trigger_type="concentration", trigger_name="집중도 초과",
                    description="삼성전자 비중 45%", action="비중 축소",
                    urgency="high",
                ),
            ],
            needs_rebalance=True,
        )
        text = format_rebalance_alert(result)
        assert "**" not in text
        assert "집중도" in text
        assert "주호님" in text

    def test_critical_urgency(self):
        result = RebalanceResult(
            actions=[
                RebalanceAction(
                    trigger_type="portfolio_stop", trigger_name="포트폴리오 손절",
                    description="전체 수익률 -20%", action="50% 축소",
                    urgency="critical",
                ),
            ],
            needs_rebalance=True,
        )
        text = format_rebalance_alert(result)
        assert "\U0001f6a8" in text


# ===========================================================================
# TestDBCRUD - Short Selling
# ===========================================================================

class TestDBShortSelling:
    """Verify short_selling table operations."""

    def test_add_and_get(self, store):
        rid = store.add_short_selling(
            ticker="005930", date="2026-02-23",
            short_volume=100000, total_volume=500000,
            short_ratio=20.0, short_balance=50000,
            short_balance_ratio=5.0,
        )
        assert rid is not None
        rows = store.get_short_selling("005930", days=7)
        assert len(rows) >= 1
        assert rows[0]["short_ratio"] == 20.0

    def test_unique_constraint(self, store):
        store.add_short_selling("005930", "2026-02-23", short_ratio=10.0)
        # Same ticker+date should be ignored
        result = store.add_short_selling("005930", "2026-02-23", short_ratio=15.0)
        assert result is None

    def test_get_latest(self, store):
        store.add_short_selling("005930", "2026-02-22", short_ratio=5.0)
        store.add_short_selling("005930", "2026-02-23", short_ratio=8.0)
        latest = store.get_short_selling_latest("005930")
        assert latest is not None
        assert latest["short_ratio"] == 8.0

    def test_get_overheated(self, store):
        store.add_short_selling("005930", "2026-02-23", short_ratio=25.0)
        store.add_short_selling("000660", "2026-02-23", short_ratio=5.0)
        overheated = store.get_overheated_shorts(min_ratio=20.0, days=7)
        assert len(overheated) >= 1
        assert overheated[0]["ticker"] == "005930"

    def test_empty_results(self, store):
        rows = store.get_short_selling("999999", days=7)
        assert rows == []
        latest = store.get_short_selling_latest("999999")
        assert latest is None


# ===========================================================================
# TestDBCRUD - Inverse ETF
# ===========================================================================

class TestDBInverseETF:
    """Verify inverse_etf table operations."""

    def test_add_and_get(self, store):
        rid = store.add_inverse_etf(
            ticker="114800", date="2026-02-23",
            name="KODEX 인버스", sector="코스피",
            volume=5000000, price=5500, change_pct=-1.2,
        )
        assert rid is not None
        rows = store.get_inverse_etf("114800", days=7)
        assert len(rows) >= 1

    def test_get_by_sector(self, store):
        store.add_inverse_etf("114800", "2026-02-23", sector="코스피")
        store.add_inverse_etf("251340", "2026-02-23", sector="코스닥")
        rows = store.get_inverse_etf_by_sector("코스피", days=7)
        assert len(rows) >= 1
        assert all(r["sector"] == "코스피" for r in rows)


# ===========================================================================
# TestDBCRUD - Margin Balance
# ===========================================================================

class TestDBMarginBalance:
    """Verify margin_balance table operations."""

    def test_add_and_get(self, store):
        rid = store.add_margin_balance(
            ticker="005930", date="2026-02-23",
            credit_buy=1000, credit_sell=500,
            credit_balance=5000, credit_ratio=3.5,
            collateral_balance=2000,
        )
        assert rid is not None
        rows = store.get_margin_balance("005930", days=7)
        assert len(rows) >= 1
        assert rows[0]["credit_ratio"] == 3.5

    def test_get_latest(self, store):
        store.add_margin_balance("005930", "2026-02-22", credit_ratio=3.0)
        store.add_margin_balance("005930", "2026-02-23", credit_ratio=4.0)
        latest = store.get_margin_balance_latest("005930")
        assert latest is not None
        assert latest["credit_ratio"] == 4.0

    def test_unique_constraint(self, store):
        store.add_margin_balance("005930", "2026-02-23", credit_ratio=3.0)
        result = store.add_margin_balance("005930", "2026-02-23", credit_ratio=5.0)
        assert result is None

    def test_empty_results(self, store):
        rows = store.get_margin_balance("999999", days=7)
        assert rows == []
        latest = store.get_margin_balance_latest("999999")
        assert latest is None


# ===========================================================================
# TestDBCRUD - Margin Thresholds
# ===========================================================================

class TestDBMarginThresholds:
    """Verify margin_thresholds table operations."""

    def test_upsert_and_get(self, store):
        store.upsert_margin_threshold(
            ticker="005930", metric="short_ratio",
            mean_60d=5.0, std_60d=1.0,
            upper_1sigma=6.0, lower_1sigma=4.0,
            upper_2sigma=7.0, lower_2sigma=3.0,
        )
        rows = store.get_margin_thresholds("005930")
        assert len(rows) == 1
        assert rows[0]["mean_60d"] == 5.0

    def test_upsert_overwrites(self, store):
        store.upsert_margin_threshold("005930", "short_ratio", mean_60d=5.0)
        store.upsert_margin_threshold("005930", "short_ratio", mean_60d=8.0)
        rows = store.get_margin_thresholds("005930")
        assert len(rows) == 1
        assert rows[0]["mean_60d"] == 8.0

    def test_multiple_metrics(self, store):
        store.upsert_margin_threshold("005930", "short_ratio", mean_60d=5.0)
        store.upsert_margin_threshold("005930", "credit_ratio", mean_60d=3.0)
        rows = store.get_margin_thresholds("005930")
        assert len(rows) == 2

    def test_empty_results(self, store):
        rows = store.get_margin_thresholds("999999")
        assert rows == []


# ===========================================================================
# TestDBCRUD - Rebalance History
# ===========================================================================

class TestDBRebalanceHistory:
    """Verify rebalance_history table operations."""

    def test_add_and_get(self, store):
        rid = store.add_rebalance_event(
            trigger_type="concentration",
            description="삼성전자 비중 45%",
            action="비중 축소",
            tickers_json=json.dumps(["005930"]),
        )
        assert rid > 0
        rows = store.get_rebalance_history(limit=5)
        assert len(rows) == 1
        assert rows[0]["trigger_type"] == "concentration"

    def test_mark_executed(self, store):
        rid = store.add_rebalance_event("milestone", "5억 달성")
        store.mark_rebalance_executed(rid)
        rows = store.get_rebalance_history(limit=5)
        assert rows[0]["executed"] == 1

    def test_history_limit(self, store):
        for i in range(10):
            store.add_rebalance_event(f"trigger_{i}", f"이벤트 {i}")
        rows = store.get_rebalance_history(limit=5)
        assert len(rows) == 5

    def test_empty_history(self, store):
        rows = store.get_rebalance_history()
        assert rows == []


# ===========================================================================
# TestWeeklyReportSections
# ===========================================================================

class TestWeeklyReportSections:
    """Verify weekly report now has sections 8 and 9."""

    def test_has_short_selling_section(self, store):
        from kstock.bot.weekly_report import collect_weekly_data, generate_report_content
        data = collect_weekly_data(store)
        content = generate_report_content(data)
        assert "8. 공매도 동향" in content

    def test_has_leverage_section(self, store):
        from kstock.bot.weekly_report import collect_weekly_data, generate_report_content
        data = collect_weekly_data(store)
        content = generate_report_content(data)
        assert "9. 레버리지 동향" in content

    def test_sections_no_bold(self, store):
        from kstock.bot.weekly_report import collect_weekly_data, generate_report_content
        data = collect_weekly_data(store)
        content = generate_report_content(data)
        assert "**" not in content

    def test_still_has_original_sections(self, store):
        from kstock.bot.weekly_report import collect_weekly_data, generate_report_content
        data = collect_weekly_data(store)
        content = generate_report_content(data)
        assert "1. 주간 시장 요약" in content
        assert "7. 30억 로드맵" in content
        assert "K-Quant v3.5 AI" in content


# ===========================================================================
# TestBotShortCommand
# ===========================================================================

class TestBotShortCommand:
    """Verify /short command handler exists."""

    def test_cmd_short_method_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_short")

    def test_cmd_short_is_async(self):
        import asyncio
        from kstock.bot.bot import KQuantBot
        assert asyncio.iscoroutinefunction(KQuantBot.cmd_short)
