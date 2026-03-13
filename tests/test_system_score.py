from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from kstock.core.system_score import compute_system_score


class DummyScoreDB:
    def __init__(self, db_path):
        self._db_path = str(db_path)
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """
            CREATE TABLE ml_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                pred_date TEXT,
                actual_return REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO ml_predictions (ticker, pred_date, actual_return) VALUES (?, ?, ?)",
            ("005930", datetime.utcnow().strftime("%Y-%m-%d"), None),
        )
        conn.commit()
        conn.close()

    def _connect(self):
        return sqlite3.connect(self._db_path)

    def get_signal_source_stats(self):
        return [{"total": 7, "evaluated": 0, "hits": 0}]

    def get_trade_debriefs(self, limit=50):
        return []

    def get_recent_alerts(self, limit=100):
        return []

    def get_user_preferences(self):
        return []

    def get_recent_enhanced_messages(self, limit=5):
        return []

    def get_recent_manager_stances(self, hours=48):
        return {"scalp": "돌파 대기", "swing": "눌림 대기", "tenbagger": "이벤트 선점"}

    def get_monthly_api_usage(self, year_month=""):
        return {"total_calls": 0, "total_cost": 0, "error_count": 0}

    def get_events(self, limit=100):
        return []

    def get_job_runs(self, run_date):
        return [
            {"status": "success"},
            {"status": "success"},
            {"status": "success"},
            {"status": "error"},
        ]

    def save_system_score(self, **kwargs):
        return None


def test_compute_system_score_treats_unevaluated_signals_as_pending(tmp_path):
    db = DummyScoreDB(tmp_path / "score.db")

    result = compute_system_score(db)

    assert result["signal"] == 10.0
    assert result["details"]["signal"]["msg"] == "평가 대기 중인 신호만 존재"
    assert result["details"]["learning"]["manager_stances"] == 3
    assert result["details"]["learning"]["ml_mature_total"] == 0


def test_compute_system_score_uses_job_runs_when_event_log_is_empty(tmp_path):
    db = DummyScoreDB(tmp_path / "score.db")

    result = compute_system_score(db)

    assert result["details"]["uptime"]["job_success_rate"] == 75.0
    assert result["uptime"] == 7.0
