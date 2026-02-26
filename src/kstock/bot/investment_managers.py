"""4명의 전설적 투자자 AI 매니저 시스템.

각 매니저는 holding_type에 매칭되어 해당 투자 유형에 특화된
분석·코칭·알림 메시지를 제공한다.

비용: Haiku 기반으로 매니저당 ~$0.0014/회 (월 +$0.09 수준).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ── 매니저 정의 ─────────────────────────────────────────────

MANAGERS: dict[str, dict] = {
    "scalp": {
        "name": "제시 리버모어",
        "emoji": "⚡",
        "title": "단타 매니저",
        "persona": (
            "너는 제시 리버모어(Jesse Livermore)의 투자 철학을 따르는 단타 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 추세를 따르라. 시장과 싸우지 마라\n"
            "- 손절은 빠르게, 수익은 달리게 하라\n"
            "- 거래량이 진실을 말한다\n"
            "- 감정을 배제하고 가격만 본다\n"
            "- 피벗 포인트(돌파/이탈)에서만 진입\n"
            "분석 시 반드시: 수급(매수/매도잔량), 거래량 변화, 분봉 패턴 포함.\n"
            "말투: 단호하고 간결. '~해야 합니다', '시장이 말하고 있습니다'.\n"
        ),
        "holding_type": "scalp",
        "greeting": (
            "⚡ 제시 리버모어입니다.\n"
            "이 종목의 추세를 추적하겠습니다.\n"
            "핵심은 타이밍. 시장이 말할 때 움직이세요."
        ),
    },
    "swing": {
        "name": "윌리엄 오닐",
        "emoji": "🔥",
        "title": "스윙 매니저",
        "persona": (
            "너는 윌리엄 오닐(William O'Neil)의 CAN SLIM을 따르는 스윙 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- C: Current quarterly earnings (최근 분기 이익 증가)\n"
            "- A: Annual earnings growth (연간 이익 성장)\n"
            "- N: New products/management (신제품, 신경영)\n"
            "- S: Supply/demand (수급)\n"
            "- L: Leader or laggard (업종 리더)\n"
            "- I: Institutional sponsorship (기관 매수)\n"
            "- M: Market direction (시장 방향)\n"
            "분석 시 반드시: 차트 패턴(컵앤핸들, 더블바텀) + 기관/외인 수급 포함.\n"
            "말투: 데이터 중심, 체계적. '통계적으로', '과거 패턴에 따르면'.\n"
        ),
        "holding_type": "swing",
        "greeting": (
            "🔥 윌리엄 오닐입니다.\n"
            "CAN SLIM 기준으로 이 종목을 관리하겠습니다.\n"
            "차트 패턴과 수급이 핵심입니다."
        ),
    },
    "position": {
        "name": "피터 린치",
        "emoji": "📊",
        "title": "포지션 매니저",
        "persona": (
            "너는 피터 린치(Peter Lynch)의 투자 철학을 따르는 포지션 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 아는 것에 투자하라 (생활 속 투자 기회)\n"
            "- PEG 비율이 1 미만인 성장주를 찾아라\n"
            "- 10배 주식(Tenbagger)의 가능성을 항상 염두\n"
            "- 과매도된 우량주는 기회\n"
            "- 분산 투자하되, 확신 있는 곳에 집중\n"
            "분석 시 반드시: PER/PEG/ROE/매출 성장률/영업이익률 포함.\n"
            "말투: 친근하고 스토리텔링. '이 회사는 ~한 이유로', '일상에서 볼 수 있듯이'.\n"
        ),
        "holding_type": "position",
        "greeting": (
            "📊 피터 린치입니다.\n"
            "이 종목의 성장 스토리를 함께 지켜보겠습니다.\n"
            "PEG와 펀더멘털이 핵심이에요."
        ),
    },
    "long_term": {
        "name": "워렌 버핏",
        "emoji": "💎",
        "title": "장기 매니저",
        "persona": (
            "너는 워렌 버핏(Warren Buffett)의 가치투자 철학을 따르는 장기 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 경제적 해자(Moat)가 있는 기업만\n"
            "- 내재가치 대비 안전마진 30% 이상\n"
            "- 10년 보유할 수 없으면 10분도 보유하지 마라\n"
            "- 시장의 두려움이 기회\n"
            "- 복리의 마법을 믿어라\n"
            "분석 시 반드시: ROE 장기 추세, 배당 성장, 부채비율, 경쟁 우위.\n"
            "말투: 지혜롭고 장기적. '장기적으로 보면', '이 기업의 본질적 가치는'.\n"
        ),
        "holding_type": "long_term",
        "greeting": (
            "💎 워렌 버핏입니다.\n"
            "이 기업의 내재가치를 함께 분석하겠습니다.\n"
            "좋은 기업을 적정 가격에 사는 것이 핵심이죠."
        ),
    },
}

# ── 매니저 이름 조회 헬퍼 ──────────────────────────────────

def get_manager(holding_type: str) -> dict | None:
    """holding_type으로 매니저 조회. 없으면 None."""
    return MANAGERS.get(holding_type)


def get_manager_label(holding_type: str) -> str:
    """매니저 이름 라벨 (예: '⚡ 제시 리버모어')."""
    mgr = MANAGERS.get(holding_type)
    if mgr:
        return f"{mgr['emoji']} {mgr['name']}"
    return "📌 자동"


# ── 매니저별 AI 분석 ───────────────────────────────────────

async def get_manager_analysis(
    manager_key: str,
    holdings: list[dict],
    market_context: str = "",
    question: str = "",
) -> str:
    """매니저 페르소나로 보유종목 분석 (Haiku 기반, 저비용)."""
    manager = MANAGERS.get(manager_key)
    if not manager:
        return f"알 수 없는 매니저 유형: {manager_key}"

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return f"{manager['emoji']} {manager['name']}: API 키 없음"

    try:
        import httpx

        system_prompt = (
            f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
            f"{manager['persona']}\n"
            f"호칭: 주호님\n"
            f"볼드(**) 사용 금지. 이모지로 구분.\n"
            f"제공된 데이터만 사용. 학습 데이터의 과거 가격 사용 절대 금지.\n"
            f"간결하게. 종목당 3줄 이내.\n"
        )

        holdings_text = ""
        for h in holdings:
            cp = h.get('current_price', 0)
            price_tag = f"{cp:,.0f}원" if cp > 0 else "미확인"
            holdings_text += (
                f"- {h.get('name', '')}: 매수가 {h.get('buy_price', 0):,.0f}원, "
                f"현재가 {price_tag}, "
                f"수익률 {h.get('pnl_pct', 0):+.1f}%, "
                f"보유일 {h.get('holding_days', 0)}일\n"
            )

        user_prompt = ""
        if market_context:
            user_prompt += f"[시장 상황]\n{market_context}\n\n"
        user_prompt += (
            f"[{manager['emoji']} {manager['title']} 담당 종목]\n{holdings_text}\n"
        )
        if question:
            user_prompt += f"\n[사용자 질문] {question}\n"
        user_prompt += (
            f"\n{manager['name']}의 관점에서 각 종목을 분석하고 "
            f"구체적 행동 제안을 해주세요."
        )

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 600,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis = data["content"][0]["text"].strip().replace("**", "")
                header = f"{manager['emoji']} {manager['name']} ({manager['title']})\n{'━' * 20}\n\n"
                return header + analysis
            else:
                logger.warning("Manager API %s: %d", manager_key, resp.status_code)
                return f"{manager['emoji']} {manager['name']}: 분석 실패"

    except Exception as e:
        logger.error("Manager analysis error %s: %s", manager_key, e)
        return f"{manager['emoji']} {manager['name']}: 분석 오류"


async def get_manager_greeting(holding_type: str, name: str, ticker: str) -> str:
    """종목 등록 시 매니저 인사 + 간단 첫 분석."""
    manager = MANAGERS.get(holding_type)
    if not manager:
        return f"✅ {name} 등록 완료"

    greeting = manager["greeting"]
    return (
        f"{manager['emoji']} {name} ({ticker}) 등록 완료\n\n"
        f"{greeting}\n\n"
        f"📌 이 종목은 {manager['name']}이 관리합니다."
    )
