# 프롬프트 7: 딥러닝 시계열 예측 (LSTM + 기존 ML 앙상블)

## 현재 문제

`src/kstock/ml/predictor.py`:
- ✅ LightGBM + XGBoost 앙상블 — 잘 되어 있음
- ✅ 30개 피처, SHAP 설명, Optuna 하이퍼파라미터 튜닝
- ❌ 트리 모델만 → 시계열 패턴(추세 지속, 반전) 포착 못함
- ❌ 시퀀스 학습 불가 → "최근 10일 패턴"을 하나의 입력으로 못 봄
- ❌ feature_store.py가 stub → 피처 캐싱 없음

## 목표

기존 `predictor.py`는 건드리지 말고, **새 파일 `src/kstock/ml/lstm_predictor.py`** 생성.
기존 앙상블에 LSTM 예측을 추가하여 3-모델 앙상블.

---

## 기존 인프라 (건드리지 말 것)

- `predictor.py` — LightGBM + XGBoost 그대로 유지
- `FEATURE_NAMES` (30개) — 그대로 사용
- `PredictionResult` 데이터클래스 — 그대로 유지
- `build_features()` — 그대로 사용
- `sentiment.py` — 감성 분석 그대로

---

## 의존성 확인

**먼저 확인:** PyTorch가 설치되어 있는지
```bash
python3 -c "import torch; print(torch.__version__)"
```

설치 안 되어 있으면:
```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

**CPU 전용으로 충분.** Mac Mini M1이면 MPS도 가능하지만 CPU가 안전.

**없으면 graceful degradation:** torch 없을 때 neutral prediction 반환 (기존 predictor.py 패턴과 동일).

---

## 작업 1: LSTM 모델 정의

`src/kstock/ml/lstm_predictor.py`:

```python
"""LSTM 시계열 예측 모듈.

주가의 시계열 패턴을 학습하여 5일 수익률 예측.
기존 LightGBM/XGBoost와 앙상블하여 정확도 향상.

torch 없으면 graceful degradation (neutral 50% 반환).
"""
from __future__ import annotations

import logging
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
```

**모델 구조:**

```python
class StockLSTM(nn.Module):
    """주가 예측 LSTM.

    입력: (batch, seq_len, features) — 최근 N일간 피처 시퀀스
    출력: (batch, 1) — 5일 후 +3% 이상 확률
    """
    def __init__(
        self,
        input_size: int = 30,     # 피처 수 (FEATURE_NAMES)
        hidden_size: int = 64,    # LSTM 히든 유닛
        num_layers: int = 2,      # LSTM 레이어 수
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
        # x: (batch, seq_len, features)
        lstm_out, _ = self.lstm(x)  # (batch, seq_len, hidden)
        # Attention mechanism
        attn_weights = self.attention(lstm_out)  # (batch, seq_len, 1)
        context = torch.sum(lstm_out * attn_weights, dim=1)  # (batch, hidden)
        return self.fc(context)  # (batch, 1)
```

## 작업 2: 시퀀스 데이터 생성

```python
SEQUENCE_LENGTH = 20  # 최근 20일 시퀀스

def build_sequences(
    feature_matrix: np.ndarray,  # (total_days, 30) — 일별 피처
    targets: np.ndarray,         # (total_days,) — 5일 후 수익률 > 3% 여부
    seq_len: int = SEQUENCE_LENGTH,
) -> tuple[np.ndarray, np.ndarray]:
    """시계열 데이터를 LSTM 입력 시퀀스로 변환.

    Args:
        feature_matrix: 일별 피처 행렬 (time_steps, features)
        targets: 이진 타겟 (1: +3% 이상, 0: 미만)
        seq_len: 입력 시퀀스 길이

    Returns:
        X: (n_samples, seq_len, features)
        y: (n_samples,)
    """
    n = len(feature_matrix)
    if n <= seq_len:
        return np.empty((0, seq_len, feature_matrix.shape[1])), np.empty(0)

    X, y = [], []
    for i in range(seq_len, n):
        X.append(feature_matrix[i - seq_len:i])
        y.append(targets[i])

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)
```

## 작업 3: 학습 파이프라인

```python
@dataclass
class LSTMTrainResult:
    """LSTM 학습 결과."""
    train_loss: float
    val_loss: float
    val_auc: float
    epochs_trained: int
    best_epoch: int
    overfitting_warning: bool


def train_lstm(
    X: np.ndarray,        # (n_samples, seq_len, features)
    y: np.ndarray,        # (n_samples,)
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 0.001,
    patience: int = 10,   # early stopping
    val_ratio: float = 0.2,
) -> tuple[StockLSTM | None, LSTMTrainResult]:
    """LSTM 모델 학습.

    시계열 특성상 shuffle 안 함 — 마지막 20%를 검증셋으로.
    """
