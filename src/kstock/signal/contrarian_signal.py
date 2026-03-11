"""역발상(Contrarian) 시그널 고도화 - Phase 3-1.

한국 시장에 특화된 역발상 매매 시그널을 생성하는 시스템.

주요 시그널:
  1. 공포 극대치 매수: VIX 스파이크 + 외인 순매도 + RSI 과매도
  2. 탐욕 극대치 매도: VIX 저점 + 개인 순매수 폭증 + RSI 과매수
  3. 패닉셀링 감지: 거래량 급증 + 급락 + 외인/기관 이탈
  4. 우량주 가치 함정/기회: PBR/PER 극단 + 재무 건전
  5. 신용잔고 역발상: 신용 급증 → 고점 경고, 급감 → 저점
  6. 프로그램 매매 역발상: 프로그램 순매도 폭증 → 기술적 저점
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


# ── 데이터 구조 ───────────────────────────────────────────────────

@dataclass
class ContrarianSignal:
    """역발상 시그널."""
    signal_type: str       # "fear_buy", "greed_sell", "panic_buy", "value_buy", "margin_contrarian"
    ticker: str
    name: str
    direction: str         # "BUY" / "SELL" / "WATCH"
    strength: float        # 0~1 (1이 가장 강한 신호)
    score_adj: int         # 스코어 보정값 (-15 ~ +20)
    reasons: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class ContrarianDashboard:
    """역발상 시그널 대시보드."""
    timestamp: str
    market_fear_level: str  # "극단공포", "공포", "중립", "탐욕", "극단탐욕"
    vix: float
    signals: list[ContrarianSignal]
    summary: str


# ── 상수 ──────────────────────────────────────────────────────────

# VIX 임계값 — risk_config 중앙화 (v12.3)
def _vix_thresholds():
    try:
        from kstock.core.risk_config import get_risk_thresholds
        v = get_risk_thresholds().vix
        return v.fear, v.normal_high, v.calm, 12.0  # extreme_fear, fear, greed, extreme_greed
    except Exception:
        return 30.0, 25.0, 15.0, 12.0

VIX_EXTREME_FEAR, VIX_FEAR, VIX_GREED, VIX_EXTREME_GREED = _vix_thresholds()

# RSI 임계값
RSI_OVERSOLD = 25
RSI_EXTREME_OVERSOLD = 20
RSI_OVERBOUGHT = 75
RSI_EXTREME_OVERBOUGHT = 80

# 거래량 배수 임계값
VOLUME_SPIKE_RATIO = 3.0     # 평균 대비 3배
VOLUME_PANIC_RATIO = 5.0     # 패닉셀링 기준

# PBR/PER 극단값
PBR_DEEP_VALUE = 0.5
PER_DEEP_VALUE = 5.0
PBR_OVERVALUED = 5.0
PER_OVERVALUED = 50.0


class ContrarianEngine:
    """역발상 시그널 엔진."""

    def __init__(self):
        self._cache: dict[str, float] = {}

    # ── 종합 역발상 분석 ─────────────────────────────────────────

    def analyze(
        self,
        ticker: str,
        name: str,
        vix: float = 20.0,
        rsi: float = 50.0,
        volume_ratio: float = 1.0,
        foreign_net_days: int = 0,
        institution_net_days: int = 0,
        retail_net_buy_krw: float = 0,
        per: float = 15.0,
        pbr: float = 1.0,
        roe: float = 10.0,
        debt_ratio: float = 80.0,
        price_change_pct: float = 0.0,
        bb_pctb: float = 0.5,
        margin_change_pct: float = 0.0,
        program_net_buy_krw: float = 0,
    ) -> list[ContrarianSignal]:
        """종목별 역발상 시그널 종합 분석.

        Args:
            ticker: 종목 코드
            name: 종목명
            vix: 현재 VIX
            rsi: 현재 RSI
            volume_ratio: 거래량/20일 평균 비율
            foreign_net_days: 외국인 연속 순매수 일수 (음수=순매도)
            institution_net_days: 기관 연속 순매수 일수
            retail_net_buy_krw: 개인 순매수 금액 (원)
            per: PER
            pbr: PBR
            roe: ROE (%)
            debt_ratio: 부채비율 (%)
            price_change_pct: 당일 등락률 (%)
            bb_pctb: 볼린저밴드 %B (0~1)
            margin_change_pct: 신용잔고 증감률 (%)
            program_net_buy_krw: 프로그램 순매수 금액

        Returns:
            ContrarianSignal 리스트
        """
        signals: list[ContrarianSignal] = []

        # 1. 공포 극대치 매수
        sig = self._check_fear_buy(
            ticker, name, vix, rsi, foreign_net_days,
            volume_ratio, bb_pctb, price_change_pct,
        )
        if sig:
            signals.append(sig)

        # 2. 탐욕 극대치 매도
        sig = self._check_greed_sell(
            ticker, name, vix, rsi, retail_net_buy_krw,
            volume_ratio, bb_pctb,
        )
        if sig:
            signals.append(sig)

        # 3. 패닉셀링 감지 (역발상 매수 기회)
        sig = self._check_panic_selling(
            ticker, name, volume_ratio, price_change_pct,
            foreign_net_days, institution_net_days,
        )
        if sig:
            signals.append(sig)

        # 4. 딥 밸류 기회
        sig = self._check_deep_value(
            ticker, name, per, pbr, roe, debt_ratio, rsi,
        )
        if sig:
            signals.append(sig)

        # 5. 신용잔고 역발상
        sig = self._check_margin_contrarian(
            ticker, name, margin_change_pct, price_change_pct,
        )
        if sig:
            signals.append(sig)

        # 6. 프로그램 매매 역발상
        sig = self._check_program_contrarian(
            ticker, name, program_net_buy_krw, price_change_pct,
        )
        if sig:
            signals.append(sig)

        return signals

    # ── 개별 시그널 체크 ─────────────────────────────────────────

    def _check_fear_buy(
        self, ticker: str, name: str,
        vix: float, rsi: float, foreign_net_days: int,
        volume_ratio: float, bb_pctb: float, price_change: float,
    ) -> ContrarianSignal | None:
        """공포 극대치 역발상 매수 시그널."""
        reasons = []
        strength = 0.0

        # VIX 공포
        if vix >= VIX_EXTREME_FEAR:
            reasons.append(f"VIX {vix:.0f} 극단공포")
            strength += 0.3
        elif vix >= VIX_FEAR:
            reasons.append(f"VIX {vix:.0f} 공포")
            strength += 0.15

        # RSI 과매도
        if rsi <= RSI_EXTREME_OVERSOLD:
            reasons.append(f"RSI {rsi:.0f} 극단과매도")
            strength += 0.25
        elif rsi <= RSI_OVERSOLD:
            reasons.append(f"RSI {rsi:.0f} 과매도")
            strength += 0.15

        # 외인 순매도 지속
        if foreign_net_days <= -5:
            reasons.append(f"외인 {abs(foreign_net_days)}일 연속 순매도")
            strength += 0.15

        # BB 하단 이탈
        if bb_pctb < 0.1:
            reasons.append(f"BB 하단 이탈 ({bb_pctb:.2f})")
            strength += 0.1

        # 급락
        if price_change <= -5:
            reasons.append(f"당일 {price_change:+.1f}% 급락")
            strength += 0.1

        # 최소 2가지 이상 조건 충족 시만 시그널
        if len(reasons) >= 2 and strength >= 0.3:
            score_adj = min(20, int(strength * 25))
            return ContrarianSignal(
                signal_type="fear_buy",
                ticker=ticker,
                name=name,
                direction="BUY",
                strength=min(1.0, strength),
                score_adj=score_adj,
                reasons=reasons,
                data={"vix": vix, "rsi": rsi, "bb_pctb": bb_pctb},
                created_at=datetime.now().isoformat(),
            )
        return None

    def _check_greed_sell(
        self, ticker: str, name: str,
        vix: float, rsi: float, retail_net_buy_krw: float,
        volume_ratio: float, bb_pctb: float,
    ) -> ContrarianSignal | None:
        """탐욕 극대치 역발상 매도 시그널."""
        reasons = []
        strength = 0.0

        # VIX 극저 (과열)
        if vix <= VIX_EXTREME_GREED:
            reasons.append(f"VIX {vix:.0f} 극단탐욕 (안일)")
            strength += 0.25
        elif vix <= VIX_GREED:
            reasons.append(f"VIX {vix:.0f} 탐욕")
            strength += 0.1

        # RSI 과매수
        if rsi >= RSI_EXTREME_OVERBOUGHT:
            reasons.append(f"RSI {rsi:.0f} 극단과매수")
            strength += 0.25
        elif rsi >= RSI_OVERBOUGHT:
            reasons.append(f"RSI {rsi:.0f} 과매수")
            strength += 0.15

        # 개인 순매수 폭증 (역신호)
        if retail_net_buy_krw > 50e8:  # 500억 이상
            reasons.append(f"개인 순매수 {retail_net_buy_krw / 1e8:.0f}억 (과열)")
            strength += 0.2

        # BB 상단 이탈
        if bb_pctb > 0.95:
            reasons.append(f"BB 상단 돌파 ({bb_pctb:.2f})")
            strength += 0.1

        if len(reasons) >= 2 and strength >= 0.3:
            score_adj = max(-15, -int(strength * 20))
            return ContrarianSignal(
                signal_type="greed_sell",
                ticker=ticker,
                name=name,
                direction="SELL",
                strength=min(1.0, strength),
                score_adj=score_adj,
                reasons=reasons,
                data={"vix": vix, "rsi": rsi},
                created_at=datetime.now().isoformat(),
            )
        return None

    def _check_panic_selling(
        self, ticker: str, name: str,
        volume_ratio: float, price_change: float,
        foreign_net_days: int, institution_net_days: int,
    ) -> ContrarianSignal | None:
        """패닉셀링 감지 → 역발상 매수 기회."""
        reasons = []
        strength = 0.0

        # 거래량 폭증 + 급락 = 패닉셀링
        if volume_ratio >= VOLUME_PANIC_RATIO and price_change <= -5:
            reasons.append(
                f"거래량 {volume_ratio:.1f}배 폭증 + {price_change:+.1f}% 급락 "
                "→ 패닉셀링"
            )
            strength += 0.4

        elif volume_ratio >= VOLUME_SPIKE_RATIO and price_change <= -3:
            reasons.append(
                f"거래량 {volume_ratio:.1f}배 급증 + {price_change:+.1f}% 하락"
            )
            strength += 0.2

        # 외인+기관 동시 이탈 → 투매
        if foreign_net_days <= -3 and institution_net_days <= -3:
            reasons.append(
                f"외인({foreign_net_days}일)+기관({institution_net_days}일) 동반 매도"
            )
            strength += 0.2

        if reasons and strength >= 0.3:
            return ContrarianSignal(
                signal_type="panic_buy",
                ticker=ticker,
                name=name,
                direction="BUY",
                strength=min(1.0, strength),
                score_adj=min(15, int(strength * 20)),
                reasons=reasons,
                data={
                    "volume_ratio": volume_ratio,
                    "price_change": price_change,
                },
                created_at=datetime.now().isoformat(),
            )
        return None

    def _check_deep_value(
        self, ticker: str, name: str,
        per: float, pbr: float, roe: float, debt_ratio: float,
        rsi: float,
    ) -> ContrarianSignal | None:
        """딥 밸류 우량주 매수 기회."""
        reasons = []
        strength = 0.0

        # 저PBR + 저PER + 양호한 재무
        is_cheap = (
            0 < pbr <= PBR_DEEP_VALUE
            and 0 < per <= PER_DEEP_VALUE * 3
        )
        is_quality = roe >= 8 and debt_ratio < 150

        if is_cheap and is_quality:
            reasons.append(
                f"PBR {pbr:.1f} / PER {per:.0f} / ROE {roe:.0f}% "
                "→ 저평가 우량주"
            )
            strength += 0.3

        if pbr <= PBR_DEEP_VALUE and rsi <= RSI_OVERSOLD:
            reasons.append(
                f"PBR {pbr:.1f} + RSI {rsi:.0f} → 극단 저평가 + 과매도"
            )
            strength += 0.25

        # 고PBR 경고
        if pbr >= PBR_OVERVALUED or per >= PER_OVERVALUED:
            return ContrarianSignal(
                signal_type="value_sell",
                ticker=ticker,
                name=name,
                direction="SELL",
                strength=0.4,
                score_adj=-10,
                reasons=[f"PBR {pbr:.1f} / PER {per:.0f} → 고평가 경고"],
                data={"per": per, "pbr": pbr},
                created_at=datetime.now().isoformat(),
            )

        if reasons and strength >= 0.25:
            return ContrarianSignal(
                signal_type="value_buy",
                ticker=ticker,
                name=name,
                direction="BUY",
                strength=min(1.0, strength),
                score_adj=min(15, int(strength * 20)),
                reasons=reasons,
                data={"per": per, "pbr": pbr, "roe": roe},
                created_at=datetime.now().isoformat(),
            )
        return None

    def _check_margin_contrarian(
        self, ticker: str, name: str,
        margin_change_pct: float, price_change: float,
    ) -> ContrarianSignal | None:
        """신용잔고 역발상 시그널."""
        if abs(margin_change_pct) < 5:
            return None

        reasons = []
        if margin_change_pct > 20:
            # 신용 급증 → 개인 레버리지 과열 → 매도 경고
            reasons.append(
                f"신용잔고 {margin_change_pct:+.0f}% 급증 → 개인 레버리지 과열"
            )
            return ContrarianSignal(
                signal_type="margin_contrarian",
                ticker=ticker,
                name=name,
                direction="SELL",
                strength=min(0.8, margin_change_pct / 40),
                score_adj=-8,
                reasons=reasons,
                data={"margin_change": margin_change_pct},
                created_at=datetime.now().isoformat(),
            )

        if margin_change_pct < -15:
            # 신용 급감 → 반대매매 일단락 → 저점 근처
            reasons.append(
                f"신용잔고 {margin_change_pct:+.0f}% 급감 "
                "→ 반대매매 일단락, 기술적 저점 가능"
            )
            return ContrarianSignal(
                signal_type="margin_contrarian",
                ticker=ticker,
                name=name,
                direction="BUY",
                strength=min(0.7, abs(margin_change_pct) / 30),
                score_adj=8,
                reasons=reasons,
                data={"margin_change": margin_change_pct},
                created_at=datetime.now().isoformat(),
            )

        return None

    def _check_program_contrarian(
        self, ticker: str, name: str,
        program_net_buy_krw: float, price_change: float,
    ) -> ContrarianSignal | None:
        """프로그램 매매 역발상 시그널."""
        if abs(program_net_buy_krw) < 10e8:  # 100억 미만 무시
            return None

        if program_net_buy_krw < -50e8 and price_change < -2:
            # 프로그램 대량 매도 + 급락 → 기술적 저점
            return ContrarianSignal(
                signal_type="program_contrarian",
                ticker=ticker,
                name=name,
                direction="BUY",
                strength=min(0.7, abs(program_net_buy_krw) / 200e8),
                score_adj=7,
                reasons=[
                    f"프로그램 순매도 {program_net_buy_krw / 1e8:.0f}억 + "
                    f"주가 {price_change:+.1f}% → 기술적 저점 가능"
                ],
                data={"program_net": program_net_buy_krw},
                created_at=datetime.now().isoformat(),
            )

        if program_net_buy_krw > 80e8 and price_change > 3:
            # 프로그램 대량 매수 + 급등 → 차익실현 임박 경고
            return ContrarianSignal(
                signal_type="program_contrarian",
                ticker=ticker,
                name=name,
                direction="SELL",
                strength=min(0.6, program_net_buy_krw / 200e8),
                score_adj=-5,
                reasons=[
                    f"프로그램 순매수 {program_net_buy_krw / 1e8:.0f}억 + "
                    f"급등 {price_change:+.1f}% → 차익실현 매물 경고"
                ],
                data={"program_net": program_net_buy_krw},
                created_at=datetime.now().isoformat(),
            )

        return None

    # ── 시장 전체 역발상 분석 ────────────────────────────────────

    def analyze_market(
        self,
        vix: float,
        fear_greed_label: str,
        kospi_change_pct: float = 0.0,
        foreign_net_total_krw: float = 0,
        margin_change_pct: float = 0.0,
    ) -> ContrarianDashboard:
        """시장 전체 역발상 시그널 분석."""
        signals: list[ContrarianSignal] = []

        # 시장 공포 기반 시그널
        if vix >= VIX_EXTREME_FEAR:
            signals.append(ContrarianSignal(
                signal_type="market_fear",
                ticker="MARKET",
                name="시장전체",
                direction="BUY",
                strength=0.8,
                score_adj=15,
                reasons=[
                    f"VIX {vix:.0f} 극단공포 → 역발상 매수 구간",
                    "과거 VIX 30+ 구간에서 3개월 후 평균 +12% 반등",
                ],
                created_at=datetime.now().isoformat(),
            ))
        elif vix <= VIX_EXTREME_GREED:
            signals.append(ContrarianSignal(
                signal_type="market_greed",
                ticker="MARKET",
                name="시장전체",
                direction="SELL",
                strength=0.6,
                score_adj=-10,
                reasons=[
                    f"VIX {vix:.0f} 극단탐욕 → 시장 과열 경고",
                    "현금 비중 확대, 신규 매수 자제 권장",
                ],
                created_at=datetime.now().isoformat(),
            ))

        # 외인 투매 → 바닥 근처
        if foreign_net_total_krw < -500e8:
            signals.append(ContrarianSignal(
                signal_type="foreign_capitulation",
                ticker="MARKET",
                name="시장전체",
                direction="BUY",
                strength=0.6,
                score_adj=10,
                reasons=[
                    f"외인 순매도 {foreign_net_total_krw / 1e8:.0f}억 → "
                    "투매 일단락 시 반등 기대"
                ],
                created_at=datetime.now().isoformat(),
            ))

        summary = self._build_summary(vix, fear_greed_label, signals)

        return ContrarianDashboard(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            market_fear_level=fear_greed_label,
            vix=vix,
            signals=signals,
            summary=summary,
        )

    def _build_summary(
        self, vix: float, fear_greed: str, signals: list[ContrarianSignal],
    ) -> str:
        """역발상 대시보드 요약 텍스트."""
        buy_signals = [s for s in signals if s.direction == "BUY"]
        sell_signals = [s for s in signals if s.direction == "SELL"]

        if buy_signals and not sell_signals:
            return f"🟢 역발상 매수 구간 (VIX {vix:.0f}, {fear_greed})"
        elif sell_signals and not buy_signals:
            return f"🔴 역발상 매도 경고 (VIX {vix:.0f}, {fear_greed})"
        elif buy_signals and sell_signals:
            return f"🟡 혼조 시그널 — 종목별 선별 필요"
        return f"⚪ 특이사항 없음 (VIX {vix:.0f})"


# ── 텔레그램 포맷 ─────────────────────────────────────────────────

def format_contrarian_dashboard(dashboard: ContrarianDashboard) -> str:
    """역발상 대시보드를 텔레그램 메시지로 포맷."""
    lines = [
        "🔮 역발상 시그널 대시보드",
        "━" * 25,
        f"⏰ {dashboard.timestamp}",
        f"📊 시장 심리: {dashboard.market_fear_level} (VIX {dashboard.vix:.0f})",
        "",
        dashboard.summary,
    ]

    if dashboard.signals:
        lines.extend(["", "📡 활성 시그널"])
        for sig in dashboard.signals[:8]:
            emoji = "🟢" if sig.direction == "BUY" else "🔴" if sig.direction == "SELL" else "🟡"
            strength_pct = int(sig.strength * 100)
            lines.append(f"  {emoji} {sig.name} [{strength_pct}%]")
            for r in sig.reasons[:2]:
                lines.append(f"    └ {r}")

    return "\n".join(lines)


def format_contrarian_alert(signal: ContrarianSignal) -> str:
    """단일 역발상 시그널 알림 포맷."""
    emoji = "🟢" if signal.direction == "BUY" else "🔴"
    strength_bar = "●" * int(signal.strength * 5) + "○" * (5 - int(signal.strength * 5))
    lines = [
        f"🔮 역발상 시그널 감지!",
        f"",
        f"{emoji} {signal.name} ({signal.ticker})",
        f"방향: {signal.direction} | 강도: [{strength_bar}]",
        "",
    ]
    for r in signal.reasons:
        lines.append(f"  └ {r}")

    return "\n".join(lines)
