"""SQLite store for metadata, portfolio, holdings, watchlist, and job watermarks."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator


DEFAULT_DB_PATH = Path("data/kquant.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS job_runs (
    job_name   TEXT    NOT NULL,
    run_date   TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'success',
    started_at TEXT    NOT NULL,
    ended_at   TEXT,
    message    TEXT,
    PRIMARY KEY (job_name, run_date)
);

CREATE TABLE IF NOT EXISTS portfolio (
    ticker     TEXT    NOT NULL,
    name       TEXT,
    score      REAL,
    signal     TEXT,
    sell_code  TEXT,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (ticker)
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT    NOT NULL,
    alert_type TEXT    NOT NULL,
    message    TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    buy_price     REAL    NOT NULL,
    current_price REAL,
    buy_date      TEXT    NOT NULL,
    target_1      REAL,
    target_2      REAL,
    stop_price    REAL,
    status        TEXT    DEFAULT 'active',
    sold_pct      REAL    DEFAULT 0,
    pnl_pct       REAL    DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    ticker       TEXT    PRIMARY KEY,
    name         TEXT    NOT NULL,
    target_price REAL,
    target_rsi   REAL    DEFAULT 30,
    active       INTEGER DEFAULT 1,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    name              TEXT    NOT NULL,
    strategy_type     TEXT    DEFAULT 'A',
    action            TEXT    NOT NULL,
    recommended_price REAL,
    action_price      REAL,
    quantity_pct      REAL    DEFAULT 0,
    pnl_pct           REAL    DEFAULT 0,
    recommendation_id INTEGER,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    rec_date      TEXT    NOT NULL,
    rec_price     REAL    NOT NULL,
    rec_score     REAL    NOT NULL,
    strategy_type TEXT    DEFAULT 'A',
    sell_reason   TEXT,
    current_price REAL,
    pnl_pct       REAL    DEFAULT 0,
    status        TEXT    DEFAULT 'active',
    target_1      REAL,
    target_2      REAL,
    stop_price    REAL,
    closed_at     TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS screenshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    image_hash      TEXT,
    recognized_at   TEXT    NOT NULL,
    total_eval      REAL    DEFAULT 0,
    total_profit    REAL    DEFAULT 0,
    total_profit_pct REAL   DEFAULT 0,
    cash            REAL    DEFAULT 0,
    portfolio_score INTEGER DEFAULT 0,
    holdings_json   TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS screenshot_holdings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    screenshot_id   INTEGER NOT NULL,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    quantity        INTEGER DEFAULT 0,
    avg_price       REAL    DEFAULT 0,
    current_price   REAL    DEFAULT 0,
    profit_pct      REAL    DEFAULT 0,
    eval_amount     REAL    DEFAULT 0,
    diagnosis       TEXT,
    diagnosis_action TEXT,
    diagnosis_msg   TEXT,
    FOREIGN KEY (screenshot_id) REFERENCES screenshots(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    order_type    TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    quantity      INTEGER NOT NULL,
    price         REAL,
    order_id      TEXT,
    status        TEXT    DEFAULT 'pending',
    filled_price  REAL,
    filled_at     TEXT,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    pred_date     TEXT    NOT NULL,
    probability   REAL    NOT NULL,
    actual_return REAL,
    correct       INTEGER,
    shap_top3     TEXT,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    analysis_date   TEXT    NOT NULL,
    positive_pct    REAL    DEFAULT 0,
    negative_pct    REAL    DEFAULT 0,
    neutral_pct     REAL    DEFAULT 0,
    headline_count  INTEGER DEFAULT 0,
    summary         TEXT,
    score_bonus     INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL,
    ticker          TEXT    NOT NULL,
    rec_price       REAL    NOT NULL,
    strategy_type   TEXT    DEFAULT 'A',
    regime_at_rec   TEXT,
    day5_price      REAL,
    day5_return     REAL,
    day10_price     REAL,
    day10_return    REAL,
    day20_price     REAL,
    day20_return    REAL,
    correct         INTEGER,
    evaluated_at    TEXT,
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
);

CREATE TABLE IF NOT EXISTS feedback_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT    NOT NULL,
    period_days     INTEGER DEFAULT 7,
    total_recs      INTEGER DEFAULT 0,
    hits            INTEGER DEFAULT 0,
    misses          INTEGER DEFAULT 0,
    pending         INTEGER DEFAULT 0,
    hit_rate        REAL    DEFAULT 0,
    avg_return      REAL    DEFAULT 0,
    lessons_json    TEXT,
    strategy_json   TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT    NOT NULL,
    total_asset     REAL    NOT NULL,
    cash            REAL    DEFAULT 0,
    positions_count INTEGER DEFAULT 0,
    daily_return    REAL    DEFAULT 0,
    milestone       TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tenbagger_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    found_date      TEXT    NOT NULL,
    price_at_found  REAL    NOT NULL,
    conditions_met  INTEGER DEFAULT 0,
    conditions_json TEXT,
    status          TEXT    DEFAULT 'monitoring',
    current_return  REAL    DEFAULT 0,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS swing_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    entry_date      TEXT    NOT NULL,
    entry_price     REAL    NOT NULL,
    target_price    REAL,
    stop_price      REAL,
    exit_date       TEXT,
    exit_price      REAL,
    pnl_pct         REAL    DEFAULT 0,
    status          TEXT    DEFAULT 'active',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- v3.5 tables -----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    broker            TEXT    NOT NULL,
    ticker            TEXT,
    target_price      REAL,
    prev_target_price REAL,
    opinion           TEXT,
    prev_opinion      TEXT,
    date              TEXT    NOT NULL,
    pdf_url           TEXT,
    summary           TEXT,
    created_at        TEXT    NOT NULL,
    UNIQUE(title, broker, date)
);

CREATE TABLE IF NOT EXISTS consensus (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    name              TEXT,
    avg_target_price  REAL,
    current_price     REAL,
    upside_pct        REAL,
    buy_count         INTEGER DEFAULT 0,
    hold_count        INTEGER DEFAULT 0,
    sell_count         INTEGER DEFAULT 0,
    target_trend      TEXT,
    target_trend_pct  REAL    DEFAULT 0,
    score_bonus       INTEGER DEFAULT 0,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS earnings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    name              TEXT,
    period            TEXT    NOT NULL,
    earnings_date     TEXT,
    revenue           REAL,
    revenue_consensus REAL,
    operating_income  REAL,
    op_income_consensus REAL,
    op_margin         REAL,
    prev_op_margin    REAL,
    surprise_pct      REAL,
    verdict           TEXT,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS financials (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    name              TEXT,
    period            TEXT,
    revenue           REAL,
    operating_income  REAL,
    net_income        REAL,
    op_margin         REAL,
    roe               REAL,
    roa               REAL,
    debt_ratio        REAL,
    current_ratio     REAL,
    per               REAL,
    pbr               REAL,
    eps               REAL,
    bps               REAL,
    dps               REAL,
    fcf               REAL,
    ebitda            REAL,
    score_total       INTEGER DEFAULT 0,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS supply_demand (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    foreign_net       REAL    DEFAULT 0,
    institution_net   REAL    DEFAULT 0,
    retail_net        REAL    DEFAULT 0,
    program_net       REAL    DEFAULT 0,
    short_balance     REAL    DEFAULT 0,
    short_ratio       REAL    DEFAULT 0,
    created_at        TEXT    NOT NULL,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    role              TEXT    NOT NULL,
    content           TEXT    NOT NULL,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_usage (
    date              TEXT    PRIMARY KEY,
    count             INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS macro_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    date              TEXT    NOT NULL,
    name              TEXT    NOT NULL,
    country           TEXT,
    importance        TEXT    DEFAULT 'booting',
    description       TEXT,
    actual_value      TEXT,
    expected_value    TEXT,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS investment_horizons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    horizon         TEXT    NOT NULL DEFAULT 'default',
    screenshot_id   INTEGER,
    stop_pct        REAL,
    target_pct      REAL,
    trailing_pct    REAL,
    is_margin       INTEGER DEFAULT 0,
    margin_type     TEXT,
    diagnosis       TEXT,
    diagnosis_action TEXT,
    diagnosis_msg   TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS notification_settings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_name    TEXT    NOT NULL UNIQUE,
    enabled         INTEGER DEFAULT 1,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_horizon (
    ticker          TEXT    PRIMARY KEY,
    name            TEXT,
    horizon         TEXT    NOT NULL DEFAULT 'dangi',
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_label      TEXT    NOT NULL,
    week_start      TEXT    NOT NULL,
    week_end        TEXT    NOT NULL,
    doc_url         TEXT,
    summary_json    TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS short_selling (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT    NOT NULL,
    date                TEXT    NOT NULL,
    short_volume        INTEGER DEFAULT 0,
    total_volume        INTEGER DEFAULT 0,
    short_ratio         REAL    DEFAULT 0,
    short_balance       INTEGER DEFAULT 0,
    short_balance_ratio REAL    DEFAULT 0,
    created_at          TEXT    NOT NULL,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS inverse_etf (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT,
    sector          TEXT,
    date            TEXT    NOT NULL,
    volume          INTEGER DEFAULT 0,
    price           REAL    DEFAULT 0,
    change_pct      REAL    DEFAULT 0,
    created_at      TEXT    NOT NULL,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS margin_balance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT    NOT NULL,
    date                TEXT    NOT NULL,
    credit_buy          INTEGER DEFAULT 0,
    credit_sell         INTEGER DEFAULT 0,
    credit_balance      INTEGER DEFAULT 0,
    credit_ratio        REAL    DEFAULT 0,
    collateral_balance  INTEGER DEFAULT 0,
    created_at          TEXT    NOT NULL,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS margin_thresholds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    metric          TEXT    NOT NULL,
    mean_60d        REAL    DEFAULT 0,
    std_60d         REAL    DEFAULT 0,
    upper_1sigma    REAL    DEFAULT 0,
    lower_1sigma    REAL    DEFAULT 0,
    upper_2sigma    REAL    DEFAULT 0,
    lower_2sigma    REAL    DEFAULT 0,
    updated_at      TEXT    NOT NULL,
    UNIQUE(ticker, metric)
);

CREATE TABLE IF NOT EXISTS rebalance_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type    TEXT    NOT NULL,
    description     TEXT,
    action          TEXT,
    tickers_json    TEXT,
    executed        INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS solution_tracking (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_type     TEXT    NOT NULL,
    description       TEXT,
    suggested_date    TEXT    NOT NULL,
    executed          INTEGER DEFAULT 0,
    before_snapshot_id INTEGER,
    after_snapshot_id  INTEGER,
    profit_change_pct  REAL   DEFAULT 0,
    alpha_change       REAL   DEFAULT 0,
    created_at         TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_violations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    violation_type  TEXT    NOT NULL,
    severity        TEXT    NOT NULL DEFAULT 'medium',
    description     TEXT,
    recommended_action TEXT,
    action_taken    TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    total_value     REAL    DEFAULT 0,
    cash            REAL    DEFAULT 0,
    holdings_count  INTEGER DEFAULT 0,
    daily_pnl_pct   REAL    DEFAULT 0,
    total_pnl_pct   REAL    DEFAULT 0,
    mdd             REAL    DEFAULT 0,
    peak_value      REAL    DEFAULT 0,
    kospi_close     REAL    DEFAULT 0,
    kosdaq_close    REAL    DEFAULT 0,
    holdings_json   TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    strategy        TEXT    NOT NULL DEFAULT 'A',
    score           REAL    DEFAULT 0,
    recommended_date TEXT   NOT NULL,
    entry_price     REAL    NOT NULL,
    price_d1        REAL,
    price_d3        REAL,
    price_d5        REAL,
    price_d10       REAL,
    price_d20       REAL,
    return_d1       REAL,
    return_d3       REAL,
    return_d5       REAL,
    return_d10      REAL,
    return_d20      REAL,
    hit             INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS ml_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    model_version   TEXT,
    train_score     REAL    DEFAULT 0,
    val_score       REAL    DEFAULT 0,
    overfit_gap     REAL    DEFAULT 0,
    features_used   INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS hallucination_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    query           TEXT,
    response_preview TEXT,
    verified_count  INTEGER DEFAULT 0,
    unverified_count INTEGER DEFAULT 0,
    unverified_claims TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    direction       TEXT    NOT NULL DEFAULT 'buy',
    quantity        INTEGER DEFAULT 0,
    price           REAL    DEFAULT 0,
    amount          REAL    DEFAULT 0,
    commission      REAL    DEFAULT 0,
    strategy        TEXT,
    score           REAL    DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    is_admin        INTEGER DEFAULT 0,
    config_json     TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS future_watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    sector          TEXT    NOT NULL,
    tier            TEXT    NOT NULL,
    future_score    INTEGER DEFAULT 0,
    tech_maturity   INTEGER DEFAULT 0,
    financial_stability INTEGER DEFAULT 0,
    policy_benefit  INTEGER DEFAULT 0,
    momentum        INTEGER DEFAULT 0,
    valuation       INTEGER DEFAULT 0,
    entry_signal    TEXT    DEFAULT 'WAIT',
    updated_at      TEXT    NOT NULL,
    UNIQUE(ticker)
);

CREATE TABLE IF NOT EXISTS future_triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sector          TEXT    NOT NULL,
    trigger_type    TEXT    NOT NULL,
    impact          TEXT    DEFAULT 'LOW',
    title           TEXT    NOT NULL,
    source          TEXT,
    matched_keywords TEXT,
    beneficiary_tickers TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS seed_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    sector          TEXT    NOT NULL,
    tier            TEXT    NOT NULL,
    avg_price       REAL    DEFAULT 0,
    quantity        INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'active',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE(ticker)
);

-- v3.5-phase7 tables ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS strategy_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT    NOT NULL,
    period          TEXT    NOT NULL,
    total_count     INTEGER DEFAULT 0,
    win_count       INTEGER DEFAULT 0,
    win_rate        REAL    DEFAULT 0,
    avg_return      REAL    DEFAULT 0,
    calculated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS surge_stocks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT,
    scan_time       TEXT,
    change_pct      REAL    DEFAULT 0,
    volume_ratio    REAL    DEFAULT 0,
    triggers        TEXT,
    market_cap      REAL    DEFAULT 0,
    health_grade    TEXT,
    health_score    INTEGER DEFAULT 0,
    health_reasons  TEXT,
    ai_analysis     TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS stealth_accumulations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT,
    total_score     INTEGER DEFAULT 0,
    patterns_json   TEXT,
    price_change_20d REAL   DEFAULT 0,
    inst_total      REAL    DEFAULT 0,
    foreign_total   REAL    DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_registers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    quantity        INTEGER DEFAULT 0,
    price           REAL    DEFAULT 0,
    total_amount    REAL    DEFAULT 0,
    source          TEXT    DEFAULT 'text',
    horizon         TEXT    DEFAULT 'swing',
    trailing_stop_pct REAL  DEFAULT 0.05,
    target_profit_pct REAL  DEFAULT 0.10,
    status          TEXT    DEFAULT 'active',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS multi_agent_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT,
    technical_score INTEGER DEFAULT 0,
    fundamental_score INTEGER DEFAULT 0,
    sentiment_score INTEGER DEFAULT 0,
    combined_score  INTEGER DEFAULT 0,
    verdict         TEXT,
    confidence      TEXT,
    strategist_summary TEXT,
    created_at      TEXT    NOT NULL
);

-- v3.5-phase8 tables ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS macro_cache (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_json   TEXT    NOT NULL,
    ai_summary      TEXT    DEFAULT '',
    ai_summary_at   TEXT,
    fetched_at      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

-- v3.5-phase9: 투자자 프로필 & 학습 시스템 -----------------------------------

CREATE TABLE IF NOT EXISTS investor_profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    style           TEXT    DEFAULT 'balanced',
    risk_tolerance  TEXT    DEFAULT 'medium',
    avg_hold_days   REAL    DEFAULT 0,
    win_rate        REAL    DEFAULT 0,
    avg_profit_pct  REAL    DEFAULT 0,
    avg_loss_pct    REAL    DEFAULT 0,
    trade_count     INTEGER DEFAULT 0,
    leverage_used   INTEGER DEFAULT 0,
    preferred_sectors TEXT  DEFAULT '',
    notes_json      TEXT    DEFAULT '{}',
    updated_at      TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS holding_analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    holding_id      INTEGER NOT NULL,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    hold_type       TEXT    NOT NULL DEFAULT 'swing',
    hold_days       INTEGER DEFAULT 0,
    leverage_flag   INTEGER DEFAULT 0,
    sector          TEXT    DEFAULT '',
    ai_analysis     TEXT    DEFAULT '',
    ai_suggestion   TEXT    DEFAULT '',
    last_alert_at   TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE(holding_id)
);

CREATE TABLE IF NOT EXISTS trade_lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    pnl_pct         REAL    DEFAULT 0,
    hold_days       INTEGER DEFAULT 0,
    lesson          TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL
);
"""


