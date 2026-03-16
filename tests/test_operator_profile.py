from __future__ import annotations

import sqlite3
from datetime import date


class _DummyDB:
    def __init__(self, path: str) -> None:
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def compute_investor_stats(self) -> dict:
        with self._connect() as conn:
            trades = conn.execute(
                "SELECT * FROM holdings WHERE status != 'active'"
            ).fetchall()

        if not trades:
            return {
                "trade_count": 0,
                "win_rate": 0,
                "avg_hold_days": 0,
                "avg_profit_pct": 0,
                "avg_loss_pct": 0,
                "style": "신규",
                "risk_tolerance": "medium",
            }

        wins = 0
        profit_sum = 0.0
        loss_sum = 0.0
        profit_count = 0
        loss_count = 0
        hold_days = 0
        for row in trades:
            pnl = float(row["pnl_pct"] or 0)
            buy = str(row["buy_date"] or "")[:10]
            sell = str(row["updated_at"] or "")[:10]
            if buy and sell:
                hold_days += max((date.fromisoformat(sell) - date.fromisoformat(buy)).days, 0)
            if pnl > 0:
                wins += 1
                profit_sum += pnl
                profit_count += 1
            elif pnl < 0:
                loss_sum += abs(pnl)
                loss_count += 1

        avg_hold = hold_days / len(trades)
        if avg_hold <= 3:
            style = "scalper"
        elif avg_hold <= 14:
            style = "swing"
        elif avg_hold <= 60:
            style = "position"
        else:
            style = "long_term"

        return {
            "trade_count": len(trades),
            "win_rate": round(wins / len(trades) * 100, 1),
            "avg_hold_days": round(avg_hold, 1),
            "avg_profit_pct": round(profit_sum / profit_count, 1) if profit_count else 0,
            "avg_loss_pct": round(loss_sum / loss_count, 1) if loss_count else 0,
            "style": style,
            "risk_tolerance": "medium",
        }

    def get_investor_profile(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM investor_profile WHERE id=1"
            ).fetchone()
        return dict(row) if row else None

    def upsert_investor_profile(self, **kwargs) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM investor_profile WHERE id=1"
            ).fetchone()
            if existing:
                sets = ", ".join(f"{key}=?" for key in kwargs)
                conn.execute(
                    f"UPDATE investor_profile SET {sets} WHERE id=1",
                    list(kwargs.values()),
                )
            else:
                cols = ["id", *kwargs.keys()]
                vals = [1, *kwargs.values()]
                placeholders = ", ".join("?" for _ in vals)
                conn.execute(
                    f"INSERT INTO investor_profile ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )


def _setup_db(path: str) -> _DummyDB:
    db = _DummyDB(path)
    with db._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                name TEXT,
                buy_price REAL,
                current_price REAL,
                pnl_pct REAL,
                buy_date TEXT,
                updated_at TEXT,
                holding_type TEXT,
                quantity REAL,
                eval_amount REAL,
                status TEXT
            );
            CREATE TABLE user_trade_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT UNIQUE NOT NULL,
                profile_value TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE investor_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                style TEXT DEFAULT 'balanced',
                risk_tolerance TEXT DEFAULT 'medium',
                avg_hold_days REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_profit_pct REAL DEFAULT 0,
                avg_loss_pct REAL DEFAULT 0,
                trade_count INTEGER DEFAULT 0,
                leverage_used INTEGER DEFAULT 0,
                preferred_sectors TEXT DEFAULT '',
                notes_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT ''
            );
            CREATE TABLE recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                name TEXT,
                rec_date TEXT,
                rec_price REAL,
                rec_score REAL,
                strategy_type TEXT DEFAULT 'A',
                current_price REAL,
                pnl_pct REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                target_1 REAL,
                target_2 REAL,
                stop_price REAL,
                closed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                manager TEXT DEFAULT ''
            );
            CREATE TABLE recommendation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id INTEGER,
                ticker TEXT,
                rec_price REAL,
                strategy_type TEXT DEFAULT 'A',
                day5_return REAL,
                day10_return REAL,
                day20_return REAL,
                correct INTEGER DEFAULT 0
            );
            CREATE TABLE manager_scorecard (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_key TEXT NOT NULL,
                period TEXT NOT NULL,
                total_recs INTEGER DEFAULT 0,
                evaluated_recs INTEGER DEFAULT 0,
                hits INTEGER DEFAULT 0,
                hit_rate REAL DEFAULT 0.0,
                avg_return_5d REAL DEFAULT 0.0,
                avg_return_10d REAL DEFAULT 0.0,
                avg_return_20d REAL DEFAULT 0.0,
                best_trade TEXT DEFAULT '',
                worst_trade TEXT DEFAULT '',
                weight_adj REAL DEFAULT 1.0,
                calculated_at TEXT NOT NULL
            );
            CREATE TABLE ml_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                pred_date TEXT NOT NULL,
                probability REAL DEFAULT 0,
                actual_return REAL,
                correct INTEGER DEFAULT 0
            );
            CREATE TABLE tenbagger_universe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                name TEXT,
                status TEXT DEFAULT 'active',
                tenbagger_score REAL DEFAULT 0,
                current_return REAL DEFAULT 0,
                updated_at TEXT DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO holdings
            (ticker, name, buy_price, current_price, pnl_pct, buy_date, updated_at, holding_type, quantity, eval_amount, status)
            VALUES
            ('086520', '에코프로', 90700, 152000, 0.6721, '2026-03-01', '2026-03-10T09:00:00', 'long_term', 10, 1520000, 'sold'),
            ('005930', '삼성전자', 82000, 79000, -0.0365, '2026-03-05', '2026-03-08T09:00:00', 'swing', 10, 790000, 'sold')
            """
        )
        conn.execute(
            """
            INSERT INTO chat_history (role, content, created_at)
            VALUES
            ('user', '오늘 시장 분석하고 보유 종목 매도 타이밍도 봐줘', '2026-03-10T07:00:00'),
            ('user', '지금 매수할 종목 추천해줘', '2026-03-10T08:00:00'),
            ('assistant', 'ignored', '2026-03-10T08:05:00')
            """
        )
        conn.execute(
            """
            INSERT INTO recommendations
            (ticker, name, rec_date, rec_price, rec_score, strategy_type, created_at, updated_at, manager)
            VALUES
            ('005930', '삼성전자', '2026-03-10', 79000, 74, 'A', '2026-03-10T08:00:00', '2026-03-10T08:00:00', ''),
            ('086520', '에코프로', '2026-03-10', 150000, 81, 'C', '2026-03-10T08:00:00', '2026-03-10T08:00:00', '')
            """
        )
        conn.execute(
            """
            INSERT INTO recommendation_results
            (recommendation_id, ticker, rec_price, strategy_type, day5_return, day10_return, day20_return, correct)
            VALUES
            (1, '005930', 79000, 'A', 5.0, 6.0, 8.0, 1),
            (2, '086520', 150000, 'C', 2.0, 3.0, 5.0, 1)
            """
        )
        conn.execute(
            """
            INSERT INTO tenbagger_universe (ticker, name, status, tenbagger_score, current_return, updated_at)
            VALUES ('083650', '비에이치아이', 'active', 88.5, 0.0, '2026-03-10T08:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO ml_predictions (ticker, pred_date, probability, actual_return, correct)
            VALUES
            ('005930', '2026-03-10', 0.72, NULL, 0),
            ('000660', '2026-03-10', 0.68, NULL, 0),
            ('005930', '2026-03-11', 0.58, NULL, 0),
            ('000660', '2026-03-12', 0.44, NULL, 0),
            ('005930', '2026-03-13', 0.66, NULL, 0)
            """
        )
    return db


def test_analyze_user_trade_patterns_reads_sold_history_and_saves_operator_profile(tmp_path) -> None:
    from kstock.bot.learning_engine import (
        analyze_user_trade_patterns,
        get_user_operator_profile,
        get_user_trade_profile,
    )

    db = _setup_db(str(tmp_path / "profile.db"))

    profile = analyze_user_trade_patterns(db)

    assert profile["total_trades"] == 2
    assert profile["win_count"] == 1
    assert profile["loss_count"] == 1
    assert "operator_profile" in profile

    trade_profile = get_user_trade_profile(db)
    assert trade_profile["total_trades"] == 2

    operator_profile = get_user_operator_profile(db)
    assert "시장" in operator_profile["primary_focus"]
    assert any(topic in operator_profile["primary_focus"] for topic in ("매수", "매도", "보유"))
    assert operator_profile["dominant_style_label"] in {"장기", "스윙", "포지션", "단타"}
    assert "assistant_brief" in operator_profile

    investor_profile = db.get_investor_profile()
    assert investor_profile is not None
    assert investor_profile["trade_count"] == 2


def test_calculate_manager_scorecard_falls_back_to_strategy_clusters_and_tenbagger(tmp_path) -> None:
    from kstock.bot.learning_engine import calculate_manager_scorecard, format_manager_scorecard

    db = _setup_db(str(tmp_path / "scorecard.db"))
    scorecards = calculate_manager_scorecard(db, days=30)

    assert scorecards["scalp"]["total"] == 1
    assert scorecards["scalp"]["hit_rate"] == 100.0
    assert scorecards["long_term"]["total"] == 1
    assert scorecards["tenbagger"]["total"] == 1
    assert round(scorecards["tenbagger"]["avg_score"], 1) == 88.5

    text = format_manager_scorecard(scorecards)
    assert "5일 평균수익: +5.0%" in text
    assert "최고: 삼성전자 +5.0%" in text


def test_shadow_portfolio_summary_uses_recommendation_results(tmp_path) -> None:
    from kstock.bot.learning_engine import (
        calculate_manager_scorecard,
        calculate_shadow_portfolio_summary,
        format_shadow_portfolio_summary,
    )

    db = _setup_db(str(tmp_path / "shadow.db"))
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO recommendations
            (ticker, name, rec_date, rec_price, rec_score, strategy_type, created_at, updated_at, manager)
            VALUES
            ('111111', '스윙주', '2026-03-01', 10000, 78, 'F', '2026-03-01T08:00:00', '2026-03-01T08:00:00', ''),
            ('222222', '장기주', '2026-03-02', 20000, 81, 'C', '2026-03-02T08:00:00', '2026-03-02T08:00:00', ''),
            ('333333', '포지션주', '2026-03-03', 15000, 76, 'D', '2026-03-03T08:00:00', '2026-03-03T08:00:00', '')
            """
        )
        conn.execute(
            """
            INSERT INTO recommendation_results
            (recommendation_id, ticker, rec_price, strategy_type, day5_return, day10_return, day20_return, correct)
            VALUES
            (3, '111111', 10000, 'F', -2.0, NULL, NULL, 0),
            (4, '222222', 20000, 'C', 4.0, NULL, NULL, 1),
            (5, '333333', 15000, 'D', 3.0, NULL, NULL, 1)
            """
        )

    calculate_manager_scorecard(db, days=30)
    summary = calculate_shadow_portfolio_summary(db, days=90)

    assert summary["trades_considered"] >= 5
    assert summary["trades_taken"] >= 3
    assert summary["total_return_pct"] != 0
    assert summary["strongest_manager"] in {"long_term", "position", "scalp", "swing"}

    text = format_shadow_portfolio_summary(summary)
    assert "그림자 포트폴리오" in text
    assert "누적:" in text


