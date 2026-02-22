"""Trade registration system (매수 보고).

Two ways to register trades:
  1. Text message: "에코프로 100주 178500원에 샀어"
  2. Image: Securities app screenshot -> OCR (handled externally)

After registration:
  -> Save to DB
  -> Ask investment horizon (scalp/swing/mid/long)
  -> Set automatic monitoring parameters
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HORIZON_SETTINGS: dict[str, dict] = {
    "scalp": {
        "label": "단타 (1~3일)",
        "trailing_stop": 0.03,
        "target_profit": 0.05,
        "max_target": 0.08,
    },
    "swing": {
        "label": "스윙 (1~2주)",
        "trailing_stop": 0.05,
        "target_profit": 0.10,
        "max_target": 0.15,
    },
    "mid": {
        "label": "중기 (1~3개월)",
        "trailing_stop": 0.08,
        "target_profit": 0.20,
        "max_target": 0.30,
    },
    "long": {
        "label": "장기 (3개월+)",
        "trailing_stop": 0.15,
        "target_profit": 0.40,
        "max_target": 1.00,
    },
}

# Check intervals for monitoring by horizon (seconds)
_CHECK_INTERVAL: dict[str, int] = {
    "scalp": 30,
    "swing": 300,
    "mid": 1800,
    "long": 3600,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TradeInfo:
    """매수 정보 (파싱 결과)."""

    ticker: str = ""
    name: str = ""
    quantity: int = 0
    price: float = 0.0
    total_amount: float = 0.0
    source: str = "text"           # "text" or "image"
    raw_message: str = ""


@dataclass
class RegisteredTrade:
    """등록 완료된 매수 건."""

    trade_info: TradeInfo = field(default_factory=TradeInfo)
    horizon: str = ""
    trailing_stop_pct: float = 0.0
    target_profit_pct: float = 0.0
    registered_at: str = ""


# ---------------------------------------------------------------------------
# Regex patterns for Korean trade message parsing
# ---------------------------------------------------------------------------

# Numbers: handles commas (e.g. 178,500)
_NUM = r"[\d,]+"

# Pattern 1: "에코프로 100주 178500원에 샀어"
_PAT_NAME_QTY_PRICE = re.compile(
    rf"(?P<name>\S+)\s+(?P<qty>{_NUM})\s*주\s+(?P<price>{_NUM})\s*원",
    re.UNICODE,
)

# Pattern 2: "에코프로 매수 178500원 100주"
_PAT_NAME_BUY_PRICE_QTY = re.compile(
    rf"(?P<name>\S+)\s+매수\s+(?P<price>{_NUM})\s*원\s+(?P<qty>{_NUM})\s*주",
    re.UNICODE,
)

# Pattern 3: "삼성전자 50주 매수" (no price)
_PAT_NAME_QTY_BUY = re.compile(
    rf"(?P<name>\S+)\s+(?P<qty>{_NUM})\s*주\s*매수",
    re.UNICODE,
)

# Pattern 4: "247540 30주 85000" (ticker code, qty, price - no units)
_PAT_CODE_QTY_PRICE = re.compile(
    rf"(?P<code>\d{{6}})\s+(?P<qty>{_NUM})\s*주?\s+(?P<price>{_NUM})",
    re.UNICODE,
)

# Pattern 5: "에코프로 100주 샀어" (name, qty, no price)
_PAT_NAME_QTY_BOUGHT = re.compile(
    rf"(?P<name>\S+)\s+(?P<qty>{_NUM})\s*주\s*샀어",
    re.UNICODE,
)


def _parse_int(s: str) -> int:
    """Comma-separated number string to int."""
    return int(s.replace(",", ""))


def _parse_float(s: str) -> float:
    """Comma-separated number string to float."""
    return float(s.replace(",", ""))


# ---------------------------------------------------------------------------
# 1. parse_trade_text
# ---------------------------------------------------------------------------

def parse_trade_text(message: str) -> TradeInfo | None:
    """한국어 매수 메시지에서 종목/수량/가격을 추출한다.

    지원 패턴:
      - "에코프로 100주 178500원에 샀어"
      - "삼성전자 50주 매수"
      - "247540 30주 85000"
      - "에코프로 100주 샀어" (가격 없음)
      - "에코프로 매수 178500원 100주"

    파싱 실패 시 None 반환.
    """
    try:
        msg = message.strip()
        if not msg:
            logger.warning("빈 메시지 수신")
            return None

        # Try each pattern in priority order

        # Pattern 1: name qty주 price원
        m = _PAT_NAME_QTY_PRICE.search(msg)
        if m:
            name = m.group("name")
            qty = _parse_int(m.group("qty"))
            price = _parse_float(m.group("price"))
            ticker = name if name.isdigit() and len(name) == 6 else ""
            display_name = "" if ticker else name
            return TradeInfo(
                ticker=ticker,
                name=display_name,
                quantity=qty,
                price=price,
                total_amount=qty * price,
                source="text",
                raw_message=message,
            )

        # Pattern 2: name 매수 price원 qty주
        m = _PAT_NAME_BUY_PRICE_QTY.search(msg)
        if m:
            name = m.group("name")
            qty = _parse_int(m.group("qty"))
            price = _parse_float(m.group("price"))
            ticker = name if name.isdigit() and len(name) == 6 else ""
            display_name = "" if ticker else name
            return TradeInfo(
                ticker=ticker,
                name=display_name,
                quantity=qty,
                price=price,
                total_amount=qty * price,
                source="text",
                raw_message=message,
            )

        # Pattern 3: name qty주 매수 (no price)
        m = _PAT_NAME_QTY_BUY.search(msg)
        if m:
            name = m.group("name")
            qty = _parse_int(m.group("qty"))
            ticker = name if name.isdigit() and len(name) == 6 else ""
            display_name = "" if ticker else name
            return TradeInfo(
                ticker=ticker,
                name=display_name,
                quantity=qty,
                price=0.0,
                total_amount=0.0,
                source="text",
                raw_message=message,
            )

        # Pattern 4: 6-digit code qty price
        m = _PAT_CODE_QTY_PRICE.search(msg)
        if m:
            code = m.group("code")
            qty = _parse_int(m.group("qty"))
            price = _parse_float(m.group("price"))
            return TradeInfo(
                ticker=code,
                name="",
                quantity=qty,
                price=price,
                total_amount=qty * price,
                source="text",
                raw_message=message,
            )

        # Pattern 5: name qty주 샀어 (no price)
        m = _PAT_NAME_QTY_BOUGHT.search(msg)
        if m:
            name = m.group("name")
            qty = _parse_int(m.group("qty"))
            ticker = name if name.isdigit() and len(name) == 6 else ""
            display_name = "" if ticker else name
            return TradeInfo(
                ticker=ticker,
                name=display_name,
                quantity=qty,
                price=0.0,
                total_amount=0.0,
                source="text",
                raw_message=message,
            )

        logger.info("매수 메시지 파싱 실패: %s", msg)
        return None

    except Exception:
        logger.exception("parse_trade_text 오류 (message=%s)", message)
        return None


# ---------------------------------------------------------------------------
# 2. parse_trade_image_result
# ---------------------------------------------------------------------------

def parse_trade_image_result(ocr_json: list[dict]) -> list[TradeInfo]:
    """Vision API OCR 결과(외부에서 전달받은 JSON)를 TradeInfo 목록으로 변환한다.

    각 dict 에는 ticker, name, quantity, price 가 있을 수 있으며
    null/빈 값은 무시한다. 유효하지 않은 항목은 필터링한다.
    """
    try:
        if not ocr_json:
            logger.info("OCR 결과가 비어있습니다")
            return []

        results: list[TradeInfo] = []

        for idx, item in enumerate(ocr_json):
            try:
                ticker = str(item.get("ticker") or "").strip()
                name = str(item.get("name") or "").strip()
                raw_qty = item.get("quantity")
                raw_price = item.get("price")

                # 종목 식별자가 하나도 없으면 건너뜀
                if not ticker and not name:
                    logger.debug("OCR 항목 #%d: ticker/name 없음, 건너뜀", idx)
                    continue

                # 수량 파싱
                qty = 0
                if raw_qty is not None and str(raw_qty).strip():
                    qty = int(float(str(raw_qty).replace(",", "")))

                if qty <= 0:
                    logger.debug("OCR 항목 #%d: 수량 0 이하, 건너뜀", idx)
                    continue

                # 가격 파싱 (없을 수도 있음)
                price = 0.0
                if raw_price is not None and str(raw_price).strip():
                    price = float(str(raw_price).replace(",", ""))

                total = qty * price if price > 0 else 0.0

                results.append(TradeInfo(
                    ticker=ticker,
                    name=name,
                    quantity=qty,
                    price=price,
                    total_amount=total,
                    source="image",
                    raw_message=str(item),
                ))
            except (ValueError, TypeError):
                logger.warning("OCR 항목 #%d 파싱 실패: %s", idx, item)
                continue

        logger.info("OCR 파싱 결과: %d건 중 %d건 유효", len(ocr_json), len(results))
        return results

    except Exception:
        logger.exception("parse_trade_image_result 오류")
        return []


# ---------------------------------------------------------------------------
# 3. validate_trade_info
# ---------------------------------------------------------------------------

def validate_trade_info(trade: TradeInfo) -> tuple[bool, str]:
    """매수 정보의 유효성을 검증한다.

    Returns:
        (is_valid, error_message) 형태의 튜플.
        유효하면 (True, ""), 아니면 (False, 사유).
    """
    try:
        if not trade.name and not trade.ticker:
            return False, "종목명 또는 종목코드가 필요합니다"

        if trade.quantity <= 0:
            return False, f"수량이 올바르지 않습니다 (입력값: {trade.quantity})"

        if trade.price < 0:
            return False, f"가격이 올바르지 않습니다 (입력값: {trade.price})"

        # price == 0 은 허용 (나중에 조회 가능)
        return True, ""

    except Exception:
        logger.exception("validate_trade_info 오류")
        return False, "검증 중 알 수 없는 오류가 발생했습니다"


# ---------------------------------------------------------------------------
# 4. compute_monitoring_params
# ---------------------------------------------------------------------------

def compute_monitoring_params(price: float, horizon: str) -> dict:
    """매수가와 투자 기간에 따른 모니터링 파라미터를 산출한다.

    Returns:
        {
            trailing_stop_price: float,
            target_price: float,
            max_target_price: float,
            check_interval_seconds: int,
        }
    """
    try:
        settings = HORIZON_SETTINGS.get(horizon)
        if settings is None:
            logger.warning("알 수 없는 horizon: %s, scalp 기본값 사용", horizon)
            settings = HORIZON_SETTINGS["scalp"]
            horizon = "scalp"

        trailing_pct = settings["trailing_stop"]
        target_pct = settings["target_profit"]
        max_pct = settings["max_target"]
        interval = _CHECK_INTERVAL.get(horizon, 300)

        trailing_stop_price = round(price * (1 - trailing_pct), 2)
        target_price = round(price * (1 + target_pct), 2)
        max_target_price = round(price * (1 + max_pct), 2)

        return {
            "trailing_stop_price": trailing_stop_price,
            "target_price": target_price,
            "max_target_price": max_target_price,
            "check_interval_seconds": interval,
        }

    except Exception:
        logger.exception("compute_monitoring_params 오류 (price=%s, horizon=%s)", price, horizon)
        return {
            "trailing_stop_price": 0.0,
            "target_price": 0.0,
            "max_target_price": 0.0,
            "check_interval_seconds": 300,
        }


# ---------------------------------------------------------------------------
# 5. format_trade_confirmation
# ---------------------------------------------------------------------------

def _fmt_price(value: float) -> str:
    """숫자를 천 단위 콤마 포맷으로 변환 (정수부만)."""
    if value == 0:
        return "미입력"
    return f"{int(value):,}원"


def format_trade_confirmation(trade: TradeInfo) -> str:
    """매수 확인 메시지를 생성한다 (투자 기간 선택 안내 포함).

    Returns:
        한국어 확인 메시지 문자열.
    """
    try:
        display = trade.name or trade.ticker or "알 수 없는 종목"
        ticker_part = f" ({trade.ticker})" if trade.ticker and trade.name else ""

        lines = [
            f"{USER_NAME}, 매수 등록 확인입니다.",
            "",
            f"종목: {display}{ticker_part}",
            f"수량: {trade.quantity:,}주",
            f"매수가: {_fmt_price(trade.price)}",
        ]

        if trade.total_amount > 0:
            lines.append(f"매수금액: {_fmt_price(trade.total_amount)}")

        lines.extend([
            "",
            "투자 기간을 선택해주세요:",
        ])

        for key, cfg in HORIZON_SETTINGS.items():
            lines.append(f"  [{key}] {cfg['label']}")

        lines.extend([
            "",
            "선택하신 기간에 맞춰 자동 모니터링을 설정합니다.",
        ])

        return "\n".join(lines)

    except Exception:
        logger.exception("format_trade_confirmation 오류")
        return f"{USER_NAME}, 매수 정보를 확인해주세요."


# ---------------------------------------------------------------------------
# 6. format_registered_trade
# ---------------------------------------------------------------------------

def format_registered_trade(reg: RegisteredTrade) -> str:
    """등록 완료 안내 메시지를 생성한다 (모니터링 파라미터 포함).

    Returns:
        한국어 등록 완료 메시지 문자열.
    """
    try:
        t = reg.trade_info
        display = t.name or t.ticker or "알 수 없는 종목"
        ticker_part = f" ({t.ticker})" if t.ticker and t.name else ""

        horizon_label = HORIZON_SETTINGS.get(reg.horizon, {}).get("label", reg.horizon)
        params = compute_monitoring_params(t.price, reg.horizon)

        lines = [
            f"{USER_NAME}, 매수 등록이 완료되었습니다.",
            "",
            f"종목: {display}{ticker_part}",
            f"수량: {t.quantity:,}주",
            f"매수가: {_fmt_price(t.price)}",
        ]

        if t.total_amount > 0:
            lines.append(f"매수금액: {_fmt_price(t.total_amount)}")

        lines.extend([
            f"투자 기간: {horizon_label}",
            "",
            "-- 모니터링 설정 --",
            f"트레일링 스탑: {_fmt_price(params['trailing_stop_price'])} ({reg.trailing_stop_pct * 100:.0f}%)",
            f"1차 목표가: {_fmt_price(params['target_price'])} (+{reg.target_profit_pct * 100:.0f}%)",
            f"최대 목표가: {_fmt_price(params['max_target_price'])}",
            f"체크 주기: {params['check_interval_seconds']}초",
            "",
            f"등록 시각: {reg.registered_at}",
            "",
            "자동 모니터링이 시작됩니다.",
            "가격 변동 시 알림을 보내드리겠습니다.",
        ])

        return "\n".join(lines)

    except Exception:
        logger.exception("format_registered_trade 오류")
        return f"{USER_NAME}, 매수 등록이 완료되었습니다. 상세 정보 확인 중 오류가 발생했습니다."


# ---------------------------------------------------------------------------
# 7. format_trade_image_preview
# ---------------------------------------------------------------------------

def format_trade_image_preview(trades: list[TradeInfo]) -> str:
    """OCR로 추출된 매수 목록의 미리보기 메시지를 생성한다.

    Returns:
        한국어 미리보기 메시지 문자열.
    """
    try:
        if not trades:
            return f"{USER_NAME}, 이미지에서 매수 내역을 찾지 못했습니다."

        lines = [
            f"{USER_NAME}, 이미지에서 {len(trades)}건의 매수 내역을 찾았습니다.",
            "",
        ]

        for i, t in enumerate(trades, 1):
            display = t.name or t.ticker or "???"
            ticker_part = f" ({t.ticker})" if t.ticker and t.name else ""
            price_str = _fmt_price(t.price)
            amount_str = _fmt_price(t.total_amount) if t.total_amount > 0 else ""

            line = f"{i}. {display}{ticker_part} / {t.quantity:,}주 / {price_str}"
            if amount_str:
                line += f" / 총 {amount_str}"
            lines.append(line)

        lines.extend([
            "",
            "위 내역이 맞으면 각 종목의 투자 기간을 선택해주세요.",
            "수정이 필요하면 말씀해주세요.",
        ])

        return "\n".join(lines)

    except Exception:
        logger.exception("format_trade_image_preview 오류")
        return f"{USER_NAME}, 매수 내역 미리보기 생성 중 오류가 발생했습니다."


# ---------------------------------------------------------------------------
# 8. create_registered_trade
# ---------------------------------------------------------------------------

def create_registered_trade(trade: TradeInfo, horizon: str) -> RegisteredTrade:
    """TradeInfo와 투자 기간으로 RegisteredTrade를 생성한다.

    Args:
        trade: 파싱된 매수 정보.
        horizon: 투자 기간 키 (scalp/swing/mid/long).

    Returns:
        모니터링 파라미터가 설정된 RegisteredTrade.
    """
    try:
        settings = HORIZON_SETTINGS.get(horizon)
        if settings is None:
            logger.warning("알 수 없는 horizon '%s', scalp 기본값 적용", horizon)
            settings = HORIZON_SETTINGS["scalp"]
            horizon = "scalp"

        now_kst = datetime.now(KST)
        registered_at = now_kst.strftime("%Y-%m-%d %H:%M:%S KST")

        return RegisteredTrade(
            trade_info=trade,
            horizon=horizon,
            trailing_stop_pct=settings["trailing_stop"],
            target_profit_pct=settings["target_profit"],
            registered_at=registered_at,
        )

    except Exception:
        logger.exception("create_registered_trade 오류")
        now_kst = datetime.now(KST)
        return RegisteredTrade(
            trade_info=trade,
            horizon=horizon or "scalp",
            trailing_stop_pct=0.03,
            target_profit_pct=0.05,
            registered_at=now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        )
