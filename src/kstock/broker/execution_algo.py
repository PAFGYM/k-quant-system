"""고급 실행 알고리즘 — VWAP, TWAP, 동적 슬리피지, 분할 주문.

한국 주식 시장에 최적화된 실행 알고리즘 모듈.

핵심 기능:
  1. VWAP 스케줄 — 거래량 프로파일 기반 시간대별 분할
  2. TWAP 스케줄 — 균등 시간 분할
  3. 동적 슬리피지 — 변동성/거래량/시간대/시장 상태 반영
  4. 분할 주문 — VWAP/TWAP/Iceberg/PoV 알고 선택
  5. 실행 품질 평가 — 벤치마크 대비 슬리피지/등급 산정
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── 한국 시장 상수 ─────────────────────────────────────────

# 기본 시간대별 거래량 비중 (과거 KOSPI/KOSDAQ 평균)
DEFAULT_VOLUME_PROFILE: Dict[str, float] = {
    "09:00-09:30": 0.25,   # 개장 초반 (높은 거래량)
    "09:30-11:30": 0.30,   # 오전 중반
    "11:30-14:00": 0.20,   # 점심시간 (저조)
    "14:00-15:30": 0.25,   # 마감 전 (높은 거래량)
}

# 시간대 경계 (분 단위, 09:00 기준 오프셋)
SLICE_BOUNDARIES_MINUTES: List[tuple] = [
    (0, 30),     # 09:00-09:30
    (30, 150),   # 09:30-11:30
    (150, 300),  # 11:30-14:00
    (300, 390),  # 14:00-15:30
]

URGENCY_PARTICIPATION: Dict[str, float] = {
    "low": 0.10,
    "medium": 0.20,
    "high": 0.40,
}


# ── Dataclasses ────────────────────────────────────────────

@dataclass
class VWAPSchedule:
    """VWAP 기반 실행 스케줄."""

    time_slices: List[Dict]
    target_pct_by_slice: List[float]
    estimated_vwap: float
    expected_cost_bps: float


@dataclass
class TWAPSchedule:
    """TWAP 기반 실행 스케줄."""

    n_slices: int
    interval_minutes: int
    qty_per_slice: int
    total_qty: int
    expected_cost_bps: float


@dataclass
class SlippageModel:
    """동적 슬리피지 추정 모델."""

    base_bps: float
    volatility_adj_bps: float
    volume_adj_bps: float
    time_adj_bps: float
    total_bps: float
    regime: str


@dataclass
class SplitOrder:
    """분할 주문 계획."""

    parent_order_id: str
    child_orders: List[Dict]
    algo: str
    total_qty: int
    filled_qty: int
    avg_price: float
    status: str


@dataclass
class ExecutionQuality:
    """실행 품질 평가 결과."""

    algo: str
    benchmark_price: float
    avg_fill_price: float
    slippage_bps: float
    vs_vwap_bps: float
    vs_twap_bps: float
    market_impact_bps: float
    grade: str


# ── 거래량 프로파일 유틸 ───────────────────────────────────

def _extract_volume_profile(ohlcv: pd.DataFrame) -> Dict[str, float]:
    """OHLCV 데이터에서 시간대별 거래량 비중을 추출한다.

    Parameters
    ----------
    ohlcv : pd.DataFrame
        최소 ``volume`` 컬럼과 DatetimeIndex(또는 ``datetime`` 컬럼) 필요.

    Returns
    -------
    dict
        DEFAULT_VOLUME_PROFILE 키와 동일한 시간대별 비중 (합 ≈ 1.0).
        데이터가 부족하면 DEFAULT_VOLUME_PROFILE을 그대로 반환.
    """
    if ohlcv is None or ohlcv.empty or "volume" not in ohlcv.columns:
        return dict(DEFAULT_VOLUME_PROFILE)

    df = ohlcv.copy()

    # DatetimeIndex 또는 datetime 컬럼에서 시간 추출
    if isinstance(df.index, pd.DatetimeIndex):
        minutes = df.index.hour * 60 + df.index.minute
    elif "datetime" in df.columns:
        dt = pd.to_datetime(df["datetime"])
        minutes = dt.dt.hour * 60 + dt.dt.minute
    else:
        return dict(DEFAULT_VOLUME_PROFILE)

    profile: Dict[str, float] = {}
    total_vol = float(df["volume"].sum())
    if total_vol <= 0:
        return dict(DEFAULT_VOLUME_PROFILE)

    keys = list(DEFAULT_VOLUME_PROFILE.keys())
    for idx, (start_m, end_m) in enumerate(SLICE_BOUNDARIES_MINUTES):
        # 09:00 기준 → 실제 분 오프셋 = 540 + start_m
        abs_start = 540 + start_m
        abs_end = 540 + end_m
        mask = (minutes >= abs_start) & (minutes < abs_end)
        slice_vol = float(df.loc[mask, "volume"].sum())
        profile[keys[idx]] = slice_vol / total_vol

    # 프로파일 합이 0이면 (시간 범위 밖 데이터) 기본 프로파일 사용
    profile_sum = sum(profile.values())
    if profile_sum <= 0:
        return dict(DEFAULT_VOLUME_PROFILE)

    # 정규화
    for k in profile:
        profile[k] /= profile_sum

    return profile


def _compute_vwap_from_ohlcv(ohlcv: pd.DataFrame) -> float:
    """OHLCV에서 VWAP를 계산한다 (close * volume 가중 평균)."""
    if ohlcv is None or ohlcv.empty:
        return 0.0

    if "close" not in ohlcv.columns or "volume" not in ohlcv.columns:
        return 0.0

    vol = ohlcv["volume"].values.astype(np.float64)
    close = ohlcv["close"].values.astype(np.float64)
    total_vol = vol.sum()
    if total_vol <= 0:
        return float(close.mean()) if len(close) > 0 else 0.0

    return float(np.sum(close * vol) / total_vol)


def _compute_twap_from_ohlcv(ohlcv: pd.DataFrame) -> float:
    """OHLCV에서 TWAP를 계산한다 (close 단순 평균)."""
    if ohlcv is None or ohlcv.empty:
        return 0.0
    if "close" not in ohlcv.columns:
        return 0.0
    return float(ohlcv["close"].mean())


# ── 1. VWAP 스케줄 ────────────────────────────────────────

def compute_vwap_schedule(
    total_qty: int,
    ohlcv: pd.DataFrame,
    participation_rate: float = 0.1,
) -> VWAPSchedule:
    """과거 거래량 프로파일 기반 VWAP 실행 스케줄을 생성한다.

    Parameters
    ----------
    total_qty : int
        주문 총 수량.
    ohlcv : pd.DataFrame
        과거 OHLCV 데이터 (DatetimeIndex + open/high/low/close/volume).
    participation_rate : float
        목표 참여율 (0~1). 기본 10%.

    Returns
    -------
    VWAPSchedule
    """
    if total_qty <= 0:
        return VWAPSchedule(
            time_slices=[], target_pct_by_slice=[], estimated_vwap=0.0,
            expected_cost_bps=0.0,
        )

    profile = _extract_volume_profile(ohlcv)
    estimated_vwap = _compute_vwap_from_ohlcv(ohlcv)

    keys = list(DEFAULT_VOLUME_PROFILE.keys())
    time_slices: List[Dict] = []
    target_pct: List[float] = []
    remaining = total_qty

    for i, key in enumerate(keys):
        pct = profile.get(key, DEFAULT_VOLUME_PROFILE[key])
        target_pct.append(pct)

        if i < len(keys) - 1:
            qty = int(round(total_qty * pct))
            qty = min(qty, remaining)
        else:
            # 마지막 슬라이스: 잔여 전량
            qty = remaining

        remaining -= qty

        start_m, end_m = SLICE_BOUNDARIES_MINUTES[i]
        time_slices.append({
            "slice_id": i,
            "time_range": key,
            "start_offset_min": start_m,
            "end_offset_min": end_m,
            "target_qty": qty,
            "volume_pct": round(pct, 4),
            "participation_rate": participation_rate,
        })

    # 예상 비용: 참여율이 높으면 시장 충격 증가
    expected_cost_bps = round(participation_rate * 30.0, 2)

    logger.info(
        "VWAP schedule: total=%d, slices=%d, est_vwap=%.2f, cost=%.1fbps",
        total_qty, len(time_slices), estimated_vwap, expected_cost_bps,
    )

    return VWAPSchedule(
        time_slices=time_slices,
        target_pct_by_slice=target_pct,
        estimated_vwap=round(estimated_vwap, 2),
        expected_cost_bps=expected_cost_bps,
    )


# ── 2. TWAP 스케줄 ────────────────────────────────────────

def compute_twap_schedule(
    total_qty: int,
    duration_minutes: int = 120,
    interval_minutes: int = 10,
) -> TWAPSchedule:
    """균등 시간 분할 TWAP 실행 스케줄을 생성한다.

    Parameters
    ----------
    total_qty : int
        주문 총 수량.
    duration_minutes : int
        전체 실행 기간 (분).
    interval_minutes : int
        주문 간격 (분).

    Returns
    -------
    TWAPSchedule
    """
    if total_qty <= 0 or duration_minutes <= 0 or interval_minutes <= 0:
        return TWAPSchedule(
            n_slices=0, interval_minutes=interval_minutes,
            qty_per_slice=0, total_qty=total_qty, expected_cost_bps=0.0,
        )

    n_slices = max(1, duration_minutes // interval_minutes)
    qty_per_slice = total_qty // n_slices

    # 잔여 수량은 마지막 슬라이스에 추가
    remainder = total_qty - qty_per_slice * n_slices

    # 예상 비용: 슬라이스가 적으면(급하면) 비용 증가
    expected_cost_bps = round(max(1.0, 30.0 / n_slices), 2)

    logger.info(
        "TWAP schedule: total=%d, n_slices=%d, qty/slice=%d, remainder=%d",
        total_qty, n_slices, qty_per_slice, remainder,
    )

    return TWAPSchedule(
        n_slices=n_slices,
        interval_minutes=interval_minutes,
        qty_per_slice=qty_per_slice,
        total_qty=total_qty,
        expected_cost_bps=expected_cost_bps,
    )


# ── 3. 동적 슬리피지 추정 ─────────────────────────────────

def estimate_dynamic_slippage(
    order_qty: int,
    price: float,
    avg_volume: int,
    volatility_pct: float,
    time_of_day: str = "mid",
    market_regime: str = "normal",
) -> SlippageModel:
    """주문 특성과 시장 상태를 반영한 동적 슬리피지를 추정한다.

    Parameters
    ----------
    order_qty : int
        주문 수량.
    price : float
        현재가.
    avg_volume : int
        일평균 거래량.
    volatility_pct : float
        최근 변동성 (%, 예: 2.5 → 2.5%).
    time_of_day : str
        "open" (개장), "close" (마감), "lunch" (점심), "mid" (일반).
    market_regime : str
        "normal", "volatile", "crisis".

    Returns
    -------
    SlippageModel
    """
    # 기본 스프레드 비용 (매수/매도 평균)
    base_bps = 4.0

    # 변동성 조정: 변동성이 높을수록 슬리피지 증가
    volatility_adj_bps = round(volatility_pct * 0.5, 4)

    # 거래량 대비 주문 크기 조정
    if avg_volume > 0:
        vol_ratio = order_qty / avg_volume
        volume_adj_bps = round(max(0.0, 10.0 * (vol_ratio - 0.01)), 4)
    else:
        volume_adj_bps = 10.0  # 거래량 정보 없음 → 보수적

    # 시간대 조정
    time_adj_map = {
        "open": 5.0,
        "close": 5.0,
        "lunch": 3.0,
        "mid": 0.0,
    }
    time_adj_bps = time_adj_map.get(time_of_day, 0.0)

    # 시장 레짐 조정
    regime_adj_map = {
        "crisis": 10.0,
        "volatile": 5.0,
        "normal": 0.0,
    }
    regime_adj_bps = regime_adj_map.get(market_regime, 0.0)

    total_bps = round(
        base_bps + volatility_adj_bps + volume_adj_bps
        + time_adj_bps + regime_adj_bps, 4,
    )

    logger.debug(
        "Slippage: base=%.1f vol_adj=%.1f volum_adj=%.1f time=%.1f regime=%.1f total=%.1f bps",
        base_bps, volatility_adj_bps, volume_adj_bps,
        time_adj_bps, regime_adj_bps, total_bps,
    )

    return SlippageModel(
        base_bps=base_bps,
        volatility_adj_bps=volatility_adj_bps,
        volume_adj_bps=volume_adj_bps,
        time_adj_bps=time_adj_bps,
        total_bps=total_bps,
        regime=market_regime,
    )


# ── 4. 분할 주문 계획 ─────────────────────────────────────

def _build_vwap_children(
    total_qty: int,
    price: float,
    ohlcv: Optional[pd.DataFrame],
    participation_rate: float,
) -> List[Dict]:
    """VWAP 알고 기반 child order 목록 생성."""
    schedule = compute_vwap_schedule(total_qty, ohlcv, participation_rate)
    children: List[Dict] = []
    for s in schedule.time_slices:
        children.append({
            "slice_id": s["slice_id"],
            "qty": s["target_qty"],
            "target_time": s["time_range"],
            "price_limit": round(price * (1 + schedule.expected_cost_bps / 10000), 2),
        })
    return children


def _build_twap_children(
    total_qty: int,
    price: float,
    duration_minutes: int = 120,
    interval_minutes: int = 10,
) -> List[Dict]:
    """TWAP 알고 기반 child order 목록 생성."""
    schedule = compute_twap_schedule(total_qty, duration_minutes, interval_minutes)

    if schedule.n_slices <= 0:
        return []

    children: List[Dict] = []
    remaining = total_qty
    for i in range(schedule.n_slices):
        if i < schedule.n_slices - 1:
            qty = schedule.qty_per_slice
        else:
            qty = remaining  # 마지막 슬라이스: 잔여 전량
        remaining -= qty

        children.append({
            "slice_id": i,
            "qty": qty,
            "target_time": f"+{i * interval_minutes}min",
            "price_limit": round(price * (1 + schedule.expected_cost_bps / 10000), 2),
        })
    return children


def _build_iceberg_children(
    total_qty: int,
    price: float,
    visible_ratio: float = 0.1,
) -> List[Dict]:
    """Iceberg 알고 기반 child order 목록 생성.

    보이는 수량을 제한하여 시장 충격을 최소화한다.
    """
    visible_qty = max(1, int(total_qty * visible_ratio))
    n_slices = max(1, (total_qty + visible_qty - 1) // visible_qty)

    children: List[Dict] = []
    remaining = total_qty
    for i in range(n_slices):
        qty = min(visible_qty, remaining)
        remaining -= qty
        children.append({
            "slice_id": i,
            "qty": qty,
            "target_time": f"slice_{i}",
            "price_limit": round(price * 1.001, 2),  # 시장가 + 1bps 허용
        })
        if remaining <= 0:
            break
    return children


def _build_pov_children(
    total_qty: int,
    price: float,
    avg_volume: int,
    participation_rate: float,
) -> List[Dict]:
    """Percentage of Volume (PoV) 알고 기반 child order 목록 생성.

    목표 참여율을 유지하면서 시장 거래량에 비례하여 주문.
    """
    # 분당 평균 거래량 (09:00~15:30 = 390분)
    vol_per_min = avg_volume / 390 if avg_volume > 0 else 1.0
    target_per_min = max(1, int(vol_per_min * participation_rate))

    # 10분 단위로 분할
    interval = 10
    qty_per_slice = max(1, target_per_min * interval)
    n_slices = max(1, (total_qty + qty_per_slice - 1) // qty_per_slice)

    children: List[Dict] = []
    remaining = total_qty
    for i in range(n_slices):
        qty = min(qty_per_slice, remaining)
        remaining -= qty
        children.append({
            "slice_id": i,
            "qty": qty,
            "target_time": f"+{i * interval}min",
            "price_limit": round(price * 1.002, 2),  # 2bps 허용
        })
        if remaining <= 0:
            break
    return children


def plan_split_order(
    total_qty: int,
    price: float,
    avg_volume: int,
    algo: str = "vwap",
    ohlcv: Optional[pd.DataFrame] = None,
    urgency: str = "medium",
) -> SplitOrder:
    """실행 알고리즘에 따라 분할 주문 계획을 생성한다.

    Parameters
    ----------
    total_qty : int
        주문 총 수량.
    price : float
        현재가 (또는 기준가).
    avg_volume : int
        일평균 거래량.
    algo : str
        "vwap", "twap", "iceberg", "pov" 중 택 1.
    ohlcv : pd.DataFrame, optional
        과거 OHLCV 데이터 (VWAP 알고에 필요).
    urgency : str
        "low", "medium", "high" — 참여율/실행 속도 결정.

    Returns
    -------
    SplitOrder
    """
    participation = URGENCY_PARTICIPATION.get(urgency, 0.20)
    parent_id = str(uuid.uuid4())[:12]

    algo_lower = algo.lower()
    if algo_lower == "vwap":
        children = _build_vwap_children(total_qty, price, ohlcv, participation)
    elif algo_lower == "twap":
        # urgency에 따라 duration 조정
        duration_map = {"low": 240, "medium": 120, "high": 60}
        duration = duration_map.get(urgency, 120)
        children = _build_twap_children(total_qty, price, duration, 10)
    elif algo_lower == "iceberg":
        children = _build_iceberg_children(total_qty, price)
    elif algo_lower == "pov":
        children = _build_pov_children(total_qty, price, avg_volume, participation)
    else:
        logger.warning("Unknown algo '%s', fallback to TWAP", algo)
        children = _build_twap_children(total_qty, price)

    logger.info(
        "Split order: algo=%s, total=%d, children=%d, urgency=%s",
        algo_lower, total_qty, len(children), urgency,
    )

    return SplitOrder(
        parent_order_id=parent_id,
        child_orders=children,
        algo=algo_lower,
        total_qty=total_qty,
        filled_qty=0,
        avg_price=0.0,
        status="planned",
    )


# ── 5. 실행 품질 평가 ─────────────────────────────────────

def evaluate_execution(
    fills: List[Dict],
    benchmark_price: float,
    ohlcv: pd.DataFrame,
) -> ExecutionQuality:
    """실행 결과의 품질을 평가한다.

    Parameters
    ----------
    fills : list[dict]
        체결 내역. 각 항목: {"price": float, "qty": int, "time": str}.
    benchmark_price : float
        기준가 (주문 시점 가격).
    ohlcv : pd.DataFrame
        OHLCV 데이터 (VWAP/TWAP 비교용).

    Returns
    -------
    ExecutionQuality
    """
    if not fills:
        return ExecutionQuality(
            algo="unknown", benchmark_price=benchmark_price,
            avg_fill_price=0.0, slippage_bps=0.0, vs_vwap_bps=0.0,
            vs_twap_bps=0.0, market_impact_bps=0.0, grade="D",
        )

    # 가중 평균 체결가
    total_qty = sum(f["qty"] for f in fills)
    if total_qty <= 0:
        return ExecutionQuality(
            algo="unknown", benchmark_price=benchmark_price,
            avg_fill_price=0.0, slippage_bps=0.0, vs_vwap_bps=0.0,
            vs_twap_bps=0.0, market_impact_bps=0.0, grade="D",
        )

    avg_fill = sum(f["price"] * f["qty"] for f in fills) / total_qty

    # 벤치마크 대비 슬리피지 (bps)
    if benchmark_price > 0:
        slippage_bps = (avg_fill - benchmark_price) / benchmark_price * 10000
    else:
        slippage_bps = 0.0

    # VWAP 대비
    vwap = _compute_vwap_from_ohlcv(ohlcv)
    if vwap > 0:
        vs_vwap_bps = (avg_fill - vwap) / vwap * 10000
    else:
        vs_vwap_bps = 0.0

    # TWAP 대비
    twap = _compute_twap_from_ohlcv(ohlcv)
    if twap > 0:
        vs_twap_bps = (avg_fill - twap) / twap * 10000
    else:
        vs_twap_bps = 0.0

    # 시장 충격 = |슬리피지| (절대값으로 방향 무관하게 평가)
    market_impact_bps = abs(slippage_bps)

    # 등급 산정 (절대값 기준)
    abs_slip = abs(slippage_bps)
    if abs_slip < 5:
        grade = "A"
    elif abs_slip < 15:
        grade = "B"
    elif abs_slip < 30:
        grade = "C"
    else:
        grade = "D"

    logger.info(
        "Execution quality: avg_fill=%.2f, slippage=%.1fbps, grade=%s",
        avg_fill, slippage_bps, grade,
    )

    return ExecutionQuality(
        algo="evaluated",
        benchmark_price=round(benchmark_price, 2),
        avg_fill_price=round(avg_fill, 2),
        slippage_bps=round(slippage_bps, 2),
        vs_vwap_bps=round(vs_vwap_bps, 2),
        vs_twap_bps=round(vs_twap_bps, 2),
        market_impact_bps=round(market_impact_bps, 2),
        grade=grade,
    )


# ── 6. 텔레그램 포맷 ──────────────────────────────────────

def format_execution_plan(split: SplitOrder) -> str:
    """분할 주문 계획을 텔레그램 메시지 형식으로 포맷한다.

    Returns
    -------
    str
        plain text + emoji 형식 메시지 (parse_mode 없음).
    """
    lines: List[str] = []
    lines.append(f"{'='*28}")
    lines.append(f"  분할주문 계획")
    lines.append(f"{'='*28}")
    lines.append("")
    lines.append(f"  알고리즘: {split.algo.upper()}")
    lines.append(f"  총 수량: {split.total_qty:,}주")
    lines.append(f"  분할 수: {len(split.child_orders)}개")
    lines.append(f"  상태: {split.status}")
    lines.append("")

    for child in split.child_orders:
        sid = child.get("slice_id", "?")
        qty = child.get("qty", 0)
        target = child.get("target_time", "?")
        limit_p = child.get("price_limit", 0)
        lines.append(
            f"  #{sid}  {qty:>6,}주  {target}  (limit {limit_p:,.0f})"
        )

    lines.append("")
    lines.append(f"{'='*28}")
    return "\n".join(lines)


def format_execution_quality(quality: ExecutionQuality) -> str:
    """실행 품질 평가를 텔레그램 메시지 형식으로 포맷한다.

    Returns
    -------
    str
        plain text + emoji 형식 메시지 (parse_mode 없음).
    """
    grade_emoji = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}.get(quality.grade, "⚪")

    lines: List[str] = []
    lines.append(f"{'='*28}")
    lines.append(f"  실행 품질 평가  {grade_emoji}")
    lines.append(f"{'='*28}")
    lines.append("")
    lines.append(f"  등급: {quality.grade} {grade_emoji}")
    lines.append(f"  기준가: {quality.benchmark_price:,.0f}원")
    lines.append(f"  평균체결가: {quality.avg_fill_price:,.0f}원")
    lines.append("")
    lines.append(f"  슬리피지: {quality.slippage_bps:+.1f} bps")
    lines.append(f"  vs VWAP: {quality.vs_vwap_bps:+.1f} bps")
    lines.append(f"  vs TWAP: {quality.vs_twap_bps:+.1f} bps")
    lines.append(f"  시장충격: {quality.market_impact_bps:.1f} bps")
    lines.append("")
    lines.append(f"{'='*28}")
    return "\n".join(lines)
