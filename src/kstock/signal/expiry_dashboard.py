"""v12.2: 선물 만기일 대시보드.

KOSPI200 선물·옵션 만기일 전후 4대 지표를 통합 조회:
1. 베이시스 방향 (콘탱고/백워데이션)
2. 프로그램 매매 순매수/순매도
3. 외국인 선물 포지션
4. 야간 미국장 + 유가
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime

import requests

from kstock.core.tz import KST
from kstock.ingest.program_trading import (
    ProgramTradingData,
    analyze_program_trading,
    fetch_program_trading,
)

logger = logging.getLogger(__name__)

# ── 만기일 캘린더 ──────────────────────────────────────────

_QUARTERLY_MONTHS = {3, 6, 9, 12}


def get_expiry_date(year: int, month: int) -> date:
    """해당 월 KOSPI200 선물옵션 만기일 (둘째 목요일)."""
    cal = calendar.monthcalendar(year, month)
    # 둘째 목요일 찾기 (목=3)
    thursdays = [week[calendar.THURSDAY] for week in cal if week[calendar.THURSDAY] != 0]
    return date(year, month, thursdays[1])


def is_expiry_day(d: date | None = None) -> bool:
    """오늘이 만기일인지."""
    d = d or datetime.now(KST).date()
    return d == get_expiry_date(d.year, d.month)


def is_quarterly_expiry(d: date | None = None) -> bool:
    """분기 대형 만기(3,6,9,12)인지."""
    d = d or datetime.now(KST).date()
    return d.month in _QUARTERLY_MONTHS and is_expiry_day(d)


def days_until_expiry(d: date | None = None) -> int:
    """다음 만기일까지 남은 일수."""
    d = d or datetime.now(KST).date()
    target = get_expiry_date(d.year, d.month)
    if target < d:
        # 이번 달 만기 지남 → 다음 달
        m = d.month + 1
        y = d.year
        if m > 12:
            m = 1
            y += 1
        target = get_expiry_date(y, m)
    return (target - d).days


# ── 베이시스 (yfinance 현물 + Investing.com 선물) ─────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _parse_num(s: str) -> float:
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0.0


def _fetch_futures_investing() -> float:
    """Investing.com에서 KOSPI200 선물 현재가 스크래핑."""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            "https://kr.investing.com/indices/korea-200-futures",
            headers=_HEADERS, timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.select_one(
            "[data-test=instrument-price-last], .text-5xl, "
            ".instrument-price_last__KQzyA"
        )
        if tag:
            return _parse_num(tag.text.strip())
    except Exception as e:
        logger.warning("Investing.com futures fetch failed: %s", e)
    return 0.0


def _fetch_spot_yfinance() -> float:
    """yfinance에서 KOSPI200 현물 지수 조회."""
    try:
        import yfinance as yf
        tk = yf.Ticker("^KS200")
        hist = tk.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("yfinance KOSPI200 fetch failed: %s", e)
    return 0.0


def _fetch_spot_naver() -> float:
    """네이버 금융에서 KOSPI200 현물 지수 조회 (폴백)."""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            "https://finance.naver.com/sise/sise_index.naver?code=KPI200",
            headers=_HEADERS, timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.select_one("#now_value")
        if tag:
            return _parse_num(tag.text.strip())
    except Exception as e:
        logger.warning("Naver KOSPI200 fetch failed: %s", e)
    return 0.0


def fetch_kospi200_basis() -> dict:
    """KOSPI200 선물 베이시스 조회.

    소스 우선순위:
    - 선물: Investing.com 스크래핑
    - 현물: yfinance ^KS200 → 네이버 금융 폴백

    Returns:
        dict: spot, futures, basis, basis_pct, direction
    """
    # 선물
    futures_price = _fetch_futures_investing()

    # 현물
    spot_price = _fetch_spot_yfinance()
    if spot_price <= 0:
        spot_price = _fetch_spot_naver()

    if futures_price <= 0 or spot_price <= 0:
        return {
            "spot": spot_price,
            "futures": futures_price,
            "basis": 0,
            "basis_pct": 0,
            "direction": "데이터 없음",
        }

    basis = futures_price - spot_price
    basis_pct = (basis / spot_price) * 100 if spot_price else 0

    if basis > 0.3:
        direction = "콘탱고"
    elif basis < -0.3:
        direction = "백워데이션"
    else:
        direction = "보합"

    logger.info(
        "KOSPI200 basis: futures=%.2f spot=%.2f basis=%.2f (%.3f%%) %s",
        futures_price, spot_price, basis, basis_pct, direction,
    )

    return {
        "spot": round(spot_price, 2),
        "futures": round(futures_price, 2),
        "basis": round(basis, 2),
        "basis_pct": round(basis_pct, 3),
        "direction": direction,
    }


# ── 대시보드 조립 ─────────────────────────────────────────

async def build_expiry_dashboard(macro_client, db) -> str:
    """만기일 대시보드 4대 지표 조합 → 텔레그램 메시지."""
    today = datetime.now(KST).date()
    expiry = get_expiry_date(today.year, today.month)
    d_day = (expiry - today).days
    is_quarterly = expiry.month in _QUARTERLY_MONTHS

    # 1) 베이시스
    basis_data = fetch_kospi200_basis()

    # 2) 프로그램 매매
    prog_data = fetch_program_trading(pages=1)
    prog_analysis = analyze_program_trading(prog_data)

    # 3) 외국인 선물 포지션 (매크로 스냅샷 기반)
    macro = await macro_client.get_snapshot()

    # 4) 미국장 + 유가는 macro에서

    return _format_dashboard(
        today=today,
        expiry=expiry,
        d_day=d_day,
        is_quarterly=is_quarterly,
        basis=basis_data,
        prog=prog_analysis,
        macro=macro,
    )


def _signal_emoji(positive: bool | None) -> str:
    if positive is None:
        return "🟡"
    return "🟢" if positive else "🔴"


def _format_dashboard(
    today: date,
    expiry: date,
    d_day: int,
    is_quarterly: bool,
    basis: dict,
    prog: dict,
    macro,
) -> str:
    lines = []

    # 헤더
    if d_day == 0:
        header = "오늘 만기일!"
    elif d_day > 0:
        header = f"만기일까지 D-{d_day}"
    else:
        header = "만기일 경과"

    expiry_type = "분기 동시만기" if is_quarterly else "월물 만기"
    weekday_kr = "월화수목금토일"[expiry.weekday()]

    lines.append("📅 선물 만기일 대시보드")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"{expiry.strftime('%Y.%m.%d')} ({weekday_kr}) {expiry_type}")
    lines.append(f"📌 {header}")
    lines.append("")

    # 1️⃣ 베이시스
    b_dir = basis.get("direction", "데이터 없음")
    b_val = basis.get("basis", 0)
    b_pct = basis.get("basis_pct", 0)
    b_fut = basis.get("futures", 0)
    b_spot = basis.get("spot", 0)

    if b_dir == "데이터 없음":
        b_positive = None
    elif b_dir == "콘탱고":
        b_positive = True
    elif b_dir == "백워데이션":
        b_positive = False
    else:
        b_positive = None

    lines.append(f"1️⃣ 베이시스")
    if b_dir == "데이터 없음":
        lines.append("🟡 데이터 조회 실패")
    else:
        lines.append(f"{_signal_emoji(b_positive)} {b_dir} {b_val:+.2f}pt ({b_pct:+.3f}%)")
        lines.append(f"  선물 {b_fut:,.2f} / 현물 {b_spot:,.2f}")
    lines.append("")

    # 2️⃣ 프로그램 매매
    p_signal = prog.get("signal", "데이터 없음")
    p_total = prog.get("total_net", 0)
    p_arb = prog.get("arb_net", 0)
    p_non_arb = prog.get("non_arb_net", 0)
    p_trend = prog.get("trend", "")

    if p_signal == "데이터 없음":
        p_positive = None
    elif "매수" in p_signal:
        p_positive = True
    elif "매도" in p_signal:
        p_positive = False
    else:
        p_positive = None

    lines.append(f"2️⃣ 프로그램 매매")
    if p_signal == "데이터 없음":
        lines.append("🟡 데이터 조회 실패")
    else:
        p_dir = "매수" if p_total >= 0 else "매도"
        lines.append(f"{_signal_emoji(p_positive)} 순{p_dir} {p_total:+,.0f}억")
        lines.append(f"  차익 {p_arb:+,.0f} / 비차익 {p_non_arb:+,.0f}")
        if p_trend:
            lines.append(f"  {p_trend}")
    lines.append("")

    # 3️⃣ 외국인 선물 포지션
    foreign_fut = getattr(macro, "foreign_total", 0)
    spx_chg = getattr(macro, "spx_change_pct", 0)
    usdkrw = getattr(macro, "usdkrw", 0)
    dxy_chg = getattr(macro, "dxy_change_pct", 0)

    # 외인 선물 방향 간이 판단
    if foreign_fut > 0:
        f_positive = True
        f_label = "순매수"
    elif foreign_fut < 0:
        f_positive = False
        f_label = "순매도"
    else:
        f_positive = None
        f_label = "중립"

    lines.append(f"3️⃣ 외국인 선물 포지션")
    if foreign_fut != 0:
        lines.append(f"{_signal_emoji(f_positive)} {f_label} {foreign_fut:+,.0f}억")
    else:
        lines.append("🟡 데이터 확인 필요")
    if usdkrw > 0:
        lines.append(f"  원/달러 {usdkrw:,.1f}")
    lines.append("")

    # 4️⃣ 야간 미국장 + 유가
    sp_chg = getattr(macro, "spx_change_pct", 0)
    nq_chg = getattr(macro, "nasdaq_change_pct", 0)
    vix = getattr(macro, "vix", 0)
    vix_chg = getattr(macro, "vix_change_pct", 0)
    wti = getattr(macro, "wti_price", 0)
    wti_chg = getattr(macro, "wti_change_pct", 0)
    brent = getattr(macro, "brent_price", 0)

    us_positive = sp_chg > 0 if sp_chg != 0 else None

    lines.append(f"4️⃣ 야간 미국장 + 유가")
    lines.append(f"{_signal_emoji(us_positive)} S&P {sp_chg:+.2f}% / 나스닥 {nq_chg:+.2f}%")
    if wti > 0:
        lines.append(f"  WTI ${wti:.1f} ({wti_chg:+.1f}%)")
    if brent > 0:
        lines.append(f"  Brent ${brent:.1f}")
    if vix > 0:
        lines.append(f"  VIX {vix:.1f} ({vix_chg:+.1f}%)")
    lines.append("")

    # 종합 판단
    signals = [b_positive, p_positive, f_positive, us_positive]
    green = sum(1 for s in signals if s is True)
    red = sum(1 for s in signals if s is False)

    if green >= 3:
        overall = "매수 우위"
        overall_emoji = "🟢"
    elif red >= 3:
        overall = "매도 우위"
        overall_emoji = "🔴"
    else:
        overall = "혼조세"
        overall_emoji = "🟡"

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"🎯 종합: {overall_emoji} {overall} ({green}/4 긍정)")

    # 핵심 요약 한 줄
    notes = []
    if b_dir == "콘탱고":
        notes.append("베이시스 정상")
    elif b_dir == "백워데이션":
        notes.append("베이시스 역전 주의")
    if p_total < -1000:
        notes.append("프로그램 매도 경계")
    elif p_total > 1000:
        notes.append("프로그램 매수 유입")
    if notes:
        lines.append(f"  {' + '.join(notes)}")

    return "\n".join(lines)
