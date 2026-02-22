"""Live performance tracking for recommendations and portfolio (core/performance_tracker.py).

Tracks D+1, D+3, D+5, D+10, D+20 returns for each recommendation.
Computes strategy-level and overall hit rates and alpha.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

TRACK_DAYS = [1, 3, 5, 10, 20]

KST = timezone(timedelta(hours=9))

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
class RecommendationTrack:
    """Single recommendation tracking record with D+N return slots."""

    ticker: str
    name: str
    strategy: str
    score: float
    recommended_date: str
    entry_price: float
    returns: dict[int, float] = field(default_factory=dict)  # {1: 2.3, 3: 4.1, ...}
    hit: bool = False  # True if max return > 0


@dataclass
class StrategyPerformance:
    """Aggregated performance for one strategy type."""

    strategy: str
    total_recs: int = 0
    hits: int = 0
    hit_rate_pct: float = 0.0
    avg_return_pct: float = 0.0
    best_return_pct: float = 0.0
    worst_return_pct: float = 0.0


@dataclass
class PerformanceSummary:
    """Overall performance summary across all strategies."""

    start_date: str = ""
    days_active: int = 0
    total_recs: int = 0
    overall_hit_rate_pct: float = 0.0
    overall_avg_return_pct: float = 0.0
    alpha_vs_kospi: float = 0.0
    alpha_vs_kosdaq: float = 0.0
    strategy_breakdown: list[StrategyPerformance] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Track recommendation
# ---------------------------------------------------------------------------

def track_recommendation(
    ticker: str,
    name: str,
    strategy: str,
    score: float,
    entry_price: float,
    date_str: str | None = None,
) -> RecommendationTrack:
    """Create a new tracking record for a recommendation.

    Args:
        ticker: Stock ticker code (e.g. "005930").
        name: Stock name (e.g. "삼성전자").
        strategy: Strategy type key (e.g. "A", "B", ..., "G").
        score: Composite score at recommendation time.
        entry_price: Price at which the recommendation was made.
        date_str: Optional date string (YYYY-MM-DD). Defaults to today KST.

    Returns:
        A new RecommendationTrack with empty returns dict.
    """
    try:
        if date_str is None:
            date_str = datetime.now(KST).strftime("%Y-%m-%d")

        if entry_price <= 0:
            logger.warning(
                "%s(%s) 진입가 %.2f 가 유효하지 않습니다",
                name, ticker, entry_price,
            )

        track = RecommendationTrack(
            ticker=ticker,
            name=name,
            strategy=strategy,
            score=round(score, 2),
            recommended_date=date_str,
            entry_price=entry_price,
            returns={},
            hit=False,
        )
        logger.info(
            "추천 트래킹 생성: %s(%s) 전략=%s 진입가=%.0f 날짜=%s",
            name, ticker, strategy, entry_price, date_str,
        )
        return track

    except Exception as exc:
        logger.error("track_recommendation 실패: %s", exc)
        return RecommendationTrack(
            ticker=ticker or "",
            name=name or "",
            strategy=strategy or "A",
            score=0.0,
            recommended_date=date_str or "",
            entry_price=entry_price or 0.0,
        )


# ---------------------------------------------------------------------------
# 2. Update track returns
# ---------------------------------------------------------------------------

def update_track_returns(
    track: RecommendationTrack,
    current_prices: dict[int, float],
) -> RecommendationTrack:
    """Update D+N returns for the track using observed prices.

    Args:
        track: The existing recommendation track to update.
        current_prices: Mapping of day offset to observed price at that offset.
            e.g. {1: 51000, 3: 52500, 5: 50000}

    Returns:
        The same RecommendationTrack with returns dict populated and
        hit flag recalculated.
    """
    try:
        if track.entry_price <= 0:
            logger.warning(
                "%s(%s) 진입가가 0 이하라 수익률 계산 불가",
                track.name, track.ticker,
            )
            return track

        for day_n, price_at_d_n in current_prices.items():
            if day_n not in TRACK_DAYS:
                logger.debug("day_n=%d 는 추적 대상이 아닙니다 (무시)", day_n)
                continue

            if price_at_d_n <= 0:
                logger.warning(
                    "%s D+%d 가격이 유효하지 않습니다: %.2f",
                    track.ticker, day_n, price_at_d_n,
                )
                continue

            ret_pct = round(
                (price_at_d_n - track.entry_price) / track.entry_price * 100,
                2,
            )
            track.returns[day_n] = ret_pct

        # Recalculate hit flag: True if any tracked return is positive
        if track.returns:
            track.hit = max(track.returns.values()) > 0
        else:
            track.hit = False

        logger.debug(
            "%s(%s) 수익률 업데이트: %s hit=%s",
            track.name, track.ticker, track.returns, track.hit,
        )
        return track

    except Exception as exc:
        logger.error("update_track_returns 실패 (%s): %s", track.ticker, exc)
        return track


# ---------------------------------------------------------------------------
# 3. Compute strategy performance
# ---------------------------------------------------------------------------

def compute_strategy_performance(
    tracks: list[RecommendationTrack],
) -> list[StrategyPerformance]:
    """Group tracks by strategy and compute per-strategy statistics.

    Only tracks that have at least one D+N return recorded are counted
    towards hit rate and return calculations.

    Args:
        tracks: List of recommendation tracks to aggregate.

    Returns:
        List of StrategyPerformance objects, one per strategy encountered,
        sorted by strategy key.
    """
    try:
        buckets: dict[str, list[RecommendationTrack]] = defaultdict(list)
        for t in tracks:
            buckets[t.strategy].append(t)

        results: list[StrategyPerformance] = []

        for strategy in sorted(buckets.keys()):
            strat_tracks = buckets[strategy]

            # Only evaluate tracks that have at least one return recorded
            evaluated = [t for t in strat_tracks if t.returns]
            total_recs = len(strat_tracks)

            if not evaluated:
                results.append(StrategyPerformance(
                    strategy=strategy,
                    total_recs=total_recs,
                    hits=0,
                    hit_rate_pct=0.0,
                    avg_return_pct=0.0,
                    best_return_pct=0.0,
                    worst_return_pct=0.0,
                ))
                continue

            hits = sum(1 for t in evaluated if t.hit)
            hit_rate = round(hits / len(evaluated) * 100, 1)

            # Use the best available return (longest horizon) for each track
            best_returns: list[float] = []
            for t in evaluated:
                max_day = max(t.returns.keys())
                best_returns.append(t.returns[max_day])

            avg_ret = round(sum(best_returns) / len(best_returns), 2)
            best_ret = round(max(best_returns), 2)
            worst_ret = round(min(best_returns), 2)

            results.append(StrategyPerformance(
                strategy=strategy,
                total_recs=total_recs,
                hits=hits,
                hit_rate_pct=hit_rate,
                avg_return_pct=avg_ret,
                best_return_pct=best_ret,
                worst_return_pct=worst_ret,
            ))

        logger.info(
            "전략 성과 계산 완료: %d개 전략, 총 %d건 추천",
            len(results), len(tracks),
        )
        return results

    except Exception as exc:
        logger.error("compute_strategy_performance 실패: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 4. Compute performance summary
# ---------------------------------------------------------------------------

def compute_performance_summary(
    tracks: list[RecommendationTrack],
    start_date: str,
    kospi_return: float = 0.0,
    kosdaq_return: float = 0.0,
) -> PerformanceSummary:
    """Build an overall performance summary.

    Args:
        tracks: All recommendation tracks collected so far.
        start_date: YYYY-MM-DD when tracking began.
        kospi_return: KOSPI index return (%) over the same period.
        kosdaq_return: KOSDAQ index return (%) over the same period.

    Returns:
        PerformanceSummary with overall stats, alpha, and strategy breakdown.
    """
    try:
        # Days active
        days_active = 0
        if start_date:
            try:
                start_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
                delta = datetime.now(KST).replace(tzinfo=None) - start_dt
                days_active = max(delta.days, 0)
            except (ValueError, TypeError):
                days_active = 0

        strategy_breakdown = compute_strategy_performance(tracks)

        # Overall stats from evaluated tracks
        evaluated = [t for t in tracks if t.returns]
        total_recs = len(tracks)

        if evaluated:
            overall_hits = sum(1 for t in evaluated if t.hit)
            overall_hit_rate = round(overall_hits / len(evaluated) * 100, 1)

            all_returns: list[float] = []
            for t in evaluated:
                max_day = max(t.returns.keys())
                all_returns.append(t.returns[max_day])

            overall_avg_return = round(sum(all_returns) / len(all_returns), 2)
        else:
            overall_hit_rate = 0.0
            overall_avg_return = 0.0

        # Alpha = our avg return - benchmark return
        alpha_kospi = round(overall_avg_return - kospi_return, 2)
        alpha_kosdaq = round(overall_avg_return - kosdaq_return, 2)

        summary = PerformanceSummary(
            start_date=start_date,
            days_active=days_active,
            total_recs=total_recs,
            overall_hit_rate_pct=overall_hit_rate,
            overall_avg_return_pct=overall_avg_return,
            alpha_vs_kospi=alpha_kospi,
            alpha_vs_kosdaq=alpha_kosdaq,
            strategy_breakdown=strategy_breakdown,
        )

        logger.info(
            "성과 요약 생성: %d일 활동, %d건 추천, 적중률 %.1f%%, 알파(KOSPI) %+.2f%%",
            days_active, total_recs, overall_hit_rate, alpha_kospi,
        )
        return summary

    except Exception as exc:
        logger.error("compute_performance_summary 실패: %s", exc)
        return PerformanceSummary(
            start_date=start_date,
            strategy_breakdown=[],
        )


# ---------------------------------------------------------------------------
# 5. Create portfolio snapshot
# ---------------------------------------------------------------------------

def create_portfolio_snapshot(
    holdings: list[dict],
    total_value: float,
    cash: float,
    kospi_close: float = 0.0,
    kosdaq_close: float = 0.0,
) -> dict:
    """Build a daily portfolio snapshot dict suitable for DB storage.

    Args:
        holdings: List of holding dicts (each should have ticker, name,
            quantity, current_price, avg_price, profit_pct).
        total_value: Total evaluated portfolio value in KRW.
        cash: Available cash balance in KRW.
        kospi_close: KOSPI closing price on the snapshot date.
        kosdaq_close: KOSDAQ closing price on the snapshot date.

    Returns:
        A flat dict ready for DB insertion with computed fields like
        invested_value, total_profit_pct, stock_count, etc.
    """
    try:
        now = datetime.now(KST)
        snapshot_date = now.strftime("%Y-%m-%d")
        snapshot_ts = now.strftime("%Y-%m-%d %H:%M:%S")

        # Compute invested value from holdings
        invested_value = 0.0
        holding_records: list[dict] = []
        for h in holdings:
            qty = h.get("quantity", 0) or 0
            avg_price = h.get("avg_price", 0) or 0
            current_price = h.get("current_price", 0) or 0
            profit_pct = h.get("profit_pct", 0) or 0

            cost_basis = qty * avg_price
            invested_value += cost_basis

            holding_records.append({
                "ticker": h.get("ticker", ""),
                "name": h.get("name", ""),
                "quantity": qty,
                "avg_price": avg_price,
                "current_price": current_price,
                "profit_pct": round(profit_pct, 2),
                "market_value": round(qty * current_price, 0),
            })

        # Total profit
        total_profit = total_value - invested_value - cash
        total_profit_pct = (
            round(total_profit / invested_value * 100, 2)
            if invested_value > 0 else 0.0
        )

        # Weight of stocks vs cash
        stock_value = total_value - cash
        stock_weight_pct = (
            round(stock_value / total_value * 100, 1)
            if total_value > 0 else 0.0
        )
        cash_weight_pct = round(100.0 - stock_weight_pct, 1)

        snapshot = {
            "snapshot_date": snapshot_date,
            "snapshot_ts": snapshot_ts,
            "total_value": round(total_value, 0),
            "invested_value": round(invested_value, 0),
            "cash": round(cash, 0),
            "total_profit": round(total_profit, 0),
            "total_profit_pct": total_profit_pct,
            "stock_weight_pct": stock_weight_pct,
            "cash_weight_pct": cash_weight_pct,
            "stock_count": len(holding_records),
            "kospi_close": round(kospi_close, 2),
            "kosdaq_close": round(kosdaq_close, 2),
            "holdings": holding_records,
        }

        logger.info(
            "포트폴리오 스냅샷 생성: 날짜=%s 총평가=%,.0f원 수익률=%+.2f%%",
            snapshot_date, total_value, total_profit_pct,
        )
        return snapshot

    except Exception as exc:
        logger.error("create_portfolio_snapshot 실패: %s", exc)
        return {
            "snapshot_date": datetime.now(KST).strftime("%Y-%m-%d"),
            "snapshot_ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "total_value": total_value,
            "cash": cash,
            "total_profit": 0.0,
            "total_profit_pct": 0.0,
            "stock_count": 0,
            "kospi_close": kospi_close,
            "kosdaq_close": kosdaq_close,
            "holdings": [],
        }


# ---------------------------------------------------------------------------
# 6. Compute benchmark alpha
# ---------------------------------------------------------------------------

def compute_benchmark_alpha(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
) -> float:
    """Compute simple alpha as mean(portfolio) - mean(benchmark).

    Both lists should contain period returns in percent (e.g. daily or
    weekly returns).  They are aligned positionally (index 0 = same period).

    Args:
        portfolio_returns: Portfolio period returns in percent.
        benchmark_returns: Benchmark period returns in percent.

    Returns:
        Alpha in percent.  Positive means outperformance.
    """
    try:
        if not portfolio_returns:
            logger.info("포트폴리오 수익률 데이터 없음, 알파 0.0 반환")
            return 0.0

        port_mean = sum(portfolio_returns) / len(portfolio_returns)

        if benchmark_returns:
            bench_mean = sum(benchmark_returns) / len(benchmark_returns)
        else:
            bench_mean = 0.0
            logger.info("벤치마크 수익률 데이터 없음, 벤치마크 평균 0.0으로 처리")

        alpha = round(port_mean - bench_mean, 4)

        logger.debug(
            "알파 계산: 포트폴리오 평균 %.4f%% - 벤치마크 평균 %.4f%% = %.4f%%",
            port_mean, bench_mean, alpha,
        )
        return alpha

    except Exception as exc:
        logger.error("compute_benchmark_alpha 실패: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 7. Format performance report (Telegram)
# ---------------------------------------------------------------------------

def format_performance_report(summary: PerformanceSummary) -> str:
    """Format the performance summary for Telegram.

    Korean language, no ** bold, includes strategy breakdown,
    alpha vs KOSPI/KOSDAQ, and actionable notes.

    Args:
        summary: A PerformanceSummary object.

    Returns:
        Multi-line string ready for Telegram.
    """
    try:
        lines: list[str] = []

        lines.append("\u2550" * 24)
        lines.append(f"{USER_NAME} 라이브 성과 리포트")
        lines.append("\u2550" * 24)
        lines.append("")

        # Overview
        lines.append(f"추적 시작: {summary.start_date}")
        lines.append(f"활동 기간: {summary.days_active}일")
        lines.append(f"총 추천 수: {summary.total_recs}건")
        lines.append("")

        # Hit rate
        if summary.total_recs > 0:
            lines.append(
                f"전체 적중률: {summary.overall_hit_rate_pct:.1f}% "
                f"(양수 수익 달성 기준)"
            )
            lines.append(f"평균 수익률: {summary.overall_avg_return_pct:+.2f}%")
        else:
            lines.append("아직 추천 데이터가 없습니다.")
        lines.append("")

        # Alpha
        lines.append("\u2500" * 25)
        lines.append("벤치마크 대비 알파")
        kospi_emoji = "\u2191" if summary.alpha_vs_kospi >= 0 else "\u2193"
        kosdaq_emoji = "\u2191" if summary.alpha_vs_kosdaq >= 0 else "\u2193"
        lines.append(
            f"  vs KOSPI: {kospi_emoji} {summary.alpha_vs_kospi:+.2f}%p"
        )
        lines.append(
            f"  vs KOSDAQ: {kosdaq_emoji} {summary.alpha_vs_kosdaq:+.2f}%p"
        )
        lines.append("")

        # Strategy breakdown
        if summary.strategy_breakdown:
            lines.append("\u2500" * 25)
            lines.append("전략별 성과 분석")
            lines.append("")

            for sp in summary.strategy_breakdown:
                label = STRATEGY_LABELS.get(sp.strategy, sp.strategy)

                if sp.hit_rate_pct >= 70:
                    grade_icon = "\U0001f7e2"
                elif sp.hit_rate_pct >= 50:
                    grade_icon = "\U0001f7e1"
                else:
                    grade_icon = "\U0001f534"

                lines.append(f"  {grade_icon} {label} ({sp.strategy})")
                lines.append(
                    f"    추천: {sp.total_recs}건 | "
                    f"적중: {sp.hits}건 ({sp.hit_rate_pct:.0f}%)"
                )
                lines.append(
                    f"    평균: {sp.avg_return_pct:+.2f}% | "
                    f"최고: {sp.best_return_pct:+.2f}% | "
                    f"최저: {sp.worst_return_pct:+.2f}%"
                )
                lines.append("")

        # Actionable notes
        lines.append("\u2500" * 25)
        lines.append("시사점")

        notes: list[str] = []
        if summary.overall_hit_rate_pct >= 70:
            notes.append("적중률 우수 구간 -> 현행 전략 유지")
        elif summary.overall_hit_rate_pct >= 50:
            notes.append("적중률 양호 -> 미스 패턴 분석 후 미세 조정")
        elif summary.total_recs > 0:
            notes.append("적중률 부진 -> 진입 조건 보수적 조정 필요")

        if summary.alpha_vs_kospi > 2.0:
            notes.append("KOSPI 대비 알파 양호 -> 현 전략이 시장을 이기고 있습니다")
        elif summary.alpha_vs_kospi < -2.0:
            notes.append("KOSPI 대비 언더퍼폼 -> 시장 대비 부진, 포지션 재검토")

        # Identify weak strategies
        for sp in summary.strategy_breakdown:
            if sp.total_recs >= 3 and sp.hit_rate_pct < 40:
                label = STRATEGY_LABELS.get(sp.strategy, sp.strategy)
                notes.append(f"{label} 전략 적중률 저조 -> 비중 축소 검토")

        if not notes:
            notes.append("현재 특이 패턴 없음 -> 현행 유지")

        for i, note in enumerate(notes, 1):
            lines.append(f"  {i}. {note}")
        lines.append("")

        lines.append("\u2500" * 25)
        lines.append("K-Quant 라이브 성과 추적 시스템")

        return "\n".join(lines)

    except Exception as exc:
        logger.error("format_performance_report 실패: %s", exc)
        return f"{USER_NAME}, 성과 리포트 생성 중 오류가 발생했습니다."


# ---------------------------------------------------------------------------
# 8. Format live scorecard
# ---------------------------------------------------------------------------

def format_live_scorecard(tracks: list[RecommendationTrack]) -> str:
    """Format a live scorecard of recent tracks with D+1 ... D+20 returns.

    Shows the most recent tracks (up to 15) with a compact table-like
    layout suitable for Telegram.

    Args:
        tracks: List of recommendation tracks to display.

    Returns:
        Multi-line string ready for Telegram.
    """
    try:
        lines: list[str] = []

        lines.append("\u2550" * 24)
        lines.append(f"{USER_NAME} 추천 라이브 스코어카드")
        lines.append("\u2550" * 24)
        lines.append("")

        if not tracks:
            lines.append("아직 추적 중인 추천이 없습니다.")
            lines.append("")
            lines.append("K-Quant 라이브 성과 추적 시스템")
            return "\n".join(lines)

        # Sort by recommended_date descending, show latest first
        sorted_tracks = sorted(
            tracks,
            key=lambda t: t.recommended_date,
            reverse=True,
        )[:15]

        # Header row for D+N columns
        header_parts = ["D+" + str(d) for d in TRACK_DAYS]
        header_line = "  " + " | ".join(f"{h:>5s}" for h in header_parts)
        lines.append("종목 / 전략 / 진입가")
        lines.append(header_line)
        lines.append("\u2500" * 42)

        total_evaluated = 0
        total_hits = 0

        for t in sorted_tracks:
            label = STRATEGY_LABELS.get(t.strategy, t.strategy)
            lines.append(
                f"{t.name} ({t.ticker}) [{label}]"
            )
            lines.append(
                f"  진입: {t.entry_price:,.0f}원 | "
                f"날짜: {t.recommended_date} | "
                f"점수: {t.score:.0f}"
            )

            # Return values row
            ret_parts: list[str] = []
            for d in TRACK_DAYS:
                if d in t.returns:
                    val = t.returns[d]
                    if val > 0:
                        ret_parts.append(f"+{val:.1f}%")
                    else:
                        ret_parts.append(f"{val:.1f}%")
                else:
                    ret_parts.append("  -  ")

            ret_line = "  " + " | ".join(f"{p:>5s}" for p in ret_parts)
            lines.append(ret_line)

            # Hit/miss indicator
            if t.returns:
                total_evaluated += 1
                if t.hit:
                    total_hits += 1
                    lines.append("  -> 양수 수익 달성")
                else:
                    lines.append("  -> 아직 양수 미달성")
            else:
                lines.append("  -> 수익률 미수신")

            lines.append("")

        # Summary footer
        lines.append("\u2500" * 42)
        lines.append(
            f"표시: {len(sorted_tracks)}건 / "
            f"전체: {len(tracks)}건"
        )
        if total_evaluated > 0:
            sc_hit_rate = round(total_hits / total_evaluated * 100, 1)
            lines.append(
                f"평가 완료: {total_evaluated}건 | "
                f"적중: {total_hits}건 ({sc_hit_rate:.0f}%)"
            )
        else:
            lines.append("아직 평가 완료된 추천이 없습니다.")
        lines.append("")
        lines.append("K-Quant 라이브 성과 추적 시스템")

        return "\n".join(lines)

    except Exception as exc:
        logger.error("format_live_scorecard 실패: %s", exc)
        return f"{USER_NAME}, 스코어카드 생성 중 오류가 발생했습니다."
