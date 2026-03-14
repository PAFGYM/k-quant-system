import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch
from types import SimpleNamespace


def _make_macro():
    macro = MagicMock()
    macro.vix = 24.5
    macro.vix_change_pct = 9.0
    macro.wti_change_pct = 4.2
    macro.usdkrw_change_pct = 0.8
    macro.koru_change_pct = -9.2
    macro.nq_futures_change_pct = -1.4
    macro.regime = "defense"
    return macro


def test_build_daily_candidate_actions_promotes_top_non_holding_picks():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin._last_scan_results = [object()]
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.0, "position": 1.0, "long_term": 1.0, "tenbagger": 1.0},
            [],
        )
    )
    mixin._enrich_manager_candidates_with_flow_short_context = MagicMock(side_effect=lambda data, macro: data)
    mixin._build_manager_fast_context = MagicMock(return_value={})
    mixin._enrich_manager_candidates_with_fast_context = MagicMock(side_effect=lambda data, fast_context: data)

    with patch(
        "kstock.bot.investment_managers.filter_discovery_candidates",
        side_effect=lambda scan_results, manager_key, exclude: {
            "position": [
                {
                    "ticker": "111111",
                    "name": "포지션주",
                    "fit_score": 82,
                    "composite": 76,
                    "confidence_score": 0.81,
                    "fit_reasons": ["실적 턴어라운드", "중기 수급 동행"],
                    "action_hint": "실적/스토리 유지 전제 1~3개월 보유",
                    "flow_signal": "외인+기관 순유입",
                },
            ],
            "tenbagger": [
                {
                    "ticker": "222222",
                    "name": "텐배거주",
                    "fit_score": 88,
                    "composite": 79,
                    "confidence_score": 0.73,
                    "fit_reasons": ["국내 스몰캡 핵심 구간", "정책 이벤트"],
                    "action_hint": "정책·산업 이벤트 전 씨앗 포지션 구축",
                    "event_tags": ["GTC"],
                },
            ],
            "swing": [
                {
                    "ticker": "333333",
                    "name": "스윙주",
                    "fit_score": 71,
                    "composite": 70,
                    "confidence_score": 0.64,
                    "fit_reasons": ["BB 하단 0.31"],
                    "action_hint": "반등 확인 후 2~3회 분할 진입",
                },
            ],
        }.get(manager_key, []),
    ):
        actions = mixin._build_daily_candidate_actions(
            holdings=[{"ticker": "999999", "name": "보유주"}],
            macro=_make_macro(),
            playbook=None,
            alert_mode="normal",
        )

    assert len(actions) >= 2
    assert actions[0]["priority"] == "opportunity"
    assert actions[0]["ticker"] == "222222"
    assert "씨앗 포지션" in actions[0]["action"]
    assert actions[0]["secondary_callback"] == "mgr_tab:tenbagger"


def test_build_daily_action_coach_lines_summarizes_market_and_top_actions():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_portfolio_snapshots.return_value = [{"total_value": 100_000_000, "cash": 18_000_000}]
    mixin.db.get_active_holdings.return_value = []
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.0, "position": 1.0, "long_term": 1.0, "tenbagger": 1.0},
            ["개인화 우선 레인: 스윙 매니저, 포지션 매니저"],
        )
    )
    mixin._build_downside_playbook = MagicMock(
        return_value=MagicMock(
            strong_stocks=[SimpleNamespace(name="강한주")],
        ),
    )

    actions = [
        {
            "priority": "urgent",
            "name": "씨에스윈드",
            "action": "비중 점검",
        },
        {
            "priority": "opportunity",
            "name": "텐배거주",
            "action": "씨앗 포지션 검토",
            "ticker": "222222",
            "manager_label": "🔟 텐베거",
        },
    ]

    dummy_memory = MagicMock(
        headline="유가+환율 쇼크 구간, 수급과 방어 업종 우선",
        attack_points=["방산", "전력/원전"],
        avoid_points=["지수 레버리지 추격", "리딩방 과열주"],
    )

    with patch("kstock.bot.mixins.scheduler.detect_regime", return_value=MagicMock(mode="defense", label="방어")), \
         patch("kstock.signal.krx_operator_memory.build_krx_operator_memory", return_value=dummy_memory):
        lines = mixin._build_daily_action_coach_lines(actions, _make_macro())

    assert any("기본 태세:" in line for line in lines)
    assert any("1순위: 씨에스윈드" in line for line in lines)
    assert any("신규 후보: 텐배거주" in line for line in lines)
    assert any("오늘 관리: 축소 1 · 추가 1 · 교체 0" in line for line in lines)
    assert any("회피:" in line for line in lines)
    assert any("현금:" in line for line in lines)
    assert any("개인화 우선 레인:" in line for line in lines)


