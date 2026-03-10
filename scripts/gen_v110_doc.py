#!/usr/bin/env python3
"""K-Quant System v11.0 기능 설명서 PDF 생성.

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
    story.append(p(S_COVER_TITLE, "K-Quant System v11.0"))
    story.append(p(S_COVER_SUB, "한국 주식 AI 자동 분석 & 트레이딩 시스템"))
    story.append(Spacer(1, 8*mm))
    story.append(p(S_COVER_VER, f"작성일: {datetime.now().strftime('%Y년 %m월 %d일')}"))
    story.append(p(S_COVER_VER, "버전: v11.0.0"))
    story.append(p(S_COVER_VER, "플랫폼: macOS (Mac Mini) + Telegram Bot"))
    story.append(Spacer(1, 15*mm))

    # 한 눈에 보는 시스템
    story.append(p(S_H2, "시스템 한 눈에 보기"))
    story.append(make_table(
        ["항목", "수치"],
        [
            ["Python 모듈", "170+개"],
            ["SQLite 테이블", "30+개"],
            ["자동 스케줄 작업", "50+개"],
            ["트레이딩 전략", "10개 (A~J)"],
            ["기술 지표", "18+개"],
            ["ML 피처", "58개"],
            ["AI 에이전트", "4개 (Haiku x3 + Sonnet)"],
            ["투자 매니저", "4명 (리버모어/오닐/린치/버핏)"],
            ["YouTube 학습 채널", "24개"],
            ["추적 애널리스트", "19명"],
            ["일일 학습 예산", "$1.00/일"],
        ],
        col_widths=[80*mm, 95*mm],
    ))

    story.append(PageBreak())

    # 목차
    story.append(p(S_H1, "목차"))
    toc = [
        "1. v11.0 신기능 하이라이트",
        "2. 시스템 아키텍처",
        "3. ML 머신러닝 시스템",
        "4. AI 학습 파이프라인",
        "5. 50+ 자동 스케줄러",
        "6. 4대 투자 매니저 AI",
        "7. 10대 트레이딩 전략",
        "8. 기술적 분석 엔진",
        "9. AI 시스템 (토론 + 멀티분석)",
        "10. 매매 시스템 & 리스크 관리",
        "11. 크로스마켓 & 유가 분석",
        "12. 리포팅 & 데이터",
        "13. 텔레그램 UX",
    ]
    for t in toc:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {t}"))
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 1. v11.0 신기능
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "1. v11.0 신기능 하이라이트"))
    story.append(p(S_BODY,
        "v11.0은 'AI 학습 대폭 확장 + ML 앙상블 실전 투입'에 집중한 메이저 업데이트입니다. "
        "YouTube 24채널 + 19명 애널리스트 추적 + ML 예측을 결합합니다."))

    story.append(make_highlight_table([
        ("🤖", "ML 앙상블 예측",
         "LightGBM + XGBoost + LSTM 3모델 앙상블. 58개 피처 (기술+수급+크로스마켓). "
         "매일 자동 예측 + SHAP 설명. 주간 자동 재학습."),
        ("📺", "YouTube 24채널 학습",
         "Tier1: Gemini Flash 벌크 분석 (90건/일, $0.03). "
         "Tier2: Claude Haiku 심화 (15건/일, $0.11). "
         "라이브 방송 VOD 자동자막 학습."),
        ("👨‍💼", "19명 애널리스트 추적",
         "주요 증권사 애널리스트 이름 감지 시 자동 Tier2 승격. "
         "YouTube + 리포트 + 칼럼에서 의견 자동 수집."),
        ("🌍", "크로스마켓 임팩트",
         "18개 글로벌 자산 + 6요인 복합점수 + 10 섹터베타. "
         "ML 피처 8개 자동 주입."),
        ("🛢", "유가 분석 엔진",
         "WTI/Brent 레짐 분류, 7종 시그널, 섹터 임팩트, "
         "OPEC/EIA/지정학 이벤트 추적."),
        ("📊", "일일 합성 리포트",
         "매일 21:30 YouTube+리포트+칼럼 학습 내용 종합. "
         "핵심 인사이트 + 관심종목 + 위험요인 자동 생성."),
        ("💰", "학습 예산 관리",
         "$1.00/일 예산 내 자동 비용 최적화. "
         "Gemini Flash 벌크 + Claude Haiku 심화 2-tier 전략."),
    ]))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 2. 아키텍처
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "2. 시스템 아키텍처"))
    story.append(p(S_BODY,
        "Python 170+개 모듈, 8개 Bot Mixin 클래스를 조합하여 "
        "단일 텔레그램 봇 프로세스에서 실행됩니다."))

    story.append(make_table(
        ["모듈", "역할"],
        [
            ["CoreHandlersMixin", "초기화, 콜백 라우팅, WebSocket, 사용설명서"],
            ["MenusKisMixin", "메인 메뉴, KIS 증권 연동, 알림 설정"],
            ["TradingMixin", "포트폴리오 관리, 매수/매도 플로우, 리스크 분석"],
            ["SchedulerMixin", "50+개 자동 작업, 5x 학습 배치, ML 스케줄"],
            ["CommandsMixin", "25+개 슬래시 커맨드, /learning, /ml"],
            ["AdminExtrasMixin", "관리 패널, 에이전트 채팅, 원격 Claude Code"],
            ["ControlMixin", "Unix 소켓 제어 서버, 원격 명령 실행"],
            ["RemoteClaudeMixin", "Claude Code 원격 세션 관리"],
        ],
        col_widths=[45*mm, 130*mm],
    ))

    story.append(p(S_H2, "주요 서브시스템"))
    story.append(make_highlight_table([
        ("🧠", "Signal 엔진",
         "scoring.py (175점 복합점수) + strategies.py (10전략) + ensemble.py (가중투표)"),
        ("🤖", "AI 시스템",
         "ai_router.py (태스크 라우팅) + debate_engine.py (3라운드 토론)"),
        ("📊", "분석 엔진",
         "technical.py (18지표) + pattern_matcher.py + price_target.py"),
        ("🎓", "학습 엔진",
         "global_news.py (YouTube 24채널) + column_crawler.py + report_crawler.py"),
        ("🤖", "ML 시스템",
         "auto_trainer.py (LGB+XGB+LSTM 앙상블) + features.db (58피처)"),
        ("💰", "매매 시스템",
         "position_sizer.py (Kelly/ATR) + investment_managers.py (4매니저)"),
        ("📡", "데이터",
         "KIS API > yfinance > Naver Finance (3단계 폴백 체인)"),
        ("🌍", "글로벌",
         "cross_market_impact.py + oil_analysis.py + macro_tracker.py"),
    ]))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 3. ML 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "3. ML 머신러닝 시스템"))
    story.append(p(S_BODY,
        "3개 모델 앙상블로 종목별 매수/매도 확률을 예측합니다. "
        "58개 피처를 사용하며, 주간 자동 재학습으로 점점 정확해집니다."))

    story.append(p(S_H2, "3모델 앙상블"))
    story.append(make_table(
        ["모델", "가중치", "특징"],
        [
            ["LightGBM", "동적", "빠른 학습, 카테고리 피처 지원, SHAP 설명"],
            ["XGBoost", "동적", "정규화 강점, 과적합 방지, GPU 지원"],
            ["LSTM", "동적", "시계열 패턴 학습, 30일 시퀀스 입력"],
        ],
        col_widths=[35*mm, 25*mm, 115*mm],
    ))
    story.append(p(S_SMALL, "가중치는 검증 데이터 성능 기반 자동 조정"))

    story.append(p(S_H2, "58개 ML 피처"))
    story.append(make_table(
        ["카테고리", "개수", "주요 피처"],
        [
            ["기술적 지표", "18", "RSI, MACD, BB, ATR, ADX, OBV, 이평선 등"],
            ["수급/거래량", "12", "외인순매수, 기관순매수, 거래량비율, 신용잔고 등"],
            ["펀더멘탈", "10", "PER, PBR, ROE, 매출성장률, 부채비율 등"],
            ["패턴/모멘텀", "10", "52주고점비, 주봉패턴, N일수익률, 변동성 등"],
            ["크로스마켓", "8", "VIX, 환율, 미국선물, 원자재, 금리 등"],
        ],
        col_widths=[35*mm, 18*mm, 122*mm],
    ))

    story.append(p(S_H2, "ML 파이프라인"))
    story.append(make_table(
        ["단계", "시간", "동작"],
        [
            ["피처 수집", "매일 16:30", "20종목 x 58피처 → features.db 저장"],
            ["일일 예측", "매일 16:35", "3모델 앙상블 예측 → BUY/NEUTRAL/AVOID + SHAP"],
            ["실적 추적", "D+5일", "pykrx로 실제 수익률 계산 → 학습 라벨 갱신"],
            ["주간 재학습", "토 08:00", "축적된 데이터로 3모델 재학습 + 가중치 조정"],
        ],
        col_widths=[30*mm, 28*mm, 117*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 4. AI 학습 파이프라인
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "4. AI 학습 파이프라인"))
    story.append(p(S_BODY,
        "매일 $1 예산으로 YouTube 24채널 + 증권사 리포트 + 네이버 칼럼을 "
        "자동 학습합니다. Gemini Flash 벌크 + Claude Haiku 심화 2-tier 전략."))

    story.append(p(S_H2, "YouTube 24채널"))
    story.append(make_table(
        ["카테고리", "채널수", "대표 채널"],
        [
            ["경제방송", "6", "한국경제TV, SBS Biz, 이데일리TV, MTN, 매일경제TV, 토마토증권통"],
            ["시황전문", "4", "삼프로TV, 증시각도기TV, 주식단테, 시윤주식"],
            ["증권사 공식", "5", "키움증권, 삼성증권, KB증권, 한국투자증권, 하나증권"],
            ["투자/매크로", "5", "월가아재, 박곰희TV, 슈카월드, 부읽남, 신사임당"],
            ["뉴스/미국", "4", "뉴욕주민, 연합뉴스경제TV, 오선미국증시, 경제원탑"],
        ],
        col_widths=[30*mm, 22*mm, 123*mm],
    ))

    story.append(p(S_H2, "2-Tier 분석 전략"))
    story.append(make_table(
        ["Tier", "AI", "건수/일", "비용", "용도"],
        [
            ["Tier1", "Gemini Flash", "~90건", "$0.03", "벌크 스크리닝 (자막 기반)"],
            ["Tier2", "Claude Haiku", "~15건", "$0.11", "심화 분석 (최고 영상 선별)"],
        ],
        col_widths=[20*mm, 35*mm, 25*mm, 25*mm, 70*mm],
    ))

    story.append(p(S_H2, "19명 추적 애널리스트"))
    story.append(p(S_BODY,
        "주요 증권사 시황/전략 애널리스트를 자동 추적합니다. "
        "YouTube 제목이나 리포트에서 이름이 감지되면 자동 Tier2 승격."))
    story.append(make_table(
        ["애널리스트", "소속", "애널리스트", "소속"],
        [
            ["이선엽", "신한투자증권", "김선우", "메리츠증권"],
            ["이은택", "KB증권", "이재만", "하나증권"],
            ["박석중", "신한투자증권", "김록호", "하나증권"],
            ["박유악", "키움증권", "성종화", "이베스트투자증권"],
            ["이문종", "신한투자증권", "김동희", "메리츠증권"],
            ["강동진", "현대차증권", "김현수", "하나증권"],
            ["정원석", "하이투자증권", "김진우", "한국투자증권"],
            ["최광식", "다올투자증권", "이달미", "SK증권"],
            ["이승규", "바이오협회", "조진표", "투자전문가"],
        ],
        col_widths=[30*mm, 45*mm, 30*mm, 45*mm],
    ))
    story.append(p(S_SMALL, "이영훈 (투자이사) 외 추가 예정"))

    story.append(p(S_H2, "5x/일 학습 배치"))
    story.append(make_table(
        ["시간", "배치", "내용"],
        [
            ["06:30", "youtube_batch_1", "YouTube 20건 (Gemini Flash)"],
            ["08:00", "youtube_batch_2 + column_crawl", "YouTube 20건 + 네이버 칼럼 수집"],
            ["12:00", "youtube_batch_3", "YouTube 15건 (Gemini Flash)"],
            ["17:00", "youtube_batch_4 + report_crawl", "YouTube 20건 + 증권사 리포트"],
            ["21:00", "youtube_batch_5", "YouTube 15건 (Gemini Flash)"],
            ["21:30", "daily_synthesis", "일일 합성 리포트 (Flash + Haiku)"],
            ["22:00", "youtube_tier2_deep", "Tier2 심화 15건 (Haiku + Whisper)"],
        ],
        col_widths=[22*mm, 55*mm, 98*mm],
    ))

    story.append(p(S_H2, "$1/일 예산 배분"))
    story.append(make_table(
        ["항목", "비용", "비중"],
        [
            ["YouTube Tier1 (Gemini Flash, 90건)", "$0.034", "3.4%"],
            ["YouTube Tier2 (Claude Haiku, 15건)", "$0.108", "10.8%"],
            ["Whisper (자막없는 영상 5건)", "$0.240", "24.0%"],
            ["칼럼/리포트 분석 (Gemini Flash, 80건)", "$0.020", "2.0%"],
            ["일일/주간 합성", "$0.017", "1.7%"],
            ["기존 시스템 AI", "$0.100", "10.0%"],
            ["여유분 (버퍼)", "$0.481", "48.1%"],
        ],
        col_widths=[75*mm, 35*mm, 25*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 5. 스케줄러
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "5. 50+ 자동 스케줄러"))
    story.append(p(S_BODY,
        "사용자가 아무것도 안 해도, 하루 종일 자동으로 분석/학습/알림이 진행됩니다. "
        "v11.0에서 9개 학습 잡이 추가되어 총 50+개."))

    story.append(p(S_H2, "주요 일일 스케줄"))
    story.append(make_table(
        ["시간", "작업", "설명"],
        [
            ["06:00", "AI 운영 지침", "당일 시장 상황에 맞는 운영 방침"],
            ["06:30", "YouTube 배치 1 + 자동 분류", "20건 학습 + 종목 매니저 배정"],
            ["07:00", "미국 프리마켓", "전날 미국장 결과 + 오늘 영향"],
            ["07:10", "유가 분석", "WTI/Brent 레짐 + 섹터 임팩트"],
            ["07:15", "크로스마켓", "18 글로벌 자산 복합점수"],
            ["07:30", "모닝 브리핑", "글로벌 + 매니저별 분석 + ML 예측"],
            ["08:00", "YouTube 배치 2 + 칼럼", "20건 + 네이버 칼럼 수집"],
            ["08:20", "증권사 리포트", "네이버금융 리포트 자동 수집"],
            ["08:50", "WebSocket 연결", "75종목 실시간 스트리밍"],
            ["09:30", "AI 토론 (장시작)", "4매니저 3라운드 토론"],
            ["09~15", "장중 모니터링", "급등/급락/손절/목표가 60초 감시"],
            ["12:00", "YouTube 배치 3", "15건 (Gemini Flash)"],
            ["14:00", "AI 토론 (오후)", "장 중반 재평가"],
            ["16:00", "장마감 리포트 + PDF", "4페이지 전문가급 PDF"],
            ["16:20", "신호 평가 + ML 예측", "추천 추적 + 58피처 예측"],
            ["17:00", "YouTube 배치 4 + 리포트", "20건 + 증권사 리포트"],
            ["21:00", "YouTube 배치 5 + 자가진단", "15건 + AI 개선 제안"],
            ["21:30", "일일 합성", "전체 학습 종합 리포트"],
            ["22:00", "Tier2 심화", "최고 영상 15건 Haiku 분석"],
        ],
        col_widths=[18*mm, 45*mm, 112*mm],
    ))

    story.append(p(S_H2, "적응형 모니터링 (VIX 기반)"))
    story.append(make_table(
        ["시장 상태", "VIX", "모니터링 주기", "급등 감지 기준"],
        [
            ["안정", "< 18", "120초", "3%"],
            ["보통", "18-25", "60초", "2.5%"],
            ["공포", "25-30", "30초", "2%"],
            ["패닉", "> 30", "15초", "1.5%"],
        ],
        col_widths=[30*mm, 30*mm, 50*mm, 55*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 6. 4대 매니저
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "6. 4대 투자 매니저 AI"))
    story.append(p(S_BODY,
        "보유종목마다 전설적 투자자의 철학을 가진 AI 매니저가 배정됩니다. "
        "매니저는 매일 분석하고, 적중률이 자동 기록되어 점점 똑똑해집니다."))

    story.append(make_table(
        ["매니저", "유형", "기간", "투자 철학"],
        [
            ["제시 리버모어", "스캘프", "1~3일",
             "기술적 분석 + 차트 패턴 + 모멘텀 돌파, -3% 손절"],
            ["윌리엄 오닐", "스윙", "1~2주",
             "CAN-SLIM 성장주 + 상대강도 + 돌파매수, -7% 손절"],
            ["피터 린치", "포지션", "1~3개월",
             "PEG < 1.0 + 6가지 분류 + 합리적 가치, -12% 손절"],
            ["워런 버핏", "장기", "3개월+",
             "경제적 해자 + 내재가치 + 30% 안전마진, -20% 손절"],
        ],
        col_widths=[32*mm, 18*mm, 18*mm, 107*mm],
    ))

    story.append(p(S_H2, "매니저 학습 시스템"))
    for item in [
        "추천 적중률 자동 추적: D+5일 후 수익이면 적중(1), 손실이면 미적중(0)",
        "매니저 성적표: 적중률/평균수익/최고매매/최악매매 자동 기록",
        "가중치 자동 조절: 적중률 70%+ > 1.2배 / 40%- > 0.8배",
        "모닝 브리핑에 적중률 배지 자동 표시",
        "매매 교훈 자동 추출 > 다음 분석에 반영",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 7. 10대 전략
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "7. 10대 트레이딩 전략 (A~J)"))
    story.append(make_table(
        ["전략", "이름", "설명", "보유기간"],
        [
            ["A", "단기반등", "과매도 반등 (RSI<30 + BB하단)", "3-10일"],
            ["B", "ETF", "레버리지/인버스 ETF 타이밍", "1-3일"],
            ["C", "장기우량", "배당+시세 복합 성장", "6개월+"],
            ["D", "섹터로테이션", "업종 순환 포착", "1-3개월"],
            ["E", "글로벌분산", "글로벌 테마/분산 투자", "장기"],
            ["F", "모멘텀", "추세추종, 상대강도 상위", "2-8주"],
            ["G", "돌파매매", "신고가/박스권 돌파", "3-10일"],
            ["H", "밸류", "저PBR/저PER 가치주", "1-6개월"],
            ["I", "성장GARP", "적정가격 성장주 (PEG)", "1-3개월"],
            ["J", "이벤트", "실적/정책/이슈 촉매", "1-4주"],
        ],
        col_widths=[15*mm, 30*mm, 75*mm, 25*mm],
    ))

    story.append(p(S_H2, "시그널 앙상블 투표"))
    story.append(p(S_BODY,
        "10개 전략이 독립 평가 후 가중 투표로 최종 시그널 결정. "
        "레짐(risk_on/risk_off/neutral)에 따라 전략 가중치 자동 조절."))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 8. 기술적 분석
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "8. 기술적 분석 엔진"))

    story.append(p(S_H2, "18+ 기술 지표"))
    story.append(make_table(
        ["지표", "설명"],
        [
            ["RSI(14)", "과매수/과매도 + RSI 다이버전스 감지"],
            ["볼린저밴드", "%B, 밴드폭, 스퀴즈 감지 (20,2)"],
            ["MACD(12,26,9)", "시그널 크로스, 히스토그램 방향"],
            ["ATR(14)", "변동성 측정 > 동적 손절/익절 계산에 사용"],
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
    # 9. AI 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "9. AI 시스템"))

    story.append(p(S_H2, "3라운드 AI 토론"))
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

    story.append(p(S_H2, "AI 라우터 (v11.0)"))
    story.append(p(S_BODY,
        "태스크별 최적 AI 모델을 자동 선택합니다."))
    story.append(make_table(
        ["태스크", "모델", "비용"],
        [
            ["youtube_screening (Tier1 벌크)", "Gemini Flash", "$0.0004/건"],
            ["column_summary (칼럼/리포트)", "Gemini Flash", "$0.0003/건"],
            ["daily_synthesis (일일 합성)", "Flash + Haiku", "$0.017/일"],
            ["youtube_deep (Tier2 심화)", "Claude Haiku", "$0.007/건"],
            ["debate / multi_analysis", "Haiku + Sonnet", "기존 비용"],
        ],
        col_widths=[55*mm, 40*mm, 35*mm],
    ))

    story.append(p(S_H2, "AI 안전장치"))
    for item in [
        "매도 지시 필터: '전량 매도' > '포지션 점검 검토' 자동 변환",
        "패닉 언어 필터: '긴급', '당장' > 차분한 표현 교체",
        "할루시네이션 가드: 목표가 +-10% 이상 괴리 시 [미확인] 태깅",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 10. 매매 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "10. 매매 시스템 & 리스크 관리"))

    story.append(p(S_H2, "매매 플로우"))
    story.append(p(S_BODY,
        "추천 > 매수 확인(ATR 손절/목표 + 분할매수 + 확신도 표시) "
        "> 자동 모니터링 > 트레일링 스탑 > 단계적 익절 > 자동 복기 > 학습"))

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
        "WebSocket 75종목 실시간 체결/호가 스트리밍",
        "08:50 자동 연결, 15:35 자동 해제",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 11. 크로스마켓 & 유가
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "11. 크로스마켓 & 유가 분석"))

    story.append(p(S_H2, "크로스마켓 임팩트 (v10.4)"))
    story.append(p(S_BODY,
        "18개 글로벌 자산을 추적하여 한국 시장 영향도를 실시간 분석합니다."))
    story.append(make_table(
        ["자산군", "추적 대상"],
        [
            ["미국 주식", "S&P500, 나스닥, 다우, 러셀2000"],
            ["미국 국채", "2년, 10년, 30년 금리"],
            ["통화", "원/달러, 엔/달러, 달러인덱스"],
            ["원자재", "WTI, 금, 구리"],
            ["아시아", "닛케이225, 항셍지수"],
            ["변동성", "VIX, VKOSPI"],
        ],
        col_widths=[35*mm, 140*mm],
    ))

    story.append(p(S_H2, "유가 분석 엔진 (v10.2)"))
    story.append(make_highlight_table([
        ("🛢", "레짐 분류", "안정/상승/하락/급등/급락/변동성확대 자동 분류"),
        ("📊", "7종 시그널", "추세/변동성/스프레드/52주위치/이평선/모멘텀/균형"),
        ("🏭", "섹터 임팩트", "정유/항공/해운/화학/유틸 등 종목별 영향도"),
        ("🌍", "이벤트 추적", "OPEC/EIA/지정학 이벤트 자동 감지"),
    ]))

    # ═══════════════════════════════════════════════════════
    # 12. 리포팅
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "12. 리포팅 & 데이터"))

    story.append(p(S_H2, "자동 리포트"))
    story.append(make_table(
        ["리포트", "시간", "내용"],
        [
            ["모닝 브리핑", "07:30", "글로벌 + ML예측 + 매니저별 분석 + 유가"],
            ["AI 토론 요약", "09:30/14:00", "4매니저 합의/반대의견/목표가"],
            ["일간 PDF", "16:00", "4페이지 전문가급 (시장/분석/포트폴리오/전략)"],
            ["일일 합성", "21:30", "YouTube+리포트+칼럼 학습 종합"],
            ["자가진단", "21:00", "AI 활동수, 승률, 결핍 분석, 개선 제안"],
            ["주간 학습", "토 09:00", "이번 주 학습 + 다음 주 전략 조정"],
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

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 13. 텔레그램 UX
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "13. 텔레그램 UX"))

    story.append(p(S_H2, "메인 메뉴"))
    story.append(make_table(
        ["버튼", "기능"],
        [
            ["분석", "종목 스캔, 멀티분석, 전략 필터"],
            ["시황", "실시간 시장 + AI 코멘터리 + 유가"],
            ["잔고", "보유종목 현황 + 손익"],
            ["즐겨찾기", "6탭 대시보드 (스캘프/스윙/포지션/장기/미분류/보유)"],
            ["AI질문", "자유 질문 (Claude AI)"],
            ["리포트", "PDF 리포트, 주간 보고, 일일 합성"],
            ["더보기", "섹터 딥다이브, 비주얼 차트, 관리, /learning"],
            ["사용설명서", "v11.0 기능 가이드"],
        ],
        col_widths=[30*mm, 145*mm],
    ))

    story.append(p(S_H2, "주요 슬래시 커맨드"))
    story.append(make_table(
        ["명령어", "설명"],
        [
            ["/start", "봇 시작 + 온보딩"],
            ["/ml", "ML 예측 결과 (BUY/NEUTRAL/AVOID + SHAP)"],
            ["/learning", "학습 현황 (예산/채널/합성/애널리스트)"],
            ["/risk", "포트폴리오 리스크 분석 (VaR + 스트레스)"],
            ["/surge", "실시간 급등 종목 스캐너"],
            ["/finance", "종목 재무 분석"],
            ["/consensus", "AI 토론 합의 조회"],
            ["/backtest", "전략 백테스트"],
            ["/admin", "관리자 패널"],
            ["/claude", "Claude Code 원격 세션"],
        ],
        col_widths=[35*mm, 140*mm],
    ))

    story.append(p(S_H2, "버전 히스토리"))
    story.append(make_table(
        ["버전", "핵심 변경"],
        [
            ["v11.0", "ML 앙상블 + YouTube 24채널 + 19 애널리스트 + 일일합성"],
            ["v10.4", "크로스마켓 + YouTube 심화학습 + 리포트 크롤"],
            ["v10.2", "유가 분석 엔진 + OPEC/EIA 이벤트"],
            ["v10.0", "ML 전환 (LGB+XGB+LSTM) + 공매도 분석"],
            ["v9.6", "추천 추적 학습 + ATR 손절/익절 + 확신도 사이징"],
            ["v9.4", "AI 3라운드 토론 + 멀티분석"],
            ["v9.0", "한국형 리스크 + 산업 밸류체인 + ETF 추적"],
        ],
        col_widths=[25*mm, 150*mm],
    ))

    # ── 마지막 ──
    story.append(Spacer(1, 10*mm))
    story.append(p(S_SMALL, "━" * 80))
    story.append(p(S_SMALL,
        f"K-Quant System v11.0.0 | 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        "Co-Authored-By: Claude Opus 4.6"))

    doc.build(story)
    return out_path


if __name__ == "__main__":
    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports",
        f"K-Quant_v110_Features_{datetime.now().strftime('%Y%m%d')}.pdf",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    build_pdf(out)
    print(f"PDF 생성 완료: {out}")
