"""글로벌 뉴스 수집기 — RSS 기반 실시간 헤드라인 수집.

지정학 리스크, 매크로 이벤트, 시장 급변 뉴스를 자동 수집하여
AI 컨텍스트와 브리핑에 반영한다.

v6.0: 초기 버전 — RSS 피드 기반
v6.1: 위기 감지 + 매크로 선행지표 연동 + 적응형 빈도
v6.2.2: 영문 뉴스 제목 한글 번역 자동화
v8.2: YouTube 경제방송 확대 (10채널) + 자막 기반 내용 요약
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from dataclasses import dataclass, field

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# ── RSS 피드 소스 정의 ──────────────────────────────────────

RSS_FEEDS: list[dict] = [
    # 한국 뉴스
    {
        "name": "한경 글로벌",
        "url": "https://www.hankyung.com/feed/globalmarket",
        "lang": "ko",
        "category": "market",
    },
    {
        "name": "연합뉴스 경제",
        "url": "https://www.yna.co.kr/rss/economy.xml",
        "lang": "ko",
        "category": "economy",
    },
    {
        "name": "연합뉴스 국제",
        "url": "https://www.yna.co.kr/rss/international.xml",
        "lang": "ko",
        "category": "geopolitics",
    },
    # 한국 경제 전문 매체
    {
        "name": "매일경제",
        "url": "https://www.mk.co.kr/rss/30000001/",
        "lang": "ko",
        "category": "market",
    },
    {
        "name": "매경 경제",
        "url": "https://www.mk.co.kr/rss/30100041/",
        "lang": "ko",
        "category": "economy",
    },
    # 글로벌 영문 뉴스
    {
        "name": "CNBC World",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
        "lang": "en",
        "category": "market",
    },
    {
        "name": "Reuters Business",
        "url": "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best",
        "lang": "en",
        "category": "market",
    },
]

# ── 유튜브 경제방송 채널 RSS 피드 (v8.2 확대) ──────────────
YOUTUBE_FEEDS: list[dict] = [
    # 경제 전문 방송
    {
        "name": "삼프로TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UChlv4GSd7OQl3js-jkLOnFA",
        "lang": "ko",
        "category": "youtube_finance",
    },
    {
        "name": "한국경제TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCF8AeLlUbEpKju6v1H6p8Eg",
        "lang": "ko",
        "category": "youtube_news",
    },
    {
        "name": "SBS Biz",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbMjg2EvXs_RUGW-KrdM3pw",
        "lang": "ko",
        "category": "youtube_news",
    },
    {
        "name": "이데일리TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC8Sv6O3Ux8ePVqorx8aOBMg",
        "lang": "ko",
        "category": "youtube_news",
    },
    {
        "name": "MTN머니투데이",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UClErHbdZKUnD1NyIUeQWvuQ",
        "lang": "ko",
        "category": "youtube_news",
    },
    {
        "name": "매일경제TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCnfwIKyFYRuqZzzKBDt6JOA",
        "lang": "ko",
        "category": "youtube_news",
    },
    {
        "name": "토마토증권통",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCgJ5pT6S2NuTVP-6-AlLZew",
        "lang": "ko",
        "category": "youtube_finance",
    },
    # 투자 분석 채널
    {
        "name": "뉴욕주민",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC3dYEYtdihZpsexdC9-qKDA",
        "lang": "ko",
        "category": "youtube_us_market",
    },
    {
        "name": "월가아재",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCpqD9_OJNtF6suPpi6mOQCQ",
        "lang": "ko",
        "category": "youtube_finance",
    },
    {
        "name": "박곰희TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCr7XsrSrvAn_WcU4kF99bbQ",
        "lang": "ko",
        "category": "youtube_finance",
    },
    # v10.0: 추가 채널
    {
        "name": "슈카월드",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCsJ6RuBiTVWRX156FVbeaGg",
        "lang": "ko",
        "category": "youtube_finance",
    },
]

# ── 긴급 이벤트 키워드 ────────────────────────────────────

URGENT_KEYWORDS_KO = [
    "전쟁", "공습", "폭격", "미사일", "핵", "제재", "봉쇄",
    "대공황", "경기침체", "리세션", "금융위기", "디폴트", "파산",
    "급락", "폭락", "서킷브레이커", "블랙먼데이", "패닉",
    "긴급", "계엄", "쿠데타", "테러",
    "금리 인상", "금리 인하", "양적완화", "양적긴축",
    "관세", "무역전쟁", "수출규제",
    "유가 급등", "유가 폭등", "호르무즈",
]

URGENT_KEYWORDS_EN = [
    "war", "strike", "bomb", "missile", "nuclear", "sanction", "blockade",
    "recession", "depression", "crisis", "default", "bankrupt",
    "crash", "plunge", "circuit breaker", "panic", "black monday",
    "emergency", "martial law", "coup", "terror",
    "rate hike", "rate cut", "QE", "QT",
    "tariff", "trade war", "export ban",
    "oil surge", "oil spike", "hormuz",
]

# 시장 영향도 키워드 (가중치)
IMPACT_KEYWORDS = {
    "전쟁": 10, "war": 10, "공습": 9, "strike": 8,
    "핵": 10, "nuclear": 10, "미사일": 8, "missile": 8,
    "폭락": 9, "crash": 9, "대공황": 10, "depression": 10,
    "경기침체": 8, "recession": 8, "금융위기": 9, "crisis": 9,
    "서킷브레이커": 9, "circuit breaker": 9,
    "봉쇄": 8, "blockade": 8, "호르무즈": 9, "hormuz": 9,
    "디폴트": 9, "default": 8, "파산": 7, "bankrupt": 7,
    "관세": 6, "tariff": 6, "제재": 7, "sanction": 7,
    "급락": 7, "plunge": 7, "급등": 6, "surge": 5,
}


@dataclass
class NewsItem:
    """단일 뉴스 헤드라인."""
    title: str
    source: str
    url: str = ""
    published: str = ""
    category: str = ""  # market, geopolitics, economy
    lang: str = "ko"
    impact_score: int = 0  # 시장 영향도 (0-10)
    is_urgent: bool = False
    content_summary: str = ""  # v8.2: 영상 내용 요약 (자막 기반)
    video_id: str = ""  # v8.2: YouTube video ID


def _compute_impact(title: str) -> tuple[int, bool]:
    """헤드라인에서 시장 영향도 점수 계산."""
    title_lower = title.lower()
    max_score = 0
    for kw, score in IMPACT_KEYWORDS.items():
        if kw.lower() in title_lower:
            max_score = max(max_score, score)
    is_urgent = max_score >= 8
    return max_score, is_urgent


def _parse_rss(xml_text: str, feed: dict) -> list[NewsItem]:
    """RSS XML 파싱 → NewsItem 리스트. RSS 2.0 + Atom(YouTube) 지원."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0 또는 Atom
        channel = root.find("channel")
        if channel is not None:
            entries = channel.findall("item")
        else:
            # Atom feed (YouTube 등)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)
            if not entries:
                entries = root.findall("{http://www.w3.org/2005/Atom}entry")
            if not entries:
                entries = root.findall("entry")

        for entry in entries[:10]:
            title = ""
            link = ""
            pub_date = ""

            # Atom namespace 태그 시도 (YouTube Atom 피드용)
            atom_ns = "{http://www.w3.org/2005/Atom}"
            t = entry.find(f"{atom_ns}title")
            if t is None:
                t = entry.find("title")
            if t is not None and t.text:
                title = t.text.strip()

            # Link: Atom은 href 속성, RSS 2.0은 text
            l = entry.find(f"{atom_ns}link")
            if l is None:
                l = entry.find("link")
            if l is not None:
                link = (l.get("href", "") or l.text or "").strip()

            # Published date
            p = entry.find("pubDate")
            if p is not None and p.text:
                pub_date = p.text.strip()
            if not pub_date:
                for tag in [f"{atom_ns}published", f"{atom_ns}updated",
                            "published", "updated"]:
                    p2 = entry.find(tag)
                    if p2 is not None and p2.text:
                        pub_date = p2.text.strip()
                        break

            if not title:
                continue

            impact, urgent = _compute_impact(title)

            # YouTube 피드: 소스명에 🎬 아이콘 추가 + video_id 추출
            source_name = feed["name"]
            is_youtube = "youtube" in feed.get("category", "")
            vid = ""
            if is_youtube:
                source_name = f"🎬{source_name}"
                # YouTube video ID 추출 (yt:videoId 태그 또는 URL에서)
                yt_ns = "{http://www.youtube.com/xml/schemas/2015}"
                vid_el = entry.find(f"{yt_ns}videoId")
                if vid_el is not None and vid_el.text:
                    vid = vid_el.text.strip()
                elif link:
                    vid_match = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', link)
                    if vid_match:
                        vid = vid_match.group(1)

            items.append(NewsItem(
                title=title,
                source=source_name,
                url=link,
                published=pub_date,
                category=feed.get("category", "market"),
                lang=feed.get("lang", "ko"),
                impact_score=impact,
                is_urgent=urgent,
                video_id=vid,
            ))

    except ET.ParseError as e:
        logger.debug("RSS parse error for %s: %s", feed["name"], e)
    except Exception as e:
        logger.debug("RSS processing error for %s: %s", feed["name"], e)

    return items


