"""SQLite store for metadata, portfolio, holdings, watchlist, and job watermarks.

이 파일은 Mixin 합성 레이어입니다.
실제 구현은 _base.py, _portfolio.py, _trading.py, _market.py, _meta.py에 있습니다.
기존 ``from kstock.store.sqlite import SQLiteStore`` 임포트 호환성을 유지합니다.
"""

from __future__ import annotations

from kstock.store._base import DEFAULT_DB_PATH, StoreBase, _SCHEMA_SQL  # noqa: F401
from kstock.store._market import MarketMixin
from kstock.store._meta import MetaMixin
from kstock.store._portfolio import PortfolioMixin
from kstock.store._trading import TradingMixin


class SQLiteStore(StoreBase, PortfolioMixin, TradingMixin, MarketMixin, MetaMixin):
    """Thin wrapper around SQLite for K-Quant metadata."""

    pass
