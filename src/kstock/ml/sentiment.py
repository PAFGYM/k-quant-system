"""K-Quant v3.5 news sentiment analysis via Naver scraping + Claude API.

Pipeline:
1. Scrape Naver News search results for each stock name.
2. Batch-send headlines to Claude (claude-haiku-4-5-20251001) for sentiment classification.
3. Return structured SentimentResult per stock with score bonus for the composite scorer.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SentimentResult:
    """Sentiment analysis result for a single stock."""

    positive_pct: float = 0.0   # 0 ~ 100
    negative_pct: float = 0.0   # 0 ~ 100
    neutral_pct: float = 0.0    # 0 ~ 100
    summary: str = ""           # one-line Korean summary
    headline_count: int = 0


# ---------------------------------------------------------------------------
# 1. Naver News scraping
# ---------------------------------------------------------------------------


def fetch_news(stock_name: str, days: int = 3) -> list[str]:
    """Scrape Naver News search results for *stock_name*.

    Args:
        stock_name: Korean stock name (e.g. "삼성전자").
        days: Number of recent days to consider (used for filtering if needed).

    Returns:
        List of headline strings (up to ~15). Empty list on any failure.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("requests/bs4 not installed; skipping news fetch for %s", stock_name)
        return []

    encoded = quote_plus(stock_name)
    url = f"https://search.naver.com/search.naver?where=news&query={encoded}"

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": "https://www.naver.com/",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception:
        logger.warning("Failed to fetch Naver news for %s", stock_name, exc_info=True)
        return []

    headlines: list[str] = []
    try:
        soup = BeautifulSoup(resp.text, "html.parser")

        # Primary selector: Naver news search result titles
        selectors = [
            "a.news_tit",                          # current Naver layout
            "a.title_link",                         # alternate layout
            ".news_area a.news_tit",                # scoped variant
            ".list_news .news_tit",                 # list variant
            ".news_wrap .news_tit",                 # wrap variant
        ]

        for selector in selectors:
            elements = soup.select(selector)
            if elements:
                for el in elements:
                    title = el.get_text(strip=True)
                    if title and len(title) > 5:
                        headlines.append(title)
                break

        # Fallback: grab any anchor tags inside news areas
        if not headlines:
            for a_tag in soup.find_all("a"):
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href", "")
                if (
                    title
                    and len(title) > 10
                    and stock_name in title
                    and ("news" in href or "article" in href)
                ):
                    headlines.append(title)
                    if len(headlines) >= 15:
                        break

    except Exception:
        logger.warning("Failed to parse Naver news HTML for %s", stock_name, exc_info=True)
        return []

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    return unique[:15]


# ---------------------------------------------------------------------------
# 2. Claude API batch sentiment analysis
# ---------------------------------------------------------------------------


_SENTIMENT_SYSTEM_PROMPT = (
    "You are a Korean stock news sentiment analyst. "
    "Analyze the news headlines provided for each stock ticker "
    "and classify the overall sentiment."
)


def _build_sentiment_prompt(stock_headlines: dict[str, list[str]]) -> str:
    """Build the user prompt that contains all stocks and their headlines."""
    parts: list[str] = []
    parts.append(
        "아래 종목별 뉴스 헤드라인을 분석하여 감성을 분류해주세요.\n"
        "각 종목에 대해 positive/negative/neutral 비율(합계 100%)과 "
        "한줄 요약(Korean)을 제공해주세요.\n\n"
        "반드시 아래 JSON 형식으로만 응답해주세요 (다른 텍스트 없이):\n"
        "{\n"
        '  "TICKER": {\n'
        '    "positive_pct": 60.0,\n'
        '    "negative_pct": 20.0,\n'
        '    "neutral_pct": 20.0,\n'
        '    "summary": "한줄 요약"\n'
        "  },\n"
        "  ...\n"
        "}\n\n"
        "---\n\n"
    )

    for ticker, headlines in stock_headlines.items():
        parts.append(f"[{ticker}]")
        if headlines:
            for i, h in enumerate(headlines, 1):
                parts.append(f"  {i}. {h}")
        else:
            parts.append("  (뉴스 없음 - neutral로 처리)")
        parts.append("")

    return "\n".join(parts)