async def fetch_global_news(
    max_per_feed: int = 5,
    feeds: list[dict] | None = None,
    include_youtube: bool = True,
) -> list[NewsItem]:
    """글로벌 뉴스 RSS 피드에서 헤드라인 수집 (병렬).

    v6.6: YouTube 금융 채널 RSS도 함께 수집.

    Returns:
        NewsItem 리스트 (impact_score 내림차순 정렬)
    """
    import httpx

    target_feeds = feeds or RSS_FEEDS
    if include_youtube and not feeds:
        target_feeds = target_feeds + YOUTUBE_FEEDS
    all_items: list[NewsItem] = []

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        async def _fetch_one(feed: dict) -> list[NewsItem]:
            try:
                resp = await client.get(
                    feed["url"],
                    headers={"User-Agent": "K-Quant/6.0 NewsBot"},
                )
                if resp.status_code == 200:
                    return _parse_rss(resp.text, feed)[:max_per_feed]
            except Exception as e:
                logger.debug("RSS fetch error %s: %s", feed["name"], e)
            return []

        results = await asyncio.gather(
            *[_fetch_one(f) for f in target_feeds],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, list):
                all_items.extend(result)

    # impact_score 내림차순 정렬
    all_items.sort(key=lambda x: (-x.impact_score, x.published), reverse=False)
    return all_items


# v9.5.3: YouTube 경제방송 코멘터리 소스 (속보가 아닌 분석/해설)
# 이 채널들의 영상 제목에 '전쟁' 등 키워드가 있어도 속보가 아님
_YOUTUBE_COMMENTARY_SOURCES = {
    "삼프로TV", "한국경제TV", "SBS Biz", "이데일리TV",
    "MTN머니투데이", "매일경제TV", "토마토증권통",
    "뉴욕주민", "월가아재", "박곰희TV",
    # 추가 채널 방어
    "슈카월드", "신사임당", "월급쟁이부자들",
    "체인지그라운드", "언더스탠딩",
}


