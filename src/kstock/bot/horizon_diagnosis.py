"""Investment-horizon-based account diagnosis - K-Quant v3.5.

Routes each holding to horizon-specific diagnosis logic:
  danta  (1~5일)   : RSI, 볼린저, 거래량, 갭
  dangi  (1~4주)   : 기술적 + 수급 + 이벤트
  junggi (1~6개월) : + 실적 + 정책 + 컨센서스
  janggi (6개월+)  : 재무 + 산업전망 + 정책패러다임 + 장기컨센서스 + 리스크

Also handles margin-purchase detection and risk warnings.
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
# Horizon configuration
# ---------------------------------------------------------------------------

HORIZON_CONFIG: dict[str, dict[str, Any]] = {
    "danta": {
        "label": "단타 (1~5일)",
        "stop": -2,
        "target": 5,
        "trailing": -3,
    },
    "dangi": {
        "label": "단기 (1~4주)",
        "stop": -5,
        "target": 10,
        "trailing": -5,
    },
    "junggi": {
        "label": "중기 (1~6개월)",
        "stop": -8,
        "target": 20,
        "trailing": -8,
    },
    "janggi": {
        "label": "장기 (6개월+)",
        "stop": -15,
        "target": 50,
        "trailing": -15,
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HorizonDiagnosisResult:
    """Result of a horizon-based diagnosis."""

    ticker: str = ""
    name: str = ""
    horizon: str = "default"
    diagnosis: str = "B"        # A, B, C, D
    action: str = "hold"        # hold, add, partial_sell, stop_loss
    message: str = ""
    target_price: float = 0.0
    stop_loss: float = 0.0
    add_buy_price: float = 0.0
    trailing_stop_pct: float = 0.0
    profit_pct: float = 0.0
    is_margin: bool = False
    margin_type: str | None = None
    margin_warning: str = ""


# ---------------------------------------------------------------------------
# Margin detection
# ---------------------------------------------------------------------------

MARGIN_KEYWORDS = {"신용", "유융", "유옹", "담보"}


def detect_margin_purchase(holding: dict[str, Any]) -> tuple[bool, str | None]:
    """Detect if a holding was purchased on margin.

    Checks purchase_type field and name for margin keywords.

    Returns:
        (is_margin, margin_type) tuple.
    """
    purchase_type = str(holding.get("purchase_type", "") or "").strip()

    for kw in MARGIN_KEYWORDS:
        if kw in purchase_type:
            return True, purchase_type

    # Also check name for OCR artifacts
    name = str(holding.get("name", "") or "")
    for kw in MARGIN_KEYWORDS:
        if kw in name:
            return True, kw

    return False, None


def _margin_warning(horizon: str, margin_type: str | None) -> str:
    """Generate margin warning message based on horizon."""
    if horizon == "janggi":
        return (
            "\u26a0\ufe0f 장기 보유와 신용 매수는 양립 불가합니다.\n"
            "신용 만기(90일) 전에 반드시 현금 전환하세요.\n"
            "장기 투자는 현금 매수만 해야 합니다."
        )
    mtype = margin_type or "신용"
    return (
        f"\u26a0\ufe0f {mtype} 매수 종목입니다.\n"
        "만기(90일) 전에 정리가 필요합니다.\n"
        "반대매매 위험에 유의하세요."
    )


# ---------------------------------------------------------------------------
# Horizon-specific prompts for Claude
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BASE = """\
당신은 한국 주식 투자 어드바이저입니다. 사용자 이름은 주호님입니다.

절대 규칙:
1. 잘 올라가고 있는 종목을 헷징 형태로 팔라고 하지 마세요.
2. +5% 이상 수익 종목은 절대 매도 추천 금지. "잘 잡으셨습니다"로 칭찬하세요.
3. 볼드(**) 사용 금지. 이모지와 줄바꿈으로 가독성을 확보하세요.
4. 숫자에는 쉼표를 넣으세요 (예: 58,000원).
5. 주호님이라고 불러주세요.

