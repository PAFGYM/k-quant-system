"""Tests for the policy/political event calendar engine."""

from __future__ import annotations

from datetime import date

import pytest

from kstock.signal.policy_engine import (
    get_active_events,
    get_adjustments,
    get_score_bonus,
    get_telegram_summary,
    has_bullish_policy,
)


@pytest.fixture
def single_event_config() -> dict:
    """Config with a single bullish sector event."""
    return {
        "events": [
            {
                "name": "AI 반도체 지원 정책",
                "start": "2026-02-01",
                "end": "2026-02-28",
                "effect": "bullish_sector",
                "description": "정부 AI 반도체 투자 확대",
                "target_sectors": ["반도체", "AI"],
                "adjustments": {
                    "sector_bonus": 7,
                    "momentum_weight": 1.3,
                },
            },
        ],
        "leading_sectors": {"tier1": [], "tier2": []},
    }


@pytest.fixture
def multi_event_config() -> dict:
    """Config with multiple overlapping events."""
    return {
        "events": [
            {
                "name": "총선 일정",
                "start": "2026-03-01",
                "end": "2026-04-15",
                "effect": "cautious",
                "description": "총선 관련 불확실성",
                "adjustments": {
                    "leverage_etf_ok": False,
                    "cash_min_pct": 20,
                    "momentum_weight": 1.4,
                },
            },
            {
                "name": "코스닥 활성화 정책",
                "start": "2026-03-10",
                "end": "2026-05-30",
                "effect": "bullish_kosdaq",
                "description": "코스닥 벤처기업 지원 확대",
                "adjustments": {
                    "kosdaq_bonus": 5,
                    "cash_min_pct": 15,
                    "momentum_weight": 1.2,
                },
            },
        ],
        "leading_sectors": {
            "tier1": ["반도체", "2차전지"],
            "tier2": ["바이오", "자동차"],
        },
    }


@pytest.fixture
def empty_config() -> dict:
    """Config with no events and no leading sectors."""
    return {
        "events": [],
        "leading_sectors": {"tier1": [], "tier2": []},
    }


# --- get_active_events ---


class TestGetActiveEvents:
    def test_returns_events_within_date_range(self, single_event_config: dict) -> None:
        """Events whose start <= today <= end should be returned."""
        result = get_active_events(date(2026, 2, 15), single_event_config)
        assert len(result) == 1
        assert result[0]["name"] == "AI 반도체 지원 정책"

    def test_returns_events_on_start_date(self, single_event_config: dict) -> None:
        """Event should be active on its start date (inclusive)."""
        result = get_active_events(date(2026, 2, 1), single_event_config)
        assert len(result) == 1

    def test_returns_events_on_end_date(self, single_event_config: dict) -> None:
        """Event should be active on its end date (inclusive)."""
        result = get_active_events(date(2026, 2, 28), single_event_config)
        assert len(result) == 1

    def test_returns_empty_list_before_start(self, single_event_config: dict) -> None:
        """No events should be returned before the event start date."""
        result = get_active_events(date(2026, 1, 31), single_event_config)
        assert result == []

    def test_returns_empty_list_after_end(self, single_event_config: dict) -> None:
        """No events should be returned after the event end date."""
        result = get_active_events(date(2026, 3, 1), single_event_config)
        assert result == []

    def test_empty_config_returns_empty_list(self, empty_config: dict) -> None:
        """Empty events list in config returns empty list."""
        result = get_active_events(date(2026, 2, 15), empty_config)
        assert result == []

    def test_multiple_overlapping_events(self, multi_event_config: dict) -> None:
        """When multiple events overlap on a date, all should be returned."""
        result = get_active_events(date(2026, 3, 15), multi_event_config)
        assert len(result) == 2
        names = {ev["name"] for ev in result}
        assert names == {"총선 일정", "코스닥 활성화 정책"}


# --- get_adjustments ---


class TestGetAdjustments:
    def test_single_event_adjustments(self, single_event_config: dict) -> None:
        """Adjustments from a single event pass through directly."""
        result = get_adjustments(date(2026, 2, 15), single_event_config)
        assert result["sector_bonus"] == 7
        assert result["momentum_weight"] == 1.3

    def test_merges_overlapping_events(self, multi_event_config: dict) -> None:
        """Overlapping events should have their adjustments merged."""
        result = get_adjustments(date(2026, 3, 15), multi_event_config)
        # bool merging: and
        assert result["leverage_etf_ok"] is False
        # _pct suffix: takes max
        assert result["cash_min_pct"] == 20
        # _weight suffix: takes max
        assert result["momentum_weight"] == 1.4
        # kosdaq_bonus only in second event, appears directly
        assert result["kosdaq_bonus"] == 5

    def test_no_active_events_returns_empty(self, multi_event_config: dict) -> None:
        """No active events should produce an empty adjustments dict."""
        result = get_adjustments(date(2026, 1, 1), multi_event_config)
        assert result == {}

    def test_empty_config_returns_empty(self, empty_config: dict) -> None:
        """Empty config should return empty adjustments dict."""
        result = get_adjustments(date(2026, 3, 15), empty_config)
        assert result == {}


