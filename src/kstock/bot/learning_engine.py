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
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


_STYLE_LABELS = {
    "scalper": "단타",
    "swing": "스윙",
    "position": "포지션",
    "long_term": "장기",
    "balanced": "균형",
    "신규": "신규",
}

_CHAT_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "market": ("시장", "시황", "환율", "유가", "vix", "금리", "뉴스", "전시", "레짐"),
    "buy": ("매수", "사도", "살까", "종목", "추천", "들어가", "진입", "비중"),
    "sell": ("매도", "익절", "손절", "정리", "청산", "팔아", "팔까"),
    "holdings": ("보유", "잔고", "계좌", "포트", "내 종목", "내 보유"),
    "tenbagger": ("텐베거", "텐배거", "미래", "산업", "정책", "이벤트", "촉매"),
}

_CHAT_TOPIC_LABELS = {
    "market": "시장",
    "buy": "매수",
    "sell": "매도",
    "holdings": "보유",
    "tenbagger": "텐베거",
}

_MANAGER_STRATEGY_CLUSTERS: dict[str, tuple[str, ...]] = {
    "scalp": ("A", "B", "G", "H"),
    "swing": ("F", "J"),
    "position": ("D", "I"),
    "long_term": ("C", "E"),
}

_MANAGER_LABELS = {
    "scalp": "⚡ 리버모어(단타)",
    "swing": "🔥 오닐(스윙)",
    "position": "📊 린치(포지션)",
    "long_term": "💎 버핏(장기)",
    "tenbagger": "🔟 텐베거",
}

_STRATEGY_SHORT_LABELS = {
    "A": "반등",
    "B": "ETF",
    "C": "장기",
    "D": "섹터",
    "E": "글로벌",
    "F": "모멘텀",
    "G": "돌파",
    "H": "공격",
    "I": "실적",
    "J": "평균회귀",
}