def test_build_daily_action_coach_lines_includes_risk_window_guidance():
    from kstock.bot.mixins.scheduler import SchedulerMixin
    from kstock.signal.risk_windows import RiskWindowAssessment

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_portfolio_snapshots.return_value = [{"total_value": 100_000_000, "cash": 18_000_000}]
    mixin.db.get_active_holdings.return_value = []
    mixin._build_personalized_lane_bias = MagicMock(return_value=({}, []))
    mixin._build_downside_playbook = MagicMock(return_value=None)
    risk_window = RiskWindowAssessment(
        active=True,
        key="earnings_apr_may",
        label="4월말 실적·5월 선반영 윈도우",
        severity=3,
        coach_line="1분기 실적 재평가와 5월 리스크 선반영이 겹치는 구간입니다.",
        action_line="실적 확인 전 추격매수보다 눌림·외인수급 확인이 우선",
        scalp_multiplier=0.82,
        swing_multiplier=0.88,
        cash_floor_add=5.0,
    )
    mixin._load_daily_allocation_context = MagicMock(
        return_value={
            "cash_known": True,
            "current_cash_pct": 18.0,
            "cash_floor_pct": 20.0,
            "risk_window": risk_window,
        }
    )

    with patch("kstock.bot.mixins.scheduler.detect_regime", return_value=MagicMock(mode="defense", label="방어")), \
         patch("kstock.signal.krx_operator_memory.build_krx_operator_memory", return_value=MagicMock(headline="", attack_points=[], avoid_points=[])):
        lines = mixin._build_daily_action_coach_lines([], _make_macro())

    assert any("달력 리스크: 4월말 실적·5월 선반영 윈도우" in line for line in lines)
    assert any("실적 재평가" in line for line in lines)


def test_generate_daily_actions_adds_personalized_holding_management():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_active_holdings.return_value = [
        {
            "ticker": "083650",
            "name": "비에이치아이",
            "buy_price": 100_000,
            "current_price": 124_000,
            "eval_amount": 60_000_000,
            "quantity": 500,
            "holding_type": "position",
        },
        {
            "ticker": "017670",
            "name": "SK텔레콤",
            "buy_price": 60_000,
            "current_price": 61_500,
            "eval_amount": 6_000_000,
            "quantity": 300,
            "holding_type": "long_term",
        },
    ]
    mixin.db.get_portfolio_snapshots.return_value = [
        {"total_value": 100_000_000, "cash": 40_000_000},
    ]
    mixin._get_price = MagicMock(side_effect=lambda ticker, base_price=0: {"083650": 124_000, "017670": 61_500}.get(ticker, base_price))
    mixin._build_downside_playbook = MagicMock(return_value=None)
    mixin._load_recent_manager_scorecards = MagicMock(
        return_value={
            "position": {"weight_adj": 0.88, "avg_return_5d": -0.6},
            "long_term": {"weight_adj": 1.16, "avg_return_5d": 1.2},
        }
    )
    mixin._build_daily_candidate_actions = MagicMock(return_value=[])

    with patch("kstock.bot.mixins.scheduler.detect_regime", return_value=MagicMock(mode="neutral", label="중립", emoji="⚪")):
        actions = asyncio.run(mixin._generate_daily_actions(_make_macro()))

    assert any(a["ticker"] == "083650" and a["action"] == "분할익절 우선" for a in actions)
    assert any(a["ticker"] == "017670" and a["action"] == "추매 후보" for a in actions)


