"""KIS WebSocket Client — 실시간 호가/체결가 수신.

K-Quant v3.6: 한국투자증권 WebSocket API 연동.
실시간 현재가(체결) + 10단계 호가창 데이터 수신.

Usage:
    ws = KISWebSocket()
    await ws.connect()
    await ws.subscribe("005930")  # 삼성전자 실시간 구독
    data = ws.get_orderbook("005930")  # 호가 데이터 조회
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# KIS WebSocket 엔드포인트
WS_URL_VIRTUAL = "ws://ops.koreainvestment.com:31000"
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"

# TR IDs
TR_REALTIME_PRICE = "H0STCNT0"    # 실시간 체결가
TR_REALTIME_ORDERBOOK = "H0STASP0"  # 실시간 호가 (10단계)


@dataclass
class RealtimePrice:
    """실시간 체결 데이터."""
    ticker: str
    price: float
    change: float
    change_pct: float
    volume: int
    trade_volume: int  # 체결 수량
    trade_time: str    # HH:MM:SS
    bid_price: float   # 매수호가
    ask_price: float   # 매도호가
    total_ask_vol: int  # 총 매도잔량
    total_bid_vol: int  # 총 매수잔량
    updated_at: float = 0.0

    @property
    def spread(self) -> float:
        """호가 스프레드 (매도-매수)."""
        return self.ask_price - self.bid_price if self.ask_price and self.bid_price else 0

    @property
    def spread_pct(self) -> float:
        """호가 스프레드 비율."""
        return (self.spread / self.price * 100) if self.price > 0 else 0

    @property
    def pressure(self) -> str:
        """매수/매도 압력 판단."""
        if self.total_bid_vol > 0 and self.total_ask_vol > 0:
            ratio = self.total_bid_vol / self.total_ask_vol
            if ratio > 1.5:
                return "강한 매수세"
            elif ratio > 1.1:
                return "매수 우위"
            elif ratio < 0.67:
                return "강한 매도세"
            elif ratio < 0.9:
                return "매도 우위"
        return "중립"


@dataclass
class OrderbookLevel:
    """호가 1단계."""
    price: float
    volume: int


@dataclass
class Orderbook:
    """10단계 호가창."""
    ticker: str
    asks: list[OrderbookLevel] = field(default_factory=list)  # 매도호가 (낮→높)
    bids: list[OrderbookLevel] = field(default_factory=list)  # 매수호가 (높→낮)
    total_ask_vol: int = 0
    total_bid_vol: int = 0
    updated_at: float = 0.0

    @property
    def mid_price(self) -> float:
        """중간가."""
        if self.asks and self.bids:
            return (self.asks[0].price + self.bids[0].price) / 2
        return 0

    @property
    def bid_ask_ratio(self) -> float:
        """매수/매도 잔량 비율."""
        if self.total_ask_vol > 0:
            return self.total_bid_vol / self.total_ask_vol
        return 0

    def format_display(self, name: str = "") -> str:
        """텔레그램용 호가창 포맷."""
        header = f"📊 {name} 호가창" if name else "📊 호가창"
        lines = [header, "─" * 28]

        # 매도호가 (위에서 아래로 = 높→낮)
        lines.append("  매도호가         잔량")
        for level in reversed(self.asks[:5]):
            bar = "█" * min(int(level.volume / max(self.total_ask_vol, 1) * 20), 10)
            lines.append(
                f"  🔴 {level.price:>10,.0f}  {level.volume:>8,}  {bar}"
            )

        lines.append("  " + "─" * 26)

        # 매수호가 (위에서 아래로 = 높→낮)
        for level in self.bids[:5]:
            bar = "█" * min(int(level.volume / max(self.total_bid_vol, 1) * 20), 10)
            lines.append(
                f"  🟢 {level.price:>10,.0f}  {level.volume:>8,}  {bar}"
            )
        lines.append("  매수호가         잔량")

        lines.append("─" * 28)

        # 잔량 비교
        ratio = self.bid_ask_ratio
        pressure = "매수 우위 📈" if ratio > 1.1 else "매도 우위 📉" if ratio < 0.9 else "균형 ⚖️"
        lines.append(f"매도잔량: {self.total_ask_vol:,}")
        lines.append(f"매수잔량: {self.total_bid_vol:,}")
        lines.append(f"비율: {ratio:.2f} ({pressure})")

        return "\n".join(lines)


class KISWebSocket:
    """KIS WebSocket 클라이언트 — 실시간 호가/체결가."""

    def __init__(self) -> None:
        self._app_key = os.getenv("KIS_APP_KEY", "")
        self._app_secret = os.getenv("KIS_APP_SECRET", "")
        self._is_virtual = os.getenv("KIS_VIRTUAL", "true").lower() == "true"
        self._approval_key: str = ""
        self._ws = None
        self._connected = False
        self._subscriptions: set[str] = set()
        self._prices: dict[str, RealtimePrice] = {}
        self._orderbooks: dict[str, Orderbook] = {}
        self._callbacks: list[Callable] = []
        self._recv_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def get_approval_key(self) -> str:
        """WebSocket 접속 승인키 발급 (REST API)."""
        if self._approval_key:
            return self._approval_key

        base_url = (
            "https://openapivts.koreainvestment.com:29443"
            if self._is_virtual
            else "https://openapi.koreainvestment.com:9443"
        )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{base_url}/oauth2/Approval",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "secretkey": self._app_secret,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                self._approval_key = data.get("approval_key", "")
                logger.info("KIS WebSocket approval key obtained")
                return self._approval_key
            else:
                logger.error("Failed to get approval key: %d %s", resp.status_code, resp.text[:200])
                return ""

    async def connect(self) -> bool:
        """WebSocket 연결."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets package not installed. pip install websockets")
            return False

        if not self._app_key or not self._app_secret:
            logger.warning("KIS API keys not configured")
            return False

        try:
            approval_key = await self.get_approval_key()
            if not approval_key:
                return False

            ws_url = WS_URL_VIRTUAL if self._is_virtual else WS_URL_REAL
            self._ws = await websockets.connect(ws_url, ping_interval=30)
            self._connected = True
            logger.info("KIS WebSocket connected to %s", ws_url)

            # 수신 루프 시작
            self._recv_task = asyncio.create_task(self._receive_loop())
            return True

        except Exception as e:
            logger.error("WebSocket connection failed: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """WebSocket 연결 해제."""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()
        self._subscriptions.clear()
        logger.info("KIS WebSocket disconnected")

    async def subscribe(self, ticker: str, tr_type: str = "both") -> bool:
        """종목 실시간 구독.

        Args:
            ticker: 종목코드 (6자리)
            tr_type: "price"(체결), "orderbook"(호가), "both"(둘 다)
        """
        if not self._connected or not self._ws:
            logger.warning("WebSocket not connected")
            return False

        success = True
        if tr_type in ("price", "both"):
            success &= await self._send_subscribe(TR_REALTIME_PRICE, ticker)
        if tr_type in ("orderbook", "both"):
            success &= await self._send_subscribe(TR_REALTIME_ORDERBOOK, ticker)

        if success:
            self._subscriptions.add(ticker)
        return success

    async def unsubscribe(self, ticker: str) -> bool:
        """종목 구독 해제."""
        if not self._connected or not self._ws:
            return False
        await self._send_unsubscribe(TR_REALTIME_PRICE, ticker)
        await self._send_unsubscribe(TR_REALTIME_ORDERBOOK, ticker)
        self._subscriptions.discard(ticker)
        return True

    async def _send_subscribe(self, tr_id: str, ticker: str) -> bool:
        """구독 요청 전송."""
        msg = json.dumps({
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "1",  # 1=구독, 2=해제
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": ticker,
                }
            }
        })
        try:
            await self._ws.send(msg)
            logger.debug("Subscribed %s for %s", tr_id, ticker)
            return True
        except Exception as e:
            logger.error("Subscribe failed: %s", e)
            return False

    async def _send_unsubscribe(self, tr_id: str, ticker: str) -> bool:
        """구독 해제 요청."""
        msg = json.dumps({
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": ticker,
                }
            }
        })
        try:
            await self._ws.send(msg)
            return True
        except Exception:
            return False

    async def _receive_loop(self) -> None:
        """WebSocket 데이터 수신 루프."""
        while self._connected and self._ws:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")

                # JSON 응답 (구독 확인 등)
                if raw.startswith("{"):
                    data = json.loads(raw)
                    header = data.get("header", {})
                    tr_id = header.get("tr_id", "")
                    if header.get("tr_type") == "P":
                        logger.debug("WebSocket PING-PONG")
                    elif tr_id:
                        logger.debug("Subscribe confirmed: %s", tr_id)
                    continue

                # 파이프('|') 구분 실시간 데이터
                self._parse_realtime_data(raw)

            except asyncio.TimeoutError:
                # 타임아웃은 정상 — 데이터 없을 때
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WebSocket receive error: %s", e)
                if not self._connected:
                    break
                await asyncio.sleep(1)

    def _parse_realtime_data(self, raw: str) -> None:
        """실시간 데이터 파싱 (파이프 구분 형식)."""
        try:
            parts = raw.split("|")
            if len(parts) < 4:
                return

            tr_id = parts[1]
            data_count = int(parts[2])
            data_str = parts[3]

            if tr_id == TR_REALTIME_PRICE:
                self._parse_price(data_str)
            elif tr_id == TR_REALTIME_ORDERBOOK:
                self._parse_orderbook(data_str)

        except Exception as e:
            logger.debug("Parse error: %s", e)

    def _parse_price(self, data: str) -> None:
        """실시간 체결가 파싱 (H0STCNT0)."""
        fields = data.split("^")
        if len(fields) < 40:
            return

        try:
            ticker = fields[0]
            price = float(fields[2]) if fields[2] else 0
            change = float(fields[4]) if fields[4] else 0
            change_pct = float(fields[5]) if fields[5] else 0
            # 부호 처리
            sign = fields[3]  # 1=상한, 2=상승, 3=보합, 4=하한, 5=하락
            if sign in ("4", "5"):
                change = -abs(change)
                change_pct = -abs(change_pct)

            volume = int(fields[13]) if fields[13] else 0
            trade_vol = int(fields[12]) if fields[12] else 0
            trade_time = fields[1][:6] if fields[1] else ""
            # 시간 포맷: HHMMSS → HH:MM:SS
            if len(trade_time) == 6:
                trade_time = f"{trade_time[:2]}:{trade_time[2:4]}:{trade_time[4:6]}"

            ask_price = float(fields[25]) if len(fields) > 25 and fields[25] else 0
            bid_price = float(fields[26]) if len(fields) > 26 and fields[26] else 0
            total_ask = int(fields[27]) if len(fields) > 27 and fields[27] else 0
            total_bid = int(fields[28]) if len(fields) > 28 and fields[28] else 0

            self._prices[ticker] = RealtimePrice(
                ticker=ticker,
                price=price,
                change=change,
                change_pct=change_pct,
                volume=volume,
                trade_volume=trade_vol,
                trade_time=trade_time,
                bid_price=bid_price,
                ask_price=ask_price,
                total_ask_vol=total_ask,
                total_bid_vol=total_bid,
                updated_at=time.time(),
            )

            # 콜백 실행
            for cb in self._callbacks:
                try:
                    cb("price", ticker, self._prices[ticker])
                except Exception:
                    pass

        except (ValueError, IndexError) as e:
            logger.debug("Price parse error: %s", e)

    def _parse_orderbook(self, data: str) -> None:
        """실시간 호가 파싱 (H0STASP0)."""
        fields = data.split("^")
        if len(fields) < 43:
            return

        try:
            ticker = fields[0]

            # 매도호가 10단계 (3~22: 가격, 잔량 교대)
            asks = []
            for i in range(10):
                price_idx = 3 + i * 2
                vol_idx = 4 + i * 2
                if price_idx < len(fields) and vol_idx < len(fields):
                    p = float(fields[price_idx]) if fields[price_idx] else 0
                    v = int(fields[vol_idx]) if fields[vol_idx] else 0
                    if p > 0:
                        asks.append(OrderbookLevel(price=p, volume=v))

            # 매수호가 10단계 (23~42)
            bids = []
            for i in range(10):
                price_idx = 23 + i * 2
                vol_idx = 24 + i * 2
                if price_idx < len(fields) and vol_idx < len(fields):
                    p = float(fields[price_idx]) if fields[price_idx] else 0
                    v = int(fields[vol_idx]) if fields[vol_idx] else 0
                    if p > 0:
                        bids.append(OrderbookLevel(price=p, volume=v))

            total_ask = int(fields[43]) if len(fields) > 43 and fields[43] else 0
            total_bid = int(fields[44]) if len(fields) > 44 and fields[44] else 0

            self._orderbooks[ticker] = Orderbook(
                ticker=ticker,
                asks=asks,
                bids=bids,
                total_ask_vol=total_ask,
                total_bid_vol=total_bid,
                updated_at=time.time(),
            )

            for cb in self._callbacks:
                try:
                    cb("orderbook", ticker, self._orderbooks[ticker])
                except Exception:
                    pass

        except (ValueError, IndexError) as e:
            logger.debug("Orderbook parse error: %s", e)

    # ── Public Data Access ───────────────────────────────────────────────────

    def get_price(self, ticker: str) -> RealtimePrice | None:
        """최근 체결가 조회."""
        return self._prices.get(ticker)

    def get_orderbook(self, ticker: str) -> Orderbook | None:
        """최근 호가 조회."""
        return self._orderbooks.get(ticker)

    def get_all_prices(self) -> dict[str, RealtimePrice]:
        """모든 구독 종목 체결가."""
        return dict(self._prices)

    def on_update(self, callback: Callable) -> None:
        """실시간 업데이트 콜백 등록.

        callback(event_type: str, ticker: str, data: RealtimePrice|Orderbook)
        """
        self._callbacks.append(callback)

    def get_subscriptions(self) -> set[str]:
        """현재 구독 중인 종목 목록."""
        return set(self._subscriptions)

    # ── REST API Fallback (WebSocket 미연결 시) ─────────────────────────────

    async def get_orderbook_rest(self, ticker: str) -> Orderbook | None:
        """REST API로 호가 조회 (WebSocket 미연결 시 대안).

        KIS REST API: /quotations/inquire-asking-price-exp-ccn
        """
        app_key = self._app_key
        app_secret = self._app_secret
        if not app_key or not app_secret:
            return None

        base_url = (
            "https://openapivts.koreainvestment.com:29443"
            if self._is_virtual
            else "https://openapi.koreainvestment.com:9443"
        )

        # 토큰 발급
        token = await self._get_access_token(base_url, app_key, app_secret)
        if not token:
            return None

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                    headers={
                        "content-type": "application/json; charset=utf-8",
                        "authorization": f"Bearer {token}",
                        "appkey": app_key,
                        "appsecret": app_secret,
                        "tr_id": "FHKST01010200",
                    },
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": ticker,
                    },
                )

                if resp.status_code != 200:
                    logger.warning("REST orderbook failed: %d", resp.status_code)
                    return None

                data = resp.json()
                output1 = data.get("output1", {})
                output2 = data.get("output2", {})

                asks = []
                bids = []
                for i in range(1, 11):
                    ask_p = float(output1.get(f"askp{i}", 0) or 0)
                    ask_v = int(output1.get(f"askp_rsqn{i}", 0) or 0)
                    bid_p = float(output1.get(f"bidp{i}", 0) or 0)
                    bid_v = int(output1.get(f"bidp_rsqn{i}", 0) or 0)
                    if ask_p > 0:
                        asks.append(OrderbookLevel(price=ask_p, volume=ask_v))
                    if bid_p > 0:
                        bids.append(OrderbookLevel(price=bid_p, volume=bid_v))

                total_ask = int(output2.get("total_askp_rsqn", 0) or 0)
                total_bid = int(output2.get("total_bidp_rsqn", 0) or 0)

                orderbook = Orderbook(
                    ticker=ticker,
                    asks=asks,
                    bids=bids,
                    total_ask_vol=total_ask,
                    total_bid_vol=total_bid,
                    updated_at=time.time(),
                )
                self._orderbooks[ticker] = orderbook
                return orderbook

        except Exception as e:
            logger.error("REST orderbook error: %s", e)
            return None

    async def _get_access_token(self, base_url: str, app_key: str, app_secret: str) -> str:
        """OAuth 토큰 발급."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{base_url}/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "appsecret": app_secret,
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("access_token", "")
        except Exception as e:
            logger.error("Token fetch error: %s", e)
        return ""

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> str:
        """WebSocket 상태 문자열."""
        if not self._connected:
            return "❌ WebSocket 미연결"
        mode = "🔧 모의" if self._is_virtual else "🔴 실전"
        subs = len(self._subscriptions)
        prices = len(self._prices)
        return f"✅ WebSocket 연결 ({mode}) | 구독 {subs}종목 | 시세 {prices}건"
