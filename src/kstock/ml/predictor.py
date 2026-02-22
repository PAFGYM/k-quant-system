"""K-Quant v3.0 LightGBM + XGBoost ensemble predictor.

Binary classification: will the stock's 5-business-day return exceed +3%?
Uses 30 hand-crafted features drawn from the existing K-Quant data pipeline.

The module degrades gracefully -- if lightgbm, xgboost, shap, or optuna are
not installed, predictions fall back to a neutral 50% probability.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Optional heavy dependencies -- import with graceful fallback
# ---------------------------------------------------------------------------

try:
    import lightgbm as lgb

    _HAS_LGB = True
except (ImportError, OSError, Exception):  # pragma: no cover
    lgb = None  # type: ignore[assignment]
    _HAS_LGB = False

try:
    from xgboost import XGBClassifier

    _HAS_XGB = True
except (ImportError, OSError, Exception):  # pragma: no cover
    XGBClassifier = None  # type: ignore[assignment,misc]
    _HAS_XGB = False

try:
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score, log_loss

    _HAS_SKLEARN = True
except (ImportError, OSError, Exception):  # pragma: no cover
    TimeSeriesSplit = None  # type: ignore[assignment,misc]
    roc_auc_score = None  # type: ignore[assignment]
    log_loss = None  # type: ignore[assignment]
    _HAS_SKLEARN = False

try:
    import optuna

    _HAS_OPTUNA = True
except (ImportError, OSError, Exception):  # pragma: no cover
    optuna = None  # type: ignore[assignment]
    _HAS_OPTUNA = False

try:
    import shap

    _HAS_SHAP = True
except (ImportError, OSError, Exception):  # pragma: no cover
    shap = None  # type: ignore[assignment]
    _HAS_SHAP = False


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FEATURE_NAMES: list[str] = [
    # Technical (12)
    "rsi",
    "bb_pctb",
    "bb_bandwidth",
    "macd_histogram",
    "macd_signal_cross",
    "atr_pct",
    "ema_50",
    "ema_200",
    "golden_cross",
    "dead_cross",
    "volume_ratio",
    "bb_squeeze",
    # Momentum (6)
    "return_3m_pct",
    "high_52w_ratio",
    "high_20d_ratio",
    "mtf_aligned",
    "weekly_trend_up",
    "rs_percentile",
    # Macro (4)
    "vix",
    "spx_change_pct",
    "usdkrw",
    "regime_encoded",
    # Fundamental (4)
    "per",
    "roe",
    "debt_ratio",
    "market_cap_log",
    # Flow proxy (2)
    "foreign_net_buy_days",
    "institution_net_buy_days",
    # Other (2)
    "sector_encoded",
    "policy_bonus",
]

_NUM_FEATURES = 30

assert len(FEATURE_NAMES) == _NUM_FEATURES, (
    f"Expected {_NUM_FEATURES} features, got {len(FEATURE_NAMES)}"
)

_REGIME_MAP: dict[str, int] = {
    "risk_on": 2,
    "neutral": 1,
    "risk_off": 0,
}

_NEUTRAL_PROB = 0.5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PredictionResult:
    """Result of a single ML prediction."""

    probability: float  # 0.0 ~ 1.0
    label: str  # "STRONG_BUY", "BUY", "NEUTRAL", "AVOID"
    shap_top3: list[tuple[str, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def build_features(
    tech: Any,
    info: Any,
    macro: Any,
    flow: Any,
    sector_encoded: int = 0,
    policy_bonus: int = 0,
) -> dict[str, float]:
    """Build the 30-feature dict from existing K-Quant data objects.

    Args:
        tech: ``TechnicalIndicators`` instance.
        info: ``StockInfo`` instance.
        macro: ``MacroSnapshot`` instance.
        flow: ``FlowData`` instance (from ``scoring.py``).
        sector_encoded: Integer-encoded sector (0 = unknown).
        policy_bonus: Score bonus from ``policy_engine.get_score_bonus``.

    Returns:
        Dict mapping each of the 30 feature names to a float value.
    """
    current_price = getattr(info, "current_price", 0.0) or 1.0
    high_52w = getattr(tech, "high_52w", 0.0) or 1.0
    high_20d = getattr(tech, "high_20d", 0.0) or 1.0
    market_cap = getattr(info, "market_cap", 0.0)
    market_cap_log = math.log10(max(market_cap, 1.0))

    weekly_trend = getattr(tech, "weekly_trend", "neutral")

    return {
        # Technical (12)
        "rsi": float(getattr(tech, "rsi", 50.0)),
        "bb_pctb": float(getattr(tech, "bb_pctb", 0.5)),
        "bb_bandwidth": float(getattr(tech, "bb_bandwidth", 0.0)),
        "macd_histogram": float(getattr(tech, "macd_histogram", 0.0)),
        "macd_signal_cross": float(getattr(tech, "macd_signal_cross", 0)),
        "atr_pct": float(getattr(tech, "atr_pct", 0.0)),
        "ema_50": float(getattr(tech, "ema_50", 0.0)),
        "ema_200": float(getattr(tech, "ema_200", 0.0)),
        "golden_cross": float(getattr(tech, "golden_cross", False)),
        "dead_cross": float(getattr(tech, "dead_cross", False)),
        "volume_ratio": float(getattr(tech, "volume_ratio", 1.0)),
        "bb_squeeze": float(getattr(tech, "bb_squeeze", False)),
        # Momentum (6)
        "return_3m_pct": float(getattr(tech, "return_3m_pct", 0.0)),
        "high_52w_ratio": current_price / high_52w,
        "high_20d_ratio": current_price / high_20d,
        "mtf_aligned": float(getattr(tech, "mtf_aligned", False)),
        "weekly_trend_up": float(weekly_trend == "up"),
        "rs_percentile": 50.0,  # caller should override with actual value
        # Macro (4)
        "vix": float(getattr(macro, "vix", 20.0)),
        "spx_change_pct": float(getattr(macro, "spx_change_pct", 0.0)),
        "usdkrw": float(getattr(macro, "usdkrw", 1300.0)),
        "regime_encoded": float(_REGIME_MAP.get(
            getattr(macro, "regime", "neutral"), 1,
        )),
        # Fundamental (4)
        "per": float(getattr(info, "per", 0.0)),
        "roe": float(getattr(info, "roe", 0.0)),
        "debt_ratio": float(getattr(info, "debt_ratio", 0.0)),
        "market_cap_log": market_cap_log,
        # Flow proxy (2)
        "foreign_net_buy_days": float(
            getattr(flow, "foreign_net_buy_days", 0),
        ),
        "institution_net_buy_days": float(
            getattr(flow, "institution_net_buy_days", 0),
        ),
        # Other (2)
        "sector_encoded": float(sector_encoded),
        "policy_bonus": float(policy_bonus),
    }


def build_training_data(
    historical_data: list[dict],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert a list of historical feature dicts into numpy arrays.

    Each element of *historical_data* must contain the 30 feature keys
    **plus** a ``"target"`` key (1 if 5-day return > 3%, else 0).

    Returns:
        ``(X, y)`` where ``X`` has shape ``(n_samples, 30)`` and ``y``
        has shape ``(n_samples,)``.
    """
    n = len(historical_data)
    if n == 0:
        return np.empty((0, _NUM_FEATURES)), np.empty((0,))

    X = np.zeros((n, _NUM_FEATURES), dtype=np.float32)
    y = np.zeros(n, dtype=np.int32)

    for i, row in enumerate(historical_data):
        for j, fname in enumerate(FEATURE_NAMES):
            X[i, j] = float(row.get(fname, 0.0))
        y[i] = int(row.get("target", 0))

    logger.info(
        "Built training data: %d samples, %d features, pos_rate=%.2f%%",
        n, _NUM_FEATURES, y.mean() * 100,
    )
    return X, y


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def _optuna_lgb_objective(
    trial: Any,
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 5,
) -> float:
    """Optuna objective for LightGBM hyper-parameter tuning."""
    params = {
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
        "n_jobs": -1,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 15, 127),
        "max_depth": trial.suggest_int("max_depth", 3, 12),
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
    }

    tscv = TimeSeriesSplit(n_splits=n_splits)
    auc_scores: list[float] = []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        dtrain = lgb.Dataset(X_tr, label=y_tr)
        dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

        model = lgb.train(
            params,
            dtrain,
            num_boost_round=300,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )

        preds = model.predict(X_val)
        auc = roc_auc_score(y_val, preds)
        auc_scores.append(auc)

    return float(np.mean(auc_scores))


