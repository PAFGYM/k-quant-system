"""Cross-asset correlation analysis module.

Computes rolling correlations, tail dependencies, regime-conditional
correlations, and diversification ratios across a global asset universe.
Generates actionable signals when correlation structure shifts.

All functions are pure computation — no external API calls at runtime.

Usage:
    from kstock.signal.cross_asset import (
        compute_asset_correlations,
        compute_tail_dependency,
        compute_regime_correlations,
        detect_cross_asset_signals,
        compute_diversification_ratio,
        generate_cross_asset_report,
        format_cross_asset_report,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global asset universe
# ---------------------------------------------------------------------------

GLOBAL_ASSETS: Dict[str, Dict[str, str]] = {
    "KOSPI": {"type": "equity", "region": "KR"},
    "KOSDAQ": {"type": "equity", "region": "KR"},
    "SPX": {"type": "equity", "region": "US"},
    "NASDAQ": {"type": "equity", "region": "US"},
    "US10Y": {"type": "bond", "region": "US"},
    "KR10Y": {"type": "bond", "region": "KR"},
    "USDKRW": {"type": "fx", "region": "KR"},
    "DXY": {"type": "fx", "region": "US"},
    "GOLD": {"type": "commodity", "region": "global"},
    "OIL": {"type": "commodity", "region": "global"},
    "VIX": {"type": "volatility", "region": "US"},
    "BTC": {"type": "crypto", "region": "global"},
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AssetCorrelation:
    """Rolling correlation between two assets across multiple windows."""

    asset_a: str
    asset_b: str
    correlation_30d: float
    correlation_90d: float
    correlation_252d: float
    rolling_change: float  # corr_30d - corr_90d
    regime: str  # "strengthening" / "weakening" / "stable"


@dataclass
class TailDependency:
    """Tail dependency statistics for an asset pair."""

    asset_a: str
    asset_b: str
    lower_tail: float  # P(B < q5 | A < q5)
    upper_tail: float  # P(B > q95 | A > q95)
    asymmetry: float  # lower_tail - upper_tail
    joint_crash_prob: float  # P(both < q5)


@dataclass
class RegimeCorrelation:
    """Correlation statistics within a market regime."""

    regime: str
    avg_correlation: float
    correlation_matrix: dict
    n_observations: int
    description: str


@dataclass
class CrossAssetSignal:
    """Actionable signal from cross-asset analysis."""

    signal_type: str
    description: str
    strength: float  # 0..1
    affected_assets: list
    recommendation: str


@dataclass
class CrossAssetReport:
    """Full cross-asset correlation report."""

    correlations: list  # List[AssetCorrelation]
    tail_deps: list  # List[TailDependency]
    regime_correlations: list  # List[RegimeCorrelation]
    signals: list  # List[CrossAssetSignal]
    diversification_ratio: float


# ---------------------------------------------------------------------------
# 1. Rolling correlations
# ---------------------------------------------------------------------------

def _rolling_corr_last(
    s_a: pd.Series,
    s_b: pd.Series,
    window: int,
) -> float:
    """Return the most recent rolling Pearson correlation for *window* days.

    Returns ``np.nan`` when insufficient overlapping data exists.
    """
    aligned = pd.DataFrame({"a": s_a, "b": s_b}).dropna()
    if len(aligned) < window:
        return np.nan
    rolling = aligned["a"].rolling(window).corr(aligned["b"])
    last_val = rolling.iloc[-1]
    return float(last_val) if not np.isnan(last_val) else np.nan


def _classify_regime(change: float) -> str:
    """Classify correlation change into a regime label."""
    if change > 0.1:
        return "strengthening"
    if change < -0.1:
        return "weakening"
    return "stable"


def compute_asset_correlations(
    returns_map: Dict[str, pd.Series],
    windows: Optional[List[int]] = None,
) -> List[AssetCorrelation]:
    """Compute rolling Pearson correlations for every asset pair.

    Parameters
    ----------
    returns_map : dict[str, pd.Series]
        Asset name -> daily return series.
    windows : list[int], optional
        Rolling windows in trading days.  Defaults to ``[30, 90, 252]``.

    Returns
    -------
    list[AssetCorrelation]
    """
    if windows is None:
        windows = [30, 90, 252]
    if len(returns_map) < 2:
        return []

    results: List[AssetCorrelation] = []
    assets = sorted(returns_map.keys())

    for a, b in combinations(assets, 2):
        corrs: Dict[int, float] = {}
        for w in windows:
            corrs[w] = _rolling_corr_last(returns_map[a], returns_map[b], w)

        c30 = corrs.get(30, np.nan)
        c90 = corrs.get(90, np.nan)
        c252 = corrs.get(252, np.nan)

        if np.isnan(c30) and np.isnan(c90):
            rolling_change = 0.0
        elif np.isnan(c30) or np.isnan(c90):
            rolling_change = 0.0
        else:
            rolling_change = c30 - c90

        regime = _classify_regime(rolling_change)

        results.append(
            AssetCorrelation(
                asset_a=a,
                asset_b=b,
                correlation_30d=c30 if not np.isnan(c30) else 0.0,
                correlation_90d=c90 if not np.isnan(c90) else 0.0,
                correlation_252d=c252 if not np.isnan(c252) else 0.0,
                rolling_change=round(rolling_change, 4),
                regime=regime,
            )
        )
    return results


# ---------------------------------------------------------------------------
# 2. Tail dependency
# ---------------------------------------------------------------------------

def compute_tail_dependency(
    returns_a: pd.Series,
    returns_b: pd.Series,
    threshold_pct: int = 5,
    asset_a: str = "A",
    asset_b: str = "B",
) -> TailDependency:
    """Compute empirical tail dependency between two return series.

    Parameters
    ----------
    returns_a, returns_b : pd.Series
        Daily return series (same index preferred).
    threshold_pct : int
        Percentile for tail definition (default 5 = 5th / 95th).
    asset_a, asset_b : str
        Label names for the result dataclass.

    Returns
    -------
    TailDependency
    """
    df = pd.DataFrame({"a": returns_a, "b": returns_b}).dropna()
    n = len(df)
    if n < 20:
        logger.warning(
            "Insufficient data (%d rows) for tail dependency", n,
        )
        return TailDependency(
            asset_a=asset_a, asset_b=asset_b,
            lower_tail=0.0, upper_tail=0.0,
            asymmetry=0.0, joint_crash_prob=0.0,
        )

    lo_q = threshold_pct / 100.0
    hi_q = 1.0 - lo_q

    a_lo = float(np.nanquantile(df["a"], lo_q))
    b_lo = float(np.nanquantile(df["b"], lo_q))
    a_hi = float(np.nanquantile(df["a"], hi_q))
    b_hi = float(np.nanquantile(df["b"], hi_q))

    # Lower tail: P(B < q5 | A < q5)
    mask_a_lo = df["a"] <= a_lo
    n_a_lo = int(mask_a_lo.sum())
    if n_a_lo > 0:
        lower_tail = float((df.loc[mask_a_lo, "b"] <= b_lo).sum()) / n_a_lo
    else:
        lower_tail = 0.0

    # Upper tail: P(B > q95 | A > q95)
    mask_a_hi = df["a"] >= a_hi
    n_a_hi = int(mask_a_hi.sum())
    if n_a_hi > 0:
        upper_tail = float((df.loc[mask_a_hi, "b"] >= b_hi).sum()) / n_a_hi
    else:
        upper_tail = 0.0

    # Joint crash probability
    joint_crash_prob = float(((df["a"] <= a_lo) & (df["b"] <= b_lo)).sum()) / n

    asymmetry = round(lower_tail - upper_tail, 4)

    return TailDependency(
        asset_a=asset_a,
        asset_b=asset_b,
        lower_tail=round(lower_tail, 4),
        upper_tail=round(upper_tail, 4),
        asymmetry=asymmetry,
        joint_crash_prob=round(joint_crash_prob, 4),
    )


# ---------------------------------------------------------------------------
# 3. Regime-conditional correlations
# ---------------------------------------------------------------------------

def compute_regime_correlations(
    returns_map: Dict[str, pd.Series],
    regime_labels: List[str],
) -> List[RegimeCorrelation]:
    """Compute correlation matrices separately for each market regime.

    Parameters
    ----------
    returns_map : dict[str, pd.Series]
        Asset name -> daily return series.  Series must share a
        DatetimeIndex or an integer index of equal length.
    regime_labels : list[str]
        Daily regime assignment aligned to the return series index.
        Typical values: ``"bull"``, ``"bear"``, ``"neutral"``.

    Returns
    -------
    list[RegimeCorrelation]
    """
    if len(returns_map) < 2 or not regime_labels:
        return []

    df = pd.DataFrame(returns_map)
    if len(regime_labels) != len(df):
        logger.warning(
            "regime_labels length (%d) != returns length (%d); truncating",
            len(regime_labels),
            len(df),
        )
        min_len = min(len(regime_labels), len(df))
        regime_labels = regime_labels[:min_len]
        df = df.iloc[:min_len]

    regime_series = pd.Series(regime_labels, index=df.index)
    unique_regimes = sorted(set(regime_labels))

    descriptions = {
        "bull": "강세장 — 위험자산 간 상관관계 하락 경향",
        "bear": "약세장 — 위험자산 간 상관관계 상승 (동반 하락)",
        "neutral": "중립장 — 자산별 고유 움직임 유지",
    }

    results: List[RegimeCorrelation] = []
    for regime in unique_regimes:
        subset = df[regime_series == regime].dropna()
        n_obs = len(subset)
        if n_obs < 5:
            continue

        corr_matrix = subset.corr()

        # Flatten correlation matrix to dict for serialization
        corr_dict: Dict[str, Dict[str, float]] = {}
        for col in corr_matrix.columns:
            corr_dict[col] = {
                row: round(float(corr_matrix.loc[row, col]), 4)
                for row in corr_matrix.index
            }

        # Average off-diagonal correlation
        mask = np.ones(corr_matrix.shape, dtype=bool)
        np.fill_diagonal(mask, False)
        avg_corr = float(np.nanmean(corr_matrix.values[mask]))

        results.append(
            RegimeCorrelation(
                regime=regime,
                avg_correlation=round(avg_corr, 4),
                correlation_matrix=corr_dict,
                n_observations=n_obs,
                description=descriptions.get(regime, f"{regime} 레짐"),
            )
        )
    return results


# ---------------------------------------------------------------------------
# 4. Signal detection
# ---------------------------------------------------------------------------

def _find_corr(
    correlations: List[AssetCorrelation],
    a: str,
    b: str,
) -> Optional[AssetCorrelation]:
    """Look up the correlation entry for a pair (order-agnostic)."""
    for c in correlations:
        if {c.asset_a, c.asset_b} == {a, b}:
            return c
    return None


def _find_tail(
    tail_deps: List[TailDependency],
    a: str,
    b: str,
) -> Optional[TailDependency]:
    """Look up the tail dependency for a pair (order-agnostic)."""
    for t in tail_deps:
        if {t.asset_a, t.asset_b} == {a, b}:
            return t
    return None


def detect_cross_asset_signals(
    correlations: List[AssetCorrelation],
    tail_deps: List[TailDependency],
    current_regime: str = "neutral",
) -> List[CrossAssetSignal]:
    """Detect actionable signals from cross-asset analysis.

    Signal types
    -------------
    - **correlation_breakdown** : |rolling_change| > 0.3
    - **contagion_risk** : lower_tail > 0.4
    - **safe_haven_activation** : equity-gold inverse correlation strengthening
    - **risk_on_signal** : VIX falling + equity rising + KRW strengthening
    - **divergence_opportunity** : same-region equity pair abnormal gap

    Parameters
    ----------
    correlations : list[AssetCorrelation]
    tail_deps : list[TailDependency]
    current_regime : str
        ``"bull"`` / ``"bear"`` / ``"neutral"``

    Returns
    -------
    list[CrossAssetSignal]
    """
    signals: List[CrossAssetSignal] = []

    # ---- correlation_breakdown ----
    for c in correlations:
        if abs(c.rolling_change) > 0.3:
            direction = "급등" if c.rolling_change > 0 else "급락"
            signals.append(
                CrossAssetSignal(
                    signal_type="correlation_breakdown",
                    description=(
                        f"{c.asset_a}-{c.asset_b} 상관관계 {direction} "
                        f"(30d-90d 변화: {c.rolling_change:+.2f})"
                    ),
                    strength=min(abs(c.rolling_change), 1.0),
                    affected_assets=[c.asset_a, c.asset_b],
                    recommendation=(
                        "상관관계 구조 변화 — 기존 헤지 포지션 재점검 필요"
                    ),
                )
            )

    # ---- contagion_risk ----
    for t in tail_deps:
        if t.lower_tail > 0.4:
            signals.append(
                CrossAssetSignal(
                    signal_type="contagion_risk",
                    description=(
                        f"{t.asset_a}-{t.asset_b} 동시 하락 확률 높음 "
                        f"(lower tail: {t.lower_tail:.1%})"
                    ),
                    strength=min(t.lower_tail, 1.0),
                    affected_assets=[t.asset_a, t.asset_b],
                    recommendation="위험 전이 가능성 — 분산 효과 제한적",
                )
            )

    # ---- safe_haven_activation ----
    equity_names = [
        k for k, v in GLOBAL_ASSETS.items() if v["type"] == "equity"
    ]
    for eq in equity_names:
        gold_corr = _find_corr(correlations, eq, "GOLD")
        if gold_corr is None:
            continue
        # Strengthening inverse: 30d corr < -0.2 and weakening (becoming more negative)
        if gold_corr.correlation_30d < -0.2 and gold_corr.rolling_change < -0.1:
            signals.append(
                CrossAssetSignal(
                    signal_type="safe_haven_activation",
                    description=(
                        f"{eq} 하락 + GOLD 상승 역상관 강화 "
                        f"(30d corr: {gold_corr.correlation_30d:.2f})"
                    ),
                    strength=min(abs(gold_corr.correlation_30d), 1.0),
                    affected_assets=[eq, "GOLD"],
                    recommendation="안전자산 선호 강화 — 금 비중 확대 검토",
                )
            )

    # ---- risk_on_signal ----
    # VIX-equity inverse corr weakening (VIX dropping, stocks up)
    # + USDKRW declining (원화 강세)
    for eq in ["KOSPI", "SPX"]:
        vix_corr = _find_corr(correlations, eq, "VIX")
        fx_corr = _find_corr(correlations, eq, "USDKRW")
        if vix_corr is None:
            continue
        # VIX-equity: normally negative; risk-on when that stays negative
        # and USDKRW-equity is becoming more negative (원화 강세 = risk on)
        if (
            vix_corr.correlation_30d < -0.3
            and fx_corr is not None
            and fx_corr.rolling_change < -0.05
        ):
            signals.append(
                CrossAssetSignal(
                    signal_type="risk_on_signal",
                    description=(
                        f"VIX 하락 + {eq} 상승 + 원화 강세 동시 진행"
                    ),
                    strength=min(abs(vix_corr.correlation_30d) * 0.8, 1.0),
                    affected_assets=[eq, "VIX", "USDKRW"],
                    recommendation="리스크온 환경 — 위험자산 비중 유지/확대",
                )
            )

    # ---- divergence_opportunity ----
    same_region_pairs = [
        ("KOSPI", "KOSDAQ"),
        ("SPX", "NASDAQ"),
    ]
    for a, b in same_region_pairs:
        corr = _find_corr(correlations, a, b)
        if corr is None:
            continue
        # Normally highly correlated (>0.8); divergence when 30d drops
        if corr.correlation_30d < 0.6 and corr.correlation_90d > 0.7:
            gap = corr.correlation_90d - corr.correlation_30d
            signals.append(
                CrossAssetSignal(
                    signal_type="divergence_opportunity",
                    description=(
                        f"{a}-{b} 비정상 괴리 "
                        f"(30d: {corr.correlation_30d:.2f} vs "
                        f"90d: {corr.correlation_90d:.2f})"
                    ),
                    strength=min(gap, 1.0),
                    affected_assets=[a, b],
                    recommendation=(
                        "동일 지역 자산 괴리 — 수렴 트레이딩 기회 탐색"
                    ),
                )
            )

    # Amplify strength in bear regime
    if current_regime == "bear":
        for s in signals:
            if s.signal_type in ("contagion_risk", "safe_haven_activation"):
                s.strength = min(s.strength * 1.2, 1.0)

    return signals


# ---------------------------------------------------------------------------
# 5. Diversification ratio
# ---------------------------------------------------------------------------

def compute_diversification_ratio(
    returns_map: Dict[str, pd.Series],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Compute the diversification ratio of a weighted portfolio.

    DR = sum(w_i * sigma_i) / sigma_portfolio

    A DR > 1 indicates diversification benefit; DR ~ 1 means assets are
    perfectly correlated (no diversification).

    Parameters
    ----------
    returns_map : dict[str, pd.Series]
    weights : dict[str, float], optional
        Asset name -> portfolio weight.  Defaults to equal weight.

    Returns
    -------
    float
        Diversification ratio (>= 1).  Returns 1.0 on degenerate input.
    """
    if len(returns_map) < 1:
        return 1.0

    df = pd.DataFrame(returns_map).dropna()
    if len(df) < 2:
        return 1.0

    assets = list(df.columns)
    n = len(assets)

    if weights is None:
        w = np.ones(n) / n
    else:
        w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
        w_sum = w.sum()
        if w_sum <= 0:
            return 1.0
        w = w / w_sum  # normalize

    # Individual volatilities
    vols = df.std().values  # (n,)

    # Weighted average volatility
    weighted_avg_vol = float(np.dot(w, vols))

    # Portfolio volatility
    cov = df.cov().values
    port_var = float(np.dot(w, np.dot(cov, w)))
    port_vol = np.sqrt(max(port_var, 0.0))

    if port_vol < 1e-12:
        return 1.0

    dr = weighted_avg_vol / port_vol
    return round(max(dr, 1.0), 4)


