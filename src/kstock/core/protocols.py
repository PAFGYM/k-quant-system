"""Protocol interfaces for K-Quant data and broker layers.

v12.3: Unified Core 패턴 도입 — 구조적(duck-typing) 프로토콜.
기존 클래스(YFinanceKRClient, NaverFinanceClient, KisBroker)가
수정 없이 이 프로토콜을 자동으로 만족한다.

런타임 동작 변경 없음 — 타입 문서화 + IDE 지원 목적.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import pandas as pd


# ---------------------------------------------------------------------------
# Market Data Provider (Korean stocks)
# ---------------------------------------------------------------------------

@runtime_checkable
class MarketDataProvider(Protocol):
    """한국 주식 시장 데이터 제공자.

    만족하는 클래스:
        - YFinanceKRClient (ingest/yfinance_kr_client.py)
        - NaverFinanceClient (ingest/naver_finance.py)

    DataRouter가 이 프로토콜의 인스턴스에 위임한다.
    """

    async def get_current_price(self, code: str, *args: Any, **kwargs: Any) -> float:
        """현재가 조회 (KRW). 실패 시 0.0."""
        ...

    async def get_ohlcv(
        self, code: str, market: str = "KOSPI", period: str = "6mo",
    ) -> pd.DataFrame:
        """OHLCV DataFrame 조회. 실패 시 빈 DataFrame."""
        ...

    async def get_stock_info(
        self, code: str, *args: Any, **kwargs: Any,
    ) -> dict:
        """종목 기본 정보 dict. 'current_price' 키 포함."""
        ...


# ---------------------------------------------------------------------------
# Broker (Trading)
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    """주문 실행 결과 (KisBroker.buy/sell 반환값과 호환)."""
    success: bool = False
    order_no: str = ""
    message: str = ""
    quantity: int = 0
    price: float = 0.0


@runtime_checkable
class BrokerProtocol(Protocol):
    """증권사 브로커 인터페이스.

    만족하는 클래스:
        - KisBroker (broker/kis_broker.py)
    """

    connected: bool
    mode: str  # "virtual" | "real"

    def get_realtime_price(self, ticker: str) -> float:
        """실시간 현재가."""
        ...

    def get_balance(self) -> dict | None:
        """계좌 잔고 조회."""
        ...

    def buy(
        self, ticker: str, quantity: int, price: int | None = None,
    ) -> Any:
        """매수 주문."""
        ...

    def sell(
        self, ticker: str, quantity: int, price: int | None = None,
    ) -> Any:
        """매도 주문."""
        ...
