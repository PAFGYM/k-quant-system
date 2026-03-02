"""Portfolio optimization engine (포트폴리오 최적화 엔진).

Implements Markowitz mean-variance, risk parity (ERC), Black-Litterman,
minimum variance, and efficient frontier computation.

All functions are pure computation with no external API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OptimizedPortfolio:
    """Result of portfolio optimization."""

    method: str                          # e.g. "markowitz", "risk_parity"
    weights: dict[str, float]            # ticker -> weight
    expected_return: float               # annualized
    expected_volatility: float           # annualized
    sharpe_ratio: float
    diversification_ratio: float         # weighted avg vol / portfolio vol
    max_weight: float
    min_weight: float
    effective_n: float                   # effective number of assets = 1/sum(w^2)
    message: str = ""


@dataclass
class EfficientFrontierPoint:
    """Single point on the efficient frontier."""

    target_return: float
    volatility: float
    sharpe_ratio: float
    weights: dict[str, float]


@dataclass
class EfficientFrontier:
    """Collection of efficient frontier points."""

    points: list[EfficientFrontierPoint] = field(default_factory=list)
    optimal_point: EfficientFrontierPoint | None = None
    min_variance_point: EfficientFrontierPoint | None = None
    max_return_point: EfficientFrontierPoint | None = None


@dataclass
class BlackLittermanInput:
    """Single investor view for Black-Litterman."""

    view_ticker: str
    view_return: float      # expected annual return for this ticker
    confidence: float       # 0~1, higher = more confident


@dataclass
class RiskContribution:
    """Risk contribution breakdown for a single asset."""

    ticker: str
    weight: float
    marginal_risk: float
    risk_contribution: float
    risk_contribution_pct: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_returns(
    ohlcv_map: dict[str, pd.DataFrame],
    lookback: int = 252,
) -> tuple[np.ndarray, list[str]]:
    """Extract aligned daily return matrix from OHLCV map.

    Returns:
        (returns_matrix: shape (T, N), tickers: list of length N)
    """
    returns_dict: dict[str, pd.Series] = {}
    for ticker, df in ohlcv_map.items():
        if df is None or df.empty:
            continue
        close = df["close"].astype(float)
        if len(close) < 2:
            continue
        ret = close.pct_change().dropna()
        # Use last `lookback` days (or whatever is available)
        ret = ret.iloc[-lookback:]
        if len(ret) >= 20:  # minimum usable length
            returns_dict[ticker] = ret

    if not returns_dict:
        return np.empty((0, 0)), []

    # Align on common dates via DataFrame
    ret_df = pd.DataFrame(returns_dict)
    ret_df = ret_df.dropna()

    if ret_df.empty:
        return np.empty((0, 0)), []

    tickers = list(ret_df.columns)
    return ret_df.values, tickers


def _build_covariance_matrix(
    ohlcv_map: dict[str, pd.DataFrame],
    lookback: int = 252,
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Build annualized covariance matrix with Ledoit-Wolf shrinkage.

    Args:
        ohlcv_map: ticker -> OHLCV DataFrame
        lookback: number of trading days to use

    Returns:
        (cov_matrix: (N,N) ndarray, tickers: list[str], mean_returns: (N,) ndarray)
        All annualized (252 trading days).
    """
    returns_matrix, tickers = _extract_returns(ohlcv_map, lookback)
    if len(tickers) == 0:
        return np.empty((0, 0)), [], np.empty(0)

    T, N = returns_matrix.shape

    # Sample covariance
    sample_cov = np.cov(returns_matrix, rowvar=False, ddof=1)
    if N == 1:
        sample_cov = sample_cov.reshape(1, 1)

    # Ledoit-Wolf shrinkage (simplified: fixed alpha=0.1)
    alpha = 0.1
    mu_trace = np.trace(sample_cov) / N
    target = mu_trace * np.eye(N)
    shrunk_cov = (1 - alpha) * sample_cov + alpha * target

    # Annualize
    ann_cov = shrunk_cov * 252
    mean_returns = returns_matrix.mean(axis=0) * 252

    return ann_cov, tickers, mean_returns


