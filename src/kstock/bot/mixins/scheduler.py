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
                "☀️ 주호님, 좋은 아침이에요\n\n"
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
                    + format_market_status(macro, regime_mode)
                )

            await context.bot.send_message(chat_id=self.chat_id, text=msg)

            # v3.9: 매니저별 보유종목 분석 (holding_type별 그룹핑)
            await self._send_manager_briefings(context, macro)
            self.db.upsert_job_run("morning_briefing", _today(), status="success")
            logger.info("Morning briefing sent")
        except Exception as e:
            logger.error("Morning briefing failed: %s", e)
            self.db.upsert_job_run("morning_briefing", _today(), status="error", message=str(e))

    async def _send_manager_briefings(self, context, macro) -> None:
        """매니저별 보유종목 분석 메시지 발송 (보유종목 있는 매니저만)."""
        try:
            from collections import defaultdict
            from kstock.bot.investment_managers import get_manager_analysis, MANAGERS

            holdings = self.db.get_active_holdings()
            if not holdings:
                return

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

            for mtype, mholdings in by_type.items():
                if mtype not in MANAGERS or not mholdings:
                    continue
                try:
                    report = await get_manager_analysis(mtype, mholdings, market_text)
                    if report:
                        await context.bot.send_message(
                            chat_id=self.chat_id, text=report[:4000],
                        )
                except Exception as e:
                    logger.debug("Manager briefing %s error: %s", mtype, e)

            logger.info("Manager briefings sent: %s", list(by_type.keys()))
        except Exception as e:
            logger.debug("Manager briefings error: %s", e)

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
                    current_price = 0
                    try:
                        current_price = await self._get_price(ticker, base_price=buy_price)
                    except Exception:
                        current_price = h.get("current_price", 0)
                    pnl_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 and current_price > 0 else 0
                    holdings_text += (
                        f"  {name}({ticker}): "
                        f"매수가 {buy_price:,.0f}원, 현재가 {current_price:,.0f}원, "
                        f"수익률 {pnl_pct:+.1f}%, 수량 {qty}주, "
                        f"투자시계 {horizon}\n"
                    )
            else:
                holdings_text = "  보유종목 없음\n"

            # v6.1: 글로벌 뉴스 컨텍스트 추가
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
                pass

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
                f"{news_ctx}"
                f"[보유종목]\n{holdings_text}\n"
                f"아래 형식으로 작성해주세요:\n\n"
                f"1) 시장 요약 (3줄 이내) — 글로벌 뉴스 헤드라인이 있으면 핵심 이슈 반영\n"
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
        # 보유종목 캐시 갱신 (매도 가이드용)
        self._holdings_cache = self.db.get_active_holdings()
        self._holdings_index = {
            h.get("ticker", ""): h for h in self._holdings_cache if h.get("ticker")
        }
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
                    pass

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
                pass

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
                        pass
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
                pass

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
                pass

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
                f"   - 주시할 가격대/지지선 (참고용)\n"
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
                "4. 분석만 하라. 행동 지시가 아닌 정보 전달.\n\n"
                "[형식 규칙]\n"
                "볼드(**) 사용 금지. 이모지로 구분. "
                "구체적 수치 필수. 추상적 표현 금지. "
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
                msg = (
                    f"🇺🇸 미국 시장 프리마켓 브리핑\n"
                    f"{signal_header}\n"
                    f"{market_note}\n"
                    f"{spx_emoji} S&P500: {macro.spx_change_pct:+.2f}%\n"
                    f"{ndq_emoji} 나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                    f"💰 VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%)\n"
                    f"💱 환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%)\n"
                    f"📊 미국10년물: {macro.us10y:.2f}%\n"
                    f"🪙 BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%)\n"
                    f"🥇 금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%)\n\n"
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

            msg = (
                f"📡 시장 신호 변경\n"
                f"{'━' * 22}\n"
                f"국내 시장 전망: {signal_emoji} {signal_label}\n\n"
                f"S&P500: {macro.spx_change_pct:+.2f}%\n"
                f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
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

                    # mock 데이터인지 확인 (실제 데이터만 저장)
                    frgn_net = 0
                    inst_net = 0
                    is_mock = False

                    if not frgn.empty:
                        frgn_net = int(frgn.iloc[0].get("net_buy", 0))
                    if not inst.empty:
                        inst_net = int(inst.iloc[0].get("net_buy", 0))

                    # mock 데이터 판별: 실수로 mock이 저장되지 않도록 체크
                    if hasattr(frgn, "attrs") and frgn.attrs.get("mock"):
                        is_mock = True

                    if not is_mock and (frgn_net != 0 or inst_net != 0):
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
                # 보유종목 캐시 초기화
                self._holdings_cache = self.db.get_active_holdings()
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
                        pass

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
                    pass

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
                pass

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

                # 손절/트레일링 스탑만 즉시 발송 (1일 1회 제한)
                if alert and alert.alert_type in ("stop_loss", "trailing_stop"):
                    if not self.db.has_recent_alert(
                        ticker, f"profit_{alert.alert_type}", hours=24,
                    ):
                        self.db.insert_alert(
                            ticker, f"profit_{alert.alert_type}",
                            alert.message[:200],
                        )
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
                            text=sizer.format_profit_alert(alert),
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
                pass

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
                            pass

                # 에러 항목만 알림 (warning은 로그만)
                # v5.4: 동일 알림 반복 방지 — 4시간 쿨다운
                errors = [c for c in failed if c.status == "error"]
                if errors:
                    if not hasattr(self, '_health_alert_cache'):
                        self._health_alert_cache = {}
                    from datetime import datetime, timezone, timedelta
                    now = datetime.now(timezone(timedelta(hours=9)))
                    new_errors = []
                    for c in errors:
                        last_sent = self._health_alert_cache.get(c.name)
                        if last_sent and (now - last_sent).total_seconds() < 14400:
                            continue  # 4시간 내 이미 전송됨
                        new_errors.append(c)
                        self._health_alert_cache[c.name] = now

                    if new_errors:
                        lines = ["🏥 시스템 헬스체크 알림", "━" * 22, ""]
                        for c in new_errors:
                            lines.append(f"🔴 {c.name}: {c.message}")
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
                pass

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
                    pass

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
                pass

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
                                pass
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
                            date_str=d["date"],
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

            # 이미 전송한 뉴스 URL 추적
            sent_news = context.bot_data.setdefault("sent_news", set())
            # 오래된 항목 정리 (1000개 초과 시)
            if len(sent_news) > 1000:
                context.bot_data["sent_news"] = set()
                sent_news = context.bot_data["sent_news"]

            # 중요 키워드
            important_kw = [
                "급등", "급락", "상한가", "하한가", "실적", "어닝",
                "인수", "합병", "M&A", "공시", "배당", "증자", "감자",
                "상장폐지", "거래정지", "신고가", "신저가", "목표가",
                "투자의견", "매수", "매도", "상향", "하향",
            ]
            # 시장 전체 뉴스 제외 키워드 (종목과 무관한 뉴스)
            market_noise = [
                "코스피", "코스닥", "증시", "지수", "외국인",
                "기관", "개인", "순매수", "순매도", "국채", "금리",
            ]

            alerts = []
            for ticker, name in list(ticker_names.items())[:15]:
                try:
                    news_list = await get_stock_news(ticker, limit=5)
                    for news in news_list:
                        url = news.get("url", "")
                        title = news.get("title", "")
                        if not url or url in sent_news:
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
                            sent_news.add(url)
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
            )

            # 1. RSS 뉴스 수집
            items = await fetch_global_news(max_per_feed=5)
            if items:
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
                    }
                    for item in items
                ]
                saved = self.db.save_global_news(news_dicts)
                logger.info("Global news: %d fetched, %d saved", len(items), saved)

                # 2. 긴급 뉴스 감지 → 텔레그램 알림
                urgent = filter_urgent_news(items)
                if urgent and self.chat_id:
                    # 쿨다운: 같은 뉴스 30분 내 중복 알림 방지
                    last_urgent = getattr(self, "_last_urgent_news_time", 0.0)
                    now_mono = _time.monotonic()
                    if now_mono - last_urgent >= 1800:
                        alert_msg = format_urgent_alert(urgent)
                        if alert_msg:
                            await context.bot.send_message(
                                chat_id=self.chat_id, text=alert_msg,
                            )
                            self._last_urgent_news_time = now_mono
                            logger.info("Urgent news alert sent: %d items", len(urgent))

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
