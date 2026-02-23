"""AI context builder - assembles system prompt with live portfolio/market data.

Gathers data from multiple sources (DB, macro snapshot, policy engine,
broker reports) and formats it into a structured system prompt for the
Claude AI chat handler.

Section 54 of K-Quant system architecture.

Rules:
- No ** bold in any output
- Korean text throughout
- "주호님" personalized greeting
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

SYSTEM_PROMPT_TEMPLATE = '''너는 {user_name}의 전속 투자 참모 '퀀트봇'이다.
CFA/CAIA 자격 보유, 한국+미국 시장 10년차 퀀트 트레이더.

[핵심 원칙]
1. 절대 모호하게 답하지 마. "~할 수 있습니다" 금지. "~하세요"로 단정.
2. 숫자로 말하라. 목표가, 손절가, 비중%, 기간을 반드시 명시.
3. 이유를 반드시 달아라. "왜"가 없는 조언은 금지.
4. 실행 가능한 액션을 줘라. "관심을 가져보세요" 금지. "내일 시가에 10% 비중으로 매수하세요" 식으로.

[분석 프레임워크]
종목 질문 시 반드시 3가지 분석:
- 기술적: RSI, MACD, 이동평균선(5/20/60/120일), 볼린저밴드, 거래량
- 펀더멘털: PER, PBR, ROE, 매출성장률, 영업이익률, 부채비율
- 수급: 외인/기관 순매수, 공매도 잔고, 프로그램 매매

시장 질문 시:
- 글로벌 매크로 환경 (미국 금리, 달러, 유가, 반도체 사이클)
- 한국 시장 특수 요인 (환율, 외인 동향, 정책)
- 섹터 로테이션 관점
- 구체적 전략 제시 (어떤 섹터, 어떤 종목, 비중)

[응답 형식 - 모바일 텔레그램 최적화]
- 볼드(별표 두개) 절대 사용 금지
- 한국어로 답변
- 한 문장은 최대 25자. 긴 문장은 줄바꿈으로 끊어라.
- 각 섹션 사이에 빈 줄 하나 넣어라.
- 구분선: ── (20개)
- 숫자/가격에는 콤마 사용: 75,000원
- 핵심 내용은 이모지로 시작: 📈 📉 💰 ⚠️ 🎯 💡
- 목록은 이모지 bullet으로: ✅ 🔸 →
- 관심/매수/매도 포인트를 명확히 구분:
  🟡 관심: 아직 매수 타이밍 아님, 지켜보기
  🟢 매수: 지금 사도 되는 구간
  🔴 매도: 이익 실현 또는 손절 필요
- 500~800자 범위로 답변 (너무 길지 않게)
- 항상 "{user_name}"으로 호칭

[{user_name}의 투자 성향]
{investor_style}

[보유 종목 + 맞춤 솔루션]
{portfolio_with_solutions}

[오늘의 시장]
{market_data}

[최근 추천 기록]
{recent_recommendations}

[활성 정책 이벤트]
{active_policies}

[최근 리포트]
{recent_reports}

[재무 요약]
{financial_summary}

[매매 교훈]
{trade_lessons}

[종목 분석 시 필수 포인트 태깅]
종목 분석 시 반드시 다음 포인트를 명시하라:
🟡 관심: 아직 매수 타이밍이 아니지만 주시할 가격대와 조건
🟢 매수: 진입하기 좋은 가격대와 그 이유
🔴 매도: 이익실현 또는 손절 가격대

예시 형식:
🟡 관심: 74,000원 이하로 내려오면 주목
🟢 매수: 73,000~74,500원 구간 (20일선 지지)
🎯 목표: 82,000원 (+11%)
🔴 손절: 70,000원 (-5%)

[핵심 지시]
- 위 데이터를 항상 참조하여 {user_name} 맞춤 조언을 제공하라.
- 보유종목별 "맞춤 솔루션"의 보유유형(단타/스윙/포지션/장기)에 맞게 답변하라.
- 단타 종목에는 즉각적이고 구체적인 행동을, 장기 종목에는 펀더멘털 중심 판단을.
- 레버리지/신용 종목은 특히 손절 타이밍에 민감하게 대응하라.
- 투자 성향 데이터를 참고하되, 항상 수익 극대화 관점에서 조언하라.
- 데이터가 없는 항목은 일반론으로 대체하되, 있는 데이터는 반드시 활용하라.'''


def build_system_prompt(context: dict) -> str:
    """Build the system prompt by filling in context data.

    Takes a context dict with pre-formatted Korean strings for each
    data section and interpolates them into the system prompt template.

    Args:
        context: Dict with keys: portfolio, market, recommendations,
                 policies, reports, financials. Missing keys default
                 to "정보 없음" messages.

    Returns:
        Fully formatted system prompt string for Claude API.
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        user_name=USER_NAME,
        investor_style=context.get("investor_style", "투자 성향 데이터 없음"),
        portfolio_with_solutions=context.get(
            "portfolio_with_solutions",
            context.get("portfolio", "보유 종목 정보 없음"),
        ),
        portfolio_data=context.get("portfolio", "보유 종목 정보 없음"),
        market_data=context.get("market", "시장 데이터 없음"),
        recent_recommendations=context.get("recommendations", "최근 추천 없음"),
        active_policies=context.get("policies", "활성 정책 없음"),
        recent_reports=context.get("reports", "최근 리포트 없음"),
        financial_summary=context.get("financials", "재무 데이터 없음"),
        trade_lessons=context.get("trade_lessons", "매매 교훈 없음"),
    )


