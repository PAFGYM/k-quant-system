"""Factor Research module — decay analysis, turnover, dynamic weights, shock detection.

Extends the multi_factor model with advanced research tools:
  - Factor IC decay curves and half-life estimation
  - Portfolio turnover cost analysis
  - Dynamic factor weight optimisation (IC-weighted, momentum, regime)
  - Factor shock detection via z-score monitoring
  - Factor crowding measurement via HHI

Usage:
    report = generate_factor_report(factor_matrix, returns, portfolio_history)
    text = format_factor_research(report)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FactorDecay:
    """IC decay profile for a single factor across horizons."""

    factor_name: str
    half_life_days: int
    decay_curve: List[float]  # IC values at each horizon
    ic_by_horizon: Dict[int, float]  # horizon_days -> IC
    effective_horizon: int  # last horizon where IC > 0.03


@dataclass
class TurnoverAnalysis:
    """Portfolio turnover cost breakdown."""

    period: str
    avg_turnover_pct: float
    turnover_cost_pct: float
    net_alpha_after_cost: float
    break_even_turnover: float
    turnover_by_factor: Dict[str, float]


@dataclass
class DynamicWeight:
    """Dynamic weight for a single factor."""

    factor_name: str
    base_weight: float
    current_weight: float
    momentum_adj: float
    ic_adj: float
    regime_adj: float


@dataclass
class FactorShock:
    """Detected factor shock event."""

    factor_name: str
    shock_date: str
    z_score: float
    direction: str  # "long_squeeze" or "reversal"
    magnitude_pct: float
    affected_quintile: str  # "top" or "bottom"
    recommendation: str  # "reduce_exposure" / "hedge" / "monitor"


@dataclass
class FactorResearchReport:
    """Aggregated factor research report."""

    factor_decays: List[FactorDecay]
    turnover: TurnoverAnalysis
    dynamic_weights: List[DynamicWeight]
    shocks: List[FactorShock]
    regime_tilts: Dict[str, float]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_IC_THRESHOLD = 0.03  # minimum meaningful IC
_DEFAULT_ROUND_TRIP_COST = 0.005  # 0.5% round-trip
_REGIME_FACTOR_TILTS = {
    "bull": {"momentum": 1.3, "quality": 0.8, "value": 0.9, "size": 1.1,
             "volatility": 0.8, "investment": 0.9},
    "bear": {"momentum": 0.7, "quality": 1.4, "value": 1.1, "size": 0.8,
             "volatility": 1.3, "investment": 1.1},
    "neutral": {"momentum": 1.0, "quality": 1.0, "value": 1.0, "size": 1.0,
                "volatility": 1.0, "investment": 1.0},
}


def _safe_spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation, returning 0.0 on failure."""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    try:
        corr, _ = sp_stats.spearmanr(x, y)
        if np.isnan(corr):
            return 0.0
        return float(corr)
    except (ValueError, TypeError):
        return 0.0


