"""Tactical downside playbook for Korean market stress sessions."""

from __future__ import annotations

from dataclasses import dataclass, field

from kstock.ingest.macro_client import MacroSnapshot

_DANGEROUS_CROWD = {"개미 과열 경계", "리딩방 급행 주의", "커뮤니티 과열"}
_ETF_NAME_HINTS = ("KODEX", "TIGER", "KOSEF", "ETF", "ETN")


@dataclass
class TacticalPick:
    ticker: str
    name: str
    day_change: float
    return_3m: float
    composite: float
    relative_resilience: float
    thesis: str
    score: float


@dataclass
class DownsidePlaybook:
    regime: str = "normal"
    risk_score: float = 0.0
    headline: str = ""
    summary: str = ""
    triggers: list[str] = field(default_factory=list)
    tactics: list[str] = field(default_factory=list)
    strong_stocks: list[TacticalPick] = field(default_factory=list)
    avoid_stocks: list[TacticalPick] = field(default_factory=list)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _looks_like_etf(candidate: dict) -> bool:
    ticker = str(candidate.get("ticker", "") or "")
    name = str(candidate.get("name", "") or "")
    if ticker in {"069500", "102110", "122630", "114800", "252670", "229200", "233740", "251340"}:
        return True
    return any(hint in name for hint in _ETF_NAME_HINTS)


def _benchmark_drop(
    macro: MacroSnapshot,
    leverage_change_pct: float | None = None,
) -> float:
    drops = [
        _safe_float(getattr(macro, "kospi_change_pct", 0.0)),
        _safe_float(getattr(macro, "kosdaq_change_pct", 0.0)),
    ]
    if leverage_change_pct is not None:
        drops.append(_safe_float(leverage_change_pct) / 1.8)
    return min(0.0, *drops)


def _score_resilient_candidate(candidate: dict, benchmark_drop: float) -> tuple[float, str]:
    day_change = _safe_float(candidate.get("day_change", 0.0))
    return_3m = _safe_float(candidate.get("return_3m", 0.0))
    composite = _safe_float(candidate.get("composite", 0.0))
    foreign_days = _safe_int(candidate.get("foreign_days", 0))
    inst_days = _safe_int(candidate.get("inst_days", 0))
    crowd_signal = str(candidate.get("crowd_signal", "") or "")
    event_tags = candidate.get("event_tags") or []
    market_cap = _safe_float(candidate.get("market_cap", 0.0))

    relative_resilience = day_change - benchmark_drop
    score = 0.0
    reasons: list[str] = []

    if relative_resilience >= 2.0:
        score += 24.0
        reasons.append("시장 대비 하락 방어")
    elif relative_resilience >= 1.0:
        score += 14.0
        reasons.append("시장보다 강함")

    if day_change >= 0:
        score += 10.0
        reasons.append("장중 플러스 유지")
    elif day_change >= -1.0:
        score += 5.0

    if return_3m >= 20:
        score += 18.0
        reasons.append(f"3개월 RS {return_3m:+.1f}%")
    elif return_3m >= 10:
        score += 12.0
        reasons.append(f"3개월 RS {return_3m:+.1f}%")
    elif return_3m >= 5:
        score += 6.0

    if composite >= 75:
        score += 16.0
        reasons.append(f"종합 {composite:.0f}")
    elif composite >= 65:
        score += 10.0
        reasons.append(f"종합 {composite:.0f}")

    if foreign_days >= 2 and inst_days >= 2:
        score += 12.0
        reasons.append("외인·기관 동행")
    elif foreign_days >= 2 or inst_days >= 2:
        score += 6.0
        reasons.append("수급 유입")

    if event_tags:
        score += 4.0
        reasons.append(f"이벤트 {event_tags[0]}")

    if market_cap and market_cap < 300_0000_0000:
        score -= 8.0
    if crowd_signal in _DANGEROUS_CROWD:
        score -= 14.0

    thesis = " · ".join(reasons[:3]) or "상대강도 확인 필요"
    return score, thesis


def _score_avoid_candidate(candidate: dict, benchmark_drop: float) -> tuple[float, str]:
    day_change = _safe_float(candidate.get("day_change", 0.0))
    return_3m = _safe_float(candidate.get("return_3m", 0.0))
    crowd_signal = str(candidate.get("crowd_signal", "") or "")
    vol_ratio = _safe_float(candidate.get("vol_ratio", 0.0))
    foreign_days = _safe_int(candidate.get("foreign_days", 0))
    inst_days = _safe_int(candidate.get("inst_days", 0))
    relative_resilience = day_change - benchmark_drop

    score = 0.0
    reasons: list[str] = []
    if crowd_signal in _DANGEROUS_CROWD:
        score += 18.0
        reasons.append(crowd_signal)
    if day_change <= benchmark_drop - 1.5:
        score += 12.0
        reasons.append("시장보다 더 약함")
    if day_change <= -6.0:
        score += 10.0
        reasons.append("급락 확대")
    if vol_ratio >= 250 and foreign_days <= 0 and inst_days <= 0:
        score += 8.0
        reasons.append("거래량만 과열")
    if return_3m <= -10:
        score += 6.0
    thesis = " · ".join(reasons[:3]) or "변동성 주의"
    return score, thesis


