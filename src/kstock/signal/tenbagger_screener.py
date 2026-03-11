"""Tenbagger Screener v2.1 — 텐배거 후보 7팩터 스코어링 엔진.

4개 AI 합의 + 실전 투자 메모 최종판 기반 8개 섹터 텐배거 스크리닝.

7팩터 스코어링 시스템:
1. TAM Growth (20%) — Total Addressable Market 폭발 잠재력
2. Policy Tailwind (20%) — 정부 정책/보조금/규제 완화 수혜
3. Technology Moat (15%) — 특허, 독점 계약, 퍼스트무버
4. Revenue Trajectory (15%) — 매출/이익 성장 궤적
5. Institutional Discovery (10%) — 초기 기관/외인 매집
6. Sector Momentum (10%) — 섹터 ETF/대장주 동반 상승
7. AI Consensus (10%) — 4개 AI 모델 컨센서스

등급 체계:
A — 10배 경로 + 12~18개월 확인지표 분명 (포지션 8~10%)
B — 좋은 사업이지만 3~5배 확률이 더 높음 (포지션 4~6%)
C — 이벤트/옵션 베팅 (포지션 1~3%)

포트폴리오: 코어(45%) / 구조적성장(35%) / 옵션(20%)
지역배분: 미국 60% / 한국 40%
유니버스: 한국 10종목 + 미국 10종목 = 20종목

v12.0 (2026-03)
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── config ──────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "tenbagger.yaml"
_config_cache: dict | None = None


def load_tenbagger_config() -> dict:
    """tenbagger.yaml 설정을 로드한다 (캐시 포함)."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
        return _config_cache
    except Exception:
        logger.warning("tenbagger.yaml 로드 실패 — 기본값 사용")
        return {}


def reload_config() -> dict:
    """캐시를 버리고 설정을 다시 로드한다."""
    global _config_cache
    _config_cache = None
    return load_tenbagger_config()


# ── dataclass ───────────────────────────────────────────────