def get_portfolio_context(db) -> str:
    """Extract portfolio holdings context from DB.

    Reads the latest account screenshot from the database and formats
    each holding as a single line with buy price, current price,
    profit percentage, and quantity.

    Args:
        db: SQLiteStore instance with get_latest_screenshot() method.

    Returns:
        Multi-line string of holdings, or fallback message if unavailable.
        Format: "- 에코프로: 매수 90,700원, 현재 170,900원, +88.4%, 10주"
    """
    try:
        # 1순위: active_holdings (매수 등록된 종목)
        active = db.get_active_holdings()
        if active:
            lines: list[str] = []
            for h in active:
                name = h.get("name", "")
                ticker = h.get("ticker", "")
                bp = h.get("buy_price", 0)
                cp = h.get("current_price", bp)
                pnl = h.get("pnl_pct", 0)
                qty = h.get("quantity", 0)
                lines.append(
                    f"- {name}({ticker}): 매수 {bp:,.0f}원, "
                    f"현재 {cp:,.0f}원, {pnl:+.1f}%, {qty}주"
                )
            return "\n".join(lines)

        # 2순위: 스크린샷 기반
        screenshots = db.get_latest_screenshot()
        if not screenshots:
            return "보유 종목 정보 없음"
        holdings = screenshots.get("holdings_json", "")
        if not holdings:
            return "보유 종목 정보 없음"
        import json
        items = json.loads(holdings) if isinstance(holdings, str) else holdings
        lines = []
        for h in items:
            name = h.get("name", "")
            avg = h.get("avg_price", 0)
            cur = h.get("current_price", 0)
            pct = h.get("profit_pct", 0)
            qty = h.get("quantity", 0)
            lines.append(
                f"- {name}: 매수 {avg:,.0f}원, 현재 {cur:,.0f}원, "
                f"{pct:+.1f}%, {qty}주"
            )
        return "\n".join(lines) if lines else "보유 종목 정보 없음"
    except Exception as e:
        logger.warning("Failed to get portfolio context: %s", e)
        return "보유 종목 정보 없음"


