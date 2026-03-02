"""K-Quant advanced sentiment analysis module.

Quantitative sentiment strength, contrarian signal detection, sentiment regime
classification, and news-impact decay modelling.  Pure functions + dataclasses;
depends only on numpy / pandas / stdlib.

Python 3.9 compatible (``from __future__ import annotations``).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class NewsImpact:
    """Single headline's measured market impact."""

    headline: str = ""
    published_at: str = ""
    sentiment_score: float = 0.0
    impact_score: float = 0.0
    price_reaction_pct: float = 0.0
    reaction_window_minutes: int = 60
    decay_half_life_hours: float = 24.0
    category: str = "market"


@dataclass
class SentimentStrength:
    """Quantitative sentiment strength metrics for a ticker."""

    ticker: str = ""
    raw_score: float = 0.0
    magnitude: float = 0.0
    confidence: float = 0.0
    agreement_ratio: float = 0.0
    contrarian_signal: bool = False
    weighted_score: float = 0.0


@dataclass
class ContrarianSignal:
    """Divergence between sentiment direction and price direction."""

    ticker: str = ""
    sentiment_direction: str = "neutral"
    price_direction: str = "neutral"
    divergence_days: int = 0
    signal_strength: float = 0.0
    historical_accuracy: float = 0.0
    recommendation: str = ""


@dataclass
class SentimentRegime:
    """Current market sentiment regime classification."""

    regime: str = "neutral"
    score: float = 0.0
    duration_days: int = 0
    mean_reversion_prob: float = 0.5
    historical_return_after: float = 0.0


@dataclass
class SentimentReport:
    """Aggregated sentiment report for a single ticker."""

    ticker: str = ""
    current_sentiment: SentimentStrength = field(
        default_factory=SentimentStrength,
    )
    news_impacts: List[NewsImpact] = field(default_factory=list)
    contrarian: Optional[ContrarianSignal] = None
    regime: SentimentRegime = field(default_factory=SentimentRegime)
    composite_signal: float = 0.0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "earnings": ["실적", "매출", "영업이익", "순이익", "분기", "earnings", "revenue"],
    "macro": ["금리", "환율", "GDP", "물가", "인플레", "CPI", "기준금리", "macro"],
    "analyst": ["목표가", "투자의견", "리포트", "애널", "컨센서스", "rating"],
    "corporate": ["인수", "합병", "M&A", "유상증자", "배당", "자사주", "경영권"],
    "market": [],  # fallback
}

_REACTION_WINDOWS_MINUTES = [30, 60, 240, 1440]  # 30m, 1h, 4h, 1d

# Regime thresholds
_EUPHORIA_THRESHOLD = 0.7
_OPTIMISM_THRESHOLD = 0.3
_FEAR_THRESHOLD = -0.3
_PANIC_THRESHOLD = -0.7

# Historical regime return estimates (empirical proxies)
_REGIME_RETURN_MAP: dict[str, float] = {
    "euphoria": -0.02,
    "optimism": 0.005,
    "neutral": 0.003,
    "fear": 0.01,
    "panic": 0.04,
}

_REGIME_REVERSION_MAP: dict[str, float] = {
    "euphoria": 0.75,
    "optimism": 0.40,
    "neutral": 0.20,
    "fear": 0.45,
    "panic": 0.80,
}


# ---------------------------------------------------------------------------
# 1. compute_news_impact
# ---------------------------------------------------------------------------


def _classify_headline_category(headline: str) -> str:
    """Return the best-matching category for *headline*."""
    headline_lower = headline.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if cat == "market":
            continue
        for kw in keywords:
            if kw.lower() in headline_lower:
                return cat
    return "market"


def _find_nearest_price_index(
    price_times: pd.DatetimeIndex,
    target: datetime,
) -> int | None:
    """Return the index of the closest timestamp >= *target*, or None."""
    later = price_times[price_times >= target]
    if later.empty:
        return None
    return price_times.get_loc(later[0])


