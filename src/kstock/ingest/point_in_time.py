"""Point-in-Time 데이터 무결성 엔진 — v5.0-1.

모든 데이터에 시점(event_time), 수집시간(ingest_time), 출처(source)를
태깅하여 미래 데이터 유입(look-ahead bias)을 원천 차단한다.

핵심 기능:
  1. DataPoint 래퍼 — 모든 수치 데이터에 시점·출처 메타 첨부
  2. AsOfJoin — 특정 기준 시점(cutoff) 이전 데이터만 반환
  3. SourceRegistry — 데이터 출처별 지연(latency) 관리
  4. PITValidator — 데이터 프레임 전체의 시점 무결성 검증
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import pandas as pd

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


# ── 데이터 소스 열거 ──────────────────────────────────────

class DataSourceType(str, Enum):
    """데이터 출처 유형."""
    KIS_REALTIME = "kis_realtime"     # KIS 실시간 (지연 ~0.5초)
    KIS_HISTORICAL = "kis_historical"  # KIS 일봉 (T+0 15:30 확정)
    YFINANCE = "yfinance"              # yfinance (지연 ~15분)
    NAVER = "naver"                    # 네이버 금융 (지연 ~20분)
    MANUAL = "manual"                  # 수동 입력
    SCREENSHOT = "screenshot"          # 스크린샷 OCR
    BACKTEST = "backtest"              # 백테스트 합성 데이터


# 소스별 추정 지연 시간 (초)
SOURCE_LATENCY: dict[str, float] = {
    DataSourceType.KIS_REALTIME: 0.5,
    DataSourceType.KIS_HISTORICAL: 0.0,    # 확정 데이터
    DataSourceType.YFINANCE: 900.0,        # ~15분
    DataSourceType.NAVER: 1200.0,          # ~20분
    DataSourceType.MANUAL: 0.0,
    DataSourceType.SCREENSHOT: 0.0,
    DataSourceType.BACKTEST: 0.0,
}


# ── 데이터 포인트 래퍼 ────────────────────────────────────

@dataclass
class DataPoint:
    """시점 태깅된 단일 데이터 포인트."""

    value: Any
    event_time: datetime          # 이벤트 발생 시점 (시세 기준 시각)
    ingest_time: datetime         # 시스템 수집 시점
    source: str                   # DataSourceType 값
    ticker: str = ""
    field_name: str = ""          # "close", "volume", "rsi", etc.
    confidence: float = 1.0       # 0~1, 수동입력은 낮게
    metadata: dict = field(default_factory=dict)

    @property
    def latency_seconds(self) -> float:
        """수집 지연 시간 (초)."""
        return (self.ingest_time - self.event_time).total_seconds()

    @property
    def is_stale(self) -> bool:
        """데이터가 5분 이상 지연되면 stale."""
        return self.latency_seconds > 300

    def to_dict(self) -> dict:
        """직렬화."""
        return {
            "value": self.value,
            "event_time": self.event_time.isoformat(),
            "ingest_time": self.ingest_time.isoformat(),
            "source": self.source,
            "ticker": self.ticker,
            "field_name": self.field_name,
            "confidence": self.confidence,
            "latency_s": round(self.latency_seconds, 2),
        }

    @classmethod
    def now(cls, value: Any, source: str, ticker: str = "",
            field_name: str = "", **kwargs) -> DataPoint:
        """현재 시점으로 즉시 생성."""
        now = datetime.now(KST)
        latency = SOURCE_LATENCY.get(source, 0.0)
        event_time = now - timedelta(seconds=latency)
        return cls(
            value=value,
            event_time=event_time,
            ingest_time=now,
            source=source,
            ticker=ticker,
            field_name=field_name,
            **kwargs,
        )


# ── As-Of Join 엔진 ──────────────────────────────────────

class AsOfJoinEngine:
    """기준 시점(cutoff) 이전 데이터만 허용하는 필터.

    백테스트와 실전 모두에서 미래 데이터 유입을 차단한다.

    Usage:
        engine = AsOfJoinEngine()
        # OHLCV에 시점 컬럼 추가
        df = engine.tag_dataframe(df, source="yfinance", ticker="005930")
        # cutoff 이전 데이터만 추출
        safe = engine.filter_asof(df, cutoff=some_datetime)
    """

    @staticmethod
    def tag_dataframe(
        df: pd.DataFrame,
        source: str,
        ticker: str = "",
        event_col: str = "date",
    ) -> pd.DataFrame:
        """DataFrame에 시점·출처 메타 컬럼을 추가한다.

        Args:
            df: OHLCV 또는 feature DataFrame.
            source: DataSourceType 값.
            ticker: 종목 코드.
            event_col: 이벤트 시점 컬럼명 (없으면 인덱스).

        Returns:
            _pit_event_time, _pit_ingest_time, _pit_source 컬럼이 추가된 DataFrame.
        """
        if df.empty:
            return df

        result = df.copy()
        now = datetime.now(KST)

        # event_time 결정
        if event_col in result.columns:
            result["_pit_event_time"] = pd.to_datetime(result[event_col])
        elif isinstance(result.index, pd.DatetimeIndex):
            result["_pit_event_time"] = result.index
        else:
            result["_pit_event_time"] = now

        result["_pit_ingest_time"] = now
        result["_pit_source"] = source
        result["_pit_ticker"] = ticker

        return result

    @staticmethod
    def filter_asof(
        df: pd.DataFrame,
        cutoff: datetime,
        event_col: str = "_pit_event_time",
    ) -> pd.DataFrame:
        """cutoff 시점 이전 데이터만 반환한다.

        Args:
            df: tag_dataframe()으로 태깅된 DataFrame.
            cutoff: 기준 시점.

        Returns:
            cutoff 이전 행만 포함하는 DataFrame.
        """
        if df.empty or event_col not in df.columns:
            return df

        cutoff_ts = pd.Timestamp(cutoff)
        mask = df[event_col] <= cutoff_ts
        filtered = df.loc[mask]

        dropped = len(df) - len(filtered)
        if dropped > 0:
            logger.info(
                "AsOfJoin: cutoff=%s 기준 %d행 제거 (미래 데이터 차단)",
                cutoff.isoformat(), dropped,
            )

        return filtered

    @staticmethod
    def latest_asof(
        df: pd.DataFrame,
        cutoff: datetime,
        event_col: str = "_pit_event_time",
    ) -> pd.Series | None:
        """cutoff 이전 가장 최신 행을 반환한다.

        Args:
            df: 태깅된 DataFrame.
            cutoff: 기준 시점.

        Returns:
            최신 행 (Series) 또는 None.
        """
        filtered = AsOfJoinEngine.filter_asof(df, cutoff, event_col)
        if filtered.empty:
            return None
        return filtered.iloc[-1]


# ── PIT Validator ─────────────────────────────────────────

@dataclass
class PITViolation:
    """시점 무결성 위반."""
    violation_type: str    # "future_data", "stale_data", "source_mismatch"
    severity: str          # "critical", "warning"
    description: str
    row_index: int | None = None
    details: dict = field(default_factory=dict)


class PITValidator:
    """데이터 프레임의 시점 무결성을 검증한다.

    주요 검증:
      1. 미래 데이터 (event_time > 현재시간): critical
      2. 시점 역전 (event_time이 정렬되지 않음): warning
      3. 스테일 데이터 (ingest - event > threshold): warning
      4. 소스 일관성 (한 데이터셋에 여러 소스 혼합): warning
    """

    def __init__(self, stale_threshold_seconds: float = 600.0):
        self.stale_threshold = stale_threshold_seconds

    def validate(
        self,
        df: pd.DataFrame,
        reference_time: datetime | None = None,
    ) -> list[PITViolation]:
        """DataFrame 전체의 시점 무결성을 검증한다.

        Args:
            df: _pit_event_time, _pit_ingest_time, _pit_source 컬럼 필요.
            reference_time: 기준 시점. None이면 현재 시각.

        Returns:
            위반 리스트.
        """
        violations: list[PITViolation] = []

        if df.empty:
            return violations

        ref = reference_time or datetime.now(KST)
        ref_ts = pd.Timestamp(ref)

        # 1. 미래 데이터 검사
        if "_pit_event_time" in df.columns:
            future_mask = df["_pit_event_time"] > ref_ts
            future_count = future_mask.sum()
            if future_count > 0:
                violations.append(PITViolation(
                    violation_type="future_data",
                    severity="critical",
                    description=(
                        f"미래 데이터 {future_count}행 발견. "
                        "Look-ahead bias 위험."
                    ),
                    details={"count": int(future_count)},
                ))

            # 2. 시점 역전 검사
            event_times = df["_pit_event_time"]
            if not event_times.is_monotonic_increasing:
                reversals = (event_times.diff() < pd.Timedelta(0)).sum()
                if reversals > 0:
                    violations.append(PITViolation(
                        violation_type="time_reversal",
                        severity="warning",
                        description=(
                            f"시점 역전 {reversals}회. 데이터 정렬 필요."
                        ),
                        details={"reversal_count": int(reversals)},
                    ))

        # 3. 스테일 데이터 검사
        if "_pit_ingest_time" in df.columns and "_pit_event_time" in df.columns:
            latency = (
                df["_pit_ingest_time"] - df["_pit_event_time"]
            ).dt.total_seconds()
            stale_mask = latency > self.stale_threshold
            stale_count = stale_mask.sum()
            if stale_count > 0:
                violations.append(PITViolation(
                    violation_type="stale_data",
                    severity="warning",
                    description=(
                        f"스테일 데이터 {stale_count}행 "
                        f"(지연 >{self.stale_threshold}초)."
                    ),
                    details={
                        "count": int(stale_count),
                        "max_latency_s": round(float(latency.max()), 1),
                    },
                ))

        # 4. 소스 일관성 검사
        if "_pit_source" in df.columns:
            sources = df["_pit_source"].unique()
            if len(sources) > 1:
                violations.append(PITViolation(
                    violation_type="source_mismatch",
                    severity="warning",
                    description=(
                        f"데이터 소스 {len(sources)}개 혼합: "
                        f"{', '.join(str(s) for s in sources)}"
                    ),
                    details={"sources": list(str(s) for s in sources)},
                ))

        return violations


# ── Source Registry ───────────────────────────────────────

class SourceRegistry:
    """데이터 소스 이력 관리.

    각 소스의 마지막 성공/실패, 평균 지연, 신뢰도를 추적한다.
    """

    def __init__(self):
        self._sources: dict[str, dict] = {}

    def record_fetch(
        self,
        source: str,
        ticker: str,
        success: bool,
        latency_ms: float = 0.0,
        record_count: int = 0,
    ) -> None:
        """데이터 수집 결과 기록."""
        key = f"{source}:{ticker}"
        if key not in self._sources:
            self._sources[key] = {
                "source": source,
                "ticker": ticker,
                "success_count": 0,
                "failure_count": 0,
                "total_latency_ms": 0.0,
                "last_success": None,
                "last_failure": None,
                "total_records": 0,
            }

        entry = self._sources[key]
        if success:
            entry["success_count"] += 1
            entry["total_latency_ms"] += latency_ms
            entry["last_success"] = datetime.now(KST).isoformat()
            entry["total_records"] += record_count
        else:
            entry["failure_count"] += 1
            entry["last_failure"] = datetime.now(KST).isoformat()

    def get_reliability(self, source: str, ticker: str = "") -> float:
        """소스 신뢰도 (0~1)."""
        key = f"{source}:{ticker}"
        entry = self._sources.get(key)
        if not entry:
            return 1.0  # 기본값
        total = entry["success_count"] + entry["failure_count"]
        if total == 0:
            return 1.0
        return entry["success_count"] / total

    def get_avg_latency_ms(self, source: str, ticker: str = "") -> float:
        """소스 평균 지연 (ms)."""
        key = f"{source}:{ticker}"
        entry = self._sources.get(key)
        if not entry or entry["success_count"] == 0:
            return 0.0
        return entry["total_latency_ms"] / entry["success_count"]

    def get_summary(self) -> list[dict]:
        """전체 소스 상태 요약."""
        summary = []
        for key, entry in self._sources.items():
            total = entry["success_count"] + entry["failure_count"]
            reliability = entry["success_count"] / total if total > 0 else 1.0
            avg_lat = (
                entry["total_latency_ms"] / entry["success_count"]
                if entry["success_count"] > 0 else 0.0
            )
            summary.append({
                "source": entry["source"],
                "ticker": entry["ticker"],
                "reliability": round(reliability, 3),
                "avg_latency_ms": round(avg_lat, 1),
                "total_fetches": total,
                "total_records": entry["total_records"],
                "last_success": entry["last_success"],
                "last_failure": entry["last_failure"],
            })
        return summary


# ── 글로벌 인스턴스 ───────────────────────────────────────

_registry = SourceRegistry()
_validator = PITValidator()
_asof = AsOfJoinEngine()


def get_registry() -> SourceRegistry:
    """글로벌 SourceRegistry 반환."""
    return _registry


def get_validator() -> PITValidator:
    """글로벌 PITValidator 반환."""
    return _validator


def get_asof_engine() -> AsOfJoinEngine:
    """글로벌 AsOfJoinEngine 반환."""
    return _asof


# ── 텔레그램 포맷 ─────────────────────────────────────────

def format_pit_status(violations: list[PITViolation]) -> str:
    """PIT 검증 결과를 텔레그램 포맷."""
    if not violations:
        return "✅ PIT 무결성: 이상 없음"

    lines = ["🔍 PIT 데이터 무결성 검증", "━" * 25, ""]
    critical = [v for v in violations if v.severity == "critical"]
    warnings = [v for v in violations if v.severity == "warning"]

    if critical:
        lines.append("🔴 심각")
        for v in critical:
            lines.append(f"  • {v.description}")

    if warnings:
        lines.append("🟡 경고")
        for v in warnings:
            lines.append(f"  • {v.description}")

    return "\n".join(lines)


def format_source_summary(registry: SourceRegistry | None = None) -> str:
    """소스 상태 요약을 텔레그램 포맷."""
    reg = registry or _registry
    summary = reg.get_summary()

    if not summary:
        return "📡 데이터 소스: 미등록"

    lines = ["📡 데이터 소스 상태", "━" * 25, ""]
    for s in summary:
        icon = "🟢" if s["reliability"] >= 0.9 else "🟡" if s["reliability"] >= 0.7 else "🔴"
        lines.append(
            f"  {icon} {s['source']}"
            f" | 신뢰도 {s['reliability']:.0%}"
            f" | 평균지연 {s['avg_latency_ms']:.0f}ms"
            f" | 수집 {s['total_fetches']}회"
        )

    return "\n".join(lines)
