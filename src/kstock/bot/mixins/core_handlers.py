"""Core handlers: init, build_app, routing, screenshot, callbacks."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403


class CoreHandlersMixin:
    def __init__(self) -> None:
        # v3.6: 보안 검증
        startup_security_check()

        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._start_time = datetime.now(KST)
        # Try loading persisted numeric chat_id
        try:
            _saved_id = Path("data/.chat_id").read_text().strip()
            if _saved_id.lstrip("-").isdigit():
                self.chat_id = _saved_id
        except Exception:
            logger.debug("__init__ failed to load persisted chat_id", exc_info=True)
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.kis = KISClient()
        self.yf_client = YFinanceKRClient()
        self.db = SQLiteStore()
        # v6.2.1: 글로벌 DB 참조 설정 (토큰 추적용)
        try:
            from kstock.core.token_tracker import set_db
            set_db(self.db)
        except Exception:
            pass
        self.macro_client = MacroClient(db=self.db)
        self.scoring_config = load_scoring_config()
        self.universe_config = _load_universe()
        self.universe = self.universe_config.get("tickers", [])
        self.all_tickers = _all_tickers(self.universe_config)
        self._last_scan_results: list = []
        self._scan_cache_time: datetime | None = None
        self._sector_strengths: list = []
        self._ohlcv_cache: dict = {}
        # v3.0: KIS broker + data router
        self.kis_broker = KisBroker()
        self.data_router = DataRouter(
            kis_broker=self.kis_broker, yf_client=self.yf_client, db=self.db,
        )
        self._ml_model: dict | None = None
        self._sentiment_cache: dict = {}
        # Phase 8: 실시간 시장 감지 + 매도 계획
        self.market_pulse = MarketPulse()
        self.sell_planner = SellPlanner()
        # v3.6: Multi-AI Router
        self.ai = AIRouter()
        # v3.6: KIS WebSocket (실시간 호가)
        self.ws = KISWebSocket()

    def build_app(self) -> Application:
        app = (
            Application.builder()
            .token(self.token)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("backtest", self.cmd_backtest))
        app.add_handler(CommandHandler("optimize", self.cmd_optimize))
        app.add_handler(CommandHandler("setup_kis", self.cmd_setup_kis))
        app.add_handler(CommandHandler("goal", self.cmd_goal))
        # v3.5: new commands
        app.add_handler(CommandHandler("finance", self.cmd_finance))
        app.add_handler(CommandHandler("consensus", self.cmd_consensus))
        app.add_handler(CommandHandler("short", self.cmd_short))
        app.add_handler(CommandHandler("future", self.cmd_future))
        app.add_handler(CommandHandler("history", self.cmd_history))
        app.add_handler(CommandHandler("risk", self.cmd_risk))
        app.add_handler(CommandHandler("health", self.cmd_health))
        app.add_handler(CommandHandler("performance", self.cmd_performance))
        app.add_handler(CommandHandler("scenario", self.cmd_scenario))
        app.add_handler(CommandHandler("ml", self.cmd_ml))
        app.add_handler(CommandHandler("multi", self.cmd_multi))
        app.add_handler(CommandHandler("surge", self.cmd_surge))
        app.add_handler(CommandHandler("feedback", self.cmd_feedback))
        app.add_handler(CommandHandler("stats", self.cmd_stats))
        app.add_handler(CommandHandler("accumulation", self.cmd_accumulation))
        app.add_handler(CommandHandler("register", self.cmd_register))
        app.add_handler(CommandHandler("balance", self.cmd_balance))
        app.add_handler(CommandHandler("admin", self.cmd_admin))
        app.add_handler(CommandHandler("claude", self.cmd_claude))
        # v3.0: screenshot image handler
        app.add_handler(
            MessageHandler(filters.PHOTO, self.handle_screenshot)
        )
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_menu_text)
        )
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        # 글로벌 에러 핸들러: 오류 발생 시 Claude Code에 자동 수정 요청
        app.add_error_handler(self._on_error_with_auto_fix)
        return app

    @staticmethod
    async def _post_init(app: Application) -> None:
        """Register Telegram menu button commands on startup."""
        from telegram import BotCommand
        await app.bot.set_my_commands([
            BotCommand("start", "메뉴 열기"),
            BotCommand("goal", "30억 목표 대시보드"),
            BotCommand("finance", "재무 진단"),
            BotCommand("consensus", "컨센서스 조회"),
            BotCommand("backtest", "백테스트 실행"),
            BotCommand("optimize", "포트폴리오 최적화"),
            BotCommand("setup_kis", "KIS 증권 연결"),
            BotCommand("short", "공매도/레버리지 분석"),
            BotCommand("future", "미래기술 워치리스트"),
            BotCommand("history", "계좌 추이/솔루션 이력"),
            BotCommand("risk", "리스크 현황"),
            BotCommand("health", "시스템 상태"),
            BotCommand("performance", "실전 성과"),
            BotCommand("scenario", "시나리오 분석"),
            BotCommand("ml", "ML 모델 상태"),
            BotCommand("multi", "멀티 에이전트 분석"),
            BotCommand("surge", "급등주 포착"),
            BotCommand("feedback", "피드백 현황"),
            BotCommand("stats", "추천 성적표"),
            BotCommand("accumulation", "매집 탐지"),
            BotCommand("register", "매수 등록"),
            BotCommand("balance", "잔고 조회"),
            BotCommand("claude", "Claude Code 원격 실행"),
        ])

    async def _post_shutdown(self, app: Application) -> None:
        """Graceful shutdown: WebSocket 정리."""
        try:
            await self.ws.disconnect()
        except Exception as e:
            logger.warning("WebSocket shutdown error: %s", e)

    def schedule_jobs(self, app: Application) -> None:
        jq = app.job_queue
        if jq is None:
            logger.warning("Job queue not available; skipping scheduled jobs")
            return

        self._job_queue = jq
        self._application = app  # WebSocket 콜백에서 bot 접근용

        # v6.2.2: 경계 모드 초기화 (DB에서 복원)
        self.__init_scheduler_state__()

        # v5.9: 매일 06:00 일일 운영 지침 → AI 자율 판단
        jq.run_daily(
            self.job_daily_directive,
            time=dt_time(hour=6, minute=0, tzinfo=KST),
            name="daily_directive",
        )
        # 매수 플래너 (07:50 평일)
        jq.run_daily(
            self.job_premarket_buy_planner,
            time=dt_time(hour=7, minute=50, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="premarket_buy_planner",
        )
        # Phase 10+: 07:00 미국 시장 프리마켓 브리핑 (새벽 미국장 분석)
        jq.run_daily(
            self.job_us_premarket_briefing,
            time=dt_time(hour=7, minute=0, tzinfo=KST),
            name="us_premarket_briefing",
        )
        # Phase 10+: 07:30 모닝 브리핑 (기존 08:45 → 07:30 앞당김)
        jq.run_daily(
            self.job_morning_briefing,
            time=dt_time(hour=7, minute=30, tzinfo=KST),
            name="morning_briefing",
        )
        jq.run_repeating(
            self.job_intraday_monitor,
            interval=60,
            first=30,
            name="intraday_monitor",
        )
        # job_eod_report 제거 → job_daily_pdf_report에 통합 (16:00)
        jq.run_daily(
            self.job_weekly_learning,
            time=dt_time(hour=9, minute=0, tzinfo=KST),
            days=(5,),
            name="weekly_learning",
        )
        # v3.0: screenshot reminder (Mon, Fri 08:00)
        jq.run_daily(
            self.job_screenshot_reminder,
            time=dt_time(hour=8, minute=0, tzinfo=KST),
            days=(0, 4),
            name="screenshot_reminder",
        )
        # v3.0: sentiment analysis (daily 08:00)
        jq.run_daily(
            self.job_sentiment_analysis,
            time=dt_time(hour=8, minute=0, tzinfo=KST),
            name="sentiment_analysis",
        )
        # v3.5: weekly report (Sunday 19:00)
        jq.run_daily(
            self.job_weekly_report,
            time=dt_time(hour=19, minute=0, tzinfo=KST),
            days=(6,),
            name="weekly_report",
        )
        # Phase 8: macro cache warm-up (1분마다 백그라운드 갱신 — 정확도 향상)
        jq.run_repeating(
            self.job_macro_refresh,
            interval=60,
            first=10,
            name="macro_refresh",
        )
        # Phase 8: market pulse (1분마다, 실시간 시장 모니터링)
        jq.run_repeating(
            self.job_market_pulse,
            interval=60,
            first=60,
            name="market_pulse",
        )
        # 통합 장 마감 리포트 (16:00 — 텍스트 요약 + PDF 1건)
        jq.run_daily(
            self.job_daily_pdf_report,
            time=dt_time(hour=16, minute=0, tzinfo=KST),
            name="daily_pdf_report",
        )
        # Phase 10: daily self-report (21:00)
        jq.run_daily(
            self.job_daily_self_report,
            time=dt_time(hour=21, minute=0, tzinfo=KST),
            name="daily_self_report",
        )
        # v3.6.2: 증권사 리포트 자동 수집 (매일 08:20, 평일)
        jq.run_daily(
            self.job_report_crawl,
            time=dt_time(hour=8, minute=20, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="report_crawl",
        )
        # v3.10: DART 공시 체크 (08:30, 평일)
        jq.run_daily(
            self.job_dart_check,
            time=dt_time(hour=8, minute=30, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="dart_check",
        )
        # v3.10: 수급 데이터 수집 (16:10, 평일)
        jq.run_daily(
            self.job_supply_demand_collect,
            time=dt_time(hour=16, minute=10, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="supply_demand_collect",
        )
        # KIS WebSocket: 장 시작 전 연결 (08:50), 장 종료 후 해제 (15:35)
        jq.run_daily(
            self.job_ws_connect,
            time=dt_time(hour=8, minute=50, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="ws_connect",
        )
        jq.run_daily(
            self.job_ws_disconnect,
            time=dt_time(hour=15, minute=35, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="ws_disconnect",
        )
        # 봇 시작 시 장중이면 즉시 WebSocket 연결
        jq.run_once(self.job_ws_connect, when=5, name="ws_connect_startup")
        # v5.9: 봇 시작 시 클로드 메뉴 자동 발송 (Reply Keyboard 복원)
        jq.run_once(self._job_send_claude_menu, when=3, name="send_claude_menu")
        # 14:30 초단기 청산 리마인더
        jq.run_daily(
            self.job_scalp_close_reminder,
            time=dt_time(hour=14, minute=30, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="scalp_close_reminder",
        )
        # 08:00 단기 종목 3일 미달 검토
        jq.run_daily(
            self.job_short_term_review,
            time=dt_time(hour=8, minute=0, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="short_term_review",
        )
        # v3.8: LSTM 재학습 (일요일 03:00)
        jq.run_daily(
            self.job_lstm_retrain,
            time=dt_time(hour=3, minute=0, tzinfo=KST),
            days=(6,),
            name="lstm_retrain",
        )
        # v6.2.2: 경계 모드에 따른 초기 인터벌 설정
        _acfg = self._get_alert_config() if hasattr(self, '_get_alert_config') else {}
        _risk_iv = _acfg.get("risk_interval", 120)
        _news_iv = _acfg.get("news_interval", 900)
        _global_iv = _acfg.get("global_news_interval", 1800)
        _us_iv = _acfg.get("us_futures_interval", 3600)
        if hasattr(self, '_SURGE_THRESHOLD_PCT') and _acfg:
            self._SURGE_THRESHOLD_PCT = _acfg.get("surge_threshold", 3.0)
        # v4.2: 리스크 모니터링 (트레일링 스탑 추적 + 긴급 알림)
        # v6.2.2: 경계 모드별 동적 인터벌
        jq.run_repeating(
            self.job_risk_monitor,
            interval=_risk_iv,
            first=30,
            name="risk_monitor",
        )
        # v4.2: 장 마감 리스크 종합 리포트 (1일 1회, 15:40)
        jq.run_daily(
            self.job_eod_risk_report,
            time=dt_time(hour=15, minute=40, tzinfo=KST),
            days=tuple(range(5)),
            name="eod_risk_report",
        )
        # v4.0: 시스템 헬스체크 (30분마다)
        jq.run_repeating(
            self.job_health_check,
            interval=1800,
            first=60,
            name="health_check",
        )
        # v4.3: 주간 매매일지 AI 복기 (일요일 10:00)
        jq.run_daily(
            self.job_weekly_journal_review,
            time=dt_time(hour=10, minute=0, tzinfo=KST),
            days=(6,),
            name="weekly_journal_review",
        )
        # v4.3: 섹터 로테이션 체크 (평일 09:05)
        jq.run_daily(
            self.job_sector_rotation_check,
            time=dt_time(hour=9, minute=5, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="sector_rotation_check",
        )
        # v4.3: 역발상 시그널 스캔 (평일 14:00)
        jq.run_daily(
            self.job_contrarian_scan,
            time=dt_time(hour=14, minute=0, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="contrarian_scan",
        )
        # v5.5: 매일 저녁 7시 일일 평가 알림
        jq.run_daily(
            self.job_daily_rating,
            time=dt_time(hour=19, minute=0, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="daily_rating",
        )
        # v5.8: 공매도 데이터 수집 (16:15, 평일)
        jq.run_daily(
            self.job_short_selling_collect,
            time=dt_time(hour=16, minute=15, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="short_selling_collect",
        )
        # v5.8: 뉴스 모니터링 — 경계 모드별 동적 인터벌
        jq.run_repeating(
            self.job_news_monitor,
            interval=_news_iv,
            first=120,
            name="news_monitor",
        )
        # v5.9: 미국 선물 신호등 모니터링 — 경계 모드별 동적 인터벌
        jq.run_repeating(
            self.job_us_futures_signal,
            interval=_us_iv,
            first=300,
            name="us_futures_signal",
        )
        # v6.1: 글로벌 뉴스 수집 — 경계 모드별 동적 인터벌
        jq.run_repeating(
            self.job_global_news_collect,
            interval=_global_iv,
            first=60,
            name="global_news_collect",
        )
        # v6.2: 자가 학습 — 신호 적중률 평가 (매일 16:20, 장 마감 후)
        jq.run_daily(
            self.job_signal_evaluation,
            time=dt_time(hour=16, minute=20, tzinfo=KST),
            days=(0, 1, 2, 3, 4),
            name="signal_evaluation",
        )
        # v6.2: 자가 학습 리포트 (매주 토요일 11:00)
        jq.run_daily(
            self.job_learning_report,
            time=dt_time(hour=11, minute=0, tzinfo=KST),
            days=(5,),
            name="learning_report",
        )
        # v6.2.1: 일일 시스템 자가 점수 (매일 23:55)
        jq.run_daily(
            self.job_daily_system_score,
            time=dt_time(hour=23, minute=55, tzinfo=KST),
            name="daily_system_score",
        )
        logger.info(
            "Scheduled: buy_planner(weekday 07:50), us_premarket(07:00), "
            "morning(07:30), intraday(1min), "
            "weekly_learn(Sat 09:00), screenshot(Mon/Fri 08:00), "
            "sentiment(08:00), weekly_report(Sun 19:00), "
            "macro_refresh(1min), market_pulse(1min), "
            "daily_report_pdf(16:00), self_report(21:00), "
            "report_crawl(weekday 08:20), "
            "ws_connect(weekday 08:50), ws_disconnect(weekday 15:35), "
            "scalp_close(weekday 14:30), short_review(weekday 08:00), "
            "lstm_retrain(Sun 03:00), "
            "risk_monitor(%ds), news_monitor(%ds), "
            "global_news(%ds), us_futures(%ds), "
            "surge_threshold(%.1f%%), "
            "alert_mode(%s), "
            "signal_eval(weekday 16:20), learning_report(Sat 11:00), "
            "daily_system_score(23:55) KST",
            _risk_iv, _news_iv, _global_iv, _us_iv,
            self._SURGE_THRESHOLD_PCT,
            self._alert_mode,
        )

    # == 봇 시작 시 클로드 메뉴 자동 발송 ====================================

    async def _job_send_claude_menu(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """v5.9: 봇 시작 시 CLAUDE_MODE_MENU를 자동으로 보내 Reply Keyboard 복원."""
        if not self.chat_id:
            return
        try:
            from kstock.bot.mixins.remote_claude import CLAUDE_MODE_MENU
            await context.bot.send_message(
                chat_id=self.chat_id,
                text="🤖 K-Quant 시스템 재시작 완료",
                reply_markup=CLAUDE_MODE_MENU,
            )
        except Exception as e:
            logger.error("Failed to send Claude menu on startup: %s", e)

    # == Command & Menu Handlers =============================================

    def _persist_chat_id(self, update: Update) -> None:
        """Save numeric chat_id from an incoming update for proactive messaging."""
        if update.effective_chat and update.effective_chat.id:
            numeric_id = str(update.effective_chat.id)
            if self.chat_id != numeric_id:
                self.chat_id = numeric_id
                logger.info("Saved chat_id: %s", numeric_id)
                # Persist to file for future sessions
                try:
                    chat_id_path = Path("data/.chat_id")
                    chat_id_path.parent.mkdir(parents=True, exist_ok=True)
                    chat_id_path.write_text(numeric_id)
                except Exception:
                    logger.debug("_persist_chat_id file write failed", exc_info=True)

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._persist_chat_id(update)
        await update.message.reply_text(format_welcome(), reply_markup=get_reply_markup(context))

    async def cmd_backtest(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: /backtest [종목코드]\n예) /backtest 005930",
                reply_markup=get_reply_markup(context),
            )
            return

        ticker = args[0].strip()
        name = ticker
        market = "KOSPI"
        for item in self.all_tickers:
            if item["code"] == ticker:
                name = item["name"]
                market = item.get("market", "KOSPI")
                break

        await update.message.reply_text(
            f"\U0001f4ca {name} 백테스트 실행 중... 잠시만 기다려주세요."
        )

        try:
            from kstock.backtest.engine import run_backtest, format_backtest_result
            result = run_backtest(ticker, name=name, market=market)
            if result:
                msg = format_backtest_result(result)
                await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
                # Backtest Pro 버튼 추가
                bt_buttons = [
                    [
                        InlineKeyboardButton(
                            "\U0001f4b0 비용 포함 재실행",
                            callback_data=f"bt:withcost:{ticker}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "\U0001f4ca 포트폴리오 백테스트",
                            callback_data="bt:portfolio",
                        ),
                    ],
                ]
                await update.message.reply_text(
                    "\U0001f4ca Backtest Pro",
                    reply_markup=InlineKeyboardMarkup(bt_buttons),
                )
            else:
                msg = f"\u26a0\ufe0f {name} 백테스트 실패\n데이터가 부족하거나 종목코드를 확인해주세요."
                await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Backtest error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 백테스트 중 오류가 발생했어요. 종목코드를 확인하고 다시 시도해주세요.",
                reply_markup=get_reply_markup(context),
            )

    async def cmd_optimize(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        ticker = args[0].strip() if args else "005930"
        name = ticker
        market = "KOSPI"
        for item in self.all_tickers:
            if item["code"] == ticker:
                name = item["name"]
                market = item.get("market", "KOSPI")
                break

        await update.message.reply_text(
            f"\u2699\ufe0f {name} 파라미터 최적화 중...\n시간이 걸릴 수 있습니다."
        )

        try:
            from kstock.backtest.optimizer import run_optimization, format_optimization_result
            result = run_optimization(ticker, market=market)
            if result:
                msg = format_optimization_result(result)
                buttons = [[
                    InlineKeyboardButton(
                        "\u2705 적용하기", callback_data=f"opt_apply:{ticker}",
                    ),
                    InlineKeyboardButton(
                        "\u274c 무시", callback_data="opt_ignore:0",
                    ),
                ]]
                await update.message.reply_text(
                    msg, reply_markup=InlineKeyboardMarkup(buttons),
                )
            else:
                await update.message.reply_text(
                    "\u26a0\ufe0f 최적화 실패 - 데이터 부족",
                    reply_markup=get_reply_markup(context),
                )
        except Exception as e:
            logger.error("Optimize error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 최적화 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.",
                reply_markup=get_reply_markup(context),
            )

    async def cmd_setup_kis(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /setup_kis command."""
        args = context.args or []
        if not args:
            msg = format_kis_setup_guide()
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
            return

        # Parse KIS credentials from message text
        text = update.message.text or ""
        parts = {}
        for line in text.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                parts[key.strip().upper()] = val.strip()

        hts_id = parts.get("KIS_ID", "")
        app_key = parts.get("KIS_KEY", "")
        app_secret = parts.get("KIS_SECRET", "")
        account = parts.get("KIS_ACCOUNT", "")

        if not all([hts_id, app_key, app_secret, account]):
            await update.message.reply_text(
                "\u26a0\ufe0f 형식이 올바르지 않습니다.\n\n"
                "KIS_ID: 홍길동\nKIS_KEY: Pa0knAM6...\n"
                "KIS_SECRET: V9J3YG...\nKIS_ACCOUNT: 12345678-01",
                reply_markup=get_reply_markup(context),
            )
            return

        success = self.kis_broker.save_credentials(hts_id, app_key, app_secret, account)
        if success:
            self.data_router.refresh_source()
            await update.message.reply_text(
                "\u2705 KIS API 연결 완료!\n"
                "모의투자 모드로 설정되었습니다.\n"
                "이제 자동매매가 가능합니다.",
                reply_markup=get_reply_markup(context),
            )
        else:
            await update.message.reply_text(
                "\u274c KIS 연결 실패.\n인증 정보를 확인해주세요.",
                reply_markup=get_reply_markup(context),
            )

    async def handle_screenshot(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle screenshot image messages for account analysis."""
        # Claude Code 대화 모드: 이미지를 Vision API로 분석
        if context.user_data.get("claude_mode"):
            await self._handle_claude_mode_image(update, context)
            return

        # 관리자 모드: 오류 스크린샷 접수
        admin_mode = context.user_data.get("admin_mode")
        if admin_mode:
            if admin_mode == "directive_edit":
                # 지침 수정은 텍스트만 가능
                await update.message.reply_text(
                    "📋 운영 지침은 텍스트로만 수정 가능합니다.\n텍스트를 보내주세요.",
                )
                context.user_data["admin_mode"] = "directive_edit"  # 유지
                return
            context.user_data.pop("admin_mode", None)
            caption = update.message.caption or "이미지 첨부"
            await self._save_admin_report(update, admin_mode, caption, has_image=True)
            return

        if not self.anthropic_key:
            await update.message.reply_text(
                "\u26a0\ufe0f Anthropic API 키가 설정되지 않았습니다.",
                reply_markup=get_reply_markup(context),
            )
            return

        await update.message.reply_text("\U0001f4f8 스크린샷 분석 중... 잠시만 기다려주세요.")

        try:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_bytes = await file.download_as_bytearray()

            parsed = await parse_account_screenshot(bytes(image_bytes), self.anthropic_key)
            holdings = parsed.get("holdings", [])

            # Get previous screenshot for comparison
            prev = self.db.get_last_screenshot()
            comparison = None
            prev_diagnoses = None
            if prev and prev.get("holdings_json"):
                import json
                prev_parsed = json.loads(prev["holdings_json"])
                comparison = compare_screenshots(parsed, {"holdings": prev_parsed})
                prev_holdings = self.db.get_screenshot_holdings(prev["id"])
                if prev_holdings:
                    prev_diagnoses = [
                        {
                            "ticker": h["ticker"], "name": h["name"],
                            "direction": "up" if h.get("profit_pct", 0) > 0 else "down",
                            "confidence": 60,
                        }
                        for h in prev_holdings if h.get("diagnosis")
                    ]

            # Save screenshot to DB
            from kstock.bot.account_reader import compute_portfolio_score
            import json
            import hashlib
            summary = parsed.get("summary", {})
            img_hash = hashlib.md5(bytes(image_bytes)).hexdigest()
            score = compute_portfolio_score(holdings)
            screenshot_id = self.db.add_screenshot(
                image_hash=img_hash,
                total_eval=summary.get("total_eval", 0),
                total_profit=summary.get("total_profit", 0),
                total_profit_pct=summary.get("total_profit_pct", 0),
                cash=summary.get("cash", 0),
                portfolio_score=score,
                holdings_json=json.dumps(holdings, ensure_ascii=False),
            )

            # [v3.5.1 FIX] 스크린샷 ID + 보유종목을 user_data에 저장 (진단/저장용)
            context.user_data["pending_screenshot_id"] = screenshot_id
            context.user_data["pending_holdings"] = holdings

            # [v3.5.1 FIX] 보유종목을 holdings DB에 자동 upsert (이전 기록 유지)
            # [v3.6.3 FIX] 한국 종목코드(6자리 숫자)만 holdings에 등록 — 미국주식 오등록 방지
            import re
            for h in holdings:
                ticker = h.get("ticker", "")
                hname = h.get("name", "")
                # [v3.5.5 FIX] ticker 비어있으면 이름으로 유니버스에서 찾기
                if not ticker and hname:
                    ticker = self._resolve_ticker_from_name(hname)
                    if ticker:
                        h["ticker"] = ticker  # 원본도 업데이트
                if not hname:
                    continue
                # 한국 종목코드 형식(6자리 숫자)이 아니면 holdings에 넣지 않음
                if not ticker or not re.match(r'^\d{6}$', ticker):
                    logger.debug("Skipping non-KR holding: %s (%s)", hname, ticker)
                    continue
                qty = h.get("quantity", 0)
                avg_price = h.get("avg_price", 0)
                cur_price = h.get("current_price", 0)
                pnl_pct = h.get("profit_pct", 0)
                eval_amt = h.get("eval_amount", 0)
                try:
                    self.db.upsert_holding(
                        ticker=ticker, name=hname,
                        quantity=qty, buy_price=avg_price,
                        current_price=cur_price, pnl_pct=pnl_pct,
                        eval_amount=eval_amt,
                    )
                except Exception as he:
                    logger.debug("Holding upsert for %s failed: %s", ticker, he)

                # screenshot_holdings 테이블에도 저장
                try:
                    is_margin, margin_type = detect_margin_purchase(h)
                    self.db.add_screenshot_holding(
                        screenshot_id=screenshot_id,
                        ticker=ticker, name=hname,
                        quantity=qty, avg_price=avg_price,
                        current_price=cur_price, profit_pct=pnl_pct,
                        eval_amount=eval_amt,
                        is_margin=1 if is_margin else 0,
                        margin_type=margin_type or "",
                    )
                except Exception as she:
                    logger.debug("Screenshot holding save for %s failed: %s", ticker, she)

            logger.info(
                "Screenshot saved: id=%s, %d holdings upserted",
                screenshot_id, len(holdings),
            )

            # Format and send summary
            msg = format_screenshot_summary(parsed, comparison, prev_diagnoses)
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

            # 포트폴리오 자동 추가 제안 (스크린샷에서 인식된 종목)
            if holdings:
                # 이미 DB에 등록된 종목 제외
                active = self.db.get_active_holdings()
                active_tickers = {h.get("ticker", "") for h in active}
                new_holdings = [
                    h for h in holdings
                    if h.get("ticker", "") and h.get("ticker", "") not in active_tickers
                ]
                if new_holdings:
                    # user_data에 저장 (콜백에서 사용)
                    context.user_data["screenshot_new_holdings"] = new_holdings
                    names = ", ".join(h.get("name", "?") for h in new_holdings[:5])
                    if len(new_holdings) > 5:
                        names += f" 외 {len(new_holdings)-5}종목"
                    buttons = [
                        [
                            InlineKeyboardButton(
                                "✅ 전체 추가",
                                callback_data="add_ss:all",
                            ),
                            InlineKeyboardButton(
                                "❌ 건너뛰기",
                                callback_data="add_ss:skip",
                            ),
                        ],
                    ]
                    # 개별 종목 버튼 (최대 5개)
                    for h in new_holdings[:5]:
                        t = h.get("ticker", "")
                        n = h.get("name", t)
                        p = h.get("avg_price", 0)
                        buttons.append([
                            InlineKeyboardButton(
                                f"➕ {n} ({p:,.0f}원)",
                                callback_data=f"add_ss:one:{t}",
                            ),
                        ])
                    await update.message.reply_text(
                        f"📋 신규 종목 {len(new_holdings)}개 감지!\n"
                        f"{names}\n\n"
                        "포트폴리오에 추가해드릴까요?",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )

            # 자동 투자기간 설정 (기본: 단기 스윙)
            if holdings:
                for h in holdings:
                    ticker = h.get("ticker", "")
                    hname = h.get("name", "")
                    if ticker:
                        # 신용/레버리지면 단타, 아니면 단기 기본
                        is_margin, _ = detect_margin_purchase(h)
                        hz = "danta" if is_margin else "dangi"
                        self.db.upsert_portfolio_horizon(
                            ticker=ticker, name=hname, horizon=hz,
                        )

        except Exception as e:
            logger.error("Screenshot analysis failed: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 스크린샷 분석 실패. 다시 시도해주세요.",
                reply_markup=get_reply_markup(context),
            )

    async def handle_menu_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._persist_chat_id(update)
        text = update.message.text
        handlers = {
            # ── v3.6.2 메인 메뉴 (4행) ──
            "📊 분석": self._menu_analysis_hub,
            "📈 시황": self._menu_market_status,
            "💰 잔고": self._menu_balance,
            "⭐ 즐겨찾기": self._menu_favorites,
            "🤖 에이전트": self._menu_agent_chat,
            "📋 리포트": self._menu_reports,
            "💬 AI질문": self._menu_ai_chat,
            "⚙️ 더보기": self._menu_more,
            "🔙 메인으로": self._menu_back_to_main,
            # ── 더보기 서브메뉴 ──
            "📸 계좌분석": self._menu_account_analysis,
            "🎯 전략별 보기": self._menu_strategy_view,
            "🔥 급등주": self._menu_surge,
            "⚡ 스윙 기회": self._menu_swing,
            "📊 멀티분석": self._menu_multi_agent,
            "🕵️ 매집탐지": self._menu_accumulation,
            "📅 주간 보고서": self._menu_weekly_report,
            "📊 공매도": self._menu_short,
            "🚀 미래기술": self._menu_future_tech,
            "🎯 30억 목표": self._menu_goal,
            "📊 재무 진단": self._menu_financial,
            "📡 KIS설정": self._menu_kis_setup,
            "🔔 알림 설정": self._menu_notification_settings,
            "⚙️ 최적화": self._menu_optimize,
            "💻 클로드": self._menu_claude_code,
            "🔙 대화 종료": self._exit_claude_mode,
            "🛠 관리자": self._menu_admin,
            # ── 이전 메뉴 하위호환 ──
            "\U0001f4d6 사용법 가이드": self._menu_usage_guide,
            "\U0001f514 알림": self._menu_notification_settings,
            "\U0001f30d 시장현황": self._menu_market_status,
            "\U0001f4c8 추천 성과": self._menu_reco_performance,
            "\U0001f4ac AI에게 질문": self._menu_ai_chat,
            "\U0001f4cb 증권사 리포트": self._menu_reports,
            "\U0001f916 에이전트": self._menu_agent_chat,
            "\U0001f514 실시간 알림": self._menu_alerts,
            "\U0001f4ca 오늘의 추천종목": self._menu_recommendations,
            "\U0001f4bc 내 포트폴리오": self._menu_portfolio,
            "\U0001f4ca 백테스트": self._menu_backtest,
            "\u2753 도움말": self._menu_usage_guide,
            "\U0001f4b0 잔고": self._menu_balance,
            "\U0001f4cb 리포트": self._menu_reports,
            "\U0001f4e1 KIS설정": self._menu_kis_setup,
        }
        handler = handlers.get(text)
        if handler:
            # 메뉴 이동 시 진행 중인 상태 클리어
            context.user_data.pop("kis_setup", None)
            context.user_data.pop("awaiting_optimize_ticker", None)
            # Claude 대화 모드: CLAUDE_MODE_MENU에 포함된 버튼은 모드 유지
            _claude_safe_buttons = {
                "💻 클로드", "🔙 대화 종료", "🤖 에이전트",
                "📊 분석", "📈 시황", "💰 잔고", "⭐ 즐겨찾기",
                "💬 AI질문", "📋 리포트", "⚙️ 더보기",
            }
            if text not in _claude_safe_buttons:
                context.user_data.pop("claude_mode", None)
                context.user_data.pop("claude_turn", None)
            try:
                await handler(update, context)
            except Exception as e:
                logger.error("Menu handler error: %s", e, exc_info=True)
                await update.message.reply_text(
                    "\u26a0\ufe0f 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    reply_markup=get_reply_markup(context),
                )
        else:
            # 매수 플래너: 금액 입력 대기 → 장바구니 모드 진입
            if context.user_data.get("awaiting_buy_amount"):
                import re as _re
                nums = _re.findall(r'\d+', text)
                if nums:
                    amount_만원 = int(nums[0])
                    amount_won = amount_만원 * 10000
                    context.user_data["awaiting_buy_amount"] = False
                    context.user_data["buy_cart"] = {
                        "budget": amount_won,
                        "remaining": amount_won,
                        "items": [],
                        "active": True,
                    }
                    # 장바구니 메뉴 표시
                    await self._show_cart_menu(update, context)
                else:
                    await update.message.reply_text(
                        "숫자를 입력해주세요 (예: 100)"
                    )
                return

            # 0-fav. 즐겨찾기 종목 추가 모드
            if context.user_data.get("awaiting_fav_add"):
                context.user_data.pop("awaiting_fav_add", None)
                detected = self._detect_stock_query(text)
                if detected:
                    ticker = detected.get("code", "")
                    name = detected.get("name", text)
                    if ticker:
                        self.db.add_watchlist(ticker, name)
                        await update.message.reply_text(
                            f"⭐ {name}({ticker})을 즐겨찾기에 등록했습니다!",
                            reply_markup=get_reply_markup(context),
                        )
                        return
                await update.message.reply_text(
                    f"⚠️ '{text}' 종목을 찾을 수 없습니다. 정확한 종목명을 입력해주세요.",
                    reply_markup=get_reply_markup(context),
                )
                return

            # 0. 잔고에서 "종목 추가" 후 종목명 입력 대기 상태
            if context.user_data.get("awaiting_stock_add"):
                detected = self._detect_stock_query(text)
                if detected:
                    context.user_data.pop("awaiting_stock_add", None)
                    await self._show_stock_actions(update, context, detected)
                    return
                else:
                    context.user_data.pop("awaiting_stock_add", None)
                    # 종목 못 찾으면 일반 처리로 진행

            # 0-0.5. 관리자 모드: 오류 신고 / 업데이트 요청 / 운영 지침 수정
            admin_mode = context.user_data.get("admin_mode")
            if admin_mode:
                context.user_data.pop("admin_mode", None)
                if admin_mode == "directive_edit":
                    await self._save_directive_edit(update, context, text)
                    return
                await self._save_admin_report(update, admin_mode, text)
                return

            # 0-1. KIS 설정 단계별 입력 상태
            kis_setup = context.user_data.get("kis_setup")
            if kis_setup:
                await self._handle_kis_setup_step(update, context, text, kis_setup)
                return

            # 0-2. 최적화 종목코드 입력 대기 상태
            if context.user_data.get("awaiting_optimize_ticker"):
                context.user_data.pop("awaiting_optimize_ticker", None)
                await self._run_optimize_from_text(update, context, text)
                return

            # 0-3. 에이전트 모드: 사용자 피드백 수집
            if context.user_data.get("agent_mode"):
                agent_type = context.user_data.get("agent_type", "feedback")
                logger.info(
                    "AGENT_FEEDBACK [%s]: %s", agent_type, text,
                )
                # 로그 파일에 피드백 기록
                try:
                    feedback_path = Path("data/agent_feedback.log")
                    feedback_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(feedback_path, "a", encoding="utf-8") as f:
                        ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                        f.write(f"[{ts}] [{agent_type}] {text}\n")
                except Exception:
                    logger.debug("handle_message agent feedback log write failed", exc_info=True)
                context.user_data.pop("agent_mode", None)
                context.user_data.pop("agent_type", None)
                await update.message.reply_text(
                    f"✅ 접수 완료!\n\n"
                    f"📝 [{agent_type}] {text[:60]}{'...' if len(text) > 60 else ''}\n\n"
                    f"다음 업데이트에 반영하겠습니다. 감사합니다! 🙏",
                    reply_markup=get_reply_markup(context),
                )
                return

            # 0-4. 클로드 대화 모드: "💻 클로드" 누른 후 자유 대화 (Claude API)
            if context.user_data.get("claude_mode"):
                # 대화 종료 버튼
                if text == "🔙 대화 종료":
                    await self._exit_claude_mode(update, context)
                    return
                if not self._is_authorized_chat(update):
                    return
                # v5.3: Claude API 자유 대화 (CLI 실행 아님)
                await self._handle_claude_free_chat(update, context, text)
                return

            # 0-5. Claude Code 원격 실행: "클코 ..." prefix
            from kstock.bot.mixins.remote_claude import CLAUDE_PREFIX
            if text.startswith(CLAUDE_PREFIX):
                if not self._is_authorized_chat(update):
                    return
                prompt = text[len(CLAUDE_PREFIX):].strip()
                if prompt:
                    await self._execute_claude_prompt(update, prompt)
                    return

            # 1. 자연어 보유종목 등록/매도 감지
            trade = self._detect_trade_input(text)
            if trade:
                if trade.get("action") == "sell":
                    await self._propose_trade_sell(update, context, trade)
                else:
                    await self._propose_trade_addition(update, context, trade)
                return

            # 2. 자연어 종목 감지 — 종목명만 입력해도 바로 분석
            detected = self._detect_stock_query(text)
            if detected:
                stock_name = detected.get("name", "")
                remaining = text.replace(stock_name, "").strip()
                # [v3.5.1] 종목명만 입력하면 바로 분석 실행 (슬래시 명령 불필요)
                # 종목명만 딱 입력한 경우 (remaining이 거의 없음) = 바로 분석
                if len(remaining) <= 3:
                    await self._handle_stock_analysis(
                        update, context, detected, f"{stock_name} 분석",
                    )
                else:
                    await self._handle_stock_analysis(
                        update, context, detected, text,
                    )
            else:
                # 메뉴에 없는 텍스트 -> AI 질문으로 처리
                await self._handle_ai_question(update, context, text)

    def _detect_stock_query(self, text: str) -> dict | None:
        """자연어에서 종목명/티커를 감지합니다.

        긴 이름 우선 매칭 (예: "삼성전자우"가 "삼성전자"보다 먼저).
        Returns:
            dict with 'code', 'name', 'market' if detected, else None.
        """
        import re

        clean = text.strip()

        # 1. 6자리 숫자 종목코드 감지
        code_match = re.search(r'(\d{6})', clean)
        if code_match:
            code = code_match.group(1)
            for item in self.all_tickers:
                if item["code"] == code:
                    return item
            holdings = self.db.get_active_holdings()
            for h in holdings:
                if h.get("ticker") == code:
                    return {"code": code, "name": h.get("name", code), "market": "KOSPI"}
            return {"code": code, "name": code, "market": "KOSPI"}

        # 2. 한글 종목명 매칭 (긴 이름 우선: "삼성전자우" > "삼성전자")
        # 유니버스 + 보유종목을 이름 길이 내림차순으로 정렬 후 매칭
        candidates = []
        for item in self.all_tickers:
            candidates.append((item["name"], item))
        holdings = self.db.get_active_holdings()
        for h in holdings:
            name = h.get("name", "")
            if name:
                candidates.append((name, {
                    "code": h.get("ticker", ""),
                    "name": name,
                    "market": "KOSPI",
                }))
        # 긴 이름 우선 정렬
        candidates.sort(key=lambda x: len(x[0]), reverse=True)

        for cand_name, cand_data in candidates:
            if cand_name and cand_name in clean:
                return cand_data

        # 3. 부분 매칭: 사용자 입력 키워드가 종목명에 포함 ("하이닉스" → "SK하이닉스")
        # 한글 3글자 이상 키워드만 매칭 (오탐 방지)
        words = re.findall(r"[가-힣]{3,}", clean)
        for word in words:
            for cand_name, cand_data in candidates:
                if cand_name and word in cand_name and word != cand_name:
                    return cand_data

        return None

    def _detect_trade_input(self, text: str) -> dict | None:
        """자연어에서 매수/매도 등록 패턴을 감지합니다.

        지원 패턴:
          - "삼성전자 50주 76000원"
          - "에코프로 100주 178500원에 샀어"
          - "005930 30주 매수"
          - "삼성전자 추가 50주 76000원"
          - "삼성전자 50주 80000원에 팔았어"
          - "에코프로 익절 100주 200000원"

        Returns:
            dict with 'ticker', 'name', 'quantity', 'price', 'action' or None.
        """
        import re

        # 매도 관련 키워드
        sell_keywords = ["팔았", "매도", "청산", "익절", "손절"]
        is_sell = any(kw in text for kw in sell_keywords)

        # 매수 관련 키워드가 포함되었거나, 수량+가격 패턴이 있는 경우만
        trade_keywords = ["샀", "매수", "추가", "편입", "담았", "들어갔"]
        has_keyword = any(kw in text for kw in trade_keywords) or is_sell

        # 수량(주) + 가격(원) 패턴 감지
        qty_price_pat = re.search(
            r'(\d[\d,]*)주.*?(\d[\d,]*)원', text,
        )
        if not qty_price_pat and not has_keyword:
            return None

        # parse_trade_text로 구조화
        trade = parse_trade_text(text)
        if not trade:
            return None

        # 종목코드가 없으면 이름으로 매칭
        ticker = trade.ticker
        name = trade.name
        if not ticker and name:
            stock = self._detect_stock_query(name)
            if stock:
                ticker = stock.get("code", "")
                name = stock.get("name", name)

        if not ticker:
            return None

        return {
            "ticker": ticker,
            "name": name,
            "quantity": trade.quantity,
            "price": trade.price,
            "action": "sell" if is_sell else "buy",
        }

    async def _propose_trade_addition(
        self, update: Update, context, trade: dict,
    ) -> None:
        """감지된 매수 정보를 확인 후 포트폴리오에 추가 제안."""
        ticker = trade["ticker"]
        name = trade["name"]
        qty = trade.get("quantity", 0)
        price = trade.get("price", 0)

        # user_data에 저장
        context.user_data["pending_text_holding"] = trade

        qty_str = f"{qty}주 " if qty else ""
        price_str = f"{price:,.0f}원" if price else "가격 미지정"

        buttons = [
            [
                InlineKeyboardButton(
                    "✅ 추가", callback_data="add_txt:yes",
                ),
                InlineKeyboardButton(
                    "❌ 취소", callback_data="add_txt:no",
                ),
            ],
        ]
        await update.message.reply_text(
            f"📋 매수 등록 감지!\n\n"
            f"종목: {name} ({ticker})\n"
            f"수량: {qty_str}\n"
            f"매수가: {price_str}\n\n"
            f"포트폴리오에 추가해드릴까요?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _propose_trade_sell(
        self, update: Update, context, trade: dict,
    ) -> None:
        """감지된 매도 정보를 확인 후 포트폴리오에서 매도 기록 제안."""
        ticker = trade["ticker"]
        name = trade["name"]
        qty = trade.get("quantity", 0)
        sell_price = trade.get("price", 0)

        # 보유종목에서 매수가 조회
        holding = None
        try:
            holdings = self.db.get_active_holdings()
            for h in holdings:
                if h.get("ticker") == ticker:
                    holding = h
                    break
        except Exception:
            logger.debug("_handle_sell_trade holding lookup failed for %s", ticker, exc_info=True)

        buy_price = holding.get("avg_price", 0) if holding else 0
        pnl_pct = ((sell_price - buy_price) / buy_price * 100) if buy_price > 0 and sell_price > 0 else 0

        context.user_data["pending_sell"] = {
            **trade,
            "buy_price": buy_price,
            "pnl_pct": pnl_pct,
        }

        qty_str = f"{qty}주 " if qty else ""
        price_str = f"{sell_price:,.0f}원" if sell_price else "가격 미지정"
        pnl_str = f"{pnl_pct:+.1f}%" if buy_price > 0 and sell_price > 0 else "산출 불가"
        pnl_emoji = "\U0001f4c8" if pnl_pct > 0 else "\U0001f4c9" if pnl_pct < 0 else "\u2796"

        buttons = [
            [
                InlineKeyboardButton(
                    "\u2705 매도 기록", callback_data="sell_confirm:yes",
                ),
                InlineKeyboardButton(
                    "\u274c 취소", callback_data="sell_confirm:no",
                ),
            ],
        ]
        await update.message.reply_text(
            f"\U0001f4cb 매도 기록 감지!\n\n"
            f"종목: {name} ({ticker})\n"
            f"수량: {qty_str}\n"
            f"매도가: {price_str}\n"
            f"매수가: {buy_price:,.0f}원\n"
            f"{pnl_emoji} 수익률: {pnl_str}\n\n"
            f"매도를 기록할까요?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_confirm_sell(self, query, context, payload: str) -> None:
        """매도 기록 확인 콜백."""
        if payload != "yes":
            await query.edit_message_text("\u274c 매도 기록을 취소했습니다.")
            return

        sell = context.user_data.pop("pending_sell", None)
        if not sell:
            await query.edit_message_text("\u26a0\ufe0f 매도 정보가 만료되었습니다.")
            return

        ticker = sell["ticker"]
        name = sell["name"]
        sell_price = sell.get("price", 0)
        pnl_pct = sell.get("pnl_pct", 0)

        try:
            self.db.add_trade(
                ticker=ticker, name=name, action="sell",
                action_price=sell_price, pnl_pct=pnl_pct,
            )
        except Exception as e:
            logger.warning("Failed to record sell trade: %s", e)

        # 보유종목 상태를 sold로 변경
        try:
            h = self.db.get_holding_by_ticker(ticker)
            if h:
                self.db.update_holding(h["id"], status="sold")
        except Exception as e:
            logger.warning("Failed to update holding after sell: %s", e)

        pnl_emoji = "\U0001f4c8" if pnl_pct > 0 else "\U0001f4c9" if pnl_pct < 0 else "\u2796"
        await query.edit_message_text(
            f"\u2705 매도 기록 완료!\n\n"
            f"종목: {name} ({ticker})\n"
            f"매도가: {sell_price:,.0f}원\n"
            f"{pnl_emoji} 수익률: {pnl_pct:+.1f}%"
        )

    async def _show_stock_actions(
        self, update: Update, context, stock: dict,
    ) -> None:
        """종목명만 입력했을 때 액션 버튼 제공.

        "삼성전자" → [📊 분석] [➕ 추가] [👀 관심]
        """
        code = stock.get("code", "")
        name = stock.get("name", code)
        market = stock.get("market", "KOSPI")

        # 현재가 자동 조회
        price = 0.0
        price_str = "현재가: 조회 중"
        try:
            price = await self._get_price(code)
            if price > 0:
                price_str = f"현재가: {price:,.0f}원"
        except Exception:
            logger.debug("_detect_stock_query get_price failed for %s", code, exc_info=True)
            price_str = "현재가: 조회 실패"

        # user_data에 저장 (콜백에서 사용)
        context.user_data["pending_stock_action"] = {
            "code": code, "name": name, "market": market, "price": price,
        }

        # 이미 보유 중인지 확인
        existing = self.db.get_holding_by_ticker(code)

        if existing:
            add_btn = InlineKeyboardButton(
                "✅ 보유 중", callback_data=f"stock_act:noop:{code}",
            )
        else:
            add_btn = InlineKeyboardButton(
                "➕ 포트폴리오 추가",
                callback_data=f"stock_act:add:{code}",
            )

        buttons = [
            [
                InlineKeyboardButton(
                    "📊 분석", callback_data=f"stock_act:analyze:{code}",
                ),
                add_btn,
            ],
            [
                InlineKeyboardButton(
                    "👀 관심종목", callback_data=f"stock_act:watch:{code}",
                ),
                InlineKeyboardButton(
                    "❌ 닫기", callback_data="dismiss:0",
                ),
            ],
        ]

        await update.message.reply_text(
            f"📌 {name} ({code})\n{price_str}\n\n어떻게 하시겠어요?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _handle_stock_analysis(
        self, update: Update, context, stock: dict, original_text: str
    ) -> None:
        """자연어로 감지된 종목에 대해 AI 분석을 수행합니다."""
        code = stock.get("code", "")
        name = stock.get("name", code)

        placeholder = await update.message.reply_text(
            f"\U0001f50d {name}({code}) 분석 중..."
        )

        try:
            market = stock.get("market", "KOSPI")
            tech_data = ""
            price_data = ""
            fund_data = ""
            cur_price = 0.0

            try:
                ohlcv = await self.yf_client.get_ohlcv(code, market)
                if ohlcv is not None and not ohlcv.empty:
                    tech = compute_indicators(ohlcv)
                    close = ohlcv["close"].astype(float)
                    volume = ohlcv["volume"].astype(float)
                    cur_price = float(close.iloc[-1])
                    prev_price = float(close.iloc[-2]) if len(close) >= 2 else cur_price
                    change_pct = ((cur_price - prev_price) / prev_price * 100) if prev_price > 0 else 0
                    avg_vol = float(volume.tail(20).mean())
                    cur_vol = float(volume.iloc[-1])

                    price_data = (
                        f"현재가: {cur_price:,.0f}원 ({change_pct:+.1f}%)\n"
                        f"거래량: {cur_vol:,.0f}주 (20일평균 대비 {cur_vol/avg_vol:.1f}배)"
                    )
                    tech_data = (
                        f"RSI: {tech.rsi:.1f}\n"
                        f"MACD: {tech.macd:.2f} (시그널: {tech.macd_signal:.2f})\n"
                        f"볼린저밴드 위치: {tech.bb_position:.2f}\n"
                        f"이동평균선: 5일 {tech.ma5:,.0f}원, 20일 {tech.ma20:,.0f}원, "
                        f"60일 {tech.ma60:,.0f}원, 120일 {tech.ma120:,.0f}원"
                    )
            except Exception:
                logger.debug("_handle_stock_analysis tech data fetch failed for %s", code, exc_info=True)
                tech_data = "기술적 데이터 조회 실패"

            try:
                fin = self.db.get_financials(code)
                if fin:
                    fund_data = (
                        f"PER: {fin.get('per', 0):.1f} "
                        f"(섹터평균: {fin.get('sector_per', 15):.1f})\n"
                        f"PBR: {fin.get('pbr', 0):.2f}, "
                        f"ROE: {fin.get('roe', 0):.1f}%\n"
                        f"부채비율: {fin.get('debt_ratio', 0):.0f}%"
                    )
            except Exception:
                logger.debug("_handle_stock_analysis financials fetch failed for %s", code, exc_info=True)
                fund_data = "재무 데이터 없음"

            # v8.2: 현재 시장 상황 반영형 매매 레벨
            trade_levels = ""
            situation_ctx = ""
            alert_mode = getattr(self, '_alert_mode', 'normal')
            try:
                macro = await self.macro_client.get_snapshot()
                vix = getattr(macro, "vix", 15)
                spx_chg = getattr(macro, "spx_change_pct", 0)

                if alert_mode == "wartime":
                    situation_ctx = (
                        f"[🔴 전시 상황]\n"
                        f"현재 전시 경계 모드 — 국내 증시 폭락장.\n"
                        f"VIX: {vix:.1f}, S&P500: {spx_chg:+.1f}%\n"
                        f"전시 대응 원칙: 신규 매수 자제, 손절 강화(-5%), 현금 확보 40%+\n"
                        f"방어 섹터(의료/필수소비재/유틸리티) 외 경기민감 종목은 축소 검토.\n\n"
                    )
                elif alert_mode == "elevated":
                    situation_ctx = (
                        f"[🟡 긴장 상황]\n"
                        f"시장 변동성 확대 중. VIX: {vix:.1f}\n"
                        f"보수적 접근 필요. 신규 매수는 확신 높은 경우만.\n\n"
                    )
            except Exception:
                pass

            if cur_price > 0:
                if alert_mode == "wartime":
                    # 전시: 보수적 레벨 (손절 -5%, 목표 낮춤)
                    trade_levels = (
                        f"[전시 매매 레벨 - 현재가 {cur_price:,.0f}원 기준]\n"
                        f"🔴 전시 손절: {cur_price * 0.95:,.0f}원 (현재가 -5%)\n"
                        f"관망 유지: 추가 하락 가능성 열어둘 것\n"
                        f"반등 시 목표: {cur_price * 1.05:,.0f}원 (+5%)\n"
                        f"분할 매수 1차: {cur_price * 0.90:,.0f}원 (-10%) — 확신 시만\n"
                        f"분할 매수 2차: {cur_price * 0.85:,.0f}원 (-15%) — 패닉 매수\n"
                    )
                elif alert_mode == "elevated":
                    trade_levels = (
                        f"[긴장 매매 레벨 - 현재가 {cur_price:,.0f}원 기준]\n"
                        f"손절 기준: {cur_price * 0.94:,.0f}원 (현재가 -6%)\n"
                        f"관심 매수: {cur_price * 0.93:,.0f}원 (현재가 -7%)\n"
                        f"단기 목표: {cur_price * 1.07:,.0f}원 (현재가 +7%)\n"
                        f"중기 목표: {cur_price * 1.15:,.0f}원 (현재가 +15%)\n"
                    )
                else:
                    trade_levels = (
                        f"[매매 참고 레벨 - 현재가 {cur_price:,.0f}원 기준]\n"
                        f"적극 매수: {cur_price * 0.90:,.0f}원 (현재가 -10%)\n"
                        f"관심 매수: {cur_price * 0.95:,.0f}원 (현재가 -5%)\n"
                        f"단기 목표: {cur_price * 1.10:,.0f}원 (현재가 +10%)\n"
                        f"중기 목표: {cur_price * 1.20:,.0f}원 (현재가 +20%)\n"
                        f"손절 기준: {cur_price * 0.93:,.0f}원 (현재가 -7%)\n"
                    )

            enriched_question = (
                f"{name}({code}) 종목 분석 요청.\n"
                f"사용자 질문: {original_text}\n\n"
                f"{situation_ctx}"
                f"[실시간 가격]\n{price_data}\n\n"
                f"[기술적 지표]\n{tech_data}\n\n"
                f"[펀더멘털]\n{fund_data}\n\n"
                f"{trade_levels}\n"
                f"[절대 규칙] 위 [실시간 가격]과 [매매 참고 레벨]의 숫자만 사용하라. "
                f"너의 학습 데이터에 있는 과거 주가를 절대 사용 금지. "
                f"매수/매도 포인트 가격은 반드시 위 [매매 참고 레벨]에서 선택하라. "
                f"현재 시장 상황을 반드시 반영하여 분석하라."
            )

            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(
                self.db, self.macro_client, self.yf_client,
            )
            answer = await handle_ai_question(
                enriched_question, ctx, self.db, chat_mem,
                verified_names={name},
            )

            # 후속 질문 파싱 → 버튼 변환
            stock_data = {"code": code, "name": name, "market": market}
            answer, followup_buttons = self._parse_followup_buttons(answer)
            if not followup_buttons:
                followup_buttons = self._build_followup_buttons(original_text, stock_data)
            markup = InlineKeyboardMarkup(followup_buttons) if followup_buttons else None

            try:
                await placeholder.edit_text(answer, reply_markup=markup)
            except Exception:
                logger.debug("_handle_stock_analysis edit_text failed, falling back", exc_info=True)
                await update.message.reply_text(
                    answer, reply_markup=markup or get_reply_markup(context),
                )
        except Exception as e:
            logger.error("Stock analysis error: %s", e, exc_info=True)
            try:
                await placeholder.edit_text(
                    f"\u26a0\ufe0f {name} 분석 중 오류가 발생했습니다."
                )
            except Exception:
                logger.debug("_handle_stock_analysis error recovery edit_text also failed", exc_info=True)
                await update.message.reply_text(
                    f"\u26a0\ufe0f {name} 분석 중 오류가 발생했습니다.",
                    reply_markup=get_reply_markup(context),
                )

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            logger.debug("handle_callback query.answer failed (query too old or invalid)", exc_info=True)
        data = query.data or ""
        try:
            action, _, payload = data.partition(":")
            dispatch = {
                "buy": self._action_buy,
                "skip": self._action_skip,
                "watch_alert": self._action_watch,
                "pass": self._action_skip,
                "sell_profit": self._action_sell_profit,
                "hold_profit": self._action_hold_profit,
                "stop_loss": self._action_stop_loss,
                "hold_through": self._action_hold_through,
                "sell_half": self._action_sell_profit,
                "hold_more": self._action_hold_profit,
                "detail": self._action_detail,
                "nowatch": self._action_nowatch,
                "watch_btn": self._action_watch_btn,
                "strat": self._action_strategy,
                "opt_apply": self._action_opt_apply,
                "opt_ignore": self._action_opt_ignore,
                "kis_buy": self._action_kis_buy,
                "kis_pass": self._action_skip,
                "hz": self._action_horizon_select,
                "ht": self._action_set_holding_type,
                "sol": self._action_solution_detail,
                "scn": self._action_scenario_run,
                "notif": self._action_notification_toggle,
                "rpt": self._action_report_submenu,
                "sector_rpt": self._action_sector_report,
                "weekly": self._action_weekly_submenu,
                "sell_plans": self._action_sell_plans,
                "multi_run": self._action_multi_run,
                "quick_q": self._handle_quick_question,
                "add_ss": self._action_add_from_screenshot,
                "add_txt": self._action_confirm_text_holding,
                "sell_confirm": self._action_confirm_sell,
                "stock_act": self._action_stock_action,
                "bal": self._action_balance,
                "selfupd": self._action_self_update,
                "kis_hub": self._action_kis_hub,
                "kis_mode": self._action_kis_mode,
                "price_alert": self._action_price_alert,
                "kis": self._action_kis,
                "opt_run": self._action_opt_run,
                "fav": self._action_favorites,
                "agent": self._action_agent,
                "goto": self._action_goto,
                "adm": self._handle_admin_callback,
                # v3.6 신규
                "ai": self._action_ai_status,
                "orderbook": self._action_orderbook,
                "short": self._action_short_analysis,
                "hub": self._action_hub,
                # v3.7: 매수 플래너
                "bp": self._action_buy_plan,
                # Backtest Pro
                "bt": self._action_backtest_pro,
                # v3.8: 고급 리스크
                "risk": self._action_risk_advanced,
                # v3.9: 매도 알림 뮤트
                "mute": self._action_mute_alert,
                # v3.9: 매니저 조회
                "mgr": self._action_manager_view,
                # v3.9: 거품 판별
                "bubble": self._action_bubble_check,
                # v4.1: 차익실현 콜백
                "pt": self._action_profit_taking,
                # v4.3: 매매일지/섹터로테이션/역발상
                "journal": self._action_journal_view,
                "sector_rotate": self._action_sector_rotate,
                "contrarian": self._action_contrarian_view,
                "bt_adv": self._action_backtest_advanced,
                # auto-fix 승인/거부
                "autofix": self._action_autofix,
                # AI 후속 질문
                "followup": self._action_followup,
                "followup_q": self._action_followup_dynamic,
                # v5.3: 범용 닫기 버튼
                "dismiss": self._action_dismiss,
                # v5.5: 피드백 + 일일 평가
                "fb": self._action_feedback,
                "rate": self._action_daily_rate,
                # v5.9: 더보기 인라인 메뉴 → 텍스트 메뉴 호출
                "menu": self._action_menu_dispatch,
            }
            handler = dispatch.get(action)
            if handler:
                await handler(query, context, payload)
        except Exception as e:
            logger.error("Callback error: %s", e, exc_info=True)
            try:
                # v6.2.1: 에러 복구 버튼 (단순 텍스트 대신 액션 제공)
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                err_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 다시 시도",
                                          callback_data=f"{action}:{payload}" if payload else action)],
                    [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
                ])
                await query.message.reply_text(
                    f"⚠️ 오류가 발생했습니다.\n원인: {str(e)[:80]}\n\n다시 시도하거나 닫아주세요.",
                    reply_markup=err_kb,
                )
            except Exception:
                logger.debug("handle_callback error recovery also failed", exc_info=True)

    # == 더보기 인라인 메뉴 디스패치 (v5.9) ====================================

    async def _action_menu_dispatch(self, query, context, payload: str) -> None:
        """더보기 InlineKeyboard → 해당 메뉴 함수 호출.

        v5.9: 더보기를 InlineKeyboard로 전환하여 Reply Keyboard(클로드 메뉴) 유지.
        v6.2.1: UX 개선 — 기능별 로딩 메시지 + 에러 복구 버튼.
        """
        menu_map = {
            "account_analysis": self._menu_account_analysis,
            "strategy_view": self._menu_strategy_view,
            "surge": self._menu_surge,
            "swing": self._menu_swing,
            "multi_agent": self._menu_multi_agent,
            "accumulation": self._menu_accumulation,
            "weekly_report": self._menu_weekly_report,
            "short": self._menu_short,
            "future_tech": self._menu_future_tech,
            "goal": self._menu_goal,
            "financial": self._menu_financial,
            "kis_setup": self._menu_kis_setup,
            "notification": self._menu_notification_settings,
            "optimize": self._menu_optimize,
            "admin": self._menu_admin,
        }
        # v6.2.1: 기능별 로딩 메시지
        _loading_msg = {
            "account_analysis": "💻 계좌 분석 준비 중...",
            "strategy_view": "🎯 전략별 보기 로딩 중...",
            "surge": "🔥 급등주 스캔 중... (약 5초)",
            "swing": "⚡ 스윙 기회 조회 중...",
            "multi_agent": "📊 멀티 에이전트 준비 중...",
            "accumulation": "🕵️ 매집 탐지 분석 중... (약 10초)",
            "weekly_report": "📅 주간 보고서 로딩 중...",
            "short": "📊 공매도 데이터 조회 중...",
            "future_tech": "🚀 미래기술 종목 로딩 중...",
            "goal": "🎯 30억 목표 대시보드 로딩 중...",
            "financial": "📊 재무 진단 데이터 로딩 중...",
            "kis_setup": "📡 KIS 연결 상태 확인 중...",
            "notification": "🔔 알림 설정 로딩 중...",
            "optimize": "⚙️ 최적화 설정 로딩 중...",
            "admin": "🛠 관리자 메뉴 로딩 중...",
        }
        handler = menu_map.get(payload)
        if not handler:
            await query.edit_message_text(f"⚠️ 알 수 없는 메뉴: {payload}")
            return
        # 로딩 메시지 표시
        loading = _loading_msg.get(payload, "⏳ 로딩 중...")
        try:
            await query.edit_message_text(loading)
        except Exception:
            logger.debug("_action_menu_dispatch edit_text transition failed", exc_info=True)
        # 메뉴 함수는 update.message를 기대 → SimpleNamespace로 래핑
        import types
        fake_update = types.SimpleNamespace(
            message=query.message,
            effective_user=query.from_user,
            effective_chat=query.message.chat,
        )
        try:
            await handler(fake_update, context)
        except Exception as e:
            logger.error("Menu dispatch error [%s]: %s", payload, e, exc_info=True)
            # v6.2.1: 에러 복구 버튼
            _menu_labels = {
                "account_analysis": "계좌분석", "surge": "급등주",
                "multi_agent": "멀티분석", "accumulation": "매집탐지",
                "weekly_report": "주간보고서", "kis_setup": "KIS설정",
            }
            label = _menu_labels.get(payload, payload)
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                err_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 다시 시도", callback_data=f"menu:{payload}")],
                    [
                        InlineKeyboardButton("🛠 오류 신고", callback_data="adm:bug"),
                        InlineKeyboardButton("🔙 더보기", callback_data="goto:more"),
                    ],
                ])
                await query.message.reply_text(
                    f"⚠️ {label} 오류\n\n"
                    f"원인: {str(e)[:80]}\n\n"
                    f"아래 버튼으로 다시 시도하거나 오류를 신고해주세요.",
                    reply_markup=err_buttons,
                )
            except Exception:
                logger.debug("_action_menu_dispatch error recovery also failed", exc_info=True)

    # == Dismiss (generic close button) =======================================

    async def _action_dismiss(self, query, context, payload: str) -> None:
        """범용 닫기 버튼 — 메뉴를 닫고 상태 정리 + Reply Keyboard 복구."""
        # 진행 중인 상태 클리어
        for key in ("admin_mode", "admin_faq_type", "agent_mode", "agent_type"):
            context.user_data.pop(key, None)
        try:
            await query.edit_message_text("✅ 메뉴를 닫았습니다.")
            # Reply Keyboard 복구 (InlineKeyboard 닫은 후 하단 메뉴 보이게)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📱 메뉴를 사용하세요.",
                reply_markup=get_reply_markup(context),
            )
        except Exception:
            logger.debug("_action_dismiss edit/send failed", exc_info=True)

    # == Feedback system (v5.5) ================================================

    async def _action_feedback(self, query, context, payload: str) -> None:
        """👍/👎 피드백 처리 — fb:like:menu_name / fb:dislike:menu_name."""
        parts = payload.split(":", 1)
        fb_type = parts[0] if parts else ""
        menu_name = parts[1] if len(parts) > 1 else "unknown"

        self.db.add_user_feedback(menu_name, fb_type)

        if fb_type == "like":
            try:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(
                    "👍 감사합니다! 피드백이 반영되었습니다.",
                    reply_markup=get_reply_markup(context),
                )
            except Exception:
                logger.debug("_action_feedback like reply failed", exc_info=True)
        elif fb_type == "dislike":
            # 싫어요 → 자동 오류 진단 + 로그 기록
            try:
                # 원본 메시지 내용 캡처 (진단용)
                original_msg = query.message.text or ""
                original_short = original_msg[:500]

                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(
                    f"👎 {menu_name} 기능 오류 감지\n\n"
                    f"자동 진단을 시작합니다...",
                    reply_markup=get_reply_markup(context),
                )

                # 자동 오류 진단: 메시지 내용 분석
                diag_lines = [f"[자동 진단] {menu_name} 기능 오류 리포트"]
                diag_lines.append(f"시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
                diag_lines.append("")

                # 메시지 내용 기반 진단
                issues_found = []
                if "분석 불가" in original_msg or "데이터 부족" in original_msg:
                    issues_found.append("AI 분석 데이터 부족 또는 API 호출 실패")
                if "50점" in original_msg and original_msg.count("50점") >= 2:
                    issues_found.append("모든 에이전트가 기본값(50점) 반환 — API 응답 파싱 실패 의심")
                if "중립" in original_msg and original_msg.count("중립") >= 2:
                    issues_found.append("모든 판단이 중립 — 데이터 미주입 또는 파싱 오류 의심")
                if "오류" in original_msg or "에러" in original_msg:
                    issues_found.append("명시적 오류 메시지 포함")
                if "None" in original_msg:
                    issues_found.append("None 값 노출 — 데이터 누락")
                if not issues_found:
                    issues_found.append("사용자 불만 — 구체적 원인은 로그 분석 필요")

                for issue in issues_found:
                    diag_lines.append(f"  - {issue}")

                diag_lines.append("")
                diag_lines.append(f"원본 메시지 (앞 200자):")
                diag_lines.append(original_short[:200])

                diag_msg = "\n".join(diag_lines)
                logger.warning("[피드백/싫어요] %s", diag_msg)

                # 진단 결과 전송
                await query.message.reply_text(
                    f"🔍 자동 진단 결과\n\n"
                    f"기능: {menu_name}\n"
                    f"감지된 문제:\n" +
                    "\n".join(f"  - {i}" for i in issues_found) +
                    f"\n\n이 진단은 자동으로 로그에 기록되었습니다.\n"
                    f"구체적인 문제를 메시지로 알려주시면 더 정확한 수정이 가능합니다.",
                    reply_markup=get_reply_markup(context),
                )
            except Exception as e:
                logger.error("피드백 자동진단 오류: %s", e)
                try:
                    await query.message.reply_text(
                        f"👎 {menu_name} 피드백이 기록되었습니다.",
                        reply_markup=get_reply_markup(context),
                    )
                except Exception:
                    logger.debug("_action_feedback dislike error recovery reply also failed", exc_info=True)

    async def _action_daily_rate(self, query, context, payload: str) -> None:
        """일일 평가 — rate:상 / rate:중 / rate:하."""
        rating = payload  # 상, 중, 하
        rating_map = {"상": "excellent", "중": "average", "하": "poor"}
        self.db.add_user_feedback("daily_rating", rating_map.get(rating, rating))

        emoji = {"상": "🌟", "중": "👌", "하": "😔"}.get(rating, "📝")
        try:
            await query.edit_message_text(
                f"{emoji} 오늘 평가: {rating}\n\n"
                f"소중한 평가 감사합니다.\n"
                f"더 나은 서비스를 위해 노력하겠습니다."
            )
            # Reply Keyboard 복구
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📱 메뉴를 사용하세요.",
                reply_markup=get_reply_markup(context),
            )
        except Exception:
            logger.debug("_action_daily_rate reply failed", exc_info=True)

    # == Horizon selection ====================================================

    async def _action_horizon_select(self, query, context, payload: str) -> None:
        """Handle horizon selection callback: hz:horizon:ticker or hz:default_all:0."""
        horizon, _, ticker = payload.partition(":")

        # "전체 기본 진단" button → run legacy batch_diagnose
        if horizon == "default_all":
            holdings = context.user_data.get("pending_holdings", [])
            screenshot_id = context.user_data.get("pending_screenshot_id")
            if not holdings:
                await query.edit_message_text("\u26a0\ufe0f 진단할 종목이 없습니다.")
                return

            await query.edit_message_text("\U0001f50d 전체 기본 진단 실행 중...")
            tech_map: dict = {}
            flow_map: dict = {}
            diagnoses = await batch_diagnose(
                holdings, tech_map, flow_map, self.anthropic_key,
            )
            pairs = list(zip(holdings, diagnoses))
            diag_msg = format_diagnosis_report(pairs)
            await query.message.reply_text(diag_msg, reply_markup=get_reply_markup(context))

            # Save to DB
            if screenshot_id:
                for h, d in zip(holdings, diagnoses):
                    is_margin, margin_type = detect_margin_purchase(h)
                    self.db.add_screenshot_holding(
                        screenshot_id=screenshot_id,
                        ticker=h.get("ticker", ""),
                        name=h.get("name", ""),
                        quantity=h.get("quantity", 0),
                        avg_price=h.get("avg_price", 0),
                        current_price=h.get("current_price", 0),
                        profit_pct=h.get("profit_pct", 0),
                        eval_amount=h.get("eval_amount", 0),
                        diagnosis=d.diagnosis,
                        diagnosis_action=d.action,
                        diagnosis_msg=d.message,
                        is_margin=1 if is_margin else 0,
                        margin_type=margin_type or "",
                    )

            # Account-level 8-item diagnosis
            await self._send_account_diagnosis(query, holdings, screenshot_id)

            # Cleanup
            context.user_data.pop("pending_horizons", None)
            context.user_data.pop("pending_holdings", None)
            context.user_data.pop("pending_screenshot_id", None)
            return

        # Individual horizon selection
        pending = context.user_data.get("pending_horizons", {})
        cfg = HORIZON_CONFIG.get(horizon, {})
        label = cfg.get("label", "기본")
        pending[ticker] = horizon

        name = ticker
        for h in context.user_data.get("pending_holdings", []):
            if h.get("ticker") == ticker:
                name = h.get("name", ticker)
                break

        # portfolio_horizon에 항상 저장 (매수 후 선택 / 스크린샷 선택 공통)
        if ticker:
            holding = self.db.get_holding_by_ticker(ticker)
            if holding:
                name = holding.get("name", name)
            self.db.upsert_portfolio_horizon(
                ticker=ticker, name=name, horizon=horizon,
            )

        await query.edit_message_text(f"\u2705 {name}: {label} 선택됨")

        # Check if all holdings have been assigned a horizon (스크린샷 플로우)
        holdings = context.user_data.get("pending_holdings", [])
        all_tickers = {h.get("ticker", "") for h in holdings}
        if all_tickers and all_tickers <= set(pending.keys()):
            await self._run_horizon_diagnosis(query, context)

    async def _run_horizon_diagnosis(self, query, context) -> None:
        """Execute horizon-based diagnosis for all pending holdings."""
        holdings = context.user_data.get("pending_holdings", [])
        horizons = context.user_data.get("pending_horizons", {})
        screenshot_id = context.user_data.get("pending_screenshot_id")

        if not holdings:
            return

        await query.message.reply_text("\U0001f50d 투자 시계별 진단 실행 중... 잠시만 기다려주세요.")

        # Build (holding, horizon) pairs
        pairs = []
        for h in holdings:
            ticker = h.get("ticker", "")
            hz = horizons.get(ticker, "default")
            pairs.append((h, hz))

        results = await batch_diagnose_by_horizon(
            pairs,
            anthropic_key=self.anthropic_key,
            db=self.db,
        )

        report = format_horizon_report(results)
        await query.message.reply_text(report, reply_markup=get_reply_markup(context))

        # Save to DB
        if screenshot_id:
            for h, r in zip(holdings, results):
                self.db.add_screenshot_holding(
                    screenshot_id=screenshot_id,
                    ticker=r.ticker,
                    name=r.name,
                    quantity=h.get("quantity", 0),
                    avg_price=h.get("avg_price", 0),
                    current_price=h.get("current_price", 0),
                    profit_pct=r.profit_pct,
                    eval_amount=h.get("eval_amount", 0),
                    diagnosis=r.diagnosis,
                    diagnosis_action=r.action,
                    diagnosis_msg=r.message,
                    is_margin=1 if r.is_margin else 0,
                    margin_type=r.margin_type or "",
                )
                self.db.add_investment_horizon(
                    ticker=r.ticker,
                    name=r.name,
                    horizon=r.horizon,
                    screenshot_id=screenshot_id,
                    stop_pct=HORIZON_CONFIG.get(r.horizon, {}).get("stop"),
                    target_pct=HORIZON_CONFIG.get(r.horizon, {}).get("target"),
                    trailing_pct=HORIZON_CONFIG.get(r.horizon, {}).get("trailing"),
                    is_margin=1 if r.is_margin else 0,
                    margin_type=r.margin_type,
                    diagnosis=r.diagnosis,
                    diagnosis_action=r.action,
                    diagnosis_msg=r.message,
                )
                # Also save to portfolio_horizon for next time
                if r.horizon and r.horizon != "default":
                    self.db.upsert_portfolio_horizon(
                        ticker=r.ticker, name=r.name, horizon=r.horizon,
                    )

        # Account-level 8-item diagnosis
        await self._send_account_diagnosis(query, holdings, screenshot_id)

        # Cleanup
        context.user_data.pop("pending_horizons", None)
        context.user_data.pop("pending_holdings", None)
        context.user_data.pop("pending_screenshot_id", None)

    async def _send_account_diagnosis(
        self, query, holdings: list, screenshot_id: int | None = None,
    ) -> None:
        """Send portfolio-level 8-item diagnosis and offer solutions."""
        try:
            summary = {}
            total_eval = sum(h.get("eval_amount", 0) for h in holdings)
            total_profit = sum(h.get("eval_amount", 0) - (h.get("avg_price", 0) * h.get("quantity", 0))
                               for h in holdings)
            cash = 0
            if screenshot_id:
                ss = self.db.get_last_screenshot()
                if ss:
                    cash = ss.get("cash", 0) or 0
                    total_eval = ss.get("total_eval", 0) or total_eval
            total_buy = sum(h.get("avg_price", 0) * h.get("quantity", 0) for h in holdings)
            total_profit_pct = (total_profit / total_buy * 100) if total_buy > 0 else 0

            diag = diagnose_account(
                holdings=holdings,
                total_profit_pct=total_profit_pct,
                cash=cash,
                total_eval=total_eval,
            )
            report = format_account_diagnosis(diag)
            await query.message.reply_text(report, reply_markup=get_reply_markup(context))

            # Save solutions to DB
            if diag.solutions and screenshot_id:
                for sol in diag.solutions:
                    self.db.add_solution(
                        solution_type=sol["type"],
                        description=f"{sol['description']} -> {sol['action']}",
                        before_snapshot_id=screenshot_id,
                    )

            # Offer "솔루션 보기" button if there are solutions
            if diag.solutions:
                import json
                sol_btn = [[
                    InlineKeyboardButton(
                        "\U0001f4a1 솔루션 상세 보기",
                        callback_data="sol:detail:0",
                    ),
                ]]
                await query.message.reply_text(
                    "솔루션 상세를 확인하시겠습니까?",
                    reply_markup=InlineKeyboardMarkup(sol_btn),
                )
                # Store solutions in user_data for callback
                context_data = getattr(query, "_context_data", None)

        except Exception as e:
            logger.error("Account diagnosis failed: %s", e, exc_info=True)

    async def _action_solution_detail(self, query, context, payload: str) -> None:
        """Handle [솔루션 보기] callback."""
        try:
            solutions = self.db.get_pending_solutions()
            sol_dicts = [
                {"type": s.get("solution_type", ""),
                 "urgency": "medium",
                 "description": s.get("description", "").split(" -> ")[0] if " -> " in s.get("description", "") else s.get("description", ""),
                 "action": s.get("description", "").split(" -> ")[1] if " -> " in s.get("description", "") else ""}
                for s in solutions
            ]
            msg = format_solution_detail(sol_dicts)
            await query.edit_message_text(msg)
        except Exception as e:
            logger.error("Solution detail callback failed: %s", e, exc_info=True)
            try:
                await query.edit_message_text("\u26a0\ufe0f 솔루션 조회 중 오류가 발생했습니다.")
            except Exception:
                logger.debug("_action_solution_detail error recovery edit_text also failed", exc_info=True)

    # == v4.1: Profit-Taking callbacks ==========================================

    async def _action_profit_taking(self, query, context, payload: str) -> None:
        """차익실현 알림 콜백.

        콜백: pt:sell:{ticker}:{shares}, pt:ignore:{ticker}, pt:snooze:{ticker}
        """
        try:
            parts = payload.split(":")
            action = parts[0] if parts else ""

            if action == "sell":
                ticker = parts[1] if len(parts) > 1 else ""
                shares = int(parts[2]) if len(parts) > 2 else 0
                name = ""
                for h in self.db.get_active_holdings():
                    if h.get("ticker") == ticker:
                        name = h.get("name", ticker)
                        break

                # 매도 기록
                if shares > 0 and ticker:
                    self.db.add_trade(
                        ticker=ticker, name=name, action="sell",
                        strategy_type="profit_taking",
                        recommended_price=0, action_price=0,
                        quantity_pct=0,
                    )
                    # 트레일링 스탑 리셋
                    if hasattr(self, '_position_sizer'):
                        self._position_sizer.reset_trailing_stop(ticker)

                await query.edit_message_text(
                    f"✅ {name or ticker} {shares}주 매도 기록 완료\n\n"
                    f"실제 매도는 증권사 앱에서 진행하세요."
                )

            elif action == "ignore":
                ticker = parts[1] if len(parts) > 1 else ""
                await query.edit_message_text(
                    f"👌 확인했습니다. 알림을 무시합니다."
                )

            elif action == "snooze":
                ticker = parts[1] if len(parts) > 1 else ""
                # 1시간 뮤트
                if hasattr(self, '_muted_tickers'):
                    import time
                    self._muted_tickers[ticker] = time.time() + 3600
                await query.edit_message_text(
                    f"⏰ 1시간 뒤에 다시 알려드릴게요."
                )

        except Exception as e:
            logger.error("Profit taking callback error: %s", e, exc_info=True)
            try:
                await query.edit_message_text("⚠️ 처리 중 오류가 발생했습니다.")
            except Exception:
                logger.debug("_action_profit_taking error recovery edit_text also failed", exc_info=True)

    # == Usage guide ===========================================================


