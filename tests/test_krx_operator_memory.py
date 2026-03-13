from __future__ import annotations

from types import SimpleNamespace


class DummyDB:
    def get_market_regime(self, days: int = 30) -> list[dict]:
        return [
            {
                "date": "2026-03-12",
                "regime": "bear",
                "description": "하락장 (종합 -28, 신뢰도 68%)",
                "sector_rotation_json": '{"방산":"방어주 비중 확대","에너지":"유가 연동 확인 후 선별"}',
                "portfolio_guide_json": '{"new_buy":"신규 매수 보류","hedging":"인버스 ETF 20%","position_size":"축소 (40-60%)"}',
            },
            {
                "date": "2026-03-11",
                "regime": "bear",
                "description": "하락장 (종합 -24, 신뢰도 60%)",
                "sector_rotation_json": '{"방산":"방어주 비중 확대","금융":"고배당주 집중"}',
                "portfolio_guide_json": '{"new_buy":"신규 매수 보류","hedging":"인버스 ETF 20%","position_size":"축소 (40-60%)"}',
            },
        ]

    def get_latest_cross_market(self) -> dict:
        return {
            "date": "2026-03-12",
            "direction": "risk_off",
            "composite_score": -3.8,
            "vix": 28.5,
            "usdkrw_change_pct": 0.8,
            "wti_change_pct": 6.1,
            "risk_flags_json": '["oil spike", "fx stress"]',
        }

    def get_cross_market_impact(self, days: int = 45) -> list[dict]:
        return [
            self.get_latest_cross_market(),
            {
                "date": "2026-03-11",
                "direction": "risk_off",
                "composite_score": -3.1,
                "vix": 27.0,
                "usdkrw_change_pct": 0.7,
                "wti_change_pct": 5.5,
                "risk_flags_json": '["oil spike"]',
            },
        ]

    def get_learning_history(self, days: int = 14) -> list[dict]:
        return [
            {
                "description": "크로스마켓 분석: risk_off",
                "impact_summary": "유가 급등과 환율 상승이 겹치면 방산·에너지와 달러 수혜 수출주가 상대적으로 강했다.",
            },
        ]

    def get_trade_lessons(self, limit: int = 6) -> list[dict]:
        return [
            {
                "lesson": "하락장에서는 거래량만 터진 테마주보다 외인/기관이 버티는 종목만 추적",
            },
        ]


def test_build_krx_operator_memory_surfaces_similar_regime_and_attack_points():
    from kstock.signal.krx_operator_memory import (
        build_krx_operator_memory,
        format_operator_memory_lines,
    )

    macro = SimpleNamespace(
        vix=29.0,
        usdkrw_change_pct=0.9,
        wti_change_pct=6.0,
        koru_change_pct=-12.0,
        nq_futures_change_pct=-1.6,
    )
    memory = build_krx_operator_memory(DummyDB(), macro)
    lines = format_operator_memory_lines(memory)

    assert memory.regime_key == "bear"
    assert memory.top_matches
    assert memory.top_matches[0].date == "2026-03-11"
    assert memory.attack_points
    assert memory.avoid_points
    assert memory.manager_focus
    assert any("유사 장세" in line for line in lines)
    assert any("오늘 공략" in line for line in lines)
    assert any("최근 학습" in line for line in lines)


def test_build_krx_operator_memory_falls_back_without_history():
    from kstock.signal.krx_operator_memory import build_krx_operator_memory

    class SparseDB:
        def get_market_regime(self, days: int = 30) -> list[dict]:
            return []

        def get_latest_cross_market(self) -> dict | None:
            return None

        def get_cross_market_impact(self, days: int = 45) -> list[dict]:
            return []

    macro = SimpleNamespace(vix=18.0, usdkrw_change_pct=0.0, wti_change_pct=0.0)
    memory = build_krx_operator_memory(SparseDB(), macro, regime_mode={"mode": "balanced"})

    assert memory.regime_key == "neutral"
    assert memory.headline
    assert memory.manager_focus
