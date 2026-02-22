"""Tests for kstock.signal.feedback_loop module."""

from __future__ import annotations

import pytest

from kstock.signal.feedback_loop import (
    FeedbackReport,
    RecommendationResult,
    STRATEGY_LABELS,
    compute_regime_hit_rates,
    compute_strategy_hit_rates,
    evaluate_diagnosis_accuracy,
    evaluate_recommendation,
    format_feedback_report,
)


# ---------------------------------------------------------------------------
# evaluate_recommendation
# ---------------------------------------------------------------------------
class TestEvaluateRecommendation:
    def test_positive_return_day5(self):
        rec = {
            "ticker": "005930", "name": "삼성전자",
            "rec_date": "2025-01-01", "rec_price": 50000,
            "strategy_type": "A",
        }
        result = evaluate_recommendation(rec, current_price=55000, days_since=5)
        assert result.day5_return == 10.0
        assert result.correct is True

    def test_negative_return(self):
        rec = {
            "ticker": "005930", "name": "삼성전자",
            "rec_date": "2025-01-01", "rec_price": 50000,
            "strategy_type": "A",
        }
        result = evaluate_recommendation(rec, current_price=45000, days_since=10)
        assert result.day5_return == -10.0
        assert result.day10_return == -10.0
        assert result.correct is False

    def test_day20_return(self):
        rec = {
            "ticker": "005930", "name": "삼성전자",
            "rec_date": "2025-01-01", "rec_price": 50000,
            "strategy_type": "C",
        }
        result = evaluate_recommendation(rec, current_price=60000, days_since=25)
        assert result.day5_return == 20.0
        assert result.day10_return == 20.0
        assert result.day20_return == 20.0
        assert result.correct is True

    def test_too_early_no_day5(self):
        rec = {
            "ticker": "005930", "name": "삼성전자",
            "rec_date": "2025-01-01", "rec_price": 50000,
            "strategy_type": "A",
        }
        result = evaluate_recommendation(rec, current_price=55000, days_since=3)
        assert result.day5_return is None
        assert result.correct is None

    def test_zero_rec_price(self):
        rec = {
            "ticker": "005930", "name": "삼성전자",
            "rec_date": "2025-01-01", "rec_price": 0,
            "strategy_type": "A",
        }
        result = evaluate_recommendation(rec, current_price=55000, days_since=10)
        assert result.day5_return is None
        assert result.correct is None


# ---------------------------------------------------------------------------
# compute_strategy_hit_rates
# ---------------------------------------------------------------------------
class TestComputeStrategyHitRates:
    def test_all_profitable(self):
        recs = [
            {"strategy_type": "A", "status": "profit", "pnl_pct": 5.0},
            {"strategy_type": "A", "status": "profit", "pnl_pct": 10.0},
        ]
        result = compute_strategy_hit_rates(recs)
        assert result["A"]["hit_rate"] == 100.0
        assert result["A"]["hits"] == 2

    def test_mixed_results(self):
        recs = [
            {"strategy_type": "B", "status": "profit", "pnl_pct": 5.0},
            {"strategy_type": "B", "status": "stop", "pnl_pct": -3.0},
            {"strategy_type": "B", "status": "stop", "pnl_pct": -2.0},
        ]
        result = compute_strategy_hit_rates(recs)
        b = result["B"]
        assert b["hits"] == 1
        assert b["misses"] == 2
        assert b["hit_rate"] < 60
        assert b["warning"] is not None

    def test_no_completed_recs(self):
        recs = [
            {"strategy_type": "C", "status": "active", "pnl_pct": 0.0},
        ]
        result = compute_strategy_hit_rates(recs)
        assert result["C"]["hit_rate"] == 0.0
        assert result["C"]["warning"] is None

    def test_empty_list(self):
        result = compute_strategy_hit_rates([])
        assert result == {}

    def test_warning_below_60(self):
        recs = [
            {"strategy_type": "D", "status": "profit", "pnl_pct": 2.0},
            {"strategy_type": "D", "status": "stop", "pnl_pct": -1.0},
            {"strategy_type": "D", "status": "stop", "pnl_pct": -3.0},
        ]
        result = compute_strategy_hit_rates(recs)
        assert result["D"]["warning"] is not None
        assert "적중률" in result["D"]["warning"]


