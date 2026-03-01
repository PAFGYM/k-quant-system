"""ML model validation with time-series cross-validation (ML 모델 검증).

Provides time-series aware cross-validation, overfitting detection,
feature importance analysis, and model drift detection. All functions
are pure computation with no external API calls.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CVResult:
    """Time-series cross-validation result."""

    n_splits: int = 5
    train_scores: list[float] = field(default_factory=list)
    val_scores: list[float] = field(default_factory=list)
    avg_train: float = 0.0
    avg_val: float = 0.0
    overfit_gap: float = 0.0
    is_overfit: bool = False


@dataclass
class FeatureImportanceResult:
    """Feature importance analysis result."""

    top_20: list[tuple[str, float]] = field(default_factory=list)
    bottom: list[tuple[str, float]] = field(default_factory=list)
    total_features: int = 0


@dataclass
class DriftResult:
    """Model drift detection result."""

    drifted: bool = False
    recent: float = 0.0
    average: float = 0.0
    gap: float = 0.0


# ---------------------------------------------------------------------------
# Time-series cross-validation
# ---------------------------------------------------------------------------

def validate_model_cv(
    X: list[list[float]],
    y: list[float],
    n_splits: int = 5,
) -> dict:
    """시계열 교차검증을 수행합니다.

    Manual TimeSeriesSplit implementation (sklearn 미사용).
    각 fold에서 train/val 점수를 계산하고 과적합 여부를 판단합니다.

    Returns:
        dict with keys: train_scores, val_scores, avg_train, avg_val,
        overfit_gap, is_overfit
    """
    try:
        n_samples = len(X)
        if n_samples < n_splits + 1:
            logger.warning("샘플 수(%d)가 fold 수(%d)보다 적습니다.", n_samples, n_splits)
            return {
                "train_scores": [],
                "val_scores": [],
                "avg_train": 0.0,
                "avg_val": 0.0,
                "overfit_gap": 0.0,
                "is_overfit": False,
            }

        # Manual time-series split: increasing training window
        min_train_size = max(2, n_samples // (n_splits + 1))
        fold_size = (n_samples - min_train_size) // n_splits

        train_scores: list[float] = []
        val_scores: list[float] = []

        for i in range(n_splits):
            train_end = min_train_size + fold_size * i
            val_end = min(train_end + fold_size, n_samples)

            if train_end >= n_samples or val_end <= train_end:
                continue

            y_train = y[:train_end]
            y_val = y[train_end:val_end]

            if not y_train or not y_val:
                continue

            # Simple accuracy proxy: mean of training labels predicts validation
            train_mean = sum(y_train) / len(y_train)

            # Train "accuracy" - how well mean predicts train
            train_correct = sum(
                1 for v in y_train if abs(v - train_mean) < 0.5
            )
            train_acc = train_correct / len(y_train) if y_train else 0.0

            # Validation "accuracy"
            val_correct = sum(
                1 for v in y_val if abs(v - train_mean) < 0.5
            )
            val_acc = val_correct / len(y_val) if y_val else 0.0

            train_scores.append(round(train_acc, 4))
            val_scores.append(round(val_acc, 4))

        avg_train = sum(train_scores) / len(train_scores) if train_scores else 0.0
        avg_val = sum(val_scores) / len(val_scores) if val_scores else 0.0
        overfit_gap = round(avg_train - avg_val, 4)
        is_overfit = overfit_gap > 0.10

        if is_overfit:
            logger.warning(
                "과적합 감지: train=%.4f, val=%.4f, gap=%.4f",
                avg_train, avg_val, overfit_gap,
            )

        return {
            "train_scores": train_scores,
            "val_scores": val_scores,
            "avg_train": round(avg_train, 4),
            "avg_val": round(avg_val, 4),
            "overfit_gap": overfit_gap,
            "is_overfit": is_overfit,
        }

    except Exception as e:
        logger.error("교차검증 실패: %s", e, exc_info=True)
        return {
            "train_scores": [],
            "val_scores": [],
            "avg_train": 0.0,
            "avg_val": 0.0,
            "overfit_gap": 0.0,
            "is_overfit": False,
        }


# ---------------------------------------------------------------------------
# Feature importance analysis
# ---------------------------------------------------------------------------

def analyze_feature_importance(
    feature_names: list[str],
    importances: list[float],
) -> dict:
    """피처 중요도를 분석합니다.

    중요도 기준으로 정렬하고 하위 피처(기여도 1% 미만)를 식별합니다.
    """
    try:
        if len(feature_names) != len(importances):
            logger.error("피처 이름 수(%d)와 중요도 수(%d)가 불일치합니다.",
                         len(feature_names), len(importances))
            return {"top_20": [], "bottom": [], "total_features": 0}

        total = sum(abs(v) for v in importances)
        if total == 0:
            logger.warning("모든 피처 중요도가 0입니다.")
            return {"top_20": [], "bottom": [], "total_features": len(feature_names)}

        # Normalize to percentages and pair with names
        paired = [
            (name, round(abs(imp) / total * 100, 2))
            for name, imp in zip(feature_names, importances)
        ]
        paired.sort(key=lambda x: x[1], reverse=True)

        top_20 = paired[:20]
        bottom = [(name, pct) for name, pct in paired if pct < 1.0]

        if bottom:
            logger.info("기여도 1%% 미만 피처 %d개 감지", len(bottom))

        return {
            "top_20": top_20,
            "bottom": bottom,
            "total_features": len(feature_names),
        }

    except Exception as e:
        logger.error("피처 중요도 분석 실패: %s", e, exc_info=True)
        return {"top_20": [], "bottom": [], "total_features": 0}


# ---------------------------------------------------------------------------
# Model drift detection
# ---------------------------------------------------------------------------

def check_model_drift(
    monthly_accuracies: list[float],
    threshold: float = 0.85,
) -> dict:
    """모델 드리프트를 감지합니다.

    최근 3개월 평균 정확도와 전체 평균을 비교합니다.
    """
    try:
        if not monthly_accuracies:
            logger.warning("월별 정확도 데이터가 비어있습니다.")
            return {"drifted": False, "recent": 0.0, "average": 0.0, "gap": 0.0}

        overall_avg = sum(monthly_accuracies) / len(monthly_accuracies)
        recent_window = min(3, len(monthly_accuracies))
        recent_avg = sum(monthly_accuracies[-recent_window:]) / recent_window

        gap = round(overall_avg - recent_avg, 4)
        drifted = recent_avg < threshold

        if drifted:
            logger.warning(
                "모델 드리프트 감지: 최근 %.4f < 기준 %.4f (gap=%.4f)",
                recent_avg, threshold, gap,
            )

        return {
            "drifted": drifted,
            "recent": round(recent_avg, 4),
            "average": round(overall_avg, 4),
            "gap": gap,
        }

    except Exception as e:
        logger.error("드리프트 감지 실패: %s", e, exc_info=True)
        return {"drifted": False, "recent": 0.0, "average": 0.0, "gap": 0.0}


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_ml_report(
    cv_result: dict,
    importance: dict,
    drift: dict,
) -> str:
    """ML 검증 리포트를 텔레그램 형식으로 생성합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
        lines = [
            f"[ML 모델 검증 리포트] {now}",
            f"{USER_NAME}, ML 모델 상태를 점검했습니다.",
            "",
            "-- 교차검증 --",
            f"  학습 평균: {cv_result.get('avg_train', 0):.4f}",
            f"  검증 평균: {cv_result.get('avg_val', 0):.4f}",
            f"  과적합 갭: {cv_result.get('overfit_gap', 0):.4f}",
        ]

        if cv_result.get("is_overfit"):
            lines.append("  경고: 과적합이 감지되었습니다 (갭 > 0.10)")
        else:
            lines.append("  상태: 양호")

        lines.append("")
        lines.append("-- 피처 중요도 --")
        lines.append(f"  전체 피처 수: {importance.get('total_features', 0)}")

        top_features = importance.get("top_20", [])
        if top_features:
            lines.append("  상위 5개:")
            for name, pct in top_features[:5]:
                lines.append(f"    {name}: {pct:.1f}%")

        bottom_count = len(importance.get("bottom", []))
        if bottom_count:
            lines.append(f"  기여도 1% 미만: {bottom_count}개 (제거 검토)")

        lines.append("")
        lines.append("-- 드리프트 --")
        lines.append(f"  최근 정확도: {drift.get('recent', 0):.4f}")
        lines.append(f"  전체 평균: {drift.get('average', 0):.4f}")

        if drift.get("drifted"):
            lines.append("  경고: 모델 성능 저하가 감지되었습니다. 재학습을 권장합니다.")
        else:
            lines.append("  상태: 안정적")

        return "\n".join(lines)

    except Exception as e:
        logger.error("ML 리포트 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, ML 리포트 생성 중 오류가 발생했습니다."
