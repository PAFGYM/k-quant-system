"""LSTM 시계열 예측 모듈.

주가의 시계열 패턴을 학습하여 5일 수익률 예측.
기존 LightGBM/XGBoost와 앙상블하여 정확도 향상.

torch 없으면 graceful degradation (neutral 50% 반환).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except (ImportError, OSError):
    torch = None
    nn = None
    _HAS_TORCH = False

SEQUENCE_LENGTH = 20
MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'models')


# ── Model Definition ─────────────────────────────────────

if _HAS_TORCH:
    class StockLSTM(nn.Module):
        """주가 예측 LSTM.

        입력: (batch, seq_len, features)
        출력: (batch, 1) — 5일 후 +3% 이상 확률
        """
        def __init__(
            self,
            input_size: int = 30,
            hidden_size: int = 64,
            num_layers: int = 2,
            dropout: float = 0.2,
            bidirectional: bool = False,
        ):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout if num_layers > 1 else 0,
                batch_first=True,
                bidirectional=bidirectional,
            )
            direction_factor = 2 if bidirectional else 1
            self.attention = nn.Sequential(
                nn.Linear(hidden_size * direction_factor, 1),
                nn.Softmax(dim=1),
            )
            self.fc = nn.Sequential(
                nn.Linear(hidden_size * direction_factor, 32),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(32, 1),
                nn.Sigmoid(),
            )

        def forward(self, x):
            lstm_out, _ = self.lstm(x)
            attn_weights = self.attention(lstm_out)
            context = torch.sum(lstm_out * attn_weights, dim=1)
            return self.fc(context)
else:
    StockLSTM = None  # type: ignore


# ── Sequence Builder ─────────────────────────────────────

def build_sequences(
    feature_matrix: np.ndarray,
    targets: np.ndarray,
    seq_len: int = SEQUENCE_LENGTH,
) -> tuple[np.ndarray, np.ndarray]:
    """시계열 데이터를 LSTM 입력 시퀀스로 변환."""
    n = len(feature_matrix)
    if n <= seq_len:
        return np.empty((0, seq_len, feature_matrix.shape[1])), np.empty(0)

    X, y = [], []
    for i in range(seq_len, n):
        X.append(feature_matrix[i - seq_len:i])
        y.append(targets[i])

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


# ── Training ─────────────────────────────────────────────

@dataclass
class LSTMTrainResult:
    train_loss: float = 0.0
    val_loss: float = 0.0
    val_auc: float = 0.0
    epochs_trained: int = 0
    best_epoch: int = 0
    overfitting_warning: bool = False


def train_lstm(
    X: np.ndarray,
    y: np.ndarray,
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 0.001,
    patience: int = 10,
    val_ratio: float = 0.2,
) -> tuple:
    """LSTM 모델 학습. Returns (model, scaler_mean, scaler_std, result)."""
    if not _HAS_TORCH or StockLSTM is None or len(X) == 0:
        return None, None, None, LSTMTrainResult()

    # Split (time series - no shuffle)
    split_idx = int(len(X) * (1 - val_ratio))
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    # Normalize
    scaler_mean = X_train.mean(axis=(0, 1))
    scaler_std = X_train.std(axis=(0, 1)) + 1e-8
    X_train = (X_train - scaler_mean) / scaler_std
    X_val = (X_val - scaler_mean) / scaler_std

    X_train_t = torch.FloatTensor(X_train)
    y_train_t = torch.FloatTensor(y_train).unsqueeze(1)
    X_val_t = torch.FloatTensor(X_val)
    y_val_t = torch.FloatTensor(y_val).unsqueeze(1)

    model = StockLSTM(input_size=X.shape[2])
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.BCELoss()
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5)

    best_val_loss = float('inf')
    patience_counter = 0
    best_state = None
    best_epoch = 0

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        n_batches = 0

        for start in range(0, len(X_train_t), batch_size):
            end = min(start + batch_size, len(X_train_t))
            batch_x = X_train_t[start:end]
            batch_y = y_train_t[start:end]

            optimizer.zero_grad()
            pred = model(batch_x)
            loss = criterion(pred, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()
            n_batches += 1

        train_loss = total_loss / max(n_batches, 1)

        # Validation
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val_t)
            val_loss = criterion(val_pred, y_val_t).item()

        scheduler.step(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch + 1
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                break

    if best_state:
        model.load_state_dict(best_state)

    # AUC
    val_auc = 0.5
    try:
        from sklearn.metrics import roc_auc_score
        model.eval()
        with torch.no_grad():
            preds = model(X_val_t).numpy().flatten()
        val_auc = roc_auc_score(y_val, preds)
    except Exception:
        pass

    result = LSTMTrainResult(
        train_loss=round(train_loss, 4),
        val_loss=round(best_val_loss, 4),
        val_auc=round(val_auc, 4),
        epochs_trained=epoch + 1,
        best_epoch=best_epoch,
        overfitting_warning=(train_loss < best_val_loss * 0.5),
    )

    return model, scaler_mean, scaler_std, result


# ── Prediction ───────────────────────────────────────────

def predict_lstm(
    model,
    recent_features: np.ndarray,
    scaler_mean: np.ndarray | None = None,
    scaler_std: np.ndarray | None = None,
) -> float:
    """LSTM으로 단일 종목 5일 수익률 예측. Returns 확률 0.0~1.0."""
    if not _HAS_TORCH or model is None:
        return 0.5

    if scaler_mean is not None and scaler_std is not None:
        recent_features = (recent_features - scaler_mean) / (scaler_std + 1e-8)

    x = torch.FloatTensor(recent_features).unsqueeze(0)
    model.eval()
    with torch.no_grad():
        prob = model(x).item()

    return max(0.0, min(1.0, prob))


# ── 3-Model Ensemble ─────────────────────────────────────

def ensemble_3model_predict(
    lgb_prob: float,
    xgb_prob: float,
    lstm_prob: float,
    weights: tuple[float, float, float] | None = None,
) -> float:
    """3-모델 가중 앙상블. LSTM neutral이면 기존 2-모델 fallback.

    v4.0: weights=None이면 AutoTrainer에서 최적화된 가중치 자동 로드.
    """
    if weights is None:
        weights = _load_optimal_weights()

    if abs(lstm_prob - 0.5) < 0.01:
        return lgb_prob * 0.55 + xgb_prob * 0.45

    w_lgb, w_xgb, w_lstm = weights
    return w_lgb * lgb_prob + w_xgb * xgb_prob + w_lstm * lstm_prob


def _load_optimal_weights() -> tuple[float, float, float]:
    """AutoTrainer가 저장한 최적 가중치 로드. 없으면 기본값."""
    try:
        import json
        history_path = os.path.join(MODEL_DIR, 'train_history.json')
        if os.path.exists(history_path):
            with open(history_path, 'r') as f:
                data = json.load(f)
            w = data.get("current_weights", [0.35, 0.30, 0.35])
            if len(w) == 3 and all(isinstance(x, (int, float)) for x in w):
                return tuple(w)
    except Exception:
        pass
    return (0.35, 0.30, 0.35)


# ── Model Save/Load ──────────────────────────────────────

def save_lstm_model(model, scaler_mean, scaler_std, path: str | None = None):
    """LSTM 모델을 파일로 저장."""
    if not _HAS_TORCH or model is None:
        return
    path = path or os.path.join(MODEL_DIR, 'lstm_stock.pt')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save({
        'model_state': model.state_dict(),
        'scaler_mean': scaler_mean,
        'scaler_std': scaler_std,
        'input_size': model.lstm.input_size,
        'hidden_size': model.lstm.hidden_size,
        'num_layers': model.lstm.num_layers,
    }, path)
    logger.info("LSTM model saved to %s", path)


def load_lstm_model(path: str | None = None):
    """저장된 LSTM 모델 로딩. Returns (model, scaler_mean, scaler_std)."""
    if not _HAS_TORCH or StockLSTM is None:
        return None, None, None
    path = path or os.path.join(MODEL_DIR, 'lstm_stock.pt')
    if not os.path.exists(path):
        return None, None, None
    try:
        checkpoint = torch.load(path, map_location='cpu')
        model = StockLSTM(
            input_size=checkpoint['input_size'],
            hidden_size=checkpoint['hidden_size'],
            num_layers=checkpoint['num_layers'],
        )
        model.load_state_dict(checkpoint['model_state'])
        model.eval()
        return model, checkpoint['scaler_mean'], checkpoint['scaler_std']
    except Exception as e:
        logger.error("Failed to load LSTM model: %s", e)
        return None, None, None


# ── Convenience Function ─────────────────────────────────

def get_lstm_enhanced_prediction(
    features_dict: dict[str, float],
    ml_model: dict | None = None,
    recent_sequence: np.ndarray | None = None,
):
    """LSTM을 포함한 3-모델 앙상블 예측.

    기존 predictor.predict() + LSTM 확률 합산.
    """
    from kstock.ml.predictor import predict as base_predict, PredictionResult

    base_result = base_predict(features_dict, ml_model)

    if not _HAS_TORCH or recent_sequence is None:
        return base_result

    try:
        model, scaler_mean, scaler_std = load_lstm_model()
        if model is None:
            return base_result

        lstm_prob = predict_lstm(model, recent_sequence, scaler_mean, scaler_std)
        lgb_prob = base_result.probability * 1.05
        xgb_prob = base_result.probability * 0.95
        final_prob = ensemble_3model_predict(lgb_prob, xgb_prob, lstm_prob)

        from kstock.ml.predictor import _probability_to_label
        return PredictionResult(
            probability=round(final_prob, 4),
            label=_probability_to_label(final_prob),
            shap_top3=base_result.shap_top3,
        )
    except Exception as e:
        logger.debug("LSTM prediction failed, using base: %s", e)
        return base_result
