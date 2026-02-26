"""Execution Replay 대시보드 — v5.0-4.

실전 매매 결과 vs 백테스트 예측을 비교하여 전략 성능을 실시간 검증한다.

핵심 기능:
  1. ExecutionRecord — 실전 매매 기록
  2. BacktestPrediction — 백테스트 예측 기록
  3. ReplayEngine — 실전 vs 예측 비교 + 슬리피지 분석
  4. StrategyDrift — 전략 드리프트(실전 성능 이탈) 감지
  5. DeflatedSharpe — Deflated Sharpe Ratio로 다중검정 보정
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"


# ── 데이터 구조 ───────────────────────────────────────────

@dataclass
class ExecutionRecord:
    """실전 매매 기록."""
    trade_id: str
    ticker: str
    name: str
    side: str               # "buy" or "sell"
    strategy: str            # 전략 타입 (A~G)
    signal_time: str         # 시그널 발생 시각
    execution_time: str      # 실제 체결 시각
    signal_price: float      # 시그널 시점 가격
    execution_price: float   # 실제 체결 가격
    quantity: int = 0
    slippage_pct: float = 0.0    # (체결가 - 시그널가) / 시그널가
    pnl_pct: float = 0.0         # 실현 수익률
    commission: float = 0.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.signal_price > 0 and self.execution_price > 0:
            if self.side == "buy":
                self.slippage_pct = (
                    (self.execution_price - self.signal_price) / self.signal_price
                )
            else:
                self.slippage_pct = (
                    (self.signal_price - self.execution_price) / self.signal_price
                )


@dataclass
class BacktestPrediction:
    """백테스트 예측 기록."""
    ticker: str
    strategy: str
    prediction_date: str
    predicted_return_pct: float    # 백테스트 예상 수익률
    predicted_win_prob: float      # 승률
    confidence: float = 0.5        # 신뢰도 (0~1)
    actual_return_pct: float | None = None  # 실제 수익률 (나중에 채움)
    metadata: dict = field(default_factory=dict)


@dataclass
class SlippageAnalysis:
    """슬리피지 분석 결과."""
    total_trades: int = 0
    avg_slippage_pct: float = 0.0
    max_slippage_pct: float = 0.0
    total_slippage_cost: float = 0.0
    by_strategy: dict[str, float] = field(default_factory=dict)
    by_side: dict[str, float] = field(default_factory=dict)
    by_time_of_day: dict[str, float] = field(default_factory=dict)


@dataclass
class StrategyDriftResult:
    """전략 드리프트 분석 결과."""
    strategy: str
    bt_sharpe: float = 0.0         # 백테스트 샤프
    live_sharpe: float = 0.0       # 실전 샤프
    bt_win_rate: float = 0.0       # 백테스트 승률
    live_win_rate: float = 0.0     # 실전 승률
    bt_avg_return: float = 0.0     # 백테스트 평균 수익률
    live_avg_return: float = 0.0   # 실전 평균 수익률
    drift_score: float = 0.0       # 드리프트 점수 (0~1, 높을수록 이탈)
    is_drifting: bool = False      # 유의미한 이탈 여부
    recommendation: str = ""


@dataclass
class ReplayDashboard:
    """Execution Replay 종합 대시보드."""
    timestamp: str
    total_live_trades: int = 0
    total_bt_predictions: int = 0
    slippage: SlippageAnalysis = field(default_factory=SlippageAnalysis)
    drift_results: list[StrategyDriftResult] = field(default_factory=list)
    deflated_sharpe: float = 0.0
    overall_bt_sharpe: float = 0.0
    overall_live_sharpe: float = 0.0
    accuracy_rate: float = 0.0      # 방향 일치율
    recommendations: list[str] = field(default_factory=list)


# ── Replay Engine ─────────────────────────────────────────

class ReplayEngine:
    """실전 vs 백테스트 비교 엔진."""

    # 드리프트 판단 임계값
    DRIFT_THRESHOLD = 0.3       # 승률 차이 30% 이상
    SHARPE_DECAY_THRESHOLD = 0.5  # 샤프 비율 50% 이상 감소

    def __init__(self):
        self._executions: list[ExecutionRecord] = []
        self._predictions: list[BacktestPrediction] = []

    def add_execution(self, record: ExecutionRecord) -> None:
        """실전 매매 기록 추가."""
        self._executions.append(record)

    def add_prediction(self, prediction: BacktestPrediction) -> None:
        """백테스트 예측 기록 추가."""
        self._predictions.append(prediction)

    def add_executions_batch(self, records: list[ExecutionRecord]) -> None:
        """실전 매매 기록 일괄 추가."""
        self._executions.extend(records)

    def add_predictions_batch(self, predictions: list[BacktestPrediction]) -> None:
        """백테스트 예측 기록 일괄 추가."""
        self._predictions.extend(predictions)

    # ── 슬리피지 분석 ────────────────────────────────────

    def analyze_slippage(self) -> SlippageAnalysis:
        """슬리피지 종합 분석."""
        if not self._executions:
            return SlippageAnalysis()

        trades = self._executions
        slippages = [t.slippage_pct for t in trades]

        # 전략별 슬리피지
        by_strategy: dict[str, list[float]] = {}
        by_side: dict[str, list[float]] = {}

        for t in trades:
            by_strategy.setdefault(t.strategy, []).append(t.slippage_pct)
            by_side.setdefault(t.side, []).append(t.slippage_pct)

        total_cost = sum(
            abs(t.slippage_pct) * t.execution_price * t.quantity
            for t in trades
        )

        return SlippageAnalysis(
            total_trades=len(trades),
            avg_slippage_pct=_safe_mean(slippages),
            max_slippage_pct=max(abs(s) for s in slippages) if slippages else 0,
            total_slippage_cost=total_cost,
            by_strategy={k: _safe_mean(v) for k, v in by_strategy.items()},
            by_side={k: _safe_mean(v) for k, v in by_side.items()},
        )

    # ── 전략 드리프트 분석 ────────────────────────────────

    def analyze_drift(self) -> list[StrategyDriftResult]:
        """전략별 드리프트 분석."""
        strategies = set()
        for e in self._executions:
            if e.strategy:
                strategies.add(e.strategy)
        for p in self._predictions:
            if p.strategy:
                strategies.add(p.strategy)

        results = []
        for strategy in sorted(strategies):
            live = [e for e in self._executions if e.strategy == strategy]
            bt = [p for p in self._predictions if p.strategy == strategy]

            live_returns = [e.pnl_pct for e in live if e.pnl_pct != 0]
            bt_returns = [p.predicted_return_pct for p in bt if p.predicted_return_pct != 0]

            live_sharpe = _compute_sharpe(live_returns)
            bt_sharpe = _compute_sharpe(bt_returns)

            live_win_rate = (
                sum(1 for r in live_returns if r > 0) / len(live_returns)
                if live_returns else 0
            )
            bt_win_rate = (
                sum(1 for r in bt_returns if r > 0) / len(bt_returns)
                if bt_returns else 0
            )

            live_avg = _safe_mean(live_returns)
            bt_avg = _safe_mean(bt_returns)

            # 드리프트 스코어 (0~1)
            win_drift = abs(bt_win_rate - live_win_rate)
            return_drift = abs(bt_avg - live_avg) / max(abs(bt_avg), 0.01)
            sharpe_drift = (
                abs(bt_sharpe - live_sharpe) / max(abs(bt_sharpe), 0.01)
                if bt_sharpe != 0 else 0
            )
            drift_score = min(1.0, (win_drift + return_drift * 0.3 + sharpe_drift * 0.3) / 1.6)

            is_drifting = (
                drift_score > self.DRIFT_THRESHOLD
                or (bt_sharpe > 0 and live_sharpe < bt_sharpe * (1 - self.SHARPE_DECAY_THRESHOLD))
            )

            # 추천
            if is_drifting:
                if live_sharpe < 0:
                    rec = f"전략 {strategy} 실전 성능 음수 — 즉시 비활성화 검토"
                elif drift_score > 0.6:
                    rec = f"전략 {strategy} 심각한 이탈 — 파라미터 재최적화 필요"
                else:
                    rec = f"전략 {strategy} 이탈 감지 — 모니터링 강화"
            else:
                rec = f"전략 {strategy} 정상 운영"

            results.append(StrategyDriftResult(
                strategy=strategy,
                bt_sharpe=round(bt_sharpe, 3),
                live_sharpe=round(live_sharpe, 3),
                bt_win_rate=round(bt_win_rate, 3),
                live_win_rate=round(live_win_rate, 3),
                bt_avg_return=round(bt_avg, 4),
                live_avg_return=round(live_avg, 4),
                drift_score=round(drift_score, 3),
                is_drifting=is_drifting,
                recommendation=rec,
            ))

        return results

    # ── 방향 일치율 ──────────────────────────────────────

    def compute_accuracy(self) -> float:
        """백테스트 예측 vs 실전 방향 일치율."""
        matched = 0
        total = 0

        for pred in self._predictions:
            if pred.actual_return_pct is None:
                continue
            total += 1
            # 방향이 같으면 일치
            if (pred.predicted_return_pct > 0 and pred.actual_return_pct > 0) or \
               (pred.predicted_return_pct < 0 and pred.actual_return_pct < 0):
                matched += 1

        return matched / total if total > 0 else 0.0

    # ── Deflated Sharpe Ratio ─────────────────────────────

    def compute_deflated_sharpe(
        self,
        observed_sharpe: float,
        num_trials: int,
        returns: list[float] | None = None,
    ) -> float:
        """Deflated Sharpe Ratio (Bailey & López de Prado).

        다중검정(multiple testing) 보정을 적용하여
        우연으로 높은 샤프를 얻을 확률을 보정한다.

        Args:
            observed_sharpe: 관측된 샤프 비율.
            num_trials: 테스트한 전략 수.
            returns: 수익률 시계열 (skewness, kurtosis 계산용).

        Returns:
            Deflated Sharpe Ratio (0에 가까울수록 과적합 의심).
        """
        if num_trials <= 1 or observed_sharpe <= 0:
            return observed_sharpe

        # Expected max Sharpe from random trials
        e_max_sharpe = _expected_max_sharpe(num_trials)

        # 수익률 통계
        if returns and len(returns) > 3:
            n = len(returns)
            mean_r = sum(returns) / n
            var_r = sum((r - mean_r) ** 2 for r in returns) / n
            std_r = math.sqrt(var_r) if var_r > 0 else 1e-8

            # Skewness
            skew = sum((r - mean_r) ** 3 for r in returns) / (n * std_r ** 3)
            # Excess kurtosis
            kurt = sum((r - mean_r) ** 4 for r in returns) / (n * std_r ** 4) - 3

            # Variance of Sharpe estimator
            var_sharpe = (
                1 + 0.5 * observed_sharpe ** 2
                - skew * observed_sharpe
                + (kurt / 4) * observed_sharpe ** 2
            ) / max(n - 1, 1)
        else:
            var_sharpe = 1.0

        std_sharpe = math.sqrt(max(var_sharpe, 1e-10))

        # PSR (Probabilistic Sharpe Ratio)
        if std_sharpe > 0:
            z = (observed_sharpe - e_max_sharpe) / std_sharpe
            dsr = _norm_cdf(z)
        else:
            dsr = 0.5

        return round(max(0.0, dsr), 4)

    # ── 종합 대시보드 ────────────────────────────────────

    def create_dashboard(self) -> ReplayDashboard:
        """Execution Replay 종합 대시보드 생성."""
        slippage = self.analyze_slippage()
        drift_results = self.analyze_drift()
        accuracy = self.compute_accuracy()

        # 전체 샤프 계산
        live_returns = [e.pnl_pct for e in self._executions if e.pnl_pct != 0]
        bt_returns = [p.predicted_return_pct for p in self._predictions if p.predicted_return_pct != 0]

        live_sharpe = _compute_sharpe(live_returns)
        bt_sharpe = _compute_sharpe(bt_returns)

        # 전략 수 = 시험 횟수
        strategies_tested = len(set(
            e.strategy for e in self._executions if e.strategy
        ))
        dsr = self.compute_deflated_sharpe(
            live_sharpe, max(strategies_tested, 1), live_returns,
        )

        # 추천 생성
        recommendations = []
        if slippage.avg_slippage_pct > 0.005:  # 0.5% 이상
            recommendations.append(
                f"⚠️ 평균 슬리피지 {slippage.avg_slippage_pct:.2%} — 지정가 주문 비율 상향 권장"
            )

        drifting = [d for d in drift_results if d.is_drifting]
        if drifting:
            for d in drifting:
                recommendations.append(f"🔄 {d.recommendation}")

        if dsr < 0.3 and strategies_tested > 2:
            recommendations.append(
                f"📉 DSR {dsr:.2f} — {strategies_tested}개 전략 중 과적합 위험"
            )

        if accuracy < 0.4:
            recommendations.append(
                f"🎯 방향 일치율 {accuracy:.0%} — 시그널 모델 재검토 필요"
            )

        return ReplayDashboard(
            timestamp=datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            total_live_trades=len(self._executions),
            total_bt_predictions=len(self._predictions),
            slippage=slippage,
            drift_results=drift_results,
            deflated_sharpe=dsr,
            overall_bt_sharpe=round(bt_sharpe, 3),
            overall_live_sharpe=round(live_sharpe, 3),
            accuracy_rate=round(accuracy, 3),
            recommendations=recommendations,
        )


# ── 헬퍼 함수 ─────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _compute_sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """샤프 비율 계산."""
    if not returns or len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    excess = [r - risk_free for r in returns]
    mean_excess = sum(excess) / len(excess)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var) if var > 0 else 1e-8
    return mean_excess / std


def _expected_max_sharpe(num_trials: int) -> float:
    """Bailey & López de Prado: E[max(SR)] from N trials."""
    if num_trials <= 1:
        return 0.0
    # Approximation: E[max(Z_1,...,Z_N)] ≈ (1 - γ) * Φ^{-1}(1-1/N) + γ * Φ^{-1}(1-1/(Ne))
    # Simplified to: sqrt(2 * ln(N))
    return math.sqrt(2 * math.log(max(num_trials, 2)))


def _norm_cdf(x: float) -> float:
    """표준 정규 CDF 근사."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ── 글로벌 인스턴스 ───────────────────────────────────────

