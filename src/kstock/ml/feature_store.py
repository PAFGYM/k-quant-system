"""Centralised feature store backed by SQLite.

Stores per-ticker, per-date feature values with category & source metadata.
Supports batch writes, history queries, cross-section lookups, and TTL cleanup.

Backwards-compatible module-level ``add_feature`` / ``get_features`` are
provided so existing call sites continue to work without changes.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FeatureRecord:
    """A single feature measurement."""

    ticker: str
    date: str
    feature_name: str
    value: float
    category: str = ""  # technical / fundamental / sentiment / factor
    source: str = ""
    computed_at: str = ""


@dataclass
class FeatureSet:
    """All features for one ticker on one date."""

    ticker: str
    date: str
    features: dict = field(default_factory=dict)        # {name: value}
    categories: dict = field(default_factory=dict)       # {name: category}
    sources: dict = field(default_factory=dict)          # {name: source}
    is_complete: bool = False


@dataclass
class FeatureStats:
    """Aggregate statistics for the feature store."""

    total_records: int = 0
    unique_tickers: int = 0
    unique_features: int = 0
    date_range: tuple = ("", "")
    stale_records: int = 0


# ---------------------------------------------------------------------------
# FeatureStore
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS features (
    ticker       TEXT    NOT NULL,
    date         TEXT    NOT NULL,
    feature_name TEXT    NOT NULL,
    value        REAL    NOT NULL,
    category     TEXT    DEFAULT '',
    source       TEXT    DEFAULT '',
    computed_at  TEXT    DEFAULT '',
    PRIMARY KEY (ticker, date, feature_name)
)
"""

_CREATE_IDX_TICKER_DATE = (
    "CREATE INDEX IF NOT EXISTS idx_features_ticker_date "
    "ON features(ticker, date)"
)
_CREATE_IDX_DATE = (
    "CREATE INDEX IF NOT EXISTS idx_features_date ON features(date)"
)


