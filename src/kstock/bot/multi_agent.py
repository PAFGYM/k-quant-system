"""Multi-agent stock analysis system (멀티 에이전트 분석).

Routes analysis through 4 specialized agents:
  1. Technical (기술적) - chart patterns, indicators
  2. Fundamental (기본적) - financials, valuation
  3. Sentiment (뉴스/센티먼트) - news, supply/demand
  4. Strategist (전략) - synthesizes all 3

Each sub-agent uses Haiku for cost savings, strategist uses Sonnet.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

AGENTS = {
    "technical": {
        "name": "기술적 분석 에이전트",
        "model": "claude-haiku-4-5-20251001",
        "system_prompt": (
            "당신은 한국 주식 기술적 분석 전문가입니다.\n"
            "주어진 데이터를 바탕으로 기술적 분석만 수행하세요.\n\n"
            "분석 항목:\n"
            "1. 이동평균선 (5/20/60/120일) 배열 상태\n"
            "2. RSI 과매수/과매도 판단\n"
            "3. MACD 시그널 (골든크로스/데드크로스)\n"
            "4. 거래량 추세 (20일 평균 대비)\n"
            "5. 52주 고점/저점 대비 현재 위치\n"
            "6. 지지선/저항선\n\n"
            "반드시 아래 형식으로 출력하세요:\n"
            "기술적 점수: [숫자] (0~100 사이)\n"
            "신호: [강한매수/매수/중립/매도/강한매도]\n"
            "핵심 근거: [2~3줄]\n"
            "주의사항: [1줄]\n\n"
            "데이터가 None인 항목은 '데이터 없음'으로 표기하되, "
            "있는 데이터만으로 분석하세요. 분석 불가라고 하지 마세요."
        ),
    },
    "fundamental": {
        "name": "기본적 분석 에이전트",
        "model": "claude-haiku-4-5-20251001",
        "system_prompt": (
            "당신은 한국 주식 기본적 분석 전문가입니다.\n"
            "주어진 재무 데이터를 바탕으로 기본적 분석만 수행하세요.\n\n"
            "분석 항목:\n"
            "1. PER/PBR 업종 평균 대비\n"
            "2. 매출/영업이익 성장률 추세\n"
            "3. ROE/ROA 추세\n"
            "4. 부채비율 안정성\n"
            "5. 배당 수익률\n"
            "6. 컨센서스 대비 실적 서프라이즈 여부\n\n"
            "반드시 아래 형식으로 출력하세요:\n"
            "기본적 점수: [숫자] (0~100 사이)\n"
            "밸류에이션: [저평가/적정/고평가]\n"
            "핵심 근거: [2~3줄]\n"
            "리스크: [1줄]\n\n"
            "데이터가 None이거나 0인 항목은 '데이터 없음'으로 표기하되, "
            "있는 데이터만으로 분석하세요. 분석 불가라고 하지 마세요."
        ),
    },
    "sentiment": {
        "name": "뉴스/센티먼트 에이전트",
        "model": "claude-haiku-4-5-20251001",
        "system_prompt": (
            "당신은 한국 주식 뉴스 및 시장 심리 분석 전문가입니다.\n"
            "주어진 뉴스와 수급 데이터를 바탕으로 센티먼트 분석만 수행하세요.\n\n"
            "분석 항목:\n"
            "1. 최근 뉴스 감성 (긍정/부정/중립)\n"
            "2. 기관/외인 수급 동향 (5일/20일)\n"
            "3. 공매도 잔고 변화\n"
            "4. 신용잔고 변화\n"
            "5. 섹터 전체 분위기\n"
            "6. 정책/규제 영향\n"
            "7. 글로벌 이벤트 영향\n\n"
            "반드시 아래 형식으로 출력하세요:\n"
            "센티먼트 점수: [숫자] (0~100 사이)\n"
            "시장 심리: [탐욕/낙관/중립/비관/공포]\n"
            "핵심 이슈: [2~3줄]\n"
            "변수: [1줄]\n\n"
            "데이터가 부족하더라도 있는 정보를 바탕으로 반드시 점수를 매기세요."
        ),
    },
    "strategist": {
        "name": "투자 전략 에이전트",
        "model": "claude-sonnet-4-5-20250929",
        "system_prompt": (
            "당신은 K-Quant 수석 투자 전략가입니다.\n"
            "3명의 전문 에이전트 분석 결과를 종합하여 최종 투자 판단을 내리세요.\n\n"
            "판단 기준:\n"
            "- 3개 에이전트 중 2개 이상 일치하면 해당 방향\n"
            "- 기술적+기본적 일치 시 가중치 높음\n"
            "- 센티먼트가 극단적(20 이하 또는 80 이상)이면 반대 신호 주의\n\n"
            "호칭: 반드시 '주호님'\n"
            "볼드(**) 사용 금지\n"
            "이모지 사용 금지\n\n"
            "반드시 아래 형식으로 출력하세요:\n"
            "종합 점수: [숫자] (0~215 사이)\n"
            "판단: [매수/홀딩/매도/관망]\n"
            "확신도: [상/중/하]\n"
            "근거 요약: [3~5줄]\n"
            "액션: [구체적 행동 제안 1~2줄]\n"
            "리스크: [1줄]"
        ),
    },
}

# Signal labels in Korean
SIGNAL_LABELS = ["강한매수", "매수", "중립", "매도", "강한매도"]
MARKET_SENTIMENTS = ["탐욕", "낙관", "중립", "비관", "공포"]
VALUATION_LABELS = ["저평가", "적정", "고평가"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Single agent analysis result."""

    agent_key: str = ""
    agent_name: str = ""
    score: int = 50
    signal: str = "중립"
    summary: str = ""
    raw_response: str = ""