@dataclass
class TenbaggerScore:
    """텐배거 후보 종합 스코어 (7팩터 + 종합)."""

    ticker: str
    name: str
    market: str = "KRX"       # KRX or US
    sector: str = ""
    sector_name: str = ""
    sector_emoji: str = ""

    # 7팩터 개별 점수 (0~100)
    tam_score: float = 0
    policy_score: float = 0
    moat_score: float = 0
    revenue_score: float = 0
    discovery_score: float = 0
    momentum_score: float = 0
    consensus_score: float = 0

    # 종합 점수
    tenbagger_score: float = 0

    # 부가 정보
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    kill_conditions: list[str] = field(default_factory=list)
    monitor_12m: list[str] = field(default_factory=list)
    current_price: float = 0
    market_cap: float = 0

    # config 등급 (A/B/C) — 실전 투자 메모 기준
    config_grade: str = ""
    config_tier: str = ""   # core / structural / option
    config_rank: int = 0
    character: str = ""

    @property
    def grade(self) -> str:
        """텐배거 등급.

        config_grade가 설정되어 있으면 그것을 우선 사용 (실전 투자 메모 기준).
        없으면 스코어 기반: A(80+), B(60+), C(40+), D(<40).
        """
        if self.config_grade:
            return self.config_grade
        if self.tenbagger_score >= 80:
            return "A"
        if self.tenbagger_score >= 60:
            return "B"
        if self.tenbagger_score >= 40:
            return "C"
        return "D"

    @property
    def position_range(self) -> tuple[float, float]:
        """등급별 권장 포지션 비중 범위 (%)."""
        g = self.grade
        if g == "A":
            return (8.0, 10.0)
        if g == "B":
            return (4.0, 6.0)
        if g == "C":
            return (1.0, 3.0)
        return (0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        """DB 저장용 dict 변환."""
        return {
            "tenbagger_score": self.tenbagger_score,
            "tam_score": self.tam_score,
            "policy_score": self.policy_score,
            "moat_score": self.moat_score,
            "revenue_score": self.revenue_score,
            "discovery_score": self.discovery_score,
            "momentum_score": self.momentum_score,
            "consensus_score": self.consensus_score,
            "current_price": self.current_price,
            "price_at_score": self.current_price,
            "grade": self.grade,
            "tier": self.config_tier,
            "rank": self.config_rank,
        }


# ── 7팩터 개별 스코어링 함수 ────────────────────────────────

def _score_tam_growth(
    revenue_growth_yoy: float = 0,
    tam_cagr: float = 0,
    sector_tailwind: float = 50,
) -> float:
    """1. TAM 성장 잠재력 (0~100).

    - tam_cagr: 섹터 TAM 5년 CAGR (%)
    - revenue_growth_yoy: 매출 YoY 성장률 (%, TAM 점유 지표)
    - sector_tailwind: 섹터 기본 순풍 점수 (config)
    """
    score = 0.0
    # TAM CAGR
    if tam_cagr >= 50:
        score += 50
    elif tam_cagr >= 30:
        score += 35
    elif tam_cagr >= 20:
        score += 20
    elif tam_cagr >= 10:
        score += 10

    # 매출 성장 → TAM 점유 속도
    if revenue_growth_yoy >= 100:
        score += 30
    elif revenue_growth_yoy >= 50:
        score += 20
    elif revenue_growth_yoy >= 30:
        score += 10

    # 섹터 기본 순풍
    score += sector_tailwind * 0.2

    return min(100, score)


def _score_policy_tailwind(
    sector: str = "",
    policy_events: list[str] | None = None,
    config: dict | None = None,
) -> float:
    """2. 정책 순풍 점수 (0~100).

    - sector: 섹터 키 (config에서 base score 참조)
    - policy_events: 구체적 정책 이벤트 리스트 (건당 +5)
    """
    cfg = config or load_tenbagger_config()
    sector_cfg = cfg.get("sectors", {}).get(sector, {})
    base = sector_cfg.get("tailwind_score", 50)

    if policy_events:
        base = min(100, base + len(policy_events) * 5)

    return float(base)


def _score_technology_moat(
    has_patents: bool = False,
    patent_count: int = 0,
    first_mover: bool = False,
    exclusive_contracts: bool = False,
    market_share_pct: float = 0,
    domestic_monopoly: bool = False,
) -> float:
    """3. 기술 해자 점수 (0~100).

    - 특허, 퍼스트무버, 독점 계약, 시장 점유율, 국내 독점 여부
    """
    score = 0.0
    if has_patents:
        score += min(30, patent_count * 3)
    if first_mover:
        score += 25
    if exclusive_contracts:
        score += 20
    if domestic_monopoly:
        score += 25
    if market_share_pct >= 50:
        score += 25
    elif market_share_pct >= 30:
        score += 15
    elif market_share_pct >= 10:
        score += 10
    return min(100, score)


def _score_revenue_trajectory(
    revenue_growth_yoy: float = 0,
    revenue_growth_qoq: float = 0,
    operating_profit_growth: float = 0,
    is_profitable: bool = False,
    turning_profitable: bool = False,
) -> float:
    """4. 매출/이익 궤적 점수 (0~100).

    - 매출 YoY/QoQ 성장률, 영업이익 성장률, 흑자/흑자전환 여부
    """
    score = 0.0
    if revenue_growth_yoy >= 100:
        score += 40
    elif revenue_growth_yoy >= 50:
        score += 30
    elif revenue_growth_yoy >= 30:
        score += 20
    elif revenue_growth_yoy >= 10:
        score += 10

    if revenue_growth_qoq >= 20:
        score += 15
    elif revenue_growth_qoq >= 10:
        score += 10

    if is_profitable:
        score += 15
    elif turning_profitable:
        score += 20  # 흑자 전환 보너스

    if operating_profit_growth >= 50:
        score += 15

    return min(100, score)


def _score_institutional_discovery(
    foreign_buy_days_in_20: int = 0,
    institution_buy_days_in_20: int = 0,
    foreign_ratio_change: float = 0,
) -> float:
    """5. 기관 발견 점수 (0~100) — 초기 매집 신호.

    - foreign_buy_days_in_20: 최근 20일 중 외인 순매수 일수
    - institution_buy_days_in_20: 최근 20일 중 기관 순매수 일수
    - foreign_ratio_change: 외국인 보유비중 변화 (%)
    """
    score = 0.0
    if foreign_buy_days_in_20 >= 15:
        score += 40
    elif foreign_buy_days_in_20 >= 10:
        score += 25
    elif foreign_buy_days_in_20 >= 7:
        score += 15

    if institution_buy_days_in_20 >= 12:
        score += 30
    elif institution_buy_days_in_20 >= 8:
        score += 20
    elif institution_buy_days_in_20 >= 5:
        score += 10

    if foreign_ratio_change > 2.0:
        score += 20
    elif foreign_ratio_change > 1.0:
        score += 10

    return min(100, score)


def _score_sector_momentum(
    sector_return_1m: float = 0,
    sector_return_3m: float = 0,
    leader_return_1m: float = 0,
) -> float:
    """6. 섹터 모멘텀 점수 (0~100).

    - sector_return_1m/3m: 섹터 ETF 또는 대장주 수익률 (%)
    - leader_return_1m: 해당 섹터 미국 리더주 1개월 수익률
    """
    score = 0.0
    if sector_return_1m >= 10:
        score += 30
    elif sector_return_1m >= 5:
        score += 20
    elif sector_return_1m >= 0:
        score += 10

    if sector_return_3m >= 20:
        score += 30
    elif sector_return_3m >= 10:
        score += 20
    elif sector_return_3m >= 0:
        score += 10

    if leader_return_1m >= 15:
        score += 25
    elif leader_return_1m >= 5:
        score += 15
    elif leader_return_1m >= 0:
        score += 5

    return min(100, score)


def _score_ai_consensus(
    consensus_data: dict | None = None,
) -> float:
    """7. AI 합의 점수 (0~100).

    - consensus_data: {"claude": 85, "gpt": 80, "gemini": 75, ...}
    - 4개 AI 평균 + 합의도(낮은 스프레드 = 보너스)
    """
    if not consensus_data:
        return 50.0  # 데이터 없으면 중립

    scores = [v for v in consensus_data.values() if isinstance(v, (int, float))]
    if not scores:
        return 50.0

    avg = sum(scores) / len(scores)
    spread = max(scores) - min(scores) if len(scores) > 1 else 0
    agreement_bonus = max(0, 20 - spread)

    return min(100, avg * 0.8 + agreement_bonus)


# ── 종합 스코어링 ───────────────────────────────────────────

def compute_tenbagger_score(
    ticker: str,
    name: str,
    market: str = "KRX",
    sector: str = "",
    *,
    # Factor 1: TAM
    revenue_growth_yoy: float = 0,
    tam_cagr: float = 0,
    # Factor 2: Policy
    policy_events: list[str] | None = None,
    # Factor 3: Moat
    has_patents: bool = False,
    patent_count: int = 0,
    first_mover: bool = False,
    exclusive_contracts: bool = False,
    market_share_pct: float = 0,
    domestic_monopoly: bool = False,
    # Factor 4: Revenue
    revenue_growth_qoq: float = 0,
    operating_profit_growth: float = 0,
    is_profitable: bool = False,
    turning_profitable: bool = False,
    # Factor 5: Discovery
    foreign_buy_days_in_20: int = 0,
    institution_buy_days_in_20: int = 0,
    foreign_ratio_change: float = 0,
    # Factor 6: Momentum
    sector_return_1m: float = 0,
    sector_return_3m: float = 0,
    leader_return_1m: float = 0,
    # Factor 7: AI Consensus
    consensus_data: dict | None = None,
    # Context
    current_price: float = 0,
    market_cap: float = 0,
    catalysts: list[str] | None = None,
    risks: list[str] | None = None,
) -> TenbaggerScore:
    """7팩터 가중 종합 텐배거 점수를 계산한다."""
    config = load_tenbagger_config()

    # 가중치 로드
    weights = config.get("scoring_weights", {})
    w_tam = weights.get("tam_growth", 0.20)
    w_policy = weights.get("policy_tailwind", 0.20)
    w_moat = weights.get("technology_moat", 0.15)
    w_revenue = weights.get("revenue_trajectory", 0.15)
    w_discovery = weights.get("institutional_discovery", 0.10)
    w_momentum = weights.get("sector_momentum", 0.10)
    w_consensus = weights.get("ai_consensus", 0.10)

    sector_cfg = config.get("sectors", {}).get(sector, {})
    sector_tailwind = sector_cfg.get("tailwind_score", 50)

    # 7팩터 개별 점수 계산
    tam = _score_tam_growth(revenue_growth_yoy, tam_cagr, sector_tailwind)
    policy = _score_policy_tailwind(sector, policy_events, config)
    moat = _score_technology_moat(
        has_patents, patent_count, first_mover,
        exclusive_contracts, market_share_pct, domestic_monopoly,
    )
    revenue = _score_revenue_trajectory(
        revenue_growth_yoy, revenue_growth_qoq,
        operating_profit_growth, is_profitable, turning_profitable,
    )
    discovery = _score_institutional_discovery(
        foreign_buy_days_in_20, institution_buy_days_in_20,
        foreign_ratio_change,
    )
    momentum = _score_sector_momentum(
        sector_return_1m, sector_return_3m, leader_return_1m,
    )
    consensus = _score_ai_consensus(consensus_data)

    # 가중 종합
    composite = (
        tam * w_tam
        + policy * w_policy
        + moat * w_moat
        + revenue * w_revenue
        + discovery * w_discovery
        + momentum * w_momentum
        + consensus * w_consensus
    )

    return TenbaggerScore(
        ticker=ticker,
        name=name,
        market=market,
        sector=sector,
        sector_name=sector_cfg.get("name", sector),
        sector_emoji=sector_cfg.get("emoji", ""),
        tam_score=round(tam, 1),
        policy_score=round(policy, 1),
        moat_score=round(moat, 1),
        revenue_score=round(revenue, 1),
        discovery_score=round(discovery, 1),
        momentum_score=round(momentum, 1),
        consensus_score=round(consensus, 1),
        tenbagger_score=round(composite, 1),
        catalysts=catalysts or [],
        risks=risks or [],
        current_price=current_price,
        market_cap=market_cap,
    )


# ── 포맷팅 함수 ────────────────────────────────────────────

_GRADE_EMOJI = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}


