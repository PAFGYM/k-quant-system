"""Market regime detection including bubble attack mode for K-Quant v3.0."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.policy_engine import has_bullish_policy

logger = logging.getLogger(__name__)


@dataclass
class RegimeResult:
    """Market regime detection result."""

    mode: str  # "bubble_attack", "attack", "balanced", "defense"
    emoji: str
    label: str
    message: str
    allocations: dict
    profit_target_pct: float = 5.0
    trailing_stop_pct: float = -5.0


def detect_regime(
    macro: MacroSnapshot,
    kospi_60d_return: float = 0.0,
    foreign_consecutive_sell_days: int = 0,
    usdkrw_spike: bool = False,
    kospi_daily_drop: float = 0.0,
    today: date | None = None,
) -> RegimeResult:
    """Detect market regime including bubble attack mode.

    Bubble Attack conditions (all 3 must be met):
    1. KOSPI 60-day return > 15%
    2. Active bullish policy event
    3. VIX < 20

    Safety triggers:
    - KOSPI daily -3% -> balanced mode
    - VIX >= 25 -> defense mode
    - 3-day foreign sell + FX spike -> warning
    """
    # Safety overrides first
    if macro.vix >= 25 or macro.regime == "risk_off":
        return RegimeResult(
            mode="defense",
            emoji="\U0001f6e1\ufe0f",
            label="\ubc29\uc5b4 \ubaa8\ub4dc",
            message="\uc9c0\uae08\uc740 \uc0ac\uc9c0 \ub9c8\uc138\uc694. \uc778\ubc84\uc2a4\ub85c \ud5f7\uc9d5\ud558\uc138\uc694",
            allocations={
                "A": 5, "B": 25, "C": 15, "D": 5,
                "E": 15, "F": 0, "G": 0, "cash": 35,
            },
            profit_target_pct=3.0,
            trailing_stop_pct=-3.0,
        )

    if kospi_daily_drop <= -3.0:
        return RegimeResult(
            mode="balanced",
            emoji="\u26a0\ufe0f",
            label="\uae34\uae09 \uade0\ud615 \ubaa8\ub4dc",
            message="KOSPI \uae09\ub77d! \uc2e0\uaddc \ub9e4\uc218 \uc911\ub2e8, \ubcf4\uc720 \uc885\ubaa9 \uc810\uac80",
            allocations={
                "A": 10, "B": 10, "C": 20, "D": 10,
                "E": 15, "F": 5, "G": 5, "cash": 25,
            },
            profit_target_pct=3.0,
            trailing_stop_pct=-5.0,
        )

    # Bubble Attack check
    bullish_policy = has_bullish_policy(today)
    is_bubble = (
        kospi_60d_return > 15
        and bullish_policy
        and macro.vix < 20
    )

    if is_bubble:
        # Check warning conditions
        warning = ""
        if foreign_consecutive_sell_days >= 3 and usdkrw_spike:
            warning = "\n\u26a0\ufe0f \uc678\uc778 3\uc77c \uc5f0\uc18d \uc21c\ub9e4\ub3c4 + \ud658\uc728 \uae09\ub4f1 \uacbd\uace0!"

        return RegimeResult(
            mode="bubble_attack",
            emoji="\U0001f525\U0001f680",
            label="BUBBLE ATTACK",
            message=f"\ubc84\ube14\uc7a5 \uacf5\uaca9 \ubaa8\ub4dc! \ubaa8\uba58\ud140+\ub3cc\ud30c \uc804\ub7b5 \uac15\ud654{warning}",
            allocations={
                "A": 10, "B": 10, "C": 5, "D": 10,
                "E": 5, "F": 30, "G": 20, "cash": 5,
                "trailing_mode": True,
            },
            profit_target_pct=8.0,
            trailing_stop_pct=-7.0,
        )

    if macro.regime == "risk_on" or macro.vix < 15:
        return RegimeResult(
            mode="attack",
            emoji="\U0001f680",
            label="\uacf5\uaca9 \ubaa8\ub4dc",
            message="\uc2dc\uc7a5\uc774 \uc88b\uc2b5\ub2c8\ub2e4. \uc801\uadf9 \ub9e4\uc218 \uad6c\uac04",
            allocations={
                "A": 20, "B": 15, "C": 10, "D": 15,
                "E": 10, "F": 20, "G": 5, "cash": 5,
            },
            profit_target_pct=5.0,
            trailing_stop_pct=-5.0,
        )

    # Balanced (default)
    return RegimeResult(
        mode="balanced",
        emoji="\u2696\ufe0f",
        label="\uade0\ud615 \ubaa8\ub4dc",
        message="\uac1c\ubcc4\uc885\ubaa9 \ubc18\ub4f1 + \uc7a5\uae30 \uc801\ub9bd\uc2dd \ubcd1\ud589",
        allocations={
            "A": 15, "B": 10, "C": 20, "D": 10,
            "E": 15, "F": 10, "G": 5, "cash": 15,
        },
        profit_target_pct=5.0,
        trailing_stop_pct=-5.0,
    )


# ---------------------------------------------------------------------------
# HMM / GMM regime extension (v6.3)
# ---------------------------------------------------------------------------

@dataclass
class HMMRegimeState:
    """Hidden Markov Model regime state (v6.3)."""

    state_id: int
    label: str  # "bull", "bear", "transition"
    probability: float
    mean_return: float
    volatility: float
    duration_days: float


@dataclass
class RegimeAnalysis:
    """Extended regime analysis with HMM (v6.3)."""

    current_regime: RegimeResult
    hmm_state: HMMRegimeState | None
    transition_probability: float
    state_history: list[int] = field(default_factory=list)
    regime_duration_days: int = 0
    volatility_regime: str = "normal"   # "low_vol", "normal", "high_vol"
    trend_regime: str = "random"        # "trending", "mean_reverting", "random"


def _fit_gmm_regime(
    returns: np.ndarray,
    n_states: int = 3,
    n_iter: int = 30,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Simplified Gaussian Mixture Model regime detection (numpy only).

    3 states: bull(0), transition(1), bear(2).
    Uses Expectation-Maximization algorithm.

    Args:
        returns: 1-D array of daily returns.
        n_states: Number of mixture components (default 3).
        n_iter: EM iterations.

    Returns:
        Tuple of (means, stds, weights, state_sequence).
    """
    try:
        returns = returns.flatten().astype(float)
        n = len(returns)
        if n < 5:
            # Fallback: simple classification
            means = np.array([0.001, 0.0, -0.001])
            stds = np.array([0.01, 0.01, 0.01])
            weights = np.array([1 / 3, 1 / 3, 1 / 3])
            seq = np.ones(n, dtype=int)  # all transition
            return means, stds, weights, seq

        # Initialization
        means = np.array([
            np.percentile(returns, 75),
            np.median(returns),
            np.percentile(returns, 25),
        ])
        stds = np.array([np.std(returns)] * n_states)
        stds = np.maximum(stds, 1e-8)
        weights = np.array([1 / n_states] * n_states)

        # EM iterations
        responsibilities = np.zeros((n, n_states))

        for _ in range(n_iter):
            # E-step: compute responsibilities
            for k in range(n_states):
                diff = returns - means[k]
                exponent = -0.5 * (diff / stds[k]) ** 2
                # Clip exponent to avoid overflow
                exponent = np.clip(exponent, -500, 500)
                responsibilities[:, k] = (
                    weights[k]
                    / (stds[k] * math.sqrt(2 * math.pi))
                    * np.exp(exponent)
                )

            # Normalize
            row_sums = responsibilities.sum(axis=1, keepdims=True)
            row_sums = np.maximum(row_sums, 1e-300)
            responsibilities = responsibilities / row_sums

            # M-step: update parameters
            nk = responsibilities.sum(axis=0)
            nk = np.maximum(nk, 1e-10)

            weights = nk / n
            for k in range(n_states):
                means[k] = np.sum(responsibilities[:, k] * returns) / nk[k]
                diff = returns - means[k]
                stds[k] = math.sqrt(
                    np.sum(responsibilities[:, k] * diff ** 2) / nk[k]
                )
                stds[k] = max(stds[k], 1e-8)

        state_sequence = np.argmax(responsibilities, axis=1)
        return means, stds, weights, state_sequence

    except Exception as exc:
        logger.warning("GMM fitting failed, using volatility fallback: %s", exc)
        # Fallback: simple volatility-based classification
        n = len(returns)
        means = np.array([0.001, 0.0, -0.001])
        stds = np.array([0.01, 0.01, 0.01])
        weights = np.array([1 / 3, 1 / 3, 1 / 3])
        seq = np.ones(n, dtype=int)  # default: transition

        if n > 0:
            median_ret = np.median(returns)
            for i in range(n):
                if returns[i] > median_ret + np.std(returns) * 0.5:
                    seq[i] = 0  # bull
                elif returns[i] < median_ret - np.std(returns) * 0.5:
                    seq[i] = 2  # bear
                else:
                    seq[i] = 1  # transition

        return means, stds, weights, seq


