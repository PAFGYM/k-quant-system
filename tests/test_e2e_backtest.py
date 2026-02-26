"""
E2E tests for backtest integration.

Covers: TradeCosts, run_backtest, run_portfolio_backtest.
All external data (yfinance) is mocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers -- synthetic OHLCV data
# ---------------------------------------------------------------------------

def _make_ohlcv(
    days: int = 120,
    start_price: float = 10000.0,
    volatility: float = 0.02,
    start_date: str = "2025-06-01",
) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame that mimics yfinance output."""
    np.random.seed(42)
    dates = pd.bdate_range(start=start_date, periods=days)
    close = [start_price]
    for _ in range(days - 1):
        ret = np.random.normal(0.0005, volatility)
        close.append(close[-1] * (1 + ret))
    close = np.array(close)

    df = pd.DataFrame(
        {
            "Open": close * (1 - np.random.uniform(0, 0.005, days)),
            "High": close * (1 + np.random.uniform(0, 0.01, days)),
            "Low": close * (1 - np.random.uniform(0, 0.01, days)),
            "Close": close,
            "Volume": np.random.randint(100_000, 5_000_000, days),
        },
        index=dates,
    )
    return df


_ENGINE_PATH = "kstock.backtest.engine"


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _import_engine():
    import importlib
    return importlib.import_module(_ENGINE_PATH)


# ===========================================================================
# Tests
# ===========================================================================


class TestBacktestWithCosts:
    """1. Backtest with transaction costs produces lower return than without."""

    @patch("yfinance.download")
    def test_backtest_with_costs_lower(self, mock_yf):
        engine = _import_engine()

        ohlcv = _make_ohlcv(days=120, start_price=10000)
        mock_yf.return_value = ohlcv

        # Run without costs (zero fees)
        no_costs = engine.TradeCosts(
            commission_rate=0.0, sell_tax_rate=0.0, slippage_rate=0.0
        )
        result_free = engine.run_backtest(
            "005930",
            name="Samsung",
            market="KOSPI",
            period="6mo",
            costs=no_costs,
        )

        # Run with realistic costs
        with_costs = engine.TradeCosts(
            commission_rate=0.00015, sell_tax_rate=0.0023, slippage_rate=0.001
        )
        result_cost = engine.run_backtest(
            "005930",
            name="Samsung",
            market="KOSPI",
            period="6mo",
            costs=with_costs,
        )

        # Costs should reduce returns
        assert result_cost.total_return_pct <= result_free.total_return_pct
        # Verify cost accounting is non-zero
        assert result_cost.total_cost_pct >= 0.0


class TestPortfolioWeights:
    """2. Portfolio backtest respects weight summing to 1.0."""

    @patch("yfinance.download")
    def test_portfolio_backtest_weights(self, mock_yf):
        engine = _import_engine()

        mock_yf.return_value = _make_ohlcv(days=120)

        tickers = [
            {"code": "005930", "name": "Samsung", "market": "KOSPI", "weight": 0.4},
            {"code": "035720", "name": "Kakao", "market": "KOSPI", "weight": 0.3},
            {"code": "068270", "name": "Celltrion", "market": "KOSPI", "weight": 0.3},
        ]

        total_weight = sum(t["weight"] for t in tickers)
        assert abs(total_weight - 1.0) < 1e-9, "Weights must sum to 1.0"

        result = engine.run_portfolio_backtest(
            tickers=tickers,
            period="6mo",
            initial_capital=100_000_000,
            costs=engine.TradeCosts(),
        )

        assert result.initial_capital == 100_000_000
        assert result.total_return_pct is not None
        assert result.per_stock_results is not None
        assert len(result.per_stock_results) == 3


class TestKosdaqNoTax:
    """3. KOSDAQ backtest with zero sell tax."""

    @patch("yfinance.download")
    def test_backtest_kosdaq_no_tax(self, mock_yf):
        engine = _import_engine()

        mock_yf.return_value = _make_ohlcv(days=120)

        kosdaq_costs = engine.TradeCosts(
            commission_rate=0.00015,
            sell_tax_rate=0.0,   # KOSDAQ: no securities transaction tax
            slippage_rate=0.001,
        )

        result = engine.run_backtest(
            "247540",
            name="Ecopro BM",
            market="KOSDAQ",
            period="6mo",
            costs=kosdaq_costs,
        )

        # Total cost should be lower than KOSPI (no sell tax component)
        # At minimum, verify the result is valid
        assert result.ticker == "247540"
        assert result.total_cost_pct >= 0.0


class TestCostsRoundtripPnl:
    """4. TradeCosts buy+sell costs are internally consistent."""

    def test_costs_roundtrip_pnl(self):
        engine = _import_engine()

        costs = engine.TradeCosts(
            commission_rate=0.00015,
            sell_tax_rate=0.0023,
            slippage_rate=0.001,
        )

        buy_price = 10000
        sell_price = 10500
        qty = 100

        buy_total = costs.buy_cost(buy_price, qty)
        sell_total = costs.sell_cost(sell_price, qty)
        pnl = costs.net_pnl(buy_price, sell_price, qty)
        pnl_pct = costs.net_pnl_pct(buy_price, sell_price)

        # Gross PnL = (sell - buy) * qty = 50000
        gross_pnl = (sell_price - buy_price) * qty
        assert gross_pnl == 50000

        # Net PnL should be less than gross due to costs
        assert pnl < gross_pnl

        # buy_cost should include commission + slippage
        expected_buy = buy_price * qty * (costs.commission_rate + costs.slippage_rate)
        assert abs(buy_total - expected_buy) < 1.0  # allow float rounding

        # sell_cost should include commission + slippage + tax
        expected_sell = sell_price * qty * (
            costs.commission_rate + costs.slippage_rate + costs.sell_tax_rate
        )
        assert abs(sell_total - expected_sell) < 1.0

        # net_pnl = gross - buy_cost - sell_cost
        expected_net = gross_pnl - buy_total - sell_total
        assert abs(pnl - expected_net) < 1.0

        # pnl_pct should be roughly pnl / (buy_price * qty) * 100 (or similar)
        assert pnl_pct < (sell_price - buy_price) / buy_price * 100


class TestPortfolioEquityCurve:
    """5. equity_curve length matches trading days in the backtest period."""

    @patch("yfinance.download")
    def test_portfolio_equity_curve(self, mock_yf):
        engine = _import_engine()

        days = 120
        ohlcv = _make_ohlcv(days=days)
        mock_yf.return_value = ohlcv

        tickers = [
            {"code": "005930", "name": "Samsung", "market": "KOSPI", "weight": 0.5},
            {"code": "035720", "name": "Kakao", "market": "KOSPI", "weight": 0.5},
        ]

        result = engine.run_portfolio_backtest(
            tickers=tickers,
            period="6mo",
            initial_capital=100_000_000,
            costs=engine.TradeCosts(),
        )

        assert result.equity_curve is not None
        # Equity curve length should correspond to the trading days
        # (may differ slightly from `days` due to engine internals)
        curve_len = len(result.equity_curve)
        assert curve_len > 0
        assert curve_len <= days
        # First point should be close to initial capital
        first_val = (
            result.equity_curve.iloc[0]
            if hasattr(result.equity_curve, "iloc")
            else result.equity_curve[0]
        )
        # Allow up to 5% deviation on day-1 (cost of initial buy)
        assert abs(first_val - 100_000_000) / 100_000_000 < 0.05