def compute_news_impact(
    headlines: list[dict],
    price_data: pd.DataFrame,
) -> list[NewsImpact]:
    """Measure impact of each headline on price movements.

    Parameters
    ----------
    headlines:
        ``[{"headline": str, "published_at": str, "sentiment": float}, ...]``
        ``published_at`` is ISO-8601 or ``%Y-%m-%d %H:%M`` (KST assumed).
    price_data:
        DataFrame with a DatetimeIndex (timezone-aware or naive KST) and
        a ``"close"`` column.

    Returns
    -------
    list[NewsImpact]
    """
    if not headlines or price_data.empty:
        return []

    # Normalise price index to tz-aware KST
    idx = price_data.index
    if not hasattr(idx, "tz") or idx.tz is None:
        idx = idx.tz_localize(KST)
    else:
        idx = idx.tz_convert(KST)
    price_data = price_data.copy()
    price_data.index = idx

    results: list[NewsImpact] = []
    for item in headlines:
        headline = item.get("headline", "")
        published_str = item.get("published_at", "")
        sentiment = float(item.get("sentiment", 0.0))

        # Parse published_at
        pub_dt: datetime | None = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                pub_dt = datetime.strptime(published_str, fmt).replace(tzinfo=KST)
                break
            except (ValueError, TypeError):
                continue
        if pub_dt is None:
            results.append(NewsImpact(headline=headline, published_at=published_str,
                                      sentiment_score=sentiment,
                                      category=_classify_headline_category(headline)))
            continue

        # Find price at publication and after each reaction window
        base_idx = _find_nearest_price_index(price_data.index, pub_dt)
        if base_idx is None:
            results.append(NewsImpact(headline=headline, published_at=published_str,
                                      sentiment_score=sentiment,
                                      category=_classify_headline_category(headline)))
            continue

        base_price = float(price_data.iloc[base_idx]["close"])
        if base_price == 0:
            results.append(NewsImpact(headline=headline, published_at=published_str,
                                      sentiment_score=sentiment,
                                      category=_classify_headline_category(headline)))
            continue

        # Compute reactions for each window; keep best window
        best_reaction = 0.0
        best_window = _REACTION_WINDOWS_MINUTES[0]
        for win_min in _REACTION_WINDOWS_MINUTES:
            target_time = pub_dt + pd.Timedelta(minutes=win_min)
            end_idx = _find_nearest_price_index(price_data.index, target_time)
            if end_idx is None:
                continue
            end_price = float(price_data.iloc[end_idx]["close"])
            reaction = (end_price - base_price) / base_price * 100.0
            if abs(reaction) > abs(best_reaction):
                best_reaction = reaction
                best_window = win_min

        # Impact = |reaction| * alignment factor
        # alignment: sentiment and price in same direction => higher impact
        if sentiment != 0 and best_reaction != 0:
            same_dir = (sentiment > 0 and best_reaction > 0) or (
                sentiment < 0 and best_reaction < 0
            )
            alignment = 1.5 if same_dir else 0.5
        else:
            alignment = 1.0

        impact = abs(best_reaction) * alignment

        # Estimate decay half-life: stronger impact decays slower
        half_life = max(4.0, min(72.0, 24.0 * (1 + abs(best_reaction) / 2)))

        category = _classify_headline_category(headline)

        results.append(
            NewsImpact(
                headline=headline,
                published_at=published_str,
                sentiment_score=sentiment,
                impact_score=round(impact, 4),
                price_reaction_pct=round(best_reaction, 4),
                reaction_window_minutes=best_window,
                decay_half_life_hours=round(half_life, 2),
                category=category,
            )
        )

    return results


# ---------------------------------------------------------------------------
# 2. compute_sentiment_strength
# ---------------------------------------------------------------------------


def compute_sentiment_strength(
    sentiments: list[float],
    volumes: list[int] | None = None,
    ticker: str = "",
) -> SentimentStrength:
    """Compute multi-dimensional sentiment strength.

    Parameters
    ----------
    sentiments:
        List of individual sentiment scores in [-1, 1].
    volumes:
        Optional parallel list of trading volumes for weighting.
    ticker:
        Stock ticker for labelling.

    Returns
    -------
    SentimentStrength
    """
    if not sentiments:
        return SentimentStrength(ticker=ticker)

    arr = np.array(sentiments, dtype=np.float64)

    raw_score = float(np.mean(arr))
    magnitude = float(np.mean(np.abs(arr)))
    std = float(np.std(arr, ddof=0))
    confidence = max(0.0, 1.0 - std)

    n_positive = int(np.sum(arr > 0))
    n_negative = int(np.sum(arr < 0))
    total = len(arr)
    agreement_ratio = max(n_positive, n_negative) / total if total > 0 else 0.0

    # Contrarian: extreme average sentiment suggests potential mean reversion
    contrarian_signal = abs(raw_score) > 0.8

    # Weighted score
    if volumes is not None and len(volumes) == len(sentiments):
        vol_arr = np.array(volumes, dtype=np.float64)
        vol_sum = vol_arr.sum()
        if vol_sum > 0:
            vol_weights = vol_arr / vol_sum
            weighted_raw = float(np.dot(arr, vol_weights))
        else:
            weighted_raw = raw_score
        weighted_score = weighted_raw * magnitude * confidence
    else:
        weighted_score = raw_score * magnitude * confidence

    return SentimentStrength(
        ticker=ticker,
        raw_score=round(raw_score, 4),
        magnitude=round(magnitude, 4),
        confidence=round(confidence, 4),
        agreement_ratio=round(agreement_ratio, 4),
        contrarian_signal=contrarian_signal,
        weighted_score=round(weighted_score, 4),
    )