def detect_hmm_regime(
    daily_returns: list[float],
    lookback: int = 252,
) -> HMMRegimeState | None:
    """Detect current regime using Gaussian Mixture Model.

    Args:
        daily_returns: List of daily returns (decimal).
        lookback: Number of recent days to analyze.

    Returns:
        HMMRegimeState or None if insufficient data (<30).
    """
    if len(daily_returns) < 30:
        return None

    try:
        recent = daily_returns[-lookback:]
        arr = np.array(recent, dtype=float)
        means, stds, weights, state_seq = _fit_gmm_regime(arr)

        # Map states by mean: highest→bull, lowest→bear, middle→transition
        sorted_indices = np.argsort(means)  # ascending
        label_map = {}
        label_map[int(sorted_indices[2])] = "bull"
        label_map[int(sorted_indices[1])] = "transition"
        label_map[int(sorted_indices[0])] = "bear"

        current_state = int(state_seq[-1])
        label = label_map.get(current_state, "transition")

        # Probability: fraction of recent 20 days in this state
        recent_20 = state_seq[-20:] if len(state_seq) >= 20 else state_seq
        prob = float(np.mean(recent_20 == current_state))

        # Duration: consecutive days in current state (from end)
        duration = 0
        for s in reversed(state_seq):
            if s == current_state:
                duration += 1
            else:
                break

        return HMMRegimeState(
            state_id=current_state,
            label=label,
            probability=round(prob, 4),
            mean_return=round(float(means[current_state]), 6),
            volatility=round(float(stds[current_state]), 6),
            duration_days=float(duration),
        )

    except Exception as exc:
        logger.error("detect_hmm_regime failed: %s", exc)
        return None


