# K-Quant System v11.0

한국 주식 AI 퀀트 트레이딩 시스템 + 4매니저 다중 전략 + 텔레그램 봇

## 핵심 특징

- 4명의 AI 투자매니저 (리버모어/오닐/린치/버핏 페르소나)
- 10가지 투자 전략 (A~J), 58개 ML 피처
- 앙상블 ML 예측 (LightGBM + XGBoost + LSTM)
- 10종 기술적 차트 (MACD, 수급, 공매도, 주봉, 밸류에이션 밴드 등)
- 18개 글로벌 자산 크로스마켓 분석 (v10.4)
- 유가 분석 + 레짐 분류 (v10.2)
- 한국형 리스크 게이지 (신용잔고/ETF/환율/공매도 7요인)
- KIS 증권 API 자동매매 연동
- Claude AI 브리핑 + 매니저 토론 + 자기반성
- YouTube 24채널 자동 학습 + Gemini Flash/Claude Haiku 2단계 분석 (v11.0)
- 19명 애널리스트 추적 + 네이버 칼럼 크롤링 (v11.0)
- 일일 $1 학습 예산 관리 (v11.0)
- 170+ Python 모듈, 30+ DB 테이블, 50+ 스케줄 잡

## 4매니저 시스템

| 매니저 | 페르소나 | 투자시계 | 특성 |
|--------|----------|----------|------|
| 리버모어 | 단타의 전설 | 1~3일 (scalp) | 거래량+모멘텀, 빠른 진입/퇴출 |
| 오닐 | CANSLIM | 1~2주 (swing) | RSI 과매도+기관 수급, 반등 포착 |
| 린치 | 성장주 사냥꾼 | 1~3개월 (position) | PEG+ROE+매출성장, 숨겨진 성장 |
| 버핏 | 가치투자 거장 | 3개월+ (long_term) | 해자+FCF+배당, 장기 복리 |

## ML 시스템 (v10.3+)

| 항목 | 설명 |
|------|------|
| 목적 | 5일내 +3% 상승 확률 예측 (이진분류) |
| 앙상블 | LightGBM + XGBoost + LSTM |
| 피처 | 58개 (기술12+모멘텀6+매크로4+수급6+센티먼트4+크로스마켓8+기타18) |
| 학습 | 평일 22:00 점진 / 일요일 03:00 전체 재학습 |
| 저장 | `models/lgb_model.txt`, `xgb_model.json` |
| 피처DB | `data/features.db` (일별 전종목 58피처 축적) |

## 학습 파이프라인 (v11.0)

### YouTube 학습 (24채널)
- **Tier1** (Gemini Flash): 전체 영상 빠른 스크리닝 (~$0.0004/건)
- **Tier2** (Claude Haiku): 상위 15건 심화 분석 (~$0.007/건)
- 5회/일 배치: 06:30, 08:00, 12:00, 17:00, 21:00
- YouTube 자동자막 추출 (`youtube_transcript_api`)

### 채널 목록

**기존 11개**: 삼프로TV, 한국경제TV, SBS Biz, 이데일리TV, MTN머니투데이, 매일경제TV, 토마토증권통, 뉴욕주민, 월가아재, 박곰희TV, 슈카월드

**신규 13개**: 증시각도기TV, 주식단테, 시윤주식, 경제원탑, 키움증권채널K, 삼성증권POP, KB증권깨비마블TV, 한국투자증권BanKIS, 하나증권하나TV, 부읽남, 신사임당, 연합뉴스경제TV, 오선의미국증시라이브

### 애널리스트 추적 (19명)
YouTube 제목/네이버 칼럼에서 이름 감지 시 자동 Tier2 승격

### 예산 관리
- 일일 한도: $1.00 (80% 경고, 100% 학습 중단)
- `core/budget_manager.py` — `api_usage_log` 테이블 기반 추적

## 차트 시스템

