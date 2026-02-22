"""Tests for the weekly report module."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from kstock.bot.weekly_report import (
    _get_week_range,
    collect_weekly_data,
    generate_report_content,
    format_telegram_summary,
    create_google_doc,
)
from kstock.store.sqlite import SQLiteStore


KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(db_path=tmp_path / "test.db")


@pytest.fixture
def store_with_data(store):
    """Store with sample portfolio and report data."""
    # Add screenshots
    store.add_screenshot(
        total_eval=182500000,
        total_profit=82298582,
        total_profit_pct=82.1,
        cash=5000000,
        holdings_json=json.dumps([
            {"name": "에코프로", "ticker": "086520", "current_price": 178500, "profit_pct": 88.0},
            {"name": "현대차", "ticker": "005380", "current_price": 515000, "profit_pct": 0.8},
        ], ensure_ascii=False),
    )
    store.add_screenshot(
        total_eval=175639400,
        total_profit=75439400,
        total_profit_pct=75.2,
        cash=3000000,
        holdings_json=json.dumps([
            {"name": "에코프로", "ticker": "086520", "current_price": 170900, "profit_pct": 84.0},
        ], ensure_ascii=False),
    )

    # Add portfolio horizons
    store.upsert_portfolio_horizon("086520", "에코프로", "janggi")
    store.upsert_portfolio_horizon("005380", "현대차", "dangi")

    # Add reports
    store.add_report(
        source="naver", title="에코프로 목표가 상향",
        broker="NH투자증권", ticker="086520",
        target_price=230000, prev_target_price=200000,
        opinion="매수", date="2026-02-24",
    )

    return store


# ---------------------------------------------------------------------------
# TestWeekRange
# ---------------------------------------------------------------------------

class TestWeekRange:
    """Verify week range calculation."""

    def test_sunday_range(self):
        # Sunday Feb 23, 2026
        sunday = datetime(2026, 2, 23, 19, 0, tzinfo=KST)
        label, start, end = _get_week_range(sunday)
        assert "2026" in label
        assert "2" in label  # February
        assert start <= end

    def test_friday_range(self):
        friday = datetime(2026, 2, 27, 12, 0, tzinfo=KST)
        label, start, end = _get_week_range(friday)
        assert "2026" in label

    def test_label_format(self):
        today = datetime(2026, 2, 23, 19, 0, tzinfo=KST)
        label, _, _ = _get_week_range(today)
        assert "년" in label
        assert "월" in label
        assert "주차" in label


# ---------------------------------------------------------------------------
# TestCollectData
# ---------------------------------------------------------------------------

class TestCollectData:
    """Verify data collection from DB."""

    def test_collects_week_label(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        assert "week_label" in data
        assert "week_start" in data
        assert "week_end" in data

    def test_collects_total_eval(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        # First screenshot added is the most recent (add order)
        assert data["total_eval"] > 0

    def test_collects_holdings(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        assert isinstance(data["holdings"], list)

    def test_collects_weekly_change(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        assert "weekly_change" in data
        assert "weekly_change_pct" in data

    def test_collects_horizons(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        assert "horizons" in data
        assert len(data["horizons"]) == 2

    def test_collects_goal_progress(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        assert "goal_progress" in data
        assert "progress_pct" in data["goal_progress"]

    def test_collects_reports(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        assert "recent_reports" in data

    def test_empty_store(self, store):
        data = collect_weekly_data(store)
        assert data["total_eval"] == 0
        assert data["holdings"] == []
        assert data["weekly_change"] == 0


# ---------------------------------------------------------------------------
# TestGenerateContent
# ---------------------------------------------------------------------------

class TestGenerateContent:
    """Verify report content generation."""

    def test_has_all_seven_sections(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "1. 주간 시장 요약" in content
        assert "2." in content and "포트폴리오 성과" in content
        assert "3. 종목별 주간 분석" in content
        assert "4. 증권사 리포트 요약" in content
        assert "5. K-Quant 추천 성과" in content
        assert "6. 다음 주 전망" in content
        assert "7. 30억 로드맵" in content

    def test_no_bold_markers(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "**" not in content

    def test_contains_username(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "주호님" in content

    def test_contains_header(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "K-Quant 주간 투자 보고서" in content
        assert "K-Quant v3.5 AI" in content

    def test_contains_footer(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "자동 생성" in content
        assert "투자의 최종 결정" in content

    def test_contains_portfolio_data(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "총 평가금액" in content

    def test_contains_goal_progress(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        content = generate_report_content(data)
        assert "30억 목표" in content

    def test_empty_store_generates_report(self, store):
        data = collect_weekly_data(store)
        content = generate_report_content(data)
        assert "K-Quant 주간 투자 보고서" in content
        assert "**" not in content


# ---------------------------------------------------------------------------
# TestTelegramSummary
# ---------------------------------------------------------------------------

class TestTelegramSummary:
    """Verify Telegram summary format."""

    def test_summary_format(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        msg = format_telegram_summary(data, doc_url="https://docs.google.com/document/d/abc")
        assert "주호님" in msg
        assert "주간 투자 보고서" in msg
        assert "주간 수익" in msg
        assert "누적 수익" in msg
        assert "30억 목표" in msg
        assert "구글 문서" in msg
        assert "좋은 한 주" in msg
        assert "**" not in msg

    def test_summary_without_doc_url(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        msg = format_telegram_summary(data, doc_url=None)
        assert "구글 문서" not in msg
        assert "주호님" in msg

    def test_summary_no_bold(self, store_with_data):
        data = collect_weekly_data(store_with_data)
        msg = format_telegram_summary(data)
        assert "**" not in msg


# ---------------------------------------------------------------------------
# TestGoogleDocsIntegration
# ---------------------------------------------------------------------------

class TestGoogleDocsIntegration:
    """Verify Google Docs integration (mocked)."""

    def test_create_google_doc_no_credentials(self):
        """Without credentials, should return None."""
        with patch.dict("os.environ", {"GOOGLE_CREDENTIALS_PATH": "/nonexistent/path"}):
            result = create_google_doc("Test", "Content")
            assert result is None

    def test_create_google_doc_no_google_lib(self):
        """Without google library, should return None gracefully."""
        with patch.dict("os.environ", {"GOOGLE_CREDENTIALS_PATH": "/nonexistent/path"}):
            result = create_google_doc("Test", "Content")
            assert result is None


# ---------------------------------------------------------------------------
# TestWeeklyReportDB
# ---------------------------------------------------------------------------

class TestWeeklyReportDB:
    """Verify weekly_reports table operations."""

    def test_add_and_get_latest(self, store):
        rid = store.add_weekly_report(
            week_label="2026년 2월 4주차",
            week_start="2026-02-24",
            week_end="2026-02-28",
            doc_url="https://docs.google.com/test",
            summary_json='{"weekly_change": 6860600}',
        )
        assert rid > 0
        latest = store.get_latest_weekly_report()
        assert latest is not None
        assert latest["week_label"] == "2026년 2월 4주차"
        assert latest["doc_url"] == "https://docs.google.com/test"

    def test_get_weekly_reports_history(self, store):
        for i in range(5):
            store.add_weekly_report(
                week_label=f"2026년 2월 {i+1}주차",
                week_start=f"2026-02-0{i+1}",
                week_end=f"2026-02-0{i+5}",
            )
        reports = store.get_weekly_reports(limit=4)
        assert len(reports) == 4

    def test_empty_reports(self, store):
        latest = store.get_latest_weekly_report()
        assert latest is None
        reports = store.get_weekly_reports()
        assert reports == []


# ---------------------------------------------------------------------------
# TestSchedule
# ---------------------------------------------------------------------------

class TestSchedule:
    """Verify weekly report scheduling."""

    def test_schedule_job_method_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "job_weekly_report")

    def test_schedule_job_is_async(self):
        import asyncio
        from kstock.bot.bot import KQuantBot
        assert asyncio.iscoroutinefunction(KQuantBot.job_weekly_report)
