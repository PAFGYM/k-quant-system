"""Historical learning backfill for recommendations, trades, and operator memory."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from kstock.bot.learning_engine import analyze_user_trade_patterns, calculate_manager_scorecard
from kstock.ingest.yfinance_kr_client import YFinanceKRClient

logger = logging.getLogger(__name__)


def _normalize_pairs(ohlcv) -> list[tuple[str, float]]:
    """Convert OHLCV dataframe into sorted (date, close) pairs."""
    try:
        import pandas as pd

        if ohlcv is None or getattr(ohlcv, "empty", True):
            return []
        if "date" in ohlcv.columns:
            dates = pd.to_datetime(ohlcv["date"]).dt.strftime("%Y-%m-%d").tolist()
        else:
            dates = pd.to_datetime(ohlcv.index).strftime("%Y-%m-%d").tolist()
        closes = pd.to_numeric(ohlcv["close"], errors="coerce").tolist()
        pairs = [
            (date_str, float(close))
            for date_str, close in zip(dates, closes)
            if close is not None and not pd.isna(close)
        ]
        return pairs
    except Exception:
        logger.debug("normalize_pairs failed", exc_info=True)
        return []


def _calc_forward_returns(
    pairs: list[tuple[str, float]],
    base_date: str,
) -> dict[str, float]:
    """Calculate forward trading-day prices/returns from a base date."""
    if not pairs:
        return {}
    base_idx = None
    for idx, (date_str, _) in enumerate(pairs):
        if date_str >= base_date:
            base_idx = idx
            break
    if base_idx is None:
        return {}

    base_price = float(pairs[base_idx][1] or 0.0)
    if base_price <= 0:
        return {}

    def nth(day: int) -> tuple[float | None, float | None]:
        target_idx = base_idx + day
        if target_idx >= len(pairs):
            return None, None
        price = float(pairs[target_idx][1] or 0.0)
        if price <= 0:
            return None, None
        ret = round((price - base_price) / base_price * 100.0, 2)
        return price, ret

    result: dict[str, float] = {}
    for day in (1, 3, 5, 10, 20):
        price, ret = nth(day)
        if price is not None:
            result[f"price_d{day}"] = price
        if ret is not None:
            result[f"return_d{day}"] = ret
    if "return_d5" in result:
        result["correct"] = 1 if float(result["return_d5"]) > 0 else 0
    return result


async def _get_ohlcv_with_market_fallback(
    client: YFinanceKRClient,
    ticker: str,
) -> tuple[str, Any]:
    """Try KOSPI first, then KOSDAQ, and return market + dataframe."""
    for market in ("KOSPI", "KOSDAQ"):
        try:
            ohlcv = await client.get_ohlcv(ticker, market=market, period="1y")
            if ohlcv is not None and not getattr(ohlcv, "empty", True):
                return market, ohlcv
        except Exception:
            logger.debug("ohlcv fallback failed for %s/%s", ticker, market, exc_info=True)
    return "", None


async def backfill_historical_recommendations(db, limit: int = 200) -> dict[str, int]:
    """Backfill recommendation D+N results using historical closes."""
    pending = db.get_unevaluated_recommendations(min_days=5)[:limit]
    if not pending:
        return {"pending": 0, "updated": 0, "tracks": 0, "skipped": 0}

    client = YFinanceKRClient()
    cache: dict[str, list[tuple[str, float]]] = {}
    updated = 0
    track_updates = 0
    skipped = 0

    for rec in pending:
        ticker = str(rec.get("ticker") or "").strip()
        rec_date = str(rec.get("rec_date") or "").strip()[:10]
        rec_price = float(rec.get("rec_price") or 0.0)
        if not ticker or not rec_date or rec_price <= 0:
            skipped += 1
            continue

        if ticker not in cache:
            _, ohlcv = await _get_ohlcv_with_market_fallback(client, ticker)
            cache[ticker] = _normalize_pairs(ohlcv)

        returns = _calc_forward_returns(cache.get(ticker, []), rec_date)
        if not returns or "return_d5" not in returns:
            skipped += 1
            continue

        result_id = rec.get("result_id")
        if result_id is None:
            result_id = db.add_recommendation_result(
                recommendation_id=int(rec["id"]),
                ticker=ticker,
                rec_price=rec_price,
                strategy_type=str(rec.get("strategy_type", "A") or "A"),
                regime_at_rec="",
            )

        update_kwargs: dict[str, Any] = {"evaluated_at": datetime.utcnow().isoformat()}
        if rec.get("day5_return") is None and returns.get("return_d5") is not None:
            update_kwargs["day5_price"] = returns.get("price_d5")
            update_kwargs["day5_return"] = returns.get("return_d5")
        if rec.get("day10_return") is None and returns.get("return_d10") is not None:
            update_kwargs["day10_price"] = returns.get("price_d10")
            update_kwargs["day10_return"] = returns.get("return_d10")
        if rec.get("day20_return") is None and returns.get("return_d20") is not None:
            update_kwargs["day20_price"] = returns.get("price_d20")
            update_kwargs["day20_return"] = returns.get("return_d20")
        if returns.get("correct") is not None and rec.get("day5_return") is None:
            update_kwargs["correct"] = int(returns["correct"])

        if len(update_kwargs) > 1:
            db.update_recommendation_result(int(result_id), **update_kwargs)
            updated += 1

        try:
            with db._connect() as conn:
                track = conn.execute(
                    """
                    SELECT id
                    FROM recommendation_tracking
                    WHERE ticker=? AND recommended_date=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (ticker, rec_date),
                ).fetchone()
                if track:
                    conn.execute(
                        """
                        UPDATE recommendation_tracking
                        SET price_d1=COALESCE(?, price_d1),
                            price_d3=COALESCE(?, price_d3),
                            price_d5=COALESCE(?, price_d5),
                            price_d10=COALESCE(?, price_d10),
                            price_d20=COALESCE(?, price_d20),
                            return_d1=COALESCE(?, return_d1),
                            return_d3=COALESCE(?, return_d3),
                            return_d5=COALESCE(?, return_d5),
                            return_d10=COALESCE(?, return_d10),
                            return_d20=COALESCE(?, return_d20),
                            hit=COALESCE(?, hit)
                        WHERE id=?
                        """,
                        (
                            returns.get("price_d1"),
                            returns.get("price_d3"),
                            returns.get("price_d5"),
                            returns.get("price_d10"),
                            returns.get("price_d20"),
                            returns.get("return_d1"),
                            returns.get("return_d3"),
                            returns.get("return_d5"),
                            returns.get("return_d10"),
                            returns.get("return_d20"),
                            returns.get("correct"),
                            int(track["id"]),
                        ),
                    )
                    track_updates += 1
        except Exception:
            logger.debug("recommendation_tracking backfill failed for %s", ticker, exc_info=True)

    return {
        "pending": len(pending),
        "updated": updated,
        "tracks": track_updates,
        "skipped": skipped,
    }


