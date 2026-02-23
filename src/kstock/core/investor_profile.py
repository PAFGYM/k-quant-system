"""투자자 프로필 학습 및 보유기간별 맞춤 솔루션 엔진.

Phase 9: 투자 성향 자동 학습 + 보유기간별 전략 차별화.

주요 기능:
1. 매매 이력에서 투자 성향 자동 분석 (스캘핑/스윙/포지션/장기)
2. 보유기간별 맞춤 솔루션 (단타는 즉각 대응, 장기는 펀더멘털 중심)
3. 신규 종목 추가 시 AI 분석 + 학습 제안
4. 레버리지/신용 사용 시 단기 전략 자동 적용
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

STYLE_LABELS = {
    "scalper": "단타 (1~3일)",
    "swing": "스윙 (1~2주)",
    "position": "포지션 (1~2개월)",
    "long_term": "장기투자 (2개월+)",
    "신규": "아직 데이터 부족",
    "balanced": "균형형",
}

RISK_LABELS = {
    "conservative": "보수적",
    "medium": "중립",
    "aggressive": "공격적",
}

HOLD_TYPE_CONFIG = {
    "scalp": {
        "label": "단타 (1~3일)",
        "max_days": 3,
        "profit_target": 3.0,
        "stop_loss": -2.0,
        "check_interval": "매시간",
        "focus": "호가창, 거래량, 틱차트, 수급",
        "action_style": "즉시 실행. 망설이면 놓침.",
    },
    "swing": {
        "label": "스윙 (1~2주)",
        "max_days": 14,
        "profit_target": 7.0,
        "stop_loss": -5.0,
        "check_interval": "하루 2회",
        "focus": "일봉 MACD, RSI, 이동평균선, 수급 추세",
        "action_style": "추세 확인 후 행동. 조급하게 움직이지 마세요.",
    },
    "position": {
        "label": "포지션 (1~2개월)",
        "max_days": 60,
        "profit_target": 15.0,
        "stop_loss": -8.0,
        "check_interval": "주 2회",
        "focus": "주봉 추세, 실적 발표, 섹터 모멘텀, 기관 수급",
        "action_style": "큰 흐름을 보세요. 일일 변동은 무시.",
    },
    "long_term": {
        "label": "장기투자 (2개월+)",
        "max_days": 999,
        "profit_target": 30.0,
        "stop_loss": -15.0,
        "check_interval": "월 1~2회",
        "focus": "분기 실적, 산업 구조 변화, 밸류에이션, 배당",
        "action_style": "좋은 기업은 시간이 편. 단기 노이즈 무시.",
    },
}


@dataclass
class InvestorInsight:
    """투자자 학습 결과 요약."""
    style: str = "balanced"
    style_label: str = "균형형"
    risk_tolerance: str = "medium"
    risk_label: str = "중립"
    trade_count: int = 0
    win_rate: float = 0
    avg_hold_days: float = 0
    avg_profit_pct: float = 0
    avg_loss_pct: float = 0
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


def classify_hold_type(holding: dict) -> str:
    """보유종목의 보유기간/특성으로 유형 분류.

    Args:
        holding: Dict with buy_date, leverage_flag, etc.

    Returns:
        One of: 'scalp', 'swing', 'position', 'long_term'
    """
    # 레버리지/신용 사용 → 무조건 단타
    if holding.get("leverage_flag") or holding.get("margin_used"):
        return "scalp"

    # 보유기간으로 분류
    try:
        buy_date = datetime.fromisoformat(holding["buy_date"])
        now = datetime.now(KST).replace(tzinfo=None)
        if buy_date.tzinfo:
            buy_date = buy_date.replace(tzinfo=None)
        days = (now - buy_date).days
    except (ValueError, TypeError, KeyError):
        days = 0

    if days <= 3:
        return "scalp"
    elif days <= 14:
        return "swing"
    elif days <= 60:
        return "position"
    else:
        return "long_term"


def get_holding_days(holding: dict) -> int:
    """보유일수 계산."""
    try:
        buy_date = datetime.fromisoformat(holding["buy_date"])
        now = datetime.now(KST).replace(tzinfo=None)
        if buy_date.tzinfo:
            buy_date = buy_date.replace(tzinfo=None)
        return max((now - buy_date).days, 0)
    except (ValueError, TypeError, KeyError):
        return 0


def generate_hold_solution(holding: dict, hold_type: str) -> dict:
    """보유기간별 맞춤 솔루션 생성.

    Args:
        holding: Holding dict with pnl_pct, buy_price, current_price, etc.
        hold_type: One of 'scalp', 'swing', 'position', 'long_term'

    Returns:
        Dict with action, reason, targets, urgency, etc.
    """
    config = HOLD_TYPE_CONFIG.get(hold_type, HOLD_TYPE_CONFIG["swing"])
    pnl = holding.get("pnl_pct", 0)
    buy_price = holding.get("buy_price", 0)
    days = get_holding_days(holding)
    name = holding.get("name", "")

    result = {
        "hold_type": hold_type,
        "hold_type_label": config["label"],
        "hold_days": days,
        "check_interval": config["check_interval"],
        "focus_points": config["focus"],
    }

    # 수익 구간
    if pnl >= config["profit_target"]:
        result["action"] = "익절 실행"
        result["urgency"] = "high"
        result["reason"] = (
            f"{name} 수익률 {pnl:+.1f}%로 "
            f"{hold_type} 목표({config['profit_target']}%)를 달성했습니다."
        )
        if hold_type in ("scalp", "swing"):
            result["detail"] = "전량 매도 후 다음 기회를 노리세요."
        else:
            result["detail"] = "50% 익절 후 나머지는 트레일링 스탑으로 관리하세요."

    # 손절 구간
    elif pnl <= config["stop_loss"]:
        result["action"] = "손절 검토"
        result["urgency"] = "high"
        result["reason"] = (
            f"{name} 손실 {pnl:+.1f}%로 "
            f"{hold_type} 손절선({config['stop_loss']}%)에 도달했습니다."
        )
        if hold_type in ("scalp", "swing"):
            result["detail"] = "즉시 손절하세요. 반등 기대는 금물."
        else:
            result["detail"] = "펀더멘털이 변하지 않았다면 물타기 검토. 변했다면 손절."

    # 보유기간 초과 (단타/스윙)
    elif days > config["max_days"] and hold_type in ("scalp", "swing"):
        result["action"] = "보유기간 초과"
        result["urgency"] = "medium"
        result["reason"] = (
            f"{name} 보유 {days}일, "
            f"{hold_type} 권장 기간({config['max_days']}일) 초과."
        )
        if pnl > 0:
            result["detail"] = "수익 중이니 익절하고 새 종목으로 회전하세요."
        else:
            result["detail"] = "기간 대비 수익이 없습니다. 기회비용을 고려해 정리하세요."

    # 정상 보유
    else:
        result["action"] = "홀딩"
        result["urgency"] = "low"
        if pnl > 0:
            result["reason"] = f"{name} 수익 {pnl:+.1f}%, 목표가까지 홀딩."
        else:
            result["reason"] = f"{name} 손실 {pnl:+.1f}%, 아직 손절선 미도달."
        result["detail"] = config["action_style"]

    # 목표가/손절가 계산
    if buy_price > 0:
        result["target_price"] = round(buy_price * (1 + config["profit_target"] / 100))
        result["stop_price"] = round(buy_price * (1 + config["stop_loss"] / 100))

    return result


def analyze_investor_style(db) -> InvestorInsight:
    """매매 이력에서 투자 성향 분석.

    Args:
        db: SQLiteStore instance.

    Returns:
        InvestorInsight with style, strengths, weaknesses, suggestions.
    """
    stats = db.compute_investor_stats()
    insight = InvestorInsight(
        style=stats["style"],
        style_label=STYLE_LABELS.get(stats["style"], stats["style"]),
        risk_tolerance=stats["risk_tolerance"],
        risk_label=RISK_LABELS.get(stats["risk_tolerance"], stats["risk_tolerance"]),
        trade_count=stats["trade_count"],
        win_rate=stats["win_rate"],
        avg_hold_days=stats["avg_hold_days"],
        avg_profit_pct=stats["avg_profit_pct"],
        avg_loss_pct=stats["avg_loss_pct"],
    )

    # 강점 분석
    if insight.win_rate > 60:
        insight.strengths.append(f"높은 승률 ({insight.win_rate}%)")
    if insight.avg_profit_pct > insight.avg_loss_pct * 1.5:
        insight.strengths.append("손익비가 좋음 (이익 > 손실)")
    if insight.avg_hold_days > 30:
        insight.strengths.append("인내심 있는 투자")

    # 약점 분석 (거래 5건 이상부터 의미 있음)
    if insight.trade_count >= 5:
        if insight.win_rate < 40:
            insight.weaknesses.append(f"승률이 낮음 ({insight.win_rate}%)")
        if insight.avg_loss_pct > insight.avg_profit_pct:
            insight.weaknesses.append("평균 손실이 평균 이익보다 큼")
        if insight.avg_hold_days < 3 and insight.win_rate < 50:
            insight.weaknesses.append("너무 빠른 매매로 수익 기회 놓침")

    # 제안
    if insight.style == "scalper":
        insight.suggestions.append("거래량/호가창 분석 역량을 키우세요")
        insight.suggestions.append("하루 최대 거래 횟수를 정하세요")
    elif insight.style == "swing":
        insight.suggestions.append("MACD/RSI 골든크로스에서 진입하세요")
        insight.suggestions.append("손절 라인을 미리 정하고 지키세요")
    elif insight.style in ("position", "long_term"):
        insight.suggestions.append("분기 실적을 체크 포인트로 활용하세요")
        insight.suggestions.append("섹터 로테이션을 관찰하세요")

    if insight.avg_loss_pct > 10:
        insight.suggestions.append("손절 라인을 더 타이트하게 설정하세요")

    return insight


def format_investor_profile(insight: InvestorInsight) -> str:
    """투자자 프로필을 텔레그램 형식으로 포맷."""
    lines = [
        "📊 투자자 프로필 분석",
        "─" * 20,
        "",
        f"투자 스타일: {insight.style_label}",
        f"리스크 성향: {insight.risk_label}",
        f"총 거래: {insight.trade_count}건",
        f"승률: {insight.win_rate:.1f}%",
        f"평균 보유: {insight.avg_hold_days:.0f}일",
        f"평균 수익: {insight.avg_profit_pct:+.1f}%",
        f"평균 손실: {insight.avg_loss_pct:-.1f}%",
    ]

    if insight.strengths:
        lines.extend(["", "💪 강점:"])
        for s in insight.strengths:
            lines.append(f"  ✅ {s}")

    if insight.weaknesses:
        lines.extend(["", "⚠️ 개선점:"])
        for w in insight.weaknesses:
            lines.append(f"  🔸 {w}")

    if insight.suggestions:
        lines.extend(["", "💡 제안:"])
        for s in insight.suggestions:
            lines.append(f"  → {s}")

    return "\n".join(lines)


def generate_new_holding_analysis(holding: dict, macro_context: str = "") -> str:
    """신규 종목 추가 시 AI에게 보낼 분석 요청 프롬프트 생성.

    Returns:
        AI에게 전달할 분석 요청 프롬프트.
    """
    name = holding.get("name", "")
    ticker = holding.get("ticker", "")
    buy_price = holding.get("buy_price", 0)
    hold_type = classify_hold_type(holding)
    config = HOLD_TYPE_CONFIG.get(hold_type, HOLD_TYPE_CONFIG["swing"])

    return (
        f"주호님이 새로 {name}({ticker})을 매수가 {buy_price:,.0f}원에 편입했습니다.\n"
        f"보유 유형: {config['label']}\n"
        f"목표 수익: {config['profit_target']}% / 손절: {config['stop_loss']}%\n"
        f"점검 주기: {config['check_interval']}\n"
        f"집중 분석: {config['focus']}\n\n"
        f"현재 시장 상황:\n{macro_context}\n\n"
        f"다음을 분석해주세요:\n"
        f"1. {name}의 현재 기술적 위치 (RSI, MACD, 지지/저항)\n"
        f"2. 매수 타이밍 적절성 판단\n"
        f"3. {hold_type} 전략에 맞는 구체적 목표가/손절가\n"
        f"4. 같은 섹터/밸류체인의 주요 리스크\n"
        f"5. 앞으로 {config['max_days']}일 내 주요 이벤트/체크포인트"
    )


def build_holdings_context_with_solutions(db) -> str:
    """모든 보유종목에 대해 보유기간별 솔루션을 포함한 컨텍스트 생성.

    Returns:
        AI 시스템 프롬프트에 주입할 보유종목 + 솔루션 텍스트.
    """
    holdings = db.get_active_holdings()
    if not holdings:
        return "보유 종목 없음"

    lines: list[str] = []
    for h in holdings:
        hold_type = classify_hold_type(h)
        sol = generate_hold_solution(h, hold_type)
        days = get_holding_days(h)
        pnl = h.get("pnl_pct", 0)

        lines.append(
            f"- {h.get('name', '')} ({h.get('ticker', '')}): "
            f"매수 {h.get('buy_price', 0):,.0f}원, "
            f"현재 {h.get('current_price', 0):,.0f}원, "
            f"{pnl:+.1f}%, {days}일 보유"
        )
        lines.append(
            f"  유형: {sol['hold_type_label']} | "
            f"판단: {sol['action']} ({sol['urgency']}) | "
            f"근거: {sol['reason']}"
        )
        if sol.get("target_price"):
            lines.append(
                f"  목표가: {sol['target_price']:,}원 | "
                f"손절가: {sol['stop_price']:,}원"
            )

    return "\n".join(lines)