응답은 반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트 없이 JSON만 출력하세요:
{
  "diagnosis": "A/B/C/D 중 하나",
  "action": "hold/add/partial_sell/stop_loss 중 하나",
  "message": "주호님에게 보내는 분석 메시지 (이모지 포함, 볼드 금지, 숫자 쉼표 포함)",
  "target_price": 목표가(숫자),
  "stop_loss": 손절가(숫자),
  "add_buy_price": 추가매수가(숫자, 해당없으면 0),
  "trailing_stop_pct": 트레일링스탑퍼센트(숫자)
}
"""

_DANTA_CONTEXT = """\
투자 시계: 단타 (1~5일)
분석 관점: 기술적 단기 시그널 위주. RSI 과매수/과매도, 볼린저밴드 이탈, 거래량 급증, 갭 발생 여부를 중심으로 판단.
손절 기준: -2%, 목표 수익: +5%, 트레일링 스탑: -3%
장기 재무분석이나 산업전망은 불필요합니다. 오직 기술적 지표와 단기 수급만 참고하세요.
"""

_DANGI_CONTEXT = """\
투자 시계: 단기 (1~4주)
분석 관점: 기술적 분석 + 수급 패턴 + 이벤트 드리븐. 외국인/기관 수급 전환, 실적 서프라이즈, 목표가 상향 체인을 분석.
손절 기준: -5%, 목표 수익: +10%, 트레일링 스탑: -5%
장기 재무나 산업전망보다는 수급과 이벤트 중심으로 판단하세요.
"""

_JUNGGI_CONTEXT = """\
투자 시계: 중기 (1~6개월)
분석 관점: 실적 추이 + 정책 수혜 + 증권사 컨센서스 + 기술적 추세. 분기 실적 방향성, 정부 정책 수혜 여부, 컨센서스 목표가 추이를 종합 판단.
손절 기준: -8%, 목표 수익: +20%, 트레일링 스탑: -8%
단기 RSI보다는 추세와 펀더멘털 변화를 중심으로 분석하세요.
"""

_JANGGI_CONTEXT = """\
투자 시계: 장기 (6개월+)
분석 관점: 재무 건전성 + 산업 전망 + 정책 패러다임 + 장기 컨센서스 + 리스크.
매출 CAGR, 영업이익률 추이, ROE, 부채비율 등 재무 지표 분석.
산업 성장률(TAM), 정책 수혜 타임라인, 증권사 장기 목표가를 종합 판단.
손절 기준: -15%, 목표 수익: +50%, 트레일링 스탑: -15%
단기 기술적 지표(RSI, 볼린저 등)는 무시하세요. 펀더멘털과 장기 성장성만 분석하세요.

