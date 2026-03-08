"""EWC (Elastic Weight Consolidation) + LightGBM 점진 학습.

v10.1 Phase C: 매일 새로운 데이터로 모델을 점진적으로 업데이트하되,
과거에 학습한 지식을 잊지 않도록 보호하는 연속 학습 시스템.

- LSTM: Fisher Information Matrix 기반 EWC 정규화
- LightGBM: init_model 기반 부스팅 트리 추가
- 앙상블 가중치: 최근 성과 기반 미세 조정
"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'models')

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except (ImportError, OSError):
    torch = None
    nn = None
    _HAS_TORCH = False

try:
    import lightgbm as lgb
    _HAS_LGB = True
except (ImportError, OSError):
    lgb = None
    _HAS_LGB = False


# ── Data Classes ─────────────────────────────────────────

@dataclass
class IncrementalResult:
    """점진 학습 결과."""
    success: bool = False
    lgb_trees_added: int = 0
    lstm_epochs: int = 0
    lstm_loss_before: float = 0.0
    lstm_loss_after: float = 0.0
    samples_used: int = 0
    message: str = ""


# ── EWC Trainer (LSTM) ───────────────────────────────────

class EWCTrainer:
    """Elastic Weight Consolidation으로 LSTM 점진 학습.

    핵심 원리:
    - Fisher Information Matrix로 각 파라미터의 중요도 계산
    - 중요한 파라미터는 덜 변하게 제약 → 과거 지식 보존
    - 새 데이터로 소수 에폭 미세 조정 → 최신 시장 반영

    Usage:
        ewc = EWCTrainer(lstm_model, fisher_multiplier=400)
        ewc.compute_fisher(old_dataloader)   # 기존 데이터로 Fisher 계산
        ewc.incremental_update(new_X, new_y, epochs=5)  # 새 데이터로 업데이트
    """

    def __init__(self, model=None, fisher_multiplier: float = 400.0):
        self.model = model
        self.fisher_multiplier = fisher_multiplier
        self._fisher: dict[str, np.ndarray] = {}
        self._optimal_params: dict[str, np.ndarray] = {}
        self._fisher_computed = False

    def compute_fisher(
        self,
        X: np.ndarray,
        y: np.ndarray,
        n_samples: int = 200,
    ) -> None:
        """현재 모델의 Fisher Information Matrix 계산.

        Args:
            X: (n, seq_len, features) 시퀀스 데이터.
            y: (n,) 타겟.
            n_samples: Fisher 추정에 사용할 샘플 수.
        """
        if not _HAS_TORCH or self.model is None:
            return

        self.model.eval()

        # 현재 최적 파라미터 저장
        self._optimal_params = {
            name: param.data.clone()
            for name, param in self.model.named_parameters()
        }

        # Fisher Information = E[∇log p(y|x, θ)²]
        fisher = {
            name: torch.zeros_like(param)
            for name, param in self.model.named_parameters()
        }

        n = min(len(X), n_samples)
        indices = np.random.choice(len(X), n, replace=False)

        criterion = nn.BCELoss()

        for idx in indices:
            x_t = torch.FloatTensor(X[idx:idx + 1])
            y_t = torch.FloatTensor([[y[idx]]])

            self.model.zero_grad()
            output = self.model(x_t)
            loss = criterion(output, y_t)
            loss.backward()

            for name, param in self.model.named_parameters():
                if param.grad is not None:
                    fisher[name] += param.grad.data ** 2

        # 평균
        for name in fisher:
            fisher[name] /= n

        self._fisher = {
            name: f.cpu().numpy() for name, f in fisher.items()
        }
        self._fisher_computed = True
        logger.info("Fisher Information computed with %d samples", n)

    def ewc_loss(self, model, base_loss: torch.Tensor) -> torch.Tensor:
        """EWC 정규화 손실 = base_loss + λ/2 * Σ Fi(θi - θ*i)².

        Args:
            model: 현재 학습 중인 모델.
            base_loss: 새 데이터에 대한 기본 손실.

        Returns:
            EWC 정규화가 적용된 전체 손실.
        """
        if not self._fisher_computed or not _HAS_TORCH:
            return base_loss

        ewc_penalty = torch.tensor(0.0)

        for name, param in model.named_parameters():
            if name in self._fisher and name in self._optimal_params:
                fisher_t = torch.FloatTensor(self._fisher[name])
                optimal_t = self._optimal_params[name]
                ewc_penalty += (fisher_t * (param - optimal_t) ** 2).sum()

        return base_loss + (self.fisher_multiplier / 2.0) * ewc_penalty

    def incremental_update(
        self,
        X_new: np.ndarray,
        y_new: np.ndarray,
        epochs: int = 5,
        lr: float = 0.0005,
        batch_size: int = 32,
        scaler_mean: np.ndarray | None = None,
        scaler_std: np.ndarray | None = None,
    ) -> IncrementalResult:
        """새 데이터로 LSTM 점진 학습 (EWC 정규화 적용).

        Args:
            X_new: (n, seq_len, features) 새 시퀀스 데이터.
            y_new: (n,) 새 타겟.
            epochs: 미세조정 에폭 수 (소수).
            lr: 학습률 (기존보다 낮게).
            scaler_mean, scaler_std: 정규화 파라미터.
        """
        if X_new is None or y_new is None:
            return IncrementalResult(message="No data provided")

        result = IncrementalResult(samples_used=len(X_new))

        if not _HAS_TORCH or self.model is None or len(X_new) < 5:
            result.message = "LSTM unavailable or insufficient data"
            return result

        try:
            # 정규화
            if scaler_mean is not None and scaler_std is not None:
                X_new = (X_new - scaler_mean) / (scaler_std + 1e-8)

            X_t = torch.FloatTensor(X_new)
            y_t = torch.FloatTensor(y_new).unsqueeze(1)

            # 학습 전 손실 측정
            self.model.eval()
            criterion = nn.BCELoss()
            with torch.no_grad():
                pred_before = self.model(X_t)
                loss_before = criterion(pred_before, y_t).item()
            result.lstm_loss_before = round(loss_before, 4)

            # 점진 학습
            optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
            self.model.train()

            for epoch in range(epochs):
                for start in range(0, len(X_t), batch_size):
                    end = min(start + batch_size, len(X_t))
                    batch_x = X_t[start:end]
                    batch_y = y_t[start:end]

                    optimizer.zero_grad()
                    output = self.model(batch_x)
                    base_loss = criterion(output, batch_y)

                    # EWC 정규화 적용
                    total_loss = self.ewc_loss(self.model, base_loss)
                    total_loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    optimizer.step()

            # 학습 후 손실 측정
            self.model.eval()
            with torch.no_grad():
                pred_after = self.model(X_t)
                loss_after = criterion(pred_after, y_t).item()
            result.lstm_loss_after = round(loss_after, 4)
            result.lstm_epochs = epochs
            result.success = True
            result.message = (
                f"LSTM EWC update: loss {loss_before:.4f} → {loss_after:.4f} "
                f"({epochs} epochs, {len(X_new)} samples)"
            )
            logger.info(result.message)

        except Exception as e:
            result.message = f"LSTM EWC update failed: {e}"
            logger.error(result.message, exc_info=True)

        return result


# ── LightGBM Incremental Update ──────────────────────────

def incremental_lgb_update(
    existing_model_path: str | None = None,
    new_X: np.ndarray | None = None,
    new_y: np.ndarray | None = None,
    n_new_trees: int = 10,
    learning_rate: float = 0.02,
) -> IncrementalResult:
    """기존 LightGBM 모델에 새 트리 추가 (온라인 학습).

    LightGBM의 init_model 파라미터를 활용:
    - 기존 모델의 모든 트리를 유지
    - 새 데이터에 대한 트리 n_new_trees개 추가
    - 학습률을 낮춰 급격한 변화 방지

    Args:
        existing_model_path: 기존 LGB 모델 파일 경로.
        new_X: 새 학습 피처 (n, features).
        new_y: 새 타겟 (n,).
        n_new_trees: 추가할 부스팅 라운드 수.
        learning_rate: 점진 학습 시 학습률 (기본보다 낮음).

    Returns:
        IncrementalResult.
    """
    result = IncrementalResult()

    if not _HAS_LGB:
        result.message = "LightGBM not available"
        return result

    if new_X is None or new_y is None or len(new_X) < 10:
        result.message = "Insufficient new data for incremental update"
        return result

    model_path = existing_model_path or os.path.join(MODEL_DIR, "lgb_model.txt")

    try:
        # 기존 모델 로드
        init_model = None
        if os.path.exists(model_path):
            init_model = lgb.Booster(model_file=model_path)
            logger.info("Loaded existing LGB model from %s", model_path)

        # 새 데이터셋
        new_train = lgb.Dataset(new_X, label=new_y)

        params = {
            "objective": "binary",
            "metric": "auc",
            "verbosity": -1,
            "n_jobs": -1,
            "learning_rate": learning_rate,
            "num_leaves": 31,
            "min_child_samples": 10,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
        }

        # 기존 모델 이어서 학습
        updated = lgb.train(
            params,
            new_train,
            num_boost_round=n_new_trees,
            init_model=init_model,
        )

        # 모델 저장
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        updated.save_model(model_path)

        result.success = True
        result.lgb_trees_added = n_new_trees
        result.samples_used = len(new_X)
        total_trees = updated.num_trees()
        result.message = (
            f"LGB incremental: +{n_new_trees} trees "
            f"(total={total_trees}, {len(new_X)} samples)"
        )
        logger.info(result.message)

    except Exception as e:
        result.message = f"LGB incremental update failed: {e}"
        logger.error(result.message, exc_info=True)

    return result