def test_generate_daily_actions_links_rotation_candidate_when_cash_tight():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_active_holdings.return_value = [
        {
            "ticker": "083650",
            "name": "비에이치아이",
            "buy_price": 100_000,
            "current_price": 92_000,
            "eval_amount": 45_000_000,
            "quantity": 500,
            "holding_type": "position",
        },
    ]
    mixin.db.get_portfolio_snapshots.return_value = [
        {"total_value": 100_000_000, "cash": 10_000_000},
    ]
    mixin._get_price = MagicMock(side_effect=lambda ticker, base_price=0: {"083650": 92_000}.get(ticker, base_price))
    mixin._build_downside_playbook = MagicMock(return_value=None)
    mixin._load_recent_manager_scorecards = MagicMock(
        return_value={
            "position": {"weight_adj": 0.82, "avg_return_5d": -1.1},
            "long_term": {"weight_adj": 1.18, "avg_return_5d": 1.7},
        }
    )
    mixin._build_daily_candidate_actions = MagicMock(
        return_value=[
            {
                "priority": "opportunity",
                "ticker": "017670",
                "name": "SK텔레콤",
                "action": "하루에 몰지 말고 2~3회 장기 분할",
                "reason": "품질 가치 · 안정 현금흐름",
                "manager_key": "long_term",
                "manager_label": "🏦 장기 가치 매니저",
                "callback_data": "fav:stock:017670",
                "secondary_callback": "mgr_tab:long_term",
                "button_label": "🏦 SK텔레콤 후보",
                "next_step": "하루에 몰지 말고 2~3회 장기 분할",
                "weight_pct": 3.0,
                "requested_weight_pct": 3.0,
                "budget_krw": 3_000_000,
                "requested_budget_krw": 3_000_000,
                "allocation_summary": "권장 비중 3.0% · 3,000,000원",
                "allocation_split": "씨앗 1.2% → 눌림 1.0% → 확인 0.8%",
                "split_weights": [1.2, 1.0, 0.8],
                "manager_weight_adj": 1.18,
                "candidate_sector": "통신/방어",
                "allocation_note": "",
            },
        ]
    )

    with patch("kstock.bot.mixins.scheduler.detect_regime", return_value=MagicMock(mode="neutral", label="중립", emoji="⚪")):
        actions = asyncio.run(mixin._generate_daily_actions(_make_macro()))

    weak_action = next(a for a in actions if a["ticker"] == "083650" and a["action"] == "교체매도 후보")
    candidate = next(a for a in actions if a["ticker"] == "017670")
    ranking = next(a for a in actions if a["name"] == "보유 랭킹")
    assert "교체 1순위 SK텔레콤" in weak_action["next_step"]
    assert "교체매수 우선" in candidate["next_step"]
    assert "교체재원 비에이치아이" in candidate["allocation_summary"]
    assert "교체 비에이치아이" in ranking["reason"]


def test_build_daily_candidate_actions_applies_personal_lane_bias():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin._last_scan_results = [object()]
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.20, "position": 1.0, "long_term": 1.0, "tenbagger": 0.85},
            ["개인화 우선 레인: 스윙 매니저, 포지션 매니저"],
        )
    )
    mixin._enrich_manager_candidates_with_flow_short_context = MagicMock(side_effect=lambda data, macro: data)
    mixin._build_manager_fast_context = MagicMock(return_value={})
    mixin._enrich_manager_candidates_with_fast_context = MagicMock(side_effect=lambda data, fast_context: data)

    with patch(
        "kstock.bot.investment_managers.filter_discovery_candidates",
        side_effect=lambda scan_results, manager_key, exclude: {
            "tenbagger": [
                {
                    "ticker": "222222",
                    "name": "텐배거주",
                    "fit_score": 86,
                    "composite": 78,
                    "confidence_score": 0.74,
                    "fit_reasons": ["국내 스몰캡 핵심 구간"],
                    "action_hint": "씨앗 포지션 구축",
                },
            ],
            "swing": [
                {
                    "ticker": "333333",
                    "name": "스윙주",
                    "fit_score": 80,
                    "composite": 76,
                    "confidence_score": 0.72,
                    "fit_reasons": ["눌림목 반등"],
                    "action_hint": "반등 확인 후 분할 진입",
                },
            ],
        }.get(manager_key, []),
    ):
        actions = mixin._build_daily_candidate_actions(
            holdings=[],
            macro=_make_macro(),
            playbook=None,
            alert_mode="normal",
        )

    assert len(actions) >= 2
    assert actions[0]["ticker"] == "333333"
    assert "주호님 스타일과 잘 맞음" in actions[0]["reason"]


