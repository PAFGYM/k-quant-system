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

    기존 tuple (bool, str) 패턴 대체.
    """
    allowed: bool = True
    reason: str = ""
    risk_level: str = "normal"  # normal / caution / warning / danger / blocked
    source: str = ""            # 판단 모듈 이름

    # 정량적 컨텍스트
    risk_score: float = 0.0     # 0-100
    vix: float = 0.0
    shock_grade: str = "NONE"   # macro_shock 등급

    # 액션 가이드
    max_position_pct: float = 100.0
    stop_loss_override: Optional[float] = None

    flags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def __bool__(self) -> bool:
        """if risk_decision: 패턴 지원."""
        return self.allowed


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
