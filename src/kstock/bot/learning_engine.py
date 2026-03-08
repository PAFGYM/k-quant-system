"""v9.5.3 학습 엔진 — 매니저 성적표 + 매매 패턴 학습 + 이벤트 반영.

주호님 전용 시스템:
- 매니저별 추천 성과를 자동 추적하고 가중치 조절
- 과거 매매에서 패턴을 추출하여 프로필로 저장
- 글로벌 이벤트를 실제 전략 점수에 반영

섹터 집중 투자자를 위한 설계:
- 섹터별 매니저 정확도 추적
- 보유 기간 분석 → 매니저 매칭
- 이벤트 → 섹터 점수 자동 조절
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


# ── 매니저 성적표 계산 ─────────────────────────────────────────


def calculate_manager_scorecard(db, days: int = 30) -> dict[str, dict]:
    """매니저별 추천 성과를 계산하여 DB에 저장.

    recommendations + recommendation_results 테이블을 조인하여
    매니저별 적중률, 평균 수익, 강점/약점을 도출.

    Returns:
        {manager_key: {hit_rate, avg_return_5d, total, ...}}
    """
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    scorecards = {}
    managers = ["scalp", "swing", "position", "long_term"]

    for mgr in managers:
        try:
            with db._connect() as conn:
                # 추천 + 결과 조인
                rows = conn.execute(
                    """
                    SELECT r.ticker, r.name, r.rec_score, r.rec_price,
                           rr.day5_return, rr.day10_return, rr.day20_return,
                           rr.correct
                    FROM recommendations r
                    LEFT JOIN recommendation_results rr
                        ON rr.recommendation_id = r.id
                    WHERE r.manager = ?
                      AND r.created_at >= ?
                    ORDER BY r.created_at DESC
                    """,
                    (mgr, cutoff),
                ).fetchall()

            if not rows:
                scorecards[mgr] = {
                    "total": 0, "evaluated": 0, "hits": 0,
                    "hit_rate": 0.0, "avg_return_5d": 0.0,
                    "weight_adj": 1.0,
                }
                continue

            total = len(rows)
            evaluated = sum(1 for r in rows if r["day5_return"] is not None)
            hits = sum(1 for r in rows if r["correct"])

            # 평균 수익률
            d5_vals = [r["day5_return"] for r in rows if r["day5_return"] is not None]
            d10_vals = [r["day10_return"] for r in rows if r["day10_return"] is not None]
            d20_vals = [r["day20_return"] for r in rows if r["day20_return"] is not None]

            avg_d5 = sum(d5_vals) / len(d5_vals) if d5_vals else 0.0
            avg_d10 = sum(d10_vals) / len(d10_vals) if d10_vals else 0.0
            avg_d20 = sum(d20_vals) / len(d20_vals) if d20_vals else 0.0
            hit_rate = (hits / evaluated * 100) if evaluated > 0 else 0.0

            # 최고/최악 매매
            best = max(rows, key=lambda r: r["day5_return"] or -999)
            worst = min(rows, key=lambda r: r["day5_return"] or 999)
            best_text = f"{best['name']} +{(best['day5_return'] or 0)*100:.1f}%"
            worst_text = f"{worst['name']} {(worst['day5_return'] or 0)*100:.1f}%"

            # 가중치 조절 (hit_rate 기반)
            if hit_rate >= 70:
                weight = 1.2
            elif hit_rate >= 60:
                weight = 1.1
            elif hit_rate >= 50:
                weight = 1.0
            elif hit_rate >= 40:
                weight = 0.9
            else:
                weight = 0.8

            card = {
                "total": total,
                "evaluated": evaluated,
                "hits": hits,
                "hit_rate": hit_rate,
                "avg_return_5d": avg_d5,
                "avg_return_10d": avg_d10,
                "avg_return_20d": avg_d20,
                "best_trade": best_text,
                "worst_trade": worst_text,
                "weight_adj": weight,
            }
            scorecards[mgr] = card

            # DB 저장
            with db._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO manager_scorecard
                    (manager_key, period, total_recs, evaluated_recs, hits,
                     hit_rate, avg_return_5d, avg_return_10d, avg_return_20d,
                     best_trade, worst_trade, weight_adj, calculated_at)
                    VALUES (?, 'monthly', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (mgr, total, evaluated, hits, hit_rate,
                     avg_d5, avg_d10, avg_d20,
                     best_text, worst_text, weight, now_str),
                )

        except Exception as e:
            logger.error("Manager scorecard calc failed for %s: %s", mgr, e)
            scorecards[mgr] = {"total": 0, "hit_rate": 0.0, "weight_adj": 1.0}

    return scorecards


def format_manager_scorecard(scorecards: dict) -> str:
    """매니저 성적표를 텔레그램 메시지로 포맷."""
    mgr_names = {
        "scalp": "⚡ 리버모어(단타)",
        "swing": "🔥 오닐(스윙)",
        "position": "📊 린치(포지션)",
        "long_term": "💎 버핏(장기)",
    }
    lines = [
        "📋 매니저 성적표 (최근 30일)",
        "━" * 22,
    ]
    for mgr, name in mgr_names.items():
        card = scorecards.get(mgr, {})
        total = card.get("total", 0)
        hits = card.get("hits", 0)
        rate = card.get("hit_rate", 0)
        avg5 = card.get("avg_return_5d", 0) * 100
        weight = card.get("weight_adj", 1.0)

        # 등급
        if rate >= 70:
            grade = "A"
        elif rate >= 60:
            grade = "B"
        elif rate >= 50:
            grade = "C"
        else:
            grade = "D"

        lines.append(f"\n{name}")
        if total == 0:
            lines.append("  추천 이력 없음")
            continue
        lines.append(f"  추천: {total}건 | 적중: {hits}건 ({rate:.0f}%) [{grade}]")
        lines.append(f"  5일 평균수익: {avg5:+.1f}%")
        if card.get("best_trade"):
            lines.append(f"  최고: {card['best_trade']}")
        lines.append(f"  가중치: {weight:.2f}x")

    return "\n".join(lines)


# ── 사용자 매매 패턴 학습 ──────────────────────────────────────


def analyze_user_trade_patterns(db) -> dict[str, Any]:
    """주호님의 과거 매매에서 패턴을 추출.

    분석 항목:
    - 수익 낸 섹터 vs 손실 섹터
    - 평균 보유 기간 (수익 매매 vs 손실 매매)
    - 선호 진입 점수대
    - 시장 레짐별 성과
    - 집중도 (상위 3 섹터 비중)
    """
    profile = {}
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with db._connect() as conn:
            # 1. 모든 종료된 매매 조회
            trades = conn.execute(
                """
                SELECT h.ticker, h.name, h.buy_price, h.current_price,
                       h.pnl_pct, h.buy_date, h.updated_at,
                       h.holding_type, h.quantity, h.eval_amount
                FROM holdings h
                WHERE h.status = 'closed' AND h.pnl_pct IS NOT NULL
                ORDER BY h.updated_at DESC
                LIMIT 200
                """,
            ).fetchall()

        if not trades:
            logger.info("No closed trades found for pattern analysis")
            return {}

        # 기본 통계
        profits = [t for t in trades if (t["pnl_pct"] or 0) > 0]
        losses = [t for t in trades if (t["pnl_pct"] or 0) <= 0]
        total_pnl = sum(t["pnl_pct"] or 0 for t in trades)

        profile["total_trades"] = len(trades)
        profile["win_count"] = len(profits)
        profile["loss_count"] = len(losses)
        profile["win_rate"] = len(profits) / len(trades) * 100 if trades else 0
        profile["avg_pnl"] = total_pnl / len(trades) if trades else 0
        profile["avg_win"] = (
            sum(t["pnl_pct"] for t in profits) / len(profits)
            if profits else 0
        )
        profile["avg_loss"] = (
            sum(t["pnl_pct"] for t in losses) / len(losses)
            if losses else 0
        )

        # 보유 기간 분석
        hold_days_wins = []
        hold_days_losses = []
        for t in trades:
            try:
                buy = datetime.strptime(t["buy_date"][:10], "%Y-%m-%d")
                sell = datetime.strptime(t["updated_at"][:10], "%Y-%m-%d")
                days = (sell - buy).days
                if days < 0:
                    continue
                if (t["pnl_pct"] or 0) > 0:
                    hold_days_wins.append(days)
                else:
                    hold_days_losses.append(days)
            except Exception:
                continue

        profile["avg_hold_days_win"] = (
            sum(hold_days_wins) / len(hold_days_wins)
            if hold_days_wins else 0
        )
        profile["avg_hold_days_loss"] = (
            sum(hold_days_losses) / len(hold_days_losses)
            if hold_days_losses else 0
        )

        # 보유 유형 분석 (집중도)
        type_counts = {}
        for t in trades:
            ht = t.get("holding_type", "unknown")
            type_counts[ht] = type_counts.get(ht, 0) + 1
        profile["holding_type_distribution"] = type_counts

        # 수익 매매 상위 종목
        top_wins = sorted(profits, key=lambda t: t["pnl_pct"] or 0, reverse=True)[:5]
        profile["top_wins"] = [
            {"name": t["name"], "pnl": round((t["pnl_pct"] or 0) * 100, 1)}
            for t in top_wins
        ]

        # 손실 매매 하위 종목
        top_losses = sorted(losses, key=lambda t: t["pnl_pct"] or 0)[:5]
        profile["top_losses"] = [
            {"name": t["name"], "pnl": round((t["pnl_pct"] or 0) * 100, 1)}
            for t in top_losses
        ]

        # DB 저장
        _save_profile(db, "trade_stats", json.dumps(profile, ensure_ascii=False), now_str)
        _save_profile(db, "last_analysis", now_str, now_str)

        logger.info(
            "Trade pattern analysis: %d trades, %.0f%% win rate, avg PnL %.1f%%",
            len(trades), profile["win_rate"], profile["avg_pnl"] * 100,
        )

    except Exception as e:
        logger.error("Trade pattern analysis failed: %s", e)

    return profile


def _save_profile(db, key: str, value: str, now_str: str) -> None:
    """user_trade_profile 테이블에 키-값 저장."""
    try:
        with db._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_trade_profile (profile_key, profile_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    profile_value = excluded.profile_value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now_str),
            )
    except Exception as e:
        logger.debug("Profile save failed for %s: %s", key, e)


def format_trade_profile(profile: dict) -> str:
    """매매 프로필을 텔레그램 메시지로 포맷."""
    if not profile:
        return "아직 분석할 매매 이력이 부족합니다."

    lines = [
        "📈 주호님 매매 프로필 분석",
        "━" * 22,
        f"\n총 매매: {profile.get('total_trades', 0)}건",
        f"승률: {profile.get('win_rate', 0):.0f}% "
        f"({profile.get('win_count', 0)}승 / {profile.get('loss_count', 0)}패)",
        f"평균 수익: {profile.get('avg_pnl', 0) * 100:+.1f}%",
        f"평균 수익(이긴 매매): {profile.get('avg_win', 0) * 100:+.1f}%",
        f"평균 손실(진 매매): {profile.get('avg_loss', 0) * 100:.1f}%",
    ]

    # 보유 기간
    if profile.get("avg_hold_days_win"):
        lines.append(
            f"\n보유 기간(수익): 평균 {profile['avg_hold_days_win']:.0f}일"
        )
    if profile.get("avg_hold_days_loss"):
        lines.append(
            f"보유 기간(손실): 평균 {profile['avg_hold_days_loss']:.0f}일"
        )

    # 상위 매매
    top_wins = profile.get("top_wins", [])
    if top_wins:
        lines.append("\n🏆 최고 수익 매매")
        for w in top_wins[:3]:
            lines.append(f"  {w['name']}: +{w['pnl']:.1f}%")

    top_losses = profile.get("top_losses", [])
    if top_losses:
        lines.append("\n💔 최대 손실 매매")
        for l in top_losses[:3]:
            lines.append(f"  {l['name']}: {l['pnl']:.1f}%")

    return "\n".join(lines)


# ── 이벤트 → 전략 점수 반영 ────────────────────────────────────


async def apply_event_to_strategy(
    db, event_summary: str, affected_sectors: list[str],
    affected_tickers: list[str], adjustment: int,
    confidence: float = 0.7, duration_hours: int = 48,
) -> bool:
    """글로벌 이벤트를 전략 점수에 반영.

    긴급 뉴스 분석 결과를 DB에 저장하여
    scan_engine이 다음 스캔 시 해당 섹터/종목에 보너스/페널티 적용.
    """
    now = datetime.now(KST)
    expires = (now + timedelta(hours=duration_hours)).strftime("%Y-%m-%d %H:%M:%S")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        with db._connect() as conn:
            conn.execute(
                """
                INSERT INTO event_score_adjustments
                (event_type, event_summary, affected_sectors, affected_tickers,
                 score_adjustment, confidence, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "global_news", event_summary[:200],
                    json.dumps(affected_sectors, ensure_ascii=False),
                    json.dumps(affected_tickers, ensure_ascii=False),
                    adjustment, confidence, expires, now_str,
                ),
            )
        logger.info(
            "Event score adjustment saved: %s → %+d for %s",
            event_summary[:50], adjustment, affected_sectors,
        )
        return True
    except Exception as e:
        logger.error("Event score adjustment save failed: %s", e)
        return False