def _build_tactical_lines(regime: str) -> tuple[str, str, list[str]]:
    if regime == "crisis":
        return (
            "🔴 선물 급락 방어 모드",
            "국내 레버리지 붕괴와 변동성 확대가 겹친 구간입니다. 오늘은 반사 매수보다 포지션 방어가 우선입니다.",
            [
                "시초 추격매수 금지. 첫 30~60분은 손절선과 수급부터 확인",
                "신규 진입은 상대강도 상위·외인/기관 동행 종목만 1차 씨앗 접근",
                "리딩방 과열주·레버리지 추가매수는 금지, 인버스/현금으로 완충",
                "기존 보유는 손절선 이탈 종목부터 감축하고 강한 종목만 남기기",
            ],
        )
    if regime == "defense":
        return (
            "🟠 선물 약세 방어 모드",
            "하락 압력이 우세한 날입니다. 공격보다 선별이 중요하고, 강한 종목만 눌림을 노려야 합니다.",
            [
                "오전 변동성 소화 전에는 추격 금지, 눌림 확인 후 분할 접근",
                "수급 동행·이벤트 보유·상대강도 우위 종목만 감시",
                "단타는 손절 짧게, 스윙은 1차 씨앗만 허용",
                "군집 과열 종목은 제외하고 강한 종목의 종가 유지력을 확인",
            ],
        )
    if regime == "caution":
        return (
            "🟡 선물 경계 모드",
            "시장 변동성이 커질 수 있습니다. 매수보다 종목 선별과 속도 조절이 먼저입니다.",
            [
                "추격매수보다 눌림 대기",
                "보유 종목 손절·익절 라인 재점검",
                "상대강도와 수급이 함께 있는 종목만 관심 유지",
            ],
        )
    return (
        "🟢 일반 모드",
        "특별한 방어 플레이가 필요한 구간은 아닙니다.",
        ["기존 계획대로 운용"],
    )


