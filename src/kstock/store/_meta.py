"""MetaMixin: 채팅, 사용자, 알림, 피드백, 미래전망, 기타 메서드."""

from __future__ import annotations

from datetime import datetime, timedelta


class MetaMixin:
    """채팅 + 사용자 + 알림 + 피드백 + 미래전망 + 기타 Mixin."""

    # -- alerts -----------------------------------------------------------------

    def insert_alert(self, ticker: str, alert_type: str, message: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alerts (ticker, alert_type, message, created_at) VALUES (?,?,?,?)",
                (ticker, alert_type, message, now),
            )

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def has_recent_alert(self, ticker: str, alert_type: str, hours: int = 4) -> bool:
        """Check if a similar alert was sent recently (spam prevention)."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE ticker=? AND alert_type=? AND created_at>?",
                (ticker, alert_type, cutoff),
            ).fetchone()
        return dict(row)["cnt"] > 0

    # -- chat_history (v3.5) ---------------------------------------------------

    def add_chat_message(self, role: str, content: str) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, now),
            )
            return cursor.lastrowid

    def get_recent_chat_messages(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = [dict(r) for r in rows]
        result.reverse()
        return result

    def cleanup_old_chat_messages(self, hours: int = 24) -> int:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_history WHERE created_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def clear_chat_history(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_history")

    # -- chat_usage (v3.5) -----------------------------------------------------

    def get_chat_usage_count(self, date: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM chat_usage WHERE date=?", (date,)
            ).fetchone()
        return row["count"] if row else 0

    def increment_chat_usage(self, date: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_usage (date, count) VALUES (?, 1)
                ON CONFLICT(date) DO UPDATE SET count = count + 1
                """,
                (date,),
            )

    # -- users ------------------------------------------------------------------

    def add_user(self, telegram_id: int, name: str, is_admin: bool = False,
                 config_json: str = "{}") -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO users
                   (telegram_id, name, is_admin, config_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (telegram_id, name, 1 if is_admin else 0, config_json, now, now),
            )
        return cur.lastrowid or 0

    def get_user(self, telegram_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id=?",
                (telegram_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_user(self, telegram_id: int, config_json: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET config_json=?, updated_at=? WHERE telegram_id=?",
                (config_json, now, telegram_id),
            )

    # -- user_feedback ---------------------------------------------------------

    def add_user_feedback(self, menu_name: str, feedback: str, comment: str = "") -> None:
        """사용자 피드백 저장 (좋아요/싫어요/상/중/하)."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_feedback (menu_name, feedback, comment, created_at) "
                "VALUES (?, ?, ?, ?)",
                (menu_name, feedback, comment, now),
            )

    def get_today_feedback(self) -> list[dict]:
        """오늘 피드백 조회."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_feedback WHERE created_at LIKE ?",
                (f"{today}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_feedback_stats(self, days: int = 7) -> dict:
        """최근 N일 피드백 통계."""
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT menu_name, feedback, COUNT(*) as cnt "
                "FROM user_feedback WHERE created_at > ? "
                "GROUP BY menu_name, feedback ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- feedback_reports (v3.0+) ----------------------------------------------

    def add_feedback_report(
        self,
        report_date: str,
        period_days: int = 7,
        total_recs: int = 0,
        hits: int = 0,
        misses: int = 0,
        pending: int = 0,
        hit_rate: float = 0,
        avg_return: float = 0,
        lessons_json: str = "",
        strategy_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback_reports
                    (report_date, period_days, total_recs, hits, misses, pending,
                     hit_rate, avg_return, lessons_json, strategy_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (report_date, period_days, total_recs, hits, misses, pending,
                 hit_rate, avg_return, lessons_json, strategy_json, now),
            )
            return cursor.lastrowid

    def get_latest_feedback_report(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM feedback_reports ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # -- screenshots (v3.0) ----------------------------------------------------

    def add_screenshot(
        self,
        total_eval: float = 0,
        total_profit: float = 0,
        total_profit_pct: float = 0,
        cash: float = 0,
        portfolio_score: int = 0,
        holdings_json: str = "",
        image_hash: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO screenshots
                    (image_hash, recognized_at, total_eval, total_profit,
                     total_profit_pct, cash, portfolio_score, holdings_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (image_hash, now, total_eval, total_profit,
                 total_profit_pct, cash, portfolio_score, holdings_json, now),
            )
            return cursor.lastrowid

    def get_last_screenshot(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM screenshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # Alias for backward compatibility
    get_latest_screenshot = get_last_screenshot

    def get_screenshot_history(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screenshots ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_screenshot_holding(
        self,
        screenshot_id: int,
        ticker: str,
        name: str,
        quantity: int = 0,
        avg_price: float = 0,
        current_price: float = 0,
        profit_pct: float = 0,
        eval_amount: float = 0,
        diagnosis: str = "",
        diagnosis_action: str = "",
        diagnosis_msg: str = "",
        is_margin: int = 0,
        margin_type: str = "",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO screenshot_holdings
                    (screenshot_id, ticker, name, quantity, avg_price,
                     current_price, profit_pct, eval_amount,
                     diagnosis, diagnosis_action, diagnosis_msg,
                     is_margin, margin_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (screenshot_id, ticker, name, quantity, avg_price,
                 current_price, profit_pct, eval_amount,
                 diagnosis, diagnosis_action, diagnosis_msg,
                 is_margin, margin_type),
            )
            return cursor.lastrowid

    def get_screenshot_holdings(self, screenshot_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screenshot_holdings WHERE screenshot_id=?",
                (screenshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- investment_horizons ----------------------------------------------------

    def add_investment_horizon(
        self,
        ticker: str,
        name: str,
        horizon: str = "default",
        screenshot_id: int | None = None,
        stop_pct: float | None = None,
        target_pct: float | None = None,
        trailing_pct: float | None = None,
        is_margin: int = 0,
        margin_type: str | None = None,
        diagnosis: str = "",
        diagnosis_action: str = "",
        diagnosis_msg: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO investment_horizons
                    (ticker, name, horizon, screenshot_id,
                     stop_pct, target_pct, trailing_pct,
                     is_margin, margin_type,
                     diagnosis, diagnosis_action, diagnosis_msg,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, horizon, screenshot_id,
                 stop_pct, target_pct, trailing_pct,
                 is_margin, margin_type,
                 diagnosis, diagnosis_action, diagnosis_msg,
                 now, now),
            )
            return cursor.lastrowid

    def get_investment_horizon(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM investment_horizons WHERE ticker=? ORDER BY updated_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_horizons_for_screenshot(self, screenshot_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM investment_horizons WHERE screenshot_id=?",
                (screenshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- future_watchlist -------------------------------------------------------

    def upsert_future_watchlist(
        self,
        ticker: str,
        name: str,
        sector: str,
        tier: str,
        future_score: int = 0,
        tech_maturity: int = 0,
        financial_stability: int = 0,
        policy_benefit: int = 0,
        momentum: int = 0,
        valuation: int = 0,
        entry_signal: str = "WAIT",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO future_watchlist
                    (ticker, name, sector, tier, future_score,
                     tech_maturity, financial_stability, policy_benefit,
                     momentum, valuation, entry_signal, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, sector=excluded.sector, tier=excluded.tier,
                    future_score=excluded.future_score,
                    tech_maturity=excluded.tech_maturity,
                    financial_stability=excluded.financial_stability,
                    policy_benefit=excluded.policy_benefit,
                    momentum=excluded.momentum, valuation=excluded.valuation,
                    entry_signal=excluded.entry_signal, updated_at=excluded.updated_at
                """,
                (ticker, name, sector, tier, future_score,
                 tech_maturity, financial_stability, policy_benefit,
                 momentum, valuation, entry_signal, now),
            )

    def get_future_watchlist(self, sector: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if sector:
                rows = conn.execute(
                    "SELECT * FROM future_watchlist WHERE sector=? ORDER BY future_score DESC",
                    (sector,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM future_watchlist ORDER BY sector, future_score DESC",
                ).fetchall()
        return [dict(r) for r in rows]

    def get_future_watchlist_entry(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM future_watchlist WHERE ticker=?", (ticker,),
            ).fetchone()
        return dict(row) if row else None

    # -- future_triggers --------------------------------------------------------

    def add_future_trigger(
        self,
        sector: str,
        trigger_type: str,
        impact: str,
        title: str,
        source: str = "",
        matched_keywords: str = "",
        beneficiary_tickers: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO future_triggers
                    (sector, trigger_type, impact, title, source,
                     matched_keywords, beneficiary_tickers, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sector, trigger_type, impact, title, source,
                 matched_keywords, beneficiary_tickers, now),
            )
        return cur.lastrowid or 0

    def get_future_triggers(
        self, sector: str | None = None, days: int = 7, limit: int = 20,
    ) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            if sector:
                rows = conn.execute(
                    "SELECT * FROM future_triggers WHERE sector=? AND created_at>=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (sector, cutoff, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM future_triggers WHERE created_at>=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- multi_agent_results ---------------------------------------------------

    def add_multi_agent_result(
        self, ticker: str, name: str = "",
        technical_score: int = 0, fundamental_score: int = 0,
        sentiment_score: int = 0, combined_score: int = 0,
        verdict: str = "", confidence: str = "",
        strategist_summary: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO multi_agent_results (ticker, name, technical_score, "
                "fundamental_score, sentiment_score, combined_score, verdict, "
                "confidence, strategist_summary, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ticker, name, technical_score, fundamental_score,
                 sentiment_score, combined_score, verdict, confidence,
                 strategist_summary, now),
            )
        return cur.lastrowid or 0

    def get_multi_agent_results(self, ticker: str | None = None, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM multi_agent_results WHERE ticker=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (ticker, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM multi_agent_results ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- investor_profile (Phase 9) --------------------------------------------

    def get_investor_profile(self) -> dict | None:
        """투자자 프로필 반환."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM investor_profile WHERE id=1"
            ).fetchone()
        return dict(row) if row else None

    def upsert_investor_profile(self, **kwargs) -> None:
        """투자자 프로필 생성 또는 업데이트."""
        now = datetime.utcnow().isoformat()
        existing = self.get_investor_profile()
        if existing:
            sets = ["updated_at=?"]
            vals: list = [now]
            for k, v in kwargs.items():
                sets.append(f"{k}=?")
                vals.append(v)
            vals.append(1)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE investor_profile SET {', '.join(sets)} WHERE id=1",
                    vals,
                )
        else:
            cols = ["id", "updated_at"]
            placeholders = ["1", "?"]
            vals = [now]
            for k, v in kwargs.items():
                cols.append(k)
                placeholders.append("?")
                vals.append(v)
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO investor_profile ({', '.join(cols)}) "
                    f"VALUES ({', '.join(placeholders)})",
                    vals,
                )

    # -- holding_analysis -------------------------------------------------------

    def get_holding_analysis(self, holding_id: int) -> dict | None:
        """보유종목 분석 데이터 반환."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holding_analysis WHERE holding_id=?",
                (holding_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_holding_analyses(self) -> list[dict]:
        """모든 활성 보유종목 분석 데이터."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ha.* FROM holding_analysis ha "
                "JOIN holdings h ON ha.holding_id = h.id "
                "WHERE h.status = 'active' ORDER BY ha.updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_holding_analysis(
        self, holding_id: int, ticker: str, name: str, **kwargs
    ) -> None:
        """보유종목 분석 생성 또는 업데이트."""
        now = datetime.utcnow().isoformat()
        existing = self.get_holding_analysis(holding_id)
        if existing:
            sets = ["updated_at=?"]
            vals: list = [now]
            for k, v in kwargs.items():
                sets.append(f"{k}=?")
                vals.append(v)
            vals.append(holding_id)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE holding_analysis SET {', '.join(sets)} "
                    f"WHERE holding_id=?",
                    vals,
                )
        else:
            cols = ["holding_id", "ticker", "name", "created_at", "updated_at"]
            placeholders = ["?", "?", "?", "?", "?"]
            vals = [holding_id, ticker, name, now, now]
            for k, v in kwargs.items():
                cols.append(k)
                placeholders.append("?")
                vals.append(v)
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO holding_analysis ({', '.join(cols)}) "
                    f"VALUES ({', '.join(placeholders)})",
                    vals,
                )

    # -- compute_investor_stats -------------------------------------------------

    def compute_investor_stats(self) -> dict:
        """매매 이력으로 투자 성향 통계 계산."""
        with self._connect() as conn:
            # 완료된 거래 통계
            trades = conn.execute(
                "SELECT * FROM holdings WHERE status != 'active'"
            ).fetchall()

        if not trades:
            return {
                "trade_count": 0, "win_rate": 0, "avg_hold_days": 0,
                "avg_profit_pct": 0, "avg_loss_pct": 0,
                "style": "신규", "risk_tolerance": "medium",
            }

        wins = 0
        total_profit = 0.0
        total_loss = 0.0
        profit_count = 0
        loss_count = 0
        total_days = 0

        for t in trades:
            t = dict(t)
            pnl = t.get("pnl_pct", 0)
            # 보유 기간 계산
            try:
                buy = datetime.fromisoformat(t["buy_date"])
                sell = datetime.fromisoformat(t["updated_at"])
                days = (sell - buy).days
            except (ValueError, TypeError, KeyError):
                days = 0
            total_days += max(days, 0)

            if pnl > 0:
                wins += 1
                total_profit += pnl
                profit_count += 1
            elif pnl < 0:
                total_loss += abs(pnl)
                loss_count += 1

        count = len(trades)
        avg_hold = total_days / count if count else 0
        win_rate = (wins / count * 100) if count else 0
        avg_profit = total_profit / profit_count if profit_count else 0
        avg_loss = total_loss / loss_count if loss_count else 0

        # 투자 스타일 자동 판단
        if avg_hold <= 3:
            style = "scalper"
        elif avg_hold <= 14:
            style = "swing"
        elif avg_hold <= 60:
            style = "position"
        else:
            style = "long_term"

        # 리스크 성향
        if avg_loss > 10 or win_rate < 40:
            risk = "aggressive"
        elif avg_loss < 3 and win_rate > 60:
            risk = "conservative"
        else:
            risk = "medium"

        return {
            "trade_count": count,
            "win_rate": round(win_rate, 1),
            "avg_hold_days": round(avg_hold, 1),
            "avg_profit_pct": round(avg_profit, 1),
            "avg_loss_pct": round(avg_loss, 1),
            "style": style,
            "risk_tolerance": risk,
        }

    # -- sentiment (v3.0) ------------------------------------------------------

    def add_sentiment(
        self,
        ticker: str,
        analysis_date: str,
        positive_pct: float = 0,
        negative_pct: float = 0,
        neutral_pct: float = 0,
        headline_count: int = 0,
        summary: str = "",
        score_bonus: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sentiment
                    (ticker, analysis_date, positive_pct, negative_pct,
                     neutral_pct, headline_count, summary, score_bonus, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, analysis_date, positive_pct, negative_pct,
                 neutral_pct, headline_count, summary, score_bonus, now),
            )
            return cursor.lastrowid

    def get_sentiment(self, ticker: str, analysis_date: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sentiment WHERE ticker=? AND analysis_date=? "
                "ORDER BY created_at DESC LIMIT 1",
                (ticker, analysis_date),
            ).fetchone()
        return dict(row) if row else None

    def get_all_sentiments(self, analysis_date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sentiment WHERE analysis_date=?",
                (analysis_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- weekly_reports ---------------------------------------------------------

    def add_weekly_report(
        self,
        week_label: str,
        week_start: str,
        week_end: str,
        doc_url: str = "",
        summary_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO weekly_reports
                    (week_label, week_start, week_end, doc_url, summary_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (week_label, week_start, week_end, doc_url, summary_json, now),
            )
            return cursor.lastrowid

    def get_latest_weekly_report(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM weekly_reports ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return dict(row) if row else None

    def get_weekly_reports(self, limit: int = 4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM weekly_reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- event_log (v5.0) -------------------------------------------------------

    def add_event(
        self, event_type: str, severity: str, message: str,
        source: str = "", ticker: str = "", order_id: str = "",
        data_json: str = "",
    ) -> None:
        """이벤트 로그 저장."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO event_log "
                "(event_type, severity, message, source, ticker, order_id, data_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_type, severity, message, source, ticker, order_id,
                 data_json or None, now),
            )

    def get_events(self, event_type: str = "", limit: int = 50) -> list[dict]:
        """이벤트 로그 조회."""
        with self._connect() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM event_log WHERE event_type=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM event_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- reconciliation_log (v5.0) -----------------------------------------------

    def add_reconciliation(
        self, status: str, internal_count: int, broker_count: int,
        matched_count: int, mismatch_count: int, safety_level: str,
        mismatches_json: str = "",
    ) -> None:
        """리컨실레이션 결과 저장."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO reconciliation_log "
                "(status, internal_count, broker_count, matched_count, "
                "mismatch_count, safety_level, mismatches_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (status, internal_count, broker_count, matched_count,
                 mismatch_count, safety_level, mismatches_json or None, now),
            )

    def get_reconciliations(self, limit: int = 10) -> list[dict]:
        """리컨실레이션 이력 조회."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reconciliation_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- execution_replay (v5.0) -----------------------------------------------

    def add_execution_replay(
        self, ticker: str, strategy: str, side: str,
        signal_price: float, execution_price: float,
        slippage_pct: float, pnl_pct: float,
        bt_predicted_return: float = 0.0, bt_win_prob: float = 0.0,
        direction_match: int | None = None,
    ) -> None:
        """Execution Replay 기록 저장."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO execution_replay "
                "(ticker, strategy, side, signal_price, execution_price, "
                "slippage_pct, pnl_pct, bt_predicted_return, bt_win_prob, "
                "direction_match, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, strategy, side, signal_price, execution_price,
                 slippage_pct, pnl_pct, bt_predicted_return, bt_win_prob,
                 direction_match, now),
            )

    def get_execution_replays(self, limit: int = 50) -> list[dict]:
        """Execution Replay 이력 조회."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_replay ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_execution_replays_by_strategy(self, strategy: str, limit: int = 50) -> list[dict]:
        """전략별 Execution Replay 이력 조회."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_replay WHERE strategy=? "
                "ORDER BY created_at DESC LIMIT ?",
                (strategy, limit),
            ).fetchall()
        return [dict(r) for r in rows]
