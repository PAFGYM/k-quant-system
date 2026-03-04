"""TCA(Transaction Cost Analysis) + 시장충격모델 + 동적상관관계 + 고급 VaR.

risk_engine.py의 기본 VaR/Monte Carlo를 보완하는 고급 분석 모듈.
- Almgren-Chriss / Kyle 시장충격 모델
- Implementation Shortfall 기반 TCA
- DCC-GARCH 간소화 동적 상관관계 + Tail dependency
- Cornish-Fisher VaR + Component/Marginal/Incremental VaR
- Gaussian / t-copula VaR 시뮬레이션

순수 함수, dataclass, numpy/pandas/scipy만 사용.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# scipy optional (fallback z-score 테이블)
_Z_MAP = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.9600, 0.99: 2.3263}

try:
    from scipy import stats as sp_stats
    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    sp_stats = None
    _HAS_SCIPY = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dataclasses
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TCAReport:
    """단일 거래의 Transaction Cost Analysis 결과."""
    ticker: str
    order_size: int
    avg_daily_volume: float
    market_impact_pct: float
    timing_cost_pct: float
    spread_cost_pct: float
    total_cost_pct: float
    benchmark_price: float
    execution_price: float
    implementation_shortfall_pct: float


@dataclass
class MarketImpactEstimate:
    """시장충격 추정 결과."""
    ticker: str
    order_pct_of_volume: float
    temporary_impact_pct: float
    permanent_impact_pct: float
    total_impact_pct: float
    optimal_participation_rate: float


@dataclass
class DynamicCorrelation:
    """동적 상관관계 분석 결과."""
    ticker_a: str
    ticker_b: str
    rolling_60d: float
    rolling_120d: float
    crisis_correlation: float
    tail_correlation: float
    regime: str  # "normal" / "stress" / "crisis"


@dataclass
class AdvancedVaRResult:
    """고급 VaR 분석 결과."""
    method: str
    confidence: float
    horizon_days: int
    var_pct: float
    cvar_pct: float
    component_var: dict = field(default_factory=dict)
    marginal_var: dict = field(default_factory=dict)
    incremental_var: dict = field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Market Impact 모델
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def estimate_market_impact(
    order_shares: int,
    avg_daily_volume: float,
    price: float,
    volatility: float,
    method: str = "almgren_chriss",
    ticker: str = "",
) -> MarketImpactEstimate:
    """시장충격 추정 (Almgren-Chriss 또는 Kyle 모델).

    Args:
        order_shares: 주문 수량 (주)
        avg_daily_volume: 평균 일거래량 (주)
        price: 현재가
        volatility: 일간 변동성 (예: 0.02 = 2%)
        method: "almgren_chriss" 또는 "kyle"
        ticker: 종목 코드

    Returns:
        MarketImpactEstimate
    """
    try:
        if avg_daily_volume <= 0 or price <= 0:
            logger.warning("Invalid input: volume=%s, price=%s", avg_daily_volume, price)
            return MarketImpactEstimate(
                ticker=ticker,
                order_pct_of_volume=0.0,
                temporary_impact_pct=0.0,
                permanent_impact_pct=0.0,
                total_impact_pct=0.0,
                optimal_participation_rate=0.0,
            )

        n = abs(order_shares)
        v = avg_daily_volume
        sigma = max(volatility, 1e-8)
        pct_of_vol = n / v

        if method == "kyle":
            return _kyle_impact(n, v, sigma, price, ticker, pct_of_vol)

        return _almgren_chriss_impact(n, v, sigma, price, ticker, pct_of_vol)

    except Exception:
        logger.exception("estimate_market_impact error")
        return MarketImpactEstimate(
            ticker=ticker,
            order_pct_of_volume=0.0,
            temporary_impact_pct=0.0,
            permanent_impact_pct=0.0,
            total_impact_pct=0.0,
            optimal_participation_rate=0.0,
        )


def _almgren_chriss_impact(
    n: float, v: float, sigma: float, price: float,
    ticker: str, pct_of_vol: float,
) -> MarketImpactEstimate:
    """Almgren-Chriss 모델 구현.

    temporary = eta * sigma * (n/V)^0.6
    permanent = gamma * sigma * (n/V)^0.5
    """
    eta = 0.142      # temporary impact coefficient
    gamma = 0.314    # permanent impact coefficient

    ratio = n / v
    temp_impact = eta * sigma * (ratio ** 0.6)
    perm_impact = gamma * sigma * (ratio ** 0.5)
    total = temp_impact + perm_impact

    # 최적 참여율: urgency-adjusted
    # 참여율이 높으면 timing risk 감소하지만 market impact 증가
    # 최적점: sqrt(sigma_timing / eta_impact)
    # 간소화: 변동성 대비 적절 분산 → min(pct_of_vol^0.3, 0.25)
    optimal_rate = min(max(ratio ** 0.3 * 0.15, 0.01), 0.25)

    return MarketImpactEstimate(
        ticker=ticker,
        order_pct_of_volume=pct_of_vol,
        temporary_impact_pct=round(temp_impact * 100, 4),
        permanent_impact_pct=round(perm_impact * 100, 4),
        total_impact_pct=round(total * 100, 4),
        optimal_participation_rate=round(optimal_rate, 4),
    )


def _kyle_impact(
    n: float, v: float, sigma: float, price: float,
    ticker: str, pct_of_vol: float,
) -> MarketImpactEstimate:
    """Kyle 모델: lambda = sigma / sqrt(V), impact = lambda * n.

    Kyle's lambda는 informed trading의 가격 영향을 측정.
    """
    kyle_lambda = sigma / math.sqrt(v)
    total_impact = kyle_lambda * n / price  # 가격 대비 비율

    # Kyle 모델은 temporary/permanent 구분이 없으므로 비율로 분배
    temp_pct = total_impact * 0.4
    perm_pct = total_impact * 0.6

    optimal_rate = min(max(pct_of_vol ** 0.3 * 0.15, 0.01), 0.25)

    return MarketImpactEstimate(
        ticker=ticker,
        order_pct_of_volume=pct_of_vol,
        temporary_impact_pct=round(temp_pct * 100, 4),
        permanent_impact_pct=round(perm_pct * 100, 4),
        total_impact_pct=round(total_impact * 100, 4),
        optimal_participation_rate=round(optimal_rate, 4),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TCA (Transaction Cost Analysis)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_tca(
    trades: list[dict],
    ohlcv_map: dict,
) -> list[TCAReport]:
    """TCA: Implementation Shortfall 분해.

    Args:
        trades: 거래 리스트, 각 dict는:
            - ticker: str
            - order_size: int (주문 수량)
            - execution_price: float (체결가)
            - benchmark_price: float (주문 시점 가격, arrival price)
        ohlcv_map: ticker → dict with 'avg_volume', 'spread_pct', 'volatility'

    Returns:
        list[TCAReport]
    """
    if not trades:
        return []

    results = []
    for trade in trades:
        try:
            report = _compute_single_tca(trade, ohlcv_map)
            if report is not None:
                results.append(report)
        except Exception:
            logger.exception("TCA computation error for trade: %s", trade)
            continue

    return results


def _compute_single_tca(trade: dict, ohlcv_map: dict) -> TCAReport | None:
    """단일 거래 TCA 계산."""
    ticker = trade.get("ticker", "")
    order_size = trade.get("order_size", 0)
    exec_price = trade.get("execution_price", 0.0)
    bench_price = trade.get("benchmark_price", 0.0)

    if bench_price <= 0 or exec_price <= 0:
        logger.warning("Invalid prices for %s: bench=%s exec=%s",
                        ticker, bench_price, exec_price)
        return None

    market_data = ohlcv_map.get(ticker, {})
    avg_vol = market_data.get("avg_volume", 1_000_000)
    spread_pct = market_data.get("spread_pct", 0.1)   # 기본 10bp
    volatility = market_data.get("volatility", 0.02)

    # Implementation Shortfall (총 비용)
    is_pct = (exec_price - bench_price) / bench_price * 100

    # 비용 분해
    # 1) Spread cost: 매수시 반스프레드
    spread_cost = spread_pct / 2

    # 2) Market impact: Almgren-Chriss 모델 기반 추정
    impact_est = estimate_market_impact(
        order_shares=order_size,
        avg_daily_volume=avg_vol,
        price=bench_price,
        volatility=volatility,
        ticker=ticker,
    )
    market_impact = impact_est.total_impact_pct

    # 3) Timing cost: 잔차 (IS - impact - spread)
    timing_cost = is_pct - market_impact - spread_cost

    return TCAReport(
        ticker=ticker,
        order_size=order_size,
        avg_daily_volume=avg_vol,
        market_impact_pct=round(market_impact, 4),
        timing_cost_pct=round(timing_cost, 4),
        spread_cost_pct=round(spread_cost, 4),
        total_cost_pct=round(is_pct, 4),
        benchmark_price=bench_price,
        execution_price=exec_price,
        implementation_shortfall_pct=round(is_pct, 4),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Dynamic Correlation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_dynamic_correlation(
    returns_a: np.ndarray | pd.Series,
    returns_b: np.ndarray | pd.Series,
    lookback: int = 120,
    ticker_a: str = "A",
    ticker_b: str = "B",
) -> DynamicCorrelation:
    """DCC-GARCH 간소화 동적 상관관계.

    - Exponentially weighted correlation (decay=0.94)
    - Crisis correlation: 둘 다 -2sigma 이하일 때 조건부 상관
    - Tail dependency: Clayton copula 근사

    Args:
        returns_a: 수익률 시리즈 A
        returns_b: 수익률 시리즈 B
        lookback: 최대 관측 기간
        ticker_a: 종목 코드 A
        ticker_b: 종목 코드 B

    Returns:
        DynamicCorrelation
    """
    try:
        a = np.asarray(returns_a, dtype=np.float64)
        b = np.asarray(returns_b, dtype=np.float64)

        # NaN 제거 및 길이 맞추기
        min_len = min(len(a), len(b))
        if min_len < 10:
            logger.warning("Too few data points (%d) for correlation", min_len)
            return _empty_correlation(ticker_a, ticker_b)

        a = a[-min_len:]
        b = b[-min_len:]

        # NaN mask
        mask = ~(np.isnan(a) | np.isnan(b))
        a = a[mask]
        b = b[mask]

        if len(a) < 10:
            return _empty_correlation(ticker_a, ticker_b)

        # Rolling correlations
        n = len(a)
        window_60 = min(60, n)
        window_120 = min(lookback, n)

        roll_60 = _pearson_corr(a[-window_60:], b[-window_60:])
        roll_120 = _pearson_corr(a[-window_120:], b[-window_120:])

        # EWMA correlation (DCC-GARCH 간소화, decay=0.94)
        ewma_corr = _ewma_correlation(a, b, decay=0.94)

        # Crisis correlation: 둘 다 -2σ 이하
        sigma_a = np.std(a)
        sigma_b = np.std(b)
        threshold_a = -2.0 * sigma_a
        threshold_b = -2.0 * sigma_b
        crisis_mask = (a < threshold_a) & (b < threshold_b)

        if np.sum(crisis_mask) >= 3:
            crisis_corr = _pearson_corr(a[crisis_mask], b[crisis_mask])
        else:
            # 충분한 위기 데이터 없으면 EWMA 상관 * 1.15 (위기시 상관 상승 경향)
            crisis_corr = min(ewma_corr * 1.15, 1.0)

        # Tail dependency: Clayton copula approximation
        tail_corr = _clayton_tail_dependency(a, b)

        # 레짐 판단
        regime = _determine_regime(ewma_corr, crisis_corr, a, b)

        return DynamicCorrelation(
            ticker_a=ticker_a,
            ticker_b=ticker_b,
            rolling_60d=round(roll_60, 4),
            rolling_120d=round(roll_120, 4),
            crisis_correlation=round(crisis_corr, 4),
            tail_correlation=round(tail_corr, 4),
            regime=regime,
        )

    except Exception:
        logger.exception("Dynamic correlation error")
        return _empty_correlation(ticker_a, ticker_b)


def _pearson_corr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson 상관계수 (안전한 계산)."""
    if len(a) < 2:
        return 0.0
    std_a = np.std(a)
    std_b = np.std(b)
    if std_a < 1e-12 or std_b < 1e-12:
        return 0.0
    corr = np.corrcoef(a, b)[0, 1]
    if np.isnan(corr):
        return 0.0
    return float(np.clip(corr, -1.0, 1.0))


