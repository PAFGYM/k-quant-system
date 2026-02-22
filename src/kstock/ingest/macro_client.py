"""Macro data client using yfinance (FRED optional)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class MacroSnapshot:
    """Current macro environment snapshot."""

    vix: float
    vix_change_pct: float
    spx_change_pct: float
    usdkrw: float
    usdkrw_change_pct: float
    us10y: float
    dxy: float
    regime: str  # risk_on, neutral, risk_off
    # Extended fields (defaults for backward compat)
    nasdaq_change_pct: float = 0.0
    btc_price: float = 0.0
    btc_change_pct: float = 0.0
    institution_total: float = 0.0
    foreign_total: float = 0.0
    gold_price: float = 0.0
    gold_change_pct: float = 0.0


class MacroClient:
    """Client for fetching macro indicators."""

    def __init__(self) -> None:
        self.fred_api_key = os.getenv("FRED_API_KEY", "")

    async def get_snapshot(self) -> MacroSnapshot:
        """Fetch current macro snapshot. Uses yfinance with fallback to mock."""
        try:
            return self._fetch_live_snapshot()
        except Exception as e:
            logger.warning("Live macro fetch failed (%s), using mock", e)
            return self._generate_mock_snapshot()

    def _fetch_live_snapshot(self) -> MacroSnapshot:
        """Fetch live macro data from yfinance."""
        tickers = yf.Tickers("^VIX ^GSPC ^IXIC KRW=X ^TNX DX-Y.NYB BTC-USD GC=F")

        vix_hist = tickers.tickers["^VIX"].history(period="5d")
        spx_hist = tickers.tickers["^GSPC"].history(period="5d")
        ndx_hist = tickers.tickers["^IXIC"].history(period="5d")
        krw_hist = tickers.tickers["KRW=X"].history(period="5d")
        tny_hist = tickers.tickers["^TNX"].history(period="5d")
        dxy_hist = tickers.tickers["DX-Y.NYB"].history(period="5d")
        btc_hist = tickers.tickers["BTC-USD"].history(period="5d")
        gold_hist = tickers.tickers["GC=F"].history(period="5d")

        vix = float(vix_hist["Close"].iloc[-1])
        vix_prev = float(vix_hist["Close"].iloc[-2])
        vix_change = (vix - vix_prev) / vix_prev * 100

        spx = float(spx_hist["Close"].iloc[-1])
        spx_prev = float(spx_hist["Close"].iloc[-2])
        spx_change = (spx - spx_prev) / spx_prev * 100

        ndx = float(ndx_hist["Close"].iloc[-1])
        ndx_prev = float(ndx_hist["Close"].iloc[-2])
        ndx_change = (ndx - ndx_prev) / ndx_prev * 100

        usdkrw = float(krw_hist["Close"].iloc[-1])
        usdkrw_prev = float(krw_hist["Close"].iloc[-2])
        usdkrw_change = (usdkrw - usdkrw_prev) / usdkrw_prev * 100

        us10y = float(tny_hist["Close"].iloc[-1])
        dxy = float(dxy_hist["Close"].iloc[-1])

        btc = float(btc_hist["Close"].iloc[-1])
        btc_prev = float(btc_hist["Close"].iloc[-2])
        btc_change = (btc - btc_prev) / btc_prev * 100

        gold = float(gold_hist["Close"].iloc[-1]) if not gold_hist.empty else 0
        gold_prev = float(gold_hist["Close"].iloc[-2]) if len(gold_hist) >= 2 else gold
        gold_change = (gold - gold_prev) / gold_prev * 100 if gold_prev > 0 else 0

        regime = self._classify_regime(spx_change, vix, usdkrw_change)

        # Mock institutional/foreign totals (KIS API required for real data)
        rng = np.random.default_rng(seed=int(vix * 100) % (2**31))
        inst_total = float(rng.integers(-2_000_000_000_000, 2_000_000_000_000))
        foreign_total = float(rng.integers(-1_000_000_000_000, 1_000_000_000_000))

        return MacroSnapshot(
            vix=round(vix, 2),
            vix_change_pct=round(vix_change, 2),
            spx_change_pct=round(spx_change, 2),
            usdkrw=round(usdkrw, 2),
            usdkrw_change_pct=round(usdkrw_change, 2),
            us10y=round(us10y, 2),
            dxy=round(dxy, 2),
            regime=regime,
            nasdaq_change_pct=round(ndx_change, 2),
            btc_price=round(btc, 0),
            btc_change_pct=round(btc_change, 2),
            institution_total=inst_total,
            foreign_total=foreign_total,
            gold_price=round(gold, 0),
            gold_change_pct=round(gold_change, 2),
        )

    @staticmethod
    def _generate_mock_snapshot() -> MacroSnapshot:
        """Generate mock macro data for testing."""
        rng = np.random.default_rng(seed=42)
        vix = float(rng.uniform(12, 30))
        spx_change = float(rng.normal(0, 1))
        nasdaq_change = float(rng.normal(0, 1.2))
        usdkrw = float(rng.uniform(1200, 1450))
        usdkrw_change = float(rng.normal(0, 0.5))
        btc = float(rng.uniform(50000, 80000))
        btc_change = float(rng.normal(0, 2))
        inst_total = float(rng.integers(-2_000_000_000_000, 2_000_000_000_000))
        foreign_total = float(rng.integers(-1_000_000_000_000, 1_000_000_000_000))
        gold = float(rng.uniform(1800, 2500))
        gold_change = float(rng.normal(0, 1))

        regime = MacroClient._classify_regime(spx_change, vix, usdkrw_change)

        return MacroSnapshot(
            vix=round(vix, 2),
            vix_change_pct=round(float(rng.normal(0, 5)), 2),
            spx_change_pct=round(spx_change, 2),
            usdkrw=round(usdkrw, 2),
            usdkrw_change_pct=round(usdkrw_change, 2),
            us10y=round(float(rng.uniform(3.5, 5.0)), 2),
            dxy=round(float(rng.uniform(100, 110)), 2),
            regime=regime,
            nasdaq_change_pct=round(nasdaq_change, 2),
            btc_price=round(btc, 0),
            btc_change_pct=round(btc_change, 2),
            institution_total=inst_total,
            foreign_total=foreign_total,
            gold_price=round(gold, 0),
            gold_change_pct=round(gold_change, 2),
        )

    @staticmethod
    def _classify_regime(
        spx_change_pct: float, vix: float, usdkrw_change_pct: float
    ) -> str:
        """Classify macro regime."""
        risk_off_signals = 0
        if spx_change_pct < -1.0:
            risk_off_signals += 1
        if vix > 25:
            risk_off_signals += 1
        if usdkrw_change_pct > 0.5:
            risk_off_signals += 1

        if risk_off_signals >= 2:
            return "risk_off"
        elif risk_off_signals == 0 and vix < 18 and spx_change_pct > 0:
            return "risk_on"
        return "neutral"
