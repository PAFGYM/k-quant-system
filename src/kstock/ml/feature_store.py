"""Feature store stub (v4.0 준비).

Will implement centralized feature management in future versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FeatureRecord:
    """A single feature record."""

    ticker: str = ""
    date: str = ""
    features: dict = field(default_factory=dict)


def add_feature(ticker: str, date: str, name: str, value: float) -> None:
    """Store a feature value.

    Stub for v4.0. Currently a no-op.
    """


def get_features(ticker: str, date: str) -> dict:
    """Retrieve all features for a ticker on a date.

    Stub for v4.0. Currently returns empty dict.
    """
    return {}
