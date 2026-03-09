"""3-round structured AI debate engine for K-Quant v9.4.

Round 1: 독립 분석 — 4 매니저가 병렬로 독립 의견 제시 (Haiku)
Round 2: 상호 반론 — 각 매니저가 다른 3명 의견을 보고 수정/고수 (Haiku)
Round 3: 최종 종합 — Sonnet이 전체 토론 종합하여 합의/분쟁 판결

비용: 종목당 9 API calls (~$0.01)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# ── 데이터 클래스 ────────────────────────────────────────────

@dataclass
class Opinion:
    """한 매니저의 의견."""
    manager_key: str = ""
    manager_name: str = ""
    emoji: str = ""
    action: str = "관망"       # 매수/매도/관망/홀딩
    confidence: float = 0.5   # 0~1
    reasoning: str = ""
    price_target: float = 0   # 목표가 (원)
    stop_loss: float = 0      # 손절가 (원)
    changed: bool = False     # Round 2에서 의견 변경 여부
    previous_action: str = "" # Round 2에서 이전 의견


@dataclass
class DebateResult:
    """전체 토론 결과."""
    ticker: str = ""
    name: str = ""
    round1_opinions: list[Opinion] = field(default_factory=list)
    round2_opinions: list[Opinion] = field(default_factory=list)
    final_verdict: str = "관망"
    confidence: float = 0.0
    consensus_level: str = "분쟁"  # 강한합의/약한합의/분쟁
    price_target: float = 0
    stop_loss: float = 0
    key_arguments: list[str] = field(default_factory=list)
    dissenting_view: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(KST))
    pattern_summary: str = ""   # 패턴 매칭 요약
    api_calls: int = 0
    error: str = ""


# ── 매니저별 Round 1 시스템 프롬프트 ─────────────────────────

_R1_SYSTEM = (
    "너는 {name}이다. {title}의 관점에서만 분석하라.\n"
    "{persona_summary}\n"
    "호칭: 주호님. 볼드(**) 금지.\n"
    "제공된 데이터만 사용. 학습 데이터 가격 절대 금지.\n\n"
    "반드시 아래 JSON 형식으로만 답하라:\n"
    '{{"action": "매수|매도|관망|홀딩", '
    '"confidence": 0.0~1.0, '
    '"reasoning": "2~3줄 핵심 근거", '
    '"price_target": 목표가(숫자), '
    '"stop_loss": 손절가(숫자)}}'
)

_R2_SYSTEM = (
    "너는 {name}이다. {title}의 관점에서 다른 매니저들의 의견을 검토하라.\n"
    "{persona_summary}\n"
    "호칭: 주호님. 볼드(**) 금지.\n\n"
    "다른 매니저들의 Round 1 의견:\n{others_opinions}\n\n"
    "너의 Round 1 의견: {my_action} (근거: {my_reasoning})\n\n"
    "다른 의견을 검토한 후, 의견을 유지하거나 수정하라.\n"
    "반드시 아래 JSON 형식으로만 답하라:\n"
    '{{"action": "매수|매도|관망|홀딩", '
    '"changed": true|false, '
    '"confidence": 0.0~1.0, '
    '"reasoning": "2~3줄 (수정 사유 또는 고수 사유)"}}'
)

_R3_SYSTEM = (
    "너는 K-Quant 수석 전략가이다. 4명의 투자 매니저가 2라운드에 걸쳐 토론한 결과를 종합하라.\n"
    "호칭: 주호님. 볼드(**) 금지. 이모지 사용 가능.\n"
    "제공된 토론 데이터만 사용. 학습 데이터 가격 절대 금지.\n\n"
    "## 판단 기준\n"
    "- 3명 이상 동일 의견: 강한합의\n"
    "- 2명 동일 + 수정된 의견 포함: 약한합의\n"
    "- 2:2 분열: 분쟁 (관망 추천)\n"
    "- Round 2에서 의견을 변경한 매니저의 논거에 가중치 부여 (재검토 결과이므로)\n\n"
    "## 가격 목표\n"
    "- 매니저들의 목표가/손절가 가중평균 산출\n"
    "- 기술적 지지/저항 데이터 참고\n\n"
    "반드시 아래 JSON 형식으로만 답하라:\n"
    '{{"verdict": "매수|매도|관망|홀딩", '
    '"confidence": 0.0~1.0, '
    '"consensus_level": "강한합의|약한합의|분쟁", '
    '"price_target": 목표가(숫자), '
    '"stop_loss": 손절가(숫자), '
    '"key_arguments": ["논거1", "논거2", "논거3"], '
    '"dissenting_view": "소수 반대 의견 1줄"}}'
)


# ── 매니저 요약 (debate_engine 전용, 간결한 버전) ────────────

_MANAGER_SUMMARY = {
    "scalp": {
        "name": "제시 리버모어",
        "emoji": "⚡",
        "title": "단타 매니저",
        "persona_summary": (
            "추세 추종, 거래량 분석, 피벗 포인트 매매 전문.\n"
            "손절 -3%, 목표 +5~8% (1~3일).\n"
            "거래량 급증, RSI 40~65, 20일선 돌파를 중시."
        ),
    },
    "swing": {
        "name": "윌리엄 오닐",
        "emoji": "🔥",
        "title": "스윙 매니저",
        "persona_summary": (
            "CAN SLIM 기반 성장주 스윙. EPS 성장 ≥25%, RS 상위 20%.\n"
            "손절 -7~8%, 목표 +20~25% (1~4주).\n"
            "차트 패턴(컵앤핸들, 더블바텀), 기관 수급을 중시."
        ),
    },
    "position": {
        "name": "피터 린치",
        "emoji": "📊",
        "title": "포지션 매니저",
        "persona_summary": (
            "PEG<1 성장주, 린치 6분류, 스토리텔링 투자.\n"
            "손절 -12%, 목표 +30~50% (1~6개월).\n"
            "PEG, ROE ≥15%, 매출 성장률, 투자 스토리를 중시."
        ),
    },
    "long_term": {
        "name": "워렌 버핏",
        "emoji": "💎",
        "title": "장기 매니저",
        "persona_summary": (
            "가치투자, 경제적 해자, 내재가치 대비 안전마진 30%+ 요구.\n"
            "손절 -20%, 목표 +30~80% (1년+).\n"
            "ROE ≥15% 5년, FCF, 배당 성장, 경쟁 우위를 중시."
        ),
    },
}


# ── AI API 호출 ──────────────────────────────────────────────

async def _call_ai(
    system: str,
    user: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 300,
    api_key: str = "",
) -> str:
    """Anthropic API 호출 (v9.6.3: 지수 백오프 재시도)."""
    if not api_key:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return '{"error": "API 키 없음"}'

    max_retries = 2
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": max_tokens,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                )
                if resp.status_code == 200:
                    return resp.json()["content"][0]["text"].strip()
                # 5xx/429 → 재시도, 4xx → 즉시 실패
                if resp.status_code >= 500 or resp.status_code == 429:
                    if attempt < max_retries - 1:
                        delay = 1.5 * (attempt + 1)
                        logger.info("AI call retry %d/%d (status=%d), wait %.1fs",
                                    attempt + 1, max_retries, resp.status_code, delay)
                        await asyncio.sleep(delay)
                        continue
                logger.warning("AI call failed: status=%d body=%s", resp.status_code, resp.text[:200])
                return '{"error": "API 호출 실패"}'
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                delay = 1.5 * (attempt + 1)
                logger.info("AI call retry %d/%d (%s), wait %.1fs",
                            attempt + 1, max_retries, type(e).__name__, delay)
                await asyncio.sleep(delay)
                continue
            logger.warning("AI call exception after retries: %s", e)
            return f'{{"error": "{e}"}}'
        except Exception as e:
            logger.warning("AI call exception: %s", e)
            return f'{{"error": "{e}"}}'
    return '{"error": "max retries exceeded"}'


def _parse_json(text: str) -> dict:
    """AI 응답에서 JSON 추출 (v9.6.1: 강화된 파싱).

    1단계: markdown 코드펜스 제거 후 파싱
    2단계: 균형 중괄호 매칭으로 가장 큰 JSON 블록 추출
    3단계: 전체 텍스트 직접 파싱
    4단계: 키워드 fallback (reasoning에서 JSON 잔여물 제거)
    """
    # 1단계: markdown ```json ... ``` 코드펜스 제거
    fence_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # 2단계: 균형 중괄호 매칭 (중첩 지원)
    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    # 3단계: 전체 텍스트 직접 파싱
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 4단계: 키워드 fallback — reasoning에서 JSON 잔여물 제거
    action = "관망"
    for kw in ("매수", "매도", "홀딩"):
        if kw in text:
            action = kw
            break
    # JSON/코드펜스 제거하여 깨끗한 reasoning 추출
    clean = re.sub(r'```(?:json)?.*?```', '', text, flags=re.DOTALL)
    clean = re.sub(r'\{[^}]*$', '', clean)  # 미완결 JSON 제거
    clean = re.sub(r'\{.*?\}', '', clean, flags=re.DOTALL)  # 남은 JSON 블록 제거
    clean = clean.strip()
    if not clean:
        clean = "분석 결과 파싱 실패 — 원문 참조"
    return {"action": action, "confidence": 0.5, "reasoning": clean[:200], "error": "JSON 파싱 실패"}


# ── 메인 토론 엔진 ───────────────────────────────────────────

class DebateEngine:
    """3라운드 구조화 토론 엔진."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")

    async def run_debate(
        self,
        ticker: str,
        name: str,
        stock_data: str = "",
        market_context: str = "",
        pattern_summary: str = "",
        price_target_data: str = "",
        shock_context: str = "",
    ) -> DebateResult:
        """3라운드 토론 실행.

        Args:
            ticker: 종목 코드
            name: 종목명
            stock_data: 주가/지표/수급 데이터 텍스트
            market_context: 시장 상황 텍스트
            pattern_summary: 패턴 매칭 결과 (Phase 2)
            price_target_data: 가격 목표 데이터 (Phase 3)
            shock_context: v10.3 매크로 쇼크 컨텍스트
        """
        result = DebateResult(ticker=ticker, name=name)

        if not self._api_key:
            result.error = "API 키 없음"
            return result

        try:
            # ── Round 1: 독립 분석 ────────────────
            context = self._build_context(
                ticker, name, stock_data, market_context,
                pattern_summary, price_target_data,
                shock_context,
            )
            r1 = await self._round1_independent(ticker, name, context)
            result.round1_opinions = r1
            result.api_calls += 4

            # ── Round 2: 상호 반론 ────────────────
            r2 = await self._round2_rebuttal(r1, context)
            result.round2_opinions = r2
            result.api_calls += 4

            # ── Round 3: 최종 종합 ────────────────
            synthesis = await self._round3_synthesis(r1, r2, context)
            result.api_calls += 1

            result.final_verdict = synthesis.get("verdict", "관망")
            result.confidence = float(synthesis.get("confidence", 0.5))
            result.consensus_level = synthesis.get("consensus_level", "분쟁")
            result.price_target = float(synthesis.get("price_target", 0))
            result.stop_loss = float(synthesis.get("stop_loss", 0))
            result.key_arguments = synthesis.get("key_arguments", [])
            result.dissenting_view = synthesis.get("dissenting_view", "")
            result.pattern_summary = pattern_summary

        except Exception as e:
            logger.error("DebateEngine.run_debate error: %s", e, exc_info=True)
            result.error = str(e)

        return result

    def _build_context(
        self, ticker: str, name: str,
        stock_data: str, market_context: str,
        pattern_summary: str, price_target_data: str,
        shock_context: str = "",
    ) -> str:
        """토론용 통합 컨텍스트 구성."""
        parts = [f"종목: {name} ({ticker})"]
        if shock_context:
            parts.append(f"\n[글로벌 매크로 쇼크 상태]\n{shock_context}")
        if stock_data:
            parts.append(f"\n[주가/지표 데이터]\n{stock_data}")
        if market_context:
            parts.append(f"\n[시장 상황]\n{market_context}")
        if pattern_summary:
            parts.append(f"\n[과거 패턴 매칭]\n{pattern_summary}")
        if price_target_data:
            parts.append(f"\n[기술적 가격 목표]\n{price_target_data}")
        return "\n".join(parts)

    async def _round1_independent(
        self, ticker: str, name: str, context: str,
    ) -> list[Opinion]:
        """Round 1: 4매니저 독립 분석 (병렬)."""

        async def _get_opinion(mgr_key: str) -> Opinion:
            mgr = _MANAGER_SUMMARY[mgr_key]
            system = _R1_SYSTEM.format(
                name=mgr["name"],
                title=mgr["title"],
                persona_summary=mgr["persona_summary"],
            )
            user = f"{context}\n\n이 종목에 대한 {mgr['name']}의 견해와 액션을 JSON으로 제시하세요."

            raw = await _call_ai(system, user, max_tokens=300, api_key=self._api_key)
            data = _parse_json(raw)

            return Opinion(
                manager_key=mgr_key,
                manager_name=mgr["name"],
                emoji=mgr["emoji"],
                action=data.get("action", "관망"),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", raw[:200]),
                price_target=float(data.get("price_target", 0)),
                stop_loss=float(data.get("stop_loss", 0)),
            )

        opinions = await asyncio.gather(
            *[_get_opinion(k) for k in ("scalp", "swing", "position", "long_term")],
            return_exceptions=True,
        )
        # v9.6.3: Exception 발생한 매니저는 기본 관망 의견으로 대체
        safe_opinions = []
        for i, op in enumerate(opinions):
            if isinstance(op, Exception):
                mgr_key = ("scalp", "swing", "position", "long_term")[i]
                mgr = _MANAGER_SUMMARY[mgr_key]
                logger.warning("R1 %s failed: %s", mgr["name"], op)
                safe_opinions.append(Opinion(
                    manager_key=mgr_key, manager_name=mgr["name"],
                    emoji=mgr["emoji"], action="관망", confidence=0.3,
                    reasoning="분석 실패 — 관망 기본값",
                    price_target=0, stop_loss=0,
                ))
            else:
                safe_opinions.append(op)
        return safe_opinions

    async def _round2_rebuttal(
        self, round1: list[Opinion], context: str,
    ) -> list[Opinion]:
        """Round 2: 다른 의견 검토 후 수정/고수."""

        async def _rebuttal(my_opinion: Opinion) -> Opinion:
            mgr = _MANAGER_SUMMARY[my_opinion.manager_key]

            # 다른 매니저들의 의견 포맷
            others = []
            for op in round1:
                if op.manager_key != my_opinion.manager_key:
                    others.append(
                        f"- {op.emoji} {op.manager_name}: {op.action} "
                        f"(확신 {op.confidence:.0%}) — {op.reasoning}"
                    )
            others_text = "\n".join(others)

            system = _R2_SYSTEM.format(
                name=mgr["name"],
                title=mgr["title"],
                persona_summary=mgr["persona_summary"],
                others_opinions=others_text,
                my_action=my_opinion.action,
                my_reasoning=my_opinion.reasoning,
            )
            user = f"{context}\n\n다른 매니저 의견을 검토 후, 최종 견해를 JSON으로 제시하세요."

            raw = await _call_ai(system, user, max_tokens=250, api_key=self._api_key)
            data = _parse_json(raw)

            changed = data.get("changed", False)
            new_action = data.get("action", my_opinion.action)

            return Opinion(
                manager_key=my_opinion.manager_key,
                manager_name=my_opinion.manager_name,
                emoji=my_opinion.emoji,
                action=new_action,
                confidence=float(data.get("confidence", my_opinion.confidence)),
                reasoning=data.get("reasoning", ""),
                price_target=my_opinion.price_target,
                stop_loss=my_opinion.stop_loss,
                changed=changed or (new_action != my_opinion.action),
                previous_action=my_opinion.action,
            )

        opinions = await asyncio.gather(
            *[_rebuttal(op) for op in round1],
            return_exceptions=True,
        )
        # v9.6.3: R2 실패 시 R1 의견 유지
        safe_opinions = []
        for op_r1, op_r2 in zip(round1, opinions):
            if isinstance(op_r2, Exception):
                logger.warning("R2 %s failed: %s", op_r1.manager_name, op_r2)
                safe_opinions.append(op_r1)
            else:
                safe_opinions.append(op_r2)
        return safe_opinions

    async def _round3_synthesis(
        self, round1: list[Opinion], round2: list[Opinion], context: str,
    ) -> dict:
        """Round 3: Sonnet이 전체 토론 종합."""
        # 토론 내역 포맷
        r1_text = "\n".join(
            f"- {op.emoji} {op.manager_name}: {op.action} "
            f"(확신 {op.confidence:.0%}) — {op.reasoning}"
            for op in round1
        )
        r2_text = "\n".join(
            f"- {op.emoji} {op.manager_name}: {op.action} "
            f"({'의견변경' if op.changed else '유지'}, "
            f"이전: {op.previous_action}, 확신 {op.confidence:.0%}) — {op.reasoning}"
            for op in round2
        )

        # 목표가/손절가 데이터
        targets = [op.price_target for op in round2 if op.price_target > 0]
        stops = [op.stop_loss for op in round2 if op.stop_loss > 0]
        target_info = ""
        if targets:
            target_info = f"\n매니저 목표가 범위: {min(targets):,.0f} ~ {max(targets):,.0f}원"
        if stops:
            target_info += f"\n매니저 손절가 범위: {min(stops):,.0f} ~ {max(stops):,.0f}원"

        system = _R3_SYSTEM
        user = (
            f"{context}\n\n"
            f"[Round 1 — 독립 분석]\n{r1_text}\n\n"
            f"[Round 2 — 상호 반론 후]\n{r2_text}\n"
            f"{target_info}\n\n"
            f"위 토론을 종합하여 최종 판결을 JSON으로 내리세요."
        )

        raw = await _call_ai(
            system, user,
            model="claude-sonnet-4-5-20250929",
            max_tokens=500,
            api_key=self._api_key,
        )
        return _parse_json(raw)


