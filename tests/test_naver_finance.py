"""Naver Finance 클라이언트 테스트 (파싱 로직 중심)."""
import pandas as pd

from kstock.ingest.naver_finance import (
    NaverFinanceClient, _parse_current_price, _parse_sise_json,
    _parse_main_page, _to_float,
)


def test_to_float():
    assert _to_float("75,000") == 75000.0
    assert _to_float("1,234,567") == 1234567.0
    assert _to_float("") == 0.0
    assert _to_float("abc") == 0.0


def test_parse_current_price_blind():
    html = '''
    <p class="no_today">
        <span class="blind">75,000</span>
    </p>
    '''
    assert _parse_current_price(html) == 75000.0


def test_parse_current_price_empty():
    assert _parse_current_price("") == 0.0


def test_parse_sise_json_empty():
    df = _parse_sise_json("")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_parse_sise_json_valid():
    text = '''
    ["날짜","시가","고가","저가","종가","거래량"]
    ["20260225","75000","76000","74500","75500","12345678"]
    ["20260226","75500","77000","75000","76500","9876543"]
    '''
    df = _parse_sise_json(text)
    assert len(df) == 2
    assert "date" in df.columns
    assert "close" in df.columns
    assert df.iloc[0]["close"] == 75500.0


def test_parse_sise_json_header_only():
    text = '["날짜","시가","고가","저가","종가","거래량"]'
    df = _parse_sise_json(text)
    assert df.empty


def test_parse_main_page_empty():
    result = _parse_main_page("")
    assert isinstance(result, dict)


def test_naver_client_init():
    client = NaverFinanceClient()
    assert isinstance(client._failed_tickers, set)


def test_data_router_naver_integration():
    """DataRouter가 Naver 폴백을 초기화하는지 확인."""
    from kstock.ingest.data_router import DataRouter
    router = DataRouter()
    naver = router._get_naver_client()
    assert naver is not None
    assert isinstance(naver, NaverFinanceClient)
