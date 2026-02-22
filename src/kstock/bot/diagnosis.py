"""Per-stock diagnosis via Claude API - K-Quant v3.0.

Diagnoses each holding and generates personalized advice:
  A) +5% or more   -> Never sell. Praise. Trailing stop. Add-buy timing.
  B) 0~5%          -> Hold. Present both scenarios.
  C) -5~0%         -> Not yet stop-loss. Suggest add-buy price.
  D) Below -5%     -> Diagnose why. Rebound possible -> hold, else -> stop-loss.

Key rule: Never suggest selling a stock that is rising well (no hedging sells).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_NAME = "주호님"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_VERSION = "2023-06-01"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DiagnosisResult:
    """Result of a per-stock diagnosis."""

    ticker: str = ""
    name: str = ""
    diagnosis: str = "B"        # A, B, C, D
    action: str = "hold"        # hold, add, partial_sell, stop_loss
    message: str = ""
    target_price: float = 0.0
    stop_loss: float = 0.0
    add_buy_price: float = 0.0
    trailing_stop_pct: float = 0.0
    profit_pct: float = 0.0


# ---------------------------------------------------------------------------
# Claude prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
당신은 한국 주식 투자 어드바이저입니다. 사용자 이름은 주호님입니다.

절대 규칙:
1. 잘 올라가고 있는 종목을 헷징 형태로 팔라고 하지 마세요.
2. +5% 이상 수익 종목은 절대 매도 추천 금지. "잘 잡으셨습니다"로 칭찬하세요.
3. 볼드(**) 사용 금지. 이모지와 줄바꿈으로 가독성을 확보하세요.
4. 숫자에는 쉼표를 넣으세요 (예: 58,000원).
5. 주호님이라고 불러주세요.

진단 카테고리:
A) 수익률 +5% 이상: 절대 매도 금지. "잘 잡으셨습니다" 칭찬. 트레일링 스탑 -7~10% 제안. 추가 매수 타이밍 제안.
B) 수익률 0~5%: 보유 유지. 상승/하락 두 시나리오 제시. "좀 더 지켜보세요"
C) 수익률 -5~0%: 아직 손절 구간 아님. 추가 매수가 제안. "조금 더 기다려보세요"
D) 수익률 -5% 미만: 하락 원인 진단. 반등 가능하면 "버티세요", 아니면 "손절하세요"

응답은 반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트 없이 JSON만 출력하세요:
{
  "diagnosis": "A/B/C/D 중 하나",
  "action": "hold/add/partial_sell/stop_loss 중 하나",
  "message": "주호님에게 보내는 3~5줄 메시지 (이모지 포함, 볼드 금지, 숫자 쉼표 포함)",
  "target_price": 목표가(숫자),
  "stop_loss": 손절가(숫자),
  "add_buy_price": 추가매수가(숫자, 해당없으면 0),
  "trailing_stop_pct": 트레일링스탑퍼센트(숫자, 예: 7.0)
}
"""


