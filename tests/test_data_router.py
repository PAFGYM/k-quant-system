"""Tests for data source router."""

from __future__ import annotations

import pytest

from kstock.ingest.data_router import DataRouter, DataSource


# ---------------------------------------------------------------------------
# Helpers / mock objects
# ---------------------------------------------------------------------------

class FakeKISBroker:
    """Minimal stand-in for a KIS broker object."""

    def __init__(self, *, connected: bool = True, mode: str = "virtual") -> None:
        self.connected = connected
        self.mode = mode


class FakeDB:
    """Minimal stand-in for a database object."""


class FakeYFClient:
    """Minimal stand-in for a yfinance client object."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kis_connected():
    return FakeKISBroker(connected=True, mode="virtual")


@pytest.fixture
def kis_disconnected():
    return FakeKISBroker(connected=False, mode="virtual")


@pytest.fixture
def kis_real():
    return FakeKISBroker(connected=True, mode="real")


@pytest.fixture
def yf_client():
    return FakeYFClient()


@pytest.fixture
def db():
    return FakeDB()


# ---------------------------------------------------------------------------
# DataSource dataclass
# ---------------------------------------------------------------------------

class TestDataSource:
    def test_defaults(self):
        ds = DataSource(name="yfinance")
        assert ds.name == "yfinance"
        assert ds.connected is False

    def test_explicit_connected(self):
        ds = DataSource(name="kis", connected=True)
        assert ds.name == "kis"
        assert ds.connected is True

    def test_equality(self):
        a = DataSource(name="kis", connected=True)
        b = DataSource(name="kis", connected=True)
        assert a == b

    def test_inequality_name(self):
        a = DataSource(name="kis", connected=True)
        b = DataSource(name="yfinance", connected=True)
        assert a != b


# ---------------------------------------------------------------------------
# DataRouter initialisation
# ---------------------------------------------------------------------------

class TestDataRouterInit:
    def test_no_kis_source_is_yfinance(self, yf_client, db):
        """Without a KIS broker the router should fall back to yfinance."""
        router = DataRouter(kis_broker=None, yf_client=yf_client, db=db)
        assert router.source_name == "yfinance"

    def test_disconnected_kis_source_is_yfinance(self, kis_disconnected, yf_client, db):
        """A KIS broker that is not connected should still fall back."""
        router = DataRouter(kis_broker=kis_disconnected, yf_client=yf_client, db=db)
        assert router.source_name == "yfinance"

    def test_connected_kis_source_is_kis(self, kis_connected, yf_client, db):
        """A connected KIS broker should be selected as the source."""
        router = DataRouter(kis_broker=kis_connected, yf_client=yf_client, db=db)
        assert router.source_name == "kis"

    def test_all_none_params(self):
        """Router should work even with all params set to None."""
        router = DataRouter(kis_broker=None, yf_client=None, db=None)
        assert router.source_name == "yfinance"
        assert router.kis_connected is False

    def test_db_only(self, db):
        """Router with only a database should fall back to yfinance."""
        router = DataRouter(db=db)
        assert router.source_name == "yfinance"
        assert router.db is db

    def test_stores_references(self, kis_connected, yf_client, db):
        """Router should store the injected dependencies."""
        router = DataRouter(kis_broker=kis_connected, yf_client=yf_client, db=db)
        assert router.kis is kis_connected
        assert router.yf is yf_client
        assert router.db is db


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestSourceNameProperty:
    def test_returns_kis(self, kis_connected):
        router = DataRouter(kis_broker=kis_connected)
        assert router.source_name == "kis"

    def test_returns_yfinance(self):
        router = DataRouter()
        assert router.source_name == "yfinance"


class TestKisConnectedProperty:
    def test_true_when_connected(self, kis_connected):
        router = DataRouter(kis_broker=kis_connected)
        assert router.kis_connected is True

    def test_false_when_disconnected(self, kis_disconnected):
        router = DataRouter(kis_broker=kis_disconnected)
        assert router.kis_connected is False

    def test_false_when_no_broker(self):
        router = DataRouter()
        assert router.kis_connected is False


# ---------------------------------------------------------------------------
# refresh_source
# ---------------------------------------------------------------------------

class TestRefreshSource:
    def test_redetects_after_connection_change(self, kis_connected):
        """If the broker becomes disconnected, refresh should pick it up."""
        router = DataRouter(kis_broker=kis_connected)
        assert router.source_name == "kis"

        # Simulate broker losing connection
        kis_connected.connected = False
        result = router.refresh_source()

        assert result == "yfinance"
        assert router.source_name == "yfinance"

    def test_redetects_after_reconnection(self, kis_disconnected):
        """If a disconnected broker reconnects, refresh should detect it."""
        router = DataRouter(kis_broker=kis_disconnected)
        assert router.source_name == "yfinance"

        # Simulate broker reconnecting
        kis_disconnected.connected = True
        result = router.refresh_source()

        assert result == "kis"
        assert router.source_name == "kis"

    def test_returns_source_name(self, kis_connected):
        router = DataRouter(kis_broker=kis_connected)
        name = router.refresh_source()
        assert isinstance(name, str)
        assert name == "kis"


# ---------------------------------------------------------------------------
# format_source_status
# ---------------------------------------------------------------------------

class TestFormatSourceStatus:
    def test_kis_virtual_mode(self, kis_connected):
        router = DataRouter(kis_broker=kis_connected)
        status = router.format_source_status()
        assert "KIS API" in status
        assert "\ubaa8\uc758\ud22c\uc790" in status  # "모의투자"

    def test_kis_real_mode(self, kis_real):
        router = DataRouter(kis_broker=kis_real)
        status = router.format_source_status()
        assert "KIS API" in status
        assert "\uc2e4\uc804" in status  # "실전"

    def test_yfinance_fallback(self):
        router = DataRouter()
        status = router.format_source_status()
        assert "yfinance" in status

    def test_kis_without_mode_attribute(self):
        """Broker without a mode attr should default to virtual."""
        class BareKIS:
            connected = True

        router = DataRouter(kis_broker=BareKIS())
        status = router.format_source_status()
        assert "\ubaa8\uc758\ud22c\uc790" in status  # defaults to "모의투자"


# ---------------------------------------------------------------------------
# get_connection_message
# ---------------------------------------------------------------------------

class TestGetConnectionMessage:
    def test_connected_message(self, kis_connected):
        router = DataRouter(kis_broker=kis_connected)
        msg = router.get_connection_message()
        assert "\uc5f0\uacb0 \uc644\ub8cc" in msg  # "연결 완료"
        assert "\uc2e4\uc2dc\uac04" in msg  # "실시간"

    def test_disconnected_message(self):
        router = DataRouter()
        msg = router.get_connection_message()
        assert "\ub04a\uacbc\uc5b4\uc694" in msg  # "끊겼어요"
        assert "\uc804\ud658" in msg  # "전환"

    def test_disconnected_broker_message(self, kis_disconnected):
        router = DataRouter(kis_broker=kis_disconnected)
        msg = router.get_connection_message()
        assert "\ub04a\uacbc\uc5b4\uc694" in msg  # "끊겼어요"