def test_build_daily_candidate_actions_attaches_allocation_hint():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_portfolio_snapshots.return_value = [
        {
            "total_value": 200_000_000,
            "cash": 60_000_000,
        },
    ]
    mixin._last_scan_results = [object()]
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.12, "position": 1.0, "long_term": 1.0, "tenbagger": 1.0},
            ["개인화 우선 레인: 스윙 매니저, 포지션 매니저"],
        )
    )
    mixin._enrich_manager_candidates_with_flow_short_context = MagicMock(side_effect=lambda data, macro: data)
    mixin._build_manager_fast_context = MagicMock(return_value={})
    mixin._enrich_manager_candidates_with_fast_context = MagicMock(side_effect=lambda data, fast_context: data)
    mixin._build_manager_herd_signals = MagicMock(return_value=({}, []))
    mixin._enrich_manager_candidates_with_herd_context = MagicMock(side_effect=lambda data, herd_map: data)
    mixin._enrich_manager_candidates_with_operator_memory = MagicMock(
        return_value=(
            {
                "swing": [{
                    "ticker": "333333",
                    "name": "스윙주",
                    "price": 50_000,
                    "fit_score": 82,
                    "composite": 74,
                    "confidence_score": 0.72,
                    "fit_reasons": ["눌림목 반등", "기관 순매수"],
                    "action_hint": "반등 확인 후 분할 진입",
                    "day_change": 1.8,
                }],
            },
            SimpleNamespace(manager_focus=[]),
        )
    )

    with patch(
        "kstock.bot.investment_managers.filter_discovery_candidates",
        side_effect=lambda scan_results, manager_key, exclude: [{"ticker": "333333"}] if manager_key == "swing" else [],
    ):
        actions = mixin._build_daily_candidate_actions(
            holdings=[],
            macro=_make_macro(),
            playbook=None,
            alert_mode="normal",
        )

    assert actions
    assert "권장 비중" in actions[0]["allocation_summary"]
    assert "현금 바닥" in actions[0]["allocation_summary"]
    assert "씨앗" in actions[0]["allocation_split"]


def test_build_daily_candidate_actions_penalizes_sector_overlap():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_portfolio_snapshots.return_value = [
        {
            "total_value": 200_000_000,
            "cash": 20_000_000,
        },
    ]
    mixin._last_scan_results = [object()]
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.0, "position": 1.0, "long_term": 1.0, "tenbagger": 1.0},
            [],
        )
    )
    mixin._enrich_manager_candidates_with_flow_short_context = MagicMock(side_effect=lambda data, macro: data)
    mixin._build_manager_fast_context = MagicMock(return_value={})
    mixin._enrich_manager_candidates_with_fast_context = MagicMock(side_effect=lambda data, fast_context: data)
    mixin._build_manager_herd_signals = MagicMock(return_value=({}, []))
    mixin._enrich_manager_candidates_with_herd_context = MagicMock(side_effect=lambda data, herd_map: data)
    mixin._enrich_manager_candidates_with_operator_memory = MagicMock(
        return_value=(
            {
                "position": [{
                    "ticker": "105840",
                    "name": "우진",
                    "price": 30_000,
                    "fit_score": 84,
                    "composite": 76,
                    "confidence_score": 0.70,
                    "fit_reasons": ["원전 계측기", "정책 수혜"],
                    "action_hint": "실적/스토리 유지 전제 1~3개월 보유",
                    "day_change": 1.2,
                }],
            },
            SimpleNamespace(manager_focus=[]),
        )
    )

    with patch(
        "kstock.bot.investment_managers.filter_discovery_candidates",
        side_effect=lambda scan_results, manager_key, exclude: [{"ticker": "105840"}] if manager_key == "position" else [],
    ):
        actions = mixin._build_daily_candidate_actions(
            holdings=[{"ticker": "083650", "name": "비에이치아이", "eval_amount": 110_000_000, "holding_type": "position"}],
            macro=_make_macro(),
            playbook=None,
            alert_mode="normal",
        )

    assert actions
    assert "원전/전력" in actions[0]["allocation_summary"]
    assert "편중" in actions[0]["next_step"] or "비중 높음" in actions[0]["next_step"]


