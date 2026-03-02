"""Chat memory management - stores conversation history in SQLite.

v6.2: Enhanced with topic tagging, keyword extraction, and
context-aware retrieval (lightweight RAG without vector DB).

Provides:
- Basic recent message retrieval (backward compatible)
- Topic-based message search
- Ticker-based message search
- User preference learning from patterns
- Relevant context injection for AI responses
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# 토픽 분류 규칙
TOPIC_PATTERNS = {
    "buy_analysis": [
        r"매수", r"살까", r"사도 될까", r"진입", r"매수 추천", r"지금 사",
    ],
    "sell_analysis": [
        r"매도", r"팔까", r"손절", r"익절", r"청산", r"파는",
    ],
    "market_outlook": [
        r"시장", r"장세", r"코스피", r"코스닥", r"오늘 장", r"내일 장",
        r"미국", r"나스닥", r"금리", r"환율",
    ],
    "stock_analysis": [
        r"어떻게 보", r"전망", r"분석", r"차트", r"목표가", r"적정가",
    ],
    "portfolio": [
        r"포트폴리오", r"보유종목", r"잔고", r"배분", r"리밸런싱",
    ],
    "strategy": [
        r"전략", r"단타", r"스윙", r"장기", r"포지션", r"투자 방법",
    ],
    "risk": [
        r"위험", r"리스크", r"손실", r"MDD", r"방어", r"헤지",
    ],
    "sector": [
        r"섹터", r"반도체", r"2차전지", r"바이오", r"AI", r"에너지",
        r"자동차", r"금융", r"IT",
    ],
    "macro": [
        r"매크로", r"금리", r"CPI", r"고용", r"인플레이션", r"GDP",
        r"연준", r"FOMC",
    ],
}

# 의도 분류
INTENT_PATTERNS = {
    "question": [r"\?", r"어떻", r"어때", r"할까", r"될까", r"인가"],
    "command": [r"해줘", r"보여줘", r"알려줘", r"분석해", r"추천해"],
    "opinion": [r"같아", r"보여", r"생각", r"판단"],
    "information": [r"뭐", r"어디", r"언제", r"얼마", r"몇"],
}

# 한국 종목 코드 패턴
TICKER_PATTERN = re.compile(r"\b(\d{6})\b")
# 종목명 패턴 (일반적인 한국 종목명)
STOCK_NAME_PATTERNS = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스",
    "현대차", "기아", "삼성SDI", "셀트리온", "KB금융", "카카오",
    "NAVER", "포스코", "LG화학", "현대모비스", "삼성물산",
    "에코프로", "에코프로비엠", "포스코퓨처엠", "두산에너빌리티",
    "한화에어로스페이스", "현대건설", "LG전자", "SK이노베이션",
]


class ChatMemory:
    """Manages conversation history with topic-aware retrieval.

    v6.2: Enhanced with topic tagging, keyword extraction, and
    context-aware retrieval for more relevant AI responses.

    Maintains backward compatibility with the basic interface.

    Typical usage::

        memory = ChatMemory(db)
        memory.add("user", "에코프로 어떻게 보여?")
        memory.add("assistant", "주호님, 에코프로는 현재...")

        # 기본 최근 메시지
        history = memory.get_recent(limit=10)

        # 관련 컨텍스트 검색
        context = memory.get_relevant_context("에코프로 매수 타이밍은?")
    """

    def __init__(self, db) -> None:
        """Initialize ChatMemory with a database connection."""
        self.db = db

    def add(self, role: str, content: str) -> None:
        """Add a message to conversation history (basic + enhanced).

        Automatically extracts topic, tickers, intent, and keywords.
        """
        if role not in ("user", "assistant"):
            logger.warning("Invalid chat role '%s'", role)

        # 기본 채팅 이력 (기존 호환)
        self.db.add_chat_message(role, content)

        # 강화된 메시지 저장
        try:
            topic = _extract_topic(content)
            tickers = _extract_tickers(content)
            intent = _extract_intent(content) if role == "user" else ""
            keywords = _extract_keywords(content)

            self.db.add_enhanced_chat_message(
                role=role,
                content=content,
                topic=topic,
                tickers=",".join(tickers),
                intent=intent,
                keywords=",".join(keywords),
            )
        except Exception as e:
            logger.debug("Enhanced chat save failed: %s", e)

        # 사용자 선호도 학습 (user 메시지만)
        if role == "user":
            try:
                _learn_preferences(self.db, content)
            except Exception as e:
                logger.debug("Preference learning failed: %s", e)

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent messages, ordered oldest to newest."""
        return self.db.get_recent_chat_messages(limit=limit)

    def get_relevant_context(
        self, question: str, max_items: int = 5,
    ) -> str:
        """질문과 관련된 과거 대화를 검색하여 컨텍스트 문자열로 반환.

        Lightweight RAG: 토픽 + 티커 + 키워드 기반 검색.
        벡터 DB 없이 SQLite LIKE 검색으로 구현.

        Args:
            question: 사용자의 현재 질문.
            max_items: 반환할 최대 관련 대화 수.

        Returns:
            관련 대화를 요약한 문자열 (없으면 빈 문자열).
        """
        results: list[dict] = []

        # 1. 티커 기반 검색
        tickers = _extract_tickers(question)
        for ticker in tickers[:3]:
            found = self.db.search_chat_by_ticker(ticker, limit=3)
            results.extend(found)

        # 2. 토픽 기반 검색
        topic = _extract_topic(question)
        if topic:
            found = self.db.search_chat_by_topic(topic, limit=3)
            results.extend(found)

        # 3. 키워드 기반 검색
        keywords = _extract_keywords(question)
        for kw in keywords[:2]:
            if len(kw) >= 2:  # 2글자 이상만 검색
                found = self.db.search_chat_by_keywords(kw, limit=2)
                results.extend(found)

        if not results:
            return ""

        # 중복 제거 + 최신순 정렬
        seen_ids = set()
        unique: list[dict] = []
        for r in results:
            rid = r.get("id")
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique.append(r)

        unique.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        selected = unique[:max_items]

        # 컨텍스트 문자열 생성
        context_lines = ["[과거 관련 대화]"]
        for msg in selected:
            role = msg.get("role", "")
            content = msg.get("content", "")[:200]
            date = msg.get("created_at", "")[:10]
            context_lines.append(f"- [{date}] {role}: {content}")

        return "\n".join(context_lines)

    def get_user_preferences_context(self) -> str:
        """사용자 선호도를 AI 시스템 프롬프트용 문자열로 반환."""
        try:
            prefs = self.db.get_user_preferences()
            if not prefs:
                return ""

            lines = ["[사용자 투자 성향]"]
            pref_map = {
                "preferred_horizon": "선호 투자 기간",
                "risk_appetite": "리스크 성향",
                "preferred_sectors": "관심 섹터",
                "question_style": "질문 스타일",
                "active_hours": "활동 시간대",
                "frequent_tickers": "자주 조회 종목",
            }

            for key, label in pref_map.items():
                data = prefs.get(key)
                if data and data.get("confidence", 0) >= 0.3:
                    lines.append(f"- {label}: {data['value']}")

            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""

    def cleanup(self, hours: int = 24) -> int:
        """Delete old basic messages. Enhanced messages kept for 90 days."""
        deleted = self.db.cleanup_old_chat_messages(hours=hours)
        if deleted > 0:
            logger.info("Cleaned up %d old chat messages (older than %dh)", deleted, hours)
        # Enhanced 대화는 90일 유지
        try:
            self.db.cleanup_enhanced_chat(days=90)
        except Exception:
            pass
        return deleted

    def clear(self) -> None:
        """Clear all chat history."""
        self.db.clear_chat_history()
        logger.info("Chat history cleared")

    def message_count(self) -> int:
        """Return total number of messages in history."""
        messages = self.db.get_recent_chat_messages(limit=9999)
        return len(messages)


