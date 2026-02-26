"""Tests for LSTM predictor module."""
import numpy as np
from kstock.ml.lstm_predictor import (
    _HAS_TORCH,
    build_sequences,
    ensemble_3model_predict,
    predict_lstm,
    SEQUENCE_LENGTH,
)


def test_graceful_without_torch():
    """torch 없을 때 neutral 반환."""
    prob = predict_lstm(None, np.zeros((20, 30)))
    assert prob == 0.5


def test_build_sequences_shape():
    features = np.random.randn(100, 30).astype(np.float32)
    targets = np.random.randint(0, 2, 100).astype(np.float32)
    X, y = build_sequences(features, targets, seq_len=20)
    assert X.shape == (80, 20, 30)
    assert y.shape == (80,)


def test_build_sequences_short_data():
    features = np.random.randn(10, 30).astype(np.float32)
    targets = np.random.randint(0, 2, 10).astype(np.float32)
    X, y = build_sequences(features, targets, seq_len=20)
    assert X.shape[0] == 0


def test_ensemble_3model_fallback():
    """LSTM이 0.5일 때 기존 2-모델로 fallback."""
    result = ensemble_3model_predict(0.7, 0.6, 0.5)
    expected = 0.7 * 0.55 + 0.6 * 0.45
    assert abs(result - expected) < 0.01


def test_ensemble_3model_weighted():
    result = ensemble_3model_predict(0.7, 0.6, 0.8)
    expected = 0.35 * 0.7 + 0.30 * 0.6 + 0.35 * 0.8
    assert abs(result - expected) < 0.01


def test_ensemble_bounds():
    result = ensemble_3model_predict(0.0, 0.0, 0.0)
    assert 0.0 <= result <= 1.0
    result = ensemble_3model_predict(1.0, 1.0, 1.0)
    assert 0.0 <= result <= 1.0