def get_active_event_adjustments(db) -> list[dict]:
    """현재 유효한 이벤트 기반 점수 조정 목록."""
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_summary, affected_sectors, affected_tickers,
                       score_adjustment, confidence, expires_at
                FROM event_score_adjustments
                WHERE expires_at >= ?
                ORDER BY created_at DESC
                """,
                (now_str,),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["affected_sectors"] = json.loads(d.get("affected_sectors", "[]"))
            except Exception:
                d["affected_sectors"] = []
            try:
                d["affected_tickers"] = json.loads(d.get("affected_tickers", "[]"))
            except Exception:
                d["affected_tickers"] = []
            results.append(d)
        return results
    except Exception as e:
        logger.debug("Event adjustments query failed: %s", e)
        return []


def get_event_bonus_for_ticker(db, ticker: str, sector: str = "") -> int:
    """특정 종목/섹터에 대한 이벤트 기반 점수 보너스."""
    adjustments = get_active_event_adjustments(db)
    total_bonus = 0
    for adj in adjustments:
        # 직접 종목 매칭
        if ticker in adj.get("affected_tickers", []):
            total_bonus += adj["score_adjustment"]
            continue
        # 섹터 매칭
        if sector and sector in adj.get("affected_sectors", []):
            bonus = int(adj["score_adjustment"] * adj.get("confidence", 0.7))
            total_bonus += bonus

    # 범위 제한 (-15 ~ +15)
    return max(-15, min(15, total_bonus))


def get_manager_weight(db, manager_key: str) -> float:
    """매니저 성적표 기반 가중치 조회 (기본 1.0)."""
    try:
        with db._connect() as conn:
            row = conn.execute(
                """
                SELECT weight_adj FROM manager_scorecard
                WHERE manager_key = ?
                ORDER BY calculated_at DESC LIMIT 1
                """,
                (manager_key,),
            ).fetchone()
        return float(row["weight_adj"]) if row else 1.0
    except Exception:
        return 1.0


def get_user_trade_profile(db) -> dict:
    """저장된 사용자 매매 프로필 조회."""
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT profile_value FROM user_trade_profile "
                "WHERE profile_key = 'trade_stats'",
            ).fetchone()
        if row:
            return json.loads(row["profile_value"])
    except Exception:
        pass
    return {}
