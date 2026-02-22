"""100-point scoring system with YAML-driven weights."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from kstock.features.technical import TechnicalIndicators
from kstock.ingest.kis_client import StockInfo
from kstock.ingest.macro_client import MacroSnapshot


@dataclass
class ScoreBreakdown:
    """Breakdown of the 100-point composite score."""

    macro: float  # 0.0 ~ 1.0
    flow: float
    fundamental: float
    technical: float
    risk: float
    composite: float  # 0 ~ 100 weighted sum
    signal: str  # BUY, WATCH, HOLD


@dataclass
class FlowData:
    """Investor flow data for scoring."""

    foreign_net_buy_days: int = 0  # consecutive net buy days
    institution_net_buy_days: int = 0
    avg_trade_value_krw: float = 0.0


def load_scoring_config(config_path: Path | None = None) -> dict:
    """Load scoring configuration from YAML."""
    if config_path is None:
        config_path = Path("config/scoring.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def score_macro(macro: MacroSnapshot, thresholds: dict) -> float:
    """Score macro environment (0.0 ~ 1.0, higher = more favorable).

    Args:
        macro: Current macro snapshot.
        thresholds: Threshold config from YAML.

    Returns:
        Score between 0.0 and 1.0.
    """
    score = 0.5  # neutral baseline

    vix_high = thresholds.get("vix_high", 25)
    vix_low = thresholds.get("vix_low", 15)
    usdkrw_high = thresholds.get("usdkrw_high", 1350)
    usdkrw_low = thresholds.get("usdkrw_low", 1250)

    # VIX scoring: low VIX = good
    if macro.vix <= vix_low:
        score += 0.2
    elif macro.vix >= vix_high:
        score -= 0.2

    # SPX change: positive = good
    if macro.spx_change_pct > 0.5:
        score += 0.15
    elif macro.spx_change_pct < -1.0:
        score -= 0.15

    # USDKRW: low = good for Korean stocks
    if macro.usdkrw <= usdkrw_low:
        score += 0.15
    elif macro.usdkrw >= usdkrw_high:
        score -= 0.15

    return round(max(0.0, min(1.0, score)), 4)


def score_flow(flow: FlowData, thresholds: dict) -> float:
    """Score investor flow (0.0 ~ 1.0, higher = more favorable).

    Args:
        flow: Flow data with net buy days and trade values.
        thresholds: Threshold config from YAML.

    Returns:
        Score between 0.0 and 1.0.
    """
    score = 0.5

    req_days = thresholds.get("foreign_net_buy_days", 3)
    inst_days = thresholds.get("institution_net_buy_days", 3)
    min_value = thresholds.get("min_avg_value_krw", 3_000_000_000)

    # Foreign net buy streak
    if flow.foreign_net_buy_days >= req_days:
        score += 0.2
    elif flow.foreign_net_buy_days <= -req_days:
        score -= 0.2

    # Institutional net buy streak
    if flow.institution_net_buy_days >= inst_days:
        score += 0.15
    elif flow.institution_net_buy_days <= -inst_days:
        score -= 0.15

    # Trading value (liquidity)
    if flow.avg_trade_value_krw >= min_value:
        score += 0.1
    elif flow.avg_trade_value_krw < min_value * 0.5:
        score -= 0.1

    return round(max(0.0, min(1.0, score)), 4)


def score_fundamental(info: StockInfo, thresholds: dict) -> float:
    """Score fundamentals (0.0 ~ 1.0, higher = more favorable).

    Args:
        info: Stock fundamental info.
        thresholds: Threshold config from YAML.

    Returns:
        Score between 0.0 and 1.0.
    """
    score = 0.5

    per_max = thresholds.get("per_max", 30)
    per_min = thresholds.get("per_min", 5)
    roe_min = thresholds.get("roe_min", 8.0)
    debt_max = thresholds.get("debt_ratio_max", 200)
    target_pct = thresholds.get("consensus_target_pct", 10.0)

    # PER scoring
    if per_min <= info.per <= per_max:
        score += 0.1
    elif info.per > per_max:
        score -= 0.1

    # ROE scoring
    if info.roe >= roe_min:
        score += 0.15
    elif info.roe < roe_min * 0.5:
        score -= 0.1

    # Debt ratio
    if info.debt_ratio <= debt_max * 0.5:
        score += 0.1
    elif info.debt_ratio > debt_max:
        score -= 0.2

    # Consensus target upside
    if info.current_price > 0:
        upside_pct = (info.consensus_target - info.current_price) / info.current_price * 100
        if upside_pct >= target_pct:
            score += 0.15
        elif upside_pct < 0:
            score -= 0.1

    return round(max(0.0, min(1.0, score)), 4)


def score_technical(tech: TechnicalIndicators, thresholds: dict) -> float:
    """Score technical indicators (0.0 ~ 1.0, higher = more favorable).

    Args:
        tech: Computed technical indicators.
        thresholds: Threshold config from YAML.

    Returns:
        Score between 0.0 and 1.0.
    """
    score = 0.5

    rsi_oversold = thresholds.get("rsi_oversold", 30)
    rsi_overbought = thresholds.get("rsi_overbought", 70)

    # RSI scoring: oversold = opportunity, overbought = caution
    if tech.rsi <= rsi_oversold:
        score += 0.2
    elif tech.rsi >= rsi_overbought:
        score -= 0.15
    elif 40 <= tech.rsi <= 60:
        score += 0.05

    # Bollinger Band %B: near lower band = opportunity
    if tech.bb_pctb <= 0.2:
        score += 0.15
    elif tech.bb_pctb >= 0.8:
        score -= 0.1

    # MACD signal cross
    if tech.macd_signal_cross == 1:
        score += 0.15
    elif tech.macd_signal_cross == -1:
        score -= 0.1

    return round(max(0.0, min(1.0, score)), 4)


def score_risk(tech: TechnicalIndicators, info: StockInfo, thresholds: dict) -> float:
    """Score risk factors (0.0 ~ 1.0, higher = lower risk = more favorable).

    Args:
        tech: Technical indicators (for ATR-based volatility).
        info: Stock info (for debt ratio).
        thresholds: Threshold config from YAML.

    Returns:
        Score between 0.0 and 1.0.
    """
    score = 0.7  # start favorable

    debt_max = thresholds.get("debt_ratio_max", 200)
    max_dd = thresholds.get("max_drawdown_pct", 20)

    # Volatility (ATR%) - high ATR = higher risk
    if tech.atr_pct > 5.0:
        score -= 0.3
    elif tech.atr_pct > 3.0:
        score -= 0.15
    elif tech.atr_pct < 1.5:
        score += 0.1

    # Debt ratio risk
    if info.debt_ratio > debt_max:
        score -= 0.2
    elif info.debt_ratio > debt_max * 0.75:
        score -= 0.1

    return round(max(0.0, min(1.0, score)), 4)


def compute_composite_score(
    macro: MacroSnapshot,
    flow: FlowData,
    info: StockInfo,
    tech: TechnicalIndicators,
    config: dict | None = None,
    mtf_bonus: int = 0,
    sector_adj: int = 0,
    policy_bonus: int = 0,
    ml_bonus: int = 0,
    sentiment_bonus: int = 0,
    leading_sector_bonus: int = 0,
) -> ScoreBreakdown:
    """Compute the 100-point composite score.

    Args:
        macro: Macro snapshot.
        flow: Flow data.
        info: Stock info.
        tech: Technical indicators.
        config: Scoring config dict. Loaded from YAML if None.

    Returns:
        ScoreBreakdown with individual and composite scores.
    """
    if config is None:
        config = load_scoring_config()

    weights = config["weights"]
    thresholds = config["thresholds"]
    buy_threshold = config.get("buy_threshold", 70)
    watch_threshold = config.get("watch_threshold", 55)

    s_macro = score_macro(macro, thresholds)
    s_flow = score_flow(flow, thresholds)
    s_fundamental = score_fundamental(info, thresholds)
    s_technical = score_technical(tech, thresholds)
    s_risk = score_risk(tech, info, thresholds)

    composite = (
        s_macro * weights["macro"]
        + s_flow * weights["flow"]
        + s_fundamental * weights["fundamental"]
        + s_technical * weights["technical"]
        + s_risk * weights["risk"]
    ) * 100

    # v2.5: Multi-timeframe and sector adjustments
    composite += mtf_bonus  # +10 aligned, -10 misaligned
    composite += sector_adj  # +5 top sector, -5 bottom

    # v3.0: Policy, ML, sentiment, leading sector bonuses
    composite += policy_bonus       # +10 밸류업 수혜, +5 코스닥
    composite += ml_bonus           # +15/+10/+5/-10
    composite += sentiment_bonus    # +10/+5/-10
    composite += leading_sector_bonus  # +5 tier1, +2 tier2

    # v3.0: max ~160 points possible
    composite = round(max(0.0, min(160.0, composite)), 2)

    # v3.0 thresholds (max 160 scale)
    if composite >= 130:
        signal = "STRONG_BUY"
    elif composite >= 110:
        signal = "BUY"
    elif composite >= 90:
        signal = "WATCH"
    elif composite >= buy_threshold:
        signal = "MILD_BUY"
    elif composite >= watch_threshold:
        signal = "WATCH"
    else:
        signal = "HOLD"

    return ScoreBreakdown(
        macro=s_macro,
        flow=s_flow,
        fundamental=s_fundamental,
        technical=s_technical,
        risk=s_risk,
        composite=composite,
        signal=signal,
    )
