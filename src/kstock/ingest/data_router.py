"""Data source router for K-Quant v3.0.

Priority:
1. KIS API (real-time, if connected)
2. Screenshot (Claude Vision parsed)
3. Button records (manual input)
4. yfinance (fallback)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DataSource:
    """Tracks which data source is active."""

    name: str  # "kis", "screenshot", "button", "yfinance"
    connected: bool = False


class DataRouter:
    """Routes data requests to the best available source."""

    def __init__(self, kis_broker=None, yf_client=None, db=None) -> None:
        self.kis = kis_broker
        self.yf = yf_client
        self.db = db
        self._source = self._detect_source()

    def _detect_source(self) -> DataSource:
        if self.kis and self.kis.connected:
            return DataSource(name="kis", connected=True)
        return DataSource(name="yfinance", connected=True)

    @property
    def source_name(self) -> str:
        return self._source.name

    @property
    def kis_connected(self) -> bool:
        return self.kis is not None and self.kis.connected

    def refresh_source(self) -> str:
        """Re-detect the best available data source."""
        self._source = self._detect_source()
        return self._source.name

    async def get_portfolio(self) -> dict | None:
        """Get portfolio from best available source."""
        # 1. KIS API
        if self.kis_connected:
            balance = self.kis.get_balance()
            if balance:
                return balance

        # 2. SQLite (screenshot/button records)
        if self.db:
            holdings = self.db.get_active_holdings()
            if holdings:
                return {
                    "holdings": [
                        {
                            "ticker": h["ticker"],
                            "name": h["name"],
                            "quantity": 0,
                            "avg_price": h["buy_price"],
                            "current_price": h.get("current_price") or h["buy_price"],
                            "profit_pct": h.get("pnl_pct", 0),
                            "eval_amount": 0,
                        }
                        for h in holdings
                    ],
                    "total_eval": 0,
                    "total_profit": 0,
                    "cash": 0,
                    "source": "database",
                }

        return None

    async def get_price(self, ticker: str, market: str = "KOSPI") -> float:
        """Get current price from best source."""
        # 1. KIS
        if self.kis_connected:
            price = self.kis.get_realtime_price(ticker)
            if price > 0:
                return price

        # 2. yfinance
        if self.yf:
            try:
                price = await self.yf.get_current_price(ticker, market)
                if price > 0:
                    return price
            except Exception:
                pass

        return 0.0

    async def get_investor_data(self, ticker: str) -> dict | None:
        """Get foreign/institutional investor data."""
        if self.kis_connected:
            try:
                stock = self.kis.kis.stock(ticker)
                return stock.investor()
            except Exception:
                pass
        return None

    def format_source_status(self) -> str:
        """Format current data source for display."""
        if self.kis_connected:
            mode = getattr(self.kis, "mode", "virtual")
            mode_kr = "\ubaa8\uc758\ud22c\uc790" if mode == "virtual" else "\uc2e4\uc804"
            return f"\U0001f4e1 \ub370\uc774\ud130: KIS API ({mode_kr})"
        return "\U0001f4e1 \ub370\uc774\ud130: yfinance"

    def get_connection_message(self) -> str:
        """Generate connection change notification."""
        if self.kis_connected:
            return (
                "\u2705 \uc8fc\ud638\ub2d8, KIS API \uc5f0\uacb0 \uc644\ub8cc!\n"
                "\uc774\uc81c \uc2e4\uc2dc\uac04 \ub370\uc774\ud130\ub85c \ubd84\uc11d\ud569\ub2c8\ub2e4"
            )
        return (
            "\u26a0\ufe0f \uc8fc\ud638\ub2d8, KIS \uc5f0\uacb0\uc774 \ub04a\uacbc\uc5b4\uc694.\n"
            "\uc2a4\ud06c\ub9b0\uc0f7/\ubc84\ud2bc \ubaa8\ub4dc\ub85c \uc804\ud658\ud569\ub2c8\ub2e4"
        )
