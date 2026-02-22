"""Korea Investment Securities API client (한국투자증권 KIS API).

Provides market data, balance inquiry, and order execution.
Falls back gracefully when KIS API keys are not configured.

Authentication: .env based (KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# Safety configuration -- 안전 설정
# ---------------------------------------------------------------------------
SAFETY = {
    "require_confirmation": True,
    "auto_trade": False,
    "max_daily_order_pct": 0.20,
    "max_single_order_pct": 0.10,
    "emergency_stop": True,
}

# ---------------------------------------------------------------------------
# Rate limit configuration -- 초당/분당 요청 제한
# ---------------------------------------------------------------------------
RATE_LIMITS = {
    "rest_per_second": 20,
    "token_per_minute": 1,
    "websocket_max_stocks": 40,
}


# ---------------------------------------------------------------------------
# pykis optional import -- pykis 미설치 시에도 동작하도록 처리
# ---------------------------------------------------------------------------
_PYKIS_AVAILABLE = False

try:
    import pykis  # noqa: F401
    _PYKIS_AVAILABLE = True
    logger.debug("pykis 라이브러리가 설치되어 있습니다.")
except Exception:
    pykis = None  # type: ignore[assignment]
    logger.debug("pykis 라이브러리를 불러올 수 없습니다. 스텁 모드로 동작합니다.")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class KisConfig:
    """KIS API 인증 설정."""

    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    hts_id: str = ""
    is_virtual: bool = True
    is_configured: bool = False


@dataclass
class KisPrice:
    """KIS 시세 데이터."""

    ticker: str = ""
    price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    high: float = 0.0
    low: float = 0.0
    open_price: float = 0.0


@dataclass
class KisBalance:
    """KIS 계좌 잔고."""

    cash: float = 0.0
    total_eval: float = 0.0
    total_pnl: float = 0.0
    total_pnl_rate: float = 0.0
    holdings: list[dict] = field(default_factory=list)


@dataclass
class KisOrderResult:
    """KIS 주문 결과."""

    success: bool = False
    order_id: str = ""
    ticker: str = ""
    name: str = ""
    quantity: int = 0
    price: float = 0.0
    message: str = ""


@dataclass
class KisSafety:
    """주문 안전 장치 설정."""

    require_confirmation: bool = True
    auto_trade: bool = False
    max_daily_order_pct: float = 0.20
    max_single_order_pct: float = 0.10


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

def load_kis_config() -> KisConfig:
    """환경 변수에서 KIS API 설정을 불러옵니다.

    .env 파일에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO가 설정되어
    있어야 합니다. 하나라도 누락되면 is_configured=False를 반환합니다.

    Returns:
        KisConfig: KIS API 설정. 키가 누락이면 is_configured=False.
    """
    try:
        app_key = os.getenv("KIS_APP_KEY", "")
        app_secret = os.getenv("KIS_APP_SECRET", "")
        account_no = os.getenv("KIS_ACCOUNT_NO", "")
        hts_id = os.getenv("KIS_HTS_ID", "")
        is_virtual = os.getenv("KIS_IS_VIRTUAL", "true").lower() in ("true", "1", "yes")

        configured = bool(app_key and app_secret and account_no)

        if not configured:
            logger.info(
                "%s KIS API 키가 설정되지 않았습니다. "
                "시세 조회만 가능합니다.",
                USER_NAME,
            )
        else:
            masked_key = app_key[:4] + "****" if len(app_key) > 4 else "****"
            logger.info(
                "%s KIS API 설정을 불러왔습니다. (app_key=%s, virtual=%s)",
                USER_NAME,
                masked_key,
                is_virtual,
            )

        return KisConfig(
            app_key=app_key,
            app_secret=app_secret,
            account_no=account_no,
            hts_id=hts_id,
            is_virtual=is_virtual,
            is_configured=configured,
        )
    except Exception:
        logger.exception("%s KIS 설정 로드 중 오류가 발생했습니다.", USER_NAME)
        return KisConfig(is_configured=False)


# ---------------------------------------------------------------------------
# Order validation
# ---------------------------------------------------------------------------

def validate_order(
    ticker: str,
    quantity: int,
    price: float,
    total_assets: float,
    daily_ordered: float,
    safety: KisSafety | None = None,
) -> tuple[bool, str]:
    """주문의 안전성을 검증합니다.

    단일 주문 비중과 일일 누적 주문 비중이 안전 한도 이내인지 확인합니다.

    Args:
        ticker: 종목 코드.
        quantity: 주문 수량.
        price: 주문 단가.
        total_assets: 총 자산 평가액.
        daily_ordered: 금일 이미 주문한 누적 금액.
        safety: 안전 장치 설정. None이면 기본값을 사용합니다.

    Returns:
        (is_valid, error_message) 튜플. 유효하면 ("", True).
    """
    try:
        if safety is None:
            safety = get_default_safety()

        order_amount = compute_order_amount(price, quantity)

        if total_assets <= 0:
            msg = (
                f"{USER_NAME}, 총 자산이 0 이하입니다. "
                f"주문을 실행할 수 없습니다."
            )
            logger.warning(msg)
            return False, msg

        # 단일 주문 비중 검사
        single_pct = order_amount / total_assets
        if single_pct > safety.max_single_order_pct:
            msg = (
                f"{USER_NAME}, [{ticker}] 단일 주문 비중이 "
                f"{single_pct:.1%}로 한도({safety.max_single_order_pct:.0%})를 "
                f"초과합니다. 주문 금액: {order_amount:,.0f}원"
            )
            logger.warning(msg)
            return False, msg

        # 일일 누적 주문 비중 검사
        new_daily_total = daily_ordered + order_amount
        daily_pct = new_daily_total / total_assets
        if daily_pct > safety.max_daily_order_pct:
            msg = (
                f"{USER_NAME}, 금일 누적 주문 비중이 "
                f"{daily_pct:.1%}로 한도({safety.max_daily_order_pct:.0%})를 "
                f"초과합니다. 누적 주문액: {new_daily_total:,.0f}원"
            )
            logger.warning(msg)
            return False, msg

        # 수량 검사
        if quantity <= 0:
            msg = f"{USER_NAME}, 주문 수량은 1주 이상이어야 합니다."
            logger.warning(msg)
            return False, msg

        # 가격 검사
        if price <= 0:
            msg = f"{USER_NAME}, 주문 가격이 올바르지 않습니다. (price={price})"
            logger.warning(msg)
            return False, msg

        logger.info(
            "%s [%s] 주문 검증 통과 (수량=%d, 단가=%.0f, 비중=%.1f%%)",
            USER_NAME,
            ticker,
            quantity,
            price,
            single_pct * 100,
        )
        return True, ""

    except Exception:
        logger.exception(
            "%s [%s] 주문 검증 중 오류가 발생했습니다.", USER_NAME, ticker,
        )
        return False, f"{USER_NAME}, 주문 검증 중 오류가 발생했습니다."


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_kis_price(price_data: KisPrice) -> str:
    """시세 데이터를 한국어 형식으로 포맷합니다.

    Args:
        price_data: KisPrice 시세 데이터.

    Returns:
        포맷된 시세 문자열.
    """
    try:
        sign = "+" if price_data.change >= 0 else ""
        arrow = "▲" if price_data.change > 0 else ("▼" if price_data.change < 0 else "―")

        lines = [
            f"[{price_data.ticker}] 현재가 정보",
            f"현재가: {price_data.price:,.0f}원",
            f"전일대비: {arrow} {sign}{price_data.change:,.0f}원 ({sign}{price_data.change_pct:.2f}%)",
            f"거래량: {price_data.volume:,}주",
            f"고가: {price_data.high:,.0f}원 / 저가: {price_data.low:,.0f}원",
            f"시가: {price_data.open_price:,.0f}원",
        ]
        return "\n".join(lines)

    except Exception:
        logger.exception(
            "%s 시세 포맷 중 오류가 발생했습니다. ticker=%s",
            USER_NAME,
            getattr(price_data, "ticker", "unknown"),
        )
        return f"{USER_NAME}, 시세 정보를 표시하는 중 오류가 발생했습니다."


def format_kis_balance(balance: KisBalance) -> str:
    """계좌 잔고를 한국어 형식으로 포맷합니다.

    Args:
        balance: KisBalance 잔고 데이터.

    Returns:
        포맷된 잔고 문자열.
    """
    try:
        pnl_sign = "+" if balance.total_pnl >= 0 else ""
        pnl_arrow = "▲" if balance.total_pnl > 0 else ("▼" if balance.total_pnl < 0 else "―")

        lines = [
            f"{USER_NAME} 계좌 잔고",
            f"예수금: {balance.cash:,.0f}원",
            f"총 평가금액: {balance.total_eval:,.0f}원",
            f"총 손익: {pnl_arrow} {pnl_sign}{balance.total_pnl:,.0f}원 ({pnl_sign}{balance.total_pnl_rate:.2f}%)",
            "",
        ]

        if balance.holdings:
            lines.append(f"보유종목 ({len(balance.holdings)}개)")
            lines.append("-" * 30)
            for h in balance.holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                qty = h.get("quantity", 0)
                avg_price = h.get("avg_price", 0)
                cur_price = h.get("current_price", 0)
                pnl = h.get("pnl", 0)
                pnl_rate = h.get("pnl_rate", 0.0)

                h_sign = "+" if pnl >= 0 else ""
                lines.append(
                    f"  {name}({ticker}) {qty}주 "
                    f"| 평균 {avg_price:,.0f}원 -> 현재 {cur_price:,.0f}원 "
                    f"| {h_sign}{pnl:,.0f}원 ({h_sign}{pnl_rate:.2f}%)"
                )
        else:
            lines.append("보유 종목이 없습니다.")

        return "\n".join(lines)

    except Exception:
        logger.exception("%s 잔고 포맷 중 오류가 발생했습니다.", USER_NAME)
        return f"{USER_NAME}, 잔고 정보를 표시하는 중 오류가 발생했습니다."


def format_order_confirmation(
    ticker: str,
    name: str,
    quantity: int,
    price: float,
    order_type: str,
) -> str:
    """주문 실행 전 확인 메시지를 생성합니다.

    Args:
        ticker: 종목 코드.
        name: 종목명.
        quantity: 주문 수량.
        price: 주문 단가.
        order_type: 주문 유형 (매수/매도).

    Returns:
        확인 메시지 문자열.
    """
    try:
        total = compute_order_amount(price, quantity)
        type_label = "매수" if order_type in ("buy", "매수") else "매도"

        lines = [
            f"{USER_NAME}, 다음 주문을 확인해 주세요.",
            "",
            f"주문유형: {type_label}",
            f"종목: {name} ({ticker})",
            f"수량: {quantity:,}주",
            f"단가: {price:,.0f}원",
            f"총 주문금액: {total:,.0f}원",
            "",
            "주문을 진행하시겠습니까? (예/아니오)",
        ]
        return "\n".join(lines)

    except Exception:
        logger.exception(
            "%s 주문 확인 메시지 생성 중 오류가 발생했습니다. ticker=%s",
            USER_NAME,
            ticker,
        )
        return f"{USER_NAME}, 주문 확인 메시지를 생성하는 중 오류가 발생했습니다."


def format_order_result(result: KisOrderResult) -> str:
    """주문 결과를 한국어 형식으로 포맷합니다.

    Args:
        result: KisOrderResult 주문 결과.

    Returns:
        포맷된 주문 결과 문자열.
    """
    try:
        if result.success:
            total = compute_order_amount(result.price, result.quantity)
            lines = [
                f"{USER_NAME}, 주문이 정상 접수되었습니다.",
                "",
                f"주문번호: {result.order_id}",
                f"종목: {result.name} ({result.ticker})",
                f"수량: {result.quantity:,}주",
                f"단가: {result.price:,.0f}원",
                f"총 주문금액: {total:,.0f}원",
            ]
            if result.message:
                lines.append(f"비고: {result.message}")
            return "\n".join(lines)
        else:
            lines = [
                f"{USER_NAME}, 주문이 실패했습니다.",
                "",
                f"종목: {result.name} ({result.ticker})",
            ]
            if result.message:
                lines.append(f"사유: {result.message}")
            return "\n".join(lines)

    except Exception:
        logger.exception(
            "%s 주문 결과 포맷 중 오류가 발생했습니다. ticker=%s",
            USER_NAME,
            getattr(result, "ticker", "unknown"),
        )
        return f"{USER_NAME}, 주문 결과를 표시하는 중 오류가 발생했습니다."


def format_kis_not_configured() -> str:
    """KIS API 미설정 안내 메시지를 반환합니다.

    Returns:
        KIS API 미연결 안내 문자열.
    """
    try:
        return (
            f"{USER_NAME}, KIS API가 연결되지 않았습니다. "
            f"시세 조회만 가능합니다."
        )
    except Exception:
        logger.exception("%s KIS 미설정 메시지 생성 중 오류.", USER_NAME)
        return "KIS API가 연결되지 않았습니다."


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(request_count: int, window_start: float) -> bool:
    """현재 요청이 초당 제한 이내인지 확인합니다.

    window_start 이후 경과 시간 내 request_count가 rest_per_second 이하이면
    True(허용)를 반환합니다.

    Args:
        request_count: 현재 윈도우 내 누적 요청 수.
        window_start: 윈도우 시작 시각 (time.time() 기준).

    Returns:
        True이면 요청 허용, False이면 제한 초과.
    """
    try:
        elapsed = time.time() - window_start
        max_per_second = RATE_LIMITS["rest_per_second"]

        if elapsed <= 0:
            logger.warning(
                "%s 레이트 리밋 윈도우 시작 시각이 유효하지 않습니다.", USER_NAME,
            )
            return False

        # 1초 윈도우 기준 검사
        if elapsed < 1.0:
            if request_count >= max_per_second:
                logger.warning(
                    "%s 초당 요청 제한(%d)에 도달했습니다. "
                    "현재 %d건 (%.2f초 경과)",
                    USER_NAME,
                    max_per_second,
                    request_count,
                    elapsed,
                )
                return False
        # 1초가 지났으면 윈도우를 리셋할 수 있으므로 허용
        return True

    except Exception:
        logger.exception("%s 레이트 리밋 확인 중 오류가 발생했습니다.", USER_NAME)
        return False


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def compute_order_amount(price: float, quantity: int) -> float:
    """주문 금액을 계산합니다.

    Args:
        price: 주문 단가.
        quantity: 주문 수량.

    Returns:
        총 주문 금액 (price * quantity).
    """
    try:
        amount = price * quantity
        return float(amount)
    except Exception:
        logger.exception(
            "%s 주문 금액 계산 중 오류가 발생했습니다. price=%s, quantity=%s",
            USER_NAME,
            price,
            quantity,
        )
        return 0.0


def get_default_safety() -> KisSafety:
    """기본 안전 장치 설정을 반환합니다.

    SAFETY 딕셔너리의 값을 KisSafety 데이터클래스로 변환합니다.

    Returns:
        기본 KisSafety 설정.
    """
    try:
        return KisSafety(
            require_confirmation=SAFETY["require_confirmation"],
            auto_trade=SAFETY["auto_trade"],
            max_daily_order_pct=SAFETY["max_daily_order_pct"],
            max_single_order_pct=SAFETY["max_single_order_pct"],
        )
    except Exception:
        logger.exception(
            "%s 기본 안전 설정 로드 중 오류가 발생했습니다.", USER_NAME,
        )
        return KisSafety()


# ---------------------------------------------------------------------------
# Stub API wrappers -- pykis 없이도 동작하도록 스텁 제공
# ---------------------------------------------------------------------------

def _get_pykis_client(config: KisConfig):
    """pykis 클라이언트 인스턴스를 생성합니다.

    pykis 라이브러리가 설치되어 있지 않으면 None을 반환합니다.
    실제 API 호출은 수행하지 않습니다 (안전 모드).

    Args:
        config: KIS API 설정.

    Returns:
        pykis 클라이언트 또는 None.
    """
    try:
        try:
            import pykis as _pykis  # noqa: F811
        except Exception:
            logger.info(
                "%s pykis를 불러올 수 없어 스텁 클라이언트를 반환합니다.",
                USER_NAME,
            )
            return None

        if not config.is_configured:
            logger.info(
                "%s KIS API가 설정되지 않아 클라이언트를 생성할 수 없습니다.",
                USER_NAME,
            )
            return None

        # NOTE: 실제 클라이언트 생성은 안전을 위해 비활성화합니다.
        # 실서비스 연동 시 아래 주석을 해제하세요.
        # client = _pykis.PyKis(
        #     id=config.hts_id,
        #     account=config.account_no,
        #     appkey=config.app_key,
        #     secretkey=config.app_secret,
        #     virtual=config.is_virtual,
        # )
        # return client

        logger.info(
            "%s 안전 모드: pykis 클라이언트 생성을 건너뜁니다.", USER_NAME,
        )
        return None

    except Exception:
        logger.exception(
            "%s pykis 클라이언트 생성 중 오류가 발생했습니다.", USER_NAME,
        )
        return None


def stub_get_price(ticker: str) -> KisPrice:
    """시세 조회 스텁 -- 실제 API 호출 없이 빈 데이터를 반환합니다.

    Args:
        ticker: 종목 코드.

    Returns:
        빈 KisPrice (price=0).
    """
    try:
        logger.info(
            "%s [%s] 시세 조회 스텁 호출. 실제 API 미연동 상태입니다.",
            USER_NAME,
            ticker,
        )
        return KisPrice(ticker=ticker)
    except Exception:
        logger.exception(
            "%s [%s] 시세 스텁 호출 중 오류.", USER_NAME, ticker,
        )
        return KisPrice(ticker=ticker)


def stub_get_balance(config: KisConfig) -> KisBalance:
    """잔고 조회 스텁 -- 실제 API 호출 없이 빈 데이터를 반환합니다.

    Args:
        config: KIS API 설정 (사용되지 않음, 인터페이스 일관성 유지).

    Returns:
        빈 KisBalance.
    """
    try:
        logger.info(
            "%s 잔고 조회 스텁 호출. 실제 API 미연동 상태입니다.", USER_NAME,
        )
        return KisBalance()
    except Exception:
        logger.exception("%s 잔고 스텁 호출 중 오류.", USER_NAME)
        return KisBalance()


def stub_place_order(
    config: KisConfig,
    ticker: str,
    name: str,
    quantity: int,
    price: float,
    order_type: str,
) -> KisOrderResult:
    """주문 실행 스텁 -- 실제 주문을 전송하지 않습니다.

    안전을 위해 항상 실패 결과를 반환합니다.
    실서비스 연동 시 이 함수를 실제 API 호출로 교체하세요.

    Args:
        config: KIS API 설정.
        ticker: 종목 코드.
        name: 종목명.
        quantity: 주문 수량.
        price: 주문 단가.
        order_type: 주문 유형 (buy/sell).

    Returns:
        KisOrderResult (success=False, 스텁 모드 안내).
    """
    try:
        logger.warning(
            "%s [%s] 주문 스텁 호출. 실제 주문은 전송되지 않습니다. "
            "(type=%s, qty=%d, price=%.0f)",
            USER_NAME,
            ticker,
            order_type,
            quantity,
            price,
        )
        return KisOrderResult(
            success=False,
            order_id="",
            ticker=ticker,
            name=name,
            quantity=quantity,
            price=price,
            message=(
                f"{USER_NAME}, 현재 스텁 모드입니다. "
                f"실제 주문이 전송되지 않았습니다. "
                f"실서비스 연동 후 다시 시도해 주세요."
            ),
        )
    except Exception:
        logger.exception(
            "%s [%s] 주문 스텁 호출 중 오류.", USER_NAME, ticker,
        )
        return KisOrderResult(
            success=False,
            ticker=ticker,
            name=name,
            message=f"{USER_NAME}, 주문 처리 중 오류가 발생했습니다.",
        )
