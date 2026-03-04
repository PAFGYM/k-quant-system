"""SQLite persistence for runtime state that must survive restarts."""
import json, logging, sqlite3
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = Path("data/kquant_state.db")

def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_tables():
    """Create tables if not exist."""
    conn = _get_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            action TEXT,  -- 'buy' or 'sell'
            price REAL,
            quantity INTEGER,
            strategy TEXT,
            signal_score REAL,
            market_regime TEXT,
            vix_at_entry REAL,
            kelly_fraction REAL,
            reason TEXT,
            sell_reason TEXT,
            pnl_pct REAL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS trailing_stop_state (
            ticker TEXT PRIMARY KEY,
            peak_price REAL NOT NULL,
            entry_price REAL,
            stop_pct REAL,
            last_updated TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS signal_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            ticker TEXT,
            signal_type TEXT,  -- 'BUY','SELL','HOLD'
            signal_score REAL,
            outcome_pnl_pct REAL,
            hit INTEGER,  -- 1=win, 0=loss
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS kelly_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            win_rate REAL,
            avg_win REAL,
            avg_loss REAL,
            kelly_fraction REAL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """)
        conn.commit()
        logger.info("Persistence tables initialized at %s", DB_PATH)
    finally:
        conn.close()

# --- Trade Journal ---
def save_journal_entry(ticker, name, action, price, quantity=0, strategy="",
                       signal_score=0, market_regime="", vix=0, kelly=0,
                       reason="", sell_reason="", pnl_pct=0):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO trade_journal (ticker,name,action,price,quantity,strategy,"
            "signal_score,market_regime,vix_at_entry,kelly_fraction,reason,sell_reason,pnl_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, name, action, price, quantity, strategy, signal_score,
             market_regime, vix, kelly, reason, sell_reason, pnl_pct)
        )
        conn.commit()
    finally:
        conn.close()

def load_journal_entries(limit=100):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM trade_journal ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM trade_journal LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()

# --- Trailing Stop ---
def save_trailing_stop(ticker, peak_price, entry_price=0, stop_pct=0.07):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO trailing_stop_state (ticker,peak_price,entry_price,stop_pct,last_updated) "
            "VALUES (?,?,?,?,datetime('now','localtime'))",
            (ticker, peak_price, entry_price, stop_pct)
        )
        conn.commit()
    finally:
        conn.close()

def load_trailing_stops():
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT ticker,peak_price,entry_price,stop_pct FROM trailing_stop_state").fetchall()
        return {r[0]: {"peak_price": r[1], "entry_price": r[2], "stop_pct": r[3]} for r in rows}
    finally:
        conn.close()

def delete_trailing_stop(ticker):
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM trailing_stop_state WHERE ticker=?", (ticker,))
        conn.commit()
    finally:
        conn.close()

# --- Signal Quality ---
def save_signal_quality(strategy, ticker, signal_type, signal_score, outcome_pnl_pct=0, hit=0):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO signal_quality (strategy,ticker,signal_type,signal_score,outcome_pnl_pct,hit) "
            "VALUES (?,?,?,?,?,?)",
            (strategy, ticker, signal_type, signal_score, outcome_pnl_pct, hit)
        )
        conn.commit()
    finally:
        conn.close()

def load_signal_quality(strategy=None, limit=200):
    conn = _get_conn()
    try:
        if strategy:
            rows = conn.execute(
                "SELECT * FROM signal_quality WHERE strategy=? ORDER BY id DESC LIMIT ?",
                (strategy, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM signal_quality ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM signal_quality LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()

def get_strategy_stats():
    """Get per-strategy hit rate and average PnL."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT strategy,
                   COUNT(*) as total,
                   SUM(hit) as wins,
                   ROUND(AVG(outcome_pnl_pct), 2) as avg_pnl,
                   ROUND(CAST(SUM(hit) AS REAL) / COUNT(*) * 100, 1) as hit_rate
            FROM signal_quality
            WHERE outcome_pnl_pct IS NOT NULL
            GROUP BY strategy
            ORDER BY hit_rate DESC
        """).fetchall()
        return [{"strategy": r[0], "total": r[1], "wins": r[2], "avg_pnl": r[3], "hit_rate": r[4]} for r in rows]
    finally:
        conn.close()

# --- Kelly History ---
def save_kelly_history(ticker, win_rate, avg_win, avg_loss, kelly_fraction):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO kelly_history (ticker,win_rate,avg_win,avg_loss,kelly_fraction) VALUES (?,?,?,?,?)",
            (ticker, win_rate, avg_win, avg_loss, kelly_fraction)
        )
        conn.commit()
    finally:
        conn.close()

def load_kelly_history(ticker, limit=30):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT win_rate,avg_win,avg_loss,kelly_fraction,created_at FROM kelly_history "
            "WHERE ticker=? ORDER BY id DESC LIMIT ?", (ticker, limit)
        ).fetchall()
        return [{"win_rate": r[0], "avg_win": r[1], "avg_loss": r[2],
                 "kelly": r[3], "date": r[4]} for r in rows]
    finally:
        conn.close()