def analyze_sentiment_batch(
    stock_headlines: dict[str, list[str]],
    anthropic_key: str,
) -> dict[str, SentimentResult]:
    """Send all stocks in one Claude API call and return parsed results.

    Args:
        stock_headlines: Mapping of ticker -> list of headline strings.
        anthropic_key: Anthropic API key.

    Returns:
        Mapping of ticker -> SentimentResult. On failure, returns neutral
        defaults for every ticker.
    """
    if not stock_headlines:
        return {}

    # Prepare neutral fallback
    def _neutral(ticker: str, count: int) -> SentimentResult:
        return SentimentResult(
            positive_pct=0.0,
            negative_pct=0.0,
            neutral_pct=100.0,
            summary="뉴스 데이터 부족",
            headline_count=count,
        )

    fallback = {
        t: _neutral(t, len(hs)) for t, hs in stock_headlines.items()
    }

    if not anthropic_key:
        logger.warning("No Anthropic API key provided; returning neutral sentiment.")
        return fallback

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed; returning neutral sentiment.")
        return fallback

    prompt = _build_sentiment_prompt(stock_headlines)

    try:
        client = anthropic.Anthropic(api_key=anthropic_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=_SENTIMENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.error("Claude API call failed for sentiment analysis", exc_info=True)
        return fallback

    # Parse response
    raw_text = ""
    try:
        for block in message.content:
            if hasattr(block, "text"):
                raw_text += block.text
    except Exception:
        logger.error("Failed to extract text from Claude response", exc_info=True)
        return fallback

    # Try to extract JSON from the response (handle markdown code fences)
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        # Remove code fence markers
        lines = json_text.split("\n")
        # Drop first line (```json or ```) and last line (```)
        inner_lines = []
        started = False
        for line in lines:
            stripped = line.strip()
            if not started:
                if stripped.startswith("```"):
                    started = True
                    continue
            else:
                if stripped == "```":
                    break
                inner_lines.append(line)
        json_text = "\n".join(inner_lines)

    try:
        data: dict[str, Any] = json.loads(json_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from Claude response: %s", raw_text[:300])
        return fallback

    results: dict[str, SentimentResult] = {}
    for ticker, headlines in stock_headlines.items():
        entry = data.get(ticker, {})
        if not isinstance(entry, dict):
            results[ticker] = _neutral(ticker, len(headlines))
            continue

        try:
            pos = float(entry.get("positive_pct", 0))
            neg = float(entry.get("negative_pct", 0))
            neu = float(entry.get("neutral_pct", 0))
            summary = str(entry.get("summary", ""))
        except (TypeError, ValueError):
            results[ticker] = _neutral(ticker, len(headlines))
            continue

        # Normalize percentages to sum to 100
        total = pos + neg + neu
        if total > 0 and abs(total - 100.0) > 0.1:
            pos = pos / total * 100
            neg = neg / total * 100
            neu = neu / total * 100

        results[ticker] = SentimentResult(
            positive_pct=round(pos, 1),
            negative_pct=round(neg, 1),
            neutral_pct=round(neu, 1),
            summary=summary or "감성 분석 완료",
            headline_count=len(headlines),
        )

    # Fill any missing tickers
    for ticker in stock_headlines:
        if ticker not in results:
            results[ticker] = _neutral(ticker, len(stock_headlines[ticker]))

    return results


# ---------------------------------------------------------------------------
# 3. Score bonus
# ---------------------------------------------------------------------------


def get_sentiment_bonus(result: SentimentResult) -> int:
    """Compute a score bonus/penalty from sentiment for the composite scorer.

    Rules:
        headline_count < 3 -> 0 (insufficient data, ignore)
        positive >= 70%    -> +10
        positive 50-70%    -> +5
        negative >= 50%    -> -10

    Args:
        result: SentimentResult for one stock.

    Returns:
        Integer bonus to add to the composite score.
    """
    if result.headline_count < 3:
        return 0

    if result.positive_pct >= 70.0:
        return 10
    if result.positive_pct >= 50.0:
        return 5
    if result.negative_pct >= 50.0:
        return -10

    return 0


# ---------------------------------------------------------------------------
# 4. Telegram format
# ---------------------------------------------------------------------------


def format_sentiment_summary(results: dict[str, SentimentResult]) -> str:
    """Format sentiment results for a Telegram message.

    Shows top positive and top negative stocks with emojis.
    No ** bold formatting (plain text for Telegram).

    Args:
        results: Mapping of ticker -> SentimentResult.

    Returns:
        Formatted string for Telegram.
    """
    if not results:
        return f"\U0001f4f0 {USER_NAME}, 뉴스 감성 분석 결과가 없습니다."

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    # Sort by positive_pct descending
    sorted_positive = sorted(
        results.items(),
        key=lambda x: x[1].positive_pct,
        reverse=True,
    )
    # Sort by negative_pct descending
    sorted_negative = sorted(
        results.items(),
        key=lambda x: x[1].negative_pct,
        reverse=True,
    )

    lines: list[str] = [
        f"\U0001f4f0 {USER_NAME}, AI 뉴스 감성 분석",
        "\u2500" * 25,
        "",
    ]

    # Top positive (up to 5)
    lines.append("\U0001f7e2 긍정 뉴스 Top")
    positive_shown = 0
    for ticker, r in sorted_positive:
        if r.positive_pct < 40 or r.headline_count < 1:
            continue
        emoji = "\U0001f525" if r.positive_pct >= 70 else "\U0001f7e2"
        lines.append(
            f"  {emoji} {ticker}: 긍정 {r.positive_pct:.0f}% "
            f"({r.headline_count}건)"
        )
        if r.summary:
            lines.append(f"     \u2192 {r.summary}")
        positive_shown += 1
        if positive_shown >= 5:
            break
    if positive_shown == 0:
        lines.append("  (해당 없음)")
    lines.append("")

    # Top negative (up to 5)
    lines.append("\U0001f534 부정 뉴스 Top")
    negative_shown = 0
    for ticker, r in sorted_negative:
        if r.negative_pct < 30 or r.headline_count < 1:
            continue
        emoji = "\u26a0\ufe0f" if r.negative_pct >= 50 else "\U0001f534"
        lines.append(
            f"  {emoji} {ticker}: 부정 {r.negative_pct:.0f}% "
            f"({r.headline_count}건)"
        )
        if r.summary:
            lines.append(f"     \u2192 {r.summary}")
        negative_shown += 1
        if negative_shown >= 5:
            break
    if negative_shown == 0:
        lines.append("  (해당 없음)")

    lines.extend([
        "",
        "\u2500" * 25,
        f"\U0001f551 {now_str}",
        "\U0001f916 Powered by Claude (K-Quant v3.0)",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Daily pipeline
# ---------------------------------------------------------------------------


def run_daily_sentiment(
    universe: list[dict],
    anthropic_key: str,
) -> dict[str, SentimentResult]:
    """Fetch news and run sentiment analysis for the entire universe.

    Stocks are batched into groups of 10 per Claude API call to stay
    cost-efficient.

    Args:
        universe: List of stock dicts, each must have "ticker" and "name" keys.
        anthropic_key: Anthropic API key.

    Returns:
        Combined mapping of ticker -> SentimentResult for all stocks.
    """
    if not universe:
        logger.info("Empty universe; skipping sentiment analysis.")
        return {}

    # Step 1: Fetch news for each stock (with rate limiting)
    all_headlines: dict[str, list[str]] = {}
    for stock in universe:
        ticker = stock.get("ticker", "")
        name = stock.get("name", "")
        if not ticker or not name:
            logger.warning("Skipping stock with missing ticker/name: %s", stock)
            continue

        logger.info("Fetching news for %s (%s)", name, ticker)
        headlines = fetch_news(name)
        all_headlines[ticker] = headlines

        # Rate limit: 0.5s between requests
        time.sleep(0.5)

    if not all_headlines:
        logger.warning("No headlines fetched for any stock.")
        return {}

    # Step 2: Batch into groups of 10 for API calls
    tickers = list(all_headlines.keys())
    batch_size = 10
    combined_results: dict[str, SentimentResult] = {}

    for i in range(0, len(tickers), batch_size):
        batch_tickers = tickers[i : i + batch_size]
        batch_headlines = {t: all_headlines[t] for t in batch_tickers}

        batch_label = ", ".join(batch_tickers[:3])
        if len(batch_tickers) > 3:
            batch_label += f" ... ({len(batch_tickers)}종목)"
        logger.info("Analyzing sentiment batch: %s", batch_label)

        batch_results = analyze_sentiment_batch(batch_headlines, anthropic_key)
        combined_results.update(batch_results)

        # Small delay between API batches
        if i + batch_size < len(tickers):
            time.sleep(1.0)

    logger.info(
        "Sentiment analysis complete: %d stocks, %d with headlines",
        len(combined_results),
        sum(1 for r in combined_results.values() if r.headline_count > 0),
    )

    return combined_results