def _build_stock_prompt(
    holding: dict[str, Any],
    tech_data: dict[str, Any] | None,
    flow_data: dict[str, Any] | None,
) -> str:
    """Build a per-stock user prompt for Claude."""
    name = holding.get("name", "")
    ticker = holding.get("ticker", "")
    avg_price = holding.get("avg_price", 0) or holding.get("buy_price", 0)
    current_price = holding.get("current_price", 0)
    profit_pct = holding.get("profit_pct", 0) or holding.get("pnl_pct", 0)

    if avg_price > 0 and current_price > 0 and profit_pct == 0:
        profit_pct = round((current_price - avg_price) / avg_price * 100, 2)

    parts = [
        f"종목: {name} ({ticker})",
        f"평균매수가: {avg_price:,.0f}원",
        f"현재가: {current_price:,.0f}원",
        f"수익률: {profit_pct:+.2f}%",
    ]

    if tech_data:
        rsi = tech_data.get("rsi", 0)
        macd_hist = tech_data.get("macd_histogram", 0)
        macd_cross = tech_data.get("macd_signal_cross", 0)
        ema_50 = tech_data.get("ema_50", 0)
        ema_200 = tech_data.get("ema_200", 0)
        bb_pctb = tech_data.get("bb_pctb", 0.5)

        macd_status = "골든크로스" if macd_cross == 1 else "데드크로스" if macd_cross == -1 else "중립"
        ema_status = "상승추세" if ema_50 > ema_200 else "하락추세" if ema_50 < ema_200 else "횡보"

        parts.extend([
            f"RSI: {rsi:.1f}",
            f"MACD 히스토그램: {macd_hist:.4f} ({macd_status})",
            f"EMA 50/200: {ema_50:,.0f}/{ema_200:,.0f} ({ema_status})",
            f"볼린저밴드 %B: {bb_pctb:.2f}",
        ])

        sector = tech_data.get("sector", "")
        if sector:
            parts.append(f"섹터: {sector}")

    if flow_data:
        foreign = flow_data.get("foreign_net_buy_days", 0)
        inst = flow_data.get("institution_net_buy_days", 0)
        foreign_label = f"순매수 {foreign}일" if foreign > 0 else f"순매도 {abs(foreign)}일"
        inst_label = f"순매수 {inst}일" if inst > 0 else f"순매도 {abs(inst)}일"
        parts.extend([
            f"외국인: {foreign_label}",
            f"기관: {inst_label}",
        ])

    parts.append("")
    parts.append("위 데이터를 기반으로 진단해주세요.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

async def _call_claude(
    prompt: str,
    anthropic_key: str,
    timeout_sec: float = 30.0,
) -> dict[str, Any] | None:
    """Call Claude API and return parsed JSON response.

    Returns None if the API call fails or the response cannot be parsed.
    """
    headers = {
        "x-api-key": anthropic_key,
        "anthropic-version": CLAUDE_API_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 600,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(CLAUDE_API_URL, headers=headers, json=payload)

            if resp.status_code != 200:
                logger.warning(
                    "Claude API returned %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None

            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")

            # Extract JSON from response (handle cases where model wraps in markdown)
            text = text.strip()
            if text.startswith("```"):
                # Strip markdown code fences
                lines = text.split("\n")
                json_lines = []
                inside = False
                for line in lines:
                    if line.strip().startswith("```") and not inside:
                        inside = True
                        continue
                    if line.strip().startswith("```") and inside:
                        break
                    if inside:
                        json_lines.append(line)
                text = "\n".join(json_lines)

            return json.loads(text)

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse Claude response as JSON: %s", e)
        return None
    except httpx.TimeoutException:
        logger.warning("Claude API call timed out")
        return None
    except Exception as e:
        logger.warning("Claude API call failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

def _fallback_diagnosis(
    holding: dict[str, Any],
    tech_data: dict[str, Any] | None = None,
) -> DiagnosisResult:
    """Rule-based fallback when Claude API is unavailable."""
    name = holding.get("name", "")
    ticker = holding.get("ticker", "")
    avg_price = holding.get("avg_price", 0) or holding.get("buy_price", 0)
    current_price = holding.get("current_price", 0)
    profit_pct = holding.get("profit_pct", 0) or holding.get("pnl_pct", 0)

    if avg_price > 0 and current_price > 0 and profit_pct == 0:
        profit_pct = round((current_price - avg_price) / avg_price * 100, 2)

    if profit_pct >= 5.0:
        # Category A: +5% or more
        trailing_pct = 7.0 if profit_pct < 10 else 10.0
        target = round(current_price * 1.10, 0)
        stop = round(current_price * (1 - trailing_pct / 100), 0)
        add_price = round(current_price * 0.97, 0)

        return DiagnosisResult(
            ticker=ticker,
            name=name,
            diagnosis="A",
            action="hold",
            message=(
                f"\U0001f389 {USER_NAME}, {name} 잘 잡으셨습니다!\n"
                f"현재 {profit_pct:+.1f}% 수익 중이에요.\n"
                f"\U0001f4c8 트레일링 스탑 {trailing_pct:.0f}% 설정 권장합니다.\n"
                f"추가 매수는 {add_price:,.0f}원 부근 눌림에서 고려하세요.\n"
                f"\U0001f4aa 계속 들고 가세요!"
            ),
            target_price=target,
            stop_loss=stop,
            add_buy_price=add_price,
            trailing_stop_pct=trailing_pct,
            profit_pct=profit_pct,
        )

    elif profit_pct >= 0:
        # Category B: 0~5%
        target = round(current_price * 1.05, 0)
        stop = round(avg_price * 0.95, 0)

        return DiagnosisResult(
            ticker=ticker,
            name=name,
            diagnosis="B",
            action="hold",
            message=(
                f"\U0001f7e1 {USER_NAME}, {name} 좀 더 지켜보세요.\n"
                f"현재 {profit_pct:+.1f}% 수익 구간입니다.\n"
                f"\U0001f4c8 상승 시나리오: {target:,.0f}원까지 기대할 수 있어요.\n"
                f"\U0001f4c9 하락 시나리오: {stop:,.0f}원이 손절 라인입니다.\n"
                f"\u23f3 조금 더 지켜보세요."
            ),
            target_price=target,
            stop_loss=stop,
            add_buy_price=0.0,
            trailing_stop_pct=0.0,
            profit_pct=profit_pct,
        )

    elif profit_pct >= -5.0:
        # Category C: -5~0%
        add_price = round(current_price * 0.97, 0)
        stop = round(avg_price * 0.93, 0)
        target = round(avg_price * 1.03, 0)

        return DiagnosisResult(
            ticker=ticker,
            name=name,
            diagnosis="C",
            action="add",
            message=(
                f"\U0001f7e0 {USER_NAME}, {name} 조금 더 기다려보세요.\n"
                f"현재 {profit_pct:+.1f}%로 아직 손절 구간은 아닙니다.\n"
                f"\U0001f4b0 {add_price:,.0f}원에서 추가 매수 고려하세요.\n"
                f"\U0001f6d1 {stop:,.0f}원 아래로 내려가면 손절을 고려해야 합니다.\n"
                f"\U0001f91e 반등 가능성이 있어요."
            ),
            target_price=target,
            stop_loss=stop,
            add_buy_price=add_price,
            trailing_stop_pct=0.0,
            profit_pct=profit_pct,
        )

    else:
        # Category D: below -5%
        stop = round(current_price * 0.97, 0)
        target = round(avg_price * 1.0, 0)

        # Simple heuristic: check RSI for rebound potential
        rsi = 50.0
        if tech_data:
            rsi = tech_data.get("rsi", 50.0)

        if rsi < 30:
            # Oversold, potential rebound
            action = "hold"
            msg = (
                f"\U0001f534 {USER_NAME}, {name} 힘든 구간이에요.\n"
                f"현재 {profit_pct:+.1f}% 손실이지만 RSI {rsi:.1f}로 과매도입니다.\n"
                f"\U0001f4aa 반등 가능성이 있으니 버티세요.\n"
                f"\U0001f6d1 다만 {stop:,.0f}원 아래로 가면 재검토 필요합니다.\n"
                f"\U0001f440 외국인/기관 수급 전환을 지켜보세요."
            )
        else:
            action = "stop_loss"
            msg = (
                f"\U0001f6a8 {USER_NAME}, {name} 손절을 고려하세요.\n"
                f"현재 {profit_pct:+.1f}% 손실이고 반등 시그널이 약합니다.\n"
                f"\U0001f6d1 손절하시고 다음 기회를 노리는 게 나을 수 있어요.\n"
                f"손절 후 반등하더라도 멘탈 관리가 더 중요합니다.\n"
                f"\U0001f4aa 다음에 더 좋은 기회가 옵니다."
            )

        return DiagnosisResult(
            ticker=ticker,
            name=name,
            diagnosis="D",
            action=action,
            message=msg,
            target_price=target,
            stop_loss=stop,
            add_buy_price=0.0,
            trailing_stop_pct=0.0,
            profit_pct=profit_pct,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def diagnose_holding(
    holding_dict: dict[str, Any],
    tech_data: dict[str, Any] | None,
    flow_data: dict[str, Any] | None,
    anthropic_key: str,
) -> DiagnosisResult:
    """Diagnose a single holding via Claude API with rule-based fallback.

    Args:
        holding_dict: Holding info with keys: name, ticker, avg_price (or buy_price),
                      current_price, profit_pct (or pnl_pct).
        tech_data: Technical indicator dict with keys: rsi, macd_histogram,
                   macd_signal_cross, ema_50, ema_200, bb_pctb, sector.
        flow_data: Flow data dict with keys: foreign_net_buy_days,
                   institution_net_buy_days.
        anthropic_key: Anthropic API key.

    Returns:
        DiagnosisResult with diagnosis category, action, and message.
    """
    ticker = holding_dict.get("ticker", "")
    name = holding_dict.get("name", "")
    avg_price = holding_dict.get("avg_price", 0) or holding_dict.get("buy_price", 0)
    current_price = holding_dict.get("current_price", 0)
    profit_pct = holding_dict.get("profit_pct", 0) or holding_dict.get("pnl_pct", 0)

    if avg_price > 0 and current_price > 0 and profit_pct == 0:
        profit_pct = round((current_price - avg_price) / avg_price * 100, 2)

    # Try Claude API first
    if anthropic_key:
        prompt = _build_stock_prompt(holding_dict, tech_data, flow_data)
        result = await _call_claude(prompt, anthropic_key)

        if result:
            diagnosis = result.get("diagnosis", "B")
            action = result.get("action", "hold")
            message = result.get("message", "")
            target_price = float(result.get("target_price", 0))
            stop_loss_val = float(result.get("stop_loss", 0))
            add_buy_price = float(result.get("add_buy_price", 0))
            trailing_stop_pct = float(result.get("trailing_stop_pct", 0))

            # Enforce key rule: never suggest selling stocks with +5% or more
            if profit_pct >= 5.0 and action in ("partial_sell", "stop_loss"):
                logger.info(
                    "Overriding Claude action for %s: %s -> hold (profit %.1f%%)",
                    name, action, profit_pct,
                )
                action = "hold"
                diagnosis = "A"

            return DiagnosisResult(
                ticker=ticker,
                name=name,
                diagnosis=diagnosis,
                action=action,
                message=message,
                target_price=target_price,
                stop_loss=stop_loss_val,
                add_buy_price=add_buy_price,
                trailing_stop_pct=trailing_stop_pct,
                profit_pct=profit_pct,
            )

    # Fallback to rule-based diagnosis
    logger.info("Using rule-based fallback for %s", name)
    return _fallback_diagnosis(holding_dict, tech_data)


async def batch_diagnose(
    holdings: list[dict[str, Any]],
    tech_map: dict[str, dict[str, Any]],
    flow_map: dict[str, dict[str, Any]],
    anthropic_key: str,
    max_concurrency: int = 5,
) -> list[DiagnosisResult]:
    """Diagnose multiple holdings concurrently.

    Args:
        holdings: List of holding dicts.
        tech_map: Dict mapping ticker -> technical data dict.
        flow_map: Dict mapping ticker -> flow data dict.
        anthropic_key: Anthropic API key.
        max_concurrency: Maximum concurrent API calls (default 5).

    Returns:
        List of DiagnosisResult in the same order as holdings.
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _diagnose_with_limit(holding: dict[str, Any]) -> DiagnosisResult:
        async with semaphore:
            ticker = holding.get("ticker", "")
            tech_data = tech_map.get(ticker)
            flow_data = flow_map.get(ticker)
            return await diagnose_holding(
                holding, tech_data, flow_data, anthropic_key,
            )

    tasks = [_diagnose_with_limit(h) for h in holdings]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Convert exceptions to fallback results
    final: list[DiagnosisResult] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "Diagnosis failed for %s: %s",
                holdings[i].get("name", "?"),
                result,
            )
            final.append(_fallback_diagnosis(holdings[i]))
        else:
            final.append(result)

    return final


# ---------------------------------------------------------------------------
# Telegram report formatting
# ---------------------------------------------------------------------------

def _won(price: float) -> str:
    """Format price in Korean Won."""
    if price <= 0:
        return "-"
    return f"{price:,.0f}원"


def _diagnosis_emoji(diagnosis: str) -> str:
    """Get emoji for diagnosis category."""
    return {
        "A": "\U0001f7e2",  # green circle
        "B": "\U0001f7e1",  # yellow circle
        "C": "\U0001f7e0",  # orange circle
        "D": "\U0001f534",  # red circle
    }.get(diagnosis, "\u26aa")


def _action_label(action: str) -> str:
    """Get Korean label for action."""
    return {
        "hold": "보유 유지",
        "add": "추가 매수 고려",
        "partial_sell": "일부 익절",
        "stop_loss": "손절 고려",
    }.get(action, action)


def format_diagnosis_report(
    holdings_with_diagnosis: list[tuple[dict[str, Any], DiagnosisResult]],
    summary: dict[str, Any] | None = None,
) -> str:
    """Format a full diagnosis report for Telegram.

    Args:
        holdings_with_diagnosis: List of (holding_dict, DiagnosisResult) tuples.
        summary: Optional summary dict with keys like total_pnl, total_count.

    Returns:
        Formatted string ready for Telegram (no Markdown bold).
    """
    if not holdings_with_diagnosis:
        return (
            f"\U0001f3e5 {USER_NAME}의 보유 종목 진단\n\n"
            "진단할 보유 종목이 없습니다.\n"
            "\U0001f4ca 추천종목에서 매수 후 다시 확인하세요!"
        )

    lines: list[str] = [
        "\u2550" * 22,
        f"\U0001f3e5 {USER_NAME}의 보유 종목 진단",
        "\u2550" * 22,
        "",
    ]

    # Group by diagnosis category
    by_category: dict[str, list[tuple[dict[str, Any], DiagnosisResult]]] = {
        "A": [], "B": [], "C": [], "D": [],
    }
    for holding, diag in holdings_with_diagnosis:
        cat = diag.diagnosis if diag.diagnosis in by_category else "B"
        by_category[cat].append((holding, diag))

    category_headers = {
        "A": "\U0001f7e2 A등급: 수익 구간 (+5% 이상)",
        "B": "\U0001f7e1 B등급: 보합 구간 (0~5%)",
        "C": "\U0001f7e0 C등급: 소폭 하락 (-5~0%)",
        "D": "\U0001f534 D등급: 주의 구간 (-5% 미만)",
    }

    for cat in ["A", "B", "C", "D"]:
        items = by_category[cat]
        if not items:
            continue

        lines.append(category_headers[cat])
        lines.append("\u2500" * 25)

        for holding, diag in items:
            avg_price = holding.get("avg_price", 0) or holding.get("buy_price", 0)
            current_price = holding.get("current_price", 0)
            emoji = _diagnosis_emoji(diag.diagnosis)

            lines.append(
                f"{emoji} {diag.name}  {diag.profit_pct:+.1f}%"
            )
            lines.append(
                f"   {_won(avg_price)} -> {_won(current_price)}"
            )
            lines.append(f"   \U0001f3af {_action_label(diag.action)}")

            if diag.target_price > 0:
                lines.append(f"   \U0001f4c8 목표: {_won(diag.target_price)}")
            if diag.stop_loss > 0:
                lines.append(f"   \U0001f6d1 손절: {_won(diag.stop_loss)}")
            if diag.add_buy_price > 0:
                lines.append(f"   \U0001f4b0 추가매수: {_won(diag.add_buy_price)}")
            if diag.trailing_stop_pct > 0:
                lines.append(
                    f"   \U0001f504 트레일링: 고점 -{diag.trailing_stop_pct:.0f}%"
                )

            lines.append("")

        # Add the Claude message for each holding
        for _holding, diag in items:
            if diag.message:
                lines.append(f"\U0001f4ac {diag.name} 코멘트:")
                lines.append(diag.message)
                lines.append("")

    # Summary section
    if summary:
        lines.append("\u2500" * 25)
        lines.append("\U0001f4ca 포트폴리오 요약")

        total_pnl = summary.get("total_pnl", 0)
        total_count = summary.get("total_count", 0)
        profit_count = summary.get("profit_count", 0)
        loss_count = summary.get("loss_count", 0)

        pnl_emoji = "\U0001f7e2" if total_pnl >= 0 else "\U0001f534"
        lines.append(f"{pnl_emoji} 평균 수익률: {total_pnl:+.1f}%")
        lines.append(f"\U0001f4bc 보유: {total_count}종목 (수익 {profit_count} / 손실 {loss_count})")

        if summary.get("best_stock"):
            lines.append(f"\U0001f947 최고: {summary['best_stock']}")
        if summary.get("worst_stock"):
            lines.append(f"\U0001f6a8 최저: {summary['worst_stock']}")

    lines.extend([
        "",
        "\u2500" * 25,
        "\U0001f916 Powered by Claude AI + K-Quant v3.0",
    ])

    return "\n".join(lines)
