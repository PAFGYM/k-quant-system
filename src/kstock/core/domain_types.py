"""Shared domain types for inter-module communication (v12.3).

scheduler → trading → commands → AI context → display 사이를
흐르는 가장 빈번한 dict 패턴을 타입화한 공유 데이터 클래스.

설계 원칙:
1. 모든 필드에 기본값 (기존 dict 부분 데이터 호환)
2. to_dict() — 기존 dict 소비자 backward compat
3. from_dict() / from_legacy_dict() — 점진적 마이그레이션 헬퍼
4. frozen=False — scheduler가 단계별로 필드를 채움
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TradeSignal: 표준화된 시그널 출력
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    """표준화된 매매 시그널.

    기존 dict 패턴:
        {"ticker": "005930", "signal": "BUY", "score": 72, ...}
    """
    ticker: str = ""
    name: str = ""
    market: str = "KOSPI"
    signal: str = "HOLD"        # BUY / WATCH / HOLD / SELL
    score: float = 0.0          # 0-100 종합 점수
    confidence: float = 0.0     # 0.0-1.0 모델 신뢰도

    # 소스별 점수 분해
    source: str = ""
    macro_score: float = 0.0
    technical_score: float = 0.0
    fundamental_score: float = 0.0
    flow_score: float = 0.0
    risk_score: float = 0.0
    ml_score: float = 0.0

    # 가격 컨텍스트
    current_price: float = 0.0
    target_price: float = 0.0
    stop_loss_price: float = 0.0

    # 메타데이터
    timestamp: Optional[datetime] = None
    reasons: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """기존 dict 소비자 호환."""
        d = asdict(self)
        if d.get("timestamp"):
            d["timestamp"] = d["timestamp"].isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TradeSignal:
        """기존 dict → TradeSignal 변환. 미지 키는 extra로."""
        known = {f.name for f in fields(cls)}
        init_args = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        sig = cls(**init_args)
        sig.extra.update(extra)
        return sig


# ---------------------------------------------------------------------------
# RiskDecision: 표준화된 리스크 판단 결과
# ---------------------------------------------------------------------------

@dataclass
class RiskDecision:
    """리스크 체크 결과 (pre-trade / portfolio / macro).

    v12.3: 기존 tuple (bool, str) 패턴 대체.
    v12.4: 매크로 상태 + 액션 가이드 통합.

    사용법:
        rd = RiskDecision.from_market_state(vix=28, usdkrw=1380)
        if rd.block_new_buy:
            ...  # 매수 차단
        print(rd.reasons)  # ["VIX 경계(28.0)", "환율 주의(1380)"]

    기존 tuple 호환:
        ok, msg = rd.to_tuple()
    """
    allowed: bool = True
    reason: str = ""
    risk_level: str = "normal"  # normal / caution / warning / danger / blocked
    source: str = ""            # 판단 모듈 이름

    # ── 정량적 컨텍스트 ──────────────────────────────────────
    risk_score: float = 0.0     # 0-100
    vix: float = 0.0
    shock_grade: str = "NONE"   # macro_shock 등급

    # ── v12.4: 매크로 상태 ──────────────────────────────────
    regime: str = ""            # VIX 기반 레짐: calm/normal/fear/panic/crisis
    vix_status: str = ""        # 한글 라벨: 안정/주의/경계/공포
    usdkrw: float = 0.0
    usdkrw_status: str = ""     # 강세/안정/주의/경고/위험/위기
    usdkrw_change_pct: float = 0.0   # v12.6: 일간 변화율
    usdkrw_momentum: str = ""        # v12.6: 급등/급락/상승/하락/급변
    usdkrw_composite: float = 0.0    # v12.6: 복합 심각도 (0.0~1.0)

    # ── v12.4: 액션 가이드 ──────────────────────────────────
    block_new_buy: bool = False      # 신규 매수 차단 여부
    reduce_position: bool = False    # 포지션 축소 권고
    max_position_pct: float = 100.0  # 최대 허용 포지션 비중 (%)
    cash_floor_pct: float = 0.0      # 최소 현금 비중 (%)
    stop_loss_override: Optional[float] = None  # 손절 강제 override

    # ── v12.4: 사유/플래그 ──────────────────────────────────
    reasons: List[str] = field(default_factory=list)     # 판단 사유 목록
    source_flags: List[str] = field(default_factory=list) # 원천 플래그 (expiry, NFP 등)
    flags: List[str] = field(default_factory=list)        # 기존 호환 플래그

    def to_dict(self) -> dict:
        return asdict(self)

    def to_tuple(self) -> tuple:
        """기존 (bool, str) 패턴 호환."""
        return self.allowed, self.reason

    def __bool__(self) -> bool:
        """if risk_decision: 패턴 지원."""
        return self.allowed

    @classmethod
    def from_dict(cls, d: dict) -> "RiskDecision":
        """dict → RiskDecision. 미지 키 무시."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_market_state(
        cls,
        vix: float = 0.0,
        usdkrw: float = 0.0,
        usdkrw_change_pct: float = 0.0,
        days_to_expiry: int = 999,
        shock_grade: str = "NONE",
        korea_risk_score: float = 0.0,
        source: str = "market_state",
    ) -> "RiskDecision":
        """현재 매크로 상태에서 RiskDecision 생성.

        risk_config 중앙 임계값 사용.
        """
        reasons: List[str] = []
        source_flags: List[str] = []
        block_buy = False
        reduce_pos = False
        cash_floor = 0.0

        # VIX 상태
        try:
            from kstock.core.risk_config import get_risk_thresholds
            rt = get_risk_thresholds()
            regime = rt.vix.regime_for(vix) if vix > 0 else ""
            vix_label = rt.vix.status_label(vix) if vix > 0 else ""
        except Exception:
            regime = ""
            vix_label = ""

        if vix > 0:
            reasons.append(f"VIX {vix_label}({vix:.1f})")
            if regime in ("panic", "crisis"):
                block_buy = True
                reduce_pos = True
                cash_floor = 40.0
            elif regime == "fear":
                cash_floor = 25.0

        # USDKRW 상태
        usdkrw_label = ""
        if usdkrw > 0:
            try:
                ut = rt.usdkrw
                if usdkrw >= ut.crisis:
                    usdkrw_label = "위기"
                    block_buy = True
                    reduce_pos = True
                elif usdkrw >= ut.danger:
                    usdkrw_label = "위험"
                elif usdkrw >= ut.warning:
                    usdkrw_label = "경고"
                elif usdkrw >= ut.normal_high:
                    usdkrw_label = "주의"
                elif usdkrw >= ut.normal_low:
                    usdkrw_label = "안정"
                else:
                    usdkrw_label = "강세"
            except Exception:
                usdkrw_label = ""
            if usdkrw_label:
                reasons.append(f"환율 {usdkrw_label}({usdkrw:,.0f})")

        # v12.6: 환율 모멘텀 (일간 변화율 기반)
        usdkrw_momentum = ""
        if abs(usdkrw_change_pct) >= 1.5:
            usdkrw_momentum = "급변"
        elif usdkrw_change_pct >= 1.0:
            usdkrw_momentum = "급등"
        elif usdkrw_change_pct <= -1.0:
            usdkrw_momentum = "급락"
        elif usdkrw_change_pct >= 0.5:
            usdkrw_momentum = "상승"
        elif usdkrw_change_pct <= -0.5:
            usdkrw_momentum = "하락"

        if usdkrw_momentum in ("급등", "급변") and usdkrw > 0:
            reasons.append(f"환율 {usdkrw_momentum}({usdkrw_change_pct:+.1f}%)")

        # v12.6: 복합 심각도 — level(60%) + momentum(40%)
        _level_scores = {"강세": 0, "안정": 0.1, "주의": 0.3,
                         "경고": 0.5, "위험": 0.7, "위기": 1.0}
        _lv = _level_scores.get(usdkrw_label, 0)
        _mo = min(1.0, max(0, usdkrw_change_pct) / 2.0)
        usdkrw_composite = min(1.0, _lv * 0.6 + _mo * 0.4)

        # 복합 에스컬레이션: danger + 급등 → 매수 차단
        if usdkrw_composite >= 0.8 and not block_buy and usdkrw > 0:
            block_buy = True
            reduce_pos = True
            reasons.append(f"환율 복합 위험({usdkrw:,.0f} {usdkrw_momentum})")

        # v12.6: VIX × USDKRW 교차 — 외인 이탈 패턴
        if vix >= 25 and usdkrw_change_pct >= 0.5:
            source_flags.append("foreign_outflow_pattern")
            cash_floor = max(cash_floor, 20.0)
            reasons.append("외인 이탈 패턴(VIX↑+원화↓)")

        # 만기일
        if days_to_expiry <= 1:
            source_flags.append("expiry_day")
            reasons.append("선물옵션 만기일")
        elif days_to_expiry <= 3:
            source_flags.append("expiry_near")

        # 쇼크 등급
        if shock_grade not in ("NONE", ""):
            source_flags.append(f"shock_{shock_grade}")
            if shock_grade == "SHOCK":
                block_buy = True
                reasons.append(f"매크로 쇼크({shock_grade})")

        # risk_level 결정
        if block_buy:
            risk_level = "blocked"
        elif reduce_pos:
            risk_level = "danger"
        elif cash_floor > 0:
            risk_level = "warning"
        elif korea_risk_score >= 50:
            risk_level = "caution"
        else:
            risk_level = "normal"

        # risk_score 합산
        score = korea_risk_score
        if vix >= 30:
            score = max(score, 60)
        elif vix >= 25:
            score = max(score, 40)

        return cls(
            allowed=not block_buy,
            reason=" / ".join(reasons) if reasons else "",
            risk_level=risk_level,
            source=source,
            risk_score=score,
            vix=vix,
            shock_grade=shock_grade,
            regime=regime,
            vix_status=vix_label,
            usdkrw=usdkrw,
            usdkrw_status=usdkrw_label,
            usdkrw_change_pct=usdkrw_change_pct,
            usdkrw_momentum=usdkrw_momentum,
            usdkrw_composite=usdkrw_composite,
            block_new_buy=block_buy,
            reduce_position=reduce_pos,
            max_position_pct=60.0 if reduce_pos else (80.0 if cash_floor > 0 else 100.0),
            cash_floor_pct=cash_floor,
            reasons=reasons,
            source_flags=source_flags,
        )


