"""v9.0: 신용잔고 + 고객예탁금 수집 (네이버 금융).

신용잔고 급증 → 개인 레버리지 과열 → 반대매매 리스크.
고객예탁금 감소 → 시장 자금 이탈 → 하락 압력.

소스: finance.naver.com/sise/sise_deposit.naver
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

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
class CreditBalanceData:
    """일별 신용잔고/예탁금 데이터 (단위: 억원)."""

    date: str  # YYYY-MM-DD
    deposit: float  # 고객예탁금
    deposit_change: float  # 고객예탁금 증감
    credit: float  # 신용잔고
    credit_change: float  # 신용잔고 증감


def fetch_credit_balance(pages: int = 1) -> list[CreditBalanceData]:
    """네이버 금융에서 신용잔고/고객예탁금 조회.

    Args:
        pages: 조회할 페이지 수 (1페이지 = 20일).

    Returns:
        CreditBalanceData 리스트 (최신 → 과거 순).
    """
    results: list[CreditBalanceData] = []

    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/sise/sise_deposit.naver?&page={page}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            rows = _parse_deposit_table(resp.text)
            results.extend(rows)
        except Exception as e:
            logger.warning("Credit balance fetch page %d failed: %s", page, e)
            break

    return results


def _parse_deposit_table(html: str) -> list[CreditBalanceData]:
    """네이버 증시자금동향 테이블 파싱."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[CreditBalanceData] = []

    table = soup.find("table", attrs={"summary": lambda s: s and "증시자금" in s})
    if not table:
        # 폴백: class로 찾기
        table = soup.find("table", class_="type_1")
    if not table:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        date_text = tds[0].get_text(strip=True)
        if not date_text or len(date_text) < 8:
            continue

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

        rows.append(CreditBalanceData(
            date=date_str,
            deposit=_parse_num(tds[1]),
            deposit_change=_parse_num(tds[2]),
            credit=_parse_num(tds[3]),
            credit_change=_parse_num(tds[4]),
        ))

    return rows


def analyze_credit_balance(data: list[CreditBalanceData]) -> dict:
    """신용잔고 분석.

    Returns:
        dict with keys: signal, credit_trillion, credit_change,
        deposit_trillion, weekly_change_pct, warning, summary.
    """
    if not data:
        return {"signal": "데이터 없음", "summary": "신용잔고 데이터 없음"}

    latest = data[0]
    credit_tril = latest.credit / 10000  # 억 → 조
    deposit_tril = latest.deposit / 10000

    # 주간 변화율 (5일)
    weekly_change_pct = 0.0
    if len(data) >= 5:
        prev_credit = data[4].credit
        if prev_credit > 0:
            weekly_change_pct = (latest.credit - prev_credit) / prev_credit * 100

    # 신호 판단
    signal = "안전"
    warning = ""

    if credit_tril >= 20 or weekly_change_pct >= 5:
        signal = "극위험"
        warning = "신용잔고 과열! 반대매매 경고. 레버리지 종목 매수 회피"
    elif credit_tril >= 18 or weekly_change_pct >= 2:
        signal = "위험"
        warning = "신용잔고 급증 주의. 포지션 사이징 -20%"
    elif latest.credit_change > 2000:
        signal = "주의"
        warning = "신용잔고 일일 2,000억+ 증가. 개인 과열 징후"
    elif latest.credit_change < -3000:
        signal = "반대매매 가능"
        warning = "신용잔고 급감 → 반대매매 발생 가능. 저가 매수 기회 모니터링"

    summary_parts = [
        f"신용잔고: {credit_tril:.1f}조 ({latest.credit_change:+,.0f}억)",
        f"고객예탁금: {deposit_tril:.1f}조 ({latest.deposit_change:+,.0f}억)",
        f"주간변화: {weekly_change_pct:+.1f}%",
    ]
    if warning:
        summary_parts.append(f"⚠️ {warning}")

    return {
        "signal": signal,
        "credit_trillion": round(credit_tril, 1),
        "credit_change": latest.credit_change,
        "deposit_trillion": round(deposit_tril, 1),
        "weekly_change_pct": round(weekly_change_pct, 1),
        "warning": warning,
        "summary": "\n".join(summary_parts),
        "date": latest.date,
    }


def format_credit_balance(analysis: dict) -> str:
    """텔레그램 표시용 포맷."""
    signal = analysis.get("signal", "데이터 없음")
    signal_emoji = {
        "안전": "🟢", "주의": "🟡",
        "위험": "🟠", "극위험": "🔴",
        "반대매매 가능": "⚠️",
    }.get(signal, "⚪")

    credit_t = analysis.get("credit_trillion", 0)
    credit_chg = analysis.get("credit_change", 0)
    deposit_t = analysis.get("deposit_trillion", 0)
    weekly = analysis.get("weekly_change_pct", 0)
    warning = analysis.get("warning", "")

    lines = [
        f"{signal_emoji} 신용잔고: {credit_t:.1f}조 ({credit_chg:+,.0f}억) [{signal}]",
        f"  예탁금: {deposit_t:.1f}조 | 주간 {weekly:+.1f}%",
    ]
    if warning:
        lines.append(f"  ⚠️ {warning}")

    return "\n".join(lines)