```

**학습 로직:**
1. 데이터 분할: 마지막 val_ratio를 검증셋 (시계열이라 shuffle 불가)
2. 피처 정규화: BatchNorm 또는 수동 StandardScaler (학습셋 기준)
3. BCELoss (이진 분류)
4. Adam optimizer
5. Early stopping (patience=10)
6. Best model 저장 (state_dict)

```python
# 핵심 학습 루프
model = StockLSTM(input_size=X.shape[2])
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
criterion = nn.BCELoss()
scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5)

best_val_loss = float('inf')
patience_counter = 0
best_state = None

for epoch in range(epochs):
    model.train()
    # ... 미니배치 학습
    model.eval()
    # ... 검증 손실 계산
    if val_loss < best_val_loss:
        best_val_loss = val_loss
        best_state = model.state_dict().copy()
        patience_counter = 0
    else:
        patience_counter += 1
        if patience_counter >= patience:
            break

model.load_state_dict(best_state)
```

## 작업 4: LSTM 예측

```python
def predict_lstm(
    model: StockLSTM,
    recent_features: np.ndarray,  # (seq_len, 30) — 최근 20일 피처
    scaler_mean: np.ndarray | None = None,
    scaler_std: np.ndarray | None = None,
) -> float:
    """LSTM으로 단일 종목 5일 수익률 예측.

    Returns:
        확률 (0.0 ~ 1.0)
    """
    if not _HAS_TORCH or model is None:
        return 0.5  # neutral

    # 정규화
    if scaler_mean is not None and scaler_std is not None:
        recent_features = (recent_features - scaler_mean) / (scaler_std + 1e-8)

    x = torch.FloatTensor(recent_features).unsqueeze(0)  # (1, seq_len, 30)
    model.eval()
    with torch.no_grad():
        prob = model(x).item()

    return max(0.0, min(1.0, prob))
```

## 작업 5: 3-모델 앙상블 통합

기존 `predictor.py`의 `_ensemble_predict()`를 확장하지 말고, 별도 통합 함수:

```python
# lstm_predictor.py에 추가

def ensemble_3model_predict(
    lgb_prob: float,     # LightGBM 확률
    xgb_prob: float,     # XGBoost 확률
    lstm_prob: float,    # LSTM 확률
    weights: tuple[float, float, float] = (0.35, 0.30, 0.35),
) -> float:
    """3-모델 가중 앙상블.

    기본 가중치: LGB 35% + XGB 30% + LSTM 35%
    LSTM이 neutral(0.5)이면 기존 2-모델로 fallback.
    """
    if abs(lstm_prob - 0.5) < 0.01:
        # LSTM 미사용 → 기존 2-모델
        return lgb_prob * 0.55 + xgb_prob * 0.45

    w_lgb, w_xgb, w_lstm = weights
    return w_lgb * lgb_prob + w_xgb * xgb_prob + w_lstm * lstm_prob
```

## 작업 6: 기존 파이프라인에 연결

**predictor.py 수정 최소화** — `predict()` 함수의 마지막에 LSTM 확률 합산:

```python
# predictor.py의 predict() 함수 마지막 부분 수정:
# 기존 ensemble probability 계산 후, LSTM 결합

try:
    from kstock.ml.lstm_predictor import predict_lstm, ensemble_3model_predict, _HAS_TORCH
    if _HAS_TORCH and hasattr(predict_lstm, '_model_cache'):
        lstm_prob = predict_lstm(...)  # 캐시된 모델 사용
        prob = ensemble_3model_predict(lgb_prob, xgb_prob, lstm_prob)
except Exception:
    pass  # LSTM 없으면 기존 2-모델 그대로
```

**대안 (더 깔끔함):** predictor.py를 수정하지 말고, scoring.py나 commands.py에서 3-모델 앙상블 호출:
```python
# commands.py의 _analyze_stock()에서
from kstock.ml.lstm_predictor import get_lstm_enhanced_prediction
enhanced_result = get_lstm_enhanced_prediction(features_dict, ml_model)
```

## 작업 7: 모델 저장/로딩

```python
import os

MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'models')

def save_lstm_model(model: StockLSTM, scaler_mean, scaler_std, path: str = None):
    """LSTM 모델을 파일로 저장."""
    if not _HAS_TORCH:
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

def load_lstm_model(path: str = None) -> tuple[StockLSTM | None, np.ndarray | None, np.ndarray | None]:
    """저장된 LSTM 모델 로딩."""
    if not _HAS_TORCH:
        return None, None, None
    path = path or os.path.join(MODEL_DIR, 'lstm_stock.pt')
    if not os.path.exists(path):
        return None, None, None
    checkpoint = torch.load(path, map_location='cpu')
    model = StockLSTM(
        input_size=checkpoint['input_size'],
        hidden_size=checkpoint['hidden_size'],
        num_layers=checkpoint['num_layers'],
    )
    model.load_state_dict(checkpoint['model_state'])
    model.eval()
    return model, checkpoint['scaler_mean'], checkpoint['scaler_std']
