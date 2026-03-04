"""포지션 사이징 엔진 — Half-Kelly + ATR 변동성 조정.

Phase 1: 상위 1% 투자자 시스템
- Half-Kelly Criterion 기반 최적 투자 비율 산출
- ATR 기반 변동성 조정 (고변동성 종목 → 비중 축소)
- 포트폴리오 집중도 제한 (종목 30%, 섹터 50%)
- 단계별 차익실현 자동 알림 (Trailing Stop)

사용법:
    sizer = PositionSizer(account_value=200_000_000)
    result = sizer.calculate(
        ticker="005930", current_price=75000,
        atr_pct=1.8, win_rate=0.65,
        target_pct=0.10, stop_pct=-0.05,
        existing_weight=0.15,
    )
    print(result.shares, result.amount, result.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# ── 기본 설정 ──────────────────────────────────────────────
DEFAULT_LIMITS = {
    "max_single_weight": 0.30,     # 종목당 최대 30%
    "max_sector_weight": 0.50,     # 섹터당 최대 50%
    "min_kelly_fraction": 0.03,    # 최소 3% 배분
    "max_kelly_fraction": 0.25,    # 최대 25% 배분 (Half-Kelly cap)
    "min_shares": 1,               # 최소 1주
    "atr_scale_factor": 2.0,       # ATR 스케일링: 기본 ATR(1.5%) 대비 비율
    "base_atr_pct": 1.5,           # 기준 ATR%
}

# ── 차익실현 단계 (수익률 → 행동) ──────────────────────────
PROFIT_STAGES = [
    {
        "threshold": 0.50,       # +50%
        "sell_pct": 0.33,        # 1/3 매도
        "label": "1차 익절",
        "emoji": "🟡",
        "message": "수익 +50% 달성! 1/3 매도로 수익 확보 권장.",
    },
    {
        "threshold": 1.00,       # +100%
        "sell_pct": 0.50,        # 남은 것의 50% (원금 회수)
        "label": "원금 회수",
        "emoji": "🟠",
        "message": "수익 +100% 달성! 원금 회수 매도 후 나머지 무위험 보유.",
    },
]

# ── 트레일링 스탑 설정 ────────────────────────────────────
TRAILING_STOP_CONFIG = {
    "scalp":    {"trail_pct": 0.03, "activate_at": 0.03},   # 3% 트레일링, +3%부터
    "swing":    {"trail_pct": 0.05, "activate_at": 0.08},   # 5% 트레일링, +8%부터
    "mid":      {"trail_pct": 0.10, "activate_at": 0.15},   # 10% 트레일링, +15%부터
    "long":     {"trail_pct": 0.15, "activate_at": 0.30},   # 15% 트레일링, +30%부터
    "position": {"trail_pct": 0.10, "activate_at": 0.15},
    "long_term": {"trail_pct": 0.15, "activate_at": 0.30},
    "auto":     {"trail_pct": 0.08, "activate_at": 0.10},
}


# ── Dataclasses ────────────────────────────────────────────
@dataclass
class PositionSize:
    """포지션 사이징 결과."""
    ticker: str
    name: str = ""
    shares: int = 0                # 추천 매수 수량
    amount: float = 0.0            # 추천 매수 금액
    weight_pct: float = 0.0        # 포트폴리오 비중 (%)
    kelly_fraction: float = 0.0    # Half-Kelly 비율
    atr_adjusted: float = 0.0      # ATR 조정 후 비율
    volatility_grade: str = ""     # A(안정)/B(보통)/C(공격)
    expected_return: float = 0.0   # 기대 수익률
    stop_price: float = 0.0        # 손절가
    target_price: float = 0.0      # 목표가
    reason: str = ""               # 설명


@dataclass
class ProfitAlert:
    """차익실현 알림."""
    ticker: str
    name: str
    alert_type: str       # "stage_1", "stage_2", "trailing_stop", "stop_loss"
    pnl_pct: float        # 현재 수익률
    buy_price: float
    current_price: float
    action: str           # "1/3 매도 권장", "원금 회수 매도", "트레일링 스탑 발동"
    sell_shares: int = 0  # 매도 추천 수량
    sell_pct: float = 0.0 # 매도 비율
    message: str = ""     # 텔레그램 메시지
    urgency: str = "medium"


@dataclass
class TrailingStopState:
    """트레일링 스탑 상태 추적."""
    ticker: str
    high_price: float = 0.0     # 매수 이후 최고가
    trail_pct: float = 0.15     # 트레일링 비율
    is_active: bool = False     # 활성화 여부
    activated_at: float = 0.0   # 활성화 시점 가격
    stop_price: float = 0.0     # 현재 트레일링 스탑 가격
    stages_triggered: list = field(default_factory=list)


# ── Position Sizer ─────────────────────────────────────────
class PositionSizer:
    """포지션 사이징 + 차익실현 자동화 엔진.

    주호님의 포트폴리오에 최적화:
    - 계좌 규모 2억+ 기준
    - 에코프로 등 고변동성 종목 비중 자동 제어
    - 섹터 집중(2차전지 80%) 경고

    사용법:
        sizer = PositionSizer(account_value=200_000_000)
        result = sizer.calculate(
            ticker="005930", current_price=75000,
            atr_pct=1.8, win_rate=0.65,
            target_pct=0.10, stop_pct=-0.05,
        )
    """

    def __init__(
        self,
        account_value: float = 200_000_000,
        limits: dict | None = None,
        alert_mode: str = "normal",
    ) -> None:
        self.account_value = account_value
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._alert_mode = alert_mode

        # 트레일링 스탑 상태 추적 (ticker → TrailingStopState)
        self._trailing_states: dict[str, TrailingStopState] = {}

        # DB에서 저장된 트레일링 스탑 상태 복원
        self._load_trailing_stops_from_db()

    def _load_trailing_stops_from_db(self) -> None:
        """재시작 시 DB에서 트레일링 스탑 상태를 복원한다."""
        try:
            from kstock.core.persistence import load_trailing_stops
            saved = load_trailing_stops()
            for ticker, data in saved.items():
                state = TrailingStopState(
                    ticker=ticker,
                    high_price=data["peak_price"],
                    trail_pct=data.get("stop_pct", 0.07),
                )
                if data.get("entry_price", 0) > 0:
                    # 활성화 여부 판단: entry_price가 있으면 이전에 활성화됐을 가능성
                    state.is_active = True
                    state.activated_at = data["entry_price"]
                    state.stop_price = state.high_price * (1 - state.trail_pct)
                self._trailing_states[ticker] = state
            if saved:
                logger.info("Restored %d trailing stop states from DB", len(saved))
        except Exception:
            logger.warning("Failed to load trailing stops from DB, starting fresh")

    # ── 핵심: 포지션 사이즈 계산 ──────────────────────────
    def calculate(
        self,
        ticker: str,
        current_price: float,
        atr_pct: float = 1.5,
        win_rate: float = 0.55,
        target_pct: float = 0.10,
        stop_pct: float = -0.05,
        existing_weight: float = 0.0,
        sector_weight: float = 0.0,
        name: str = "",
    ) -> PositionSize:
        """최적 포지션 사이즈 계산.

        Args:
            ticker: 종목 코드
            current_price: 현재가
            atr_pct: ATR(14) 비율 (%, e.g., 1.8 = 1.8%)
            win_rate: 승률 (0~1)
            target_pct: 목표 수익률 (양수, e.g., 0.10 = +10%)
            stop_pct: 손절 비율 (음수, e.g., -0.05 = -5%)
            existing_weight: 이미 보유 중인 비중 (0~1)
            sector_weight: 해당 섹터 현재 비중 (0~1)
            name: 종목명

        Returns:
            PositionSize with optimal shares, amount, and reasoning.
        """
        try:
            if current_price <= 0 or self.account_value <= 0:
                return PositionSize(
                    ticker=ticker, name=name,
                    reason="가격 또는 계좌 정보 없음",
                )

            # 1. Half-Kelly 계산
            kelly = self._half_kelly(win_rate, target_pct, abs(stop_pct))

            # 2. ATR 변동성 조정
            atr_adj = self._atr_adjust(kelly, atr_pct)

            # 3. 집중도 제한 적용 — 통합 제약 조건 참조
            from kstock.core.risk_policy import get_risk_constraints
            constraints = get_risk_constraints()
            max_single = constraints.max_single_weight
            max_sector = constraints.max_sector_weight

            # 3-1. 전시(wartime) 모드: 포지션 50% 축소
            _wartime = self._alert_mode == "wartime"
            if _wartime:
                from kstock.core.risk_policy import wartime_adjustments as _wt_adj
                _wt = _wt_adj()
                max_single *= _wt.max_position_ratio
                max_sector *= _wt.max_position_ratio

            available_weight = min(
                max_single - existing_weight,
                max_sector - sector_weight,
                atr_adj,
            )
            available_weight = max(available_weight, 0)

            # 4. 변동성 등급
            vol_grade = self._volatility_grade(atr_pct)

            # 5. 수량 계산
            invest_amount = self.account_value * available_weight
            shares = int(invest_amount / current_price)
            shares = max(shares, 0)

            # 슬리피지 기본 추정 (대량 주문 시 수량 축소)
            if shares > 0 and current_price > 0:
                order_value = shares * current_price
                if order_value > self.account_value * 0.10:  # 계좌의 10% 이상
                    slippage_adj = 0.95  # 5% 축소
                    shares = max(1, int(shares * slippage_adj))

            actual_amount = shares * current_price

            # 6. 기대 수익률
            expected_return = win_rate * target_pct + (1 - win_rate) * stop_pct

            # 7. 손절/목표가
            stop_price = current_price * (1 + stop_pct)
            target_price = current_price * (1 + target_pct)

            # 8. 추천 사유
            reason = self._build_reason(
                kelly, atr_adj, available_weight, vol_grade,
                existing_weight, sector_weight, shares, current_price,
                expected_return,
            )
            if _wartime:
                reason = f"[전시모드 50%축소] {reason}"

            result = PositionSize(
                ticker=ticker,
                name=name,
                shares=shares,
                amount=actual_amount,
                weight_pct=round(available_weight * 100, 1),
                kelly_fraction=round(kelly, 4),
                atr_adjusted=round(atr_adj, 4),
                volatility_grade=vol_grade,
                expected_return=round(expected_return, 4),
                stop_price=round(stop_price),
                target_price=round(target_price),
                reason=reason,
            )

            logger.info(
                "PositionSize [%s]: %d주 x %s원 = %s원 (Kelly=%.2f%%, ATR조정=%.2f%%, 비중=%.1f%%)",
                ticker, shares, f"{current_price:,.0f}",
                f"{actual_amount:,.0f}", kelly * 100, atr_adj * 100,
                available_weight * 100,
            )
            return result

        except Exception:
            logger.exception("Position sizing error for %s", ticker)
            return PositionSize(
                ticker=ticker, name=name,
                reason="계산 중 오류 발생",
            )

    # ── Dynamic Kelly ─────────────────────────────────────
    def calculate_dynamic_kelly(
        self,
        ticker: str,
        current_price: float,
        atr_pct: float = 1.5,
        trade_history: list[dict] | None = None,
        target_pct: float = 0.10,
        stop_pct: float = -0.05,
        existing_weight: float = 0.0,
        sector_weight: float = 0.0,
        name: str = "",
        min_trades: int = 10,
    ) -> PositionSize:
        """Dynamic Kelly: 실제 거래 이력에서 승률/손익비 자동 산출.

        Args:
            trade_history: 최근 거래 리스트. 각 dict:
                - pnl_pct: float (수익률, e.g., 0.05 = +5%)
                - is_win: bool (수익 여부)
            min_trades: 최소 거래 수 (미달 시 기본값 사용)

        Returns:
            PositionSize with dynamically computed Kelly fraction.
        """
        # 거래 이력에서 승률/손익비 자동 계산
        if trade_history and len(trade_history) >= min_trades:
            wins = [t for t in trade_history if t.get("is_win", False)]
            losses = [t for t in trade_history if not t.get("is_win", False)]

            win_rate = len(wins) / len(trade_history)

            avg_win = (
                sum(abs(t.get("pnl_pct", 0)) for t in wins) / len(wins)
                if wins else target_pct
            )
            avg_loss = (
                sum(abs(t.get("pnl_pct", 0)) for t in losses) / len(losses)
                if losses else abs(stop_pct)
            )

            # 동적 target/stop: 실제 평균 수익/손실 반영
            dynamic_target = avg_win
            dynamic_stop = -avg_loss

            logger.info(
                "Dynamic Kelly [%s]: win_rate=%.1f%%, avg_win=%.1f%%, avg_loss=%.1f%% (from %d trades)",
                ticker, win_rate * 100, avg_win * 100, avg_loss * 100, len(trade_history),
            )
        else:
            win_rate = 0.55  # 기본값
            dynamic_target = target_pct
            dynamic_stop = stop_pct
            if trade_history:
                logger.info(
                    "Dynamic Kelly [%s]: insufficient trades (%d < %d), using defaults",
                    ticker, len(trade_history), min_trades,
                )

        return self.calculate(
            ticker=ticker,
            current_price=current_price,
            atr_pct=atr_pct,
            win_rate=win_rate,
            target_pct=dynamic_target,
            stop_pct=dynamic_stop,
            existing_weight=existing_weight,
            sector_weight=sector_weight,
            name=name,
        )

    @staticmethod
    def get_trade_stats(trade_history: list[dict]) -> dict:
        """거래 이력 통계 산출.

        Returns:
            dict with: win_rate, avg_win_pct, avg_loss_pct, profit_factor,
            max_consecutive_loss, sharpe_like_ratio
        """
        if not trade_history:
            return {
                "win_rate": 0.55, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
                "profit_factor": 1.0, "max_consecutive_loss": 0,
                "total_trades": 0, "sharpe_like_ratio": 0.0,
            }

        wins = [t for t in trade_history if t.get("is_win", False)]
        losses = [t for t in trade_history if not t.get("is_win", False)]

        win_rate = len(wins) / len(trade_history) if trade_history else 0.55
        avg_win = (
            sum(abs(t.get("pnl_pct", 0)) for t in wins) / len(wins)
            if wins else 0.0
        )
        avg_loss = (
            sum(abs(t.get("pnl_pct", 0)) for t in losses) / len(losses)
            if losses else 0.0
        )

        gross_win = sum(abs(t.get("pnl_pct", 0)) for t in wins)
        gross_loss = sum(abs(t.get("pnl_pct", 0)) for t in losses)
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float('inf')

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for t in trade_history:
            if not t.get("is_win", False):
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0

        # Sharpe-like ratio
        pnls = [t.get("pnl_pct", 0) for t in trade_history]
        mean_pnl = np.mean(pnls) if pnls else 0.0
        std_pnl = np.std(pnls) if len(pnls) > 1 else 1.0
        sharpe = float(mean_pnl / std_pnl) if std_pnl > 0 else 0.0

        return {
            "win_rate": round(win_rate, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "max_consecutive_loss": max_consec,
            "total_trades": len(trade_history),
            "sharpe_like_ratio": round(sharpe, 4),
        }

    # ── 슬리피지 추정 ──────────────────────────────────────
    @staticmethod
    def estimate_slippage(
        order_shares: int,
        current_price: float,
        avg_daily_volume: float = 1_000_000,
        spread_bps: float = 10.0,
    ) -> dict:
        """체결 슬리피지 추정.

        Almgren-Chriss 간소화 모델: 시장충격 + 스프레드 비용.

        Args:
            order_shares: 주문 수량
            current_price: 현재가
            avg_daily_volume: 평균 일 거래량 (주)
            spread_bps: 스프레드 (bp, 기본 10bp = 0.1%)

        Returns:
            dict with: impact_pct, spread_cost_pct, total_slippage_pct,
            adjusted_shares, effective_price
        """
        try:
            if order_shares <= 0 or current_price <= 0 or avg_daily_volume <= 0:
                return {
                    "impact_pct": 0.0, "spread_cost_pct": 0.0,
                    "total_slippage_pct": 0.0, "adjusted_shares": order_shares,
                    "effective_price": current_price,
                }

            # 참여율
            participation = order_shares / avg_daily_volume

            # 시장 충격: eta * sigma * (participation)^0.6
            # 간소화: sigma ≈ 2% (한국 주식 평균 일일 변동)
            sigma = 0.02
            eta = 0.142  # Almgren-Chriss 계수

            temp_impact = eta * sigma * (participation ** 0.6) * 100  # %

            # 스프레드 비용
            spread_cost = spread_bps / 100 / 2  # half-spread (%)

            # 총 슬리피지
            total = temp_impact + spread_cost

            # 조정 가격
            effective_price = current_price * (1 + total / 100)

            # 슬리피지 반영 수량 조정
            if total > 0.5:  # 0.5% 이상이면 수량 축소
                reduction = min(total / 5, 0.3)  # 최대 30% 축소
                adjusted_shares = max(1, int(order_shares * (1 - reduction)))
            else:
                adjusted_shares = order_shares

            return {
                "impact_pct": round(temp_impact, 4),
                "spread_cost_pct": round(spread_cost, 4),
                "total_slippage_pct": round(total, 4),
                "adjusted_shares": adjusted_shares,
                "effective_price": round(effective_price, 0),
                "participation_rate": round(participation, 4),
            }

        except Exception:
            logger.exception("슬리피지 추정 실패")
            return {
                "impact_pct": 0.0, "spread_cost_pct": 0.0,
                "total_slippage_pct": 0.0, "adjusted_shares": order_shares,
                "effective_price": current_price,
            }

    # ── 차익실현 체크 ─────────────────────────────────────
    def check_profit_taking(
        self,
        ticker: str,
        name: str,
        buy_price: float,
        current_price: float,
        quantity: int,
        holding_type: str = "auto",
        sold_pct: float = 0.0,
    ) -> ProfitAlert | None:
        """보유 종목의 차익실현 조건을 체크한다.

        Args:
            ticker: 종목 코드
            name: 종목명
            buy_price: 매수가
            current_price: 현재가
            quantity: 보유 수량
            holding_type: 투자 유형 (scalp/swing/mid/long 등)
            sold_pct: 이미 매도한 비율 (0~1)

        Returns:
            ProfitAlert if action needed, None otherwise.
        """
        try:
            if buy_price <= 0 or current_price <= 0 or quantity <= 0:
                return None

            pnl_pct = (current_price - buy_price) / buy_price

            # 트레일링 스탑 상태 업데이트
            trail_state = self._update_trailing_stop(
                ticker, current_price, buy_price, holding_type,
            )

            # 1. 손절 체크 (매수가 대비)
            stop_configs = {
                "scalp": -0.03, "swing": -0.05, "short": -0.05,
                "mid": -0.08, "position": -0.08,
                "long": -0.15, "long_term": -0.15,
                "auto": -0.05,
            }
            stop_limit = stop_configs.get(holding_type, -0.05)
            if pnl_pct <= stop_limit:
                return ProfitAlert(
                    ticker=ticker, name=name,
                    alert_type="stop_loss",
                    pnl_pct=round(pnl_pct * 100, 1),
                    buy_price=buy_price,
                    current_price=current_price,
                    action="손절 매도",
                    sell_shares=quantity,
                    sell_pct=1.0,
                    urgency="critical",
                    message=(
                        f"🔴 {name} 손절 도달\n"
                        f"   매수가 {buy_price:,.0f}원 → 현재 {current_price:,.0f}원\n"
                        f"   수익률 {pnl_pct*100:+.1f}% (한도 {stop_limit*100:.0f}%)\n"
                        f"   ➡️ 전량 매도 권장"
                    ),
                )

            # 2. 트레일링 스탑 체크
            if trail_state.is_active and current_price <= trail_state.stop_price:
                sell_shares = quantity - int(quantity * sold_pct)
                return ProfitAlert(
                    ticker=ticker, name=name,
                    alert_type="trailing_stop",
                    pnl_pct=round(pnl_pct * 100, 1),
                    buy_price=buy_price,
                    current_price=current_price,
                    action="트레일링 스탑 발동",
                    sell_shares=sell_shares,
                    sell_pct=1.0 - sold_pct,
                    urgency="high",
                    message=(
                        f"📉 {name} 트레일링 스탑!\n"
                        f"   고점 {trail_state.high_price:,.0f}원 → "
                        f"현재 {current_price:,.0f}원\n"
                        f"   고점 대비 -{trail_state.trail_pct*100:.0f}% 하락\n"
                        f"   수익률 {pnl_pct*100:+.1f}%\n"
                        f"   ➡️ 잔여 {sell_shares}주 매도 권장"
                    ),
                )

            # 3. 단계별 차익실현 체크
            for i, stage in enumerate(PROFIT_STAGES):
                stage_key = f"stage_{i+1}"
                if stage_key in trail_state.stages_triggered:
                    continue  # 이미 알림 발송됨

                if pnl_pct >= stage["threshold"]:
                    remaining_qty = quantity - int(quantity * sold_pct)
                    sell_shares = int(remaining_qty * stage["sell_pct"])
                    sell_shares = max(sell_shares, 1)

                    trail_state.stages_triggered.append(stage_key)

                    return ProfitAlert(
                        ticker=ticker, name=name,
                        alert_type=stage_key,
                        pnl_pct=round(pnl_pct * 100, 1),
                        buy_price=buy_price,
                        current_price=current_price,
                        action=stage["label"],
                        sell_shares=sell_shares,
                        sell_pct=stage["sell_pct"],
                        urgency="medium",
                        message=(
                            f"{stage['emoji']} {name} {stage['label']}\n"
                            f"   매수가 {buy_price:,.0f}원 → 현재 {current_price:,.0f}원\n"
                            f"   수익률 +{pnl_pct*100:.1f}%\n"
                            f"   {stage['message']}\n"
                            f"   ➡️ {sell_shares}주 매도 추천 "
                            f"(보유의 {stage['sell_pct']*100:.0f}%)"
                        ),
                    )

            return None

        except Exception:
            logger.exception("Profit taking check error for %s", ticker)
            return None

    # ── 포트폴리오 집중도 분석 ────────────────────────────
    def analyze_concentration(
        self,
        holdings: list[dict],
        sector_map: dict[str, str] | None = None,
    ) -> list[str]:
        """포트폴리오 집중도 위반 사항을 분석한다.

        Args:
            holdings: [{"ticker": "005930", "name": "삼성전자",
                       "eval_amount": 50_000_000}, ...]
            sector_map: ticker → sector name mapping

        Returns:
            위반 메시지 리스트 (빈 리스트면 정상).
        """
        try:
            if not holdings:
                return []

            from kstock.core.risk_manager import SECTOR_MAP

            smap = sector_map or SECTOR_MAP
            total = sum(h.get("eval_amount", 0) for h in holdings)
            if total <= 0:
                return []

            violations: list[str] = []
            max_single = self.limits["max_single_weight"]
            max_sector = self.limits["max_sector_weight"]

            # 종목별 비중
            for h in holdings:
                weight = h.get("eval_amount", 0) / total
                if weight > max_single:
                    name = h.get("name", h.get("ticker", ""))
                    excess = (weight - max_single) * 100
                    violations.append(
                        f"⚠️ {name} 비중 {weight*100:.1f}% "
                        f"(한도 {max_single*100:.0f}%, {excess:.0f}%p 초과)\n"
                        f"   권장: {name} {excess:.0f}%p 비중 축소"
                    )

            # 섹터별 비중
            sector_amounts: dict[str, float] = {}
            for h in holdings:
                ticker = h.get("ticker", "")
                sector = smap.get(ticker, "기타")
                sector_amounts[sector] = (
                    sector_amounts.get(sector, 0) + h.get("eval_amount", 0)
                )

            for sector, amount in sector_amounts.items():
                weight = amount / total
                if weight > max_sector:
                    excess = (weight - max_sector) * 100
                    violations.append(
                        f"⚠️ {sector} 섹터 비중 {weight*100:.1f}% "
                        f"(한도 {max_sector*100:.0f}%, {excess:.0f}%p 초과)\n"
                        f"   권장: {sector} 섹터 내 종목 분산"
                    )

            return violations

        except Exception:
            logger.exception("Concentration analysis error")
            return []

    # ── 텔레그램 포맷 ─────────────────────────────────────
    def format_position_advice(self, result: PositionSize) -> str:
        """포지션 사이징 결과를 텔레그램 메시지로 포맷."""
        if result.shares <= 0:
            return (
                f"📊 {result.name or result.ticker} 포지션 분석\n\n"
                f"⛔ 현재 매수 불가\n"
                f"사유: {result.reason}"
            )

        vol_emoji = {"A": "🟢", "B": "🟡", "C": "🔴"}.get(
            result.volatility_grade, "⚪"
        )

        return (
            f"📊 {result.name or result.ticker} 포지션 사이징\n"
            f"{'━' * 22}\n\n"
            f"📌 추천 매수: {result.shares}주\n"
            f"💰 금액: {result.amount:,.0f}원\n"
            f"📈 포트폴리오 비중: {result.weight_pct:.1f}%\n\n"
            f"{vol_emoji} 변동성: {result.volatility_grade}등급\n"
            f"🎯 목표가: {result.target_price:,.0f}원\n"
            f"🔴 손절가: {result.stop_price:,.0f}원\n"
            f"📊 기대수익률: {result.expected_return*100:+.1f}%\n\n"
            f"💡 {result.reason}"
        )

    def format_profit_alert(self, alert: ProfitAlert) -> str:
        """차익실현 알림을 텔레그램 메시지로 포맷."""
        urgency_header = {
            "critical": "🚨 긴급",
            "high": "⚠️ 주의",
            "medium": "📢 알림",
        }
        header = urgency_header.get(alert.urgency, "📢 알림")

        buttons_hint = ""
        if alert.alert_type != "stop_loss":
            buttons_hint = (
                f"\n\n[✅ 매도 실행] [❌ 무시] [⏰ 나중에]"
            )

        return (
            f"{header} 차익실현 알림\n"
            f"{'━' * 22}\n\n"
            f"{alert.message}"
            f"{buttons_hint}"
        )

    # ── 내부 메서드 ───────────────────────────────────────

    def _half_kelly(
        self, win_rate: float, target_pct: float, stop_pct: float,
    ) -> float:
        """Half-Kelly 비율 계산.

        Kelly Criterion: f* = (p*b - q) / b
        where: p=win_rate, q=1-p, b=target/stop ratio
        Half-Kelly = f* / 2 (보수적)
        """
        if win_rate <= 0 or target_pct <= 0 or stop_pct <= 0:
            return self.limits["min_kelly_fraction"]

        b = target_pct / stop_pct  # win/loss ratio
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b

        # Half-Kelly with bounds
        half_kelly = kelly / 2
        half_kelly = max(
            self.limits["min_kelly_fraction"],
            min(half_kelly, self.limits["max_kelly_fraction"]),
        )
        return round(half_kelly, 4)

    def _atr_adjust(self, kelly: float, atr_pct: float) -> float:
        """ATR 기반 변동성 조정.

        ATR이 기준(1.5%)보다 높으면 비율 축소.
        ATR이 낮으면 비율 유지 (확대는 안 함).

        공식: adjusted = kelly * (base_atr / max(atr, base_atr))
        """
        base_atr = self.limits["base_atr_pct"]
        scale = self.limits["atr_scale_factor"]

        if atr_pct <= 0:
            return kelly

        # ATR이 기준보다 높으면 비례 축소
        if atr_pct > base_atr:
            ratio = base_atr / atr_pct
            adjusted = kelly * (ratio ** (1 / scale))
        else:
            adjusted = kelly  # ATR 낮으면 유지

        return max(
            self.limits["min_kelly_fraction"],
            min(adjusted, self.limits["max_kelly_fraction"]),
        )

    def _volatility_grade(self, atr_pct: float) -> str:
        """ATR 기반 변동성 등급."""
        if atr_pct < 2.0:
            return "A"  # 안정
        elif atr_pct < 4.0:
            return "B"  # 보통
        else:
            return "C"  # 공격

    def _update_trailing_stop(
        self,
        ticker: str,
        current_price: float,
        buy_price: float,
        holding_type: str,
    ) -> TrailingStopState:
        """트레일링 스탑 상태 업데이트.

        고점을 추적하고, 활성화 조건을 확인한다.
        """
        config = TRAILING_STOP_CONFIG.get(
            holding_type,
            TRAILING_STOP_CONFIG["auto"],
        )

        if ticker not in self._trailing_states:
            self._trailing_states[ticker] = TrailingStopState(
                ticker=ticker,
                high_price=current_price,
                trail_pct=config["trail_pct"],
            )

        state = self._trailing_states[ticker]
        state.trail_pct = config["trail_pct"]

        # 고점 갱신
        _peak_changed = False
        if current_price > state.high_price:
            state.high_price = current_price
            state.stop_price = state.high_price * (1 - state.trail_pct)
            _peak_changed = True

        # 활성화 체크
        pnl_pct = (current_price - buy_price) / buy_price if buy_price > 0 else 0
        if pnl_pct >= config["activate_at"] and not state.is_active:
            state.is_active = True
            state.activated_at = current_price
            state.stop_price = state.high_price * (1 - state.trail_pct)
            _peak_changed = True
            logger.info(
                "Trailing stop activated: %s at %s (trail=%.0f%%)",
                ticker, f"{current_price:,.0f}", state.trail_pct * 100,
            )

        # DB 영속화: 고점 갱신 또는 활성화 시 저장
        if _peak_changed:
            try:
                from kstock.core.persistence import save_trailing_stop
                save_trailing_stop(
                    ticker, state.high_price,
                    entry_price=buy_price, stop_pct=state.trail_pct,
                )
            except Exception:
                logger.debug("Failed to persist trailing stop for %s", ticker)

        return state

    def _build_reason(
        self,
        kelly: float,
        atr_adj: float,
        available: float,
        vol_grade: str,
        existing_w: float,
        sector_w: float,
        shares: int,
        price: float,
        exp_return: float,
    ) -> str:
        """추천 사유 문자열 생성."""
        parts: list[str] = []

        if available <= 0:
            if existing_w >= self.limits["max_single_weight"]:
                return "종목 비중 한도(30%) 초과. 추가 매수 불가."
            if sector_w >= self.limits["max_sector_weight"]:
                return "섹터 비중 한도(50%) 초과. 추가 매수 불가."
            return "비중 한도 초과. 추가 매수 불가."

        # Kelly 해석
        if kelly >= 0.15:
            parts.append("Kelly 지수 우수(높은 승률+손익비)")
        elif kelly >= 0.08:
            parts.append("Kelly 지수 양호")
        else:
            parts.append("Kelly 지수 보수적(낮은 승률 또는 손익비)")

        # ATR 조정
        if atr_adj < kelly * 0.8:
            parts.append(f"변동성 조정으로 비중 축소({vol_grade}등급)")

        # 집중도
        if existing_w > 0:
            parts.append(f"기존 보유 {existing_w*100:.0f}% 반영")

        # 기대수익
        if exp_return > 0.03:
            parts.append(f"기대수익률 {exp_return*100:+.1f}% 양호")
        elif exp_return > 0:
            parts.append(f"기대수익률 {exp_return*100:+.1f}% (보통)")
        else:
            parts.append(f"기대수익률 음수 — 신중 접근")

        return ". ".join(parts) + "."

    def reset_trailing_stop(self, ticker: str) -> None:
        """종목 매도 시 트레일링 스탑 상태 초기화."""
        self._trailing_states.pop(ticker, None)
        # DB에서도 삭제
        try:
            from kstock.core.persistence import delete_trailing_stop
            delete_trailing_stop(ticker)
        except Exception:
            logger.debug("Failed to delete trailing stop from DB for %s", ticker)

    def get_trailing_state(self, ticker: str) -> TrailingStopState | None:
        """특정 종목 트레일링 스탑 상태 조회."""
        return self._trailing_states.get(ticker)

    def get_all_trailing_states(self) -> dict[str, TrailingStopState]:
        """전체 트레일링 스탑 상태 조회."""
        return dict(self._trailing_states)


# ── 포맷 헬퍼 ──────────────────────────────────────────────
def format_concentration_warnings(warnings: list[str]) -> str:
    """집중도 경고를 텔레그램 메시지로 포맷."""
    if not warnings:
        return ""

    lines = [
        "🎯 포트폴리오 집중도 경고",
        "━" * 22,
        "",
    ]
    for w in warnings:
        lines.append(w)
        lines.append("")

    lines.append(f"{USER_NAME}, 분산투자로 리스크 관리하세요.")
    return "\n".join(lines)


def format_profit_taking_summary(alerts: list[ProfitAlert]) -> str:
    """차익실현 알림 요약 메시지."""
    if not alerts:
        return ""

    lines = [
        "💰 차익실현 알림",
        "━" * 22,
        "",
    ]

    critical = [a for a in alerts if a.urgency == "critical"]
    others = [a for a in alerts if a.urgency != "critical"]

    if critical:
        lines.append("🚨 긴급 조치 필요:")
        for a in critical:
            lines.append(f"  {a.name}: {a.action} ({a.pnl_pct:+.1f}%)")
        lines.append("")

    if others:
        for a in others:
            emoji = {"high": "⚠️", "medium": "📢"}.get(a.urgency, "📢")
            lines.append(f"{emoji} {a.name}: {a.action} ({a.pnl_pct:+.1f}%)")
        lines.append("")

    lines.append(f"{USER_NAME}, 수익 확보 전략을 검토하세요.")
    return "\n".join(lines)
