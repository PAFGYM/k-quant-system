from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


class _FakeDB:
    def __init__(self, tmp_path: Path) -> None:
        self.db_file = tmp_path / "holdings.db"
        self._make_schema()
        self.active_holdings: list[dict] = []
        self.snapshots: list[dict] = []
        self.latest_screenshot = {
            "recognized_at": "2026-03-13T15:26:03.862983",
            "created_at": "2026-03-13T15:26:03.862983",
            "total_eval": 103_925_550,
            "cash": 34_855_921,
            "total_profit_pct": -1.82,
            "holdings_json": json.dumps(
                [
                    {
                        "name": "비에이치아이",
                        "ticker": "",
                        "quantity": 812,
                        "avg_price": 104_886,
                        "current_price": 102_900,
                        "profit_pct": -2.12,
                        "eval_amount": 83_554_800,
                        "purchase_type": "현금",
                    },
                    {
                        "name": "우진",
                        "ticker": "",
                        "quantity": 827,
                        "avg_price": 27_950,
                        "current_price": 28_250,
                        "profit_pct": 0.84,
                        "eval_amount": 23_362_750,
                        "purchase_type": "현금",
                    },
                    {
                        "name": "씨에스윈드",
                        "ticker": "",
                        "quantity": 1_760,
                        "avg_price": 56_700,
                        "current_price": 55_000,
                        "profit_pct": -3.24,
                        "eval_amount": 96_800_000,
                        "purchase_type": "유융",
                    },
                ],
                ensure_ascii=False,
            ),
        }

    def _make_schema(self) -> None:
        conn = sqlite3.connect(str(self.db_file))
        conn.execute(
            """
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                name TEXT,
                status TEXT,
                buy_date TEXT,
                created_at TEXT,
                updated_at TEXT,
                purchase_type TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO holdings (ticker, name, status, buy_date, created_at, updated_at, purchase_type)
            VALUES ('247540', '에코프로비엠', 'sold', '2026-03-10', '2026-03-12T07:00:00', '2026-03-12T07:00:00', '현금')
            """
        )
        conn.commit()
        conn.close()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row
        return conn

    def get_active_holdings(self) -> list[dict]:
        return list(self.active_holdings)

    def get_latest_screenshot(self) -> dict:
        return dict(self.latest_screenshot)

    def get_holding_by_name(self, name: str) -> dict | None:
        for row in self.active_holdings:
            if row.get("name") == name:
                return row
        return None

    def upsert_holding(self, **kwargs):
        self.active_holdings.append(
            {
                "ticker": kwargs["ticker"],
                "name": kwargs["name"],
                "quantity": kwargs.get("quantity", 0),
                "buy_price": kwargs.get("buy_price", 0),
                "current_price": kwargs.get("current_price", 0),
                "pnl_pct": kwargs.get("pnl_pct", 0),
                "eval_amount": kwargs.get("eval_amount", 0),
                "holding_type": kwargs.get("holding_type", "auto"),
                "purchase_type": kwargs.get("purchase_type", ""),
                "is_margin": kwargs.get("is_margin", 0),
                "margin_type": kwargs.get("margin_type", ""),
            }
        )
        return len(self.active_holdings)

    def get_portfolio_snapshots(self, limit: int = 1) -> list[dict]:
        return list(self.snapshots[:limit])

    def add_portfolio_snapshot(self, **kwargs):
        self.snapshots.insert(0, dict(kwargs))
        return len(self.snapshots)


@pytest.mark.asyncio
async def test_load_holdings_with_fallback_restores_newer_screenshot(tmp_path):
    from kstock.bot.mixins.trading import TradingMixin

    mixin = TradingMixin.__new__(TradingMixin)
    mixin.db = _FakeDB(tmp_path)
    mixin.all_tickers = [
        {"code": "083650", "name": "비에이치아이"},
        {"code": "105840", "name": "우진"},
        {"code": "112610", "name": "씨에스윈드"},
    ]

    holdings = await mixin._load_holdings_with_fallback()

    assert [h["ticker"] for h in holdings] == ["083650", "105840", "112610"]
    assert len(mixin.db.active_holdings) == 3
    assert mixin.db.snapshots
    assert mixin.db.snapshots[0]["total_value"] == 203_717_550
    assert mixin.db.snapshots[0]["holdings_count"] == 3


def test_build_balance_buttons_maps_auto_to_real_manager(tmp_path):
    from kstock.bot.mixins.trading import TradingMixin

    mixin = TradingMixin.__new__(TradingMixin)
    mixin.db = _FakeDB(tmp_path)

    buttons = mixin._build_balance_buttons(
        [{"ticker": "083650", "name": "비에이치아이", "holding_type": "auto"}]
    )

    analysis_button = buttons[1][0]
    assert analysis_button.callback_data == "mgr:swing:083650"


def test_format_balance_lines_is_scannable_block_layout(tmp_path):
    from kstock.bot.mixins.trading import TradingMixin

    mixin = TradingMixin.__new__(TradingMixin)
    mixin.db = _FakeDB(tmp_path)
    mixin._alert_mode = "wartime"

    lines = mixin._format_balance_lines(
        [
            {
                "ticker": "112610",
                "name": "씨에스윈드",
                "quantity": 1760,
                "buy_price": 56_700,
                "current_price": 55_400,
                "pnl_pct": -2.3,
                "eval_amount": 96_800_000,
                "purchase_type": "유융",
                "is_margin": 1,
                "stop_price": 53_865,
                "holding_type": "auto",
                "day_change_pct": -1.1,
                "day_change": -600,
            }
        ],
        total_eval=204_989_950,
        total_invested=208_074_082,
    )

    text = "\n".join(lines)
    assert "📦 보유종목 1개" in text
    assert "씨에스윈드 (112610)" in text
    assert "1760주 · 유융 · 신용/마진" in text
    assert "매수 56,700원 / 현재 55,400원" in text
    assert "🔥 스윙 매니저" in text
    assert "전시 손절 53,865원" in text


def test_format_balance_lines_includes_timing_coach(tmp_path):
    from kstock.bot.mixins.trading import TradingMixin

    mixin = TradingMixin.__new__(TradingMixin)
    mixin.db = _FakeDB(tmp_path)
    mixin._alert_mode = "normal"

    lines = mixin._format_balance_lines(
        [
            {
                "ticker": "083650",
                "name": "비에이치아이",
                "quantity": 812,
                "buy_price": 104_886,
                "current_price": 103_600,
                "pnl_pct": -1.2,
                "eval_amount": 83_554_800,
                "purchase_type": "현금",
                "holding_type": "auto",
                "_timing_lines": [
                    "⏱ 타이밍 15일 중심 변곡 끝자락",
                    "15일 중심 변곡 끝자락 확인. 지금은 씨앗 또는 1차 분할이 맞고, MA5 유지 여부를 같이 확인하세요.",
                ],
            }
        ],
        total_eval=83_554_800,
        total_invested=85_000_000,
    )

    text = "\n".join(lines)
    assert "⏱ 타이밍 15일 중심 변곡 끝자락" in text
    assert "씨앗 또는 1차 분할" in text
