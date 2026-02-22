"""Tests for kstock.signal.surge_detector module.

Covers: surge condition config, condition checking, exclusion filters,
health classification, stock scanning, alert formatting,
holding surge alerts, and small-cap linkage.
"""

import pytest

from kstock.signal.surge_detector import (
    SURGE_CONDITIONS,
    EXCLUDE_FILTERS,
    SurgeHealth,
    SurgeStock,
    check_surge_conditions,
    passes_exclude_filter,
    classify_surge_health,
    scan_stocks,
    format_surge_alert,
    format_holding_surge_alert,
    link_surge_to_smallcap,
)


# ---------------------------------------------------------------------------
# TestSurgeConditions
# ---------------------------------------------------------------------------
class TestSurgeConditions:
    """SURGE_CONDITIONS configuration validation."""

    def test_four_conditions_exist(self):
        assert len(SURGE_CONDITIONS) == 4
        assert set(SURGE_CONDITIONS.keys()) == {
            "price_surge", "volume_explosion", "combined", "limit_approach",
        }

    def test_price_surge_min_change(self):
        assert SURGE_CONDITIONS["price_surge"]["min_change"] == 5.0

    def test_volume_explosion_min_volume_ratio(self):
        assert SURGE_CONDITIONS["volume_explosion"]["min_volume_ratio"] == 3.0

    def test_combined_has_both_thresholds(self):
        cond = SURGE_CONDITIONS["combined"]
        assert cond["min_change"] == 3.0
        assert cond["min_volume_ratio"] == 2.0


# ---------------------------------------------------------------------------
# TestCheckSurgeConditions
# ---------------------------------------------------------------------------
class TestCheckSurgeConditions:
    """check_surge_conditions returns triggered condition keys."""

    def test_8pct_triggers_price_surge(self):
        triggers = check_surge_conditions(8.0, 1.0)
        assert "price_surge" in triggers

    def test_4x_volume_triggers_volume_explosion(self):
        triggers = check_surge_conditions(0.5, 4.0)
        assert "volume_explosion" in triggers

    def test_4pct_and_2_5x_triggers_combined(self):
        triggers = check_surge_conditions(4.0, 2.5)
        assert "combined" in triggers

    def test_28pct_triggers_limit_approach(self):
        triggers = check_surge_conditions(28.0, 1.0)
        assert "limit_approach" in triggers
        # Also triggers price_surge since 28 >= 5
        assert "price_surge" in triggers

    def test_1pct_and_1x_triggers_nothing(self):
        triggers = check_surge_conditions(1.0, 1.0)
        assert triggers == []


# ---------------------------------------------------------------------------
# TestPassesExcludeFilter
# ---------------------------------------------------------------------------
class TestPassesExcludeFilter:
    """passes_exclude_filter validates stocks against exclusion criteria."""

    def test_normal_stock_passes(self):
        result = passes_exclude_filter(
            market_cap=1_0000_0000_0000,   # 1조
            daily_volume=100_0000_0000,     # 100억
            is_managed=False,
            is_warning=False,
            listing_days=365,
        )
        assert result is True

    def test_low_market_cap_fails(self):
        result = passes_exclude_filter(
            market_cap=100_0000_0000,       # 100억 (< 500억)
            daily_volume=100_0000_0000,
            is_managed=False,
            is_warning=False,
            listing_days=365,
        )
        assert result is False

    def test_managed_stock_fails(self):
        result = passes_exclude_filter(
            market_cap=1_0000_0000_0000,
            daily_volume=100_0000_0000,
            is_managed=True,
            is_warning=False,
            listing_days=365,
        )
        assert result is False

    def test_new_listing_under_90_days_fails(self):
        result = passes_exclude_filter(
            market_cap=1_0000_0000_0000,
            daily_volume=100_0000_0000,
            is_managed=False,
            is_warning=False,
            listing_days=30,
        )
        assert result is False


