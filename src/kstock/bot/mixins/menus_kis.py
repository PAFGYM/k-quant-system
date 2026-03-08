"""Menu handlers + KIS integration."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403


class MenusKisMixin:
    async def _menu_usage_guide(self, update: Update, context) -> None:
        msg = (
            "📖 주호님, K-Quant v9.1 사용법\n\n"

            "━━ 메인 메뉴 ━━\n\n"

            "📊 분석: 종목 분석 허브\n"
            "💰 잔고: 보유종목 + 총평가\n"
            "📈 시황: 미국/한국 시장 현황\n"
            "⭐ 즐겨찾기: 관심종목 관리\n"
            "💬 AI비서: 뭐든 물어보세요\n"
            "⚙️ 더보기: 전체 기능 메뉴\n\n"

            "━━ 종목 분석 (3가지 방법) ━━\n\n"

            "1️⃣ 종목명 입력\n"
            "  삼성전자 → [분석] [추가] [관심]\n\n"

            "2️⃣ 스크린샷 전송\n"
            "  증권사 캡처 → 자동 인식 + 등록\n\n"

            "3️⃣ 분석 허브 버튼\n"
            "  📊분석 → 멀티분석/급등주/스윙\n\n"

            "━━ 🆕 매수 플래너 (07:50) ━━\n\n"

            "매일 07:50 장 시작 전 매수 알림\n"
            "  [매수 계획 있음] → 금액 입력\n"
            "  → 투자 기간 선택 (초단기~장기)\n"
            "  → AI가 종목 추천 + Kelly 배분\n"
            "  → 장바구니로 매수 확정\n\n"

            "━━ 🆕 4인 투자 매니저 ━━\n\n"

            "⚡ 제시 리버모어: 초단기 담당\n"
            "🔥 윌리엄 오닐: 단기 스윙 담당\n"
            "📊 피터 린치: 중기 포지션 담당\n"
            "💎 워렌 버핏: 장기 가치 담당\n"
            "  → 보유종목별 전담 매니저 배정\n"
            "  → 아침 브리핑에 매니저별 코멘트\n"
            "  → 매도 알림에 매니저 표시\n\n"

            "━━ 🆕 실시간 코칭 ━━\n\n"

            "WebSocket 실시간 가격 모니터링\n"
            "  +3% 급등 감지 → 즉시 알림\n"
            "  목표가 도달 → 매도 가이드\n"
            "  손절가 도달 → 손절 안내\n"
            "  14:30 초단기 청산 리마인더\n"
            "  08:00 단기 3일 미수익 리뷰\n\n"

            "━━ 🆕 백테스트 프로 ━━\n\n"

            "/backtest [종목코드]\n"
            "  수수료+세금+슬리피지 반영\n"
            "  포트폴리오 백테스트 (MDD, 샤프)\n"
            "  에쿼티 커브 생성\n\n"

            "━━ 🆕 리스크 엔진 ━━\n\n"

            "고급 리스크 분석 (💰잔고 → 리스크)\n"
            "  Historical/Parametric VaR\n"
            "  Monte Carlo 시뮬레이션\n"
            "  5대 위기 스트레스 테스트\n"
            "  리스크 등급 A~F\n\n"

            "━━ 🆕 LSTM 딥러닝 ━━\n\n"

            "LSTM + Attention 시계열 예측\n"
            "  LightGBM + XGBoost + LSTM 앙상블\n"
            "  일요일 03:00 자동 재학습\n\n"

            "━━ 투자 기능 (⚙️더보기) ━━\n\n"

            "📸 계좌분석: 스크린샷 AI 진단\n"
            "🎯 전략별 보기: 7가지 전략\n"
            "🔥 급등주: +5% 급등 포착\n"
            "⚡ 스윙 기회: 단기 매매 추천\n"
            "📊 멀티분석: AI 5개 관점\n"
            "📋 리포트: 증권사 리포트\n"
            "📅 주간 보고서: 일요일 생성\n"
            "🕵 매집탐지: 세력 매집 감지\n"
            "🚀 미래기술: 텐배거 후보\n"
            "📊 공매도: 공매도/레버리지\n"
            "🎯 30억 목표: 자산 로드맵\n"
            "📊 재무 진단: 100점 분석\n\n"

            "━━ Multi-AI 엔진 ━━\n\n"

            "🟣 Claude: 심층분석, OCR, 전략\n"
            "🔵 GPT: 기술분석, 구조화 데이터\n"
            "🟢 Gemini: 뉴스감성, 빠른요약\n"
            "  → 태스크별 최적 AI 자동 선택\n\n"

            "━━ KIS 연동 (📡 KIS설정) ━━\n\n"

            "💰 실시간 잔고 + 호가 스트리밍\n"
            "📊 수급 분석: 외인/기관 동향\n"
            "🔔 가격 알림: 목표가/손절가\n"
            "📈 매수 스캔: 시그널 탐색\n\n"

            "━━ 자동 알림 (하루 일과) ━━\n\n"

            "03:00 🧠 LSTM 모델 재학습 (일)\n"
            "07:00 🇺🇸 미국 프리마켓 브리핑\n"
            "07:30 ☀️ 모닝 브리핑 + 매니저 코멘트\n"
            "07:50 🛒 매수 플래너\n"
            "08:00 📰 뉴스 감성 분석\n"
            "08:00 📋 단기 미수익 리뷰 (평일)\n"
            "08:20 📋 증권사 리포트 크롤링\n"
            "08:50 📡 WebSocket 연결 (62종목)\n"
            "09:00~ 장중 모니터링 (1분마다)\n"
            "14:30 ⚡ 초단기 청산 리마인더\n"
            "16:00 📊 장마감 PDF (4페이지)\n"
            "21:00 🔧 자가진단\n"
            "일요일 19:00 주간 보고서\n\n"

            "━━ 꿀팁 ━━\n\n"

            "종목명만 치면 바로 분석!\n"
            "스크린샷 한 장이면 포트폴리오 완성\n"
            "아무 질문이나 하면 AI가 답변\n"
            "62개 종목 실시간 WebSocket 감시"
        )
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

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
        buttons.append(make_feedback_row("알림설정"))
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
        buttons.append(make_feedback_row("알림설정"))
        await safe_edit_or_reply(query,
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
            make_feedback_row("리포트"),
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
        stock_name = r.get("summary", "")
        ticker = r.get("ticker", "")

        if stock_name and ticker:
            lines = [f"📌 {stock_name}({ticker}) — {broker} ({date})"]
        elif stock_name:
            lines = [f"📌 {stock_name} — {broker} ({date})"]
        else:
            lines = [f"{broker} ({date})"]
        lines.append(f"  {title}")

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
            buttons.append([InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")])
            await safe_edit_or_reply(query,
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

        await safe_edit_or_reply(query,msg)

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
        await safe_edit_or_reply(query,msg)

    # == Weekly report menu ====================================================

    async def _menu_weekly_report(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """주간 보고서 서브 메뉴."""
        buttons = [
            [InlineKeyboardButton("이번 주 보고서", callback_data="weekly:latest")],
            [InlineKeyboardButton("지난 보고서", callback_data="weekly:history")],
            [InlineKeyboardButton("즉시 생성", callback_data="weekly:generate")],
            make_feedback_row("주간보고서"),
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
            await safe_edit_or_reply(query,msg)

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
            await safe_edit_or_reply(query,msg)

        elif payload == "generate":
            await safe_edit_or_reply(query,"\U0001f50d 주간 보고서 생성 중... 잠시만 기다려주세요.")
            try:
                from kstock.bot.weekly_report import generate_weekly_report
                telegram_msg, doc_url = await generate_weekly_report(self.db)
                await query.message.reply_text(telegram_msg, reply_markup=get_reply_markup(context))
            except Exception as e:
                logger.error("Weekly report generation failed: %s", e, exc_info=True)
                await query.message.reply_text(
                    "\u26a0\ufe0f 주간 보고서 생성 실패. 잠시 후 다시 시도해주세요.",
                    reply_markup=get_reply_markup(context),
                )

    # == Menu implementations ================================================

    async def _menu_alerts(self, update: Update, context) -> None:
        alerts = self.db.get_recent_alerts(limit=10)
        await update.message.reply_text(
            format_alerts_summary(alerts), reply_markup=get_reply_markup(context)
        )

    async def _menu_recommendations(self, update: Update, context) -> None:
        await update.message.reply_text(
            "\U0001f50d 종목 분석 중... 잠시만 기다려주세요."
        )
        results = await self._scan_all_stocks()
        self._last_scan_results = results
        self._scan_cache_time = datetime.now(KST)

        # v9.4: debate 뱃지 추가
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
                r.strategy_type, r.info.current_price, "", debate_badge, _atr,
            ))
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
        buttons.append(make_feedback_row("매수추천"))
        keyboard = InlineKeyboardMarkup(buttons)
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
            alert_mode=getattr(self, '_alert_mode', 'normal'),
        )

        # v9.0: 프로그램 매매 데이터
        try:
            prog_data = self.db.get_program_trading(days=3, market="KOSPI")
            if prog_data:
                from kstock.ingest.program_trading import analyze_program_trading, format_program_trading, ProgramTradingData
                ptd_list = [ProgramTradingData(
                    date=d["date"], market=d["market"],
                    arb_buy=d["arb_buy"], arb_sell=d["arb_sell"], arb_net=d["arb_net"],
                    non_arb_buy=d["non_arb_buy"], non_arb_sell=d["non_arb_sell"],
                    non_arb_net=d["non_arb_net"],
                    total_buy=d["total_buy"], total_sell=d["total_sell"],
                    total_net=d["total_net"],
                ) for d in prog_data]
                analysis = analyze_program_trading(ptd_list)
                msg += "\n\n" + format_program_trading(analysis)
        except Exception:
            pass

        # v9.0: 신용잔고/예탁금
        try:
            cred_data = self.db.get_credit_balance(days=5)
            if cred_data:
                from kstock.ingest.credit_balance import analyze_credit_balance, format_credit_balance, CreditBalanceData
                cbd_list = [CreditBalanceData(
                    date=d["date"],
                    deposit=d["deposit"],
                    deposit_change=d["deposit_change"],
                    credit=d["credit"],
                    credit_change=d["credit_change"],
                ) for d in cred_data]
                analysis = analyze_credit_balance(cbd_list)
                msg += "\n\n" + format_credit_balance(analysis)
        except Exception:
            pass

        # v9.0: ETF 자금흐름
        try:
            etf_data = self.db.get_etf_flow(days=1)
            if etf_data:
                from kstock.signal.etf_flow import analyze_etf_flow, format_etf_flow, ETFFlowData
                current = [ETFFlowData(
                    code=d["code"], name=d["name"], etf_type=d["etf_type"],
                    price=d["price"], change_pct=d["change_pct"],
                    nav=d["nav"], market_cap=d["market_cap"], volume=d["volume"],
                ) for d in etf_data]
                prev_data = self.db.get_etf_flow_previous()
                analysis = analyze_etf_flow(current, prev_data)
                msg += "\n\n" + format_etf_flow(analysis)
        except Exception:
            pass

        # v9.0: 한국형 리스크 종합
        try:
            from kstock.signal.korea_risk import assess_korea_risk, format_korea_risk
            kr_args = {}
            if macro:
                kr_args["vix"] = getattr(macro, "vix", 0)
                kr_args["usdkrw"] = getattr(macro, "usdkrw", 0)
                kr_args["usdkrw_change_pct"] = getattr(macro, "usdkrw_change_pct", 0)
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
            from datetime import datetime as _dt
            from kstock.core.tz import KST as _KST
            _now = _dt.now(_KST)
            kr_args["month"] = _now.month
            kr_args["day"] = _now.day
            assessment = assess_korea_risk(**kr_args)
            if assessment.total_risk > 0:
                msg += "\n\n" + format_korea_risk(assessment)
        except Exception:
            pass

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
            logger.debug("_menu_portfolio edit_text failed, falling back", exc_info=True)
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

        # Phase 8: 실시간 보고서도 별도 전송 (AI 요약 포함)
        if live_report:
            buttons = [
                [InlineKeyboardButton(
                    "\U0001f4cb 매도 계획 보기", callback_data="sell_plans",
                ),
                InlineKeyboardButton(
                    "🎙️ AI토론", callback_data="menu:debate",
                )],
                make_feedback_row("시황"),
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
                logger.debug("_menu_portfolio_detail price update failed for %s", h.get("ticker"), exc_info=True)
        msg = format_portfolio(holdings, db=self.db)

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
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

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
                logger.debug("_menu_reco_performance price update failed for %s", r.get("ticker"), exc_info=True)
        msg = format_reco_performance(active, completed, watch, stats)
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

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
            make_feedback_row("전략"),
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
        buttons.append(make_feedback_row("최적화"))
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
            reply_markup=get_reply_markup(context),
        )

    async def _menu_help(self, update: Update, context) -> None:
        await update.message.reply_text(format_help(), reply_markup=get_reply_markup(context))

    async def _menu_account_analysis(self, update: Update, context) -> None:
        msg = format_screenshot_reminder()
        await update.message.reply_text(msg, reply_markup=get_reply_markup(context))

    async def _menu_kis_setup(self, update: Update, context) -> None:
        # KIS API 토큰 연결 확인 (실제 API 호출)
        kis_live = False
        kis_error = ""
        if self.kis._is_configured:
            try:
                kis_live = await self.kis._ensure_token()
            except Exception as e:
                logger.debug("KIS token ensure failed: %s", e)
                kis_error = "연결 오류 - 잠시 후 다시 시도해주세요"

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
                make_feedback_row("KIS설정"),
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
            await safe_edit_or_reply(query,
                "🔧 KIS 설정을 시작합니다.\n\n"
                "1/4 단계: HTS ID를 입력하세요.\n"
                "(한국투자증권 로그인 ID)\n\n"
                "예: hongildong"
            )
        elif payload == "test":
            await safe_edit_or_reply(query,"🧪 연결 테스트 중...")
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
                buttons = [
                    [InlineKeyboardButton(
                        "🔑 키 재설정", callback_data="kis:setup",
                    )],
                ]
                await query.message.reply_text(
                    "❌ 연결 테스트 실패\n\n"
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
            await safe_edit_or_reply(query,"📡 '📡 KIS설정' 메뉴를 눌러주세요.")
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
            await safe_edit_or_reply(query,
                guide, reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "balance":
            await safe_edit_or_reply(query,"💰 실시간 잔고 조회 중...")
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
                                logger.debug("_action_kis_balance day_info fetch failed", exc_info=True)
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
                                logger.debug("_action_kis_balance DB fallback price_detail failed for %s", ticker, exc_info=True)
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
                    "❌ 잔고 조회에 실패했어요.\n"
                    "DB 기반 잔고는 '💰 잔고' 메뉴에서 확인하세요.",
                )
            return

        if action == "supply":
            await safe_edit_or_reply(query,"📊 수급 분석 중...")
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
                    if foreign is not None and not foreign.empty and "net_buy_volume" in foreign.columns:
                        f_net = int(foreign["net_buy_volume"].sum())
                    if inst is not None and not inst.empty and "net_buy_volume" in inst.columns:
                        i_net = int(inst["net_buy_volume"].sum())

                    f_emoji = "🔵" if f_net > 0 else "🔴" if f_net < 0 else "⚪"
                    i_emoji = "🔵" if i_net > 0 else "🔴" if i_net < 0 else "⚪"
                    lines.append(
                        f"\n[{name}]\n"
                        f"  {f_emoji} 외인 3일: {f_net:+,}주\n"
                        f"  {i_emoji} 기관 3일: {i_net:+,}주"
                    )
                except Exception:
                    logger.debug("_action_supply_demand data fetch failed for %s", name, exc_info=True)
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
                await safe_edit_or_reply(query,
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

            await safe_edit_or_reply(query,
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "scan":
            await safe_edit_or_reply(query,"📈 매수 시그널 스캔 중...")
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
                await safe_edit_or_reply(query,
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
            await safe_edit_or_reply(query,"\n".join(lines))
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
                await safe_edit_or_reply(query,"⚠️ 종목 정보가 없습니다.")
                return

            holding = self.db.get_holding_by_ticker(ticker)
            name = holding.get("name", ticker) if holding else ticker
            cur = holding.get("current_price", 0) if holding else 0
            if cur == 0:
                try:
                    cur = await self._get_price(ticker, 0)
                except Exception:
                    logger.debug("_action_alert_setup get_price failed for %s", ticker, exc_info=True)

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

            await safe_edit_or_reply(query,
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
                    logger.debug("_action_alert_set get_price failed for %s", ticker, exc_info=True)

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
                await safe_edit_or_reply(query,
                    f"✅ 알림 설정 완료!\n\n"
                    f"{emoji} {name}\n"
                    f"현재가: {cur:,.0f}원\n"
                    f"{label}: {target:,.0f}원\n\n"
                    f"도달 시 텔레그램으로 알림을 보내드립니다."
                )
            except Exception as e:
                logger.error("Alert setup error: %s", e)
                await safe_edit_or_reply(query,
                    "❌ 알림 설정에 실패했어요. 잠시 후 다시 시도해주세요."
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
            await safe_edit_or_reply(query,"⚠️ 설정 데이터가 없습니다. 다시 시도해주세요.")
            return

        hts_id = setup_data.get("id", "")
        app_key = setup_data.get("key", "")
        app_secret = setup_data.get("secret", "")
        account = setup_data.get("account", "")
        is_virtual = payload == "virtual"
        mode_text = "모의투자" if is_virtual else "실전투자"

        context.user_data.pop("kis_setup", None)

        if not all([hts_id, app_key, app_secret, account]):
            await safe_edit_or_reply(query,
                "⚠️ 입력값이 부족합니다. 다시 시도해주세요."
            )
            return

        await safe_edit_or_reply(query,f"⏳ {mode_text} 모드로 설정 중...")

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
                logger.debug("_action_kis_setup token test price fetch failed", exc_info=True)

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


