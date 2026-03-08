#!/usr/bin/env python3
"""K-Quant System v9.6 기능 설명서 PDF 생성.

보기 쉬운 레이아웃으로 구성:
- 큰 글씨 + 넉넉한 여백
- 색상으로 중요도 구분
- 표 중심 정보 전달
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, PageBreak, KeepTogether,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 한글 폰트 ──────────────────────────────────────────────
FONT = "Helvetica"
for path, name in [
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", "Korean"),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "Korean"),
]:
    if Path(path).exists():
        pdfmetrics.registerFont(TTFont(name, path))
        FONT = name
        break

# ── 색상 ────────────────────────────────────────────────────
C_DARK = colors.HexColor("#1a1a2e")
C_BLUE = colors.HexColor("#0f3460")
C_ACCENT = colors.HexColor("#e94560")
C_LIGHT = colors.HexColor("#f5f5f5")
C_WHITE = colors.white
C_GREEN = colors.HexColor("#27ae60")
C_ORANGE = colors.HexColor("#f39c12")
C_TEAL = colors.HexColor("#1abc9c")

# ── 스타일 (보기 쉽게 크게) ─────────────────────────────────
S_TITLE = ParagraphStyle("Title", fontName=FONT, fontSize=24, textColor=C_DARK,
                         spaceAfter=4*mm, leading=30)
S_H1 = ParagraphStyle("H1", fontName=FONT, fontSize=15, textColor=C_BLUE,
                       spaceBefore=7*mm, spaceAfter=3*mm, leading=20)
S_H2 = ParagraphStyle("H2", fontName=FONT, fontSize=12, textColor=C_ACCENT,
                       spaceBefore=5*mm, spaceAfter=2*mm, leading=16)
S_H3 = ParagraphStyle("H3", fontName=FONT, fontSize=10.5, textColor=C_TEAL,
                       spaceBefore=3*mm, spaceAfter=1.5*mm, leading=14)
S_BODY = ParagraphStyle("Body", fontName=FONT, fontSize=10, textColor=C_DARK,
                        spaceAfter=2*mm, leading=14)
S_BULLET = ParagraphStyle("Bullet", fontName=FONT, fontSize=10, textColor=C_DARK,
                          leftIndent=14, spaceAfter=1.5*mm, leading=13,
                          bulletIndent=4, bulletFontSize=9)
S_SMALL = ParagraphStyle("Small", fontName=FONT, fontSize=8, textColor=colors.gray,
                         spaceAfter=1*mm, leading=11)
S_COVER_TITLE = ParagraphStyle("CoverTitle", fontName=FONT, fontSize=30,
                               textColor=C_DARK, leading=38, spaceAfter=5*mm)
S_COVER_SUB = ParagraphStyle("CoverSub", fontName=FONT, fontSize=15,
                             textColor=C_BLUE, leading=22, spaceAfter=4*mm)
S_COVER_VER = ParagraphStyle("CoverVer", fontName=FONT, fontSize=12,
                             textColor=colors.HexColor("#666666"), leading=16,
                             spaceAfter=2*mm)
S_NEW = ParagraphStyle("New", fontName=FONT, fontSize=10, textColor=C_ACCENT,
                       leftIndent=14, spaceAfter=1.5*mm, leading=13,
                       bulletIndent=4, bulletFontSize=9)
S_TABLE_H = ParagraphStyle("TH", fontName=FONT, fontSize=9, textColor=C_WHITE,
                           leading=12)
S_TABLE_D = ParagraphStyle("TD", fontName=FONT, fontSize=8.5, textColor=C_DARK,
                           leading=12, wordWrap="CJK")


def p(style, text):
    return Paragraph(text, style)


def make_table(headers, rows, col_widths=None):
    data = [[p(S_TABLE_H, h) for h in headers]]
    for row in rows:
        data.append([p(S_TABLE_D, str(c)) for c in row])

    w = col_widths or [170 / len(headers) * mm] * len(headers)
    t = Table(data, colWidths=w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def make_highlight_table(items, col_widths=None):
    """강조 테이블: 아이콘 + 기능명 + 설명."""
    data = []
    for icon, name, desc in items:
        data.append([
            p(S_TABLE_D, icon),
            p(ParagraphStyle("BoldCell", fontName=FONT, fontSize=9.5,
                             textColor=C_DARK, leading=13), name),
            p(S_TABLE_D, desc),
        ])
    w = col_widths or [12*mm, 45*mm, 118*mm]
    t = Table(data, colWidths=w)
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C_WHITE, colors.HexColor("#f0f8ff")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def build_pdf(out_path: str):
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )
    story = []

    # ═══════════════════════════════════════════════════════
    # 표지
    # ═══════════════════════════════════════════════════════
    story.append(Spacer(1, 55*mm))
    story.append(p(S_COVER_TITLE, "K-Quant System v9.6"))
    story.append(p(S_COVER_SUB, "한국 주식 AI 자동 분석 & 트레이딩 시스템"))
    story.append(Spacer(1, 8*mm))
    story.append(p(S_COVER_VER, f"작성일: {datetime.now().strftime('%Y년 %m월 %d일')}"))
    story.append(p(S_COVER_VER, "버전: v9.6.0"))
    story.append(p(S_COVER_VER, "플랫폼: macOS (Mac Mini) + Telegram Bot"))
    story.append(Spacer(1, 15*mm))

    # 한 눈에 보는 시스템
    story.append(p(S_H2, "시스템 한 눈에 보기"))
    story.append(make_table(
        ["항목", "수치"],
        [
            ["Python 모듈", "170+개"],
            ["SQLite 테이블", "30+개"],
            ["자동 스케줄 작업", "45개"],
            ["트레이딩 전략", "10개 (A~J)"],
            ["기술 지표", "18+개"],
            ["AI 에이전트", "4개 (Haiku x3 + Sonnet)"],
            ["투자 매니저", "4명 (리버모어/오닐/린치/버핏)"],
            ["테스트 케이스", "2,895개"],
        ],
        col_widths=[80*mm, 95*mm],
    ))

    story.append(PageBreak())

    # 목차
    story.append(p(S_H1, "목차"))
    toc = [
        "1. v9.6 신기능 하이라이트",
        "2. 시스템 아키텍처",
        "3. 45개 자동 스케줄러",
        "4. 4대 투자 매니저 AI",
        "5. 10대 트레이딩 전략",
        "6. 기술적 분석 엔진",
        "7. AI 시스템 (토론 + 멀티분석)",
        "8. 매매 시스템 & 리스크 관리",
        "9. 학습 엔진 & 추천 추적",
        "10. 리포팅 & 데이터",
        "11. 텔레그램 UX",
    ]
    for t in toc:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {t}"))
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 1. v9.6 신기능
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "1. v9.6 신기능 하이라이트"))
    story.append(p(S_BODY,
        "v9.6은 '수익률 향상'에 집중한 업데이트입니다. "
        "학습 루프를 살리고, 변동성에 맞는 손절/익절을 자동 설정하며, "
        "높은 확신 종목에 더 많이 투자합니다."))

    story.append(make_highlight_table([
        ("🎯", "추천 추적 학습",
         "D+5/10/20일 실제 수익을 자동 추적. "
         "매니저 적중률 실시간 갱신. 약한 매니저 가중치 자동 하향."),
        ("📊", "ATR 동적 손절/익절",
         "고정 -3%/-7% 대신, 종목 변동성(ATR)에 맞춰 손절선과 1차/2차 목표가를 자동 계산. "
         "추천 목록 + 종목 상세 + 매수 확인에 표시."),
        ("🔥", "확신도 포지션 사이징",
         "점수 130점(STRONG_BUY) → ~20% 배분 vs 72점(MILD_BUY) → ~5% 배분. "
         "Half-Kelly 수학적 최적화. 매수 시 ★ 확신도 표시."),
        ("📐", "분할매수 안내",
         "50% 즉시 + 30% 눌림(현재-1ATR) + 20% 돌파확인(현재+0.5ATR). "
         "스캘핑은 전량 즉시. 매수 확인 시 자동 안내."),
        ("🏷", "매니저 정확도 배지",
         "모닝 브리핑에 각 매니저 적중률/평균수익 배지 표시. "
         "예: '🟢 적중률 67% | 5일 평균 +2.3%'"),
    ]))

    story.append(Spacer(1, 3*mm))
    story.append(p(S_H2, "ATR 손절/익절 예시"))
    story.append(make_table(
        ["유형", "손절(ATRx)", "1차목표(ATRx)", "2차목표(ATRx)", "트레일링"],
        [
            ["⚡ 스캘프", "1.5배", "2.0배", "3.0배", "1.0배"],
            ["🔥 스윙", "2.0배", "3.0배", "5.0배", "1.5배"],
            ["📊 포지션", "2.5배", "4.0배", "8.0배", "2.0배"],
            ["💎 장기", "3.0배", "5.0배", "10.0배", "2.5배"],
        ],
        col_widths=[30*mm, 30*mm, 35*mm, 35*mm, 35*mm],
    ))
    story.append(p(S_SMALL,
        "예: ATR 2.5% 스윙 종목 → 손절 -5%, 1차 목표 +7.5%, 2차 목표 +12.5%"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 2. 아키텍처
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "2. 시스템 아키텍처"))
    story.append(p(S_BODY,
        "Python 170+개 모듈, 6개 Bot Mixin 클래스를 조합하여 "
        "단일 텔레그램 봇 프로세스에서 실행됩니다."))

    story.append(make_table(
        ["모듈", "역할"],
        [
            ["CoreHandlersMixin", "초기화, 콜백 라우팅, 스크린샷 인식, 사용설명서"],
            ["MenusKisMixin", "메인 메뉴, KIS 증권 연동, 알림 설정"],
            ["TradingMixin", "포트폴리오 관리, 매수/매도 플로우, 리스크 분석"],
            ["SchedulerMixin", "45개 자동 작업, WebSocket, 적응형 모니터링"],
            ["CommandsMixin", "25개 슬래시 커맨드, 종목 스캐닝"],
            ["AdminExtrasMixin", "관리 패널, 에이전트 채팅, 원격 Claude Code"],
        ],
        col_widths=[45*mm, 130*mm],
    ))

    story.append(p(S_H2, "주요 서브시스템"))
    story.append(make_highlight_table([
        ("🧠", "Signal 엔진",
         "scoring.py (175점 복합점수) + strategies.py (10전략) + ensemble.py (가중투표)"),
        ("🤖", "AI 시스템",
         "agent_bridge.py (4에이전트) + debate_engine.py (3라운드 토론)"),
        ("📊", "분석 엔진",
         "technical.py (18지표) + pattern_matcher.py + price_target.py"),
        ("🎓", "학습 엔진",
         "learning_engine.py (매니저 성적표) + auto_debrief.py (추천 추적)"),
        ("💰", "매매 시스템",
         "position_sizer.py (Kelly/ATR) + investment_managers.py (4매니저)"),
        ("📡", "데이터",
         "KIS API > yfinance > Naver Finance (3단계 폴백 체인)"),
    ]))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 3. 스케줄러
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "3. 45개 자동 스케줄러"))
    story.append(p(S_BODY,
        "사용자가 아무것도 안 해도, 하루 종일 자동으로 분석/알림/학습이 진행됩니다."))

    story.append(p(S_H2, "주요 일일 스케줄"))
    story.append(make_table(
        ["시간", "작업", "설명"],
        [
            ["06:00", "AI 운영 지침 생성", "당일 시장 상황에 맞는 운영 방침"],
            ["06:30", "미분류 종목 자동 분류", "AI가 종목 특성 → 매니저 자동 배정"],
            ["07:00", "미국 프리마켓", "전날 미국장 결과 + 오늘 영향"],
            ["07:30", "모닝 브리핑", "글로벌 시장 + 매니저별 보유종목 분석"],
            ["08:20", "증권사 리포트", "네이버금융 리포트 자동 수집"],
            ["08:50", "WebSocket 연결", "62종목 실시간 체결/호가 스트리밍"],
            ["09:30", "AI 토론 (장시작)", "4매니저 3라운드 토론 → 합의 도출"],
            ["09~15시", "장중 모니터링", "60초마다 급등/급락/손절선/목표가 감시"],
            ["14:00", "AI 토론 (오후)", "장 중반 재평가 토론"],
            ["14:30", "단타 청산 리마인더", "스캘핑 종목 마감 전 알림"],
            ["15:35", "WebSocket 해제", "장 마감 후 연결 정리"],
            ["16:00", "장마감 리포트 + PDF", "추천종목 + 4페이지 전문가급 PDF"],
            ["16:20", "신호 평가 + 추천 추적", "D+N 수익 추적 + 매니저 성적표 갱신"],
            ["21:00", "일일 자가진단", "AI 활동/승률/결핍 분석 + 개선 제안"],
        ],
        col_widths=[22*mm, 45*mm, 108*mm],
    ))

    story.append(p(S_H2, "적응형 모니터링 (VIX 기반)"))
    story.append(make_table(
        ["시장 상태", "VIX", "모니터링 주기", "급등 감지 기준"],
        [
            ["🟢 안정", "< 18", "120초", "3%"],
            ["🟡 보통", "18-25", "60초", "2.5%"],
            ["🔴 공포", "25-30", "30초", "2%"],
            ["⚫ 패닉", "> 30", "15초", "1.5%"],
        ],
        col_widths=[30*mm, 30*mm, 50*mm, 55*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 4. 4대 매니저
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "4. 4대 투자 매니저 AI"))
    story.append(p(S_BODY,
        "보유종목마다 전설적 투자자의 철학을 가진 AI 매니저가 배정됩니다. "
        "매니저는 매일 분석하고, 적중률이 자동 기록되어 점점 똑똑해집니다."))

    story.append(make_table(
        ["매니저", "유형", "기간", "투자 철학"],
        [
            ["⚡ 제시 리버모어", "스캘프", "1~3일",
             "기술적 분석 + 차트 패턴 + 모멘텀 돌파, -3% 손절"],
            ["🔥 윌리엄 오닐", "스윙", "1~2주",
             "CAN-SLIM 성장주 + 상대강도 + 돌파매수, -7% 손절"],
            ["📊 피터 린치", "포지션", "1~3개월",
             "PEG < 1.0 + 6가지 분류 + 합리적 가치, -12% 손절"],
            ["💎 워런 버핏", "장기", "3개월+",
             "경제적 해자 + 내재가치 + 30% 안전마진, -20% 손절"],
        ],
        col_widths=[32*mm, 18*mm, 18*mm, 107*mm],
    ))

    story.append(p(S_H2, "v9.6 매니저 학습 시스템"))
    for item in [
        "추천 적중률 자동 추적: D+5일 후 수익이면 적중(1), 손실이면 미적중(0)",
        "매니저 성적표: 적중률/평균수익/최고매매/최악매매 자동 기록",
        "가중치 자동 조절: 적중률 70%+ → 1.2배 / 40%- → 0.8배",
        "모닝 브리핑에 '🟢 적중률 67%' 같은 정확도 배지 표시",
        "매매 교훈 자동 추출 → 다음 분석에 반영",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 5. 10대 전략
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "5. 10대 트레이딩 전략 (A~J)"))
    story.append(make_table(
        ["전략", "이름", "설명", "보유기간"],
        [
            ["A", "🔥 단기반등", "과매도 반등 (RSI<30 + BB하단)", "3-10일"],
            ["B", "⚡ ETF", "레버리지/인버스 ETF 타이밍", "1-3일"],
            ["C", "🏦 장기우량", "배당+시세 복합 성장", "6개월+"],
            ["D", "🔄 섹터로테이션", "업종 순환 포착", "1-3개월"],
            ["E", "🌍 글로벌분산", "글로벌 테마/분산 투자", "장기"],
            ["F", "🚀 모멘텀", "추세추종, 상대강도 상위", "2-8주"],
            ["G", "💥 돌파매매", "신고가/박스권 돌파", "3-10일"],
            ["H", "🔬 밸류", "저PBR/저PER 가치주", "1-6개월"],
            ["I", "📊 성장GARP", "적정가격 성장주 (PEG)", "1-3개월"],
            ["J", "🎯 이벤트", "실적/정책/이슈 촉매", "1-4주"],
        ],
        col_widths=[15*mm, 30*mm, 75*mm, 25*mm],
    ))

    story.append(p(S_H2, "시그널 앙상블 투표"))
    story.append(p(S_BODY,
        "10개 전략이 독립 평가 후 가중 투표로 최종 시그널 결정. "
        "레짐(risk_on/risk_off/neutral)에 따라 전략 가중치 자동 조절."))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 6. 기술적 분석
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "6. 기술적 분석 엔진"))

    story.append(p(S_H2, "18+ 기술 지표"))
    story.append(make_table(
        ["지표", "설명"],
        [
            ["RSI(14)", "과매수/과매도 + RSI 다이버전스 감지"],
            ["볼린저밴드", "%B, 밴드폭, 스퀴즈 감지 (20,2)"],
            ["MACD(12,26,9)", "시그널 크로스, 히스토그램 방향"],
            ["ATR(14)", "변동성 측정 → 동적 손절/익절 계산에 사용"],
            ["EMA 50/200", "골든크로스/데드크로스, 장기 추세"],
            ["스토캐스틱", "%K/%D 크로스, 과매수/과매도"],
            ["ADX", "추세 강도 (25+ = 강한 추세)"],
            ["OBV", "거래량 추세 확인"],
            ["52주 고점", "신고가 근접도, 눌림 깊이"],
            ["주봉 패턴", "매집/세력 탐지 (0-100점)"],
        ],
        col_widths=[35*mm, 140*mm],
    ))

    story.append(p(S_H2, "175점 복합 시그널 스코어"))
    story.append(make_table(
        ["영역", "비중", "평가 항목"],
        [
            ["매크로", "10%", "VIX, 환율, 시장 체제 (risk_on/off)"],
            ["수급", "30%", "외인/기관 순매수, 프로그램매매, 신용잔고"],
            ["펀더멘탈", "30%", "EPS, PER, PBR, ROE, 부채비율, 성장률"],
            ["기술적", "20%", "RSI, BB, MACD, ATR, 이평선, 패턴"],
            ["리스크", "10%", "변동성, 낙폭, 공매도, 집중도"],
            ["보너스", "+최대25", "AI 토론 합의 가산/감산"],
        ],
        col_widths=[28*mm, 20*mm, 127*mm],
    ))

    story.append(p(S_H2, "특수 스캐너"))
    for item in [
        "텐배거 헌터: 52주 -50%+ / 정책수혜 / 매출성장 / 외인매집 / 거래량 반전",
        "스텔스 매집: 기관+외인 동시 5일+ 연속 순매수 감지",
        "서지 스캐너: 당일 +5% 급등 종목 실시간 감지",
        "마켓 펄스: 7단계 시장 상태 (STRONG_BULL ~ STRONG_BEAR / REVERSAL)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 7. AI 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "7. AI 시스템"))

    story.append(p(S_H2, "3라운드 AI 토론 (v9.4+)"))
    story.append(p(S_BODY,
        "보유종목에 대해 4명의 AI 매니저가 실제 토론을 벌입니다."))
    story.append(make_table(
        ["라운드", "참여", "내용"],
        [
            ["R1: 독립분석", "Haiku x4", "각 매니저가 독립적으로 매수/매도/보유 판단"],
            ["R2: 반박", "Haiku x4", "다른 매니저 의견을 보고 반박/동의"],
            ["R3: 종합", "Sonnet x1", "모든 의견을 종합하여 최종 판정 + 목표가"],
        ],
        col_widths=[35*mm, 25*mm, 115*mm],
    ))
    story.append(p(S_BODY,
        "결과: STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL + 합의도 + 신뢰도"))

    story.append(p(S_H2, "4-에이전트 멀티분석"))
    story.append(make_highlight_table([
        ("📊", "기술 에이전트", "차트 지표 분석 (RSI, BB, MACD, 패턴)"),
        ("💰", "펀더멘탈 에이전트", "재무/밸류에이션 분석 (PER, ROE, 성장)"),
        ("📰", "센티먼트 에이전트", "뉴스/수급 감성 분석"),
        ("🧠", "전략가 (Sonnet)", "위 3개를 종합하여 최종 판단"),
    ]))

    story.append(p(S_H2, "AI 안전장치"))
    for item in [
        "매도 지시 필터: '전량 매도' → '포지션 점검 검토' 자동 변환",
        "패닉 언어 필터: '긴급', '당장' → 차분한 표현 교체",
        "할루시네이션 가드: 목표가 +-10% 이상 괴리 시 [미확인] 태깅",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 8. 매매 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "8. 매매 시스템 & 리스크 관리"))

    story.append(p(S_H2, "v9.6 매매 플로우"))
    story.append(p(S_BODY,
        "추천 → 매수 확인(ATR 손절/목표 + 분할매수 + 확신도 표시) "
        "→ 자동 모니터링 → 트레일링 스탑 → 단계적 익절 → 자동 복기 → 학습"))

    story.append(p(S_H3, "단계적 익절 (ATR 기반)"))
    story.append(make_table(
        ["단계", "조건", "행동"],
        [
            ["1차 익절", "1차 목표 도달 (ATR x tp1)", "1/3 물량 매도"],
            ["2차 익절", "2차 목표 도달 (ATR x tp2)", "1/3 물량 매도"],
            ["트레일링", "고점 대비 ATR x trail 하락", "나머지 전량 매도"],
            ["손절", "ATR x stop 하락", "전량 매도"],
        ],
        col_widths=[30*mm, 65*mm, 70*mm],
    ))

    story.append(p(S_H2, "리스크 엔진"))
    for item in [
        "Historical VaR(95%/99%) + Parametric VaR + CVaR (Expected Shortfall)",
        "몬테카를로 시뮬레이션 (기대수익/최대손실 추정)",
        "5개 역사적 위기 시나리오 스트레스 테스트",
        "포트폴리오 집중도 경고 (섹터 50% / 개별 30% 한도)",
        "안전 한도: 단일 주문 15%, 일일 10건, 일일 손실 -3% 한도",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "KIS 증권 연동"))
    for item in [
        "실전/모의 매매, 시장가/지정가 주문",
        "WebSocket 62종목 실시간 체결/호가 스트리밍",
        "08:50 자동 연결, 15:35 자동 해제",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 9. 학습 엔진
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "9. 학습 엔진 & 추천 추적"))
    story.append(p(S_BODY,
        "v9.6의 핵심: 추천한 종목의 실제 수익을 자동 추적하여, "
        "시스템이 스스로 학습하고 개선됩니다."))

    story.append(p(S_H2, "추천 추적 파이프라인"))
    story.append(make_table(
        ["단계", "시점", "동작"],
        [
            ["1. 추천", "스캔 시", "종목 + 점수 + 가격을 recommendations 테이블에 저장"],
            ["2. D+5 추적", "5일 후", "현재가 조회 → day5_return 기록"],
            ["3. D+10 추적", "10일 후", "현재가 조회 → day10_return 기록"],
            ["4. D+20 추적", "20일 후", "현재가 조회 → day20_return 기록"],
            ["5. 적중 판정", "자동", "D+5 수익 > 0 → 적중(1), 아니면 미적중(0)"],
            ["6. 성적표 갱신", "매일 16:20", "매니저별 적중률/평균수익/가중치 재계산"],
        ],
        col_widths=[30*mm, 25*mm, 120*mm],
    ))

    story.append(p(S_H2, "학습 결과 반영"))
    for item in [
        "매니저 가중치: 적중률 높으면 추천 비중 UP (1.2배), 낮으면 DOWN (0.8배)",
        "전략 가중치: 실적 좋은 전략의 앙상블 투표 비중 자동 상향",
        "매매 교훈: 완료 거래에서 AI가 교훈 추출 → 다음 분석 프롬프트에 주입",
        "사용자 프로필: 매매 패턴 학습 (평균 보유일/승률/선호 전략 등)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "추가 기능 (v9.0~9.5)"))
    story.append(make_highlight_table([
        ("🔬", "섹터 딥다이브",
         "6개 섹터 심층 리서치: 글로벌 피어 + 밸류체인 + 촉매/리스크"),
        ("📊", "비주얼 차트",
         "포트폴리오/수익추이/섹터/매니저별 이미지 차트 생성"),
        ("🎬", "유튜브 인텔리전스",
         "경제방송 AI 분석 → 종목/섹터/심리 추출"),
        ("🔗", "통합 컨텍스트",
         "모든 시스템이 서로 참조 (매니저+뉴스+토론+YouTube)"),
        ("📅", "이벤트→전략 연동",
         "FOMC/실적발표/정책 → 점수 자동 가감"),
        ("🌏", "한국형 리스크",
         "환율/지정학/수급/계절 등 7요소 종합 (0-100점)"),
    ]))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 10. 리포팅 & 데이터
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "10. 리포팅 & 데이터"))

    story.append(p(S_H2, "자동 리포트"))
    story.append(make_table(
        ["리포트", "시간", "내용"],
        [
            ["모닝 브리핑", "07:30", "글로벌 시장 + 매니저별 보유종목 분석 + 정확도 배지"],
            ["AI 토론 요약", "09:30/14:00", "4매니저 합의/반대의견/목표가"],
            ["일간 PDF", "16:00", "4페이지 전문가급 (시장/분석/포트폴리오/전략)"],
            ["자가진단", "21:00", "AI 활동수, 승률, 결핍 분석, 개선 제안"],
            ["주간 학습", "토 09:00", "이번 주 학습 + 다음 주 전략 조정"],
            ["매니저 성적표", "일 16:20", "4매니저 적중률/수익/가중치"],
        ],
        col_widths=[35*mm, 30*mm, 110*mm],
    ))

    story.append(p(S_H2, "데이터 소스 (3단계 폴백)"))
    story.append(make_table(
        ["순위", "소스", "지연", "용도"],
        [
            ["1순위", "KIS API (한국투자증권)", "실시간", "매매 결정 허용"],
            ["2순위", "yfinance", "~15분", "분석용 (매매 차단)"],
            ["3순위", "네이버 금융", "~20분", "백업 (매매 차단)"],
        ],
        col_widths=[22*mm, 50*mm, 25*mm, 68*mm],
    ))

    story.append(p(S_H2, "매크로 데이터 (3-tier 캐시)"))
    story.append(p(S_BODY,
        "VIX, S&P500, 나스닥, 코스피, 코스닥, 원/달러, 미10년 금리, "
        "BTC, 금, 공포탐욕지수 등 10+개 지표를 "
        "메모리 → SQLite → 라이브 API 순으로 3단계 캐싱."))

    # ═══════════════════════════════════════════════════════
    # 11. 텔레그램 UX
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "11. 텔레그램 UX"))

    story.append(p(S_H2, "메인 메뉴"))
    story.append(make_table(
        ["버튼", "기능"],
        [
            ["📊 분석", "종목 스캔, 멀티분석, 전략 필터"],
            ["📈 시황", "실시간 시장 + AI 코멘터리"],
            ["💰 잔고", "보유종목 현황 + 손익"],
            ["⭐ 즐겨찾기", "6탭 대시보드 (스캘프/스윙/포지션/장기/미분류/보유)"],
            ["💬 AI질문", "자유 질문 (Claude AI)"],
            ["📋 리포트", "PDF 리포트, 주간 보고"],
            ["⚙️ 더보기", "섹터 딥다이브, 비주얼 차트, 관리"],
            ["📖 사용설명서", "5페이지 가이드 (v9.6 업데이트)"],
        ],
        col_widths=[30*mm, 145*mm],
    ))

    story.append(p(S_H2, "v9.6 UX 변경점"))
    for item in [
        "추천 목록: 매수 종목 아래 ATR 손절→1차→2차 목표가 표시",
        "종목 상세: ATR 기준 가이드 섹션 (손절/1차/2차 가격)",
        "매수 확인: ATR 손절/목표 + 분할매수 추천 + 확신도 ★ 표시",
        "매니저 브리핑: 적중률/평균수익 배지 자동 표시",
        "사용설명서: v9.6 신기능 안내 페이지 업데이트",
    ]:
        story.append(p(S_NEW, f"<bullet>⭐</bullet> {item}"))

    story.append(p(S_H2, "25개 슬래시 커맨드"))
    story.append(p(S_BODY,
        "/start /goal /finance /consensus /backtest /optimize "
        "/short /future /history /risk /health /performance "
        "/scenario /ml /multi /surge /feedback /stats "
        "/accumulation /register /balance /admin /claude 등"))

    # ── 마지막 ──
    story.append(Spacer(1, 10*mm))
    story.append(p(S_SMALL, "━" * 80))
    story.append(p(S_SMALL,
        f"K-Quant System v9.6.0 | 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        "Co-Authored-By: Claude Opus 4.6"))

    doc.build(story)
    return out_path


if __name__ == "__main__":
    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports",
        f"K-Quant_v96_Features_{datetime.now().strftime('%Y%m%d')}.pdf",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    build_pdf(out)
    print(f"PDF 생성 완료: {out}")