@dataclass
class MultiAgentReport:
    """Combined multi-agent analysis report."""

    ticker: str = ""
    name: str = ""
    price: float = 0.0
    results: dict[str, AgentResult] = field(default_factory=dict)
    strategist_result: AgentResult = field(default_factory=AgentResult)
    combined_score: int = 0
    verdict: str = "관망"
    confidence: str = "하"
    action: str = ""
    risk_note: str = ""
    created_at: str = ""


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------

def format_data_for_agent(agent_key: str, stock_data: dict) -> str:
    """에이전트별 필요 데이터만 추출해서 전달합니다."""
    try:
        if agent_key == "technical":
            return (
                f"이동평균: 5일={stock_data.get('ma5')}, "
                f"20일={stock_data.get('ma20')}, "
                f"60일={stock_data.get('ma60')}, "
                f"120일={stock_data.get('ma120')}\n"
                f"RSI(14): {stock_data.get('rsi')}\n"
                f"MACD: {stock_data.get('macd')}, "
                f"Signal: {stock_data.get('macd_signal')}\n"
                f"거래량: 오늘={stock_data.get('volume')}, "
                f"20일평균={stock_data.get('avg_volume_20')}\n"
                f"52주 고점: {stock_data.get('high_52w')}, "
                f"저점: {stock_data.get('low_52w')}\n"
                f"현재가: {stock_data.get('price')}\n"
                f"최근 5일 종가: {stock_data.get('prices_5d')}"
            )

        elif agent_key == "fundamental":
            return (
                f"PER: {stock_data.get('per')}, "
                f"업종 평균: {stock_data.get('sector_per')}\n"
                f"PBR: {stock_data.get('pbr')}\n"
                f"ROE: {stock_data.get('roe')}\n"
                f"매출 성장률: {stock_data.get('revenue_growth')}\n"
                f"영업이익 성장률: {stock_data.get('op_growth')}\n"
                f"부채비율: {stock_data.get('debt_ratio')}\n"
                f"컨센서스 목표가: {stock_data.get('target_price')}\n"
                f"최근 실적: {stock_data.get('recent_earnings')}"
            )

        elif agent_key == "sentiment":
            return (
                f"최근 뉴스 요약: {stock_data.get('news_summary')}\n"
                f"기관 순매수(5일): {stock_data.get('inst_net_5d')}\n"
                f"외인 순매수(5일): {stock_data.get('foreign_net_5d')}\n"
                f"공매도 잔고 변화: {stock_data.get('short_change')}\n"
                f"신용잔고 변화: {stock_data.get('margin_change')}\n"
                f"섹터 동향: {stock_data.get('sector_trend')}"
            )

        return f"데이터: {stock_data}"

    except Exception as e:
        logger.error("에이전트 데이터 포맷 실패: %s", e, exc_info=True)
        return f"데이터 포맷 오류: {e}"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_SCORE_PATTERN = re.compile(
    r"(?:기술적\s*)?(?:기본적\s*)?(?:센티먼트\s*)?(?:종합\s*)?(?:점수|score)\s*[:\uff1a]?\s*(\d{1,3})",
    re.IGNORECASE,
)
# 폴백: 줄 앞부분에 숫자+점 패턴 (예: "72점", "65/100")
_SCORE_FALLBACK = re.compile(r"(\d{1,3})\s*(?:점|/\s*100)", re.IGNORECASE)
_SIGNAL_PATTERN = re.compile(
    r"(?:신호|signal|판단|밸류에이션|시장\s*심리)\s*[:\uff1a]?\s*"
    r"(강한매수|매수|중립|매도|강한매도|저평가|적정|고평가|탐욕|낙관|비관|공포)",
)


