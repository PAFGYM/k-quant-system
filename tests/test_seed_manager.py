"""Tests for signal/seed_manager.py - Seed position management."""

import pytest
from kstock.signal.seed_manager import (
    SEED_CONFIG,
    TIER_WEIGHT_LIMITS,
    SeedPosition,
    SeedAction,
    check_future_limits,
    compute_seed_amount,
    evaluate_seed_position,
    format_seed_alert,
    format_seed_overview,
)


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

class TestSeedConfig:
    """Test seed configuration values."""

    def test_max_future_ratio(self):
        assert SEED_CONFIG["max_future_ratio"] == 0.15

    def test_max_per_sector(self):
        assert SEED_CONFIG["max_per_sector"] == 0.05

    def test_max_per_stock(self):
        assert SEED_CONFIG["max_per_stock"] == 0.02

    def test_min_seed_amount(self):
        assert SEED_CONFIG["min_seed_amount"] == 500_000

    def test_max_seed_amount(self):
        assert SEED_CONFIG["max_seed_amount"] == 3_000_000

    def test_scale_up_trigger(self):
        assert SEED_CONFIG["scale_up_trigger"] == 0.15

    def test_cut_loss_trigger(self):
        assert SEED_CONFIG["cut_loss_trigger"] == -0.10


# ---------------------------------------------------------------------------
# Limit checking
# ---------------------------------------------------------------------------

class TestCheckFutureLimits:
    """Test position limit checking."""

    def test_no_positions_allowed(self):
        result = check_future_limits(
            total_portfolio_value=100_000_000,
            future_positions=[],
        )
        assert result["allowed"] is True
        assert result["total_future_pct"] == 0
        assert result["violations"] == []

    def test_within_limits_allowed(self):
        positions = [
            SeedPosition(ticker="054450", sector="autonomous_driving",
                         eval_amount=1_000_000),
        ]
        result = check_future_limits(100_000_000, positions)
        assert result["allowed"] is True
        assert abs(result["total_future_pct"] - 0.01) < 0.001

    def test_total_limit_violation(self):
        # 16% > 15% limit
        positions = [
            SeedPosition(ticker="054450", sector="autonomous_driving",
                         eval_amount=10_000_000),
            SeedPosition(ticker="047810", sector="space_aerospace",
                         eval_amount=6_000_000),
        ]
        result = check_future_limits(100_000_000, positions)
        assert result["allowed"] is False
        assert any("총 비중" in v for v in result["violations"])

    def test_sector_limit_violation(self):
        # Single sector > 5%
        positions = [
            SeedPosition(ticker="054450", sector="autonomous_driving",
                         eval_amount=3_000_000),
            SeedPosition(ticker="396270", sector="autonomous_driving",
                         eval_amount=3_000_000),
        ]
        result = check_future_limits(100_000_000, positions)
        assert result["allowed"] is False
        assert any("자율주행" in v for v in result["violations"])

    def test_stock_limit_violation(self):
        # Single stock > 2%
        positions = [
            SeedPosition(ticker="054450", sector="autonomous_driving",
                         eval_amount=2_500_000),
        ]
        result = check_future_limits(100_000_000, positions)
        assert result["allowed"] is False
        assert any("비중" in v for v in result["violations"])

    def test_new_position_checked(self):
        result = check_future_limits(
            total_portfolio_value=100_000_000,
            future_positions=[],
            new_ticker="054450",
            new_amount=1_000_000,
        )
        assert result["allowed"] is True
        assert abs(result["total_future_pct"] - 0.01) < 0.001

    def test_new_position_too_small(self):
        result = check_future_limits(
            total_portfolio_value=100_000_000,
            future_positions=[],
            new_ticker="054450",
            new_amount=100_000,  # < 500k min
        )
        assert result["allowed"] is False
        assert any("최소" in v for v in result["violations"])

    def test_new_position_too_large(self):
        result = check_future_limits(
            total_portfolio_value=100_000_000,
            future_positions=[],
            new_ticker="054450",
            new_amount=5_000_000,  # > 3M max
        )
        assert result["allowed"] is False
        assert any("최대" in v for v in result["violations"])

    def test_zero_portfolio_value(self):
        result = check_future_limits(0, [])
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Seed amount computation
# ---------------------------------------------------------------------------

class TestComputeSeedAmount:
    """Test seed amount computation."""

    def test_tier1_amounts(self):
        result = compute_seed_amount("005380", 200_000_000)  # 현대차, tier1
        assert result["tier"] == "tier1_platform"
        assert result["min_amount"] >= SEED_CONFIG["min_seed_amount"]
        assert result["max_amount"] <= SEED_CONFIG["max_seed_amount"]

    def test_tier3_amounts(self):
        result = compute_seed_amount("344860", 200_000_000)  # 슈어소프트테크, tier3
        assert result["tier"] == "tier3_emerging"

    def test_unknown_ticker_defaults_to_tier3(self):
        result = compute_seed_amount("999999", 100_000_000)
        assert result["tier"] == "tier3_emerging"


