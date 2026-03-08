"""Commands and analysis functions."""
from __future__ import annotations

import re

from kstock.bot.bot_imports import *  # noqa: F403


_OHLCV_CACHE_TTL = 600  # v9.3.3: 스캔 OHLCV 캐시 10분


class CommandsMixin:
    async def _scan_all_stocks(self) -> list:
        import time as _t
        # v9.3.3: 캐시가 10분 이상 경과했으면 초기화
        if _t.monotonic() - getattr(self, '_ohlcv_cache_time', 0) > _OHLCV_CACHE_TTL:
            self._ohlcv_cache.clear()
            self._ohlcv_cache_time = _t.monotonic()

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
                if ohlcv is not None and not ohlcv.empty and "close" in ohlcv.columns:
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
                    return_exceptions=True,
                )
                # v9.6.3: 개별 실패 처리
                if isinstance(ohlcv, Exception):
                    logger.debug("OHLCV fetch failed: %s", ohlcv)
                    ohlcv = None
                if isinstance(yf_info, Exception):
                    logger.debug("yf_info fetch failed: %s", yf_info)
                    yf_info = {}
                # v9.3.2: yfinance OHLCV 실패 시 Naver OHLCV 직접 시도
                if (ohlcv is None or ohlcv.empty):
                    try:
                        from kstock.ingest.naver_finance import NaverFinanceClient
                        naver = NaverFinanceClient()
                        ohlcv = await naver.get_ohlcv(ticker, period_days=120)
                        if not ohlcv.empty:
                            logger.info("Naver OHLCV 직접 폴백 성공: %s (%d행)", ticker, len(ohlcv))
                    except Exception:
                        logger.debug("Naver OHLCV 직접 폴백 실패: %s", ticker, exc_info=True)
                # OHLCV 완전 실패 시 스킵 (가짜 데이터로 분석하지 않음)
                if ohlcv is None or ohlcv.empty:
                    logger.warning("OHLCV 완전 실패 — %s(%s) 분석 스킵", name, ticker)
                    return None
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
            # v9.3.3: OHLCV의 마지막 종가를 최종 fallback으로 사용
            if live_price <= 0 and "close" in ohlcv.columns and len(ohlcv) > 0:
                live_price = float(ohlcv["close"].iloc[-1])

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

            # Flow data: KIS API + Naver 수급 보강 (v9.3)
            foreign_flow, inst_flow = await asyncio.gather(
                self.kis.get_foreign_flow(ticker),
                self.kis.get_institution_flow(ticker),
                return_exceptions=True,
            )
            # v9.6.3: 수급 데이터 실패 시 빈 DataFrame
            if isinstance(foreign_flow, Exception):
                logger.debug("foreign_flow failed: %s", foreign_flow)
                foreign_flow = pd.DataFrame()
            if isinstance(inst_flow, Exception):
                logger.debug("inst_flow failed: %s", inst_flow)
                inst_flow = pd.DataFrame()
            foreign_days = 0
            inst_days = 0
            if not foreign_flow.empty and "net_buy_volume" in foreign_flow.columns:
                foreign_days = int(
                    (foreign_flow["net_buy_volume"] > 0).sum()
                    - (foreign_flow["net_buy_volume"] < 0).sum()
                )
            if not inst_flow.empty and "net_buy_volume" in inst_flow.columns:
                inst_days = int(
                    (inst_flow["net_buy_volume"] > 0).sum()
                    - (inst_flow["net_buy_volume"] < 0).sum()
                )

            # v9.3: Naver 투자자 매매동향으로 보강
            supply_demand_bonus = 0
            try:
                from kstock.ingest.naver_finance import (
                    get_investor_trading, analyze_investor_trend,
                )
                inv_data = await get_investor_trading(ticker, days=20)
                if inv_data:
                    inv_trend = analyze_investor_trend(inv_data)
                    sd_score = inv_trend.get("score", 0)
                    # v9.6.1: 수급 보너스 감점 완화 (-2~+8)
                    # 수급 데이터 부족 시 과도한 감점이 전체 점수를 끌어내림 방지
                    if sd_score >= 4:
                        supply_demand_bonus = 8
                    elif sd_score >= 2:
                        supply_demand_bonus = 5
                    elif sd_score >= 0:
                        supply_demand_bonus = 0
                    elif sd_score >= -2:
                        supply_demand_bonus = -1   # v9.6.1: -3 → -1
                    else:
                        supply_demand_bonus = -2   # v9.6.1: -5 → -2

                    # Naver 연속매수일로 KIS 데이터 보정
                    nv_f_days = inv_trend.get("consecutive_foreign_buy", 0)
                    nv_i_days = inv_trend.get("consecutive_inst_buy", 0)
                    if nv_f_days > abs(foreign_days):
                        foreign_days = max(foreign_days, nv_f_days)
                    if nv_i_days > abs(inst_days):
                        inst_days = max(inst_days, nv_i_days)

                    # DB 저장
                    try:
                        self.db.bulk_save_supply_demand(ticker, inv_data)
                    except Exception:
                        logger.debug("bulk_save_supply_demand failed for %s", ticker, exc_info=True)
            except Exception:
                logger.debug("Naver investor data failed for %s", ticker, exc_info=True)

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
                logger.debug("multi_agent_bonus failed for %s", ticker, exc_info=True)

            # v9.5: YouTube 방송 언급 보너스
            try:
                from kstock.signal.agent_bridge import get_youtube_mention_bonus
                yt_bonus = get_youtube_mention_bonus(self.db, ticker)
                if yt_bonus:
                    multi_agent_bonus += yt_bonus
            except Exception:
                logger.debug("youtube_mention_bonus failed for %s", ticker, exc_info=True)

            # v9.5.3: 글로벌 이벤트 기반 점수 조정
            event_bonus = 0
            try:
                from kstock.bot.learning_engine import get_event_bonus_for_ticker
                event_bonus = get_event_bonus_for_ticker(
                    self.db, ticker, sector=sector,
                )
            except Exception:
                logger.debug("event_bonus failed for %s", ticker, exc_info=True)

            score = compute_composite_score(
                macro, flow, info, tech, self.scoring_config,
                mtf_bonus=mtf_bonus, sector_adj=sector_adj,
                policy_bonus=policy_bonus,
                ml_bonus=ml_bonus_val,
                sentiment_bonus=sentiment_bonus,
                leading_sector_bonus=leading_sector_bonus,
                multi_agent_bonus=multi_agent_bonus,
                factor_bonus=supply_demand_bonus,  # v9.3: 수급 보너스
                event_bonus=event_bonus,  # v9.5.3: 이벤트 보너스
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

            # v9.4: 패턴 매칭 + 가격 목표 계산
            pt_result = None
            pr_result = None
            try:
                from kstock.signal.price_target import PriceTargetEngine
                pt_engine = PriceTargetEngine()
                pt_result = pt_engine.calculate(ohlcv, info)
            except Exception:
                logger.debug("PriceTarget calc failed for %s", ticker, exc_info=True)
            try:
                from kstock.signal.pattern_matcher import PatternMatcher
                pm = PatternMatcher()
                pr_result = pm.find_similar_patterns(ohlcv)
            except Exception:
                logger.debug("PatternMatcher calc failed for %s", ticker, exc_info=True)

            return ScanResult(
                ticker=ticker, name=name, score=score,
                tech=tech, info=info, flow=flow,
                strategy_type=best_strategy,
                strategy_signals=strat_signals,
                confidence_score=conf_score,
                confidence_stars=conf_stars,
                confidence_label=conf_label,
                price_target=pt_result,
                pattern_report=pr_result,
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
        # 4순위: base_price fallback (stale 가능성 경고)
        if base_price > 0:
            logger.warning(
                "Price %s: ALL sources failed, using stale fallback=%s",
                ticker, base_price,
            )
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
                    price_target=result.price_target,
                    pattern_report=result.pattern_report,
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
                msg = format_watch_alert(
                    name, ticker, score, tech, result.info,
                    strategy_type=strat_type, price_target=result.price_target,
                )
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

                # v9.3: holding_type 반영 임계값 (장기보유 종목 보호)
                ht = h.get("holding_type") or h.get("horizon") or "auto"
                from kstock.store._portfolio import HOLDING_THRESHOLDS
                th = HOLDING_THRESHOLDS.get(ht, HOLDING_THRESHOLDS["auto"])
                target_1 = h.get("target_1") or round(buy_price * (1 + th["t1"]), 0)
                stop_price = h.get("stop_price") or round(buy_price * (1 + th["stop"]), 0)

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
            price_data = self.db.get_supply_demand(ticker, days=20)
            short_signal = analyze_short_selling(short_data, ticker, name)
            lines.append(format_short_alert(short_signal, short_data, price_data))
            lines.append("")

            # Short pattern detection
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
            lines.append("또는 종목명을 바로 입력하세요")

            # 종목명 입력 대기 상태 설정
            context.user_data["awaiting_short"] = True

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
        """스윙 트레이딩 기회 조회 — 관심종목 기술적 스캔."""
        import asyncio as _aio

        placeholder = await update.message.reply_text(
            "\u26a1 스윙 기회 스캔 중... (약 15초)"
        )
        try:
            # 관심종목 중 swing/scalp 카테고리
            watchlist = self.db.get_watchlist()
            holdings = self.db.get_active_holdings()
            held = {h["ticker"] for h in holdings}
            candidates = [
                w for w in watchlist
                if w["ticker"] not in held
                and w.get("horizon") in ("swing", "scalp", "")
            ][:30]

            if not candidates:
                candidates = [w for w in watchlist if w["ticker"] not in held][:20]

            from kstock.features.technical import compute_indicators

            async def _scan(w):
                try:
                    ticker = w["ticker"]
                    market = "KOSPI"
                    for s in self.all_tickers:
                        if s["code"] == ticker:
                            market = s.get("market", "KOSPI")
                            break
                    ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="3mo")
                    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
                        return None
                    tech = compute_indicators(ohlcv)
                    close = float(ohlcv["close"].iloc[-1])
                    prev = float(ohlcv["close"].iloc[-2])
                    dc = ((close - prev) / prev * 100) if prev > 0 else 0
                    # 스윙 매수 조건: RSI 과매도 + BB 하단 + MACD 골든크로스 가점
                    score = 0
                    if tech.rsi <= 35:
                        score += 30
                    elif tech.rsi <= 45:
                        score += 15
                    if tech.bb_pctb <= 0.2:
                        score += 25
                    elif tech.bb_pctb <= 0.35:
                        score += 10
                    if tech.macd_signal_cross > 0:
                        score += 20
                    if tech.volume_ratio >= 1.5:
                        score += 15
                    elif tech.volume_ratio >= 1.2:
                        score += 5
                    if score >= 25:
                        return {
                            "ticker": ticker, "name": (w.get("name") or ticker)[:8],
                            "price": close, "dc": dc, "score": score,
                            "rsi": tech.rsi, "bb": tech.bb_pctb,
                            "macd_x": tech.macd_signal_cross, "vr": tech.volume_ratio,
                        }
                except Exception:
                    pass
                return None

            results = []
            for i in range(0, len(candidates), 15):
                batch = candidates[i:i + 15]
                batch_r = await _aio.gather(*[_scan(w) for w in batch], return_exceptions=True)
                for r in batch_r:
                    if isinstance(r, dict):
                        results.append(r)

            results.sort(key=lambda x: x["score"], reverse=True)
            top = results[:8]

            if not top:
                try:
                    await placeholder.edit_text(
                        "\u26a1 현재 스윙 매수 조건(RSI 과매도 + BB 하단)을 "
                        "충족하는 관심종목이 없습니다.\n\n"
                        "즐겨찾기에 종목을 추가하면 더 많은 기회를 스캔합니다."
                    )
                except Exception:
                    pass
                return

            lines = [f"\u26a1 스윙 기회 ({len(results)}종목 감지)\n"]
            for i, r in enumerate(top, 1):
                sig = "🟢" if r["score"] >= 50 else "🟡"
                mc = "↑" if r["macd_x"] > 0 else ("↓" if r["macd_x"] < 0 else "-")
                ds = "+" if r["dc"] > 0 else ""
                lines.append(
                    f"{i}. {sig} {r['name']} ({r['score']}점)\n"
                    f"   {r['price']:,.0f}원({ds}{r['dc']:.1f}%) "
                    f"RSI:{r['rsi']:.0f} BB:{r['bb']:.2f} MACD:{mc}"
                )

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            buttons = []
            btn_row = []
            for r in top[:6]:
                cb = f"fav:diag:{r['ticker']}"
                if len(cb) <= 64:
                    btn_row.append(InlineKeyboardButton(f"🔍 {r['name']}", callback_data=cb))
                if len(btn_row) == 3:
                    buttons.append(btn_row)
                    btn_row = []
            if btn_row:
                buttons.append(btn_row)
            buttons.append([
                InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0"),
            ])

            try:
                await placeholder.edit_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception:
                await update.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
        except Exception as e:
            logger.error("Swing scan error: %s", e, exc_info=True)
            try:
                await placeholder.edit_text("\u26a0\ufe0f 스윙 스캔 오류가 발생했습니다.")
            except Exception:
                pass

    # -- v3.5 handlers ---------------------------------------------------------

    async def _menu_ai_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """AI 비서 모드 - 투자 + 범용 질문 버튼 + 직접 입력 안내."""
        from kstock.bot.bot_imports import make_feedback_row
        buttons = [
            [InlineKeyboardButton("📊 오늘 시장 분석", callback_data="quick_q:market")],
            [InlineKeyboardButton("💼 내 포트폴리오 조언", callback_data="quick_q:portfolio")],
            [InlineKeyboardButton("🎯 4매니저 동시추천", callback_data="quick_q:mgr4")],
            [InlineKeyboardButton("⚠️ 리스크 점검", callback_data="quick_q:risk")],
            make_feedback_row("AI비서"),
        ]
        msg = (
            "🤖 AI 비서 대기 중\n\n"
            "투자 분석부터 일상 질문까지\n"
            "무엇이든 물어보세요.\n\n"
            "💬 채팅창에 직접 입력하세요.\n\n"
            "예시:\n"
            "  '에코프로 어떻게 보여?'\n"
            "  '오늘 뉴스 요약해줘'\n"
            "  '영어 이메일 써줘'\n"
            "  '이번주 할 일 정리해줘'"
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

            # v9.3.2: 종목 감지 시 모든 데이터 소스 병렬 수집 (부분 실패 허용)
            enriched = question
            stock = None
            try:
                stock = self._detect_stock_query(question)
                if stock:
                    code = stock.get("code", "")
                    name = stock.get("name", code)
                    market = stock.get("market", "KOSPI")

                    # ── 1단계: 데이터 병렬 수집 (각각 독립, 실패 허용) ──
                    import asyncio
                    from kstock.ingest.naver_finance import (
                        NaverFinanceClient,
                        get_investor_trading, analyze_investor_trend,
                        get_sector_rankings, analyze_sector_momentum,
                    )
                    naver = NaverFinanceClient()

                    # 병렬 실행: yfinance OHLCV, Naver OHLCV, Naver 재무, 수급, 섹터
                    yf_ohlcv_task = self.yf_client.get_ohlcv(code, market)
                    nv_ohlcv_task = naver.get_ohlcv(code, period_days=120)
                    nv_info_task = naver.get_stock_info(code, name)
                    inv_task = get_investor_trading(code, days=20)
                    sector_task = get_sector_rankings(limit=15)

                    results = await asyncio.gather(
                        yf_ohlcv_task, nv_ohlcv_task, nv_info_task,
                        inv_task, sector_task,
                        return_exceptions=True,
                    )

                    yf_ohlcv = results[0] if not isinstance(results[0], Exception) else pd.DataFrame()
                    nv_ohlcv = results[1] if not isinstance(results[1], Exception) else pd.DataFrame()
                    nv_info = results[2] if not isinstance(results[2], Exception) else {}
                    inv_data = results[3] if not isinstance(results[3], Exception) else []
                    sectors = results[4] if not isinstance(results[4], Exception) else []

                    # ── 2단계: 최선의 OHLCV 선택 ──
                    ohlcv = pd.DataFrame()
                    ohlcv_source = "없음"
                    if isinstance(yf_ohlcv, pd.DataFrame) and not yf_ohlcv.empty:
                        ohlcv = yf_ohlcv
                        ohlcv_source = "yfinance"
                    elif isinstance(nv_ohlcv, pd.DataFrame) and not nv_ohlcv.empty:
                        ohlcv = nv_ohlcv
                        ohlcv_source = "네이버"

                    # ── 3단계: 현재가 (다중 소스) ──
                    cur = 0.0
                    price_source = ""
                    # OHLCV 종가
                    if not ohlcv.empty:
                        cur = float(ohlcv["close"].astype(float).iloc[-1])
                        price_source = ohlcv_source
                    # Naver 재무에서 현재가
                    if cur <= 0 and isinstance(nv_info, dict) and nv_info.get("current_price", 0) > 0:
                        cur = float(nv_info["current_price"])
                        price_source = "네이버시세"
                    # 수급 데이터에서 종가
                    if cur <= 0 and inv_data and inv_data[0].get("close", 0) > 0:
                        cur = float(inv_data[0]["close"])
                        price_source = "수급데이터"
                    # KIS/Naver 실시간가
                    try:
                        live = await self._get_price(code, base_price=cur)
                        if live > 0:
                            cur = live
                            price_source = "실시간"
                    except Exception:
                        pass

                    # ── 4단계: 기술적 분석 (OHLCV 있을 때만) ──
                    tech_text = ""
                    if not ohlcv.empty and len(ohlcv) >= 20:
                        from kstock.core.technical import compute_indicators
                        tech = compute_indicators(ohlcv)
                        tech_text = (
                            f"\n이동평균: 5일 {tech.ma5:,.0f}원, "
                            f"20일 {tech.ma20:,.0f}원, "
                            f"60일 {tech.ma60:,.0f}원\n"
                            f"RSI: {tech.rsi:.1f} | MACD: {tech.macd:.0f}\n"
                            f"볼린저: 상단 {tech.bb_upper:,.0f} / 하단 {tech.bb_lower:,.0f}"
                        )
                        # 추가 분석 데이터
                        close = ohlcv["close"].astype(float)
                        vol = ohlcv["volume"].astype(float)
                        if len(close) >= 5:
                            ret_1w = (float(close.iloc[-1]) / float(close.iloc[-5]) - 1) * 100
                            vol_avg_5 = float(vol.iloc[-5:].mean())
                            vol_avg_20 = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else vol_avg_5
                            vol_ratio = vol_avg_5 / vol_avg_20 if vol_avg_20 > 0 else 1.0
                            trade_val = float(close.iloc[-5:].mean() * vol.iloc[-5:].mean())
                            tech_text += (
                                f"\n1주 수익률: {ret_1w:+.1f}%"
                                f" | 거래량비: {vol_ratio:.1f}x"
                                f" | 5일 거래대금: {trade_val/1e8:,.0f}억원"
                            )
                        if len(close) >= 20:
                            ret_1m = (float(close.iloc[-1]) / float(close.iloc[-20]) - 1) * 100
                            tech_text += f" | 1개월: {ret_1m:+.1f}%"
                        if len(close) >= 60:
                            ret_3m = (float(close.iloc[-1]) / float(close.iloc[-60]) - 1) * 100
                            tech_text += f" | 3개월: {ret_3m:+.1f}%"

                    # ── 5단계: 재무 데이터 ──
                    fin_text = ""
                    if isinstance(nv_info, dict):
                        parts = []
                        if nv_info.get("per", 0) > 0:
                            parts.append(f"PER {nv_info['per']:.1f}")
                        if nv_info.get("pbr", 0) > 0:
                            parts.append(f"PBR {nv_info['pbr']:.2f}")
                        if nv_info.get("roe", 0) > 0:
                            parts.append(f"ROE {nv_info['roe']:.1f}%")
                        if nv_info.get("market_cap", 0) > 0:
                            parts.append(f"시총 {nv_info['market_cap']/1e8:,.0f}억원")
                        if nv_info.get("foreign_ratio", 0) > 0:
                            parts.append(f"외국인비율 {nv_info['foreign_ratio']:.1f}%")
                        if nv_info.get("52w_high", 0) > 0:
                            parts.append(f"52주고가 {nv_info['52w_high']:,.0f}원")
                        if nv_info.get("52w_low", 0) > 0:
                            parts.append(f"52주저가 {nv_info['52w_low']:,.0f}원")
                        if parts:
                            fin_text = f"\n\n[재무/기본정보]\n" + " | ".join(parts)

                    # ── 6단계: 수급 분석 ──
                    supply_text = ""
                    if inv_data:
                        inv_analysis = analyze_investor_trend(inv_data)
                        supply_text = (
                            f"\n\n[수급 분석 (최근 20일)]\n"
                            f"{inv_analysis.get('summary', '')}\n"
                            f"수급 시그널: {inv_analysis.get('signal', '중립')}"
                        )
                        # DB 저장
                        try:
                            self.db.bulk_save_supply_demand(code, inv_data)
                        except Exception:
                            pass

                    # ── 7단계: 섹터 분석 ──
                    sector_text = ""
                    if sectors:
                        sec_analysis = analyze_sector_momentum(sectors)
                        sector_text = (
                            f"\n\n[섹터 동향]\n"
                            f"{sec_analysis.get('summary', '')}"
                        )

                    # ── 8단계: 데이터 품질 표시 + enriched 조립 ──
                    data_parts = []
                    if cur > 0:
                        data_parts.append(f"현재가: {cur:,.0f}원 (소스: {price_source})")
                    if tech_text:
                        data_parts.append(f"[기술적 분석 — {ohlcv_source} {len(ohlcv)}일]{tech_text}")
                    elif ohlcv_source == "없음":
                        data_parts.append("⚠️ 차트(OHLCV) 데이터 조회 불가 — 기술적 지표 없이 가용 데이터로 분석")

                    if data_parts or fin_text or supply_text or sector_text:
                        enriched = (
                            f"{question}\n\n"
                            f"[{name}({code}) 데이터]\n"
                            + "\n".join(data_parts)
                            + fin_text
                            + supply_text
                            + sector_text
                            + "\n\n[절대 규칙] 위 데이터의 가격만 참고하라. "
                            "너의 학습 데이터에 있는 과거 주가를 절대 사용 금지. "
                            "데이터가 부족한 항목은 '데이터 없음'이라고 명시하고, "
                            "있는 데이터만으로 최선의 분석을 제공하라."
                        )
                        logger.info("AI질문 데이터 주입: %s 현재가=%s원 OHLCV=%s 수급=%s건 재무=%s",
                                    name, f"{cur:,.0f}" if cur > 0 else "없음",
                                    ohlcv_source, len(inv_data),
                                    "있음" if fin_text else "없음")
                    else:
                        logger.warning("AI질문: %s 데이터 완전 실패 — 모든 소스 응답 없음", name)
            except Exception as e:
                logger.warning("AI질문 데이터 수집 실패: %s", e, exc_info=True)

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
                text="⚠️ AI 응답 중 오류가 발생했습니다.\n💡 잠시 후 같은 질문을 다시 시도하시거나, '💬 AI비서' 메뉴를 이용해주세요.",
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
                text="⚠️ AI 응답 중 오류가 발생했습니다.\n💡 잠시 후 같은 질문을 다시 시도하시거나, '💬 AI비서' 메뉴를 이용해주세요.",
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
                        f_net = int(frgn["net_buy_volume"].sum()) if not frgn.empty and "net_buy_volume" in frgn.columns else 0
                        i_net = int(inst["net_buy_volume"].sum()) if not inst.empty and "net_buy_volume" in inst.columns else 0
                        if f_net != 0 or i_net != 0:
                            flow_lines.append(
                                f"- {hname}: 외인 {f_net:+,}주 / 기관 {i_net:+,}주 (5일 누적)"
                            )
                    except Exception:
                        logger.debug("flow fetch failed for %s", ticker)
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
            horizon_results = await asyncio.gather(
                *horizon_tasks.values(), return_exceptions=True,
            )

            picks_by_horizon = {}
            for hz, result in zip(horizon_tasks.keys(), horizon_results):
                if isinstance(result, Exception):
                    logger.warning("horizon %s failed: %s", hz, result)
                    picks, _err = [], str(result)
                else:
                    picks, _err = result
                picks_by_horizon[hz] = picks

            # 2. 매크로 컨텍스트 수집 (풍부한 버전)
            market_context = ""
            snap = None
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

            # 3. 4매니저 동시 AI 분석 (asyncio.gather) — 공유 컨텍스트 + 레짐 가중치
            from kstock.bot.investment_managers import (
                get_all_managers_picks, MANAGERS, MANAGER_HORIZON_MAP,
            )
            current_alert = getattr(self, '_alert_mode', 'normal')
            _vix = getattr(snap, 'vix', 20.0) if snap else 20.0
            manager_analyses = await get_all_managers_picks(
                picks_by_horizon, market_context, shared_context,
                alert_mode=current_alert,
                vix=_vix,
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

            # #2 크로스매니저 컨센서스 감지
            try:
                from kstock.bot.investment_managers import detect_consensus, format_consensus
                consensus = detect_consensus(picks_by_horizon)
                if consensus:
                    consensus_text = format_consensus(consensus)
                    if consensus_text:
                        lines.append(f"\n{consensus_text}")
            except Exception:
                pass

            # v9.5: 통합 합의 섹션 (AI토론 + YouTube + 매니저 크로스 참조)
            try:
                from kstock.bot.unified_state import build_unified_state
                unified = await build_unified_state(self.db, macro_client=self.macro_client)
                if unified.consensus_tickers:
                    lines.append(f"\n🤝 시스템 종합 합의")
                    lines.append(f"{'━' * 20}")
                    for ct in unified.consensus_tickers[:5]:
                        lines.append(
                            f"- {ct['name']}: {ct['verdict']} "
                            f"[{ct['sources']}]"
                        )
                if unified.youtube_insights:
                    yt_outlook = []
                    for yi in unified.youtube_insights[:2]:
                        src = yi.get("source", "").replace("🎬", "").strip()
                        outlook = yi.get("market_outlook", "")
                        if outlook:
                            yt_outlook.append(f"🎬 {src}: {outlook[:50]}")
                    if yt_outlook:
                        lines.append(f"\n📺 방송 전망")
                        lines.extend(yt_outlook)
            except Exception:
                logger.debug("Unified consensus in 4manager failed", exc_info=True)

            result_text = "\n".join(lines)

            # 5. 액션 버튼 구성
            from kstock.bot.bot_imports import make_feedback_row
            buttons = []

            # 각 매니저 1순위 종목 "담기" + "토론" 버튼
            for mgr_key, pick in top_picks_for_buttons.items():
                mgr = MANAGERS.get(mgr_key, {})
                emoji = mgr.get("emoji", "📌")
                hz = MANAGER_HORIZON_MAP.get(mgr_key, "scalp")
                ticker = pick.get("ticker", "")
                name = pick.get("name", "")[:5]
                cb = f"bp:add:{ticker}:{hz}"
                if len(cb) <= 64 and ticker:
                    buttons.append([
                        InlineKeyboardButton(
                            f"{emoji}{name} 담기",
                            callback_data=cb,
                        ),
                        InlineKeyboardButton(
                            f"🎙️ 토론",
                            callback_data=f"debate:{ticker}",
                        ),
                    ])

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
            if cache_age < _OHLCV_CACHE_TTL and hasattr(self, '_last_scan_results') and self._last_scan_results:
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
            # v6.0: 매수 시작 + AI토론 버튼
            followup_buttons.append([
                InlineKeyboardButton(
                    "🛒 이 종목들로 매수 시작",
                    callback_data="bp:start",
                ),
                InlineKeyboardButton(
                    "🎙️ AI토론",
                    callback_data="menu:debate",
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
        """공매도 분석 메뉴 — 보유종목 + 관심종목 버튼 표시."""
        holdings = self.db.get_active_holdings()
        buttons: list[list[InlineKeyboardButton]] = []
        seen_tickers: set[str] = set()

        # 1) 보유종목
        for h in holdings[:6]:
            ticker = h.get("ticker", "")
            name = h.get("name", ticker)
            if ticker and ticker not in seen_tickers:
                buttons.append([InlineKeyboardButton(
                    f"📊 {name} 공매도", callback_data=f"short:{ticker}",
                )])
                seen_tickers.add(ticker)

        if len(holdings) > 1:
            buttons.append([InlineKeyboardButton(
                "📊 전체 보유종목 요약", callback_data="short:all",
            )])

        # 2) 관심종목도 추가 (보유종목에 없는 것만)
        watchlist = self.db.get_watchlist()
        wl_row: list[InlineKeyboardButton] = []
        for w in watchlist:
            ticker = w.get("ticker", "")
            name = w.get("name", ticker)
            if ticker and ticker not in seen_tickers:
                wl_row.append(InlineKeyboardButton(
                    f"📊 {name[:6]}", callback_data=f"short:{ticker}",
                ))
                seen_tickers.add(ticker)
                if len(wl_row) == 3:
                    buttons.append(wl_row)
                    wl_row = []
                if len(seen_tickers) >= 15:
                    break
        if wl_row:
            buttons.append(wl_row)

        buttons.append([InlineKeyboardButton(
            "🔙 더보기", callback_data="menu:more",
        )])

        header = "📊 공매도/레버리지 분석\n\n"
        if holdings:
            header += "보유종목 + 관심종목 공매도 현황을 분석합니다:"
        else:
            header += "관심종목의 공매도 현황을 분석합니다:\n(보유종목이 없어 관심종목을 표시합니다)"

        await update.message.reply_text(
            header,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

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
                                date=d["date"],
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
            price_data = self.db.get_supply_demand(ticker, days=20)
            lines: list[str] = []

            signal = analyze_short_selling(short_data, ticker, name)
            lines.append(format_short_alert(signal, short_data, price_data))
            lines.append("")
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

            # v9.4: 패턴/가격목표 텍스트 주입
            try:
                ohlcv = self._ohlcv_cache.get(ticker)
                if ohlcv is not None and not ohlcv.empty:
                    from kstock.signal.pattern_matcher import PatternMatcher
                    pm = PatternMatcher()
                    pr = pm.find_similar_patterns(ohlcv)
                    if pr and pr.match_count > 0:
                        report.pattern_text = (
                            f"유사패턴 {pr.match_count}건: "
                            f"20일후 평균 {pr.avg_return_20d:+.1f}% "
                            f"(상승 {pr.positive_pct:.0f}%)"
                        )
                    from kstock.signal.price_target import PriceTargetEngine
                    from kstock.ingest.kis_client import StockInfo
                    pt_info = StockInfo(
                        ticker=ticker, name=name, market="KOSPI",
                        current_price=price,
                        per=stock_data.get("per", 0),
                    )
                    pt_engine = PriceTargetEngine()
                    pt = pt_engine.calculate(ohlcv, pt_info)
                    if pt:
                        parts = []
                        if pt.resistance_1 > 0:
                            parts.append(f"저항 {pt.resistance_1:,.0f}원")
                        if pt.support_1 > 0:
                            parts.append(f"지지 {pt.support_1:,.0f}원")
                        if pt.risk_reward_ratio > 0:
                            parts.append(f"RR {pt.risk_reward_ratio:.1f}배")
                        if parts:
                            report.price_target_text = " | ".join(parts)
            except Exception:
                logger.debug("멀티분석 패턴/가격목표 주입 실패: %s", ticker, exc_info=True)

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

            # v9.4: 패턴/가격목표 텍스트 주입
            try:
                ohlcv = self._ohlcv_cache.get(ticker)
                if ohlcv is not None and not ohlcv.empty:
                    from kstock.signal.pattern_matcher import PatternMatcher
                    pm = PatternMatcher()
                    pr = pm.find_similar_patterns(ohlcv)
                    if pr and pr.match_count > 0:
                        report.pattern_text = (
                            f"유사패턴 {pr.match_count}건: "
                            f"20일후 평균 {pr.avg_return_20d:+.1f}% "
                            f"(상승 {pr.positive_pct:.0f}%)"
                        )
                    from kstock.signal.price_target import PriceTargetEngine
                    from kstock.ingest.kis_client import StockInfo
                    pt_info = StockInfo(
                        ticker=ticker, name=name, market="KOSPI",
                        current_price=price,
                        per=stock_data.get("per", 0),
                    )
                    pt_engine = PriceTargetEngine()
                    pt = pt_engine.calculate(ohlcv, pt_info)
                    if pt:
                        parts = []
                        if pt.resistance_1 > 0:
                            parts.append(f"저항 {pt.resistance_1:,.0f}원")
                        if pt.support_1 > 0:
                            parts.append(f"지지 {pt.support_1:,.0f}원")
                        if pt.risk_reward_ratio > 0:
                            parts.append(f"RR {pt.risk_reward_ratio:.1f}배")
                        if parts:
                            report.price_target_text = " | ".join(parts)
            except Exception:
                logger.debug("멀티분석 패턴/가격목표 주입 실패: %s", ticker, exc_info=True)

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
        """Handle /surge - scan for surge stocks (watchlist + universe top 30)."""
        import asyncio as _aio
        try:
            self._persist_chat_id(update)
            placeholder = await update.message.reply_text(
                "\U0001f525 급등주 스캔 중... (약 15초)"
            )

            # 관심종목 + 유니버스 상위 30개 조합 (중복 제거)
            watchlist = self.db.get_watchlist()
            wl_codes = {w["ticker"] for w in watchlist}
            scan_items = []
            seen = set()
            # 1) 관심종목 우선
            for w in watchlist:
                code = w["ticker"]
                if code not in seen:
                    name = w.get("name") or code
                    market = "KOSPI"
                    for s in self.all_tickers:
                        if s["code"] == code:
                            name = s.get("name", name)
                            market = s.get("market", "KOSPI")
                            break
                    scan_items.append({"code": code, "name": name, "market": market})
                    seen.add(code)
            # 2) 유니버스 보충 (최대 60개)
            for item in self.all_tickers:
                if len(scan_items) >= 60:
                    break
                if item["code"] not in seen:
                    scan_items.append({"code": item["code"], "name": item["name"],
                                       "market": item.get("market", "KOSPI")})
                    seen.add(item["code"])

            async def _check_one(item):
                try:
                    ohlcv = await self.yf_client.get_ohlcv(item["code"], item["market"], period="1mo")
                    if ohlcv is None or ohlcv.empty or len(ohlcv) < 2:
                        return None
                    close = ohlcv["close"].astype(float)
                    volume = ohlcv["volume"].astype(float)
                    cur = float(close.iloc[-1])
                    prev = float(close.iloc[-2])
                    chg = ((cur - prev) / prev * 100) if prev > 0 else 0
                    avg_v = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
                    cur_v = float(volume.iloc[-1])
                    vr = cur_v / avg_v if avg_v > 0 else 0
                    if chg >= 3.0 or vr >= 2.0:
                        return {"ticker": item["code"], "name": item["name"],
                                "price": cur, "change_pct": chg, "vol_ratio": vr,
                                "in_watchlist": item["code"] in wl_codes}
                except Exception:
                    pass
                return None

            # 병렬 스캔 (20개씩 배치)
            stocks_data = []
            for i in range(0, len(scan_items), 20):
                batch = scan_items[i:i + 20]
                results = await _aio.gather(*[_check_one(it) for it in batch], return_exceptions=True)
                for r in results:
                    if isinstance(r, dict):
                        stocks_data.append(r)

            if not stocks_data:
                try:
                    await placeholder.edit_text(
                        "\U0001f525 현재 급등 조건(+3% 또는 거래량 2배)을 충족하는 종목이 없습니다.\n\n"
                        "장중(09:00~15:30)에 다시 시도해보세요."
                    )
                except Exception:
                    pass
                return

            stocks_data.sort(key=lambda s: s["change_pct"], reverse=True)
            top = stocks_data[:10]
            lines = [f"\U0001f525 급등주 스캔 ({len(stocks_data)}종목 감지)\n"]
            for i, s in enumerate(top, 1):
                icon = "\U0001f4c8" if s["change_pct"] >= 5 else "\U0001f525" if s["change_pct"] >= 3 else "\u26a1"
                star = "⭐" if s.get("in_watchlist") else ""
                lines.append(
                    f"{i}. {icon} {s['name']} {s['change_pct']:+.1f}% "
                    f"거래량 {s['vol_ratio']:.1f}배 {star}"
                )
                self.db.add_surge_stock(
                    ticker=s["ticker"], name=s["name"],
                    scan_time=datetime.now(KST).strftime("%H:%M"),
                    change_pct=s["change_pct"], volume_ratio=s["vol_ratio"],
                    triggers="price_surge" if s["change_pct"] >= 5 else "combined",
                    market_cap=0, health_grade="HEALTHY" if s["change_pct"] < 10 else "CAUTION",
                )

            # 종목 버튼
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            buttons = []
            btn_row = []
            for s in top[:6]:
                cb = f"fav:stock:{s['ticker']}"
                if len(cb) <= 64:
                    btn_row.append(InlineKeyboardButton(f"{s['name'][:6]}", callback_data=cb))
                if len(btn_row) == 3:
                    buttons.append(btn_row)
                    btn_row = []
            if btn_row:
                buttons.append(btn_row)
            buttons.append([InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")])

            try:
                await placeholder.edit_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception:
                await update.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
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
        """Handle /accumulation - stealth accumulation scan (parallel)."""
        import asyncio as _aio
        try:
            self._persist_chat_id(update)
            placeholder = await update.message.reply_text(
                "\U0001f575\ufe0f 매집 패턴 탐지 중... (약 15초)"
            )

            # 관심종목 + 유니버스 상위 조합 (최대 40개)
            watchlist = self.db.get_watchlist()
            scan_items = []
            seen = set()
            for w in watchlist:
                code = w["ticker"]
                if code not in seen:
                    name = w.get("name") or code
                    market = "KOSPI"
                    for s in self.all_tickers:
                        if s["code"] == code:
                            name = s.get("name", name)
                            market = s.get("market", "KOSPI")
                            break
                    scan_items.append({"code": code, "name": name, "market": market})
                    seen.add(code)
            for item in self.all_tickers:
                if len(scan_items) >= 40:
                    break
                if item["code"] not in seen:
                    scan_items.append({"code": item["code"], "name": item["name"],
                                       "market": item.get("market", "KOSPI")})
                    seen.add(item["code"])

            async def _scan_one(item):
                try:
                    ohlcv = await self.yf_client.get_ohlcv(item["code"], item["market"], period="3mo")
                    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
                        return None
                    close = ohlcv["close"].astype(float)
                    volume = ohlcv["volume"].astype(float)
                    p20 = float(close.iloc[-20])
                    pnow = float(close.iloc[-1])
                    prc_chg = ((pnow - p20) / p20 * 100) if p20 > 0 else 0
                    avg_vol = float(volume.tail(20).mean())
                    daily_inst, daily_foreign = [], []
                    for j in range(-20, 0):
                        if abs(j) <= len(volume):
                            v = float(volume.iloc[j])
                            r = v / avg_vol if avg_vol > 0 else 1
                            daily_inst.append(v * 0.3 if r > 1.5 else -v * 0.1)
                            daily_foreign.append(v * 0.2 if r > 1.3 else -v * 0.1)
                    return {
                        "ticker": item["code"], "name": item["name"],
                        "daily_inst": daily_inst, "daily_foreign": daily_foreign,
                        "price_change_20d": prc_chg, "disclosure_text": "",
                    }
                except Exception:
                    return None

            # 병렬 스캔 (15개씩)
            stocks_data = []
            for i in range(0, len(scan_items), 15):
                batch = scan_items[i:i + 15]
                results = await _aio.gather(*[_scan_one(it) for it in batch], return_exceptions=True)
                for r in results:
                    if isinstance(r, dict):
                        stocks_data.append(r)

            if not stocks_data:
                try:
                    await placeholder.edit_text("\U0001f575\ufe0f 분석 가능한 종목 데이터가 없습니다.")
                except Exception:
                    pass
                return

            detections = scan_accumulations(stocks_data)
            if not detections:
                try:
                    await placeholder.edit_text(
                        f"\U0001f575\ufe0f 현재 매집 패턴이 감지되지 않았습니다.\n"
                        f"({len(stocks_data)}종목 스캔 완료)")
                except Exception:
                    pass
                return

            lines = [f"\U0001f575\ufe0f 스텔스 매집 감지 ({len(detections)}종목)\n"]
            for i, d in enumerate(detections[:10], 1):
                lines.append(
                    f"{i}. {d.name} ({d.ticker}) 스코어 {d.total_score}\n"
                    f"   기관: {d.inst_total / 1e8:.0f}억 "
                    f"외인: {d.foreign_total / 1e8:.0f}억 "
                    f"20일: {d.price_change_20d:+.1f}%")
                import json
                pj = json.dumps(
                    [{"type": p.pattern_type, "days": p.streak_days, "score": p.score}
                     for p in d.patterns], ensure_ascii=False) if d.patterns else "[]"
                self.db.add_stealth_accumulation(
                    ticker=d.ticker, name=d.name, total_score=d.total_score,
                    patterns_json=pj, price_change_20d=d.price_change_20d,
                    inst_total=d.inst_total, foreign_total=d.foreign_total)

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            buttons = []
            btn_row = []
            for d in detections[:6]:
                cb = f"fav:stock:{d.ticker}"
                if len(cb) <= 64:
                    btn_row.append(InlineKeyboardButton(f"📋 {d.name[:6]}", callback_data=cb))
                if len(btn_row) == 3:
                    buttons.append(btn_row)
                    btn_row = []
            if btn_row:
                buttons.append(btn_row)
            buttons.append([InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")])

            try:
                await placeholder.edit_text(
                    "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
            except Exception:
                await update.message.reply_text(
                    "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            logger.error("Accumulation command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 매집 탐지 중 오류가 발생했습니다.", reply_markup=get_reply_markup(context))


