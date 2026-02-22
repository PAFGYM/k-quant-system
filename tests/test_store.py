"""Tests for SQLite store."""

from __future__ import annotations

import pytest

from kstock.store.sqlite import SQLiteStore


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(db_path=tmp_path / "test.db")


class TestJobRuns:
    def test_upsert_and_get(self, store):
        store.upsert_job_run("test_job", "2024-01-01", status="success")
        result = store.get_last_job_run("test_job")
        assert result is not None
        assert result["job_name"] == "test_job"
        assert result["status"] == "success"

    def test_get_nonexistent(self, store):
        assert store.get_last_job_run("nonexistent") is None


class TestPortfolio:
    def test_upsert_and_get(self, store):
        store.upsert_portfolio("005930", name="삼성전자", score=85.0, signal="BUY")
        entries = store.get_portfolio()
        assert len(entries) == 1
        assert entries[0]["ticker"] == "005930"
        assert entries[0]["score"] == 85.0

    def test_get_entry(self, store):
        store.upsert_portfolio("005930", name="삼성전자", score=85.0, signal="BUY")
        entry = store.get_portfolio_entry("005930")
        assert entry is not None
        assert entry["name"] == "삼성전자"

    def test_update_existing(self, store):
        store.upsert_portfolio("005930", name="삼성전자", score=60.0, signal="WATCH")
        store.upsert_portfolio("005930", score=85.0, signal="BUY")
        entry = store.get_portfolio_entry("005930")
        assert entry["score"] == 85.0
        assert entry["signal"] == "BUY"


class TestHoldings:
    def test_add_and_get(self, store):
        hid = store.add_holding("005930", "삼성전자", 58000)
        assert hid > 0
        holdings = store.get_active_holdings()
        assert len(holdings) == 1
        assert holdings[0]["buy_price"] == 58000
        assert holdings[0]["target_1"] == 59740
        assert holdings[0]["stop_price"] == 55100

    def test_update_holding(self, store):
        hid = store.add_holding("005930", "삼성전자", 58000)
        store.update_holding(hid, current_price=60000, pnl_pct=3.45)
        h = store.get_holding(hid)
        assert h["current_price"] == 60000
        assert h["pnl_pct"] == 3.45

    def test_get_by_ticker(self, store):
        store.add_holding("005930", "삼성전자", 58000)
        h = store.get_holding_by_ticker("005930")
        assert h is not None
        assert h["name"] == "삼성전자"

    def test_no_active_holding(self, store):
        assert store.get_holding_by_ticker("999999") is None


class TestWatchlist:
    def test_add_and_get(self, store):
        store.add_watchlist("005930", "삼성전자", target_price=56000)
        wl = store.get_watchlist()
        assert len(wl) == 1
        assert wl[0]["ticker"] == "005930"

    def test_remove(self, store):
        store.add_watchlist("005930", "삼성전자")
        store.remove_watchlist("005930")
        assert len(store.get_watchlist()) == 0


class TestAlerts:
    def test_insert_and_get(self, store):
        store.insert_alert("005930", "buy", "Test alert")
        alerts = store.get_recent_alerts()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "buy"

    def test_has_recent_alert(self, store):
        store.insert_alert("005930", "buy", "Test")
        assert store.has_recent_alert("005930", "buy", hours=1) is True
        assert store.has_recent_alert("005930", "sell", hours=1) is False

    def test_no_recent_alert(self, store):
        assert store.has_recent_alert("005930", "buy", hours=1) is False


