"""실시간 시장 보고서 - Phase 8.

v3.5-phase8 speed optimization:
- SQLite 캐시에서 매크로 데이터 즉시 읽기 (0ms)
- AI 요약 캐시 (5분 TTL) → 매번 새로 생성 안 함
- asyncio.gather로 모든 데이터 병렬 수집

목표: 버튼 → 1초 이내 응답 (캐시 히트 시)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"

# haiku 사용 여부 (ANTHROPIC_API_KEY 있으면 AI 요약 추가)
_HAS_ANTHROPIC = bool(os.getenv("ANTHROPIC_API_KEY", ""))

try:
    import anthropic
    _HAS_ANTHROPIC = _HAS_ANTHROPIC and True
except ImportError:
    _HAS_ANTHROPIC = False


async def generate_live_report(
    macro_client,
    db=None,
    pulse_engine=None,
    sector_strengths: list | None = None,
) -> str:
    """실시간 시장 보고서 생성 (최적화 버전).

    Args:
        macro_client: MacroClient instance (3-tier cache 사용).
        db: SQLiteStore for holdings data + AI summary cache.
        pulse_engine: MarketPulse instance for recent state history.
        sector_strengths: Pre-computed sector data.

    Returns:
        Formatted report string for Telegram.
    """
    start_ts = time.monotonic()
    now = datetime.now(KST)

    # ── 모든 데이터 병렬 수집 ──
    macro_coro = macro_client.get_snapshot()
    holdings_coro = _get_holdings(db)
    ai_coro = _get_ai_summary_cached(db, macro_client, now)

    macro, holdings, ai_summary = await asyncio.gather(
        macro_coro, holdings_coro, ai_coro,
        return_exceptions=True,
    )

    # 에러 처리
    if isinstance(macro, Exception):
        logger.warning("Macro fetch failed in live report: %s", macro)
        macro = None
    if isinstance(holdings, Exception):
        logger.warning("Holdings fetch failed: %s", holdings)
        holdings = []
    if isinstance(ai_summary, Exception):
        logger.warning("AI summary failed: %s", ai_summary)
        ai_summary = ""

    if macro is None:
        return "시장 데이터를 가져올 수 없습니다. 잠시 후 다시 시도해주세요."

    # 시장 맥박 (in-memory, instant)
    pulse_summary = ""
    if pulse_engine:
        recent = pulse_engine.get_recent_history(minutes=60)
        current_state = pulse_engine.get_current_state()
        if recent:
            pulse_summary = _format_pulse_summary(current_state, recent)

    # 보유종목 요약
    holdings_summary = ""
    if holdings:
        holdings_summary = _format_holdings_brief(holdings)

    # 보고서 조립
    elapsed_ms = (time.monotonic() - start_ts) * 1000
    report = _assemble_report(
        macro=macro,
        now=now,
        pulse_summary=pulse_summary,
        holdings_summary=holdings_summary,
        sector_strengths=sector_strengths,
        ai_summary=ai_summary if isinstance(ai_summary, str) else "",
        elapsed_ms=elapsed_ms,
    )

    return report


async def _get_holdings(db) -> list[dict]:
    """DB에서 보유종목 가져오기 (thread pool)."""
    if not db:
        return []
    return await asyncio.to_thread(db.get_active_holdings)


async def _get_ai_summary_cached(db, macro_client, now: datetime) -> str:
    """AI 요약을 캐시에서 가져오거나 새로 생성.

    5분 이내 캐시가 있으면 즉시 반환.
    없으면 새로 생성하되, 타임아웃 2초.
    """
    # 1. 캐시 확인
    if db:
        try:
            cached = await asyncio.to_thread(
                db.get_ai_summary_cache, 300  # 5분 TTL
            )
            if cached:
                return cached
        except Exception as e:
            logger.debug("_get_ai_summary_cached cache read failed: %s", e)

    # 2. 새로 생성 (타임아웃 적용)
    if not _HAS_ANTHROPIC:
        return ""

    try:
        # 매크로 데이터가 필요하지만 이미 캐시에 있을 것
        macro = await macro_client.get_snapshot()
        summary = await asyncio.wait_for(
            _generate_ai_summary(macro, now),
            timeout=2.0,  # 최대 2초 대기 (v3.5 속도 최적화)
        )
        # 캐시에 저장
        if db and summary:
            try:
                await asyncio.to_thread(db.save_ai_summary_cache, summary)
            except Exception as e:
                logger.debug("_get_ai_summary_cached save failed: %s", e)
        return summary
    except asyncio.TimeoutError:
        logger.warning("AI summary timed out")
        return ""
    except Exception as e:
        logger.warning("AI summary failed: %s", e)
        return ""


def _format_pulse_summary(current_state: str, history: list) -> str:
    """시장 맥박 요약."""
    from kstock.signal.market_pulse import MARKET_STATES

    state_info = MARKET_STATES.get(current_state, {"label": current_state, "emoji": ""})
    lines = [
        f"시장 맥박: {state_info.get('emoji', '')} {state_info['label']}",
    ]

    if len(history) >= 3:
        # 최근 6개 포인트의 미니 트렌드
        recent = history[-6:]
        scores = [r.score for r in recent]
        if scores[-1] > scores[0] + 10:
            lines.append("  추세: 상승 전환 중")
        elif scores[-1] < scores[0] - 10:
            lines.append("  추세: 하락 전환 중")
        else:
            lines.append("  추세: 횡보")

    return "\n".join(lines)


def _format_holdings_brief(holdings: list[dict]) -> str:
    """보유종목 간략 현황."""
    lines = ["보유종목 현황:"]
    total_pnl = 0.0
    for h in holdings[:5]:
        name = h.get("name", "")
        pnl = h.get("pnl_pct", 0)
        total_pnl += pnl
        emoji = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < 0 else "\u26aa"
        lines.append(f"  {emoji} {name}: {pnl:+.1f}%")

    if len(holdings) > 5:
        lines.append(f"  ... 외 {len(holdings) - 5}종목")

    avg_pnl = total_pnl / len(holdings) if holdings else 0
    lines.append(f"  평균: {avg_pnl:+.1f}%")

    return "\n".join(lines)


async def _generate_ai_summary(macro, now: datetime) -> str:
    """Haiku로 빠른 시장 요약 생성."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    client = anthropic.AsyncAnthropic(api_key=api_key)

    prompt = (
        f"현재 시각 {now.strftime('%H:%M')} 기준 시장 현황을 150자 이내로 요약하세요.\n\n"
        f"S&P500: {macro.spx_change_pct:+.2f}%\n"
        f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
        f"VIX: {macro.vix:.1f}\n"
        f"환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
        f"BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
        f"금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n"
        f"US10Y: {macro.us10y:.2f}%\n\n"
        f"호칭: {USER_NAME}\n"
        "볼드(**) 사용 금지\n"
        "핵심만 직접적으로. 마지막에 현재 시장 한줄 판단."
    )

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("Haiku summary generation failed: %s", e)
        return ""