def filter_urgent_news(items: list[NewsItem]) -> list[NewsItem]:
    """긴급 뉴스만 필터 (impact_score >= 8).

    v9.5.3: YouTube 경제방송 코멘터리는 제외 (분석 영상 ≠ 뉴스 속보).
    방송에서 '전쟁' 키워드를 분석해도 그건 속보가 아님.
    """
    urgent = []
    for item in items:
        if not item.is_urgent:
            continue
        # YouTube 방송 코멘터리 제외 (🎬 접두사 제거 후 비교)
        raw_source = item.source.lstrip("🎬")
        if item.video_id and raw_source in _YOUTUBE_COMMENTARY_SOURCES:
            continue
        urgent.append(item)
    return urgent


def format_news_for_context(items: list[NewsItem], max_items: int = 8) -> str:
    """AI 컨텍스트용 뉴스 포맷.

    시스템 프롬프트에 주입할 간결한 형식.
    v8.2: YouTube 영상 내용 요약도 포함.
    """
    if not items:
        return "글로벌 이슈 없음"

    lines = []
    seen_titles = set()
    for item in items[:max_items]:
        # 중복 제거 (비슷한 헤드라인)
        title_key = item.title[:20]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        urgency = "🚨" if item.is_urgent else "📰"
        impact = f"[영향:{item.impact_score}/10]" if item.impact_score > 0 else ""
        lines.append(f"{urgency} [{item.source}] {item.title} {impact}")
        if item.content_summary:
            # 컨텍스트에 요약 핵심 150자 포함
            lines.append(f"   → {item.content_summary[:150]}")

    return "\n".join(lines) if lines else "글로벌 이슈 없음"


def format_news_for_telegram(
    items: list[NewsItem], max_items: int = 10, db=None,
) -> str:
    """텔레그램 알림용 뉴스 포맷 (v9.5: YouTube 구조화 분석 표시).

    Args:
        items: NewsItem 리스트
        max_items: 최대 표시 항목 수
        db: SQLiteStore (있으면 youtube_intelligence에서 구조화 데이터 조회)
    """
    if not items:
        return ""

    now = datetime.now(KST)
    lines = [
        f"📰 글로벌 뉴스 브리핑 ({now.strftime('%H:%M')} KST)",
        f"{'━' * 22}",
    ]

    urgent = [i for i in items if i.is_urgent]
    normal = [i for i in items if not i.is_urgent]

    if urgent:
        lines.append("\n🚨 긴급 이슈")
        for item in urgent[:5]:
            lines.append(f"  {item.title}")
            lines.append(f"  — {item.source}")
            if item.content_summary:
                lines.append(f"  📝 {item.content_summary[:200]}")

    # YouTube 구조화 인텔리전스 조회
    yt_intel_map: dict = {}
    if db:
        try:
            yt_intels = db.get_recent_youtube_intelligence(hours=6, limit=10)
            for yi in yt_intels:
                vid = yi.get("video_id", "")
                if vid:
                    yt_intel_map[vid] = yi
        except Exception:
            pass

    # YouTube 요약이 있는 항목 우선 표시
    yt_summarized = [i for i in normal if i.content_summary and i.video_id]
    yt_other = [i for i in normal if not i.content_summary and "🎬" in i.source]
    non_yt = [i for i in normal if "🎬" not in i.source]

    if yt_summarized:
        lines.append("\n🎬 경제방송 분석")
        for item in yt_summarized[:5]:
            channel = item.source.replace("🎬", "").strip()
            intel = yt_intel_map.get(item.video_id or "")

            if intel and intel.get("full_summary"):
                # v9.5: 구조화 인텔리전스 표시
                lines.append(f"\n🎬 [{channel}] 경제방송 분석")
                lines.append(f"{'━' * 22}")
                lines.append(f"📋 {intel['full_summary'][:600]}")

                # 언급 종목
                tickers = intel.get("mentioned_tickers", [])
                if isinstance(tickers, list) and tickers:
                    ticker_parts = []
                    for t in tickers[:6]:
                        name = t.get("name", "")
                        senti = t.get("sentiment", "")
                        emoji = {"긍정": "🟢", "부정": "🔴", "중립": "⚪"}.get(senti, "⚪")
                        ticker_parts.append(f"{name}{emoji}")
                    lines.append(f"📊 언급 종목: {' '.join(ticker_parts)}")

                # 시장 전망
                outlook = intel.get("market_outlook", "")
                if outlook:
                    outlook_emoji = "📈" if "상승" in outlook or "긍정" in outlook else (
                        "📉" if "하락" in outlook or "부정" in outlook else "➡️"
                    )
                    lines.append(f"🔍 시장 전망: {outlook_emoji} {outlook[:80]}")

                # 투자 시사점
                impl = intel.get("investment_implications", "")
                if impl:
                    lines.append(f"💡 {impl[:100]}")
            else:
                # 기존 방식 fallback
                lines.append(f"\n  [{item.source}] {item.title}")
                lines.append(f"  📝 {item.content_summary}")

    remaining = max_items - len(urgent[:5]) - len(yt_summarized[:5])
    rest = yt_other + non_yt
    if rest and remaining > 0:
        lines.append("\n📰 주요 뉴스")
        for item in rest[:remaining]:
            lines.append(f"  {item.title}")
            lines.append(f"  — {item.source}")

    return "\n".join(lines)


