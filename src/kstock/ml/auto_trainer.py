"""ML 자동 재학습 + 성능 모니터링 + 앙상블 가중치 최적화.

FreqAI 스타일의 자동 재학습 시스템:
- 모델 드리프트 감지 (예측 정확도 하락 → 자동 재학습 트리거)
- 앙상블 가중치 동적 최적화 (LGB/XGB/LSTM 비율 자동 조정)
- 학습 히스토리 추적 + 텔레그램 리포트

비용: CPU only, 외부 API 없음.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.path.dirname(__file__)).parent.parent.parent / "models"
HISTORY_FILE = MODEL_DIR / "train_history.json"


# ── Data Classes ──────────────────────────────────────────

@dataclass
class ModelPerformance:
    """단일 모델의 성능 지표."""
    model_name: str
    date: str
    accuracy: float = 0.0       # 예측 정확도 (0~1)
    precision: float = 0.0      # 양성 정밀도
    recall: float = 0.0         # 재현율
    auc: float = 0.5            # AUC-ROC
    sharpe_ratio: float = 0.0   # 예측 기반 매매의 Sharpe ratio
    predictions_count: int = 0
    correct_count: int = 0


@dataclass
class TrainHistory:
    """학습 히스토리 단일 레코드."""
    train_date: str
    trigger: str       # "scheduled", "drift", "manual"
    samples: int
    train_auc: float
    val_auc: float
    lgb_weight: float
    xgb_weight: float
    lstm_weight: float
    duration_sec: float
    overfitting: bool = False


@dataclass
class DriftReport:
    """모델 드리프트 감지 결과."""
    is_drifting: bool
    current_accuracy: float
    baseline_accuracy: float
    accuracy_drop_pct: float    # 정확도 하락률 (%)
    days_since_train: int
    retrain_recommended: bool
    reason: str


@dataclass
class AutoTrainResult:
    """자동 재학습 실행 결과."""
    success: bool
    trigger: str
    samples: int = 0
    train_auc: float = 0.0
    val_auc: float = 0.0
    optimal_weights: tuple[float, float, float] = (0.35, 0.30, 0.35)
    duration_sec: float = 0.0
    message: str = ""


# ── 성능 모니터링 ────────────────────────────────────────

class ModelMonitor:
    """ML 모델 성능 모니터링 + 드리프트 감지."""

    # 드리프트 감지 임계값
    ACCURACY_DROP_THRESHOLD = 0.10   # 10% 이상 정확도 하락
    MIN_EVAL_SAMPLES = 20            # 최소 평가 샘플 수
    MAX_DAYS_WITHOUT_RETRAIN = 14    # 최대 재학습 없이 운영 일수

    def __init__(self, db=None) -> None:
        self.db = db
        self._prediction_log: list[dict] = []  # 최근 예측 기록
        self._baseline_accuracy: float = 0.6   # 기준 정확도

    def log_prediction(
        self,
        ticker: str,
        predicted_prob: float,
        actual_return_5d: float | None = None,
    ) -> None:
        """예측 결과 기록. 5일 후 실제 수익률로 평가."""
        entry = {
            "ticker": ticker,
            "date": date.today().isoformat(),
            "predicted_prob": predicted_prob,
            "predicted_label": "BUY" if predicted_prob >= 0.6 else "AVOID",
            "actual_return_5d": actual_return_5d,
            "correct": None,
        }

        if actual_return_5d is not None:
            actual_label = 1 if actual_return_5d > 0.03 else 0
            pred_label = 1 if predicted_prob >= 0.5 else 0
            entry["correct"] = actual_label == pred_label

        self._prediction_log.append(entry)
        # 최대 500개 유지
        if len(self._prediction_log) > 500:
            self._prediction_log = self._prediction_log[-500:]

    def evaluate_recent(self, days: int = 14) -> ModelPerformance:
        """최근 N일간 예측 성능 평가."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        recent = [
            p for p in self._prediction_log
            if p["date"] >= cutoff and p["correct"] is not None
        ]

        perf = ModelPerformance(
            model_name="ensemble",
            date=date.today().isoformat(),
            predictions_count=len(recent),
        )

        if len(recent) < self.MIN_EVAL_SAMPLES:
            perf.accuracy = self._baseline_accuracy
            return perf

        correct = sum(1 for p in recent if p["correct"])
        perf.correct_count = correct
        perf.accuracy = correct / len(recent)

        # Precision: BUY 예측 중 실제 +3% 달성
        buy_preds = [p for p in recent if p["predicted_prob"] >= 0.5]
        if buy_preds:
            buy_correct = sum(1 for p in buy_preds if p.get("actual_return_5d", 0) > 0.03)
            perf.precision = buy_correct / len(buy_preds)

        # Recall: 실제 +3% 중 BUY로 예측
        actual_buys = [p for p in recent if p.get("actual_return_5d", 0) > 0.03]
        if actual_buys:
            recalled = sum(1 for p in actual_buys if p["predicted_prob"] >= 0.5)
            perf.recall = recalled / len(actual_buys)

        return perf

    def detect_drift(self, last_train_date: date | None = None) -> DriftReport:
        """모델 드리프트 감지."""
        perf = self.evaluate_recent()
        days_since = (
            (date.today() - last_train_date).days
            if last_train_date else 999
        )

        accuracy_drop = self._baseline_accuracy - perf.accuracy
        drop_pct = (accuracy_drop / max(self._baseline_accuracy, 0.01)) * 100

        # 재학습 추천 조건
        retrain_reasons = []
        if accuracy_drop > self.ACCURACY_DROP_THRESHOLD:
            retrain_reasons.append(f"정확도 {drop_pct:.1f}% 하락")
        if days_since > self.MAX_DAYS_WITHOUT_RETRAIN:
            retrain_reasons.append(f"최근 학습 {days_since}일 전")
        if perf.predictions_count < self.MIN_EVAL_SAMPLES:
            # 데이터 부족 시에는 시간 기반 재학습만
            if days_since > self.MAX_DAYS_WITHOUT_RETRAIN:
                retrain_reasons.append("평가 데이터 부족 + 장기 미학습")

        is_drifting = len(retrain_reasons) > 0
        reason = " / ".join(retrain_reasons) if retrain_reasons else "정상 범위"

        return DriftReport(
            is_drifting=is_drifting,
            current_accuracy=round(perf.accuracy, 4),
            baseline_accuracy=round(self._baseline_accuracy, 4),
            accuracy_drop_pct=round(drop_pct, 1),
            days_since_train=days_since,
            retrain_recommended=is_drifting,
            reason=reason,
        )

    def update_baseline(self, new_accuracy: float) -> None:
        """학습 후 기준 정확도 갱신."""
        if new_accuracy > 0.5:
            self._baseline_accuracy = new_accuracy


