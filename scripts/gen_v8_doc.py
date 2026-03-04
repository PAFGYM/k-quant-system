#!/usr/bin/env python3
"""K-Quant System v8 기능 설명서 PDF 생성."""

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

# ── 스타일 ──────────────────────────────────────────────────
S_TITLE = ParagraphStyle("Title", fontName=FONT, fontSize=22, textColor=C_DARK,
                         spaceAfter=3*mm, leading=28)
S_H1 = ParagraphStyle("H1", fontName=FONT, fontSize=14, textColor=C_BLUE,
                       spaceBefore=6*mm, spaceAfter=3*mm, leading=18)
S_H2 = ParagraphStyle("H2", fontName=FONT, fontSize=11, textColor=C_ACCENT,
                       spaceBefore=4*mm, spaceAfter=2*mm, leading=14)
S_BODY = ParagraphStyle("Body", fontName=FONT, fontSize=9, textColor=C_DARK,
                        spaceAfter=2*mm, leading=13)
S_BULLET = ParagraphStyle("Bullet", fontName=FONT, fontSize=9, textColor=C_DARK,
                          leftIndent=12, spaceAfter=1*mm, leading=12,
                          bulletIndent=4, bulletFontSize=8)
S_SMALL = ParagraphStyle("Small", fontName=FONT, fontSize=7.5, textColor=colors.gray,
                         spaceAfter=1*mm, leading=10)
S_COVER_TITLE = ParagraphStyle("CoverTitle", fontName=FONT, fontSize=28,
                               textColor=C_DARK, leading=36, spaceAfter=5*mm)
S_COVER_SUB = ParagraphStyle("CoverSub", fontName=FONT, fontSize=14,
                             textColor=C_BLUE, leading=20, spaceAfter=3*mm)
S_TABLE_H = ParagraphStyle("TH", fontName=FONT, fontSize=8.5, textColor=C_WHITE,
                           leading=11)
S_TABLE_D = ParagraphStyle("TD", fontName=FONT, fontSize=8, textColor=C_DARK,
                           leading=11)


def p(style, text):
    return Paragraph(text, style)


