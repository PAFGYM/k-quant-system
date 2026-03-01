"""Semi-automatic trade execution flow (반자동 매매 실행).

Provides trade order computation, trailing stop management,
split-order planning, and Telegram-formatted confirmations.
All functions are pure computation with no external API calls.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"

# Trailing stop percentages by horizon
TRAILING_PCT_MAP: dict[str, float] = {
    "scalp": 0.03,   # 3%
    "short": 0.05,   # 5%
    "mid": 0.08,     # 8%
    "long": 0.15,    # 15%
}

DEFAULT_COMMISSION_PCT = 0.00015  # 0.015%


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TradeOrder:
    """매매 주문 정보."""

    ticker: str = ""
    name: str = ""
    direction: str = "buy"          # "buy" or "sell"
    quantity: int = 0
    price: float = 0.0
    amount: float = 0.0
    commission: float = 0.0
    order_type: str = "limit"       # "limit" or "market"
    strategy: str = ""
    score: float = 0.0


@dataclass
class TrailingStop:
    """트레일링 스탑 정보."""

    ticker: str = ""
    name: str = ""
    peak_price: float = 0.0
    trailing_pct: float = 0.0
    stop_price: float = 0.0
    is_triggered: bool = False


@dataclass
class SplitPlan:
    """분할매매 계획."""

    ticker: str = ""
    name: str = ""
    tranches: list[dict] = field(default_factory=list)
    total_quantity: int = 0
    total_amount: float = 0.0


# ---------------------------------------------------------------------------
# Order computation
# ---------------------------------------------------------------------------

def compute_order(
    ticker: str,
    name: str,
    price: float,
    total_budget: float,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
) -> TradeOrder:
    """주어진 예산으로 매수 주문을 계산합니다.

    수수료를 포함한 최대 매수 가능 수량과 금액을 산출합니다.
    """
    try:
        if price <= 0:
            logger.warning("[%s] 가격이 0 이하입니다: %.2f", ticker, price)
            return TradeOrder(ticker=ticker, name=name, price=price)

        if total_budget <= 0:
            logger.warning("[%s] 예산이 0 이하입니다: %.2f", ticker, total_budget)
            return TradeOrder(ticker=ticker, name=name, price=price)

        # Max quantity considering commission
        effective_price = price * (1 + commission_pct)
        quantity = int(total_budget / effective_price)

        if quantity <= 0:
            logger.info("[%s] 예산 부족: 가격=%.0f, 예산=%.0f", ticker, price, total_budget)
            return TradeOrder(
                ticker=ticker, name=name, direction="buy",
                quantity=0, price=price, amount=0.0, commission=0.0,
            )

        amount = quantity * price
        commission = round(amount * commission_pct, 2)

        order = TradeOrder(
            ticker=ticker,
            name=name,
            direction="buy",
            quantity=quantity,
            price=price,
            amount=amount,
            commission=commission,
            order_type="limit",
        )

        logger.info(
            "[%s] 주문 계산: %d주 x %,.0f원 = %,.0f원 (수수료 %,.0f원)",
            ticker, quantity, price, amount, commission,
        )
        return order

    except Exception as e:
        logger.error("[%s] 주문 계산 실패: %s", ticker, e, exc_info=True)
        return TradeOrder(ticker=ticker, name=name, price=price)


# ---------------------------------------------------------------------------
# Trailing stop
# ---------------------------------------------------------------------------

def compute_trailing_stop(
    ticker: str,
    name: str,
    current_price: float,
    peak_price: float,
    horizon: str = "mid",
) -> TrailingStop:
    """트레일링 스탑 가격을 계산합니다.

    horizon에 따라 다른 트레일링 비율을 적용합니다:
    scalp=3%, short=5%, mid=8%, long=15%
    """
    try:
        trailing_pct = TRAILING_PCT_MAP.get(horizon, TRAILING_PCT_MAP["mid"])

        # Update peak if current is higher
        actual_peak = max(current_price, peak_price)
        stop_price = round(actual_peak * (1 - trailing_pct), 0)
        is_triggered = current_price <= stop_price

        stop = TrailingStop(
            ticker=ticker,
            name=name,
            peak_price=actual_peak,
            trailing_pct=trailing_pct,
            stop_price=stop_price,
            is_triggered=is_triggered,
        )

        if is_triggered:
            logger.warning(
                "[%s] 트레일링 스탑 발동: 현재가=%.0f <= 스탑=%.0f (고점=%.0f, -%.0f%%)",
                ticker, current_price, stop_price, actual_peak,
                trailing_pct * 100,
            )

        return stop

    except Exception as e:
        logger.error("[%s] 트레일링 스탑 계산 실패: %s", ticker, e, exc_info=True)
        return TrailingStop(ticker=ticker, name=name)


def check_trailing_stops(
    stops: list[TrailingStop],
    current_prices: dict[str, float],
) -> list[TrailingStop]:
    """모든 트레일링 스탑을 현재가 기준으로 점검합니다.

    발동된 스탑만 반환합니다.
    """
    try:
        triggered: list[TrailingStop] = []

        for stop in stops:
            current = current_prices.get(stop.ticker, 0.0)
            if current <= 0:
                continue

            # Update peak
            actual_peak = max(current, stop.peak_price)
            new_stop_price = round(actual_peak * (1 - stop.trailing_pct), 0)

            stop.peak_price = actual_peak
            stop.stop_price = new_stop_price
            stop.is_triggered = current <= new_stop_price

            if stop.is_triggered:
                triggered.append(stop)

        if triggered:
            logger.warning("트레일링 스탑 발동: %d건", len(triggered))

        return triggered

    except Exception as e:
        logger.error("트레일링 스탑 점검 실패: %s", e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Split plan
# ---------------------------------------------------------------------------

def create_split_plan(
    ticker: str,
    name: str,
    total_quantity: int,
    n_tranches: int = 3,
    base_price: float = 0.0,
) -> SplitPlan:
    """분할 매매 계획을 생성합니다.

    수량을 n_tranches로 나누고, 각 트랜치에 가격 단계를 배정합니다.
    기본 전략: 1차(40%), 2차(35%), 3차(25%) 비중 배분
    """
    try:
        if total_quantity <= 0 or n_tranches <= 0:
            logger.warning("[%s] 유효하지 않은 매개변수: 수량=%d, 트랜치=%d",
                           ticker, total_quantity, n_tranches)
            return SplitPlan(ticker=ticker, name=name)

        # Weight distribution
        if n_tranches == 1:
            weights = [1.0]
        elif n_tranches == 2:
            weights = [0.55, 0.45]
        elif n_tranches == 3:
            weights = [0.40, 0.35, 0.25]
        elif n_tranches == 4:
            weights = [0.35, 0.25, 0.25, 0.15]
        else:
            # Equal split for 5+
            weights = [1.0 / n_tranches] * n_tranches

        tranches: list[dict] = []
        allocated = 0

        for i, w in enumerate(weights):
            if i == len(weights) - 1:
                # Last tranche gets remainder
                qty = total_quantity - allocated
            else:
                qty = max(1, round(total_quantity * w))
                allocated += qty

            # Price step: -2% per tranche from base
            if base_price > 0:
                price = round(base_price * (1 - 0.02 * i), 0)
            else:
                price = 0.0

            pct = round(w * 100, 1)
            tranches.append({
                "tranche": i + 1,
                "price": price,
                "quantity": qty,
                "pct": pct,
            })

        total_amount = sum(t["price"] * t["quantity"] for t in tranches)

        plan = SplitPlan(
            ticker=ticker,
            name=name,
            tranches=tranches,
            total_quantity=total_quantity,
            total_amount=total_amount,
        )

        logger.info("[%s] 분할 계획 생성: %d주 -> %d트랜치", ticker, total_quantity, n_tranches)
        return plan

    except Exception as e:
        logger.error("[%s] 분할 계획 생성 실패: %s", ticker, e, exc_info=True)
        return SplitPlan(ticker=ticker, name=name)


# ---------------------------------------------------------------------------
# Formatting functions
# ---------------------------------------------------------------------------

def format_order_confirmation(order: TradeOrder) -> str:
    """주문 확인 메시지를 텔레그램 형식으로 생성합니다."""
    try:
        direction_label = "매수" if order.direction == "buy" else "매도"
        now = datetime.now(tz=KST).strftime("%H:%M")

        lines = [
            f"[{direction_label} 주문 확인] {now}",
            f"{USER_NAME}, {direction_label} 주문을 확인해 주세요.",
            "",
            f"  종목: {order.name} ({order.ticker})",
            f"  방향: {direction_label}",
            f"  수량: {order.quantity:,}주",
            f"  가격: {order.price:,.0f}원",
            f"  금액: {order.amount:,.0f}원",
            f"  수수료: {order.commission:,.0f}원",
            f"  총액: {order.amount + order.commission:,.0f}원",
            f"  주문유형: {'지정가' if order.order_type == 'limit' else '시장가'}",
        ]

        if order.strategy:
            lines.append(f"  전략: {order.strategy}")
        if order.score > 0:
            lines.append(f"  점수: {order.score:.1f}점")

        lines.append("")
        lines.append("이 주문을 실행하시겠습니까?")

        return "\n".join(lines)

    except Exception as e:
        logger.error("주문 확인 메시지 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 주문 확인 메시지 생성 중 오류가 발생했습니다."


def format_trailing_alert(stop: TrailingStop) -> str:
    """트레일링 스탑 발동 알림 메시지를 생성합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%H:%M")
        drop_pct = stop.trailing_pct * 100

        lines = [
            f"[트레일링 스탑 발동] {now}",
            f"{USER_NAME}, 스탑 가격에 도달했습니다.",
            "",
            f"  종목: {stop.name} ({stop.ticker})",
            f"  고점: {stop.peak_price:,.0f}원",
            f"  스탑가: {stop.stop_price:,.0f}원 (-{drop_pct:.0f}%)",
            "",
            "매도 검토를 권장합니다.",
        ]

        return "\n".join(lines)

    except Exception as e:
        logger.error("트레일링 알림 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 트레일링 스탑 알림 생성 중 오류가 발생했습니다."


def format_split_plan(plan: SplitPlan) -> str:
    """분할 매매 계획 메시지를 텔레그램 형식으로 생성합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%H:%M")

        lines = [
            f"[분할매매 계획] {now}",
            f"{USER_NAME}, {plan.name} 분할매매 계획입니다.",
            "",
            f"  종목: {plan.name} ({plan.ticker})",
            f"  총 수량: {plan.total_quantity:,}주",
            f"  트랜치: {len(plan.tranches)}단계",
            "",
        ]

        for t in plan.tranches:
            tranche_num = t.get("tranche", 0)
            price = t.get("price", 0)
            qty = t.get("quantity", 0)
            pct = t.get("pct", 0)

            if price > 0:
                amount = price * qty
                lines.append(
                    f"  {tranche_num}차: {price:,.0f}원 x {qty:,}주 "
                    f"({pct:.0f}%, {amount:,.0f}원)"
                )
            else:
                lines.append(f"  {tranche_num}차: {qty:,}주 ({pct:.0f}%)")

        if plan.total_amount > 0:
            lines.append("")
            lines.append(f"  총 예상 금액: {plan.total_amount:,.0f}원")

        lines.append("")
        lines.append("각 단계별 조건 충족 시 실행해 주세요.")

        return "\n".join(lines)

    except Exception as e:
        logger.error("분할 계획 메시지 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 분할매매 계획 메시지 생성 중 오류가 발생했습니다."
