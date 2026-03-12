"""Action-oriented stock news headline classifier."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StockNewsSignal:
    label: str
    emoji: str
    action: str
    score: int
    category: str
    reason: str


_HEADLINE_RULES: list[dict] = [
    {
        "label": "매수 재료",
        "emoji": "🟢",
        "action": "매수/추가매수 검토",
        "score": 9,
        "category": "수주/계약",
        "reason": "실적 가시성 강화",
        "keywords": ["수주", "계약", "납품", "공급", "독점", "양산", "승인", "허가", "특허"],
    },
    {
        "label": "실적 호재",
        "emoji": "🟢",
        "action": "보유 강화 또는 추세 매수 검토",
        "score": 8,
        "category": "실적",
        "reason": "이익 개선 신호",
        "keywords": ["실적 서프라이즈", "흑자전환", "영업이익", "매출 증가", "호실적", "가이던스 상향"],
    },
    {
        "label": "정책 촉매",
        "emoji": "🟢",
        "action": "테마 확산 전 선점 검토",
        "score": 8,
        "category": "정책",
        "reason": "정책 수혜 기대",
        "keywords": ["규제완화", "특별법", "지원", "보조금", "정책", "국책", "예산", "의무화"],
    },
    {
        "label": "리스크",
        "emoji": "🔴",
        "action": "신규 진입 보류 / 비중 축소 체크",
        "score": -10,
        "category": "자금조달/거버넌스",
        "reason": "희석/신뢰 훼손 가능성",
        "keywords": ["유상증자", "전환사채", "cb", "bw", "감자", "횡령", "배임", "거래정지", "상장폐지"],
    },
    {
        "label": "실적 악화",
        "emoji": "🔴",
        "action": "보유 근거 재점검",
        "score": -8,
        "category": "실적",
        "reason": "이익 추정 하향",
        "keywords": ["적자전환", "실적 부진", "영업손실", "가이던스 하향", "목표가 하향", "투자의견 하향"],
    },
    {
        "label": "주의 신호",
        "emoji": "🟡",
        "action": "사실 확인 후 관망",
        "score": 4,
        "category": "테마/수급",
        "reason": "과열 또는 단기 변동성 가능성",
        "keywords": ["급등", "상한가", "신고가", "테마", "리딩방", "작전", "급락", "하한가"],
    },
    {
        "label": "체크 포인트",
        "emoji": "🟡",
        "action": "보유 이유와 연결되는지 확인",
        "score": 3,
        "category": "애널리스트/일정",
        "reason": "확인성 뉴스",
        "keywords": ["목표가", "투자의견", "설명회", "ir", "컨퍼런스", "실적발표", "간담회"],
    },
]

_MARKET_NOISE = {
    "코스피", "코스닥", "증시", "지수", "외국인", "기관", "개인", "선물", "환율", "금리",
}


def assess_stock_news_headline(title: str) -> StockNewsSignal:
    """헤드라인 기반 매매 행동 신호 추정."""
    lowered = (title or "").lower()
    best = StockNewsSignal(
        label="정보성",
        emoji="⚪",
        action="즉시 매매 재료는 약함",
        score=0,
        category="일반",
        reason="헤드라인만으로는 방향성 약함",
    )

    for rule in _HEADLINE_RULES:
        if any(keyword.lower() in lowered for keyword in rule["keywords"]):
            if abs(rule["score"]) > abs(best.score):
                best = StockNewsSignal(
                    label=rule["label"],
                    emoji=rule["emoji"],
                    action=rule["action"],
                    score=rule["score"],
                    category=rule["category"],
                    reason=rule["reason"],
                )

    if best.score == 0 and any(noise.lower() in lowered for noise in _MARKET_NOISE):
        return StockNewsSignal(
            label="시장 잡음",
            emoji="⚪",
            action="종목 직접 재료 아님",
            score=0,
            category="시장",
            reason="시장 일반 기사",
        )
    return best


def format_stock_news_brief(name: str, news_items: list[dict], max_items: int = 5) -> str:
    """종목 뉴스 리스트를 행동 중심 메시지로 포맷."""
    if not news_items:
        return f"📰 {name}: 최근 뉴스가 없습니다."

    scored = []
    for item in news_items:
        signal = assess_stock_news_headline(item.get("title", ""))
        scored.append((abs(signal.score), signal.score, signal, item))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)

    lines = [f"📰 {name} 뉴스 브리핑", "━" * 20]
    actionable = 0
    for _, _, signal, item in scored[:max_items]:
        title = item.get("title", "")
        date = item.get("date", "")
        source = item.get("source", "")
        lines.append(f"\n{signal.emoji} {signal.label} | {signal.category}")
        lines.append(f"{title}")
        lines.append(f"행동: {signal.action}")
        lines.append(f"이유: {signal.reason}")
        if date or source:
            lines.append(f"{date} | {source}".strip(" |"))
        if item.get("url"):
            lines.append(f"🔗 {item['url']}")
        if signal.score != 0:
            actionable += 1

    if actionable == 0:
        lines.insert(2, "즉시 매매로 연결될 뉴스는 적고, 사실 확인용 헤드라인이 많습니다.")
    else:
        lines.insert(2, f"헤드라인 기준으로 매매에 연결될 만한 뉴스 {actionable}건을 우선 정리했습니다.")
    return "\n".join(lines)
