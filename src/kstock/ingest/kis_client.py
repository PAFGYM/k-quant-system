"""KIS OpenAPI client for Korean stock data.

MVP uses mock data. Real endpoint documentation included for future implementation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()


@dataclass
class StockInfo:
    """Basic stock information."""

    ticker: str
    name: str
    market: str
    market_cap: float
    per: float
    roe: float
    debt_ratio: float
    consensus_target: float
    current_price: float


# Real KIS API endpoints (for future implementation)
# Base URL: https://openapi.koreainvestment.com:9443
# Auth:     POST /oauth2/tokenP
# Price:    GET  /uapi/domestic-stock/v1/quotations/inquire-price
# Daily:    GET  /uapi/domestic-stock/v1/quotations/inquire-daily-price
# Foreign:  GET  /uapi/domestic-stock/v1/quotations/inquire-investor


class KISClient:
    """KIS OpenAPI client (mock for MVP)."""

    def __init__(self) -> None:
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.base_url = "https://openapi.koreainvestment.com:9443"

    async def get_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        """Fetch OHLCV data for a ticker (mock for MVP)."""
        return _generate_mock_ohlcv(ticker, days)

    async def get_stock_info(self, ticker: str, name: str = "") -> StockInfo:
        """Fetch stock fundamental info (mock for MVP)."""
        return _generate_mock_stock_info(ticker, name)

    async def get_foreign_flow(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """Fetch foreign investor flow data (mock for MVP)."""
        return _generate_mock_foreign_flow(ticker, days)

    async def get_institution_flow(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """Fetch institutional investor flow data (mock for MVP)."""
        return _generate_mock_institution_flow(ticker, days)

    async def get_current_price(self, ticker: str, base_price: float = 0) -> float:
        """Get simulated current price for monitoring (mock).

        Uses hour-based seed for hourly price changes to simulate movement.
        """
        if base_price > 0:
            hour_seed = int(datetime.now().strftime("%Y%m%d%H"))
            rng = np.random.default_rng(
                seed=(hash(ticker) + hour_seed) % (2**31)
            )
            change_pct = rng.normal(0.005, 0.02)
            return round(base_price * (1 + change_pct), 0)
        ohlcv = await self.get_ohlcv(ticker, days=5)
        return float(ohlcv["close"].iloc[-1])


def _generate_mock_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    """Generate realistic mock OHLCV data."""
    rng = np.random.default_rng(seed=hash(ticker) % (2**31))
    dates = pd.bdate_range(end=datetime.now(), periods=days)

    base_price = 50000 + rng.integers(0, 100000)
    returns = rng.normal(0.0005, 0.02, size=days)
    prices = base_price * np.cumprod(1 + returns)

    df = pd.DataFrame(
        {
            "date": dates,
            "open": prices * (1 + rng.uniform(-0.01, 0.01, days)),
            "high": prices * (1 + rng.uniform(0.005, 0.03, days)),
            "low": prices * (1 - rng.uniform(0.005, 0.03, days)),
            "close": prices,
            "volume": rng.integers(100_000, 5_000_000, size=days),
        }
    )
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df.round(0).astype({"volume": int})


def _generate_mock_stock_info(ticker: str, name: str) -> StockInfo:
    """Generate mock fundamental data."""
    rng = np.random.default_rng(seed=hash(ticker) % (2**31))
    current_price = 50000 + rng.integers(0, 100000)
    return StockInfo(
        ticker=ticker,
        name=name or ticker,
        market="KOSPI",
        market_cap=float(rng.integers(1_000_000_000_000, 500_000_000_000_000)),
        per=float(rng.uniform(5, 30)),
        roe=float(rng.uniform(3, 25)),
        debt_ratio=float(rng.uniform(20, 250)),
        consensus_target=float(current_price * rng.uniform(0.9, 1.3)),
        current_price=float(current_price),
    )


def _generate_mock_foreign_flow(ticker: str, days: int) -> pd.DataFrame:
    """Generate mock foreign investor flow."""
    rng = np.random.default_rng(seed=hash(ticker + "foreign") % (2**31))
    dates = pd.bdate_range(end=datetime.now(), periods=days)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "net_buy_volume": rng.integers(-500_000, 500_000, size=days),
            "net_buy_amount": rng.integers(-50_000_000_000, 50_000_000_000, size=days),
        }
    )


def _generate_mock_institution_flow(ticker: str, days: int) -> pd.DataFrame:
    """Generate mock institutional investor flow."""
    rng = np.random.default_rng(seed=hash(ticker + "inst") % (2**31))
    dates = pd.bdate_range(end=datetime.now(), periods=days)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "net_buy_volume": rng.integers(-300_000, 300_000, size=days),
            "net_buy_amount": rng.integers(-30_000_000_000, 30_000_000_000, size=days),
        }
    )