def _assemble_report(
    macro,
    now: datetime,
    pulse_summary: str = "",
    holdings_summary: str = "",
    sector_strengths: list | None = None,
    ai_summary: str = "",
    elapsed_ms: float = 0,
) -> str:
    """최종 보고서 조립."""
    from kstock.bot.messages import _trend_arrow, _fear_greed_bar

    vix_status = "안정" if macro.vix < 20 else "주의" if macro.vix < 25 else "공포"
    fg_score = getattr(macro, "fear_greed_score", 50)
    fg_label = getattr(macro, "fear_greed_label", "중립")

    cache_indicator = "\u26a1" if not macro.is_cached else "\U0001f4be"
    lines = [
        f"\U0001f4ca [실시간 시장 보고서] {now.strftime('%H:%M')} 기준 {cache_indicator}",
        "\u2500" * 25,
        "",
        f"{_trend_arrow(macro.spx_change_pct)} S&P500: {macro.spx_change_pct:+.2f}%",
        f"{_trend_arrow(macro.nasdaq_change_pct)} 나스닥: {macro.nasdaq_change_pct:+.2f}%",
        f"{_trend_arrow(macro.vix_change_pct)} VIX: {macro.vix:.1f} ({vix_status})",
        f"\U0001f4b1 환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)",
        f"\U0001fa99 BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)",
    ]

    if macro.gold_price > 0:
        lines.append(f"\U0001f947 금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)")

    # US10Y / DXY
    us10y_chg = getattr(macro, "us10y_change_pct", 0)
    dxy_chg = getattr(macro, "dxy_change_pct", 0)
    lines.append(f"\U0001f4c9 10년물: {macro.us10y:.2f}% ({us10y_chg:+.1f}%)")
    lines.append(f"\U0001f4b5 DXY: {macro.dxy:.1f} ({dxy_chg:+.1f}%)")

    # Fear & Greed
    fg_bar = _fear_greed_bar(fg_score)
    lines.extend([
        "",
        f"탐욕/공포: {fg_score:.0f}점 ({fg_label}) {fg_bar}",
    ])

    # 시장 맥박
    if pulse_summary:
        lines.extend(["", pulse_summary])

    # AI 요약
    if ai_summary:
        lines.extend(["", "\u2500" * 25, f"\U0001f916 AI 판단:", ai_summary])

    # 보유종목
    if holdings_summary:
        lines.extend(["", "\u2500" * 25, holdings_summary])

    # 응답 속도 표시
    if elapsed_ms > 0:
        lines.extend(["", f"\u23f1 응답: {elapsed_ms:.0f}ms"])

    return "\n".join(lines)
