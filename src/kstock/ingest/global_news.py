"""글로벌 뉴스 수집기 — RSS 기반 실시간 헤드라인 수집.

지정학 리스크, 매크로 이벤트, 시장 급변 뉴스를 자동 수집하여
AI 컨텍스트와 브리핑에 반영한다.

v6.0: 초기 버전 — RSS 피드 기반
v6.1: 위기 감지 + 매크로 선행지표 연동 + 적응형 빈도
v6.2.2: 영문 뉴스 제목 한글 번역 자동화
v8.2: YouTube 경제방송 확대 (10채널) + 자막 기반 내용 요약
v10.4: Whisper 자막 추출 + 심화 YouTube 학습 파이프라인
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
    # v12.2: 해외 증권사/투자은행/매크로 뉴스 강화
    {
        "name": "Bloomberg Markets",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "lang": "en",
        "category": "market",
    },
    {
        "name": "FT Markets",
        "url": "https://www.ft.com/markets?format=rss",
        "lang": "en",
        "category": "market",
    },
    {
        "name": "CNBC Economy",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
        "lang": "en",
        "category": "economy",
    },
    {
        "name": "CNBC Energy",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768",
        "lang": "en",
        "category": "economy",
    },
    {
        "name": "MarketWatch",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "lang": "en",
        "category": "market",
    },
    # 한국 경제 추가 (유가/환율/정책 비중 높은 매체)
    {
        "name": "한경 국제경제",
        "url": "https://www.hankyung.com/feed/international",
        "lang": "ko",
        "category": "geopolitics",
    },
    {
        "name": "연합인포맥스 증권",
        "url": "https://news.einfomax.co.kr/rss/S1N10.xml",
        "lang": "ko",
        "category": "market",
    },
    {
        "name": "연합인포맥스 경제",
        "url": "https://news.einfomax.co.kr/rss/S1N2.xml",
        "lang": "ko",
        "category": "economy",
    },
    {
        "name": "WSJ Markets",
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "lang": "en",
        "category": "market",
    },
]

# ── 유튜브 경제방송 채널 RSS 피드 (v8.2 확대) ──────────────
# ── v11.0: 추적 애널리스트 20명 ───────────────────────────────────────────────
TRACKED_ANALYSTS: dict[str, str] = {
    "이선엽": "신한투자증권", "김선우": "메리츠증권",
    "이은택": "KB증권", "이재만": "하나증권",
    "박석중": "신한투자증권", "김록호": "하나증권",
    "박유악": "키움증권", "성종화": "이베스트투자증권",
    "이문종": "신한투자증권", "김동희": "메리츠증권",
    "강동진": "현대차증권", "김현수": "하나증권",
    "정원석": "하이투자증권", "김진우": "한국투자증권",
    "최광식": "다올투자증권", "이달미": "SK증권",
    "이승규": "한국바이오협회", "조진표": "투자전문가",
    "이영훈": "투자이사",
}

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
    # ── v11.0: 시황 전문 ──
    {
        "name": "증시각도기TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCdOjVxkj5JA0iDu3_xcsTyQ",
        "lang": "ko",
        "category": "youtube_finance",
    },
    {
        "name": "주식단테",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC6ij59Gy_HnqO4pFu9A_zgQ",
        "lang": "ko",
        "category": "youtube_finance",
    },
    {
        "name": "시윤주식",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCv-spDeZBGYVUI9eGXGaLSg",
        "lang": "ko",
        "category": "youtube_finance",
    },
    {
        "name": "경제원탑",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDjaj6eENGMvgIXfeRErPmA",
        "lang": "ko",
        "category": "youtube_finance",
    },
    # ── v11.0: 증권사 공식 ──
    {
        "name": "키움증권 채널K",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCZW1d7B2nYqQUiTiOnkirrQ",
        "lang": "ko",
        "category": "youtube_broker",
    },
    {
        "name": "삼성증권 POP",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCq7h8qFlHN5FL_T6waKZllw",
        "lang": "ko",
        "category": "youtube_broker",
    },
    {
        "name": "KB증권 깨비마블TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCD0k4Kq7SJROxxV-9N5v8IA",
        "lang": "ko",
        "category": "youtube_broker",
    },
    {
        "name": "한국투자증권 BanKIS",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCU6f21g_qaJk6rkX-IF6X2g",
        "lang": "ko",
        "category": "youtube_broker",
    },
    {
        "name": "하나증권 하나TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCP8KMauNQ5YhTL8fzoSYcfQ",
        "lang": "ko",
        "category": "youtube_broker",
    },
    # ── v11.0: 매크로/투자 ──
    {
        "name": "부읽남",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC2QeHNJFfuQWB4cy3M-745g",
        "lang": "ko",
        "category": "youtube_finance",
    },
    {
        "name": "신사임당",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCaJdckl6MBdDPDf75Ec_bJA",
        "lang": "ko",
        "category": "youtube_finance",
    },
    # ── v11.0: 뉴스/미국 ──
    {
        "name": "연합뉴스경제TV",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC_xNTnsFIpKUhM8sWCQN6Zw",
        "lang": "ko",
        "category": "youtube_news",
    },
    {
        "name": "오선의 미국 증시 라이브",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC_JJ_NhRqPKcIOj5Ko3W_3w",
        "lang": "ko",
        "category": "youtube_us_market",
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
    "유가 급등", "유가 폭등", "유가 급락", "호르무즈", "OPEC 감산",
    "환율 급등", "원달러 급등", "달러 급등",
]

URGENT_KEYWORDS_EN = [
    "war", "strike", "bomb", "missile", "nuclear", "sanction", "blockade",
    "recession", "depression", "crisis", "default", "bankrupt",
    "crash", "plunge", "circuit breaker", "panic", "black monday",
    "emergency", "martial law", "coup", "terror",
    "rate hike", "rate cut", "QE", "QT", "FOMC",
    "tariff", "trade war", "export ban",
    "oil surge", "oil spike", "oil crash", "hormuz", "OPEC",
    "dollar surge", "FX intervention",
]

# 시장 영향도 키워드 (가중치)
IMPACT_KEYWORDS = {
    # 지정학/전쟁
    "전쟁": 10, "war": 10, "공습": 9, "strike": 8,
    "핵": 10, "nuclear": 10, "미사일": 8, "missile": 8,
    "봉쇄": 8, "blockade": 8, "호르무즈": 9, "hormuz": 9,
    "제재": 7, "sanction": 7,
    # 경제위기
    "폭락": 9, "crash": 9, "대공황": 10, "depression": 10,
    "경기침체": 8, "recession": 8, "금융위기": 9, "crisis": 9,
    "서킷브레이커": 9, "circuit breaker": 9,
    "디폴트": 9, "default": 8, "파산": 7, "bankrupt": 7,
    "급락": 7, "plunge": 7, "급등": 6, "surge": 5,
    # 관세/무역
    "관세": 6, "tariff": 6,
    # v12.2: 유가/환율/정책 (매매 직결 지표)
    "유가 급등": 7, "유가 폭등": 8, "유가 급락": 7, "oil spike": 7,
    "OPEC": 6, "감산": 7, "증산": 6, "원유": 5,
    "환율": 5, "원달러": 6, "달러 급등": 7, "원화 약세": 6,
    "금리 인상": 7, "금리 인하": 7, "rate hike": 7, "rate cut": 7,
    "FOMC": 6, "연준": 6, "Fed": 5, "기준금리": 6,
    "양적긴축": 7, "양적완화": 7, "QT": 6, "QE": 6,
    "CPI": 5, "고용": 5, "실업률": 5, "PCE": 5,
    # 해외 증권사/투자은행 리포트 키워드
    "upgrade": 5, "downgrade": 6, "목표가": 5, "투자의견": 5,
    "Goldman Sachs": 5, "JP Morgan": 5, "Morgan Stanley": 5,
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
    hours_lookback: int = 0,
) -> list[NewsItem]:
    """글로벌 뉴스 RSS 피드에서 헤드라인 수집 (병렬).

    v6.6: YouTube 금융 채널 RSS도 함께 수집.
    v12.1: hours_lookback > 0이면 해당 시간 이내 뉴스만 반환.

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

    # v12.1: hours_lookback 시간 필터링
    if hours_lookback > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lookback)
        filtered = []
        for item in all_items:
            try:
                pub = item.published
                if isinstance(pub, str):
                    # 다양한 날짜 포맷 시도
                    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%a, %d %b %Y %H:%M:%S %z",
                                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                        try:
                            pub = datetime.strptime(pub, fmt)
                            break
                        except ValueError:
                            continue
                if hasattr(pub, "timestamp"):
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub >= cutoff:
                        filtered.append(item)
                else:
                    filtered.append(item)  # 날짜 파싱 실패 시 포함
            except Exception:
                filtered.append(item)
        all_items = filtered

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
    # v11.0 신규 채널
    "증시각도기TV", "주식단테", "시윤주식", "경제원탑",
    "키움증권 채널K", "삼성증권 POP", "KB증권 깨비마블TV",
    "한국투자증권 BanKIS", "하나증권 하나TV",
    "부읽남", "연합뉴스경제TV", "오선의 미국 증시 라이브",
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

