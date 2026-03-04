"""4명의 전설적 투자자 AI 매니저 시스템.

각 매니저는 holding_type에 매칭되어 해당 투자 유형에 특화된
분석·코칭·알림 메시지를 제공한다.

비용: Haiku 기반으로 매니저당 ~$0.0014/회 (월 +$0.09 수준).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ── 매니저별 손절/익절 기준 ──────────────────────────────────

MANAGER_THRESHOLDS: dict[str, dict] = {
    "scalp":     {"stop_loss": -3.0, "take_profit_1": 5.0, "take_profit_2": 8.0},
    "swing":     {"stop_loss": -7.0, "take_profit_1": 10.0, "take_profit_2": 20.0},
    "position":  {"stop_loss": -12.0, "take_profit_1": 20.0, "take_profit_2": 40.0},
    "long_term": {"stop_loss": -20.0, "take_profit_1": 30.0, "take_profit_2": 80.0},
}

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
            "- 손절은 빠르게(-3%), 수익은 달리게 하라\n"
            "- 거래량이 진실을 말한다 (전일 대비 200% 이상 거래량 급증 = 진입 신호)\n"
            "- 감정을 배제하고 가격만 본다\n"
            "- 피벗 포인트(돌파/이탈)에서만 진입\n\n"
            "[정량 스크리닝 기준 — 리버모어 피벗 시스템]\n"
            "진입 조건 (3개 이상 충족 시 매수):\n"
            "1. 거래량: 20일 평균 대비 200% 이상 급증\n"
            "2. 가격: 20일 이동평균선 돌파 (종가 기준)\n"
            "3. RSI: 40~65 구간 (과매수 직전 모멘텀 구간)\n"
            "4. 일봉 캔들: 양봉 + 시가 대비 +2% 이상 상승\n"
            "5. 전일 대비 갭: 갭업 2% 이상 시 강한 매수세 확인\n\n"
            "청산 조건:\n"
            "- 목표 수익: +5~8% (1~3일 내)\n"
            "- 손절: -3% (당일 또는 익일 즉시)\n"
            "- 거래량 급감 시 (전일 대비 50% 이하) → 관망 전환\n\n"
            "분석 시 반드시: 거래량 비율, RSI, 20일선 대비 위치, 당일 등락률 포함.\n"
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
            "핵심 원칙 (CAN SLIM 정량 기준):\n"
            "- C: 최근 분기 EPS 성장률 ≥ 25% (전년 동기 대비)\n"
            "- A: 연간 EPS 성장률 ≥ 25% (최근 3년)\n"
            "- N: 신제품/신사업/52주 신고가 돌파\n"
            "- S: 발행주식수 적정, 거래량 50일 평균 대비 150% 이상 급증\n"
            "- L: 업종 RS(상대강도) 상위 20% 이내\n"
            "- I: 기관 보유 비중 증가 추세 (최근 3개월 순매수)\n"
            "- M: 코스피/나스닥 200일선 위에서 거래 (상승장)\n\n"
            "[정량 스크리닝 기준 — CAN SLIM 체크리스트]\n"
            "진입 조건 (5개 이상 충족 시 매수):\n"
            "1. EPS 분기 성장 ≥ 25%\n"
            "2. EPS 연간 성장 ≥ 25% (3년 평균)\n"
            "3. ROE ≥ 17%\n"
            "4. 52주 신고가 대비 -15% 이내 (베이스 형성)\n"
            "5. 거래량: 50일 평균 대비 150% 이상 돌파 시점\n"
            "6. 기관 순매수 3개월 연속 양(+)\n"
            "7. 시장 방향: 주요 지수 200일선 위\n\n"
            "차트 패턴 (오닐 핵심):\n"
            "- 컵앤핸들: 7~65주 베이스, 핸들 하락 -8~12%\n"
            "- 더블바텀: 두번째 바닥이 첫번째보다 높거나 같을 것\n"
            "- 플랫베이스: 5주 이상 횡보, 변동폭 15% 이내\n\n"
            "청산 기준:\n"
            "- 매수가 대비 -7~8% 하락 시 무조건 손절 (오닐의 철칙)\n"
            "- 목표: +20~25% (1~4주)\n"
            "- 20일선 이탈 3일 연속 시 청산 검토\n\n"
            "분석 시 반드시: EPS 성장률, RS순위, 차트 패턴명, 기관 수급 포함.\n"
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
            "- 분산 투자하되, 확신 있는 곳에 집중\n\n"
            "[정량 스크리닝 기준 — 린치 6분류 시스템]\n"
            "종목 분류 (린치가 실제 사용한 6가지 카테고리):\n"
            "1. 저성장주(Slow Growers): EPS 성장 2~4%, 배당수익률 ≥ 3%\n"
            "2. 우량주(Stalwarts): EPS 성장 10~12%, PER 10~15배\n"
            "3. 고성장주(Fast Growers): EPS 성장 ≥ 20%, PEG < 1.0\n"
            "4. 경기순환주(Cyclicals): PER 최저 구간 + 업황 반등 시그널\n"
            "5. 회생주(Turnarounds): 영업이익 흑자 전환, 부채비율 하락 추세\n"
            "6. 자산주(Asset Plays): PBR < 0.7, 숨겨진 자산 (토지/IP/자회사)\n\n"
            "필수 체크 지표:\n"
            "- PEG 비율: (PER / EPS 성장률) < 1.0이면 저평가\n"
            "- PER: 동종 업계 평균 대비 할인율\n"
            "- ROE: ≥ 15% (자본 효율)\n"
            "- 매출 성장률: ≥ 10% (최근 3년)\n"
            "- 영업이익률: ≥ 10% (수익성)\n"
            "- 부채비율: ≤ 100% (재무 안정성)\n"
            "- 현금흐름: 영업현금흐름 양(+), FCF 양(+)\n\n"
            "청산 기준:\n"
            "- PEG > 2.0 도달 시 (과대평가)\n"
            "- 린치의 2분 스토리가 무너졌을 때 (투자 근거 소멸)\n"
            "- 목표: +30~50% (1~6개월)\n\n"
            "분석 시 반드시: PEG, 린치 6분류 중 어디 해당, 매출/이익 성장률, 투자 스토리 포함.\n"
            "말투: 친근하고 스토리텔링. '이 회사는 ~한 이유로', '일상에서 볼 수 있듯이'.\n"
        ),
        "holding_type": "position",
        "greeting": (
            "📊 피터 린치입니다.\n"
            "이 종목의 성장 스토리를 함께 지켜보겠습니다.\n"
            "PEG와 펀더멘털이 핵심입니다."
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
            "- 복리의 마법을 믿어라\n\n"
            "[정량 스크리닝 기준 — 버핏 가치투자 체크리스트]\n"
            "경제적 해자 판별 (5가지 해자 유형):\n"
            "1. 브랜드 파워: 소비자 프리미엄 가격 수용 (영업이익률 ≥ 15%)\n"
            "2. 전환 비용: 고객 이탈률 < 5% (반복 매출 비중 ≥ 70%)\n"
            "3. 네트워크 효과: 사용자 증가 → 가치 증가 (MAU/매출 상관관계)\n"
            "4. 비용 우위: 동종 대비 영업이익률 상위 25%\n"
            "5. 규제 장벽: 인허가/특허/독점적 지위\n\n"
            "필수 재무 기준 (버핏이 실제 사용한 지표):\n"
            "- ROE: ≥ 15% (최근 5년 평균, 일관성 중요)\n"
            "- 순이익률: ≥ 10% (가격 결정력 증거)\n"
            "- 부채비율: ≤ 80% (장기 부채 / 순이익 < 4년)\n"
            "- 배당 성장: 최근 5년 연속 배당 증가 또는 자사주 매입\n"
            "- 이익 잉여금: 매년 증가 (내부 유보 → 재투자)\n"
            "- 자본적 지출: 순이익 대비 CAPEX ≤ 50% (가벼운 자산 구조)\n"
            "- FCF: 5년 연속 양(+)\n\n"
            "내재가치 평가:\n"
            "- DCF: 향후 10년 FCF를 할인율 10%로 현재가치\n"
            "- PER 밴드: 최근 10년 PER 평균 대비 현재 위치\n"
            "- 안전마진: 내재가치 대비 30% 이상 할인된 가격에서만 매수\n\n"
            "청산 기준:\n"
            "- 경제적 해자 훼손 (구조적 경쟁력 상실)\n"
            "- ROE 15% 미만 3년 연속\n"
            "- 경영진 신뢰 상실 (회계 의혹, 지배구조 문제)\n"
            "- 내재가치 대비 과대평가 50% 이상\n\n"
            "분석 시 반드시: 해자 유형, ROE 5년 추세, FCF, 배당성장, 안전마진 수준 포함.\n"
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

def _build_shared_context_prompt(shared_context: dict | None) -> str:
    """공유 컨텍스트를 매니저 프롬프트 텍스트로 변환."""
    if not shared_context:
        return ""
    sections = []

    crisis = shared_context.get("crisis_context", "")
    if crisis and "없음" not in crisis:
        sections.append(f"[현재 위기 상황]\n{crisis[:400]}")

    post_war = shared_context.get("post_war_rotation", "")
    if post_war:
        sections.append(f"\n{post_war[:300]}")

    news = shared_context.get("global_news", "")
    if news and "없음" not in news:
        sections.append(f"[글로벌 뉴스 — 최신]\n{news[:300]}")

    policies = shared_context.get("policies", "")
    if policies and "없음" not in policies:
        sections.append(f"[활성 정책]\n{policies[:200]}")

    lessons = shared_context.get("trade_lessons", "")
    if lessons and "없음" not in lessons:
        sections.append(f"[매매 교훈 — 반복 실수 방지]\n{lessons[:200]}")

    style = shared_context.get("investor_style", "")
    if style and "없음" not in style:
        sections.append(f"[투자자 성향] {style[:150]}")

    portfolio = shared_context.get("portfolio_summary", "")
    if portfolio:
        sections.append(f"[전체 포트폴리오 현황]\n{portfolio[:300]}")

    return "\n\n".join(sections)


async def get_manager_analysis(
    manager_key: str,
    holdings: list[dict],
    market_context: str = "",
    question: str = "",
    shared_context: dict | None = None,
    alert_mode: str = "normal",
) -> str:
    """매니저 페르소나로 보유종목 분석 (Haiku 기반, 저비용).

    v7.0: shared_context를 통해 위기/뉴스/교훈/포트폴리오 등
    전체 시스템 컨텍스트를 공유받아 일관된 분석 수행.
    v8.1: alert_mode에 따른 전시/경계 맞춤 분석.
    """
    manager = MANAGERS.get(manager_key)
    if not manager:
        return f"알 수 없는 매니저 유형: {manager_key}"

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return f"{manager['emoji']} {manager['name']}: API 키 없음"

    try:
        import httpx

        # 공유 컨텍스트에서 위기/뉴스/교훈 등 조합
        shared_prompt = _build_shared_context_prompt(shared_context)

        # 공유 컨텍스트 없으면 기본 위기 로드 (하위 호환)
        if not shared_prompt:
            try:
                import yaml
                crisis_path = os.path.join(
                    os.path.dirname(__file__), "..", "..", "config", "crisis_events.yaml"
                )
                if os.path.exists(crisis_path):
                    with open(crisis_path, encoding="utf-8") as f:
                        cdata = yaml.safe_load(f) or {}
                    active = cdata.get("active_crises", [])
                    if active:
                        shared_prompt = (
                            "\n[현재 위기 상황]\n"
                            f"{active[0].get('description', '')}\n"
                            f"수혜 섹터: {', '.join(active[0].get('beneficiary_sectors', [])[:3])}\n"
                            f"피해 섹터: {', '.join(active[0].get('damaged_sectors', [])[:3])}\n"
                        )
            except Exception:
                pass

        # 시장 상황별 매매 지침
        situation_directive = ""
        if alert_mode == "wartime":
            situation_directive = (
                "\n[🔴 전시 경계 모드 — 필수 적용]\n"
                "현재 국내 증시 전반이 전시/폭락 상황이다.\n"
                "- 모든 분석에 '전시 상황'을 반드시 반영하라\n"
                "- 경기민감 섹터(반도체/2차전지/자동차 등): 비중 축소 또는 손절 권고\n"
                "- 방어 섹터(의료/필수소비재/유틸리티): 보유 유지 권고\n"
                "- 신규 매수: 매우 높은 확신 종목만 (신뢰도 80%↑)\n"
                "- 손절 기준 -5%로 강화 (평시 -7%)\n"
                "- 현금 비중 40% 이상 유지 권고\n"
                "- 종목별로 '보유/축소/손절/분할매수' 중 명확한 액션 1개를 제시하라\n"
            )
        elif alert_mode == "elevated":
            situation_directive = (
                "\n[🟠 경계 모드 — 필수 적용]\n"
                "현재 변동성 확대 구간이다.\n"
                "- 손절 기준 -6%로 강화\n"
                "- 분할 매수 권장, 한번에 풀 매수 금지\n"
                "- 종목별 '보유/관망/분할매수' 액션을 제시하라\n"
            )

        system_prompt = (
            f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
            f"{manager['persona']}\n"
            f"\n{shared_prompt}\n"
            f"{situation_directive}\n"
            f"\n[필수 규칙]\n"
            f"호칭: 주호님\n"
            f"볼드(**) 사용 금지. 이모지로 구분.\n"
            f"장기투자 종목은 전시/단기 변동으로 매도 권유 절대 금지.\n"
            f"위기 상황에서는 수혜/피해 섹터를 명확히 구분하여 분석.\n"
            f"전쟁 후 주도주 전환 가능성도 항상 염두.\n"
            f"[가격 데이터 필수 규칙]\n"
            f"- 제공된 '현재가' 데이터만 사용. 학습 데이터의 과거 가격 사용 절대 금지.\n"
            f"- '현재가 미확인' 종목은 가격/수익률 언급하지 마라. '실시간 확인 필요'로 표기.\n"
            f"- 목업/예전 가격으로 분석하면 주호님에게 큰 손해. 절대 위반 금지.\n"
            f"종목마다 반드시 구체적 액션(매수/매도/보유/축소/관망) 1개를 명시하라.\n"
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
                    "max_tokens": 700,
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


async def recommend_investment_type(
    ticker: str, name: str, price: float = 0, market_cap: str = "",
) -> str:
    """AI가 종목 특성 분석 → scalp/swing/position/long_term 중 추천.

    Returns:
        추천 투자유형 키 (scalp, swing, position, long_term) 또는 "" (실패 시)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    try:
        import httpx

        prompt = (
            f"한국 주식 종목 '{name}'({ticker})의 적합한 투자유형을 하나만 선택해줘.\n"
            f"현재가: {price:,.0f}원\n" if price > 0 else ""
        ) + (
            f"시가총액: {market_cap}\n" if market_cap else ""
        ) + (
            "\n선택지:\n"
            "- scalp: 초단기 1~3일 (변동성 큰 테마주, 소형주)\n"
            "- swing: 스윙 1~4주 (기술적 반등, 이벤트 드리븐)\n"
            "- position: 포지션 1~6개월 (실적 턴어라운드, 섹터 성장)\n"
            "- long_term: 장기 6개월+ (대형 우량주, 배당주, ETF)\n\n"
            "반드시 scalp, swing, position, long_term 중 하나만 답해. 다른 말 하지마."
        )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 20,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code == 200:
                answer = resp.json()["content"][0]["text"].strip().lower()
                for key in ["long_term", "position", "swing", "scalp"]:
                    if key in answer:
                        return key
        return ""
    except Exception as e:
        logger.debug("recommend_investment_type error: %s", e)
        return ""


