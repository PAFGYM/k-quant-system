"""Tests for kstock.ml.feature_store — SQLite feature store."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from kstock.ml.feature_store import (
    FeatureRecord,
    FeatureSet,
    FeatureStats,
    FeatureStore,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store():
    """In-memory feature store for isolated tests."""
    s = FeatureStore(db_path=":memory:", ttl_days=30)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


class TestAddAndGet:
    def test_add_and_get_single(self, store: FeatureStore):
        store.add_feature("005930", "2025-03-01", "rsi_14", 62.5, category="technical")
        fs = store.get_features("005930", "2025-03-01")

        assert isinstance(fs, FeatureSet)
        assert fs.features["rsi_14"] == pytest.approx(62.5)
        assert fs.categories["rsi_14"] == "technical"
        assert fs.is_complete is True

    def test_get_empty(self, store: FeatureStore):
        """Querying a non-existent ticker should return an empty FeatureSet."""
        fs = store.get_features("999999", "2025-01-01")

        assert fs.features == {}
        assert fs.is_complete is False


class TestBatchInsert:
    def test_batch_100(self, store: FeatureStore):
        """Insert 100 records and verify count."""
        records = [
            FeatureRecord(
                ticker="005930",
                date=f"2025-01-{(i % 28) + 1:02d}",
                feature_name=f"feat_{i}",
                value=float(i),
                category="technical",
            )
            for i in range(100)
        ]
        count = store.add_features_batch(records)
        assert count == 100

        stats = store.get_stats()
        assert stats.total_records == 100

    def test_batch_empty(self, store: FeatureStore):
        assert store.add_features_batch([]) == 0


class TestOverwrite:
    def test_same_key_overwrites(self, store: FeatureStore):
        """INSERT OR REPLACE should keep the latest value."""
        store.add_feature("005930", "2025-03-01", "rsi_14", 60.0)
        store.add_feature("005930", "2025-03-01", "rsi_14", 75.0)

        fs = store.get_features("005930", "2025-03-01")
        assert fs.features["rsi_14"] == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# History & cross-section
# ---------------------------------------------------------------------------


class TestHistory:
    def test_date_ordered(self, store: FeatureStore):
        """History should return results sorted by date ascending."""
        for day in range(1, 11):
            store.add_feature(
                "005930",
                f"2025-03-{day:02d}",
                "rsi_14",
                50.0 + day,
            )

        history = store.get_feature_history(
            "005930", "rsi_14", "2025-03-01", "2025-03-10"
        )
        assert len(history) == 10
        dates = [h[0] for h in history]
        assert dates == sorted(dates)
        assert history[0][1] == pytest.approx(51.0)
        assert history[-1][1] == pytest.approx(60.0)


class TestCrossSection:
    def test_five_tickers(self, store: FeatureStore):
        """Cross-section should return all tickers for a given date & feature."""
        tickers = ["005930", "000660", "035420", "051910", "006400"]
        for i, tk in enumerate(tickers):
            store.add_feature(tk, "2025-03-01", "rsi_14", 50.0 + i)

        cs = store.get_cross_section("2025-03-01", "rsi_14")
        assert len(cs) == 5
        assert cs["005930"] == pytest.approx(50.0)
        assert cs["006400"] == pytest.approx(54.0)


# ---------------------------------------------------------------------------
# Cleanup & stats
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_removes_old_keeps_new(self, store: FeatureStore):
        """Stale records should be deleted; fresh ones retained."""
        old_date = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
        new_date = datetime.utcnow().strftime("%Y-%m-%d")

        store.add_feature("005930", old_date, "rsi_14", 50.0)
        store.add_feature("005930", new_date, "rsi_14", 65.0)

        deleted = store.cleanup_stale(ttl_days=30)
        assert deleted == 1

        # Old one gone
        fs_old = store.get_features("005930", old_date)
        assert fs_old.features == {}

        # New one survives
        fs_new = store.get_features("005930", new_date)
        assert fs_new.features["rsi_14"] == pytest.approx(65.0)


class TestStats:
    def test_correct_counts(self, store: FeatureStore):
        tickers = ["005930", "000660", "035420"]
        features = ["rsi_14", "macd", "bb_pctb"]

        for tk in tickers:
            for feat in features:
                store.add_feature(tk, "2025-03-01", feat, 50.0)

        stats = store.get_stats()
        assert isinstance(stats, FeatureStats)
        assert stats.total_records == 9
        assert stats.unique_tickers == 3
        assert stats.unique_features == 3
        assert stats.date_range == ("2025-03-01", "2025-03-01")


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatible:
    def test_module_level_api(self):
        """Module-level add_feature / get_features should work."""
        import kstock.ml.feature_store as fs_mod

        # Patch the default store to use in-memory DB
        fs_mod._default_store = FeatureStore(db_path=":memory:")

        try:
            fs_mod.add_feature("005930", "2025-03-01", "rsi_14", 70.0)
            result = fs_mod.get_features("005930", "2025-03-01")

            assert isinstance(result, dict)
            assert result["rsi_14"] == pytest.approx(70.0)
        finally:
            fs_mod._default_store.close()
            fs_mod._default_store = None
