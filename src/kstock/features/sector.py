"""Sector relative strength calculation.

Computes 1-month returns for sector ETFs, ranks them,
and provides score adjustments for individual stocks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

# Sector ETF mapping: sector name -> ETF code
SECTOR_ETF_MAP = {
    "반도체": "091160",
    "2차전지": "305540",
    "바이오": "244580",
}


@dataclass
class SectorStrength:
    """Sector relative strength result."""

    sector: str
    etf_code: str
    return_1m_pct: float
    rank: int
    total_sectors: int


def compute_sector_returns(ohlcv_map: dict[str, pd.DataFrame]) -> list[SectorStrength]:
    """Compute 1-month return for each sector ETF.

    Args:
        ohlcv_map: dict mapping ETF code -> OHLCV DataFrame

    Returns:
        List of SectorStrength sorted by return (best first).
    """
    results = []
    for sector, etf_code in SECTOR_ETF_MAP.items():
        df = ohlcv_map.get(etf_code)
        if df is None or df.empty or len(df) < 20:
            continue
        close = df["close"].astype(float)
        lookback = min(20, len(close) - 1)
        current = close.iloc[-1]
        past = close.iloc[-lookback - 1]
        if past > 0:
            ret = (current - past) / past * 100
        else:
            ret = 0.0
        results.append(SectorStrength(
            sector=sector,
            etf_code=etf_code,
            return_1m_pct=round(ret, 2),
            rank=0,
            total_sectors=0,
        ))

    results.sort(key=lambda s: s.return_1m_pct, reverse=True)
    total = len(results)
    for i, r in enumerate(results):
        r.rank = i + 1
        r.total_sectors = total

    return results


def get_sector_score_adjustment(
    sector: str,
    sector_strengths: list[SectorStrength],
) -> int:
    """Get score bonus/penalty based on sector rank.

    Returns:
        +5 for top sector, -5 for bottom sector, 0 otherwise.
    """
    if not sector_strengths:
        return 0
    for s in sector_strengths:
        if s.sector == sector:
            if s.rank == 1:
                return 5
            if s.rank == s.total_sectors and s.total_sectors > 1:
                return -5
            return 0
    return 0


def format_sector_strength(strengths: list[SectorStrength]) -> str:
    """Format sector strength for Telegram display."""
    if not strengths:
        return ""
    lines = ["\U0001f3ed 섹터 강도 (1개월)"]
    for s in strengths:
        if s.return_1m_pct > 5:
            emoji = " \U0001f525"
        elif s.return_1m_pct < -2:
            emoji = " \u2744\ufe0f"
        else:
            emoji = ""
        lines.append(f"{s.rank}위  {s.sector} {s.return_1m_pct:+.1f}%{emoji}")
    return "\n".join(lines)
