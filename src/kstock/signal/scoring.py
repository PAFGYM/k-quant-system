"""100-point scoring system with YAML-driven weights."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

import numpy as np

from kstock.features.technical import TechnicalIndicators
from kstock.ingest.kis_client import StockInfo
from kstock.ingest.macro_client import MacroSnapshot


def _quantile_score(value: float, historical: list[float] | None, default: float = 0.5) -> float:
    """값을 백분위 기반 0~1 점수로 변환.

    historical이 없으면 default 반환.
    """
    if not historical or len(historical) < 5:
        return default
    arr = np.array(historical, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 5:
        return default
    # percentile rank
    rank = np.sum(arr <= value) / len(arr)
    return round(max(0.0, min(1.0, rank)), 4)


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


def get_regime_weights(config: dict, macro: MacroSnapshot) -> dict:
    """VIX 레짐에 따른 동적 가중치 반환.

    risk_on (VIX<15): 기술적 분석 가중, 리스크 중시
    risk_off (VIX>25): 매크로/리스크 가중, 기술적 축소
    panic (VIX>35): 리스크 최우선
    """
    regime_weights = config.get("regime_weights", {})

    if macro.vix > 35:
        regime = "panic"
    elif macro.vix > 25 or macro.regime == "risk_off":
        regime = "risk_off"
    elif macro.vix < 15 or macro.regime == "risk_on":
        regime = "risk_on"
    else:
        regime = "neutral"

    weights = regime_weights.get(regime)
    if weights:
        return weights
    return config["weights"]


def score_macro(macro: MacroSnapshot, thresholds: dict, historical: dict | None = None) -> float:
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

    # VIX scoring: quantile-based if historical available
    vix_hist = (historical or {}).get("vix_history")
    if vix_hist and len(vix_hist) >= 20:
        vix_pctile = _quantile_score(macro.vix, vix_hist)
        # Low percentile (low VIX) = good
        score += (1.0 - vix_pctile) * 0.4 - 0.2  # maps [0,1] -> [-0.2, +0.2]
    else:
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


def score_flow(flow: FlowData, thresholds: dict, historical: dict | None = None) -> float:
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

    # Foreign flow: continuous scoring instead of binary
    if abs(flow.foreign_net_buy_days) >= 1:
        # Sigmoid-like continuous mapping: [-10,+10] -> [-0.25, +0.25]
        flow_signal = max(-10, min(10, flow.foreign_net_buy_days))
        score += flow_signal / 40  # +-10 days -> +-0.25

    # Institutional: same continuous approach
    if abs(flow.institution_net_buy_days) >= 1:
        inst_signal = max(-10, min(10, flow.institution_net_buy_days))
        score += inst_signal / 60  # +-10 days -> +-0.167

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

    # Divergence signals (v8.1)
    if tech.rsi_divergence == 1:  # bullish
        score += 0.1
    elif tech.rsi_divergence == -1:  # bearish
        score -= 0.1

    if tech.macd_divergence == 1:
        score += 0.1
    elif tech.macd_divergence == -1:
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
    multi_agent_bonus: int = 0,
    factor_bonus: int = 0,
    event_bonus: int = 0,
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

    weights = get_regime_weights(config, macro)
    thresholds = config["thresholds"]
    buy_threshold = config.get("buy_threshold", 70)
    watch_threshold = config.get("watch_threshold", 55)

    historical = config.get("historical_data")
    s_macro = score_macro(macro, thresholds, historical)
    s_flow = score_flow(flow, thresholds, historical)
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

    # v6.2: 멀티에이전트 분석 연동 보너스 (+15/+10/+5/-5/-10)
    composite += multi_agent_bonus

    # v7.0: 멀티팩터 모델 연동 보너스 (+10/+5/-5/-10)
    composite += factor_bonus

    # v9.5.3: 글로벌 이벤트 기반 점수 조정 (±15)
    composite += event_bonus

    # v3.0: max ~175 points possible (event_bonus 추가)
    composite = round(max(0.0, min(175.0, composite)), 2)

    # v9.6.1: 시그널 임계치를 config에서 읽어 동적 적용
    strong_buy_th = config.get("strong_buy_threshold", 130)
    buy_high_th = config.get("buy_threshold_high", 110)
    watch_high_th = config.get("watch_threshold_high", 90)

    if composite >= strong_buy_th:
        signal = "STRONG_BUY"
    elif composite >= buy_high_th:
        signal = "BUY"
    elif composite >= watch_high_th:
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