def _train_lgb(
    X: np.ndarray,
    y: np.ndarray,
    n_trials: int = 30,
) -> tuple[Any, dict[str, float]]:
    """Train LightGBM with Optuna hyper-parameter search.

    Returns:
        ``(model, best_params)``
    """
    if not (_HAS_LGB and _HAS_SKLEARN and _HAS_OPTUNA):
        logger.warning(
            "LightGBM/sklearn/optuna not available; skipping LGB training",
        )
        return None, {}

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda trial: _optuna_lgb_objective(trial, X, y),
        n_trials=n_trials,
        show_progress_bar=False,
    )

    best = study.best_params
    logger.info("LGB Optuna best AUC=%.4f params=%s", study.best_value, best)

    # Retrain on full data with best params
    params = {
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
        "n_jobs": -1,
        **best,
    }

    dtrain = lgb.Dataset(X, label=y)
    model = lgb.train(params, dtrain, num_boost_round=300)
    return model, best


def _train_xgb(
    X: np.ndarray,
    y: np.ndarray,
    lgb_params: dict[str, Any] | None = None,
) -> Any | None:
    """Train XGBClassifier with params similar to tuned LightGBM.

    Returns:
        Fitted ``XGBClassifier`` or ``None``.
    """
    if not (_HAS_XGB and _HAS_SKLEARN):
        logger.warning("XGBoost/sklearn not available; skipping XGB training")
        return None

    lgb_params = lgb_params or {}

    xgb_params = {
        "n_estimators": 300,
        "learning_rate": lgb_params.get("learning_rate", 0.05),
        "max_depth": lgb_params.get("max_depth", 6),
        "subsample": lgb_params.get("subsample", 0.8),
        "colsample_bytree": lgb_params.get("colsample_bytree", 0.8),
        "reg_alpha": lgb_params.get("reg_alpha", 0.1),
        "reg_lambda": lgb_params.get("reg_lambda", 1.0),
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "use_label_encoder": False,
        "verbosity": 0,
        "n_jobs": -1,
    }

    model = XGBClassifier(**xgb_params)
    model.fit(X, y)
    return model


