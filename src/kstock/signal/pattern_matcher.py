"""Historical chart pattern matching using DTW (Dynamic Time Warping).

v9.4: 현재 차트 패턴을 과거 데이터와 비교하여
"이 패턴이 과거 N번 나타났고, X% 확률로 Y일 후 Z% 변동" 인사이트 제공.

DTW는 numpy만 사용하여 구현 (외부 라이브러리 불필요).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── 데이터 클래스 ────────────────────────────────────────────

@dataclass
class PatternMatch:
    """과거 유사 패턴 1건."""
    match_date: str = ""           # 매칭 시작일 (YYYY-MM-DD)
    match_end_date: str = ""       # 매칭 종료일
    similarity: float = 0.0       # 0~1 (1=동일)
    forward_5d_return: float = 0.0
    forward_10d_return: float = 0.0
    forward_20d_return: float = 0.0


@dataclass
class PatternReport:
    """패턴 매칭 종합 리포트."""
    ticker: str = ""
    current_window: str = ""      # 현재 분석 구간
    total_windows: int = 0        # 비교한 총 윈도우 수
    matches: list[PatternMatch] = field(default_factory=list)
    # 통계
    avg_5d_return: float = 0.0
    avg_10d_return: float = 0.0
    avg_20d_return: float = 0.0
    positive_5d_pct: float = 0.0  # 5일 후 상승 확률 (%)
    positive_10d_pct: float = 0.0
    positive_20d_pct: float = 0.0


# ── DTW 구현 (numpy) ─────────────────────────────────────────

def _dtw_distance(s1: np.ndarray, s2: np.ndarray) -> float:
    """Dynamic Time Warping 거리 계산.

    O(n*m) 기본 구현. 20x20 윈도우에서 충분히 빠름.
    """
    n, m = len(s1), len(s2)
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(s1[i - 1] - s2[j - 1])
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

    return float(dtw[n, m])


def _normalize_series(prices: np.ndarray) -> np.ndarray:
    """가격 시리즈를 0~1로 정규화."""
    mn = prices.min()
    mx = prices.max()
    if mx - mn < 1e-8:
        return np.zeros_like(prices)
    return (prices - mn) / (mx - mn)


# ── 패턴 매칭 엔진 ───────────────────────────────────────────

class PatternMatcher:
    """과거 차트 패턴 매칭 엔진."""

    def __init__(self, lookback: int = 20, top_k: int = 10, min_similarity: float = 0.5):
        """
        Args:
            lookback: 비교 윈도우 크기 (거래일)
            top_k: 반환할 상위 유사 패턴 수
            min_similarity: 최소 유사도 임계값 (0~1)
        """
        self.lookback = lookback
        self.top_k = top_k
        self.min_similarity = min_similarity

    def find_similar_patterns(
        self,
        ohlcv: pd.DataFrame,
        current_only: bool = True,
    ) -> PatternReport:
        """OHLCV 데이터에서 유사 패턴 검색.

        Args:
            ohlcv: 최소 lookback+20 행 이상의 OHLCV DataFrame
                   (columns: date, close 필수)
            current_only: True면 최근 lookback일만 대상, False면 전체 스캔

        Returns:
            PatternReport with top_k matches
        """
        report = PatternReport()

        if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
            return report

        closes = ohlcv["close"].values.astype(float)
        dates = ohlcv["date"].values if "date" in ohlcv.columns else np.arange(len(closes))

        n = len(closes)
        lb = self.lookback

        if n < lb + 5:  # 최소 lookback + 5일 forward return 필요
            return report

        # 현재 패턴 (최근 lookback일)
        current = _normalize_series(closes[-lb:])
        report.current_window = f"{dates[-lb]}~{dates[-1]}" if hasattr(dates[-1], '__str__') else ""

        # 과거 모든 윈도우와 비교 (현재 윈도우 제외)
        # forward return 계산을 위해 최소 20일 이후 데이터 필요
        max_forward = 20
        total_windows = n - lb - max_forward
        if total_windows <= 0:
            return report

        report.total_windows = total_windows
        distances = []

        for i in range(total_windows):
            window = _normalize_series(closes[i : i + lb])
            dist = _dtw_distance(current, window)
            distances.append((i, dist))

        # DTW 거리를 유사도(0~1)로 변환
        if not distances:
            return report

        max_dist = max(d for _, d in distances) or 1.0
        similarities = [
            (idx, 1.0 - dist / max_dist)
            for idx, dist in distances
        ]

        # 유사도 상위 top_k 선택
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_matches = [
            (idx, sim) for idx, sim in similarities[:self.top_k]
            if sim >= self.min_similarity
        ]

        matches = []
        for idx, sim in top_matches:
            end_idx = idx + lb - 1
            # forward returns
            fr_5d = self._forward_return(closes, end_idx, 5)
            fr_10d = self._forward_return(closes, end_idx, 10)
            fr_20d = self._forward_return(closes, end_idx, 20)

            match_date = str(dates[idx]) if idx < len(dates) else ""
            match_end = str(dates[end_idx]) if end_idx < len(dates) else ""

            matches.append(PatternMatch(
                match_date=match_date[:10],  # YYYY-MM-DD
                match_end_date=match_end[:10],
                similarity=round(sim, 3),
                forward_5d_return=round(fr_5d, 4),
                forward_10d_return=round(fr_10d, 4),
                forward_20d_return=round(fr_20d, 4),
            ))

        report.matches = matches

        # 통계 산출
        if matches:
            report.avg_5d_return = np.mean([m.forward_5d_return for m in matches])
            report.avg_10d_return = np.mean([m.forward_10d_return for m in matches])
            report.avg_20d_return = np.mean([m.forward_20d_return for m in matches])
            report.positive_5d_pct = sum(1 for m in matches if m.forward_5d_return > 0) / len(matches) * 100
            report.positive_10d_pct = sum(1 for m in matches if m.forward_10d_return > 0) / len(matches) * 100
            report.positive_20d_pct = sum(1 for m in matches if m.forward_20d_return > 0) / len(matches) * 100

        return report

    def _forward_return(self, closes: np.ndarray, end_idx: int, days: int) -> float:
        """매칭 구간 종료 후 N일 수익률."""
        future_idx = end_idx + days
        if future_idx >= len(closes) or closes[end_idx] <= 0:
            return 0.0
        return (closes[future_idx] - closes[end_idx]) / closes[end_idx]

    def find_supply_demand_patterns(
        self,
        supply_df: pd.DataFrame,
        current_foreign_days: int = 0,
        current_inst_days: int = 0,
    ) -> str:
        """수급 패턴 매칭 (과거 유사 수급 상황 후 수익률).

        Args:
            supply_df: supply_demand 테이블 데이터
            current_foreign_days: 현재 외국인 연속 순매수 일수
            current_inst_days: 현재 기관 연속 순매수 일수

        Returns:
            요약 텍스트
        """
        if supply_df is None or supply_df.empty:
            return ""

        lines = []
        if current_foreign_days >= 3:
            lines.append(
                f"외국인 {current_foreign_days}일 연속 순매수 진행 중"
            )
        if current_inst_days >= 3:
            lines.append(
                f"기관 {current_inst_days}일 연속 순매수 진행 중"
            )

        return " / ".join(lines) if lines else ""


# ── 포맷팅 ───────────────────────────────────────────────────

def format_pattern_report(report: PatternReport) -> str:
    """PatternReport → 텍스트."""
    if not report.matches:
        return "📊 유사 패턴 없음 (데이터 부족)"

    n = len(report.matches)
    lines = [
        f"📊 과거 패턴 매칭 ({n}건 유사)",
        f"비교 구간: 최근 20일 vs 과거 {report.total_windows}개 윈도우",
        "",
    ]

    # 통계 요약
    lines.append("📈 유사 패턴 이후 수익률 통계:")
    lines.append(f"  5일 후: 평균 {report.avg_5d_return:+.1%} (상승 {report.positive_5d_pct:.0f}%)")
    lines.append(f"  10일 후: 평균 {report.avg_10d_return:+.1%} (상승 {report.positive_10d_pct:.0f}%)")
    lines.append(f"  20일 후: 평균 {report.avg_20d_return:+.1%} (상승 {report.positive_20d_pct:.0f}%)")
    lines.append("")

    # 상위 3건 상세
    lines.append("상위 유사 패턴:")
    for i, m in enumerate(report.matches[:3], 1):
        lines.append(
            f"  {i}. {m.match_date}~{m.match_end_date} "
            f"(유사도 {m.similarity:.0%}) → "
            f"5일 {m.forward_5d_return:+.1%}, "
            f"20일 {m.forward_20d_return:+.1%}"
        )

    return "\n".join(lines)


def format_pattern_for_debate(report: PatternReport) -> str:
    """AI 토론용 패턴 요약 (간략)."""
    if not report.matches:
        return ""

    n = len(report.matches)
    pos_pct = report.positive_20d_pct

    return (
        f"과거 유사패턴 {n}건 분석: "
        f"5일후 평균 {report.avg_5d_return:+.1%}, "
        f"20일후 평균 {report.avg_20d_return:+.1%} "
        f"(상승 확률 {pos_pct:.0f}%)"
    )
