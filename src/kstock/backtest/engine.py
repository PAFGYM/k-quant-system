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
class TradeCosts:
    """한국 주식 거래 비용 모델."""
    commission_rate: float = 0.00015    # KIS 수수료 0.015%
    sell_tax_rate: float = 0.0023       # 매도세 0.23% (코스피)
    slippage_rate: float = 0.001        # 슬리피지 0.1%

    def buy_cost(self, price: float, quantity: int) -> float:
        amount = price * quantity
        return amount * (self.commission_rate + self.slippage_rate)

    def sell_cost(self, price: float, quantity: int) -> float:
        amount = price * quantity
        return amount * (self.commission_rate + self.sell_tax_rate + self.slippage_rate)

    def net_pnl(self, buy_price: float, sell_price: float, quantity: int) -> float:
        gross = (sell_price - buy_price) * quantity
        costs = self.buy_cost(buy_price, quantity) + self.sell_cost(sell_price, quantity)
        return gross - costs

    def net_pnl_pct(self, buy_price: float, sell_price: float) -> float:
        gross_pct = (sell_price - buy_price) / buy_price * 100
        cost_pct = (self.commission_rate * 2 + self.sell_tax_rate + self.slippage_rate * 2) * 100
        return gross_pct - cost_pct


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
    total_cost_pct: float = 0.0
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
    costs: TradeCosts | None = None,
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
            if costs:
                pnl = costs.net_pnl_pct(entry_price, current)
            else:
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
        if costs:
            pnl = costs.net_pnl_pct(entry_price, current)
        else:
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

    total_cost_pct = 0.0
    if costs:
        cost_per_trade = (costs.commission_rate * 2 + costs.sell_tax_rate + costs.slippage_rate * 2) * 100
        total_cost_pct = cost_per_trade * len(trades)

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
        total_cost_pct=round(total_cost_pct, 2),
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


@dataclass
class PortfolioBacktestResult:
    period: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_holding_days: float
    total_cost_pct: float
    per_stock_results: list[BacktestResult] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


