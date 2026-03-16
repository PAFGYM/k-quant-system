"""지능형 알림 시스템 (v6.5).

기존 단순 알림을 "이유 + 액션 + 컨텍스트" 포함하는 스마트 알림으로 강화.

알림 유형:
1. 보유종목 가격 알림 (손절/익절 + 이유 + 추천 액션)
2. 매수 기회 알림 (왜 기회인지 + 과거 적중률)
3. 리스크 알림 (구체적 위험 + 방어 전략)
4. 학습 알림 (이번 주 학습 결과 요약)

v6.5: signal_guard 통합 — 장기보유 매도 억제 + 신뢰도 등급

핵심 원칙: 모든 알림에 "왜?" + "뭘 해야 하나?" 포함
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def build_holding_alert(
    name: str,
    ticker: str,
    pnl_pct: float,
    buy_price: float,
    current_price: float,
    holding_type: str = "swing",
    hold_days: int = 0,
    stop_price: float | None = None,
    target_price: float | None = None,
    market_regime: str = "",
    signal_weight: float = 1.0,
    consensus: str = "",
    confidence: float = 0.5,
    agreement: float = 0.5,
) -> str | None:
    """보유종목 상태에 따라 이유+액션 포함 스마트 알림 생성.

    v6.5: signal_guard 통합 — 장기보유 매도 신호 억제.

    Returns:
        알림 메시지 (None이면 알림 불필요).
    """
    if pnl_pct == 0 and hold_days < 2:
        return None  # 진입 직후는 알림 불필요

    horizon_kr = {
        "scalp": "초단타", "swing": "스윙",
        "position": "포지션", "long_term": "장기",
    }.get(holding_type, holding_type)

    # v6.5: 장기보유 보호 — 매도 신호 억제 체크
    if holding_type in ("long_term", "position") and pnl_pct <= -7:
        try:
            from kstock.signal.signal_guard import (
                apply_holding_guard,
                format_guard_result,
            )
            guard = apply_holding_guard(
                consensus=consensus or "SELL",
                holding_type=holding_type,
                hold_days=hold_days,
                pnl_pct=pnl_pct,
                confidence=confidence,
                agreement=agreement,
                market_regime=market_regime,
            )
            if guard.suppressed:
                # 장기/포지션 보유 → 매도 억제됨 → 보호 알림만 전송
                lines: list[str] = [
                    f"🛡 장기보유 보호: {name}",
                    f"{'━' * 20}",
                    f"현재: {current_price:,.0f}원 ({pnl_pct:+.1f}%)",
                    f"매수가: {buy_price:,.0f}원 | {horizon_kr} {hold_days}일차",
                    "",
                    "📌 매도 신호 감지되었으나 억제됨:",
                    f"  • {guard.reason}",
                    "",
                    "🎯 추천 액션:",
                    "  1. 현재 보유 유지",
                    "  2. 펀더멘탈 재확인 (실적/뉴스)",
                ]
                if guard.override_conditions:
                    lines.append("")
                    lines.append("⚠️ 다음 조건 시 매도 허용:")
                    for cond in guard.override_conditions:
                        lines.append(f"  • {cond}")
                return "\n".join(lines)
        except Exception as e:
            logger.warning("Signal guard check failed: %s", e)

    lines: list[str] = []

    # === 손절/손절선 근접 알림 ===
    if pnl_pct <= -4.5 and holding_type not in ("long_term", "position"):
        is_hard_stop = pnl_pct <= -7
        lines.append(f"{'🚨' if is_hard_stop else '⚠️'} 손절선 점검: {name}")
        lines.append(f"{'━' * 20}")
        lines.append(f"현재: {current_price:,.0f}원 ({pnl_pct:+.1f}%)")
        lines.append(f"매수가: {buy_price:,.0f}원 | {horizon_kr}")
        lines.append("")

        # 이유
        reasons = []
        if is_hard_stop:
            reasons.append(f"손실 {abs(pnl_pct):.1f}% — {horizon_kr} 기준 손절선 이탈")
        else:
            reasons.append(f"손실 {abs(pnl_pct):.1f}% — {horizon_kr} 기준 손절선 근접")
        if holding_type == "scalp" and hold_days > 3:
            reasons.append(f"초단타 보유 {hold_days}일 — 계획 초과")
        if holding_type == "swing" and hold_days > 15:
            reasons.append(f"스윙 보유 {hold_days}일 — 장기 전환 또는 손절 결정 필요")
        if market_regime in ("fear", "panic"):
            reasons.append(f"시장 {market_regime} 모드 — 추가 하락 위험")

        lines.append("❓ 이유:")
        for r in reasons:
            lines.append(f"  • {r}")
        lines.append("")

        # 액션
        lines.append("🎯 추천 액션:")
        if pnl_pct <= -10:
            lines.append("  1. 비중부터 즉시 줄이고 뉴스/수급 재확인")
            lines.append("  2. 반등 실패 시 남은 물량 정리")
        elif is_hard_stop:
            lines.append("  1. 종가 기준 회복 여부 확인 전 30~50% 축소 검토")
            lines.append("  2. 수급 약화 지속 시 남은 물량 정리")
        else:
            lines.append("  1. 성급한 일괄 정리보다 종가 회복 여부 먼저 확인")
            lines.append("  2. 약한 레인이면 30% 내외 축소로 리스크 축소")

        if stop_price and stop_price > 0:
            lines.append(f"  📌 설정 손절가: {stop_price:,.0f}원")

        return "\n".join(lines)

    # === 익절 알림 ===
    elif pnl_pct >= 8:
        lines.append(f"🎉 익절 타이밍: {name}")
        lines.append(f"{'━' * 20}")
        lines.append(f"현재: {current_price:,.0f}원 ({pnl_pct:+.1f}%)")
        lines.append(f"매수가: {buy_price:,.0f}원 | {horizon_kr}")
        lines.append("")

        # 이유
        reasons = []
        if holding_type == "scalp" and pnl_pct >= 5:
            reasons.append(f"초단타 목표({5}%) 달성 — 익절 우선")
        elif holding_type == "swing" and pnl_pct >= 8:
            reasons.append(f"스윙 목표({8}%) 달성 — 분할 익절 추천")
        elif pnl_pct >= 15:
            reasons.append(f"수익 {pnl_pct:.0f}% — 과열 구간 진입 가능")

        if market_regime in ("fear", "panic"):
            reasons.append("변동성 확대 구간 — 수익 확보 우선")
        elif market_regime == "calm":
            reasons.append("안정 시장 — 목표가 도달 시 부분 익절")

        lines.append("❓ 이유:")
        for r in reasons:
            lines.append(f"  • {r}")
        lines.append("")

        # 액션
        lines.append("🎯 추천 액션:")
        if pnl_pct >= 15:
            lines.append("  1. 50% 익절 (확실한 수익)")
            lines.append("  2. 트레일링 스탑 설정 (+5%에서 걸기)")
        elif holding_type == "scalp":
            lines.append("  1. 전량 익절 (초단타 원칙)")
        else:
            lines.append("  1. 30~50% 부분 익절")
            lines.append("  2. 나머지는 트레일링 스탑으로 보호")

        if target_price and target_price > 0:
            lines.append(f"  📌 설정 목표가: {target_price:,.0f}원")

        return "\n".join(lines)

    # === 보유 기간 초과 알림 ===
    elif holding_type == "scalp" and hold_days > 3:
        lines.append(f"⏰ 보유 기간 초과: {name}")
        lines.append(f"{'━' * 20}")
        lines.append(f"수익률: {pnl_pct:+.1f}% | 보유: {hold_days}일")
        lines.append("")
        lines.append("❓ 이유:")
        lines.append(f"  • 초단타 기준 {hold_days}일 보유 — 원칙 위반")
        lines.append("")
        lines.append("🎯 추천 액션:")
        if pnl_pct > 0:
            lines.append("  1. 즉시 전량 익절")
        elif pnl_pct > -3:
            lines.append("  1. 즉시 청산 (원금 보전)")
        else:
            lines.append("  1. 즉시 손절 (손실 확대 방지)")
        return "\n".join(lines)

    return None


def build_opportunity_alert(
    name: str,
    ticker: str,
    score: float,
    signal: str,
    reasons: list[str],
    signal_source: str = "scan_engine",
    hit_rate: float = 0,
    past_recommendations: int = 0,
    reliability_grade: str = "",
    reliability_emoji: str = "",
) -> str:
    """매수 기회 알림 (이유 + 과거 적중률 포함).

    Returns:
        알림 메시지 문자열.
    """
    from kstock.signal.auto_debrief import SIGNAL_SOURCES

    source_kr = SIGNAL_SOURCES.get(signal_source, signal_source)

    signal_emoji = {
        "STRONG_BUY": "🟢", "BUY": "🟢",
        "WATCH": "🟡", "MILD_BUY": "🟡",
        "HOLD": "⚪", "SELL": "🔴",
    }.get(signal, "⚪")

    # v6.5: 신뢰도 등급 표시
    grade_text = ""
    if reliability_grade:
        grade_text = f" | {reliability_emoji} {reliability_grade}등급"

    lines = [
        f"💡 매수 기회: {name}({ticker})",
        f"{'━' * 20}",
        f"{signal_emoji} 점수: {score:.0f}점 | {signal}{grade_text}",
        f"출처: {source_kr}",
        "",
    ]

    if reasons:
        lines.append("❓ 이유:")
        for r in reasons[:3]:
            lines.append(f"  • {r}")
        lines.append("")

    if hit_rate > 0:
        lines.append(f"📊 과거 적중률: {hit_rate:.0f}% ({past_recommendations}건)")
        if hit_rate >= 70:
            lines.append("  → 신뢰도 높은 신호")
        elif hit_rate >= 50:
            lines.append("  → 보통 수준의 신뢰도")
        else:
            lines.append("  → 신뢰도 낮음 — 소량 진입 권장")
        lines.append("")

    lines.append("🎯 추천 액션:")
    if score >= 130:
        lines.append("  1. 계획 금액의 50~70% 즉시 진입")
        lines.append("  2. 나머지는 눌림 시 추가 매수")
    elif score >= 110:
        lines.append("  1. 계획 금액의 30~50% 진입")
        lines.append("  2. 추가 확인 후 결정")
    else:
        lines.append("  1. 관심 등록 (워치리스트)")
        lines.append("  2. 눌림 시 재확인 후 소량 진입")

    return "\n".join(lines)


def build_risk_alert(
    alert_type: str,
    details: dict,
    market_regime: str = "",
    holdings: list[dict] | None = None,
) -> str:
    """리스크 알림 (구체적 위험 + 방어 전략).

    alert_type: 'vix_spike', 'market_drop', 'concentration', 'mdd'
    """
    lines: list[str] = []

    if alert_type == "vix_spike":
        vix = details.get("vix", 0)
        change = details.get("change", 0)
        lines.append(f"🚨 VIX 급등 경보")
        lines.append(f"{'━' * 20}")
        lines.append(f"VIX: {vix:.1f} ({change:+.1f})")
        lines.append("")
        lines.append("❓ 이유:")
        if vix >= 30:
            lines.append("  • 패닉 수준 — 대규모 불확실성 반영")
        elif vix >= 25:
            lines.append("  • 공포 수준 — 시장 변동성 급확대")
        lines.append("")
        lines.append("🎯 방어 전략:")
        lines.append("  1. 신규 매수 보류")
        if holdings:
            scalps = [h for h in holdings if h.get("holding_type") == "scalp"]
            if scalps:
                lines.append(f"  2. 초단타 {len(scalps)}종목 즉시 청산 검토")
        lines.append("  3. 전체 포지션 사이즈 20% 축소")
        lines.append("  4. 현금 비중 확대")

    elif alert_type == "market_drop":
        drop_pct = details.get("drop_pct", 0)
        index_name = details.get("index", "코스피")
        lines.append(f"🔴 시장 급락 경보")
        lines.append(f"{'━' * 20}")
        lines.append(f"{index_name}: {drop_pct:+.1f}%")
        lines.append("")
        lines.append("❓ 이유:")
        lines.append(f"  • {index_name} {abs(drop_pct):.1f}% 하락")
        if market_regime in ("fear", "panic"):
            lines.append(f"  • 시장 {market_regime} 모드 진입")
        lines.append("")
        lines.append("🎯 방어 전략:")
        lines.append("  1. 추가 매수 보류")
        lines.append("  2. 손절 기준 재확인")
        if drop_pct <= -3:
            lines.append("  3. 포지션 50% 축소 검토")

    elif alert_type == "concentration":
        top_stock = details.get("top_stock", "")
        top_pct = details.get("top_pct", 0)
        lines.append(f"⚠️ 집중 위험 경보")
        lines.append(f"{'━' * 20}")
        lines.append(f"최대 비중: {top_stock} ({top_pct:.0f}%)")
        lines.append("")
        lines.append("❓ 이유:")
        lines.append(f"  • 단일 종목 비중 {top_pct:.0f}% — 분산 부족")
        lines.append("  • 해당 종목 급락 시 포트폴리오 전체 타격")
        lines.append("")
        lines.append("🎯 추천 액션:")
        lines.append(f"  1. {top_stock} 비중 30% 이하로 조정")
        lines.append("  2. 다른 섹터 종목으로 분산")

    elif alert_type == "mdd":
        mdd = details.get("mdd", 0)
        peak = details.get("peak", 0)
        current = details.get("current", 0)
        lines.append(f"🔴 MDD 경고")
        lines.append(f"{'━' * 20}")
        lines.append(f"최대 낙폭: {mdd:.1f}%")
        lines.append(f"고점: {peak:,.0f}원 → 현재: {current:,.0f}원")
        lines.append("")
        lines.append("❓ 이유:")
        if mdd <= -15:
            lines.append("  • 심각한 포트폴리오 손실 발생")
        elif mdd <= -10:
            lines.append("  • 상당한 누적 손실 — 전략 재검토 필요")
        lines.append("")
        lines.append("🎯 방어 전략:")
        lines.append("  1. 전체 포트폴리오 리뷰 실시")
        lines.append("  2. 손실 종목 개별 진단 + 손절 결정")
        lines.append("  3. 신규 매수 중단 (안정화 후 재개)")

    else:
        lines.append(f"⚠️ 리스크 알림: {alert_type}")
        lines.append(str(details))

    return "\n".join(lines)


def build_learning_summary_alert(
    win_rate: float,
    avg_pnl: float,
    top_lessons: list[str],
    weight_changes: dict[str, str],
) -> str:
    """주간 학습 결과 요약 알림."""
    lines = [
        "🧠 이번 주 학습 결과",
        f"{'━' * 20}",
        "",
        f"승률: {win_rate:.0f}% | 평균 수익: {avg_pnl:+.1f}%",
        "",
    ]

    if top_lessons:
        lines.append("💡 핵심 교훈:")
        for i, lesson in enumerate(top_lessons[:3], 1):
            lines.append(f"  {i}. {lesson}")
        lines.append("")

    if weight_changes:
        lines.append("🔧 시스템 자동 조정:")
        for source, change in weight_changes.items():
            lines.append(f"  • {source}: {change}")
        lines.append("")

    lines.append("다음 주 주의사항:")
    if win_rate < 50:
        lines.append("  → 보수적 접근 (포지션 사이즈 축소)")
    elif win_rate >= 70:
        lines.append("  → 현행 유지 (자신감 있는 구간)")
    else:
        lines.append("  → 선별적 진입 (고확률 기회만)")

    return "\n".join(lines)
