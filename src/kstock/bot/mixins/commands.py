"""Commands and analysis functions."""
from __future__ import annotations

import re

from kstock.bot.bot_imports import *  # noqa: F403


class CommandsMixin:
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
                logger.debug("_run_scan pre-scan failed for %s", stock.get("code"), exc_info=True)
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
            import asyncio
            ohlcv = self._ohlcv_cache.get(ticker)
            if ohlcv is None or ohlcv.empty:
                # Fetch OHLCV and stock info in parallel
                ohlcv, yf_info = await asyncio.gather(
                    self.yf_client.get_ohlcv(ticker, market),
                    self.yf_client.get_stock_info(ticker, name, market),
                )
                self._ohlcv_cache[ticker] = ohlcv
            else:
                yf_info = await self.yf_client.get_stock_info(ticker, name, market)

            # v5.2: 실시간 현재가 우선 조회 (KIS→Naver→yfinance 순)
            live_price = yf_info.get("current_price", 0)
            try:
                realtime = await self._get_price(ticker, base_price=live_price)
                if realtime > 0:
                    live_price = realtime
            except Exception:
                logger.debug("_run_scan_for_stock get_price failed for %s", ticker, exc_info=True)

            info = StockInfo(
                ticker=ticker, name=name, market=market,
                market_cap=yf_info.get("market_cap", 0),
                per=yf_info.get("per", 0),
                roe=yf_info.get("roe", 0),
                debt_ratio=yf_info.get("debt_ratio", 0),
                consensus_target=yf_info.get("consensus_target", 0),
                current_price=live_price,
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
                    logger.debug("_run_scan_for_stock ML prediction failed for %s", ticker, exc_info=True)

            # v3.0: sentiment bonus
            sentiment_bonus = 0
            if ticker in self._sentiment_cache and HAS_SENTIMENT:
                try:
                    sentiment_bonus = get_sentiment_bonus(self._sentiment_cache[ticker])
                except Exception:
                    logger.debug("_run_scan_for_stock sentiment bonus failed for %s", ticker, exc_info=True)

            # v3.0: leading sector bonus
            from kstock.signal.policy_engine import _load_config as _load_policy_config
            try:
                pc = _load_policy_config()
                leading = pc.get("leading_sectors", {})
                tier1 = leading.get("tier1", [])
                tier2 = leading.get("tier2", [])
                leading_sector_bonus = 5 if sector in tier1 else 2 if sector in tier2 else 0
            except Exception:
                logger.debug("_run_scan_for_stock leading sector bonus failed for %s", ticker, exc_info=True)
                leading_sector_bonus = 0

            # v6.2: 멀티에이전트 보너스 연동
            multi_agent_bonus = 0
            try:
                from kstock.signal.agent_bridge import get_multi_agent_bonus
                multi_agent_bonus = get_multi_agent_bonus(self.db, ticker)
            except Exception:
                pass

            score = compute_composite_score(
                macro, flow, info, tech, self.scoring_config,
                mtf_bonus=mtf_bonus, sector_adj=sector_adj,
                policy_bonus=policy_bonus,
                ml_bonus=ml_bonus_val,
                sentiment_bonus=sentiment_bonus,
                leading_sector_bonus=leading_sector_bonus,
                multi_agent_bonus=multi_agent_bonus,
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
        """Get current price. KIS → Naver → yfinance 순 (v5.3)."""
        # 1순위: KIS API (실시간, 정확도 최우선)
        try:
            price = await self.kis.get_current_price(ticker, 0)
            if price > 0:
                logger.debug("Price %s: KIS=%s", ticker, price)
                return price
        except Exception:
            logger.debug("_get_price KIS failed for %s", ticker, exc_info=True)
        # 2순위: Naver Finance (장중 ~수분 지연)
        try:
            from kstock.ingest.naver_finance import NaverFinanceClient
            naver = NaverFinanceClient()
            price = await naver.get_current_price(ticker)
            if price > 0:
                logger.debug("Price %s: Naver=%s", ticker, price)
                return price
        except Exception:
            logger.debug("_get_price Naver failed for %s", ticker, exc_info=True)
        # 3순위: yfinance (전일 종가 기반, 지연 큼)
        market = "KOSPI"
        for s in self.all_tickers:
            if s["code"] == ticker:
                market = s.get("market", "KOSPI")
                break
        try:
            price = await self.yf_client.get_current_price(ticker, market)
            if price > 0:
                logger.debug("Price %s: yfinance=%s", ticker, price)
                return price
        except Exception:
            logger.debug("_get_price yfinance failed for %s", ticker, exc_info=True)
        # 4순위: base_price fallback
        if base_price > 0:
            logger.debug("Price %s: fallback=%s", ticker, base_price)
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
            logger.debug("_get_price_detail KIS failed for %s", ticker, exc_info=True)
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
                "\n".join(lines), reply_markup=get_reply_markup(context),
            )
        else:
            # No ticker: show portfolio overview
            last_ss = self.db.get_last_screenshot()
            if not last_ss:
                await update.message.reply_text(
                    "\U0001f4f8 먼저 계좌 스크린샷을 전송해주세요.\n"
                    "또는: /short [종목코드]\n예) /short 005930",
                    reply_markup=get_reply_markup(context),
                )
                return

            import json as _json
            try:
                holdings = _json.loads(last_ss.get("holdings_json", "[]") or "[]")
            except (_json.JSONDecodeError, TypeError):
                logger.debug("cmd_history holdings JSON parse failed", exc_info=True)
                holdings = []

            if not holdings:
                await update.message.reply_text(
                    "\U0001f4ca 보유 종목이 없습니다.", reply_markup=get_reply_markup(context),
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
                "\n".join(lines), reply_markup=get_reply_markup(context),
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

        # Get current asset from portfolio snapshot → screenshot → fallback
        import json
        current_asset = 175_000_000
        holdings_list = []
        snapshots = self.db.get_portfolio_snapshots(limit=1)
        if snapshots:
            current_asset = snapshots[0].get("total_value", 0) or 175_000_000
            try:
                holdings_list = json.loads(snapshots[0].get("holdings_json", "[]") or "[]")
            except (json.JSONDecodeError, TypeError):
                logger.debug("cmd_goal snapshot holdings JSON parse failed", exc_info=True)
                holdings_list = []
        else:
            last_ss = self.db.get_last_screenshot()
            if last_ss:
                current_asset = last_ss.get("total_eval", 175_000_000) or 175_000_000
                try:
                    h_json = last_ss.get("holdings_json", "[]")
                    holdings_list = json.loads(h_json) if h_json else []
                except (json.JSONDecodeError, TypeError):
                    logger.debug("cmd_goal screenshot holdings JSON parse failed", exc_info=True)
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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

    # -- v3.5 handlers ---------------------------------------------------------

    async def _menu_ai_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """AI 질문 모드 - 자주하는 질문 4개 버튼 + 직접 입력 안내."""
        from kstock.bot.bot_imports import make_feedback_row
        buttons = [
            [InlineKeyboardButton("🎯 4매니저 동시추천", callback_data="quick_q:mgr4")],
            [InlineKeyboardButton("📊 오늘 시장 분석", callback_data="quick_q:market")],
            [InlineKeyboardButton("💼 내 포트폴리오 조언", callback_data="quick_q:portfolio")],
            [InlineKeyboardButton("🔥 지금 매수할 종목", callback_data="quick_q:buy_pick")],
            [InlineKeyboardButton("⚠️ 리스크 점검", callback_data="quick_q:risk")],
            make_feedback_row("AI질문"),
        ]
        msg = (
            "🤖 Claude AI가 대기 중입니다\n\n"
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
                reply_markup=get_reply_markup(context),
            )
            return

        # 즉시 "처리 중..." 메시지 → edit로 교체
        placeholder = await update.message.reply_text(
            "🤖 Claude가 분석 중입니다..."
        )
        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            # 질문에 종목명이 있으면 실시간 가격을 주입
            enriched = question
            stock = None
            try:
                stock = self._detect_stock_query(question)
                if stock:
                    code = stock.get("code", "")
                    name = stock.get("name", code)
                    market = stock.get("market", "KOSPI")
                    ohlcv = await self.yf_client.get_ohlcv(code, market)
                    if ohlcv is not None and not ohlcv.empty:
                        from kstock.core.technical import compute_indicators
                        tech = compute_indicators(ohlcv)
                        # v5.3: KIS→Naver→yfinance 순 실시간 현재가
                        close = ohlcv["close"].astype(float)
                        cur = float(close.iloc[-1])
                        try:
                            live = await self._get_price(code, base_price=cur)
                            if live > 0:
                                cur = live
                        except Exception:
                            logger.debug("_handle_ai_question get_price failed for %s", code, exc_info=True)
                        if cur > 0:
                            enriched = (
                                f"{question}\n\n"
                                f"[{name}({code}) 실시간 데이터]\n"
                                f"현재가: {cur:,.0f}원\n"
                                f"이동평균: 5일 {tech.ma5:,.0f}원, "
                                f"20일 {tech.ma20:,.0f}원, "
                                f"60일 {tech.ma60:,.0f}원\n"
                                f"RSI: {tech.rsi:.1f}\n\n"
                                f"[절대 규칙] 위 실시간 데이터의 가격만 참고하라. "
                                f"너의 학습 데이터에 있는 과거 주가를 절대 사용 금지."
                            )
                            logger.info("AI질문 가격 주입: %s 현재가 %s원", name, f"{cur:,.0f}")
            except Exception as e:
                logger.warning("AI질문 가격 주입 실패: %s", e)

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(
                self.db, self.macro_client, self.yf_client,
            )
            v_names = {stock.get("name", "")} if stock and stock.get("name") else None
            answer = await handle_ai_question(enriched, ctx, self.db, chat_mem, verified_names=v_names)

            # AI 응답에서 후속 질문 파싱 → 버튼 변환
            answer, followup_buttons = self._parse_followup_buttons(answer)
            if not followup_buttons:
                followup_buttons = self._build_followup_buttons(question, stock)
            markup = InlineKeyboardMarkup(followup_buttons) if followup_buttons else None

            try:
                await placeholder.edit_text(answer, reply_markup=markup)
            except Exception:
                logger.debug("_handle_ai_question edit_text failed, falling back to reply_text", exc_info=True)
                await update.message.reply_text(
                    answer,
                    reply_markup=markup or get_reply_markup(context),
                )
        except Exception as e:
            logger.error("AI chat error: %s", e, exc_info=True)
            try:
                await placeholder.edit_text(
                    "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                )
            except Exception:
                logger.debug("_handle_ai_question error edit_text also failed", exc_info=True)
                await update.message.reply_text(
                    "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    reply_markup=get_reply_markup(context),
                )

    def _parse_followup_buttons(self, answer: str) -> tuple:
        """AI 응답에서 후속 질문을 파싱하여 버튼으로 변환.

        1순위: ---followup--- 구분자
        2순위: 텍스트 A/B/C/D 패턴 감지 (AI가 형식 안 따를 때)

        Returns:
            (cleaned_answer, buttons_list) — 버튼이 없으면 빈 리스트.
        """
        import re

        clean_answer = answer
        questions = []

        # 1순위: ---followup--- 구분자
        separator = "---followup---"
        if separator in answer:
            parts = answer.split(separator, 1)
            clean_answer = parts[0].rstrip()
            followup_text = parts[1].strip()
            questions = [
                q.strip().lstrip("- ").strip()
                for q in followup_text.splitlines()
                if q.strip() and len(q.strip()) >= 2
            ][:4]

        # 2순위: "다음 궁금하실 것들" / "A. ... B. ..." 패턴 감지
        if not questions:
            # "📌 다음 궁금하실 것들" 같은 헤더 이후의 A~F 항목 추출
            section_patterns = [
                r'(?:다음 궁금하실|더 궁금하|추가 질문|골라주|선택해).*?\n',
                r'💬.*?(?:뭐가 필요|궁금하|골라주).*?\n',
                r'📌.*?(?:다음|궁금|질문).*?\n',
            ]
            section_start = -1
            for pat in section_patterns:
                m = re.search(pat, answer)
                if m:
                    section_start = m.start()
                    break

            # A. / B. / C. 패턴 또는 🔴 A. / 🟡 B. 패턴 추출
            abc_pattern = re.compile(
                r'(?:^|\n)\s*(?:[\U0001f534\U0001f7e1\U0001f7e2\U0001f535\u26aa\u2753]\s*)?'
                r'[A-F]\.\s*(.+?)(?:\n\s*(?:→|  ).*)?$',
                re.MULTILINE,
            )
            matches = list(abc_pattern.finditer(answer))
            if matches:
                # 첫 매치 위치 이전까지가 본문
                first_match_start = matches[0].start()
                # 섹션 헤더가 있으면 그 앞부터 자르기
                cut_start = section_start if section_start >= 0 and section_start < first_match_start else first_match_start
                clean_answer = answer[:cut_start].rstrip()
                # 뒤에 남는 "뭐든 좋아요!" 같은 꼬리도 제거
                clean_answer = re.sub(
                    r'\n\s*(?:뭐든|편하게|골라주|위 [A-F]).*$', '',
                    clean_answer, flags=re.DOTALL,
                ).rstrip()
                questions = [m.group(1).strip().rstrip('?!') + '?' for m in matches][:4]

        if not questions:
            return answer, []

        # 2개씩 행으로 묶어서 버튼 생성
        buttons = []
        row = []
        for q in questions:
            # callback_data 64바이트 제한: UTF-8 기준으로 자르기
            label = q[:18]
            cb_q = q[:40]
            cb_data = f"followup_q:{cb_q}"
            # callback_data가 64바이트 초과하면 더 자르기
            while len(cb_data.encode('utf-8')) > 64:
                cb_q = cb_q[:-1]
                cb_data = f"followup_q:{cb_q}"
            row.append(InlineKeyboardButton(label, callback_data=cb_data))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        return clean_answer, buttons

    def _build_followup_buttons(self, question: str, stock: dict | None) -> list:
        """AI 응답 후 후속 질문 인라인 버튼 생성.

        v6.2.1: 모든 후속 버튼 세트에 닫기 버튼 추가.
        """
        buttons = []
        # v6.0: 4매니저 추천 후속 질문
        if question == "4매니저추천":
            buttons = [
                [
                    InlineKeyboardButton(
                        "🛡️ 가장 안전한 종목은?",
                        callback_data="followup_q:safe",
                    ),
                    InlineKeyboardButton(
                        "🔥 수익률 1위 종목은?",
                        callback_data="followup_q:top_pick",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "💼 내 보유종목과 분산?",
                        callback_data="followup:portfolio:",
                    ),
                    InlineKeyboardButton(
                        "🛒 매수 시작",
                        callback_data="bp:start",
                    ),
                ],
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ]
            return buttons
        if stock:
            ticker = stock.get("code", "")
            name = stock.get("name", "")
            # 종목 관련 후속 질문
            buttons = [
                [
                    InlineKeyboardButton(
                        "\U0001f7e2 지금 사도 돼?",
                        callback_data=f"followup:buy_timing:{ticker}",
                    ),
                    InlineKeyboardButton(
                        "\U0001f3af 목표가/손절가",
                        callback_data=f"followup:target:{ticker}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "\U0001f4ca 차트 분석",
                        callback_data=f"followup:chart:{ticker}",
                    ),
                    InlineKeyboardButton(
                        "\u2696\ufe0f 다른 종목 비교",
                        callback_data=f"followup:compare:{ticker}",
                    ),
                ],
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ]
        else:
            # 일반 질문 후속
            buttons = [
                [
                    InlineKeyboardButton(
                        "\U0001f4b0 내 포트폴리오 점검",
                        callback_data="followup:portfolio:",
                    ),
                    InlineKeyboardButton(
                        "\U0001f525 오늘 뭐 살까?",
                        callback_data="followup:buy_pick:",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "\U0001f30d 시장 전망",
                        callback_data="followup:market:",
                    ),
                    InlineKeyboardButton(
                        "\u26a0\ufe0f 리스크 점검",
                        callback_data="followup:risk:",
                    ),
                ],
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ]
        return buttons

    async def _action_followup(self, query, context, payload: str) -> None:
        """후속 질문 버튼 콜백 — AI에 후속 질문 전달."""
        parts = payload.split(":")
        qtype = parts[0] if parts else ""
        ticker = parts[1] if len(parts) > 1 else ""

        # buy_pick은 실시간 스캔 기반으로 처리
        if qtype == "buy_pick":
            await self._handle_buy_pick_with_live_data(query, context)
            return

        # 종목명 조회
        name = ticker
        for item in self.all_tickers:
            if item["code"] == ticker:
                name = item["name"]
                break

        # market/portfolio/risk는 실시간 데이터 주입 경로로 전달
        if qtype in ("market", "portfolio", "risk"):
            await self._handle_quick_question(query, context, qtype)
            return

        question_map = {
            "buy_timing": f"{name} 지금 매수 타이밍이야? 기술적 지표 기준으로 진입 시점 알려줘",
            "target": f"{name} 목표가와 손절가를 구체적으로 알려줘. 근거도 같이",
            "chart": f"{name} 차트 분석해줘. 이동평균선, RSI, MACD, 거래량 종합 판단",
            "compare": f"{name}과 같은 섹터 경쟁사 비교해줘. 어디가 더 매력적인지",
        }

        question = question_map.get(qtype, f"{name} 더 자세히 분석해줘")

        await safe_edit_or_reply(
            query, query.message.text + f"\n\n\U0001f4ad {question}..."
        )

        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            # 종목 관련이면 실시간 가격 주입 (KIS→Naver→yfinance 순)
            enriched = question
            v_names = None
            if ticker:
                try:
                    stock = self._detect_stock_query(name)
                    if stock:
                        code = stock.get("code", "")
                        market = stock.get("market", "KOSPI")
                        ohlcv = await self.yf_client.get_ohlcv(code, market)
                        if ohlcv is not None and not ohlcv.empty:
                            from kstock.core.technical import compute_indicators
                            tech = compute_indicators(ohlcv)
                            close = ohlcv["close"].astype(float)
                            cur = float(close.iloc[-1])
                            # v5.3: KIS→Naver→yfinance 순 실시간 현재가
                            try:
                                live = await self._get_price(code, base_price=cur)
                                if live > 0:
                                    cur = live
                            except Exception:
                                logger.debug("_handle_followup get_price failed for %s", code, exc_info=True)
                            if cur > 0:
                                v_names = {name}
                                enriched = (
                                    f"{question}\n\n"
                                    f"[{name}({code}) 실시간 데이터]\n"
                                    f"현재가: {cur:,.0f}원\n"
                                    f"RSI: {tech.rsi:.1f}\n"
                                    f"[절대 규칙] 위 실시간 데이터의 가격만 사용하라. "
                                    f"너의 학습 데이터에 있는 과거 주가를 절대 사용 금지."
                                )
                except Exception:
                    logger.debug("_handle_followup stock data enrichment failed", exc_info=True)

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(
                self.db, self.macro_client, self.yf_client,
            )
            answer = await handle_ai_question(enriched, ctx, self.db, chat_mem, verified_names=v_names)

            # 후속 질문 파싱 → 버튼
            answer, followup_buttons = self._parse_followup_buttons(answer)
            if not followup_buttons:
                stock_data = {"code": ticker, "name": name, "market": "KOSPI"} if ticker else None
                followup_buttons = self._build_followup_buttons(question, stock_data)
            markup = InlineKeyboardMarkup(followup_buttons) if followup_buttons else None

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=answer,
                reply_markup=markup,
            )
        except Exception as e:
            logger.error("Followup AI error: %s", e, exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ AI 응답 중 오류가 발생했습니다.\n💡 잠시 후 같은 질문을 다시 시도하시거나, '💬 AI질문' 메뉴를 이용해주세요.",
            )

    async def _action_followup_dynamic(self, query, context, payload: str) -> None:
        """AI가 생성한 동적 후속 질문 버튼 콜백."""
        question = payload  # payload가 곧 질문 텍스트

        await safe_edit_or_reply(
            query, query.message.text + f"\n\n\U0001f4ad {question}..."
        )

        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            # v5.5: 종목명이 있으면 _get_price로 실시간 가격 주입
            enriched = question
            v_names = None
            stock = None
            try:
                stock = self._detect_stock_query(question)
                if stock:
                    code = stock.get("code", "")
                    sname = stock.get("name", code)
                    live = await self._get_price(code, base_price=0)
                    if live > 0:
                        v_names = {sname}
                        rsi_str = ""
                        try:
                            ohlcv = await self.yf_client.get_ohlcv(code, "KOSPI")
                            if ohlcv is not None and not ohlcv.empty:
                                from kstock.core.technical import compute_indicators
                                tech = compute_indicators(ohlcv)
                                rsi_str = f"\nRSI: {tech.rsi:.1f}"
                        except Exception:
                            logger.debug("_handle_dynamic_followup RSI fetch failed for %s", code, exc_info=True)
                        enriched = (
                            f"{question}\n\n"
                            f"[{sname}({code}) 실시간 데이터]\n"
                            f"현재가: {live:,.0f}원{rsi_str}\n"
                            f"[절대 규칙] 위 실시간 데이터의 가격만 사용하라. "
                            f"너의 학습 데이터에 있는 과거 주가를 절대 사용 금지."
                        )
            except Exception:
                logger.debug("_handle_dynamic_followup stock enrichment failed", exc_info=True)

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(
                self.db, self.macro_client, self.yf_client,
            )
            answer = await handle_ai_question(enriched, ctx, self.db, chat_mem, verified_names=v_names)

            # 후속 질문 파싱 → 버튼
            answer, followup_buttons = self._parse_followup_buttons(answer)
            if not followup_buttons:
                followup_buttons = self._build_followup_buttons(question, stock)
            markup = InlineKeyboardMarkup(followup_buttons) if followup_buttons else None

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=answer,
                reply_markup=markup,
            )
        except Exception as e:
            logger.error("Dynamic followup AI error: %s", e, exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ AI 응답 중 오류가 발생했습니다.\n💡 잠시 후 같은 질문을 다시 시도하시거나, '💬 AI질문' 메뉴를 이용해주세요.",
            )

    async def _handle_quick_question(
        self, query, context: ContextTypes.DEFAULT_TYPE, question_type: str
    ) -> None:
        """Handle quick question buttons from AI chat menu.

        v5.4: 모든 질문 유형에 실시간 데이터 주입. 목업/학습 데이터 사용 완전 차단.
        """
        # buy_pick은 실시간 스캔 데이터를 직접 사용 (AI 환각 방지)
        if question_type == "buy_pick":
            await self._handle_buy_pick_with_live_data(query, context)
            return

        # v6.0: 4매니저 동시 추천
        if question_type == "mgr4":
            await self._handle_4manager_picks(query, context)
            return

        if not self.anthropic_key:
            await query.edit_message_text(
                "주호님, AI 기능을 사용하려면 ANTHROPIC_API_KEY 설정이 필요합니다."
            )
            return

        await query.edit_message_text(
            "🤖 Claude가 실시간 데이터 수집 + 분석 중..."
        )

        try:
            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory
            from datetime import datetime
            now = datetime.now(KST)

            # 1. 실시간 매크로 데이터 수집
            macro_block = ""
            try:
                snap = await self.macro_client.get_snapshot()
                parts = []
                if hasattr(snap, 'vix') and snap.vix:
                    parts.append(f"VIX: {snap.vix:.1f}")
                if hasattr(snap, 'spx_change_pct') and snap.spx_change_pct:
                    parts.append(f"S&P500: {snap.spx_change_pct:+.2f}%")
                if hasattr(snap, 'nasdaq_change_pct') and snap.nasdaq_change_pct:
                    parts.append(f"나스닥: {snap.nasdaq_change_pct:+.2f}%")
                if hasattr(snap, 'usdkrw') and snap.usdkrw:
                    parts.append(f"원/달러: {snap.usdkrw:,.0f}원")
                if hasattr(snap, 'fear_greed_score') and snap.fear_greed_score:
                    parts.append(f"공포탐욕: {snap.fear_greed_score:.0f}")
                if hasattr(snap, 'us10y') and snap.us10y:
                    parts.append(f"미국10Y: {snap.us10y:.2f}%")
                if parts:
                    macro_block = " | ".join(parts)
            except Exception:
                logger.debug("_action_quick_question macro snapshot failed", exc_info=True)
                macro_block = "매크로 데이터 조회 실패"

            # 2. KOSPI/KOSDAQ 실시간 지수
            index_block = ""
            try:
                for idx_code, idx_name in [("0001", "KOSPI"), ("2001", "KOSDAQ")]:
                    p = await self._get_price(idx_code, base_price=0)
                    if p > 0:
                        index_block += f"{idx_name}: {p:,.2f} | "
            except Exception:
                logger.debug("_action_quick_question index price fetch failed", exc_info=True)

            # 3. 보유종목 실시간 가격
            holdings = self.db.get_active_holdings()
            portfolio_block = ""
            v_names = set()
            if holdings and question_type in ("portfolio", "risk", "flow"):
                pf_lines = []
                for h in holdings[:10]:
                    ticker = h.get("ticker", "")
                    hname = h.get("name", ticker)
                    buy_price = h.get("buy_price", 0) or h.get("avg_price", 0) or 0
                    qty = h.get("quantity", 0)
                    if not ticker:
                        continue
                    v_names.add(hname)
                    try:
                        live = await self._get_price(ticker, base_price=buy_price)
                        if live > 0:
                            pnl = ((live - buy_price) / buy_price * 100) if buy_price > 0 else 0
                            pf_lines.append(
                                f"- {hname}({ticker}): 현재가 {live:,.0f}원 | "
                                f"매수가 {buy_price:,.0f}원 | 수량 {qty}주 | "
                                f"수익률 {pnl:+.1f}%"
                            )
                        else:
                            pf_lines.append(f"- {hname}({ticker}): 매수가 {buy_price:,.0f}원 | 수량 {qty}주")
                    except Exception:
                        logger.debug("_action_quick_question holding price fetch failed for %s", ticker, exc_info=True)
                        pf_lines.append(f"- {hname}({ticker}): 매수가 {buy_price:,.0f}원 | 수량 {qty}주")
                if pf_lines:
                    portfolio_block = "\n".join(pf_lines)

            # v6.2.2: 외인/기관 수급 quick question
            if question_type == "flow" and holdings:
                flow_lines = []
                for h in holdings[:8]:
                    ticker = h.get("ticker", "")
                    hname = h.get("name", ticker)
                    try:
                        frgn = await self.kis.get_foreign_flow(ticker, days=5)
                        inst = await self.kis.get_institution_flow(ticker, days=5)
                        f_net = int(frgn["net_buy_volume"].sum()) if len(frgn) > 0 else 0
                        i_net = int(inst["net_buy_volume"].sum()) if len(inst) > 0 else 0
                        flow_lines.append(
                            f"- {hname}: 외인 {f_net:+,}주 / 기관 {i_net:+,}주 (5일 누적)"
                        )
                    except Exception:
                        pass
                if flow_lines:
                    portfolio_block += "\n\n[외인/기관 수급 5일]\n" + "\n".join(flow_lines)

            # 4. 질문 + 실시간 데이터 조합
            base_questions = {
                "market": "오늘 시장 전체 흐름을 분석하고, 지금 어떤 전략이 유효한지 판단해주세요",
                "portfolio": "주호님의 보유종목 전체를 점검하고, 각 종목별로 지금 해야 할 행동(홀딩/추매/익절/손절)을 구체적으로 알려주세요",
                "risk": "주호님의 포트폴리오 리스크를 점검해주세요. 집중도, 섹터 편중, 손실 종목, 전체 시장 리스크를 분석하고 대응 방안을 알려주세요",
                "flow": "주호님의 보유종목 외인/기관 수급 현황을 분석하고, 수급 흐름에 따른 매수/매도 판단을 알려주세요. 외인과 기관이 동시에 사는 종목은 특히 주목해주세요",
            }
            base_q = base_questions.get(question_type, "오늘 시장 어떤가요?")

            data_sections = [f"[실시간 데이터 — {now.strftime('%Y-%m-%d %H:%M')} KST]"]
            if macro_block:
                data_sections.append(f"글로벌: {macro_block}")
            if index_block:
                data_sections.append(f"지수: {index_block.rstrip(' | ')}")
            if portfolio_block:
                data_sections.append(f"\n[보유종목 실시간 현황]\n{portfolio_block}")

            enriched = (
                f"{base_q}\n\n"
                + "\n".join(data_sections)
                + "\n\n[절대 규칙] 위 실시간 데이터만 사용하라. 학습 데이터의 과거 주가/지수 절대 사용 금지."
            )

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(self.db, self.macro_client, self.yf_client)
            answer = await handle_ai_question(enriched, ctx, self.db, chat_mem, verified_names=v_names or None)

            # 후속 버튼
            answer, followup_buttons = self._parse_followup_buttons(answer)
            if not followup_buttons:
                followup_buttons = self._build_followup_buttons(base_q, None)
            markup = InlineKeyboardMarkup(followup_buttons) if followup_buttons else None

            try:
                await query.edit_message_text(answer, reply_markup=markup)
            except Exception:
                logger.debug("_action_quick_question edit_text failed, falling back", exc_info=True)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=answer,
                    reply_markup=markup,
                )
        except Exception as e:
            logger.error("Quick question error: %s", e, exc_info=True)
            try:
                await query.edit_message_text(
                    "주호님, AI 응답 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                )
            except Exception:
                logger.debug("_action_quick_question error recovery edit_text also failed", exc_info=True)

    @staticmethod
    def _fix_hallucinated_prices(text: str, price_map: dict[str, float]) -> str:
        """AI 응답에서 종목별 환각 가격을 실제 가격 기준으로 교체.

        price_map: {종목명: 실시간가격} 매핑.
        종목명 이후 15줄 내에서 실제 가격 대비 ±30% 범위 밖 가격을
        '[현재가 XXX,XXX원 기준]'으로 교체.
        """
        if not price_map:
            return text

        lines = text.split("\n")
        result_lines = []
        name_ranges = {}
        for name, price in price_map.items():
            if price > 0:
                name_ranges[name] = (price * 0.70, price * 1.30, price)

        current_stock = None
        stock_line_count = 0
        for line in lines:
            for name in name_ranges:
                if name in line:
                    current_stock = name
                    stock_line_count = 0
                    break

            if current_stock and stock_line_count < 15:
                stock_line_count += 1
                lo, hi, actual = name_ranges[current_stock]

                def _replace_bad(m, _lo=lo, _hi=hi, _actual=actual):
                    price_str = m.group(1)
                    val = float(price_str.replace(",", ""))
                    if val < _lo or val > _hi:
                        return f"[현재가 {_actual:,.0f}원 기준]"
                    return m.group(0)

                # "73,500~76,500원" 같은 범위 패턴 먼저 처리
                line = re.sub(
                    r"(\d{1,3}(?:,\d{3})+)\s*~\s*(\d{1,3}(?:,\d{3})+)\s*원",
                    lambda m, _lo=lo, _hi=hi, _actual=actual: (
                        f"[현재가 {_actual:,.0f}원 기준]"
                        if (float(m.group(1).replace(",", "")) < _lo
                            or float(m.group(2).replace(",", "")) > _hi)
                        else m.group(0)
                    ),
                    line,
                )
                # 단일 가격 패턴
                line = re.sub(
                    r"(\d{1,3}(?:,\d{3})+)\s*원",
                    _replace_bad,
                    line,
                )
            result_lines.append(line)

        return "\n".join(result_lines)

    async def _handle_4manager_picks(self, query, context) -> None:
        """v6.0: 4매니저 동시추천 — 1탭으로 4명 매니저가 각자 투자 성향에 맞는 종목 추천.

        리버모어(단타)/오닐(스윙)/린치(포지션)/버핏(장기) 병렬 분석.
        ~3-5초 내 완료 (순차 20초 대비 75% 단축).
        """
        await query.edit_message_text(
            "🎯 4매니저가 동시에 종목을 분석 중...\n"
            "⚡리버모어 🔥오닐 📊린치 💎버핏\n"
            "(약 5초 소요)"
        )

        try:
            # 1. 4개 horizon 종목 병렬 수집
            import asyncio
            default_budget = 5_000_000  # 500만원 기준

            horizon_tasks = {
                hz: self._get_horizon_picks_data(hz, default_budget)
                for hz in ("scalp", "short", "mid", "long")
            }
            horizon_results = await asyncio.gather(*horizon_tasks.values())

            picks_by_horizon = {}
            for hz, (picks, _err) in zip(horizon_tasks.keys(), horizon_results):
                picks_by_horizon[hz] = picks

            # 2. 매크로 컨텍스트 수집 (풍부한 버전)
            market_context = ""
            try:
                snap = await self.macro_client.get_snapshot()
                from kstock.signal.strategies import get_regime_mode
                regime = get_regime_mode(snap)
                parts = [
                    f"VIX: {snap.vix:.1f}",
                    f"나스닥: {snap.nasdaq_change_pct:+.2f}%",
                    f"S&P500: {snap.spx_change_pct:+.2f}%",
                    f"원/달러: {snap.usdkrw:,.0f}원",
                    f"레짐: {regime['label']}",
                ]
                if hasattr(snap, "kospi") and snap.kospi > 0:
                    parts.insert(0, f"코스피: {snap.kospi:,.0f}({snap.kospi_change_pct:+.2f}%)")
                if hasattr(snap, "koru_price") and snap.koru_price > 0:
                    parts.append(f"KORU: ${snap.koru_price:.2f}({snap.koru_change_pct:+.1f}%)")
                market_context = " | ".join(parts)
            except Exception:
                logger.debug("_action_4manager_picks macro context fetch failed", exc_info=True)

            # 2.5. 공유 컨텍스트 빌드 (위기/뉴스/교훈/포트폴리오 등)
            shared_context = None
            try:
                from kstock.bot.context_builder import build_manager_shared_context
                shared_context = await build_manager_shared_context(
                    self.db, self.macro_client,
                )
            except Exception:
                logger.debug("_action_4manager_picks shared context build failed", exc_info=True)

            # 3. 4매니저 동시 AI 분석 (asyncio.gather) — 공유 컨텍스트 포함
            from kstock.bot.investment_managers import (
                get_all_managers_picks, MANAGERS, MANAGER_HORIZON_MAP,
            )
            current_alert = getattr(self, '_alert_mode', 'normal')
            manager_analyses = await get_all_managers_picks(
                picks_by_horizon, market_context, shared_context,
                alert_mode=current_alert,
            )

            # 4. 결과 포맷
            lines = ["🎯 4매니저 동시추천", f"{'━' * 20}"]
            if market_context:
                lines.append(f"📈 {market_context}\n")

            # 각 매니저 추천 1순위 종목 수집 (버튼용)
            top_picks_for_buttons = {}

            for mgr_key in ("scalp", "swing", "position", "long_term"):
                analysis = manager_analyses.get(mgr_key, "")
                if analysis:
                    lines.append(f"\n{analysis}")

                # 버튼용: 해당 horizon의 1순위 종목
                hz = MANAGER_HORIZON_MAP.get(mgr_key, "")
                hz_picks = picks_by_horizon.get(hz, [])
                if hz_picks:
                    top_picks_for_buttons[mgr_key] = hz_picks[0]

            result_text = "\n".join(lines)

            # 5. 액션 버튼 구성
            from kstock.bot.bot_imports import make_feedback_row
            buttons = []

            # 각 매니저 1순위 종목 "담기" 버튼
            add_row = []
            for mgr_key, pick in top_picks_for_buttons.items():
                mgr = MANAGERS.get(mgr_key, {})
                emoji = mgr.get("emoji", "📌")
                hz = MANAGER_HORIZON_MAP.get(mgr_key, "scalp")
                ticker = pick.get("ticker", "")
                name = pick.get("name", "")[:5]
                cb = f"bp:add:{ticker}:{hz}"
                if len(cb) <= 64:
                    add_row.append(
                        InlineKeyboardButton(
                            f"{emoji}{name} 담기",
                            callback_data=cb,
                        )
                    )
                if len(add_row) == 2:
                    buttons.append(add_row)
                    add_row = []
            if add_row:
                buttons.append(add_row)

            # 후속 질문 버튼
            followup_buttons = self._build_followup_buttons("4매니저추천", None)
            if followup_buttons:
                buttons.extend(followup_buttons)

            buttons.append(make_feedback_row("4매니저추천"))

            markup = InlineKeyboardMarkup(buttons) if buttons else None

            # 6. 메시지 전송 (4096자 제한 → 페이지네이션)
            if len(result_text) > 3800:
                # 긴 메시지는 페이지 분할 전송
                await send_long_message(query.message, result_text, reply_markup=markup)
            else:
                try:
                    await query.edit_message_text(result_text, reply_markup=markup)
                except Exception:
                    logger.debug("_action_4manager_picks edit_text failed, falling back", exc_info=True)
                    await query.message.reply_text(
                        result_text, reply_markup=markup or get_reply_markup(context),
                    )

        except Exception as e:
            logger.error("4manager picks error: %s", e, exc_info=True)
            retry_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 다시 시도", callback_data="quick_q:mgr4")],
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ])
            await safe_edit_or_reply(
                query,
                "⚠️ 4매니저 분석 중 오류가 발생했습니다.\n"
                "네트워크 또는 AI 서비스 일시 장애일 수 있습니다.\n\n"
                "아래 버튼으로 다시 시도해주세요.",
                reply_markup=retry_kb,
            )

    async def _handle_buy_pick_with_live_data(self, query, context) -> None:
        """매수 추천 — 실시간 스캔 데이터 기반 (AI 환각 주가 완전 차단).

        v5.3: AI가 자체 학습 데이터의 옛날 주가를 사용하는 문제를 근본 해결.
        실시간 스캔 → TOP3 실시간 가격 조회 → 가격 데이터를 AI에 주입.
        """
        await query.edit_message_text(
            "🔍 실시간 종목 스캔 + 가격 조회 중..."
        )

        try:
            # 1. 실시간 스캔 (캐시 또는 새로 실행)
            from datetime import datetime
            now = datetime.now(KST)
            cache_age = (now - self._scan_cache_time).total_seconds() if hasattr(self, '_scan_cache_time') and self._scan_cache_time else 9999
            if cache_age < 600 and hasattr(self, '_last_scan_results') and self._last_scan_results:
                results = self._last_scan_results
            else:
                results = await self._scan_all_stocks()
                self._last_scan_results = results
                self._scan_cache_time = now

            if not results:
                await query.edit_message_text(
                    "⚠️ 현재 스캔 결과가 없습니다. 잠시 후 다시 시도해주세요."
                )
                return

            # 2. TOP3 실시간 가격 강제 조회 (KIS→Naver→yfinance)
            top3 = results[:3]
            stock_data_lines = []
            live_prices = {}  # 종목명 → 실시간 가격 매핑
            for i, r in enumerate(top3, 1):
                live_price = r.info.current_price
                try:
                    p = await self._get_price(r.ticker, base_price=live_price)
                    if p > 0:
                        live_price = p
                except Exception:
                    logger.debug("_handle_buy_pick get_price failed for %s", r.ticker, exc_info=True)
                live_prices[r.name] = live_price

                medals = {1: "1위", 2: "2위", 3: "3위"}
                signal_kr = {"BUY": "매수", "WATCH": "관심", "HOLD": "홀딩", "SELL": "매도"}.get(r.score.signal, "관심")
                stock_data_lines.append(
                    f"{medals.get(i, f'{i}위')}. {r.name}({r.ticker})\n"
                    f"  현재가: {live_price:,.0f}원 | 점수: {r.score.composite:.1f}/100 | 신호: {signal_kr}\n"
                    f"  RSI: {r.tech.rsi:.1f} | EMA50/200: {r.tech.ema_50:,.0f}/{r.tech.ema_200:,.0f}"
                )

            stock_block = "\n".join(stock_data_lines)

            # 3. AI에 실시간 데이터 주입해서 분석 요청
            enriched_question = (
                f"아래 3개 종목은 K-Quant 스캔 엔진이 실시간으로 선정한 오늘의 추천종목이다.\n"
                f"[절대 규칙]\n"
                f"1. 아래 데이터의 현재가만 사용하라. 너의 학습 데이터에 있는 과거 주가를 절대 사용 금지.\n"
                f"2. '종가', '시가총액', '시가', '고가', '저가' 등 아래에 없는 가격 정보를 절대 생성 금지.\n"
                f"3. 목표가/손절가는 반드시 아래 현재가 기준 비율(%)로 산출하라.\n\n"
                f"[실시간 스캔 결과 — {now.strftime('%Y-%m-%d %H:%M')} 기준]\n"
                f"{stock_block}\n\n"
                f"각 종목에 대해 간단히 분석하고, 매수 매력도를 설명해줘.\n"
                f"현재가는 위 데이터를 그대로 사용하라."
            )

            from kstock.bot.chat_handler import handle_ai_question
            from kstock.bot.context_builder import build_full_context_with_macro
            from kstock.bot.chat_memory import ChatMemory

            chat_mem = ChatMemory(self.db)
            ctx = await build_full_context_with_macro(self.db, self.macro_client, self.yf_client)
            v_names = {r.name for r in top3}
            answer = await handle_ai_question(enriched_question, ctx, self.db, chat_mem, verified_names=v_names)

            # 4. 가격 환각 후처리: AI가 생성한 가격을 실제 가격과 교체
            answer = self._fix_hallucinated_prices(answer, live_prices)

            # 후속 질문 파싱
            answer, followup_buttons = self._parse_followup_buttons(answer)
            if not followup_buttons:
                followup_buttons = self._build_followup_buttons("매수 추천", None)
            # v6.0: 매수 시작 버튼 추가
            followup_buttons.append([
                InlineKeyboardButton(
                    "🛒 이 종목들로 매수 시작",
                    callback_data="bp:start",
                ),
            ])
            markup = InlineKeyboardMarkup(followup_buttons) if followup_buttons else None

            try:
                await query.edit_message_text(answer, reply_markup=markup)
            except Exception:
                logger.debug("_handle_buy_pick edit_text failed, falling back", exc_info=True)
                await query.message.reply_text(answer, reply_markup=markup or get_reply_markup(context))
        except Exception as e:
            logger.error("Buy pick with live data error: %s", e, exc_info=True)
            retry_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 다시 시도", callback_data="quick_q:buy_pick")],
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ])
            await safe_edit_or_reply(
                query,
                "⚠️ 추천 종목 분석 중 오류가 발생했습니다.\n"
                "스캔 또는 AI 서비스 일시 장애일 수 있습니다.\n\n"
                "아래 버튼으로 다시 시도해주세요.",
                reply_markup=retry_kb,
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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

    async def cmd_finance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /finance command."""
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: /finance [종목코드]\n예) /finance 005930",
                reply_markup=get_reply_markup(context),
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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

    async def cmd_consensus(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /consensus command."""
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: /consensus [종목코드 또는 종목명]\n예) /consensus 에코프로",
                reply_markup=get_reply_markup(context),
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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

    async def _menu_short(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """공매도 분석 메뉴 — 보유종목 버튼 표시."""
        holdings = self.db.get_active_holdings()
        buttons = []
        for h in holdings[:6]:
            ticker = h.get("ticker", "")
            name = h.get("name", ticker)
            if ticker:
                buttons.append([InlineKeyboardButton(
                    f"📊 {name} 공매도", callback_data=f"short:{ticker}",
                )])

        if buttons:
            buttons.append([InlineKeyboardButton(
                "📊 전체 보유종목 요약", callback_data="short:all",
            )])
            await update.message.reply_text(
                "📊 공매도/레버리지 분석\n\n"
                "보유종목을 선택하면 공매도 현황을 분석합니다:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            # 보유종목 없으면 기존 방식
            await self.cmd_short(update, context)

    async def _action_short_analysis(self, query, context, payload: str) -> None:
        """공매도 분석 콜백 — 종목별 또는 전체."""
        if payload == "all":
            # 전체 보유종목 요약 (기존 cmd_short 로직)
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.edit_message_text("📊 보유 종목이 없습니다.")
                return
            lines = ["📊 포트폴리오 공매도/레버리지 현황\n"]
            for h in holdings[:10]:
                ticker = h.get("ticker", "")
                name = h.get("name", "?")
                if not ticker:
                    continue
                short_data = self.db.get_short_selling(ticker, days=20)
                signal = analyze_short_selling(short_data, ticker, name)
                if signal.is_overheated:
                    status = "🚨 과열"
                elif signal.score_adj <= -5:
                    status = "🔴 주의"
                elif signal.score_adj >= 5:
                    status = "🟢 긍정"
                else:
                    status = "⚪ 보통"
                latest_ratio = 0.0
                if short_data:
                    latest_ratio = short_data[-1].get("short_ratio", 0.0)
                lines.append(
                    f"  {status} {name} ({ticker})\n"
                    f"    공매도 비율: {latest_ratio:.1f}% | "
                    f"점수: {signal.score_adj:+d}"
                )
            dismiss_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ])
            text = "\n".join(lines)
            if len(text) > 4000:
                await send_long_message(query.message, text, reply_markup=dismiss_kb)
            else:
                try:
                    await query.edit_message_text(text, reply_markup=dismiss_kb)
                except Exception:
                    await query.message.reply_text(text, reply_markup=dismiss_kb)
        else:
            # 개별 종목 분석
            ticker = payload
            name = ticker
            for item in self.all_tickers:
                if item["code"] == ticker:
                    name = item["name"]
                    break
            # holdings에서도 이름 찾기
            h = self.db.get_holding_by_ticker(ticker)
            if h:
                name = h.get("name", name)

            await query.edit_message_text(f"🔍 {name} ({ticker}) 공매도 분석 중...")

            short_data = self.db.get_short_selling(ticker, days=60)

            # v5.8: 데이터 없으면 실시간 수집 시도
            if not short_data:
                try:
                    from kstock.ingest.naver_finance import get_short_selling
                    fresh = await get_short_selling(ticker, days=20)
                    if fresh:
                        for d in fresh[:10]:
                            self.db.add_short_selling(
                                ticker=ticker,
                                date_str=d["date"],
                                short_volume=d["short_volume"],
                                total_volume=d["total_volume"],
                                short_ratio=d["short_ratio"],
                                short_balance=d.get("short_balance", 0),
                                short_balance_ratio=d.get("short_balance_ratio", 0.0),
                            )
                        short_data = self.db.get_short_selling(ticker, days=60)
                except Exception:
                    logger.debug("_action_short_selling data fetch failed for %s", ticker, exc_info=True)

            if not short_data:
                await query.message.reply_text(
                    f"📊 {name} ({ticker}) 공매도 분석\n\n"
                    f"공매도 데이터가 아직 수집되지 않았습니다.\n"
                    f"매일 16:15에 자동 수집됩니다.\n\n"
                    f"내일 다시 확인해주세요."
                )
                return
            margin_data = self.db.get_margin_balance(ticker, days=60)
            lines: list[str] = []

            signal = analyze_short_selling(short_data, ticker, name)
            lines.append(format_short_alert(signal, short_data))
            lines.append("")

            price_data = self.db.get_supply_demand(ticker, days=20)
            pattern_result = detect_all_patterns(
                short_data, price_data, ticker=ticker, name=name,
            )
            if pattern_result.patterns:
                lines.append(format_pattern_report(pattern_result))
                lines.append("")

            if margin_data:
                margin_signal = detect_margin_patterns(
                    margin_data, price_data, short_data, ticker, name,
                )
                lines.append(format_margin_alert(margin_signal, margin_data))
                lines.append("")
                combined = compute_combined_leverage_score(
                    signal.score_adj, margin_signal.total_score_adj,
                )
                lines.append(f"📊 공매도+레버리지 종합: {combined:+d}점")

            dismiss_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ])
            await send_long_message(query.message, "\n".join(lines), reply_markup=dismiss_kb)

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
                await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
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
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

        except Exception as e:
            logger.error("Future tech command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 미래기술 워치리스트 조회 중 오류가 발생했습니다.",
                reply_markup=get_reply_markup(context),
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

            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("History command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 계좌 추이 조회 중 오류가 발생했습니다.",
                reply_markup=get_reply_markup(context),
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
                    reply_markup=get_reply_markup(context),
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
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Risk command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 리스크 조회 중 오류가 발생했습니다.",
                reply_markup=get_reply_markup(context),
            )

    async def cmd_health(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /health command - show system health."""
        try:
            self._persist_chat_id(update)
            checks = run_health_checks(db_path=self.db.db_path)
            msg = format_system_report(checks, db_path=self.db.db_path)
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Health command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 시스템 상태 조회 중 오류가 발생했습니다.",
                reply_markup=get_reply_markup(context),
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
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Performance command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 성과 조회 중 오류가 발생했습니다.",
                reply_markup=get_reply_markup(context),
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
                [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
            ]
            await update.message.reply_text(
                "\U0001f4ca 시나리오 분석을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error("Scenario command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 시나리오 분석 오류.",
                reply_markup=get_reply_markup(context),
            )

    async def _action_multi_run(self, query, context, payload: str) -> None:
        """멀티 에이전트 분석 인라인 버튼 콜백."""
        ticker = payload
        try:
            await query.edit_message_text(
                f"\U0001f4ca {ticker} 멀티 에이전트 분석 중... (3개 AI 동시 분석)"
            )

            name = ticker
            market = "KOSPI"
            for item in self.all_tickers:
                if item["code"] == ticker:
                    name = item["name"]
                    market = item.get("market", "KOSPI")
                    break

            # 실시간 가격 가져오기 (KIS → Naver → yfinance 체인)
            live_price = 0
            try:
                live_price = await self._get_price(ticker, base_price=0)
            except Exception:
                logger.debug("_action_multi_run get_price failed for %s", ticker, exc_info=True)

            stock_data = {"name": name, "ticker": ticker, "price": live_price}
            try:
                ohlcv = await self.yf_client.get_ohlcv(ticker, market)
                if ohlcv is not None and not ohlcv.empty:
                    tech = compute_indicators(ohlcv)
                    close = ohlcv["close"].astype(float)
                    ohlcv_price = float(close.iloc[-1])
                    # 실시간 가격이 없으면 OHLCV 종가 사용
                    if live_price <= 0:
                        live_price = ohlcv_price
                        stock_data["price"] = live_price
                    vol_series = ohlcv["volume"].astype(float)
                    avg_vol_20 = float(vol_series.tail(20).mean()) if len(vol_series) >= 20 else float(vol_series.mean())
                    cur_vol = float(vol_series.iloc[-1])
                    stock_data.update({
                        "ma5": tech.ma5, "ma20": tech.ma20,
                        "ma60": tech.ma60, "ma120": tech.ma120,
                        "rsi": tech.rsi,
                        "macd": tech.macd,
                        "macd_signal": tech.macd_signal,
                        "macd_signal_cross": tech.macd_signal_cross,
                        "bb_pctb": tech.bb_pctb,
                        "bb_squeeze": tech.bb_squeeze,
                        "ema_50": tech.ema_50,
                        "ema_200": tech.ema_200,
                        "golden_cross": tech.golden_cross,
                        "dead_cross": tech.dead_cross,
                        "weekly_trend": tech.weekly_trend,
                        "volume": cur_vol,
                        "avg_volume_20": avg_vol_20,
                        "volume_ratio": round(cur_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0,
                        "high_52w": float(close.max()),
                        "low_52w": float(close.min()),
                        "prices_5d": [float(x) for x in close.tail(5).tolist()],
                    })
            except Exception as e:
                logger.warning("멀티분석 OHLCV 가져오기 실패 (%s): %s", ticker, e)

            # 재무 데이터
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

            # 수급 데이터 (외국인 + 기관)
            try:
                frgn_df = await self.kis.get_foreign_flow(ticker, days=5)
                if frgn_df is not None and not frgn_df.empty and "net_buy_volume" in frgn_df.columns:
                    net_vols = frgn_df["net_buy_volume"].tolist()
                    stock_data["foreign_net_5d"] = int(sum(net_vols))
                    stock_data["foreign_flow_detail"] = ", ".join(
                        f"{int(v):+,}" for v in net_vols[-5:]
                    )
                    if "net_buy_amount" in frgn_df.columns:
                        stock_data["avg_trade_value"] = float(
                            frgn_df["net_buy_amount"].abs().mean()
                        )
            except Exception as e:
                logger.debug("멀티분석 외국인 수급 실패 (%s): %s", ticker, e)

            try:
                inst_df = await self.kis.get_institution_flow(ticker, days=5)
                if inst_df is not None and not inst_df.empty and "net_buy_volume" in inst_df.columns:
                    net_vols = inst_df["net_buy_volume"].tolist()
                    stock_data["inst_net_5d"] = int(sum(net_vols))
                    stock_data["inst_flow_detail"] = ", ".join(
                        f"{int(v):+,}" for v in net_vols[-5:]
                    )
            except Exception as e:
                logger.debug("멀티분석 기관 수급 실패 (%s): %s", ticker, e)

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

            # v6.2: 신호 성과 추적 기록
            try:
                self.db.save_signal_performance(
                    signal_source="multi_agent",
                    signal_type="analysis",
                    ticker=ticker,
                    name=name,
                    signal_date=datetime.now(KST).strftime("%Y-%m-%d"),
                    signal_score=report.combined_score,
                    signal_price=price,
                )
            except Exception:
                pass

            # 후속 버튼: 다른 종목 분석, 피드백, 닫기
            buttons = []
            # 보유종목 중 다른 종목 분석 버튼
            holdings = self.db.get_active_holdings()
            other_btns = []
            for h in holdings:
                hticker = h.get("ticker", "")
                hname = h.get("name", hticker)
                if hticker and hticker != ticker:
                    other_btns.append(
                        InlineKeyboardButton(
                            f"\U0001f4ca {hname[:6]}",
                            callback_data=f"multi_run:{hticker}",
                        )
                    )
                    if len(other_btns) >= 3:
                        break
            if other_btns:
                buttons.append(other_btns)
            # 피드백 행
            buttons.append(make_feedback_row("멀티분석"))

            await query.edit_message_text(
                msg,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error("Multi-run callback error: %s", e, exc_info=True)
            await safe_edit_or_reply(
                query,
                "⚠️ 분석 중 일시적 오류가 발생했습니다.\n💡 잠시 후 다시 시도해주세요.",
            )

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
                    logger.debug("_action_sell_plans get_price failed for %s", h.get("ticker"), exc_info=True)

            market_state = self.market_pulse.get_current_state()
            plans = self.sell_planner.create_plans_for_all(holdings, market_state)
            msg = format_sell_plans(plans)

            # 텔레그램 메시지 길이 제한 → 페이지네이션
            if len(msg) > 3800:
                await send_long_message(query.message, msg)
            else:
                await query.edit_message_text(msg)
        except Exception as e:
            logger.error("Sell plans error: %s", e, exc_info=True)
            await safe_edit_or_reply(
                query,
                "⚠️ 매도 계획 생성 중 오류가 발생했습니다.\n💡 잠시 후 '📊 분석' 메뉴에서 다시 시도해주세요.",
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
            await safe_edit_or_reply(
                query,
                "⚠️ 시나리오 분석 중 오류가 발생했습니다.\n💡 '📊 분석' 메뉴에서 다시 시도해주세요.",
            )

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
                    reply_markup=get_reply_markup(context),
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
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("ML command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f ML 상태 조회 오류.",
                reply_markup=get_reply_markup(context),
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
                    reply_markup=get_reply_markup(context),
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
                    _vol = ohlcv["volume"].astype(float)
                    stock_data.update({
                        "price": float(close.iloc[-1]),
                        "ma5": tech.ma5, "ma20": tech.ma20,
                        "ma60": tech.ma60, "ma120": tech.ma120,
                        "rsi": tech.rsi,
                        "macd": tech.macd,
                        "macd_signal": tech.macd_signal,
                        "macd_signal_cross": tech.macd_signal_cross,
                        "bb_pctb": tech.bb_pctb,
                        "bb_squeeze": tech.bb_squeeze,
                        "ema_50": tech.ema_50,
                        "ema_200": tech.ema_200,
                        "golden_cross": tech.golden_cross,
                        "dead_cross": tech.dead_cross,
                        "weekly_trend": tech.weekly_trend,
                        "volume": float(_vol.iloc[-1]),
                        "avg_volume_20": float(_vol.tail(20).mean()),
                        "volume_ratio": tech.volume_ratio,
                        "high_52w": float(close.tail(252).max()) if len(close) >= 252 else float(close.max()),
                        "low_52w": float(close.tail(252).min()) if len(close) >= 252 else float(close.min()),
                        "prices_5d": [float(x) for x in close.tail(5).tolist()],
                    })
            except Exception:
                logger.debug("cmd_multi_agent OHLCV/tech fetch failed for %s", ticker, exc_info=True)

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
                logger.debug("cmd_multi_agent edit_text failed, falling back", exc_info=True)
                await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Multi-agent command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 멀티 에이전트 분석 오류.", reply_markup=get_reply_markup(context),
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
                    logger.debug("cmd_surge stock data build failed", exc_info=True)
                    continue

            if not stocks_data:
                try:
                    await placeholder.edit_text(
                        "\U0001f525 현재 급등 조건을 충족하는 종목이 없습니다."
                    )
                except Exception:
                    logger.debug("cmd_surge edit_text failed for empty result", exc_info=True)
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
                logger.debug("cmd_surge edit_text failed, falling back", exc_info=True)
                await update.message.reply_text("\n".join(lines), reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Surge command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 급등주 스캔 중 오류가 발생했습니다.", reply_markup=get_reply_markup(context),
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
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Feedback command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 피드백 조회 오류.", reply_markup=get_reply_markup(context),
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
                    reply_markup=get_reply_markup(context),
                )
                return
            lines = ["\U0001f4ca 전체 추천 성적표\n"]
            for s in stats:
                lines.append(
                    f"  {s.get('strategy', '')}: 승률 {s.get('win_rate', 0):.0f}% "
                    f"({s.get('win_count', 0)}/{s.get('total_count', 0)}), "
                    f"평균 {s.get('avg_return', 0):+.1f}%"
                )
            await update.message.reply_text("\n".join(lines), reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Stats command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 성적표 조회 오류.", reply_markup=get_reply_markup(context),
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
                    logger.debug("cmd_accumulation stock data build failed", exc_info=True)
                    continue

            if not stocks_data:
                try:
                    await placeholder.edit_text(
                        "\U0001f575\ufe0f 분석 가능한 종목 데이터가 없습니다."
                    )
                except Exception:
                    logger.debug("cmd_accumulation edit_text failed for empty result", exc_info=True)
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
                    logger.debug("cmd_accumulation edit_text failed for no detection", exc_info=True)
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
                logger.debug("cmd_accumulation edit_text failed, falling back", exc_info=True)
                await update.message.reply_text("\n".join(lines), reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Accumulation command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 매집 탐지 중 오류가 발생했습니다.", reply_markup=get_reply_markup(context),
            )


