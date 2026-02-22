"""Parquet-based storage for OHLCV data in data/lake/."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


DEFAULT_LAKE_DIR = Path("data/lake")


class ParquetStore:
    """Read/write OHLCV DataFrames as Parquet files."""

    def __init__(self, lake_dir: Path = DEFAULT_LAKE_DIR) -> None:
        self.lake_dir = lake_dir
        self.lake_dir.mkdir(parents=True, exist_ok=True)

    def _ticker_path(self, ticker: str) -> Path:
        """Return the Parquet file path for a given ticker."""
        return self.lake_dir / f"{ticker}.parquet"

    def save(self, ticker: str, df: pd.DataFrame) -> Path:
        """Save a DataFrame as a Parquet file.

        Args:
            ticker: Stock ticker code.
            df: DataFrame with OHLCV columns (date, open, high, low, close, volume).

        Returns:
            Path to the saved Parquet file.
        """
        path = self._ticker_path(ticker)
        df.to_parquet(path, index=False, engine="pyarrow")
        return path

    def load(self, ticker: str) -> pd.DataFrame | None:
        """Load a Parquet file as a DataFrame.

        Args:
            ticker: Stock ticker code.

        Returns:
            DataFrame if file exists, None otherwise.
        """
        path = self._ticker_path(ticker)
        if not path.exists():
            return None
        return pd.read_parquet(path, engine="pyarrow")

    def exists(self, ticker: str) -> bool:
        """Check if Parquet data exists for a ticker."""
        return self._ticker_path(ticker).exists()

    def list_tickers(self) -> list[str]:
        """List all tickers with stored data."""
        return [p.stem for p in self.lake_dir.glob("*.parquet")]

    def append(self, ticker: str, new_df: pd.DataFrame) -> Path:
        """Append new rows to existing data, deduplicating by date.

        Args:
            ticker: Stock ticker code.
            new_df: New OHLCV data to append.

        Returns:
            Path to the saved Parquet file.
        """
        existing = self.load(ticker)
        if existing is not None:
            combined = pd.concat([existing, new_df], ignore_index=True)
            if "date" in combined.columns:
                combined = combined.drop_duplicates(subset=["date"], keep="last")
                combined = combined.sort_values("date").reset_index(drop=True)
        else:
            combined = new_df
        return self.save(ticker, combined)
