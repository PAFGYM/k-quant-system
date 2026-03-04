"""Multi-factor model for cross-sectional stock ranking.

Six academic factors — Size, Value, Momentum, Quality, Volatility, Investment —
computed as cross-sectional z-scores and combined into a composite score.

Usage:
    matrix = build_factor_matrix(universe_data, ohlcv_map)
    profile = score_stock_multifactor("005930", matrix)
    text = format_factor_profile(profile)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FactorExposure:
    """Single factor exposure for a stock."""

    factor_name: str
    raw_value: float
    z_score: float
    percentile: float  # 0 ~ 100


@dataclass
class MultiFactorProfile:
    """Composite multi-factor profile for a stock."""

    ticker: str
    name: str
    exposures: List[FactorExposure]
    composite_score: float  # 0 ~ 100
    quintile: int  # 1 (best) ~ 5 (worst)
    factor_tilt: str  # dominant factor label


@dataclass
class FactorReturn:
    """Return attribution for a single factor over a period."""

    factor_name: str
    period_return_pct: float
    cumulative_return_pct: float
    t_statistic: float
    is_significant: bool  # |t| > 2.0


@dataclass
class FactorModelResult:
    """OLS factor regression result (Fama-French style)."""

    alpha: float
    alpha_t_stat: float
    factor_loadings: Dict[str, float]
    factor_t_stats: Dict[str, float]
    r_squared: float
    adjusted_r_squared: float
    residual_vol: float
    factor_returns: List[FactorReturn] = field(default_factory=list)


@dataclass
class FactorRanking:
    """Factor-sorted quintile ranking and spread."""

    factor_name: str
    top_quintile: List[str]  # tickers
    bottom_quintile: List[str]  # tickers
    spread_return_pct: float  # top - bottom forward return
    ic: float  # information coefficient (rank corr)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FACTOR_NAMES = ["size", "value", "momentum", "quality", "volatility", "investment"]

_FACTOR_EMOJI = {
    "size": "SMB",
    "value": "HML",
    "momentum": "UMD",
    "quality": "QMJ",
    "volatility": "BAB",
    "investment": "CMA",
}


def _zscore(values: dict[str, float]) -> dict[str, float]:
    """Cross-sectional z-score normalisation.

    Returns z-score for each key. If std==0, all z-scores are 0.
    NaN/inf inputs are excluded from mean/std calculation and mapped to 0.
    """
    clean: dict[str, float] = {}
    for k, v in values.items():
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            clean[k] = np.nan
        else:
            clean[k] = float(v)

    arr = np.array([v for v in clean.values() if not np.isnan(v)])
    if len(arr) < 2:
        return {k: 0.0 for k in values}

    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))
    if sigma < 1e-12:
        return {k: 0.0 for k in values}

    result: dict[str, float] = {}
    for k in values:
        v = clean.get(k, np.nan)
        if np.isnan(v):
            result[k] = 0.0
        else:
            result[k] = (v - mu) / sigma
    return result


def _percentile_from_zscore(z: float) -> float:
    """Approximate percentile from z-score using the standard normal CDF.

    Uses a fast rational approximation (Abramowitz & Stegun 26.2.17).
    """
    # scipy-free normal CDF approximation
    return float(_norm_cdf(z) * 100.0)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _quintile_from_score(score: float) -> int:
    """Map 0~100 composite score to quintile 1 (best) ~ 5 (worst)."""
    if score >= 80:
        return 1
    if score >= 60:
        return 2
    if score >= 40:
        return 3
    if score >= 20:
        return 4
    return 5


# ---------------------------------------------------------------------------
# 6 Factor Functions (cross-sectional, pure)
# ---------------------------------------------------------------------------


def compute_size_factor(market_caps: dict[str, float]) -> dict[str, float]:
    """Size factor: log(market_cap) z-score, inverted so small=positive.

    Args:
        market_caps: {ticker: market_cap_krw}

    Returns:
        {ticker: z_score} where positive = small-cap tilt.
    """
    log_caps: dict[str, float] = {}
    for t, cap in market_caps.items():
        if cap is not None and cap > 0:
            log_caps[t] = math.log(cap)
        else:
            log_caps[t] = float("nan")

    z = _zscore(log_caps)
    # Invert: small cap → positive
    return {t: -v for t, v in z.items()}


def compute_value_factor(
    per_map: dict[str, float],
    pbr_map: dict[str, float],
    div_yield_map: dict[str, float],
) -> dict[str, float]:
    """Value factor: composite of 1/PER + 1/PBR + div_yield, z-scored.

    Higher score = cheaper (more value).
    """
    tickers = set(per_map) | set(pbr_map) | set(div_yield_map)

    inv_per: dict[str, float] = {}
    inv_pbr: dict[str, float] = {}
    dy: dict[str, float] = {}

    for t in tickers:
        p = per_map.get(t)
        inv_per[t] = (1.0 / p) if (p is not None and p > 0) else 0.0

        b = pbr_map.get(t)
        inv_pbr[t] = (1.0 / b) if (b is not None and b > 0) else 0.0

        d = div_yield_map.get(t)
        dy[t] = float(d) if d is not None else 0.0

    z_per = _zscore(inv_per)
    z_pbr = _zscore(inv_pbr)
    z_dy = _zscore(dy)

    composite: dict[str, float] = {}
    for t in tickers:
        composite[t] = (z_per.get(t, 0.0) + z_pbr.get(t, 0.0) + z_dy.get(t, 0.0)) / 3.0

    return _zscore(composite)


def compute_momentum_factor(
    ohlcv_map: dict[str, pd.DataFrame],
    lookback: int = 252,
    skip: int = 21,
) -> dict[str, float]:
    """Momentum factor: 12-1 month return, z-scored.

    Skips the most recent *skip* days to avoid short-term reversal.
    Positive score = strong recent performer.

    Args:
        ohlcv_map: {ticker: DataFrame} with 'close' column, sorted by date ascending.
        lookback: total lookback window (trading days).
        skip: recent days to skip.
    """
    mom: dict[str, float] = {}
    for t, df in ohlcv_map.items():
        if df is None or len(df) < lookback:
            mom[t] = float("nan")
            continue
        try:
            close = df["close"].values if "close" in df.columns else df["Close"].values
            end_idx = len(close) - skip  # skip most recent
            start_idx = len(close) - lookback
            if start_idx < 0 or end_idx <= start_idx:
                mom[t] = float("nan")
                continue
            p_end = float(close[end_idx - 1])
            p_start = float(close[start_idx])
            if p_start > 0:
                mom[t] = (p_end - p_start) / p_start
            else:
                mom[t] = float("nan")
        except (KeyError, IndexError):
            mom[t] = float("nan")

    return _zscore(mom)


def compute_quality_factor(
    roe_map: dict[str, float],
    debt_ratio_map: dict[str, float],
    earnings_stability_map: dict[str, float] | None = None,
) -> dict[str, float]:
    """Quality factor: ROE z-score + inverted debt_ratio z-score.

    Optionally includes earnings stability (higher = better).
    """
    z_roe = _zscore(roe_map)
    # Invert debt: lower debt → positive
    inv_debt = {t: -v for t, v in debt_ratio_map.items()}
    z_debt = _zscore(inv_debt)

    tickers = set(roe_map) | set(debt_ratio_map)

    if earnings_stability_map:
        z_es = _zscore(earnings_stability_map)
        composite = {
            t: (z_roe.get(t, 0.0) + z_debt.get(t, 0.0) + z_es.get(t, 0.0)) / 3.0
            for t in tickers
        }
    else:
        composite = {
            t: (z_roe.get(t, 0.0) + z_debt.get(t, 0.0)) / 2.0
            for t in tickers
        }

    return _zscore(composite)


def compute_volatility_factor(
    ohlcv_map: dict[str, pd.DataFrame],
    lookback: int = 60,
) -> dict[str, float]:
    """Volatility factor: realised volatility (annualised), inverted so low_vol=positive.

    Args:
        ohlcv_map: {ticker: DataFrame} with 'close' column.
        lookback: trading days for vol calculation.
    """
    vol: dict[str, float] = {}
    for t, df in ohlcv_map.items():
        if df is None or len(df) < lookback + 1:
            vol[t] = float("nan")
            continue
        try:
            close = df["close"].values if "close" in df.columns else df["Close"].values
            prices = close[-(lookback + 1):]
            log_returns = np.diff(np.log(prices.astype(float)))
            ann_vol = float(np.std(log_returns, ddof=1) * np.sqrt(252))
            vol[t] = ann_vol
        except (KeyError, IndexError, ValueError):
            vol[t] = float("nan")

    z = _zscore(vol)
    # Invert: low volatility → positive
    return {t: -v for t, v in z.items()}


def compute_investment_factor(
    asset_growth_map: dict[str, float],
) -> dict[str, float]:
    """Investment factor: low asset growth = positive (conservative investment).

    Based on Hou-Xue-Zhang (2015) I/A factor.
    """
    z = _zscore(asset_growth_map)
    # Invert: low growth → positive
    return {t: -v for t, v in z.items()}


# ---------------------------------------------------------------------------
# Dynamic regime-based factor weighting
# ---------------------------------------------------------------------------


def get_regime_factor_weights(
    macro_regime: str = "neutral",
    vix: float = 20.0,
) -> dict[str, float]:
    """시장 레짐에 따른 동적 팩터 가중치.

    risk_on:  모멘텀/성장 강조 (momentum, size)
    neutral:  균등
    risk_off: 품질/저변동 강조 (quality, volatility, value)
    panic:    방어/저변동 극대화

    Returns:
        {factor_name: weight} where sum(weights) = 1.0
    """
    if vix > 35 or macro_regime == "panic":
        return {
            "size": 0.05,
            "value": 0.15,
            "momentum": 0.05,
            "quality": 0.30,
            "volatility": 0.35,
            "investment": 0.10,
        }
    elif vix > 25 or macro_regime == "risk_off":
        return {
            "size": 0.08,
            "value": 0.20,
            "momentum": 0.07,
            "quality": 0.25,
            "volatility": 0.25,
            "investment": 0.15,
        }
    elif vix < 15 or macro_regime == "risk_on":
        return {
            "size": 0.15,
            "value": 0.10,
            "momentum": 0.30,
            "quality": 0.15,
            "volatility": 0.10,
            "investment": 0.20,
        }
    else:
        # Equal weight
        return {f: 1.0 / 6 for f in _FACTOR_NAMES}


# ---------------------------------------------------------------------------
# Higher-Level Functions
# ---------------------------------------------------------------------------


def build_factor_matrix(
    universe_data: list[dict],
    ohlcv_map: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build cross-sectional factor matrix for a stock universe.

    Args:
        universe_data: list of dicts, each with keys:
            ticker, name, market_cap, per, pbr, div_yield, roe,
            debt_ratio, asset_growth_pct
        ohlcv_map: {ticker: DataFrame} with 'close' column.

    Returns:
        DataFrame with index=ticker, columns=[size, value, momentum,
        quality, volatility, investment]. All z-score normalised.
    """
    if not universe_data:
        return pd.DataFrame(columns=_FACTOR_NAMES)

    # Extract per-factor input maps
    market_caps: dict[str, float] = {}
    per_map: dict[str, float] = {}
    pbr_map: dict[str, float] = {}
    div_yield_map: dict[str, float] = {}
    roe_map: dict[str, float] = {}
    debt_ratio_map: dict[str, float] = {}
    asset_growth_map: dict[str, float] = {}

    for row in universe_data:
        t = row["ticker"]
        market_caps[t] = row.get("market_cap", 0)
        per_map[t] = row.get("per", 0)
        pbr_map[t] = row.get("pbr", 0)
        div_yield_map[t] = row.get("div_yield", 0)
        roe_map[t] = row.get("roe", 0)
        debt_ratio_map[t] = row.get("debt_ratio", 0)
        asset_growth_map[t] = row.get("asset_growth_pct", 0)

    # Compute each factor
    size_z = compute_size_factor(market_caps)
    value_z = compute_value_factor(per_map, pbr_map, div_yield_map)
    momentum_z = compute_momentum_factor(ohlcv_map)
    quality_z = compute_quality_factor(roe_map, debt_ratio_map)
    volatility_z = compute_volatility_factor(ohlcv_map)
    investment_z = compute_investment_factor(asset_growth_map)

    tickers = [row["ticker"] for row in universe_data]
    data = {
        "size": [size_z.get(t, 0.0) for t in tickers],
        "value": [value_z.get(t, 0.0) for t in tickers],
        "momentum": [momentum_z.get(t, 0.0) for t in tickers],
        "quality": [quality_z.get(t, 0.0) for t in tickers],
        "volatility": [volatility_z.get(t, 0.0) for t in tickers],
        "investment": [investment_z.get(t, 0.0) for t in tickers],
    }

    df = pd.DataFrame(data, index=tickers)
    df.index.name = "ticker"
    return df