# v12.2: 주제 키워드 클러스터 — 같은 클러스터 내 키워드가 겹치면 동일 주제로 묶음
_TOPIC_CLUSTERS = [
    {"이란", "iran", "공습", "전쟁", "war", "미사일", "missile", "중동", "호르무즈", "hormuz", "테헤란"},
    {"러시아", "우크라이나", "russia", "ukraine", "NATO", "나토"},
    {"북한", "미사일", "핵", "nuclear", "north korea", "ICBM"},
    {"관세", "tariff", "무역전쟁", "trade war", "수출규제", "export ban"},
    {"금리", "rate", "FOMC", "연준", "Fed", "기준금리", "인상", "인하"},
    {"유가", "oil", "OPEC", "원유", "WTI", "Brent", "석유"},
]


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


def _same_topic_cluster(t1: str, t2: str) -> bool:
    """두 제목이 같은 주제 클러스터에 속하는지 확인.

    '이란 전쟁 확대'와 '미국 공습 이란'처럼 단어가 달라도
    같은 주제 클러스터 키워드가 2개 이상 겹치면 True.
    """
    combined1 = t1.lower()
    combined2 = t2.lower()
    for cluster in _TOPIC_CLUSTERS:
        hits1 = sum(1 for kw in cluster if kw.lower() in combined1)
        hits2 = sum(1 for kw in cluster if kw.lower() in combined2)
        if hits1 >= 1 and hits2 >= 1:
            return True
    return False