def parse_agent_score(response_text: str) -> int:
    """응답 텍스트에서 점수(0~100)를 추출합니다."""
    try:
        match = _SCORE_PATTERN.search(response_text)
        if match:
            score = int(match.group(1))
            return max(0, min(100, score))
        # 폴백: "72점" 또는 "65/100" 패턴
        fallback = _SCORE_FALLBACK.search(response_text)
        if fallback:
            score = int(fallback.group(1))
            return max(0, min(100, score))
        logger.warning("점수 추출 실패 — 응답 앞 200자: %s", response_text[:200])
        return 50  # default
    except Exception:
        return 50


def parse_agent_signal(response_text: str) -> str:
    """응답 텍스트에서 신호를 추출합니다."""
    try:
        match = _SIGNAL_PATTERN.search(response_text)
        if match:
            return match.group(1)
        return "중립"
    except Exception:
        return "중립"


# ---------------------------------------------------------------------------
# Score synthesis
# ---------------------------------------------------------------------------

# Weights for combining agent scores (must sum to 1.0)
_WEIGHTS = {
    "technical": 0.35,
    "fundamental": 0.30,
    "sentiment": 0.35,
}


def synthesize_scores(
    results: dict[str, AgentResult],
) -> tuple[int, str, str]:
    """3개 에이전트 점수를 종합합니다.

    Returns:
        (combined_score 0~215, verdict, confidence)
    """
    try:
        weighted_sum = 0.0
        scores = []

        for key, weight in _WEIGHTS.items():
            agent = results.get(key)
            score = agent.score if agent else 50
            weighted_sum += score * weight
            scores.append(score)

        # Scale 0~100 → 0~215
        combined_score = int(round(weighted_sum * 2.15))
        combined_score = max(0, min(215, combined_score))

        # Verdict
        if combined_score >= 160:
            verdict = "매수"
        elif combined_score >= 120:
            verdict = "홀딩"
        elif combined_score >= 80:
            verdict = "관망"
        else:
            verdict = "매도"

        # Confidence based on agreement
        if len(scores) >= 3:
            spread = max(scores) - min(scores)
            if spread <= 15:
                confidence = "상"
            elif spread <= 30:
                confidence = "중"
            else:
                confidence = "하"
        else:
            confidence = "하"

        return combined_score, verdict, confidence

    except Exception as e:
        logger.error("점수 종합 실패: %s", e, exc_info=True)
        return 107, "관망", "하"