# ---------------------------------------------------------------------------
# 내부 유틸리티 함수
# ---------------------------------------------------------------------------

def _extract_topic(text: str) -> str:
    """텍스트에서 토픽 추출."""
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for topic, patterns in TOPIC_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if re.search(pattern, text_lower):
                score += 1
        if score > 0:
            scores[topic] = score

    if not scores:
        return ""

    return max(scores, key=scores.get)


def _extract_tickers(text: str) -> list[str]:
    """텍스트에서 종목 코드/이름 추출."""
    tickers = []

    # 6자리 숫자 코드
    for match in TICKER_PATTERN.finditer(text):
        code = match.group(1)
        # 날짜가 아닌지 확인
        if not (code.startswith("20") or code.startswith("19")):
            tickers.append(code)

    # 종목명 매칭
    for name in STOCK_NAME_PATTERNS:
        if name in text:
            tickers.append(name)

    return list(set(tickers))


def _extract_intent(text: str) -> str:
    """사용자 의도 추출."""
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return intent
    return "general"


def _extract_keywords(text: str) -> list[str]:
    """핵심 키워드 추출 (한국어 명사/고유명사)."""
    # 간단한 규칙 기반 (형태소 분석기 없이)
    keywords = []

    # 종목명
    for name in STOCK_NAME_PATTERNS:
        if name in text:
            keywords.append(name)

    # 투자 키워드
    invest_keywords = [
        "매수", "매도", "손절", "익절", "포지션", "스윙", "단타",
        "장기", "배당", "실적", "공시", "수급", "외인", "기관",
        "거래량", "돌파", "지지", "저항", "RSI", "MACD",
        "PER", "PBR", "ROE", "EPS", "목표가", "적정가",
        "리밸런싱", "분산", "헤지", "레버리지",
    ]
    for kw in invest_keywords:
        if kw in text:
            keywords.append(kw)

    return list(set(keywords))


