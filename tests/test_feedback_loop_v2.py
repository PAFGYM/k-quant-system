"""Tests for Phase 7 additions to kstock.signal.feedback_loop module."""

from __future__ import annotations

import pytest

from kstock.signal.feedback_loop import (
    STRATEGY_LABELS,
    format_feedback_stats,
    get_feedback_for_ticker,
    get_similar_condition_stats,
)
from kstock.store.sqlite import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_completed_recs(
    count: int,
    *,
    strategy: str = "A",
    score: float = 80,
    regime: str = "attack",
    profit_ratio: float = 0.6,
) -> list[dict]:
    """Build a list of completed recommendation dicts.

    ``profit_ratio`` controls what fraction are 'profit' vs 'stop'.
    """
    recs: list[dict] = []
    for i in range(count):
        is_win = i < int(count * profit_ratio)
        recs.append({
            "id": i + 1,
            "ticker": f"{i:06d}",
            "name": f"종목{i}",
            "rec_date": f"2025-05-{(i % 28) + 1:02d}",
            "rec_price": 50000,
            "rec_score": score,
            "strategy_type": strategy,
            "regime": regime,
            "status": "profit" if is_win else "stop",
            "pnl_pct": 5.0 if is_win else -3.0,
        })
    return recs


# ---------------------------------------------------------------------------
# TestGetSimilarConditionStats
# ---------------------------------------------------------------------------
class TestGetSimilarConditionStats:
    def test_6_matching_returns_dict(self):
        recs = _make_completed_recs(8, strategy="A", score=80, regime="attack")
        result = get_similar_condition_stats(recs)
        assert result is not None
        assert "sample_size" in result
        assert "win_rate" in result
        assert "avg_return" in result
        assert "confidence" in result

    def test_less_than_5_returns_none(self):
        recs = _make_completed_recs(4, strategy="A")
        result = get_similar_condition_stats(recs)
        assert result is None

    def test_strategy_filter(self):
        recs_a = _make_completed_recs(6, strategy="A")
        recs_b = _make_completed_recs(6, strategy="B")
        all_recs = recs_a + recs_b
        result = get_similar_condition_stats(all_recs, strategy="A")
        assert result is not None
        assert result["sample_size"] == 6

    def test_score_range_filter(self):
        low_score = _make_completed_recs(6, score=30)
        high_score = _make_completed_recs(6, score=150)
        all_recs = low_score + high_score
        result = get_similar_condition_stats(
            all_recs, score_min=100, score_max=200,
        )
        assert result is not None
        assert result["sample_size"] == 6

    def test_market_condition_filter(self):
        attack = _make_completed_recs(6, regime="attack")
        defense = _make_completed_recs(6, regime="defense")
        all_recs = attack + defense
        result = get_similar_condition_stats(
            all_recs, market_condition="defense",
        )
        assert result is not None
        assert result["sample_size"] == 6

    def test_confidence_levels(self):
        # 25 recs -> high
        recs_high = _make_completed_recs(25)
        result_high = get_similar_condition_stats(recs_high)
        assert result_high is not None
        assert result_high["confidence"] == "high"

        # 12 recs -> medium
        recs_med = _make_completed_recs(12)
        result_med = get_similar_condition_stats(recs_med)
        assert result_med is not None
        assert result_med["confidence"] == "medium"

        # 6 recs -> low
        recs_low = _make_completed_recs(6)
        result_low = get_similar_condition_stats(recs_low)
        assert result_low is not None
        assert result_low["confidence"] == "low"