def _ewma_correlation(a: np.ndarray, b: np.ndarray, decay: float = 0.94) -> float:
    """Exponentially Weighted Moving Average 상관관계.

    DCC-GARCH의 핵심을 간소화: Q_t = (1-lambda)*r_t*r_t' + lambda*Q_{t-1}
    """
    n = len(a)
    if n < 2:
        return 0.0

    # 표준화
    mu_a, mu_b = np.mean(a), np.mean(b)
    std_a, std_b = np.std(a), np.std(b)
    if std_a < 1e-12 or std_b < 1e-12:
        return 0.0

    za = (a - mu_a) / std_a
    zb = (b - mu_b) / std_b

    # EWMA covariance
    weights = np.array([(1 - decay) * (decay ** i) for i in range(n - 1, -1, -1)])
    weights /= weights.sum()

    ewma_cov = np.sum(weights * za * zb)
    ewma_var_a = np.sum(weights * za * za)
    ewma_var_b = np.sum(weights * zb * zb)

    denom = math.sqrt(ewma_var_a * ewma_var_b)
    if denom < 1e-12:
        return 0.0

    result = ewma_cov / denom
    return float(np.clip(result, -1.0, 1.0))


def _clayton_tail_dependency(a: np.ndarray, b: np.ndarray) -> float:
    """Clayton copula lower tail dependency 근사.

    Clayton copula의 lower tail dependency: lambda_L = 2^(-1/theta)
    theta는 Kendall's tau로 추정: theta = 2*tau / (1-tau)
    """
    n = len(a)
    if n < 10:
        return 0.0

    # Kendall's tau 추정 (간소화: scipy 없이도 동작)
    if _HAS_SCIPY:
        tau, _ = sp_stats.kendalltau(a, b)
    else:
        tau = _kendall_tau_simple(a, b)

    if np.isnan(tau) or tau <= 0:
        return 0.0

    # Clayton parameter
    theta = 2.0 * tau / max(1.0 - tau, 1e-8)
    if theta <= 0:
        return 0.0

    # Lower tail dependency
    lambda_l = 2.0 ** (-1.0 / theta)
    return float(np.clip(lambda_l, 0.0, 1.0))


