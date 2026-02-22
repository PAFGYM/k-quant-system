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

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

SYSTEM_PROMPT_TEMPLATE = '''너는 K-Quant AI 투자 어시스턴트야.
사용자 이름은 "{user_name}"이야. 항상 "{user_name}"으로 호칭해.
볼드 마크다운 절대 사용하지 마.
텔레그램이니까 짧고 핵심만 답변해. 최대 500자.
한국어로 답변해.

아래는 {user_name}의 현재 투자 상황이야:

[보유 종목]
{portfolio_data}

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

{user_name}이 질문하면 위 컨텍스트를 참고해서
구체적이고 실행 가능한 조언을 해줘.
"~할 수 있습니다" 같은 애매한 답변 대신
"~하세요" 같은 직접적인 지시로 답변해.'''


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
        portfolio_data=context.get("portfolio", "보유 종목 정보 없음"),
        market_data=context.get("market", "시장 데이터 없음"),
        recent_recommendations=context.get("recommendations", "최근 추천 없음"),
        active_policies=context.get("policies", "활성 정책 없음"),
        recent_reports=context.get("reports", "최근 리포트 없음"),
        financial_summary=context.get("financials", "재무 데이터 없음"),
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
        screenshots = db.get_latest_screenshot()
        if not screenshots:
            return "보유 종목 정보 없음"
        holdings = screenshots.get("holdings_json", "")
        if not holdings:
            return "보유 종목 정보 없음"
        import json
        items = json.loads(holdings) if isinstance(holdings, str) else holdings
        lines: list[str] = []
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
        macro_snapshot: Dict with keys: kospi, kosdaq, usdkrw, sp500, etc.
                       Can be None if data is unavailable.

    Returns:
        Multi-line string of market data, or fallback message.
    """
    if not macro_snapshot:
        return "시장 데이터 없음"
    lines: list[str] = []
    if "kospi" in macro_snapshot:
        lines.append(f"KOSPI: {macro_snapshot['kospi']:,.2f}")
    if "kosdaq" in macro_snapshot:
        lines.append(f"KOSDAQ: {macro_snapshot['kosdaq']:,.2f}")
    if "usdkrw" in macro_snapshot:
        lines.append(f"원/달러: {macro_snapshot['usdkrw']:,.0f}원")
    if "sp500" in macro_snapshot:
        lines.append(f"S&P500: {macro_snapshot['sp500']:,.2f}")
    if "vix" in macro_snapshot:
        lines.append(f"VIX: {macro_snapshot['vix']:,.2f}")
    if "btc_price" in macro_snapshot:
        lines.append(f"BTC: ${macro_snapshot['btc_price']:,.0f}")
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
    """Get financial summary for portfolio holdings.

    Currently returns a placeholder. Will be populated with
    PER, PBR, ROE, and dividend data in v3.5.

    Args:
        db: SQLiteStore instance (reserved for future use).

    Returns:
        Financial summary string, or placeholder message.
    """
    return "재무 데이터 없음 (v3.5에서 업데이트 예정)"


def build_full_context(
    db,
    macro_snapshot: dict | None = None,
    policy_config: dict | None = None,
) -> dict:
    """Build complete context dict for AI prompt.

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
