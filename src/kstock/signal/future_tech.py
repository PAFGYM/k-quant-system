"""Future technology sector watchlist engine - K-Quant v3.5.

Defines 3 key future tech sectors (autonomous driving, space/aerospace,
quantum computing) with tiered watchlists, scoring, and entry evaluation.

Rules:
- No ** bold, no Markdown parse_mode
- Korean responses only
- "주호님" personalized greeting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# 3 Future Technology Sector definitions
# ---------------------------------------------------------------------------

FUTURE_SECTORS: dict[str, dict[str, Any]] = {
    "autonomous_driving": {
        "name": "자율주행",
        "phase": "성장 초기 (L3 상용화 시작)",
        "timeline": "2026~2030 본격 확산",
        "emoji": "\U0001f697",
        "trigger_keywords": [
            "자율주행", "L3", "L4", "ADAS", "SDV", "라이다", "V2X",
            "자율주행 허용", "무인택시", "로보택시", "웨이모", "모빌아이",
            "자율주행 규제완화", "운전자보조", "자율주행특별법",
        ],
        "watchlist": {
            "tier1_platform": {
                "현대오토에버": {"ticker": "307950", "reason": "ADAS SW 플랫폼, 현대차 SDV 핵심"},
                "현대모비스": {"ticker": "012330", "reason": "자율주행 센서/모듈 통합, L3 양산"},
                "현대차": {"ticker": "005380", "reason": "2026 페이스카 SDV"},
            },
            "tier2_core": {
                "텔레칩스": {"ticker": "054450", "reason": "차량용 AP(SoC), 인포테인먼트 칩"},
                "칩스앤미디어": {"ticker": "094360", "reason": "NXP에 코덱 공급, 차량용 비전"},
                "넥스트칩": {"ticker": "396270", "reason": "AI ISP 칩, 자율주행 카메라"},
                "모트렉스": {"ticker": "118990", "reason": "HUD, ADAS 부품, 현대차 납품"},
                "엠씨넥스": {"ticker": "097520", "reason": "자동차 카메라모듈, 테슬라 납품"},
            },
            "tier3_emerging": {
                "슈어소프트테크": {"ticker": "344860", "reason": "자율주행 SW 검증, 현대차"},
                "에스오에스랩": {"ticker": "448710", "reason": "자율주행 소프트웨어, CES 주목"},
                "라닉스": {"ticker": "317120", "reason": "V2X 통신칩, 국내 최초"},
                "오비고": {"ticker": "352910", "reason": "SDV 플랫폼, 커넥티드카"},
                "이노시뮬레이션": {"ticker": "274230", "reason": "자율주행 XR 시뮬레이터"},
            },
        },
    },

    "space_aerospace": {
        "name": "우주항공",
        "phase": "정책 주도 성장기",
        "timeline": "2026~2035 한국형 우주경제",
        "emoji": "\U0001f6f0",
        "trigger_keywords": [
            "우주", "항공", "위성", "발사체", "누리호", "스페이스X",
            "우주항공청", "저궤도", "우주인터넷", "SAR위성", "정찰위성",
            "우주산업육성", "KF-21", "KAI", "방위산업",
        ],
        "watchlist": {
            "tier1_platform": {
                "한국항공우주": {"ticker": "047810", "reason": "국내 유일 항공기 체계종합, KF-21"},
                "한화에어로스페이스": {"ticker": "012450", "reason": "항공엔진+방산, 누리호 엔진"},
                "대한항공": {"ticker": "003490", "reason": "군용기 MRO, 위성부품 제조"},
            },
            "tier2_core": {
                "쎄트렉아이": {"ticker": "099320", "reason": "소형 SAR위성, 해외 수출 실적"},
                "인텔리안테크": {"ticker": "189300", "reason": "위성통신 안테나 글로벌 1위"},
                "켄코아에어로스페이스": {"ticker": "274090", "reason": "항공기 부품, 보잉/에어버스 납품"},
                "한국카본": {"ticker": "017960", "reason": "탄소복합재, 항공/방산 소재"},
                "AP위성": {"ticker": "211270", "reason": "위성통신 단말, 5G NTN"},
            },
            "tier3_emerging": {
                "아스트": {"ticker": "067390", "reason": "항공기 기체구조물, KF-21 동체"},
                "비츠로테크": {"ticker": "082800", "reason": "누리호 추진체 부품"},
                "퍼스텍": {"ticker": "093640", "reason": "전자전 장비, 위성탑재체"},
                "하이록코리아": {"ticker": "013030", "reason": "초고압 피팅, 우주발사체 부품"},
                "스피어": {"ticker": "036200", "reason": "정보보안, 위성 통신 보안"},
            },
        },
    },

    "quantum_computing": {
        "name": "양자컴퓨터",
        "phase": "연구개발 -> 초기 상용화",
        "timeline": "2027~2035 본격 상용화 예상",
        "emoji": "\u269b",
        "trigger_keywords": [
            "양자컴퓨터", "양자암호", "양자통신", "양자내성암호", "PQC",
            "QKD", "큐비트", "아이온큐", "IONQ", "구글 윌로우",
            "양자기술", "양자우월성", "양자키분배", "포스트양자",
        ],
        "watchlist": {
            "tier1_platform": {
                "SK텔레콤": {"ticker": "017670", "reason": "양자암호통신 국내 선도, IDQ 투자"},
                "KT": {"ticker": "030200", "reason": "양자암호 네트워크 구축"},
                "삼성전자": {"ticker": "005930", "reason": "양자컴퓨터 칩 연구, 삼성리서치"},
            },
            "tier2_core": {
                "드림시큐리티": {"ticker": "203650", "reason": "양자내성암호(PQC) 국내 선두"},
                "우리넷": {"ticker": "115440", "reason": "양자암호통신 장비, SKT 납품"},
                "ICTK": {"ticker": "431190", "reason": "PUF 보안칩, 양자내성암호 하드웨어"},
                "쏘니드": {"ticker": "060230", "reason": "양자암호 통신모듈 개발"},
            },
            "tier3_emerging": {
                "이와이엘": {"ticker": "277410", "reason": "초전도 소재, 양자컴퓨터 핵심"},
                "크리스탈신소재": {"ticker": "900250", "reason": "단결정 소재, 양자 하드웨어"},
                "엑스게이트": {"ticker": "149010", "reason": "양자내성 VPN, 보안 솔루션"},
                "노르마": {"ticker": "273640", "reason": "IoT 보안, 양자보안 연계"},
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIER_CONFIG = {
    "tier1_platform": {
        "label": "Tier 1 (대형 플랫폼)",
        "weight_min": 0.03,
        "weight_max": 0.05,
        "hold_period": "장기 (1년+)",
        "strategy": "조정 시 분할 매수",
    },
    "tier2_core": {
        "label": "Tier 2 (핵심 부품)",
        "weight_min": 0.01,
        "weight_max": 0.03,
        "hold_period": "중기 (3~12개월)",
        "strategy": "기술적 매수 신호 시",
    },
    "tier3_emerging": {
        "label": "Tier 3 (소형 고위험)",
        "weight_min": 0.005,
        "weight_max": 0.01,
        "hold_period": "트리거 기반 (유동적)",
        "strategy": "뉴스/정책 트리거 시",
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FutureStockScore:
    """Score breakdown for a future tech stock."""

    ticker: str = ""
    name: str = ""
    sector: str = ""
    tier: str = ""
    total_score: int = 0
    tech_maturity: int = 0
    financial_stability: int = 0
    policy_benefit: int = 0
    momentum: int = 0
    valuation: int = 0
    details: list[str] = field(default_factory=list)


@dataclass
class EntrySignal:
    """Entry evaluation result for a future tech stock."""

    ticker: str = ""
    name: str = ""
    sector: str = ""
    signal: str = "WAIT"  # STRONG_BUY, WATCH, WAIT
    conditions_met: int = 0
    conditions_total: int = 5
    conditions_detail: dict[str, bool] = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_all_watchlist_tickers() -> dict[str, dict[str, Any]]:
    """Return a flat dict of ticker -> {name, sector, tier, reason}."""
    result: dict[str, dict[str, Any]] = {}
    for sector_key, sector in FUTURE_SECTORS.items():
        for tier_key, stocks in sector["watchlist"].items():
            for name, info in stocks.items():
                result[info["ticker"]] = {
                    "name": name,
                    "sector": sector_key,
                    "sector_name": sector["name"],
                    "tier": tier_key,
                    "reason": info["reason"],
                }
    return result


def get_sector_watchlist(sector_key: str) -> dict[str, dict[str, Any]]:
    """Return flat dict for a specific sector."""
    result: dict[str, dict[str, Any]] = {}
    sector = FUTURE_SECTORS.get(sector_key, {})
    if not sector:
        return result
    for tier_key, stocks in sector.get("watchlist", {}).items():
        for name, info in stocks.items():
            result[info["ticker"]] = {
                "name": name,
                "tier": tier_key,
                "reason": info["reason"],
            }
    return result


def get_ticker_info(ticker: str) -> dict[str, Any] | None:
    """Look up a single ticker across all sectors."""
    all_tickers = get_all_watchlist_tickers()
    return all_tickers.get(ticker)


def find_tier_for_ticker(ticker: str) -> str:
    """Return the tier key for a given ticker, or ''."""
    info = get_ticker_info(ticker)
    return info["tier"] if info else ""


# ---------------------------------------------------------------------------
# Tech maturity assessment (rule-based, max 25)
# ---------------------------------------------------------------------------

TECH_MATURITY_LEVELS = {
    "revenue": 25,       # 실제 매출 발생
    "contract": 20,      # 수주/계약 확보
    "prototype": 15,     # 시제품/파일럿
    "rnd": 10,           # R&D 단계
    "related": 5,        # 관련성만 있음
}


def assess_tech_maturity(
    ticker: str,
    financial_data: dict[str, Any] | None = None,
    reports: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Assess technology maturity of a future tech stock.

    Uses financial data (revenue growth) and recent reports to estimate.

    Returns (score 0-25, description).
    """
    if financial_data:
        # If we have revenue and it's growing in the relevant sector
        rev_growth = financial_data.get("revenue_growth_pct", 0)
        operating_profit = financial_data.get("operating_profit", 0)
        if operating_profit > 0 and rev_growth > 10:
            return 25, "실제 매출+흑자"
        if rev_growth > 20:
            return 20, "수주/매출 성장"
        if rev_growth > 0:
            return 15, "시제품/초기매출"

    # Check reports for keywords
    if reports:
        report_text = " ".join(r.get("title", "") for r in reports)
        if any(kw in report_text for kw in ["수주", "계약", "납품"]):
            return 20, "수주/계약 확보"
        if any(kw in report_text for kw in ["시제품", "파일럿", "양산"]):
            return 15, "시제품/파일럿"
        if any(kw in report_text for kw in ["개발", "연구", "R&D"]):
            return 10, "R&D 단계"

    # Default: related only
    return 5, "관련성만 있음"