def detect_volatility_regime(
    daily_returns: list[float],
    lookback: int = 60,
) -> str:
    """Classify current volatility regime.

    Args:
        daily_returns: List of daily returns (decimal).
        lookback: Short-term lookback period.

    Returns:
        "low_vol", "normal", or "high_vol".
    """
    if len(daily_returns) < lookback:
        return "normal"

    try:
        sqrt_252 = math.sqrt(252)
        recent = daily_returns[-lookback:]
        current_vol = float(np.std(recent)) * sqrt_252

        # Use up to 252 days for average vol
        long_period = min(len(daily_returns), 252)
        long_data = daily_returns[-long_period:]
        avg_vol = float(np.std(long_data)) * sqrt_252

        if avg_vol < 1e-12:
            return "normal"

        ratio = current_vol / avg_vol

        if ratio < 0.7:
            return "low_vol"
        elif ratio > 1.3:
            return "high_vol"
        else:
            return "normal"

    except Exception as exc:
        logger.error("detect_volatility_regime failed: %s", exc)
        return "normal"


def detect_trend_regime(
    daily_returns: list[float],
    lookback: int = 60,
) -> str:
    """Classify trend regime using Hurst exponent (Rescaled Range method).

    Args:
        daily_returns: List of daily returns (decimal).
        lookback: Lookback period.

    Returns:
        "trending" (H > 0.6), "mean_reverting" (H < 0.4), or "random".
    """
    if len(daily_returns) < lookback:
        return "random"

    try:
        recent = daily_returns[-lookback:]
        arr = np.array(recent, dtype=float)
        mean_r = float(np.mean(arr))

        # Cumulative deviation from mean
        series = np.cumsum(arr - mean_r)

        R = float(np.max(series) - np.min(series))
        S = float(np.std(arr, ddof=0))

        if R <= 0 or S <= 0 or lookback <= 1:
            return "random"

        H = math.log(R / S) / math.log(lookback)

        if H > 0.6:
            return "trending"
        elif H < 0.4:
            return "mean_reverting"
        else:
            return "random"

    except Exception as exc:
        logger.error("detect_trend_regime failed: %s", exc)
        return "random"


