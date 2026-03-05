"""v9.0: 프로그램 매매 데이터 수집 (네이버 금융).

차익거래/비차익거래 순매수 데이터를 일별로 수집하여
기관 포트폴리오 리밸런싱, 만기일 프로그램 매매 급변 등 감지.

소스: finance.naver.com/sise/programDealTrendDay.naver
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


@dataclass
class ProgramTradingData:
    """일별 프로그램 매매 데이터 (단위: 억원)."""

    date: str  # YYYY-MM-DD
    market: str  # "KOSPI" or "KOSDAQ"
    arb_buy: float  # 차익거래 매수
    arb_sell: float  # 차익거래 매도
    arb_net: float  # 차익거래 순매수
    non_arb_buy: float  # 비차익거래 매수
    non_arb_sell: float  # 비차익거래 매도
    non_arb_net: float  # 비차익거래 순매수
    total_buy: float  # 전체 매수
    total_sell: float  # 전체 매도
    total_net: float  # 전체 순매수


def fetch_program_trading(
    date: str | None = None,
    market: str = "KOSPI",
    pages: int = 1,
) -> list[ProgramTradingData]:
    """네이버 금융에서 프로그램 매매 데이터 조회.

    Args:
        date: 조회 날짜 (YYYYMMDD 형식). None이면 오늘.
        market: "KOSPI" 또는 "KOSDAQ".
        pages: 조회할 페이지 수 (1페이지 = 10일).

    Returns:
        ProgramTradingData 리스트 (최신 → 과거 순).
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    sosok = "" if market.upper() == "KOSPI" else "02"
    results: list[ProgramTradingData] = []

    for page in range(1, pages + 1):
        url = (
            f"https://finance.naver.com/sise/programDealTrendDay.naver"
            f"?bizdate={date}&sosok={sosok}&page={page}"
        )
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            rows = _parse_program_table(resp.text, market.upper())
            results.extend(rows)
        except Exception as e:
            logger.warning("Program trading fetch page %d failed: %s", page, e)
            break

    return results


def _parse_program_table(html: str, market: str) -> list[ProgramTradingData]:
    """네이버 프로그램 매매 테이블 파싱."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[ProgramTradingData] = []

    table = soup.find("table", class_="type_1")
    if not table:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue

        date_text = tds[0].get_text(strip=True)
        if not date_text or len(date_text) < 8:
            continue

        # "26.03.05" → "2026-03-05"
        try:
            dt = datetime.strptime(date_text, "%y.%m.%d")
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

        def _parse_num(td) -> float:
            text = td.get_text(strip=True).replace(",", "")
            try:
                return float(text)
            except ValueError:
                return 0.0

        rows.append(ProgramTradingData(
            date=date_str,
            market=market,
            arb_buy=_parse_num(tds[1]),
            arb_sell=_parse_num(tds[2]),
            arb_net=_parse_num(tds[3]),
            non_arb_buy=_parse_num(tds[4]),
            non_arb_sell=_parse_num(tds[5]),
            non_arb_net=_parse_num(tds[6]),
            total_buy=_parse_num(tds[7]),
            total_sell=_parse_num(tds[8]),
            total_net=_parse_num(tds[9]),
        ))

    return rows


def analyze_program_trading(
    data: list[ProgramTradingData],
) -> dict:
    """프로그램 매매 데이터 분석.

    Returns:
        dict with keys: signal, summary, arb_net, non_arb_net, total_net,
        trend (3일 추세), warning.
    """
    if not data:
        return {"signal": "데이터 없음", "summary": "프로그램 매매 데이터 없음"}

    latest = data[0]
    arb_net = latest.arb_net
    non_arb_net = latest.non_arb_net
    total_net = latest.total_net

    # 3일 추세 분석
    trend = "N/A"
    warning = ""
    if len(data) >= 3:
        recent_3 = [d.total_net for d in data[:3]]
        if all(n < 0 for n in recent_3):
            trend = "3일 연속 순매도"
            warning = "프로그램 매도 지속 → 지수 하방 압력"
        elif all(n > 0 for n in recent_3):
            trend = "3일 연속 순매수"
        else:
            avg = sum(recent_3) / 3
            trend = f"3일 평균 {avg:+,.0f}억"

    # 차익거래 신호
    arb_signal = ""
    if abs(arb_net) > 3000:
        direction = "매수" if arb_net > 0 else "매도"
        arb_signal = f"차익 대량 {direction} {abs(arb_net):,.0f}억 → 베이시스 변동 주의"

    # 비차익거래 신호 (기관 리밸런싱)
    non_arb_signal = ""
    if non_arb_net > 5000:
        non_arb_signal = "비차익 대량 순매수 → 기관 편입 신호"
    elif non_arb_net < -5000:
        non_arb_signal = "비차익 대량 순매도 → 기관 이탈 경고"

    # 종합 신호
    if total_net > 3000:
        signal = "강한 매수"
    elif total_net > 1000:
        signal = "매수"
    elif total_net < -3000:
        signal = "강한 매도"
    elif total_net < -1000:
        signal = "매도"
    else:
        signal = "중립"

    summary_parts = [
        f"전체: {total_net:+,.0f}억 ({signal})",
        f"차익: {arb_net:+,.0f}억 / 비차익: {non_arb_net:+,.0f}억",
        f"추세: {trend}",
    ]
    if arb_signal:
        summary_parts.append(arb_signal)
    if non_arb_signal:
        summary_parts.append(non_arb_signal)
    if warning:
        summary_parts.append(f"⚠️ {warning}")

    return {
        "signal": signal,
        "summary": "\n".join(summary_parts),
        "arb_net": arb_net,
        "non_arb_net": non_arb_net,
        "total_net": total_net,
        "trend": trend,
        "warning": warning,
        "date": latest.date,
    }


def format_program_trading(analysis: dict) -> str:
    """텔레그램 표시용 프로그램 매매 포맷."""
    signal = analysis.get("signal", "데이터 없음")
    signal_emoji = {
        "강한 매수": "🟢🟢", "매수": "🟢",
        "강한 매도": "🔴🔴", "매도": "🔴",
        "중립": "⚪",
    }.get(signal, "⚪")

    total = analysis.get("total_net", 0)
    arb = analysis.get("arb_net", 0)
    non_arb = analysis.get("non_arb_net", 0)
    trend = analysis.get("trend", "")
    warning = analysis.get("warning", "")

    lines = [
        f"{signal_emoji} 프로그램 매매: {signal}",
        f"  전체: {total:+,.0f}억 (차익 {arb:+,.0f} / 비차익 {non_arb:+,.0f})",
        f"  {trend}",
    ]
    if warning:
        lines.append(f"  ⚠️ {warning}")

    return "\n".join(lines)
