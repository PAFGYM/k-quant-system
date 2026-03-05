# K-Quant v9.2 아키텍처

## Mixin 구조
KQuantBot = CoreHandlers + MenusKis + Trading + Scheduler + Commands + AdminExtras + RemoteClaude
- src/kstock/bot/bot.py: 메인 클래스 (7개 mixin 합성)
- src/kstock/bot/mixins/core_handlers.py: 콜백 dispatch + schedule_jobs() (45개 잡)
- src/kstock/bot/mixins/scheduler.py: 스케줄 잡 구현 + WebSocket 콜백
- src/kstock/bot/mixins/trading.py: 매수 플래너, 리스크 분석, 매도 가이드, 매니저 토론
- src/kstock/bot/mixins/menus_kis.py: 메뉴 + KIS 증권 연동
- src/kstock/bot/mixins/commands.py: 명령어 핸들러 + 4매니저 추천
- src/kstock/bot/mixins/admin_extras.py: 관리자 기능 + 10종 차트 + 즐겨찾기
- src/kstock/bot/mixins/remote_claude.py: 원격 Claude 연동

## 주요 모듈 (174 소스 파일, 10개 패키지)
- kstock/bot/: 텔레그램 봇 + AI 대화 + 4매니저 + 제어 서버
- kstock/core/: 리스크 엔진, 시나리오 분석, 투자자 프로필, 매도 플래너
- kstock/backtest/: 백테스트 엔진 (TradeCosts, portfolio_backtest)
- kstock/ml/: LSTM 예측, ML 예측, 감성 분석, 피처 스토어
- kstock/signal/: 37개+ 시그널 모듈 (한국리스크, 산업생태계, ETF흐름, 기관추적)
- kstock/features/: 기술적 지표(30+), 차트 생성(10종), 주봉 패턴
- kstock/ingest/: 데이터 수집 (macro 3-tier캐시, KIS WebSocket, yfinance, Naver)
- kstock/broker/: KIS 증권 주문 관리
- kstock/store/: SQLite (74 테이블) + Parquet
- kstock/report/: PDF 보고서 생성
- kstock/ops/: 스케줄러, 레이트 리밋

## 4매니저 AI 시스템 (v9.1)
- investment_managers.py: 4매니저 정의, 임계값, 분석/추천/토론/반성
- 성과 피드백 루프: DB 매매 이력 → 승률/교훈 → AI 프롬프트 주입
- 레짐 가중치: VIX 기반 calm/normal/fear/panic → 매니저별 영향력 조절
- 크로스매니저 컨센서스: 2명+ 동시 추천 종목 감지
- 능동 발굴: 매니저별 기준으로 신규 종목 자동 탐색 (평일 13:00)
- 주간 자기반성: 성과 분석 + 전략 조정 제안 (일요일 19:30)
- 임계값 자동 조정: 성과 기반 손절/익절 튜닝 제안

## 차트 시스템 (v9.2)
- chart_gen.py: 7개 async 함수
  - generate_stock_chart(): 기본 3패널 (캔들+BB, 거래량, RSI)
  - generate_full_chart(): 확장 4-5패널 (MACD+수급+공매도+시그널선+다이버전스)
  - generate_weekly_chart(): 주봉 + 매집점수(0-100)
  - generate_mtf_chart(): 일봉+주봉 멀티타임프레임 2열
  - generate_risk_gauge(): 한국형 리스크 반원 다이얼 + 요인 바
  - generate_sector_comparison(): 섹터 내 수익률+RSI 비교
  - generate_valuation_band(): PER 밴드(8x~25x) + 적정가 오버레이
- 텔레그램 연동: fav:chtm → 차트 모드 선택 → fav:ch0~ch4

## 데이터 흐름
1. MacroClient → 1분마다 시장 데이터 갱신 (3-tier: Memory → SQLite → yfinance)
2. MarketPulse → 1분마다 시장 상태 체크 + 동적 알림 간격
3. AI 질문 → build_full_context_with_macro() → Claude API
4. 스케줄러 → 45개 정기 작업
5. WebSocket → 실시간 급등 감지(+3%) + 보유종목 매도 타겟 모니터링
6. 제어 서버 → Unix 소켓 (/tmp/kquant_control.sock) → kbot CLI 원격 제어

## 스케줄 잡 (45개)

### 일일 고정 (33개)
| 시간 | 잡 | 요일 |
|------|-----|------|
| 03:00 | lstm_retrain | 일 |
| 06:00 | daily_directive | 매일 |
| 06:30 | daily_auto_classify | 매일 |
| 07:00 | us_premarket_briefing | 매일 |
| 07:30 | morning_briefing | 매일 |
| 07:50 | premarket_buy_planner | 평일 |
| 08:00 | screenshot_reminder | 월,금 |
| 08:00 | short_term_review | 평일 |
| 08:00 | sentiment_analysis | 매일 |
| 08:20 | report_crawl | 평일 |
| 08:30 | dart_check | 평일 |
| 08:50 | ws_connect | 평일 |
| 09:00 | weekly_learning | 토 |
| 09:05 | sector_rotation_check | 평일 |
| 10:00 | weekly_journal_review | 일 |
| 11:00 | learning_report | 토 |
| 13:00 | manager_discovery | 평일 |
| 14:00 | contrarian_scan | 평일 |
| 14:30 | scalp_close_reminder | 평일 |
| 15:35 | ws_disconnect | 평일 |
| 15:40 | eod_risk_report | 평일 |
| 16:00 | daily_pdf_report | 매일 |
| 16:10 | supply_demand_collect | 평일 |
| 16:15 | program_trading_collect | 평일 |
| 16:15 | short_selling_collect | 평일 |
| 16:20 | credit_balance_collect | 평일 |
| 16:20 | signal_evaluation | 평일 |
| 16:25 | etf_flow_collect | 평일 |
| 19:00 | daily_rating | 평일 |
| 19:00 | weekly_report | 일 |
| 19:30 | manager_reflection | 일 |
| 21:00 | daily_self_report | 매일 |
| 23:55 | daily_system_score | 매일 |

### 반복 (8개)
| 주기 | 잡 |
|------|-----|
| 60초 | intraday_monitor (+ manager_alerts) |
| 60초 | macro_refresh |
| 60초 | market_pulse |
| 동적 | risk_monitor (120~3600초) |
| 동적 | news_monitor (900초) |
| 동적 | us_futures_signal (3600초) |
| 동적 | global_news_collect (1800초) |
| 1800초 | health_check |

### 시작 시 1회 (4개)
| 지연 | 잡 |
|------|-----|
| 2초 | control_server_start |
| 3초 | send_claude_menu |
| 5초 | ws_connect_startup |
| 10초 | startup_auto_classify |

## DB 주요 테이블 (74개)
holdings, investor_profile, holding_analysis, trade_lessons, trade_journal,
macro_cache, chat_history, chat_memory_enhanced, trade_registers, ml_predictions,
portfolio_snapshots, screenshots, orders, surge_stocks, supply_demand,
short_selling, program_trading, credit_balance, etf_flow,
consensus, earnings, financials, signal_performance, system_scores,
api_usage_log, global_news, sector_snapshots, contrarian_signals ...

## 콜백 액션 (61개)
buy, skip, watch_alert, sell_profit, hold_profit, stop_loss,
detail, strat, kis_buy, mgr_debate, multi_run, fav, agent,
bubble, risk, journal, short, orderbook, ctrl ...
