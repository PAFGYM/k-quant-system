"""Parameter optimization engine with walk-forward validation.

Grid search over RSI, BB, EMA parameters, then walk-forward
validation to prevent overfitting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import product

import numpy as np
import pandas as pd
import yfinance as yf

from kstock.features.technical import _rsi, _bbands, _macd

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of parameter optimization."""

    rsi_oversold: int
    rsi_overbought: int
    bb_period: int
    bb_std: float
    ema_fast: int
    ema_slow: int
    train_sharpe: float
    test_sharpe: float
    test_win_rate: float
    overfitting_check: str  # "pass" or "fail"
    sharpe_diff_pct: float


@dataclass
class OptimizationRun:
    """A single parameter combination run."""

    params: dict
    sharpe: float
    win_rate: float
    total_return: float
    trades: int


def _backtest_params(
    df: pd.DataFrame,
    rsi_os: int,
    rsi_ob: int,
    bb_period: int,
    bb_std: float,
    ema_fast: int,
    ema_slow: int,
    target_pct: float = 3.0,
    stop_pct: float = -5.0,
) -> OptimizationRun:
    """Run a single backtest with given parameters."""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    rsi_series = _rsi(close, 14)
    bb_lower, bb_mid, bb_upper = _bbands(close, bb_period, bb_std)

    ema_f = close.ewm(span=ema_fast, adjust=False).mean()
    ema_s = close.ewm(span=ema_slow, adjust=False).mean()

    bb_range = bb_upper - bb_lower
    bb_pctb = (close - bb_lower) / bb_range.replace(0, np.nan)

    lookback = max(ema_slow, bb_period) + 5
    trades_pnl = []
    in_trade = False
    entry_price = 0.0
    entry_idx = 0

    closes = close.values
    for i in range(lookback, len(df) - 1):
        if in_trade:
            pnl = (closes[i] - entry_price) / entry_price * 100
            days = i - entry_idx
            if pnl >= target_pct or pnl <= stop_pct or days >= 20:
                trades_pnl.append(pnl)
                in_trade = False
            continue

        rsi_val = rsi_series.iloc[i]
        bb_val = bb_pctb.iloc[i]
        ema_cross = ema_f.iloc[i] > ema_s.iloc[i] and ema_f.iloc[i - 1] <= ema_s.iloc[i - 1]

        if np.isnan(rsi_val) or np.isnan(bb_val):
            continue

        buy_signal = (rsi_val <= rsi_os or bb_val <= 0.2 or ema_cross)
        if buy_signal:
            entry_price = closes[i + 1]
            entry_idx = i + 1
            in_trade = True

    if in_trade:
        pnl = (closes[-1] - entry_price) / entry_price * 100
        trades_pnl.append(pnl)

    if not trades_pnl:
        return OptimizationRun(
            params={}, sharpe=0, win_rate=0, total_return=0, trades=0,
        )

    wins = [p for p in trades_pnl if p > 0]
    win_rate = len(wins) / len(trades_pnl) * 100

    total_ret = 1.0
    for p in trades_pnl:
        total_ret *= (1 + p / 100)
    total_ret = (total_ret - 1) * 100

    std = np.std(trades_pnl)
    sharpe = float(np.mean(trades_pnl) / std * np.sqrt(252 / 10)) if std > 0 else 0

    return OptimizationRun(
        params={
            "rsi_oversold": rsi_os,
            "rsi_overbought": rsi_ob,
            "bb_period": bb_period,
            "bb_std": bb_std,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
        },
        sharpe=round(sharpe, 2),
        win_rate=round(win_rate, 1),
        total_return=round(total_ret, 2),
        trades=len(trades_pnl),
    )


def run_optimization(
    code: str,
    market: str = "KOSPI",
    period: str = "2y",
) -> OptimizationResult | None:
    """Run grid search optimization with walk-forward validation.

    Downloads 2 years of data, uses first 75% for training,
    last 25% for testing.
    """
    suffix = ".KS" if market.upper() == "KOSPI" else ".KQ"
    symbol = f"{code}{suffix}"

    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist.empty or len(hist) < 200:
            return None
    except Exception as e:
        logger.error("Optimization data download failed: %s", e)
        return None

    df = pd.DataFrame({
        "date": hist.index.strftime("%Y-%m-%d"),
        "open": hist["Open"].values,
        "high": hist["High"].values,
        "low": hist["Low"].values,
        "close": hist["Close"].values,
        "volume": hist["Volume"].astype(int).values,
    }).reset_index(drop=True)

    # Split: 75% train, 25% test
    split_idx = int(len(df) * 0.75)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx - 50:].reset_index(drop=True)  # overlap for indicator warmup

    if len(train_df) < 150 or len(test_df) < 50:
        return None

    # Grid search on training data
    rsi_grid = [25, 28, 30, 33, 35]
    bb_period_grid = [15, 20, 25]
    bb_std_grid = [1.5, 2.0, 2.5]
    ema_fast_grid = [10, 21, 50]
    ema_slow_grid = [100, 150, 200]

    best_run = None
    best_sharpe = -999

    for rsi_os in rsi_grid:
        for bb_p, bb_s in product(bb_period_grid, bb_std_grid):
            for ema_f, ema_s in product(ema_fast_grid, ema_slow_grid):
                if ema_f >= ema_s:
                    continue
                run = _backtest_params(
                    train_df, rsi_os, 70, bb_p, bb_s, ema_f, ema_s,
                )
                if run.trades >= 3 and run.sharpe > best_sharpe:
                    best_sharpe = run.sharpe
                    best_run = run

    if not best_run or best_run.sharpe <= 0:
        return None

    # Walk-forward validation on test data
    p = best_run.params
    test_run = _backtest_params(
        test_df,
        p["rsi_oversold"], p["rsi_overbought"],
        p["bb_period"], p["bb_std"],
        p["ema_fast"], p["ema_slow"],
    )

    train_sharpe = best_run.sharpe
    test_sharpe = test_run.sharpe

    if train_sharpe > 0:
        diff_pct = abs(train_sharpe - test_sharpe) / train_sharpe * 100
    else:
        diff_pct = 100

    overfit = "fail" if diff_pct > 30 else "pass"

    return OptimizationResult(
        rsi_oversold=p["rsi_oversold"],
        rsi_overbought=p["rsi_overbought"],
        bb_period=p["bb_period"],
        bb_std=p["bb_std"],
        ema_fast=p["ema_fast"],
        ema_slow=p["ema_slow"],
        train_sharpe=train_sharpe,
        test_sharpe=round(test_sharpe, 2),
        test_win_rate=test_run.win_rate,
        overfitting_check=overfit,
        sharpe_diff_pct=round(diff_pct, 1),
    )


def format_optimization_result(result: OptimizationResult) -> str:
    """Format optimization result for Telegram."""
    check_emoji = "\u2705" if result.overfitting_check == "pass" else "\u26a0\ufe0f"

    return (
        "\u2699\ufe0f 최적화 결과\n"
        "\u2500" * 25 + "\n\n"
        f"RSI 최적값  {result.rsi_oversold} (과매도)\n"
        f"BB 최적값  period {result.bb_period}, std {result.bb_std}\n"
        f"EMA 최적값  fast {result.ema_fast}, slow {result.ema_slow}\n\n"
        f"검증 Sharpe  {result.test_sharpe:.2f} (학습 {result.train_sharpe:.2f})\n"
        f"검증 승률  {result.test_win_rate:.0f}%\n"
        f"과최적화 체크  {check_emoji} {result.overfitting_check} "
        f"(차이 {result.sharpe_diff_pct:.0f}%)"
    )
