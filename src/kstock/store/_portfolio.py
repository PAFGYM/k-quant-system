"""PortfolioMixin: 포트폴리오, 보유종목, 워치리스트, 포트폴리오 스냅샷, 리스크 관련 메서드."""

from __future__ import annotations

from datetime import datetime, timedelta


class PortfolioMixin:
    """포트폴리오 + 보유종목 + 워치리스트 + 포트폴리오 스냅샷 + 리스크 Mixin."""

    # -- portfolio --------------------------------------------------------------

    def upsert_portfolio(
        self,
        ticker: str,
        name: str | None = None,
        score: float | None = None,
        signal: str | None = None,
        sell_code: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio (ticker, name, score, signal, sell_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=COALESCE(excluded.name, portfolio.name),
                    score=COALESCE(excluded.score, portfolio.score),
                    signal=COALESCE(excluded.signal, portfolio.signal),
                    sell_code=COALESCE(excluded.sell_code, portfolio.sell_code),
                    updated_at=excluded.updated_at
                """,
                (ticker, name, score, signal, sell_code, now),
            )

    def get_portfolio(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM portfolio ORDER BY score DESC").fetchall()
        return [dict(r) for r in rows]

    def get_portfolio_entry(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio WHERE ticker=?", (ticker,)
            ).fetchone()
        return dict(row) if row else None

    # -- holdings ---------------------------------------------------------------

    def add_holding(
        self, ticker: str, name: str, buy_price: float,
        holding_type: str = "auto",
    ) -> int:
        now = datetime.utcnow().isoformat()
        target_1 = round(buy_price * 1.03, 0)
        target_2 = round(buy_price * 1.07, 0)
        stop_price = round(buy_price * 0.95, 0)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO holdings
                    (ticker, name, buy_price, current_price, buy_date,
                     target_1, target_2, stop_price, status, sold_pct, pnl_pct,
                     holding_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?, ?)
                """,
                (ticker, name, buy_price, buy_price, now[:10],
                 target_1, target_2, stop_price, holding_type, now, now),
            )
            return cursor.lastrowid

    def get_active_holdings(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT h.*, ph.horizon
                   FROM holdings h
                   LEFT JOIN portfolio_horizon ph ON h.ticker = ph.ticker
                   WHERE h.status='active'
                   ORDER BY h.created_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_holding(self, holding_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE id=?", (holding_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_holding_by_ticker(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE ticker=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_holding_by_name(self, name: str) -> dict | None:
        """종목명으로 active 보유종목 조회 (ticker 없을 때 fallback)."""
        if not name:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE name=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_holding(
        self,
        ticker: str,
        name: str,
        quantity: int = 0,
        buy_price: float = 0,
        current_price: float = 0,
        pnl_pct: float = 0,
        eval_amount: float = 0,
        holding_type: str = "auto",
    ) -> int:
        """스크린샷에서 파싱한 보유종목을 holdings DB에 upsert.

        이미 active인 동일 종목이 있으면 현재가/수익률만 업데이트,
        없으면 신규 등록.
        ticker가 비어있으면 name으로 조회.
        """
        existing = None
        if ticker:
            existing = self.get_holding_by_ticker(ticker)
        if not existing and name:
            existing = self.get_holding_by_name(name)
        # sold/삭제된 종목이 있으면 재등록하지 않음
        if not existing and ticker:
            with self._connect() as conn:
                sold_row = conn.execute(
                    "SELECT id FROM holdings WHERE ticker=? AND status='sold' "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
                if sold_row:
                    return sold_row["id"]
        now = datetime.utcnow().isoformat()
        if existing:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE holdings SET
                        current_price=?, pnl_pct=?, quantity=?,
                        eval_amount=?, name=?, updated_at=?
                    WHERE id=?""",
                    (current_price, pnl_pct, quantity, eval_amount,
                     name, now, existing["id"]),
                )
            return existing["id"]
        else:
            target_1 = round(buy_price * 1.03, 0) if buy_price else 0
            target_2 = round(buy_price * 1.07, 0) if buy_price else 0
            stop_price = round(buy_price * 0.95, 0) if buy_price else 0
            with self._connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO holdings
                        (ticker, name, buy_price, current_price, quantity,
                         eval_amount, buy_date, target_1, target_2, stop_price,
                         status, sold_pct, pnl_pct, holding_type,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?,
                            ?, ?)""",
                    (ticker, name, buy_price, current_price, quantity,
                     eval_amount, now[:10], target_1, target_2, stop_price,
                     pnl_pct, holding_type, now, now),
                )
                return cursor.lastrowid

    def update_holding(self, holding_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(holding_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE holdings SET {', '.join(sets)} WHERE id=?", vals
            )

    def update_holding_type(self, holding_id: int, holding_type: str) -> None:
        """보유종목의 투자전략(holding_type)을 변경합니다."""
        self.update_holding(holding_id, holding_type=holding_type)

    # -- watchlist --------------------------------------------------------------

    def add_watchlist(
        self,
        ticker: str,
        name: str,
        target_price: float | None = None,
        target_rsi: float = 30,
        rec_price: float = 0,
        horizon: str = "",
        manager: str = "",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            # 기존 데이터 보존: rec_price/horizon/manager가 비어있으면 기존 값 유지
            existing = conn.execute(
                "SELECT rec_price, horizon, manager FROM watchlist WHERE ticker=?",
                (ticker,),
            ).fetchone()
            if existing:
                rec_price = rec_price or (existing["rec_price"] if existing["rec_price"] else 0)
                horizon = horizon or (existing["horizon"] if existing["horizon"] else "")
                manager = manager or (existing["manager"] if existing["manager"] else "")
            conn.execute(
                """
                INSERT OR REPLACE INTO watchlist
                    (ticker, name, target_price, target_rsi, active, created_at,
                     rec_price, horizon, manager)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (ticker, name, target_price, target_rsi, now,
                 rec_price, horizon, manager),
            )

    def update_watchlist_horizon(self, ticker: str, horizon: str, manager: str = "") -> None:
        """즐겨찾기 종목의 투자유형/매니저 변경."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE watchlist SET horizon=?, manager=? WHERE ticker=?",
                (horizon, manager, ticker),
            )

    def get_watchlist(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM watchlist WHERE active=1").fetchall()
        return [dict(r) for r in rows]

    def remove_watchlist(self, ticker: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE watchlist SET active=0 WHERE ticker=?", (ticker,))

    # -- portfolio_horizon ------------------------------------------------------

    def upsert_portfolio_horizon(
        self, ticker: str, name: str = "", horizon: str = "dangi",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO portfolio_horizon (ticker, name, horizon, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                       name=COALESCE(excluded.name, portfolio_horizon.name),
                       horizon=excluded.horizon, updated_at=excluded.updated_at""",
                (ticker, name, horizon, now),
            )

    def get_portfolio_horizon(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio_horizon WHERE ticker=?", (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_portfolio_horizons(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio_horizon ORDER BY updated_at DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    # -- portfolio_snapshots ----------------------------------------------------

    def add_portfolio_snapshot(
        self, date_str: str, total_value: float = 0, cash: float = 0,
        holdings_count: int = 0, daily_pnl_pct: float = 0,
        total_pnl_pct: float = 0, mdd: float = 0, peak_value: float = 0,
        kospi_close: float = 0, kosdaq_close: float = 0,
        holdings_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO portfolio_snapshots
                   (date, total_value, cash, holdings_count, daily_pnl_pct,
                    total_pnl_pct, mdd, peak_value, kospi_close, kosdaq_close,
                    holdings_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (date_str, total_value, cash, holdings_count, daily_pnl_pct,
                 total_pnl_pct, mdd, peak_value, kospi_close, kosdaq_close,
                 holdings_json, now),
            )
        return cur.lastrowid or 0

    def get_portfolio_snapshots(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_portfolio_peak(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(total_value) as peak FROM portfolio_snapshots",
            ).fetchone()
        return row["peak"] if row and row["peak"] else 0.0

    # -- risk_violations --------------------------------------------------------

    def add_risk_violation(
        self, date_str: str, violation_type: str, severity: str = "medium",
        description: str = "", recommended_action: str = "", action_taken: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO risk_violations
                   (date, violation_type, severity, description,
                    recommended_action, action_taken, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date_str, violation_type, severity, description,
                 recommended_action, action_taken, now),
            )
        return cur.lastrowid or 0

    def get_risk_violations(self, days: int = 7, limit: int = 50) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM risk_violations WHERE date >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- rebalance_history ------------------------------------------------------

    def add_rebalance_event(
        self, trigger_type: str, description: str = "",
        action: str = "", tickers_json: str = "",
        executed: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rebalance_history
                    (trigger_type, description, action, tickers_json, executed, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trigger_type, description, action, tickers_json, executed, now),
            )
            return cursor.lastrowid

    def get_rebalance_history(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rebalance_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_rebalance_executed(self, rebalance_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE rebalance_history SET executed=1 WHERE id=?",
                (rebalance_id,),
            )

    # -- solution_tracking ------------------------------------------------------

    def add_solution(
        self,
        solution_type: str,
        description: str,
        before_snapshot_id: int | None = None,
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = now[:10]
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO solution_tracking
                    (solution_type, description, suggested_date,
                     before_snapshot_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (solution_type, description, today, before_snapshot_id, now),
            )
        return cur.lastrowid or 0

    def get_pending_solutions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM solution_tracking WHERE executed=0 "
                "ORDER BY created_at DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_solution_executed(
        self,
        solution_id: int,
        after_snapshot_id: int | None = None,
        profit_change_pct: float = 0.0,
        alpha_change: float = 0.0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE solution_tracking SET
                    executed=1, after_snapshot_id=?, profit_change_pct=?, alpha_change=?
                WHERE id=?
                """,
                (after_snapshot_id, profit_change_pct, alpha_change, solution_id),
            )

    def get_solution_history(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM solution_tracking ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_solution_stats(self) -> dict:
        """Return solution execution and effectiveness stats."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM solution_tracking",
            ).fetchone()[0]
            executed = conn.execute(
                "SELECT COUNT(*) FROM solution_tracking WHERE executed=1",
            ).fetchone()[0]
            effective = conn.execute(
                "SELECT COUNT(*) FROM solution_tracking "
                "WHERE executed=1 AND profit_change_pct > 0",
            ).fetchone()[0]
        exec_rate = (executed / total) if total > 0 else 0
        eff_rate = (effective / executed) if executed > 0 else 0
        return {
            "total": total,
            "executed": executed,
            "effective": effective,
            "execution_rate": exec_rate,
            "effectiveness_rate": eff_rate,
        }

    # -- notification_settings --------------------------------------------------

    DEFAULT_NOTIFICATION_SETTINGS = [
        "report_alert", "supply_alert", "earnings_alert",
        "policy_alert", "morning_briefing", "weekly_report",
    ]

    def _ensure_default_notification_settings(self) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            for name in self.DEFAULT_NOTIFICATION_SETTINGS:
                conn.execute(
                    """INSERT OR IGNORE INTO notification_settings
                       (setting_name, enabled, updated_at) VALUES (?, 1, ?)""",
                    (name, now),
                )

    def get_notification_settings(self) -> dict[str, bool]:
        self._ensure_default_notification_settings()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM notification_settings").fetchall()
        return {r["setting_name"]: bool(r["enabled"]) for r in rows}

    def toggle_notification_setting(self, setting_name: str) -> bool:
        """Toggle a notification setting. Returns new state."""
        now = datetime.utcnow().isoformat()
        current = self.get_notification_settings()
        new_val = 0 if current.get(setting_name, True) else 1
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO notification_settings (setting_name, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(setting_name) DO UPDATE SET enabled=?, updated_at=?""",
                (setting_name, new_val, now, new_val, now),
            )
        return bool(new_val)

    # -- goal_snapshots ---------------------------------------------------------

    def add_goal_snapshot(
        self,
        total_asset: float,
        cash: float = 0,
        positions_count: int = 0,
        daily_return: float = 0,
        milestone: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO goal_snapshots
                    (snapshot_date, total_asset, cash, positions_count,
                     daily_return, milestone, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (today, total_asset, cash, positions_count,
                 daily_return, milestone, now),
            )
            return cursor.lastrowid

    def get_goal_snapshots(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goal_snapshots ORDER BY snapshot_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- tenbagger_candidates ---------------------------------------------------

    def add_tenbagger_candidate(
        self,
        ticker: str,
        name: str,
        price_at_found: float,
        conditions_met: int = 0,
        conditions_json: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tenbagger_candidates
                    (ticker, name, found_date, price_at_found, conditions_met,
                     conditions_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'monitoring', ?, ?)
                """,
                (ticker, name, today, price_at_found, conditions_met,
                 conditions_json, now, now),
            )
            return cursor.lastrowid

    def get_active_tenbagger_candidates(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenbagger_candidates WHERE status='monitoring' "
                "ORDER BY conditions_met DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_tenbagger_candidate(self, candidate_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(candidate_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tenbagger_candidates SET {', '.join(sets)} WHERE id=?", vals
            )