def _portfolio_stats(
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    risk_free_rate: float = 0.035,
) -> tuple[float, float, float]:
    """Compute portfolio return, volatility, Sharpe."""
    port_return = float(weights @ mean_returns)
    port_vol = float(np.sqrt(weights @ cov_matrix @ weights))
    sharpe = (port_return - risk_free_rate) / port_vol if port_vol > 1e-10 else 0.0
    return port_return, port_vol, sharpe


def _diversification_ratio(
    weights: np.ndarray,
    cov_matrix: np.ndarray,
) -> float:
    """Weighted average individual vol / portfolio vol."""
    individual_vols = np.sqrt(np.diag(cov_matrix))
    weighted_avg_vol = float(weights @ individual_vols)
    port_vol = float(np.sqrt(weights @ cov_matrix @ weights))
    if port_vol < 1e-10:
        return 1.0
    return weighted_avg_vol / port_vol


def _effective_n(weights: np.ndarray) -> float:
    """Herfindahl-based effective number of assets: 1 / sum(w^2)."""
    sum_sq = float(np.sum(weights ** 2))
    if sum_sq < 1e-10:
        return 0.0
    return 1.0 / sum_sq


def _build_result(
    method: str,
    weights: np.ndarray,
    tickers: list[str],
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    risk_free_rate: float = 0.035,
    message: str = "",
) -> OptimizedPortfolio:
    """Construct an OptimizedPortfolio from raw arrays."""
    port_ret, port_vol, sharpe = _portfolio_stats(
        weights, mean_returns, cov_matrix, risk_free_rate
    )
    div_ratio = _diversification_ratio(weights, cov_matrix)
    eff_n = _effective_n(weights)
    w_dict = {t: round(float(w), 6) for t, w in zip(tickers, weights)}

    return OptimizedPortfolio(
        method=method,
        weights=w_dict,
        expected_return=round(port_ret, 6),
        expected_volatility=round(port_vol, 6),
        sharpe_ratio=round(sharpe, 4),
        diversification_ratio=round(div_ratio, 4),
        max_weight=round(float(np.max(weights)), 6),
        min_weight=round(float(np.min(weights)), 6),
        effective_n=round(eff_n, 2),
        message=message,
    )


def _handle_single_or_empty(
    ohlcv_map: dict[str, pd.DataFrame],
    method: str,
    risk_free_rate: float = 0.035,
) -> OptimizedPortfolio | None:
    """Return result for trivial cases (0 or 1 asset), or None if N>=2."""
    # Filter valid tickers
    valid = {
        t: df for t, df in ohlcv_map.items()
        if df is not None and not df.empty and len(df) >= 2
    }
    if len(valid) == 0:
        return OptimizedPortfolio(
            method=method,
            weights={},
            expected_return=0.0,
            expected_volatility=0.0,
            sharpe_ratio=0.0,
            diversification_ratio=1.0,
            max_weight=0.0,
            min_weight=0.0,
            effective_n=0.0,
            message="입력 데이터 없음",
        )
    if len(valid) == 1:
        ticker = list(valid.keys())[0]
        df = valid[ticker]
        close = df["close"].astype(float)
        ret = close.pct_change().dropna()
        ann_ret = float(ret.mean() * 252) if len(ret) > 0 else 0.0
        ann_vol = float(ret.std() * np.sqrt(252)) if len(ret) > 1 else 0.0
        sharpe = (
            (ann_ret - risk_free_rate) / ann_vol
            if ann_vol > 1e-10
            else 0.0
        )
        return OptimizedPortfolio(
            method=method,
            weights={ticker: 1.0},
            expected_return=round(ann_ret, 6),
            expected_volatility=round(ann_vol, 6),
            sharpe_ratio=round(sharpe, 4),
            diversification_ratio=1.0,
            max_weight=1.0,
            min_weight=1.0,
            effective_n=1.0,
            message="단일 자산 포트폴리오",
        )
    return None


