"""v9.0: ETF 설정/해지 + 레버리지/인버스 추적.

레버리지 ETF 순자산 급증 → 개인 과열 경고
인버스 ETF 순자산 급증 → 극공포 → 역발상 매수 기회
시가총액 변화 = 설정/해지 흐름의 프록시

소스: Naver Finance ETF JSON API
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 추적 대상 ETF
_TRACKED_ETFS = {
    # 레버리지
    "122630": {"name": "KODEX 레버리지", "type": "leverage"},
    "123320": {"name": "TIGER 레버리지", "type": "leverage"},
    # 인버스 (2X)
    "252670": {"name": "KODEX 200선물인버스2X", "type": "inverse"},
    "252710": {"name": "TIGER 200선물인버스2X", "type": "inverse"},
    # 인버스 (1X)
    "114800": {"name": "KODEX 인버스", "type": "inverse"},
    # 대표 ETF
    "069500": {"name": "KODEX 200", "type": "index"},
    # --- KOSDAQ ---
    # KOSDAQ 대표
    "229200": {"name": "KODEX KOSDAQ150", "type": "kosdaq_index"},
    # KOSDAQ 레버리지
    "233740": {"name": "KODEX KOSDAQ150레버리지", "type": "kosdaq_leverage"},
    # KOSDAQ 인버스
    "251340": {"name": "KODEX KOSDAQ150인버스", "type": "kosdaq_inverse"},
    "278530": {"name": "KODEX KOSDAQ150선물인버스2X", "type": "kosdaq_inverse"},
}


@dataclass
class ETFFlowData:
    """ETF 흐름 데이터."""

    code: str
    name: str
    etf_type: str  # leverage / inverse / index
    price: float
    change_pct: float
    nav: float
    market_cap: float  # 시총 (억원)
    volume: int


def fetch_etf_flow() -> list[ETFFlowData]:
    """네이버 금융 ETF API에서 추적 대상 ETF 데이터 조회.

    Returns:
        ETFFlowData 리스트.
    """
    url = (
        "https://finance.naver.com/api/sise/etfItemList.nhn"
        "?etfType=0&targetColumn=market_sum&sortOrder=desc"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("ETF flow fetch failed: %s", e)
        return []

    items = data.get("result", {}).get("etfItemList", [])
    results: list[ETFFlowData] = []

    for item in items:
        code = item.get("itemcode", "")
        if code not in _TRACKED_ETFS:
            continue

        info = _TRACKED_ETFS[code]
        market_cap_raw = item.get("marketSum", 0)
        # marketSum is in 억원
        try:
            market_cap = float(market_cap_raw)
        except (ValueError, TypeError):
            market_cap = 0.0

        try:
            change_pct = float(item.get("changeRate", 0))
        except (ValueError, TypeError):
            change_pct = 0.0

        try:
            nav = float(item.get("nav", 0))
        except (ValueError, TypeError):
            nav = 0.0

        try:
            price = float(item.get("nowVal", 0))
        except (ValueError, TypeError):
            price = 0.0

        try:
            volume = int(item.get("quant", 0))
        except (ValueError, TypeError):
            volume = 0

        results.append(ETFFlowData(
            code=code,
            name=info["name"],
            etf_type=info["type"],
            price=price,
            change_pct=change_pct,
            nav=nav,
            market_cap=market_cap,
            volume=volume,
        ))

    return results


def analyze_etf_flow(
    current: list[ETFFlowData],
    previous: list[dict] | None = None,
) -> dict:
    """ETF 흐름 분석.

    Args:
        current: 현재 ETF 데이터.
        previous: 이전 저장 데이터 (DB에서 조회). 없으면 변화율 생략.

    Returns:
        dict with keys: signal, summary, leverage_total, inverse_total,
        leverage_change_pct, inverse_change_pct, warning, details.
    """
    if not current:
        return {"signal": "데이터 없음", "summary": "ETF 데이터 없음"}

    # 유형별 시총 합산 (KOSPI)
    leverage_total = sum(e.market_cap for e in current if e.etf_type == "leverage")
    inverse_total = sum(e.market_cap for e in current if e.etf_type == "inverse")

    # KOSDAQ 유형별 시총 합산
    kq_lev_total = sum(e.market_cap for e in current if e.etf_type == "kosdaq_leverage")
    kq_inv_total = sum(e.market_cap for e in current if e.etf_type == "kosdaq_inverse")

    # 이전 데이터와 비교 (시총 변화 = 설정/해지 프록시)
    lev_change_pct = 0.0
    inv_change_pct = 0.0

    kq_lev_change_pct = 0.0
    kq_inv_change_pct = 0.0

    if previous:
        prev_map = {d["code"]: d for d in previous}
        prev_lev = sum(
            d.get("market_cap", 0) for d in previous if d.get("etf_type") == "leverage"
        )
        prev_inv = sum(
            d.get("market_cap", 0) for d in previous if d.get("etf_type") == "inverse"
        )
        prev_kq_lev = sum(
            d.get("market_cap", 0) for d in previous if d.get("etf_type") == "kosdaq_leverage"
        )
        prev_kq_inv = sum(
            d.get("market_cap", 0) for d in previous if d.get("etf_type") == "kosdaq_inverse"
        )
        if prev_lev > 0:
            lev_change_pct = (leverage_total - prev_lev) / prev_lev * 100
        if prev_inv > 0:
            inv_change_pct = (inverse_total - prev_inv) / prev_inv * 100
        if prev_kq_lev > 0:
            kq_lev_change_pct = (kq_lev_total - prev_kq_lev) / prev_kq_lev * 100
        if prev_kq_inv > 0:
            kq_inv_change_pct = (kq_inv_total - prev_kq_inv) / prev_kq_inv * 100

    # 신호 판단
    signal = "중립"
    warning = ""

    # 레버리지 ETF 과열 (개인 투기)
    if leverage_total >= 50000 and lev_change_pct >= 10:
        signal = "레버리지 과열"
        warning = "레버리지 ETF 급증! 개인 과열 극심. 신규 매수 주의"
    elif lev_change_pct >= 5:
        signal = "레버리지 주의"
        warning = "레버리지 ETF 시총 5%+ 증가. 개인 투기 과열 징후"

    # 인버스 ETF 급증 (공포)
    if inv_change_pct >= 15:
        if signal == "중립":
            signal = "극공포 (역발상 기회)"
        warning += (" | " if warning else "") + "인버스 ETF 급증 → 극공포 → 역발상 매수 기회 모니터링"
    elif inv_change_pct >= 8:
        if signal == "중립":
            signal = "공포 증가"
        warning += (" | " if warning else "") + "인버스 ETF 증가 → 시장 불안 확대"

    # KOSDAQ 시그널
    kq_signal = "중립"
    if kq_lev_change_pct >= 10:
        kq_signal = "KOSDAQ 레버리지 과열"
        warning += (" | " if warning else "") + "KOSDAQ 레버리지 ETF 급증! 개인 투기 과열"
    elif kq_lev_change_pct >= 5:
        kq_signal = "KOSDAQ 레버리지 주의"
        warning += (" | " if warning else "") + "KOSDAQ 레버리지 ETF 증가 → 투기 징후"

    if kq_inv_change_pct >= 15:
        if kq_signal == "중립":
            kq_signal = "KOSDAQ 극공포"
        warning += (" | " if warning else "") + "KOSDAQ 인버스 급증 → 극공포 → 역발상 기회"
    elif kq_inv_change_pct >= 8:
        if kq_signal == "중립":
            kq_signal = "KOSDAQ 공포 증가"
        warning += (" | " if warning else "") + "KOSDAQ 인버스 증가 → 불안 확대"

    # 레버리지/인버스 비율 (KOSPI)
    li_ratio = leverage_total / inverse_total if inverse_total > 0 else 0
    # KOSDAQ 레버리지/인버스 비율
    kq_li_ratio = kq_lev_total / kq_inv_total if kq_inv_total > 0 else 0

    details = []
    for e in current:
        details.append({
            "code": e.code,
            "name": e.name,
            "type": e.etf_type,
            "market_cap": e.market_cap,
            "change_pct": e.change_pct,
        })

    lev_tril = leverage_total / 10000  # 억 → 조
    inv_tril = inverse_total / 10000
    kq_lev_tril = kq_lev_total / 10000
    kq_inv_tril = kq_inv_total / 10000

    summary_parts = [
        f"[KOSPI] 레버리지: {lev_tril:.1f}조({lev_change_pct:+.1f}%) | 인버스: {inv_tril:.1f}조({inv_change_pct:+.1f}%) | 비율: {li_ratio:.1f}",
        f"[KOSDAQ] 레버리지: {kq_lev_tril:.2f}조({kq_lev_change_pct:+.1f}%) | 인버스: {kq_inv_tril:.2f}조({kq_inv_change_pct:+.1f}%) | 비율: {kq_li_ratio:.1f}",
    ]
    if warning:
        summary_parts.append(f"⚠️ {warning}")

    return {
        "signal": signal,
        "kosdaq_signal": kq_signal,
        "summary": "\n".join(summary_parts),
        "leverage_total": round(leverage_total, 0),
        "inverse_total": round(inverse_total, 0),
        "leverage_change_pct": round(lev_change_pct, 1),
        "inverse_change_pct": round(inv_change_pct, 1),
        "li_ratio": round(li_ratio, 1),
        "kq_leverage_total": round(kq_lev_total, 0),
        "kq_inverse_total": round(kq_inv_total, 0),
        "kq_leverage_change_pct": round(kq_lev_change_pct, 1),
        "kq_inverse_change_pct": round(kq_inv_change_pct, 1),
        "kq_li_ratio": round(kq_li_ratio, 1),
        "warning": warning,
        "details": details,
    }


def format_etf_flow(analysis: dict) -> str:
    """텔레그램 표시용 ETF 흐름 포맷."""
    signal = analysis.get("signal", "데이터 없음")
    signal_emoji = {
        "중립": "⚪",
        "레버리지 주의": "🟡",
        "레버리지 과열": "🔴",
        "공포 증가": "🟠",
        "극공포 (역발상 기회)": "💀",
    }.get(signal, "⚪")

    kq_signal = analysis.get("kosdaq_signal", "중립")
    kq_emoji = {
        "중립": "⚪",
        "KOSDAQ 레버리지 주의": "🟡",
        "KOSDAQ 레버리지 과열": "🔴",
        "KOSDAQ 공포 증가": "🟠",
        "KOSDAQ 극공포": "💀",
    }.get(kq_signal, "⚪")

    lev = analysis.get("leverage_total", 0) / 10000
    inv = analysis.get("inverse_total", 0) / 10000
    lev_chg = analysis.get("leverage_change_pct", 0)
    inv_chg = analysis.get("inverse_change_pct", 0)
    li_ratio = analysis.get("li_ratio", 0)

    kq_lev = analysis.get("kq_leverage_total", 0) / 10000
    kq_inv = analysis.get("kq_inverse_total", 0) / 10000
    kq_lev_chg = analysis.get("kq_leverage_change_pct", 0)
    kq_inv_chg = analysis.get("kq_inverse_change_pct", 0)
    kq_li_ratio = analysis.get("kq_li_ratio", 0)

    warning = analysis.get("warning", "")

    lines = [
        f"{signal_emoji} KOSPI ETF: {signal}",
        f"  레버리지: {lev:.1f}조({lev_chg:+.1f}%) | 인버스: {inv:.1f}조({inv_chg:+.1f}%) | 비율: {li_ratio:.1f}",
        f"{kq_emoji} KOSDAQ ETF: {kq_signal}",
        f"  레버리지: {kq_lev:.2f}조({kq_lev_chg:+.1f}%) | 인버스: {kq_inv:.2f}조({kq_inv_chg:+.1f}%) | 비율: {kq_li_ratio:.1f}",
    ]
    if warning:
        lines.append(f"  ⚠️ {warning}")

    return "\n".join(lines)
