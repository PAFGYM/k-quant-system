"""Data quality validation with cross-verification (데이터 품질 검증).

Provides price/volume anomaly detection, cross-source verification,
and quality reporting. All functions are pure computation with
no external API calls.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

# Thresholds
PRICE_CHANGE_LIMIT = 0.30   # 30% daily limit
VOLUME_SPIKE_LIMIT = 10.0   # 10x average
PRICE_TOLERANCE_DEFAULT = 0.01  # 1% cross-source tolerance


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PriceValidation:
    """단일 가격 검증 결과."""

    ticker: str = ""
    valid: bool = True
    change_pct: float = 0.0
    anomaly_type: str = ""


@dataclass
class VolumeValidation:
    """단일 거래량 검증 결과."""

    ticker: str = ""
    valid: bool = True
    ratio: float = 0.0
    anomaly_type: str = ""


@dataclass
class PriceMismatch:
    """교차 검증 가격 불일치."""

    ticker: str = ""
    source1_price: float = 0.0
    source2_price: float = 0.0
    diff_pct: float = 0.0


# ---------------------------------------------------------------------------
# Price validation
# ---------------------------------------------------------------------------

def validate_price(
    current: float,
    prev_close: float,
    ticker: str = "",
) -> dict:
    """가격 유효성을 검증합니다.

    전일종가 대비 변동률이 30%(가격제한폭)를 초과하면 이상으로 판단합니다.
    """
    try:
        if prev_close <= 0:
            logger.warning("[%s] 전일종가가 0 이하입니다: %.2f", ticker, prev_close)
            return {
                "valid": False,
                "change_pct": 0.0,
                "anomaly_type": "invalid_prev_close",
            }

        change_pct = (current - prev_close) / prev_close
        is_anomaly = abs(change_pct) > PRICE_CHANGE_LIMIT

        if is_anomaly:
            logger.warning(
                "[%s] 가격 이상 감지: 현재가=%.0f, 전일종가=%.0f, 변동률=%.2f%%",
                ticker, current, prev_close, change_pct * 100,
            )

        return {
            "valid": not is_anomaly,
            "change_pct": round(change_pct, 4),
            "anomaly_type": "price_limit_exceeded" if is_anomaly else "",
        }

    except Exception as e:
        logger.error("[%s] 가격 검증 실패: %s", ticker, e, exc_info=True)
        return {"valid": False, "change_pct": 0.0, "anomaly_type": "validation_error"}


# ---------------------------------------------------------------------------
# Volume validation
# ---------------------------------------------------------------------------

def validate_volume(
    volume: float,
    avg_volume: float,
    ticker: str = "",
) -> dict:
    """거래량 유효성을 검증합니다.

    평균 거래량의 10배를 초과하면 이상으로 판단합니다.
    """
    try:
        if avg_volume <= 0:
            logger.warning("[%s] 평균 거래량이 0 이하입니다: %.0f", ticker, avg_volume)
            return {
                "valid": False,
                "ratio": 0.0,
                "anomaly_type": "invalid_avg_volume",
            }

        ratio = volume / avg_volume
        is_anomaly = ratio > VOLUME_SPIKE_LIMIT

        if is_anomaly:
            logger.warning(
                "[%s] 거래량 이상 감지: 거래량=%.0f, 평균=%.0f, 배율=%.1fx",
                ticker, volume, avg_volume, ratio,
            )

        return {
            "valid": not is_anomaly,
            "ratio": round(ratio, 2),
            "anomaly_type": "volume_spike" if is_anomaly else "",
        }

    except Exception as e:
        logger.error("[%s] 거래량 검증 실패: %s", ticker, e, exc_info=True)
        return {"valid": False, "ratio": 0.0, "anomaly_type": "validation_error"}


# ---------------------------------------------------------------------------
# Anomaly detection (batch)
# ---------------------------------------------------------------------------

def detect_anomalies(prices: list[dict]) -> list[dict]:
    """가격/거래량 이상 항목을 일괄 탐지합니다.

    각 항목은 {ticker, price, prev_close, volume, avg_volume} 형태입니다.
    """
    try:
        anomalies: list[dict] = []

        for item in prices:
            ticker = item.get("ticker", "")
            price = item.get("price", 0)
            prev_close = item.get("prev_close", 0)
            volume = item.get("volume", 0)
            avg_volume = item.get("avg_volume", 0)

            price_result = validate_price(price, prev_close, ticker)
            if not price_result["valid"]:
                anomalies.append({
                    "ticker": ticker,
                    "type": "price",
                    "detail": price_result["anomaly_type"],
                    "change_pct": price_result["change_pct"],
                })

            volume_result = validate_volume(volume, avg_volume, ticker)
            if not volume_result["valid"]:
                anomalies.append({
                    "ticker": ticker,
                    "type": "volume",
                    "detail": volume_result["anomaly_type"],
                    "ratio": volume_result["ratio"],
                })

        logger.info("이상 탐지 완료: %d건 중 %d건 이상", len(prices), len(anomalies))
        return anomalies

    except Exception as e:
        logger.error("이상 탐지 실패: %s", e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Cross-validation between sources
# ---------------------------------------------------------------------------

def cross_validate_prices(
    source1_prices: dict[str, float],
    source2_prices: dict[str, float],
    tolerance: float = PRICE_TOLERANCE_DEFAULT,
) -> list[dict]:
    """두 데이터 소스 간 가격을 교차 검증합니다.

    동일 종목의 가격 차이가 tolerance(기본 1%)를 초과하면 불일치로 판단합니다.
    """
    try:
        mismatches: list[dict] = []
        common_tickers = set(source1_prices.keys()) & set(source2_prices.keys())

        for ticker in sorted(common_tickers):
            p1 = source1_prices[ticker]
            p2 = source2_prices[ticker]

            if p1 <= 0 or p2 <= 0:
                continue

            diff_pct = abs(p1 - p2) / max(p1, p2)
            if diff_pct > tolerance:
                mismatches.append({
                    "ticker": ticker,
                    "source1_price": p1,
                    "source2_price": p2,
                    "diff_pct": round(diff_pct, 4),
                })

        if mismatches:
            logger.warning("교차 검증 불일치 %d건 발견", len(mismatches))

        return mismatches

    except Exception as e:
        logger.error("교차 검증 실패: %s", e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def generate_quality_report(
    anomalies: list[dict],
    mismatches: list[dict],
    duplicates_removed: int = 0,
    nulls_filled: int = 0,
) -> str:
    """데이터 품질 리포트를 텔레그램 형식으로 생성합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
        lines = [
            f"[데이터 품질 리포트] {now}",
            f"{USER_NAME}, 데이터 품질 점검 결과입니다.",
            "",
            "-- 이상 감지 --",
            f"  이상 항목: {len(anomalies)}건",
        ]

        price_anomalies = [a for a in anomalies if a.get("type") == "price"]
        volume_anomalies = [a for a in anomalies if a.get("type") == "volume"]

        if price_anomalies:
            lines.append(f"  가격 이상: {len(price_anomalies)}건")
            for a in price_anomalies[:5]:
                lines.append(
                    f"    {a['ticker']}: 변동률 {a.get('change_pct', 0) * 100:+.1f}%"
                )

        if volume_anomalies:
            lines.append(f"  거래량 이상: {len(volume_anomalies)}건")
            for a in volume_anomalies[:5]:
                lines.append(
                    f"    {a['ticker']}: 평균 대비 {a.get('ratio', 0):.1f}배"
                )

        lines.append("")
        lines.append("-- 교차 검증 --")
        lines.append(f"  소스 간 불일치: {len(mismatches)}건")
        for m in mismatches[:5]:
            lines.append(
                f"    {m['ticker']}: {m['source1_price']:,.0f} vs {m['source2_price']:,.0f} ({m['diff_pct'] * 100:.1f}%)"
            )

        lines.append("")
        lines.append("-- 자동 정제 --")
        lines.append(f"  중복 제거: {duplicates_removed}건")
        lines.append(f"  결측 보정: {nulls_filled}건")

        total_issues = len(anomalies) + len(mismatches) + duplicates_removed + nulls_filled
        if total_issues == 0:
            lines.append("")
            lines.append("전체 데이터 품질 양호합니다.")
        else:
            lines.append("")
            lines.append(f"총 {total_issues}건 이슈를 확인했습니다. 검토해 주세요.")

        return "\n".join(lines)

    except Exception as e:
        logger.error("품질 리포트 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 데이터 품질 리포트 생성 중 오류가 발생했습니다."


def format_data_alert(anomalies: list[dict]) -> str:
    """이상 항목 알림 메시지를 생성합니다."""
    try:
        if not anomalies:
            return ""

        now = datetime.now(tz=KST).strftime("%H:%M")
        lines = [
            f"[데이터 이상 알림] {now}",
            f"{USER_NAME}, 데이터 이상이 감지되었습니다.",
            "",
        ]

        for a in anomalies[:10]:
            ticker = a.get("ticker", "")
            atype = a.get("type", "")
            if atype == "price":
                pct = a.get("change_pct", 0) * 100
                lines.append(f"  {ticker}: 가격 변동 {pct:+.1f}% (제한폭 초과 의심)")
            elif atype == "volume":
                ratio = a.get("ratio", 0)
                lines.append(f"  {ticker}: 거래량 {ratio:.1f}배 (스파이크)")
            else:
                lines.append(f"  {ticker}: {a.get('detail', '알 수 없는 이상')}")

        if len(anomalies) > 10:
            lines.append(f"  ... 외 {len(anomalies) - 10}건")

        lines.append("")
        lines.append("해당 종목의 데이터를 재확인해 주세요.")

        return "\n".join(lines)

    except Exception as e:
        logger.error("이상 알림 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 데이터 이상 알림 생성 중 오류가 발생했습니다."