# ---------------------------------------------------------------------------
# Position evaluation
# ---------------------------------------------------------------------------

class TestEvaluateSeedPosition:
    """Test seed position evaluation."""

    def test_scale_up_at_15_pct(self):
        pos = SeedPosition(
            ticker="054450", name="텔레칩스",
            sector="autonomous_driving", tier="tier2_core",
            avg_price=50000, current_price=58000,
            eval_amount=1_160_000, unrealized_pnl=160_000,
            unrealized_pnl_pct=0.16,
        )
        action = evaluate_seed_position(pos, future_score=70, trigger_active=True)
        assert action.action == "scale_up"
        assert "주호님" in action.message
        assert "씨앗" in action.message

    def test_cut_loss_with_dead_trigger(self):
        pos = SeedPosition(
            ticker="277410", name="이와이엘",
            sector="quantum_computing", tier="tier3_emerging",
            avg_price=10000, current_price=8800,
            eval_amount=880_000, unrealized_pnl=-120_000,
            unrealized_pnl_pct=-0.12,
        )
        action = evaluate_seed_position(pos, trigger_active=False)
        assert action.action == "cut_loss"
        assert action.urgency == "critical"
        assert "주호님" in action.message

    def test_hold_with_active_trigger(self):
        pos = SeedPosition(
            ticker="277410", name="이와이엘",
            avg_price=10000, current_price=8800,
            unrealized_pnl_pct=-0.12,
        )
        action = evaluate_seed_position(pos, trigger_active=True)
        assert action.action == "hold"
        assert action.urgency == "high"

    def test_normal_hold(self):
        pos = SeedPosition(
            ticker="054450", name="텔레칩스",
            avg_price=50000, current_price=52000,
            unrealized_pnl_pct=0.04,
        )
        action = evaluate_seed_position(pos)
        assert action.action == "hold"
        assert action.urgency == "normal"

    def test_boundary_scale_up(self):
        pos = SeedPosition(
            ticker="054450", name="텔레칩스",
            avg_price=50000, current_price=57500,
            unrealized_pnl_pct=0.15,  # Exactly at trigger
        )
        action = evaluate_seed_position(pos)
        assert action.action == "scale_up"

    def test_boundary_cut_loss(self):
        pos = SeedPosition(
            ticker="054450", name="텔레칩스",
            avg_price=50000, current_price=45000,
            unrealized_pnl_pct=-0.10,  # Exactly at trigger
        )
        action = evaluate_seed_position(pos, trigger_active=False)
        assert action.action == "cut_loss"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatSeedAlert:
    """Test seed alert formatting."""

    def test_format_scale_up(self):
        action = SeedAction(
            ticker="054450", name="텔레칩스",
            action="scale_up", urgency="normal",
            message="주호님, 텔레칩스 씨앗이 자라고 있습니다!",
            details=["수익: +16%", "스코어: 72/100"],
        )
        text = format_seed_alert(action)
        assert "씨앗 성장" in text
        assert "텔레칩스" in text
        assert "**" not in text

    def test_format_cut_loss(self):
        action = SeedAction(
            ticker="277410", name="이와이엘",
            action="cut_loss", urgency="critical",
            message="주호님, 이와이엘 손절 권장",
            details=["손실: -12%"],
        )
        text = format_seed_alert(action)
        assert "손절" in text

    def test_format_hold(self):
        action = SeedAction(
            ticker="054450", name="텔레칩스",
            action="hold", urgency="normal",
            message="텔레칩스 유지",
            details=["수익: +4%"],
        )
        text = format_seed_alert(action)
        assert "유지" in text


class TestFormatSeedOverview:
    """Test seed overview formatting."""

    def test_empty_positions(self):
        text = format_seed_overview([], 100_000_000)
        assert "씨앗 포지션이 없습니다" in text

    def test_with_positions(self):
        positions = [
            SeedPosition(
                ticker="054450", name="텔레칩스",
                sector="autonomous_driving", tier="tier2_core",
                eval_amount=1_000_000, unrealized_pnl=50_000,
                unrealized_pnl_pct=0.05,
            ),
        ]
        text = format_seed_overview(positions, 100_000_000)
        assert "텔레칩스" in text
        assert "자율주행" in text
        assert "**" not in text

    def test_shows_limit(self):
        text = format_seed_overview([], 100_000_000)
        assert "/future" in text