class TestRecommendations:
    def test_add_and_get_active(self, store):
        rid = store.add_recommendation("005930", "삼성전자", 58000, 85.0)
        assert rid > 0
        recs = store.get_active_recommendations()
        assert len(recs) == 1
        assert recs[0]["ticker"] == "005930"
        assert recs[0]["rec_price"] == 58000
        assert recs[0]["rec_score"] == 85.0
        assert recs[0]["status"] == "active"
        assert recs[0]["target_1"] == 59740
        assert recs[0]["stop_price"] == 55100
        assert recs[0]["strategy_type"] == "A"

    def test_add_with_strategy(self, store):
        rid = store.add_recommendation(
            "122630", "KODEX 레버리지", 15000, 70.0,
            strategy_type="B", target_pct=3.5, stop_pct=-3.0,
        )
        recs = store.get_active_recommendations()
        assert recs[0]["strategy_type"] == "B"
        assert recs[0]["target_1"] == 15525  # 15000 * 1.035
        assert recs[0]["stop_price"] == 14550  # 15000 * 0.97

    def test_add_watch_recommendation(self, store):
        rid = store.add_recommendation("005930", "삼성전자", 56000, 62.0, status="watch")
        recs = store.get_watch_recommendations()
        assert len(recs) == 1
        assert recs[0]["status"] == "watch"

    def test_update_recommendation(self, store):
        rid = store.add_recommendation("005930", "삼성전자", 58000, 85.0)
        store.update_recommendation(rid, current_price=60000, pnl_pct=3.45)
        recs = store.get_active_recommendations()
        assert recs[0]["current_price"] == 60000
        assert recs[0]["pnl_pct"] == 3.45

    def test_completed_recommendations(self, store):
        rid = store.add_recommendation("005930", "삼성전자", 58000, 85.0)
        store.update_recommendation(rid, status="profit", pnl_pct=3.5, closed_at="2024-01-15")
        completed = store.get_completed_recommendations()
        assert len(completed) == 1
        assert completed[0]["status"] == "profit"

    def test_has_active_recommendation(self, store):
        store.add_recommendation("005930", "삼성전자", 58000, 85.0)
        assert store.has_active_recommendation("005930") is True
        assert store.has_active_recommendation("999999") is False

    def test_get_by_strategy(self, store):
        store.add_recommendation("005930", "삼성전자", 58000, 85.0, strategy_type="A")
        store.add_recommendation("122630", "KODEX 레버리지", 15000, 70.0, strategy_type="B")
        recs_a = store.get_recommendations_by_strategy("A")
        recs_b = store.get_recommendations_by_strategy("B")
        assert len(recs_a) == 1
        assert len(recs_b) == 1
        assert recs_a[0]["name"] == "삼성전자"
        assert recs_b[0]["name"] == "KODEX 레버리지"

    def test_stats(self, store):
        store.add_recommendation("005930", "삼성전자", 58000, 85.0)
        store.add_recommendation("000660", "SK하이닉스", 120000, 72.0, status="watch")
        rid3 = store.add_recommendation("035420", "NAVER", 200000, 80.0)
        store.update_recommendation(rid3, status="profit", pnl_pct=5.0, closed_at="2024-01-15")
        stats = store.get_all_recommendations_stats()
        assert stats["total"] == 3
        assert stats["active"] == 1
        assert stats["watch"] == 1
        assert stats["profit"] == 1
        assert stats["stop"] == 0
        assert stats["avg_closed_pnl"] == 5.0

    def test_no_duplicate_active(self, store):
        store.add_recommendation("005930", "삼성전자", 58000, 85.0)
        assert store.has_active_recommendation("005930") is True
        store.add_recommendation("000660", "SK하이닉스", 120000, 72.0, status="watch")
        assert store.has_active_recommendation("000660") is True


class TestTrades:
    def test_add_and_get(self, store):
        tid = store.add_trade(
            ticker="005930", name="삼성전자", action="buy",
            strategy_type="A", recommended_price=58000,
            action_price=58000, quantity_pct=10,
        )
        assert tid > 0
        trades = store.get_trades()
        assert len(trades) == 1
        assert trades[0]["action"] == "buy"
        assert trades[0]["strategy_type"] == "A"
        assert trades[0]["action_price"] == 58000

    def test_get_by_strategy(self, store):
        store.add_trade("005930", "삼성전자", "buy", strategy_type="A")
        store.add_trade("122630", "KODEX 레버리지", "buy", strategy_type="B")
        trades_a = store.get_trades_by_strategy("A")
        trades_b = store.get_trades_by_strategy("B")
        assert len(trades_a) == 1
        assert len(trades_b) == 1

    def test_skip_trade(self, store):
        store.add_trade("005930", "삼성전자", "skip", strategy_type="A", recommended_price=58000)
        trades = store.get_trades()
        assert trades[0]["action"] == "skip"

    def test_stop_loss_trade(self, store):
        store.add_trade(
            "005930", "삼성전자", "stop_loss",
            action_price=55000, pnl_pct=-5.2,
        )
        trades = store.get_trades()
        assert trades[0]["action"] == "stop_loss"
        assert trades[0]["pnl_pct"] == -5.2

    def test_hold_through_stop(self, store):
        store.add_trade("005930", "삼성전자", "hold_through_stop", action_price=55000)
        trades = store.get_trades()
        assert trades[0]["action"] == "hold_through_stop"

    def test_strategy_performance(self, store):
        # Add some recommendations for performance tracking
        rid1 = store.add_recommendation("005930", "삼성전자", 58000, 85.0, strategy_type="A")
        store.update_recommendation(rid1, status="profit", pnl_pct=3.5, closed_at="2024-01-15")
        rid2 = store.add_recommendation("000660", "SK하이닉스", 120000, 72.0, strategy_type="A")
        store.update_recommendation(rid2, status="stop", pnl_pct=-4.0, closed_at="2024-01-20")

        # Add some trades
        store.add_trade("005930", "삼성전자", "buy", strategy_type="A")
        store.add_trade("000660", "SK하이닉스", "skip", strategy_type="A")
        store.add_trade("005930", "삼성전자", "stop_loss", strategy_type="A")

        perf = store.get_strategy_performance()
        assert "A" in perf
        assert perf["A"]["total"] == 2
        assert perf["A"]["wins"] == 1
        assert perf["A"]["win_rate"] == 50.0
        assert "summary" in perf
        assert perf["summary"]["execution_rate"] == 50.0