# ---------------------------------------------------------------------------
# Strategist input
# ---------------------------------------------------------------------------

def build_strategist_input(
    results: dict[str, AgentResult],
    stock_data: dict,
    feedback_data: str = "",
) -> str:
    """전략 에이전트에 전달할 종합 입력을 구성합니다."""
    try:
        lines = []

        for key in ("technical", "fundamental", "sentiment"):
            agent = results.get(key)
            if agent:
                label = {
                    "technical": "기술적 분석",
                    "fundamental": "기본적 분석",
                    "sentiment": "뉴스/센티먼트 분석",
                }.get(key, key)
                lines.append(f"[{label} 결과]")
                lines.append(agent.raw_response or agent.summary or "분석 불가")
                lines.append("")

        if feedback_data:
            lines.append("[과거 피드백 데이터]")
            lines.append(feedback_data)
            lines.append("")

        name = stock_data.get("name", "")
        ticker = stock_data.get("ticker", "")
        price = stock_data.get("price", "N/A")
        lines.append(f"종목: {name} ({ticker})")
        lines.append(f"현재가: {price}원")

        return "\n".join(lines)

    except Exception as e:
        logger.error("전략 입력 구성 실패: %s", e, exc_info=True)
        return "데이터 구성 오류"


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_multi_agent_report(report: MultiAgentReport) -> str:
    """멀티 에이전트 분석 결과를 텔레그램 형식으로 포맷합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
        lines = [
            f"{report.name} ({report.ticker}) 멀티 에이전트 분석",
            f"분석 시각: {now}",
            "",
        ]

        # Sub-agent results
        agent_labels = {
            "technical": "기술적",
            "fundamental": "기본적",
            "sentiment": "센티먼트",
        }
        for key in ("technical", "fundamental", "sentiment"):
            agent = report.results.get(key)
            if agent:
                label = agent_labels.get(key, key)
                lines.append(f"[{label}] {agent.score}점 - {agent.signal}")
                if agent.summary:
                    for line in agent.summary.split("\n")[:2]:
                        lines.append(f"  {line.strip()}")
                lines.append("")

        # Combined result
        lines.append(
            f"[종합 판단] {report.combined_score}/215 - "
            f"{report.verdict} (확신도: {report.confidence})"
        )

        if report.strategist_result.summary:
            for line in report.strategist_result.summary.split("\n")[:4]:
                lines.append(f"  {line.strip()}")

        if report.action:
            lines.append(f"  액션: {report.action}")
        if report.risk_note:
            lines.append(f"  리스크: {report.risk_note}")

        lines.append("")
        lines.append(f"{USER_NAME}, 4개 에이전트 종합 분석 결과입니다.")

        return "\n".join(lines)

    except Exception as e:
        logger.error("멀티 에이전트 리포트 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 멀티 에이전트 분석 결과 생성 중 오류가 발생했습니다."


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def create_empty_report(ticker: str, name: str, price: float) -> MultiAgentReport:
    """API 실패 시 기본 리포트를 생성합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
        results = {}
        for key in ("technical", "fundamental", "sentiment"):
            results[key] = AgentResult(
                agent_key=key,
                agent_name=AGENTS[key]["name"],
                score=50,
                signal="중립",
                summary="데이터 부족으로 분석 불가",
            )

        combined_score, verdict, confidence = synthesize_scores(results)

        return MultiAgentReport(
            ticker=ticker,
            name=name,
            price=price,
            results=results,
            strategist_result=AgentResult(
                agent_key="strategist",
                agent_name=AGENTS["strategist"]["name"],
                score=50,
                signal="관망",
                summary="에이전트 분석 데이터 부족으로 종합 판단 보류",
            ),
            combined_score=combined_score,
            verdict=verdict,
            confidence=confidence,
            action="추가 데이터 확보 후 재분석 필요",
            risk_note="데이터 부족으로 판단 유보",
            created_at=now,
        )

    except Exception as e:
        logger.error("빈 리포트 생성 실패: %s", e, exc_info=True)
        return MultiAgentReport(ticker=ticker, name=name, price=price)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

