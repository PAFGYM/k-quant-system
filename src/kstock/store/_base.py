"""StoreBase: DB 연결, 스키마 초기화, job_runs 메서드."""

from __future__ import annotations

import asyncio
import functools
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator, TypeVar

T = TypeVar("T")


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
    ticker            TEXT    NOT NULL UNIQUE,
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

CREATE TABLE IF NOT EXISTS dart_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    title             TEXT    NOT NULL DEFAULT '',
    event_type        TEXT    DEFAULT '',
    url               TEXT    DEFAULT '',
    created_at        TEXT    NOT NULL,
    UNIQUE(ticker, date, title)
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

-- v4.3: 매매일지 AI 복기
CREATE TABLE IF NOT EXISTS trade_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT    NOT NULL DEFAULT 'weekly',
    date_range      TEXT    NOT NULL,
    total_trades    INTEGER DEFAULT 0,
    win_rate        REAL    DEFAULT 0,
    avg_pnl         REAL    DEFAULT 0,
    best_trade_json TEXT,
    worst_trade_json TEXT,
    patterns_json   TEXT,
    ai_review       TEXT    DEFAULT '',
    tips_json       TEXT,
    mistakes_json   TEXT,
    created_at      TEXT    NOT NULL
);

-- v4.3: 섹터 로테이션 스냅샷
CREATE TABLE IF NOT EXISTS sector_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT    NOT NULL,
    sectors_json    TEXT    NOT NULL,
    signals_json    TEXT,
    portfolio_json  TEXT,
    recommendations_json TEXT,
    created_at      TEXT    NOT NULL
);

-- v4.3: 역발상 시그널 이력
CREATE TABLE IF NOT EXISTS contrarian_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type     TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    direction       TEXT    NOT NULL,
    strength        REAL    DEFAULT 0,
    score_adj       INTEGER DEFAULT 0,
    reasons_json    TEXT,
    data_json       TEXT,
    created_at      TEXT    NOT NULL
);

-- v5.0: 통합 이벤트 로그
CREATE TABLE IF NOT EXISTS event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    severity        TEXT    NOT NULL DEFAULT 'info',
    message         TEXT    NOT NULL,
    source          TEXT    DEFAULT '',
    ticker          TEXT    DEFAULT '',
    order_id        TEXT    DEFAULT '',
    data_json       TEXT,
    created_at      TEXT    NOT NULL
);

-- v5.5: 사용자 피드백 (좋아요/싫어요/일일평가)
CREATE TABLE IF NOT EXISTS user_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_name       TEXT    NOT NULL,
    feedback        TEXT    NOT NULL,
    comment         TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL
);

-- v5.0: 리컨실레이션 이력
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT    NOT NULL DEFAULT 'ok',
    internal_count  INTEGER DEFAULT 0,
    broker_count    INTEGER DEFAULT 0,
    matched_count   INTEGER DEFAULT 0,
    mismatch_count  INTEGER DEFAULT 0,
    safety_level    TEXT    DEFAULT 'NORMAL',
    mismatches_json TEXT,
    created_at      TEXT    NOT NULL
);

-- v5.0: Execution Replay 이력
CREATE TABLE IF NOT EXISTS execution_replay (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT    NOT NULL,
    strategy            TEXT    DEFAULT '',
    side                TEXT    NOT NULL,
    signal_price        REAL    DEFAULT 0,
    execution_price     REAL    DEFAULT 0,
    slippage_pct        REAL    DEFAULT 0,
    pnl_pct             REAL    DEFAULT 0,
    bt_predicted_return REAL,
    bt_win_prob         REAL,
    direction_match     INTEGER,
    created_at          TEXT    NOT NULL
);