# ---------------------------------------------------------------------------
# compute_regime_hit_rates
# ---------------------------------------------------------------------------
class TestComputeRegimeHitRates:
    def test_single_regime(self):
        recs = [
            {"regime": "attack", "status": "profit", "pnl_pct": 5.0},
            {"regime": "attack", "status": "stop", "pnl_pct": -2.0},
        ]
        result = compute_regime_hit_rates(recs)
        assert "attack" in result
        assert result["attack"]["hits"] == 1
        assert result["attack"]["misses"] == 1

    def test_unknown_regime(self):
        recs = [{"status": "profit", "pnl_pct": 5.0}]
        result = compute_regime_hit_rates(recs)
        assert "unknown" in result

    def test_no_completed(self):
        recs = [{"regime": "balanced", "status": "active"}]
        result = compute_regime_hit_rates(recs)
        assert result["balanced"]["assessment"] == "데이터 부족"

    def test_empty(self):
        result = compute_regime_hit_rates([])
        assert result == {}


# ---------------------------------------------------------------------------
# evaluate_diagnosis_accuracy
# ---------------------------------------------------------------------------
class TestEvaluateDiagnosisAccuracy:
    def test_hold_correct(self):
        prev = [{"ticker": "005930", "action": "hold", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 55000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["total"] == 1
        assert result["correct"] == 1
        assert result["accuracy"] == 100.0

    def test_hold_incorrect(self):
        prev = [{"ticker": "005930", "action": "hold", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 45000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["incorrect"] == 1

    def test_stop_loss_correct(self):
        prev = [{"ticker": "005930", "action": "stop_loss", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 40000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["correct"] == 1

    def test_stop_loss_incorrect(self):
        prev = [{"ticker": "005930", "action": "stop_loss", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 55000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["incorrect"] == 1

    def test_message_based_hold(self):
        prev = [{"ticker": "005930", "action": "", "message": "버티세요 반등 예상", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 55000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["correct"] == 1

    def test_missing_ticker_skipped(self):
        prev = [{"ticker": "999999", "action": "hold", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 55000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["total"] == 0

    def test_empty_inputs(self):
        result = evaluate_diagnosis_accuracy([], [])
        assert result["total"] == 0
        assert result["accuracy"] == 0.0

    def test_neutral_action_skipped(self):
        prev = [{"ticker": "005930", "action": "watch", "message": "모니터링", "diagnosis_price": 50000}]
        current = [{"ticker": "005930", "current_price": 55000}]
        result = evaluate_diagnosis_accuracy(prev, current)
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# format_feedback_report
# ---------------------------------------------------------------------------
class TestFormatFeedbackReport:
    def test_basic_report(self):
        report = FeedbackReport(
            period="최근 7일", total_recs=10,
            hits=7, misses=3, pending=2,
            hit_rate=70.0, avg_return=5.5,
            lessons=["현행 전략 유지"],
            strategy_breakdown={
                "A": {"total": 5, "hits": 4, "misses": 1, "hit_rate": 80.0, "avg_return": 6.0, "warning": None},
            },
        )
        result = format_feedback_report(report)
        assert "주간 피드백 리포트" in result
        assert "적중 7건" in result
        assert "미스 3건" in result
        assert "70%" in result
        assert "주호님" in result
        assert "K-Quant" in result

    def test_no_bold(self):
        report = FeedbackReport(
            period="최근 7일", total_recs=5,
            hits=3, misses=2, pending=0,
            hit_rate=60.0, avg_return=2.0,
        )
        result = format_feedback_report(report)
        assert "**" not in result

    def test_no_evaluated(self):
        report = FeedbackReport(
            period="최근 7일", total_recs=3,
            hits=0, misses=0, pending=3,
            hit_rate=0.0, avg_return=0.0,
        )
        result = format_feedback_report(report)
        assert "아직 평가 완료된 추천이 없습니다" in result


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
class TestConstants:
    def test_strategy_labels(self):
        assert "A" in STRATEGY_LABELS
        assert "B" in STRATEGY_LABELS
        assert len(STRATEGY_LABELS) == 7

    def test_recommendation_result_dataclass(self):
        r = RecommendationResult(
            ticker="005930", name="삼성전자",
            rec_date="2025-01-01", rec_price=50000,
            strategy_type="A",
        )
        assert r.day5_return is None
        assert r.correct is None
