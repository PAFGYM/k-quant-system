# K-Quant System v2.0

한국 주식 & ETF 자동 분석 시스템 + 다중 전략 매매 타이밍 알림

## 주요 기능

- 5가지 투자 전략 (단기반등/ETF레버리지/장기우량주/섹터로테이션/글로벌분산)
- yfinance 실제 데이터 기반 분석 (한국 종목 .KS/.KQ)
- 100점 스코어링 + 장기 투자 별도 스코어링
- 시장 상황별 자동 전략 전환 (공격/균형/방어 모드)
- 신호등 매매 알림 (자동 발송)
- 추천 성과 자동 추적 + 백테스트 엔진
- Claude API 일일 브리핑 (선택)
- 버튼 기반 텔레그램 UX

## 유니버스

- 개별 우량주 40종목 (삼성전자, SK하이닉스 등)
- 코스피200 ETF (KODEX 200, 레버리지, 인버스 등)
- 섹터 ETF (반도체, 2차전지, 바이오)
- 해외/원자재 ETF (미국 S&P500, 나스닥100, 골드)
- 배당 ETF (TIGER 배당성장, KODEX 배당가치)

## 5가지 전략

| 전략 | 대상 | 기간 | 목표 |
|---|---|---|---|
| A 단기반등 | 개별 종목 | 3~10일 | +3~7% |
| B ETF레버리지 | 레버리지/인버스 ETF | 1~3일 | +2~5% |
| C 장기우량주 | 배당 ETF, 우량주 | 6개월~1년 | 배당+시세 |
| D 섹터로테이션 | 섹터 ETF | 1~3개월 | 시장 초과 |
| E 글로벌분산 | 미국 ETF, 골드 | 장기 | 분산+시세 |

## 빠른 시작

```bash
# 1. 의존성 설치
pip3 install --break-system-packages -r requirements.txt

# 2. 환경변수 설정 (.env)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ANTHROPIC_API_KEY=your_key  # 선택 (Claude 브리핑용)

# 3. 실행
PYTHONPATH=src python3 -m kstock.app
```

## 스케줄러

| 시간 (KST) | 작업 | 설명 |
|---|---|---|
| 08:45 | 오전 브리핑 | 글로벌 시장 + AI 브리핑 (Claude) |
| 09:00~15:30 | 장중 모니터링 | 5분마다 매수/매도 조건 체크 |
| 16:00 | 장 마감 리포트 | Top 10 + 추천 가격 업데이트 |

## 텔레그램 봇 메뉴

| 버튼 | 기능 |
|---|---|
| 실시간 알림 | 최근 매수/매도 알림 확인 |
| 오늘의 추천종목 | 스코어링 + 전략 태그 |
| 시장현황 | S&P, VIX, 환율, BTC, 금 + 모드 |
| 내 포트폴리오 | 보유 종목 수익률 추적 |
| 추천 성과 | 진행중/완료/관심 현황 |
| 전략별 보기 | 5가지 전략별 추천 필터 |
| 시스템상태 | 스케줄러 상태 |
| 도움말 | 사용법 |

## 커맨드

- `/start` - 시작
- `/backtest 005930` - 백테스트 실행

## 프로젝트 구조

```
src/kstock/
├── app.py                  # 메인 엔트리포인트
├── bot/
│   ├── bot.py              # 텔레그램 봇 (메뉴/콜백/스케줄)
│   └── messages.py         # 메시지 포맷팅
├── ingest/
│   ├── kis_client.py       # 한국 주식 (mock)
│   ├── macro_client.py     # 매크로 (yfinance)
│   └── yfinance_kr_client.py  # 한국 종목 실데이터 (yfinance)
├── features/
│   └── technical.py        # 기술적 지표 (RSI, BB, MACD, ATR)
├── signal/
│   ├── scoring.py          # 100점 스코어링
│   ├── strategies.py       # 5가지 전략 시스템
│   ├── long_term_scoring.py # 장기 투자 스코어
│   └── sell_reason.py      # 외인 매도 분류
├── backtest/
│   └── engine.py           # 백테스트 엔진
├── store/
│   ├── sqlite.py           # SQLite (포트폴리오/추천/알림)
│   └── parquet_store.py    # Parquet OHLCV 저장
└── ops/
    ├── scheduler.py        # APScheduler 래퍼
    └── rate_limit.py       # API 레이트 리밋
```

## 테스트

```bash
PYTHONPATH=src python3 -m pytest tests/ -v
```

## macOS 자동 시작 (launchd)

```bash
cp com.kquant.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kquant.bot.plist
```

## 기술 스택

- Python 3.9+
- python-telegram-bot 20+
- APScheduler 3.10+
- yfinance (실시간 글로벌 + 한국 종목 데이터)
- pandas + numpy (기술적 분석)
- SQLite (메타데이터)
- Claude API (선택, AI 브리핑)
