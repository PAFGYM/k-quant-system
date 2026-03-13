from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


class DummyDB:
    db_path = Path(__file__)

    @staticmethod
    def get_chat_usage_count(_date: str) -> int:
        return 12

    @staticmethod
    def get_active_recommendations() -> list[dict]:
        return []

    @staticmethod
    def get_job_runs(_date: str) -> list[dict]:
        return [{"job_name": "morning_briefing", "status": "success"}]

    @staticmethod
    def get_active_holdings() -> list[dict]:
        return []

    @staticmethod
    def get_financials(_ticker: str) -> dict | None:
        return {"roe": 11.2}

    @staticmethod
    def get_market_regime(days: int = 30) -> list[dict]:
        return [
            {
                "date": "2026-03-12",
                "regime": "bear",
                "description": "하락장 (종합 -28, 신뢰도 68%)",
                "sector_rotation_json": '{"방산":"방어주 비중 확대"}',
                "portfolio_guide_json": '{"new_buy":"신규 매수 보류","hedging":"인버스 ETF 20%","position_size":"축소 (40-60%)"}',
            },
            {
                "date": "2026-03-11",
                "regime": "bear",
                "description": "하락장 (종합 -24, 신뢰도 60%)",
                "sector_rotation_json": '{"방산":"방어주 비중 확대"}',
                "portfolio_guide_json": '{"new_buy":"신규 매수 보류","hedging":"인버스 ETF 20%","position_size":"축소 (40-60%)"}',
            },
        ]

    @staticmethod
    def get_latest_cross_market() -> dict:
        return {
            "date": "2026-03-12",
            "direction": "risk_off",
            "composite_score": -3.8,
            "vix": 28.5,
            "usdkrw_change_pct": 0.8,
            "wti_change_pct": 6.1,
            "risk_flags_json": '["oil spike"]',
        }

    @staticmethod
    def get_cross_market_impact(days: int = 45) -> list[dict]:
        return [
            DummyDB.get_latest_cross_market(),
            {
                "date": "2026-03-11",
                "direction": "risk_off",
                "composite_score": -3.2,
                "vix": 27.4,
                "usdkrw_change_pct": 0.7,
                "wti_change_pct": 5.7,
                "risk_flags_json": '["oil spike"]',
            },
        ]

    @staticmethod
    def get_learning_history(days: int = 14) -> list[dict]:
        return [
            {
                "description": "크로스마켓 분석",
                "impact_summary": "유가와 환율이 같이 튀는 날에는 방산·에너지와 달러 수혜주가 상대적으로 강했다.",
            },
        ]

    @staticmethod
    def get_trade_lessons(limit: int = 6) -> list[dict]:
        return [
            {"lesson": "하락장 시초에는 추격매수보다 강한 종목의 눌림 확인이 우선"},
        ]


class DummyMacroClient:
    async def get_snapshot(self):
        return SimpleNamespace(
            is_cached=False,
            vix=29.0,
            usdkrw_change_pct=0.9,
            wti_change_pct=6.0,
            koru_change_pct=-11.2,
            nq_futures_change_pct=-1.4,
        )


@pytest.mark.asyncio
async def test_generate_daily_self_report_includes_operator_memory_section():
    from kstock.bot.daily_self_report import generate_daily_self_report

    report = await generate_daily_self_report(DummyDB(), DummyMacroClient(), ws=None)

    assert "한국장 오퍼레이터 메모" in report
    assert "유사 장세" in report
    assert "공략 포인트" in report
    assert "회피 포인트" in report