def _kendall_tau_simple(a: np.ndarray, b: np.ndarray) -> float:
    """Kendall's tau 간소화 구현 (scipy 없을 때 fallback)."""
    n = len(a)
    if n < 2:
        return 0.0

    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            sign_a = np.sign(a[j] - a[i])
            sign_b = np.sign(b[j] - b[i])
            prod = sign_a * sign_b
            if prod > 0:
                concordant += 1
            elif prod < 0:
                discordant += 1

    total = concordant + discordant
    if total == 0:
        return 0.0
    return (concordant - discordant) / total


def _determine_regime(
    ewma_corr: float, crisis_corr: float,
    a: np.ndarray, b: np.ndarray,
) -> str:
    """시장 레짐 판단.

    - crisis: 최근 수익률이 -2σ 이하 빈도 > 10%
    - stress: crisis_corr과 ewma_corr 차이 크거나, 최근 변동성 상승
    - normal: 기타
    """
    n = len(a)
    recent = min(20, n)

    sigma_a = np.std(a)
    sigma_b = np.std(b)

    if sigma_a < 1e-12 or sigma_b < 1e-12:
        return "normal"

    # 최근 20일 중 -2σ 이하 빈도
    recent_a = a[-recent:]
    recent_b = b[-recent:]
    crisis_count = np.sum(
        (recent_a < -2.0 * sigma_a) | (recent_b < -2.0 * sigma_b)
    )
    crisis_ratio = crisis_count / recent

    if crisis_ratio > 0.15:
        return "crisis"
    if crisis_ratio > 0.05 or abs(crisis_corr - ewma_corr) > 0.3:
        return "stress"
    return "normal"