def analyze_regime_extended(
    macro: MacroSnapshot,
    daily_returns: list[float],
    kospi_60d_return: float = 0.0,
    foreign_consecutive_sell_days: int = 0,
    usdkrw_spike: bool = False,
    kospi_daily_drop: float = 0.0,
    today: date | None = None,
) -> RegimeAnalysis:
    """Perform extended regime analysis combining rule-based and HMM.

    Args:
        macro: Current macro snapshot.
        daily_returns: List of daily returns (decimal).
        kospi_60d_return: KOSPI 60-day return (%).
        foreign_consecutive_sell_days: Consecutive foreign sell days.
        usdkrw_spike: Whether USD/KRW spiked.
        kospi_daily_drop: KOSPI daily drop (%).
        today: Override date for policy check.

    Returns:
        RegimeAnalysis with combined regime information.
    """
    # 1. Rule-based regime
    current_regime = detect_regime(
        macro=macro,
        kospi_60d_return=kospi_60d_return,
        foreign_consecutive_sell_days=foreign_consecutive_sell_days,
        usdkrw_spike=usdkrw_spike,
        kospi_daily_drop=kospi_daily_drop,
        today=today,
    )

    # 2. HMM regime
    hmm_state = detect_hmm_regime(daily_returns)

    # 3. Volatility regime
    volatility_regime = detect_volatility_regime(daily_returns)

    # 4. Trend regime
    trend_regime = detect_trend_regime(daily_returns)

    # 5. State history and transition probability
    state_history: list[int] = []
    transition_probability = 0.0

    if hmm_state is not None and len(daily_returns) >= 30:
        arr = np.array(daily_returns[-252:], dtype=float)
        _, _, _, state_seq = _fit_gmm_regime(arr)
        state_history = [int(s) for s in state_seq]

        # Transition probability: fraction of state changes in recent 20 days
        recent_states = state_history[-20:] if len(state_history) >= 20 else state_history
        if len(recent_states) > 1:
            changes = sum(
                1 for i in range(1, len(recent_states))
                if recent_states[i] != recent_states[i - 1]
            )
            transition_probability = round(changes / (len(recent_states) - 1), 4)

    # 6. Regime duration: consecutive days in current rule-based mode
    regime_duration_days = int(hmm_state.duration_days) if hmm_state else 0

    return RegimeAnalysis(
        current_regime=current_regime,
        hmm_state=hmm_state,
        transition_probability=transition_probability,
        state_history=state_history,
        regime_duration_days=regime_duration_days,
        volatility_regime=volatility_regime,
        trend_regime=trend_regime,
    )


def format_regime_analysis(analysis: RegimeAnalysis) -> str:
    """Format RegimeAnalysis for Telegram display.

    Args:
        analysis: RegimeAnalysis to format.

    Returns:
        Multi-line string ready for Telegram (plain text + emoji).
    """
    lines: list[str] = []

    r = analysis.current_regime
    lines.append(f"{r.emoji} 시장 레짐: {r.label}")
    lines.append(f"  {r.message}")
    lines.append("")

    if analysis.hmm_state:
        h = analysis.hmm_state
        state_emoji = {"bull": "\U0001f7e2", "bear": "\U0001f534", "transition": "\U0001f7e1"}
        emoji = state_emoji.get(h.label, "\u2753")
        lines.append(f"{emoji} HMM 상태: {h.label.upper()}")
        lines.append(f"  확률: {h.probability:.0%} | 평균수익: {h.mean_return:.4f}")
        lines.append(f"  변동성: {h.volatility:.4f} | 지속: {h.duration_days:.0f}일")
    else:
        lines.append("HMM 분석: 데이터 부족")
    lines.append("")

    vol_emoji = {"low_vol": "\U0001f7e2", "normal": "\U0001f7e1", "high_vol": "\U0001f534"}
    trend_emoji = {"trending": "\u2197\ufe0f", "mean_reverting": "\u2194\ufe0f", "random": "\u2753"}

    lines.append(
        f"변동성: {vol_emoji.get(analysis.volatility_regime, '')} "
        f"{analysis.volatility_regime}"
    )
    lines.append(
        f"추세: {trend_emoji.get(analysis.trend_regime, '')} "
        f"{analysis.trend_regime}"
    )
    lines.append(f"전환 확률: {analysis.transition_probability:.1%}")
    lines.append(f"레짐 지속: {analysis.regime_duration_days}일")

    return "\n".join(lines)