def test_build_daily_candidate_actions_rebalances_budget_toward_strong_manager():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_portfolio_snapshots.return_value = [
        {
            "total_value": 200_000_000,
            "cash": 80_000_000,
        },
    ]
    mixin._last_scan_results = [object()]
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.0, "position": 1.0, "long_term": 1.0, "tenbagger": 1.0},
            [],
        )
    )
    mixin._load_recent_manager_scorecards = MagicMock(
        return_value={
            "swing": {"weight_adj": 0.78, "avg_return_5d": -0.8},
            "long_term": {"weight_adj": 1.18, "avg_return_5d": 1.7},
        }
    )
    mixin._enrich_manager_candidates_with_flow_short_context = MagicMock(side_effect=lambda data, macro: data)
    mixin._build_manager_fast_context = MagicMock(return_value={})
    mixin._enrich_manager_candidates_with_fast_context = MagicMock(side_effect=lambda data, fast_context: data)
    mixin._build_manager_herd_signals = MagicMock(return_value=({}, []))
    mixin._enrich_manager_candidates_with_herd_context = MagicMock(side_effect=lambda data, herd_map: data)
    mixin._enrich_manager_candidates_with_operator_memory = MagicMock(
        return_value=(
            {
                "swing": [{
                    "ticker": "333333",
                    "name": "스윙주",
                    "price": 50_000,
                    "fit_score": 82,
                    "composite": 74,
                    "confidence_score": 0.72,
                    "fit_reasons": ["눌림목 반등", "기관 순매수"],
                    "action_hint": "반등 확인 후 분할 진입",
                    "day_change": 1.0,
                }],
                "long_term": [{
                    "ticker": "017670",
                    "name": "SK텔레콤",
                    "price": 60_000,
                    "fit_score": 80,
                    "composite": 73,
                    "confidence_score": 0.70,
                    "fit_reasons": ["품질 가치", "안정 현금흐름"],
                    "action_hint": "하루에 몰지 말고 2~3회 장기 분할",
                    "day_change": 0.8,
                }],
            },
            SimpleNamespace(manager_focus=[]),
        )
    )

    with patch(
        "kstock.bot.investment_managers.filter_discovery_candidates",
        side_effect=lambda scan_results, manager_key, exclude: [{"ticker": manager_key}] if manager_key in {"swing", "long_term"} else [],
    ):
        actions = mixin._build_daily_candidate_actions(
            holdings=[],
            macro=_make_macro(),
            playbook=None,
            alert_mode="normal",
        )

    by_mgr = {action["manager_key"]: action for action in actions}
    assert by_mgr["long_term"]["weight_pct"] > by_mgr["swing"]["weight_pct"]
    assert "레인 1.18x" in by_mgr["long_term"]["allocation_summary"]
    assert "약한 레인" in by_mgr["swing"]["next_step"]


def test_build_daily_action_coach_lines_highlights_rotation_pair():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = MagicMock()
    mixin.db.get_portfolio_snapshots.return_value = [{"total_value": 100_000_000, "cash": 18_000_000}]
    mixin.db.get_active_holdings.return_value = []
    mixin._build_personalized_lane_bias = MagicMock(
        return_value=(
            {"scalp": 1.0, "swing": 1.0, "position": 1.0, "long_term": 1.0, "tenbagger": 1.0},
            [],
        )
    )
    mixin._build_downside_playbook = MagicMock(return_value=None)

    actions = [
        {
            "priority": "opportunity",
            "name": "SK텔레콤",
            "action": "추매 후보",
            "ticker": "017670",
            "rotation_source": "비에이치아이",
            "allocation_summary": "권장 비중 3.0% · 3,000,000원 · 교체재원 비에이치아이",
            "weight_pct": 3.0,
            "budget_krw": 3_000_000,
        },
        {
            "priority": "check",
            "name": "보유 랭킹",
            "action": "유지/추가/축소 순서",
            "holding_rank_summary": "교체 비에이치아이 > 추가 SK텔레콤 > 유지 우진",
        },
    ]

    lines = mixin._build_daily_action_coach_lines(actions, _make_macro())

    assert any("교체 우선: 비에이치아이->SK텔레콤" in line for line in lines)
    assert any("보유 랭킹: 교체 비에이치아이 > 추가 SK텔레콤 > 유지 우진" in line for line in lines)


def test_apply_intraday_execution_timing_adds_phase_guidance():
    from kstock.bot.mixins.scheduler import SchedulerMixin
    from kstock.core.tz import KST

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    actions = [
        {"priority": "opportunity", "name": "SK텔레콤", "action": "추매 후보"},
        {"priority": "caution", "name": "비에이치아이", "action": "분할익절 우선"},
        {"priority": "caution", "name": "씨에스윈드", "action": "교체매도 후보"},
        {"priority": "check", "name": "보유 랭킹", "action": "유지/추가/축소 순서"},
    ]

    timed = mixin._apply_intraday_execution_timing(
        actions,
        now=datetime(2026, 3, 13, 14, 45, tzinfo=KST),
    )

    by_action = {item["action"]: item for item in timed}
    assert by_action["추매 후보"]["execution_window"].startswith("오후:")
    assert "오후 강도 유지" in by_action["추매 후보"]["execution_window"]
    assert "교체 우선" in by_action["교체매도 후보"]["execution_window"]
    assert "랭킹 상단부터" in by_action["유지/추가/축소 순서"]["execution_window"]
