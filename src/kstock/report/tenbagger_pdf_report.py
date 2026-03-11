"""텐배거 유니버스 종합 PDF 리포트.

텐배거 유니버스 현황 + 7팩터 스코어링 + 포트폴리오 배분 + 매수전략.
텔레그램 봇에서 호출하여 PDF 생성 후 전송.

v2.1 (2026-03)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Table,
        TableStyle, Spacer, PageBreak, KeepTogether,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# ── 색상 ──
_DARK = None
_BLUE = None
_ACCENT = None
_GREEN = None
_RED = None
_GRAY = None
_LGRAY = None

if HAS_REPORTLAB:
    _DARK = colors.HexColor("#1a1a2e")
    _BLUE = colors.HexColor("#16213e")
    _ACCENT = colors.HexColor("#0f3460")
    _GREEN = colors.HexColor("#27ae60")
    _RED = colors.HexColor("#e74c3c")
    _GRAY = colors.HexColor("#555555")
    _LGRAY = colors.HexColor("#f0f0f0")

_FONT = "Helvetica"
_REPORT_DIR = Path(__file__).resolve().parents[3] / "reports"


def _register_font() -> str:
    if not HAS_REPORTLAB:
        return "Helvetica"
    global _FONT
    for path, sub in [
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", None),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
    ]:
        if Path(path).exists():
            try:
                if sub is not None:
                    pdfmetrics.registerFont(TTFont("Korean", path, subfontIndex=sub))
                else:
                    pdfmetrics.registerFont(TTFont("Korean", path))
                _FONT = "Korean"
                return _FONT
            except Exception:
                continue
    return _FONT


def _cell(text: str, sz: int = 10, bold: bool = False) -> "Paragraph":
    if bold:
        text = f"<b>{text}</b>"
    st = ParagraphStyle("c", fontName=_FONT, fontSize=sz, leading=sz + 5, wordWrap="CJK")
    return Paragraph(str(text), st)


def _tbl_style(header_color=None):
    hc = header_color or _DARK
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), hc),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LGRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])


def generate_tenbagger_report() -> str | None:
    """텐배거 유니버스 PDF 리포트를 생성한다.

    Returns:
        생성된 PDF 파일 경로 (실패 시 None).
    """
    if not HAS_REPORTLAB:
        logger.warning("reportlab 미설치 — PDF 생성 불가")
        return None

    _register_font()

    from kstock.signal.tenbagger_screener import (
        reload_config, get_initial_universe, load_tenbagger_config,
    )

    reload_config()
    config = load_tenbagger_config()
    universe = get_initial_universe()
    kr_stocks = [u for u in universe if u["market"] == "KRX"]
    us_stocks = [u for u in universe if u["market"] == "US"]

    sector_names = {k: v.get("name", k) for k, v in config.get("sectors", {}).items()}

    # 스타일
    s_title = ParagraphStyle("t", fontName=_FONT, fontSize=22, spaceAfter=8 * mm, textColor=_DARK, leading=28)
    s_sub = ParagraphStyle("sub", fontName=_FONT, fontSize=14, spaceAfter=4 * mm, textColor=_ACCENT, leading=18)
    s_sec = ParagraphStyle("sec", fontName=_FONT, fontSize=16, spaceBefore=6 * mm, spaceAfter=4 * mm, textColor=_BLUE, leading=20)
    s_body = ParagraphStyle("b", fontName=_FONT, fontSize=12, leading=18, textColor=colors.HexColor("#333"))
    s_sm = ParagraphStyle("sm", fontName=_FONT, fontSize=11, leading=16, textColor=_GRAY)
    s_bul = ParagraphStyle("bul", fontName=_FONT, fontSize=11, leading=16, textColor=colors.HexColor("#333"), leftIndent=12)
    s_hi = ParagraphStyle("hi", fontName=_FONT, fontSize=12, leading=18, textColor=_GREEN)
    s_warn = ParagraphStyle("w", fontName=_FONT, fontSize=12, leading=18, textColor=_RED)
    s_foot = ParagraphStyle("f", fontName=_FONT, fontSize=9, textColor=_GRAY, alignment=1)

    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = str(_REPORT_DIR / f"K-Quant_Tenbagger_{today}.pdf")

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )
    story: list = []

    # ═══ P1: 표지 ═══
    story.append(Spacer(1, 20 * mm))
    story.append(Paragraph("K-Quant Tenbagger Report", s_title))
    story.append(Paragraph(f"텐배거 유니버스 종합 분석 | {now.strftime('%Y.%m.%d')}", s_sub))
    ver = config.get("version", "2.0")
    story.append(Paragraph(f"v{ver} — 한국 {len(kr_stocks)}종목 + 미국 {len(us_stocks)}종목 = 총 {len(universe)}종목", s_sm))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Executive Summary", s_sec))
    # 등급별 카운트
    for market_label, stocks in [("한국", kr_stocks), ("미국", us_stocks)]:
        a_cnt = sum(1 for s in stocks if s["grade"] == "A")
        b_cnt = sum(1 for s in stocks if s["grade"] == "B")
        c_cnt = sum(1 for s in stocks if s["grade"] == "C")
        story.append(Paragraph(f"  {market_label}: A등급 {a_cnt} / B등급 {b_cnt} / C등급 {c_cnt} = {len(stocks)}종목", s_bul))

    # 섹터 분포
    sector_dist: dict[str, int] = {}
    for u in universe:
        sn = sector_names.get(u["sector"], u["sector"])
        sector_dist[sn] = sector_dist.get(sn, 0) + 1
    dist_str = " / ".join(f"{k}({v})" for k, v in sorted(sector_dist.items(), key=lambda x: -x[1]))
    story.append(Paragraph(f"  섹터: {dist_str}", s_bul))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("투자 4대 원칙", s_sec))
    for p in config.get("investment_criteria", []):
        story.append(Paragraph(f"  {p}", s_body))

    story.append(PageBreak())

    # ═══ P2: 한국 유니버스 ═══
    story.append(Paragraph(f"한국 텐배거 유니버스 ({len(kr_stocks)}종목)", s_sec))
    story.append(Paragraph("TAM(20%) | 정책(20%) | 해자(15%) | 매출(15%) | 수급(10%) | 모멘텀(10%) | AI(10%)", s_sm))
    story.append(Spacer(1, 3 * mm))

    hdr = [_cell("#", 9, True), _cell("종목", 9, True), _cell("섹터", 9, True),
           _cell("등급", 9, True), _cell("AI합의", 9, True), _cell("캐릭터", 9, True)]
    rows = [hdr]
    for i, u in enumerate(kr_stocks, 1):
        g = u["grade"]
        gc = {"A": "green", "B": "#f39c12", "C": "#e74c3c"}.get(g, "black")
        sn = sector_names.get(u["sector"], u["sector"])[:6]
        char = u.get("character", "")
        if len(char) > 25:
            char = char[:25] + "..."
        rows.append([
            _cell(str(i), 10),
            _cell(u["name"], 10),
            _cell(sn, 9),
            _cell(f"<font color='{gc}'><b>{g}</b></font>", 11),
            _cell(str(u.get("ai_consensus", "")), 10),
            _cell(char, 8),
        ])
    t1 = Table(rows, colWidths=[22, 82, 52, 28, 38, 190])
    t1.setStyle(_tbl_style())
    story.append(t1)

    story.append(Spacer(1, 5 * mm))

    # 미국
    story.append(Paragraph(f"미국 텐배거 유니버스 ({len(us_stocks)}종목)", s_sec))
    uhdr = [_cell("#", 10, True), _cell("종목", 10, True), _cell("티커", 10, True),
            _cell("섹터", 10, True), _cell("등급", 10, True), _cell("AI합의", 10, True)]
    urows = [uhdr]
    for i, u in enumerate(us_stocks, 1):
        g = u["grade"]
        gc = {"A": "green", "B": "#f39c12", "C": "#e74c3c"}.get(g, "black")
        sn = sector_names.get(u["sector"], u["sector"])[:8]
        urows.append([
            _cell(str(i), 11), _cell(u["name"], 11), _cell(u["ticker"], 11),
            _cell(sn, 10),
            _cell(f"<font color='{gc}'><b>{g}</b></font>", 12),
            _cell(str(u.get("ai_consensus", "")), 11),
        ])
    t2 = Table(urows, colWidths=[25, 95, 45, 80, 32, 45])
    t2.setStyle(_tbl_style())
    story.append(t2)

    story.append(PageBreak())

    # ═══ P3: 종목별 상세 카드 (한국) ═══
    story.append(Paragraph("한국 종목별 상세", s_sec))

    for u in kr_stocks:
        g = u["grade"]
        gc = {"A": "green", "B": "#f39c12", "C": "#e74c3c"}[g]
        sn = sector_names.get(u["sector"], u["sector"])
        elems = [
            Paragraph(
                f"<font color='{gc}'><b>[{g}]</b></font> {u['name']} ({u['ticker']}) | {sn}",
                ParagraphStyle("sh", fontName=_FONT, fontSize=14, leading=20, textColor=_BLUE, spaceBefore=3 * mm),
            ),
        ]
        if u.get("character"):
            elems.append(Paragraph(u["character"], s_sm))

        for c in u.get("catalysts", [])[:3]:
            elems.append(Paragraph(f"  + {c}", s_hi))
        for k in u.get("kill_conditions", [])[:2]:
            elems.append(Paragraph(f"  - {k}", s_warn))

        if u.get("monitor_12m"):
            monitors = " / ".join(u["monitor_12m"][:3])
            elems.append(Paragraph(f"  12M: {monitors}", s_sm))

        elems.append(Spacer(1, 2 * mm))
        story.append(KeepTogether(elems))

    story.append(PageBreak())

    # ═══ P4: 포트폴리오 구조 + 리스크 ═══
    story.append(Paragraph("포트폴리오 구조", s_sec))
    ps = config.get("portfolio_structure", {})
    story.append(Paragraph(
        f"A등급 코어 {ps.get('core_pct', 45)}% | "
        f"B등급 구조적 {ps.get('structural_pct', 35)}% | "
        f"C등급 옵션 {ps.get('option_pct', 20)}%",
        s_body,
    ))
    story.append(Paragraph(
        f"지역: 미국 {ps.get('region_us_pct', 60)}% | 한국 {ps.get('region_kr_pct', 40)}%",
        s_body,
    ))

    story.append(Spacer(1, 4 * mm))

    # 등급 정의
    grades_cfg = config.get("grades", {})
    for gk in ["A", "B", "C"]:
        gi = grades_cfg.get(gk, {})
        gc = {"A": "green", "B": "#f39c12", "C": "#e74c3c"}[gk]
        story.append(Paragraph(
            f"<font color='{gc}'><b>{gk}등급</b></font> {gi.get('label', '')} "
            f"({gi.get('position_min_pct', 0)}~{gi.get('position_max_pct', 0)}%) — "
            f"{gi.get('description', '')}",
            s_body,
        ))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("리스크 관리", s_sec))
    th = config.get("thresholds", {})
    story.append(Paragraph(f"  손절: {th.get('stop_loss_pct', -25)}%", s_warn))
    story.append(Paragraph(f"  1차 익절: +{th.get('target_1_pct', 100)}% (2배) — 원금 회수 50% 매도", s_hi))
    story.append(Paragraph(f"  2차 익절: +{th.get('target_2_pct', 500)}% (6배) — 추가 30% 매도", s_hi))
    story.append(Paragraph(f"  텐배거: +{th.get('target_3_pct', 900)}% (10배) — 나머지 홀드", s_body))

    # 제거 종목
    excluded = config.get("excluded", [])
    if excluded:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("제거/하향 종목", s_sec))
        for ex in excluded:
            name = ex.get("name", ex.get("ticker", ""))
            reason = ex.get("reason", "")
            story.append(Paragraph(f"  {name}: {reason}", s_sm))

    # 면책
    story.append(Spacer(1, 15 * mm))
    story.append(Paragraph(
        "본 보고서는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.",
        s_foot,
    ))
    story.append(Paragraph(
        f"K-Quant Tenbagger Manager | Generated {now.strftime('%Y-%m-%d %H:%M')}",
        s_foot,
    ))

    try:
        doc.build(story)
        logger.info("텐배거 PDF 생성: %s", out_path)
        return out_path
    except Exception as e:
        logger.error("텐배거 PDF 생성 실패: %s", e, exc_info=True)
        return None


def format_tenbagger_report_text() -> str:
    """텐배거 리포트 전송 시 동반 텍스트 메시지."""
    from kstock.signal.tenbagger_screener import (
        reload_config, get_initial_universe,
    )
    reload_config()
    universe = get_initial_universe()
    kr = [u for u in universe if u["market"] == "KRX"]
    us = [u for u in universe if u["market"] == "US"]

    date_str = datetime.now(KST).strftime("%Y.%m.%d")

    kr_a = sum(1 for s in kr if s["grade"] == "A")
    kr_b = sum(1 for s in kr if s["grade"] == "B")
    kr_c = sum(1 for s in kr if s["grade"] == "C")

    lines = [
        f"🔟 텐배거 유니버스 리포트 | {date_str}",
        "",
        f"한국 {len(kr)}종목 (A{kr_a} B{kr_b} C{kr_c})",
        f"미국 {len(us)}종목",
        "",
    ]

    for u in kr[:6]:
        g = u["grade"]
        ge = {"A": "🟢", "B": "🟡", "C": "🟠"}.get(g, "⚪")
        lines.append(f"  {ge} {u['name']} ({u['ticker']}) {g}등급")

    if len(kr) > 6:
        lines.append(f"  ...외 {len(kr) - 6}종목")

    lines.append("")
    lines.append("📎 상세 분석은 PDF 첨부 확인")
    return "\n".join(lines)
