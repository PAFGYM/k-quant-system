"""Tactical downside playbook for Korean market stress sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kstock.core.tz import KST
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
    flow_lines: list[str] = field(default_factory=list)
    strong_stocks: list[TacticalPick] = field(default_factory=list)
    short_squeeze_watch: list[TacticalPick] = field(default_factory=list)
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


def _pattern_codes(candidate: dict) -> set[str]:
    raw = candidate.get("short_pattern_codes") or []
    codes: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if text:
            codes.add(text)
    return codes


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
    herd_pattern = str(candidate.get("herd_pattern", "") or "")
    event_tags = candidate.get("event_tags") or []
    market_cap = _safe_float(candidate.get("market_cap", 0.0))
    flow_signal = str(candidate.get("flow_signal", "") or "")
    short_ratio = _safe_float(candidate.get("short_ratio", 0.0))
    short_balance_ratio = _safe_float(candidate.get("short_balance_ratio", 0.0))
    short_codes = _pattern_codes(candidate)

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

    if herd_pattern == "진성 세력":
        score += 12.0
        reasons.append("진성 세력")
    elif herd_pattern == "세력 매집 초기":
        score += 8.0
        reasons.append("세력 매집 초기")
    elif herd_pattern == "개미떼 유입":
        score -= 10.0
    elif herd_pattern == "리딩방 급락":
        score -= 14.0

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

    if flow_signal in {"외인+기관 순유입", "기관 방어 매수", "외인 선행 유입"}:
        score += 8.0
        reasons.append(flow_signal)
    elif flow_signal == "외인+기관 동반 이탈":
        score -= 8.0

    if "real_buy" in short_codes:
        score += 8.0
        reasons.append("실매수 전환")
    if "short_covering" in short_codes:
        score += 10.0
        reasons.append("숏커버링")
    if "short_squeeze" in short_codes:
        score += 8.0
        reasons.append("숏스퀴즈")
    if "short_buildup" in short_codes:
        score -= 12.0
    elif short_ratio >= 10.0 or short_balance_ratio >= 5.0:
        score -= 4.0

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
    herd_pattern = str(candidate.get("herd_pattern", "") or "")
    vol_ratio = _safe_float(candidate.get("vol_ratio", 0.0))
    foreign_days = _safe_int(candidate.get("foreign_days", 0))
    inst_days = _safe_int(candidate.get("inst_days", 0))
    relative_resilience = day_change - benchmark_drop
    flow_signal = str(candidate.get("flow_signal", "") or "")
    short_ratio = _safe_float(candidate.get("short_ratio", 0.0))
    short_balance_ratio = _safe_float(candidate.get("short_balance_ratio", 0.0))
    short_codes = _pattern_codes(candidate)

    score = 0.0
    reasons: list[str] = []
    if herd_pattern == "리딩방 급락":
        score += 16.0
        reasons.append("리딩방 급락")
    elif herd_pattern == "개미떼 유입":
        score += 12.0
        reasons.append("개미떼 유입")
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
    if flow_signal == "외인+기관 동반 이탈":
        score += 12.0
        reasons.append("수급 동반 이탈")
    if "short_buildup" in short_codes:
        score += 12.0
        reasons.append("공매도 빌드업")
    elif short_ratio >= 10.0 or short_balance_ratio >= 5.0:
        score += 6.0
        reasons.append("공매도 압력")
    thesis = " · ".join(reasons[:3]) or "변동성 주의"
    return score, thesis


def _score_squeeze_candidate(candidate: dict) -> tuple[float, str]:
    short_codes = _pattern_codes(candidate)
    if not short_codes:
        return 0.0, ""

    crowd_signal = str(candidate.get("crowd_signal", "") or "")
    flow_signal = str(candidate.get("flow_signal", "") or "")
    short_ratio = _safe_float(candidate.get("short_ratio", 0.0))
    short_balance_ratio = _safe_float(candidate.get("short_balance_ratio", 0.0))

    score = 0.0
    reasons: list[str] = []
    if "short_squeeze" in short_codes:
        score += 18.0
        reasons.append("숏스퀴즈 진행")
    if "short_covering" in short_codes:
        score += 14.0
        reasons.append("숏커버링 랠리")
    if "real_buy" in short_codes:
        score += 10.0
        reasons.append("실매수 유입")
    if short_ratio >= 10.0:
        score += min(10.0, short_ratio * 0.5)
        reasons.append(f"공매도 {short_ratio:.1f}%")
    if short_balance_ratio >= 4.0:
        score += min(6.0, short_balance_ratio)
    if flow_signal in {"외인+기관 순유입", "기관 방어 매수", "외인 선행 유입"}:
        score += 6.0
        reasons.append(flow_signal)
    if crowd_signal in _DANGEROUS_CROWD and "short_squeeze" not in short_codes:
        score -= 8.0

    return score, " · ".join(reasons[:3])


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
    program_data: dict | None = None,
) -> DownsidePlaybook:
    """선물/레버리지 급락일 대응 플레이북 생성."""
    risk_score = 0.0
    triggers: list[str] = []
    flow_lines: list[str] = []

    macro_leverage_change = _safe_float(getattr(macro, "kodex_leverage_change_pct", 0.0))
    macro_inverse_change = _safe_float(getattr(macro, "kodex_inverse2x_change_pct", 0.0))
    leverage_drop = _safe_float(
        leverage_change_pct if leverage_change_pct is not None else macro_leverage_change,
        0.0,
    )
    inverse_jump = _safe_float(
        inverse_change_pct if inverse_change_pct is not None else macro_inverse_change,
        0.0,
    )
    kospi_change = _safe_float(getattr(macro, "kospi_change_pct", 0.0))
    kosdaq_change = _safe_float(getattr(macro, "kosdaq_change_pct", 0.0))
    es_change = _safe_float(getattr(macro, "es_futures_change_pct", 0.0))
    nq_change = _safe_float(getattr(macro, "nq_futures_change_pct", 0.0))
    vix = _safe_float(getattr(macro, "vix", 0.0))
    vix_change = _safe_float(getattr(macro, "vix_change_pct", 0.0))
    usdkrw_change = _safe_float(getattr(macro, "usdkrw_change_pct", 0.0))
    ewy_change = _safe_float(getattr(macro, "ewy_change_pct", 0.0))
    koru_change = _safe_float(getattr(macro, "koru_change_pct", 0.0))
    wti_price = _safe_float(getattr(macro, "wti_price", 0.0))
    wti_change = _safe_float(getattr(macro, "wti_change_pct", 0.0))
    brent_price = _safe_float(getattr(macro, "brent_price", 0.0))
    brent_change = _safe_float(getattr(macro, "brent_change_pct", 0.0))

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

    if wti_change >= 5.0 or wti_price >= 95.0:
        risk_score += 10.0
        triggers.append(f"WTI {wti_change:+.1f}%")
        flow_lines.append(
            f"유가 쇼크: WTI ${wti_price:.1f} ({wti_change:+.1f}%) / "
            f"Brent ${brent_price:.1f} ({brent_change:+.1f}%)"
        )
        if usdkrw_change >= 0.7:
            risk_score += 6.0
            flow_lines.append("유가+환율 동시 악화: 한국 수입물가·외인 리스크오프 압력")
        if wti_price >= 95.0 and brent_price >= 95.0:
            flow_lines.append("지정학 프리미엄 확대: 중동/호르무즈 리스크 점검 필요")
    elif wti_change >= 2.0 or brent_change >= 2.0:
        flow_lines.append(
            f"유가 반등: WTI {wti_change:+.1f}% / Brent {brent_change:+.1f}%"
        )

    if ewy_change <= -2.0:
        risk_score += 10.0
        triggers.append(f"EWY(MSCI Korea) {ewy_change:+.1f}%")
        flow_lines.append("패시브 프록시 약세: MSCI Korea 자금 이탈 경계")
    elif ewy_change <= -1.0:
        flow_lines.append(f"패시브 프록시 약세: EWY {ewy_change:+.1f}%")
    elif ewy_change >= 1.0:
        flow_lines.append(f"패시브 프록시 견조: EWY {ewy_change:+.1f}%")

    if koru_change <= -4.0:
        risk_score += 8.0
        triggers.append(f"KORU {koru_change:+.1f}%")
        flow_lines.append("미국 상장 한국 레버리지 약세: 외인 리스크오프 강화")
    elif koru_change >= 2.0:
        flow_lines.append(f"KORU {koru_change:+.1f}% 반등")

    if program_data:
        total_net = _safe_float(program_data.get("total_net", 0.0))
        non_arb_net = _safe_float(program_data.get("non_arb_net", 0.0))
        arb_net = _safe_float(program_data.get("arb_net", 0.0))
        flow_lines.append(
            f"프로그램 {total_net:+,.0f}억 / 비차익 {non_arb_net:+,.0f}억"
        )
        if non_arb_net <= -2500:
            risk_score += 12.0
            triggers.append(f"비차익 {non_arb_net:+,.0f}억")
            flow_lines.append(f"기관/패시브 비차익 {non_arb_net:+,.0f}억 -> 리밸런싱 매도 압력")
        elif non_arb_net >= 2500:
            risk_score = max(0.0, risk_score - 6.0)
            flow_lines.append(f"비차익 {non_arb_net:+,.0f}억 -> 기관 편입/패시브 유입")

        if total_net <= -3000:
            risk_score += 8.0
            triggers.append(f"프로그램 {total_net:+,.0f}억")
        elif total_net >= 3000:
            risk_score = max(0.0, risk_score - 4.0)

        if abs(arb_net) >= 2500:
            flow_lines.append(f"차익 {arb_net:+,.0f}억 -> 베이시스/만기 변동성 주의")

    today = datetime.now(KST)
    if today.month in {2, 5, 8, 11} and today.day >= 20:
        flow_lines.append("MSCI 정기변경 윈도우: 패시브 리밸런싱 변동성 확대 구간")

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
        flow_lines=flow_lines[:4],
    )

    if not candidates:
        return playbook

    benchmark_drop = _benchmark_drop(macro, leverage_change_pct=leverage_change_pct)
    strong: list[TacticalPick] = []
    squeeze_watch: list[TacticalPick] = []
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

        squeeze_score, squeeze_thesis = _score_squeeze_candidate(raw)
        if squeeze_score >= 18.0:
            squeeze_watch.append(TacticalPick(
                ticker=ticker,
                name=name,
                day_change=day_change,
                return_3m=return_3m,
                composite=composite,
                relative_resilience=relative_resilience,
                thesis=squeeze_thesis,
                score=round(squeeze_score, 1),
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
    squeeze_watch.sort(key=lambda item: (-item.score, -item.relative_resilience, -item.day_change))
    avoid.sort(key=lambda item: (-item.score, item.relative_resilience, item.day_change))
    playbook.strong_stocks = strong[:3]
    playbook.short_squeeze_watch = squeeze_watch[:3]
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

    if playbook.flow_lines:
        lines.append("")
        lines.append("수급/패시브")
        for line in playbook.flow_lines:
            lines.append(f"- {line}")

    if playbook.strong_stocks:
        lines.append("")
        lines.append("버티는 강한 종목")
        for pick in playbook.strong_stocks:
            lines.append(
                f"- {pick.name} ({pick.ticker}) | {pick.day_change:+.1f}% | "
                f"RS {pick.return_3m:+.1f}% | {pick.thesis}"
            )

    if playbook.short_squeeze_watch:
        lines.append("")
        lines.append("숏커버 레이더")
        for pick in playbook.short_squeeze_watch:
            lines.append(
                f"- {pick.name} ({pick.ticker}) | {pick.day_change:+.1f}% | "
                f"{pick.thesis}"
            )

    if playbook.avoid_stocks:
        lines.append("")
        lines.append("피해야 할 종목")
        for pick in playbook.avoid_stocks:
            lines.append(
                f"- {pick.name} ({pick.ticker}) | {pick.day_change:+.1f}% | {pick.thesis}"
            )

    return "\n".join(lines)