# ---------------------------------------------------------------------------
# PortfolioSnapshot + HoldingItem
# ---------------------------------------------------------------------------

@dataclass
class HoldingItem:
    """포트폴리오 내 단일 보유 종목."""
    ticker: str = ""
    name: str = ""
    quantity: int = 0
    avg_price: float = 0.0
    current_price: float = 0.0
    profit_pct: float = 0.0
    eval_amount: float = 0.0
    weight_pct: float = 0.0
    sector: str = ""
    holding_type: str = ""      # scalp / swing / position / long_term

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> HoldingItem:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class PortfolioSnapshot:
    """표준화된 포트폴리오 상태.

    DataRouter.get_portfolio() 반환 dict를
    commands, AI context, display에서 사용.
    """
    holdings: List[HoldingItem] = field(default_factory=list)
    total_eval: float = 0.0
    total_profit: float = 0.0
    total_profit_pct: float = 0.0
    cash: float = 0.0
    cash_pct: float = 0.0

    # 소스 메타데이터
    source: str = ""               # kis_realtime / database / yfinance
    is_realtime: bool = False
    fetched_at: Optional[datetime] = None

    # 리스크 요약
    max_single_weight: float = 0.0
    sector_concentration: str = ""

    @property
    def holding_count(self) -> int:
        return len(self.holdings)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("fetched_at"):
            d["fetched_at"] = d["fetched_at"].isoformat()
        return d

    @classmethod
    def from_legacy_dict(cls, d: dict) -> PortfolioSnapshot:
        """DataRouter.get_portfolio() 기존 dict 변환."""
        holdings = []
        for h in d.get("holdings", []):
            holdings.append(HoldingItem(
                ticker=h.get("ticker", ""),
                name=h.get("name", ""),
                quantity=h.get("quantity", 0),
                avg_price=h.get("avg_price", 0),
                current_price=h.get("current_price", 0),
                profit_pct=h.get("profit_pct", 0),
                eval_amount=h.get("eval_amount", 0),
            ))
        return cls(
            holdings=holdings,
            total_eval=d.get("total_eval", 0),
            total_profit=d.get("total_profit", 0),
            cash=d.get("cash", 0),
            source=d.get("source", d.get("_pit_source", "")),
            is_realtime=d.get("_pit_source", "") == "kis_realtime",
        )