def _walk_forward_validate(
    X: np.ndarray,
    y: np.ndarray,
    train_months: int = 6,
    test_months: int = 2,
    trading_days_per_month: int = 21,
) -> dict[str, Any]:
    """Walk-forward validation: train on *train_months*, test on *test_months*.

    If the AUC gap between train and test exceeds 15 percentage points the
    result includes an ``overfitting_warning``.

    Returns:
        Dict with ``train_auc``, ``test_auc``, ``auc_gap``, and
        ``overfitting_warning`` (bool).
    """
    if not (_HAS_LGB and _HAS_SKLEARN):
        return {
            "train_auc": 0.0,
            "test_auc": 0.0,
            "auc_gap": 0.0,
            "overfitting_warning": False,
        }

    train_size = train_months * trading_days_per_month
    test_size = test_months * trading_days_per_month
    total_needed = train_size + test_size

    if len(X) < total_needed:
        logger.warning(
            "Not enough data for walk-forward (%d < %d); skipping",
            len(X), total_needed,
        )
        return {
            "train_auc": 0.0,
            "test_auc": 0.0,
            "auc_gap": 0.0,
            "overfitting_warning": False,
        }

    train_aucs: list[float] = []
    test_aucs: list[float] = []

    start = 0
    while start + total_needed <= len(X):
        tr_end = start + train_size
        te_end = tr_end + test_size

        X_tr, y_tr = X[start:tr_end], y[start:tr_end]
        X_te, y_te = X[tr_end:te_end], y[tr_end:te_end]

        # Quick LGB fit (no Optuna for validation speed)
        params = {
            "objective": "binary",
            "metric": "auc",
            "verbosity": -1,
            "num_leaves": 31,
            "learning_rate": 0.05,
            "n_jobs": -1,
        }
        dtrain = lgb.Dataset(X_tr, label=y_tr)
        model = lgb.train(params, dtrain, num_boost_round=200)

        p_tr = model.predict(X_tr)
        p_te = model.predict(X_te)

        if len(np.unique(y_tr)) > 1:
            train_aucs.append(roc_auc_score(y_tr, p_tr))
        if len(np.unique(y_te)) > 1:
            test_aucs.append(roc_auc_score(y_te, p_te))

        start += test_size  # roll forward by test window

    avg_train = float(np.mean(train_aucs)) if train_aucs else 0.0
    avg_test = float(np.mean(test_aucs)) if test_aucs else 0.0
    gap = avg_train - avg_test
    overfit = gap > 0.15

    if overfit:
        logger.warning(
            "Overfitting detected: train_auc=%.4f test_auc=%.4f gap=%.4f",
            avg_train, avg_test, gap,
        )

    return {
        "train_auc": round(avg_train, 4),
        "test_auc": round(avg_test, 4),
        "auc_gap": round(gap, 4),
        "overfitting_warning": overfit,
    }


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    n_trials: int = 30,
) -> dict[str, Any]:
    """Train the LightGBM + XGBoost ensemble.

    Args:
        X: Feature matrix ``(n_samples, 30)``.
        y: Binary target array ``(n_samples,)``.
        n_trials: Number of Optuna trials for LGB hyper-parameter search.

    Returns:
        Dict containing:
        - ``model``: dict with ``"lgb"`` and ``"xgb"`` sub-models.
        - ``metrics``: walk-forward validation metrics.
        - ``feature_importance``: list of ``(feature_name, importance)``
          sorted descending by importance.
    """
    if len(X) == 0:
        logger.warning("Empty training data; returning stub model")
        return {
            "model": {"lgb": None, "xgb": None},
            "metrics": {},
            "feature_importance": [],
        }

    logger.info(
        "Training ML model on %d samples (%d positive, %.1f%%)",
        len(y), int(y.sum()), y.mean() * 100,
    )

    # 1. LightGBM with Optuna
    lgb_model, lgb_best_params = _train_lgb(X, y, n_trials=n_trials)

    # 2. XGBoost with similar params
    xgb_model = _train_xgb(X, y, lgb_params=lgb_best_params)

    # 3. Walk-forward validation
    wf_metrics = _walk_forward_validate(X, y)

    # 4. Feature importance (from LGB if available, else XGB)
    importance: list[tuple[str, float]] = []
    if lgb_model is not None:
        raw_imp = lgb_model.feature_importance(importance_type="gain")
        total = raw_imp.sum() or 1.0
        for fname, imp in zip(FEATURE_NAMES, raw_imp):
            importance.append((fname, round(float(imp / total), 4)))
    elif xgb_model is not None:
        raw_imp = xgb_model.feature_importances_
        for fname, imp in zip(FEATURE_NAMES, raw_imp):
            importance.append((fname, round(float(imp), 4)))

    importance.sort(key=lambda t: t[1], reverse=True)

    logger.info("Training complete. Walk-forward: %s", wf_metrics)

    return {
        "model": {"lgb": lgb_model, "xgb": xgb_model},
        "metrics": wf_metrics,
        "feature_importance": importance,
    }


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------


