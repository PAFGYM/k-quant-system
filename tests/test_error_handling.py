"""E2E error-handling / resilience tests.

Verify graceful degradation when external services fail,
data is missing, or portfolios are empty.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. yfinance timeout during backtest
# ---------------------------------------------------------------------------

def test_yfinance_timeout():
    """run_backtest handles yfinance failure gracefully (no crash)."""
    from kstock.backtest.engine import run_backtest

    with patch("yfinance.download", side_effect=Exception("timeout")):
        result = run_backtest("005930", name="Samsung", market="KOSPI")

    # Should return None or an empty result, not crash
    if result is not None:
        assert result.total_trades == 0, "Should have 0 trades on data failure"


# ---------------------------------------------------------------------------
# 2. Claude API failure in morning briefing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claude_api_failure():
    """Morning briefing falls back when _generate_morning_briefing_v2 raises."""
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.chat_id = 12345
    mixin.db = MagicMock()
    mixin.db.get_active_holdings.return_value = []
    mixin.db.upsert_job_run = MagicMock()
    mixin.anthropic_key = None  # No API → _generate_morning_briefing_v2 returns None

    macro = MagicMock()
    macro.vix = 18.0
    macro.vix_change_pct = -0.5
    macro.spx_change_pct = 0.3
    macro.nasdaq_change_pct = 0.5
    macro.usdkrw = 1320
    macro.usdkrw_change_pct = -0.2
    macro.btc_price = 60000
    macro.btc_change_pct = 1.0
    macro.gold_price = 2300
    macro.gold_change_pct = 0.1
    macro.regime = "normal"
    mixin.macro_client = MagicMock()
    mixin.macro_client.get_snapshot = AsyncMock(return_value=macro)
    mixin._generate_morning_briefing_v2 = AsyncMock(return_value=None)

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    regime = MagicMock()
    regime.mode = "normal"
    regime.emoji = "🟢"
    regime.label = "정상"
    regime.message = ""
    regime.allocations = {}

    with patch("kstock.bot.mixins.scheduler.detect_regime", return_value=regime), \
         patch("kstock.bot.mixins.scheduler.format_market_status", return_value="시장 정상"), \
         patch("kstock.bot.mixins.scheduler._today", return_value="2026-02-25"):
        await mixin.job_morning_briefing(context)

    # Fallback message should have been sent
    context.bot.send_message.assert_called_once()
    text = context.bot.send_message.call_args.kwargs["text"]
    assert "오전 브리핑" in text or "시장" in text


# ---------------------------------------------------------------------------
# 3. WebSocket callback with malformed data
# ---------------------------------------------------------------------------

def test_websocket_callback_error():
    """_on_realtime_update must not raise even with missing data attrs."""
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.chat_id = 12345
    mixin._SURGE_THRESHOLD_PCT = 3.0
    mixin._SURGE_COOLDOWN_SEC = 1800
    mixin._SELL_TARGET_COOLDOWN_SEC = 3600
    mixin._surge_cooldown = {}
    mixin._holdings_index = {}

    # Empty-spec object has no change_pct etc.
    bad_data = MagicMock(spec=[])

    mock_now = MagicMock()
    mock_now.hour = 10
    mock_now.minute = 30

    try:
        with patch("kstock.bot.mixins.scheduler.datetime") as mock_dt, \
             patch("kstock.bot.mixins.scheduler._time") as mock_time:
            mock_dt.now.return_value = mock_now
            mock_time.time.return_value = 100000.0
            SchedulerMixin._on_realtime_update(mixin, "price", "005930", bad_data)
    except Exception as exc:
        pytest.fail(f"_on_realtime_update raised on bad data: {exc}")


# ---------------------------------------------------------------------------
# 4. Empty holdings for multiple features
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_holdings_morning_briefing():
    """Morning briefing with zero holdings still sends message."""
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.chat_id = 12345
    mixin.db = MagicMock()
    mixin.db.get_active_holdings.return_value = []
    mixin.db.upsert_job_run = MagicMock()
    mixin.anthropic_key = None
    mixin.macro_client = MagicMock()
    macro = MagicMock(
        vix=18.0, vix_change_pct=-0.5, spx_change_pct=0.3,
        nasdaq_change_pct=0.5, usdkrw=1320, usdkrw_change_pct=-0.2,
        btc_price=60000, btc_change_pct=1.0, gold_price=2300,
        gold_change_pct=0.1, regime="normal",
    )
    mixin.macro_client.get_snapshot = AsyncMock(return_value=macro)
    mixin._generate_morning_briefing_v2 = AsyncMock(return_value=None)

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    regime = MagicMock(mode="normal", emoji="🟢", label="정상", message="", allocations={})

    with patch("kstock.bot.mixins.scheduler.detect_regime", return_value=regime), \
         patch("kstock.bot.mixins.scheduler.format_market_status", return_value="시장"), \
         patch("kstock.bot.mixins.scheduler._today", return_value="2026-02-25"):
        await mixin.job_morning_briefing(context)

    context.bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_empty_holdings_scalp_close_reminder():
    """job_scalp_close_reminder with no holdings returns silently."""
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.chat_id = 12345
    mixin.db = MagicMock()
    mixin.db.get_active_holdings.return_value = []

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await SchedulerMixin.job_scalp_close_reminder(mixin, context)

    # No scalp holdings → no message
    context.bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Risk engine with empty portfolio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_engine_empty_portfolio():
    """generate_advanced_risk_report with empty holdings returns safe defaults."""
    from kstock.core.risk_engine import generate_advanced_risk_report

    report = await generate_advanced_risk_report(
        portfolio_value=0,
        holdings=[],
    )

    assert report is not None
    assert report.portfolio_value == 0
    assert report.risk_grade in ("A", "B", "C", "D", "F")


# ---------------------------------------------------------------------------
# 6. TradeCosts with zero quantities
# ---------------------------------------------------------------------------

def test_trade_costs_zero_quantity():
    """TradeCosts methods handle qty=0 without errors."""
    from kstock.backtest.engine import TradeCosts

    costs = TradeCosts()
    assert costs.buy_cost(10000, 0) == 0
    assert costs.sell_cost(10000, 0) == 0
    assert costs.net_pnl(10000, 11000, 0) == 0