class FeatureStore:
    """SQLite-backed feature store.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database file.  Use ``":memory:"`` for testing.
    ttl_days : int
        Default time-to-live for cleanup operations.
    """

    def __init__(self, db_path: str | Path = "data/features.db", ttl_days: int = 365):
        self.db_path = str(db_path)
        self.ttl_days = ttl_days
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # -- lifecycle -----------------------------------------------------------

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_IDX_TICKER_DATE)
        self._conn.execute(_CREATE_IDX_DATE)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._init_db()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        """Close the underlying database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- write operations ----------------------------------------------------

    def add_feature(
        self,
        ticker: str,
        date: str,
        name: str,
        value: float,
        category: str = "",
        source: str = "",
    ) -> None:
        """Insert or replace a single feature value."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR REPLACE INTO features "
            "(ticker, date, feature_name, value, category, source, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, date, name, value, category, source, now),
        )
        self.conn.commit()

    def add_features_batch(self, records: List[FeatureRecord]) -> int:
        """Insert or replace a batch of feature records.

        Returns the number of records written.
        """
        if not records:
            return 0

        now = datetime.utcnow().isoformat(timespec="seconds")
        rows = [
            (
                r.ticker,
                r.date,
                r.feature_name,
                r.value,
                r.category,
                r.source,
                r.computed_at or now,
            )
            for r in records
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO features "
            "(ticker, date, feature_name, value, category, source, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # -- read operations -----------------------------------------------------

    def get_features(self, ticker: str, date: str) -> FeatureSet:
        """Retrieve all features for a ticker on a given date."""
        cur = self.conn.execute(
            "SELECT feature_name, value, category, source "
            "FROM features WHERE ticker = ? AND date = ?",
            (ticker, date),
        )
        rows = cur.fetchall()

        fs = FeatureSet(ticker=ticker, date=date)
        for name, value, category, source in rows:
            fs.features[name] = value
            fs.categories[name] = category
            fs.sources[name] = source

        fs.is_complete = len(fs.features) > 0
        return fs

    def get_feature_history(
        self,
        ticker: str,
        feature_name: str,
        start_date: str,
        end_date: str,
    ) -> List[Tuple[str, float]]:
        """Return ``(date, value)`` pairs for a single feature over a date range.

        Results are ordered by date ascending.
        """
        cur = self.conn.execute(
            "SELECT date, value FROM features "
            "WHERE ticker = ? AND feature_name = ? AND date BETWEEN ? AND ? "
            "ORDER BY date ASC",
            (ticker, feature_name, start_date, end_date),
        )
        return cur.fetchall()

    def get_cross_section(self, date: str, feature_name: str) -> dict:
        """Return ``{ticker: value}`` for all tickers on a given date."""
        cur = self.conn.execute(
            "SELECT ticker, value FROM features "
            "WHERE date = ? AND feature_name = ?",
            (date, feature_name),
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    # -- v10.1: training data helpers ----------------------------------------

    def get_available_dates(self, before: str = "", limit: int = 60) -> list[str]:
        """feature_store에 기록된 고유 날짜 목록 (최신순).

        Args:
            before: 이 날짜 이전만 반환 (빈 문자열이면 전체).
            limit: 최대 반환 개수.
        """
        if before:
            cur = self.conn.execute(
                "SELECT DISTINCT date FROM features "
                "WHERE date < ? ORDER BY date DESC LIMIT ?",
                (before, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT DISTINCT date FROM features "
                "ORDER BY date DESC LIMIT ?",
                (limit,),
            )
        return [row[0] for row in cur.fetchall()]

    def get_tickers_for_date(self, date_str: str) -> list[str]:
        """특정 날짜에 피처가 저장된 종목 코드 목록."""
        cur = self.conn.execute(
            "SELECT DISTINCT ticker FROM features WHERE date = ?",
            (date_str,),
        )
        return [row[0] for row in cur.fetchall()]

    def get_features_dict(self, ticker: str, date_str: str) -> dict[str, float]:
        """종목+날짜의 피처를 {name: value} dict로 반환 (학습 데이터용)."""
        cur = self.conn.execute(
            "SELECT feature_name, value FROM features "
            "WHERE ticker = ? AND date = ?",
            (ticker, date_str),
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    # -- maintenance ---------------------------------------------------------

    def cleanup_stale(self, ttl_days: int | None = None) -> int:
        """Delete records older than *ttl_days*.  Returns deleted count."""
        ttl = ttl_days if ttl_days is not None else self.ttl_days
        cutoff = (datetime.utcnow() - timedelta(days=ttl)).strftime("%Y-%m-%d")
        cur = self.conn.execute(
            "DELETE FROM features WHERE date < ?", (cutoff,)
        )
        self.conn.commit()
        return cur.rowcount

    def get_stats(self) -> FeatureStats:
        """Return aggregate statistics about the store."""
        c = self.conn

        total = c.execute("SELECT COUNT(*) FROM features").fetchone()[0]
        tickers = c.execute(
            "SELECT COUNT(DISTINCT ticker) FROM features"
        ).fetchone()[0]
        features = c.execute(
            "SELECT COUNT(DISTINCT feature_name) FROM features"
        ).fetchone()[0]

        row = c.execute(
            "SELECT MIN(date), MAX(date) FROM features"
        ).fetchone()
        date_range = (row[0] or "", row[1] or "")

        cutoff = (
            datetime.utcnow() - timedelta(days=self.ttl_days)
        ).strftime("%Y-%m-%d")
        stale = c.execute(
            "SELECT COUNT(*) FROM features WHERE date < ?", (cutoff,)
        ).fetchone()[0]

        return FeatureStats(
            total_records=total,
            unique_tickers=tickers,
            unique_features=features,
            date_range=date_range,
            stale_records=stale,
        )


# ---------------------------------------------------------------------------
# Backward-compatible module-level API
# ---------------------------------------------------------------------------

_default_store: FeatureStore | None = None


def _get_store() -> FeatureStore:
    """Lazily initialise the default feature store singleton."""
    global _default_store
    if _default_store is None:
        _default_store = FeatureStore()
    return _default_store


def add_feature(ticker: str, date: str, name: str, value: float) -> None:
    """Store a feature value (backward-compatible wrapper)."""
    _get_store().add_feature(ticker, date, name, float(value))


def get_features(ticker: str, date: str) -> dict:
    """Retrieve all features for a ticker on a date (backward-compatible wrapper).

    Returns ``{feature_name: value}`` dict.
    """
    fs = _get_store().get_features(ticker, date)
    return fs.features
