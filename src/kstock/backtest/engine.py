"""Simplified backtesting engine for K-Quant strategies.

Downloads 1 year of historical data via yfinance and simulates
the scoring/strategy logic to compute performance metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from kstock.features.technical import compute_indicators
from kstock.signal.scoring import (
    FlowData,
    ScoreBreakdown,
    compute_composite_score,
    load_scoring_config,
)
from kstock.ingest.macro_client import MacroSnapshot

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A single simulated trade."""

    ticker: str
    name: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float
    holding_days: int
    signal_score: float


@dataclass
class BacktestResult:
    """Summary of backtest results."""

    ticker: str
    name: str
    period: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_pnl_pct: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    trades: list[BacktestTrade] = field(default_factory=list)


def _yf_symbol(code: str, market: str = "KOSPI") -> str:
    suffix = ".KS" if market.upper() == "KOSPI" else ".KQ"
    return f"{code}{suffix}"


def _simulate_macro() -> MacroSnapshot:
    """Create a neutral macro snapshot for backtesting."""
    return MacroSnapshot(
        vix=18.0,
        vix_change_pct=0.0,
        spx_change_pct=0.1,
        usdkrw=1300.0,
        usdkrw_change_pct=0.0,
        us10y=4.0,
        dxy=104.0,
        regime="neutral",
    )