# ── 앙상블 가중치 최적화 ─────────────────────────────────

def optimize_ensemble_weights(
    lgb_probs: list[float],
    xgb_probs: list[float],
    lstm_probs: list[float],
    actuals: list[int],
    grid_steps: int = 20,
) -> tuple[float, float, float]:
    """Grid search로 최적 앙상블 가중치 탐색.

    Args:
        lgb_probs: LightGBM 예측 확률 리스트
        xgb_probs: XGBoost 예측 확률 리스트
        lstm_probs: LSTM 예측 확률 리스트
        actuals: 실제 라벨 (0/1) 리스트
        grid_steps: 그리드 탐색 단위 수

    Returns:
        (w_lgb, w_xgb, w_lstm) 최적 가중치 튜플
    """
    if not lgb_probs or len(lgb_probs) != len(actuals):
        return (0.35, 0.30, 0.35)

    lgb_arr = np.array(lgb_probs)
    xgb_arr = np.array(xgb_probs) if xgb_probs else lgb_arr * 0.95
    lstm_arr = np.array(lstm_probs) if lstm_probs else np.full(len(lgb_arr), 0.5)
    actual_arr = np.array(actuals)

    best_score = -1.0
    best_weights = (0.35, 0.30, 0.35)

    step = 1.0 / grid_steps
    for w1_steps in range(1, grid_steps):
        w1 = w1_steps * step
        for w2_steps in range(1, grid_steps - w1_steps):
            w2 = w2_steps * step
            w3 = 1.0 - w1 - w2
            if w3 < step:
                continue

            combined = w1 * lgb_arr + w2 * xgb_arr + w3 * lstm_arr
            preds = (combined >= 0.5).astype(int)

            accuracy = np.mean(preds == actual_arr)

            # 보정: 예측 기반 시뮬레이션 수익률 가중
            buy_mask = combined >= 0.6
            if buy_mask.any():
                # BUY 신호 정밀도 보너스
                buy_correct = np.mean(actual_arr[buy_mask] == 1) if buy_mask.sum() > 0 else 0
                score = accuracy * 0.6 + buy_correct * 0.4
            else:
                score = accuracy * 0.7

            if score > best_score:
                best_score = score
                best_weights = (round(w1, 2), round(w2, 2), round(w3, 2))

    logger.info(
        "Optimal ensemble weights: LGB=%.2f, XGB=%.2f, LSTM=%.2f (score=%.4f)",
        *best_weights, best_score,
    )
    return best_weights


