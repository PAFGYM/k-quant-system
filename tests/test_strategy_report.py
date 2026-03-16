from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kstock.bot.mixins.scheduler import SchedulerMixin


@pytest.mark.asyncio
async def test_generate_strategy_report_includes_market_holdings_tenbagger_and_learning():
    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.macro_client = MagicMock()
    mixin.macro_client.get_snapshot = AsyncMock(
        return_value=MagicMock(
            vix=27.2,
            usdkrw=1494.4,
            ewy_change_pct=0.53,
            wti_price=71.0,
            wti_change_pct=3.1,
            regime="risk_off",
        )
    )
    mixin._market_signal = lambda macro: ("🔴", "경계")
    mixin._build_downside_playbook = lambda macro: None
    mixin._build_morning_market_impact_lines = lambda macro, playbook: ["- 글로벌 변수 점검"]
    mixin._build_morning_action_lines = lambda macro, regime_mode, playbook: ["- 다음 개장 전 보유주 손절선 확인"]
    mixin._build_personal_operator_lines = lambda limit=4: ["- 주로 보는 것: 매수, 시장, 매도"]
    mixin._build_morning_holdings_lines = AsyncMock(
        return_value=["- 보유 3종목 | 건강점수 72/100 | 평균 +0.6%", "- 우진 +1.1% | 보유 유지"]
    )
    mixin._build_market_rotation_learning_snapshot = AsyncMock(
        return_value={
            "tags": ["코스피-코스닥 디커플링", "대형 반도체 쏠림", "원전/전력 차익실현"],
            "explanation": "코스피는 대형 반도체 강세로 올랐지만 코스닥과 원전주는 차익실현이 우세했다.",
            "action": "원전주는 개별 악재보다 테마 전체 약세 여부를 먼저 확인한다.",
            "affected_holdings": ["우진", "비에이치아이"],
            "top_semi": {"name": "SK하이닉스", "change_pct": 7.0},
            "weakest_nuclear": {"name": "우진", "change_pct": -6.2},
        }
    )
    mixin._save_market_rotation_learning_snapshot = lambda snapshot: None
    mixin._build_strategy_watch_brief = lambda **kwargs: "[전략 감시]\n🌍 글로벌: 유가/환율 경계"
    mixin.db = MagicMock()
    mixin.db.get_tenbagger_universe.return_value = [
        {
            "ticker": "105840",
            "name": "우진",
            "tenbagger_score": 92,
            "sector": "nuclear_smr",
            "current_return": 10.5,
        }
    ]
    mixin.db.get_tenbagger_score_trend.return_value = [
        {"tenbagger_score": 92},
        {"tenbagger_score": 88},
    ]
    mixin.db.get_tenbagger_catalysts.return_value = [{"title": "SMR 수주 확대"}]

    with patch(
        "kstock.bot.mixins.scheduler.detect_regime",
        return_value=MagicMock(label="리스크오프"),
    ), patch(
        "kstock.bot.learning_engine.format_learning_impact_snapshot",
        return_value="🎯 학습으로 바뀐 것\n  강화: ⚡ 리버모어 1.20x",
    ):
        text = await mixin._generate_strategy_report()

    assert "전략 보고서" in text
    assert "시장 환경" in text or "휴장 점검" in text
    assert "내 보유 종목" in text
    assert "텐베거 감시" in text
    assert "학습으로 바뀐 것" in text
    assert "우진 92점 (+4)" in text
    assert "오늘 장 학습" in text
    assert "코스피-코스닥 디커플링" in text