_TIER_LABEL = {"core": "코어", "structural": "구조적성장", "option": "옵션"}


def format_tenbagger_card(score: TenbaggerScore) -> str:
    """텐배거 후보 종목 카드 (텔레그램 메시지용)."""
    ge = _GRADE_EMOJI.get(score.grade, "⚪")
    tier_label = _TIER_LABEL.get(score.config_tier, "")
    tier_str = f" [{tier_label}]" if tier_label else ""
    pos_lo, pos_hi = score.position_range

    lines = [
        f"🔟 텐배거 후보 | {score.sector_emoji} {score.sector_name}",
        "━" * 24,
        f"{score.name} ({score.ticker}) [{score.market}]",
    ]

    if score.character:
        lines.append(f"💡 {score.character}")

    lines += [
        "",
        f"{ge} {score.grade}등급{tier_str} | 점수 {score.tenbagger_score:.0f}/100",
        f"📊 권장비중: {pos_lo:.0f}~{pos_hi:.0f}%",
        "",
        f"  TAM 성장:     {score.tam_score:.0f}",
        f"  정책 순풍:     {score.policy_score:.0f}",
        f"  기술 해자:     {score.moat_score:.0f}",
        f"  매출 궤적:     {score.revenue_score:.0f}",
        f"  기관 발견:     {score.discovery_score:.0f}",
        f"  섹터 모멘텀:   {score.momentum_score:.0f}",
        f"  AI 합의:       {score.consensus_score:.0f}",
    ]

    if score.current_price > 0:
        lines.append(f"\n현재가: {score.current_price:,.0f}")

    if score.catalysts:
        lines.append("\n📌 카탈리스트:")
        for c in score.catalysts[:3]:
            lines.append(f"  • {c}")

    if score.kill_conditions:
        lines.append("\n🚫 가설 붕괴 조건:")
        for k in score.kill_conditions[:3]:
            lines.append(f"  • {k}")

    if score.monitor_12m:
        lines.append("\n🔍 12개월 확인지표:")
        for m in score.monitor_12m[:3]:
            lines.append(f"  • {m}")

    if score.risks:
        lines.append("\n⚠️ 리스크:")
        for r in score.risks[:2]:
            lines.append(f"  • {r}")

    return "\n".join(lines)


