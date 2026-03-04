"""Technical indicator computation using pure pandas/numpy.

Computes RSI(14), BB(20,2), MACD(12,26,9), ATR(14) without external TA libraries.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TechnicalIndicators:
    """Computed technical indicators for a stock."""

    rsi: float
    bb_pctb: float  # Bollinger Band %B (0=lower, 1=upper)
    bb_bandwidth: float
    macd_histogram: float
    macd_signal_cross: int  # 1=bullish cross, -1=bearish cross, 0=none
    atr: float
    atr_pct: float  # ATR as percentage of close
    # v2.5: Multi-timeframe & momentum fields
    ema_50: float = 0.0
    ema_200: float = 0.0
    golden_cross: bool = False  # 50 EMA > 200 EMA crossover
    dead_cross: bool = False
    weekly_trend: str = "neutral"  # "up", "down", "neutral"
    mtf_aligned: bool = False  # multi-timeframe alignment
    high_52w: float = 0.0
    high_20d: float = 0.0
    volume_ratio: float = 1.0  # current vol / 20d avg vol
    bb_squeeze: bool = False  # bandwidth contracting
    return_3m_pct: float = 0.0  # 3-month return for relative strength
    # v8.1: Moving averages & MACD line/signal (used by multi-agent)
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    ma120: float = 0.0
    macd: float = 0.0  # alias for macd_histogram (compat)
    macd_signal: float = 0.0  # MACD signal line value
    # v8.1: Divergence detection
    rsi_divergence: int = 0   # 1=bullish divergence, -1=bearish divergence, 0=none
    macd_divergence: int = 0  # 1=bullish divergence, -1=bearish divergence, 0=none


@dataclass
class NormalizedIndicators:
    """모든 지표를 0~1 범위로 정규화한 결과.

    0 = 가장 약세/위험, 1 = 가장 강세/안전.
    """
    rsi: float = 0.5          # 0=극과매도, 1=극과매수 (투자 관점에선 역전)
    rsi_opportunity: float = 0.5  # RSI 매수 기회 (0=과매수, 1=과매도=기회)
    bb_position: float = 0.5  # 0=하단, 1=상단
    bb_opportunity: float = 0.5  # BB 매수 기회 (0=상단, 1=하단=기회)
    macd_strength: float = 0.5  # MACD 시그널 강도
    trend_strength: float = 0.5  # EMA 기반 추세 강도
    momentum: float = 0.5      # 3개월 수익률 기반 모멘텀
    volume_signal: float = 0.5  # 거래량 시그널
    volatility_risk: float = 0.5  # 변동성 리스크 (0=고변동, 1=안정)
    breakout_score: float = 0.0   # 돌파 점수
    composite: float = 0.5    # 가중 합성 점수


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Compute RSI using Wilder's smoothing method."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Bollinger Bands (lower, mid, upper)."""
    mid = close.rolling(window=length).mean()
    rolling_std = close.rolling(window=length).std()
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std
    return lower, mid, upper


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute MACD line, signal line, histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Compute Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=length).mean()


def _detect_rsi_divergence(close: pd.Series, rsi_series: pd.Series, lookback: int = 20) -> int:
    """Detect RSI divergence.

    Bullish divergence (1): price makes lower low but RSI makes higher low
    Bearish divergence (-1): price makes higher high but RSI makes lower high
    """
    if len(close) < lookback + 5:
        return 0

    recent = close.iloc[-lookback:]
    recent_rsi = rsi_series.iloc[-lookback:]

    # Remove NaN
    if recent_rsi.isna().any():
        return 0

    # Find local lows (last 2)
    half = lookback // 2
    price_low1 = recent.iloc[:half].min()
    price_low2 = recent.iloc[half:].min()
    rsi_low1 = recent_rsi.iloc[:half].min()
    rsi_low2 = recent_rsi.iloc[half:].min()

    # Bullish: price lower low, RSI higher low
    if price_low2 < price_low1 and rsi_low2 > rsi_low1 + 2:
        return 1

    # Find local highs
    price_high1 = recent.iloc[:half].max()
    price_high2 = recent.iloc[half:].max()
    rsi_high1 = recent_rsi.iloc[:half].max()
    rsi_high2 = recent_rsi.iloc[half:].max()

    # Bearish: price higher high, RSI lower high
    if price_high2 > price_high1 and rsi_high2 < rsi_high1 - 2:
        return -1

    return 0


