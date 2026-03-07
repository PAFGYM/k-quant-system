"""Data source router for K-Quant v5.1.

Priority:
1. KIS API (real-time, if connected)
2. Screenshot (Claude Vision parsed)
3. Button records (manual input)
4. yfinance (primary fallback)
5. Naver Finance (secondary fallback when yfinance fails)

v5.0: PIT(Point-in-Time) 소스 태깅 + 지연 추적 통합.
v5.1: 폴백 데이터 매수 차단 — 지연 소스(yfinance/naver)로 신규 매수 의사결정 금지.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# v5.1: 실시간 소스 (매수 의사결정 허용)
REALTIME_SOURCES = {"kis_realtime"}

# v5.1: 지연 소스 (매수 의사결정 금지 — 정보 표시/알림만 허용)
DELAYED_SOURCES = {"yfinance", "naver"}

# v5.1: 소스별 추정 지연 시간 (초)
SOURCE_DELAY_SECONDS = {
    "kis_realtime": 0.5,
    "database": 0,        # 수동 입력이라 시점 불확실
    "yfinance": 900,      # ~15분
    "naver": 1200,        # ~20분
    "manual": 0,
    "none": 0,
}


@dataclass
class DataSource:
    """Tracks which data source is active."""

    name: str  # "kis", "screenshot", "button", "yfinance", "naver"
    connected: bool = False


class DataRouter:
    """Routes data requests to the best available source.

    v4.0: Naver Finance 폴백 추가로 yfinance 장애 시에도 가격/OHLCV 제공.
    v5.0: PIT 소스 태깅 + SourceRegistry 연동.
    """

    def __init__(self, kis_broker=None, yf_client=None, db=None) -> None:
        self.kis = kis_broker
        self.yf = yf_client
        self.db = db
        self._naver = None  # lazy init
        self._source = self._detect_source()
        self._fallback_count = 0  # Naver 폴백 사용 횟수
        self._last_source_used: str = ""  # v5.0: 마지막 사용 소스

    def _get_naver_client(self):
        """NaverFinanceClient lazy 초기화."""
        if self._naver is None:
            try:
                from kstock.ingest.naver_finance import NaverFinanceClient
                self._naver = NaverFinanceClient()
            except Exception as e:
                logger.debug("NaverFinanceClient init failed: %s", e)
        return self._naver

    def _get_registry(self):
        """PIT SourceRegistry lazy import."""
        try:
            from kstock.ingest.point_in_time import get_registry
            return get_registry()
        except Exception:
            logger.debug("_get_registry: PIT SourceRegistry import failed", exc_info=True)
            return None

    def _detect_source(self) -> DataSource:
        if self.kis and self.kis.connected:
            return DataSource(name="kis", connected=True)
        return DataSource(name="yfinance", connected=True)

    @property
    def source_name(self) -> str:
        return self._source.name

    @property
    def last_source_used(self) -> str:
        """v5.0: 마지막으로 실제 데이터를 반환한 소스."""
        return self._last_source_used

    @property
    def kis_connected(self) -> bool:
        return self.kis is not None and self.kis.connected

    @property
    def is_realtime(self) -> bool:
        """v5.1: 마지막 데이터가 실시간 소스인지 확인."""
        return self._last_source_used in REALTIME_SOURCES

    @property
    def is_delayed(self) -> bool:
        """v5.1: 마지막 데이터가 지연 소스(폴백)인지 확인."""
        return self._last_source_used in DELAYED_SOURCES

    @property
    def estimated_delay_seconds(self) -> float:
        """v5.1: 마지막 소스의 추정 지연 시간 (초)."""
        return SOURCE_DELAY_SECONDS.get(self._last_source_used, 0)

    def can_buy_with_current_data(self) -> tuple[bool, str]:
        """v5.1: 현재 데이터 소스로 매수 의사결정이 가능한지 판단.

        Returns:
            (허용 여부, 사유 메시지)
        """
        src = self._last_source_used
        if not src:
            return False, "데이터 소스 미확인 — 매수 불가"
        if src in REALTIME_SOURCES:
            return True, ""
        delay = SOURCE_DELAY_SECONDS.get(src, 0)
        return False, (
            f"지연 데이터 소스({src}, ~{delay // 60:.0f}분 지연) 사용 중 — "
            f"신규 매수 차단 (매도/정보조회는 허용)"
        )

    def refresh_source(self) -> str:
        """Re-detect the best available data source."""
        self._source = self._detect_source()
        return self._source.name

    def _record_fetch(self, source: str, ticker: str, success: bool,
                      latency_ms: float, record_count: int = 1) -> None:
        """PIT SourceRegistry에 수집 결과 기록."""
        registry = self._get_registry()
        if registry:
            registry.record_fetch(
                source=source, ticker=ticker, success=success,
                latency_ms=latency_ms, record_count=record_count,
            )

    async def get_portfolio(self) -> dict | None:
        """Get portfolio from best available source."""
        # 1. KIS API
        if self.kis_connected:
            try:
                balance = self.kis.get_balance()
                if balance:
                    balance["_pit_source"] = "kis_realtime"
                    self._last_source_used = "kis_realtime"
                    return balance
            except Exception:
                logger.warning("KIS get_balance failed", exc_info=True)

        # 2. SQLite (screenshot/button records)
        if self.db:
            holdings = self.db.get_active_holdings()
            if holdings:
                self._last_source_used = "database"
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
                    "_pit_source": "manual",
                }

        return None

    async def get_price(self, ticker: str, market: str = "KOSPI") -> float:
        """Get current price from best source (3-tier fallback).

        v5.0: 소스 태깅 + 지연 추적.
        """
        # 1. KIS
        if self.kis_connected:
            t0 = time.monotonic()
            price = self.kis.get_realtime_price(ticker)
            elapsed = (time.monotonic() - t0) * 1000
            if price > 0:
                self._last_source_used = "kis_realtime"
                self._record_fetch("kis_realtime", ticker, True, elapsed)
                return price
            self._record_fetch("kis_realtime", ticker, False, elapsed)

        # 2. yfinance
        if self.yf:
            try:
                t0 = time.monotonic()
                price = await self.yf.get_current_price(ticker, market)
                elapsed = (time.monotonic() - t0) * 1000
                if price > 0:
                    self._last_source_used = "yfinance"
                    self._record_fetch("yfinance", ticker, True, elapsed)
                    return price
                self._record_fetch("yfinance", ticker, False, elapsed)
            except Exception:
                logger.debug("get_price: yfinance price fetch failed for %s", ticker, exc_info=True)
                self._record_fetch("yfinance", ticker, False, 0)

        # 3. Naver Finance (새로운 폴백)
        naver = self._get_naver_client()
        if naver:
            try:
                t0 = time.monotonic()
                price = await naver.get_current_price(ticker)
                elapsed = (time.monotonic() - t0) * 1000
                if price > 0:
                    self._fallback_count += 1
                    self._last_source_used = "naver"
                    self._record_fetch("naver", ticker, True, elapsed)
                    logger.debug(
                        "Naver fallback price for %s: %s (count=%d)",
                        ticker, price, self._fallback_count,
                    )
                    return price
                self._record_fetch("naver", ticker, False, elapsed)
            except Exception:
                logger.debug("get_price: Naver price fetch failed for %s", ticker, exc_info=True)
                self._record_fetch("naver", ticker, False, 0)

        return 0.0

    async def get_ohlcv(
        self, ticker: str, market: str = "KOSPI", period: str = "6mo"
    ) -> "pd.DataFrame":
        """Get OHLCV data with fallback.

        v5.0: PIT 소스 태깅 자동 적용.

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
            + _pit_source, _pit_ingest_time (v5.0)
        """
        import pandas as pd

        # 1. yfinance
        if self.yf:
            try:
                t0 = time.monotonic()
                df = await self.yf.get_ohlcv(ticker, market, period)
                elapsed = (time.monotonic() - t0) * 1000
                if not df.empty:
                    self._last_source_used = "yfinance"
                    self._record_fetch(
                        "yfinance", ticker, True, elapsed,
                        record_count=len(df),
                    )
                    df = self._tag_ohlcv(df, "yfinance", ticker)
                    return df
                self._record_fetch("yfinance", ticker, False, elapsed)
            except Exception:
                logger.debug("get_ohlcv: yfinance OHLCV fetch failed for %s", ticker, exc_info=True)
                self._record_fetch("yfinance", ticker, False, 0)

        # 2. Naver Finance
        naver = self._get_naver_client()
        if naver:
            try:
                t0 = time.monotonic()
                period_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}.get(period, 120)
                df = await naver.get_ohlcv(ticker, period_days)
                elapsed = (time.monotonic() - t0) * 1000
                if not df.empty:
                    self._fallback_count += 1
                    self._last_source_used = "naver"
                    self._record_fetch(
                        "naver", ticker, True, elapsed,
                        record_count=len(df),
                    )
                    df = self._tag_ohlcv(df, "naver", ticker)
                    return df
                self._record_fetch("naver", ticker, False, elapsed)
            except Exception:
                logger.debug("get_ohlcv: Naver OHLCV fetch failed for %s", ticker, exc_info=True)
                self._record_fetch("naver", ticker, False, 0)

        return pd.DataFrame()

    def _tag_ohlcv(self, df: "pd.DataFrame", source: str, ticker: str) -> "pd.DataFrame":
        """OHLCV DataFrame에 PIT 메타 컬럼 추가."""
        try:
            from kstock.ingest.point_in_time import AsOfJoinEngine
            return AsOfJoinEngine.tag_dataframe(df, source=source, ticker=ticker)
        except Exception:
            logger.debug("_tag_ohlcv: PIT AsOfJoinEngine import/tag failed", exc_info=True)
            # PIT 모듈 없어도 동작
            return df

    async def get_stock_info(
        self, ticker: str, name: str = "", market: str = "KOSPI"
    ) -> dict:
        """Get fundamental info with fallback."""
        # 1. yfinance
        if self.yf:
            try:
                t0 = time.monotonic()
                info = await self.yf.get_stock_info(ticker, name, market)
                elapsed = (time.monotonic() - t0) * 1000
                if info.get("current_price", 0) > 0:
                    info["_pit_source"] = "yfinance"
                    self._last_source_used = "yfinance"
                    self._record_fetch("yfinance", ticker, True, elapsed)
                    return info
                self._record_fetch("yfinance", ticker, False, elapsed)
            except Exception:
                logger.debug("get_stock_info: yfinance info fetch failed for %s", ticker, exc_info=True)
                self._record_fetch("yfinance", ticker, False, 0)

        # 2. Naver Finance
        naver = self._get_naver_client()
        if naver:
            try:
                t0 = time.monotonic()
                info = await naver.get_stock_info(ticker, name)
                elapsed = (time.monotonic() - t0) * 1000
                if info.get("current_price", 0) > 0:
                    self._fallback_count += 1
                    info["_pit_source"] = "naver"
                    self._last_source_used = "naver"
                    self._record_fetch("naver", ticker, True, elapsed)
                    return info
                self._record_fetch("naver", ticker, False, elapsed)
            except Exception:
                logger.debug("get_stock_info: Naver info fetch failed for %s", ticker, exc_info=True)
                self._record_fetch("naver", ticker, False, 0)

        return {
            "ticker": ticker, "name": name or ticker, "market": market,
            "current_price": 0, "per": 0, "pbr": 0, "roe": 0,
            "_pit_source": "none",
        }

    async def get_investor_data(self, ticker: str) -> dict | None:
        """Get foreign/institutional investor data."""
        if self.kis_connected:
            try:
                stock = self.kis.kis.stock(ticker)
                return stock.investor()
            except Exception:
                logger.warning("get_investor_data: KIS investor data fetch failed for %s", ticker, exc_info=True)
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