def rank_by_factor(
    factor_matrix: pd.DataFrame,
    factor_name: str,
    forward_returns: dict[str, float] | None = None,
) -> FactorRanking:
    """Sort universe by a single factor and compute quintile spread / IC.

    Args:
        factor_matrix: DataFrame from build_factor_matrix().
        factor_name: column name (e.g. 'value').
        forward_returns: {ticker: forward_return_pct} for IC calculation.

    Returns:
        FactorRanking with top/bottom quintile tickers, spread, and IC.
    """
    if factor_name not in factor_matrix.columns:
        raise ValueError(f"Unknown factor: {factor_name}")

    col = factor_matrix[factor_name].dropna().sort_values(ascending=False)
    n = len(col)
    q_size = max(n // 5, 1)

    top_q = list(col.index[:q_size])
    bottom_q = list(col.index[-q_size:])

    # Spread return
    spread = 0.0
    ic = 0.0

    if forward_returns:
        top_ret = np.mean([forward_returns.get(t, 0.0) for t in top_q])
        bot_ret = np.mean([forward_returns.get(t, 0.0) for t in bottom_q])
        spread = float(top_ret - bot_ret)

        # Information coefficient: Spearman rank correlation
        common = [t for t in col.index if t in forward_returns]
        if len(common) >= 3:
            factor_ranks = col[common].rank(ascending=False)
            ret_series = pd.Series({t: forward_returns[t] for t in common})
            ret_ranks = ret_series.rank(ascending=False)
            # Spearman = Pearson of ranks
            ic = float(factor_ranks.corr(ret_ranks))
            if np.isnan(ic):
                ic = 0.0

    return FactorRanking(
        factor_name=factor_name,
        top_quintile=top_q,
        bottom_quintile=bottom_q,
        spread_return_pct=spread,
        ic=ic,
    )


def compute_factor_loadings(
    portfolio_returns: list[float],
    factor_returns_df: pd.DataFrame,
    risk_free_rate: float = 0.035,
) -> FactorModelResult:
    """OLS factor regression: R_p - R_f = alpha + sum(beta_i * F_i) + epsilon.

    Uses numpy-only OLS (no statsmodels dependency).

    Args:
        portfolio_returns: list of period returns (e.g. daily).
        factor_returns_df: DataFrame with columns = factor names, rows = periods.
        risk_free_rate: annualised risk-free rate (divided by 252 internally).

    Returns:
        FactorModelResult with alpha, betas, t-stats, R-squared.
    """
    n_obs = len(portfolio_returns)
    if n_obs < 3:
        raise ValueError(f"Need at least 3 observations, got {n_obs}")

    y = np.array(portfolio_returns, dtype=float)

    # Align lengths
    X_raw = factor_returns_df.values[:n_obs].astype(float)
    if len(X_raw) < n_obs:
        n_obs = len(X_raw)
        y = y[:n_obs]

    # Excess returns
    rf_period = risk_free_rate / 252.0
    y_excess = y - rf_period

    # Add intercept column
    ones = np.ones((n_obs, 1))
    X = np.hstack([ones, X_raw])  # [intercept, f1, f2, ...]

    k = X.shape[1]  # intercept + num_factors

    # OLS: beta = (X'X)^-1 X'y
    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)

    beta_hat = XtX_inv @ (X.T @ y_excess)
    residuals = y_excess - X @ beta_hat

    # R-squared
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y_excess - np.mean(y_excess)) ** 2))
    r_sq = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-15 else 0.0
    r_sq = max(0.0, min(1.0, r_sq))

    adj_r_sq = 1.0 - (1.0 - r_sq) * (n_obs - 1) / max(n_obs - k, 1)
    adj_r_sq = max(0.0, min(1.0, adj_r_sq))

    # Standard errors and t-stats
    sigma2 = ss_res / max(n_obs - k, 1)
    se = np.sqrt(np.abs(np.diag(XtX_inv) * sigma2))

    t_stats = np.zeros(k)
    for i in range(k):
        t_stats[i] = beta_hat[i] / se[i] if se[i] > 1e-15 else 0.0

    # Residual volatility (annualised)
    residual_vol = float(np.std(residuals, ddof=1) * np.sqrt(252))

    # Map to factor names
    factor_names = list(factor_returns_df.columns)
    factor_loadings = {fn: float(beta_hat[i + 1]) for i, fn in enumerate(factor_names)}
    factor_t = {fn: float(t_stats[i + 1]) for i, fn in enumerate(factor_names)}

    # Build factor returns summary
    factor_rets_list: list[FactorReturn] = []
    for fn in factor_names:
        if fn in factor_returns_df.columns:
            col = factor_returns_df[fn].values[:n_obs]
            period_ret = float(np.mean(col) * 252 * 100)  # annualised %
            cum_ret = float((np.prod(1.0 + col) - 1.0) * 100)
            t_stat_f = factor_t.get(fn, 0.0)
            factor_rets_list.append(
                FactorReturn(
                    factor_name=fn,
                    period_return_pct=period_ret,
                    cumulative_return_pct=cum_ret,
                    t_statistic=t_stat_f,
                    is_significant=abs(t_stat_f) > 2.0,
                )
            )

    return FactorModelResult(
        alpha=float(beta_hat[0]) * 252,  # annualised
        alpha_t_stat=float(t_stats[0]),
        factor_loadings=factor_loadings,
        factor_t_stats=factor_t,
        r_squared=r_sq,
        adjusted_r_squared=adj_r_sq,
        residual_vol=residual_vol,
        factor_returns=factor_rets_list,
    )


