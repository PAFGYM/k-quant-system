"""Scheduled jobs and report generators."""
from __future__ import annotations

import asyncio
import time as _time

from kstock.bot.bot_imports import *  # noqa: F403
from kstock.core.market_calendar import is_kr_market_open, market_status_text, next_market_day

# ── 적응형 모니터링: VIX 레짐별 체크 주기 (초) ─────────────────────
ADAPTIVE_INTERVALS = {
    "calm":   {"intraday_monitor": 120, "market_pulse": 180},  # VIX < 18
    "normal": {"intraday_monitor": 60,  "market_pulse": 60},   # VIX 18-25
    "fear":   {"intraday_monitor": 30,  "market_pulse": 30},   # VIX 25-30
    "panic":  {"intraday_monitor": 15,  "market_pulse": 15},   # VIX > 30
}

# 레짐 변경 쿨다운 (초)
_RESCHEDULE_COOLDOWN = 300  # 5분


def _get_vix_regime(vix: float) -> str:
    """VIX 값으로 시장 레짐 산출."""
    if vix >= 30:
        return "panic"
    if vix >= 25:
        return "fear"
    if vix >= 18:
        return "normal"
    return "calm"


class SchedulerMixin:
    # 급등 감지 + 매도 가이드 상태
    _SURGE_COOLDOWN_SEC = 1800
    _SELL_TARGET_COOLDOWN_SEC = 86400  # 24시간 (기존 1시간 → 반복 알림 방지)
    _SURGE_THRESHOLD_PCT = 3.0
    _surge_callback_registered: bool = False

    # ── 동시성 보호 ──────────────────────────────────────
    _state_lock: asyncio.Lock | None = None

    def _ensure_lock(self) -> asyncio.Lock:
        """Lock 초기화 보장 (lazy init)."""
        if self._state_lock is None:
            self._state_lock = asyncio.Lock()
        return self._state_lock

    # ── 경계 모드 (Alert Mode) ─────────────────────────────
    # v6.2.2: 전시/긴장/일상 3단계 — 자동 에스컬레이션 + 디에스컬레이션
    _ALERT_MODES = {
        "normal": {
            "label": "🟢 일상",
            "risk_interval": 120,       # 리스크 모니터 (초)
            "news_interval": 900,       # 뉴스 모니터 (초)
            "global_news_interval": 1800,  # 글로벌 뉴스 수집 (초)
            "surge_threshold": 3.0,     # 급등 감지 %
            "us_futures_interval": 3600,  # 미국 선물 (초)
        },
        "elevated": {
            "label": "🟡 긴장",
            "risk_interval": 60,
            "news_interval": 600,
            "global_news_interval": 900,
            "surge_threshold": 2.0,
            "us_futures_interval": 1800,
        },
        "wartime": {
            "label": "🔴 전시",
            "risk_interval": 30,
            "news_interval": 300,
            "global_news_interval": 300,
            "surge_threshold": 1.5,
            "us_futures_interval": 900,
        },
    }
    # 자동 강등 시간 (설정 후 N시간 무사 경과 시 한 단계 완화)
    _AUTO_DEESCALATE_HOURS = {
        "wartime": 6,    # 전시 → 6시간 무사 → 긴장
        "elevated": 12,  # 긴장 → 12시간 무사 → 일상
    }
    # 뉴스 긴장 키워드 — 일치 시 자동 에스컬레이션
    _ESCALATION_KEYWORDS_WARTIME = [
        "전쟁", "공습", "미사일", "핵", "계엄", "쿠데타",
        "war", "strike", "missile", "nuclear", "martial law",
        "crash", "폭락", "서킷브레이커", "circuit breaker",
        "blockade", "봉쇄", "대공황", "depression",
    ]
    _ESCALATION_KEYWORDS_ELEVATED = [
        "긴급", "위기", "제재", "관세", "무역전쟁",
        "급락", "경기침체", "recession", "crisis",
        "sanction", "tariff", "plunge",
        "금리 인상", "금리 인하", "rate hike", "rate cut",
        "유가 급등", "oil surge", "호르무즈", "hormuz",
        "디폴트", "default", "파산", "bankrupt",
    ]

    def __init_scheduler_state__(self):
        """인스턴스별 mutable 상태 초기화 (class 속성 공유 문제 방지)."""
        if not hasattr(self, '_surge_cooldown'):
            self._surge_cooldown = {}
        if not hasattr(self, '_muted_tickers'):
            self._muted_tickers = {}  # ticker → mute_until (timestamp)
        if not hasattr(self, '_holdings_cache'):
            self._holdings_cache = []
        if not hasattr(self, '_holdings_index'):
            self._holdings_index = {}  # ticker → holding dict (O(1) 조회)
        # 경계 모드 초기화 — DB에서 복원
        if not hasattr(self, '_alert_mode'):
            self._alert_mode = "normal"
            self._alert_mode_since = _time.monotonic()
            self._alert_last_escalation = 0.0
            # DB에서 이전 모드 복원
            try:
                saved = self.db.get_meta("alert_mode")
                if saved and saved in self._ALERT_MODES:
                    self._alert_mode = saved
                    logger.info("Alert mode restored from DB: %s", saved)
            except Exception:
                pass

    # ── 경계 모드 관리 메서드 ──────────────────────────────
    def _get_alert_config(self) -> dict:
        """현재 경계 모드의 설정값 반환."""
        return self._ALERT_MODES.get(self._alert_mode, self._ALERT_MODES["normal"])

    async def set_alert_mode(
        self,
        mode: str,
        context=None,
        reason: str = "",
        notify: bool = True,
    ) -> str:
        """경계 모드 변경 + 스케줄 동적 조정.

        Returns:
            변경 결과 메시지.
        """
        if mode not in self._ALERT_MODES:
            return f"⚠️ 알 수 없는 모드: {mode} (normal/elevated/wartime)"

        prev = self._alert_mode
        if prev == mode:
            cfg = self._ALERT_MODES[mode]
            return f"{cfg['label']} 이미 {cfg['label']} 모드입니다"

        async with self._ensure_lock():
            self._alert_mode = mode
            self._alert_mode_since = _time.monotonic()
            self._SURGE_THRESHOLD_PCT = self._ALERT_MODES[mode]["surge_threshold"]
        cfg = self._ALERT_MODES[mode]

        # DB 저장 (재시작 후 복원)
        try:
            self.db.set_meta("alert_mode", mode)
        except Exception:
            pass

        # 스케줄 동적 재조정
        if context:
            await self._reschedule_for_alert_mode(context, cfg)

        prev_cfg = self._ALERT_MODES[prev]
        reason_str = f"\n이유: {reason}" if reason else ""
        msg = (
            f"🚨 경계 모드 변경\n"
            f"{'━' * 20}\n"
            f"{prev_cfg['label']} → {cfg['label']}\n"
            f"{reason_str}\n\n"
            f"📊 리스크 모니터: {cfg['risk_interval']}초\n"
            f"📰 뉴스 모니터: {cfg['news_interval'] // 60}분\n"
            f"🌍 글로벌 뉴스: {cfg['global_news_interval'] // 60}분\n"
            f"⚡ 급등 감지: {cfg['surge_threshold']}%\n"
            f"🇺🇸 미국 선물: {cfg['us_futures_interval'] // 60}분"
        )

        # 전시 모드 진입 시 전략 조정 메시지 추가
        wartime_strategy_msg = ""
        if mode == "wartime":
            try:
                from kstock.core.risk_policy import wartime_adjustments
                wt = wartime_adjustments()
                wartime_strategy_msg = (
                    f"\n\n🛡️ 전시 전략 조정 발동\n"
                    f"{'━' * 20}\n"
                    f"🔴 손절 강화: -7% → {wt.stop_loss_pct * 100:.0f}%\n"
                    f"📉 포지션 축소: 평시 대비 {wt.max_position_ratio * 100:.0f}%\n"
                    f"💵 최대 노출: {wt.max_portfolio_exposure * 100:.0f}% (현금 {(1 - wt.max_portfolio_exposure) * 100:.0f}% 확보)\n"
                    f"🚫 매수 제한: 신뢰도 {wt.min_buy_confidence * 100:.0f}% 이상만 허용\n"
                    f"🛡️ 방어 섹터 선호: {', '.join(wt.defensive_sectors)}\n"
                    f"⚠️ 축소 대상: {', '.join(wt.cyclical_sectors)}"
                )
                msg += wartime_strategy_msg
            except Exception:
                logger.debug("Wartime strategy message generation failed", exc_info=True)

        # 텔레그램 알림
        if notify and self.chat_id and context:
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                buttons = []
                if mode != "normal":
                    buttons.append([InlineKeyboardButton(
                        "🟢 일상으로 복귀", callback_data="adm:alert:normal",
                    )])
                if mode == "normal":
                    buttons.append([InlineKeyboardButton(
                        "🟡 긴장 모드", callback_data="adm:alert:elevated",
                    )])
                buttons.append([InlineKeyboardButton(
                    "❌ 닫기", callback_data="dismiss:0",
                )])
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=msg,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception as e:
                logger.debug("Alert mode notification failed: %s", e)

            # 전시 모드 진입 시 보유종목 점검 실행
            if mode == "wartime":
                try:
                    wartime_report = await self._wartime_holdings_check()
                    if wartime_report:
                        await context.bot.send_message(
                            chat_id=self.chat_id,
                            text=wartime_report,
                        )
                except Exception:
                    logger.debug("Wartime holdings check failed", exc_info=True)

        logger.info(
            "Alert mode changed: %s → %s (reason: %s)",
            prev, mode, reason or "manual",
        )
        return msg

    async def _wartime_holdings_check(self) -> str | None:
        """전시 모드 진입 시 보유종목 점검.

        모든 보유종목을 분석하여:
        - 경기민감(고베타) 종목: 축소/매도 권장
        - 방어(저베타) 종목: 보유 유지 권장
        - 전시 손절가 재산정 (-5%)
        """
        try:
            from kstock.core.risk_policy import (
                wartime_adjustments,
                is_sector_defensive,
                is_sector_cyclical,
            )
            from kstock.core.risk_manager import SECTOR_MAP

            holdings = self.db.get_active_holdings()
            if not holdings:
                return None

            wt = wartime_adjustments()

            reduce_lines: list[str] = []   # 축소/매도 권장
            hold_lines: list[str] = []     # 보유 유지 권장
            stop_lines: list[str] = []     # 새 손절가

            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                bp = h.get("buy_price", 0)
                qty = h.get("quantity", 0)
                sector = SECTOR_MAP.get(ticker, "기타")

                if bp <= 0 or qty <= 0:
                    continue

                # 현재가 조회
                try:
                    detail = await self._get_price_detail(ticker, bp)
                    cur = detail["price"]
                except Exception:
                    cur = bp

                pnl_pct = (cur - bp) / bp if bp > 0 else 0

                # 전시 손절가 (-5%)
                new_stop = round(cur * (1 + wt.stop_loss_pct))
                pnl_str = f"{pnl_pct * 100:+.1f}%"
                eval_amount = cur * qty

                if is_sector_cyclical(sector):
                    # 경기민감 → 축소/매도 권장
                    action = "축소 권장" if pnl_pct >= 0 else "매도 검토"
                    reduce_lines.append(
                        f"  🔴 {name} ({sector})\n"
                        f"     {bp:,.0f}→{cur:,.0f}원 ({pnl_str})\n"
                        f"     평가금 {eval_amount:,.0f}원 | {action}"
                    )
                elif is_sector_defensive(sector):
                    # 방어 섹터 → 보유 유지
                    hold_lines.append(
                        f"  🟢 {name} ({sector})\n"
                        f"     {bp:,.0f}→{cur:,.0f}원 ({pnl_str})\n"
                        f"     평가금 {eval_amount:,.0f}원 | 보유 유지"
                    )
                else:
                    # 기타 섹터 → 손실 중이면 축소, 아니면 관망
                    if pnl_pct < -0.03:
                        reduce_lines.append(
                            f"  🟡 {name} ({sector})\n"
                            f"     {bp:,.0f}→{cur:,.0f}원 ({pnl_str})\n"
                            f"     평가금 {eval_amount:,.0f}원 | 손실 축소 검토"
                        )
                    else:
                        hold_lines.append(
                            f"  ⚪ {name} ({sector})\n"
                            f"     {bp:,.0f}→{cur:,.0f}원 ({pnl_str})\n"
                            f"     평가금 {eval_amount:,.0f}원 | 관망"
                        )

                # 전시 손절가
                stop_lines.append(
                    f"  {name}: {new_stop:,.0f}원 (현재가 대비 {wt.stop_loss_pct * 100:.0f}%)"
                )

            # 리포트 조립
            lines = [
                "🛡️ 전시 보유종목 긴급 점검",
                "━" * 22,
            ]

            if reduce_lines:
                lines.append("\n📉 축소/매도 검토 (경기민감·고베타)")
                lines.extend(reduce_lines)

            if hold_lines:
                lines.append("\n📊 보유 유지 (방어·저베타)")
                lines.extend(hold_lines)

            if stop_lines:
                lines.append(f"\n🔴 전시 손절가 (기준: {wt.stop_loss_pct * 100:.0f}%)")
                lines.extend(stop_lines)

            lines.extend([
                "",
                "━" * 22,
                "💡 전시 모드에서는 현금 비중 확대와",
                "   방어 섹터 중심 운용을 권장합니다.",
            ])

            return "\n".join(lines)

        except Exception:
            logger.exception("Wartime holdings check failed")
            return None

    async def _reschedule_for_alert_mode(self, context, cfg: dict) -> None:
        """경계 모드에 따라 반복 스케줄 동적 재조정."""
        jq = getattr(self, "_job_queue", None)
        if jq is None:
            jq = context.application.job_queue
        if jq is None:
            return

        # 재조정할 잡 목록: (잡 이름, 새 간격, 핸들러)
        reschedule_map = {
            "risk_monitor": (cfg["risk_interval"], self.job_risk_monitor),
            "news_monitor": (cfg["news_interval"], self.job_news_monitor),
            "global_news_collect": (cfg["global_news_interval"], self.job_global_news_collect),
            "us_futures_signal": (cfg["us_futures_interval"], self.job_us_futures_signal),
        }

        current_jobs = jq.jobs()
        for job_name, (new_interval, handler) in reschedule_map.items():
            # 기존 잡 제거
            for job in current_jobs:
                if job.name == job_name:
                    job.schedule_removal()
            # 새 간격으로 재등록
            jq.run_repeating(
                handler,
                interval=new_interval,
                first=10,
                name=job_name,
            )
        logger.info(
            "Rescheduled jobs for %s mode: risk=%ds, news=%ds, global=%ds, us=%ds",
            self._alert_mode,
            cfg["risk_interval"], cfg["news_interval"],
            cfg["global_news_interval"], cfg["us_futures_interval"],
        )

    async def _check_news_escalation(self, items) -> None:
        """뉴스 헤드라인으로 경계 모드 자동 에스컬레이션 판단."""
        if not items:
            return

        now = _time.monotonic()
        # 쿨다운: 마지막 에스컬레이션 후 30분 내 재에스컬레이션 방지
        if now - self._alert_last_escalation < 1800:
            return

        titles = " ".join(item.title.lower() for item in items)

        # 전시 키워드 체크
        wartime_hits = sum(
            1 for kw in self._ESCALATION_KEYWORDS_WARTIME
            if kw.lower() in titles
        )
        # 긴장 키워드 체크
        elevated_hits = sum(
            1 for kw in self._ESCALATION_KEYWORDS_ELEVATED
            if kw.lower() in titles
        )

        new_mode = None
        reason = ""

        if wartime_hits >= 2 and self._alert_mode != "wartime":
            new_mode = "wartime"
            reason = f"전시 키워드 {wartime_hits}개 감지"
        elif (wartime_hits >= 1 or elevated_hits >= 2) and self._alert_mode == "normal":
            new_mode = "elevated"
            reason = f"긴장 키워드 감지 (전시:{wartime_hits}, 긴장:{elevated_hits})"

        if new_mode:
            self._alert_last_escalation = now
            # context가 없으므로 저장만 하고 알림은 글로벌 뉴스 잡에서 처리
            self._pending_escalation = (new_mode, reason)

    async def _check_auto_deescalation(self, context) -> None:
        """시간 경과에 따른 자동 경계 완화."""
        if self._alert_mode == "normal":
            return

        hours_limit = self._AUTO_DEESCALATE_HOURS.get(self._alert_mode)
        if not hours_limit:
            return

        elapsed_hours = (_time.monotonic() - self._alert_mode_since) / 3600
        if elapsed_hours < hours_limit:
            return

        # 한 단계 완화
        if self._alert_mode == "wartime":
            new_mode = "elevated"
            reason = f"전시 모드 {hours_limit}시간 경과, 자동 완화"
        else:
            new_mode = "normal"
            reason = f"긴장 모드 {hours_limit}시간 경과, 자동 완화"

        await self.set_alert_mode(new_mode, context=context, reason=reason)

    def get_alert_mode_status(self) -> str:
        """현재 경계 모드 상태 텍스트."""
        cfg = self._get_alert_config()
        elapsed = (_time.monotonic() - self._alert_mode_since) / 3600
        deesc = self._AUTO_DEESCALATE_HOURS.get(self._alert_mode)
        deesc_str = ""
        if deesc:
            remaining = max(0, deesc - elapsed)
            deesc_str = f"\n⏱ 자동 완화까지: {remaining:.1f}시간"

        return (
            f"경계 모드: {cfg['label']}\n"
            f"유지 시간: {elapsed:.1f}시간{deesc_str}\n\n"
            f"📊 리스크 모니터: {cfg['risk_interval']}초\n"
            f"📰 뉴스 모니터: {cfg['news_interval'] // 60}분\n"
            f"🌍 글로벌 뉴스: {cfg['global_news_interval'] // 60}분\n"
            f"⚡ 급등 감지: {cfg['surge_threshold']}%\n"
            f"🇺🇸 미국 선물: {cfg['us_futures_interval'] // 60}분"
        )

    async def job_premarket_buy_planner(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매일 07:50 장 시작 전 매수 플래너 질문."""
        if not self.chat_id:
            return
        if not is_kr_market_open():
            return

        # v5.2: 매수 의향 + 금액/타입 안내 개선
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💰 매수 계획 있음", callback_data="bp:yes",
                ),
                InlineKeyboardButton(
                    "🏖️ 오늘은 관망", callback_data="bp:no",
                ),
            ],
        ])
        await context.bot.send_message(
            chat_id=self.chat_id,
            text=(
                "☀️ 주호님, 좋은 아침입니다\n\n"
                "오늘 추가 매수 계획이 있으신가요?\n\n"
                "매수 계획 있음을 누르면\n"
                "금액 → 투자 타입 선택 후\n"
                "전담 매니저가 종목을 추천합니다."
            ),
            reply_markup=keyboard,
        )
        self.db.upsert_job_run("premarket_buy_planner", _today(), status="success")
        logger.info("Premarket buy planner sent")

    async def job_morning_briefing(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """07:30 아침 브리핑.

        v5.9: 휴장일이면 간소화 브리핑 (미국 요약 + 다음 개장일),
              개장일이면 신호등 포함 전체 브리핑.
        """
        if not self.chat_id:
            return
        try:
            today = datetime.now(KST).date()
            market_open = is_kr_market_open(today)

            macro = await self.macro_client.get_snapshot()
            signal_emoji, signal_label = self._market_signal(macro)

            if not market_open:
                # 휴장일: 간소화 브리핑 — 미국 요약 + 다음 개장일 안내만
                spx_e = "📈" if macro.spx_change_pct > 0 else "📉"
                ndx_e = "📈" if macro.nasdaq_change_pct > 0 else "📉"
                nxt = next_market_day(today)
                msg = (
                    f"☀️ 오전 브리핑\n"
                    f"{'━' * 22}\n"
                    f"{market_status_text(today)}\n"
                    f"📅 다음 개장일: {nxt.strftime('%m/%d(%a)')}\n\n"
                    f"🇺🇸 미국 시장 마감 요약\n"
                    f"{spx_e} S&P500: {macro.spx_change_pct:+.2f}%\n"
                    f"{ndx_e} 나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                    f"💰 VIX: {macro.vix:.1f}\n"
                    f"💱 환율: {macro.usdkrw:,.0f}원\n\n"
                    f"다음 개장일 전망: {signal_emoji} {signal_label}\n"
                    f"{'━' * 22}\n"
                    f"🤖 K-Quant | 휴장일 간소 브리핑"
                )
                await context.bot.send_message(chat_id=self.chat_id, text=msg)
                self.db.upsert_job_run("morning_briefing", _today(), status="success")
                logger.info("Morning briefing sent (market closed)")
                return

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
                # 신호등을 AI 브리핑 앞에 추가
                signal_line = f"오늘 국내 시장 전망: {signal_emoji} {signal_label}"
                msg = format_claude_briefing(f"{signal_line}\n{'━' * 22}\n{briefing_text}")
            else:
                msg = (
                    f"☀️ 오전 브리핑\n"
                    f"오늘 국내 시장 전망: {signal_emoji} {signal_label}\n\n"
                    + format_market_status(
                        macro, regime_mode,
                        alert_mode=getattr(self, '_alert_mode', 'normal'),
                    )
                )

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            morning_buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 차트", callback_data="vchart:menu"),
                    InlineKeyboardButton("🔬 섹터", callback_data="sdive:menu"),
                    InlineKeyboardButton("💼 잔고", callback_data="bal:0"),
                ],
                [
                    InlineKeyboardButton("👍", callback_data="fb:like:모닝브리핑"),
                    InlineKeyboardButton("👎", callback_data="fb:dislike:모닝브리핑"),
                ],
            ])
            await context.bot.send_message(
                chat_id=self.chat_id, text=msg,
                reply_markup=morning_buttons,
            )

            # v9.5.1: 브리핑을 DB에 저장 (AI 채팅이 참조할 수 있도록)
            try:
                self.db.save_briefing("morning", msg[:4000])
            except Exception:
                logger.debug("Failed to save morning briefing to DB", exc_info=True)

            # v9.5: 통합 상태 헤더 발송 (매니저 브리핑 전 전체 상황 요약)
            try:
                from kstock.bot.unified_state import build_unified_state, format_unified_header
                unified = await build_unified_state(self.db, macro_client=self.macro_client)
                header = format_unified_header(unified)
                if header and len(header) > 20:
                    await context.bot.send_message(chat_id=self.chat_id, text=header)
            except Exception:
                logger.debug("Unified state header failed", exc_info=True)

            # v3.9: 매니저별 보유종목 분석 (holding_type별 그룹핑)
            await self._send_manager_briefings(context, macro)

            # v8.6: 매니저 관심종목 매수 스캔
            await self._send_manager_watchlist_scan(context, macro)

            # v8.5: 오늘의 할 일 (Daily Action Planner)
            await self._send_daily_actions(context, macro)

            self.db.upsert_job_run("morning_briefing", _today(), status="success")
            logger.info("Morning briefing sent")
        except Exception as e:
            logger.error("Morning briefing failed: %s", e)
            self.db.upsert_job_run("morning_briefing", _today(), status="error", message=str(e))

    async def _send_manager_briefings(self, context, macro) -> None:
        """매니저별 보유종목 분석 메시지 발송 (보유종목 있는 매니저만).

        v9.5: shared_context로 YouTube 인텔리전스 + 매니저 크로스 컨텍스트 주입.
        """
        try:
            from collections import defaultdict
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from kstock.bot.investment_managers import get_manager_analysis, MANAGERS

            holdings = self.db.get_active_holdings()
            if not holdings:
                return

            # v9.5: 공유 컨텍스트 구축 (YouTube 인텔 + 뉴스 + 교훈 등)
            shared_ctx = None
            try:
                from kstock.bot.context_builder import build_manager_shared_context
                shared_ctx = await build_manager_shared_context(
                    self.db, macro_client=self.macro_client,
                )
            except Exception:
                logger.debug("Shared context build failed", exc_info=True)

            # v9.4: AI 토론 합의 요약 먼저 발송
            try:
                recent_debates = self.db.get_all_recent_debates(hours=24)
                if recent_debates:
                    _v_emoji = {
                        "STRONG_BUY": "\U0001f7e2\U0001f7e2", "BUY": "\U0001f7e2",
                        "HOLD": "\U0001f7e1", "SELL": "\U0001f534",
                        "STRONG_SELL": "\U0001f534\U0001f534",
                    }
                    debate_lines = ["\U0001f399 AI 토론 합의 요약", "\u2501" * 20]
                    for d in recent_debates[:10]:
                        v = d.get("verdict", "")
                        ve = _v_emoji.get(v, "\u2753")
                        cons = d.get("consensus_level", "")
                        conf = d.get("confidence", 0)
                        dname = d.get("name", d.get("ticker", ""))
                        pt = d.get("price_target", 0)
                        line = f"{dname}: {ve}{v} ({cons} {conf:.0f}%)"
                        if pt and pt > 0:
                            line += f" 목표 {pt:,.0f}원"
                        debate_lines.append(line)
                    await context.bot.send_message(
                        chat_id=self.chat_id,
                        text="\n".join(debate_lines),
                    )
            except Exception:
                logger.debug("AI debate summary in briefing failed", exc_info=True)

            # holding_type별 그룹핑
            by_type = defaultdict(list)
            for h in holdings:
                ht = h.get("holding_type", "auto")
                if ht == "auto":
                    ht = "swing"  # auto는 스윙으로 기본 배정
                by_type[ht].append(h)

            market_text = (
                f"VIX={macro.vix:.1f}, S&P={macro.spx_change_pct:+.2f}%, "
                f"나스닥={macro.nasdaq_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원, 레짐={macro.regime}"
            )

            current_alert = getattr(self, '_alert_mode', 'normal')
            for mtype, mholdings in by_type.items():
                if mtype not in MANAGERS or not mholdings:
                    continue

                # scalp/swing: 차트 데이터 보강
                if mtype in ("scalp", "swing"):
                    try:
                        from kstock.features.technical import compute_indicators
                        from kstock.bot.investment_managers import build_chart_summary
                        for h in mholdings:
                            ticker = h.get("ticker", "")
                            if not ticker:
                                continue
                            try:
                                market = "KOSPI"
                                for s in self.all_tickers:
                                    if s["code"] == ticker:
                                        market = s.get("market", "KOSPI")
                                        break
                                ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="3mo")
                                if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 20:
                                    tech = compute_indicators(ohlcv)
                                    cp = float(h.get("current_price") or 0)
                                    supply = self.db.get_supply_demand(ticker, days=5)
                                    h["chart_summary"] = build_chart_summary(
                                        tech, cp, supply,
                                        ohlcv=ohlcv, ticker=ticker,
                                        name=h.get("name", ""),
                                    )
                            except Exception:
                                logger.debug("Chart enrich %s failed", ticker, exc_info=True)
                    except ImportError:
                        pass

                # position/long_term: 재무 데이터 보강
                if mtype in ("position", "long_term"):
                    try:
                        from kstock.bot.investment_managers import build_fundamental_summary
                        for h in mholdings:
                            ticker = h.get("ticker", "")
                            if not ticker:
                                continue
                            try:
                                info = await self.data_router.get_stock_info(
                                    ticker, h.get("name", ""),
                                ) if hasattr(self, "data_router") else None
                                financials = self.db.get_financials(ticker)
                                consensus = self.db.get_consensus(ticker)
                                supply = self.db.get_supply_demand(ticker, days=5)
                                cp = float(h.get("current_price") or 0)
                                summary = build_fundamental_summary(
                                    info, financials, consensus, supply,
                                    current_price=cp, ticker=ticker,
                                    name=h.get("name", ""),
                                )
                                if summary:
                                    h["fundamental_summary"] = summary
                            except Exception:
                                logger.debug("Fundamental enrich %s failed", ticker, exc_info=True)
                    except ImportError:
                        pass

                # 매니저 성과 + 교훈 주입
                mgr_perf = None
                mgr_lessons = None
                try:
                    mgr_perf = self.db.get_manager_performance(mtype, days=90)
                    mgr_lessons = self.db.get_trade_lessons_by_manager(mtype, limit=5)
                except Exception:
                    pass

                try:
                    report = await get_manager_analysis(
                        mtype, mholdings, market_text,
                        shared_context=shared_ctx,
                        alert_mode=current_alert,
                        performance=mgr_perf,
                        manager_lessons=mgr_lessons,
                    )
                    if report:
                        # v9.6.0: 정확도 배지 프리픽스
                        try:
                            with self.db._connect() as _sc_conn:
                                _sc_row = _sc_conn.execute(
                                    "SELECT hit_rate, avg_return_5d, weight_adj "
                                    "FROM manager_scorecard WHERE manager_key=? "
                                    "ORDER BY calculated_at DESC LIMIT 1",
                                    (mtype,),
                                ).fetchone()
                            if _sc_row and _sc_row["hit_rate"] > 0:
                                _hr = _sc_row["hit_rate"]
                                _ar = _sc_row["avg_return_5d"] * 100
                                _badge_e = "🟢" if _hr >= 60 else ("🟡" if _hr >= 45 else "🔴")
                                _badge = f"{_badge_e} 적중률 {_hr:.0f}% | 5일 평균 {_ar:+.1f}%"
                                report = report.replace("\n", f"\n{_badge}\n", 1)
                        except Exception:
                            pass
                        _mgr_btns = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("👍", callback_data=f"fb:like:매니저_{mtype}"),
                                InlineKeyboardButton("👎", callback_data=f"fb:dislike:매니저_{mtype}"),
                            ],
                        ])
                        await context.bot.send_message(
                            chat_id=self.chat_id, text=report[:4000],
                            reply_markup=_mgr_btns,
                        )
                        # v9.5: 매니저 stance를 DB에 저장 (통합 상태용)
                        try:
                            stance_line = report.split("\n")[0][:120] if report else ""
                            # 첫 줄이 구분선이면 두 번째 줄 사용
                            for line in report.split("\n"):
                                line = line.strip()
                                if line and "━" not in line and len(line) > 5:
                                    stance_line = line[:120]
                                    break
                            self.db.save_manager_stance(mtype, stance_line)
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("Manager briefing %s error: %s", mtype, e)

            # 포트폴리오 밸런스 조언
            try:
                from kstock.bot.investment_managers import analyze_portfolio_balance
                balance_msg = analyze_portfolio_balance(by_type)
                if balance_msg:
                    await context.bot.send_message(
                        chat_id=self.chat_id, text=balance_msg[:2000],
                    )
            except Exception:
                pass

            logger.info("Manager briefings sent: %s", list(by_type.keys()))
        except Exception as e:
            logger.debug("Manager briefings error: %s", e)

    # ── v8.6: 자동분류 + 매니저 매수 스캔 ────────────────────────

    async def job_auto_classify(self, context) -> None:
        """미분류 종목 자동 분류 (시작 시 + 매일 06:30)."""
        try:
            classified = await self._auto_classify_unassigned(limit=75)
            if classified > 0:
                logger.info("Auto-classify: %d stocks classified", classified)
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"🤖 자동분류 완료: {classified}종목 분류됨",
                )
            else:
                logger.info("Auto-classify: no unclassified stocks")
        except Exception as e:
            logger.error("job_auto_classify failed: %s", e)

    async def _send_manager_watchlist_scan(self, context, macro) -> None:
        """매니저별 관심종목 매수 스캔 — 기술적 데이터 보강 후 AI 분석."""
        try:
            from collections import defaultdict
            from kstock.bot.investment_managers import (
                scan_manager_domain, MANAGERS, compute_recovery_score,
            )

            watchlist = self.db.get_watchlist()
            if not watchlist:
                return

            # 보유종목 티커 set
            holdings = self.db.get_active_holdings()
            held_tickers = {h["ticker"] for h in holdings}

            market_text = (
                f"VIX={macro.vix:.1f}, S&P={macro.spx_change_pct:+.2f}%, "
                f"나스닥={macro.nasdaq_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원, 레짐={macro.regime}"
            )

            # 관심종목을 매니저별 그룹핑 (보유 종목 제외)
            by_manager = defaultdict(list)
            for w in watchlist:
                hz = w.get("horizon", "")
                if not hz or w["ticker"] in held_tickers:
                    continue
                if hz not in MANAGERS:
                    continue

                # ── 기술적 데이터 보강 (가격 + RSI + BB + MACD + 거래량) ──
                try:
                    # 가격 상세 (등락률 포함)
                    detail = await self._get_price_detail(w["ticker"], 0)
                    w["price"] = detail.get("price", 0)
                    w["day_change"] = detail.get("day_change_pct", 0)

                    # OHLCV → 기술적 지표 계산
                    market = "KOSPI"
                    for s in self.all_tickers:
                        if s["code"] == w["ticker"]:
                            market = s.get("market", "KOSPI")
                            break
                    ohlcv = await self.yf_client.get_ohlcv(
                        w["ticker"], market, period="3mo",
                    )
                    if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 20:
                        from kstock.features.technical import compute_indicators
                        tech = compute_indicators(ohlcv)
                        w["rsi"] = tech.rsi
                        w["bb_pctb"] = tech.bb_pctb
                        w["macd_cross"] = tech.macd_signal_cross
                        w["vol_ratio"] = tech.volume_ratio * 100  # %로 변환
                        # 52주 고점 대비 하락폭
                        if tech.high_52w > 0 and w["price"] > 0:
                            w["drop_from_high"] = (
                                (w["price"] - tech.high_52w) / tech.high_52w
                            ) * 100
                        else:
                            w["drop_from_high"] = 0
                        # 회복 탄력성 점수
                        w["recovery_score"] = compute_recovery_score(
                            tech, w.get("day_change", 0),
                        )
                except Exception as e:
                    logger.debug("Watchlist enrich %s: %s", w.get("ticker"), e)
                    if "price" not in w:
                        w["price"] = 0

                by_manager[hz].append(w)

            current_alert = getattr(self, '_alert_mode', 'normal')
            scanned = 0
            for mgr_key, stocks in by_manager.items():
                if not stocks:
                    continue
                try:
                    report = await scan_manager_domain(
                        mgr_key, stocks, market_text,
                        alert_mode=current_alert,
                    )
                    if report and "매수 타이밍 종목 없음" not in report:
                        await context.bot.send_message(
                            chat_id=self.chat_id, text=report[:4000],
                        )
                        scanned += 1
                except Exception as e:
                    logger.debug("Manager scan %s error: %s", mgr_key, e)

            logger.info("Manager watchlist scan: %d managers had picks", scanned)
        except Exception as e:
            logger.debug("Manager watchlist scan error: %s", e)

    async def _generate_daily_actions(self, macro) -> list[dict]:
        """보유종목 + 시장 상황 기반 오늘의 할 일 생성."""
        actions = []
        holdings = self.db.get_active_holdings()
        alert_mode = getattr(self, '_alert_mode', 'normal')

        for h in holdings:
            ticker = h.get("ticker", "")
            name = (h.get("name", "") or ticker)[:8]
            buy_price = float(h.get("buy_price", 0) or 0)
            if buy_price <= 0:
                continue

            cur = float(h.get("current_price", 0) or 0)
            try:
                cur = (await self._get_price(ticker, base_price=buy_price)) or cur
            except Exception:
                pass
            if cur <= 0:
                continue

            pnl = (cur - buy_price) / buy_price * 100
            stop = float(h.get("stop_price", buy_price * 0.95) or buy_price * 0.95)
            target = float(h.get("target_1", buy_price * 1.03) or buy_price * 1.03)
            ht = h.get("holding_type", "swing")
            if ht == "auto":
                ht = "swing"

            # 매니저별 기준 적용
            from kstock.bot.investment_managers import MANAGER_THRESHOLDS
            mgr_th = MANAGER_THRESHOLDS.get(ht, MANAGER_THRESHOLDS["swing"])
            mgr_stop = mgr_th["stop_loss"]
            mgr_tp1 = mgr_th["take_profit_1"]

            # 긴급: 매니저별 손절 기준 도달
            wartime_stop = mgr_stop * 0.6 if alert_mode == "wartime" else mgr_stop
            if cur <= stop or pnl <= wartime_stop:
                reason = f"{pnl:+.1f}% (매니저 손절 {wartime_stop:.0f}%)"
                if alert_mode == "wartime":
                    reason += " 🔴전시"
                actions.append({
                    "priority": "urgent", "ticker": ticker, "name": name,
                    "action": "손절 필요",
                    "reason": reason,
                    "callback_data": f"detail:{ticker}",
                })
            # 주의: 매니저별 1차 익절 기준 60% 도달
            elif pnl >= mgr_tp1 * 0.6 and cur >= target * 0.98:
                actions.append({
                    "priority": "caution", "ticker": ticker, "name": name,
                    "action": "1차 익절 검토",
                    "reason": f"+{pnl:.1f}% 수익, 목표가 근접",
                    "callback_data": f"detail:{ticker}",
                })
            # 주의: 단타 보유일 초과
            elif ht == "scalp" and pnl < 3:
                actions.append({
                    "priority": "caution", "ticker": ticker, "name": name,
                    "action": "단타 청산 검토",
                    "reason": f"단타 종목 {pnl:+.1f}%",
                    "callback_data": f"detail:{ticker}",
                })
            # 주의: 큰 변동
            elif abs(pnl) >= 5:
                p = "caution"
                act = "큰 수익 관리" if pnl > 0 else "큰 손실 점검"
                actions.append({
                    "priority": p, "ticker": ticker, "name": name,
                    "action": act,
                    "reason": f"{pnl:+.1f}%",
                    "callback_data": f"detail:{ticker}",
                })

        # 기회: 시장 레짐
        try:
            regime = detect_regime(macro)
            if regime.mode in ("risk_on", "bubble_attack"):
                actions.append({
                    "priority": "opportunity", "ticker": "", "name": "시장 레짐",
                    "action": "공격 투자 환경",
                    "reason": f"{regime.emoji} {regime.label} — 신규 매수 기회",
                    "callback_data": "fav:tab::0",
                })
        except Exception:
            pass

        # 확인: 포트폴리오 점검
        if len(holdings) >= 2:
            actions.append({
                "priority": "check", "ticker": "", "name": "포트폴리오",
                "action": "비중 점검",
                "reason": f"보유 {len(holdings)}종목 밸런스 확인",
                "callback_data": "fav:tab:holding:0",
            })

        # 정렬
        order = {"urgent": 0, "caution": 1, "opportunity": 2, "check": 3}
        actions.sort(key=lambda a: order.get(a.get("priority", ""), 4))
        return actions

    async def _send_daily_actions(self, context, macro) -> None:
        """오늘의 할 일 메시지 전송."""
        try:
            actions = await self._generate_daily_actions(macro)
            alert_mode = getattr(self, '_alert_mode', 'normal')
            text = format_daily_actions(actions, alert_mode=alert_mode)

            buttons = []
            for a in actions[:6]:
                cb = a.get("callback_data", "")
                if cb:
                    emoji = {"urgent": "\U0001f534", "caution": "\U0001f7e1",
                             "opportunity": "\U0001f7e2", "check": "\u26aa"
                             }.get(a["priority"], "")
                    label = f"{emoji} {a['name']}: {a['action']}"[:40]
                    buttons.append([InlineKeyboardButton(label, callback_data=cb)])
            buttons.append(make_feedback_row("daily_actions"))

            await context.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            logger.info("Daily actions sent: %d items", len(actions))
        except Exception as e:
            logger.debug("Daily actions failed: %s", e)

    async def _generate_morning_briefing_v2(
        self, macro: MacroSnapshot, regime_mode: dict
    ) -> str | None:
        """보유종목별 투자 기간(단기/중기/장기)에 따른 보유/매도 판단 포함 브리핑."""
        if not self.anthropic_key:
            return None
        try:
            import httpx

            # 보유종목 정보 수집 — v5.5: 실시간 가격 조회
            holdings = self.db.get_active_holdings()
            holdings_text = ""
            if holdings:
                for h in holdings:
                    ticker = h.get("ticker", "")
                    name = h.get("name", ticker)
                    buy_price = h.get("buy_price", 0)
                    horizon = h.get("horizon", "swing")
                    qty = h.get("quantity", 0)
                    # v5.5: KIS→Naver→yfinance 순 실시간 가격 조회
                    # v9.3: stale 가격 감지 — base_price=0으로 시도, 실패 시 DB fallback
                    current_price = 0
                    price_stale = False
                    try:
                        current_price = await self._get_price(ticker, base_price=0)
                        if current_price <= 0:
                            current_price = h.get("current_price", 0) or buy_price
                            price_stale = True
                    except Exception:
                        logger.debug("job_morning_briefing get_price failed for %s", ticker, exc_info=True)
                        current_price = h.get("current_price", 0) or buy_price
                        price_stale = True
                    pnl_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 and current_price > 0 else 0
                    stale_mark = " ⚠️전일" if price_stale else ""
                    holdings_text += (
                        f"  {name}({ticker}): "
                        f"매수가 {buy_price:,.0f}원, 현재가 {current_price:,.0f}원{stale_mark}, "
                        f"수익률 {pnl_pct:+.1f}%, 수량 {qty}주, "
                        f"투자시계 {horizon}\n"
                    )
            else:
                holdings_text = "  보유종목 없음\n"

            # v6.1: 글로벌 뉴스 컨텍스트 추가
            news_ctx = ""
            try:
                news_items = self.db.get_recent_global_news(limit=8, hours=12)
                if news_items:
                    news_lines = []
                    for n in news_items:
                        urgency = "🚨" if n.get("is_urgent") else "📰"
                        news_lines.append(f"  {urgency} {n.get('title', '')}")
                        # v8.2: YouTube 영상 내용 요약 포함
                        summary = n.get("content_summary", "")
                        if summary:
                            news_lines.append(f"    📝 {summary[:150]}")
                    news_ctx = "\n[글로벌 뉴스 헤드라인]\n" + "\n".join(news_lines) + "\n"
            except Exception:
                logger.debug("job_morning_briefing global news fetch failed", exc_info=True)

            # v9.5: YouTube 인텔리전스 컨텍스트
            yt_intel_ctx = ""
            try:
                yt_intel = self.db.get_recent_youtube_intelligence(hours=12, limit=5)
                if yt_intel:
                    yt_lines = []
                    for yi in yt_intel:
                        src = yi.get("source", "")
                        summary = yi.get("full_summary", "")[:200]
                        outlook = yi.get("market_outlook", "")
                        tickers = yi.get("mentioned_tickers", [])
                        ticker_str = ", ".join(
                            f"{t.get('name', '')}({t.get('sentiment', '')})"
                            for t in (tickers[:5] if isinstance(tickers, list) else [])
                        )
                        yt_lines.append(f"  [{src}] {summary}")
                        if outlook:
                            yt_lines.append(f"    전망: {outlook}")
                        if ticker_str:
                            yt_lines.append(f"    언급종목: {ticker_str}")
                    yt_intel_ctx = "\n[🎬 YouTube 방송 인사이트]\n" + "\n".join(yt_lines) + "\n"
            except Exception:
                logger.debug("YouTube intelligence context failed", exc_info=True)

            # v6.2.1: 운영자 특별 지시사항 (DB에서 로드, 없으면 빈 문자열)
            special_ctx = ""
            try:
                directive = self.db.get_meta("market_special_context")
                if directive:
                    special_ctx = f"\n[📌 운영자 특별 관심사항]\n{directive}\n"
            except Exception:
                pass

            # v6.2.2: 보유종목 외인/기관 수급 데이터
            flow_ctx = ""
            try:
                if holdings:
                    flow_lines = []
                    for h in holdings[:8]:
                        ticker = h.get("ticker", "")
                        name = h.get("name", ticker)
                        try:
                            frgn = await self.kis.get_foreign_flow(ticker, days=3)
                            inst = await self.kis.get_institution_flow(ticker, days=3)
                            f_net = int(frgn["net_buy_volume"].sum()) if not frgn.empty and "net_buy_volume" in frgn.columns else 0
                            i_net = int(inst["net_buy_volume"].sum()) if not inst.empty and "net_buy_volume" in inst.columns else 0
                            f_e = "🔵" if f_net > 0 else "🔴"
                            i_e = "🟢" if i_net > 0 else "🔴"
                            flow_lines.append(
                                f"  {name}: 외인{f_e}{f_net:+,}주 기관{i_e}{i_net:+,}주 (3일)"
                            )
                        except Exception:
                            pass
                    if flow_lines:
                        flow_ctx = "\n[외인/기관 수급 (3일)]\n" + "\n".join(flow_lines) + "\n"

                    # v9.0: 한국 특수 수급 패턴 탐지
                    try:
                        from kstock.signal.institutional_tracker import detect_korean_flow_patterns
                        pattern_lines = []
                        for h in holdings[:8]:
                            ticker = h.get("ticker", "")
                            name = h.get("name", ticker)
                            sd = self.db.get_supply_demand(ticker, days=10)
                            if len(sd) >= 3:
                                pats = detect_korean_flow_patterns(
                                    ticker, name, sd,
                                    usdkrw_change_pct=getattr(macro, "usdkrw_change_pct", 0),
                                )
                                for p in pats:
                                    pattern_lines.append(
                                        f"  [{p.signal}] {name}: {p.description} ({p.score_adj:+d}점)"
                                    )
                        if pattern_lines:
                            flow_ctx += "\n[수급 패턴 감지]\n" + "\n".join(pattern_lines) + "\n"
                    except Exception:
                        pass
            except Exception:
                logger.debug("Morning briefing flow data failed", exc_info=True)

            # v9.0: 주봉 매집 분석 (보유종목)
            accum_ctx = ""
            try:
                from kstock.features.weekly_pattern import analyze_weekly_accumulation
                accum_lines = []
                for h in holdings[:5]:
                    ticker = h.get("ticker", "")
                    name = h.get("name", ticker)
                    try:
                        ohlcv = await self.yf_client.get_ohlcv(ticker, period="6mo")
                        if ohlcv is not None and len(ohlcv) >= 40:
                            sd = self.db.get_supply_demand(ticker, days=20)
                            acc = analyze_weekly_accumulation(ohlcv, sd)
                            if acc.total >= 40:
                                grade = "매집확인" if acc.total >= 70 else "매집가능"
                                accum_lines.append(
                                    f"  {name}: {acc.total}점({grade}) "
                                    f"{'|'.join(acc.signals[:2])}"
                                )
                                if acc.pattern:
                                    accum_lines[-1] += f" [{acc.pattern}]"
                    except Exception:
                        pass
                if accum_lines:
                    accum_ctx = "\n[주봉 매집 분석]\n" + "\n".join(accum_lines) + "\n"
            except Exception:
                logger.debug("Morning briefing accumulation failed", exc_info=True)

            # v6.2.2 / v8.1: 경계 모드 컨텍스트 — 전시 상황 구체화
            alert_ctx = ""
            current_alert = getattr(self, '_alert_mode', 'normal')
            if current_alert == "wartime":
                alert_ctx = (
                    "\n[🔴 전시 경계 모드 — 필수 반영]\n"
                    "국내 증시 전반 폭락/전시 상황. 브리핑에 반드시 반영:\n"
                    "- 모든 종목 판단에 '전시 상황'을 명시적으로 반영\n"
                    "- 경기민감 섹터: 비중 축소 또는 손절 권고\n"
                    "- 방어 섹터: 보유 유지 권고\n"
                    "- 신규 매수: 극히 제한적, 분할 진입만\n"
                    "- 손절 기준 -5%로 강화\n"
                    "- '현재 상황을 이해하고 있다'는 느낌을 반드시 전달\n\n"
                )
            elif current_alert == "elevated":
                acfg = self._get_alert_config()
                alert_ctx = (
                    f"\n[🟠 경계 모드: {acfg['label']}]\n"
                    "변동성 확대 구간. 손절 -6%, 분할 매수 권장.\n\n"
                )
            elif current_alert != "normal":
                acfg = self._get_alert_config()
                alert_ctx = f"\n[⚠️ 현재 경계 모드: {acfg['label']}]\n"

            # v9.0: 선물지수 + 만기 경고
            futures_ctx = ""
            es = getattr(macro, "es_futures", 0)
            nq = getattr(macro, "nq_futures", 0)
            if es > 0 or nq > 0:
                parts = []
                if es > 0:
                    parts.append(f"S&P선물={es:,.0f}({getattr(macro, 'es_futures_change_pct', 0):+.2f}%)")
                if nq > 0:
                    parts.append(f"나스닥선물={nq:,.0f}({getattr(macro, 'nq_futures_change_pct', 0):+.2f}%)")
                futures_ctx = ", ".join(parts) + "\n"

            from kstock.bot.context_builder import get_futures_expiry_warning
            expiry_warn = get_futures_expiry_warning()
            if expiry_warn:
                futures_ctx += f"{expiry_warn}\n"

            # v9.0: 변동성 레짐
            vol_ctx = ""
            kr_vol = getattr(macro, "korean_vol", 0)
            vol_regime = getattr(macro, "vol_regime", "")
            if vol_regime:
                regime_labels = {"low": "저변동", "normal": "보통", "high": "고변동", "extreme": "극단"}
                vol_ctx = f"변동성레짐={regime_labels.get(vol_regime, vol_regime)}(한국Vol={kr_vol:.1f}%)\n"

            # v9.0: 프로그램 매매 데이터
            prog_ctx = ""
            try:
                prog_data = self.db.get_program_trading(days=3, market="KOSPI")
                if prog_data:
                    latest = prog_data[0]
                    prog_ctx = (
                        f"[프로그램매매] 전체={latest['total_net']:+,.0f}억 "
                        f"(차익={latest['arb_net']:+,.0f}, 비차익={latest['non_arb_net']:+,.0f})\n"
                    )
            except Exception:
                pass

            # v9.0: 신용잔고/예탁금
            credit_ctx = ""
            try:
                credit_data = self.db.get_credit_balance(days=5)
                if credit_data:
                    c = credit_data[0]
                    credit_tril = c["credit"] / 10000
                    deposit_tril = c["deposit"] / 10000
                    credit_ctx = (
                        f"[신용잔고] {credit_tril:.1f}조({c['credit_change']:+,.0f}억) "
                        f"예탁금={deposit_tril:.1f}조({c['deposit_change']:+,.0f}억)\n"
                    )
            except Exception:
                pass

            # v9.0: ETF 자금흐름
            etf_ctx = ""
            try:
                etf_data = self.db.get_etf_flow(days=1)
                if etf_data:
                    lev_cap = sum(d["market_cap"] for d in etf_data if d.get("etf_type") == "leverage")
                    inv_cap = sum(d["market_cap"] for d in etf_data if d.get("etf_type") == "inverse")
                    if lev_cap > 0 or inv_cap > 0:
                        etf_ctx = (
                            f"[ETF흐름] 레버리지={lev_cap/10000:.1f}조 "
                            f"인버스={inv_cap/10000:.1f}조\n"
                        )
            except Exception:
                pass

            # v9.0: 산업 생태계 컨텍스트
            industry_ctx = ""
            try:
                from kstock.signal.industry_ecosystem import format_industry_for_telegram
                ind_lines = []
                for h in holdings[:5]:
                    ticker = h.get("ticker", "")
                    ind = format_industry_for_telegram(ticker)
                    if ind:
                        ind_lines.append(f"  {h.get('name', ticker)}: {ind.split(chr(10))[0]}")
                if ind_lines:
                    industry_ctx = "\n[산업 생태계]\n" + "\n".join(ind_lines) + "\n"
            except Exception:
                pass

            # v9.0: 한국형 리스크 종합
            korea_risk_ctx = ""
            try:
                from kstock.signal.korea_risk import assess_korea_risk, format_korea_risk
                kr_args = {
                    "vix": macro.vix,
                    "usdkrw": macro.usdkrw,
                    "usdkrw_change_pct": getattr(macro, "usdkrw_change_pct", 0),
                    "month": datetime.now(KST).month,
                    "day": datetime.now(KST).day,
                }
                try:
                    cred = self.db.get_credit_balance(days=1)
                    if cred:
                        kr_args["credit_data"] = cred
                except Exception:
                    pass
                try:
                    etf = self.db.get_etf_flow(days=1)
                    if etf:
                        kr_args["etf_data"] = etf
                except Exception:
                    pass
                try:
                    prog = self.db.get_program_trading(days=1, market="KOSPI")
                    if prog:
                        kr_args["program_data"] = prog
                except Exception:
                    pass
                # 만기일
                from calendar import monthcalendar
                now_br = datetime.now(KST)
                cal = monthcalendar(now_br.year, now_br.month)
                thursdays = [week[3] for week in cal if week[3] != 0]
                if len(thursdays) >= 2:
                    expiry_day = thursdays[1]
                    days_until = (datetime(now_br.year, now_br.month, expiry_day, tzinfo=KST).date() - now_br.date()).days
                    if 0 <= days_until <= 5:
                        kr_args["days_to_expiry"] = days_until
                assessment = assess_korea_risk(**kr_args)
                if assessment.total_risk > 0:
                    korea_risk_ctx = f"\n{format_korea_risk(assessment)}\n"
            except Exception:
                logger.debug("Morning briefing korea risk failed", exc_info=True)

            prompt = (
                f"주호님의 오늘 아침 투자 브리핑을 작성해주세요.\n\n"
                f"[시장 데이터]\n"
                f"VIX={macro.vix:.1f}({macro.vix_change_pct:+.1f}%), "
                f"S&P500={macro.spx_change_pct:+.2f}%, "
                f"나스닥={macro.nasdaq_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원({macro.usdkrw_change_pct:+.2f}%), "
                f"BTC=${macro.btc_price:,.0f}({macro.btc_change_pct:+.1f}%), "
                f"금=${macro.gold_price:,.0f}({macro.gold_change_pct:+.1f}%), "
                f"레짐={macro.regime}, 모드={regime_mode.get('label', '')}\n"
                f"{futures_ctx}"
                f"{vol_ctx}"
                f"{prog_ctx}"
                f"{credit_ctx}"
                f"{etf_ctx}"
                f"{korea_risk_ctx}\n"
                f"{news_ctx}"
                f"{yt_intel_ctx}"
                f"{special_ctx}"
                f"{alert_ctx}"
                f"[보유종목]\n{holdings_text}\n"
                f"{flow_ctx}"
                f"{accum_ctx}"
                f"{industry_ctx}"
                f"아래 형식으로 작성해주세요:\n\n"
                f"1) 시장 요약 (3줄 이내) — 글로벌 뉴스 + 수급 핵심\n"
                f"2) 보유종목별 판단 — 각 종목마다:\n"
                f"   - 종목명 + 수익률 + 외인/기관 수급 동향\n"
                f"   - 투자시계(단기/스윙/중기/장기)에 맞는 판단\n"
                f"   - 판단: 보유유지/추가매수/일부익절/전량매도/손절 중 택1\n"
                f"   - 구체적 이유 1줄 (수급 근거 포함)\n"
                f"   (가격 추측 금지 — 지지선/목표가/손절가 등 구체적 가격은 제공된 데이터만 사용)\n"
                f"3) 외인/기관 수급 종합 (수급 데이터가 있으면 반드시 분석)\n"
                f"4) 오늘 주목할 이벤트/섹터 (2줄)\n\n"
                f"투자시계별 기준:\n"
                f"- 단기(scalp): 1~3일, 수익 3~5% 목표\n"
                f"- 스윙(swing): 1~2주, 수익 8~15% 목표\n"
                f"- 중기(mid): 1~3개월, 수익 15~30% 목표\n"
                f"- 장기(long): 3개월+, 수익 30~100% 목표\n\n"
                f"볼드(**) 사용 금지. 이모지로 가독성 확보.\n"
                f"존댓말 사용 (주호님). 한 문장 최대 25자.\n\n"
                f"[가격 환각 금지 — 최중요]\n"
                f"지지선, 저항선, 목표가, 손절가 등 구체적 가격을 절대 만들어내지 마라.\n"
                f"위 보유종목의 매수가/현재가/수익률만 인용 가능.\n"
                f"'XX,000원 지지선', 'XX만원 목표' 같은 추측 가격 제시 시 사용자에게 금전적 피해 발생.\n"
                f"기술적 가격이 필요하면 '차트에서 지지/저항 확인 필요'로 대체."
            )
            result = await self.ai.analyze(
                "morning_briefing", prompt, max_tokens=1200,
            )
            if result and not result.startswith("[AI 응답 불가]"):
                return result
            logger.warning("Morning v2 AI router returned empty/error")
        except Exception as e:
            logger.warning("Morning v2 briefing failed: %s, falling back", e)
        # fallback to simple briefing
        return await self._generate_claude_briefing(macro, regime_mode)

    async def job_intraday_monitor(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
            return
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if not (market_open <= now <= market_close):
            return
        # 보유종목 캐시 갱신 (매도 가이드용) — 동시성 보호
        _new_holdings = self.db.get_active_holdings()
        async with self._ensure_lock():
            self._holdings_cache = _new_holdings
            self._holdings_index = {
                h.get("ticker", ""): h for h in self._holdings_cache if h.get("ticker")
            }
        try:
            results = await self._scan_all_stocks()
            self._last_scan_results = results
            self._scan_cache_time = now
            macro = await self.macro_client.get_snapshot()

            # v7.0: Alert fatigue 방지 — 우선순위 기반 Top-N 알림
            # 점수순 정렬 후 상위 5개만 알림 발송 (나머지는 로그만)
            MAX_ALERTS_PER_SCAN = 5
            sorted_results = sorted(
                results,
                key=lambda r: getattr(r.score, "composite", 0),
                reverse=True,
            )
            alert_count = 0
            for r in sorted_results:
                if alert_count >= MAX_ALERTS_PER_SCAN:
                    break
                # 알림 대상 여부: BUY/WATCH 시그널만
                sig = getattr(r.score, "signal", "HOLD")
                if sig in ("BUY", "STRONG_BUY", "WATCH", "MILD_BUY"):
                    await self._check_and_send_alerts(context.bot, r, macro)
                    alert_count += 1

            await self._check_holdings(context.bot)

            # #3 매니저별 장중 실시간 알림
            await self._check_manager_alerts(context.bot)

            # 장중 급등 종목 감지 + 장기 우량주 추천
            await self._check_surge_and_longterm(context.bot, results, macro)

            logger.info("Intraday monitor: %d stocks scanned", len(results))
        except Exception as e:
            logger.error("Intraday monitor error: %s", e, exc_info=True)

    async def _check_manager_alerts(self, bot) -> None:
        """#3 매니저별 장중 실시간 알림 (보유종목 기술적 조건 체크)."""
        try:
            from kstock.bot.investment_managers import check_manager_alert_conditions
            from kstock.features.technical import compute_indicators

            holdings = self.db.get_active_holdings()
            for h in holdings:
                mtype = h.get("holding_type", "auto")
                if mtype == "auto" or mtype not in ("scalp", "swing", "position", "long_term"):
                    continue
                ticker = h.get("ticker", "")
                if not ticker:
                    continue
                # 쿨다운: 동일 종목 매니저 알림 4시간 내 중복 방지
                if self.db.has_recent_alert(ticker, f"mgr_{mtype}", hours=4):
                    continue
                try:
                    market = "KOSPI"
                    for s in self.all_tickers:
                        if s["code"] == ticker:
                            market = s.get("market", "KOSPI")
                            break
                    ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="1mo")
                    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
                        continue
                    tech = compute_indicators(ohlcv)
                    cp = float(h.get("current_price") or 0)
                    bp = float(h.get("buy_price") or 0)
                    hold_days = 0
                    if h.get("buy_date"):
                        try:
                            from datetime import datetime as _dt
                            bd = _dt.fromisoformat(h["buy_date"].replace("Z", ""))
                            hold_days = (_dt.utcnow() - bd).days
                        except Exception:
                            pass

                    alerts = check_manager_alert_conditions(
                        mtype, ticker, h.get("name", ""),
                        tech, cp, bp, hold_days,
                    )
                    for alert_text in alerts[:2]:
                        await bot.send_message(
                            chat_id=self.chat_id, text=alert_text[:2000],
                        )
                        self.db.insert_alert(ticker, f"mgr_{mtype}", alert_text[:200])
                except Exception:
                    logger.debug("Manager alert check %s failed", ticker, exc_info=True)
        except Exception:
            logger.debug("_check_manager_alerts failed", exc_info=True)

    async def _check_surge_and_longterm(
        self, bot, results: list, macro: MacroSnapshot
    ) -> None:
        """장중 급등/반등/장기 종목 감지.

        v6.2.2: 반등 감지 추가 — 최근 급락 후 V턴 시그널.
        """
        surge_stocks = []
        longterm_picks = []
        bounce_stocks = []

        for r in results:
            info = r.info
            change_pct = getattr(info, "change_pct", 0)
            score = r.score

            # 급등 감지: 경계 모드별 임계값 적용
            if change_pct >= self._SURGE_THRESHOLD_PCT:
                if not self.db.has_recent_alert(r.ticker, "surge", hours=8):
                    surge_stocks.append(r)

            # 장기 우량주: 점수 65+ & 펀더멘탈 높음 & RSI 과매도 아님
            if (score.composite >= 65
                    and score.fundamental >= 0.7
                    and r.tech.rsi >= 30):
                if not self.db.has_recent_alert(r.ticker, "longterm_pick", hours=72):
                    longterm_picks.append(r)

            # v6.2.2: 반등(V턴) 감지
            # 조건: RSI 과매도(< 35) → 당일 +1.5%+ 반등 + 거래량 증가
            rsi = getattr(r.tech, "rsi", 50)
            vol_ratio = getattr(info, "volume_ratio", 1.0)
            if (rsi < 35
                    and change_pct >= 1.5
                    and vol_ratio >= 1.5):
                if not self.db.has_recent_alert(r.ticker, "bounce", hours=24):
                    bounce_stocks.append(r)

        # 급등 알림 (상위 3개) — v6.2: 이유+액션 포함
        if surge_stocks:
            surge_stocks.sort(
                key=lambda x: getattr(x.info, "change_pct", 0), reverse=True,
            )
            lines = ["\U0001f525 장중 급등 종목 감지\n"]
            for s in surge_stocks[:3]:
                chg = getattr(s.info, "change_pct", 0)
                price = getattr(s.info, "current_price", 0)
                vol_ratio = getattr(s.info, "volume_ratio", 0)
                # v6.2: 급등 이유 추론
                reasons = []
                if vol_ratio > 3:
                    reasons.append(f"거래량 {vol_ratio:.0f}배 급증")
                if chg >= 10:
                    reasons.append("상한가 접근 — 뉴스/공시 확인 필요")
                elif chg >= 5:
                    reasons.append("강한 매수세 유입")
                if s.score.composite >= 110:
                    reasons.append(f"스캔 점수 {s.score.composite:.0f}점 — 펀더멘탈 양호")
                reason_text = " | ".join(reasons[:2]) if reasons else "장중 모멘텀"

                lines.append(
                    f"\U0001f4c8 {s.name} ({s.ticker})\n"
                    f"  {price:,.0f}원 | +{chg:.1f}%\n"
                    f"  점수 {s.score.composite:.0f}점 | {s.score.signal}\n"
                    f"  💡 {reason_text}"
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

        # v6.2.2: 반등(V턴) 감지 알림
        if bounce_stocks:
            bounce_stocks.sort(
                key=lambda x: getattr(x.info, "change_pct", 0), reverse=True,
            )
            lines = ["📈 반등(V턴) 감지\n"]
            for b in bounce_stocks[:3]:
                chg = getattr(b.info, "change_pct", 0)
                price = getattr(b.info, "current_price", 0)
                rsi = getattr(b.tech, "rsi", 50)
                vol = getattr(b.info, "volume_ratio", 1)
                # 보유종목 여부 확인
                is_held = b.ticker in self._holdings_index
                held_tag = " 📌보유중" if is_held else ""
                lines.append(
                    f"🔄 {b.name} ({b.ticker}){held_tag}\n"
                    f"  {price:,.0f}원 | +{chg:.1f}% 반등\n"
                    f"  RSI {rsi:.0f}(과매도) | 거래량 {vol:.1f}배\n"
                    f"  💡 급락 후 매수세 유입 — 추가매수 검토"
                )
                self.db.insert_alert(b.ticker, "bounce", f"V턴 +{chg:.1f}%")
            buttons = []
            for b in bounce_stocks[:3]:
                row = [
                    InlineKeyboardButton(
                        f"🔍 상세", callback_data=f"detail:{b.ticker}",
                    ),
                ]
                if b.ticker not in self._holdings_index:
                    row.append(InlineKeyboardButton(
                        f"⭐ 즐겨찾기", callback_data=f"fav:add:{b.ticker}:{b.name}",
                    ))
                buttons.append(row)
            buttons.append([InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")])
            await bot.send_message(
                chat_id=self.chat_id,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    async def job_eod_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
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

            # 2. 추천 종목 (v9.4: debate 뱃지 추가)
            reco_data = []
            for i, r in enumerate(results[:10], 1):
                debate_badge = ""
                try:
                    d = self.db.get_latest_debate(r.ticker)
                    if d:
                        v = d.get("verdict", "")
                        conf = d.get("confidence", 0)
                        debate_badge = f"{v}{conf:.0f}%"
                except Exception:
                    pass
                _atr = getattr(r.tech, "atr_pct", 0.0) if r.tech else 0.0
                reco_data.append((
                    i, r.name, r.ticker, r.score.composite, r.score.signal,
                    r.strategy_type, r.info.current_price if hasattr(r.info, 'current_price') else 0,
                    "", debate_badge, _atr,
                ))
            msg = "\U0001f4ca 장 마감 리포트\n\n" + format_recommendations(reco_data)
            buttons = [
                [InlineKeyboardButton(
                    f"\U0001f4cb {r.name} 상세보기", callback_data=f"detail:{r.ticker}",
                )]
                for r in results[:3]
            ]
            buttons.extend([
                [
                    InlineKeyboardButton("📊 차트", callback_data="vchart:portfolio"),
                    InlineKeyboardButton("🔬 섹터", callback_data="sdive:menu"),
                ],
                [
                    InlineKeyboardButton("👍", callback_data="fb:like:장마감"),
                    InlineKeyboardButton("👎", callback_data="fb:dislike:장마감"),
                ],
            ])
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
            logger.warning("_eod_market_analysis macro snapshot failed", exc_info=True)
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

        # v6.1: 글로벌 뉴스 컨텍스트
        eod_news_ctx = ""
        try:
            eod_news = self.db.get_recent_global_news(limit=5, hours=12)
            if eod_news:
                eod_news_lines = []
                for n in eod_news:
                    urgency = "🚨" if n.get("is_urgent") else "📰"
                    eod_news_lines.append(f"  {urgency} {n.get('title', '')}")
                eod_news_ctx = "\n[글로벌 뉴스 헤드라인]\n" + "\n".join(eod_news_lines) + "\n"
        except Exception:
            logger.debug("_eod_market_analysis global news fetch failed", exc_info=True)

        # v9.0: 한국형 리스크 컨텍스트
        eod_risk_ctx = ""
        try:
            from kstock.signal.korea_risk import assess_korea_risk, format_korea_risk
            kr_args = {
                "vix": macro.vix,
                "usdkrw": macro.usdkrw,
                "usdkrw_change_pct": getattr(macro, "usdkrw_change_pct", 0),
                "month": datetime.now(KST).month,
                "day": datetime.now(KST).day,
            }
            try:
                cred = self.db.get_credit_balance(days=1)
                if cred:
                    kr_args["credit_data"] = cred
            except Exception:
                pass
            try:
                etf = self.db.get_etf_flow(days=1)
                if etf:
                    kr_args["etf_data"] = etf
            except Exception:
                pass
            try:
                prog = self.db.get_program_trading(days=1, market="KOSPI")
                if prog:
                    kr_args["program_data"] = prog
            except Exception:
                pass
            assessment = assess_korea_risk(**kr_args)
            if assessment.total_risk > 0:
                eod_risk_ctx = f"\n{format_korea_risk(assessment)}\n"
        except Exception:
            pass

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
            f"{eod_news_ctx}"
            f"{eod_risk_ctx}"
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

        eod_system = (
            "너는 CFA/CAIA 자격을 보유한 20년 경력 한국 주식 전문 애널리스트 QuantBot이다. "
            "주호님 전용 비서로, 매일 장 마감 후 4000자 수준의 전문 시장 분석을 제공한다. "
            "볼드(**) 사용 금지. 마크다운 헤딩(#) 사용 금지. "
            "이모지로 섹션을 구분하고, 번호 매기기를 사용해 가독성을 높인다. "
            "반드시 구체적 수치와 근거를 제시하라. "
            "추상적 표현(예: '관심 필요', '주시 필요') 대신 명확한 액션을 제시. "
            "글로벌 투자은행 리서치 수준의 분석 깊이를 목표로 한다. "
            "보유종목에 대해서는 특히 구체적으로 분석하라."
        )
        analysis = await self.ai.analyze(
            "eod_report", prompt,
            system=eod_system, max_tokens=3500, temperature=0.3,
        )
        analysis = analysis.strip().replace("**", "")

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
                logger.debug("job_market_pulse get_price_detail failed for %s", ticker, exc_info=True)
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
        """매크로 데이터 백그라운드 갱신 + VIX 레짐 변경 시 모니터링 주기 조정."""
        try:
            await self.macro_client.refresh_now()
        except Exception as e:
            logger.debug("Macro refresh job error: %s", e)
            return

        # ── VIX 레짐 체크 → 모니터링 주기 동적 조정 ──
        try:
            macro = await self.macro_client.get_snapshot()
            new_regime = _get_vix_regime(macro.vix)

            if not hasattr(self, "_current_vix_regime"):
                self._current_vix_regime = "normal"
            if not hasattr(self, "_last_reschedule_time"):
                self._last_reschedule_time = 0.0

            if new_regime != self._current_vix_regime:
                now_mono = _time.monotonic()
                if now_mono - self._last_reschedule_time >= _RESCHEDULE_COOLDOWN:
                    old_regime = self._current_vix_regime
                    self._current_vix_regime = new_regime
                    self._last_reschedule_time = now_mono
                    await self._reschedule_monitors(context, new_regime, old_regime, macro.vix)
        except Exception as e:
            logger.debug("VIX regime check error: %s", e)

    async def job_market_pulse(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """시장 맥박 체크 + 변화 시 알림 + 적응형 모니터링 주기 조정."""
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
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

    async def _reschedule_monitors(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        new_regime: str,
        old_regime: str,
        vix: float,
    ) -> None:
        """VIX 레짐 변경 시 intraday_monitor/market_pulse 제거 후 새 주기로 재등록."""
        intervals = ADAPTIVE_INTERVALS.get(new_regime, ADAPTIVE_INTERVALS["normal"])
        old_intervals = ADAPTIVE_INTERVALS.get(old_regime, ADAPTIVE_INTERVALS["normal"])

        jq = getattr(self, "_job_queue", None) or context.application.job_queue
        if jq is None:
            return

        try:
            # 기존 job 제거
            current_jobs = jq.jobs()
            for job in current_jobs:
                if job.name in ("intraday_monitor", "market_pulse"):
                    job.schedule_removal()

            # 새 주기로 재등록
            jq.run_repeating(
                self.job_intraday_monitor,
                interval=intervals["intraday_monitor"],
                first=5,
                name="intraday_monitor",
            )
            jq.run_repeating(
                self.job_market_pulse,
                interval=intervals["market_pulse"],
                first=10,
                name="market_pulse",
            )

            old_sec = old_intervals.get("intraday_monitor", 60)
            new_sec = intervals["intraday_monitor"]

            logger.info(
                "Adaptive monitoring: %s -> %s (VIX: %.1f, interval: %ds -> %ds)",
                old_regime, new_regime, vix, old_sec, new_sec,
            )

            # 텔레그램 알림
            if self.chat_id:
                regime_emoji = {
                    "calm": "😴", "normal": "🟢", "fear": "🟠", "panic": "🔴",
                }
                msg = (
                    f"{regime_emoji.get(new_regime, '⚡')} 모니터링 주기 변경\n\n"
                    f"VIX: {vix:.1f}\n"
                    f"레짐: {old_regime} → {new_regime}\n"
                    f"체크 주기: {old_sec}초 → {new_sec}초"
                )
                if new_regime in ("fear", "panic"):
                    msg += "\n\n🚨 시장 감시 강화 모드 진입"
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=msg,
                )
        except Exception as e:
            logger.error("Adaptive reschedule failed: %s", e)

    async def job_daily_pdf_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """통합 장 마감 리포트 (16:00 KST).

        1건의 간결한 텍스트 메시지 + 1건의 PDF 파일.
        기존 eod_report + daily_pdf_report를 통합.
        """
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
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
                    logger.debug("job_pdf_report get_price_detail failed for %s", h.get("ticker"), exc_info=True)

            # ── 3. PDF 생성 ──
            market_state = self.market_pulse.get_current_state()
            sell_plans = self.sell_planner.create_plans_for_all(
                holdings, market_state,
            )
            # v6.1: PDF에 글로벌 뉴스 포함
            pdf_news = []
            try:
                pdf_news = self.db.get_recent_global_news(limit=8, hours=24)
            except Exception:
                logger.debug("job_pdf_report global news fetch failed", exc_info=True)

            filepath = await generate_daily_pdf(
                macro_snapshot=macro,
                holdings=holdings,
                sell_plans=sell_plans,
                pulse_history=self.market_pulse.get_recent_history(minutes=360),
                yf_client=self.yf_client,
                global_news=pdf_news,
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
                # 포트폴리오 스냅샷 저장
                try:
                    import json as _json
                    daily_pnl_pct = (total_day_pnl / total_eval * 100) if total_eval > 0 else 0
                    self.db.add_portfolio_snapshot(
                        date_str=now.strftime("%Y-%m-%d"),
                        total_value=total_eval,
                        holdings_count=len(holdings),
                        daily_pnl_pct=daily_pnl_pct,
                        total_pnl_pct=total_rate,
                        holdings_json=_json.dumps(
                            [{"ticker": h.get("ticker"), "name": h.get("name"),
                              "pnl_pct": h.get("pnl_pct", 0)} for h in holdings],
                            ensure_ascii=False,
                        ),
                    )
                    logger.info("Portfolio snapshot saved: %s, value=%.0f", now.strftime("%Y-%m-%d"), total_eval)
                except Exception as e:
                    logger.warning("Failed to save portfolio snapshot: %s", e)
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

            # v9.0: 변동성 레짐 + 프로그램 매매 라인
            extra_lines = []
            vr_level = getattr(macro, "vol_regime", "")
            if vr_level:
                vr_labels = {"low": "🟢 저변동", "normal": "🟡 보통", "high": "🟠 고변동", "extreme": "🔴 극단"}
                extra_lines.append(f"변동성: {vr_labels.get(vr_level, vr_level)}")
            try:
                _prog = self.db.get_program_trading(days=1, market="KOSPI")
                if _prog:
                    _p = _prog[0]
                    extra_lines.append(f"프로그램: {_p['total_net']:+,.0f}억")
            except Exception:
                pass
            try:
                _cred = self.db.get_credit_balance(days=1)
                if _cred:
                    _c = _cred[0]
                    extra_lines.append(f"신용: {_c['credit']/10000:.1f}조({_c['credit_change']:+,.0f}억)")
            except Exception:
                pass
            try:
                _etf = self.db.get_etf_flow(days=1)
                if _etf:
                    _lev = sum(d["market_cap"] for d in _etf if d.get("etf_type") == "leverage")
                    _inv = sum(d["market_cap"] for d in _etf if d.get("etf_type") == "inverse")
                    if _lev > 0:
                        extra_lines.append(f"레버ETF: {_lev/10000:.1f}조")
            except Exception:
                pass
            extra_text = " | ".join(extra_lines)
            if extra_text:
                extra_text = f"\n{extra_text}"

            date_str = now.strftime("%m/%d")
            text_msg = (
                f"📊 장 마감 리포트 {date_str}\n"
                f"{'━' * 22}\n\n"
                f"🎯 결론: {verdict}\n"
                f"시장: {regime_kr} | S&P {macro.spx_change_pct:+.2f}%{extra_text}\n\n"
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

    @staticmethod
    def _market_signal(macro) -> tuple[str, str]:
        """미국 시장 데이터 기반 한국 시장 신호등 산출.

        Returns: (emoji, label)
            🟢 원활  — 미국장 양호, 위험지표 안정
            🟡 주의  — 혼조세 또는 약한 하락
            🔴 경계  — 미국장 급락 또는 VIX 급등
        """
        score = 0
        # S&P500
        spx = macro.spx_change_pct
        if spx > 0.5:
            score += 2
        elif spx > 0:
            score += 1
        elif spx > -0.5:
            score -= 1
        elif spx > -1.5:
            score -= 2
        else:
            score -= 3
        # 나스닥
        ndx = macro.nasdaq_change_pct
        if ndx > 0.5:
            score += 2
        elif ndx > 0:
            score += 1
        elif ndx > -0.5:
            score -= 1
        elif ndx > -1.5:
            score -= 2
        else:
            score -= 3
        # VIX
        vix = macro.vix
        if vix < 15:
            score += 2
        elif vix < 20:
            score += 1
        elif vix < 25:
            score -= 1
        elif vix < 30:
            score -= 2
        else:
            score -= 3
        # 환율 (원화 약세 = 부정)
        krw = macro.usdkrw_change_pct
        if krw > 0.5:
            score -= 1
        elif krw < -0.3:
            score += 1

        if score >= 3:
            return "🟢", "원활"
        elif score >= 0:
            return "🟡", "보통"
        elif score >= -3:
            return "🟠", "주의"
        else:
            return "🔴", "경계"

    async def job_daily_directive(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """v5.9: 매일 06:00 일일 운영 지침 읽기 + AI 자율 판단.

        data/daily_directive.md를 읽고, 시장 데이터 + 보유종목 상황과 결합하여
        AI가 오늘의 운영 계획을 수립. 결과를 텔레그램으로 전송.
        """
        if not self.chat_id:
            return
        try:
            from pathlib import Path

            # 1. 지침 파일 읽기
            directive_path = Path("data/daily_directive.md")
            if not directive_path.exists():
                logger.warning("daily_directive.md not found")
                return
            directive = directive_path.read_text(encoding="utf-8")

            # 2. 시장 데이터 수집
            macro = await self.macro_client.get_snapshot()
            signal_emoji, signal_label = self._market_signal(macro)

            # 3. 보유종목 상황
            holdings = self.db.get_active_holdings()
            holdings_text = ""
            alert_stocks = []
            if holdings:
                for h in holdings:
                    name = h.get("name", h.get("ticker", ""))
                    pnl = h.get("pnl_pct", 0)
                    horizon = h.get("holding_type", "swing")
                    current = h.get("current_price", 0)
                    buy = h.get("buy_price", 0)
                    # 실시간 가격 시도
                    try:
                        current = await self._get_price(h.get("ticker", ""), base_price=buy)
                        if buy > 0 and current > 0:
                            pnl = (current - buy) / buy * 100
                    except Exception:
                        logger.debug("job_daily_directive get_price failed for %s", h.get("ticker", ""), exc_info=True)
                    holdings_text += f"  {name}: {pnl:+.1f}% (매수 {buy:,.0f} → 현재 {current:,.0f}, {horizon})\n"
                    # 알림 대상 감지
                    if pnl <= -7 and horizon not in ("long", "long_term"):
                        alert_stocks.append(f"🔴 {name} {pnl:+.1f}% — 손절 검토 필요")
                    elif pnl >= 10:
                        alert_stocks.append(f"🟢 {name} {pnl:+.1f}% — 부분 익절 타이밍")
            else:
                holdings_text = "  보유종목 없음\n"

            # 4. 즐겨찾기 종목
            watchlist = self.db.get_watchlist()
            watch_names = ", ".join(w.get("name", w.get("ticker", ""))[:6] for w in watchlist[:10]) if watchlist else "없음"

            # 5. 시장 개장 여부
            today = datetime.now(KST).date()
            market_open = is_kr_market_open(today)
            market_note = "개장일" if market_open else "휴장일"

            # 5.5 v6.1: 글로벌 뉴스 컨텍스트
            news_ctx = ""
            try:
                news_items = self.db.get_recent_global_news(limit=5, hours=12)
                if news_items:
                    news_lines = []
                    for n in news_items:
                        urgency = "🚨" if n.get("is_urgent") else "📰"
                        news_lines.append(f"  {urgency} {n.get('title', '')}")
                    news_ctx = "\n[글로벌 뉴스 헤드라인]\n" + "\n".join(news_lines) + "\n"
            except Exception:
                logger.debug("job_daily_directive global news fetch failed", exc_info=True)

            # 6. AI 프롬프트 구성
            prompt = (
                f"K-Quant 에이전트 일일 운영 지침을 읽고 오늘의 운영 계획을 수립해주세요.\n\n"
                f"━━━ 운영 지침 ━━━\n{directive}\n\n"
                f"━━━ 오늘의 상황 ━━━\n"
                f"날짜: {today.strftime('%Y-%m-%d (%A)')}\n"
                f"한국 시장: {market_note}\n"
                f"시장 신호등: {signal_emoji} {signal_label}\n\n"
                f"[글로벌 시장]\n"
                f"S&P500: {macro.spx_change_pct:+.2f}%\n"
                f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                f"VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
                f"환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
                f"BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
                f"레짐: {macro.regime}\n"
                f"공포탐욕: {macro.fear_greed_score:.0f} ({macro.fear_greed_label})\n\n"
                f"{news_ctx}"
                f"[보유종목 현황]\n{holdings_text}\n"
                f"[즐겨찾기]\n  {watch_names}\n\n"
            )

            if alert_stocks:
                prompt += f"[긴급 알림 대상]\n" + "\n".join(alert_stocks) + "\n\n"

            prompt += (
                f"아래 형식으로 오늘의 운영 계획을 작성해주세요:\n\n"
                f"1. 오늘의 시장 모드 (한 줄)\n"
                f"   예: '🟢 적극 모드 — VIX 안정, 미국장 상승'\n"
                f"   예: '🔴 방어 모드 — VIX 급등, 미국장 급락'\n"
                f"   예: '📅 휴장일 — 미국 시장 모니터링만'\n\n"
                f"2. 보유종목 체크포인트 (종목별 1줄)\n"
                f"   - 지침의 손절/익절 기준에 해당하는 종목 체크\n"
                f"   - 오늘 주의할 이벤트가 있는 종목\n\n"
                f"3. 오늘 모니터링 포인트 (2-3줄)\n"
                f"   - 주목할 이벤트/지표\n"
                f"   - 관심 섹터 동향\n\n"
                f"4. 에이전트 행동 계획\n"
                f"   - 오늘 어떤 알림을 집중할지\n"
                f"   - 모니터링 강도 (평상시/강화/최소)\n\n"
                f"볼드(**) 사용 금지. 이모지로 구분. 전체 300자 이내."
            )

            system_prompt = (
                "너는 K-Quant 에이전트다. 주호님의 투자 비서로 매일 아침 6시에 "
                "운영 지침을 읽고 오늘 하루 어떻게 운영할지 계획을 세운다.\n"
                "행동 지시가 아닌 정보 전달. 매도 권유 금지. 공포 유발 금지.\n"
                "간결하고 실용적으로. 볼드(**) 금지."
            )

            if hasattr(self, 'ai') and self.ai:
                raw = await self.ai.analyze(
                    "daily_directive", prompt,
                    system=system_prompt, max_tokens=800, temperature=0.3,
                )
                from kstock.bot.chat_handler import _sanitize_response
                plan = _sanitize_response(raw.strip())
            else:
                # AI 없으면 기본 계획
                plan = (
                    f"1. 시장 모드: {signal_emoji} {signal_label}\n"
                    f"2. VIX {macro.vix:.1f}, 환율 {macro.usdkrw:,.0f}원\n"
                    f"3. 보유 {len(holdings)}종목, 즐겨찾기 {len(watchlist)}종목\n"
                    f"4. 모니터링: 평상시"
                )

            # 7. 긴급 알림이 있으면 별도 강조
            alert_text = ""
            if alert_stocks:
                alert_text = "\n\n⚠️ 긴급 체크\n" + "\n".join(alert_stocks)

            msg = (
                f"📋 일일 운영 계획\n"
                f"{'━' * 22}\n\n"
                f"{plan}"
                f"{alert_text}\n\n"
                f"{'━' * 22}\n"
                f"🤖 K-Quant Agent | {datetime.now(KST).strftime('%m/%d %H:%M')}"
            )

            await context.bot.send_message(chat_id=self.chat_id, text=msg[:4000])
            self.db.upsert_job_run("daily_directive", _today(), status="success")
            logger.info("Daily directive sent")
        except Exception as e:
            logger.error("Daily directive failed: %s", e, exc_info=True)
            self.db.upsert_job_run(
                "daily_directive", _today(),
                status="error", message=str(e),
            )

    async def job_us_premarket_briefing(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매일 07:00 미국 시장 프리마켓 브리핑 (새벽 미국장 분석).

        v5.9: 한국 시장 신호등 추가 + 휴장일 안내.
        """
        if not self.chat_id:
            return
        try:
            macro = await self.macro_client.get_snapshot()
            signal_emoji, signal_label = self._market_signal(macro)

            # 한국 시장 개장 여부
            today = datetime.now(KST).date()
            market_open = is_kr_market_open(today)
            market_note = ""
            if not market_open:
                market_note = f"\n{market_status_text(today)}\n📅 다음 개장일: {next_market_day(today).strftime('%m/%d(%a)')}\n"

            # 보유종목 컨텍스트
            holdings = self.db.get_active_holdings()
            holdings_ctx = ""
            if holdings:
                parts = []
                for h in holdings[:10]:
                    name = h.get("name", "")
                    pnl = h.get("pnl_pct", 0)
                    parts.append(f"{name}({pnl:+.1f}%)")
                holdings_ctx = f"\n보유종목: {', '.join(parts)}"

            # 신호등 헤더
            signal_header = (
                f"{'━' * 22}\n"
                f"오늘 국내 시장 전망: {signal_emoji} {signal_label}\n"
                f"{'━' * 22}"
            )

            # v6.1: 글로벌 뉴스 컨텍스트
            news_ctx = ""
            try:
                news_items = self.db.get_recent_global_news(limit=5, hours=12)
                if news_items:
                    news_lines = []
                    for n in news_items:
                        urgency = "🚨" if n.get("is_urgent") else "📰"
                        news_lines.append(f"  {urgency} {n.get('title', '')}")
                    news_ctx = "\n[글로벌 뉴스 헤드라인]\n" + "\n".join(news_lines) + "\n"
            except Exception:
                logger.debug("job_us_premarket global news fetch failed", exc_info=True)

            prompt = (
                f"새벽 미국 시장 마감 결과를 분석하고, "
                f"오늘 한국 시장에 미칠 영향을 알려줘.\n\n"
                f"[미국 시장 마감 데이터]\n"
                f"S&P500: {macro.spx_change_pct:+.2f}%\n"
                f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                f"다우: {getattr(macro, 'dow_change_pct', 0):+.2f}%\n"
                f"S&P선물(ES): {getattr(macro, 'es_futures', 0):,.0f} ({getattr(macro, 'es_futures_change_pct', 0):+.2f}%)\n"
                f"나스닥선물(NQ): {getattr(macro, 'nq_futures', 0):,.0f} ({getattr(macro, 'nq_futures_change_pct', 0):+.2f}%)\n"
                f"VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
                f"USD/KRW: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
                f"미국 10년물: {macro.us10y:.2f}%\n"
                f"미국 2년물: {getattr(macro, 'us2y', 0):.2f}%\n"
                f"DXY: {macro.dxy:.1f}\n"
                f"BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
                f"금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n"
                f"유가: ${getattr(macro, 'wti_price', 0):.1f}\n"
                f"시장체제: {macro.regime}\n"
                f"한국시장 전망 신호등: {signal_emoji} {signal_label}\n"
                f"한국시장 개장여부: {'개장' if market_open else '휴장'}\n"
                f"{news_ctx}"
                f"{holdings_ctx}\n\n"
                f"아래 형식으로 분석:\n\n"
                f"1. 미국 시장 마감 요약 (2-3줄)\n"
                f"   - 3대 지수 동향 + 주요 원인\n"
                f"   - 글로벌 뉴스 헤드라인이 있으면 핵심 이슈 반영\n\n"
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
                f"5. 오늘 주호님 참고 포인트\n"
                f"   - 장 시작 전 확인할 지표/이벤트\n"
                f"   - 보유종목 관련 섹터 영향 (매도 지시 금지, 정보만 제공)\n"
            )

            us_premarket_system = (
                "너는 한국 주식 전문 애널리스트 QuantBot이다. "
                "주호님 전용 비서. 매일 아침 7시에 새벽 미국 시장 분석을 전달한다.\n\n"
                "[절대 규칙]\n"
                "1. 매도/매수 지시 절대 금지. '매도하세요', '팔아라', '전량 매도', "
                "'무조건 매도', '시초가에 매도' 같은 표현 금지.\n"
                "2. 장기투자 종목에 시장 하락을 이유로 매도 권유 절대 금지. "
                "'잘 버티고 계세요', '장기 관점에서 문제없습니다' 식으로 안심.\n"
                "3. 공포 유발 표현 금지: '긴급', '심각', '무조건', '1초도 망설이지 마세요', "
                "'알람 맞춰두세요', '날리면 안 됩니다'.\n"
                "4. 분석만 하라. 행동 지시가 아닌 정보 전달.\n"
                "5. [가격 환각 금지 — 최중요] "
                "구체적 가격(지지선, 저항선, 목표가, 손절가)을 절대 만들어내지 마라. "
                "프롬프트에 제공된 매수가/현재가/수익률 외에는 가격 언급 금지. "
                "'120,000원 지지선', '60,000원 목표' 같은 추측 가격 절대 금지. "
                "기술적 가격 분석이 필요하면 '차트 확인 필요'로 대체하라.\n\n"
                "[형식 규칙]\n"
                "볼드(**) 사용 금지. 이모지로 구분. "
                "구체적 수치는 제공된 데이터만 사용. "
                "한국 시장 영향에 초점."
            )

            if hasattr(self, 'ai') and self.ai:
                raw = await self.ai.analyze(
                    "us_premarket", prompt,
                    system=us_premarket_system, max_tokens=2000, temperature=0.3,
                )
                from kstock.bot.chat_handler import _sanitize_response
                analysis = _sanitize_response(raw.strip())

                msg = (
                    f"🇺🇸 미국 시장 프리마켓 브리핑\n"
                    f"{signal_header}\n"
                    f"{market_note}\n"
                    f"{analysis}\n\n"
                    f"{'━' * 22}\n"
                    f"🤖 K-Quant | {datetime.now(KST).strftime('%H:%M')} 분석"
                )
            else:
                spx_emoji = "📈" if macro.spx_change_pct > 0 else "📉"
                ndq_emoji = "📈" if macro.nasdaq_change_pct > 0 else "📉"
                # v9.0: 선물 라인
                es_val = getattr(macro, 'es_futures', 0)
                nq_val = getattr(macro, 'nq_futures', 0)
                ft_line = ""
                if es_val > 0 or nq_val > 0:
                    ft_parts = []
                    if es_val > 0:
                        ft_parts.append(f"ES: {es_val:,.0f}({getattr(macro, 'es_futures_change_pct', 0):+.2f}%)")
                    if nq_val > 0:
                        ft_parts.append(f"NQ: {nq_val:,.0f}({getattr(macro, 'nq_futures_change_pct', 0):+.2f}%)")
                    ft_line = f"📡 선물: {' / '.join(ft_parts)}\n"
                msg = (
                    f"🇺🇸 미국 시장 프리마켓 브리핑\n"
                    f"{signal_header}\n"
                    f"{market_note}\n"
                    f"{spx_emoji} S&P500: {macro.spx_change_pct:+.2f}%\n"
                    f"{ndq_emoji} 나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                    f"{ft_line}"
                    f"💰 VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
                    f"💱 환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
                    f"📊 미국10년물: {macro.us10y:.2f}%\n"
                    f"🪙 BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
                    f"🥇 금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n\n"
                    f"{'━' * 22}\n"
                    f"🤖 K-Quant | {datetime.now(KST).strftime('%H:%M')}"
                )

            await context.bot.send_message(chat_id=self.chat_id, text=msg)

            # v9.5.1: 브리핑을 DB에 저장 (AI 채팅이 참조할 수 있도록)
            try:
                self.db.save_briefing("premarket", msg[:4000])
            except Exception:
                logger.debug("Failed to save premarket briefing to DB", exc_info=True)

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

    async def job_us_futures_signal(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """v5.9: 장중 미국 선물 변동 모니터링 (1시간마다).

        미국 선물/VIX가 급변하면 색깔 신호등으로 알림.
        이전 신호 대비 변동이 있을 때만 알림 발송 (스팸 방지).
        """
        if not self.chat_id:
            return
        now = datetime.now(KST)
        # 장중만 (09:00~15:30)
        if not (9 <= now.hour < 16):
            return
        if not is_kr_market_open(now.date()):
            return

        try:
            macro = await self.macro_client.get_snapshot()
            signal_emoji, signal_label = self._market_signal(macro)

            # 이전 신호와 비교
            prev = getattr(self, '_prev_us_signal', None)
            if prev == signal_label:
                return  # 변동 없으면 스킵
            self._prev_us_signal = signal_label

            # VIX 급변 체크
            vix_alert = ""
            vix_chg = macro.vix_change_pct
            if abs(vix_chg) > 5:
                vix_dir = "급등" if vix_chg > 0 else "급락"
                vix_alert = f"\n⚠️ VIX {vix_dir}: {macro.vix:.1f} ({vix_chg:+.1f}%)"

            # v9.0: 선물지수 추가
            futures_line = ""
            es = getattr(macro, "es_futures", 0)
            nq = getattr(macro, "nq_futures", 0)
            if es > 0 or nq > 0:
                parts = []
                if es > 0:
                    parts.append(f"S&P선물: {es:,.0f} ({getattr(macro, 'es_futures_change_pct', 0):+.2f}%)")
                if nq > 0:
                    parts.append(f"NQ선물: {nq:,.0f} ({getattr(macro, 'nq_futures_change_pct', 0):+.2f}%)")
                futures_line = "\n".join(parts) + "\n"

            msg = (
                f"📡 시장 신호 변경\n"
                f"{'━' * 22}\n"
                f"국내 시장 전망: {signal_emoji} {signal_label}\n\n"
                f"S&P500: {macro.spx_change_pct:+.2f}%\n"
                f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                f"{futures_line}"
                f"VIX: {macro.vix:.1f} ({vix_chg:+.1f}%)\n"
                f"환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)"
                f"{vix_alert}\n\n"
                f"{'━' * 22}\n"
                f"🤖 K-Quant | {now.strftime('%H:%M')}"
            )
            await context.bot.send_message(chat_id=self.chat_id, text=msg)
            logger.info("US futures signal changed: %s → %s", prev, signal_label)
        except Exception as e:
            logger.error("US futures signal failed: %s", e)

    async def job_daily_self_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """매일 21:00 자가진단 보고서 + 자동 업데이트 제안."""
        if not self.chat_id:
            return
        try:
            from kstock.bot.daily_self_report import generate_daily_self_report
            report = await generate_daily_self_report(self.db, self.macro_client, ws=self.ws)
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
            logger.debug("_get_self_improvement_suggestions financials check failed", exc_info=True)

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
            logger.debug("_get_self_improvement_suggestions price check failed", exc_info=True)

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
            logger.debug("_get_self_improvement_suggestions job_runs check failed", exc_info=True)

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
            logger.debug("_get_self_improvement_suggestions horizon check failed", exc_info=True)

        # 5. v3.8 건강 체크: WebSocket, LSTM, 브리핑, 단타 모니터링
        try:
            health_items = []
            if not self._surge_callback_registered:
                health_items.append("🔌 WebSocket 콜백 미등록")
            import os
            if not os.path.exists("models/lstm_stock.pt"):
                has_any_lstm = any(
                    os.path.exists(f"models/lstm_{h.get('ticker', '')}.pt")
                    for h in holdings
                ) if holdings else False
                if not has_any_lstm:
                    health_items.append("🧠 LSTM 모델 없음")
            scalp_count = len([
                h for h in holdings if h.get("holding_type") == "scalp"
            ])
            if scalp_count > 0:
                health_items.append(f"⚡ 단타 종목 {scalp_count}개 보유중")
            if health_items:
                suggestions.append(
                    "🏥 시스템 상태: " + ", ".join(health_items)
                )
        except Exception:
            logger.debug("_get_self_improvement_suggestions health check failed", exc_info=True)

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
                            logger.debug("_action_self_update fetch_financials failed for %s", h.get("ticker"), exc_info=True)
                    results.append(f"📊 재무 데이터: {collected}종목 수집 완료")
            except Exception:
                logger.debug("_action_self_update financials collection failed", exc_info=True)

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
                            logger.debug("_action_self_update get_price failed for %s", ticker, exc_info=True)
                if updated > 0:
                    results.append(f"💰 현재가 갱신: {updated}종목 완료")
            except Exception:
                logger.debug("_action_self_update price update failed", exc_info=True)

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
                logger.debug("_action_self_update horizon set failed", exc_info=True)

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

    async def job_dart_check(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """08:30 평일: 보유/관심종목 공시 체크."""
        try:
            from kstock.ingest.dart_client import DartClient
            dart = DartClient()
            if not dart.available:
                logger.debug("DART API key not set, skipping")
                return

            holdings = self.db.get_active_holdings()
            watchlist = self.db.get_watchlist() if hasattr(self.db, "get_watchlist") else []

            # 종목명 → ticker 매핑
            name_to_ticker = {}
            for h in holdings:
                name = h.get("name", "")
                ticker = h.get("ticker", "")
                if name and ticker:
                    name_to_ticker[name] = ticker
            for w in watchlist:
                name = w.get("name", "")
                ticker = w.get("ticker", "")
                if name and ticker:
                    name_to_ticker[name] = ticker

            disclosures = await dart.get_today_disclosures()
            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            saved = 0
            alerts = []

            for d in disclosures:
                corp_name = d.get("corp_name", "")
                # 공시 기업명이 보유/관심종목에 있는지 확인
                ticker = name_to_ticker.get(corp_name)
                if not ticker:
                    continue
                title = d.get("report_nm", "")
                rcept_no = d.get("rcept_no", "")
                url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
                self.db.add_dart_event(
                    ticker=ticker, date=today_str,
                    title=title, url=url,
                )
                saved += 1
                alerts.append(f"  \u2192 {corp_name}: {title}")

            if alerts and self.chat_id:
                msg = (
                    f"\U0001f4e2 공시 알림 ({today_str})\n"
                    f"\u2500" * 22 + "\n\n"
                    + "\n".join(alerts[:10])
                )
                await context.bot.send_message(chat_id=self.chat_id, text=msg)

            self.db.upsert_job_run("dart_check", today_str, status="success")
            logger.info("DART check: %d events saved", saved)
        except Exception as e:
            logger.error("DART check failed: %s", e)
            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            self.db.upsert_job_run("dart_check", today_str, status="error", message=str(e))

    async def job_supply_demand_collect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """16:10 평일: 보유종목 수급 데이터 수집."""
        try:
            holdings = self.db.get_active_holdings()
            tickers = [h.get("ticker", "") for h in holdings if h.get("ticker")]
            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            collected = 0

            for ticker in tickers[:20]:
                try:
                    frgn = await self.kis.get_foreign_flow(ticker, days=1)
                    inst = await self.kis.get_institution_flow(ticker, days=1)

                    # v9.3.3: 빈 DataFrame 방어 (KIS 미설정 시 빈 DF 반환)
                    frgn_net = 0
                    inst_net = 0

                    if not frgn.empty and "net_buy_volume" in frgn.columns:
                        frgn_net = int(frgn.iloc[0]["net_buy_volume"])
                    if not inst.empty and "net_buy_volume" in inst.columns:
                        inst_net = int(inst.iloc[0]["net_buy_volume"])

                    if frgn_net != 0 or inst_net != 0:
                        self.db.add_supply_demand(
                            ticker=ticker,
                            date_str=today_str,
                            foreign_net=frgn_net,
                            institution_net=inst_net,
                        )
                        collected += 1
                except Exception as e:
                    logger.debug("Supply demand collect failed for %s: %s", ticker, e)

            self.db.upsert_job_run("supply_demand_collect", today_str, status="success")
            logger.info("Supply demand collected for %d tickers", collected)
        except Exception as e:
            logger.error("Supply demand collect failed: %s", e)
            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            self.db.upsert_job_run(
                "supply_demand_collect", today_str, status="error", message=str(e),
            )

    async def job_program_trading_collect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """16:15 평일: 프로그램 매매 데이터 수집 (네이버 금융)."""
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            from kstock.ingest.program_trading import fetch_program_trading

            data = await asyncio.to_thread(
                fetch_program_trading, None, "KOSPI", 3
            )
            saved = 0
            for item in data:
                self.db.save_program_trading({
                    "date": item.date,
                    "market": item.market,
                    "arb_buy": item.arb_buy,
                    "arb_sell": item.arb_sell,
                    "arb_net": item.arb_net,
                    "non_arb_buy": item.non_arb_buy,
                    "non_arb_sell": item.non_arb_sell,
                    "non_arb_net": item.non_arb_net,
                    "total_buy": item.total_buy,
                    "total_sell": item.total_sell,
                    "total_net": item.total_net,
                })
                saved += 1

            self.db.upsert_job_run("program_trading_collect", today_str, status="success")
            logger.info("Program trading collected: %d records", saved)
        except Exception as e:
            logger.error("Program trading collect failed: %s", e)
            self.db.upsert_job_run(
                "program_trading_collect", today_str, status="error", message=str(e),
            )

    async def job_credit_balance_collect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """16:20 평일: 신용잔고/고객예탁금 수집 (네이버 금융)."""
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            from kstock.ingest.credit_balance import fetch_credit_balance

            data = await asyncio.to_thread(fetch_credit_balance, 2)
            saved = 0
            for item in data:
                self.db.save_credit_balance({
                    "date": item.date,
                    "deposit": item.deposit,
                    "deposit_change": item.deposit_change,
                    "credit": item.credit,
                    "credit_change": item.credit_change,
                })
                saved += 1

            self.db.upsert_job_run("credit_balance_collect", today_str, status="success")
            logger.info("Credit balance collected: %d records", saved)
        except Exception as e:
            logger.error("Credit balance collect failed: %s", e)
            self.db.upsert_job_run(
                "credit_balance_collect", today_str, status="error", message=str(e),
            )

    async def job_etf_flow_collect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """16:25 평일: ETF 자금흐름 수집 (네이버 금융 API)."""
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            from kstock.signal.etf_flow import fetch_etf_flow

            data = await asyncio.to_thread(fetch_etf_flow)
            saved = 0
            for item in data:
                self.db.save_etf_flow({
                    "date": today_str,
                    "code": item.code,
                    "name": item.name,
                    "etf_type": item.etf_type,
                    "price": item.price,
                    "change_pct": item.change_pct,
                    "nav": item.nav,
                    "market_cap": item.market_cap,
                    "volume": item.volume,
                })
                saved += 1

            self.db.upsert_job_run("etf_flow_collect", today_str, status="success")
            logger.info("ETF flow collected: %d records", saved)
        except Exception as e:
            logger.error("ETF flow collect failed: %s", e)
            self.db.upsert_job_run(
                "etf_flow_collect", today_str, status="error", message=str(e),
            )

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
        if not is_kr_market_open(now.date()):
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

    async def job_manager_reflection(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """#6 매니저 주간 자기반성 보고서 — Sunday 19:30 KST."""
        if not self.chat_id:
            return
        try:
            import asyncio
            from kstock.bot.investment_managers import (
                generate_manager_reflection,
                suggest_threshold_adjustment,
                format_threshold_suggestions,
                MANAGERS,
            )

            reflections = []
            threshold_suggestions = {}

            for mgr_key in MANAGERS:
                perf = self.db.get_manager_performance(mgr_key, days=30)
                lessons = self.db.get_trade_lessons_by_manager(mgr_key, limit=5)
                reflection = await generate_manager_reflection(mgr_key, perf, lessons)
                if reflection:
                    reflections.append(reflection)

                # #10 임계값 자동 조정 제안
                sugg = suggest_threshold_adjustment(mgr_key, perf)
                if sugg:
                    threshold_suggestions[mgr_key] = sugg

            for ref_text in reflections:
                await context.bot.send_message(
                    chat_id=self.chat_id, text=ref_text[:4000],
                )

            # 임계값 조정 제안
            if threshold_suggestions:
                sugg_text = format_threshold_suggestions(threshold_suggestions)
                if sugg_text:
                    await context.bot.send_message(
                        chat_id=self.chat_id, text=sugg_text[:2000],
                    )

            self.db.upsert_job_run("manager_reflection", _today(), status="success")
            logger.info("Manager reflection reports sent")
        except Exception as e:
            logger.error("Manager reflection error: %s", e, exc_info=True)

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

    # == 증권사 리포트 자동 수집 (v3.6.2) =====================================

    async def job_report_crawl(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """매일 08:20 증권사 리포트 자동 수집 → DB 저장."""
        try:
            from kstock.ingest.report_crawler import crawl_all_reports

            stats = await crawl_all_reports(
                self.db, company_pages=3, industry_pages=2,
            )
            total = stats.get("total_new", 0)
            if total > 0 and self.chat_id:
                msg = (
                    f"📋 증권사 리포트 자동 수집 완료\n"
                    f"종목분석: {stats['company']}건 | "
                    f"산업분석: {stats['industry']}건\n"
                    f"신규 저장: {total}건"
                )
                await context.bot.send_message(chat_id=self.chat_id, text=msg)
            self.db.upsert_job_run("report_crawl", _today(), status="success",
                                   message=f"new={total}")
            logger.info("Report crawl done: %s", stats)
        except Exception as e:
            logger.error("Report crawl job failed: %s", e, exc_info=True)
            self.db.upsert_job_run("report_crawl", _today(), status="error",
                                   message=str(e))

    # == KIS WebSocket Jobs ====================================================

    async def job_ws_connect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """장 시작 전 WebSocket 연결 + 보유종목 구독."""
        # 이미 연결되어 있으면 스킵
        if self.ws.is_connected:
            return

        # 장중 시간 체크 (평일 08:50~15:35)
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):  # 주말
            return

        try:
            ok = await self.ws.connect()
            if not ok:
                logger.warning("WebSocket connection failed")
                return

            # 보유종목 + 전체 유니버스 구독
            tickers_to_sub: set[str] = set()

            # 1. 보유종목 (최우선)
            holdings = self.db.get_active_holdings()
            for h in holdings:
                ticker = h.get("ticker", "")
                if ticker and len(ticker) == 6:
                    tickers_to_sub.add(ticker)

            # 2. 전체 유니버스
            for item in self.all_tickers:
                code = item.get("code", "")
                if code:
                    tickers_to_sub.add(code)

            subscribed = 0
            for ticker in tickers_to_sub:
                await self.ws.subscribe(ticker)
                subscribed += 1

            # 급등 감지 + 매도 가이드 콜백 등록 (최초 1회)
            if not self._surge_callback_registered:
                self.ws.on_update(self._on_realtime_update)
                self._surge_callback_registered = True
                # 보유종목 캐시 초기화 — 동시성 보호
                _init_holdings = self.db.get_active_holdings()
                async with self._ensure_lock():
                    self._holdings_cache = _init_holdings
                    self._holdings_index = {
                        h.get("ticker", ""): h
                        for h in self._holdings_cache if h.get("ticker")
                    }
                logger.info("Realtime surge/sell-guide callback registered")

            logger.info("WebSocket connected: %d tickers subscribed", subscribed)

        except Exception as e:
            logger.error("WebSocket connect job failed: %s", e)
            if self.chat_id:
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text="\u26a0\ufe0f 실시간 시세 연결 실패\nWebSocket 연결에 문제가 있습니다.",
                )

    async def job_ws_disconnect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """장 종료 후 WebSocket 연결 해제."""
        if not self.ws.is_connected:
            return
        try:
            subs = len(self.ws.get_subscriptions())
            await self.ws.disconnect()
            logger.info("WebSocket disconnected (%d subs)", subs)
        except Exception as e:
            logger.error("WebSocket disconnect job failed: %s", e)

    # == Realtime WebSocket: 급등 감지 + 매도 가이드 ========================

    def _on_realtime_update(self, event_type: str, ticker: str, data) -> None:
        """KIS WebSocket 실시간 업데이트 콜백. 동기 함수."""
        if event_type != "price":
            return

        now = _time.time()
        now_kst = datetime.now(KST)

        # 장중 시간 체크 (09:00 ~ 15:20)
        if now_kst.hour < 9 or (now_kst.hour >= 15 and now_kst.minute > 20):
            return

        # 이벤트 루프에서 비동기 태스크 안전하게 실행
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # 1. 급등 감지 (+3% 이상)
        change_pct = getattr(data, 'change_pct', 0)
        if change_pct >= self._SURGE_THRESHOLD_PCT:
            last_alert = self._surge_cooldown.get(f"surge:{ticker}", 0)
            if now - last_alert >= self._SURGE_COOLDOWN_SEC:
                self._surge_cooldown[f"surge:{ticker}"] = now
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(
                        asyncio.ensure_future,
                        self._send_surge_alert(ticker, data),
                    )
                else:
                    try:
                        asyncio.ensure_future(
                            self._send_surge_alert(ticker, data),
                        )
                    except RuntimeError:
                        logger.debug("on_ws_price_update no running event loop for surge alert")

        # 2. 보유종목 목표가/손절가 체크
        self._check_sell_targets(ticker, data, now, loop)

    async def _send_surge_alert(self, ticker: str, data) -> None:
        """급등 감지 알림 발송."""
        if not self.chat_id or not hasattr(self, '_application'):
            return
        try:
            # 종목명 조회
            name = ticker
            for item in self.all_tickers:
                if item.get("code") == ticker:
                    name = item.get("name", ticker)
                    break

            # 보유 여부
            is_held = ticker in self._holdings_index

            # 스캔 캐시에서 스코어 확인
            score_info = ""
            if getattr(self, '_last_scan_results', None):
                for r in self._last_scan_results:
                    if r.ticker == ticker:
                        if r.score.composite < 50:
                            logger.debug("Surge skipped (low score): %s", ticker)
                            return
                        score_info = (
                            f"📊 스코어: {r.score.composite:.0f}점 | "
                            f"RSI: {r.tech.rsi:.0f}"
                        )
                        break

            held_tag = "📦 보유중" if is_held else "🆕 미보유"
            pressure = getattr(data, 'pressure', '중립')
            change_pct = getattr(data, 'change_pct', 0)
            price = getattr(data, 'price', 0)

            text = (
                f"🚀 급등 감지: {name} ({ticker})\n\n"
                f"현재가: {price:,.0f}원 ({change_pct:+.1f}%)\n"
                f"매수세: {pressure}\n"
                f"{score_info}\n"
                f"{held_tag}"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔍 상세분석", callback_data=f"detail:{ticker}",
                    ),
                    InlineKeyboardButton(
                        "⭐ 즐겨찾기",
                        callback_data=f"fav:add:{ticker}:{name}",
                    ),
                ],
            ])

            await self._application.bot.send_message(
                chat_id=self.chat_id, text=text, reply_markup=keyboard,
            )
            logger.info("Surge alert: %s %+.1f%%", ticker, change_pct)
        except Exception as e:
            logger.error("Surge alert error %s: %s", ticker, e)

    def _check_sell_targets(
        self, ticker: str, data, now: float, loop=None,
    ) -> None:
        """보유종목 목표가/손절가 도달 여부 확인. O(1) ticker 조회."""
        self.__init_scheduler_state__()
        h = self._holdings_index.get(ticker)
        if not h:
            return

        buy_price = h.get("buy_price", 0)
        if buy_price <= 0:
            return

        price = getattr(data, 'price', 0)
        if price <= 0:
            return

        # 사용자가 뮤트한 종목이면 무시
        mute_until = self._muted_tickers.get(ticker, 0)
        if now < mute_until:
            return

        change_from_buy = (price - buy_price) / buy_price * 100
        holding_type = h.get("holding_type", "auto")
        name = h.get("name", ticker)

        # 쿨다운 (24시간)
        alert_key = f"sell:{ticker}"
        if now - self._surge_cooldown.get(alert_key, 0) < self._SELL_TARGET_COOLDOWN_SEC:
            return

        # holding_type별 목표가/손절가
        targets = {
            "scalp":     {"target": 3.0,  "stop": -2.0},
            "swing":     {"target": 5.0,  "stop": -3.0},
            "position":  {"target": 12.0, "stop": -7.0},
            "long_term": {"target": 20.0, "stop": -10.0},
            "auto":      {"target": 5.0,  "stop": -3.0},
        }
        t = targets.get(holding_type, targets["auto"])

        alert_type = None
        if change_from_buy >= t["target"]:
            alert_type = "target"
        elif change_from_buy <= t["stop"]:
            alert_type = "stop"

        if alert_type:
            self._surge_cooldown[alert_key] = now
            coro = self._send_sell_guide(
                name, ticker, price, buy_price,
                change_from_buy, alert_type, holding_type,
            )
            if loop and loop.is_running():
                loop.call_soon_threadsafe(asyncio.ensure_future, coro)
            else:
                try:
                    asyncio.ensure_future(coro)
                except RuntimeError:
                    logger.debug("_check_sell_targets no running event loop for sell guide")

    async def _send_sell_guide(
        self, name: str, ticker: str, current_price: float,
        buy_price: float, change_pct: float,
        alert_type: str, holding_type: str,
    ) -> None:
        """매도 가이드 알림 (무시/뮤트 버튼 포함)."""
        if not self.chat_id or not hasattr(self, '_application'):
            return

        from kstock.bot.investment_managers import get_manager_label
        mgr_label = get_manager_label(holding_type)

        if alert_type == "target":
            emoji, title = "🎯", "목표가 도달"
            action = "수익 실현을 검토해보세요"
        else:
            emoji, title = "🔴", "손절가 도달"
            action = "포지션 정리를 검토해보세요"

        text = (
            f"{emoji} {title}: {name} ({ticker})\n\n"
            f"현재가: {current_price:,.0f}원 ({change_pct:+.1f}%)\n"
            f"매수가: {buy_price:,.0f}원\n"
            f"담당: {mgr_label}\n\n"
            f"💡 {action}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔍 상세분석", callback_data=f"detail:{ticker}",
                ),
                InlineKeyboardButton(
                    "🔇 24시간 무시", callback_data=f"mute:24h:{ticker}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔕 이 종목 알림 끄기", callback_data=f"mute:off:{ticker}",
                ),
            ],
        ])

        try:
            await self._application.bot.send_message(
                chat_id=self.chat_id, text=text, reply_markup=keyboard,
            )
            logger.info("Sell guide: %s %s %.1f%%", ticker, alert_type, change_pct)
        except Exception as e:
            logger.error("Sell guide error: %s", e)

    async def _action_mute_alert(self, query, context, payload: str) -> None:
        """mute:{duration}:{ticker} 콜백 처리. 알림 뮤트."""
        self.__init_scheduler_state__()
        duration, _, ticker = payload.partition(":")
        import time
        now = time.time()

        if duration == "24h":
            self._muted_tickers[ticker] = now + 86400  # 24시간
            await query.edit_message_text(
                f"🔇 {ticker} 매도 알림을 24시간 동안 무시합니다.\n"
                f"내일 이 시간 이후 다시 알림이 올 수 있습니다."
            )
            logger.info("Muted sell alert: %s for 24h", ticker)
        elif duration == "off":
            self._muted_tickers[ticker] = now + 86400 * 365  # 사실상 영구
            await query.edit_message_text(
                f"🔕 {ticker} 매도 알림을 끕니다.\n"
                f"종목을 매도하거나 봇을 재시작하면 다시 활성화됩니다."
            )
            logger.info("Muted sell alert: %s permanently", ticker)

    async def job_scalp_close_reminder(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """14:30 초단기 보유종목 청산 리마인더."""
        if not self.chat_id:
            return

        holdings = self.db.get_active_holdings()
        scalp_holdings = [h for h in holdings if h.get("holding_type") == "scalp"]
        if not scalp_holdings:
            return

        lines = ["⏰ 초단기 종목 청산 점검 (14:30)\n"]
        for h in scalp_holdings:
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            buy_price = h.get("buy_price", 0)
            rt = self.ws.get_price(ticker) if self.ws.is_connected else None
            if rt and buy_price > 0:
                pnl = (rt.price - buy_price) / buy_price * 100
                lines.append(
                    f"  {name}: {rt.price:,.0f}원 ({pnl:+.1f}%)"
                )
            else:
                lines.append(f"  {name}: 실시간 가격 미수신")

        lines.append("\n💡 당일 청산 전제. 오버나잇 리스크 유의.")
        await context.bot.send_message(
            chat_id=self.chat_id, text="\n".join(lines),
        )

    async def job_short_term_review(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """단기 보유종목 3거래일 경과 + 수익률 미달 점검."""
        if not self.chat_id:
            return

        holdings = self.db.get_active_holdings()
        now = datetime.now(KST)
        alerts = []

        for h in holdings:
            if h.get("holding_type") != "swing":
                continue
            buy_date_str = h.get("buy_date") or h.get("created_at", "")
            if not buy_date_str:
                continue
            try:
                buy_date = datetime.fromisoformat(buy_date_str[:10])
            except (ValueError, TypeError):
                logger.debug("_check_swing_holding_period invalid buy_date for %s", h.get("ticker"), exc_info=True)
                continue

            days_held = (now.date() - buy_date.date()).days
            if days_held < 4:
                continue

            buy_price = h.get("buy_price", 0)
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            current_price = 0
            rt = self.ws.get_price(ticker) if self.ws.is_connected else None
            if rt:
                current_price = rt.price
            if current_price > 0 and buy_price > 0:
                pnl = (current_price - buy_price) / buy_price * 100
                if pnl < 3.0:
                    alerts.append(
                        f"  {name}: {current_price:,.0f}원 "
                        f"({pnl:+.1f}%) [{days_held}일 보유]"
                    )

        if not alerts:
            return

        text = (
            "📋 단기 종목 검토 알림\n\n"
            "3거래일 경과 + 수익률 3% 미만:\n"
            + "\n".join(alerts)
            + "\n\n💡 본전 매도를 검토해보세요\n"
            "📊 자금이 묶여 있는 시간도 비용입니다 (기회비용)"
        )
        await context.bot.send_message(chat_id=self.chat_id, text=text)

    async def job_lstm_retrain(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매주 일요일 03:00 ML 전체 자동 재학습 (AutoTrainer v4.0).

        v4.0: AutoTrainer → LGB+XGB+LSTM 통합 학습 + 가중치 최적화.
        기존 개별 LSTM 학습 → 통합 파이프라인으로 대체.
        """
        try:
            from kstock.ml.auto_trainer import AutoTrainer

            trainer = AutoTrainer(db=self.db, yf_client=self.yf_client)

            # 1. 드리프트 체크 → 트리거 결정
            drift = trainer.should_retrain()
            trigger = "drift" if drift.is_drifting else "scheduled"

            # 2. 자동 재학습 실행
            result = await trainer.run_auto_train(trigger=trigger)

            # 3. 결과 알림
            if self.chat_id:
                msg = result.message or (
                    "🧠 ML 재학습 완료" if result.success else "❌ ML 재학습 실패"
                )
                await context.bot.send_message(chat_id=self.chat_id, text=msg)

            self.db.upsert_job_run(
                "lstm_retrain", _today(),
                status="success" if result.success else "error",
            )
            logger.info("ML auto-train %s: %s", trigger, "OK" if result.success else "FAIL")

        except Exception as e:
            logger.error("ML auto-train job error: %s", e, exc_info=True)
            try:
                self.db.upsert_job_run("lstm_retrain", _today(), status="error")
            except Exception:
                logger.debug("job_ml_auto_train upsert_job_run also failed", exc_info=True)

    async def job_risk_monitor(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """실시간 포트폴리오 리스크 + 차익실현 모니터링 (매 5분).

        v4.2: 알림 빈도 최적화
        - 리스크/집중도 경고 → 장 마감(EOD) 리포트에 통합 (1일 1회)
        - 손절/트레일링 스탑 → 긴급만 즉시 발송
        - 차익실현 알림 → 1일 1회 (종목별)
        - 트레일링 스탑 고점 추적 → 매 5분 (알림 없이 백그라운드)
        """
        if not self.chat_id:
            return
        try:
            from kstock.core.position_sizer import PositionSizer

            holdings = self.db.get_active_holdings()
            if not holdings or len(holdings) < 1:
                return

            # 현재 포트폴리오 가치 계산
            total_value = 0.0
            for h in holdings:
                cp = h.get("current_price", 0) or h.get("buy_price", 0)
                qty = h.get("quantity", 1)
                total_value += cp * qty

            if total_value <= 0:
                return

            # PositionSizer 인스턴스 (세션 내 유지)
            if not hasattr(self, '_position_sizer'):
                self._position_sizer = PositionSizer(account_value=total_value)
            else:
                self._position_sizer.account_value = total_value

            sizer = self._position_sizer

            # === 백그라운드: 트레일링 스탑 고점 추적 (알림 없음) ===
            for h in holdings:
                ticker = h.get("ticker", "")
                buy_price = h.get("buy_price", 0)
                current_price = h.get("current_price", 0)
                holding_type = h.get("holding_type", "auto")
                if buy_price > 0 and current_price > 0:
                    sizer._update_trailing_stop(
                        ticker, current_price, buy_price, holding_type,
                    )

            # === 긴급 알림만 즉시 발송: 손절 + 트레일링 스탑 발동 ===
            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                buy_price = h.get("buy_price", 0)
                current_price = h.get("current_price", 0)
                quantity = h.get("quantity", 1)
                holding_type = h.get("holding_type", "auto")
                sold_pct = h.get("sold_pct", 0) or 0

                if buy_price <= 0 or current_price <= 0:
                    continue

                alert = sizer.check_profit_taking(
                    ticker=ticker, name=name,
                    buy_price=buy_price,
                    current_price=current_price,
                    quantity=quantity,
                    holding_type=holding_type,
                    sold_pct=sold_pct / 100 if sold_pct > 1 else sold_pct,
                )

                # 손절/트레일링 스탑만 즉시 발송
                # v6.6: 워타임 시 쿨다운 4시간, 평상시 24시간
                if alert and alert.alert_type in ("stop_loss", "trailing_stop"):
                    _sl_cooldown_hours = 4 if getattr(self, '_alert_mode', 'normal') == 'wartime' else 24
                    if not self.db.has_recent_alert(
                        ticker, f"profit_{alert.alert_type}", hours=_sl_cooldown_hours,
                    ):
                        self.db.insert_alert(
                            ticker, f"profit_{alert.alert_type}",
                            alert.message[:200],
                        )

                        # v6.2: 스마트 알림 (이유+액션 포함)
                        pnl_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                        smart_msg = None
                        try:
                            from kstock.bot.smart_alerts import build_holding_alert
                            # 시장 레짐 확인
                            _regime = ""
                            try:
                                _macro = await self.macro_client.get_snapshot()
                                if _macro and hasattr(_macro, "vix"):
                                    if _macro.vix >= 30: _regime = "panic"
                                    elif _macro.vix >= 25: _regime = "fear"
                                    elif _macro.vix >= 18: _regime = "normal"
                                    else: _regime = "calm"
                            except Exception:
                                pass

                            # 보유일수 계산
                            _hold_days = 0
                            try:
                                bd = h.get("buy_date") or h.get("created_at", "")
                                if bd:
                                    _bd_dt = datetime.strptime(bd[:10], "%Y-%m-%d")
                                    _hold_days = max(0, (datetime.utcnow() - _bd_dt).days)
                            except Exception:
                                pass

                            smart_msg = build_holding_alert(
                                name=name, ticker=ticker,
                                pnl_pct=pnl_pct,
                                buy_price=buy_price,
                                current_price=current_price,
                                holding_type=holding_type,
                                hold_days=_hold_days,
                                market_regime=_regime,
                            )
                        except Exception:
                            pass

                        alert_text = smart_msg if smart_msg else sizer.format_profit_alert(alert)

                        # v6.5: 장기보유 보호 시 "확인" 버튼만 표시
                        if smart_msg and "장기보유 보호" in smart_msg:
                            buttons = [
                                [
                                    InlineKeyboardButton(
                                        "✅ 확인",
                                        callback_data=f"pt:ignore:{alert.ticker}",
                                    ),
                                ],
                            ]
                        else:
                            buttons = [
                                [
                                    InlineKeyboardButton(
                                        "🔴 매도" if alert.alert_type == "stop_loss" else "⚠️ 매도",
                                        callback_data=f"pt:sell:{alert.ticker}:{alert.sell_shares}",
                                    ),
                                    InlineKeyboardButton(
                                        "💎 홀드",
                                        callback_data=f"pt:ignore:{alert.ticker}",
                                    ),
                                ],
                            ]
                        await context.bot.send_message(
                            chat_id=self.chat_id,
                            text=alert_text,
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        logger.info(
                            "Urgent alert: %s %s (%+.1f%%)",
                            alert.name, alert.alert_type, alert.pnl_pct,
                        )

            logger.debug("Risk monitor: trailing stop tracking updated")

        except Exception as e:
            logger.debug("Risk monitor error: %s", e)

    async def job_eod_risk_report(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """장 마감 리스크 + 차익실현 종합 리포트 (1일 1회, 15:40).

        v4.2: 기존 5분마다 반복되던 경고를 장 마감 1회로 통합.
        - 포트폴리오 집중도 분석
        - 리스크 위반 (MDD, 일간 손실)
        - 차익실현 알림 (+50%, +100%)
        - 트레일링 스탑 현황
        """
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
            return
        try:
            from kstock.core.risk_manager import (
                calculate_mdd, RISK_LIMITS,
            )
            from kstock.core.position_sizer import (
                PositionSizer, format_concentration_warnings,
            )

            holdings = self.db.get_active_holdings()
            if not holdings:
                return

            # 포트폴리오 가치
            total_value = 0.0
            for h in holdings:
                cp = h.get("current_price", 0) or h.get("buy_price", 0)
                qty = h.get("quantity", 1)
                total_value += cp * qty

            if total_value <= 0:
                return

            if not hasattr(self, '_position_sizer'):
                self._position_sizer = PositionSizer(account_value=total_value)
            else:
                self._position_sizer.account_value = total_value
            sizer = self._position_sizer

            lines = [
                "🛡️ 장 마감 리스크 리포트",
                "━" * 22,
                "",
                f"💰 포트폴리오: {total_value:,.0f}원",
                "",
            ]

            has_issues = False

            # === 1. 종목/섹터 집중도 ===
            weights = {}
            for h in holdings:
                cp = h.get("current_price", 0) or h.get("buy_price", 0)
                qty = h.get("quantity", 1)
                w = (cp * qty) / total_value if total_value > 0 else 0
                weights[h.get("ticker", "")] = w

            conc_issues = []
            for h in holdings:
                ticker = h.get("ticker", "")
                w = weights.get(ticker, 0)
                name = h.get("name", ticker)
                if w > 0.50:
                    conc_issues.append(
                        f"  🚨 {name} 비중 {w*100:.1f}% (긴급 한도 50% 초과)"
                    )
                elif w > 0.30:
                    conc_issues.append(
                        f"  ⚠️ {name} 비중 {w*100:.1f}% (경고 한도 30% 초과)"
                    )

            # 섹터 집중도
            conc_holdings = [
                {
                    "ticker": h.get("ticker", ""),
                    "name": h.get("name", ""),
                    "eval_amount": (
                        (h.get("current_price", 0) or h.get("buy_price", 0))
                        * h.get("quantity", 1)
                    ),
                }
                for h in holdings
            ]
            sector_warnings = sizer.analyze_concentration(conc_holdings)

            if conc_issues or sector_warnings:
                has_issues = True
                lines.append("📊 집중도 분석")
                lines.extend(conc_issues)
                for sw in sector_warnings:
                    if "섹터" in sw:
                        lines.append(f"  {sw}")
                lines.append("")

            # === 2. MDD / 일간 손실 ===
            risk_issues = []
            try:
                snapshots = self.db.get_portfolio_snapshots(days=30)
                if snapshots and len(snapshots) >= 2:
                    peak = max(s.get("total_value", 0) for s in snapshots)
                    if peak > 0:
                        mdd = calculate_mdd(total_value, peak)
                        if mdd < RISK_LIMITS.get("max_portfolio_mdd", -0.15):
                            risk_issues.append(
                                f"  📉 MDD {mdd*100:.1f}% "
                                f"(한도 {RISK_LIMITS['max_portfolio_mdd']*100:.0f}%)"
                            )
                        if mdd < RISK_LIMITS.get("emergency_mdd", -0.20):
                            risk_issues.append(
                                "  🚨 긴급: MDD 20% 초과 — 전량 매도 검토"
                            )
            except Exception:
                logger.debug("job_risk_monitor MDD calculation failed", exc_info=True)

            for h in holdings:
                pnl = h.get("pnl_pct", 0) or 0
                if pnl < -5.0:
                    risk_issues.append(
                        f"  🔴 {h['name']}: {pnl:+.1f}% (일간 손실 한도 초과)"
                    )

            if risk_issues:
                has_issues = True
                lines.append("🚨 리스크 위반")
                lines.extend(risk_issues)
                lines.append("")

            # === 3. 차익실현 대상 ===
            profit_items = []
            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                buy_price = h.get("buy_price", 0)
                current_price = h.get("current_price", 0)
                quantity = h.get("quantity", 1)
                holding_type = h.get("holding_type", "auto")
                sold_pct = h.get("sold_pct", 0) or 0

                if buy_price <= 0 or current_price <= 0:
                    continue

                alert = sizer.check_profit_taking(
                    ticker=ticker, name=name,
                    buy_price=buy_price,
                    current_price=current_price,
                    quantity=quantity,
                    holding_type=holding_type,
                    sold_pct=sold_pct / 100 if sold_pct > 1 else sold_pct,
                )
                if alert and alert.alert_type.startswith("stage"):
                    pnl_pct = (current_price - buy_price) / buy_price * 100
                    profit_items.append(
                        f"  {alert.name}: +{pnl_pct:.0f}% → {alert.action} "
                        f"({alert.sell_shares}주)"
                    )

            if profit_items:
                has_issues = True
                lines.append("💰 차익실현 대상")
                lines.extend(profit_items)
                lines.append("")

            # === 4. 트레일링 스탑 현황 ===
            trail_items = []
            for ticker, state in sizer.get_all_trailing_states().items():
                if state.is_active:
                    name = next(
                        (h["name"] for h in holdings if h.get("ticker") == ticker),
                        ticker,
                    )
                    trail_items.append(
                        f"  {name}: 고점 {state.high_price:,.0f}원 "
                        f"→ 스탑 {state.stop_price:,.0f}원 "
                        f"(-{state.trail_pct*100:.0f}%)"
                    )

            if trail_items:
                lines.append("📈 트레일링 스탑 활성")
                lines.extend(trail_items)
                lines.append("")

            # === 5. 고급 리스크 분석 (VaR + 동적 상관관계) ===
            try:
                from kstock.core.advanced_risk import (
                    compute_advanced_var,
                    compute_dynamic_correlation,
                    format_risk_report,
                )
                import pandas as pd
                import numpy as np

                # 보유 종목의 OHLCV 데이터로 수익률 행렬 구성
                tickers_held = [h.get("ticker", "") for h in holdings if h.get("ticker")]
                returns_data = {}
                for t in tickers_held[:10]:  # 상위 10종목만 (성능)
                    try:
                        ohlcv = self.db.get_ohlcv(t, days=120)
                        if ohlcv and len(ohlcv) >= 30:
                            closes = [row.get("close", 0) for row in ohlcv]
                            rets = np.diff(np.log(np.array(closes, dtype=float)))
                            returns_data[t] = rets
                    except Exception:
                        continue

                if len(returns_data) >= 2:
                    # 수익률 행렬 구성
                    min_len = min(len(v) for v in returns_data.values())
                    rm_dict = {t: v[-min_len:] for t, v in returns_data.items()}
                    rm = pd.DataFrame(rm_dict)

                    # 포트폴리오 가중치
                    port_weights = {}
                    for h in holdings:
                        t = h.get("ticker", "")
                        if t in returns_data:
                            cp = h.get("current_price", 0) or h.get("buy_price", 0)
                            qty = h.get("quantity", 1)
                            port_weights[t] = (cp * qty) / total_value if total_value > 0 else 0

                    # VaR 계산 (Cornish-Fisher 보정)
                    var_result = compute_advanced_var(
                        rm, port_weights,
                        confidence=0.95, horizon=1,
                        method="parametric",
                    )

                    if var_result.var_pct > 0:
                        has_issues = True
                        lines.append("📉 고급 VaR 분석")
                        lines.append(f"  1일 VaR(95%): {var_result.var_pct:.2f}%")
                        lines.append(f"  CVaR: {var_result.cvar_pct:.2f}%")
                        # Component VaR 상위 3
                        if var_result.component_var:
                            sorted_cv = sorted(
                                var_result.component_var.items(),
                                key=lambda x: -x[1],
                            )[:3]
                            for t, cv in sorted_cv:
                                n = next(
                                    (h["name"] for h in holdings if h.get("ticker") == t),
                                    t,
                                )
                                lines.append(f"    {n}: {cv:.2f}%")
                        lines.append("")

                    # 상위 보유종목 간 동적 상관관계
                    top_tickers = sorted(
                        port_weights.items(), key=lambda x: -x[1],
                    )[:3]
                    if len(top_tickers) >= 2:
                        corr_items = []
                        for i in range(len(top_tickers)):
                            for j in range(i + 1, len(top_tickers)):
                                t_a, _ = top_tickers[i]
                                t_b, _ = top_tickers[j]
                                if t_a in rm.columns and t_b in rm.columns:
                                    dc = compute_dynamic_correlation(
                                        rm[t_a].values, rm[t_b].values,
                                        ticker_a=t_a, ticker_b=t_b,
                                    )
                                    regime_emoji = {"normal": "🟢", "stress": "🟡", "crisis": "🔴"}.get(dc.regime, "⚪")
                                    n_a = next((h["name"] for h in holdings if h.get("ticker") == t_a), t_a)
                                    n_b = next((h["name"] for h in holdings if h.get("ticker") == t_b), t_b)
                                    corr_items.append(
                                        f"  {n_a}-{n_b}: {dc.rolling_60d:.2f} {regime_emoji}"
                                    )
                        if corr_items:
                            lines.append("🔗 종목간 상관관계")
                            lines.extend(corr_items)
                            lines.append("")

            except Exception as e:
                logger.debug("Advanced risk in EOD report: %s", e)

            # === 6. VIX 리스크 정책 현황 ===
            try:
                from kstock.core.risk_policy import vix_adjusted_policy
                _macro = await self.macro_client.get_snapshot()
                if _macro and hasattr(_macro, "vix") and _macro.vix > 0:
                    policy = vix_adjusted_policy(_macro.vix)
                    lines.append(f"📋 리스크 정책: {policy['regime_label']}")
                    lines.append(f"  VIX: {_macro.vix:.1f}")
                    lines.append(f"  종목 한도: {policy['max_single_weight']*100:.0f}%")
                    lines.append(f"  현금 바닥: {policy['cash_floor_pct']}%")
                    if not policy["new_buy_allowed"]:
                        lines.append("  🚫 신규 매수 차단 중")
                    lines.append("")
            except Exception as e:
                logger.debug("VIX policy in EOD report: %s", e)

            # === 발송 ===
            if not has_issues and not trail_items:
                lines.append("✅ 리스크 위반 없음. 포트폴리오 정상.")
                lines.append("")

            lines.append("주호님, 안전한 투자 되세요.")

            # 차익실현 대상이 있으면 버튼 추가
            keyboard = None
            if profit_items:
                buttons = []
                for h in holdings:
                    bp = h.get("buy_price", 0)
                    cp = h.get("current_price", 0)
                    if bp > 0 and cp > 0 and (cp - bp) / bp >= 0.50:
                        buttons.append([
                            InlineKeyboardButton(
                                f"💰 {h['name']} 익절 실행",
                                callback_data=f"pt:sell:{h['ticker']}:{h.get('quantity',0)//3}",
                            ),
                        ])
                if buttons:
                    buttons.append([
                        InlineKeyboardButton(
                            "👌 확인", callback_data="pt:ignore:all",
                        ),
                    ])
                    keyboard = InlineKeyboardMarkup(buttons)

            await context.bot.send_message(
                chat_id=self.chat_id,
                text="\n".join(lines),
                reply_markup=keyboard,
            )
            logger.info("EOD risk report sent")

        except Exception as e:
            logger.error("EOD risk report error: %s", e)

    async def job_health_check(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """시스템 헬스체크 (30분마다).

        v4.0: health_monitor + circuit_breaker 통합.
        디스크/메모리/DB/데이터 최신성 + 서킷 브레이커 상태.
        """
        if not self.chat_id:
            return
        try:
            from kstock.core.health_monitor import (
                run_health_checks, attempt_recovery,
            )

            db_path = getattr(self.db, 'db_path', None) or "data/kquant.db"
            checks = run_health_checks(db_path=db_path)

            # 실패한 체크만 필터
            failed = [c for c in checks if c.status in ("error", "warning")]

            if failed:
                # 자동 복구 시도
                for fc in failed:
                    if fc.status == "error":
                        try:
                            recovered = attempt_recovery(fc)
                            if recovered:
                                fc.status = "ok"
                                fc.message += " (자동 복구 완료)"
                        except Exception:
                            logger.debug("job_health_check recovery attempt failed for %s", fc.name, exc_info=True)

                # v8.1: error + warning 모두 알림 (쿨다운 적용)
                if not hasattr(self, '_health_alert_cache'):
                    self._health_alert_cache = {}
                from datetime import datetime
                now = datetime.now(KST)
                new_alerts = []
                for c in failed:
                    cooldown = 14400 if c.status == "error" else 28800  # error 4h, warning 8h
                    last_sent = self._health_alert_cache.get(c.name)
                    if last_sent and (now - last_sent).total_seconds() < cooldown:
                        continue
                    new_alerts.append(c)
                    self._health_alert_cache[c.name] = now

                if new_alerts:
                    lines = ["시스템 헬스체크 알림", "━" * 22, ""]
                    for c in new_alerts:
                        icon = "!!" if c.status == "error" else "!"
                        lines.append(f"{icon} {c.name}: {c.message}")
                    lines.append("")
                    lines.append(f"총 {len(checks)}개 체크 중 {len(failed)}개 이상")
                    await context.bot.send_message(
                        chat_id=self.chat_id, text="\n".join(lines),
                    )

            # 서킷 브레이커 상태 로그
            try:
                from kstock.core.circuit_breaker import get_all_stats
                for stat in get_all_stats():
                    if stat.state != "closed":
                        logger.warning(
                            "CircuitBreaker %s: %s (failures=%d)",
                            stat.name, stat.state, stat.consecutive_failures,
                        )
            except Exception:
                logger.debug("job_health_check circuit_breaker stats failed", exc_info=True)

        except Exception as e:
            logger.debug("Health check job error: %s", e)

    # == Phase 2+3 Jobs (v4.3) ================================================

    async def job_weekly_journal_review(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """주간 매매일지 AI 복기 (일요일 10:00).

        v4.3: 지난 주 매매를 분석하고 AI 복기 리포트 생성.
        """
        if not self.chat_id:
            return
        try:
            import json
            from kstock.core.trade_journal import (
                TradeJournal, format_journal_report, format_journal_short,
            )

            journal = TradeJournal(db=self.db)
            trades = journal.collect_trades(days=7)

            if not trades:
                logger.debug("Weekly journal: no trades in past 7 days")
                return

            patterns = journal.analyze_patterns(trades)
            prompt = journal.build_review_prompt(trades, patterns, period="weekly")

            # AI 복기 생성
            ai_review = ""
            if prompt:
                try:
                    ai_review = await self.ai_router.analyze(
                        task="deep_analysis",
                        prompt=prompt,
                        system="당신은 숙련된 주식 투자 코치입니다. 한국어로 친근하게 답변하세요.",
                        max_tokens=1500,
                    )
                except Exception as e:
                    logger.warning("AI journal review failed: %s", e)

            report = journal.generate_report(trades, patterns, ai_review=ai_review)

            # DB 저장
            try:
                self.db.add_journal_report(
                    period="weekly",
                    date_range=report.date_range,
                    total_trades=report.total_trades,
                    win_rate=report.win_rate,
                    avg_pnl=report.avg_pnl,
                    best_trade_json=json.dumps(report.best_trade, ensure_ascii=False) if report.best_trade else "",
                    worst_trade_json=json.dumps(report.worst_trade, ensure_ascii=False) if report.worst_trade else "",
                    ai_review=ai_review,
                )
            except Exception as e:
                logger.debug("Journal DB save error: %s", e)

            # 텔레그램 발송
            text = format_journal_report(report)
            await context.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "📊 상세 보기", callback_data="journal:detail:weekly",
                    ),
                ]]),
            )
            logger.info("Weekly journal review sent (%d trades)", report.total_trades)
            self.db.upsert_job_run("weekly_journal_review", _today(), status="success")

        except Exception as e:
            logger.error("Weekly journal review error: %s", e, exc_info=True)

    async def job_sector_rotation_check(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """섹터 로테이션 체크 (매일 09:05, 평일).

        v4.3: 섹터 모멘텀 분석 + 포트폴리오 리밸런싱 제안.
        """
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
            return

        try:
            import json
            from kstock.core.sector_rotation import (
                SectorRotationEngine, SECTOR_ETF_MAP,
                format_sector_dashboard,
            )

            engine = SectorRotationEngine(db=self.db, yf_client=self.yf_client)

            # 섹터 ETF OHLCV 수집
            ohlcv_map = {}
            for sector, etf_code in SECTOR_ETF_MAP.items():
                try:
                    df = await self.yf_client.get_ohlcv(etf_code, "KOSPI")
                    if df is not None and not df.empty:
                        ohlcv_map[etf_code] = df
                except Exception:
                    logger.debug("job_sector_rotation ETF OHLCV fetch failed for %s", etf_code, exc_info=True)

            if not ohlcv_map:
                logger.debug("Sector rotation: no ETF data available")
                return

            # 보유종목 가져오기
            holdings = self.db.get_active_holdings()

            # 대시보드 생성
            dashboard = engine.create_dashboard(ohlcv_map, holdings)

            # 시그널이 있을 때만 발송 (매일 알림 → 시그널 있을 때만)
            if dashboard.signals:
                text = format_sector_dashboard(dashboard)
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "📊 섹터 상세", callback_data="sector_rotate:detail",
                        ),
                    ]]),
                )

                # DB 저장
                try:
                    self.db.add_sector_snapshot(
                        snapshot_date=now.strftime("%Y-%m-%d"),
                        sectors_json=json.dumps(
                            [{"sector": s.sector, "momentum": s.momentum_score,
                              "1w": s.return_1w_pct, "1m": s.return_1m_pct}
                             for s in dashboard.sectors],
                            ensure_ascii=False,
                        ),
                        signals_json=json.dumps(
                            [{"type": s.signal_type, "sector": s.sector,
                              "direction": s.direction}
                             for s in dashboard.signals],
                            ensure_ascii=False,
                        ),
                        portfolio_json=json.dumps(dashboard.portfolio_sectors, ensure_ascii=False),
                    )
                except Exception as e:
                    logger.debug("Sector snapshot save error: %s", e)

            logger.info("Sector rotation check: %d sectors, %d signals",
                        len(dashboard.sectors), len(dashboard.signals))
            self.db.upsert_job_run("sector_rotation_check", _today(), status="success")

        except Exception as e:
            logger.error("Sector rotation check error: %s", e, exc_info=True)

    async def job_contrarian_scan(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """역발상 시그널 스캔 (14:00 평일 — 장 후반 1회).

        v4.3: 시장 + 보유종목 역발상 분석, 강한 시그널만 알림.
        """
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
            return

        try:
            import json
            from kstock.signal.contrarian_signal import (
                ContrarianEngine, format_contrarian_dashboard,
                format_contrarian_alert,
            )

            engine = ContrarianEngine()

            # 시장 전체 분석
            snap = None
            try:
                snap = await self.macro_client.get_snapshot()
            except Exception:
                logger.debug("job_contrarian_scan macro snapshot failed", exc_info=True)

            vix = getattr(snap, 'vix', 20.0) if snap else 20.0
            fear_greed = getattr(snap, 'regime', '중립') if snap else '중립'

            dashboard = engine.analyze_market(
                vix=vix,
                fear_greed_label=fear_greed,
            )

            # 보유종목별 역발상 분석
            holdings = self.db.get_active_holdings()
            strong_signals = []

            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", "")
                try:
                    signals = engine.analyze(
                        ticker=ticker,
                        name=name,
                        vix=vix,
                        rsi=h.get("rsi", 50),
                        volume_ratio=h.get("volume_ratio", 1.0),
                        foreign_net_days=h.get("foreign_net_buy_days", 0),
                        per=h.get("per", 15),
                        pbr=h.get("pbr", 1.0),
                        roe=h.get("roe", 10),
                        price_change_pct=h.get("change_pct", 0),
                        bb_pctb=h.get("bb_pctb", 0.5),
                    )
                    for sig in signals:
                        if sig.strength >= 0.5:
                            strong_signals.append(sig)
                            # DB 저장
                            try:
                                self.db.add_contrarian_signal(
                                    signal_type=sig.signal_type,
                                    ticker=sig.ticker,
                                    name=sig.name,
                                    direction=sig.direction,
                                    strength=sig.strength,
                                    score_adj=sig.score_adj,
                                    reasons_json=json.dumps(sig.reasons, ensure_ascii=False),
                                    data_json=json.dumps(sig.data, ensure_ascii=False),
                                )
                            except Exception:
                                logger.debug("job_contrarian_scan DB save signal failed for %s", sig.ticker, exc_info=True)
                except Exception as e:
                    logger.debug("Contrarian scan error for %s: %s", ticker, e)

            # 시장 시그널 or 강한 종목 시그널이 있을 때만 발송
            if dashboard.signals or strong_signals:
                text = format_contrarian_dashboard(dashboard)
                if strong_signals:
                    text += "\n\n📡 보유종목 역발상 시그널"
                    for sig in strong_signals[:5]:
                        text += f"\n  {'🟢' if sig.direction == 'BUY' else '🔴'} "
                        text += f"{sig.name}: {sig.reasons[0] if sig.reasons else ''}"

                await context.bot.send_message(
                    chat_id=self.chat_id, text=text,
                )

            logger.info("Contrarian scan: market=%d, holdings=%d signals",
                        len(dashboard.signals), len(strong_signals))
            self.db.upsert_job_run("contrarian_scan", _today(), status="success")

        except Exception as e:
            logger.error("Contrarian scan error: %s", e, exc_info=True)

    # ── #7 매니저 능동 발굴 스캔 (평일 13:00) ──────────────────

    async def job_manager_discovery_scan(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매니저별 발굴 기준으로 신규 종목 탐색 — 캐시된 스캔 결과 활용."""
        if not self.chat_id:
            return
        now = datetime.now(KST)
        if not is_kr_market_open(now.date()):
            return

        try:
            from kstock.bot.investment_managers import (
                scan_manager_domain, filter_discovery_candidates, MANAGERS,
            )

            # 캐시된 스캔 결과 사용 (아침 스캔에서 생성됨)
            results = getattr(self, '_last_scan_results', None)
            if not results:
                logger.debug("Manager discovery: no cached scan results")
                return

            # 보유+관심종목 제외
            holdings = self.db.get_active_holdings()
            watchlist = self.db.get_watchlist()
            exclude = {h["ticker"] for h in holdings}
            exclude |= {w["ticker"] for w in watchlist}

            # 시장 컨텍스트
            market_text = ""
            try:
                macro = await self.macro_client.get_snapshot()
                market_text = (
                    f"VIX={macro.vix:.1f}, S&P={macro.spx_change_pct:+.2f}%, "
                    f"나스닥={macro.nasdaq_change_pct:+.2f}%, "
                    f"환율={macro.usdkrw:,.0f}원"
                )
            except Exception:
                pass

            current_alert = getattr(self, '_alert_mode', 'normal')
            found = 0
            for mgr_key in MANAGERS:
                candidates = filter_discovery_candidates(
                    results, mgr_key, exclude,
                )
                if not candidates:
                    continue
                try:
                    report = await scan_manager_domain(
                        mgr_key, candidates, market_text,
                        alert_mode=current_alert,
                    )
                    if report and "매수 타이밍 종목 없음" not in report:
                        header = "🔍 매니저 신규 발굴\n"
                        await context.bot.send_message(
                            chat_id=self.chat_id,
                            text=(header + report)[:4000],
                        )
                        found += 1
                except Exception as e:
                    logger.debug("Discovery scan %s error: %s", mgr_key, e)

            logger.info("Manager discovery scan: %d managers found picks", found)
            self.db.upsert_job_run("manager_discovery", _today(), status="success")

        except Exception as e:
            logger.error("Manager discovery scan error: %s", e, exc_info=True)

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
                logger.debug("_refresh_sector_strengths ETF OHLCV failed for %s", code, exc_info=True)
        self._sector_strengths = compute_sector_returns(ohlcv_map)

    # == v5.5: 매일 저녁 7시 일일 평가 알림 ====================================

    async def job_daily_rating(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매일 저녁 7시 — 오늘의 서비스 평가하기 (상/중/하)."""
        if not self.chat_id:
            return
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            # 오늘 피드백 요약
            today_fb = self.db.get_today_feedback()
            likes = sum(1 for f in today_fb if f.get("feedback") == "like")
            dislikes = sum(1 for f in today_fb if f.get("feedback") == "dislike")
            fb_summary = ""
            if likes or dislikes:
                fb_summary = f"\n📊 오늘 피드백: 👍 {likes}건 / 👎 {dislikes}건"

            buttons = [
                [
                    InlineKeyboardButton("🌟 상", callback_data="rate:상"),
                    InlineKeyboardButton("👌 중", callback_data="rate:중"),
                    InlineKeyboardButton("😔 하", callback_data="rate:하"),
                ],
            ]
            await context.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    f"📋 오늘의 K-Quant 평가하기\n\n"
                    f"오늘 하루 서비스는 어떠셨나요?{fb_summary}\n\n"
                    f"🌟 상 — 만족, 잘 활용함\n"
                    f"👌 중 — 보통, 개선 필요\n"
                    f"😔 하 — 불만족, 심각한 문제\n\n"
                    f"솔직한 평가 부탁드립니다."
                ),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.debug("Daily rating job error: %s", e)

    # ── 공매도 데이터 수집 (v5.8) ─────────────────────────────

    async def job_short_selling_collect(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """16:15 평일: 보유/즐겨찾기 종목 공매도 데이터 수집 + 과열 알림."""
        try:
            from kstock.ingest.naver_finance import get_short_selling

            # 보유 + 즐겨찾기 종목 합치기
            holdings = self.db.get_active_holdings()
            watchlist = self.db.get_watchlist()
            tickers = set()
            for h in holdings:
                t = h.get("ticker", "")
                if t:
                    tickers.add(t)
            for w in watchlist:
                t = w.get("ticker", "")
                if t:
                    tickers.add(t)

            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            collected = 0
            alerts = []

            for ticker in list(tickers)[:20]:
                try:
                    data = await get_short_selling(ticker, days=5)
                    if not data:
                        continue
                    for d in data[:3]:
                        self.db.add_short_selling(
                            ticker=ticker,
                            date=d["date"],
                            short_volume=d["short_volume"],
                            total_volume=d["total_volume"],
                            short_ratio=d["short_ratio"],
                            short_balance=d.get("short_balance", 0),
                            short_balance_ratio=d.get("short_balance_ratio", 0.0),
                        )
                    collected += 1

                    # 과열 체크
                    latest = data[0] if data else {}
                    ratio = latest.get("short_ratio", 0)
                    if ratio >= 15:
                        name = self._resolve_name(ticker, ticker) if hasattr(self, '_resolve_name') else ticker
                        alerts.append(f"🔴 {name}: 공매도 비중 {ratio:.1f}%")

                    await asyncio.sleep(0.5)  # rate limit
                except Exception as e:
                    logger.debug("Short selling collect for %s: %s", ticker, e)

            # 과열 종목 알림
            if alerts:
                msg = (
                    f"⚠️ 공매도 과열 감지 ({today_str})\n"
                    f"{'━' * 22}\n\n"
                    + "\n".join(alerts)
                )
                await context.bot.send_message(
                    chat_id=self.chat_id, text=msg,
                )

            self.db.upsert_job_run("short_selling_collect", today_str, status="success")
            logger.info("Short selling collected for %d tickers, %d alerts", collected, len(alerts))
        except Exception as e:
            logger.error("Short selling collect failed: %s", e)

    # ── 뉴스 모니터링 (v5.8) ─────────────────────────────────

    async def job_news_monitor(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """09:00~15:30 매 30분: 보유/즐겨찾기 종목 뉴스 모니터링."""
        try:
            from kstock.ingest.naver_finance import get_stock_news

            # 보유 + 즐겨찾기 종목
            holdings = self.db.get_active_holdings()
            watchlist = self.db.get_watchlist()
            ticker_names = {}
            for h in holdings:
                t = h.get("ticker", "")
                if t:
                    ticker_names[t] = h.get("name", t)
            for w in watchlist:
                t = w.get("ticker", "")
                if t:
                    ticker_names[t] = w.get("name", t)

            # 이미 전송한 뉴스 URL 추적 (DB 영속 + 메모리 캐시 병행)
            # v6.2.1: 재시작 후에도 중복 뉴스 방지
            sent_news = context.bot_data.setdefault("sent_news", set())
            if not sent_news:
                # 봇 시작 후 첫 실행: DB에서 최근 전송 URL 로드
                try:
                    rows = self.db.conn.execute(
                        "SELECT url FROM sent_news_urls ORDER BY id DESC LIMIT 500"
                    ).fetchall()
                    sent_news.update(r[0] for r in rows)
                except Exception:
                    # 테이블 없으면 생성
                    try:
                        self.db.conn.execute(
                            "CREATE TABLE IF NOT EXISTS sent_news_urls ("
                            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                            "url TEXT UNIQUE NOT NULL, "
                            "created_at TEXT DEFAULT (datetime('now')))"
                        )
                        self.db.conn.commit()
                    except Exception:
                        pass
            # 오래된 항목 정리 (1000개 초과 시)
            if len(sent_news) > 1000:
                context.bot_data["sent_news"] = set()
                sent_news = context.bot_data["sent_news"]
                try:
                    self.db.conn.execute(
                        "DELETE FROM sent_news_urls WHERE id NOT IN "
                        "(SELECT id FROM sent_news_urls ORDER BY id DESC LIMIT 500)"
                    )
                    self.db.conn.commit()
                except Exception:
                    pass

            # 중요 키워드
            important_kw = [
                "급등", "급락", "상한가", "하한가", "실적", "어닝",
                "인수", "합병", "M&A", "공시", "배당", "증자", "감자",
                "상장폐지", "거래정지", "신고가", "신저가", "목표가",
                "투자의견", "매수", "매도", "상향", "하향",
                # v6.2.1: 정부 정책/부양책/시장 안정화 관련 키워드
                "정부", "부양", "국채", "안정화", "긴급", "대책",
                "규제", "완화", "지원", "보조금", "정책", "100조",
                "공매도", "금지", "재개", "밸류업", "기업가치",
            ]
            # 시장 전체 뉴스 제외 키워드 (종목과 무관한 뉴스)
            # v6.2.1: 국채/금리 제거 (정부 정책 모니터링 강화)
            market_noise = [
                "코스피", "코스닥", "증시", "지수", "외국인",
                "기관", "개인", "순매수", "순매도",
            ]

            import re as _re

            def _news_dedup_key(url: str) -> str:
                """article_id+office_id 기반 중복 키 (code= 파라미터 제거)."""
                m_art = _re.search(r"article_id=([^&]+)", url)
                m_off = _re.search(r"office_id=([^&]+)", url)
                if m_art and m_off:
                    return f"{m_art.group(1)}_{m_off.group(1)}"
                # 폴백: URL 전체 (code= 파라미터 제거)
                return _re.sub(r"[&?]code=[^&]*", "", url)

            alerts = []
            for ticker, name in list(ticker_names.items())[:15]:
                try:
                    news_list = await get_stock_news(ticker, limit=5)
                    for news in news_list:
                        url = news.get("url", "")
                        title = news.get("title", "")
                        if not url:
                            continue
                        dedup_key = _news_dedup_key(url)
                        if dedup_key in sent_news:
                            continue
                        # 종목명이 제목에 포함된 뉴스만 (잘못된 매칭 방지)
                        name_clean = name.replace("우", "").replace("홀딩스", "")
                        name_variants = {name, name_clean, name_clean[:3], name_clean[:2]}
                        has_name = any(v in title for v in name_variants if len(v) >= 2)
                        if not has_name:
                            continue  # 종목명이 없는 뉴스는 무시
                        # 중요 뉴스 필터
                        is_important = any(kw in title for kw in important_kw)
                        if is_important:
                            alerts.append(f"📰 {name}: {title}\n🔗 {url}")
                            sent_news.add(dedup_key)
                            # DB에도 저장 (재시작 후 중복 방지)
                            try:
                                self.db.conn.execute(
                                    "INSERT OR IGNORE INTO sent_news_urls (url) VALUES (?)",
                                    (dedup_key,),
                                )
                                self.db.conn.commit()
                            except Exception:
                                pass
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.debug("News monitor for %s: %s", ticker, e)

            if alerts:
                msg = (
                    f"📰 종목 뉴스 알림\n{'━' * 22}\n\n"
                    + "\n\n".join(alerts[:5])
                )
                await context.bot.send_message(
                    chat_id=self.chat_id, text=msg,
                )
                logger.info("News alerts sent: %d", len(alerts))
        except Exception as e:
            logger.error("News monitor failed: %s", e)

    # ── v6.1: 글로벌 뉴스 수집 + 위기 감지 (적응형 빈도) ──────────

    async def job_global_news_collect(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """글로벌 뉴스 RSS 수집 + DB 저장 + 위기 감지 + 긴급 알림.

        적응형 빈도: 정상 30분, 주의 15분, 경계 10분, 위기 5분.
        매크로 선행지표(VIX/BTC/금/S&P500)로 위기 판단.
        """
        try:
            from kstock.ingest.global_news import (
                fetch_global_news,
                filter_urgent_news,
                format_urgent_alert,
                detect_crisis_from_macro,
                format_crisis_alert,
                translate_titles_to_korean,
                enrich_youtube_summaries,
                group_similar_news,
                analyze_urgent_news,
                make_alert_hash,
            )

            # 1. RSS 뉴스 수집
            items = await fetch_global_news(max_per_feed=5)
            if items:
                # 1-1. 영문 제목 → 한글 번역
                items = await translate_titles_to_korean(items)

                # 1-2. YouTube 영상 자막 기반 내용 요약 (v8.2)
                try:
                    items = await enrich_youtube_summaries(items, max_summaries=5, db=self.db)
                except Exception as e:
                    logger.debug("YouTube summary enrichment failed: %s", e)

                # NewsItem → dict 변환 후 DB 저장
                news_dicts = [
                    {
                        "title": item.title,
                        "source": item.source,
                        "url": item.url,
                        "category": item.category,
                        "lang": item.lang,
                        "impact_score": item.impact_score,
                        "is_urgent": item.is_urgent,
                        "published": item.published,
                        "content_summary": item.content_summary,
                        "video_id": item.video_id,
                    }
                    for item in items
                ]
                saved = self.db.save_global_news(news_dicts)
                logger.info("Global news: %d fetched, %d saved", len(items), saved)

                # 2. 긴급 뉴스 감지 → 유사 그룹핑 → AI 분석 → 텔레그램 알림
                urgent = filter_urgent_news(items)
                if urgent and self.chat_id:
                    # v9.5.3: 유사 뉴스 그룹핑 (같은 이벤트 통합)
                    groups = group_similar_news(urgent, threshold=0.35)

                    # DB 기반 중복 방지 (재시작 후에도 유지)
                    new_groups = []
                    for group in groups:
                        h = make_alert_hash(group)
                        if not self.db.is_alert_sent(h, hours=6):
                            new_groups.append(group)

                    if new_groups:
                        # AI 분석 포함 리치 알림 생성
                        try:
                            alert_msg = await analyze_urgent_news(
                                new_groups, db=self.db,
                            )
                        except Exception as e:
                            logger.warning("AI news analysis failed, fallback: %s", e)
                            alert_msg = format_urgent_alert(
                                [g[0] for g in new_groups],
                            )

                        if alert_msg:
                            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                            news_buttons = InlineKeyboardMarkup([
                                [
                                    InlineKeyboardButton("📊 시장분석", callback_data="quick_q:market"),
                                    InlineKeyboardButton("🔬 섹터분석", callback_data="sdive:menu"),
                                ],
                                [
                                    InlineKeyboardButton("👍", callback_data="fb:like:글로벌뉴스"),
                                    InlineKeyboardButton("👎", callback_data="fb:dislike:글로벌뉴스"),
                                    InlineKeyboardButton("❌", callback_data="dismiss:0"),
                                ],
                            ])
                            await context.bot.send_message(
                                chat_id=self.chat_id, text=alert_msg,
                                reply_markup=news_buttons,
                            )
                            # DB에 전송 기록
                            for group in new_groups:
                                h = make_alert_hash(group)
                                title = group[0].title[:100]
                                self.db.save_sent_alert(h, title)
                            logger.info(
                                "Urgent news alert sent: %d groups (%d items)",
                                len(new_groups),
                                sum(len(g) for g in new_groups),
                            )

                # 2-1. 뉴스 키워드 기반 경계 모드 자동 에스컬레이션
                await self._check_news_escalation(items)
                pending = getattr(self, "_pending_escalation", None)
                if pending:
                    new_mode, reason = pending
                    del self._pending_escalation
                    await self.set_alert_mode(
                        new_mode, context=context, reason=reason,
                    )

            # 2-2. 자동 경계 완화 체크
            await self._check_auto_deescalation(context)

            # 3. 매크로 선행지표 기반 위기 감지 + 적응형 빈도 조정
            try:
                macro = await self.macro_client.get_snapshot()
                crisis = detect_crisis_from_macro(macro)

                prev_severity = getattr(self, "_news_crisis_severity", 0)
                self._news_crisis_severity = crisis.severity

                # 위기 수준 변경 시 → 수집 빈도 동적 조정 + 알림
                if crisis.severity != prev_severity:
                    await self._reschedule_news_collect(
                        context, crisis.recommended_interval,
                    )
                    # 경계 이상이면 텔레그램 알림
                    if crisis.severity >= 2 and self.chat_id:
                        crisis_msg = format_crisis_alert(crisis)
                        if crisis_msg:
                            await context.bot.send_message(
                                chat_id=self.chat_id, text=crisis_msg,
                            )
                    # 매크로 위기 → 경계 모드 자동 에스컬레이션
                    if crisis.severity >= 3 and self._alert_mode != "wartime":
                        await self.set_alert_mode(
                            "wartime", context=context,
                            reason=f"매크로 위기 감지: {', '.join(crisis.triggers[:3])}",
                        )
                    elif crisis.severity >= 2 and self._alert_mode == "normal":
                        await self.set_alert_mode(
                            "elevated", context=context,
                            reason=f"매크로 경계: {', '.join(crisis.triggers[:3])}",
                        )
                    logger.info(
                        "Crisis level changed: %d → %d (%s), interval=%ds",
                        prev_severity, crisis.severity, crisis.label,
                        crisis.recommended_interval,
                    )
            except Exception as e:
                logger.debug("Crisis detection error: %s", e)

            # 4. 주기적 클린업 (1일 1회)
            now = datetime.now(KST)
            last_cleanup = getattr(self, "_last_news_cleanup", None)
            if last_cleanup is None or last_cleanup.date() != now.date():
                cleaned = self.db.cleanup_old_news(days=7)
                self._last_news_cleanup = now
                if cleaned > 0:
                    logger.info("Old news cleaned: %d rows", cleaned)

        except Exception as e:
            logger.error("Global news collect failed: %s", e, exc_info=True)

    async def _reschedule_news_collect(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        new_interval: int,
    ) -> None:
        """글로벌 뉴스 수집 주기 동적 조정."""
        jq = getattr(self, "_job_queue", None)
        if jq is None:
            jq = context.application.job_queue
        if jq is None:
            return

        try:
            current_jobs = jq.jobs()
            for job in current_jobs:
                if job.name == "global_news_collect":
                    job.schedule_removal()

            jq.run_repeating(
                self.job_global_news_collect,
                interval=new_interval,
                first=10,
                name="global_news_collect",
            )
            logger.info("News collect interval changed to %ds", new_interval)
        except Exception as e:
            logger.error("News reschedule failed: %s", e)

    # ── v6.2: 자가 학습 루프 ─────────────────────────────────────────────

    async def job_signal_evaluation(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매일 16:20 — 신호 적중률 평가 + 가중치 재계산.

        장 마감 후 미평가 신호들의 D+N 수익률을 계산하고
        신호 소스별 가중치를 자동 조정합니다.
        """
        if not self.chat_id:
            return
        if not is_kr_market_open():
            return
        try:
            from kstock.signal.auto_debrief import (
                evaluate_pending_signals,
                compute_signal_weights,
            )

            # 1. 미평가 신호 가격 추적
            evaluated = await evaluate_pending_signals(self.db)

            # 1-b. v9.6.0: 추천(Recommendation) D+N 추적
            rec_evaluated = 0
            try:
                from kstock.signal.auto_debrief import evaluate_pending_recommendations
                rec_evaluated = await evaluate_pending_recommendations(self.db)
            except Exception as e:
                logger.debug("추천 추적: %s", e)

            # 2. 가중치 재계산
            weights = compute_signal_weights(self.db, period_days=90)

            # v9.5.3: 매니저 성적표 + 매매 패턴 학습
            mgr_msg = ""
            try:
                from kstock.bot.learning_engine import (
                    calculate_manager_scorecard,
                    analyze_user_trade_patterns,
                    format_manager_scorecard,
                )
                scorecards = calculate_manager_scorecard(self.db, days=30)
                profile = analyze_user_trade_patterns(self.db)
                mgr_msg = f", managers={len(scorecards)}"

                # 주간 성적표 전송 (일요일만)
                if datetime.now(KST).weekday() == 6:  # Sunday
                    card_text = format_manager_scorecard(scorecards)
                    if card_text and self.chat_id:
                        await context.bot.send_message(
                            chat_id=self.chat_id, text=card_text,
                        )
            except Exception as e:
                logger.debug("Learning engine update: %s", e)

            # v9.5.4: 매일 보유 섹터 딥다이브 자동 갱신
            try:
                from kstock.bot.sector_intelligence import (
                    detect_user_focus_sectors,
                    generate_sector_deep_dive,
                )
                import anthropic as _anthropic
                _ai_client = _anthropic.Anthropic()
                focus_sectors = detect_user_focus_sectors(self.db)
                for sk in focus_sectors[:2]:  # 상위 2개 섹터만
                    try:
                        result = await generate_sector_deep_dive(
                            self.db, sk, anthropic_client=_ai_client,
                            include_peers=True,
                        )
                        if result.get("report") and not result.get("cached"):
                            logger.info("sector deep-dive generated: %s", sk)
                    except Exception as e2:
                        logger.debug("sector deep-dive %s failed: %s", sk, e2)
            except Exception as e:
                logger.debug("Sector deep-dive update: %s", e)

            self.db.upsert_job_run(
                "signal_evaluation", _today(), status="success",
                message=f"evaluated={evaluated}, recs={rec_evaluated}, sources={len(weights)}{mgr_msg}",
            )
            logger.info(
                "Signal evaluation: %d signals evaluated, %d sources weighted",
                evaluated, len(weights),
            )
        except Exception as e:
            logger.error("Signal evaluation failed: %s", e, exc_info=True)
            self.db.upsert_job_run(
                "signal_evaluation", _today(),
                status="error", message=str(e)[:200],
            )

    async def job_learning_report(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매주 토요일 11:00 — 자가 학습 리포트 텔레그램 전송.

        신호 적중률, 매매 복기 요약, 가중치 조정, 반복 패턴을 종합.
        """
        if not self.chat_id:
            return
        try:
            from kstock.signal.feedback_loop import generate_learning_feedback

            report_text = generate_learning_feedback(self.db)

            if report_text and len(report_text) > 50:
                # v6.2.1: 페이지네이션 (4096자 제한 대응)
                if len(report_text) > 3800:
                    pages = []
                    lines = report_text.split("\n")
                    cur, cur_len = [], 0
                    for line in lines:
                        ll = len(line) + 1
                        if cur_len + ll > 3800 and cur:
                            pages.append("\n".join(cur))
                            cur, cur_len = [line], ll
                        else:
                            cur.append(line)
                            cur_len += ll
                    if cur:
                        pages.append("\n".join(cur))
                    for i, pg in enumerate(pages):
                        hdr = f"📄 ({i+1}/{len(pages)})\n" if len(pages) > 1 else ""
                        await context.bot.send_message(
                            chat_id=self.chat_id, text=hdr + pg,
                        )
                else:
                    await context.bot.send_message(
                        chat_id=self.chat_id,
                        text=report_text,
                    )

            self.db.upsert_job_run(
                "learning_report", _today(), status="success",
            )
            logger.info("Weekly learning report sent")
        except Exception as e:
            logger.error("Learning report failed: %s", e, exc_info=True)
            self.db.upsert_job_run(
                "learning_report", _today(),
                status="error", message=str(e)[:200],
            )

    async def job_daily_system_score(
        self, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """매일 23:55 — 시스템 자가 점수 계산 및 저장.

        100점 만점으로 신호적중/매매성과/알림/학습/비용/안정성 종합 평가.
        """
        try:
            from kstock.core.system_score import compute_system_score
            score = compute_system_score(self.db)
            logger.info(
                "System score: %s/100 (grade=%s)",
                score.get("total", 0), score.get("grade", "?"),
            )
            self.db.upsert_job_run(
                "daily_system_score", _today(), status="success",
                message=f"score={score.get('total', 0)}, grade={score.get('grade', '?')}",
            )
        except Exception as e:
            logger.error("Daily system score failed: %s", e, exc_info=True)
            self.db.upsert_job_run(
                "daily_system_score", _today(),
                status="error", message=str(e)[:200],
            )

    # ══════════════════════════════════════════════════════════════
    # v9.4: AI 토론 자동 실행 + 예측 추적
    # ══════════════════════════════════════════════════════════════

    async def job_auto_debate(self, context) -> None:
        """v9.4: 보유종목 + 관심종목 자동 AI 토론 (09:30/14:00).

        3라운드 구조화 토론 → DB 저장 → verdict 변화 시 알림.
        """
        from kstock.core.market_calendar import is_kr_market_open

        if not is_kr_market_open():
            return

        try:
            from kstock.bot.debate_engine import DebateEngine, format_debate_short
            from kstock.signal.pattern_matcher import PatternMatcher, format_pattern_for_debate
            from kstock.signal.price_target import PriceTargetEngine, format_price_target_for_debate

            # 토론 대상: 보유종목 + 관심종목 (최대 10개)
            targets = []
            holdings = self.db.get_active_holdings()
            for h in (holdings or []):
                ticker = h.get("ticker", "")
                name = h.get("name", "") or ticker
                if ticker and ticker not in [t[0] for t in targets]:
                    targets.append((ticker, name))

            watchlist = self.db.get_watchlist() if hasattr(self.db, "get_watchlist") else []
            for w in (watchlist or []):
                ticker = w.get("ticker", "")
                name = w.get("name", "") or ticker
                if ticker and ticker not in [t[0] for t in targets]:
                    targets.append((ticker, name))

            targets = targets[:10]  # 비용 제한

            if not targets:
                logger.info("job_auto_debate: no targets")
                return

            engine = DebateEngine()
            pm = PatternMatcher()
            pte = PriceTargetEngine()
            debated = 0

            for ticker, name in targets:
                try:
                    # 데이터 수집
                    stock_data = ""
                    market_context = ""
                    pattern_summary = ""
                    price_target_text = ""

                    # OHLCV
                    ohlcv = None
                    if hasattr(self, "data_router"):
                        ohlcv = await self.data_router.get_ohlcv(ticker)
                    elif hasattr(self, "yf_client") and self.yf_client:
                        ohlcv = await self.yf_client.get_ohlcv(ticker)

                    # 현재가
                    live_price = 0
                    if hasattr(self, "data_router"):
                        live_price = await self.data_router.get_price(ticker)
                    if live_price <= 0 and ohlcv is not None and not ohlcv.empty and "close" in ohlcv.columns:
                        live_price = float(ohlcv["close"].iloc[-1])

                    # 기본 정보
                    info = {}
                    if hasattr(self, "data_router"):
                        info = await self.data_router.get_stock_info(ticker, name)

                    # stock_data 구성
                    if live_price > 0:
                        stock_data = f"현재가: {live_price:,.0f}원"
                    if info:
                        per = info.get("per", 0) or info.get("PER", 0)
                        pbr = info.get("pbr", 0) or info.get("PBR", 0)
                        roe = info.get("roe", 0) or info.get("ROE", 0)
                        if per:
                            stock_data += f", PER: {per:.1f}"
                        if pbr:
                            stock_data += f", PBR: {pbr:.2f}"
                        if roe:
                            stock_data += f", ROE: {roe:.1f}%"

                    # 패턴 매칭
                    if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 40:
                        pr = pm.find_similar_patterns(ohlcv)
                        pattern_summary = format_pattern_for_debate(pr)

                    # 가격 목표
                    if ohlcv is not None and not ohlcv.empty:
                        pt = pte.calculate_targets(
                            ohlcv=ohlcv,
                            current_price=live_price,
                            stock_info=info,
                        )
                        price_target_text = format_price_target_for_debate(pt)

                    # 토론 실행
                    result = await engine.run_debate(
                        ticker=ticker,
                        name=name,
                        stock_data=stock_data,
                        market_context=market_context,
                        pattern_summary=pattern_summary,
                        price_target_data=price_target_text,
                    )

                    if result and not result.error:
                        # DB 저장
                        self.db.save_debate_result(result)
                        debated += 1

                        # 이전 토론과 비교 → verdict 변화 알림
                        prev = self.db.get_latest_debate(ticker)
                        # prev가 방금 저장한 것이므로 history에서 2번째를 봐야 함
                        history = self.db.get_debate_history(ticker, days=7)
                        if len(history) >= 2:
                            prev_verdict = history[1].get("verdict", "")
                            if prev_verdict and prev_verdict != result.final_verdict:
                                await self._notify_verdict_change(
                                    ticker, name, prev_verdict, result,
                                )

                except Exception as e:
                    logger.warning(
                        "job_auto_debate: error for %s: %s", ticker, e,
                        exc_info=True,
                    )

            self.db.upsert_job_run(
                "auto_debate", _today(),
                status="success",
                message=f"debated={debated}/{len(targets)}",
            )
            logger.info("job_auto_debate: completed %d/%d", debated, len(targets))

        except Exception as e:
            logger.error("job_auto_debate failed: %s", e, exc_info=True)
            self.db.upsert_job_run(
                "auto_debate", _today(),
                status="error", message=str(e)[:200],
            )

    async def _notify_verdict_change(
        self, ticker: str, name: str, prev_verdict: str, result,
    ) -> None:
        """verdict 변화 알림 전송."""
        try:
            _ACTION_EMOJI = {"매수": "🟢", "매도": "🔴", "관망": "🟡", "홀딩": "🔵"}
            prev_e = _ACTION_EMOJI.get(prev_verdict, "⚪")
            curr_e = _ACTION_EMOJI.get(result.final_verdict, "⚪")

            text = (
                f"🔄 AI 토론 결과 변화!\n\n"
                f"{name} ({ticker})\n"
                f"이전: {prev_e} {prev_verdict}\n"
                f"현재: {curr_e} {result.final_verdict} "
                f"({result.consensus_level}, 확신 {result.confidence:.0%})\n"
            )
            if result.key_arguments:
                text += f"\n변화 사유: {result.key_arguments[0]}"

            admin_id = getattr(self, "admin_chat_id", None) or getattr(self, "ADMIN_CHAT_ID", None)
            if admin_id:
                await self._application.bot.send_message(
                    chat_id=admin_id, text=text,
                )
        except Exception as e:
            logger.warning("_notify_verdict_change error: %s", e, exc_info=True)

    async def job_track_predictions(self, context) -> None:
        """v9.4: 과거 토론 예측 정확도 추적 (16:10).

        5일 전 토론의 예측을 실제 가격과 비교.
        """
        try:
            unevaluated = self.db.get_unevaluated_debates(min_age_days=5)
            if not unevaluated:
                return

            evaluated = 0
            for debate in unevaluated:
                ticker = debate["ticker"]
                try:
                    # 현재가 가져오기
                    current_price = 0
                    if hasattr(self, "data_router"):
                        current_price = await self.data_router.get_price(ticker)

                    if current_price <= 0:
                        continue

                    predicted_target = debate.get("price_target", 0)
                    predicted_verdict = debate.get("verdict", "")

                    # 정확도 점수 계산
                    # verdict 방향 정확도: 매수→상승, 매도→하락이면 정확
                    accuracy = 0.0
                    if predicted_target > 0:
                        # 목표가 대비 실제 이동 방향
                        target_direction = predicted_target - current_price
                        if (predicted_verdict == "매수" and current_price > predicted_target * 0.95) or \
                           (predicted_verdict == "매도" and current_price < predicted_target * 1.05):
                            accuracy = 0.8
                        elif predicted_verdict in ("관망", "홀딩"):
                            accuracy = 0.5
                        else:
                            accuracy = 0.3

                    self.db.save_debate_accuracy(
                        debate_id=debate["id"],
                        ticker=ticker,
                        predicted_verdict=predicted_verdict,
                        predicted_target=predicted_target,
                        actual_price_5d=current_price,
                        accuracy_score=accuracy,
                    )
                    evaluated += 1

                except Exception as e:
                    logger.debug("track_predictions: error for %s: %s", ticker, e)

            if evaluated > 0:
                self.db.upsert_job_run(
                    "track_predictions", _today(),
                    status="success",
                    message=f"evaluated={evaluated}",
                )
                logger.info("job_track_predictions: evaluated %d debates", evaluated)

        except Exception as e:
            logger.error("job_track_predictions failed: %s", e, exc_info=True)

    async def job_weekly_ai_report(self, context) -> None:
        """v9.4: 주간 AI 예측 정확도 리포트 (일요일 10:00)."""
        try:
            stats = self.db.get_prediction_accuracy(days=7)
            if stats.get("total", 0) == 0:
                return

            text = (
                f"📊 주간 AI 토론 성적표\n"
                f"{'━' * 20}\n\n"
                f"평가 건수: {stats['total']}건\n"
                f"정확도: {stats['accuracy_pct']:.1f}%\n"
            )

            # 최근 토론 요약
            recent = self.db.get_all_recent_debates(hours=168)  # 7일
            if recent:
                buy_count = sum(1 for d in recent if d.get("verdict") == "매수")
                sell_count = sum(1 for d in recent if d.get("verdict") == "매도")
                hold_count = len(recent) - buy_count - sell_count
                text += (
                    f"\n토론 결과 분포:\n"
                    f"  매수: {buy_count}건\n"
                    f"  매도: {sell_count}건\n"
                    f"  관망/홀딩: {hold_count}건\n"
                )

            admin_id = getattr(self, "admin_chat_id", None) or getattr(self, "ADMIN_CHAT_ID", None)
            if admin_id:
                await context.bot.send_message(chat_id=admin_id, text=text)

            self.db.upsert_job_run(
                "weekly_ai_report", _today(),
                status="success",
                message=f"accuracy={stats['accuracy_pct']:.1f}%",
            )

        except Exception as e:
            logger.error("job_weekly_ai_report failed: %s", e, exc_info=True)
