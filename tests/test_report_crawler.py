"""Tests for kstock.signal.report_crawler (Section 47 - broker report management)."""

from __future__ import annotations

import pytest

from kstock.signal.report_crawler import (
    BrokerReport,
    classify_alert_level,
    compute_target_change_pct,
    format_report_alert,
    is_duplicate,
    parse_opinion_change,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(**overrides) -> BrokerReport:
    """Create a BrokerReport with sensible defaults, overridable."""
    defaults = dict(
        source="naver",
        title="삼성전자 실적 분석",
        broker="미래에셋증권",
        ticker="005930",
        target_price=90000.0,
        prev_target_price=80000.0,
        opinion="매수",
        prev_opinion="매수",
        date="2026-02-22",
        pdf_url="https://example.com/report.pdf",
        summary="반도체 수요 회복에 따른 실적 개선 전망",
    )
    defaults.update(overrides)
    return BrokerReport(**defaults)


# ---------------------------------------------------------------------------
# BrokerReport dataclass
# ---------------------------------------------------------------------------


class TestBrokerReportDataclass:
    def test_creation_with_all_fields(self) -> None:
        report = _make_report()
        assert report.source == "naver"
        assert report.ticker == "005930"
        assert report.target_price == 90000.0

    def test_creation_with_custom_fields(self) -> None:
        report = _make_report(ticker="035720", broker="NH투자증권", opinion="중립")
        assert report.ticker == "035720"
        assert report.broker == "NH투자증권"
        assert report.opinion == "중립"


# ---------------------------------------------------------------------------
# is_duplicate
# ---------------------------------------------------------------------------


class TestIsDuplicate:
    def test_exact_match_is_duplicate(self) -> None:
        report = _make_report()
        existing = [{"title": "삼성전자 실적 분석", "broker": "미래에셋증권", "date": "2026-02-22"}]
        assert is_duplicate(report, existing) is True

    def test_different_title_not_duplicate(self) -> None:
        report = _make_report(title="SK하이닉스 분석")
        existing = [{"title": "삼성전자 실적 분석", "broker": "미래에셋증권", "date": "2026-02-22"}]
        assert is_duplicate(report, existing) is False

    def test_different_broker_not_duplicate(self) -> None:
        report = _make_report(broker="한국투자증권")
        existing = [{"title": "삼성전자 실적 분석", "broker": "미래에셋증권", "date": "2026-02-22"}]
        assert is_duplicate(report, existing) is False

    def test_different_date_not_duplicate(self) -> None:
        report = _make_report(date="2026-02-21")
        existing = [{"title": "삼성전자 실적 분석", "broker": "미래에셋증권", "date": "2026-02-22"}]
        assert is_duplicate(report, existing) is False

    def test_empty_existing_not_duplicate(self) -> None:
        report = _make_report()
        assert is_duplicate(report, []) is False


# ---------------------------------------------------------------------------
# parse_opinion_change
# ---------------------------------------------------------------------------


class TestParseOpinionChange:
    def test_downgrade_buy_to_neutral(self) -> None:
        assert parse_opinion_change("매수", "중립") == "매수->중립"

    def test_upgrade_neutral_to_buy(self) -> None:
        assert parse_opinion_change("중립", "매수") == "중립->매수"

    def test_same_opinion_returns_empty(self) -> None:
        assert parse_opinion_change("매수", "매수") == ""

    def test_empty_prev_returns_empty(self) -> None:
        assert parse_opinion_change("", "매수") == ""

    def test_empty_current_returns_empty(self) -> None:
        assert parse_opinion_change("매수", "") == ""

    def test_whitespace_stripped(self) -> None:
        assert parse_opinion_change(" 매수 ", " 중립 ") == "매수->중립"


# ---------------------------------------------------------------------------
# compute_target_change_pct
# ---------------------------------------------------------------------------


class TestComputeTargetChangePct:
    def test_positive_change(self) -> None:
        result = compute_target_change_pct(80000, 90000)
        assert result == 12.5

    def test_negative_change(self) -> None:
        result = compute_target_change_pct(100000, 80000)
        assert result == -20.0

    def test_zero_prev_returns_zero(self) -> None:
        assert compute_target_change_pct(0, 90000) == 0.0

    def test_negative_prev_returns_zero(self) -> None:
        assert compute_target_change_pct(-1000, 90000) == 0.0

    def test_no_change(self) -> None:
        assert compute_target_change_pct(80000, 80000) == 0.0


# ---------------------------------------------------------------------------
# classify_alert_level
# ---------------------------------------------------------------------------


class TestClassifyAlertLevel:
    def test_urgent_portfolio_with_large_target_change(self) -> None:
        """Portfolio ticker + target change >= 20% -> 긴급."""
        report = _make_report(
            ticker="005930",
            target_price=100000,
            prev_target_price=80000,
        )
        level = classify_alert_level(
            report,
            portfolio_tickers=["005930"],
            tenbagger_tickers=[],
            watch_sectors=[],
        )
        assert level == "긴급"

    def test_urgent_portfolio_with_opinion_downgrade(self) -> None:
        """Portfolio ticker + opinion downgrade -> 긴급."""
        report = _make_report(
            ticker="005930",
            prev_opinion="매수",
            opinion="중립",
            target_price=80000,
            prev_target_price=80000,
        )
        level = classify_alert_level(
            report,
            portfolio_tickers=["005930"],
            tenbagger_tickers=[],
            watch_sectors=[],
        )
        assert level == "긴급"

    def test_important_portfolio_new_report(self) -> None:
        """Portfolio ticker without big change -> 중요."""
        report = _make_report(
            ticker="005930",
            target_price=82000,
            prev_target_price=80000,
        )
        level = classify_alert_level(
            report,
            portfolio_tickers=["005930"],
            tenbagger_tickers=[],
            watch_sectors=[],
        )
        assert level == "중요"

    def test_important_tenbagger_ticker(self) -> None:
        """Tenbagger watchlist ticker -> 중요."""
        report = _make_report(ticker="035720")
        level = classify_alert_level(
            report,
            portfolio_tickers=[],
            tenbagger_tickers=["035720"],
            watch_sectors=[],
        )
        assert level == "중요"

    def test_reference_sector_match_in_title(self) -> None:
        """Watch sector keyword in title -> 참고."""
        report = _make_report(ticker="999999", title="반도체 산업 전망")
        level = classify_alert_level(
            report,
            portfolio_tickers=[],
            tenbagger_tickers=[],
            watch_sectors=["반도체"],
        )
        assert level == "참고"

    def test_reference_sector_match_in_summary(self) -> None:
        """Watch sector keyword in summary -> 참고."""
        report = _make_report(
            ticker="999999",
            title="일반 제목",
            summary="2차전지 시장 전망 분석",
        )
        level = classify_alert_level(
            report,
            portfolio_tickers=[],
            tenbagger_tickers=[],
            watch_sectors=["2차전지"],
        )
        assert level == "참고"

    def test_empty_when_not_relevant(self) -> None:
        """No match at all -> empty string."""
        report = _make_report(ticker="999999", title="무관한 리포트", summary="무관")
        level = classify_alert_level(
            report,
            portfolio_tickers=["005930"],
            tenbagger_tickers=["035720"],
            watch_sectors=["반도체"],
        )
        assert level == ""


# ---------------------------------------------------------------------------
# format_report_alert
# ---------------------------------------------------------------------------


class TestFormatReportAlert:
    def test_urgent_contains_ticker(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "긴급")
        assert "005930" in msg

    def test_urgent_no_bold(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "긴급")
        assert "**" not in msg

    def test_urgent_contains_alert_level(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "긴급")
        assert "긴급" in msg

    def test_urgent_contains_juho(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "긴급")
        assert "주호님" in msg

    def test_important_contains_juho(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "중요")
        assert "주호님" in msg

    def test_reference_format(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "참고")
        assert "참고" in msg
        assert "**" not in msg

    def test_opinion_change_shown(self) -> None:
        report = _make_report(prev_opinion="매수", opinion="중립")
        msg = format_report_alert(report, "긴급")
        assert "하향" in msg

    def test_holding_profit_shown(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "중요", holding_profit_pct=15.3)
        assert "+15.3%" in msg

    def test_pdf_url_shown(self) -> None:
        report = _make_report()
        msg = format_report_alert(report, "참고")
        assert "https://example.com/report.pdf" in msg