출력 형식 가이드:
- [산업 전망]: 글로벌/국내 시장 규모, 성장률
- [정책 수혜 타임라인]: 향후 정책 이벤트
- [재무 성장 궤적]: 매출 CAGR, 영업이익률 추이
- [증권사 장기 컨센서스]: 평균 목표가, 매수 비율
- [리스크]: 산업/기업 고유 리스크 요인
"""

HORIZON_PROMPTS = {
    "danta": _DANTA_CONTEXT,
    "dangi": _DANGI_CONTEXT,
    "junggi": _JUNGGI_CONTEXT,
    "janggi": _JANGGI_CONTEXT,
}


def build_horizon_prompt(
    holding: dict[str, Any],
    horizon: str,
    extra_data: dict[str, Any] | None = None,
) -> str:
    """Build a per-stock prompt enriched with horizon context and optional data."""
    name = holding.get("name", "")
    ticker = holding.get("ticker", "")
    avg_price = holding.get("avg_price", 0) or holding.get("buy_price", 0)
    current_price = holding.get("current_price", 0)
    profit_pct = holding.get("profit_pct", 0) or holding.get("pnl_pct", 0)

    if avg_price > 0 and current_price > 0 and profit_pct == 0:
        profit_pct = round((current_price - avg_price) / avg_price * 100, 2)

    cfg = HORIZON_CONFIG.get(horizon, HORIZON_CONFIG["dangi"])

    parts = [
        HORIZON_PROMPTS.get(horizon, _DANGI_CONTEXT),
        "",
        f"종목: {name} ({ticker})",
        f"평균매수가: {avg_price:,.0f}원",
        f"현재가: {current_price:,.0f}원",
        f"수익률: {profit_pct:+.2f}%",
        f"손절 기준: {cfg['stop']}%, 목표: +{cfg['target']}%, 트레일링: {cfg['trailing']}%",
    ]

    if extra_data:
        # Technical indicators
        if "rsi" in extra_data:
            parts.append(f"RSI: {extra_data['rsi']:.1f}")
        if "bb_pctb" in extra_data:
            parts.append(f"볼린저밴드 %B: {extra_data['bb_pctb']:.2f}")
        if "macd_histogram" in extra_data:
            macd_cross = extra_data.get("macd_signal_cross", 0)
            status = "골든크로스" if macd_cross == 1 else "데드크로스" if macd_cross == -1 else "중립"
            parts.append(f"MACD: {extra_data['macd_histogram']:.4f} ({status})")

        # Supply/demand
        if "foreign_net" in extra_data:
            parts.append(f"외국인 순매수: {extra_data['foreign_net']:+,.0f}")
        if "institution_net" in extra_data:
            parts.append(f"기관 순매수: {extra_data['institution_net']:+,.0f}")

        # Gap / breakout
        if "gap_type" in extra_data:
            parts.append(f"갭: {extra_data['gap_type']} ({extra_data.get('gap_pct', 0):+.1f}%)")
        if "breakout" in extra_data:
            parts.append(f"변동성 돌파: {extra_data['breakout']}")

        # Events
        if "events" in extra_data:
            for ev in extra_data["events"]:
                parts.append(f"이벤트: {ev}")

        # Consensus
        if "avg_target_price" in extra_data:
            upside = extra_data.get("upside_pct", 0)
            parts.append(f"증권사 평균 목표가: {extra_data['avg_target_price']:,.0f}원 (상승여력 {upside:+.1f}%)")

        # Financial
        if "revenue_cagr" in extra_data:
            parts.append(f"매출 CAGR(3년): {extra_data['revenue_cagr']:.1f}%")
        if "op_margin" in extra_data:
            parts.append(f"영업이익률: {extra_data['op_margin']:.1f}%")
        if "roe" in extra_data:
            parts.append(f"ROE: {extra_data['roe']:.1f}%")
        if "debt_ratio" in extra_data:
            parts.append(f"부채비율: {extra_data['debt_ratio']:.1f}%")

        # Policy
        if "policy_summary" in extra_data:
            parts.append(f"정책: {extra_data['policy_summary']}")

    parts.append("")
    parts.append("위 데이터를 기반으로 진단해주세요.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude API call (reused pattern from diagnosis.py)
# ---------------------------------------------------------------------------

async def _call_claude_horizon(
    prompt: str,
    horizon: str,
    anthropic_key: str,
    timeout_sec: float = 30.0,
) -> dict[str, Any] | None:
    """Call Claude API with horizon-specific system prompt."""
    system = _SYSTEM_PROMPT_BASE
    if horizon in HORIZON_PROMPTS:
        system = _SYSTEM_PROMPT_BASE + "\n" + HORIZON_PROMPTS[horizon]

    headers = {
        "x-api-key": anthropic_key,
        "anthropic-version": CLAUDE_API_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 800,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(CLAUDE_API_URL, headers=headers, json=payload)

            if resp.status_code != 200:
                logger.warning(
                    "Claude API returned %d: %s", resp.status_code, resp.text[:200],
                )
                return None

            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            text = text.strip()
            if text.startswith("```"):
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
# Gather extra data per horizon
# ---------------------------------------------------------------------------

def _gather_danta_data(
    holding: dict[str, Any],
    tech_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gather RSI, bollinger, gap, breakout data for danta."""
    data: dict[str, Any] = {}
    if tech_data:
        for key in ("rsi", "bb_pctb", "macd_histogram", "macd_signal_cross",
                     "volume_ratio"):
            if key in tech_data:
                data[key] = tech_data[key]
    return data


