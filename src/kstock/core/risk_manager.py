"""Portfolio risk management with hard limits (core/risk_manager.py) — v5.1.

Monitors MDD, concentration, sector weight, correlation, margin ratio.
Generates violations and recommended actions.

v5.1: RiskPolicy 단일 소스 참조 — 임계치 중복 해소.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)
USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# Risk limit defaults — v5.1: RiskPolicy에서 읽되 하위호환 유지
# ---------------------------------------------------------------------------
def _get_risk_limits() -> dict:
    """RiskPolicy 단일 소스에서 한도를 읽는다 (v5.1)."""
    try:
        from kstock.core.risk_policy import get_risk_policy
        return get_risk_policy().to_risk_limits_dict()
    except Exception:
        pass
    # 폴백: 하드코딩 (RiskPolicy 모듈 없을 때)
    return {
        "max_portfolio_mdd": -0.15,
        "emergency_mdd": -0.20,
        "max_daily_loss": -0.05,
        "max_single_stock_weight": 0.40,
        "max_sector_weight": 0.60,
        "max_correlation": 0.85,
        "max_margin_ratio": 0.20,
        "max_single_margin": 0.30,
    }


# 하위호환 유지: 기존 코드에서 RISK_LIMITS를 직접 참조하는 경우
RISK_LIMITS = _get_risk_limits()

# ---------------------------------------------------------------------------
# Sector mapping for common Korean tickers
# ---------------------------------------------------------------------------
SECTOR_MAP: dict[str, str] = {
    "005930": "반도체", "000660": "반도체",
    "373220": "2차전지", "006400": "2차전지", "247540": "2차전지", "086520": "2차전지",
    "035420": "소프트웨어", "035720": "소프트웨어",
    "207940": "바이오", "068270": "바이오",
    "005380": "자동차", "000270": "자동차",
    "055550": "금융", "105560": "금융", "316140": "금융",
    "005490": "철강", "051910": "화학",
    "017670": "통신", "030200": "통신",
    "352820": "엔터", "009540": "조선", "012450": "방산",
}

# v5.2: 레거시 보유종목 — 퀀트 시스템 이전부터 보유, 리스크 경고/비중 제한에서 완전 제외
LEGACY_EXEMPT_TICKERS: set[str] = {
    "086520",  # 에코프로
    "247540",  # 에코프로비엠
    "005380",  # 현대차
}

# Ticker -> human-readable name for suggestion messages
TICKER_NAME: dict[str, str] = {
    "005930": "삼성전자", "000660": "SK하이닉스",
    "373220": "LG에너지솔루션", "006400": "삼성SDI",
    "247540": "에코프로비엠", "086520": "에코프로",
    "035420": "NAVER", "035720": "카카오",
    "207940": "삼성바이오로직스", "068270": "셀트리온",
    "005380": "현대차", "000270": "기아",
    "055550": "신한지주", "105560": "KB금융", "316140": "우리금융지주",
    "005490": "POSCO홀딩스", "051910": "LG화학",
    "017670": "SK텔레콤", "030200": "KT",
    "352820": "하이브", "009540": "한국조선해양", "012450": "한화에어로스페이스",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class RiskViolation:
    """Single risk limit violation."""

    violation_type: str  # MDD_BREACH, EMERGENCY_MDD, DAILY_LOSS, CONCENTRATION, SECTOR, CORRELATION, MARGIN
    severity: str  # critical, high, medium
    description: str
    recommended_action: str
    details: dict = field(default_factory=dict)


@dataclass
class RiskReport:
    """Full risk check report."""

    date: str = ""
    total_value: float = 0.0
    peak_value: float = 0.0
    current_mdd: float = 0.0
    daily_pnl_pct: float = 0.0
    violations: list[RiskViolation] = field(default_factory=list)
    is_buy_blocked: bool = False
    stock_weights: dict[str, float] = field(default_factory=dict)
    sector_weights: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. MDD calculation
# ---------------------------------------------------------------------------
def calculate_mdd(current_value: float, peak_value: float) -> float:
    """현재 가치와 고점 대비 MDD를 음수 비율로 반환한다.

    Args:
        current_value: 현재 포트폴리오 평가 금액.
        peak_value: 최고점 평가 금액.

    Returns:
        음수 비율 (예: -0.12 = -12%). 고점 이하일 때만 음수.
        peak_value가 0 이하이면 0.0 반환.
    """
    try:
        if peak_value <= 0:
            return 0.0
        if current_value >= peak_value:
            return 0.0
        mdd = (current_value - peak_value) / peak_value
        return round(mdd, 6)
    except Exception:
        logger.exception("MDD 계산 중 오류 발생")
        return 0.0


# ---------------------------------------------------------------------------
# 2. Stock weights
# ---------------------------------------------------------------------------
def calculate_stock_weights(holdings: list[dict]) -> dict[str, float]:
    """보유 종목별 비중을 계산한다.

    Args:
        holdings: 종목 딕셔너리 리스트.
            각 항목에 ticker, name, eval_amount 키 필요.

    Returns:
        ticker -> 비중(0~1 사이 비율) 딕셔너리.
    """
    try:
        if not holdings:
            return {}
        total = sum(h.get("eval_amount", 0) for h in holdings)
        if total <= 0:
            return {}
        weights: dict[str, float] = {}
        for h in holdings:
            ticker = h.get("ticker", "")
            amount = h.get("eval_amount", 0)
            if ticker and amount > 0:
                weights[ticker] = round(amount / total, 6)
        return weights
    except Exception:
        logger.exception("종목 비중 계산 중 오류 발생")
        return {}


# ---------------------------------------------------------------------------
# 3. Sector weights
# ---------------------------------------------------------------------------
def calculate_sector_weights(holdings: list[dict]) -> dict[str, float]:
    """섹터별 비중을 계산한다.

    Args:
        holdings: 종목 딕셔너리 리스트.
            각 항목에 ticker, eval_amount 키 필요.

    Returns:
        sector_name -> 비중(0~1 사이 비율) 딕셔너리.
    """
    try:
        if not holdings:
            return {}
        total = sum(h.get("eval_amount", 0) for h in holdings)
        if total <= 0:
            return {}
        sector_amounts: dict[str, float] = {}
        for h in holdings:
            ticker = h.get("ticker", "")
            sector = SECTOR_MAP.get(ticker, "기타")
            sector_amounts[sector] = sector_amounts.get(sector, 0) + h.get("eval_amount", 0)
        sector_weights: dict[str, float] = {}
        for sector, amount in sector_amounts.items():
            sector_weights[sector] = round(amount / total, 6)
        return sector_weights
    except Exception:
        logger.exception("섹터 비중 계산 중 오류 발생")
        return {}


# ---------------------------------------------------------------------------
# 4. Correlation proxy
# ---------------------------------------------------------------------------
def calculate_correlation_proxy(
    holdings: list[dict],
) -> list[tuple[str, str, float]]:
    """같은 섹터 종목 쌍은 0.9, 다른 섹터 종목 쌍은 0.3으로 상관관계를 추정한다.

    실시간 상관관계 계산이 어려운 환경에서 섹터 기반 프록시를 사용한다.

    Args:
        holdings: 종목 딕셔너리 리스트.
            각 항목에 ticker 키 필요.

    Returns:
        (ticker_a, ticker_b, correlation) 튜플 리스트.
        각 쌍은 한 번만 등장한다 (A-B가 있으면 B-A는 없음).
    """
    try:
        if not holdings or len(holdings) < 2:
            return []
        tickers = [h.get("ticker", "") for h in holdings if h.get("ticker")]
        pairs: list[tuple[str, str, float]] = []
        for i in range(len(tickers)):
            sector_i = SECTOR_MAP.get(tickers[i], "기타")
            for j in range(i + 1, len(tickers)):
                sector_j = SECTOR_MAP.get(tickers[j], "기타")
                if sector_i == sector_j and sector_i != "기타":
                    corr = 0.9
                else:
                    corr = 0.3
                pairs.append((tickers[i], tickers[j], corr))
        return pairs
    except Exception:
        logger.exception("상관관계 프록시 계산 중 오류 발생")
        return []


# ---------------------------------------------------------------------------
# 5. Main risk check
# ---------------------------------------------------------------------------
def check_risk_limits(
    holdings: list[dict],
    total_value: float,
    peak_value: float,
    daily_pnl_pct: float,
    cash: float = 0,
    limits: dict | None = None,
) -> RiskReport:
    """모든 리스크 한도를 점검하고 위반 사항을 보고한다.

    Args:
        holdings: 종목 딕셔너리 리스트.
            각 항목에 ticker, name, eval_amount 키 필요.
            신용 매수 종목은 margin_amount 키 추가 가능.
        total_value: 포트폴리오 총 평가금액 (현금 포함).
        peak_value: 포트폴리오 역대 최고 평가금액.
        daily_pnl_pct: 금일 손익률 (음수 = 손실, 예: -0.03 = -3%).
        cash: 현금 잔고.
        limits: 사용자 정의 한도. None이면 RISK_LIMITS 기본값 사용.

    Returns:
        RiskReport with violations and metrics.
    """
    try:
        lim = limits if limits is not None else RISK_LIMITS
        report = RiskReport(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_value=total_value,
            peak_value=peak_value,
        )

        # --- MDD check ---
        mdd = calculate_mdd(total_value, peak_value)
        report.current_mdd = mdd

        emergency_threshold = lim.get("emergency_mdd", -0.20)
        normal_threshold = lim.get("max_portfolio_mdd", -0.15)

        if mdd <= emergency_threshold:
            report.violations.append(RiskViolation(
                violation_type="EMERGENCY_MDD",
                severity="critical",
                description=(
                    f"{USER_NAME}, 포트폴리오 MDD {mdd * 100:.1f}% 도달. "
                    f"비상 한도 {emergency_threshold * 100:.0f}% 초과입니다."
                ),
                recommended_action="전 종목 즉시 점검 필요. 손실 종목 우선 정리 권장.",
                details={"mdd": mdd, "threshold": emergency_threshold},
            ))
            report.is_buy_blocked = True
        elif mdd <= normal_threshold:
            report.violations.append(RiskViolation(
                violation_type="MDD_BREACH",
                severity="high",
                description=(
                    f"{USER_NAME}, 포트폴리오 MDD {mdd * 100:.1f}% 도달. "
                    f"한도 {normal_threshold * 100:.0f}% 초과입니다."
                ),
                recommended_action="신규 매수 중단, 손실 큰 종목부터 비중 축소 권장.",
                details={"mdd": mdd, "threshold": normal_threshold},
            ))
            report.is_buy_blocked = True

        # --- Daily loss check ---
        report.daily_pnl_pct = daily_pnl_pct
        max_daily = lim.get("max_daily_loss", -0.05)
        if daily_pnl_pct <= max_daily:
            report.violations.append(RiskViolation(
                violation_type="DAILY_LOSS",
                severity="high",
                description=(
                    f"{USER_NAME}, 금일 손실 {daily_pnl_pct * 100:.1f}%로 "
                    f"일일 한도 {max_daily * 100:.0f}% 초과입니다."
                ),
                recommended_action="추가 매매 자제, 장 마감까지 관망 권장.",
                details={"daily_pnl_pct": daily_pnl_pct, "threshold": max_daily},
            ))
            report.is_buy_blocked = True

        # --- Stock weight (concentration) check ---
        # v5.2: 레거시 종목 제외한 비중 계산
        stock_weights = calculate_stock_weights(holdings)
        report.stock_weights = stock_weights
        max_stock_w = lim.get("max_single_stock_weight", 0.40)

        for ticker, weight in stock_weights.items():
            if ticker in LEGACY_EXEMPT_TICKERS:
                continue  # v5.2: 레거시 종목 비중 위반 무시
            if weight > max_stock_w:
                name = _resolve_name(ticker, holdings)
                report.violations.append(RiskViolation(
                    violation_type="CONCENTRATION",
                    severity="high",
                    description=(
                        f"{name} 비중 {weight * 100:.1f}%로 "
                        f"단일 종목 한도 {max_stock_w * 100:.0f}% 초과입니다."
                    ),
                    recommended_action=f"{name} 비중을 {max_stock_w * 100:.0f}% 이하로 축소 권장.",
                    details={"ticker": ticker, "name": name, "weight": weight, "threshold": max_stock_w},
                ))

        # --- Sector weight check ---
        # v5.2: 레거시 종목을 제외한 섹터 비중 계산
        non_legacy_holdings = [h for h in holdings if h.get("ticker", "") not in LEGACY_EXEMPT_TICKERS]
        sector_weights = calculate_sector_weights(holdings)  # 전체 비중은 리포트용
        report.sector_weights = sector_weights
        sector_weights_filtered = calculate_sector_weights(non_legacy_holdings)  # 위반 검사용
        max_sector_w = lim.get("max_sector_weight", 0.60)

        for sector, weight in sector_weights_filtered.items():
            if weight > max_sector_w:
                report.violations.append(RiskViolation(
                    violation_type="SECTOR",
                    severity="medium",
                    description=(
                        f"{sector} 섹터 비중 {weight * 100:.1f}%로 "
                        f"섹터 한도 {max_sector_w * 100:.0f}% 초과입니다."
                    ),
                    recommended_action=f"{sector} 섹터 내 종목 일부 매도, 타 섹터 분산 권장.",
                    details={"sector": sector, "weight": weight, "threshold": max_sector_w},
                ))

        # --- Correlation check ---
        max_corr = lim.get("max_correlation", 0.85)
        pairs = calculate_correlation_proxy(holdings)
        for ticker_a, ticker_b, corr in pairs:
            # v5.2: 레거시 종목 쌍은 상관관계 경고 제외
            if ticker_a in LEGACY_EXEMPT_TICKERS or ticker_b in LEGACY_EXEMPT_TICKERS:
                continue
            if corr > max_corr:
                name_a = _resolve_name(ticker_a, holdings)
                name_b = _resolve_name(ticker_b, holdings)
                report.violations.append(RiskViolation(
                    violation_type="CORRELATION",
                    severity="medium",
                    description=(
                        f"{name_a}과 {name_b} 상관관계 {corr:.2f}로 "
                        f"한도 {max_corr:.2f} 초과입니다."
                    ),
                    recommended_action=f"두 종목 중 하나의 비중 축소 권장.",
                    details={
                        "ticker_a": ticker_a, "ticker_b": ticker_b,
                        "name_a": name_a, "name_b": name_b,
                        "correlation": corr, "threshold": max_corr,
                    },
                ))

        # --- Margin ratio check ---
        max_margin = lim.get("max_margin_ratio", 0.20)
        max_single_margin = lim.get("max_single_margin", 0.30)
        total_margin = 0.0
        for h in holdings:
            margin_amt = h.get("margin_amount", 0)
            total_margin += margin_amt

            # Per-stock margin check
            eval_amt = h.get("eval_amount", 0)
            if eval_amt > 0 and margin_amt > 0:
                single_ratio = margin_amt / eval_amt
                if single_ratio > max_single_margin:
                    name = h.get("name", h.get("ticker", ""))
                    report.violations.append(RiskViolation(
                        violation_type="MARGIN",
                        severity="high",
                        description=(
                            f"{name} 신용 비율 {single_ratio * 100:.1f}%로 "
                            f"종목별 신용 한도 {max_single_margin * 100:.0f}% 초과입니다."
                        ),
                        recommended_action=f"{name} 신용 매수분 상환 우선 권장.",
                        details={
                            "ticker": h.get("ticker", ""),
                            "name": name,
                            "margin_ratio": single_ratio,
                            "threshold": max_single_margin,
                        },
                    ))

        # Portfolio-level margin check
        if total_value > 0 and total_margin > 0:
            portfolio_margin = total_margin / total_value
            if portfolio_margin > max_margin:
                report.violations.append(RiskViolation(
                    violation_type="MARGIN",
                    severity="critical",
                    description=(
                        f"{USER_NAME}, 포트폴리오 전체 신용 비율 {portfolio_margin * 100:.1f}%로 "
                        f"한도 {max_margin * 100:.0f}% 초과입니다."
                    ),
                    recommended_action="신용 잔고 즉시 축소 필요. 반대매매 위험 주의.",
                    details={"margin_ratio": portfolio_margin, "threshold": max_margin},
                ))
                report.is_buy_blocked = True

        logger.info(
            "리스크 점검 완료: MDD=%.2f%%, 위반=%d건, 매수차단=%s",
            mdd * 100, len(report.violations), report.is_buy_blocked,
        )
        return report

    except Exception:
        logger.exception("리스크 한도 점검 중 오류 발생")
        return RiskReport(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_value=total_value,
            peak_value=peak_value,
        )


# ---------------------------------------------------------------------------
# 6. Rebalance suggestions
# ---------------------------------------------------------------------------
def generate_rebalance_suggestions(report: RiskReport) -> list[dict]:
    """위반 사항별로 구체적인 리밸런싱 제안을 생성한다.

    Args:
        report: check_risk_limits에서 생성된 RiskReport.

    Returns:
        제안 딕셔너리 리스트. 각 항목은:
            action (str): "reduce" / "rebalance" / "close_margin" / "hold"
            ticker (str): 대상 종목 코드 (해당 시)
            name (str): 대상 종목명
            description (str): 구체적 설명
            priority (int): 1(최우선) ~ 3(참고)
    """
    try:
        suggestions: list[dict] = []

        for v in report.violations:
            if v.violation_type == "EMERGENCY_MDD":
                suggestions.append({
                    "action": "reduce",
                    "ticker": "",
                    "name": "전체 포트폴리오",
                    "description": f"{USER_NAME}, MDD {report.current_mdd * 100:.1f}% 비상. 손실 상위 종목부터 30% 이상 비중 축소 권장.",
                    "priority": 1,
                })

            elif v.violation_type == "MDD_BREACH":
                suggestions.append({
                    "action": "hold",
                    "ticker": "",
                    "name": "전체 포트폴리오",
                    "description": f"신규 매수 중단. 기존 포지션 유지하되, 추가 하락 시 손절 기준 재설정 필요.",
                    "priority": 1,
                })

            elif v.violation_type == "DAILY_LOSS":
                suggestions.append({
                    "action": "hold",
                    "ticker": "",
                    "name": "전체 포트폴리오",
                    "description": f"금일 손실 {report.daily_pnl_pct * 100:.1f}%. 장 마감까지 추가 매매 자제 권장.",
                    "priority": 1,
                })

            elif v.violation_type == "CONCENTRATION":
                ticker = v.details.get("ticker", "")
                name = v.details.get("name", "")
                weight = v.details.get("weight", 0)
                threshold = v.details.get("threshold", 0.40)
                excess_pct = (weight - threshold) * 100
                suggestions.append({
                    "action": "reduce",
                    "ticker": ticker,
                    "name": name,
                    "description": f"{name} {excess_pct:.0f}% 축소 필요 (현재 {weight * 100:.1f}% -> 목표 {threshold * 100:.0f}% 이하).",
                    "priority": 2,
                })

            elif v.violation_type == "SECTOR":
                sector = v.details.get("sector", "")
                weight = v.details.get("weight", 0)
                threshold = v.details.get("threshold", 0.60)
                excess_pct = (weight - threshold) * 100
                # 해당 섹터에서 가장 비중 큰 종목 찾기
                top_ticker = ""
                top_name = ""
                top_weight = 0.0
                for t, w in report.stock_weights.items():
                    if SECTOR_MAP.get(t, "기타") == sector and w > top_weight:
                        top_weight = w
                        top_ticker = t
                        top_name = TICKER_NAME.get(t, t)
                if top_ticker:
                    suggestions.append({
                        "action": "reduce",
                        "ticker": top_ticker,
                        "name": top_name,
                        "description": f"{sector} 섹터 {excess_pct:.0f}% 초과. {top_name} 비중 축소 우선 권장.",
                        "priority": 2,
                    })
                else:
                    suggestions.append({
                        "action": "rebalance",
                        "ticker": "",
                        "name": sector,
                        "description": f"{sector} 섹터 비중 {weight * 100:.1f}% -> {threshold * 100:.0f}% 이하로 분산 필요.",
                        "priority": 2,
                    })

            elif v.violation_type == "CORRELATION":
                name_a = v.details.get("name_a", "")
                name_b = v.details.get("name_b", "")
                ticker_a = v.details.get("ticker_a", "")
                ticker_b = v.details.get("ticker_b", "")
                # 비중이 작은 쪽 축소 제안
                w_a = report.stock_weights.get(ticker_a, 0)
                w_b = report.stock_weights.get(ticker_b, 0)
                if w_a <= w_b:
                    target_ticker, target_name = ticker_a, name_a
                else:
                    target_ticker, target_name = ticker_b, name_b
                suggestions.append({
                    "action": "reduce",
                    "ticker": target_ticker,
                    "name": target_name,
                    "description": f"{name_a}/{name_b} 동일 섹터 고상관. {target_name} 3% 축소 권장.",
                    "priority": 3,
                })

            elif v.violation_type == "MARGIN":
                ticker = v.details.get("ticker", "")
                name = v.details.get("name", "")
                if ticker:
                    suggestions.append({
                        "action": "close_margin",
                        "ticker": ticker,
                        "name": name,
                        "description": f"{name} 신용 매수분 상환 우선. 반대매매 리스크 관리.",
                        "priority": 1,
                    })
                else:
                    suggestions.append({
                        "action": "close_margin",
                        "ticker": "",
                        "name": "전체 포트폴리오",
                        "description": "포트폴리오 전체 신용 비율 초과. 신용 잔고 즉시 축소 필요.",
                        "priority": 1,
                    })

        # 우선순위로 정렬
        suggestions.sort(key=lambda s: s.get("priority", 9))
        return suggestions

    except Exception:
        logger.exception("리밸런싱 제안 생성 중 오류 발생")
        return []


# ---------------------------------------------------------------------------
# 7. Telegram report format
# ---------------------------------------------------------------------------
def format_risk_report(report: RiskReport) -> str:
    """RiskReport를 텔레그램용 메시지로 포맷팅한다.

    한국어, 볼드(**) 미사용, 이모지 최소 활용.

    Args:
        report: check_risk_limits 결과.

    Returns:
        포맷팅된 문자열.
    """
    try:
        lines: list[str] = []

        # 헤더
        if report.violations:
            critical = any(v.severity == "critical" for v in report.violations)
            if critical:
                header_icon = "\U0001f6a8"  # 경광등
            else:
                header_icon = "\u26a0\ufe0f"  # 경고
        else:
            header_icon = "\u2705"  # 체크
        lines.append(f"{header_icon} 포트폴리오 리스크 리포트 ({report.date})")
        lines.append("")

        # 핵심 지표
        lines.append(f"평가금액: {_format_krw(report.total_value)}")
        lines.append(f"고점 대비 MDD: {report.current_mdd * 100:.1f}%")
        lines.append(f"금일 손익: {report.daily_pnl_pct * 100:+.1f}%")
        if report.is_buy_blocked:
            lines.append("\U0001f6d1 신규 매수 차단 상태")
        lines.append("")

        # 종목 비중
        if report.stock_weights:
            lines.append("\U0001f4ca 종목 비중")
            sorted_weights = sorted(
                report.stock_weights.items(), key=lambda x: x[1], reverse=True,
            )
            for ticker, weight in sorted_weights:
                name = TICKER_NAME.get(ticker, ticker)
                bar = _bar_chart(weight * 100)
                lines.append(f"  {name} {bar} {weight * 100:.1f}%")
            lines.append("")

        # 섹터 비중
        if report.sector_weights:
            lines.append("\U0001f4c1 섹터 비중")
            sorted_sectors = sorted(
                report.sector_weights.items(), key=lambda x: x[1], reverse=True,
            )
            for sector, weight in sorted_sectors:
                bar = _bar_chart(weight * 100)
                lines.append(f"  {sector} {bar} {weight * 100:.1f}%")
            lines.append("")

        # 위반 사항
        if report.violations:
            lines.append(f"\u26a0\ufe0f 위반 사항 ({len(report.violations)}건)")
            for i, v in enumerate(report.violations, 1):
                severity_icon = _severity_icon(v.severity)
                lines.append(f"  {i}. {severity_icon} [{v.violation_type}] {v.description}")
                lines.append(f"     -> {v.recommended_action}")
            lines.append("")
        else:
            lines.append("\u2705 리스크 위반 사항 없음")
            lines.append("")

        lines.append(f"{USER_NAME}, 안전한 투자 되세요.")
        return "\n".join(lines)

    except Exception:
        logger.exception("리스크 리포트 포맷팅 중 오류 발생")
        return f"{USER_NAME}, 리스크 리포트 생성 중 오류가 발생했습니다."


# ---------------------------------------------------------------------------
# 8. Urgent alert format
# ---------------------------------------------------------------------------
def format_risk_alert(violations: list[RiskViolation]) -> str:
    """긴급 리스크 경고 메시지를 포맷팅한다.

    critical/high 심각도 위반만 포함. 즉시 대응이 필요한 상황용.

    Args:
        violations: RiskViolation 리스트.

    Returns:
        긴급 경고 포맷 문자열. 위반이 없으면 빈 문자열.
    """
    try:
        urgent = [v for v in violations if v.severity in ("critical", "high")]
        if not urgent:
            return ""

        lines: list[str] = []
        lines.append(f"\U0001f6a8 긴급 리스크 경고 \U0001f6a8")
        lines.append("")
        lines.append(f"{USER_NAME}, 즉시 확인이 필요한 리스크가 감지되었습니다.")
        lines.append("")

        for i, v in enumerate(urgent, 1):
            severity_icon = _severity_icon(v.severity)
            lines.append(f"{i}. {severity_icon} {v.description}")
            lines.append(f"   조치: {v.recommended_action}")
            lines.append("")

        critical_count = sum(1 for v in urgent if v.severity == "critical")
        high_count = sum(1 for v in urgent if v.severity == "high")

        summary_parts: list[str] = []
        if critical_count:
            summary_parts.append(f"심각 {critical_count}건")
        if high_count:
            summary_parts.append(f"주의 {high_count}건")
        lines.append(f"합계: {', '.join(summary_parts)}")
        lines.append("")
        lines.append(f"{USER_NAME}, 포지션 점검 후 조치 부탁드립니다.")

        return "\n".join(lines)

    except Exception:
        logger.exception("긴급 경고 포맷팅 중 오류 발생")
        return f"\U0001f6a8 {USER_NAME}, 리스크 경고 생성 중 오류 발생. 포트폴리오를 직접 확인해 주세요."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _resolve_name(ticker: str, holdings: list[dict]) -> str:
    """종목 코드에서 이름을 찾는다. holdings에서 먼저 찾고, 없으면 TICKER_NAME 사용."""
    for h in holdings:
        if h.get("ticker") == ticker:
            name = h.get("name", "")
            if name:
                return name
    return TICKER_NAME.get(ticker, ticker)


def _format_krw(amount: float) -> str:
    """KRW 금액을 읽기 좋은 형태로 포맷한다."""
    try:
        amt = int(amount)
        if amt >= 100_000_000:
            return f"{amt / 100_000_000:.1f}억원"
        if amt >= 10_000:
            return f"{amt / 10_000:,.0f}만원"
        return f"{amt:,}원"
    except Exception:
        return f"{amount:,.0f}원"


def _bar_chart(pct: float, width: int = 10) -> str:
    """간단한 텍스트 막대 차트를 생성한다."""
    filled = int(pct / 100 * width)
    filled = max(0, min(width, filled))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _severity_icon(severity: str) -> str:
    """심각도에 따른 아이콘을 반환한다."""
    icons = {
        "critical": "\U0001f534",  # 빨간 동그라미
        "high": "\U0001f7e0",     # 주황 동그라미
        "medium": "\U0001f7e1",   # 노란 동그라미
    }
    return icons.get(severity, "\u2B55")