# ── 자동 재학습 엔진 ─────────────────────────────────────

class AutoTrainer:
    """FreqAI 스타일 자동 재학습 엔진.

    주요 기능:
    1. 주간 정기 재학습 (일요일 03:00)
    2. 드리프트 감지 → 비정기 재학습
    3. 앙상블 가중치 동적 최적화
    4. 학습 히스토리 추적
    """

    def __init__(self, db=None, yf_client=None) -> None:
        self.db = db
        self.yf_client = yf_client
        self.monitor = ModelMonitor(db)
        self._last_train_date: date | None = None
        self._current_weights = (0.35, 0.30, 0.35)
        self._history: list[TrainHistory] = []
        self._load_history()

    def _load_history(self) -> None:
        """디스크에서 학습 히스토리 로드."""
        try:
            if HISTORY_FILE.exists():
                data = json.loads(HISTORY_FILE.read_text("utf-8"))
                self._history = [TrainHistory(**h) for h in data.get("history", [])]
                w = data.get("current_weights", [0.35, 0.30, 0.35])
                self._current_weights = tuple(w)
                last = data.get("last_train_date")
                if last:
                    self._last_train_date = date.fromisoformat(last)
                logger.debug(
                    "Loaded %d train history records, weights=%s",
                    len(self._history), self._current_weights,
                )
        except Exception as e:
            logger.debug("Train history load failed: %s", e)

    def _save_history(self) -> None:
        """학습 히스토리 디스크 저장."""
        try:
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "history": [
                    {
                        "train_date": h.train_date,
                        "trigger": h.trigger,
                        "samples": h.samples,
                        "train_auc": h.train_auc,
                        "val_auc": h.val_auc,
                        "lgb_weight": h.lgb_weight,
                        "xgb_weight": h.xgb_weight,
                        "lstm_weight": h.lstm_weight,
                        "duration_sec": h.duration_sec,
                        "overfitting": h.overfitting,
                    }
                    for h in self._history[-50:]  # 최근 50회만 유지
                ],
                "current_weights": list(self._current_weights),
                "last_train_date": self._last_train_date.isoformat() if self._last_train_date else None,
            }
            HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        except Exception as e:
            logger.debug("Train history save failed: %s", e)

    @property
    def current_weights(self) -> tuple[float, float, float]:
        """현재 앙상블 가중치."""
        return self._current_weights

    def should_retrain(self) -> DriftReport:
        """재학습 필요 여부 확인 (드리프트 감지)."""
        return self.monitor.detect_drift(self._last_train_date)

    async def run_auto_train(
        self,
        trigger: str = "scheduled",
        training_data: list[dict] | None = None,
    ) -> AutoTrainResult:
        """자동 재학습 실행.

        Args:
            trigger: "scheduled" (정기), "drift" (드리프트), "manual" (수동)
            training_data: 학습 데이터. None이면 DB에서 수집.

        Returns:
            AutoTrainResult
        """
        start_time = time.time()

        try:
            from kstock.ml.predictor import build_training_data, train_model
            from kstock.ml.lstm_predictor import (
                build_sequences, train_lstm, save_lstm_model,
            )

            # 1. 학습 데이터 수집
            if training_data is None:
                training_data = await self._collect_training_data()

            if not training_data or len(training_data) < 50:
                return AutoTrainResult(
                    success=False,
                    trigger=trigger,
                    samples=len(training_data) if training_data else 0,
                    message=f"학습 데이터 부족: {len(training_data) if training_data else 0}개 (<50)",
                )

            # 2. Feature matrix 구축
            X, y = build_training_data(training_data)
            if len(X) == 0:
                return AutoTrainResult(
                    success=False, trigger=trigger,
                    message="Feature matrix 생성 실패",
                )

            # 3. LightGBM + XGBoost 학습
            result = train_model(X, y, n_trials=20)
            lgb_model = result["model"]["lgb"]
            xgb_model = result["model"]["xgb"]
            metrics = result.get("metrics", {})

            # 4. LSTM 학습 (시퀀스 데이터 있는 경우)
            lstm_result_auc = 0.5
            try:
                X_seq, y_seq = build_sequences(X, y)
                if len(X_seq) > 30:
                    lstm_model, scaler_mean, scaler_std, lstm_train_result = train_lstm(
                        X_seq, y_seq, epochs=30, patience=7
                    )
                    if lstm_model is not None:
                        save_lstm_model(lstm_model, scaler_mean, scaler_std)
                        lstm_result_auc = lstm_train_result.val_auc
            except Exception as e:
                logger.debug("LSTM training skipped: %s", e)

            # 5. 앙상블 가중치 최적화
            optimal_weights = self._current_weights
            try:
                if lgb_model is not None:
                    lgb_probs = lgb_model.predict(X).tolist()
                    xgb_probs = (
                        xgb_model.predict_proba(X)[:, 1].tolist()
                        if xgb_model is not None else []
                    )
                    lstm_probs = []  # LSTM은 시퀀스 기반이므로 별도 처리 필요
                    optimal_weights = optimize_ensemble_weights(
                        lgb_probs, xgb_probs, lstm_probs, y.tolist()
                    )
                    self._current_weights = optimal_weights
            except Exception as e:
                logger.debug("Weight optimization skipped: %s", e)

            # 6. 성능 기록
            train_auc = metrics.get("train_auc", 0)
            val_auc = metrics.get("test_auc", 0)
            overfitting = metrics.get("overfitting_warning", False)
            duration = time.time() - start_time

            self._last_train_date = date.today()
            self.monitor.update_baseline(val_auc if val_auc > 0.5 else 0.6)

            history = TrainHistory(
                train_date=date.today().isoformat(),
                trigger=trigger,
                samples=len(X),
                train_auc=round(train_auc, 4),
                val_auc=round(val_auc, 4),
                lgb_weight=optimal_weights[0],
                xgb_weight=optimal_weights[1],
                lstm_weight=optimal_weights[2],
                duration_sec=round(duration, 1),
                overfitting=overfitting,
            )
            self._history.append(history)
            self._save_history()

            # DB에 성능 기록
            if self.db:
                try:
                    self.db.add_ml_performance(
                        model_name="ensemble_v4",
                        train_date=date.today().isoformat(),
                        train_auc=train_auc,
                        val_auc=val_auc,
                        samples=len(X),
                        weights=json.dumps(list(optimal_weights)),
                    )
                except Exception as e:
                    logger.debug("run_auto_train DB ml_performance save failed: %s", e)

            message = (
                f"✅ ML 재학습 완료 ({trigger})\n"
                f"  샘플: {len(X):,}개\n"
                f"  Train AUC: {train_auc:.4f}\n"
                f"  Val AUC: {val_auc:.4f}\n"
                f"  LSTM AUC: {lstm_result_auc:.4f}\n"
                f"  가중치: LGB={optimal_weights[0]:.2f} / "
                f"XGB={optimal_weights[1]:.2f} / LSTM={optimal_weights[2]:.2f}\n"
                f"  소요: {duration:.1f}초"
            )
            if overfitting:
                message += "\n  ⚠️ 과적합 감지 — 규제 강화 필요"

            logger.info(message)

            return AutoTrainResult(
                success=True,
                trigger=trigger,
                samples=len(X),
                train_auc=round(train_auc, 4),
                val_auc=round(val_auc, 4),
                optimal_weights=optimal_weights,
                duration_sec=round(duration, 1),
                message=message,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error("Auto-train failed: %s", e)
            return AutoTrainResult(
                success=False,
                trigger=trigger,
                duration_sec=round(duration, 1),
                message=f"❌ ML 재학습 실패: {e}",
            )

    async def _collect_training_data(self) -> list[dict]:
        """DB + feature_store에서 학습 데이터 수집.

        v10.1: feature_store 우선 → recommendation_results 보조 → 합성 폴백.
        """
        if not self.db:
            return []

        # 1. Feature Store 기반 (best quality — 전 종목 일별 데이터)
        fs_data = self._collect_from_feature_store()

        # 2. Recommendation Results (legacy)
        rec_data = []
        try:
            recs = self.db.get_recommendation_results(limit=500)
            for r in recs:
                features = r.get("features", {})
                if isinstance(features, str):
                    try:
                        features = json.loads(features)
                    except json.JSONDecodeError:
                        continue

                if not features or len(features) < 10:
                    continue

                actual_return = r.get("actual_return_pct", 0) or 0
                target = 1 if actual_return > 3.0 else 0
                features["target"] = target
                rec_data.append(features)

        except Exception as e:
            logger.debug("Recommendation data collection: %s", e)

        # 3. Merge (feature_store 우선, dedup by feature hash)
        seen = set()
        training_data = []
        for item in fs_data + rec_data:
            key = (
                item.get("rsi", 0),
                item.get("vix", 0),
                item.get("per", 0),
                item.get("target", -1),
            )
            if key not in seen:
                seen.add(key)
                training_data.append(item)

        logger.info(
            "Training data collected: %d total (fs=%d, rec=%d)",
            len(training_data), len(fs_data), len(rec_data),
        )

        # 4. 합성 데이터 보충 (최소 50개)
        if len(training_data) < 50:
            logger.debug(
                "Training data insufficient (%d), generating synthetic",
                len(training_data),
            )
            training_data.extend(_generate_synthetic_training_data(
                50 - len(training_data)
            ))

        return training_data

    def _collect_from_feature_store(self) -> list[dict]:
        """feature_store에서 D+5 실제 수익률이 확정된 데이터를 학습용으로 수집.

        v10.1: 매일 스캔 시 축적된 46개 피처 + OHLCV 기반 실제 수익률 매칭.
        """
        try:
            from kstock.ml.feature_store import FeatureStore
        except ImportError:
            return []

        training_data = []
        try:
            fs = FeatureStore()

            # D+8일(영업일 5일+여유) 이전 데이터만 사용 (수익률 확정)
            cutoff = (date.today() - timedelta(days=8)).strftime("%Y-%m-%d")
            dates = fs.get_available_dates(before=cutoff, limit=60)

            if not dates:
                return []

            for date_str in dates:
                tickers = fs.get_tickers_for_date(date_str)
                for ticker in tickers:
                    features = fs.get_features_dict(ticker, date_str)
                    if not features or len(features) < 30:
                        continue

                    # D+5 실제 수익률 계산
                    actual_return = self._calc_actual_return(ticker, date_str, days=5)
                    if actual_return is None:
                        continue

                    features["target"] = 1 if actual_return > 3.0 else 0
                    training_data.append(features)

            logger.info("Feature store training data: %d samples from %d dates", len(training_data), len(dates))
        except Exception as e:
            logger.debug("Feature store data collection failed: %s", e)

        return training_data

    def _calc_actual_return(self, ticker: str, base_date: str, days: int = 5) -> float | None:
        """base_date 기준 D+days 실제 수익률(%) 계산.

        OHLCV 데이터에서 base_date의 종가와 D+days 종가 비교.
        """
        try:
            if not self.db:
                return None

            # DB에서 OHLCV 조회 (supply_demand에 price 정보가 없으므로 yfinance 캐시 활용)
            # 간단한 접근: recommendation_results에서 해당 종목의 actual return 검색
            recs = self.db.get_recommendation_results(limit=500)
            for r in recs:
                if r.get("ticker") == ticker:
                    created = r.get("created_at", "")
                    if created and created[:10] == base_date:
                        ret = r.get("day5_return")
                        if ret is not None:
                            return float(ret)

            # 대안: pykrx로 직접 조회
            try:
                from pykrx import stock as pykrx_stock
                base_dt = datetime.strptime(base_date, "%Y-%m-%d")
                end_dt = base_dt + timedelta(days=days + 5)  # 여유

                df = pykrx_stock.get_market_ohlcv(
                    base_dt.strftime("%Y%m%d"),
                    end_dt.strftime("%Y%m%d"),
                    ticker,
                )
                if df is not None and len(df) > days:
                    base_price = float(df.iloc[0]["종가"])
                    target_price = float(df.iloc[days]["종가"])
                    if base_price > 0:
                        return (target_price - base_price) / base_price * 100
            except Exception:
                pass

            return None
        except Exception:
            return None

    def format_train_report(self) -> str:
        """최근 학습 히스토리 텔레그램 포맷."""
        if not self._history:
            return "📊 ML 학습 이력이 없습니다."

        recent = self._history[-5:]  # 최근 5회
        lines = [
            "🤖 ML 모델 상태",
            "━" * 22,
            "",
        ]

        if self._last_train_date:
            days_ago = (date.today() - self._last_train_date).days
            lines.append(f"📅 최근 학습: {self._last_train_date} ({days_ago}일 전)")
        lines.append(
            f"⚖️ 가중치: LGB={self._current_weights[0]:.2f} / "
            f"XGB={self._current_weights[1]:.2f} / LSTM={self._current_weights[2]:.2f}"
        )
        lines.append("")

        for h in reversed(recent):
            icon = "⚠️" if h.overfitting else "✅"
            lines.append(
                f"{icon} {h.train_date} ({h.trigger}): "
                f"AUC {h.val_auc:.3f} | {h.samples}개 | {h.duration_sec:.0f}s"
            )

        # 드리프트 상태
        drift = self.should_retrain()
        if drift.retrain_recommended:
            lines.extend(["", f"⚠️ 재학습 권장: {drift.reason}"])

        return "\n".join(lines)


# ── 합성 학습 데이터 ─────────────────────────────────────

def _generate_synthetic_training_data(n: int) -> list[dict]:
    """학습 데이터 부족 시 합성 데이터 생성.

    실제 데이터를 대체할 수는 없지만, 모델 초기 학습에 사용.
    """
    from kstock.ml.predictor import FEATURE_NAMES

    rng = np.random.default_rng(42)
    data = []

    for _ in range(n):
        features = {}
        for fname in FEATURE_NAMES:
            if fname == "rsi":
                features[fname] = rng.uniform(20, 80)
            elif fname == "vix":
                features[fname] = rng.uniform(12, 35)
            elif fname == "per":
                features[fname] = rng.uniform(5, 50)
            elif fname in ("golden_cross", "dead_cross", "bb_squeeze", "mtf_aligned"):
                features[fname] = float(rng.random() > 0.7)
            elif fname in ("volume_ratio",):
                features[fname] = rng.uniform(0.5, 3.0)
            elif fname == "usdkrw":
                features[fname] = rng.uniform(1200, 1500)
            else:
                features[fname] = rng.standard_normal()

        # 타겟: RSI 낮고 volume_ratio 높으면 상승 확률 높음 (기본 규칙)
        buy_signal = (
            features.get("rsi", 50) < 40
            and features.get("volume_ratio", 1) > 1.5
            and features.get("vix", 20) < 25
        )
        features["target"] = 1 if buy_signal else (1 if rng.random() > 0.65 else 0)
        data.append(features)

    return data