def _ensemble_predict(
    lgb_model: Any | None,
    xgb_model: Any | None,
    X: np.ndarray,
) -> np.ndarray:
    """Soft-vote average of LGB and XGB predicted probabilities.

    Falls back to a single model if only one is available, or to
    ``_NEUTRAL_PROB`` if neither is present.
    """
    probs: list[np.ndarray] = []

    if lgb_model is not None:
        probs.append(lgb_model.predict(X))

    if xgb_model is not None:
        p = xgb_model.predict_proba(X)
        # predict_proba returns (n, 2); take the positive class column
        probs.append(p[:, 1] if p.ndim == 2 else p)

    if not probs:
        return np.full(len(X), _NEUTRAL_PROB)

    return np.mean(probs, axis=0)


def _probability_to_label(prob: float) -> str:
    """Map a probability to a human-readable label."""
    if prob >= 0.80:
        return "STRONG_BUY"
    if prob >= 0.65:
        return "BUY"
    if prob >= 0.50:
        return "NEUTRAL"
    return "AVOID"


def predict(
    features_dict: dict[str, float],
    model: dict[str, Any] | None = None,
) -> PredictionResult:
    """Produce a single ML prediction.

    Args:
        features_dict: Dict of 30 features (see ``FEATURE_NAMES``).
        model: Dict with ``"lgb"`` and ``"xgb"`` sub-models.
            If ``None`` or both sub-models are ``None``, returns a
            neutral prediction.

    Returns:
        ``PredictionResult`` with probability, label, and SHAP top-3.
    """
    lgb_model = model.get("lgb") if model else None
    xgb_model = model.get("xgb") if model else None

    if lgb_model is None and xgb_model is None:
        logger.debug("No trained model available; returning neutral prediction")
        return PredictionResult(
            probability=_NEUTRAL_PROB,
            label="NEUTRAL",
            shap_top3=[],
        )

    X = np.array(
        [[features_dict.get(f, 0.0) for f in FEATURE_NAMES]],
        dtype=np.float32,
    )

    prob = float(_ensemble_predict(lgb_model, xgb_model, X)[0])
    prob = max(0.0, min(1.0, prob))
    label = _probability_to_label(prob)

    # SHAP explanation (best-effort)
    shap_top3 = get_shap_explanation(
        lgb_model or xgb_model,
        features_dict,
    )

    return PredictionResult(
        probability=round(prob, 4),
        label=label,
        shap_top3=shap_top3,
    )