def run_backtest(
    code: str,
    name: str = "",
    market: str = "KOSPI",
    period: str = "1y",
    target_pct: float = 3.0,
    stop_pct: float = -5.0,
    lookback: int = 60,
) -> BacktestResult | None:
    """Run backtest for a single ticker.

    Downloads historical data, applies scoring at each point,
    and simulates trades based on BUY signals.

    Args:
        code: Stock code (e.g., "005930")
        name: Stock name
        market: KOSPI or KOSDAQ
        period: yfinance period string (e.g., "1y", "2y")
        target_pct: Take-profit percentage
        stop_pct: Stop-loss percentage (negative)
        lookback: Minimum bars needed for indicators
    """
    symbol = _yf_symbol(code, market)
    name = name or code

    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist.empty or len(hist) < lookback + 20:
            logger.warning("Insufficient data for backtest: %s (%d bars)", symbol, len(hist))
            return None
    except Exception as e:
        logger.error("Failed to download data for %s: %s", symbol, e)
        return None

    df = pd.DataFrame({
        "date": hist.index.strftime("%Y-%m-%d"),
        "open": hist["Open"].values,
        "high": hist["High"].values,
        "low": hist["Low"].values,
        "close": hist["Close"].values,
        "volume": hist["Volume"].astype(int).values,
    }).reset_index(drop=True)

    config = load_scoring_config()
    macro = _simulate_macro()
    flow = FlowData(foreign_net_buy_days=0, institution_net_buy_days=0, avg_trade_value_krw=5e9)

    trades: list[BacktestTrade] = []
    in_trade = False
    entry_price = 0.0
    entry_date = ""
    entry_idx = 0

    closes = df["close"].astype(float).values

    for i in range(lookback, len(df) - 1):
        window = df.iloc[:i + 1].copy()

        if in_trade:
            current = closes[i]
            pnl = (current - entry_price) / entry_price * 100
            days_held = i - entry_idx

            # Exit conditions
            if pnl >= target_pct or pnl <= stop_pct or days_held >= 20:
                trades.append(BacktestTrade(
                    ticker=code,
                    name=name,
                    entry_date=entry_date,
                    entry_price=round(entry_price, 0),
                    exit_date=df["date"].iloc[i],
                    exit_price=round(current, 0),
                    pnl_pct=round(pnl, 2),
                    holding_days=days_held,
                    signal_score=0,
                ))
                in_trade = False
            continue

        # Check for entry signal
        try:
            from kstock.ingest.kis_client import StockInfo
            tech = compute_indicators(window)
            info = StockInfo(
                ticker=code, name=name, market=market,
                market_cap=1e13, per=15, roe=12,
                debt_ratio=80, consensus_target=closes[i] * 1.1,
                current_price=closes[i],
            )
            score = compute_composite_score(macro, flow, info, tech, config)

            if score.signal == "BUY":
                buy_trigger = (
                    tech.rsi <= 30
                    or tech.bb_pctb <= 0.2
                    or tech.macd_signal_cross == 1
                )
                if buy_trigger:
                    entry_price = closes[i + 1]  # buy next day open approximation
                    entry_date = df["date"].iloc[i + 1]
                    entry_idx = i + 1
                    in_trade = True
                    trades[-1:] and None  # no-op
        except Exception:
            continue

    # Close any remaining trade
    if in_trade:
        current = closes[-1]
        pnl = (current - entry_price) / entry_price * 100
        trades.append(BacktestTrade(
            ticker=code, name=name,
            entry_date=entry_date, entry_price=round(entry_price, 0),
            exit_date=df["date"].iloc[-1], exit_price=round(current, 0),
            pnl_pct=round(pnl, 2),
            holding_days=len(df) - 1 - entry_idx,
            signal_score=0,
        ))

    # Compute metrics
    if not trades:
        return BacktestResult(
            ticker=code, name=name,
            period=period, total_trades=0,
            winning_trades=0, losing_trades=0,
            win_rate=0, avg_pnl_pct=0,
            total_return_pct=0, max_drawdown_pct=0,
            sharpe_ratio=0, profit_factor=0,
            trades=[],
        )

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_pnl = np.mean(pnls) if pnls else 0

    # Total return (compound)
    total_return = 1.0
    for p in pnls:
        total_return *= (1 + p / 100)
    total_return = (total_return - 1) * 100

    # Max drawdown
    cumulative = np.cumprod([1 + p / 100 for p in pnls])
    peak = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - peak) / peak * 100
    max_dd = float(np.min(drawdown)) if len(drawdown) > 0 else 0

    # Sharpe ratio (simplified, daily)
    sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252 / 10)) if len(pnls) > 1 and np.std(pnls) > 0 else 0

    # Profit factor
    total_wins = sum(wins) if wins else 0
    total_losses = abs(sum(losses)) if losses else 1
    profit_factor = total_wins / total_losses if total_losses > 0 else total_wins

    period_str = f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}"

    return BacktestResult(
        ticker=code,
        name=name,
        period=period_str,
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(win_rate, 1),
        avg_pnl_pct=round(float(avg_pnl), 2),
        total_return_pct=round(total_return, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        profit_factor=round(profit_factor, 2),
        trades=trades,
    )


def format_backtest_result(result: BacktestResult) -> str:
    """Format backtest result for Telegram display."""
    if result.total_trades == 0:
        return (
            f"\U0001f4ca {result.name} \ubc31\ud14c\uc2a4\ud2b8\n\n"
            f"\uae30\uac04: {result.period}\n"
            f"\uac70\ub798 \uc2e0\ud638 \uc5c6\uc74c (BUY \uc870\uac74 \ubbf8\ucda9\uc871)"
        )

    pnl_emoji = "\U0001f7e2" if result.total_return_pct > 0 else "\U0001f534"

    lines = [
        f"\U0001f4ca {result.name} \ubc31\ud14c\uc2a4\ud2b8 \uacb0\uacfc",
        "\u2500" * 25,
        f"\uae30\uac04: {result.period}",
        "",
        f"{pnl_emoji} \ucd1d \uc218\uc775\ub960: {result.total_return_pct:+.1f}%",
        f"\U0001f4b0 \ud3c9\uade0 \uc218\uc775: {result.avg_pnl_pct:+.1f}%",
        f"\U0001f3af \uc2b9\ub960: {result.win_rate:.0f}% "
        f"({result.winning_trades}\uc2b9 {result.losing_trades}\ud328)",
        f"\U0001f4c9 \ucd5c\ub300 \ub0a8\ud3ed: {result.max_drawdown_pct:.1f}%",
        f"\U0001f4ca \uc0e4\ud504\ube44\uc728: {result.sharpe_ratio:.2f}",
        f"\u2696\ufe0f Profit Factor: {result.profit_factor:.2f}",
        "",
        "\u2500" * 25,
        f"\ucd1d {result.total_trades}\ud68c \uac70\ub798",
    ]

    # Show last 3 trades
    if result.trades:
        lines.append("")
        lines.append("\ucd5c\uadfc \uac70\ub798:")
        for t in result.trades[-3:]:
            emoji = "\U0001f7e2" if t.pnl_pct > 0 else "\U0001f534"
            lines.append(
                f"  {emoji} {t.entry_date} \u2192 {t.exit_date} "
                f"{t.pnl_pct:+.1f}% ({t.holding_days}\uc77c)"
            )

    return "\n".join(lines)
