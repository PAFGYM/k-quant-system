"""Telegram bot with multi-strategy system v3.6 — Multi-AI + Real-time + Security."""

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

# v3.6 imports
from kstock.bot.ai_router import AIRouter
from kstock.ingest.kis_websocket import KISWebSocket
from kstock.core.security import startup_security_check, security_audit, mask_key

KST = timezone(timedelta(hours=9))

# Claude Code remote execution prefix
CLAUDE_PREFIX = "클코"

# ── v3.6.2 메인 메뉴 (자주 쓰는 기능 상단 배치) ─────────────────────────────
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📊 분석", "📈 시황"],
        ["💰 잔고", "⭐ 즐겨찾기"],
        ["💻 클로드", "🤖 에이전트"],
        ["💬 AI질문", "📋 리포트"],
        ["⚙️ 더보기"],
    ],
    resize_keyboard=True,
)

# 더보기 서브메뉴 (알림/최적화/관리자 → 하단)
MORE_MENU = ReplyKeyboardMarkup(
    [
        ["📸 계좌분석", "🎯 전략별 보기"],
        ["🔥 급등주", "⚡ 스윙 기회"],
        ["📊 멀티분석", "🕵️ 매집탐지"],
        ["📅 주간 보고서", "📊 공매도"],
        ["🚀 미래기술", "🎯 30억 목표"],
        ["📊 재무 진단", "📡 KIS설정"],
        ["🔔 알림 설정", "⚙️ 최적화"],
        ["💻 클로드", "🛠 관리자"],
        ["🔙 메인으로"],
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



def _won(price: float) -> str:
    return f"\u20a9{price:,.0f}"


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


# Export all names (including _ prefixed helpers) for mixin imports
import sys as _sys_mod
__all__ = [_n for _n in dir(_sys_mod.modules[__name__]) if not _n.startswith('__')]


