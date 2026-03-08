"""MarketMixin: 시장리포트, 펀더멘탈, 매크로, 공매도, 뉴스 관련 메서드."""

from __future__ import annotations

import logging
import sqlite3
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
                    INSERT INTO supply_demand
                        (ticker, date, foreign_net, institution_net, retail_net,
                         program_net, short_balance, short_ratio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        foreign_net = excluded.foreign_net,
                        institution_net = excluded.institution_net,
                        retail_net = excluded.retail_net,
                        program_net = excluded.program_net,
                        short_balance = excluded.short_balance,
                        short_ratio = excluded.short_ratio,
                        created_at = excluded.created_at
                    """,
                    (ticker, date, foreign_net, institution_net, retail_net,
                     program_net, short_balance, short_ratio, now),
                )
                return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception:
            logger.warning("add_supply_demand failed: %s", ticker, exc_info=True)
            return None

    def bulk_save_supply_demand(self, ticker: str, rows: list[dict]) -> int:
        """투자자별 매매동향 일괄 저장.

        Args:
            ticker: 종목코드
            rows: [{date, foreign_net, institution_net, ...}, ...]

        Returns:
            저장된 행 수
        """
        now = datetime.utcnow().isoformat()
        saved = 0
        try:
            with self._connect() as conn:
                for r in rows:
                    conn.execute(
                        """
                        INSERT INTO supply_demand
                            (ticker, date, foreign_net, institution_net, retail_net,
                             program_net, short_balance, short_ratio, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker, date) DO UPDATE SET
                            foreign_net = excluded.foreign_net,
                            institution_net = excluded.institution_net,
                            retail_net = excluded.retail_net,
                            created_at = excluded.created_at
                        """,
                        (
                            ticker,
                            r.get("date", ""),
                            r.get("foreign_net", 0),
                            r.get("institution_net", 0),
                            r.get("retail_net", 0),
                            r.get("program_net", 0),
                            r.get("short_balance", 0),
                            r.get("short_ratio", 0),
                            now,
                        ),
                    )
                    saved += 1
        except Exception:
            logger.warning("bulk_save_supply_demand failed: %s", ticker, exc_info=True)
        return saved

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
                title = item.get("title", "")
                # URL 기반 중복 체크 (URL 없으면 제목으로 체크)
                if url:
                    existing = conn.execute(
                        "SELECT id FROM global_news WHERE url=?", (url,)
                    ).fetchone()
                    if existing:
                        continue
                elif title:
                    existing = conn.execute(
                        "SELECT id FROM global_news WHERE title=? AND created_at>=?",
                        (title, (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")),
                    ).fetchone()
                    if existing:
                        continue
                conn.execute(
                    "INSERT INTO global_news "
                    "(title, source, url, category, lang, impact_score, "
                    "is_urgent, published, created_at, content_summary, video_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        item.get("content_summary", ""),
                        item.get("video_id", ""),
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

    # -- sent_urgent_alerts (v9.5.3) 긴급 알림 중복 방지 -----------------------

    def is_alert_sent(self, alert_hash: str, hours: int = 24) -> bool:
        """이 해시의 긴급 알림이 최근 N시간 내 전송됐는지 확인."""
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM sent_urgent_alerts "
                "WHERE alert_hash=? AND created_at>=?",
                (alert_hash, cutoff),
            ).fetchone()
        return row is not None

    def save_sent_alert(self, alert_hash: str, title_summary: str = "") -> None:
        """전송한 긴급 알림 해시를 DB에 기록."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sent_urgent_alerts "
                "(alert_hash, title_summary, created_at) VALUES (?, ?, ?)",
                (alert_hash, title_summary[:200], now),
            )

    def cleanup_old_alerts(self, days: int = 3) -> int:
        """오래된 긴급 알림 기록 정리."""
        cutoff = (
            datetime.now() - timedelta(days=days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM sent_urgent_alerts WHERE created_at < ?", (cutoff,)
            )
        return cursor.rowcount

    # -- youtube_intelligence (v9.5) -------------------------------------------

    def save_youtube_intelligence(self, data: dict) -> bool:
        """YouTube 인텔리전스 저장 (video_id 기반 upsert)."""
        import json as _json
        video_id = data.get("video_id", "")
        if not video_id:
            return False
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO youtube_intelligence
                    (video_id, source, title, mentioned_tickers, mentioned_sectors,
                     market_outlook, key_numbers, investment_implications,
                     full_summary, raw_summary, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        video_id,
                        data.get("source", ""),
                        data.get("title", ""),
                        _json.dumps(data.get("mentioned_tickers", []), ensure_ascii=False),
                        _json.dumps(data.get("mentioned_sectors", []), ensure_ascii=False),
                        data.get("market_outlook", ""),
                        _json.dumps(data.get("key_numbers", []), ensure_ascii=False),
                        data.get("investment_implications", ""),
                        data.get("full_summary", ""),
                        data.get("raw_summary", ""),
                        data.get("confidence", 0.0),
                        now,
                    ),
                )
            return True
        except Exception as e:
            logger.error("save_youtube_intelligence error: %s", e)
            return False

    def get_recent_youtube_intelligence(
        self, hours: int = 24, limit: int = 10,
    ) -> list[dict]:
        """최근 YouTube 인텔리전스 조회 (JSON 파싱 포함)."""
        import json as _json
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT * FROM youtube_intelligence
                    WHERE created_at >= ?
                    ORDER BY created_at DESC LIMIT ?""",
                    (cutoff, limit),
                ).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                for key in ("mentioned_tickers", "mentioned_sectors", "key_numbers"):
                    try:
                        d[key] = _json.loads(d.get(key) or "[]")
                    except Exception:
                        d[key] = []
                results.append(d)
            return results
        except Exception as e:
            logger.error("get_recent_youtube_intelligence error: %s", e)
            return []

    def get_youtube_mentioned_tickers(self, hours: int = 48) -> list[dict]:
        """YouTube에서 언급된 종목 집계 (센티먼트 포함)."""
        import json as _json
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT mentioned_tickers, source FROM youtube_intelligence
                    WHERE created_at >= ?""",
                    (cutoff,),
                ).fetchall()
            # 종목별 센티먼트 집계
            ticker_map: dict = {}
            for row in rows:
                tickers = _json.loads(row["mentioned_tickers"] or "[]")
                # v9.5.1: 타입 검증
                if not isinstance(tickers, list):
                    continue
                source = row["source"]
                for t in tickers:
                    if not isinstance(t, dict):
                        continue
                    name = t.get("name", "")
                    if not name:
                        continue
                    if name not in ticker_map:
                        ticker_map[name] = {
                            "name": name,
                            "ticker": t.get("ticker", ""),
                            "mentions": 0,
                            "긍정": 0,
                            "부정": 0,
                            "중립": 0,
                            "sources": [],
                        }
                    ticker_map[name]["mentions"] += 1
                    sent = t.get("sentiment", "중립")
                    # v9.5.1: 영문→한글 호환 (기존 DB 데이터 대응)
                    _sent_norm = {"positive": "긍정", "negative": "부정", "neutral": "중립"}
                    sent = _sent_norm.get(sent, sent)
                    ticker_map[name][sent] = ticker_map[name].get(sent, 0) + 1
                    if source not in ticker_map[name]["sources"]:
                        ticker_map[name]["sources"].append(source)
            result = sorted(ticker_map.values(), key=lambda x: x["mentions"], reverse=True)
            return result
        except Exception as e:
            logger.error("get_youtube_mentioned_tickers error: %s", e)
            return []

    def save_manager_stance(
        self, manager_key: str, stance: str, holdings_summary: str = "",
    ) -> None:
        """매니저 분석 stance 저장 (통합 컨텍스트용)."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO manager_stances
                    (manager_key, stance, holdings_summary, created_at)
                    VALUES (?, ?, ?, ?)""",
                    (manager_key, stance, holdings_summary, now),
                )
        except Exception as e:
            logger.error("save_manager_stance error: %s", e)

    def get_recent_manager_stances(self, hours: int = 24) -> dict[str, str]:
        """최근 매니저 stance 조회. {manager_key: stance}."""
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT manager_key, stance FROM manager_stances
                    WHERE created_at >= ?
                    ORDER BY created_at DESC""",
                    (cutoff,),
                ).fetchall()
            stances = {}
            for row in rows:
                key = row["manager_key"]
                if key not in stances:  # 가장 최근 것만
                    stances[key] = row["stance"]
            return stances
        except Exception as e:
            logger.error("get_recent_manager_stances error: %s", e)
            return {}

    # -- briefings (v9.5.1) ---------------------------------------------------

    def save_briefing(self, briefing_type: str, content: str) -> bool:
        """브리핑 텍스트 저장 (AI 채팅에서 참조할 수 있도록).

        Args:
            briefing_type: "morning" | "premarket" | "manager" 등
            content: 브리핑 전문 텍스트
        """
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO briefings (briefing_type, content, created_at)
                    VALUES (?, ?, ?)""",
                    (briefing_type, content[:4000], now),
                )
            return True
        except Exception as e:
            logger.error("save_briefing error: %s", e)
            return False

    def get_recent_briefings(
        self, hours: int = 24, limit: int = 3,
    ) -> list[dict]:
        """최근 브리핑 조회.

        Returns:
            [{"briefing_type": str, "content": str, "created_at": str}]
        """
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT briefing_type, content, created_at
                    FROM briefings
                    WHERE created_at >= ?
                    ORDER BY created_at DESC LIMIT ?""",
                    (cutoff, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_recent_briefings error: %s", e)
            return []

    # -- sector_deep_dive (v9.5.4) --------------------------------------------

    def save_sector_deep_dive(
        self, sector_key: str, report_json: str, data_sources: str = "{}",
    ) -> bool:
        """섹터 딥다이브 리포트 저장."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO sector_deep_dive
                    (sector_key, report_json, data_sources, created_at)
                    VALUES (?, ?, ?, ?)""",
                    (sector_key, report_json, data_sources, now),
                )
            return True
        except Exception as e:
            logger.error("save_sector_deep_dive error: %s", e)
            return False

    def get_sector_deep_dive(
        self, sector_key: str, hours: int = 24,
    ) -> dict | None:
        """최근 섹터 딥다이브 리포트 조회."""
        import json as _json
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """SELECT report_json, data_sources, created_at
                    FROM sector_deep_dive
                    WHERE sector_key = ? AND created_at >= ?
                    ORDER BY created_at DESC LIMIT 1""",
                    (sector_key, cutoff),
                ).fetchone()
            if not row:
                return None
            report = _json.loads(row["report_json"])
            report["_cached_at"] = row["created_at"]
            return report
        except Exception as e:
            logger.error("get_sector_deep_dive error: %s", e)
            return None

    def get_all_recent_deep_dives(self, hours: int = 48) -> list[dict]:
        """최근 모든 섹터 딥다이브 리포트 조회 (컨텍스트 주입용)."""
        import json as _json
        cutoff = (
            datetime.now() - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT sector_key, report_json, created_at
                    FROM sector_deep_dive
                    WHERE created_at >= ?
                    ORDER BY created_at DESC""",
                    (cutoff,),
                ).fetchall()
            results = []
            seen = set()
            for row in rows:
                key = row["sector_key"]
                if key in seen:
                    continue
                seen.add(key)
                try:
                    report = _json.loads(row["report_json"])
                    report["_cached_at"] = row["created_at"]
                    results.append(report)
                except Exception:
                    continue
            return results
        except Exception as e:
            logger.error("get_all_recent_deep_dives error: %s", e)
            return []

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

    # -- program_trading (v9.0) -----------------------------------------------

    def save_program_trading(self, data: dict) -> None:
        """프로그램 매매 데이터 저장 (UPSERT by date+market)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO program_trading
                    (date, market, arb_buy, arb_sell, arb_net,
                     non_arb_buy, non_arb_sell, non_arb_net,
                     total_buy, total_sell, total_net)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, market) DO UPDATE SET
                    arb_buy=excluded.arb_buy, arb_sell=excluded.arb_sell,
                    arb_net=excluded.arb_net,
                    non_arb_buy=excluded.non_arb_buy,
                    non_arb_sell=excluded.non_arb_sell,
                    non_arb_net=excluded.non_arb_net,
                    total_buy=excluded.total_buy,
                    total_sell=excluded.total_sell,
                    total_net=excluded.total_net
                """,
                (
                    data["date"], data.get("market", "KOSPI"),
                    data.get("arb_buy", 0), data.get("arb_sell", 0),
                    data.get("arb_net", 0),
                    data.get("non_arb_buy", 0), data.get("non_arb_sell", 0),
                    data.get("non_arb_net", 0),
                    data.get("total_buy", 0), data.get("total_sell", 0),
                    data.get("total_net", 0),
                ),
            )

    def get_program_trading(self, days: int = 5, market: str = "KOSPI") -> list[dict]:
        """최근 N일 프로그램 매매 데이터 조회."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, market, arb_buy, arb_sell, arb_net,
                       non_arb_buy, non_arb_sell, non_arb_net,
                       total_buy, total_sell, total_net
                FROM program_trading
                WHERE market = ?
                ORDER BY date DESC LIMIT ?
                """,
                (market, days),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- credit_balance (v9.0) ------------------------------------------------

    def save_credit_balance(self, data: dict) -> None:
        """신용잔고/고객예탁금 데이터 저장 (UPSERT by date)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO credit_balance
                    (date, deposit, deposit_change, credit, credit_change)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    deposit=excluded.deposit,
                    deposit_change=excluded.deposit_change,
                    credit=excluded.credit,
                    credit_change=excluded.credit_change
                """,
                (
                    data["date"],
                    data.get("deposit", 0),
                    data.get("deposit_change", 0),
                    data.get("credit", 0),
                    data.get("credit_change", 0),
                ),
            )

    def get_credit_balance(self, days: int = 5) -> list[dict]:
        """최근 N일 신용잔고/예탁금 데이터 조회."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, deposit, deposit_change, credit, credit_change
                FROM credit_balance
                ORDER BY date DESC LIMIT ?
                """,
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- etf_flow (v9.0) ------------------------------------------------------

    def save_etf_flow(self, data: dict) -> None:
        """ETF 흐름 데이터 저장 (UPSERT by date+code)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO etf_flow
                    (date, code, name, etf_type, price, change_pct, nav, market_cap, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, code) DO UPDATE SET
                    name=excluded.name, etf_type=excluded.etf_type,
                    price=excluded.price, change_pct=excluded.change_pct,
                    nav=excluded.nav, market_cap=excluded.market_cap,
                    volume=excluded.volume
                """,
                (
                    data["date"], data["code"], data.get("name", ""),
                    data.get("etf_type", ""), data.get("price", 0),
                    data.get("change_pct", 0), data.get("nav", 0),
                    data.get("market_cap", 0), data.get("volume", 0),
                ),
            )

    def get_etf_flow(self, days: int = 5) -> list[dict]:
        """최근 N일 ETF 흐름 데이터 조회."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, code, name, etf_type, price, change_pct,
                       nav, market_cap, volume
                FROM etf_flow
                ORDER BY date DESC, market_cap DESC
                LIMIT ?
                """,
                (days * 10,),  # 추적 ETF 수 * 일수
            ).fetchall()
        return [dict(r) for r in rows]

    def get_etf_flow_by_date(self, date: str) -> list[dict]:
        """특정 날짜의 ETF 흐름 데이터."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, code, name, etf_type, price, change_pct,
                       nav, market_cap, volume
                FROM etf_flow WHERE date = ?
                ORDER BY market_cap DESC
                """,
                (date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_etf_flow_previous(self) -> list[dict]:
        """전일 ETF 흐름 데이터 (변화율 계산용)."""
        with self._connect() as conn:
            # 가장 최근 날짜 찾기
            row = conn.execute(
                "SELECT DISTINCT date FROM etf_flow ORDER BY date DESC LIMIT 1 OFFSET 1"
            ).fetchone()
            if not row:
                return []
            prev_date = row["date"]
            rows = conn.execute(
                """
                SELECT date, code, name, etf_type, price, change_pct,
                       nav, market_cap, volume
                FROM etf_flow WHERE date = ?
                """,
                (prev_date,),
            ).fetchall()
        return [dict(r) for r in rows]