def _empty_correlation(ticker_a: str, ticker_b: str) -> DynamicCorrelation:
    """빈 상관관계 결과."""
    return DynamicCorrelation(
        ticker_a=ticker_a,
        ticker_b=ticker_b,
        rolling_60d=0.0,
        rolling_120d=0.0,
        crisis_correlation=0.0,
        tail_correlation=0.0,
        regime="normal",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Advanced VaR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _z_score(confidence: float) -> float:
    """Z-score 계산 (scipy 없으면 테이블 사용)."""
    if _HAS_SCIPY:
        return float(sp_stats.norm.ppf(confidence))
    return _Z_MAP.get(confidence, 1.6449)  # default 95%


def compute_advanced_var(
    returns_matrix: pd.DataFrame,
    weights: dict,
    confidence: float = 0.95,
    horizon: int = 1,
    method: str = "parametric",
) -> AdvancedVaRResult:
    """고급 VaR 계산 (Parametric + Cornish-Fisher / Historical).

    Args:
        returns_matrix: columns = ticker, rows = dates, values = daily returns
        weights: {ticker: weight} (합 = 1)
        confidence: 신뢰 수준 (예: 0.95)
        horizon: 보유 기간 (일)
        method: "parametric" 또는 "historical"

    Returns:
        AdvancedVaRResult
    """
    try:
        if returns_matrix.empty or not weights:
            return _empty_var_result(method, confidence, horizon)

        # 공통 ticker만 사용
        tickers = [t for t in weights if t in returns_matrix.columns]
        if not tickers:
            return _empty_var_result(method, confidence, horizon)

        rm = returns_matrix[tickers].dropna()
        if len(rm) < 10:
            return _empty_var_result(method, confidence, horizon)

        w_arr = np.array([weights[t] for t in tickers])
        w_sum = w_arr.sum()
        if w_sum < 1e-12:
            return _empty_var_result(method, confidence, horizon)
        w_arr = w_arr / w_sum  # 정규화

        # 포트폴리오 수익률
        port_returns = rm.values @ w_arr

        if method == "historical":
            return _historical_var(
                port_returns, rm, tickers, w_arr, confidence, horizon,
            )
        return _parametric_var(
            port_returns, rm, tickers, w_arr, confidence, horizon,
        )

    except Exception:
        logger.exception("Advanced VaR computation error")
        return _empty_var_result(method, confidence, horizon)


def _parametric_var(
    port_returns: np.ndarray,
    rm: pd.DataFrame,
    tickers: list[str],
    w_arr: np.ndarray,
    confidence: float,
    horizon: int,
) -> AdvancedVaRResult:
    """Parametric VaR + Cornish-Fisher 보정."""
    mu = np.mean(port_returns)
    sigma = np.std(port_returns, ddof=1)
    if sigma < 1e-12:
        return _empty_var_result("parametric", confidence, horizon)

    z = _z_score(confidence)

    # Cornish-Fisher 보정 (skewness/kurtosis)
    skew = float(_safe_skewness(port_returns))
    kurt = float(_safe_kurtosis(port_returns))

    z_cf = (
        z
        + (z ** 2 - 1) * skew / 6
        + (z ** 3 - 3 * z) * kurt / 24
        - (2 * z ** 3 - 5 * z) * (skew ** 2) / 36
    )

    # VaR (양수: 손실)
    var_1d = -(mu - z_cf * sigma)
    var_pct = var_1d * math.sqrt(horizon)

    # CVaR (Expected Shortfall)
    if _HAS_SCIPY:
        pdf_z = sp_stats.norm.pdf(z)
        cvar_pct = -(mu - sigma * pdf_z / (1 - confidence)) * math.sqrt(horizon)
    else:
        # 근사: CVaR ≈ VaR * 1.25 (정규분포 95% 기준)
        cvar_pct = var_pct * 1.25

    # Component / Marginal / Incremental VaR
    cov_matrix = rm.cov().values
    port_sigma = math.sqrt(float(w_arr @ cov_matrix @ w_arr))
    if port_sigma < 1e-12:
        port_sigma = sigma

    component_var = _compute_component_var(
        tickers, w_arr, cov_matrix, port_sigma, z_cf, horizon,
    )
    marginal_var = _compute_marginal_var(
        tickers, w_arr, cov_matrix, port_sigma, z_cf, horizon,
    )
    incremental_var = _compute_incremental_var(
        tickers, w_arr, rm, confidence, horizon,
    )

    return AdvancedVaRResult(
        method="parametric_cornish_fisher",
        confidence=confidence,
        horizon_days=horizon,
        var_pct=round(abs(var_pct) * 100, 4),
        cvar_pct=round(abs(cvar_pct) * 100, 4),
        component_var=component_var,
        marginal_var=marginal_var,
        incremental_var=incremental_var,
    )


def _historical_var(
    port_returns: np.ndarray,
    rm: pd.DataFrame,
    tickers: list[str],
    w_arr: np.ndarray,
    confidence: float,
    horizon: int,
) -> AdvancedVaRResult:
    """Historical VaR (정렬 기반)."""
    sorted_returns = np.sort(port_returns)
    n = len(sorted_returns)
    idx = int(n * (1 - confidence))
    idx = max(0, min(idx, n - 1))

    var_1d = -sorted_returns[idx]
    var_pct = var_1d * math.sqrt(horizon)

    # CVaR: VaR 이하 평균
    tail = sorted_returns[:idx + 1]
    cvar_1d = -np.mean(tail) if len(tail) > 0 else var_1d
    cvar_pct = cvar_1d * math.sqrt(horizon)

    # Component / Marginal / Incremental VaR
    cov_matrix = rm.cov().values
    port_sigma = math.sqrt(float(w_arr @ cov_matrix @ w_arr))
    z_equiv = var_1d / max(port_sigma, 1e-12)

    component_var = _compute_component_var(
        tickers, w_arr, cov_matrix, port_sigma, z_equiv, horizon,
    )
    marginal_var = _compute_marginal_var(
        tickers, w_arr, cov_matrix, port_sigma, z_equiv, horizon,
    )
    incremental_var = _compute_incremental_var(
        tickers, w_arr, rm, confidence, horizon,
    )

    return AdvancedVaRResult(
        method="historical",
        confidence=confidence,
        horizon_days=horizon,
        var_pct=round(abs(var_pct) * 100, 4),
        cvar_pct=round(abs(cvar_pct) * 100, 4),
        component_var=component_var,
        marginal_var=marginal_var,
        incremental_var=incremental_var,
    )


def _compute_component_var(
    tickers: list[str], w: np.ndarray, cov: np.ndarray,
    port_sigma: float, z: float, horizon: int,
) -> dict:
    """Component VaR: VaR_i = w_i * beta_i * VaR_p."""
    result = {}
    port_var_total = z * port_sigma * math.sqrt(horizon)

    for i, ticker in enumerate(tickers):
        # beta_i = Cov(r_i, r_p) / Var(r_p)
        cov_ip = float(cov[i] @ w)
        port_variance = port_sigma ** 2
        if port_variance < 1e-12:
            result[ticker] = 0.0
            continue
        beta_i = cov_ip / port_variance
        comp_var_i = w[i] * beta_i * port_var_total
        result[ticker] = round(abs(comp_var_i) * 100, 4)

    return result


def _compute_marginal_var(
    tickers: list[str], w: np.ndarray, cov: np.ndarray,
    port_sigma: float, z: float, horizon: int,
) -> dict:
    """Marginal VaR: dVaR/dw_i = beta_i * sigma_p * z_alpha."""
    result = {}
    for i, ticker in enumerate(tickers):
        cov_ip = float(cov[i] @ w)
        port_variance = port_sigma ** 2
        if port_variance < 1e-12:
            result[ticker] = 0.0
            continue
        beta_i = cov_ip / port_variance
        mvar = beta_i * port_sigma * z * math.sqrt(horizon)
        result[ticker] = round(abs(mvar) * 100, 4)

    return result


def _compute_incremental_var(
    tickers: list[str], w: np.ndarray,
    rm: pd.DataFrame, confidence: float, horizon: int,
) -> dict:
    """Incremental VaR: VaR(with asset) - VaR(without asset)."""
    result = {}
    n_data = len(rm)
    if n_data < 10:
        return {t: 0.0 for t in tickers}

    # 전체 포트폴리오 VaR
    port_ret = rm.values @ w
    sorted_full = np.sort(port_ret)
    idx_full = max(0, int(n_data * (1 - confidence)))
    var_full = -sorted_full[min(idx_full, n_data - 1)] * math.sqrt(horizon)

    for i, ticker in enumerate(tickers):
        if w[i] < 1e-12:
            result[ticker] = 0.0
            continue
        # 해당 자산 제외한 가중치 재정규화
        w_excl = w.copy()
        w_excl[i] = 0.0
        w_sum = w_excl.sum()
        if w_sum < 1e-12:
            result[ticker] = round(abs(var_full) * 100, 4)
            continue
        w_excl = w_excl / w_sum

        port_ret_excl = rm.values @ w_excl
        sorted_excl = np.sort(port_ret_excl)
        idx_excl = max(0, int(n_data * (1 - confidence)))
        var_excl = -sorted_excl[min(idx_excl, n_data - 1)] * math.sqrt(horizon)

        incr = var_full - var_excl
        result[ticker] = round(abs(incr) * 100, 4)

    return result


def _safe_skewness(arr: np.ndarray) -> float:
    """안전한 skewness 계산."""
    if len(arr) < 3:
        return 0.0
    if _HAS_SCIPY:
        s = sp_stats.skew(arr, bias=False)
        return 0.0 if np.isnan(s) else float(s)
    mu = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std < 1e-12:
        return 0.0
    n = len(arr)
    return float((n / ((n - 1) * (n - 2))) * np.sum(((arr - mu) / std) ** 3))


def _safe_kurtosis(arr: np.ndarray) -> float:
    """안전한 excess kurtosis 계산."""
    if len(arr) < 4:
        return 0.0
    if _HAS_SCIPY:
        k = sp_stats.kurtosis(arr, bias=False)
        return 0.0 if np.isnan(k) else float(k)
    mu = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std < 1e-12:
        return 0.0
    n = len(arr)
    m4 = np.mean((arr - mu) ** 4)
    return float(m4 / (std ** 4) - 3.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Copula VaR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_copula_var(
    returns_matrix: pd.DataFrame,
    weights: dict,
    n_sim: int = 10000,
    confidence: float = 0.95,
    horizon: int = 1,
    copula_type: str = "gaussian",
    seed: int | None = None,
) -> AdvancedVaRResult:
    """Copula 기반 VaR (Gaussian / t-copula).

    1. Rank-transform → Uniform marginals
    2. Inverse normal CDF → Correlated normal
    3. Simulate from multivariate distribution
    4. Back-transform to original marginals

    Args:
        returns_matrix: columns=ticker, rows=dates
        weights: {ticker: weight}
        n_sim: 시뮬레이션 횟수
        confidence: 신뢰 수준
        horizon: 보유 기간 (일)
        copula_type: "gaussian" 또는 "t"
        seed: 랜덤 시드 (재현성)

    Returns:
        AdvancedVaRResult
    """
    try:
        if returns_matrix.empty or not weights:
            return _empty_var_result("copula_" + copula_type, confidence, horizon)

        tickers = [t for t in weights if t in returns_matrix.columns]
        if not tickers:
            return _empty_var_result("copula_" + copula_type, confidence, horizon)

        rm = returns_matrix[tickers].dropna()
        if len(rm) < 20:
            return _empty_var_result("copula_" + copula_type, confidence, horizon)

        w_arr = np.array([weights[t] for t in tickers])
        w_sum = w_arr.sum()
        if w_sum < 1e-12:
            return _empty_var_result("copula_" + copula_type, confidence, horizon)
        w_arr = w_arr / w_sum

        rng = np.random.RandomState(seed)
        data = rm.values  # (n_obs, n_assets)
        n_obs, n_assets = data.shape

        # Step 1: Rank-transform → pseudo-uniform [0,1]
        # (rank / (n+1)) to avoid 0 and 1
        ranks = np.zeros_like(data)
        for j in range(n_assets):
            order = data[:, j].argsort().argsort()
            ranks[:, j] = (order + 1) / (n_obs + 1)

        # Step 2: Inverse normal CDF → standard normal
        if _HAS_SCIPY:
            z_data = sp_stats.norm.ppf(ranks)
        else:
            z_data = _approx_norm_ppf(ranks)

        # Correlation matrix of z-transformed data
        corr_matrix = np.corrcoef(z_data.T)
        # 양의 정치 행렬 보장
        corr_matrix = _nearest_positive_definite(corr_matrix)

        # Step 3: Simulate
        if copula_type == "t":
            sim_returns = _simulate_t_copula(
                data, corr_matrix, n_sim, n_assets, n_obs, rng, df=5,
            )
        else:
            sim_returns = _simulate_gaussian_copula(
                data, corr_matrix, n_sim, n_assets, n_obs, rng,
            )

        # Step 4: Portfolio returns
        port_sim = sim_returns @ w_arr

        # Multi-day horizon
        if horizon > 1:
            port_sim = port_sim * math.sqrt(horizon)

        # VaR / CVaR
        sorted_sim = np.sort(port_sim)
        idx = max(0, int(n_sim * (1 - confidence)))
        var_pct = -sorted_sim[min(idx, n_sim - 1)]
        tail = sorted_sim[:idx + 1]
        cvar_pct = -np.mean(tail) if len(tail) > 0 else var_pct

        return AdvancedVaRResult(
            method="copula_" + copula_type,
            confidence=confidence,
            horizon_days=horizon,
            var_pct=round(abs(var_pct) * 100, 4),
            cvar_pct=round(abs(cvar_pct) * 100, 4),
            component_var={},
            marginal_var={},
            incremental_var={},
        )

    except Exception:
        logger.exception("Copula VaR error")
        return _empty_var_result("copula_" + copula_type, confidence, horizon)


def _simulate_gaussian_copula(
    data: np.ndarray, corr: np.ndarray,
    n_sim: int, n_assets: int, n_obs: int,
    rng: np.random.RandomState,
) -> np.ndarray:
    """Gaussian copula 시뮬레이션."""
    # Cholesky decomposition
    L = np.linalg.cholesky(corr)

    # Correlated standard normals
    z = rng.standard_normal((n_sim, n_assets))
    corr_z = z @ L.T

    # Transform to uniform via normal CDF
    if _HAS_SCIPY:
        u = sp_stats.norm.cdf(corr_z)
    else:
        u = _approx_norm_cdf(corr_z)

    # Back-transform to original marginals via empirical quantile
    sim = np.zeros((n_sim, n_assets))
    for j in range(n_assets):
        sorted_col = np.sort(data[:, j])
        indices = np.clip(
            (u[:, j] * n_obs).astype(int), 0, n_obs - 1,
        )
        sim[:, j] = sorted_col[indices]

    return sim


def _simulate_t_copula(
    data: np.ndarray, corr: np.ndarray,
    n_sim: int, n_assets: int, n_obs: int,
    rng: np.random.RandomState, df: int = 5,
) -> np.ndarray:
    """t-copula 시뮬레이션 (heavier tails)."""
    L = np.linalg.cholesky(corr)

    # Correlated standard normals
    z = rng.standard_normal((n_sim, n_assets))
    corr_z = z @ L.T

    # Chi-squared for t-distribution scaling
    chi2 = rng.chisquare(df, size=n_sim)
    scale = np.sqrt(df / chi2)

    # t-distributed variates
    t_vars = corr_z * scale[:, np.newaxis]

    # Transform to uniform via t-CDF
    if _HAS_SCIPY:
        u = sp_stats.t.cdf(t_vars, df=df)
    else:
        # Fallback: use normal CDF (approximate for moderate df)
        u = _approx_norm_cdf(t_vars / math.sqrt(df / (df - 2)))

    # Back-transform
    sim = np.zeros((n_sim, n_assets))
    for j in range(n_assets):
        sorted_col = np.sort(data[:, j])
        indices = np.clip(
            (u[:, j] * n_obs).astype(int), 0, n_obs - 1,
        )
        sim[:, j] = sorted_col[indices]

    return sim


def _nearest_positive_definite(m: np.ndarray) -> np.ndarray:
    """가장 가까운 양의 정치 행렬 (Higham 2002 간소화)."""
    eigvals, eigvecs = np.linalg.eigh(m)
    eigvals = np.maximum(eigvals, 1e-8)
    result = eigvecs @ np.diag(eigvals) @ eigvecs.T
    # 대각 정규화 (상관행렬 유지)
    d = np.sqrt(np.diag(result))
    d[d < 1e-12] = 1.0
    result = result / np.outer(d, d)
    np.fill_diagonal(result, 1.0)
    return result


def _approx_norm_ppf(u: np.ndarray) -> np.ndarray:
    """Normal PPF 근사 (Beasley-Springer-Moro, scipy 없을 때)."""
    # 간소화: 역정규 CDF 근사
    u_clipped = np.clip(u, 1e-6, 1 - 1e-6)
    # Rational approximation
    t = np.where(u_clipped < 0.5,
                 np.sqrt(-2.0 * np.log(u_clipped)),
                 np.sqrt(-2.0 * np.log(1.0 - u_clipped)))
    # Abramowitz & Stegun 26.2.23
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    result = t - (c0 + c1 * t + c2 * t ** 2) / (1 + d1 * t + d2 * t ** 2 + d3 * t ** 3)
    return np.where(u_clipped < 0.5, -result, result)


def _approx_norm_cdf(x: np.ndarray) -> np.ndarray:
    """Normal CDF 근사 (scipy 없을 때)."""
    # Abramowitz & Stegun 7.1.26
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = np.sign(x)
    x_abs = np.abs(x)
    t = 1.0 / (1.0 + p * x_abs)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x_abs * x_abs / 2)
    return np.where(sign >= 0, (1.0 + y) / 2.0, (1.0 - y) / 2.0)


def _empty_var_result(method: str, confidence: float, horizon: int) -> AdvancedVaRResult:
    """빈 VaR 결과."""
    return AdvancedVaRResult(
        method=method,
        confidence=confidence,
        horizon_days=horizon,
        var_pct=0.0,
        cvar_pct=0.0,
        component_var={},
        marginal_var={},
        incremental_var={},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. 텔레그램 포맷 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def format_tca_report(reports: list[TCAReport]) -> str:
    """TCA 분석 결과를 텔레그램 plain text로 포맷.

    Returns:
        str: 줄바꿈 + emoji 포맷
    """
    if not reports:
        return "📊 TCA 분석: 거래 데이터 없음"

    lines = ["📊 거래 비용 분석 (TCA)"]
    lines.append("")

    for r in reports:
        lines.append(f"🔹 {r.ticker}")
        lines.append(f"  주문: {r.order_size:,}주")
        lines.append(f"  기준가: {r.benchmark_price:,.0f}원")
        lines.append(f"  체결가: {r.execution_price:,.0f}원")
        lines.append(f"  IS: {r.implementation_shortfall_pct:+.2f}%")
        lines.append(f"    시장충격: {r.market_impact_pct:+.2f}%")
        lines.append(f"    타이밍: {r.timing_cost_pct:+.2f}%")
        lines.append(f"    스프레드: {r.spread_cost_pct:+.2f}%")
        lines.append("")

    avg_is = sum(r.implementation_shortfall_pct for r in reports) / len(reports)
    grade = "우수" if abs(avg_is) < 0.1 else "보통" if abs(avg_is) < 0.3 else "개선필요"
    lines.append(f"📈 평균 IS: {avg_is:+.2f}% ({grade})")

    return "\n".join(lines)


def format_risk_report(
    var_result: AdvancedVaRResult,
    correlations: list[DynamicCorrelation] | None = None,
) -> str:
    """VaR + 동적상관관계 리포트를 텔레그램 plain text로 포맷.

    Returns:
        str: 줄바꿈 + emoji 포맷
    """
    lines = ["🛡 고급 리스크 리포트"]
    lines.append("")

    # VaR 섹션
    method_name = {
        "parametric_cornish_fisher": "파라메트릭 (C-F 보정)",
        "historical": "히스토리컬",
        "copula_gaussian": "가우시안 코퓰라",
        "copula_t": "t-코퓰라",
    }.get(var_result.method, var_result.method)

    lines.append(f"📉 VaR ({method_name})")
    lines.append(f"  신뢰수준: {var_result.confidence:.0%}")
    lines.append(f"  보유기간: {var_result.horizon_days}일")
    lines.append(f"  VaR: {var_result.var_pct:.2f}%")
    lines.append(f"  CVaR: {var_result.cvar_pct:.2f}%")

    # Component VaR
    if var_result.component_var:
        lines.append("")
        lines.append("🔸 Component VaR (기여도)")
        for ticker, val in sorted(
            var_result.component_var.items(), key=lambda x: -x[1],
        ):
            lines.append(f"  {ticker}: {val:.2f}%")

    # 상관관계 섹션
    if correlations:
        lines.append("")
        lines.append("🔗 동적 상관관계")
        for c in correlations:
            regime_emoji = {"normal": "🟢", "stress": "🟡", "crisis": "🔴"}.get(
                c.regime, "⚪",
            )
            lines.append(
                f"  {c.ticker_a}-{c.ticker_b}: "
                f"60일={c.rolling_60d:.2f} "
                f"위기={c.crisis_correlation:.2f} "
                f"{regime_emoji}{c.regime}"
            )

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 스트레스 테스트 엔진
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class StressScenario:
    """스트레스 시나리오 정의."""
    name: str
    description: str
    market_shock_pct: float       # KOSPI 충격 (%)
    sector_shocks: dict = field(default_factory=dict)  # {sector: shock_pct}
    vix_level: float = 35.0
    correlation_boost: float = 0.3  # 위기 시 상관관계 상승분


@dataclass
class StressTestResult:
    """스트레스 테스트 결과."""
    scenario_name: str
    portfolio_loss_pct: float
    worst_stock: str
    worst_stock_loss_pct: float
    estimated_recovery_days: int
    breach_mdd_limit: bool
    per_stock_impact: dict = field(default_factory=dict)  # {ticker: loss_pct}


# 사전 정의 시나리오
PREDEFINED_SCENARIOS = [
    StressScenario(
        name="KOSPI -5%",
        description="일반 조정 (2-3개월 1회 빈도)",
        market_shock_pct=-5.0,
        vix_level=25.0,
        correlation_boost=0.1,
    ),
    StressScenario(
        name="KOSPI -10%",
        description="강한 조정 (연 1-2회 빈도, COVID 초기 수준)",
        market_shock_pct=-10.0,
        vix_level=30.0,
        correlation_boost=0.2,
    ),
    StressScenario(
        name="KOSPI -20%",
        description="금융위기급 폭락 (2008, COVID 3월 수준)",
        market_shock_pct=-20.0,
        vix_level=45.0,
        correlation_boost=0.4,
    ),
    StressScenario(
        name="2차전지 -30%",
        description="섹터 버블 붕괴 시나리오",
        market_shock_pct=-8.0,
        sector_shocks={"2차전지": -30.0, "반도체": -10.0},
        vix_level=28.0,
        correlation_boost=0.2,
    ),
    StressScenario(
        name="금리 급등",
        description="미국 금리 +100bp 충격",
        market_shock_pct=-7.0,
        sector_shocks={"성장주": -15.0, "바이오": -12.0, "금융": 5.0},
        vix_level=28.0,
        correlation_boost=0.15,
    ),
    StressScenario(
        name="서킷브레이커 발동",
        description="KOSPI -8% 이상 급락, 전종목 거래정지",
        market_shock_pct=-8.0,
        sector_shocks={"전체": -8.0},
        vix_level=40.0,
        correlation_boost=0.5,
    ),
    StressScenario(
        name="환율 급등 (1400원 돌파)",
        description="USD/KRW 1400원 돌파, 외국인 대규모 이탈",
        market_shock_pct=-6.0,
        sector_shocks={"수출주": -3.0, "내수주": -8.0, "금융": -7.0},
        vix_level=30.0,
        correlation_boost=0.3,
    ),
    StressScenario(
        name="VIX 50 돌파 (극단 패닉)",
        description="VIX 50 이상 극단적 공포, 글로벌 동반 급락",
        market_shock_pct=-15.0,
        sector_shocks={"전체": -15.0},
        vix_level=50.0,
        correlation_boost=0.6,
    ),
    StressScenario(
        name="유동성 위기",
        description="신용경색, 마진콜 연쇄, 거래량 급감",
        market_shock_pct=-10.0,
        sector_shocks={"금융": -15.0, "건설": -12.0, "소재": -10.0},
        vix_level=45.0,
        correlation_boost=0.4,
    ),
    StressScenario(
        name="반도체 수출 급감",
        description="글로벌 반도체 수요 급감, 수출 20% 감소",
        market_shock_pct=-5.0,
        sector_shocks={"반도체": -25.0, "전자부품": -15.0, "IT": -10.0},
        vix_level=28.0,
        correlation_boost=0.2,
    ),
]


def run_stress_test(
    holdings: list[dict],
    scenario: StressScenario,
    sector_map: dict[str, str] | None = None,
    beta_map: dict[str, float] | None = None,
    mdd_limit: float = -0.15,
) -> StressTestResult:
    """포트폴리오에 스트레스 시나리오를 적용.

    Args:
        holdings: [{"ticker", "name", "eval_amount", "weight"}]
        scenario: 적용할 시나리오
        sector_map: {ticker: sector}
        beta_map: {ticker: beta_to_kospi} (없으면 1.0 가정)
        mdd_limit: MDD 한도 (기본 -15%)

    Returns:
        StressTestResult
    """
    try:
        if not holdings:
            return StressTestResult(
                scenario_name=scenario.name,
                portfolio_loss_pct=0.0,
                worst_stock="N/A",
                worst_stock_loss_pct=0.0,
                estimated_recovery_days=0,
                breach_mdd_limit=False,
            )

        sector_map = sector_map or {}
        beta_map = beta_map or {}

        total_value = sum(h.get("eval_amount", 0) for h in holdings)
        if total_value <= 0:
            return StressTestResult(
                scenario_name=scenario.name, portfolio_loss_pct=0.0,
                worst_stock="N/A", worst_stock_loss_pct=0.0,
                estimated_recovery_days=0, breach_mdd_limit=False,
            )

        per_stock = {}
        weighted_loss = 0.0
        worst_ticker = ""
        worst_loss = 0.0

        for h in holdings:
            ticker = h.get("ticker", "")
            weight = h.get("eval_amount", 0) / total_value
            beta = beta_map.get(ticker, 1.0)
            sector = sector_map.get(ticker, "기타")

            # 시장 충격 * 베타
            stock_loss = scenario.market_shock_pct * beta

            # 섹터별 추가 충격
            sector_shock = scenario.sector_shocks.get(sector, 0.0)
            stock_loss += sector_shock

            # 위기 시 상관관계 상승 → 추가 손실
            stock_loss *= (1.0 + scenario.correlation_boost)

            per_stock[ticker] = round(stock_loss, 2)
            weighted_loss += weight * stock_loss

            if stock_loss < worst_loss:
                worst_loss = stock_loss
                worst_ticker = h.get("name", ticker)

        # 회복 추정: 일 평균 +0.3% 기준
        recovery_days = int(abs(weighted_loss) / 0.3) if weighted_loss < 0 else 0

        return StressTestResult(
            scenario_name=scenario.name,
            portfolio_loss_pct=round(weighted_loss, 2),
            worst_stock=worst_ticker,
            worst_stock_loss_pct=round(worst_loss, 2),
            estimated_recovery_days=recovery_days,
            breach_mdd_limit=weighted_loss <= mdd_limit * 100,
            per_stock_impact=per_stock,
        )

    except Exception:
        logger.exception("스트레스 테스트 실패: %s", scenario.name)
        return StressTestResult(
            scenario_name=scenario.name, portfolio_loss_pct=0.0,
            worst_stock="N/A", worst_stock_loss_pct=0.0,
            estimated_recovery_days=0, breach_mdd_limit=False,
        )


def run_all_stress_tests(
    holdings: list[dict],
    sector_map: dict[str, str] | None = None,
    beta_map: dict[str, float] | None = None,
) -> list[StressTestResult]:
    """모든 사전정의 시나리오 일괄 실행."""
    return [
        run_stress_test(holdings, s, sector_map, beta_map)
        for s in PREDEFINED_SCENARIOS
    ]


def format_stress_test_report(results: list[StressTestResult]) -> str:
    """스트레스 테스트 결과 텔레그램 포맷."""
    lines = [
        "🔥 스트레스 테스트 결과",
        "━" * 25,
        "",
    ]

    for r in results:
        breach = "⛔ MDD 한도 초과!" if r.breach_mdd_limit else "✅ 한도 이내"
        lines.append(
            f"📌 {r.scenario_name}\n"
            f"   포트폴리오 손실: {r.portfolio_loss_pct:+.1f}%\n"
            f"   최악 종목: {r.worst_stock} ({r.worst_stock_loss_pct:+.1f}%)\n"
            f"   예상 회복: {r.estimated_recovery_days}일\n"
            f"   {breach}\n"
        )

    # Summary
    worst = min(results, key=lambda r: r.portfolio_loss_pct)
    lines.append(f"최악 시나리오: {worst.scenario_name} ({worst.portfolio_loss_pct:+.1f}%)")

    return "\n".join(lines)