# ---------------------------------------------------------------------------
# DB integration tests
# ---------------------------------------------------------------------------

class TestDBFutureWatchlist:
    """Test SQLite future watchlist CRUD."""

    def test_upsert_and_get(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.upsert_future_watchlist(
            ticker="054450", name="텔레칩스",
            sector="autonomous_driving", tier="tier2_core",
            future_score=72, tech_maturity=20,
            entry_signal="WATCH",
        )
        result = db.get_future_watchlist_entry("054450")
        assert result is not None
        assert result["name"] == "텔레칩스"
        assert result["future_score"] == 72
        assert result["entry_signal"] == "WATCH"

    def test_upsert_overwrites(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.upsert_future_watchlist(
            ticker="054450", name="텔레칩스",
            sector="autonomous_driving", tier="tier2_core",
            future_score=60,
        )
        db.upsert_future_watchlist(
            ticker="054450", name="텔레칩스",
            sector="autonomous_driving", tier="tier2_core",
            future_score=75,
        )
        result = db.get_future_watchlist_entry("054450")
        assert result["future_score"] == 75

    def test_get_by_sector(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.upsert_future_watchlist("054450", "텔레칩스", "autonomous_driving", "tier2_core", 72)
        db.upsert_future_watchlist("047810", "한국항공우주", "space_aerospace", "tier1_platform", 78)
        result = db.get_future_watchlist(sector="autonomous_driving")
        assert len(result) == 1
        assert result[0]["ticker"] == "054450"


class TestDBFutureTriggers:
    """Test SQLite future triggers CRUD."""

    def test_add_and_get(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        tid = db.add_future_trigger(
            sector="autonomous_driving",
            trigger_type="policy",
            impact="HIGH",
            title="자율주행 L3 허용",
        )
        assert tid > 0
        result = db.get_future_triggers(sector="autonomous_driving")
        assert len(result) == 1
        assert result[0]["title"] == "자율주행 L3 허용"

    def test_get_all_triggers(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.add_future_trigger("autonomous_driving", "policy", "HIGH", "AD news")
        db.add_future_trigger("space_aerospace", "corporate", "MEDIUM", "Space news")
        result = db.get_future_triggers()
        assert len(result) == 2


class TestDBSeedPositions:
    """Test SQLite seed positions CRUD."""

    def test_upsert_and_get(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.upsert_seed_position(
            ticker="054450", name="텔레칩스",
            sector="autonomous_driving", tier="tier2_core",
            avg_price=50000, quantity=20,
        )
        result = db.get_seed_position("054450")
        assert result is not None
        assert result["avg_price"] == 50000
        assert result["quantity"] == 20

    def test_get_active_positions(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.upsert_seed_position("054450", "텔레칩스", "autonomous_driving", "tier2_core", 50000, 20)
        db.upsert_seed_position("047810", "한국항공우주", "space_aerospace", "tier1_platform", 30000, 10)
        result = db.get_seed_positions(status="active")
        assert len(result) == 2

    def test_close_position(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.upsert_seed_position("054450", "텔레칩스", "autonomous_driving", "tier2_core", 50000, 20)
        db.close_seed_position("054450")
        result = db.get_seed_positions(status="active")
        assert len(result) == 0
        result = db.get_seed_position("054450")
        assert result["status"] == "closed"


# ---------------------------------------------------------------------------
# Weekly report integration
# ---------------------------------------------------------------------------

class TestWeeklyReportFutureTech:
    """Test future tech section in weekly report."""

    def test_report_contains_section_10(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        from kstock.bot.weekly_report import collect_weekly_data, generate_report_content
        db = SQLiteStore(tmp_path / "test.db")
        data = collect_weekly_data(db)
        content = generate_report_content(data)
        assert "10. 미래기술" in content
        assert "자율주행" in content
        assert "우주항공" in content
        assert "양자컴퓨터" in content

    def test_report_no_bold(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        from kstock.bot.weekly_report import collect_weekly_data, generate_report_content
        db = SQLiteStore(tmp_path / "test.db")
        data = collect_weekly_data(db)
        content = generate_report_content(data)
        assert "**" not in content


# ---------------------------------------------------------------------------
# Bot menu integration
# ---------------------------------------------------------------------------

class TestBotFutureMenu:
    """Test bot menu structure with future tech."""

    def test_main_menu_has_future(self):
        from kstock.bot.bot import MAIN_MENU
        flat = str(MAIN_MENU)
        assert "미래기술" in flat

    def test_bot_has_future_handler(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_future")
        assert hasattr(KQuantBot, "_menu_future_tech")