def format_sector_summary(scores: list[TenbaggerScore]) -> str:
    """섹터별 텐배거 포트폴리오 요약."""
    by_sector: dict[str, list[TenbaggerScore]] = defaultdict(list)
    for s in scores:
        by_sector[s.sector].append(s)

    lines = ["🔟 텐배거 섹터 포트폴리오", "━" * 24]

    for sector_key, items in sorted(
        by_sector.items(),
        key=lambda x: -max(i.tenbagger_score for i in x[1]),
    ):
        if not items:
            continue
        top = items[0]
        avg_score = sum(i.tenbagger_score for i in items) / len(items)
        lines.append(
            f"\n{top.sector_emoji} {top.sector_name} "
            f"(평균 {avg_score:.0f}점, {len(items)}종목)"
        )
        for item in sorted(items, key=lambda x: -x.tenbagger_score)[:3]:
            ge = _GRADE_EMOJI.get(item.grade, "⚪")
            ret_str = ""
            if item.current_price > 0 and hasattr(item, "current_return"):
                pass  # DB에서 수익률은 별도 관리
            lines.append(f"  {ge} {item.name} {item.tenbagger_score:.0f}점")

    total = len(scores)
    avg_all = sum(s.tenbagger_score for s in scores) / total if total else 0
    lines.append(f"\n전체: {total}종목, 평균 {avg_all:.0f}점")

    return "\n".join(lines)


