"""PortfolioMixin: 포트폴리오, 보유종목, 워치리스트, 포트폴리오 스냅샷, 리스크 관련 메서드."""

from __future__ import annotations

from datetime import datetime, timedelta

# v9.3: 매니저별 보유종목 임계값 (holding_type 기반)
HOLDING_THRESHOLDS: dict[str, dict[str, float]] = {
    "scalp":     {"stop": -0.03, "t1": 0.05, "t2": 0.08},
    "swing":     {"stop": -0.07, "t1": 0.10, "t2": 0.20},
    "position":  {"stop": -0.12, "t1": 0.20, "t2": 0.40},
    "long_term": {"stop": -0.20, "t1": 0.30, "t2": 0.80},
    "tenbagger": {"stop": -0.25, "t1": 1.00, "t2": 5.00},   # v12.0: 손절-25%, 1차 2배, 2차 6배
    "auto":      {"stop": -0.05, "t1": 0.03, "t2": 0.07},
}

_MARGIN_KEYWORDS = ("유융", "유옹", "신용", "담보")


def _calc_thresholds(
    buy_price: float, holding_type: str = "auto",
) -> tuple[float, float, float]:
    """매수가와 투자전략에 따른 (target_1, target_2, stop_price) 계산."""
    th = HOLDING_THRESHOLDS.get(holding_type, HOLDING_THRESHOLDS["auto"])
    return (
        round(buy_price * (1 + th["t1"]), 0),
        round(buy_price * (1 + th["t2"]), 0),
        round(buy_price * (1 + th["stop"]), 0),
    )


def _normalize_purchase_type(value: str | None) -> str:
    return str(value or "").strip()


