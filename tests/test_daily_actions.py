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
    assert any("회피:" in line for line in lines)