async def run_multi_agent_analysis(
    ticker: str,
    name: str,
    price: float,
    stock_data: dict,
) -> MultiAgentReport:
    """2개 에이전트(기술적/펀더멘털)를 병렬 호출하여 멀티 분석 수행.

    Args:
        ticker: 종목코드 (예: "005930")
        name: 종목명 (예: "삼성전자")
        price: 현재가
        stock_data: 기술적/재무 데이터 dict

    Returns:
        MultiAgentReport with combined analysis.
    """
    import asyncio
    import os

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, returning empty report")
        return create_empty_report(ticker, name, price)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
    except (ImportError, Exception) as e:
        logger.error("Anthropic client init failed: %s", e)
        return create_empty_report(ticker, name, price)

    async def _call_agent(agent_key: str) -> AgentResult:
        """단일 에이전트 API 호출."""
        agent_config = AGENTS[agent_key]
        data_text = format_data_for_agent(agent_key, stock_data)
        prompt = f"종목: {name} ({ticker})\n현재가: {price:,.0f}원\n\n{data_text}"
        model = agent_config["model"]
        try:
            logger.info("[멀티분석] %s 에이전트 호출 시작 (model=%s)", agent_key, model)
            response = await client.messages.create(
                model=model,
                max_tokens=800,
                system=agent_config["system_prompt"],
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            score = parse_agent_score(raw)
            signal = parse_agent_signal(raw)
            logger.info(
                "[멀티분석] %s 에이전트 완료: score=%d, signal=%s, 응답길이=%d",
                agent_key, score, signal, len(raw),
            )
            return AgentResult(
                agent_key=agent_key,
                agent_name=agent_config["name"],
                score=score,
                signal=signal,
                summary=raw[:300],
                raw_response=raw,
            )
        except Exception as e:
            logger.error(
                "[멀티분석] %s 에이전트 실패: %s (model=%s)",
                agent_key, e, model, exc_info=True,
            )
            return AgentResult(
                agent_key=agent_key,
                agent_name=agent_config["name"],
                score=50,
                signal="중립",
                summary="분석 API 호출 실패 - 잠시 후 재시도",
            )

    # 3개 에이전트 병렬 호출 (기술적 + 펀더멘털 + 센티먼트)
    tech_result, fund_result, sent_result = await asyncio.gather(
        _call_agent("technical"),
        _call_agent("fundamental"),
        _call_agent("sentiment"),
    )

    results = {
        "technical": tech_result,
        "fundamental": fund_result,
        "sentiment": sent_result,
    }

    combined_score, verdict, confidence = synthesize_scores(results)

    # 목표가/손절가 계산
    if verdict == "매수":
        target_pct = 15
        stop_pct = -5
    elif verdict == "매도":
        target_pct = -5
        stop_pct = -10
    else:
        target_pct = 10
        stop_pct = -7

    target_price = price * (1 + target_pct / 100) if price > 0 else 0
    stop_price = price * (1 + stop_pct / 100) if price > 0 else 0

    action = ""
    if verdict == "매수":
        action = f"목표가 {target_price:,.0f}원(+{target_pct}%), 손절가 {stop_price:,.0f}원({stop_pct}%)"
    elif verdict == "매도":
        action = "보유 시 익절 또는 손절 검토"
    else:
        action = f"관망 추천. 진입 시 손절 {stop_price:,.0f}원({stop_pct}%) 설정"

    now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
    return MultiAgentReport(
        ticker=ticker,
        name=name,
        price=price,
        results=results,
        strategist_result=AgentResult(
            agent_key="strategist",
            agent_name="종합 전략",
            score=combined_score,
            signal=verdict,
            summary=(
                f"기술적 {tech_result.score}점 + "
                f"펀더멘털 {fund_result.score}점 + "
                f"센티먼트 {sent_result.score}점 종합"
            ),
        ),
        combined_score=combined_score,
        verdict=verdict,
        confidence=confidence,
        action=action,
        risk_note=f"목표가: {target_price:,.0f}원 / 손절가: {stop_price:,.0f}원",
        created_at=now,
    )


def format_multi_agent_report_v2(report: MultiAgentReport) -> str:
    """멀티 분석 리포트 v2 (이모지 + 구조화 + 3 에이전트)."""
    try:
        now = report.created_at or datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")
        price_str = f"{report.price:,.0f}원" if report.price > 0 else "가격정보 없음"
        lines = [
            f"\U0001f4ca [{report.name}] 멀티 분석 리포트",
            f"현재가: {price_str}",
            "\u2500" * 25,
        ]

        # 기술적 분석 (Agent 1)
        tech = report.results.get("technical")
        if tech:
            lines.append(f"\U0001f535 기술적 분석 (Agent 1)")
            if tech.summary and tech.summary not in ("분석 실패", "데이터 부족으로 분석 불가"):
                for line in tech.summary.split("\n")[:3]:
                    stripped = line.strip()
                    if stripped:
                        lines.append(f"  {stripped}")
            lines.append(f"  판단: {tech.signal} {tech.score}점")
            lines.append("")

        # 펀더멘털 분석 (Agent 2)
        fund = report.results.get("fundamental")
        if fund:
            lines.append(f"\U0001f7e2 펀더멘털 분석 (Agent 2)")
            if fund.summary and fund.summary not in ("분석 실패", "데이터 부족으로 분석 불가"):
                for line in fund.summary.split("\n")[:3]:
                    stripped = line.strip()
                    if stripped:
                        lines.append(f"  {stripped}")
            lines.append(f"  판단: {fund.signal} {fund.score}점")
            lines.append("")

        # 센티먼트 분석 (Agent 3)
        sent = report.results.get("sentiment")
        if sent:
            lines.append(f"\U0001f7e1 센티먼트 분석 (Agent 3)")
            if sent.summary and sent.summary not in ("분석 실패", "데이터 부족으로 분석 불가", "수급 데이터 기반 중립 판단"):
                for line in sent.summary.split("\n")[:3]:
                    stripped = line.strip()
                    if stripped:
                        lines.append(f"  {stripped}")
            lines.append(f"  판단: {sent.signal} {sent.score}점")
            lines.append("")

        # 종합 판단
        lines.append("\u2500" * 25)
        verdict_emoji = {
            "매수": "\U0001f3af", "홀딩": "\U0001f91d",
            "관망": "\u23f8", "매도": "\u26a0\ufe0f",
        }
        emoji = verdict_emoji.get(report.verdict, "\U0001f3af")
        lines.append(f"{emoji} 종합 판단: {report.verdict} ({report.combined_score}/215)")

        if report.action:
            lines.append(f"  {report.action}")
        if report.risk_note:
            lines.append(f"  {report.risk_note}")

        lines.append("")
        lines.append(f"분석 시각: {now}")
        lines.append(f"{USER_NAME}, 3개 에이전트 종합 분석 결과입니다.")

        return "\n".join(lines)
    except Exception as e:
        logger.error("멀티 리포트 v2 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 멀티 에이전트 분석 결과 생성 중 오류가 발생했습니다."


def estimate_analysis_cost(n_stocks: int = 1) -> dict:
    """분석 비용을 추정합니다.

    분석 1회당:
      Haiku x 3 = ~$0.003
      Sonnet x 1 = ~$0.015
      합계: ~$0.018 (약 25원)
    """
    try:
        per_stock = 0.018
        total = per_stock * n_stocks
        return {
            "per_stock_usd": per_stock,
            "total_usd": round(total, 3),
            "total_krw": round(total * 1400),
            "haiku_calls": 3 * n_stocks,
            "sonnet_calls": 1 * n_stocks,
        }
    except Exception:
        return {"per_stock_usd": 0.018, "total_usd": 0.018, "total_krw": 25}
