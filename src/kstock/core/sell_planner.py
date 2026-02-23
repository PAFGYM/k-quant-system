"""매도 계획 엔진 - Phase 8.

투자 시계별 매도 전략 자동 생성.
매일 장 마감 후 + 시장 변화 시 업데이트.

투자 시계:
  scalp (1~3일): +3~8% 목표, -3% 손절
  swing (1~2주): +8~15% 목표, -5% 손절
  mid (1~3개월): +15~30% 목표, -8% 손절
  long (3개월+): +30~100% 목표, -15% 손절
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

USER_NAME = "주호님"


@dataclass
class SellPlan:
    """종목별 매도 계획."""
    ticker: str
    name: str
    horizon: str  # scalp, swing, mid, long
    target: str
    stoploss: str
    strategy: str
    urgency: str  # high, medium, low
    pnl_pct: float = 0.0


class SellPlanner:
    """투자 시계별 매도 계획 엔진."""

    def create_plan(self, holding: dict, market_state: str = "NEUTRAL") -> SellPlan:
        """종목별 매도 계획 생성.

        Args:
            holding: Dict with keys: ticker, name, buy_price (or entry_price),
                     current_price, pnl_pct, horizon, ma5, ma20.
            market_state: Current market pulse state.

        Returns:
            SellPlan instance.
        """
        horizon = holding.get("horizon", "swing")
        entry = holding.get("buy_price", holding.get("entry_price", 0))
        current = holding.get("current_price", entry)
        pnl_pct = holding.get("pnl_pct", 0)

        # pnl_pct는 항상 퍼센트 단위 (예: 5.7 = 5.7%)
        pnl_rate = pnl_pct / 100

        if horizon == "scalp":
            plan = self._scalp_plan(holding, pnl_rate, entry, current, market_state)
        elif horizon == "swing":
            plan = self._swing_plan(holding, pnl_rate, entry, current, market_state)
        elif horizon == "mid":
            plan = self._mid_plan(holding, pnl_rate, entry, current, market_state)
        else:
            plan = self._long_plan(holding, pnl_rate, entry, current, market_state)

        plan.ticker = holding.get("ticker", "")
        plan.name = holding.get("name", "")
        plan.horizon = horizon
        plan.pnl_pct = pnl_pct
        return plan

    def _scalp_plan(
        self, holding: dict, pnl_rate: float, entry: float,
        current: float, market_state: str,
    ) -> SellPlan:
        """단타 (1~3일) 매도 계획."""
        if pnl_rate >= 0.05:
            # 수익 5% 이상: 트레일링 스탑
            trail_stop = int(current * 0.97)
            return SellPlan(
                ticker="", name="", horizon="scalp",
                target=f"{int(entry * 1.08):,}원 (+8%)",
                stoploss=f"{trail_stop:,}원 (고점 -3%)",
                strategy=(
                    f"수익 +{pnl_rate:.1%} 구간. "
                    f"트레일링 스탑 -3% 설정. "
                    f"오늘 장 마감 전 50% 이상 익절 권장."
                ),
                urgency="high",
            )
        elif pnl_rate <= -0.02:
            return SellPlan(
                ticker="", name="", horizon="scalp",
                target=f"{int(entry * 1.03):,}원 (+3%)",
                stoploss=f"{int(entry * 0.97):,}원 (-3%)",
                strategy=(
                    f"손실 {pnl_rate:.1%} 구간. "
                    f"손절가 {int(entry * 0.97):,}원 도달 시 즉시 매도. "
                    f"반등 없으면 내일 오전 정리."
                ),
                urgency="high",
            )
        else:
            return SellPlan(
                ticker="", name="", horizon="scalp",
                target=f"{int(entry * 1.05):,}원 (+5%)",
                stoploss=f"{int(entry * 0.97):,}원 (-3%)",
                strategy=(
                    f"보합 구간. 목표가 또는 손절가 도달까지 홀딩. "
                    f"장 마감 30분 전 재점검."
                ),
                urgency="medium",
            )

    def _swing_plan(
        self, holding: dict, pnl_rate: float, entry: float,
        current: float, market_state: str,
    ) -> SellPlan:
        """스윙 (1~2주) 매도 계획."""
        ma5 = holding.get("ma5", entry)
        ma20 = holding.get("ma20", entry)
        below_ma20 = current < ma20 if ma20 > 0 else False

        if pnl_rate >= 0.10:
            return SellPlan(
                ticker="", name="", horizon="swing",
                target=f"{int(entry * 1.15):,}원 (+15%)",
                stoploss=f"{int(ma5):,}원 (5일선)",
                strategy=(
                    f"수익 +{pnl_rate:.1%} 구간. "
                    f"1차 목표 근접. 50% 익절 후 나머지 +15% 목표. "
                    f"5일선 이탈 시 잔여분 정리."
                ),
                urgency="medium",
            )
        elif below_ma20:
            return SellPlan(
                ticker="", name="", horizon="swing",
                target=f"{int(ma20):,}원 (20일선 회복)",
                stoploss=f"{int(entry * 0.95):,}원 (-5%)",
                strategy=(
                    f"20일선 이탈 상태. 주의 필요. "
                    f"3일 내 20일선 회복 못하면 손절 고려. "
                    f"거래량 동반 회복 시에만 홀딩."
                ),
                urgency="high",
            )
        else:
            target_pct = max(10, pnl_rate * 100 + 5)
            return SellPlan(
                ticker="", name="", horizon="swing",
                target=f"{int(entry * 1.10):,}원 (+10%)",
                stoploss=f"{int(entry * 0.95):,}원 (-5%)",
                strategy=(
                    f"정상 궤도. 20일선 위 유지 중. "
                    f"이번 주 목표 +{target_pct:.0f}%. "
                    f"섹터 약세 전환 시 재점검."
                ),
                urgency="low",
            )

    def _mid_plan(
        self, holding: dict, pnl_rate: float, entry: float,
        current: float, market_state: str,
    ) -> SellPlan:
        """중기 (1~3개월) 매도 계획."""
        if pnl_rate >= 0.20:
            return SellPlan(
                ticker="", name="", horizon="mid",
                target=f"{int(entry * 1.30):,}원 (+30%)",
                stoploss=f"{int(current * 0.92):,}원 (고점 -8%)",
                strategy=(
                    f"목표 구간 진입. 30% 부분 익절 후 "
                    f"나머지 +30% 추가 목표. "
                    f"실적 발표 전후 재점검."
                ),
                urgency="medium",
            )
        elif pnl_rate <= -0.05:
            return SellPlan(
                ticker="", name="", horizon="mid",
                target=f"{int(entry * 1.15):,}원 (+15%)",
                stoploss=f"{int(entry * 0.92):,}원 (-8%)",
                strategy=(
                    f"손실 {pnl_rate:.1%} 구간. "
                    f"투자 논리 재점검 필요. "
                    f"-8% 도달 시 손절. 실적 개선 확인 시 홀딩."
                ),
                urgency="high" if pnl_rate <= -0.07 else "medium",
            )
        else:
            return SellPlan(
                ticker="", name="", horizon="mid",
                target=f"{int(entry * 1.25):,}원 (+25%)",
                stoploss=f"{int(entry * 0.92):,}원 (-8%)",
                strategy=(
                    f"정상 보유 구간. 분기 실적 중심 판단. "
                    f"업종 트렌드와 기관/외인 수급 모니터링."
                ),
                urgency="low",
            )

    def _long_plan(
        self, holding: dict, pnl_rate: float, entry: float,
        current: float, market_state: str,
    ) -> SellPlan:
        """장기 (3개월+) 매도 계획."""
        if pnl_rate >= 0.40:
            return SellPlan(
                ticker="", name="", horizon="long",
                target=f"{int(entry * 2.0):,}원 (+100%)",
                stoploss=f"{int(current * 0.85):,}원 (고점 -15%)",
                strategy=(
                    f"장기 목표 진행 중. +{pnl_rate:.0%} 달성. "
                    f"20~30% 부분 익절 고려. "
                    f"분기마다 투자 논리 점검."
                ),
                urgency="low",
            )
        elif pnl_rate <= -0.10:
            return SellPlan(
                ticker="", name="", horizon="long",
                target=f"{int(entry * 1.30):,}원 (+30%)",
                stoploss=f"{int(entry * 0.85):,}원 (-15%)",
                strategy=(
                    f"장기 손실 {pnl_rate:.1%}. "
                    f"투자 논리(성장성/밸류에이션) 재점검 필수. "
                    f"논리 훼손 시 손절, 건재 시 추가매수 검토."
                ),
                urgency="high" if pnl_rate <= -0.13 else "medium",
            )
        else:
            return SellPlan(
                ticker="", name="", horizon="long",
                target=f"{int(entry * 1.50):,}원 (+50%)",
                stoploss=f"{int(entry * 0.85):,}원 (-15%)",
                strategy=(
                    f"장기 보유 구간. 분기 실적/산업 트렌드 중심 판단. "
                    f"밸류에이션 과열 시 부분 매도."
                ),
                urgency="low",
            )

    def create_plans_for_all(
        self, holdings: list[dict], market_state: str = "NEUTRAL",
    ) -> list[SellPlan]:
        """모든 보유종목에 대한 매도 계획 생성."""
        return [self.create_plan(h, market_state) for h in holdings]


def format_sell_plans(plans: list[SellPlan]) -> str:
    """매도 계획을 텔레그램 메시지로 포맷."""
    if not plans:
        return "보유종목이 없어 매도 계획을 생성할 수 없습니다."

    horizon_labels = {
        "scalp": "\u26a1 단타 (1~3일)",
        "swing": "\U0001f4c8 스윙 (1~2주)",
        "mid": "\U0001f4ca 중기 (1~3개월)",
        "long": "\U0001f3e6 장기 (3개월+)",
    }

    lines = [f"\U0001f4cb {USER_NAME} 매도 계획", "\u2500" * 25, ""]

    # 시계별로 그룹핑
    by_horizon: dict[str, list[SellPlan]] = {}
    for p in plans:
        by_horizon.setdefault(p.horizon, []).append(p)

    for horizon in ["scalp", "swing", "mid", "long"]:
        group = by_horizon.get(horizon)
        if not group:
            continue

        label = horizon_labels.get(horizon, horizon)
        lines.append(f"{label}")

        for p in group:
            urgency_emoji = "\U0001f534" if p.urgency == "high" else "\U0001f7e1" if p.urgency == "medium" else "\U0001f7e2"
            lines.append(f"  {urgency_emoji} {p.name} ({p.pnl_pct:+.1f}%)")
            lines.append(f"    목표: {p.target}")
            lines.append(f"    손절: {p.stoploss}")
            lines.append(f"    전략: {p.strategy}")
            lines.append("")

    # 긴급 종목 하이라이트
    urgent = [p for p in plans if p.urgency == "high"]
    if urgent:
        lines.append("\u2500" * 25)
        lines.append("\u26a0\ufe0f 긴급 주의 종목:")
        for p in urgent:
            lines.append(f"  \U0001f534 {p.name}: {p.strategy[:50]}...")

    return "\n".join(lines)