| 차트 | 설명 |
|------|------|
| 기본 | 캔들스틱 + BB(20,2) + MA(5/20/60) + RSI(14) |
| 확장 | +MACD(12,26,9) + 수급 오버레이 + 공매도 + 시그널선 |
| 주봉 | 주봉 리샘플 + 매집점수(0-100) + 세력패턴 |
| MTF | 일봉(60일) + 주봉(26주) 멀티타임프레임 |
| 밸류에이션 | PER 밴드(8x~25x) + 컨센서스 적정가 |
| 리스크 게이지 | 0-100 다이얼 + 7요인 바 차트 |
| 섹터 비교 | 종목간 3M 수익률 + RSI 비교 |

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/start` | 메인 메뉴 |
| `/goal` | 300억 목표 대시보드 |
| `/learning` | 학습 현황 (예산+ML+YouTube+애널리스트) |
| `/ml` | ML 모델 상태 |
| `/risk` | 리스크 현황 |
| `/short` | 공매도/레버리지 분석 |
| `/health` | 시스템 상태 |
| `/performance` | 실전 성과 |
| `/stats` | 추천 스코어카드 |
| `/surge` | 급등 감지 |
| `/accumulation` | 매집 감지 |
| `/backtest` | 백테스트 실행 |
| `/optimize` | 포트폴리오 최적화 |
| `/scenario` | 시나리오 분석 |
| `/multi` | 멀티에이전트 분석 |
| `/consensus` | 합의 서베이 |
| `/finance` | 재무 진단 |
| `/balance` | 잔고 확인 |
| `/register` | 매수 등록 |
| `/claude` | 원격 Claude Code |
| `/admin` | 관리자 메뉴 |

## 메뉴 시스템

```
┌──────────────────────────────┐
│  📊 분석   │  📈 시황       │
├──────────────────────────────┤
│  💰 잔고   │  ⭐ 즐겨찾기    │
├──────────────────────────────┤
│  💻 클로드  │  🤖 에이전트   │
├──────────────────────────────┤
│  💬 AI비서  │  📋 리포트     │
├──────────────────────────────┤
│         ⚙️ 더보기           │
└──────────────────────────────┘
```

**인라인 메뉴**: 종목상세, 매수/매도, AI 토론, 시나리오, 차트, 주봉, 리스크, 섹터비교, 유가상세, 멀티에이전트 등 60+ 콜백 액션

## 스케줄러 (50+ 잡)

### 아침 (06:00~09:30)

| 시간 | 잡 | 설명 |
|------|-----|------|
| 06:00 | daily_directive | 운영 지침 + 자동 분류 |
| 06:30 | youtube_batch_1 | YouTube Tier1 스크리닝 |
| 07:00 | us_premarket | 미국 프리마켓 요약 |
| 07:10 | oil_analysis | 유가 분석 + 레짐 |
| 07:15 | cross_market | 18개 글로벌 자산 분석 |
| 07:30 | morning_briefing | AI 모닝 브리핑 |
| 07:50 | buy_planner | 매니저별 매수 계획 |
| 08:00 | youtube_batch_2 | YouTube Tier1 + 센티먼트 |
| 08:10 | column_crawl_am | 네이버 칼럼 수집 |
| 08:20 | report_crawl | 증권사 리포트 |
| 08:50 | ws_connect | KIS 웹소켓 연결 |
| 09:30 | auto_debate | 매니저 AI 토론 |

### 장중 (09:00~15:30)

| 주기 | 잡 | 설명 |
|------|-----|------|
| 1분 | intraday_monitor | 실시간 모니터링 |
| 1분 | macro_refresh | 매크로 캐시 |
| 1분 | market_pulse | 시장 펄스 |
| 30~120초 | risk_monitor | 리스크 + 트레일링스톱 |
| 5~15분 | news_monitor | 뉴스 모니터링 |

### 장후 (15:30~17:30)

| 시간 | 잡 | 설명 |
|------|-----|------|
| 15:35 | ws_disconnect | 웹소켓 해제 |
| 15:40 | eod_risk_report | 장마감 리스크 |
| 16:00 | daily_pdf_report | PDF 일간 리포트 |
| 16:10~16:35 | 데이터 수집 | 수급/프로그램/공매도/신용/ETF/옵션 |
| 17:00 | youtube_batch_4 | YouTube Tier1 |
| 17:10 | column_crawl_pm | 칼럼 수집 |

### 야간 (19:00~23:55)

| 시간 | 잡 | 설명 |
|------|-----|------|
| 21:00 | youtube_batch_5 | YouTube Tier1 + 자가진단 |
| 21:30 | daily_synthesis | 일일 학습 합성 (Flash+Haiku) |
| 22:00 | youtube_tier2_deep | Tier2 심화 (Haiku) |
| 22:00 | ml_daily_update | ML 점진 학습 |
| 23:55 | daily_system_score | 시스템 자가점수 |

### 주간

| 시간 | 잡 | 설명 |
|------|-----|------|
| 토 09:00 | weekly_learning | 주간 학습 |
| 토 09:00 | youtube_weekly_synthesis | Gemini 2.0 Pro 주간 합성 |
| 일 03:00 | lstm_retrain | ML 전체 재학습 |
| 일 10:00 | weekly_ai_report | 주간 AI 리포트 |
| 일 19:00 | weekly_report | 주간 성과 리포트 |

## 경계 모드 (Alert Mode)

| 모드 | 리스크모니터 | 뉴스 | 급등감지 |
|------|------------|------|---------|
| 🟢 Normal | 120초 | 15분 | 3.0% |
| 🟡 Elevated | 60초 | 10분 | 2.0% |
| 🔴 Wartime | 30초 | 5분 | 1.5% |

자동 해제: Wartime → 6시간 → Elevated → 12시간 → Normal

## 빠른 시작

```bash
# 1. 의존성 설치
pip3 install --break-system-packages -r requirements.txt

