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
