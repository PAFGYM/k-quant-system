"""Real data client for Korean stocks via yfinance (.KS/.KQ suffixes).

v4.0: 서킷 브레이커 + Naver Finance 폴백 적용.
v4.1: KST 타임존 캐시 + 날짜 경계 무효화.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import yfinance as yf

from kstock.core.tz import KST

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# v4.0: 서킷 브레이커 (yfinance 연속 실패 시 일시 차단)
try:
    from kstock.core.circuit_breaker import get_breaker
    _yf_breaker = get_breaker("yfinance", failure_threshold=5, recovery_timeout=120)
except Exception:
    logger.debug("yfinance circuit breaker import failed", exc_info=True)
    _yf_breaker = None

# Cache for yfinance data to avoid repeated API calls
_price_cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
_info_cache: dict[str, tuple[datetime, dict]] = {}
_CACHE_TTL = timedelta(minutes=10)


def _is_cache_stale_date(cached_time: datetime) -> bool:
    """캐시 저장 시각이 오늘(KST) 이전 날짜이면 stale로 판단."""
    now = datetime.now(KST)
    ct = cached_time
    if ct.tzinfo is None:
        # naive datetime → KST로 간주
        ct = ct.replace(tzinfo=KST)
    return ct.astimezone(KST).date() < now.date()


def _yf_ticker(code: str, market: str = "KOSPI") -> str:
    """Convert Korean stock code to yfinance symbol."""
    suffix = ".KS" if market.upper() == "KOSPI" else ".KQ"
    return f"{code}{suffix}"


@dataclass
class YFStockData:
    """Stock data fetched from yfinance."""

    ticker: str
    name: str
    ohlcv: pd.DataFrame
    current_price: float
    per: float
    pbr: float
    dividend_yield: float
    market_cap: float
    roe: float
    debt_ratio: float
    consensus_target: float


class YFinanceKRClient:
    """Client for fetching Korean stock data via yfinance."""

    def __init__(self) -> None:
        self._failed_tickers: set[str] = set()

    async def get_ohlcv(
        self, code: str, market: str = "KOSPI", period: str = "6mo"
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a Korean stock."""
        symbol = _yf_ticker(code, market)
        now = datetime.now(KST)

        # Check cache (v4.1: KST 타임존 + 날짜 경계 무효화)
        if symbol in _price_cache:
            cached_time, cached_df = _price_cache[symbol]
            if (not _is_cache_stale_date(cached_time)
                    and now - cached_time < _CACHE_TTL
                    and not cached_df.empty):
                return cached_df

        # v4.0: 서킷 브레이커 체크
        if _yf_breaker and not _yf_breaker.can_execute():
            logger.debug("yfinance circuit OPEN, using cache/fallback for %s", symbol)
            if symbol in _price_cache:
                return _price_cache[symbol][1]
            return await self._naver_fallback_ohlcv(code)

        try:
            hist = await asyncio.to_thread(self._fetch_ohlcv_sync, symbol, period)
            if hist.empty:
                raise ValueError(f"No data for {symbol}")

            df = pd.DataFrame({
                "date": hist.index.strftime("%Y-%m-%d"),
                "open": hist["Open"].values,
                "high": hist["High"].values,
                "low": hist["Low"].values,
                "close": hist["Close"].values,
                "volume": hist["Volume"].astype(int).values,
            })
            df = df.reset_index(drop=True)
            _price_cache[symbol] = (now, df)
            if _yf_breaker:
                _yf_breaker.record_success()
            return df

        except Exception as e:
            logger.warning("yfinance OHLCV failed for %s: %s", symbol, e)
            if _yf_breaker:
                _yf_breaker.record_failure()
            if symbol in _price_cache:
                return _price_cache[symbol][1]
            return await self._naver_fallback_ohlcv(code)

    @staticmethod
    def _fetch_ohlcv_sync(symbol: str, period: str) -> pd.DataFrame:
        """Synchronous yfinance fetch - runs in thread pool."""
        ticker = yf.Ticker(symbol)
        return ticker.history(period=period)

    async def get_stock_info(self, code: str, name: str = "", market: str = "KOSPI") -> dict:
        """Fetch fundamental info from yfinance."""
        symbol = _yf_ticker(code, market)
        now = datetime.now(KST)

        if symbol in _info_cache:
            cached_time, cached_info = _info_cache[symbol]
            if not _is_cache_stale_date(cached_time) and now - cached_time < _CACHE_TTL:
                return cached_info

        try:
            info = await asyncio.to_thread(self._fetch_info_sync, symbol)

            result = {
                "ticker": code,
                "name": name or info.get("shortName", code),
                "market": market,
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice", 0),
                "market_cap": info.get("marketCap", 0),
                "per": info.get("trailingPE") or info.get("forwardPE", 0) or 0,
                "pbr": info.get("priceToBook", 0) or 0,
                "roe": (info.get("returnOnEquity") or 0) * 100,
                "debt_ratio": (info.get("debtToEquity") or 0),
                "dividend_yield": (info.get("dividendYield") or 0) * 100,
                "consensus_target": info.get("targetMeanPrice", 0) or 0,
                "52w_high": info.get("fiftyTwoWeekHigh", 0) or 0,
                "52w_low": info.get("fiftyTwoWeekLow", 0) or 0,
                "beta": info.get("beta", 1.0) or 1.0,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
            }
            _info_cache[symbol] = (now, result)
            return result

        except Exception as e:
            logger.warning("yfinance info failed for %s: %s", symbol, e)
            if symbol in _info_cache:
                return _info_cache[symbol][1]
            # v9.3.2: Naver Finance에서 실제 재무정보 폴백
            return await self._naver_fallback_info(code, name, market)

    @staticmethod
    def _fetch_info_sync(symbol: str) -> dict:
        """Synchronous yfinance info fetch - runs in thread pool."""
        ticker = yf.Ticker(symbol)
        return ticker.info or {}

    async def get_current_price(self, code: str, market: str = "KOSPI") -> float:
        """Get current price from yfinance (v4.0: circuit breaker + Naver fallback)."""
        symbol = _yf_ticker(code, market)

        # 서킷 브레이커 체크
        if _yf_breaker and not _yf_breaker.can_execute():
            # Naver 폴백
            price = await self._naver_fallback_price(code)
            if price > 0:
                return price
            if symbol in _price_cache:
                df = _price_cache[symbol][1]
                if not df.empty:
                    return float(df["close"].iloc[-1])
            return 0.0

        try:
            hist = await asyncio.to_thread(self._fetch_ohlcv_sync, symbol, "1d")
            if not hist.empty:
                if _yf_breaker:
                    _yf_breaker.record_success()
                return float(hist["Close"].iloc[-1])
        except Exception:
            logger.debug("get_current_price: yfinance fetch failed for %s", symbol, exc_info=True)
            if _yf_breaker:
                _yf_breaker.record_failure()

        # Fallback chain: cache → Naver
        if symbol in _price_cache:
            df = _price_cache[symbol][1]
            if not df.empty:
                return float(df["close"].iloc[-1])

        return await self._naver_fallback_price(code)

    async def _naver_fallback_price(self, code: str) -> float:
        """Naver Finance 가격 폴백."""
        try:
            from kstock.ingest.naver_finance import NaverFinanceClient
            naver = NaverFinanceClient()
            return await naver.get_current_price(code)
        except Exception:
            logger.debug("_naver_fallback_price: Naver price fallback failed", exc_info=True)
            return 0.0

    async def _naver_fallback_info(self, code: str, name: str, market: str) -> dict:
        """Naver Finance에서 실제 재무정보 폴백 (PER/PBR/ROE/시총 등)."""
        try:
            from kstock.ingest.naver_finance import NaverFinanceClient
            naver = NaverFinanceClient()
            info = await naver.get_stock_info(code, name)
            if info.get("current_price", 0) > 0 or info.get("per", 0) > 0:
                info["market"] = market
                logger.info("Naver info fallback OK for %s: price=%s PER=%s",
                            code, info.get("current_price"), info.get("per"))
                return info
        except Exception:
            logger.debug("_naver_fallback_info: Naver info fallback failed for %s", code, exc_info=True)
        # 최후: 빈 정보 (가짜 데이터 생성 안 함)
        logger.warning("Info 완전 실패 %s: yfinance+Naver 모두 재무정보 없음", code)
        return {
            "ticker": code, "name": name or code, "market": market,
            "current_price": 0, "market_cap": 0, "per": 0, "pbr": 0,
            "roe": 0, "debt_ratio": 0, "dividend_yield": 0,
            "consensus_target": 0, "52w_high": 0, "52w_low": 0,
            "beta": 1.0, "sector": "", "industry": "",
            "_data_source": "none",
        }

    async def _naver_fallback_ohlcv(self, code: str) -> pd.DataFrame:
        """Naver Finance OHLCV 폴백. 실패 시 빈 DataFrame 반환 (가짜 데이터 생성 안 함)."""
        try:
            from kstock.ingest.naver_finance import NaverFinanceClient
            naver = NaverFinanceClient()
            df = await naver.get_ohlcv(code)
            if not df.empty:
                return df
        except Exception:
            logger.debug("_naver_fallback_ohlcv: Naver OHLCV fallback failed for %s", code, exc_info=True)
        logger.warning("OHLCV 완전 실패 %s: yfinance+Naver 모두 데이터 없음 (가짜 데이터 미생성)", code)
        return pd.DataFrame()

    async def batch_download(
        self, codes: list[dict], period: str = "6mo"
    ) -> dict[str, pd.DataFrame]:
        """Batch download OHLCV for multiple tickers."""
        symbols = []
        code_map = {}
        for item in codes:
            code = item["code"]
            market = item.get("market", "KOSPI")
            symbol = _yf_ticker(code, market)
            symbols.append(symbol)
            code_map[symbol] = code

        result = {}
        try:
            data = await asyncio.to_thread(
                yf.download, symbols, period=period, group_by="ticker", progress=False
            )
            now = datetime.now(KST)
            for symbol in symbols:
                try:
                    if len(symbols) == 1:
                        hist = data
                    else:
                        hist = data[symbol]
                    if hist.empty or hist.dropna(how="all").empty:
                        continue
                    hist = hist.dropna()
                    df = pd.DataFrame({
                        "date": hist.index.strftime("%Y-%m-%d"),
                        "open": hist["Open"].values,
                        "high": hist["High"].values,
                        "low": hist["Low"].values,
                        "close": hist["Close"].values,
                        "volume": hist["Volume"].astype(int).values,
                    }).reset_index(drop=True)
                    code = code_map[symbol]
                    result[code] = df
                    _price_cache[symbol] = (now, df)
                except Exception:
                    logger.debug("batch_download: parse failed for symbol %s", symbol, exc_info=True)
        except Exception as e:
            logger.warning("Batch download failed: %s", e)

        return result



# v9.3.3: Mock data generators removed — all fallbacks use real Naver Finance data.
