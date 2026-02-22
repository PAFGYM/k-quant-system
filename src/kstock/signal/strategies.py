"""Multi-strategy system for K-Quant v2.5.

Strategy A: Foreign oversold bounce (short-term, individual stocks)
Strategy B: ETF leverage swing (ultra-short, leveraged/inverse ETFs) - enhanced
Strategy C: Long-term quality accumulation (dividend ETFs, value stocks)
Strategy D: Sector rotation (sector ETFs)
Strategy E: Global diversification (US ETFs, gold)
Strategy F: Momentum (golden cross + relative strength)
Strategy G: Breakout (52-week high / 20-day high breakout)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kstock.features.technical import TechnicalIndicators
    from kstock.ingest.macro_client import MacroSnapshot
    from kstock.signal.scoring import FlowData, ScoreBreakdown

logger = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """Result of a strategy evaluation."""

    strategy: str  # A, B, C, D, E, F, G
    strategy_name: str
    strategy_emoji: str
    ticker: str
    name: str
    action: str  # BUY, WATCH, HOLD, SELL
    score: float  # 0~100
    confidence: float  # 0~1
    target_pct: float  # target gain %
    stop_pct: float  # stop loss %
    holding_days: str  # expected holding period
    reasons: list[str] = field(default_factory=list)


STRATEGY_META = {
    "A": {"name": "단기 반등", "emoji": "\U0001f525", "target": 5.0, "stop": -5.0, "days": "3~10일"},
    "B": {"name": "ETF 레버리지", "emoji": "\u26a1", "target": 3.5, "stop": -3.0, "days": "1~3일"},
    "C": {"name": "장기 우량주", "emoji": "\U0001f3e6", "target": 15.0, "stop": -10.0, "days": "6개월~1년"},
    "D": {"name": "섹터 로테이션", "emoji": "\U0001f504", "target": 10.0, "stop": -7.0, "days": "1~3개월"},
    "E": {"name": "글로벌 분산", "emoji": "\U0001f30e", "target": 12.0, "stop": -8.0, "days": "장기"},
    "F": {"name": "모멘텀", "emoji": "\U0001f680", "target": 7.0, "stop": -5.0, "days": "2~8주"},
    "G": {"name": "돌파", "emoji": "\U0001f4a5", "target": 5.0, "stop": -2.0, "days": "3~10일"},
}

# ETF categorization
LEVERAGE_ETFS = {"122630", "252670"}
INVERSE_ETFS = {"114800", "252670"}
SECTOR_ETFS = {"091160", "305540", "244580", "462900"}
GLOBAL_ETFS = {"360750", "133690", "132030", "130680"}
DIVIDEND_ETFS = {"211560", "290080"}
INDEX_ETFS = {"069500", "102110"}

ALL_ETFS = LEVERAGE_ETFS | INVERSE_ETFS | SECTOR_ETFS | GLOBAL_ETFS | DIVIDEND_ETFS | INDEX_ETFS


def evaluate_strategy_a(
    ticker: str,
    name: str,
    score: ScoreBreakdown,
    tech: TechnicalIndicators,
    flow: FlowData,
    macro: MacroSnapshot,
) -> StrategySignal | None:
    """Strategy A: Foreign oversold bounce."""
    meta = STRATEGY_META["A"]

    if ticker in ALL_ETFS:
        return None

    reasons = []
    sig_score = 0.0

    if flow.foreign_net_buy_days <= -3:
        sig_score += 30
        reasons.append(f"외인 매도 {abs(flow.foreign_net_buy_days)}일 연속")

    if tech.rsi <= 30:
        sig_score += 25
        reasons.append(f"RSI {tech.rsi:.1f} 과매도")
    elif tech.rsi <= 40:
        sig_score += 10
        reasons.append(f"RSI {tech.rsi:.1f} 근접")

    if flow.institution_net_buy_days >= 2:
        sig_score += 20
        reasons.append(f"기관 순매수 {flow.institution_net_buy_days}일")

    if tech.bb_pctb <= 0.2:
        sig_score += 15
        reasons.append("볼린저밴드 하단 터치")

    if tech.macd_signal_cross == 1:
        sig_score += 10
        reasons.append("MACD 골든크로스")

    if sig_score < 40:
        return None

    action = "BUY" if sig_score >= 65 else "WATCH" if sig_score >= 50 else "HOLD"

    return StrategySignal(
        strategy="A", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_b(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
    macro: MacroSnapshot,
) -> StrategySignal | None:
    """Strategy B: ETF leverage swing (enhanced v2.5).

    Enhanced entry conditions:
    - VIX decline + KOSPI 5-day breakout -> leverage buy
    - VIX spike (+15%) -> inverse buy
    - BB squeeze breakout -> leverage buy

    Risk rules:
    - Max hold 5 days, max position 15%, stop -3%
    """
    meta = STRATEGY_META["B"]

    is_leverage = ticker in LEVERAGE_ETFS
    is_inverse = ticker in INVERSE_ETFS
    if not is_leverage and not is_inverse:
        return None

    reasons = []
    sig_score = 0.0

    if is_inverse and not is_leverage:
        if macro.vix > 25:
            sig_score += 35
            reasons.append(f"VIX {macro.vix:.1f} 급등 -> 인버스 기회")
        if macro.vix_change_pct >= 15:
            sig_score += 20
            reasons.append(f"VIX 급등 {macro.vix_change_pct:+.1f}% (15%+ 트리거)")
        if macro.spx_change_pct < -1.5:
            sig_score += 25
            reasons.append(f"S&P500 {macro.spx_change_pct:+.1f}% 급락")
        if macro.regime == "risk_off":
            sig_score += 20
            reasons.append("리스크오프 환경")
    elif is_leverage:
        # Enhanced: VIX decline + market rebound
        if macro.vix_change_pct < -5 and macro.vix > 18:
            sig_score += 35
            reasons.append(f"VIX 하락 전환 ({macro.vix_change_pct:+.1f}%)")
        if macro.spx_change_pct > 1.0:
            sig_score += 20
            reasons.append(f"S&P500 반등 ({macro.spx_change_pct:+.1f}%)")
        if tech.rsi <= 30:
            sig_score += 20
            reasons.append(f"RSI {tech.rsi:.1f} 과매도 반등 기대")
        if macro.regime == "risk_on":
            sig_score += 15
            reasons.append("리스크온 환경")
        # v2.5: BB squeeze breakout
        if tech.bb_squeeze and tech.bb_pctb > 0.8:
            sig_score += 15
            reasons.append("BB 스퀴즈 상방 돌파")

    if sig_score < 40:
        return None

    action = "BUY" if sig_score >= 60 else "WATCH"

    return StrategySignal(
        strategy="B", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_c(
    ticker: str,
    name: str,
    info_dict: dict,
    tech: TechnicalIndicators,
) -> StrategySignal | None:
    """Strategy C: Long-term quality accumulation."""
    meta = STRATEGY_META["C"]

    is_dividend_etf = ticker in DIVIDEND_ETFS
    is_stock = ticker not in ALL_ETFS

    if not is_dividend_etf and not is_stock:
        return None

    reasons = []
    sig_score = 0.0

    div_yield = info_dict.get("dividend_yield", 0)
    pbr = info_dict.get("pbr", 0)
    roe = info_dict.get("roe", 0)
    debt = info_dict.get("debt_ratio", 0)
    per = info_dict.get("per", 0)

    if div_yield >= 3.0:
        sig_score += 25
        reasons.append(f"배당수익률 {div_yield:.1f}%")
    elif div_yield >= 2.0:
        sig_score += 10
        reasons.append(f"배당수익률 {div_yield:.1f}%")

    if 0 < pbr <= 1.0:
        sig_score += 20
        reasons.append(f"PBR {pbr:.2f} (자산가치 이하)")
    elif 0 < pbr <= 1.5:
        sig_score += 10

    if roe >= 10:
        sig_score += 15
        reasons.append(f"ROE {roe:.1f}%")

    if 0 < debt <= 100:
        sig_score += 10
        reasons.append(f"부채비율 {debt:.0f}% (안정)")

    if 5 <= per <= 15:
        sig_score += 10
        reasons.append(f"PER {per:.1f} (저평가)")

    if tech.rsi <= 50:
        sig_score += 10

    if is_dividend_etf:
        sig_score += 20
        reasons.append("배당 ETF")

    if sig_score < 40:
        return None

    action = "BUY" if sig_score >= 60 else "WATCH"

    return StrategySignal(
        strategy="C", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_d(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
    macro: MacroSnapshot,
    sector: str = "",
) -> StrategySignal | None:
    """Strategy D: Sector rotation."""
    meta = STRATEGY_META["D"]

    if ticker not in SECTOR_ETFS:
        return None

    reasons = []
    sig_score = 0.0

    if macro.regime == "risk_on":
        sig_score += 25
        reasons.append("리스크온 -> 섹터 투자 유리")
    elif macro.regime == "neutral":
        sig_score += 10

    if 40 <= tech.rsi <= 65:
        sig_score += 20
        reasons.append(f"RSI {tech.rsi:.1f} 건전한 상승")
    elif tech.rsi <= 35:
        sig_score += 15
        reasons.append(f"RSI {tech.rsi:.1f} 반등 기대")

    if tech.macd_signal_cross == 1:
        sig_score += 20
        reasons.append("MACD 골든크로스")

    if 0.3 <= tech.bb_pctb <= 0.7:
        sig_score += 10
        reasons.append("볼린저 중립 구간")

    if sector:
        reasons.append(f"섹터: {sector}")
        sig_score += 5

    if sig_score < 35:
        return None

    action = "BUY" if sig_score >= 55 else "WATCH"

    return StrategySignal(
        strategy="D", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_e(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
    macro: MacroSnapshot,
) -> StrategySignal | None:
    """Strategy E: Global diversification."""
    meta = STRATEGY_META["E"]

    if ticker not in GLOBAL_ETFS:
        return None

    reasons = []
    sig_score = 0.0

    is_us = ticker in {"360750", "133690"}
    is_gold = ticker == "132030"
    is_oil = ticker == "130680"

    if is_us:
        if macro.usdkrw >= 1350:
            sig_score += 15
            reasons.append(f"원화 약세 ({macro.usdkrw:,.0f}원)")
        elif macro.usdkrw <= 1250:
            sig_score += 25
            reasons.append(f"원화 강세 -> 달러 매수 기회 ({macro.usdkrw:,.0f}원)")
        if macro.spx_change_pct > 0:
            sig_score += 15
            reasons.append(f"미국 시장 상승 ({macro.spx_change_pct:+.1f}%)")
        if macro.regime == "risk_on":
            sig_score += 15
            reasons.append("리스크온 -> 미국 주식 유리")
        sig_score += 15
        reasons.append("글로벌 분산 투자")
    elif is_gold:
        if macro.vix > 20:
            sig_score += 20
            reasons.append(f"VIX {macro.vix:.1f} -> 안전자산 선호")
        if macro.regime == "risk_off":
            sig_score += 25
            reasons.append("리스크오프 -> 금 수요 증가")
        elif macro.regime == "neutral":
            sig_score += 10
        sig_score += 15
        reasons.append("인플레이션 헷지")
    elif is_oil:
        if macro.regime == "risk_on":
            sig_score += 20
            reasons.append("리스크온 -> 원유 수요 기대")
        sig_score += 10
        reasons.append("원자재 분산")

    if tech.rsi <= 40:
        sig_score += 10
        reasons.append(f"RSI {tech.rsi:.1f} 매수 구간")

    if sig_score < 35:
        return None

    action = "BUY" if sig_score >= 55 else "WATCH"

    return StrategySignal(
        strategy="E", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_f(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
    rs_rank: int = 0,
    rs_total: int = 1,
) -> StrategySignal | None:
    """Strategy F: Momentum.

    Conditions:
    - Golden cross (50 EMA > 200 EMA)
    - Relative strength top 20%
    - Volume >= 1.5x average
    Exit: 50 EMA downward break or dead cross
    """
    meta = STRATEGY_META["F"]

    # Skip ETFs (momentum is for individual stocks)
    if ticker in ALL_ETFS:
        return None

    reasons = []
    sig_score = 0.0

    # Golden cross
    if tech.golden_cross:
        sig_score += 35
        reasons.append("골든크로스 발생 (50일 EMA > 200일 EMA)")
    elif tech.ema_50 > tech.ema_200 > 0:
        sig_score += 15
        reasons.append("상승 추세 유지 (50일 EMA > 200일 EMA)")

    # Relative strength (RS)
    if rs_total > 0:
        rs_pctile = rs_rank / rs_total * 100
        if rs_pctile <= 20:
            sig_score += 25
            reasons.append(f"상대강도 상위 {rs_pctile:.0f}% ({rs_total}종목 중 {rs_rank}위)")
        elif rs_pctile <= 40:
            sig_score += 10
            reasons.append(f"상대강도 상위 {rs_pctile:.0f}%")

    # Volume confirmation
    if tech.volume_ratio >= 1.5:
        sig_score += 15
        reasons.append(f"거래량 평균 대비 {tech.volume_ratio:.1f}배")
    elif tech.volume_ratio >= 1.2:
        sig_score += 5

    # 3-month return positive
    if tech.return_3m_pct > 10:
        sig_score += 10
        reasons.append(f"3개월 수익률 {tech.return_3m_pct:+.1f}%")

    if sig_score < 40:
        return None

    action = "BUY" if sig_score >= 65 else "WATCH" if sig_score >= 50 else "HOLD"

    return StrategySignal(
        strategy="F", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_g(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
) -> StrategySignal | None:
    """Strategy G: Breakout.

    Conditions:
    - 52-week high breakout, OR
    - 20-day high breakout + volume 2x+
    - BB squeeze before breakout = higher confidence

    Exit: falls back below breakout price -2%
    """
    meta = STRATEGY_META["G"]

    if ticker in ALL_ETFS:
        return None

    reasons = []
    sig_score = 0.0
    current = tech.ema_50  # approximate current price

    # 52-week high breakout
    if tech.high_52w > 0 and current > 0:
        near_52w = current / tech.high_52w
        if near_52w >= 0.98:
            sig_score += 35
            reasons.append(f"52주 신고가 돌파 ({tech.high_52w:,.0f})")

    # 20-day high breakout + volume
    if tech.high_20d > 0 and current > 0:
        near_20d = current / tech.high_20d
        if near_20d >= 0.98 and tech.volume_ratio >= 2.0:
            sig_score += 30
            reasons.append(f"20일 고점 돌파 + 거래량 {tech.volume_ratio:.1f}배")
        elif near_20d >= 0.98 and tech.volume_ratio >= 1.5:
            sig_score += 15
            reasons.append(f"20일 고점 돌파 근접")

    # BB squeeze bonus (pre-breakout compression)
    if tech.bb_squeeze:
        sig_score += 15
        reasons.append("BB 스퀴즈 (돌파 전 밴드 압축)")

    # Volume confirmation
    if tech.volume_ratio >= 2.0:
        sig_score += 10
        reasons.append(f"거래량 폭발 (평균 {tech.volume_ratio:.1f}배)")

    # Positive trend confirmation
    if tech.ema_50 > tech.ema_200 > 0:
        sig_score += 5
        reasons.append("상승 추세 확인")

    if sig_score < 40:
        return None

    action = "BUY" if sig_score >= 60 else "WATCH"

    return StrategySignal(
        strategy="G", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def compute_confidence_score(
    base_score: float,
    tech: TechnicalIndicators,
    sector_adj: int = 0,
    roe_top_30: bool = False,
    inst_buy_days: int = 0,
    is_leverage_etf: bool = False,
    leverage_hold_days: int = 0,
    corr_penalty: bool = False,
) -> tuple[float, str, str]:
    """Compute enhanced confidence score with bonuses and penalties.

    Returns (score, stars, label).
    """
    score = base_score

    # Bonuses
    if tech.mtf_aligned:
        score += 10
    if sector_adj > 0:
        score += 5
    if roe_top_30:
        score += 5
    if inst_buy_days >= 3:
        score += 5

    # Penalties
    if tech.weekly_trend == "down" and tech.ema_50 <= tech.ema_200:
        score -= 10
    if sector_adj < 0:
        score -= 5
    if corr_penalty:
        score -= 5
    if is_leverage_etf and leverage_hold_days > 3:
        score -= 5

    score = max(0, min(100, score))

    if score >= 90:
        stars = "\u2605\u2605\u2605\u2605\u2605"
        label = "강한 매수"
    elif score >= 80:
        stars = "\u2605\u2605\u2605\u2605\u2606"
        label = "매수 추천"
    elif score >= 70:
        stars = "\u2605\u2605\u2605\u2606\u2606"
        label = "관심"
    elif score >= 60:
        stars = "\u2605\u2605\u2606\u2606\u2606"
        label = "약한 관심"
    else:
        stars = "\u2605\u2606\u2606\u2606\u2606"
        label = "대기"

    return round(score, 1), stars, label


def evaluate_all_strategies(
    ticker: str,
    name: str,
    score: ScoreBreakdown,
    tech: TechnicalIndicators,
    flow: FlowData,
    macro: MacroSnapshot,
    info_dict: dict | None = None,
    sector: str = "",
    rs_rank: int = 0,
    rs_total: int = 1,
) -> list[StrategySignal]:
    """Run all applicable strategies for a ticker and return matching signals."""
    signals = []
    info_dict = info_dict or {}

    result_a = evaluate_strategy_a(ticker, name, score, tech, flow, macro)
    if result_a:
        signals.append(result_a)

    result_b = evaluate_strategy_b(ticker, name, tech, macro)
    if result_b:
        signals.append(result_b)

    result_c = evaluate_strategy_c(ticker, name, info_dict, tech)
    if result_c:
        signals.append(result_c)

    result_d = evaluate_strategy_d(ticker, name, tech, macro, sector)
    if result_d:
        signals.append(result_d)

    result_e = evaluate_strategy_e(ticker, name, tech, macro)
    if result_e:
        signals.append(result_e)

    result_f = evaluate_strategy_f(ticker, name, tech, rs_rank, rs_total)
    if result_f:
        signals.append(result_f)

    result_g = evaluate_strategy_g(ticker, name, tech)
    if result_g:
        signals.append(result_g)

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


def get_regime_mode(macro: MacroSnapshot) -> dict:
    """Determine market mode based on macro regime."""
    if macro.regime == "risk_off" or macro.vix > 25:
        return {
            "mode": "defense",
            "emoji": "\U0001f6e1\ufe0f",
            "label": "방어 모드",
            "message": "지금은 사지 마세요. 인버스로 헷징하세요",
            "allocations": {
                "A": 5, "B": 25, "C": 15, "D": 5,
                "E": 15, "F": 0, "G": 0, "cash": 35,
            },
        }
    elif macro.regime == "risk_on" or macro.vix < 15:
        return {
            "mode": "attack",
            "emoji": "\U0001f680",
            "label": "공격 모드",
            "message": "시장이 좋습니다. 적극 매수 구간",
            "allocations": {
                "A": 20, "B": 15, "C": 10, "D": 15,
                "E": 10, "F": 20, "G": 5, "cash": 5,
            },
        }
    else:
        return {
            "mode": "balanced",
            "emoji": "\u2696\ufe0f",
            "label": "균형 모드",
            "message": "개별종목 반등 + 장기 적립식 병행",
            "allocations": {
                "A": 15, "B": 10, "C": 20, "D": 10,
                "E": 15, "F": 10, "G": 5, "cash": 15,
            },
        }