def predict_batch(
    features_list: list[dict[str, float]],
    model: dict[str, Any] | None = None,
) -> list[PredictionResult]:
    """Batch prediction for multiple stocks.

    Args:
        features_list: List of feature dicts.
        model: Dict with ``"lgb"`` and ``"xgb"`` sub-models.

    Returns:
        List of ``PredictionResult`` in the same order as input.
    """
    if not features_list:
        return []

    lgb_model = model.get("lgb") if model else None
    xgb_model = model.get("xgb") if model else None

    if lgb_model is None and xgb_model is None:
        return [
            PredictionResult(
                probability=_NEUTRAL_PROB,
                label="NEUTRAL",
                shap_top3=[],
            )
            for _ in features_list
        ]

    X = np.array(
        [[fd.get(f, 0.0) for f in FEATURE_NAMES] for fd in features_list],
        dtype=np.float32,
    )

    probs = _ensemble_predict(lgb_model, xgb_model, X)

    results: list[PredictionResult] = []
    for i, fd in enumerate(features_list):
        prob = float(np.clip(probs[i], 0.0, 1.0))
        shap_top3 = get_shap_explanation(lgb_model or xgb_model, fd)
        results.append(PredictionResult(
            probability=round(prob, 4),
            label=_probability_to_label(prob),
            shap_top3=shap_top3,
        ))

    return results


# ---------------------------------------------------------------------------
# Score bonus
# ---------------------------------------------------------------------------


def get_score_bonus(probability: float) -> int:
    """Map ML probability to a composite-score bonus/penalty.

    Returns:
        +15 for >= 80%, +10 for 70-80%, +5 for 60-70%, -10 for < 50%.
    """
    if probability >= 0.80:
        return 15
    if probability >= 0.70:
        return 10
    if probability >= 0.60:
        return 5
    if probability < 0.50:
        return -10
    return 0


# ---------------------------------------------------------------------------
# ML filter
# ---------------------------------------------------------------------------


def should_recommend(strategy: str, probability: float) -> bool:
    """Decide whether the ML filter passes for a given strategy.

    Args:
        strategy: Strategy code (``"A"`` .. ``"G"``).
        probability: ML-predicted probability of +3% in 5 days.

    Returns:
        ``True`` if the stock should proceed to recommendation.
    """
    # ETF and long-term strategies bypass the ML filter
    if strategy in ("B", "C", "E"):
        return True

    # Momentum and Breakout require higher confidence
    if strategy in ("F", "G"):
        return probability >= 0.65

    # Bounce (A) and Sector (D)
    if strategy in ("A", "D"):
        return probability >= 0.60

    # Unknown strategy -- pass through
    return True