def fama_macbeth_regression(
    factor_matrix: pd.DataFrame,
    forward_returns: dict[str, float],
    n_periods: int = 1,
) -> dict:
    """Fama-MacBeth 횡단면 회귀: 팩터 프리미엄 추정.

    각 기간별 횡단면 회귀를 실행하고 시계열 평균으로
    팩터 리스크 프리미엄을 추정.

    Args:
        factor_matrix: (N_stocks x K_factors) z-score matrix
        forward_returns: {ticker: forward_return_pct}
        n_periods: 기간 수 (단일 기간이면 1)

    Returns:
        dict with: factor_premiums, t_stats, significant_factors,
        cross_sectional_r2
    """
    try:
        # 공통 종목 필터
        common = [t for t in factor_matrix.index if t in forward_returns]
        if len(common) < 10:
            return {
                "factor_premiums": {},
                "t_stats": {},
                "significant_factors": [],
                "cross_sectional_r2": 0.0,
                "n_stocks": len(common),
                "sufficient": False,
            }

        X = factor_matrix.loc[common].values
        y = np.array([forward_returns[t] for t in common])

        factors = list(factor_matrix.columns)
        k = X.shape[1]

        # OLS: y = X @ gamma + epsilon
        ones = np.ones((len(common), 1))
        X_full = np.hstack([ones, X])

        try:
            XtX_inv = np.linalg.inv(X_full.T @ X_full)
        except np.linalg.LinAlgError:
            XtX_inv = np.linalg.pinv(X_full.T @ X_full)

        gamma = XtX_inv @ (X_full.T @ y)
        residuals = y - X_full @ gamma

        # R-squared
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0
        r2 = max(0.0, min(1.0, r2))

        # Standard errors
        sigma2 = ss_res / max(len(common) - k - 1, 1)
        se = np.sqrt(np.abs(np.diag(XtX_inv) * sigma2))

        t_stats_arr = np.zeros(k + 1)
        for i in range(k + 1):
            t_stats_arr[i] = gamma[i] / se[i] if se[i] > 1e-15 else 0.0

        factor_premiums = {f: float(gamma[i + 1]) for i, f in enumerate(factors)}
        t_stats = {f: float(t_stats_arr[i + 1]) for i, f in enumerate(factors)}
        significant = [f for f in factors if abs(t_stats.get(f, 0)) > 2.0]

        return {
            "factor_premiums": {k: round(v, 6) for k, v in factor_premiums.items()},
            "t_stats": {k: round(v, 3) for k, v in t_stats.items()},
            "significant_factors": significant,
            "cross_sectional_r2": round(r2, 4),
            "intercept": round(float(gamma[0]), 6),
            "intercept_t": round(float(t_stats_arr[0]), 3),
            "n_stocks": len(common),
            "sufficient": True,
        }

    except Exception:
        logger.exception("Fama-MacBeth 회귀 실패")
        return {
            "factor_premiums": {}, "t_stats": {},
            "significant_factors": [], "cross_sectional_r2": 0.0,
            "n_stocks": 0, "sufficient": False,
        }


