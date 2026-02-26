"""ML AutoTrainer 테스트."""
import pytest
from datetime import date, timedelta

from kstock.ml.auto_trainer import (
    ModelMonitor, ModelPerformance, DriftReport,
    AutoTrainer, optimize_ensemble_weights,
    _generate_synthetic_training_data,
)


def test_model_monitor_init():
    monitor = ModelMonitor()
    assert monitor._baseline_accuracy == 0.6
    assert len(monitor._prediction_log) == 0


def test_log_prediction():
    monitor = ModelMonitor()
    monitor.log_prediction("005930", 0.75, actual_return_5d=0.05)
    assert len(monitor._prediction_log) == 1
    assert monitor._prediction_log[0]["correct"] is True


def test_log_prediction_wrong():
    monitor = ModelMonitor()
    monitor.log_prediction("005930", 0.75, actual_return_5d=0.01)
    assert monitor._prediction_log[0]["correct"] is False


def test_log_prediction_no_actual():
    monitor = ModelMonitor()
    monitor.log_prediction("005930", 0.75)
    assert monitor._prediction_log[0]["correct"] is None


def test_evaluate_recent_insufficient_data():
    monitor = ModelMonitor()
    perf = monitor.evaluate_recent()
    assert perf.predictions_count == 0
    assert perf.accuracy == 0.6  # baseline


def test_evaluate_recent_with_data():
    monitor = ModelMonitor()
    # 30 correct, 10 wrong
    for i in range(30):
        monitor.log_prediction(f"T{i}", 0.7, actual_return_5d=0.05)
    for i in range(10):
        monitor.log_prediction(f"T{i+30}", 0.7, actual_return_5d=0.01)

    perf = monitor.evaluate_recent(days=30)
    assert perf.predictions_count == 40
    assert perf.accuracy == pytest.approx(0.75, abs=0.01)


def test_detect_drift_no_train():
    monitor = ModelMonitor()
    drift = monitor.detect_drift(last_train_date=None)
    assert drift.retrain_recommended
    assert drift.days_since_train == 999


def test_detect_drift_recent_train():
    monitor = ModelMonitor()
    drift = monitor.detect_drift(last_train_date=date.today())
    # No data for evaluation, but trained today
    assert drift.days_since_train == 0


def test_detect_drift_old_train():
    monitor = ModelMonitor()
    old_date = date.today() - timedelta(days=30)
    drift = monitor.detect_drift(last_train_date=old_date)
    assert drift.retrain_recommended
    assert drift.days_since_train == 30


def test_optimize_weights_empty():
    weights = optimize_ensemble_weights([], [], [], [])
    assert weights == (0.35, 0.30, 0.35)


def test_optimize_weights_basic():
    lgb = [0.7] * 50 + [0.3] * 50
    xgb = [0.6] * 50 + [0.4] * 50
    lstm = [0.8] * 50 + [0.2] * 50
    actuals = [1] * 50 + [0] * 50

    w = optimize_ensemble_weights(lgb, xgb, lstm, actuals)
    assert len(w) == 3
    assert abs(sum(w) - 1.0) < 0.05


def test_synthetic_training_data():
    data = _generate_synthetic_training_data(20)
    assert len(data) == 20
    for d in data:
        assert "target" in d
        assert "rsi" in d
        assert "vix" in d


def test_auto_trainer_init():
    trainer = AutoTrainer()
    assert trainer._current_weights == (0.35, 0.30, 0.35)
    assert trainer._last_train_date is None


def test_auto_trainer_format_report():
    trainer = AutoTrainer()
    report = trainer.format_train_report()
    assert "ML" in report


def test_auto_trainer_should_retrain_fresh():
    trainer = AutoTrainer()
    drift = trainer.should_retrain()
    assert drift.retrain_recommended


def test_update_baseline():
    monitor = ModelMonitor()
    monitor.update_baseline(0.75)
    assert monitor._baseline_accuracy == 0.75
    # Should not update with bad accuracy
    monitor.update_baseline(0.3)
    assert monitor._baseline_accuracy == 0.75
