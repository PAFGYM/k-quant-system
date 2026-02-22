"""Feedback loop: track and learn from recommendations and diagnoses.

Evaluates past recommendations, computes hit rates by strategy and regime,
checks diagnosis accuracy, and generates weekly feedback reports for
continuous improvement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

STRATEGY_LABELS = {
    "A": "단기반등",
    "B": "ETF레버리지",
    "C": "장기우량주",
    "D": "섹터로테이션",
    "E": "글로벌분산",
    "F": "모멘텀",
    "G": "돌파",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RecommendationResult:
    """Evaluation result for a single recommendation."""

    ticker: str
    name: str
    rec_date: str
    rec_price: float
    strategy_type: str
    day5_return: float | None = None
    day10_return: float | None = None
    day20_return: float | None = None
    correct: bool | None = None


@dataclass
class FeedbackReport:
    """Aggregated feedback report for a period."""

    period: str
    total_recs: int
    hits: int
    misses: int
    pending: int
    hit_rate: float
    avg_return: float
    lessons: list[str] = field(default_factory=list)
    strategy_breakdown: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Recommendation evaluation
# ---------------------------------------------------------------------------

def evaluate_recommendation(
    rec: dict,
    current_price: float,
    days_since: int,
) -> RecommendationResult:
    """Evaluate a single recommendation against the current price.

    Args:
        rec: Recommendation dict from the DB (must have rec_price, ticker,
             name, rec_date, strategy_type).
        current_price: The stock's price at the evaluation moment.
        days_since: Trading days elapsed since the recommendation date.

    Returns:
        RecommendationResult with return fields populated based on
        *days_since* and correctness determined by whether the return
        at the evaluation point is positive.
    """
    rec_price = rec.get("rec_price", 0)
    if rec_price <= 0:
        logger.warning("rec_price <= 0 for %s, skipping evaluation", rec.get("ticker"))
        return RecommendationResult(
            ticker=rec.get("ticker", ""),
            name=rec.get("name", ""),
            rec_date=rec.get("rec_date", ""),
            rec_price=rec_price,
            strategy_type=rec.get("strategy_type", "A"),
        )

    pct_return = round((current_price - rec_price) / rec_price * 100, 2)

    day5 = pct_return if days_since >= 5 else None
    day10 = pct_return if days_since >= 10 else None
    day20 = pct_return if days_since >= 20 else None

    # Determine correctness at the current evaluation point
    correct: bool | None = None
    if days_since >= 5:
        correct = pct_return > 0

    return RecommendationResult(
        ticker=rec.get("ticker", ""),
        name=rec.get("name", ""),
        rec_date=rec.get("rec_date", ""),
        rec_price=rec_price,
        strategy_type=rec.get("strategy_type", "A"),
        day5_return=day5,
        day10_return=day10,
        day20_return=day20,
        correct=correct,
    )


# ---------------------------------------------------------------------------
# Strategy hit rates
# ---------------------------------------------------------------------------

def compute_strategy_hit_rates(recommendations: list[dict]) -> dict:
    """Group recommendations by strategy_type and compute hit rates.

    A recommendation counts as a *hit* when ``status == 'profit'`` or
    ``pnl_pct > 0`` for completed positions.  Only completed (non-active)
    recommendations are considered for the rate calculation.

    Returns a dict keyed by strategy_type (A-G) with sub-keys:
        total, hits, misses, hit_rate, avg_return, warning (str | None).
    Strategies with hit_rate < 60% receive a warning message.
    """
    buckets: dict[str, list[dict]] = {}
    for rec in recommendations:
        strat = rec.get("strategy_type", "A")
        buckets.setdefault(strat, []).append(rec)

    result: dict[str, dict] = {}
    for strat in sorted(buckets):
        recs = buckets[strat]
        completed = [
            r for r in recs
            if r.get("status") in ("profit", "stop")
        ]
        if not completed:
            result[strat] = {
                "total": len(recs),
                "hits": 0,
                "misses": 0,
                "hit_rate": 0.0,
                "avg_return": 0.0,
                "warning": None,
            }
            continue

        hits = sum(
            1 for r in completed
            if r.get("status") == "profit" or (r.get("pnl_pct") or 0) > 0
        )
        misses = len(completed) - hits
        pnls = [r.get("pnl_pct", 0) or 0 for r in completed]
        avg_ret = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
        hit_rate = round(hits / len(completed) * 100, 1) if completed else 0.0

        warning: str | None = None
        label = STRATEGY_LABELS.get(strat, strat)
        if hit_rate < 60:
            warning = (
                f"{label} 전략 적중률 {hit_rate:.0f}%로 낮음 "
                f"-> 비중 축소 또는 진입 조건 강화 필요"
            )

        result[strat] = {
            "total": len(recs),
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "avg_return": avg_ret,
            "warning": warning,
        }

    return result


# ---------------------------------------------------------------------------
# Regime hit rates
# ---------------------------------------------------------------------------

def compute_regime_hit_rates(recommendations: list[dict]) -> dict:
    """Group recommendations by market regime at time of recommendation.

    Each recommendation dict may contain a ``regime`` key (e.g.
    "attack", "balanced", "defense", "bubble_attack").  If missing,
    the recommendation is classified under "unknown".

    Returns a dict keyed by regime with sub-keys:
        total, hits, misses, hit_rate, avg_return, assessment.
    ``assessment`` is a short Korean note about performance in that regime.
    """
    buckets: dict[str, list[dict]] = {}
    for rec in recommendations:
        regime = rec.get("regime", "unknown")
        buckets.setdefault(regime, []).append(rec)

    result: dict[str, dict] = {}
    best_regime: str | None = None
    worst_regime: str | None = None
    best_rate = -1.0
    worst_rate = 101.0

    for regime, recs in sorted(buckets.items()):
        completed = [
            r for r in recs
            if r.get("status") in ("profit", "stop")
        ]
        if not completed:
            result[regime] = {
                "total": len(recs),
                "hits": 0,
                "misses": 0,
                "hit_rate": 0.0,
                "avg_return": 0.0,
                "assessment": "데이터 부족",
            }
            continue

        hits = sum(
            1 for r in completed
            if r.get("status") == "profit" or (r.get("pnl_pct") or 0) > 0
        )
        misses = len(completed) - hits
        pnls = [r.get("pnl_pct", 0) or 0 for r in completed]
        avg_ret = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
        hit_rate = round(hits / len(completed) * 100, 1)

        if hit_rate > best_rate:
            best_rate = hit_rate
            best_regime = regime
        if hit_rate < worst_rate:
            worst_rate = hit_rate
            worst_regime = regime

        result[regime] = {
            "total": len(recs),
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "avg_return": avg_ret,
            "assessment": "",
        }

    # Fill assessments
    regime_kr = {
        "attack": "공격 모드",
        "balanced": "균형 모드",
        "defense": "방어 모드",
        "bubble_attack": "버블 공격 모드",
        "unknown": "미분류",
    }
    for regime, data in result.items():
        label = regime_kr.get(regime, regime)
        if regime == best_regime and best_rate > 0:
            data["assessment"] = f"{label}에서 가장 잘 맞음 (적중률 {best_rate:.0f}%)"
        elif regime == worst_regime and worst_rate <= 100:
            data["assessment"] = f"{label}에서 정확도 낮음 (적중률 {worst_rate:.0f}%) -> 보수적 접근 필요"
        elif data["assessment"] == "":
            data["assessment"] = f"{label} 적중률 {data['hit_rate']:.0f}%"

    return result


# ---------------------------------------------------------------------------
# Diagnosis accuracy
# ---------------------------------------------------------------------------

def evaluate_diagnosis_accuracy(
    prev_diagnoses: list[dict],
    current_holdings: list[dict],
) -> dict:
    """Evaluate whether previous diagnosis advice was correct.

    Checks two main advice types:
    - "버티세요" (hold / wait for rebound): correct if price went up since.
    - "손절하세요" (stop-loss): correct if price dropped further since.

    Args:
        prev_diagnoses: List of past diagnosis dicts.  Expected keys:
            ticker, action, diagnosis_price (price at diagnosis time).
        current_holdings: List of current holding dicts.  Expected keys:
            ticker, current_price.

    Returns:
        Dict with keys: total, correct, incorrect, accuracy, details (list).
    """
    price_map: dict[str, float] = {}
    for h in current_holdings:
        ticker = h.get("ticker", "")
        price = h.get("current_price", 0) or 0
        if ticker and price > 0:
            price_map[ticker] = price

    total = 0
    correct_count = 0
    incorrect_count = 0
    details: list[dict] = []

    for diag in prev_diagnoses:
        ticker = diag.get("ticker", "")
        action = diag.get("action", "")
        diag_price = diag.get("diagnosis_price", 0) or diag.get("current_price", 0) or 0
        message = diag.get("message", "") or diag.get("diagnosis_msg", "")

        if not ticker or diag_price <= 0:
            continue

        current = price_map.get(ticker)
        if current is None:
            continue

        is_hold_advice = action in ("hold", "add") or "버티세요" in message
        is_stop_advice = action == "stop_loss" or "손절하세요" in message or "손절" in message

        if not is_hold_advice and not is_stop_advice:
            continue

        total += 1
        price_change_pct = round((current - diag_price) / diag_price * 100, 2)

        if is_hold_advice:
            was_correct = price_change_pct > 0
        else:  # is_stop_advice
            was_correct = price_change_pct < 0

        if was_correct:
            correct_count += 1
        else:
            incorrect_count += 1

        details.append({
            "ticker": ticker,
            "advice": "버티세요" if is_hold_advice else "손절하세요",
            "diagnosis_price": diag_price,
            "current_price": current,
            "change_pct": price_change_pct,
            "correct": was_correct,
        })

    accuracy = round(correct_count / total * 100, 1) if total > 0 else 0.0

    return {
        "total": total,
        "correct": correct_count,
        "incorrect": incorrect_count,
        "accuracy": accuracy,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Weekly feedback generation
# ---------------------------------------------------------------------------

def _compute_days_since(rec_date_str: str) -> int:
    """Compute the number of calendar days since rec_date_str (YYYY-MM-DD)."""
    try:
        rec_dt = datetime.strptime(rec_date_str[:10], "%Y-%m-%d")
        delta = datetime.utcnow() - rec_dt
        return max(delta.days, 0)
    except (ValueError, TypeError):
        return 0


def _generate_lessons(
    strategy_breakdown: dict,
    hits: int,
    misses: int,
    completed_recs: list[dict],
) -> list[str]:
    """Generate actionable Korean-language lessons from patterns."""
    lessons: list[str] = []

    # Identify weak strategies
    for strat, data in strategy_breakdown.items():
        warning = data.get("warning")
        if warning:
            lessons.append(warning)

    # Overall hit-rate lesson
    total_evaluated = hits + misses
    if total_evaluated > 0:
        rate = hits / total_evaluated * 100
        if rate >= 80:
            lessons.append("전체 적중률 우수 -> 현행 전략 유지")
        elif rate >= 60:
            lessons.append("적중률 양호 -> 미스 패턴 분석 후 미세 조정")
        else:
            lessons.append("적중률 부진 -> 진입 조건 보수적 조정 필요")

    # Check for foreign-selling misses
    miss_recs = [r for r in completed_recs if r.get("status") == "stop"]
    foreign_sell_misses = [
        r for r in miss_recs
        if r.get("strategy_type") in ("A", "F")
    ]
    if len(foreign_sell_misses) >= 2:
        lessons.append(
            "모멘텀/반등 전략 미스 원인: 외인 급매도 지속 가능성 "
            "-> 외인 수급 전환 확인 후 진입"
        )

    # Check for ETF strategy performance
    etf_data = strategy_breakdown.get("B", {})
    if etf_data.get("misses", 0) >= 2:
        lessons.append(
            "ETF 레버리지 전략 손실 반복 "
            "-> 보유 기간 1~2일 엄수, VIX 20 이상 시 진입 보류"
        )

    # Check for long-term underperformance
    lt_data = strategy_breakdown.get("C", {})
    if lt_data.get("avg_return", 0) < -3:
        lessons.append(
            "장기 우량주 전략 손실 확대 "
            "-> 배당주/가치주 비중 유지하되 추가 매수 시점 재검토"
        )

    if not lessons:
        lessons.append("이번 주기에 특이 패턴 없음 -> 현행 유지")

    return lessons


def generate_weekly_feedback(
    db: Any,
    period_days: int = 7,
) -> FeedbackReport:
    """Generate a feedback report from the last *period_days*.

    Uses the following db methods:
    - ``db.get_active_recommendations()``
    - ``db.get_completed_recommendations()``
    - ``db.get_recommendations_by_strategy(strategy_type)``

    Args:
        db: SQLiteStore instance (typed as Any to avoid circular imports).
        period_days: Look-back window in calendar days (default 7).

    Returns:
        A FeedbackReport summarising hits, misses, lessons and breakdowns.
    """
    cutoff = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d")

    # Gather recommendations
    active_recs = db.get_active_recommendations()
    completed_recs = db.get_completed_recommendations(limit=100)

    # Filter to the requested period
    def _in_period(rec: dict) -> bool:
        rec_date = rec.get("rec_date", "") or rec.get("created_at", "")
        return rec_date[:10] >= cutoff

    period_active = [r for r in active_recs if _in_period(r)]
    period_completed = [r for r in completed_recs if _in_period(r)]
    all_period = period_active + period_completed

    hits = sum(
        1 for r in period_completed
        if r.get("status") == "profit" or (r.get("pnl_pct") or 0) > 0
    )
    misses = sum(
        1 for r in period_completed
        if r.get("status") == "stop" or (
            r.get("status") not in ("active", "watch")
            and (r.get("pnl_pct") or 0) <= 0
        )
    )
    pending = len(period_active)
    total_recs = len(all_period)

    evaluated = hits + misses
    hit_rate = round(hits / evaluated * 100, 1) if evaluated > 0 else 0.0

    pnls = [
        r.get("pnl_pct", 0) or 0
        for r in period_completed
        if r.get("pnl_pct") is not None
    ]
    avg_return = round(sum(pnls) / len(pnls), 2) if pnls else 0.0

    # Strategy breakdown (use all recs for richer context)
    all_for_breakdown: list[dict] = []
    for strat in ("A", "B", "C", "D", "E", "F", "G"):
        try:
            strat_recs = db.get_recommendations_by_strategy(strat)
            all_for_breakdown.extend(strat_recs)
        except Exception:
            pass
    # Include completed recs too (they might not appear in by_strategy query)
    seen_ids = {r.get("id") for r in all_for_breakdown if r.get("id")}
    for r in completed_recs:
        if r.get("id") not in seen_ids:
            all_for_breakdown.append(r)

    strategy_breakdown = compute_strategy_hit_rates(all_for_breakdown)

    # Generate lessons
    lessons = _generate_lessons(
        strategy_breakdown, hits, misses, period_completed,
    )

    period_label = f"최근 {period_days}일"

    report = FeedbackReport(
        period=period_label,
        total_recs=total_recs,
        hits=hits,
        misses=misses,
        pending=pending,
        hit_rate=hit_rate,
        avg_return=avg_return,
        lessons=lessons,
        strategy_breakdown=strategy_breakdown,
    )

    logger.info(
        "Feedback report generated: %d recs, %d hits, %d misses (%.1f%%)",
        total_recs, hits, misses, hit_rate,
    )

    return report


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_feedback_report(report: FeedbackReport) -> str:
    """Format the feedback report for Telegram.

    No ** bold.  Uses emojis and line breaks.  주호님 style.
    """
    lines: list[str] = []

    evaluated = report.hits + report.misses
    lines.append("\u2550" * 22)
    lines.append(f"{USER_NAME}의 주간 피드백 리포트")
    lines.append("\u2550" * 22)
    lines.append("")

    # Hit / miss summary
    if evaluated > 0:
        lines.append(
            f"\u2705 적중 {report.hits}건 / \u274c 미스 {report.misses}건 "
            f"(적중률 {report.hit_rate:.0f}%)"
        )
    else:
        lines.append("\u23f3 아직 평가 완료된 추천이 없습니다")

    if report.pending > 0:
        lines.append(f"\u23f3 진행 중 {report.pending}건")

    lines.append(f"\U0001f4ca 평균 수익률: {report.avg_return:+.1f}%")
    lines.append("")

    # Strategy breakdown
    lines.append("\u2500" * 25)
    lines.append("\U0001f3af 전략별 성과")
    for strat in ("A", "B", "C", "D", "E", "F", "G"):
        data = report.strategy_breakdown.get(strat)
        if not data or data.get("total", 0) == 0:
            continue
        label = STRATEGY_LABELS.get(strat, strat)
        hr = data.get("hit_rate", 0)
        avg = data.get("avg_return", 0)
        emoji = "\U0001f7e2" if hr >= 70 else "\U0001f7e1" if hr >= 50 else "\U0001f534"
        total_cnt = data.get("hits", 0) + data.get("misses", 0)
        if total_cnt > 0:
            lines.append(
                f"  {emoji} {label}: 적중률 {hr:.0f}% | 평균 {avg:+.1f}% ({total_cnt}건)"
            )
    lines.append("")

    # Lessons
    if report.lessons:
        lines.append("\u2500" * 25)
        lines.append("\U0001f4a1 교훈")
        for i, lesson in enumerate(report.lessons, 1):
            lines.append(f"  {i}. {lesson}")
        lines.append("")

    # Adjustment suggestions
    adjustments: list[str] = []
    for strat, data in report.strategy_breakdown.items():
        warning = data.get("warning")
        if warning and strat in STRATEGY_LABELS:
            label = STRATEGY_LABELS[strat]
            adjustments.append(f"{label} 비중 축소")
    if report.hit_rate >= 70 and evaluated >= 3:
        adjustments.append("현행 비중 유지, 자신감 있는 구간")
    elif report.hit_rate < 50 and evaluated >= 3:
        adjustments.append("전체 포지션 사이즈 10% 축소 권장")

    if adjustments:
        lines.append("\u2500" * 25)
        lines.append("\U0001f504 다음 주 조정")
        for adj in adjustments:
            lines.append(f"  \u2192 {adj}")
        lines.append("")

    lines.append("\u2500" * 25)
    lines.append(f"\U0001f4c5 기간: {report.period}")
    lines.append("\U0001f916 K-Quant 피드백 시스템")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Similar-condition stats (Phase 7)
# ---------------------------------------------------------------------------

def get_similar_condition_stats(
    recommendations: list[dict],
    strategy: str | None = None,
    score_min: float = 0,
    score_max: float = 300,
    market_condition: str | None = None,
) -> dict | None:
    """유사 조건의 과거 추천 승률을 조회합니다.

    5건 미만이면 None (신뢰도 낮음).
    """
    try:
        filtered = []
        for rec in recommendations:
            if strategy and rec.get("strategy_type") != strategy:
                continue
            score = rec.get("rec_score", 0) or 0
            if score < score_min or score > score_max:
                continue
            if market_condition and rec.get("regime") != market_condition:
                continue
            if rec.get("status") not in ("profit", "stop"):
                continue
            filtered.append(rec)

        if len(filtered) < 5:
            return None

        wins = sum(
            1 for r in filtered
            if r.get("status") == "profit" or (r.get("pnl_pct") or 0) > 0
        )
        pnls = [r.get("pnl_pct", 0) or 0 for r in filtered]
        avg_ret = round(sum(pnls) / len(pnls), 2) if pnls else 0.0

        sample_size = len(filtered)
        confidence = "high" if sample_size >= 20 else "medium" if sample_size >= 10 else "low"

        return {
            "sample_size": sample_size,
            "win_rate": round(wins / sample_size * 100, 1),
            "avg_return": avg_ret,
            "confidence": confidence,
        }
    except Exception as e:
        logger.error("유사 조건 통계 실패: %s", e, exc_info=True)
        return None


def get_feedback_for_ticker(
    recommendations: list[dict],
    ticker: str,
    strategy_stats: dict | None = None,
    limit: int = 5,
) -> str:
    """특정 종목의 과거 추천 이력 + 전략 승률을 AI에 전달할 텍스트로 포맷합니다."""
    try:
        lines: list[str] = []

        # 해당 종목 과거 추천
        past = [r for r in recommendations if r.get("ticker") == ticker]
        past = sorted(past, key=lambda r: r.get("rec_date", ""), reverse=True)[:limit]

        if past:
            lines.append("이 종목 과거 추천 이력:")
            for r in past:
                date_str = (r.get("rec_date") or "")[:10]
                score = r.get("rec_score", 0)
                strat = r.get("strategy_type", "A")
                label = STRATEGY_LABELS.get(strat, strat)
                pnl = r.get("pnl_pct", 0) or 0
                status_kr = "성공" if pnl > 0 else "실패" if pnl < 0 else "진행중"
                lines.append(
                    f"  {date_str}: 스코어 {score:.0f}, {label} -> D+10 수익 {pnl:+.1f}% ({status_kr})"
                )
        else:
            lines.append("이 종목 과거 추천 이력: 없음")

        # 전략별 승률
        if strategy_stats:
            lines.append("")
            lines.append("현재 전략 실전 승률 (최근 90일):")
            for strat_key, stats in strategy_stats.items():
                label = STRATEGY_LABELS.get(strat_key, strat_key)
                total = stats.get("total", 0)
                wins = stats.get("wins", 0)
                hr = stats.get("hit_rate", 0)
                avg = stats.get("avg_return", 0)
                if total > 0:
                    lines.append(
                        f"  {label}: {hr:.0f}% ({wins}/{total}), 평균 수익 {avg:+.1f}%"
                    )

        return "\n".join(lines) if lines else "피드백 데이터 없음"

    except Exception as e:
        logger.error("피드백 텍스트 생성 실패: %s", e, exc_info=True)
        return "피드백 데이터 조회 실패"


def format_feedback_stats(stats: dict) -> str:
    """전략별 승률 통계를 텔레그램 형식으로 포맷합니다."""
    try:
        lines = [
            "\u2550" * 22,
            f"{USER_NAME}의 피드백 현황",
            "\u2550" * 22,
            "",
        ]

        sample = stats.get("sample_size", 0)
        win_rate = stats.get("win_rate", 0)
        avg_ret = stats.get("avg_return", 0)
        conf = stats.get("confidence", "low")

        conf_kr = {"high": "높음", "medium": "보통", "low": "낮음"}.get(conf, conf)

        lines.append(f"유사 조건 승률: {win_rate:.0f}% ({sample}건)")
        lines.append(f"평균 수익률: {avg_ret:+.1f}%")
        lines.append(f"신뢰도: {conf_kr} ({sample}건)")
        lines.append("")
        lines.append("\U0001f916 K-Quant 피드백 시스템")

        return "\n".join(lines)

    except Exception as e:
        logger.error("피드백 통계 포맷 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 피드백 통계 포맷 중 오류가 발생했습니다."