def format_tenbagger_briefing_context(
    universe: list[dict],
    catalysts: list[dict] | None = None,
) -> str:
    """AI 브리핑 컨텍스트용 텐배거 요약 텍스트."""
    if not universe:
        return ""

    lines = ["[텐배거 유니버스 현황]"]
    for u in universe[:10]:
        name = u.get("name", "")
        score = u.get("tenbagger_score", 0)
        sector = u.get("sector", "")
        ret = u.get("current_return", 0)
        lines.append(f"  {name}({u.get('ticker','')}): {score:.0f}점 | {sector} | 수익률 {ret:+.1f}%")

    if catalysts:
        pending = [c for c in catalysts if c.get("status") == "pending"]
        if pending:
            lines.append("\n[임박 카탈리스트]")
            for c in pending[:5]:
                lines.append(
                    f"  {c.get('ticker','')}: {c.get('description','')} "
                    f"({c.get('expected_date','TBD')})"
                )

    return "\n".join(lines)


# ── 유니버스 초기화 헬퍼 ────────────────────────────────────

def get_initial_universe() -> list[dict]:
    """config에서 초기 텐배거 유니버스를 읽어온다.

    Returns: [{"ticker", "name", "market", "sector", "grade", "tier", "rank",
               "character", "catalysts", "kill_conditions", "monitor_12m",
               "ai_consensus"}, ...]
    """
    config = load_tenbagger_config()
    result = []

    for item in config.get("korea_universe", []):
        result.append({
            "ticker": item["code"],
            "name": item["name"],
            "market": "KRX",
            "sector": item["sector"],
            "grade": item.get("grade", "C"),
            "tier": item.get("tier", "option"),
            "rank": item.get("rank", 99),
            "character": item.get("character", ""),
            "catalysts": item.get("catalysts", []),
            "kill_conditions": item.get("kill_conditions", []),
            "monitor_12m": item.get("monitor_12m", []),
            "ai_consensus": item.get("ai_consensus", 50),
            # v2.2: bucket 분류 필드
            "bucket": "active_tenbagger",
            "directness": item.get("directness", ""),
            "earnings_linkage": item.get("earnings_linkage", ""),
            "catalyst_strength": item.get("catalyst_strength", ""),
        })

    for item in config.get("us_universe", []):
        result.append({
            "ticker": item["ticker"],
            "name": item["name"],
            "market": "US",
            "sector": item["sector"],
            "grade": item.get("grade", "C"),
            "tier": item.get("tier", "option"),
            "rank": item.get("rank", 99),
            "character": item.get("character", ""),
            "catalysts": item.get("catalysts", []),
            "kill_conditions": item.get("kill_conditions", []),
            "monitor_12m": item.get("monitor_12m", []),
            "ai_consensus": item.get("ai_consensus", 50),
            # v2.2: bucket 분류 필드
            "bucket": "active_tenbagger",
            "directness": item.get("directness", ""),
            "earnings_linkage": item.get("earnings_linkage", ""),
            "catalyst_strength": item.get("catalyst_strength", ""),
        })

    return result


