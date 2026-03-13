"""Tests for scheduler tactical flow/short context enrichment."""

from unittest.mock import MagicMock, patch

import pandas as pd


def test_get_ticker_tactical_context_detects_flow_and_short_patterns():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_supply_demand.return_value = [
        {"foreign_net": 120_000_000, "institution_net": 90_000_000, "program_net": 10_000_000},
        {"foreign_net": 80_000_000, "institution_net": 70_000_000, "program_net": 5_000_000},
        {"foreign_net": 60_000_000, "institution_net": 40_000_000, "program_net": 0},
    ]
    mixin.db.get_short_selling.return_value = [
        {"date": "2026-03-03", "short_ratio": 11.5, "short_balance": 120_000, "short_balance_ratio": 5.5},
        {"date": "2026-03-04", "short_ratio": 11.8, "short_balance": 118_000, "short_balance_ratio": 5.4},
        {"date": "2026-03-05", "short_ratio": 12.2, "short_balance": 112_000, "short_balance_ratio": 5.2},
        {"date": "2026-03-06", "short_ratio": 12.8, "short_balance": 98_000, "short_balance_ratio": 4.8},
        {"date": "2026-03-09", "short_ratio": 12.4, "short_balance": 82_000, "short_balance_ratio": 4.2},
        {"date": "2026-03-10", "short_ratio": 11.9, "short_balance": 68_000, "short_balance_ratio": 3.7},
    ]
    mixin._ohlcv_cache = {
        "111111": pd.DataFrame(
            {
                "close": [100, 101, 100, 102, 103, 103, 104, 105, 106, 107, 108, 116],
                "volume": [1000, 980, 1020, 995, 1010, 1005, 990, 1005, 980, 995, 1000, 3200],
            }
        )
    }

    macro = MagicMock()
    macro.kospi_change_pct = -1.7

    context = SchedulerMixin._get_ticker_tactical_context(
        mixin, "111111", "테스트주", macro=macro,
    )

    assert context["flow_signal"] == "외인+기관 순유입"
    assert "short_covering" in set(context.get("short_pattern_codes") or [])
    assert context["short_timing_action"] in {"적극 매수", "매수 검토"}


def test_enrich_manager_candidates_with_flow_short_context_updates_score_and_reasons():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin._get_ticker_tactical_context = MagicMock(
        return_value={
            "flow_signal": "외인+기관 순유입",
            "short_ratio": 13.2,
            "short_pattern_codes": ["short_covering", "short_squeeze"],
            "short_pattern_labels": ["숏커버링 랠리", "숏스퀴즈"],
            "short_timing_action": "매수 검토",
        }
    )

    candidates = {
        "tenbagger": [
            {
                "ticker": "222222",
                "name": "미래주",
                "fit_score": 71.0,
                "fit_reasons": ["국내 스몰캡 핵심 구간"],
                "action_hint": "정책·산업 이벤트 전 씨앗 포지션 구축",
                "composite": 78.0,
                "confidence_score": 0.82,
            }
        ]
    }

    enriched = SchedulerMixin._enrich_manager_candidates_with_flow_short_context(
        mixin, candidates, macro=None,
    )
    candidate = enriched["tenbagger"][0]

    assert candidate["fit_score"] > 71.0
    assert "외인+기관 순유입" in candidate["fit_reasons"]
    assert "숏커버링" in candidate["fit_reasons"]
    assert "숏스퀴즈" in candidate["fit_reasons"]
    assert "숏커버" in candidate["action_hint"]


def test_enrich_manager_candidates_with_herd_context_updates_score_and_action():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    candidates = {
        "tenbagger": [
            {
                "ticker": "333333",
                "name": "매집주",
                "fit_score": 66.0,
                "fit_reasons": ["국내 스몰캡 핵심 구간"],
                "action_hint": "정책·산업 이벤트 전 씨앗 포지션 구축",
                "composite": 74.0,
                "confidence_score": 0.81,
            }
        ],
        "swing": [
            {
                "ticker": "444444",
                "name": "과열주",
                "fit_score": 61.0,
                "fit_reasons": ["거래량 280% 급증"],
                "action_hint": "반등 확인 후 2~3회 분할 진입",
                "composite": 58.0,
                "confidence_score": 0.67,
            }
        ],
    }
    herd_signal_map = {
        "333333": {
            "pattern": "세력 매집 초기",
            "danger_level": "안전",
            "score_adj": 25,
            "volume_ratio": 2.4,
            "reasons": ["기관 연속매수 4일", "거래량 2.4배"],
        },
        "444444": {
            "pattern": "리딩방 급락",
            "danger_level": "위험",
            "score_adj": -30,
            "volume_ratio": 3.8,
            "reasons": ["5일 급등 후 당일 급락", "세력 물량 떠넘기기 의심"],
        },
    }

    enriched = SchedulerMixin._enrich_manager_candidates_with_herd_context(
        mixin, candidates, herd_signal_map,
    )

    strong = enriched["tenbagger"][0]
    weak = enriched["swing"][0]

    assert strong["fit_score"] > 66.0
    assert "세력 매집 초기" in strong["fit_reasons"]
    assert "눌림 씨앗 분할" in strong["action_hint"]
    assert strong["herd_pattern"] == "세력 매집 초기"

    assert weak["fit_score"] < 61.0
    assert weak["crowd_signal"] == "리딩방 급락 경계"
    assert "리딩방 급락" in weak["fit_reasons"]
    assert "종가 회복 확인" in weak["action_hint"]


def test_backfill_profile_day_change_uses_cached_ohlcv_when_intraday_change_missing():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin._ohlcv_cache = {
        "039200": pd.DataFrame(
            {
                "date": ["2026-03-12", "2026-03-13"],
                "close": [52000, 53000],
            }
        )
    }

    profile = {
        "ticker": "039200",
        "price": 58300,
        "day_change": 0.0,
    }

    with patch("kstock.bot.mixins.scheduler.datetime") as mock_dt:
        mock_now = MagicMock()
        mock_now.date.return_value = pd.Timestamp("2026-03-13").date()
        mock_dt.now.return_value = mock_now
        day_change = SchedulerMixin._backfill_profile_day_change(mixin, profile)

    assert day_change == 12.1