# ---------------------------------------------------------------------------
# 6. Report generation
# ---------------------------------------------------------------------------

def generate_cross_asset_report(
    returns_map: Dict[str, pd.Series],
    weights: Optional[Dict[str, float]] = None,
    regime_labels: Optional[List[str]] = None,
) -> CrossAssetReport:
    """Generate a comprehensive cross-asset correlation report.

    Parameters
    ----------
    returns_map : dict[str, pd.Series]
        Asset name -> daily return series.
    weights : dict[str, float], optional
        Portfolio weights for diversification ratio.
    regime_labels : list[str], optional
        Daily regime assignment for regime-conditional analysis.

    Returns
    -------
    CrossAssetReport
    """
    # Correlations
    correlations = compute_asset_correlations(returns_map)

    # Tail dependencies — all pairs
    tail_deps: List[TailDependency] = []
    assets = sorted(returns_map.keys())
    for a, b in combinations(assets, 2):
        td = compute_tail_dependency(
            returns_map[a], returns_map[b],
            asset_a=a, asset_b=b,
        )
        tail_deps.append(td)

    # Regime correlations
    regime_corrs: List[RegimeCorrelation] = []
    if regime_labels:
        regime_corrs = compute_regime_correlations(returns_map, regime_labels)

    # Current regime (most recent label)
    current_regime = regime_labels[-1] if regime_labels else "neutral"

    # Signals
    signals = detect_cross_asset_signals(
        correlations, tail_deps, current_regime,
    )

    # Diversification ratio
    dr = compute_diversification_ratio(returns_map, weights)

    return CrossAssetReport(
        correlations=correlations,
        tail_deps=tail_deps,
        regime_correlations=regime_corrs,
        signals=signals,
        diversification_ratio=dr,
    )


