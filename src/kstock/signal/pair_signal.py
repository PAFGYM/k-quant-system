"""Pair trading signal evaluator (Section 59 - 페어 트레이딩 시그널).

Evaluates relative value between known stock pairs (e.g. parent-subsidiary,
sector peers) by computing price ratios and z-scores.  When a pair diverges
significantly from its historical mean, a signal is produced.

All functions are pure computation with no external API calls at runtime.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_PAIRS: list[dict] = [
    {
        "a": "086520",
        "a_name": "에코프로",
        "b": "247540",
        "b_name": "에코프로비엠",
        "relationship": "모자회사",
    },
    {
        "a": "005930",
        "a_name": "삼성전자",
        "b": "000660",
        "b_name": "SK하이닉스",
        "relationship": "반도체",
    },
    {
        "a": "005380",
        "a_name": "현대차",
        "b": "000270",
        "b_name": "기아",
        "relationship": "자동차",
    },
]
"""Pre-configured pairs for monitoring.

Each entry contains:
    a / a_name: ticker and name of stock A.
    b / b_name: ticker and name of stock B.
    relationship: Korean description of the pair relationship.
"""

Z_SCORE_THRESHOLD = 2.0
"""Absolute z-score threshold for generating a signal."""

MIN_DATA_POINTS = 20
"""Minimum number of price observations required to compute statistics."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class PairSignal:
    """Represents a pair trading evaluation result.

    Attributes:
        pair: The pair dict from KNOWN_PAIRS (or custom).
        ratio: Current price ratio (A / B).
        mean_ratio: Historical mean of the ratio.
        std_ratio: Historical standard deviation of the ratio.
        z_score: (current ratio - mean) / std.
        signal: Korean label for the condition.
        suggestion: Korean trading suggestion.
        message: Pre-formatted Telegram message.
    """

    pair: dict = field(default_factory=dict)
    ratio: float = 0.0
    mean_ratio: float = 0.0
    std_ratio: float = 0.0
    z_score: float = 0.0
    signal: str = "정상 범위"
    suggestion: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_pair_ratio(
    prices_a: list[float],
    prices_b: list[float],
) -> tuple[float, float, float]:
    """Compute current ratio, historical mean, and std from price series.

    The ratio is defined as price_a / price_b for each matching index.
    The current ratio uses the last element of each series.

    Args:
        prices_a: Daily close prices for stock A (chronological order).
        prices_b: Daily close prices for stock B (chronological order).

    Returns:
        Tuple of (current_ratio, mean_ratio, std_ratio).

    Raises:
        ValueError: If series are too short or have different lengths.
    """
    if len(prices_a) != len(prices_b):
        raise ValueError(
            f"Price series length mismatch: A={len(prices_a)}, B={len(prices_b)}"
        )

    n = len(prices_a)
    if n < MIN_DATA_POINTS:
        raise ValueError(
            f"Insufficient data: {n} points, need at least {MIN_DATA_POINTS}"
        )

    # Compute ratio series, skipping zero denominators
    ratios: list[float] = []
    for pa, pb in zip(prices_a, prices_b):
        if pb > 0:
            ratios.append(pa / pb)

    if len(ratios) < MIN_DATA_POINTS:
        raise ValueError(
            f"Too few valid ratios: {len(ratios)} < {MIN_DATA_POINTS}"
        )

    current_ratio = ratios[-1]
    mean_ratio = sum(ratios) / len(ratios)

    variance = sum((r - mean_ratio) ** 2 for r in ratios) / len(ratios)
    std_ratio = math.sqrt(variance) if variance > 0 else 0.0

    logger.debug(
        "Pair ratio: current=%.4f, mean=%.4f, std=%.4f (%d points)",
        current_ratio, mean_ratio, std_ratio, len(ratios),
    )

    return current_ratio, mean_ratio, std_ratio


