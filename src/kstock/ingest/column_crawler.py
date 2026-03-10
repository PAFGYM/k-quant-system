"""v11.0: 네이버 금융 전문가 칼럼/투자전략 크롤러.

네이버 금융 리서치 페이지에서 전문가 칼럼과 투자전략 글을 수집.
TRACKED_ANALYSTS 이름 매칭으로 중요도 판별.

소스:
- finance.naver.com/research/invest_list.naver (전문가 칼럼)
- finance.naver.com/research/market_info_list.naver (투자전략)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── URL ──────────────────────────────────────────────────────────────────────

INVEST_LIST_URL = "https://finance.naver.com/research/invest_list.naver?&page={page}"
MARKET_INFO_URL = "https://finance.naver.com/research/market_info_list.naver?&page={page}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/research/",
}


async def crawl_naver_columns(pages: int = 2) -> list[dict]:
    """네이버 금융 전문가 칼럼 + 투자전략 수집.

    Args:
        pages: 각 소스별 수집할 페이지 수 (1페이지 ≈ 20건)

    Returns:
        list of column dicts with title, author, broker, date, content_preview
    """
    columns: list[dict] = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for url_tpl, source_tag in [
            (INVEST_LIST_URL, "naver_invest"),
            (MARKET_INFO_URL, "naver_market_info"),
        ]:
            for page in range(1, pages + 1):
                url = url_tpl.format(page=page)
                try:
                    resp = await client.get(url, headers=HEADERS)
                    resp.raise_for_status()
                    page_cols = _parse_column_list(resp.text, source_tag)
                    columns.extend(page_cols)
                    logger.info("%s page %d: %d columns", source_tag, page, len(page_cols))
                except Exception as e:
                    logger.warning("%s page %d failed: %s", source_tag, page, e)
                await asyncio.sleep(0.3)

    logger.info("Total columns crawled: %d", len(columns))
    return columns


def _parse_column_list(html: str, source_tag: str) -> list[dict]:
    """칼럼 목록 페이지 파싱.

    테이블 컬럼: [제목] [증권사(작성자)] [날짜] [조회수]
    """
    soup = BeautifulSoup(html, "html.parser")
    columns = []

    table = soup.find("table", class_="type_1")
    if not table:
        return columns

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        try:
            # [0] 제목
            title_link = cells[0].find("a")
            title = cells[0].get_text(strip=True)
            if title_link:
                title = title_link.get_text(strip=True)

            # [1] 증권사/작성자
            author_text = cells[1].get_text(strip=True)

            # [2] 날짜
            date_text = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            date = _parse_date(date_text)

            if not title:
                continue

            # 작성자 분리 (증권사 이름 vs 개인 이름)
            broker = author_text
            author = ""

            # 제목에서 애널리스트 이름 추출 시도
            from kstock.ingest.global_news import TRACKED_ANALYSTS
            is_tracked = False
            for analyst_name in TRACKED_ANALYSTS:
                if analyst_name in title or analyst_name in author_text:
                    author = analyst_name
                    is_tracked = True
                    break

            columns.append({
                "source": source_tag,
                "title": title,
                "author": author or broker,
                "broker": broker,
                "date": date,
                "is_tracked_analyst": is_tracked,
                "ai_summary": "",
                "mentioned_tickers": "",
                "mentioned_sectors": "",
            })
        except Exception:
            continue

    return columns


async def summarize_column_gemini(title: str, author: str, db=None) -> dict:
    """Gemini Flash로 칼럼 제목 기반 간략 분석.

    실제 본문은 PDF이므로 제목+작성자+증권사 기반 추론.
    """
    import os
    import httpx
    import json as _json

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        return {"ai_summary": "", "mentioned_tickers": "", "mentioned_sectors": ""}

    prompt = (
        f"증권사 칼럼 분석:\n제목: {title}\n작성자: {author}\n\n"
        "이 제목에서 투자 관련 키워드를 추출하고 1-2줄로 요약해줘. "
        "JSON: {\"ai_summary\":\"요약\",\"mentioned_tickers\":\"종목1,종목2\",\"mentioned_sectors\":\"섹터1,섹터2\"}"
    )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash"
        f":generateContent?key={gemini_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.2},
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return {"ai_summary": title, "mentioned_tickers": "", "mentioned_sectors": ""}

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return {"ai_summary": title, "mentioned_tickers": "", "mentioned_sectors": ""}
            parts = candidates[0].get("content", {}).get("parts", [])
            text = parts[0].get("text", "") if parts else ""

        # 토큰 추적
        try:
            from kstock.core.token_tracker import track_usage_global
            usage = data.get("usageMetadata", {})
            track_usage_global(
                provider="gemini", model="gemini-2.0-flash",
                function_name="column_summary",
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
            )
        except Exception:
            pass

        json_text = text.strip()
        if "```" in json_text:
            m = re.search(r"```(?:json)?\s*(.*?)```", json_text, re.DOTALL)
            if m:
                json_text = m.group(1)
        if json_text.startswith("{"):
            return _json.loads(json_text)
        return {"ai_summary": title, "mentioned_tickers": "", "mentioned_sectors": ""}
    except Exception:
        return {"ai_summary": title, "mentioned_tickers": "", "mentioned_sectors": ""}


async def crawl_all_columns(db, max_ai_summaries: int = 30) -> dict:
    """칼럼 수집 → AI 요약 → DB 저장 오케스트레이터.

    Returns:
        {"total": N, "new": M, "ai_summarized": K, "tracked_analyst": T}
    """
    from kstock.core.budget_manager import can_spend

    stats = {"total": 0, "new": 0, "ai_summarized": 0, "tracked_analyst": 0}

    columns = await crawl_naver_columns(pages=2)
    stats["total"] = len(columns)

    ai_count = 0
    for col in columns:
        # DB 저장 (중복 체크는 DB에서)
        try:
            saved = db.save_financial_column(col)
            if saved:
                stats["new"] += 1
            else:
                continue  # 이미 존재
        except Exception:
            continue

        if col.get("is_tracked_analyst"):
            stats["tracked_analyst"] += 1

        # AI 요약 (예산 내, 최대 max_ai_summaries건)
        if ai_count < max_ai_summaries and can_spend(db, 0.0002):
            summary = await summarize_column_gemini(col["title"], col["author"], db)
            if summary.get("ai_summary"):
                try:
                    db.update_column_summary(
                        col["source"], col["title"], col["date"],
                        summary.get("ai_summary", ""),
                        summary.get("mentioned_tickers", ""),
                        summary.get("mentioned_sectors", ""),
                    )
                except Exception:
                    pass
                ai_count += 1
                stats["ai_summarized"] += 1
            await asyncio.sleep(0.2)

    logger.info(
        "crawl_all_columns: %d total, %d new, %d ai, %d tracked",
        stats["total"], stats["new"], stats["ai_summarized"], stats["tracked_analyst"],
    )
    return stats


def _parse_date(text: str) -> str:
    """'26.03.10' → '2026-03-10'"""
    if not text:
        return datetime.now().strftime("%Y-%m-%d")
    text = text.strip().replace("/", ".")
    m = re.match(r"(\d{2,4})\.(\d{1,2})\.(\d{1,2})", text)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return f"{year:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return text