# ---------------------------------------------------------------------------
# Auto-retrain
# ---------------------------------------------------------------------------


def retrain_if_needed(
    data: list[dict],
    last_train_date: date | None,
    retrain_interval_days: int = 7,
) -> bool:
    """Check whether the model should be retrained.

    Retrain when:
    - No model has ever been trained (``last_train_date is None``).
    - More than ``retrain_interval_days`` have elapsed since the last train.

    Returns:
        ``True`` if retraining is needed.
    """
    if last_train_date is None:
        logger.info("No previous training date; retraining needed")
        return True

    today = date.today()
    days_since = (today - last_train_date).days

    if days_since >= retrain_interval_days:
        logger.info(
            "Last training was %d days ago (>= %d); retraining needed",
            days_since, retrain_interval_days,
        )
        return True

    logger.debug(
        "Last training was %d days ago (< %d); no retrain needed",
        days_since, retrain_interval_days,
    )
    return False


# ---------------------------------------------------------------------------
# SHAP explanation
# ---------------------------------------------------------------------------


def get_shap_explanation(
    model: Any | None,
    features: dict[str, float],
) -> list[tuple[str, float]]:
    """Return the top-3 most impactful features for a single prediction.

    Uses SHAP ``TreeExplainer`` when available; otherwise falls back to
    the model's ``feature_importances_`` attribute.

    Returns:
        List of ``(feature_name, importance)`` tuples, length <= 3.
    """
    if model is None:
        return []

    X_single = np.array(
        [[features.get(f, 0.0) for f in FEATURE_NAMES]],
        dtype=np.float32,
    )

    # Try SHAP TreeExplainer
    if _HAS_SHAP:
        try:
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_single)

            # shap_values may be a list (for binary classifiers) or ndarray
            if isinstance(shap_values, list):
                vals = np.abs(shap_values[1][0])  # positive class
            elif shap_values.ndim == 3:
                vals = np.abs(shap_values[0, :, 1])
            else:
                vals = np.abs(shap_values[0])

            ranked = sorted(
                zip(FEATURE_NAMES, vals),
                key=lambda t: t[1],
                reverse=True,
            )
            return [(name, round(float(v), 4)) for name, v in ranked[:3]]
        except Exception as exc:
            logger.debug("SHAP explanation failed: %s", exc)

    # Fallback: global feature importances
    try:
        # LightGBM Booster
        if hasattr(model, "feature_importance"):
            raw = model.feature_importance(importance_type="gain")
        # sklearn-like API (XGBoost, etc.)
        elif hasattr(model, "feature_importances_"):
            raw = model.feature_importances_
        else:
            return []

        total = float(np.sum(raw)) or 1.0
        ranked = sorted(
            zip(FEATURE_NAMES, raw),
            key=lambda t: t[1],
            reverse=True,
        )
        return [(name, round(float(v / total), 4)) for name, v in ranked[:3]]
    except Exception as exc:
        logger.debug("Feature importance fallback failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def format_ml_prediction(result: PredictionResult) -> str:
    """Format a prediction result for Telegram display.

    Returns:
        Multi-line string with probability bar, label, and top SHAP features.
    """
    pct = result.probability * 100

    # Visual probability bar (10 segments)
    filled = round(result.probability * 10)
    bar = ">" * filled + "-" * (10 - filled)

    label_map = {
        "STRONG_BUY": "ML: 강한 매수 신호",
        "BUY": "ML: 매수 우호",
        "NEUTRAL": "ML: 중립",
        "AVOID": "ML: 비추천",
    }
    label_text = label_map.get(result.label, f"ML: {result.label}")

    lines = [
        f"[{bar}] {pct:.1f}%  {label_text}",
    ]

    bonus = get_score_bonus(result.probability)
    if bonus != 0:
        sign = "+" if bonus > 0 else ""
        lines.append(f"  ML 점수 보정: {sign}{bonus}점")

    if result.shap_top3:
        reasons = ", ".join(
            f"{name}({imp:.2f})" for name, imp in result.shap_top3
        )
        lines.append(f"  주요 근거: {reasons}")

    return "\n".join(lines)
