"""Tests for backtest pro features (TradeCosts, portfolio backtest)."""
from kstock.backtest.engine import (
    TradeCosts,
    BacktestResult,
    PortfolioBacktestResult,
    format_portfolio_backtest,
)


def test_trade_costs_buy_sell():
    costs = TradeCosts()
    buy_cost = costs.buy_cost(10000, 100)
    assert buy_cost > 0
    sell_cost = costs.sell_cost(10000, 100)
    assert sell_cost > buy_cost  # sell has tax


def test_net_pnl_pct_less_than_gross():
    costs = TradeCosts()
    net = costs.net_pnl_pct(10000, 10300)
    assert 0 < net < 3.0


def test_net_pnl_negative():
    costs = TradeCosts()
    net = costs.net_pnl(10000, 9900, 100)
    assert net < 0


def test_trade_costs_custom():
    costs = TradeCosts(commission_rate=0.001, sell_tax_rate=0.003, slippage_rate=0.002)
    net = costs.net_pnl_pct(10000, 10500)
    assert net < 5.0  # less than gross 5%


def test_backtest_result_has_cost_field():
    r = BacktestResult(
        ticker="005930", name="삼성전자", period="1y",
        total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=60, avg_pnl_pct=1.5, total_return_pct=7.5,
        max_drawdown_pct=-3.0, sharpe_ratio=1.2,
        profit_factor=1.5, total_cost_pct=1.2,
    )
    assert r.total_cost_pct == 1.2


def test_portfolio_backtest_result_format():
    r = PortfolioBacktestResult(
        period="2024-01-01 ~ 2024-12-31",
        initial_capital=10000000, final_capital=11500000,
        total_return_pct=15.0, annualized_return_pct=15.0,
        max_drawdown_pct=-8.0, sharpe_ratio=1.4,
        sortino_ratio=1.8, calmar_ratio=1.9,
        total_trades=20, win_rate=60.0,
        profit_factor=1.6, avg_holding_days=5.0,
        total_cost_pct=2.0,
        per_stock_results=[
            BacktestResult(
                ticker="005930", name="삼성전자", period="1y",
                total_trades=10, winning_trades=6, losing_trades=4,
                win_rate=60, avg_pnl_pct=2.0, total_return_pct=10.0,
                max_drawdown_pct=-5.0, sharpe_ratio=1.0,
                profit_factor=1.5, total_cost_pct=1.0,
            ),
        ],
    )
    text = format_portfolio_backtest(r)
    assert "포트폴리오 백테스트" in text
    assert "삼성전자" in text
    assert "+15.0%" in text