def get_portfolio_allocation() -> dict:
    """config에서 포트폴리오 배분 구조를 읽어온다."""
    config = load_tenbagger_config()
    return config.get("portfolio_structure", {
        "core_pct": 45,
        "structural_pct": 35,
        "option_pct": 20,
        "region_us_pct": 60,
        "region_kr_pct": 40,
    })


def get_grade_info() -> dict:
    """등급별 정의 정보를 읽어온다."""
    config = load_tenbagger_config()
    return config.get("grades", {
        "A": {"label": "코어 텐배거", "position_min_pct": 8, "position_max_pct": 10},
        "B": {"label": "구조적 성장", "position_min_pct": 4, "position_max_pct": 6},
        "C": {"label": "옵션 베팅", "position_min_pct": 1, "position_max_pct": 3},
    })


def get_theme_watchlist() -> list[dict]:
    """config에서 테마 워치리스트를 읽어온다 (v2.3).

    theme_watchlist는 active universe에 아직 편입하지 않은 관찰 종목.
    매매 비대상. 승격(promotion) 조건 충족 시 active로 이동.

    Returns: [{"ticker", "name", "market", "sector", "bucket", "directness",
               "earnings_linkage", "catalyst_strength", "promotion_rule",
               "theme", "character", "catalysts", "watch_reasons",
               "promotion_condition", "ai_consensus", "priority"}, ...]
    """
    config = load_tenbagger_config()
    result = []

    for item in config.get("theme_watchlist", []):
        result.append({
            "ticker": item.get("code", item.get("ticker", "")),
            "name": item["name"],
            "market": item.get("market", "KRX"),
            "sector": item.get("sector", ""),
            "bucket": item.get("bucket", "theme_watchlist"),
            "directness": item.get("directness", "indirect"),
            "earnings_linkage": item.get("earnings_linkage", "speculative"),
            "catalyst_strength": item.get("catalyst_strength", "weak"),
            "promotion_rule": item.get("promotion_rule", ""),
            "theme": item.get("theme", ""),
            "character": item.get("character", ""),
            "catalysts": item.get("catalysts", []),
            "watch_reasons": item.get("watch_reasons", []),
            "promotion_condition": item.get("promotion_condition", ""),
            "ai_consensus": item.get("ai_consensus", 50),
            "priority": item.get("priority", 99),
        })

    return result


def get_bucket_info() -> dict:
    """bucket 정의를 읽어온다 (v2.3)."""
    config = load_tenbagger_config()
    return config.get("buckets", {
        "direction_leaders": {
            "label": "방향성 리더",
            "tradeable": False,
        },
        "active_tenbagger": {
            "label": "액티브 텐배거",
            "tradeable": True,
        },
        "theme_watchlist": {
            "label": "테마 워치리스트",
            "tradeable": False,
        },
    })


def get_direction_leaders() -> list[dict]:
    """config에서 방향성 리더를 읽어온다 (v2.3).

    섹터 on/off 신호용 대형 대표주. 매수 핵심 대상 아님.

    Returns: [{"ticker", "name", "market", "sector", "bucket", "directness",
               "earnings_linkage", "catalyst_strength", "character",
               "signal_role", "why_in_bucket"}, ...]
    """
    config = load_tenbagger_config()
    result = []

    for item in config.get("direction_leaders", []):
        result.append({
            "ticker": item.get("ticker", item.get("code", "")),
            "name": item["name"],
            "market": item.get("market", "US"),
            "sector": item.get("sector", ""),
            "bucket": "direction_leaders",
            "directness": item.get("directness", ""),
            "earnings_linkage": item.get("earnings_linkage", ""),
            "catalyst_strength": item.get("catalyst_strength", ""),
            "character": item.get("character", ""),
            "signal_role": item.get("signal_role", ""),
            "why_in_bucket": item.get("why_in_bucket", ""),
        })

    return result


