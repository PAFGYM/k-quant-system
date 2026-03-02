"""옵션/파생상품 분석 모듈.

Black-Scholes 가격 산정, Greeks 계산, 내재변동성 역추산,
옵션 체인 분석, 변동성 표면 구축, 전략 분석 등을 제공한다.

사용:
    from kstock.signal.options_analytics import (
        black_scholes, compute_greeks, implied_volatility,
        analyze_iv, analyze_option_chain, build_volatility_surface,
        analyze_strategy, format_greeks, format_option_analysis,
    )
    price = black_scholes(S=100, K=100, T=0.25, r=0.03, sigma=0.2)
    greeks = compute_greeks(S=100, K=100, T=0.25, r=0.03, sigma=0.2)

모든 함수는 순수 계산이며 외부 API 호출 없음.
numpy/scipy만 사용.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import norm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IV_INITIAL_GUESS = 0.2
"""Newton-Raphson IV 초기값 (20%)."""

IV_MAX_ITERATIONS = 100
"""Newton-Raphson 최대 반복 횟수."""

IV_CONVERGENCE_THRESHOLD = 0.001
"""Newton-Raphson 수렴 조건 (|price_diff| < 0.001)."""

IV_MIN = 0.0001
"""내재변동성 하한."""

IV_MAX = 5.0
"""내재변동성 상한 (500%)."""

UNUSUAL_VOLUME_MULTIPLE = 3.0
"""이상 거래량 탐지 기준 (평균 대비 배수)."""

TRADING_DAYS_PER_YEAR = 252
"""연간 거래일 수."""

MONEYNESS_ATM_THRESHOLD = 0.02
"""ATM 판단 임계값 (2%)."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OptionGreeks:
    """옵션 Greeks (1차 + 2차).

    Attributes:
        delta: 기초자산 가격 변화에 대한 옵션 가격 민감도.
        gamma: Delta의 변화율.
        vega: 변동성 변화에 대한 옵션 가격 민감도.
        theta: 시간 경과에 대한 옵션 가격 민감도 (일 단위).
        rho: 금리 변화에 대한 옵션 가격 민감도.
        charm: Delta의 시간 감쇠 (delta decay).
        vanna: Delta의 변동성 민감도 (d(delta)/d(sigma)).
        volga: Vega의 변동성 민감도 (d(vega)/d(sigma)).
    """
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    charm: float = 0.0
    vanna: float = 0.0
    volga: float = 0.0


@dataclass
class OptionPrice:
    """옵션 가격 정보.

    Attributes:
        theoretical_price: Black-Scholes 이론 가격.
        intrinsic_value: 내재 가치.
        time_value: 시간 가치.
        moneyness: "ITM", "ATM", "OTM".
    """
    theoretical_price: float = 0.0
    intrinsic_value: float = 0.0
    time_value: float = 0.0
    moneyness: str = "OTM"


@dataclass
class ImpliedVolatility:
    """내재변동성 분석 결과.

    Attributes:
        iv: 현재 내재변동성.
        iv_percentile_1y: 1년 IV 백분위 (0~1).
        iv_rank_1y: 1년 IV 랭크 (0~1).
        skew_25d: 25-delta 스큐 (put IV - call IV).
        term_structure: 만기별 IV [(days_to_expiry, iv), ...].
    """
    iv: float = 0.0
    iv_percentile_1y: float = 0.0
    iv_rank_1y: float = 0.0
    skew_25d: float = 0.0
    term_structure: List[Tuple[int, float]] = field(default_factory=list)


@dataclass
class OptionChainAnalysis:
    """옵션 체인 분석 결과.

    Attributes:
        put_call_ratio: Put/Call 미결제약정 비율.
        max_pain_strike: Max Pain 행사가.
        total_oi_calls: 콜 총 미결제약정.
        total_oi_puts: 풋 총 미결제약정.
        dominant_sentiment: "bullish", "bearish", "neutral".
        unusual_activity: 이상 거래 목록.
    """
    put_call_ratio: float = 0.0
    max_pain_strike: float = 0.0
    total_oi_calls: int = 0
    total_oi_puts: int = 0
    dominant_sentiment: str = "neutral"
    unusual_activity: List[Dict] = field(default_factory=list)