def _normalize_margin_fields(
    purchase_type: str | None,
    is_margin: int | bool | None = None,
    margin_type: str | None = None,
) -> tuple[str, int, str]:
    purchase_type = _normalize_purchase_type(purchase_type)
    margin_type = _normalize_purchase_type(margin_type)
    inferred_margin = any(k in purchase_type for k in _MARGIN_KEYWORDS) or any(
        k in margin_type for k in _MARGIN_KEYWORDS
    )
    is_margin_flag = 1 if inferred_margin or bool(is_margin) else 0
    if is_margin_flag and not margin_type and inferred_margin:
        margin_type = purchase_type
    if not is_margin_flag:
        margin_type = ""
    return purchase_type, is_margin_flag, margin_type


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
        *,
        purchase_type: str = "",
        is_margin: int | bool | None = None,
        margin_type: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        target_1, target_2, stop_price = _calc_thresholds(buy_price, holding_type)
        purchase_type, is_margin_value, margin_type = _normalize_margin_fields(
            purchase_type, is_margin, margin_type,
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO holdings
                    (ticker, name, buy_price, current_price, buy_date,
                     target_1, target_2, stop_price, status, sold_pct, pnl_pct,
                     holding_type, purchase_type, is_margin, margin_type,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, buy_price, buy_price, now[:10],
                 target_1, target_2, stop_price, holding_type,
                 purchase_type, is_margin_value, margin_type, now, now),
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

    def get_holding_by_ticker(
        self, ticker: str, purchase_type: str | None = None,
    ) -> dict | None:
        with self._connect() as conn:
            if purchase_type is None:
                row = conn.execute(
                    "SELECT * FROM holdings WHERE ticker=? AND status='active' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM holdings WHERE ticker=? AND status='active' "
                    "AND COALESCE(purchase_type, '')=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (ticker, _normalize_purchase_type(purchase_type)),
                ).fetchone()
        return dict(row) if row else None

    def get_holding_by_name(
        self, name: str, purchase_type: str | None = None,
    ) -> dict | None:
        """종목명으로 active 보유종목 조회 (ticker 없을 때 fallback)."""
        if not name:
            return None
        with self._connect() as conn:
            if purchase_type is None:
                row = conn.execute(
                    "SELECT * FROM holdings WHERE name=? AND status='active' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (name,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM holdings WHERE name=? AND status='active' "
                    "AND COALESCE(purchase_type, '')=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (name, _normalize_purchase_type(purchase_type)),
                ).fetchone()
        return dict(row) if row else None

    def _get_holding_candidates(self, ticker: str, name: str, status: str) -> list[dict]:
        clauses: list[str] = []
        params: list[str] = []
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker)
        if name:
            clauses.append("name=?")
            params.append(name)
        if not clauses:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM holdings WHERE status=? AND ({' OR '.join(clauses)}) "
                "ORDER BY updated_at DESC, created_at DESC",
                [status, *params],
            ).fetchall()
        seen: set[int] = set()
        results: list[dict] = []
        for row in rows:
            data = dict(row)
            if data["id"] in seen:
                continue
            seen.add(data["id"])
            results.append(data)
        return results

    def _find_matching_holding(
        self, ticker: str, name: str, purchase_type: str = "",
    ) -> dict | None:
        purchase_type = _normalize_purchase_type(purchase_type)
        candidates = self._get_holding_candidates(ticker, name, status="active")
        if not candidates:
            return None
        if purchase_type:
            for row in candidates:
                if _normalize_purchase_type(row.get("purchase_type")) == purchase_type:
                    return row
            for row in candidates:
                if not _normalize_purchase_type(row.get("purchase_type")):
                    return row
            return None
        blank_matches = [
            row for row in candidates if not _normalize_purchase_type(row.get("purchase_type"))
        ]
        if len(blank_matches) == 1:
            return blank_matches[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

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
        purchase_type: str = "",
        is_margin: int | bool | None = None,
        margin_type: str = "",
    ) -> int:
        """스크린샷에서 파싱한 보유종목을 holdings DB에 upsert.

        이미 active인 동일 종목이 있으면 현재가/수익률만 업데이트,
        없으면 신규 등록.
        ticker가 비어있으면 name으로 조회.
        """
        purchase_type, is_margin_value, margin_type = _normalize_margin_fields(
            purchase_type, is_margin, margin_type,
        )
        existing = self._find_matching_holding(ticker, name, purchase_type)
        has_position_data = any(
            float(v or 0) > 0 for v in (buy_price, current_price, eval_amount)
        ) or int(quantity or 0) > 0
        if not existing and not has_position_data:
            sold_candidates = self._get_holding_candidates(ticker, name, status="sold")
            for row in sold_candidates:
                if purchase_type and _normalize_purchase_type(row.get("purchase_type")) != purchase_type:
                    continue
                return row["id"]
        now = datetime.utcnow().isoformat()
        if existing:
            updates: dict[str, object] = {
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "quantity": quantity,
                "eval_amount": eval_amount,
                "name": name,
            }
            if buy_price and not float(existing.get("buy_price", 0) or 0):
                target_1, target_2, stop_price = _calc_thresholds(buy_price, holding_type)
                updates.update({
                    "buy_price": buy_price,
                    "target_1": target_1,
                    "target_2": target_2,
                    "stop_price": stop_price,
                })
            if purchase_type:
                updates["purchase_type"] = purchase_type
            if purchase_type or margin_type or is_margin is not None:
                updates["is_margin"] = is_margin_value
                updates["margin_type"] = margin_type
            self.update_holding(existing["id"], **updates)
            return existing["id"]
        else:
            if buy_price:
                target_1, target_2, stop_price = _calc_thresholds(buy_price, holding_type)
            else:
                target_1 = target_2 = stop_price = 0
            with self._connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO holdings
                        (ticker, name, buy_price, current_price, quantity,
                         eval_amount, buy_date, target_1, target_2, stop_price,
                         status, sold_pct, pnl_pct, holding_type,
                         purchase_type, is_margin, margin_type,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, name, buy_price, current_price, quantity,
                     eval_amount, now[:10], target_1, target_2, stop_price,
                     pnl_pct, holding_type, purchase_type, is_margin_value,
                     margin_type, now, now),
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
        """보유종목의 투자전략(holding_type)을 변경하고 임계값을 재계산합니다."""
        holding = self.get_holding(holding_id)
        if holding and holding.get("buy_price"):
            t1, t2, stop = _calc_thresholds(holding["buy_price"], holding_type)
            self.update_holding(
                holding_id, holding_type=holding_type,
                target_1=t1, target_2=t2, stop_price=stop,
            )
        else:
            self.update_holding(holding_id, holding_type=holding_type)

    def migrate_holding_thresholds(self) -> int:
        """v9.3: 기존 active holdings의 임계값을 holding_type에 맞게 재계산.

        auto가 아닌 holding_type을 가진 종목 중 stop_price가
        구버전(-5%) 기준인 건을 매니저별 임계값으로 업데이트.
        Returns: 업데이트된 건수.
        """
        updated = 0
        holdings = self.get_active_holdings()
        for h in holdings:
            ht = h.get("holding_type") or "auto"
            if ht == "auto":
                continue
            buy_price = h.get("buy_price", 0)
            if buy_price <= 0:
                continue
            t1, t2, stop = _calc_thresholds(buy_price, ht)
            # 현재 DB 값과 다르면 업데이트
            if (h.get("stop_price") != stop
                    or h.get("target_1") != t1
                    or h.get("target_2") != t2):
                self.update_holding(
                    h["id"], target_1=t1, target_2=t2, stop_price=stop,
                )
                updated += 1
        return updated

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

    def bulk_add_watchlist(self, items: list[dict]) -> int:
        """유니버스 종목을 일괄 등록. 이미 있는 종목은 건너뜀."""
        now = datetime.utcnow().isoformat()
        count = 0
        with self._connect() as conn:
            for item in items:
                existing = conn.execute(
                    "SELECT ticker FROM watchlist WHERE ticker=?",
                    (item["ticker"],),
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO watchlist
                           (ticker, name, target_price, target_rsi, active,
                            created_at, rec_price, horizon, manager, sector)
                           VALUES (?, ?, NULL, 30, 1, ?, 0, '', '', ?)""",
                        (item["ticker"], item["name"], now,
                         item.get("sector", "")),
                    )
                    count += 1
                else:
                    # sector만 업데이트 (기존 horizon/manager 보존)
                    sector = item.get("sector", "")
                    if sector:
                        conn.execute(
                            "UPDATE watchlist SET sector=? WHERE ticker=? AND (sector IS NULL OR sector='')",
                            (sector, item["ticker"]),
                        )
        return count

    def get_watchlist_by_category(
        self, category: str, limit: int = 8, offset: int = 0,
    ) -> tuple[list[dict], int]:
        """카테고리별 워치리스트 조회 (페이지네이션). Returns (items, total_count)."""
        with self._connect() as conn:
            if category == "holding":
                rows = conn.execute(
                    """SELECT w.ticker, w.name, w.horizon, w.manager, w.sector,
                              w.rec_price,
                              h.buy_price, h.current_price, h.pnl_pct,
                              h.quantity, h.eval_amount, h.holding_type
                       FROM watchlist w
                       INNER JOIN holdings h ON w.ticker = h.ticker
                         AND h.status='active'
                       WHERE w.active=1
                       ORDER BY w.name
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
                total = conn.execute(
                    """SELECT COUNT(*) FROM watchlist w
                       INNER JOIN holdings h ON w.ticker = h.ticker
                         AND h.status='active'
                       WHERE w.active=1""",
                ).fetchone()[0]
            elif category == "unclassified":
                rows = conn.execute(
                    """SELECT w.ticker, w.name, w.horizon, w.manager, w.sector,
                              w.rec_price,
                              NULL as buy_price, NULL as current_price,
                              NULL as pnl_pct, NULL as quantity,
                              NULL as eval_amount, NULL as holding_type
                       FROM watchlist w
                       WHERE w.active=1
                         AND (w.horizon IS NULL OR w.horizon='')
                       ORDER BY w.name
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
                total = conn.execute(
                    """SELECT COUNT(*) FROM watchlist
                       WHERE active=1
                         AND (horizon IS NULL OR horizon='')""",
                ).fetchone()[0]
            else:
                # scalp, swing, position, long_term
                rows = conn.execute(
                    """SELECT w.ticker, w.name, w.horizon, w.manager, w.sector,
                              w.rec_price,
                              h.buy_price, h.current_price, h.pnl_pct,
                              h.quantity, h.eval_amount, h.holding_type
                       FROM watchlist w
                       LEFT JOIN holdings h ON w.ticker = h.ticker
                         AND h.status='active'
                       WHERE w.active=1 AND w.horizon=?
                       ORDER BY w.name
                       LIMIT ? OFFSET ?""",
                    (category, limit, offset),
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM watchlist WHERE active=1 AND horizon=?",
                    (category,),
                ).fetchone()[0]
            return [dict(r) for r in rows], total

    def get_watchlist_category_counts(self) -> dict:
        """카테고리별 종목 수 반환."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM watchlist WHERE active=1",
            ).fetchone()[0]
            counts = {"total": total}
            for cat in ("scalp", "swing", "position", "long_term", "tenbagger"):
                counts[cat] = conn.execute(
                    "SELECT COUNT(*) FROM watchlist WHERE active=1 AND horizon=?",
                    (cat,),
                ).fetchone()[0]
            counts["unclassified"] = conn.execute(
                """SELECT COUNT(*) FROM watchlist
                   WHERE active=1 AND (horizon IS NULL OR horizon='')""",
            ).fetchone()[0]
            counts["holding"] = conn.execute(
                """SELECT COUNT(*) FROM watchlist w
                   INNER JOIN holdings h ON w.ticker = h.ticker
                     AND h.status='active'
                   WHERE w.active=1""",
            ).fetchone()[0]
            return counts

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
                "SELECT * FROM portfolio_snapshots "
                "ORDER BY date DESC, created_at DESC LIMIT ?",
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

    # ── v12.0: tenbagger_universe 메서드 ────────────────────────

    def upsert_tenbagger_universe(
        self,
        ticker: str,
        name: str,
        market: str = "KRX",
        sector: str = "",
        **scores,
    ) -> int:
        """텐배거 유니버스 종목 upsert (INSERT or UPDATE)."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tenbagger_universe
                    (ticker, name, market, sector, tenbagger_score,
                     tam_score, policy_score, moat_score, revenue_score,
                     discovery_score, momentum_score, consensus_score,
                     ai_consensus, status, entry_price, current_price,
                     current_return, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 0, ?, ?, ?)
                ON CONFLICT(ticker, market) DO UPDATE SET
                    name=excluded.name,
                    sector=excluded.sector,
                    tenbagger_score=excluded.tenbagger_score,
                    tam_score=excluded.tam_score,
                    policy_score=excluded.policy_score,
                    moat_score=excluded.moat_score,
                    revenue_score=excluded.revenue_score,
                    discovery_score=excluded.discovery_score,
                    momentum_score=excluded.momentum_score,
                    consensus_score=excluded.consensus_score,
                    ai_consensus=excluded.ai_consensus,
                    current_price=excluded.current_price,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    ticker, name, market, sector,
                    scores.get("tenbagger_score", 0),
                    scores.get("tam_score", 0),
                    scores.get("policy_score", 0),
                    scores.get("moat_score", 0),
                    scores.get("revenue_score", 0),
                    scores.get("discovery_score", 0),
                    scores.get("momentum_score", 0),
                    scores.get("consensus_score", 0),
                    scores.get("ai_consensus", "{}"),
                    scores.get("entry_price", 0),
                    scores.get("current_price", 0),
                    scores.get("notes", ""),
                    now, now,
                ),
            )
            return cursor.lastrowid or 0

    def get_tenbagger_universe(
        self, sector: str = "", status: str = "active",
    ) -> list[dict]:
        """텐배거 유니버스 조회 (섹터 필터 옵션)."""
        with self._connect() as conn:
            if sector:
                rows = conn.execute(
                    "SELECT * FROM tenbagger_universe "
                    "WHERE status=? AND sector=? ORDER BY tenbagger_score DESC",
                    (status, sector),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tenbagger_universe "
                    "WHERE status=? ORDER BY tenbagger_score DESC",
                    (status,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_tenbagger_by_ticker(self, ticker: str) -> dict | None:
        """텐배거 유니버스에서 티커로 단건 조회."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenbagger_universe WHERE ticker=? AND status='active'",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def update_tenbagger_universe_price(
        self, ticker: str, current_price: float, entry_price: float = 0,
    ) -> None:
        """텐배거 유니버스 현재가 & 수익률 갱신."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            if entry_price > 0:
                ret = (current_price - entry_price) / entry_price * 100
                conn.execute(
                    "UPDATE tenbagger_universe SET current_price=?, current_return=?, "
                    "entry_price=?, updated_at=? WHERE ticker=? AND status='active'",
                    (current_price, round(ret, 2), entry_price, now, ticker),
                )
            else:
                # entry_price 는 유지, current_return 만 갱신
                conn.execute(
                    """UPDATE tenbagger_universe SET current_price=?,
                       current_return=CASE WHEN entry_price > 0
                           THEN round((? - entry_price) / entry_price * 100, 2)
                           ELSE 0 END,
                       updated_at=? WHERE ticker=? AND status='active'""",
                    (current_price, current_price, now, ticker),
                )

    # ── v12.0: tenbagger_catalyst 메서드 ────────────────────────

    def add_tenbagger_catalyst(
        self,
        ticker: str,
        catalyst_type: str,
        description: str,
        expected_date: str = "",
        impact_score: float = 50,
    ) -> int:
        """텐배거 카탈리스트 추가."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO tenbagger_catalyst
                    (ticker, catalyst_type, description, expected_date,
                     status, impact_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (ticker, catalyst_type, description, expected_date,
                 impact_score, now, now),
            )
            return cursor.lastrowid or 0

    def get_tenbagger_catalysts(
        self, ticker: str = "", status: str = "",
    ) -> list[dict]:
        """텐배거 카탈리스트 조회 (티커/상태 필터)."""
        with self._connect() as conn:
            query = "SELECT * FROM tenbagger_catalyst WHERE 1=1"
            params: list = []
            if ticker:
                query += " AND ticker=?"
                params.append(ticker)
            if status:
                query += " AND status=?"
                params.append(status)
            query += " ORDER BY expected_date ASC"
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def trigger_tenbagger_catalyst(self, catalyst_id: int) -> None:
        """카탈리스트를 triggered 상태로 변경."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE tenbagger_catalyst SET status='triggered', "
                "triggered_at=?, updated_at=? WHERE id=?",
                (now, now, catalyst_id),
            )

    # ── v12.0: tenbagger_score_history 메서드 ───────────────────

    def save_tenbagger_score(
        self, ticker: str, score_date: str, **scores,
    ) -> None:
        """텐배거 점수 히스토리 저장 (주간 리스코어링용)."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tenbagger_score_history
                    (ticker, score_date, tenbagger_score,
                     tam_score, policy_score, moat_score, revenue_score,
                     discovery_score, momentum_score, consensus_score,
                     price_at_score, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker, score_date,
                    scores.get("tenbagger_score", 0),
                    scores.get("tam_score", 0),
                    scores.get("policy_score", 0),
                    scores.get("moat_score", 0),
                    scores.get("revenue_score", 0),
                    scores.get("discovery_score", 0),
                    scores.get("momentum_score", 0),
                    scores.get("consensus_score", 0),
                    scores.get("price_at_score", 0),
                    now,
                ),
            )

    def get_tenbagger_score_trend(
        self, ticker: str, weeks: int = 12,
    ) -> list[dict]:
        """텐배거 점수 추이 조회 (최근 N주)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenbagger_score_history WHERE ticker=? "
                "ORDER BY score_date DESC LIMIT ?",
                (ticker, weeks),
            ).fetchall()
        return [dict(r) for r in rows]