def build_downside_playbook(
    macro: MacroSnapshot,
    candidates: list[dict] | None = None,
    *,
    leverage_change_pct: float | None = None,
    inverse_change_pct: float | None = None,
) -> DownsidePlaybook:
    """선물/레버리지 급락일 대응 플레이북 생성."""
    risk_score = 0.0
    triggers: list[str] = []

    leverage_drop = _safe_float(leverage_change_pct, 0.0)
    inverse_jump = _safe_float(inverse_change_pct, 0.0)
    kospi_change = _safe_float(getattr(macro, "kospi_change_pct", 0.0))
    kosdaq_change = _safe_float(getattr(macro, "kosdaq_change_pct", 0.0))
    es_change = _safe_float(getattr(macro, "es_futures_change_pct", 0.0))
    nq_change = _safe_float(getattr(macro, "nq_futures_change_pct", 0.0))
    vix = _safe_float(getattr(macro, "vix", 0.0))
    vix_change = _safe_float(getattr(macro, "vix_change_pct", 0.0))
    usdkrw_change = _safe_float(getattr(macro, "usdkrw_change_pct", 0.0))

    if leverage_drop <= -5.0:
        risk_score += 30.0
        triggers.append(f"KODEX 레버리지 {leverage_drop:+.1f}% 급락")
    elif leverage_drop <= -3.0:
        risk_score += 20.0
        triggers.append(f"KODEX 레버리지 {leverage_drop:+.1f}%")

    if inverse_jump >= 4.0:
        risk_score += 14.0
        triggers.append(f"인버스2X {inverse_jump:+.1f}% 급등")
    elif inverse_jump >= 2.0:
        risk_score += 8.0
        triggers.append(f"인버스2X {inverse_jump:+.1f}%")

    if kospi_change <= -2.0:
        risk_score += 16.0
        triggers.append(f"코스피 {kospi_change:+.1f}%")
    elif kospi_change <= -1.0:
        risk_score += 8.0

    if kosdaq_change <= -2.5:
        risk_score += 16.0
        triggers.append(f"코스닥 {kosdaq_change:+.1f}%")
    elif kosdaq_change <= -1.5:
        risk_score += 8.0

    if nq_change <= -1.5 or es_change <= -1.2:
        risk_score += 12.0
        triggers.append(f"미국선물 ES {es_change:+.1f}% / NQ {nq_change:+.1f}%")
    elif nq_change <= -0.8 or es_change <= -0.6:
        risk_score += 6.0

    if vix >= 25 or vix_change >= 15:
        risk_score += 14.0
        triggers.append(f"VIX {vix:.1f} ({vix_change:+.1f}%)")
    elif vix >= 20 or vix_change >= 8:
        risk_score += 6.0

    if usdkrw_change >= 0.7:
        risk_score += 10.0
        triggers.append(f"원달러 {usdkrw_change:+.1f}%")
    elif usdkrw_change >= 0.3:
        risk_score += 4.0

    if str(getattr(macro, "regime", "") or "") == "risk_off":
        risk_score += 8.0
        triggers.append("매크로 risk_off")

    if risk_score >= 65:
        regime = "crisis"
    elif risk_score >= 40:
        regime = "defense"
    elif risk_score >= 20:
        regime = "caution"
    else:
        regime = "normal"

    headline, summary, tactics = _build_tactical_lines(regime)
    playbook = DownsidePlaybook(
        regime=regime,
        risk_score=round(risk_score, 1),
        headline=headline,
        summary=summary,
        triggers=triggers[:5],
        tactics=tactics,
    )

    if not candidates:
        return playbook

    benchmark_drop = _benchmark_drop(macro, leverage_change_pct=leverage_change_pct)
    strong: list[TacticalPick] = []
    avoid: list[TacticalPick] = []
    for raw in candidates:
        if _looks_like_etf(raw):
            continue
        name = str(raw.get("name", "") or "")
        ticker = str(raw.get("ticker", "") or "")
        if not name or not ticker:
            continue
        day_change = _safe_float(raw.get("day_change", 0.0))
        return_3m = _safe_float(raw.get("return_3m", 0.0))
        composite = _safe_float(raw.get("composite", 0.0))
        relative_resilience = round(day_change - benchmark_drop, 1)

        strong_score, strong_thesis = _score_resilient_candidate(raw, benchmark_drop)
        crowd_signal = str(raw.get("crowd_signal", "") or "")
        if (
            strong_score >= 28.0
            and relative_resilience >= 0.8
            and crowd_signal not in _DANGEROUS_CROWD
        ):
            strong.append(TacticalPick(
                ticker=ticker,
                name=name,
                day_change=day_change,
                return_3m=return_3m,
                composite=composite,
                relative_resilience=relative_resilience,
                thesis=strong_thesis,
                score=round(strong_score, 1),
            ))

        avoid_score, avoid_thesis = _score_avoid_candidate(raw, benchmark_drop)
        if avoid_score >= 18.0:
            avoid.append(TacticalPick(
                ticker=ticker,
                name=name,
                day_change=day_change,
                return_3m=return_3m,
                composite=composite,
                relative_resilience=relative_resilience,
                thesis=avoid_thesis,
                score=round(avoid_score, 1),
            ))

    strong.sort(key=lambda item: (-item.score, -item.relative_resilience, -item.return_3m))
    avoid.sort(key=lambda item: (-item.score, item.relative_resilience, item.day_change))
    playbook.strong_stocks = strong[:3]
    playbook.avoid_stocks = avoid[:3]
    return playbook


def format_downside_playbook(playbook: DownsidePlaybook) -> str:
    """텔레그램용 플레이북 텍스트."""
    if playbook.regime == "normal":
        return ""

    lines = [
        playbook.headline,
        "━" * 22,
        f"리스크 점수: {playbook.risk_score:.0f}",
        playbook.summary,
    ]
    if playbook.triggers:
        lines.append("")
        lines.append("트리거")
        for trigger in playbook.triggers:
            lines.append(f"- {trigger}")

    if playbook.tactics:
        lines.append("")
        lines.append("오늘 플레이")
        for idx, tactic in enumerate(playbook.tactics, 1):
            lines.append(f"{idx}. {tactic}")

    if playbook.strong_stocks:
        lines.append("")
        lines.append("버티는 강한 종목")
        for pick in playbook.strong_stocks:
            lines.append(
                f"- {pick.name} ({pick.ticker}) | {pick.day_change:+.1f}% | "
                f"RS {pick.return_3m:+.1f}% | {pick.thesis}"
            )

    if playbook.avoid_stocks:
        lines.append("")
        lines.append("피해야 할 종목")
        for pick in playbook.avoid_stocks:
            lines.append(
                f"- {pick.name} ({pick.ticker}) | {pick.day_change:+.1f}% | {pick.thesis}"
            )

    return "\n".join(lines)