def compute_dynamic_factor_bonus(
    factor_profile: "MultiFactorProfile",
    factor_premiums: dict[str, float] | None = None,
) -> int:
    """팩터 프리미엄 기반 동적 보너스 점수 산출.

    Fama-MacBeth 결과를 활용하여 통계적으로 유의한
    팩터에 더 높은 가중치를 부여.

    Returns:
        int: -15 ~ +15 보너스 점수
    """
    try:
        if not factor_premiums:
            # 기존 방식: quintile 기반 고정 보너스
            q = factor_profile.quintile
            return {1: 10, 2: 5, 3: 0, 4: -5, 5: -10}.get(q, 0)

        # 유의한 팩터의 exposure × premium 합산
        premium_score = 0.0
        for exp in factor_profile.exposures:
            premium = factor_premiums.get(exp.factor_name, 0.0)
            premium_score += exp.z_score * premium

        # -15 ~ +15 범위로 매핑
        bonus = int(max(-15, min(15, premium_score * 100)))
        return bonus

    except Exception:
        logger.exception("Dynamic factor bonus 계산 실패")
        return 0


def score_stock_multifactor(
    ticker: str,
    factor_matrix: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> MultiFactorProfile:
    """Compute composite multi-factor score for a single stock.

    Args:
        ticker: stock ticker (must be in factor_matrix index).
        factor_matrix: from build_factor_matrix().
        weights: {factor_name: weight}. Defaults to equal 1/6.

    Returns:
        MultiFactorProfile with composite score (0~100), quintile, exposures.
    """
    if ticker not in factor_matrix.index:
        raise KeyError(f"Ticker {ticker} not in factor matrix")

    row = factor_matrix.loc[ticker]
    factors = list(factor_matrix.columns)

    if weights is None:
        w = {f: 1.0 / len(factors) for f in factors}
    else:
        w = weights
        total_w = sum(w.values())
        if total_w > 0:
            w = {k: v / total_w for k, v in w.items()}

    # Build exposures
    exposures: list[FactorExposure] = []
    weighted_sum = 0.0
    for f in factors:
        z = float(row[f])
        pct = _percentile_from_zscore(z)
        exposures.append(FactorExposure(
            factor_name=f,
            raw_value=z,
            z_score=z,
            percentile=pct,
        ))
        weighted_sum += w.get(f, 0.0) * z

    # Normalise composite to 0~100 via percentile of weighted z
    composite = _percentile_from_zscore(weighted_sum)
    composite = max(0.0, min(100.0, composite))

    quintile = _quintile_from_score(composite)

    # Determine dominant factor tilt
    best_factor = max(exposures, key=lambda e: e.z_score)
    factor_tilt = best_factor.factor_name

    # Try to get stock name from universe — not available here, use ticker
    name = ticker

    return MultiFactorProfile(
        ticker=ticker,
        name=name,
        exposures=exposures,
        composite_score=composite,
        quintile=quintile,
        factor_tilt=factor_tilt,
    )


def format_factor_profile(profile: MultiFactorProfile) -> str:
    """Format MultiFactorProfile as plain text for Telegram.

    No parse_mode, plain text + emoji.
    """
    lines: list[str] = []
    lines.append(f"{'='*28}")
    lines.append(f"  Multi-Factor  {profile.ticker}")
    lines.append(f"{'='*28}")
    lines.append("")
    lines.append(f"  Composite: {profile.composite_score:.1f}/100")
    lines.append(f"  Quintile: Q{profile.quintile}")
    lines.append(f"  Tilt: {profile.factor_tilt}")
    lines.append("")

    for exp in profile.exposures:
        bar_len = int(max(0, min(10, (exp.percentile / 100.0) * 10)))
        bar = "|" * bar_len + "." * (10 - bar_len)
        label = _FACTOR_EMOJI.get(exp.factor_name, exp.factor_name[:3].upper())
        lines.append(
            f"  {label:>3} [{bar}] {exp.percentile:5.1f}%  (z={exp.z_score:+.2f})"
        )

    lines.append("")

    # Quintile interpretation
    q_text = {
        1: "Very Strong",
        2: "Strong",
        3: "Neutral",
        4: "Weak",
        5: "Very Weak",
    }
    lines.append(f"  Rating: {q_text.get(profile.quintile, 'N/A')}")
    lines.append(f"{'='*28}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rolling Validation / Backtesting
# ---------------------------------------------------------------------------


def rolling_factor_backtest(
    factor_data_history: list[dict],
    forward_returns_history: list[dict],
    window: int = 60,
    step: int = 20,
) -> dict:
    """Roll through historical data to validate factor model performance.

    Args:
        factor_data_history: List of dicts, each with {ticker: {factor_name: value}} per period
        forward_returns_history: List of dicts, each with {ticker: forward_return} per period
        window: Rolling window size in periods
        step: Step size between windows

    Returns:
        Dict with rolling IC, turnover, and stability metrics.
    """
    if len(factor_data_history) < window + 1:
        return {"error": "insufficient_data", "min_required": window + 1}

    results = {
        "ic_by_factor": {},  # factor -> [ic values]
        "premium_by_factor": {},  # factor -> [premium values]
        "hit_rate_by_factor": {},  # factor -> hit rate (IC > 0)
        "ic_stability": {},  # factor -> IC mean / IC std
        "total_windows": 0,
    }

    factor_names: set[str] = set()
    for fd in factor_data_history[:3]:
        for ticker_data in fd.values():
            factor_names.update(ticker_data.keys())
            break

    for fname in factor_names:
        results["ic_by_factor"][fname] = []
        results["premium_by_factor"][fname] = []

    n_windows = 0
    for start in range(0, len(factor_data_history) - window, step):
        end = start + window
        if end >= len(forward_returns_history):
            break

        # Get factor data at end of window
        current_factors = factor_data_history[end - 1]
        # Get forward returns (next period)
        if end < len(forward_returns_history):
            fwd_rets = forward_returns_history[end]
        else:
            continue

        # Common tickers
        common = set(current_factors.keys()) & set(fwd_rets.keys())
        if len(common) < 10:
            continue

        common_sorted = sorted(common)
        n_windows += 1

        for fname in factor_names:
            factor_vals: list[float] = []
            ret_vals: list[float] = []
            for t in common_sorted:
                fv = current_factors[t].get(fname, 0)
                rv = fwd_rets[t]
                if fv is not None and rv is not None:
                    factor_vals.append(fv)
                    ret_vals.append(rv)

            if len(factor_vals) >= 10:
                # Rank IC (Spearman)
                try:
                    from scipy import stats as scipy_stats

                    ic, _ = scipy_stats.spearmanr(factor_vals, ret_vals)
                except ImportError:
                    # Manual Spearman rank correlation
                    def _rank(arr: list[float]) -> list[int]:
                        temp = sorted(range(len(arr)), key=lambda i: arr[i])
                        ranks = [0] * len(arr)
                        for i, idx in enumerate(temp):
                            ranks[idx] = i
                        return ranks

                    r1 = _rank(factor_vals)
                    r2 = _rank(ret_vals)
                    _n = len(r1)
                    d_sq = sum((a - b) ** 2 for a, b in zip(r1, r2))
                    ic = 1 - 6 * d_sq / (_n * (_n * _n - 1))

                try:
                    if not np.isnan(ic):
                        results["ic_by_factor"][fname].append(ic)
                except Exception:
                    pass

                # Simple premium: mean return of top quintile - bottom quintile
                sorted_pairs = sorted(
                    zip(factor_vals, ret_vals), key=lambda x: x[0]
                )
                n = len(sorted_pairs)
                q_size = max(1, n // 5)
                bottom_ret = np.mean([p[1] for p in sorted_pairs[:q_size]])
                top_ret = np.mean([p[1] for p in sorted_pairs[-q_size:]])
                results["premium_by_factor"][fname].append(top_ret - bottom_ret)

    results["total_windows"] = n_windows

    # Compute summary stats
    for fname in factor_names:
        ics = results["ic_by_factor"].get(fname, [])
        if ics:
            ic_mean = np.mean(ics)
            ic_std = float(np.std(ics)) if len(ics) > 1 else 1.0
            results["hit_rate_by_factor"][fname] = round(
                sum(1 for x in ics if x > 0) / len(ics) * 100, 1
            )
            results["ic_stability"][fname] = (
                round(ic_mean / ic_std, 3) if ic_std > 0.001 else 0
            )
        else:
            results["hit_rate_by_factor"][fname] = 0
            results["ic_stability"][fname] = 0

    return results


def format_factor_validation_report(results: dict) -> str:
    """Format rolling factor backtest results as readable report."""
    if "error" in results:
        return f"팩터 검증 불가: {results['error']}"

    lines = [
        "=== 멀티팩터 모델 롤링 검증 결과 ===",
        f"총 검증 윈도우: {results['total_windows']}개",
        "",
        "팩터별 IC (Information Coefficient):",
    ]

    for fname in sorted(results["ic_by_factor"].keys()):
        ics = results["ic_by_factor"][fname]
        if ics:
            ic_mean = np.mean(ics)
            hit = results["hit_rate_by_factor"].get(fname, 0)
            stability = results["ic_stability"].get(fname, 0)
            grade = (
                "A"
                if stability > 0.5
                else "B"
                if stability > 0.3
                else "C"
                if stability > 0.1
                else "D"
            )
            lines.append(
                f"  {fname}: IC평균={ic_mean:.3f}, 적중률={hit}%, "
                f"안정성={stability:.2f} [{grade}]"
            )

    lines.append("")
    lines.append("팩터별 롱숏 프리미엄:")
    for fname in sorted(results["premium_by_factor"].keys()):
        prems = results["premium_by_factor"][fname]
        if prems:
            prem_mean = np.mean(prems) * 100
            lines.append(f"  {fname}: 평균 {prem_mean:+.2f}%/기간")

    # Recommendations
    lines.append("")
    lines.append("권장사항:")
    strong = [f for f, s in results["ic_stability"].items() if s > 0.3]
    weak = [f for f, s in results["ic_stability"].items() if s < 0.1]
    if strong:
        lines.append(f"  강한 팩터: {', '.join(strong)} → 가중치 상향 권장")
    if weak:
        lines.append(f"  약한 팩터: {', '.join(weak)} → 가중치 하향 또는 제거 검토")

    return "\n".join(lines)
