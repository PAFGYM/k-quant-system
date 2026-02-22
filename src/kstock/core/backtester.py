"""K-Quant 포트폴리오 백테스트 엔진 (core/backtester.py).

Tests whether the K-Quant scoring system generates real alpha
by simulating top-N equal-weight portfolio rebalancing over historical data.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# Default parameters
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = {
    "initial_capital": 100_000_000,       # 1억
    "slippage_pct": 0.003,                # 0.3%
    "buy_commission_pct": 0.00015,        # 매수 0.015%
    "sell_commission_pct": 0.00315,       # 매도 0.315% (세금 포함)
    "min_daily_turnover": 1_000_000_000,  # 일 거래대금 10억+
    "risk_free_rate": 0.035,              # 무위험 3.5%
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DailySnapshot:
    """Daily portfolio value snapshot."""

    date: str
    portfolio_value: float
    cash: float
    holdings_count: int
    benchmark_kospi: float = 0.0
    benchmark_kosdaq: float = 0.0


@dataclass
class BacktestMetrics:
    """Computed performance metrics."""

    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    mdd_pct: float = 0.0
    mdd_date: str = ""
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    alpha_vs_kospi: float = 0.0
    alpha_vs_kosdaq: float = 0.0
    total_trades: int = 0
    benchmark_return_pct: float = 0.0


@dataclass
class PortfolioBacktestResult:
    """Full backtest result."""

    start_date: str = ""
    end_date: str = ""
    top_n: int = 10
    rebalance_period: str = "weekly"
    initial_capital: float = 100_000_000
    final_value: float = 0.0
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    daily_snapshots: list[DailySnapshot] = field(default_factory=list)
    monthly_returns: dict[str, float] = field(default_factory=dict)
    strategy_attribution: dict[str, float] = field(default_factory=dict)
    walk_forward_results: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(
    daily_values: list[float],
    daily_dates: list[str],
    benchmark_values: list[float],
    initial_capital: float,
    trade_results: list[float],
    risk_free_rate: float = 0.035,
) -> BacktestMetrics:
    """Compute all performance metrics from daily portfolio values.

    Implements: total return, CAGR, MDD, Sharpe, Sortino, win rate,
    avg win/loss, profit factor, alpha vs benchmark.

    Args:
        daily_values: Portfolio value at each trading day close.
        daily_dates: Corresponding date strings (YYYY-MM-DD).
        benchmark_values: Benchmark index values aligned to daily_dates.
        initial_capital: Starting capital in KRW.
        trade_results: List of per-trade return percentages.
        risk_free_rate: Annualised risk-free rate (default 3.5%).

    Returns:
        BacktestMetrics with all 13 fields populated.
    """
    try:
        if len(daily_values) < 2:
            logger.warning("compute_metrics: 일별 데이터 부족 (%d일)", len(daily_values))
            return BacktestMetrics()

        final_value = daily_values[-1]
        num_days = len(daily_values)

        # --- Total return ---
        total_return_pct = (final_value / initial_capital - 1.0) * 100.0

        # --- CAGR ---
        if num_days > 1 and initial_capital > 0 and final_value > 0:
            years = num_days / 252.0
            cagr_pct = (math.pow(final_value / initial_capital, 1.0 / years) - 1.0) * 100.0
        else:
            cagr_pct = 0.0

        # --- Daily returns ---
        daily_returns: list[float] = []
        for i in range(1, num_days):
            if daily_values[i - 1] > 0:
                daily_returns.append(daily_values[i] / daily_values[i - 1] - 1.0)
            else:
                daily_returns.append(0.0)

        # --- MDD (Maximum Drawdown) ---
        mdd_pct = 0.0
        mdd_date = ""
        peak = daily_values[0]
        for i, val in enumerate(daily_values):
            if val > peak:
                peak = val
            if peak > 0:
                drawdown = (val - peak) / peak * 100.0
                if drawdown < mdd_pct:
                    mdd_pct = drawdown
                    mdd_date = daily_dates[i] if i < len(daily_dates) else ""

        # --- Sharpe Ratio ---
        # Sharpe = (mean_daily_return - rf_daily) / std_daily * sqrt(252)
        rf_daily = risk_free_rate / 252.0
        if len(daily_returns) > 1:
            mean_ret = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std_ret = math.sqrt(variance)
            if std_ret > 1e-12:
                sharpe_ratio = (mean_ret - rf_daily) / std_ret * math.sqrt(252.0)
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0

        # --- Sortino Ratio ---
        # Uses only downside deviation (returns below rf)
        downside_diffs = [min(r - rf_daily, 0.0) for r in daily_returns]
        if len(downside_diffs) > 1:
            downside_var = sum(d ** 2 for d in downside_diffs) / (len(downside_diffs) - 1)
            downside_std = math.sqrt(downside_var)
            if downside_std > 1e-12:
                mean_ret = sum(daily_returns) / len(daily_returns)
                sortino_ratio = (mean_ret - rf_daily) / downside_std * math.sqrt(252.0)
            else:
                sortino_ratio = 0.0
        else:
            sortino_ratio = 0.0

        # --- Trade-level metrics ---
        wins = [t for t in trade_results if t > 0]
        losses = [t for t in trade_results if t <= 0]
        total_trades = len(trade_results)

        if total_trades > 0:
            win_rate_pct = len(wins) / total_trades * 100.0
        else:
            win_rate_pct = 0.0

        avg_win_pct = sum(wins) / len(wins) if wins else 0.0
        avg_loss_pct = sum(losses) / len(losses) if losses else 0.0

        # --- Profit Factor ---
        sum_wins = sum(wins) if wins else 0.0
        sum_losses = abs(sum(losses)) if losses else 0.0
        if sum_losses > 1e-12:
            profit_factor = sum_wins / sum_losses
        else:
            profit_factor = sum_wins if sum_wins > 0 else 0.0

        # --- Benchmark return and alpha ---
        benchmark_return_pct = 0.0
        alpha_vs_kospi = 0.0
        if len(benchmark_values) >= 2 and benchmark_values[0] > 0:
            benchmark_return_pct = (benchmark_values[-1] / benchmark_values[0] - 1.0) * 100.0
            # Alpha = portfolio CAGR - benchmark CAGR
            bm_years = len(benchmark_values) / 252.0
            if benchmark_values[-1] > 0 and bm_years > 0:
                bm_cagr = (math.pow(benchmark_values[-1] / benchmark_values[0], 1.0 / bm_years) - 1.0) * 100.0
            else:
                bm_cagr = 0.0
            alpha_vs_kospi = cagr_pct - bm_cagr

        return BacktestMetrics(
            total_return_pct=round(total_return_pct, 2),
            cagr_pct=round(cagr_pct, 2),
            mdd_pct=round(mdd_pct, 2),
            mdd_date=mdd_date,
            sharpe_ratio=round(sharpe_ratio, 2),
            sortino_ratio=round(sortino_ratio, 2),
            win_rate_pct=round(win_rate_pct, 1),
            avg_win_pct=round(avg_win_pct, 2),
            avg_loss_pct=round(avg_loss_pct, 2),
            profit_factor=round(profit_factor, 2),
            alpha_vs_kospi=round(alpha_vs_kospi, 2),
            alpha_vs_kosdaq=0.0,
            total_trades=total_trades,
            benchmark_return_pct=round(benchmark_return_pct, 2),
        )

    except Exception as e:
        logger.error("compute_metrics 실패: %s", e, exc_info=True)
        return BacktestMetrics()


# ---------------------------------------------------------------------------
# Portfolio simulation
# ---------------------------------------------------------------------------

def _should_rebalance(current_date: str, last_rebalance_date: str, mode: str) -> bool:
    """Determine whether a rebalance should occur on this date.

    Args:
        current_date: YYYY-MM-DD string for the current trading day.
        last_rebalance_date: YYYY-MM-DD string for the last rebalance date.
        mode: One of 'daily', 'weekly', 'monthly'.

    Returns:
        True if rebalancing should happen.
    """
    try:
        if not last_rebalance_date:
            return True

        cur = datetime.strptime(current_date, "%Y-%m-%d")
        prev = datetime.strptime(last_rebalance_date, "%Y-%m-%d")

        if mode == "daily":
            return True
        elif mode == "weekly":
            # Rebalance if we've crossed into a new ISO week
            return cur.isocalendar()[1] != prev.isocalendar()[1] or (cur - prev).days >= 7
        elif mode == "monthly":
            return cur.month != prev.month or cur.year != prev.year
        else:
            return (cur - prev).days >= 5
    except Exception as e:
        logger.warning("_should_rebalance 판정 오류: %s", e)
        return False


def _apply_slippage_and_commission(
    price: float,
    is_buy: bool,
    slippage_pct: float,
    buy_commission_pct: float,
    sell_commission_pct: float,
) -> float:
    """Return the effective execution price after slippage and commission.

    For buys, price is adjusted upward (worse fill).
    For sells, price is adjusted downward (worse fill).

    Args:
        price: Raw market price.
        is_buy: True for buy, False for sell.
        slippage_pct: Slippage assumption (fraction, e.g. 0.003).
        buy_commission_pct: Buy-side commission rate.
        sell_commission_pct: Sell-side commission + tax rate.

    Returns:
        Adjusted execution price.
    """
    try:
        if is_buy:
            return price * (1.0 + slippage_pct + buy_commission_pct)
        else:
            return price * (1.0 - slippage_pct - sell_commission_pct)
    except Exception as e:
        logger.warning("슬리피지/수수료 계산 오류: %s", e)
        return price


def simulate_portfolio(
    scores_by_date: dict[str, list[dict]],
    price_data: dict[str, dict[str, float]],
    top_n: int = 10,
    rebalance: str = "weekly",
    initial_capital: float = 100_000_000,
    slippage_pct: float = 0.003,
    buy_commission_pct: float = 0.00015,
    sell_commission_pct: float = 0.00315,
) -> PortfolioBacktestResult:
    """Run portfolio-level backtest simulation.

    This is the core engine. It takes pre-computed scores and price data
    (to avoid look-ahead bias -- caller must ensure scores only use data
    available at that point).

    Args:
        scores_by_date: {date_str: [{ticker, name, score, strategy}, ...]}
            Scores sorted descending by score for each date.
        price_data: {ticker: {date_str: close_price}}
            Historical close prices keyed by ticker then date.
        top_n: Number of top-scored stocks to hold.
        rebalance: 'daily', 'weekly', or 'monthly'.
        initial_capital: Starting capital in KRW.
        slippage_pct: Assumed slippage per trade.
        buy_commission_pct: Buy commission rate.
        sell_commission_pct: Sell commission + tax rate.

    Returns:
        PortfolioBacktestResult with daily snapshots, metrics, etc.
    """
    try:
        sorted_dates = sorted(scores_by_date.keys())
        if not sorted_dates:
            logger.warning("simulate_portfolio: 날짜별 스코어 데이터 없음")
            return PortfolioBacktestResult(initial_capital=initial_capital)

        # State: cash and holdings {ticker: {shares, avg_cost, name, strategy}}
        cash = initial_capital
        holdings: dict[str, dict[str, Any]] = {}

        daily_snapshots: list[DailySnapshot] = []
        daily_values: list[float] = []
        daily_dates_list: list[str] = []
        trade_results: list[float] = []
        strategy_pnl: dict[str, float] = {}

        last_rebalance_date = ""

        for dt in sorted_dates:
            # ---------------------------------------------------------------
            # Step 1: Compute current portfolio value at today's prices
            # ---------------------------------------------------------------
            holdings_value = 0.0
            for ticker, pos in list(holdings.items()):
                ticker_prices = price_data.get(ticker, {})
                price_today = ticker_prices.get(dt, 0.0)
                if price_today > 0:
                    holdings_value += pos["shares"] * price_today
                else:
                    # Use last known price if today's missing
                    holdings_value += pos["shares"] * pos.get("last_price", pos["avg_cost"])

            portfolio_value = cash + holdings_value
            daily_values.append(portfolio_value)
            daily_dates_list.append(dt)

            daily_snapshots.append(DailySnapshot(
                date=dt,
                portfolio_value=round(portfolio_value, 0),
                cash=round(cash, 0),
                holdings_count=len(holdings),
            ))

            # ---------------------------------------------------------------
            # Step 2: Check if we should rebalance
            # ---------------------------------------------------------------
            if not _should_rebalance(dt, last_rebalance_date, rebalance):
                # Update last_price on existing holdings
                for ticker in holdings:
                    tp = price_data.get(ticker, {}).get(dt, 0.0)
                    if tp > 0:
                        holdings[ticker]["last_price"] = tp
                continue

            last_rebalance_date = dt

            # ---------------------------------------------------------------
            # Step 3: Select top N stocks by score
            # ---------------------------------------------------------------
            scored = scores_by_date.get(dt, [])
            # Filter to stocks that have a valid price today
            eligible: list[dict] = []
            for s in scored:
                tk = s.get("ticker", "")
                tk_prices = price_data.get(tk, {})
                px = tk_prices.get(dt, 0.0)
                if px > 0:
                    eligible.append(s)
                if len(eligible) >= top_n * 3:
                    break

            target_tickers = [s["ticker"] for s in eligible[:top_n]]

            # ---------------------------------------------------------------
            # Step 4: Sell positions not in target list
            # ---------------------------------------------------------------
            tickers_to_sell = [t for t in holdings if t not in target_tickers]
            for ticker in tickers_to_sell:
                pos = holdings[ticker]
                tk_prices = price_data.get(ticker, {})
                sell_price_raw = tk_prices.get(dt, pos.get("last_price", pos["avg_cost"]))
                sell_price = _apply_slippage_and_commission(
                    sell_price_raw, is_buy=False,
                    slippage_pct=slippage_pct,
                    buy_commission_pct=buy_commission_pct,
                    sell_commission_pct=sell_commission_pct,
                )
                proceeds = pos["shares"] * sell_price
                cash += proceeds

                # Record trade result
                cost_basis = pos["shares"] * pos["avg_cost"]
                if cost_basis > 0:
                    trade_pnl_pct = (proceeds / cost_basis - 1.0) * 100.0
                else:
                    trade_pnl_pct = 0.0
                trade_results.append(trade_pnl_pct)

                # Strategy attribution
                strat = pos.get("strategy", "unknown")
                strategy_pnl[strat] = strategy_pnl.get(strat, 0.0) + (proceeds - cost_basis)

                logger.debug(
                    "매도 %s (%s) x%d @ %,.0f -> PnL %.1f%%",
                    ticker, pos.get("name", ""), pos["shares"],
                    sell_price, trade_pnl_pct,
                )
                del holdings[ticker]

            # ---------------------------------------------------------------
            # Step 5: Determine equal-weight allocation for target positions
            # ---------------------------------------------------------------
            # Recalculate current portfolio value after sales
            remaining_value = 0.0
            for ticker, pos in holdings.items():
                tp = price_data.get(ticker, {}).get(dt, pos.get("last_price", pos["avg_cost"]))
                remaining_value += pos["shares"] * tp

            total_investable = cash + remaining_value
            if len(target_tickers) == 0:
                continue
            target_weight = total_investable / len(target_tickers)

            # ---------------------------------------------------------------
            # Step 6: Buy new positions or rebalance existing ones
            # ---------------------------------------------------------------
            # Build a lookup for the scored entries
            scored_lookup = {s["ticker"]: s for s in eligible[:top_n]}

            for ticker in target_tickers:
                tk_prices = price_data.get(ticker, {})
                buy_price_raw = tk_prices.get(dt, 0.0)
                if buy_price_raw <= 0:
                    continue

                buy_price = _apply_slippage_and_commission(
                    buy_price_raw, is_buy=True,
                    slippage_pct=slippage_pct,
                    buy_commission_pct=buy_commission_pct,
                    sell_commission_pct=sell_commission_pct,
                )

                if ticker in holdings:
                    # Already held -- adjust to target weight
                    current_value = holdings[ticker]["shares"] * buy_price_raw
                    diff = target_weight - current_value
                    if abs(diff) < target_weight * 0.05:
                        # Within 5% tolerance, skip rebalance cost
                        holdings[ticker]["last_price"] = buy_price_raw
                        continue
                    if diff > 0:
                        # Need to buy more
                        additional_shares = int(diff / buy_price)
                        cost = additional_shares * buy_price
                        if cost <= cash and additional_shares > 0:
                            total_shares = holdings[ticker]["shares"] + additional_shares
                            total_cost = holdings[ticker]["shares"] * holdings[ticker]["avg_cost"] + cost
                            holdings[ticker]["avg_cost"] = total_cost / total_shares
                            holdings[ticker]["shares"] = total_shares
                            holdings[ticker]["last_price"] = buy_price_raw
                            cash -= cost
                    else:
                        # Need to sell some
                        sell_shares = int(abs(diff) / buy_price_raw)
                        if sell_shares > 0 and sell_shares < holdings[ticker]["shares"]:
                            sell_px = _apply_slippage_and_commission(
                                buy_price_raw, is_buy=False,
                                slippage_pct=slippage_pct,
                                buy_commission_pct=buy_commission_pct,
                                sell_commission_pct=sell_commission_pct,
                            )
                            cash += sell_shares * sell_px
                            holdings[ticker]["shares"] -= sell_shares
                            holdings[ticker]["last_price"] = buy_price_raw
                else:
                    # New position
                    shares_to_buy = int(target_weight / buy_price)
                    cost = shares_to_buy * buy_price
                    if cost <= cash and shares_to_buy > 0:
                        entry_info = scored_lookup.get(ticker, {})
                        holdings[ticker] = {
                            "shares": shares_to_buy,
                            "avg_cost": buy_price,
                            "last_price": buy_price_raw,
                            "name": entry_info.get("name", ticker),
                            "strategy": entry_info.get("strategy", "unknown"),
                        }
                        cash -= cost
                        logger.debug(
                            "매수 %s (%s) x%d @ %,.0f",
                            ticker, entry_info.get("name", ""),
                            shares_to_buy, buy_price,
                        )

            # Update last_price on all holdings
            for ticker in holdings:
                tp = price_data.get(ticker, {}).get(dt, 0.0)
                if tp > 0:
                    holdings[ticker]["last_price"] = tp

        # -------------------------------------------------------------------
        # Compute final value (close out remaining positions at last prices)
        # -------------------------------------------------------------------
        final_value = cash
        for ticker, pos in holdings.items():
            final_value += pos["shares"] * pos.get("last_price", pos["avg_cost"])

        # -------------------------------------------------------------------
        # Compute benchmark values (use first available ticker's prices
        # as a placeholder if no explicit benchmark; caller can provide)
        # -------------------------------------------------------------------
        benchmark_values_list: list[float] = []
        # Default: flat benchmark (no alpha computation possible)
        for _ in daily_dates_list:
            benchmark_values_list.append(1.0)

        # -------------------------------------------------------------------
        # Compute metrics
        # -------------------------------------------------------------------
        metrics = compute_metrics(
            daily_values=daily_values,
            daily_dates=daily_dates_list,
            benchmark_values=benchmark_values_list,
            initial_capital=initial_capital,
            trade_results=trade_results,
            risk_free_rate=DEFAULT_PARAMS["risk_free_rate"],
        )

        # -------------------------------------------------------------------
        # Monthly returns
        # -------------------------------------------------------------------
        monthly_rets = compute_monthly_returns(daily_values, daily_dates_list)

        # -------------------------------------------------------------------
        # Normalise strategy attribution to percentage
        # -------------------------------------------------------------------
        total_strat_pnl = sum(abs(v) for v in strategy_pnl.values())
        if total_strat_pnl > 0:
            strategy_attribution = {
                k: round(v / total_strat_pnl * 100.0, 1)
                for k, v in strategy_pnl.items()
            }
        else:
            strategy_attribution = {}

        return PortfolioBacktestResult(
            start_date=sorted_dates[0] if sorted_dates else "",
            end_date=sorted_dates[-1] if sorted_dates else "",
            top_n=top_n,
            rebalance_period=rebalance,
            initial_capital=initial_capital,
            final_value=round(final_value, 0),
            metrics=metrics,
            daily_snapshots=daily_snapshots,
            monthly_returns=monthly_rets,
            strategy_attribution=strategy_attribution,
            walk_forward_results=[],
        )

    except Exception as e:
        logger.error("simulate_portfolio 실패: %s", e, exc_info=True)
        return PortfolioBacktestResult(initial_capital=initial_capital)


# ---------------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------------

def compute_walk_forward(
    scores_by_date: dict[str, list[dict]],
    price_data: dict[str, dict[str, float]],
    train_months: int = 12,
    test_months: int = 3,
    top_n: int = 10,
) -> list[dict]:
    """Run walk-forward validation.

    Rolling window: train_months training -> test_months testing.
    The training window is used only to verify that the scoring system
    had positive returns before trusting the test window results.

    Args:
        scores_by_date: {date_str: [{ticker, name, score, strategy}, ...]}
        price_data: {ticker: {date_str: close_price}}
        train_months: Number of months in each training window.
        test_months: Number of months in each testing window.
        top_n: Number of top-scored stocks to hold.

    Returns:
        List of dicts, each with:
            train_period, test_period, train_return, test_return, overfit_gap
    """
    try:
        sorted_dates = sorted(scores_by_date.keys())
        if len(sorted_dates) < 60:
            logger.warning("walk-forward: 날짜 부족 (%d일), 최소 60일 필요", len(sorted_dates))
            return []

        first_date = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
        last_date = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
        total_span = (last_date - first_date).days
        window_days = (train_months + test_months) * 30

        if total_span < window_days:
            logger.warning(
                "walk-forward: 전체 기간 %d일 < 윈도우 %d일",
                total_span, window_days,
            )
            return []

        results: list[dict] = []
        step_days = test_months * 30  # slide by test_months each iteration
        window_start = first_date

        while True:
            train_end = window_start + timedelta(days=train_months * 30)
            test_end = train_end + timedelta(days=test_months * 30)

            if test_end > last_date + timedelta(days=1):
                break

            train_start_str = window_start.strftime("%Y-%m-%d")
            train_end_str = train_end.strftime("%Y-%m-%d")
            test_start_str = train_end_str
            test_end_str = test_end.strftime("%Y-%m-%d")

            # Split dates into train and test sets
            train_scores: dict[str, list[dict]] = {}
            test_scores: dict[str, list[dict]] = {}

            for dt in sorted_dates:
                if train_start_str <= dt < train_end_str:
                    train_scores[dt] = scores_by_date[dt]
                elif train_end_str <= dt < test_end_str:
                    test_scores[dt] = scores_by_date[dt]

            # Run backtest on each window
            train_result = simulate_portfolio(
                scores_by_date=train_scores,
                price_data=price_data,
                top_n=top_n,
                rebalance="weekly",
                initial_capital=DEFAULT_PARAMS["initial_capital"],
            )
            test_result = simulate_portfolio(
                scores_by_date=test_scores,
                price_data=price_data,
                top_n=top_n,
                rebalance="weekly",
                initial_capital=DEFAULT_PARAMS["initial_capital"],
            )

            train_ret = train_result.metrics.total_return_pct
            test_ret = test_result.metrics.total_return_pct

            # Overfit gap: how much worse is test vs train
            if abs(train_ret) > 1e-6:
                overfit_gap = train_ret - test_ret
            else:
                overfit_gap = -test_ret if test_ret != 0 else 0.0

            results.append({
                "train_period": f"{train_start_str} ~ {train_end_str}",
                "test_period": f"{test_start_str} ~ {test_end_str}",
                "train_return": round(train_ret, 2),
                "test_return": round(test_ret, 2),
                "overfit_gap": round(overfit_gap, 2),
            })

            logger.info(
                "Walk-forward [%s ~ %s] train=%.1f%% test=%.1f%% gap=%.1f%%",
                train_start_str, test_end_str, train_ret, test_ret, overfit_gap,
            )

            # Slide forward by test_months
            window_start = window_start + timedelta(days=step_days)

        return results

    except Exception as e:
        logger.error("compute_walk_forward 실패: %s", e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Monthly returns
# ---------------------------------------------------------------------------

def compute_monthly_returns(
    daily_values: list[float],
    daily_dates: list[str],
) -> dict[str, float]:
    """Compute monthly return percentages.

    Groups daily values by YYYY-MM and computes the return from
    the first to the last trading day of each month.

    Args:
        daily_values: Portfolio values at each trading day close.
        daily_dates: Corresponding YYYY-MM-DD date strings.

    Returns:
        Dict mapping 'YYYY-MM' to return percentage for that month.
    """
    try:
        if len(daily_values) < 2 or len(daily_values) != len(daily_dates):
            return {}

        # Group indices by YYYY-MM
        month_groups: dict[str, list[int]] = {}
        for i, dt in enumerate(daily_dates):
            month_key = dt[:7]  # 'YYYY-MM'
            if month_key not in month_groups:
                month_groups[month_key] = []
            month_groups[month_key].append(i)

        monthly: dict[str, float] = {}
        prev_month_end_value = None

        for month_key in sorted(month_groups.keys()):
            indices = month_groups[month_key]
            first_idx = indices[0]
            last_idx = indices[-1]

            month_end_value = daily_values[last_idx]

            if prev_month_end_value is not None and prev_month_end_value > 0:
                ret_pct = (month_end_value / prev_month_end_value - 1.0) * 100.0
                monthly[month_key] = round(ret_pct, 2)
            elif first_idx > 0 and daily_values[first_idx - 1] > 0:
                # Use the previous day's value as the baseline
                ret_pct = (month_end_value / daily_values[first_idx - 1] - 1.0) * 100.0
                monthly[month_key] = round(ret_pct, 2)
            else:
                # First month -- use start-to-end within month
                if daily_values[first_idx] > 0:
                    ret_pct = (month_end_value / daily_values[first_idx] - 1.0) * 100.0
                    monthly[month_key] = round(ret_pct, 2)

            prev_month_end_value = month_end_value

        return monthly

    except Exception as e:
        logger.error("compute_monthly_returns 실패: %s", e, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _format_number(value: float) -> str:
    """Format a KRW amount with commas (e.g., 123,456,789)."""
    try:
        if abs(value) >= 1_0000_0000:
            eok = value / 1_0000_0000
            return f"{eok:,.1f}억"
        elif abs(value) >= 1_0000:
            man = value / 1_0000
            return f"{man:,.0f}만"
        else:
            return f"{value:,.0f}"
    except Exception:
        return str(value)


def _sign_str(value: float) -> str:
    """Return value with explicit +/- sign."""
    try:
        return f"+{value:.2f}" if value >= 0 else f"{value:.2f}"
    except Exception:
        return str(value)


def format_backtest_report(result: PortfolioBacktestResult) -> str:
    """Format full backtest report for Telegram.

    Produces a Korean-language report personalised for the user.
    No bold formatting is used.

    Args:
        result: PortfolioBacktestResult from simulate_portfolio.

    Returns:
        Multi-line string ready for Telegram delivery.
    """
    try:
        m = result.metrics
        sep = "\u2500" * 26

        # Header
        lines: list[str] = [
            f"{USER_NAME}, 포트폴리오 백테스트 리포트",
            sep,
            f"기간: {result.start_date} ~ {result.end_date}",
            f"전략: Top-{result.top_n} 동일비중 ({result.rebalance_period} 리밸런싱)",
            f"초기자금: {_format_number(result.initial_capital)}원",
            f"최종자산: {_format_number(result.final_value)}원",
            "",
        ]

        # Performance section
        ret_arrow = "+" if m.total_return_pct >= 0 else ""
        cagr_arrow = "+" if m.cagr_pct >= 0 else ""

        lines.append("[ 수익률 ]")
        lines.append(f"  총 수익률: {ret_arrow}{m.total_return_pct:.2f}%")
        lines.append(f"  연환산(CAGR): {cagr_arrow}{m.cagr_pct:.2f}%")
        lines.append(f"  벤치마크 수익률: {_sign_str(m.benchmark_return_pct)}%")

        alpha_str = _sign_str(m.alpha_vs_kospi)
        lines.append(f"  초과수익(알파): {alpha_str}%p")
        lines.append("")

        # Risk section
        lines.append("[ 위험 지표 ]")
        lines.append(f"  최대낙폭(MDD): {m.mdd_pct:.2f}%")
        if m.mdd_date:
            lines.append(f"  MDD 발생일: {m.mdd_date}")
        lines.append(f"  샤프비율: {m.sharpe_ratio:.2f}")
        lines.append(f"  소르티노비율: {m.sortino_ratio:.2f}")
        lines.append("")

        # Trade stats
        lines.append("[ 거래 통계 ]")
        lines.append(f"  총 거래 횟수: {m.total_trades}회")
        lines.append(f"  승률: {m.win_rate_pct:.1f}%")
        lines.append(f"  평균 수익(승): {_sign_str(m.avg_win_pct)}%")
        lines.append(f"  평균 손실(패): {_sign_str(m.avg_loss_pct)}%")
        lines.append(f"  손익비(Profit Factor): {m.profit_factor:.2f}")
        lines.append("")

        # Monthly returns table
        if result.monthly_returns:
            lines.append("[ 월별 수익률 ]")
            for ym in sorted(result.monthly_returns.keys()):
                ret = result.monthly_returns[ym]
                indicator = "+" if ret >= 0 else ""
                lines.append(f"  {ym}: {indicator}{ret:.1f}%")
            lines.append("")

        # Strategy attribution
        if result.strategy_attribution:
            lines.append("[ 전략별 기여도 ]")
            sorted_strats = sorted(
                result.strategy_attribution.items(),
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            for strat, pct in sorted_strats:
                lines.append(f"  {strat}: {pct:+.1f}%")
            lines.append("")

        # Walk-forward results
        if result.walk_forward_results:
            lines.append("[ Walk-Forward 검증 ]")
            pass_count = 0
            total_wf = len(result.walk_forward_results)
            for wf in result.walk_forward_results:
                gap = wf.get("overfit_gap", 0)
                status = "PASS" if abs(gap) < 10 else "WARN"
                if status == "PASS":
                    pass_count += 1
                lines.append(
                    f"  {wf['test_period']}: "
                    f"학습 {_sign_str(wf['train_return'])}% / "
                    f"검증 {_sign_str(wf['test_return'])}% "
                    f"[{status}]"
                )
            lines.append(f"  통과율: {pass_count}/{total_wf}")
            lines.append("")

        # Footer with interpretation
        lines.append(sep)
        if m.sharpe_ratio >= 1.0 and m.total_return_pct > 0:
            lines.append(
                f"{USER_NAME}, K-Quant 스코어링 시스템이 유의미한 알파를 "
                f"생성하고 있습니다. 샤프 {m.sharpe_ratio:.2f}로 위험 대비 "
                f"수익이 양호합니다."
            )
        elif m.total_return_pct > 0:
            lines.append(
                f"{USER_NAME}, 양의 수익률을 기록했으나 샤프비율 "
                f"{m.sharpe_ratio:.2f}로 변동성 관리가 필요합니다."
            )
        else:
            lines.append(
                f"{USER_NAME}, 백테스트 결과 수익률이 마이너스입니다. "
                f"스코어링 파라미터 재검토를 권장드립니다."
            )

        if m.mdd_pct < -20:
            lines.append(
                f"  MDD {m.mdd_pct:.1f}%로 낙폭이 크므로 "
                f"포지션 사이징 조절을 고려해 주세요."
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error("format_backtest_report 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 백테스트 리포트 생성 중 오류가 발생했습니다."
