"""시그널 정제 엔진 — v5.0-5.

45개 시그널을 상관분석으로 정제하여 20~25개 독립 시그널로 축소한다.
다중 공선성(multicollinearity)을 제거하고, 정보 비율이 높은 시그널만 유지.

핵심 기능:
  1. SignalCorrelationMatrix — 시그널 간 상관계수 행렬 계산
  2. SignalPruner — 고상관 시그널 그룹에서 대표 선정 + 중복 제거
  3. PurgedKFoldCV — Purged K-Fold 교차검증 (시계열 데이터 누출 방지)
  4. SignalQualityScore — 시그널 품질 평가 (IC, turnover, decay)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 시그널 메타데이터 ─────────────────────────────────────

@dataclass
class SignalMeta:
    """시그널 메타 정보."""
    name: str
    category: str = ""         # "momentum", "value", "sentiment", "technical", etc.
    source_module: str = ""    # 소스 모듈 경로
    lookback_days: int = 0     # 룩백 기간
    update_freq: str = "daily" # "realtime", "daily", "weekly"


@dataclass
class SignalQuality:
    """시그널 품질 평가."""
    name: str
    ic: float = 0.0                    # Information Coefficient (수익률과의 상관)
    ic_std: float = 0.0                # IC 표준편차
    icir: float = 0.0                  # IC Information Ratio (IC/IC_std)
    avg_turnover: float = 0.0          # 평균 회전율
    decay_halflife_days: float = 0.0   # 시그널 반감기 (일)
    hit_rate: float = 0.0              # 방향 적중률
    quality_score: float = 0.0         # 종합 품질 점수 (0~100)
    is_selected: bool = True           # 정제 후 선택 여부


@dataclass
class CorrelationCluster:
    """고상관 시그널 클러스터."""
    cluster_id: int
    signals: list[str]
    avg_correlation: float
    representative: str        # 대표 시그널
    removed: list[str]         # 제거된 시그널


@dataclass
class RefineryReport:
    """시그널 정제 결과."""
    timestamp: str = ""
    total_signals: int = 0
    selected_signals: int = 0
    removed_signals: int = 0
    clusters: list[CorrelationCluster] = field(default_factory=list)
    quality_scores: list[SignalQuality] = field(default_factory=list)
    correlation_threshold: float = 0.0
    recommendations: list[str] = field(default_factory=list)


# ── 상관 행렬 계산 ────────────────────────────────────────

class SignalCorrelationMatrix:
    """시그널 간 상관계수 행렬 계산.

    numpy 없이 순수 Python으로 구현.
    """

    @staticmethod
    def compute(
        signals: dict[str, list[float]],
    ) -> dict[tuple[str, str], float]:
        """시그널 간 피어슨 상관계수를 계산한다.

        Args:
            signals: {시그널명: 값 리스트} 딕셔너리. 모든 리스트 길이 동일.

        Returns:
            {(시그널A, 시그널B): 상관계수} 딕셔너리.
        """
        names = sorted(signals.keys())
        corr_map: dict[tuple[str, str], float] = {}

        for i, name_a in enumerate(names):
            vals_a = signals[name_a]
            for j, name_b in enumerate(names):
                if j <= i:
                    continue
                vals_b = signals[name_b]
                r = _pearson(vals_a, vals_b)
                corr_map[(name_a, name_b)] = r
                corr_map[(name_b, name_a)] = r

        return corr_map

    @staticmethod
    def find_high_correlation_pairs(
        corr_map: dict[tuple[str, str], float],
        threshold: float = 0.7,
    ) -> list[tuple[str, str, float]]:
        """임계값 이상 고상관 쌍 추출."""
        seen = set()
        pairs = []
        for (a, b), r in corr_map.items():
            key = (min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            if abs(r) >= threshold:
                pairs.append((a, b, r))

        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        return pairs


# ── 시그널 프루너 ─────────────────────────────────────────

class SignalPruner:
    """고상관 시그널 그룹에서 대표 선정 + 중복 제거.

    알고리즘:
      1. 상관계수 행렬에서 threshold 이상 쌍을 추출
      2. Union-Find로 클러스터 구성
      3. 각 클러스터에서 품질 점수가 가장 높은 시그널을 대표로 선정
      4. 나머지는 제거 후보

    Target: 45개 → 20~25개
    """

    def __init__(self, correlation_threshold: float = 0.7):
        self.threshold = correlation_threshold

    def prune(
        self,
        signals: dict[str, list[float]],
        quality_map: dict[str, float] | None = None,
        target_count: int | None = None,
    ) -> RefineryReport:
        """시그널 정제 실행.

        Args:
            signals: {시그널명: 값 리스트}.
            quality_map: {시그널명: 품질 점수}. None이면 IC 기반 자동 계산.
            target_count: 목표 시그널 수. None이면 자동.

        Returns:
            RefineryReport.
        """
        if not signals:
            return RefineryReport()

        names = sorted(signals.keys())
        total = len(names)

        # 상관 행렬 계산
        corr_map = SignalCorrelationMatrix.compute(signals)
        high_pairs = SignalCorrelationMatrix.find_high_correlation_pairs(
            corr_map, self.threshold,
        )

        # 품질 점수 (없으면 분산 기반 대체)
        if quality_map is None:
            quality_map = {}
            for name, vals in signals.items():
                quality_map[name] = _signal_variance_score(vals)

        # Union-Find로 클러스터 구성
        parent: dict[str, str] = {n: n for n in names}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for a, b, _ in high_pairs:
            union(a, b)

        # 클러스터 수집
        cluster_members: dict[str, list[str]] = {}
        for n in names:
            root = find(n)
            cluster_members.setdefault(root, []).append(n)

        # 각 클러스터에서 대표 선정
        clusters: list[CorrelationCluster] = []
        selected: set[str] = set()
        removed: set[str] = set()

        for idx, (root, members) in enumerate(
            sorted(cluster_members.items(), key=lambda x: -len(x[1]))
        ):
            if len(members) == 1:
                selected.add(members[0])
                continue

            # 품질 기준 정렬
            members_sorted = sorted(
                members, key=lambda n: quality_map.get(n, 0), reverse=True,
            )
            representative = members_sorted[0]
            selected.add(representative)
            to_remove = members_sorted[1:]
            removed.update(to_remove)

            # 클러스터 내 평균 상관
            pair_corrs = []
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    key = (a, b) if (a, b) in corr_map else (b, a)
                    if key in corr_map:
                        pair_corrs.append(abs(corr_map[key]))
            avg_corr = sum(pair_corrs) / len(pair_corrs) if pair_corrs else 0

            clusters.append(CorrelationCluster(
                cluster_id=idx,
                signals=members,
                avg_correlation=round(avg_corr, 3),
                representative=representative,
                removed=to_remove,
            ))

        # 목표 수에 맞게 추가 제거
        if target_count and len(selected) > target_count:
            # 품질 낮은 순으로 추가 제거
            selected_list = sorted(
                selected, key=lambda n: quality_map.get(n, 0),
            )
            while len(selected) > target_count and selected_list:
                to_drop = selected_list.pop(0)
                selected.discard(to_drop)
                removed.add(to_drop)

        # 품질 점수 리스트
        quality_scores = []
        for name in names:
            quality_scores.append(SignalQuality(
                name=name,
                quality_score=round(quality_map.get(name, 0), 2),
                is_selected=name in selected,
            ))
        quality_scores.sort(key=lambda q: q.quality_score, reverse=True)

        # 추천
        recommendations = []
        if len(removed) > 0:
            recommendations.append(
                f"✂️ {total}개 → {len(selected)}개 시그널 정제 완료 "
                f"(상관 {self.threshold:.1f} 기준)"
            )
        multi_clusters = [c for c in clusters if len(c.signals) > 2]
        if multi_clusters:
            for c in multi_clusters[:3]:
                recommendations.append(
                    f"🔗 클러스터 {c.cluster_id}: {', '.join(c.signals[:3])}... "
                    f"(상관 {c.avg_correlation:.2f}) → 대표: {c.representative}"
                )

        return RefineryReport(
            total_signals=total,
            selected_signals=len(selected),
            removed_signals=len(removed),
            clusters=clusters,
            quality_scores=quality_scores,
            correlation_threshold=self.threshold,
            recommendations=recommendations,
        )


# ── Purged K-Fold CV ──────────────────────────────────────

class PurgedKFoldCV:
    """Purged K-Fold 교차검증.

    시계열 데이터에서 train/test 사이 데이터 누출을 방지.
    test 폴드 전후 purge_gap 만큼의 데이터를 train에서 제거.
    """

    def __init__(self, n_splits: int = 5, purge_gap: int = 5):
        """
        Args:
            n_splits: 폴드 수.
            purge_gap: test 전후 제거할 행 수.
        """
        self.n_splits = n_splits
        self.purge_gap = purge_gap

    def split(self, n_samples: int) -> list[tuple[list[int], list[int]]]:
        """인덱스를 train/test로 분할.

        Args:
            n_samples: 전체 데이터 수.

        Returns:
            [(train_indices, test_indices), ...] 리스트.
        """
        if n_samples < self.n_splits * 3:
            logger.warning(
                "PurgedKFold: 데이터 부족 (%d < %d)",
                n_samples, self.n_splits * 3,
            )
            return []

        fold_size = n_samples // self.n_splits
        splits = []

        for i in range(self.n_splits):
            test_start = i * fold_size
            test_end = min((i + 1) * fold_size, n_samples)

            # Purge: test 전후 gap 제거
            purge_start = max(0, test_start - self.purge_gap)
            purge_end = min(n_samples, test_end + self.purge_gap)

            test_idx = list(range(test_start, test_end))
            train_idx = (
                list(range(0, purge_start))
                + list(range(purge_end, n_samples))
            )

            if train_idx and test_idx:
                splits.append((train_idx, test_idx))

        return splits


# ── 시그널 카탈로그 (K-Quant 현재 시그널 맵) ──────────────

SIGNAL_CATALOG: list[SignalMeta] = [
    # Technical
    SignalMeta("rsi_14", "technical", "features.technical"),
    SignalMeta("rsi_7", "technical", "features.technical"),
    SignalMeta("macd_histogram", "technical", "features.technical"),
    SignalMeta("macd_signal_cross", "technical", "features.technical"),
    SignalMeta("bb_position", "technical", "features.technical"),
    SignalMeta("bb_width", "technical", "features.technical"),
    SignalMeta("stochastic_k", "technical", "features.technical"),
    SignalMeta("stochastic_d", "technical", "features.technical"),
    SignalMeta("obv_trend", "technical", "features.technical"),
    SignalMeta("atr_pct", "technical", "features.technical"),
    SignalMeta("adx", "technical", "features.technical"),
    SignalMeta("cci", "technical", "features.technical"),
    SignalMeta("williams_r", "technical", "features.technical"),
    SignalMeta("ichimoku_signal", "technical", "features.technical"),
    SignalMeta("volume_ma_ratio", "technical", "features.technical"),
    # Momentum
    SignalMeta("momentum_1w", "momentum", "signal.scoring"),
    SignalMeta("momentum_1m", "momentum", "signal.scoring"),
    SignalMeta("momentum_3m", "momentum", "signal.scoring"),
    SignalMeta("roc_5d", "momentum", "features.technical"),
    SignalMeta("roc_20d", "momentum", "features.technical"),
    SignalMeta("volatility_breakout", "momentum", "signal.volatility_breakout"),
    SignalMeta("gap_signal", "momentum", "signal.gap_trader"),
    SignalMeta("surge_score", "momentum", "signal.surge_detector"),
    # Value / Fundamental
    SignalMeta("per_relative", "value", "signal.factor_scoring"),
    SignalMeta("pbr_relative", "value", "signal.factor_scoring"),
    SignalMeta("roe_rank", "value", "signal.factor_scoring"),
    SignalMeta("dividend_yield", "value", "signal.factor_scoring"),
    SignalMeta("earnings_surprise", "value", "signal.earnings_tracker"),
    SignalMeta("financial_health", "value", "signal.financial_analyzer"),
    # Sentiment / Flow
    SignalMeta("foreign_net_flow", "flow", "signal.foreign_predictor"),
    SignalMeta("institutional_flow", "flow", "signal.institutional_tracker"),
    SignalMeta("short_interest", "flow", "signal.short_selling"),
    SignalMeta("margin_balance", "flow", "signal.margin_balance"),
    SignalMeta("program_trade", "flow", "signal.contrarian_signal"),
    SignalMeta("stealth_accumulation", "flow", "signal.stealth_accumulation"),
    SignalMeta("news_sentiment", "sentiment", "ml.sentiment"),
    SignalMeta("market_psychology", "sentiment", "signal.market_psychology"),
    # Macro / Regime
    SignalMeta("vix_level", "macro", "signal.market_regime"),
    SignalMeta("market_regime", "macro", "signal.market_regime"),
    SignalMeta("sector_momentum", "macro", "core.sector_rotation"),
    SignalMeta("fx_signal", "macro", "signal.fx_strategy"),
    # Strategy-specific
    SignalMeta("swing_entry", "strategy", "signal.swing_trader"),
    SignalMeta("pair_spread", "strategy", "signal.pair_signal"),
    SignalMeta("tenbagger_score", "strategy", "signal.tenbagger_hunter"),
    SignalMeta("contrarian_composite", "strategy", "signal.contrarian_signal"),
    SignalMeta("consensus_divergence", "strategy", "signal.consensus_tracker"),
]


def get_signal_catalog() -> list[SignalMeta]:
    """현재 시그널 카탈로그 반환."""
    return SIGNAL_CATALOG.copy()


# ── 헬퍼 함수 ─────────────────────────────────────────────

def _pearson(x: list[float], y: list[float]) -> float:
    """피어슨 상관계수 (순수 Python)."""
    n = min(len(x), len(y))
    if n < 3:
        return 0.0

    x, y = x[:n], y[:n]
    mx = sum(x) / n
    my = sum(y) / n

    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y))

    if sx == 0 or sy == 0:
        return 0.0

    return cov / (sx * sy)


def _signal_variance_score(values: list[float]) -> float:
    """분산 기반 시그널 품질 대체 점수.

    값의 변동이 클수록 정보량이 높다고 간주.
    """
    if not values or len(values) < 2:
        return 0.0

    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)

    # 정규화: 표준편차를 0~100 스케일
    std = math.sqrt(var)
    # 비제로 비율도 반영 (상수 시그널 패널티)
    nonzero_ratio = sum(1 for v in values if v != 0) / n

    return min(100.0, std * 10 * nonzero_ratio)


# ── 텔레그램 포맷 ─────────────────────────────────────────

def format_refinery_report(report: RefineryReport) -> str:
    """정제 결과를 텔레그램 포맷."""
    if report.total_signals == 0:
        return "🔬 시그널 정제: 데이터 없음"

    lines = [
        "🔬 시그널 정제 결과",
        "━" * 25,
        f"전체: {report.total_signals}개 → 선택: {report.selected_signals}개",
        f"제거: {report.removed_signals}개 (상관 {report.correlation_threshold:.1f} 기준)",
        "",
    ]

    # 클러스터
    multi = [c for c in report.clusters if len(c.signals) > 1]
    if multi:
        lines.append(f"🔗 클러스터: {len(multi)}개")
        for c in multi[:5]:
            lines.append(
                f"  [{c.cluster_id}] {', '.join(c.signals[:3])}"
                f"{'...' if len(c.signals) > 3 else ''}"
                f" → 대표: {c.representative}"
            )
        lines.append("")

    # 상위 품질 시그널
    selected = [q for q in report.quality_scores if q.is_selected][:10]
    if selected:
        lines.append("⭐ 상위 시그널:")
        for q in selected:
            lines.append(f"  {q.name}: {q.quality_score:.1f}점")

    # 추천
    if report.recommendations:
        lines.extend(["", "━" * 25])
        for rec in report.recommendations[:3]:
            lines.append(f"  {rec}")

    return "\n".join(lines)
