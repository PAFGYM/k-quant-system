# K-Quant System v9.2

한국 주식 AI 퀀트 트레이딩 시스템 + 4매니저 다중 전략 + 텔레그램 봇

## 핵심 특징

- 4명의 AI 투자매니저 (리버모어/오닐/린치/버핏 페르소나)
- 10가지 투자 전략 (A~J), 50+ 팩터 스코어링
- VIX 기반 레짐별 매니저 가중치 자동 조절
- 10종 기술적 차트 (MACD, 수급, 공매도, 주봉, 밸류에이션 밴드 등)
- 한국형 리스크 게이지 (신용잔고/ETF/환율/공매도 7요인)
- KIS 증권 API 자동매매 연동
- Claude AI 브리핑 + 매니저 토론 + 자기반성
- 174개 Python 모듈, 74개 DB 테이블, 45개 스케줄 잡

## 4매니저 시스템

| 매니저 | 페르소나 | 투자시계 | 특성 |
|--------|----------|----------|------|
| 리버모어 | 단타의 전설 | 1~3일 (scalp) | 거래량+모멘텀, 빠른 진입/퇴출 |
| 오닐 | CANSLIM | 1~2주 (swing) | RSI 과매도+기관 수급, 반등 포착 |
| 린치 | 성장주 사냥꾼 | 1~3개월 (position) | PEG+ROE+매출성장, 숨겨진 성장 |
| 버핏 | 가치투자 거장 | 3개월+ (long_term) | 해자+FCF+배당, 장기 복리 |

## 차트 시스템 (v9.2)

| 차트 | 설명 |
|------|------|
| 기본 | 캔들스틱 + BB(20,2) + MA(5/20/60) + RSI(14) |
| 확장 | +MACD(12,26,9) + 수급 오버레이 + 공매도 + 시그널선 |
| 주봉 | 주봉 리샘플 + 매집점수(0-100) + 세력패턴 |
| MTF | 일봉(60일) + 주봉(26주) 멀티타임프레임 |
| 밸류에이션 | PER 밴드(8x~25x) + 컨센서스 적정가 |
| 리스크 게이지 | 0-100 다이얼 + 7요인 바 차트 |
| 섹터 비교 | 종목간 3M 수익률 + RSI 비교 |

## 스케줄러 (주요)

| 시간 (KST) | 작업 | 설명 |
|---|---|---|
| 06:00 | 일일 지침 | 운영 지침 + 자동 분류 |
| 07:00 | 미국 프리마켓 | 전야 미국 시장 요약 |
| 07:30 | 모닝 브리핑 | AI 브리핑 + 매니저별 분석 |
| 07:50 | 매수 플래너 | 매니저별 매수 계획 |
| 09:00~15:30 | 장중 모니터링 | 60초 주기 (스캔+수급+알림) |
| 13:00 | 매니저 발굴 | 매니저별 기준 신규 종목 탐색 |
| 14:30 | 단타 청산 | scalp 종목 청산 리마인더 |
| 15:40 | 장마감 리스크 | EOD 리스크 리포트 |
| 16:00 | 일일 리포트 | PDF 보고서 + 수급/공매도 수집 |
| 19:00(일) | 주간 리포트 | 주간 성과 + 매니저 반성 |
| 21:00 | 자가진단 | 일일 자가 리포트 |

## 빠른 시작

```bash
# 1. 의존성 설치
pip3 install --break-system-packages -r requirements.txt

# 2. 환경변수 설정 (.env)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ANTHROPIC_API_KEY=your_key
KIS_APP_KEY=your_kis_key        # 선택 (자동매매용)
KIS_APP_SECRET=your_kis_secret  # 선택

# 3. 실행
./kbot start
```

## kbot CLI

```bash
./kbot start          # 봇 시작
./kbot stop           # 봇 종료
./kbot restart        # 봇 재시작
./kbot status         # 상태 (PID, 메모리, 가동시간)
./kbot logs 30        # 최근 로그 30줄
./kbot errors         # ERROR만 필터
./kbot tail           # 실시간 로그

./kbot jobs           # 스케줄 작업 목록
./kbot trigger <잡>   # 작업 즉시 실행
./kbot alert [모드]   # 경계모드 (normal/elevated/wartime)
./kbot holdings       # 보유종목
./kbot macro          # 매크로 스냅샷
./kbot costs          # API 비용
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
src/kstock/ (174 파일)
├── app.py                       # 메인 엔트리포인트
├── bot/                         # 텔레그램 봇
│   ├── bot.py                   # KQuantBot (7개 mixin 합성)
│   ├── investment_managers.py   # 4매니저 AI + 성과/토론/발굴
│   ├── context_builder.py       # AI 컨텍스트 빌더
│   ├── chat_handler.py          # AI 대화 핸들러
│   ├── control_server.py        # Unix 소켓 제어 서버
│   └── mixins/                  # 7개 기능별 mixin
│       ├── core_handlers.py     # 콜백 디스패치 + 45개 스케줄 잡
│       ├── scheduler.py         # 브리핑/모니터링/리포트
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
│   ├── korea_risk.py            # 한국형 리스크 (0-100)
│   ├── industry_ecosystem.py    # 산업 밸류체인
│   ├── etf_flow.py              # ETF 레버리지/인버스
│   └── institutional_tracker.py # 기관 수급 패턴
├── ingest/                      # 데이터 수집
│   ├── macro_client.py          # 매크로 (3-tier 캐시)
│   ├── kis_client.py            # KIS 증권 API
│   ├── yfinance_kr_client.py    # yfinance 한국 종목
│   ├── credit_balance.py        # 신용잔고/예탁금
│   └── naver_finance.py         # 네이버 금융 크롤링
├── broker/                      # KIS 자동매매
├── core/                        # 리스크 엔진, 시나리오
├── backtest/                    # 백테스트 엔진
├── ml/                          # LSTM, ML, 감성분석
├── store/                       # SQLite (74 테이블) + Parquet
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

- Python 3.9+ / python-telegram-bot 20+ / APScheduler
- yfinance + pykrx + KIS OpenAPI (데이터 3중 폴백)
- pandas + numpy + matplotlib (분석 + 차트)
- Claude API (Haiku x3 + Sonnet, 4 AI 에이전트)
- SQLite (74 테이블) / LSTM (PyTorch)
- macOS Mac Mini (상시 운영)

## 테스트

```bash
PYTHONPATH=src python3 -m pytest tests/ -q   # 2900+ 테스트
```