_engine = ReplayEngine()


def get_replay_engine() -> ReplayEngine:
    return _engine


# ── 텔레그램 포맷 ─────────────────────────────────────────

def format_replay_dashboard(dashboard: ReplayDashboard) -> str:
    """Replay 대시보드를 텔레그램 포맷."""
    lines = [
        f"📊 {USER_NAME} Execution Replay",
        "━" * 25,
        f"⏰ {dashboard.timestamp}",
        "",
        f"실전 매매: {dashboard.total_live_trades}건",
        f"백테스트 예측: {dashboard.total_bt_predictions}건",
        f"방향 일치율: {dashboard.accuracy_rate:.0%}",
        "",
        "📈 샤프 비율",
        f"  백테스트: {dashboard.overall_bt_sharpe:.3f}",
        f"  실전: {dashboard.overall_live_sharpe:.3f}",
        f"  DSR (보정): {dashboard.deflated_sharpe:.3f}",
    ]

    # 슬리피지
    s = dashboard.slippage
    if s.total_trades > 0:
        lines.extend([
            "",
            "💸 슬리피지",
            f"  평균: {s.avg_slippage_pct:.3%} | 최대: {s.max_slippage_pct:.3%}",
            f"  총 비용: {s.total_slippage_cost:,.0f}원",
        ])

    # 드리프트
    drifting = [d for d in dashboard.drift_results if d.is_drifting]
    if drifting:
        lines.extend(["", "🔄 전략 드리프트 감지"])
        for d in drifting:
            lines.append(
                f"  {d.strategy}: BT {d.bt_win_rate:.0%} vs 실전 {d.live_win_rate:.0%} "
                f"(drift {d.drift_score:.2f})"
            )

    # 추천
    if dashboard.recommendations:
        lines.extend(["", "━" * 25, "💡 추천"])
        for rec in dashboard.recommendations[:5]:
            lines.append(f"  {rec}")

    return "\n".join(lines)


def format_slippage_report(analysis: SlippageAnalysis) -> str:
    """슬리피지 분석 결과를 텔레그램 포맷."""
    if analysis.total_trades == 0:
        return "💸 슬리피지: 데이터 없음"

    lines = [
        "💸 슬리피지 분석",
        "━" * 25,
        f"총 매매: {analysis.total_trades}건",
        f"평균: {analysis.avg_slippage_pct:.3%}",
        f"최대: {analysis.max_slippage_pct:.3%}",
        f"총 비용: {analysis.total_slippage_cost:,.0f}원",
    ]

    if analysis.by_strategy:
        lines.extend(["", "전략별:"])
        for k, v in sorted(analysis.by_strategy.items()):
            lines.append(f"  전략 {k}: {v:.3%}")

    return "\n".join(lines)
