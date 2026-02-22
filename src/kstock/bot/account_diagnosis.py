"""Portfolio-level account diagnosis - K-Quant v3.0.

8-item diagnostic report for account screenshots:
1. Return diagnosis (vs KOSPI)
2. Concentration diagnosis (sector/stock HHI)
3. Loss stock diagnosis (-5%, -10% thresholds)
4. Cash ratio diagnosis (vs regime recommendation)
5. Correlation diagnosis (0.8+ pairs)
6. Valuation diagnosis (PER/PBR vs sector avg)
7. Policy beneficiary diagnosis (밸류업/코스닥3000)
8. Timing diagnosis (sell signals, add-buy opportunities)

Rules:
- No ** bold, no Markdown parse_mode
- Korean responses only
- "주호님" personalized greeting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# Sector mapping for known tickers
SECTOR_MAP: dict[str, str] = {
    "005930": "반도체", "000660": "반도체",
    "373220": "2차전지", "006400": "2차전지",
    "247540": "2차전지", "086520": "2차전지",
    "035420": "소프트웨어", "035720": "소프트웨어",
    "207940": "바이오", "068270": "바이오",
    "005380": "자동차", "000270": "자동차",
    "055550": "금융", "105560": "금융", "316140": "금융",
    "005490": "철강", "051910": "화학",
    "017670": "통신", "030200": "통신",
    "352820": "엔터", "009540": "조선",
    "012450": "방산", "004170": "유통",
}

POLICY_SECTORS = {"금융", "자동차", "보험", "지주"}
KOSDAQ_BONUS_SECTORS = {"바이오", "소프트웨어", "엔터", "게임"}


@dataclass
class DiagnosisItem:
    """A single diagnosis item result."""
    name: str = ""
    grade: str = ""   # A, B, C, D
    emoji: str = ""
    summary: str = ""
    details: list[str] = field(default_factory=list)


@dataclass
class AccountDiagnosis:
    """Full 8-item account diagnosis."""
    overall_grade: str = "B"
    overall_score: int = 0
    items: list[DiagnosisItem] = field(default_factory=list)
    solutions: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------

_GRADE_EMOJI = {"A": "\U0001f7e2", "B": "\U0001f7e1", "C": "\U0001f7e0", "D": "\U0001f534"}


def _grade_to_score(grade: str) -> int:
    return {"A": 4, "B": 3, "C": 2, "D": 1}.get(grade, 2)


def _score_to_grade(score: float) -> str:
    if score >= 3.5:
        return "A"
    if score >= 2.5:
        return "B"
    if score >= 1.5:
        return "C"
    return "D"


# ---------------------------------------------------------------------------
# Individual diagnosis functions
# ---------------------------------------------------------------------------

def _diagnose_returns(
    total_profit_pct: float,
    kospi_return_pct: float = 0.0,
) -> DiagnosisItem:
    """1. Return diagnosis - compare with KOSPI."""
    alpha = total_profit_pct - kospi_return_pct
    if alpha >= 5:
        grade = "A"
        summary = f"총 {total_profit_pct:+.1f}% (KOSPI {kospi_return_pct:+.1f}% 대비 +{alpha:.1f}%p 초과)"
    elif alpha >= 0:
        grade = "B"
        summary = f"총 {total_profit_pct:+.1f}% (KOSPI 수준, 알파 {alpha:+.1f}%p)"
    elif alpha >= -5:
        grade = "C"
        summary = f"총 {total_profit_pct:+.1f}% (KOSPI 대비 {alpha:+.1f}%p 부진)"
    else:
        grade = "D"
        summary = f"총 {total_profit_pct:+.1f}% (KOSPI 대비 {alpha:+.1f}%p 크게 부진)"

    return DiagnosisItem(
        name="수익률",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=summary,
    )


def _diagnose_concentration(
    holdings: list[dict[str, Any]],
) -> DiagnosisItem:
    """2. Concentration diagnosis - sector/stock HHI."""
    if not holdings:
        return DiagnosisItem(name="편중도", grade="B", emoji=_GRADE_EMOJI["B"],
                             summary="보유 종목 없음")

    total_eval = sum(h.get("eval_amount", 0) for h in holdings)
    if total_eval <= 0:
        return DiagnosisItem(name="편중도", grade="B", emoji=_GRADE_EMOJI["B"],
                             summary="평가금액 0원")

    # Stock concentration
    stock_weights = [(h.get("name", "?"), h.get("eval_amount", 0) / total_eval)
                     for h in holdings]
    max_stock = max(stock_weights, key=lambda x: x[1]) if stock_weights else ("", 0)

    # Sector concentration
    sector_totals: dict[str, float] = {}
    for h in holdings:
        ticker = h.get("ticker", "")
        sector = SECTOR_MAP.get(ticker, "기타")
        sector_totals[sector] = sector_totals.get(sector, 0) + h.get("eval_amount", 0)
    sector_weights = {s: v / total_eval for s, v in sector_totals.items()}
    max_sector = max(sector_weights.items(), key=lambda x: x[1]) if sector_weights else ("", 0)

    # HHI
    hhi = sum(w ** 2 for w in sector_weights.values())

    details = []
    grade = "A"

    if max_stock[1] >= 0.30:
        grade = "D"
        details.append(f"{max_stock[0]} 비중 {max_stock[1]:.0%} - 단일종목 30% 초과 경고")
    elif max_stock[1] >= 0.20:
        grade = max(grade, "C")

    if max_sector[1] >= 0.40:
        if grade != "D":
            grade = "C"
        details.append(f"{max_sector[0]} 섹터 {max_sector[1]:.0%} - 편중 경고")

    if hhi < 0.15:
        if grade == "A":
            grade = "A"
    elif hhi < 0.25:
        if grade == "A":
            grade = "B"
    else:
        if grade == "A":
            grade = "C"

    summary_parts = []
    if max_sector[0]:
        summary_parts.append(f"{max_sector[0]} {max_sector[1]:.0%}")
    if details:
        summary_parts.extend(details[:1])
    else:
        summary_parts.append(f"HHI {hhi:.2f} 적절")

    return DiagnosisItem(
        name="편중도",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=" | ".join(summary_parts) if summary_parts else "분산 양호",
        details=details,
    )


def _diagnose_losses(
    holdings: list[dict[str, Any]],
) -> DiagnosisItem:
    """3. Loss stock diagnosis."""
    losses = [(h.get("name", "?"), h.get("profit_pct", 0))
              for h in holdings if h.get("profit_pct", 0) < 0]
    critical = [l for l in losses if l[1] <= -10]
    warning = [l for l in losses if -10 < l[1] <= -5]

    if critical:
        grade = "D"
        summary = f"{len(critical)}종목 -10% 이하 (즉시 손절 검토)"
        details = [f"{n} {p:+.1f}%" for n, p in critical]
    elif warning:
        grade = "C"
        summary = f"{len(warning)}종목 -5% 이하 (손절 검토)"
        details = [f"{n} {p:+.1f}%" for n, p in warning]
    elif losses:
        grade = "B"
        summary = f"소폭 손실 {len(losses)}종목 (관찰 중)"
        details = []
    else:
        grade = "A"
        summary = "손실 종목 없음"
        details = []

    return DiagnosisItem(
        name="손실종목",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=summary,
        details=details,
    )


def _diagnose_cash_ratio(
    cash: float,
    total_eval: float,
    regime_cash_pct: float = 15.0,
    regime_label: str = "",
) -> DiagnosisItem:
    """4. Cash ratio diagnosis."""
    if total_eval <= 0:
        return DiagnosisItem(name="현금비중", grade="B", emoji=_GRADE_EMOJI["B"],
                             summary="평가금액 0원")

    cash_pct = (cash / (total_eval + cash)) * 100 if (total_eval + cash) > 0 else 0

    diff = cash_pct - regime_cash_pct
    if abs(diff) <= 5:
        grade = "A"
        summary = f"{cash_pct:.0f}% (적정)"
    elif cash_pct > regime_cash_pct + 10:
        grade = "C"
        summary = f"{cash_pct:.0f}% (현금 과다, 투자 기회 놓치는 중)"
    elif cash_pct < regime_cash_pct - 10:
        grade = "D"
        summary = f"{cash_pct:.0f}% (현금 부족, 리스크 과다)"
    else:
        grade = "B"
        summary = f"{cash_pct:.0f}% (권장 {regime_cash_pct:.0f}%)"

    if regime_label:
        summary += f" [{regime_label}]"

    return DiagnosisItem(
        name="현금비중",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=summary,
    )


def _diagnose_correlation(
    holdings: list[dict[str, Any]],
) -> DiagnosisItem:
    """5. Correlation diagnosis (sector-based proxy)."""
    # Group by sector
    sector_stocks: dict[str, list[str]] = {}
    for h in holdings:
        ticker = h.get("ticker", "")
        sector = SECTOR_MAP.get(ticker, "기타")
        sector_stocks.setdefault(sector, []).append(h.get("name", "?"))

    high_corr_pairs = []
    for sector, stocks in sector_stocks.items():
        if len(stocks) >= 2 and sector != "기타":
            for i in range(len(stocks)):
                for j in range(i + 1, len(stocks)):
                    high_corr_pairs.append((stocks[i], stocks[j], sector))

    if not high_corr_pairs:
        grade = "A"
        summary = "동반하락 리스크 낮음"
    elif len(high_corr_pairs) <= 1:
        grade = "B"
        pair = high_corr_pairs[0]
        summary = f"{pair[0]}-{pair[1]} ({pair[2]}) 동일 섹터"
    else:
        grade = "C"
        summary = f"{len(high_corr_pairs)}쌍 동일 섹터 - 동반하락 위험"

    details = [f"{p[0]}-{p[1]} ({p[2]})" for p in high_corr_pairs[:3]]
    return DiagnosisItem(
        name="상관관계",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=summary,
        details=details,
    )


def _diagnose_policy_beneficiary(
    holdings: list[dict[str, Any]],
) -> DiagnosisItem:
    """7. Policy beneficiary diagnosis."""
    if not holdings:
        return DiagnosisItem(name="정책수혜", grade="B", emoji=_GRADE_EMOJI["B"],
                             summary="보유 종목 없음")

    policy_count = 0
    for h in holdings:
        ticker = h.get("ticker", "")
        sector = SECTOR_MAP.get(ticker, "기타")
        if sector in POLICY_SECTORS or sector in KOSDAQ_BONUS_SECTORS:
            policy_count += 1

    ratio = policy_count / len(holdings) if holdings else 0

    if ratio >= 0.5:
        grade = "A"
        summary = f"정책 수혜 {policy_count}/{len(holdings)}종목 ({ratio:.0%})"
    elif ratio >= 0.3:
        grade = "B"
        summary = f"정책 수혜 {policy_count}/{len(holdings)}종목 ({ratio:.0%})"
    elif ratio >= 0.1:
        grade = "C"
        summary = f"정책 수혜 {policy_count}/{len(holdings)}종목 ({ratio:.0%}) - 보강 필요"
    else:
        grade = "D"
        summary = f"정책 수혜 없음 - 로테이션 검토"

    return DiagnosisItem(
        name="정책수혜",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=summary,
    )


def _diagnose_timing(
    holdings: list[dict[str, Any]],
) -> DiagnosisItem:
    """8. Timing diagnosis - sell signals and add-buy opportunities."""
    sell_candidates = []
    add_candidates = []

    for h in holdings:
        pct = h.get("profit_pct", 0)
        name = h.get("name", "?")
        if pct <= -10:
            sell_candidates.append(f"{name} {pct:+.1f}% 손절 검토")
        elif pct >= 15:
            add_candidates.append(f"{name} {pct:+.1f}% 트레일링 스탑 설정")
        elif -5 <= pct <= 0:
            add_candidates.append(f"{name} {pct:+.1f}% 추가매수 검토")

    if sell_candidates:
        grade = "C"
        summary = f"손절 대상 {len(sell_candidates)}종목"
        details = sell_candidates + add_candidates
    elif add_candidates:
        grade = "B"
        summary = f"관리 필요 {len(add_candidates)}종목"
        details = add_candidates
    else:
        grade = "A"
        summary = "특별한 조치 필요 없음"
        details = []

    return DiagnosisItem(
        name="타이밍",
        grade=grade,
        emoji=_GRADE_EMOJI[grade],
        summary=summary,
        details=details,
    )


# ---------------------------------------------------------------------------
# Main diagnosis function
# ---------------------------------------------------------------------------

def diagnose_account(
    holdings: list[dict[str, Any]],
    total_profit_pct: float = 0.0,
    cash: float = 0.0,
    total_eval: float = 0.0,
    kospi_return_pct: float = 0.0,
    regime_cash_pct: float = 15.0,
    regime_label: str = "",
) -> AccountDiagnosis:
    """Run full 8-item account diagnosis.

    Returns AccountDiagnosis with overall grade and per-item results.
    """
    items = [
        _diagnose_returns(total_profit_pct, kospi_return_pct),
        _diagnose_concentration(holdings),
        _diagnose_losses(holdings),
        _diagnose_cash_ratio(cash, total_eval, regime_cash_pct, regime_label),
        _diagnose_correlation(holdings),
        DiagnosisItem(name="밸류에이션", grade="B", emoji=_GRADE_EMOJI["B"],
                      summary="(실시간 PER/PBR 연동 후 자동 진단)"),
        _diagnose_policy_beneficiary(holdings),
        _diagnose_timing(holdings),
    ]

    # Overall grade
    scores = [_grade_to_score(item.grade) for item in items]
    avg_score = sum(scores) / len(scores) if scores else 2.0
    overall_grade = _score_to_grade(avg_score)
    overall_score = round(avg_score * 25)  # 0-100 scale

    # Generate solutions
    solutions = _generate_solutions(items, holdings, cash, total_eval)

    return AccountDiagnosis(
        overall_grade=overall_grade,
        overall_score=overall_score,
        items=items,
        solutions=solutions,
    )


def _generate_solutions(
    items: list[DiagnosisItem],
    holdings: list[dict[str, Any]],
    cash: float,
    total_eval: float,
) -> list[dict[str, Any]]:
    """Generate actionable solutions from diagnosis items."""
    solutions: list[dict[str, Any]] = []

    for item in items:
        if item.grade in ("C", "D"):
            if item.name == "편중도":
                solutions.append({
                    "type": "reduce_concentration",
                    "urgency": "high" if item.grade == "D" else "medium",
                    "description": f"편중 해소: {item.summary}",
                    "action": "비중 축소 후 분산 투자",
                })
            elif item.name == "손실종목":
                solutions.append({
                    "type": "stop_loss",
                    "urgency": "critical" if item.grade == "D" else "high",
                    "description": f"손절 검토: {item.summary}",
                    "action": "손절선 도달 종목 정리",
                })
            elif item.name == "현금비중":
                if "과다" in item.summary:
                    solutions.append({
                        "type": "increase_investment",
                        "urgency": "medium",
                        "description": f"현금 투입: {item.summary}",
                        "action": "확신도 높은 추천 종목에 분할 매수",
                    })
                else:
                    solutions.append({
                        "type": "increase_cash",
                        "urgency": "high",
                        "description": f"현금 확보: {item.summary}",
                        "action": "수익 종목 일부 익절로 현금 확보",
                    })
            elif item.name == "상관관계":
                solutions.append({
                    "type": "reduce_correlation",
                    "urgency": "medium",
                    "description": f"상관관계 해소: {item.summary}",
                    "action": "동일 섹터 종목 중 1개 축소",
                })
            elif item.name == "정책수혜":
                solutions.append({
                    "type": "add_policy_stock",
                    "urgency": "low",
                    "description": f"정책 수혜 보강: {item.summary}",
                    "action": "금융/자동차/바이오 등 정책 수혜 종목 편입 검토",
                })

    return solutions


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_diagnosis_report(diag: AccountDiagnosis) -> str:
    """Format full diagnosis report for Telegram."""
    grade_labels = {"A": "A+ (시장 초과)", "B": "B+ (시장 수준)",
                    "C": "C (시장 미달)", "D": "D (위험)"}
    overall_label = grade_labels.get(diag.overall_grade, diag.overall_grade)
    overall_emoji = _GRADE_EMOJI.get(diag.overall_grade, "\u26aa")

    lines: list[str] = [
        "\u2550" * 22,
        f"\U0001f3e5 {USER_NAME} 계좌 진단 리포트",
        "\u2550" * 22,
        "",
        f"종합 등급: {overall_emoji} {overall_label}",
        "",
    ]

    for item in diag.items:
        emoji = item.emoji or _GRADE_EMOJI.get(item.grade, "\u26aa")
        lines.append(f"{item.name}  {emoji} {item.summary}")
        for detail in item.details[:2]:
            lines.append(f"  {detail}")

    if diag.solutions:
        lines.append("")
        lines.append("\u2500" * 22)
        lines.append("\U0001f4a1 솔루션 요약")
        for sol in diag.solutions[:3]:
            urgency_emoji = {
                "critical": "\U0001f6a8", "high": "\u26a0\ufe0f",
                "medium": "\U0001f4a1", "low": "\U0001f4ac",
            }.get(sol["urgency"], "\U0001f4ac")
            lines.append(f"{urgency_emoji} {sol['description']}")
            lines.append(f"  -> {sol['action']}")

    lines.append("")
    lines.append("\U0001f916 Powered by K-Quant v3.0")

    return "\n".join(lines)


def format_solution_detail(solutions: list[dict[str, Any]]) -> str:
    """Format detailed solutions for [솔루션 보기] callback."""
    if not solutions:
        return f"{USER_NAME}, 현재 추가 솔루션이 없습니다. 포트폴리오 상태가 양호합니다!"

    lines: list[str] = [
        "\u2550" * 22,
        f"\U0001f4a1 {USER_NAME} 맞춤 솔루션",
        "\u2550" * 22,
        "",
    ]

    for i, sol in enumerate(solutions, 1):
        urgency_labels = {
            "critical": "긴급", "high": "중요",
            "medium": "권장", "low": "참고",
        }
        urgency = urgency_labels.get(sol["urgency"], "참고")
        lines.append(f"{i}. {sol['description']} ({urgency})")
        lines.append(f"   {sol['action']}")
        lines.append("")

    lines.append(f"다음 점검: 1주 후 스크린샷 보내주세요")

    return "\n".join(lines)


def format_account_history(
    snapshots: list[dict[str, Any]],
) -> str:
    """Format account snapshot history for /history command."""
    if not snapshots:
        return (
            f"\U0001f4ca {USER_NAME} 계좌 추이\n\n"
            "아직 스크린샷 기록이 없습니다.\n"
            "계좌 스크린샷을 보내주시면 추이를 기록합니다!"
        )

    lines: list[str] = [
        "\u2550" * 22,
        f"\U0001f4ca {USER_NAME} 계좌 추이",
        "\u2550" * 22,
        "",
        "날짜        총평가        수익률",
        "\u2500" * 30,
    ]

    for ss in snapshots[:10]:
        date_str = (ss.get("created_at", "") or "")[:10]
        total = ss.get("total_eval", 0)
        pct = ss.get("total_profit_pct", 0)
        emoji = "\U0001f7e2" if pct >= 0 else "\U0001f534"
        lines.append(f"{date_str}  {total:>12,.0f}원  {emoji} {pct:+.1f}%")

    # Trend analysis
    if len(snapshots) >= 2:
        first = snapshots[-1]
        last = snapshots[0]
        first_eval = first.get("total_eval", 0)
        last_eval = last.get("total_eval", 0)
        if first_eval > 0:
            total_return = (last_eval - first_eval) / first_eval * 100
            lines.append("")
            lines.append(f"기간 수익률: {total_return:+.1f}%")
            lines.append(f"기록 횟수: {len(snapshots)}회")

    return "\n".join(lines)
