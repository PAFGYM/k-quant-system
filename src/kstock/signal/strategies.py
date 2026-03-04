"""Multi-strategy system for K-Quant v2.5.

Strategy A: Foreign oversold bounce (short-term, individual stocks)
Strategy B: ETF leverage swing (ultra-short, leveraged/inverse ETFs) - enhanced
Strategy C: Long-term quality accumulation (dividend ETFs, value stocks)
Strategy D: Sector rotation (sector ETFs)
Strategy E: Global diversification (US ETFs, gold)
Strategy F: Momentum (golden cross + relative strength)
Strategy G: Breakout (52-week high / 20-day high breakout)
Strategy H: Volatility mean reversion (변동성 역전 전략)
Strategy I: Earnings momentum (실적 모멘텀 전략)
Strategy J: Mean reversion breakout (평균회귀 돌파 전략)
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


class RegimeAdjuster:
    """VIX/매크로 기반 동적 임계값 조정기.

    VIX < 15 (risk_on): 공격적 — RSI 과매도 35, BUY 임계값 -10
    VIX 15-25 (neutral): 기본값 유지
    VIX 25-35 (risk_off): 보수적 — RSI 과매도 25, BUY 임계값 +10
    VIX > 35 (panic): 극보수 — 신규 매수 거의 차단
    """

    @staticmethod
    def get_adjustments(macro) -> dict:
        """매크로 상태에 따른 조정값 딕셔너리 반환."""
        vix = getattr(macro, 'vix', 20)
        regime = getattr(macro, 'regime', 'neutral')

        if vix > 35 or regime == "panic":
            return {
                "rsi_oversold": 20,       # 극과매도에서만 반등 매수
                "rsi_near": 30,
                "buy_threshold_adj": 20,   # BUY 기준 대폭 상향
                "score_multiplier": 0.7,   # 전체 점수 30% 감소
                "bb_lower": 0.10,
                "vix_inverse_threshold": 30,
                "label": "극보수",
            }
        elif vix > 25 or regime == "risk_off":
            return {
                "rsi_oversold": 25,
                "rsi_near": 35,
                "buy_threshold_adj": 10,
                "score_multiplier": 0.85,
                "bb_lower": 0.15,
                "vix_inverse_threshold": 25,
                "label": "보수적",
            }
        elif vix < 15 or regime == "risk_on":
            return {
                "rsi_oversold": 35,
                "rsi_near": 45,
                "buy_threshold_adj": -10,
                "score_multiplier": 1.15,
                "bb_lower": 0.25,
                "vix_inverse_threshold": 20,
                "label": "공격적",
            }
        else:
            return {
                "rsi_oversold": 30,
                "rsi_near": 40,
                "buy_threshold_adj": 0,
                "score_multiplier": 1.0,
                "bb_lower": 0.20,
                "vix_inverse_threshold": 25,
                "label": "기본",
            }


@dataclass
class StrategySignal:
    """Result of a strategy evaluation."""

    strategy: str  # A, B, C, D, E, F, G, H, I, J
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
    "H": {"name": "변동성 역전", "emoji": "\U0001f300", "target": 12.0, "stop": -8.0, "days": "3~10일"},
    "I": {"name": "실적 모멘텀", "emoji": "\U0001f4c8", "target": 15.0, "stop": -7.0, "days": "20~60일"},
    "J": {"name": "평균회귀 돌파", "emoji": "\U0001f3af", "target": 10.0, "stop": -5.0, "days": "5~15일"},
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
    adj = RegimeAdjuster.get_adjustments(macro)

    if ticker in ALL_ETFS:
        return None

    reasons = []
    sig_score = 0.0

    if flow.foreign_net_buy_days <= -3:
        sig_score += 30
        reasons.append(f"외인 매도 {abs(flow.foreign_net_buy_days)}일 연속")

    if tech.rsi <= adj["rsi_oversold"]:
        sig_score += 25
        reasons.append(f"RSI {tech.rsi:.1f} 과매도")
    elif tech.rsi <= adj["rsi_near"]:
        sig_score += 10
        reasons.append(f"RSI {tech.rsi:.1f} 근접")

    if flow.institution_net_buy_days >= 2:
        sig_score += 20
        reasons.append(f"기관 순매수 {flow.institution_net_buy_days}일")

    if tech.bb_pctb <= adj["bb_lower"]:
        sig_score += 15
        reasons.append("볼린저밴드 하단 터치")

    if tech.macd_signal_cross == 1:
        sig_score += 10
        reasons.append("MACD 골든크로스")

    if sig_score < 40:
        return None

    sig_score *= adj["score_multiplier"]
    reasons.append(f"시장 레짐: {adj['label']}")

    buy_adj = adj["buy_threshold_adj"]
    action = "BUY" if sig_score >= 65 + buy_adj else "WATCH" if sig_score >= 50 + buy_adj else "HOLD"

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
    adj = RegimeAdjuster.get_adjustments(macro)

    is_leverage = ticker in LEVERAGE_ETFS
    is_inverse = ticker in INVERSE_ETFS
    if not is_leverage and not is_inverse:
        return None

    reasons = []
    sig_score = 0.0

    if is_inverse and not is_leverage:
        if macro.vix > adj["vix_inverse_threshold"]:
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
        if tech.bb_squeeze and tech.bb_pctb > 0.5:
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

    if div_yield >= 2.5:
        sig_score += 25
        reasons.append(f"배당수익률 {div_yield:.1f}%")
    elif div_yield >= 1.5:
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

    # 장기투자: 장기 추세 확인 (하락 추세에서 매집)
    if tech.ema_50 > tech.ema_200 > 0:
        sig_score += 10
        reasons.append("장기 상승 추세 확인")
    elif tech.rsi <= 35:
        sig_score += 5
        reasons.append(f"RSI {tech.rsi:.1f} 과매도 매집 기회")

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
    macro: MacroSnapshot | None = None,
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

    # Apply regime adjustments if macro data available
    if macro is not None:
        adj = RegimeAdjuster.get_adjustments(macro)
        sig_score *= adj["score_multiplier"]
        reasons.append(f"시장 레짐: {adj['label']}")
        buy_adj = adj["buy_threshold_adj"]
    else:
        buy_adj = 0

    action = "BUY" if sig_score >= 65 + buy_adj else "WATCH" if sig_score >= 50 + buy_adj else "HOLD"

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
    macro: MacroSnapshot | None = None,
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
    current = getattr(tech, 'close', 0) or tech.ema_50  # prefer actual close price

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

    # Apply regime adjustments if macro data available
    if macro is not None:
        adj = RegimeAdjuster.get_adjustments(macro)
        sig_score *= adj["score_multiplier"]
        reasons.append(f"시장 레짐: {adj['label']}")
        buy_adj = adj["buy_threshold_adj"]
    else:
        buy_adj = 0

    action = "BUY" if sig_score >= 60 + buy_adj else "WATCH"

    return StrategySignal(
        strategy="G", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=min(sig_score / 100, 1.0),
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_h(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
    macro: MacroSnapshot,
) -> StrategySignal | None:
    """Strategy H: Volatility mean reversion (변동성 역전 전략).

    Targets individual stocks and ETFs when volatility is extremely high,
    looking for reversal candidates at volatility peaks.

    Conditions:
    - ATR% > 5.0 AND RSI < 35 → reversal candidate
    - Bollinger %B < 0.1 → extreme oversold bounce
    - VIX > 25 but declining (macro.spx_change_pct > 0) → volatility peak
    - Score: count of conditions met * 30 (max 90)
    - Stop: -8%, Target: +12%, Holding: 3~10 days
    """
    meta = STRATEGY_META["H"]
    regime = getattr(macro, 'regime', 'neutral')

    reasons = []
    conditions_met = 0

    # Condition 1: ATR% > 5.0 AND RSI < 35 → reversal candidate
    if tech.atr_pct > 5.0 and tech.rsi < 35:
        conditions_met += 1
        reasons.append(f"ATR% {tech.atr_pct:.1f}% + RSI {tech.rsi:.1f} (역전 후보)")

    # Condition 2: Bollinger %B < 0.1 → extreme oversold bounce
    if tech.bb_pctb < 0.1:
        conditions_met += 1
        reasons.append(f"BB %B {tech.bb_pctb:.3f} (극단적 과매도)")

    # Condition 3: VIX > 25 but declining (SPX positive) → volatility peak
    if macro.vix > 25 and macro.spx_change_pct > 0:
        conditions_met += 1
        reasons.append(f"VIX {macro.vix:.1f} 고점 하락 전환 (S&P500 {macro.spx_change_pct:+.1f}%)")

    sig_score = min(conditions_met * 30, 90)

    if sig_score < 30:
        return None

    # Regime filtering: skip in risk_on if score < 70 (works best in risk_off/panic)
    if regime == "risk_on" and sig_score < 70:
        return None

    reasons.append(f"충족 조건: {conditions_met}/3")

    # Confidence: 0.6 base + 0.1 per extra condition beyond first
    confidence = min(0.6 + max(0, conditions_met - 1) * 0.1, 1.0)

    action = "BUY" if sig_score >= 60 else "WATCH"

    if regime in ("risk_off", "panic"):
        reasons.append(f"시장 레짐: {'리스크오프' if regime == 'risk_off' else '패닉'} (변동성 역전 유리)")
    else:
        reasons.append(f"시장 레짐: {regime}")

    return StrategySignal(
        strategy="H", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=confidence,
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_i(
    ticker: str,
    name: str,
    info_dict: dict,
    tech: TechnicalIndicators,
    macro: MacroSnapshot,
) -> StrategySignal | None:
    """Strategy I: Earnings momentum (실적 모멘텀 전략).

    Targets individual stocks with strong earnings at reasonable prices.

    Conditions:
    - ROE > 15% AND PER < sector_per → quality at reasonable price
    - Revenue growth > 10% → growing business
    - Target price upside > 15% → analyst consensus bullish
    - Debt ratio < 100% → healthy balance sheet
    - EMA50 > EMA200 → uptrend confirmation
    - Score: count of conditions met * 20 (max 100)
    - Stop: -7%, Target: +15%, Holding: 20~60 days
    """
    meta = STRATEGY_META["I"]
    regime = getattr(macro, 'regime', 'neutral')

    # Skip ETFs — this strategy is for individual stocks
    if ticker in ALL_ETFS:
        return None

    # Skip in panic regime
    if regime == "panic":
        return None

    reasons = []
    conditions_met = 0

    roe = info_dict.get("roe", 0)
    per = info_dict.get("per", 0)
    sector_per = info_dict.get("sector_per", 15)
    revenue_growth = info_dict.get("revenue_growth", 0)
    target_price = info_dict.get("target_price", 0)
    current_price = info_dict.get("current_price", 0)
    debt_ratio = info_dict.get("debt_ratio", 0)

    # Condition 1: ROE > 15% AND PER < sector_per → quality at reasonable price
    if roe > 15 and 0 < per < sector_per:
        conditions_met += 1
        reasons.append(f"ROE {roe:.1f}% + PER {per:.1f} < 섹터 {sector_per:.1f} (우량 저평가)")

    # Condition 2: Revenue growth > 10%
    if revenue_growth > 10:
        conditions_met += 1
        reasons.append(f"매출 성장률 {revenue_growth:.1f}% (성장 기업)")

    # Condition 3: Target price upside > 15%
    if target_price > 0 and current_price > 0:
        upside = (target_price - current_price) / current_price * 100
        if upside > 15:
            conditions_met += 1
            reasons.append(f"목표가 괴리율 {upside:+.1f}% (애널리스트 긍정)")

    # Condition 4: Debt ratio < 100%
    if 0 < debt_ratio < 100:
        conditions_met += 1
        reasons.append(f"부채비율 {debt_ratio:.0f}% (재무건전)")

    # Condition 5: EMA50 > EMA200 → uptrend confirmation
    if tech.ema_50 > tech.ema_200 > 0:
        conditions_met += 1
        reasons.append("EMA50 > EMA200 (상승 추세 확인)")

    sig_score = min(conditions_met * 20, 100)

    if sig_score < 60:
        return None

    reasons.append(f"충족 조건: {conditions_met}/5")

    # Confidence: 0.5 base + 0.1 per extra condition beyond first
    confidence = min(0.5 + max(0, conditions_met - 1) * 0.1, 1.0)

    action = "BUY" if sig_score >= 60 else "WATCH"

    reasons.append(f"시장 레짐: {regime}")

    return StrategySignal(
        strategy="I", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=confidence,
        target_pct=meta["target"], stop_pct=meta["stop"],
        holding_days=meta["days"], reasons=reasons,
    )


def evaluate_strategy_j(
    ticker: str,
    name: str,
    tech: TechnicalIndicators,
    macro: MacroSnapshot,
) -> StrategySignal | None:
    """Strategy J: Mean reversion breakout (평균회귀 돌파 전략).

    Targets stocks with BB squeeze about to break out.

    Conditions:
    - BB squeeze detected (tech.bb_squeeze == True)
    - Volume ratio > 1.3 (increasing volume)
    - RSI between 40-60 (neutral, ready to move)
    - MACD signal cross == 1 (bullish momentum starting)
    - Price near MA20 (within 3%)
    - Score: count of conditions met * 25 (max 100)
    - Stop: -5%, Target: +10%, Holding: 5~15 days
    """
    meta = STRATEGY_META["J"]
    regime = getattr(macro, 'regime', 'neutral')

    # Works for stocks; skip pure leverage/inverse ETFs
    if ticker in LEVERAGE_ETFS or ticker in INVERSE_ETFS:
        return None

    reasons = []
    conditions_met = 0

    # Condition 1: BB squeeze detected
    if tech.bb_squeeze:
        conditions_met += 1
        reasons.append("BB 스퀴즈 감지 (밴드 압축)")

    # Condition 2: Volume ratio > 1.3
    if tech.volume_ratio > 1.3:
        conditions_met += 1
        reasons.append(f"거래량 증가 (평균 {tech.volume_ratio:.1f}배)")

    # Condition 3: RSI between 40-60 (neutral zone)
    if 40 <= tech.rsi <= 60:
        conditions_met += 1
        reasons.append(f"RSI {tech.rsi:.1f} (중립 구간, 방향 대기)")

    # Condition 4: MACD signal cross == 1 (bullish)
    if tech.macd_signal_cross == 1:
        conditions_met += 1
        reasons.append("MACD 골든크로스 (상승 모멘텀 시작)")

    # Condition 5: Price near MA20 (within 3%)
    if tech.ma20 > 0:
        close_price = getattr(tech, 'close', 0) or tech.ema_50
        if close_price > 0:
            distance_pct = abs(close_price - tech.ma20) / tech.ma20 * 100
            if distance_pct <= 3.0:
                conditions_met += 1
                reasons.append(f"MA20 근접 (괴리율 {distance_pct:.1f}%)")

    sig_score = min(conditions_met * 25, 100)

    # Regime filtering:
    # Best in neutral/risk_on, still works in risk_off with score >= 75
    if regime == "risk_off" and sig_score < 75:
        return None
    if regime == "panic":
        return None

    if sig_score < 50:
        return None

    reasons.append(f"충족 조건: {conditions_met}/5")

    # Confidence: 0.55 base + 0.1 per extra condition beyond first
    confidence = min(0.55 + max(0, conditions_met - 1) * 0.1, 1.0)

    action = "BUY" if sig_score >= 50 else "WATCH"

    if regime == "risk_on":
        reasons.append("시장 레짐: 리스크온 (돌파 유리)")
    elif regime == "neutral":
        reasons.append("시장 레짐: 중립")
    else:
        reasons.append(f"시장 레짐: {regime}")

    return StrategySignal(
        strategy="J", strategy_name=meta["name"], strategy_emoji=meta["emoji"],
        ticker=ticker, name=name, action=action,
        score=min(sig_score, 100), confidence=confidence,
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

    result_f = evaluate_strategy_f(ticker, name, tech, macro, rs_rank, rs_total)
    if result_f:
        signals.append(result_f)

    result_g = evaluate_strategy_g(ticker, name, tech, macro)
    if result_g:
        signals.append(result_g)

    result_h = evaluate_strategy_h(ticker, name, tech, macro)
    if result_h:
        signals.append(result_h)

    result_i = evaluate_strategy_i(ticker, name, info_dict, tech, macro)
    if result_i:
        signals.append(result_i)

    result_j = evaluate_strategy_j(ticker, name, tech, macro)
    if result_j:
        signals.append(result_j)

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


def get_regime_mode(macro: MacroSnapshot) -> dict:
    """Determine market mode based on macro regime."""
    if macro.vix > 35:
        return {
            "mode": "panic",
            "emoji": "\U0001f6a8",
            "label": "패닉 모드",
            "message": "극단적 공포. 신규 매수 전면 중단. 현금 + 인버스만",
            "allocations": {
                "A": 0, "B": 25, "C": 10, "D": 0,
                "E": 10, "F": 0, "G": 0, "H": 5, "I": 0, "J": 0, "cash": 50,
            },
        }
    elif macro.regime == "risk_off" or macro.vix > 25:
        return {
            "mode": "defense",
            "emoji": "\U0001f6e1\ufe0f",
            "label": "방어 모드",
            "message": "지금은 사지 마세요. 인버스로 헷징하세요",
            "allocations": {
                "A": 5, "B": 20, "C": 15, "D": 5,
                "E": 10, "F": 0, "G": 0, "H": 5, "I": 5, "J": 0, "cash": 35,
            },
        }
    elif macro.regime == "risk_on" or macro.vix < 15:
        return {
            "mode": "attack",
            "emoji": "\U0001f680",
            "label": "공격 모드",
            "message": "시장이 좋습니다. 적극 매수 구간",
            "allocations": {
                "A": 15, "B": 10, "C": 5, "D": 10,
                "E": 5, "F": 15, "G": 5, "H": 5, "I": 15, "J": 10, "cash": 5,
            },
        }
    else:
        return {
            "mode": "balanced",
            "emoji": "\u2696\ufe0f",
            "label": "균형 모드",
            "message": "개별종목 반등 + 장기 적립식 병행",
            "allocations": {
                "A": 10, "B": 10, "C": 15, "D": 10,
                "E": 10, "F": 5, "G": 5, "H": 5, "I": 10, "J": 5, "cash": 15,
            },
        }
