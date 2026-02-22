"""Tests for kstock.signal.stealth_accumulation module."""

from __future__ import annotations

import pytest

from kstock.signal.stealth_accumulation import (
    ACCUMULATION_PATTERNS,
    PENSION_ENTITIES,
    AccumulationDetection,
    StreakResult,
    count_streak,
    detect_dual_accumulation,
    detect_foreign_streak,
    detect_institutional_streak,
    detect_pension_entry,
    format_accumulation_alert,
    format_pension_alert,
    integrate_accumulation_score,
    scan_all_stocks,
    scan_stock,
)


# ---------------------------------------------------------------------------
# TestAccumulationPatterns
# ---------------------------------------------------------------------------
class TestAccumulationPatterns:
    def test_four_patterns_exist(self):
        assert len(ACCUMULATION_PATTERNS) == 4
        assert "institutional_streak" in ACCUMULATION_PATTERNS
        assert "foreign_streak" in ACCUMULATION_PATTERNS
        assert "dual_accumulation" in ACCUMULATION_PATTERNS
        assert "pension_entry" in ACCUMULATION_PATTERNS

    def test_institutional_needs_5_days(self):
        cfg = ACCUMULATION_PATTERNS["institutional_streak"]
        assert cfg["min_consecutive_days"] == 5

    def test_dual_needs_3_days(self):
        cfg = ACCUMULATION_PATTERNS["dual_accumulation"]
        assert cfg["min_consecutive_days"] == 3

    def test_pension_has_entities(self):
        cfg = ACCUMULATION_PATTERNS["pension_entry"]
        entities = cfg["entities"]
        assert len(entities) >= 4
        assert "국민연금" in entities
        assert "사학연금" in entities
        assert entities == PENSION_ENTITIES


# ---------------------------------------------------------------------------
# TestCountStreak
# ---------------------------------------------------------------------------
class TestCountStreak:
    def test_five_day_streak(self):
        amounts = [1e8, 2e8, 3e8, 4e8, 5e8]
        days, total = count_streak(amounts, min_daily=1e8)
        assert days == 5
        assert total == 15e8

    def test_breaks_at_zero(self):
        # [1e8, 0, 2e8, 3e8, 4e8] -> streak from end: 4e8, 3e8, 2e8 then 0 breaks
        amounts = [1e8, 0, 2e8, 3e8, 4e8]
        days, total = count_streak(amounts, min_daily=1e8)
        assert days == 3
        assert total == 9e8

    def test_empty_list(self):
        days, total = count_streak([], min_daily=1e8)
        assert days == 0
        assert total == 0.0

    def test_all_below_min(self):
        amounts = [5e7, 3e7, 2e7]
        days, total = count_streak(amounts, min_daily=1e8)
        assert days == 0
        assert total == 0.0


# ---------------------------------------------------------------------------
# TestDetectInstitutionalStreak
# ---------------------------------------------------------------------------
class TestDetectInstitutionalStreak:
    def test_5_day_streak_returns_result(self):
        # 5 consecutive days above 1e8 each
        amounts = [2e8, 3e8, 2e8, 1e8, 4e8]
        result = detect_institutional_streak(amounts)
        assert result is not None
        assert isinstance(result, StreakResult)
        assert result.streak_days == 5
        assert result.score == 30  # base weight for exactly 5 days

    def test_3_day_streak_returns_none(self):
        # Only 3 days (below the 5-day minimum)
        amounts = [2e8, 3e8, 2e8]
        result = detect_institutional_streak(amounts)
        assert result is None

    def test_respects_max_daily_amount(self):
        # avg_daily > max_daily_amount (5e10) -> not stealth, returns None
        amounts = [6e10, 6e10, 6e10, 6e10, 6e10]
        result = detect_institutional_streak(amounts)
        assert result is None


# ---------------------------------------------------------------------------
# TestDetectForeignStreak
# ---------------------------------------------------------------------------
class TestDetectForeignStreak:
    def test_5_day_streak_returns_result(self):
        amounts = [5e8, 3e8, 4e8, 2e8, 6e8]
        result = detect_foreign_streak(amounts)
        assert result is not None
        assert isinstance(result, StreakResult)
        assert result.streak_days == 5
        assert "외인" in result.pattern_name

    def test_short_streak_returns_none(self):
        amounts = [5e8, 0, 4e8, 2e8]
        result = detect_foreign_streak(amounts)
        assert result is None