# ---------------------------------------------------------------------------
# Core optimization functions
# ---------------------------------------------------------------------------

def optimize_markowitz(
    ohlcv_map: dict[str, pd.DataFrame],
    target_return: float | None = None,
    risk_free_rate: float = 0.035,
    min_weight: float = 0.0,
    max_weight: float = 0.3,
) -> OptimizedPortfolio:
    """Markowitz mean-variance optimization.

    Args:
        ohlcv_map: ticker -> OHLCV DataFrame
        target_return: if None, maximize Sharpe ratio.
                       if given, minimize volatility s.t. return >= target.
        risk_free_rate: annualized risk-free rate (default 3.5%)
        min_weight: minimum weight per asset
        max_weight: maximum weight per asset

    Returns:
        OptimizedPortfolio
    """
    trivial = _handle_single_or_empty(ohlcv_map, "markowitz", risk_free_rate)
    if trivial is not None:
        return trivial

    cov, tickers, mu = _build_covariance_matrix(ohlcv_map)
    N = len(tickers)
    if N == 0:
        return _handle_single_or_empty({}, "markowitz", risk_free_rate)  # type: ignore

    # Ensure max_weight allows feasibility: max_weight >= 1/N
    effective_max = max(max_weight, 1.0 / N + 1e-6)
    effective_min = min(min_weight, effective_max - 1e-6)
    bounds = [(effective_min, effective_max)] * N
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

    w0 = np.full(N, 1.0 / N)

    if target_return is None:
        # Maximize Sharpe: minimize negative Sharpe
        def neg_sharpe(w: np.ndarray) -> float:
            ret = w @ mu
            vol = np.sqrt(w @ cov @ w)
            if vol < 1e-12:
                return 0.0
            return -(ret - risk_free_rate) / vol

        result = minimize(
            neg_sharpe, w0, method="SLSQP",
            bounds=bounds, constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        method_msg = "Max Sharpe 포트폴리오"
    else:
        # Minimize volatility s.t. return >= target
        def portfolio_vol(w: np.ndarray) -> float:
            return float(np.sqrt(w @ cov @ w))

        return_constraint = {
            "type": "ineq",
            "fun": lambda w: w @ mu - target_return,
        }
        constraints.append(return_constraint)

        result = minimize(
            portfolio_vol, w0, method="SLSQP",
            bounds=bounds, constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        method_msg = f"목표수익률 {target_return:.1%} 최소분산 포트폴리오"

    if not result.success:
        logger.warning("Markowitz optimization failed: %s. Using equal weight.", result.message)
        weights = w0
        method_msg += " (폴백: 등가중)"
    else:
        weights = result.x

    # Clip tiny negatives from numerical noise
    weights = np.maximum(weights, 0.0)
    weights = weights / weights.sum()

    return _build_result(
        method="markowitz",
        weights=weights,
        tickers=tickers,
        mean_returns=mu,
        cov_matrix=cov,
        risk_free_rate=risk_free_rate,
        message=method_msg,
    )


def optimize_risk_parity(
    ohlcv_map: dict[str, pd.DataFrame],
    risk_free_rate: float = 0.035,
) -> OptimizedPortfolio:
    """Risk parity (Equal Risk Contribution) optimization.

    Finds weights such that each asset contributes equally to total
    portfolio risk. Falls back to inverse-volatility if optimization fails.

    Args:
        ohlcv_map: ticker -> OHLCV DataFrame
        risk_free_rate: annualized risk-free rate

    Returns:
        OptimizedPortfolio
    """
    trivial = _handle_single_or_empty(ohlcv_map, "risk_parity", risk_free_rate)
    if trivial is not None:
        return trivial

    cov, tickers, mu = _build_covariance_matrix(ohlcv_map)
    N = len(tickers)
    if N == 0:
        return _handle_single_or_empty({}, "risk_parity", risk_free_rate)  # type: ignore

    target_rc = 1.0 / N

    def risk_parity_obj(w: np.ndarray) -> float:
        w = np.maximum(w, 1e-10)
        port_var = w @ cov @ w
        if port_var < 1e-20:
            return 0.0
        marginal = cov @ w
        rc = w * marginal / port_var  # risk contribution fractions
        return float(np.sum((rc - target_rc) ** 2))

    # Use log-transform trick: w = exp(x) / sum(exp(x)) to keep weights positive
    # and automatically satisfy sum=1 constraint
    def risk_parity_obj_log(x: np.ndarray) -> float:
        w = np.exp(x)
        w = w / w.sum()
        return risk_parity_obj(w)

    x0 = np.zeros(N)
    result = minimize(
        risk_parity_obj_log, x0, method="SLSQP",
        options={"maxiter": 2000, "ftol": 1e-14},
    )

    if result.success:
        weights = np.exp(result.x)
        weights = weights / weights.sum()
        msg = "리스크 패리티 (ERC) 포트폴리오"
    else:
        # Fallback: inverse-volatility weighting
        logger.warning("Risk parity optimization failed, using inverse-vol fallback.")
        vols = np.sqrt(np.diag(cov))
        inv_vol = 1.0 / np.maximum(vols, 1e-10)
        weights = inv_vol / inv_vol.sum()
        msg = "리스크 패리티 (역변동성 폴백)"

    weights = np.maximum(weights, 0.0)
    weights = weights / weights.sum()

    return _build_result(
        method="risk_parity",
        weights=weights,
        tickers=tickers,
        mean_returns=mu,
        cov_matrix=cov,
        risk_free_rate=risk_free_rate,
        message=msg,
    )


def optimize_black_litterman(
    ohlcv_map: dict[str, pd.DataFrame],
    views: list[BlackLittermanInput],
    market_weights: dict[str, float],
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    risk_free_rate: float = 0.035,
    max_weight: float = 0.4,
) -> OptimizedPortfolio:
    """Black-Litterman portfolio optimization.

    Combines market equilibrium with investor views to produce a
    posterior expected return, then runs mean-variance optimization.

    Args:
        ohlcv_map: ticker -> OHLCV DataFrame
        views: list of BlackLittermanInput (investor views)
        market_weights: ticker -> market cap weight
        risk_aversion: risk aversion parameter (delta)
        tau: uncertainty scaling (typically 0.025~0.05)
        risk_free_rate: annualized risk-free rate
        max_weight: maximum weight per asset

    Returns:
        OptimizedPortfolio
    """
    trivial = _handle_single_or_empty(ohlcv_map, "black_litterman", risk_free_rate)
    if trivial is not None:
        return trivial

    cov, tickers, _ = _build_covariance_matrix(ohlcv_map)
    N = len(tickers)
    if N == 0:
        return _handle_single_or_empty({}, "black_litterman", risk_free_rate)  # type: ignore

    ticker_idx = {t: i for i, t in enumerate(tickers)}

    # Market weights vector (aligned to tickers)
    w_mkt = np.zeros(N)
    for t, w in market_weights.items():
        if t in ticker_idx:
            w_mkt[ticker_idx[t]] = w
    # Normalize if non-zero
    if w_mkt.sum() > 1e-10:
        w_mkt = w_mkt / w_mkt.sum()
    else:
        w_mkt = np.full(N, 1.0 / N)

    # Equilibrium excess returns: pi = delta * Sigma * w_mkt
    pi = risk_aversion * cov @ w_mkt

    # Filter views to only include tickers in our universe
    valid_views = [v for v in views if v.view_ticker in ticker_idx]

    if len(valid_views) == 0:
        # No valid views -> posterior = equilibrium = market weights
        return _build_result(
            method="black_litterman",
            weights=w_mkt,
            tickers=tickers,
            mean_returns=pi,
            cov_matrix=cov,
            risk_free_rate=risk_free_rate,
            message="블랙-리터만 (뷰 없음, 균형 수익률 사용)",
        )

    # Build P (pick matrix), Q (view returns), Omega (uncertainty)
    K = len(valid_views)  # number of views
    P = np.zeros((K, N))
    Q = np.zeros(K)
    omega_diag = np.zeros(K)

    for k, view in enumerate(valid_views):
        idx = ticker_idx[view.view_ticker]
        P[k, idx] = 1.0
        Q[k] = view.view_return
        # Omega: uncertainty = (1 - confidence) * tau * variance of that asset
        conf = np.clip(view.confidence, 0.01, 0.99)
        omega_diag[k] = ((1.0 - conf) / conf) * tau * cov[idx, idx]

    Omega = np.diag(omega_diag)

    # Posterior expected returns:
    # mu_bl = inv(inv(tau*Sigma) + P'*inv(Omega)*P) @ (inv(tau*Sigma)@pi + P'*inv(Omega)@Q)
    tau_cov = tau * cov
    tau_cov_inv = np.linalg.inv(tau_cov)
    Omega_inv = np.linalg.inv(Omega)

    # Posterior precision
    posterior_precision = tau_cov_inv + P.T @ Omega_inv @ P
    # Posterior mean
    posterior_cov = np.linalg.inv(posterior_precision)
    mu_bl = posterior_cov @ (tau_cov_inv @ pi + P.T @ Omega_inv @ Q)

    # Optimize with posterior returns (max Sharpe)
    effective_max = max(max_weight, 1.0 / N + 1e-6)
    bounds = [(0.0, effective_max)] * N
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    w0 = np.full(N, 1.0 / N)

    def neg_sharpe(w: np.ndarray) -> float:
        ret = w @ mu_bl
        vol = np.sqrt(w @ cov @ w)
        if vol < 1e-12:
            return 0.0
        return -(ret - risk_free_rate) / vol

    result = minimize(
        neg_sharpe, w0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )

    if result.success:
        weights = result.x
        msg = f"블랙-리터만 포트폴리오 ({K}개 뷰 반영)"
    else:
        logger.warning("BL optimization failed, using posterior-weighted market weights.")
        # Fallback: use BL returns to tilt market weights
        tilt = mu_bl - pi
        weights = w_mkt + 0.1 * tilt
        weights = np.maximum(weights, 0.0)
        weights = weights / weights.sum()
        msg = "블랙-리터만 (최적화 폴백)"

    weights = np.maximum(weights, 0.0)
    weights = weights / weights.sum()

    return _build_result(
        method="black_litterman",
        weights=weights,
        tickers=tickers,
        mean_returns=mu_bl,
        cov_matrix=cov,
        risk_free_rate=risk_free_rate,
        message=msg,
    )


def optimize_min_variance(
    ohlcv_map: dict[str, pd.DataFrame],
    max_weight: float = 0.3,
    risk_free_rate: float = 0.035,
) -> OptimizedPortfolio:
    """Global minimum variance portfolio.

    Minimizes portfolio variance without any return constraint.

    Args:
        ohlcv_map: ticker -> OHLCV DataFrame
        max_weight: maximum weight per asset
        risk_free_rate: annualized risk-free rate

    Returns:
        OptimizedPortfolio
    """
    trivial = _handle_single_or_empty(ohlcv_map, "min_variance", risk_free_rate)
    if trivial is not None:
        return trivial

    cov, tickers, mu = _build_covariance_matrix(ohlcv_map)
    N = len(tickers)
    if N == 0:
        return _handle_single_or_empty({}, "min_variance", risk_free_rate)  # type: ignore

    effective_max = max(max_weight, 1.0 / N + 1e-6)
    bounds = [(0.0, effective_max)] * N
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    w0 = np.full(N, 1.0 / N)

    def portfolio_var(w: np.ndarray) -> float:
        return float(w @ cov @ w)

    result = minimize(
        portfolio_var, w0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )

    if result.success:
        weights = result.x
        msg = "최소분산 포트폴리오"
    else:
        logger.warning("Min variance optimization failed. Using equal weight.")
        weights = w0
        msg = "최소분산 (폴백: 등가중)"

    weights = np.maximum(weights, 0.0)
    weights = weights / weights.sum()

    return _build_result(
        method="min_variance",
        weights=weights,
        tickers=tickers,
        mean_returns=mu,
        cov_matrix=cov,
        risk_free_rate=risk_free_rate,
        message=msg,
    )


# ---------------------------------------------------------------------------
# Efficient frontier
# ---------------------------------------------------------------------------

def compute_efficient_frontier(
    ohlcv_map: dict[str, pd.DataFrame],
    n_points: int = 20,
    risk_free_rate: float = 0.035,
    max_weight: float = 0.3,
) -> EfficientFrontier:
    """Compute the efficient frontier by varying target return.

    Args:
        ohlcv_map: ticker -> OHLCV DataFrame
        n_points: number of frontier points to compute
        risk_free_rate: annualized risk-free rate
        max_weight: maximum weight per asset

    Returns:
        EfficientFrontier
    """
    cov, tickers, mu = _build_covariance_matrix(ohlcv_map)
    N = len(tickers)

    if N == 0:
        return EfficientFrontier()

    # Find return range
    min_ret = float(mu.min())
    max_ret = float(mu.max())

    # For single asset case
    if N == 1:
        vol = float(np.sqrt(cov[0, 0]))
        sharpe = (mu[0] - risk_free_rate) / vol if vol > 1e-10 else 0.0
        pt = EfficientFrontierPoint(
            target_return=round(float(mu[0]), 6),
            volatility=round(vol, 6),
            sharpe_ratio=round(float(sharpe), 4),
            weights={tickers[0]: 1.0},
        )
        return EfficientFrontier(
            points=[pt],
            optimal_point=pt,
            min_variance_point=pt,
            max_return_point=pt,
        )

    effective_max = max(max_weight, 1.0 / N + 1e-6)
    bounds = [(0.0, effective_max)] * N
    constraints_base = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    w0 = np.full(N, 1.0 / N)

    target_returns = np.linspace(min_ret, max_ret, n_points)
    points: list[EfficientFrontierPoint] = []
    best_sharpe_pt: EfficientFrontierPoint | None = None
    min_vol_pt: EfficientFrontierPoint | None = None
    max_ret_pt: EfficientFrontierPoint | None = None

    for tgt in target_returns:
        constraints = list(constraints_base)
        constraints.append({
            "type": "ineq",
            "fun": lambda w, t=tgt: w @ mu - t,
        })

        def portfolio_vol(w: np.ndarray) -> float:
            return float(np.sqrt(w @ cov @ w))

        result = minimize(
            portfolio_vol, w0, method="SLSQP",
            bounds=bounds, constraints=constraints,
            options={"maxiter": 500, "ftol": 1e-10},
        )

        if not result.success:
            continue

        weights = np.maximum(result.x, 0.0)
        weights = weights / weights.sum()
        ret, vol, sharpe = _portfolio_stats(weights, mu, cov, risk_free_rate)

        pt = EfficientFrontierPoint(
            target_return=round(ret, 6),
            volatility=round(vol, 6),
            sharpe_ratio=round(sharpe, 4),
            weights={t: round(float(w), 6) for t, w in zip(tickers, weights)},
        )
        points.append(pt)

        if best_sharpe_pt is None or sharpe > best_sharpe_pt.sharpe_ratio:
            best_sharpe_pt = pt
        if min_vol_pt is None or vol < min_vol_pt.volatility:
            min_vol_pt = pt
        if max_ret_pt is None or ret > max_ret_pt.target_return:
            max_ret_pt = pt

    return EfficientFrontier(
        points=points,
        optimal_point=best_sharpe_pt,
        min_variance_point=min_vol_pt,
        max_return_point=max_ret_pt,
    )


# ---------------------------------------------------------------------------
# Risk decomposition
# ---------------------------------------------------------------------------

def compute_risk_contributions(
    weights: dict[str, float],
    cov_matrix: np.ndarray,
    tickers: list[str],
) -> list[RiskContribution]:
    """Compute risk contribution of each asset.

    Args:
        weights: ticker -> weight
        cov_matrix: (N,N) annualized covariance matrix
        tickers: ordered list of tickers matching cov_matrix

    Returns:
        list of RiskContribution, one per asset
    """
    N = len(tickers)
    if N == 0:
        return []

    w = np.array([weights.get(t, 0.0) for t in tickers])
    port_var = w @ cov_matrix @ w

    if port_var < 1e-20:
        return [
            RiskContribution(
                ticker=t, weight=float(w[i]),
                marginal_risk=0.0, risk_contribution=0.0,
                risk_contribution_pct=0.0,
            )
            for i, t in enumerate(tickers)
        ]

    port_vol = np.sqrt(port_var)
    marginal = cov_matrix @ w  # (N,)
    marginal_risk = marginal / port_vol  # d(sigma)/d(w_i)
    risk_contrib = w * marginal_risk  # w_i * d(sigma)/d(w_i)
    total_rc = risk_contrib.sum()

    if abs(total_rc) < 1e-20:
        pcts = np.zeros(N)
    else:
        pcts = risk_contrib / total_rc * 100.0

    results = []
    for i, t in enumerate(tickers):
        results.append(RiskContribution(
            ticker=t,
            weight=round(float(w[i]), 6),
            marginal_risk=round(float(marginal_risk[i]), 6),
            risk_contribution=round(float(risk_contrib[i]), 6),
            risk_contribution_pct=round(float(pcts[i]), 2),
        ))

    return results


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_portfolio_optimization(
    result: OptimizedPortfolio,
    ticker_names: dict[str, str] | None = None,
) -> str:
    """Format optimization result for Telegram display.

    Plain text + emoji, no parse_mode.

    Args:
        result: OptimizedPortfolio to format
        ticker_names: optional ticker -> Korean name mapping

    Returns:
        Formatted string
    """
    if ticker_names is None:
        ticker_names = {}

    method_labels = {
        "markowitz": "마코위츠 최적화",
        "risk_parity": "리스크 패리티",
        "black_litterman": "블랙-리터만",
        "min_variance": "최소분산",
    }
    method_label = method_labels.get(result.method, result.method)

    lines: list[str] = []
    lines.append(f"{'='*24}")
    lines.append(f"  포트폴리오 최적화 결과")
    lines.append(f"{'='*24}")
    lines.append("")
    lines.append(f"  전략: {method_label}")
    if result.message:
        lines.append(f"  {result.message}")
    lines.append("")

    # Performance metrics
    lines.append("  성과 지표")
    lines.append(f"  기대수익률: {result.expected_return:+.2%}")
    lines.append(f"  변동성: {result.expected_volatility:.2%}")
    lines.append(f"  샤프비율: {result.sharpe_ratio:.2f}")
    lines.append(f"  분산비율: {result.diversification_ratio:.2f}")
    lines.append(f"  유효자산수: {result.effective_n:.1f}")
    lines.append("")

    # Weights
    if result.weights:
        lines.append("  종목별 비중")
        # Sort by weight descending
        sorted_w = sorted(result.weights.items(), key=lambda x: -x[1])
        for ticker, weight in sorted_w:
            if weight < 0.001:
                continue
            name = ticker_names.get(ticker, ticker)
            bar_len = int(weight * 20)
            bar = "=" * bar_len
            lines.append(
                f"  {name:<10s} {weight:6.1%} {bar}"
            )
        lines.append("")

    # Weight bounds
    lines.append(f"  최대비중: {result.max_weight:.1%}")
    lines.append(f"  최소비중: {result.min_weight:.1%}")

    return "\n".join(lines)
