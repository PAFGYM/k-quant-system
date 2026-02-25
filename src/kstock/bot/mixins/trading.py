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
        """ht:{type}:{id_or_all} 콜백 처리."""
        hold_type, _, target = payload.partition(":")

        if hold_type == "skip":
            await query.edit_message_text("⏭️ 투자 전략 설정을 건너뛰었습니다.")
            context.user_data.pop("recent_holding_ids", None)
            return

        type_labels = {
            "scalp": "⚡ 초단기",
            "swing": "🔥 단기",
            "position": "📊 중기",
            "long_term": "💎 장기투자",
        }
        label = type_labels.get(hold_type, hold_type)

        if target == "all":
            ids = context.user_data.get("recent_holding_ids", [])
            for hid in ids:
                try:
                    self.db.update_holding_type(hid, hold_type)
                except Exception:
                    pass
            await query.edit_message_text(
                f"✅ {len(ids)}종목 → {label} 설정 완료"
            )
            context.user_data.pop("recent_holding_ids", None)
        else:
            try:
                hid = int(target)
                self.db.update_holding_type(hid, hold_type)
                holding = self.db.get_holding(hid)
                name = holding.get("name", "") if holding else ""
                await query.edit_message_text(
                    f"✅ {name} → {label} 설정 완료"
                )
            except Exception as e:
                logger.error("holding_type 설정 실패: %s", e)
                await query.edit_message_text("⚠️ 투자 전략 설정 실패")

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
                await query.edit_message_text(f"🗑️ {hname} 포트폴리오에서 삭제!")
            else:
                await query.edit_message_text("⚠️ 종목을 찾을 수 없습니다.")

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
        """
        holdings = self.db.get_active_holdings()
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
        """매수 플래너 콜백 핸들러.

        콜백: bp:yes, bp:no, bp:hz:{horizon}:{amount}, bp:dismiss
        """
        if payload == "yes":
            context.user_data["awaiting_buy_amount"] = True
            await query.edit_message_text(
                "💰 투자 금액을 입력해주세요\n"
                "(만원 단위 숫자만 입력)\n\n"
                "예: 50 → 50만원\n"
                "예: 300 → 300만원"
            )
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

        if payload.startswith("hz:"):
            parts = payload.split(":")
            if len(parts) < 3:
                return
            horizon = parts[1]
            amount_만원 = int(parts[2])
            amount_won = amount_만원 * 10000

            await query.edit_message_text(
                "💭 주호님 맞춤 종목을 분석하고 있습니다...\n"
                "(약 30초 소요)"
            )

            try:
                result_text, buttons = await self._generate_buy_recommendations(
                    horizon, amount_won,
                )
                keyboard = InlineKeyboardMarkup(buttons) if buttons else None
                await query.message.reply_text(
                    result_text, reply_markup=keyboard,
                )
            except Exception as e:
                logger.error("Buy planner error: %s", e, exc_info=True)
                await query.message.reply_text(
                    "⚠️ 종목 분석 중 오류가 발생했습니다.\n"
                    "잠시 후 다시 시도해주세요."
                )

    async def _generate_buy_recommendations(
        self, horizon: str, amount_won: int,
    ) -> tuple:
        """투자 기간 + 예산에 맞는 종목 추천 생성."""
        config = self._HORIZON_STRATEGIES.get(horizon)
        if not config:
            return "⚠️ 잘못된 투자 기간입니다.", []

        amount_만원 = amount_won // 10000

        # 0. 시장 레짐 확인
        macro = await self.macro_client.get_snapshot()
        from kstock.signal.strategies import get_regime_mode
        regime = get_regime_mode(macro)

        if horizon == "scalp" and regime["mode"] == "defense":
            return (
                f"🛡️ 현재 방어 모드 (VIX {macro.vix:.1f})\n\n"
                "시장 변동성이 높아 초단기 매매는 권장하지 않습니다.\n"
                "단기 이상 기간을 선택하시거나, 시장 안정 후 재시도해주세요.\n\n"
                "💡 방어 모드에서는 현금 비중 35% 권장",
                [],
            )

        # 1. 전체 종목 스캔 (5분 캐시)
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

        # 2. 전략 필터링
        target_strategies = config["strategies"]
        filtered = []
        for r in results:
            for sig in (r.strategy_signals or []):
                if sig.strategy in target_strategies and sig.action in ("BUY", "WATCH"):
                    filtered.append((r, sig))
                    break

        # BUY 우선, 점수 높은 순
        filtered.sort(
            key=lambda x: (0 if x[1].action == "BUY" else 1, -x[0].score.composite),
        )
        top_picks = filtered[:5]

        if not top_picks:
            return (
                f"📋 {config['label']} 조건에 맞는 종목이 현재 없습니다.\n\n"
                "시장 상황이 해당 전략에 맞지 않을 수 있습니다.\n"
                "다른 기간을 선택하거나 장 시작 후 다시 확인해보세요.",
                [],
            )

        # 3. 종목 데이터 + ATR 등급 + Kelly 배분 + E[R] 계산
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

            # E[R] < 거래비용(0.5%)이면 스킵
            if expected_return < 0.5:
                continue

            if price <= 0:
                continue

            allocated_won = int(amount_won * kelly_frac)
            qty = int(allocated_won / price)
            invest_amount = qty * price

            if qty <= 0:
                continue

            picks_data.append({
                "name": r.name,
                "ticker": r.ticker,
                "price": price,
                "score": r.score.composite,
                "rsi": getattr(r.tech, 'rsi', 50),
                "macd": getattr(r.tech, 'macd', 0),
                "bb_pct": getattr(r.tech, 'bb_pct', 0.5),
                "ma5": getattr(r.tech, 'ma5', 0),
                "ma20": getattr(r.tech, 'ma20', 0),
                "ma60": getattr(r.tech, 'ma60', 0),
                "atr_pct": atr_pct,
                "risk_grade": risk_grade,
                "strategy": sig.strategy,
                "strategy_name": sig.strategy_name,
                "signal": sig.action,
                "confidence": sig.confidence,
                "reasons": sig.reasons or [],
                "quantity": qty,
                "invest_amount": invest_amount,
                "kelly_frac": kelly_frac,
                "expected_return": expected_return,
                "target_pct": target_pct,
                "stop_pct": stop_pct,
                "win_rate": win_rate,
            })

        if not picks_data:
            return (
                f"📋 {config['label']} 기간에 기대수익이 양수인 종목이 없습니다.\n\n"
                "현재 시장에서 해당 전략의 수익 기대가 거래비용보다 낮습니다.\n"
                "💡 오늘은 관망하시는 것이 합리적입니다.",
                [],
            )

        # 4. Claude Sonnet AI 분석
        analysis = await self._ai_analyze_buy_picks(
            picks_data, config, horizon, amount_만원, macro, regime,
        )

        # 5. 결과 메시지
        regime_emoji = regime.get("emoji", "")
        regime_label = regime.get("label", "")
        header = (
            f"📋 주호님 맞춤 매수 추천\n\n"
            f"💰 예산: {amount_만원}만원 | {config['label']}\n"
            f"📊 VIX: {macro.vix:.1f} | 나스닥: {macro.nasdaq_change_pct:+.1f}%\n"
            f"{regime_emoji} 시장 레짐: {regime_label}\n\n"
            f"{'━' * 22}\n\n"
        )

        # E[R] 요약
        top3 = picks_data[:3]
        avg_er = sum(p["expected_return"] for p in top3) / len(top3)
        max_loss = sum(
            abs(p["stop_pct"]) / 100 * p["invest_amount"]
            for p in top3
        )

        footer = (
            f"\n{'━' * 22}\n"
            f"⚠️ 참고용 분석이며 투자 지시가 아닙니다\n"
            f"📌 {config['hold_desc']}\n"
            f"💡 평균 E[R]: {avg_er:+.1f}% | 최대 손실: {max_loss:,.0f}원"
        )

        text = header + analysis + footer

        # 6. 버튼
        buttons = []
        for i, p in enumerate(top3):
            buttons.append([
                InlineKeyboardButton(
                    f"🔍 {i+1}번 상세분석",
                    callback_data=f"detail:{p['ticker']}",
                ),
                InlineKeyboardButton(
                    "⭐ 즐겨찾기",
                    callback_data=f"fav:add:{p['ticker']}:{p['name']}",
                ),
            ])
        buttons.append([
            InlineKeyboardButton("❌ 패스", callback_data="bp:dismiss"),
        ])

        return text, buttons

    async def _ai_analyze_buy_picks(
        self, picks: list, config: dict, horizon: str,
        amount_만원: int, macro, regime: dict,
    ) -> str:
        """Claude Sonnet으로 매수 추천 종목 정교한 분석."""
        if not self.anthropic_key:
            return self._format_picks_basic(picks, config, horizon)

        picks_text = ""
        for i, p in enumerate(picks[:3], 1):
            risk_info = ""
            if p.get("risk_grade"):
                rg = p["risk_grade"]
                risk_info = (
                    f"  ATR(20): {p['atr_pct']:.1f}% | 리스크: {rg['label']}\n"
                    f"  등급별 목표: +{rg['target_min']}~{rg['target_max']}% | "
                    f"손절: {rg['stop']}%\n"
                )
            picks_text += (
                f"\n종목 {i}: {p['name']} ({p['ticker']})\n"
                f"  현재가: {p['price']:,.0f}원 | 스코어: {p['score']:.0f}점\n"
                f"  RSI: {p['rsi']:.0f} | MACD: {p['macd']:+.0f} | "
                f"BB%: {p['bb_pct']:.2f}\n"
                f"  5일선: {p['ma5']:,.0f} | 20일선: {p['ma20']:,.0f} | "
                f"60일선: {p['ma60']:,.0f}\n"
                f"{risk_info}"
                f"  전략: {p['strategy_name']} ({p['strategy']}) | "
                f"신호: {p['signal']}\n"
                f"  매수근거: {', '.join(p['reasons'][:3])}\n"
                f"  Kelly 배분: {p['kelly_frac']:.0%} "
                f"({p['invest_amount']:,.0f}원, {p['quantity']}주)\n"
                f"  E[R]: {p['expected_return']:+.1f}% | "
                f"승률: {p['win_rate']:.0%}\n"
                f"  목표: +{p['target_pct']:.1f}% | "
                f"손절: {p['stop_pct']:.1f}%\n"
            )

        horizon_rules = {
            "scalp": (
                "초단기 당일 매매 전략이다.\n"
                "- ATR 기반 리스크 등급(A/B/C) 제공됨\n"
                "- Kelly 배분 비율 참고\n"
                "- 장 시작 30분 내 거래량 확인 후 진입\n"
                "- 갭업 5% 이상 추격 매수 금지\n"
                "- 14:30까지 목표 미달 시 종가 청산\n"
                "- RSI 70+ 종목 제외"
            ),
            "short": (
                "단기 3~5일 보유 전략이다.\n"
                "- 목표: +5~10%. 손절: -3%\n"
                "- 3거래일 내 +3% 미만이면 본전 매도 검토\n"
                "- 이동평균선 지지/저항 기준으로 매수 범위 제시"
            ),
            "mid": (
                "중기 1~3개월 보유 전략이다.\n"
                "- 목표: +10~15%. 손절: -7%\n"
                "- 60일 이동평균선 위 종목 우선\n"
                "- 섹터 로테이션 흐름과 매크로 중심 판단"
            ),
            "long": (
                "장기 6개월+ 보유 전략이다.\n"
                "- 목표: +15~30%. 손절: -10%\n"
                "- 펀더멘털(PER, ROE, 배당) 중심\n"
                "- 분할 매수 계획 제시 (1/3씩 3회)"
            ),
        }

        rules = horizon_rules.get(horizon, "")

        prompt = (
            f"주호님이 오늘 {amount_만원}만원으로 {config['label']} 매수를 계획.\n\n"
            f"[시장 상황]\n"
            f"VIX: {macro.vix:.1f} | S&P500: {macro.spx_change_pct:+.2f}% | "
            f"나스닥: {macro.nasdaq_change_pct:+.2f}%\n"
            f"원/달러: {macro.usdkrw:,.0f}원 | 레짐: {regime['label']}\n\n"
            f"[투자 기간 규칙]\n{rules}\n\n"
            f"[후보 종목 데이터]\n{picks_text}\n\n"
            f"위 후보 중 최적 3종목 선정하여 아래 형식으로 추천.\n"
            f"E[R] 높은 종목 우선. 리스크 분산 고려.\n"
            f"시장 불안하면 '오늘은 관망' 권고.\n\n"
            f"형식 (종목당):\n"
            f"[번호 이모지] 종목명 (코드) [리스크 등급]\n"
            f"   현재가: X원 | 점수: X점\n"
            f"   [핵심 기술지표 1줄]\n"
            f"   🟢 매수: 가격범위 (수량, 금액)\n"
            f"   🎯 목표: 가격 (+수익률%)\n"
            f"   🔴 손절: 가격 (-하락률%)\n"
            f"   📊 배분: X% (Kelly) | E[R]: +X.X%\n"
            f"   💡 실전 팁 1줄\n\n"
            f"볼드(**) 사용 금지. 한 문장 25자 이내. 이모지로 구분."
        )

        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
            response = await client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2000,
                temperature=0.2,
                system=(
                    "너는 주호님의 전속 투자 참모 '퀀트봇'이다.\n"
                    "CFA/CAIA 자격 + 계량금융 전문가.\n\n"
                    "[절대 규칙]\n"
                    "1. 매도/매수 '지시' 금지. '검토해보세요' 식으로\n"
                    "2. 공포 유발 표현 금지\n"
                    "3. 제공된 데이터만 사용. 과거 가격 사용 금지\n"
                    "4. 볼드(**) 사용 금지. 이모지로 구분\n"
                    "5. 초단기는 당일 청산. 오버나잇 경고\n"
                    "6. Kelly 배분과 E[R] 근거로 배분\n"
                    "7. C등급(ATR>4%)은 '고위험' 경고 필수\n"
                    "8. 손실 비대칭성: -10%는 +11.1% 필요"
                ),
                messages=[{"role": "user", "content": prompt}],
            )

            from kstock.bot.chat_handler import _sanitize_response
            return _sanitize_response(response.content[0].text)

        except Exception as e:
            logger.error("Buy planner AI error: %s", e)
            return self._format_picks_basic(picks, config, horizon)

    def _format_picks_basic(self, picks: list, config: dict, horizon: str) -> str:
        """AI 없을 때 기본 포맷."""
        emojis = ["1️⃣", "2️⃣", "3️⃣"]
        lines = []
        for i, p in enumerate(picks[:3]):
            rg = p.get("risk_grade")
            risk_label = f" [{rg['label']}]" if rg else ""
            target_price = int(p['price'] * (1 + p['target_pct'] / 100))
            stop_price = int(p['price'] * (1 + p['stop_pct'] / 100))
            lines.append(
                f"{emojis[i]} {p['name']} ({p['ticker']}){risk_label}\n"
                f"   현재가: {p['price']:,.0f}원 | 점수: {p['score']:.0f}점\n"
                f"   RSI {p['rsi']:.0f} | ATR {p['atr_pct']:.1f}%\n"
                f"   🟢 매수: {p['price']:,.0f}원 ({p['quantity']}주)\n"
                f"   🎯 목표: {target_price:,.0f}원 (+{p['target_pct']:.1f}%)\n"
                f"   🔴 손절: {stop_price:,.0f}원 ({p['stop_pct']:.1f}%)\n"
                f"   📊 배분: {p['kelly_frac']:.0%} | "
                f"E[R]: {p['expected_return']:+.1f}%"
            )
        return "\n\n".join(lines)

    # == Scheduled Jobs ======================================================


