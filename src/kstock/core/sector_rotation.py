"""섹터 로테이션 엔진 - Phase 2-2.

섹터 간 모멘텀/평균회귀 기반 로테이션 시그널을 생성하고,
포트폴리오의 섹터 배분 개선을 제안하는 시스템.

주요 기능:
  1. 섹터별 상대 강도 추적 (1주/1개월/3개월)
  2. 모멘텀 로테이션: 강세 섹터 → 비중 확대
  3. 평균회귀 역발상: 급락 섹터 → 반등 기회
  4. 포트폴리오 섹터 리밸런싱 제안
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# ── 확장 섹터 ETF 맵 ──────────────────────────────────────────────

SECTOR_ETF_MAP: dict[str, str] = {
    "반도체": "091160",
    "2차전지": "305540",
    "바이오": "244580",
    "자동차": "091170",
    "철강/소재": "117680",
    "금융": "091180",
    "에너지/화학": "117460",
    "건설": "117700",
    "IT/소프트웨어": "098560",
    "미디어/엔터": "228800",
}

# 종목 → 섹터 매핑 (risk_manager 호환)
TICKER_SECTOR_MAP: dict[str, str] = {
    "005930": "반도체", "000660": "반도체",
    "373220": "2차전지", "006400": "2차전지",
    "247540": "2차전지", "086520": "2차전지",
    "035420": "IT/소프트웨어", "035720": "IT/소프트웨어",
    "207940": "바이오", "068270": "바이오",
    "005380": "자동차", "000270": "자동차",
    "055550": "금융", "105560": "금융", "316140": "금융",
    "005490": "철강/소재", "051910": "에너지/화학",
    "017670": "IT/소프트웨어", "030200": "IT/소프트웨어",
    "352820": "미디어/엔터", "009540": "건설", "012450": "건설",
}


# ── 데이터 구조 ───────────────────────────────────────────────────

@dataclass
class SectorMomentum:
    """섹터별 모멘텀 데이터."""
    sector: str
    etf_code: str
    return_1w_pct: float = 0.0
    return_1m_pct: float = 0.0
    return_3m_pct: float = 0.0
    momentum_score: float = 0.0   # -100 ~ +100
    rank: int = 0
    total_sectors: int = 0
    signal: str = ""              # "강세", "약세", "중립", "반등 기대"


@dataclass
class RotationSignal:
    """로테이션 시그널."""
    signal_type: str    # "momentum", "mean_reversion", "overweight", "underweight"
    sector: str
    direction: str      # "overweight" / "underweight" / "rotate_in" / "rotate_out"
    strength: float     # 0~1
    reason: str
    data: dict = field(default_factory=dict)


@dataclass
class SectorDashboard:
    """섹터 로테이션 대시보드."""
    timestamp: str
    sectors: list[SectorMomentum]
    signals: list[RotationSignal]
    portfolio_sectors: dict[str, float]  # 현재 섹터 비중
    recommendations: list[str]


class SectorRotationEngine:
    """섹터 로테이션 분석 엔진."""

    def __init__(self, db=None, yf_client=None):
        self.db = db
        self.yf_client = yf_client

    # ── 섹터 모멘텀 계산 ─────────────────────────────────────────

    def compute_momentum(
        self, ohlcv_map: dict[str, pd.DataFrame],
    ) -> list[SectorMomentum]:
        """섹터별 모멘텀 스코어 계산.

        Args:
            ohlcv_map: ETF 코드 → OHLCV DataFrame

        Returns:
            모멘텀 순으로 정렬된 SectorMomentum 리스트
        """
        results = []
        for sector, etf_code in SECTOR_ETF_MAP.items():
            df = ohlcv_map.get(etf_code)
            if df is None or df.empty or len(df) < 5:
                continue

            close = df["close"].astype(float)
            current = close.iloc[-1]

            # 1주 수익률
            idx_1w = min(5, len(close) - 1)
            ret_1w = (current - close.iloc[-idx_1w - 1]) / close.iloc[-idx_1w - 1] * 100

            # 1개월 수익률
            idx_1m = min(20, len(close) - 1)
            ret_1m = (current - close.iloc[-idx_1m - 1]) / close.iloc[-idx_1m - 1] * 100

            # 3개월 수익률
            idx_3m = min(60, len(close) - 1)
            ret_3m = (current - close.iloc[-idx_3m - 1]) / close.iloc[-idx_3m - 1] * 100

            # 복합 모멘텀 스코어: 단기(30%) + 중기(40%) + 장기(30%)
            momentum = ret_1w * 0.3 + ret_1m * 0.4 + ret_3m * 0.3

            # 시그널 판단
            if momentum > 5:
                signal = "강세"
            elif momentum < -5:
                # 3개월 급락 후 1주 반등 → 반등 기대
                if ret_3m < -10 and ret_1w > 1:
                    signal = "반등 기대"
                else:
                    signal = "약세"
            else:
                signal = "중립"

            results.append(SectorMomentum(
                sector=sector,
                etf_code=etf_code,
                return_1w_pct=round(ret_1w, 2),
                return_1m_pct=round(ret_1m, 2),
                return_3m_pct=round(ret_3m, 2),
                momentum_score=round(momentum, 2),
                signal=signal,
            ))

        # 순위 매기기
        results.sort(key=lambda s: s.momentum_score, reverse=True)
        total = len(results)
        for i, r in enumerate(results):
            r.rank = i + 1
            r.total_sectors = total

        return results

    # ── 로테이션 시그널 생성 ──────────────────────────────────────

    def generate_signals(
        self,
        sectors: list[SectorMomentum],
        portfolio_weights: dict[str, float] | None = None,
    ) -> list[RotationSignal]:
        """로테이션 시그널 생성.

        Args:
            sectors: 모멘텀 분석 결과
            portfolio_weights: 현재 포트폴리오 섹터 비중 (%)

        Returns:
            로테이션 시그널 리스트
        """
        if not sectors:
            return []

        signals: list[RotationSignal] = []
        portfolio_weights = portfolio_weights or {}

        # 1. 모멘텀 로테이션: 상위 2 섹터 → overweight 추천
        for s in sectors[:2]:
            if s.momentum_score > 3:
                current_weight = portfolio_weights.get(s.sector, 0)
                signals.append(RotationSignal(
                    signal_type="momentum",
                    sector=s.sector,
                    direction="overweight",
                    strength=min(1.0, s.momentum_score / 15),
                    reason=(
                        f"{s.sector} 모멘텀 +{s.momentum_score:.1f} "
                        f"(1주 {s.return_1w_pct:+.1f}%, 1개월 {s.return_1m_pct:+.1f}%)"
                    ),
                    data={"current_weight": current_weight},
                ))

        # 2. 하위 2 섹터 → underweight / 이탈 경고
        for s in sectors[-2:]:
            if s.momentum_score < -3:
                current_weight = portfolio_weights.get(s.sector, 0)
                if current_weight > 10:  # 보유 비중이 있을 때만
                    signals.append(RotationSignal(
                        signal_type="momentum",
                        sector=s.sector,
                        direction="underweight",
                        strength=min(1.0, abs(s.momentum_score) / 15),
                        reason=(
                            f"{s.sector} 약세 {s.momentum_score:.1f} "
                            f"— 현재 비중 {current_weight:.0f}% 축소 고려"
                        ),
                        data={"current_weight": current_weight},
                    ))

        # 3. 평균회귀: 3개월 급락 + 1주 반등 시작
        for s in sectors:
            if s.signal == "반등 기대":
                signals.append(RotationSignal(
                    signal_type="mean_reversion",
                    sector=s.sector,
                    direction="rotate_in",
                    strength=0.6,
                    reason=(
                        f"{s.sector} 3개월 {s.return_3m_pct:+.1f}% 급락 후 "
                        f"1주 {s.return_1w_pct:+.1f}% 반등 시작"
                    ),
                ))

        # 4. 포트폴리오 편중 경고
        for sector, weight in portfolio_weights.items():
            if weight > 50:
                signals.append(RotationSignal(
                    signal_type="overweight",
                    sector=sector,
                    direction="underweight",
                    strength=min(1.0, weight / 80),
                    reason=f"{sector} 비중 {weight:.0f}% — 편중 위험, 분산 투자 권장",
                    data={"weight": weight},
                ))

        return signals

    # ── 포트폴리오 섹터 비중 계산 ────────────────────────────────

    def compute_portfolio_sectors(
        self, holdings: list[dict],
    ) -> dict[str, float]:
        """현재 포트폴리오의 섹터별 비중 계산."""
        total_value = 0.0
        sector_values: dict[str, float] = {}

        for h in holdings:
            ticker = h.get("ticker", "")
            value = h.get("eval_amount", 0) or (
                h.get("current_price", 0) * h.get("quantity", 1)
            )
            total_value += value
            sector = TICKER_SECTOR_MAP.get(ticker, "기타")
            sector_values[sector] = sector_values.get(sector, 0) + value

        if total_value <= 0:
            return {}

        return {
            sector: round(value / total_value * 100, 1)
            for sector, value in sorted(
                sector_values.items(), key=lambda x: x[1], reverse=True,
            )
        }

    # ── 대시보드 생성 ─────────────────────────────────────────────

    def create_dashboard(
        self,
        ohlcv_map: dict[str, pd.DataFrame],
        holdings: list[dict] | None = None,
    ) -> SectorDashboard:
        """종합 섹터 로테이션 대시보드 생성."""
        sectors = self.compute_momentum(ohlcv_map)
        portfolio_weights = (
            self.compute_portfolio_sectors(holdings) if holdings else {}
        )
        signals = self.generate_signals(sectors, portfolio_weights)

        # 추천 요약
        recommendations = []
        for sig in signals:
            if sig.strength >= 0.5:
                emoji = "🟢" if sig.direction in ("overweight", "rotate_in") else "🔴"
                recommendations.append(f"{emoji} {sig.reason}")

        return SectorDashboard(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            sectors=sectors,
            signals=signals,
            portfolio_sectors=portfolio_weights,
            recommendations=recommendations,
        )


# ── 텔레그램 포맷 ─────────────────────────────────────────────────

def format_sector_dashboard(dashboard: SectorDashboard) -> str:
    """섹터 대시보드를 텔레그램 메시지로 포맷."""
    lines = [
        f"🔄 {USER_NAME} 섹터 로테이션 대시보드",
        "━" * 25,
        f"⏰ {dashboard.timestamp}",
        "",
        "📊 섹터 모멘텀 순위",
    ]

    for s in dashboard.sectors:
        # 신호 이모지
        if s.signal == "강세":
            emoji = "🔥"
        elif s.signal == "약세":
            emoji = "❄️"
        elif s.signal == "반등 기대":
            emoji = "🔄"
        else:
            emoji = "➖"

        lines.append(
            f"  {s.rank}. {emoji} {s.sector} "
            f"[1주 {s.return_1w_pct:+.1f}% | 1개월 {s.return_1m_pct:+.1f}%]"
        )

    # 포트폴리오 섹터 비중
    if dashboard.portfolio_sectors:
        lines.extend(["", "💼 내 포트폴리오 섹터 비중"])
        for sector, weight in dashboard.portfolio_sectors.items():
            bar_len = int(weight / 5)
            bar = "█" * bar_len
            lines.append(f"  {sector}: {weight:.0f}% {bar}")

    # 로테이션 시그널
    if dashboard.signals:
        lines.extend(["", "━" * 25, "📡 로테이션 시그널"])
        for sig in dashboard.signals[:5]:
            strength_bar = "●" * int(sig.strength * 5) + "○" * (5 - int(sig.strength * 5))
            lines.append(f"  [{strength_bar}] {sig.reason}")

    # 추천
    if dashboard.recommendations:
        lines.extend(["", "━" * 25, "💡 추천 액션"])
        for rec in dashboard.recommendations[:3]:
            lines.append(f"  {rec}")

    return "\n".join(lines)


def format_sector_brief(dashboard: SectorDashboard) -> str:
    """섹터 간략 요약 (모닝브리핑용)."""
    if not dashboard.sectors:
        return ""

    top = dashboard.sectors[0]
    bottom = dashboard.sectors[-1]
    return (
        f"🔄 섹터: {top.sector} 강세({top.return_1m_pct:+.1f}%) | "
        f"{bottom.sector} 약세({bottom.return_1m_pct:+.1f}%)"
    )