def get_market_context(macro_snapshot: dict | None = None) -> str:
    """Format market data context from a macro snapshot dict.

    Args:
        macro_snapshot: Dict with keys from MacroClient snapshot.

    Returns:
        Multi-line string of market data, or fallback message.
    """
    if not macro_snapshot:
        return "시장 데이터 없음"
    lines: list[str] = []
    # Support both old-style keys and new MacroClient keys
    sp500 = macro_snapshot.get("sp500", macro_snapshot.get("spx_change_pct"))
    nasdaq = macro_snapshot.get("nasdaq", macro_snapshot.get("nasdaq_change_pct"))
    vix = macro_snapshot.get("vix")
    usdkrw = macro_snapshot.get("usdkrw")
    btc = macro_snapshot.get("btc_price")
    gold = macro_snapshot.get("gold_price")
    us10y = macro_snapshot.get("us10y")
    dxy = macro_snapshot.get("dxy")
    fg = macro_snapshot.get("fear_greed")

    if sp500 is not None:
        lines.append(f"S&P500: {sp500:+.2f}%")
    if nasdaq is not None:
        lines.append(f"나스닥: {nasdaq:+.2f}%")
    if vix is not None:
        status = "안정" if vix < 20 else "주의" if vix < 25 else "공포"
        lines.append(f"VIX: {vix:.1f} ({status})")
    if usdkrw is not None and usdkrw > 0:
        lines.append(f"원/달러: {usdkrw:,.0f}원")
    if btc is not None and btc > 0:
        lines.append(f"BTC: ${btc:,.0f}")
    if gold is not None and gold > 0:
        lines.append(f"금: ${gold:,.0f}")
    if us10y is not None and us10y > 0:
        lines.append(f"미국 10년물: {us10y:.2f}%")
    if dxy is not None and dxy > 0:
        lines.append(f"달러인덱스: {dxy:.1f}")
    if fg is not None:
        label = "극도공포" if fg < 25 else "공포" if fg < 45 else "중립" if fg < 55 else "탐욕" if fg < 75 else "극도탐욕"
        lines.append(f"공포탐욕지수: {fg:.0f}점 ({label})")

    return "\n".join(lines) if lines else "시장 데이터 없음"


