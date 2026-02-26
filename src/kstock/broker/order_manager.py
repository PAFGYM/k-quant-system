"""주문 상태머신 + 멱등성 키 — v5.1.

모든 주문을 상태머신으로 관리하여 중복주문을 차단하고,
주문 라이프사이클을 추적한다.

상태 전이:
  INTENT → VALIDATED → PLACED → (FILLED | PARTIAL | REJECTED | CANCELLED)
         ↘ BLOCKED (리스크 위반)

핵심 기능:
  1. OrderStateMachine — 주문 상태 추적 + 전이 규칙
  2. IdempotencyGuard — 중복 주문 차단 (ticker+side+5분 윈도우)
  3. PreTradeValidator — 주문 전 리스크 체크
  4. OrderLedger — 전체 주문 이력 관리
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

# 멱등성 윈도우 (같은 종목+방향 주문을 이 시간 내 중복 차단)
IDEMPOTENCY_WINDOW_SECONDS = 300  # 5분


# ── 주문 상태 ─────────────────────────────────────────────

class OrderState(str, Enum):
    """주문 라이프사이클 상태."""
    INTENT = "intent"           # 주문 의도 생성
    VALIDATED = "validated"     # 리스크 체크 통과
    BLOCKED = "blocked"         # 리스크 체크 실패 → 차단
    PLACED = "placed"           # 브로커에 주문 접수
    PARTIAL = "partial"         # 부분 체결
    FILLED = "filled"           # 전량 체결
    REJECTED = "rejected"       # 브로커 거부
    CANCELLED = "cancelled"     # 사용자/시스템 취소
    EXPIRED = "expired"         # 시간 만료


# 유효한 상태 전이 맵
VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.INTENT: {OrderState.VALIDATED, OrderState.BLOCKED},
    OrderState.VALIDATED: {OrderState.PLACED, OrderState.CANCELLED},
    OrderState.BLOCKED: set(),  # 종료 상태
    OrderState.PLACED: {
        OrderState.PARTIAL, OrderState.FILLED,
        OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED,
    },
    OrderState.PARTIAL: {OrderState.FILLED, OrderState.CANCELLED},
    OrderState.FILLED: set(),      # 종료 상태
    OrderState.REJECTED: set(),    # 종료 상태
    OrderState.CANCELLED: set(),   # 종료 상태
    OrderState.EXPIRED: set(),     # 종료 상태
}

TERMINAL_STATES = {
    OrderState.BLOCKED, OrderState.FILLED, OrderState.REJECTED,
    OrderState.CANCELLED, OrderState.EXPIRED,
}


# ── 주문 엔트리 ───────────────────────────────────────────

@dataclass
class ManagedOrder:
    """상태머신이 관리하는 주문."""

    order_id: str                     # UUID
    idempotency_key: str              # 중복 방지 키
    ticker: str = ""
    name: str = ""
    side: str = "buy"                 # "buy" or "sell"
    quantity: int = 0
    price: float = 0.0
    order_type: str = "limit"         # "limit" or "market"
    strategy: str = ""

    state: OrderState = OrderState.INTENT
    broker_order_id: str = ""         # 브로커에서 받은 주문번호
    filled_quantity: int = 0
    filled_price: float = 0.0
    filled_amount: float = 0.0

    block_reason: str = ""            # BLOCKED 시 사유
    reject_reason: str = ""           # REJECTED 시 사유

    created_at: str = ""
    updated_at: str = ""
    transitions: list[dict] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return not self.is_terminal

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "idempotency_key": self.idempotency_key,
            "ticker": self.ticker,
            "name": self.name,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "order_type": self.order_type,
            "strategy": self.strategy,
            "state": self.state.value,
            "broker_order_id": self.broker_order_id,
            "filled_quantity": self.filled_quantity,
            "filled_price": self.filled_price,
            "filled_amount": self.filled_amount,
            "block_reason": self.block_reason,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "transitions": self.transitions,
        }


# ── 주문 상태머신 ─────────────────────────────────────────

class OrderStateMachine:
    """단일 주문의 상태 전이를 관리한다."""

    def __init__(self, order: ManagedOrder):
        self.order = order

    def transition(self, new_state: OrderState, reason: str = "") -> bool:
        """상태 전이 시도.

        Args:
            new_state: 목표 상태.
            reason: 전이 사유.

        Returns:
            전이 성공 여부.
        """
        current = self.order.state
        valid = VALID_TRANSITIONS.get(current, set())

        if new_state not in valid:
            logger.warning(
                "주문 %s: 잘못된 상태 전이 %s → %s (허용: %s)",
                self.order.order_id[:8], current.value, new_state.value,
                [s.value for s in valid],
            )
            return False

        old_state = current
        self.order.state = new_state
        self.order.updated_at = datetime.now(KST).isoformat()

        transition_record = {
            "from": old_state.value,
            "to": new_state.value,
            "reason": reason,
            "timestamp": self.order.updated_at,
        }
        self.order.transitions.append(transition_record)

        logger.info(
            "주문 %s [%s %s %d주]: %s → %s%s",
            self.order.order_id[:8],
            self.order.side, self.order.ticker, self.order.quantity,
            old_state.value, new_state.value,
            f" ({reason})" if reason else "",
        )

        return True

    def validate(self, reason: str = "리스크 체크 통과") -> bool:
        return self.transition(OrderState.VALIDATED, reason)

    def block(self, reason: str) -> bool:
        self.order.block_reason = reason
        return self.transition(OrderState.BLOCKED, reason)

    def place(self, broker_order_id: str = "") -> bool:
        self.order.broker_order_id = broker_order_id
        return self.transition(OrderState.PLACED, f"broker_id={broker_order_id}")

    def fill(self, filled_qty: int, filled_price: float) -> bool:
        self.order.filled_quantity += filled_qty
        self.order.filled_price = filled_price
        self.order.filled_amount = self.order.filled_quantity * filled_price

        if self.order.filled_quantity >= self.order.quantity:
            return self.transition(OrderState.FILLED, f"체결 {filled_qty}주@{filled_price:,.0f}")
        else:
            return self.transition(OrderState.PARTIAL, f"부분체결 {filled_qty}주@{filled_price:,.0f}")

    def reject(self, reason: str) -> bool:
        self.order.reject_reason = reason
        return self.transition(OrderState.REJECTED, reason)

    def cancel(self, reason: str = "사용자 취소") -> bool:
        return self.transition(OrderState.CANCELLED, reason)


# ── 멱등성 가드 ───────────────────────────────────────────

class IdempotencyGuard:
    """중복 주문 차단.

    같은 ticker + side 조합이 IDEMPOTENCY_WINDOW_SECONDS 내에
    이미 주문되었으면 차단한다.
    """

    def __init__(self, window_seconds: float = IDEMPOTENCY_WINDOW_SECONDS):
        self.window = window_seconds
        self._keys: dict[str, float] = {}  # key → timestamp

    @staticmethod
    def generate_key(ticker: str, side: str, quantity: int = 0) -> str:
        """멱등성 키 생성."""
        raw = f"{ticker}:{side}:{quantity}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def check_and_register(self, key: str) -> tuple[bool, str]:
        """중복 확인 + 등록.

        Returns:
            (허용 여부, 메시지)
        """
        self._cleanup_expired()

        now = time.time()
        if key in self._keys:
            elapsed = now - self._keys[key]
            remaining = self.window - elapsed
            return False, (
                f"중복 주문 차단 (동일 주문 {elapsed:.0f}초 전 접수, "
                f"{remaining:.0f}초 후 재시도 가능)"
            )

        self._keys[key] = now
        return True, ""

    def release(self, key: str) -> None:
        """키 해제 (주문 취소/실패 시)."""
        self._keys.pop(key, None)

    def _cleanup_expired(self) -> None:
        """만료된 키 정리."""
        now = time.time()
        expired = [k for k, t in self._keys.items() if now - t > self.window]
        for k in expired:
            del self._keys[k]

    @property
    def active_count(self) -> int:
        self._cleanup_expired()
        return len(self._keys)


# ── Pre-Trade Validator ───────────────────────────────────

@dataclass
class PreTradeResult:
    """주문 전 검증 결과."""
    approved: bool = True
    reasons: list[str] = field(default_factory=list)

    def add_block(self, reason: str) -> None:
        self.approved = False
        self.reasons.append(reason)

    def __bool__(self) -> bool:
        return self.approved


class PreTradeValidator:
    """주문 전 리스크 체크.

    체크 항목:
      1. SafetyLimits (일일 주문 횟수, 1회 주문 한도, 일일 손실 한도)
      2. 중복 주문 (IdempotencyGuard)
      3. 시장 시간 체크
      4. 킬스위치 상태
    """

    def __init__(self, safety_limits=None, idempotency_guard=None):
        self.safety = safety_limits
        self.guard = idempotency_guard or IdempotencyGuard()
        self._kill_switch_active = False
        self._data_source_checker = None  # v5.1: DataRouter.can_buy_with_current_data

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @kill_switch_active.setter
    def kill_switch_active(self, value: bool) -> None:
        self._kill_switch_active = value
        if value:
            logger.warning("킬스위치 활성화 — 모든 신규 주문 차단")
        else:
            logger.info("킬스위치 해제")

    def set_data_source_checker(self, checker) -> None:
        """v5.1: DataRouter의 can_buy_with_current_data 메서드를 연결.

        Args:
            checker: callable returning (bool, str) — e.g. DataRouter.can_buy_with_current_data
        """
        self._data_source_checker = checker

    def validate(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: float,
        total_eval: float = 0.0,
    ) -> PreTradeResult:
        """주문 전 검증.

        Args:
            ticker: 종목 코드.
            side: "buy" or "sell".
            quantity: 주문 수량.
            price: 주문 가격.
            total_eval: 포트폴리오 총 평가액 (비중 계산용).

        Returns:
            PreTradeResult.
        """
        result = PreTradeResult()

        # 1. 킬스위치 체크
        if self._kill_switch_active:
            result.add_block("킬스위치 활성 — 모든 주문 차단")
            return result

        # 2. v5.1: 데이터 품질 체크 (매수만 — 매도는 지연 데이터여도 허용)
        if side == "buy" and self._data_source_checker:
            try:
                can_buy, reason = self._data_source_checker()
                if not can_buy:
                    result.add_block(f"데이터품질: {reason}")
            except Exception as e:
                logger.debug("데이터 소스 체크 실패 (무시): %s", e)

        # 3. SafetyLimits 체크
        if self.safety and total_eval > 0:
            order_amount = quantity * price
            order_pct = order_amount / total_eval * 100
            can_order, msg = self.safety.can_order(order_pct)
            if not can_order:
                result.add_block(f"SafetyLimits: {msg}")

        # 4. 멱등성 체크
        idem_key = IdempotencyGuard.generate_key(ticker, side, quantity)
        allowed, msg = self.guard.check_and_register(idem_key)
        if not allowed:
            result.add_block(msg)

        # 5. 기본 유효성
        if quantity <= 0:
            result.add_block(f"수량 오류: {quantity}주")
        if price < 0:
            result.add_block(f"가격 오류: {price}")

        return result


# ── Order Ledger ──────────────────────────────────────────

class OrderLedger:
    """주문 이력 관리.

    메모리 + DB 하이브리드. 당일 주문은 메모리에, 과거 주문은 DB에.
    """

    def __init__(self, db=None):
        self.db = db
        self._orders: dict[str, ManagedOrder] = {}
        self._state_machines: dict[str, OrderStateMachine] = {}
        self._validator = PreTradeValidator()

    @property
    def validator(self) -> PreTradeValidator:
        return self._validator

    def set_safety_limits(self, safety) -> None:
        """SafetyLimits 설정."""
        self._validator.safety = safety

    def create_order(
        self,
        ticker: str,
        name: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str = "limit",
        strategy: str = "",
        total_eval: float = 0.0,
    ) -> tuple[ManagedOrder | None, str]:
        """새 주문 생성 + 검증.

        Returns:
            (ManagedOrder | None, 메시지)
        """
        # Pre-trade 검증
        result = self._validator.validate(
            ticker, side, quantity, price, total_eval,
        )

        now = datetime.now(KST).isoformat()
        idem_key = IdempotencyGuard.generate_key(ticker, side, quantity)

        order = ManagedOrder(
            order_id=str(uuid.uuid4()),
            idempotency_key=idem_key,
            ticker=ticker,
            name=name,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            strategy=strategy,
            created_at=now,
            updated_at=now,
        )

        sm = OrderStateMachine(order)
        self._orders[order.order_id] = order
        self._state_machines[order.order_id] = sm

        if not result:
            sm.block("; ".join(result.reasons))
            # 멱등성 키 해제 (차단된 주문은 재시도 허용)
            self._validator.guard.release(idem_key)
            return order, f"주문 차단: {'; '.join(result.reasons)}"

        sm.validate()
        return order, "주문 검증 통과"

    def get_machine(self, order_id: str) -> OrderStateMachine | None:
        """주문의 상태머신 조회."""
        return self._state_machines.get(order_id)

    def get_order(self, order_id: str) -> ManagedOrder | None:
        """주문 조회."""
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[ManagedOrder]:
        """활성 주문 목록."""
        return [o for o in self._orders.values() if o.is_active]

    def get_orders_by_ticker(self, ticker: str) -> list[ManagedOrder]:
        """종목별 주문 목록."""
        return [o for o in self._orders.values() if o.ticker == ticker]

    def get_today_orders(self) -> list[ManagedOrder]:
        """당일 주문 목록."""
        today = datetime.now(KST).strftime("%Y-%m-%d")
        return [
            o for o in self._orders.values()
            if o.created_at.startswith(today)
        ]

    def get_stats(self) -> dict:
        """주문 통계."""
        all_orders = list(self._orders.values())
        today_orders = self.get_today_orders()

        return {
            "total_orders": len(all_orders),
            "today_orders": len(today_orders),
            "active_orders": sum(1 for o in all_orders if o.is_active),
            "filled_orders": sum(1 for o in all_orders if o.state == OrderState.FILLED),
            "blocked_orders": sum(1 for o in all_orders if o.state == OrderState.BLOCKED),
            "rejected_orders": sum(1 for o in all_orders if o.state == OrderState.REJECTED),
            "cancelled_orders": sum(1 for o in all_orders if o.state == OrderState.CANCELLED),
            "idempotency_active": self._validator.guard.active_count,
            "kill_switch": self._validator.kill_switch_active,
        }

    def save_to_db(self, order: ManagedOrder) -> None:
        """주문을 DB에 저장."""
        if not self.db:
            return
        try:
            import json
            with self.db._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO orders
                       (ticker, name, order_type, side, quantity, price,
                        order_id, status, filled_price, filled_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        order.ticker, order.name, order.order_type,
                        order.side, order.quantity, order.price,
                        order.order_id, order.state.value,
                        order.filled_price or None,
                        order.updated_at if order.state == OrderState.FILLED else None,
                        order.created_at,
                    ),
                )
        except Exception as e:
            logger.error("주문 DB 저장 실패: %s", e)


# ── 글로벌 인스턴스 ───────────────────────────────────────

_ledger: OrderLedger | None = None


def get_order_ledger(db=None) -> OrderLedger:
    """글로벌 OrderLedger 반환."""
    global _ledger
    if _ledger is None:
        _ledger = OrderLedger(db=db)
    return _ledger


# ── 텔레그램 포맷 ─────────────────────────────────────────

def format_order_status(order: ManagedOrder) -> str:
    """주문 상태를 텔레그램 포맷."""
    state_icons = {
        OrderState.INTENT: "📝",
        OrderState.VALIDATED: "✅",
        OrderState.BLOCKED: "🚫",
        OrderState.PLACED: "📤",
        OrderState.PARTIAL: "🔄",
        OrderState.FILLED: "🎯",
        OrderState.REJECTED: "❌",
        OrderState.CANCELLED: "⚪",
        OrderState.EXPIRED: "⏰",
    }
    icon = state_icons.get(order.state, "❓")
    side_kr = "매수" if order.side == "buy" else "매도"

    lines = [
        f"{icon} [{order.state.value.upper()}] {side_kr} 주문",
        f"  종목: {order.name} ({order.ticker})",
        f"  수량: {order.quantity:,}주 | 가격: {order.price:,.0f}원",
    ]

    if order.filled_quantity > 0:
        lines.append(
            f"  체결: {order.filled_quantity:,}주 @ {order.filled_price:,.0f}원"
        )

    if order.block_reason:
        lines.append(f"  차단: {order.block_reason}")

    if order.reject_reason:
        lines.append(f"  거부: {order.reject_reason}")

    return "\n".join(lines)


def format_order_ledger_summary(ledger: OrderLedger) -> str:
    """주문 원장 요약을 텔레그램 포맷."""
    stats = ledger.get_stats()
    kill_icon = "🔴 활성" if stats["kill_switch"] else "🟢 해제"

    lines = [
        "📋 주문 원장 현황",
        "━" * 25,
        f"  오늘 주문: {stats['today_orders']}건",
        f"  활성 주문: {stats['active_orders']}건",
        f"  체결 완료: {stats['filled_orders']}건",
        f"  차단/거부: {stats['blocked_orders'] + stats['rejected_orders']}건",
        f"  킬스위치: {kill_icon}",
        f"  중복차단 키: {stats['idempotency_active']}개",
    ]

    # 활성 주문 상세
    active = ledger.get_active_orders()
    if active:
        lines.extend(["", "활성 주문:"])
        for o in active[:5]:
            side_kr = "매수" if o.side == "buy" else "매도"
            lines.append(
                f"  • {o.name} {side_kr} {o.quantity}주 [{o.state.value}]"
            )

    return "\n".join(lines)
