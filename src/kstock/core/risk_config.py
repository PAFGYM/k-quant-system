"""Centralized risk thresholds loader (v12.3).

config/risk_thresholds.yaml에서 모든 리스크 임계값을 로드한다.
기존 모듈은 점진적으로 여기서 읽도록 마이그레이션.
YAML이 없으면 하드코딩된 기본값 사용 (backward compat).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("config/risk_thresholds.yaml")


# ---------------------------------------------------------------------------
# Frozen sub-dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VixThresholds:
    calm: float = 15.0
    normal_low: float = 18.0
    normal_high: float = 25.0
    fear: float = 30.0
    panic: float = 35.0
    crisis: float = 40.0

    def regime_for(self, vix: float) -> str:
        """VIX 값 → 레짐 이름."""
        if vix >= self.crisis:
            return "crisis"
        if vix >= self.fear:
            return "panic"
        if vix >= self.normal_high:
            return "fear"
        if vix >= self.normal_low:
            return "normal"
        return "calm"

    def status_label(self, vix: float) -> str:
        """VIX 값 → 한글 상태 라벨 (디스플레이용)."""
        if vix >= self.fear:
            return "공포"
        if vix >= self.normal_high:
            return "경계"
        if vix >= self.normal_low:
            return "주의"
        return "안정"


@dataclass(frozen=True)
class UsdkrwThresholds:
    favorable: float = 1200.0
    normal_low: float = 1250.0
    normal_high: float = 1300.0
    warning: float = 1350.0
    danger: float = 1400.0
    crisis: float = 1450.0


@dataclass(frozen=True)
class ShockThresholds:
    """쇼크 카테고리 임계값 (watch/alert/shock %)."""
    watch_pct: float = 0.0
    alert_pct: float = 0.0
    shock_pct: float = 0.0


@dataclass(frozen=True)
class PositionLimits:
    max_single_weight: float = 0.30
    max_sector_weight: float = 0.50
    max_kelly_fraction: float = 0.25
    min_cash_pct: float = 0.05


# ---------------------------------------------------------------------------
# Top-level aggregate
# ---------------------------------------------------------------------------

@dataclass
class RiskThresholds:
    """모든 리스크 임계값 통합."""
    vix: VixThresholds = field(default_factory=VixThresholds)
    usdkrw: UsdkrwThresholds = field(default_factory=UsdkrwThresholds)
    oil: ShockThresholds = field(
        default_factory=lambda: ShockThresholds(2.0, 3.0, 5.0))
    us_futures: ShockThresholds = field(
        default_factory=lambda: ShockThresholds(1.0, 1.5, 2.5))
    vix_change: ShockThresholds = field(
        default_factory=lambda: ShockThresholds(15.0, 25.0, 40.0))
    dollar: ShockThresholds = field(
        default_factory=lambda: ShockThresholds(0.5, 1.0, 1.5))
    korea_etf: ShockThresholds = field(
        default_factory=lambda: ShockThresholds(1.5, 2.5, 4.0))
    usdkrw_change: ShockThresholds = field(
        default_factory=lambda: ShockThresholds(0.5, 1.0, 1.5))
    position_limits: PositionLimits = field(default_factory=PositionLimits)
    adaptive_intervals: Dict[str, Dict[str, int]] = field(
        default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        logger.warning("risk_thresholds.yaml not found at %s, using defaults", path)
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_risk_thresholds(path: Path | None = None) -> RiskThresholds:
    """YAML에서 리스크 임계값 로드 (없으면 기본값)."""
    raw = _load_yaml(path or _DEFAULT_PATH)
    rt = RiskThresholds()

    if "vix" in raw:
        rt.vix = VixThresholds(**{k: float(v) for k, v in raw["vix"].items()})
    if "usdkrw" in raw:
        rt.usdkrw = UsdkrwThresholds(
            **{k: float(v) for k, v in raw["usdkrw"].items()})

    for key in ("oil", "us_futures", "vix_change", "dollar",
                "korea_etf", "usdkrw_change"):
        if key in raw:
            setattr(rt, key, ShockThresholds(
                **{k: float(v) for k, v in raw[key].items()}))

    if "position_limits" in raw:
        rt.position_limits = PositionLimits(
            **{k: float(v) for k, v in raw["position_limits"].items()})

    rt.adaptive_intervals = raw.get("adaptive_intervals", {})

    return rt


# ---------------------------------------------------------------------------
# Global singleton (lazy)
# ---------------------------------------------------------------------------

_thresholds: RiskThresholds | None = None


def get_risk_thresholds() -> RiskThresholds:
    """전역 리스크 임계값 싱글턴."""
    global _thresholds
    if _thresholds is None:
        _thresholds = load_risk_thresholds()
    return _thresholds
