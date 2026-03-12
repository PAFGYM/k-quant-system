"""AI chat handler for K-Quant v9.1 - Claude API integration.

Handles free-form user questions via the Anthropic Claude API.
Maintains daily usage limits, conversation history via ChatMemory,
and injects live portfolio/market context into the system prompt.

Section 53 of K-Quant system architecture.

Rules:
- No ** bold, no Markdown parse_mode
- Korean responses only
- "주호님" personalized greeting
- CFA/CAIA 수준 전문 분석가 관점
- Direct action instructions (not vague)
- [v3.6.6] AI 응답에서 매도 지시 자동 필터링
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"

# Daily limit for AI chat questions
DEFAULT_DAILY_LIMIT = 50

_LIGHT_CHAT_HINTS = (
    "요약", "정리", "번역", "영어", "이메일", "문장", "초안",
    "체크리스트", "할 일", "설명", "한줄", "짧게", "빠르게",
    "뉴스", "시장", "시황", "분위기", "의미", "왜",
)
_INVESTMENT_HEAVY_HINTS = re.compile(
    r"(매수|매도|손절|익절|종목|주가|차트|포트폴리오|잔고|보유|"
    r"목표가|지지선|저항선|6자리\s*종목코드|\d{6})",
    re.IGNORECASE,
)
_DEEP_INVESTMENT_HINTS = re.compile(
    r"(비교|포트폴리오|리밸런싱|리밸런스|시나리오|전략|비중|"
    r"분할매수|분할매도|장기투자|텐베거|수급분석|산업분석|"
    r"거시|매크로|정책|환율|유가|공매도|숏스퀴즈|이벤트)",
    re.IGNORECASE,
)

# [v3.6.6] 매도 지시 / 공포 유발 키워드 필터
_SELL_PATTERNS = [
    r'무조건\s*매도',
    r'전량\s*매도',
    r'즉시\s*매도',
    r'시초가\s*매도',
    r'시초가에\s*매도',
    r'매도\s*주문\s*필수',
    r'1초도\s*망설이지',
    r'알람\s*맞춰',
    r'날리면\s*안\s*됩니다',
    r'절대\s*날리',
    r'이거\s*심각합니다',
    r'긴급\s*전략',
    r'긴급\s*익절',
    r'긴급\s*매도',
]
_SELL_RE = re.compile('|'.join(_SELL_PATTERNS), re.IGNORECASE)


def should_use_lightweight_chat(question: str) -> bool:
    """짧고 가벼운 질문은 경량 채팅 경로로 보낸다."""
    text = str(question or "").strip()
    if not text:
        return False
    if len(text) > 120:
        return False
    if _INVESTMENT_HEAVY_HINTS.search(text):
        return False
    if any(hint in text for hint in _LIGHT_CHAT_HINTS):
        return True
    # 짧은 일반 질의는 기본적으로 빠른 경로로 보낸다.
    return len(text.split()) <= 10


def classify_chat_route(question: str) -> str:
    """질문 복잡도에 따라 light / balanced / deep 경로를 결정한다."""
    text = str(question or "").strip()
    if not text:
        return "balanced"
    if should_use_lightweight_chat(text):
        return "light"

    # 한 종목/짧은 투자 질문은 기본적으로 balanced(저비용 고품질) 경로
    stock_code_count = len(re.findall(r"\b\d{6}\b", text))
    stock_name_count = len(re.findall(r"[가-힣A-Za-z]{2,10}\s*(?:전자|에너지|중공업|하이닉스|바이오|테크|솔루션|모비스|로직스|홀딩스|금융)", text))
    if (
        len(text) <= 180
        and not _DEEP_INVESTMENT_HINTS.search(text)
        and stock_code_count <= 1
        and stock_name_count <= 1
    ):
        return "balanced"

    if len(text) > 180 or _DEEP_INVESTMENT_HINTS.search(text) or stock_code_count >= 2 or stock_name_count >= 2:
        return "deep"

    return "balanced"


def _build_balanced_system_prompt(context: dict) -> str:
    """단일 종목/일반 투자 질문용 중간 프롬프트."""
    market = context.get("market", "")
    portfolio = context.get("portfolio", "")
    recent_briefing = context.get("recent_briefing", "")
    recommendations = context.get("recommendations", "")
    lines = [
        f"너는 {USER_NAME}의 실전 투자 비서다.",
        "한국어 존댓말로 짧고 분명하게 답하라.",
        "투자 질문이면 먼저 결론을 말하고, 이유 2~4개와 지금 볼 포인트를 덧붙여라.",
        "매수/매도 단정 지시는 금지하고, 관심/분할 접근/관망/보유 점검처럼 행동형으로 답하라.",
        "구체적 가격은 제공된 실시간 데이터가 있을 때만 말하라.",
        "본문은 6~10줄 안에서 끝내고, 장황한 서론과 반복은 금지한다.",
    ]
    if market:
        lines.append(f"[시장]\n{market[:900]}")
    if portfolio:
        lines.append(f"[보유]\n{portfolio[:900]}")
    if recent_briefing:
        lines.append(f"[최근 브리핑]\n{recent_briefing[:700]}")
    if recommendations:
        lines.append(f"[최근 추천]\n{recommendations[:700]}")
    return "\n\n".join(lines)


def _build_lightweight_system_prompt(context: dict) -> str:
    """빠른 일반/시황 질문용 경량 시스템 프롬프트."""
    market = context.get("market", "")
    recent_briefing = context.get("recent_briefing", "")
    lines = [
        f"너는 {USER_NAME}의 빠른 투자 비서다.",
        "핵심부터 3~6줄로 짧고 명확하게 답하라.",
        "불필요한 서론, 반복, 과장 금지.",
        "구체적 가격은 제공된 데이터가 있을 때만 인용하고 추측하지 마라.",
        "투자 질문이면 가능하면 '한줄 결론 / 이유 / 지금 할 일' 순서로 답하라.",
    ]
    if market:
        lines.append(f"[시장 요약]\n{market}")
    if recent_briefing:
        lines.append(f"[최근 브리핑]\n{recent_briefing}")
    return "\n\n".join(lines)


def _sanitize_response(answer: str) -> str:
    """[v3.6.6] AI 응답에서 매도 지시 및 공포 유발 표현을 필터링.

    프롬프트 가드레일을 보강하는 코드 기반 안전장치.
    매도 지시가 발견되면 해당 섹션을 부드러운 표현으로 대체.
    """
    # Markdown 정리
    answer = answer.replace("**", "")
    answer = answer.replace("###", "").replace("##", "").replace("# ", "")
    answer = re.sub(r'\n{3,}', '\n\n', answer)

    # 매도 지시 키워드 필터링
    if _SELL_RE.search(answer):
        logger.warning("🚫 AI 응답에서 매도 지시 감지! 필터링 적용.")
        # 문제되는 표현들을 부드럽게 교체
        replacements = {
            '무조건 매도': '상황 점검 필요',
            '전량 매도': '포지션 점검 검토',
            '즉시 매도': '상황 모니터링',
            '시초가에 매도': '시초가 확인 후 판단',
            '시초가 매도': '시초가 확인 후 판단',
            '매도 주문 필수': '시장 상황 주시',
            '1초도 망설이지 마세요': '차분하게 판단하세요',
            '절대 날리면 안 됩니다': '장기 관점에서 차분하게 대응하세요',
            '이거 심각합니다': '주의 깊게 살펴보세요',
            '긴급 전략': '참고 포인트',
            '긴급 익절': '수익 점검',
            '긴급 매도': '상황 점검',
        }
        for bad, good in replacements.items():
            answer = answer.replace(bad, good)
            # 느낌표 뒤에 붙는 경우도 처리
            answer = answer.replace(bad + '!', good)

    # 길이 제한
    if len(answer) > 4000:
        answer = answer[:3997] + "..."

    return answer


async def handle_ai_question(question: str, context: dict, db, chat_memory, verified_names: set | None = None) -> str:
    """Process a user question via Claude API.

    Builds a system prompt from live portfolio/market context, appends
    conversation history, and sends the question to Claude. The response
    is sanitized (no ** bold, no sell orders) and saved to chat memory.

    Args:
        question: User's free-form question text.
        context: Dict with keys: portfolio, market, recommendations,
                 policies, reports, financials. Each value is a
                 pre-formatted Korean string.
        db: SQLiteStore instance for chat_usage tracking.
        chat_memory: ChatMemory instance for conversation history.

    Returns:
        AI response text (Korean, no ** bold, max ~4000 chars).
        On error, returns a user-friendly Korean error message.
    """
    # Validate API key
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return f"{USER_NAME}, AI 기능을 사용하려면 ANTHROPIC_API_KEY 설정이 필요합니다."

    # Check daily usage limit
    daily_limit = int(os.getenv("CHAT_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    usage_count = db.get_chat_usage_count(today)
    if usage_count >= daily_limit:
        return (
            f"{USER_NAME}, 오늘 AI 질문 한도({daily_limit}회)에 도달했습니다. "
            "내일 다시 이용해주세요."
        )

    # Initialize Anthropic async client
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic 패키지 미설치. pip install anthropic 필요.")
        return f"{USER_NAME}, AI 채팅 기능이 현재 사용할 수 없습니다. 다른 기능을 이용해주세요."
    except Exception as e:
        logger.error("Anthropic client initialization error: %s", e)
        return f"{USER_NAME}, AI 연결 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

    # Build system prompt from context
    from kstock.bot.context_builder import build_system_prompt
    chat_route = classify_chat_route(question)
    use_lightweight = chat_route == "light"
    if chat_route == "light":
        system_prompt = _build_lightweight_system_prompt(context)
    elif chat_route == "balanced":
        system_prompt = _build_balanced_system_prompt(context)
    else:
        system_prompt = build_system_prompt(context)

    # v6.2: 과거 관련 대화 검색 + 사용자 선호도 컨텍스트
    rag_context = ""
    pref_context = ""
    if chat_route == "deep":
        try:
            rag_context = chat_memory.get_relevant_context(question, max_items=5)
            pref_context = chat_memory.get_user_preferences_context()
        except Exception as e:
            logger.debug("RAG/preference context extraction failed: %s", e)

    if rag_context or pref_context:
        extra_context = ""
        if pref_context:
            extra_context += f"\n\n{pref_context}"
        if rag_context:
            extra_context += f"\n\n{rag_context}"
        system_prompt = system_prompt + extra_context

    # Assemble conversation messages from history + new question
    history_limit = {
        "light": 6,
        "balanced": 8,
        "deep": 12,
    }.get(chat_route, 8)
    history = chat_memory.get_recent(limit=history_limit)
    messages: list[dict[str, str]] = []
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": question})

    model = "claude-sonnet-4-5-20250929" if chat_route == "deep" else "claude-haiku-4-5-20251001"
    max_tokens = {
        "light": 550,
        "balanced": 850,
        "deep": 1400,
    }.get(chat_route, 850)
    temperature = {
        "light": 0.15,
        "balanced": 0.2,
        "deep": 0.28,
    }.get(chat_route, 0.2)
    logger.info(
        "AI chat route selected: route=%s model=%s history=%d chars=%d",
        chat_route, model, history_limit, len(question or ""),
    )

    # Call Claude API (async) with Prompt Caching
    # - system prompt: explicit cache (변경 시에만 재생성, 동일하면 캐시 히트)
    # - conversation: automatic cache (멀티턴 대화 자동 캐시)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_control={"type": "ephemeral"},  # 대화 자동 캐시
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},  # 시스템 프롬프트 캐시
            }],
            messages=messages,
        )
        answer = response.content[0].text

        # 캐시 히트 통계 로깅
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        if cache_read > 0:
            logger.info(
                "💰 Cache HIT: read=%d, write=%d, input=%d, output=%d (saved ~%.0f%%)",
                cache_read, cache_write, input_tokens, output_tokens,
                (cache_read / (cache_read + cache_write + input_tokens)) * 90,
            )
        else:
            logger.info(
                "📝 Cache MISS: write=%d, input=%d, output=%d",
                cache_write, input_tokens, output_tokens,
            )

        # [v6.2.1] 토큰 사용량 DB 기록
        try:
            from kstock.core.token_tracker import track_usage
            track_usage(
                db=db, provider="anthropic",
                model=model,
                function_name=f"chat:{chat_route}",
                response=response,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error("Claude API call error: %s", e)
        return (
            f"{USER_NAME}, AI 응답 중 오류가 발생했습니다. "
            "잠시 후 다시 시도해주세요."
        )

    # [v3.6.6] 코드 기반 응답 검증 + 매도 지시 필터링
    answer = _sanitize_response(answer)

    # [v3.6.7] 가격 환각 검증 — 현재가 대비 범위 밖 가격 교체
    try:
        from kstock.bot.hallucination_guard import (
            validate_prices_against_context,
            strip_unverified_prices,
            validate_market_indices,
        )
        # 1) 질문에 현재가가 있으면 그 기준으로 범위 밖 가격 교체
        answer = validate_prices_against_context(answer, question)
        # 2) 비보유 종목 가격 교체 — v9.5.2: 항상 실행 (verified 포함)
        holdings = db.get_active_holdings()
        known_names = {h.get("name", "") for h in holdings if h.get("name")}
        if verified_names:
            known_names |= verified_names
        answer = strip_unverified_prices(answer, known_names)
        # 3) [v6.1.3] 시장 지수 환각 검증 — 코스피/코스닥 값 교체
        market_str = context.get("market", "")
        actual_indices = {}
        import re as _re
        kospi_m = _re.search(r"코스피:\s*([\d,]+\.\d+)", market_str)
        kosdaq_m = _re.search(r"코스닥:\s*([\d,]+\.\d+)", market_str)
        if kospi_m:
            actual_indices["kospi"] = float(kospi_m.group(1).replace(",", ""))
        if kosdaq_m:
            actual_indices["kosdaq"] = float(kosdaq_m.group(1).replace(",", ""))
        if actual_indices:
            answer = validate_market_indices(answer, actual_indices)
    except Exception as e:
        logger.error("환각 가드 적용 실패: %s", e)

    # Save to conversation memory and increment daily usage
    chat_memory.add("user", question)
    chat_memory.add("assistant", answer)
    db.increment_chat_usage(today)

    return answer


def format_ai_greeting() -> str:
    """Return greeting message when user enters AI chat mode.

    Displays a friendly greeting with example questions the user
    can ask in Korean.

    Returns:
        Formatted greeting string for Telegram (no ** bold).
    """
    return (
        f"{USER_NAME}, 무엇이든 물어보세요!\n\n"
        "예시:\n"
        "- 에코프로 어떻게 보여?\n"
        "- 오늘 시장 분위기는?\n"
        "- 내 포트폴리오 조언해줘\n"
        "- 반도체 섹터 전망은?"
    )


def format_chat_usage_status(db) -> str:
    """Return current AI chat usage status for today.

    Args:
        db: SQLiteStore instance for chat_usage tracking.

    Returns:
        Formatted usage status string in Korean.
    """
    daily_limit = int(os.getenv("CHAT_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    usage_count = db.get_chat_usage_count(today)
    remaining = max(0, daily_limit - usage_count)
    return (
        f"{USER_NAME}, 오늘 AI 질문 현황\n"
        f"사용: {usage_count}회 / 한도: {daily_limit}회\n"
        f"남은 횟수: {remaining}회"
    )