def _safe_json_loads(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return {}


def _infer_style_label(profile: dict[str, Any]) -> str:
    style = str(profile.get("dominant_style") or profile.get("style") or "balanced")
    return _STYLE_LABELS.get(style, style)


def _load_user_chat_focus(db, limit: int = 300) -> dict[str, Any]:
    """최근 사용자 질문에서 관심 주제를 추출."""
    try:
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT content
                FROM chat_history
                WHERE role='user'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except Exception as e:
        logger.debug("load_user_chat_focus failed: %s", e)
        return {
            "message_count": 0,
            "topic_counts": {},
            "top_topics": [],
            "top_keywords": [],
        }

    counts: Counter[str] = Counter()
    keywords: Counter[str] = Counter()
    messages = [str(r["content"] or "") for r in rows]
    for msg in messages:
        lowered = msg.lower()
        for topic, topic_keywords in _CHAT_TOPIC_KEYWORDS.items():
            matched = False
            for keyword in topic_keywords:
                key = keyword.lower()
                if keyword in msg or key in lowered:
                    keywords[keyword] += 1
                    matched = True
            if matched:
                counts[topic] += 1

    top_topics = [
        _CHAT_TOPIC_LABELS.get(topic, topic)
        for topic, _ in counts.most_common(3)
    ]
    return {
        "message_count": len(messages),
        "topic_counts": dict(counts),
        "top_topics": top_topics,
        "top_keywords": [word for word, _ in keywords.most_common(5)],
    }


def _build_operator_profile(profile: dict[str, Any], chat_focus: dict[str, Any]) -> dict[str, Any]:
    """매매 이력 + 질문 패턴 기반 초개인화 프로필 생성."""
    total_trades = int(profile.get("total_trades", 0) or 0)
    win_rate = float(profile.get("win_rate", 0) or 0)
    avg_hold = float(profile.get("avg_hold_days_win", 0) or 0)
    avg_pnl = float(profile.get("avg_pnl", 0) or 0) * 100
    avg_win = float(profile.get("avg_win", 0) or 0) * 100
    avg_loss = float(profile.get("avg_loss", 0) or 0) * 100
    holding_dist = profile.get("holding_type_distribution", {}) or {}

    dominant_style = "balanced"
    if holding_dist:
        dominant_style = str(
            max(holding_dist.items(), key=lambda item: item[1])[0]
        ).strip() or dominant_style
    if dominant_style in {"auto", "unknown"}:
        if avg_hold <= 3:
            dominant_style = "scalper"
        elif avg_hold <= 14:
            dominant_style = "swing"
        elif avg_hold <= 60:
            dominant_style = "position"
        else:
            dominant_style = "long_term"

    strengths: list[str] = []
    if win_rate >= 60:
        strengths.append(f"승률 {win_rate:.0f}%")
    if avg_win > abs(avg_loss) and avg_win > 0:
        strengths.append("손익비 우위")
    if avg_pnl > 5:
        strengths.append("수익 종목을 크게 키움")

    risks: list[str] = []
    if avg_loss <= -7:
        risks.append("손실 종목 방치 주의")
    if total_trades >= 5 and win_rate < 45:
        risks.append("진입 정확도 재점검 필요")
    if float(profile.get("avg_hold_days_loss", 0) or 0) > float(profile.get("avg_hold_days_win", 0) or 0):
        risks.append("손실 종목 보유가 길어지는 편")

    focus_topics = list(chat_focus.get("top_topics") or [])
    if not focus_topics:
        focus_topics = ["시장", "매수", "보유"]

    brief_parts = [
        f"{', '.join(focus_topics[:3])} 중심으로 묻는 편",
        f"주력 스타일은 {_infer_style_label({'dominant_style': dominant_style})}",
    ]
    if strengths:
        brief_parts.append(f"강점은 {strengths[0]}")
    if risks:
        brief_parts.append(f"주의는 {risks[0]}")

    return {
        "message_count": int(chat_focus.get("message_count", 0) or 0),
        "primary_focus": focus_topics,
        "top_keywords": list(chat_focus.get("top_keywords") or []),
        "dominant_style": dominant_style,
        "dominant_style_label": _STYLE_LABELS.get(dominant_style, dominant_style),
        "strengths": strengths,
        "risks": risks,
        "top_wins": profile.get("top_wins", [])[:3],
        "top_losses": profile.get("top_losses", [])[:3],
        "assistant_brief": " · ".join(part for part in brief_parts if part),
    }


def format_operator_profile(profile: dict[str, Any]) -> str:
    """초개인화 운영자 프로필을 짧게 포맷."""
    if not profile:
        return "아직 개인화 프로필이 준비되지 않았습니다."

    focus = ", ".join(profile.get("primary_focus", [])[:3]) or "시장, 매수, 보유"
    lines = [
        "👤 주호님 투자 DNA",
        f"  주로 보는 것: {focus}",
        f"  주력 스타일: {profile.get('dominant_style_label', '균형')}",
    ]
    strengths = profile.get("strengths", [])
    risks = profile.get("risks", [])
    if strengths:
        lines.append(f"  강점: {strengths[0]}")
    if risks:
        lines.append(f"  주의: {risks[0]}")
    return "\n".join(lines)


def format_learning_impact_snapshot(db, days: int = 7) -> str:
    """학습 결과가 실제 추천/코칭을 어떻게 바꾸는지 요약."""
    lines = ["🎯 학습으로 바뀐 것"]

    profile = get_user_operator_profile(db)
    if profile:
        focus = " · ".join(profile.get("primary_focus", [])[:3]) or "시장 · 매수 · 보유"
        lines.append(
            f"  개인화: {focus} 중심, {_infer_style_label(profile)} 레인 우선"
        )
        strengths = list(profile.get("strengths") or [])
        risks = list(profile.get("risks") or [])
        if strengths or risks:
            pieces: list[str] = []
            if strengths:
                pieces.append(f"강점 {strengths[0]}")
            if risks:
                pieces.append(f"주의 {risks[0]}")
            lines.append(f"  투자 DNA: {' | '.join(pieces)}")

    latest_cards: dict[str, dict[str, Any]] = {}
    try:
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.manager_key, m.evaluated_recs, m.hit_rate, m.avg_return_5d, m.weight_adj
                FROM manager_scorecard m
                JOIN (
                    SELECT manager_key, MAX(calculated_at) AS latest_ts
                    FROM manager_scorecard
                    GROUP BY manager_key
                ) latest
                  ON latest.manager_key = m.manager_key
                 AND latest.latest_ts = m.calculated_at
                """
            ).fetchall()
        latest_cards = {str(row["manager_key"]): dict(row) for row in rows}
    except Exception:
        latest_cards = {}

    if latest_cards:
        boosted: list[str] = []
        reduced: list[str] = []
        for key, card in latest_cards.items():
            label = _MANAGER_LABELS.get(key, key)
            weight = float(card.get("weight_adj", 1.0) or 1.0)
            evaluated = int(card.get("evaluated_recs", 0) or 0)
            suffix = f"({evaluated}건)"
            if weight >= 1.05:
                boosted.append(f"{label} {weight:.2f}x {suffix}")
            elif weight <= 0.95:
                reduced.append(f"{label} {weight:.2f}x {suffix}")
        if boosted:
            lines.append(f"  강화: {', '.join(boosted[:2])}")
        if reduced:
            lines.append(f"  보수화: {', '.join(reduced[:3])}")

        swing_card = latest_cards.get("swing")
        long_term_card = latest_cards.get("long_term")
        scalp_card = latest_cards.get("scalp")
        action_lines: list[str] = []
        if swing_card and float(swing_card.get("weight_adj", 1.0) or 1.0) <= 0.9:
            action_lines.append("스윙 후보는 더 까다롭게 선별")
        if long_term_card and float(long_term_card.get("hit_rate", 0) or 0.0) >= 60:
            action_lines.append("장기 레인은 보유/우선순위 유지")
        if scalp_card and int(scalp_card.get("evaluated_recs", 0) or 0) < 5:
            action_lines.append("단타 강화는 표본이 적어 과신 금지")
        if action_lines:
            lines.append(f"  추천 변화: {' | '.join(action_lines[:3])}")

    events: list[dict[str, Any]] = []
    try:
        if hasattr(db, "get_learning_history"):
            events = list(db.get_learning_history(days=days) or [])
        else:
            with db._connect() as conn:
                cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
                rows = conn.execute(
                    """
                    SELECT date, event_type, description, impact_summary, data_json
                    FROM learning_history
                    WHERE date >= ?
                    ORDER BY date DESC
                    LIMIT 30
                    """,
                    (cutoff,),
                ).fetchall()
            events = [dict(row) for row in rows]
    except Exception:
        events = []

    if events:
        event_map = {str(ev.get("event_type") or ""): ev for ev in events}
        evidence: list[str] = []
        regime = event_map.get("market_regime")
        if regime and regime.get("description"):
            evidence.append(str(regime["description"]))
        cross = event_map.get("cross_market_analysis")
        if cross and cross.get("description"):
            evidence.append(str(cross["description"]))
        history = event_map.get("historical_trade_debrief")
        if history and history.get("impact_summary"):
            evidence.append(str(history["impact_summary"]))
        if evidence:
            lines.append(f"  최근 근거: {' | '.join(evidence[:3])}")

    return "\n".join(lines)


def get_ml_progress_snapshot(db) -> dict[str, Any]:
    """ML 예측이 현재 어느 평가 단계까지 도달했는지 요약."""
    snapshot: dict[str, Any] = {
        "total_predictions": 0,
        "evaluated_predictions": 0,
        "prediction_days": 0,
        "first_pred_date": None,
        "last_pred_date": None,
        "d1_ready": 0,
        "d3_ready": 0,
        "d5_ready": 0,
        "pending_5d": 0,
        "high_conf_total": 0,
        "high_conf_d1": 0,
        "high_conf_d3": 0,
        "high_conf_d5": 0,
    }
    try:
        with db._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM ml_predictions").fetchone()[0] or 0)
            evaluated = int(
                conn.execute(
                    "SELECT COUNT(*) FROM ml_predictions WHERE actual_return IS NOT NULL"
                ).fetchone()[0] or 0
            )
            rows = conn.execute(
                """
                SELECT pred_date, COUNT(*) AS cnt
                FROM ml_predictions
                GROUP BY pred_date
                ORDER BY pred_date ASC
                """
            ).fetchall()
            high_conf_rows = conn.execute(
                """
                SELECT pred_date, COUNT(*) AS cnt
                FROM ml_predictions
                WHERE probability >= 0.65
                GROUP BY pred_date
                ORDER BY pred_date ASC
                """
            ).fetchall()
    except Exception as e:
        logger.debug("get_ml_progress_snapshot failed: %s", e)
        return snapshot

    date_counts = [(str(r["pred_date"]), int(r["cnt"] or 0)) for r in rows if r["pred_date"]]
    high_conf_counts = {str(r["pred_date"]): int(r["cnt"] or 0) for r in high_conf_rows if r["pred_date"]}

    snapshot["total_predictions"] = total
    snapshot["evaluated_predictions"] = evaluated
    snapshot["prediction_days"] = len(date_counts)
    if date_counts:
        snapshot["first_pred_date"] = date_counts[0][0]
        snapshot["last_pred_date"] = date_counts[-1][0]

    d1_ready = d3_ready = d5_ready = 0
    high_conf_total = sum(high_conf_counts.values())
    high_conf_d1 = high_conf_d3 = high_conf_d5 = 0

    for idx, (pred_date, count) in enumerate(date_counts):
        later_days = len(date_counts) - idx - 1
        if later_days >= 1:
            d1_ready += count
            high_conf_d1 += high_conf_counts.get(pred_date, 0)
        if later_days >= 3:
            d3_ready += count
            high_conf_d3 += high_conf_counts.get(pred_date, 0)
        if later_days >= 5:
            d5_ready += count
            high_conf_d5 += high_conf_counts.get(pred_date, 0)

    snapshot["d1_ready"] = d1_ready
    snapshot["d3_ready"] = d3_ready
    snapshot["d5_ready"] = d5_ready
    snapshot["pending_5d"] = max(0, total - d5_ready)
    snapshot["high_conf_total"] = high_conf_total
    snapshot["high_conf_d1"] = high_conf_d1
    snapshot["high_conf_d3"] = high_conf_d3
    snapshot["high_conf_d5"] = high_conf_d5
    return snapshot


def format_ml_progress_snapshot(db) -> str:
    """사용자에게 보이는 ML 진행 성적표."""
    data = get_ml_progress_snapshot(db)
    total = int(data.get("total_predictions", 0) or 0)
    if total <= 0:
        return "🧪 ML 진행 성적표\n  아직 예측 데이터가 없습니다."

    first_pred = str(data.get("first_pred_date") or "-")
    last_pred = str(data.get("last_pred_date") or "-")
    d1_ready = int(data.get("d1_ready", 0) or 0)
    d3_ready = int(data.get("d3_ready", 0) or 0)
    d5_ready = int(data.get("d5_ready", 0) or 0)
    evaluated = int(data.get("evaluated_predictions", 0) or 0)
    pending_5d = int(data.get("pending_5d", 0) or 0)
    high_conf_total = int(data.get("high_conf_total", 0) or 0)
    high_conf_d3 = int(data.get("high_conf_d3", 0) or 0)
    high_conf_d5 = int(data.get("high_conf_d5", 0) or 0)

    lines = ["🧪 ML 진행 성적표"]
    lines.append(f"  예측 구간: {first_pred} ~ {last_pred}")
    lines.append(f"  누적 예측: {total:,}건 | 실제 D+5 평가 완료 {evaluated:,}건")
    lines.append(f"  중간 도달: D+1 {d1_ready:,}건 | D+3 {d3_ready:,}건 | D+5 {d5_ready:,}건")
    if pending_5d > 0:
        lines.append(f"  대기 중: 5거래일 채점 전 {pending_5d:,}건")
    if high_conf_total > 0:
        lines.append(
            f"  고확률(65%+) 추적: 전체 {high_conf_total:,}건 | D+3 {high_conf_d3:,}건 | D+5 {high_conf_d5:,}건"
        )
    if d5_ready == 0:
        lines.append("  해석: 최근 예측은 아직 5거래일 채점 전이라 중간 진행도 위주로 봐야 합니다.")
    return "\n".join(lines)


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
    managers = ["scalp", "swing", "position", "long_term", "tenbagger"]

    for mgr in managers:
        try:
            with db._connect() as conn:
                if mgr == "tenbagger":
                    rows = conn.execute(
                        """
                        SELECT ticker, name, tenbagger_score, current_return
                        FROM tenbagger_universe
                        WHERE status='active'
                        ORDER BY tenbagger_score DESC
                        """,
                    ).fetchall()
                else:
                    cluster = _MANAGER_STRATEGY_CLUSTERS.get(mgr, ())
                    placeholders = ", ".join("?" for _ in cluster) or "''"
                    rows = conn.execute(
                        f"""
                        SELECT r.ticker, r.name, r.rec_score, r.rec_price,
                               r.strategy_type,
                               rr.day5_return, rr.day10_return, rr.day20_return,
                               rr.correct
                        FROM recommendations r
                        LEFT JOIN recommendation_results rr
                            ON rr.recommendation_id = r.id
                        WHERE r.created_at >= ?
                          AND (
                              r.manager = ?
                              OR (
                                  COALESCE(r.manager, '') = ''
                                  AND r.strategy_type IN ({placeholders})
                              )
                          )
                        ORDER BY r.created_at DESC
                        """,
                        (cutoff, mgr, *cluster),
                    ).fetchall()

            if not rows:
                scorecards[mgr] = {
                    "total": 0, "evaluated": 0, "hits": 0,
                    "hit_rate": 0.0, "avg_return_5d": 0.0,
                    "weight_adj": 1.0,
                }
                continue

            if mgr == "tenbagger":
                total = len(rows)
                avg_score = sum(float(r["tenbagger_score"] or 0) for r in rows) / total if total else 0.0
                best = max(rows, key=lambda r: r["tenbagger_score"] or -999)
                best_text = f"{best['name']} {float(best['tenbagger_score'] or 0):.0f}점"
                card = {
                    "total": total,
                    "evaluated": 0,
                    "hits": 0,
                    "hit_rate": 0.0,
                    "avg_return_5d": 0.0,
                    "avg_return_10d": 0.0,
                    "avg_return_20d": 0.0,
                    "best_trade": best_text,
                    "worst_trade": "",
                    "weight_adj": 1.0,
                    "avg_score": avg_score,
                    "source": "universe",
                }
                scorecards[mgr] = card
                with db._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO manager_scorecard
                        (manager_key, period, total_recs, evaluated_recs, hits,
                         hit_rate, avg_return_5d, avg_return_10d, avg_return_20d,
                         best_trade, worst_trade, weight_adj, calculated_at)
                        VALUES (?, 'monthly', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mgr,
                            total,
                            0,
                            0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            best_text,
                            "",
                            1.0,
                            now_str,
                        ),
                    )
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

            strategy_breakdown: dict[str, dict[str, float]] = {}
            for strategy_type in sorted({str(r["strategy_type"] or "") for r in rows if r["strategy_type"]}):
                strat_rows = [r for r in rows if str(r["strategy_type"] or "") == strategy_type]
                strat_eval = [r for r in strat_rows if r["day5_return"] is not None]
                if not strat_eval:
                    continue
                strat_vals = [float(r["day5_return"] or 0.0) for r in strat_eval]
                strat_hits = sum(1 for r in strat_eval if r["correct"])
                strategy_breakdown[strategy_type] = {
                    "evaluated": float(len(strat_eval)),
                    "avg_return_5d": sum(strat_vals) / len(strat_vals),
                    "hit_rate": (strat_hits / len(strat_eval) * 100.0) if strat_eval else 0.0,
                }

            # 최고/최악 매매
            best = max(rows, key=lambda r: r["day5_return"] or -999)
            worst = min(rows, key=lambda r: r["day5_return"] or 999)
            best_text = f"{best['name']} +{(best['day5_return'] or 0):.1f}%"
            worst_text = f"{worst['name']} {(worst['day5_return'] or 0):.1f}%"

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

            if mgr == "swing":
                momentum_stats = strategy_breakdown.get("F", {})
                reversion_stats = strategy_breakdown.get("J", {})
                if momentum_stats.get("evaluated", 0) >= 3 and momentum_stats.get("avg_return_5d", 0.0) >= 1.0:
                    weight *= 1.03
                if reversion_stats.get("evaluated", 0) >= 3:
                    if reversion_stats.get("avg_return_5d", 0.0) < 0:
                        weight *= 0.88
                    if reversion_stats.get("hit_rate", 0.0) < 35:
                        weight *= 0.92
                weight = max(0.75, min(1.25, weight))

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
                "strategy_breakdown": strategy_breakdown,
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

    try:
        shadow_summary = calculate_shadow_portfolio_summary_with_options(
            db,
            days=max(days, 90),
            use_manager_weights=False,
        )
        shadow_manager_returns = shadow_summary.get("manager_avg_returns", {})
        if shadow_manager_returns:
            with db._connect() as conn:
                for mgr, avg_return in shadow_manager_returns.items():
                    card = scorecards.get(mgr)
                    if not card or int(card.get("evaluated", 0) or 0) < 3:
                        continue
                    multiplier = 1.0
                    if avg_return >= 2.0:
                        multiplier = 1.08
                    elif avg_return >= 0.5:
                        multiplier = 1.03
                    elif avg_return < -1.0:
                        multiplier = 0.84
                    elif avg_return < 0:
                        multiplier = 0.92
                    new_weight = round(max(0.75, min(1.25, float(card.get("weight_adj", 1.0)) * multiplier)), 2)
                    card["weight_adj"] = new_weight
                    card["shadow_avg_return_5d"] = round(float(avg_return), 2)
                    conn.execute(
                        """
                        UPDATE manager_scorecard
                        SET weight_adj=?
                        WHERE manager_key=? AND calculated_at=?
                        """,
                        (new_weight, mgr, now_str),
                    )
    except Exception as e:
        logger.debug("shadow portfolio feedback apply failed: %s", e)

    return scorecards


def format_manager_scorecard(scorecards: dict) -> str:
    """매니저 성적표를 텔레그램 메시지로 포맷."""
    lines = [
        "📋 매니저 성적표 (최근 30일)",
        "━" * 22,
    ]
    for mgr, name in _MANAGER_LABELS.items():
        card = scorecards.get(mgr, {})
        total = card.get("total", 0)
        hits = card.get("hits", 0)
        rate = card.get("hit_rate", 0)
        avg5 = card.get("avg_return_5d", 0)
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
        if mgr == "tenbagger":
            avg_score = float(card.get("avg_score", 0) or 0)
            lines.append(f"  추적: {total}종목 | 평균 점수 {avg_score:.0f}/100")
            if card.get("best_trade"):
                lines.append(f"  최상단: {card['best_trade']}")
            lines.append("  역할: 큰 촉매 전 씨앗 포지션 구축")
            continue
        lines.append(f"  추천: {total}건 | 적중: {hits}건 ({rate:.0f}%) [{grade}]")
        lines.append(f"  5일 평균수익: {avg5:+.1f}%")
        if mgr == "swing":
            sub_parts: list[str] = []
            for strategy_type, strat_card in sorted((card.get("strategy_breakdown") or {}).items()):
                sub_parts.append(
                    f"{_STRATEGY_SHORT_LABELS.get(strategy_type, strategy_type)} "
                    f"{float(strat_card.get('avg_return_5d', 0.0)):+.1f}%"
                    f"({float(strat_card.get('hit_rate', 0.0)):.0f}%)"
                )
            if sub_parts:
                lines.append(f"  세부: {' / '.join(sub_parts)}")
        if card.get("shadow_avg_return_5d") is not None:
            lines.append(f"  그림자 검증: {float(card.get('shadow_avg_return_5d', 0.0)):+.1f}%")
        if card.get("best_trade"):
            lines.append(f"  최고: {card['best_trade']}")
        lines.append(f"  가중치: {weight:.2f}x")

    return "\n".join(lines)


def calculate_shadow_portfolio_summary(db, days: int = 90) -> dict[str, Any]:
    """추천 결과를 실제 포트폴리오처럼 묶어 보수적으로 검증."""
    return calculate_shadow_portfolio_summary_with_options(db, days=days, use_manager_weights=True)


def calculate_shadow_portfolio_summary_with_options(
    db,
    days: int = 90,
    *,
    use_manager_weights: bool = True,
) -> dict[str, Any]:
    """추천 결과를 실제 포트폴리오처럼 묶어 보수적으로 검증."""
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.ticker, r.name, r.rec_date, r.rec_score,
                       r.strategy_type, r.manager, r.created_at,
                       rr.day5_return
                FROM recommendations r
                JOIN recommendation_results rr
                  ON rr.recommendation_id = r.id
                WHERE r.created_at >= ?
                  AND rr.day5_return IS NOT NULL
                ORDER BY r.rec_date ASC, r.rec_score DESC, r.created_at ASC
                """,
                (cutoff,),
            ).fetchall()
            weight_rows = conn.execute(
                """
                SELECT manager_key, weight_adj
                FROM manager_scorecard
                WHERE calculated_at IN (
                    SELECT MAX(calculated_at)
                    FROM manager_scorecard
                    GROUP BY manager_key
                )
                """,
            ).fetchall()
    except Exception as e:
        logger.debug("calculate_shadow_portfolio_summary failed: %s", e)
        return {}

    manager_weights = (
        {
            str(row["manager_key"]): float(row["weight_adj"] or 1.0)
            for row in weight_rows
        }
        if use_manager_weights else {}
    )

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        manager_key = str(row["manager"] or "").strip()
        if not manager_key:
            strat = str(row["strategy_type"] or "")
            for candidate_key, cluster in _MANAGER_STRATEGY_CLUSTERS.items():
                if strat in cluster:
                    manager_key = candidate_key
                    break
        normalized_rows.append({
            "ticker": row["ticker"],
            "name": row["name"],
            "rec_date": row["rec_date"] or row["created_at"],
            "created_at": row["created_at"],
            "rec_score": float(row["rec_score"] or 0.0),
            "manager_key": manager_key or "position",
            "day5_return": float(row["day5_return"] or 0.0),
        })

    from kstock.core.performance_tracker import simulate_shadow_portfolio

    summary = simulate_shadow_portfolio(
        normalized_rows,
        manager_weights=manager_weights,
        max_positions=4,
        position_size_pct=20.0,
        cash_buffer_pct=20.0,
        round_trip_cost_pct=0.35,
    )
    return {
        "trades_considered": summary.trades_considered,
        "trades_taken": summary.trades_taken,
        "total_return_pct": summary.total_return_pct,
        "avg_trade_return_pct": summary.avg_trade_return_pct,
        "win_rate_pct": summary.win_rate_pct,
        "max_drawdown_pct": summary.max_drawdown_pct,
        "max_positions": summary.max_positions,
        "position_size_pct": summary.position_size_pct,
        "cash_buffer_pct": summary.cash_buffer_pct,
        "strongest_manager": summary.strongest_manager,
        "weakest_manager": summary.weakest_manager,
        "manager_avg_returns": {
            mgr: round(
                sum(trade.net_return_pct for trade in trades) / len(trades),
                2,
            )
            for mgr in {trade.manager_key for trade in summary.trade_results}
            if (trades := [trade for trade in summary.trade_results if trade.manager_key == mgr])
        },
    }