def get_promotion_process() -> dict:
    """promotion_process 설정을 읽어온다 (v2.3).

    watchlist → active 승격 프로세스.
    현재: 자동 모니터링 + GPT Pro 수동 승인 구조.
    """
    config = load_tenbagger_config()
    return config.get("promotion_process", {})


def get_full_universe() -> dict[str, list[dict]]:
    """active + watchlist + leaders를 bucket별로 구분해 반환 (v2.2).

    Returns: {"active_tenbagger": [...], "theme_watchlist": [...],
              "direction_leaders": [...]}
    """
    return {
        "active_tenbagger": get_initial_universe(),
        "theme_watchlist": get_theme_watchlist(),
        "direction_leaders": get_direction_leaders(),
    }


# ── 매니저 코칭 (구체적 매수 계획 생성) ────────────────────

_SPLIT_STRATEGY = {
    # grade → (분할 횟수, 비중 배분)
    "A": {"splits": 3, "ratios": [0.40, 0.35, 0.25], "label": "3분할 피라미딩"},
    "B": {"splits": 2, "ratios": [0.50, 0.50], "label": "2분할 균등"},
    "C": {"splits": 1, "ratios": [1.0], "label": "1회 소량 진입"},
}


def generate_buy_coaching(
    ticker: str,
    name: str,
    grade: str,
    current_price: float,
    total_budget: float,
    *,
    market: str = "KRX",
    sector: str = "",
    character: str = "",
    kill_conditions: list[str] | None = None,
    already_held_qty: int = 0,
    already_held_avg: float = 0,
) -> str:
    """텐배거 매니저 구체적 매수 코칭 메시지 생성.

    Args:
        ticker: 종목코드
        name: 종목명
        grade: A/B/C 등급
        current_price: 현재가
        total_budget: 이 종목에 배정된 총 예산 (원 or USD)
        market: KRX or US
        character: 종목 성격 (config)
        kill_conditions: 가설 붕괴 조건
        already_held_qty: 기보유 수량
        already_held_avg: 기보유 평균가

    Returns:
        텔레그램 메시지용 코칭 텍스트
    """
    config = load_tenbagger_config()
    strategy = _SPLIT_STRATEGY.get(grade, _SPLIT_STRATEGY["B"])
    currency = "원" if market == "KRX" else "USD"
    unit = 1 if market == "KRX" else 1  # 한국주식 1주 단위

    ge = _GRADE_EMOJI.get(grade, "⚪")
    lines = [
        f"🔟 텐배거 코칭 | {ge}{grade}등급",
        "━" * 24,
        f"📌 {name} ({ticker})",
    ]
    if character:
        lines.append(f"💡 {character}")

    lines.append(f"\n현재가: {current_price:,.0f}{currency}")

    if already_held_qty > 0:
        pnl = (current_price - already_held_avg) / already_held_avg * 100 if already_held_avg > 0 else 0
        lines.append(
            f"보유: {already_held_qty}주 × {already_held_avg:,.0f}{currency}"
            f" ({pnl:+.1f}%)"
        )

    lines.append(f"\n📊 배정 예산: {total_budget:,.0f}{currency}")
    lines.append(f"전략: {strategy['label']} ({strategy['splits']}회)")
    lines.append("")

    # 분할 매수 계획 생성
    stop_pct = config.get("thresholds", {}).get("stop_loss_pct", -25)
    remaining = total_budget
    total_qty = 0

    for i, ratio in enumerate(strategy["ratios"], 1):
        alloc = total_budget * ratio
        if i == 1:
            buy_price = current_price
            condition = "즉시 (시장가 or 현재가 지정가)"
        elif i == 2:
            buy_price = current_price * 0.95  # -5% 조정 시
            condition = f"현재가 대비 -5% ({buy_price:,.0f}{currency})"
        else:
            buy_price = current_price * 0.90  # -10% 조정 시
            condition = f"현재가 대비 -10% ({buy_price:,.0f}{currency})"

        qty = int(alloc / buy_price) if buy_price > 0 else 0
        if qty < 1:
            qty = 1
        cost = qty * buy_price
        total_qty += qty

        lines.append(f"🔸 {i}차 매수")
        lines.append(f"  조건: {condition}")
        lines.append(f"  수량: {qty}주 × {buy_price:,.0f} = {cost:,.0f}{currency}")
        lines.append(f"  배분: {ratio*100:.0f}% ({alloc:,.0f}{currency})")
        lines.append("")

    # 손절 / 목표가
    stop_price = current_price * (1 + stop_pct / 100)
    tp1_price = current_price * 2  # 100%
    tp2_price = current_price * 6  # 500%

    lines.append("📍 매도 계획")
    lines.append(f"  🔴 손절: {stop_price:,.0f}{currency} ({stop_pct}%)")
    lines.append(f"     → 가설 붕괴 조건 확인 후 실행")
    lines.append(f"  🟡 1차 익절(2배): {tp1_price:,.0f}{currency}")
    lines.append(f"     → 원금 회수 (보유수량 50% 매도)")
    lines.append(f"  🟢 2차 익절(6배): {tp2_price:,.0f}{currency}")
    lines.append(f"     → 추가 30% 매도, 나머지 텐배거 홀드")

    if kill_conditions:
        lines.append("\n🚫 즉시 전량 매도 조건:")
        for kc in kill_conditions[:3]:
            lines.append(f"  • {kc}")

    lines.append(f"\n💰 요약: 총 {total_qty}주, 예상 투입 ~{total_budget:,.0f}{currency}")

    return "\n".join(lines)