# ---------------------------------------------------------------------------
# Future stock scoring (100-point scale)
# ---------------------------------------------------------------------------

def score_future_stock(
    ticker: str,
    sector_key: str,
    financial_data: dict[str, Any] | None = None,
    reports: list[dict[str, Any]] | None = None,
    has_gov_contract: bool = False,
    is_national_project: bool = False,
    foreign_net_buy_days: int = 0,
    psr: float | None = None,
) -> FutureStockScore:
    """Score a future tech stock on a 100-point scale.

    Categories:
      1. Tech maturity (25)
      2. Financial stability (20)
      3. Policy/govt benefit (20)
      4. Momentum (20)
      5. Valuation (15)
    """
    info = get_ticker_info(ticker)
    name = info["name"] if info else ticker
    tier = info["tier"] if info else ""

    score = FutureStockScore(
        ticker=ticker,
        name=name,
        sector=sector_key,
        tier=tier,
    )

    # 1. Tech maturity (25 points)
    maturity_score, maturity_desc = assess_tech_maturity(
        ticker, financial_data, reports,
    )
    score.tech_maturity = maturity_score
    score.details.append(f"기술성숙도: {maturity_score}/25 ({maturity_desc})")

    # 2. Financial stability (20 points)
    fin_score = 0
    if financial_data:
        if financial_data.get("operating_profit", 0) > 0:
            fin_score += 15
            score.details.append("영업이익 흑자: +15")
        if financial_data.get("debt_ratio", 999) < 100:
            fin_score += 5
            score.details.append("부채비율 100% 미만: +5")
    score.financial_stability = fin_score

    # 3. Policy/govt benefit (20 points)
    policy_score = 0
    if is_national_project:
        policy_score = 20
        score.details.append("국가 프로젝트 참여: +20")
    elif has_gov_contract:
        policy_score = 15
        score.details.append("정부 사업 수주: +15")
    score.policy_benefit = policy_score

    # 4. Momentum (20 points)
    momentum_score = 0
    if reports:
        # Check for recent positive reports
        positive_keywords = ["매수", "목표가 상향", "실적 호조", "수주"]
        recent_positive = any(
            any(kw in r.get("title", "") for kw in positive_keywords)
            for r in reports[:5]
        )
        if recent_positive:
            momentum_score += 10
            score.details.append("최근 긍정 리포트: +10")

    if foreign_net_buy_days > 0:
        momentum_score += min(10, foreign_net_buy_days * 2)
        score.details.append(f"외인 순매수 {foreign_net_buy_days}일: +{min(10, foreign_net_buy_days * 2)}")
    score.momentum = momentum_score

    # 5. Valuation (15 points) - PSR based for future tech
    val_score = 0
    if psr is not None and psr > 0:
        if psr < 3:
            val_score = 15
        elif psr < 7:
            val_score = 10
        elif psr < 15:
            val_score = 5
        score.details.append(f"PSR {psr:.1f}: +{val_score}")
    score.valuation = val_score

    score.total_score = (
        score.tech_maturity
        + score.financial_stability
        + score.policy_benefit
        + score.momentum
        + score.valuation
    )

    return score


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_sector_overview(
    sector_key: str,
    scores: dict[str, FutureStockScore] | None = None,
    triggers: list[dict[str, Any]] | None = None,
    entries: dict[str, EntrySignal] | None = None,
) -> str:
    """Format a single sector's overview for Telegram."""
    sector = FUTURE_SECTORS.get(sector_key, {})
    if not sector:
        return f"알 수 없는 섹터: {sector_key}"

    emoji = sector.get("emoji", "")
    name = sector["name"]
    phase = sector["phase"]
    lines: list[str] = [
        f"{emoji} {name} - {phase}",
    ]

    for tier_key in ["tier1_platform", "tier2_core", "tier3_emerging"]:
        stocks = sector["watchlist"].get(tier_key, {})
        if not stocks:
            continue
        tier_label = TIER_CONFIG[tier_key]["label"]
        stock_parts: list[str] = []
        for sname, sinfo in stocks.items():
            ticker = sinfo["ticker"]
            if scores and ticker in scores:
                stock_parts.append(f"{sname} {scores[ticker].total_score}점")
            else:
                stock_parts.append(sname)
        lines.append(f"  {tier_label}: {', '.join(stock_parts)}")

    # Recent triggers
    if triggers:
        latest = triggers[0]
        lines.append(f"  최근 트리거: {latest.get('title', '')} ({latest.get('date', '')})")
    else:
        lines.append("  최근 트리거: 없음")

    # Entry signals
    if entries:
        strong = [e for e in entries.values() if e.signal == "STRONG_BUY"]
        watch = [e for e in entries.values() if e.signal == "WATCH"]
        if strong:
            for e in strong:
                lines.append(f"  진입 신호: {e.name} STRONG_BUY \u2605")
        elif watch:
            for e in watch[:2]:
                lines.append(f"  진입 신호: {e.name} WATCH (조건 {e.conditions_met}/{e.conditions_total} 충족)")
        else:
            lines.append("  진입 신호: 전종목 WAIT (트리거 대기)")
    else:
        lines.append("  진입 신호: 스코어 미산출")

    return "\n".join(lines)