def test_format_learning_impact_snapshot_shows_actual_behavior_change(tmp_path) -> None:
    from kstock.bot.learning_engine import (
        analyze_user_trade_patterns,
        calculate_manager_scorecard,
        format_learning_impact_snapshot,
    )

    db = _setup_db(str(tmp_path / "impact.db"))
    analyze_user_trade_patterns(db)
    calculate_manager_scorecard(db, days=30)

    with db._connect() as conn:
        conn.execute(
            """
            CREATE TABLE learning_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                event_type TEXT NOT NULL,
                description TEXT DEFAULT '',
                data_json TEXT DEFAULT '{}',
                impact_summary TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO learning_history
            (date, event_type, description, impact_summary, created_at)
            VALUES
            ('2026-03-14', 'market_regime', '시장 레짐: bear (점수 -23, 신뢰도 65%)', '', '2026-03-14T09:00:00'),
            ('2026-03-14', 'market_rotation_pattern', '시장 내부 로테이션 재학습', '코스피-코스닥 디커플링 | 반도체 SK하이닉스 +7.0% / 원전 우진 -6.2%', '2026-03-14T09:03:00'),
            ('2026-03-14', 'historical_trade_debrief', '과거 매매 교훈 재학습', '수익 구간에서 분할 매도 전략 유지', '2026-03-14T09:05:00')
            """
        )

    text = format_learning_impact_snapshot(db)

    assert "학습으로 바뀐 것" in text
    assert "개인화:" in text
    assert ("강화:" in text) or ("보수화:" in text)
    assert "추천 변화:" in text
    assert "최근 근거:" in text
    assert "코스피-코스닥 디커플링" in text


def test_format_ml_progress_snapshot_shows_stage_progress(tmp_path) -> None:
    from kstock.bot.learning_engine import format_ml_progress_snapshot, get_ml_progress_snapshot

    db = _setup_db(str(tmp_path / "ml_progress.db"))

    snapshot = get_ml_progress_snapshot(db)
    assert snapshot["total_predictions"] == 5
    assert snapshot["d1_ready"] == 4
    assert snapshot["d3_ready"] == 2
    assert snapshot["d5_ready"] == 0

    text = format_ml_progress_snapshot(db)
    assert "ML 진행 성적표" in text
    assert "중간 도달" in text
    assert "D+1 4건" in text
    assert "D+3 2건" in text