-- v6.0: 글로벌 뉴스 헤드라인
CREATE TABLE IF NOT EXISTS global_news (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    url             TEXT    DEFAULT '',
    category        TEXT    DEFAULT 'market',
    lang            TEXT    DEFAULT 'ko',
    impact_score    INTEGER DEFAULT 0,
    is_urgent       INTEGER DEFAULT 0,
    published       TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL,
    content_summary TEXT    DEFAULT '',
    video_id        TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_global_news_created ON global_news(created_at);
CREATE INDEX IF NOT EXISTS idx_global_news_urgent ON global_news(is_urgent, created_at);

-- v6.2: 대화 메모리 강화 (RAG)
CREATE TABLE IF NOT EXISTS chat_memory_enhanced (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role            TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    topic           TEXT    DEFAULT '',
    tickers         TEXT    DEFAULT '',
    intent          TEXT    DEFAULT '',
    keywords        TEXT    DEFAULT '',
    sentiment       TEXT    DEFAULT 'neutral',
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_enhanced_topic ON chat_memory_enhanced(topic);
CREATE INDEX IF NOT EXISTS idx_chat_enhanced_tickers ON chat_memory_enhanced(tickers);
CREATE INDEX IF NOT EXISTS idx_chat_enhanced_created ON chat_memory_enhanced(created_at);

-- v6.2: 사용자 선호도 패턴
CREATE TABLE IF NOT EXISTS user_preferences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    preference_key  TEXT    NOT NULL UNIQUE,
    preference_value TEXT   NOT NULL DEFAULT '',
    confidence      REAL    DEFAULT 0.5,
    source          TEXT    DEFAULT 'inferred',
    updated_at      TEXT    NOT NULL
);

-- v6.2: 신호별 적중률 추적 (자가 학습 루프)
CREATE TABLE IF NOT EXISTS signal_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_source   TEXT    NOT NULL,
    signal_type     TEXT    NOT NULL DEFAULT 'buy',
    ticker          TEXT    NOT NULL,
    name            TEXT    DEFAULT '',
    signal_date     TEXT    NOT NULL,
    signal_score    REAL    DEFAULT 0,
    signal_price    REAL    DEFAULT 0,
    horizon         TEXT    DEFAULT 'swing',
    manager         TEXT    DEFAULT '',
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
    max_return      REAL,
    max_drawdown    REAL,
    hit             INTEGER DEFAULT 0,
    evaluated_at    TEXT,
    created_at      TEXT    NOT NULL,
    UNIQUE(signal_source, ticker, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_signal_perf_source ON signal_performance(signal_source);
CREATE INDEX IF NOT EXISTS idx_signal_perf_date ON signal_performance(signal_date);
CREATE INDEX IF NOT EXISTS idx_signal_perf_ticker ON signal_performance(ticker);

-- v6.2: 매매 자동 복기 (trade_debrief)
CREATE TABLE IF NOT EXISTS trade_debrief (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER,
    ticker          TEXT    NOT NULL,
    name            TEXT    DEFAULT '',
    action          TEXT    NOT NULL,
    entry_price     REAL    DEFAULT 0,
    exit_price      REAL    DEFAULT 0,
    pnl_pct         REAL    DEFAULT 0,
    hold_days       INTEGER DEFAULT 0,
    horizon         TEXT    DEFAULT 'swing',
    manager         TEXT    DEFAULT '',
    signal_source   TEXT    DEFAULT '',
    signal_score    REAL    DEFAULT 0,
    market_regime   TEXT    DEFAULT '',
    entry_reason    TEXT    DEFAULT '',
    exit_reason     TEXT    DEFAULT '',
    ai_review       TEXT    DEFAULT '',
    lessons_json    TEXT    DEFAULT '[]',
    mistakes_json   TEXT    DEFAULT '[]',
    improvements    TEXT    DEFAULT '',
    grade           TEXT    DEFAULT 'C',
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debrief_ticker ON trade_debrief(ticker);
CREATE INDEX IF NOT EXISTS idx_debrief_created ON trade_debrief(created_at);
CREATE INDEX IF NOT EXISTS idx_debrief_grade ON trade_debrief(grade);

-- v6.2: 신호 소스별 성과 요약 (주기적 집계)
CREATE TABLE IF NOT EXISTS signal_source_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_source   TEXT    NOT NULL,
    period          TEXT    NOT NULL DEFAULT 'weekly',
    total_signals   INTEGER DEFAULT 0,
    evaluated       INTEGER DEFAULT 0,
    hits            INTEGER DEFAULT 0,
    hit_rate        REAL    DEFAULT 0,
    avg_return_d5   REAL    DEFAULT 0,
    avg_return_d10  REAL    DEFAULT 0,
    avg_return_d20  REAL    DEFAULT 0,
    avg_max_return  REAL    DEFAULT 0,
    avg_max_dd      REAL    DEFAULT 0,
    weight_adj      REAL    DEFAULT 1.0,
    calculated_at   TEXT    NOT NULL,
    UNIQUE(signal_source, period, calculated_at)
);

-- v6.2.1: API 토큰 사용량 로그
CREATE TABLE IF NOT EXISTS api_usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    provider        TEXT    NOT NULL DEFAULT 'anthropic',
    model           TEXT    NOT NULL DEFAULT '',
    function_name   TEXT    NOT NULL DEFAULT '',
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cache_read_tokens   INTEGER DEFAULT 0,
    cache_write_tokens  INTEGER DEFAULT 0,
    total_cost_usd  REAL    DEFAULT 0,
    latency_ms      REAL    DEFAULT 0,
    status          TEXT    DEFAULT 'success',
    error_message   TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_api_usage_ts ON api_usage_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_usage_provider ON api_usage_log(provider);
CREATE INDEX IF NOT EXISTS idx_api_usage_model ON api_usage_log(model);

-- v6.2.1: 시스템 자가 점수 기록
CREATE TABLE IF NOT EXISTS system_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    score_date      TEXT    NOT NULL,
    total_score     REAL    DEFAULT 0,
    signal_score    REAL    DEFAULT 0,
    trade_score     REAL    DEFAULT 0,
    alert_score     REAL    DEFAULT 0,
    learning_score  REAL    DEFAULT 0,
    cost_score      REAL    DEFAULT 0,
    uptime_score    REAL    DEFAULT 0,
    details_json    TEXT    DEFAULT '{}',
    created_at      TEXT    NOT NULL,
    UNIQUE(score_date)
);

-- v6.2.1: 뉴스 중복 전송 방지 (재시작 후에도 유지)
CREATE TABLE IF NOT EXISTS sent_news_urls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT    UNIQUE NOT NULL,
    created_at TEXT    DEFAULT (datetime('now'))
);