def format_urgent_alert(items: list[NewsItem]) -> str:
    """긴급 이벤트 텔레그램 알림 포맷 (레거시 폴백)."""
    if not items:
        return ""

    now = datetime.now(KST)
    lines = [
        f"🚨 긴급 글로벌 이벤트 ({now.strftime('%H:%M')} KST)",
        f"{'━' * 22}",
    ]
    for item in items[:3]:
        impact_bar = "🔴" * min(item.impact_score // 2, 5)
        lines.append(f"\n{impact_bar} {item.title}")
        lines.append(f"  출처: {item.source}")
        lines.append(f"  영향도: {item.impact_score}/10")

    lines.append("\n⚠️ 포트폴리오 리스크 점검을 권장합니다")
    return "\n".join(lines)


# ── v9.5.3: 뉴스 유사도 그룹핑 + AI 분석 ─────────────────

def _normalize_title(title: str) -> str:
    """제목에서 핵심 단어만 추출 (중복 판별용)."""
    # 특수문자, 따옴표, 괄호 등 제거
    t = re.sub(r"[^\w\s]", " ", title)
    # 공백 정리
    return re.sub(r"\s+", " ", t).strip()


def _title_similarity(t1: str, t2: str) -> float:
    """두 제목의 단어 기반 Jaccard 유사도 (0~1)."""
    w1 = set(_normalize_title(t1).split())
    w2 = set(_normalize_title(t2).split())
    if not w1 or not w2:
        return 0.0
    intersection = w1 & w2
    union = w1 | w2
    return len(intersection) / len(union) if union else 0.0


def group_similar_news(items: list[NewsItem], threshold: float = 0.4) -> list[list[NewsItem]]:
    """유사한 뉴스를 그룹으로 묶기.

    같은 이벤트에 대한 여러 헤드라인을 하나로 통합.
    threshold=0.4 → 단어 40% 이상 겹치면 같은 이벤트.
    """
    if not items:
        return []

    groups: list[list[NewsItem]] = []
    used = set()

    for i, item in enumerate(items):
        if i in used:
            continue
        group = [item]
        used.add(i)
        for j, other in enumerate(items):
            if j in used:
                continue
            if _title_similarity(item.title, other.title) >= threshold:
                group.append(other)
                used.add(j)
        groups.append(group)

    return groups


async def analyze_urgent_news(groups: list[list[NewsItem]], db=None) -> str:
    """AI로 긴급 뉴스 그룹을 분석하여 한국 시장 영향 해석.

    각 이벤트 그룹에 대해:
    1. 실제 심각도 재평가 (키워드가 아닌 내용 기반)
    2. 한국 시장/섹터 영향 분석
    3. 보유 종목 영향 (DB 있으면)

    비용: ~$0.002/호출 (Haiku, 최대 3그룹)
    """
    if not groups:
        return ""

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _format_urgent_alert_basic(groups)

    # 보유 종목 정보 가져오기
    holdings_text = ""
    if db:
        try:
            holdings = db.get_active_holdings()
            if holdings:
                names = [h.get("name", "") for h in holdings[:15] if h.get("name")]
                holdings_text = f"\n사용자 보유 종목: {', '.join(names)}"
        except Exception:
            pass

    # 뉴스 그룹 텍스트 구성
    news_text = ""
    for i, group in enumerate(groups[:3]):
        titles = [it.title for it in group]
        sources = list({it.source for it in group})
        max_impact = max(it.impact_score for it in group)
        news_text += f"\n이벤트 {i+1} (키워드 영향도: {max_impact}/10):\n"
        for t in titles[:3]:
            news_text += f"  - {t}\n"
        news_text += f"  출처: {', '.join(sources)}\n"

    prompt = (
        f"다음 긴급 글로벌 뉴스를 분석해줘.\n"
        f"{news_text}\n"
        f"{holdings_text}\n\n"
        "각 이벤트에 대해 다음을 판단해줘:\n"
        "1. 실제 심각도 (1-10): 키워드가 아닌 실제 내용 기반. "
        "'전쟁 추모식 참석'은 전쟁 발발이 아니므로 낮게.\n"
        "2. 한국 증시 영향: 어떤 섹터/종목이 어떻게 영향받는지 구체적으로\n"
        "3. 보유 종목 영향: 있으면 구체적 영향, 없으면 생략\n"
        "4. 투자자 행동 제안: 구체적 (단순히 '점검 권장' 금지)\n\n"
        "형식:\n"
        "이벤트 제목 (한 줄 요약)\n"
        "실제 심각도: X/10\n"
        "시장 영향: (2줄 이내)\n"
        "보유종목: (해당 시 1줄)\n"
        "행동: (1줄)\n\n"
        "규칙:\n"
        "- 한국어, 간결하게\n"
        "- 과장 금지. 추모식/의전 행사는 심각도 2-3\n"
        "- 실제 군사 충돌/경제 위기만 심각도 8+\n"
        "- ** 볼드 금지"
    )

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = response.content[0].text.replace("**", "")

        # 토큰 사용량 기록
        try:
            if db:
                from kstock.core.token_tracker import track_usage
                track_usage(
                    db=db, provider="anthropic",
                    model="claude-haiku-4-5-20251001",
                    function_name="urgent_news_analysis",
                    response=response,
                )
        except Exception:
            pass

        # v9.5.3: 이벤트 → 전략 점수 반영 (학습 엔진 연동)
        try:
            if db:
                await _extract_and_save_event_adjustments(
                    client, analysis, groups, db,
                )
        except Exception as e:
            logger.debug("Event adjustment extraction: %s", e)

        return _format_urgent_alert_rich(groups, analysis)

    except Exception as e:
        logger.warning("Urgent news AI analysis failed: %s", e)
        return _format_urgent_alert_basic(groups)


async def _extract_and_save_event_adjustments(
    client, analysis: str, groups: list[list[NewsItem]], db,
) -> None:
    """AI 분석 결과에서 섹터/종목 점수 조정을 추출하여 DB 저장.

    긴급 뉴스 분석 결과를 구조화하여 scan_engine이 사용할 수 있도록
    event_score_adjustments 테이블에 저장.
    """
    import json as _json

    # 간단한 2차 호출로 구조화 데이터 추출
    extract_prompt = (
        f"다음 뉴스 분석을 JSON으로 구조화해줘.\n\n"
        f"{analysis[:800]}\n\n"
        "JSON 형식 (배열):\n"
        '[{"summary": "이벤트 1줄 요약", '
        '"severity": 1-10, '
        '"affected_sectors": ["반도체", "방산"], '
        '"score_adj": -5~+10, '
        '"duration_hours": 24-72}]\n\n'
        "규칙:\n"
        "- severity 5 이하 = score_adj 0 (무시)\n"
        "- severity 6-7 = score_adj ±3~5\n"
        "- severity 8+ = score_adj ±5~10\n"
        "- 긍정 영향 = 양수, 부정 = 음수\n"
        "- JSON만 출력, 설명 없이"
    )

    try:
        resp2 = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=0.1,
            messages=[{"role": "user", "content": extract_prompt}],
        )
        raw = resp2.content[0].text.strip()

        # JSON 추출
        if "[" in raw:
            raw = raw[raw.index("["):raw.rindex("]") + 1]
        events = _json.loads(raw)

        if not isinstance(events, list):
            return

        from kstock.bot.learning_engine import apply_event_to_strategy
        for evt in events[:3]:
            adj = evt.get("score_adj", 0)
            if adj == 0:
                continue
            await apply_event_to_strategy(
                db=db,
                event_summary=evt.get("summary", ""),
                affected_sectors=evt.get("affected_sectors", []),
                affected_tickers=[],
                adjustment=adj,
                confidence=min(evt.get("severity", 5) / 10, 1.0),
                duration_hours=evt.get("duration_hours", 48),
            )
        logger.info("Event adjustments saved: %d events", len(events))

    except Exception as e:
        logger.debug("Event adjustment extraction failed: %s", e)


