"""글로벌 뉴스 수집기 — RSS 기반 실시간 헤드라인 수집.

지정학 리스크, 매크로 이벤트, 시장 급변 뉴스를 자동 수집하여
AI 컨텍스트와 브리핑에 반영한다.

v6.0: 초기 버전 — RSS 피드 기반
v6.1: 위기 감지 + 매크로 선행지표 연동 + 적응형 빈도
"""
from __future__ import annotations

import asyncio
import logging
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
    """RSS XML 파싱 → NewsItem 리스트."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0 또는 Atom
        channel = root.find("channel")
        if channel is not None:
            entries = channel.findall("item")
        else:
            # Atom feed
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)
            if not entries:
                entries = root.findall("entry")

        for entry in entries[:10]:
            title = ""
            link = ""
            pub_date = ""

            # RSS 2.0
            t = entry.find("title")
            if t is not None and t.text:
                title = t.text.strip()
            l = entry.find("link")
            if l is not None:
                link = (l.text or l.get("href", "")).strip()
            p = entry.find("pubDate")
            if p is not None and p.text:
                pub_date = p.text.strip()
            # Atom fallback
            if not pub_date:
                p2 = entry.find("published") or entry.find("updated")
                if p2 is not None and p2.text:
                    pub_date = p2.text.strip()

            if not title:
                continue

            impact, urgent = _compute_impact(title)
            items.append(NewsItem(
                title=title,
                source=feed["name"],
                url=link,
                published=pub_date,
                category=feed.get("category", "market"),
                lang=feed.get("lang", "ko"),
                impact_score=impact,
                is_urgent=urgent,
            ))

    except ET.ParseError as e:
        logger.debug("RSS parse error for %s: %s", feed["name"], e)
    except Exception as e:
        logger.debug("RSS processing error for %s: %s", feed["name"], e)

    return items


async def fetch_global_news(
    max_per_feed: int = 5,
    feeds: list[dict] | None = None,
) -> list[NewsItem]:
    """글로벌 뉴스 RSS 피드에서 헤드라인 수집 (병렬).

    Returns:
        NewsItem 리스트 (impact_score 내림차순 정렬)
    """
    import httpx

    target_feeds = feeds or RSS_FEEDS
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


def filter_urgent_news(items: list[NewsItem]) -> list[NewsItem]:
    """긴급 뉴스만 필터 (impact_score >= 8)."""
    return [item for item in items if item.is_urgent]


def format_news_for_context(items: list[NewsItem], max_items: int = 8) -> str:
    """AI 컨텍스트용 뉴스 포맷.

    시스템 프롬프트에 주입할 간결한 형식.
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

    return "\n".join(lines) if lines else "글로벌 이슈 없음"


def format_news_for_telegram(items: list[NewsItem], max_items: int = 10) -> str:
    """텔레그램 알림용 뉴스 포맷."""
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

    if normal:
        lines.append("\n📰 주요 뉴스")
        remaining = max_items - len(urgent[:5])
        for item in normal[:remaining]:
            lines.append(f"  {item.title}")
            lines.append(f"  — {item.source}")

    return "\n".join(lines)


def format_urgent_alert(items: list[NewsItem]) -> str:
    """긴급 이벤트 텔레그램 알림 포맷."""
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