@dataclass
class VolatilitySurface:
    """변동성 표면.

    Attributes:
        strikes: 행사가 목록.
        expirations: 만기(일) 목록.
        iv_matrix: 2D IV 행렬 [expiration_idx][strike_idx].
        atm_vol: ATM 변동성.
        skew_slope: 스큐 기울기 (IV vs moneyness 선형회귀).
    """
    strikes: List[float] = field(default_factory=list)
    expirations: List[int] = field(default_factory=list)
    iv_matrix: List[List[float]] = field(default_factory=list)
    atm_vol: float = 0.0
    skew_slope: float = 0.0


@dataclass
class OptionStrategy:
    """옵션 전략 분석 결과.

    Attributes:
        name: 전략 이름.
        legs: 전략 다리 목록.
        max_profit: 최대 수익.
        max_loss: 최대 손실.
        breakeven: 손익분기점 목록.
        greeks_net: 순 Greeks.
    """
    name: str = ""
    legs: List[Dict] = field(default_factory=list)
    max_profit: float = 0.0
    max_loss: float = 0.0
    breakeven: List[float] = field(default_factory=list)
    greeks_net: OptionGreeks = field(default_factory=OptionGreeks)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d1 계산."""
    return (math.log(S / K) + (r + sigma * sigma / 2.0) * T) / (sigma * math.sqrt(T))


def _d2(d1_val: float, sigma: float, T: float) -> float:
    """Black-Scholes d2 계산."""
    return d1_val - sigma * math.sqrt(T)


def _validate_inputs(S: float, K: float, T: float, sigma: float) -> None:
    """기본 입력값 검증."""
    if S <= 0:
        raise ValueError(f"기초자산 가격(S)은 양수여야 합니다: {S}")
    if K <= 0:
        raise ValueError(f"행사가(K)는 양수여야 합니다: {K}")
    if T <= 0:
        raise ValueError(f"잔존만기(T)는 양수여야 합니다: {T}")
    if sigma <= 0:
        raise ValueError(f"변동성(sigma)은 양수여야 합니다: {sigma}")


def _classify_moneyness(S: float, K: float, option_type: str) -> str:
    """내가격/등가격/외가격 판단."""
    ratio = abs(S - K) / S
    if ratio < MONEYNESS_ATM_THRESHOLD:
        return "ATM"
    if option_type == "call":
        return "ITM" if S > K else "OTM"
    else:
        return "ITM" if S < K else "OTM"


# ---------------------------------------------------------------------------
# 1. Black-Scholes 가격 산정
# ---------------------------------------------------------------------------

def black_scholes(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> OptionPrice:
    """Black-Scholes 모형으로 유럽형 옵션 이론 가격을 계산한다.

    Args:
        S: 기초자산 현재 가격.
        K: 행사가.
        T: 잔존만기 (연 단위, 예: 0.25 = 3개월).
        r: 무위험 이자율 (연율, 예: 0.03 = 3%).
        sigma: 변동성 (연율, 예: 0.2 = 20%).
        option_type: "call" 또는 "put".

    Returns:
        OptionPrice 데이터클래스.

    Raises:
        ValueError: 잘못된 입력값.
    """
    _validate_inputs(S, K, T, sigma)
    option_type = option_type.lower()
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type은 'call' 또는 'put'이어야 합니다: {option_type}")

    d1_val = _d1(S, K, T, r, sigma)
    d2_val = _d2(d1_val, sigma, T)
    discount = K * math.exp(-r * T)

    if option_type == "call":
        price = S * norm.cdf(d1_val) - discount * norm.cdf(d2_val)
        intrinsic = max(S - K, 0.0)
    else:
        price = discount * norm.cdf(-d2_val) - S * norm.cdf(-d1_val)
        intrinsic = max(K - S, 0.0)

    time_val = price - intrinsic
    moneyness = _classify_moneyness(S, K, option_type)

    logger.debug(
        "BS price: S=%.2f K=%.2f T=%.4f r=%.4f sigma=%.4f type=%s -> %.4f (%s)",
        S, K, T, r, sigma, option_type, price, moneyness,
    )

    return OptionPrice(
        theoretical_price=price,
        intrinsic_value=intrinsic,
        time_value=time_val,
        moneyness=moneyness,
    )


# ---------------------------------------------------------------------------
# 2. Greeks 계산
# ---------------------------------------------------------------------------

def compute_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> OptionGreeks:
    """옵션 Greeks를 계산한다 (1차 + 2차).

    Args:
        S: 기초자산 현재 가격.
        K: 행사가.
        T: 잔존만기 (연).
        r: 무위험 이자율 (연율).
        sigma: 변동성 (연율).
        option_type: "call" 또는 "put".

    Returns:
        OptionGreeks 데이터클래스.
    """
    _validate_inputs(S, K, T, sigma)
    option_type = option_type.lower()

    sqrt_T = math.sqrt(T)
    d1_val = _d1(S, K, T, r, sigma)
    d2_val = _d2(d1_val, sigma, T)

    n_d1 = norm.pdf(d1_val)  # 표준정규분포 확률밀도
    N_d1 = norm.cdf(d1_val)
    N_neg_d1 = norm.cdf(-d1_val)
    discount = math.exp(-r * T)

    # --- 1차 Greeks ---

    # Gamma (call/put 동일)
    gamma = n_d1 / (S * sigma * sqrt_T)

    # Vega (call/put 동일, 1% 변동 기준)
    vega = S * n_d1 * sqrt_T / 100.0

    if option_type == "call":
        delta = N_d1
        theta_annual = (
            -(S * n_d1 * sigma) / (2.0 * sqrt_T)
            - r * K * discount * norm.cdf(d2_val)
        )
        rho = K * T * discount * norm.cdf(d2_val) / 100.0
    else:
        delta = N_d1 - 1.0
        theta_annual = (
            -(S * n_d1 * sigma) / (2.0 * sqrt_T)
            + r * K * discount * norm.cdf(-d2_val)
        )
        rho = -K * T * discount * norm.cdf(-d2_val) / 100.0

    # Theta (일 단위 변환)
    theta = theta_annual / TRADING_DAYS_PER_YEAR

    # --- 2차 Greeks ---

    # Charm (delta decay): d(delta)/d(T)
    charm_val = -n_d1 * (
        2.0 * (r - 0.0) * T - d2_val * sigma * sqrt_T
    ) / (2.0 * T * sigma * sqrt_T)

    # Vanna: d(delta)/d(sigma) = d(vega)/d(S)
    vanna_val = -n_d1 * d2_val / sigma

    # Volga (vomma): d(vega)/d(sigma)
    volga_val = S * n_d1 * sqrt_T * d1_val * d2_val / (sigma * 100.0)

    logger.debug(
        "Greeks: S=%.2f K=%.2f T=%.4f type=%s -> delta=%.4f gamma=%.6f vega=%.4f theta=%.4f",
        S, K, T, option_type, delta, gamma, vega * 100, theta,
    )

    return OptionGreeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        charm=charm_val,
        vanna=vanna_val,
        volga=volga_val,
    )


# ---------------------------------------------------------------------------
# 3. 내재변동성 (Newton-Raphson)
# ---------------------------------------------------------------------------

def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
) -> float:
    """Newton-Raphson 반복법으로 내재변동성을 역추산한다.

    Args:
        market_price: 시장 옵션 가격.
        S: 기초자산 가격.
        K: 행사가.
        T: 잔존만기 (연).
        r: 무위험 이자율 (연율).
        option_type: "call" 또는 "put".

    Returns:
        내재변동성 (연율, 예: 0.25 = 25%).

    Raises:
        ValueError: 수렴 실패.
    """
    if market_price <= 0:
        raise ValueError(f"시장 가격은 양수여야 합니다: {market_price}")
    if S <= 0 or K <= 0 or T <= 0:
        raise ValueError("S, K, T는 모두 양수여야 합니다.")

    sigma = IV_INITIAL_GUESS

    for i in range(IV_MAX_ITERATIONS):
        bs = black_scholes(S, K, T, r, sigma, option_type)
        price_diff = bs.theoretical_price - market_price

        if abs(price_diff) < IV_CONVERGENCE_THRESHOLD:
            logger.debug("IV 수렴: sigma=%.6f (반복 %d회)", sigma, i + 1)
            return sigma

        # Vega (derivative for Newton-Raphson), 비정규화 버전
        sqrt_T = math.sqrt(T)
        d1_val = _d1(S, K, T, r, sigma)
        vega_raw = S * norm.pdf(d1_val) * sqrt_T

        if abs(vega_raw) < 1e-12:
            # Vega 너무 작으면 bisection 한 스텝
            if price_diff > 0:
                sigma *= 0.5
            else:
                sigma *= 1.5
            sigma = max(IV_MIN, min(IV_MAX, sigma))
            continue

        sigma = sigma - price_diff / vega_raw
        sigma = max(IV_MIN, min(IV_MAX, sigma))

    logger.warning("IV 수렴 실패: market_price=%.4f S=%.2f K=%.2f T=%.4f (최종 sigma=%.6f)",
                   market_price, S, K, T, sigma)
    return sigma


# ---------------------------------------------------------------------------
# 4. IV 분석
# ---------------------------------------------------------------------------

def analyze_iv(
    iv_history: List[float],
    current_iv: float,
    put_25d_iv: Optional[float] = None,
    call_25d_iv: Optional[float] = None,
    term_data: Optional[List[Tuple[int, float]]] = None,
) -> ImpliedVolatility:
    """내재변동성 분석 (percentile, rank, skew, term structure).

    Args:
        iv_history: 과거 IV 시계열 (1년).
        current_iv: 현재 IV.
        put_25d_iv: 25-delta 풋 IV (optional).
        call_25d_iv: 25-delta 콜 IV (optional).
        term_data: 만기별 IV 데이터 [(days, iv), ...] (optional).

    Returns:
        ImpliedVolatility 데이터클래스.
    """
    if not iv_history:
        logger.warning("IV 히스토리가 비어있습니다.")
        return ImpliedVolatility(iv=current_iv)

    arr = np.array(iv_history, dtype=float)
    iv_min = float(np.min(arr))
    iv_max = float(np.max(arr))

    # IV Percentile: 현재 IV보다 낮은 과거 관측값의 비율
    percentile = float(np.sum(arr < current_iv)) / len(arr)

    # IV Rank: 범위 내 상대 위치
    if iv_max - iv_min > 1e-12:
        rank = (current_iv - iv_min) / (iv_max - iv_min)
    else:
        rank = 0.5

    # 25-delta skew
    skew = 0.0
    if put_25d_iv is not None and call_25d_iv is not None:
        skew = put_25d_iv - call_25d_iv

    # Term structure
    term = term_data if term_data else []

    logger.debug(
        "IV 분석: current=%.4f percentile=%.2f rank=%.2f skew=%.4f",
        current_iv, percentile, rank, skew,
    )

    return ImpliedVolatility(
        iv=current_iv,
        iv_percentile_1y=max(0.0, min(1.0, percentile)),
        iv_rank_1y=max(0.0, min(1.0, rank)),
        skew_25d=skew,
        term_structure=term,
    )


# ---------------------------------------------------------------------------
# 5. 옵션 체인 분석
# ---------------------------------------------------------------------------

def _compute_max_pain(chain: List[Dict]) -> float:
    """Max Pain 행사가 계산.

    Max Pain = 옵션 매수자의 총 손실이 최대(= 매도자 이익 최대)인 행사가.
    즉, 모든 옵션 보유자의 합산 이익이 최소인 행사가.
    """
    strikes = sorted(set(item["strike"] for item in chain))
    if not strikes:
        return 0.0

    calls = [item for item in chain if item.get("type", "").lower() == "call"]
    puts = [item for item in chain if item.get("type", "").lower() == "put"]

    min_pain = float("inf")
    max_pain_strike = strikes[0]

    for test_price in strikes:
        total_pain = 0.0

        # 콜 보유자 손실 합산
        for c in calls:
            oi = c.get("oi", 0)
            intrinsic = max(test_price - c["strike"], 0.0)
            total_pain += intrinsic * oi

        # 풋 보유자 손실 합산
        for p in puts:
            oi = p.get("oi", 0)
            intrinsic = max(p["strike"] - test_price, 0.0)
            total_pain += intrinsic * oi

        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = test_price

    return max_pain_strike


def analyze_option_chain(chain: List[Dict]) -> OptionChainAnalysis:
    """옵션 체인을 분석한다.

    Args:
        chain: 옵션 데이터 목록.
            [{"strike": K, "type": "call"/"put", "oi": int, "volume": int,
              "iv": float, ...}, ...]

    Returns:
        OptionChainAnalysis 데이터클래스.
    """
    if not chain:
        logger.warning("빈 옵션 체인")
        return OptionChainAnalysis()

    calls = [item for item in chain if item.get("type", "").lower() == "call"]
    puts = [item for item in chain if item.get("type", "").lower() == "put"]

    total_oi_calls = sum(item.get("oi", 0) for item in calls)
    total_oi_puts = sum(item.get("oi", 0) for item in puts)

    # Put-Call Ratio
    pc_ratio = total_oi_puts / total_oi_calls if total_oi_calls > 0 else 0.0

    # Sentiment 판단
    if pc_ratio > 1.2:
        sentiment = "bearish"
    elif pc_ratio < 0.8:
        sentiment = "bullish"
    else:
        sentiment = "neutral"

    # Max Pain
    max_pain = _compute_max_pain(chain)

    # Unusual Activity: volume > 3x 평균
    all_volumes = [item.get("volume", 0) for item in chain if item.get("volume", 0) > 0]
    avg_volume = np.mean(all_volumes) if all_volumes else 0.0
    unusual = []

    for item in chain:
        vol = item.get("volume", 0)
        if avg_volume > 0 and vol > UNUSUAL_VOLUME_MULTIPLE * avg_volume:
            unusual.append({
                "strike": item.get("strike"),
                "type": item.get("type"),
                "volume": vol,
                "oi": item.get("oi", 0),
                "volume_ratio": round(vol / avg_volume, 1),
            })

    logger.debug(
        "체인 분석: P/C=%.2f max_pain=%.0f calls_oi=%d puts_oi=%d unusual=%d건",
        pc_ratio, max_pain, total_oi_calls, total_oi_puts, len(unusual),
    )

    return OptionChainAnalysis(
        put_call_ratio=round(pc_ratio, 4),
        max_pain_strike=max_pain,
        total_oi_calls=total_oi_calls,
        total_oi_puts=total_oi_puts,
        dominant_sentiment=sentiment,
        unusual_activity=unusual,
    )


# ---------------------------------------------------------------------------
# 6. 변동성 표면
# ---------------------------------------------------------------------------

def build_volatility_surface(
    chain_data: Dict[int, List[Dict]],
    S: float,
    r: float,
) -> VolatilitySurface:
    """만기별 옵션 체인 데이터로 변동성 표면을 구축한다.

    Args:
        chain_data: {만기일수: [{"strike": K, "type": "call", "price": float, ...}, ...]}.
        S: 기초자산 현재 가격.
        r: 무위험 이자율.

    Returns:
        VolatilitySurface 데이터클래스.
    """
    if not chain_data:
        logger.warning("빈 체인 데이터로 변동성 표면 구축 불가")
        return VolatilitySurface()

    expirations = sorted(chain_data.keys())

    # 모든 행사가 수집
    all_strikes: set = set()
    for items in chain_data.values():
        for item in items:
            all_strikes.add(item["strike"])
    strikes = sorted(all_strikes)

    if not strikes:
        return VolatilitySurface()

    # IV 행렬 계산
    iv_matrix: List[List[float]] = []
    for exp_days in expirations:
        T = exp_days / 365.0
        row: List[float] = []
        items = chain_data[exp_days]
        price_map: Dict[float, Dict] = {}
        for item in items:
            price_map[item["strike"]] = item

        for K in strikes:
            if K in price_map and price_map[K].get("price", 0) > 0:
                item = price_map[K]
                opt_type = item.get("type", "call").lower()
                try:
                    iv = implied_volatility(
                        item["price"], S, K, T, r, opt_type,
                    )
                    row.append(round(iv, 6))
                except (ValueError, ZeroDivisionError):
                    row.append(0.0)
            else:
                row.append(0.0)
        iv_matrix.append(row)

    # ATM vol: strike 가장 가까운 것
    atm_idx = int(np.argmin([abs(k - S) for k in strikes]))
    atm_vol = 0.0
    for row in iv_matrix:
        if row[atm_idx] > 0:
            atm_vol = row[atm_idx]
            break

    # Skew slope: 선형회귀 (moneyness vs IV)
    moneyness_vals: List[float] = []
    iv_vals: List[float] = []
    # 첫 번째 유효 만기 사용
    for row in iv_matrix:
        for i, K in enumerate(strikes):
            if row[i] > 0:
                m = math.log(K / S)
                moneyness_vals.append(m)
                iv_vals.append(row[i])
        if moneyness_vals:
            break

    skew_slope = 0.0
    if len(moneyness_vals) >= 2:
        coeffs = np.polyfit(moneyness_vals, iv_vals, 1)
        skew_slope = float(coeffs[0])

    logger.debug(
        "변동성 표면: %d만기 x %d행사가, ATM vol=%.4f, skew=%.4f",
        len(expirations), len(strikes), atm_vol, skew_slope,
    )

    return VolatilitySurface(
        strikes=strikes,
        expirations=expirations,
        iv_matrix=iv_matrix,
        atm_vol=atm_vol,
        skew_slope=round(skew_slope, 6),
    )


# ---------------------------------------------------------------------------
# 7. 전략 분석
# ---------------------------------------------------------------------------

_STRATEGY_PATTERNS = {
    "covered_call": [
        {"type": "call", "action": "sell"},
    ],
    "protective_put": [
        {"type": "put", "action": "buy"},
    ],
    "straddle": [
        {"type": "call", "action": "buy"},
        {"type": "put", "action": "buy"},
    ],
    "strangle": [
        {"type": "call", "action": "buy"},
        {"type": "put", "action": "buy"},
    ],
    "butterfly": [
        {"type": "call", "action": "buy"},
        {"type": "call", "action": "sell"},
        {"type": "call", "action": "buy"},
    ],
    "iron_condor": [
        {"type": "put", "action": "buy"},
        {"type": "put", "action": "sell"},
        {"type": "call", "action": "sell"},
        {"type": "call", "action": "buy"},
    ],
}


def _identify_strategy(legs: List[Dict]) -> str:
    """다리 구성으로 전략 이름 식별."""
    n = len(legs)
    types_actions = [(l.get("type", "").lower(), l.get("action", "").lower()) for l in legs]
    strikes = [l.get("strike", 0) for l in legs]

    if n == 1:
        t, a = types_actions[0]
        if t == "call" and a == "sell":
            return "covered_call"
        if t == "put" and a == "buy":
            return "protective_put"

    if n == 2:
        t0, a0 = types_actions[0]
        t1, a1 = types_actions[1]
        if a0 == "buy" and a1 == "buy":
            if t0 == "call" and t1 == "put" or t0 == "put" and t1 == "call":
                if strikes[0] == strikes[1]:
                    return "straddle"
                return "strangle"

    if n == 3:
        # Butterfly: buy-sell-buy 같은 타입
        if all(t == types_actions[0][0] for t, _ in types_actions):
            actions = [a for _, a in types_actions]
            if actions == ["buy", "sell", "buy"]:
                return "butterfly"

    if n == 4:
        # Iron condor: buy put, sell put, sell call, buy call
        sorted_legs = sorted(legs, key=lambda x: x.get("strike", 0))
        st = [(l.get("type", "").lower(), l.get("action", "").lower()) for l in sorted_legs]
        if (st[0] == ("put", "buy") and st[1] == ("put", "sell")
                and st[2] == ("call", "sell") and st[3] == ("call", "buy")):
            return "iron_condor"

    return "custom"


def _compute_payoff_at_expiry(legs: List[Dict], S_expiry: float) -> float:
    """만기 시 특정 기초자산 가격에서 페이오프 계산."""
    total = 0.0
    for leg in legs:
        K = leg.get("strike", 0)
        qty = leg.get("qty", 1)
        action = leg.get("action", "buy").lower()
        opt_type = leg.get("type", "call").lower()
        premium = leg.get("premium", 0.0)

        if opt_type == "call":
            intrinsic = max(S_expiry - K, 0.0)
        else:
            intrinsic = max(K - S_expiry, 0.0)

        if action == "buy":
            total += (intrinsic - premium) * qty
        else:
            total += (premium - intrinsic) * qty

    return total


def analyze_strategy(
    legs: List[Dict],
    S: float,
    r: float,
    sigma: float,
) -> OptionStrategy:
    """옵션 전략을 분석한다.

    Args:
        legs: 전략 다리 목록.
            [{"type": "call"/"put", "strike": K, "expiry_days": T_days,
              "action": "buy"/"sell", "qty": int, "premium": float (optional)}, ...]
        S: 기초자산 현재 가격.
        r: 무위험 이자율.
        sigma: 변동성.

    Returns:
        OptionStrategy 데이터클래스.
    """
    if not legs:
        logger.warning("빈 전략 다리")
        return OptionStrategy()

    name = _identify_strategy(legs)

    # --- 프리미엄 계산 (없으면 BS로 산출) ---
    processed_legs: List[Dict] = []
    for leg in legs:
        leg_copy = dict(leg)
        if "premium" not in leg_copy or leg_copy["premium"] is None:
            T = leg_copy.get("expiry_days", 30) / 365.0
            bs = black_scholes(S, leg_copy["strike"], T, r, sigma, leg_copy["type"])
            leg_copy["premium"] = bs.theoretical_price
        processed_legs.append(leg_copy)

    # --- 페이오프 범위 계산 ---
    all_strikes = [leg["strike"] for leg in processed_legs]
    min_K = min(all_strikes)
    max_K = max(all_strikes)
    margin = max(max_K - min_K, S * 0.3)
    price_range = np.linspace(
        max(0.01, min_K - margin), max_K + margin, 1000,
    )

    payoffs = [_compute_payoff_at_expiry(processed_legs, p) for p in price_range]
    max_profit = max(payoffs)
    max_loss = min(payoffs)

    # 무한 손익 탐지 (범위 끝에서 증가하면 무한)
    if payoffs[-1] > payoffs[-2] and payoffs[-1] == max_profit:
        max_profit = float("inf")
    if payoffs[0] < payoffs[1] and payoffs[0] == max_loss:
        max_loss = float("-inf")

    # --- 손익분기점 계산 ---
    breakevens: List[float] = []
    for i in range(len(payoffs) - 1):
        if payoffs[i] * payoffs[i + 1] < 0:
            # 선형 보간
            x0, x1 = float(price_range[i]), float(price_range[i + 1])
            y0, y1 = payoffs[i], payoffs[i + 1]
            be = x0 - y0 * (x1 - x0) / (y1 - y0)
            breakevens.append(round(be, 2))

    # --- Net Greeks ---
    net_greeks = OptionGreeks()
    for leg in processed_legs:
        T = leg.get("expiry_days", 30) / 365.0
        g = compute_greeks(S, leg["strike"], T, r, sigma, leg["type"])
        qty = leg.get("qty", 1)
        sign = 1.0 if leg.get("action", "buy").lower() == "buy" else -1.0
        mult = sign * qty

        net_greeks.delta += g.delta * mult
        net_greeks.gamma += g.gamma * mult
        net_greeks.vega += g.vega * mult
        net_greeks.theta += g.theta * mult
        net_greeks.rho += g.rho * mult
        net_greeks.charm += g.charm * mult
        net_greeks.vanna += g.vanna * mult
        net_greeks.volga += g.volga * mult

    # 반올림
    for attr in ("delta", "gamma", "vega", "theta", "rho", "charm", "vanna", "volga"):
        setattr(net_greeks, attr, round(getattr(net_greeks, attr), 6))

    logger.debug(
        "전략 분석: %s (%d 다리) max_profit=%.2f max_loss=%.2f breakevens=%s",
        name, len(processed_legs),
        max_profit if max_profit != float("inf") else 999999,
        max_loss if max_loss != float("-inf") else -999999,
        breakevens,
    )

    return OptionStrategy(
        name=name,
        legs=processed_legs,
        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=breakevens,
        greeks_net=net_greeks,
    )


# ---------------------------------------------------------------------------
# 8. 포맷 함수
# ---------------------------------------------------------------------------

def format_greeks(greeks: OptionGreeks, ticker: str = "") -> str:
    """Greeks를 텔레그램 메시지 포맷으로 변환한다.

    Args:
        greeks: OptionGreeks 인스턴스.
        ticker: 종목 코드 (optional).

    Returns:
        포맷된 문자열.
    """
    header = f"Greeks {ticker}" if ticker else "Greeks"
    lines = [
        f"📊 {header}",
        "",
        f"Delta  {greeks.delta:+.4f}",
        f"Gamma  {greeks.gamma:.6f}",
        f"Vega   {greeks.vega:+.4f}",
        f"Theta  {greeks.theta:+.4f}",
        f"Rho    {greeks.rho:+.4f}",
        "",
        "2차 Greeks",
        f"Charm  {greeks.charm:+.6f}",
        f"Vanna  {greeks.vanna:+.6f}",
        f"Volga  {greeks.volga:+.6f}",
    ]
    return "\n".join(lines)


def format_option_analysis(
    chain: OptionChainAnalysis,
    iv: ImpliedVolatility,
) -> str:
    """옵션 분석 결과를 텔레그램 메시지 포맷으로 변환한다.

    Args:
        chain: OptionChainAnalysis 인스턴스.
        iv: ImpliedVolatility 인스턴스.

    Returns:
        포맷된 문자열.
    """
    sentiment_emoji = {
        "bullish": "🟢",
        "bearish": "🔴",
        "neutral": "⚪",
    }
    emoji = sentiment_emoji.get(chain.dominant_sentiment, "⚪")

    lines = [
        "📈 옵션 분석",
        "",
        f"내재변동성 {iv.iv:.1%}",
        f"IV Percentile {iv.iv_percentile_1y:.0%}",
        f"IV Rank {iv.iv_rank_1y:.0%}",
    ]

    if iv.skew_25d != 0:
        lines.append(f"25d Skew {iv.skew_25d:+.4f}")

    lines.extend([
        "",
        f"P/C Ratio {chain.put_call_ratio:.2f}",
        f"Max Pain {chain.max_pain_strike:,.0f}",
        f"콜 OI {chain.total_oi_calls:,}",
        f"풋 OI {chain.total_oi_puts:,}",
        f"센티먼트 {emoji} {chain.dominant_sentiment}",
    ])

    if chain.unusual_activity:
        lines.append("")
        lines.append(f"⚠️ 이상거래 {len(chain.unusual_activity)}건")
        for act in chain.unusual_activity[:3]:
            lines.append(
                f"  {act['type'].upper()} {act['strike']:,.0f} "
                f"vol={act['volume']:,} ({act['volume_ratio']:.1f}x)"
            )

    return "\n".join(lines)