# --- get_score_bonus ---


class TestGetScoreBonus:
    def test_sector_bonus_for_matching_sector(self, single_event_config: dict) -> None:
        """Ticker in a target sector of a bullish_sector event gets sector_bonus."""
        bonus = get_score_bonus(
            "005930", sector="반도체", market="KOSPI",
            today=date(2026, 2, 15), config=single_event_config,
        )
        assert bonus == 7

    def test_no_bonus_for_non_matching_sector(self, single_event_config: dict) -> None:
        """Ticker not in target sectors gets no sector bonus."""
        bonus = get_score_bonus(
            "000660", sector="자동차", market="KOSPI",
            today=date(2026, 2, 15), config=single_event_config,
        )
        assert bonus == 0

    def test_kosdaq_bonus(self, multi_event_config: dict) -> None:
        """KOSDAQ ticker gets kosdaq_bonus from bullish_kosdaq event."""
        bonus = get_score_bonus(
            "293490", sector="IT", market="KOSDAQ",
            today=date(2026, 3, 15), config=multi_event_config,
        )
        # kosdaq_bonus=5, IT is not in leading sectors
        assert bonus == 5

    def test_kosdaq_bonus_lowercase_market(self, multi_event_config: dict) -> None:
        """KOSDAQ bonus should work with lowercase 'kosdaq' market value."""
        bonus = get_score_bonus(
            "293490", sector="IT", market="kosdaq",
            today=date(2026, 3, 15), config=multi_event_config,
        )
        assert bonus == 5

    def test_leading_sector_tier1_bonus(self, multi_event_config: dict) -> None:
        """Tier1 leading sector adds +5 bonus."""
        bonus = get_score_bonus(
            "005930", sector="반도체", market="KOSPI",
            today=date(2026, 1, 1), config=multi_event_config,
        )
        # No active events on 2026-01-01, but leading_sectors tier1 still applies
        assert bonus == 5

    def test_leading_sector_tier2_bonus(self, multi_event_config: dict) -> None:
        """Tier2 leading sector adds +2 bonus."""
        bonus = get_score_bonus(
            "068270", sector="바이오", market="KOSPI",
            today=date(2026, 1, 1), config=multi_event_config,
        )
        assert bonus == 2

    def test_bonus_caps_at_20(self) -> None:
        """Total bonus should be capped at 20 regardless of how many sources."""
        config = {
            "events": [
                {
                    "name": "대형 정책 1",
                    "start": "2026-01-01",
                    "end": "2026-12-31",
                    "effect": "bullish_sector",
                    "target_sectors": ["반도체"],
                    "adjustments": {"sector_bonus": 12},
                },
                {
                    "name": "대형 정책 2",
                    "start": "2026-01-01",
                    "end": "2026-12-31",
                    "effect": "bullish_sector",
                    "target_sectors": ["반도체"],
                    "adjustments": {"sector_bonus": 10},
                },
            ],
            "leading_sectors": {"tier1": ["반도체"], "tier2": []},
        }
        bonus = get_score_bonus(
            "005930", sector="반도체", market="KOSPI",
            today=date(2026, 6, 15), config=config,
        )
        # 12 + 10 + 5 (tier1) = 27 -> capped at 20
        assert bonus == 20

    def test_zero_bonus_for_empty_config(self, empty_config: dict) -> None:
        """Empty config should yield 0 bonus."""
        bonus = get_score_bonus(
            "005930", sector="반도체", market="KOSPI",
            today=date(2026, 2, 15), config=empty_config,
        )
        assert bonus == 0

    def test_kosdaq_and_leading_sector_combined(self, multi_event_config: dict) -> None:
        """KOSDAQ bonus and leading sector tier1 bonus should stack."""
        bonus = get_score_bonus(
            "373220", sector="2차전지", market="KOSDAQ",
            today=date(2026, 3, 15), config=multi_event_config,
        )
        # kosdaq_bonus=5 + tier1=5 = 10
        assert bonus == 10


# --- has_bullish_policy ---


