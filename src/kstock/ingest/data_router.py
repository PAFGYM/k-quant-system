"""Data source router for K-Quant v4.0.

Priority:
1. KIS API (real-time, if connected)
2. Screenshot (Claude Vision parsed)
3. Button records (manual input)
4. yfinance (primary fallback)
5. Naver Finance (secondary fallback when yfinance fails)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DataSource:
    """Tracks which data source is active."""

    name: str  # "kis", "screenshot", "button", "yfinance", "naver"
    connected: bool = False


class DataRouter:
    """Routes data requests to the best available source.

    v4.0: Naver Finance 폴백 추가로 yfinance 장애 시에도 가격/OHLCV 제공.
    """

    def __init__(self, kis_broker=None, yf_client=None, db=None) -> None:
        self.kis = kis_broker
        self.yf = yf_client
        self.db = db
        self._naver = None  # lazy init
        self._source = self._detect_source()
        self._fallback_count = 0  # Naver 폴백 사용 횟수

    def _get_naver_client(self):
        """NaverFinanceClient lazy 초기화."""
        if self._naver is None:
            try:
                from kstock.ingest.naver_finance import NaverFinanceClient
                self._naver = NaverFinanceClient()
            except Exception as e:
                logger.debug("NaverFinanceClient init failed: %s", e)
        return self._naver

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
        """Get current price from best source (3-tier fallback)."""
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

        # 3. Naver Finance (새로운 폴백)
        naver = self._get_naver_client()
        if naver:
            try:
                price = await naver.get_current_price(ticker)
                if price > 0:
                    self._fallback_count += 1
                    logger.debug(
                        "Naver fallback price for %s: %s (count=%d)",
                        ticker, price, self._fallback_count,
                    )
                    return price
            except Exception:
                pass

        return 0.0

    async def get_ohlcv(
        self, ticker: str, market: str = "KOSPI", period: str = "6mo"
    ) -> "pd.DataFrame":
        """Get OHLCV data with fallback.

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
        """
        import pandas as pd

        # 1. yfinance
        if self.yf:
            try:
                df = await self.yf.get_ohlcv(ticker, market, period)
                if not df.empty:
                    return df
            except Exception:
                pass

        # 2. Naver Finance
        naver = self._get_naver_client()
        if naver:
            try:
                period_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}.get(period, 120)
                df = await naver.get_ohlcv(ticker, period_days)
                if not df.empty:
                    self._fallback_count += 1
                    return df
            except Exception:
                pass

        return pd.DataFrame()

    async def get_stock_info(
        self, ticker: str, name: str = "", market: str = "KOSPI"
    ) -> dict:
        """Get fundamental info with fallback."""
        # 1. yfinance
        if self.yf:
            try:
                info = await self.yf.get_stock_info(ticker, name, market)
                if info.get("current_price", 0) > 0:
                    return info
            except Exception:
                pass

        # 2. Naver Finance
        naver = self._get_naver_client()
        if naver:
            try:
                info = await naver.get_stock_info(ticker, name)
                if info.get("current_price", 0) > 0:
                    self._fallback_count += 1
                    return info
            except Exception:
                pass

        return {
            "ticker": ticker, "name": name or ticker, "market": market,
            "current_price": 0, "per": 0, "pbr": 0, "roe": 0,
        }

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
            mode_kr = "모의투자" if mode == "virtual" else "실전"
            return f"📡 데이터: KIS API ({mode_kr})"
        fallback_info = f" [Naver 폴백 {self._fallback_count}회]" if self._fallback_count > 0 else ""
        return f"📡 데이터: yfinance{fallback_info}"

    def get_connection_message(self) -> str:
        """Generate connection change notification."""
        if self.kis_connected:
            return (
                "✅ 주호님, KIS API 연결 완료!\n"
                "이제 실시간 데이터로 분석합니다"
            )
        return (
            "⚠️ 주호님, KIS 연결이 끊겼어요.\n"
            "스크린샷/버튼 모드로 전환합니다"
        )
