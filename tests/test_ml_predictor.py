"""Tests for the ML predictor module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pytest

from kstock.ml.predictor import (
    FEATURE_NAMES,
    PredictionResult,
    _probability_to_label,
    build_features,
    build_training_data,
    format_ml_prediction,
    get_score_bonus,
    predict,
    predict_batch,
    retrain_if_needed,
    should_recommend,
)


# ---------------------------------------------------------------------------
# Mock data objects
# ---------------------------------------------------------------------------


@dataclass
class MockTech:
    """Minimal mock for TechnicalIndicators."""

    rsi: float = 55.0
    bb_pctb: float = 0.6
    bb_bandwidth: float = 0.05
    macd_histogram: float = 0.3
    macd_signal_cross: int = 1
    atr_pct: float = 2.0
    ema_50: float = 50000.0
    ema_200: float = 48000.0
    golden_cross: bool = True
    dead_cross: bool = False
    volume_ratio: float = 1.5
    bb_squeeze: bool = False
    return_3m_pct: float = 12.0
    high_52w: float = 60000.0
    high_20d: float = 55000.0
    mtf_aligned: bool = True
    weekly_trend: str = "up"


@dataclass
class MockInfo:
    """Minimal mock for StockInfo."""

    current_price: float = 52000.0
    market_cap: float = 5_000_000_000_000.0
    per: float = 12.5
    roe: float = 15.0
    debt_ratio: float = 80.0


@dataclass
class MockMacro:
    """Minimal mock for MacroSnapshot."""

    vix: float = 18.0
    spx_change_pct: float = 0.5
    usdkrw: float = 1320.0
    regime: str = "risk_on"


@dataclass
class MockFlow:
    """Minimal mock for FlowData."""

    foreign_net_buy_days: int = 5
    institution_net_buy_days: int = 3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tech() -> MockTech:
    return MockTech()


@pytest.fixture
def info() -> MockInfo:
    return MockInfo()


@pytest.fixture
def macro() -> MockMacro:
    return MockMacro()


@pytest.fixture
def flow() -> MockFlow:
    return MockFlow()


@pytest.fixture
def sample_features(
    tech: MockTech,
    info: MockInfo,
    macro: MockMacro,
    flow: MockFlow,
) -> dict[str, float]:
    return build_features(tech, info, macro, flow, sector_encoded=3, policy_bonus=2)


@pytest.fixture
def sample_historical_data() -> list[dict]:
    """Build a small list of historical feature dicts with targets."""
    rows: list[dict] = []
    for i in range(20):
        row = {name: float(i + j) for j, name in enumerate(FEATURE_NAMES)}
        row["target"] = 1 if i % 3 == 0 else 0
        rows.append(row)
    return rows


# ===========================================================================
# 1. FEATURE_NAMES
# ===========================================================================


class TestFeatureNames:
    def test_feature_names_length(self) -> None:
        """FEATURE_NAMES has exactly 30 entries."""
        assert len(FEATURE_NAMES) == 30

    def test_feature_names_are_unique(self) -> None:
        assert len(set(FEATURE_NAMES)) == 30


# ===========================================================================
# 2-3. build_features
# ===========================================================================


class TestBuildFeatures:
    def test_returns_dict_with_all_30_keys(self, sample_features: dict) -> None:
        """build_features returns dict with all 30 feature keys."""
        assert isinstance(sample_features, dict)
        assert set(sample_features.keys()) == set(FEATURE_NAMES)
        assert len(sample_features) == 30

    def test_values_are_float(self, sample_features: dict) -> None:
        for key, value in sample_features.items():
            assert isinstance(value, float), f"{key} is {type(value)}, expected float"

    def test_handles_missing_attributes_gracefully(self) -> None:
        """build_features works with bare objects that lack attributes."""

        class Empty:
            pass

        result = build_features(
            tech=Empty(),
            info=Empty(),
            macro=Empty(),
            flow=Empty(),
        )
        assert len(result) == 30
        # Defaults should produce finite floats
        for key, value in result.items():
            assert np.isfinite(value), f"{key} is not finite: {value}"

    def test_known_values(
        self,
        tech: MockTech,
        info: MockInfo,
        macro: MockMacro,
        flow: MockFlow,
    ) -> None:
        result = build_features(tech, info, macro, flow, sector_encoded=7, policy_bonus=3)
        assert result["rsi"] == 55.0
        assert result["golden_cross"] == 1.0
        assert result["dead_cross"] == 0.0
        assert result["volume_ratio"] == 1.5
        assert result["vix"] == 18.0
        assert result["regime_encoded"] == 2.0  # risk_on -> 2
        assert result["sector_encoded"] == 7.0
        assert result["policy_bonus"] == 3.0
        assert result["foreign_net_buy_days"] == 5.0
        assert result["weekly_trend_up"] == 1.0  # weekly_trend == "up"

    def test_high_52w_ratio(
        self,
        tech: MockTech,
        info: MockInfo,
        macro: MockMacro,
        flow: MockFlow,
    ) -> None:
        result = build_features(tech, info, macro, flow)
        # current_price / high_52w = 52000 / 60000
        expected = 52000.0 / 60000.0
        assert abs(result["high_52w_ratio"] - expected) < 1e-9


# ===========================================================================
# 4-5. build_training_data
# ===========================================================================


class TestBuildTrainingData:
    def test_valid_data(self, sample_historical_data: list[dict]) -> None:
        """build_training_data returns correct shapes for valid data."""
        X, y = build_training_data(sample_historical_data)
        assert X.shape == (20, 30)
        assert y.shape == (20,)
        assert X.dtype == np.float32
        assert y.dtype == np.int32

    def test_empty_data(self) -> None:
        """build_training_data returns empty arrays for empty input."""
        X, y = build_training_data([])
        assert X.shape == (0, 30)
        assert y.shape == (0,)

    def test_target_values(self, sample_historical_data: list[dict]) -> None:
        """Targets are correctly extracted."""
        X, y = build_training_data(sample_historical_data)
        for i, row in enumerate(sample_historical_data):
            assert y[i] == row["target"]


# ===========================================================================
# 6-7. predict
# ===========================================================================


class TestPredict:
    def test_no_model_returns_neutral(self, sample_features: dict) -> None:
        """predict with model=None returns neutral (0.5, NEUTRAL)."""
        result = predict(sample_features, model=None)
        assert isinstance(result, PredictionResult)
        assert result.probability == 0.5
        assert result.label == "NEUTRAL"
        assert result.shap_top3 == []

    def test_both_submodels_none_returns_neutral(
        self, sample_features: dict
    ) -> None:
        """predict with model dict containing both None values returns neutral."""
        model = {"lgb": None, "xgb": None}
        result = predict(sample_features, model=model)
        assert result.probability == 0.5
        assert result.label == "NEUTRAL"
        assert result.shap_top3 == []


# ===========================================================================
# 8-9. predict_batch
# ===========================================================================


class TestPredictBatch:
    def test_empty_list(self) -> None:
        """predict_batch with empty list returns empty list."""
        result = predict_batch([], model=None)
        assert result == []

    def test_no_model_returns_neutral_for_all(
        self, sample_features: dict
    ) -> None:
        """predict_batch with no model returns neutral for every entry."""
        features_list = [sample_features, sample_features, sample_features]
        results = predict_batch(features_list, model=None)
        assert len(results) == 3
        for r in results:
            assert r.probability == 0.5
            assert r.label == "NEUTRAL"
            assert r.shap_top3 == []

    def test_both_submodels_none_batch(self, sample_features: dict) -> None:
        model = {"lgb": None, "xgb": None}
        results = predict_batch([sample_features], model=model)
        assert len(results) == 1
        assert results[0].probability == 0.5
        assert results[0].label == "NEUTRAL"


# ===========================================================================
# 10-14. get_score_bonus
# ===========================================================================


class TestGetScoreBonus:
    def test_returns_15_for_80_plus(self) -> None:
        """get_score_bonus returns +15 for probability >= 0.80."""
        assert get_score_bonus(0.80) == 15
        assert get_score_bonus(0.90) == 15
        assert get_score_bonus(1.0) == 15

    def test_returns_10_for_70_to_80(self) -> None:
        """get_score_bonus returns +10 for 0.70 <= p < 0.80."""
        assert get_score_bonus(0.70) == 10
        assert get_score_bonus(0.75) == 10
        assert get_score_bonus(0.79) == 10

    def test_returns_5_for_60_to_70(self) -> None:
        """get_score_bonus returns +5 for 0.60 <= p < 0.70."""
        assert get_score_bonus(0.60) == 5
        assert get_score_bonus(0.65) == 5
        assert get_score_bonus(0.69) == 5

    def test_returns_negative_10_for_below_50(self) -> None:
        """get_score_bonus returns -10 for probability < 0.50."""
        assert get_score_bonus(0.49) == -10
        assert get_score_bonus(0.30) == -10
        assert get_score_bonus(0.0) == -10

    def test_returns_0_for_50_to_60(self) -> None:
        """get_score_bonus returns 0 for 0.50 <= p < 0.60."""
        assert get_score_bonus(0.50) == 0
        assert get_score_bonus(0.55) == 0
        assert get_score_bonus(0.59) == 0


# ===========================================================================
# 15-17. should_recommend
# ===========================================================================


class TestShouldRecommend:
    def test_bypasses_etf_strategies(self) -> None:
        """ETF / long-term strategies (B, C, E) always return True."""
        for strategy in ("B", "C", "E"):
            assert should_recommend(strategy, 0.0) is True
            assert should_recommend(strategy, 0.30) is True
            assert should_recommend(strategy, 1.0) is True

    def test_requires_065_for_f_g(self) -> None:
        """Momentum/Breakout strategies (F, G) require probability >= 0.65."""
        for strategy in ("F", "G"):
            assert should_recommend(strategy, 0.65) is True
            assert should_recommend(strategy, 0.80) is True
            assert should_recommend(strategy, 0.64) is False
            assert should_recommend(strategy, 0.50) is False

    def test_requires_060_for_a_d(self) -> None:
        """Bounce/Sector strategies (A, D) require probability >= 0.60."""
        for strategy in ("A", "D"):
            assert should_recommend(strategy, 0.60) is True
            assert should_recommend(strategy, 0.75) is True
            assert should_recommend(strategy, 0.59) is False
            assert should_recommend(strategy, 0.40) is False

    def test_unknown_strategy_passes(self) -> None:
        """Unknown strategy codes pass through (return True)."""
        assert should_recommend("Z", 0.0) is True
        assert should_recommend("X", 0.10) is True


# ===========================================================================
# 18-20. retrain_if_needed
# ===========================================================================


class TestRetrainIfNeeded:
    def test_returns_true_when_no_last_train_date(self) -> None:
        """retrain_if_needed returns True when last_train_date is None."""
        assert retrain_if_needed(data=[], last_train_date=None) is True

    def test_returns_true_when_days_exceeded(self) -> None:
        """retrain_if_needed returns True when interval days have passed."""
        old_date = date.today() - timedelta(days=10)
        assert retrain_if_needed(data=[], last_train_date=old_date, retrain_interval_days=7) is True

    def test_returns_false_when_recent(self) -> None:
        """retrain_if_needed returns False when training is recent."""
        recent_date = date.today() - timedelta(days=2)
        assert retrain_if_needed(data=[], last_train_date=recent_date, retrain_interval_days=7) is False

    def test_boundary_exact_interval(self) -> None:
        """retrain_if_needed returns True when exactly at interval boundary."""
        boundary_date = date.today() - timedelta(days=7)
        assert retrain_if_needed(data=[], last_train_date=boundary_date, retrain_interval_days=7) is True


# ===========================================================================
# 21. format_ml_prediction
# ===========================================================================


class TestFormatMlPrediction:
    def test_returns_formatted_string(self) -> None:
        """format_ml_prediction returns a multi-line formatted string."""
        result = PredictionResult(
            probability=0.85,
            label="STRONG_BUY",
            shap_top3=[("rsi", 0.25), ("volume_ratio", 0.18), ("vix", 0.12)],
        )
        text = format_ml_prediction(result)
        assert isinstance(text, str)
        assert "85.0%" in text
        assert "ML: " in text
        assert "+15" in text  # score bonus for 0.85
        assert "rsi(0.25)" in text
        assert "volume_ratio(0.18)" in text

    def test_neutral_no_bonus_line(self) -> None:
        """Neutral prediction (bonus=0) omits the bonus line."""
        result = PredictionResult(probability=0.55, label="NEUTRAL", shap_top3=[])
        text = format_ml_prediction(result)
        assert "55.0%" in text
        assert "ML 점수 보정" not in text  # bonus is 0, line is skipped

    def test_avoid_negative_bonus(self) -> None:
        """AVOID prediction shows negative bonus."""
        result = PredictionResult(probability=0.35, label="AVOID", shap_top3=[])
        text = format_ml_prediction(result)
        assert "35.0%" in text
        assert "-10" in text

    def test_probability_bar(self) -> None:
        """Probability bar has exactly 10 characters."""
        result = PredictionResult(probability=0.70, label="BUY", shap_top3=[])
        text = format_ml_prediction(result)
        # The bar should appear as [>>>>>>>---] (7 filled, 3 empty)
        assert ">>>>>>>---" in text


# ===========================================================================
# 22. _probability_to_label
# ===========================================================================


class TestProbabilityToLabel:
    @pytest.mark.parametrize(
        ("prob", "expected"),
        [
            (0.95, "STRONG_BUY"),
            (0.80, "STRONG_BUY"),
            (0.79, "BUY"),
            (0.65, "BUY"),
            (0.64, "NEUTRAL"),
            (0.50, "NEUTRAL"),
            (0.49, "AVOID"),
            (0.30, "AVOID"),
            (0.0, "AVOID"),
            (1.0, "STRONG_BUY"),
        ],
    )
    def test_label_mapping(self, prob: float, expected: str) -> None:
        """_probability_to_label maps probability to correct label."""
        assert _probability_to_label(prob) == expected
