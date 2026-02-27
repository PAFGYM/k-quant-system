"""Admin, favorites, agents, v3.6 features."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403


def _holding_type_to_horizon(ht: str) -> str:
    """holding_type → watchlist horizon 변환."""
    mapping = {
        "scalp": "scalp", "danta": "scalp",
        "swing": "swing",
        "position": "position", "dangi": "position", "junggi": "position",
        "long_term": "long_term", "janggi": "long_term",
    }
    return mapping.get(ht, "")


def _horizon_to_manager(ht: str) -> str:
    """holding_type → manager key 변환."""
    mapping = {
        "scalp": "scalp", "danta": "scalp",
        "swing": "swing",
        "position": "position", "dangi": "position", "junggi": "position",
        "long_term": "long_term", "janggi": "long_term",
    }
    return mapping.get(ht, "")


def _admin_buttons() -> list:
    """관리자 메뉴 인라인 버튼 생성."""
    return [
        [
            InlineKeyboardButton("\U0001f41b 오류 신고", callback_data="adm:bug"),
            InlineKeyboardButton("\U0001f4ca 봇 상태", callback_data="adm:status"),
        ],
        [
            InlineKeyboardButton("\U0001f4cb 보유종목 DB", callback_data="adm:holdings"),
            InlineKeyboardButton("\U0001f6a8 에러 로그", callback_data="adm:logs"),
        ],
        [
            InlineKeyboardButton("\U0001f4a1 업데이트 요청", callback_data="adm:request"),
        ],
        [
            InlineKeyboardButton("\U0001f512 보안 감사", callback_data="adm:security"),
            InlineKeyboardButton("\U0001f916 AI 상태", callback_data="ai:status"),
        ],
        [
            InlineKeyboardButton("\U0001f512 메뉴 닫기", callback_data="adm:close"),
        ],
    ]


# v5.2: 오류 신고 자주하는 질문(FAQ) 메뉴
def _bug_faq_buttons() -> list:
    """오류 신고 FAQ 인라인 버튼 생성."""
    return [
        [InlineKeyboardButton("📊 추천 종목 주가 오류", callback_data="adm:faq:price")],
        [InlineKeyboardButton("💬 AI 응답 이상", callback_data="adm:faq:ai_response")],
        [InlineKeyboardButton("📈 브리핑/알림 미발송", callback_data="adm:faq:notification")],
        [InlineKeyboardButton("💰 잔고 데이터 불일치", callback_data="adm:faq:balance")],
        [InlineKeyboardButton("🔧 기타 오류 (직접 작성)", callback_data="adm:faq:custom")],
        [
            InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu"),
            InlineKeyboardButton("❌ 종료", callback_data="adm:close"),
        ],
    ]


class AdminExtrasMixin:
    async def _menu_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🛠 관리자 메뉴 버튼 — 인라인 버튼으로 관리 기능 제공."""
        await update.message.reply_text(
            "\U0001f6e0 관리자 모드 (v3.6)\n\n"
            "아래 버튼을 눌러주세요.\n"
            "오류 신고 시 메시지나 스크린샷을\n"
            "바로 보내면 됩니다!",
            reply_markup=InlineKeyboardMarkup(_admin_buttons()),
        )

    async def _handle_admin_callback(
        self, query, context, payload: str
    ) -> None:
        """관리자 콜백 핸들러."""
        import json as _json

        admin_log_path = Path("data/admin_reports.jsonl")
        admin_log_path.parent.mkdir(parents=True, exist_ok=True)

        subcmd = payload.split(":")[0] if payload else ""

        back_btn = [[InlineKeyboardButton("\U0001f519 관리자 메뉴", callback_data="adm:menu")]]

        if subcmd == "bug":
            # v5.2: 오류 신고 FAQ 메뉴 표시
            await query.edit_message_text(
                "\U0001f41b 오류 신고\n\n"
                "자주 발생하는 오류를 선택하거나\n"
                "직접 작성해주세요.\n\n"
                "스크린샷 + 설명을 함께 보내면\n"
                "더 빠르게 수정됩니다!",
                reply_markup=InlineKeyboardMarkup(_bug_faq_buttons()),
            )

        elif subcmd == "request":
            # 업데이트 요청 모드
            context.user_data["admin_mode"] = "update_request"
            await query.edit_message_text(
                "\U0001f4a1 업데이트 요청 모드\n\n"
                "원하는 기능이나 개선사항을\n"
                "메시지로 보내주세요!\n\n"
                "Claude Code에서 확인 후 구현합니다.",
                reply_markup=InlineKeyboardMarkup(back_btn),
            )

        elif subcmd == "faq":
            # v5.2: FAQ 선택 → 자동 오류 기록 또는 직접 작성 모드
            faq_type = payload.split(":", 1)[1] if ":" in payload else payload
            faq_messages = {
                "price": "추천 종목의 주가가 실제와 다릅니다 (작년 주가 등)",
                "ai_response": "AI 응답이 엉뚱하거나 프로그래밍 답변이 나옵니다",
                "notification": "아침 브리핑이나 알림이 발송되지 않았습니다",
                "balance": "잔고 데이터가 실제와 다릅니다",
            }
            if faq_type in faq_messages:
                # FAQ 선택 → 자동 기록 + 추가 설명 요청
                auto_msg = faq_messages[faq_type]
                context.user_data["admin_mode"] = "bug_report"
                context.user_data["admin_faq_type"] = faq_type
                # 자동 기록
                report = {
                    "type": "bug_report",
                    "message": f"[FAQ:{faq_type}] {auto_msg}",
                    "has_image": False,
                    "timestamp": datetime.now(KST).isoformat(),
                    "status": "open",
                    "faq_type": faq_type,
                }
                with open(admin_log_path, "a", encoding="utf-8") as f:
                    f.write(_json.dumps(report, ensure_ascii=False) + "\n")
                close_btn = [[
                    InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu"),
                    InlineKeyboardButton("❌ 종료", callback_data="adm:close"),
                ]]
                await query.edit_message_text(
                    f"\U0001f41b 오류 접수: {auto_msg}\n\n"
                    f"추가 설명이나 스크린샷을 보내주세요.\n"
                    f"(캡션에 설명을 넣으면 같이 기록됩니다)\n\n"
                    f"완료되면 아래 버튼을 눌러주세요.",
                    reply_markup=InlineKeyboardMarkup(close_btn),
                )
            elif faq_type == "custom":
                # 직접 작성 모드
                context.user_data["admin_mode"] = "bug_report"
                close_btn = [[
                    InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu"),
                    InlineKeyboardButton("❌ 종료", callback_data="adm:close"),
                ]]
                await query.edit_message_text(
                    "\U0001f41b 오류 신고 모드\n\n"
                    "오류 내용을 텍스트로 보내주세요.\n"
                    "스크린샷도 함께 보내면 더 좋습니다!\n"
                    "(캡션에 설명 추가 가능)\n\n"
                    "완료되면 아래 버튼을 눌러주세요.",
                    reply_markup=InlineKeyboardMarkup(close_btn),
                )

        elif subcmd == "menu":
            # 관리자 메뉴로 복귀
            context.user_data.pop("admin_mode", None)
            context.user_data.pop("admin_faq_type", None)
            await query.edit_message_text(
                "\U0001f6e0 관리자 모드 (v5.2)\n\n"
                "아래 버튼을 눌러주세요.",
                reply_markup=InlineKeyboardMarkup(_admin_buttons()),
            )

        elif subcmd == "close":
            # 관리자 메뉴 닫기 + 상태 초기화
            context.user_data.pop("admin_mode", None)
            context.user_data.pop("admin_faq_type", None)
            await query.edit_message_text("\U0001f6e0 관리자 메뉴를 닫았습니다.")

        elif subcmd == "security":
            # v3.6: 보안 감사
            audit_result = security_audit()
            await query.edit_message_text(
                audit_result,
                reply_markup=InlineKeyboardMarkup(back_btn),
            )

        elif subcmd == "status":
            holdings = self.db.get_active_holdings()
            chat_count = 0
            try:
                chat_count = self.db.get_chat_usage(_today())
            except Exception:
                pass
            uptime = datetime.now(KST) - self._start_time
            hours = uptime.seconds // 3600
            mins = (uptime.seconds % 3600) // 60

            # v3.6: AI + WebSocket 상태 추가
            ai_available = [n for n, p in self.ai.providers.items() if p.available]
            ai_text = ", ".join(ai_available) if ai_available else "없음"
            ws_text = "연결" if self.ws.is_connected else "미연결"
            ws_subs = len(self.ws.get_subscriptions())

            await query.edit_message_text(
                f"\U0001f4ca 봇 상태 (v3.6)\n\n"
                f"\u2705 가동: {hours}시간 {mins}분\n"
                f"\U0001f4b0 보유종목: {len(holdings)}개\n"
                f"\U0001f916 AI 채팅: {chat_count}회/50\n"
                f"\U0001f9e0 AI 엔진: {ai_text}\n"
                f"\U0001f4e1 WebSocket: {ws_text} ({ws_subs}종목)\n"
                f"\U0001f310 KIS: {'연결' if self.kis_broker.connected else '미연결'}\n"
                f"\U0001f4c5 날짜: {datetime.now(KST).strftime('%m/%d %H:%M')}",
                reply_markup=InlineKeyboardMarkup(back_btn),
            )

        elif subcmd == "holdings":
            holdings = self.db.get_active_holdings()
            if not holdings:
                await query.edit_message_text(
                    "\U0001f4ad DB에 보유종목 없음\n잔고 스크린샷을 보내주세요!",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )
                return
            lines = [f"\U0001f4ca 보유종목 ({len(holdings)}개)\n"]
            for h in holdings[:10]:
                pnl = h.get("pnl_pct", 0)
                e = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
                lines.append(
                    f"{e} {h.get('name', '')} {pnl:+.1f}%"
                )
            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(back_btn),
            )

        elif subcmd == "logs":
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-50", "bot.log"],
                    capture_output=True, text=True, timeout=5,
                )
                errors = [
                    l.strip()[-90:]
                    for l in result.stdout.splitlines()
                    if "ERROR" in l
                ][-8:]
                if errors:
                    await query.edit_message_text(
                        "\U0001f6a8 최근 에러\n\n" + "\n\n".join(errors),
                        reply_markup=InlineKeyboardMarkup(back_btn),
                    )
                else:
                    await query.edit_message_text(
                        "\u2705 에러 없음!",
                        reply_markup=InlineKeyboardMarkup(back_btn),
                    )
            except Exception as e:
                await query.edit_message_text(
                    f"\u26a0\ufe0f 로그 확인 실패: {e}",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )

    async def _save_admin_report(
        self, update: Update, report_type: str, text: str, has_image: bool = False,
    ) -> None:
        """관리자 리포트를 파일에 저장 (Claude Code 모니터링용)."""
        import json as _json
        admin_log_path = Path("data/admin_reports.jsonl")
        admin_log_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "type": report_type,
            "message": text,
            "has_image": has_image,
            "timestamp": datetime.now(KST).isoformat(),
            "status": "open",
        }

        # 이미지가 있으면 파일 ID 기록
        if has_image and update.message.photo:
            report["photo_file_id"] = update.message.photo[-1].file_id

        with open(admin_log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(report, ensure_ascii=False) + "\n")

        type_label = "\U0001f41b 오류 신고" if report_type == "bug_report" else "\U0001f4a1 업데이트 요청"
        await update.message.reply_text(
            f"{type_label} 접수 완료!\n\n"
            f"\U0001f4dd {text[:200]}\n"
            f"\U0001f4f7 이미지: {'있음' if has_image else '없음'}\n"
            f"\u23f0 {datetime.now(KST).strftime('%H:%M:%S')}\n\n"
            f"Claude Code에서 확인 후\n"
            f"즉시 수정/반영됩니다!",
            reply_markup=InlineKeyboardMarkup(_admin_buttons()),
        )

    async def cmd_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """관리자 모드 — 오류 보고 + 봇 상태 확인 + Claude Code 연동.

        사용법:
            /admin bug <에러 내용>     → 버그 리포트 기록
            /admin status              → 봇 상태 종합
            /admin logs                → 최근 에러 로그
            /admin restart             → 봇 재시작 요청
            /admin holdings            → 보유종목 DB 현황
        """
        self._persist_chat_id(update)
        args = context.args or []
        admin_log_path = Path("data/admin_reports.jsonl")
        admin_log_path.parent.mkdir(parents=True, exist_ok=True)

        if not args:
            await update.message.reply_text(
                "\U0001f6e0 관리자 모드\n\n"
                "/admin bug <에러 내용> — 버그 리포트\n"
                "/admin status — 봇 상태\n"
                "/admin logs — 최근 에러\n"
                "/admin holdings — 보유종목 현황\n\n"
                "\U0001f4a1 버그를 보고하면 Claude Code가\n"
                "자동으로 감지하고 수정합니다.",
                reply_markup=get_reply_markup(context),
            )
            return

        subcmd = args[0].lower()

        if subcmd == "bug":
            # 버그 리포트를 파일로 기록 (Claude Code가 모니터링)
            bug_text = " ".join(args[1:]) if len(args) > 1 else "내용 없음"
            import json as _json
            report = {
                "type": "bug",
                "message": bug_text,
                "timestamp": datetime.now(KST).isoformat(),
                "chat_id": str(update.effective_chat.id),
                "status": "open",
            }
            with open(admin_log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(report, ensure_ascii=False) + "\n")
            # 최근 에러 로그도 첨부
            recent_errors = []
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-20", "bot.log"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if "ERROR" in line or "error" in line.lower():
                        recent_errors.append(line.strip()[-120:])
            except Exception:
                pass
            if recent_errors:
                report["recent_errors"] = recent_errors[-5:]
                with open(admin_log_path, "a", encoding="utf-8") as f:
                    f.write(_json.dumps({"type": "error_context", "errors": recent_errors[-5:]}, ensure_ascii=False) + "\n")

            await update.message.reply_text(
                f"\U0001f4e9 버그 리포트 접수 완료\n\n"
                f"내용: {bug_text[:200]}\n"
                f"시간: {datetime.now(KST).strftime('%H:%M:%S')}\n\n"
                f"\U0001f4c1 data/admin_reports.jsonl에 기록됨\n"
                f"Claude Code에서 확인 후 수정 예정",
                reply_markup=get_reply_markup(context),
            )

        elif subcmd == "status":
            # 봇 상태 종합
            holdings = self.db.get_active_holdings()
            jobs_today = 0
            try:
                today_str = _today()
                for job_name in ["morning_briefing", "sentiment_analysis", "daily_pdf_report"]:
                    jr = self.db.get_job_run(job_name, today_str)
                    if jr and jr.get("status") == "success":
                        jobs_today += 1
            except Exception:
                pass

            chat_count = 0
            try:
                chat_count = self.db.get_chat_usage(_today())
            except Exception:
                pass

            uptime = datetime.now(KST) - getattr(self, '_start_time', datetime.now(KST))
            lines = [
                "\U0001f4ca 봇 상태 종합\n",
                f"\u2705 가동시간: {uptime.seconds // 3600}시간 {(uptime.seconds % 3600) // 60}분",
                f"\U0001f4b0 보유종목: {len(holdings)}개",
                f"\U0001f916 오늘 AI 채팅: {chat_count}회",
                f"\u23f0 오늘 완료 작업: {jobs_today}/3",
                f"\U0001f4be DB: kquant.db",
                f"\U0001f310 KIS: {'연결됨' if self.kis_broker.connected else '미연결'}",
            ]
            await update.message.reply_text(
                "\n".join(lines), reply_markup=get_reply_markup(context),
            )

        elif subcmd == "logs":
            # 최근 에러 로그
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-50", "bot.log"],
                    capture_output=True, text=True, timeout=5,
                )
                error_lines = [
                    l.strip()[-100:]
                    for l in result.stdout.splitlines()
                    if "ERROR" in l or "WARNING" in l
                ][-10:]
                if error_lines:
                    await update.message.reply_text(
                        "\U0001f6a8 최근 에러/경고\n\n" + "\n".join(error_lines),
                        reply_markup=get_reply_markup(context),
                    )
                else:
                    await update.message.reply_text(
                        "\u2705 최근 에러 없음!", reply_markup=get_reply_markup(context),
                    )
            except Exception as e:
                await update.message.reply_text(
                    f"\u26a0\ufe0f 로그 확인 실패: {e}", reply_markup=get_reply_markup(context),
                )

        elif subcmd == "holdings":
            # 보유종목 DB 현황
            holdings = self.db.get_active_holdings()
            if not holdings:
                await update.message.reply_text(
                    "\U0001f4ad DB에 보유종목이 없습니다.\n"
                    "잔고 스크린샷을 찍어주세요!",
                    reply_markup=get_reply_markup(context),
                )
                return
            lines = [f"\U0001f4ca 보유종목 DB ({len(holdings)}개)\n"]
            for h in holdings:
                pnl = h.get("pnl_pct", 0)
                emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
                lines.append(
                    f"{emoji} {h.get('name', '')} ({h.get('ticker', '')})\n"
                    f"  매수 {h.get('buy_price', 0):,.0f} | "
                    f"현재 {h.get('current_price', 0):,.0f} | "
                    f"{pnl:+.1f}%"
                )
            await update.message.reply_text(
                "\n".join(lines), reply_markup=get_reply_markup(context),
            )

        else:
            await update.message.reply_text(
                f"\u26a0\ufe0f 알 수 없는 명령: {subcmd}\n"
                "/admin 으로 도움말 확인",
                reply_markup=get_reply_markup(context),
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
                    reply_markup=get_reply_markup(context),
                )
                return
            text = " ".join(args)
            trade = parse_trade_text(text)
            if not trade:
                await update.message.reply_text(
                    "\u26a0\ufe0f 매수 정보를 파싱하지 못했습니다.\n"
                    "예: /register 삼성전자 50주 76000원",
                    reply_markup=get_reply_markup(context),
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
            await update.message.reply_text(msg, reply_markup=get_reply_markup(context))
        except Exception as e:
            logger.error("Register command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 매수 등록 오류.", reply_markup=get_reply_markup(context),
            )

    async def cmd_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /balance - show portfolio balance from holdings + screenshots."""
        try:
            self._persist_chat_id(update)
            placeholder = await update.message.reply_text(
                "\U0001f4b0 잔고 조회 중..."
            )

            holdings = await self._load_holdings_with_fallback()

            if not holdings:
                empty_buttons = [[
                    InlineKeyboardButton(
                        "➕ 종목 추가", callback_data="bal:add",
                    ),
                ]]
                try:
                    await placeholder.edit_text(
                        "💰 주호님, 등록된 보유종목이 없습니다.\n\n"
                        "📸 스크린샷 전송 → 자동 인식\n"
                        "💬 종목명 입력 → 버튼으로 추가\n\n"
                        "아래 버튼을 눌러 시작하세요!",
                        reply_markup=InlineKeyboardMarkup(empty_buttons),
                    )
                except Exception:
                    pass
                return

            total_eval, total_invested = await self._update_holdings_prices(holdings)
            lines = self._format_balance_lines(holdings, total_eval, total_invested)
            bal_buttons = self._build_balance_buttons(holdings)
            try:
                await placeholder.edit_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(bal_buttons),
                )
            except Exception:
                await update.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(bal_buttons),
                )
        except Exception as e:
            logger.error("Balance command error: %s", e, exc_info=True)
            await update.message.reply_text(
                "\u26a0\ufe0f 잔고 조회 중 오류가 발생했습니다.", reply_markup=get_reply_markup(context),
            )

    # -- Phase 7 menu handlers ---------------------------------------------------

    async def _menu_multi_agent(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """멀티 에이전트 분석 메뉴 - 최근 결과 표시 + 빠른 분석 버튼."""
        # 최근 분석 결과 조회
        recent = self.db.get_multi_agent_results(limit=5)

        lines = ["\U0001f4ca 멀티 에이전트 분석\n"]

        if recent:
            lines.append("최근 분석 결과:")
            for r in recent:
                verdict_emoji = {
                    "매수": "\U0001f7e2", "홀딩": "\U0001f7e1",
                    "관망": "\u26aa", "매도": "\U0001f534",
                }.get(r.get("verdict", ""), "\u26aa")
                lines.append(
                    f"  {verdict_emoji} {r.get('name', '')} "
                    f"- {r.get('verdict', '관망')} "
                    f"({r.get('combined_score', 0)}/215)"
                )
            lines.append("")

        lines.append("종목명을 직접 입력하면 자동 분석됩니다.")
        lines.append("예: '삼성전자 분석' 또는 /multi 삼성전자")

        # 보유종목 기반 빠른 분석 버튼
        holdings = self.db.get_active_holdings()
        buttons = []
        for h in holdings[:4]:
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            if ticker and name:
                buttons.append([
                    InlineKeyboardButton(
                        f"\U0001f50d {name} 분석",
                        callback_data=f"multi_run:{ticker}",
                    )
                ])

        buttons.append(make_feedback_row("멀티분석"))
        keyboard = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=keyboard,
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

    # ── v3.6 신규 메뉴 핸들러 ────────────────────────────────────────

    async def _menu_more(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """더보기 메뉴 표시."""
        await update.message.reply_text(
            "\u2699\ufe0f 더보기 메뉴\n원하는 기능을 선택하세요:",
            reply_markup=MORE_MENU,
        )

    async def _menu_back_to_main(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """메인 메뉴로 복귀."""
        await update.message.reply_text(
            "\U0001f3e0 메인 메뉴로 돌아왔습니다.",
            reply_markup=get_reply_markup(context),
        )

    async def _menu_analysis_hub(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """분석 허브 — 종목명 입력 또는 빠른 분석 선택."""
        buttons = [
            [
                InlineKeyboardButton("📊 멀티분석", callback_data="hub:multi"),
                InlineKeyboardButton("🔥 급등주", callback_data="hub:surge"),
            ],
            [
                InlineKeyboardButton("⚡ 스윙기회", callback_data="hub:swing"),
                InlineKeyboardButton("🎯 매수추천", callback_data="quick_q:buy_pick"),
            ],
            [
                InlineKeyboardButton("📊 호가조회", callback_data="orderbook:select"),
                InlineKeyboardButton("🤖 AI상태", callback_data="ai:status"),
            ],
            make_feedback_row("분석허브"),
        ]
        await update.message.reply_text(
            "📊 분석 허브\n\n"
            "종목명을 직접 입력하거나\n"
            "아래 버튼으로 빠른 분석을 시작하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_hub(self, query, context, payload: str) -> None:
        """분석 허브 버튼 콜백 — 각 기능 직접 실행."""
        if payload == "surge":
            await query.edit_message_text("🔥 급등주 실시간 스캔 중...")
            # cmd_surge는 update.message를 사용하므로 직접 실행
            try:
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
                        avg_vol = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
                        cur_vol = float(volume.iloc[-1])
                        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0
                        if change_pct >= 3.0 or vol_ratio >= 2.0:
                            stocks_data.append({
                                "name": item["name"], "ticker": code,
                                "change_pct": change_pct, "volume_ratio": vol_ratio,
                            })
                    except Exception:
                        continue

                if not stocks_data:
                    await query.edit_message_text("🔥 현재 급등 조건을 충족하는 종목이 없습니다.")
                    return

                stocks_data.sort(key=lambda s: s["change_pct"], reverse=True)
                lines = [f"🔥 급등주 실시간 스캔 ({len(stocks_data)}종목 감지)\n"]
                for i, s in enumerate(stocks_data[:10], 1):
                    icon = "📈" if s["change_pct"] >= 5 else "🔥" if s["change_pct"] >= 3 else "⚡"
                    lines.append(
                        f"{i}. {icon} {s['name']}({s['ticker']}) "
                        f"{s['change_pct']:+.1f}% 거래량 {s['volume_ratio']:.1f}배"
                    )
                await query.edit_message_text("\n".join(lines))
            except Exception as e:
                logger.error("Hub surge error: %s", e, exc_info=True)
                await query.edit_message_text("⚠️ 급등주 스캔 중 오류가 발생했습니다.")

        elif payload == "swing":
            active_swings = self.db.get_active_swing_trades()
            if active_swings:
                lines = ["⚡ 활성 스윙 거래\n"]
                for sw in active_swings[:5]:
                    pnl = sw.get("pnl_pct", 0)
                    lines.append(
                        f"{sw['name']} {_won(sw['entry_price'])} → "
                        f"목표 {_won(sw.get('target_price', 0))} ({pnl:+.1f}%)"
                    )
                await query.edit_message_text("\n".join(lines))
            else:
                await query.edit_message_text(
                    "⚡ 현재 활성 스윙 거래가 없습니다.\n\n"
                    "스캔 중 조건 충족 종목 발견 시 알려드리겠습니다."
                )

        elif payload == "multi":
            # 멀티분석: 보유종목 버튼 표시
            holdings = self.db.get_active_holdings()
            buttons = []
            for h in holdings[:4]:
                ticker = h.get("ticker", "")
                name = h.get("name", "")
                if ticker and name:
                    buttons.append([InlineKeyboardButton(
                        f"🔍 {name} 분석", callback_data=f"multi_run:{ticker}",
                    )])
            if buttons:
                await query.edit_message_text(
                    "📊 멀티 에이전트 분석\n\n보유종목을 선택하세요:",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            else:
                await query.edit_message_text(
                    "📊 멀티분석\n\n종목명을 직접 입력하면 자동 분석됩니다.\n"
                    "예: 삼성전자 분석"
                )

    async def _action_ai_status(self, query, context, payload: str) -> None:
        """AI 엔진 상태 표시."""
        status = self.ai.get_status()
        routing = self.ai.get_routing_table()
        ws_status = self.ws.get_status()
        text = f"{status}\n\n{routing}\n\n\U0001f4e1 실시간: {ws_status}"
        await query.edit_message_text(text)

    async def _action_orderbook(self, query, context, payload: str) -> None:
        """호가 조회 액션."""
        if payload == "select":
            # 보유종목 목록에서 선택
            holdings = await self._load_holdings_with_fallback()
            if not holdings:
                await query.edit_message_text(
                    "\U0001f4ca 호가를 조회할 보유종목이 없습니다.\n종목코드를 직접 입력해주세요."
                )
                return
            buttons = []
            for h in holdings[:6]:
                ticker = h.get("ticker", "")
                name = h.get("name", ticker)
                if ticker:
                    buttons.append([InlineKeyboardButton(
                        f"\U0001f4ca {name}",
                        callback_data=f"orderbook:{ticker}",
                    )])
            await query.edit_message_text(
                "\U0001f4ca 호가 조회할 종목을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            # 특정 종목 호가 조회
            ticker = payload
            name = ticker
            for item in self.all_tickers:
                if item["code"] == ticker:
                    name = item["name"]
                    break

            await query.edit_message_text(f"\U0001f4ca {name} 호가 조회 중...")

            orderbook = None
            # WebSocket 데이터 우선
            if self.ws.is_connected:
                orderbook = self.ws.get_orderbook(ticker)

            # REST fallback
            if not orderbook:
                try:
                    orderbook = await self.ws.get_orderbook_rest(ticker)
                except Exception as e:
                    logger.warning("Orderbook REST failed: %s", e)

            if orderbook:
                text = orderbook.format_display(name)
                await query.message.reply_text(text)
            else:
                # 시뮬레이션 호가 (데이터 없을 때)
                try:
                    price = await self._get_price(ticker)
                    if price > 0:
                        text = (
                            f"\U0001f4ca {name} 호가 (추정)\n"
                            f"{'─' * 25}\n"
                            f"\U0001f534 매도 1호가: {price * 1.001:>12,.0f}원\n"
                            f"\U0001f7e2 매수 1호가: {price * 0.999:>12,.0f}원\n"
                            f"{'─' * 25}\n"
                            f"현재가: {price:,.0f}원\n\n"
                            "\u26a0\ufe0f 실시간 호가는 KIS WebSocket 연결 시 지원됩니다."
                        )
                    else:
                        text = f"\u26a0\ufe0f {name} 호가 데이터를 조회할 수 없습니다."
                except Exception:
                    text = f"\u26a0\ufe0f {name} 호가 조회 실패"
                await query.message.reply_text(text)

    # ── 즐겨찾기 메뉴 ──────────────────────────────────────────────

    def _resolve_name(self, ticker: str, fallback: str = "") -> str:
        """종목코드 → 종목명 변환. universe에서 조회."""
        for item in self.all_tickers:
            if item.get("code") == ticker:
                return item.get("name", fallback or ticker)
        return fallback if fallback and fallback != ticker else ticker

    async def _menu_favorites(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """⭐ 즐겨찾기 — watchlist 종목 표시 + 빠른 액션.

        v5.7: 추천시점 대비 수익률, 투자유형별 분류 필수, 담당 매니저 표시.
        """
        from kstock.bot.investment_managers import MANAGERS

        watchlist = self.db.get_watchlist()

        # [v3.6.2] 비어있으면 보유종목 자동 등록
        if not watchlist:
            holdings = await self._load_holdings_with_fallback()
            for h in holdings:
                ticker = h.get("ticker", "")
                name = h.get("name", "")
                bp = h.get("buy_price", 0)
                ht = h.get("holding_type", "")
                if ticker and name:
                    try:
                        # 매수가를 추천가격으로 기록
                        mgr = _horizon_to_manager(ht)
                        hz = _holding_type_to_horizon(ht)
                        self.db.add_watchlist(
                            ticker, name, rec_price=bp,
                            horizon=hz, manager=mgr,
                        )
                    except Exception:
                        pass
            watchlist = self.db.get_watchlist()

        if not watchlist:
            await update.message.reply_text(
                "⭐ 즐겨찾기가 비어있습니다.\n\n"
                "종목명을 입력하면 자동으로 추가할 수 있습니다.\n"
                "예: 삼성전자",
                reply_markup=get_reply_markup(context),
            )
            return

        # 종목별 데이터 수집
        items = []
        for w in watchlist[:15]:
            ticker = w.get("ticker", "")
            name = w.get("name", ticker)
            rec_price = w.get("rec_price", 0) or 0
            horizon = w.get("horizon", "") or ""
            manager = w.get("manager", "") or ""

            # 이름이 코드와 같으면 universe에서 이름 찾기
            if name == ticker or not name:
                name = self._resolve_name(ticker, name)
                if name != ticker:
                    try:
                        self.db.add_watchlist(ticker, name)
                    except Exception:
                        pass

            # 실시간 가격 조회
            cur = 0
            dc_pct = 0.0
            try:
                detail = await self._get_price_detail(ticker, 0)
                cur = detail["price"]
                dc_pct = detail["day_change_pct"]
            except Exception:
                pass

            # 추천가 대비 수익률
            rec_pnl = 0.0
            if rec_price > 0 and cur > 0:
                rec_pnl = (cur - rec_price) / rec_price * 100

            # 추천가 없으면 현재가를 기록
            if rec_price <= 0 and cur > 0:
                rec_price = cur
                try:
                    self.db.add_watchlist(ticker, name, rec_price=cur)
                except Exception:
                    pass

            items.append({
                "ticker": ticker, "name": name, "price": cur,
                "dc_pct": dc_pct, "rec_price": rec_price,
                "rec_pnl": rec_pnl, "horizon": horizon,
                "manager": manager,
            })

        # 투자유형별 그룹핑
        horizon_labels = {
            "scalp": "⚡ 단타 (1~3일) — 제시 리버모어",
            "swing": "🔥 스윙 (1~4주) — 윌리엄 오닐",
            "position": "📊 포지션 (1~6개월) — 피터 린치",
            "long_term": "💎 장기 (6개월+) — 워렌 버핏",
        }
        grouped = {}
        ungrouped = []
        for item in items:
            hz = item["horizon"]
            if hz and hz in horizon_labels:
                grouped.setdefault(hz, []).append(item)
            else:
                ungrouped.append(item)

        lines = ["⭐ 내 즐겨찾기\n"]
        buttons = []

        def _format_item(item):
            name = item["name"]
            cur = item["price"]
            dc_pct = item["dc_pct"]
            rec_pnl = item["rec_pnl"]
            if cur > 0:
                dc_sign = "+" if dc_pct > 0 else ""
                dc_emoji = "📈" if dc_pct > 0 else "📉" if dc_pct < 0 else "─"
                # 추천 대비 수익률
                pnl_sign = "+" if rec_pnl > 0 else ""
                pnl_emoji = "🟢" if rec_pnl > 0 else "🔴" if rec_pnl < 0 else "⚪"
                pnl_str = f" {pnl_emoji}{pnl_sign}{rec_pnl:.1f}%" if item["rec_price"] > 0 else ""
                return (
                    f"{dc_emoji} {name}: {cur:,.0f}원 "
                    f"(오늘 {dc_sign}{dc_pct:.1f}%){pnl_str}"
                )
            return f"─ {name}"

        # 그룹별 출력
        for hz_key in ["scalp", "swing", "position", "long_term"]:
            if hz_key not in grouped:
                continue
            mgr = MANAGERS.get(hz_key, {})
            mgr_name = mgr.get("name", "") if mgr else ""
            lines.append(f"\n{horizon_labels[hz_key]}")
            for item in grouped[hz_key]:
                lines.append(f"  {_format_item(item)}")
                buttons.append([
                    InlineKeyboardButton(
                        f"📋 {item['name'][:8]}", callback_data=f"detail:{item['ticker']}",
                    ),
                    InlineKeyboardButton(
                        "🔄 분류", callback_data=f"fav:classify:{item['ticker']}",
                    ),
                    InlineKeyboardButton(
                        "❌", callback_data=f"fav:rm:{item['ticker']}",
                    ),
                ])

        # 미분류 종목
        if ungrouped:
            lines.append("\n📌 미분류 (분류 필요!)")
            for item in ungrouped:
                lines.append(f"  {_format_item(item)}")
                buttons.append([
                    InlineKeyboardButton(
                        f"📋 {item['name'][:8]}", callback_data=f"detail:{item['ticker']}",
                    ),
                    InlineKeyboardButton(
                        "🔄 분류", callback_data=f"fav:classify:{item['ticker']}",
                    ),
                    InlineKeyboardButton(
                        "❌", callback_data=f"fav:rm:{item['ticker']}",
                    ),
                ])

        buttons.append([
            InlineKeyboardButton("➕ 종목 추가", callback_data="fav:add_mode"),
            InlineKeyboardButton("🔄 새로고침", callback_data="fav:refresh"),
        ])
        buttons.append([
            InlineKeyboardButton("👨‍💼 매니저 현황", callback_data="fav:managers"),
        ])
        buttons.append(make_feedback_row("즐겨찾기"))
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_favorites(self, query, context, payload: str = "") -> None:
        """즐겨찾기 콜백: fav:add:{ticker}:{name} / fav:rm:{ticker} / fav:refresh."""
        parts = payload.split(":")
        action = parts[0] if parts else ""

        if action == "add":
            ticker = parts[1] if len(parts) > 1 else ""
            name = parts[2] if len(parts) > 2 else ticker
            if ticker:
                self.db.add_watchlist(ticker, name)
                await query.edit_message_text(
                    f"⭐ {name}({ticker})을 즐겨찾기에 등록했습니다!\n\n"
                    "⭐ 즐겨찾기 메뉴에서 확인하세요."
                )
            return

        if action == "add_mode":
            # 종목 추가 모드: 채팅에 종목명 입력하라고 안내
            context.user_data["awaiting_fav_add"] = True
            await query.edit_message_text(
                "⭐ 종목 추가\n\n"
                "추가할 종목명을 채팅창에 입력하세요.\n"
                "예: 에코프로비엠, 삼성전자"
            )
            return

        if action == "rm":
            ticker = parts[1] if len(parts) > 1 else ""
            if ticker:
                name = self._resolve_name(ticker, ticker)
                self.db.remove_watchlist(ticker)
                await query.edit_message_text(f"⭐ {name} 즐겨찾기에서 삭제되었습니다.")
            return

        if action == "classify":
            # 종목 투자유형 분류 → 4개 버튼 표시
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            buttons = [
                [
                    InlineKeyboardButton("⚡ 단타", callback_data=f"fav:set_hz:scalp:{ticker}"),
                    InlineKeyboardButton("🔥 스윙", callback_data=f"fav:set_hz:swing:{ticker}"),
                ],
                [
                    InlineKeyboardButton("📊 포지션", callback_data=f"fav:set_hz:position:{ticker}"),
                    InlineKeyboardButton("💎 장기", callback_data=f"fav:set_hz:long_term:{ticker}"),
                ],
            ]
            await query.edit_message_text(
                f"🔄 {name} 투자유형 분류\n\n"
                f"⚡ 단타: 1~3일 (제시 리버모어)\n"
                f"🔥 스윙: 1~4주 (윌리엄 오닐)\n"
                f"📊 포지션: 1~6개월 (피터 린치)\n"
                f"💎 장기: 6개월+ (워렌 버핏)\n\n"
                f"투자유형을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "set_hz":
            # 투자유형 설정 확정
            horizon = parts[1] if len(parts) > 1 else ""
            ticker = parts[2] if len(parts) > 2 else ""
            name = self._resolve_name(ticker, ticker)
            manager = horizon  # horizon과 manager가 같은 키
            self.db.update_watchlist_horizon(ticker, horizon, manager)

            from kstock.bot.investment_managers import MANAGERS
            mgr = MANAGERS.get(horizon, {})
            mgr_name = mgr.get("name", "알 수 없음") if mgr else "알 수 없음"
            mgr_emoji = mgr.get("emoji", "📌") if mgr else "📌"

            await query.edit_message_text(
                f"✅ {name} 투자유형 설정 완료\n\n"
                f"유형: {mgr_emoji} {mgr.get('title', horizon)}\n"
                f"담당: {mgr_name}\n\n"
                f"⭐ 즐겨찾기에서 확인하세요."
            )
            return

        if action == "managers":
            # 4명의 매니저 현황 대시보드
            from kstock.bot.investment_managers import MANAGERS
            watchlist = self.db.get_watchlist()

            lines = ["👨‍💼 투자 매니저 현황\n"]
            for mgr_key in ["scalp", "swing", "position", "long_term"]:
                mgr = MANAGERS[mgr_key]
                stocks = [w for w in watchlist if w.get("manager") == mgr_key]
                lines.append(f"{mgr['emoji']} {mgr['name']} ({mgr['title']})")
                if stocks:
                    for s in stocks[:5]:
                        name = s.get("name", s.get("ticker", ""))
                        lines.append(f"  - {name}")
                    lines.append(f"  총 {len(stocks)}종목 관리 중")
                else:
                    lines.append("  배정된 종목 없음")
                lines.append("")

            buttons = []
            for mgr_key in ["scalp", "swing", "position", "long_term"]:
                mgr = MANAGERS[mgr_key]
                buttons.append([
                    InlineKeyboardButton(
                        f"{mgr['emoji']} {mgr['name']} 분석 요청",
                        callback_data=f"mgr:{mgr_key}",
                    ),
                ])
            buttons.append(make_feedback_row("매니저"))
            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "refresh":
            await query.edit_message_text("⭐ 새로고침 중... ⭐ 메뉴에서 다시 확인하세요.")
            return

    # ── 에이전트 대화 메뉴 ─────────────────────────────────────────

    async def _menu_agent_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🤖 에이전트 — 오류 신고/기능 요청을 Claude Code에 전달."""
        context.user_data["agent_mode"] = True
        buttons = [
            [InlineKeyboardButton("🐛 오류 신고", callback_data="agent:bug")],
            [InlineKeyboardButton("💡 기능 요청", callback_data="agent:feature")],
            [InlineKeyboardButton("❓ 질문하기", callback_data="agent:question")],
            [InlineKeyboardButton("🔙 나가기", callback_data="agent:exit")],
        ]
        await update.message.reply_text(
            "🤖 K-Quant 에이전트\n\n"
            "무엇을 도와드릴까요?\n"
            "아래 버튼을 선택하거나, 직접 메시지를 입력하세요.\n\n"
            "입력한 내용은 로그에 기록되어 다음 업데이트에 반영됩니다.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_agent(self, query, context, payload: str = "") -> None:
        """에이전트 콜백: agent:bug/feature/question/exit."""
        if payload == "bug":
            context.user_data["agent_mode"] = True
            context.user_data["agent_type"] = "bug"
            await query.edit_message_text(
                "🐛 오류 신고\n\n"
                "어떤 오류가 발생했나요?\n"
                "스크린샷을 보내거나, 메시지로 설명해주세요.\n\n"
                "예: '잔고에서 가격이 이상해요', '버튼이 안 눌려요'"
            )
        elif payload == "feature":
            context.user_data["agent_mode"] = True
            context.user_data["agent_type"] = "feature"
            await query.edit_message_text(
                "💡 기능 요청\n\n"
                "어떤 기능이 필요하신가요?\n"
                "자유롭게 설명해주세요.\n\n"
                "예: '알림을 카카오톡으로도 받고 싶어요'"
            )
        elif payload == "question":
            context.user_data["agent_mode"] = True
            context.user_data["agent_type"] = "question"
            await query.edit_message_text(
                "❓ 질문하기\n\n"
                "궁금한 점을 물어보세요.\n\n"
                "예: '모멘텀 전략이 뭔가요?', '자동매매는 언제 되나요?'"
            )
        elif payload == "exit":
            context.user_data.pop("agent_mode", None)
            context.user_data.pop("agent_type", None)
            await query.edit_message_text("🔙 에이전트 모드를 종료했습니다.")


    async def _action_goto(self, query, context, payload: str = "") -> None:
        """간단한 메뉴 리다이렉트 콜백."""
        if payload == "strategy":
            buttons = [
                [
                    InlineKeyboardButton("🔥 반등", callback_data="strat:A"),
                    InlineKeyboardButton("⚡ ETF", callback_data="strat:B"),
                    InlineKeyboardButton("🏢 장기", callback_data="strat:C"),
                ],
                [
                    InlineKeyboardButton("🔄 섹터", callback_data="strat:D"),
                    InlineKeyboardButton("🌎 글로벌", callback_data="strat:E"),
                ],
                [
                    InlineKeyboardButton("🚀 모멘텀", callback_data="strat:F"),
                    InlineKeyboardButton("💥 돌파", callback_data="strat:G"),
                ],
            ]
            await query.edit_message_text(
                "🎯 전략을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif payload == "reco":
            recs = self.db.get_active_recommendations()
            if recs:
                lines = ["📈 추천 성과\n"]
                for r in recs[:10]:
                    pnl = r.get("pnl_pct", 0)
                    emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "🟡"
                    lines.append(f"{emoji} {r['name']} ({pnl:+.1f}%)")
                await query.edit_message_text("\n".join(lines))
            else:
                await query.edit_message_text("📈 아직 추천 내역이 없습니다.")


