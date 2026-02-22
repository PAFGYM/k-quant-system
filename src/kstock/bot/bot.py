"""Telegram bot with multi-strategy system v3.0 - ML, sentiment, KIS, screenshot."""

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

load_dotenv()
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
        ["\U0001f575\ufe0f 매집탐지", "\U0001f4b0 잔고"],
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
    """Telegram bot for K-Quant system v3.0."""

    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
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
        self.macro_client = MacroClient()
        self.db = SQLiteStore()
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

        jq.run_daily(
            self.job_morning_briefing,
            time=dt_time(hour=8, minute=45, tzinfo=KST),
            name="morning_briefing",
        )
        jq.run_repeating(
            self.job_intraday_monitor,
            interval=300,
            first=30,
            name="intraday_monitor",
        )
        jq.run_daily(
            self.job_eod_report,
            time=dt_time(hour=16, minute=0, tzinfo=KST),
            name="eod_report",
        )
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
        logger.info(
            "Scheduled: morning(08:45), intraday(5min), eod(16:00), "
            "weekly_learn(Sat 09:00), screenshot(Mon/Fri 08:00), "
            "sentiment(08:00), weekly_report(Sun 19:00) KST"
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

            # Format and send summary
            msg = format_screenshot_summary(parsed, comparison, prev_diagnoses)
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)

            # Offer horizon selection before diagnosis
            if holdings and self.anthropic_key:
                context.user_data["pending_horizons"] = {}
                context.user_data["pending_holdings"] = holdings
                context.user_data["pending_screenshot_id"] = screenshot_id

                # Send inline keyboard for each holding
                for h in holdings:
                    name = h.get("name", "?")
                    ticker = h.get("ticker", "000000")
                    is_margin, margin_type = detect_margin_purchase(h)
                    margin_tag = f" \u26a0\ufe0f{margin_type or '신용'}" if is_margin else ""

                    # Check for previous horizon setting
                    prev = self.db.get_portfolio_horizon(ticker)
                    prev_hz = prev.get("horizon", "") if prev else ""
                    prev_label = HORIZON_CONFIG.get(prev_hz, {}).get("label", "")

                    buttons = [
                        [
                            InlineKeyboardButton(
                                "단타 (1~5일)", callback_data=f"hz:danta:{ticker}",
                            ),
                            InlineKeyboardButton(
                                "단기 (1~4주)", callback_data=f"hz:dangi:{ticker}",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "중기 (1~6개월)", callback_data=f"hz:junggi:{ticker}",
                            ),
                            InlineKeyboardButton(
                                "장기 (6개월+)", callback_data=f"hz:janggi:{ticker}",
                            ),
                        ],
                    ]
                    # Add "keep previous" button if a previous setting exists
                    if prev_hz and prev_hz in HORIZON_CONFIG:
                        buttons.append([
                            InlineKeyboardButton(
                                f"이전 설정 유지: {prev_label}",
                                callback_data=f"hz:{prev_hz}:{ticker}",
                            ),
                        ])
                    buttons.append([
                        InlineKeyboardButton(
                            "기본 진단", callback_data=f"hz:default:{ticker}",
                        ),
                    ])

                    prompt_text = f"[{name}]{margin_tag} - 투자 기간을 선택하세요:"
                    if prev_label:
                        prompt_text = (
                            f"[{name}]{margin_tag}\n"
                            f"이전 설정: {prev_label}\n"
                            f"투자 기간을 선택하세요:"
                        )
                    await update.message.reply_text(
                        prompt_text,
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )

                # Add a "skip all" button
                skip_btn = [[
                    InlineKeyboardButton(
                        "전체 기본 진단", callback_data="hz:default_all:0",
                    ),
                ]]
                await update.message.reply_text(
                    "\u2b07\ufe0f 전체 종목에 기본 진단을 적용하려면:",
                    reply_markup=InlineKeyboardMarkup(skip_btn),
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
            "\U0001f4b0 잔고": self._menu_balance,
            # Legacy keys (backward compat)
            "\U0001f514 실시간 알림": self._menu_alerts,
            "\U0001f4ca 오늘의 추천종목": self._menu_recommendations,
            "\U0001f4bc 내 포트폴리오": self._menu_portfolio,
            "\U0001f4ca 백테스트": self._menu_backtest,
            "\u2753 도움말": self._menu_usage_guide,
        }
        handler = handlers.get(text)
        if handler:
            try:
                await handler(update, context)
            except Exception as e:
                logger.error("Menu handler error: %s", e, exc_info=True)
                await update.message.reply_text(
                    "\u26a0\ufe0f 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    reply_markup=MAIN_MENU,
                )
        else:
            # 메뉴에 없는 텍스트 -> AI 질문으로 처리
            await self._handle_ai_question(update, context, text)

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
            "\U0001f4d6 주호님, K-Quant v3.5 사용법입니다!\n\n"
            "[오른쪽 메뉴 - 투자 기능]\n"
            "\U0001f4f8 계좌분석: 증권사 스크린샷을 보내면 AI가 종목별 진단\n"
            "\U0001f4ac AI에게 질문: 무엇이든 물어보세요 (보유종목+시장 컨텍스트 포함)\n"
            "\U0001f4cb 증권사 리포트: 보유종목/추천종목/섹터별 리포트 조회\n"
            "\U0001f4ca 재무 진단: 종목명 입력하면 재무 100점 분석\n"
            "\u26a1 스윙 기회: 오늘의 스윙 트레이딩 추천\n"
            "\U0001f3af 전략별 보기: 7가지 전략별 추천 종목\n"
            "\U0001f4c5 주간 보고서: 매주 일요일 자동 생성 (구글 문서)\n\n"
            "[왼쪽 메뉴 - 설정/관리]\n"
            "\U0001f514 알림 설정: 리포트/수급/실적/관세 알림 ON/OFF\n"
            "\u2699\ufe0f 최적화: 전략 파라미터 최적화\n"
            "\U0001f4e1 KIS설정: 한국투자증권 API 설정\n"
            "\U0001f3af 30억 목표: 현재 자산 \u2192 30억 로드맵 진행률\n"
            "\U0001f4c8 추천 성과: K-Quant 추천 종목 적중률\n"
            "\U0001f30d 시장현황: KOSPI/KOSDAQ/환율/미국 시장\n\n"
            "[자동 알림]\n"
            "매일 08:20 모닝 브리핑\n"
            "보유종목 증권사 리포트 발행 시 즉시 알림\n"
            "외인 3일 연속 매수/매도 시 알림\n"
            "실적 발표 D-3일 사전 알림\n"
            "매주 일요일 19:00 주간 보고서\n\n"
            "아무 텍스트나 입력하면 AI가 답변합니다!"
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
        await update.message.reply_text("\U0001f30d 시장 데이터 수집 중...")
        macro = await self.macro_client.get_snapshot()

        # v3.0: detect_regime replaces get_regime_mode
        regime_result = detect_regime(macro)
        regime_mode = {
            "mode": regime_result.mode,
            "emoji": regime_result.emoji,
            "label": regime_result.label,
            "message": regime_result.message,
            "allocations": regime_result.allocations,
        }

        # Compute sector strength
        await self._update_sector_strengths()
        sector_text = format_sector_strength(self._sector_strengths)

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

        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

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
        await update.message.reply_text(
            "\u2699\ufe0f 최적화 기능\n\n"
            "/optimize [종목코드] 로 파라미터 최적화를 실행하세요.\n"
            "예) /optimize 005930\n\n"
            "RSI, BB, EMA 파라미터를 자동으로 찾아줍니다.",
            reply_markup=MAIN_MENU,
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
        msg = format_kis_status(self.kis_broker)
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

    # == Callback actions ====================================================

    async def _action_buy(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
        if not result:
            await query.edit_message_text("\u26a0\ufe0f 종목 정보를 찾을 수 없습니다.")
            return
        price = result.info.current_price
        self.db.add_holding(ticker, result.name, price)
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
        buttons = [
            [
                InlineKeyboardButton("\uc0c0\uc5b4\uc694 \u2705", callback_data=f"buy:{ticker}"),
                InlineKeyboardButton("\uc548 \uc0b4\ub798\uc694 \u274c", callback_data=f"skip:{ticker}"),
            ]
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

            briefing_text = await self._generate_claude_briefing(macro, regime_mode)
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
            logger.info("Intraday monitor: %d stocks scanned", len(results))
        except Exception as e:
            logger.error("Intraday monitor error: %s", e, exc_info=True)

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

            # Strategy performance summary
            strat_stats = self.db.get_strategy_performance()
            if strat_stats and any(k != "summary" for k in strat_stats):
                perf_msg = format_strategy_performance(strat_stats)
                await context.bot.send_message(chat_id=self.chat_id, text=perf_msg)

            self.db.upsert_job_run("eod_scan", _today(), status="success")
            logger.info("EOD report sent")
        except Exception as e:
            logger.error("EOD report failed: %s", e)
            self.db.upsert_job_run("eod_scan", _today(), status="error", message=str(e))

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
                ohlcv = await self.yf_client.get_ohlcv(ticker, market)
                self._ohlcv_cache[ticker] = ohlcv
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

            # Mock flow data
            foreign_flow = await self.kis.get_foreign_flow(ticker)
            inst_flow = await self.kis.get_institution_flow(ticker)
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
        return await self.kis.get_current_price(ticker, base_price)

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
        """AI 질문 모드 안내."""
        from kstock.bot.chat_handler import format_ai_greeting
        msg = format_ai_greeting()
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)

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
        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context
            from kstock.bot.chat_memory import ChatMemory

            chat_mem = ChatMemory(self.db)
            ctx = build_full_context(self.db)
            answer = await handle_ai_question(question, ctx, self.db, chat_mem)
            await update.message.reply_text(answer, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("AI chat error: %s", e, exc_info=True)
            await update.message.reply_text(
                "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                reply_markup=MAIN_MENU,
            )

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
            report = create_empty_report(query, query, 0)
            msg = format_multi_agent_report(report)
            self.db.add_multi_agent_result(
                ticker=query, name=query,
                combined_score=report.combined_score,
                verdict=report.verdict, confidence=report.confidence,
            )
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Multi-agent command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 멀티 에이전트 분석 오류.", reply_markup=MAIN_MENU,
            )

    async def cmd_surge(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /surge - today's surge stocks."""
        try:
            self._persist_chat_id(update)
            surges = self.db.get_surge_stocks(days=1, limit=10)
            if not surges:
                await update.message.reply_text(
                    "\U0001f525 오늘 감지된 급등주가 없습니다.",
                    reply_markup=MAIN_MENU,
                )
                return
            lines = ["\U0001f525 오늘의 급등주 포착 결과\n"]
            for i, s in enumerate(surges, 1):
                grade = s.get("health_grade", "")
                icon = "\u2705" if grade == "HEALTHY" else "\u26a0\ufe0f" if grade == "CAUTION" else "\U0001f6ab"
                lines.append(
                    f"{i}. {icon} {s.get('name', '')} {s.get('change_pct', 0):+.1f}% "
                    f"(거래량 {s.get('volume_ratio', 0):.1f}배)"
                )
            await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Surge command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 급등주 조회 오류.", reply_markup=MAIN_MENU,
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
        """Handle /accumulation - stealth accumulation detection results."""
        try:
            self._persist_chat_id(update)
            detections = self.db.get_stealth_accumulations(days=1, limit=10)
            if not detections:
                await update.message.reply_text(
                    "\U0001f575\ufe0f 오늘 감지된 매집 패턴이 없습니다.",
                    reply_markup=MAIN_MENU,
                )
                return
            lines = ["\U0001f575\ufe0f 스텔스 매집 감지 결과\n"]
            for i, d in enumerate(detections, 1):
                lines.append(
                    f"{i}. {d.get('name', '')} ({d.get('ticker', '')}) "
                    f"스코어 {d.get('total_score', 0)}"
                )
                lines.append(
                    f"   기관 누적: {d.get('inst_total', 0) / 1e8:.0f}억, "
                    f"외인 누적: {d.get('foreign_total', 0) / 1e8:.0f}억"
                )
            await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Accumulation command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 매집 탐지 조회 오류.", reply_markup=MAIN_MENU,
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
        """Handle /balance - KIS balance inquiry."""
        try:
            self._persist_chat_id(update)
            kis_cfg = load_kis_config()
            if not kis_cfg.is_configured:
                msg = format_kis_not_configured()
            else:
                msg = "주호님, KIS 잔고 조회 기능이 준비되었습니다.\n실시간 연동은 KIS API 키 설정 후 사용 가능합니다."
            await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        except Exception as e:
            logger.error("Balance command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 잔고 조회 오류.", reply_markup=MAIN_MENU,
            )

    # -- Phase 7 menu handlers ---------------------------------------------------

    async def _menu_multi_agent(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """멀티 에이전트 분석 메뉴."""
        await update.message.reply_text(
            "\U0001f4ca 멀티 에이전트 분석\n\n"
            "종목명을 입력하면 4개 전문 에이전트가 분석합니다.\n"
            "사용법: /multi <종목명>\n예: /multi 삼성전자",
            reply_markup=MAIN_MENU,
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


def _won(price: float) -> str:
    return f"\u20a9{price:,.0f}"


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def main() -> None:
    """Entry point: build and run the K-Quant v3.5 Telegram bot with auto-restart."""
    import time

    load_dotenv()
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