def _save_daily_learning_event_once(
    db,
    event_type: str,
    description: str,
    data: dict[str, Any],
    impact_summary: str,
) -> bool:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with db._connect() as conn:
        existing = conn.execute(
            """
            SELECT 1
            FROM learning_history
            WHERE date=? AND event_type=? AND description=?
            LIMIT 1
            """,
            (today, event_type, description),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """
            INSERT INTO learning_history
                (date, event_type, description, data_json, impact_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                today,
                event_type,
                description,
                json.dumps(data, ensure_ascii=False),
                impact_summary,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return True


def _top_phrases(values: list[str], limit: int = 3) -> list[str]:
    counter = Counter(v.strip() for v in values if str(v or "").strip())
    return [text for text, _ in counter.most_common(limit)]


def backfill_learning_memory(db, days: int = 180) -> dict[str, Any]:
    """Turn older recommendations/trades into reusable learning memory."""
    profile = analyze_user_trade_patterns(db)
    scorecards = calculate_manager_scorecard(db, days=days)
    operator_profile = profile.get("operator_profile", {}) if profile else {}

    saved_events = 0
    manager_events = []
    for mgr, card in scorecards.items():
        if mgr == "tenbagger" or int(card.get("evaluated", 0) or 0) <= 0:
            continue
        manager_events.append(
            {
                "manager": mgr,
                "hit_rate": round(float(card.get("hit_rate", 0.0) or 0.0), 1),
                "avg_return_5d": round(float(card.get("avg_return_5d", 0.0) or 0.0), 2),
                "weight_adj": round(float(card.get("weight_adj", 1.0) or 1.0), 2),
            }
        )

    if manager_events:
        best = max(manager_events, key=lambda item: item["avg_return_5d"])
        worst = min(manager_events, key=lambda item: item["avg_return_5d"])
        if _save_daily_learning_event_once(
            db,
            "historical_manager_scorecard",
            "과거 추천 성과 재학습",
            {"days": days, "managers": manager_events},
            f"강한 레인 {best['manager']} {best['avg_return_5d']:+.1f}% / 약한 레인 {worst['manager']} {worst['avg_return_5d']:+.1f}%",
        ):
            saved_events += 1

        stance_templates = {
            "scalp": "단타 레인은 강한 종목만 짧게 대응, 약한 장세 추격은 금지",
            "swing": "스윙 레인은 눌림 확인 후 접근, 평균회귀 추격은 더 보수적으로",
            "position": "포지션 레인은 실적·수급 확인형 위주로 압축",
            "long_term": "장기 레인은 품질과 현금흐름 중심으로 유지",
            "tenbagger": "텐베거 레인은 씨앗 비중만 유지하고 촉매 전 선점 관점으로 관리",
        }
        focus_text = ", ".join(operator_profile.get("primary_focus", [])[:2]) or "시장/매수"
        for item in manager_events:
            manager_key = str(item["manager"] or "")
            avg_ret = float(item.get("avg_return_5d", 0.0) or 0.0)
            hit_rate = float(item.get("hit_rate", 0.0) or 0.0)
            action = "비중 우대" if avg_ret > 2.0 or hit_rate >= 60 else "보수 운용"
            stance = (
                f"{manager_key}: 최근 180일 5일평균 {avg_ret:+.1f}% / 적중률 {hit_rate:.0f}% · "
                f"{action}. {stance_templates.get(manager_key, '')} "
                f"(주호님 관심축: {focus_text})"
            ).strip()
            try:
                db.save_manager_stance(manager_key, stance[:220])
            except Exception:
                logger.debug("save_manager_stance backfill failed for %s", manager_key, exc_info=True)

    if operator_profile:
        if _save_daily_learning_event_once(
            db,
            "historical_trade_profile",
            "과거 매매 패턴 재학습",
            {
                "total_trades": int(profile.get("total_trades", 0) or 0),
                "win_rate": round(float(profile.get("win_rate", 0.0) or 0.0), 1),
                "dominant_style": operator_profile.get("dominant_style"),
                "strengths": operator_profile.get("strengths", []),
                "risks": operator_profile.get("risks", []),
            },
            operator_profile.get("assistant_brief", ""),
        ):
            saved_events += 1

    try:
        with db._connect() as conn:
            debrief_rows = conn.execute(
                """
                SELECT horizon, grade, lessons_json, mistakes_json, improvements
                FROM trade_debrief
                ORDER BY created_at DESC
                LIMIT 100
                """
            ).fetchall()
    except Exception:
        debrief_rows = []

    if debrief_rows:
        horizons = Counter(str(r["horizon"] or "swing") for r in debrief_rows)
        grades = Counter(str(r["grade"] or "C") for r in debrief_rows)
        lessons: list[str] = []
        mistakes: list[str] = []
        improvements: list[str] = []
        for row in debrief_rows:
            try:
                lessons.extend(json.loads(row["lessons_json"] or "[]"))
            except Exception:
                pass
            try:
                mistakes.extend(json.loads(row["mistakes_json"] or "[]"))
            except Exception:
                pass
            if str(row["improvements"] or "").strip():
                improvements.append(str(row["improvements"]).strip())
        impact = " / ".join(
            part for part in [
                f"주력 {horizons.most_common(1)[0][0]}" if horizons else "",
                f"상위 교훈 {', '.join(_top_phrases(lessons, 2))}" if lessons else "",
                f"상위 실수 {', '.join(_top_phrases(mistakes, 2))}" if mistakes else "",
            ] if part
        )
        if _save_daily_learning_event_once(
            db,
            "historical_trade_debrief",
            "과거 매매 교훈 재학습",
            {
                "grades": dict(grades),
                "horizons": dict(horizons),
                "top_lessons": _top_phrases(lessons, 5),
                "top_mistakes": _top_phrases(mistakes, 5),
                "top_improvements": _top_phrases(improvements, 5),
            },
            impact,
        ):
            saved_events += 1

    try:
        with db._connect() as conn:
            rebalance_rows = conn.execute(
                """
                SELECT trigger_type, action, description, tickers_json, executed
                FROM rebalance_history
                ORDER BY created_at DESC
                LIMIT 50
                """
            ).fetchall()
    except Exception:
        rebalance_rows = []

    if rebalance_rows:
        triggers = Counter(str(r["trigger_type"] or "manual") for r in rebalance_rows)
        actions = Counter(str(r["action"] or "observe") for r in rebalance_rows)
        if _save_daily_learning_event_once(
            db,
            "historical_rebalance",
            "과거 리밸런스 패턴 재학습",
            {
                "trigger_counts": dict(triggers),
                "action_counts": dict(actions),
                "executed": sum(int(r["executed"] or 0) for r in rebalance_rows),
                "recent_descriptions": [str(r["description"] or "") for r in rebalance_rows[:5]],
            },
            f"주요 트리거 {', '.join(f'{k}:{v}' for k, v in triggers.most_common(3))}",
        ):
            saved_events += 1

    return {
        "saved_events": saved_events,
        "profile_trades": int(profile.get("total_trades", 0) or 0) if profile else 0,
        "manager_count": len(manager_events),
    }
