"""Multi-timeframe (MTF) analysis engine.

Resamples daily OHLCV into weekly/monthly, computes per-timeframe
trend & RSI, then determines cross-timeframe alignment and breakout signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TimeframeData:
    """Container for multi-timeframe OHLCV data."""

    ticker: str
    daily: pd.DataFrame
    weekly: pd.DataFrame
    monthly: pd.DataFrame


@dataclass
class MTFSignal:
    """Multi-timeframe trend alignment signal."""

    ticker: str
    daily_trend: str  # "up" / "down" / "neutral"
    weekly_trend: str
    monthly_trend: str
    alignment: str  # "all_up" / "all_down" / "mixed_bullish" / "mixed_bearish" / "neutral"
    alignment_score: float  # -1.0 ~ +1.0
    daily_rsi: float
    weekly_rsi: float
    monthly_rsi: float
    confirmation: bool  # weekly agrees with daily
    message: str


@dataclass
class MTFBreakout:
    """Multi-timeframe breakout detection result."""

    ticker: str
    daily_breakout: bool
    weekly_breakout: bool
    monthly_breakout: bool
    breakout_level: str  # "daily" / "weekly" / "monthly" / "none"
    breakout_price: float
    strength: str  # "strong" / "moderate" / "none"


# ---------------------------------------------------------------------------
# Resampling helpers
# ---------------------------------------------------------------------------

_OHLCV_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure lowercase column names for consistent access."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    return df


def resample_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly bars (week ending Friday).

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily OHLCV with a DatetimeIndex (or a ``date`` column).

    Returns
    -------
    pd.DataFrame
        Weekly OHLCV with a DatetimeIndex.
    """
    df = _normalise_columns(daily_df)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df.index = pd.to_datetime(df["date"])
        else:
            df.index = pd.to_datetime(df.index)

    agg = {k: v for k, v in _OHLCV_AGG.items() if k in df.columns}
    weekly = df.resample("W-FRI").agg(agg).dropna(subset=["close"])
    return weekly