# ---------------------------------------------------------------------------
# TestClassifySurgeHealth
# ---------------------------------------------------------------------------
class TestClassifySurgeHealth:
    """classify_surge_health grades surges as HEALTHY/CAUTION/DANGER."""

    def test_disclosure_inst_foreign_healthy(self):
        # disclosure +30, news +20, inst+foreign +25 = 75 -> HEALTHY
        health = classify_surge_health(
            change_pct=7.0, volume_ratio=2.5,
            has_news=True, has_disclosure=True,
            inst_net=100_0000, foreign_net=50_0000,
            retail_net=0, prev_vol_ratio=0.8,
            detected_time="10:30", past_suspicious_count=0,
        )
        assert health.grade == "HEALTHY"
        assert health.score >= 30

    def test_news_only_retail_only_caution(self):
        # news +20, no disclosure, no inst/foreign -20 = 0 -> CAUTION
        health = classify_surge_health(
            change_pct=6.0, volume_ratio=2.0,
            has_news=True, has_disclosure=False,
            inst_net=0, foreign_net=0,
            retail_net=500_0000, prev_vol_ratio=0.5,
            detected_time="11:00", past_suspicious_count=0,
        )
        assert health.grade == "CAUTION"
        assert 0 <= health.score < 30

    def test_no_news_retail_past_suspicious_danger(self):
        # no news -30, no inst/foreign -20, past_suspicious >= 3 -15 = -65 -> DANGER
        health = classify_surge_health(
            change_pct=10.0, volume_ratio=4.0,
            has_news=False, has_disclosure=False,
            inst_net=0, foreign_net=0,
            retail_net=1000_0000, prev_vol_ratio=0.3,
            detected_time="14:00", past_suspicious_count=5,
        )
        assert health.grade == "DANGER"
        assert health.score < 0

    def test_early_morning_no_disclosure_penalty(self):
        # Hour 9 without disclosure -> -10 penalty
        health = classify_surge_health(
            change_pct=6.0, volume_ratio=2.0,
            has_news=True, has_disclosure=False,
            inst_net=100_0000, foreign_net=50_0000,
            retail_net=0, prev_vol_ratio=0.5,
            detected_time="09:15", past_suspicious_count=0,
        )
        # news +20, inst+foreign +25, early morning no disclosure -10 = 35 -> HEALTHY
        # But the -10 brings it down; check that reason is recorded
        reason_texts = " ".join(health.reasons)
        assert "장 초반" in reason_texts

    def test_past_suspicious_gte_3_penalty(self):
        health = classify_surge_health(
            change_pct=6.0, volume_ratio=2.0,
            has_news=True, has_disclosure=True,
            inst_net=100_0000, foreign_net=50_0000,
            retail_net=0, prev_vol_ratio=0.5,
            detected_time="11:00", past_suspicious_count=4,
        )
        reason_texts = " ".join(health.reasons)
        assert "과거 의심 이력" in reason_texts


# ---------------------------------------------------------------------------
# TestScanStocks
# ---------------------------------------------------------------------------
class TestScanStocks:
    """scan_stocks filters, sorts, and limits surge stocks."""

    @staticmethod
    def _make_stock(ticker, name, price, change_pct, volume, avg_vol, market_cap=1_0000_0000_0000):
        return {
            "ticker": ticker, "name": name, "price": price,
            "change_pct": change_pct, "volume": volume,
            "avg_volume_20": avg_vol,
            "market_cap": market_cap,
            "daily_volume": 100_0000_0000,
            "is_managed": False, "is_warning": False, "listing_days": 365,
            "has_news": True, "has_disclosure": True,
            "inst_net": 10000, "foreign_net": 5000, "retail_net": 0,
            "prev_vol_ratio": 1.0, "detected_time": "10:00",
            "past_suspicious_count": 0,
        }

    def test_filters_and_sorts_by_change_pct(self):
        data = [
            self._make_stock("A", "종목A", 10000, 6.0, 500000, 100000),
            self._make_stock("B", "종목B", 20000, 10.0, 600000, 100000),
            self._make_stock("C", "종목C", 30000, 1.0, 50000, 100000),  # too low
        ]
        result = scan_stocks(data)
        # C should be excluded (no triggers at 1%), A and B remain
        assert len(result) == 2
        assert result[0].name == "종목B"  # highest change_pct first
        assert result[1].name == "종목A"

    def test_max_10_results(self):
        data = [
            self._make_stock(f"T{i:02d}", f"종목{i}", 10000, 6.0 + i, 500000, 100000)
            for i in range(15)
        ]
        result = scan_stocks(data)
        assert len(result) <= 10

    def test_empty_input_returns_empty(self):
        assert scan_stocks([]) == []


