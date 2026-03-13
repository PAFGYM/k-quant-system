"""System self-evaluation score for K-Quant.

v6.2.1: 시스템이 스스로 성능을 100점 만점으로 평가.

점수 구성 (100점 만점):
1. 신호 적중률 (25점) — signal_performance 테이블 기반
2. 매매 성과 (25점) — trade_debrief 등급 기반
3. 알림 정확도 (15점) — 알림 발송 + 피드백 기반
4. 자가 학습 (15점) — 학습 루프 활성도
5. 비용 효율 (10점) — 캐시 히트율 + 적정 비용
6. 시스템 안정성 (10점) — 가동시간 + 에러율
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _hit_rate_to_signal_score(hit_rate: float) -> float:
    """적중률을 25점 만점 신호 점수로 변환."""
    if hit_rate >= 80:
        return 25.0
    if hit_rate >= 60:
        return 18.0 + (hit_rate - 60) / 20 * 7
    if hit_rate >= 40:
        return 10.0 + (hit_rate - 40) / 20 * 8
    if hit_rate >= 20:
        return 5.0 + (hit_rate - 20) / 20 * 5
    return hit_rate / 20 * 5


def _count_sql(db: Any, sql: str, params: tuple = ()) -> int:
    """간단한 COUNT 쿼리를 안전하게 실행."""
    try:
        with db._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        logger.debug("count query failed", exc_info=True)
        return 0


def compute_system_score(db: Any) -> dict:
    """시스템 자가 점수 계산 (100점 만점).

    Returns:
        dict with keys: total, signal, trade, alert, learning, cost, uptime, details
    """
    details: dict[str, Any] = {}

    # 1. 신호 적중률 (25점)
    signal_score, signal_details = _score_signals(db)
    details["signal"] = signal_details

    # 2. 매매 성과 (25점)
    trade_score, trade_details = _score_trades(db)
    details["trade"] = trade_details

    # 3. 알림 정확도 (15점)
    alert_score, alert_details = _score_alerts(db)
    details["alert"] = alert_details

    # 4. 자가 학습 활성도 (15점)
    learning_score, learn_details = _score_learning(db)
    details["learning"] = learn_details

    # 5. 비용 효율 (10점)
    cost_score, cost_details = _score_cost_efficiency(db)
    details["cost"] = cost_details

    # 6. 시스템 안정성 (10점)
    uptime_score, uptime_details = _score_uptime(db)
    details["uptime"] = uptime_details

    total = round(
        signal_score + trade_score + alert_score
        + learning_score + cost_score + uptime_score, 1
    )

    result = {
        "total": total,
        "signal": round(signal_score, 1),
        "trade": round(trade_score, 1),
        "alert": round(alert_score, 1),
        "learning": round(learning_score, 1),
        "cost": round(cost_score, 1),
        "uptime": round(uptime_score, 1),
        "details": details,
        "grade": _total_to_grade(total),
    }

    # DB에 저장
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        db.save_system_score(
            score_date=today,
            total_score=total,
            signal_score=signal_score,
            trade_score=trade_score,
            alert_score=alert_score,
            learning_score=learning_score,
            cost_score=cost_score,
            uptime_score=uptime_score,
            details_json=json.dumps(details, ensure_ascii=False, default=str),
        )
    except Exception as e:
        logger.debug("시스템 점수 DB 저장 실패: %s", e)

    return result


def _score_signals(db: Any) -> tuple[float, dict]:
    """신호 적중률 점수 (25점 만점)."""
    try:
        stats = db.get_signal_source_stats()
        if not stats:
            return 12.5, {"msg": "신호 데이터 부족 (기본점)", "hit_rate": 0}

        # 가중 평균 적중률 계산 (DB 쿼리: total/evaluated/hits)
        total_evaluated = sum(s.get("evaluated", 0) or 0 for s in stats)
        total_hits = sum(s.get("hits", 0) or 0 for s in stats)
        total_signals = sum(s.get("total", s.get("total_signals", 0)) or 0 for s in stats)
        if total_evaluated == 0 and total_signals > 0:
            return 10.0, {
                "hit_rate": None,
                "total_signals": total_signals,
                "hits": 0,
                "msg": "평가 대기 중인 신호만 존재",
            }
        hit_rate = (total_hits / total_evaluated * 100) if total_evaluated > 0 else 0
        raw_score = _hit_rate_to_signal_score(hit_rate)

        details = {
            "hit_rate": round(hit_rate, 1),
            "total_signals": total_signals,
            "hits": total_hits,
        }
        if total_evaluated < 10:
            baseline = 12.5
            sample_confidence = min(1.0, total_evaluated / 10.0)
            blended_score = raw_score * sample_confidence + baseline * (1.0 - sample_confidence)
            details["sample_adjusted"] = True
            details["evaluated"] = total_evaluated
            details["msg"] = "표본 부족 보정 적용"
            return round(blended_score, 1), details

        details["evaluated"] = total_evaluated
        return raw_score, details
    except Exception as e:
        logger.debug("신호 점수 계산 실패: %s", e)
        return 12.5, {"msg": "계산 오류", "error": str(e)}


def _score_trades(db: Any) -> tuple[float, dict]:
    """매매 성과 점수 (25점 만점)."""
    try:
        debriefs = db.get_trade_debriefs(limit=50)
        if not debriefs:
            return 12.5, {"msg": "매매 이력 부족 (기본점)", "grade_avg": "N/A"}

        grade_scores = {"A": 25, "B": 20, "C": 15, "D": 8, "F": 3}
        total = sum(grade_scores.get(d.get("grade", "C"), 15) for d in debriefs)
        avg = total / len(debriefs)

        # 등급 분포
        grade_dist = {}
        for d in debriefs:
            g = d.get("grade", "C")
            grade_dist[g] = grade_dist.get(g, 0) + 1

        # 승률
        wins = sum(1 for d in debriefs if d.get("pnl_pct", 0) > 0)
        win_rate = wins / len(debriefs) * 100

        return avg, {
            "count": len(debriefs),
            "win_rate": round(win_rate, 1),
            "grade_dist": grade_dist,
        }
    except Exception as e:
        logger.debug("매매 점수 계산 실패: %s", e)
        return 12.5, {"msg": "계산 오류", "error": str(e)}


def _score_alerts(db: Any) -> tuple[float, dict]:
    """알림 정확도 점수 (15점 만점)."""
    try:
        # 최근 7일 알림 수
        alerts = db.get_recent_alerts(limit=100)
        recent = [a for a in alerts
                  if a.get("created_at", "") >= (datetime.utcnow() - timedelta(days=7)).isoformat()]

        if not recent:
            return 10.0, {"msg": "알림 없음 (기본점)", "count": 0}

        # 알림 수에 따른 점수 (적당히 많으면 좋음)
        count = len(recent)
        if 5 <= count <= 30:
            score = 15.0  # 적절한 빈도
        elif count < 5:
            score = 10.0  # 너무 적음
        elif count <= 50:
            score = 12.0  # 약간 많음
        else:
            score = 8.0   # 너무 많음 (알림 피로)

        return score, {"count": count, "msg": "7일간 알림"}
    except Exception as e:
        logger.debug("알림 점수 계산 실패: %s", e)
        return 10.0, {"msg": "계산 오류"}


def _score_learning(db: Any) -> tuple[float, dict]:
    """자가 학습 활성도 점수 (15점 만점)."""
    try:
        score = 0.0
        details: dict[str, Any] = {}

        # 1) 신호 가중치 조정 이력 존재? (+5)
        stats = db.get_signal_source_stats()
        manager_scorecard_rows = _count_sql(
            db,
            "SELECT COUNT(*) FROM manager_scorecard "
            "WHERE calculated_at >= datetime('now','-30 day')",
        )
        if stats:
            score += 5.0
            details["weight_adjustment"] = True
            details["weight_adjustment_source"] = "signal_source_stats"
        elif manager_scorecard_rows > 0:
            score += 5.0
            details["weight_adjustment"] = True
            details["weight_adjustment_source"] = "manager_scorecard"
        else:
            details["weight_adjustment"] = False
            details["weight_adjustment_source"] = "none"

        # 2) 매매 복기 생성? (+5)
        debriefs = db.get_trade_debriefs(limit=10)
        if debriefs:
            ai_reviewed = sum(1 for d in debriefs if d.get("ai_review"))
            score += min(5.0, ai_reviewed)
            details["debriefs"] = len(debriefs)
            details["ai_reviewed"] = ai_reviewed
        else:
            details["debriefs"] = 0

        # 3) 사용자 선호도 학습? (+3)
        prefs = db.get_user_preferences()
        if prefs and len(prefs) >= 2:
            score += 3.0
            details["preferences"] = len(prefs)
        else:
            details["preferences"] = len(prefs) if prefs else 0

        # 4) RAG 대화 기록? (+2)
        try:
            recent_chat = db.get_recent_enhanced_messages(limit=5)
            if recent_chat:
                score += 2.0
                details["rag_active"] = True
            else:
                details["rag_active"] = False
        except Exception:
            details["rag_active"] = False

        # 5) 매니저 stance 누적? (+2)
        try:
            stances = db.get_recent_manager_stances(hours=48)
            stance_count = len(stances or {})
            details["manager_stances"] = stance_count
            if stance_count >= 3:
                score += 2.0
        except Exception:
            details["manager_stances"] = 0

        # 6) ML 예측 검증 준비도 (성숙한 예측만 반영) (+3)
        mature_total = _count_sql(
            db,
            "SELECT COUNT(*) FROM ml_predictions WHERE pred_date <= date('now','-5 day')",
        )
        mature_done = _count_sql(
            db,
            "SELECT COUNT(*) FROM ml_predictions "
            "WHERE pred_date <= date('now','-5 day') AND actual_return IS NOT NULL",
        )
        details["ml_mature_total"] = mature_total
        details["ml_mature_done"] = mature_done
        if mature_total > 0:
            eval_ratio = mature_done / mature_total
            score += min(3.0, round(eval_ratio * 3.0, 2))
            details["ml_eval_ratio"] = round(eval_ratio * 100, 1)
        else:
            details["ml_eval_ratio"] = None

        return min(15.0, score), details
    except Exception as e:
        logger.debug("학습 점수 계산 실패: %s", e)
        return 7.5, {"msg": "계산 오류"}


def _score_cost_efficiency(db: Any) -> tuple[float, dict]:
    """비용 효율 점수 (10점 만점)."""
    try:
        monthly = db.get_monthly_api_usage()
        if not monthly or monthly.get("total_calls", 0) == 0:
            return 8.0, {"msg": "사용 이력 없음 (기본점)"}

        total_cost = monthly.get("total_cost", 0)
        total_input = monthly.get("total_input", 0)
        cache_read = monthly.get("total_cache_read", 0)
        total_all = total_input + cache_read

        # 캐시 절약률 → 점수
        cache_pct = (cache_read / total_all * 100) if total_all > 0 else 0

        # 비용 기반 점수
        # $5 이하: 10점, $10 이하: 8점, $20 이하: 6점, $30+: 4점
        if total_cost <= 5:
            cost_score = 5.0
        elif total_cost <= 10:
            cost_score = 4.0
        elif total_cost <= 20:
            cost_score = 3.0
        else:
            cost_score = 2.0

        # 캐시 점수: 50%+: 5점, 30%+: 3점, 10%+: 2점
        if cache_pct >= 50:
            cache_score = 5.0
        elif cache_pct >= 30:
            cache_score = 3.0
        elif cache_pct >= 10:
            cache_score = 2.0
        else:
            cache_score = 1.0

        score = cost_score + cache_score

        return min(10.0, score), {
            "monthly_cost": round(total_cost, 4),
            "cache_pct": round(cache_pct, 1),
        }
    except Exception as e:
        logger.debug("비용 점수 계산 실패: %s", e)
        return 5.0, {"msg": "계산 오류"}


def _score_uptime(db: Any) -> tuple[float, dict]:
    """시스템 안정성 점수 (10점 만점)."""
    try:
        # 최근 이벤트 로그에서 에러 비율 확인
        events = db.get_events(limit=100)
        if not events:
            try:
                job_runs = db.get_job_runs(datetime.utcnow().strftime("%Y-%m-%d")) or []
            except Exception:
                job_runs = []
            if not job_runs:
                return 8.0, {"msg": "이벤트 로그 없음 (기본점)"}

            total_jobs = len(job_runs)
            error_jobs = sum(1 for j in job_runs if j.get("status") == "error")
            success_jobs = sum(1 for j in job_runs if j.get("status") == "success")
            success_rate = (success_jobs / total_jobs * 100) if total_jobs > 0 else 0
            if success_rate >= 95:
                score = 9.0
            elif success_rate >= 85:
                score = 8.0
            elif success_rate >= 70:
                score = 7.0
            else:
                score = 5.0
            return score, {
                "job_success_rate": round(success_rate, 1),
                "job_errors": error_jobs,
                "recent_jobs": total_jobs,
            }

        errors = sum(1 for e in events if e.get("severity") in ("error", "critical"))
        total = len(events)
        error_rate = (errors / total * 100) if total > 0 else 0

        # 에러율 → 점수: 0%=10, 5%=8, 10%=6, 20%+=4
        if error_rate <= 1:
            score = 10.0
        elif error_rate <= 5:
            score = 8.0
        elif error_rate <= 10:
            score = 6.0
        elif error_rate <= 20:
            score = 4.0
        else:
            score = 2.0

        # API 에러율 확인
        monthly = db.get_monthly_api_usage()
        api_errors = monthly.get("error_count", 0)
        api_total = monthly.get("total_calls", 0)
        api_err_rate = (api_errors / api_total * 100) if api_total > 0 else 0

        return score, {
            "error_rate": round(error_rate, 1),
            "api_error_rate": round(api_err_rate, 1),
            "recent_events": total,
        }
    except Exception as e:
        logger.debug("안정성 점수 계산 실패: %s", e)
        return 5.0, {"msg": "계산 오류"}


def _total_to_grade(total: float) -> str:
    """총점 → 등급 변환."""
    if total >= 90:
        return "S"
    if total >= 80:
        return "A"
    if total >= 70:
        return "B"
    if total >= 60:
        return "C"
    if total >= 50:
        return "D"
    return "F"


def format_score_report(score: dict) -> str:
    """시스템 점수를 텔레그램 메시지 형식으로 포맷."""
    total = score.get("total", 0)
    grade = score.get("grade", "?")

    # 등급별 이모지
    grade_emoji = {
        "S": "🏆", "A": "🌟", "B": "✅",
        "C": "📊", "D": "⚠️", "F": "❌",
    }.get(grade, "📊")

    lines = [
        f"{grade_emoji} K-Quant 시스템 점수: {total}/100 ({grade})",
        f"{'━' * 24}",
    ]

    # 각 항목 바 차트
    items = [
        ("🎯 신호 적중", score.get("signal", 0), 25),
        ("💰 매매 성과", score.get("trade", 0), 25),
        ("🔔 알림 품질", score.get("alert", 0), 15),
        ("🧠 자가 학습", score.get("learning", 0), 15),
        ("💵 비용 효율", score.get("cost", 0), 10),
        ("⚙️ 시스템 안정", score.get("uptime", 0), 10),
    ]

    for label, val, maximum in items:
        pct = val / maximum * 100 if maximum > 0 else 0
        bar_len = int(pct / 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(f"{label}: {bar} {val}/{maximum}")

    # 세부 사항
    details = score.get("details", {})

    signal_d = details.get("signal", {})
    if "hit_rate" in signal_d:
        if signal_d["hit_rate"] is None:
            lines.append("\n📈 신호 적중률: 평가 대기")
        else:
            lines.append(f"\n📈 신호 적중률: {signal_d['hit_rate']}%")

    trade_d = details.get("trade", {})
    if "win_rate" in trade_d:
        lines.append(f"📈 매매 승률: {trade_d['win_rate']}%")
        if "grade_dist" in trade_d:
            dist = trade_d["grade_dist"]
            dist_str = " ".join(f"{g}:{c}" for g, c in sorted(dist.items()))
            lines.append(f"📊 등급 분포: {dist_str}")

    cost_d = details.get("cost", {})
    if "monthly_cost" in cost_d:
        krw = cost_d["monthly_cost"] * 1400
        lines.append(f"💵 이번달 비용: ${cost_d['monthly_cost']:.4f} (≈{krw:,.0f}원)")
    if "cache_pct" in cost_d:
        lines.append(f"⚡ 캐시 절약률: {cost_d['cache_pct']}%")

    learn_d = details.get("learning", {})
    if learn_d:
        active = []
        if learn_d.get("weight_adjustment"):
            active.append("가중치 조정")
        if learn_d.get("debriefs", 0) > 0:
            active.append(f"복기 {learn_d['debriefs']}건")
        if learn_d.get("preferences", 0) > 0:
            active.append(f"선호도 {learn_d['preferences']}개")
        if learn_d.get("rag_active"):
            active.append("RAG 활성")
        if learn_d.get("manager_stances", 0) > 0:
            active.append(f"매니저 stance {learn_d['manager_stances']}개")
        if learn_d.get("ml_mature_total", 0) > 0:
            active.append(
                f"ML 검증 {learn_d.get('ml_mature_done', 0)}/"
                f"{learn_d.get('ml_mature_total', 0)}"
            )
        if active:
            lines.append(f"🧠 학습: {', '.join(active)}")

    return "\n".join(lines)


def format_score_trend(db: Any, days: int = 7) -> str:
    """점수 추이 (최근 N일)."""
    scores = db.get_system_scores(limit=days)
    if not scores:
        return "📊 아직 점수 기록이 없습니다."

    lines = ["📊 시스템 점수 추이"]
    for s in reversed(scores):
        date = s.get("score_date", "")
        total = s.get("total_score", 0)
        grade = _total_to_grade(total)
        emoji = {"S": "🏆", "A": "🌟", "B": "✅", "C": "📊", "D": "⚠️", "F": "❌"}.get(grade, "📊")
        lines.append(f"  {date}: {emoji} {total:.0f}점 ({grade})")

    if len(scores) >= 2:
        latest = scores[0].get("total_score", 0)
        oldest = scores[-1].get("total_score", 0)
        diff = latest - oldest
        trend = "📈 상승" if diff > 0 else ("📉 하락" if diff < 0 else "➖ 유지")
        lines.append(f"\n{trend}: {diff:+.1f}점")

    return "\n".join(lines)
