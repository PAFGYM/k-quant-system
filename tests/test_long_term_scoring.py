"""Tests for long-term investment scoring."""

from __future__ import annotations

from kstock.signal.long_term_scoring import compute_long_term_score, GROWTH_SECTORS


class TestLongTermScore:
    def test_high_dividend_value(self):
        score = compute_long_term_score(
            dividend_yield=4.0, pbr=0.8, roe=15,
            debt_ratio=50, sector="금융",
        )
        assert score.total >= 70
        assert score.grade in ("A+", "A")
        assert "적립식" in score.monthly_recommendation

    def test_low_quality_stock(self):
        score = compute_long_term_score(
            dividend_yield=0.5, pbr=3.0, roe=3,
            debt_ratio=250, sector="",
        )
        assert score.total < 50
        assert score.grade in ("C", "D")

    def test_growth_sector_bonus(self):
        score_growth = compute_long_term_score(
            dividend_yield=2.0, pbr=1.5, roe=10,
            debt_ratio=100, sector="반도체",
        )
        score_other = compute_long_term_score(
            dividend_yield=2.0, pbr=1.5, roe=10,
            debt_ratio=100, sector="기타",
        )
        assert score_growth.total > score_other.total

    def test_etf_mode(self):
        score = compute_long_term_score(
            dividend_yield=3.5, pbr=0, roe=0,
            debt_ratio=0, sector="", is_etf=True,
        )
        assert score.total > 40

    def test_score_bounds(self):
        score = compute_long_term_score(
            dividend_yield=5.0, pbr=0.3, roe=20,
            debt_ratio=30, sector="반도체",
        )
        assert 0 <= score.total <= 100

    def test_grades(self):
        high = compute_long_term_score(
            dividend_yield=5, pbr=0.5, roe=18,
            debt_ratio=40, sector="반도체",
        )
        assert high.grade == "A+"

    def test_growth_sectors_list(self):
        assert "반도체" in GROWTH_SECTORS
        assert "바이오" in GROWTH_SECTORS
        assert "2차전지" in GROWTH_SECTORS