# ---------------------------------------------------------------------------
# 3. detect_contrarian_signal
# ---------------------------------------------------------------------------


def detect_contrarian_signal(
    sentiment_history: list[float],
    price_returns: list[float],
    lookback: int = 20,
    ticker: str = "",
) -> ContrarianSignal | None:
    """Detect sentiment/price divergence (contrarian opportunity).

    Parameters
    ----------
    sentiment_history:
        Daily sentiment scores (most recent last).
    price_returns:
        Daily price returns (most recent last), same length as sentiment.
    lookback:
        Number of trailing days to analyse.
    ticker:
        Stock ticker for labelling.

    Returns
    -------
    ContrarianSignal or None if no divergence detected.
    """
    if len(sentiment_history) < lookback or len(price_returns) < lookback:
        return None

    sent_arr = np.array(sentiment_history[-lookback:], dtype=np.float64)
    ret_arr = np.array(price_returns[-lookback:], dtype=np.float64)

    sent_mean = float(np.mean(sent_arr))
    sent_std = float(np.std(sent_arr, ddof=1)) if len(sent_arr) > 1 else 1.0

    # Z-score of recent sentiment (last 5 days vs full window)
    recent_sent = float(np.mean(sent_arr[-5:]))
    z_score = (recent_sent - sent_mean) / sent_std if sent_std > 0 else 0.0

    # Price direction over same trailing window
    cumulative_return = float(np.sum(ret_arr[-5:]))

    # Need extreme sentiment (|z| > 2) AND opposite price direction
    extreme = abs(z_score) > 2.0
    divergent = (z_score > 0 and cumulative_return < 0) or (
        z_score < 0 and cumulative_return > 0
    )

    if not (extreme and divergent):
        return None

    sent_dir = "bullish" if z_score > 0 else "bearish"
    price_dir = "up" if cumulative_return > 0 else "down"

    # Divergence days: how many consecutive days sentiment and return disagree
    divergence_days = 0
    for i in range(len(sent_arr) - 1, -1, -1):
        if (sent_arr[i] > 0 and ret_arr[i] < 0) or (sent_arr[i] < 0 and ret_arr[i] > 0):
            divergence_days += 1
        else:
            break

    signal_strength = min(1.0, abs(z_score) / 4.0)

    # Historical accuracy proxy: proportion of past divergences that reverted
    # (simplified heuristic based on z-score magnitude)
    historical_accuracy = min(0.85, 0.5 + abs(z_score) * 0.1)

    if sent_dir == "bullish" and price_dir == "down":
        recommendation = "sentiment_too_high_vs_price"
    else:
        recommendation = "sentiment_too_low_vs_price"

    return ContrarianSignal(
        ticker=ticker,
        sentiment_direction=sent_dir,
        price_direction=price_dir,
        divergence_days=divergence_days,
        signal_strength=round(signal_strength, 4),
        historical_accuracy=round(historical_accuracy, 4),
        recommendation=recommendation,
    )


# ---------------------------------------------------------------------------
# 4. classify_sentiment_regime
# ---------------------------------------------------------------------------