# 2. 환경변수 설정 (.env)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ANTHROPIC_API_KEY=your_key
GEMINI_API_KEY=your_gemini_key    # v11.0 학습용
KIS_APP_KEY=your_kis_key          # 선택 (자동매매)
KIS_APP_SECRET=your_kis_secret    # 선택

# 3. 실행
./kbot start
```

## kbot CLI

```bash
# 프로세스
./kbot start          # 봇 시작
./kbot stop           # 봇 종료
./kbot restart        # 봇 재시작
./kbot status         # 상태 (PID, 메모리, 가동시간)

# 로그
./kbot logs 30        # 최근 로그 30줄
./kbot errors         # ERROR만 필터
./kbot tail           # 실시간 로그

# 제어 (실행 중인 봇)
./kbot jobs           # 스케줄 작업 목록
./kbot trigger <잡>   # 작업 즉시 실행
./kbot pause <잡>     # 작업 일시중지
./kbot resume <잡>    # 작업 재개
./kbot alert [모드]   # 경계모드 조회/변경

# 정보
./kbot holdings       # 보유종목
./kbot macro          # 매크로 스냅샷
./kbot costs          # API 비용
./kbot market         # 시장 현황
./kbot version        # 버전 정보

# AI
./kbot send "메시지"   # 텔레그램 메시지 전송
./kbot ai "질문"       # AI 질문
./kbot scan [매니저]   # 매니저별 매수 스캔
```

## 원격 관리 (맥북 -> 맥미니)

```bash
# 맥미니에서 1회 실행
sudo bash scripts/setup_remote.sh

