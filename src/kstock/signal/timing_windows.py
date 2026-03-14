"""Multi-window turning-point timing coach for K-Quant.

짧은 파동(7일), 중간 파동(15일), 긴 파동(30일)을 함께 보고
변곡 '시작'이 아니라 '끝자락 확인'에 가까운지 판단한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class TimingWindowSignal:
    window_days: int
    phase: str
    low_age: int
    rebound_pct: float
    stretch_pct: float
    line: str


@dataclass
class TimingAssessment:
    overall_phase: str
    preferred_window: int
    coach_line: str
    detail_lines: list[str]


def _phase_label(
    *,
    low_age: int,
    rebound_pct: float,
    stretch_pct: float,
    window_days: int,
) -> str:
    # 너무 이른 구간: 저점 근처에서 아직 반등 확인이 거의 없음
    if rebound_pct < 2.5:
        return "early"
    # 변곡 끝자락: 저점이 최근에 나왔고, 적당히 반등했지만 아직 과하게 뜨진 않음
    if low_age <= max(2, window_days // 3) and 2.5 <= rebound_pct <= 11 and stretch_pct <= 3.5:
        return "end"
    # 추격 구간: 저점 대비 많이 떠서 이미 먹을 구간을 지나감
    if rebound_pct >= 14 or stretch_pct >= 6:
        return "late"
    return "mid"


def analyze_timing_windows(close_series: pd.Series) -> TimingAssessment | None:
    if close_series is None or len(close_series) < 35:
        return None

    close = close_series.astype(float).dropna()
    if len(close) < 35:
        return None

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    current = float(close.iloc[-1])
    current_ma5 = float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else current
    current_ma20 = float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else current

    window_signals: list[TimingWindowSignal] = []
    for window in (7, 15, 30):
        tail = close.tail(window)
        if len(tail) < window:
            continue
        low = float(tail.min())
        low_pos = int(tail.values.argmin())
        low_age = window - 1 - low_pos
        rebound_pct = ((current - low) / low * 100) if low > 0 else 0.0
        stretch_pct = ((current - current_ma20) / current_ma20 * 100) if current_ma20 > 0 else 0.0
        phase = _phase_label(
            low_age=low_age,
            rebound_pct=rebound_pct,
            stretch_pct=stretch_pct,
            window_days=window,
        )
        phase_text = {
            "early": "변곡 시작 전",
            "mid": "반등 확인 중",
            "end": "변곡 끝자락",
            "late": "추격 구간",
        }[phase]
        line = (
            f"{window}일축 {phase_text} · 저점 {low_age}일 전 · "
            f"저점대비 {rebound_pct:+.1f}% · MA20 괴리 {stretch_pct:+.1f}%"
        )
        window_signals.append(
            TimingWindowSignal(
                window_days=window,
                phase=phase,
                low_age=low_age,
                rebound_pct=rebound_pct,
                stretch_pct=stretch_pct,
                line=line,
            )
        )

    if not window_signals:
        return None

    preferred = next((sig for sig in window_signals if sig.phase == "end"), None)
    if preferred is None:
        preferred = next((sig for sig in window_signals if sig.phase == "mid"), None)
    if preferred is None:
        preferred = window_signals[0]

    if preferred.phase == "end":
        coach = (
            f"{preferred.window_days}일 중심 변곡 끝자락 확인. "
            f"지금은 씨앗 또는 1차 분할이 맞고, MA5 유지 여부를 같이 확인하세요."
        )
        overall = "end"
    elif preferred.phase == "mid":
        coach = (
            f"{preferred.window_days}일 중심 반등 확인 중. "
            "시작은 지났지만 추격 전이고, 눌림이 오면 분할 접근이 유리합니다."
        )
        overall = "mid"
    elif preferred.phase == "late":
        coach = (
            f"{preferred.window_days}일 중심은 이미 추격 구간입니다. "
            "지금은 신규 매수보다 눌림 재확인 대기가 낫습니다."
        )
        overall = "late"
    else:
        coach = (
            f"{preferred.window_days}일 중심 저점 확인이 아직 부족합니다. "
            "변곡 시작 구간일 가능성이 커서 서두르지 않는 편이 낫습니다."
        )
        overall = "early"

    if current < current_ma5:
        coach += " 현재가가 MA5 아래라 하루 더 확인하는 쪽이 안전합니다."

    return TimingAssessment(
        overall_phase=overall,
        preferred_window=preferred.window_days,
        coach_line=coach,
        detail_lines=[sig.line for sig in window_signals],
    )