class SQLiteStore:
    """Thin wrapper around SQLite for K-Quant metadata."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Migrate: add strategy_type column if missing
            try:
                conn.execute("SELECT strategy_type FROM recommendations LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    conn.execute(
                        "ALTER TABLE recommendations ADD COLUMN strategy_type TEXT DEFAULT 'A'"
                    )
                except sqlite3.OperationalError:
                    pass
            # Migrate: add margin columns to screenshot_holdings
            try:
                conn.execute("SELECT is_margin FROM screenshot_holdings LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    conn.execute(
                        "ALTER TABLE screenshot_holdings ADD COLUMN is_margin INTEGER DEFAULT 0"
                    )
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("SELECT margin_type FROM screenshot_holdings LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    conn.execute(
                        "ALTER TABLE screenshot_holdings ADD COLUMN margin_type TEXT"
                    )
                except sqlite3.OperationalError:
                    pass
            # Migrate: add quantity/eval_amount to holdings table (v3.5.1)
            for col, sql in [
                ("quantity", "ALTER TABLE holdings ADD COLUMN quantity INTEGER DEFAULT 0"),
                ("eval_amount", "ALTER TABLE holdings ADD COLUMN eval_amount REAL DEFAULT 0"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM holdings LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass

    # -- job_runs ---------------------------------------------------------------

    def upsert_job_run(
        self,
        job_name: str,
        run_date: str,
        status: str = "success",
        started_at: str | None = None,
        ended_at: str | None = None,
        message: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_runs (job_name, run_date, status, started_at, ended_at, message)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_name, run_date) DO UPDATE SET
                    status=excluded.status, ended_at=excluded.ended_at, message=excluded.message
                """,
                (job_name, run_date, status, started_at or now, ended_at or now, message),
            )

    def get_last_job_run(self, job_name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_runs WHERE job_name=? ORDER BY run_date DESC LIMIT 1",
                (job_name,),
            ).fetchone()
        return dict(row) if row else None

    def get_job_runs(self, run_date: str) -> list[dict]:
        """특정 날짜의 모든 잡 실행 기록 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs WHERE run_date=? ORDER BY started_at DESC",
                (run_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- portfolio --------------------------------------------------------------

    def upsert_portfolio(
        self,
        ticker: str,
        name: str | None = None,
        score: float | None = None,
        signal: str | None = None,
        sell_code: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio (ticker, name, score, signal, sell_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=COALESCE(excluded.name, portfolio.name),
                    score=COALESCE(excluded.score, portfolio.score),
                    signal=COALESCE(excluded.signal, portfolio.signal),
                    sell_code=COALESCE(excluded.sell_code, portfolio.sell_code),
                    updated_at=excluded.updated_at
                """,
                (ticker, name, score, signal, sell_code, now),
            )

    def get_portfolio(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM portfolio ORDER BY score DESC").fetchall()
        return [dict(r) for r in rows]

    def get_portfolio_entry(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio WHERE ticker=?", (ticker,)
            ).fetchone()
        return dict(row) if row else None

    # -- alerts -----------------------------------------------------------------

    def insert_alert(self, ticker: str, alert_type: str, message: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alerts (ticker, alert_type, message, created_at) VALUES (?,?,?,?)",
                (ticker, alert_type, message, now),
            )

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def has_recent_alert(self, ticker: str, alert_type: str, hours: int = 4) -> bool:
        """Check if a similar alert was sent recently (spam prevention)."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE ticker=? AND alert_type=? AND created_at>?",
                (ticker, alert_type, cutoff),
            ).fetchone()
        return dict(row)["cnt"] > 0

    # -- holdings ---------------------------------------------------------------

    def add_holding(self, ticker: str, name: str, buy_price: float) -> int:
        now = datetime.utcnow().isoformat()
        target_1 = round(buy_price * 1.03, 0)
        target_2 = round(buy_price * 1.07, 0)
        stop_price = round(buy_price * 0.95, 0)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO holdings
                    (ticker, name, buy_price, current_price, buy_date,
                     target_1, target_2, stop_price, status, sold_pct, pnl_pct,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?)
                """,
                (ticker, name, buy_price, buy_price, now[:10],
                 target_1, target_2, stop_price, now, now),
            )
            return cursor.lastrowid

    def get_active_holdings(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM holdings WHERE status='active' ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_holding(self, holding_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE id=?", (holding_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_holding_by_ticker(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE ticker=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_holding_by_name(self, name: str) -> dict | None:
        """종목명으로 active 보유종목 조회 (ticker 없을 때 fallback)."""
        if not name:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE name=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_holding(
        self,
        ticker: str,
        name: str,
        quantity: int = 0,
        buy_price: float = 0,
        current_price: float = 0,
        pnl_pct: float = 0,
        eval_amount: float = 0,
    ) -> int:
        """스크린샷에서 파싱한 보유종목을 holdings DB에 upsert.

        이미 active인 동일 종목이 있으면 현재가/수익률만 업데이트,
        없으면 신규 등록.
        ticker가 비어있으면 name으로 조회.
        """
        # ticker가 있으면 ticker로, 없으면 name으로 조회
        existing = None
        if ticker:
            existing = self.get_holding_by_ticker(ticker)
        if not existing and name:
            existing = self.get_holding_by_name(name)
        now = datetime.utcnow().isoformat()
        if existing:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE holdings SET
                        current_price=?, pnl_pct=?, quantity=?,
                        eval_amount=?, name=?, updated_at=?
                    WHERE id=?""",
                    (current_price, pnl_pct, quantity, eval_amount,
                     name, now, existing["id"]),
                )
            return existing["id"]
        else:
            target_1 = round(buy_price * 1.03, 0) if buy_price else 0
            target_2 = round(buy_price * 1.07, 0) if buy_price else 0
            stop_price = round(buy_price * 0.95, 0) if buy_price else 0
            with self._connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO holdings
                        (ticker, name, buy_price, current_price, quantity,
                         eval_amount, buy_date, target_1, target_2, stop_price,
                         status, sold_pct, pnl_pct, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)""",
                    (ticker, name, buy_price, current_price, quantity,
                     eval_amount, now[:10], target_1, target_2, stop_price,
                     pnl_pct, now, now),
                )
                return cursor.lastrowid

    def update_holding(self, holding_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(holding_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE holdings SET {', '.join(sets)} WHERE id=?", vals
            )

    # -- watchlist --------------------------------------------------------------

    def add_watchlist(
        self,
        ticker: str,
        name: str,
        target_price: float | None = None,
        target_rsi: float = 30,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO watchlist
                    (ticker, name, target_price, target_rsi, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (ticker, name, target_price, target_rsi, now),
            )

    def get_watchlist(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM watchlist WHERE active=1").fetchall()
        return [dict(r) for r in rows]

    def remove_watchlist(self, ticker: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE watchlist SET active=0 WHERE ticker=?", (ticker,))

    # -- recommendations -------------------------------------------------------

    def add_recommendation(
        self,
        ticker: str,
        name: str,
        rec_price: float,
        rec_score: float,
        status: str = "active",
        sell_reason: str | None = None,
        strategy_type: str = "A",
        target_pct: float = 3.0,
        stop_pct: float = -5.0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        target_1 = round(rec_price * (1 + target_pct / 100), 0)
        target_2 = round(rec_price * (1 + target_pct * 2 / 100), 0)
        stop_price = round(rec_price * (1 + stop_pct / 100), 0)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO recommendations
                    (ticker, name, rec_date, rec_price, rec_score, strategy_type,
                     sell_reason, current_price, pnl_pct, status,
                     target_1, target_2, stop_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, now[:10], rec_price, rec_score, strategy_type,
                 sell_reason, rec_price, status, target_1, target_2, stop_price,
                 now, now),
            )
            return cursor.lastrowid

    def get_active_recommendations(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE status='active' ORDER BY rec_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_completed_recommendations(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE status IN ('profit', 'stop') "
                "ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_watch_recommendations(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE status='watch' ORDER BY rec_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_recommendations_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations"
            ).fetchone()
            active = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='active'"
            ).fetchone()
            profit = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='profit'"
            ).fetchone()
            stop = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='stop'"
            ).fetchone()
            watch = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='watch'"
            ).fetchone()
            avg_pnl = conn.execute(
                "SELECT AVG(pnl_pct) as avg_pnl FROM recommendations "
                "WHERE status IN ('profit', 'stop')"
            ).fetchone()
            avg_active_pnl = conn.execute(
                "SELECT AVG(pnl_pct) as avg_pnl FROM recommendations "
                "WHERE status='active'"
            ).fetchone()
        return {
            "total": dict(total)["cnt"],
            "active": dict(active)["cnt"],
            "profit": dict(profit)["cnt"],
            "stop": dict(stop)["cnt"],
            "watch": dict(watch)["cnt"],
            "avg_closed_pnl": dict(avg_pnl)["avg_pnl"] or 0.0,
            "avg_active_pnl": dict(avg_active_pnl)["avg_pnl"] or 0.0,
        }

    def update_recommendation(self, rec_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(rec_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE recommendations SET {', '.join(sets)} WHERE id=?", vals
            )

    def get_recommendations_by_strategy(self, strategy_type: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE strategy_type=? "
                "AND status IN ('active', 'watch') ORDER BY rec_score DESC",
                (strategy_type,),
            ).fetchall()
        return [dict(r) for r in rows]

    def has_active_recommendation(self, ticker: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations "
                "WHERE ticker=? AND status IN ('active', 'watch')",
                (ticker,),
            ).fetchone()
        return dict(row)["cnt"] > 0

    # -- trades ----------------------------------------------------------------

    def add_trade(
        self,
        ticker: str,
        name: str,
        action: str,
        strategy_type: str = "A",
        recommended_price: float = 0,
        action_price: float = 0,
        quantity_pct: float = 0,
        pnl_pct: float = 0,
        recommendation_id: int | None = None,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades
                    (ticker, name, strategy_type, action, recommended_price,
                     action_price, quantity_pct, pnl_pct, recommendation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, strategy_type, action, recommended_price,
                 action_price, quantity_pct, pnl_pct, recommendation_id, now),
            )
            return cursor.lastrowid

    def get_trades(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trades_by_strategy(self, strategy_type: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE strategy_type=? ORDER BY created_at DESC",
                (strategy_type,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_strategy_performance(self) -> dict:
        """Compute per-strategy performance stats from trades + recommendations."""
        result = {}
        for strat in ["A", "B", "C", "D", "E", "F", "G"]:
            with self._connect() as conn:
                recs = conn.execute(
                    "SELECT * FROM recommendations WHERE strategy_type=? "
                    "AND status IN ('profit', 'stop')",
                    (strat,),
                ).fetchall()
                total = len(recs)
                if total == 0:
                    continue
                profits = [dict(r) for r in recs if dict(r)["status"] == "profit"]
                win_rate = len(profits) / total * 100
                pnls = [dict(r).get("pnl_pct", 0) for r in recs]
                avg_pnl = sum(pnls) / len(pnls) if pnls else 0
                result[strat] = {
                    "total": total,
                    "wins": len(profits),
                    "win_rate": round(win_rate, 1),
                    "avg_pnl": round(avg_pnl, 2),
                }

        # Summary from trades
        with self._connect() as conn:
            total_trades = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades"
            ).fetchone()
            buys = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='buy'"
            ).fetchone()
            skips = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='skip'"
            ).fetchone()
            stops = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='stop_loss'"
            ).fetchone()
            holds_through = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='hold_through_stop'"
            ).fetchone()

        total_reco = dict(buys)["cnt"] + dict(skips)["cnt"]
        exec_rate = (dict(buys)["cnt"] / total_reco * 100) if total_reco > 0 else 0
        total_stop_events = dict(stops)["cnt"] + dict(holds_through)["cnt"]
        stop_compliance = (dict(stops)["cnt"] / total_stop_events * 100) if total_stop_events > 0 else 100

        result["summary"] = {
            "total_trades": dict(total_trades)["cnt"],
            "execution_rate": round(exec_rate, 1),
            "stop_compliance": round(stop_compliance, 1),
            "avg_hold_days": 0,  # TODO: compute from actual dates
        }
        return result

    # -- screenshots (v3.0) ----------------------------------------------------

    def add_screenshot(
        self,
        total_eval: float = 0,
        total_profit: float = 0,
        total_profit_pct: float = 0,
        cash: float = 0,
        portfolio_score: int = 0,
        holdings_json: str = "",
        image_hash: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO screenshots
                    (image_hash, recognized_at, total_eval, total_profit,
                     total_profit_pct, cash, portfolio_score, holdings_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (image_hash, now, total_eval, total_profit,
                 total_profit_pct, cash, portfolio_score, holdings_json, now),
            )
            return cursor.lastrowid

    def get_last_screenshot(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM screenshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # Alias for backward compatibility
    get_latest_screenshot = get_last_screenshot

    def get_screenshot_history(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screenshots ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_screenshot_holding(
        self,
        screenshot_id: int,
        ticker: str,
        name: str,
        quantity: int = 0,
        avg_price: float = 0,
        current_price: float = 0,
        profit_pct: float = 0,
        eval_amount: float = 0,
        diagnosis: str = "",
        diagnosis_action: str = "",
        diagnosis_msg: str = "",
        is_margin: int = 0,
        margin_type: str = "",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO screenshot_holdings
                    (screenshot_id, ticker, name, quantity, avg_price,
                     current_price, profit_pct, eval_amount,
                     diagnosis, diagnosis_action, diagnosis_msg,
                     is_margin, margin_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (screenshot_id, ticker, name, quantity, avg_price,
                 current_price, profit_pct, eval_amount,
                 diagnosis, diagnosis_action, diagnosis_msg,
                 is_margin, margin_type),
            )
            return cursor.lastrowid

    def get_screenshot_holdings(self, screenshot_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screenshot_holdings WHERE screenshot_id=?",
                (screenshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- investment_horizons ----------------------------------------------------

    def add_investment_horizon(
        self,
        ticker: str,
        name: str,
        horizon: str = "default",
        screenshot_id: int | None = None,
        stop_pct: float | None = None,
        target_pct: float | None = None,
        trailing_pct: float | None = None,
        is_margin: int = 0,
        margin_type: str | None = None,
        diagnosis: str = "",
        diagnosis_action: str = "",
        diagnosis_msg: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO investment_horizons
                    (ticker, name, horizon, screenshot_id,
                     stop_pct, target_pct, trailing_pct,
                     is_margin, margin_type,
                     diagnosis, diagnosis_action, diagnosis_msg,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, horizon, screenshot_id,
                 stop_pct, target_pct, trailing_pct,
                 is_margin, margin_type,
                 diagnosis, diagnosis_action, diagnosis_msg,
                 now, now),
            )
            return cursor.lastrowid

    def get_investment_horizon(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM investment_horizons WHERE ticker=? ORDER BY updated_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_horizons_for_screenshot(self, screenshot_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM investment_horizons WHERE screenshot_id=?",
                (screenshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- notification_settings --------------------------------------------------

    DEFAULT_NOTIFICATION_SETTINGS = [
        "report_alert", "supply_alert", "earnings_alert",
        "policy_alert", "morning_briefing", "weekly_report",
    ]

    def _ensure_default_notification_settings(self) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            for name in self.DEFAULT_NOTIFICATION_SETTINGS:
                conn.execute(
                    """INSERT OR IGNORE INTO notification_settings
                       (setting_name, enabled, updated_at) VALUES (?, 1, ?)""",
                    (name, now),
                )

    def get_notification_settings(self) -> dict[str, bool]:
        self._ensure_default_notification_settings()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM notification_settings").fetchall()
        return {r["setting_name"]: bool(r["enabled"]) for r in rows}

    def toggle_notification_setting(self, setting_name: str) -> bool:
        """Toggle a notification setting. Returns new state."""
        now = datetime.utcnow().isoformat()
        current = self.get_notification_settings()
        new_val = 0 if current.get(setting_name, True) else 1
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO notification_settings (setting_name, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(setting_name) DO UPDATE SET enabled=?, updated_at=?""",
                (setting_name, new_val, now, new_val, now),
            )
        return bool(new_val)

    # -- portfolio_horizon ------------------------------------------------------

    def upsert_portfolio_horizon(
        self, ticker: str, name: str = "", horizon: str = "dangi",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO portfolio_horizon (ticker, name, horizon, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                       name=COALESCE(excluded.name, portfolio_horizon.name),
                       horizon=excluded.horizon, updated_at=excluded.updated_at""",
                (ticker, name, horizon, now),
            )

    def get_portfolio_horizon(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio_horizon WHERE ticker=?", (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_portfolio_horizons(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio_horizon ORDER BY updated_at DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    # -- weekly_reports ---------------------------------------------------------

    def add_weekly_report(
        self,
        week_label: str,
        week_start: str,
        week_end: str,
        doc_url: str = "",
        summary_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO weekly_reports
                    (week_label, week_start, week_end, doc_url, summary_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (week_label, week_start, week_end, doc_url, summary_json, now),
            )
            return cursor.lastrowid

    def get_latest_weekly_report(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM weekly_reports ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return dict(row) if row else None

    def get_weekly_reports(self, limit: int = 4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM weekly_reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- reports helpers --------------------------------------------------------

    def get_reports_for_tickers(self, tickers: list[str], limit: int = 5) -> list[dict]:
        if not tickers:
            return []
        placeholders = ",".join("?" for _ in tickers)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM reports WHERE ticker IN ({placeholders}) "
                "ORDER BY date DESC LIMIT ?",
                (*tickers, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_target_upgrades(self, days: int = 7, limit: int = 10) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE date >= ? AND target_price > 0 AND prev_target_price > 0
                         AND target_price > prev_target_price
                   ORDER BY (target_price - prev_target_price) * 1.0 / prev_target_price DESC
                   LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_target_downgrades(self, days: int = 7, limit: int = 10) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE date >= ? AND target_price > 0 AND prev_target_price > 0
                         AND target_price < prev_target_price
                   ORDER BY (prev_target_price - target_price) * 1.0 / prev_target_price DESC
                   LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_by_sector(self, keywords: list[str], limit: int = 5) -> list[dict]:
        if not keywords:
            return []
        conditions = " OR ".join("title LIKE ?" for _ in keywords)
        params = [f"%{kw}%" for kw in keywords]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM reports WHERE ({conditions}) ORDER BY date DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_today(self, limit: int = 10) -> list[dict]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reports WHERE date=? ORDER BY created_at DESC LIMIT ?",
                (today, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- orders (v3.0) ---------------------------------------------------------

    def add_order(
        self,
        ticker: str,
        name: str,
        order_type: str,
        side: str,
        quantity: int,
        price: float = 0,
        order_id: str = "",
        status: str = "pending",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders
                    (ticker, name, order_type, side, quantity, price,
                     order_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, order_type, side, quantity, price,
                 order_id, status, now),
            )
            return cursor.lastrowid

    def update_order(self, order_db_id: int, **kwargs) -> None:
        sets = []
        vals: list = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(order_db_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE orders SET {', '.join(sets)} WHERE id=?", vals
            )

    def get_pending_orders(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_order_count(self) -> int:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM orders WHERE created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
        return dict(row)["cnt"]

    # -- ml_predictions (v3.0) -------------------------------------------------

    def add_prediction(
        self,
        ticker: str,
        pred_date: str,
        probability: float,
        shap_top3: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ml_predictions
                    (ticker, pred_date, probability, shap_top3, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ticker, pred_date, probability, shap_top3, now),
            )
            return cursor.lastrowid

    def get_predictions(self, pred_date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ml_predictions WHERE pred_date=?",
                (pred_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- sentiment (v3.0) ------------------------------------------------------

    def add_sentiment(
        self,
        ticker: str,
        analysis_date: str,
        positive_pct: float = 0,
        negative_pct: float = 0,
        neutral_pct: float = 0,
        headline_count: int = 0,
        summary: str = "",
        score_bonus: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sentiment
                    (ticker, analysis_date, positive_pct, negative_pct,
                     neutral_pct, headline_count, summary, score_bonus, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, analysis_date, positive_pct, negative_pct,
                 neutral_pct, headline_count, summary, score_bonus, now),
            )
            return cursor.lastrowid

    def get_sentiment(self, ticker: str, analysis_date: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sentiment WHERE ticker=? AND analysis_date=? "
                "ORDER BY created_at DESC LIMIT 1",
                (ticker, analysis_date),
            ).fetchone()
        return dict(row) if row else None

    def get_all_sentiments(self, analysis_date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sentiment WHERE analysis_date=?",
                (analysis_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- recommendation_results (v3.0+) ----------------------------------------

    def add_recommendation_result(
        self,
        recommendation_id: int,
        ticker: str,
        rec_price: float,
        strategy_type: str = "A",
        regime_at_rec: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO recommendation_results
                    (recommendation_id, ticker, rec_price, strategy_type,
                     regime_at_rec, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (recommendation_id, ticker, rec_price, strategy_type,
                 regime_at_rec, now),
            )
            return cursor.lastrowid

    def update_recommendation_result(self, result_id: int, **kwargs) -> None:
        sets = []
        vals: list = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(result_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE recommendation_results SET {', '.join(sets)} WHERE id=?", vals
            )

    def get_recommendation_results(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_results WHERE created_at > ? "
                "ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- feedback_reports (v3.0+) ----------------------------------------------

    def add_feedback_report(
        self,
        report_date: str,
        period_days: int = 7,
        total_recs: int = 0,
        hits: int = 0,
        misses: int = 0,
        pending: int = 0,
        hit_rate: float = 0,
        avg_return: float = 0,
        lessons_json: str = "",
        strategy_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback_reports
                    (report_date, period_days, total_recs, hits, misses, pending,
                     hit_rate, avg_return, lessons_json, strategy_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (report_date, period_days, total_recs, hits, misses, pending,
                 hit_rate, avg_return, lessons_json, strategy_json, now),
            )
            return cursor.lastrowid

    def get_latest_feedback_report(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM feedback_reports ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # -- goal_snapshots (v3.0+) ------------------------------------------------

    def add_goal_snapshot(
        self,
        total_asset: float,
        cash: float = 0,
        positions_count: int = 0,
        daily_return: float = 0,
        milestone: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO goal_snapshots
                    (snapshot_date, total_asset, cash, positions_count,
                     daily_return, milestone, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (today, total_asset, cash, positions_count,
                 daily_return, milestone, now),
            )
            return cursor.lastrowid

    def get_goal_snapshots(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goal_snapshots ORDER BY snapshot_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- tenbagger_candidates (v3.0+) ------------------------------------------

    def add_tenbagger_candidate(
        self,
        ticker: str,
        name: str,
        price_at_found: float,
        conditions_met: int = 0,
        conditions_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tenbagger_candidates
                    (ticker, name, found_date, price_at_found, conditions_met,
                     conditions_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'monitoring', ?, ?)
                """,
                (ticker, name, today, price_at_found, conditions_met,
                 conditions_json, now, now),
            )
            return cursor.lastrowid

    def get_active_tenbagger_candidates(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenbagger_candidates WHERE status='monitoring' "
                "ORDER BY conditions_met DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_tenbagger_candidate(self, candidate_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(candidate_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tenbagger_candidates SET {', '.join(sets)} WHERE id=?", vals
            )

    # -- swing_trades (v3.0+) --------------------------------------------------

    def add_swing_trade(
        self,
        ticker: str,
        name: str,
        entry_price: float,
        target_price: float = 0,
        stop_price: float = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO swing_trades
                    (ticker, name, entry_date, entry_price, target_price,
                     stop_price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (ticker, name, today, entry_price, target_price,
                 stop_price, now, now),
            )
            return cursor.lastrowid

    def get_active_swing_trades(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM swing_trades WHERE status='active' "
                "ORDER BY entry_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_swing_trade(self, trade_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(trade_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE swing_trades SET {', '.join(sets)} WHERE id=?", vals
            )

    # -- reports (v3.5) --------------------------------------------------------

    def add_report(
        self,
        source: str,
        title: str,
        broker: str,
        date: str,
        ticker: str = "",
        target_price: float = 0,
        prev_target_price: float = 0,
        opinion: str = "",
        prev_opinion: str = "",
        pdf_url: str = "",
        summary: str = "",
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO reports
                        (source, title, broker, ticker, target_price, prev_target_price,
                         opinion, prev_opinion, date, pdf_url, summary, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source, title, broker, ticker, target_price, prev_target_price,
                     opinion, prev_opinion, date, pdf_url, summary, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            return None

    def get_recent_reports(self, limit: int = 10, ticker: str = "") -> list[dict]:
        with self._connect() as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM reports WHERE ticker=? ORDER BY date DESC LIMIT ?",
                    (ticker, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reports ORDER BY date DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- consensus (v3.5) ------------------------------------------------------

    def upsert_consensus(
        self, ticker: str, name: str = "", avg_target_price: float = 0,
        current_price: float = 0, upside_pct: float = 0,
        buy_count: int = 0, hold_count: int = 0, sell_count: int = 0,
        target_trend: str = "", target_trend_pct: float = 0, score_bonus: int = 0,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO consensus
                    (ticker, name, avg_target_price, current_price, upside_pct,
                     buy_count, hold_count, sell_count, target_trend,
                     target_trend_pct, score_bonus, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, avg_target_price=excluded.avg_target_price,
                    current_price=excluded.current_price, upside_pct=excluded.upside_pct,
                    buy_count=excluded.buy_count, hold_count=excluded.hold_count,
                    sell_count=excluded.sell_count, target_trend=excluded.target_trend,
                    target_trend_pct=excluded.target_trend_pct,
                    score_bonus=excluded.score_bonus, updated_at=excluded.updated_at
                """,
                (ticker, name, avg_target_price, current_price, upside_pct,
                 buy_count, hold_count, sell_count, target_trend,
                 target_trend_pct, score_bonus, now),
            )

    def get_consensus(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM consensus WHERE ticker=?", (ticker,)
            ).fetchone()
        return dict(row) if row else None

    # -- earnings (v3.5) -------------------------------------------------------

    def add_earnings(
        self, ticker: str, name: str, period: str,
        earnings_date: str = "", revenue: float = 0, revenue_consensus: float = 0,
        operating_income: float = 0, op_income_consensus: float = 0,
        op_margin: float = 0, prev_op_margin: float = 0,
        surprise_pct: float = 0, verdict: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO earnings
                    (ticker, name, period, earnings_date, revenue, revenue_consensus,
                     operating_income, op_income_consensus, op_margin, prev_op_margin,
                     surprise_pct, verdict, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, period, earnings_date, revenue, revenue_consensus,
                 operating_income, op_income_consensus, op_margin, prev_op_margin,
                 surprise_pct, verdict, now),
            )
            return cursor.lastrowid

    def get_earnings(self, ticker: str, limit: int = 4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM earnings WHERE ticker=? ORDER BY created_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- financials (v3.5) -----------------------------------------------------

    def upsert_financials(self, ticker: str, name: str = "", period: str = "", **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM financials WHERE ticker=? AND period=?", (ticker, period)
            ).fetchone()
            if existing:
                sets = ["name=?", "created_at=?"]
                vals: list = [name, now]
                for k, v in kwargs.items():
                    sets.append(f"{k}=?")
                    vals.append(v)
                vals.append(existing["id"])
                conn.execute(f"UPDATE financials SET {', '.join(sets)} WHERE id=?", vals)
            else:
                cols = ["ticker", "name", "period", "created_at"]
                vals_list: list = [ticker, name, period, now]
                for k, v in kwargs.items():
                    cols.append(k)
                    vals_list.append(v)
                placeholders = ",".join("?" * len(cols))
                conn.execute(
                    f"INSERT INTO financials ({','.join(cols)}) VALUES ({placeholders})",
                    vals_list,
                )

    def get_financials(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM financials WHERE ticker=? ORDER BY created_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    # -- supply_demand (v3.5) --------------------------------------------------

    def add_supply_demand(
        self, ticker: str, date: str,
        foreign_net: float = 0, institution_net: float = 0, retail_net: float = 0,
        program_net: float = 0, short_balance: float = 0, short_ratio: float = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO supply_demand
                        (ticker, date, foreign_net, institution_net, retail_net,
                         program_net, short_balance, short_ratio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, foreign_net, institution_net, retail_net,
                     program_net, short_balance, short_ratio, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            return None

    def get_supply_demand(self, ticker: str, days: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM supply_demand WHERE ticker=? AND date >= ? ORDER BY date DESC",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- chat_history (v3.5) ---------------------------------------------------

    def add_chat_message(self, role: str, content: str) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, now),
            )
            return cursor.lastrowid

    def get_recent_chat_messages(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = [dict(r) for r in rows]
        result.reverse()
        return result

    def cleanup_old_chat_messages(self, hours: int = 24) -> int:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_history WHERE created_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def clear_chat_history(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_history")

    # -- chat_usage (v3.5) -----------------------------------------------------

    def get_chat_usage_count(self, date: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM chat_usage WHERE date=?", (date,)
            ).fetchone()
        return row["count"] if row else 0

    def increment_chat_usage(self, date: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_usage (date, count) VALUES (?, 1)
                ON CONFLICT(date) DO UPDATE SET count = count + 1
                """,
                (date,),
            )

    # -- macro_events (v3.5) ---------------------------------------------------

    def add_macro_event(
        self, date: str, name: str, country: str = "",
        importance: str = "보통", description: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO macro_events
                    (date, name, country, importance, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date, name, country, importance, description, now),
            )
            return cursor.lastrowid

    def get_macro_events(self, start_date: str, end_date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM macro_events WHERE date >= ? AND date <= ? ORDER BY date",
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- short_selling (v3.5) --------------------------------------------------

    def add_short_selling(
        self, ticker: str, date: str,
        short_volume: int = 0, total_volume: int = 0, short_ratio: float = 0,
        short_balance: int = 0, short_balance_ratio: float = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO short_selling
                        (ticker, date, short_volume, total_volume, short_ratio,
                         short_balance, short_balance_ratio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, short_volume, total_volume, short_ratio,
                     short_balance, short_balance_ratio, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            return None

    def get_short_selling(self, ticker: str, days: int = 60) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM short_selling WHERE ticker=? AND date >= ? ORDER BY date",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_short_selling_latest(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM short_selling WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_overheated_shorts(self, min_ratio: float = 20.0, days: int = 7) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM short_selling
                WHERE (short_ratio >= ? OR short_balance_ratio >= ?)
                  AND date >= ?
                ORDER BY short_ratio DESC
                """,
                (min_ratio, min_ratio / 2, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- inverse_etf (v3.5) ----------------------------------------------------

    def add_inverse_etf(
        self, ticker: str, date: str, name: str = "",
        sector: str = "", volume: int = 0, price: float = 0,
        change_pct: float = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO inverse_etf
                        (ticker, date, name, sector, volume, price, change_pct, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, name, sector, volume, price, change_pct, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            return None

    def get_inverse_etf(self, ticker: str, days: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inverse_etf WHERE ticker=? AND date >= ? ORDER BY date",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_inverse_etf_by_sector(self, sector: str, days: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inverse_etf WHERE sector=? AND date >= ? ORDER BY date",
                (sector, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- margin_balance (v3.5) -------------------------------------------------

    def add_margin_balance(
        self, ticker: str, date: str,
        credit_buy: int = 0, credit_sell: int = 0,
        credit_balance: int = 0, credit_ratio: float = 0,
        collateral_balance: int = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO margin_balance
                        (ticker, date, credit_buy, credit_sell, credit_balance,
                         credit_ratio, collateral_balance, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, credit_buy, credit_sell, credit_balance,
                     credit_ratio, collateral_balance, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            return None

    def get_margin_balance(self, ticker: str, days: int = 60) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM margin_balance WHERE ticker=? AND date >= ? ORDER BY date",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_margin_balance_latest(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM margin_balance WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    # -- margin_thresholds (v3.5) ----------------------------------------------

    def upsert_margin_threshold(
        self, ticker: str, metric: str,
        mean_60d: float = 0, std_60d: float = 0,
        upper_1sigma: float = 0, lower_1sigma: float = 0,
        upper_2sigma: float = 0, lower_2sigma: float = 0,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO margin_thresholds
                    (ticker, metric, mean_60d, std_60d,
                     upper_1sigma, lower_1sigma, upper_2sigma, lower_2sigma,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, metric) DO UPDATE SET
                    mean_60d=excluded.mean_60d,
                    std_60d=excluded.std_60d,
                    upper_1sigma=excluded.upper_1sigma,
                    lower_1sigma=excluded.lower_1sigma,
                    upper_2sigma=excluded.upper_2sigma,
                    lower_2sigma=excluded.lower_2sigma,
                    updated_at=excluded.updated_at
                """,
                (ticker, metric, mean_60d, std_60d,
                 upper_1sigma, lower_1sigma, upper_2sigma, lower_2sigma, now),
            )

    def get_margin_thresholds(self, ticker: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM margin_thresholds WHERE ticker=?", (ticker,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- rebalance_history (v3.5) -----------------------------------------------

    def add_rebalance_event(
        self, trigger_type: str, description: str = "",
        action: str = "", tickers_json: str = "",
        executed: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rebalance_history
                    (trigger_type, description, action, tickers_json, executed, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trigger_type, description, action, tickers_json, executed, now),
            )
            return cursor.lastrowid

    def get_rebalance_history(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rebalance_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_rebalance_executed(self, rebalance_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE rebalance_history SET executed=1 WHERE id=?",
                (rebalance_id,),
            )

    # -- solution_tracking ------------------------------------------------------

    def add_solution(
        self,
        solution_type: str,
        description: str,
        before_snapshot_id: int | None = None,
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = now[:10]
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO solution_tracking
                    (solution_type, description, suggested_date,
                     before_snapshot_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (solution_type, description, today, before_snapshot_id, now),
            )
        return cur.lastrowid or 0

    def get_pending_solutions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM solution_tracking WHERE executed=0 "
                "ORDER BY created_at DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_solution_executed(
        self,
        solution_id: int,
        after_snapshot_id: int | None = None,
        profit_change_pct: float = 0.0,
        alpha_change: float = 0.0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE solution_tracking SET
                    executed=1, after_snapshot_id=?, profit_change_pct=?, alpha_change=?
                WHERE id=?
                """,
                (after_snapshot_id, profit_change_pct, alpha_change, solution_id),
            )

    def get_solution_history(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM solution_tracking ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_solution_stats(self) -> dict:
        """Return solution execution and effectiveness stats."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM solution_tracking",
            ).fetchone()[0]
            executed = conn.execute(
                "SELECT COUNT(*) FROM solution_tracking WHERE executed=1",
            ).fetchone()[0]
            effective = conn.execute(
                "SELECT COUNT(*) FROM solution_tracking "
                "WHERE executed=1 AND profit_change_pct > 0",
            ).fetchone()[0]
        exec_rate = (executed / total) if total > 0 else 0
        eff_rate = (effective / executed) if executed > 0 else 0
        return {
            "total": total,
            "executed": executed,
            "effective": effective,
            "execution_rate": exec_rate,
            "effectiveness_rate": eff_rate,
        }

    # -- risk_violations --------------------------------------------------------

    def add_risk_violation(
        self, date_str: str, violation_type: str, severity: str = "medium",
        description: str = "", recommended_action: str = "", action_taken: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO risk_violations
                   (date, violation_type, severity, description,
                    recommended_action, action_taken, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date_str, violation_type, severity, description,
                 recommended_action, action_taken, now),
            )
        return cur.lastrowid or 0

    def get_risk_violations(self, days: int = 7, limit: int = 50) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM risk_violations WHERE date >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- portfolio_snapshots ----------------------------------------------------

    def add_portfolio_snapshot(
        self, date_str: str, total_value: float = 0, cash: float = 0,
        holdings_count: int = 0, daily_pnl_pct: float = 0,
        total_pnl_pct: float = 0, mdd: float = 0, peak_value: float = 0,
        kospi_close: float = 0, kosdaq_close: float = 0,
        holdings_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO portfolio_snapshots
                   (date, total_value, cash, holdings_count, daily_pnl_pct,
                    total_pnl_pct, mdd, peak_value, kospi_close, kosdaq_close,
                    holdings_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (date_str, total_value, cash, holdings_count, daily_pnl_pct,
                 total_pnl_pct, mdd, peak_value, kospi_close, kosdaq_close,
                 holdings_json, now),
            )
        return cur.lastrowid or 0

    def get_portfolio_snapshots(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_portfolio_peak(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(total_value) as peak FROM portfolio_snapshots",
            ).fetchone()
        return row["peak"] if row and row["peak"] else 0.0

    # -- recommendation_tracking ------------------------------------------------

    def add_recommendation_track(
        self, ticker: str, name: str, strategy: str, score: float,
        recommended_date: str, entry_price: float,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO recommendation_tracking
                   (ticker, name, strategy, score, recommended_date,
                    entry_price, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ticker, name, strategy, score, recommended_date,
                 entry_price, now),
            )
        return cur.lastrowid or 0

    def update_recommendation_track(
        self, track_id: int, **kwargs,
    ) -> None:
        allowed = {"price_d1", "price_d3", "price_d5", "price_d10", "price_d20",
                    "return_d1", "return_d3", "return_d5", "return_d10", "return_d20", "hit"}
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return
        vals.append(track_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE recommendation_tracking SET {', '.join(sets)} WHERE id=?",
                vals,
            )

    def get_recommendation_tracks(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_tracking "
                "ORDER BY recommended_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- ml_performance ---------------------------------------------------------

    def add_ml_performance(
        self, date_str: str, model_version: str = "",
        train_score: float = 0, val_score: float = 0,
        overfit_gap: float = 0, features_used: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO ml_performance
                   (date, model_version, train_score, val_score,
                    overfit_gap, features_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date_str, model_version, train_score, val_score,
                 overfit_gap, features_used, now),
            )
        return cur.lastrowid or 0

    def get_ml_performance(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ml_performance ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- hallucination_log ------------------------------------------------------

    def add_hallucination_log(
        self, date_str: str, query: str = "", response_preview: str = "",
        verified_count: int = 0, unverified_count: int = 0,
        unverified_claims: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO hallucination_log
                   (date, query, response_preview, verified_count,
                    unverified_count, unverified_claims, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date_str, query, response_preview, verified_count,
                 unverified_count, unverified_claims, now),
            )
        return cur.lastrowid or 0

    def get_hallucination_stats(self, days: int = 7) -> dict:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM hallucination_log WHERE date >= ?",
                (cutoff,),
            ).fetchone()[0]
            unverified = conn.execute(
                "SELECT SUM(unverified_count) FROM hallucination_log WHERE date >= ?",
                (cutoff,),
            ).fetchone()[0] or 0
        return {"total_responses": total, "total_unverified": unverified}

    # -- trade_executions -------------------------------------------------------

    def add_trade_execution(
        self, ticker: str, name: str, direction: str = "buy",
        quantity: int = 0, price: float = 0, amount: float = 0,
        commission: float = 0, strategy: str = "", score: float = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO trade_executions
                   (ticker, name, direction, quantity, price, amount,
                    commission, strategy, score, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticker, name, direction, quantity, price, amount,
                 commission, strategy, score, now),
            )
        return cur.lastrowid or 0

    def get_trade_executions(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_executions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- users ------------------------------------------------------------------

    def add_user(self, telegram_id: int, name: str, is_admin: bool = False,
                 config_json: str = "{}") -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO users
                   (telegram_id, name, is_admin, config_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (telegram_id, name, 1 if is_admin else 0, config_json, now, now),
            )
        return cur.lastrowid or 0

    def get_user(self, telegram_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id=?",
                (telegram_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_user(self, telegram_id: int, config_json: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET config_json=?, updated_at=? WHERE telegram_id=?",
                (config_json, now, telegram_id),
            )

    # -- future_watchlist -------------------------------------------------------

    def upsert_future_watchlist(
        self,
        ticker: str,
        name: str,
        sector: str,
        tier: str,
        future_score: int = 0,
        tech_maturity: int = 0,
        financial_stability: int = 0,
        policy_benefit: int = 0,
        momentum: int = 0,
        valuation: int = 0,
        entry_signal: str = "WAIT",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO future_watchlist
                    (ticker, name, sector, tier, future_score,
                     tech_maturity, financial_stability, policy_benefit,
                     momentum, valuation, entry_signal, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, sector=excluded.sector, tier=excluded.tier,
                    future_score=excluded.future_score,
                    tech_maturity=excluded.tech_maturity,
                    financial_stability=excluded.financial_stability,
                    policy_benefit=excluded.policy_benefit,
                    momentum=excluded.momentum, valuation=excluded.valuation,
                    entry_signal=excluded.entry_signal, updated_at=excluded.updated_at
                """,
                (ticker, name, sector, tier, future_score,
                 tech_maturity, financial_stability, policy_benefit,
                 momentum, valuation, entry_signal, now),
            )

    def get_future_watchlist(self, sector: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if sector:
                rows = conn.execute(
                    "SELECT * FROM future_watchlist WHERE sector=? ORDER BY future_score DESC",
                    (sector,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM future_watchlist ORDER BY sector, future_score DESC",
                ).fetchall()
        return [dict(r) for r in rows]

    def get_future_watchlist_entry(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM future_watchlist WHERE ticker=?", (ticker,),
            ).fetchone()
        return dict(row) if row else None

    # -- future_triggers --------------------------------------------------------

    def add_future_trigger(
        self,
        sector: str,
        trigger_type: str,
        impact: str,
        title: str,
        source: str = "",
        matched_keywords: str = "",
        beneficiary_tickers: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO future_triggers
                    (sector, trigger_type, impact, title, source,
                     matched_keywords, beneficiary_tickers, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sector, trigger_type, impact, title, source,
                 matched_keywords, beneficiary_tickers, now),
            )
        return cur.lastrowid or 0

    def get_future_triggers(
        self, sector: str | None = None, days: int = 7, limit: int = 20,
    ) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            if sector:
                rows = conn.execute(
                    "SELECT * FROM future_triggers WHERE sector=? AND created_at>=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (sector, cutoff, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM future_triggers WHERE created_at>=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- seed_positions ---------------------------------------------------------

    def upsert_seed_position(
        self,
        ticker: str,
        name: str,
        sector: str,
        tier: str,
        avg_price: float = 0.0,
        quantity: int = 0,
        status: str = "active",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seed_positions
                    (ticker, name, sector, tier, avg_price, quantity, status,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, avg_price=excluded.avg_price,
                    quantity=excluded.quantity, status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (ticker, name, sector, tier, avg_price, quantity, status, now, now),
            )

    def get_seed_positions(self, status: str = "active") -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM seed_positions WHERE status=? ORDER BY sector, name",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_seed_position(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM seed_positions WHERE ticker=?", (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def close_seed_position(self, ticker: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE seed_positions SET status='closed', updated_at=? WHERE ticker=?",
                (now, ticker),
            )

    # -- strategy_stats ------------------------------------------------------------

    def add_strategy_stat(
        self, strategy: str, period: str, total_count: int,
        win_count: int, win_rate: float, avg_return: float,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO strategy_stats (strategy, period, total_count, win_count, "
                "win_rate, avg_return, calculated_at) VALUES (?,?,?,?,?,?,?)",
                (strategy, period, total_count, win_count, win_rate, avg_return, now),
            )
        return cur.lastrowid or 0

    def get_strategy_stats(self, strategy: str | None = None, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            if strategy:
                rows = conn.execute(
                    "SELECT * FROM strategy_stats WHERE strategy=? "
                    "ORDER BY calculated_at DESC LIMIT ?",
                    (strategy, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM strategy_stats ORDER BY calculated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- surge_stocks --------------------------------------------------------------

    def add_surge_stock(
        self, ticker: str, name: str = "", scan_time: str = "",
        change_pct: float = 0.0, volume_ratio: float = 0.0,
        triggers: str = "", market_cap: float = 0.0,
        health_grade: str = "", health_score: int = 0,
        health_reasons: str = "", ai_analysis: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO surge_stocks (ticker, name, scan_time, change_pct, "
                "volume_ratio, triggers, market_cap, health_grade, health_score, "
                "health_reasons, ai_analysis, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, name, scan_time, change_pct, volume_ratio, triggers,
                 market_cap, health_grade, health_score, health_reasons,
                 ai_analysis, now),
            )
        return cur.lastrowid or 0

    def get_surge_stocks(self, days: int = 1, limit: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM surge_stocks WHERE created_at>=? "
                "ORDER BY change_pct DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- stealth_accumulations -----------------------------------------------------

    def add_stealth_accumulation(
        self, ticker: str, name: str = "", total_score: int = 0,
        patterns_json: str = "", price_change_20d: float = 0.0,
        inst_total: float = 0.0, foreign_total: float = 0.0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO stealth_accumulations (ticker, name, total_score, "
                "patterns_json, price_change_20d, inst_total, foreign_total, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ticker, name, total_score, patterns_json,
                 price_change_20d, inst_total, foreign_total, now),
            )
        return cur.lastrowid or 0

    def get_stealth_accumulations(self, days: int = 1, limit: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stealth_accumulations WHERE created_at>=? "
                "ORDER BY total_score DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- trade_registers -----------------------------------------------------------

    def add_trade_register(
        self, ticker: str, name: str, quantity: int = 0,
        price: float = 0.0, total_amount: float = 0.0,
        source: str = "text", horizon: str = "swing",
        trailing_stop_pct: float = 0.05, target_profit_pct: float = 0.10,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO trade_registers (ticker, name, quantity, price, "
                "total_amount, source, horizon, trailing_stop_pct, "
                "target_profit_pct, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, name, quantity, price, total_amount, source,
                 horizon, trailing_stop_pct, target_profit_pct, "active", now, now),
            )
        return cur.lastrowid or 0

    def get_trade_registers(self, status: str = "active", limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_registers WHERE status=? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def close_trade_register(self, trade_id: int) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE trade_registers SET status='closed', updated_at=? WHERE id=?",
                (now, trade_id),
            )

    # -- multi_agent_results -------------------------------------------------------

    def add_multi_agent_result(
        self, ticker: str, name: str = "",
        technical_score: int = 0, fundamental_score: int = 0,
        sentiment_score: int = 0, combined_score: int = 0,
        verdict: str = "", confidence: str = "",
        strategist_summary: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO multi_agent_results (ticker, name, technical_score, "
                "fundamental_score, sentiment_score, combined_score, verdict, "
                "confidence, strategist_summary, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ticker, name, technical_score, fundamental_score,
                 sentiment_score, combined_score, verdict, confidence,
                 strategist_summary, now),
            )
        return cur.lastrowid or 0

    def get_multi_agent_results(self, ticker: str | None = None, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM multi_agent_results WHERE ticker=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (ticker, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM multi_agent_results ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- macro_cache (Phase 8 speed optimization) --------------------------------

    def save_macro_cache(self, snapshot_json: str) -> None:
        """매크로 스냅샷을 SQLite에 캐시 (항상 1행, UPSERT)."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO macro_cache (id, snapshot_json, fetched_at, created_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    fetched_at=excluded.fetched_at
                """,
                (snapshot_json, now, now),
            )

    def get_macro_cache(self) -> dict | None:
        """캐시된 매크로 스냅샷 반환. 없으면 None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_json, ai_summary, ai_summary_at, fetched_at "
                "FROM macro_cache WHERE id=1"
            ).fetchone()
        if row:
            return dict(row)
        return None

    def save_ai_summary_cache(self, summary: str) -> None:
        """AI 요약을 캐시에 저장."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE macro_cache SET ai_summary=?, ai_summary_at=?
                WHERE id=1
                """,
                (summary, now),
            )

    def get_ai_summary_cache(self, max_age_seconds: int = 300) -> str | None:
        """캐시된 AI 요약 반환. max_age_seconds 이내만 유효."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ai_summary, ai_summary_at FROM macro_cache WHERE id=1"
            ).fetchone()
        if not row or not row["ai_summary"] or not row["ai_summary_at"]:
            return None
        try:
            cached_at = datetime.fromisoformat(row["ai_summary_at"])
            if (datetime.utcnow() - cached_at).total_seconds() > max_age_seconds:
                return None
        except (ValueError, TypeError):
            return None
        return row["ai_summary"]

    # -- Phase 9: 투자자 프로필 & 학습 시스템 ----------------------------------

    def get_investor_profile(self) -> dict | None:
        """투자자 프로필 반환."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM investor_profile WHERE id=1"
            ).fetchone()
        return dict(row) if row else None

    def upsert_investor_profile(self, **kwargs) -> None:
        """투자자 프로필 생성 또는 업데이트."""
        now = datetime.utcnow().isoformat()
        existing = self.get_investor_profile()
        if existing:
            sets = ["updated_at=?"]
            vals: list = [now]
            for k, v in kwargs.items():
                sets.append(f"{k}=?")
                vals.append(v)
            vals.append(1)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE investor_profile SET {', '.join(sets)} WHERE id=1",
                    vals,
                )
        else:
            cols = ["id", "updated_at"]
            placeholders = ["1", "?"]
            vals = [now]
            for k, v in kwargs.items():
                cols.append(k)
                placeholders.append("?")
                vals.append(v)
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO investor_profile ({', '.join(cols)}) "
                    f"VALUES ({', '.join(placeholders)})",
                    vals,
                )

    def get_holding_analysis(self, holding_id: int) -> dict | None:
        """보유종목 분석 데이터 반환."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holding_analysis WHERE holding_id=?",
                (holding_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_holding_analyses(self) -> list[dict]:
        """모든 활성 보유종목 분석 데이터."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ha.* FROM holding_analysis ha "
                "JOIN holdings h ON ha.holding_id = h.id "
                "WHERE h.status = 'active' ORDER BY ha.updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_holding_analysis(
        self, holding_id: int, ticker: str, name: str, **kwargs
    ) -> None:
        """보유종목 분석 생성 또는 업데이트."""
        now = datetime.utcnow().isoformat()
        existing = self.get_holding_analysis(holding_id)
        if existing:
            sets = ["updated_at=?"]
            vals: list = [now]
            for k, v in kwargs.items():
                sets.append(f"{k}=?")
                vals.append(v)
            vals.append(holding_id)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE holding_analysis SET {', '.join(sets)} "
                    f"WHERE holding_id=?",
                    vals,
                )
        else:
            cols = ["holding_id", "ticker", "name", "created_at", "updated_at"]
            placeholders = ["?", "?", "?", "?", "?"]
            vals = [holding_id, ticker, name, now, now]
            for k, v in kwargs.items():
                cols.append(k)
                placeholders.append("?")
                vals.append(v)
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO holding_analysis ({', '.join(cols)}) "
                    f"VALUES ({', '.join(placeholders)})",
                    vals,
                )

    def add_trade_lesson(
        self, ticker: str, name: str, action: str,
        pnl_pct: float = 0, hold_days: int = 0, lesson: str = "",
    ) -> int:
        """매매 교훈 기록."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO trade_lessons "
                "(ticker, name, action, pnl_pct, hold_days, lesson, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, name, action, pnl_pct, hold_days, lesson, now),
            )
            return cursor.lastrowid

    def get_trade_lessons(self, limit: int = 20) -> list[dict]:
        """최근 매매 교훈 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lessons ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def compute_investor_stats(self) -> dict:
        """매매 이력으로 투자 성향 통계 계산."""
        with self._connect() as conn:
            # 완료된 거래 통계
            trades = conn.execute(
                "SELECT * FROM holdings WHERE status != 'active'"
            ).fetchall()

        if not trades:
            return {
                "trade_count": 0, "win_rate": 0, "avg_hold_days": 0,
                "avg_profit_pct": 0, "avg_loss_pct": 0,
                "style": "신규", "risk_tolerance": "medium",
            }

        wins = 0
        total_profit = 0.0
        total_loss = 0.0
        profit_count = 0
        loss_count = 0
        total_days = 0

        for t in trades:
            t = dict(t)
            pnl = t.get("pnl_pct", 0)
            # 보유 기간 계산
            try:
                buy = datetime.fromisoformat(t["buy_date"])
                sell = datetime.fromisoformat(t["updated_at"])
                days = (sell - buy).days
            except (ValueError, TypeError, KeyError):
                days = 0
            total_days += max(days, 0)

            if pnl > 0:
                wins += 1
                total_profit += pnl
                profit_count += 1
            elif pnl < 0:
                total_loss += abs(pnl)
                loss_count += 1

        count = len(trades)
        avg_hold = total_days / count if count else 0
        win_rate = (wins / count * 100) if count else 0
        avg_profit = total_profit / profit_count if profit_count else 0
        avg_loss = total_loss / loss_count if loss_count else 0

        # 투자 스타일 자동 판단
        if avg_hold <= 3:
            style = "scalper"
        elif avg_hold <= 14:
            style = "swing"
        elif avg_hold <= 60:
            style = "position"
        else:
            style = "long_term"

        # 리스크 성향
        if avg_loss > 10 or win_rate < 40:
            risk = "aggressive"
        elif avg_loss < 3 and win_rate > 60:
            risk = "conservative"
        else:
            risk = "medium"

        return {
            "trade_count": count,
            "win_rate": round(win_rate, 1),
            "avg_hold_days": round(avg_hold, 1),
            "avg_profit_pct": round(avg_profit, 1),
            "avg_loss_pct": round(avg_loss, 1),
            "style": style,
            "risk_tolerance": risk,
        }