def _format_urgent_alert_rich(
    groups: list[list[NewsItem]], analysis: str,
) -> str:
    """AI 분석이 포함된 리치 긴급 알림 포맷."""
    now = datetime.now(KST)
    lines = [
        f"🚨 긴급 글로벌 이벤트 ({now.strftime('%H:%M')} KST)",
        f"{'━' * 22}",
    ]

    # 헤드라인 요약 (그룹별 대표 1개만) + URL 링크
    for group in groups[:3]:
        rep = group[0]
        impact_bar = "🔴" * min(rep.impact_score // 2, 5)
        lines.append(f"\n{impact_bar} {rep.title}")
        if len(group) > 1:
            lines.append(f"  (관련 뉴스 {len(group)}건 통합)")
        sources = list({it.source for it in group})
        lines.append(f"  출처: {', '.join(sources[:2])}")
        # URL 링크 추가
        for it in group[:2]:
            if it.url:
                lines.append(f"  🔗 {it.url}")

    # AI 분석
    lines.append(f"\n{'━' * 22}")
    lines.append("📋 AI 영향 분석")
    lines.append(analysis[:1500])

    return "\n".join(lines)


def _format_urgent_alert_basic(groups: list[list[NewsItem]]) -> str:
    """AI 없이 기본 포맷 (폴백)."""
    now = datetime.now(KST)
    lines = [
        f"🚨 긴급 글로벌 이벤트 ({now.strftime('%H:%M')} KST)",
        f"{'━' * 22}",
    ]
    for group in groups[:3]:
        rep = group[0]
        impact_bar = "🔴" * min(rep.impact_score // 2, 5)
        lines.append(f"\n{impact_bar} {rep.title}")
        if len(group) > 1:
            lines.append(f"  (관련 뉴스 {len(group)}건)")
        lines.append(f"  출처: {', '.join(list({it.source for it in group})[:2])}")
        lines.append(f"  영향도: {rep.impact_score}/10")
        # URL 링크
        for it in group[:2]:
            if it.url:
                lines.append(f"  🔗 {it.url}")

    lines.append("\n⚠️ 포트폴리오 영향 분석은 AI 모드에서 확인하세요")
    return "\n".join(lines)


def make_alert_hash(items: list[NewsItem]) -> str:
    """긴급 뉴스 그룹의 해시 키 생성 (DB 중복 방지용).

    v9.6.3: 대표 기사(첫 번째) 제목 기준 해시 — 그룹 구성 변경 시에도 안정적.
    핵심 명사만 추출하여 정렬 후 해시 → 제목 변형에도 동일 해시.
    """
    import hashlib
    if not items:
        return "empty"
    # 대표 기사(impact 최고) 제목의 핵심 단어만 사용 — 그룹 변경에도 해시 안정
    rep = max(items, key=lambda x: x.impact_score)
    words = _normalize_title(rep.title).split()
    # 2글자 이상 단어만 (조사/접속사 제거)
    key_words = sorted(w for w in words if len(w) >= 2)
    key = "|".join(key_words)
    return hashlib.md5(key.encode()).hexdigest()[:16]


# ── 영문 → 한글 번역 ──────────────────────────────────────

_EN_CHAR_RE = re.compile(r"[a-zA-Z]")


def _is_english(text: str) -> bool:
    """문자열이 영어 위주인지 판단 (알파벳 비율 > 40%)."""
    if not text:
        return False
    alpha = sum(1 for c in text if _EN_CHAR_RE.match(c))
    return alpha / len(text) > 0.4


async def translate_titles_to_korean(items: list[NewsItem]) -> list[NewsItem]:
    """영문 뉴스 제목을 한글로 번역 (Claude Haiku 배치 호출).

    비용: ~$0.001/호출 (Haiku, 50 토큰 × 5개 제목)
    원본 title → title_ko 필드에 저장, title은 원본 유지 안 함 (덮어쓰기).
    번역 실패 시 원본 그대로 반환.
    """
    en_items = [item for item in items if _is_english(item.title)]
    if not en_items:
        return items  # 번역할 영문 제목 없음

    # 번역할 제목 모음
    titles = [item.title for item in en_items]
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))

    prompt = (
        "다음 영문 금융/경제 뉴스 헤드라인을 한국어로 번역해줘.\n"
        "규칙:\n"
        "- 번호 형식 유지 (1. 2. 3. ...)\n"
        "- 금융 전문 용어는 한국 증시에서 쓰는 표현으로\n"
        "- 간결하게 1줄로, 부연설명 없이 번역만\n\n"
        f"{numbered}"
    )

    try:
        import httpx

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.debug("ANTHROPIC_API_KEY not set, skipping translation")
            return items

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
                    "max_tokens": 300,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code != 200:
                logger.debug("Translation API error %d", resp.status_code)
                return items

            result = resp.json()["content"][0]["text"]

        # 번역 결과 파싱: "1. 번역문" 형식
        translated: dict[int, str] = {}
        for line in result.strip().split("\n"):
            line = line.strip()
            m = re.match(r"(\d+)\.\s*(.+)", line)
            if m:
                idx = int(m.group(1)) - 1
                translated[idx] = m.group(2).strip()

        # 번역 적용
        for i, item in enumerate(en_items):
            if i in translated and translated[i]:
                item.title = translated[i]

        logger.info(
            "Translated %d/%d English titles to Korean",
            len(translated), len(en_items),
        )

    except Exception as e:
        logger.debug("Title translation failed: %s", e)

    return items