-- v9.5.3: 긴급 글로벌 알림 중복 방지 (내용 해시 기반)
CREATE TABLE IF NOT EXISTS sent_urgent_alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_hash    TEXT    UNIQUE NOT NULL,
    title_summary TEXT    DEFAULT '',
    created_at    TEXT    NOT NULL
);

-- v9.0: 프로그램 매매 추적
CREATE TABLE IF NOT EXISTS program_trading (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT    NOT NULL,
    market     TEXT    NOT NULL DEFAULT 'KOSPI',
    arb_buy    REAL    DEFAULT 0,
    arb_sell   REAL    DEFAULT 0,
    arb_net    REAL    DEFAULT 0,
    non_arb_buy  REAL  DEFAULT 0,
    non_arb_sell REAL  DEFAULT 0,
    non_arb_net  REAL  DEFAULT 0,
    total_buy  REAL    DEFAULT 0,
    total_sell REAL    DEFAULT 0,
    total_net  REAL    DEFAULT 0,
    created_at TEXT    DEFAULT (datetime('now')),
    UNIQUE(date, market)
);

CREATE INDEX IF NOT EXISTS idx_program_trading_date ON program_trading(date);

-- v9.0: 신용잔고 + 고객예탁금 추적
CREATE TABLE IF NOT EXISTS credit_balance (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT    NOT NULL,
    deposit        REAL    DEFAULT 0,
    deposit_change REAL    DEFAULT 0,
    credit         REAL    DEFAULT 0,
    credit_change  REAL    DEFAULT 0,
    created_at     TEXT    DEFAULT (datetime('now')),
    UNIQUE(date)
);

CREATE INDEX IF NOT EXISTS idx_credit_balance_date ON credit_balance(date);

-- v9.0: ETF 자금흐름 추적
CREATE TABLE IF NOT EXISTS etf_flow (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT    NOT NULL,
    code       TEXT    NOT NULL,
    name       TEXT,
    etf_type   TEXT,
    price      REAL    DEFAULT 0,
    change_pct REAL    DEFAULT 0,
    nav        REAL    DEFAULT 0,
    market_cap REAL    DEFAULT 0,
    volume     INTEGER DEFAULT 0,
    created_at TEXT    DEFAULT (datetime('now')),
    UNIQUE(date, code)
);

CREATE INDEX IF NOT EXISTS idx_etf_flow_date ON etf_flow(date);

-- 성능 인덱스: 자주 조회되는 테이블
CREATE INDEX IF NOT EXISTS idx_holdings_status ON holdings(status);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status);
CREATE INDEX IF NOT EXISTS idx_recommendations_created ON recommendations(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders(ticker);
CREATE INDEX IF NOT EXISTS idx_watchlist_ticker ON watchlist(ticker);
CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_event_log_created ON event_log(created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(ticker);
CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker);
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);

