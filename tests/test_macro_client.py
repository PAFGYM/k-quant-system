from __future__ import annotations

import asyncio
import sqlite3

import numpy as np
import pandas as pd

from kstock.bot.learning_engine import apply_event_to_strategy
from kstock.ingest.macro_client import MacroClient


def _multi_df(payload: dict[str, list[float]]) -> pd.DataFrame:
    rows = max(len(v) for v in payload.values())
    cols = []
    data = {}
    for ticker, values in payload.items():
        cols.append((ticker, "Close"))
        padded = values + [values[-1]] * (rows - len(values))
        data[(ticker, "Close")] = padded
    return pd.DataFrame(data, columns=pd.MultiIndex.from_tuples(cols))


def test_macro_client_survives_partial_yfinance_payload(monkeypatch) -> None:
    client = MacroClient(db=None)
    client._cached_snapshot = client._generate_mock_snapshot()

    partial = _multi_df(
        {
            "^VIX": [26.0, 27.0],
            "KRW=X": [1488.0, 1492.0],
            "^KS11": [2580.0, 2595.0],
        }
    )

    monkeypatch.setattr("kstock.ingest.macro_client.yf.download", lambda *a, **k: partial)

    snap = client._fetch_live_snapshot()

    assert snap.vix == 27.0
    assert snap.usdkrw == 1492.0
    assert snap.kospi == 2595.0
    # Missing tickers should fall back instead of exploding.
    assert snap.spx_change_pct == client._cached_snapshot.spx_change_pct
    assert snap.ewy_change_pct == 0.0


def test_macro_client_refresh_now_uses_cached_snapshot_on_sparse_index_error(monkeypatch) -> None:
    client = MacroClient(db=None)
    client._cached_snapshot = client._generate_mock_snapshot()
    client._cached_at = client._cached_snapshot.fetched_at

    monkeypatch.setattr(
        client,
        "_fetch_live_snapshot",
        lambda: (_ for _ in ()).throw(IndexError("single positional indexer is out-of-bounds")),
    )

    snap = asyncio.run(client.refresh_now())

    assert snap is not None
    assert snap.is_cached is True
    assert snap.vix == client._cached_snapshot.vix


class _MemoryDB:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE event_score_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                event_summary TEXT,
                affected_sectors TEXT,
                affected_tickers TEXT,
                score_adjustment INTEGER,
                confidence REAL,
                expires_at TEXT,
                created_at TEXT
            )
            """
        )

    def _connect(self):
        return self.conn


def test_apply_event_to_strategy_casts_numpy_values() -> None:
    db = _MemoryDB()

    ok = asyncio.run(
        apply_event_to_strategy(
            db=db,
            event_summary="유가 급등",
            affected_sectors=["정유", "방산"],
            affected_tickers=["010950", "012450"],
            adjustment=np.int64(3),
            confidence=np.float64(0.85),
        )
    )

    row = db.conn.execute(
        "SELECT score_adjustment, confidence, affected_sectors, affected_tickers FROM event_score_adjustments"
    ).fetchone()

    assert ok is True
    assert row["score_adjustment"] == 3
    assert round(float(row["confidence"]), 2) == 0.85
    assert "정유" in row["affected_sectors"]
    assert "012450" in row["affected_tickers"]
