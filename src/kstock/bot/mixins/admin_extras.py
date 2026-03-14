"""Admin, favorites, agents, v3.6 features."""
from __future__ import annotations

from kstock import APP_NAME, DISPLAY_VERSION, SYSTEM_NAME
from kstock.bot.bot_imports import *  # noqa: F403
from kstock.core.log_paths import APP_LOG_FILE, ERROR_LOG_FILE, STDOUT_LOG_FILE


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
            InlineKeyboardButton("🔧 시스템 제어", callback_data="adm:sys"),
            InlineKeyboardButton("\U0001f4ca 봇 상태", callback_data="adm:status"),
        ],
        [
            InlineKeyboardButton("\U0001f41b 오류 신고", callback_data="adm:bug"),
            InlineKeyboardButton("\U0001f6a8 에러 로그", callback_data="adm:logs"),
        ],
        [
            InlineKeyboardButton("\U0001f4a1 업데이트 요청", callback_data="adm:request"),
            InlineKeyboardButton("📋 운영 지침", callback_data="adm:directive"),
        ],
        [
            InlineKeyboardButton("\U0001f916 AI 상태", callback_data="ai:status"),
            InlineKeyboardButton("💰 API 비용", callback_data="adm:cost"),
        ],
        [
            InlineKeyboardButton("🏆 시스템 점수", callback_data="adm:score"),
            InlineKeyboardButton("🚨 경계 모드", callback_data="adm:alert"),
        ],
        [
            InlineKeyboardButton("🎮 시스템 컨트롤", callback_data="ctrl:menu"),
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
    def _alert_mode_buttons(self) -> list:
        """경계 모드 변경 버튼 생성 (현재 모드 제외)."""
        current = getattr(self, "_alert_mode", "normal")
        buttons = []
        mode_info = [
            ("normal", "🟢 일상"),
            ("elevated", "🟡 긴장"),
            ("wartime", "🔴 전시"),
        ]
        row = []
        for mode_key, label in mode_info:
            if mode_key == current:
                row.append(InlineKeyboardButton(
                    f"✅ {label} (현재)", callback_data=f"adm:alert:{mode_key}",
                ))
            else:
                row.append(InlineKeyboardButton(
                    label, callback_data=f"adm:alert:{mode_key}",
                ))
        buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu")])
        return buttons

    async def _menu_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🛠 관리자 메뉴 버튼 — 인라인 버튼으로 관리 기능 제공."""
        await update.message.reply_text(
            f"\U0001f6e0 관리자 모드 {DISPLAY_VERSION}\n\n"
            "아래 버튼을 눌러주세요.",
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
            await safe_edit_or_reply(query,
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
            await safe_edit_or_reply(query,
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
                await safe_edit_or_reply(query,
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
                await safe_edit_or_reply(query,
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
            await safe_edit_or_reply(query,
                f"\U0001f6e0 관리자 모드 {DISPLAY_VERSION}\n\n"
                "아래 버튼을 눌러주세요.",
                reply_markup=InlineKeyboardMarkup(_admin_buttons()),
            )

        elif subcmd == "directive":
            # 운영 지침 조회/수정
            sub2 = payload.split(":", 1)[1] if ":" in payload else ""
            directive_path = Path("data/daily_directive.md")

            if sub2 == "edit":
                # 수정 모드 진입
                context.user_data["admin_mode"] = "directive_edit"
                await safe_edit_or_reply(query,
                    "📋 운영 지침 수정 모드\n\n"
                    "새로운 지침을 메시지로 보내주세요.\n"
                    "전체 내용이 교체됩니다.\n\n"
                    "또는 '추가: ...' 형식으로 보내면\n"
                    "'오늘의 특별 지침' 섹션에 추가됩니다.",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )
            elif sub2 == "run":
                # 지금 즉시 실행
                await safe_edit_or_reply(query,"📋 운영 지침 실행 중...")
                try:
                    await self.job_daily_directive(context)
                    await safe_edit_or_reply(query,
                        "📋 운영 지침 실행 완료!\n채팅에서 결과를 확인하세요.",
                        reply_markup=InlineKeyboardMarkup(back_btn),
                    )
                except Exception as e:
                    await safe_edit_or_reply(query,
                        f"⚠️ 실행 실패: {e}",
                        reply_markup=InlineKeyboardMarkup(back_btn),
                    )
            else:
                # 지침 조회
                if directive_path.exists():
                    content = directive_path.read_text(encoding="utf-8")
                    # 4000자 제한
                    if len(content) > 3500:
                        content = content[:3500] + "\n..."
                else:
                    content = "(지침 파일 없음)"
                buttons = [
                    [InlineKeyboardButton("✏️ 수정", callback_data="adm:directive:edit"),
                     InlineKeyboardButton("▶️ 지금 실행", callback_data="adm:directive:run")],
                    [InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu")],
                ]
                await safe_edit_or_reply(query,
                    f"📋 현재 운영 지침\n{'━' * 20}\n\n{content}",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

        elif subcmd == "sys":
            # v8.7: 시스템 제어 패널
            sub2 = payload.split(":", 1)[1] if ":" in payload else ""
            sys_back = [[InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu")]]

            if sub2 == "restart_confirm":
                # 재시작 확인 완료 → 실행
                await safe_edit_or_reply(query,"🔄 봇 재시작 중... (5초 후 복귀)")
                import subprocess as _sp
                _sp.Popen(
                    ["bash", "-c", f"sleep 2 && cd {os.getcwd()} && ./kbot restart"],
                    start_new_session=True,
                )
                return

            if sub2 == "restart":
                # 재시작 확인 요청
                await safe_edit_or_reply(query,
                    "🔄 봇을 재시작하시겠습니까?\n\n"
                    "⚠️ 재시작 동안 5~10초간 응답이 중단됩니다.",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ 재시작", callback_data="adm:sys:restart_confirm"),
                            InlineKeyboardButton("❌ 취소", callback_data="adm:sys"),
                        ],
                    ]),
                )
                return

            if sub2 == "resources":
                # 시스템 리소스 상세
                import subprocess as _sp
                lines = ["🖥 시스템 리소스", "━" * 22]
                try:
                    pid = _sp.check_output(
                        ["pgrep", "-f", "kstock.app"], text=True, timeout=3,
                    ).strip().split("\n")[0]
                    ps_out = _sp.check_output(
                        ["ps", "-p", pid, "-o", "rss=,etime=,%cpu="],
                        text=True, timeout=3,
                    ).strip()
                    parts = ps_out.split()
                    mem_mb = int(parts[0]) // 1024 if parts else 0
                    uptime = parts[1] if len(parts) > 1 else "?"
                    cpu = parts[2] if len(parts) > 2 else "?"
                    lines.append(f"PID: {pid}")
                    lines.append(f"메모리: {mem_mb}MB")
                    lines.append(f"CPU: {cpu}%")
                    lines.append(f"가동: {uptime}")
                except Exception:
                    lines.append("봇 프로세스 없음")

                # 디스크
                try:
                    df_out = _sp.check_output(
                        ["df", "-h", "/"], text=True, timeout=3,
                    ).strip().split("\n")[-1].split()
                    lines.append(f"\n💾 디스크: {df_out[2]} 사용 / {df_out[1]} (잔여 {df_out[3]})")
                except Exception:
                    pass

                # DB 크기
                try:
                    db_path = os.path.join(os.getcwd(), "data", "kquant.db")
                    if os.path.exists(db_path):
                        db_size = os.path.getsize(db_path) / (1024 * 1024)
                        lines.append(f"📊 DB: {db_size:.1f}MB")
                except Exception:
                    pass

                # 로그 크기
                try:
                    log_sizes = []
                    for path in (APP_LOG_FILE, ERROR_LOG_FILE, STDOUT_LOG_FILE):
                        if os.path.exists(path):
                            log_sizes.append(os.path.getsize(path))
                    if log_sizes:
                        lines.append(f"📝 로그: {sum(log_sizes) / (1024 * 1024):.1f}MB")
                except Exception:
                    pass

                await safe_edit_or_reply(query,
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 시스템 제어", callback_data="adm:sys")],
                    ]),
                )
                return

            if sub2 == "errors":
                # 최근 에러 로그 (실제 로그 파일)
                import subprocess as _sp
                try:
                    result = _sp.run(
                        [
                            "bash", "-lc",
                            f"grep -E 'ERROR|CRITICAL|Exception' "
                            f"'{APP_LOG_FILE}' '{ERROR_LOG_FILE}' '{STDOUT_LOG_FILE}' 2>/dev/null",
                        ],
                        capture_output=True, text=True, timeout=5,
                    )
                    error_lines = result.stdout.strip().split("\n")
                    recent = [l.strip()[-100:] for l in error_lines if l.strip()][-8:]
                    if recent:
                        text = "🚨 최근 에러 (8건)\n" + "━" * 22 + "\n\n"
                        text += "\n\n".join(recent)
                    else:
                        text = "✅ 에러 없음!"
                except Exception as e:
                    text = f"⚠️ 로그 확인 실패: {e}"
                await safe_edit_or_reply(query,
                    text[:4000],
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 시스템 제어", callback_data="adm:sys")],
                    ]),
                )
                return

            if sub2 == "jobs":
                # 스케줄러 현황
                lines = ["📋 스케줄러 현황", "━" * 22]
                try:
                    app = context.application
                    if hasattr(app, "job_queue") and app.job_queue:
                        jobs = app.job_queue.jobs()
                        if jobs:
                            for j in sorted(jobs, key=lambda x: str(x.next_t or ""))[:15]:
                                next_t = j.next_t.strftime("%H:%M") if j.next_t else "?"
                                name = (j.name or "unnamed")[:25]
                                lines.append(f"  ⏰ {next_t} — {name}")
                            lines.append(f"\n총 {len(jobs)}개 작업")
                        else:
                            lines.append("등록된 작업 없음")
                    else:
                        lines.append("job_queue 없음")
                except Exception as e:
                    lines.append(f"조회 실패: {e}")
                await safe_edit_or_reply(query,
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 시스템 제어", callback_data="adm:sys")],
                    ]),
                )
                return

            # 기본: 시스템 제어 메뉴
            sys_buttons = [
                [
                    InlineKeyboardButton("🔄 봇 재시작", callback_data="adm:sys:restart"),
                    InlineKeyboardButton("🖥 리소스", callback_data="adm:sys:resources"),
                ],
                [
                    InlineKeyboardButton("🚨 에러 로그", callback_data="adm:sys:errors"),
                    InlineKeyboardButton("📋 스케줄러", callback_data="adm:sys:jobs"),
                ],
                [InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu")],
            ]
            # 간단한 상태 요약
            status_line = "🔴 중지됨"
            try:
                import subprocess as _sp
                pid_out = _sp.check_output(
                    ["pgrep", "-f", "kstock.app"], text=True, timeout=3,
                ).strip().split("\n")[0]
                if pid_out:
                    up = _sp.check_output(
                        ["ps", "-p", pid_out, "-o", "etime="],
                        text=True, timeout=3,
                    ).strip()
                    status_line = f"🟢 실행 중 (PID {pid_out}, 가동 {up})"
            except Exception:
                pass

            await safe_edit_or_reply(query,
                f"🔧 시스템 제어\n{'━' * 22}\n\n"
                f"상태: {status_line}\n"
                f"버전: {DISPLAY_VERSION}",
                reply_markup=InlineKeyboardMarkup(sys_buttons),
            )
            return

        elif subcmd == "close":
            # 관리자 메뉴 닫기 + 상태 초기화 + Reply Keyboard 복구
            context.user_data.pop("admin_mode", None)
            context.user_data.pop("admin_faq_type", None)
            await safe_edit_or_reply(query,"\U0001f6e0 관리자 메뉴를 닫았습니다.")
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="📱 메뉴를 사용하세요.",
                    reply_markup=get_reply_markup(context),
                )
            except Exception:
                logger.debug("_action_admin menu reply_markup restore failed", exc_info=True)

        elif subcmd == "score":
            # v6.2.1: 시스템 자가 점수
            try:
                from kstock.core.system_score import compute_system_score, format_score_report
                score = compute_system_score(self.db)
                text = format_score_report(score)
                score_buttons = [
                    [InlineKeyboardButton("📊 점수 추이", callback_data="adm:score_trend")],
                    [InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu")],
                ]
                await safe_edit_or_reply(query,
                    text,
                    reply_markup=InlineKeyboardMarkup(score_buttons),
                )
            except Exception as e:
                logger.error("System score error: %s", e, exc_info=True)
                await safe_edit_or_reply(query,
                    f"⚠️ 점수 계산 실패: {e}",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )

        elif subcmd == "score_trend":
            # v6.2.1: 점수 추이
            try:
                from kstock.core.system_score import format_score_trend
                text = format_score_trend(self.db, days=14)
                await safe_edit_or_reply(query,
                    text,
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )
            except Exception as e:
                await safe_edit_or_reply(query,
                    f"⚠️ 추이 조회 실패: {e}",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )

        elif subcmd == "cost":
            # v6.2.1: API 비용 현황
            try:
                from kstock.core.token_tracker import format_monthly_cost_report
                text = format_monthly_cost_report(self.db)
                cost_buttons = [
                    [InlineKeyboardButton("📅 오늘 사용량", callback_data="adm:cost_today")],
                    [InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu")],
                ]
                await safe_edit_or_reply(query,
                    text,
                    reply_markup=InlineKeyboardMarkup(cost_buttons),
                )
            except Exception as e:
                logger.error("Cost report error: %s", e, exc_info=True)
                await safe_edit_or_reply(query,
                    f"⚠️ 비용 조회 실패: {e}",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )

        elif subcmd == "cost_today":
            # v6.2.1: 오늘 API 사용량
            try:
                from datetime import datetime as _dt
                today = _dt.utcnow().strftime("%Y-%m-%d")
                daily = self.db.get_daily_api_usage(today)
                total_cost = daily.get("total_cost", 0)
                krw = total_cost * 1400
                text = (
                    f"📅 오늘 API 사용량 ({today})\n"
                    f"{'━' * 24}\n\n"
                    f"📊 호출: {daily.get('total_calls', 0):,}회\n"
                    f"💵 비용: ${total_cost:.4f} (≈{krw:,.0f}원)\n"
                    f"📝 입력: {daily.get('total_input', 0):,} tok\n"
                    f"📝 출력: {daily.get('total_output', 0):,} tok\n"
                    f"⚡ 캐시절약: {daily.get('total_cache_read', 0):,} tok\n"
                    f"⏱ 평균응답: {daily.get('avg_latency', 0):.0f}ms"
                )
                await safe_edit_or_reply(query,
                    text,
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )
            except Exception as e:
                await safe_edit_or_reply(query,
                    f"⚠️ 조회 실패: {e}",
                    reply_markup=InlineKeyboardMarkup(back_btn),
                )

        elif subcmd == "alert":
            # v6.2.2: 경계 모드 관리
            sub2 = payload.split(":", 1)[1] if ":" in payload else ""

            if sub2 in ("normal", "elevated", "wartime"):
                # 모드 변경
                await self.set_alert_mode(sub2, context=context, reason="수동 변경")
                # 다시 경계 모드 메뉴 표시
                status_text = self.get_alert_mode_status()
                alert_buttons = self._alert_mode_buttons()
                await safe_edit_or_reply(query,
                    f"🚨 경계 모드 설정\n{'━' * 20}\n\n{status_text}",
                    reply_markup=InlineKeyboardMarkup(alert_buttons),
                )
            else:
                # 현재 상태 + 변경 버튼
                status_text = self.get_alert_mode_status()
                alert_buttons = self._alert_mode_buttons()
                await safe_edit_or_reply(query,
                    f"🚨 경계 모드 설정\n{'━' * 20}\n\n{status_text}",
                    reply_markup=InlineKeyboardMarkup(alert_buttons),
                )

        elif subcmd == "security":
            # v3.6: 보안 감사
            audit_result = security_audit()
            await safe_edit_or_reply(query,
                audit_result,
                reply_markup=InlineKeyboardMarkup(back_btn),
            )

        elif subcmd == "status":
            holdings = self.db.get_active_holdings()
            chat_count = 0
            try:
                chat_count = self.db.get_chat_usage(_today())
            except Exception:
                logger.debug("_action_admin chat_usage query failed", exc_info=True)
            uptime = datetime.now(KST) - self._start_time
            hours = uptime.seconds // 3600
            mins = (uptime.seconds % 3600) // 60

            # v3.6: AI + WebSocket 상태 추가
            ai_available = [n for n, p in self.ai.providers.items() if p.available]
            ai_text = ", ".join(ai_available) if ai_available else "없음"
            ws_text = "연결" if self.ws.is_connected else "미연결"
            ws_subs = len(self.ws.get_subscriptions())

            await safe_edit_or_reply(query,
                f"\U0001f4ca 봇 상태 {DISPLAY_VERSION}\n\n"
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
                await safe_edit_or_reply(query,
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
            await safe_edit_or_reply(query,
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(back_btn),
            )

        elif subcmd == "logs":
            try:
                import subprocess
                result = subprocess.run(
                    ["tail", "-50", str(APP_LOG_FILE)],
                    capture_output=True, text=True, timeout=5,
                )
                errors = [
                    l.strip()[-90:]
                    for l in result.stdout.splitlines()
                    if "ERROR" in l
                ][-8:]
                if errors:
                    await safe_edit_or_reply(query,
                        "\U0001f6a8 최근 에러\n\n" + "\n\n".join(errors),
                        reply_markup=InlineKeyboardMarkup(back_btn),
                    )
                else:
                    await safe_edit_or_reply(query,
                        "\u2705 에러 없음!",
                        reply_markup=InlineKeyboardMarkup(back_btn),
                    )
            except Exception as e:
                await safe_edit_or_reply(query,
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

    async def _save_directive_edit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
    ) -> None:
        """운영 지침 수정/추가 처리."""
        directive_path = Path("data/daily_directive.md")
        directive_path.parent.mkdir(parents=True, exist_ok=True)

        if text.startswith("추가:") or text.startswith("추가 :"):
            # '오늘의 특별 지침' 섹션에 추가
            addition = text.split(":", 1)[1].strip()
            if directive_path.exists():
                content = directive_path.read_text(encoding="utf-8")
            else:
                content = ""
            # 특별 지침 섹션 찾아서 추가
            marker = "## 오늘의 특별 지침"
            if marker in content:
                content = content.replace(
                    marker,
                    f"{marker}\n- {addition}",
                )
            else:
                content += f"\n\n{marker}\n- {addition}\n"
            directive_path.write_text(content, encoding="utf-8")
            await update.message.reply_text(
                f"📋 특별 지침 추가 완료!\n\n➕ {addition}",
                reply_markup=InlineKeyboardMarkup(_admin_buttons()),
            )
        else:
            # 전체 교체
            directive_path.write_text(text, encoding="utf-8")
            await update.message.reply_text(
                f"📋 운영 지침 전체 교체 완료!\n\n📝 {len(text)}자 저장됨",
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
                    ["tail", "-20", str(APP_LOG_FILE)],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if "ERROR" in line or "error" in line.lower():
                        recent_errors.append(line.strip()[-120:])
            except Exception:
                logger.debug("cmd_admin_report bot.log error scan failed", exc_info=True)
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
                logger.debug("cmd_admin_report job_runs check failed", exc_info=True)

            chat_count = 0
            try:
                chat_count = self.db.get_chat_usage(_today())
            except Exception:
                logger.debug("cmd_admin_report chat_usage query failed", exc_info=True)

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
                    ["tail", "-50", str(APP_LOG_FILE)],
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
                empty_buttons = [
                    [InlineKeyboardButton(
                        "➕ 종목 추가", callback_data="bal:add",
                    )],
                    [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
                ]
                try:
                    await placeholder.edit_text(
                        "💰 주호님, 등록된 보유종목이 없습니다.\n\n"
                        "📸 스크린샷 전송 → 자동 인식\n"
                        "💬 종목명 입력 → 버튼으로 추가\n\n"
                        "아래 버튼을 눌러 시작하세요!",
                        reply_markup=InlineKeyboardMarkup(empty_buttons),
                    )
                except Exception:
                    logger.debug("cmd_balance empty holdings edit_text failed", exc_info=True)
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
                logger.debug("cmd_balance edit_text failed, falling back", exc_info=True)
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

    # ── v9.6.1: AI 토론 바로가기 메뉴 ──────────────────────────────────

    async def _menu_debate(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """AI 토론 바로가기 — 보유종목 중 빠른 토론 시작."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        lines = ["🎙️ AI 매니저 토론\n"]
        lines.append("4명의 투자 매니저가 종목을 토론합니다.")
        lines.append("종목명을 입력하거나 아래 버튼을 누르세요.\n")

        # 보유종목 기반 빠른 토론 버튼
        holdings = self.db.get_active_holdings()
        buttons = []
        for h in holdings[:6]:
            ticker = h.get("ticker", "")
            name = h.get("name", ticker)[:6]
            buttons.append(
                InlineKeyboardButton(
                    f"🎙️ {name}", callback_data=f"debate:{ticker}"
                )
            )
        # 2열로 배치
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

        if not rows:
            lines.append("보유종목이 없습니다. 종목명을 직접 입력하세요.")
            lines.append("예: '삼성전자 토론' 또는 종목코드 입력")

        rows.append([InlineKeyboardButton("❌ 닫기", callback_data="dismiss:debate")])

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
        )

    # ── v3.6 신규 메뉴 핸들러 ────────────────────────────────────────

    async def _menu_more(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """더보기 메뉴 — InlineKeyboard로 표시하여 클로드 메뉴(Reply Keyboard) 유지."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        # v9.6.2: 22→12개로 정리 (중복/실험적 기능 제거)
        buttons = [
            [InlineKeyboardButton("💎 텐베거", callback_data="menu:tenbagger_scan"),
             InlineKeyboardButton("⚡ 스윙 기회", callback_data="menu:swing")],
            [InlineKeyboardButton("🔥 급등주", callback_data="menu:surge"),
             InlineKeyboardButton("🕵️ 매집탐지", callback_data="menu:accumulation")],
            [InlineKeyboardButton("📅 주간 보고서", callback_data="menu:weekly_report"),
             InlineKeyboardButton("📊 공매도", callback_data="menu:short")],
            [InlineKeyboardButton("🎙️ AI 토론", callback_data="menu:debate"),
             InlineKeyboardButton("🔬 섹터 딥다이브", callback_data="sdive:menu")],
            [InlineKeyboardButton("🔔 알림 설정", callback_data="menu:notification"),
             InlineKeyboardButton("📡 KIS설정", callback_data="menu:kis_setup")],
            [InlineKeyboardButton("📖 사용설명서", callback_data="guide:main"),
             InlineKeyboardButton("🛠 관리자", callback_data="menu:admin")],
            [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:more")],
        ]
        await update.message.reply_text(
            "⚙️ 더보기 메뉴\n원하는 기능을 선택하세요:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _menu_back_to_main(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """메인 메뉴로 복귀."""
        await update.message.reply_text(
            "\U0001f3e0 메인 메뉴로 돌아왔습니다.",
            reply_markup=get_reply_markup(context),
        )

    async def _menu_guide(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """v9.5.5 사용설명서 — 텍스트 메뉴에서 진입."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        text = (
            f"📖 {APP_NAME} 사용설명서\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "AI 투자 비서 + ML 예측 + 24채널 학습\n\n"
            "🔹 기본 사용법\n"
            "  종목명 입력 → AI 분석 + ML 예측\n"
            "  예: '삼성전자', 'SK하이닉스'\n\n"
            "🔹 학습 현황\n"
            "  /learning → 예산, YouTube, ML, 합성\n\n"
            "🔹 자동 알림 (매일)\n"
            "  07:30 모닝브리핑 (크로스마켓+유가)\n"
            "  09:30 AI 토론 (장 시작)\n"
            "  16:00 장마감 리포트 + PDF\n"
            "  21:30 일일 학습 합성\n\n"
            "아래에서 상세 기능을 확인하세요 👇"
        )
        buttons = [
            [
                InlineKeyboardButton("📊 분석 기능", callback_data="guide:analysis"),
                InlineKeyboardButton("🤖 4매니저", callback_data="guide:manager"),
            ],
            [
                InlineKeyboardButton("🔔 알림 시스템", callback_data="guide:alerts"),
                InlineKeyboardButton("⭐ 신규 기능", callback_data="guide:new"),
            ],
            [
                InlineKeyboardButton("🔬 섹터분석", callback_data="sdive:menu"),
                InlineKeyboardButton("📊 차트", callback_data="vchart:menu"),
            ],
            make_feedback_row("사용설명서"),
        ]
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _menu_analysis_hub(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """분석 허브 — 종목명 입력 또는 빠른 분석 선택."""
        context.user_data["awaiting_analysis_query"] = True
        buttons = [
            [
                InlineKeyboardButton("🎯 4매니저 동시추천", callback_data="quick_q:mgr4"),
                InlineKeyboardButton("🔥 매수추천", callback_data="quick_q:buy_pick"),
            ],
            [
                InlineKeyboardButton("💼 포트폴리오 조언", callback_data="quick_q:portfolio"),
                InlineKeyboardButton("📊 시장 분석", callback_data="quick_q:market"),
            ],
            [
                InlineKeyboardButton("⚡ 스윙기회", callback_data="hub:swing"),
                InlineKeyboardButton("🔥 급등주", callback_data="hub:surge"),
            ],
            [
                InlineKeyboardButton("📊 멀티분석", callback_data="hub:multi"),
                InlineKeyboardButton("⚠️ 리스크 점검", callback_data="quick_q:risk"),
            ],
            [
                InlineKeyboardButton("🔬 섹터 딥다이브", callback_data="sdive:menu"),
            ],
            make_feedback_row("분석허브"),
        ]
        await update.message.reply_text(
            "🔎 종목 검색\n\n"
            "어떤 종목을 찾고 계신가요?\n"
            "종목명 또는 6자리 종목코드를 보내주세요.\n"
            "입력하면 바로 `분석 · AI 토론 · 매수 시나리오` 버튼이 뜹니다.\n\n"
            "예: GC지놈 / 340450 / 삼성전자 / 005930\n\n"
            "⬇️ 또는 지금 바로 보기:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_hub(self, query, context, payload: str) -> None:
        """분석 허브 버튼 콜백 — 각 기능 직접 실행."""
        if payload == "surge":
            await safe_edit_or_reply(query,"🔥 급등주 실시간 스캔 중...")
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
                        logger.debug("_action_hub surge scan data build failed for %s", code, exc_info=True)
                        continue

                if not stocks_data:
                    await safe_edit_or_reply(query,"🔥 현재 급등 조건을 충족하는 종목이 없습니다.")
                    return

                stocks_data.sort(key=lambda s: s["change_pct"], reverse=True)
                lines = [f"🔥 급등주 실시간 스캔 ({len(stocks_data)}종목 감지)\n"]
                for i, s in enumerate(stocks_data[:10], 1):
                    icon = "📈" if s["change_pct"] >= 5 else "🔥" if s["change_pct"] >= 3 else "⚡"
                    lines.append(
                        f"{i}. {icon} {s['name']}({s['ticker']}) "
                        f"{s['change_pct']:+.1f}% 거래량 {s['volume_ratio']:.1f}배"
                    )
                await safe_edit_or_reply(query,"\n".join(lines))
            except Exception as e:
                logger.error("Hub surge error: %s", e, exc_info=True)
                await safe_edit_or_reply(query,"⚠️ 급등주 스캔 중 오류가 발생했습니다.")

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
                await safe_edit_or_reply(query,"\n".join(lines))
            else:
                await safe_edit_or_reply(query,
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
                await safe_edit_or_reply(query,
                    "📊 멀티 에이전트 분석\n\n보유종목을 선택하세요:",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            else:
                await safe_edit_or_reply(query,
                    "📊 멀티분석\n\n종목명을 직접 입력하면 자동 분석됩니다.\n"
                    "예: 삼성전자 분석"
                )

    async def _action_ai_status(self, query, context, payload: str) -> None:
        """AI 엔진 상태 표시."""
        status = self.ai.get_status()
        routing = self.ai.get_routing_table()
        ws_status = self.ws.get_status()
        text = f"{status}\n\n{routing}\n\n\U0001f4e1 실시간: {ws_status}"
        await safe_edit_or_reply(query,text)

    async def _action_orderbook(self, query, context, payload: str) -> None:
        """호가 조회 액션."""
        if payload == "select":
            # 보유종목 목록에서 선택
            holdings = await self._load_holdings_with_fallback()
            if not holdings:
                await safe_edit_or_reply(query,
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
            await safe_edit_or_reply(query,
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

            await safe_edit_or_reply(query,f"\U0001f4ca {name} 호가 조회 중...")

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
                    logger.debug("_action_orderbook price fetch failed for %s", ticker, exc_info=True)
                    text = f"\u26a0\ufe0f {name} 호가 조회 실패"
                await query.message.reply_text(text)

    # ── 즐겨찾기 메뉴 ──────────────────────────────────────────────

    def _resolve_name(self, ticker: str, fallback: str = "") -> str:
        """종목코드 → 종목명 변환. universe에서 조회."""
        for item in self.all_tickers:
            if item.get("code") == ticker:
                return item.get("name", fallback or ticker)
        return fallback if fallback and fallback != ticker else ticker

    # ── 종목 관리 대시보드 (v8.4) ─────────────────────────────────

    _HZ_TAG = {
        "scalp": "⚡단타", "swing": "🔥스윙",
        "position": "📊포지션", "long_term": "💎장기",
        "tenbagger": "🔟텐배거",
    }
    _HZ_HDR = {
        "scalp": "⚡ 단타 — 제시 리버모어",
        "swing": "🔥 스윙 — 윌리엄 오닐",
        "position": "📊 포지션 — 피터 린치",
        "long_term": "💎 장기 — 워렌 버핏",
        "tenbagger": "🔟 텐배거 — 10배 후보",
    }
    _TAB_EMOJI = {
        "holding": "💰", "scalp": "⚡", "swing": "🔥",
        "position": "📊", "long_term": "💎", "tenbagger": "🔟",
        "unclassified": "📦",
    }
    _TAB_LABEL = {
        "holding": "보유", "scalp": "단타", "swing": "스윙",
        "position": "포지션", "long_term": "장기", "tenbagger": "텐배거",
        "unclassified": "미분류",
    }
    _TB_GRADE_EMOJI = {"A": "🟢", "B": "🟡", "C": "🟠"}
    _ITEMS_PER_PAGE = 8

    async def _sync_universe_to_watchlist(self) -> int:
        """유니버스 전체를 워치리스트에 동기화. 신규 종목만 추가."""
        items = []
        for stock in self.all_tickers:
            items.append({
                "ticker": stock["code"],
                "name": stock["name"],
                "sector": stock.get("sector", stock.get("category", "")),
            })
        count = self.db.bulk_add_watchlist(items)

        # 기존 holdings의 holding_type → watchlist.horizon 반영
        try:
            holdings = self.db.get_active_holdings()
            for h in holdings:
                ticker = h.get("ticker", "")
                ht = h.get("holding_type", "")
                if ticker and ht and ht not in ("auto", ""):
                    hz = _holding_type_to_horizon(ht)
                    if hz:
                        self.db.update_watchlist_horizon(ticker, hz, hz)
        except Exception:
            logger.debug("_sync_universe_to_watchlist holdings sync failed", exc_info=True)

        return count

    def _build_action_console_preview(self) -> list[str]:
        """대시보드 상단용 매수/보유/매도 미리보기."""
        try:
            from kstock.bot.investment_managers import MANAGER_THRESHOLDS
        except Exception:
            MANAGER_THRESHOLDS = {}

        holdings = self.db.get_active_holdings() or []
        ranked: list[tuple[int, str]] = []

        for h in holdings:
            ticker = h.get("ticker", "")
            name = (h.get("name", "") or ticker)[:8]
            holding_type = h.get("holding_type", "swing")
            buy_price = float(h.get("buy_price", 0) or 0)
            current_price = float(h.get("current_price", 0) or 0)
            if buy_price <= 0:
                continue
            pnl = ((current_price - buy_price) / buy_price * 100) if current_price > 0 else 0.0
            thresholds = MANAGER_THRESHOLDS.get(
                holding_type, MANAGER_THRESHOLDS.get("swing", {}),
            )
            stop_loss = float(thresholds.get("stop_loss", -7.0) or -7.0)
            take_profit = float(thresholds.get("take_profit_1", 10.0) or 10.0)

            verdict = "보유 유지"
            urgency = 1
            if pnl <= stop_loss + 0.7:
                verdict = "매도 타이밍 점검"
                urgency = 4
            elif pnl >= take_profit:
                verdict = "분할매도 구간"
                urgency = 3
            elif holding_type in {"position", "long_term", "tenbagger"} and pnl <= max(-3.0, stop_loss * 0.5):
                verdict = "보유/추매 체크"
                urgency = 2
            elif holding_type == "scalp":
                verdict = "당일 추세 확인"

            ranked.append((urgency, f"- {name}: {verdict} ({pnl:+.1f}%)"))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [line for _, line in ranked[:3]]

    async def _build_dashboard_view(
        self, category: str = "", page: int = 0,
    ) -> tuple[str, InlineKeyboardMarkup]:
        """종목 관리 대시보드 빌드. category=""이면 요약, 아니면 탭 상세."""
        counts = self.db.get_watchlist_category_counts()

        if category == "":
            return await self._build_dashboard_summary(counts)
        return await self._build_dashboard_tab(category, page, counts)

    async def _build_dashboard_summary(
        self, counts: dict,
    ) -> tuple[str, InlineKeyboardMarkup]:
        """대시보드 요약 화면: 카테고리 수 + 보유종목 미리보기."""
        total = counts.get("total", 0)
        lines = [
            f"⭐ 종목 관리 ({total}종목)",
            "━" * 22,
            "",
            f"💰 보유: {counts.get('holding', 0)}종목",
            f"⚡ 단타: {counts.get('scalp', 0)} | "
            f"🔥 스윙: {counts.get('swing', 0)}",
            f"📊 포지션: {counts.get('position', 0)} | "
            f"💎 장기: {counts.get('long_term', 0)}",
            f"📦 미분류: {counts.get('unclassified', 0)}",
        ]
        action_preview = self._build_action_console_preview()
        if action_preview:
            lines.append("\n📋 액션 콘솔")
            lines.extend(action_preview)

        # 보유종목 미리보기 (최대 5개, 가격 조회)
        if counts.get("holding", 0) > 0:
            held, _ = self.db.get_watchlist_by_category("holding", limit=5)
            lines.append("\n── 💰 보유종목 ──")
            for h in held:
                name = h.get("name", h.get("ticker", ""))[:8]
                pnl = float(h.get("pnl_pct", 0) or 0)
                bp = float(h.get("buy_price", 0) or 0)
                cur = 0.0
                dc_pct = 0.0
                try:
                    detail = await self._get_price_detail(h["ticker"], 0)
                    cur = detail["price"]
                    dc_pct = detail["day_change_pct"]
                except Exception:
                    cur = float(h.get("current_price", 0) or 0)

                if cur > 0 and bp > 0:
                    pnl = (cur - bp) / bp * 100

                de = "📈" if dc_pct > 0 else ("📉" if dc_pct < 0 else "➖")
                pe = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
                ps = "+" if pnl > 0 else ""
                ds = "+" if dc_pct > 0 else ""
                if cur > 0:
                    lines.append(
                        f"{de} {name}: {cur:,.0f}원 ({ds}{dc_pct:.1f}%) "
                        f"{pe}{ps}{pnl:.1f}%"
                    )
                else:
                    lines.append(f"  {name}")

        # 버튼: 7개 탭 + 자동분류 + 새로고침
        buttons = []
        row1 = []
        row2 = []
        row3 = []
        for cat in ("holding", "scalp", "swing"):
            e = self._TAB_EMOJI[cat]
            n = counts.get(cat, 0)
            lbl = self._TAB_LABEL[cat]
            row1.append(InlineKeyboardButton(
                f"{e}{lbl} {n}", callback_data=f"fav:tab:{cat}:0",
            ))
        for cat in ("position", "long_term", "tenbagger"):
            e = self._TAB_EMOJI[cat]
            n = counts.get(cat, 0)
            lbl = self._TAB_LABEL[cat]
            row2.append(InlineKeyboardButton(
                f"{e}{lbl} {n}", callback_data=f"fav:tab:{cat}:0",
            ))
        row3.append(InlineKeyboardButton(
            f"📦미분류 {counts.get('unclassified', 0)}",
            callback_data="fav:tab:unclassified:0",
        ))
        buttons.append(row1)
        buttons.append(row2)
        buttons.append(row3)

        # 액션 콘솔 + 매수 추천 + 매니저
        action_row = [
            InlineKeyboardButton(
                "📋 액션콘솔", callback_data="menu:daily_actions",
            ),
            InlineKeyboardButton(
                "📈 매수추천", callback_data="fav:buy_scan",
            ),
            InlineKeyboardButton(
                "👨‍💼 매니저", callback_data="fav:managers",
            ),
        ]
        buttons.append(action_row)

        action_row2 = [
            InlineKeyboardButton(
                "🎙️ AI토론", callback_data="menu:debate",
            ),
            InlineKeyboardButton(
                "➕ 추가", callback_data="fav:add_mode",
            ),
        ]
        if counts.get("unclassified", 0) > 0:
            action_row2.append(InlineKeyboardButton(
                "🤖 자동분류", callback_data="fav:auto_classify",
            ))
        action_row2.append(InlineKeyboardButton(
            "🔄 새로고침", callback_data="fav:refresh",
        ))
        buttons.append(action_row2)

        # 텐배거 유니버스 미등록 시 등록 버튼
        if counts.get("tenbagger", 0) == 0:
            buttons.append([InlineKeyboardButton(
                "🔟 텐배거 20종목 등록하기",
                callback_data="fav:tb_import",
            )])

        # 텐배거 리포트 PDF 생성 버튼
        buttons.append([InlineKeyboardButton(
            "📄 텐배거 리포트 PDF",
            callback_data="fav:tb_report",
        )])

        buttons.append(make_feedback_row("즐겨찾기"))

        text = "\n".join(lines)
        return text, InlineKeyboardMarkup(buttons)

    async def _build_dashboard_tab(
        self, category: str, page: int, counts: dict,
    ) -> tuple[str, InlineKeyboardMarkup]:
        """카테고리 탭 상세 화면: 종목 리스트 + 페이지네이션."""
        per_page = self._ITEMS_PER_PAGE
        items, total = self.db.get_watchlist_by_category(
            category, limit=per_page, offset=page * per_page,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)

        e = self._TAB_EMOJI.get(category, "📌")
        lbl = self._TAB_LABEL.get(category, category)
        hdr = self._HZ_HDR.get(category, f"{e} {lbl}")
        lines = [
            f"{hdr} ({total}종목)",
            "━" * 22,
        ]

        # 텐배거 탭이면 등급 정보 로드
        tb_grades = {}
        if category == "tenbagger":
            try:
                from kstock.signal.tenbagger_screener import get_initial_universe
                for u in get_initial_universe():
                    tb_grades[u["ticker"]] = {
                        "grade": u.get("grade", ""),
                        "tier": u.get("tier", ""),
                        "rank": u.get("rank", 99),
                        "character": u.get("character", ""),
                    }
            except Exception:
                pass

        # 종목 데이터 + 가격 조회
        buttons = []
        for item in items:
            ticker = item.get("ticker", "")
            name = (item.get("name", "") or ticker)[:8]
            bp = float(item.get("buy_price", 0) or 0)
            is_held = bp > 0

            cur = 0.0
            dc_pct = 0.0
            try:
                detail = await self._get_price_detail(ticker, 0)
                cur = detail["price"]
                dc_pct = detail["day_change_pct"]
            except Exception:
                pass

            de = "📈" if dc_pct > 0 else ("📉" if dc_pct < 0 else "➖")
            ds = "+" if dc_pct > 0 else ""
            hold_tag = ""

            if is_held and cur > 0:
                pnl = (cur - bp) / bp * 100
                pe = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
                ps = "+" if pnl > 0 else ""
                hold_tag = f" 보유{pe}{ps}{pnl:.1f}%"

            # 텐배거 등급 태그
            grade_tag = ""
            if category == "tenbagger" and ticker in tb_grades:
                g = tb_grades[ticker]["grade"]
                ge = self._TB_GRADE_EMOJI.get(g, "")
                grade_tag = f"{ge}{g} "

            if cur > 0:
                lines.append(
                    f"{grade_tag}{de} {name}: {cur:,.0f}원 ({ds}{dc_pct:.1f}%){hold_tag}"
                )
            else:
                lines.append(f"  {grade_tag}{name}")

            # 종목별 버튼: [종목명] → 상세
            buttons.append([
                InlineKeyboardButton(
                    f"📋 {name}", callback_data=f"fav:stock:{ticker}",
                ),
                InlineKeyboardButton(
                    "📊", callback_data=f"fav:chart:{ticker}",
                ),
                InlineKeyboardButton(
                    "🔄", callback_data=f"fav:classify:{ticker}",
                ),
            ])

        if total_pages > 1:
            lines.append(f"\n[{page + 1}/{total_pages} 페이지]")

        # 페이지네이션 버튼
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                "← 이전", callback_data=f"fav:tab:{category}:{page - 1}",
            ))
        nav_row.append(InlineKeyboardButton(
            "🔙 전체", callback_data="fav:tab::0",
        ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                "다음 →", callback_data=f"fav:tab:{category}:{page + 1}",
            ))
        buttons.append(nav_row)
        buttons.append(make_feedback_row("즐겨찾기탭"))

        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "\n..."

        return text, InlineKeyboardMarkup(buttons)

    async def _build_stock_detail(
        self, ticker: str,
    ) -> tuple[str, InlineKeyboardMarkup]:
        """개별 종목 상세 보기 (보유 현황 포함)."""
        name = self._resolve_name(ticker, ticker)

        # watchlist 정보
        watchlist = self.db.get_watchlist()
        w_item = None
        for w in watchlist:
            if w.get("ticker") == ticker:
                w_item = w
                break
        horizon = (w_item.get("horizon", "") or "") if w_item else ""
        sector = (w_item.get("sector", "") or "") if w_item else ""

        # universe에서 sector 보완
        if not sector:
            for s in self.all_tickers:
                if s["code"] == ticker:
                    sector = s.get("sector", s.get("category", ""))
                    break

        # 현재가
        cur = 0.0
        dc_pct = 0.0
        try:
            detail = await self._get_price_detail(ticker, 0)
            cur = detail["price"]
            dc_pct = detail["day_change_pct"]
        except Exception:
            pass

        # 보유 여부
        holding = self.db.get_holding_by_ticker(ticker)

        hz_label = self._HZ_TAG.get(horizon, "📦미분류")
        de = "📈" if dc_pct > 0 else ("📉" if dc_pct < 0 else "➖")
        ds = "+" if dc_pct > 0 else ""

        lines = [
            f"{de} {name} ({ticker}) — {hz_label}",
            "━" * 22,
        ]
        if cur > 0:
            lines.append(f"현재가: {cur:,.0f}원 ({ds}{dc_pct:.1f}%)")

        if holding:
            bp = float(holding.get("buy_price", 0) or 0)
            qty = int(holding.get("quantity", 0) or 0)
            pnl = 0.0
            if bp > 0 and cur > 0:
                pnl = (cur - bp) / bp * 100
            pe = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
            ps = "+" if pnl > 0 else ""
            lines.append(f"\n{pe} 보유중 | 매수: {bp:,.0f}원 | 손익: {ps}{pnl:.1f}%")
            if qty:
                eval_amt = cur * qty if cur > 0 else 0
                lines.append(f"수량: {qty}주 | 평가: {eval_amt:,.0f}원")

        if sector:
            lines.append(f"섹터: {sector}")

        # v9.0: 산업 생태계 정보
        try:
            from kstock.signal.industry_ecosystem import format_industry_for_telegram
            ind = format_industry_for_telegram(ticker)
            if ind:
                lines.append(ind)
        except Exception:
            pass

        # 버튼
        buttons = [
            [
                InlineKeyboardButton("🔍 AI진단", callback_data=f"fav:diag:{ticker}"),
                InlineKeyboardButton("🤖 분석", callback_data=f"mgr:{horizon or 'swing'}:{ticker}"),
            ],
            [
                InlineKeyboardButton("🎙️ 토론", callback_data=f"debate:{ticker}"),
                InlineKeyboardButton("📰 뉴스", callback_data=f"fav:news:{ticker}"),
                InlineKeyboardButton("📊 차트", callback_data=f"fav:chtm:{ticker}"),
            ],
            [
                InlineKeyboardButton("🔄 분류", callback_data=f"fav:classify:{ticker}"),
                InlineKeyboardButton("💰 매수", callback_data=f"kis_buy:{ticker}"),
            ],
            [
                InlineKeyboardButton("🗑 삭제", callback_data=f"fav:rm:{ticker}"),
                InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0"),
            ],
        ]

        # 돌아가기 + 피드백
        back_cat = horizon if horizon else "unclassified"
        buttons.append([
            InlineKeyboardButton("🔙 돌아가기", callback_data=f"fav:tab:{back_cat}:0"),
            InlineKeyboardButton("👍", callback_data="fb:like:종목상세"),
            InlineKeyboardButton("👎", callback_data="fb:dislike:종목상세"),
        ])

        return "\n".join(lines), InlineKeyboardMarkup(buttons)

    def _rule_based_classify(self, ticker: str) -> str:
        """규칙 기반 빠른 분류. ETF/대형주 등 명확한 종목만."""
        item = None
        for s in self.all_tickers:
            if s["code"] == ticker:
                item = s
                break
        if not item:
            return ""

        category = item.get("category", "")
        sector = item.get("sector", "")
        market = item.get("market", "")

        # ETF 분류
        if category in ("index", "global", "dividend", "sector", "commodity"):
            return "long_term"
        if category in ("leverage", "inverse"):
            return "scalp"

        # 방어적 대형주 → 장기
        if sector in ("금융", "통신", "보험", "담배", "지주"):
            return "long_term"

        # 고성장 KOSDAQ → 스윙
        if market == "KOSDAQ":
            return "swing"

        # 경기순환 → 포지션
        if sector in ("자동차", "조선", "건설", "에너지", "철강", "화학"):
            return "position"

        return ""

    async def _auto_classify_unassigned(self, limit: int = 10) -> int:
        """미분류 종목을 규칙+AI로 일괄 분류."""
        watchlist = self.db.get_watchlist()
        unclassified = [
            w for w in watchlist
            if not w.get("horizon") and w.get("active", 1)
        ][:limit]

        classified = 0
        for w in unclassified:
            ticker = w["ticker"]
            name = w.get("name", ticker)
            try:
                hz = self._rule_based_classify(ticker)
                if not hz:
                    try:
                        from kstock.bot.investment_managers import recommend_investment_type
                        hz = await recommend_investment_type(ticker, name)
                    except Exception:
                        logger.debug("AI classify failed for %s", ticker, exc_info=True)
                if hz:
                    self.db.update_watchlist_horizon(ticker, hz, hz)
                    classified += 1
            except Exception:
                logger.debug("auto_classify failed for %s", ticker, exc_info=True)
        return classified

    async def _build_favorites_view(self) -> tuple[str, InlineKeyboardMarkup] | None:
        """(하위호환) 기존 즐겨찾기 뷰 → 대시보드로 리다이렉트."""
        counts = self.db.get_watchlist_category_counts()
        if counts.get("total", 0) == 0:
            return None
        return await self._build_dashboard_view()

    async def _menu_favorites(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """⭐ 즐겨찾기 → 종목 관리 대시보드."""
        # 유니버스 동기화 (첫 실행 또는 종목 부족 시)
        counts = self.db.get_watchlist_category_counts()
        if counts.get("total", 0) < len(self.all_tickers) // 2:
            added = await self._sync_universe_to_watchlist()
            if added > 0:
                counts = self.db.get_watchlist_category_counts()

        if counts.get("total", 0) == 0:
            await update.message.reply_text(
                "⭐ 종목 관리 대시보드\n\n"
                "등록된 종목이 없습니다. 유니버스 설정을 확인해주세요.",
                reply_markup=get_reply_markup(context),
            )
            return

        text, markup = await self._build_dashboard_view()
        await update.message.reply_text(text, reply_markup=markup)

    async def _action_favorites(self, query, context, payload: str = "") -> None:
        """즐겨찾기 콜백: fav:add:{ticker}:{name} / fav:rm:{ticker} / fav:refresh."""
        parts = payload.split(":")
        action = parts[0] if parts else ""

        if action == "add":
            ticker = parts[1] if len(parts) > 1 else ""
            name = parts[2] if len(parts) > 2 else ticker
            if ticker:
                self.db.add_watchlist(ticker, name)
                await safe_edit_or_reply(query,
                    f"⭐ {name}({ticker})을 즐겨찾기에 등록했습니다!",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("⭐ 즐겨찾기 보기", callback_data="fav:refresh"),
                            InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0"),
                        ],
                    ]),
                )
            return

        if action == "add_mode":
            # 종목 추가 모드: 채팅에 종목명 입력하라고 안내
            context.user_data["awaiting_fav_add"] = True
            await safe_edit_or_reply(query,
                "⭐ 종목 추가\n\n"
                "추가할 종목명을 채팅창에 입력하세요.\n"
                "예: 에코프로비엠, 삼성전자"
            )
            return

        if action == "ai_rec":
            # AI가 종목 특성 분석 → 투자유형 자동 추천
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            await safe_edit_or_reply(query,f"🤖 {name} 투자유형 분석 중...")
            try:
                from kstock.bot.investment_managers import recommend_investment_type
                rec_hz = await recommend_investment_type(ticker, name)
                if rec_hz:
                    from kstock.bot.investment_managers import MANAGERS
                    mgr = MANAGERS.get(rec_hz, {})
                    self.db.update_watchlist_horizon(ticker, rec_hz, rec_hz)
                    await safe_edit_or_reply(query,
                        f"🤖 AI 추천 완료: {name}\n\n"
                        f"유형: {mgr.get('emoji', '')} {mgr.get('title', rec_hz)}\n"
                        f"담당: {mgr.get('name', '')}",
                        reply_markup=InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                                InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                            ],
                            [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
                        ]),
                    )
                else:
                    # AI 추천 실패 → 수동 분류로 전환
                    await self._action_favorites(query, context, f"classify:{ticker}")
            except Exception:
                logger.debug("_action_favorites auto_classify failed for %s, falling back to manual", ticker, exc_info=True)
                await self._action_favorites(query, context, f"classify:{ticker}")
            return

        if action == "diag":
            # v8.7: AI 종목 진단 — 매수/매도/관망 + 근거 + 적정가
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            await safe_edit_or_reply(query, f"🔍 {name} AI 진단 중...")

            try:
                # 1) 현재가 + 등락
                cur = 0.0
                dc_pct = 0.0
                try:
                    detail = await self._get_price_detail(ticker, 0)
                    cur = detail["price"]
                    dc_pct = detail["day_change_pct"]
                except Exception:
                    pass

                # 2) 기술적 지표
                tech_text = ""
                try:
                    market = "KRX"
                    for s in self.all_tickers:
                        if s["code"] == ticker:
                            market = s.get("market", "KRX")
                            break
                    ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="3mo")
                    if ohlcv is not None and len(ohlcv) > 5:
                        from kstock.features.technical import compute_indicators
                        tech = compute_indicators(ohlcv)
                        tech_text = (
                            f"RSI: {tech.rsi:.1f}, BB%B: {tech.bb_pctb:.2f}, "
                            f"MACD크로스: {'상향' if tech.macd_signal_cross > 0 else '하향' if tech.macd_signal_cross < 0 else '중립'}, "
                            f"거래량비: {tech.volume_ratio:.1f}배"
                        )
                        if tech.high_52w and tech.high_52w > 0:
                            drop = (cur - tech.high_52w) / tech.high_52w * 100 if cur > 0 else 0
                            tech_text += f", 52주고점대비: {drop:+.1f}%"
                except Exception:
                    logger.debug("fav:diag tech failed for %s", ticker, exc_info=True)

                # 3) 보유 정보
                hold_text = "미보유"
                holding = None
                for h in self.db.get_active_holdings():
                    if h.get("ticker") == ticker:
                        holding = h
                        break
                if holding:
                    bp = float(holding.get("buy_price", 0) or 0)
                    pnl = ((cur - bp) / bp * 100) if bp > 0 and cur > 0 else 0
                    hold_text = f"보유중 (매수가: {bp:,.0f}원, 손익: {pnl:+.1f}%)"

                # 4) 매크로 환경
                macro_text = ""
                try:
                    macro = await self.macro_client.get_snapshot()
                    alert_mode = getattr(self, "_alert_mode", "normal")
                    macro_text = (
                        f"코스피: {macro.kospi:,.0f}, VIX: {macro.vix:.1f}, "
                        f"환율: {macro.usdkrw:,.0f}원, 경계모드: {alert_mode}"
                    )
                except Exception:
                    pass

                # 5) AI 진단 요청
                prompt = (
                    f"종목: {name}({ticker})\n"
                    f"현재가: {cur:,.0f}원 (등락: {dc_pct:+.1f}%)\n"
                    f"보유: {hold_text}\n"
                )
                if tech_text:
                    prompt += f"기술지표: {tech_text}\n"
                if macro_text:
                    prompt += f"시장환경: {macro_text}\n"
                prompt += (
                    "\n위 데이터 기반으로 이 종목에 대해 간결하게 진단해주세요:\n"
                    "1. 판단: 매수/매도/관망 중 하나\n"
                    "2. 적정 매수가격대 (현재가 대비)\n"
                    "3. 근거 (기술적 + 펀더멘탈 2~3줄)\n"
                    "4. 리스크 요인 1줄\n"
                    "5. 보유자 조언 (보유중이면) 또는 진입 전략 (미보유면)\n"
                    "반드시 한국어로, 총 15줄 이내로 답변하세요."
                )

                from kstock.bot.chat_handler import handle_ai_question
                from kstock.bot.context_builder import build_full_context_with_macro
                from kstock.bot.chat_memory import ChatMemory
                context_text = await build_full_context_with_macro(
                    self.db, macro_client=self.macro_client,
                )
                chat_mem = ChatMemory(self.db)
                diag_result = await handle_ai_question(
                    prompt, context_text, self.db, chat_mem,
                )

                header = f"🔍 {name} AI 진단\n{'━' * 22}\n\n"
                result_text = header + diag_result

            except Exception as e:
                logger.error("fav:diag failed for %s: %s", ticker, e, exc_info=True)
                result_text = f"⚠️ {name} 진단 실패: {e}"

            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                    InlineKeyboardButton("📊 차트", callback_data=f"fav:chtm:{ticker}"),
                ],
                [
                    InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                    InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0"),
                ],
            ])
            await query.message.reply_text(
                result_text[:4000], reply_markup=nav_kb,
            )
            return

        if action == "buy_scan":
            # v8.7: 전체 관심종목 매수 추천 스캔
            import asyncio as _aio
            await safe_edit_or_reply(query, "📈 관심종목 매수 스캔 중... (30초 소요)")

            try:
                watchlist = self.db.get_watchlist()
                holdings = self.db.get_active_holdings()
                held_tickers = {h["ticker"] for h in holdings}
                # 보유 종목 제외, 분류된 종목만 (미분류는 제외)
                candidates = [
                    w for w in watchlist
                    if w["ticker"] not in held_tickers
                    and w.get("horizon") in ("scalp", "swing", "position", "long_term")
                ]

                if not candidates:
                    await query.message.reply_text(
                        "📈 스캔 대상 종목이 없습니다.\n"
                        "즐겨찾기에서 종목을 분류해주세요.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh")],
                        ]),
                    )
                    return

                from kstock.features.technical import compute_indicators
                from kstock.bot.investment_managers import compute_recovery_score

                async def _scan_one(w):
                    """한 종목 기술 스캔."""
                    ticker = w["ticker"]
                    result = {"ticker": ticker, "name": (w.get("name") or ticker)[:8],
                              "horizon": w.get("horizon", ""), "score": 0, "ok": False}
                    try:
                        detail = await self._get_price_detail(ticker, 0)
                        price = detail["price"]
                        dc = detail["day_change_pct"]
                        result["price"] = price
                        result["dc"] = dc

                        market = "KOSPI"
                        for s in self.all_tickers:
                            if s["code"] == ticker:
                                market = s.get("market", "KOSPI")
                                break
                        ohlcv = await self.yf_client.get_ohlcv(ticker, market, period="3mo")
                        if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 20:
                            tech = compute_indicators(ohlcv)
                            result["rsi"] = tech.rsi
                            result["bb_pctb"] = tech.bb_pctb
                            result["macd_cross"] = tech.macd_signal_cross
                            result["vol_ratio"] = tech.volume_ratio
                            result["score"] = compute_recovery_score(tech, dc)
                            result["ok"] = True
                    except Exception:
                        pass
                    return result

                # 병렬 스캔 (최대 20개씩 배치)
                batch_size = 20
                all_results = []
                for i in range(0, len(candidates), batch_size):
                    batch = candidates[i:i + batch_size]
                    batch_results = await _aio.gather(
                        *[_scan_one(w) for w in batch],
                        return_exceptions=True,
                    )
                    for r in batch_results:
                        if isinstance(r, dict) and r.get("ok"):
                            all_results.append(r)

                # 점수순 정렬, 상위 10개
                all_results.sort(key=lambda x: x["score"], reverse=True)
                top = all_results[:10]

                hz_emoji = {"scalp": "⚡", "swing": "🔥", "position": "📊", "long_term": "💎"}
                lines = [
                    f"📈 매수 추천 스캔 ({len(all_results)}종목 분석)",
                    "━" * 22,
                ]

                if not top:
                    lines.append("\n스캔 결과가 없습니다.")
                else:
                    for i, r in enumerate(top, 1):
                        he = hz_emoji.get(r.get("horizon"), "📌")
                        score = r["score"]
                        # 점수에 따른 신호등
                        if score >= 60:
                            sig = "🟢 매수"
                        elif score >= 40:
                            sig = "🟡 관심"
                        else:
                            sig = "⚪ 보류"
                        rsi = r.get("rsi", 50)
                        mc = r.get("macd_cross", 0)
                        mc_txt = "↑" if mc > 0 else ("↓" if mc < 0 else "-")
                        vr = r.get("vol_ratio", 1)
                        price = r.get("price", 0)
                        dc = r.get("dc", 0)
                        ds = "+" if dc > 0 else ""
                        lines.append(
                            f"{i}. {he}{r['name']} {sig} ({score}점)\n"
                            f"   {price:,.0f}원({ds}{dc:.1f}%) "
                            f"RSI:{rsi:.0f} MACD:{mc_txt} 거래량:{vr:.1f}배"
                        )

                    lines.append(f"\n{'━' * 22}")
                    lines.append("🟢60+점=매수 🟡40+점=관심 ⚪보류")

                # 상위 종목 버튼 (최대 6개, 2열)
                buttons = []
                btn_row = []
                for r in top[:6]:
                    cb = f"fav:diag:{r['ticker']}"
                    if len(cb) <= 64:
                        btn_row.append(InlineKeyboardButton(
                            f"🔍 {r['name']}", callback_data=cb,
                        ))
                    if len(btn_row) == 3:
                        buttons.append(btn_row)
                        btn_row = []
                if btn_row:
                    buttons.append(btn_row)

                buttons.append([
                    InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                    InlineKeyboardButton("👍", callback_data="fb:like:매수추천"),
                    InlineKeyboardButton("👎", callback_data="fb:dislike:매수추천"),
                    InlineKeyboardButton("❌", callback_data="dismiss:0"),
                ])

                await query.message.reply_text(
                    "\n".join(lines)[:4000],
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception as e:
                logger.error("fav:buy_scan failed: %s", e, exc_info=True)
                await query.message.reply_text(
                    f"⚠️ 매수 스캔 실패: {e}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh")],
                    ]),
                )
            return

        if action == "chtm":
            # v9.2: 차트 모드 선택 메뉴
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            buttons = [
                [
                    InlineKeyboardButton("📊 기본", callback_data=f"fav:ch0:{ticker}"),
                    InlineKeyboardButton("📈 확장(MACD+수급)", callback_data=f"fav:ch1:{ticker}"),
                ],
                [
                    InlineKeyboardButton("📅 주봉", callback_data=f"fav:ch2:{ticker}"),
                    InlineKeyboardButton("🔀 일봉+주봉", callback_data=f"fav:ch3:{ticker}"),
                ],
                [
                    InlineKeyboardButton("💰 밸류에이션", callback_data=f"fav:ch4:{ticker}"),
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                ],
            ]
            await safe_edit_or_reply(
                query,
                f"📊 {name} ({ticker}) 차트 선택\n\n"
                f"기본: 캔들+BB+RSI\n"
                f"확장: +MACD+수급+공매도+시그널선\n"
                f"주봉: 주봉캔들+매집점수\n"
                f"일봉+주봉: 멀티타임프레임\n"
                f"밸류에이션: PER밴드+적정가",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action in ("chart", "ch0"):
            # 기본 차트 (캔들+BB+RSI)
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 다른 차트", callback_data=f"fav:chtm:{ticker}"),
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                ],
            ])
            await safe_edit_or_reply(query, f"📊 {name} 기본 차트 생성 중...")
            try:
                from kstock.features.chart_gen import generate_stock_chart
                chart_path = await generate_stock_chart(ticker, name)
                if chart_path:
                    with open(chart_path, "rb") as f:
                        await query.message.reply_photo(
                            photo=f,
                            caption=f"📊 {name} ({ticker}) 기본 차트",
                        )
                    await query.message.reply_text("📊 차트 완료", reply_markup=nav_kb)
                else:
                    await query.message.reply_text(f"📊 {name}: 데이터 없음", reply_markup=nav_kb)
            except Exception:
                logger.debug("chart ch0 failed for %s", ticker, exc_info=True)
                await query.message.reply_text(f"📊 {name}: 차트 생성 실패", reply_markup=nav_kb)
            return

        if action == "ch1":
            # 확장 차트 (MACD+수급+공매도+시그널선)
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 다른 차트", callback_data=f"fav:chtm:{ticker}"),
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                ],
            ])
            await safe_edit_or_reply(query, f"📈 {name} 확장 차트 생성 중...")
            try:
                from kstock.features.chart_gen import generate_full_chart
                supply = self.db.get_supply_demand(ticker, days=60)
                short = self.db.get_short_selling(ticker, days=60) if hasattr(self.db, "get_short_selling") else None
                # 보유종목이면 매수/손절/목표가 표시
                bp, sp, t1, t2 = 0, 0, 0, 0
                for h in self.db.get_active_holdings():
                    if h.get("ticker") == ticker:
                        bp = float(h.get("buy_price") or 0)
                        sp = float(h.get("stop_price") or 0)
                        t1 = float(h.get("target_1") or 0)
                        t2 = float(h.get("target_2") or 0)
                        break
                chart_path = await generate_full_chart(
                    ticker, name, days=60,
                    supply_data=supply, short_data=short,
                    buy_price=bp, stop_price=sp, target_1=t1, target_2=t2,
                )
                if chart_path:
                    with open(chart_path, "rb") as f:
                        await query.message.reply_photo(
                            photo=f,
                            caption=f"📈 {name} ({ticker}) 확장 차트 (MACD+수급+공매도)",
                        )
                    await query.message.reply_text("📈 확장 차트 완료", reply_markup=nav_kb)
                else:
                    await query.message.reply_text(f"📈 {name}: 데이터 없음", reply_markup=nav_kb)
            except Exception:
                logger.debug("chart ch1 failed for %s", ticker, exc_info=True)
                await query.message.reply_text(f"📈 {name}: 확장 차트 실패", reply_markup=nav_kb)
            return

        if action == "ch2":
            # 주봉 차트 + 매집 점수
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 다른 차트", callback_data=f"fav:chtm:{ticker}"),
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                ],
            ])
            await safe_edit_or_reply(query, f"📅 {name} 주봉 차트 생성 중...")
            try:
                from kstock.features.chart_gen import generate_weekly_chart
                acc_score = None
                try:
                    from kstock.features.weekly_pattern import analyze_weekly_accumulation
                    from kstock.features.chart_gen import _fetch_ohlcv, _ensure_numeric
                    raw = _fetch_ohlcv(ticker, 180)
                    if not raw.empty:
                        raw = _ensure_numeric(raw)
                        supply = self.db.get_supply_demand(ticker, days=20)
                        result = analyze_weekly_accumulation(raw, supply)
                        acc_score = {
                            "total": result.total,
                            "pattern": result.pattern,
                        }
                except Exception:
                    pass
                chart_path = await generate_weekly_chart(ticker, name, accumulation_score=acc_score)
                if chart_path:
                    caption = f"📅 {name} ({ticker}) 주봉 차트"
                    if acc_score:
                        caption += f"\n매집점수: {acc_score['total']}/100"
                        if acc_score.get("pattern"):
                            caption += f" | {acc_score['pattern']}"
                    with open(chart_path, "rb") as f:
                        await query.message.reply_photo(photo=f, caption=caption)
                    await query.message.reply_text("📅 주봉 차트 완료", reply_markup=nav_kb)
                else:
                    await query.message.reply_text(f"📅 {name}: 데이터 없음", reply_markup=nav_kb)
            except Exception:
                logger.debug("chart ch2 failed for %s", ticker, exc_info=True)
                await query.message.reply_text(f"📅 {name}: 주봉 차트 실패", reply_markup=nav_kb)
            return

        if action == "ch3":
            # 멀티타임프레임 (일봉+주봉)
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 다른 차트", callback_data=f"fav:chtm:{ticker}"),
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                ],
            ])
            await safe_edit_or_reply(query, f"🔀 {name} 멀티타임프레임 차트 생성 중...")
            try:
                from kstock.features.chart_gen import generate_mtf_chart
                chart_path = await generate_mtf_chart(ticker, name)
                if chart_path:
                    with open(chart_path, "rb") as f:
                        await query.message.reply_photo(
                            photo=f,
                            caption=f"🔀 {name} ({ticker}) 일봉+주봉 멀티타임프레임",
                        )
                    await query.message.reply_text("🔀 MTF 차트 완료", reply_markup=nav_kb)
                else:
                    await query.message.reply_text(f"🔀 {name}: 데이터 없음", reply_markup=nav_kb)
            except Exception:
                logger.debug("chart ch3 failed for %s", ticker, exc_info=True)
                await query.message.reply_text(f"🔀 {name}: MTF 차트 실패", reply_markup=nav_kb)
            return

        if action == "ch4":
            # 밸류에이션 밴드
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 다른 차트", callback_data=f"fav:chtm:{ticker}"),
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                ],
            ])
            await safe_edit_or_reply(query, f"💰 {name} 밸류에이션 밴드 생성 중...")
            try:
                from kstock.features.chart_gen import generate_valuation_band
                per, sector_per, fair = 0, 0, 0
                fin = self.db.get_financials(ticker)
                if fin:
                    per = float(fin.get("per", 0) or 0)
                cons = self.db.get_consensus(ticker)
                if cons:
                    fair = float(cons.get("avg_target_price", 0) or 0)
                chart_path = await generate_valuation_band(
                    ticker, name, per=per, sector_per=sector_per, fair_price=fair,
                )
                if chart_path:
                    caption = f"💰 {name} ({ticker}) 밸류에이션 밴드"
                    if per > 0:
                        caption += f"\nPER: {per:.1f}x"
                    if fair > 0:
                        caption += f" | 컨센서스 적정가: {fair:,.0f}원"
                    with open(chart_path, "rb") as f:
                        await query.message.reply_photo(photo=f, caption=caption)
                    await query.message.reply_text("💰 밸류에이션 차트 완료", reply_markup=nav_kb)
                else:
                    await query.message.reply_text(f"💰 {name}: 데이터 없음", reply_markup=nav_kb)
            except Exception:
                logger.debug("chart ch4 failed for %s", ticker, exc_info=True)
                await query.message.reply_text(f"💰 {name}: 밸류에이션 차트 실패", reply_markup=nav_kb)
            return

        if action == "news":
            # 종목별 뉴스 조회
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)
            nav_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                    InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                ],
            ])
            try:
                from kstock.bot.news_action import format_stock_news_brief
                from kstock.ingest.naver_finance import get_stock_news
                news = await get_stock_news(ticker, limit=5)
                if news:
                    text = format_stock_news_brief(name, news[:5])
                    await safe_edit_or_reply(query, text, reply_markup=nav_kb)
                else:
                    await safe_edit_or_reply(query, f"📰 {name}: 최근 뉴스가 없습니다.", reply_markup=nav_kb)
            except Exception:
                logger.debug("_action_favorites news fetch failed for %s", ticker, exc_info=True)
                await safe_edit_or_reply(query, f"📰 {name}: 뉴스 조회 실패", reply_markup=nav_kb)
            return

        if action == "rm":
            ticker = parts[1] if len(parts) > 1 else ""
            if ticker:
                name = self._resolve_name(ticker, ticker)
                self.db.remove_watchlist(ticker)
                await safe_edit_or_reply(query,
                    f"⭐ {name} 즐겨찾기에서 삭제되었습니다.",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                            InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0"),
                        ],
                    ]),
                )
            return

        if action == "classify":
            # 종목 투자유형 분류 → AI 추천 + 4개 유형 한 화면
            ticker = parts[1] if len(parts) > 1 else ""
            name = self._resolve_name(ticker, ticker)

            # AI 추천 비동기로 시도
            ai_line = ""
            try:
                from kstock.bot.investment_managers import recommend_investment_type, MANAGERS
                await safe_edit_or_reply(query,f"🤖 {name} 분석 중...")
                rec_hz = await recommend_investment_type(ticker, name)
                if rec_hz:
                    mgr = MANAGERS.get(rec_hz, {})
                    ai_line = f"\n🤖 AI 추천: {mgr.get('emoji', '')} {mgr.get('title', rec_hz)} ({mgr.get('name', '')})\n"
            except Exception:
                logger.debug("_action_favorites recommend_investment_type failed for %s", ticker, exc_info=True)

            buttons = [
                [
                    InlineKeyboardButton("⚡ 단타", callback_data=f"fav:set_hz:scalp:{ticker}"),
                    InlineKeyboardButton("🔥 스윙", callback_data=f"fav:set_hz:swing:{ticker}"),
                ],
                [
                    InlineKeyboardButton("📊 포지션", callback_data=f"fav:set_hz:position:{ticker}"),
                    InlineKeyboardButton("💎 장기", callback_data=f"fav:set_hz:long_term:{ticker}"),
                ],
                [InlineKeyboardButton("⭐ 돌아가기", callback_data="fav:refresh")],
            ]
            await safe_edit_or_reply(query,
                f"🔄 {name} 투자유형 분류\n"
                f"{ai_line}\n"
                f"⚡ 단타: 1~3일 (제시 리버모어)\n"
                f"🔥 스윙: 1~4주 (윌리엄 오닐)\n"
                f"📊 포지션: 1~6개월 (피터 린치)\n"
                f"💎 장기: 6개월+ (워렌 버핏)",
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

            await safe_edit_or_reply(query,
                f"✅ {name} 투자유형 설정 완료\n\n"
                f"유형: {mgr_emoji} {mgr.get('title', horizon)}\n"
                f"담당: {mgr_name}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔙 종목상세", callback_data=f"fav:stock:{ticker}"),
                        InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh"),
                    ],
                    [InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")],
                ]),
            )
            return

        if action == "managers":
            # v8.6: 매니저 도메인 대시보드 — 보유+관심 통합
            from collections import defaultdict
            from kstock.bot.investment_managers import MANAGERS, MANAGER_THRESHOLDS

            holdings = self.db.get_active_holdings()
            watchlist = self.db.get_watchlist()
            alert_mode = getattr(self, '_alert_mode', 'normal')

            # 보유종목 매니저별 카운트
            held_by_mgr = defaultdict(int)
            for h in holdings:
                ht = h.get("holding_type", "auto")
                if ht == "auto":
                    ht = "swing"
                held_by_mgr[ht] += 1

            # 관심종목 매니저별 카운트 (horizon 기준)
            wl_by_mgr = defaultdict(int)
            unclassified = 0
            for w in watchlist:
                hz = w.get("horizon", "")
                if hz and hz in MANAGERS:
                    wl_by_mgr[hz] += 1
                elif not hz:
                    unclassified += 1

            lines = ["👨‍💼 투자 매니저 대시보드\n" + "━" * 22]

            if alert_mode == "wartime":
                lines.append("🔴 전시 경계 모드 활성")
            elif alert_mode == "elevated":
                lines.append("🟠 경계 모드 활성")
            lines.append("")

            for mgr_key in ["scalp", "swing", "position", "long_term"]:
                mgr = MANAGERS[mgr_key]
                th = MANAGER_THRESHOLDS[mgr_key]
                held = held_by_mgr.get(mgr_key, 0)
                watch = wl_by_mgr.get(mgr_key, 0)
                lines.append(
                    f"{mgr['emoji']} {mgr['name']}: "
                    f"보유 {held} | 관심 {watch}"
                )
                lines.append(
                    f"  손절 {th['stop_loss']:.0f}% | "
                    f"익절 +{th['take_profit_1']:.0f}%/+{th['take_profit_2']:.0f}%"
                )

            if unclassified > 0:
                lines.append(f"\n📦 미분류: {unclassified}종목")

            buttons = [
                [InlineKeyboardButton(
                    f"{MANAGERS[k]['emoji']} {MANAGERS[k]['name'][:4]} 관리",
                    callback_data=f"mgr_tab:{k}",
                ) for k in ["scalp", "swing"]],
                [InlineKeyboardButton(
                    f"{MANAGERS[k]['emoji']} {MANAGERS[k]['name'][:3]} 관리",
                    callback_data=f"mgr_tab:{k}",
                ) for k in ["position", "long_term"]],
            ]
            if unclassified > 0:
                buttons.append([InlineKeyboardButton(
                    "🤖 전체 자동분류", callback_data="mgr_tab:classify",
                )])
            buttons.append([
                InlineKeyboardButton("🔙 즐겨찾기", callback_data="fav:refresh"),
                InlineKeyboardButton("👍", callback_data="fb:like:매니저"),
                InlineKeyboardButton("👎", callback_data="fb:dislike:매니저"),
                InlineKeyboardButton("❌", callback_data="dismiss:0"),
            ])

            await safe_edit_or_reply(query,
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "tab":
            # 탭 이동: fav:tab:{category}:{page}
            cat = parts[1] if len(parts) > 1 else ""
            pg = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            text, markup = await self._build_dashboard_view(cat, pg)
            await safe_edit_or_reply(query, text, reply_markup=markup)
            return

        if action == "stock":
            # 개별 종목 상세: fav:stock:{ticker}
            ticker = parts[1] if len(parts) > 1 else ""
            if ticker:
                text, markup = await self._build_stock_detail(ticker)
                await safe_edit_or_reply(query, text, reply_markup=markup)
            return

        if action == "auto_classify":
            # 미분류 종목 자동 분류
            await safe_edit_or_reply(query, "🤖 미분류 종목 자동 분류 중...")
            classified = await self._auto_classify_unassigned(limit=75)
            text, markup = await self._build_dashboard_view()
            header = f"🤖 {classified}종목 자동 분류 완료!\n\n"
            await safe_edit_or_reply(query, header + text, reply_markup=markup)
            return

        if action == "refresh":
            text, markup = await self._build_dashboard_view()
            await safe_edit_or_reply(query, text, reply_markup=markup)
            return

        if action == "tb_import":
            # 텐배거 유니버스 20종목을 즐겨찾기에 자동 등록
            try:
                from kstock.signal.tenbagger_screener import get_initial_universe
                universe = get_initial_universe()
                added = 0
                for u in universe:
                    ticker = u["ticker"]
                    name = u["name"]
                    # KRX만 즐겨찾기에 등록 (미국주식은 KIS API 미지원)
                    if u["market"] != "KRX":
                        continue
                    self.db.add_watchlist(ticker, name, horizon="tenbagger", manager="tenbagger")
                    added += 1
                await safe_edit_or_reply(query,
                    f"🔟 텐배거 유니버스 등록 완료!\n\n"
                    f"한국 {added}종목을 즐겨찾기 텐배거 탭에 추가했습니다.\n"
                    f"(미국 종목은 별도 관리)",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔟 텐배거 보기", callback_data="fav:tab:tenbagger:0")],
                        [InlineKeyboardButton("⭐ 즐겨찾기", callback_data="fav:refresh")],
                    ]),
                )
            except Exception as exc:
                logger.exception("tb_import failed")
                await safe_edit_or_reply(query, f"❌ 텐배거 등록 실패: {exc}")
            return

        if action == "tb_report":
            # 텐배거 유니버스 PDF 리포트 생성 & 전송
            try:
                from kstock.report.tenbagger_pdf_report import (
                    generate_tenbagger_report,
                    format_tenbagger_report_text,
                )
                await safe_edit_or_reply(query, "🔟 텐배거 리포트 생성 중...")

                pdf_path = generate_tenbagger_report()
                if pdf_path and os.path.exists(pdf_path):
                    text_msg = format_tenbagger_report_text()
                    await context.bot.send_message(
                        chat_id=self.chat_id, text=text_msg,
                    )
                    with open(pdf_path, "rb") as f:
                        await context.bot.send_document(
                            chat_id=self.chat_id, document=f,
                        )
                else:
                    await context.bot.send_message(
                        chat_id=self.chat_id,
                        text="❌ PDF 생성 실패 (reportlab 미설치?)",
                    )
            except Exception as exc:
                logger.exception("tb_report failed")
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"❌ 텐배거 리포트 실패: {exc}",
                )
            return

    # ── 원격접속 메뉴 ─────────────────────────────────────────

    async def _menu_remote_access(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🖥 원격접속 — 맥미니 연결 방법 3가지 + 실시간 상태."""
        import subprocess as _sp

        # 1. 로컬 IP
        local_ip = "확인 불가"
        try:
            out = _sp.check_output(
                ["ifconfig", "en0"], text=True, timeout=3,
            )
            for line in out.splitlines():
                if "inet " in line and "127." not in line:
                    local_ip = line.strip().split()[1]
                    break
        except Exception:
            pass

        # 2. Tailscale IP
        ts_ip = ""
        ts_status = "❌ 미연결"
        try:
            out = _sp.check_output(
                ["/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                 "ip", "-4"],
                text=True, timeout=3,
            ).strip()
            if out and out.startswith("100."):
                ts_ip = out
                ts_status = f"✅ {ts_ip}"
        except Exception:
            pass

        # 3. SSH 상태
        ssh_status = "❌ 비활성"
        try:
            out = _sp.check_output(
                ["launchctl", "list"], text=True, timeout=3,
            )
            if "com.openssh.sshd" in out:
                ssh_status = "✅ 활성"
        except Exception:
            pass

        # 4. Screen Sharing 상태
        vnc_status = "✅ 활성"  # macOS 기본 활성

        lines = [
            "🖥 맥미니 원격접속",
            "━" * 22,
            "",
            "1️⃣ 텔레그램 Claude (지금 사용 중)",
            "   💻 클로드 버튼 → 명령 입력",
            "   어디서든 가능, 코드 수정/시스템 제어",
            "",
            "2️⃣ SSH (터미널)",
            f"   상태: {ssh_status}",
            f"   로컬: ssh botddol@{local_ip}",
        ]
        if ts_ip:
            lines.append(f"   외부: ssh botddol@{ts_ip}")
        lines.extend([
            "",
            "3️⃣ 화면 공유 (원격 데스크톱)",
            f"   상태: {vnc_status}",
            f"   로컬: vnc://{local_ip}",
        ])
        if ts_ip:
            lines.append(f"   외부: vnc://{ts_ip}")
        lines.extend([
            "",
            "━" * 22,
            f"🌐 Tailscale: {ts_status}",
            f"📡 로컬 IP: {local_ip}",
        ])

        buttons = [
            [InlineKeyboardButton(
                "🔄 SSH 활성화", callback_data="remote:ssh",
            )],
            [InlineKeyboardButton(
                "🌐 Tailscale 로그인", callback_data="remote:tailscale",
            )],
            [InlineKeyboardButton(
                "📋 접속정보 복사용", callback_data="remote:copy",
            )],
        ]

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_remote(self, query, context, payload: str) -> None:
        """remote:* 콜백 — 원격접속 관련 액션."""
        import subprocess as _sp

        if payload == "ssh":
            # SSH 활성화 시도
            try:
                _sp.run(
                    ["sudo", "-n", "systemsetup", "-setremotelogin", "on"],
                    capture_output=True, text=True, timeout=5,
                )
                await safe_edit_or_reply(query,
                    "✅ SSH 활성화 시도 완료.\n\n"
                    "admin 비밀번호가 필요하면 맥미니에서 직접:\n"
                    "시스템 설정 → 일반 → 공유 → 원격 로그인 켜기"
                )
            except Exception:
                await safe_edit_or_reply(query,
                    "⚠️ SSH 활성화에 admin 권한이 필요합니다.\n\n"
                    "맥미니에서 직접 설정하세요:\n"
                    "시스템 설정 → 일반 → 공유 → 원격 로그인 켜기"
                )

        elif payload == "tailscale":
            try:
                _sp.Popen(
                    ["open", "/Applications/Tailscale.app"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                )
                await safe_edit_or_reply(query,
                    "🌐 Tailscale 앱을 실행했습니다.\n\n"
                    "맥미니 화면에서 로그인해주세요.\n"
                    "로그인 후 맥북에서도 같은 계정으로 Tailscale 설치."
                )
            except Exception:
                await safe_edit_or_reply(query,
                    "⚠️ Tailscale 앱을 찾을 수 없습니다.\n"
                    "Mac App Store에서 Tailscale을 설치하세요."
                )

        elif payload == "copy":
            import subprocess as _sp
            local_ip = "172.30.1.61"
            try:
                out = _sp.check_output(
                    ["ifconfig", "en0"], text=True, timeout=3,
                )
                for line in out.splitlines():
                    if "inet " in line and "127." not in line:
                        local_ip = line.strip().split()[1]
                        break
            except Exception:
                pass

            ts_ip = ""
            try:
                out = _sp.check_output(
                    ["/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                     "ip", "-4"],
                    text=True, timeout=3,
                ).strip()
                if out and out.startswith("100."):
                    ts_ip = out
            except Exception:
                pass

            copy_text = (
                f"SSH: ssh botddol@{local_ip}\n"
                f"VNC: vnc://{local_ip}\n"
            )
            if ts_ip:
                copy_text += (
                    f"SSH(외부): ssh botddol@{ts_ip}\n"
                    f"VNC(외부): vnc://{ts_ip}\n"
                )

            await safe_edit_or_reply(query,
                f"📋 접속 정보\n\n```\n{copy_text}```",
                parse_mode="Markdown",
            )

    # ── 에이전트 대화 메뉴 ─────────────────────────────────────────

    async def _menu_agent_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """🤖 에이전트 → 클로드 모드로 통합 (v10.3.1)."""
        context.user_data.pop("agent_mode", None)
        context.user_data["claude_mode"] = True
        context.user_data["claude_turn"] = 0
        await update.message.reply_text(
            "🤖 AI 에이전트 (Claude)\n"
            f"{'━' * 22}\n\n"
            "주식 질문 → Sonnet (정밀 분석)\n"
            "일반 대화 → Haiku (빠른 응답)\n\n"
            "자유롭게 대화하세요.\n"
            "🔙 종료하려면 다른 메뉴 버튼을 누르세요.",
            reply_markup=get_reply_markup(context),
        )

    async def _action_agent(self, query, context, payload: str = "") -> None:
        """에이전트 콜백 → 클로드 모드로 통합 (v10.3.1)."""
        if payload in ("bug", "feature", "question"):
            context.user_data.pop("agent_mode", None)
            context.user_data.pop("agent_type", None)
            context.user_data["claude_mode"] = True
            context.user_data["claude_turn"] = 0
            labels = {"bug": "🐛 오류/버그", "feature": "💡 기능 요청", "question": "❓ 질문"}
            await safe_edit_or_reply(query,
                f"{labels.get(payload, '')} → Claude 모드 전환\n\n"
                "자유롭게 메시지를 입력하세요.\n"
                "스크린샷도 보낼 수 있습니다."
            )
        elif payload == "v8doc":
            # v11.0: 버전 설명서 PDF 생성 및 전송
            await safe_edit_or_reply(query, f"📋 {DISPLAY_VERSION} 기능 설명서 PDF 생성 중...")
            try:
                import subprocess as _sp
                _here = os.path.abspath(__file__)
                proj = os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.dirname(_here)))))
                script = os.path.join(proj, "scripts", "gen_v110_doc.py")
                if not os.path.exists(script):
                    proj = os.environ.get("PROJECT_DIR", "/Users/botddol/k-quant-system")
                    script = os.path.join(proj, "scripts", "gen_v110_doc.py")
                if not os.path.exists(script):
                    await query.message.reply_text("⚠️ gen_v110_doc.py 스크립트를 찾을 수 없습니다.")
                    return
                import sys as _sys
                result = _sp.run(
                    [_sys.executable, script],
                    capture_output=True, text=True, timeout=30,
                    cwd=proj, env={**os.environ, "PYTHONPATH": os.path.join(proj, "src")},
                )
                from datetime import datetime as _dt
                pdf_name = f"K-Quant_v110_Features_{_dt.now().strftime('%Y%m%d')}.pdf"
                pdf_path = os.path.join(proj, "reports", pdf_name)
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f:
                        await query.message.reply_document(
                            document=f,
                            filename="K-Quant_v110_Features.pdf",
                            caption=f"📋 {SYSTEM_NAME} 기능 설명서",
                        )
                else:
                    await query.message.reply_text(
                        f"⚠️ PDF 생성 실패\n{result.stderr[:500] if result.stderr else '알 수 없는 오류'}")
            except Exception as e:
                logger.error("v110doc generation failed: %s", e, exc_info=True)
                await query.message.reply_text(f"⚠️ 설명서 생성 실패: {e}")
            return
        elif payload == "exit":
            context.user_data.pop("agent_mode", None)
            context.user_data.pop("agent_type", None)
            await safe_edit_or_reply(query,"🔙 에이전트 모드를 종료했습니다.")


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
            await safe_edit_or_reply(query,
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
                await safe_edit_or_reply(query,"\n".join(lines))
            else:
                await safe_edit_or_reply(query,"📈 아직 추천 내역이 없습니다.")

    # ── v8.5: 온보딩 + 오늘의 할 일 ───────────────────────────────────

    async def _menu_onboarding(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """인터랙티브 온보딩 — 7단계 가이드 투어."""
        import json
        progress_raw = self.db.get_meta("onboarding_progress")
        progress = json.loads(progress_raw) if progress_raw else {"step": 1}
        step_num = progress.get("step", 1)
        if step_num > len(ONBOARDING_STEPS):
            text = format_onboarding_complete()
            buttons = [[
                InlineKeyboardButton("🔄 처음부터 다시", callback_data="onboard:restart:0"),
                InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0"),
            ]]
            await update.message.reply_text(
                text, reply_markup=InlineKeyboardMarkup(buttons),
            )
            return
        step = ONBOARDING_STEPS[step_num - 1]
        text = format_onboarding_step(step, step_num, len(ONBOARDING_STEPS))
        buttons = []
        buttons.append([InlineKeyboardButton(
            step["try_label"], callback_data=step["try_cb"],
        )])
        nav = []
        if step_num < len(ONBOARDING_STEPS):
            nav.append(InlineKeyboardButton(
                "다음 >>", callback_data=f"onboard:next:{step_num + 1}",
            ))
        nav.append(InlineKeyboardButton(
            "건너뛰기", callback_data="onboard:skip:0",
        ))
        buttons.append(nav)
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_onboarding(self, query, context, payload: str) -> None:
        """온보딩 콜백 — try/next/skip/restart."""
        import json
        sub, _, val = payload.partition(":")

        if sub == "restart":
            self.db.set_meta("onboarding_progress", json.dumps({"step": 1}))
            step = ONBOARDING_STEPS[0]
            text = format_onboarding_step(step, 1, len(ONBOARDING_STEPS))
            buttons = [
                [InlineKeyboardButton(step["try_label"], callback_data=step["try_cb"])],
                [InlineKeyboardButton("다음 >>", callback_data="onboard:next:2"),
                 InlineKeyboardButton("건너뛰기", callback_data="onboard:skip:0")],
            ]
            await safe_edit_or_reply(
                query, text, reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if sub == "skip":
            self.db.set_meta("onboarding_progress", json.dumps({
                "step": len(ONBOARDING_STEPS) + 1,
            }))
            await safe_edit_or_reply(query, format_onboarding_complete())
            return

        if sub == "next":
            step_num = int(val) if val else 2
            self.db.set_meta("onboarding_progress", json.dumps({"step": step_num}))
            if step_num > len(ONBOARDING_STEPS):
                await safe_edit_or_reply(query, format_onboarding_complete())
                return
            step = ONBOARDING_STEPS[step_num - 1]
            text = format_onboarding_step(step, step_num, len(ONBOARDING_STEPS))
            buttons = [
                [InlineKeyboardButton(step["try_label"], callback_data=step["try_cb"])],
            ]
            nav = []
            if step_num < len(ONBOARDING_STEPS):
                nav.append(InlineKeyboardButton(
                    "다음 >>", callback_data=f"onboard:next:{step_num + 1}",
                ))
            nav.append(InlineKeyboardButton(
                "건너뛰기", callback_data="onboard:skip:0",
            ))
            buttons.append(nav)
            await safe_edit_or_reply(
                query, text, reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if sub == "try":
            # 체험 → 해당 메뉴로 연결
            progress_raw = self.db.get_meta("onboarding_progress")
            progress = json.loads(progress_raw) if progress_raw else {"step": 1}
            current_step = progress.get("step", 1)
            # 자동으로 다음 스텝 전진
            self.db.set_meta("onboarding_progress", json.dumps({
                "step": current_step + 1,
            }))

            try_map = {
                "analysis": ("📊 분석 체험", self._menu_analysis_hub),
                "balance": ("💰 잔고 체험", self.cmd_balance),
                "favorites": ("⭐ 즐겨찾기 체험", self._menu_favorites),
                "market": ("📈 시황 체험", self._menu_market_status),
                "ai_chat": ("💬 AI비서 체험", self._menu_ai_chat),
                "agents": ("🤖 에이전트 체험", self._menu_agent_chat),
                "alerts": ("🔔 알림 스케줄", self._menu_notification_settings),
            }
            info = try_map.get(val)
            if info:
                label, handler = info
                await safe_edit_or_reply(query, f"✨ {label}으로 이동합니다!")
                # 핸들러 호출 — update.message가 없으므로 query.message로 대체
                class _FakeUpdate:
                    def __init__(self, msg):
                        self.message = msg
                        self.effective_user = query.from_user
                fake = _FakeUpdate(query.message)
                try:
                    await handler(fake, context)
                except Exception as e:
                    logger.debug("onboarding try handler error: %s", e)
            else:
                await safe_edit_or_reply(query, "✅ 체험 기능이 준비 중입니다.")

    async def _menu_daily_actions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """오늘의 할 일 — 수동 접근."""
        await update.message.reply_text("📋 오늘의 할 일 생성 중...")
        try:
            from kstock.bot.investment_managers import build_daily_action_shortcuts

            macro = None
            try:
                macro_client = MacroClient()
                macro = await macro_client.snapshot()
            except Exception:
                pass
            actions = await self._generate_daily_actions(macro)
            coach_lines = self._build_daily_action_coach_lines(actions, macro)
            text = format_daily_actions(
                actions,
                alert_mode=getattr(self, "_alert_mode", "normal"),
                coach_lines=coach_lines,
            )
            buttons = make_shortcut_rows(build_daily_action_shortcuts(actions, max_buttons=5))
            manager_shortcuts = []
            seen_secondary = set()
            for action in actions:
                callback_data = str(action.get("secondary_callback", "") or "")
                manager_label = str(action.get("manager_label", "") or "").strip()
                if not callback_data or not manager_label or callback_data in seen_secondary:
                    continue
                seen_secondary.add(callback_data)
                manager_shortcuts.append({
                    "label": manager_label,
                    "callback_data": callback_data,
                })
                if len(manager_shortcuts) >= 4:
                    break
            if manager_shortcuts:
                buttons.extend(make_shortcut_rows(manager_shortcuts))
            buttons.append([InlineKeyboardButton("❌ 닫기", callback_data="dismiss:0")])
            await send_long_message(
                update.message, text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error("daily_actions error: %s", e, exc_info=True)
            await update.message.reply_text(
                "⚠️ 오늘의 할 일 생성 중 오류가 발생했습니다.",
                reply_markup=get_reply_markup(context),
            )

    # ── v8.6: 매니저 탭 + 매수 스캔 ─────────────────────────────

    async def _action_manager_tab(self, query, context, payload: str) -> None:
        """mgr_tab:{manager_key|classify} — 매니저별 관리화면 또는 자동분류."""
        from kstock.bot.investment_managers import MANAGERS, MANAGER_THRESHOLDS

        if payload == "classify":
            await safe_edit_or_reply(query, "🤖 전체 자동분류 실행 중...")
            classified = await self._auto_classify_unassigned(limit=75)
            buttons = [[InlineKeyboardButton(
                "👨‍💼 매니저 대시보드", callback_data="fav:managers",
            )]]
            await safe_edit_or_reply(
                query,
                f"🤖 자동분류 완료: {classified}종목 분류됨",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        mgr = MANAGERS.get(payload)
        if not mgr:
            await safe_edit_or_reply(query, "⚠️ 알 수 없는 매니저")
            return

        th = MANAGER_THRESHOLDS.get(payload, {})

        # 보유종목 (이 매니저 담당)
        holdings = self.db.get_active_holdings()
        held = [
            h for h in holdings
            if (h.get("holding_type", "auto") == payload)
            or (payload == "swing" and h.get("holding_type") == "auto")
        ]

        # 관심종목 (watchlist에서 horizon 매칭, 보유 제외)
        watchlist = self.db.get_watchlist()
        held_tickers = {h["ticker"] for h in held}
        wl_only = [
            w for w in watchlist
            if w.get("horizon") == payload and w["ticker"] not in held_tickers
        ]

        lines = [
            f"{mgr['emoji']} {mgr['name']} ({mgr['title']})",
            "━" * 22,
            f"손절 {th.get('stop_loss', -5):.0f}% | "
            f"익절 +{th.get('take_profit_1', 5):.0f}%/+{th.get('take_profit_2', 10):.0f}%",
            "",
        ]

        # 보유종목 섹션
        if held:
            lines.append(f"💰 보유 종목 ({len(held)})")
            for h in held[:10]:
                name = (h.get("name", "") or h.get("ticker", ""))[:10]
                pnl = float(h.get("pnl_pct", 0) or 0)
                bp = float(h.get("buy_price", 0) or 0)
                cp = float(h.get("current_price", 0) or 0)
                try:
                    cp_live = await self._get_price(h["ticker"], base_price=bp)
                    if cp_live and isinstance(cp_live, (int, float)):
                        cp = cp_live
                        pnl = (cp - bp) / bp * 100 if bp > 0 else 0
                except Exception:
                    pass
                emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                lines.append(f"  {emoji} {name}: {pnl:+.1f}%")
        else:
            lines.append("💰 보유 종목 없음")

        lines.append("")

        # 관심종목 섹션
        if wl_only:
            lines.append(f"👀 관심 종목 ({len(wl_only)})")
            if len(wl_only) > 8:
                # 8개 초과 시 텍스트로 표시
                for w in wl_only[:10]:
                    name = (w.get("name", "") or w.get("ticker", ""))[:12]
                    lines.append(f"  - {name}")
                if len(wl_only) > 10:
                    lines.append(f"  ... 외 {len(wl_only) - 10}종목")
        else:
            lines.append("👀 관심 종목 없음")

        buttons = []

        # 관심종목 클릭 버튼 (8개까지)
        if wl_only and len(wl_only) <= 8:
            row = []
            for w in wl_only[:8]:
                wname = (w.get("name", "") or w.get("ticker", ""))[:6]
                cb = f"fav:stock:{w['ticker']}"
                if len(cb) <= 64:
                    row.append(InlineKeyboardButton(wname, callback_data=cb))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

        buttons.append(
            [InlineKeyboardButton(
                f"{mgr['emoji']} 보유종목 분석", callback_data=f"mgr:{payload}",
            )],
        )
        buttons.append(
            [InlineKeyboardButton(
                "🔍 매수 스캔", callback_data=f"mgr_scan:{payload}",
            )],
        )
        buttons.append([
            InlineKeyboardButton("👨‍💼 대시보드", callback_data="fav:managers"),
            InlineKeyboardButton("👍", callback_data="fb:like:매니저탭"),
            InlineKeyboardButton("👎", callback_data="fb:dislike:매니저탭"),
            InlineKeyboardButton("❌", callback_data="dismiss:0"),
        ])

        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "\n..."
        await safe_edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))

    async def _action_manager_scan(self, query, context, payload: str) -> None:
        """mgr_scan:{manager_key} — 매니저 관심종목 매수 스캔 (수동)."""
        from kstock.bot.investment_managers import MANAGERS, scan_manager_domain

        mgr = MANAGERS.get(payload)
        if not mgr:
            await safe_edit_or_reply(query, "⚠️ 알 수 없는 매니저")
            return

        await safe_edit_or_reply(
            query, f"{mgr['emoji']} {mgr['name']} 관심종목 매수 스캔 중...",
        )

        # 관심종목 조회 (보유 제외)
        watchlist = self.db.get_watchlist()
        holdings = self.db.get_active_holdings()
        held_tickers = {h["ticker"] for h in holdings}
        stocks = [
            w for w in watchlist
            if w.get("horizon") == payload and w["ticker"] not in held_tickers
        ]

        if not stocks:
            buttons = [[InlineKeyboardButton(
                "👨‍💼 대시보드", callback_data="fav:managers",
            )]]
            await query.message.reply_text(
                f"{mgr['emoji']} {mgr['name']}: 관심종목이 없습니다.\n"
                f"즐겨찾기에서 종목을 분류해주세요.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 기술적 데이터 보강 (가격 + RSI + BB + MACD + 거래량)
        for w in stocks:
            try:
                detail = await self._get_price_detail(w["ticker"], 0)
                w["price"] = detail.get("price", 0)
                w["day_change"] = detail.get("day_change_pct", 0)

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
                    w["vol_ratio"] = tech.volume_ratio * 100
                    if tech.high_52w > 0 and w["price"] > 0:
                        w["drop_from_high"] = (
                            (w["price"] - tech.high_52w) / tech.high_52w
                        ) * 100
                    else:
                        w["drop_from_high"] = 0
                    from kstock.bot.investment_managers import compute_recovery_score
                    w["recovery_score"] = compute_recovery_score(
                        tech, w.get("day_change", 0),
                    )
            except Exception:
                if "price" not in w:
                    w["price"] = 0

        # 시장 상황
        market_text = ""
        try:
            macro_client = MacroClient()
            macro = await macro_client.snapshot()
            market_text = (
                f"VIX={macro.vix:.1f}, S&P={macro.spx_change_pct:+.2f}%, "
                f"환율={macro.usdkrw:,.0f}원"
            )
        except Exception:
            pass

        current_alert = getattr(self, '_alert_mode', 'normal')
        report = await scan_manager_domain(
            payload, stocks, market_text, alert_mode=current_alert,
        )

        if not report:
            report = f"{mgr['emoji']} {mgr['name']}: 분석 실패. 잠시 후 다시 시도해주세요."

        # 관심종목 바로가기 버튼 (최대 6개, 2열)
        buttons = []
        stock_row = []
        for w in stocks[:6]:
            sname = (w.get("name", "") or w.get("ticker", ""))[:6]
            cb = f"fav:stock:{w['ticker']}"
            if len(cb) <= 64:
                stock_row.append(InlineKeyboardButton(f"📋 {sname}", callback_data=cb))
            if len(stock_row) == 3:
                buttons.append(stock_row)
                stock_row = []
        if stock_row:
            buttons.append(stock_row)

        buttons.append(
            [InlineKeyboardButton(
                f"{mgr['emoji']} 관리화면", callback_data=f"mgr_tab:{payload}",
            )],
        )
        buttons.append([
            InlineKeyboardButton("👨‍💼 대시보드", callback_data="fav:managers"),
            InlineKeyboardButton("👍", callback_data="fb:like:매수스캔"),
            InlineKeyboardButton("👎", callback_data="fb:dislike:매수스캔"),
            InlineKeyboardButton("❌", callback_data="dismiss:0"),
        ])
        await query.message.reply_text(
            report[:4000], reply_markup=InlineKeyboardMarkup(buttons),
        )