# ---------------------------------------------------------------------------
# 7. Telegram formatting
# ---------------------------------------------------------------------------

def format_cross_asset_report(report: CrossAssetReport) -> str:
    """Format a CrossAssetReport as plain text with emoji for Telegram.

    No parse_mode — returns plain text string.
    """
    lines: List[str] = []

    lines.append("🌐 크로스 에셋 상관관계 리포트")
    lines.append("")

    # --- Top correlations (sorted by absolute 30d corr) ---
    lines.append("📊 주요 상관관계 (30일)")
    sorted_corrs = sorted(
        report.correlations,
        key=lambda c: abs(c.correlation_30d),
        reverse=True,
    )
    for c in sorted_corrs[:5]:
        arrow = "🔼" if c.rolling_change > 0.05 else (
            "🔽" if c.rolling_change < -0.05 else "➡️"
        )
        lines.append(
            f"  {c.asset_a}/{c.asset_b}: "
            f"{c.correlation_30d:+.2f} {arrow} ({c.regime})"
        )
    lines.append("")

    # --- Tail dependency warnings ---
    high_tail = [t for t in report.tail_deps if t.lower_tail > 0.3]
    if high_tail:
        lines.append("⚠️ 높은 꼬리 의존성 (동시 하락 위험)")
        for t in sorted(high_tail, key=lambda x: -x.lower_tail)[:3]:
            lines.append(
                f"  {t.asset_a}/{t.asset_b}: "
                f"하락 동조 {t.lower_tail:.0%} / "
                f"비대칭 {t.asymmetry:+.2f}"
            )
        lines.append("")

    # --- Regime correlations ---
    if report.regime_correlations:
        lines.append("🔄 레짐별 평균 상관관계")
        for rc in report.regime_correlations:
            emoji = {"bull": "🐂", "bear": "🐻", "neutral": "⚖️"}.get(
                rc.regime, "📌",
            )
            lines.append(
                f"  {emoji} {rc.regime}: "
                f"avg {rc.avg_correlation:+.2f} "
                f"(n={rc.n_observations})"
            )
        lines.append("")

    # --- Signals ---
    if report.signals:
        lines.append("🚨 크로스 에셋 시그널")
        type_emoji = {
            "correlation_breakdown": "💥",
            "contagion_risk": "🦠",
            "safe_haven_activation": "🛡️",
            "risk_on_signal": "🟢",
            "divergence_opportunity": "🔀",
        }
        for s in sorted(report.signals, key=lambda x: -x.strength):
            em = type_emoji.get(s.signal_type, "📌")
            lines.append(f"  {em} [{s.strength:.0%}] {s.description}")
            lines.append(f"     → {s.recommendation}")
        lines.append("")

    # --- Diversification ratio ---
    dr = report.diversification_ratio
    if dr >= 1.5:
        dr_label = "양호"
        dr_emoji = "✅"
    elif dr >= 1.2:
        dr_label = "보통"
        dr_emoji = "🟡"
    else:
        dr_label = "부족"
        dr_emoji = "🔴"
    lines.append(f"📐 분산 효과: {dr:.2f}x {dr_emoji} ({dr_label})")

    return "\n".join(lines)