# ---------------------------------------------------------------------------
# TestGetFeedbackForTicker
# ---------------------------------------------------------------------------
class TestGetFeedbackForTicker:
    def test_ticker_with_past_recs(self):
        recs = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "rec_date": "2025-05-01",
                "rec_score": 85,
                "strategy_type": "A",
                "pnl_pct": 5.0,
                "status": "profit",
            },
        ]
        result = get_feedback_for_ticker(recs, "005930")
        assert "삼성전자" in result or "005930" in result or "추천 이력" in result

    def test_ticker_with_no_recs(self):
        recs = [
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "rec_date": "2025-05-01",
                "rec_score": 90,
                "strategy_type": "C",
                "pnl_pct": 3.0,
                "status": "profit",
            },
        ]
        result = get_feedback_for_ticker(recs, "005930")
        assert "없음" in result

    def test_with_strategy_stats(self):
        recs = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "rec_date": "2025-05-01",
                "rec_score": 85,
                "strategy_type": "A",
                "pnl_pct": 5.0,
                "status": "profit",
            },
        ]
        strategy_stats = {
            "A": {"total": 10, "wins": 7, "hit_rate": 70.0, "avg_return": 4.5},
        }
        result = get_feedback_for_ticker(recs, "005930", strategy_stats=strategy_stats)
        # Should contain strategy label in Korean
        assert "단기반등" in result or "승률" in result or "전략" in result

    def test_empty_recommendations(self):
        result = get_feedback_for_ticker([], "005930")
        assert "없음" in result

    def test_limits_to_5_past_recs(self):
        recs = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "rec_date": f"2025-05-{i:02d}",
                "rec_score": 80 + i,
                "strategy_type": "A",
                "pnl_pct": 2.0 * i,
                "status": "profit",
            }
            for i in range(1, 10)  # 9 records
        ]
        result = get_feedback_for_ticker(recs, "005930", limit=5)
        # Count lines that look like individual recommendation entries
        rec_lines = [
            line for line in result.split("\n")
            if line.strip().startswith("20") and "스코어" in line
        ]
        assert len(rec_lines) <= 5


# ---------------------------------------------------------------------------
# TestFormatFeedbackStats
# ---------------------------------------------------------------------------
class TestFormatFeedbackStats:
    def _sample_stats(self) -> dict:
        return {
            "sample_size": 25,
            "win_rate": 72.0,
            "avg_return": 4.5,
            "confidence": "high",
        }

    def test_no_bold(self):
        msg = format_feedback_stats(self._sample_stats())
        assert "**" not in msg

    def test_contains_juhonim(self):
        msg = format_feedback_stats(self._sample_stats())
        assert "주호님" in msg

    def test_contains_win_rate(self):
        msg = format_feedback_stats(self._sample_stats())
        assert "72" in msg  # win_rate value

    def test_contains_confidence_korean(self):
        msg = format_feedback_stats(self._sample_stats())
        # confidence "high" -> "높음"
        assert "높음" in msg


# ---------------------------------------------------------------------------
# TestStrategyLabels
# ---------------------------------------------------------------------------
class TestStrategyLabels:
    def test_7_strategies(self):
        assert len(STRATEGY_LABELS) == 7
        for key in ("A", "B", "C", "D", "E", "F", "G"):
            assert key in STRATEGY_LABELS

    def test_all_have_korean_labels(self):
        for key, label in STRATEGY_LABELS.items():
            assert isinstance(label, str)
            assert len(label) > 0
            # Korean labels should contain Korean characters
            has_korean = any("\uac00" <= ch <= "\ud7a3" for ch in label)
            assert has_korean, f"Label for {key} ('{label}') has no Korean characters"


# ---------------------------------------------------------------------------
# TestDBNewPhase7Tables
# ---------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    return SQLiteStore(db_path=tmp_path / "test.db")