def get_recommendation_context(db, limit: int = 5) -> str:
    """Get recent recommendations context from DB.

    Fetches active recommendations and formats each one with
    stock name, recommended price, current PnL, and date.

    Args:
        db: SQLiteStore instance with get_active_recommendations() method.
        limit: Maximum number of recommendations to include.

    Returns:
        Multi-line string of recommendations, or fallback message.
    """
    try:
        recs = db.get_active_recommendations()
        if not recs:
            return "최근 추천 없음"
        lines: list[str] = []
        for r in recs[:limit]:
            name = r.get("name", "")
            price = r.get("rec_price", 0)
            pnl = r.get("pnl_pct", 0)
            date = r.get("rec_date", "")
            lines.append(
                f"- {name}: 추천가 {price:,.0f}원, 수익률 {pnl:+.1f}%, ({date})"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get recommendation context: %s", e)
        return "최근 추천 없음"


def get_policy_context(config: dict | None = None) -> str:
    """Get active policy events context.

    Loads policy events from the policy engine and formats each one
    with name and truncated description.

    Args:
        config: Optional policy configuration dict. Passed through
                to get_active_events().

    Returns:
        Multi-line string of policy events, or fallback message.
    """
    try:
        from kstock.signal.policy_engine import get_active_events
        events = get_active_events(config=config)
        if not events:
            return "활성 정책 없음"
        lines: list[str] = []
        for ev in events:
            lines.append(
                f"- {ev.get('name', '')}: {ev.get('description', '')[:50]}"
            )
        return "\n".join(lines)
    except ImportError:
        logger.debug("policy_engine not available for context")
        return "활성 정책 없음"
    except Exception as e:
        logger.warning("Failed to get policy context: %s", e)
        return "활성 정책 없음"


def get_report_context(db, limit: int = 3) -> str:
    """Get recent broker reports context from DB.

    Args:
        db: SQLiteStore instance with get_recent_reports() method.
        limit: Maximum number of reports to include.

    Returns:
        Multi-line string of reports, or fallback message.
    """
    try:
        reports = db.get_recent_reports(limit=limit)
        if not reports:
            return "최근 리포트 없음"
        lines: list[str] = []
        for r in reports:
            lines.append(
                f"- [{r.get('broker', '')}] "
                f"{r.get('title', '')} ({r.get('date', '')})"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get report context: %s", e)
        return "최근 리포트 없음"


def get_financial_context(db) -> str:
    """Get financial summary for portfolio holdings from DB.

    Reads holdings tickers and fetches their financials (PER, PBR, ROE,
    debt ratio) from the financials table.

    Args:
        db: SQLiteStore instance with get_active_holdings() and
            get_financials() methods.

    Returns:
        Financial summary string, or placeholder message.
    """
    try:
        holdings = db.get_active_holdings()
        if not holdings:
            return "보유 종목 재무 데이터 없음"
        lines: list[str] = []
        for h in holdings[:5]:
            ticker = h.get("ticker", "")
            name = h.get("name", ticker)
            fin = db.get_financials(ticker)
            if fin:
                per = fin.get("per", 0)
                pbr = fin.get("pbr", 0)
                roe = fin.get("roe", 0)
                debt = fin.get("debt_ratio", 0)
                lines.append(
                    f"- {name}: PER {per:.1f}, PBR {pbr:.2f}, "
                    f"ROE {roe:.1f}%, 부채비율 {debt:.0f}%"
                )
            else:
                lines.append(f"- {name}: 재무 데이터 미수집")
        return "\n".join(lines) if lines else "보유 종목 재무 데이터 없음"
    except Exception as e:
        logger.warning("Failed to get financial context: %s", e)
        return "재무 데이터 조회 실패"


def build_full_context(
    db,
    macro_snapshot: dict | None = None,
    policy_config: dict | None = None,
) -> dict:
    """Build complete context dict for AI prompt (sync version).

    Calls all individual context functions and returns a single dict
    ready to pass to build_system_prompt() or handle_ai_question().

    Args:
        db: SQLiteStore instance for data access.
        macro_snapshot: Optional macro data dict for market context.
        policy_config: Optional policy configuration dict.

    Returns:
        Dict with keys: portfolio, market, recommendations, policies,
        reports, financials. Each value is a pre-formatted Korean string.
    """
    return {
        "portfolio": get_portfolio_context(db),
        "market": get_market_context(macro_snapshot),
        "recommendations": get_recommendation_context(db),
        "policies": get_policy_context(policy_config),
        "reports": get_report_context(db),
        "financials": get_financial_context(db),
    }


async def build_full_context_async(
    db,
    macro_snapshot: dict | None = None,
    policy_config: dict | None = None,
) -> dict:
    """Build complete context dict for AI prompt (async version).

    Runs all individual context functions in parallel using thread pool
    for improved performance.

    Args:
        db: SQLiteStore instance for data access.
        macro_snapshot: Optional macro data dict for market context.
        policy_config: Optional policy configuration dict.

    Returns:
        Dict with keys: portfolio, market, recommendations, policies,
        reports, financials. Each value is a pre-formatted Korean string.
    """
    loop = asyncio.get_event_loop()
    portfolio, market, recommendations, policies, reports, financials = (
        await asyncio.gather(
            loop.run_in_executor(None, get_portfolio_context, db),
            loop.run_in_executor(None, get_market_context, macro_snapshot),
            loop.run_in_executor(None, get_recommendation_context, db),
            loop.run_in_executor(None, get_policy_context, policy_config),
            loop.run_in_executor(None, get_report_context, db),
            loop.run_in_executor(None, get_financial_context, db),
        )
    )
    return {
        "portfolio": portfolio,
        "market": market,
        "recommendations": recommendations,
        "policies": policies,
        "reports": reports,
        "financials": financials,
    }


async def build_full_context_with_macro(db, macro_client=None, yf_client=None) -> dict:
    """Build context with live macro data from MacroClient (async).

    This is the preferred method - fetches real-time market data
    from the 3-tier cache (memory -> SQLite -> yfinance).

    Args:
        db: SQLiteStore instance for data access.
        macro_client: MacroClient instance for live market data.
        yf_client: YFinanceKRClient instance for real-time stock prices.

    Returns:
        Dict with all context sections populated with live data.
    """
    # Fetch macro snapshot from cache (instant if cached)
    macro_dict = None
    if macro_client:
        try:
            snap = await macro_client.get_snapshot()
            macro_dict = {
                "sp500": getattr(snap, "spx_change_pct", 0),
                "nasdaq": getattr(snap, "nasdaq_change_pct", 0),
                "vix": getattr(snap, "vix", 0),
                "usdkrw": getattr(snap, "usdkrw", 0),
                "btc_price": getattr(snap, "btc_price", 0),
                "gold_price": getattr(snap, "gold_price", 0),
                "us10y": getattr(snap, "us10y", 0),
                "dxy": getattr(snap, "dxy", 0),
                "fear_greed": getattr(snap, "fear_greed_score", 50),
            }
        except Exception as e:
            logger.warning("Failed to get macro for AI context: %s", e)

    loop = asyncio.get_event_loop()
    (
        portfolio, market, recommendations, policies, reports, financials,
        investor_style, portfolio_solutions, trade_lessons_text,
    ) = await asyncio.gather(
        loop.run_in_executor(None, get_portfolio_context, db),
        loop.run_in_executor(None, get_market_context, macro_dict),
        loop.run_in_executor(None, get_recommendation_context, db),
        loop.run_in_executor(None, get_policy_context, None),
        loop.run_in_executor(None, get_report_context, db),
        loop.run_in_executor(None, get_financial_context, db),
        loop.run_in_executor(None, _get_investor_style_context, db),
        loop.run_in_executor(None, _get_portfolio_solutions_context, db),
        loop.run_in_executor(None, _get_trade_lessons_context, db),
    )

    # 실시간 주가 데이터 주입 (yf_client가 있으면)
    realtime_data = ""
    if yf_client:
        try:
            realtime_data = await _get_realtime_portfolio_data(db, yf_client)
        except Exception as e:
            logger.warning("Failed to get realtime portfolio data: %s", e)

    # portfolio에 실시간 데이터 추가
    if realtime_data:
        portfolio = portfolio + "\n\n[실시간 기술지표]\n" + realtime_data

    return {
        "portfolio": portfolio,
        "market": market,
        "recommendations": recommendations,
        "policies": policies,
        "reports": reports,
        "financials": financials,
        "investor_style": investor_style,
        "portfolio_with_solutions": portfolio_solutions,
        "trade_lessons": trade_lessons_text,
    }


async def _get_realtime_portfolio_data(db, yf_client) -> str:
    """보유종목의 실시간 가격 + 기술지표를 yfinance에서 조회."""
    holdings = db.get_active_holdings()
    if not holdings:
        return ""

    lines: list[str] = []
    for h in holdings[:5]:  # 최대 5종목
        ticker = h.get("ticker", "")
        name = h.get("name", ticker)
        if not ticker:
            continue
        try:
            ohlcv = await yf_client.get_ohlcv(ticker, h.get("market", "KOSPI"))
            if ohlcv is None or ohlcv.empty:
                continue
            from kstock.features.technical import compute_indicators
            tech = compute_indicators(ohlcv)
            close = ohlcv["close"].astype(float)
            cur = float(close.iloc[-1])
            lines.append(
                f"- {name}: {cur:,.0f}원 "
                f"| RSI {tech.rsi:.0f} "
                f"| MACD {tech.macd:+.0f} "
                f"| 5일선 {tech.ma5:,.0f} / 20일선 {tech.ma20:,.0f} / 60일선 {tech.ma60:,.0f}"
            )
        except Exception as e:
            logger.debug("Realtime data for %s failed: %s", ticker, e)
            continue
    return "\n".join(lines)


def _get_investor_style_context(db) -> str:
    """투자 성향 컨텍스트 문자열 생성."""
    try:
        from kstock.core.investor_profile import analyze_investor_style, STYLE_LABELS, RISK_LABELS
        insight = analyze_investor_style(db)
        if insight.trade_count == 0:
            return "아직 매매 이력이 부족하여 성향 분석 불가. 기본 '균형형' 전략으로 조언."
        lines = [
            f"스타일: {insight.style_label} (최근 {insight.trade_count}건 분석)",
            f"리스크: {insight.risk_label}",
            f"승률: {insight.win_rate:.0f}%, 평균보유: {insight.avg_hold_days:.0f}일",
            f"평균수익: {insight.avg_profit_pct:+.1f}%, 평균손실: {insight.avg_loss_pct:-.1f}%",
        ]
        if insight.weaknesses:
            lines.append(f"개선점: {', '.join(insight.weaknesses)}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get investor style: %s", e)
        return "투자 성향 데이터 없음"


def _get_portfolio_solutions_context(db) -> str:
    """보유종목 + 보유기간별 솔루션 컨텍스트."""
    try:
        from kstock.core.investor_profile import build_holdings_context_with_solutions
        return build_holdings_context_with_solutions(db)
    except Exception as e:
        logger.warning("Failed to get portfolio solutions: %s", e)
        return "보유 종목 솔루션 데이터 없음"


def _get_trade_lessons_context(db) -> str:
    """매매 교훈 컨텍스트."""
    try:
        lessons = db.get_trade_lessons(limit=5)
        if not lessons:
            return "아직 기록된 매매 교훈 없음"
        lines: list[str] = []
        for l in lessons:
            lines.append(
                f"- {l['name']} {l['action']}: {l['pnl_pct']:+.1f}% "
                f"({l['hold_days']}일) → {l.get('lesson', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get trade lessons: %s", e)
        return "매매 교훈 없음"
