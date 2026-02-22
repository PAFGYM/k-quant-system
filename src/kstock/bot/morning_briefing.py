"""Morning briefing generator for K-Quant v3.5.

Generates the daily morning briefing message with global market data,
today's events, pre-market holdings data, new broker reports, and
a strategy note. Also provides a weekly macro events preview.

Section 65 of K-Quant system architecture.

Rules:
- No ** bold in any output
- Korean text throughout
- "주호님" personalized greeting
- Commas in numbers (58,000)
- Direct action instructions (not vague)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"


def generate_morning_briefing(
    market_data: dict | None = None,
    events: list[dict] | None = None,
    holdings_premarket: list[dict] | None = None,
    recent_reports: list[dict] | None = None,
    strategy_note: str = "",
) -> str:
    """Generate the daily morning briefing message.

    Assembles a comprehensive morning briefing for Telegram delivery,
    including global market performance, today's economic events,
    pre-market data for held stocks, new broker reports, and an
    optional strategy note.

    Args:
        market_data: Dict with global market data. Expected keys:
            sp500_pct (float): S&P 500 daily change percent.
            nasdaq_pct (float): NASDAQ daily change percent.
            shanghai_pct (float): Shanghai Composite change percent.
            nikkei_pct (float): Nikkei 225 change percent.
            usdkrw (float): USD/KRW exchange rate.
            usdkrw_change (float): USD/KRW change in won.
        events: List of today's event dicts with keys:
            name (str): Event name.
            description (str): Brief description.
            importance (str): "높음", "보통", or "낮음".
        holdings_premarket: List of holdings with pre-market data.
            Each dict has keys: name, premarket_pct, premarket_price.
        recent_reports: List of new broker report dicts with keys:
            broker (str): Broker name.
            title (str): Report title.
            ticker (str): Related stock ticker (optional).
        strategy_note: Free text strategy note for today. Can be empty.

    Returns:
        Formatted morning briefing string. No ** bold. Ready for
        Telegram delivery.
    """
    now = datetime.now(KST)
    date_str = now.strftime("%Y.%m.%d")
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]

    lines: list[str] = [
        f"{USER_NAME}, 좋은 아침입니다!",
        f"K-Quant 모닝 브리핑 ({date_str} {weekday_kr}요일)",
        "",
    ]

    # --- Global markets section ---
    lines.append("[글로벌 시장]")
    if market_data:
        if "sp500_pct" in market_data:
            sp = market_data["sp500_pct"]
            nq = market_data.get("nasdaq_pct", 0)
            lines.append(f"미국: S&P500 {sp:+.1f}%, 나스닥 {nq:+.1f}%")
        if "shanghai_pct" in market_data:
            lines.append(f"중국: 상해 {market_data['shanghai_pct']:+.1f}%")
        if "nikkei_pct" in market_data:
            lines.append(f"일본: 니케이 {market_data['nikkei_pct']:+.1f}%")
        if "usdkrw" in market_data:
            chg = market_data.get("usdkrw_change", 0)
            lines.append(f"원/달러: {market_data['usdkrw']:,.0f}원 ({chg:+.0f}원)")
    else:
        lines.append("데이터 수집 중...")
    lines.append("")

    # --- Today's events section ---
    if events:
        lines.append("[오늘의 이벤트]")
        for ev in events[:3]:
            imp = ev.get("importance", "")
            prefix = "!" if imp == "높음" else ""
            lines.append(f"{prefix}{ev.get('name', '')}")
            desc = ev.get("description", "")
            if desc:
                lines.append(f"  -> {desc[:60]}")
        lines.append("")

    # --- Holdings pre-market section ---
    if holdings_premarket:
        lines.append("[보유 종목 프리마켓]")
        for h in holdings_premarket[:5]:
            pct = h.get("premarket_pct", 0)
            price = h.get("premarket_price", 0)
            sign = "+" if pct >= 0 else ""
            lines.append(
                f"{h.get('name', '')}: 시간외 {sign}{pct:.1f}% ({price:,.0f}원)"
            )
        lines.append("")

    # --- New reports section ---
    if recent_reports:
        lines.append("[신규 리포트]")
        for r in recent_reports[:3]:
            lines.append(f"{r.get('broker', '')} \"{r.get('title', '')}\"")
        lines.append("")

    # --- Strategy note section ---
    if strategy_note:
        lines.append("[오늘의 전략]")
        lines.append(strategy_note)
        lines.append("")

    lines.append("오늘도 좋은 하루 되세요!")

    return "\n".join(lines)


def format_weekly_macro_preview(
    events: list[dict],
    week_range: str = "",
) -> str:
    """Format weekly macro events preview.

    Generates a week-at-a-glance view of upcoming economic events,
    grouped by day, with high-importance events highlighted.

    Args:
        events: List of event dicts with keys:
            day (str): Day of week in Korean (월, 화, 수, 목, 금).
            name (str): Event name.
            importance (str): "높음", "보통", or "낮음".
        week_range: Optional date range string (e.g., "02/17~02/21").
                   Auto-generated from current week if empty.

    Returns:
        Formatted weekly preview string. No ** bold.
    """
    if not week_range:
        now = datetime.now(KST)
        monday = now - timedelta(days=now.weekday())
        friday = monday + timedelta(days=4)
        week_range = f"{monday.strftime('%m/%d')}~{friday.strftime('%m/%d')}"

    lines: list[str] = [
        f"[주간 매크로 캘린더] {week_range}",
        "",
    ]

    if not events:
        lines.append("이번 주 주요 경제 이벤트 없음")
    else:
        for ev in events:
            day = ev.get("day", "")
            name = ev.get("name", "")
            imp = ev.get("importance", "보통")
            suffix = " (가장 중요!)" if imp == "높음" else ""
            lines.append(f"{day}: {name}{suffix}")

    lines.append("")

    # Highlight the most important event of the week
    high_imp = [e for e in (events or []) if e.get("importance") == "높음"]
    if high_imp:
        focus = high_imp[0].get("name", "")
        lines.append(f"{USER_NAME}, 이번 주 핵심은 {focus}입니다.")

    return "\n".join(lines)


def format_market_summary_line(market_data: dict | None = None) -> str:
    """Format a single-line market summary for compact display.

    Useful for inline market context in other messages.

    Args:
        market_data: Dict with sp500_pct, nasdaq_pct, usdkrw keys.

    Returns:
        Single-line market summary string, or empty string if no data.
    """
    if not market_data:
        return ""
    parts: list[str] = []
    if "sp500_pct" in market_data:
        parts.append(f"S&P {market_data['sp500_pct']:+.1f}%")
    if "nasdaq_pct" in market_data:
        parts.append(f"NQ {market_data['nasdaq_pct']:+.1f}%")
    if "usdkrw" in market_data:
        parts.append(f"환율 {market_data['usdkrw']:,.0f}원")
    return " | ".join(parts)