def _gather_dangi_data(
    holding: dict[str, Any],
    tech_data: dict[str, Any] | None = None,
    flow_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gather technical + supply/demand + events for dangi."""
    data = _gather_danta_data(holding, tech_data)
    if flow_data:
        for key in ("foreign_net", "institution_net", "retail_net"):
            if key in flow_data:
                data[key] = flow_data[key]
    return data


def _gather_junggi_data(
    holding: dict[str, Any],
    tech_data: dict[str, Any] | None = None,
    flow_data: dict[str, Any] | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Gather dangi data + consensus + policy for junggi."""
    data = _gather_dangi_data(holding, tech_data, flow_data)
    ticker = holding.get("ticker", "")

    if db:
        # Consensus
        try:
            from kstock.signal.consensus_tracker import compute_consensus
            reports = []
            consensus_rows = getattr(db, "get_reports_for_ticker", lambda t: [])(ticker)
            if consensus_rows:
                current_price = holding.get("current_price", 0)
                consensus = compute_consensus(consensus_rows, current_price)
                if consensus:
                    data["avg_target_price"] = consensus.avg_target_price
                    data["upside_pct"] = consensus.upside_pct
        except Exception:
            pass

        # Policy
        try:
            from kstock.signal.policy_engine import get_policy_summary
            summary = get_policy_summary()
            if summary:
                data["policy_summary"] = summary[:200]
        except Exception:
            pass

    return data


def _gather_janggi_data(
    holding: dict[str, Any],
    tech_data: dict[str, Any] | None = None,
    flow_data: dict[str, Any] | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Gather junggi data + financials for janggi."""
    data = _gather_junggi_data(holding, tech_data, flow_data, db)
    ticker = holding.get("ticker", "")

    if db:
        # Financials
        try:
            fin_rows = getattr(db, "get_financials", lambda t: [])(ticker)
            if fin_rows and len(fin_rows) > 0:
                latest = fin_rows[0] if isinstance(fin_rows, list) else fin_rows
                for key in ("op_margin", "roe", "debt_ratio"):
                    if key in latest and latest[key] is not None:
                        data[key] = latest[key]
        except Exception:
            pass

    return data


_GATHER_FN = {
    "danta": _gather_danta_data,
    "dangi": _gather_dangi_data,
    "junggi": _gather_junggi_data,
    "janggi": _gather_janggi_data,
}


# ---------------------------------------------------------------------------
# Rule-based fallback (horizon-aware)
# ---------------------------------------------------------------------------

def fallback_diagnosis(
    holding: dict[str, Any],
    horizon: str = "default",
    tech_data: dict[str, Any] | None = None,
) -> HorizonDiagnosisResult:
    """Rule-based fallback when Claude API is unavailable.

    Uses horizon-specific stop/target/trailing parameters.
    """
    name = holding.get("name", "")
    ticker = holding.get("ticker", "")
    avg_price = holding.get("avg_price", 0) or holding.get("buy_price", 0)
    current_price = holding.get("current_price", 0)
    profit_pct = holding.get("profit_pct", 0) or holding.get("pnl_pct", 0)

    if avg_price > 0 and current_price > 0 and profit_pct == 0:
        profit_pct = round((current_price - avg_price) / avg_price * 100, 2)

    cfg = HORIZON_CONFIG.get(horizon, HORIZON_CONFIG["dangi"])
    label = cfg["label"]
    stop_pct = abs(cfg["stop"])
    target_pct = cfg["target"]
    trailing_pct = abs(cfg["trailing"])

    is_margin, margin_type = detect_margin_purchase(holding)
    mwarning = _margin_warning(horizon, margin_type) if is_margin else ""

    if profit_pct >= 5.0:
        # Category A: profitable
        target = round(current_price * (1 + target_pct / 100), 0)
        stop = round(current_price * (1 - trailing_pct / 100), 0)
        add_price = round(current_price * 0.97, 0)

        msg = (
            f"\U0001f389 {USER_NAME}, {name} 잘 잡으셨습니다!\n"
            f"{label} 기준 | 현재 {profit_pct:+.1f}% 수익 중이에요.\n"
            f"\U0001f4c8 트레일링 스탑 -{trailing_pct:.0f}% 설정하세요.\n"
            f"\U0001f3af 목표가: {target:,.0f}원 (+{target_pct}%)\n"
            f"\U0001f4aa 계속 들고 가세요!"
        )

        return HorizonDiagnosisResult(
            ticker=ticker, name=name, horizon=horizon,
            diagnosis="A", action="hold", message=msg,
            target_price=target, stop_loss=stop, add_buy_price=add_price,
            trailing_stop_pct=trailing_pct, profit_pct=profit_pct,
            is_margin=is_margin, margin_type=margin_type, margin_warning=mwarning,
        )

    elif profit_pct >= 0:
        # Category B: break-even
        target = round(current_price * (1 + target_pct / 100), 0)
        stop = round(avg_price * (1 - stop_pct / 100), 0)

        msg = (
            f"\U0001f7e1 {USER_NAME}, {name} 좀 더 지켜보세요.\n"
            f"{label} 기준 | 현재 {profit_pct:+.1f}% 수익 구간입니다.\n"
            f"\U0001f4c8 상승 시나리오: {target:,.0f}원까지 기대.\n"
            f"\U0001f6d1 손절 라인: {stop:,.0f}원 (-{stop_pct}%)\n"
            f"\u23f3 조금 더 지켜보세요."
        )

        return HorizonDiagnosisResult(
            ticker=ticker, name=name, horizon=horizon,
            diagnosis="B", action="hold", message=msg,
            target_price=target, stop_loss=stop,
            trailing_stop_pct=0.0, profit_pct=profit_pct,
            is_margin=is_margin, margin_type=margin_type, margin_warning=mwarning,
        )

    elif profit_pct >= -stop_pct:
        # Category C: small loss, not yet stop-loss
        add_price = round(current_price * 0.97, 0)
        stop = round(avg_price * (1 - stop_pct / 100), 0)
        target = round(avg_price * (1 + target_pct / 200), 0)  # half target

        msg = (
            f"\U0001f7e0 {USER_NAME}, {name} 조금 더 기다려보세요.\n"
            f"{label} 기준 | 현재 {profit_pct:+.1f}%로 아직 손절 구간 아닙니다.\n"
            f"\U0001f4b0 {add_price:,.0f}원에서 추가 매수 고려하세요.\n"
            f"\U0001f6d1 {stop:,.0f}원 아래로 가면 손절 고려.\n"
            f"\U0001f91e 반등 가능성이 있어요."
        )

        return HorizonDiagnosisResult(
            ticker=ticker, name=name, horizon=horizon,
            diagnosis="C", action="add", message=msg,
            target_price=target, stop_loss=stop, add_buy_price=add_price,
            trailing_stop_pct=0.0, profit_pct=profit_pct,
            is_margin=is_margin, margin_type=margin_type, margin_warning=mwarning,
        )

    else:
        # Category D: significant loss
        stop = round(current_price * 0.97, 0)
        target = round(avg_price, 0)

        rsi = 50.0
        if tech_data:
            rsi = tech_data.get("rsi", 50.0)

        if rsi < 30:
            action = "hold"
            msg = (
                f"\U0001f534 {USER_NAME}, {name} 힘든 구간이에요.\n"
                f"{label} 기준 | 현재 {profit_pct:+.1f}% 손실이지만 RSI {rsi:.1f}로 과매도입니다.\n"
                f"\U0001f4aa 반등 가능성이 있으니 버티세요.\n"
                f"\U0001f6d1 {stop:,.0f}원 아래로 가면 재검토.\n"
                f"\U0001f440 수급 전환을 지켜보세요."
            )
        else:
            action = "stop_loss"
            msg = (
                f"\U0001f6a8 {USER_NAME}, {name} 손절을 고려하세요.\n"
                f"{label} 기준 | 현재 {profit_pct:+.1f}% 손실이고 반등 시그널이 약합니다.\n"
                f"\U0001f6d1 손절 기준 -{stop_pct}%를 넘었습니다.\n"
                f"손절 후 다음 기회를 노리세요.\n"
                f"\U0001f4aa 다음에 더 좋은 기회가 옵니다."
            )

        return HorizonDiagnosisResult(
            ticker=ticker, name=name, horizon=horizon,
            diagnosis="D", action=action, message=msg,
            target_price=target, stop_loss=stop,
            trailing_stop_pct=0.0, profit_pct=profit_pct,
            is_margin=is_margin, margin_type=margin_type, margin_warning=mwarning,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def diagnose_by_horizon(
    holding: dict[str, Any],
    horizon: str,
    anthropic_key: str = "",
    db: Any = None,
    tech_data: dict[str, Any] | None = None,
    flow_data: dict[str, Any] | None = None,
) -> HorizonDiagnosisResult:
    """Main entry point for horizon-based diagnosis.

    Args:
        holding: Holding dict with name, ticker, avg_price, current_price, profit_pct.
        horizon: One of danta/dangi/junggi/janggi/default.
        anthropic_key: Anthropic API key.
        db: SQLiteStore instance for enriching data.
        tech_data: Technical indicator dict.
        flow_data: Flow data dict.

    Returns:
        HorizonDiagnosisResult with diagnosis, action, message, and margin info.
    """
    ticker = holding.get("ticker", "")
    name = holding.get("name", "")
    profit_pct = holding.get("profit_pct", 0) or holding.get("pnl_pct", 0)
    avg_price = holding.get("avg_price", 0) or holding.get("buy_price", 0)
    current_price = holding.get("current_price", 0)

    if avg_price > 0 and current_price > 0 and profit_pct == 0:
        profit_pct = round((current_price - avg_price) / avg_price * 100, 2)

    is_margin, margin_type = detect_margin_purchase(holding)

    # Gather horizon-specific extra data
    gather_fn = _GATHER_FN.get(horizon)
    extra_data: dict[str, Any] | None = None
    if gather_fn:
        if horizon in ("junggi", "janggi"):
            extra_data = gather_fn(holding, tech_data, flow_data, db)
        elif horizon == "dangi":
            extra_data = gather_fn(holding, tech_data, flow_data)
        else:
            extra_data = gather_fn(holding, tech_data)

    # Try Claude API
    if anthropic_key and horizon != "default":
        prompt = build_horizon_prompt(holding, horizon, extra_data)
        result = await _call_claude_horizon(prompt, horizon, anthropic_key)

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
                action = "hold"
                diagnosis = "A"

            mwarning = _margin_warning(horizon, margin_type) if is_margin else ""

            return HorizonDiagnosisResult(
                ticker=ticker, name=name, horizon=horizon,
                diagnosis=diagnosis, action=action, message=message,
                target_price=target_price, stop_loss=stop_loss_val,
                add_buy_price=add_buy_price, trailing_stop_pct=trailing_stop_pct,
                profit_pct=profit_pct,
                is_margin=is_margin, margin_type=margin_type,
                margin_warning=mwarning,
            )

    # Fallback to rule-based
    logger.info("Using rule-based fallback for %s (horizon=%s)", name, horizon)
    return fallback_diagnosis(holding, horizon, tech_data)


async def batch_diagnose_by_horizon(
    holdings_with_horizons: list[tuple[dict[str, Any], str]],
    anthropic_key: str = "",
    db: Any = None,
    tech_map: dict[str, dict[str, Any]] | None = None,
    flow_map: dict[str, dict[str, Any]] | None = None,
    max_concurrency: int = 5,
) -> list[HorizonDiagnosisResult]:
    """Diagnose multiple holdings with their assigned horizons concurrently.

    Args:
        holdings_with_horizons: List of (holding_dict, horizon) tuples.
        anthropic_key: Anthropic API key.
        db: SQLiteStore instance.
        tech_map: Dict mapping ticker -> technical data.
        flow_map: Dict mapping ticker -> flow data.
        max_concurrency: Max concurrent API calls.

    Returns:
        List of HorizonDiagnosisResult in the same order.
    """
    tech_map = tech_map or {}
    flow_map = flow_map or {}
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _diagnose(holding: dict[str, Any], horizon: str) -> HorizonDiagnosisResult:
        async with semaphore:
            ticker = holding.get("ticker", "")
            return await diagnose_by_horizon(
                holding, horizon, anthropic_key, db,
                tech_data=tech_map.get(ticker),
                flow_data=flow_map.get(ticker),
            )

    tasks = [_diagnose(h, hz) for h, hz in holdings_with_horizons]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final: list[HorizonDiagnosisResult] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "Horizon diagnosis failed for %s: %s",
                holdings_with_horizons[i][0].get("name", "?"), result,
            )
            holding, horizon = holdings_with_horizons[i]
            final.append(fallback_diagnosis(holding, horizon))
        else:
            final.append(result)

    return final


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _won(price: float) -> str:
    """Format price in Korean Won."""
    if price <= 0:
        return "-"
    return f"{price:,.0f}원"


def format_horizon_report(
    results: list[HorizonDiagnosisResult],
) -> str:
    """Format horizon diagnosis results for Telegram.

    No bold (**) usage. Uses emojis and line breaks for readability.
    """
    if not results:
        return (
            f"\U0001f3e5 {USER_NAME}의 보유 종목 진단\n\n"
            "진단할 보유 종목이 없습니다."
        )

    lines: list[str] = [
        "\u2550" * 22,
        f"\U0001f3e5 {USER_NAME}의 투자 시계별 진단",
        "\u2550" * 22,
        "",
    ]

    horizon_emoji = {
        "danta": "\u26a1",
        "dangi": "\U0001f4c8",
        "junggi": "\U0001f4ca",
        "janggi": "\U0001f3e2",
        "default": "\U0001f4cb",
    }

    diagnosis_emoji = {
        "A": "\U0001f7e2",
        "B": "\U0001f7e1",
        "C": "\U0001f7e0",
        "D": "\U0001f534",
    }

    action_label = {
        "hold": "보유 유지",
        "add": "추가 매수 고려",
        "partial_sell": "일부 익절",
        "stop_loss": "손절 고려",
    }

    for r in results:
        cfg = HORIZON_CONFIG.get(r.horizon, {})
        label = cfg.get("label", "기본")
        hz_emoji = horizon_emoji.get(r.horizon, "\U0001f4cb")
        d_emoji = diagnosis_emoji.get(r.diagnosis, "\u26aa")

        lines.append(f"{hz_emoji} {r.name} ({r.ticker}) - {label}")
        lines.append(
            f"   수익률: {r.profit_pct:+.1f}% | "
            f"{_won(r.stop_loss if r.stop_loss > 0 else 0)} -> {_won(r.target_price)}"
        )
        lines.append(f"   {d_emoji} {action_label.get(r.action, r.action)}")
        lines.append("")

        if r.message:
            # Indent message lines
            for mline in r.message.split("\n"):
                lines.append(f"   {mline}")
            lines.append("")

        if r.target_price > 0:
            lines.append(f"   \U0001f4c8 목표: {_won(r.target_price)}")
        if r.stop_loss > 0:
            lines.append(f"   \U0001f6d1 손절: {_won(r.stop_loss)}")
        if r.trailing_stop_pct > 0:
            lines.append(f"   \U0001f504 트레일링: 고점 -{r.trailing_stop_pct:.0f}%")

        if r.margin_warning:
            lines.append("")
            for wline in r.margin_warning.split("\n"):
                lines.append(f"   {wline}")

        lines.append("\u2500" * 25)

    return "\n".join(lines)
