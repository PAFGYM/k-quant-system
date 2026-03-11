"""통합 리스크 정책 — v5.1.

시스템 전체의 리스크 임계치를 단일 소스로 관리한다.
모든 리스크 관련 모듈(RiskManager, SafetyLimits, SafetyModeManager)은
이 파일의 정책을 참조해야 한다.

전문가 피드백:
  "SafetyLimits(-3%) vs RiskManager(-5%) 임계치 중복/혼란"
  → 단일 소스(risk_policy)로 통일, 레이어는 정책 집행 위치만 다르게.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── 통합 리스크 정책 ────────────────────────────────────────

@dataclass
class RiskPolicy:
    """시스템 전체 리스크 한도 단일 소스.

    모든 리스크 관련 체크는 이 정책 객체에서 한도를 읽어야 한다.
    레이어별 적용 규칙:
      - PreTradeValidator (Layer 2): order_* 한도 적용
      - RiskManager (Layer 3): portfolio_* 한도 적용
      - SafetyModeManager (Layer 5): safety_* 한도 적용
    """

    # ── 주문 레벨 (PreTradeValidator / SafetyLimits) ──────
    order_max_single_pct: float = 15.0        # 1회 주문 최대 비중 (%)
    order_max_daily_count: int = 10           # 일일 최대 주문 횟수
    order_daily_loss_limit_pct: float = -3.0  # 일일 손실 한도 (%) — 매수 차단

    # ── 포트폴리오 레벨 (RiskManager) ─────────────────────
    portfolio_max_mdd: float = -0.15          # MDD 경고 한도
    portfolio_emergency_mdd: float = -0.20    # MDD 비상 한도
    portfolio_max_daily_loss: float = -0.05   # 일일 손실 비상 한도
    portfolio_max_stock_weight: float = 0.40  # 단일 종목 비중 한도
    portfolio_max_sector_weight: float = 0.60 # 단일 섹터 비중 한도
    portfolio_max_correlation: float = 0.85   # 최대 상관관계
    portfolio_max_margin_ratio: float = 0.20  # 최대 포트폴리오 신용 비율
    portfolio_max_single_margin: float = 0.30 # 단일 종목 신용 비율

    # ── 안전모드 레벨 (SafetyModeManager) ─────────────────
    safety_caution_threshold: int = 1         # CAUTION 진입: 불일치 N건+
    safety_safe_threshold: int = 3            # SAFE 진입: 불일치 N건+
    safety_lockdown_threshold: int = 5        # LOCKDOWN 진입: 불일치 N건+

    # ── 데이터 품질 (v5.1) ────────────────────────────────
    data_max_delay_for_buy_seconds: float = 60.0  # 매수 허용 최대 데이터 지연 (초)

    def to_risk_limits_dict(self) -> dict:
        """기존 RISK_LIMITS 형식으로 변환 (하위호환)."""
        return {
            "max_portfolio_mdd": self.portfolio_max_mdd,
            "emergency_mdd": self.portfolio_emergency_mdd,
            "max_daily_loss": self.portfolio_max_daily_loss,
            "max_single_stock_weight": self.portfolio_max_stock_weight,
            "max_sector_weight": self.portfolio_max_sector_weight,
            "max_correlation": self.portfolio_max_correlation,
            "max_margin_ratio": self.portfolio_max_margin_ratio,
            "max_single_margin": self.portfolio_max_single_margin,
        }

    def to_safety_limits_kwargs(self) -> dict:
        """SafetyLimits 생성자용 kwargs (하위호환)."""
        return {
            "max_order_pct": self.order_max_single_pct,
            "max_daily_orders": self.order_max_daily_count,
            "daily_loss_limit_pct": self.order_daily_loss_limit_pct,
        }

    def format_summary(self) -> str:
        """정책 요약 텔레그램 포맷."""
        return "\n".join([
            "📏 리스크 정책 (단일 소스)",
            "━" * 25,
            "",
            "주문 레벨:",
            f"  1회 주문 한도: {self.order_max_single_pct}%",
            f"  일일 주문 횟수: {self.order_max_daily_count}회",
            f"  일일 손실 한도: {self.order_daily_loss_limit_pct}%",
            "",
            "포트폴리오 레벨:",
            f"  MDD 경고: {self.portfolio_max_mdd * 100:.0f}%",
            f"  MDD 비상: {self.portfolio_emergency_mdd * 100:.0f}%",
            f"  일일 손실: {self.portfolio_max_daily_loss * 100:.0f}%",
            f"  단일 종목: {self.portfolio_max_stock_weight * 100:.0f}%",
            f"  단일 섹터: {self.portfolio_max_sector_weight * 100:.0f}%",
            f"  상관관계: {self.portfolio_max_correlation}",
            f"  신용 비율: {self.portfolio_max_margin_ratio * 100:.0f}%",
            "",
            "안전모드:",
            f"  CAUTION: 불일치 {self.safety_caution_threshold}건+",
            f"  SAFE: 불일치 {self.safety_safe_threshold}건+",
            f"  LOCKDOWN: 불일치 {self.safety_lockdown_threshold}건+",
            "",
            "데이터 품질:",
            f"  매수 허용 지연: {self.data_max_delay_for_buy_seconds}초",
        ])


# ── 글로벌 인스턴스 ──────────────────────────────────────────

_policy: RiskPolicy | None = None


def get_risk_policy() -> RiskPolicy:
    """글로벌 RiskPolicy 반환."""
    global _policy
    if _policy is None:
        _policy = RiskPolicy()
    return _policy


def set_risk_policy(policy: RiskPolicy) -> None:
    """글로벌 RiskPolicy 설정."""
    global _policy
    _policy = policy
    logger.info("리스크 정책 업데이트 완료")


# ── VIX 기반 동적 리스크 정책 ─────────────────────────────────


def _vix_bounds() -> tuple[float, float, float]:
    """v12.3: risk_config에서 VIX 임계값 로드."""
    try:
        from kstock.core.risk_config import get_risk_thresholds
        v = get_risk_thresholds().vix
        return v.fear, v.normal_high, v.normal_low  # 30, 25, 18
    except Exception:
        return 30.0, 25.0, 18.0


def vix_adjusted_policy(
    vix: float,
    base_max_single: float = 0.30,
    base_max_sector: float = 0.50,
    base_new_buy_allowed: bool = True,
) -> dict:
    """VIX 기반 동적 리스크 정책.

    VIX < normal_low: 완화 (+15% 비중 한도 확대)
    VIX normal_low~normal_high: 기본값 유지
    VIX normal_high~fear: 긴축 (비중 한도 -20%, 신규 매수 제한)
    VIX > fear: 극긴축 (신규 매수 차단, 비중 한도 -40%)

    Returns:
        dict with: max_single_weight, max_sector_weight,
        new_buy_allowed, leverage_allowed, cash_floor_pct,
        stop_loss_tighten_pct, regime_label
    """
    vix_fear, vix_high, vix_low = _vix_bounds()

    if vix > vix_fear:
        return {
            "max_single_weight": base_max_single * 0.6,
            "max_sector_weight": base_max_sector * 0.6,
            "new_buy_allowed": False,
            "leverage_allowed": False,
            "cash_floor_pct": 40,
            "stop_loss_tighten_pct": 30,
            "regime_label": "🔴 극긴축",
        }
    elif vix > vix_high:
        return {
            "max_single_weight": base_max_single * 0.8,
            "max_sector_weight": base_max_sector * 0.8,
            "new_buy_allowed": True,
            "leverage_allowed": False,
            "cash_floor_pct": 25,
            "stop_loss_tighten_pct": 15,
            "regime_label": "🟡 긴축",
        }
    elif vix < vix_low:
        return {
            "max_single_weight": min(base_max_single * 1.15, 0.35),
            "max_sector_weight": min(base_max_sector * 1.15, 0.55),
            "new_buy_allowed": True,
            "leverage_allowed": True,
            "cash_floor_pct": 5,
            "stop_loss_tighten_pct": 0,
            "regime_label": "🟢 완화",
        }
    else:
        return {
            "max_single_weight": base_max_single,
            "max_sector_weight": base_max_sector,
            "new_buy_allowed": True,
            "leverage_allowed": True,
            "cash_floor_pct": 15,
            "stop_loss_tighten_pct": 0,
            "regime_label": "⚪ 기본",
        }


# ── 통합 리스크 제약 조건 셋 ─────────────────────────────────

@dataclass
class RiskConstraintSet:
    """모든 모듈이 참조하는 통합 리스크 제약 조건.

    position_sizer, portfolio_optimizer, risk_manager가
    동일한 한도를 사용하도록 단일 소스 제공.
    """
    max_single_weight: float = 0.30
    max_sector_weight: float = 0.50
    min_weight: float = 0.0
    max_kelly_fraction: float = 0.25
    min_kelly_fraction: float = 0.03
    max_leverage: float = 1.0
    max_portfolio_turnover: float = 0.30  # 일일 최대 회전율
    min_cash_pct: float = 0.05

    def for_regime(self, vix: float) -> "RiskConstraintSet":
        """VIX 레짐에 따라 동적 조정된 제약 조건 반환 (v12.3: risk_config)."""
        try:
            vix_fear, vix_high, vix_calm = _vix_bounds()
            if vix > vix_fear:
                return RiskConstraintSet(
                    max_single_weight=self.max_single_weight * 0.6,
                    max_sector_weight=self.max_sector_weight * 0.6,
                    max_kelly_fraction=self.max_kelly_fraction * 0.5,
                    min_cash_pct=0.40,
                    max_leverage=0.0,
                    max_portfolio_turnover=0.10,
                )
            elif vix > vix_high:
                return RiskConstraintSet(
                    max_single_weight=self.max_single_weight * 0.8,
                    max_sector_weight=self.max_sector_weight * 0.8,
                    max_kelly_fraction=self.max_kelly_fraction * 0.7,
                    min_cash_pct=0.25,
                    max_leverage=0.0,
                    max_portfolio_turnover=0.20,
                )
            elif vix < vix_calm:
                return RiskConstraintSet(
                    max_single_weight=min(self.max_single_weight * 1.15, 0.35),
                    max_sector_weight=min(self.max_sector_weight * 1.15, 0.55),
                    max_kelly_fraction=self.max_kelly_fraction,
                    min_cash_pct=0.05,
                    max_leverage=1.0,
                    max_portfolio_turnover=0.30,
                )
            return self  # neutral
        except Exception:
            logger.exception("RiskConstraintSet.for_regime() 오류, 기본값 반환")
            return self

    def for_regime_smoothed(self, vix: float, prev_vix: float | None = None) -> "RiskConstraintSet":
        """VIX-based regime with smooth transitions (no hard cutoffs).

        Uses sigmoid blending between regimes instead of step functions.
        """
        import math

        def _sigmoid(x: float, center: float, width: float = 3.0) -> float:
            """Smooth transition: 0 at x<<center, 1 at x>>center."""
            try:
                return 1.0 / (1.0 + math.exp(-(x - center) / width))
            except OverflowError:
                return 0.0 if x < center else 1.0

        # Blend factors: risk_config 중앙 임계값 (v12.3)
        vix_fear, vix_high, vix_low = _vix_bounds()
        tight_25 = _sigmoid(vix, center=vix_high, width=3)   # starts tightening
        tight_30 = _sigmoid(vix, center=vix_fear, width=2)   # extreme tightening
        relax_15 = 1.0 - _sigmoid(vix, center=vix_low - 3, width=3)  # relaxation

        # If previous VIX available, dampen transitions (mean of current and previous)
        if prev_vix is not None:
            prev_tight_25 = _sigmoid(prev_vix, center=25, width=3)
            tight_25 = 0.7 * tight_25 + 0.3 * prev_tight_25  # 70% current, 30% previous
            prev_tight_30 = _sigmoid(prev_vix, center=30, width=2)
            tight_30 = 0.7 * tight_30 + 0.3 * prev_tight_30

        # Interpolate constraints
        base_single = self.max_single_weight
        base_sector = self.max_sector_weight
        base_cash = self.min_cash_pct

        # Tighten: reduce max weights, increase cash
        adj_single = base_single * (1.0 - 0.4 * tight_25 - 0.2 * tight_30)
        adj_sector = base_sector * (1.0 - 0.3 * tight_25 - 0.2 * tight_30)
        adj_cash = base_cash + 0.15 * tight_25 + 0.20 * tight_30

        # Relax: slightly increase max weights in calm markets
        adj_single = adj_single * (1.0 + 0.15 * relax_15)
        adj_sector = adj_sector * (1.0 + 0.10 * relax_15)

        return RiskConstraintSet(
            max_single_weight=round(max(0.05, min(0.40, adj_single)), 4),
            max_sector_weight=round(max(0.10, min(0.60, adj_sector)), 4),
            min_weight=self.min_weight,
            max_kelly_fraction=round(self.max_kelly_fraction * (1.0 - 0.3 * tight_30), 4),
            min_kelly_fraction=self.min_kelly_fraction,
            max_leverage=round(max(0.5, self.max_leverage * (1.0 - 0.3 * tight_25)), 2),
            max_portfolio_turnover=self.max_portfolio_turnover,
            min_cash_pct=round(min(0.50, adj_cash), 4),
        )


_constraints: RiskConstraintSet | None = None


def get_risk_constraints() -> RiskConstraintSet:
    """글로벌 RiskConstraintSet 반환."""
    global _constraints
    if _constraints is None:
        _constraints = RiskConstraintSet()
    return _constraints


# ── 전시(Wartime) 리스크 조정 ─────────────────────────────────

# 방어 섹터: 전시에 선호
DEFENSIVE_SECTORS: set[str] = {"의료", "필수소비재", "유틸리티", "바이오", "통신"}

# 경기민감(공격) 섹터: 전시에 축소/회피
CYCLICAL_SECTORS: set[str] = {
    "반도체", "2차전지", "자동차", "철강", "화학", "조선", "엔터", "소프트웨어",
}


@dataclass
class WartimeAdjustments:
    """전시 모드 리스크 조정값."""

    stop_loss_pct: float = -0.05            # 손절: -7% → -5%
    max_position_ratio: float = 0.50        # 평시 대비 50%로 축소
    max_portfolio_exposure: float = 0.60    # 총 노출 60% (나머지 현금)
    min_buy_confidence: float = 0.80        # BUY 시그널 최소 신뢰도
    defensive_sectors: tuple[str, ...] = (
        "의료", "필수소비재", "유틸리티", "바이오", "통신",
    )
    cyclical_sectors: tuple[str, ...] = (
        "반도체", "2차전지", "자동차", "철강", "화학", "조선", "엔터", "소프트웨어",
    )

    def format_summary(self) -> str:
        """전시 조정 요약 텍스트."""
        return "\n".join([
            "🔴 전시 리스크 조정",
            "━" * 22,
            f"  손절 기준: {self.stop_loss_pct * 100:.0f}% (평시 -7%)",
            f"  포지션 축소: 평시 대비 {self.max_position_ratio * 100:.0f}%",
            f"  최대 포트폴리오 노출: {self.max_portfolio_exposure * 100:.0f}%",
            f"  매수 최소 신뢰도: {self.min_buy_confidence * 100:.0f}%",
            f"  방어 섹터: {', '.join(self.defensive_sectors)}",
            f"  축소 대상 섹터: {', '.join(self.cyclical_sectors)}",
        ])


def wartime_adjustments() -> WartimeAdjustments:
    """전시(wartime) 모드 활성화 시 적용할 리스크 제약 반환.

    변경 사항:
      - 손절 강화: -7% → -5%
      - 최대 포지션 50% 축소
      - 총 포트폴리오 노출 60%
      - BUY 시그널 신뢰도 0.8 이상만 허용
      - 방어 섹터 선호 (의료, 필수소비재, 유틸리티)
    """
    return WartimeAdjustments()


def wartime_constraint_set() -> RiskConstraintSet:
    """전시 모드용 RiskConstraintSet 반환.

    평시 기본 제약에 전시 조정을 적용한 제약 조건 셋.
    """
    base = get_risk_constraints()
    adj = wartime_adjustments()
    return RiskConstraintSet(
        max_single_weight=base.max_single_weight * adj.max_position_ratio,
        max_sector_weight=base.max_sector_weight * adj.max_position_ratio,
        min_weight=base.min_weight,
        max_kelly_fraction=base.max_kelly_fraction * adj.max_position_ratio,
        min_kelly_fraction=base.min_kelly_fraction,
        max_leverage=0.0,  # 전시: 레버리지 금지
        max_portfolio_turnover=0.10,  # 전시: 회전율 최소화
        min_cash_pct=1.0 - adj.max_portfolio_exposure,  # 40% 현금
    )


def is_sector_defensive(sector: str) -> bool:
    """해당 섹터가 방어 섹터인지 확인."""
    return sector in DEFENSIVE_SECTORS


def is_sector_cyclical(sector: str) -> bool:
    """해당 섹터가 경기민감(공격) 섹터인지 확인."""
    return sector in CYCLICAL_SECTORS


def wartime_check_buy_signal(confidence: float) -> tuple[bool, str]:
    """전시 모드에서 BUY 시그널 허용 여부 판단.

    Args:
        confidence: 시그널 신뢰도 (0~1, score/100 기준)

    Returns:
        (허용 여부, 사유)
    """
    adj = wartime_adjustments()
    if confidence >= adj.min_buy_confidence:
        return True, f"전시 모드 매수 허용 (신뢰도 {confidence:.0%} >= {adj.min_buy_confidence:.0%})"
    return False, (
        f"전시 모드 매수 차단: 신뢰도 {confidence:.0%} < "
        f"최소 요구 {adj.min_buy_confidence:.0%}"
    )


def wartime_check_buy_signal_rd(confidence: float):
    """전시 모드 BUY 시그널 판단 → RiskDecision 반환 (v12.4).

    기존 wartime_check_buy_signal() 래핑.
    호출자가 준비되면 이 버전으로 전환.
    """
    from kstock.core.domain_types import RiskDecision
    ok, msg = wartime_check_buy_signal(confidence)
    return RiskDecision(
        allowed=ok,
        reason=msg,
        source="wartime",
        risk_level="normal" if ok else "blocked",
        block_new_buy=not ok,
    )
