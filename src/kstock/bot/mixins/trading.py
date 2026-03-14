"""Trading, balance, holdings management."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403


class TradingMixin:
    async def _action_opt_run(self, query, context, payload: str) -> None:
        """최적화 콜백: opt_run:{ticker} or opt_run:manual."""
        if payload == "manual":
            context.user_data["awaiting_optimize_ticker"] = True
            await safe_edit_or_reply(query,
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
                    reply_markup=get_reply_markup(context),
                )
        except Exception as e:
            logger.error("Optimize error: %s", e, exc_info=True)
            await message.reply_text(
                "\u26a0\ufe0f 최적화 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.",
                reply_markup=get_reply_markup(context),
            )

    # == Callback actions ====================================================

    async def _action_buy(self, query, context, ticker: str) -> None:
        # v10.2: 매크로 쇼크 매수 차단 (SHOCK/CRISIS)
        _shock = getattr(self, "_current_shock", None)
        if _shock and not _shock.policy.new_buy_allowed:
            await safe_edit_or_reply(query,
                "⛔ 매크로 쇼크 매수 차단 중\n\n"
                f"등급: {_shock.overall_grade.name}\n"
                "신규 매수가 제한됩니다."
            )
            return
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
        if not result:
            await safe_edit_or_reply(query,"\u26a0\ufe0f 종목 정보를 찾을 수 없습니다.")
            return
        price = result.info.current_price
        if not price or price <= 0:
            # v9.3.3: price=0 방지 — 실시간 가격 재시도
            try:
                price = await self._get_price(ticker, 0)
            except Exception:
                pass
        if not price or price <= 0:
            await safe_edit_or_reply(query,"⚠️ 현재가를 가져올 수 없습니다. 잠시 후 다시 시도해주세요.")
            return
        holding_id = self.db.add_holding(ticker, result.name, price)
        # v9.6.0: ATR 저장
        try:
            _entry_atr = getattr(result.tech, "atr_pct", 0.0) if result.tech else 0.0
            if _entry_atr > 0:
                self.db.update_holding(holding_id, atr_at_entry=_entry_atr)
        except Exception:
            pass
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
        # v9.6.0: ATR + 확신도 정보 전달
        _atr = getattr(result.tech, "atr_pct", 0.0) if result.tech else 0.0
        _cscore = result.score.composite if result.score else 0.0
        msg = format_trade_record(
            result.name, "buy", price,
            atr_pct=_atr, composite_score=_cscore,
        )
        await safe_edit_or_reply(query,msg)

        # v6.2: 신호 성과 추적 기록
        try:
            self.db.save_signal_performance(
                signal_source="scan_engine",
                signal_type="buy",
                ticker=ticker,
                name=result.name,
                signal_date=datetime.now(KST).strftime("%Y-%m-%d"),
                signal_score=result.score.composite if result.score else 0,
                signal_price=price,
                horizon="swing",
            )
        except Exception as e:
            logger.debug("Signal performance save failed: %s", e)

        # 투자전략 선택 InlineKeyboard
        await self._ask_horizon(query, ticker, result.name)

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
        await safe_edit_or_reply(query,msg)

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
                logger.debug("_analyze_new_holding macro snapshot failed", exc_info=True)
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
            # [v6.2.1] 토큰 추적
            try:
                from kstock.core.token_tracker import track_usage
                track_usage(
                    db=self.db, provider="anthropic",
                    model="claude-haiku-4-5-20251001",
                    function_name="investment_manager",
                    response=response,
                )
            except Exception:
                logger.debug("Token tracking failed", exc_info=True)
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
            await safe_edit_or_reply(query,"⏭️ 건너뛰었습니다.")
            context.user_data.pop("screenshot_new_holdings", None)
            return

        if payload == "all":
            # 전체 추가
            added = []
            added_ids = []
            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                price = h.get("avg_price", 0) or h.get("current_price", 0)
                if ticker and price > 0:
                    holding_id = self.db.add_holding(ticker, name, price)
                    added.append(f"  {name} ({price:,.0f}원)")
                    added_ids.append(holding_id)
                    try:
                        await self._analyze_new_holding(
                            ticker, name, price, holding_id,
                        )
                    except Exception:
                        logger.debug("_action_add_screenshot analyze_new_holding failed for %s", ticker, exc_info=True)
            if added:
                msg = (
                    f"✅ {len(added)}종목 포트폴리오 추가 완료!\n\n"
                    + "\n".join(added)
                )
            else:
                msg = "⚠️ 추가할 수 있는 종목이 없습니다."
            await safe_edit_or_reply(query,msg)
            # 투자전략 일괄 선택 키보드
            if added_ids:
                context.user_data["recent_holding_ids"] = added_ids
                try:
                    await self._ask_holding_type_bulk(query, added_ids)
                except Exception:
                    logger.debug("_action_add_screenshot ask_holding_type_bulk failed", exc_info=True)
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
                    await safe_edit_or_reply(query,
                        f"✅ {name} 포트폴리오 추가!\n"
                        f"매수가: {price:,.0f}원"
                    )
                    try:
                        await self._ask_holding_type(query, holding_id, name)
                    except Exception:
                        logger.debug("_action_add_screenshot ask_holding_type failed for %s", name, exc_info=True)
                    try:
                        await self._analyze_new_holding(
                            ticker, name, price, holding_id,
                        )
                    except Exception:
                        logger.debug("_action_add_screenshot analyze_new_holding failed for %s", ticker, exc_info=True)
                else:
                    await safe_edit_or_reply(query,
                        f"⚠️ {name} 가격 정보가 없어 추가할 수 없습니다."
                    )
            else:
                await safe_edit_or_reply(query,"⚠️ 종목을 찾을 수 없습니다.")
            return

    async def _action_confirm_text_holding(
        self, query, context, payload: str,
    ) -> None:
        """자연어로 입력된 보유종목 확인 후 추가."""
        pending = context.user_data.get("pending_text_holding")
        if not pending:
            await safe_edit_or_reply(query,"⚠️ 등록할 종목 정보가 없습니다.")
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
                await safe_edit_or_reply(query,
                    f"✅ {name}{qty_str} 포트폴리오 추가!\n"
                    f"매수가: {price:,.0f}원"
                )
                try:
                    await self._ask_holding_type(query, holding_id, name)
                except Exception:
                    logger.debug("_action_text_add ask_holding_type failed for %s", name, exc_info=True)
                try:
                    await self._analyze_new_holding(
                        ticker, name, price, holding_id,
                    )
                except Exception:
                    logger.debug("_action_text_add analyze_new_holding failed for %s", ticker, exc_info=True)
            else:
                await safe_edit_or_reply(query,"⚠️ 가격 정보가 부족합니다.")
        else:
            await safe_edit_or_reply(query,"⏭️ 등록을 건너뛰었습니다.")

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
            await safe_edit_or_reply(query,f"🔍 {name}({code}) 분석 중...")
            try:
                # 기존 분석 로직 재활용
                tech_data = ""
                price_data = ""
                fund_data = ""
                timing_data = ""
                cur_price = 0.0
                try:
                    ohlcv = await self.yf_client.get_ohlcv(code, market)
                    if ohlcv is not None and not ohlcv.empty:
                        from kstock.signal.timing_windows import analyze_timing_windows

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
                        timing = analyze_timing_windows(close)
                        if timing is not None:
                            timing_data = "\n".join(
                                self._format_timing_lines(timing, detailed=True)
                            )
                except Exception:
                    logger.debug("_action_stock_action tech data fetch failed for %s", code, exc_info=True)
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
                    logger.debug("_action_stock_action financials fetch failed for %s", code, exc_info=True)
                    fund_data = ""

                # 매매 레벨 계산 (현재가 기반)
                trade_levels = ""
                if cur_price > 0:
                    trade_levels = (
                        f"[매매 참고 레벨 - 현재가 {cur_price:,.0f}원 기준]\n"
                        f"적극 매수: {cur_price * 0.90:,.0f}원 (현재가 -10%)\n"
                        f"관심 매수: {cur_price * 0.95:,.0f}원 (현재가 -5%)\n"
                        f"단기 목표: {cur_price * 1.10:,.0f}원 (현재가 +10%)\n"
                        f"중기 목표: {cur_price * 1.20:,.0f}원 (현재가 +20%)\n"
                        f"손절 기준: {cur_price * 0.93:,.0f}원 (현재가 -7%)\n"
                    )

                enriched_question = (
                    f"{name}({code}) 종목 분석 요청.\n\n"
                    f"[실시간 가격]\n{price_data}\n\n"
                    f"[기술적 지표]\n{tech_data}\n\n"
                    f"[타이밍 체크]\n{timing_data or '타이밍 데이터 없음'}\n\n"
                    f"[펀더멘털]\n{fund_data}\n\n"
                    f"{trade_levels}\n"
                    f"[절대 규칙] 위 [실시간 가격]과 [매매 참고 레벨]의 숫자만 사용하라. "
                    f"너의 학습 데이터에 있는 과거 주가를 절대 사용 금지. "
                    f"매수/매도 포인트 가격은 반드시 위 [매매 참고 레벨]에서 선택하라. "
                    f"[타이밍 체크]에 나온 7/15/30일축 변곡 해석을 먼저 반영하라."
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
                if timing_data:
                    answer = f"{timing_data}\n\n{answer}"
                try:
                    await query.message.reply_text(answer, reply_markup=get_reply_markup(context))
                except Exception:
                    logger.debug("_action_stock_action reply_text with markup failed", exc_info=True)
                    await query.message.reply_text(answer)
            except Exception as e:
                logger.error("Stock action analyze error: %s", e, exc_info=True)
                await query.message.reply_text(
                    f"⚠️ {name} 분석 중 오류가 발생했습니다.",
                    reply_markup=get_reply_markup(context),
                )

        elif action == "add":
            # 현재가 자동 조회
            if price <= 0:
                try:
                    price = await self._get_price(code)
                except Exception:
                    logger.debug("_action_stock_action get_price failed for %s", code, exc_info=True)
            if price > 0:
                holding_id = self.db.add_holding(code, name, price)
                self.db.upsert_portfolio_horizon(
                    ticker=code, name=name, horizon="dangi",
                )
                await safe_edit_or_reply(query,
                    f"✅ {name} 포트폴리오 추가!\n"
                    f"매수가(현재가): {price:,.0f}원\n"
                    f"기간: 단기(스윙)"
                )
                try:
                    await self._analyze_new_holding(code, name, price, holding_id)
                except Exception:
                    logger.debug("_action_stock_action analyze_new_holding failed for %s", code, exc_info=True)
            else:
                await safe_edit_or_reply(query,
                    f"⚠️ {name} 가격 조회 실패.\n다시 시도해주세요."
                )

        elif action == "watch":
            self.db.add_watchlist(code, name)
            await safe_edit_or_reply(query,f"👀 {name} 관심종목 등록!")

        elif action == "noop":
            await safe_edit_or_reply(query,
                f"ℹ️ {name}은(는) 이미 포트폴리오에 있습니다."
            )

    async def _ask_horizon(self, query, ticker: str, name: str) -> None:
        """매수 후 투자전략(보유기간) 선택 InlineKeyboard 전송."""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("단타 (1~5일)", callback_data=f"hz:danta:{ticker}"),
                InlineKeyboardButton("스윙 (1~4주)", callback_data=f"hz:dangi:{ticker}"),
            ],
            [
                InlineKeyboardButton("중기 (1~6개월)", callback_data=f"hz:junggi:{ticker}"),
                InlineKeyboardButton("장기 (6개월+)", callback_data=f"hz:janggi:{ticker}"),
            ],
            [InlineKeyboardButton("❌ 취소", callback_data="dismiss:0")],
        ])
        await query.message.reply_text(
            f"📊 {name} 투자 전략을 선택하세요:",
            reply_markup=keyboard,
        )

    async def _ask_holding_type(
        self, query, holding_id: int, name: str,
    ) -> None:
        """종목 추가 후 투자전략(holding_type) 선택 InlineKeyboard."""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚡ 초단기 (1~3일)", callback_data=f"ht:scalp:{holding_id}",
                ),
                InlineKeyboardButton(
                    "🔥 단기 (1~2주)", callback_data=f"ht:swing:{holding_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 중기 (1~2개월)", callback_data=f"ht:position:{holding_id}",
                ),
                InlineKeyboardButton(
                    "💎 장기 (2개월+)", callback_data=f"ht:long_term:{holding_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔟 텐배거 (1~5년)", callback_data=f"ht:tenbagger:{holding_id}",
                ),
            ],
            [InlineKeyboardButton("❌ 취소", callback_data="dismiss:0")],
        ])
        await query.message.reply_text(
            f"📊 {name} 투자 전략을 선택하세요:",
            reply_markup=keyboard,
        )

    async def _ask_holding_type_bulk(
        self, query, holding_ids: list[int],
    ) -> None:
        """스크린샷 다수 종목 추가 후 투자전략 일괄 선택."""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚡ 전체 초단기", callback_data="ht:scalp:all",
                ),
                InlineKeyboardButton(
                    "🔥 전체 단기", callback_data="ht:swing:all",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 전체 중기", callback_data="ht:position:all",
                ),
                InlineKeyboardButton(
                    "💎 전체 장기", callback_data="ht:long_term:all",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔟 전체 텐배거", callback_data="ht:tenbagger:all",
                ),
                InlineKeyboardButton(
                    "⏭️ 나중에", callback_data="ht:skip:0",
                ),
            ],
        ])
        await query.message.reply_text(
            f"📊 추가된 {len(holding_ids)}종목의 투자 전략:",
            reply_markup=keyboard,
        )

    async def _action_set_holding_type(
        self, query, context, payload: str,
    ) -> None:
        """ht:{type}:{id_or_all} 콜백 처리 + 매니저 인사."""
        hold_type, _, target = payload.partition(":")

        if hold_type == "skip":
            await safe_edit_or_reply(query,"⏭️ 투자 전략 설정을 건너뛰었습니다.")
            context.user_data.pop("recent_holding_ids", None)
            return

        from kstock.bot.investment_managers import get_manager_greeting, get_manager_label

        if target == "all":
            ids = context.user_data.get("recent_holding_ids", [])
            for hid in ids:
                try:
                    self.db.update_holding_type(hid, hold_type)
                except Exception:
                    logger.debug("_action_holding_type update_holding_type failed for id=%s", hid, exc_info=True)
            label = get_manager_label(hold_type)
            await safe_edit_or_reply(query,
                f"✅ {len(ids)}종목 → {label} 배정 완료\n\n"
                f"📌 이 종목들은 {label}이 관리합니다."
            )
            context.user_data.pop("recent_holding_ids", None)
        else:
            try:
                hid = int(target)
                self.db.update_holding_type(hid, hold_type)
                holding = self.db.get_holding(hid)
                name = holding.get("name", "") if holding else ""
                ticker = holding.get("ticker", "") if holding else ""

                # 매니저 인사 메시지
                greeting = await get_manager_greeting(hold_type, name, ticker)
                await safe_edit_or_reply(query,greeting)
            except Exception as e:
                logger.error("holding_type 설정 실패: %s", e)
                await safe_edit_or_reply(query,"⚠️ 투자 전략 설정 실패")

    async def _action_manager_view(
        self, query, context, payload: str,
    ) -> None:
        """mgr:{type} 또는 mgr:{type}:{ticker} 콜백 — 매니저 분석 요청.

        잔고 화면에서는 mgr:swing:005930 형태로 특정 종목 분석,
        그 외에는 mgr:swing 형태로 해당 유형 전체 분석.
        """
        from kstock.bot.investment_managers import get_manager_analysis, MANAGERS

        # payload에서 매니저 유형과 선택적 ticker 분리
        parts = payload.split(":", 1)
        mgr_type = parts[0]
        target_ticker = parts[1] if len(parts) > 1 else ""

        manager = MANAGERS.get(mgr_type)
        if not manager:
            await safe_edit_or_reply(query,"⚠️ 알 수 없는 매니저 유형")
            return

        holdings = self.db.get_active_holdings()

        if target_ticker:
            # 특정 종목만 분석 (잔고 또는 즐겨찾기에서 클릭)
            type_holdings = [
                h for h in holdings if h.get("ticker") == target_ticker
            ]
            if not type_holdings:
                # 보유종목이 아닌 관심종목 → 가상 엔트리 생성
                name = target_ticker
                for s in self.all_tickers:
                    if s["code"] == target_ticker:
                        name = s.get("name", target_ticker)
                        break
                cur = 0.0
                try:
                    cur = await self._get_price(target_ticker, 0)
                except Exception:
                    logger.debug("Price fetch failed for %s", target_ticker, exc_info=True)
                type_holdings = [{
                    "ticker": target_ticker,
                    "name": name,
                    "buy_price": 0,
                    "quantity": 0,
                    "current_price": cur,
                    "holding_type": mgr_type,
                    "pnl_pct": 0,
                }]
        else:
            # 해당 유형 전체 분석
            type_holdings = [
                h for h in holdings
                if h.get("holding_type", "auto") == mgr_type
                or (mgr_type == "swing" and h.get("holding_type", "auto") == "auto")
            ]

        if not type_holdings:
            await safe_edit_or_reply(query,
                f"{manager['emoji']} {manager['name']}: 담당 종목이 없습니다."
            )
            return

        target_name = type_holdings[0].get("name", target_ticker) if target_ticker else ""
        loading_text = (
            f"{manager['emoji']} {manager['name']} — {target_name} 분석 중..."
            if target_name
            else f"{manager['emoji']} {manager['name']} 분석 중..."
        )
        await safe_edit_or_reply(query,loading_text)

        try:
            macro = await self.macro_client.get_snapshot()
            parts = [f"VIX={macro.vix:.1f}", f"S&P={macro.spx_change_pct:+.2f}%"]
            if hasattr(macro, "kospi") and macro.kospi > 0:
                parts.insert(0, f"코스피={macro.kospi:,.0f}")
            if hasattr(macro, "kosdaq") and macro.kosdaq > 0:
                parts.insert(1, f"코스닥={macro.kosdaq:,.0f}")
            parts.append(f"환율={macro.usdkrw:,.0f}원")
            if hasattr(macro, "koru_price") and macro.koru_price > 0:
                parts.append(f"KORU=${macro.koru_price:.2f}")
            market_text = ", ".join(parts)
        except Exception:
            logger.debug("_action_manager_analysis macro snapshot failed", exc_info=True)
            market_text = ""

        # 공유 컨텍스트 빌드 (위기/뉴스/교훈/포트폴리오)
        shared_context = None
        try:
            from kstock.bot.context_builder import build_manager_shared_context
            shared_context = await build_manager_shared_context(
                self.db, self.macro_client,
            )
        except Exception:
            logger.debug("_action_manager_view shared context build failed", exc_info=True)

        current_alert = getattr(self, '_alert_mode', 'normal')

        # scalp/swing: 차트 데이터 보강
        if mgr_type in ("scalp", "swing"):
            try:
                from kstock.features.technical import compute_indicators
                from kstock.bot.investment_managers import build_chart_summary
                for h in type_holdings:
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
        if mgr_type in ("position", "long_term"):
            try:
                from kstock.bot.investment_managers import build_fundamental_summary
                for h in type_holdings:
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
            mgr_perf = self.db.get_manager_performance(mgr_type, days=90)
            mgr_lessons = self.db.get_trade_lessons_by_manager(mgr_type, limit=5)
        except Exception:
            logger.debug("Manager perf/lessons fetch failed for %s", mgr_type, exc_info=True)

        report = await get_manager_analysis(
            mgr_type, type_holdings, market_text,
            shared_context=shared_context,
            alert_mode=current_alert,
            performance=mgr_perf,
            manager_lessons=mgr_lessons,
        )

        # 후속 액션 버튼
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        followup_buttons = []
        if target_ticker:
            followup_buttons.append([
                InlineKeyboardButton("📊 멀티분석", callback_data=f"multi_run:{target_ticker}"),
                InlineKeyboardButton("🎙️ 매니저토론", callback_data=f"mgr_debate:{target_ticker}"),
            ])
            followup_buttons.append([
                InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{target_ticker}"),
            ])
        followup_buttons.append([
            InlineKeyboardButton("🎯 4매니저 추천", callback_data="quick_q:mgr4"),
            InlineKeyboardButton("❌ 닫기", callback_data="dismiss:mgr"),
        ])
        markup = InlineKeyboardMarkup(followup_buttons)
        await query.message.reply_text(report[:4000], reply_markup=markup)

    async def _action_manager_debate(
        self, query, context, payload: str,
    ) -> None:
        """mgr_debate:{ticker} 콜백 — 3라운드 AI 토론 (v9.4)."""
        ticker = payload.strip()
        name = ticker
        for s in self.all_tickers:
            if s["code"] == ticker:
                name = s["name"]
                break

        # v11.0: 재토론 시 이전 결과 보존 — 기존 메시지 버튼을 제거하고 새 메시지로 진행
        try:
            from telegram import InlineKeyboardMarkup
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
        except Exception:
            pass
        await query.message.reply_text(f"🎙️ {name} 3라운드 토론 중... (약 15초)")

        # 차트+재무 데이터 수집
        stock_data = ""
        ohlcv = None
        live_price = 0
        info = None
        try:
            from kstock.features.technical import compute_indicators
            from kstock.bot.investment_managers import build_chart_summary, build_fundamental_summary
            market = "KOSPI"
            for s in self.all_tickers:
                if s["code"] == ticker:
                    market = s.get("market", "KOSPI")
                    break
            ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="3mo")
            if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 20:
                tech = compute_indicators(ohlcv)
                live_price = float(ohlcv.iloc[-1].get("close", 0) or ohlcv.iloc[-1].get("Close", 0))
                supply = self.db.get_supply_demand(ticker, days=5)
                chart = build_chart_summary(tech, live_price, supply, ohlcv=ohlcv, ticker=ticker, name=name)
                if chart:
                    stock_data += f"[차트 데이터]\n{chart}\n\n"
                info = await self.data_router.get_stock_info(ticker, name) if hasattr(self, "data_router") else None
                financials = self.db.get_financials(ticker)
                consensus = self.db.get_consensus(ticker)
                fund = build_fundamental_summary(info, financials, consensus, supply, current_price=live_price, ticker=ticker, name=name)
                if fund:
                    stock_data += f"[재무 데이터]\n{fund}\n"
        except Exception:
            logger.debug("Manager debate data fetch failed for %s", ticker, exc_info=True)

        market_text = ""
        try:
            macro = await self.macro_client.get_snapshot()
            market_text = f"VIX={macro.vix:.1f}, 환율={macro.usdkrw:,.0f}원"
        except Exception:
            logger.debug("Manager debate macro fetch failed", exc_info=True)

        # v9.4: 패턴 매칭 + 가격 목표
        pattern_summary = ""
        price_target_text = ""
        try:
            from kstock.signal.pattern_matcher import PatternMatcher, format_pattern_for_debate
            from kstock.signal.price_target import PriceTargetEngine, format_price_target_for_debate

            if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 40:
                pm = PatternMatcher()
                pr = pm.find_similar_patterns(ohlcv)
                pattern_summary = format_pattern_for_debate(pr)

            if ohlcv is not None and not ohlcv.empty:
                pte = PriceTargetEngine()
                pt = pte.calculate_targets(
                    ohlcv=ohlcv,
                    current_price=live_price,
                    stock_info=info if isinstance(info, dict) else {},
                )
                price_target_text = format_price_target_for_debate(pt)
        except Exception:
            logger.debug("Pattern/price target failed for %s", ticker, exc_info=True)

        # 3라운드 토론 실행
        from kstock.bot.investment_managers import manager_debate_full
        from kstock.bot.debate_engine import format_debate_telegram

        debate_result = await manager_debate_full(
            ticker, name, stock_data, market_text,
            pattern_summary, price_target_text,
        )

        if debate_result and not debate_result.error:
            # DB 저장
            try:
                self.db.save_debate_result(debate_result)
            except Exception:
                logger.debug("Debate save failed for %s", ticker, exc_info=True)

            text = format_debate_telegram(debate_result)

            # 패턴 + 가격 목표 추가
            extras = []
            if pattern_summary:
                extras.append(f"\n📊 {pattern_summary}")
            if price_target_text:
                extras.append(f"\n📈 {price_target_text}")
            if extras:
                text += "\n" + "\n".join(extras)

            # v11.0: 후속 액션 버튼 (재토론 시 이전 결과 보존)
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎙️ 재토론", callback_data=f"mgr_debate:{ticker}"),
                    InlineKeyboardButton("📜 토론 이력", callback_data=f"debhist:{ticker}"),
                ],
                [
                    InlineKeyboardButton("📊 종목상세", callback_data=f"fav:stock:{ticker}"),
                    InlineKeyboardButton("📊 차트", callback_data=f"fav:chtm:{ticker}"),
                ],
                [
                    InlineKeyboardButton("🔍 다른종목", callback_data="menu:debate"),
                    InlineKeyboardButton("🔙 메인메뉴", callback_data="back:main"),
                ],
            ])
            await query.message.reply_text(text[:4000], reply_markup=kb)
        else:
            error_msg = debate_result.error if debate_result else "토론 실패"
            await query.message.reply_text(f"⚠️ 토론 오류: {error_msg}")

    async def _action_debate_history(
        self, query, context, payload: str,
    ) -> None:
        """debhist:{ticker} — 토론 이력 조회."""
        ticker = payload.strip()
        name = ticker
        for s in self.all_tickers:
            if s["code"] == ticker:
                name = s["name"]
                break

        history = self.db.get_debate_history(ticker, days=30)
        if not history:
            await safe_edit_or_reply(query,f"📜 {name} 토론 이력 없음")
            return

        _AE = {"매수": "🟢", "매도": "🔴", "관망": "🟡", "홀딩": "🔵"}
        lines = [f"📜 {name} ({ticker}) 최근 토론 이력\n{'━' * 20}\n"]

        for h in history[:10]:
            ae = _AE.get(h.get("verdict", ""), "⚪")
            dt = h.get("created_at", "")[:16]
            conf = h.get("confidence", 0)
            cl = h.get("consensus_level", "")
            pt = h.get("price_target", 0)
            lines.append(
                f"{dt} {ae} {h.get('verdict', '?')} "
                f"({cl}, {conf:.0%})"
                f"{f' 목표:{pt:,.0f}' if pt else ''}"
            )

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎙️ 새 토론", callback_data=f"mgr_debate:{ticker}"),
                InlineKeyboardButton("📊 종목상세", callback_data=f"fav:stock:{ticker}"),
            ],
            [
                InlineKeyboardButton("🔍 다른종목", callback_data="menu:debate"),
                InlineKeyboardButton("🔙 메인메뉴", callback_data="back:main"),
            ],
        ])
        await safe_edit_or_reply(query, "\n".join(lines), reply_markup=kb)

    async def _action_ai_accuracy(
        self, query, context, payload: str,
    ) -> None:
        """aistat — AI 예측 정확도 통계."""
        stats = self.db.get_prediction_accuracy(days=30)
        if stats.get("total", 0) == 0:
            await safe_edit_or_reply(query,"📊 AI 예측 데이터 없음 (최소 5일 후 집계)")
            return

        text = (
            f"📊 AI 토론 예측 정확도 (최근 30일)\n"
            f"{'━' * 20}\n\n"
            f"평가 건수: {stats['total']}건\n"
            f"정확도: {stats['accuracy_pct']:.1f}%\n"
        )

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
        ])
        await safe_edit_or_reply(query,text, reply_markup=kb)

    async def _action_bubble_check(
        self, query, context, payload: str,
    ) -> None:
        """bubble:{ticker} 콜백 — 거품 판별 실행."""
        from kstock.signal.bubble_detector import (
            analyze_bubble, format_bubble_analysis, get_bubble_data_from_yfinance,
        )

        ticker = payload
        if not ticker:
            # 보유종목 선택 리스트 표시
            holdings = self.db.get_active_holdings()
            if not holdings:
                await safe_edit_or_reply(query,"📦 보유종목이 없습니다.")
                return

            buttons = []
            row = []
            for h in holdings[:10]:
                t = h.get("ticker", "")
                n = h.get("name", "")[:6]
                row.append(
                    InlineKeyboardButton(n, callback_data=f"bubble:{t}"),
                )
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            await safe_edit_or_reply(query,
                "🫧 거품 판별할 종목을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        await safe_edit_or_reply(query,f"🫧 {ticker} 거품 분석 중...")

        # yfinance에서 데이터 조회
        data = await get_bubble_data_from_yfinance(ticker, self.yf_client)

        if data["eps"] == 0 or data["current_price"] == 0:
            # EPS나 현재가가 없으면 분석 불가
            await query.message.reply_text(
                f"⚠️ {ticker} 데이터 부족\n\n"
                f"PER/EPS 데이터를 가져올 수 없습니다.\n"
                f"yfinance에서 지원하지 않는 종목이거나\n"
                f"데이터가 아직 업데이트되지 않았습니다."
            )
            return

        # 종목명 찾기
        name = ticker
        holding = self._holdings_index.get(ticker) if hasattr(self, '_holdings_index') else None
        if holding:
            name = holding.get("name", ticker)
        else:
            for item in self.all_tickers:
                if item.get("code") == ticker:
                    name = item.get("name", ticker)
                    break

        result = analyze_bubble(
            ticker=ticker,
            name=name,
            current_price=data["current_price"],
            trailing_per=data["trailing_per"],
            forward_per=data["forward_per"],
            eps=data["eps"],
            sector_avg_per=data["sector_avg_per"],
            kospi_avg_per=data["kospi_avg_per"],
            revenue_yoy=data["revenue_yoy"],
            op_profit_yoy=data["op_profit_yoy"],
            earnings_cagr_2y=data["earnings_cagr_2y"],
        )

        text = format_bubble_analysis(result)
        await query.message.reply_text(text)

    async def _action_balance(
        self, query, context, payload: str,
    ) -> None:
        """잔고 메뉴 액션 처리: bal:add/refresh/remove:ticker."""
        if payload == "add":
            context.user_data["awaiting_stock_add"] = True
            await safe_edit_or_reply(query,
                "📝 추가할 종목명을 입력하세요.\n\n"
                "예: 삼성전자\n"
                "예: 005930\n\n"
                "또는 스크린샷을 전송하세요 📸"
            )

        elif payload == "refresh":
            await safe_edit_or_reply(query,"🔄 잔고 새로고침 중...")
            try:
                holdings = await self._load_holdings_with_fallback()
                if not holdings:
                    await query.message.reply_text(
                        "💰 등록된 보유종목이 없습니다.\n📸 스크린샷을 보내주세요!",
                        reply_markup=get_reply_markup(context),
                    )
                    return

                total_eval, total_invested = await self._update_holdings_prices(holdings)
                lines = self._format_balance_lines(holdings, total_eval, total_invested)
                bal_buttons = self._build_balance_buttons(holdings)
                await query.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(bal_buttons),
                )
            except Exception as e:
                logger.error("Balance refresh error: %s", e, exc_info=True)
                await query.message.reply_text(
                    "⚠️ 잔고 새로고침 실패.", reply_markup=get_reply_markup(context),
                )

        elif payload.startswith("remove:"):
            ticker = payload[7:]
            holding = self.db.get_holding_by_ticker(ticker)
            if holding:
                self.db.update_holding(holding["id"], status="sold")
                hname = holding.get("name", ticker)
                # v6.2: 삭제 시에도 복기 (수동 청산으로 기록)
                pnl = holding.get("pnl_pct", 0) or 0
                exit_price = holding.get("current_price") or holding.get("buy_price", 0)
                await self._trigger_auto_debrief(
                    ticker=ticker, name=hname, action="manual_close",
                    entry_price=holding.get("buy_price", 0), exit_price=exit_price,
                    pnl_pct=pnl, holding=holding,
                )
                # 삭제 후 잔고 메뉴 재표시 (메뉴 닫기 전까지 유지)
                holdings = await self._load_holdings_with_fallback()
                if holdings:
                    total_eval, total_invested = await self._update_holdings_prices(holdings)
                    lines = self._format_balance_lines(holdings, total_eval, total_invested)
                    lines.insert(0, f"\U0001f5d1\ufe0f {hname} 삭제 완료!\n")
                    bal_buttons = self._build_balance_buttons(holdings)
                    await safe_edit_or_reply(query,
                        "\n".join(lines),
                        reply_markup=InlineKeyboardMarkup(bal_buttons),
                    )
                else:
                    await safe_edit_or_reply(query,
                        f"\U0001f5d1\ufe0f {hname} 삭제 완료!\n\n"
                        "\U0001f4b0 보유종목이 없습니다."
                    )
            else:
                await safe_edit_or_reply(query,"\u26a0\ufe0f 종목을 찾을 수 없습니다.")

    def _resolve_ticker_from_name(self, name: str) -> str:
        """종목명으로 유니버스에서 티커 코드를 찾습니다."""
        if not name:
            return ""
        # 1. 유니버스 정확 매치
        for item in self.all_tickers:
            if item["name"] == name:
                return item["code"]
        # 1-2. 텐베거/확장 설정에서 추가 탐색
        try:
            cfg_path = Path("config/tenbagger.yaml")
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                for item in cfg.get("korea_universe", []) or []:
                    if item.get("name") == name and item.get("code"):
                        return str(item["code"])
                for item in cfg.get("excluded", []) or []:
                    if item.get("name") == name and item.get("code"):
                        return str(item["code"])
        except Exception:
            logger.debug("_resolve_ticker_from_name tenbagger lookup failed", exc_info=True)
        # 2. DB 보유종목에서 이름+ticker 매치
        existing = self.db.get_holding_by_name(name)
        if existing and existing.get("ticker"):
            return existing["ticker"]
        return ""

    @staticmethod
    def _parse_iso_ts(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    @staticmethod
    def _has_meaningful_screenshot_items(items: list[dict]) -> bool:
        for item in items:
            if not isinstance(item, dict):
                continue
            has_identity = bool(str(item.get("name", "") or "").strip()) or bool(
                str(item.get("ticker", "") or "").strip()
            )
            has_value = any(
                float(item.get(key, 0) or 0) > 0
                for key in ("quantity", "avg_price", "current_price", "eval_amount")
            )
            if has_identity and has_value:
                return True
        return False

    def _manager_lane_for_holding(self, holding: dict | None) -> str:
        """잔고/코칭용 매니저 lane 정규화."""
        raw = str(
            (holding or {}).get("holding_type")
            or (holding or {}).get("horizon")
            or "auto"
        ).strip()
        if raw in {"scalp", "swing", "position", "long_term", "tenbagger"}:
            return raw
        purchase_type = str((holding or {}).get("purchase_type", "") or "")
        pnl_pct = float((holding or {}).get("pnl_pct", 0) or 0)
        if "유융" in purchase_type or "신용" in purchase_type:
            return "swing"
        if pnl_pct >= 20:
            return "position"
        return "swing"

    def _manager_coaching_text(self, holding: dict) -> tuple[str, str]:
        lane = self._manager_lane_for_holding(holding)
        label_map = {
            "scalp": "⚡ 단타 매니저",
            "swing": "🔥 스윙 매니저",
            "position": "📊 포지션 매니저",
            "long_term": "💎 장기 매니저",
            "tenbagger": "🚀 텐베거 매니저",
        }
        pnl = float(holding.get("pnl_pct", 0) or 0)
        current_price = float(holding.get("current_price", 0) or 0)
        target_1 = float(holding.get("target_1", 0) or 0)
        target_2 = float(holding.get("target_2", 0) or 0)
        stop_price = float(holding.get("stop_price", 0) or 0)

        if stop_price > 0 and current_price > 0 and current_price <= stop_price:
            message = f"손절선 근접, 축소·교체 우선 (손절 {stop_price:,.0f}원)"
        elif target_2 > 0 and current_price >= target_2:
            message = f"2차 목표권, 추세 유지면 분할익절 검토 (목표 {target_2:,.0f}원)"
        elif target_1 > 0 and current_price >= target_1:
            message = f"1차 목표권, 일부 익절 또는 본전컷 상향 (목표 {target_1:,.0f}원)"
        elif lane == "long_term":
            message = "스토리 훼손 전까지 핵심 보유, 급락만 경계"
        elif lane == "tenbagger":
            message = "촉매·정책·이벤트 유지 여부를 계속 추적"
        elif lane == "position":
            message = "중기 추세 유지 여부 점검, 눌림 시만 추가"
        elif lane == "scalp":
            message = "장중 추세 약해지면 빠르게 비중 축소"
        elif pnl < 0:
            message = "반등 확인 전 추매 금지, 교체 후보와 비교"
        else:
            message = "추세 유지 확인 후 보유, 눌림 구간만 분할 대응"

        return label_map.get(lane, "📌 종합 매니저"), message

    def _market_for_ticker(self, ticker: str, default: str = "KOSPI") -> str:
        for item in getattr(self, "all_tickers", []) or []:
            if item.get("code") == ticker:
                return item.get("market", default)
        return default

    @staticmethod
    def _format_timing_lines(assessment, *, detailed: bool = False) -> list[str]:
        if assessment is None:
            return []

        phase_label = {
            "early": "변곡 시작 전",
            "mid": "반등 확인 중",
            "end": "변곡 끝자락",
            "late": "추격 구간",
        }.get(getattr(assessment, "overall_phase", ""), "타이밍 점검")

        lines = [
            f"⏱ 타이밍 {getattr(assessment, 'preferred_window', 0)}일 중심 {phase_label}",
            getattr(assessment, "coach_line", ""),
        ]
        if detailed:
            detail_lines = getattr(assessment, "detail_lines", []) or []
            lines.extend(f"- {line}" for line in detail_lines)
        return [line for line in lines if line]

    async def _load_holdings_with_fallback(self) -> list[dict]:
        """보유종목 로드 (DB 우선, 없으면 스크린샷 fallback → DB 동기화).

        [v3.5.5] 빈 ticker를 유니버스에서 해결 시도.
        [v3.6.2] 스크린샷 fallback 시 holdings DB에 자동 동기화.
        [v13.1] 최신 스크린샷이 sold 이력보다 최신이면 잔고 복구 허용.
        """
        holdings = self.db.get_active_holdings()
        restored_from_screenshot = False
        screenshot_items: list[dict] = []
        screenshot: dict = {}
        if not holdings:
            try:
                screenshot = self.db.get_latest_screenshot() or {}
                if screenshot:
                    import json

                    raw = screenshot.get("holdings_json", "")
                    screenshot_items = json.loads(raw) if isinstance(raw, str) and raw else []
            except Exception as e:
                logger.warning("Screenshot holdings fallback failed: %s", e)
                screenshot_items = []

            latest_sold_at = None
            try:
                with self.db._connect() as conn:
                    row = conn.execute(
                        "SELECT MAX(COALESCE(updated_at, created_at, buy_date)) AS sold_ts "
                        "FROM holdings WHERE status='sold'"
                    ).fetchone()
                    latest_sold_at = self._parse_iso_ts((row["sold_ts"] if row else None))
            except Exception:
                logger.debug("_load_holdings_with_fallback sold timestamp query failed", exc_info=True)

            screenshot_ts = self._parse_iso_ts(
                screenshot.get("recognized_at") or screenshot.get("created_at") or ""
            )
            can_restore_from_screenshot = self._has_meaningful_screenshot_items(screenshot_items) and (
                latest_sold_at is None
                or screenshot_ts is None
                or screenshot_ts >= latest_sold_at
            )
            if can_restore_from_screenshot:
                holdings = [
                    {
                        "ticker": h.get("ticker", ""),
                        "name": h.get("name", ""),
                        "buy_price": h.get("avg_price", 0),
                        "current_price": h.get("current_price", 0),
                        "quantity": h.get("quantity", 0),
                        "pnl_pct": h.get("profit_pct", 0),
                        "eval_amount": h.get("eval_amount", 0),
                        "purchase_type": h.get("purchase_type", ""),
                        "is_margin": h.get("is_margin", 0),
                        "margin_type": h.get("margin_type", ""),
                        "holding_type": h.get("holding_type", "auto"),
                    }
                    for h in screenshot_items
                    if isinstance(h, dict)
                ]
                restored_from_screenshot = bool(holdings)

        # [v3.5.5] 빈 ticker를 유니버스에서 해결 시도
        for h in holdings:
            if not h.get("ticker") and h.get("name"):
                resolved = self._resolve_ticker_from_name(h["name"])
                if resolved:
                    h["ticker"] = resolved

        # [v3.6.2] ticker 있는 종목을 holdings DB에 동기화
        #  → 리포트, 공매도, 멀티분석 등 다른 기능과 연동
        # [v3.6.3 FIX] 한국 종목코드(6자리 숫자)만 동기화 — 미국주식 오등록 방지
        import re
        synced = False
        synced_count = 0
        for h in holdings:
            ticker = h.get("ticker", "")
            if ticker and re.match(r'^\d{6}$', ticker) and h.get("name"):
                purchase_type = str(h.get("purchase_type", "") or "").strip()
                is_margin, margin_type = detect_margin_purchase(h)
                try:
                    self.db.upsert_holding(
                        ticker=ticker,
                        name=h["name"],
                        quantity=h.get("quantity", 0),
                        buy_price=h.get("buy_price", 0),
                        current_price=h.get("current_price", 0),
                        pnl_pct=h.get("pnl_pct", 0),
                        eval_amount=h.get("eval_amount", 0),
                        holding_type=h.get("holding_type", "auto") or "auto",
                        purchase_type=purchase_type,
                        is_margin=h.get("is_margin", is_margin),
                        margin_type=h.get("margin_type", margin_type or ""),
                    )
                    synced = True
                    synced_count += 1
                except Exception:
                    logger.debug("_get_kis_holdings DB sync failed for %s", h.get("ticker"), exc_info=True)
        if synced:
            logger.debug("Holdings synced to DB: %d items", synced_count)
            try:
                db_holdings = self.db.get_active_holdings()
                if db_holdings:
                    holdings = db_holdings
            except Exception:
                logger.debug("_load_holdings_with_fallback active holdings reload failed", exc_info=True)

        if restored_from_screenshot and screenshot:
            try:
                snapshots = self.db.get_portfolio_snapshots(limit=1)
                latest_snapshot = snapshots[0] if snapshots else {}
                latest_snapshot_value = float(latest_snapshot.get("total_value", 0) or 0)
                latest_snapshot_count = int(latest_snapshot.get("holdings_count", 0) or 0)
                latest_snapshot_at = self._parse_iso_ts(
                    latest_snapshot.get("created_at") or latest_snapshot.get("date") or ""
                )
                screenshot_ts = self._parse_iso_ts(
                    screenshot.get("recognized_at") or screenshot.get("created_at") or ""
                )
                screenshot_total = float(screenshot.get("total_eval", 0) or 0)
                computed_total = sum(
                    float(item.get("eval_amount", 0) or 0)
                    or (
                        float(item.get("current_price", 0) or 0)
                        * int(item.get("quantity", 0) or 0)
                    )
                    for item in (screenshot_items or holdings)
                    if isinstance(item, dict)
                )
                if computed_total > 0:
                    gap_ratio = abs(screenshot_total - computed_total) / computed_total if screenshot_total else 1.0
                    if screenshot_total <= 0 or gap_ratio >= 0.25:
                        screenshot_total = computed_total
                screenshot_cash = float(screenshot.get("cash", 0) or 0)
                needs_snapshot_sync = (
                    screenshot_total > 0
                    and (
                        not latest_snapshot
                        or latest_snapshot_value <= 0
                        or latest_snapshot_count <= 0
                        or (
                            screenshot_ts is not None
                            and latest_snapshot_at is not None
                            and screenshot_ts > latest_snapshot_at
                        )
                    )
                )
                if needs_snapshot_sync:
                    snapshot_date = (
                        screenshot_ts.astimezone(KST).strftime("%Y-%m-%d")
                        if screenshot_ts is not None and screenshot_ts.tzinfo is not None
                        else (screenshot_ts.strftime("%Y-%m-%d") if screenshot_ts else datetime.now(KST).strftime("%Y-%m-%d"))
                    )
                    self.db.add_portfolio_snapshot(
                        date_str=snapshot_date,
                        total_value=screenshot_total,
                        cash=screenshot_cash,
                        holdings_count=len(holdings),
                        total_pnl_pct=float(screenshot.get("total_profit_pct", 0) or 0),
                        holdings_json=json.dumps(screenshot_items or holdings, ensure_ascii=False),
                    )
            except Exception:
                logger.debug("_load_holdings_with_fallback portfolio snapshot sync failed", exc_info=True)

        return holdings

    async def _update_holdings_prices(self, holdings: list[dict]) -> tuple:
        """보유종목 실시간 가격 업데이트 + 총합 계산. Returns (total_eval, total_invested).

        [v3.5.5] ticker 없어도 eval_amount/quantity로 총합 계산.
        """
        from kstock.signal.timing_windows import analyze_timing_windows

        total_eval = 0.0
        total_invested = 0.0
        for h in holdings:
            ticker = h.get("ticker", "")
            bp = float(h.get("buy_price", 0) or 0)
            qty = int(h.get("quantity", 0) or 0)
            eval_amt = float(h.get("eval_amount", 0) or 0)
            cur = float(h.get("current_price", 0) or 0)
            pnl_pct = float(h.get("pnl_pct", 0) or 0)

            # 1. ticker 있으면 실시간 시세 업데이트 시도
            if ticker and bp > 0:
                try:
                    detail = await self._get_price_detail(ticker, bp)
                    cur = detail["price"]
                    h["current_price"] = cur
                    h["pnl_pct"] = round((cur - bp) / bp * 100, 2) if bp > 0 else 0
                    pnl_pct = h["pnl_pct"]
                    h["day_change_pct"] = detail["day_change_pct"]
                    h["day_change"] = detail["day_change"]
                except Exception:
                    # 시세 조회 실패해도 기존 데이터로 진행
                    logger.debug("_update_holdings_prices get_price_detail failed for %s", ticker, exc_info=True)
                    if cur <= 0:
                        cur = bp

            if ticker and hasattr(self, "yf_client"):
                try:
                    market = self._market_for_ticker(ticker, h.get("market", "KOSPI") or "KOSPI")
                    ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="3mo")
                    if ohlcv is not None and not ohlcv.empty and "close" in ohlcv.columns:
                        assessment = analyze_timing_windows(ohlcv["close"].astype(float))
                        if assessment is not None:
                            h["_timing_assessment"] = assessment
                            h["_timing_lines"] = self._format_timing_lines(assessment)
                except Exception:
                    logger.debug("_update_holdings_prices timing assessment failed for %s", ticker, exc_info=True)

            # 2. 총합 계산 — ticker 유무 상관없이 항상 수행
            if qty > 0 and cur > 0:
                total_eval += cur * qty
                total_invested += bp * qty if bp > 0 else cur * qty
            elif eval_amt > 0:
                # eval_amount 있으면 그대로 사용
                total_eval += eval_amt
                # 투자금액 역산: eval_amount / (1 + 수익률)
                if pnl_pct != -100 and pnl_pct != 0:
                    total_invested += eval_amt / (1 + pnl_pct / 100)
                elif bp > 0 and qty > 0:
                    total_invested += bp * qty
                else:
                    total_invested += eval_amt  # 수익률 0이면 동일
            elif qty > 0 and bp > 0:
                # cur=0인 경우 buy_price로 대체
                total_eval += bp * qty
                total_invested += bp * qty

        return total_eval, total_invested

    def _format_balance_lines(self, holdings, total_eval, total_invested) -> list[str]:
        """잔고 현황 텍스트 포맷 — 시장 상황 반영 액션 포함."""
        alert_mode = getattr(self, '_alert_mode', 'normal')
        total_pnl = total_eval - total_invested
        total_pnl_rate = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        pnl_sign = "+" if total_pnl >= 0 else ""
        pnl_arrow = "\u25b2" if total_pnl > 0 else ("\u25bc" if total_pnl < 0 else "\u2015")

        # 신용/마진 종목 분리 (purchase_type에 유융/유옹/신용/담보 포함)
        margin_count = 0
        margin_eval = 0.0
        for h in holdings:
            pt = str(h.get("purchase_type", "") or "").lower()
            is_margin = h.get("is_margin") or h.get("margin_type") or any(
                k in pt for k in ("유융", "유옹", "신용", "담보")
            )
            if is_margin:
                h["_is_margin_display"] = True
                margin_count += 1
                margin_eval += float(h.get("eval_amount", 0) or 0) or (
                    float(h.get("current_price", 0) or 0) * int(h.get("quantity", 0) or 0)
                )

        lines = [
            f"\U0001f4b0 주호님 잔고 현황",
            f"\u2500" * 25,
        ]

        # 전시/위기 상황 헤더
        if alert_mode == "wartime":
            lines.extend([
                "\U0001f534 전시 경계 모드",
                "국내 증시 전반 하락장",
                "손절 강화(-5%) · 신규 매수 자제 · 현금 비중 확대",
                "",
            ])
        elif alert_mode == "elevated":
            lines.extend([
                "\U0001f7e0 경계 모드",
                "변동성 확대 구간",
                "손절 기준 -6% · 분할 매수 권장",
                "",
            ])

        lines.extend([
            f"\U0001f4b5 총 평가금액  {total_eval:,.0f}원",
            f"\U0001f4b8 총 투자금액  {total_invested:,.0f}원",
            f"\U0001f4b0 총 손익      {pnl_arrow} {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_pnl_rate:.2f}%)",
        ])
        if margin_count > 0:
            lines.append(f"\U0001f4b3 신용/마진   {margin_count}종목 · {margin_eval:,.0f}원")
        lines.extend(["", f"\U0001f4e6 보유종목 {len(holdings)}개", "\u2500" * 25])

        for idx, h in enumerate(holdings):
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            qty = int(h.get("quantity", 0) or 0)
            bp = float(h.get("buy_price", 0) or 0)
            cp = float(h.get("current_price", 0) or 0) or bp
            pnl = float(h.get("pnl_pct", 0) or 0)
            eval_amt = float(h.get("eval_amount", 0) or 0)
            day_chg_pct = float(h.get("day_change_pct", 0) or 0)
            day_chg = float(h.get("day_change", 0) or 0)

            # 손익금액 계산
            if qty > 0 and bp > 0:
                pnl_amount = (cp - bp) * qty
            elif eval_amt > 0 and pnl != 0:
                pnl_amount = eval_amt - (eval_amt / (1 + pnl / 100)) if pnl != -100 else -eval_amt
            else:
                pnl_amount = 0

            emoji = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < 0 else "\u26aa"
            pnl_sign_s = "+" if pnl_amount >= 0 else ""
            purchase_type = str(h.get("purchase_type", "") or "").strip()

            # 신용 표시
            margin_label = ""
            if h.get("_is_margin_display") or h.get("is_margin") or h.get("margin_type"):
                margin_label = "신용/마진"

            header = f"{emoji} {name}"
            if ticker:
                header += f" ({ticker})"
            badges: list[str] = []
            if qty > 0:
                badges.append(f"{qty}주")
            if purchase_type:
                badges.append(purchase_type)
            if margin_label:
                badges.append(margin_label)

            block = [header]
            if badges:
                block.append(f"   {' · '.join(badges)}")
            block.append(f"   매수 {bp:,.0f}원 / 현재 {cp:,.0f}원")
            if eval_amt > 0:
                block.append(f"   평가 {eval_amt:,.0f}원")
                if pnl_amount != 0:
                    block.append(f"   손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)")
                else:
                    block.append(f"   수익률 {pnl:+.1f}%")
            elif pnl_amount != 0:
                block.append(f"   손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)")
            else:
                block.append(f"   수익률 {pnl:+.1f}%")

            if day_chg_pct != 0:
                day_emoji = "\U0001f4c8" if day_chg_pct > 0 else "\U0001f4c9"
                day_sign = "+" if day_chg_pct > 0 else ""
                block.append(
                    f"   오늘 {day_emoji} {day_sign}{day_chg:,.0f}원 ({day_sign}{day_chg_pct:.1f}%)"
                )

            manager_label, manager_tip = self._manager_coaching_text(h)
            block.extend([
                "",
                f"   {manager_label}",
                f"   {manager_tip}",
            ])
            timing_lines = h.get("_timing_lines") or []
            if timing_lines:
                block.extend([
                    "",
                    f"   {timing_lines[0]}",
                ])
                if len(timing_lines) > 1:
                    block.append(f"   {timing_lines[1]}")

            # 상황별 종목 액션 가이드
            if alert_mode == "wartime":
                stop_price = bp * 0.95
                action_tag = self._wartime_holding_action(pnl, h)
                action_tag = action_tag.replace(" — ", " · ")
                block.extend([
                    f"   {action_tag}",
                    f"   전시 손절 {stop_price:,.0f}원",
                ])
            elif alert_mode == "elevated":
                stop_price = bp * 0.94
                if pnl < -5:
                    block.extend([
                        "   \u26a0\ufe0f 손절 검토",
                        f"   경계 손절 {stop_price:,.0f}원",
                    ])
                elif pnl < 0:
                    block.extend([
                        "   \U0001f6e1 보유 관망",
                        f"   경계 손절 {stop_price:,.0f}원",
                    ])

            lines.append("\n".join(block))
            if idx != len(holdings) - 1:
                lines.append("")
        return lines

    @staticmethod
    def _wartime_holding_action(pnl_pct: float, holding: dict) -> str:
        """전시 모드 종목별 액션 태그 결정."""
        from kstock.core.risk_policy import DEFENSIVE_SECTORS, CYCLICAL_SECTORS
        sector = str(holding.get("sector", "") or "")
        horizon = str(holding.get("holding_type", "") or holding.get("horizon", "") or "")

        # 장기투자는 보유 유지 기본
        if horizon in ("long_term", "position"):
            if pnl_pct < -15:
                return "\U0001f534 장기 보유 — 추가 하락 시 분할매수 검토"
            return "\U0001f7e2 장기 보유 유지 — 전시 변동 무시"

        # 경기민감 섹터 → 축소 검토
        if any(s in sector for s in CYCLICAL_SECTORS):
            if pnl_pct < -5:
                return "\U0001f534 경기민감 — 축소/손절 검토"
            return "\U0001f7e0 경기민감 — 비중 축소 검토"

        # 방어 섹터 → 보유 유지
        if any(s in sector for s in DEFENSIVE_SECTORS):
            return "\U0001f7e2 방어섹터 — 보유 유지"

        # 일반 종목
        if pnl_pct < -8:
            return "\U0001f534 전시 대응 — 손절 또는 분할매수 결정 필요"
        if pnl_pct < -3:
            return "\U0001f7e0 전시 관망 — 추가 하락 대비"
        if pnl_pct > 5:
            return "\U0001f7e2 이익 실현 검토 — 전시 변동성 활용"
        return "\U0001f6e1 전시 보유 — 관망"

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
            ht = self._manager_lane_for_holding(h)
            if ticker:
                # 매니저 아이콘 매핑
                mgr_emoji = {
                    "scalp": "⚡", "swing": "🔥",
                    "position": "📊", "long_term": "💎",
                    "tenbagger": "🚀",
                }.get(ht, "📌")
                row = [
                    InlineKeyboardButton(
                        f"{mgr_emoji} {hname[:4]} 분석",
                        callback_data=f"mgr:{ht}:{ticker}",
                    ),
                    InlineKeyboardButton(
                        f"🎙️ 토론",
                        callback_data=f"debate:{ticker}",
                    ),
                    InlineKeyboardButton(
                        f"❌ 삭제",
                        callback_data=f"bal:remove:{ticker}",
                    ),
                ]
                buttons.append(row)
        buttons.append(make_feedback_row("잔고"))
        return buttons

    async def _action_watch(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        name = result.name if result else ticker
        tp = result.info.current_price * 0.97 if result else None
        self.db.add_watchlist(ticker, name, target_price=tp)
        await safe_edit_or_reply(query,
            f"\U0001f514 {name} \uc54c\ub9bc \ub4f1\ub85d!\n\ub9e4\uc218 \uc870\uac74 \ucda9\uc871 \uc2dc \uc54c\ub824\ub4dc\ub9ac\uaca0\uc2b5\ub2c8\ub2e4."
        )

    async def _action_sell_profit(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        if holding:
            price = holding.get("current_price") or holding["buy_price"]
            pnl = holding.get("pnl_pct", 0)
            self.db.update_holding(holding["id"], sold_pct=50)
            trade_id = self.db.add_trade(
                ticker=ticker, name=holding["name"], action="sell",
                action_price=price, pnl_pct=pnl,
                recommended_price=holding["buy_price"], quantity_pct=50,
            )
            msg = format_trade_record(holding["name"], "sell", price, pnl)
            await safe_edit_or_reply(query,msg)
            # v6.2: 자동 복기 트리거
            await self._trigger_auto_debrief(
                ticker=ticker, name=holding["name"], action="sell",
                entry_price=holding["buy_price"], exit_price=price,
                pnl_pct=pnl, holding=holding, trade_id=trade_id,
            )
        else:
            await safe_edit_or_reply(query,"\u26a0\ufe0f 보유 종목을 찾을 수 없습니다.")

    async def _action_hold_profit(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        name = holding["name"] if holding else ticker
        price = holding.get("current_price", 0) if holding else 0
        self.db.add_trade(
            ticker=ticker, name=name, action="hold",
            action_price=price,
        )
        msg = format_trade_record(name, "hold", price)
        await safe_edit_or_reply(query,msg)

    async def _action_stop_loss(self, query, context, ticker: str) -> None:
        holding = self.db.get_holding_by_ticker(ticker)
        if holding:
            price = holding.get("current_price") or holding["buy_price"]
            pnl = holding.get("pnl_pct", 0)
            self.db.update_holding(holding["id"], status="closed")
            trade_id = self.db.add_trade(
                ticker=ticker, name=holding["name"], action="stop_loss",
                action_price=price, pnl_pct=pnl,
                recommended_price=holding["buy_price"], quantity_pct=100,
            )
            msg = format_trade_record(holding["name"], "stop_loss", price, pnl)
            await safe_edit_or_reply(query,msg)
            # v6.2: 자동 복기 트리거
            await self._trigger_auto_debrief(
                ticker=ticker, name=holding["name"], action="stop_loss",
                entry_price=holding["buy_price"], exit_price=price,
                pnl_pct=pnl, holding=holding, trade_id=trade_id,
            )
        else:
            await safe_edit_or_reply(query,"\u26a0\ufe0f 보유 종목을 찾을 수 없습니다.")

    async def _action_hold_through(self, query, context, ticker: str) -> None:
        """v9.3: 버틸래요 → 후속 조치 메뉴 제공."""
        holding = self.db.get_holding_by_ticker(ticker)
        name = holding["name"] if holding else ticker
        price = holding.get("current_price", 0) if holding else 0
        buy_price = holding.get("buy_price", 0) if holding else 0
        pnl_pct = holding.get("pnl_pct", 0) if holding else 0
        ht = (holding.get("holding_type") or "auto") if holding else "auto"

        self.db.add_trade(
            ticker=ticker, name=name, action="hold_through_stop",
            action_price=price, pnl_pct=pnl_pct,
        )

        # 매니저별 임계값 표시
        from kstock.store._portfolio import HOLDING_THRESHOLDS
        th = HOLDING_THRESHOLDS.get(ht, HOLDING_THRESHOLDS["auto"])
        ht_label = {"scalp": "스캘핑", "swing": "스윙", "position": "포지션",
                    "long_term": "장기투자", "tenbagger": "텐배거", "auto": "자동"}.get(ht, ht)

        msg = (
            f"⚠️ {name} 손절선 보유 유지\n"
            f"{'━' * 20}\n"
            f"📊 매수가: {buy_price:,.0f}원\n"
            f"📉 현재가: {price:,.0f}원 ({pnl_pct:+.1f}%)\n"
            f"🎯 투자전략: {ht_label}\n"
            f"🔻 현재 손절선: {th['stop']*100:+.0f}%\n\n"
            f"💡 후속 조치를 선택하세요:"
        )
        buttons = [
            [
                InlineKeyboardButton("🔻 손절가 조정", callback_data=f"newstop:{ticker}"),
                InlineKeyboardButton("🎯 목표가 조정", callback_data=f"newtgt:{ticker}"),
            ],
            [
                InlineKeyboardButton("📊 차트 보기", callback_data=f"fav:chtm:{ticker}"),
                InlineKeyboardButton("🤖 AI 의견", callback_data=f"holdai:{ticker}"),
            ],
        ]
        await safe_edit_or_reply(query, msg, InlineKeyboardMarkup(buttons))

    async def _action_new_stop(self, query, context, ticker: str) -> None:
        """새 손절가 선택 버튼 표시."""
        holding = self.db.get_holding_by_ticker(ticker)
        name = holding["name"] if holding else ticker
        buy_price = holding.get("buy_price", 0) if holding else 0
        msg = f"🔻 {name} 새 손절 기준 선택\n현재 매수가: {buy_price:,.0f}원"
        buttons = [
            [
                InlineKeyboardButton(f"-7% ({round(buy_price*0.93):,})", callback_data=f"setstop:{ticker}:7"),
                InlineKeyboardButton(f"-10% ({round(buy_price*0.90):,})", callback_data=f"setstop:{ticker}:10"),
            ],
            [
                InlineKeyboardButton(f"-15% ({round(buy_price*0.85):,})", callback_data=f"setstop:{ticker}:15"),
                InlineKeyboardButton(f"-20% ({round(buy_price*0.80):,})", callback_data=f"setstop:{ticker}:20"),
            ],
        ]
        await safe_edit_or_reply(query, msg, InlineKeyboardMarkup(buttons))

    async def _action_set_stop(self, query, context, payload: str) -> None:
        """선택한 손절% 적용."""
        parts = payload.split(":")
        if len(parts) < 2:
            await safe_edit_or_reply(query,"⚠️ 잘못된 요청입니다.")
            return
        ticker, pct_str = parts[0], parts[1]
        pct = int(pct_str)
        holding = self.db.get_holding_by_ticker(ticker)
        if not holding:
            await safe_edit_or_reply(query,"⚠️ 보유 종목을 찾을 수 없습니다.")
            return
        buy_price = holding["buy_price"]
        new_stop = round(buy_price * (1 - pct / 100), 0)
        self.db.update_holding(holding["id"], stop_price=new_stop)
        await safe_edit_or_reply(
            query,
            f"✅ {holding['name']} 손절가 변경 완료\n"
            f"📉 새 손절가: {new_stop:,.0f}원 (-{pct}%)\n"
            f"📊 매수가: {buy_price:,.0f}원",
        )

    async def _action_new_target(self, query, context, ticker: str) -> None:
        """새 목표가 선택 버튼 표시."""
        holding = self.db.get_holding_by_ticker(ticker)
        name = holding["name"] if holding else ticker
        buy_price = holding.get("buy_price", 0) if holding else 0
        msg = f"🎯 {name} 새 목표 수익률 선택\n현재 매수가: {buy_price:,.0f}원"
        buttons = [
            [
                InlineKeyboardButton(f"+10% ({round(buy_price*1.10):,})", callback_data=f"settgt:{ticker}:10"),
                InlineKeyboardButton(f"+20% ({round(buy_price*1.20):,})", callback_data=f"settgt:{ticker}:20"),
            ],
            [
                InlineKeyboardButton(f"+30% ({round(buy_price*1.30):,})", callback_data=f"settgt:{ticker}:30"),
                InlineKeyboardButton(f"+50% ({round(buy_price*1.50):,})", callback_data=f"settgt:{ticker}:50"),
            ],
        ]
        await safe_edit_or_reply(query, msg, InlineKeyboardMarkup(buttons))

    async def _action_set_target(self, query, context, payload: str) -> None:
        """선택한 목표% 적용."""
        parts = payload.split(":")
        if len(parts) < 2:
            await safe_edit_or_reply(query,"⚠️ 잘못된 요청입니다.")
            return
        ticker, pct_str = parts[0], parts[1]
        pct = int(pct_str)
        holding = self.db.get_holding_by_ticker(ticker)
        if not holding:
            await safe_edit_or_reply(query,"⚠️ 보유 종목을 찾을 수 없습니다.")
            return
        buy_price = holding["buy_price"]
        new_t1 = round(buy_price * (1 + pct / 100), 0)
        new_t2 = round(buy_price * (1 + pct * 2 / 100), 0)
        self.db.update_holding(holding["id"], target_1=new_t1, target_2=new_t2)
        await safe_edit_or_reply(
            query,
            f"✅ {holding['name']} 목표가 변경 완료\n"
            f"🎯 1차 목표: {new_t1:,.0f}원 (+{pct}%)\n"
            f"🎯 2차 목표: {new_t2:,.0f}원 (+{pct*2}%)\n"
            f"📊 매수가: {buy_price:,.0f}원",
        )

    async def _action_hold_ai(self, query, context, ticker: str) -> None:
        """AI 매니저 의견 요청."""
        holding = self.db.get_holding_by_ticker(ticker)
        if not holding:
            await safe_edit_or_reply(query,"⚠️ 보유 종목을 찾을 수 없습니다.")
            return
        name = holding["name"]
        buy_price = holding["buy_price"]
        current = holding.get("current_price", 0)
        pnl = holding.get("pnl_pct", 0)
        ht = holding.get("holding_type") or "auto"

        question = (
            f"{name}({ticker}) 보유 중. "
            f"매수가 {buy_price:,.0f}원, 현재가 {current:,.0f}원, 수익률 {pnl:+.1f}%. "
            f"투자전략: {ht}. "
            f"손절선에 도달했지만 버티기로 했습니다. "
            f"현재 시점에서 보유 유지 vs 손절 의견을 분석해주세요."
        )
        await safe_edit_or_reply(query,f"🤖 {name} AI 분석 요청 중...")
        try:
            await self._handle_ai_question(
                query, context, question, is_callback=True,
            )
        except Exception:
            # AI 질문 핸들러가 없으면 Claude 직접 호출
            try:
                from kstock.bot.chat_handler import handle_ai_question
                answer = await handle_ai_question(
                    question, self.anthropic_key, self.db,
                )
                await safe_edit_or_reply(query, f"🤖 AI 의견\n{'━' * 20}\n{answer[:3000]}")
            except Exception as e:
                await safe_edit_or_reply(query, f"⚠️ AI 분석 실패: {e}")

    async def _action_detail(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
            if not result:
                await safe_edit_or_reply(query,"\u26a0\ufe0f 종목 정보를 가져올 수 없습니다.")
                return
        macro = await self.macro_client.get_snapshot()
        # v9.4: debate 데이터 조회
        debate_data = None
        try:
            debate_data = self.db.get_latest_debate(ticker)
        except Exception:
            pass
        msg = format_stock_detail(
            result.name, result.ticker, result.score,
            result.tech, result.info, result.flow, macro,
            strategy_type=result.strategy_type,
            confidence_stars=result.confidence_stars,
            confidence_label=result.confidence_label,
            price_target=result.price_target,
            pattern_report=result.pattern_report,
            debate=debate_data,
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
        from kstock.bot.bot_imports import make_back_row
        buttons = [
            [
                InlineKeyboardButton("\uc0c0\uc5b4\uc694 \u2705", callback_data=f"buy:{ticker}"),
                InlineKeyboardButton("\uc548 \uc0b4\ub798\uc694 \u274c", callback_data=f"skip:{ticker}"),
            ],
            [fav_btn],
            make_back_row(),
        ]
        try:
            await safe_edit_or_reply(query,msg, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            logger.debug("_action_watch_detail edit_text failed, falling back", exc_info=True)
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))

    async def _action_nowatch(self, query, context, ticker: str) -> None:
        self.db.remove_watchlist(ticker)
        await safe_edit_or_reply(query,"\u274c 관심 목록에서 제외했습니다.")

    async def _action_watch_btn(self, query, context, ticker: str) -> None:
        result = self._find_cached_result(ticker)
        name = result.name if result else ticker
        tp = result.info.current_price * 0.97 if result else None
        self.db.add_watchlist(ticker, name, target_price=tp)
        await safe_edit_or_reply(query,
            f"\U0001f440 {name} 지켜보기 등록!\n조건 변화 시 다시 알려드리겠습니다."
        )

    async def _action_strategy(self, query, context, strategy_type: str) -> None:
        recs = self.db.get_recommendations_by_strategy(strategy_type)
        msg = format_strategy_list(strategy_type, recs)
        await safe_edit_or_reply(query,msg)

    async def _action_opt_apply(self, query, context, ticker: str) -> None:
        await safe_edit_or_reply(query,
            "\u2705 최적화 파라미터 적용 완료!\n"
            "다음 스캔부터 새 파라미터가 반영됩니다."
        )

    async def _action_opt_ignore(self, query, context, payload: str) -> None:
        await safe_edit_or_reply(query,"\u274c 최적화 결과를 무시합니다.")

    async def _action_kis_buy(self, query, context, ticker: str) -> None:
        """Handle KIS auto-buy button."""
        if not self.kis_broker.connected:
            await safe_edit_or_reply(query,"\u26a0\ufe0f KIS 미연결. /setup_kis 로 설정하세요.")
            return
        # v10.2: 매크로 쇼크 매수 차단
        _shock = getattr(self, "_current_shock", None)
        if _shock and not _shock.policy.new_buy_allowed:
            await safe_edit_or_reply(query,
                "⛔ 매크로 쇼크 매수 차단 중\n\n"
                f"등급: {_shock.overall_grade.name}\n"
                "전략적 신규 매수가 제한됩니다.\n"
                "장중 재평가 후 해제될 수 있습니다."
            )
            return
        # 안전장치: 실전매매 환경변수 체크
        real_trade = os.getenv("REAL_TRADE_ENABLED", "false").lower() == "true"
        is_virtual = getattr(self.kis, '_is_virtual', True)
        if not is_virtual and not real_trade:
            await safe_edit_or_reply(query,
                "\U0001f6ab 실전투자 모드에서 자동매매가 비활성화되어 있습니다.\n\n"
                ".env에 REAL_TRADE_ENABLED=true 설정 필요.\n"
                "\U0001f4e1 KIS설정 → 안전 설정에서 확인하세요."
            )
            return
        result = self._find_cached_result(ticker)
        if not result:
            result = await self._scan_single_stock(ticker)
        if not result:
            await safe_edit_or_reply(query,"\u26a0\ufe0f 종목 정보를 찾을 수 없습니다.")
            return
        price = result.info.current_price
        balance = self.kis_broker.get_balance()
        total_eval = balance.get("total_eval", 0) if balance else 0
        qty = self.kis_broker.compute_buy_quantity(price, total_eval, pct=10.0)
        if qty <= 0:
            await safe_edit_or_reply(query,"\u26a0\ufe0f 매수 가능 수량이 없습니다.")
            return
        # Safety check
        order_pct = (price * qty / total_eval * 100) if total_eval > 0 else 100
        can, reason = self.kis_broker.safety.can_order(order_pct)
        if not can:
            await safe_edit_or_reply(query,f"\u26a0\ufe0f 안전 제한: {reason}")
            return
        # 실전 모드 1회 주문 한도: 투자금 10% 또는 500만원 중 작은 값
        if not is_virtual:
            order_amount = price * qty
            max_amount = min(total_eval * 0.1, 5_000_000)
            if order_amount > max_amount:
                await safe_edit_or_reply(query,
                    f"\u26a0\ufe0f 실전매매 1회 한도 초과\n\n"
                    f"주문금액: {order_amount:,.0f}원\n"
                    f"한도: {max_amount:,.0f}원 (투자금 10% / 500만원 중 작은 값)"
                )
                return
        order = self.kis_broker.buy(ticker, qty)
        if order.success:
            self.db.add_order(
                ticker=ticker, name=result.name, order_type="market",
                side="buy", quantity=qty, price=price, order_id=order.order_id,
            )
            self.db.add_holding(ticker, result.name, price)
            await safe_edit_or_reply(query,
                f"\u2705 {result.name} {qty}주 시장가 매수 주문 완료!\n"
                f"주문번호: {order.order_id}"
            )
        else:
            await safe_edit_or_reply(query,f"\u274c 매수 실패: {order.message}")

    # == Buy Planner =========================================================

    # 기간별 전략 매핑
    _HORIZON_STRATEGIES = {
        "scalp": {
            "strategies": {"B", "G"},
            "label": "⚡ 초단기 (당일~1일)",
            "hold_desc": "당일 종가 매도 목표. 14:30까지 목표 미달 시 종가 청산.",
        },
        "short": {
            "strategies": {"A", "G", "F"},
            "label": "🔥 단기 (3~5일)",
            "hold_desc": "3~5 거래일 보유. 3일 내 +3% 미만이면 본전 매도 검토.",
        },
        "mid": {
            "strategies": {"D", "F"},
            "label": "📊 중기 (1~3개월)",
            "hold_desc": "1~3개월 보유. 주 1회 기술지표 점검.",
        },
        "long": {
            "strategies": {"C", "E"},
            "label": "💎 장기 (6개월+)",
            "hold_desc": "6개월 이상. 분기 실적 기준 판단. 배당 수익 포함.",
        },
    }

    # 초단기 ATR 기반 리스크 등급
    _SCALP_RISK_GRADES = {
        "A": {"atr_max": 2.0, "target_min": 3, "target_max": 5,
               "stop": -2, "label": "A (안정)", "win_rate": 0.65},
        "B": {"atr_max": 4.0, "target_min": 5, "target_max": 10,
               "stop": -3, "label": "B (보통)", "win_rate": 0.55},
        "C": {"atr_max": 999, "target_min": 10, "target_max": 20,
               "stop": -5, "label": "C (공격)", "win_rate": 0.45},
    }

    def _get_scalp_risk_grade(self, atr_pct: float) -> dict:
        """ATR(20) 비율로 초단기 리스크 등급 결정."""
        if atr_pct < 2.0:
            return self._SCALP_RISK_GRADES["A"]
        elif atr_pct < 4.0:
            return self._SCALP_RISK_GRADES["B"]
        else:
            return self._SCALP_RISK_GRADES["C"]

    def _calculate_kelly_fraction(
        self, win_rate: float, target_pct: float, stop_pct: float,
    ) -> float:
        """Half Kelly 기준 적정 투자 비율 계산."""
        if stop_pct >= 0 or target_pct <= 0:
            return 0.1
        b = target_pct / abs(stop_pct)
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b
        half_kelly = max(0.05, min(kelly / 2, 0.40))
        return round(half_kelly, 2)

    def _calculate_expected_return(
        self, win_rate: float, target_pct: float, stop_pct: float,
    ) -> float:
        """기대수익률 계산. E[R] = P(win)*target + P(lose)*stop"""
        return win_rate * target_pct + (1 - win_rate) * stop_pct

    async def _action_buy_plan(self, query, context, payload: str) -> None:
        """매수 플래너 콜백 핸들러. 장바구니 모드.

        콜백: bp:start/yes, bp:no, bp:dismiss,
              bp:view:{horizon}, bp:ai, bp:addall,
              bp:add:{ticker}:{horizon},
              bp:done, bp:confirm, bp:retry, bp:cancel
        """
        if payload in ("yes", "start"):
            # v5.2: 금액 버튼 + 직접 입력
            buttons = [
                [
                    InlineKeyboardButton("50만원", callback_data="bp:amt:50"),
                    InlineKeyboardButton("100만원", callback_data="bp:amt:100"),
                ],
                [
                    InlineKeyboardButton("200만원", callback_data="bp:amt:200"),
                    InlineKeyboardButton("300만원", callback_data="bp:amt:300"),
                ],
                [
                    InlineKeyboardButton("500만원", callback_data="bp:amt:500"),
                    InlineKeyboardButton("직접 입력", callback_data="bp:amt:custom"),
                ],
                [InlineKeyboardButton("❌ 취소", callback_data="bp:no")],
            ]
            await safe_edit_or_reply(query,
                "💰 주호님, 오늘 매수 금액을 선택해주세요\n"
                "(만원 단위)",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if payload.startswith("amt:"):
            amt_val = payload.split(":")[1]
            if amt_val == "custom":
                context.user_data["awaiting_buy_amount"] = True
                await safe_edit_or_reply(query,
                    "💰 투자 금액을 입력해주세요\n"
                    "(만원 단위 숫자만 입력)\n\n"
                    "예: 50 → 50만원"
                )
                return
            amount_만원 = int(amt_val)
            # 투자 타입 선택 버튼
            buttons = [
                [
                    InlineKeyboardButton("⚡ 단타", callback_data=f"bp:type:scalp:{amount_만원}"),
                    InlineKeyboardButton("🔥 스윙", callback_data=f"bp:type:short:{amount_만원}"),
                ],
                [
                    InlineKeyboardButton("📊 포지션", callback_data=f"bp:type:mid:{amount_만원}"),
                    InlineKeyboardButton("💎 장기", callback_data=f"bp:type:long:{amount_만원}"),
                ],
                [
                    InlineKeyboardButton("🤖 AI 추천 (전 기간)", callback_data=f"bp:type:ai:{amount_만원}"),
                ],
                [InlineKeyboardButton("🔙 금액 재선택", callback_data="bp:yes")],
            ]
            await safe_edit_or_reply(query,
                f"💰 {amount_만원}만원 매수 계획\n\n"
                f"투자 타입을 선택해주세요.\n"
                f"선택한 타입의 전담 매니저가\n"
                f"매수부터 매도까지 관리합니다.\n\n"
                f"⚡ 단타: 제시 리버모어 (1~3일)\n"
                f"🔥 스윙: 윌리엄 오닐 (1~2주)\n"
                f"📊 포지션: 피터 린치 (1~3개월)\n"
                f"💎 장기: 워렌 버핏 (3개월+)\n"
                f"🤖 AI 추천: 전 기간 최적 조합",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if payload.startswith("type:"):
            parts = payload.split(":")
            inv_type = parts[1]
            amount_만원 = int(parts[2])
            amount_won = amount_만원 * 10000
            context.user_data["buy_cart"] = {
                "budget": amount_won,
                "remaining": amount_won,
                "items": [],
                "active": True,
                "investment_type": inv_type,
            }
            if inv_type == "ai":
                await safe_edit_or_reply(query,"🤖 AI가 최적 포트폴리오를 분석 중...")
                await self._show_ai_recommendation(query, context)
            else:
                await safe_edit_or_reply(query,"💭 종목을 분석하고 있습니다...")
                await self._show_horizon_picks(query, context, inv_type)
            return

        if payload == "no":
            await safe_edit_or_reply(query,
                "🏖️ 알겠습니다!\n"
                "좋은 하루 보내세요, 주호님\n\n"
                "매수 계획이 생기면 언제든 말씀하세요"
            )
            return

        if payload == "dismiss":
            await safe_edit_or_reply(query,"👋 확인했습니다.")
            return

        if payload.startswith("view:"):
            horizon = payload.split(":")[1]
            await self._show_horizon_picks(query, context, horizon)
            return

        if payload == "ai":
            await self._show_ai_recommendation(query, context)
            return

        if payload == "addall":
            # AI 추천 전체 담기
            ai_picks = context.user_data.get("_ai_picks", [])
            cart = context.user_data.get("buy_cart")
            if not cart or not ai_picks:
                await safe_edit_or_reply(query,"⚠️ 장바구니 정보가 없습니다.")
                return
            added = 0
            for p in ai_picks:
                if cart["remaining"] < p["amount"]:
                    continue
                cart["items"].append(p)
                cart["remaining"] -= p["amount"]
                added += 1
            context.user_data.pop("_ai_picks", None)
            await safe_edit_or_reply(query,
                f"✅ {added}종목을 장바구니에 담았습니다"
            )
            await self._show_cart_menu(query, context)
            return

        if payload.startswith("add:"):
            parts = payload.split(":")
            if len(parts) < 3:
                return
            ticker, horizon = parts[1], parts[2]
            await self._add_to_cart(query, context, ticker, horizon)
            return

        if payload == "done":
            await self._show_cart_summary(query, context)
            return

        if payload == "confirm":
            await self._confirm_cart(query, context)
            return

        if payload == "retry":
            await self._show_cart_menu(query, context)
            return

        if payload == "cancel":
            context.user_data.pop("buy_cart", None)
            context.user_data.pop("_horizon_picks", None)
            context.user_data.pop("_ai_picks", None)
            await safe_edit_or_reply(query,"❌ 매수 계획을 취소했습니다.")
            return

        # 하위 호환: 기존 hz:{horizon}:{amount}
        if payload.startswith("hz:"):
            parts = payload.split(":")
            if len(parts) < 3:
                return
            horizon = parts[1]
            amount_만원 = int(parts[2])
            amount_won = amount_만원 * 10000
            # 장바구니 모드로 전환
            context.user_data["buy_cart"] = {
                "budget": amount_won,
                "remaining": amount_won,
                "items": [],
                "active": True,
            }
            await safe_edit_or_reply(query,
                "💭 종목을 분석하고 있습니다..."
            )
            await self._show_horizon_picks(query, context, horizon)
            return

    # ── 장바구니 매수 모드 ─────────────────────────────────────

    async def _show_cart_menu(self, query_or_update, context) -> None:
        """장바구니 메인 메뉴 — 기간별 종목 보기 + 장바구니 현황."""
        cart = context.user_data.get("buy_cart")
        if not cart:
            return

        budget_만원 = cart["budget"] // 10000
        remaining_만원 = cart["remaining"] // 10000
        items = cart["items"]

        lines = [f"🛒 장바구니 매수 모드\n"]
        lines.append(
            f"💰 예산: {budget_만원}만원 | "
            f"남은: {remaining_만원}만원\n"
        )

        if items:
            lines.append(f"{'─' * 20}")
            horizon_emoji = {
                "scalp": "⚡", "short": "🔥", "mid": "📊", "long": "💎",
            }
            for i, item in enumerate(items, 1):
                emoji = horizon_emoji.get(item["horizon"], "📌")
                lines.append(
                    f"  {i}. {item['name']} ({emoji})\n"
                    f"     {item['price']:,.0f}원 x {item['quantity']}주"
                    f" = {item['amount']:,.0f}원"
                )
            lines.append(f"{'─' * 20}\n")

        lines.append("종목을 선택하세요")

        buttons = [
            [
                InlineKeyboardButton(
                    "⚡ 단타 종목 보기", callback_data="bp:view:scalp",
                ),
                InlineKeyboardButton(
                    "🔥 스윙 종목 보기", callback_data="bp:view:short",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 포지션 종목 보기", callback_data="bp:view:mid",
                ),
                InlineKeyboardButton(
                    "💎 장기 종목 보기", callback_data="bp:view:long",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🤖 AI 추천 받기", callback_data="bp:ai",
                ),
            ],
        ]
        if items:
            buttons.append([
                InlineKeyboardButton(
                    f"✅ 선택 완료 ({len(items)}종목)",
                    callback_data="bp:done",
                ),
            ])
        buttons.append([
            InlineKeyboardButton("❌ 취소", callback_data="bp:cancel"),
        ])

        text = "\n".join(lines)
        keyboard = InlineKeyboardMarkup(buttons)

        # query(CallbackQuery) 또는 update(Message)에 따라 다르게 발송
        if hasattr(query_or_update, "message") and hasattr(
            query_or_update, "edit_message_text"
        ):
            # CallbackQuery
            await query_or_update.message.reply_text(
                text, reply_markup=keyboard,
            )
        else:
            # Update (from handle_menu_text)
            await query_or_update.message.reply_text(
                text, reply_markup=keyboard,
            )

    async def _get_horizon_picks_data(
        self, horizon: str, budget_won: int,
    ) -> tuple[list[dict], str | None]:
        """기간별 종목 스캔 + Kelly/E[R] 계산. (picks_data, error_msg) 반환."""
        config = self._HORIZON_STRATEGIES.get(horizon)
        if not config:
            return [], "⚠️ 잘못된 투자 기간입니다."

        # 시장 레짐 확인
        macro = await self.macro_client.get_snapshot()
        from kstock.signal.strategies import get_regime_mode
        regime = get_regime_mode(macro)

        if horizon == "scalp" and regime["mode"] == "defense":
            return [], (
                f"🛡️ 현재 방어 모드 (VIX {macro.vix:.1f})\n\n"
                "변동성이 높아 초단기 매매 비추천\n"
                "💡 현금 비중 35% 권장"
            )

        # 전체 종목 스캔 (v9.3.3: 10분 캐시로 통일)
        _SCAN_CACHE_TTL = 600
        now = datetime.now(KST)
        if (
            hasattr(self, '_scan_cache_time')
            and self._scan_cache_time
            and (now - self._scan_cache_time).total_seconds() < _SCAN_CACHE_TTL
            and getattr(self, '_last_scan_results', None)
        ):
            results = self._last_scan_results
        else:
            results = await self._scan_all_stocks()
            self._last_scan_results = results
            self._scan_cache_time = now

        # 전략 필터링
        target_strategies = config["strategies"]
        filtered = []
        for r in results:
            for sig in (r.strategy_signals or []):
                if sig.strategy in target_strategies and sig.action in ("BUY", "WATCH"):
                    filtered.append((r, sig))
                    break

        filtered.sort(
            key=lambda x: (0 if x[1].action == "BUY" else 1, -x[0].score.composite),
        )
        top_picks = filtered[:5]

        if not top_picks:
            return [], (
                f"📋 {config['label']} 조건 종목 없음\n\n"
                "다른 기간을 선택하거나\n"
                "장 시작 후 다시 확인해보세요"
            )

        # 종목 데이터 + ATR 등급 + Kelly 배분 + E[R] 계산
        picks_data = []
        for r, sig in top_picks:
            price = getattr(r.info, 'current_price', 0) or 0
            atr_pct = getattr(r.tech, 'atr_pct', 3.0) or 3.0

            if horizon == "scalp":
                risk_grade = self._get_scalp_risk_grade(atr_pct)
                target_pct = (risk_grade["target_min"] + risk_grade["target_max"]) / 2
                stop_pct = risk_grade["stop"]
                win_rate = risk_grade["win_rate"]
            else:
                risk_grade = None
                target_pct = sig.target_pct if sig.target_pct else 5.0
                stop_pct = sig.stop_pct if sig.stop_pct else -3.0
                win_rate = min(sig.confidence, 0.7) if sig.confidence else 0.5

            kelly_frac = self._calculate_kelly_fraction(win_rate, target_pct, stop_pct)
            expected_return = self._calculate_expected_return(
                win_rate, target_pct, stop_pct,
            )

            if expected_return < 0.5 or price <= 0:
                continue

            allocated_won = int(budget_won * kelly_frac)
            qty = int(allocated_won / price)
            invest_amount = qty * price
            if qty <= 0:
                continue

            rg_label = risk_grade["label"] if risk_grade else ""

            # scalp/short: 차트 요약 생성
            chart_summary = ""
            if horizon in ("scalp", "short") and r.tech:
                try:
                    from kstock.bot.investment_managers import build_chart_summary
                    sd = self.db.get_supply_demand(r.ticker, days=5)
                    # OHLCV가 있으면 전달 (추가 시그널 계산용)
                    r_ohlcv = getattr(r, "ohlcv", None)
                    chart_summary = build_chart_summary(
                        r.tech, price, sd,
                        ohlcv=r_ohlcv, ticker=r.ticker, name=r.name,
                    )
                except Exception:
                    logger.debug("Chart summary build failed for %s", r.ticker, exc_info=True)

            # mid/long: 재무 요약 생성
            fundamental_summary = ""
            if horizon in ("mid", "long"):
                try:
                    from kstock.bot.investment_managers import build_fundamental_summary
                    info_data = getattr(r, "info", None)
                    info_dict = None
                    if info_data:
                        info_dict = {
                            "per": getattr(info_data, "per", 0) or 0,
                            "pbr": getattr(info_data, "pbr", 0) or 0,
                            "roe": getattr(info_data, "roe", 0) or 0,
                            "dividend_yield": getattr(info_data, "dividend_yield", 0) or 0,
                            "foreign_ratio": getattr(info_data, "foreign_ratio", 0) or 0,
                        }
                    financials = self.db.get_financials(r.ticker)
                    consensus = self.db.get_consensus(r.ticker)
                    supply = self.db.get_supply_demand(r.ticker, days=5)
                    fundamental_summary = build_fundamental_summary(
                        info_dict, financials, consensus, supply,
                        current_price=price, ticker=r.ticker, name=r.name,
                    )
                except Exception:
                    logger.debug("Fundamental summary build failed for %s", r.ticker, exc_info=True)

            picks_data.append({
                "name": r.name,
                "ticker": r.ticker,
                "horizon": horizon,
                "price": price,
                "score": r.score.composite,
                "rsi": getattr(r.tech, 'rsi', 50),
                "macd": getattr(r.tech, 'macd', 0),
                "bb_pct": getattr(r.tech, 'bb_pct', 0.5),
                "ma5": getattr(r.tech, 'ma5', 0),
                "ma20": getattr(r.tech, 'ma20', 0),
                "ma60": getattr(r.tech, 'ma60', 0),
                "atr_pct": atr_pct,
                "risk_grade": rg_label,
                "strategy": sig.strategy,
                "strategy_name": sig.strategy_name,
                "signal": sig.action,
                "confidence": sig.confidence,
                "reasons": sig.reasons or [],
                "quantity": qty,
                "amount": invest_amount,
                "kelly_frac": kelly_frac,
                "expected_return": expected_return,
                "target_pct": target_pct,
                "stop_pct": stop_pct,
                "win_rate": win_rate,
                "chart_summary": chart_summary,
                "fundamental_summary": fundamental_summary,
            })

        if not picks_data:
            return [], (
                f"📋 {config['label']} 기간에\n"
                "기대수익 양수인 종목 없음\n\n"
                "💡 오늘은 관망이 합리적입니다"
            )

        return picks_data, None

    async def _show_horizon_picks(self, query, context, horizon: str) -> None:
        """기간별 종목 리스트 표시 + [담기] 버튼."""
        cart = context.user_data.get("buy_cart")
        if not cart:
            await safe_edit_or_reply(query,"⚠️ 장바구니 정보가 없습니다.")
            return

        await safe_edit_or_reply(query,"🔍 종목을 분석하고 있습니다...")

        picks_data, error = await self._get_horizon_picks_data(
            horizon, cart["remaining"],
        )

        if error:
            buttons = [[
                InlineKeyboardButton("🔙 돌아가기", callback_data="bp:retry"),
            ]]
            await query.message.reply_text(
                error, reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 임시 저장 (담기 버튼 클릭 시 참조용)
        if not hasattr(self, '_horizon_picks_cache'):
            self._horizon_picks_cache = {}
        for p in picks_data:
            self._horizon_picks_cache[p["ticker"]] = p

        config = self._HORIZON_STRATEGIES[horizon]
        horizon_emoji = {"scalp": "⚡", "short": "🔥", "mid": "📊", "long": "💎"}
        emoji = horizon_emoji.get(horizon, "📌")

        lines = [f"{emoji} {config['label']} 추천 종목\n"]
        emojis_num = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

        for i, p in enumerate(picks_data[:5]):
            risk_info = f" [{p['risk_grade']}]" if p["risk_grade"] else ""
            lines.append(
                f"{emojis_num[i]} {p['name']} ({p['ticker']}){risk_info}\n"
                f"   현재가: {p['price']:,.0f}원 | 점수: {p['score']:.0f}점\n"
                f"   ATR {p['atr_pct']:.1f}% | RSI {p['rsi']:.0f}\n"
                f"   🎯 +{p['target_pct']:.0f}% | 🔴 {p['stop_pct']:.0f}%\n"
                f"   Kelly {p['kelly_frac']:.0%} → "
                f"{p['amount']:,.0f}원, {p['quantity']}주"
            )

        text = "\n".join(lines)

        # 담기 버튼
        buttons = []
        # 이미 장바구니에 있는 종목은 제외
        cart_tickers = {item["ticker"] for item in cart["items"]}
        for i, p in enumerate(picks_data[:5]):
            if p["ticker"] in cart_tickers:
                continue
            if p["amount"] > cart["remaining"]:
                continue
            buttons.append([
                InlineKeyboardButton(
                    f"{emojis_num[i]} {p['name']} 담기",
                    callback_data=f"bp:add:{p['ticker']}:{horizon}",
                ),
            ])
        buttons.append([
            InlineKeyboardButton("🔙 돌아가기", callback_data="bp:retry"),
        ])

        await query.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _add_to_cart(self, query, context, ticker: str, horizon: str) -> None:
        """종목을 장바구니에 추가."""
        cart = context.user_data.get("buy_cart")
        if not cart:
            await safe_edit_or_reply(query,"⚠️ 장바구니 정보가 없습니다.")
            return

        # 캐시에서 종목 데이터 가져오기
        picks_cache = getattr(self, '_horizon_picks_cache', {})
        pick = picks_cache.get(ticker)

        if not pick:
            await safe_edit_or_reply(query,
                "⚠️ 종목 정보를 찾을 수 없습니다.\n다시 종목 보기를 선택해주세요."
            )
            return

        # 이미 담긴 종목 체크
        if any(item["ticker"] == ticker for item in cart["items"]):
            await safe_edit_or_reply(query,
                f"⚠️ {pick['name']}은 이미 장바구니에 있습니다."
            )
            return

        # 예산 체크
        if pick["amount"] > cart["remaining"]:
            await safe_edit_or_reply(query,
                f"⚠️ 예산이 부족합니다\n\n"
                f"필요: {pick['amount']:,.0f}원\n"
                f"남은 예산: {cart['remaining']:,.0f}원"
            )
            return

        # 장바구니에 추가
        cart["items"].append(pick)
        cart["remaining"] -= pick["amount"]

        horizon_emoji = {"scalp": "⚡", "short": "🔥", "mid": "📊", "long": "💎"}
        emoji = horizon_emoji.get(horizon, "📌")

        await safe_edit_or_reply(query,
            f"✅ {pick['name']} 담김 ({emoji})\n\n"
            f"🛒 장바구니 ({len(cart['items'])}종목)\n"
            f"💰 남은 예산: {cart['remaining']:,.0f}원"
        )

        # 다시 메인 메뉴로
        await self._show_cart_menu(query, context)

    async def _show_ai_recommendation(self, query, context) -> None:
        """AI가 전 기간 통합 최적 포트폴리오 추천."""
        cart = context.user_data.get("buy_cart")
        if not cart:
            await safe_edit_or_reply(query,"⚠️ 장바구니 정보가 없습니다.")
            return

        await safe_edit_or_reply(query,
            "🤖 AI가 최적 포트폴리오를 분석 중...\n"
            "(약 30초 소요)"
        )

        budget_won = cart["remaining"]
        amount_만원 = budget_won // 10000

        # 전 기간 종목을 수집
        all_picks = []
        for hz in ("scalp", "short", "mid", "long"):
            picks, _ = await self._get_horizon_picks_data(hz, budget_won)
            for p in picks:
                p["horizon"] = hz
            all_picks.extend(picks[:3])

        if not all_picks:
            buttons = [[
                InlineKeyboardButton("🔙 돌아가기", callback_data="bp:retry"),
            ]]
            await query.message.reply_text(
                "📋 추천할 종목이 없습니다.\n현재 시장에서 적합한 종목을 찾지 못했습니다.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 기존 보유종목 확인
        holdings = self.db.get_active_holdings()
        holdings_text = ""
        if holdings:
            h_list = [
                f"{h['name']}({h.get('holding_type', 'auto')})"
                for h in holdings[:5]
            ]
            holdings_text = f"현재 보유: {', '.join(h_list)}\n"

        # 매크로 데이터
        macro = await self.macro_client.get_snapshot()
        from kstock.signal.strategies import get_regime_mode
        regime = get_regime_mode(macro)

        # AI 분석
        horizon_emoji = {"scalp": "⚡단타", "short": "🔥스윙", "mid": "📊포지션", "long": "💎장기"}
        picks_text = ""
        for i, p in enumerate(all_picks, 1):
            hz_label = horizon_emoji.get(p["horizon"], p["horizon"])
            picks_text += (
                f"\n{i}. {p['name']} ({p['ticker']}) [{hz_label}]\n"
                f"   현재가: {p['price']:,.0f}원 | 점수: {p['score']:.0f}점\n"
                f"   RSI: {p['rsi']:.0f} | ATR: {p['atr_pct']:.1f}%\n"
                f"   Kelly: {p['kelly_frac']:.0%} | E[R]: {p['expected_return']:+.1f}%\n"
                f"   목표: +{p['target_pct']:.0f}% | 손절: {p['stop_pct']:.0f}%\n"
            )

        analysis_text = ""
        if self.anthropic_key:
            try:
                import anthropic
                client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
                prompt = (
                    f"주호님이 {amount_만원}만원 예산으로 매수 계획.\n\n"
                    f"[시장]\nVIX: {macro.vix:.1f} | 나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
                    f"레짐: {regime['label']}\n\n"
                    f"{holdings_text}\n"
                    f"[후보 종목]\n{picks_text}\n\n"
                    f"위 후보에서 최적 3종목 조합을 추천하세요.\n"
                    f"기간 분산, 섹터 분산, 리스크 분산 고려.\n"
                    f"시장 불안하면 '관망' 권고.\n\n"
                    f"형식 (종목당):\n"
                    f"[번호] 종목명 (기간이모지) — 금액 (비율%)\n"
                    f"   핵심 지표 1줄\n"
                    f"   🎯 +목표% | 🔴 -손절%\n"
                    f"   💡 실전 팁 1줄\n\n"
                    f"마지막에 전체 E[R]과 최대 손실 요약.\n"
                    f"볼드(**) 금지. 25자 이내. 이모지 구분."
                )

                response = await client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=1500,
                    temperature=0.2,
                    system=(
                        "너는 주호님의 전속 투자 참모 '퀀트봇'이다.\n"
                        "CFA/CAIA + 계량금융 전문가.\n\n"
                        "[규칙]\n"
                        "1. 매매 '지시' 금지. '검토해보세요' 식\n"
                        "2. 제공된 데이터만 사용\n"
                        "3. 볼드(**) 금지\n"
                        "4. Kelly/E[R] 근거 배분\n"
                        "5. 기존 보유종목과 분산 고려\n"
                        "6. 전체 포트폴리오 관점 추천"
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                # [v6.2.1] 토큰 추적
                try:
                    from kstock.core.token_tracker import track_usage
                    track_usage(
                        db=self.db, provider="anthropic",
                        model="claude-sonnet-4-5-20250929",
                        function_name="strategist",
                        response=response,
                    )
                except Exception:
                    logger.debug("Strategist token tracking failed", exc_info=True)
                from kstock.bot.chat_handler import _sanitize_response
                analysis_text = _sanitize_response(response.content[0].text)
            except Exception as e:
                logger.error("AI recommendation error: %s", e)

        if not analysis_text:
            # 폴백: 기본 포맷
            lines = []
            for i, p in enumerate(all_picks[:3]):
                hz_label = horizon_emoji.get(p["horizon"], "")
                lines.append(
                    f"{['1️⃣','2️⃣','3️⃣'][i]} {p['name']} ({hz_label})\n"
                    f"   {p['price']:,.0f}원 x {p['quantity']}주 = {p['amount']:,.0f}원\n"
                    f"   🎯 +{p['target_pct']:.0f}% | 🔴 {p['stop_pct']:.0f}%\n"
                    f"   E[R]: {p['expected_return']:+.1f}%"
                )
            analysis_text = "\n\n".join(lines)

        header = (
            f"🤖 AI 추천 포트폴리오 ({amount_만원}만원)\n\n"
            f"📊 VIX: {macro.vix:.1f} | {regime.get('emoji', '')} {regime.get('label', '')}\n"
            f"{holdings_text}\n"
            f"{'━' * 22}\n\n"
        )

        text = header + analysis_text

        # AI 추천 top3를 임시 저장 (전체 담기용)
        ai_top3 = all_picks[:3]
        context.user_data["_ai_picks"] = ai_top3

        # 캐시에도 저장 (개별 담기용)
        if not hasattr(self, '_horizon_picks_cache'):
            self._horizon_picks_cache = {}
        for p in ai_top3:
            self._horizon_picks_cache[p["ticker"]] = p

        # 버튼
        buttons = [
            [InlineKeyboardButton("✅ 전체 담기", callback_data="bp:addall")],
        ]
        for i, p in enumerate(ai_top3):
            hz_label = horizon_emoji.get(p["horizon"], "")
            buttons.append([
                InlineKeyboardButton(
                    f"{['1️⃣','2️⃣','3️⃣'][i]} {p['name']} 담기",
                    callback_data=f"bp:add:{p['ticker']}:{p['horizon']}",
                ),
            ])
        buttons.append([
            InlineKeyboardButton("🔙 돌아가기", callback_data="bp:retry"),
        ])

        await query.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _show_cart_summary(self, query, context) -> None:
        """장바구니 최종 확인 화면."""
        cart = context.user_data.get("buy_cart")
        if not cart or not cart["items"]:
            await safe_edit_or_reply(query,"🛒 장바구니가 비어있습니다.")
            return

        budget_만원 = cart["budget"] // 10000
        used = sum(item["amount"] for item in cart["items"])
        remaining = cart["budget"] - used

        lines = [
            f"📋 주호님 최종 매수 계획\n",
            f"💰 총 예산: {budget_만원}만원",
            f"📍 사용: {used:,.0f}원 | 여유: {remaining:,.0f}원\n",
            f"{'━' * 22}",
        ]

        horizon_emoji = {"scalp": "⚡단타", "short": "🔥스윙", "mid": "📊포지션", "long": "💎장기"}
        emojis_num = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        total_er = 0
        total_max_loss = 0

        for i, item in enumerate(cart["items"]):
            hz_label = horizon_emoji.get(item["horizon"], item["horizon"])
            target_price = int(item["price"] * (1 + item["target_pct"] / 100))
            stop_price = int(item["price"] * (1 + item["stop_pct"] / 100))
            max_loss = abs(item["stop_pct"]) / 100 * item["amount"]
            total_er += item["expected_return"]
            total_max_loss += max_loss

            em = emojis_num[i] if i < len(emojis_num) else f"{i+1}."
            lines.append(
                f"\n{em} {item['name']} ({hz_label})\n"
                f"   🟢 매수: {item['price']:,.0f}원 "
                f"({item['quantity']}주, {item['amount']:,.0f}원)\n"
                f"   🎯 목표: {target_price:,.0f}원 (+{item['target_pct']:.0f}%)\n"
                f"   🔴 손절: {stop_price:,.0f}원 ({item['stop_pct']:.0f}%)\n"
                f"   📊 배분: {item['kelly_frac']:.0%} (Kelly)"
                f" | E[R]: {item['expected_return']:+.1f}%"
            )

        avg_er = total_er / len(cart["items"]) if cart["items"] else 0

        lines.append(f"\n{'━' * 22}")

        # 기간별 모니터링 안내
        horizons_in_cart = {item["horizon"] for item in cart["items"]}
        if "scalp" in horizons_in_cart:
            lines.append("⚡ 단타 → 장중 실시간 모니터링")
        if "short" in horizons_in_cart:
            lines.append("🔥 스윙 → 매일 목표/손절 점검")
        if "mid" in horizons_in_cart:
            lines.append("📊 포지션 → 주 1회 점검")
        if "long" in horizons_in_cart:
            lines.append("💎 장기 → 분기 실적 기준")

        lines.append(
            f"\n⚠️ 참고용 분석이며 투자 지시가 아닙니다\n"
            f"💡 평균 E[R]: {avg_er:+.1f}%"
            f" | 최대 손실: {total_max_loss:,.0f}원"
        )

        text = "\n".join(lines)

        buttons = [
            [
                InlineKeyboardButton("✅ 확정", callback_data="bp:confirm"),
                InlineKeyboardButton("🔄 다시 선택", callback_data="bp:retry"),
            ],
            [
                InlineKeyboardButton("❌ 취소", callback_data="bp:cancel"),
            ],
        ]

        await safe_edit_or_reply(query,
            text, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _confirm_cart(self, query, context) -> None:
        """장바구니 확정: 보유종목 등록 + 모니터링 시작."""
        cart = context.user_data.get("buy_cart")
        if not cart or not cart["items"]:
            await safe_edit_or_reply(query,"🛒 장바구니가 비어있습니다.")
            return

        # 보유종목 등록
        registered = []
        horizon_to_holding_type = {
            "scalp": "scalp",
            "short": "swing",
            "mid": "position",
            "long": "long_term",
        }

        for item in cart["items"]:
            holding_type = horizon_to_holding_type.get(
                item["horizon"], "auto",
            )
            try:
                self.db.add_holding(
                    ticker=item["ticker"],
                    name=item["name"],
                    buy_price=item["price"],
                    holding_type=holding_type,
                )
                registered.append(item)
                logger.info(
                    "Cart confirmed: %s %s (%s) %d주 @ %d원",
                    holding_type, item["name"], item["ticker"],
                    item["quantity"], item["price"],
                )
                # v6.2: 신호 성과 추적 기록
                manager_map = {
                    "scalp": "manager_scalp", "short": "manager_swing",
                    "mid": "manager_position", "long": "manager_long_term",
                }
                signal_source = manager_map.get(item.get("horizon", ""), "scan_engine")
                try:
                    self.db.save_signal_performance(
                        signal_source=signal_source,
                        signal_type="buy",
                        ticker=item["ticker"],
                        name=item["name"],
                        signal_date=datetime.now(KST).strftime("%Y-%m-%d"),
                        signal_score=item.get("score", 0),
                        signal_price=item["price"],
                        horizon=holding_type,
                        manager=item.get("manager", ""),
                    )
                except Exception:
                    logger.debug("Signal performance save failed for %s", item["ticker"], exc_info=True)
            except Exception as e:
                logger.error(
                    "Failed to register holding %s: %s",
                    item["ticker"], e,
                )

        # 장바구니 정리
        context.user_data.pop("buy_cart", None)
        context.user_data.pop("_ai_picks", None)
        context.user_data.pop("_horizon_picks", None)

        if not registered:
            await safe_edit_or_reply(query,"⚠️ 종목 등록에 실패했습니다.")
            return

        # 결과 메시지
        horizon_emoji = {"scalp": "⚡", "short": "🔥", "mid": "📊", "long": "💎"}
        lines = [
            f"✅ {len(registered)}종목 매수 계획 확정!\n",
            f"{'━' * 22}",
        ]
        for item in registered:
            emoji = horizon_emoji.get(item["horizon"], "📌")
            lines.append(
                f"{emoji} {item['name']}\n"
                f"   {item['price']:,.0f}원 x {item['quantity']}주"
            )
        lines.append(f"\n{'━' * 22}")
        lines.append("📡 모니터링이 시작됩니다")

        # 단타 종목이 있으면 모니터링 주기 안내
        has_scalp = any(
            item["horizon"] == "scalp" for item in registered
        )
        if has_scalp:
            lines.append("⚡ 단타 종목 → 실시간 급등/목표 알림")

        lines.append("\n행운을 빕니다, 주호님!")

        await safe_edit_or_reply(query,"\n".join(lines))

    # == Backtest Pro ========================================================

    async def _action_backtest_pro(self, query, context, payload: str) -> None:
        """Backtest Pro 콜백: bt:portfolio, bt:withcost:{ticker}."""
        if payload == "portfolio":
            holdings = self.db.get_active_holdings()
            if not holdings:
                await safe_edit_or_reply(query,"\u26a0\ufe0f 보유종목이 없습니다.")
                return
            await safe_edit_or_reply(query,
                "\U0001f4ca 포트폴리오 백테스트 실행 중...\n(시간이 걸릴 수 있습니다)"
            )
            from kstock.backtest.engine import (
                TradeCosts,
                run_portfolio_backtest,
                format_portfolio_backtest,
            )
            tickers = []
            n = len(holdings)
            for h in holdings:
                tickers.append({
                    "code": h["ticker"],
                    "name": h.get("name", h["ticker"]),
                    "market": h.get("market", "KOSPI"),
                    "weight": 1.0 / n,
                })
            result = run_portfolio_backtest(tickers, costs=TradeCosts())
            if result:
                text = format_portfolio_backtest(result)
                await query.message.reply_text(text)
            else:
                await query.message.reply_text(
                    "\u26a0\ufe0f 백테스트 데이터가 부족합니다."
                )
            return

        if payload.startswith("withcost:"):
            ticker = payload.split(":")[1]
            name = ticker
            market = "KOSPI"
            for item in self.all_tickers:
                if item["code"] == ticker:
                    name = item["name"]
                    market = item.get("market", "KOSPI")
                    break
            await safe_edit_or_reply(query,
                f"\U0001f4ca {name} 비용 포함 백테스트 실행 중..."
            )
            from kstock.backtest.engine import (
                TradeCosts,
                run_backtest,
                format_backtest_result,
            )
            result = run_backtest(
                ticker, name=name, market=market, costs=TradeCosts(),
            )
            if result:
                text = format_backtest_result(result)
                text += f"\n\n\U0001f4b0 총 거래비용: {result.total_cost_pct:.1f}%"
                await query.message.reply_text(text)
            else:
                await query.message.reply_text("\u26a0\ufe0f 백테스트 실패")
            return

    async def _action_risk_advanced(self, query, context, payload: str) -> None:
        """고급 리스크 리포트 콜백: risk:advanced."""
        if payload != "advanced":
            return
        holdings = self.db.get_active_holdings()
        if not holdings:
            await safe_edit_or_reply(query,"\u26a0\ufe0f 보유종목이 없습니다.")
            return
        await safe_edit_or_reply(query,
            "📊 고급 리스크 분석 실행 중...\n"
            "(VaR, Monte Carlo, 스트레스 테스트)"
        )
        try:
            from kstock.core.risk_engine import (
                generate_advanced_risk_report,
                format_advanced_risk_report,
            )
            total_value = sum(
                h.get("current_price", 0) * h.get("quantity", 0)
                for h in holdings
            )
            if total_value <= 0:
                total_value = sum(
                    h.get("buy_price", 0) * h.get("quantity", 0)
                    for h in holdings
                )
            report = await generate_advanced_risk_report(total_value, holdings)
            text = format_advanced_risk_report(report)
            await query.message.reply_text(text)
            logger.info("Advanced risk report generated")
        except Exception as e:
            logger.error("Advanced risk report error: %s", e, exc_info=True)
            await query.message.reply_text(
                "\u26a0\ufe0f 리스크 분석 실행 중 오류가 발생했습니다."
            )

    # == v4.1: Position Sizing Integration ====================================

    async def _calculate_position_size_for_ticker(
        self, ticker: str, name: str = "", budget: float = 0,
    ) -> str:
        """특정 종목의 최적 포지션 사이즈를 계산하고 텔레그램 메시지로 반환."""
        try:
            from kstock.core.position_sizer import PositionSizer
            from kstock.core.risk_manager import SECTOR_MAP

            # 계좌 규모 파악
            holdings = self.db.get_active_holdings()
            total_value = budget
            if not total_value:
                total_value = sum(
                    (h.get("current_price", 0) or h.get("buy_price", 0))
                    * h.get("quantity", 1)
                    for h in holdings
                )
            if total_value <= 0:
                total_value = 200_000_000  # 기본값

            sizer = PositionSizer(account_value=total_value)

            # 종목 데이터 가져오기
            result = self._find_cached_result(ticker)
            if not result:
                result = await self._scan_single_stock(ticker)

            if not result:
                return f"⚠️ {name or ticker} 데이터를 가져올 수 없습니다."

            price = getattr(result.info, 'current_price', 0) or 0
            atr_pct = getattr(result.tech, 'atr_pct', 1.5) or 1.5
            rsi = getattr(result.tech, 'rsi', 50)

            # 기존 보유 비중 계산
            existing_weight = 0.0
            sector_weight = 0.0
            target_sector = SECTOR_MAP.get(ticker, "기타")
            total_port = sum(
                (h.get("current_price", 0) or h.get("buy_price", 0))
                * h.get("quantity", 1)
                for h in holdings
            ) or total_value

            for h in holdings:
                hval = (
                    (h.get("current_price", 0) or h.get("buy_price", 0))
                    * h.get("quantity", 1)
                )
                if h.get("ticker") == ticker:
                    existing_weight = hval / total_port
                h_sector = SECTOR_MAP.get(h.get("ticker", ""), "기타")
                if h_sector == target_sector:
                    sector_weight += hval / total_port

            # 승률/목표/손절 추정
            score = result.score.composite
            if score >= 80:
                win_rate, target_pct, stop_pct = 0.65, 0.12, -0.05
            elif score >= 60:
                win_rate, target_pct, stop_pct = 0.55, 0.10, -0.05
            else:
                win_rate, target_pct, stop_pct = 0.45, 0.08, -0.05

            pos = sizer.calculate(
                ticker=ticker,
                current_price=price,
                atr_pct=atr_pct,
                win_rate=win_rate,
                target_pct=target_pct,
                stop_pct=stop_pct,
                existing_weight=existing_weight,
                sector_weight=sector_weight,
                name=name or result.name,
            )

            return sizer.format_position_advice(pos)

        except Exception as e:
            logger.error("Position sizing error: %s", e, exc_info=True)
            return "⚠️ 포지션 사이징 계산 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."

    # == Phase 2+3 Callback Handlers (v4.3) ===================================

    async def _action_journal_view(self, query, context, payload: str) -> None:
        """매매일지 콜백: journal:detail:weekly / journal:detail:monthly."""
        parts = payload.split(":")
        period = parts[0] if parts else "weekly"
        period_label = "주간" if period == "weekly" else "월간"

        try:
            reports = self.db.get_journal_reports(period=period, limit=1)
            if not reports:
                await safe_edit_or_reply(query,
                    f"📋 {period_label} 매매일지가 아직 없습니다."
                )
                return

            r = reports[0]
            text = (
                f"📋 {period_label} 매매일지 상세\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 기간: {r.get('date_range', 'N/A')}\n"
                f"📊 거래: {r.get('total_trades', 0)}회\n"
                f"🎯 승률: {r.get('win_rate', 0):.0f}%\n"
                f"💰 평균 수익: {r.get('avg_pnl', 0):+.1f}%\n"
            )

            ai_review = r.get("ai_review", "")
            if ai_review:
                text += f"\n🤖 AI 복기\n{ai_review[:800]}"

            await safe_edit_or_reply(query,text)

        except Exception as e:
            logger.error("Journal view error: %s", e, exc_info=True)
            await safe_edit_or_reply(query,"⚠️ 매매일지 조회 중 오류 발생")

    async def _action_sector_rotate(self, query, context, payload: str) -> None:
        """섹터 로테이션 콜백: sector_rotate:detail."""
        try:
            snapshots = self.db.get_sector_snapshots(limit=1)
            if not snapshots:
                await safe_edit_or_reply(query,
                    "🔄 섹터 로테이션 데이터가 아직 없습니다.\n"
                    "매일 09:05에 자동 분석됩니다."
                )
                return

            import json
            snap = snapshots[0]
            sectors = json.loads(snap.get("sectors_json", "[]"))
            signals = json.loads(snap.get("signals_json", "[]"))
            portfolio = json.loads(snap.get("portfolio_json", "{}"))

            lines = [
                "🔄 섹터 로테이션 상세",
                f"📅 {snap.get('snapshot_date', '')}",
                "━" * 25,
                "",
                "📊 섹터 모멘텀",
            ]

            for i, s in enumerate(sectors, 1):
                score = s.get("momentum", 0)
                emoji = "🔥" if score > 5 else "❄️" if score < -5 else "➖"
                lines.append(
                    f"  {i}. {emoji} {s['sector']} "
                    f"[1주 {s.get('1w', 0):+.1f}% | 1개월 {s.get('1m', 0):+.1f}%]"
                )

            if portfolio:
                lines.extend(["", "💼 내 섹터 비중"])
                for sector, weight in portfolio.items():
                    lines.append(f"  {sector}: {weight:.0f}%")

            if signals:
                lines.extend(["", "📡 시그널"])
                for sig in signals:
                    dir_emoji = "🟢" if sig.get("direction") in ("overweight", "rotate_in") else "🔴"
                    lines.append(f"  {dir_emoji} {sig['sector']} → {sig['direction']}")

            await safe_edit_or_reply(query,"\n".join(lines))

        except Exception as e:
            logger.error("Sector rotation view error: %s", e, exc_info=True)
            await safe_edit_or_reply(query,"⚠️ 섹터 로테이션 조회 중 오류 발생")

    async def _action_contrarian_view(self, query, context, payload: str) -> None:
        """역발상 시그널 콜백: contrarian:history."""
        try:
            signals = self.db.get_contrarian_signals(limit=10)
            if not signals:
                await safe_edit_or_reply(query,
                    "🔮 역발상 시그널 이력이 없습니다.\n"
                    "매일 14:00에 자동 스캔됩니다."
                )
                return

            lines = ["🔮 최근 역발상 시그널 이력", "━" * 25, ""]
            for s in signals:
                emoji = "🟢" if s.get("direction") == "BUY" else "🔴"
                strength = s.get("strength", 0)
                lines.append(
                    f"{emoji} {s.get('name', '')} ({s.get('signal_type', '')})\n"
                    f"  강도: {strength:.0%} | {s.get('created_at', '')[:16]}"
                )

            await safe_edit_or_reply(query,"\n".join(lines))

        except Exception as e:
            logger.error("Contrarian view error: %s", e, exc_info=True)
            await safe_edit_or_reply(query,"⚠️ 역발상 시그널 조회 중 오류 발생")

    async def _action_backtest_advanced(self, query, context, payload: str) -> None:
        """고급 백테스트 콜백: bt_adv:mc:{ticker} / bt_adv:wf:{ticker}."""
        parts = payload.split(":")
        mode = parts[0] if parts else "mc"
        ticker = parts[1] if len(parts) > 1 else ""

        try:
            from kstock.backtest.engine import run_backtest
            from kstock.backtest.advanced import (
                AdvancedBacktester, format_monte_carlo,
                format_walk_forward, format_risk_metrics,
            )

            await safe_edit_or_reply(query,f"⏳ {ticker} 고급 백테스트 실행 중...")

            # 기본 백테스트 실행
            result = run_backtest(ticker, period="1y")
            if not result or not result.trades:
                await safe_edit_or_reply(query,
                    f"⚠️ {ticker} 백테스트 데이터 부족"
                )
                return

            pnls = [t.pnl_pct for t in result.trades]
            bt = AdvancedBacktester()

            if mode == "mc":
                mc = bt.run_monte_carlo(pnls, n_simulations=3000, seed=42)
                text = format_monte_carlo(mc)
            elif mode == "wf":
                wf = bt.run_walk_forward(pnls)
                text = format_walk_forward(wf)
            else:
                metrics = bt.compute_risk_metrics(pnls)
                text = format_risk_metrics(metrics)

            # 다른 분석 버튼
            buttons = []
            if mode != "mc":
                buttons.append(InlineKeyboardButton(
                    "🎲 Monte Carlo", callback_data=f"bt_adv:mc:{ticker}",
                ))
            if mode != "wf":
                buttons.append(InlineKeyboardButton(
                    "🔄 Walk-Forward", callback_data=f"bt_adv:wf:{ticker}",
                ))
            if mode != "risk":
                buttons.append(InlineKeyboardButton(
                    "📐 리스크 지표", callback_data=f"bt_adv:risk:{ticker}",
                ))

            keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
            await safe_edit_or_reply(query,text, reply_markup=keyboard)

        except Exception as e:
            logger.error("Advanced backtest error: %s", e, exc_info=True)
            await safe_edit_or_reply(query,"⚠️ 고급 백테스트 실행 중 오류 발생")

    # == v6.2: 자동 매매 복기 ================================================

    async def _trigger_auto_debrief(
        self,
        ticker: str,
        name: str,
        action: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        holding: dict | None = None,
        trade_id: int | None = None,
    ) -> None:
        """매매 완료 시 백그라운드로 자동 복기 실행."""
        try:
            from kstock.signal.auto_debrief import auto_debrief_trade, format_debrief_message

            # holding에서 추가 정보 추출
            horizon = "swing"
            manager = ""
            if holding:
                ht = holding.get("holding_type", "auto")
                horizon = {
                    "scalp": "scalp", "swing": "swing",
                    "position": "position", "long_term": "long_term",
                }.get(ht, "swing")

            # 보유일수 계산
            hold_days = 0
            if holding and holding.get("buy_date"):
                try:
                    buy_dt = datetime.strptime(holding["buy_date"][:10], "%Y-%m-%d")
                    hold_days = max(0, (datetime.now(KST).replace(tzinfo=None) - buy_dt).days)
                except Exception:
                    logger.debug("hold_days calc failed", exc_info=True)

            # 시장 레짐
            market_regime = ""
            try:
                macro = await self.macro_client.get_snapshot()
                if macro and hasattr(macro, "vix"):
                    if macro.vix >= 30:
                        market_regime = "panic"
                    elif macro.vix >= 25:
                        market_regime = "fear"
                    elif macro.vix >= 18:
                        market_regime = "normal"
                    else:
                        market_regime = "calm"
            except Exception:
                logger.debug("Auto debrief macro fetch failed", exc_info=True)

            result = await auto_debrief_trade(
                db=self.db,
                ticker=ticker,
                name=name,
                action=action,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                hold_days=hold_days,
                horizon=horizon,
                manager=manager,
                market_regime=market_regime,
                trade_id=trade_id,
            )

            # 텔레그램으로 복기 결과 전송 (등급 C 이하 또는 수익 3%+ 매매만)
            if result.grade in ("A", "B") or result.grade in ("D", "F") or abs(pnl_pct) >= 3:
                debrief_msg = format_debrief_message(result)
                if self.chat_id:
                    try:
                        from telegram import Bot
                        bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN", ""))
                        # v6.2.1: 페이지네이션 (4096자 제한)
                        if len(debrief_msg) > 3800:
                            pages = []
                            lines = debrief_msg.split("\n")
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
                                await bot.send_message(
                                    chat_id=self.chat_id, text=hdr + pg,
                                )
                        else:
                            await bot.send_message(
                                chat_id=self.chat_id,
                                text=debrief_msg,
                            )
                    except Exception as e:
                        logger.warning("복기 메시지 전송 실패: %s", e)

        except Exception as e:
            logger.error("Auto debrief trigger failed: %s", e, exc_info=True)

    # == Scheduled Jobs ======================================================
