"""Tests for the report submenu feature."""

from __future__ import annotations

import pytest

from kstock.store.sqlite import SQLiteStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(db_path=tmp_path / "test.db")


@pytest.fixture
def store_with_reports(store):
    """Store pre-populated with sample reports."""
    for i in range(3):
        store.add_report(
            source="naver",
            title=f"에코프로 목표가 상향 레포트 {i}",
            broker=f"증권사{i}",
            ticker="086520",
            target_price=200000 + i * 10000,
            prev_target_price=190000,
            opinion="매수",
            date=f"2026-02-2{i}",
        )
    store.add_report(
        source="naver",
        title="현대차 밸류업 수혜 분석",
        broker="한국투자증권",
        ticker="005380",
        target_price=580000,
        prev_target_price=580000,
        opinion="매수",
        date="2026-02-23",
    )
    # Target downgrade
    store.add_report(
        source="naver",
        title="XX화학 하향 조정",
        broker="삼성증권",
        ticker="000000",
        target_price=30000,
        prev_target_price=50000,
        opinion="중립",
        date="2026-02-22",
    )
    # Sector report
    store.add_report(
        source="naver",
        title="2차전지 소재 2026년 전망",
        broker="미래에셋",
        ticker="",
        target_price=0,
        prev_target_price=0,
        opinion="",
        date="2026-02-23",
    )
    return store


# ---------------------------------------------------------------------------
# Report submenu options
# ---------------------------------------------------------------------------

class TestReportSubmenuOptions:
    """Verify 6 submenu options exist in the bot."""

    def test_bot_has_report_submenu_method(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_action_report_submenu")

    def test_bot_has_sector_report_method(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_action_sector_report")

    def test_sector_keywords_defined(self):
        from kstock.bot.bot import KQuantBot
        sectors = KQuantBot.SECTOR_KEYWORDS
        assert "2차전지" in sectors
        assert "반도체" in sectors
        assert "자동차" in sectors
        assert "AI/로봇" in sectors
        assert "방산/조선" in sectors


# ---------------------------------------------------------------------------
# DB report query methods
# ---------------------------------------------------------------------------

class TestReportQueryMethods:
    """Verify report query methods on SQLiteStore."""

    def test_get_reports_for_tickers(self, store_with_reports):
        reports = store_with_reports.get_reports_for_tickers(["086520"], limit=5)
        assert len(reports) == 3
        for r in reports:
            assert r["ticker"] == "086520"

    def test_get_reports_for_tickers_empty(self, store):
        reports = store.get_reports_for_tickers([], limit=5)
        assert reports == []

    def test_get_reports_target_upgrades(self, store_with_reports):
        reports = store_with_reports.get_reports_target_upgrades(days=7, limit=10)
        # 3 에코프로 reports have target > prev_target
        assert len(reports) >= 3
        for r in reports:
            assert r["target_price"] > r["prev_target_price"]

    def test_get_reports_target_downgrades(self, store_with_reports):
        reports = store_with_reports.get_reports_target_downgrades(days=7, limit=10)
        assert len(reports) >= 1
        for r in reports:
            assert r["target_price"] < r["prev_target_price"]

    def test_get_reports_by_sector(self, store_with_reports):
        reports = store_with_reports.get_reports_by_sector(["2차전지"], limit=5)
        assert len(reports) >= 1
        assert "2차전지" in reports[0]["title"]

    def test_get_reports_by_sector_empty(self, store):
        reports = store.get_reports_by_sector([], limit=5)
        assert reports == []

    def test_get_reports_today(self, store_with_reports):
        # Today's date in the test is the current UTC date
        # Our fixture uses 2026-02-23 which may or may not be "today"
        # We add a report with today's date explicitly
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        store_with_reports.add_report(
            source="test", title="오늘 리포트", broker="테스트증권",
            ticker="", date=today,
        )
        reports = store_with_reports.get_reports_today(limit=10)
        assert len(reports) >= 1


# ---------------------------------------------------------------------------
# Report format
# ---------------------------------------------------------------------------

class TestReportFormat:
    """Verify report formatting has no bold markers."""

    def test_format_report_item_no_bold(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        report = {
            "broker": "NH투자증권",
            "date": "2026.02.24",
            "title": "에코프로 - HBM4 양산 수혜 전망",
            "target_price": 230000,
            "prev_target_price": 200000,
            "opinion": "매수 유지",
            "pdf_url": "",
        }
        formatted = bot._format_report_item(report)
        assert "**" not in formatted
        assert "NH투자증권" in formatted
        assert "230,000" in formatted
        assert "200,000" in formatted
        assert "상향" in formatted

    def test_format_report_item_downgrade(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        report = {
            "broker": "삼성증권",
            "date": "2026.02.22",
            "title": "XX화학 하향 조정",
            "target_price": 30000,
            "prev_target_price": 50000,
            "opinion": "중립",
            "pdf_url": "",
        }
        formatted = bot._format_report_item(report)
        assert "하향" in formatted
        assert "**" not in formatted

    def test_format_report_item_no_prev_target(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        report = {
            "broker": "미래에셋",
            "date": "2026.02.23",
            "title": "2차전지 전망",
            "target_price": 100000,
            "prev_target_price": 0,
            "opinion": "",
            "pdf_url": "",
        }
        formatted = bot._format_report_item(report)
        assert "100,000" in formatted
        assert "상향" not in formatted  # No direction without prev_target


# ---------------------------------------------------------------------------
# Sector submenu
# ---------------------------------------------------------------------------

class TestSectorSubmenu:
    """Verify sector submenu has correct sectors."""

    def test_five_sectors(self):
        from kstock.bot.bot import KQuantBot
        assert len(KQuantBot.SECTOR_KEYWORDS) == 5

    def test_sector_keywords_are_lists(self):
        from kstock.bot.bot import KQuantBot
        for sector, keywords in KQuantBot.SECTOR_KEYWORDS.items():
            assert isinstance(keywords, list)
            assert len(keywords) >= 2, f"{sector} should have at least 2 keywords"