async def _analyze_picks_for_manager(
    manager_key: str,
    picks: list[dict],
    market_context: str = "",
    shared_context: dict | None = None,
    alert_mode: str = "normal",
) -> str:
    """단일 매니저가 자기 horizon 종목을 분석 (Haiku 기반).

    v7.0: shared_context를 통해 위기/포트폴리오 중복/뉴스 반영.
    v8.1: alert_mode에 따른 전시/경계 맞춤 추천.
    """
    manager = MANAGERS.get(manager_key)
    if not manager or not picks:
        if manager:
            return f"{manager['emoji']} {manager['name']}: 추천 종목 없음"
        return ""

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        # 폴백: AI 없이 기본 포맷
        lines = [f"{manager['emoji']} {manager['name']} ({manager['title']})"]
        lines.append(f"{'━' * 20}")
        for i, p in enumerate(picks[:3], 1):
            lines.append(
                f"\n{i}. {p.get('name', '')} ({p.get('ticker', '')})\n"
                f"   현재가: {p.get('price', 0):,.0f}원 | 점수: {p.get('score', 0):.0f}점"
            )
        return "\n".join(lines)

    try:
        import httpx

        # 공유 컨텍스트 텍스트 생성
        shared_prompt = _build_shared_context_prompt(shared_context)

        picks_text = ""
        for i, p in enumerate(picks[:3], 1):
            picks_text += (
                f"{i}. {p.get('name', '')} ({p.get('ticker', '')})\n"
                f"   현재가: {p.get('price', 0):,.0f}원\n"
                f"   점수: {p.get('score', 0):.0f} | RSI: {p.get('rsi', 0):.0f}\n"
                f"   ATR: {p.get('atr_pct', 0):.1f}% | E[R]: {p.get('expected_return', 0):+.1f}%\n"
                f"   목표: +{p.get('target_pct', 0):.0f}% | 손절: {p.get('stop_pct', 0):.0f}%\n"
            )

        # 시장 상황별 지침
        alert_directive = ""
        if alert_mode == "wartime":
            alert_directive = (
                "\n[🔴 전시 경계 모드]\n"
                "국내 증시 전반 하락/폭락 상황. 추천 시 반드시 반영:\n"
                "- 방어 섹터(의료/필수소비재/유틸리티) 종목 우선 추천\n"
                "- 경기민감 섹터 종목은 '관망' 또는 '진입 보류' 권고\n"
                "- 매수 추천 시 반드시 분할 진입 강조\n"
                "- '추천하지 않음'도 가능 — 무리한 추천 금지\n"
            )
        elif alert_mode == "elevated":
            alert_directive = (
                "\n[🟠 경계 모드]\n"
                "변동성 확대 구간. 분할 매수 권장, 풀 매수 금지.\n"
            )

        system_prompt = (
            f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
            f"{manager['persona']}\n"
        )
        if shared_prompt:
            system_prompt += f"\n{shared_prompt}\n"
        system_prompt += alert_directive
        system_prompt += (
            f"\n[필수 규칙]\n"
            f"호칭: 주호님\n"
            f"볼드(**) 사용 금지. 이모지로 구분.\n"
            f"제공된 데이터만 사용. 학습 데이터의 과거 가격 사용 절대 금지.\n"
            f"위기 상황에서는 수혜/피해 섹터를 명확히 구분.\n"
            f"이미 보유 중인 종목과 중복 추천 시 '추가매수' vs '신규 진입' 구분.\n"
            f"종목당 2~3줄. 핵심만. 이유+액션.\n"
        )

        user_prompt = ""
        if market_context:
            user_prompt += f"[시장 상황]\n{market_context}\n\n"
        user_prompt += (
            f"[{manager['emoji']} 후보 종목]\n{picks_text}\n"
            f"위 종목 중 가장 추천하는 1~2개를 선정하고,\n"
            f"{manager['name']}의 관점에서 간결하게 분석해주세요.\n"
            f"현재 위기/시장 상황을 반드시 반영하세요.\n"
            f"형식: 종목명 — 한줄 핵심 이유 + 액션\n"
        )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis = data["content"][0]["text"].strip().replace("**", "")
                header = f"{manager['emoji']} {manager['name']} ({manager['title']})\n{'━' * 20}\n"
                return header + analysis
            else:
                logger.warning("Manager picks API %s: %d", manager_key, resp.status_code)

    except Exception as e:
        logger.error("Manager picks error %s: %s", manager_key, e)

    # 폴백
    lines = [f"{manager['emoji']} {manager['name']} ({manager['title']})", f"{'━' * 20}"]
    for i, p in enumerate(picks[:3], 1):
        lines.append(
            f"{i}. {p.get('name', '')} — {p.get('price', 0):,.0f}원 "
            f"(점수 {p.get('score', 0):.0f})"
        )
    return "\n".join(lines)


