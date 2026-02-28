"""TradingMixin: 거래, 주문, 스윙, 추천, 성능추적, ML 관련 메서드."""

from __future__ import annotations

from datetime import datetime, timedelta


class TradingMixin:
    """거래 + 주문 + 스윙 + 추천 + 성능추적 + ML Mixin."""

    # -- trades ----------------------------------------------------------------

    def add_trade(
        self,
        ticker: str,
        name: str,
        action: str,
        strategy_type: str = "A",
        recommended_price: float = 0,
        action_price: float = 0,
        quantity_pct: float = 0,
        pnl_pct: float = 0,
        recommendation_id: int | None = None,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades
                    (ticker, name, strategy_type, action, recommended_price,
                     action_price, quantity_pct, pnl_pct, recommendation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, strategy_type, action, recommended_price,
                 action_price, quantity_pct, pnl_pct, recommendation_id, now),
            )
            return cursor.lastrowid

    def get_trades(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trades_by_strategy(self, strategy_type: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE strategy_type=? ORDER BY created_at DESC",
                (strategy_type,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_strategy_performance(self) -> dict:
        """Compute per-strategy performance stats from trades + recommendations."""
        result = {}
        for strat in ["A", "B", "C", "D", "E", "F", "G"]:
            with self._connect() as conn:
                recs = conn.execute(
                    "SELECT * FROM recommendations WHERE strategy_type=? "
                    "AND status IN ('profit', 'stop')",
                    (strat,),
                ).fetchall()
                total = len(recs)
                if total == 0:
                    continue
                profits = [dict(r) for r in recs if dict(r)["status"] == "profit"]
                win_rate = len(profits) / total * 100
                pnls = [dict(r).get("pnl_pct", 0) for r in recs]
                avg_pnl = sum(pnls) / len(pnls) if pnls else 0
                result[strat] = {
                    "total": total,
                    "wins": len(profits),
                    "win_rate": round(win_rate, 1),
                    "avg_pnl": round(avg_pnl, 2),
                }

        # Summary from trades
        with self._connect() as conn:
            total_trades = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades"
            ).fetchone()
            buys = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='buy'"
            ).fetchone()
            skips = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='skip'"
            ).fetchone()
            stops = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='stop_loss'"
            ).fetchone()
            holds_through = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='hold_through_stop'"
            ).fetchone()

        total_reco = dict(buys)["cnt"] + dict(skips)["cnt"]
        exec_rate = (dict(buys)["cnt"] / total_reco * 100) if total_reco > 0 else 0
        total_stop_events = dict(stops)["cnt"] + dict(holds_through)["cnt"]
        stop_compliance = (dict(stops)["cnt"] / total_stop_events * 100) if total_stop_events > 0 else 100

        result["summary"] = {
            "total_trades": dict(total_trades)["cnt"],
            "execution_rate": round(exec_rate, 1),
            "stop_compliance": round(stop_compliance, 1),
            "avg_hold_days": 0,  # TODO: compute from actual dates
        }
        return result

    # -- orders (v3.0) ---------------------------------------------------------

    def add_order(
        self,
        ticker: str,
        name: str,
        order_type: str,
        side: str,
        quantity: int,
        price: float = 0,
        order_id: str = "",
        status: str = "pending",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders
                    (ticker, name, order_type, side, quantity, price,
                     order_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, order_type, side, quantity, price,
                 order_id, status, now),
            )
            return cursor.lastrowid

    def update_order(self, order_db_id: int, **kwargs) -> None:
        sets = []
        vals: list = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(order_db_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE orders SET {', '.join(sets)} WHERE id=?", vals
            )

    def get_pending_orders(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_order_count(self) -> int:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM orders WHERE created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
        return dict(row)["cnt"]

    # -- swing_trades (v3.0+) --------------------------------------------------

    def add_swing_trade(
        self,
        ticker: str,
        name: str,
        entry_price: float,
        target_price: float = 0,
        stop_price: float = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO swing_trades
                    (ticker, name, entry_date, entry_price, target_price,
                     stop_price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (ticker, name, today, entry_price, target_price,
                 stop_price, now, now),
            )
            return cursor.lastrowid

    def get_active_swing_trades(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM swing_trades WHERE status='active' "
                "ORDER BY entry_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_swing_trade(self, trade_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(trade_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE swing_trades SET {', '.join(sets)} WHERE id=?", vals
            )

    # -- recommendations -------------------------------------------------------

    def add_recommendation(
        self,
        ticker: str,
        name: str,
        rec_price: float,
        rec_score: float,
        status: str = "active",
        sell_reason: str | None = None,
        strategy_type: str = "A",
        target_pct: float = 3.0,
        stop_pct: float = -5.0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        target_1 = round(rec_price * (1 + target_pct / 100), 0)
        target_2 = round(rec_price * (1 + target_pct * 2 / 100), 0)
        stop_price = round(rec_price * (1 + stop_pct / 100), 0)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO recommendations
                    (ticker, name, rec_date, rec_price, rec_score, strategy_type,
                     sell_reason, current_price, pnl_pct, status,
                     target_1, target_2, stop_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, now[:10], rec_price, rec_score, strategy_type,
                 sell_reason, rec_price, status, target_1, target_2, stop_price,
                 now, now),
            )
            return cursor.lastrowid

    def get_active_recommendations(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE status='active' ORDER BY rec_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_completed_recommendations(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE status IN ('profit', 'stop') "
                "ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_watch_recommendations(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE status='watch' ORDER BY rec_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_recommendations_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations"
            ).fetchone()
            active = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='active'"
            ).fetchone()
            profit = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='profit'"
            ).fetchone()
            stop = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='stop'"
            ).fetchone()
            watch = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations WHERE status='watch'"
            ).fetchone()
            avg_pnl = conn.execute(
                "SELECT AVG(pnl_pct) as avg_pnl FROM recommendations "
                "WHERE status IN ('profit', 'stop')"
            ).fetchone()
            avg_active_pnl = conn.execute(
                "SELECT AVG(pnl_pct) as avg_pnl FROM recommendations "
                "WHERE status='active'"
            ).fetchone()
        return {
            "total": dict(total)["cnt"],
            "active": dict(active)["cnt"],
            "profit": dict(profit)["cnt"],
            "stop": dict(stop)["cnt"],
            "watch": dict(watch)["cnt"],
            "avg_closed_pnl": dict(avg_pnl)["avg_pnl"] or 0.0,
            "avg_active_pnl": dict(avg_active_pnl)["avg_pnl"] or 0.0,
        }

    def update_recommendation(self, rec_id: int, **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        sets = ["updated_at=?"]
        vals: list = [now]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(rec_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE recommendations SET {', '.join(sets)} WHERE id=?", vals
            )

    def get_recommendations_by_strategy(self, strategy_type: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE strategy_type=? "
                "AND status IN ('active', 'watch') ORDER BY rec_score DESC",
                (strategy_type,),
            ).fetchall()
        return [dict(r) for r in rows]

    def has_active_recommendation(self, ticker: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM recommendations "
                "WHERE ticker=? AND status IN ('active', 'watch')",
                (ticker,),
            ).fetchone()
        return dict(row)["cnt"] > 0

    # -- recommendation_results (v3.0+) ----------------------------------------

    def add_recommendation_result(
        self,
        recommendation_id: int,
        ticker: str,
        rec_price: float,
        strategy_type: str = "A",
        regime_at_rec: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO recommendation_results
                    (recommendation_id, ticker, rec_price, strategy_type,
                     regime_at_rec, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (recommendation_id, ticker, rec_price, strategy_type,
                 regime_at_rec, now),
            )
            return cursor.lastrowid

    def update_recommendation_result(self, result_id: int, **kwargs) -> None:
        sets = []
        vals: list = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(result_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE recommendation_results SET {', '.join(sets)} WHERE id=?", vals
            )

    def get_recommendation_results(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_results WHERE created_at > ? "
                "ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- recommendation_tracking ------------------------------------------------

    def add_recommendation_track(
        self, ticker: str, name: str, strategy: str, score: float,
        recommended_date: str, entry_price: float,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO recommendation_tracking
                   (ticker, name, strategy, score, recommended_date,
                    entry_price, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ticker, name, strategy, score, recommended_date,
                 entry_price, now),
            )
        return cur.lastrowid or 0

    def update_recommendation_track(
        self, track_id: int, **kwargs,
    ) -> None:
        allowed = {"price_d1", "price_d3", "price_d5", "price_d10", "price_d20",
                    "return_d1", "return_d3", "return_d5", "return_d10", "return_d20", "hit"}
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return
        vals.append(track_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE recommendation_tracking SET {', '.join(sets)} WHERE id=?",
                vals,
            )

    def get_recommendation_tracks(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_tracking "
                "ORDER BY recommended_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- trade_executions -------------------------------------------------------

    def add_trade_execution(
        self, ticker: str, name: str, direction: str = "buy",
        quantity: int = 0, price: float = 0, amount: float = 0,
        commission: float = 0, strategy: str = "", score: float = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO trade_executions
                   (ticker, name, direction, quantity, price, amount,
                    commission, strategy, score, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticker, name, direction, quantity, price, amount,
                 commission, strategy, score, now),
            )
        return cur.lastrowid or 0

    def get_trade_executions(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_executions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- strategy_stats --------------------------------------------------------

    def add_strategy_stat(
        self, strategy: str, period: str, total_count: int,
        win_count: int, win_rate: float, avg_return: float,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO strategy_stats (strategy, period, total_count, win_count, "
                "win_rate, avg_return, calculated_at) VALUES (?,?,?,?,?,?,?)",
                (strategy, period, total_count, win_count, win_rate, avg_return, now),
            )
        return cur.lastrowid or 0

    def get_strategy_stats(self, strategy: str | None = None, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            if strategy:
                rows = conn.execute(
                    "SELECT * FROM strategy_stats WHERE strategy=? "
                    "ORDER BY calculated_at DESC LIMIT ?",
                    (strategy, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM strategy_stats ORDER BY calculated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- ml_predictions (v3.0) -------------------------------------------------

    def add_prediction(
        self,
        ticker: str,
        pred_date: str,
        probability: float,
        shap_top3: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ml_predictions
                    (ticker, pred_date, probability, shap_top3, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ticker, pred_date, probability, shap_top3, now),
            )
            return cursor.lastrowid

    def get_predictions(self, pred_date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ml_predictions WHERE pred_date=?",
                (pred_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- ml_performance ---------------------------------------------------------

    def add_ml_performance(
        self, date_str: str, model_version: str = "",
        train_score: float = 0, val_score: float = 0,
        overfit_gap: float = 0, features_used: int = 0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO ml_performance
                   (date, model_version, train_score, val_score,
                    overfit_gap, features_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date_str, model_version, train_score, val_score,
                 overfit_gap, features_used, now),
            )
        return cur.lastrowid or 0

    def get_ml_performance(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ml_performance ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- hallucination_log ------------------------------------------------------

    def add_hallucination_log(
        self, date_str: str, query: str = "", response_preview: str = "",
        verified_count: int = 0, unverified_count: int = 0,
        unverified_claims: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO hallucination_log
                   (date, query, response_preview, verified_count,
                    unverified_count, unverified_claims, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date_str, query, response_preview, verified_count,
                 unverified_count, unverified_claims, now),
            )
        return cur.lastrowid or 0

    def get_hallucination_stats(self, days: int = 7) -> dict:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM hallucination_log WHERE date >= ?",
                (cutoff,),
            ).fetchone()[0]
            unverified = conn.execute(
                "SELECT SUM(unverified_count) FROM hallucination_log WHERE date >= ?",
                (cutoff,),
            ).fetchone()[0] or 0
        return {"total_responses": total, "total_unverified": unverified}

    # -- seed_positions ---------------------------------------------------------

    def upsert_seed_position(
        self,
        ticker: str,
        name: str,
        sector: str,
        tier: str,
        avg_price: float = 0.0,
        quantity: int = 0,
        status: str = "active",
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seed_positions
                    (ticker, name, sector, tier, avg_price, quantity, status,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, avg_price=excluded.avg_price,
                    quantity=excluded.quantity, status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (ticker, name, sector, tier, avg_price, quantity, status, now, now),
            )

    def get_seed_positions(self, status: str = "active") -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM seed_positions WHERE status=? ORDER BY sector, name",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_seed_position(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM seed_positions WHERE ticker=?", (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def close_seed_position(self, ticker: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE seed_positions SET status='closed', updated_at=? WHERE ticker=?",
                (now, ticker),
            )

    # -- trade_registers -------------------------------------------------------

    def add_trade_register(
        self, ticker: str, name: str, quantity: int = 0,
        price: float = 0.0, total_amount: float = 0.0,
        source: str = "text", horizon: str = "swing",
        trailing_stop_pct: float = 0.05, target_profit_pct: float = 0.10,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO trade_registers (ticker, name, quantity, price, "
                "total_amount, source, horizon, trailing_stop_pct, "
                "target_profit_pct, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, name, quantity, price, total_amount, source,
                 horizon, trailing_stop_pct, target_profit_pct, "active", now, now),
            )
        return cur.lastrowid or 0

    def get_trade_registers(self, status: str = "active", limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_registers WHERE status=? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def close_trade_register(self, trade_id: int) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE trade_registers SET status='closed', updated_at=? WHERE id=?",
                (now, trade_id),
            )

    # -- trade_lessons ----------------------------------------------------------

    def add_trade_lesson(
        self, ticker: str, name: str, action: str,
        pnl_pct: float = 0, hold_days: int = 0, lesson: str = "",
    ) -> int:
        """매매 교훈 기록."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO trade_lessons "
                "(ticker, name, action, pnl_pct, hold_days, lesson, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, name, action, pnl_pct, hold_days, lesson, now),
            )
            return cursor.lastrowid

    def get_trade_lessons(self, limit: int = 20) -> list[dict]:
        """최근 매매 교훈 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lessons ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- trade_journal (v4.3) --------------------------------------------------

    def add_journal_report(
        self,
        period: str,
        date_range: str,
        total_trades: int = 0,
        win_rate: float = 0,
        avg_pnl: float = 0,
        best_trade_json: str = "",
        worst_trade_json: str = "",
        patterns_json: str = "",
        ai_review: str = "",
        tips_json: str = "",
        mistakes_json: str = "",
    ) -> int:
        """매매일지 복기 리포트 저장."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO trade_journal "
                "(period, date_range, total_trades, win_rate, avg_pnl, "
                " best_trade_json, worst_trade_json, patterns_json, "
                " ai_review, tips_json, mistakes_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (period, date_range, total_trades, win_rate, avg_pnl,
                 best_trade_json, worst_trade_json, patterns_json,
                 ai_review, tips_json, mistakes_json, now),
            )
            return cur.lastrowid

    def get_journal_reports(self, period: str = "weekly", limit: int = 10) -> list[dict]:
        """최근 매매일지 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_journal WHERE period=? "
                "ORDER BY created_at DESC LIMIT ?",
                (period, limit),
            ).fetchall()
        return [dict(r) for r in rows]
