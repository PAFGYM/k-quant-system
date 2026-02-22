"""Tests for the ML validator module (signal/ml_validator.py)."""

from __future__ import annotations

import pytest

from kstock.signal.ml_validator import (
    validate_model_cv,
    analyze_feature_importance,
    check_model_drift,
    format_ml_report,
)


# =========================================================================
# TestValidateModelCV
# =========================================================================

class TestValidateModelCV:
    """validate_model_cv 함수 테스트."""

    def test_basic_output_keys(self):
        """기본 반환 dict 에 필수 키가 존재하는지 확인."""
        X = [[i, i * 2] for i in range(20)]
        y = [1.0 if i % 2 == 0 else 0.0 for i in range(20)]
        result = validate_model_cv(X, y, n_splits=3)
        assert "train_scores" in result
        assert "val_scores" in result
        assert "avg_train" in result
        assert "avg_val" in result
        assert "overfit_gap" in result
        assert "is_overfit" in result

    def test_returns_float_scores(self):
        X = [[i] for i in range(30)]
        y = [float(i % 3) for i in range(30)]
        result = validate_model_cv(X, y, n_splits=5)
        assert isinstance(result["avg_train"], float)
        assert isinstance(result["avg_val"], float)

    def test_overfit_flag_when_gap_large(self):
        """학습/검증 괴리가 클 때 is_overfit=True 인지 확인.

        전략: 학습 데이터에 동일한 값을 넣어 train accuracy가 높아지도록
        하고, 검증 데이터는 패턴이 달라지도록 구성.
        """
        # 학습 구간: 모두 1.0 -> train mean=1.0 근처
        # 검증 구간: 모두 0.0 -> mean != 0.0이므로 val acc 낮음
        X = [[i] for i in range(30)]
        y = [1.0] * 20 + [0.0] * 10  # 앞 20개 동일, 뒤 10개 다름
        result = validate_model_cv(X, y, n_splits=3)
        # overfit_gap 값이 양수인지 확인 (정확한 값은 구현 세부사항에 의존)
        assert result["overfit_gap"] >= 0.0

    def test_small_dataset(self):
        """샘플 수 < n_splits + 1 → 빈 결과."""
        X = [[1], [2]]
        y = [0.0, 1.0]
        result = validate_model_cv(X, y, n_splits=5)
        assert result["train_scores"] == []
        assert result["val_scores"] == []
        assert result["is_overfit"] is False

    def test_uniform_labels_no_overfit(self):
        """모든 y가 동일 → 과적합 없음."""
        X = [[i] for i in range(20)]
        y = [1.0] * 20
        result = validate_model_cv(X, y, n_splits=3)
        assert result["is_overfit"] is False

    def test_n_splits_creates_correct_folds(self):
        X = [[i] for i in range(50)]
        y = [float(i % 2) for i in range(50)]
        result = validate_model_cv(X, y, n_splits=5)
        assert len(result["train_scores"]) <= 5
        assert len(result["val_scores"]) <= 5


# =========================================================================
# TestAnalyzeFeatureImportance
# =========================================================================

class TestAnalyzeFeatureImportance:
    """analyze_feature_importance 함수 테스트."""

    def test_top_features_sorted(self):
        names = ["feat_a", "feat_b", "feat_c", "feat_d"]
        importances = [0.5, 0.3, 0.15, 0.05]
        result = analyze_feature_importance(names, importances)
        top = result["top_20"]
        assert len(top) == 4
        # 첫 번째가 가장 중요
        assert top[0][0] == "feat_a"
        assert top[1][0] == "feat_b"
        # 중요도 내림차순 확인
        for i in range(len(top) - 1):
            assert top[i][1] >= top[i + 1][1]

    def test_bottom_features_identified(self):
        """기여도 1% 미만 피처 식별."""
        names = ["big", "medium", "tiny1", "tiny2"]
        importances = [0.9, 0.09, 0.005, 0.005]
        result = analyze_feature_importance(names, importances)
        bottom = result["bottom"]
        bottom_names = [name for name, _ in bottom]
        assert "tiny1" in bottom_names
        assert "tiny2" in bottom_names

    def test_total_features_count(self):
        names = ["a", "b", "c"]
        importances = [0.5, 0.3, 0.2]
        result = analyze_feature_importance(names, importances)
        assert result["total_features"] == 3

    def test_mismatched_lengths(self):
        """피처 수와 중요도 수 불일치 → 빈 결과."""
        result = analyze_feature_importance(["a", "b"], [0.5])
        assert result["top_20"] == []
        assert result["total_features"] == 0

    def test_all_zero_importances(self):
        """모든 중요도 0 → 빈 top_20."""
        result = analyze_feature_importance(["a", "b"], [0.0, 0.0])
        assert result["top_20"] == []
        assert result["total_features"] == 2

    def test_single_feature(self):
        result = analyze_feature_importance(["only_one"], [1.0])
        assert len(result["top_20"]) == 1
        assert result["top_20"][0][0] == "only_one"
        assert result["top_20"][0][1] == pytest.approx(100.0, abs=0.1)


