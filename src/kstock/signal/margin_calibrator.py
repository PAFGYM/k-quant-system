"""Adaptive margin/short threshold calibrator (적응형 임계값 보정기).

Computes dynamic thresholds based on 60-day rolling statistics (mean ± σ)
instead of fixed constants. This allows the system to adapt to different
stocks with varying baseline short/margin levels.

All functions are pure computation with no external API calls.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CalibrationResult:
    """Adaptive threshold for a single metric."""

    ticker: str
    metric: str           # e.g., "short_ratio", "credit_ratio"
    mean_60d: float       # 60-day mean
    std_60d: float        # 60-day standard deviation
    upper_1sigma: float   # mean + 1σ
    lower_1sigma: float   # mean - 1σ
    upper_2sigma: float   # mean + 2σ
    lower_2sigma: float   # mean - 2σ
    current_value: float  # Latest observed value
    z_score: float        # (current - mean) / std
    alert_level: str      # "normal", "elevated", "extreme"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_DATA_POINTS = 20    # Minimum data points for calibration
_DEFAULT_WINDOW = 60     # Rolling window in days


# ---------------------------------------------------------------------------
# Core calibration
# ---------------------------------------------------------------------------

def _compute_stats(values: list[float]) -> tuple[float, float]:
    """Compute mean and standard deviation of a list of values."""
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    std = math.sqrt(variance)
    return round(mean, 4), round(std, 4)


def calibrate_metric(
    history: list[dict],
    metric_key: str,
    ticker: str = "",
    window: int = _DEFAULT_WINDOW,
) -> CalibrationResult | None:
    """Calibrate adaptive thresholds for a single metric.

    Args:
        history: List of daily data dicts sorted by date ascending.
        metric_key: The key in each dict to calibrate (e.g., "short_ratio").
        ticker: Stock ticker code.
        window: Rolling window size in days.

    Returns:
        CalibrationResult or None if insufficient data.
    """
    if len(history) < _MIN_DATA_POINTS:
        return None

    # Extract metric values from the window
    recent = history[-window:] if len(history) >= window else history
    values = [e.get(metric_key, 0.0) for e in recent]

    mean, std = _compute_stats(values)
    current_value = values[-1] if values else 0.0

    # Compute z-score
    z_score = (current_value - mean) / std if std > 0 else 0.0
    z_score = round(z_score, 2)

    # Determine alert level
    if abs(z_score) >= 2.0:
        alert_level = "extreme"
    elif abs(z_score) >= 1.0:
        alert_level = "elevated"
    else:
        alert_level = "normal"

    return CalibrationResult(
        ticker=ticker,
        metric=metric_key,
        mean_60d=mean,
        std_60d=std,
        upper_1sigma=round(mean + std, 4),
        lower_1sigma=round(max(0, mean - std), 4),
        upper_2sigma=round(mean + 2 * std, 4),
        lower_2sigma=round(max(0, mean - 2 * std), 4),
        current_value=round(current_value, 4),
        z_score=z_score,
        alert_level=alert_level,
    )


def calibrate_all_metrics(
    short_history: list[dict],
    margin_history: list[dict] | None = None,
    ticker: str = "",
) -> list[CalibrationResult]:
    """Calibrate all relevant metrics for a stock.

    Calibrates: short_ratio, short_balance_ratio, credit_ratio.

    Args:
        short_history: Daily short selling data.
        margin_history: Daily margin balance data.
        ticker: Stock ticker code.

    Returns:
        List of CalibrationResult for each metric.
    """
    results = []

    # Short ratio
    sr = calibrate_metric(short_history, "short_ratio", ticker)
    if sr:
        results.append(sr)

    # Short balance ratio
    sbr = calibrate_metric(short_history, "short_balance_ratio", ticker)
    if sbr:
        results.append(sbr)

    # Credit ratio
    if margin_history:
        cr = calibrate_metric(margin_history, "credit_ratio", ticker)
        if cr:
            results.append(cr)

    logger.info(
        "Calibrated %d metrics for %s", len(results), ticker,
    )

    return results


def is_anomalous(calibration: CalibrationResult, sigma_threshold: float = 2.0) -> bool:
    """Check if the current value is anomalous (beyond N sigma).

    Args:
        calibration: Calibration result for a metric.
        sigma_threshold: Number of standard deviations for anomaly.

    Returns:
        True if anomalous.
    """
    return abs(calibration.z_score) >= sigma_threshold


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_calibration_report(results: list[CalibrationResult], name: str = "") -> str:
    """Format calibration results for Telegram.

    No ** bold markers. Korean text throughout.
    """
    lines: list[str] = []

    ticker = results[0].ticker if results else ""
    lines.append(f"\U0001f4cf {name} ({ticker}) 적응형 임계값 분석")
    lines.append("")

    metric_labels = {
        "short_ratio": "공매도 비중",
        "short_balance_ratio": "공매도 잔고 비율",
        "credit_ratio": "신용 비율",
    }

    for r in results:
        label = metric_labels.get(r.metric, r.metric)
        if r.alert_level == "extreme":
            emoji = "\U0001f6a8"
        elif r.alert_level == "elevated":
            emoji = "\U0001f7e1"
        else:
            emoji = "\U0001f7e2"

        lines.append(f"  {emoji} {label}")
        lines.append(f"     현재: {r.current_value:.2f}% (z={r.z_score:+.1f})")
        lines.append(f"     60일 평균: {r.mean_60d:.2f}% (표준편차 {r.std_60d:.2f})")
        lines.append(f"     정상 범위: {r.lower_1sigma:.2f}% ~ {r.upper_1sigma:.2f}%")
        lines.append(f"     극단 범위: {r.lower_2sigma:.2f}% ~ {r.upper_2sigma:.2f}%")
        lines.append("")

    anomalous = [r for r in results if is_anomalous(r)]
    if anomalous:
        lines.append(f"\U0001f6a8 {len(anomalous)}개 지표 이상 감지")
    else:
        lines.append("\U0001f7e2 모든 지표 정상 범위")

    return "\n".join(lines)
