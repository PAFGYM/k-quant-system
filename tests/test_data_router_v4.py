"""DataRouter v4.0 테스트 — Naver Finance 폴백 + 3-tier 가격 조회."""
import pytest
from kstock.ingest.data_router import DataRouter, DataSource


def test_data_source_dataclass():
    ds = DataSource(name="yfinance", connected=True)
    assert ds.name == "yfinance"
    assert ds.connected


def test_router_init_no_args():
    router = DataRouter()
    assert router.source_name == "yfinance"
    assert not router.kis_connected


def test_router_format_status():
    router = DataRouter()
    status = router.format_source_status()
    assert "yfinance" in status


def test_router_fallback_count():
    router = DataRouter()
    assert router._fallback_count == 0


def test_naver_lazy_init():
    router = DataRouter()
    # First call creates client
    client = router._get_naver_client()
    assert client is not None
    # Second call returns same client
    client2 = router._get_naver_client()
    assert client is client2


def test_router_get_stock_info_empty():
    """No data sources → default empty dict."""
    router = DataRouter()
    import asyncio
    info = asyncio.get_event_loop().run_until_complete(
        router.get_stock_info("999999", "테스트종목")
    )
    assert info["ticker"] == "999999"
    assert info["name"] == "테스트종목"


def test_router_connection_message():
    router = DataRouter()
    msg = router.get_connection_message()
    assert "KIS" in msg


def test_router_refresh_source():
    router = DataRouter()
    result = router.refresh_source()
    assert result == "yfinance"