# ── 위기 감지 엔진 (매크로 선행지표 기반) ──────────────────

@dataclass
class CrisisSignal:
    """위기 감지 결과."""
    is_crisis: bool = False
    severity: int = 0  # 0=정상, 1=주의, 2=경계, 3=위기
    label: str = "정상"
    triggers: list[str] = field(default_factory=list)
    recommended_interval: int = 1800  # 뉴스 수집 간격 (초)


# 기준치: (임계값, 점수, 설명)
_CRISIS_THRESHOLDS = {
    "vix_high": (30.0, 3, "VIX 30+ (공포)"),
    "vix_spike": (25.0, 2, "VIX 25+ (경계)"),
    "vix_change": (10.0, 2, "VIX 일일 변동 10%+"),
    "btc_crash": (-5.0, 2, "BTC 일일 -5% 이상 하락"),
    "btc_plunge": (-10.0, 3, "BTC 일일 -10% 급락"),
    "gold_surge": (3.0, 2, "금 일일 +3% 급등 (안전자산 선호)"),
    "spx_crash": (-2.0, 2, "S&P500 -2% 이상 하락"),
    "spx_plunge": (-4.0, 3, "S&P500 -4% 급락"),
    "krw_spike": (2.0, 2, "원/달러 +2% 급등 (원화 급락)"),
    "fear_extreme": (20.0, 2, "공포탐욕지수 극도공포 (<20)"),
}


def detect_crisis_from_macro(macro_snapshot) -> CrisisSignal:
    """매크로 선행지표 기반 위기 감지.

    VIX, BTC, 금, S&P500, 환율, 공포탐욕지수를 종합하여
    위기 수준 판단 + 뉴스 수집 주기 결정.

    Args:
        macro_snapshot: MacroSnapshot 인스턴스 또는 dict.
    """
    signal = CrisisSignal()
    score = 0

    # 속성 접근 (MacroSnapshot or dict 호환)
    def _get(key: str, default: float = 0.0) -> float:
        if isinstance(macro_snapshot, dict):
            return macro_snapshot.get(key, default)
        return getattr(macro_snapshot, key, default)

    vix = _get("vix", 15.0)
    vix_chg = _get("vix_change_pct", 0.0)
    btc_chg = _get("btc_change_pct", 0.0)
    gold_chg = _get("gold_change_pct", 0.0)
    spx_chg = _get("spx_change_pct", 0.0)
    krw_chg = _get("usdkrw_change_pct", 0.0)
    fear_greed = _get("fear_greed_score", 50.0)

    # VIX 수준
    if vix >= 30:
        score += 3
        signal.triggers.append(f"VIX {vix:.1f} (공포)")
    elif vix >= 25:
        score += 2
        signal.triggers.append(f"VIX {vix:.1f} (경계)")

    # VIX 급변
    if abs(vix_chg) >= 10:
        score += 2
        signal.triggers.append(f"VIX 변동 {vix_chg:+.1f}%")

    # BTC (리스크 자산 선행)
    if btc_chg <= -10:
        score += 3
        signal.triggers.append(f"BTC {btc_chg:+.1f}% 급락")
    elif btc_chg <= -5:
        score += 2
        signal.triggers.append(f"BTC {btc_chg:+.1f}% 하락")

    # 금 (안전자산 급등 = 위기 신호)
    if gold_chg >= 3:
        score += 2
        signal.triggers.append(f"금 {gold_chg:+.1f}% 급등")

    # S&P500
    if spx_chg <= -4:
        score += 3
        signal.triggers.append(f"S&P500 {spx_chg:+.2f}% 급락")
    elif spx_chg <= -2:
        score += 2
        signal.triggers.append(f"S&P500 {spx_chg:+.2f}% 하락")

    # 환율 (원화 급락 = 외자 이탈)
    if krw_chg >= 2:
        score += 2
        signal.triggers.append(f"원/달러 {krw_chg:+.1f}% 급등")

    # 공포탐욕지수
    if fear_greed <= 20:
        score += 2
        signal.triggers.append(f"공포탐욕 {fear_greed:.0f} (극도공포)")

    # 종합 판단
    if score >= 6:
        signal.is_crisis = True
        signal.severity = 3
        signal.label = "위기"
        signal.recommended_interval = 300  # 5분
    elif score >= 4:
        signal.is_crisis = True
        signal.severity = 2
        signal.label = "경계"
        signal.recommended_interval = 600  # 10분
    elif score >= 2:
        signal.severity = 1
        signal.label = "주의"
        signal.recommended_interval = 900  # 15분
    else:
        signal.severity = 0
        signal.label = "정상"
        signal.recommended_interval = 1800  # 30분

    return signal


