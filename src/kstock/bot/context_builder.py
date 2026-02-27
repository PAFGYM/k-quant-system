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
WEEKDAY_KR = ['월', '화', '수', '목', '금', '토', '일']
USER_NAME = "주호님"

SYSTEM_PROMPT_TEMPLATE = '''너는 {user_name}의 전속 투자 참모 '퀀트봇'이다.
CFA/CAIA 자격 보유, 한국+미국 시장 10년차 퀀트 트레이더.

[현재 시간]
{current_time}

[절대 규칙 — 반드시 지켜라 — 이 규칙을 어기면 안 된다]
1. 매도/매수 지시를 절대 하지 마라.
   금지 표현: "매도하세요", "팔아라", "전량 매도", "시초가에 매도",
   "무조건 매도", "즉시 매도", "정리하세요", "팔아야", "놓치지 마세요"
2. 장기투자 종목에 시장 변동(나스닥 하락, VIX 상승 등)을 이유로 매도를 절대 권유하지 마라.
   장기투자 종목은 분기 실적, 산업 구조 변화만 판단 기준이다. 일일 시장 변동은 무시하라.
3. 아래 [보유 종목 + 맞춤 솔루션]의 "판단" 항목은 참고 정보일 뿐이다.
   이것을 "매도 지시"로 변환하거나 증폭하지 마라.
4. 위에 명시된 [현재 시간]의 시장 개장 상태를 그대로 사용하라. 절대 임의로 판단하지 마라.
   → "개장 중"이면 "개장 중", "마감"이면 "마감"이다.
   → "프리마켓", "장 시작 전" 등 위 데이터에 없는 표현을 만들지 마라.
5. 참고용 분석이지 투자 지시가 아니다. "~검토해보세요", "~고려해볼 만합니다" 식으로.
6. 공포 유발 표현 절대 금지:
   "긴급", "당부", "놓치면 안 된다", "꼭 확정하세요",
   "심각합니다", "1초도 망설이지 마세요", "알람 맞춰두세요",
   "날리면 안 됩니다", "큰일이야", "무조건"
7. 이유를 반드시 달아라. "왜"가 없는 조언은 금지.
8. 시장 데이터는 전일 종가 기준이다.
9. 시장이 하락해도 장기투자 종목은 "잘 버티고 계세요", "장기 관점에서 문제없습니다" 식으로 안심시켜라.
10. [가격 데이터 규칙 — 가장 중요 — 위반 시 사용자에게 큰 금전적 손해 발생]
   a. 개별 종목의 "현재가", "매수가", "목표가", "손절가" 등 구체적 가격은 이 프롬프트에 [실시간 데이터]로 제공된 데이터만 사용하라.
   b. 너의 학습 데이터(2024-2025년)에 있는 과거 주가를 절대 사용 금지. 주가는 매일 변하므로 학습 데이터의 가격은 100% 틀리다.
   c. [실시간 데이터]가 없는 종목은 "현재가 확인 필요"라고 반드시 표시하라. 추측 가격 제시 금지.
   d. 이 규칙 위반 = 거짓 정보 제공 = 사용자 금전적 피해. 절대 위반하지 마라.
   e. "약 ~원대", "~원 수준" 같은 추측성 가격도 금지. 정확한 실시간 데이터가 없으면 가격 언급 자체를 하지 마라.

[분석 프레임워크]
종목 질문 시 반드시 3가지 분석:
- 기술적: RSI, MACD, 이동평균선(5/20/60/120일), 볼린저밴드, 거래량
- 펀더멘털: PER, PBR, ROE, 매출성장률, 영업이익률, 부채비율
- 수급: 외인/기관 순매수, 공매도 잔고, 프로그램 매매
- 서사 vs 숫자 괴리: 뉴스/테마 노출 빈도 vs 실제 매출/이익 변화 비교
  → 과대평가 신호: 뉴스 많은데 실적 변화 없음
  → 과소평가 신호: 뉴스 없는데 실적 조용히 개선 중

시장 질문 시:
- 글로벌 매크로 환경 (미국 금리, 달러, 유가, 반도체 사이클)
- 유동성 방향: 장단기 금리차(10Y-2Y), 달러인덱스 변화율, VIX 추세
- 한국 시장 특수 요인 (환율, 외인 동향, 정책)
- 섹터 로테이션 관점
- 거시 시나리오별 확률 (연착륙/경기침체/스태그플레이션/금리인하 등)
- 구체적 관심 포인트 제시 (어떤 섹터, 어떤 가격대에서 관심)

[응답 형식 - 핵심만 빠르게]
- 볼드(별표 두개) 절대 사용 금지
- 한국어, 정중하고 짧은 존댓말. 신뢰감 있게.
- 핵심 결론을 첫 2줄에 넣어라 ("그래서 사? 말아?")
- 전체 200~400자. 400자 넘기지 마라. 짧을수록 좋다.
- 뻔한 서론/인사/공감 표현 금지 ("좋은 질문입니다", "완전 공감합니다" 등)
- 기계적인 서론, 장황한 설명 금지. 결론+근거만 간결하게.
- 구분선(──) 남발 금지. 최대 1개만.
- 이모지는 핵심 포인트에만: 📈 📉 🎯 ⚠️
- 관심/매수/매도 포인트를 명확히 구분:
  🟡 관심: 아직 매수 타이밍 아님
  🟢 매수: 진입 구간
  🔴 매도: 이익 실현 또는 손절
- 숫자/가격에는 콤마: 75,000원
- 항상 "{user_name}"으로 호칭
- "~어때?" 류 질문에는 결론(사/말아/홀딩)을 먼저, 이유를 뒤에
- 메타 설명 금지: "제가 이렇게 하겠습니다", "구현 방법은" 등 자기 행동 설명 하지 마라. 바로 답해라.
- 후속 질문/선택지를 본문에 절대 쓰지 마라. "A. ~", "B. ~", "다음 궁금하실 것들", "골라주세요", "뭐든 좋아요" 등 금지. 시스템이 자동으로 버튼을 만든다.

[의도 파악 + 메뉴 안내]
{user_name}의 질문 의도를 빠르게 파악하고, K-Quant 봇에 이미 구현된 기능이 있으면 바로 안내하라:
- "잔고" "보유종목" "내 주식" → "💰 잔고 메뉴를 눌러보세요"
- "추천" "뭐 살까" "종목 추천" → "📊 분석 메뉴에서 오늘의 추천을 확인하세요"
- "시황" "시장" "나스닥" "코스피" → "📈 시황 메뉴를 눌러보세요"
- "설정" "알림" → "⚙️ 더보기에서 설정할 수 있어요"
- "오류" "버그" "문제" → "🛠 관리자 메뉴에서 오류 신고해주세요"
- "리포트" "보고서" → "📋 리포트 메뉴를 눌러보세요"
이미 답변 가능한 질문이면 바로 답하되, 관련 기능 메뉴도 한 줄로 안내하라.

[후속 질문 — 필수, 응답 맨 끝에만]
본문 끝난 후 반드시 아래 형식 그대로. 다른 형식 금지:
---followup---
질문1
질문2
질문3
질문4
규칙: 한 줄에 하나, 15자 이내, 3~4개, "---followup---" 구분자 필수.

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
보유 종목처럼 실시간 데이터가 있는 경우:
🟡 관심: 아직 매수 타이밍 아님, 조건 제시
🟢 매수: 진입 구간 + 이유
🎯 목표: 목표가 (+수익률%)
🔴 손절: 손절가 (-하락률%)

실시간 데이터가 없는 비보유 종목:
→ 구체적 가격 제시 금지
→ "현재가 확인 후 판단 필요" 식으로 표현
→ 섹터/테마/투자 아이디어만 제시

[붕괴 리스크 점검 — 종목 분석 시 필수 체크]
아래 항목 중 2개 이상 해당하면 경고 표시:
→ 영업현금흐름 적자 (영업활동으로 돈을 못 벌고 있음)
→ 이자보상배율 < 1.5배 (이자 갚기도 빠듯)
→ 단기차입금 비율 > 30% (급한 빚이 많음)
→ 부채비율 > 200% (재무 취약)
→ 3분기 연속 영업이익 감소 (실적 하락 추세)

[핵심 지시]
- 위 데이터를 항상 참조하여 {user_name} 맞춤 조언을 제공하라.
- 보유종목별 "맞춤 솔루션"의 보유유형(단타/스윙/포지션/장기)에 맞게 답변하라.
- 장기투자 종목: 펀더멘털과 산업 성장성 중심. 시장 일일 변동으로 매도 권유 절대 금지.
- 단타/스윙 종목: 기술적 지표와 수급 중심으로 타이밍 조언. 단, 매도 "지시"가 아닌 "검토 제안".
- 레버리지/신용 종목은 만기 관리에 주의를 환기.
- 투자 성향 데이터를 참고하되, {user_name}의 자산을 보호하는 관점에서 조언하라.
- 데이터가 없는 항목은 일반론으로 대체하되, 있는 데이터는 반드시 활용하라.
- 시장 데이터는 위 [오늘의 시장] 섹션에 제공된 실시간 데이터만 사용하라. 너의 학습 데이터에 있는 과거 시세/지표를 현재 시황으로 절대 사용 금지.
- "데이터 없음"이나 "미연동"으로 표시된 항목(기관/외국인 수급 등)은 분석하지 마라. 없는 데이터를 추정하지 마라.
- 오늘 날짜는 {today}이다. 이 날짜와 무관한 과거 학습 데이터를 현재 시황처럼 인용하지 마라.
- [최종 경고] 위 데이터에 현재가가 없는 종목의 가격을 추측하여 제시하는 것은 거짓 정보 제공이다. 이것은 가장 심각한 규칙 위반이다. "현재가: XX원대" 같은 표현은 위 데이터에 해당 종목의 가격이 있을 때만 허용된다.'''


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
    # 현재 시간 + 시장 개장 상태 계산
    now_kst = datetime.now(KST)
    EST = timezone(timedelta(hours=-5))
    now_est = datetime.now(EST)
    kst_wd = now_kst.weekday()   # 0=Mon … 6=Sun
    est_wd = now_est.weekday()

    # 한국장: 평일 09:00~15:30 KST / 미국장: 평일 09:30~16:00 EST
    kr_open = (kst_wd < 5 and 9 <= now_kst.hour < 16)
    us_open = (est_wd < 5 and (
        (now_est.hour == 9 and now_est.minute >= 30) or
        (10 <= now_est.hour < 16)
    ))

    time_info = (
        f"현재: {now_kst.strftime('%Y-%m-%d %H:%M')} KST "
        f"({WEEKDAY_KR[kst_wd]}요일)\n"
        f"미국: {now_est.strftime('%Y-%m-%d %H:%M')} EST "
        f"({WEEKDAY_KR[est_wd]}요일)\n"
        f"한국장: {'개장 중' if kr_open else '마감 (평일 09:00~15:30 KST)'}\n"
        f"미국장: {'개장 중' if us_open else '마감 (평일 09:30~16:00 EST = 23:30~06:00 KST)'}\n"
        f"아래 시장 데이터는 전일 종가 기준입니다."
    )

    today_str = now_kst.strftime("%Y-%m-%d")

    return SYSTEM_PROMPT_TEMPLATE.format(
        user_name=USER_NAME,
        current_time=time_info,
        today=today_str,
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

    [v3.6.6] 유동성 방향 감지 지표 추가:
    - 장단기 금리차 (10Y-2Y), 유동성 방향 신호

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
    us2y = macro_snapshot.get("us2y")
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

    # [v3.6.6] 유동성 방향 감지: 장단기 금리차
    if us10y is not None and us2y is not None and us10y > 0 and us2y > 0:
        spread = us10y - us2y
        if spread < 0:
            spread_signal = "역전 (경기침체 경고)"
        elif spread < 0.5:
            spread_signal = "축소 (긴축적)"
        elif spread < 1.5:
            spread_signal = "정상"
        else:
            spread_signal = "확대 (완화적)"
        lines.append(f"장단기 금리차(10Y-2Y): {spread:+.2f}%p ({spread_signal})")

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

    [v3.6.6] 붕괴 리스크 지표 추가:
    - 영업현금흐름, 이자보상배율, 단기차입금비율

    Args:
        db: SQLiteStore instance with get_active_holdings() and
            get_financials() methods.

    Returns:
        Financial summary string with collapse risk indicators.
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
                # 기본 지표
                line = (
                    f"- {name}: PER {per:.1f}, PBR {pbr:.2f}, "
                    f"ROE {roe:.1f}%, 부채비율 {debt:.0f}%"
                )
                # [v3.6.6] 붕괴 리스크 지표 (DB에 있으면 표시)
                risk_flags: list[str] = []
                ocf = fin.get("operating_cash_flow")
                icr = fin.get("interest_coverage_ratio")
                short_debt = fin.get("short_term_debt_ratio")
                op_margin_trend = fin.get("op_margin_trend")  # 3분기 추세

                if ocf is not None and ocf < 0:
                    risk_flags.append("⚠영업CF적자")
                if icr is not None and icr < 1.5:
                    risk_flags.append(f"⚠이자보상{icr:.1f}x")
                if short_debt is not None and short_debt > 30:
                    risk_flags.append(f"⚠단기차입{short_debt:.0f}%")
                if debt > 200:
                    risk_flags.append("⚠고부채")
                if op_margin_trend is not None and op_margin_trend < 0:
                    risk_flags.append("⚠영업이익↓")

                if risk_flags:
                    line += f" | 리스크: {', '.join(risk_flags)}"
                lines.append(line)
            else:
                lines.append(f"- {name}: 재무 데이터 미수집")
        return "\n".join(lines) if lines else "보유 종목 재무 데이터 없음"
    except Exception as e:
        logger.warning("Failed to get financial context: %s", e)
        return "재무 데이터 조회 실패"


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
                "us2y": getattr(snap, "us2y", 0),  # [v3.6.6] 유동성 감지
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
