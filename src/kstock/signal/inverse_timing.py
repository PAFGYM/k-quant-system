"""인버스 ETF 단타 타이밍 시그널 모듈.

장중 KOSPI/KOSDAQ 하락 모멘텀 + VIX/환율 급등을 감지하여
인버스 ETF 진입/청산 타이밍을 알려준다.

대상:
  252670 KODEX 200선물인버스2X (코스피)
  251340 KODEX KOSDAQ150인버스 (코스닥)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 인버스 ETF 매핑
# ---------------------------------------------------------------------------

INVERSE_ETFS = {
    "kospi": {
        "ticker": "252670",
        "name": "KODEX 200선물인버스2X",
        "leverage": 2,
    },
    "kosdaq": {
        "ticker": "251340",
        "name": "KODEX KOSDAQ150인버스",
        "leverage": 1,
    },
}

# ---------------------------------------------------------------------------
# 시그널 임계값
# ---------------------------------------------------------------------------

# 진입 조건
ENTRY_INDEX_DROP_PCT = -1.0       # 장중 고점 대비 하락률
ENTRY_VIX_SPIKE_PCT = 10.0       # VIX 전일 대비 급등 %
ENTRY_FX_SPIKE_PCT = 0.5         # USDKRW 전일 대비 급등 %
ENTRY_SCORE_THRESHOLD = 40       # 진입 시그널 최소 점수

# 청산 조건
EXIT_TARGET_PCT = 2.5            # 목표 수익률 %
EXIT_STOP_PCT = -2.0             # 손절 %
EXIT_BOUNCE_PCT = 0.5            # 저점 대비 반등 시 청산
EXIT_FORCE_TIME_HOUR = 14        # 강제 청산 시각 (시)
EXIT_FORCE_TIME_MIN = 50         # 강제 청산 시각 (분)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class InverseSignal:
    """인버스 타이밍 시그널."""

    action: str          # "ENTRY" / "EXIT" / "FORCE_EXIT" / "NONE"
    target: str          # "kospi" / "kosdaq"
    ticker: str          # "252670" / "251340"
    name: str            # ETF 이름
    score: float         # 0~100 신호 강도
    reasons: list[str] = field(default_factory=list)
    suggested_size: str = "소량"  # "소량" / "보통"


# ---------------------------------------------------------------------------
# 진입 시그널 체크
# ---------------------------------------------------------------------------


def check_entry_signal(
    target: str,
    index_change_pct: float,
    index_high_drop_pct: float,
    vix: float,
    vix_change_pct: float,
    usdkrw_change_pct: float,
    regime: str,
    fear_greed: float = 50.0,
) -> InverseSignal:
    """인버스 진입 시그널 판단.

    Args:
        target: "kospi" 또는 "kosdaq".
        index_change_pct: 전일 대비 등락률 (%).
        index_high_drop_pct: 장중 고점 대비 하락률 (%, 음수).
        vix: 현재 VIX.
        vix_change_pct: VIX 전일 대비 변동률 (%).
        usdkrw_change_pct: 환율 전일 대비 변동률 (%).
        regime: 매크로 레짐 (risk_on / neutral / risk_off).
        fear_greed: 공포탐욕 지수 (0~100).
    """
    etf = INVERSE_ETFS[target]
    score = 0.0
    reasons: list[str] = []

    # 1) 지수 하락 모멘텀 (최대 35점)
    if index_change_pct <= ENTRY_INDEX_DROP_PCT:
        pts = min(35, abs(index_change_pct) * 15)
        score += pts
        reasons.append(f"지수 {index_change_pct:+.1f}% 하락 중")

    # 2) 장중 고점 대비 낙폭 (최대 25점)
    if index_high_drop_pct <= ENTRY_INDEX_DROP_PCT:
        pts = min(25, abs(index_high_drop_pct) * 10)
        score += pts
        reasons.append(f"장중 고점 대비 {index_high_drop_pct:.1f}%")

    # 3) VIX 급등 (최대 20점)
    if vix_change_pct >= ENTRY_VIX_SPIKE_PCT:
        pts = min(20, vix_change_pct * 1.0)
        score += pts
        reasons.append(f"VIX {vix:.1f} (+{vix_change_pct:.1f}%)")

    # 4) 환율 급등 (최대 10점)
    if usdkrw_change_pct >= ENTRY_FX_SPIKE_PCT:
        pts = min(10, usdkrw_change_pct * 5)
        score += pts
        reasons.append(f"환율 +{usdkrw_change_pct:.1f}% 급등")

    # 5) risk_off 레짐 보너스 (10점)
    if regime == "risk_off":
        score += 10
        reasons.append("risk_off 레짐")

    # 극단 공포 구간은 오히려 반등 가능 → 감점
    if fear_greed < 15:
        score = max(0, score - 15)
        reasons.append(f"극도 공포({fear_greed:.0f}) 반등 주의")

    score = min(100, score)

    if score >= ENTRY_SCORE_THRESHOLD:
        suggested = "보통" if score >= 65 else "소량"
        return InverseSignal(
            action="ENTRY",
            target=target,
            ticker=etf["ticker"],
            name=etf["name"],
            score=score,
            reasons=reasons,
            suggested_size=suggested,
        )

    return InverseSignal(
        action="NONE",
        target=target,
        ticker=etf["ticker"],
        name=etf["name"],
        score=score,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# 청산 시그널 체크
# ---------------------------------------------------------------------------


def check_exit_signal(
    target: str,
    entry_price: float,
    current_price: float,
    index_low_bounce_pct: float,
    now: datetime | None = None,
) -> InverseSignal:
    """인버스 청산 시그널 판단.

    Args:
        target: "kospi" 또는 "kosdaq".
        entry_price: 진입 가격.
        current_price: 현재 가격.
        index_low_bounce_pct: 장중 저점 대비 반등률 (%, 양수).
        now: 현재 시각.
    """
    etf = INVERSE_ETFS[target]
    pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
    reasons: list[str] = []

    # 1) 목표 수익 도달
    if pnl_pct >= EXIT_TARGET_PCT:
        reasons.append(f"목표 수익 {pnl_pct:+.1f}% 도달")
        return InverseSignal(
            action="EXIT", target=target, ticker=etf["ticker"],
            name=etf["name"], score=90, reasons=reasons,
        )

    # 2) 손절
    if pnl_pct <= EXIT_STOP_PCT:
        reasons.append(f"손절 {pnl_pct:+.1f}% 도달")
        return InverseSignal(
            action="EXIT", target=target, ticker=etf["ticker"],
            name=etf["name"], score=95, reasons=reasons,
        )

    # 3) 반등 시작
    if index_low_bounce_pct >= EXIT_BOUNCE_PCT:
        reasons.append(f"저점 대비 +{index_low_bounce_pct:.1f}% 반등")
        return InverseSignal(
            action="EXIT", target=target, ticker=etf["ticker"],
            name=etf["name"], score=70, reasons=reasons,
        )

    # 4) 강제 청산 시각
    if now is not None:
        if now.hour == EXIT_FORCE_TIME_HOUR and now.minute >= EXIT_FORCE_TIME_MIN:
            reasons.append("14:50 단타 강제 청산 시간")
            return InverseSignal(
                action="FORCE_EXIT", target=target, ticker=etf["ticker"],
                name=etf["name"], score=100, reasons=reasons,
            )
        if now.hour > EXIT_FORCE_TIME_HOUR:
            reasons.append("장 마감 임박 강제 청산")
            return InverseSignal(
                action="FORCE_EXIT", target=target, ticker=etf["ticker"],
                name=etf["name"], score=100, reasons=reasons,
            )

    return InverseSignal(
        action="NONE", target=target, ticker=etf["ticker"],
        name=etf["name"], score=0, reasons=reasons,
    )


# ---------------------------------------------------------------------------
# 종합 체크
# ---------------------------------------------------------------------------


def check_inverse_timing(
    *,
    kospi_change_pct: float = 0.0,
    kospi_high_drop_pct: float = 0.0,
    kosdaq_change_pct: float = 0.0,
    kosdaq_high_drop_pct: float = 0.0,
    vix: float = 0.0,
    vix_change_pct: float = 0.0,
    usdkrw_change_pct: float = 0.0,
    regime: str = "neutral",
    fear_greed: float = 50.0,
) -> list[InverseSignal]:
    """코스피/코스닥 인버스 진입 시그널 종합 체크.

    Returns:
        action이 "ENTRY"인 시그널만 반환. 없으면 빈 리스트.
    """
    signals: list[InverseSignal] = []

    kospi_sig = check_entry_signal(
        "kospi",
        index_change_pct=kospi_change_pct,
        index_high_drop_pct=kospi_high_drop_pct,
        vix=vix,
        vix_change_pct=vix_change_pct,
        usdkrw_change_pct=usdkrw_change_pct,
        regime=regime,
        fear_greed=fear_greed,
    )
    if kospi_sig.action == "ENTRY":
        signals.append(kospi_sig)

    kosdaq_sig = check_entry_signal(
        "kosdaq",
        index_change_pct=kosdaq_change_pct,
        index_high_drop_pct=kosdaq_high_drop_pct,
        vix=vix,
        vix_change_pct=vix_change_pct,
        usdkrw_change_pct=usdkrw_change_pct,
        regime=regime,
        fear_greed=fear_greed,
    )
    if kosdaq_sig.action == "ENTRY":
        signals.append(kosdaq_sig)

    return signals


# ---------------------------------------------------------------------------
# 알림 포맷
# ---------------------------------------------------------------------------


def format_entry_alert(sig: InverseSignal) -> str:
    """진입 시그널 텔레그램 알림 포맷."""
    lines = [
        "🔻 인버스 진입 시그널",
        "━" * 18,
        f"대상: {sig.name}",
        f"종목코드: {sig.ticker}",
        f"신호 강도: {sig.score:.0f}/100",
        "",
        "📊 근거:",
    ]
    for r in sig.reasons:
        lines.append(f"  • {r}")

    lines.extend([
        "",
        f"💰 추천 규모: {sig.suggested_size}",
        "",
        "⚠️ 단타 원칙:",
        "  • 목표: +2~3%",
        "  • 손절: -2%",
        "  • 14:50 전 청산 필수",
    ])
    return "\n".join(lines)


def format_exit_alert(sig: InverseSignal, pnl_pct: float = 0.0) -> str:
    """청산 시그널 텔레그램 알림 포맷."""
    if sig.action == "FORCE_EXIT":
        emoji = "🚨"
        header = "인버스 강제 청산"
    elif pnl_pct >= 0:
        emoji = "✅"
        header = "인버스 익절 시그널"
    else:
        emoji = "🛑"
        header = "인버스 손절 시그널"

    lines = [
        f"{emoji} {header}",
        "━" * 18,
        f"대상: {sig.name}",
        f"수익률: {pnl_pct:+.1f}%",
        "",
    ]
    for r in sig.reasons:
        lines.append(f"  • {r}")

    if sig.action == "FORCE_EXIT":
        lines.extend([
            "",
            "⏰ 당일 청산 원칙!",
            "지금 바로 매도하세요",
        ])
    return "\n".join(lines)
