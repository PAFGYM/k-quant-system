"""Naver Finance discussion-board buzz parser for Korean stocks."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_DISCUSSION_URL = "https://finance.naver.com/item/board.naver?code={code}&page={page}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}
_CACHE_TTL = timedelta(minutes=5)
_DISCUSSION_CACHE: dict[tuple[str, int], tuple[datetime, dict]] = {}

_OVERHEAT_KEYWORDS = {
    "상한가", "급등", "쩜상", "내일", "추천", "가즈아", "풀매수", "올인",
    "리딩", "작전", "상따", "주도주", "폭등", "텐버거",
}
_ACCUMULATION_KEYWORDS = {
    "매집", "눌림", "분할", "바닥", "저평가", "실적", "수주", "계약",
    "호재", "반등", "선점", "모아간다", "저점", "돌파", "전환",
}
_RISK_KEYWORDS = {
    "손절", "하한가", "급락", "유증", "악재", "물량", "털기",
    "실망", "매도", "폭락", "경고", "투매",
}
_SQUEEZE_KEYWORDS = {
    "숏커버", "숏스퀴즈", "환매", "공매도", "쇼트커버", "환매수",
}


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def parse_discussion_titles(html_text: str) -> list[str]:
    """네이버 종목토론방 HTML에서 제목 목록만 뽑는다."""
    if not html_text:
        return []

    titles: list[str] = []
    patterns = [
        r'<td[^>]*class="title"[^>]*>\s*<a[^>]*>(.*?)</a>',
        r'<a[^>]*href="/item/board_read\.naver[^"]*"[^>]*>(.*?)</a>',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html_text, re.IGNORECASE | re.DOTALL)
        for raw in matches:
            cleaned = html.unescape(_strip_tags(raw)).replace("\n", " ").strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if not cleaned or cleaned in {"글쓰기", "공지"}:
                continue
            if cleaned not in titles:
                titles.append(cleaned)
        if titles:
            break
    return titles[:20]


def analyze_discussion_titles(
    ticker: str,
    name: str,
    titles: list[str],
) -> dict:
    """토론방 제목만으로 과열/매집/숏커버/리스크 신호를 요약한다."""
    normalized = [str(title or "").strip() for title in titles if str(title or "").strip()]
    joined = " ".join(normalized)

    def _count_hits(keywords: set[str]) -> int:
        return sum(1 for title in normalized if any(keyword in title for keyword in keywords))

    overheat_hits = _count_hits(_OVERHEAT_KEYWORDS)
    accumulation_hits = _count_hits(_ACCUMULATION_KEYWORDS)
    risk_hits = _count_hits(_RISK_KEYWORDS)
    squeeze_hits = _count_hits(_SQUEEZE_KEYWORDS)
    posts = len(normalized)

    label = ""
    score_adj = 0.0
    hot_keywords: list[str] = []
    if posts >= 5 and squeeze_hits >= 2:
        label = "토론방 숏커버 화제"
        score_adj = 4.0
        hot_keywords = [kw for kw in _SQUEEZE_KEYWORDS if kw in joined][:3]
    elif posts >= 8 and overheat_hits >= 4 and risk_hits >= 2:
        label = "리딩방 급락 경계"
        score_adj = -8.0
        hot_keywords = [kw for kw in list(_OVERHEAT_KEYWORDS | _RISK_KEYWORDS) if kw in joined][:3]
    elif posts >= 8 and overheat_hits >= 4:
        label = "토론방 과열"
        score_adj = -6.0
        hot_keywords = [kw for kw in _OVERHEAT_KEYWORDS if kw in joined][:3]
    elif posts >= 6 and accumulation_hits >= 3:
        label = "토론방 매집 감지"
        score_adj = 5.0
        hot_keywords = [kw for kw in _ACCUMULATION_KEYWORDS if kw in joined][:3]
    elif posts >= 5 and (overheat_hits >= 2 or accumulation_hits >= 2 or risk_hits >= 2):
        label = "토론방 관심 집중"
        score_adj = 2.0 if accumulation_hits >= overheat_hits else -2.0
        keyword_pool = (
            _ACCUMULATION_KEYWORDS if accumulation_hits >= overheat_hits else _OVERHEAT_KEYWORDS
        )
        hot_keywords = [kw for kw in keyword_pool if kw in joined][:3]

    return {
        "ticker": ticker,
        "name": name,
        "posts": posts,
        "overheat_hits": overheat_hits,
        "accumulation_hits": accumulation_hits,
        "risk_hits": risk_hits,
        "squeeze_hits": squeeze_hits,
        "label": label,
        "score_adj": score_adj,
        "keywords": hot_keywords,
        "titles": normalized[:8],
    }


def fetch_discussion_buzz(
    ticker: str,
    *,
    name: str = "",
    pages: int = 1,
) -> dict:
    """네이버 종목토론방 버즈를 조회한다. 실패 시 빈 결과를 반환."""
    code = str(ticker or "").strip()
    if not code:
        return {"ticker": "", "name": name or "", "posts": 0, "label": "", "score_adj": 0.0, "keywords": []}

    pages = max(1, min(int(pages or 1), 2))
    now = datetime.utcnow()
    cache_key = (code, pages)
    cached = _DISCUSSION_CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        data = dict(cached[1])
        if name and not data.get("name"):
            data["name"] = name
        return data

    titles: list[str] = []
    try:
        import httpx

        with httpx.Client(timeout=3.5, follow_redirects=True) as client:
            for page in range(1, pages + 1):
                resp = client.get(_DISCUSSION_URL.format(code=code, page=page), headers=_HEADERS)
                if resp.status_code != 200:
                    continue
                titles.extend(parse_discussion_titles(resp.text))
                if len(titles) >= 12:
                    break
    except Exception:
        logger.debug("fetch_discussion_buzz failed for %s", code, exc_info=True)

    data = analyze_discussion_titles(code, name, titles)
    _DISCUSSION_CACHE[cache_key] = (now, dict(data))
    return data