def _normalise_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Normalise weights to sum to 1.0. If all zero, return equal weights."""
    total = sum(abs(v) for v in weights.values())
    if total < 1e-12:
        n = len(weights)
        return {k: 1.0 / n for k in weights} if n > 0 else {}
    return {k: v / total for k, v in weights.items()}


def _detect_regime(returns: pd.Series, lookback: int = 60) -> str:
    """Simple regime detection from recent returns.

    Bull: cumulative return > +5%
    Bear: cumulative return < -5%
    Neutral: otherwise
    """
    if len(returns) < lookback:
        return "neutral"
    recent = returns.iloc[-lookback:]
    cum_ret = float((1 + recent).prod() - 1)
    if cum_ret > 0.05:
        return "bull"
    elif cum_ret < -0.05:
        return "bear"
    return "neutral"


# ---------------------------------------------------------------------------
# 1. Factor Decay Analysis
# ---------------------------------------------------------------------------


def compute_factor_decay(
    factor_returns: pd.DataFrame,
    horizons: Optional[List[int]] = None,
) -> List[FactorDecay]:
    """Compute IC decay curves for each factor.

    For each factor column, calculates the Spearman rank correlation between
    factor scores at time t and forward returns over various horizons.

    Args:
        factor_returns: DataFrame where each column is a factor's cross-sectional
            scores and each row is a time period. Must have a 'forward_return'
            column or the last column is treated as forward returns.
            Alternatively: columns = factors, and rows contain concurrent
            factor scores with an implicit forward return derived from
            shifted values.
        horizons: list of forward-looking horizons in days.

    Returns:
        List of FactorDecay, one per factor.
    """
    if horizons is None:
        horizons = [1, 5, 10, 20, 40, 60]

    results: List[FactorDecay] = []

    # Determine return column vs factor columns
    if "forward_return" in factor_returns.columns:
        ret_col = "forward_return"
        factor_cols = [c for c in factor_returns.columns if c != ret_col]
    else:
        # Last column is the return series
        factor_cols = list(factor_returns.columns[:-1])
        ret_col = factor_returns.columns[-1]

    if len(factor_cols) == 0:
        logger.warning("compute_factor_decay: no factor columns found")
        return results

    returns_series = factor_returns[ret_col].values

    for fc in factor_cols:
        factor_vals = factor_returns[fc].values
        ic_by_horizon: Dict[int, float] = {}
        decay_curve: List[float] = []

        for h in horizons:
            if h >= len(factor_vals):
                ic_val = 0.0
            else:
                # IC = rank corr between factor score at t and return at t+h
                n = len(factor_vals) - h
                if n < 10:
                    ic_val = 0.0
                else:
                    scores = factor_vals[:n]
                    fwd_rets = returns_series[h: h + n]
                    ic_val = _safe_spearman(scores, fwd_rets)

            ic_by_horizon[h] = ic_val
            decay_curve.append(ic_val)

        # Estimate half-life via exponential decay fit
        half_life = _estimate_half_life(horizons, decay_curve)

        # Effective horizon: last horizon where IC > threshold
        effective_h = 0
        for h, ic in zip(horizons, decay_curve):
            if abs(ic) > _IC_THRESHOLD:
                effective_h = h

        results.append(FactorDecay(
            factor_name=fc,
            half_life_days=half_life,
            decay_curve=decay_curve,
            ic_by_horizon=ic_by_horizon,
            effective_horizon=effective_h,
        ))

    return results


def _estimate_half_life(horizons: List[int], ic_values: List[float]) -> int:
    """Estimate IC half-life from decay curve.

    Fits IC(h) = IC(0) * exp(-h / tau) and returns tau * ln(2).
    Falls back to a simple heuristic if fitting fails.
    """
    if not ic_values or abs(ic_values[0]) < 1e-10:
        return max(horizons) if horizons else 1

    ic0 = abs(ic_values[0])

    # Collect valid log-ratio points for linear regression
    valid_h: List[float] = []
    valid_log_ratio: List[float] = []

    for h, ic in zip(horizons, ic_values):
        ratio = abs(ic) / ic0 if ic0 > 1e-10 else 0.0
        if ratio > 1e-10 and h > 0:
            valid_h.append(float(h))
            valid_log_ratio.append(math.log(ratio))

    if len(valid_h) < 2:
        # Fallback: find where IC drops below half
        for h, ic in zip(horizons, ic_values):
            if abs(ic) < ic0 * 0.5:
                return max(1, h)
        return max(horizons) if horizons else 1

    # Linear regression: log(IC/IC0) = -h / tau
    # slope = -1/tau, so tau = -1/slope
    h_arr = np.array(valid_h)
    lr_arr = np.array(valid_log_ratio)
    slope, _, _, _, _ = sp_stats.linregress(h_arr, lr_arr)

    if slope >= 0 or abs(slope) < 1e-12:
        # IC not decaying — half-life is beyond our horizon
        return max(horizons) if horizons else 1

    tau = -1.0 / slope
    half_life = tau * math.log(2)
    return max(1, int(round(half_life)))


# ---------------------------------------------------------------------------
# 2. Turnover Analysis
# ---------------------------------------------------------------------------


def compute_turnover_analysis(
    portfolio_history: List[Dict],
    factor_matrix: pd.DataFrame,
    avg_round_trip_cost: float = _DEFAULT_ROUND_TRIP_COST,
) -> TurnoverAnalysis:
    """Analyse portfolio turnover and its cost impact.

    Args:
        portfolio_history: list of {"date": str, "holdings": {ticker: weight}}.
            Holdings weights should sum to ~1.0 per period.
        factor_matrix: factor scores DataFrame (ticker x factors) for
            attributing turnover to individual factors.
        avg_round_trip_cost: round-trip transaction cost as a fraction.

    Returns:
        TurnoverAnalysis with cost breakdown.
    """
    if len(portfolio_history) < 2:
        factor_names = list(factor_matrix.columns) if len(factor_matrix.columns) > 0 else []
        return TurnoverAnalysis(
            period="N/A",
            avg_turnover_pct=0.0,
            turnover_cost_pct=0.0,
            net_alpha_after_cost=0.0,
            break_even_turnover=0.0,
            turnover_by_factor={f: 0.0 for f in factor_names},
        )

    turnovers: List[float] = []

    for i in range(1, len(portfolio_history)):
        old_h = portfolio_history[i - 1]["holdings"]
        new_h = portfolio_history[i]["holdings"]
        all_tickers = set(old_h.keys()) | set(new_h.keys())

        turnover = sum(
            abs(new_h.get(t, 0.0) - old_h.get(t, 0.0)) for t in all_tickers
        ) / 2.0
        turnovers.append(turnover)

    avg_turnover = float(np.mean(turnovers)) if turnovers else 0.0
    avg_turnover_pct = avg_turnover * 100.0

    turnover_cost = avg_turnover * avg_round_trip_cost
    turnover_cost_pct = turnover_cost * 100.0

    # Estimate gross alpha from portfolio returns (simple: mean weight-return)
    gross_alpha = _estimate_gross_alpha(portfolio_history)
    net_alpha = gross_alpha - turnover_cost_pct

    # Break-even: how much turnover until alpha is consumed
    break_even = (gross_alpha / (avg_round_trip_cost * 100.0)
                  if avg_round_trip_cost > 1e-12 else 0.0)

    # Factor contribution to turnover
    turnover_by_factor = _attribute_turnover_to_factors(
        portfolio_history, factor_matrix,
    )

    period_start = portfolio_history[0].get("date", "?")
    period_end = portfolio_history[-1].get("date", "?")

    return TurnoverAnalysis(
        period=f"{period_start} ~ {period_end}",
        avg_turnover_pct=avg_turnover_pct,
        turnover_cost_pct=turnover_cost_pct,
        net_alpha_after_cost=net_alpha,
        break_even_turnover=break_even,
        turnover_by_factor=turnover_by_factor,
    )


def _estimate_gross_alpha(portfolio_history: List[Dict]) -> float:
    """Rough gross alpha estimate from portfolio weight changes (annualised %)."""
    if len(portfolio_history) < 2:
        return 0.0

    # Simple heuristic: assume 10% annualised gross alpha as default
    # In production this would come from actual return attribution
    n_periods = len(portfolio_history) - 1
    return 10.0 / max(n_periods, 1) * n_periods  # placeholder: ~10% annualised


def _attribute_turnover_to_factors(
    portfolio_history: List[Dict],
    factor_matrix: pd.DataFrame,
) -> Dict[str, float]:
    """Attribute turnover to individual factors.

    For each rebalance, measures how much of the weight change aligns
    with changes in each factor's ranking.
    """
    factor_names = list(factor_matrix.columns) if len(factor_matrix.columns) > 0 else []
    if not factor_names or len(portfolio_history) < 2:
        return {f: 0.0 for f in factor_names}

    # Simple attribution: correlation of |delta_w| with |factor_score|
    contrib: Dict[str, List[float]] = {f: [] for f in factor_names}

    for i in range(1, len(portfolio_history)):
        old_h = portfolio_history[i - 1]["holdings"]
        new_h = portfolio_history[i]["holdings"]
        all_tickers = list(set(old_h.keys()) | set(new_h.keys()))

        delta_w = np.array([
            abs(new_h.get(t, 0.0) - old_h.get(t, 0.0)) for t in all_tickers
        ])
        total_delta = delta_w.sum()
        if total_delta < 1e-12:
            continue

        for f in factor_names:
            if f not in factor_matrix.columns:
                contrib[f].append(0.0)
                continue
            f_scores = np.array([
                abs(factor_matrix.at[t, f])
                if t in factor_matrix.index else 0.0
                for t in all_tickers
            ])
            # Weighted contribution: how much of turnover is explained
            # by high-absolute-score tickers
            if f_scores.sum() < 1e-12:
                contrib[f].append(0.0)
            else:
                weight_aligned = float(np.dot(delta_w, f_scores) / total_delta)
                contrib[f].append(weight_aligned)

    result: Dict[str, float] = {}
    for f in factor_names:
        vals = contrib[f]
        result[f] = float(np.mean(vals)) if vals else 0.0

    # Normalise to sum to 100%
    total = sum(result.values())
    if total > 1e-12:
        result = {k: (v / total) * 100.0 for k, v in result.items()}

    return result


# ---------------------------------------------------------------------------
# 3. Dynamic Factor Weight Optimisation
# ---------------------------------------------------------------------------


def optimize_factor_weights(
    factor_matrix: pd.DataFrame,
    returns: pd.Series,
    method: str = "ic_weighted",
    lookback: int = 60,
) -> List[DynamicWeight]:
    """Optimise factor weights dynamically.

    Args:
        factor_matrix: DataFrame with factor scores (rows=dates, cols=factors).
        returns: forward return series aligned with factor_matrix index.
        method: "ic_weighted", "momentum", or "regime".
        lookback: rolling window for IC / momentum calculation.

    Returns:
        List of DynamicWeight for each factor.
    """
    factor_names = list(factor_matrix.columns)
    n_factors = len(factor_names)

    if n_factors == 0:
        return []

    base_weight = 1.0 / n_factors

    # IC adjustment: rolling Spearman correlation with returns
    ic_adj_map = _compute_ic_adjustments(factor_matrix, returns, lookback)

    # Momentum adjustment: recent factor return momentum
    momentum_adj_map = _compute_momentum_adjustments(factor_matrix, lookback)

    # Regime adjustment
    regime = _detect_regime(returns, lookback)
    regime_tilts = _REGIME_FACTOR_TILTS.get(regime, _REGIME_FACTOR_TILTS["neutral"])

    results: List[DynamicWeight] = []

    for f in factor_names:
        ic_a = ic_adj_map.get(f, 1.0)
        mom_a = momentum_adj_map.get(f, 1.0)
        reg_a = regime_tilts.get(f, 1.0)

        if method == "ic_weighted":
            raw_weight = base_weight * ic_a
        elif method == "momentum":
            raw_weight = base_weight * mom_a
        elif method == "regime":
            raw_weight = base_weight * ic_a * mom_a * reg_a
        else:
            raw_weight = base_weight * ic_a * mom_a * reg_a

        results.append(DynamicWeight(
            factor_name=f,
            base_weight=base_weight,
            current_weight=raw_weight,  # will be normalised below
            momentum_adj=mom_a,
            ic_adj=ic_a,
            regime_adj=reg_a,
        ))

    # Normalise current_weight to sum to 1.0
    total_w = sum(max(dw.current_weight, 0.0) for dw in results)
    if total_w > 1e-12:
        for dw in results:
            dw.current_weight = max(dw.current_weight, 0.0) / total_w
    else:
        for dw in results:
            dw.current_weight = base_weight

    return results


def _compute_ic_adjustments(
    factor_matrix: pd.DataFrame,
    returns: pd.Series,
    lookback: int,
) -> Dict[str, float]:
    """Compute IC-based weight adjustments.

    Higher recent IC -> higher adjustment multiplier.
    """
    factor_names = list(factor_matrix.columns)
    ic_map: Dict[str, float] = {}

    n = min(len(factor_matrix), len(returns))
    if n < 10:
        return {f: 1.0 for f in factor_names}

    start = max(0, n - lookback)
    ret_slice = returns.values[start:n]

    for f in factor_names:
        f_slice = factor_matrix[f].values[start:n]
        ic = _safe_spearman(f_slice, ret_slice)
        ic_map[f] = ic

    # Convert IC to adjustment: centre at 1.0, scale by relative IC
    if not ic_map:
        return {f: 1.0 for f in factor_names}

    mean_ic = np.mean(list(ic_map.values()))
    std_ic = np.std(list(ic_map.values()))

    result: Dict[str, float] = {}
    for f in factor_names:
        if std_ic > 1e-10:
            z = (ic_map[f] - mean_ic) / std_ic
            # Sigmoid-like mapping: 1 + 0.3 * tanh(z)
            result[f] = 1.0 + 0.3 * math.tanh(z)
        else:
            result[f] = 1.0

    return result


def _compute_momentum_adjustments(
    factor_matrix: pd.DataFrame,
    lookback: int,
) -> Dict[str, float]:
    """Compute momentum-based weight adjustments.

    Factors with positive recent drift get upweighted.
    """
    factor_names = list(factor_matrix.columns)
    n = len(factor_matrix)

    if n < 10:
        return {f: 1.0 for f in factor_names}

    start = max(0, n - lookback)
    mom_map: Dict[str, float] = {}

    for f in factor_names:
        f_vals = factor_matrix[f].values[start:]
        if len(f_vals) < 5:
            mom_map[f] = 0.0
            continue

        # Simple: mean of second half minus mean of first half
        mid = len(f_vals) // 2
        first_half = float(np.mean(f_vals[:mid]))
        second_half = float(np.mean(f_vals[mid:]))
        mom_map[f] = second_half - first_half

    mean_mom = np.mean(list(mom_map.values()))
    std_mom = np.std(list(mom_map.values()))

    result: Dict[str, float] = {}
    for f in factor_names:
        if std_mom > 1e-10:
            z = (mom_map[f] - mean_mom) / std_mom
            result[f] = 1.0 + 0.2 * math.tanh(z)
        else:
            result[f] = 1.0

    return result


# ---------------------------------------------------------------------------
# 4. Factor Shock Detection
# ---------------------------------------------------------------------------


def detect_factor_shocks(
    factor_returns: pd.DataFrame,
    lookback: int = 252,
    threshold: float = 2.5,
) -> List[FactorShock]:
    """Detect factor shocks via z-score monitoring.

    For each factor column, computes z-score of the most recent return
    relative to the lookback window and flags shocks exceeding the threshold.

    Args:
        factor_returns: DataFrame of factor returns (rows=dates, cols=factors).
            Index should be datetime-like for date extraction.
        lookback: historical window for mean/std.
        threshold: z-score threshold for shock detection.

    Returns:
        List of FactorShock for any factors with |z| > threshold.
    """
    shocks: List[FactorShock] = []

    if len(factor_returns) < 3:
        return shocks

    for col in factor_returns.columns:
        vals = factor_returns[col].dropna().values
        if len(vals) < lookback + 1:
            # Use all available data if shorter than lookback
            if len(vals) < 10:
                continue
            hist = vals[:-1]
            latest = vals[-1]
        else:
            hist = vals[-(lookback + 1):-1]
            latest = vals[-1]

        mu = float(np.mean(hist))
        sigma = float(np.std(hist, ddof=1))

        if sigma < 1e-15:
            continue

        z = (latest - mu) / sigma

        if abs(z) > threshold:
            # Determine direction
            if z < -threshold:
                direction = "long_squeeze"
                affected = "top"
            else:
                direction = "reversal"
                affected = "bottom"

            # Determine recommendation based on severity
            abs_z = abs(z)
            if abs_z > 4.0:
                recommendation = "reduce_exposure"
            elif abs_z > 3.0:
                recommendation = "hedge"
            else:
                recommendation = "monitor"

            # Extract shock date
            if hasattr(factor_returns.index, 'strftime'):
                try:
                    shock_date = str(factor_returns.index[-1].strftime("%Y-%m-%d"))
                except (AttributeError, IndexError):
                    shock_date = str(factor_returns.index[-1])
            else:
                shock_date = str(factor_returns.index[-1])

            magnitude_pct = float(latest * 100.0)

            shocks.append(FactorShock(
                factor_name=col,
                shock_date=shock_date,
                z_score=round(z, 2),
                direction=direction,
                magnitude_pct=round(magnitude_pct, 2),
                affected_quintile=affected,
                recommendation=recommendation,
            ))

    return shocks


# ---------------------------------------------------------------------------
# 5. Factor Crowding
# ---------------------------------------------------------------------------


def compute_factor_crowding(
    factor_scores: pd.DataFrame,
    holdings_universe: List[Dict],
) -> Dict[str, float]:
    """Measure factor crowding via HHI of quintile holdings.

    Higher HHI = more crowded (concentrated in fewer quintiles).

    Args:
        factor_scores: DataFrame (ticker x factors) of cross-sectional scores.
        holdings_universe: list of {"ticker": str, "weight": float} representing
            aggregate market holdings.

    Returns:
        {factor_name: crowding_score} where 0=no crowding, 1=max crowding.
    """
    if factor_scores.empty or not holdings_universe:
        return {col: 0.0 for col in factor_scores.columns}

    # Build weight map
    weight_map: Dict[str, float] = {}
    for h in holdings_universe:
        t = h.get("ticker", "")
        w = h.get("weight", 0.0)
        weight_map[t] = w

    result: Dict[str, float] = {}

    for col in factor_scores.columns:
        scores = factor_scores[col].dropna()
        if len(scores) < 5:
            result[col] = 0.0
            continue

        # Assign quintiles (1=top, 5=bottom)
        quintile_labels = pd.qcut(
            scores.rank(method="first"), q=5, labels=[1, 2, 3, 4, 5],
        )

        # Sum weights per quintile
        quintile_weights = [0.0] * 5
        for ticker, q_label in quintile_labels.items():
            w = weight_map.get(ticker, 0.0)
            idx = int(q_label) - 1
            quintile_weights[idx] += w

        total_w = sum(quintile_weights)
        if total_w < 1e-12:
            result[col] = 0.0
            continue

        # Normalise and compute HHI
        shares = [qw / total_w for qw in quintile_weights]
        hhi = sum(s ** 2 for s in shares)

        # Normalise HHI: min = 1/5 = 0.2 (uniform), max = 1.0 (all in one)
        # Map [0.2, 1.0] -> [0.0, 1.0]
        crowding = (hhi - 0.2) / 0.8

        result[col] = round(max(0.0, min(1.0, crowding)), 4)

    return result


# ---------------------------------------------------------------------------
# 6. Report Generation
# ---------------------------------------------------------------------------


def generate_factor_report(
    factor_matrix: pd.DataFrame,
    returns: pd.Series,
    portfolio_history: List[Dict],
    regime: str = "neutral",
) -> FactorResearchReport:
    """Generate comprehensive factor research report.

    Combines decay analysis, turnover, dynamic weights, and shock detection.

    Args:
        factor_matrix: time-series of factor scores (rows=dates, cols=factors).
            For decay analysis, should also include a 'forward_return' column
            or the returns Series is appended.
        returns: return series aligned with factor_matrix.
        portfolio_history: list of {"date": str, "holdings": {ticker: weight}}.
        regime: market regime override ("bull", "bear", "neutral").
            If "neutral" (default), auto-detected from returns.

    Returns:
        FactorResearchReport.
    """
    # 1. Factor decay
    decay_input = factor_matrix.copy()
    if "forward_return" not in decay_input.columns:
        aligned_ret = returns.reindex(decay_input.index).fillna(0.0)
        decay_input["forward_return"] = aligned_ret.values[:len(decay_input)]
    factor_decays = compute_factor_decay(decay_input)

    # 2. Turnover
    # Build a simple factor matrix from holdings for turnover attribution
    factor_cols_only = factor_matrix.drop(
        columns=["forward_return"], errors="ignore",
    )
    turnover = compute_turnover_analysis(portfolio_history, factor_cols_only)

    # 3. Dynamic weights
    factor_cols_only2 = factor_matrix.drop(
        columns=["forward_return"], errors="ignore",
    )
    dynamic_weights = optimize_factor_weights(
        factor_cols_only2, returns, method="regime",
    )

    # 4. Factor shocks (use factor_matrix as factor returns proxy)
    factor_cols_only3 = factor_matrix.drop(
        columns=["forward_return"], errors="ignore",
    )
    shocks = detect_factor_shocks(factor_cols_only3)

    # 5. Regime tilts
    detected_regime = regime
    if regime == "neutral":
        detected_regime = _detect_regime(returns)
    regime_tilts = dict(_REGIME_FACTOR_TILTS.get(detected_regime,
                                                  _REGIME_FACTOR_TILTS["neutral"]))

    return FactorResearchReport(
        factor_decays=factor_decays,
        turnover=turnover,
        dynamic_weights=dynamic_weights,
        shocks=shocks,
        regime_tilts=regime_tilts,
    )


# ---------------------------------------------------------------------------
# 7. Telegram Formatting
# ---------------------------------------------------------------------------


def format_factor_research(report: FactorResearchReport) -> str:
    """Format FactorResearchReport as plain text for Telegram.

    No parse_mode, plain text + emoji.
    """
    lines: List[str] = []
    lines.append(f"{'='*30}")
    lines.append("  Factor Research Report")
    lines.append(f"{'='*30}")
    lines.append("")

    # --- Decay ---
    lines.append("  IC Decay Analysis")
    lines.append(f"  {'-'*26}")
    for d in report.factor_decays:
        hl_str = f"{d.half_life_days}d" if d.half_life_days < 999 else "999+d"
        lines.append(
            f"  {d.factor_name:>12}  HL={hl_str:>5}  "
            f"EH={d.effective_horizon:>3}d"
        )
    lines.append("")

    # --- Turnover ---
    t = report.turnover
    lines.append("  Turnover Analysis")
    lines.append(f"  {'-'*26}")
    lines.append(f"  Period: {t.period}")
    lines.append(f"  Avg Turnover: {t.avg_turnover_pct:.1f}%")
    lines.append(f"  Cost: {t.turnover_cost_pct:.2f}%")
    lines.append(f"  Net Alpha: {t.net_alpha_after_cost:.2f}%")
    lines.append(f"  Break-even TO: {t.break_even_turnover:.1f}x")
    if t.turnover_by_factor:
        lines.append("  Factor Contribution:")
        for f, pct in sorted(t.turnover_by_factor.items(),
                             key=lambda x: -x[1]):
            lines.append(f"    {f:>12}: {pct:.1f}%")
    lines.append("")

    # --- Dynamic Weights ---
    lines.append("  Dynamic Weights")
    lines.append(f"  {'-'*26}")
    for dw in sorted(report.dynamic_weights,
                     key=lambda x: -x.current_weight):
        bar_len = int(max(0, min(10, dw.current_weight * 10 * len(report.dynamic_weights))))
        bar = "|" * bar_len + "." * (10 - bar_len)
        lines.append(
            f"  {dw.factor_name:>12} [{bar}] {dw.current_weight:.1%}"
        )
    lines.append("")

    # --- Shocks ---
    if report.shocks:
        lines.append("  Factor Shocks")
        lines.append(f"  {'-'*26}")
        for s in report.shocks:
            emoji = "\u26a0\ufe0f" if s.recommendation == "reduce_exposure" else "\u26a1"
            lines.append(
                f"  {emoji} {s.factor_name}: z={s.z_score:+.1f} "
                f"({s.direction}) -> {s.recommendation}"
            )
        lines.append("")

    # --- Regime Tilts ---
    lines.append("  Regime Tilts")
    lines.append(f"  {'-'*26}")
    for f, tilt in sorted(report.regime_tilts.items()):
        arrow = "\u2191" if tilt > 1.05 else ("\u2193" if tilt < 0.95 else "\u2192")
        lines.append(f"  {f:>12} {arrow} {tilt:.2f}x")

    lines.append("")
    lines.append(f"{'='*30}")

    return "\n".join(lines)
