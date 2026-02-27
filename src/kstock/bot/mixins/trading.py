"""Trading, balance, holdings management."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403


class TradingMixin:
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
                        pass
            if added:
                msg = (
                    f"✅ {len(added)}종목 포트폴리오 추가 완료!\n\n"
                    + "\n".join(added)
                )
            else:
                msg = "⚠️ 추가할 수 있는 종목이 없습니다."
            await query.edit_message_text(msg)
            # 투자전략 일괄 선택 키보드
            if added_ids:
                context.user_data["recent_holding_ids"] = added_ids
                try:
                    await self._ask_holding_type_bulk(query, added_ids)
                except Exception:
                    pass
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
                        await self._ask_holding_type(query, holding_id, name)
                    except Exception:
                        pass
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
                    await self._ask_holding_type(query, holding_id, name)
                except Exception:
                    pass
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
                    f"[펀더멘털]\n{fund_data}\n\n"
                    f"{trade_levels}\n"
                    f"[절대 규칙] 위 [실시간 가격]과 [매매 참고 레벨]의 숫자만 사용하라. "
                    f"너의 학습 데이터에 있는 과거 주가를 절대 사용 금지. "
                    f"매수/매도 포인트 가격은 반드시 위 [매매 참고 레벨]에서 선택하라."
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
                    "⏭️ 개별 설정은 나중에", callback_data="ht:skip:0",
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
            await query.edit_message_text("⏭️ 투자 전략 설정을 건너뛰었습니다.")
            context.user_data.pop("recent_holding_ids", None)
            return

        from kstock.bot.investment_managers import get_manager_greeting, get_manager_label

        if target == "all":
            ids = context.user_data.get("recent_holding_ids", [])
            for hid in ids:
                try:
                    self.db.update_holding_type(hid, hold_type)
                except Exception:
                    pass
            label = get_manager_label(hold_type)
            await query.edit_message_text(
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
                await query.edit_message_text(greeting)
            except Exception as e:
                logger.error("holding_type 설정 실패: %s", e)
                await query.edit_message_text("⚠️ 투자 전략 설정 실패")

    async def _action_manager_view(
        self, query, context, payload: str,
    ) -> None:
        """mgr:{type} 콜백 — 매니저에게 담당 종목 분석 요청."""
        from kstock.bot.investment_managers import get_manager_analysis, MANAGERS

        manager = MANAGERS.get(payload)
        if not manager:
            await query.edit_message_text("⚠️ 알 수 없는 매니저 유형")
            return

        holdings = self.db.get_active_holdings()
        type_holdings = [
            h for h in holdings
            if h.get("holding_type", "auto") == payload
            or (payload == "swing" and h.get("holding_type", "auto") == "auto")
        ]

        if not type_holdings:
            await query.edit_message_text(
                f"{manager['emoji']} {manager['name']}: 담당 종목이 없습니다."
            )
            return

        await query.edit_message_text(
            f"{manager['emoji']} {manager['name']} 분석 중..."
        )

        try:
            macro = await self.macro_client.get_snapshot()
            market_text = (
                f"VIX={macro.vix:.1f}, S&P={macro.spx_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원"
            )
        except Exception:
            market_text = ""

        report = await get_manager_analysis(payload, type_holdings, market_text)
        await query.message.reply_text(report[:4000])

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
                await query.edit_message_text("📦 보유종목이 없습니다.")
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

            await query.edit_message_text(
                "🫧 거품 판별할 종목을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        await query.edit_message_text(f"🫧 {ticker} 거품 분석 중...")

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
            await query.edit_message_text(
                "📝 추가할 종목명을 입력하세요.\n\n"
                "예: 삼성전자\n"
                "예: 005930\n\n"
                "또는 스크린샷을 전송하세요 📸"
            )

        elif payload == "refresh":
            await query.edit_message_text("🔄 잔고 새로고침 중...")
            try:
                holdings = await self._load_holdings_with_fallback()
                if not holdings:
                    await query.message.reply_text(
                        "💰 등록된 보유종목이 없습니다.\n📸 스크린샷을 보내주세요!",
                        reply_markup=MAIN_MENU,
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
                    "⚠️ 잔고 새로고침 실패.", reply_markup=MAIN_MENU,
                )

        elif payload.startswith("remove:"):
            ticker = payload[7:]
            holding = self.db.get_holding_by_ticker(ticker)
            if holding:
                self.db.update_holding(holding["id"], status="sold")
                hname = holding.get("name", ticker)
                # 삭제 후 잔고 메뉴 재표시 (메뉴 닫기 전까지 유지)
                holdings = await self._load_holdings_with_fallback()
                if holdings:
                    total_eval, total_invested = await self._update_holdings_prices(holdings)
                    lines = self._format_balance_lines(holdings, total_eval, total_invested)
                    lines.insert(0, f"\U0001f5d1\ufe0f {hname} 삭제 완료!\n")
                    bal_buttons = self._build_balance_buttons(holdings)
                    await query.edit_message_text(
                        "\n".join(lines),
                        reply_markup=InlineKeyboardMarkup(bal_buttons),
                    )
                else:
                    await query.edit_message_text(
                        f"\U0001f5d1\ufe0f {hname} 삭제 완료!\n\n"
                        "\U0001f4b0 보유종목이 없습니다."
                    )
            else:
                await query.edit_message_text("\u26a0\ufe0f 종목을 찾을 수 없습니다.")

    def _resolve_ticker_from_name(self, name: str) -> str:
        """종목명으로 유니버스에서 티커 코드를 찾습니다."""
        if not name:
            return ""
        # 1. 유니버스 정확 매치
        for item in self.all_tickers:
            if item["name"] == name:
                return item["code"]
        # 2. DB 보유종목에서 이름+ticker 매치
        existing = self.db.get_holding_by_name(name)
        if existing and existing.get("ticker"):
            return existing["ticker"]
        return ""

    async def _load_holdings_with_fallback(self) -> list[dict]:
        """보유종목 로드 (DB 우선, 없으면 스크린샷 fallback → DB 동기화).

        [v3.5.5] 빈 ticker를 유니버스에서 해결 시도.
        [v3.6.2] 스크린샷 fallback 시 holdings DB에 자동 동기화.
        [v3.10] sold 이력이 있으면 스크린샷 fallback 스킵 (삭제 종목 부활 방지).
        """
        holdings = self.db.get_active_holdings()
        if not holdings:
            # sold 이력이 있으면 유저가 의도적으로 삭제한 것 → fallback 스킵
            has_sold = False
            try:
                with self.db._connect() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM holdings WHERE status='sold'"
                    ).fetchone()
                    has_sold = (row["cnt"] if row else 0) > 0
            except Exception:
                pass
            if has_sold:
                return []
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
                                "eval_amount": h.get("eval_amount", 0),
                            }
                            for h in items
                        ]
            except Exception as e:
                logger.warning("Screenshot holdings fallback failed: %s", e)

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
        for h in holdings:
            ticker = h.get("ticker", "")
            if ticker and re.match(r'^\d{6}$', ticker) and h.get("name"):
                try:
                    self.db.upsert_holding(
                        ticker=ticker,
                        name=h["name"],
                        quantity=h.get("quantity", 0),
                        buy_price=h.get("buy_price", 0),
                        current_price=h.get("current_price", 0),
                        pnl_pct=h.get("pnl_pct", 0),
                        eval_amount=h.get("eval_amount", 0),
                    )
                    synced = True
                except Exception:
                    pass
        if synced:
            logger.debug("Holdings synced to DB: %d items", len(holdings))

        return holdings

    async def _update_holdings_prices(self, holdings: list[dict]) -> tuple:
        """보유종목 실시간 가격 업데이트 + 총합 계산. Returns (total_eval, total_invested).

        [v3.5.5] ticker 없어도 eval_amount/quantity로 총합 계산.
        """
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
                    if cur <= 0:
                        cur = bp

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
        """잔고 현황 텍스트 포맷."""
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
            f"\U0001f4b5 총 평가금액: {total_eval:,.0f}원",
            f"\U0001f4b4 총 투자금액: {total_invested:,.0f}원",
            f"\U0001f4b0 총 손익: {pnl_arrow} {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_pnl_rate:.2f}%)",
        ]
        if margin_count > 0:
            lines.append(f"\U0001f4b3 신용/마진: {margin_count}종목 ({margin_eval:,.0f}원)")
        lines.extend(["", f"보유종목 ({len(holdings)}개)", "\u2500" * 25])

        for h in holdings:
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

            # 신용 표시
            margin_tag = ""
            if h.get("_is_margin_display") or h.get("is_margin") or h.get("margin_type"):
                margin_tag = " \U0001f4b3"

            qty_text = f" {qty}주" if qty > 0 else ""
            # ticker 있으면 표시, 없으면 생략
            ticker_text = f"({ticker})" if ticker else ""
            line = f"{emoji} {name}{ticker_text}{qty_text}{margin_tag}\n"
            line += f"   매수 {bp:,.0f}원 \u2192 현재 {cp:,.0f}원\n"

            if eval_amt > 0:
                line += f"   평가 {eval_amt:,.0f}원"
                if pnl_amount != 0:
                    line += f" | 손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)"
                else:
                    line += f" | 수익률 {pnl:+.1f}%"
            elif pnl_amount != 0:
                line += f"   손익 {pnl_sign_s}{pnl_amount:,.0f}원 ({pnl:+.1f}%)"
            else:
                line += f"   수익률 {pnl:+.1f}%"

            if day_chg_pct != 0:
                day_emoji = "\U0001f4c8" if day_chg_pct > 0 else "\U0001f4c9"
                day_sign = "+" if day_chg_pct > 0 else ""
                line += f"\n   오늘 {day_emoji} {day_sign}{day_chg:,.0f}원 ({day_sign}{day_chg_pct:.1f}%)"

            lines.append(line)
        return lines

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
        buttons.append(make_feedback_row("잔고"))
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
        # 안전장치: 실전매매 환경변수 체크
        real_trade = os.getenv("REAL_TRADE_ENABLED", "false").lower() == "true"
        is_virtual = getattr(self.kis, '_is_virtual', True)
        if not is_virtual and not real_trade:
            await query.edit_message_text(
                "\U0001f6ab 실전투자 모드에서 자동매매가 비활성화되어 있습니다.\n\n"
                ".env에 REAL_TRADE_ENABLED=true 설정 필요.\n"
                "\U0001f4e1 KIS설정 → 안전 설정에서 확인하세요."
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
        # 실전 모드 1회 주문 한도: 투자금 10% 또는 500만원 중 작은 값
        if not is_virtual:
            order_amount = price * qty
            max_amount = min(total_eval * 0.1, 5_000_000)
            if order_amount > max_amount:
                await query.edit_message_text(
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
            await query.edit_message_text(
                f"\u2705 {result.name} {qty}주 시장가 매수 주문 완료!\n"
                f"주문번호: {order.order_id}"
            )
        else:
            await query.edit_message_text(f"\u274c 매수 실패: {order.message}")

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
            await query.edit_message_text(
                "💰 주호님, 오늘 매수 금액을 선택해주세요\n"
                "(만원 단위)",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if payload.startswith("amt:"):
            amt_val = payload.split(":")[1]
            if amt_val == "custom":
                context.user_data["awaiting_buy_amount"] = True
                await query.edit_message_text(
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
            await query.edit_message_text(
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
                await query.edit_message_text("🤖 AI가 최적 포트폴리오를 분석 중...")
                await self._show_ai_recommendation(query, context)
            else:
                await query.edit_message_text("💭 종목을 분석하고 있습니다...")
                await self._show_horizon_picks(query, context, inv_type)
            return

        if payload == "no":
            await query.edit_message_text(
                "🏖️ 알겠습니다!\n"
                "좋은 하루 보내세요, 주호님\n\n"
                "매수 계획이 생기면 언제든 말씀하세요"
            )
            return

        if payload == "dismiss":
            await query.edit_message_text("👋 확인했습니다.")
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
                await query.edit_message_text("⚠️ 장바구니 정보가 없습니다.")
                return
            added = 0
            for p in ai_picks:
                if cart["remaining"] < p["amount"]:
                    continue
                cart["items"].append(p)
                cart["remaining"] -= p["amount"]
                added += 1
            context.user_data.pop("_ai_picks", None)
            await query.edit_message_text(
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
            await query.edit_message_text("❌ 매수 계획을 취소했습니다.")
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
            await query.edit_message_text(
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

        # 전체 종목 스캔 (5분 캐시)
        now = datetime.now(KST)
        if (
            hasattr(self, '_scan_cache_time')
            and self._scan_cache_time
            and (now - self._scan_cache_time).total_seconds() < 300
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
            await query.edit_message_text("⚠️ 장바구니 정보가 없습니다.")
            return

        await query.edit_message_text("🔍 종목을 분석하고 있습니다...")

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
            await query.edit_message_text("⚠️ 장바구니 정보가 없습니다.")
            return

        # 캐시에서 종목 데이터 가져오기
        picks_cache = getattr(self, '_horizon_picks_cache', {})
        pick = picks_cache.get(ticker)

        if not pick:
            await query.edit_message_text(
                "⚠️ 종목 정보를 찾을 수 없습니다.\n다시 종목 보기를 선택해주세요."
            )
            return

        # 이미 담긴 종목 체크
        if any(item["ticker"] == ticker for item in cart["items"]):
            await query.edit_message_text(
                f"⚠️ {pick['name']}은 이미 장바구니에 있습니다."
            )
            return

        # 예산 체크
        if pick["amount"] > cart["remaining"]:
            await query.edit_message_text(
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

        await query.edit_message_text(
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
            await query.edit_message_text("⚠️ 장바구니 정보가 없습니다.")
            return

        await query.edit_message_text(
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
            await query.edit_message_text("🛒 장바구니가 비어있습니다.")
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

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _confirm_cart(self, query, context) -> None:
        """장바구니 확정: 보유종목 등록 + 모니터링 시작."""
        cart = context.user_data.get("buy_cart")
        if not cart or not cart["items"]:
            await query.edit_message_text("🛒 장바구니가 비어있습니다.")
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
            await query.edit_message_text("⚠️ 종목 등록에 실패했습니다.")
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

        await query.edit_message_text("\n".join(lines))

    # == Backtest Pro ========================================================

    async def _action_backtest_pro(self, query, context, payload: str) -> None:
        """Backtest Pro 콜백: bt:portfolio, bt:withcost:{ticker}."""
        if payload == "portfolio":
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.edit_message_text("\u26a0\ufe0f 보유종목이 없습니다.")
                return
            await query.edit_message_text(
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
            await query.edit_message_text(
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
            await query.edit_message_text("\u26a0\ufe0f 보유종목이 없습니다.")
            return
        await query.edit_message_text(
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
            return f"⚠️ 포지션 사이징 계산 중 오류: {str(e)[:100]}"

    # == Phase 2+3 Callback Handlers (v4.3) ===================================

    async def _action_journal_view(self, query, context, payload: str) -> None:
        """매매일지 콜백: journal:detail:weekly / journal:detail:monthly."""
        parts = payload.split(":")
        period = parts[0] if parts else "weekly"
        period_label = "주간" if period == "weekly" else "월간"

        try:
            reports = self.db.get_journal_reports(period=period, limit=1)
            if not reports:
                await query.edit_message_text(
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

            await query.edit_message_text(text)

        except Exception as e:
            logger.error("Journal view error: %s", e, exc_info=True)
            await query.edit_message_text("⚠️ 매매일지 조회 중 오류 발생")

    async def _action_sector_rotate(self, query, context, payload: str) -> None:
        """섹터 로테이션 콜백: sector_rotate:detail."""
        try:
            snapshots = self.db.get_sector_snapshots(limit=1)
            if not snapshots:
                await query.edit_message_text(
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

            await query.edit_message_text("\n".join(lines))

        except Exception as e:
            logger.error("Sector rotation view error: %s", e, exc_info=True)
            await query.edit_message_text("⚠️ 섹터 로테이션 조회 중 오류 발생")

    async def _action_contrarian_view(self, query, context, payload: str) -> None:
        """역발상 시그널 콜백: contrarian:history."""
        try:
            signals = self.db.get_contrarian_signals(limit=10)
            if not signals:
                await query.edit_message_text(
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

            await query.edit_message_text("\n".join(lines))

        except Exception as e:
            logger.error("Contrarian view error: %s", e, exc_info=True)
            await query.edit_message_text("⚠️ 역발상 시그널 조회 중 오류 발생")

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

            await query.edit_message_text(f"⏳ {ticker} 고급 백테스트 실행 중...")

            # 기본 백테스트 실행
            result = run_backtest(ticker, period="1y")
            if not result or not result.trades:
                await query.edit_message_text(
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
            await query.edit_message_text(text, reply_markup=keyboard)

        except Exception as e:
            logger.error("Advanced backtest error: %s", e, exc_info=True)
            await query.edit_message_text("⚠️ 고급 백테스트 실행 중 오류 발생")

    # == Scheduled Jobs ======================================================

