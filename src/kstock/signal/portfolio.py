"""Portfolio correlation check module.

Computes pairwise correlation between holdings/recommendations
to warn about concentrated risk from highly correlated positions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CorrelationWarning:
    """Warning for highly correlated positions."""

    ticker_a: str
    name_a: str
    ticker_b: str
    name_b: str
    correlation: float
    message: str


def compute_pairwise_correlations(
    ohlcv_map: dict[str, pd.DataFrame],
    ticker_names: dict[str, str],
    threshold: float = 0.8,
    lookback: int = 60,
) -> list[CorrelationWarning]:
    """Compute pairwise price correlations and flag high ones.

    Args:
        ohlcv_map: dict of ticker -> OHLCV DataFrame
        ticker_names: dict of ticker -> name
        threshold: correlation threshold for warning (default 0.8)
        lookback: number of days for correlation window

    Returns:
        List of CorrelationWarning for pairs above threshold.
    """
    # Build return series for each ticker
    returns_map = {}
    for ticker, df in ohlcv_map.items():
        if df is None or df.empty or len(df) < lookback:
            continue
        close = df["close"].astype(float).iloc[-lookback:]
        ret = close.pct_change().dropna()
        if len(ret) >= lookback - 5:
            returns_map[ticker] = ret.values[:lookback - 1]

    tickers = list(returns_map.keys())
    warnings = []

    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            a, b = tickers[i], tickers[j]
            ra, rb = returns_map[a], returns_map[b]
            min_len = min(len(ra), len(rb))
            if min_len < 20:
                continue
            ra_trimmed = ra[:min_len]
            rb_trimmed = rb[:min_len]

            if np.std(ra_trimmed) == 0 or np.std(rb_trimmed) == 0:
                continue

            corr = float(np.corrcoef(ra_trimmed, rb_trimmed)[0, 1])

            if abs(corr) >= threshold:
                name_a = ticker_names.get(a, a)
                name_b = ticker_names.get(b, b)
                warnings.append(CorrelationWarning(
                    ticker_a=a,
                    name_a=name_a,
                    ticker_b=b,
                    name_b=name_b,
                    correlation=round(corr, 2),
                    message=(
                        f"{name_a} \u2194 {name_b}  상관계수 {corr:.2f}\n"
                        f"두 종목이 같이 움직일 확률이 높습니다\n"
                        f"동시에 빠질 리스크가 있으니 한 종목만 추천합니다"
                    ),
                ))

    warnings.sort(key=lambda w: abs(w.correlation), reverse=True)
    return warnings


def has_correlated_position(
    new_ticker: str,
    existing_tickers: list[str],
    ohlcv_map: dict[str, pd.DataFrame],
    threshold: float = 0.8,
    lookback: int = 60,
) -> CorrelationWarning | None:
    """Check if new ticker is highly correlated with existing positions.

    Returns the highest correlation warning, or None if all OK.
    """
    new_df = ohlcv_map.get(new_ticker)
    if new_df is None or new_df.empty or len(new_df) < lookback:
        return None

    new_close = new_df["close"].astype(float).iloc[-lookback:]
    new_ret = new_close.pct_change().dropna().values

    for ex_ticker in existing_tickers:
        ex_df = ohlcv_map.get(ex_ticker)
        if ex_df is None or ex_df.empty or len(ex_df) < lookback:
            continue
        ex_close = ex_df["close"].astype(float).iloc[-lookback:]
        ex_ret = ex_close.pct_change().dropna().values

        min_len = min(len(new_ret), len(ex_ret))
        if min_len < 20:
            continue

        a = new_ret[:min_len]
        b = ex_ret[:min_len]
        if np.std(a) == 0 or np.std(b) == 0:
            continue

        corr = float(np.corrcoef(a, b)[0, 1])
        if abs(corr) >= threshold:
            return CorrelationWarning(
                ticker_a=new_ticker,
                name_a=new_ticker,
                ticker_b=ex_ticker,
                name_b=ex_ticker,
                correlation=round(corr, 2),
                message=f"상관계수 {corr:.2f} - 기존 보유 종목과 높은 상관관계",
            )

    return None


def format_correlation_warnings(warnings: list[CorrelationWarning]) -> str:
    """Format correlation warnings for Telegram."""
    if not warnings:
        return ""
    lines = ["\u26a0\ufe0f 상관관계 경고\n"]
    for w in warnings[:3]:
        lines.append(
            f"{w.name_a} \u2194 {w.name_b}  상관계수 {w.correlation:.2f}"
        )
    lines.append("\n동시에 빠질 리스크가 있으니 분산에 주의하세요")
    return "\n".join(lines)
