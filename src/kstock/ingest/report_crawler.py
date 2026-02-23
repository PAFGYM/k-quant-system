"""증권사 리포트 크롤러 — 네이버 증권 리서치 수집.

K-Quant v3.6: 네이버 금융 리서치 페이지에서 증권사 리포트를 수집하여 DB에 저장.
- 종목분석 리포트 (company): 목록 + 상세(목표가/의견)
- 산업분석 리포트 (industry): 섹터별 리포트

Sources:
    https://finance.naver.com/research/company_list.naver
    https://finance.naver.com/research/industry_list.naver
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ── URL ──────────────────────────────────────────────────────────────────────

COMPANY_LIST_URL = "https://finance.naver.com/research/company_list.naver?&page={page}"
COMPANY_DETAIL_URL = "https://finance.naver.com/research/{path}"
INDUSTRY_LIST_URL = "https://finance.naver.com/research/industry_list.naver?&page={page}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/research/",
}


# ── 종목 리포트 크롤러 ───────────────────────────────────────────────────────

async def crawl_company_reports(
    pages: int = 3,
    fetch_detail: bool = True,
) -> list[dict[str, Any]]:
    """네이버 증권 종목분석 리포트 수집.

    Args:
        pages: 수집할 페이지 수 (1페이지 = 약 20개 리포트)
        fetch_detail: True이면 상세 페이지에서 목표가/의견 추가 수집

    Returns:
        list of report dicts
    """
    reports: list[dict] = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for page in range(1, pages + 1):
            url = COMPANY_LIST_URL.format(page=page)
            try:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
                page_reports = _parse_company_list(resp.text)
                reports.extend(page_reports)
                logger.info("Report page %d: %d reports", page, len(page_reports))
            except Exception as e:
                logger.warning("Report page %d failed: %s", page, e)

        # 상세 페이지에서 목표가/의견 수집 (병렬, 5개씩)
        if fetch_detail:
            need_detail = [r for r in reports if r.get("detail_path")]
            sem = asyncio.Semaphore(5)

            async def _fetch_one(r):
                async with sem:
                    try:
                        detail_url = COMPANY_DETAIL_URL.format(path=r["detail_path"])
                        resp = await client.get(detail_url, headers=HEADERS)
                        target, opinion = _parse_detail_page(resp.text)
                        r["target_price"] = target
                        r["opinion"] = opinion
                    except Exception as e:
                        logger.debug("Detail fetch failed: %s", e)
                    await asyncio.sleep(0.3)  # rate limit

            tasks = [_fetch_one(r) for r in need_detail]
            await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("Total company reports: %d", len(reports))
    return reports


def _parse_company_list(html: str) -> list[dict]:
    """네이버 종목분석 목록 페이지 파싱.

    테이블 컬럼: [종목명] [제목] [증권사] [첨부] [작성일] [조회수]
    """
    soup = BeautifulSoup(html, "html.parser")
    reports = []

    table = soup.find("table", class_="type_1")
    if not table:
        return reports

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        try:
            # [0] 종목명 + 종목코드
            stock_link = cells[0].find("a")
            stock_name = cells[0].get_text(strip=True)
            ticker = ""
            if stock_link and stock_link.get("href"):
                m = re.search(r"code=(\d{6})", stock_link["href"])
                if m:
                    ticker = m.group(1)

            # [1] 제목 + 상세 페이지 링크
            title_link = cells[1].find("a")
            title = cells[1].get_text(strip=True)
            detail_path = ""
            if title_link:
                title = title_link.get_text(strip=True)
                href = title_link.get("href", "")
                if href and "company_read" in href:
                    detail_path = href.lstrip("/")

            # [2] 증권사
            broker = cells[2].get_text(strip=True)

            # [3] PDF 첨부
            pdf_url = ""
            pdf_link = cells[3].find("a")
            if pdf_link and pdf_link.get("href"):
                pdf_url = pdf_link["href"]

            # [4] 작성일
            date_text = cells[4].get_text(strip=True)
            date = _parse_date(date_text)

            if not title or not broker:
                continue

            reports.append({
                "source": "naver_research",
                "title": title,
                "broker": broker,
                "ticker": ticker,
                "stock_name": stock_name,
                "target_price": 0,
                "prev_target_price": 0,
                "opinion": "",
                "date": date,
                "pdf_url": pdf_url,
                "detail_path": detail_path,
            })
        except Exception as e:
            logger.debug("Row parse error: %s", e)

    return reports


def _parse_detail_page(html: str) -> tuple[float, str]:
    """상세 페이지에서 목표가 + 투자의견 추출.

    .view_info_1 클래스 내에:
      목표가 175,000 | 투자의견 Buy
    """
    soup = BeautifulSoup(html, "html.parser")

    target_price = 0.0
    opinion = ""

    # 방법 1: view_info / view_info_1 클래스
    info_box = soup.find(class_="view_info_1") or soup.find(class_="view_info")
    if info_box:
        text = info_box.get_text(separator="|", strip=True)
        # 목표가 추출
        tp_match = re.search(r"목표가[|\s]*([0-9,]+)", text)
        if tp_match:
            target_price = _parse_price(tp_match.group(1))
        # 투자의견 추출
        op_match = re.search(
            r"투자의견[|\s]*(Buy|Strong Buy|Outperform|매수|Hold|중립|Neutral|"
            r"Underperform|매도|Sell|Reduce|Market Perform|Not Rated|NR|Trading Buy)",
            text, re.IGNORECASE,
        )
        if op_match:
            opinion = _normalize_opinion(op_match.group(1))

    # 방법 2: 전체 텍스트 파싱 (fallback)
    if not target_price:
        full = soup.get_text(separator="|", strip=True)
        tp_match = re.search(r"목표가[|\s]*([0-9,]+)", full)
        if tp_match:
            target_price = _parse_price(tp_match.group(1))
        if not opinion:
            op_match = re.search(
                r"투자의견[|\s]*(Buy|Strong Buy|Outperform|매수|Hold|중립|"
                r"Sell|매도|Reduce|NR|Not Rated|Trading Buy)",
                full, re.IGNORECASE,
            )
            if op_match:
                opinion = _normalize_opinion(op_match.group(1))

    return target_price, opinion


# ── 산업 리포트 크롤러 ───────────────────────────────────────────────────────

async def crawl_industry_reports(pages: int = 2) -> list[dict[str, Any]]:
    """네이버 증권 산업분석 리포트 수집."""
    reports: list[dict] = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for page in range(1, pages + 1):
            url = INDUSTRY_LIST_URL.format(page=page)
            try:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
                page_reports = _parse_industry_list(resp.text)
                reports.extend(page_reports)
                logger.info("Industry page %d: %d reports", page, len(page_reports))
            except Exception as e:
                logger.warning("Industry page %d failed: %s", page, e)

    logger.info("Total industry reports: %d", len(reports))
    return reports


def _parse_industry_list(html: str) -> list[dict]:
    """네이버 산업분석 목록 페이지 파싱.

    테이블 컬럼: [섹터] [제목] [증권사] [첨부] [작성일] [조회수]
    """
    soup = BeautifulSoup(html, "html.parser")
    reports = []

    table = soup.find("table", class_="type_1")
    if not table:
        return reports

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        try:
            # [0] 섹터
            sector_text = cells[0].get_text(strip=True)

            # [1] 제목
            title_link = cells[1].find("a")
            title = cells[1].get_text(strip=True)
            pdf_url = ""
            if title_link:
                title = title_link.get_text(strip=True)

            # [2] 증권사
            broker = cells[2].get_text(strip=True)

            # [3] PDF 첨부
            pdf_link = cells[3].find("a")
            if pdf_link and pdf_link.get("href"):
                pdf_url = pdf_link["href"]

            # [4] 작성일
            date_text = cells[4].get_text(strip=True)
            date = _parse_date(date_text)

            if not title or not broker:
                continue

            # 섹터: 네이버 제공 섹터명 우선, 없으면 제목에서 감지
            sector = sector_text if sector_text else _detect_sector(title)
            reports.append({
                "source": "naver_industry",
                "title": title,
                "broker": broker,
                "ticker": "",
                "stock_name": sector,
                "target_price": 0,
                "prev_target_price": 0,
                "opinion": "",
                "date": date,
                "pdf_url": pdf_url,
            })
        except Exception as e:
            logger.debug("Industry row parse: %s", e)

    return reports


# ── 통합 수집 → DB 저장 ──────────────────────────────────────────────────────

async def crawl_all_reports(
    db,
    company_pages: int = 3,
    industry_pages: int = 2,
) -> dict[str, int]:
    """전체 리포트 수집 → DB 저장.

    Returns:
        {"company": N, "industry": M, "total_new": K}
    """
    stats = {"company": 0, "industry": 0, "total_new": 0}

    # 1. 종목분석 리포트
    try:
        company_reports = await crawl_company_reports(company_pages)
        for r in company_reports:
            # 이전 목표가 (같은 ticker의 직전 리포트)
            prev_target = 0
            if r.get("ticker"):
                prev_reports = db.get_recent_reports(limit=1, ticker=r["ticker"])
                if prev_reports:
                    prev_target = prev_reports[0].get("target_price", 0)

            result = db.add_report(
                source=r["source"],
                title=r["title"],
                broker=r["broker"],
                date=r["date"],
                ticker=r.get("ticker", ""),
                target_price=r.get("target_price", 0),
                prev_target_price=prev_target,
                opinion=r.get("opinion", ""),
                pdf_url=r.get("pdf_url", ""),
                summary=r.get("stock_name", ""),
            )
            if result:
                stats["company"] += 1
                stats["total_new"] += 1
        logger.info("Company reports saved: %d new", stats["company"])
    except Exception as e:
        logger.error("Company report crawl failed: %s", e)

    # 2. 산업분석 리포트
    try:
        industry_reports = await crawl_industry_reports(industry_pages)
        for r in industry_reports:
            result = db.add_report(
                source=r["source"],
                title=r["title"],
                broker=r["broker"],
                date=r["date"],
                pdf_url=r.get("pdf_url", ""),
                summary=r.get("stock_name", ""),
            )
            if result:
                stats["industry"] += 1
                stats["total_new"] += 1
        logger.info("Industry reports saved: %d new", stats["industry"])
    except Exception as e:
        logger.error("Industry report crawl failed: %s", e)

    logger.info(
        "Report crawl done: %d company + %d industry = %d new",
        stats["company"], stats["industry"], stats["total_new"],
    )
    return stats


# ── 유틸리티 ─────────────────────────────────────────────────────────────────

def _parse_price(text: str) -> float:
    """'175,000' → 175000.0"""
    if not text:
        return 0
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned) if cleaned else 0
    except ValueError:
        return 0


def _parse_date(text: str) -> str:
    """'26.02.23' → '2026-02-23'"""
    if not text:
        return datetime.now(KST).strftime("%Y-%m-%d")
    text = text.strip().replace("/", ".")
    m = re.match(r"(\d{2,4})\.(\d{1,2})\.(\d{1,2})", text)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return f"{year:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return text


def _normalize_opinion(raw: str) -> str:
    """투자의견을 한글 통일."""
    mapping = {
        "buy": "매수",
        "strong buy": "매수",
        "outperform": "매수",
        "trading buy": "매수",
        "hold": "중립",
        "neutral": "중립",
        "market perform": "중립",
        "sell": "매도",
        "underperform": "매도",
        "reduce": "매도",
        "not rated": "NR",
        "nr": "NR",
    }
    return mapping.get(raw.lower().strip(), raw)


SECTOR_KEYWORDS = {
    "반도체": ["반도체", "HBM", "메모리", "DRAM", "NAND", "파운드리"],
    "2차전지": ["2차전지", "배터리", "양극재", "음극재", "전해질"],
    "자동차": ["자동차", "전기차", "EV", "완성차"],
    "AI/로봇": ["AI", "인공지능", "로봇", "LLM", "GPU"],
    "바이오": ["바이오", "제약", "헬스케어", "신약", "임상"],
    "방산/조선": ["방산", "조선", "방위", "함정"],
    "금융": ["금융", "은행", "보험", "증권"],
    "IT": ["IT", "소프트웨어", "클라우드", "SaaS", "플랫폼"],
    "에너지": ["에너지", "태양광", "풍력", "원자력", "수소"],
    "유통/소비재": ["유통", "소비재", "화장품", "식품", "리테일"],
}


def _detect_sector(title: str) -> str:
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                return sector
    return ""