# ---------------------------------------------------------------------------
# TestDetectDualAccumulation
# ---------------------------------------------------------------------------
class TestDetectDualAccumulation:
    def test_both_3_plus_days(self):
        inst = [1e8, 2e8, 3e8]
        foreign = [2e8, 1e8, 5e8]
        result = detect_dual_accumulation(inst, foreign)
        assert result is not None
        assert isinstance(result, StreakResult)
        assert result.streak_days == 3
        assert result.score == 50  # base weight for dual at exactly 3 days
        assert "동시" in result.pattern_name

    def test_only_one_side_returns_none(self):
        inst = [1e8, 2e8, 3e8]
        foreign = [0, 0, 0]  # no foreign buying
        result = detect_dual_accumulation(inst, foreign)
        assert result is None

    def test_mixed_partial_overlap(self):
        # inst buys all 5 days, foreign only last 2 -> dual streak is 2, below min_days=3
        inst = [1e8, 1e8, 1e8, 1e8, 1e8]
        foreign = [0, 0, 0, 2e8, 3e8]
        result = detect_dual_accumulation(inst, foreign)
        assert result is None


# ---------------------------------------------------------------------------
# TestDetectPensionEntry
# ---------------------------------------------------------------------------
class TestDetectPensionEntry:
    def test_national_pension(self):
        text = "대량보유보고서: 국민연금 5.12% 취득"
        result = detect_pension_entry(text)
        assert result is not None
        assert "국민연금" in result.pattern_name
        assert result.score == 40

    def test_private_school_pension(self):
        text = "사학연금이 지분 보고"
        result = detect_pension_entry(text)
        assert result is not None
        assert "사학연금" in result.pattern_name

    def test_no_entity_returns_none(self):
        text = "일반 기관투자자 매수 보고"
        result = detect_pension_entry(text)
        assert result is None


# ---------------------------------------------------------------------------
# TestScanStock
# ---------------------------------------------------------------------------
class TestScanStock:
    def test_patterns_found(self):
        inst = [2e8, 3e8, 2e8, 1e8, 4e8]
        foreign = [5e8, 3e8, 4e8, 2e8, 6e8]
        result = scan_stock(
            ticker="005930",
            name="삼성전자",
            daily_inst=inst,
            daily_foreign=foreign,
            price_change_20d=0.03,
        )
        assert result is not None
        assert isinstance(result, AccumulationDetection)
        assert result.ticker == "005930"
        assert result.name == "삼성전자"
        assert result.total_score > 0
        assert len(result.patterns) > 0

    def test_no_patterns_returns_none(self):
        result = scan_stock(
            ticker="005930",
            name="삼성전자",
            daily_inst=[0, 0, 0],
            daily_foreign=[0, 0, 0],
            price_change_20d=0.02,
        )
        assert result is None

    def test_price_change_over_10_pct_returns_none(self):
        # Even with strong accumulation, price already moved > 10%
        inst = [2e8, 3e8, 2e8, 1e8, 4e8]
        foreign = [5e8, 3e8, 4e8, 2e8, 6e8]
        result = scan_stock(
            ticker="005930",
            name="삼성전자",
            daily_inst=inst,
            daily_foreign=foreign,
            price_change_20d=0.15,
        )
        assert result is None


# ---------------------------------------------------------------------------
# TestScanAllStocks
# ---------------------------------------------------------------------------
class TestScanAllStocks:
    def _make_stock(self, ticker: str, name: str, score_hint: float) -> dict:
        """Helper to make a stock dict that will produce detectable patterns."""
        # 5-day institutional streak with varying amounts
        base = 2e8 * (1 + score_hint)
        return {
            "ticker": ticker,
            "name": name,
            "daily_inst": [base, base, base, base, base],
            "daily_foreign": [base, base, base, base, base],
            "price_change_20d": 0.02,
        }

    def test_returns_sorted_by_score(self):
        stocks = [
            self._make_stock("000660", "SK하이닉스", 0.5),
            self._make_stock("005930", "삼성전자", 1.0),
        ]
        results = scan_all_stocks(stocks)
        assert len(results) >= 1
        if len(results) >= 2:
            assert results[0].total_score >= results[1].total_score

    def test_max_20_results(self):
        # Create 25 stocks that will all have detectable patterns
        stocks = [
            self._make_stock(f"{i:06d}", f"종목{i}", 0.1 * i)
            for i in range(1, 26)
        ]
        results = scan_all_stocks(stocks)
        assert len(results) <= 20