def generate_portfolio_coaching(
    total_investment: float,
    market: str = "KRX",
) -> str:
    """텐배거 포트폴리오 전체 배분 코칭 메시지.

    Args:
        total_investment: 텐배거 포트폴리오 총 투자금
        market: KRX or ALL

    Returns:
        종목별 배정 금액 + 등급별 배분 텍스트
    """
    config = load_tenbagger_config()
    alloc = config.get("portfolio_structure", {})
    currency = "원" if market == "KRX" else "원"

    # 등급별 예산
    core_budget = total_investment * alloc.get("core_pct", 45) / 100
    struct_budget = total_investment * alloc.get("structural_pct", 35) / 100
    option_budget = total_investment * alloc.get("option_pct", 20) / 100

    lines = [
        "🔟 텐배거 포트폴리오 코칭",
        "━" * 24,
        f"총 투자금: {total_investment:,.0f}{currency}",
        "",
        f"🟢 A등급 코어 (45%): {core_budget:,.0f}{currency}",
        f"🟡 B등급 구조적 (35%): {struct_budget:,.0f}{currency}",
        f"🟠 C등급 옵션 (20%): {option_budget:,.0f}{currency}",
        "",
    ]

    universe = get_initial_universe()
    grade_budgets = {"A": core_budget, "B": struct_budget, "C": option_budget}

    for grade_key in ["A", "B", "C"]:
        ge = _GRADE_EMOJI.get(grade_key, "⚪")
        stocks = [u for u in universe if u["grade"] == grade_key]
        if market == "KRX":
            stocks = [u for u in stocks if u["market"] == "KRX"]

        budget = grade_budgets[grade_key]
        if not stocks:
            continue

        per_stock = budget / len(stocks)
        lines.append(f"{ge} {grade_key}등급 ({len(stocks)}종목, 종목당 ~{per_stock:,.0f}{currency})")
        for s in stocks:
            lines.append(f"  • {s['name']}({s['ticker']}): ~{per_stock:,.0f}{currency}")
        lines.append("")

    lines.append("⚠️ 위 배분은 가이드입니다. 확신도에 따라 ±30% 조정 가능.")

    return "\n".join(lines)