-- v9.4: AI 토론 결과 저장
CREATE TABLE IF NOT EXISTS ai_debates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    name            TEXT,
    verdict         TEXT,
    confidence      REAL DEFAULT 0,
    consensus_level TEXT,
    price_target    REAL DEFAULT 0,
    stop_loss       REAL DEFAULT 0,
    key_arguments   TEXT,
    dissenting_view TEXT,
    round1_data     TEXT,
    round2_data     TEXT,
    pattern_summary TEXT,
    api_calls       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, created_at)
);

CREATE INDEX IF NOT EXISTS idx_debates_ticker ON ai_debates(ticker);
CREATE INDEX IF NOT EXISTS idx_debates_created ON ai_debates(created_at);

-- v9.4: 예측 정확도 추적
CREATE TABLE IF NOT EXISTS debate_accuracy (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id         INTEGER REFERENCES ai_debates(id),
    ticker            TEXT NOT NULL,
    predicted_verdict TEXT,
    predicted_target  REAL,
    actual_price_5d   REAL,
    actual_price_10d  REAL,
    actual_price_20d  REAL,
    accuracy_score    REAL,
    evaluated_at      TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_accuracy_debate ON debate_accuracy(debate_id);
CREATE INDEX IF NOT EXISTS idx_accuracy_ticker ON debate_accuracy(ticker);

-- v9.5: YouTube 인텔리전스 (구조화 분석 결과)
CREATE TABLE IF NOT EXISTS youtube_intelligence (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id                 TEXT    NOT NULL UNIQUE,
    source                   TEXT    DEFAULT '',
    title                    TEXT    DEFAULT '',
    mentioned_tickers        TEXT    DEFAULT '[]',
    mentioned_sectors        TEXT    DEFAULT '[]',
    market_outlook           TEXT    DEFAULT '',
    key_numbers              TEXT    DEFAULT '[]',
    investment_implications  TEXT    DEFAULT '',
    full_summary             TEXT    DEFAULT '',
    raw_summary              TEXT    DEFAULT '',
    confidence               REAL    DEFAULT 0.0,
    created_at               TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yt_intel_created ON youtube_intelligence(created_at);
CREATE INDEX IF NOT EXISTS idx_yt_intel_video ON youtube_intelligence(video_id);

-- v9.5: 매니저 분석 stance 캐시 (통합 컨텍스트용)
CREATE TABLE IF NOT EXISTS manager_stances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_key TEXT    NOT NULL,
    stance      TEXT    DEFAULT '',
    holdings_summary TEXT DEFAULT '',
    created_at  TEXT    NOT NULL,
    UNIQUE(manager_key, created_at)
);

CREATE INDEX IF NOT EXISTS idx_stances_created ON manager_stances(created_at);

-- v9.5.1: 브리핑 저장 (AI 채팅이 참조할 수 있도록)
CREATE TABLE IF NOT EXISTS briefings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    briefing_type TEXT NOT NULL,
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_briefings_type ON briefings(briefing_type);
CREATE INDEX IF NOT EXISTS idx_briefings_created ON briefings(created_at);

-- v9.5.3: 매니저 성적표 (매니저별 추천 성과 추적)
CREATE TABLE IF NOT EXISTS manager_scorecard (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_key     TEXT    NOT NULL,
    period          TEXT    NOT NULL DEFAULT 'daily',
    total_recs      INTEGER DEFAULT 0,
    evaluated_recs  INTEGER DEFAULT 0,
    hits            INTEGER DEFAULT 0,
    hit_rate        REAL    DEFAULT 0.0,
    avg_return_5d   REAL    DEFAULT 0.0,
    avg_return_10d  REAL    DEFAULT 0.0,
    avg_return_20d  REAL    DEFAULT 0.0,
    best_trade      TEXT    DEFAULT '',
    worst_trade     TEXT    DEFAULT '',
    strength_note   TEXT    DEFAULT '',
    weakness_note   TEXT    DEFAULT '',
    weight_adj      REAL    DEFAULT 1.0,
    calculated_at   TEXT    NOT NULL,
    UNIQUE(manager_key, period, calculated_at)
);

CREATE INDEX IF NOT EXISTS idx_mgr_scorecard ON manager_scorecard(manager_key, calculated_at);

-- v9.5.3: 사용자 매매 프로필 (주호님 매매 패턴 학습)
CREATE TABLE IF NOT EXISTS user_trade_profile (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key     TEXT    UNIQUE NOT NULL,
    profile_value   TEXT    DEFAULT '',
    updated_at      TEXT    NOT NULL
);

-- v9.5.3: 이벤트 → 전략 점수 반영 (글로벌 이벤트가 실제 점수에 영향)
CREATE TABLE IF NOT EXISTS event_score_adjustments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    event_summary   TEXT    DEFAULT '',
    affected_sectors TEXT   DEFAULT '[]',
    affected_tickers TEXT   DEFAULT '[]',
    score_adjustment INTEGER DEFAULT 0,
    confidence      REAL    DEFAULT 0.5,
    expires_at      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_adj_expires ON event_score_adjustments(expires_at);

-- v9.5.4: 섹터 딥다이브 리서치 리포트 (AI 생성, 캐시)
CREATE TABLE IF NOT EXISTS sector_deep_dive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_key      TEXT    NOT NULL,
    report_json     TEXT    NOT NULL,
    data_sources    TEXT    DEFAULT '{}',
    created_at      TEXT    NOT NULL,
    UNIQUE(sector_key, created_at)
);