def _learn_preferences(db, content: str) -> None:
    """사용자 메시지에서 선호도 학습.

    규칙 기반으로 반복 패턴 감지하여 preference 업데이트.
    """
    content_lower = content.lower()

    # 투자 기간 선호도
    horizon_scores = {
        "단타": ["단타", "초단기", "데이트레이딩", "오늘", "내일"],
        "스윙": ["스윙", "1~2주", "2주", "단기"],
        "포지션": ["포지션", "1~3개월", "중기"],
        "장기": ["장기", "가치투자", "배당", "장기 보유", "버핏"],
    }

    for horizon, keywords in horizon_scores.items():
        for kw in keywords:
            if kw in content_lower:
                current = db.get_user_preference("preferred_horizon")
                if current == horizon:
                    # 이미 같은 선호 → 신뢰도 상향
                    db.upsert_user_preference(
                        "preferred_horizon", horizon,
                        confidence=min(0.95, 0.6),
                        source="repeated_mention",
                    )
                else:
                    db.upsert_user_preference(
                        "preferred_horizon", horizon,
                        confidence=0.4,
                        source="inferred",
                    )
                break

    # 리스크 성향
    if any(kw in content_lower for kw in ["안전", "보수", "방어", "손실 최소"]):
        db.upsert_user_preference(
            "risk_appetite", "conservative", confidence=0.5, source="inferred",
        )
    elif any(kw in content_lower for kw in ["공격", "레버리지", "고수익", "급등"]):
        db.upsert_user_preference(
            "risk_appetite", "aggressive", confidence=0.5, source="inferred",
        )

    # 관심 섹터 축적
    sectors_found = []
    sector_map = {
        "반도체": ["반도체", "하이닉스", "삼성전자", "칩"],
        "2차전지": ["2차전지", "배터리", "에코프로", "LG에너지", "포스코퓨처엠"],
        "바이오": ["바이오", "셀트리온", "삼성바이오", "신약"],
        "AI": ["AI", "인공지능", "GPU", "엔비디아"],
        "자동차": ["자동차", "현대차", "기아", "전기차", "EV"],
    }
    for sector, keywords in sector_map.items():
        for kw in keywords:
            if kw in content:
                sectors_found.append(sector)
                break

    if sectors_found:
        existing = db.get_user_preference("preferred_sectors") or ""
        all_sectors = set(existing.split(",")) if existing else set()
        all_sectors.update(sectors_found)
        all_sectors.discard("")
        db.upsert_user_preference(
            "preferred_sectors",
            ",".join(sorted(all_sectors)),
            confidence=0.6,
            source="topic_analysis",
        )

    # 자주 조회하는 종목 축적
    tickers_found = _extract_tickers(content)
    if tickers_found:
        existing = db.get_user_preference("frequent_tickers") or ""
        all_tickers = existing.split(",") if existing else []
        all_tickers.extend(tickers_found)
        # 최근 20개만 유지
        recent = all_tickers[-20:]
        # 빈도순 정렬
        freq = Counter(recent).most_common(10)
        top_tickers = [t for t, _ in freq]
        db.upsert_user_preference(
            "frequent_tickers",
            ",".join(top_tickers),
            confidence=0.7,
            source="usage_tracking",
        )