def format_full_watchlist(
    scores: dict[str, FutureStockScore] | None = None,
    triggers: dict[str, list[dict[str, Any]]] | None = None,
    entries: dict[str, dict[str, EntrySignal]] | None = None,
    future_weight_pct: float = 0.0,
) -> str:
    """Format full watchlist for /future command."""
    lines: list[str] = [
        "\u2550" * 22,
        "\U0001f680 미래기술 워치리스트",
        "\u2550" * 22,
        "",
    ]

    for sector_key in ["autonomous_driving", "space_aerospace", "quantum_computing"]:
        sector_scores = {}
        if scores:
            sector_tickers = get_sector_watchlist(sector_key)
            sector_scores = {t: scores[t] for t in sector_tickers if t in scores}

        sector_triggers = (triggers or {}).get(sector_key, [])
        sector_entries = (entries or {}).get(sector_key, {})

        lines.append(format_sector_overview(
            sector_key, sector_scores, sector_triggers, sector_entries,
        ))
        lines.append("")

    lines.append(f"총 미래기술 비중: {future_weight_pct:.1f}% / 한도 15%")
    lines.append("")
    lines.append("상세 조회:")
    lines.append("  /future ad    - 자율주행")
    lines.append("  /future space - 우주항공")
    lines.append("  /future qc    - 양자컴퓨터")

    return "\n".join(lines)