def resample_to_monthly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to monthly bars.

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily OHLCV with a DatetimeIndex (or a ``date`` column).

    Returns
    -------
    pd.DataFrame
        Monthly OHLCV with a DatetimeIndex.
    """
    df = _normalise_columns(daily_df)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df.index = pd.to_datetime(df["date"])
        else:
            df.index = pd.to_datetime(df.index)

    agg = {k: v for k, v in _OHLCV_AGG.items() if k in df.columns}
    monthly = df.resample("ME").agg(agg).dropna(subset=["close"])
    return monthly


# ---------------------------------------------------------------------------
# Build multi-timeframe data
# ---------------------------------------------------------------------------


def build_timeframe_data(ticker: str, daily_df: pd.DataFrame) -> TimeframeData:
    """Create :class:`TimeframeData` from a daily OHLCV DataFrame.

    Generates weekly and monthly resampled data automatically.
    """
    daily = _normalise_columns(daily_df)
    if not isinstance(daily.index, pd.DatetimeIndex):
        if "date" in daily.columns:
            daily.index = pd.to_datetime(daily["date"])
        else:
            daily.index = pd.to_datetime(daily.index)

    weekly = resample_to_weekly(daily)
    monthly = resample_to_monthly(daily)
    return TimeframeData(ticker=ticker, daily=daily, weekly=weekly, monthly=monthly)


# ---------------------------------------------------------------------------
# Technical helpers
# ---------------------------------------------------------------------------


def _compute_ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return close.ewm(span=period, min_periods=period, adjust=False).mean()


def _compute_rsi(close: pd.Series, length: int = 14) -> float:
    """Compute RSI using Wilder's smoothing and return the latest value.

    Returns 50.0 (neutral) when there is insufficient data.
    """
    if close is None or len(close) < length + 1:
        return 50.0

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    last_loss = avg_loss.iloc[-1]
    if last_loss == 0:
        return 100.0

    rs = avg_gain.iloc[-1] / last_loss
    return round(100 - (100 / (1 + rs)), 2)


def _determine_trend(
    df: pd.DataFrame,
    ema_fast: int = 10,
    ema_slow: int = 30,
) -> str:
    """Determine trend direction from a price DataFrame.

    Returns
    -------
    str
        ``"up"`` if close > EMA(fast) > EMA(slow),
        ``"down"`` if close < EMA(fast) < EMA(slow),
        ``"neutral"`` otherwise.
    """
    if df is None or len(df) < ema_slow:
        return "neutral"

    close = df["close"] if "close" in df.columns else df["Close"]
    fast = _compute_ema(close, ema_fast)
    slow = _compute_ema(close, ema_slow)

    last_close = close.iloc[-1]
    last_fast = fast.iloc[-1]
    last_slow = slow.iloc[-1]

    if last_close > last_fast > last_slow:
        return "up"
    if last_close < last_fast < last_slow:
        return "down"
    return "neutral"


# ---------------------------------------------------------------------------
# MTF alignment analysis
# ---------------------------------------------------------------------------

_TREND_SCORE = {"up": 1.0, "neutral": 0.0, "down": -1.0}


def _classify_alignment(daily: str, weekly: str, monthly: str) -> str:
    """Classify overall alignment from three trend strings."""
    trends = (daily, weekly, monthly)
    if all(t == "up" for t in trends):
        return "all_up"
    if all(t == "down" for t in trends):
        return "all_down"

    score = sum(_TREND_SCORE[t] for t in trends)
    if score > 0:
        return "mixed_bullish"
    if score < 0:
        return "mixed_bearish"
    return "neutral"


def _build_message(ticker: str, alignment: str, score: float) -> str:
    """Build a human-readable summary message."""
    emoji = {
        "all_up": "\U0001f7e2",      # green circle
        "all_down": "\U0001f534",     # red circle
        "mixed_bullish": "\U0001f7e1",  # yellow circle
        "mixed_bearish": "\U0001f7e0",  # orange circle
        "neutral": "\u26aa",          # white circle
    }
    label = {
        "all_up": "전 타임프레임 상승",
        "all_down": "전 타임프레임 하락",
        "mixed_bullish": "혼합 (강세 우위)",
        "mixed_bearish": "혼합 (약세 우위)",
        "neutral": "중립",
    }
    icon = emoji.get(alignment, "\u26aa")
    desc = label.get(alignment, "중립")
    return f"{icon} {ticker} MTF: {desc} (점수 {score:+.2f})"


def analyze_mtf_alignment(
    ticker: str,
    tf_data: TimeframeData,
) -> MTFSignal:
    """Analyse multi-timeframe trend alignment.

    Parameters
    ----------
    ticker : str
        Stock ticker.
    tf_data : TimeframeData
        Pre-built timeframe data.

    Returns
    -------
    MTFSignal
    """
    d_trend = _determine_trend(tf_data.daily, ema_fast=10, ema_slow=30)
    w_trend = _determine_trend(tf_data.weekly, ema_fast=5, ema_slow=15)
    m_trend = _determine_trend(tf_data.monthly, ema_fast=3, ema_slow=8)

    alignment = _classify_alignment(d_trend, w_trend, m_trend)
    score = round(
        sum(_TREND_SCORE[t] for t in (d_trend, w_trend, m_trend)) / 3, 4
    )

    d_close = tf_data.daily["close"] if "close" in tf_data.daily.columns else tf_data.daily.get("Close", pd.Series(dtype=float))
    w_close = tf_data.weekly["close"] if "close" in tf_data.weekly.columns else tf_data.weekly.get("Close", pd.Series(dtype=float))
    m_close = tf_data.monthly["close"] if "close" in tf_data.monthly.columns else tf_data.monthly.get("Close", pd.Series(dtype=float))

    d_rsi = _compute_rsi(d_close)
    w_rsi = _compute_rsi(w_close)
    m_rsi = _compute_rsi(m_close)

    confirmation = d_trend == w_trend
    message = _build_message(ticker, alignment, score)

    return MTFSignal(
        ticker=ticker,
        daily_trend=d_trend,
        weekly_trend=w_trend,
        monthly_trend=m_trend,
        alignment=alignment,
        alignment_score=score,
        daily_rsi=d_rsi,
        weekly_rsi=w_rsi,
        monthly_rsi=m_rsi,
        confirmation=confirmation,
        message=message,
    )


# ---------------------------------------------------------------------------
# MTF breakout detection
# ---------------------------------------------------------------------------


def _check_breakout(df: pd.DataFrame, lookback: int) -> tuple[bool, float]:
    """Check if the latest close exceeds the prior high over *lookback* bars.

    Returns ``(is_breakout, breakout_price)``.
    """
    if df is None or len(df) < lookback + 1:
        return False, 0.0

    high_col = "high" if "high" in df.columns else "High"
    close_col = "close" if "close" in df.columns else "Close"

    prior_high = df[high_col].iloc[-(lookback + 1) : -1].max()
    last_close = df[close_col].iloc[-1]

    if np.isnan(prior_high) or np.isnan(last_close):
        return False, 0.0

    return bool(last_close > prior_high), float(last_close)


def detect_mtf_breakout(
    ticker: str,
    tf_data: TimeframeData,
    lookback_daily: int = 20,
    lookback_weekly: int = 10,
    lookback_monthly: int = 6,
) -> MTFBreakout:
    """Detect breakouts across daily / weekly / monthly timeframes.

    Parameters
    ----------
    ticker : str
        Stock ticker.
    tf_data : TimeframeData
        Pre-built timeframe data.
    lookback_daily : int
        Number of prior daily bars to consider for breakout.
    lookback_weekly : int
        Number of prior weekly bars.
    lookback_monthly : int
        Number of prior monthly bars.

    Returns
    -------
    MTFBreakout
    """
    d_brk, d_price = _check_breakout(tf_data.daily, lookback_daily)
    w_brk, w_price = _check_breakout(tf_data.weekly, lookback_weekly)
    m_brk, m_price = _check_breakout(tf_data.monthly, lookback_monthly)

    # Determine highest breakout level
    if m_brk:
        level = "monthly"
        price = m_price
    elif w_brk:
        level = "weekly"
        price = w_price
    elif d_brk:
        level = "daily"
        price = d_price
    else:
        level = "none"
        price = 0.0

    count = sum([d_brk, w_brk, m_brk])
    if count >= 2:
        strength = "strong"
    elif count == 1:
        strength = "moderate"
    else:
        strength = "none"

    return MTFBreakout(
        ticker=ticker,
        daily_breakout=d_brk,
        weekly_breakout=w_brk,
        monthly_breakout=m_brk,
        breakout_level=level,
        breakout_price=price,
        strength=strength,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_mtf_signal(signal: MTFSignal) -> str:
    """Format an MTF signal for Telegram display (plain text + emoji)."""
    trend_icon = {"up": "\u2b06", "down": "\u2b07", "neutral": "\u2796"}
    lines = [
        signal.message,
        "",
        f"  일봉  {trend_icon.get(signal.daily_trend, '-')} {signal.daily_trend}  RSI {signal.daily_rsi:.1f}",
        f"  주봉  {trend_icon.get(signal.weekly_trend, '-')} {signal.weekly_trend}  RSI {signal.weekly_rsi:.1f}",
        f"  월봉  {trend_icon.get(signal.monthly_trend, '-')} {signal.monthly_trend}  RSI {signal.monthly_rsi:.1f}",
        "",
        f"  확인: {'OK' if signal.confirmation else 'X'}  점수: {signal.alignment_score:+.2f}",
    ]
    return "\n".join(lines)


def format_mtf_breakout(breakout: MTFBreakout) -> str:
    """Format an MTF breakout result for Telegram display (plain text + emoji)."""
    if breakout.strength == "none":
        return f"\u26aa {breakout.ticker} 돌파 신호 없음"

    icon = "\U0001f525" if breakout.strength == "strong" else "\u26a1"
    parts = []
    if breakout.daily_breakout:
        parts.append("일봉")
    if breakout.weekly_breakout:
        parts.append("주봉")
    if breakout.monthly_breakout:
        parts.append("월봉")

    tf_str = "+".join(parts)
    lines = [
        f"{icon} {breakout.ticker} 돌파 감지 ({breakout.strength})",
        f"  레벨: {breakout.breakout_level}  가격: {breakout.breakout_price:,.0f}",
        f"  타임프레임: {tf_str}",
    ]
    return "\n".join(lines)