CREATE INDEX IF NOT EXISTS idx_sector_dive_key ON sector_deep_dive(sector_key, created_at);
"""


class StoreBase:
    """DB 연결, 스키마 초기화, job_runs 메서드를 제공하는 기반 클래스."""

    _thread_pool: ThreadPoolExecutor | None = None

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── async executor (동기 DB 호출을 이벤트루프 블로킹 없이 실행) ──
    async def run_in_executor(self, fn: Callable[..., T], *args: Any) -> T:
        """Run a sync DB method in a thread pool to avoid blocking the event loop.

        Usage::

            result = await db.run_in_executor(db.get_active_holdings)
            result = await db.run_in_executor(db.add_holding, ticker, name, ...)
        """
        if StoreBase._thread_pool is None:
            StoreBase._thread_pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="db"
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            StoreBase._thread_pool, functools.partial(fn, *args)
        )

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
            # Migrate: add quantity/eval_amount/holding_type to holdings table
            for col, sql in [
                ("quantity", "ALTER TABLE holdings ADD COLUMN quantity INTEGER DEFAULT 0"),
                ("eval_amount", "ALTER TABLE holdings ADD COLUMN eval_amount REAL DEFAULT 0"),
                ("holding_type", "ALTER TABLE holdings ADD COLUMN holding_type TEXT DEFAULT 'auto'"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM holdings LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass
            # Migrate: watchlist — rec_price, horizon, manager
            for col, sql in [
                ("rec_price", "ALTER TABLE watchlist ADD COLUMN rec_price REAL DEFAULT 0"),
                ("horizon", "ALTER TABLE watchlist ADD COLUMN horizon TEXT DEFAULT ''"),
                ("manager", "ALTER TABLE watchlist ADD COLUMN manager TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM watchlist LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass
            # Migrate: watchlist — sector 컬럼 (v8.4 종목 관리 대시보드)
            for col, sql in [
                ("sector", "ALTER TABLE watchlist ADD COLUMN sector TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM watchlist LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass
            # Migrate: trades — manager, holding_type (매니저 성과 추적)
            for col, sql in [
                ("manager", "ALTER TABLE trades ADD COLUMN manager TEXT DEFAULT ''"),
                ("holding_type", "ALTER TABLE trades ADD COLUMN holding_type TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM trades LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass
            # Migrate: recommendations — manager (매니저 추천 추적)
            for col, sql in [
                ("manager", "ALTER TABLE recommendations ADD COLUMN manager TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM recommendations LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass
            # Migrate: trade_lessons — manager (매니저별 교훈)
            for col, sql in [
                ("manager", "ALTER TABLE trade_lessons ADD COLUMN manager TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM trade_lessons LIMIT 1")
                except sqlite3.OperationalError:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass
            # Migrate: consensus.ticker UNIQUE 인덱스 추가 (ON CONFLICT 지원)
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_consensus_ticker "
                    "ON consensus(ticker)"
                )
            except sqlite3.OperationalError:
                pass
            # Migrate: global_news — content_summary, video_id (v8.2)
            for col, sql in [
                ("content_summary", "ALTER TABLE global_news ADD COLUMN content_summary TEXT DEFAULT ''"),
                ("video_id", "ALTER TABLE global_news ADD COLUMN video_id TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"SELECT {col} FROM global_news LIMIT 1")
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
