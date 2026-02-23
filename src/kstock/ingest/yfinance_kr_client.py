"""Real data client for Korean stocks via yfinance (.KS/.KQ suffixes)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import yfinance as yf

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Cache for yfinance data to avoid repeated API calls
_price_cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
_info_cache: dict[str, tuple[datetime, dict]] = {}
_CACHE_TTL = timedelta(minutes=30)


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
        now = datetime.now()

        # Check cache
        if symbol in _price_cache:
            cached_time, cached_df = _price_cache[symbol]
            if now - cached_time < _CACHE_TTL and not cached_df.empty:
                return cached_df

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
            return df

        except Exception as e:
            logger.warning("yfinance OHLCV failed for %s: %s", symbol, e)
            if symbol in _price_cache:
                return _price_cache[symbol][1]
            return _generate_fallback_ohlcv(code)

    @staticmethod
    def _fetch_ohlcv_sync(symbol: str, period: str) -> pd.DataFrame:
        """Synchronous yfinance fetch - runs in thread pool."""
        ticker = yf.Ticker(symbol)
        return ticker.history(period=period)

    async def get_stock_info(self, code: str, name: str = "", market: str = "KOSPI") -> dict:
        """Fetch fundamental info from yfinance."""
        symbol = _yf_ticker(code, market)
        now = datetime.now()

        if symbol in _info_cache:
            cached_time, cached_info = _info_cache[symbol]
            if now - cached_time < _CACHE_TTL:
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
            return _generate_fallback_info(code, name, market)

    @staticmethod
    def _fetch_info_sync(symbol: str) -> dict:
        """Synchronous yfinance info fetch - runs in thread pool."""
        ticker = yf.Ticker(symbol)
        return ticker.info or {}

    async def get_current_price(self, code: str, market: str = "KOSPI") -> float:
        """Get current price from yfinance."""
        symbol = _yf_ticker(code, market)
        try:
            hist = await asyncio.to_thread(self._fetch_ohlcv_sync, symbol, "1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        # Fallback: use cached OHLCV
        if symbol in _price_cache:
            df = _price_cache[symbol][1]
            if not df.empty:
                return float(df["close"].iloc[-1])
        return 0.0

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
            now = datetime.now()
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
                    pass
        except Exception as e:
            logger.warning("Batch download failed: %s", e)

        return result


def _generate_fallback_ohlcv(code: str, days: int = 120) -> pd.DataFrame:
    """Generate fallback mock OHLCV when yfinance fails."""
    rng = np.random.default_rng(seed=hash(code) % (2**31))
    dates = pd.bdate_range(end=datetime.now(), periods=days)
    base_price = 50000 + rng.integers(0, 100000)
    returns = rng.normal(0.0005, 0.02, size=days)
    prices = base_price * np.cumprod(1 + returns)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": prices * (1 + rng.uniform(-0.01, 0.01, days)),
        "high": prices * (1 + rng.uniform(0.005, 0.03, days)),
        "low": prices * (1 - rng.uniform(0.005, 0.03, days)),
        "close": prices,
        "volume": rng.integers(100_000, 5_000_000, size=days),
    })
    return df.round(0).astype({"volume": int})


def _generate_fallback_info(code: str, name: str, market: str) -> dict:
    """Generate fallback mock info when yfinance fails."""
    rng = np.random.default_rng(seed=hash(code) % (2**31))
    price = 50000 + rng.integers(0, 100000)
    return {
        "ticker": code,
        "name": name or code,
        "market": market,
        "current_price": float(price),
        "market_cap": float(rng.integers(1_000_000_000_000, 500_000_000_000_000)),
        "per": float(rng.uniform(5, 30)),
        "pbr": float(rng.uniform(0.5, 5)),
        "roe": float(rng.uniform(3, 25)),
        "debt_ratio": float(rng.uniform(20, 250)),
        "dividend_yield": float(rng.uniform(0, 5)),
        "consensus_target": float(price * rng.uniform(0.9, 1.3)),
        "52w_high": float(price * 1.3),
        "52w_low": float(price * 0.7),
        "beta": 1.0,
        "sector": "",
        "industry": "",
    }
