"""Debate (AI 토론) DB mixin for K-Quant v9.4.

ai_debates, debate_accuracy 테이블 CRUD 메서드 제공.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


class DebateMixin:
    """AI 토론 결과 저장/조회 mixin."""

    # ── 토론 결과 ────────────────────────────────────────────

    def save_debate(
        self,
        ticker: str,
        name: str,
        verdict: str,
        confidence: float,
        consensus_level: str,
        price_target: float = 0,
        stop_loss: float = 0,
        key_arguments: list[str] | None = None,
        dissenting_view: str = "",
        round1_data: list[dict] | None = None,
        round2_data: list[dict] | None = None,
        pattern_summary: str = "",
        api_calls: int = 0,
    ) -> int:
        """토론 결과 저장.

        Returns:
            inserted row id
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    """INSERT OR REPLACE INTO ai_debates
                    (ticker, name, verdict, confidence, consensus_level,
                     price_target, stop_loss, key_arguments, dissenting_view,
                     round1_data, round2_data, pattern_summary, api_calls, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ticker, name, verdict, confidence, consensus_level,
                        price_target, stop_loss,
                        json.dumps(key_arguments or [], ensure_ascii=False),
                        dissenting_view,
                        json.dumps(round1_data or [], ensure_ascii=False),
                        json.dumps(round2_data or [], ensure_ascii=False),
                        pattern_summary,
                        api_calls,
                        datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                return cur.lastrowid or 0
        except Exception as e:
            logger.error("save_debate error: %s", e, exc_info=True)
            return 0

    def save_debate_result(self, result) -> int:
        """DebateResult 객체를 직접 저장하는 편의 메서드.

        Args:
            result: DebateResult dataclass instance
        """
        r1_data = [
            {
                "manager_key": op.manager_key,
                "manager_name": op.manager_name,
                "action": op.action,
                "confidence": op.confidence,
                "reasoning": op.reasoning,
                "price_target": op.price_target,
                "stop_loss": op.stop_loss,
            }
            for op in (result.round1_opinions or [])
        ]
        r2_data = [
            {
                "manager_key": op.manager_key,
                "manager_name": op.manager_name,
                "action": op.action,
                "confidence": op.confidence,
                "reasoning": op.reasoning,
                "changed": op.changed,
                "previous_action": op.previous_action,
            }
            for op in (result.round2_opinions or [])
        ]

        return self.save_debate(
            ticker=result.ticker,
            name=result.name,
            verdict=result.final_verdict,
            confidence=result.confidence,
            consensus_level=result.consensus_level,
            price_target=result.price_target,
            stop_loss=result.stop_loss,
            key_arguments=result.key_arguments,
            dissenting_view=result.dissenting_view,
            round1_data=r1_data,
            round2_data=r2_data,
            pattern_summary=result.pattern_summary,
            api_calls=result.api_calls,
        )

    def get_latest_debate(self, ticker: str) -> dict | None:
        """최신 토론 결과 조회."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """SELECT * FROM ai_debates
                    WHERE ticker = ?
                    ORDER BY created_at DESC LIMIT 1""",
                    (ticker,),
                ).fetchone()
                if row:
                    d = dict(row)
                    d["key_arguments"] = json.loads(d.get("key_arguments") or "[]")
                    d["round1_data"] = json.loads(d.get("round1_data") or "[]")
                    d["round2_data"] = json.loads(d.get("round2_data") or "[]")
                    return d
        except Exception as e:
            logger.error("get_latest_debate error: %s", e, exc_info=True)
        return None

    def get_debate_history(self, ticker: str, days: int = 30) -> list[dict]:
        """종목 토론 이력 조회."""
        try:
            cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT id, ticker, name, verdict, confidence,
                              consensus_level, price_target, stop_loss,
                              key_arguments, dissenting_view, created_at
                    FROM ai_debates
                    WHERE ticker = ? AND created_at >= ?
                    ORDER BY created_at DESC""",
                    (ticker, cutoff),
                ).fetchall()
                results = []
                for row in rows:
                    d = dict(row)
                    d["key_arguments"] = json.loads(d.get("key_arguments") or "[]")
                    results.append(d)
                return results
        except Exception as e:
            logger.error("get_debate_history error: %s", e, exc_info=True)
            return []

    def get_all_recent_debates(self, hours: int = 24) -> list[dict]:
        """최근 N시간 내 모든 토론 결과 조회."""
        try:
            cutoff = (datetime.now(KST) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT id, ticker, name, verdict, confidence,
                              consensus_level, price_target, stop_loss, created_at
                    FROM ai_debates
                    WHERE created_at >= ?
                    ORDER BY created_at DESC""",
                    (cutoff,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_all_recent_debates error: %s", e, exc_info=True)
            return []

    # ── 예측 정확도 ──────────────────────────────────────────

    def save_debate_accuracy(
        self,
        debate_id: int,
        ticker: str,
        predicted_verdict: str,
        predicted_target: float,
        actual_price_5d: float = 0,
        actual_price_10d: float = 0,
        actual_price_20d: float = 0,
        accuracy_score: float = 0,
    ) -> None:
        """예측 정확도 결과 저장."""
        try:
            now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO debate_accuracy
                    (debate_id, ticker, predicted_verdict, predicted_target,
                     actual_price_5d, actual_price_10d, actual_price_20d,
                     accuracy_score, evaluated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        debate_id, ticker, predicted_verdict, predicted_target,
                        actual_price_5d, actual_price_10d, actual_price_20d,
                        accuracy_score, now,
                    ),
                )
        except Exception as e:
            logger.error("save_debate_accuracy error: %s", e, exc_info=True)

    def get_unevaluated_debates(self, min_age_days: int = 5) -> list[dict]:
        """정확도 미평가 토론 조회 (최소 N일 경과한 건).

        Returns:
            debate_id, ticker, verdict, price_target, created_at 리스트
        """
        try:
            cutoff = (datetime.now(KST) - timedelta(days=min_age_days)).strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT d.id, d.ticker, d.verdict, d.price_target,
                              d.confidence, d.created_at
                    FROM ai_debates d
                    LEFT JOIN debate_accuracy a ON d.id = a.debate_id
                    WHERE d.created_at <= ? AND a.id IS NULL
                    ORDER BY d.created_at ASC
                    LIMIT 20""",
                    (cutoff,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_unevaluated_debates error: %s", e, exc_info=True)
            return []

    def get_prediction_accuracy(self, days: int = 30) -> dict:
        """전체 예측 정확도 통계.

        Returns:
            {total, correct_direction, accuracy_pct, avg_error_pct, ...}
        """
        try:
            cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT a.predicted_verdict, a.predicted_target,
                              a.actual_price_5d, a.actual_price_10d,
                              a.actual_price_20d, a.accuracy_score,
                              d.verdict, d.confidence
                    FROM debate_accuracy a
                    JOIN ai_debates d ON a.debate_id = d.id
                    WHERE a.created_at >= ?""",
                    (cutoff,),
                ).fetchall()

                if not rows:
                    return {"total": 0, "accuracy_pct": 0}

                total = len(rows)
                scores = [r["accuracy_score"] for r in rows if r["accuracy_score"]]
                avg_score = sum(scores) / len(scores) if scores else 0

                return {
                    "total": total,
                    "accuracy_pct": round(avg_score * 100, 1),
                    "avg_accuracy_score": round(avg_score, 3),
                    "evaluated_count": len(scores),
                }
        except Exception as e:
            logger.error("get_prediction_accuracy error: %s", e, exc_info=True)
            return {"total": 0, "accuracy_pct": 0}
