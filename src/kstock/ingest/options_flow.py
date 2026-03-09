"""v10.5: KRX KOSPI200 옵션 통계 수집.

KRX data.krx.co.kr API에서 일일 옵션 거래량/OI/PCR 데이터 수집.
- 콜/풋 거래량, 미결제약정(OI)
- PCR(거래량), PCR(OI)
- Max Pain 근사 산출
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
}

# KRX API: KOSPI200 옵션 일별 통계
_KRX_OPT_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"


def fetch_krx_options_daily(trade_date: str | None = None) -> dict:
    """KRX에서 KOSPI200 옵션 일별 거래 통계 수집.

    Args:
        trade_date: YYYYMMDD 형식. None이면 오늘.

    Returns:
        dict with keys: date, call_volume, put_volume, call_oi, put_oi,
        pcr_volume, pcr_oi, strikes (행사가별 데이터)
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")

    # KRX 옵션 전체 시세 (MDCSTAT12501 - 행사가별)
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT12501",
        "locale": "ko_KR",
        "prodId": "KRDRVOPK2I",  # KOSPI200 옵션
        "trdDd": trade_date,
        "share": "1",
        "money": "1",
        "csvxls_is498": "false",
    }

    try:
        resp = requests.post(_KRX_OPT_URL, data=payload, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("KRX options fetch failed: %s", e)
        return {}

    items = data.get("output", [])
    if not items:
        # 대체: 상품별 통계 (MDCSTAT12001)
        payload_alt = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT12001",
            "locale": "ko_KR",
            "prodId": "KRDRVOPK2I",
            "trdDd": trade_date,
            "trdDdBox1": trade_date,
            "trdDdBox2": trade_date,
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
        }
        try:
            resp = requests.post(_KRX_OPT_URL, data=payload_alt, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("output", [])
        except Exception as e2:
            logger.warning("KRX options alt fetch failed: %s", e2)
            return {}

    if not items:
        logger.info("KRX options: no data for %s", trade_date)
        return {}

    # 행사가별 데이터 파싱
    call_volume_total = 0
    put_volume_total = 0
    call_oi_total = 0
    put_oi_total = 0
    strikes: list[dict] = []

    for item in items:
        try:
            strike = _parse_number(item.get("ISU_NM", item.get("STRK_PRC", "0")))
            c_vol = _parse_int(item.get("CALL_TRDVOL", item.get("ACC_TRDVOL", "0")))
            p_vol = _parse_int(item.get("PUT_TRDVOL", "0"))
            c_oi = _parse_int(item.get("CALL_OPNINT_QTY", item.get("OPNINT_QTY", "0")))
            p_oi = _parse_int(item.get("PUT_OPNINT_QTY", "0"))

            # 전체 통계 (콜/풋 구분이 없는 경우 ISU_NM으로 판별)
            isu_nm = item.get("ISU_NM", "")
            if "콜" in isu_nm or "Call" in isu_nm.lower():
                call_volume_total += c_vol or _parse_int(item.get("ACC_TRDVOL", "0"))
                call_oi_total += c_oi or _parse_int(item.get("OPNINT_QTY", "0"))
            elif "풋" in isu_nm or "Put" in isu_nm.lower():
                put_volume_total += p_vol or _parse_int(item.get("ACC_TRDVOL", "0"))
                put_oi_total += p_oi or _parse_int(item.get("OPNINT_QTY", "0"))
            else:
                # 행사가별 콜/풋 분리된 형태
                call_volume_total += c_vol
                put_volume_total += p_vol
                call_oi_total += c_oi
                put_oi_total += p_oi

            if strike > 0:
                strikes.append({
                    "strike": strike,
                    "call_volume": c_vol,
                    "put_volume": p_vol,
                    "call_oi": c_oi,
                    "put_oi": p_oi,
                })
        except Exception:
            continue

    # PCR 계산
    pcr_volume = put_volume_total / call_volume_total if call_volume_total > 0 else 0
    pcr_oi = put_oi_total / call_oi_total if call_oi_total > 0 else 0

    # Max Pain 근사 (OI 기반)
    max_pain = _calculate_max_pain(strikes) if strikes else 0

    result = {
        "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
        "call_volume": call_volume_total,
        "put_volume": put_volume_total,
        "call_oi": call_oi_total,
        "put_oi": put_oi_total,
        "pcr_volume": round(pcr_volume, 3),
        "pcr_oi": round(pcr_oi, 3),
        "max_pain": max_pain,
        "strikes": strikes[:20],  # 상위 20개만
        "data_json": json.dumps({"source": "KRX", "items_count": len(items)}, ensure_ascii=False),
    }

    logger.info(
        "KRX options: call_vol=%d put_vol=%d PCR(vol)=%.3f PCR(oi)=%.3f max_pain=%.0f",
        call_volume_total, put_volume_total, pcr_volume, pcr_oi, max_pain,
    )
    return result


def analyze_options_flow(current: dict, previous: list[dict] | None = None) -> dict:
    """옵션 PCR 데이터 분석 + 시그널 판정.

    Args:
        current: 오늘 옵션 데이터
        previous: 최근 5일 데이터

    Returns:
        dict with signal, summary, pcr_trend, etc.
    """
    if not current:
        return {"signal": "데이터 없음", "summary": "옵션 데이터 없음"}

    pcr_vol = current.get("pcr_volume", 0)
    pcr_oi = current.get("pcr_oi", 0)
    max_pain = current.get("max_pain", 0)

    # 시그널 판정
    signal = "중립"
    warning = ""

    # PCR > 1.2 → 풋 과다 → 공포 / 역발상 기회
    if pcr_vol >= 1.5:
        signal = "극공포 (역발상 기회)"
        warning = "PCR 1.5+: 풋 거래 극단적 과다 → 바닥 근처 가능성"
    elif pcr_vol >= 1.2:
        signal = "공포 증가"
        warning = "PCR 1.2+: 풋 거래 과다 → 하락 우려 확대"
    elif pcr_vol <= 0.5:
        signal = "과열 (콜 과다)"
        warning = "PCR 0.5 이하: 콜 거래 극단적 → 과열 경고"
    elif pcr_vol <= 0.7:
        signal = "낙관 과열"
        warning = "PCR 0.7 이하: 콜 우위 → 단기 과열 주의"

    # 추세 분석
    pcr_trend = "flat"
    if previous and len(previous) >= 3:
        recent_pcrs = [p.get("pcr_volume", 0) for p in previous[:3]]
        avg_prev = sum(recent_pcrs) / len(recent_pcrs) if recent_pcrs else 0
        if avg_prev > 0:
            change = (pcr_vol - avg_prev) / avg_prev * 100
            if change > 15:
                pcr_trend = "rising"
            elif change < -15:
                pcr_trend = "falling"

    summary_parts = [
        f"PCR(거래량): {pcr_vol:.3f} | PCR(OI): {pcr_oi:.3f}",
        f"콜 거래량: {current.get('call_volume', 0):,} | 풋 거래량: {current.get('put_volume', 0):,}",
        f"콜 OI: {current.get('call_oi', 0):,} | 풋 OI: {current.get('put_oi', 0):,}",
    ]
    if max_pain > 0:
        summary_parts.append(f"Max Pain: {max_pain:.0f}")
    if warning:
        summary_parts.append(f"⚠️ {warning}")

    return {
        "signal": signal,
        "summary": "\n".join(summary_parts),
        "pcr_volume": pcr_vol,
        "pcr_oi": pcr_oi,
        "max_pain": max_pain,
        "pcr_trend": pcr_trend,
        "warning": warning,
    }


def format_options_flow(analysis: dict) -> str:
    """텔레그램 표시용 옵션 PCR 포맷."""
    signal = analysis.get("signal", "데이터 없음")
    signal_emoji = {
        "중립": "⚪",
        "낙관 과열": "🟡",
        "과열 (콜 과다)": "🔴",
        "공포 증가": "🟠",
        "극공포 (역발상 기회)": "💀",
    }.get(signal, "⚪")

    pcr_vol = analysis.get("pcr_volume", 0)
    pcr_oi = analysis.get("pcr_oi", 0)
    max_pain = analysis.get("max_pain", 0)
    trend = analysis.get("pcr_trend", "flat")
    trend_arrow = {"rising": "📈", "falling": "📉", "flat": "➡️"}.get(trend, "➡️")
    warning = analysis.get("warning", "")

    lines = [
        f"{signal_emoji} 옵션 PCR: {signal}",
        f"  PCR(거래량): {pcr_vol:.3f} {trend_arrow} | PCR(OI): {pcr_oi:.3f}",
    ]
    if max_pain > 0:
        lines.append(f"  Max Pain: {max_pain:.0f}")
    if warning:
        lines.append(f"  ⚠️ {warning}")

    return "\n".join(lines)


def _parse_number(s: str) -> float:
    """문자열에서 숫자 추출."""
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0.0


def _parse_int(s: str) -> int:
    """문자열에서 정수 추출."""
    try:
        return int(str(s).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0


def _calculate_max_pain(strikes: list[dict]) -> float:
    """Max Pain 근사 산출 (행사가별 OI 기반).

    Max Pain: 옵션 매도자의 손실이 최소화되는 행사가.
    각 행사가에서 콜/풋 OI의 내재가치 합계를 계산하여 최소점 산출.
    """
    if not strikes:
        return 0

    valid = [s for s in strikes if s.get("strike", 0) > 0]
    if not valid:
        return 0

    min_pain = float("inf")
    max_pain_strike = 0

    for candidate in valid:
        test_price = candidate["strike"]
        total_pain = 0
        for s in valid:
            strike = s["strike"]
            c_oi = s.get("call_oi", 0)
            p_oi = s.get("put_oi", 0)
            # 콜: max(0, test_price - strike) * call_oi
            if test_price > strike:
                total_pain += (test_price - strike) * c_oi
            # 풋: max(0, strike - test_price) * put_oi
            if strike > test_price:
                total_pain += (strike - test_price) * p_oi

        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = test_price

    return max_pain_strike