def format_shadow_portfolio_summary(summary: dict[str, Any]) -> str:
    """그림자 포트폴리오 요약을 텍스트로 포맷."""
    if not summary or int(summary.get("trades_taken", 0) or 0) <= 0:
        return "🎯 그림자 포트폴리오\n  아직 검증할 실전형 데이터가 부족합니다."

    strong = _MANAGER_LABELS.get(
        str(summary.get("strongest_manager") or ""),
        str(summary.get("strongest_manager") or "").strip(),
    )
    weak = _MANAGER_LABELS.get(
        str(summary.get("weakest_manager") or ""),
        str(summary.get("weakest_manager") or "").strip(),
    )

    lines = ["🎯 그림자 포트폴리오 (최근 90일)"]
    lines.append(
        f"  가정: 최대 {int(summary.get('max_positions', 4))}종목 | "
        f"종목당 {float(summary.get('position_size_pct', 20.0)):.0f}% | "
        f"현금 {float(summary.get('cash_buffer_pct', 20.0)):.0f}%"
    )
    lines.append(
        f"  채택: {int(summary.get('trades_taken', 0))}건 / "
        f"검토 {int(summary.get('trades_considered', 0))}건"
    )
    lines.append(
        f"  누적: {float(summary.get('total_return_pct', 0.0)):+.2f}% | "
        f"평균 거래: {float(summary.get('avg_trade_return_pct', 0.0)):+.2f}%"
    )
    lines.append(
        f"  승률: {float(summary.get('win_rate_pct', 0.0)):.1f}% | "
        f"최대낙폭: {float(summary.get('max_drawdown_pct', 0.0)):+.2f}%"
    )
    if strong:
        lines.append(f"  강한 레인: {strong}")
    if weak and weak != strong:
        lines.append(f"  약한 레인: {weak}")
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
            trade_rows = conn.execute(
                """
                SELECT h.ticker, h.name, h.buy_price, h.current_price,
                       h.pnl_pct, h.buy_date, h.updated_at,
                       h.holding_type, h.quantity, h.eval_amount, h.status
                FROM holdings h
                WHERE h.status != 'active' AND h.pnl_pct IS NOT NULL
                ORDER BY h.updated_at DESC
                LIMIT 200
                """,
            ).fetchall()
        trades = [dict(row) for row in trade_rows]

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
            ht = str(t.get("holding_type", "unknown") or "unknown").strip()
            if ht in {"auto", "unknown"}:
                ht = "swing"
            type_counts[ht] = type_counts.get(ht, 0) + 1
        profile["holding_type_distribution"] = type_counts
        if type_counts:
            dominant_style = max(type_counts.items(), key=lambda item: item[1])[0]
        elif profile["avg_hold_days_win"] <= 3:
            dominant_style = "scalper"
        elif profile["avg_hold_days_win"] <= 14:
            dominant_style = "swing"
        elif profile["avg_hold_days_win"] <= 60:
            dominant_style = "position"
        else:
            dominant_style = "long_term"
        profile["dominant_style"] = dominant_style

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

        chat_focus = _load_user_chat_focus(db)
        operator_profile = _build_operator_profile(profile, chat_focus)
        profile["chat_focus"] = chat_focus
        profile["operator_profile"] = operator_profile

        # DB 저장
        _save_profile(db, "trade_stats", json.dumps(profile, ensure_ascii=False), now_str)
        _save_profile(
            db,
            "operator_profile",
            json.dumps(operator_profile, ensure_ascii=False),
            now_str,
        )
        _save_profile(db, "last_analysis", now_str, now_str)

        try:
            from kstock.core.investor_profile import analyze_investor_style

            insight = analyze_investor_style(db)
            db.upsert_investor_profile(
                style=insight.style,
                risk_tolerance=insight.risk_tolerance,
                avg_hold_days=insight.avg_hold_days,
                win_rate=insight.win_rate,
                avg_profit_pct=insight.avg_profit_pct,
                avg_loss_pct=insight.avg_loss_pct,
                trade_count=insight.trade_count,
                notes_json=json.dumps(
                    {
                        "strengths": insight.strengths,
                        "weaknesses": insight.weaknesses,
                        "suggestions": insight.suggestions,
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            logger.debug("Investor profile upsert skipped: %s", e)

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
        adjustment_value = int(adjustment)
        confidence_value = float(confidence)
        sectors_json = json.dumps(list(affected_sectors or []), ensure_ascii=False)
        tickers_json = json.dumps(list(affected_tickers or []), ensure_ascii=False)
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
                    sectors_json,
                    tickers_json,
                    adjustment_value, confidence_value, expires, now_str,
                ),
            )
        logger.info(
            "Event score adjustment saved: %s → %+d for %s",
            event_summary[:50], adjustment_value, affected_sectors,
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
    return analyze_user_trade_patterns(db)


def get_user_operator_profile(db) -> dict:
    """저장된 초개인화 운영자 프로필 조회."""
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT profile_value FROM user_trade_profile "
                "WHERE profile_key = 'operator_profile'",
            ).fetchone()
        if row:
            profile = _safe_json_loads(row["profile_value"])
            if profile:
                return profile
    except Exception:
        pass

    trade_profile = get_user_trade_profile(db)
    operator_profile = trade_profile.get("operator_profile", {})
    if isinstance(operator_profile, dict):
        return operator_profile
    return {}