def classify_sentiment_regime(
    sentiment_history: list[float],
    lookback: int = 60,
) -> SentimentRegime:
    """Classify the current sentiment regime.

    Parameters
    ----------
    sentiment_history:
        Daily sentiment scores (most recent last).
    lookback:
        Window size for classification.

    Returns
    -------
    SentimentRegime
    """
    if not sentiment_history:
        return SentimentRegime()

    window = sentiment_history[-lookback:]
    arr = np.array(window, dtype=np.float64)

    avg = float(np.mean(arr))
    std = float(np.std(arr, ddof=0))

    # Classify regime
    if avg >= _EUPHORIA_THRESHOLD and std < 0.3:
        regime = "euphoria"
    elif avg >= _OPTIMISM_THRESHOLD:
        regime = "optimism"
    elif avg <= _PANIC_THRESHOLD and std > 0.2:
        regime = "panic"
    elif avg <= _FEAR_THRESHOLD:
        regime = "fear"
    else:
        regime = "neutral"

    # Duration: consecutive days in current regime bracket
    duration = 0
    for val in reversed(window):
        if regime == "euphoria" and val >= _EUPHORIA_THRESHOLD:
            duration += 1
        elif regime == "optimism" and _OPTIMISM_THRESHOLD <= val < _EUPHORIA_THRESHOLD:
            duration += 1
        elif regime == "panic" and val <= _PANIC_THRESHOLD:
            duration += 1
        elif regime == "fear" and _FEAR_THRESHOLD >= val > _PANIC_THRESHOLD:
            duration += 1
        elif regime == "neutral" and _FEAR_THRESHOLD < val < _OPTIMISM_THRESHOLD:
            duration += 1
        else:
            break

    mean_reversion_prob = _REGIME_REVERSION_MAP.get(regime, 0.2)
    historical_return = _REGIME_RETURN_MAP.get(regime, 0.0)

    return SentimentRegime(
        regime=regime,
        score=round(avg, 4),
        duration_days=duration,
        mean_reversion_prob=round(mean_reversion_prob, 4),
        historical_return_after=round(historical_return, 4),
    )


# ---------------------------------------------------------------------------
# 5. compute_news_decay
# ---------------------------------------------------------------------------


def compute_news_decay(
    impact_scores: list[float],
    hours_elapsed: list[float],
) -> float:
    """Fit exponential decay and return the current residual impact.

    Model: ``impact(t) = A * exp(-lambda * t)``

    Parameters
    ----------
    impact_scores:
        Observed impact at each time point.
    hours_elapsed:
        Hours since the news event for each observation.

    Returns
    -------
    Current (most recent) residual impact after decay.
    Returns 0.0 on insufficient data or fitting failure.
    """
    if not impact_scores or not hours_elapsed:
        return 0.0
    if len(impact_scores) != len(hours_elapsed):
        return 0.0

    impacts = np.array(impact_scores, dtype=np.float64)
    hours = np.array(hours_elapsed, dtype=np.float64)

    # Filter out non-positive impacts and non-positive hours for log-linear fit
    mask = (impacts > 0) & (hours >= 0)
    if mask.sum() < 2:
        # Cannot fit; return last impact scaled by simple heuristic
        if len(impact_scores) > 0:
            return float(impacts[-1]) * 0.5
        return 0.0

    y = np.log(impacts[mask])
    x = hours[mask]

    # Least squares: ln(impact) = ln(A) - lambda * t
    n = len(x)
    sx = float(np.sum(x))
    sy = float(np.sum(y))
    sxx = float(np.sum(x * x))
    sxy = float(np.sum(x * y))

    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return float(impacts[-1]) * 0.5

    lam = -(n * sxy - sx * sy) / denom  # positive lambda = decay
    ln_a = (sy + lam * sx) / n if n > 0 else 0.0

    if lam < 0:
        # Impact is *increasing* — unusual; clamp to last observed
        return float(impacts[-1])

    a = math.exp(ln_a)
    latest_hour = float(hours[-1])
    residual = a * math.exp(-lam * latest_hour)

    return round(max(residual, 0.0), 4)


# ---------------------------------------------------------------------------
# 6. generate_sentiment_report
# ---------------------------------------------------------------------------