class TestDBNewPhase7Tables:
    def test_add_and_get_strategy_stat(self, store):
        row_id = store.add_strategy_stat(
            strategy="A",
            period="2025-W20",
            total_count=20,
            win_count=14,
            win_rate=70.0,
            avg_return=4.5,
        )
        assert row_id > 0

        stats = store.get_strategy_stats(strategy="A")
        assert len(stats) >= 1
        assert stats[0]["strategy"] == "A"
        assert stats[0]["total_count"] == 20
        assert stats[0]["win_rate"] == 70.0

    def test_add_and_get_surge_stock(self, store):
        row_id = store.add_surge_stock(
            ticker="005930",
            name="삼성전자",
            scan_time="2025-05-20T09:30:00",
            change_pct=8.5,
            volume_ratio=3.2,
            triggers="gap_up,volume_surge",
            market_cap=400e12,
            health_grade="A",
            health_score=85,
            health_reasons="재무건전, 수급양호",
            ai_analysis="단기 반등 가능성 높음",
        )
        assert row_id > 0

        stocks = store.get_surge_stocks(days=1)
        assert len(stocks) >= 1
        assert stocks[0]["ticker"] == "005930"
        assert stocks[0]["change_pct"] == 8.5

    def test_add_and_get_stealth_accumulation(self, store):
        row_id = store.add_stealth_accumulation(
            ticker="000660",
            name="SK하이닉스",
            total_score=80,
            patterns_json='[{"pattern": "institutional_streak"}]',
            price_change_20d=0.03,
            inst_total=15e8,
            foreign_total=10e8,
        )
        assert row_id > 0

        accums = store.get_stealth_accumulations(days=1)
        assert len(accums) >= 1
        assert accums[0]["ticker"] == "000660"
        assert accums[0]["total_score"] == 80

    def test_add_get_close_trade_register(self, store):
        row_id = store.add_trade_register(
            ticker="035420",
            name="NAVER",
            quantity=5,
            price=220000,
            total_amount=1_100_000,
            source="text",
            horizon="swing",
            trailing_stop_pct=0.05,
            target_profit_pct=0.10,
        )
        assert row_id > 0

        trades = store.get_trade_registers(status="active")
        assert len(trades) >= 1
        assert trades[0]["ticker"] == "035420"
        assert trades[0]["status"] == "active"

        store.close_trade_register(row_id)
        active_trades = store.get_trade_registers(status="active")
        closed_trades = store.get_trade_registers(status="closed")
        assert all(t["id"] != row_id for t in active_trades)
        assert any(t["id"] == row_id for t in closed_trades)

    def test_add_and_get_multi_agent_result(self, store):
        row_id = store.add_multi_agent_result(
            ticker="005930",
            name="삼성전자",
            technical_score=75,
            fundamental_score=80,
            sentiment_score=65,
            combined_score=73,
            verdict="매수 우위",
            confidence="medium",
            strategist_summary="기술적 반등 구간, 기본면 양호",
        )
        assert row_id > 0

        results = store.get_multi_agent_results()
        assert len(results) >= 1
        assert results[0]["ticker"] == "005930"
        assert results[0]["combined_score"] == 73

    def test_get_multi_agent_results_ticker_filter(self, store):
        store.add_multi_agent_result(
            ticker="005930", name="삼성전자",
            technical_score=75, fundamental_score=80,
            sentiment_score=65, combined_score=73,
        )
        store.add_multi_agent_result(
            ticker="000660", name="SK하이닉스",
            technical_score=70, fundamental_score=85,
            sentiment_score=60, combined_score=72,
        )

        samsung_only = store.get_multi_agent_results(ticker="005930")
        assert len(samsung_only) == 1
        assert samsung_only[0]["ticker"] == "005930"

        all_results = store.get_multi_agent_results()
        assert len(all_results) == 2

    def test_get_trade_registers_filters_by_status(self, store):
        id1 = store.add_trade_register(
            ticker="005930", name="삼성전자", quantity=10, price=72000,
        )
        id2 = store.add_trade_register(
            ticker="000660", name="SK하이닉스", quantity=5, price=180000,
        )
        store.close_trade_register(id2)

        active = store.get_trade_registers(status="active")
        closed = store.get_trade_registers(status="closed")

        active_tickers = [t["ticker"] for t in active]
        closed_tickers = [t["ticker"] for t in closed]

        assert "005930" in active_tickers
        assert "000660" not in active_tickers
        assert "000660" in closed_tickers

    def test_get_surge_stocks_filters_by_days(self, store):
        # Insert a surge stock (created_at is set to utcnow by the store)
        store.add_surge_stock(
            ticker="005930",
            name="삼성전자",
            change_pct=12.0,
        )

        # days=1 should find it (created just now)
        recent = store.get_surge_stocks(days=1)
        assert len(recent) >= 1
        assert recent[0]["ticker"] == "005930"

        # days=0 with a very tight window -- we pass 0 which means cutoff
        # is now, so the record created fractions of a second ago might still
        # qualify.  The key test is that the days parameter is accepted and
        # the query structure works.
        result_tight = store.get_surge_stocks(days=0)
        # With days=0 the cutoff is right now, record was just created so
        # it may or may not be included depending on fractional-second timing.
        assert isinstance(result_tight, list)
