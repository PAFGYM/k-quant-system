"""일일 전문 PDF 리포트 - Phase 8 → Phase 10+ (유료 레포트 수준).

매일 장 마감 후 증권사 수석 애널리스트급 PDF 리포트 자동 생성.
텔레그램으로 PDF 파일 전송.

구성 (4페이지):
  1페이지: Executive Summary + 글로벌 시장 총평 + 주요 지수 테이블
  2페이지: 심층 시장 분석 (미국/한국/환율/금리/원자재)
  3페이지: 포트폴리오 성과 + 보유종목별 심층 분석
  4페이지: 투자 전략 + 내일 전망 + 액션 플랜
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

# reportlab is optional
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Table,
        TableStyle, Spacer, PageBreak, Image,
        KeepTogether,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False
    logger.info("reportlab not installed; PDF reports disabled")

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import matplotlib
    matplotlib.use("Agg")  # headless 모드
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    # 한글 폰트 설정
    _kr_fonts = [
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    for _fp in _kr_fonts:
        if Path(_fp).exists():
            fm.fontManager.addfont(_fp)
            plt.rcParams["font.family"] = fm.FontProperties(fname=_fp).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _register_korean_font() -> str:
    """시스템 한글 폰트 등록. 등록된 폰트명 반환."""
    if not HAS_REPORTLAB:
        return "Helvetica"

    # 1순위: TTF 파일 (reportlab이 확실히 지원)
    # 2순위: TTC (subfontIndex 필요, PostScript outline 미지원 가능)
    font_paths = [
        # macOS TTF (가장 안정적)
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", "Korean", None),
        # Linux NanumGothic
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "Korean", None),
        ("/usr/share/fonts/nanum/NanumGothic.ttf", "Korean", None),
        # macOS TTC (subfontIndex로 시도)
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "Korean", 0),
    ]
    for entry in font_paths:
        path, name = entry[0], entry[1]
        sub_idx = entry[2] if len(entry) > 2 else None
        if Path(path).exists():
            try:
                if sub_idx is not None:
                    pdfmetrics.registerFont(TTFont(name, path, subfontIndex=sub_idx))
                else:
                    pdfmetrics.registerFont(TTFont(name, path))
                logger.info("한글 폰트 등록 성공: %s (%s)", name, path)
                return name
            except Exception as e:
                logger.debug("폰트 등록 실패 %s: %s", path, e)
                continue

    logger.warning("한글 폰트 없음. 기본 폰트 사용.")
    return "Helvetica"


def _create_styles(font_name: str) -> dict:
    """전문 리포트 스타일 생성."""
    if not HAS_REPORTLAB:
        return {}

    styles = getSampleStyleSheet()

    custom_styles = {}
    custom_styles["title"] = ParagraphStyle(
        name="ReportTitle",
        fontName=font_name,
        fontSize=16,
        spaceAfter=6 * mm,
        textColor=colors.HexColor("#1a1a2e"),
    )
    custom_styles["section"] = ParagraphStyle(
        name="SectionHeader",
        fontName=font_name,
        fontSize=13,          # 12→13
        spaceBefore=5 * mm,   # 4→5
        spaceAfter=3 * mm,    # 2→3
        textColor=colors.HexColor("#16213e"),
    )
    custom_styles["body"] = ParagraphStyle(
        name="ReportBody",
        fontName=font_name,
        fontSize=10,          # 9→10 (스마트폰 가독성)
        leading=15,           # 14→15
        textColor=colors.HexColor("#333333"),
    )
    custom_styles["small"] = ParagraphStyle(
        name="SmallBody",
        fontName=font_name,
        fontSize=9,           # 8→9
        leading=13,           # 11→13
        textColor=colors.HexColor("#555555"),
    )

    return custom_styles


def _table_style(font_name: str = "Korean"):
    """표준 테이블 스타일 (한글 폰트 적용, 스마트폰 최적화)."""
    if not HAS_REPORTLAB:
        return None
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, 0), 7),       # 헤더: 8→7
        ("FONTSIZE", (0, 1), (-1, -1), 7),       # 본문: 8→7
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),      # 첫 컬럼 좌측
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8f9fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),     # 4→6 (한글 폰트 겹침 방지)
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),  # 4→6
        ("LEFTPADDING", (0, 0), (-1, -1), 4),    # 3→4
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),   # 3→4
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])


def _cell_para(text: str, font_name: str = "Korean", size: int = 7) -> Paragraph:
    """테이블 셀용 Paragraph (자동 줄바꿈, 한글 CJK wordWrap)."""
    if not HAS_REPORTLAB:
        return str(text)
    style = ParagraphStyle(
        "cell", fontName=font_name, fontSize=size, leading=size + 4,
        wordWrap="CJK",
    )
    return Paragraph(str(text), style)


def _generate_portfolio_pnl_chart(holdings: list[dict]) -> str | None:
    """보유종목 수익률 바 차트 생성 → 임시 PNG 파일 경로 반환."""
    if not HAS_MATPLOTLIB or not holdings:
        return None
    try:
        import tempfile
        names = [h.get("name", "")[:6] for h in holdings[:10]]
        pnls = [h.get("pnl_pct", 0) for h in holdings[:10]]
        bar_colors = ["#2ecc71" if p >= 0 else "#e74c3c" for p in pnls]

        fig, ax = plt.subplots(figsize=(6, 2.5))
        bars = ax.barh(names, pnls, color=bar_colors, height=0.6)
        ax.axvline(x=0, color="#333", linewidth=0.8)
        for bar, pnl in zip(bars, pnls):
            ax.text(
                bar.get_width() + (0.3 if pnl >= 0 else -0.3),
                bar.get_y() + bar.get_height() / 2,
                f"{pnl:+.1f}%", va="center",
                fontsize=8, fontweight="bold",
                color="#2ecc71" if pnl >= 0 else "#e74c3c",
            )
        ax.set_xlabel("수익률 (%)", fontsize=9)
        ax.set_title("보유종목 수익률", fontsize=11, fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        plt.tight_layout()

        fp = os.path.join(tempfile.mkdtemp(), "pnl_chart.png")
        fig.savefig(fp, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return fp
    except Exception as e:
        logger.debug("PnL chart failed: %s", e)
        return None


def _generate_market_gauge_chart(macro) -> str | None:
    """글로벌 시장 지표 게이지 차트 생성 → 임시 PNG 파일 경로 반환."""
    if not HAS_MATPLOTLIB:
        return None
    try:
        import tempfile
        indicators = {
            "S&P500": macro.spx_change_pct,
            "나스닥": macro.nasdaq_change_pct,
            "VIX": -macro.vix_change_pct,  # VIX 하락 = 좋음
            "USD/KRW": -macro.usdkrw_change_pct,  # 원화 강세 = 좋음
            "BTC": macro.btc_change_pct,
            "Gold": macro.gold_change_pct,
        }
        names = list(indicators.keys())
        values = list(indicators.values())
        bar_colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in values]

        fig, ax = plt.subplots(figsize=(6, 2.2))
        bars = ax.bar(names, values, color=bar_colors, width=0.6)
        ax.axhline(y=0, color="#333", linewidth=0.8)
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + (0.1 if val >= 0 else -0.15),
                f"{val:+.1f}%", ha="center", fontsize=7, fontweight="bold",
            )
        ax.set_title("글로벌 시장 등락", fontsize=11, fontweight="bold")
        ax.set_ylabel("등락률 (%)", fontsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        plt.tight_layout()

        fp = os.path.join(tempfile.mkdtemp(), "market_gauge.png")
        fig.savefig(fp, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return fp
    except Exception as e:
        logger.debug("Market gauge chart failed: %s", e)
        return None


async def generate_daily_pdf(
    macro_snapshot,
    holdings: list[dict],
    sell_plans: list | None = None,
    sector_data: list | None = None,
    pulse_history: list | None = None,
    date: datetime | None = None,
    yf_client=None,
    global_news: list[dict] | None = None,
) -> str | None:
    """일일 PDF 리포트 생성.

    Args:
        macro_snapshot: MacroSnapshot instance.
        holdings: List of holding dicts.
        sell_plans: List of SellPlan from sell_planner.
        sector_data: Sector strength data.
        pulse_history: Market pulse history records.
        date: Report date. Defaults to today.
        global_news: List of global news dicts from DB.

    Returns:
        File path of generated PDF, or None if reportlab unavailable.
    """
    if not HAS_REPORTLAB:
        logger.warning("reportlab not installed, generating text report instead")
        return await _generate_text_report(
            macro_snapshot, holdings, sell_plans, sector_data, date,
        )

    # 보유종목 현재가 실시간 갱신 (PDF 신뢰성 보장)
    if holdings and yf_client:
        for h in holdings:
            ticker = h.get("ticker", "")
            if not ticker:
                continue
            try:
                fresh_price = await yf_client.get_current_price(ticker)
                if fresh_price and fresh_price > 0:
                    old_price = h.get("current_price", 0)
                    h["current_price"] = fresh_price
                    buy_price = h.get("buy_price", 0)
                    if buy_price > 0:
                        h["pnl_pct"] = (fresh_price - buy_price) / buy_price * 100
                    if old_price > 0 and abs(fresh_price - old_price) / old_price > 0.05:
                        logger.warning(
                            "PDF 가격 갭: %s %s→%s (%.1f%%)",
                            h.get("name", ticker), old_price, fresh_price,
                            (fresh_price - old_price) / old_price * 100,
                        )
            except Exception as e:
                logger.debug("PDF 가격 갱신 실패 %s: %s", ticker, e)

    if date is None:
        date = datetime.now(KST)

    filename = f"K-Quant_Daily_{date.strftime('%Y%m%d')}.pdf"
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    filepath = str(reports_dir / filename)

    font_name = _register_korean_font()
    styles = _create_styles(font_name)

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
    )

    elements = []

    # AI 분석 생성 (유료 레포트 수준)
    analysis = await _generate_ai_analysis(macro_snapshot, holdings, sell_plans)

    macro = macro_snapshot
    us10y_chg = getattr(macro, "us10y_change_pct", 0)

    # === 1페이지: Executive Summary + 글로벌 시장 ===
    elements.append(Paragraph(
        "K-Quant Daily Investment Report", styles["title"]))
    elements.append(Paragraph(
        f"{date.strftime('%Y년 %m월 %d일')} | {USER_NAME} 전용 | Confidential",
        styles["small"]))
    elements.append(Spacer(1, 4 * mm))

    # Executive Summary
    elements.append(Paragraph("Executive Summary", styles["section"]))
    exec_summary = analysis.get(
        "executive_summary",
        _default_market_summary(macro_snapshot),
    )
    elements.append(Paragraph(exec_summary, styles["body"]))
    elements.append(Spacer(1, 4 * mm))

    # 글로벌 시장 지표 테이블
    elements.append(Paragraph("Global Market Dashboard", styles["section"]))
    index_data = [
        ["지표", "현재값", "등락률", "시그널"],
        ["S&P500", f"{macro.spx_change_pct:+.2f}%", "",
         "상승" if macro.spx_change_pct > 0 else "하락"],
        ["나스닥", f"{macro.nasdaq_change_pct:+.2f}%", "",
         "상승" if macro.nasdaq_change_pct > 0 else "하락"],
        ["다우", f"{getattr(macro, 'dow_change_pct', 0):+.2f}%", "",
         "상승" if getattr(macro, 'dow_change_pct', 0) > 0 else "하락"],
        ["VIX", f"{macro.vix:.1f}", f"{macro.vix_change_pct:+.1f}%",
         "안정" if macro.vix < 20 else "주의" if macro.vix < 25 else "경계"],
        ["USD/KRW", f"{macro.usdkrw:,.0f}원", f"{macro.usdkrw_change_pct:+.1f}%",
         "원화약세" if macro.usdkrw_change_pct > 0 else "원화강세"],
        ["US10Y", f"{macro.us10y:.2f}%", f"{us10y_chg:+.1f}%", ""],
        ["US2Y", f"{getattr(macro, 'us2y', 0):.2f}%", "", ""],
        ["DXY", f"{macro.dxy:.1f}", "", ""],
        ["BTC", f"${macro.btc_price:,.0f}", f"{macro.btc_change_pct:+.1f}%", ""],
        ["Gold", f"${macro.gold_price:,.0f}", f"{macro.gold_change_pct:+.1f}%", ""],
        ["WTI", f"${getattr(macro, 'wti_price', 0):.1f}", "", ""],
    ]
    idx_table = Table(
        index_data,
        colWidths=[35 * mm, 35 * mm, 30 * mm, 30 * mm],
        rowHeights=[18] + [15] * (len(index_data) - 1),
    )
    idx_table.setStyle(_table_style(font_name))
    elements.append(idx_table)

    # 글로벌 시장 등락 차트
    market_chart = _generate_market_gauge_chart(macro)
    if market_chart and HAS_REPORTLAB:
        elements.append(Spacer(1, 3 * mm))
        elements.append(KeepTogether([
            Image(market_chart, width=160 * mm, height=60 * mm),
        ]))

    # === 2페이지: 심층 시장 분석 ===
    elements.append(PageBreak())

    elements.append(Paragraph("글로벌 시장 심층 분석", styles["section"]))
    global_market = analysis.get("global_market", "")
    if global_market:
        elements.append(Paragraph(global_market, styles["body"]))
    elements.append(Spacer(1, 4 * mm))

    elements.append(Paragraph("한국 시장 영향 분석", styles["section"]))
    korea_impact = analysis.get("korea_impact", "")
    if korea_impact:
        elements.append(Paragraph(korea_impact, styles["body"]))
    else:
        elements.append(Paragraph(
            "한국 시장 영향 분석 데이터 준비 중", styles["body"]))
    elements.append(Spacer(1, 4 * mm))

    # 시장 체제 표시
    regime_kr = {
        "risk_on": "적극 공격 (Risk-On)",
        "neutral": "중립 (Neutral)",
        "risk_off": "방어적 (Risk-Off)",
    }.get(macro.regime, "판단 중")
    elements.append(Paragraph(
        f"현재 시장 체제: {regime_kr}", styles["section"]))
    elements.append(Spacer(1, 4 * mm))

    # v6.1: 글로벌 뉴스 헤드라인 섹션
    if global_news:
        elements.append(Paragraph("Global News Headlines", styles["section"]))
        news_rows = [["구분", "출처", "헤드라인", "영향도"]]
        for n in global_news[:8]:
            urgency = "긴급" if n.get("is_urgent") else "일반"
            impact = n.get("impact_score", 0)
            impact_str = f"{impact}/10" if impact > 0 else "-"
            news_rows.append([
                urgency,
                str(n.get("source", ""))[:8],
                _cell_para(str(n.get("title", ""))[:50], font_name),
                impact_str,
            ])
        news_table = Table(
            news_rows,
            colWidths=[18 * mm, 22 * mm, 80 * mm, 18 * mm],
            rowHeights=[18] + [16] * (len(news_rows) - 1),
        )
        news_table.setStyle(_table_style(font_name))
        elements.append(news_table)
        elements.append(Spacer(1, 3 * mm))

    # === 3페이지: 포트폴리오 ===
    elements.append(PageBreak())

    elements.append(Paragraph("Portfolio Performance", styles["section"]))

    if holdings:
        total_pnl = sum(h.get("pnl_pct", 0) for h in holdings) / len(holdings)
        total_value = sum(
            h.get("current_price", 0) * h.get("quantity", 1)
            for h in holdings
        )
        elements.append(Paragraph(
            f"보유 {len(holdings)}종목 | 평균 수익률: {total_pnl:+.1f}% | "
            f"시장체제: {regime_kr}",
            styles["body"],
        ))
        elements.append(Spacer(1, 2 * mm))

        h_header = ["종목", "매수가", "현재가", "수익률", "투자시계", "판단"]
        h_rows = [h_header]
        for h in holdings[:15]:
            pnl = h.get("pnl_pct", 0)
            cur_price = h.get("current_price", 0)
            verdict = "홀드"
            if pnl > 15:
                verdict = "익절 고려"
            elif pnl < -10:
                verdict = "손절 검토"
            elif pnl > 5:
                verdict = "수익 관리"
            h_rows.append([
                h.get("name", "")[:8],
                f"{h.get('buy_price', 0):,.0f}",
                f"{cur_price:,.0f}" if cur_price > 0 else "미확인",
                f"{pnl:+.1f}%",
                h.get("horizon", "swing")[:4],
                verdict,
            ])

        ht = Table(
            h_rows,
            colWidths=[30 * mm, 25 * mm, 25 * mm, 20 * mm, 18 * mm, 22 * mm],
            rowHeights=[18] + [16] * (len(h_rows) - 1),
        )
        ht.setStyle(_table_style(font_name))
        elements.append(ht)

        # 보유종목 수익률 차트
        pnl_chart = _generate_portfolio_pnl_chart(holdings)
        if pnl_chart and HAS_REPORTLAB:
            elements.append(Spacer(1, 3 * mm))
            elements.append(KeepTogether([
                Image(pnl_chart, width=160 * mm, height=65 * mm),
            ]))
    else:
        elements.append(Paragraph("보유종목 없음", styles["body"]))

    elements.append(Spacer(1, 4 * mm))

    # 포트폴리오 심층 분석
    elements.append(Paragraph("보유종목 심층 분석", styles["section"]))
    holdings_analysis = analysis.get("portfolio_analysis", "")
    if not holdings_analysis:
        holdings_analysis = analysis.get("holdings_analysis", "")
    if holdings_analysis:
        elements.append(Paragraph(holdings_analysis, styles["body"]))
    else:
        for h in holdings[:8]:
            name = h.get("name", "")
            pnl = h.get("pnl_pct", 0)
            elements.append(Paragraph(
                f"[{name}] 수익률 {pnl:+.1f}% | "
                f"매수가 {h.get('buy_price', 0):,.0f}원 | "
                f"투자시계: {h.get('horizon', 'swing')}",
                styles["body"],
            ))
        elements.append(Spacer(1, 2 * mm))

    # === 4페이지: 전략 + 전망 ===
    elements.append(PageBreak())

    # 매도 계획
    elements.append(Paragraph("투자 시계별 매매 전략", styles["section"]))
    if sell_plans:
        from kstock.core.sell_planner import SellPlan
        sp_header = ["종목", "시계", "목표가", "손절가", "전략"]
        sp_rows = [sp_header]
        for plan in sell_plans[:10]:
            sp_rows.append([
                plan.name[:8],
                plan.horizon[:4],
                str(plan.target),
                str(plan.stoploss),
                _cell_para(plan.strategy[:40], font_name),
            ])
        spt = Table(
            sp_rows,
            colWidths=[28 * mm, 18 * mm, 25 * mm, 25 * mm, 52 * mm],
        )
        spt.setStyle(_table_style(font_name))
        elements.append(spt)
    else:
        elements.append(Paragraph("매도 계획 없음", styles["body"]))

    elements.append(Spacer(1, 4 * mm))

    # 투자 전략 제안 (AI)
    strategy = analysis.get("strategy", "")
    if strategy:
        elements.append(Paragraph("투자 전략 제안", styles["section"]))
        elements.append(Paragraph(strategy, styles["body"]))
        elements.append(Spacer(1, 4 * mm))

    # 주간/월간 전망
    elements.append(Paragraph("Outlook: 주간/월간 전망", styles["section"]))
    outlook = analysis.get("outlook", "")
    if not outlook:
        outlook = analysis.get("tomorrow", "내일 시장 전망 데이터 없음.")
    elements.append(Paragraph(outlook, styles["body"]))

    # 면책 조항
    elements.append(Spacer(1, 8 * mm))
    elements.append(Paragraph(
        "DISCLAIMER: 본 리포트는 K-Quant AI가 자동 생성한 투자 참고 자료입니다. "
        "본 자료에 수록된 내용은 신뢰할 수 있는 자료 및 정보를 바탕으로 "
        "작성한 것이나, 그 정확성이나 완전성을 보장할 수 없습니다. "
        "투자 판단과 책임은 투자자 본인에게 있습니다. "
        f"K-Quant v3.5 | {date.strftime('%Y.%m.%d')} | For {USER_NAME} Only",
        styles["small"],
    ))

    # PDF 생성
    try:
        doc.build(elements)
        logger.info("PDF report generated: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        return await _generate_text_report(
            macro_snapshot, holdings, sell_plans, sector_data, date,
        )


async def _generate_text_report(
    macro_snapshot, holdings, sell_plans, sector_data, date=None,
) -> str:
    """PDF 대신 텍스트 파일로 리포트 생성 (reportlab 없을 때)."""
    if date is None:
        date = datetime.now(KST)

    filename = f"K-Quant_Daily_{date.strftime('%Y%m%d')}.txt"
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    filepath = str(reports_dir / filename)

    lines = [
        f"K-Quant 일일 투자 리포트",
        f"{date.strftime('%Y년 %m월 %d일')}",
        "=" * 40,
        "",
        _default_market_summary(macro_snapshot),
        "",
    ]

    if holdings:
        lines.append("보유종목:")
        for h in holdings[:10]:
            lines.append(
                f"  {h.get('name', '')}: {h.get('pnl_pct', 0):+.1f}% "
                f"({h.get('horizon', 'swing')})"
            )

    if sell_plans:
        lines.extend(["", "매도 계획:"])
        for p in sell_plans[:8]:
            lines.append(f"  {p.name}: 목표 {p.target} / 손절 {p.stoploss}")

    lines.extend([
        "",
        "본 리포트는 K-Quant AI가 자동 생성한 투자 참고 자료입니다.",
    ])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


def _default_market_summary(macro) -> str:
    """AI 없이 기본 시장 요약 생성."""
    vix_status = "안정" if macro.vix < 20 else "주의" if macro.vix < 25 else "공포"
    regime_kr = {
        "risk_on": "적극 공격",
        "neutral": "보수적",
        "risk_off": "방어적",
    }.get(macro.regime, "판단 중")

    return (
        f"시장 체제: {regime_kr}\n"
        f"S&P500 {macro.spx_change_pct:+.2f}%, "
        f"나스닥 {macro.nasdaq_change_pct:+.2f}%\n"
        f"VIX {macro.vix:.1f} ({vix_status}), "
        f"환율 {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
        f"BTC ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)"
    )


async def _generate_ai_analysis(
    macro_snapshot, holdings, sell_plans,
) -> dict:
    """Sonnet으로 유료 레포트 수준 전문 분석 생성 (6개 섹션)."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or not HAS_ANTHROPIC:
        return {}

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)

        holdings_text = ""
        if holdings:
            for h in holdings[:15]:
                cp = h.get('current_price', 0)
                price_tag = f"{cp:,.0f}원 (실시간)" if cp > 0 else "현재가 없음"
                holdings_text += (
                    f"  {h.get('name', '')}: 수익률 {h.get('pnl_pct', 0):+.1f}%, "
                    f"매수가 {h.get('buy_price', 0):,.0f}원, "
                    f"현재가 {price_tag}, "
                    f"시계 {h.get('horizon', 'swing')}\n"
                )

        sell_text = ""
        if sell_plans:
            for p in sell_plans[:8]:
                sell_text += (
                    f"  {p.name}: 목표 {p.target}, 손절 {p.stoploss}, "
                    f"전략 {p.strategy[:60]}\n"
                )

        prompt = (
            "당신은 Goldman Sachs/Morgan Stanley급 글로벌 투자은행의 "
            "수석 리서치 애널리스트입니다. "
            "기관 투자자에게 제공하는 유료 데일리 리포트를 작성하세요.\n\n"
            f"호칭: {USER_NAME}\n"
            "볼드(**) 사용 금지. HTML 태그 사용 금지.\n\n"
            f"[글로벌 시장 데이터]\n"
            f"S&P500: {macro_snapshot.spx_change_pct:+.2f}%\n"
            f"나스닥: {macro_snapshot.nasdaq_change_pct:+.2f}%\n"
            f"다우: {getattr(macro_snapshot, 'dow_change_pct', 0):+.2f}%\n"
            f"VIX: {macro_snapshot.vix:.1f} ({macro_snapshot.vix_change_pct:+.1f}%)\n"
            f"환율: {macro_snapshot.usdkrw:,.0f}원 ({macro_snapshot.usdkrw_change_pct:+.1f}%)\n"
            f"US10Y: {macro_snapshot.us10y:.2f}%\n"
            f"US2Y: {getattr(macro_snapshot, 'us2y', 0):.2f}%\n"
            f"DXY: {macro_snapshot.dxy:.1f}\n"
            f"BTC: ${macro_snapshot.btc_price:,.0f} ({macro_snapshot.btc_change_pct:+.1f}%)\n"
            f"금: ${macro_snapshot.gold_price:,.0f} ({macro_snapshot.gold_change_pct:+.1f}%)\n"
            f"유가: ${getattr(macro_snapshot, 'wti_price', 0):.1f}\n"
            f"시장체제: {macro_snapshot.regime}\n\n"
            f"[보유종목]\n{holdings_text}\n"
            f"[매도계획]\n{sell_text}\n\n"
            "아래 6개 섹션을 각각 ---로 구분하여 상세히 작성:\n\n"
            "1. executive_summary: 오늘의 핵심 포인트 (3-5줄)\n"
            "   - 시장 움직임 핵심 요약\n"
            "   - 투자 판단에 영향을 주는 가장 중요한 1-2개 팩터\n"
            "   - 포트폴리오 관점에서 결론\n\n"
            "2. global_market: 글로벌 시장 심층 분석 (10-15줄)\n"
            "   - 미국 3대 지수 분석 (섹터 로테이션 포함)\n"
            "   - 채권 시장 (일드커브, 스프레드 변화)\n"
            "   - 외환 시장 (달러인덱스, 원화, 엔화)\n"
            "   - 원자재 (유가, 금, 구리)\n"
            "   - 크립토 시장 동향\n"
            "   - 글로벌 자금 흐름\n\n"
            "3. korea_impact: 한국 시장 영향 분석 (8-10줄)\n"
            "   - 코스피/코스닥 영향 예상\n"
            "   - 외국인/기관 수급 전망\n"
            "   - 업종별 영향 (반도체, 2차전지, 바이오, 자동차 등)\n"
            "   - 정책/규제 이슈\n\n"
            "4. portfolio_analysis: 포트폴리오 심층 분석 (종목별 3-4줄씩)\n"
            "   - 각 보유종목의 기술적/펀더멘털 분석\n"
            "   - 현재 포지션 적정성 평가\n"
            "   - 리스크/리워드 비율\n"
            "   - 구체적 매매 제안 (목표가, 손절가 포함)\n\n"
            "5. strategy: 투자 전략 제안 (8-10줄)\n"
            "   - 포트폴리오 리밸런싱 제안\n"
            "   - 섹터 비중 조절 의견\n"
            "   - 신규 매수 고려 종목과 근거\n"
            "   - 헷지 전략\n\n"
            "6. outlook: 주간/월간 전망 (5-7줄)\n"
            "   - 이번주 핵심 이벤트 (FOMC, 경제지표 등)\n"
            "   - 단기(1주), 중기(1개월) 시나리오\n"
            "   - 리스크 요인과 대응 방안\n"
        )

        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4000,
            temperature=0.3,
            system=(
                "당신은 Goldman Sachs Global Investment Research 팀의 "
                "수석 전략가입니다. 기관 투자자에게 제공하는 유료 데일리 리포트를 "
                "작성합니다. 모든 분석은 데이터에 기반하며, 구체적 수치와 "
                "논리적 근거를 반드시 포함합니다. 추상적 표현 대신 "
                "실행 가능한 투자 인사이트를 제공합니다. "
                "볼드(**), HTML 태그 사용 금지.\n\n"
                "절대 규칙:\n"
                "1. 제공된 [보유종목] 데이터의 현재가만 사용하라.\n"
                "2. 학습 데이터의 과거 주가를 절대 사용하지 마라.\n"
                "3. 가격 데이터 없는 종목은 '현재가 확인 필요'로 표시.\n"
                "4. 구체적 주가 언급 시 반드시 제공된 데이터에서 가져와라.\n"
                "5. 추정/기억/학습된 가격 정보 사용 절대 금지."
            ),
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        text = text.replace("**", "")
        parts = text.split("---")

        result = {}
        keys = [
            "executive_summary", "global_market", "korea_impact",
            "portfolio_analysis", "strategy", "outlook",
        ]
        for i, key in enumerate(keys):
            if i < len(parts):
                cleaned = parts[i].strip()
                # 레이블 제거 (예: "1. executive_summary:" 등)
                for prefix in [f"{i+1}.", key + ":", key]:
                    if cleaned.lower().startswith(prefix.lower()):
                        cleaned = cleaned[len(prefix):].strip()
                result[key] = cleaned

        # 하위 호환: market_summary, holdings_analysis, tomorrow
        result.setdefault("market_summary", result.get("executive_summary", ""))
        result.setdefault("holdings_analysis", result.get("portfolio_analysis", ""))
        result.setdefault("tomorrow", result.get("outlook", ""))

        return result

    except Exception as e:
        logger.warning("AI analysis generation failed: %s", e)
        return {}


def format_pdf_telegram_message(filepath: str, macro_snapshot, holdings) -> str:
    """PDF 리포트 발송 시 동반 텍스트 메시지."""
    date_str = datetime.now(KST).strftime("%Y.%m.%d")
    total_pnl = 0.0
    if holdings:
        total_pnl = sum(h.get("pnl_pct", 0) for h in holdings) / len(holdings)

    regime_kr = {
        "risk_on": "적극 공격",
        "neutral": "보수적",
        "risk_off": "방어적",
    }.get(macro_snapshot.regime, "")

    return (
        f"\U0001f4cb [일일 리포트] {date_str}\n\n"
        f"{USER_NAME}, 오늘 장 마감 리포트입니다.\n\n"
        f"시장: {regime_kr} | "
        f"S&P {macro_snapshot.spx_change_pct:+.2f}%\n"
        f"포트폴리오: {len(holdings)}종목, "
        f"평균 {total_pnl:+.1f}%\n\n"
        f"\U0001f4ce 상세 리포트는 첨부 파일 확인"
    )