def group_similar_news(items: list[NewsItem], threshold: float = 0.4) -> list[list[NewsItem]]:
    """유사한 뉴스를 그룹으로 묶기.

    같은 이벤트에 대한 여러 헤드라인을 하나로 통합.
    v12.2: Jaccard 유사도 + 주제 클러스터 기반 이중 그룹핑.
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
            # Jaccard 유사도 OR 같은 주제 클러스터면 그룹핑
            if (_title_similarity(item.title, other.title) >= threshold
                    or _same_topic_cluster(item.title, other.title)):
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

    한국어 수동 → 한국어 자동생성 → 영어 수동 → 영어 자동생성 순으로 시도.
    youtube_transcript_api v1.2+ 인스턴스 API 사용.

    v10.4: 자동생성 자막(auto-generated) 명시적 시도 추가.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()

        # 1단계: 수동 자막 (ko → en)
        for lang in [["ko"], ["en"]]:
            try:
                result = api.fetch(video_id, languages=lang)
                texts = [seg.text for seg in result if hasattr(seg, "text")]
                if texts:
                    full_text = " ".join(texts)
                    return full_text[:max_chars]
            except Exception:
                continue

        # 2단계: 자동생성 자막 리스트 조회 후 시도
        try:
            transcript_list = api.list(video_id)
            # 자동생성 한국어 → 영어 순
            for lang_code in ["ko", "en"]:
                for t in transcript_list:
                    if hasattr(t, "language_code") and t.language_code == lang_code:
                        try:
                            result = t.fetch()
                            texts = [seg.text for seg in result if hasattr(seg, "text")]
                            if texts:
                                full_text = " ".join(texts)
                                logger.info("Auto-caption found for %s (lang=%s, %d chars)",
                                            video_id, lang_code, len(full_text))
                                return full_text[:max_chars]
                        except Exception:
                            continue
        except Exception:
            pass

        return ""

    except Exception as e:
        logger.debug("Transcript fetch failed for %s: %s", video_id, e)
        return ""


def fetch_video_metadata(video_id: str) -> dict:
    """YouTube 영상 메타데이터 추출 (제목, 설명, 채널, 해시태그).

    oEmbed API + 페이지 메타태그에서 수집.
    자막 없는 영상의 폴백 분석에 사용.
    """
    import urllib.request

    meta = {"title": "", "description": "", "channel": "", "hashtags": []}

    try:
        # oEmbed: 제목 + 채널명
        oembed_url = (
            f"https://www.youtube.com/oembed?"
            f"url=https://www.youtube.com/watch?v={video_id}&format=json"
        )
        import json as _json
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
            meta["title"] = data.get("title", "")
            meta["channel"] = data.get("author_name", "")
    except Exception:
        pass

    try:
        # 페이지 메타태그: 설명 + 해시태그
        page_url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(page_url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ko",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")[:50000]

        # og:description (가장 풍부한 설명)
        m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if m:
            meta["description"] = m.group(1)

        # 해시태그 추출
        tags = re.findall(r"#(\w+)", meta.get("description", "") + " " + meta.get("title", ""))
        meta["hashtags"] = list(dict.fromkeys(tags))[:15]

    except Exception:
        pass

    return meta


def fetch_transcript_whisper(video_id: str, max_chars: int = 8000) -> str:
    """YouTube 음성을 Whisper API로 텍스트 변환 (자막 없는 영상용).

    yt-dlp으로 오디오 추출 → OpenAI Whisper API로 전사.
    비용: ~$0.006/분, 10분 영상 ≈ $0.06

    Returns:
        전사된 텍스트 (max_chars까지), 실패시 빈 문자열
    """
    import tempfile
    import subprocess

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        logger.debug("Whisper: OPENAI_API_KEY not set")
        return ""

    tmp_dir = tempfile.mkdtemp(prefix="kq_whisper_")
    audio_path = os.path.join(tmp_dir, f"{video_id}.m4a")

    try:
        # yt-dlp: 오디오 포함 영상 다운로드 (ffmpeg 없이도 동작)
        url = f"https://www.youtube.com/watch?v={video_id}"

        # yt-dlp PATH 설정
        env = os.environ.copy()
        yt_dlp_dir = os.path.expanduser("~/Library/Python/3.9/bin")
        if yt_dlp_dir not in env.get("PATH", ""):
            env["PATH"] = yt_dlp_dir + ":" + env.get("PATH", "")

        # ffmpeg 유무에 따라 명령어 결정
        has_ffmpeg = bool(subprocess.run(
            ["which", "ffmpeg"], capture_output=True, env=env,
        ).returncode == 0)

        if has_ffmpeg:
            cmd = [
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "m4a",
                "--audio-quality", "9",
                "--max-filesize", "25m",
                "--download-sections", "*0:00-15:00",
                "--no-playlist", "--quiet",
                "-o", audio_path,
                url,
            ]
        else:
            # ffmpeg 없으면: 오디오+비디오 합본(format 18)을 그대로 다운
            # Whisper API는 mp4도 지원
            audio_path = audio_path.replace(".m4a", ".mp4")
            cmd = [
                "yt-dlp",
                "-f", "18",  # 360p mp4 (audio+video, ~5MB/min)
                "--max-filesize", "25m",
                "--no-playlist", "--quiet",
                "-o", audio_path,
                url,
            ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, env=env,
        )

        # yt-dlp은 실제 확장자를 바꿀 수 있음
        actual_path = audio_path
        if not os.path.exists(audio_path):
            # .m4a.m4a 등 yt-dlp 확장자 중복 체크
            for ext in [".m4a", ".webm", ".opus", ".mp3"]:
                candidate = audio_path.replace(".m4a", ext)
                if os.path.exists(candidate):
                    actual_path = candidate
                    break
            else:
                # glob으로 tmp_dir 내 파일 찾기
                import glob as _glob
                files = _glob.glob(os.path.join(tmp_dir, f"{video_id}*"))
                if files:
                    actual_path = files[0]
                else:
                    logger.info("Whisper: yt-dlp no output for %s: %s", video_id, result.stderr[:200])
                    return ""

        file_size = os.path.getsize(actual_path)
        if file_size > 25 * 1024 * 1024:
            logger.info("Whisper: audio too large (%d MB) for %s", file_size // (1024*1024), video_id)
            return ""

        logger.info("Whisper: transcribing %s (%.1f MB)", video_id, file_size / (1024*1024))

        # OpenAI Whisper API 호출
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)

        with open(actual_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ko",
                response_format="text",
            )

        transcript = str(resp).strip()
        logger.info("Whisper: %s → %d chars", video_id, len(transcript))
        return transcript[:max_chars] if transcript else ""

    except subprocess.TimeoutExpired:
        logger.info("Whisper: yt-dlp timeout for %s", video_id)
        return ""
    except Exception as e:
        logger.info("Whisper transcription failed for %s: %s", video_id, e)
        return ""
    finally:
        # 임시 파일 정리
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


async def deep_analyze_youtube(
    video_id: str,
    title: str,
    source: str,
    db=None,
) -> dict:
    """YouTube 영상 심화 분석 — 자막 > Whisper > 제목/설명 폴백 체인.

    v10.4: 자막이 없는 영상도 Whisper로 음성 전사하여 분석.

    Returns:
        structured dict (summarize_transcript_structured 형식)
    """
    loop = asyncio.get_event_loop()

    # 1단계: 기존 자막 시도
    transcript = await loop.run_in_executor(None, fetch_transcript, video_id)

    # 2단계: 자막 없으면 Whisper API
    whisper_used = False
    if not transcript:
        logger.info("Deep YouTube: no subtitle for %s, trying Whisper", video_id)
        transcript = await loop.run_in_executor(None, fetch_transcript_whisper, video_id)
        if transcript:
            whisper_used = True
            logger.info("Deep YouTube: Whisper success for %s (%d chars)", video_id, len(transcript))

    # 3단계: Whisper도 실패하면 메타데이터 기반 풍부한 폴백
    if not transcript:
        logger.info("Deep YouTube: all transcript methods failed for %s, using metadata fallback", video_id)
        meta = await loop.run_in_executor(None, fetch_video_metadata, video_id)
        parts = [f"채널: {meta.get('channel', source)}"]
        parts.append(f"제목: {meta.get('title', '') or title}")
        if meta.get("description"):
            parts.append(f"설명: {meta['description'][:1000]}")
        if meta.get("hashtags"):
            parts.append(f"해시태그: {', '.join(meta['hashtags'][:10])}")
        transcript = "\n".join(parts)

    # AI 구조화 분석
    structured = await summarize_transcript_structured(transcript, title, source)

    # 메타데이터 추가
    structured["transcript_method"] = (
        "whisper" if whisper_used else ("subtitle" if len(transcript) > 100 else "title_fallback")
    )
    structured["video_id"] = video_id

    # DB 저장 (intelligence + learning event)
    if db and (structured.get("full_summary") or structured.get("raw_summary")):
        try:
            db.save_youtube_intelligence({
                "video_id": video_id,
                "source": source,
                "title": title,
                "mentioned_tickers": structured.get("mentioned_tickers", []),
                "mentioned_sectors": structured.get("mentioned_sectors", []),
                "market_outlook": structured.get("market_outlook", ""),
                "key_numbers": structured.get("key_numbers", []),
                "investment_implications": structured.get("investment_implications", ""),
                "full_summary": structured.get("full_summary", ""),
                "raw_summary": structured.get("raw_summary", ""),
                "confidence": structured.get("confidence", 0.0),
            })
        except Exception:
            logger.debug("Deep YouTube: intelligence save failed", exc_info=True)

        # 학습 이력 기록
        try:
            import json as _json
            db.save_learning_event(
                event_type="youtube_deep_analysis",
                description=f"[{source}] {title[:60]}",
                data_json=_json.dumps({
                    "video_id": video_id,
                    "method": structured["transcript_method"],
                    "sectors": structured.get("mentioned_sectors", []),
                    "outlook": structured.get("market_outlook", ""),
                    "tickers_count": len(structured.get("mentioned_tickers", [])),
                }, ensure_ascii=False),
                impact_summary=structured.get("investment_implications", "")[:200],
            )
        except Exception:
            logger.debug("Deep YouTube: learning event save failed", exc_info=True)

    return structured


async def batch_deep_youtube_analysis(
    db=None,
    max_videos: int = 10,
    hours_lookback: int = 24,
) -> list[dict]:
    """최근 YouTube 영상들을 배치로 심화 분석.

    스케줄러에서 호출. 이미 처리된 영상은 스킵.

    Returns:
        분석 결과 리스트
    """
    from kstock.ingest.global_news import fetch_global_news

    # YouTube 영상만 수집
    items = await fetch_global_news(max_per_feed=10)
    yt_items = [it for it in items if it.video_id]

    if not yt_items:
        logger.info("batch_deep_youtube: no YouTube items found")
        return []

    results = []
    processed = 0
    skipped = 0

    for item in yt_items:
        if processed >= max_videos:
            break

        # 중복 스킵
        if db:
            try:
                if db.check_youtube_processed(item.video_id):
                    skipped += 1
                    continue
            except Exception:
                pass

        structured = await deep_analyze_youtube(
            video_id=item.video_id,
            title=item.title,
            source=item.source.replace("\U0001f3ac", "").strip(),
            db=db,
        )

        if structured.get("full_summary") or structured.get("raw_summary"):
            results.append(structured)
            processed += 1

        # API 레이트 리밋 방지
        await asyncio.sleep(2)

    logger.info(
        "batch_deep_youtube: analyzed %d videos, skipped %d (of %d total)",
        processed, skipped, len(yt_items),
    )
    return results


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

    # v10.4: 자막 vs 메타데이터 폴백 감지 → 프롬프트 분기
    is_metadata_only = text.startswith("채널:") or text.startswith("제목:")

    if is_metadata_only:
        prompt = (
            f"다음은 [{source}]의 경제/투자 유튜브 영상 정보입니다 (자막 미제공, 메타데이터 기반).\n\n"
            f"{text}\n\n"
            "위 제목, 설명, 해시태그를 분석하여 이 영상이 다루는 투자 관련 주제를 추론해줘.\n"
            "한국 증시 전문가 관점에서 제목과 키워드로부터 최대한 투자 인사이트를 추출해.\n"
            "아래 JSON 형식으로 정확히 응답해줘. JSON만 출력하고 다른 텍스트는 넣지 마.\n"
            '{\n'
            '  "full_summary": "제목/설명/해시태그 기반 3-5줄 추론 요약 (이 영상이 다루는 시장 주제, 관련 종목, 투자 시사점 추론)",\n'
            '  "mentioned_tickers": [\n'
            '    {"name": "종목명", "ticker": "6자리코드(모르면 빈 문자열)", "sentiment": "긍정/부정/중립", "context": "왜 관련 있는지 1줄"}\n'
            '  ],\n'
            '  "mentioned_sectors": ["반도체", "2차전지"],\n'
            '  "market_outlook": "bullish/bearish/neutral/mixed",\n'
            '  "key_numbers": [],\n'
            '  "investment_implications": "투자자 관점에서의 시사점 1-2줄"\n'
            '}\n'
        )
    else:
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


async def youtube_weekly_synthesis(db=None, ai_router=None) -> dict:
    """7일간 YouTube 인텔리전스를 Gemini로 종합 합성.

    NotebookLM 대체: 크로스 영상 패턴, 합의/갈등 의견, 핵심 인사이트 추출.

    Returns:
        dict with keys: synthesis, consensus_themes, contrarian_views,
        top_tickers, top_sectors, market_consensus, created_at
    """
    import json as _json
    from datetime import datetime

    empty = {
        "synthesis": "", "consensus_themes": [], "contrarian_views": [],
        "top_tickers": [], "top_sectors": [], "market_consensus": "",
        "created_at": datetime.now().isoformat(),
    }

    if not db:
        logger.warning("youtube_weekly_synthesis: no DB provided")
        return empty

    # 7일간 YouTube 인텔리전스 수집
    intel = db.get_recent_youtube_intelligence(hours=168, limit=100)
    if not intel or len(intel) < 3:
        logger.info("youtube_weekly_synthesis: insufficient data (%d items)", len(intel))
        empty["synthesis"] = f"YouTube 인텔리전스 부족 ({len(intel)}건). 최소 3건 필요."
        return empty

    # 인텔리전스를 텍스트로 조합
    intel_texts = []
    for i, item in enumerate(intel, 1):
        parts = [f"[영상 {i}] 채널: {item.get('source', '?')} | 제목: {item.get('title', '?')}"]
        if item.get("mentioned_tickers"):
            tickers = item["mentioned_tickers"]
            if isinstance(tickers, str):
                try:
                    tickers = _json.loads(tickers)
                except Exception:
                    tickers = [tickers]
            parts.append(f"  언급 종목: {', '.join(str(t) for t in tickers)}")
        if item.get("mentioned_sectors"):
            sectors = item["mentioned_sectors"]
            if isinstance(sectors, str):
                try:
                    sectors = _json.loads(sectors)
                except Exception:
                    sectors = [sectors]
            parts.append(f"  언급 섹터: {', '.join(str(s) for s in sectors)}")
        if item.get("market_outlook"):
            parts.append(f"  시장 전망: {item['market_outlook']}")
        if item.get("investment_implications"):
            impl = item["investment_implications"]
            if len(impl) > 300:
                impl = impl[:300] + "..."
            parts.append(f"  투자 시사점: {impl}")
        if item.get("key_numbers"):
            nums = item["key_numbers"]
            if isinstance(nums, str):
                try:
                    nums = _json.loads(nums)
                except Exception:
                    nums = [nums]
            parts.append(f"  핵심 수치: {', '.join(str(n) for n in nums[:5])}")
        intel_texts.append("\n".join(parts))

    combined_text = "\n\n".join(intel_texts)

    # Gemini에 합성 요청
    system_prompt = (
        "당신은 한국 주식시장 전문 애널리스트입니다. "
        "여러 투자 유튜버의 분석을 종합하여 크로스 패턴을 추출합니다."
    )
    prompt = (
        f"아래는 지난 7일간 한국 투자 유튜브 {len(intel)}개 영상의 분석 결과입니다.\n\n"
        f"{combined_text}\n\n"
        "위 영상들을 종합 분석하여 다음을 JSON으로 반환하세요:\n"
        "{\n"
        '  "synthesis": "전체 종합 분석 (3-5문장)",\n'
        '  "consensus_themes": ["다수 유튜버가 동의하는 주제 (최대 5개)"],\n'
        '  "contrarian_views": ["소수 의견이나 반대 시각 (최대 3개)"],\n'
        '  "top_tickers": [{"ticker": "종목명", "sentiment": "긍정/부정/중립", "mentions": 횟수}],\n'
        '  "top_sectors": [{"sector": "섹터명", "outlook": "긍정/부정/중립"}],\n'
        '  "market_consensus": "전체 시장 센티먼트 요약 (1문장)"\n'
        "}\n"
        "JSON만 반환하세요."
    )

    try:
        if ai_router:
            result_text = await ai_router.analyze(
                "youtube_synthesis",
                prompt,
                system=system_prompt,
                max_tokens=2000,
                temperature=0.3,
            )
        else:
            # Direct Gemini call fallback
            import httpx
            gemini_key = os.getenv("GEMINI_API_KEY", "")
            if not gemini_key:
                logger.warning("youtube_weekly_synthesis: no GEMINI_API_KEY")
                empty["synthesis"] = "Gemini API 키 없음"
                return empty

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-pro"
                f":generateContent?key={gemini_key}"
            )
            payload = {
                "contents": [{"role": "user", "parts": [{"text": f"{system_prompt}\n\n{prompt}"}]}],
                "generationConfig": {"maxOutputTokens": 2000, "temperature": 0.3},
            }
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.error("Gemini synthesis error: %s", resp.text[:300])
                    empty["synthesis"] = f"Gemini API 오류 ({resp.status_code})"
                    return empty
                data = resp.json()
                candidates = data.get("candidates", [])
                result_text = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        result_text = parts[0].get("text", "")

        if not result_text:
            empty["synthesis"] = "AI 응답 없음"
            return empty

        # JSON 파싱
        clean = result_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = _json.loads(clean)
        parsed["created_at"] = datetime.now().isoformat()
        parsed["video_count"] = len(intel)

        # DB에 학습 이벤트 저장
        db.save_learning_event(
            event_type="youtube_weekly_synthesis",
            description=f"주간 YouTube 합성: {len(intel)}건 영상 → Gemini 종합 분석",
            data_json=_json.dumps(parsed, ensure_ascii=False, default=str),
            impact_summary=parsed.get("market_consensus", ""),
        )

        logger.info(
            "youtube_weekly_synthesis: %d videos → synthesis complete", len(intel)
        )
        return parsed

    except _json.JSONDecodeError:
        logger.warning("youtube_weekly_synthesis: JSON parse failed, using raw text")
        result = dict(empty)
        result["synthesis"] = result_text[:500] if result_text else "파싱 실패"
        result["created_at"] = datetime.now().isoformat()
        return result
    except Exception as e:
        logger.error("youtube_weekly_synthesis error: %s", e, exc_info=True)
        empty["synthesis"] = f"합성 오류: {e}"
        return empty


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

        # v10.4: 자막 없으면 Whisper → 제목+설명 폴백 체인
        if not transcript:
            # Whisper 시도 (음성 전사)
            try:
                transcript = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_transcript_whisper, item.video_id,
                )
                if transcript:
                    logger.info("YouTube Whisper fallback success: %s (%d chars)", item.title[:30], len(transcript))
            except Exception:
                logger.debug("Whisper fallback failed for %s", item.video_id, exc_info=True)

        if not transcript:
            # v10.4: 메타데이터 기반 풍부한 폴백
            try:
                meta = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_video_metadata, item.video_id,
                )
                parts = [f"채널: {meta.get('channel', item.source)}"]
                parts.append(f"제목: {meta.get('title', '') or item.title}")
                if meta.get("description"):
                    parts.append(f"설명: {meta['description'][:1000]}")
                if meta.get("hashtags"):
                    parts.append(f"해시태그: {', '.join(meta['hashtags'][:10])}")
                fallback_text = "\n".join(parts)
            except Exception:
                fallback_text = f"제목: {item.title}"

            if len(fallback_text) > 30:
                logger.info("YouTube using metadata fallback: %s", item.title[:30])
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


# ── v11.0: Gemini Flash 벌크 분석 + 티어드 배치 ─────────────────────────────

async def summarize_transcript_gemini_flash(
    transcript: str,
    title: str,
    source: str,
) -> dict:
    """Gemini Flash로 저비용 YouTube 자막 분석 (Tier1).

    비용: ~$0.0004/호출 (Flash, ~2000 토큰 입력 + 500 출력)
    Haiku 대비 ~7.5배 저렴.
    """
    import httpx
    import json as _json

    empty = {
        "full_summary": "", "mentioned_tickers": [], "mentioned_sectors": [],
        "market_outlook": "", "key_numbers": [], "investment_implications": "",
        "raw_summary": "", "confidence": 0.0,
    }
    if not transcript or len(transcript) < 50:
        return empty

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        return empty

    text = transcript[:5000]

    is_metadata_only = text.startswith("채널:") or text.startswith("제목:")
    if is_metadata_only:
        prompt = (
            f"[{source}] 영상 메타데이터 분석:\n{text}\n\n"
            "제목/설명에서 투자 인사이트 추출. JSON만 출력:\n"
            '{"full_summary":"3줄 요약","mentioned_tickers":[{"name":"종목","ticker":"코드","sentiment":"긍정/부정/중립"}],'
            '"mentioned_sectors":["섹터"],"market_outlook":"bullish/bearish/neutral","key_numbers":[],'
            '"investment_implications":"시사점 1줄"}'
        )
    else:
        prompt = (
            f"[{source}] '{title}' 자막 분석:\n{text}\n\n"
            "핵심 투자 인사이트를 JSON으로 추출:\n"
            '{"full_summary":"5-8줄 상세요약","mentioned_tickers":[{"name":"종목","ticker":"6자리코드","sentiment":"긍정/부정/중립","context":"이유1줄"}],'
            '"mentioned_sectors":["섹터"],"market_outlook":"bullish/bearish/neutral/mixed",'
            '"key_numbers":[{"label":"지표","value":"값","unit":"단위"}],'
            '"investment_implications":"액션 1-2줄"}'
        )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash"
        f":generateContent?key={gemini_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 600, "temperature": 0.2},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Gemini Flash error %d: %s", resp.status_code, resp.text[:200])
                return empty

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return empty
            parts = candidates[0].get("content", {}).get("parts", [])
            raw_text = parts[0].get("text", "") if parts else ""

        # 토큰 추적
        try:
            from kstock.core.token_tracker import track_usage_global
            usage_meta = data.get("usageMetadata", {})
            track_usage_global(
                provider="gemini",
                model="gemini-2.0-flash",
                function_name="youtube_summary_flash",
                input_tokens=usage_meta.get("promptTokenCount", 0),
                output_tokens=usage_meta.get("candidatesTokenCount", 0),
            )
        except Exception:
            pass

        # JSON 파싱
        json_text = raw_text.strip()
        if "```" in json_text:
            m = re.search(r"```(?:json)?\s*(.*?)```", json_text, re.DOTALL)
            if m:
                json_text = m.group(1)
        if json_text.startswith("{"):
            parsed = _json.loads(json_text)
            parsed["confidence"] = 0.6 if is_metadata_only else 0.8
            parsed["raw_summary"] = parsed.get("full_summary", "")[:200]
            return parsed

        return empty

    except Exception as e:
        logger.debug("Gemini Flash summary error: %s", e)
        return empty


def _detect_tracked_analysts(title: str, description: str = "") -> list[str]:
    """제목/설명에서 추적 대상 애널리스트 이름 감지."""
    text = f"{title} {description}"
    return [name for name in TRACKED_ANALYSTS if name in text]


async def batch_youtube_tiered(
    db,
    max_videos: int = 20,
    hours_lookback: int = 8,
    budget_remaining: float = 1.0,
) -> dict:
    """v11.0: 예산 인식 티어드 YouTube 배치 학습.

    Tier1 (Gemini Flash): 모든 영상 → $0.0004/건
    제목에 TRACKED_ANALYSTS 포함 시 → Tier2 자동 승격 플래그

    Returns:
        {"processed": N, "tier1": N, "tier2_flagged": N, "cost_usd": X}
    """
    from kstock.core.budget_manager import can_spend

    result = {"processed": 0, "tier1": 0, "tier2_flagged": 0, "cost_usd": 0.0, "skipped": 0}

    # RSS에서 최근 영상 수집
    items = await fetch_global_news(max_per_feed=5, hours_lookback=hours_lookback)
    yt_items = [it for it in items if it.video_id]

    if not yt_items:
        logger.info("batch_youtube_tiered: no YouTube items found")
        return result

    count = 0
    for item in yt_items:
        if count >= max_videos:
            break

        # 예산 체크 ($0.0004/건 예상)
        if not can_spend(db, 0.001):
            logger.warning("batch_youtube_tiered: budget exhausted after %d items", count)
            break

        # 중복 체크
        try:
            if db.check_youtube_processed(item.video_id):
                result["skipped"] += 1
                continue
        except Exception:
            pass

        # 자막 추출 (자동자막 포함)
        transcript = await asyncio.get_event_loop().run_in_executor(
            None, fetch_transcript, item.video_id, 8000,
        )

        # 자막 없으면 메타데이터 폴백
        if not transcript:
            try:
                meta = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_video_metadata, item.video_id,
                )
                parts = [f"채널: {meta.get('channel', item.source)}"]
                parts.append(f"제목: {meta.get('title', '') or item.title}")
                if meta.get("description"):
                    parts.append(f"설명: {meta['description'][:1000]}")
                transcript = "\n".join(parts)
            except Exception:
                transcript = f"제목: {item.title}"

        if len(transcript) < 30:
            continue

        # Tier1: Gemini Flash 분석
        structured = await summarize_transcript_gemini_flash(
            transcript, item.title, item.source,
        )

        if structured.get("full_summary"):
            count += 1
            result["tier1"] += 1
            result["cost_usd"] += 0.0004

            # 애널리스트 이름 감지
            analysts = _detect_tracked_analysts(item.title)
            if analysts:
                result["tier2_flagged"] += 1
                structured["tracked_analysts"] = analysts

            # DB 저장
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
                    "full_summary": structured.get("full_summary", ""),
                    "raw_summary": structured.get("raw_summary", ""),
                    "confidence": structured.get("confidence", 0.0),
                })
            except Exception:
                logger.debug("YouTube intelligence save failed", exc_info=True)

    result["processed"] = count
    logger.info(
        "batch_youtube_tiered: %d processed (%d tier1, %d tier2_flagged, %d skipped), ~$%.4f",
        count, result["tier1"], result["tier2_flagged"], result["skipped"], result["cost_usd"],
    )
    return result


async def daily_learning_synthesis(db=None, ai_router=None) -> dict:
    """v11.0: 일일 학습 합성 — 24시간 YouTube + 리포트 + 칼럼 통합.

    Gemini Flash 1차 합성 → 텔레그램 전송 + DB 저장.
    """
    import json as _json

    empty = {
        "synthesis": "", "top_themes": [], "ticker_consensus": [],
        "sector_outlook": [], "analyst_highlights": [], "total_items": 0,
    }

    if not db:
        return empty

    # 24시간 학습 데이터 수집
    intel = db.get_recent_youtube_intelligence(hours=24) or []
    reports = []
    try:
        reports = db.get_recent_reports(limit=30)
    except Exception:
        pass

    columns = []
    try:
        columns = db.get_recent_columns(limit=30)
    except Exception:
        pass

    if not intel and not reports:
        logger.info("daily_synthesis: no data for today")
        return empty

    # 콘텐츠 조합
    content_parts = []
    for item in intel[:60]:
        summary = item.get("full_summary", "") or item.get("raw_summary", "")
        if summary:
            source = item.get("source", "")
            title = item.get("title", "")[:50]
            content_parts.append(f"[{source}] {title}: {summary[:300]}")

    for r in reports[:20]:
        content_parts.append(
            f"[리포트 {r.get('broker','')}] {r.get('title','')[:60]}"
        )

    for c in columns[:20]:
        content_parts.append(
            f"[칼럼 {c.get('author','')}] {c.get('title','')[:60]}: {c.get('ai_summary','')[:200]}"
        )

    combined = "\n".join(content_parts)[:12000]

    system_prompt = (
        "당신은 한국 증시 전문 애널리스트입니다. "
        "오늘 하루 수집된 YouTube 분석, 증권사 리포트, 전문가 칼럼을 종합하여 "
        "투자자에게 가장 중요한 인사이트를 정리해주세요."
    )
    prompt = (
        f"오늘 수집된 {len(intel)}건 영상 + {len(reports)}건 리포트 + {len(columns)}건 칼럼:\n\n"
        f"{combined}\n\n"
        "JSON 형식으로 일일 합성 분석을 작성해줘:\n"
        '{"synthesis":"오늘 시장 종합 5-10줄",'
        '"top_themes":["주요 테마1","테마2","테마3"],'
        '"ticker_consensus":[{"ticker":"종목명","sentiment":"강세/약세/중립","count":N}],'
        '"sector_outlook":[{"sector":"섹터","outlook":"긍정/부정/중립"}],'
        '"analyst_highlights":["주요 애널리스트 발언 요약"],'
        '"market_consensus":"전체 시장 컨센서스 1줄"}'
    )

    try:
        if ai_router:
            result_text = await ai_router.analyze(
                "daily_synthesis", prompt, system=system_prompt,
                max_tokens=1500, temperature=0.3,
            )
        else:
            import httpx
            gemini_key = os.getenv("GEMINI_API_KEY", "")
            if not gemini_key:
                return empty

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash"
                f":generateContent?key={gemini_key}"
            )
            payload = {
                "contents": [{"role": "user", "parts": [{"text": f"{system_prompt}\n\n{prompt}"}]}],
                "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.3},
            }
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return empty
                data = resp.json()
                candidates = data.get("candidates", [])
                result_text = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    result_text = parts[0].get("text", "") if parts else ""

        if not result_text:
            return empty

        clean = result_text.strip()
        if "```" in clean:
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = _json.loads(clean)
        parsed["total_items"] = len(intel) + len(reports) + len(columns)
        parsed["created_at"] = datetime.now().isoformat()

        # DB 저장
        try:
            db.save_daily_synthesis(parsed)
        except Exception:
            logger.debug("daily_synthesis DB save failed", exc_info=True)

        # 학습 이벤트
        try:
            db.save_learning_event(
                event_type="daily_synthesis",
                description=f"일일 합성: {len(intel)}영상+{len(reports)}리포트+{len(columns)}칼럼",
                data_json=_json.dumps(parsed, ensure_ascii=False, default=str),
                impact_summary=parsed.get("market_consensus", ""),
            )
        except Exception:
            pass

        logger.info("daily_synthesis: %d items → synthesis complete", parsed["total_items"])
        return parsed

    except _json.JSONDecodeError:
        logger.warning("daily_synthesis: JSON parse failed")
        return {"synthesis": result_text[:500] if result_text else "", **empty}
    except Exception as e:
        logger.error("daily_synthesis error: %s", e, exc_info=True)
        return empty