# ---------------------------------------------------------------------------
# TestFormatSurgeAlert
# ---------------------------------------------------------------------------
class TestFormatSurgeAlert:
    """format_surge_alert produces no-bold, user-facing text."""

    @staticmethod
    def _make_surge(grade="HEALTHY"):
        return SurgeStock(
            ticker="005930", name="삼성전자", price=70000,
            change_pct=7.5, volume_ratio=3.2,
            triggers=["price_surge", "combined"],
            market_cap=400_0000_0000_0000,
            scan_time="09:10",
            health=SurgeHealth(
                grade=grade, label="건전", score=45,
                reasons=["공시 확인됨 (+30)", "관련 뉴스 존재 (+20)"],
                action="주호님, 추세 편승 매매 고려해보세요.",
            ),
        )

    def test_no_bold_markers(self):
        text = format_surge_alert([self._make_surge()], "09:10")
        assert "**" not in text

    def test_contains_user_name(self):
        text = format_surge_alert([self._make_surge()], "09:10")
        assert "주호님" in text

    def test_contains_health_grade_icons(self):
        # HEALTHY should have checkmark icon
        text = format_surge_alert([self._make_surge("HEALTHY")], "09:10")
        assert "\u2705" in text  # green checkmark


# ---------------------------------------------------------------------------
# TestFormatHoldingSurgeAlert
# ---------------------------------------------------------------------------
class TestFormatHoldingSurgeAlert:
    """format_holding_surge_alert for owned stock surges."""

    @staticmethod
    def _make_surge():
        return SurgeStock(
            ticker="005930", name="삼성전자", price=77000,
            change_pct=10.0, volume_ratio=4.5,
            triggers=["price_surge"],
            market_cap=400_0000_0000_0000,
            health=SurgeHealth(
                grade="HEALTHY", label="건전", score=50,
                reasons=["공시 확인됨 (+30)"],
                action="주호님, 추세 편승 매매 고려해보세요.",
            ),
        )

    def test_no_bold_markers(self):
        text = format_holding_surge_alert(self._make_surge(), 10.0, 700000)
        assert "**" not in text

    def test_contains_profit_info(self):
        text = format_holding_surge_alert(self._make_surge(), 10.0, 700000)
        assert "수익률" in text
        assert "손익" in text


# ---------------------------------------------------------------------------
# TestLinkSurgeToSmallcap
# ---------------------------------------------------------------------------
class TestLinkSurgeToSmallcap:
    """link_surge_to_smallcap identifies small-cap surges worth analyzing."""

    def test_in_range_high_score_returns_text(self):
        surge = SurgeStock(
            ticker="247540", name="에코프로비엠", price=250000,
            change_pct=8.0, volume_ratio=3.0,
            triggers=["price_surge"],
            market_cap=2000_0000_0000,  # 2000억 (in 1000~5000 range)
            health=SurgeHealth(grade="HEALTHY", label="건전", score=50, reasons=[]),
        )
        result = link_surge_to_smallcap(surge, smallcap_score=75)
        assert result is not None
        assert "소형주" in result

    def test_out_of_range_returns_none(self):
        surge = SurgeStock(
            ticker="005930", name="삼성전자", price=70000,
            change_pct=8.0, volume_ratio=3.0,
            triggers=["price_surge"],
            market_cap=400_0000_0000_0000,  # 400조 (way above 5000억)
            health=SurgeHealth(grade="HEALTHY", label="건전", score=50, reasons=[]),
        )
        result = link_surge_to_smallcap(surge, smallcap_score=75)
        assert result is None
