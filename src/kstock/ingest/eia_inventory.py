"""v10.5: EIA 원유재고 + SPR(전략비축유) 추적.

EIA Open Data API (무료):
- WCRSTUS1: 미국 상업 원유 재고 (주간, 천 배럴)
- WCSSTUS1: 전략비축유(SPR) 재고 (주간, 천 배럴)
- 5년 평균 대비 비교
- G7/SPR 방출 감지 시그널

소스: https://api.eia.gov/v2/
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "KQuant/1.0",
}

# EIA API v2 base URL
_EIA_BASE = "https://api.eia.gov/v2"

# 시리즈 ID
_SERIES = {
    "crude_inventory": "WCRSTUS1",   # 상업 원유 재고 (주간)
    "spr_inventory": "WCSSTUS1",     # SPR 전략비축유 (주간)
}


def fetch_eia_inventory(api_key: str = "", weeks: int = 10) -> dict:
    """EIA API에서 원유재고 + SPR 데이터 수집.

    Args:
        api_key: EIA API 키 (없으면 무료 제한 사용)
        weeks: 수집할 주 수

    Returns:
        dict with crude_inventory, spr_inventory, history, signal, etc.
    """
    import os
    if not api_key:
        api_key = os.getenv("EIA_API_KEY", "")

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "report_date": "",
        "crude_inventory": 0,
        "crude_change": 0,
        "spr_inventory": 0,
        "spr_change": 0,
        "five_year_avg": 0,
        "deviation_pct": 0,
        "signal": "",
        "crude_history": [],
        "spr_history": [],
    }

    # 1. 상업 원유 재고
    crude_data = _fetch_series("WCRSTUS1", api_key, weeks)
    if crude_data:
        result["crude_history"] = crude_data
        latest = crude_data[0]
        result["crude_inventory"] = latest["value"]
        result["report_date"] = latest["period"]
        if len(crude_data) >= 2:
            result["crude_change"] = latest["value"] - crude_data[1]["value"]

        # 5년 평균 근사 (최근 데이터에서 추정)
        if len(crude_data) >= 5:
            avg_5 = sum(d["value"] for d in crude_data[:5]) / 5
            result["five_year_avg"] = round(avg_5, 1)
            if avg_5 > 0:
                result["deviation_pct"] = round(
                    (latest["value"] - avg_5) / avg_5 * 100, 1
                )

    # 2. SPR 전략비축유
    spr_data = _fetch_series("WCSSTUS1", api_key, weeks)
    if spr_data:
        result["spr_history"] = spr_data
        latest_spr = spr_data[0]
        result["spr_inventory"] = latest_spr["value"]
        if len(spr_data) >= 2:
            result["spr_change"] = latest_spr["value"] - spr_data[1]["value"]

    # 3. 시그널 판정
    result["signal"] = _determine_signal(result)

    # data_json
    result["data_json"] = json.dumps({
        "crude_latest": result["crude_inventory"],
        "spr_latest": result["spr_inventory"],
        "crude_change_kb": result["crude_change"],
        "spr_change_kb": result["spr_change"],
        "deviation_pct": result["deviation_pct"],
        "weeks_fetched": weeks,
    }, ensure_ascii=False)

    logger.info(
        "EIA inventory: crude=%.1fM bbl (%.1f change), SPR=%.1fM bbl (%.1f change), signal=%s",
        result["crude_inventory"] / 1000,
        result["crude_change"] / 1000,
        result["spr_inventory"] / 1000,
        result["spr_change"] / 1000,
        result["signal"],
    )

    return result


def _fetch_series(series_id: str, api_key: str, weeks: int) -> list[dict]:
    """EIA API v2에서 시계열 데이터 수집."""
    # EIA v2 petroleum weekly endpoint
    url = f"{_EIA_BASE}/petroleum/sum/sndw/data/"
    params = {
        "frequency": "weekly",
        "data[0]": "value",
        "facets[series][]": series_id,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": str(weeks),
    }
    if api_key:
        params["api_key"] = api_key

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        if resp.status_code == 403 or resp.status_code == 401:
            # API 키 없으면 제한적 접근 — 대체 URL 시도
            logger.info("EIA API key issue, trying alternative endpoint")
            return _fetch_series_alt(series_id, api_key, weeks)

        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("EIA fetch failed for %s: %s", series_id, e)
        return _fetch_series_alt(series_id, api_key, weeks)

    response_data = data.get("response", {}).get("data", [])
    results = []
    for item in response_data:
        try:
            value = float(item.get("value", 0))
            period = item.get("period", "")
            results.append({"period": period, "value": value})
        except (ValueError, TypeError):
            continue

    return results


def _fetch_series_alt(series_id: str, api_key: str, weeks: int) -> list[dict]:
    """대체 EIA API 엔드포인트 (v2 steo 또는 direct series)."""
    # Try direct series endpoint
    url = f"{_EIA_BASE}/seriesid/{series_id}"
    params = {"length": str(weeks)}
    if api_key:
        params["api_key"] = api_key

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        response_data = data.get("response", {}).get("data", [])
        results = []
        for item in response_data:
            try:
                value = float(item.get("value", 0))
                period = item.get("period", "")
                results.append({"period": period, "value": value})
            except (ValueError, TypeError):
                continue
        return results
    except Exception as e:
        logger.warning("EIA alt fetch failed for %s: %s", series_id, e)
        return []


def _determine_signal(data: dict) -> str:
    """EIA 재고 데이터에서 시그널 판정."""
    signals = []

    crude_change = data.get("crude_change", 0)
    spr_change = data.get("spr_change", 0)
    deviation = data.get("deviation_pct", 0)

    # 상업 재고 시그널
    if crude_change > 5000:  # +500만 배럴 이상
        signals.append("재고 대폭 증가 → 수요 약화/공급 과잉")
    elif crude_change > 2000:
        signals.append("재고 증가 → 약세 압력")
    elif crude_change < -5000:
        signals.append("재고 대폭 감소 → 수요 강세/공급 부족")
    elif crude_change < -2000:
        signals.append("재고 감소 → 강세 압력")

    # SPR 방출 감지
    if spr_change < -3000:  # SPR 300만 배럴 이상 감소
        signals.append("⚠️ SPR 대규모 방출! G7 비축유 조정 가능성")
    elif spr_change < -1000:
        signals.append("SPR 방출 → 정부 유가 안정 의지")
    elif spr_change > 1000:
        signals.append("SPR 재충전 → 유가 추가 상승 압력")

    # 5년 평균 대비
    if deviation > 10:
        signals.append(f"5년 평균 대비 +{deviation:.1f}% → 공급 과잉")
    elif deviation < -10:
        signals.append(f"5년 평균 대비 {deviation:.1f}% → 공급 부족")

    return " | ".join(signals) if signals else "중립"


def analyze_eia_inventory(current: dict, previous: list[dict] | None = None) -> dict:
    """EIA 재고 데이터 종합 분석."""
    if not current:
        return {"signal": "데이터 없음", "summary": "EIA 재고 데이터 없음"}

    crude_mb = current.get("crude_inventory", 0) / 1000  # 천 배럴 → 백만 배럴
    crude_chg = current.get("crude_change", 0) / 1000
    spr_mb = current.get("spr_inventory", 0) / 1000
    spr_chg = current.get("spr_change", 0) / 1000
    deviation = current.get("deviation_pct", 0)
    signal = current.get("signal", "중립")

    summary_parts = [
        f"원유재고: {crude_mb:.1f}M bbl ({crude_chg:+.1f}M)",
        f"SPR 비축유: {spr_mb:.1f}M bbl ({spr_chg:+.1f}M)",
    ]
    if deviation != 0:
        summary_parts.append(f"5년 평균 대비: {deviation:+.1f}%")
    if signal and signal != "중립":
        summary_parts.append(f"⚠️ {signal}")

    return {
        "signal": signal,
        "summary": "\n".join(summary_parts),
        "crude_inventory_mb": round(crude_mb, 1),
        "crude_change_mb": round(crude_chg, 1),
        "spr_inventory_mb": round(spr_mb, 1),
        "spr_change_mb": round(spr_chg, 1),
        "deviation_pct": deviation,
    }


def format_eia_inventory(analysis: dict) -> str:
    """텔레그램 표시용 EIA 재고 포맷."""
    signal = analysis.get("signal", "데이터 없음")
    crude = analysis.get("crude_inventory_mb", 0)
    crude_chg = analysis.get("crude_change_mb", 0)
    spr = analysis.get("spr_inventory_mb", 0)
    spr_chg = analysis.get("spr_change_mb", 0)
    deviation = analysis.get("deviation_pct", 0)

    emoji = "⛽"
    if "SPR 대규모 방출" in signal:
        emoji = "🚨"
    elif "대폭 감소" in signal or "공급 부족" in signal:
        emoji = "📈"
    elif "대폭 증가" in signal or "공급 과잉" in signal:
        emoji = "📉"

    lines = [
        f"{emoji} EIA 원유 재고",
        f"  상업재고: {crude:.1f}M bbl ({crude_chg:+.1f}M)",
        f"  SPR 비축유: {spr:.1f}M bbl ({spr_chg:+.1f}M)",
    ]
    if deviation != 0:
        lines.append(f"  5년 평균 대비: {deviation:+.1f}%")
    if signal and signal != "중립":
        lines.append(f"  {signal}")

    return "\n".join(lines)