def format_sector_detail(
    sector_key: str,
    scores: dict[str, FutureStockScore] | None = None,
) -> str:
    """Format detailed view of a sector with individual stock scores."""
    sector = FUTURE_SECTORS.get(sector_key, {})
    if not sector:
        return f"알 수 없는 섹터: {sector_key}"

    emoji = sector.get("emoji", "")
    name = sector["name"]
    phase = sector["phase"]
    timeline = sector["timeline"]

    lines: list[str] = [
        "\u2500" * 25,
        f"{emoji} {name} 상세 워치리스트",
        f"단계: {phase}",
        f"전망: {timeline}",
        "\u2500" * 25,
        "",
    ]

    for tier_key in ["tier1_platform", "tier2_core", "tier3_emerging"]:
        stocks = sector["watchlist"].get(tier_key, {})
        if not stocks:
            continue
        tier_cfg = TIER_CONFIG[tier_key]
        lines.append(f"[{tier_cfg['label']}]")
        lines.append(f"  투자비중: {tier_cfg['weight_min']*100:.1f}~{tier_cfg['weight_max']*100:.1f}%")
        lines.append(f"  보유기간: {tier_cfg['hold_period']}")
        lines.append(f"  매수전략: {tier_cfg['strategy']}")
        lines.append("")

        for sname, sinfo in stocks.items():
            ticker = sinfo["ticker"]
            reason = sinfo["reason"]
            if scores and ticker in scores:
                sc = scores[ticker]
                lines.append(f"  {sname} ({ticker}) - {sc.total_score}점/100")
                for detail in sc.details:
                    lines.append(f"    {detail}")
            else:
                lines.append(f"  {sname} ({ticker})")
            lines.append(f"    {reason}")
            lines.append("")

    return "\n".join(lines)