def run_portfolio_backtest(
    tickers: list[dict],
    period: str = "1y",
    initial_capital: float = 10_000_000,
    costs: TradeCosts | None = None,
    rebalance_days: int = 0,
) -> PortfolioBacktestResult | None:
    """Run portfolio-level backtest.

    tickers: [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "weight": 0.4}, ...]
    """
    if not tickers:
        return None

    # Normalize weights
    total_weight = sum(t.get("weight", 1.0 / len(tickers)) for t in tickers)

    per_stock = []
    all_pnls = []
    total_cost = 0.0
    total_holding_days = 0

    for t in tickers:
        weight = t.get("weight", 1.0 / len(tickers)) / total_weight
        result = run_backtest(
            code=t["code"],
            name=t.get("name", t["code"]),
            market=t.get("market", "KOSPI"),
            period=period,
            costs=costs,
        )
        if result is None:
            continue
        per_stock.append(result)
        # Weight pnls
        for trade in result.trades:
            all_pnls.append(trade.pnl_pct * weight)
            total_holding_days += trade.holding_days
        total_cost += result.total_cost_pct * weight

    if not per_stock:
        return None

    # Total return
    total_return = 1.0
    for p in all_pnls:
        total_return *= (1 + p / 100)
    total_return_pct = (total_return - 1) * 100

    final_capital = initial_capital * total_return

    # Annualized return (assume period from first result)
    # Approximate trading days from period string
    period_map = {"1y": 252, "2y": 504, "6mo": 126, "3mo": 63}
    trading_days = period_map.get(period, 252)
    annualized = ((1 + total_return_pct / 100) ** (252 / max(trading_days, 1)) - 1) * 100

    # MDD from equity curve
    if all_pnls:
        cumulative = np.cumprod([1 + p / 100 for p in all_pnls])
        equity_curve = (cumulative * initial_capital).tolist()
        peak = np.maximum.accumulate(cumulative)
        dd = (cumulative - peak) / peak * 100
        max_dd = float(np.min(dd))
    else:
        equity_curve = [initial_capital]
        max_dd = 0.0

    # Sharpe
    if len(all_pnls) > 1 and np.std(all_pnls) > 0:
        sharpe = float(np.mean(all_pnls) / np.std(all_pnls) * np.sqrt(252 / 10))
    else:
        sharpe = 0.0

    # Sortino
    downside = [r for r in all_pnls if r < 0]
    if downside:
        downside_std = float(np.std(downside))
        sortino = (annualized / 100) / (downside_std / 100 * np.sqrt(252)) if downside_std > 0 else 0.0
    else:
        sortino = 0.0

    # Calmar
    calmar = annualized / abs(max_dd) if max_dd != 0 else 0.0

    # Win rate & profit factor
    wins = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p <= 0]
    win_rate = len(wins) / len(all_pnls) * 100 if all_pnls else 0.0
    total_wins = sum(wins) if wins else 0
    total_losses_val = abs(sum(losses)) if losses else 1
    profit_factor = total_wins / total_losses_val if total_losses_val > 0 else total_wins

    avg_hd = total_holding_days / len(all_pnls) if all_pnls else 0

    period_str = per_stock[0].period if per_stock else period

    return PortfolioBacktestResult(
        period=period_str,
        initial_capital=initial_capital,
        final_capital=round(final_capital, 0),
        total_return_pct=round(total_return_pct, 2),
        annualized_return_pct=round(annualized, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        calmar_ratio=round(calmar, 2),
        total_trades=len(all_pnls),
        win_rate=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        avg_holding_days=round(avg_hd, 1),
        total_cost_pct=round(total_cost, 2),
        per_stock_results=per_stock,
        equity_curve=equity_curve,
    )


def format_portfolio_backtest(result: PortfolioBacktestResult) -> str:
    pnl_emoji = "\U0001f7e2" if result.total_return_pct > 0 else "\U0001f534"
    init_만 = result.initial_capital / 10000
    final_만 = result.final_capital / 10000

    lines = [
        "\U0001f4ca \ud3ec\ud2b8\ud3f4\ub9ac\uc624 \ubc31\ud14c\uc2a4\ud2b8 \uacb0\uacfc",
        "\u2501" * 22,
        f"\uae30\uac04: {result.period}",
        f"\ucd08\uae30 \uc790\ubcf8: {init_만:,.0f}\ub9cc\uc6d0 \u2192 \ucd5c\uc885: {final_만:,.0f}\ub9cc\uc6d0",
        "",
        f"{pnl_emoji} \ucd1d \uc218\uc775\ub960: {result.total_return_pct:+.1f}% (\uc5f0\ud658\uc0b0 {result.annualized_return_pct:+.1f}%)",
        f"\U0001f4c9 \ucd5c\ub300 \ub0a8\ud3ed: {result.max_drawdown_pct:.1f}%",
        f"\U0001f4ca \uc0e4\ud504\ube44\uc728: {result.sharpe_ratio:.2f}",
        f"\U0001f4ca \uc18c\ub974\ud2f0\ub178: {result.sortino_ratio:.2f}",
        f"\U0001f4ca \uce7c\ub9c8\ube44\uc728: {result.calmar_ratio:.2f}",
        f"\u2696\ufe0f Profit Factor: {result.profit_factor:.2f}",
        "",
        f"\U0001f4b0 \ucd1d \uac70\ub798\ube44\uc6a9: {result.total_cost_pct:.1f}%",
        f"\U0001f504 \uc21c\uc218\uc775\ub960: {result.total_return_pct - result.total_cost_pct:+.1f}% (\ube44\uc6a9 \ucc28\uac10 \ud6c4)",
        "",
        "\uc885\ubaa9\ubcc4:",
    ]

    for r in result.per_stock_results:
        emoji = "\U0001f7e2" if r.total_return_pct > 0 else "\U0001f534"
        lines.append(
            f"  {emoji} {r.name}: {r.total_return_pct:+.1f}% "
            f"({r.winning_trades}\uc2b9 {r.losing_trades}\ud328)"
        )

    return "\n".join(lines)
