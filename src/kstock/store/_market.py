"""MarketMixin: 시장리포트, 펀더멘탈, 매크로, 공매도, 뉴스 관련 메서드."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class MarketMixin:
    """시장리포트 + 펀더멘탈 + 매크로 + 공매도 + 뉴스 Mixin."""

    # -- reports (v3.5) --------------------------------------------------------

    def add_report(
        self,
        source: str,
        title: str,
        broker: str,
        date: str,
        ticker: str = "",
        target_price: float = 0,
        prev_target_price: float = 0,
        opinion: str = "",
        prev_opinion: str = "",
        pdf_url: str = "",
        summary: str = "",
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO reports
                        (source, title, broker, ticker, target_price, prev_target_price,
                         opinion, prev_opinion, date, pdf_url, summary, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source, title, broker, ticker, target_price, prev_target_price,
                     opinion, prev_opinion, date, pdf_url, summary, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_report failed: %s %s", ticker, title[:30], exc_info=True)
            return None

    def get_recent_reports(self, limit: int = 10, ticker: str = "") -> list[dict]:
        with self._connect() as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM reports WHERE ticker=? ORDER BY date DESC LIMIT ?",
                    (ticker, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reports ORDER BY date DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- reports helpers --------------------------------------------------------

    def get_reports_for_tickers(self, tickers: list[str], limit: int = 5) -> list[dict]:
        if not tickers:
            return []
        placeholders = ",".join("?" for _ in tickers)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM reports WHERE ticker IN ({placeholders}) "
                "ORDER BY date DESC LIMIT ?",
                (*tickers, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_target_upgrades(self, days: int = 7, limit: int = 10) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE date >= ? AND target_price > 0 AND prev_target_price > 0
                         AND target_price > prev_target_price
                   ORDER BY (target_price - prev_target_price) * 1.0 / prev_target_price DESC
                   LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_target_downgrades(self, days: int = 7, limit: int = 10) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reports
                   WHERE date >= ? AND target_price > 0 AND prev_target_price > 0
                         AND target_price < prev_target_price
                   ORDER BY (prev_target_price - target_price) * 1.0 / prev_target_price DESC
                   LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_by_sector(self, keywords: list[str], limit: int = 5) -> list[dict]:
        if not keywords:
            return []
        conditions = " OR ".join("title LIKE ?" for _ in keywords)
        params = [f"%{kw}%" for kw in keywords]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM reports WHERE ({conditions}) ORDER BY date DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reports_today(self, limit: int = 10) -> list[dict]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reports WHERE date=? ORDER BY created_at DESC LIMIT ?",
                (today, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- consensus (v3.5) ------------------------------------------------------

    def upsert_consensus(
        self, ticker: str, name: str = "", avg_target_price: float = 0,
        current_price: float = 0, upside_pct: float = 0,
        buy_count: int = 0, hold_count: int = 0, sell_count: int = 0,
        target_trend: str = "", target_trend_pct: float = 0, score_bonus: int = 0,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO consensus
                    (ticker, name, avg_target_price, current_price, upside_pct,
                     buy_count, hold_count, sell_count, target_trend,
                     target_trend_pct, score_bonus, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, avg_target_price=excluded.avg_target_price,
                    current_price=excluded.current_price, upside_pct=excluded.upside_pct,
                    buy_count=excluded.buy_count, hold_count=excluded.hold_count,
                    sell_count=excluded.sell_count, target_trend=excluded.target_trend,
                    target_trend_pct=excluded.target_trend_pct,
                    score_bonus=excluded.score_bonus, updated_at=excluded.updated_at
                """,
                (ticker, name, avg_target_price, current_price, upside_pct,
                 buy_count, hold_count, sell_count, target_trend,
                 target_trend_pct, score_bonus, now),
            )

    def get_consensus(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM consensus WHERE ticker=?", (ticker,)
            ).fetchone()
        return dict(row) if row else None

    # -- earnings (v3.5) -------------------------------------------------------

    def add_earnings(
        self, ticker: str, name: str, period: str,
        earnings_date: str = "", revenue: float = 0, revenue_consensus: float = 0,
        operating_income: float = 0, op_income_consensus: float = 0,
        op_margin: float = 0, prev_op_margin: float = 0,
        surprise_pct: float = 0, verdict: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO earnings
                    (ticker, name, period, earnings_date, revenue, revenue_consensus,
                     operating_income, op_income_consensus, op_margin, prev_op_margin,
                     surprise_pct, verdict, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, name, period, earnings_date, revenue, revenue_consensus,
                 operating_income, op_income_consensus, op_margin, prev_op_margin,
                 surprise_pct, verdict, now),
            )
            return cursor.lastrowid

    def get_earnings(self, ticker: str, limit: int = 4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM earnings WHERE ticker=? ORDER BY created_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- financials (v3.5) -----------------------------------------------------

    def upsert_financials(self, ticker: str, name: str = "", period: str = "", **kwargs) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM financials WHERE ticker=? AND period=?", (ticker, period)
            ).fetchone()
            if existing:
                sets = ["name=?", "created_at=?"]
                vals: list = [name, now]
                for k, v in kwargs.items():
                    sets.append(f"{k}=?")
                    vals.append(v)
                vals.append(existing["id"])
                conn.execute(f"UPDATE financials SET {', '.join(sets)} WHERE id=?", vals)
            else:
                cols = ["ticker", "name", "period", "created_at"]
                vals_list: list = [ticker, name, period, now]
                for k, v in kwargs.items():
                    cols.append(k)
                    vals_list.append(v)
                placeholders = ",".join("?" * len(cols))
                conn.execute(
                    f"INSERT INTO financials ({','.join(cols)}) VALUES ({placeholders})",
                    vals_list,
                )

    def get_financials(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM financials WHERE ticker=? ORDER BY created_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    # -- supply_demand (v3.5) --------------------------------------------------

    def add_supply_demand(
        self, ticker: str, date: str,
        foreign_net: float = 0, institution_net: float = 0, retail_net: float = 0,
        program_net: float = 0, short_balance: float = 0, short_ratio: float = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO supply_demand
                        (ticker, date, foreign_net, institution_net, retail_net,
                         program_net, short_balance, short_ratio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, foreign_net, institution_net, retail_net,
                     program_net, short_balance, short_ratio, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_supply_demand failed: %s", ticker, exc_info=True)
            return None

    def get_supply_demand(self, ticker: str, days: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM supply_demand WHERE ticker=? AND date >= ? ORDER BY date DESC",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- dart_events (v3.10) ---------------------------------------------------

    def add_dart_event(
        self, ticker: str, date: str, title: str,
        event_type: str = "", url: str = "",
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO dart_events
                       (ticker, date, title, event_type, url, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ticker, date, title, event_type, url, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_dart_event failed: %s", ticker, exc_info=True)
            return None

    def get_dart_events(self, ticker: str, date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dart_events WHERE ticker=? AND date=? ORDER BY created_at DESC",
                (ticker, date),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- macro_events (v3.5) ---------------------------------------------------

    def add_macro_event(
        self, date: str, name: str, country: str = "",
        importance: str = "보통", description: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO macro_events
                    (date, name, country, importance, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date, name, country, importance, description, now),
            )
            return cursor.lastrowid

    def get_macro_events(self, start_date: str, end_date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM macro_events WHERE date >= ? AND date <= ? ORDER BY date",
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- short_selling (v3.5) --------------------------------------------------

    def add_short_selling(
        self, ticker: str, date: str,
        short_volume: int = 0, total_volume: int = 0, short_ratio: float = 0,
        short_balance: int = 0, short_balance_ratio: float = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO short_selling
                        (ticker, date, short_volume, total_volume, short_ratio,
                         short_balance, short_balance_ratio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, short_volume, total_volume, short_ratio,
                     short_balance, short_balance_ratio, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_short_selling failed: %s", ticker, exc_info=True)
            return None

    def get_short_selling(self, ticker: str, days: int = 60) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM short_selling WHERE ticker=? AND date >= ? ORDER BY date",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_short_selling_latest(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM short_selling WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    def get_overheated_shorts(self, min_ratio: float = 20.0, days: int = 7) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM short_selling
                WHERE (short_ratio >= ? OR short_balance_ratio >= ?)
                  AND date >= ?
                ORDER BY short_ratio DESC
                """,
                (min_ratio, min_ratio / 2, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- inverse_etf (v3.5) ----------------------------------------------------

    def add_inverse_etf(
        self, ticker: str, date: str, name: str = "",
        sector: str = "", volume: int = 0, price: float = 0,
        change_pct: float = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO inverse_etf
                        (ticker, date, name, sector, volume, price, change_pct, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, name, sector, volume, price, change_pct, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_inverse_etf failed: %s", ticker, exc_info=True)
            return None

    def get_inverse_etf(self, ticker: str, days: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inverse_etf WHERE ticker=? AND date >= ? ORDER BY date",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_inverse_etf_by_sector(self, sector: str, days: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inverse_etf WHERE sector=? AND date >= ? ORDER BY date",
                (sector, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- margin_balance (v3.5) -------------------------------------------------

    def add_margin_balance(
        self, ticker: str, date: str,
        credit_buy: int = 0, credit_sell: int = 0,
        credit_balance: int = 0, credit_ratio: float = 0,
        collateral_balance: int = 0,
    ) -> int | None:
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO margin_balance
                        (ticker, date, credit_buy, credit_sell, credit_balance,
                         credit_ratio, collateral_balance, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, date, credit_buy, credit_sell, credit_balance,
                     credit_ratio, collateral_balance, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_margin_balance failed: %s", ticker, exc_info=True)
            return None

    def get_margin_balance(self, ticker: str, days: int = 60) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM margin_balance WHERE ticker=? AND date >= ? ORDER BY date",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_margin_balance_latest(self, ticker: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM margin_balance WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return dict(row) if row else None

    # -- margin_thresholds (v3.5) ----------------------------------------------

    def upsert_margin_threshold(
        self, ticker: str, metric: str,
        mean_60d: float = 0, std_60d: float = 0,
        upper_1sigma: float = 0, lower_1sigma: float = 0,
        upper_2sigma: float = 0, lower_2sigma: float = 0,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO margin_thresholds
                    (ticker, metric, mean_60d, std_60d,
                     upper_1sigma, lower_1sigma, upper_2sigma, lower_2sigma,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, metric) DO UPDATE SET
                    mean_60d=excluded.mean_60d,
                    std_60d=excluded.std_60d,
                    upper_1sigma=excluded.upper_1sigma,
                    lower_1sigma=excluded.lower_1sigma,
                    upper_2sigma=excluded.upper_2sigma,
                    lower_2sigma=excluded.lower_2sigma,
                    updated_at=excluded.updated_at
                """,
                (ticker, metric, mean_60d, std_60d,
                 upper_1sigma, lower_1sigma, upper_2sigma, lower_2sigma, now),
            )

    def get_margin_thresholds(self, ticker: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM margin_thresholds WHERE ticker=?", (ticker,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- sector_snapshots (v4.3) -----------------------------------------------

    def add_sector_snapshot(
        self,
        snapshot_date: str,
        sectors_json: str,
        signals_json: str = "",
        portfolio_json: str = "",
        recommendations_json: str = "",
    ) -> int:
        """섹터 로테이션 스냅샷 저장."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO sector_snapshots "
                "(snapshot_date, sectors_json, signals_json, portfolio_json, "
                " recommendations_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_date, sectors_json, signals_json, portfolio_json,
                 recommendations_json, now),
            )
            return cur.lastrowid

    def get_sector_snapshots(self, limit: int = 10) -> list[dict]:
        """최근 섹터 스냅샷 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sector_snapshots ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- contrarian_signals (v4.3) ---------------------------------------------

    def add_contrarian_signal(
        self,
        signal_type: str,
        ticker: str,
        name: str,
        direction: str,
        strength: float = 0,
        score_adj: int = 0,
        reasons_json: str = "",
        data_json: str = "",
    ) -> int:
        """역발상 시그널 기록."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO contrarian_signals "
                "(signal_type, ticker, name, direction, strength, "
                " score_adj, reasons_json, data_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (signal_type, ticker, name, direction, strength,
                 score_adj, reasons_json, data_json, now),
            )
            return cur.lastrowid

    def get_contrarian_signals(self, limit: int = 20) -> list[dict]:
        """최근 역발상 시그널 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contrarian_signals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_contrarian_signals_by_ticker(self, ticker: str, limit: int = 10) -> list[dict]:
        """종목별 역발상 시그널 반환."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contrarian_signals WHERE ticker=? "
                "ORDER BY created_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- global_news (v6.0) ----------------------------------------------------

    def save_global_news(self, items: list[dict]) -> int:
        """글로벌 뉴스 저장. 중복 URL 스킵. 저장 건수 반환."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        saved = 0
        with self._connect() as conn:
            for item in items:
                url = item.get("url", "")
                # URL 기반 중복 체크
                if url:
                    existing = conn.execute(
                        "SELECT id FROM global_news WHERE url=?", (url,)
                    ).fetchone()
                    if existing:
                        continue
                conn.execute(
                    "INSERT INTO global_news "
                    "(title, source, url, category, lang, impact_score, "
                    "is_urgent, published, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.get("title", ""),
                        item.get("source", ""),
                        url,
                        item.get("category", "market"),
                        item.get("lang", "ko"),
                        item.get("impact_score", 0),
                        1 if item.get("is_urgent") else 0,
                        item.get("published", ""),
                        now,
                    ),
                )
                saved += 1
        return saved

    def get_recent_global_news(
        self, limit: int = 10, hours: int = 24, urgent_only: bool = False,
    ) -> list[dict]:
        """최근 글로벌 뉴스 조회."""
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            if urgent_only:
                rows = conn.execute(
                    "SELECT * FROM global_news "
                    "WHERE created_at >= ? AND is_urgent = 1 "
                    "ORDER BY impact_score DESC, created_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM global_news "
                    "WHERE created_at >= ? "
                    "ORDER BY impact_score DESC, created_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_old_news(self, days: int = 7) -> int:
        """오래된 뉴스 정리."""
        cutoff = (
            datetime.now() - timedelta(days=days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM global_news WHERE created_at < ?", (cutoff,)
            )
        return cursor.rowcount

    # -- surge_stocks ----------------------------------------------------------

    def add_surge_stock(
        self, ticker: str, name: str = "", scan_time: str = "",
        change_pct: float = 0.0, volume_ratio: float = 0.0,
        triggers: str = "", market_cap: float = 0.0,
        health_grade: str = "", health_score: int = 0,
        health_reasons: str = "", ai_analysis: str = "",
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO surge_stocks (ticker, name, scan_time, change_pct, "
                "volume_ratio, triggers, market_cap, health_grade, health_score, "
                "health_reasons, ai_analysis, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, name, scan_time, change_pct, volume_ratio, triggers,
                 market_cap, health_grade, health_score, health_reasons,
                 ai_analysis, now),
            )
        return cur.lastrowid or 0

    def get_surge_stocks(self, days: int = 1, limit: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM surge_stocks WHERE created_at>=? "
                "ORDER BY change_pct DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- stealth_accumulations -------------------------------------------------

    def add_stealth_accumulation(
        self, ticker: str, name: str = "", total_score: int = 0,
        patterns_json: str = "", price_change_20d: float = 0.0,
        inst_total: float = 0.0, foreign_total: float = 0.0,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO stealth_accumulations (ticker, name, total_score, "
                "patterns_json, price_change_20d, inst_total, foreign_total, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ticker, name, total_score, patterns_json,
                 price_change_20d, inst_total, foreign_total, now),
            )
        return cur.lastrowid or 0

    def get_stealth_accumulations(self, days: int = 1, limit: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stealth_accumulations WHERE created_at>=? "
                "ORDER BY total_score DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- macro_cache (Phase 8 speed optimization) --------------------------------

    def save_macro_cache(self, snapshot_json: str) -> None:
        """매크로 스냅샷을 SQLite에 캐시 (항상 1행, UPSERT)."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO macro_cache (id, snapshot_json, fetched_at, created_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    fetched_at=excluded.fetched_at
                """,
                (snapshot_json, now, now),
            )

    def get_macro_cache(self) -> dict | None:
        """캐시된 매크로 스냅샷 반환. 없으면 None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_json, ai_summary, ai_summary_at, fetched_at "
                "FROM macro_cache WHERE id=1"
            ).fetchone()
        if row:
            return dict(row)
        return None

    def save_ai_summary_cache(self, summary: str) -> None:
        """AI 요약을 캐시에 저장."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE macro_cache SET ai_summary=?, ai_summary_at=?
                WHERE id=1
                """,
                (summary, now),
            )

    def get_ai_summary_cache(self, max_age_seconds: int = 300) -> str | None:
        """캐시된 AI 요약 반환. max_age_seconds 이내만 유효."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ai_summary, ai_summary_at FROM macro_cache WHERE id=1"
            ).fetchone()
        if not row or not row["ai_summary"] or not row["ai_summary_at"]:
            return None
        try:
            cached_at = datetime.fromisoformat(row["ai_summary_at"])
            if (datetime.utcnow() - cached_at).total_seconds() > max_age_seconds:
                return None
        except (ValueError, TypeError):
            return None
        return row["ai_summary"]
