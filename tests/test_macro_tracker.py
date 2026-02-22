"""Tests for kstock.signal.macro_tracker module."""

from __future__ import annotations

from kstock.signal.macro_tracker import (
    MacroEvent,
    MacroCalendar,
    build_weekly_calendar,
    get_market_impact,
    format_macro_calendar,
    format_macro_event_alert,
)


class TestMacroEvent:
    def test_dataclass(self):
        e = MacroEvent(
            date="2026-02-24", name="FOMC 회의",
            country="미국", importance="높음",
            description="연준 금리 결정",
        )
        assert e.country == "미국"
        assert e.importance == "높음"


class TestBuildWeeklyCalendar:
    def test_with_events(self):
        events = [
            {"date": "2026-02-24", "name": "FOMC", "country": "미국", "importance": "높음", "description": "금리"},
            {"date": "2026-02-25", "name": "CPI", "country": "한국", "importance": "보통", "description": "물가"},
        ]
        cal = build_weekly_calendar(events, week_start="2026-02-24")
        assert isinstance(cal, MacroCalendar)
        assert len(cal.events) >= 1

    def test_empty_events(self):
        cal = build_weekly_calendar([], week_start="2026-02-24")
        assert isinstance(cal, MacroCalendar)
        assert len(cal.events) == 0

    def test_key_focus_set(self):
        events = [
            {"date": "2026-02-24", "name": "관세 발효", "country": "미국", "importance": "높음", "description": ""},
        ]
        cal = build_weekly_calendar(events, week_start="2026-02-24")
        assert cal.key_focus != ""


class TestGetMarketImpact:
    def test_high_importance(self):
        e = MacroEvent(date="2026-02-24", name="FOMC", country="미국",
                       importance="높음", description="금리 결정")
        result = get_market_impact(e)
        assert "direction" in result
        assert "magnitude" in result

    def test_low_importance(self):
        e = MacroEvent(date="2026-02-24", name="건축허가",
                       country="미국", importance="낮음", description="")
        result = get_market_impact(e)
        assert isinstance(result, dict)


class TestFormatMacroCalendar:
    def test_with_events(self):
        events = [
            MacroEvent(date="2026-02-24", name="관세 발효", country="미국",
                       importance="높음", description="15% 글로벌 관세"),
        ]
        cal = MacroCalendar(
            week_start="2026-02-24", week_end="2026-02-28",
            events=events, key_focus="관세 발효", message="",
        )
        result = format_macro_calendar(cal)
        assert "**" not in result
        assert "주간" in result or "매크로" in result or "캘린더" in result

    def test_empty_calendar(self):
        cal = MacroCalendar(
            week_start="2026-02-24", week_end="2026-02-28",
            events=[], key_focus="", message="",
        )
        result = format_macro_calendar(cal)
        assert isinstance(result, str)

    def test_contains_juhonim(self):
        events = [
            MacroEvent(date="2026-02-24", name="FOMC", country="미국",
                       importance="높음", description="금리"),
        ]
        cal = MacroCalendar(
            week_start="2026-02-24", week_end="2026-02-28",
            events=events, key_focus="FOMC", message="",
        )
        result = format_macro_calendar(cal)
        assert "주호님" in result


class TestFormatMacroEventAlert:
    def test_basic_format(self):
        e = MacroEvent(date="2026-02-24", name="CPI", country="미국",
                       importance="높음", description="소비자물가")
        result = format_macro_event_alert(e, actual_value="3.1%", expected="3.0%")
        assert "**" not in result
        assert "CPI" in result

    def test_without_values(self):
        e = MacroEvent(date="2026-02-24", name="GDP", country="한국",
                       importance="보통", description="경제성장률")
        result = format_macro_event_alert(e)
        assert isinstance(result, str)
