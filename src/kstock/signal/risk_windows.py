"""Calendar-based KRX risk windows for tactical coaching.

한국장은 특정 달력 구간에서
- MSCI 리뷰/리밸런싱 기대
- 실적 시즌 재평가
- 연말/분기 포지션 조정
영향으로 단타/스윙 변동성이 커지는 경향이 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class RiskWindowAssessment:
    active: bool
    key: str
    label: str
    severity: int
    coach_line: str
    action_line: str
    scalp_multiplier: float
    swing_multiplier: float
    cash_floor_add: float


def _to_date(value: date | datetime | None = None) -> date:
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    return value


def assess_krx_risk_window(value: date | datetime | None = None) -> RiskWindowAssessment:
    """한국장 달력성 리스크 윈도우를 평가한다.

    2월/8월/11월은 MSCI 리뷰 시즌,
    4월말~5월초는 1분기 실적 + 5월 리뷰 선반영 구간으로 본다.
    """
    today = _to_date(value)
    m = today.month
    d = today.day

    default = RiskWindowAssessment(
        active=False,
        key="",
        label="",
        severity=0,
        coach_line="",
        action_line="",
        scalp_multiplier=1.0,
        swing_multiplier=1.0,
        cash_floor_add=0.0,
    )

    if m == 2 and 1 <= d <= 15:
        return RiskWindowAssessment(
            active=True,
            key="msci_feb",
            label="2월 MSCI 리뷰 윈도우",
            severity=2,
            coach_line="MSCI 2월 리뷰 구간이라 외국인/패시브 수급 왜곡 가능성이 있습니다.",
            action_line="단타는 추격 금지, 스윙은 눌림 확인 후만 진입",
            scalp_multiplier=0.86,
            swing_multiplier=0.92,
            cash_floor_add=3.0,
        )
    if (m == 4 and d >= 20) or (m == 5 and d <= 15):
        return RiskWindowAssessment(
            active=True,
            key="earnings_apr_may",
            label="4월말 실적·5월 선반영 윈도우",
            severity=3,
            coach_line="1분기 실적 재평가와 5월 리스크 선반영이 겹치는 구간입니다.",
            action_line="실적 확인 전 추격매수보다 눌림·외인수급 확인이 우선",
            scalp_multiplier=0.82,
            swing_multiplier=0.88,
            cash_floor_add=5.0,
        )
    if m == 8 and 1 <= d <= 15:
        return RiskWindowAssessment(
            active=True,
            key="msci_aug",
            label="8월 MSCI 리뷰 윈도우",
            severity=2,
            coach_line="여름 비수기와 MSCI 리뷰 기대가 겹쳐 수급 왜곡이 커질 수 있습니다.",
            action_line="단타/스윙은 시초 추격보다 종가 확인형 대응",
            scalp_multiplier=0.88,
            swing_multiplier=0.93,
            cash_floor_add=3.0,
        )
    if m == 11 and 1 <= d <= 30:
        return RiskWindowAssessment(
            active=True,
            key="msci_nov",
            label="11월 MSCI·연말 리밸런싱 윈도우",
            severity=3,
            coach_line="MSCI 11월 리뷰와 연말 포지션 조정으로 대형주·지수 변동성이 커질 수 있습니다.",
            action_line="편입 기대주 외엔 비중 축소, 스윙 신규는 더 보수적으로",
            scalp_multiplier=0.80,
            swing_multiplier=0.86,
            cash_floor_add=5.0,
        )
    return default
