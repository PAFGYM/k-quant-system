"""Tests for kstock.bot.morning_briefing (Section 65 - morning briefing)."""

from __future__ import annotations

import pytest

from kstock.bot.morning_briefing import (
    format_market_summary_line,
    format_weekly_macro_preview,
    generate_morning_briefing,
)


# ---------------------------------------------------------------------------
# generate_morning_briefing
# ---------------------------------------------------------------------------


class TestGenerateMorningBriefing:
    def test_contains_juho(self) -> None:
        msg = generate_morning_briefing()
        assert "주호님" in msg

    def test_contains_good_morning(self) -> None:
        msg = generate_morning_briefing()
        assert "좋은 아침" in msg

    def test_no_bold(self) -> None:
        msg = generate_morning_briefing()
        assert "**" not in msg

    def test_empty_inputs_still_valid(self) -> None:
        msg = generate_morning_briefing()
        assert len(msg) > 0
        assert "주호님" in msg
        assert "좋은 아침" in msg
        assert "데이터 수집 중" in msg

    def test_with_market_data_contains_sp500(self) -> None:
        market = {
            "sp500_pct": 1.2,
            "nasdaq_pct": 0.8,
            "usdkrw": 1310.0,
            "usdkrw_change": 5.0,
        }
        msg = generate_morning_briefing(market_data=market)
        assert "S&P500" in msg

    def test_with_market_data_contains_usdkrw(self) -> None:
        market = {"usdkrw": 1310.0, "usdkrw_change": -3.0}
        msg = generate_morning_briefing(market_data=market)
        assert "원/달러" in msg

    def test_with_events_contains_event_name(self) -> None:
        events = [
            {"name": "FOMC 회의록", "description": "연준 금리 결정", "importance": "높음"},
        ]
        msg = generate_morning_briefing(events=events)
        assert "FOMC 회의록" in msg

    def test_with_events_high_importance_prefix(self) -> None:
        events = [
            {"name": "CPI 발표", "description": "소비자물가지수", "importance": "높음"},
        ]
        msg = generate_morning_briefing(events=events)
        assert "!CPI 발표" in msg

    def test_with_holdings_premarket(self) -> None:
        holdings = [
            {"name": "삼성전자", "premarket_pct": 1.5, "premarket_price": 72000},
        ]
        msg = generate_morning_briefing(holdings_premarket=holdings)
        assert "삼성전자" in msg
        assert "시간외" in msg

    def test_with_recent_reports(self) -> None:
        reports = [
            {"broker": "미래에셋증권", "title": "반도체 산업 전망", "ticker": "005930"},
        ]
        msg = generate_morning_briefing(recent_reports=reports)
        assert "미래에셋증권" in msg

    def test_with_strategy_note(self) -> None:
        note = "오늘은 반도체 섹터에 집중하세요"
        msg = generate_morning_briefing(strategy_note=note)
        assert note in msg
        assert "오늘의 전략" in msg

    def test_ends_with_closing(self) -> None:
        msg = generate_morning_briefing()
        assert "좋은 하루" in msg

    def test_full_briefing_no_bold(self) -> None:
        market = {"sp500_pct": 0.5, "nasdaq_pct": -0.3, "usdkrw": 1320.0, "usdkrw_change": 2.0}
        events = [{"name": "ISM 제조업", "description": "미국 제조업 지수", "importance": "보통"}]
        holdings = [{"name": "에코프로", "premarket_pct": -2.0, "premarket_price": 160000}]
        reports = [{"broker": "NH투자", "title": "2차전지 전망"}]
        msg = generate_morning_briefing(
            market_data=market,
            events=events,
            holdings_premarket=holdings,
            recent_reports=reports,
            strategy_note="관망 추천",
        )
        assert "**" not in msg

    def test_shanghai_and_nikkei(self) -> None:
        market = {"shanghai_pct": -0.5, "nikkei_pct": 1.2}
        msg = generate_morning_briefing(market_data=market)
        assert "상해" in msg
        assert "니케이" in msg


# ---------------------------------------------------------------------------
# format_weekly_macro_preview
# ---------------------------------------------------------------------------


class TestFormatWeeklyMacroPreview:
    def test_contains_weekly_header(self) -> None:
        msg = format_weekly_macro_preview([])
        assert "주간 매크로" in msg

    def test_no_bold(self) -> None:
        events = [{"day": "월", "name": "ISM", "importance": "보통"}]
        msg = format_weekly_macro_preview(events)
        assert "**" not in msg

    def test_empty_events(self) -> None:
        msg = format_weekly_macro_preview([])
        assert "주요 경제 이벤트 없음" in msg

    def test_with_events(self) -> None:
        events = [
            {"day": "수", "name": "FOMC 금리 결정", "importance": "높음"},
            {"day": "금", "name": "고용보고서", "importance": "보통"},
        ]
        msg = format_weekly_macro_preview(events, week_range="02/17~02/21")
        assert "FOMC 금리 결정" in msg
        assert "02/17~02/21" in msg

    def test_high_importance_suffix(self) -> None:
        events = [{"day": "수", "name": "CPI", "importance": "높음"}]
        msg = format_weekly_macro_preview(events)
        assert "가장 중요" in msg

    def test_juho_focus_event(self) -> None:
        events = [{"day": "목", "name": "PCE 물가지수", "importance": "높음"}]
        msg = format_weekly_macro_preview(events)
        assert "주호님" in msg
        assert "PCE 물가지수" in msg

    def test_custom_week_range(self) -> None:
        msg = format_weekly_macro_preview([], week_range="03/01~03/05")
        assert "03/01~03/05" in msg


# ---------------------------------------------------------------------------
# format_market_summary_line
# ---------------------------------------------------------------------------


class TestFormatMarketSummaryLine:
    def test_no_data_returns_empty(self) -> None:
        assert format_market_summary_line(None) == ""

    def test_with_data_contains_sp(self) -> None:
        result = format_market_summary_line({"sp500_pct": 0.5, "nasdaq_pct": -0.2, "usdkrw": 1310})
        assert "S&P" in result
        assert "NQ" in result
        assert "환율" in result