def _detect_macd_divergence(close: pd.Series, macd_hist: pd.Series, lookback: int = 20) -> int:
    """Detect MACD histogram divergence.

    Bullish (1): price lower low, MACD histogram higher low
    Bearish (-1): price higher high, MACD histogram lower high
    """
    if len(close) < lookback + 5:
        return 0

    recent = close.iloc[-lookback:]
    recent_macd = macd_hist.iloc[-lookback:]

    if recent_macd.isna().any():
        return 0

    half = lookback // 2

    # Lows
    price_low1 = recent.iloc[:half].min()
    price_low2 = recent.iloc[half:].min()
    macd_low1 = recent_macd.iloc[:half].min()
    macd_low2 = recent_macd.iloc[half:].min()

    if price_low2 < price_low1 and macd_low2 > macd_low1:
        return 1

    # Highs
    price_high1 = recent.iloc[:half].max()
    price_high2 = recent.iloc[half:].max()
    macd_high1 = recent_macd.iloc[:half].max()
    macd_high2 = recent_macd.iloc[half:].max()

    if price_high2 > price_high1 and macd_high2 < macd_high1:
        return -1

    return 0


def compute_indicators(df: pd.DataFrame) -> TechnicalIndicators:
    """Compute RSI(14), BB(20,2), MACD(12,26,9), ATR(14) from OHLCV data.

    Args:
        df: DataFrame with columns: close, high, low, (optionally open, volume).
            Must have at least 30 rows.

    Returns:
        TechnicalIndicators with the latest computed values.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # RSI(14)
    rsi_series = _rsi(close, length=14)
    rsi_val = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0

    # Bollinger Bands(20, 2)
    bb_lower, bb_mid, bb_upper = _bbands(close, length=20, std=2)
    bb_l = bb_lower.iloc[-1]
    bb_m = bb_mid.iloc[-1]
    bb_u = bb_upper.iloc[-1]

    if not np.isnan(bb_l) and not np.isnan(bb_u):
        bb_bandwidth = (bb_u - bb_l) / bb_m if bb_m != 0 else 0.0
        bb_range = bb_u - bb_l
        bb_pctb = (close.iloc[-1] - bb_l) / bb_range if bb_range != 0 else 0.5
    else:
        bb_pctb = 0.5
        bb_bandwidth = 0.0

    # MACD(12, 26, 9)
    macd_line_s, signal_line_s, macd_hist = _macd(close, fast=12, slow=26, signal=9)
    macd_hist_val = float(macd_hist.iloc[-1]) if not np.isnan(macd_hist.iloc[-1]) else 0.0
    macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 and not np.isnan(macd_hist.iloc[-2]) else 0.0
    macd_line_val = float(macd_line_s.iloc[-1]) if not np.isnan(macd_line_s.iloc[-1]) else 0.0
    signal_line_val = float(signal_line_s.iloc[-1]) if not np.isnan(signal_line_s.iloc[-1]) else 0.0

    if macd_hist_val > 0 and macd_hist_prev <= 0:
        macd_cross = 1
    elif macd_hist_val < 0 and macd_hist_prev >= 0:
        macd_cross = -1
    else:
        macd_cross = 0

    # ATR(14)
    atr_series = _atr(high, low, close, length=14)
    atr_val = float(atr_series.iloc[-1]) if not np.isnan(atr_series.iloc[-1]) else 0.0
    atr_pct = (atr_val / close.iloc[-1] * 100) if close.iloc[-1] != 0 else 0.0

    # v2.5: EMA 50/200 and golden/dead cross
    ema_50 = close.ewm(span=50, adjust=False).mean()
    ema_200 = close.ewm(span=200, adjust=False).mean() if len(close) >= 200 else close.ewm(span=min(len(close), 100), adjust=False).mean()
    ema_50_val = float(ema_50.iloc[-1])
    ema_200_val = float(ema_200.iloc[-1])

    golden = False
    dead = False
    if len(ema_50) >= 2 and len(ema_200) >= 2:
        prev_50 = float(ema_50.iloc[-2])
        prev_200 = float(ema_200.iloc[-2])
        if ema_50_val > ema_200_val and prev_50 <= prev_200:
            golden = True
        elif ema_50_val < ema_200_val and prev_50 >= prev_200:
            dead = True

    # 52-week and 20-day highs
    lookback_52w = min(252, len(close))
    high_52w = float(df["high"].astype(float).iloc[-lookback_52w:].max())
    lookback_20d = min(20, len(close))
    high_20d = float(df["high"].astype(float).iloc[-lookback_20d:].max())

    # Volume ratio
    vol = df["volume"].astype(float)
    vol_avg_20 = vol.iloc[-20:].mean() if len(vol) >= 20 else vol.mean()
    vol_ratio = float(vol.iloc[-1] / vol_avg_20) if vol_avg_20 > 0 else 1.0

    # BB squeeze detection
    bb_bw_series = (bb_upper - bb_lower) / bb_mid
    bb_squeeze = False
    if len(bb_bw_series) >= 20:
        recent_bw = float(bb_bw_series.iloc[-1])
        avg_bw = float(bb_bw_series.iloc[-20:].mean())
        if recent_bw < avg_bw * 0.7:
            bb_squeeze = True

    # 3-month return
    lookback_3m = min(60, len(close) - 1)
    if lookback_3m > 0:
        ret_3m = (close.iloc[-1] - close.iloc[-lookback_3m - 1]) / close.iloc[-lookback_3m - 1] * 100
    else:
        ret_3m = 0.0

    # v8.1: Simple moving averages
    ma5_val = float(close.tail(5).mean()) if len(close) >= 5 else float(close.iloc[-1])
    ma20_val = float(close.tail(20).mean()) if len(close) >= 20 else float(close.iloc[-1])
    ma60_val = float(close.tail(60).mean()) if len(close) >= 60 else 0.0
    ma120_val = float(close.tail(120).mean()) if len(close) >= 120 else 0.0

    # v8.1: Divergence detection
    rsi_div = _detect_rsi_divergence(close, rsi_series)
    macd_div = _detect_macd_divergence(close, macd_hist)

    return TechnicalIndicators(
        rsi=round(rsi_val, 2),
        bb_pctb=round(float(bb_pctb), 4),
        bb_bandwidth=round(float(bb_bandwidth), 4),
        macd_histogram=round(macd_hist_val, 4),
        macd_signal_cross=macd_cross,
        atr=round(atr_val, 2),
        atr_pct=round(atr_pct, 2),
        ema_50=round(ema_50_val, 2),
        ema_200=round(ema_200_val, 2),
        golden_cross=golden,
        dead_cross=dead,
        high_52w=round(high_52w, 0),
        high_20d=round(high_20d, 0),
        volume_ratio=round(vol_ratio, 2),
        bb_squeeze=bb_squeeze,
        return_3m_pct=round(float(ret_3m), 2),
        ma5=round(ma5_val, 2),
        ma20=round(ma20_val, 2),
        ma60=round(ma60_val, 2),
        ma120=round(ma120_val, 2),
        macd=round(macd_line_val, 4),
        macd_signal=round(signal_line_val, 4),
        rsi_divergence=rsi_div,
        macd_divergence=macd_div,
    )


def normalize_indicators(
    tech: TechnicalIndicators,
    weights: dict[str, float] | None = None,
) -> NormalizedIndicators:
    """기술적 지표를 0~1 범위로 정규화.

    Args:
        tech: 원본 기술적 지표.
        weights: 합성 점수 가중치. 기본값 사용 시 None.

    Returns:
        NormalizedIndicators with all values in [0, 1].
    """
    # RSI: 0-100 → 0-1
    rsi_norm = max(0.0, min(1.0, tech.rsi / 100.0))
    # RSI 매수 기회: 과매도일수록 높음 (역전)
    rsi_opp = 1.0 - rsi_norm

    # BB %B: already 0-1 range roughly
    bb_pos = max(0.0, min(1.0, tech.bb_pctb))
    bb_opp = 1.0 - bb_pos  # 하단일수록 매수 기회

    # MACD: sigmoid 변환으로 -∞~+∞ → 0~1
    macd_str = _sigmoid(tech.macd_histogram * 100)  # scale up for sensitivity

    # Trend: EMA 50 vs 200 기반
    if tech.ema_200 > 0 and tech.ema_50 > 0:
        ema_ratio = tech.ema_50 / tech.ema_200
        trend = _sigmoid((ema_ratio - 1.0) * 20)  # 1.0 = neutral
    else:
        trend = 0.5
    if tech.golden_cross:
        trend = min(trend + 0.15, 1.0)
    elif tech.dead_cross:
        trend = max(trend - 0.15, 0.0)

    # Momentum: 3개월 수익률 기반
    mom = _sigmoid(tech.return_3m_pct / 10.0)  # ±30% → roughly 0-1

    # Volume: 1.0 = 평균, 2.0+ = 강한 신호
    vol_sig = _sigmoid((tech.volume_ratio - 1.0) * 2)

    # Volatility risk: ATR% 역전 (낮을수록 안전)
    # ATR 1% = 안정, 5%+ = 매우 위험
    vol_risk = max(0.0, min(1.0, 1.0 - (tech.atr_pct - 1.0) / 5.0))

    # Breakout score
    breakout = 0.0
    if tech.high_52w > 0 and tech.ema_50 > 0:
        near_high = tech.ema_50 / tech.high_52w
        if near_high >= 0.98:
            breakout = 0.8
        elif near_high >= 0.95:
            breakout = 0.5
        elif near_high >= 0.90:
            breakout = 0.2
    if tech.bb_squeeze:
        breakout = min(breakout + 0.2, 1.0)

    # Composite weighted average
    if weights is None:
        weights = {
            "rsi_opportunity": 0.15,
            "bb_opportunity": 0.10,
            "macd_strength": 0.15,
            "trend_strength": 0.20,
            "momentum": 0.15,
            "volume_signal": 0.10,
            "volatility_risk": 0.10,
            "breakout_score": 0.05,
        }

    composite = (
        weights.get("rsi_opportunity", 0.15) * rsi_opp
        + weights.get("bb_opportunity", 0.10) * bb_opp
        + weights.get("macd_strength", 0.15) * macd_str
        + weights.get("trend_strength", 0.20) * trend
        + weights.get("momentum", 0.15) * mom
        + weights.get("volume_signal", 0.10) * vol_sig
        + weights.get("volatility_risk", 0.10) * vol_risk
        + weights.get("breakout_score", 0.05) * breakout
    )
    composite = max(0.0, min(1.0, composite))

    return NormalizedIndicators(
        rsi=round(rsi_norm, 4),
        rsi_opportunity=round(rsi_opp, 4),
        bb_position=round(bb_pos, 4),
        bb_opportunity=round(bb_opp, 4),
        macd_strength=round(macd_str, 4),
        trend_strength=round(trend, 4),
        momentum=round(mom, 4),
        volume_signal=round(vol_sig, 4),
        volatility_risk=round(vol_risk, 4),
        breakout_score=round(breakout, 4),
        composite=round(composite, 4),
    )


def _sigmoid(x: float) -> float:
    """Sigmoid function: maps (-inf, +inf) to (0, 1)."""
    import math
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def compute_weekly_trend(df: pd.DataFrame) -> str:
    """Compute weekly trend direction using 50/200 week EMA proxy.

    Since we use daily data, we approximate weekly EMA by using
    longer daily periods: 50-week ~ 250 days, 200-week ~ 1000 days.
    We simplify to 50-day and 200-day EMA trend direction.

    Returns:
        "up" if 50 EMA > 200 EMA and rising, "down" if below, else "neutral"
    """
    close = df["close"].astype(float)
    if len(close) < 50:
        return "neutral"

    ema_50 = close.ewm(span=50, adjust=False).mean()
    ema_long = close.ewm(span=min(200, len(close)), adjust=False).mean()

    if ema_50.iloc[-1] > ema_long.iloc[-1] and ema_50.iloc[-1] > ema_50.iloc[-5]:
        return "up"
    elif ema_50.iloc[-1] < ema_long.iloc[-1] and ema_50.iloc[-1] < ema_50.iloc[-5]:
        return "down"
    return "neutral"


def compute_relative_strength_rank(
    return_3m_pct: float,
    all_returns: list[float],
) -> tuple[int, float]:
    """Compute relative strength rank among all universe stocks.

    Args:
        return_3m_pct: This stock's 3-month return.
        all_returns: 3-month returns of all universe stocks.

    Returns:
        (rank, percentile) where percentile is 0-100 (lower = better).
    """
    if not all_returns:
        return 1, 50.0
    sorted_desc = sorted(all_returns, reverse=True)
    rank = 1
    for val in sorted_desc:
        if return_3m_pct >= val:
            break
        rank += 1
    percentile = rank / len(sorted_desc) * 100
    return rank, round(percentile, 1)


def compute_disparity(df: pd.DataFrame, period: int = 20) -> float:
    """Compute disparity index (price vs moving average).

    Args:
        df: DataFrame with 'close' column.
        period: Moving average period.

    Returns:
        Disparity as percentage (e.g., 110 means 10% above MA).
    """
    close = df["close"].astype(float)
    ma = close.rolling(window=period).mean()
    if ma.iloc[-1] == 0:
        return 100.0
    return round(float(close.iloc[-1] / ma.iloc[-1] * 100), 2)


def compute_near_high_pct(df: pd.DataFrame, period: int = 252) -> float:
    """Compute how close current price is to period high.

    Args:
        df: DataFrame with 'close' column.
        period: Lookback period (default 252 ~= 1 year).

    Returns:
        Percentage of period high (e.g., 95 means at 95% of high).
    """
    close = df["close"].astype(float)
    lookback = min(period, len(close))
    period_high = close.iloc[-lookback:].max()
    if period_high == 0:
        return 0.0
    return round(float(close.iloc[-1] / period_high * 100), 2)