```

## 작업 8: 자동 재학습 스케줄

```python
# scheduler.py에 추가 (주 1회)
# 일요일 03:00에 LSTM 재학습

async def job_lstm_retrain(self, context):
    """주 1회 LSTM 모델 재학습."""
    try:
        from kstock.ml.lstm_predictor import (
            train_lstm, save_lstm_model, build_sequences,
            SEQUENCE_LENGTH,
        )
        from kstock.ml.predictor import FEATURE_NAMES, build_features
        # 1. 유니버스 전체 6개월 피처 데이터 수집
        # 2. build_sequences()로 시퀀스 변환
        # 3. train_lstm() 실행
        # 4. save_lstm_model() 저장
        logger.info("LSTM 재학습 완료")
    except Exception as e:
        logger.error("LSTM 재학습 실패: %s", e)
```

**schedule_jobs()에 등록:**
```python
jq.run_daily(
    self.job_lstm_retrain,
    time=dt_time(hour=3, minute=0, tzinfo=KST),
    days=(6,),  # 일요일만
    name="lstm_retrain",
)
```

## 작업 9: 편의 함수

```python
def get_lstm_enhanced_prediction(
    features_dict: dict[str, float],
    ml_model: dict | None = None,
    recent_sequence: np.ndarray | None = None,  # (20, 30)
) -> PredictionResult:
    """LSTM을 포함한 3-모델 앙상블 예측.

    기존 predictor.predict() + LSTM 확률 합산.
    LSTM 모델이 없거나 torch가 없으면 기존 2-모델로 fallback.
    """
    from kstock.ml.predictor import predict as base_predict
    base_result = base_predict(features_dict, ml_model)

    if not _HAS_TORCH or recent_sequence is None:
        return base_result

    try:
        model, scaler_mean, scaler_std = load_lstm_model()
        if model is None:
            return base_result

        lstm_prob = predict_lstm(model, recent_sequence, scaler_mean, scaler_std)
        # 기존 확률을 LGB/XGB로 분해 (대략 55:45)
        lgb_prob = base_result.probability * 1.05  # 약간 보정
        xgb_prob = base_result.probability * 0.95
        final_prob = ensemble_3model_predict(lgb_prob, xgb_prob, lstm_prob)

        from kstock.ml.predictor import _probability_to_label
        return PredictionResult(
            probability=round(final_prob, 4),
            label=_probability_to_label(final_prob),
            shap_top3=base_result.shap_top3,
        )
    except Exception as e:
        logger.debug("LSTM 예측 실패, 기존 모델 사용: %s", e)
        return base_result
```

## 검증

1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. torch 없을 때 graceful degradation 확인
3. LSTM 예측 확률이 0~1 범위인지
4. 3-모델 앙상블이 기존 2-모델보다 나쁘지 않은지 (worst case = 동일)
5. 모델 저장/로딩 후 예측 결과 동일한지

## 테스트

`tests/test_lstm_predictor.py`:

```python
def test_graceful_without_torch():
    """torch 없을 때 neutral 반환."""

def test_build_sequences_shape():
    """시퀀스 변환 후 shape 확인."""
    features = np.random.randn(100, 30)
    targets = np.random.randint(0, 2, 100)
    X, y = build_sequences(features, targets, seq_len=20)
    assert X.shape == (80, 20, 30)
    assert y.shape == (80,)

def test_ensemble_3model_fallback():
    """LSTM이 0.5일 때 기존 2-모델로 fallback."""

def test_model_save_load():
    """저장 후 로딩 → 동일 예측."""
```

## 주의사항

| 항목 | 주의 |
|------|------|
| PyTorch 설치 | `pip install torch --index-url https://download.pytorch.org/whl/cpu` (CPU 전용) |
| Mac Mini M1 | MPS 가능하지만 CPU가 안전. `map_location='cpu'` 사용 |
| 메모리 | 시퀀스 데이터가 클 수 있음. float32 사용 (float64 아님) |
| graceful degradation | torch import 실패 → 모든 함수가 neutral 반환 |
| 기존 predictor.py 수정 최소 | 가능하면 수정 안 하고 외부에서 합산 |
| 재학습 시간 | 일요일 새벽 3시. 5~10분 소요 예상 |
| 모델 파일 경로 | `models/lstm_stock.pt` — .gitignore에 추가 |
| 피처 정규화 | 학습/예측 시 동일한 scaler 사용 필수 |
| 시퀀스 길이 | 20일 고정. 변경 시 재학습 필요 |
| PYTHONPATH=src | 반드시 설정 |
