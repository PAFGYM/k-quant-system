"""ROE + Investment (I/A) factor scoring.

Based on Korean market academic research showing ROE + conservative
investment factor produces highest Sharpe ratio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FactorScore:
    """Factor-based scoring breakdown."""

    roe_factor: float  # 0~15 points
    investment_factor: float  # 0~5 points
    per_pbr_factor: float  # 0~10 points
    total: float  # 0~30 (replaces fundamental 30pt)


def compute_factor_score(
    roe: float = 0.0,
    per: float = 0.0,
    pbr: float = 0.0,
    debt_ratio: float = 0.0,
    asset_growth_pct: float = 0.0,
    all_roe_values: list[float] | None = None,
) -> FactorScore:
    """Compute factor-based fundamental score.

    ROE factor (15 points):
        Top 30% ROE -> 15 points
        Middle 40% -> 10 points
        Bottom 30% -> 5 points

    Investment factor (5 points):
        Conservative (low asset growth) -> 5 points
        Neutral -> 3 points
        Aggressive (high asset growth) -> 1 point

    PER/PBR factor (10 points):
        Low PER + Low PBR -> 10 points

    Args:
        roe: Return on equity (%).
        per: Price to earnings ratio.
        pbr: Price to book ratio.
        debt_ratio: Debt to equity ratio (%).
        asset_growth_pct: Total asset YoY growth (%).
        all_roe_values: ROE values of all universe stocks for percentile.
    """
    # ROE factor (15 points)
    if all_roe_values and len(all_roe_values) >= 5:
        sorted_roe = sorted(all_roe_values, reverse=True)
        top_30_cutoff = sorted_roe[max(0, int(len(sorted_roe) * 0.3) - 1)]
        bot_70_cutoff = sorted_roe[max(0, int(len(sorted_roe) * 0.7) - 1)]
        if roe >= top_30_cutoff:
            s_roe = 15.0
        elif roe >= bot_70_cutoff:
            s_roe = 10.0
        else:
            s_roe = 5.0
    else:
        # Fallback: absolute thresholds
        if roe >= 15:
            s_roe = 15.0
        elif roe >= 10:
            s_roe = 12.0
        elif roe >= 7:
            s_roe = 9.0
        elif roe >= 4:
            s_roe = 6.0
        else:
            s_roe = 3.0

    # Investment factor (5 points)
    # Conservative investment (low asset growth) correlates with higher returns
    if asset_growth_pct <= 5:
        s_inv = 5.0
    elif asset_growth_pct <= 15:
        s_inv = 3.0
    else:
        s_inv = 1.0

    # PER/PBR factor (10 points)
    s_val = 0.0
    if 3 <= per <= 15:
        s_val += 5.0
    elif 15 < per <= 25:
        s_val += 3.0
    elif per > 25:
        s_val += 1.0
    else:
        s_val += 2.0  # unknown PER

    if 0 < pbr <= 1.0:
        s_val += 5.0
    elif 0 < pbr <= 2.0:
        s_val += 3.0
    elif pbr > 2.0:
        s_val += 1.0
    else:
        s_val += 2.0  # unknown PBR

    total = round(s_roe + s_inv + s_val, 1)
    return FactorScore(
        roe_factor=s_roe,
        investment_factor=s_inv,
        per_pbr_factor=s_val,
        total=total,
    )
