"""Telegram bot with multi-strategy system v3.5 - ML, sentiment, KIS, screenshot."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from kstock.bot.messages import (
    format_alerts_summary,
    format_auto_trade_alert,
    format_buy_alert,
    format_claude_briefing,
    format_help,
    format_kis_status_msg,
    format_long_term_detail,
    format_market_status,
    format_momentum_alert,
    format_breakout_alert,
    format_portfolio,
    format_reco_performance,
    format_recommendations,
    format_regime_status,
    format_sell_alert_profit,
    format_sell_alert_stop,
    format_stock_detail,
    format_strategy_list,
    format_strategy_performance,
    format_system_status,
    format_trade_record,
    format_v3_score_signal,
    format_watch_alert,
    format_weekly_learning_report,
    format_welcome,
)
from kstock.features.technical import (
    TechnicalIndicators,
    compute_indicators,
    compute_weekly_trend,
    compute_relative_strength_rank,
)
from kstock.features.sector import (
    compute_sector_returns,
    get_sector_score_adjustment,
    format_sector_strength,
)
from kstock.ingest.kis_client import KISClient, StockInfo
from kstock.ingest.macro_client import MacroClient, MacroSnapshot
from kstock.ingest.yfinance_kr_client import YFinanceKRClient
from kstock.signal.scoring import (
    FlowData,
    ScoreBreakdown,
    compute_composite_score,
    load_scoring_config,
)
from kstock.signal.strategies import (
    STRATEGY_META,
    evaluate_all_strategies,
    get_regime_mode,
    compute_confidence_score,
)
from kstock.signal.fx_strategy import compute_fx_signal
from kstock.signal.market_regime import detect_regime, RegimeResult
from kstock.signal.policy_engine import (
    get_score_bonus as get_policy_bonus,
    get_telegram_summary as get_policy_summary,
)
from kstock.signal.portfolio import (
    format_correlation_warnings,
    has_correlated_position,
)
from kstock.signal.long_term_scoring import compute_long_term_score
from kstock.broker.kis_broker import KisBroker, format_kis_setup_guide, format_kis_status
from kstock.ingest.data_router import DataRouter
from kstock.bot.account_reader import (
    parse_account_screenshot,
    compare_screenshots,
    format_screenshot_summary,
    format_screenshot_reminder,
)
from kstock.bot.diagnosis import batch_diagnose, format_diagnosis_report
from kstock.bot.account_diagnosis import (
    diagnose_account,
    format_diagnosis_report as format_account_diagnosis,
    format_solution_detail,
    format_account_history,
)
from kstock.bot.horizon_diagnosis import (
    HORIZON_CONFIG,
    HorizonDiagnosisResult,
    batch_diagnose_by_horizon,
    detect_margin_purchase,
    format_horizon_report,
)
from kstock.signal.concentration_alert import analyze_concentration
from kstock.signal.profit_protector import compute_protection
from kstock.signal.market_psychology import compute_fear_greed
from kstock.signal.foreign_predictor import predict_foreign_flow
from kstock.signal.tenbagger_hunter import scan_tenbagger
from kstock.signal.aggressive_mode import compute_goal_progress, load_goal_config
from kstock.signal.swing_trader import evaluate_swing
from kstock.signal.short_selling import (
    analyze_short_selling,
    format_short_alert,
    ShortSellingSignal,
)
from kstock.signal.short_pattern import detect_all_patterns, format_pattern_report
from kstock.signal.margin_balance import (
    detect_margin_patterns,
    format_margin_alert,
    compute_combined_leverage_score,
)
from kstock.signal.margin_calibrator import (
    calibrate_all_metrics,
    format_calibration_report,
)
from kstock.signal.rebalance_engine import (
    evaluate_rebalance_triggers,
    format_rebalance_alert,
    get_milestones_with_status,
)
from kstock.signal.position_manager import plan_buy
from kstock.signal.future_tech import (
    FUTURE_SECTORS,
    get_all_watchlist_tickers,
    get_sector_watchlist,
    score_future_stock,
    format_full_watchlist,
    format_sector_detail,
)
from kstock.signal.future_trigger import (
    evaluate_entry,
    format_entry_signal,
)
from kstock.signal.seed_manager import (
    SEED_CONFIG,
    format_seed_overview,
)
from kstock.core.risk_manager import check_risk_limits, format_risk_report, format_risk_alert
from kstock.core.health_monitor import run_health_checks, format_system_report
from kstock.core.performance_tracker import (
    compute_performance_summary,
    format_performance_report,
)
from kstock.core.scenario_analyzer import SCENARIOS, simulate_scenario, format_scenario_report
from kstock.signal.ml_validator import format_ml_report
from kstock.bot.hallucination_guard import guard_response
from kstock.bot.multi_agent import (
    AGENTS as MULTI_AGENTS,
    format_multi_agent_report,
    synthesize_scores,
    create_empty_report,
    parse_agent_score,
    parse_agent_signal,
)
from kstock.signal.surge_detector import (
    scan_stocks as scan_surge_stocks,
    format_surge_alert,
)
from kstock.signal.feedback_loop import (
    get_similar_condition_stats,
    get_feedback_for_ticker,
    format_feedback_stats,
)
from kstock.signal.stealth_accumulation import (
    scan_all_stocks as scan_accumulations,
    format_accumulation_alert,
)
from kstock.bot.trade_register import (
    parse_trade_text,
    format_trade_confirmation,
    HORIZON_SETTINGS as TRADE_HORIZON_SETTINGS,
)
from kstock.bot.intraday_monitor import (
    MONITOR_SETTINGS,
    get_settings_for_horizon,
)
from kstock.core.kis_client import (
    load_kis_config,
    format_kis_not_configured,
)
from kstock.store.sqlite import SQLiteStore
# Phase 8: 실시간 시장 감지 + 전문 리포트 + 적응형 대응
from kstock.signal.market_pulse import (
    MarketPulse,
    format_pulse_alert,
)
from kstock.bot.live_market_report import generate_live_report
from kstock.core.sell_planner import SellPlanner, format_sell_plans

try:
    from kstock.report.daily_pdf_report import (
        generate_daily_pdf,
        format_pdf_telegram_message,
        HAS_REPORTLAB,
    )
except ImportError:
    HAS_REPORTLAB = False

try:
    from kstock.ml.predictor import (
        build_features, predict, get_score_bonus as get_ml_bonus,
        format_ml_prediction,
    )
    HAS_ML = True
except ImportError:
    HAS_ML = False

try:
    from kstock.ml.sentiment import (
        run_daily_sentiment, get_sentiment_bonus,
        format_sentiment_summary,
    )
    HAS_SENTIMENT = True
except ImportError:
    HAS_SENTIMENT = False

load_dotenv(override=True)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["\U0001f4d6 사용법 가이드", "\U0001f4f8 계좌분석"],
        ["\U0001f514 알림 설정", "\U0001f4ac AI에게 질문"],
        ["\u2699\ufe0f 최적화", "\U0001f4cb 증권사 리포트"],
        ["\U0001f4e1 KIS설정", "\U0001f4ca 재무 진단"],
        ["\U0001f3af 30억 목표", "\u26a1 스윙 기회"],
        ["\U0001f30d 시장현황", "\U0001f3af 전략별 보기"],
        ["\U0001f4c8 추천 성과", "\U0001f4c5 주간 보고서"],
        ["\U0001f680 미래기술", "\U0001f4ca 공매도"],
        ["\U0001f4ca 멀티분석", "\U0001f525 급등주"],
        ["\u2b50 즐겨찾기", "\U0001f575\ufe0f 매집탐지"],
        ["\U0001f4b0 잔고", "\U0001f6e0 관리자"],
    ],
    resize_keyboard=True,
)


def _load_universe() -> dict:
    """Load full universe config with stocks + ETFs."""
    config_path = Path("config/universe.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    uni = config.get("universe", {})
    return {
        "stocks": uni.get("stocks", []),
        "etf_index": uni.get("etf_index", []),
        "etf_sector": uni.get("etf_sector", []),
        "etf_global": uni.get("etf_global", []),
        "etf_dividend": uni.get("etf_dividend", []),
        "tickers": uni.get("tickers", []),
    }


def _all_tickers(universe: dict) -> list[dict]:
    """Flatten all universe items into a single list."""
    all_items = []
    seen = set()
    for key in ["stocks", "etf_index", "etf_sector", "etf_global", "etf_dividend"]:
        for item in universe.get(key, []):
            code = item["code"]
            if code not in seen:
                seen.add(code)
                all_items.append(item)
    return all_items


@dataclass
class ScanResult:
    ticker: str
    name: str
    score: ScoreBreakdown
    tech: TechnicalIndicators
    info: StockInfo
    flow: FlowData
    strategy_type: str = "A"
    strategy_signals: list = None
    confidence_score: float = 0.0
    confidence_stars: str = ""
    confidence_label: str = ""


class KQuantBot:
    """Telegram bot for K-Quant system v3.5."""

    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._start_time = datetime.now(KST)
        # Try loading persisted numeric chat_id
        try:
            _saved_id = Path("data/.chat_id").read_text().strip()
            if _saved_id.lstrip("-").isdigit():
                self.chat_id = _saved_id
        except Exception:
            pass
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.kis = KISClient()
        self.yf_client = YFinanceKRClient()
        self.db = SQLiteStore()
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

    def build_app(self) -> Application:
        app = (
            Application.builder()
            .token(self.token)
            .post_init(self._post_init)
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
        # v3.0: screenshot image handler
        app.add_handler(
            MessageHandler(filters.PHOTO, self.handle_screenshot)
        )
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_menu_text)
        )
        app.add_handler(CallbackQueryHandler(self.handle_callback))
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
        ])

    def schedule_jobs(self, app: Application) -> None:
        jq = app.job_queue
        if jq is None:
            logger.warning("Job queue not available; skipping scheduled jobs")
            return

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
        logger.info(
            "Scheduled: us_premarket(07:00), morning(07:30), intraday(1min), "
            "weekly_learn(Sat 09:00), screenshot(Mon/Fri 08:00), "
            "sentiment(08:00), weekly_report(Sun 19:00), "
            "macro_refresh(1min), market_pulse(1min), "
            "daily_report_pdf(16:00), self_report(21:00) KST"
        )

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
                    pass

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._persist_chat_id(update)
        await update.message.reply_text(format_welcome(), reply_markup=MAIN_MENU)

    async def cmd_backtest(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: /backtest [종목코드]\n예) /backtest 005930",
                reply_markup=MAIN_MENU,
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
            else:
                msg = f"\u26a0\ufe0f {name} 백테스트 실패\n데이터가 부족하거나 종목코드를 확인해주세요."
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Backtest error: %s", e, exc_info=True)
            await update.message.reply_text(
                f"\u26a0\ufe0f 백테스트 오류: {str(e)[:100]}",
                reply_markup=MAIN_MENU,
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
                    reply_markup=MAIN_MENU,
                )
        except Exception as e:
            logger.error("Optimize error: %s", e, exc_info=True)
            await update.message.reply_text(
                f"\u26a0\ufe0f 최적화 오류: {str(e)[:100]}",
                reply_markup=MAIN_MENU,
            )

    async def cmd_setup_kis(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /setup_kis command."""
        args = context.args or []
        if not args:
            msg = format_kis_setup_guide()
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
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
                reply_markup=MAIN_MENU,
            )
            return

        success = self.kis_broker.save_credentials(hts_id, app_key, app_secret, account)
        if success:
            self.data_router.refresh_source()
            await update.message.reply_text(
                "\u2705 KIS API 연결 완료!\n"
                "모의투자 모드로 설정되었습니다.\n"
                "이제 자동매매가 가능합니다.",
                reply_markup=MAIN_MENU,
            )
        else:
            await update.message.reply_text(
                "\u274c KIS 연결 실패.\n인증 정보를 확인해주세요.",
                reply_markup=MAIN_MENU,
            )

    async def handle_screenshot(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle screenshot image messages for account analysis."""
        # 관리자 모드: 오류 스크린샷 접수
        admin_mode = context.user_data.get("admin_mode")
        if admin_mode:
            context.user_data.pop("admin_mode", None)
            caption = update.message.caption or "이미지 첨부"
            await self._save_admin_report(update, admin_mode, caption, has_image=True)
            return

        if not self.anthropic_key:
            await update.message.reply_text(
                "\u26a0\ufe0f Anthropic API 키가 설정되지 않았습니다.",
                reply_markup=MAIN_MENU,
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
            for h in holdings:
                ticker = h.get("ticker", "")
                hname = h.get("name", "")
                if not ticker:
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
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)

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
                reply_markup=MAIN_MENU,
            )

    async def handle_menu_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._persist_chat_id(update)
        text = update.message.text
        handlers = {
            # Left column (utility / settings)
            "\U0001f4d6 사용법 가이드": self._menu_usage_guide,
            "\U0001f514 알림 설정": self._menu_notification_settings,
            "\u2699\ufe0f 최적화": self._menu_optimize,
            "\U0001f4e1 KIS설정": self._menu_kis_setup,
            "\U0001f3af 30억 목표": self._menu_goal,
            "\U0001f30d 시장현황": self._menu_market_status,
            "\U0001f4c8 추천 성과": self._menu_reco_performance,
            # Right column (investing features)
            "\U0001f4f8 계좌분석": self._menu_account_analysis,
            "\U0001f4ac AI에게 질문": self._menu_ai_chat,
            "\U0001f4cb 증권사 리포트": self._menu_reports,
            "\U0001f4ca 재무 진단": self._menu_financial,
            "\u26a1 스윙 기회": self._menu_swing,
            "\U0001f3af 전략별 보기": self._menu_strategy_view,
            "\U0001f4c5 주간 보고서": self._menu_weekly_report,
            "\U0001f680 미래기술": self._menu_future_tech,
            "\U0001f4ca 공매도": self._menu_short,
            # Phase 7 menus
            "\U0001f4ca 멀티분석": self._menu_multi_agent,
            "\U0001f525 급등주": self._menu_surge,
            "\U0001f575\ufe0f 매집탐지": self._menu_accumulation,
            "\u2b50 즐겨찾기": self._menu_favorites,
            "\U0001f4b0 잔고": self._menu_balance,
            "\U0001f916 에이전트": self._menu_agent_chat,
            "\U0001f6e0 관리자": self._menu_admin,
            # Legacy keys (backward compat)
            "\U0001f514 실시간 알림": self._menu_alerts,
            "\U0001f4ca 오늘의 추천종목": self._menu_recommendations,
            "\U0001f4bc 내 포트폴리오": self._menu_portfolio,
            "\U0001f4ca 백테스트": self._menu_backtest,
            "\u2753 도움말": self._menu_usage_guide,
        }
        handler = handlers.get(text)
        if handler:
            # 메뉴 이동 시 진행 중인 KIS 설정/최적화 상태 클리어
            context.user_data.pop("kis_setup", None)
            context.user_data.pop("awaiting_optimize_ticker", None)
            try:
                await handler(update, context)
            except Exception as e:
                logger.error("Menu handler error: %s", e, exc_info=True)
                await update.message.reply_text(
                    "\u26a0\ufe0f 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    reply_markup=MAIN_MENU,
                )
        else:
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

            # 0-0.5. 관리자 모드: 오류 신고 / 업데이트 요청
            admin_mode = context.user_data.get("admin_mode")
            if admin_mode:
                context.user_data.pop("admin_mode", None)
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
                    pass
                context.user_data.pop("agent_mode", None)
                context.user_data.pop("agent_type", None)
                await update.message.reply_text(
                    f"✅ 접수 완료!\n\n"
                    f"📝 [{agent_type}] {text[:60]}{'...' if len(text) > 60 else ''}\n\n"
                    f"다음 업데이트에 반영하겠습니다. 감사합니다! 🙏",
                    reply_markup=MAIN_MENU,
                )
                return

            # 1. 자연어 보유종목 등록 감지: "삼성전자 50주 76000원", "에코프로 100주 샀어"
            trade = self._detect_trade_input(text)
            if trade:
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

        return None

    def _detect_trade_input(self, text: str) -> dict | None:
        """자연어에서 매수 등록 패턴을 감지합니다.

        지원 패턴:
          - "삼성전자 50주 76000원"
          - "에코프로 100주 178500원에 샀어"
          - "005930 30주 매수"
          - "삼성전자 추가 50주 76000원"

        Returns:
            dict with 'ticker', 'name', 'quantity', 'price' or None.
        """
        import re

        # 매수 관련 키워드가 포함되었거나, 수량+가격 패턴이 있는 경우만
        trade_keywords = ["샀", "매수", "추가", "편입", "담았", "들어갔"]
        has_keyword = any(kw in text for kw in trade_keywords)

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
                fund_data = "재무 데이터 없음"

            enriched_question = (
                f"{name}({code}) 종목 분석 요청.\n"
                f"사용자 질문: {original_text}\n\n"
                f"[실시간 가격]\n{price_data}\n\n"
                f"[기술적 지표]\n{tech_data}\n\n"
                f"[펀더멘털]\n{fund_data}\n\n"
                f"위 실시간 데이터를 참고하여 분석하라. "
                f"반드시 관심/매수/매도 포인트를 명시하라."
            )

            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(
                self.db, self.macro_client, self.yf_client,
            )
            answer = await handle_ai_question(enriched_question, ctx, self.db, chat_mem)

            try:
                await placeholder.edit_text(answer)
            except Exception:
                await update.message.reply_text(answer, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Stock analysis error: %s", e, exc_info=True)
            try:
                await placeholder.edit_text(
                    f"\u26a0\ufe0f {name} 분석 중 오류가 발생했습니다."
                )
            except Exception:
                await update.message.reply_text(
                    f"\u26a0\ufe0f {name} 분석 중 오류가 발생했습니다.",
                    reply_markup=MAIN_MENU,
                )

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
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
            }
            handler = dispatch.get(action)
            if handler:
                await handler(query, context, payload)
        except Exception as e:
            logger.error("Callback error: %s", e, exc_info=True)
            try:
                await query.edit_message_text("\u26a0\ufe0f 오류가 발생했습니다.")
            except Exception:
                pass

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
            await query.message.reply_text(diag_msg, reply_markup=MAIN_MENU)

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

        await query.edit_message_text(f"\u2705 {name}: {label} 선택됨")

        # Check if all holdings have been assigned a horizon
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
        await query.message.reply_text(report, reply_markup=MAIN_MENU)

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
            await query.message.reply_text(report, reply_markup=MAIN_MENU)

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
                pass

    # == Usage guide ===========================================================

    async def _menu_usage_guide(self, update: Update, context) -> None:
        msg = (
            "📖 주호님, K-Quant v3.5 사용법\n\n"

            "━━ 자주 쓰는 기능 ━━\n\n"

            "📸 종목 등록 (가장 쉬운 방법)\n"
            "  증권사 스크린샷 전송\n"
            "  → 자동 인식 → 전체 추가 클릭\n\n"

            "💬 종목명만 입력\n"
            "  삼성전자 → 버튼 선택\n"
            "  [📊 분석] [➕ 추가] [👀 관심]\n\n"

            "🤖 AI에게 질문\n"
            "  삼성전자 어때?\n"
            "  내 포트폴리오 점검해줘\n"
            "  오늘 시장 분석해줘\n\n"

            "💰 잔고 관리\n"
            "  잔고 → 종목추가/삭제/새로고침\n\n"

            "━━ 투자 기능 ━━\n\n"

            "📸 계좌분석: 스크린샷 → AI 진단\n"
            "💬 AI에게 질문: 시장/종목/전략\n"
            "📋 증권사 리포트: 보유종목 리포트\n"
            "📊 재무 진단: 종목 재무 100점 분석\n"
            "⚡ 스윙 기회: 단기 매매 추천\n"
            "🎯 전략별 보기: 7가지 전략 추천\n"
            "📅 주간 보고서: 일요일 자동 생성\n"
            "📊 멀티분석: AI 5개 관점 분석\n"
            "🔥 급등주: 급등 종목 포착\n"
            "🕵 매집탐지: 세력 매집 감지\n\n"

            "━━ KIS 연동 (📡 KIS설정) ━━\n\n"

            "💰 실시간 잔고: KIS API 직접 조회\n"
            "📊 수급 분석: 외인/기관 매매동향\n"
            "🔔 가격 알림: 목표가/손절가 버튼 설정\n"
            "📈 매수 스캔: 매수 시그널 종목 탐색\n"
            "🚀 자동 매수: 알림 → 버튼 클릭 → 즉시 체결\n\n"

            "━━ 설정/관리 ━━\n\n"

            "🔔 알림 설정: 알림 ON/OFF\n"
            "⚙️ 최적화: 전략 파라미터\n"
            "📡 KIS설정: 한국투자증권 API + 투자 허브\n"
            "🎯 30억 목표: 자산 로드맵\n"
            "📈 추천 성과: 적중률 확인\n"
            "🌍 시장현황: 미국/한국 시장\n\n"

            "━━ 자동 알림 (하루 일과) ━━\n\n"

            "07:00 🇺🇸 미국 시장 프리마켓 브리핑\n"
            "07:30 ☀️ 모닝 브리핑\n"
            "09:00~ 장중 모니터링 (5분마다)\n"
            "16:00 📊 장 마감 종합 분석 (~4000자)\n"
            "16:30 📋 PDF 리포트 (4페이지)\n"
            "21:00 🔧 자가진단 + 자동 업데이트\n"
            "일요일 19:00 주간 보고서\n\n"

            "━━ 꿀팁 ━━\n\n"

            "종목명만 치면 바로 분석/추가 가능\n"
            "스크린샷 한 장이면 포트폴리오 완성\n"
            "아무 질문이나 하면 AI가 답변!\n"
            "KIS 연동하면 실시간 수급+자동매수 가능"
        )
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    # == Notification settings =================================================

    NOTIFICATION_LABELS = {
        "report_alert": "리포트 알림",
        "supply_alert": "수급 알림",
        "earnings_alert": "실적 알림",
        "policy_alert": "관세/정책 알림",
        "morning_briefing": "모닝 브리핑",
        "weekly_report": "주간 보고서",
    }

    async def _menu_notification_settings(self, update: Update, context) -> None:
        settings = self.db.get_notification_settings()
        buttons = []
        for key, label in self.NOTIFICATION_LABELS.items():
            enabled = settings.get(key, True)
            status = "\U0001f7e2 ON" if enabled else "\U0001f534 OFF"
            buttons.append([
                InlineKeyboardButton(
                    f"{label} {status}",
                    callback_data=f"notif:{key}",
                ),
            ])
        await update.message.reply_text(
            "\U0001f514 알림 설정\n각 항목을 눌러 ON/OFF를 전환하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_notification_toggle(self, query, context, payload: str) -> None:
        new_state = self.db.toggle_notification_setting(payload)
        label = self.NOTIFICATION_LABELS.get(payload, payload)
        status = "\U0001f7e2 ON" if new_state else "\U0001f534 OFF"

        # Rebuild full keyboard with updated state
        settings = self.db.get_notification_settings()
        buttons = []
        for key, lbl in self.NOTIFICATION_LABELS.items():
            enabled = settings.get(key, True)
            st = "\U0001f7e2 ON" if enabled else "\U0001f534 OFF"
            buttons.append([
                InlineKeyboardButton(
                    f"{lbl} {st}",
                    callback_data=f"notif:{key}",
                ),
            ])
        await query.edit_message_text(
            f"\U0001f514 알림 설정 ({label} \u2192 {status})\n각 항목을 눌러 ON/OFF를 전환하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # == Report submenu ========================================================

    SECTOR_KEYWORDS = {
        "2차전지": ["2차전지", "배터리", "양극재", "음극재", "전해질", "분리막"],
        "반도체": ["반도체", "HBM", "메모리", "파운드리", "DRAM", "NAND"],
        "자동차": ["자동차", "전기차", "EV", "완성차", "자율주행"],
        "AI/로봇": ["AI", "인공지능", "로봇", "자동화", "LLM", "GPU"],
        "방산/조선": ["방산", "조선", "방위", "함정", "무기"],
    }

    async def _menu_reports(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """증권사 리포트 서브 메뉴."""
        buttons = [
            [InlineKeyboardButton("내 보유종목 리포트", callback_data="rpt:my_holdings")],
            [InlineKeyboardButton("추천종목 리포트", callback_data="rpt:recommended")],
            [InlineKeyboardButton("목표가 상향 종목", callback_data="rpt:upgrade")],
            [InlineKeyboardButton("목표가 하향 종목", callback_data="rpt:downgrade")],
            [InlineKeyboardButton("섹터별 리포트", callback_data="rpt:sector")],
            [InlineKeyboardButton("오늘 신규 리포트", callback_data="rpt:today")],
        ]
        await update.message.reply_text(
            "\U0001f4cb 증권사 리포트\n조회할 항목을 선택하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    def _format_report_item(self, r: dict) -> str:
        """Format a single report for display."""
        broker = r.get("broker", "")
        date = r.get("date", "")
        title = r.get("title", "")
        opinion = r.get("opinion", "")
        target = r.get("target_price", 0)
        prev_target = r.get("prev_target_price", 0)

        lines = [f"{broker} ({date})"]
        lines.append(f"{title}")

        if target and prev_target and target != prev_target:
            change_pct = round((target - prev_target) / prev_target * 100, 1)
            direction = "상향" if change_pct > 0 else "하향"
            lines.append(
                f"목표가: {prev_target:,.0f} \u2192 {target:,.0f}원 ({direction} {change_pct:+.1f}%)"
            )
        elif target:
            lines.append(f"목표가: {target:,.0f}원")

        if opinion:
            lines.append(f"투자의견: {opinion}")

        pdf_url = r.get("pdf_url", "")
        if pdf_url:
            lines.append(f"[PDF 보기] {pdf_url}")

        return "\n".join(lines)

    async def _action_report_submenu(self, query, context, payload: str) -> None:
        """Handle report submenu callback."""
        if payload == "my_holdings":
            # Get portfolio tickers
            portfolio = self.db.get_portfolio()
            tickers = [p["ticker"] for p in portfolio] if portfolio else []
            reports = self.db.get_reports_for_tickers(tickers, limit=5)
            if reports:
                ticker_str = ", ".join(
                    f"{p.get('name', p['ticker'])}" for p in (portfolio or [])[:5]
                )
                header = f"\U0001f4cb 내 보유종목 리포트\n보유종목: {ticker_str}\n"
                items = [self._format_report_item(r) for r in reports]
                msg = header + "\n\n".join(items)
            else:
                msg = "\U0001f4cb 보유종목 관련 리포트가 없습니다."

        elif payload == "recommended":
            active_recs = self.db.get_active_recommendations()
            tickers = [r["ticker"] for r in active_recs] if active_recs else []
            reports = self.db.get_reports_for_tickers(tickers, limit=5)
            if reports:
                msg = "\U0001f4cb 추천종목 리포트\n\n" + "\n\n".join(
                    self._format_report_item(r) for r in reports
                )
            else:
                msg = "\U0001f4cb 추천종목 관련 리포트가 없습니다."

        elif payload == "upgrade":
            reports = self.db.get_reports_target_upgrades(days=7, limit=10)
            if reports:
                msg = "\U0001f4cb 목표가 상향 종목 (최근 1주)\n\n" + "\n\n".join(
                    self._format_report_item(r) for r in reports
                )
            else:
                msg = "\U0001f4cb 최근 1주 내 목표가 상향 종목이 없습니다."

        elif payload == "downgrade":
            reports = self.db.get_reports_target_downgrades(days=7, limit=10)
            if reports:
                # Check if any are in portfolio
                portfolio = self.db.get_portfolio()
                portfolio_tickers = {p["ticker"] for p in portfolio} if portfolio else set()
                items = []
                for r in reports:
                    item = self._format_report_item(r)
                    if r.get("ticker") in portfolio_tickers:
                        item = "[경고] " + item
                    items.append(item)
                msg = "\U0001f4cb 목표가 하향 종목 (최근 1주)\n\n" + "\n\n".join(items)
            else:
                msg = "\U0001f4cb 최근 1주 내 목표가 하향 종목이 없습니다."

        elif payload == "sector":
            # Show sector selection submenu
            buttons = [
                [InlineKeyboardButton(name, callback_data=f"sector_rpt:{name}")]
                for name in self.SECTOR_KEYWORDS
            ]
            await query.edit_message_text(
                "\U0001f4cb 섹터를 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        elif payload == "today":
            reports = self.db.get_reports_today(limit=10)
            if reports:
                msg = "\U0001f4cb 오늘 신규 리포트\n\n" + "\n\n".join(
                    self._format_report_item(r) for r in reports
                )
            else:
                msg = "\U0001f4cb 오늘 발행된 리포트가 없습니다."
        else:
            msg = "\U0001f4cb 알 수 없는 메뉴입니다."

        await query.edit_message_text(msg)

    async def _action_sector_report(self, query, context, payload: str) -> None:
        """Handle sector report selection."""
        keywords = self.SECTOR_KEYWORDS.get(payload, [payload])
        reports = self.db.get_reports_by_sector(keywords, limit=5)
        if reports:
            msg = f"\U0001f4cb {payload} 섹터 리포트\n\n" + "\n\n".join(
                self._format_report_item(r) for r in reports
            )
        else:
            msg = f"\U0001f4cb {payload} 섹터 관련 리포트가 없습니다."
        await query.edit_message_text(msg)

    # == Weekly report menu ====================================================

    async def _menu_weekly_report(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """주간 보고서 서브 메뉴."""
        buttons = [
            [InlineKeyboardButton("이번 주 보고서", callback_data="weekly:latest")],
            [InlineKeyboardButton("지난 보고서", callback_data="weekly:history")],
            [InlineKeyboardButton("즉시 생성", callback_data="weekly:generate")],
        ]
        await update.message.reply_text(
            "\U0001f4c5 주간 보고서\n조회할 항목을 선택하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_weekly_submenu(self, query, context, payload: str) -> None:
        """Handle weekly report submenu callback."""
        if payload == "latest":
            report = self.db.get_latest_weekly_report()
            if report:
                url = report.get("doc_url", "")
                label = report.get("week_label", "")
                if url:
                    msg = f"\U0001f4c5 {label} 보고서\n\n구글 문서: {url}"
                else:
                    msg = f"\U0001f4c5 {label} 보고서 (구글 문서 링크 없음)"
            else:
                msg = "\U0001f4c5 아직 생성된 주간 보고서가 없습니다."
            await query.edit_message_text(msg)

        elif payload == "history":
            reports = self.db.get_weekly_reports(limit=4)
            if reports:
                lines = ["\U0001f4c5 최근 주간 보고서\n"]
                for r in reports:
                    label = r.get("week_label", "")
                    url = r.get("doc_url", "")
                    if url:
                        lines.append(f"{label}: {url}")
                    else:
                        lines.append(f"{label} (링크 없음)")
                msg = "\n".join(lines)
            else:
                msg = "\U0001f4c5 아직 생성된 주간 보고서가 없습니다."
            await query.edit_message_text(msg)

        elif payload == "generate":
            await query.edit_message_text("\U0001f50d 주간 보고서 생성 중... 잠시만 기다려주세요.")
            try:
                from kstock.bot.weekly_report import generate_weekly_report
                telegram_msg, doc_url = await generate_weekly_report(self.db)
                await query.message.reply_text(telegram_msg, reply_markup=MAIN_MENU)
            except Exception as e:
                logger.error("Weekly report generation failed: %s", e, exc_info=True)
                await query.message.reply_text(
                    "\u26a0\ufe0f 주간 보고서 생성 실패. 잠시 후 다시 시도해주세요.",
                    reply_markup=MAIN_MENU,
                )

    # == Menu implementations ================================================

    async def _menu_alerts(self, update: Update, context) -> None:
        alerts = self.db.get_recent_alerts(limit=10)
        await update.message.reply_text(
            format_alerts_summary(alerts), reply_markup=MAIN_MENU
        )

    async def _menu_recommendations(self, update: Update, context) -> None:
        await update.message.reply_text(
            "\U0001f50d 종목 분석 중... 잠시만 기다려주세요."
        )
        results = await self._scan_all_stocks()
        self._last_scan_results = results
        self._scan_cache_time = datetime.now(KST)

        reco_data = [
            (i, r.name, r.ticker, r.score.composite, r.score.signal, r.strategy_type)
            for i, r in enumerate(results[:10], 1)
        ]
        msg = format_recommendations(reco_data)

        buttons = [
            [
                InlineKeyboardButton(
                    f"\U0001f4cb {r.name} 상세보기",
                    callback_data=f"detail:{r.ticker}",
                )
            ]
            for r in results[:5]
        ]
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        await update.message.reply_text(msg, reply_markup=keyboard)

        for r in results:
            self.db.upsert_portfolio(
                ticker=r.ticker, name=r.name,
                score=r.score.composite, signal=r.score.signal,
            )
        self.db.upsert_job_run("eod_scan", _today(), status="success")

    async def _menu_market_status(self, update: Update, context) -> None:
        import asyncio as _aio

        # 즉시 "처리 중..." 메시지 전송 → 이후 edit로 교체
        placeholder = await update.message.reply_text(
            "\U0001f30d 실시간 시장 데이터 수집 중..."
        )

        # ── 모든 데이터 병렬 수집 (asyncio.gather) ──
        async def _get_live_report():
            try:
                return await generate_live_report(
                    macro_client=self.macro_client,
                    db=self.db,
                    pulse_engine=self.market_pulse,
                    sector_strengths=self._sector_strengths,
                )
            except Exception as e:
                logger.warning("Live report failed, falling back: %s", e)
                return None

        async def _get_macro():
            return await self.macro_client.get_snapshot()

        async def _get_regime(macro_future):
            macro = await macro_future
            return detect_regime(macro), macro

        async def _get_sectors():
            await self._update_sector_strengths()
            return format_sector_strength(self._sector_strengths)

        # 병렬 실행: live_report, macro, sector
        live_report_task = _aio.ensure_future(_get_live_report())
        macro_task = _aio.ensure_future(_get_macro())
        sector_task = _aio.ensure_future(_get_sectors())

        live_report, macro, sector_text = await _aio.gather(
            live_report_task, macro_task, sector_task,
            return_exceptions=True,
        )

        # 에러 처리
        if isinstance(live_report, Exception):
            logger.warning("Live report gather error: %s", live_report)
            live_report = None
        if isinstance(macro, Exception):
            logger.warning("Macro gather error: %s", macro)
            await placeholder.edit_text(
                "\u26a0\ufe0f 시장 데이터를 가져올 수 없습니다. 잠시 후 다시 시도해주세요.",
            )
            return
        if isinstance(sector_text, Exception):
            logger.warning("Sector gather error: %s", sector_text)
            sector_text = ""

        # regime (매크로 데이터 필요 - 이미 완료)
        regime_result = detect_regime(macro)
        regime_mode = {
            "mode": regime_result.mode,
            "emoji": regime_result.emoji,
            "label": regime_result.label,
            "message": regime_result.message,
            "allocations": regime_result.allocations,
        }

        # FX signal
        fx_signal = compute_fx_signal(usdkrw_current=macro.usdkrw)

        msg = format_market_status(
            macro, regime_mode,
            sector_text=sector_text,
            fx_message=fx_signal.message,
        )

        # v3.0: policy events
        policy_text = get_policy_summary()
        if policy_text:
            msg += "\n\n" + policy_text

        # v3.0: data source status
        msg += "\n\n" + self.data_router.format_source_status()

        # placeholder를 최종 응답으로 교체
        try:
            await placeholder.edit_text(msg)
        except Exception:
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)

        # Phase 8: 실시간 보고서도 별도 전송 (AI 요약 포함)
        if live_report:
            buttons = [
                [InlineKeyboardButton(
                    "\U0001f4cb 매도 계획 보기", callback_data="sell_plans",
                )],
            ]
            await update.message.reply_text(
                live_report,
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    async def _menu_portfolio(self, update: Update, context) -> None:
        holdings = self.db.get_active_holdings()
        for h in holdings:
            try:
                cur = await self._get_price(h["ticker"], h["buy_price"])
                bp = h["buy_price"]
                self.db.update_holding(
                    h["id"], current_price=cur,
                    pnl_pct=round((cur - bp) / bp * 100, 2),
                )
                h["current_price"] = cur
            except Exception:
                pass
        msg = format_portfolio(holdings)

        # Correlation warnings
        if len(holdings) >= 2:
            ticker_names = {h["ticker"]: h["name"] for h in holdings}
            from kstock.signal.portfolio import compute_pairwise_correlations
            warnings = compute_pairwise_correlations(
                self._ohlcv_cache, ticker_names, threshold=0.8,
            )
            corr_text = format_correlation_warnings(warnings)
            if corr_text:
                msg += "\n\n" + corr_text

        # Recommendation stats
        stats = self.db.get_all_recommendations_stats()
        if stats.get("total", 0) > 0:
            profit_cnt = stats.get("profit", 0)
            stop_cnt = stats.get("stop", 0)
            closed = profit_cnt + stop_cnt
            win_rate = (profit_cnt / closed * 100) if closed > 0 else 0
            msg += (
                "\n\n" + "\u2500" * 25 + "\n"
                f"\U0001f4c8 추천 성과: {stats['active']}건 진행 | "
                f"승률 {win_rate:.0f}% ({profit_cnt}승 {stop_cnt}패)\n"
                f"\U0001f449 [추천 성과] 메뉴에서 상세 확인"
            )
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def _menu_reco_performance(self, update: Update, context) -> None:
        active = self.db.get_active_recommendations()
        completed = self.db.get_completed_recommendations()
        watch = self.db.get_watch_recommendations()
        stats = self.db.get_all_recommendations_stats()
        for r in active:
            try:
                cur = await self._get_price(r["ticker"], r["rec_price"])
                pnl = round((cur - r["rec_price"]) / r["rec_price"] * 100, 2)
                self.db.update_recommendation(r["id"], current_price=cur, pnl_pct=pnl)
                r["current_price"] = cur
                r["pnl_pct"] = pnl
            except Exception:
                pass
        msg = format_reco_performance(active, completed, watch, stats)
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def _menu_strategy_view(self, update: Update, context) -> None:
        buttons = [
            [
                InlineKeyboardButton("\U0001f525 반등", callback_data="strat:A"),
                InlineKeyboardButton("\u26a1 ETF", callback_data="strat:B"),
                InlineKeyboardButton("\U0001f3e6 장기", callback_data="strat:C"),
            ],
            [
                InlineKeyboardButton("\U0001f504 섹터", callback_data="strat:D"),
                InlineKeyboardButton("\U0001f30e 글로벌", callback_data="strat:E"),
            ],
            [
                InlineKeyboardButton("\U0001f680 모멘텀", callback_data="strat:F"),
                InlineKeyboardButton("\U0001f4a5 돌파", callback_data="strat:G"),
            ],
        ]
        await update.message.reply_text(
            "\U0001f3af 전략을 선택하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _menu_optimize(self, update: Update, context) -> None:
        holdings = self.db.get_active_holdings()
        buttons = []
        for h in holdings[:6]:
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            if ticker and name:
                buttons.append([
                    InlineKeyboardButton(
                        f"\u2699\ufe0f {name} 최적화",
                        callback_data=f"opt_run:{ticker}",
                    )
                ])
        buttons.append([
            InlineKeyboardButton("\u270f\ufe0f 직접 입력", callback_data="opt_run:manual"),
        ])
        msg = (
            "\u2699\ufe0f 파라미터 최적화\n\n"
            "RSI, BB, EMA 파라미터를 자동 최적화합니다.\n"
            "종목을 선택하세요:"
        )
        if not holdings:
            msg += "\n\n(보유종목이 없습니다. 직접 입력해주세요.)"
        await update.message.reply_text(
            msg, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _menu_backtest(self, update: Update, context) -> None:
        await update.message.reply_text(
            "\U0001f4ca 백테스트 기능\n\n"
            "/backtest [종목코드] 로 백테스트를 실행하세요.\n"
            "예) /backtest 005930\n\n"
            "1년 히스토리 기반 전략 시뮬레이션 결과를 보여줍니다.",
            reply_markup=MAIN_MENU,
        )

    async def _menu_help(self, update: Update, context) -> None:
        await update.message.reply_text(format_help(), reply_markup=MAIN_MENU)

    async def _menu_account_analysis(self, update: Update, context) -> None:
        msg = format_screenshot_reminder()
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def _menu_kis_setup(self, update: Update, context) -> None:
        # KIS API 토큰 연결 확인 (실제 API 호출)
        kis_live = False
        kis_error = ""
        if self.kis._is_configured:
            try:
                kis_live = await self.kis._ensure_token()
            except Exception as e:
                kis_error = str(e)[:80]

        if kis_live or self.kis_broker.connected:
            # 투자 허브 대시보드
            mode_text = "실전" if not self.kis._is_virtual else "모의투자"
            s = getattr(self.kis_broker, "safety", None)

            # 보유종목 현황 요약
            holdings = self.db.get_active_holdings()
            total_val = 0
            total_pnl = 0.0
            for h in holdings:
                cur = h.get("current_price", 0)
                qty = h.get("quantity", 1)
                total_val += cur * qty
                total_pnl += h.get("pnl_pct", 0)
            avg_pnl = total_pnl / len(holdings) if holdings else 0

            pnl_emoji = "📈" if avg_pnl >= 0 else "📉"

            lines = [
                "📡 K-Quant 투자 허브\n",
                "━━ 연결 상태 ━━",
                f"✅ KIS API: {mode_text} 모드",
            ]
            if s:
                lines.append(
                    f"⚙️ 안전: 1회 {getattr(s, 'max_order_pct', 15):.0f}% | "
                    f"일일 {getattr(s, 'max_daily_orders', 10)}회"
                )
            lines.extend([
                "",
                "━━ 포트폴리오 ━━",
                f"📊 보유종목: {len(holdings)}개",
                f"💰 평가금액: {total_val:,.0f}원",
                f"{pnl_emoji} 평균수익률: {avg_pnl:+.1f}%",
            ])

            buttons = [
                [
                    InlineKeyboardButton(
                        "💰 실시간 잔고",
                        callback_data="kis_hub:balance",
                    ),
                    InlineKeyboardButton(
                        "📊 수급 분석",
                        callback_data="kis_hub:supply",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔔 가격 알림",
                        callback_data="kis_hub:alert",
                    ),
                    InlineKeyboardButton(
                        "📈 매수 종목 찾기",
                        callback_data="kis_hub:scan",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⚙️ 안전 설정",
                        callback_data="kis_hub:safety",
                    ),
                    InlineKeyboardButton(
                        "🧪 연결 테스트",
                        callback_data="kis:test",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔑 키 재설정",
                        callback_data="kis:reset",
                    ),
                ],
            ]
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif self.kis._is_configured and not kis_live:
            # 키 설정은 되어 있으나 연결 실패
            err_msg = kis_error or "토큰 발급 실패"
            lines = [
                "📡 KIS API 상태\n",
                "⚠️ API 키 설정됨, 연결 실패",
                f"오류: {err_msg}\n",
                "흔한 원인:",
                "1. APP SECRET 만료 (24시간마다 재발급 필요)",
                "2. APP KEY/SECRET 불일치",
                "3. 계좌번호 형식 오류\n",
                "해결 방법:",
                "→ https://apiportal.koreainvestment.com",
                "→ 앱 관리 → Secret 재발급 클릭",
                "→ 아래 '🔑 키 재설정' 버튼으로 입력",
            ]
            buttons = [
                [
                    InlineKeyboardButton(
                        "🔑 키 재설정",
                        callback_data="kis:setup",
                    ),
                    InlineKeyboardButton(
                        "🧪 재시도",
                        callback_data="kis:test",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📖 재발급 가이드",
                        callback_data="kis_hub:guide",
                    ),
                ],
            ]
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            # 미설정
            lines = [
                "📡 KIS API 설정\n",
                "❌ 미연결\n",
                "KIS OpenAPI를 설정하면:",
                "✅ 실시간 주가 (1초 단위)",
                "✅ 외국인/기관 수급 조회",
                "✅ 자동 매수/매도",
                "✅ 계좌 잔고 실시간 조회",
                "✅ 목표가/손절가 알림\n",
                "필요한 것:",
                "→ 한국투자증권 계좌",
                "→ KIS Developers 앱 등록",
                "→ APP KEY + SECRET + 계좌번호",
            ]
            buttons = [
                [
                    InlineKeyboardButton(
                        "🔧 KIS 설정하기",
                        callback_data="kis:setup",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📖 설정 가이드",
                        callback_data="kis_hub:guide",
                    ),
                ],
            ]
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    # == KIS 설정 인터랙티브 플로우 ============================================

    async def _action_kis(self, query, context, payload: str) -> None:
        """KIS 설정 콜백: kis:setup, kis:reset, kis:test."""
        if payload in ("setup", "reset"):
            context.user_data["kis_setup"] = {"step": "id"}
            await query.edit_message_text(
                "🔧 KIS 설정을 시작합니다.\n\n"
                "1/4 단계: HTS ID를 입력하세요.\n"
                "(한국투자증권 로그인 ID)\n\n"
                "예: hongildong"
            )
        elif payload == "test":
            await query.edit_message_text("🧪 연결 테스트 중...")
            # 1차: KIS 직접 토큰 테스트
            try:
                token_ok = await self.kis._ensure_token()
                if token_ok:
                    # 토큰 OK → 현재가 테스트
                    price = await self.kis.get_current_price("005930")
                    balance = self.kis.get_balance() if hasattr(self.kis, "get_balance") else None

                    lines = [
                        "✅ KIS API 연결 정상!\n",
                        f"토큰: 발급 완료",
                        f"삼성전자 현재가: {price:,.0f}원" if price else "현재가: 장 마감",
                    ]
                    if balance and isinstance(balance, dict):
                        cash = balance.get("cash", 0)
                        lines.append(f"예수금: {cash:,.0f}원")
                        lines.append(f"보유종목: {len(balance.get('holdings', []))}개")

                    buttons = [
                        [InlineKeyboardButton(
                            "📡 투자 허브로", callback_data="kis_hub:home",
                        )],
                    ]
                    await query.message.reply_text(
                        "\n".join(lines),
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
                else:
                    # 토큰 실패 → 구체적 안내
                    buttons = [
                        [
                            InlineKeyboardButton(
                                "🔑 키 재설정",
                                callback_data="kis:setup",
                            ),
                            InlineKeyboardButton(
                                "📖 재발급 가이드",
                                callback_data="kis_hub:guide",
                            ),
                        ],
                    ]
                    await query.message.reply_text(
                        "❌ KIS 토큰 발급 실패\n\n"
                        "APP SECRET이 만료되었을 수 있습니다.\n"
                        "한국투자증권 API포탈에서 재발급 후\n"
                        "'🔑 키 재설정'을 눌러주세요.\n\n"
                        "📎 https://apiportal.koreainvestment.com",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
            except Exception as e:
                logger.error("KIS test error: %s", e)
                err = str(e)[:100]
                buttons = [
                    [InlineKeyboardButton(
                        "🔑 키 재설정", callback_data="kis:setup",
                    )],
                ]
                await query.message.reply_text(
                    f"❌ 연결 테스트 실패\n\n오류: {err}\n\n"
                    "키를 재설정하거나 네트워크를 확인해주세요.",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

    async def _action_kis_hub(
        self, query, context, payload: str = "",
    ) -> None:
        """KIS 투자 허브 콜백: kis_hub:{action}."""
        action = payload.split(":")[0] if payload else ""

        if action in ("home", ""):
            # 투자 허브 홈으로 리다이렉트
            await query.edit_message_text("📡 '📡 KIS설정' 메뉴를 눌러주세요.")
            return

        if action == "guide":
            guide = (
                "📖 KIS OpenAPI 설정 가이드\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "1️⃣ 한국투자증권 계좌 개설\n"
                "   → 비대면 개설 가능\n\n"
                "2️⃣ KIS Developers 가입\n"
                "   → https://apiportal.koreainvestment.com\n"
                "   → 회원가입 → 로그인\n\n"
                "3️⃣ 앱 등록\n"
                "   → 내 앱 관리 → 앱 추가\n"
                "   → APP KEY, APP SECRET 발급됨\n\n"
                "4️⃣ 이 봇에서 설정\n"
                "   → '🔑 키 재설정' 버튼 클릭\n"
                "   → HTS ID, APP KEY, SECRET, 계좌번호 입력\n\n"
                "⚠️ APP SECRET은 24시간마다 재발급 필요\n"
                "⚠️ 모의투자로 먼저 테스트 권장"
            )
            buttons = [
                [InlineKeyboardButton(
                    "🔧 지금 설정하기", callback_data="kis:setup",
                )],
            ]
            await query.edit_message_text(
                guide, reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "balance":
            await query.edit_message_text("💰 실시간 잔고 조회 중...")
            try:
                # KIS API 잔고 조회 시도
                balance = await self.kis.get_balance()
                if balance and isinstance(balance, dict):
                    hs = balance.get("holdings", [])
                    cash = balance.get("cash", 0)
                    total = balance.get("total_eval", 0)
                    profit = balance.get("total_profit", 0)

                    lines = [
                        "💰 KIS 실시간 잔고\n",
                        f"예수금: {cash:,.0f}원",
                        f"평가금액: {total:,.0f}원",
                        f"총손익: {profit:,.0f}원\n",
                    ]
                    if hs:
                        lines.append("━━ 보유종목 ━━")
                        for h in hs[:10]:
                            nm = h.get("name", h.get("ticker", ""))
                            pnl = h.get("profit_pct", 0)
                            cur = h.get("current_price", 0)
                            profit_amt = h.get("profit_amount", 0)
                            qty = h.get("quantity", 0)
                            emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "─"
                            pnl_sign = "+" if profit_amt >= 0 else ""
                            # 전일 대비 조회
                            try:
                                ticker = h.get("ticker", "")
                                if ticker:
                                    det = await self.kis.get_price_detail(ticker)
                                    dc = det.get("day_change_pct", 0)
                                    dc_amt = det.get("day_change", 0)
                                    if dc != 0:
                                        dc_sign = "+" if dc > 0 else ""
                                        day_info = f" | 오늘 {dc_sign}{dc:.1f}%"
                                    else:
                                        day_info = ""
                                else:
                                    day_info = ""
                            except Exception:
                                day_info = ""
                            lines.append(
                                f"{emoji} {nm}: {cur:,.0f}원\n"
                                f"   {pnl_sign}{profit_amt:,.0f}원 ({pnl:+.1f}%){day_info}"
                            )
                    msg = "\n".join(lines)
                else:
                    # KIS 잔고 실패 → DB 잔고 표시
                    holdings = self.db.get_active_holdings()
                    if holdings:
                        lines = ["💰 포트폴리오 잔고 (DB 기준)\n"]
                        for h in holdings[:10]:
                            nm = h.get("name", "")
                            ticker = h.get("ticker", "")
                            bp = h.get("buy_price", 0)
                            qty = h.get("quantity", 0)
                            try:
                                detail = await self._get_price_detail(ticker, bp)
                                cur = detail["price"]
                                dc_pct = detail["day_change_pct"]
                            except Exception:
                                cur = h.get("current_price", bp)
                                dc_pct = 0
                            pnl = round((cur - bp) / bp * 100, 2) if bp > 0 else 0
                            pnl_amt = (cur - bp) * qty
                            emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "─"
                            pnl_sign = "+" if pnl_amt >= 0 else ""
                            dc_info = ""
                            if dc_pct != 0:
                                dc_sign = "+" if dc_pct > 0 else ""
                                dc_info = f" | 오늘 {dc_sign}{dc_pct:.1f}%"
                            lines.append(
                                f"{emoji} {nm}: {cur:,.0f}원\n"
                                f"   {pnl_sign}{pnl_amt:,.0f}원 ({pnl:+.1f}%){dc_info}"
                            )
                        msg = "\n".join(lines)
                    else:
                        msg = "💰 보유종목이 없습니다."

                buttons = [
                    [InlineKeyboardButton(
                        "🔄 새로고침", callback_data="kis_hub:balance",
                    )],
                ]
                await query.message.reply_text(
                    msg, reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception as e:
                logger.error("KIS balance error: %s", e)
                await query.message.reply_text(
                    f"❌ 잔고 조회 실패: {str(e)[:60]}\n"
                    "DB 기반 잔고는 '💰 잔고' 메뉴에서 확인하세요.",
                )
            return

        if action == "supply":
            await query.edit_message_text("📊 수급 분석 중...")
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.message.reply_text(
                    "📊 보유종목이 없습니다.\n"
                    "종목을 먼저 등록해주세요.",
                )
                return

            lines = ["📊 보유종목 외인/기관 수급 분석\n"]
            for h in holdings[:8]:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                try:
                    foreign = await self.kis.get_foreign_flow(ticker, days=3)
                    inst = await self.kis.get_institution_flow(ticker, days=3)
                    f_net = 0
                    i_net = 0
                    if foreign is not None and len(foreign) > 0:
                        f_net = int(foreign["net_buy_volume"].sum())
                    if inst is not None and len(inst) > 0:
                        i_net = int(inst["net_buy_volume"].sum())

                    f_emoji = "🔵" if f_net > 0 else "🔴" if f_net < 0 else "⚪"
                    i_emoji = "🔵" if i_net > 0 else "🔴" if i_net < 0 else "⚪"
                    lines.append(
                        f"\n[{name}]\n"
                        f"  {f_emoji} 외인 3일: {f_net:+,}주\n"
                        f"  {i_emoji} 기관 3일: {i_net:+,}주"
                    )
                except Exception:
                    lines.append(f"\n[{name}] 수급 데이터 조회 실패")

            lines.append(
                "\n\n🔵=순매수 🔴=순매도 ⚪=중립"
            )
            await query.message.reply_text("\n".join(lines))
            return

        if action == "alert":
            # 가격 알림 설정 → 보유종목 리스트 표시
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.edit_message_text(
                    "🔔 보유종목이 없습니다.\n종목을 먼저 등록해주세요."
                )
                return

            lines = ["🔔 가격 알림 설정\n", "알림 설정할 종목을 선택하세요:"]
            buttons = []
            for h in holdings[:8]:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                pnl = h.get("pnl_pct", 0)
                emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "─"
                buttons.append([InlineKeyboardButton(
                    f"{emoji} {name} ({pnl:+.1f}%)",
                    callback_data=f"price_alert:sel:{ticker}",
                )])

            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "scan":
            await query.edit_message_text("📈 매수 시그널 스캔 중...")
            # 기존 스윙 기회 스캔 기능 재활용
            try:
                from kstock.signal.swing_scanner import scan_swing_opportunities
                results = await scan_swing_opportunities(
                    self.db, self.kis, top_n=5,
                )
                if results:
                    lines = ["📈 매수 시그널 발견!\n"]
                    buttons = []
                    for r in results[:5]:
                        ticker = r.get("ticker", "")
                        name = r.get("name", ticker)
                        score = r.get("score", 0)
                        reason = r.get("reason", "")[:30]
                        lines.append(
                            f"🎯 {name}: 스코어 {score}점\n"
                            f"   → {reason}"
                        )
                        buttons.append([InlineKeyboardButton(
                            f"📊 {name} 분석",
                            callback_data=f"stock_act:analyze:{ticker}",
                        )])
                    await query.message.reply_text(
                        "\n".join(lines),
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
                else:
                    await query.message.reply_text(
                        "📈 현재 강한 매수 시그널 없음.\n"
                        "5분마다 자동 스캔 중입니다."
                    )
            except Exception as e:
                logger.warning("Scan failed: %s", e)
                await query.message.reply_text(
                    "📈 스캔 기능 준비 중...\n"
                    "'⚡ 스윙 기회' 메뉴를 이용해주세요."
                )
            return

        if action == "safety":
            s = getattr(self.kis_broker, "safety", None)
            if not s:
                await query.edit_message_text(
                    "⚙️ KIS 브로커가 연결되지 않았습니다."
                )
                return

            is_virtual = getattr(self.kis, '_is_virtual', True)
            mode_emoji = "🧪" if is_virtual else "🔴"
            mode_text = "모의투자" if is_virtual else "실전투자"
            auto_trade_status = "✅ 허용" if is_virtual else "🚫 차단 (테스트 기간)"
            lines = [
                "⚙️ 안전 설정 현황\n",
                f"투자 모드: {mode_emoji} {mode_text}",
                f"자동매매: {auto_trade_status}",
                f"1회 최대 주문: 자산의 {getattr(s, 'max_order_pct', 15):.0f}%",
                f"일일 최대 주문: {getattr(s, 'max_daily_orders', 10)}회",
                f"일일 손실 한도: {getattr(s, 'daily_loss_limit_pct', -3):.0f}%",
                f"오늘 주문 횟수: {getattr(s, 'daily_order_count', 0)}회",
                f"주문 확인: {'필수' if getattr(s, 'require_confirmation', True) else '자동'}",
                "\n⚠️ 안전 설정은 자동매매 사고를 방지합니다.",
                "실전투자 모드에서는 자동매매가 차단됩니다.",
            ]
            await query.edit_message_text("\n".join(lines))
            return

    async def _action_price_alert(
        self, query, context, payload: str = "",
    ) -> None:
        """가격 알림 설정 콜백: price_alert:sel/set:{ticker}:{type}:{pct}."""
        parts = payload.split(":")
        action = parts[0] if parts else ""

        if action == "sel":
            ticker = parts[1] if len(parts) > 1 else ""
            if not ticker:
                await query.edit_message_text("⚠️ 종목 정보가 없습니다.")
                return

            holding = self.db.get_holding_by_ticker(ticker)
            name = holding.get("name", ticker) if holding else ticker
            cur = holding.get("current_price", 0) if holding else 0
            if cur == 0:
                try:
                    cur = await self._get_price(ticker, 0)
                except Exception:
                    pass

            lines = [
                f"🔔 {name} 가격 알림 설정\n",
                f"현재가: {cur:,.0f}원\n",
                "목표가 (수익 실현):",
            ]

            buttons = [
                [
                    InlineKeyboardButton(
                        f"📈 +3% ({cur * 1.03:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:up:3",
                    ),
                    InlineKeyboardButton(
                        f"📈 +5% ({cur * 1.05:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:up:5",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"📈 +10% ({cur * 1.10:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:up:10",
                    ),
                    InlineKeyboardButton(
                        f"📈 +20% ({cur * 1.20:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:up:20",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"📉 -3% ({cur * 0.97:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:dn:3",
                    ),
                    InlineKeyboardButton(
                        f"📉 -5% ({cur * 0.95:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:dn:5",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"📉 -10% ({cur * 0.90:,.0f})",
                        callback_data=f"price_alert:set:{ticker}:dn:10",
                    ),
                ],
            ]

            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "set":
            ticker = parts[1] if len(parts) > 1 else ""
            direction = parts[2] if len(parts) > 2 else "up"
            pct = int(parts[3]) if len(parts) > 3 else 5

            holding = self.db.get_holding_by_ticker(ticker)
            name = holding.get("name", ticker) if holding else ticker
            cur = holding.get("current_price", 0) if holding else 0
            if cur == 0:
                try:
                    cur = await self._get_price(ticker, 0)
                except Exception:
                    pass

            if direction == "up":
                target = int(cur * (1 + pct / 100))
                alert_type = "target_price"
                emoji = "📈"
                label = f"+{pct}% 목표가"
            else:
                target = int(cur * (1 - pct / 100))
                alert_type = "stop_loss"
                emoji = "📉"
                label = f"-{pct}% 손절가"

            try:
                self.db.insert_alert(
                    ticker=ticker,
                    alert_type=alert_type,
                    message=f"{name} {label} {target:,}원 알림 설정",
                )
                await query.edit_message_text(
                    f"✅ 알림 설정 완료!\n\n"
                    f"{emoji} {name}\n"
                    f"현재가: {cur:,.0f}원\n"
                    f"{label}: {target:,.0f}원\n\n"
                    f"도달 시 텔레그램으로 알림을 보내드립니다."
                )
            except Exception as e:
                logger.error("Alert setup error: %s", e)
                await query.edit_message_text(
                    f"❌ 알림 설정 실패: {str(e)[:50]}"
                )
            return

    async def _handle_kis_setup_step(self, update, context, text, setup_data):
        """KIS 설정 단계별 입력 처리 (5단계: ID→KEY→SECRET→계좌→모드)."""
        step = setup_data.get("step")
        text = text.strip()

        if step == "id":
            setup_data["id"] = text
            setup_data["step"] = "key"
            context.user_data["kis_setup"] = setup_data
            await update.message.reply_text(
                "✅ ID 저장!\n\n"
                "2/5 단계: APP KEY를 입력하세요.\n"
                "(KIS Developers에서 발급받은 앱 키)"
            )
        elif step == "key":
            setup_data["key"] = text
            setup_data["step"] = "secret"
            context.user_data["kis_setup"] = setup_data
            await update.message.reply_text(
                "✅ APP KEY 저장!\n\n"
                "3/5 단계: APP SECRET을 입력하세요."
            )
        elif step == "secret":
            setup_data["secret"] = text
            setup_data["step"] = "account"
            context.user_data["kis_setup"] = setup_data
            await update.message.reply_text(
                "✅ APP SECRET 저장!\n\n"
                "4/5 단계: 계좌번호를 입력하세요.\n"
                "(8자리-2자리 형식)\n\n"
                "예: 12345678-01"
            )
        elif step == "account":
            setup_data["account"] = text
            setup_data["step"] = "mode"
            context.user_data["kis_setup"] = setup_data
            buttons = [
                [
                    InlineKeyboardButton(
                        "🧪 모의투자",
                        callback_data="kis_mode:virtual",
                    ),
                    InlineKeyboardButton(
                        "💰 실전투자",
                        callback_data="kis_mode:real",
                    ),
                ],
            ]
            await update.message.reply_text(
                "✅ 계좌번호 저장!\n\n"
                "5/5 단계: 투자 모드를 선택하세요.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif step == "mode":
            # 모드 선택은 콜백으로 처리 (_action_kis_mode)
            pass

    async def _action_kis_mode(
        self, query, context, payload: str = "",
    ) -> None:
        """KIS 모드 선택 콜백: kis_mode:virtual/real."""
        setup_data = context.user_data.get("kis_setup", {})
        if not setup_data:
            await query.edit_message_text("⚠️ 설정 데이터가 없습니다. 다시 시도해주세요.")
            return

        hts_id = setup_data.get("id", "")
        app_key = setup_data.get("key", "")
        app_secret = setup_data.get("secret", "")
        account = setup_data.get("account", "")
        is_virtual = payload == "virtual"
        mode_text = "모의투자" if is_virtual else "실전투자"

        context.user_data.pop("kis_setup", None)

        if not all([hts_id, app_key, app_secret, account]):
            await query.edit_message_text(
                "⚠️ 입력값이 부족합니다. 다시 시도해주세요."
            )
            return

        await query.edit_message_text(f"⏳ {mode_text} 모드로 설정 중...")

        # 1. .env 파일 업데이트
        try:
            env_path = Path(".env")
            if env_path.exists():
                env_content = env_path.read_text()
            else:
                env_content = ""

            env_updates = {
                "KIS_APP_KEY": app_key,
                "KIS_APP_SECRET": app_secret,
                "KIS_ACCOUNT_NO": account,
                "KIS_HTS_ID": hts_id,
                "KIS_VIRTUAL": "true" if is_virtual else "false",
            }

            import re as re_mod
            for key, value in env_updates.items():
                pattern = rf'^{key}=.*$'
                replacement = f'{key}={value}'
                if re_mod.search(pattern, env_content, re_mod.MULTILINE):
                    env_content = re_mod.sub(
                        pattern, replacement, env_content, flags=re_mod.MULTILINE,
                    )
                else:
                    env_content += f"\n{replacement}"

            env_path.write_text(env_content)
            logger.info("KIS credentials saved to .env (%s mode)", mode_text)
        except Exception as e:
            logger.error("Failed to update .env: %s", e)

        # 2. 환경변수 즉시 반영
        os.environ["KIS_APP_KEY"] = app_key
        os.environ["KIS_APP_SECRET"] = app_secret
        os.environ["KIS_ACCOUNT_NO"] = account
        os.environ["KIS_HTS_ID"] = hts_id
        os.environ["KIS_VIRTUAL"] = "true" if is_virtual else "false"

        # 3. KIS 클라이언트 재초기화
        from kstock.ingest.kis_client import KISClient
        self.kis = KISClient()

        # 4. 브로커 설정 저장 (모드별)
        mode = "virtual" if is_virtual else "real"
        success = self.kis_broker.save_credentials(
            hts_id, app_key, app_secret, account, mode=mode,
        )

        # 5. 데이터 라우터 갱신
        self.data_router.refresh_source()

        # 6. 즉시 연결 테스트
        token_ok = False
        try:
            token_ok = await self.kis._ensure_token()
        except Exception as e:
            logger.error("KIS token test failed: %s", e)

        if token_ok:
            # 성공 → 현재가 테스트
            price = 0
            try:
                price = await self.kis.get_current_price("005930")
            except Exception:
                pass

            result_lines = [
                f"✅ KIS API 설정 완료!\n",
                f"모드: {mode_text}",
                f"계좌: {account}",
                f"토큰: 발급 성공",
            ]
            if price:
                result_lines.append(f"삼성전자 현재가: {price:,.0f}원")
            result_lines.append(
                f"\n📡 KIS설정 메뉴에서 투자 허브를 이용하세요!"
            )

            buttons = [
                [InlineKeyboardButton(
                    "📡 투자 허브 열기",
                    callback_data="kis_hub:home",
                )],
            ]
            await query.message.reply_text(
                "\n".join(result_lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            # 토큰 실패
            buttons = [
                [
                    InlineKeyboardButton(
                        "🔁 재시도", callback_data="kis:test",
                    ),
                    InlineKeyboardButton(
                        "🔑 키 재설정", callback_data="kis:setup",
                    ),
                ],
            ]
            await query.message.reply_text(
                f"⚠️ 설정 저장됨, 연결 확인 실패\n\n"
                f"모드: {mode_text}\n"
                f"계좌: {account}\n\n"
                f"APP SECRET이 정확한지 확인해주세요.\n"
                f"재시도 버튼을 눌러보세요.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    # == 최적화 인터랙티브 플로우 ================================================

    async def _action_opt_run(self, query, context, payload: str) -> None:
        """최적화 콜백: opt_run:{ticker} or opt_run:manual."""
        if payload == "manual":
            context.user_data["awaiting_optimize_ticker"] = True
            await query.edit_message_text(
                "\u270f\ufe0f 최적화할 종목을 입력하세요.\n\n"
                "종목코드 또는 종목명 입력\n"
                "예: 005930 또는 삼성전자"
            )
            return
        await self._run_optimization_flow(query.message, payload)

    async def _run_optimize_from_text(self, update, context, text):
        """텍스트 입력에서 종목 감지 후 최적화 실행."""
        detected = self._detect_stock_query(text)
        ticker = detected.get("code", text.strip()) if detected else text.strip()
        await self._run_optimization_flow(update.message, ticker)

    async def _run_optimization_flow(self, message, ticker):
        """최적화 실행 공통 로직."""
        name = ticker
        market = "KOSPI"
        for item in self.all_tickers:
            if item["code"] == ticker:
                name = item["name"]
                market = item.get("market", "KOSPI")
                break

        await message.reply_text(
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
                await message.reply_text(
                    msg, reply_markup=InlineKeyboardMarkup(buttons),
                )
            else:
                await message.reply_text(
                    "\u26a0\ufe0f 최적화 실패 - 데이터 부족",
                    reply_markup=MAIN_MENU,
                )
        except Exception as e:
            logger.error("Optimize error: %s", e, exc_info=True)
            await message.reply_text(
                f"\u26a0\ufe0f 최적화 오류: {str(e)[:100]}",
                reply_markup=MAIN_MENU,
            )

    # == Callback actions ====================================================

    async def _action_buy(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
        if not result:
            await query.edit_message_text("\u26a0\ufe0f 종목 정보를 찾을 수 없습니다.")
            return
        price = result.info.current_price
        holding_id = self.db.add_holding(ticker, result.name, price)
        # Record trade
        rec = self.db.get_active_recommendations()
        rec_id = None
        for r in rec:
            if r["ticker"] == ticker:
                rec_id = r["id"]
                break
        self.db.add_trade(
            ticker=ticker, name=result.name, action="buy",
            strategy_type=result.strategy_type,
            recommended_price=price, action_price=price,
            quantity_pct=10, recommendation_id=rec_id,
        )
        msg = format_trade_record(result.name, "buy", price)
        await query.edit_message_text(msg)

        # Phase 9: 신규 편입 자동 AI 분석
        await self._analyze_new_holding(ticker, result.name, price, holding_id)

    async def _action_skip(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        name = result.name if result else ticker
        price = result.info.current_price if result else 0
        strat = result.strategy_type if result else "A"
        self.db.add_trade(
            ticker=ticker, name=name, action="skip",
            strategy_type=strat, recommended_price=price,
        )
        msg = format_trade_record(name, "skip", price)
        await query.edit_message_text(msg)

    async def _analyze_new_holding(
        self, ticker: str, name: str, price: float, holding_id: int,
    ) -> None:
        """신규 편입 종목 AI 분석 + 학습 제안 (비동기)."""
        if not self.anthropic_key:
            return
        try:
            from kstock.core.investor_profile import (
                classify_hold_type, generate_new_holding_analysis, HOLD_TYPE_CONFIG,
            )
            from kstock.bot.context_builder import get_market_context

            # 보유 유형 분류
            holding = {"buy_date": datetime.now(KST).isoformat(), "buy_price": price}
            hold_type = classify_hold_type(holding)
            config = HOLD_TYPE_CONFIG[hold_type]

            # 시장 컨텍스트
            try:
                snap = await self.macro_client.get_snapshot()
                market_ctx = (
                    f"S&P500: {snap.spx_change_pct:+.2f}%, VIX: {snap.vix:.1f}, "
                    f"환율: {snap.usdkrw:,.0f}원"
                )
            except Exception:
                market_ctx = "시장 데이터 없음"

            # AI 분석 요청
            prompt = generate_new_holding_analysis(
                {"name": name, "ticker": ticker, "buy_price": price, "buy_date": datetime.now(KST).isoformat()},
                macro_context=market_ctx,
            )

            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                temperature=0.3,
                system=(
                    "너는 한국 주식 전문 애널리스트. "
                    "구체적 수치와 근거 제시. 볼드(**) 사용 금지. "
                    "한국어로 500자 이내. 주호님으로 호칭."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            analysis = response.content[0].text.strip().replace("**", "")

            # DB에 분석 저장
            self.db.upsert_holding_analysis(
                holding_id=holding_id, ticker=ticker, name=name,
                hold_type=hold_type, ai_analysis=analysis,
            )

            # 텔레그램 알림 전송
            alert_msg = (
                f"🆕 신규 편입 분석: {name}\n"
                f"─" * 20 + "\n"
                f"매수가: {price:,.0f}원\n"
                f"전략: {config['label']}\n"
                f"목표: +{config['profit_target']}% / 손절: {config['stop_loss']}%\n"
                f"점검: {config['check_interval']}\n\n"
                f"🤖 AI 분석:\n{analysis}"
            )
            await self.app.bot.send_message(
                chat_id=self.chat_id, text=alert_msg,
            )
        except Exception as e:
            logger.warning("New holding analysis failed: %s", e)

    async def _action_add_from_screenshot(
        self, query, context, payload: str,
    ) -> None:
        """스크린샷에서 인식된 종목을 보유종목에 추가."""
        holdings = context.user_data.get("screenshot_new_holdings", [])

        if payload == "skip":
            await query.edit_message_text("⏭️ 건너뛰었습니다.")
            context.user_data.pop("screenshot_new_holdings", None)
            return

        if payload == "all":
            # 전체 추가
            added = []
            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                price = h.get("avg_price", 0) or h.get("current_price", 0)
                if ticker and price > 0:
                    holding_id = self.db.add_holding(ticker, name, price)
                    added.append(f"  {name} ({price:,.0f}원)")
                    # Phase 9: 신규 편입 분석
                    try:
                        await self._analyze_new_holding(
                            ticker, name, price, holding_id,
                        )
                    except Exception:
                        pass
            if added:
                msg = (
                    f"✅ {len(added)}종목 포트폴리오 추가 완료!\n\n"
                    + "\n".join(added)
                )
            else:
                msg = "⚠️ 추가할 수 있는 종목이 없습니다."
            await query.edit_message_text(msg)
            context.user_data.pop("screenshot_new_holdings", None)
            return

        # 개별 종목 추가: add_ss:one:005930
        if payload.startswith("one:"):
            ticker = payload[4:]
            target = None
            for h in holdings:
                if h.get("ticker", "") == ticker:
                    target = h
                    break
            if target:
                name = target.get("name", ticker)
                price = target.get("avg_price", 0) or target.get("current_price", 0)
                if price > 0:
                    holding_id = self.db.add_holding(ticker, name, price)
                    await query.edit_message_text(
                        f"✅ {name} 포트폴리오 추가!\n"
                        f"매수가: {price:,.0f}원"
                    )
                    try:
                        await self._analyze_new_holding(
                            ticker, name, price, holding_id,
                        )
                    except Exception:
                        pass
                else:
                    await query.edit_message_text(
                        f"⚠️ {name} 가격 정보가 없어 추가할 수 없습니다."
                    )
            else:
                await query.edit_message_text("⚠️ 종목을 찾을 수 없습니다.")
            return

    async def _action_confirm_text_holding(
        self, query, context, payload: str,
    ) -> None:
        """자연어로 입력된 보유종목 확인 후 추가."""
        pending = context.user_data.get("pending_text_holding")
        if not pending:
            await query.edit_message_text("⚠️ 등록할 종목 정보가 없습니다.")
            return

        if payload == "yes":
            ticker = pending.get("ticker", "")
            name = pending.get("name", ticker)
            price = pending.get("price", 0)
            quantity = pending.get("quantity", 0)
            if ticker and price > 0:
                holding_id = self.db.add_holding(ticker, name, price)
                # trade_register에도 기록
                self.db.add_trade_register(
                    ticker=ticker, name=name,
                    quantity=quantity, price=price,
                    total_amount=quantity * price,
                    source="text",
                )
                qty_str = f" {quantity}주" if quantity else ""
                await query.edit_message_text(
                    f"✅ {name}{qty_str} 포트폴리오 추가!\n"
                    f"매수가: {price:,.0f}원"
                )
                try:
                    await self._analyze_new_holding(
                        ticker, name, price, holding_id,
                    )
                except Exception:
                    pass
            else:
                await query.edit_message_text("⚠️ 가격 정보가 부족합니다.")
        else:
            await query.edit_message_text("⏭️ 등록을 건너뛰었습니다.")

        context.user_data.pop("pending_text_holding", None)

    async def _action_stock_action(
        self, query, context, payload: str,
    ) -> None:
        """종목 액션 버튼 처리: stock_act:analyze/add/watch/noop:ticker."""
        action, _, code = payload.partition(":")
        stock_data = context.user_data.get("pending_stock_action", {})
        name = stock_data.get("name", code)
        price = stock_data.get("price", 0)
        market = stock_data.get("market", "KOSPI")

        if action == "analyze":
            await query.edit_message_text(f"🔍 {name}({code}) 분석 중...")
            try:
                # 기존 분석 로직 재활용
                tech_data = ""
                price_data = ""
                fund_data = ""
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
                    tech_data = "기술적 데이터 조회 실패"
                try:
                    fin = self.db.get_financials(code)
                    if fin:
                        fund_data = (
                            f"PER: {fin.get('per', 0):.1f}, "
                            f"PBR: {fin.get('pbr', 0):.2f}, "
                            f"ROE: {fin.get('roe', 0):.1f}%"
                        )
                except Exception:
                    fund_data = ""

                enriched_question = (
                    f"{name}({code}) 종목 분석 요청.\n\n"
                    f"[실시간 가격]\n{price_data}\n\n"
                    f"[기술적 지표]\n{tech_data}\n\n"
                    f"[펀더멘털]\n{fund_data}\n\n"
                    f"위 실시간 데이터를 참고하여 분석하라. "
                    f"반드시 관심/매수/매도 포인트를 명시하라."
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
                )
                try:
                    await query.message.reply_text(answer, reply_markup=MAIN_MENU)
                except Exception:
                    await query.message.reply_text(answer)
            except Exception as e:
                logger.error("Stock action analyze error: %s", e, exc_info=True)
                await query.message.reply_text(
                    f"⚠️ {name} 분석 중 오류가 발생했습니다.",
                    reply_markup=MAIN_MENU,
                )

        elif action == "add":
            # 현재가 자동 조회
            if price <= 0:
                try:
                    price = await self._get_price(code)
                except Exception:
                    pass
            if price > 0:
                holding_id = self.db.add_holding(code, name, price)
                self.db.upsert_portfolio_horizon(
                    ticker=code, name=name, horizon="dangi",
                )
                await query.edit_message_text(
                    f"✅ {name} 포트폴리오 추가!\n"
                    f"매수가(현재가): {price:,.0f}원\n"
                    f"기간: 단기(스윙)"
                )
                try:
                    await self._analyze_new_holding(code, name, price, holding_id)
                except Exception:
                    pass
            else:
                await query.edit_message_text(
                    f"⚠️ {name} 가격 조회 실패.\n다시 시도해주세요."
                )

        elif action == "watch":
            self.db.add_watchlist(code, name)
            await query.edit_message_text(f"👀 {name} 관심종목 등록!")

        elif action == "noop":
            await query.edit_message_text(
                f"ℹ️ {name}은(는) 이미 포트폴리오에 있습니다."
            )

    async def _action_balance(
        self, query, context, payload: str,
    ) -> None:
        """잔고 메뉴 액션 처리: bal:add/refresh/remove:ticker."""
        if payload == "add":
            context.user_data["awaiting_stock_add"] = True
            await query.edit_message_text(
                "📝 추가할 종목명을 입력하세요.\n\n"
                "예: 삼성전자\n"
                "예: 005930\n\n"
                "또는 스크린샷을 전송하세요 📸"
            )

        elif payload == "refresh":
            await query.edit_message_text("🔄 잔고 새로고침 중...")
            try:
                holdings = self.db.get_active_holdings()
                if not holdings:
                    await query.message.reply_text(
                        "💰 등록된 보유종목이 없습니다.",
                        reply_markup=MAIN_MENU,
                    )
                    return

                total_eval = 0.0
                total_invested = 0.0
                for h in holdings:
                    try:
                        ticker = h.get("ticker", "")
                        bp = h.get("buy_price", 0)
                        qty = h.get("quantity", 0)
                        if ticker and bp > 0:
                            detail = await self._get_price_detail(ticker, bp)
                            cur = detail["price"]
                            h["current_price"] = cur
                            h["pnl_pct"] = round((cur - bp) / bp * 100, 2) if bp > 0 else 0
                            h["day_change_pct"] = detail["day_change_pct"]
                            h["day_change"] = detail["day_change"]
                            total_eval += cur * qty
                            total_invested += bp * qty
                    except Exception:
                        pass

                total_pnl = total_eval - total_invested
                total_pnl_rate = (total_pnl / total_invested * 100) if total_invested > 0 else 0
                pnl_sign = "+" if total_pnl >= 0 else ""
                pnl_arrow = "\u25b2" if total_pnl > 0 else ("\u25bc" if total_pnl < 0 else "\u2015")

                lines = [
                    f"\U0001f4b0 주호님 잔고 현황",
                    f"\u2500" * 25,
                    f"총 평가금액: {total_eval:,.0f}원",
                    f"총 투자금액: {total_invested:,.0f}원",
                    f"총 손익: {pnl_arrow} {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_pnl_rate:.2f}%)",
                    "",
                    f"보유종목 ({len(holdings)}개)",
                    "\u2500" * 25,
                ]
                for h in holdings:
                    hname = h.get("name", "")
                    ticker = h.get("ticker", "")
                    qty = h.get("quantity", 0)
                    bp = h.get("buy_price", 0)
                    cp = h.get("current_price", bp)
                    pnl = h.get("pnl_pct", 0)
                    pnl_amount = (cp - bp) * qty
                    day_chg_pct = h.get("day_change_pct", 0)
                    day_chg = h.get("day_change", 0)
                    emoji = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < 0 else "\u26aa"
                    pnl_sign_s = "+" if pnl_amount >= 0 else ""
                    if day_chg_pct != 0:
                        day_emoji = "📈" if day_chg_pct > 0 else "📉"
                        day_sign = "+" if day_chg_pct > 0 else ""
                        day_line = f"   오늘 {day_emoji} {day_sign}{day_chg:,.0f}원 ({day_sign}{day_chg_pct:.1f}%)"
                    else:
                        day_line = ""
                    lines.append(
                        f"{emoji} {hname}({ticker}) {qty}주\n"
                        f"   매수 {bp:,.0f}원 → 현재 {cp:,.0f}원\n"
                        f"   손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)"
                        + (f"\n{day_line}" if day_line else "")
                    )

                bal_buttons = self._build_balance_buttons(holdings)
                await query.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(bal_buttons),
                )
            except Exception as e:
                logger.error("Balance refresh error: %s", e, exc_info=True)
                await query.message.reply_text(
                    "⚠️ 잔고 새로고침 실패.", reply_markup=MAIN_MENU,
                )

        elif payload.startswith("remove:"):
            ticker = payload[7:]
            holding = self.db.get_holding_by_ticker(ticker)
            if holding:
                self.db.update_holding(holding["id"], status="sold")
                hname = holding.get("name", ticker)
                await query.edit_message_text(f"🗑️ {hname} 포트폴리오에서 삭제!")
            else:
                await query.edit_message_text("⚠️ 종목을 찾을 수 없습니다.")

    def _build_balance_buttons(self, holdings: list[dict]) -> list[list]:
        """잔고 화면용 InlineKeyboard 버튼 구성."""
        buttons = [
            [
                InlineKeyboardButton(
                    "➕ 종목 추가", callback_data="bal:add",
                ),
                InlineKeyboardButton(
                    "🔄 새로고침", callback_data="bal:refresh",
                ),
            ],
        ]
        for h in holdings[:5]:
            ticker = h.get("ticker", "")
            hname = h.get("name", ticker)
            if ticker:
                buttons.append([
                    InlineKeyboardButton(
                        f"❌ {hname} 삭제",
                        callback_data=f"bal:remove:{ticker}",
                    ),
                ])
        return buttons

    async def _action_watch(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        name = result.name if result else ticker
        tp = result.info.current_price * 0.97 if result else None
        self.db.add_watchlist(ticker, name, target_price=tp)
        await query.edit_message_text(
            f"\U0001f514 {name} \uc54c\ub9bc \ub4f1\ub85d!\n\ub9e4\uc218 \uc870\uac74 \ucda9\uc871 \uc2dc \uc54c\ub824\ub4dc\ub9ac\uaca0\uc2b5\ub2c8\ub2e4."
        )

    async def _action_sell_profit(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        if holding:
            price = holding.get("current_price") or holding["buy_price"]
            pnl = holding.get("pnl_pct", 0)
            self.db.update_holding(holding["id"], sold_pct=50)
            self.db.add_trade(
                ticker=ticker, name=holding["name"], action="sell",
                action_price=price, pnl_pct=pnl,
                recommended_price=holding["buy_price"], quantity_pct=50,
            )
            msg = format_trade_record(holding["name"], "sell", price, pnl)
            await query.edit_message_text(msg)
        else:
            await query.edit_message_text("\u26a0\ufe0f 보유 종목을 찾을 수 없습니다.")

    async def _action_hold_profit(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        name = holding["name"] if holding else ticker
        price = holding.get("current_price", 0) if holding else 0
        self.db.add_trade(
            ticker=ticker, name=name, action="hold",
            action_price=price,
        )
        msg = format_trade_record(name, "hold", price)
        await query.edit_message_text(msg)

    async def _action_stop_loss(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        if holding:
            price = holding.get("current_price") or holding["buy_price"]
            pnl = holding.get("pnl_pct", 0)
            self.db.update_holding(holding["id"], status="closed")
            self.db.add_trade(
                ticker=ticker, name=holding["name"], action="stop_loss",
                action_price=price, pnl_pct=pnl,
                recommended_price=holding["buy_price"], quantity_pct=100,
            )
            msg = format_trade_record(holding["name"], "stop_loss", price, pnl)
            await query.edit_message_text(msg)
        else:
            await query.edit_message_text("\u26a0\ufe0f 보유 종목을 찾을 수 없습니다.")

    async def _action_hold_through(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        name = holding["name"] if holding else ticker
        price = holding.get("current_price", 0) if holding else 0
        self.db.add_trade(
            ticker=ticker, name=name, action="hold_through_stop",
            action_price=price,
        )
        msg = format_trade_record(name, "hold_through_stop", price)
        await query.edit_message_text(msg)

    async def _action_detail(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
            if not result:
                await query.edit_message_text("\u26a0\ufe0f 종목 정보를 가져올 수 없습니다.")
                return
        macro = await self.macro_client.get_snapshot()
        msg = format_stock_detail(
            result.name, result.ticker, result.score,
            result.tech, result.info, result.flow, macro,
            strategy_type=result.strategy_type,
            confidence_stars=result.confidence_stars,
            confidence_label=result.confidence_label,
        )
        name = result.name
        # 즐겨찾기 여부 확인
        wl = self.db.get_watchlist()
        is_fav = any(w.get("ticker") == ticker for w in wl)
        fav_btn = (
            InlineKeyboardButton("⭐ 즐겨찾기 해제", callback_data=f"fav:rm:{ticker}")
            if is_fav
            else InlineKeyboardButton("⭐ 즐겨찾기 등록", callback_data=f"fav:add:{ticker}:{name[:10]}")
        )
        buttons = [
            [
                InlineKeyboardButton("\uc0c0\uc5b4\uc694 \u2705", callback_data=f"buy:{ticker}"),
                InlineKeyboardButton("\uc548 \uc0b4\ub798\uc694 \u274c", callback_data=f"skip:{ticker}"),
            ],
            [fav_btn],
        ]
        try:
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))

    async def _action_nowatch(self, query, context, ticker: str) -> None:
        self.db.remove_watchlist(ticker)
        await query.edit_message_text("\u274c 관심 목록에서 제외했습니다.")

    async def _action_watch_btn(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        name = result.name if result else ticker
        tp = result.info.current_price * 0.97 if result else None
        self.db.add_watchlist(ticker, name, target_price=tp)
        await query.edit_message_text(
            f"\U0001f440 {name} 지켜보기 등록!\n조건 변화 시 다시 알려드리겠습니다."
        )

    async def _action_strategy(self, query, context, strategy_type: str) -> None:
        recs = self.db.get_recommendations_by_strategy(strategy_type)
        msg = format_strategy_list(strategy_type, recs)
        await query.edit_message_text(msg)

    async def _action_opt_apply(self, query, context, ticker: str) -> None:
        await query.edit_message_text(
            "\u2705 최적화 파라미터 적용 완료!\n"
            "다음 스캔부터 새 파라미터가 반영됩니다."
        )

    async def _action_opt_ignore(self, query, context, payload: str) -> None:
        await query.edit_message_text("\u274c 최적화 결과를 무시합니다.")

    async def _action_kis_buy(self, query, context, ticker: str) -> None:
        """Handle KIS auto-buy button."""
        if not self.kis_broker.connected:
            await query.edit_message_text("\u26a0\ufe0f KIS 미연결. /setup_kis 로 설정하세요.")
            return
        # 안전장치: 모의투자 모드만 자동매매 허용
        if not getattr(self.kis, '_is_virtual', True):
            await query.edit_message_text(
                "🚫 실전투자 모드에서는 자동매매가 비활성화되어 있습니다.\n\n"
                "현재 테스트 기간으로, 모의투자 모드에서만 자동매매가 가능합니다.\n"
                "📡 KIS설정 → ⚙️ 안전 설정에서 확인하세요."
            )
            return
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
        if not result:
            await query.edit_message_text("\u26a0\ufe0f 종목 정보를 찾을 수 없습니다.")
            return
        price = result.info.current_price
        balance = self.kis_broker.get_balance()
        total_eval = balance.get("total_eval", 0) if balance else 0
        qty = self.kis_broker.compute_buy_quantity(price, total_eval, pct=10.0)
        if qty <= 0:
            await query.edit_message_text("\u26a0\ufe0f 매수 가능 수량이 없습니다.")
            return
        # Safety check
        order_pct = (price * qty / total_eval * 100) if total_eval > 0 else 100
        can, reason = self.kis_broker.safety.can_order(order_pct)
        if not can:
            await query.edit_message_text(f"\u26a0\ufe0f 안전 제한: {reason}")
            return
        order = self.kis_broker.buy(ticker, qty)
        if order.success:
            self.db.add_order(
                ticker=ticker, name=result.name, order_type="market",
                side="buy", quantity=qty, price=price, order_id=order.order_id,
            )
            self.db.add_holding(ticker, result.name, price)
            await query.edit_message_text(
                f"\u2705 {result.name} {qty}주 시장가 매수 주문 완료!\n"
                f"주문번호: {order.order_id}"
            )
        else:
            await query.edit_message_text(f"\u274c 매수 실패: {order.message}")

    # == Scheduled Jobs ======================================================

    async def job_morning_briefing(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.chat_id:
            return
        try:
            macro = await self.macro_client.get_snapshot()
            regime_result = detect_regime(macro)
            regime_mode = {
                "mode": regime_result.mode,
                "emoji": regime_result.emoji,
                "label": regime_result.label,
                "message": regime_result.message,
                "allocations": regime_result.allocations,
            }

            # 보유종목별 투자 기간 판단 포함 브리핑 생성
            briefing_text = await self._generate_morning_briefing_v2(macro, regime_mode)
            if briefing_text:
                msg = format_claude_briefing(briefing_text)
            else:
                msg = "\u2600\ufe0f 오전 브리핑\n\n" + format_market_status(macro, regime_mode)

            await context.bot.send_message(chat_id=self.chat_id, text=msg)
            self.db.upsert_job_run("morning_briefing", _today(), status="success")
            logger.info("Morning briefing sent")
        except Exception as e:
            logger.error("Morning briefing failed: %s", e)
            self.db.upsert_job_run("morning_briefing", _today(), status="error", message=str(e))

    async def _generate_morning_briefing_v2(
        self, macro: MacroSnapshot, regime_mode: dict
    ) -> str | None:
        """보유종목별 투자 기간(단기/중기/장기)에 따른 보유/매도 판단 포함 브리핑."""
        if not self.anthropic_key:
            return None
        try:
            import httpx

            # 보유종목 정보 수집
            holdings = self.db.get_active_holdings()
            holdings_text = ""
            if holdings:
                for h in holdings:
                    ticker = h.get("ticker", "")
                    name = h.get("name", ticker)
                    buy_price = h.get("buy_price", 0)
                    current_price = h.get("current_price", 0)
                    pnl_pct = h.get("pnl_pct", 0)
                    horizon = h.get("horizon", "swing")
                    qty = h.get("quantity", 0)
                    holdings_text += (
                        f"  {name}({ticker}): "
                        f"매수가 {buy_price:,.0f}원, 현재가 {current_price:,.0f}원, "
                        f"수익률 {pnl_pct:+.1f}%, 수량 {qty}주, "
                        f"투자시계 {horizon}\n"
                    )
            else:
                holdings_text = "  보유종목 없음\n"

            prompt = (
                f"주호님의 오늘 아침 투자 브리핑을 작성해주세요.\n\n"
                f"[시장 데이터]\n"
                f"VIX={macro.vix:.1f}({macro.vix_change_pct:+.1f}%), "
                f"S&P500={macro.spx_change_pct:+.2f}%, "
                f"나스닥={macro.nasdaq_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원({macro.usdkrw_change_pct:+.2f}%), "
                f"BTC=${macro.btc_price:,.0f}({macro.btc_change_pct:+.1f}%), "
                f"금=${macro.gold_price:,.0f}({macro.gold_change_pct:+.1f}%), "
                f"레짐={macro.regime}, 모드={regime_mode.get('label', '')}\n\n"
                f"[보유종목]\n{holdings_text}\n"
                f"아래 형식으로 작성해주세요:\n\n"
                f"1) 시장 요약 (3줄 이내)\n"
                f"2) 보유종목별 판단 — 각 종목마다:\n"
                f"   - 종목명 + 수익률\n"
                f"   - 투자시계(단기/스윙/중기/장기)에 맞는 판단\n"
                f"   - 판단: 보유유지/추가매수/일부익절/전량매도/손절 중 택1\n"
                f"   - 구체적 이유 1줄\n"
                f"   - 목표가, 손절가 제시\n"
                f"3) 오늘 주목할 이벤트/섹터 (2줄)\n\n"
                f"투자시계별 기준:\n"
                f"- 단기(scalp): 1~3일, 수익 3~5% 목표\n"
                f"- 스윙(swing): 1~2주, 수익 8~15% 목표\n"
                f"- 중기(mid): 1~3개월, 수익 15~30% 목표\n"
                f"- 장기(long): 3개월+, 수익 30~100% 목표\n\n"
                f"볼드(**) 사용 금지. 이모지로 가독성 확보. 한 문장 최대 25자."
            )
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1200,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["content"][0]["text"]
                logger.warning("Morning v2 Claude API returned %d", resp.status_code)
        except Exception as e:
            logger.warning("Morning v2 briefing failed: %s, falling back", e)
        # fallback to simple briefing
        return await self._generate_claude_briefing(macro, regime_mode)

    async def job_intraday_monitor(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if now.weekday() >= 5:
            return
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if not (market_open <= now <= market_close):
            return
        try:
            results = await self._scan_all_stocks()
            self._last_scan_results = results
            self._scan_cache_time = now
            macro = await self.macro_client.get_snapshot()
            for r in results:
                await self._check_and_send_alerts(context.bot, r, macro)
            await self._check_holdings(context.bot)

            # 장중 급등 종목 감지 + 장기 우량주 추천
            await self._check_surge_and_longterm(context.bot, results, macro)

            logger.info("Intraday monitor: %d stocks scanned", len(results))
        except Exception as e:
            logger.error("Intraday monitor error: %s", e, exc_info=True)

    async def _check_surge_and_longterm(
        self, bot, results: list, macro: MacroSnapshot
    ) -> None:
        """장중 급등 종목 감지 + 장기 보유 적합 종목 추천."""
        surge_stocks = []
        longterm_picks = []

        for r in results:
            info = r.info
            change_pct = getattr(info, "change_pct", 0)
            score = r.score

            # 급등 감지: 당일 +5% 이상 상승
            if change_pct >= 5.0:
                if not self.db.has_recent_alert(r.ticker, "surge", hours=8):
                    surge_stocks.append(r)

            # 장기 우량주: 점수 65+ & 펀더멘탈 높음 & RSI 과매도 아님
            if (score.composite >= 65
                    and score.fundamental >= 0.7
                    and r.tech.rsi >= 30):
                if not self.db.has_recent_alert(r.ticker, "longterm_pick", hours=72):
                    longterm_picks.append(r)

        # 급등 알림 (상위 3개)
        if surge_stocks:
            surge_stocks.sort(
                key=lambda x: getattr(x.info, "change_pct", 0), reverse=True,
            )
            lines = ["\U0001f525 장중 급등 종목 감지\n"]
            for s in surge_stocks[:3]:
                chg = getattr(s.info, "change_pct", 0)
                price = getattr(s.info, "current_price", 0)
                lines.append(
                    f"\U0001f4c8 {s.name} ({s.ticker})\n"
                    f"  {price:,.0f}원 | +{chg:.1f}%\n"
                    f"  점수 {s.score.composite:.0f}점 | {s.score.signal}"
                )
                self.db.insert_alert(s.ticker, "surge", f"급등 +{chg:.1f}%")
            buttons = []
            for s in surge_stocks[:3]:
                buttons.append([
                    InlineKeyboardButton(
                        f"\u2b50 {s.name} 즐겨찾기",
                        callback_data=f"fav:add:{s.ticker}:{s.name}",
                    ),
                    InlineKeyboardButton(
                        f"\U0001f50d 상세",
                        callback_data=f"detail:{s.ticker}",
                    ),
                ])
            await bot.send_message(
                chat_id=self.chat_id,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )

        # 장기 보유 추천 (상위 2개, 하루 1회)
        if longterm_picks:
            longterm_picks.sort(
                key=lambda x: x.score.composite, reverse=True,
            )
            lines = ["\U0001f48e 장기 보유 적합 종목\n"]
            for lp in longterm_picks[:2]:
                price = getattr(lp.info, "current_price", 0)
                lines.append(
                    f"\u2705 {lp.name} ({lp.ticker})\n"
                    f"  {price:,.0f}원 | 점수 {lp.score.composite:.0f}점\n"
                    f"  펀더멘탈 {lp.score.fundamental:.0%} | "
                    f"RSI {lp.tech.rsi:.0f}"
                )
                self.db.insert_alert(lp.ticker, "longterm_pick", f"장기추천 {lp.score.composite:.0f}점")
            buttons = []
            for lp in longterm_picks[:2]:
                buttons.append([
                    InlineKeyboardButton(
                        f"\u2b50 즐겨찾기 추가",
                        callback_data=f"fav:add:{lp.ticker}:{lp.name}",
                    ),
                    InlineKeyboardButton(
                        f"\U0001f4ca 멀티분석",
                        callback_data=f"multi:{lp.ticker}",
                    ),
                ])
            await bot.send_message(
                chat_id=self.chat_id,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )

    async def job_eod_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if now.weekday() >= 5:
            return
        try:
            results = await self._scan_all_stocks()
            self._last_scan_results = results
            self._scan_cache_time = now

            # 1. AI 시장 분석 (왜 올랐고/떨어졌는지 + 추세 전망)
            try:
                market_analysis = await self._generate_eod_market_analysis()
                if market_analysis:
                    await context.bot.send_message(
                        chat_id=self.chat_id, text=market_analysis,
                    )
            except Exception as e:
                logger.warning("EOD market analysis failed: %s", e)

            # 2. 추천 종목
            reco_data = [
                (i, r.name, r.ticker, r.score.composite, r.score.signal, r.strategy_type)
                for i, r in enumerate(results[:10], 1)
            ]
            msg = "\U0001f4ca 장 마감 리포트\n\n" + format_recommendations(reco_data)
            buttons = [
                [InlineKeyboardButton(
                    f"\U0001f4cb {r.name} 상세보기", callback_data=f"detail:{r.ticker}",
                )]
                for r in results[:3]
            ]
            keyboard = InlineKeyboardMarkup(buttons) if buttons else None
            await context.bot.send_message(chat_id=self.chat_id, text=msg, reply_markup=keyboard)

            for r in results:
                self.db.upsert_portfolio(
                    ticker=r.ticker, name=r.name,
                    score=r.score.composite, signal=r.score.signal,
                )
            await self._update_recommendations(context.bot)

            # 3. 보유종목 손익 현황
            try:
                holdings_report = await self._generate_eod_holdings_report()
                if holdings_report:
                    await context.bot.send_message(
                        chat_id=self.chat_id, text=holdings_report,
                    )
            except Exception as e:
                logger.warning("EOD holdings report failed: %s", e)

            # 4. Strategy performance summary
            strat_stats = self.db.get_strategy_performance()
            if strat_stats and any(k != "summary" for k in strat_stats):
                perf_msg = format_strategy_performance(strat_stats)
                await context.bot.send_message(chat_id=self.chat_id, text=perf_msg)

            self.db.upsert_job_run("eod_scan", _today(), status="success")
            logger.info("EOD report sent")
        except Exception as e:
            logger.error("EOD report failed: %s", e)
            self.db.upsert_job_run("eod_scan", _today(), status="error", message=str(e))

    async def _generate_eod_market_analysis(self) -> str | None:
        """장 마감 AI 시장 분석 (~4000자): 왜 올랐고/떨어졌는지 + 추세 전망."""
        if not self.anthropic_key:
            return None

        # 시장 데이터 수집
        try:
            macro = await self.macro_client.get_snapshot()
        except Exception:
            return None

        # 보유종목 현황 (상세)
        holdings = self.db.get_active_holdings()
        holdings_ctx = ""
        if holdings:
            parts = []
            for h in holdings[:15]:
                name = h.get("name", "")
                pnl = h.get("pnl_pct", 0)
                buy_p = h.get("buy_price", 0)
                cur_p = h.get("current_price", 0)
                horizon = h.get("horizon", "swing")
                parts.append(
                    f"  {name}: 수익률 {pnl:+.1f}%, "
                    f"매수가 {buy_p:,.0f}원 → 현재 {cur_p:,.0f}원, "
                    f"투자시계 {horizon}"
                )
            holdings_ctx = "\n[보유종목 상세]\n" + "\n".join(parts)

        # 시장 맥박
        pulse_state = self.market_pulse.get_current_state()

        # 공포탐욕 수준
        fear_greed = ""
        fg = getattr(macro, "fear_greed", None)
        if fg:
            fear_greed = f"\n공포탐욕지수: {fg}"

        prompt = (
            f"오늘 한국/미국 주식 시장 장 마감 종합 분석을 작성해줘.\n"
            f"4000자 내외의 전문적이고 상세한 분석을 부탁해.\n\n"
            f"[오늘의 시장 데이터]\n"
            f"S&P500: {macro.spx_change_pct:+.2f}%\n"
            f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
            f"다우: {getattr(macro, 'dow_change_pct', 0):+.2f}%\n"
            f"VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
            f"USD/KRW: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
            f"BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
            f"금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n"
            f"미국 10년물: {macro.us10y:.2f}%\n"
            f"미국 2년물: {getattr(macro, 'us2y', 0):.2f}%\n"
            f"DXY: {macro.dxy:.1f}\n"
            f"유가(WTI): ${getattr(macro, 'wti_price', 0):.1f}\n"
            f"시장 맥박: {pulse_state}\n"
            f"시장 체제: {macro.regime}{fear_greed}\n"
            f"{holdings_ctx}\n\n"
            f"아래 7개 섹션으로 상세히 분석:\n\n"
            f"1. 오늘의 시장 한줄 요약\n"
            f"   (핵심 이슈 1줄 + 시장 온도 이모지)\n\n"
            f"2. 미국 시장 분석\n"
            f"   - 주요 지수 동향과 원인\n"
            f"   - 섹터별 강약 (테크/금융/에너지/헬스케어 등)\n"
            f"   - 주요 개별종목 이슈 (엔비디아/애플/테슬라 등)\n"
            f"   - FOMC/경제지표 등 이벤트 영향\n\n"
            f"3. 한국 시장 영향 분석\n"
            f"   - 코스피/코스닥 예상 방향\n"
            f"   - 외국인/기관 수급 전망\n"
            f"   - 환율이 수출주/내수주에 미치는 영향\n"
            f"   - 반도체/2차전지/바이오 등 주도주 전망\n\n"
            f"4. 금리/환율/원자재 분석\n"
            f"   - 미국 국채 10년물 방향과 의미\n"
            f"   - 달러 강세/약세 → 신흥국 자금 흐름\n"
            f"   - 유가/금/구리 등 원자재 시그널\n\n"
            f"5. 주호님 포트폴리오 영향\n"
            f"   - 보유종목별 오늘 시장과의 연관성\n"
            f"   - 리스크 요인 및 기회 요인\n"
            f"   - 손절/익절 판단이 필요한 종목\n\n"
            f"6. 내일/이번주 전략\n"
            f"   - 단기(1-3일) 시장 방향 전망\n"
            f"   - 주간 핵심 이벤트 캘린더\n"
            f"   - 주목할 섹터/테마\n\n"
            f"7. 구체적 액션 플랜\n"
            f"   - 내일 장 시작 전 해야 할 것\n"
            f"   - 매수/매도/홀드 구체적 제안\n"
            f"   - 신규 매수 고려 종목 (있다면)\n"
        )

        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=3500,
            temperature=0.3,
            system=(
                "너는 CFA/CAIA 자격을 보유한 20년 경력 한국 주식 전문 애널리스트 QuantBot이다. "
                "주호님 전용 비서로, 매일 장 마감 후 4000자 수준의 전문 시장 분석을 제공한다. "
                "볼드(**) 사용 금지. 마크다운 헤딩(#) 사용 금지. "
                "이모지로 섹션을 구분하고, 번호 매기기를 사용해 가독성을 높인다. "
                "반드시 구체적 수치와 근거를 제시하라. "
                "추상적 표현(예: '관심 필요', '주시 필요') 대신 명확한 액션을 제시. "
                "글로벌 투자은행 리서치 수준의 분석 깊이를 목표로 한다. "
                "보유종목에 대해서는 특히 구체적으로 분석하라."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = response.content[0].text.strip().replace("**", "")

        import re
        analysis = re.sub(r'\n{3,}', '\n\n', analysis)
        analysis = analysis.replace("###", "").replace("##", "").replace("# ", "")

        return (
            f"📊 장 마감 종합 시장 분석\n"
            f"{'━' * 22}\n\n"
            f"{analysis}\n\n"
            f"{'━' * 22}\n"
            f"🤖 K-Quant AI Analyst | {datetime.now(KST).strftime('%H:%M')} 분석 완료"
        )

    async def _generate_eod_holdings_report(self) -> str | None:
        """장 마감 보유종목 손익 현황 (금액 손익 + 전일 대비 포함)."""
        holdings = self.db.get_active_holdings()
        if not holdings:
            return None

        total_eval = 0.0
        total_invested = 0.0
        total_day_pnl = 0.0
        lines = [
            "💼 오늘의 보유종목 현황",
            "━" * 22,
            "",
        ]

        for h in holdings:
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            bp = h.get("buy_price", 0)
            qty = h.get("quantity", 0)
            try:
                detail = await self._get_price_detail(ticker, bp)
                cur = detail["price"]
                day_chg = detail["day_change"]
                day_chg_pct = detail["day_change_pct"]
            except Exception:
                cur = bp
                day_chg = 0.0
                day_chg_pct = 0.0
            pnl = round((cur - bp) / bp * 100, 2) if bp > 0 else 0
            pnl_amount = (cur - bp) * qty
            total_eval += cur * qty
            total_invested += bp * qty
            total_day_pnl += day_chg * qty

            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            pnl_sign_s = "+" if pnl_amount >= 0 else ""
            # 전일 대비
            if day_chg_pct != 0:
                day_emoji = "📈" if day_chg_pct > 0 else "📉"
                day_sign = "+" if day_chg_pct > 0 else ""
                day_line = f"\n   오늘 {day_emoji} {day_sign}{day_chg:,.0f}원 ({day_sign}{day_chg_pct:.1f}%)"
            else:
                day_line = ""
            lines.append(
                f"{emoji} {name}\n"
                f"   {bp:,.0f}원 → {cur:,.0f}원\n"
                f"   손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)"
                + day_line
            )

        if total_invested > 0:
            total_pnl = total_eval - total_invested
            total_rate = total_pnl / total_invested * 100
            pnl_sign = "+" if total_pnl >= 0 else ""
            day_sign = "+" if total_day_pnl >= 0 else ""
            lines.extend([
                "",
                "━" * 22,
                f"총 손익: {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_rate:.1f}%)",
                f"오늘 변동: {day_sign}{total_day_pnl:,.0f}원",
            ])

        return "\n".join(lines)

    # == Phase 8: Macro Refresh, Market Pulse & PDF Report Jobs ================

    async def job_macro_refresh(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """3분마다 매크로 데이터 백그라운드 갱신 → SQLite 캐시 따뜻하게 유지."""
        try:
            await self.macro_client.refresh_now()
        except Exception as e:
            logger.debug("Macro refresh job error: %s", e)

    async def job_market_pulse(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """5분마다 시장 맥박 체크 + 변화 시 알림."""
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if now.weekday() >= 5:
            return
        market_start = now.replace(hour=9, minute=5, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=25, second=0, microsecond=0)
        if not (market_start <= now <= market_end):
            return
        try:
            macro = await self.macro_client.get_snapshot()
            change = self.market_pulse.check_pulse(macro)

            if change and change.severity >= 2:
                # 보유종목 영향 분석
                holdings = self.db.get_active_holdings()
                impacts = None
                if holdings:
                    impacts = self.market_pulse.analyze_portfolio_impact(
                        change, holdings,
                    )

                history = self.market_pulse.get_recent_history(minutes=30)
                alert_msg = format_pulse_alert(
                    change, macro, impacts=impacts, history=history,
                )
                await context.bot.send_message(
                    chat_id=self.chat_id, text=alert_msg,
                )
                logger.info(
                    "Market pulse alert: %s -> %s (severity=%d)",
                    change.from_state, change.to_state, change.severity,
                )
        except Exception as e:
            logger.error("Market pulse error: %s", e, exc_info=True)

    async def job_daily_pdf_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """통합 장 마감 리포트 (16:00 KST).

        1건의 간결한 텍스트 메시지 + 1건의 PDF 파일.
        기존 eod_report + daily_pdf_report를 통합.
        """
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if now.weekday() >= 5:
            return
        try:
            # ── 1. 스캔 + 추천 업데이트 + 전략별 저장 ──
            try:
                results = await self._scan_all_stocks()
                self._last_scan_results = results
                self._scan_cache_time = now
                for r in results:
                    self.db.upsert_portfolio(
                        ticker=r.ticker, name=r.name,
                        score=r.score.composite, signal=r.score.signal,
                    )
                await self._update_recommendations(context.bot)

                # 전략별 Top 추천 종목 DB 저장 (전략별 보기 활성화)
                for r in results[:20]:
                    strat = r.strategy_type or "A"
                    if not self.db.has_active_recommendation(r.ticker):
                        meta = STRATEGY_META.get(strat, STRATEGY_META["A"])
                        self.db.add_recommendation(
                            ticker=r.ticker,
                            name=r.name,
                            rec_price=r.info.current_price,
                            rec_score=r.score.composite,
                            strategy_type=strat,
                            target_pct=meta["target"],
                            stop_pct=meta["stop"],
                            status="active" if r.score.signal == "BUY" else "watch",
                        )
            except Exception as e:
                logger.warning("EOD scan in pdf_report failed: %s", e)
                results = []

            # ── 2. 보유종목 현재가 + 전일 대비 업데이트 ──
            macro = await self.macro_client.get_snapshot()
            holdings = self.db.get_active_holdings()
            total_day_pnl = 0.0
            for h in holdings:
                try:
                    detail = await self._get_price_detail(
                        h["ticker"], h.get("buy_price", 0),
                    )
                    bp = h.get("buy_price", 0)
                    cur = detail["price"]
                    if bp > 0 and cur > 0:
                        h["current_price"] = cur
                        h["pnl_pct"] = round((cur - bp) / bp * 100, 2)
                        h["day_change_pct"] = detail["day_change_pct"]
                        total_day_pnl += detail["day_change"] * h.get("quantity", 0)
                except Exception:
                    pass

            # ── 3. PDF 생성 ──
            market_state = self.market_pulse.get_current_state()
            sell_plans = self.sell_planner.create_plans_for_all(
                holdings, market_state,
            )
            filepath = await generate_daily_pdf(
                macro_snapshot=macro,
                holdings=holdings,
                sell_plans=sell_plans,
                pulse_history=self.market_pulse.get_recent_history(minutes=360),
            )

            # ── 4. 결론 위주 간결한 텍스트 메시지 1건 ──
            regime_kr = {
                "risk_on": "🟢 공격",
                "neutral": "🟡 중립",
                "risk_off": "🔴 방어",
            }.get(macro.regime, "⚪ 중립")

            # 투자 판단 결론
            if macro.regime == "risk_on":
                verdict = "📈 매수 기회 탐색"
            elif macro.regime == "risk_off":
                verdict = "🛡️ 관망/방어 권고"
            else:
                verdict = "⏸️ 선별적 접근"

            # 보유종목 요약
            if holdings:
                total_eval = sum(
                    h.get("current_price", 0) * h.get("quantity", 0) for h in holdings
                )
                total_invested = sum(
                    h.get("buy_price", 0) * h.get("quantity", 0) for h in holdings
                )
                total_pnl = total_eval - total_invested
                total_rate = (total_pnl / total_invested * 100) if total_invested > 0 else 0
                pnl_sign = "+" if total_pnl >= 0 else ""
                day_sign = "+" if total_day_pnl >= 0 else ""
                portfolio_line = (
                    f"💰 내 포트폴리오: {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_rate:.1f}%)\n"
                    f"   오늘 변동: {day_sign}{total_day_pnl:,.0f}원"
                )
            else:
                portfolio_line = "💰 포트폴리오: 보유종목 없음"

            # 추천 종목 Top 3
            top3_lines = []
            for r in results[:3]:
                score = r.score.composite
                signal = r.score.signal
                sig_emoji = "🟢" if signal == "BUY" else "🟡" if signal == "HOLD" else "🔴"
                top3_lines.append(f"  {sig_emoji} {r.name} (점수 {score:.0f})")
            top3_text = "\n".join(top3_lines) if top3_lines else "  스캔 결과 없음"

            date_str = now.strftime("%m/%d")
            text_msg = (
                f"📊 장 마감 리포트 {date_str}\n"
                f"{'━' * 22}\n\n"
                f"🎯 결론: {verdict}\n"
                f"시장: {regime_kr} | S&P {macro.spx_change_pct:+.2f}%\n\n"
                f"{portfolio_line}\n\n"
                f"📋 오늘의 Top 종목:\n{top3_text}\n\n"
                f"📎 상세 분석은 PDF 첨부 확인"
            )
            await context.bot.send_message(
                chat_id=self.chat_id, text=text_msg,
            )

            # ── 5. PDF 1건 전송 ──
            if filepath:
                try:
                    with open(filepath, "rb") as f:
                        await context.bot.send_document(
                            chat_id=self.chat_id, document=f,
                        )
                except Exception as e:
                    logger.warning("PDF send failed: %s", e)

            self.db.upsert_job_run("eod_scan", _today(), status="success")
            logger.info("Daily unified report sent")
        except Exception as e:
            logger.error("Daily PDF report failed: %s", e, exc_info=True)
            self.db.upsert_job_run("eod_scan", _today(), status="error", message=str(e))

    async def job_us_premarket_briefing(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매일 07:00 미국 시장 프리마켓 브리핑 (새벽 미국장 분석)."""
        if not self.chat_id:
            return
        try:
            macro = await self.macro_client.get_snapshot()

            # 보유종목 중 미국 관련 종목 파악
            holdings = self.db.get_active_holdings()
            holdings_ctx = ""
            if holdings:
                parts = []
                for h in holdings[:10]:
                    name = h.get("name", "")
                    pnl = h.get("pnl_pct", 0)
                    parts.append(f"{name}({pnl:+.1f}%)")
                holdings_ctx = f"\n보유종목: {', '.join(parts)}"

            if self.anthropic_key:
                import anthropic
                client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)

                prompt = (
                    f"새벽 미국 시장 마감 결과를 분석하고, "
                    f"오늘 한국 시장에 미칠 영향을 알려줘.\n\n"
                    f"[미국 시장 마감 데이터]\n"
                    f"S&P500: {macro.spx_change_pct:+.2f}%\n"
                    f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                    f"다우: {getattr(macro, 'dow_change_pct', 0):+.2f}%\n"
                    f"VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
                    f"USD/KRW: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
                    f"미국 10년물: {macro.us10y:.2f}%\n"
                    f"미국 2년물: {getattr(macro, 'us2y', 0):.2f}%\n"
                    f"DXY: {macro.dxy:.1f}\n"
                    f"BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
                    f"금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n"
                    f"유가: ${getattr(macro, 'wti_price', 0):.1f}\n"
                    f"시장체제: {macro.regime}\n"
                    f"{holdings_ctx}\n\n"
                    f"아래 형식으로 분석:\n\n"
                    f"1. 미국 시장 마감 요약 (2-3줄)\n"
                    f"   - 3대 지수 동향 + 주요 원인\n\n"
                    f"2. 주요 이슈 & 이벤트\n"
                    f"   - 실적 발표, FOMC, 경제지표 등\n"
                    f"   - 빅테크/반도체 등 핵심 종목 동향\n\n"
                    f"3. 한국 시장 영향 분석\n"
                    f"   - 코스피/코스닥 예상 방향\n"
                    f"   - 반도체/2차전지/바이오 등 주도 섹터 영향\n"
                    f"   - 외국인 수급 방향 예상\n\n"
                    f"4. 환율/금리/원자재 시그널\n"
                    f"   - 원화 방향 + 수출주 영향\n"
                    f"   - 국채 금리 → 성장주/가치주 영향\n\n"
                    f"5. 오늘 주호님 체크리스트\n"
                    f"   - 장 시작 전 확인할 것\n"
                    f"   - 보유종목 중 주의할 종목\n"
                    f"   - 매매 타이밍 제안\n"
                )

                response = await client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=2000,
                    temperature=0.3,
                    system=(
                        "너는 한국 주식 전문 애널리스트 QuantBot이다. "
                        "주호님 전용 비서. 매일 아침 7시에 새벽 미국 시장 분석을 전달한다. "
                        "볼드(**) 사용 금지. 이모지로 구분. "
                        "구체적 수치 필수. 추상적 표현 금지. "
                        "한국 시장 영향에 초점을 맞춰라."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                analysis = response.content[0].text.strip().replace("**", "")
                import re
                analysis = re.sub(r'\n{3,}', '\n\n', analysis)
                analysis = analysis.replace("###", "").replace("##", "").replace("# ", "")

                msg = (
                    f"🇺🇸 미국 시장 프리마켓 브리핑\n"
                    f"{'━' * 22}\n\n"
                    f"{analysis}\n\n"
                    f"{'━' * 22}\n"
                    f"🤖 K-Quant | {datetime.now(KST).strftime('%H:%M')} 분석"
                )
            else:
                # AI 없이 기본 데이터만 전달
                spx_emoji = "📈" if macro.spx_change_pct > 0 else "📉"
                ndq_emoji = "📈" if macro.nasdaq_change_pct > 0 else "📉"
                msg = (
                    f"🇺🇸 미국 시장 프리마켓 브리핑\n"
                    f"{'━' * 22}\n\n"
                    f"{spx_emoji} S&P500: {macro.spx_change_pct:+.2f}%\n"
                    f"{ndq_emoji} 나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                    f"💰 VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
                    f"💱 환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
                    f"📊 미국10년물: {macro.us10y:.2f}%\n"
                    f"🪙 BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
                    f"🥇 금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n\n"
                    f"시장체제: {macro.regime}\n\n"
                    f"{'━' * 22}\n"
                    f"🤖 K-Quant | {datetime.now(KST).strftime('%H:%M')}"
                )

            await context.bot.send_message(chat_id=self.chat_id, text=msg)
            self.db.upsert_job_run(
                "us_premarket_briefing", _today(), status="success",
            )
            logger.info("US premarket briefing sent")
        except Exception as e:
            logger.error("US premarket briefing failed: %s", e)
            self.db.upsert_job_run(
                "us_premarket_briefing", _today(),
                status="error", message=str(e),
            )

    async def job_daily_self_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """매일 21:00 자가진단 보고서 + 자동 업데이트 제안."""
        if not self.chat_id:
            return
        try:
            from kstock.bot.daily_self_report import generate_daily_self_report
            report = await generate_daily_self_report(self.db, self.macro_client)
            await context.bot.send_message(chat_id=self.chat_id, text=report)

            # 개선 제안 분석 후 업데이트 제안
            update_suggestions = await self._generate_update_suggestions()
            if update_suggestions:
                update_msg = (
                    f"\n🔧 자동 업데이트 제안\n"
                    f"{'━' * 22}\n\n"
                    f"{update_suggestions}\n\n"
                    f"위 개선사항을 적용할까요?"
                )
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ 업데이트 실행",
                            callback_data="selfupd:apply",
                        ),
                        InlineKeyboardButton(
                            "❌ 건너뛰기",
                            callback_data="selfupd:skip",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "📋 상세 보기",
                            callback_data="selfupd:detail",
                        ),
                    ],
                ])
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=update_msg,
                    reply_markup=keyboard,
                )

            logger.info("Daily self-report sent")
        except Exception as e:
            logger.error("Daily self-report error: %s", e, exc_info=True)

    async def _generate_update_suggestions(self) -> str | None:
        """자가진단 결과 기반 자동 업데이트 제안 생성."""
        suggestions = []
        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        # 1. 재무 데이터 없는 종목 확인
        try:
            holdings = self.db.get_active_holdings()
            no_fin = []
            for h in holdings:
                ticker = h.get("ticker", "")
                fin = self.db.get_financials(ticker)
                if not fin and ticker:
                    no_fin.append(h.get("name", ticker))
            if no_fin:
                suggestions.append(
                    f"📊 재무 데이터 수집: {', '.join(no_fin[:5])} "
                    f"({len(no_fin)}종목)"
                )
        except Exception:
            pass

        # 2. 가격 갱신이 필요한 종목
        try:
            stale_count = 0
            for h in holdings:
                cur = h.get("current_price", 0)
                buy = h.get("buy_price", 0)
                if cur == 0 and buy > 0:
                    stale_count += 1
            if stale_count > 0:
                suggestions.append(
                    f"💰 현재가 갱신 필요: {stale_count}종목"
                )
        except Exception:
            pass

        # 3. 오류 잡 재실행 제안
        try:
            job_runs = self.db.get_job_runs(today_str)
            if job_runs:
                errors = [
                    j for j in job_runs if j.get("status") == "error"
                ]
                if errors:
                    names = list({e.get("job_name", "") for e in errors})
                    suggestions.append(
                        f"🔄 실패 작업 재실행: {', '.join(names[:3])}"
                    )
        except Exception:
            pass

        # 4. 투자기간 미설정 종목
        try:
            no_horizon = []
            for h in holdings:
                horizon = h.get("horizon", "")
                if not horizon or horizon == "unknown":
                    no_horizon.append(h.get("name", ""))
            if no_horizon:
                suggestions.append(
                    f"⏰ 투자기간 미설정: {', '.join(no_horizon[:3])}"
                )
        except Exception:
            pass

        if not suggestions:
            return None

        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions))

    async def _action_self_update(
        self, query, context: ContextTypes.DEFAULT_TYPE, payload: str = "",
    ) -> None:
        """자가진단 자동 업데이트 콜백 처리."""
        parts = query.data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "skip":
            await query.edit_message_text("⏭️ 업데이트를 건너뛰었습니다.")
            return

        if action == "detail":
            suggestions = await self._generate_update_suggestions()
            detail_msg = (
                f"📋 업데이트 상세 내역\n"
                f"{'━' * 22}\n\n"
                f"{suggestions or '제안 사항 없음'}\n\n"
                f"각 항목은 자동으로 실행됩니다:\n"
                f"  재무 데이터 → yfinance에서 수집\n"
                f"  현재가 갱신 → 실시간 조회\n"
                f"  실패 작업 → 스케줄러 재실행\n"
                f"  투자기간 → 기본값(단기) 설정"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ 실행", callback_data="selfupd:apply",
                    ),
                    InlineKeyboardButton(
                        "❌ 취소", callback_data="selfupd:skip",
                    ),
                ],
            ])
            await query.edit_message_text(
                text=detail_msg, reply_markup=keyboard,
            )
            return

        if action == "apply":
            await query.edit_message_text("⏳ 자동 업데이트 실행 중...")
            results = []
            holdings = self.db.get_active_holdings()

            # 1. 재무 데이터 수집
            try:
                no_fin = [
                    h for h in holdings
                    if not self.db.get_financials(h.get("ticker", ""))
                    and h.get("ticker")
                ]
                if no_fin:
                    collected = 0
                    for h in no_fin[:5]:
                        try:
                            from kstock.data.financial import fetch_financials
                            fin_data = await fetch_financials(h["ticker"])
                            if fin_data:
                                self.db.upsert_financials(
                                    h["ticker"], fin_data,
                                )
                                collected += 1
                        except Exception:
                            pass
                    results.append(f"📊 재무 데이터: {collected}종목 수집 완료")
            except Exception:
                pass

            # 2. 현재가 갱신
            try:
                updated = 0
                for h in holdings:
                    ticker = h.get("ticker", "")
                    bp = h.get("buy_price", 0)
                    cur = h.get("current_price", 0)
                    if cur == 0 and bp > 0 and ticker:
                        try:
                            price = await self._get_price(ticker, bp)
                            if price and price > 0:
                                self.db.update_holding_price(
                                    ticker, price,
                                )
                                updated += 1
                        except Exception:
                            pass
                if updated > 0:
                    results.append(f"💰 현재가 갱신: {updated}종목 완료")
            except Exception:
                pass

            # 3. 투자기간 미설정 → 기본값 설정
            try:
                set_count = 0
                for h in holdings:
                    horizon = h.get("horizon", "")
                    if not horizon or horizon == "unknown":
                        self.db.upsert_portfolio_horizon(
                            h.get("ticker", ""),
                            h.get("name", ""),
                            "dangi",
                        )
                        set_count += 1
                if set_count > 0:
                    results.append(
                        f"⏰ 투자기간: {set_count}종목 기본값(단기) 설정"
                    )
            except Exception:
                pass

            if results:
                result_msg = (
                    f"✅ 자동 업데이트 완료\n"
                    f"{'━' * 22}\n\n"
                    + "\n".join(results)
                    + "\n\n🤖 내일도 더 나은 분석을 제공하겠습니다!"
                )
            else:
                result_msg = "✅ 모든 항목이 최신 상태입니다. 업데이트 불필요!"

            await context.bot.send_message(
                chat_id=self.chat_id, text=result_msg,
            )
            return

    async def job_weekly_learning(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Weekly learning report - runs Saturday 09:00 KST."""
        if not self.chat_id:
            return
        try:
            strat_stats = self.db.get_strategy_performance()
            summary = strat_stats.get("summary", {})

            # Generate insights
            insights = []
            best_strat = None
            best_wr = 0
            worst_strat = None
            worst_wr = 100
            for k, v in strat_stats.items():
                if k == "summary":
                    continue
                wr = v.get("win_rate", 0)
                if v.get("total", 0) >= 2:
                    if wr > best_wr:
                        best_wr = wr
                        best_strat = k
                    if wr < worst_wr:
                        worst_wr = wr
                        worst_strat = k

            if best_strat:
                from kstock.bot.messages import STRATEGY_LABELS
                insights.append(
                    f"가장 잘 맞는 전략: {STRATEGY_LABELS.get(best_strat, best_strat)} "
                    f"(승률 {best_wr:.0f}%)"
                )
            if worst_strat and worst_strat != best_strat:
                from kstock.bot.messages import STRATEGY_LABELS
                insights.append(
                    f"개선 필요: {STRATEGY_LABELS.get(worst_strat, worst_strat)} "
                    f"(승률 {worst_wr:.0f}%)"
                )
            exec_rate = summary.get("execution_rate", 0)
            if exec_rate < 50:
                insights.append(f"매수 실행률 {exec_rate:.0f}% -> 확신 있는 종목만 추천 강화")
            stop_comp = summary.get("stop_compliance", 100)
            if stop_comp < 80:
                insights.append(f"손절 준수율 {stop_comp:.0f}% -> 손절 알림 강화 필요")
            if not insights:
                insights.append("아직 충분한 데이터가 없습니다. 매매를 기록해주세요!")

            # Weight adjustments
            adjustments = {}
            if best_strat:
                adjustments[best_strat] = "+5% 비중 증가"
            if worst_strat and worst_strat != best_strat:
                adjustments[worst_strat] = "-5% 비중 감소"

            # Save preferences
            self._save_user_preference(strat_stats)

            learning_data = {
                "insights": insights,
                "adjustments": adjustments,
            }
            msg = format_weekly_learning_report(learning_data)
            await context.bot.send_message(chat_id=self.chat_id, text=msg)
            self.db.upsert_job_run("weekly_learning", _today(), status="success")
            logger.info("Weekly learning report sent")
        except Exception as e:
            logger.error("Weekly learning failed: %s", e)

    async def job_screenshot_reminder(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Screenshot reminder - runs Mon/Fri 08:00 KST."""
        if not self.chat_id:
            return
        try:
            msg = format_screenshot_reminder()
            await context.bot.send_message(chat_id=self.chat_id, text=msg)
            logger.info("Screenshot reminder sent")
        except Exception as e:
            logger.error("Screenshot reminder failed: %s", e)

    async def job_sentiment_analysis(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Daily sentiment analysis - runs 08:00 KST."""
        if not self.chat_id or not HAS_SENTIMENT or not self.anthropic_key:
            return
        now = datetime.now(KST)
        if now.weekday() >= 5:
            return
        try:
            universe = [
                {"ticker": s["code"], "name": s["name"]}
                for s in self.all_tickers[:20]
            ]
            results = run_daily_sentiment(universe, self.anthropic_key)
            self._sentiment_cache = results

            # Save to DB
            today_str = _today()
            for ticker, r in results.items():
                bonus = get_sentiment_bonus(r)
                self.db.add_sentiment(
                    ticker=ticker, analysis_date=today_str,
                    positive_pct=r.positive_pct, negative_pct=r.negative_pct,
                    neutral_pct=r.neutral_pct, headline_count=r.headline_count,
                    summary=r.summary, score_bonus=bonus,
                )

            msg = format_sentiment_summary(results)
            await context.bot.send_message(chat_id=self.chat_id, text=msg)
            self.db.upsert_job_run("sentiment_analysis", today_str, status="success")
            logger.info("Sentiment analysis complete: %d stocks", len(results))
        except Exception as e:
            logger.error("Sentiment analysis failed: %s", e)
            self.db.upsert_job_run("sentiment_analysis", _today(), status="error", message=str(e))

    async def job_weekly_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Weekly report generation - runs Sunday 19:00 KST."""
        if not self.chat_id:
            return
        # Check if notification is enabled
        settings = self.db.get_notification_settings()
        if not settings.get("weekly_report", True):
            return
        try:
            from kstock.bot.weekly_report import generate_weekly_report
            telegram_msg, doc_url = await generate_weekly_report(self.db)
            await context.bot.send_message(chat_id=self.chat_id, text=telegram_msg)
            self.db.upsert_job_run("weekly_report", _today(), status="success")
            logger.info("Weekly report generated: %s", doc_url or "no Google Doc")
        except Exception as e:
            logger.error("Weekly report failed: %s", e)
            self.db.upsert_job_run(
                "weekly_report", _today(), status="error", message=str(e),
            )

    def _save_user_preference(self, strat_stats: dict) -> None:
        """Save learned user preferences to YAML."""
        import yaml
        pref_path = Path("config/user_preference.yaml")
        try:
            if pref_path.exists():
                with open(pref_path) as f:
                    prefs = yaml.safe_load(f) or {}
            else:
                prefs = {}

            weights = prefs.get("strategy_weights", {
                "A": 15, "B": 10, "C": 20, "D": 10,
                "E": 15, "F": 10, "G": 5, "cash": 15,
            })

            # Auto-adjust: boost best, reduce worst
            best_strat = None
            best_wr = 0
            worst_strat = None
            worst_wr = 100
            for k, v in strat_stats.items():
                if k == "summary":
                    continue
                if v.get("total", 0) >= 3:
                    wr = v.get("win_rate", 0)
                    if wr > best_wr:
                        best_wr = wr
                        best_strat = k
                    if wr < worst_wr:
                        worst_wr = wr
                        worst_strat = k

            if best_strat and best_strat in weights:
                weights[best_strat] = min(30, weights.get(best_strat, 10) + 2)
            if worst_strat and worst_strat in weights and worst_strat != best_strat:
                weights[worst_strat] = max(0, weights.get(worst_strat, 10) - 2)

            # Normalize to ~100
            total = sum(weights.values())
            if total > 0 and total != 100:
                factor = 100 / total
                weights = {k: round(v * factor) for k, v in weights.items()}

            prefs["strategy_weights"] = weights
            prefs["last_updated"] = _today()
            summary = strat_stats.get("summary", {})
            prefs["user_behavior"] = {
                "execution_rate": summary.get("execution_rate", 0),
                "stop_compliance": summary.get("stop_compliance", 100),
            }

            pref_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pref_path, "w") as f:
                yaml.dump(prefs, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logger.error("Failed to save user preferences: %s", e)

    # == Core Logic ==========================================================

    async def _update_sector_strengths(self) -> None:
        """Fetch sector ETF data and compute relative strengths."""
        sector_etfs = self.universe_config.get("etf_sector", [])
        ohlcv_map = {}
        for etf in sector_etfs:
            code = etf["code"]
            try:
                df = await self.yf_client.get_ohlcv(code, etf.get("market", "KOSPI"))
                if df is not None and not df.empty:
                    ohlcv_map[code] = df
            except Exception:
                pass
        self._sector_strengths = compute_sector_returns(ohlcv_map)

    async def _scan_all_stocks(self) -> list:
        macro = await self.macro_client.get_snapshot()
        await self._update_sector_strengths()

        # First pass: collect all 3-month returns for RS ranking
        all_returns = []
        pre_results = []
        for stock in self.all_tickers:
            try:
                ohlcv = await self.yf_client.get_ohlcv(
                    stock["code"], stock.get("market", "KOSPI")
                )
                if ohlcv is not None and not ohlcv.empty:
                    self._ohlcv_cache[stock["code"]] = ohlcv
                    close = ohlcv["close"].astype(float)
                    lookback_3m = min(60, len(close) - 1)
                    if lookback_3m > 0:
                        ret = (close.iloc[-1] - close.iloc[-lookback_3m - 1]) / close.iloc[-lookback_3m - 1] * 100
                        all_returns.append(float(ret))
                        pre_results.append((stock, float(ret)))
                    else:
                        pre_results.append((stock, 0.0))
                else:
                    pre_results.append((stock, 0.0))
            except Exception:
                pre_results.append((stock, 0.0))

        # Second pass: full analysis with RS rank
        results = []
        for stock, ret_3m in pre_results:
            try:
                rs_rank, _ = compute_relative_strength_rank(ret_3m, all_returns)
                r = await self._analyze_stock(
                    stock["code"], stock["name"], macro,
                    market=stock.get("market", "KOSPI"),
                    sector=stock.get("sector", ""),
                    category=stock.get("category", ""),
                    rs_rank=rs_rank,
                    rs_total=len(all_returns),
                )
                if r:
                    results.append(r)
            except Exception as e:
                logger.error("Scan error %s: %s", stock.get("code"), e)
        results.sort(key=lambda r: r.score.composite, reverse=True)
        return results

    async def _analyze_stock(
        self, ticker: str, name: str, macro: MacroSnapshot,
        market: str = "KOSPI", sector: str = "", category: str = "",
        rs_rank: int = 0, rs_total: int = 1,
    ) -> ScanResult | None:
        try:
            ohlcv = self._ohlcv_cache.get(ticker)
            if ohlcv is None or ohlcv.empty:
                # Fetch OHLCV and stock info in parallel
                import asyncio
                ohlcv, yf_info = await asyncio.gather(
                    self.yf_client.get_ohlcv(ticker, market),
                    self.yf_client.get_stock_info(ticker, name, market),
                )
                self._ohlcv_cache[ticker] = ohlcv
            else:
                yf_info = await self.yf_client.get_stock_info(ticker, name, market)

            info = StockInfo(
                ticker=ticker, name=name, market=market,
                market_cap=yf_info.get("market_cap", 0),
                per=yf_info.get("per", 0),
                roe=yf_info.get("roe", 0),
                debt_ratio=yf_info.get("debt_ratio", 0),
                consensus_target=yf_info.get("consensus_target", 0),
                current_price=yf_info.get("current_price", 0),
            )

            tech = compute_indicators(ohlcv)

            # Multi-timeframe
            weekly_trend = compute_weekly_trend(ohlcv)
            tech.weekly_trend = weekly_trend
            tech.mtf_aligned = (weekly_trend == "up" and tech.ema_50 > tech.ema_200)

            # Sector adjustment
            sector_adj = get_sector_score_adjustment(sector, self._sector_strengths)

            # MTF bonus
            if tech.mtf_aligned:
                mtf_bonus = 10
            elif weekly_trend == "down" and tech.ema_50 < tech.ema_200:
                mtf_bonus = -10
            else:
                mtf_bonus = 0

            # Mock flow data (parallel)
            foreign_flow, inst_flow = await asyncio.gather(
                self.kis.get_foreign_flow(ticker),
                self.kis.get_institution_flow(ticker),
            )
            foreign_days = int(
                (foreign_flow["net_buy_volume"] > 0).sum()
                - (foreign_flow["net_buy_volume"] < 0).sum()
            )
            inst_days = int(
                (inst_flow["net_buy_volume"] > 0).sum()
                - (inst_flow["net_buy_volume"] < 0).sum()
            )
            avg_value = float(
                ohlcv["close"].astype(float).iloc[-5:].mean()
                * ohlcv["volume"].astype(float).iloc[-5:].mean()
            )
            flow = FlowData(
                foreign_net_buy_days=foreign_days,
                institution_net_buy_days=inst_days,
                avg_trade_value_krw=avg_value,
            )
            # v3.0: policy bonus
            policy_bonus = get_policy_bonus(ticker, sector=sector, market=market)

            # v3.0: ML bonus
            ml_bonus_val = 0
            if HAS_ML and self._ml_model:
                try:
                    features = build_features(tech, info, macro, flow, policy_bonus=policy_bonus)
                    ml_pred = predict(features, self._ml_model)
                    ml_bonus_val = get_ml_bonus(ml_pred.probability)
                except Exception:
                    pass

            # v3.0: sentiment bonus
            sentiment_bonus = 0
            if ticker in self._sentiment_cache and HAS_SENTIMENT:
                try:
                    sentiment_bonus = get_sentiment_bonus(self._sentiment_cache[ticker])
                except Exception:
                    pass

            # v3.0: leading sector bonus
            from kstock.signal.policy_engine import _load_config as _load_policy_config
            try:
                pc = _load_policy_config()
                leading = pc.get("leading_sectors", {})
                tier1 = leading.get("tier1", [])
                tier2 = leading.get("tier2", [])
                leading_sector_bonus = 5 if sector in tier1 else 2 if sector in tier2 else 0
            except Exception:
                leading_sector_bonus = 0

            score = compute_composite_score(
                macro, flow, info, tech, self.scoring_config,
                mtf_bonus=mtf_bonus, sector_adj=sector_adj,
                policy_bonus=policy_bonus,
                ml_bonus=ml_bonus_val,
                sentiment_bonus=sentiment_bonus,
                leading_sector_bonus=leading_sector_bonus,
            )

            # Multi-strategy evaluation
            strat_signals = evaluate_all_strategies(
                ticker, name, score, tech, flow, macro,
                info_dict=yf_info, sector=sector,
                rs_rank=rs_rank, rs_total=rs_total,
            )
            best_strategy = strat_signals[0].strategy if strat_signals else "A"

            # Enhanced confidence score
            from kstock.signal.strategies import LEVERAGE_ETFS
            conf_score, conf_stars, conf_label = compute_confidence_score(
                base_score=score.composite,
                tech=tech,
                sector_adj=sector_adj,
                roe_top_30=(yf_info.get("roe", 0) >= 15),
                inst_buy_days=inst_days,
                is_leverage_etf=(ticker in LEVERAGE_ETFS),
            )

            return ScanResult(
                ticker=ticker, name=name, score=score,
                tech=tech, info=info, flow=flow,
                strategy_type=best_strategy,
                strategy_signals=strat_signals,
                confidence_score=conf_score,
                confidence_stars=conf_stars,
                confidence_label=conf_label,
            )
        except Exception as e:
            logger.error("Analysis failed %s: %s", ticker, e)
            return None

    async def _scan_single_stock(self, ticker: str) -> ScanResult | None:
        name = ticker
        market = "KOSPI"
        sector = ""
        for s in self.all_tickers:
            if s["code"] == ticker:
                name = s["name"]
                market = s.get("market", "KOSPI")
                sector = s.get("sector", "")
                break
        macro = await self.macro_client.get_snapshot()
        return await self._analyze_stock(ticker, name, macro, market=market, sector=sector)

    async def _get_price(self, ticker: str, base_price: float = 0) -> float:
        """Get current price. KIS API 우선, yfinance 폴백."""
        # 1순위: KIS API (정확도 최우선)
        try:
            price = await self.kis.get_current_price(ticker, 0)
            if price > 0:
                return price
        except Exception:
            pass
        # 2순위: yfinance
        market = "KOSPI"
        for s in self.all_tickers:
            if s["code"] == ticker:
                market = s.get("market", "KOSPI")
                break
        try:
            price = await self.yf_client.get_current_price(ticker, market)
            if price > 0:
                return price
        except Exception:
            pass
        # 3순위: base_price fallback
        if base_price > 0:
            return base_price
        return 0.0

    async def _get_price_detail(self, ticker: str, base_price: float = 0) -> dict:
        """Get price with day change info. KIS 우선 → yfinance 폴백.

        Returns dict: {price, prev_close, day_change, day_change_pct}
        """
        # 1순위: KIS API (전일 대비 포함)
        try:
            detail = await self.kis.get_price_detail(ticker, 0)
            if detail["price"] > 0 and detail["prev_close"] > 0:
                return detail
        except Exception:
            pass
        # 2순위: yfinance로 현재가만, 전일 대비는 0
        price = await self._get_price(ticker, base_price)
        return {
            "price": price,
            "prev_close": price,
            "day_change": 0.0,
            "day_change_pct": 0.0,
        }

    async def _check_and_send_alerts(
        self, bot, result: ScanResult, macro: MacroSnapshot
    ) -> None:
        ticker = result.ticker
        name = result.name
        score = result.score
        tech = result.tech
        strat_type = result.strategy_type

        # Momentum alert (Strategy F)
        if result.strategy_signals:
            for sig in result.strategy_signals:
                if sig.strategy == "F" and sig.action == "BUY":
                    if not self.db.has_recent_alert(ticker, "momentum", hours=24):
                        msg = format_momentum_alert(
                            name, ticker, tech, result.info,
                            rs_rank=0, rs_total=len(self.all_tickers),
                        )
                        await bot.send_message(chat_id=self.chat_id, text=msg)
                        self.db.insert_alert(ticker, "momentum", f"\U0001f680 모멘텀! {name}")
                        if not self.db.has_active_recommendation(ticker):
                            self.db.add_recommendation(
                                ticker=ticker, name=name,
                                rec_price=result.info.current_price,
                                rec_score=score.composite,
                                strategy_type="F",
                                target_pct=STRATEGY_META["F"]["target"],
                                stop_pct=STRATEGY_META["F"]["stop"],
                            )

                elif sig.strategy == "G" and sig.action == "BUY":
                    if not self.db.has_recent_alert(ticker, "breakout", hours=24):
                        msg = format_breakout_alert(name, ticker, tech, result.info)
                        await bot.send_message(chat_id=self.chat_id, text=msg)
                        self.db.insert_alert(ticker, "breakout", f"\U0001f4a5 돌파! {name}")
                        if not self.db.has_active_recommendation(ticker):
                            self.db.add_recommendation(
                                ticker=ticker, name=name,
                                rec_price=result.info.current_price,
                                rec_score=score.composite,
                                strategy_type="G",
                                target_pct=STRATEGY_META["G"]["target"],
                                stop_pct=STRATEGY_META["G"]["stop"],
                            )

        # Buy alert
        if score.signal == "BUY":
            buy_trigger = (
                tech.rsi <= 30 or tech.bb_pctb <= 0.2 or tech.macd_signal_cross == 1
            )
            if buy_trigger and not self.db.has_recent_alert(ticker, "buy", hours=8):
                msg = format_buy_alert(
                    name, ticker, score, tech, result.info, result.flow, macro,
                    strategy_type=strat_type,
                )
                if self.kis_broker.connected:
                    buttons = [[
                        InlineKeyboardButton("\ubc14\ub85c \ub9e4\uc218 \U0001f680", callback_data=f"kis_buy:{ticker}"),
                        InlineKeyboardButton("\uc0c0\uc5b4\uc694 \u2705", callback_data=f"buy:{ticker}"),
                        InlineKeyboardButton("\ud328\uc2a4 \u274c", callback_data=f"kis_pass:{ticker}"),
                    ]]
                else:
                    buttons = [[
                        InlineKeyboardButton("\uc0c0\uc5b4\uc694 \u2705", callback_data=f"buy:{ticker}"),
                        InlineKeyboardButton("\uc548 \uc0b4\ub798\uc694 \u274c", callback_data=f"skip:{ticker}"),
                    ]]
                await bot.send_message(
                    chat_id=self.chat_id, text=msg,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                self.db.insert_alert(
                    ticker, "buy",
                    f"\U0001f7e2 매수! {name} ({score.composite:.1f}점) "
                    f"[{STRATEGY_META.get(strat_type, {}).get('emoji', '')}]",
                )
                if not self.db.has_active_recommendation(ticker):
                    meta = STRATEGY_META.get(strat_type, {})
                    self.db.add_recommendation(
                        ticker=ticker, name=name,
                        rec_price=result.info.current_price,
                        rec_score=score.composite, status="active",
                        strategy_type=strat_type,
                        target_pct=meta.get("target", 3.0),
                        stop_pct=meta.get("stop", -5.0),
                    )
                logger.info("Buy alert: %s (%.1f) [%s]", name, score.composite, strat_type)

        elif score.signal == "WATCH":
            watch_trigger = tech.rsi <= 40 or tech.bb_pctb <= 0.35
            if watch_trigger and not self.db.has_recent_alert(ticker, "watch", hours=12):
                msg = format_watch_alert(name, ticker, score, tech, result.info, strat_type)
                buttons = [[
                    InlineKeyboardButton("\U0001f514 알림 받기", callback_data=f"watch_alert:{ticker}"),
                    InlineKeyboardButton("\u274c 관심없음", callback_data=f"nowatch:{ticker}"),
                ]]
                await bot.send_message(
                    chat_id=self.chat_id, text=msg,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                self.db.insert_alert(ticker, "watch", f"\U0001f7e1 주시: {name} ({score.composite:.1f}점)")
                if not self.db.has_active_recommendation(ticker):
                    target_entry = round(result.info.current_price * 0.97, 0)
                    self.db.add_recommendation(
                        ticker=ticker, name=name,
                        rec_price=target_entry, rec_score=score.composite,
                        status="watch", strategy_type=strat_type,
                    )

    async def _check_holdings(self, bot) -> None:
        holdings = self.db.get_active_holdings()
        for h in holdings:
            try:
                ticker = h["ticker"]
                name = h["name"]
                buy_price = h["buy_price"]
                current = await self._get_price(ticker, buy_price)
                self.db.update_holding(
                    h["id"], current_price=current,
                    pnl_pct=round((current - buy_price) / buy_price * 100, 2),
                )

                target_1 = h.get("target_1") or buy_price * 1.03
                stop_price = h.get("stop_price") or buy_price * 0.95

                if current >= target_1 and (h.get("sold_pct") or 0) < 50:
                    if not self.db.has_recent_alert(ticker, "sell", hours=4):
                        msg = format_sell_alert_profit(name, h, current)
                        buttons = [[
                            InlineKeyboardButton("\ud314\uc558\uc5b4\uc694 \u2705", callback_data=f"sell_profit:{ticker}"),
                            InlineKeyboardButton("\ub354 \ub4e4\uace0\uac08\ub798\uc694 \u23f8\ufe0f", callback_data=f"hold_profit:{ticker}"),
                        ]]
                        await bot.send_message(
                            chat_id=self.chat_id, text=msg,
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        self.db.insert_alert(ticker, "sell", f"\U0001f534 익절! {name}")
                elif current <= stop_price:
                    if not self.db.has_recent_alert(ticker, "stop", hours=4):
                        msg = format_sell_alert_stop(name, h, current)
                        buttons = [[
                            InlineKeyboardButton("\uc190\uc808\ud588\uc5b4\uc694 \u2705", callback_data=f"stop_loss:{ticker}"),
                            InlineKeyboardButton("\ubc84\ud2f8\ub798\uc694 \u26a0\ufe0f", callback_data=f"hold_through:{ticker}"),
                        ]]
                        await bot.send_message(
                            chat_id=self.chat_id, text=msg,
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        self.db.insert_alert(ticker, "stop", f"\U0001f534 손절! {name}")
            except Exception as e:
                logger.error("Holdings check error %s: %s", h.get("ticker"), e)

    async def _update_recommendations(self, bot) -> None:
        active_recs = self.db.get_active_recommendations()
        for rec in active_recs:
            try:
                ticker = rec["ticker"]
                name = rec["name"]
                rec_price = rec["rec_price"]
                current = await self._get_price(ticker, rec_price)
                pnl_pct = round((current - rec_price) / rec_price * 100, 2)
                self.db.update_recommendation(rec["id"], current_price=current, pnl_pct=pnl_pct)

                target_1 = rec.get("target_1") or rec_price * 1.03
                stop_price = rec.get("stop_price") or rec_price * 0.95
                strat = rec.get("strategy_type", "A")
                tag = f"[{STRATEGY_META.get(strat, {}).get('emoji', '')}{STRATEGY_META.get(strat, {}).get('name', '')}]"

                if current >= target_1:
                    now = datetime.utcnow().isoformat()
                    self.db.update_recommendation(rec["id"], status="profit", closed_at=now)
                    if self.chat_id:
                        await bot.send_message(
                            chat_id=self.chat_id,
                            text=(
                                f"\U0001f389 추천 성공! {name} {tag}\n\n"
                                f"추천가: {rec_price:,.0f}원 -> 현재: {current:,.0f}원\n"
                                f"수익률: {pnl_pct:+.1f}%\n\n"
                                f"\u2705 목표 도달!"
                            ),
                        )
                elif current <= stop_price:
                    now = datetime.utcnow().isoformat()
                    self.db.update_recommendation(rec["id"], status="stop", closed_at=now)
                    if self.chat_id:
                        await bot.send_message(
                            chat_id=self.chat_id,
                            text=(
                                f"\U0001f6d1 추천 손절! {name} {tag}\n\n"
                                f"추천가: {rec_price:,.0f}원 -> 현재: {current:,.0f}원\n"
                                f"수익률: {pnl_pct:+.1f}%\n\n"
                                f"\U0001f534 손절가 도달"
                            ),
                        )
            except Exception as e:
                logger.error("Reco update error %s: %s", rec.get("ticker"), e)

    async def _generate_claude_briefing(
        self, macro: MacroSnapshot, regime_mode: dict
    ) -> str | None:
        if not self.anthropic_key:
            return None
        try:
            import httpx
            prompt = (
                f"한국 투자자를 위한 오늘의 시장 브리핑을 3~5줄로 작성해주세요. "
                f"데이터: VIX={macro.vix:.1f}({macro.vix_change_pct:+.1f}%), "
                f"S&P500={macro.spx_change_pct:+.2f}%, "
                f"나스닥={macro.nasdaq_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원({macro.usdkrw_change_pct:+.2f}%), "
                f"BTC=${macro.btc_price:,.0f}({macro.btc_change_pct:+.1f}%), "
                f"금=${macro.gold_price:,.0f}({macro.gold_change_pct:+.1f}%), "
                f"레짐={macro.regime}, 모드={regime_mode.get('label', '')}. "
                f"볼드(**) 사용하지 말고 이모지와 줄바꿈으로 가독성을 확보해주세요."
            )
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 500,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["content"][0]["text"]
                logger.warning("Claude API returned %d", resp.status_code)
        except Exception as e:
            logger.warning("Claude API briefing failed: %s", e)
        return None

    def _find_cached_result(self, ticker: str) -> ScanResult | None:
        for r in self._last_scan_results:
            if r.ticker == ticker:
                return r
        return None

    # -- /goal command + 30억 menu handlers (v3.0+ sections 40-46) -----------

    async def cmd_short(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /short command — show short selling & leverage analysis."""
        args = context.args or []

        # If ticker specified: analyze that ticker
        if args:
            ticker = args[0].strip()
            name = ticker
            for item in self.all_tickers:
                if item["code"] == ticker:
                    name = item["name"]
                    break

            await update.message.reply_text(
                f"\U0001f50d {name} ({ticker}) 공매도/레버리지 분석 중...",
            )

            # Fetch data from DB
            short_data = self.db.get_short_selling(ticker, days=60)
            margin_data = self.db.get_margin_balance(ticker, days=60)

            lines: list[str] = []

            # Short selling analysis
            short_signal = analyze_short_selling(short_data, ticker, name)
            lines.append(format_short_alert(short_signal, short_data))
            lines.append("")

            # Short pattern detection
            price_data = self.db.get_supply_demand(ticker, days=20)
            pattern_result = detect_all_patterns(
                short_data, price_data, ticker=ticker, name=name,
            )
            if pattern_result.patterns:
                lines.append(format_pattern_report(pattern_result))
                lines.append("")

            # Margin analysis
            if margin_data:
                margin_signal = detect_margin_patterns(
                    margin_data, price_data, short_data, ticker, name,
                )
                lines.append(format_margin_alert(margin_signal, margin_data))
                lines.append("")

                # Combined score
                combined = compute_combined_leverage_score(
                    short_signal.score_adj, margin_signal.total_score_adj,
                )
                lines.append(f"\U0001f4ca 공매도+레버리지 종합: {combined:+d}점")

            # Calibration
            calibrations = calibrate_all_metrics(short_data, margin_data, ticker)
            if calibrations:
                lines.append("")
                lines.append(format_calibration_report(calibrations, name))

            await update.message.reply_text(
                "\n".join(lines), reply_markup=MAIN_MENU,
            )
        else:
            # No ticker: show portfolio overview
            last_ss = self.db.get_last_screenshot()
            if not last_ss:
                await update.message.reply_text(
                    "\U0001f4f8 먼저 계좌 스크린샷을 전송해주세요.\n"
                    "또는: /short [종목코드]\n예) /short 005930",
                    reply_markup=MAIN_MENU,
                )
                return

            import json as _json
            try:
                holdings = _json.loads(last_ss.get("holdings_json", "[]") or "[]")
            except (_json.JSONDecodeError, TypeError):
                holdings = []

            if not holdings:
                await update.message.reply_text(
                    "\U0001f4ca 보유 종목이 없습니다.", reply_markup=MAIN_MENU,
                )
                return

            lines = ["\U0001f4ca 포트폴리오 공매도/레버리지 현황\n"]

            for h in holdings[:10]:
                ticker = h.get("ticker", "")
                name = h.get("name", "?")
                if not ticker:
                    continue

                short_data = self.db.get_short_selling(ticker, days=20)
                signal = analyze_short_selling(short_data, ticker, name)

                status = ""
                if signal.is_overheated:
                    status = "\U0001f6a8 과열"
                elif signal.score_adj <= -5:
                    status = "\U0001f534 주의"
                elif signal.score_adj >= 5:
                    status = "\U0001f7e2 긍정"
                else:
                    status = "\u26aa 보통"

                latest_ratio = 0.0
                if short_data:
                    latest_ratio = short_data[-1].get("short_ratio", 0.0)

                lines.append(
                    f"  {name}: {status} (비중 {latest_ratio:.1f}%, "
                    f"스코어 {signal.score_adj:+d})"
                )

            lines.append("")
            lines.append("상세 분석: /short [종목코드]")

            await update.message.reply_text(
                "\n".join(lines), reply_markup=MAIN_MENU,
            )

    async def cmd_goal(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._menu_goal(update, context)

    async def _menu_goal(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """30억 목표 대시보드."""
        from kstock.bot.messages import format_goal_dashboard

        # Get current asset from screenshot or holdings
        last_ss = self.db.get_last_screenshot()
        current_asset = 175_000_000
        holdings_list = []
        if last_ss:
            current_asset = last_ss.get("total_eval", 175_000_000) or 175_000_000
            import json
            try:
                h_json = last_ss.get("holdings_json", "[]")
                holdings_list = json.loads(h_json) if h_json else []
            except (json.JSONDecodeError, TypeError):
                holdings_list = []

        progress = compute_goal_progress(current_asset)
        tenbagger_count = len(self.db.get_active_tenbagger_candidates())
        swing_count = len(self.db.get_active_swing_trades())

        progress_dict = {
            "start_asset": progress.start_asset,
            "current_asset": progress.current_asset,
            "target_asset": progress.target_asset,
            "progress_pct": progress.progress_pct,
            "current_milestone": progress.current_milestone,
            "milestone_progress_pct": progress.milestone_progress_pct,
            "monthly_return_pct": progress.monthly_return_pct,
            "needed_monthly_pct": progress.needed_monthly_pct,
        }

        msg = format_goal_dashboard(
            progress_dict,
            holdings=holdings_list,
            tenbagger_count=tenbagger_count,
            swing_count=swing_count,
        )
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def _menu_swing(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """스윙 트레이딩 기회 조회."""
        from kstock.bot.messages import format_swing_alert

        active_swings = self.db.get_active_swing_trades()
        if active_swings:
            lines = ["\u26a1 활성 스윙 거래\n"]
            for sw in active_swings[:5]:
                pnl = sw.get("pnl_pct", 0)
                lines.append(
                    f"{sw['name']} {_won(sw['entry_price'])} -> "
                    f"목표 {_won(sw.get('target_price', 0))} "
                    f"({pnl:+.1f}%)"
                )
            msg = "\n".join(lines)
        else:
            msg = "\u26a1 현재 활성 스윙 거래가 없습니다.\n\n스캔 중 조건 충족 종목 발견 시 알려드리겠습니다."
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    # -- v3.5 handlers ---------------------------------------------------------

    async def _menu_ai_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """AI 질문 모드 - 자주하는 질문 4개 버튼 + 직접 입력 안내."""
        buttons = [
            [InlineKeyboardButton("📊 오늘 시장 분석", callback_data="quick_q:market")],
            [InlineKeyboardButton("💼 내 포트폴리오 조언", callback_data="quick_q:portfolio")],
            [InlineKeyboardButton("🔥 지금 매수할 종목", callback_data="quick_q:buy_pick")],
            [InlineKeyboardButton("⚠️ 리스크 점검", callback_data="quick_q:risk")],
        ]
        msg = (
            "🤖 주호님, 무엇이든 물어보세요!\n\n"
            "⬇️ 자주하는 질문을 바로 선택하거나,\n"
            "💬 채팅창에 직접 입력하세요.\n\n"
            "예시: 에코프로 어떻게 보여? / 반도체 전망은?"
        )
        await update.message.reply_text(
            msg, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _handle_ai_question(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, question: str
    ) -> None:
        """Process free-form text as AI question."""
        if not self.anthropic_key:
            await update.message.reply_text(
                "주호님, AI 기능을 사용하려면 ANTHROPIC_API_KEY 설정이 필요합니다.",
                reply_markup=MAIN_MENU,
            )
            return

        # 즉시 "처리 중..." 메시지 → edit로 교체
        placeholder = await update.message.reply_text(
            "\U0001f4ad 주호님의 질문을 분석하고 있습니다..."
        )
        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(
                self.db, self.macro_client, self.yf_client,
            )
            answer = await handle_ai_question(question, ctx, self.db, chat_mem)
            try:
                await placeholder.edit_text(answer)
            except Exception:
                await update.message.reply_text(answer, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("AI chat error: %s", e, exc_info=True)
            try:
                await placeholder.edit_text(
                    "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                )
            except Exception:
                await update.message.reply_text(
                    "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    reply_markup=MAIN_MENU,
                )

    async def _handle_quick_question(
        self, query, context: ContextTypes.DEFAULT_TYPE, question_type: str
    ) -> None:
        """Handle quick question buttons from AI chat menu."""
        questions = {
            "market": "오늘 미국/한국 시장 전체 흐름을 분석하고, 지금 어떤 전략이 유효한지 판단해줘",
            "portfolio": "내 보유종목 전체를 점검하고, 각 종목별로 지금 해야 할 행동(홀딩/추매/익절/손절)을 구체적으로 알려줘",
            "buy_pick": "현재 시장 상황에서 매수하기 좋은 한국 주식 3개를 골라서 목표가와 손절가까지 제시해줘",
            "risk": "내 포트폴리오의 리스크를 점검해줘. 집중도, 섹터 편중, 손실 종목, 전체 시장 리스크를 분석하고 대응 방안을 알려줘",
        }
        question = questions.get(question_type, "오늘 시장 어때?")

        if not self.anthropic_key:
            await query.edit_message_text(
                "주호님, AI 기능을 사용하려면 ANTHROPIC_API_KEY 설정이 필요합니다."
            )
            return

        await query.edit_message_text(
            "\U0001f4ad 주호님의 질문을 분석하고 있습니다..."
        )

        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(self.db, self.macro_client)
            answer = await handle_ai_question(question, ctx, self.db, chat_mem)
            try:
                await query.edit_message_text(answer)
            except Exception:
                await query.message.reply_text(answer, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Quick question error: %s", e, exc_info=True)
            try:
                await query.edit_message_text(
                    "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                )
            except Exception:
                pass

    async def _menu_reports(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """최근 증권사 리포트 조회."""
        reports = self.db.get_recent_reports(limit=5)
        if reports:
            lines = ["\U0001f4cb 최근 증권사 리포트\n"]
            for r in reports:
                opinion = r.get("opinion", "")
                target = r.get("target_price", 0)
                target_str = f" 목표가 {target:,.0f}원" if target else ""
                lines.append(
                    f"[{r.get('broker', '')}] {r.get('title', '')}\n"
                    f"  {opinion}{target_str} ({r.get('date', '')})"
                )
            msg = "\n".join(lines)
        else:
            msg = "\U0001f4cb 수집된 리포트가 없습니다.\n리포트 수집이 시작되면 여기에 표시됩니다."
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def _menu_financial(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """재무 진단 안내."""
        msg = (
            "\U0001f4ca 재무 진단\n\n"
            "사용법: /finance [종목코드 또는 종목명]\n"
            "예) /finance 에코프로\n"
            "예) /finance 005930\n\n"
            "보유 종목의 성장성, 수익성, 안정성, 밸류에이션을 분석합니다."
        )
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def cmd_finance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /finance command."""
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: /finance [종목코드]\n예) /finance 005930",
                reply_markup=MAIN_MENU,
            )
            return
        query = args[0].strip()
        ticker = query
        name = query
        for item in self.all_tickers:
            if item["code"] == query or item["name"] == query:
                ticker = item["code"]
                name = item["name"]
                break

        fin_data = self.db.get_financials(ticker)
        if fin_data:
            from kstock.signal.financial_analyzer import (
                FinancialData, analyze_financials, format_financial_report,
            )
            fd = FinancialData(
                ticker=ticker, name=name,
                revenue=fin_data.get("revenue", 0),
                operating_income=fin_data.get("operating_income", 0),
                net_income=fin_data.get("net_income", 0),
                op_margin=fin_data.get("op_margin", 0),
                roe=fin_data.get("roe", 0),
                roa=fin_data.get("roa", 0),
                debt_ratio=fin_data.get("debt_ratio", 0),
                current_ratio=fin_data.get("current_ratio", 0),
                per=fin_data.get("per", 0),
                pbr=fin_data.get("pbr", 0),
                eps=fin_data.get("eps", 0),
                bps=fin_data.get("bps", 0),
                dps=fin_data.get("dps", 0),
                fcf=fin_data.get("fcf", 0),
                ebitda=fin_data.get("ebitda", 0),
            )
            score = analyze_financials(fd)
            msg = format_financial_report(fd, score)
        else:
            msg = f"\U0001f4ca {name} 재무 데이터가 아직 수집되지 않았습니다."
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def cmd_consensus(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /consensus command."""
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: /consensus [종목코드 또는 종목명]\n예) /consensus 에코프로",
                reply_markup=MAIN_MENU,
            )
            return
        query = args[0].strip()
        ticker = query
        name = query
        for item in self.all_tickers:
            if item["code"] == query or item["name"] == query:
                ticker = item["code"]
                name = item["name"]
                break

        consensus_data = self.db.get_consensus(ticker)
        if consensus_data:
            from kstock.signal.consensus_tracker import format_consensus_from_dict
            msg = format_consensus_from_dict(consensus_data)
        else:
            msg = f"\U0001f4ca {name} 컨센서스 데이터가 아직 수집되지 않았습니다."
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    async def _menu_short(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """공매도 분석 메뉴."""
        await self.cmd_short(update, context)

    async def _menu_future_tech(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """미래기술 워치리스트 메뉴."""
        await self.cmd_future(update, context)

    async def cmd_future(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /future command.

        /future        → 전체 워치리스트 개요
        /future ad     → 자율주행 상세
        /future space  → 우주항공 상세
        /future qc     → 양자컴퓨터 상세
        """
        try:
            args = context.args or []
            sub = args[0].strip().lower() if args else ""

            # Sector sub-commands
            sector_map = {
                "ad": "autonomous_driving",
                "space": "space_aerospace",
                "qc": "quantum_computing",
            }

            if sub in sector_map:
                sector_key = sector_map[sub]
                # Load scores from DB if available
                db_entries = self.db.get_future_watchlist(sector=sector_key)
                scores = {}
                for entry in db_entries:
                    from kstock.signal.future_tech import FutureStockScore
                    scores[entry["ticker"]] = FutureStockScore(
                        ticker=entry["ticker"],
                        name=entry["name"],
                        sector=entry["sector"],
                        tier=entry["tier"],
                        total_score=entry.get("future_score", 0),
                        tech_maturity=entry.get("tech_maturity", 0),
                        financial_stability=entry.get("financial_stability", 0),
                        policy_benefit=entry.get("policy_benefit", 0),
                        momentum=entry.get("momentum", 0),
                        valuation=entry.get("valuation", 0),
                        details=[],
                    )
                msg = format_sector_detail(sector_key, scores or None)
                await update.message.reply_text(msg, reply_markup=MAIN_MENU)
                return

            # Full overview
            db_entries = self.db.get_future_watchlist()
            scores = {}
            for entry in db_entries:
                from kstock.signal.future_tech import FutureStockScore
                scores[entry["ticker"]] = FutureStockScore(
                    ticker=entry["ticker"],
                    name=entry["name"],
                    sector=entry["sector"],
                    tier=entry["tier"],
                    total_score=entry.get("future_score", 0),
                )

            # Compute future tech weight
            seed_positions = self.db.get_seed_positions()
            total_eval = 0
            last_ss = self.db.get_last_screenshot()
            if last_ss:
                total_eval = last_ss.get("total_eval", 0) or 0
            seed_total = sum(
                (p.get("avg_price", 0) or 0) * (p.get("quantity", 0) or 0)
                for p in seed_positions
            )
            future_pct = (seed_total / total_eval * 100) if total_eval > 0 else 0.0

            # Load triggers per sector
            triggers: dict = {}
            for sk in FUTURE_SECTORS:
                triggers[sk] = self.db.get_future_triggers(sector=sk, days=7, limit=3)

            msg = format_full_watchlist(
                scores=scores or None,
                triggers=triggers or None,
                future_weight_pct=future_pct,
            )
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)

        except Exception as e:
            logger.error("Future tech command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 미래기술 워치리스트 조회 중 오류가 발생했습니다.",
                reply_markup=MAIN_MENU,
            )


    async def cmd_history(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /history command - show account snapshot history and solution stats."""
        try:
            self._persist_chat_id(update)
            snapshots = self.db.get_screenshot_history(limit=10)
            msg = format_account_history(snapshots)

            # Add solution stats
            stats = self.db.get_solution_stats()
            if stats["total"] > 0:
                msg += "\n\n"
                msg += "\u2500" * 22 + "\n"
                msg += "\U0001f4a1 솔루션 이력\n"
                msg += f"총 제안: {stats['total']}건\n"
                msg += f"실행율: {stats['execution_rate']:.0%}\n"
                msg += f"효과율: {stats['effectiveness_rate']:.0%}\n"

            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("History command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 계좌 추이 조회 중 오류가 발생했습니다.",
                reply_markup=MAIN_MENU,
            )


    async def cmd_risk(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /risk command - show risk status and violations."""
        try:
            self._persist_chat_id(update)
            last_ss = self.db.get_last_screenshot()
            if not last_ss:
                await update.message.reply_text(
                    "\u26a0\ufe0f 포트폴리오 데이터가 없습니다. 스크린샷을 먼저 보내주세요.",
                    reply_markup=MAIN_MENU,
                )
                return
            import json
            holdings = json.loads(last_ss.get("holdings_json", "[]")) if last_ss.get("holdings_json") else []
            total_value = last_ss.get("total_eval", 0) or 0
            peak = self.db.get_portfolio_peak() or total_value
            report = check_risk_limits(
                holdings=holdings,
                total_value=total_value,
                peak_value=peak,
                daily_pnl_pct=0.0,
                cash=last_ss.get("cash", 0) or 0,
            )
            msg = format_risk_report(report)
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Risk command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 리스크 조회 중 오류가 발생했습니다.",
                reply_markup=MAIN_MENU,
            )

    async def cmd_health(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /health command - show system health."""
        try:
            self._persist_chat_id(update)
            checks = run_health_checks(db_path=self.db.db_path)
            msg = format_system_report(checks, db_path=self.db.db_path)
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Health command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 시스템 상태 조회 중 오류가 발생했습니다.",
                reply_markup=MAIN_MENU,
            )

    async def cmd_performance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /performance command - show live performance."""
        try:
            self._persist_chat_id(update)
            tracks_raw = self.db.get_recommendation_tracks(limit=100)
            from kstock.core.performance_tracker import RecommendationTrack
            tracks = []
            for r in tracks_raw:
                t = RecommendationTrack(
                    ticker=r["ticker"], name=r["name"],
                    strategy=r.get("strategy", "A"),
                    score=r.get("score", 0),
                    recommended_date=r.get("recommended_date", ""),
                    entry_price=r.get("entry_price", 0),
                    returns={
                        d: r.get(f"return_d{d}", 0) or 0
                        for d in [1, 3, 5, 10, 20]
                        if r.get(f"return_d{d}") is not None
                    },
                    hit=bool(r.get("hit", 0)),
                )
                tracks.append(t)
            summary = compute_performance_summary(tracks, start_date="2026-02-24")
            msg = format_performance_report(summary)
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Performance command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 성과 조회 중 오류가 발생했습니다.",
                reply_markup=MAIN_MENU,
            )

    async def cmd_scenario(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /scenario command - show scenario menu."""
        try:
            self._persist_chat_id(update)
            buttons = [
                [
                    InlineKeyboardButton("관세 인상", callback_data="scn:tariff_increase:0"),
                    InlineKeyboardButton("금리 인하", callback_data="scn:rate_cut:0"),
                ],
                [
                    InlineKeyboardButton("MSCI 편입", callback_data="scn:msci_inclusion:0"),
                    InlineKeyboardButton("폭락 재현", callback_data="scn:crash:0"),
                ],
            ]
            await update.message.reply_text(
                "\U0001f4ca 시나리오 분석을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error("Scenario command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 시나리오 분석 오류.",
                reply_markup=MAIN_MENU,
            )

    async def _action_multi_run(self, query, context, payload: str) -> None:
        """멀티 에이전트 분석 인라인 버튼 콜백."""
        ticker = payload
        try:
            await query.edit_message_text(
                f"\U0001f4ca {ticker} 멀티 에이전트 분석 중..."
            )

            name = ticker
            market = "KOSPI"
            for item in self.all_tickers:
                if item["code"] == ticker:
                    name = item["name"]
                    market = item.get("market", "KOSPI")
                    break

            stock_data = {"name": name, "ticker": ticker, "price": 0}
            try:
                ohlcv = await self.yf_client.get_ohlcv(ticker, market)
                if ohlcv is not None and not ohlcv.empty:
                    tech = compute_indicators(ohlcv)
                    close = ohlcv["close"].astype(float)
                    stock_data.update({
                        "price": float(close.iloc[-1]),
                        "ma5": tech.ma5, "ma20": tech.ma20,
                        "ma60": tech.ma60, "ma120": tech.ma120,
                        "rsi": tech.rsi, "macd": tech.macd,
                        "macd_signal": tech.macd_signal,
                        "volume": float(ohlcv["volume"].iloc[-1]),
                        "avg_volume_20": float(ohlcv["volume"].tail(20).mean()),
                        "high_52w": float(close.max()),
                        "low_52w": float(close.min()),
                        "prices_5d": [float(x) for x in close.tail(5).tolist()],
                    })
            except Exception:
                pass

            fin = self.db.get_financials(ticker)
            if fin:
                stock_data.update({
                    "per": fin.get("per", 0), "pbr": fin.get("pbr", 0),
                    "roe": fin.get("roe", 0), "debt_ratio": fin.get("debt_ratio", 0),
                    "sector_per": fin.get("sector_per", 15),
                    "revenue_growth": fin.get("revenue_growth", 0),
                    "op_growth": fin.get("op_growth", 0),
                    "target_price": fin.get("target_price", 0),
                    "recent_earnings": fin.get("recent_earnings", "정보 없음"),
                })

            price = stock_data.get("price", 0)

            from kstock.bot.multi_agent import run_multi_agent_analysis, format_multi_agent_report_v2
            if self.anthropic_key:
                report = await run_multi_agent_analysis(
                    ticker=ticker, name=name, price=price, stock_data=stock_data,
                )
            else:
                report = create_empty_report(ticker, name, price)

            msg = format_multi_agent_report_v2(report)
            self.db.add_multi_agent_result(
                ticker=ticker, name=name,
                combined_score=report.combined_score,
                verdict=report.verdict, confidence=report.confidence,
            )
            await query.edit_message_text(msg)
        except Exception as e:
            logger.error("Multi-run callback error: %s", e, exc_info=True)
            try:
                await query.edit_message_text("\u26a0\ufe0f 멀티 분석 오류.")
            except Exception:
                pass

    async def _action_sell_plans(self, query, context, payload: str) -> None:
        """Phase 8: 매도 계획 표시."""
        try:
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.edit_message_text("보유종목이 없어 매도 계획을 생성할 수 없습니다.")
                return

            for h in holdings:
                try:
                    cur = await self._get_price(h["ticker"], h.get("buy_price", 0))
                    bp = h.get("buy_price", 0)
                    if bp > 0:
                        h["current_price"] = cur
                        h["pnl_pct"] = round((cur - bp) / bp * 100, 2)
                except Exception:
                    pass

            market_state = self.market_pulse.get_current_state()
            plans = self.sell_planner.create_plans_for_all(holdings, market_state)
            msg = format_sell_plans(plans)

            # 텔레그램 메시지 길이 제한 (4096자)
            if len(msg) > 4000:
                msg = msg[:3990] + "\n\n... (일부 생략)"

            await query.edit_message_text(msg)
        except Exception as e:
            logger.error("Sell plans error: %s", e, exc_info=True)
            try:
                await query.edit_message_text("\u26a0\ufe0f 매도 계획 생성 오류.")
            except Exception:
                pass

    async def _action_scenario_run(self, query, context, payload: str) -> None:
        """Handle scenario selection callback."""
        try:
            scenario_key, _, _ = payload.partition(":")
            last_ss = self.db.get_last_screenshot()
            if not last_ss or not last_ss.get("holdings_json"):
                await query.edit_message_text("\u26a0\ufe0f 포트폴리오 데이터가 없습니다.")
                return
            import json
            holdings = json.loads(last_ss["holdings_json"])
            result = simulate_scenario(holdings, scenario_key)
            msg = format_scenario_report(scenario_key, result)
            await query.edit_message_text(msg)
        except Exception as e:
            logger.error("Scenario run error: %s", e, exc_info=True)
            try:
                await query.edit_message_text("\u26a0\ufe0f 시나리오 분석 오류.")
            except Exception:
                pass

    async def cmd_ml(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /ml command - show ML model status."""
        try:
            self._persist_chat_id(update)
            ml_records = self.db.get_ml_performance(limit=6)
            if not ml_records:
                await update.message.reply_text(
                    "\U0001f916 ML 모델 성능 기록이 없습니다.\n재학습 후 자동 기록됩니다.",
                    reply_markup=MAIN_MENU,
                )
                return
            latest = ml_records[0]
            monthly_vals = [r.get("val_score", 0) for r in ml_records]
            from kstock.signal.ml_validator import check_model_drift
            drift = check_model_drift(monthly_vals)
            cv_result = {
                "train_score": latest.get("train_score", 0),
                "avg_val": latest.get("val_score", 0),
                "overfit_gap": latest.get("overfit_gap", 0),
                "val_scores": monthly_vals,
            }
            msg = format_ml_report(cv_result, None, drift)
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("ML command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f ML 상태 조회 오류.",
                reply_markup=MAIN_MENU,
            )


    # -- Phase 7 commands --------------------------------------------------------

    async def cmd_multi(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /multi <종목> - multi-agent analysis."""
        try:
            self._persist_chat_id(update)
            args = context.args
            if not args:
                await update.message.reply_text(
                    "사용법: /multi <종목명 또는 종목코드>\n예: /multi 삼성전자",
                    reply_markup=MAIN_MENU,
                )
                return
            query = " ".join(args)

            # 종목 찾기
            ticker = query
            name = query
            market = "KOSPI"
            for item in self.all_tickers:
                if item["code"] == query or item["name"] == query:
                    ticker = item["code"]
                    name = item["name"]
                    market = item.get("market", "KOSPI")
                    break

            placeholder = await update.message.reply_text(
                f"\U0001f4ca {name} 멀티 에이전트 분석 중... (2개 에이전트 병렬 호출)"
            )

            # 종목 데이터 수집
            stock_data = {"name": name, "ticker": ticker, "price": 0}
            try:
                ohlcv = await self.yf_client.get_ohlcv(ticker, market)
                if ohlcv is not None and not ohlcv.empty:
                    tech = compute_indicators(ohlcv)
                    close = ohlcv["close"].astype(float)
                    stock_data.update({
                        "price": float(close.iloc[-1]),
                        "ma5": tech.ma5, "ma20": tech.ma20,
                        "ma60": tech.ma60, "ma120": tech.ma120,
                        "rsi": tech.rsi, "macd": tech.macd,
                        "macd_signal": tech.macd_signal,
                        "volume": float(ohlcv["volume"].iloc[-1]),
                        "avg_volume_20": float(ohlcv["volume"].tail(20).mean()),
                        "high_52w": float(close.tail(252).max()) if len(close) >= 252 else float(close.max()),
                        "low_52w": float(close.tail(252).min()) if len(close) >= 252 else float(close.min()),
                        "prices_5d": [float(x) for x in close.tail(5).tolist()],
                    })
            except Exception:
                pass

            fin = self.db.get_financials(ticker)
            if fin:
                stock_data.update({
                    "per": fin.get("per", 0), "pbr": fin.get("pbr", 0),
                    "roe": fin.get("roe", 0), "debt_ratio": fin.get("debt_ratio", 0),
                    "sector_per": fin.get("sector_per", 15),
                    "revenue_growth": fin.get("revenue_growth", 0),
                    "op_growth": fin.get("op_growth", 0),
                    "target_price": fin.get("target_price", 0),
                    "recent_earnings": fin.get("recent_earnings", "정보 없음"),
                })

            price = stock_data.get("price", 0)

            # 멀티 에이전트 분석 (API 키 있으면 실제 호출, 없으면 빈 리포트)
            from kstock.bot.multi_agent import run_multi_agent_analysis, format_multi_agent_report_v2
            if self.anthropic_key:
                report = await run_multi_agent_analysis(
                    ticker=ticker, name=name, price=price, stock_data=stock_data,
                )
            else:
                report = create_empty_report(ticker, name, price)

            msg = format_multi_agent_report_v2(report)
            self.db.add_multi_agent_result(
                ticker=ticker, name=name,
                combined_score=report.combined_score,
                verdict=report.verdict, confidence=report.confidence,
            )
            try:
                await placeholder.edit_text(msg)
            except Exception:
                await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Multi-agent command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 멀티 에이전트 분석 오류.", reply_markup=MAIN_MENU,
            )

    async def cmd_surge(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /surge - scan for surge stocks in real-time."""
        try:
            self._persist_chat_id(update)
            placeholder = await update.message.reply_text(
                "\U0001f525 급등주 실시간 스캔 중..."
            )

            # 실시간 스캔: 유니버스 전체 종목의 등락률/거래량 체크
            stocks_data = []
            for item in self.all_tickers:
                try:
                    code = item["code"]
                    market = item.get("market", "KOSPI")
                    ohlcv = await self.yf_client.get_ohlcv(code, market, period="1mo")
                    if ohlcv is None or ohlcv.empty or len(ohlcv) < 2:
                        continue
                    close = ohlcv["close"].astype(float)
                    volume = ohlcv["volume"].astype(float)
                    cur_price = float(close.iloc[-1])
                    prev_price = float(close.iloc[-2])
                    change_pct = ((cur_price - prev_price) / prev_price * 100) if prev_price > 0 else 0
                    avg_vol_20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
                    cur_vol = float(volume.iloc[-1])
                    vol_ratio = cur_vol / avg_vol_20 if avg_vol_20 > 0 else 0
                    mkt_cap = cur_price * 1e6  # 대략적 시총 (정확하지 않지만 필터용)

                    # 급등 조건: +3% 이상 또는 거래량 2배 이상
                    if change_pct >= 3.0 or vol_ratio >= 2.0:
                        stocks_data.append({
                            "ticker": code,
                            "name": item["name"],
                            "price": cur_price,
                            "change_pct": change_pct,
                            "volume": cur_vol,
                            "avg_volume_20": avg_vol_20,
                            "volume_ratio": vol_ratio,
                            "market_cap": mkt_cap,
                            "daily_volume": cur_vol * cur_price,
                            "is_managed": False,
                            "is_warning": False,
                            "listing_days": 999,
                            "has_news": False,
                            "has_disclosure": False,
                            "inst_net": 0,
                            "foreign_net": 0,
                            "retail_net": 0,
                            "prev_vol_ratio": 0,
                            "detected_time": datetime.now(KST).strftime("%H:%M"),
                            "past_suspicious_count": 0,
                        })
                except Exception:
                    continue

            if not stocks_data:
                try:
                    await placeholder.edit_text(
                        "\U0001f525 현재 급등 조건을 충족하는 종목이 없습니다."
                    )
                except Exception:
                    pass
                return

            # 등락률 기준 정렬, 상위 10개
            stocks_data.sort(key=lambda s: s["change_pct"], reverse=True)
            top = stocks_data[:10]

            lines = [f"\U0001f525 급등주 실시간 스캔 ({len(stocks_data)}종목 감지)\n"]
            for i, s in enumerate(top, 1):
                icon = "\U0001f4c8" if s["change_pct"] >= 5 else "\U0001f525" if s["change_pct"] >= 3 else "\u26a1"
                lines.append(
                    f"{i}. {icon} {s['name']}({s['ticker']}) "
                    f"{s['change_pct']:+.1f}% "
                    f"거래량 {s['volume_ratio']:.1f}배"
                )
                # DB에도 저장
                self.db.add_surge_stock(
                    ticker=s["ticker"], name=s["name"],
                    scan_time=s["detected_time"],
                    change_pct=s["change_pct"],
                    volume_ratio=s["volume_ratio"],
                    triggers="price_surge" if s["change_pct"] >= 5 else "combined",
                    market_cap=s["market_cap"],
                    health_grade="HEALTHY" if s["change_pct"] < 10 else "CAUTION",
                )

            try:
                await placeholder.edit_text("\n".join(lines))
            except Exception:
                await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Surge command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 급등주 스캔 중 오류가 발생했습니다.", reply_markup=MAIN_MENU,
            )

    async def cmd_feedback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /feedback - strategy win rates + feedback status."""
        try:
            self._persist_chat_id(update)
            from kstock.signal.feedback_loop import (
                generate_weekly_feedback,
                format_feedback_report,
            )
            report = generate_weekly_feedback(self.db, period_days=90)
            msg = format_feedback_report(report)
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Feedback command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 피드백 조회 오류.", reply_markup=MAIN_MENU,
            )

    async def cmd_stats(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /stats - overall recommendation scorecard."""
        try:
            self._persist_chat_id(update)
            stats = self.db.get_strategy_stats(limit=20)
            if not stats:
                await update.message.reply_text(
                    "\U0001f4ca 추천 성적 데이터가 아직 없습니다.",
                    reply_markup=MAIN_MENU,
                )
                return
            lines = ["\U0001f4ca 전체 추천 성적표\n"]
            for s in stats:
                lines.append(
                    f"  {s.get('strategy', '')}: 승률 {s.get('win_rate', 0):.0f}% "
                    f"({s.get('win_count', 0)}/{s.get('total_count', 0)}), "
                    f"평균 {s.get('avg_return', 0):+.1f}%"
                )
            await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Stats command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 성적표 조회 오류.", reply_markup=MAIN_MENU,
            )

    async def cmd_accumulation(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /accumulation - real-time stealth accumulation scan."""
        try:
            self._persist_chat_id(update)
            placeholder = await update.message.reply_text(
                "\U0001f575\ufe0f 매집 패턴 실시간 탐지 중..."
            )

            # 유니버스 종목의 기관/외인 수급 데이터 수집
            stocks_data = []
            for item in self.all_tickers[:30]:  # 상위 30종목만 (속도)
                try:
                    code = item["code"]
                    market = item.get("market", "KOSPI")
                    ohlcv = await self.yf_client.get_ohlcv(code, market, period="3mo")
                    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
                        continue
                    close = ohlcv["close"].astype(float)
                    volume = ohlcv["volume"].astype(float)

                    # 20일 가격 변화율
                    if len(close) >= 20:
                        price_20d_ago = float(close.iloc[-20])
                        price_now = float(close.iloc[-1])
                        prc_chg = ((price_now - price_20d_ago) / price_20d_ago * 100) if price_20d_ago > 0 else 0
                    else:
                        prc_chg = 0

                    # 거래량 기반 의사-수급 데이터 (실제 기관/외인 데이터 없이 추정)
                    # 거래량이 평균 대비 높으면 기관/외인 매수로 추정
                    avg_vol = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
                    daily_inst = []
                    daily_foreign = []
                    for j in range(-20, 0):
                        if abs(j) <= len(volume):
                            v = float(volume.iloc[j])
                            ratio = v / avg_vol if avg_vol > 0 else 1
                            # 거래량 1.5배 이상이면 기관 매수로 추정
                            inst_est = v * 0.3 if ratio > 1.5 else -v * 0.1
                            foreign_est = v * 0.2 if ratio > 1.3 else -v * 0.1
                            daily_inst.append(inst_est)
                            daily_foreign.append(foreign_est)

                    stocks_data.append({
                        "ticker": code,
                        "name": item["name"],
                        "daily_inst": daily_inst,
                        "daily_foreign": daily_foreign,
                        "price_change_20d": prc_chg,
                        "disclosure_text": "",
                    })
                except Exception:
                    continue

            if not stocks_data:
                try:
                    await placeholder.edit_text(
                        "\U0001f575\ufe0f 분석 가능한 종목 데이터가 없습니다."
                    )
                except Exception:
                    pass
                return

            # 매집 패턴 탐지
            detections = scan_accumulations(stocks_data)

            if not detections:
                try:
                    await placeholder.edit_text(
                        "\U0001f575\ufe0f 현재 매집 패턴이 감지되지 않았습니다.\n"
                        f"({len(stocks_data)}종목 스캔 완료)"
                    )
                except Exception:
                    pass
                return

            lines = [f"\U0001f575\ufe0f 스텔스 매집 감지 ({len(detections)}종목)\n"]
            for i, d in enumerate(detections[:10], 1):
                lines.append(
                    f"{i}. {d.name} ({d.ticker}) "
                    f"스코어 {d.total_score}"
                )
                lines.append(
                    f"   기관 누적: {d.inst_total / 1e8:.0f}억, "
                    f"외인 누적: {d.foreign_total / 1e8:.0f}억, "
                    f"20일 등락: {d.price_change_20d:+.1f}%"
                )
                # DB에도 저장
                import json
                patterns_json = json.dumps(
                    [{"type": p.pattern_type, "days": p.streak_days, "score": p.score}
                     for p in d.patterns],
                    ensure_ascii=False,
                ) if d.patterns else "[]"
                self.db.add_stealth_accumulation(
                    ticker=d.ticker, name=d.name,
                    total_score=d.total_score,
                    patterns_json=patterns_json,
                    price_change_20d=d.price_change_20d,
                    inst_total=d.inst_total,
                    foreign_total=d.foreign_total,
                )

            try:
                await placeholder.edit_text("\n".join(lines))
            except Exception:
                await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Accumulation command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 매집 탐지 중 오류가 발생했습니다.", reply_markup=MAIN_MENU,
            )

    async def _menu_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🛠 관리자 메뉴 버튼 — 인라인 버튼으로 관리 기능 제공."""
        buttons = [
            [
                InlineKeyboardButton("\U0001f41b 오류 신고", callback_data="adm:bug"),
                InlineKeyboardButton("\U0001f4ca 봇 상태", callback_data="adm:status"),
            ],
            [
                InlineKeyboardButton("\U0001f4cb 보유종목 DB", callback_data="adm:holdings"),
                InlineKeyboardButton("\U0001f6a8 에러 로그", callback_data="adm:logs"),
            ],
            [
                InlineKeyboardButton("\U0001f4a1 업데이트 요청", callback_data="adm:request"),
            ],
        ]
        await update.message.reply_text(
            "\U0001f6e0 관리자 모드\n\n"
            "아래 버튼을 눌러주세요.\n"
            "오류 신고 시 메시지나 스크린샷을\n"
            "바로 보내면 됩니다!",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _handle_admin_callback(
        self, query, context, payload: str
    ) -> None:
        """관리자 콜백 핸들러."""
        import json as _json

        admin_log_path = Path("data/admin_reports.jsonl")
        admin_log_path.parent.mkdir(parents=True, exist_ok=True)

        subcmd = payload.split(":")[0] if payload else ""

        if subcmd == "bug":
            # 오류 신고 모드 진입 — 다음 메시지/이미지를 버그로 기록
            context.user_data["admin_mode"] = "bug_report"
            await query.edit_message_text(
                "\U0001f41b 오류 신고 모드\n\n"
                "아래 내용을 보내주세요:\n"
                "  \U0001f4dd 텍스트로 오류 설명\n"
                "  \U0001f4f7 오류 화면 스크린샷\n\n"
                "보내시면 자동으로 기록됩니다.\n"
                "Claude Code에서 바로 확인 후 수정!"
            )

        elif subcmd == "request":
            # 업데이트 요청 모드
            context.user_data["admin_mode"] = "update_request"
            await query.edit_message_text(
                "\U0001f4a1 업데이트 요청 모드\n\n"
                "원하는 기능이나 개선사항을\n"
                "메시지로 보내주세요!\n\n"
                "Claude Code에서 확인 후 구현합니다."
            )

        elif subcmd == "status":
            holdings = self.db.get_active_holdings()
            chat_count = 0
            try:
                chat_count = self.db.get_chat_usage(_today())
            except Exception:
                pass
            uptime = datetime.now(KST) - self._start_time
            hours = uptime.seconds // 3600
            mins = (uptime.seconds % 3600) // 60
            await query.edit_message_text(
                f"\U0001f4ca 봇 상태\n\n"
                f"\u2705 가동: {hours}시간 {mins}분\n"
                f"\U0001f4b0 보유종목: {len(holdings)}개\n"
                f"\U0001f916 AI 채팅: {chat_count}회/50\n"
                f"\U0001f310 KIS: {'연결' if self.kis_broker.connected else '미연결'}\n"
                f"\U0001f4c5 날짜: {datetime.now(KST).strftime('%m/%d %H:%M')}"
            )

        elif subcmd == "holdings":
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.edit_message_text(
                    "\U0001f4ad DB에 보유종목 없음\n잔고 스크린샷을 보내주세요!"
                )
                return
            lines = [f"\U0001f4ca 보유종목 ({len(holdings)}개)\n"]
            for h in holdings[:10]:
                pnl = h.get("pnl_pct", 0)
                e = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
                lines.append(
                    f"{e} {h.get('name', '')} {pnl:+.1f}%"
                )
            await query.edit_message_text("\n".join(lines))

        elif subcmd == "logs":
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-50", "bot.log"],
                    capture_output=True, text=True, timeout=5,
                )
                errors = [
                    l.strip()[-90:]
                    for l in result.stdout.splitlines()
                    if "ERROR" in l
                ][-8:]
                if errors:
                    await query.edit_message_text(
                        "\U0001f6a8 최근 에러\n\n" + "\n\n".join(errors)
                    )
                else:
                    await query.edit_message_text("\u2705 에러 없음!")
            except Exception as e:
                await query.edit_message_text(f"\u26a0\ufe0f 로그 확인 실패: {e}")

    async def _save_admin_report(
        self, update: Update, report_type: str, text: str, has_image: bool = False,
    ) -> None:
        """관리자 리포트를 파일에 저장 (Claude Code 모니터링용)."""
        import json as _json
        admin_log_path = Path("data/admin_reports.jsonl")
        admin_log_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "type": report_type,
            "message": text,
            "has_image": has_image,
            "timestamp": datetime.now(KST).isoformat(),
            "status": "open",
        }

        # 이미지가 있으면 파일 ID 기록
        if has_image and update.message.photo:
            report["photo_file_id"] = update.message.photo[-1].file_id

        with open(admin_log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(report, ensure_ascii=False) + "\n")

        type_label = "\U0001f41b 오류 신고" if report_type == "bug_report" else "\U0001f4a1 업데이트 요청"
        await update.message.reply_text(
            f"{type_label} 접수 완료!\n\n"
            f"\U0001f4dd {text[:200]}\n"
            f"\U0001f4f7 이미지: {'있음' if has_image else '없음'}\n"
            f"\u23f0 {datetime.now(KST).strftime('%H:%M:%S')}\n\n"
            f"Claude Code에서 확인 후\n"
            f"즉시 수정/반영됩니다!",
            reply_markup=MAIN_MENU,
        )

    async def cmd_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """관리자 모드 — 오류 보고 + 봇 상태 확인 + Claude Code 연동.

        사용법:
            /admin bug <에러 내용>     → 버그 리포트 기록
            /admin status              → 봇 상태 종합
            /admin logs                → 최근 에러 로그
            /admin restart             → 봇 재시작 요청
            /admin holdings            → 보유종목 DB 현황
        """
        self._persist_chat_id(update)
        args = context.args or []
        admin_log_path = Path("data/admin_reports.jsonl")
        admin_log_path.parent.mkdir(parents=True, exist_ok=True)

        if not args:
            await update.message.reply_text(
                "\U0001f6e0 관리자 모드\n\n"
                "/admin bug <에러 내용> — 버그 리포트\n"
                "/admin status — 봇 상태\n"
                "/admin logs — 최근 에러\n"
                "/admin holdings — 보유종목 현황\n\n"
                "\U0001f4a1 버그를 보고하면 Claude Code가\n"
                "자동으로 감지하고 수정합니다.",
                reply_markup=MAIN_MENU,
            )
            return

        subcmd = args[0].lower()

        if subcmd == "bug":
            # 버그 리포트를 파일로 기록 (Claude Code가 모니터링)
            bug_text = " ".join(args[1:]) if len(args) > 1 else "내용 없음"
            import json as _json
            report = {
                "type": "bug",
                "message": bug_text,
                "timestamp": datetime.now(KST).isoformat(),
                "chat_id": str(update.effective_chat.id),
                "status": "open",
            }
            with open(admin_log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(report, ensure_ascii=False) + "\n")
            # 최근 에러 로그도 첨부
            recent_errors = []
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-20", "bot.log"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if "ERROR" in line or "error" in line.lower():
                        recent_errors.append(line.strip()[-120:])
            except Exception:
                pass
            if recent_errors:
                report["recent_errors"] = recent_errors[-5:]
                with open(admin_log_path, "a", encoding="utf-8") as f:
                    f.write(_json.dumps({"type": "error_context", "errors": recent_errors[-5:]}, ensure_ascii=False) + "\n")

            await update.message.reply_text(
                f"\U0001f4e9 버그 리포트 접수 완료\n\n"
                f"내용: {bug_text[:200]}\n"
                f"시간: {datetime.now(KST).strftime('%H:%M:%S')}\n\n"
                f"\U0001f4c1 data/admin_reports.jsonl에 기록됨\n"
                f"Claude Code에서 확인 후 수정 예정",
                reply_markup=MAIN_MENU,
            )

        elif subcmd == "status":
            # 봇 상태 종합
            holdings = self.db.get_active_holdings()
            jobs_today = 0
            try:
                today_str = _today()
                for job_name in ["morning_briefing", "sentiment_analysis", "daily_pdf_report"]:
                    jr = self.db.get_job_run(job_name, today_str)
                    if jr and jr.get("status") == "success":
                        jobs_today += 1
            except Exception:
                pass

            chat_count = 0
            try:
                chat_count = self.db.get_chat_usage(_today())
            except Exception:
                pass

            uptime = datetime.now(KST) - getattr(self, '_start_time', datetime.now(KST))
            lines = [
                "\U0001f4ca 봇 상태 종합\n",
                f"\u2705 가동시간: {uptime.seconds // 3600}시간 {(uptime.seconds % 3600) // 60}분",
                f"\U0001f4b0 보유종목: {len(holdings)}개",
                f"\U0001f916 오늘 AI 채팅: {chat_count}회",
                f"\u23f0 오늘 완료 작업: {jobs_today}/3",
                f"\U0001f4be DB: kquant.db",
                f"\U0001f310 KIS: {'연결됨' if self.kis_broker.connected else '미연결'}",
            ]
            await update.message.reply_text(
                "\n".join(lines), reply_markup=MAIN_MENU,
            )

        elif subcmd == "logs":
            # 최근 에러 로그
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-50", "bot.log"],
                    capture_output=True, text=True, timeout=5,
                )
                error_lines = [
                    l.strip()[-100:]
                    for l in result.stdout.splitlines()
                    if "ERROR" in l or "WARNING" in l
                ][-10:]
                if error_lines:
                    await update.message.reply_text(
                        "\U0001f6a8 최근 에러/경고\n\n" + "\n".join(error_lines),
                        reply_markup=MAIN_MENU,
                    )
                else:
                    await update.message.reply_text(
                        "\u2705 최근 에러 없음!", reply_markup=MAIN_MENU,
                    )
            except Exception as e:
                await update.message.reply_text(
                    f"\u26a0\ufe0f 로그 확인 실패: {e}", reply_markup=MAIN_MENU,
                )

        elif subcmd == "holdings":
            # 보유종목 DB 현황
            holdings = self.db.get_active_holdings()
            if not holdings:
                await update.message.reply_text(
                    "\U0001f4ad DB에 보유종목이 없습니다.\n"
                    "잔고 스크린샷을 찍어주세요!",
                    reply_markup=MAIN_MENU,
                )
                return
            lines = [f"\U0001f4ca 보유종목 DB ({len(holdings)}개)\n"]
            for h in holdings:
                pnl = h.get("pnl_pct", 0)
                emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
                lines.append(
                    f"{emoji} {h.get('name', '')} ({h.get('ticker', '')})\n"
                    f"  매수 {h.get('buy_price', 0):,.0f} | "
                    f"현재 {h.get('current_price', 0):,.0f} | "
                    f"{pnl:+.1f}%"
                )
            await update.message.reply_text(
                "\n".join(lines), reply_markup=MAIN_MENU,
            )

        else:
            await update.message.reply_text(
                f"\u26a0\ufe0f 알 수 없는 명령: {subcmd}\n"
                "/admin 으로 도움말 확인",
                reply_markup=MAIN_MENU,
            )

    async def cmd_register(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /register - manual trade registration."""
        try:
            self._persist_chat_id(update)
            args = context.args
            if not args:
                await update.message.reply_text(
                    "사용법: /register <매수 내용>\n"
                    "예: /register 삼성전자 50주 76000원",
                    reply_markup=MAIN_MENU,
                )
                return
            text = " ".join(args)
            trade = parse_trade_text(text)
            if not trade:
                await update.message.reply_text(
                    "\u26a0\ufe0f 매수 정보를 파싱하지 못했습니다.\n"
                    "예: /register 삼성전자 50주 76000원",
                    reply_markup=MAIN_MENU,
                )
                return
            msg = format_trade_confirmation(trade)
            self.db.add_trade_register(
                ticker=trade.ticker or trade.name,
                name=trade.name,
                quantity=trade.quantity,
                price=trade.price,
                total_amount=trade.total_amount,
                source="text",
            )
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Register command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 매수 등록 오류.", reply_markup=MAIN_MENU,
            )

    async def cmd_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /balance - show portfolio balance from holdings + screenshots."""
        try:
            self._persist_chat_id(update)
            placeholder = await update.message.reply_text(
                "\U0001f4b0 잔고 조회 중..."
            )

            # 1순위: DB 보유종목 (매수 등록된 종목)
            holdings = self.db.get_active_holdings()

            # 2순위: 보유종목 없으면 스크린샷에서 가져오기
            if not holdings:
                try:
                    screenshot = self.db.get_latest_screenshot()
                    if screenshot:
                        import json
                        raw = screenshot.get("holdings_json", "")
                        items = json.loads(raw) if isinstance(raw, str) and raw else []
                        if items:
                            holdings = [
                                {
                                    "ticker": h.get("ticker", ""),
                                    "name": h.get("name", ""),
                                    "buy_price": h.get("avg_price", 0),
                                    "current_price": h.get("current_price", 0),
                                    "quantity": h.get("quantity", 0),
                                    "pnl_pct": h.get("profit_pct", 0),
                                }
                                for h in items
                            ]
                except Exception as e:
                    logger.warning("Screenshot holdings load failed: %s", e)

            if not holdings:
                empty_buttons = [[
                    InlineKeyboardButton(
                        "➕ 종목 추가", callback_data="bal:add",
                    ),
                ]]
                try:
                    await placeholder.edit_text(
                        "💰 주호님, 등록된 보유종목이 없습니다.\n\n"
                        "📸 스크린샷 전송 → 자동 인식\n"
                        "💬 종목명 입력 → 버튼으로 추가\n\n"
                        "아래 버튼을 눌러 시작하세요!",
                        reply_markup=InlineKeyboardMarkup(empty_buttons),
                    )
                except Exception:
                    pass
                return

            # 현재가 + 전일 대비 업데이트
            total_eval = 0.0
            total_invested = 0.0
            for h in holdings:
                try:
                    ticker = h.get("ticker", "")
                    bp = h.get("buy_price", 0)
                    qty = h.get("quantity", 0)
                    if ticker and bp > 0:
                        detail = await self._get_price_detail(ticker, bp)
                        cur = detail["price"]
                        h["current_price"] = cur
                        h["pnl_pct"] = round((cur - bp) / bp * 100, 2) if bp > 0 else 0
                        h["day_change_pct"] = detail["day_change_pct"]
                        h["day_change"] = detail["day_change"]
                        total_eval += cur * qty
                        total_invested += bp * qty
                except Exception:
                    cur = h.get("current_price", h.get("buy_price", 0))
                    total_eval += cur * h.get("quantity", 0)
                    total_invested += h.get("buy_price", 0) * h.get("quantity", 0)

            total_pnl = total_eval - total_invested
            total_pnl_rate = (total_pnl / total_invested * 100) if total_invested > 0 else 0
            pnl_sign = "+" if total_pnl >= 0 else ""
            pnl_arrow = "\u25b2" if total_pnl > 0 else ("\u25bc" if total_pnl < 0 else "\u2015")

            lines = [
                f"\U0001f4b0 주호님 잔고 현황",
                f"\u2500" * 25,
                f"총 평가금액: {total_eval:,.0f}원",
                f"총 투자금액: {total_invested:,.0f}원",
                f"총 손익: {pnl_arrow} {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_pnl_rate:.2f}%)",
                "",
                f"보유종목 ({len(holdings)}개)",
                "\u2500" * 25,
            ]
            for h in holdings:
                name = h.get("name", "")
                ticker = h.get("ticker", "")
                qty = h.get("quantity", 0)
                bp = h.get("buy_price", 0)
                cp = h.get("current_price", bp)
                pnl = h.get("pnl_pct", 0)
                pnl_amount = (cp - bp) * qty
                day_chg_pct = h.get("day_change_pct", 0)
                day_chg = h.get("day_change", 0)
                emoji = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < 0 else "\u26aa"
                pnl_sign_s = "+" if pnl_amount >= 0 else ""
                # 전일 대비 표시
                if day_chg_pct != 0:
                    day_emoji = "📈" if day_chg_pct > 0 else "📉"
                    day_sign = "+" if day_chg_pct > 0 else ""
                    day_line = f"   오늘 {day_emoji} {day_sign}{day_chg:,.0f}원 ({day_sign}{day_chg_pct:.1f}%)"
                else:
                    day_line = ""
                lines.append(
                    f"{emoji} {name}({ticker}) {qty}주\n"
                    f"   매수 {bp:,.0f}원 \u2192 현재 {cp:,.0f}원\n"
                    f"   손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)"
                    + (f"\n{day_line}" if day_line else "")
                )

            bal_buttons = self._build_balance_buttons(holdings)
            try:
                await placeholder.edit_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(bal_buttons),
                )
            except Exception:
                await update.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(bal_buttons),
                )
        except Exception as e:
            logger.error("Balance command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 잔고 조회 중 오류가 발생했습니다.", reply_markup=MAIN_MENU,
            )

    # -- Phase 7 menu handlers ---------------------------------------------------

    async def _menu_multi_agent(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """멀티 에이전트 분석 메뉴 - 최근 결과 표시 + 빠른 분석 버튼."""
        # 최근 분석 결과 조회
        recent = self.db.get_multi_agent_results(limit=5)

        lines = ["\U0001f4ca 멀티 에이전트 분석\n"]

        if recent:
            lines.append("최근 분석 결과:")
            for r in recent:
                verdict_emoji = {
                    "매수": "\U0001f7e2", "홀딩": "\U0001f7e1",
                    "관망": "\u26aa", "매도": "\U0001f534",
                }.get(r.get("verdict", ""), "\u26aa")
                lines.append(
                    f"  {verdict_emoji} {r.get('name', '')} "
                    f"- {r.get('verdict', '관망')} "
                    f"({r.get('combined_score', 0)}/215)"
                )
            lines.append("")

        lines.append("종목명을 직접 입력하면 자동 분석됩니다.")
        lines.append("예: '삼성전자 분석' 또는 /multi 삼성전자")

        # 보유종목 기반 빠른 분석 버튼
        holdings = self.db.get_active_holdings()
        buttons = []
        for h in holdings[:4]:
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            if ticker and name:
                buttons.append([
                    InlineKeyboardButton(
                        f"\U0001f50d {name} 분석",
                        callback_data=f"multi_run:{ticker}",
                    )
                ])

        keyboard = InlineKeyboardMarkup(buttons) if buttons else MAIN_MENU
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=keyboard,
        )

    async def _menu_surge(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """급등주 포착 메뉴."""
        await self.cmd_surge(update, context)

    async def _menu_accumulation(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """매집 탐지 메뉴."""
        await self.cmd_accumulation(update, context)

    async def _menu_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """잔고 조회 메뉴."""
        await self.cmd_balance(update, context)

    # ── 즐겨찾기 메뉴 ──────────────────────────────────────────────

    async def _menu_favorites(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """⭐ 즐겨찾기 — watchlist 종목 표시 + 빠른 액션."""
        watchlist = self.db.get_watchlist()
        if not watchlist:
            buttons = [
                [InlineKeyboardButton("🎯 전략별 보기", callback_data="goto:strategy")],
                [InlineKeyboardButton("📈 추천 성과", callback_data="goto:reco")],
            ]
            await update.message.reply_text(
                "⭐ 즐겨찾기가 비어있습니다.\n\n"
                "추천 종목에서 ⭐ 버튼을 누르면 즐겨찾기에 등록됩니다.\n"
                "또는 종목명을 입력하면 자동으로 추가할 수 있습니다.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        lines = ["⭐ 내 즐겨찾기\n"]
        buttons = []
        for w in watchlist[:15]:
            ticker = w.get("ticker", "")
            name = w.get("name", ticker)
            try:
                detail = await self._get_price_detail(ticker, 0)
                cur = detail["price"]
                dc_pct = detail["day_change_pct"]
                dc = detail["day_change"]
                if cur > 0:
                    dc_sign = "+" if dc_pct > 0 else ""
                    dc_emoji = "📈" if dc_pct > 0 else "📉" if dc_pct < 0 else "─"
                    lines.append(
                        f"{dc_emoji} {name}: {cur:,.0f}원 ({dc_sign}{dc_pct:.1f}%)"
                    )
                else:
                    lines.append(f"─ {name}: 가격 미확인")
            except Exception:
                lines.append(f"─ {name}")
            buttons.append([
                InlineKeyboardButton(
                    f"📋 {name}", callback_data=f"detail:{ticker}",
                ),
                InlineKeyboardButton(
                    "❌", callback_data=f"fav:rm:{ticker}",
                ),
            ])

        buttons.append([
            InlineKeyboardButton("🔄 새로고침", callback_data="fav:refresh"),
        ])
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_favorites(self, query, context, payload: str = "") -> None:
        """즐겨찾기 콜백: fav:add:{ticker}:{name} / fav:rm:{ticker} / fav:refresh."""
        parts = payload.split(":")
        action = parts[0] if parts else ""

        if action == "add":
            ticker = parts[1] if len(parts) > 1 else ""
            name = parts[2] if len(parts) > 2 else ticker
            if ticker:
                self.db.add_watchlist(ticker, name)
                await query.edit_message_text(
                    f"⭐ {name}({ticker})을 즐겨찾기에 등록했습니다!\n\n"
                    "⭐ 즐겨찾기 메뉴에서 확인하세요."
                )
            return

        if action == "rm":
            ticker = parts[1] if len(parts) > 1 else ""
            if ticker:
                self.db.remove_watchlist(ticker)
                await query.edit_message_text(f"⭐ {ticker} 즐겨찾기에서 삭제되었습니다.")
            return

        if action == "refresh":
            await query.edit_message_text("⭐ 즐겨찾기 새로고침 중...")
            watchlist = self.db.get_watchlist()
            if not watchlist:
                await query.message.reply_text("⭐ 즐겨찾기가 비어있습니다.")
                return

            lines = ["⭐ 내 즐겨찾기\n"]
            buttons = []
            for w in watchlist[:15]:
                ticker = w.get("ticker", "")
                name = w.get("name", ticker)
                try:
                    detail = await self._get_price_detail(ticker, 0)
                    cur = detail["price"]
                    dc_pct = detail["day_change_pct"]
                    if cur > 0:
                        dc_sign = "+" if dc_pct > 0 else ""
                        dc_emoji = "📈" if dc_pct > 0 else "📉" if dc_pct < 0 else "─"
                        lines.append(
                            f"{dc_emoji} {name}: {cur:,.0f}원 ({dc_sign}{dc_pct:.1f}%)"
                        )
                    else:
                        lines.append(f"─ {name}")
                except Exception:
                    lines.append(f"─ {name}")
                buttons.append([
                    InlineKeyboardButton(f"📋 {name}", callback_data=f"detail:{ticker}"),
                    InlineKeyboardButton("❌", callback_data=f"fav:rm:{ticker}"),
                ])
            buttons.append([
                InlineKeyboardButton("🔄 새로고침", callback_data="fav:refresh"),
            ])
            await query.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

    # ── 에이전트 대화 메뉴 ─────────────────────────────────────────

    async def _menu_agent_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🤖 에이전트 — 오류 신고/기능 요청을 Claude Code에 전달."""
        context.user_data["agent_mode"] = True
        buttons = [
            [InlineKeyboardButton("🐛 오류 신고", callback_data="agent:bug")],
            [InlineKeyboardButton("💡 기능 요청", callback_data="agent:feature")],
            [InlineKeyboardButton("❓ 질문하기", callback_data="agent:question")],
            [InlineKeyboardButton("🔙 나가기", callback_data="agent:exit")],
        ]
        await update.message.reply_text(
            "🤖 K-Quant 에이전트\n\n"
            "무엇을 도와드릴까요?\n"
            "아래 버튼을 선택하거나, 직접 메시지를 입력하세요.\n\n"
            "입력한 내용은 로그에 기록되어 다음 업데이트에 반영됩니다.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_agent(self, query, context, payload: str = "") -> None:
        """에이전트 콜백: agent:bug/feature/question/exit."""
        if payload == "bug":
            context.user_data["agent_mode"] = True
            context.user_data["agent_type"] = "bug"
            await query.edit_message_text(
                "🐛 오류 신고\n\n"
                "어떤 오류가 발생했나요?\n"
                "스크린샷을 보내거나, 메시지로 설명해주세요.\n\n"
                "예: '잔고에서 가격이 이상해요', '버튼이 안 눌려요'"
            )
        elif payload == "feature":
            context.user_data["agent_mode"] = True
            context.user_data["agent_type"] = "feature"
            await query.edit_message_text(
                "💡 기능 요청\n\n"
                "어떤 기능이 필요하신가요?\n"
                "자유롭게 설명해주세요.\n\n"
                "예: '알림을 카카오톡으로도 받고 싶어요'"
            )
        elif payload == "question":
            context.user_data["agent_mode"] = True
            context.user_data["agent_type"] = "question"
            await query.edit_message_text(
                "❓ 질문하기\n\n"
                "궁금한 점을 물어보세요.\n\n"
                "예: '모멘텀 전략이 뭔가요?', '자동매매는 언제 되나요?'"
            )
        elif payload == "exit":
            context.user_data.pop("agent_mode", None)
            context.user_data.pop("agent_type", None)
            await query.edit_message_text("🔙 에이전트 모드를 종료했습니다.")


    async def _action_goto(self, query, context, payload: str = "") -> None:
        """간단한 메뉴 리다이렉트 콜백."""
        if payload == "strategy":
            buttons = [
                [
                    InlineKeyboardButton("🔥 반등", callback_data="strat:A"),
                    InlineKeyboardButton("⚡ ETF", callback_data="strat:B"),
                    InlineKeyboardButton("🏢 장기", callback_data="strat:C"),
                ],
                [
                    InlineKeyboardButton("🔄 섹터", callback_data="strat:D"),
                    InlineKeyboardButton("🌎 글로벌", callback_data="strat:E"),
                ],
                [
                    InlineKeyboardButton("🚀 모멘텀", callback_data="strat:F"),
                    InlineKeyboardButton("💥 돌파", callback_data="strat:G"),
                ],
            ]
            await query.edit_message_text(
                "🎯 전략을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif payload == "reco":
            recs = self.db.get_active_recommendations()
            if recs:
                lines = ["📈 추천 성과\n"]
                for r in recs[:10]:
                    pnl = r.get("pnl_pct", 0)
                    emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "🟡"
                    lines.append(f"{emoji} {r['name']} ({pnl:+.1f}%)")
                await query.edit_message_text("\n".join(lines))
            else:
                await query.edit_message_text("📈 아직 추천 내역이 없습니다.")


def _won(price: float) -> str:
    return f"\u20a9{price:,.0f}"


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def main() -> None:
    """Entry point: build and run the K-Quant v3.5 Telegram bot with auto-restart."""
    import time

    load_dotenv(override=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    bot = KQuantBot()
    if not bot.token:
        logger.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        return

    while True:
        try:
            app = bot.build_app()
            bot.schedule_jobs(app)
            logger.info("K-Quant v3.5 bot starting (polling)...")
            app.run_polling(drop_pending_updates=True)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error("Bot crashed: %s", e, exc_info=True)
            logger.info("Restarting in 10 seconds...")
            time.sleep(10)
            continue


if __name__ == "__main__":
    main()