# 맥북에서
scp macmini:~/k-quant-system/scripts/kbot-remote ~/bin/kbot
kbot status    # 원격 상태 확인
kbot restart   # 원격 재시작
kbot sync      # 데이터 동기화
```

## 프로젝트 구조

```
src/kstock/ (170+ 파일)
├── app.py                       # 메인 엔트리포인트
├── bot/                         # 텔레그램 봇
│   ├── bot.py                   # KQuantBot (7개 mixin 합성)
│   ├── ai_router.py             # AI 라우터 (Haiku/Sonnet/Flash 배분)
│   ├── investment_managers.py   # 4매니저 AI + 성과/토론/발굴
│   ├── context_builder.py       # AI 컨텍스트 빌더
│   ├── chat_handler.py          # AI 대화 핸들러
│   ├── control_server.py        # Unix 소켓 제어 서버
│   └── mixins/                  # 7개 기능별 mixin
│       ├── core_handlers.py     # 콜백 디스패치 + 50+ 스케줄 잡
│       ├── scheduler.py         # 브리핑/모니터링/리포트/학습
│       ├── trading.py           # 매수/매도/매니저 분석
│       ├── commands.py          # 명령어 핸들러
│       ├── admin_extras.py      # 관리 + 차트 + 즐겨찾기
│       ├── menus_kis.py         # 메뉴 + KIS 연동
│       └── remote_claude.py     # 원격 Claude 연동
├── features/
│   ├── technical.py             # 30+ 기술적 지표
│   ├── chart_gen.py             # 10종 차트 생성기
│   └── weekly_pattern.py        # 주봉 매집/세력 탐지
├── signal/                      # 시그널 모듈
│   ├── scoring.py               # 50+ 팩터 스코어링
│   ├── strategies.py            # 10가지 전략 (A~J)
│   ├── korea_risk.py            # 한국형 리스크 (0-100, 7요인)
│   ├── cross_market_impact.py   # 18 글로벌 자산 크로스마켓
│   ├── oil_analysis.py          # 유가 레짐 + 섹터 임팩트
│   ├── industry_ecosystem.py    # 산업 밸류체인
│   ├── etf_flow.py              # ETF 레버리지/인버스
│   └── institutional_tracker.py # 기관 수급 패턴
├── ingest/                      # 데이터 수집
│   ├── global_news.py           # YouTube 24채널 + 19 애널리스트 추적
│   ├── column_crawler.py        # 네이버 칼럼/투자전략 크롤러
│   ├── report_crawler.py        # 증권사 리포트 크롤러
│   ├── macro_client.py          # 매크로 (3-tier 캐시)
│   ├── kis_client.py            # KIS 증권 API
│   ├── yfinance_kr_client.py    # yfinance 한국 종목
│   ├── credit_balance.py        # 신용잔고/예탁금
│   └── naver_finance.py         # 네이버 금융 크롤링
├── ml/                          # 머신러닝
│   ├── predictor.py             # LGB+XGB 앙상블 예측
│   ├── auto_trainer.py          # 자동 학습/재학습
│   ├── lstm_predictor.py        # LSTM 시퀀스 예측
│   └── feature_store.py         # 피처 스토어 (features.db)
├── core/                        # 코어
│   ├── budget_manager.py        # 일일 학습 예산 관리
│   ├── token_tracker.py         # API 토큰/비용 추적
│   └── risk_engine.py           # 리스크 엔진
├── broker/                      # KIS 자동매매
├── store/                       # SQLite (30+ 테이블) + Parquet
├── backtest/                    # 백테스트 엔진
├── report/                      # PDF 보고서
└── ops/                         # 스케줄러, 레이트 리밋
```

## 설정 파일 (config/)

| 파일 | 용도 |
|------|------|
| universe.yaml | 종목 유니버스 (KOSPI/KOSDAQ/ETF) |
| scoring.yaml | 팩터 가중치 (매크로/수급/기술/리스크) |
| kis_config.yaml | KIS API 설정 + 자동매매 안전장치 |
| crisis_events.yaml | 위기 상황 설정 + AI 주입 |
| policy_calendar.yaml | 시장 이벤트 캘린더 (정치/세금/계절) |
| user_goal.yaml | 투자 목표 (금액/기간/전략) |
| user_preference.yaml | 전략 가중치 + 사용자 행동 |

## 기술 스택

- Python 3.9 / python-telegram-bot 20+ / APScheduler
- yfinance + pykrx + KIS OpenAPI (데이터 3중 폴백)
- LightGBM + XGBoost + PyTorch LSTM (앙상블 ML)
- Gemini Flash + Claude Haiku/Sonnet (멀티 AI)
- pandas + numpy + matplotlib (분석 + 차트)
- SQLite (30+ 테이블) + features.db (피처 스토어)
- macOS Mac Mini (상시 운영)

## 버전 히스토리

| 버전 | 주요 변경 |
|------|----------|
| v11.0 | $1/일 학습 파이프라인 (24채널, Gemini Flash, 19명 애널리스트, 일일합성) |
| v10.4 | 크로스마켓 18자산 + YouTube 심화학습 |
| v10.3 | ML 파이프라인 수정 (디스크 저장/로드) |
| v10.2 | 유가 분석 모듈 (WTI/Brent 레짐) |
| v10.0 | ML 앙상블 전환 (LGB+XGB+LSTM, 58피처) |
| v9.0 | 한국형 리스크, 산업 밸류체인, ETF, 주봉 |

## 테스트

```bash
PYTHONPATH=src python3 -m pytest tests/ -q
```
