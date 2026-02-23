"""Account screenshot reader via Claude Vision API - K-Quant v3.5.

Reads stock brokerage account screenshots using Claude's vision capabilities,
extracts holdings and summary data, compares with previous snapshots, and
formats results for Telegram delivery.

Rules:
- No ** bold, no Markdown parse_mode
- Use emojis and line breaks for readability
- Commas in numbers (58,000)
- "주호님" personalized greeting
- Direct action instructions (not vague)
"""

from __future__ import annotations

import base64
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

_VISION_PROMPT = (
    "이 한국 증권사 계좌 스크린샷을 분석해.\n"
    "테이블은 종목당 2줄 구조야:\n"
    "  1줄: 종목명 | 평가손익 | 잔고수량 | 매입가\n"
    "  2줄: 구분(현금/유융 등) | 수익률 | 평가금액 | 현재가\n"
    "같은 종목의 2줄을 합쳐서 하나의 항목으로 추출해.\n"
    "'구분' 줄의 값(현금, 유융, 유옹, 신용, 담보 등)을 purchase_type 필드로 추출해.\n"
    "같은 종목이 여러 구분(현금/유융)으로 나뉘면 합산하되, purchase_type은 쉼표로 연결해.\n\n"
    "각 보유 종목의 종목명, 보유수량, 평균매수가(매입가), 현재가, 수익률(%), 평가손익, 평가금액, 구분(purchase_type)을 추출.\n"
    "상단 요약에서 매입금액, 평가금액, 평가손익, 수익률, 순자산도 추출.\n\n"
    "반드시 아래 JSON 형식으로만 응답:\n"
    '{"holdings": [{"name": "종목명", "quantity": 수량, "avg_price": 매입가, '
    '"current_price": 현재가, "profit_pct": 수익률, "eval_amount": 평가금액, '
    '"profit_amount": 평가손익, "purchase_type": "현금/유융/신용 등"}], '
    '"summary": {"total_eval": 총평가금액, "total_profit": 총평가손익, '
    '"total_profit_pct": 총수익률, "total_buy": 총매입금액}}'
)

_EMPTY_RESULT: dict[str, Any] = {
    "holdings": [],
    "summary": {
        "total_eval": 0,
        "total_profit": 0,
        "total_profit_pct": 0.0,
        "cash": 0,
    },
}

# Broad sector classification for Korean stocks (code prefix heuristic + known tickers).
_SECTOR_MAP: dict[str, str] = {
    "005930": "반도체",
    "000660": "반도체",
    "035420": "인터넷",
    "035720": "인터넷",
    "051910": "화학",
    "006400": "자동차",
    "012330": "자동차",
    "005380": "자동차",
    "068270": "바이오",
    "207940": "바이오",
    "373220": "2차전지",
    "247540": "2차전지",
    "086520": "2차전지",
    "055550": "금융",
    "105560": "금융",
    "096770": "건설",
    "034220": "건설",
    "010130": "철강",
    "005490": "철강",
    "017670": "엔터",
    "352820": "엔터",
    "003550": "통신",
    "030200": "통신",
    "032830": "반도체",
    "036570": "게임",
    "251270": "게임",
    "028260": "바이오",
    "015760": "IT",
    "066570": "전자",
    "003670": "화학",
}


# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------