def format_crisis_alert(signal: CrisisSignal) -> str:
    """위기 감지 텔레그램 알림 포맷."""
    if signal.severity < 2:
        return ""

    now = datetime.now(KST)
    severity_emoji = {1: "🟡", 2: "🟠", 3: "🔴"}
    emoji = severity_emoji.get(signal.severity, "⚪")

    lines = [
        f"{emoji} 글로벌 위기 감지 — {signal.label}",
        f"{'━' * 22}",
        f"시간: {now.strftime('%H:%M')} KST",
        "",
    ]
    for trigger in signal.triggers:
        lines.append(f"  ⚠️ {trigger}")

    lines.append(f"\n뉴스 감시 주기: {signal.recommended_interval // 60}분으로 강화")
    if signal.severity >= 3:
        lines.append("\n🚨 포트폴리오 긴급 점검을 권장합니다")

    return "\n".join(lines)


# ── YouTube 영상 자막 추출 + AI 내용 요약 (v8.2) ──────────

def _extract_video_id(url: str) -> str:
    """YouTube URL에서 video_id 추출."""
    m = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else ""


def fetch_transcript(video_id: str, max_chars: int = 8000) -> str:
    """YouTube 자막(transcript) 추출.

    한국어 자막 우선, 영어 순으로 시도.
    youtube_transcript_api v1.2+ 인스턴스 API 사용.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()

        # 한국어 → 영어 순서로 시도
        for lang in [["ko"], ["en"]]:
            try:
                result = api.fetch(video_id, languages=lang)
                texts = [seg.text for seg in result if hasattr(seg, "text")]
                if texts:
                    full_text = " ".join(texts)
                    return full_text[:max_chars]
            except Exception:
                continue

        return ""

    except Exception as e:
        logger.debug("Transcript fetch failed for %s: %s", video_id, e)
        return ""


async def summarize_transcript_structured(
    transcript: str,
    title: str,
    source: str,
) -> dict:
    """AI로 자막 내용을 구조화 추출 (v9.5).

    비용: ~$0.003/호출 (Haiku, ~3000 토큰 입력 + 800 출력)

    Returns:
        dict with keys: full_summary, mentioned_tickers, mentioned_sectors,
        market_outlook, key_numbers, investment_implications, raw_summary, confidence
    """
    empty = {
        "full_summary": "", "mentioned_tickers": [], "mentioned_sectors": [],
        "market_outlook": "", "key_numbers": [], "investment_implications": "",
        "raw_summary": "", "confidence": 0.0,
    }
    if not transcript or len(transcript) < 50:
        return empty

    import httpx
    import json as _json

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return empty

    text = transcript[:5000]

    prompt = (
        f"다음은 [{source}]의 경제/투자 유튜브 영상 '{title}'의 자막입니다.\n\n"
        f"{text}\n\n"
        "아래 JSON 형식으로 정확히 응답해줘. JSON만 출력하고 다른 텍스트는 넣지 마.\n"
        '{\n'
        '  "full_summary": "10-15줄 상세 요약 (시장 상황, 전문가 의견, 핵심 수치, 전망 포함. 불필요한 인사/광고 제외)",\n'
        '  "mentioned_tickers": [\n'
        '    {"name": "종목명", "ticker": "6자리코드(모르면 빈 문자열)", "sentiment": "긍정/부정/중립", "context": "왜 언급됐는지 1줄"}\n'
        '  ],\n'
        '  "mentioned_sectors": ["반도체", "2차전지"],\n'
        '  "market_outlook": "bullish/bearish/neutral/mixed",\n'
        '  "key_numbers": [\n'
        '    {"label": "지표명", "value": "숫자값", "unit": "단위"}\n'
        '  ],\n'
        '  "investment_implications": "투자자가 오늘 취할 액션 2-3줄"\n'
        '}\n'
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 800,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "Structured summary API error %d for '%s': %s",
                    resp.status_code, title[:30],
                    resp.text[:200] if resp.text else "no body",
                )
                return empty

            raw_text = resp.json()["content"][0]["text"].strip()
            # 토큰 추적
            try:
                from kstock.core.token_tracker import track_usage_global
                track_usage_global(
                    provider="anthropic",
                    model="claude-haiku-4-5-20251001",
                    function_name="youtube_summary_structured",
                    response=resp,
                )
            except Exception:
                pass

            # JSON 파싱 (코드블록 제거 후)
            json_text = raw_text
            if "```" in json_text:
                import re
                m = re.search(r"```(?:json)?\s*(.*?)```", json_text, re.DOTALL)
                if m:
                    json_text = m.group(1)
            if json_text.startswith("{"):
                try:
                    data = _json.loads(json_text)
                except _json.JSONDecodeError:
                    # 부분 JSON 복구 시도
                    data = {}
            else:
                data = {}

            if not data:
                # JSON 파싱 실패 → raw text를 단문 요약으로 사용
                lines = raw_text.split("\n")
                short = "\n".join(lines[:5])
                return {**empty, "raw_summary": short, "full_summary": raw_text}

            full_summary = data.get("full_summary", "")
            # raw_summary: 3줄 버전 (backward compat)
            summary_lines = full_summary.split("\n")
            raw_summary = "\n".join(summary_lines[:3]) if summary_lines else full_summary[:200]

            conf = 0.5
            if data.get("mentioned_tickers"):
                conf += 0.2
            if data.get("mentioned_sectors"):
                conf += 0.1
            if data.get("key_numbers"):
                conf += 0.1
            if data.get("investment_implications"):
                conf += 0.1

            # v9.5.1: 타입 검증 — AI가 string 반환 시 빈 리스트로 대체
            mentioned_tickers = data.get("mentioned_tickers", [])
            if not isinstance(mentioned_tickers, list):
                mentioned_tickers = []
            mentioned_sectors = data.get("mentioned_sectors", [])
            if not isinstance(mentioned_sectors, list):
                mentioned_sectors = []
            key_numbers = data.get("key_numbers", [])
            if not isinstance(key_numbers, list):
                key_numbers = []

            return {
                "full_summary": full_summary,
                "mentioned_tickers": mentioned_tickers,
                "mentioned_sectors": mentioned_sectors,
                "market_outlook": data.get("market_outlook", ""),
                "key_numbers": key_numbers,
                "investment_implications": data.get("investment_implications", ""),
                "raw_summary": raw_summary,
                "confidence": min(conf, 1.0),
            }

    except Exception as e:
        logger.warning(
            "Structured summary generation failed for '%s': %s",
            title[:30] if title else "unknown", e, exc_info=True,
        )
        return empty


async def summarize_transcript(
    transcript: str,
    title: str,
    source: str,
) -> str:
    """AI로 자막 내용을 경제/투자 관점으로 요약 (backward compat wrapper).

    v9.5: 내부적으로 구조화 추출 후 raw_summary 반환.
    """
    result = await summarize_transcript_structured(transcript, title, source)
    return result.get("raw_summary", "") or result.get("full_summary", "")[:200]


async def enrich_youtube_summaries(
    items: list[NewsItem],
    max_summaries: int = 15,
    db=None,
) -> list[NewsItem]:
    """YouTube 영상 뉴스에 자막 기반 내용 요약을 추가.

    v9.5: 구조화 추출 후 DB에 인텔리전스 저장.
    v10.0: max_summaries 5→15, DB 중복 스킵, 자막 없을 때 제목+설명 폴백.

    Args:
        items: NewsItem 리스트 (YouTube video_id가 있는 항목)
        max_summaries: 요약할 최대 영상 수 (비용 제어)
        db: SQLiteStore instance (있으면 youtube_intelligence 저장)
    """
    yt_items = [it for it in items if it.video_id]
    if not yt_items:
        return items

    count = 0
    skipped = 0
    for item in yt_items:
        if count >= max_summaries:
            break

        # v10.0: DB 중복 스킵 — 이미 처리한 영상은 건너뛰기
        if db:
            try:
                if db.check_youtube_processed(item.video_id):
                    skipped += 1
                    continue
            except Exception:
                logger.debug("check_youtube_processed failed for %s", item.video_id, exc_info=True)

        # 자막 추출 (동기 — youtube_transcript_api는 동기 라이브러리)
        transcript = await asyncio.get_event_loop().run_in_executor(
            None, fetch_transcript, item.video_id,
        )

        # v10.0: 자막 없을 때 제목+설명 폴백
        if not transcript:
            fallback_text = f"제목: {item.title}\n설명: {item.content_summary[:500]}" if item.content_summary else ""
            if len(fallback_text) > 50:
                logger.info("YouTube transcript unavailable, using title+desc fallback: %s", item.title[:30])
                structured = await summarize_transcript_structured(
                    fallback_text, item.title, item.source.replace("\U0001f3ac", "").strip(),
                )
            else:
                continue
        else:
            # v9.5: 구조화 추출
            structured = await summarize_transcript_structured(
                transcript, item.title, item.source.replace("\U0001f3ac", "").strip(),
            )

        raw_summary = structured.get("raw_summary", "")
        full_summary = structured.get("full_summary", "")

        if raw_summary or full_summary:
            item.content_summary = raw_summary or full_summary[:200]
            count += 1
            logger.info(
                "YouTube summary: [%s] %s (%d chars, %d tickers)",
                item.source, item.title[:30], len(full_summary),
                len(structured.get("mentioned_tickers", [])),
            )

            # DB에 인텔리전스 저장
            if db:
                try:
                    db.save_youtube_intelligence({
                        "video_id": item.video_id,
                        "source": item.source,
                        "title": item.title,
                        "mentioned_tickers": structured.get("mentioned_tickers", []),
                        "mentioned_sectors": structured.get("mentioned_sectors", []),
                        "market_outlook": structured.get("market_outlook", ""),
                        "key_numbers": structured.get("key_numbers", []),
                        "investment_implications": structured.get("investment_implications", ""),
                        "full_summary": full_summary,
                        "raw_summary": raw_summary,
                        "confidence": structured.get("confidence", 0.0),
                    })
                except Exception:
                    logger.debug("YouTube intelligence DB save failed", exc_info=True)

    logger.info(
        "Enriched %d/%d YouTube items with summaries (skipped %d already processed)",
        count, len(yt_items), skipped,
    )
    return items