class TestHasBullishPolicy:
    def test_returns_true_when_bullish_event_active(self, single_event_config: dict) -> None:
        """Should return True when a bullish effect event is active."""
        result = has_bullish_policy(date(2026, 2, 15), single_event_config)
        assert result is True

    def test_returns_false_when_no_events(self, empty_config: dict) -> None:
        """Should return False when no events are active."""
        result = has_bullish_policy(date(2026, 2, 15), empty_config)
        assert result is False

    def test_returns_false_when_outside_date_range(self, single_event_config: dict) -> None:
        """Should return False when outside event date range."""
        result = has_bullish_policy(date(2026, 6, 1), single_event_config)
        assert result is False

    def test_returns_true_for_bullish_kosdaq(self, multi_event_config: dict) -> None:
        """bullish_kosdaq effect should also count as bullish."""
        result = has_bullish_policy(date(2026, 5, 1), multi_event_config)
        # Only 코스닥 활성화 정책 is active (bullish_kosdaq)
        assert result is True

    def test_cautious_event_not_bullish(self) -> None:
        """Cautious effect should not make has_bullish_policy return True."""
        config = {
            "events": [
                {
                    "name": "경기침체 우려",
                    "start": "2026-06-01",
                    "end": "2026-06-30",
                    "effect": "cautious",
                    "adjustments": {},
                },
            ],
            "leading_sectors": {"tier1": [], "tier2": []},
        }
        result = has_bullish_policy(date(2026, 6, 15), config)
        assert result is False


# --- get_telegram_summary ---


class TestGetTelegramSummary:
    def test_formats_active_events(self, single_event_config: dict) -> None:
        """Active events should be formatted into a summary string."""
        result = get_telegram_summary(date(2026, 2, 15), single_event_config)
        assert result != ""
        assert "AI 반도체 지원 정책" in result
        assert "정부 AI 반도체 투자 확대" in result

    def test_returns_empty_string_when_no_events(self, empty_config: dict) -> None:
        """No active events should produce an empty string."""
        result = get_telegram_summary(date(2026, 2, 15), empty_config)
        assert result == ""

    def test_returns_empty_string_outside_dates(self, single_event_config: dict) -> None:
        """Outside event date range should produce an empty string."""
        result = get_telegram_summary(date(2026, 6, 1), single_event_config)
        assert result == ""

    def test_includes_header_line(self, single_event_config: dict) -> None:
        """Summary should start with the policy event header."""
        result = get_telegram_summary(date(2026, 2, 15), single_event_config)
        lines = result.split("\n")
        assert "정책/정치 이벤트" in lines[0]

    def test_multiple_events_all_listed(self, multi_event_config: dict) -> None:
        """All active events should appear in the summary."""
        result = get_telegram_summary(date(2026, 3, 15), multi_event_config)
        assert "총선 일정" in result
        assert "코스닥 활성화 정책" in result

    def test_adjustment_notes_leverage_etf(self, multi_event_config: dict) -> None:
        """When leverage_etf_ok is False, summary should note it."""
        result = get_telegram_summary(date(2026, 3, 15), multi_event_config)
        assert "레버리지 ETF 비추천" in result

    def test_adjustment_notes_cash_min(self, multi_event_config: dict) -> None:
        """When cash_min_pct >= 15, summary should note cash minimum."""
        result = get_telegram_summary(date(2026, 3, 15), multi_event_config)
        assert "현금 최소" in result

    def test_adjustment_notes_momentum(self, single_event_config: dict) -> None:
        """When momentum_weight >= 1.3, summary should note momentum boost."""
        result = get_telegram_summary(date(2026, 2, 15), single_event_config)
        assert "모멘텀 강화" in result

    def test_bullish_event_uses_green_emoji(self, single_event_config: dict) -> None:
        """Bullish sector events should use the green circle emoji."""
        result = get_telegram_summary(date(2026, 2, 15), single_event_config)
        assert "\U0001f7e2" in result  # green circle


# --- default config loading ---


class TestDefaultConfigLoading:
    def test_load_config_returns_fallback_when_no_file(self) -> None:
        """_load_config with nonexistent path returns safe defaults."""
        from kstock.signal.policy_engine import _load_config
        from pathlib import Path

        result = _load_config(Path("/nonexistent/path/to/config.yaml"))
        assert "events" in result
        assert result["events"] == []
        assert "leading_sectors" in result

    def test_get_active_events_with_no_config_no_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_active_events without config arg falls back to _load_config."""
        from kstock.signal import policy_engine

        def mock_load_config(path=None):
            return {"events": [], "leading_sectors": {"tier1": [], "tier2": []}}

        monkeypatch.setattr(policy_engine, "_load_config", mock_load_config)
        result = get_active_events(date(2026, 2, 15))
        assert result == []