# ── 결과 포맷팅 (Telegram용) ─────────────────────────────────

_ACTION_EMOJI = {"매수": "🟢", "매도": "🔴", "관망": "🟡", "홀딩": "🔵"}


def _clean_reasoning(text: str) -> str:
    """reasoning에서 JSON/코드펜스 잔여물 제거."""
    if not text:
        return ""
    # 닫힌 코드펜스 제거: ```json ... ```
    text = re.sub(r'```(?:json)?.*?```', '', text, flags=re.DOTALL)
    # 닫히지 않은 코드펜스도 제거: ```json ... (끝까지)
    text = re.sub(r'```\w*.*$', '', text, flags=re.DOTALL)
    # 남은 ``` 마커 제거
    text = text.replace('```', '')
    # JSON 블록 제거 (완결/미완결 모두)
    text = re.sub(r'\{.*?\}', '', text, flags=re.DOTALL)
    text = re.sub(r'\{[^}]*$', '', text)  # 닫히지 않은 { 제거
    text = re.sub(r'^\s*[\-\*]\s*', '', text)  # 불릿 제거
    text = re.sub(r'"[^"]*"', '', text)  # 남은 JSON 키/값 따옴표 제거
    return text.strip()


def format_debate_telegram(result: DebateResult) -> str:
    """DebateResult → Telegram 텍스트."""
    if result.error:
        return f"토론 오류: {result.error}"

    lines = [
        f"🎙️ {result.name} ({result.ticker}) AI 토론 결과",
        "━" * 20,
        "",
    ]

    # Round 1
    lines.append("📍 Round 1: 독립 분석")
    for op in result.round1_opinions:
        ae = _ACTION_EMOJI.get(op.action, "⚪")
        reason = _clean_reasoning(op.reasoning)[:60] or op.action
        lines.append(f"{ae} {op.manager_name}: {op.action} — {reason}")
    lines.append("")

    # Round 2
    lines.append("📍 Round 2: 상호 반론")
    for op in result.round2_opinions:
        ae = _ACTION_EMOJI.get(op.action, "⚪")
        change_mark = ""
        if op.changed:
            change_mark = f" (수정: {op.previous_action}→{op.action})"
        else:
            change_mark = f" (유지)"
        reason = _clean_reasoning(op.reasoning)[:50] or op.action
        lines.append(f"{ae} {op.manager_name}: {op.action}{change_mark} — {reason}")
    lines.append("")

    # Round 3 최종 종합
    lines.append("📍 Round 3: 최종 종합")
    lines.append("━" * 20)
    ae = _ACTION_EMOJI.get(result.final_verdict, "⚪")
    buy_count = sum(1 for op in result.round2_opinions if op.action == result.final_verdict)
    lines.append(f"📋 판정: {ae} {result.final_verdict} ({result.consensus_level}, {buy_count}/4)")
    lines.append(f"확신도: {result.confidence:.0%}")

    if result.price_target > 0:
        lines.append(f"목표가: {result.price_target:,.0f}원")
    if result.stop_loss > 0:
        lines.append(f"손절가: {result.stop_loss:,.0f}원")
    lines.append("")

    if result.key_arguments:
        lines.append("핵심 논거:")
        for i, arg in enumerate(result.key_arguments[:5], 1):
            lines.append(f"  {i}. {arg}")
        lines.append("")

    if result.dissenting_view:
        lines.append(f"⚠️ 소수 의견: {result.dissenting_view}")

    # 의견 분포 요약
    action_counts: dict[str, int] = {}
    for op in result.round2_opinions:
        action_counts[op.action] = action_counts.get(op.action, 0) + 1
    if action_counts:
        dist = " / ".join(
            f"{_ACTION_EMOJI.get(a, '⚪')}{a} {c}명" for a, c in action_counts.items()
        )
        lines.append(f"\n투표: {dist}")

    return "\n".join(lines)


def format_debate_short(result: DebateResult) -> str:
    """간략한 토론 요약 (브리핑용)."""
    if result.error:
        return f"토론 오류"
    ae = _ACTION_EMOJI.get(result.final_verdict, "⚪")
    return (
        f"{ae} {result.final_verdict} ({result.consensus_level}, "
        f"확신 {result.confidence:.0%})"
    )