def generate_sentiment_report(
    ticker: str,
    headlines: list[dict],
    price_data: pd.DataFrame,
    sentiment_history: list[float],
    price_returns: list[float] | None = None,
) -> SentimentReport:
    """Generate a comprehensive sentiment report for *ticker*.

    Parameters
    ----------
    ticker:
        Stock ticker code.
    headlines:
        ``[{"headline": str, "published_at": str, "sentiment": float}, ...]``
    price_data:
        DataFrame with DatetimeIndex and ``"close"`` column.
    sentiment_history:
        Daily sentiment scores (most recent last).
    price_returns:
        Daily price returns.  If *None*, computed from *price_data*.

    Returns
    -------
    SentimentReport
    """
    # News impact
    news_impacts = compute_news_impact(headlines, price_data)

    # Sentiment strength from headline sentiments
    headline_sentiments = [float(h.get("sentiment", 0.0)) for h in headlines]
    current_sentiment = compute_sentiment_strength(headline_sentiments, ticker=ticker)

    # Price returns fallback
    if price_returns is None and len(price_data) > 1:
        closes = price_data["close"].values.astype(np.float64)
        price_returns_arr = list(np.diff(closes) / closes[:-1])
        price_returns_arr = [float(r) for r in price_returns_arr]
    elif price_returns is not None:
        price_returns_arr = price_returns
    else:
        price_returns_arr = []

    # Contrarian signal
    contrarian = detect_contrarian_signal(
        sentiment_history, price_returns_arr, ticker=ticker,
    )

    # Regime
    regime = classify_sentiment_regime(sentiment_history)

    # Composite signal: blend current sentiment + contrarian + regime
    composite = current_sentiment.weighted_score

    if contrarian is not None:
        # Contrarian adjusts composite toward reversal
        contrarian_adj = -contrarian.signal_strength * 0.3
        if contrarian.sentiment_direction == "bullish":
            composite += contrarian_adj  # temper bullishness
        else:
            composite -= contrarian_adj  # temper bearishness

    # Regime adjustment
    if regime.regime in ("euphoria", "panic"):
        # Extreme regimes dampen composite (mean reversion likely)
        composite *= (1.0 - regime.mean_reversion_prob * 0.5)

    composite = round(max(-1.0, min(1.0, composite)), 4)

    return SentimentReport(
        ticker=ticker,
        current_sentiment=current_sentiment,
        news_impacts=news_impacts,
        contrarian=contrarian,
        regime=regime,
        composite_signal=composite,
    )


# ---------------------------------------------------------------------------
# 7. format_sentiment_report  (Telegram plain text + emoji)
# ---------------------------------------------------------------------------


def format_sentiment_report(report: SentimentReport) -> str:
    """Format *report* for a Telegram message (plain text + emoji)."""
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    cs = report.current_sentiment
    regime = report.regime

    # Signal emoji
    if report.composite_signal > 0.3:
        sig_emoji = "\U0001f7e2"  # green circle
    elif report.composite_signal < -0.3:
        sig_emoji = "\U0001f534"  # red circle
    else:
        sig_emoji = "\U0001f7e1"  # yellow circle

    lines: list[str] = [
        f"\U0001f4ca {report.ticker} 센티먼트 분석",
        "\u2500" * 25,
        "",
        f"{sig_emoji} 종합 신호: {report.composite_signal:+.2f}",
        "",
        "\U0001f4c8 센티먼트 강도",
        f"  점수: {cs.raw_score:+.2f}  강도: {cs.magnitude:.2f}",
        f"  신뢰도: {cs.confidence:.2f}  일치율: {cs.agreement_ratio:.0%}",
    ]

    if cs.contrarian_signal:
        lines.append("  \u26a0\ufe0f 극단 센티먼트 (역발상 주의)")

    # Regime
    regime_emoji_map = {
        "euphoria": "\U0001f525",
        "optimism": "\U0001f7e2",
        "neutral": "\u26aa",
        "fear": "\U0001f7e1",
        "panic": "\U0001f6a8",
    }
    r_emoji = regime_emoji_map.get(regime.regime, "\u26aa")
    lines.extend([
        "",
        f"\U0001f30d 시장 레짐: {r_emoji} {regime.regime.upper()}",
        f"  지속: {regime.duration_days}일  복귀확률: {regime.mean_reversion_prob:.0%}",
        f"  레짐후 평균수익: {regime.historical_return_after:+.1%}",
    ])

    # Contrarian
    if report.contrarian:
        c = report.contrarian
        lines.extend([
            "",
            "\U0001f500 역발상 신호",
            f"  센티: {c.sentiment_direction} vs 가격: {c.price_direction}",
            f"  괴리: {c.divergence_days}일  강도: {c.signal_strength:.2f}",
            f"  과거 적중률: {c.historical_accuracy:.0%}",
        ])

    # Top impacts
    if report.news_impacts:
        top = sorted(report.news_impacts, key=lambda ni: ni.impact_score, reverse=True)[:3]
        lines.extend(["", "\U0001f4f0 주요 뉴스 영향"])
        for ni in top:
            direction = "\u2191" if ni.price_reaction_pct > 0 else "\u2193"
            lines.append(
                f"  {direction} {ni.headline[:30]}.. "
                f"({ni.price_reaction_pct:+.2f}%)"
            )

    lines.extend([
        "",
        "\u2500" * 25,
        f"\U0001f551 {now_str}",
        "\U0001f916 K-Quant Sentiment Advanced",
    ])

    return "\n".join(lines)