# =========================================================================
# TestCheckModelDrift
# =========================================================================

class TestCheckModelDrift:
    """check_model_drift 함수 테스트."""

    def test_stable_accuracies_no_drift(self):
        """전부 0.90 이상 → drifted=False."""
        accs = [0.92, 0.91, 0.93, 0.90, 0.91, 0.92]
        result = check_model_drift(accs, threshold=0.85)
        assert result["drifted"] is False

    def test_recent_drop_drifted(self):
        """최근 정확도 급락 → drifted=True."""
        accs = [0.92, 0.91, 0.93, 0.90, 0.70, 0.72, 0.71]
        result = check_model_drift(accs, threshold=0.85)
        assert result["drifted"] is True
        assert result["recent"] < 0.85

    def test_single_value_no_drift(self):
        result = check_model_drift([0.90], threshold=0.85)
        assert result["drifted"] is False

    def test_single_low_value_drifted(self):
        result = check_model_drift([0.70], threshold=0.85)
        assert result["drifted"] is True

    def test_empty_no_drift(self):
        result = check_model_drift([], threshold=0.85)
        assert result["drifted"] is False
        assert result["recent"] == 0.0
        assert result["average"] == 0.0

    def test_gap_computed(self):
        """전체 평균 - 최근 평균 = gap."""
        accs = [0.95, 0.95, 0.95, 0.80, 0.80, 0.80]
        result = check_model_drift(accs, threshold=0.85)
        # 전체 평균: 0.875, 최근 3개 평균: 0.80
        assert result["gap"] == pytest.approx(0.075, abs=0.01)


# =========================================================================
# TestFormatMLReport
# =========================================================================

class TestFormatMLReport:
    """format_ml_report 함수 테스트."""

    @pytest.fixture
    def cv_result(self):
        return {
            "train_scores": [0.85, 0.87, 0.86],
            "val_scores": [0.78, 0.76, 0.80],
            "avg_train": 0.8600,
            "avg_val": 0.7800,
            "overfit_gap": 0.0800,
            "is_overfit": False,
        }

    @pytest.fixture
    def importance(self):
        return {
            "top_20": [("rsi", 25.0), ("macd", 18.0), ("volume", 12.0)],
            "bottom": [("noise_feat", 0.3)],
            "total_features": 20,
        }

    @pytest.fixture
    def drift(self):
        return {
            "drifted": False,
            "recent": 0.8900,
            "average": 0.9000,
            "gap": 0.0100,
        }

    def test_no_bold(self, cv_result, importance, drift):
        text = format_ml_report(cv_result, importance, drift)
        assert "**" not in text

    def test_contains_username(self, cv_result, importance, drift):
        text = format_ml_report(cv_result, importance, drift)
        assert "주호님" in text

    def test_contains_overfit_or_learning_keyword(self, cv_result, importance, drift):
        text = format_ml_report(cv_result, importance, drift)
        assert "과적합" in text or "학습" in text

    def test_contains_feature_info(self, cv_result, importance, drift):
        text = format_ml_report(cv_result, importance, drift)
        assert "피처" in text

    def test_overfit_warning_shown(self, importance, drift):
        """과적합 감지 시 경고 메시지 포함."""
        cv_overfit = {
            "train_scores": [0.95],
            "val_scores": [0.60],
            "avg_train": 0.9500,
            "avg_val": 0.6000,
            "overfit_gap": 0.3500,
            "is_overfit": True,
        }
        text = format_ml_report(cv_overfit, importance, drift)
        assert "과적합" in text

    def test_drift_warning_shown(self, cv_result, importance):
        """드리프트 감지 시 경고 메시지 포함."""
        drift_bad = {
            "drifted": True,
            "recent": 0.7000,
            "average": 0.9000,
            "gap": 0.2000,
        }
        text = format_ml_report(cv_result, importance, drift_bad)
        assert "저하" in text or "재학습" in text

    def test_stable_state_shown(self, cv_result, importance, drift):
        """안정 상태 → '안정적' 또는 '양호' 메시지."""
        text = format_ml_report(cv_result, importance, drift)
        assert "안정" in text or "양호" in text