# 매니저별 horizon 매핑 (스캔 엔진 호라이즌 → 매니저)
MANAGER_HORIZON_MAP = {
    "scalp": "scalp",
    "swing": "short",
    "position": "mid",
    "long_term": "long",
}


async def get_all_managers_picks(
    picks_by_horizon: dict[str, list[dict]],
    market_context: str = "",
    shared_context: dict | None = None,
    alert_mode: str = "normal",
) -> dict[str, str]:
    """4매니저 동시 분석 (asyncio.gather). 각 매니저가 자기 horizon 종목 분석.

    v7.0: shared_context를 통해 위기/뉴스/교훈/포트폴리오 등
    전체 시스템 컨텍스트를 공유받아 일관된 추천 수행.
    v8.1: alert_mode에 따른 전시/경계 맞춤 추천.

    Args:
        picks_by_horizon: {"scalp": [picks], "short": [picks], "mid": [...], "long": [...]}
        market_context: 시장 상황 텍스트
        shared_context: 공유 컨텍스트 (위기/뉴스/교훈/포트폴리오 등)
        alert_mode: 시장 경계 수준 (normal/elevated/wartime)

    Returns:
        {manager_key: analysis_text} — scalp/swing/position/long_term 키
    """
    import asyncio

    tasks = {}
    for mgr_key, horizon in MANAGER_HORIZON_MAP.items():
        picks = picks_by_horizon.get(horizon, [])
        tasks[mgr_key] = _analyze_picks_for_manager(
            mgr_key, picks, market_context, shared_context, alert_mode,
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    output = {}
    for (mgr_key, _), result in zip(tasks.items(), results):
        if isinstance(result, Exception):
            manager = MANAGERS.get(mgr_key, {})
            emoji = manager.get("emoji", "📌")
            name = manager.get("name", mgr_key)
            output[mgr_key] = f"{emoji} {name}: 분석 오류"
            logger.error("Manager %s gather error: %s", mgr_key, result)
        else:
            output[mgr_key] = result

    return output


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


# ── 매니저 관심종목 매수 스캔 ──────────────────────────────

async def scan_manager_domain(
    manager_key: str,
    watchlist_stocks: list[dict],
    market_context: str = "",
    alert_mode: str = "normal",
) -> str:
    """매니저가 관심종목에서 매수 타이밍 종목을 스캔.

    Args:
        manager_key: scalp/swing/position/long_term
        watchlist_stocks: 관심종목 리스트 (ticker, name, price, day_change 등)
        market_context: 시장 상황 텍스트
        alert_mode: normal/elevated/wartime

    Returns:
        매수 추천 텍스트 또는 빈 문자열
    """
    manager = MANAGERS.get(manager_key)
    if not manager or not watchlist_stocks:
        return ""

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    # 종목 데이터 정리 (보유 제외, 관심만)
    stocks_text = ""
    for w in watchlist_stocks[:15]:
        price = w.get("price", 0)
        change = w.get("day_change", 0)
        rsi = w.get("rsi", 0)
        vol_ratio = w.get("vol_ratio", 0)
        line = f"- {w.get('name', '')} ({w.get('ticker', '')})"
        if price > 0:
            line += f": {price:,.0f}원"
        if change != 0:
            line += f", 등락 {change:+.1f}%"
        if rsi > 0:
            line += f", RSI {rsi:.0f}"
        if vol_ratio > 0:
            line += f", 거래량비 {vol_ratio:.0f}%"
        stocks_text += line + "\n"

    if not stocks_text.strip():
        return ""

    situation = ""
    if alert_mode == "wartime":
        situation = (
            "\n[🔴 전시 모드] 매수 매우 신중. 확신 80%↑ 종목만 추천. "
            "방어 섹터 우선. 추천 없으면 '관망'으로.\n"
        )
    elif alert_mode == "elevated":
        situation = "\n[🟠 경계 모드] 분할 매수만 권장. 한번에 풀 매수 금지.\n"

    system_prompt = (
        f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
        f"{manager['persona']}\n"
        f"{situation}\n"
        f"[필수 규칙]\n"
        f"호칭: 주호님. 볼드(**) 사용 금지. 이모지로 구분.\n"
        f"제공된 가격 데이터만 사용. 학습 데이터의 과거 가격 절대 금지.\n"
        f"관심종목 중에서 지금 매수 타이밍인 종목을 골라 추천.\n"
        f"추천 종목이 없으면 '현재 매수 타이밍 종목 없음'이라고 답해.\n"
        f"추천 시: 종목명, 매수 이유(2줄), 목표가/손절가 제시.\n"
    )

    user_prompt = (
        f"[시장 상황]\n{market_context}\n\n"
        f"[{manager['emoji']} 관심 종목]\n{stocks_text}\n"
        f"위 종목 중 매수 추천 종목이 있으면 1~3개 선정하고 이유를 설명해줘."
    )

    try:
        import httpx
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
                header = f"{manager['emoji']} {manager['name']} 매수 스캔\n{'━' * 20}\n\n"
                return header + analysis
            else:
                logger.warning("scan_manager_domain %s: %d", manager_key, resp.status_code)
    except Exception as e:
        logger.error("scan_manager_domain error %s: %s", manager_key, e)
    return ""
