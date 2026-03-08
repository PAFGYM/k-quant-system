"""v9.5 통합 상태 모듈 — 모든 컴포넌트의 Single Source of Truth.

모든 시스템(모닝 브리핑, 매니저, AI 채팅, 추천)이 동일한 상태를 참조하도록
DB 기반 통합 상태를 10분 캐시로 제공합니다.

추가 API 호출 없이 DB에서만 조회합니다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# 캐시 유효 시간 (초)
_CACHE_TTL = 600  # 10분

_cached_state: UnifiedState | None = None
_cached_at: float = 0.0


@dataclass
class UnifiedState:
    """시스템 전체 통합 상태."""

    timestamp: datetime = field(default_factory=datetime.now)
    market_regime: str = "normal"       # "calm"/"normal"/"fear"/"panic"
    market_signal: str = ""             # "📈 상승"/"📉 하락"/"➡️ 보합"
    macro_summary: str = ""             # 1줄 매크로 요약
    manager_stances: dict = field(default_factory=dict)  # {"scalp": "리버모어: 관망...", ...}
    debate_verdicts: dict = field(default_factory=dict)   # {"005930": {"verdict": "BUY", ...}}
    youtube_insights: list = field(default_factory=list)   # 최근 유튜브 인사이트
    consensus_tickers: list = field(default_factory=list)  # 2개+ 시스템이 추천한 종목
    multi_agent_summary: dict = field(default_factory=dict)  # {"005930": {"score": 160, ...}}


async def build_unified_state(db: Any, macro_client: Any = None) -> UnifiedState:
    """통합 상태를 구축합니다 (10분 캐시).

    추가 API 호출 없이 DB에서만 조회합니다.

    Args:
        db: SQLiteStore 인스턴스
        macro_client: MacroClient (선택, macro_summary 용)

    Returns:
        UnifiedState 인스턴스
    """
    global _cached_state, _cached_at

    now = time.monotonic()
    if _cached_state and (now - _cached_at) < _CACHE_TTL:
        return _cached_state

    state = UnifiedState(timestamp=datetime.now())

    loop = asyncio.get_event_loop()

    # 병렬 DB 조회
    tasks = [
        loop.run_in_executor(None, _load_manager_stances, db),
        loop.run_in_executor(None, _load_debate_verdicts, db),
        loop.run_in_executor(None, _load_youtube_insights, db),
        loop.run_in_executor(None, _load_multi_agent_summary, db),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    if not isinstance(results[0], Exception):
        state.manager_stances = results[0]
    if not isinstance(results[1], Exception):
        state.debate_verdicts = results[1]
    if not isinstance(results[2], Exception):
        state.youtube_insights = results[2]
    if not isinstance(results[3], Exception):
        state.multi_agent_summary = results[3]

    # 매크로 요약 (macro_client가 있으면)
    if macro_client:
        try:
            snap = await macro_client.get_snapshot()
            vix = getattr(snap, "vix", 0)
            regime = getattr(snap, "regime", "normal")
            spx = getattr(snap, "spx_change_pct", 0)
            ndx = getattr(snap, "nasdaq_change_pct", 0)
            usdkrw = getattr(snap, "usdkrw", 0)

            state.market_regime = regime
            if spx > 0.5 and ndx > 0.5:
                state.market_signal = "📈 상승"
            elif spx < -0.5 and ndx < -0.5:
                state.market_signal = "📉 하락"
            else:
                state.market_signal = "➡️ 보합"

            state.macro_summary = (
                f"VIX {vix:.1f} | S&P {spx:+.1f}% | 나스닥 {ndx:+.1f}% | 환율 {usdkrw:,.0f}원"
            )
        except Exception:
            logger.debug("Unified state macro fetch failed", exc_info=True)

    # 합의 종목 계산 (2개+ 시스템이 추천한 종목)
    state.consensus_tickers = _compute_consensus(state)

    _cached_state = state
    _cached_at = now

    return state


def invalidate_unified_cache():
    """캐시 무효화 (상태 변경 시 호출)."""
    global _cached_state, _cached_at
    _cached_state = None
    _cached_at = 0.0


def format_unified_header(state: UnifiedState) -> str:
    """모든 브리핑에 공통으로 붙는 통합 헤더.

    Returns:
        텔레그램 표시용 문자열
    """
    lines = [
        f"📊 시장 종합 | {state.market_signal or '➡️ 보합'}",
        f"{state.macro_summary}" if state.macro_summary else "",
    ]

    # 매니저 종합 의견
    if state.manager_stances:
        manager_emojis = {
            "scalp": "⚡", "swing": "🔥", "position": "📊", "long_term": "💎",
        }
        manager_names = {
            "scalp": "리버모어", "swing": "오닐", "position": "린치", "long_term": "버핏",
        }
        lines.append("")
        lines.append("🎯 매니저 종합 의견")
        for key in ("scalp", "swing", "position", "long_term"):
            stance = state.manager_stances.get(key, "")
            if stance:
                emoji = manager_emojis.get(key, "📌")
                name = manager_names.get(key, key)
                lines.append(f"{emoji} {name}: {stance[:60]}")

    # YouTube 인사이트
    if state.youtube_insights:
        lines.append("")
        lines.append("🎬 방송 인사이트")
        for yi in state.youtube_insights[:3]:
            src = yi.get("source", "").replace("🎬", "").strip()
            outlook = yi.get("market_outlook", "")
            if outlook:
                lines.append(f"- {src}: {outlook[:60]}")

    # 합의 종목
    if state.consensus_tickers:
        lines.append("")
        lines.append("🤝 시스템 합의 종목")
        for ct in state.consensus_tickers[:5]:
            lines.append(
                f"- {ct['name']}({ct['ticker']}): "
                f"{ct['sources']} ({ct['verdict']})"
            )

    return "\n".join(line for line in lines if line is not None)


def format_unified_for_context(state: UnifiedState) -> str:
    """AI 프롬프트용 통합 상태 텍스트.

    Returns:
        AI 시스템 프롬프트에 주입할 문자열
    """
    sections = []

    if state.macro_summary:
        sections.append(f"[시장 종합] {state.market_signal} | {state.macro_summary}")

    if state.manager_stances:
        manager_names = {
            "scalp": "리버모어", "swing": "오닐", "position": "린치", "long_term": "버핏",
        }
        stance_lines = []
        for key in ("scalp", "swing", "position", "long_term"):
            s = state.manager_stances.get(key, "")
            if s:
                stance_lines.append(f"- {manager_names.get(key, key)}: {s[:80]}")
        if stance_lines:
            sections.append("[매니저 투자 의견]\n" + "\n".join(stance_lines))

    if state.debate_verdicts:
        debate_lines = []
        for ticker, d in list(state.debate_verdicts.items())[:10]:
            v = d.get("verdict", "")
            conf = d.get("confidence", 0)
            name = d.get("name", ticker)
            debate_lines.append(f"- {name}({ticker}): {v} ({conf:.0f}%)")
        if debate_lines:
            sections.append("[AI 토론 합의]\n" + "\n".join(debate_lines))

    if state.youtube_insights:
        yt_lines = []
        for yi in state.youtube_insights[:5]:
            src = yi.get("source", "").replace("🎬", "").strip()
            outlook = yi.get("market_outlook", "")
            impl = yi.get("investment_implications", "")
            yt_lines.append(f"- {src}: 전망={outlook[:40]}, 시사점={impl[:60]}")
        if yt_lines:
            sections.append("[YouTube 방송 인사이트]\n" + "\n".join(yt_lines))

    if state.consensus_tickers:
        cons_lines = []
        for ct in state.consensus_tickers[:5]:
            cons_lines.append(
                f"- {ct['name']}({ct['ticker']}): {ct['verdict']} "
                f"[{ct['sources']}]"
            )
        if cons_lines:
            sections.append("[시스템 합의 종목]\n" + "\n".join(cons_lines))

    return "\n\n".join(sections)


# ── Internal helpers ──────────────────────────────────────

def _load_manager_stances(db: Any) -> dict:
    """DB에서 최근 매니저 stance 조회."""
    try:
        return db.get_recent_manager_stances(hours=24)
    except Exception:
        logger.debug("Failed to load manager stances", exc_info=True)
        return {}


def _load_debate_verdicts(db: Any) -> dict:
    """보유종목의 AI 토론 합의 결과."""
    verdicts = {}
    try:
        holdings = db.get_active_holdings()
        for h in (holdings or [])[:20]:
            ticker = h.get("ticker", "")
            d = db.get_latest_debate(ticker)
            if d:
                verdicts[ticker] = {
                    "name": h.get("name", ticker),
                    "verdict": d.get("verdict", ""),
                    "confidence": d.get("confidence", 0),
                    "consensus_level": d.get("consensus_level", ""),
                    "price_target": d.get("price_target", 0),
                }
    except Exception:
        logger.debug("Failed to load debate verdicts", exc_info=True)
    return verdicts


def _load_youtube_insights(db: Any) -> list:
    """최근 YouTube 인텔리전스."""
    try:
        return db.get_recent_youtube_intelligence(hours=24, limit=5)
    except Exception:
        logger.debug("Failed to load YouTube insights", exc_info=True)
        return []


def _load_multi_agent_summary(db: Any) -> dict:
    """멀티에이전트 분석 결과 요약."""
    summary = {}
    try:
        holdings = db.get_active_holdings()
        for h in (holdings or [])[:20]:
            ticker = h.get("ticker", "")
            results = db.get_multi_agent_results(ticker=ticker, limit=1)
            if results:
                r = results[0]
                summary[ticker] = {
                    "name": h.get("name", ticker),
                    "combined_score": r.get("combined_score", 0),
                    "verdict": r.get("verdict", ""),
                    "confidence": r.get("confidence", ""),
                }
    except Exception:
        logger.debug("Failed to load multi-agent summary", exc_info=True)
    return summary


def _compute_consensus(state: UnifiedState) -> list:
    """2개 이상 시스템이 추천한 종목을 추출.

    시스템: 매니저, AI 토론, YouTube 인텔리전스, 멀티에이전트
    """
    ticker_sources: dict[str, dict] = {}

    # 1. AI 토론 합의 — BUY/STRONG_BUY인 종목
    for ticker, d in state.debate_verdicts.items():
        verdict = d.get("verdict", "")
        if verdict in ("BUY", "STRONG_BUY"):
            key = ticker
            if key not in ticker_sources:
                ticker_sources[key] = {
                    "ticker": ticker,
                    "name": d.get("name", ticker),
                    "sources": [],
                    "verdict": verdict,
                }
            ticker_sources[key]["sources"].append("AI토론")

    # 2. 멀티에이전트 — 120+ 점수
    for ticker, m in state.multi_agent_summary.items():
        score = m.get("combined_score", 0)
        if score >= 120:
            if ticker not in ticker_sources:
                ticker_sources[ticker] = {
                    "ticker": ticker,
                    "name": m.get("name", ticker),
                    "sources": [],
                    "verdict": "매수추천" if score >= 160 else "관심",
                }
            ticker_sources[ticker]["sources"].append("멀티에이전트")

    # 3. YouTube 인사이트 — 긍정 언급 종목
    for yi in state.youtube_insights:
        tickers = yi.get("mentioned_tickers", [])
        if not isinstance(tickers, list):
            continue
        for t in tickers:
            if t.get("sentiment") == "긍정":
                name = t.get("name", "")
                t_code = t.get("ticker", name)
                # 이미 있는 종목 매칭
                matched = None
                for key, val in ticker_sources.items():
                    if key == t_code or val["name"] == name:
                        matched = key
                        break
                if matched:
                    if "YouTube" not in ticker_sources[matched]["sources"]:
                        ticker_sources[matched]["sources"].append("YouTube")
                else:
                    ticker_sources[t_code] = {
                        "ticker": t_code,
                        "name": name,
                        "sources": ["YouTube"],
                        "verdict": "긍정언급",
                    }

    # 2개 이상 소스에서 언급된 종목만 반환
    consensus = []
    for data in ticker_sources.values():
        if len(data["sources"]) >= 2:
            data["sources"] = "+".join(data["sources"])
            consensus.append(data)

    # 소스 수 내림차순 정렬
    consensus.sort(key=lambda x: x["sources"].count("+"), reverse=True)
    return consensus[:10]