def evaluate_pair(
    pair: dict,
    prices_a: list[float],
    prices_b: list[float],
) -> PairSignal:
    """Evaluate a pair and produce a PairSignal.

    Signal logic:
        - Z-score > +2.0: A is overvalued relative to B.
          Suggestion: B 매수 (or A 매도) -- "A 고평가 / B 저평가"
        - Z-score < -2.0: B is overvalued relative to A.
          Suggestion: A 매수 (or B 매도) -- "B 고평가 / A 저평가"
        - Otherwise: within normal range.

    Args:
        pair: Pair configuration dict with keys a, a_name, b, b_name,
            relationship.
        prices_a: Daily close prices for stock A.
        prices_b: Daily close prices for stock B.

    Returns:
        PairSignal with computed metrics and signal.
    """
    a_name = pair.get("a_name", pair.get("a", "?"))
    b_name = pair.get("b_name", pair.get("b", "?"))
    relationship = pair.get("relationship", "")

    try:
        current_ratio, mean_ratio, std_ratio = compute_pair_ratio(
            prices_a, prices_b
        )
    except ValueError as exc:
        logger.warning(
            "Pair evaluation failed %s/%s: %s",
            a_name, b_name, exc,
        )
        return PairSignal(
            pair=pair,
            signal="데이터 부족",
            suggestion="데이터가 부족하여 판단 불가",
        )

    # Compute z-score
    if std_ratio > 0:
        z_score = (current_ratio - mean_ratio) / std_ratio
    else:
        z_score = 0.0

    # Determine signal
    if z_score > Z_SCORE_THRESHOLD:
        signal_label = "A 고평가"
        suggestion = (
            f"{b_name} 매수 고려 (상대적 저평가). "
            f"{a_name} 비중 축소 검토."
        )
    elif z_score < -Z_SCORE_THRESHOLD:
        signal_label = "B 고평가"
        suggestion = (
            f"{a_name} 매수 고려 (상대적 저평가). "
            f"{b_name} 비중 축소 검토."
        )
    else:
        signal_label = "정상 범위"
        suggestion = "현재 비율이 정상 범위 내입니다. 대기."

    result = PairSignal(
        pair=pair,
        ratio=round(current_ratio, 4),
        mean_ratio=round(mean_ratio, 4),
        std_ratio=round(std_ratio, 4),
        z_score=round(z_score, 2),
        signal=signal_label,
        suggestion=suggestion,
    )
    result.message = format_pair_signal(result)

    logger.info(
        "Pair %s/%s (%s): ratio=%.4f, z=%.2f -> %s",
        a_name, b_name, relationship, current_ratio, z_score, signal_label,
    )

    return result


def find_pair(ticker: str) -> list[dict]:
    """Find known pairs that include the given ticker.

    Args:
        ticker: Stock ticker code.

    Returns:
        List of matching pair dicts from KNOWN_PAIRS.
    """
    return [
        p for p in KNOWN_PAIRS
        if p.get("a") == ticker or p.get("b") == ticker
    ]


def evaluate_all_pairs(
    price_data: dict[str, list[float]],
) -> list[PairSignal]:
    """Evaluate all known pairs given a price data mapping.

    Args:
        price_data: Dict mapping ticker -> list of daily close prices.

    Returns:
        List of PairSignal results for all evaluable pairs.
    """
    results: list[PairSignal] = []

    for pair in KNOWN_PAIRS:
        a_ticker = pair["a"]
        b_ticker = pair["b"]

        if a_ticker not in price_data or b_ticker not in price_data:
            logger.debug(
                "Skipping pair %s/%s: missing price data",
                pair.get("a_name", a_ticker),
                pair.get("b_name", b_ticker),
            )
            continue

        signal = evaluate_pair(pair, price_data[a_ticker], price_data[b_ticker])
        results.append(signal)

    return results


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_pair_signal(signal: PairSignal) -> str:
    """Format a PairSignal as a Telegram message.

    Produces clean Korean text without any bold (**) formatting.
    Uses "주호님" for the user greeting.

    Args:
        signal: PairSignal to format.

    Returns:
        Multi-line formatted string suitable for Telegram.

    Example output::

        [페어 분석] 에코프로 / 에코프로비엠
        관계: 모자회사
        현재 비율: 1.2345  (평균 1.1000, 표준편차 0.0500)
        Z-Score: +2.35
        판단: A 고평가
        제안: 에코프로비엠 매수 고려 (상대적 저평가).

        주호님, 페어 괴리가 발생했습니다.
    """
    pair = signal.pair
    a_name = pair.get("a_name", "A")
    b_name = pair.get("b_name", "B")
    relationship = pair.get("relationship", "")

    lines = [
        f"[페어 분석] {a_name} / {b_name}",
    ]

    if relationship:
        lines.append(f"관계: {relationship}")

    if signal.signal == "데이터 부족":
        lines.append(f"판단: {signal.signal}")
        lines.append(f"비고: {signal.suggestion}")
        return "\n".join(lines)

    lines.extend([
        f"현재 비율: {signal.ratio:.4f}  "
        f"(평균 {signal.mean_ratio:.4f}, 표준편차 {signal.std_ratio:.4f})",
        f"Z-Score: {signal.z_score:+.2f}",
        f"판단: {signal.signal}",
        f"제안: {signal.suggestion}",
    ])

    lines.append("")

    if signal.signal == "정상 범위":
        lines.append("주호님, 현재 페어 비율은 정상 범위입니다.")
    else:
        lines.append("주호님, 페어 괴리가 발생했습니다. 확인해보세요.")

    return "\n".join(lines)