# ---------------------------------------------------------------------------
# TestIntegrateAccumulationScore
# ---------------------------------------------------------------------------
class TestIntegrateAccumulationScore:
    def test_dual_adds_15(self):
        detection = AccumulationDetection(
            ticker="005930",
            name="삼성전자",
            patterns=[
                StreakResult(
                    pattern_name="기관+외인 동시 매수",
                    streak_days=5,
                    total_amount=10e8,
                    avg_daily=2e8,
                    score=50,
                ),
            ],
            total_score=50,
        )
        result = integrate_accumulation_score(100, detection)
        assert result == 115  # 100 + 15

    def test_institutional_adds_8(self):
        detection = AccumulationDetection(
            ticker="005930",
            name="삼성전자",
            patterns=[
                StreakResult(
                    pattern_name="기관 연속 매수",
                    streak_days=5,
                    total_amount=5e8,
                    avg_daily=1e8,
                    score=30,
                ),
            ],
            total_score=30,
        )
        result = integrate_accumulation_score(100, detection)
        assert result == 108  # 100 + 8

    def test_none_adds_zero(self):
        result = integrate_accumulation_score(100, None)
        assert result == 100

    def test_capped_at_250(self):
        detection = AccumulationDetection(
            ticker="005930",
            name="삼성전자",
            patterns=[
                StreakResult(
                    pattern_name="기관+외인 동시 매수",
                    streak_days=5,
                    total_amount=10e8,
                    avg_daily=2e8,
                    score=50,
                ),
                StreakResult(
                    pattern_name="기관 연속 매수",
                    streak_days=5,
                    total_amount=5e8,
                    avg_daily=1e8,
                    score=30,
                ),
            ],
            total_score=80,
        )
        result = integrate_accumulation_score(245, detection)
        assert result == 250


# ---------------------------------------------------------------------------
# TestFormatAccumulationAlert
# ---------------------------------------------------------------------------
class TestFormatAccumulationAlert:
    def _make_detection(self) -> AccumulationDetection:
        return AccumulationDetection(
            ticker="005930",
            name="삼성전자",
            patterns=[
                StreakResult(
                    pattern_name="기관 연속 매수",
                    streak_days=7,
                    total_amount=14e8,
                    avg_daily=2e8,
                    score=30,
                ),
            ],
            total_score=30,
            price_change_20d=0.03,
        )

    def test_no_bold(self):
        msg = format_accumulation_alert([self._make_detection()])
        assert "**" not in msg

    def test_contains_juhonim(self):
        msg = format_accumulation_alert([self._make_detection()])
        assert "주호님" in msg

    def test_contains_pattern_info(self):
        msg = format_accumulation_alert([self._make_detection()])
        assert "기관" in msg
        assert "삼성전자" in msg
        assert "스코어" in msg


# ---------------------------------------------------------------------------
# TestFormatPensionAlert
# ---------------------------------------------------------------------------
class TestFormatPensionAlert:
    def _make_detection(self) -> AccumulationDetection:
        return AccumulationDetection(
            ticker="005930",
            name="삼성전자",
            patterns=[
                StreakResult(
                    pattern_name="연기금 진입 (국민연금)",
                    streak_days=0,
                    total_amount=0,
                    avg_daily=0,
                    score=40,
                ),
            ],
            total_score=40,
            price_change_20d=0.02,
            inst_total=5e8,
            foreign_total=3e8,
        )

    def test_no_bold(self):
        msg = format_pension_alert(self._make_detection(), "국민연금")
        assert "**" not in msg

    def test_contains_entity_name(self):
        msg = format_pension_alert(self._make_detection(), "국민연금")
        assert "국민연금" in msg
        assert "주호님" in msg