async def parse_account_screenshot(
    image_bytes: bytes,
    anthropic_key: str,
) -> dict[str, Any]:
    """Parse a brokerage account screenshot using Claude Vision API.

    Args:
        image_bytes: Raw image bytes (PNG/JPEG).
        anthropic_key: Anthropic API key.

    Returns:
        Dict with "holdings" list and "summary" dict.
        On failure, returns empty result structure.
    """
    if not image_bytes:
        logger.warning("Empty image bytes provided")
        return _make_empty_result()

    if not anthropic_key:
        logger.warning("No Anthropic API key provided")
        return _make_empty_result()

    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    # Detect media type from magic bytes
    media_type = _detect_media_type(image_bytes)

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": _VISION_PROMPT,
                    },
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )

            if resp.status_code != 200:
                logger.error(
                    "Claude Vision API returned %d: %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return _make_empty_result()

            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            return _parse_vision_response(text)

    except httpx.TimeoutException:
        logger.error("Claude Vision API request timed out")
        return _make_empty_result()
    except httpx.HTTPError as exc:
        logger.error("Claude Vision API HTTP error: %s", exc)
        return _make_empty_result()
    except Exception as exc:
        logger.error("Unexpected error calling Claude Vision API: %s", exc, exc_info=True)
        return _make_empty_result()


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_screenshots(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Compare two parsed screenshot results and identify changes.

    Args:
        current: Current parsed screenshot result.
        previous: Previous parsed screenshot result.

    Returns:
        Dict with keys: improvements, worsened, sold, new_buys, cash_change.
    """
    cur_holdings = {h["ticker"]: h for h in current.get("holdings", [])}
    prev_holdings = {h["ticker"]: h for h in previous.get("holdings", [])}

    cur_tickers = set(cur_holdings.keys())
    prev_tickers = set(prev_holdings.keys())

    new_buys: list[dict[str, Any]] = []
    for ticker in cur_tickers - prev_tickers:
        h = cur_holdings[ticker]
        new_buys.append({
            "name": h.get("name", ""),
            "ticker": ticker,
            "quantity": h.get("quantity", 0),
            "avg_price": h.get("avg_price", 0),
            "current_price": h.get("current_price", 0),
            "eval_amount": h.get("eval_amount", 0),
        })

    sold: list[dict[str, Any]] = []
    for ticker in prev_tickers - cur_tickers:
        h = prev_holdings[ticker]
        sold.append({
            "name": h.get("name", ""),
            "ticker": ticker,
            "quantity": h.get("quantity", 0),
            "avg_price": h.get("avg_price", 0),
            "last_price": h.get("current_price", 0),
            "profit_pct": h.get("profit_pct", 0.0),
        })

    improvements: list[dict[str, Any]] = []
    worsened: list[dict[str, Any]] = []
    for ticker in cur_tickers & prev_tickers:
        cur_h = cur_holdings[ticker]
        prev_h = prev_holdings[ticker]
        cur_pct = _to_float(cur_h.get("profit_pct", 0))
        prev_pct = _to_float(prev_h.get("profit_pct", 0))
        change = cur_pct - prev_pct

        entry = {
            "name": cur_h.get("name", ""),
            "ticker": ticker,
            "prev_profit_pct": prev_pct,
            "cur_profit_pct": cur_pct,
            "change_pct": round(change, 2),
        }

        if change > 0.01:
            improvements.append(entry)
        elif change < -0.01:
            worsened.append(entry)

    # Sort by magnitude of change
    improvements.sort(key=lambda x: x["change_pct"], reverse=True)
    worsened.sort(key=lambda x: x["change_pct"])

    cur_cash = _to_float(current.get("summary", {}).get("cash", 0))
    prev_cash = _to_float(previous.get("summary", {}).get("cash", 0))

    return {
        "improvements": improvements,
        "worsened": worsened,
        "sold": sold,
        "new_buys": new_buys,
        "cash_change": round(cur_cash - prev_cash, 0),
    }


# ---------------------------------------------------------------------------
# Portfolio scoring
# ---------------------------------------------------------------------------

def compute_portfolio_score(holdings: list[dict[str, Any]]) -> int:
    """Compute a portfolio health score (0~100).

    Evaluates:
    - Diversification (number of holdings, position sizing)
    - Sector concentration
    - Win/loss ratio among current holdings

    Args:
        holdings: List of holding dicts from parse_account_screenshot.

    Returns:
        Integer score from 0 to 100.
    """
    if not holdings:
        return 0

    score = 0.0
    n = len(holdings)

    # --- Diversification: number of holdings (max 30 points) ---
    if n >= 10:
        div_score = 30.0
    elif n >= 7:
        div_score = 25.0
    elif n >= 5:
        div_score = 20.0
    elif n >= 3:
        div_score = 15.0
    elif n == 2:
        div_score = 10.0
    else:
        div_score = 5.0

    # Penalty for too many holdings (over-diversification)
    if n > 20:
        div_score -= 5.0

    score += div_score

    # --- Position size balance (max 25 points) ---
    eval_amounts = [_to_float(h.get("eval_amount", 0)) for h in holdings]
    total_eval = sum(eval_amounts)

    if total_eval > 0:
        weights = [a / total_eval for a in eval_amounts]
        max_weight = max(weights) if weights else 0
        # Ideal: no single position > 30%
        if max_weight <= 0.15:
            size_score = 25.0
        elif max_weight <= 0.25:
            size_score = 20.0
        elif max_weight <= 0.35:
            size_score = 15.0
        elif max_weight <= 0.50:
            size_score = 10.0
        else:
            size_score = 5.0
    else:
        size_score = 10.0

    score += size_score

    # --- Sector concentration (max 25 points) ---
    sectors: list[str] = []
    for h in holdings:
        ticker = h.get("ticker", "")
        sector = _SECTOR_MAP.get(ticker, "기타")
        sectors.append(sector)

    sector_counts = Counter(sectors)
    unique_sectors = len(sector_counts)
    max_sector_count = max(sector_counts.values()) if sector_counts else 0
    sector_concentration = max_sector_count / n if n > 0 else 1.0

    if unique_sectors >= 5 and sector_concentration <= 0.3:
        sector_score = 25.0
    elif unique_sectors >= 4 and sector_concentration <= 0.4:
        sector_score = 20.0
    elif unique_sectors >= 3 and sector_concentration <= 0.5:
        sector_score = 15.0
    elif unique_sectors >= 2:
        sector_score = 10.0
    else:
        sector_score = 5.0

    score += sector_score

    # --- Win/loss ratio (max 20 points) ---
    winners = 0
    losers = 0
    for h in holdings:
        pct = _to_float(h.get("profit_pct", 0))
        if pct > 0:
            winners += 1
        elif pct < 0:
            losers += 1

    total_decided = winners + losers
    if total_decided > 0:
        win_ratio = winners / total_decided
        if win_ratio >= 0.7:
            wl_score = 20.0
        elif win_ratio >= 0.5:
            wl_score = 15.0
        elif win_ratio >= 0.3:
            wl_score = 10.0
        else:
            wl_score = 5.0
    else:
        wl_score = 10.0  # neutral if all breakeven

    score += wl_score

    return max(0, min(100, int(round(score))))


# ---------------------------------------------------------------------------
# Diagnosis accuracy tracking
# ---------------------------------------------------------------------------

def evaluate_diagnosis_accuracy(
    prev_diagnoses: list[dict[str, Any]],
    current_holdings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare previous diagnosis predictions with actual outcomes.

    Args:
        prev_diagnoses: List of previous diagnosis dicts with keys:
            ticker, name, direction ("up"/"down"/"hold"), confidence (0~100).
        current_holdings: Current holdings for outcome comparison.

    Returns:
        List of accuracy result dicts with keys:
            ticker, name, predicted, actual, correct (bool), confidence.
    """
    cur_map = {h["ticker"]: h for h in current_holdings}
    results: list[dict[str, Any]] = []

    for diag in prev_diagnoses:
        ticker = diag.get("ticker", "")
        predicted = diag.get("direction", "hold")
        confidence = diag.get("confidence", 50)

        cur_h = cur_map.get(ticker)
        if cur_h is None:
            # Stock was sold - consider direction as "down" if predicted down,
            # or unknown
            actual = "sold"
            correct = predicted == "down"
        else:
            pct = _to_float(cur_h.get("profit_pct", 0))
            if pct > 1.0:
                actual = "up"
            elif pct < -1.0:
                actual = "down"
            else:
                actual = "hold"

            correct = predicted == actual

        results.append({
            "ticker": ticker,
            "name": diag.get("name", ""),
            "predicted": predicted,
            "actual": actual,
            "correct": correct,
            "confidence": confidence,
        })

    return results


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_screenshot_summary(
    parsed: dict[str, Any],
    comparison: dict[str, Any] | None = None,
    prev_diagnoses: list[dict[str, Any]] | None = None,
) -> str:
    """Format parsed screenshot data for Telegram.

    Args:
        parsed: Result from parse_account_screenshot.
        comparison: Optional result from compare_screenshots.
        prev_diagnoses: Optional previous diagnosis list for accuracy tracking.

    Returns:
        Formatted string for Telegram (no ** bold, commas in numbers).
    """
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    holdings = parsed.get("holdings", [])
    summary = parsed.get("summary", {})

    lines: list[str] = [
        "\u2550" * 22,
        f"{USER_NAME}, 계좌 현황 분석 완료",
        "\u2550" * 22,
        "",
    ]

    # --- Summary section ---
    total_eval = _to_float(summary.get("total_eval", 0))
    total_profit = _to_float(summary.get("total_profit", 0))
    total_profit_pct = _to_float(summary.get("total_profit_pct", 0))
    cash = _to_float(summary.get("cash", 0))

    profit_emoji = "\U0001f7e2" if total_profit > 0 else "\U0001f534" if total_profit < 0 else "\U0001f7e1"

    lines.append(f"\U0001f4b0 총 평가금액: {total_eval:,.0f}원")
    lines.append(f"{profit_emoji} 총 손익: {total_profit:+,.0f}원 ({total_profit_pct:+.2f}%)")
    lines.append(f"\U0001f4b5 예수금: {cash:,.0f}원")
    lines.append("")

    # --- Holdings section ---
    if holdings:
        lines.append(f"\U0001f4ca 보유 종목 ({len(holdings)}개)")
        lines.append("\u2500" * 25)

        for h in holdings:
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            qty = h.get("quantity", 0)
            avg_price = _to_float(h.get("avg_price", 0))
            cur_price = _to_float(h.get("current_price", 0))
            pct = _to_float(h.get("profit_pct", 0))
            eval_amt = _to_float(h.get("eval_amount", 0))

            emoji = "\U0001f7e2" if pct > 0 else "\U0001f534" if pct < 0 else "\U0001f7e1"

            lines.append(f"{emoji} {name} ({ticker})")
            lines.append(f"   {qty}주 | 평균 {avg_price:,.0f}원 -> {cur_price:,.0f}원")
            lines.append(f"   수익률 {pct:+.2f}% | 평가 {eval_amt:,.0f}원")
            lines.append("")
    else:
        lines.append("보유 종목이 없습니다.")
        lines.append("")

    # --- Portfolio score ---
    portfolio_score = compute_portfolio_score(holdings)
    score_emoji = (
        "\U0001f3c6" if portfolio_score >= 80
        else "\U0001f44d" if portfolio_score >= 60
        else "\U0001f44a" if portfolio_score >= 40
        else "\u26a0\ufe0f"
    )
    lines.append(f"{score_emoji} 포트폴리오 건강 점수: {portfolio_score}/100")
    lines.append("")

    # --- Comparison section ---
    if comparison is not None:
        lines.append("\u2500" * 25)
        lines.append("\U0001f504 이전 대비 변화")
        lines.append("")

        improvements = comparison.get("improvements", [])
        worsened = comparison.get("worsened", [])
        new_buys = comparison.get("new_buys", [])
        sold = comparison.get("sold", [])
        cash_change = comparison.get("cash_change", 0)

        if improvements:
            lines.append("\U0001f7e2 개선 종목")
            for item in improvements[:5]:
                lines.append(
                    f"   {item['name']}: {item['prev_profit_pct']:+.2f}% -> "
                    f"{item['cur_profit_pct']:+.2f}% ({item['change_pct']:+.2f}%p)"
                )
            lines.append("")

        if worsened:
            lines.append("\U0001f534 악화 종목")
            for item in worsened[:5]:
                lines.append(
                    f"   {item['name']}: {item['prev_profit_pct']:+.2f}% -> "
                    f"{item['cur_profit_pct']:+.2f}% ({item['change_pct']:+.2f}%p)"
                )
            lines.append("")

        if new_buys:
            lines.append("\U0001f195 신규 매수")
            for item in new_buys:
                lines.append(
                    f"   {item['name']} ({item['ticker']}) "
                    f"{item['quantity']}주 @ {_to_float(item['avg_price']):,.0f}원"
                )
            lines.append("")

        if sold:
            lines.append("\U0001f4a8 매도 종목")
            for item in sold:
                pct = _to_float(item.get("profit_pct", 0))
                emoji = "\U0001f7e2" if pct > 0 else "\U0001f534"
                lines.append(
                    f"   {emoji} {item['name']} ({item['ticker']}) "
                    f"수익률 {pct:+.2f}%"
                )
            lines.append("")

        if cash_change != 0:
            cash_emoji = "\U0001f4b5" if cash_change > 0 else "\U0001f4b8"
            lines.append(f"{cash_emoji} 예수금 변동: {cash_change:+,.0f}원")
            lines.append("")

    # --- Diagnosis accuracy tracking ---
    if prev_diagnoses:
        accuracy_results = evaluate_diagnosis_accuracy(prev_diagnoses, holdings)
        if accuracy_results:
            correct_count = sum(1 for r in accuracy_results if r["correct"])
            total_count = len(accuracy_results)
            accuracy_pct = correct_count / total_count * 100 if total_count > 0 else 0

            lines.append("\u2500" * 25)
            lines.append(f"\U0001f3af 이전 진단 정확도: {accuracy_pct:.0f}% ({correct_count}/{total_count})")

            for r in accuracy_results:
                direction_map = {"up": "상승", "down": "하락", "hold": "보합", "sold": "매도"}
                predicted_kr = direction_map.get(r["predicted"], r["predicted"])
                actual_kr = direction_map.get(r["actual"], r["actual"])
                mark = "\u2705" if r["correct"] else "\u274c"
                lines.append(
                    f"   {mark} {r['name']}: "
                    f"예측 {predicted_kr} -> 실제 {actual_kr} "
                    f"(확신 {r['confidence']}%)"
                )
            lines.append("")

    lines.append(f"\U0001f551 {now}")
    lines.append("K-Quant v3.5")

    return "\n".join(lines)


def format_screenshot_reminder() -> str:
    """Generate a reminder message prompting the user to send a screenshot.

    Returns:
        Formatted reminder string for Telegram.
    """
    now = datetime.now(KST).strftime("%H:%M")

    return (
        f"\U0001f4f8 {USER_NAME}, 계좌 스크린샷을 보내주세요!\n\n"
        "증권 앱에서 계좌 잔고 화면을 캡처해서\n"
        "이 채팅에 사진으로 보내주시면\n"
        "AI가 자동으로 분석해드립니다.\n\n"
        "\U0001f4cc 분석 항목\n"
        "\u2022 종목별 수익률 현황\n"
        "\u2022 포트폴리오 건강 점수\n"
        "\u2022 이전 대비 변화 추적\n"
        "\u2022 진단 정확도 검증\n\n"
        "\U0001f4a1 팁: 보유 종목과 예수금이 모두 보이게 캡처해주세요!\n\n"
        f"\U0001f551 {now} KST"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_empty_result() -> dict[str, Any]:
    """Return a fresh empty result dict (avoid mutating the module-level constant)."""
    return {
        "holdings": [],
        "summary": {
            "total_eval": 0,
            "total_profit": 0,
            "total_profit_pct": 0.0,
            "cash": 0,
        },
    }


def _detect_media_type(image_bytes: bytes) -> str:
    """Detect image media type from magic bytes."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:4] == b"GIF8":
        return "image/gif"
    # Default to JPEG
    return "image/jpeg"


def _parse_vision_response(text: str) -> dict[str, Any]:
    """Parse the JSON text returned by Claude Vision.

    Handles cases where the response may contain markdown code fences
    or extra text around the JSON.
    """
    if not text:
        logger.warning("Empty response from Claude Vision")
        return _make_empty_result()

    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                raw = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                logger.error("Failed to parse Claude Vision JSON response")
                return _make_empty_result()
        else:
            logger.error("No JSON found in Claude Vision response")
            return _make_empty_result()

    return _normalize_parsed(raw)


def _normalize_parsed(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize the raw parsed JSON into a consistent structure.

    The Claude Vision response may use various key names; this function
    maps them to our canonical keys.
    """
    # Try to find holdings list under various key names
    holdings_raw = (
        raw.get("holdings")
        or raw.get("보유종목")
        or raw.get("stocks")
        or raw.get("portfolio")
        or []
    )

    holdings: list[dict[str, Any]] = []
    for item in holdings_raw:
        h: dict[str, Any] = {
            "name": _first_of(item, "name", "종목명", "stock_name", default=""),
            "ticker": str(
                _first_of(item, "ticker", "종목코드", "code", "stock_code", default="")
            ),
            "quantity": _to_int(
                _first_of(item, "quantity", "보유수량", "수량", "qty", default=0)
            ),
            "avg_price": _to_float(
                _first_of(item, "avg_price", "평균매수가", "평균가", "average_price", default=0)
            ),
            "current_price": _to_float(
                _first_of(item, "current_price", "현재가", "price", default=0)
            ),
            "profit_pct": _to_float(
                _first_of(item, "profit_pct", "수익률", "return_pct", "pnl_pct", default=0)
            ),
            "eval_amount": _to_float(
                _first_of(item, "eval_amount", "평가금액", "evaluation", "eval", default=0)
            ),
            "purchase_type": str(
                _first_of(item, "purchase_type", "구분", "type", "buy_type", default="")
            ),
        }
        # Ensure ticker is zero-padded to 6 digits if it looks numeric
        ticker = h["ticker"].replace(" ", "")
        if ticker.isdigit() and len(ticker) < 6:
            ticker = ticker.zfill(6)
        h["ticker"] = ticker

        holdings.append(h)

    # Try to find summary under various key names
    summary_raw = (
        raw.get("summary")
        or raw.get("계좌요약")
        or raw.get("account_summary")
        or raw.get("total")
        or raw
    )

    summary: dict[str, Any] = {
        "total_eval": _to_float(
            _first_of(summary_raw, "total_eval", "총평가", "총평가금액", "total_evaluation", default=0)
        ),
        "total_profit": _to_float(
            _first_of(summary_raw, "total_profit", "총손익", "총수익", "total_pnl", default=0)
        ),
        "total_profit_pct": _to_float(
            _first_of(summary_raw, "total_profit_pct", "총수익률", "total_return_pct", default=0)
        ),
        "cash": _to_float(
            _first_of(summary_raw, "cash", "예수금", "현금", "available_cash", default=0)
        ),
    }

    return {
        "holdings": holdings,
        "summary": summary,
    }


def _first_of(d: dict, *keys: str, default: Any = None) -> Any:
    """Return the value for the first key found in dict."""
    for key in keys:
        if key in d:
            return d[key]
    return default


def _to_float(value: Any) -> float:
    """Convert a value to float, handling strings with commas/percent signs."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").replace("원", "").replace("+", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _to_int(value: Any) -> int:
    """Convert a value to int, handling strings with commas."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("주", "").strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return 0
    return 0