def make_table(headers, rows, col_widths=None):
    """테이블 생성."""
    data = [[p(S_TABLE_H, h) for h in headers]]
    for row in rows:
        data.append([p(S_TABLE_D, str(c)) for c in row])

    w = col_widths or [160 / len(headers) * mm] * len(headers)
    t = Table(data, colWidths=w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
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
    story.append(Spacer(1, 60*mm))
    story.append(p(S_COVER_TITLE, "K-Quant System v8"))
    story.append(p(S_COVER_SUB, "한국 주식 AI 자동 분석 & 트레이딩 시스템"))
    story.append(Spacer(1, 10*mm))
    story.append(p(S_BODY, f"작성일: {datetime.now().strftime('%Y년 %m월 %d일')}"))
    story.append(p(S_BODY, "버전: v8.7.0"))
    story.append(p(S_BODY, "플랫폼: macOS (Mac Mini) + Telegram Bot"))
    story.append(Spacer(1, 20*mm))

    # 목차
    story.append(p(S_H1, "목차"))
    toc = [
        "1. 시스템 아키텍처",
        "2. 스케줄러 (19개 자동 작업)",
        "3. 4대 투자 매니저 AI",
        "4. 7대 트레이딩 전략",
        "5. 기술적 분석 엔진",
        "6. AI 시스템",
        "7. 즐겨찾기 & 대시보드",
        "8. 매매 시스템 & KIS 증권 연동",
        "9. 리스크 엔진",
        "10. ML/머신러닝",
        "11. 리포팅 시스템",
        "12. 경계 모드 시스템",
        "13. 데이터 소스 & 인프라",
        "14. 시스템 관리",
        "15. 터미널/CLI 제어",
        "16. 텔레그램 UX",
    ]
    for t in toc:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {t}"))
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 1. 시스템 아키텍처
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "1. 시스템 아키텍처"))
    story.append(p(S_BODY,
        "K-Quant는 Python 기반 모듈러 아키텍처로, 7개 Mixin 클래스를 조합하여 "
        "단일 텔레그램 봇 프로세스에서 실행됩니다."))

    story.append(p(S_H2, "핵심 Mixin 모듈"))
    story.append(make_table(
        ["모듈", "역할"],
        [
            ["CoreHandlersMixin", "초기화, 콜백 라우팅, 글로벌 에러 핸들러"],
            ["MenusKisMixin", "메인 메뉴, KIS 증권 연동, 알림 설정"],
            ["TradingMixin", "포트폴리오 관리, 매수 플래너, 리스크 분석"],
            ["SchedulerMixin", "19개 스케줄 작업, WebSocket, 적응형 모니터링"],
            ["CommandsMixin", "25개 슬래시 커맨드, 종목 스캐닝"],
            ["AdminExtrasMixin", "관리 패널, 즐겨찾기, 에이전트 채팅, 시스템 제어"],
            ["RemoteClaudeMixin", "텔레그램에서 Claude Code CLI 원격 실행"],
        ],
        col_widths=[45*mm, 130*mm],
    ))

    story.append(p(S_H2, "인프라 특징"))
    for item in [
        "57개 SQLite 테이블, 2,149개 테스트 케이스",
        "PID 파일 + 좀비 프로세스 자동 정리, 크래시 시 10초 백오프 자동 재시작",
        "launchd plist macOS 서비스 관리",
        "Unix 소켓 IPC (kbot CLI <-> 봇 프로세스)",
        "Circuit Breaker 패턴 (외부 API 장애 격리)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 2. 스케줄러
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "2. 스케줄러 (19개 자동 작업)"))
    story.append(make_table(
        ["시간(KST)", "작업", "주기"],
        [
            ["03:00", "LSTM 모델 재학습", "일요일"],
            ["06:00", "AI 운영 지침 생성", "매일"],
            ["07:00", "미국 프리마켓 브리핑", "매일"],
            ["07:30", "모닝 브리핑 + 매니저 코멘트", "매일"],
            ["07:50", "프리마켓 매수 플래너", "평일"],
            ["08:00", "센티먼트 분석 / 단기 리뷰 / 스크린샷 리마인더", "매일/평일"],
            ["08:20", "증권사 리포트 크롤링", "평일"],
            ["08:50", "WebSocket 62종목 연결", "평일"],
            ["09:00", "주간 학습 리포트", "토요일"],
            ["14:30", "스캘프 마감 리마인더", "평일"],
            ["15:35", "WebSocket 해제", "평일"],
            ["16:00", "일간 PDF 리포트 (4페이지)", "매일"],
            ["19:00", "주간 리포트", "일요일"],
            ["21:00", "자가진단 리포트", "매일"],
            ["60초마다", "장중 모니터링 / 매크로 갱신 / 마켓 펄스", "반복"],
        ],
        col_widths=[28*mm, 95*mm, 52*mm],
    ))

    story.append(p(S_H2, "적응형 모니터링 (VIX 기반)"))
    story.append(make_table(
        ["구간", "VIX 범위", "장중 모니터링", "마켓 펄스"],
        [
            ["안정", "< 18", "120초", "180초"],
            ["보통", "18-25", "60초", "60초"],
            ["공포", "25-30", "30초", "30초"],
            ["패닉", "> 30", "15초", "15초"],
        ],
        col_widths=[30*mm, 35*mm, 50*mm, 50*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 3. 4대 투자 매니저
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "3. 4대 투자 매니저 AI"))
    story.append(p(S_BODY,
        "각 보유종목은 투자 성향에 맞는 전설적 투자자 AI 매니저에게 배정됩니다. "
        "매니저는 Claude Haiku를 사용하여 종목별 분석과 매수/매도 판단을 수행합니다."))

    story.append(make_table(
        ["매니저", "유형", "투자 철학", "핵심 기준"],
        [
            ["⚡ 제시 리버모어", "스캘프", "단기 모멘텀 돌파",
             "20일 MA 돌파, 거래량 200%+, RSI 40-65, -3% 손절"],
            ["🔥 윌리엄 오닐", "스윙", "CAN SLIM 성장주",
             "EPS 25%+, ROE 17%+, 52주 신고가 -15%, -7% 손절"],
            ["📊 피터 린치", "포지션", "합리적 가치 투자",
             "PEG<1.0, 6가지 분류, PBR/ROE/FCF 중심"],
            ["💎 워런 버핏", "장기", "경제적 해자 투자",
             "ROE 15%+, DCF 내재가치, 30% 안전마진"],
        ],
        col_widths=[32*mm, 18*mm, 38*mm, 87*mm],
    ))

    story.append(p(S_H2, "매니저 기능"))
    for item in [
        "종목별 매니저 자동/수동 배정 (AI 자동분류)",
        "모닝 브리핑 시 매니저별 코멘트 자동 생성",
        "매니저 대시보드: 매니저별 관심종목 클릭 + 매수 스캔",
        "신규 포지션 배정 시 환영 메시지",
        "Recovery Score: 기술적 지표 기반 회복 탄력성 점수 (0-100)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 4. 7대 트레이딩 전략
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "4. 7대 트레이딩 전략"))
    story.append(make_table(
        ["전략", "설명", "보유기간", "목표 수익"],
        [
            ["A", "단기 반등 (과매도 반등)", "3-10일", "+3~7%"],
            ["B", "ETF 레버리지", "1-3일", "+2~5%"],
            ["C", "장기 우량주 (배당+시세)", "6개월-1년", "배당+시세"],
            ["D", "섹터 로테이션", "1-3개월", "섹터 순환"],
            ["E", "글로벌 분산", "장기", "분산 투자"],
            ["F", "모멘텀 추세추종", "2-8주", "+5~10%"],
            ["G", "돌파 매매", "3-10일", "+3~7%"],
        ],
        col_widths=[18*mm, 60*mm, 40*mm, 50*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 5. 기술적 분석
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "5. 기술적 분석 엔진"))

    story.append(p(S_H2, "TechnicalIndicators (18개 지표)"))
    for item in [
        "RSI(14) — Wilder's smoothing / RSI 다이버전스 감지",
        "볼린저밴드(20,2) — %B, 밴드폭, 스퀴즈 감지",
        "MACD(12,26,9) — 히스토그램, 시그널 크로스 (+1 골든/-1 데드)",
        "ATR(14) — 변동성, ATR% (종가 대비)",
        "EMA 50/200 — 골든크로스/데드크로스 감지",
        "주간 트렌드 (up/down/neutral) + 멀티타임프레임 정렬",
        "52주 고점, 20일 고점, 거래량 비율 (현재 vs 20일 평균)",
        "3개월 수익률 (상대 강도), MA 5/20/60/120",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "100점 복합 시그널 스코어"))
    story.append(make_table(
        ["영역", "평가 항목", "비중"],
        [
            ["매크로", "VIX, 환율, 시장 체제", "가변"],
            ["수급", "외인/기관 순매수 일수", "가변"],
            ["펀더멘탈", "재무지표 (EPS, PER, ROE 등)", "가변"],
            ["기술적", "RSI, BB, MACD, ATR", "가변"],
            ["리스크", "변동성, 낙폭, 집중도", "가변"],
            ["멀티에이전트", "4-에이전트 합의 보너스", "+15/+10/+5/-5/-10"],
        ],
        col_widths=[30*mm, 85*mm, 55*mm],
    ))

    story.append(p(S_H2, "특수 스캐너"))
    for item in [
        "텐배거 헌터: 52주 고점 -50%+ / 정책 수혜 / 매출 성장 / 외인 매집 / 거래량 반전",
        "스텔스 매집 감지: 기관 5일+, 외인 5일+, 동시 3일+ 연속 순매수",
        "서지 스캐너: 당일 +5% 급등 종목 실시간 감지",
        "마켓 펄스: 7단계 시장 상태 (STRONG_BULL ~ STRONG_BEAR / REVERSAL)",
        "시그널 앙상블: 전략별 가중 투표 + 엔트로피 합의도 + 신뢰도 A/B/C/D",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 6. AI 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "6. AI 시스템"))

    story.append(p(S_H2, "멀티 AI 라우터"))
    story.append(make_table(
        ["AI", "용도", "특징"],
        [
            ["Claude (Anthropic)", "심층분석, OCR/Vision, 전략 종합",
             "프롬프트 캐싱 90% 비용절감"],
            ["GPT (OpenAI)", "기술분석, 정형 데이터 출력", "구조화된 JSON 응답"],
            ["Gemini (Google)", "뉴스 센티먼트, 한국어 속도", "빠른 처리"],
        ],
        col_widths=[40*mm, 65*mm, 70*mm],
    ))

    story.append(p(S_H2, "4-에이전트 멀티분석"))
    for item in [
        "기술 에이전트 → 차트 지표 분석",
        "펀더멘탈 에이전트 → 재무/밸류에이션",
        "센티먼트 에이전트 → 뉴스/수급 감성",
        "전략가 에이전트 → 위 3개 종합 (Claude Sonnet)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "AI 안전장치"))
    for item in [
        "매도 지시 필터: '전량 매도' → '포지션 점검 검토' 자동 변환",
        "패닉 언어 필터: '긴급', '1초도 망설이지' → 차분한 표현 교체",
        "할루시네이션 가드: 목표가 실시간 검증 (+-10% 벗어나면 [미확인] 태깅)",
        "일일 AI 질문 한도: 50건",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "ChatMemory (RAG)"))
    for item in [
        "8개 토픽 분류 (매수분석/매도분석/시장전망/종목분석/포트폴리오/전략/리스크/섹터)",
        "티커 자동 추출 + 사용자 선호 학습",
        "맥락 인식 검색 (벡터DB 없이 경량 RAG)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "기타 AI 기능"))
    for item in [
        "스크린샷 계좌 인식: Claude Vision으로 증권사 앱 캡처 → 종목/잔고 자동 등록",
        "원격 Claude Code: 텔레그램에서 '클코' → Claude CLI 실행 (10분 타임아웃)",
        "운영 지침 시스템: 매일 06:00 AI가 당일 운영 지침 자동 생성",
        "자동 트레이드 디브리프: 완료된 거래에서 교훈 추출 → 자가학습",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 7. 즐겨찾기
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "7. 즐겨찾기 & 대시보드"))

    story.append(p(S_H2, "6개 탭 대시보드"))
    story.append(make_table(
        ["탭", "설명"],
        [
            ["💰 보유", "실제 보유 종목 (KIS 연동)"],
            ["⚡ 스캘프", "리버모어 스타일 단기 관심종목"],
            ["🔥 스윙", "오닐 스타일 중기 관심종목"],
            ["📊 포지션", "린치 스타일 중장기 관심종목"],
            ["💎 장기", "버핏 스타일 장기 관심종목"],
            ["📌 미분류", "아직 분류되지 않은 종목"],
        ],
        col_widths=[30*mm, 145*mm],
    ))

    story.append(p(S_H2, "대시보드 기능"))
    for item in [
        "📈 매수추천 스캔: 전체 관심종목 기술 지표 스캔 → Recovery Score 순위",
        "  - 🟢 60+점 = 매수 / 🟡 40+점 = 관심 / ⚪ 보류",
        "🤖 자동분류: AI가 종목 특성 분석 후 매니저 자동 배정",
        "종목 상세: 🔍AI진단 / 🤖매니저분석 / 📰뉴스 / 📊차트 / 🔄분류 / 💰매수 / 🗑삭제",
        "AI 진단: 현재가+기술지표+매크로+보유정보 → 매수/매도/관망 판단",
        "모든 화면에 네비게이션 버튼 + 👍👎 피드백",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 8. 매매 시스템
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "8. 매매 시스템 & KIS 증권 연동"))

    story.append(p(S_H2, "매수 시스템"))
    for item in [
        "매수 플래너: 투자금/기간 입력 → AI 추천 + 켈리 기준 포지션 사이징",
        "안전 한도: 단일 주문 15% 한도, 일일 10건, 일일 손실 -3% 한도",
        "신뢰도별 별점: 1~5성 (복합 스코어 기반)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "매도 시스템"))
    story.append(make_table(
        ["유형", "목표 수익", "손절", "전략"],
        [
            ["스캘프", "+3~8%", "-3%", "빠른 익절, 당일~3일"],
            ["스윙", "+8~15%", "-5%", "단계적 익절 (50/30/트레일링)"],
            ["포지션", "+15~30%", "-8%", "중기 보유, 분할 매도"],
            ["장기", "+30~100%", "-15%", "장기 보유 보호 (노이즈 필터링)"],
        ],
        col_widths=[28*mm, 35*mm, 25*mm, 80*mm],
    ))

    story.append(p(S_H2, "KIS 증권 연동"))
    for item in [
        "실전/모의 매매 지원, 시장가/지정가 주문",
        "WebSocket 62종목 실시간 체결/호가 스트리밍",
        "+3% 급등 감지 (30분 쿨다운), 목표가/손절가 실시간 모니터링",
        "08:50 자동 연결, 15:35 자동 해제",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 9. 리스크 엔진
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "9. 리스크 엔진"))
    for item in [
        "Historical VaR (95%, 99%) + Parametric VaR + CVaR (Expected Shortfall)",
        "몬테카를로 시뮬레이션 (VaR, CVaR, 기대수익률)",
        "5개 역사적 위기 시나리오 스트레스 테스트",
        "포트폴리오 집중도 경고 (섹터별 50% 한도, 개별 30% 한도)",
        "리스크 등급 A-F, 커스텀 시나리오 분석 (/scenario)",
        "RiskPolicy: 구간별 손절/포지션 한도 통합 정책",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 10. ML
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "10. ML/머신러닝"))
    story.append(make_table(
        ["모델", "방식", "특징"],
        [
            ["LightGBM + XGBoost", "앙상블 분류",
             "30개 피처, 5영업일 +3% 예측, Optuna 최적화"],
            ["BiLSTM + Attention", "시퀀스 예측",
             "PyTorch, 20-step, 일요일 자동 재학습"],
            ["센티먼트 분석", "뉴스 감성 분류",
             "Claude/Gemini 기반, 정확도 추적"],
        ],
        col_widths=[40*mm, 35*mm, 100*mm],
    ))
    story.append(p(S_BODY,
        "모든 ML 라이브러리는 선택적 의존성 — 미설치 시 중립 50% 폴백으로 graceful degradation."))

    # ═══════════════════════════════════════════════════════
    # 11. 리포팅
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "11. 리포팅 시스템"))
    story.append(make_table(
        ["리포트", "시간", "내용"],
        [
            ["모닝 브리핑", "07:30", "글로벌 시장 + 보유종목 + 매니저 코멘트"],
            ["일간 PDF", "16:00", "4페이지 전문가급 (시장/분석/포트폴리오/전략)"],
            ["자가진단", "21:00", "AI 질문수, 승률, 결핍 분석, 개선 제안"],
            ["주간 학습", "토 09:00", "이번 주 학습 + 다음 주 전략 조정"],
            ["주간 리포트", "일 19:00", "주간 종합 성과"],
            ["30억 대시보드", "/goal", "목표 진행률 + 마일스톤 추적"],
        ],
        col_widths=[35*mm, 22*mm, 118*mm],
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 12. 경계 모드
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "12. 경계 모드 시스템"))
    story.append(make_table(
        ["모드", "트리거", "모니터링", "급등 기준"],
        [
            ["🟢 일상", "기본 상태", "120초", "3%"],
            ["🟡 긴장", "VIX 상승, 뉴스 키워드", "60초", "2%"],
            ["🔴 전시", "전쟁/미사일/계엄/서킷브레이커", "30초", "1.5%"],
        ],
        col_widths=[25*mm, 60*mm, 40*mm, 40*mm],
    ))
    story.append(p(S_BODY,
        "자동 에스컬레이션 (뉴스 키워드 매칭) + 자동 해제 (전시 6시간/긴장 12시간 안정 후)."))

    # ═══════════════════════════════════════════════════════
    # 13. 데이터 소스
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "13. 데이터 소스 & 인프라"))

    story.append(p(S_H2, "가격 데이터 우선순위"))
    story.append(make_table(
        ["순위", "소스", "지연", "매수 결정"],
        [
            ["1", "KIS API 실시간", "실시간", "허용"],
            ["2", "스크린샷 OCR (Claude Vision)", "수동", "허용"],
            ["3", "버튼 기록", "수동", "허용"],
            ["4", "yfinance", "~15분", "차단"],
            ["5", "네이버 금융", "~20분", "차단"],
        ],
        col_widths=[18*mm, 55*mm, 35*mm, 55*mm],
    ))
    story.append(p(S_SMALL, "* 지연 데이터 소스(yfinance/네이버)로는 신규 매수 결정 차단"))

    story.append(p(S_H2, "매크로 3-tier 캐시"))
    for item in [
        "Tier 1: 메모리 캐시 (0ms)",
        "Tier 2: SQLite 캐시 (즉시)",
        "Tier 3: yfinance 라이브 (백그라운드 갱신)",
        "데이터: VIX, S&P500, 나스닥, 코스피, 코스닥, 원/달러, 미10년, BTC, 금, 공포탐욕지수",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(p(S_H2, "추가 데이터 소스"))
    for item in [
        "DART 공시 API (재무제표, 5% 보고서)",
        "증권사 리포트 크롤링 (네이버 금융)",
        "글로벌 뉴스 RSS (위기 감지, 영문 자동 번역)",
        "정책/관세 트래커 (무역전쟁/정부 정책 이벤트)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ═══════════════════════════════════════════════════════
    # 14. 시스템 관리
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "14. 시스템 관리"))

    story.append(p(S_H2, "시스템 점수 (100점)"))
    story.append(make_table(
        ["항목", "배점"],
        [
            ["시그널 정확도", "25점"],
            ["매매 성과", "25점"],
            ["알림 정확도", "15점"],
            ["자가학습 활동", "15점"],
            ["비용 효율", "10점"],
            ["시스템 안정성", "10점"],
        ],
        col_widths=[80*mm, 80*mm],
    ))

    story.append(p(S_H2, "관리 기능"))
    for item in [
        "관리 패널: 봇 상태 / 에러 로그 / 운영 지침 / 보안 감사 / API 비용",
        "API 비용 추적: 건별 토큰 로깅, 월간 비용 리포트 (KRW 환산)",
        "텔레그램 시스템 제어: 재시작 / 리소스 / 에러 로그 / 스케줄러",
        "피드백 루프: 👍👎 → 시그널 승률 자동 학습 → 프롬프트 개선",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════
    # 15. CLI
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "15. 터미널/CLI 제어"))

    story.append(p(S_H2, "kbot CLI"))
    story.append(make_table(
        ["명령어", "설명"],
        [
            ["kbot start|stop|restart", "봇 프로세스 관리"],
            ["kbot status", "PID, 가동시간, 메모리"],
            ["kbot logs [N] / errors [N]", "로그/에러 조회"],
            ["kbot tail", "실시간 로그 모니터링"],
            ["kbot send \"메시지\"", "텔레그램 메시지 전송"],
            ["kbot ai \"질문\"", "AI에게 질문"],
            ["kbot scan", "매수 추천 스캔"],
            ["kbot balance / market / macro", "잔고/시장/매크로 조회"],
            ["kbot jobs / trigger / pause / resume", "스케줄러 작업 제어"],
            ["kbot alert [normal|elevated|wartime]", "경계 모드 변경"],
            ["kbot scores / costs", "시스템 점수 / API 비용"],
            ["kbot claude \"프롬프트\"", "Claude Code 실행"],
        ],
        col_widths=[55*mm, 120*mm],
    ))

    story.append(p(S_H2, "tg.py (스크립트)"))
    story.append(p(S_BODY,
        "python3 scripts/tg.py --status | --balance | --market | --macro | --holdings | --ai \"질문\" | --restart"))

    # ═══════════════════════════════════════════════════════
    # 16. 텔레그램 UX
    # ═══════════════════════════════════════════════════════
    story.append(p(S_H1, "16. 텔레그램 UX"))

    story.append(p(S_H2, "메인 메뉴"))
    story.append(make_table(
        ["버튼", "기능"],
        [
            ["📊 분석", "멀티분석, 서지 스캐너, 스윙, 전략별 필터"],
            ["📈 시황", "실시간 시장 현황 + AI 코멘터리"],
            ["💰 잔고", "보유종목 현황 + 손익"],
            ["⭐ 즐겨찾기", "6탭 대시보드 + 매수추천 + AI진단"],
            ["💻 클로드", "원격 Claude Code 실행"],
            ["🤖 에이전트", "버그 리포트, 기능 요청, 질문"],
            ["💬 AI질문", "자유 질문 (일 50건)"],
            ["📋 리포트", "PDF 리포트, 주간 보고"],
            ["⚙️ 더보기", "관리 패널, 설정, 알림"],
        ],
        col_widths=[30*mm, 145*mm],
    ))

    story.append(p(S_H2, "25개 슬래시 커맨드"))
    story.append(p(S_BODY,
        "/goal /finance /consensus /backtest /optimize /setup_kis /short "
        "/future /history /risk /health /performance /scenario /ml /multi "
        "/surge /feedback /stats /accumulation /register /balance /admin "
        "/claude /start 등"))

    story.append(p(S_H2, "UX 패턴"))
    for item in [
        "safe_edit_or_reply: 메시지 수정 실패 시 자동 폴백",
        "모든 화면에 네비게이션 버튼 (dead-end 제거)",
        "👍👎 피드백 버튼 (주요 기능 하단)",
        "종목명 입력 → 자동 감지 → 분석/즐겨찾기/관심 버튼",
        "페이지네이션 (긴 목록 분할)",
    ]:
        story.append(p(S_BULLET, f"<bullet>&bull;</bullet> {item}"))

    # ── 마지막 페이지 ──
    story.append(Spacer(1, 15*mm))
    story.append(p(S_SMALL, "━" * 80))
    story.append(p(S_SMALL,
        f"K-Quant System v8.7.0 | 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        "Co-Authored-By: Claude Opus 4.6"))

    doc.build(story)
    return out_path


if __name__ == "__main__":
    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports",
        f"K-Quant_v8_Features_{datetime.now().strftime('%Y%m%d')}.pdf",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    build_pdf(out)
    print(f"PDF 생성 완료: {out}")
