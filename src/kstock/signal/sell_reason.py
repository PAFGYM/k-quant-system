"""Foreign sell reason classifier (A/B/C/D types)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SellReasonInput:
    """Input data for sell reason classification."""

    # Macro (Type A)
    spx_change_pct: float = 0.0
    vix_change_pct: float = 0.0
    usdkrw_change_pct: float = 0.0
    broad_sell_pct: float = 0.0  # % of stocks with foreign net sell

    # Flow/Mechanical (Type B)
    program_ratio_pct: float = 0.0  # program trading ratio
    sector_corr: float = 0.0  # correlation with sector
    basis_pct: float = 0.0  # futures basis

    # Idiosyncratic (Type C)
    stock_only_drop: bool = False  # stock drops while market flat
    consensus_change_pct: float = 0.0  # consensus target change
    dart_event: bool = False  # DART disclosure event

    # Technical (Type D)
    near_high_pct: float = 0.0  # % of 52-week high
    volume_ratio: float = 0.0  # volume vs average
    disparity_pct: float = 100.0  # disparity index


@dataclass
class SellReason:
    """Classified sell reason."""

    code: str  # A, B, C, D
    label: str
    confidence: float  # 0.0 ~ 1.0
    rationale: str
    sub_signals: list[str] = field(default_factory=list)


def load_thresholds(config_path: Path | None = None) -> dict:
    """Load sell reason thresholds from scoring.yaml."""
    if config_path is None:
        config_path = Path("config/scoring.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    return config.get("sell_reason", {})


def classify_sell_reason(
    data: SellReasonInput,
    thresholds: dict | None = None,
) -> SellReason:
    """Classify foreign sell reason into A/B/C/D types.

    Args:
        data: Input signals for classification.
        thresholds: Threshold config dict. Loaded from YAML if None.

    Returns:
        SellReason with code, confidence, and rationale.

    Classification Rules:
        A: Macro Risk-off - SPX<-1.2% + VIX>+10% + USDKRW>+0.6% + broad_sell>70%
        B: Flow/Mechanical - program_ratio>25% + (sector_corr>0.7 or basis<-0.3%)
        C: Idiosyncratic - stock_only_drop + (consensus<-5% or dart_event)
        D: Technical - near_high>95% + volume>2.5x + disparity>110%
    """
    if thresholds is None:
        thresholds = load_thresholds()

    candidates: list[SellReason] = []

    # Type A: Macro Risk-off
    a_result = _check_type_a(data, thresholds.get("macro_risk_off", {}))
    if a_result:
        candidates.append(a_result)

    # Type B: Flow/Mechanical
    b_result = _check_type_b(data, thresholds.get("flow_mechanical", {}))
    if b_result:
        candidates.append(b_result)

    # Type C: Idiosyncratic
    c_result = _check_type_c(data, thresholds.get("idiosyncratic", {}))
    if c_result:
        candidates.append(c_result)

    # Type D: Technical
    d_result = _check_type_d(data, thresholds.get("technical_sell", {}))
    if d_result:
        candidates.append(d_result)

    if not candidates:
        return SellReason(
            code="U",
            label="Unclassified",
            confidence=0.0,
            rationale="No clear sell signal pattern identified.",
        )

    # Return highest confidence
    return max(candidates, key=lambda x: x.confidence)


def _check_type_a(data: SellReasonInput, cfg: dict) -> SellReason | None:
    """Check Type A: Macro Risk-off."""
    spx_thresh = cfg.get("spx_drop_pct", -1.2)
    vix_thresh = cfg.get("vix_spike_pct", 10.0)
    krw_thresh = cfg.get("usdkrw_spike_pct", 0.6)
    broad_thresh = cfg.get("broad_sell_pct", 70.0)

    signals: list[str] = []
    score = 0.0

    if data.spx_change_pct <= spx_thresh:
        signals.append(f"SPX {data.spx_change_pct:+.1f}% (< {spx_thresh}%)")
        score += 0.25
    if data.vix_change_pct >= vix_thresh:
        signals.append(f"VIX {data.vix_change_pct:+.1f}% (> +{vix_thresh}%)")
        score += 0.25
    if data.usdkrw_change_pct >= krw_thresh:
        signals.append(f"USDKRW {data.usdkrw_change_pct:+.1f}% (> +{krw_thresh}%)")
        score += 0.25
    if data.broad_sell_pct >= broad_thresh:
        signals.append(f"Broad sell {data.broad_sell_pct:.0f}% (> {broad_thresh}%)")
        score += 0.25

    if score >= 0.5:
        return SellReason(
            code="A",
            label="Macro Risk-off",
            confidence=round(score, 2),
            rationale="Global macro risk-off triggered foreign selling.",
            sub_signals=signals,
        )
    return None


def _check_type_b(data: SellReasonInput, cfg: dict) -> SellReason | None:
    """Check Type B: Flow/Mechanical."""
    program_thresh = cfg.get("program_ratio_pct", 25.0)
    sector_thresh = cfg.get("sector_corr", 0.7)
    basis_thresh = cfg.get("basis_pct", -0.3)

    signals: list[str] = []
    score = 0.0

    if data.program_ratio_pct >= program_thresh:
        signals.append(
            f"Program ratio {data.program_ratio_pct:.1f}% (> {program_thresh}%)"
        )
        score += 0.4

    if data.sector_corr >= sector_thresh:
        signals.append(f"Sector corr {data.sector_corr:.2f} (> {sector_thresh})")
        score += 0.3
    elif data.basis_pct <= basis_thresh:
        signals.append(f"Basis {data.basis_pct:.2f}% (< {basis_thresh}%)")
        score += 0.3

    if score >= 0.4:
        return SellReason(
            code="B",
            label="Flow/Mechanical",
            confidence=round(min(score, 1.0), 2),
            rationale="Program/mechanical trading driving foreign sells.",
            sub_signals=signals,
        )
    return None


def _check_type_c(data: SellReasonInput, cfg: dict) -> SellReason | None:
    """Check Type C: Idiosyncratic."""
    consensus_thresh = cfg.get("consensus_drop_pct", -5.0)

    signals: list[str] = []
    score = 0.0

    if data.stock_only_drop:
        signals.append("Stock-specific drop (market flat)")
        score += 0.4

    if data.consensus_change_pct <= consensus_thresh:
        signals.append(
            f"Consensus {data.consensus_change_pct:+.1f}% (< {consensus_thresh}%)"
        )
        score += 0.3

    if data.dart_event:
        signals.append("DART disclosure event detected")
        score += 0.3

    if score >= 0.4:
        return SellReason(
            code="C",
            label="Idiosyncratic",
            confidence=round(min(score, 1.0), 2),
            rationale="Stock-specific factors driving foreign sells.",
            sub_signals=signals,
        )
    return None


def _check_type_d(data: SellReasonInput, cfg: dict) -> SellReason | None:
    """Check Type D: Technical."""
    high_thresh = cfg.get("near_high_pct", 95.0)
    vol_thresh = cfg.get("volume_multiplier", 2.5)
    disp_thresh = cfg.get("disparity_pct", 110.0)

    signals: list[str] = []
    score = 0.0

    if data.near_high_pct >= high_thresh:
        signals.append(f"Near high {data.near_high_pct:.1f}% (> {high_thresh}%)")
        score += 0.33
    if data.volume_ratio >= vol_thresh:
        signals.append(f"Volume {data.volume_ratio:.1f}x (> {vol_thresh}x)")
        score += 0.33
    if data.disparity_pct >= disp_thresh:
        signals.append(f"Disparity {data.disparity_pct:.1f}% (> {disp_thresh}%)")
        score += 0.34

    if score >= 0.33:
        return SellReason(
            code="D",
            label="Technical",
            confidence=round(min(score, 1.0), 2),
            rationale="Technical overextension triggering foreign profit-taking.",
            sub_signals=signals,
        )
    return None
