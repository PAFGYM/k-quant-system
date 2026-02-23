"""AI chat handler for K-Quant v3.5 - Claude API integration.

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
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

# Daily limit for AI chat questions
DEFAULT_DAILY_LIMIT = 50


async def handle_ai_question(question: str, context: dict, db, chat_memory) -> str:
    """Process a user question via Claude API.

    Builds a system prompt from live portfolio/market context, appends
    conversation history, and sends the question to Claude. The response
    is sanitized (no ** bold) and saved to chat memory.

    Args:
        question: User's free-form question text.
        context: Dict with keys: portfolio, market, recommendations,
                 policies, reports, financials. Each value is a
                 pre-formatted Korean string.
        db: SQLiteStore instance for chat_usage tracking.
        chat_memory: ChatMemory instance for conversation history.

    Returns:
        AI response text (Korean, no ** bold, max ~500 chars).
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
    system_prompt = build_system_prompt(context)

    # Assemble conversation messages from history + new question
    history = chat_memory.get_recent(limit=10)
    messages: list[dict[str, str]] = []
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": question})

    # Call Claude API (async)
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            temperature=0.3,
            system=system_prompt,
            messages=messages,
        )
        answer = response.content[0].text
    except Exception as e:
        logger.error("Claude API call error: %s", e)
        return (
            f"{USER_NAME}, AI 응답 중 오류가 발생했습니다. "
            "잠시 후 다시 시도해주세요."
        )

    # Sanitize: remove all ** bold markers and clean up formatting
    answer = answer.replace("**", "")
    answer = answer.replace("###", "").replace("##", "").replace("# ", "")

    # 3줄 이상 연속 빈 줄 → 2줄로 정리
    import re
    answer = re.sub(r'\n{3,}', '\n\n', answer)

    # Truncate if excessively long (safety net)
    if len(answer) > 4000:
        answer = answer[:3997] + "..."

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
