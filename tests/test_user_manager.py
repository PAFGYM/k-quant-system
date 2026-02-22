"""Tests for core/user_manager.py, DB new tables, and bot new commands."""

import json
from pathlib import Path

import pytest
from kstock.core.user_manager import (
    UserConfig,
    get_default_user,
    get_user,
    create_user,
    update_user_settings,
    is_authorized,
    format_user_profile,
    _user_store,
)


# ---------------------------------------------------------------------------
# Fixture: clean in-memory user store between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_user_store():
    """각 테스트 전후로 인메모리 사용자 저장소 초기화."""
    _user_store.clear()
    yield
    _user_store.clear()


# ---------------------------------------------------------------------------
# TestUserConfig
# ---------------------------------------------------------------------------

class TestUserConfig:
    """UserConfig 기본값 검증."""

    def test_default_user_id(self):
        cfg = UserConfig()
        assert cfg.user_id == 0

    def test_default_name(self):
        cfg = UserConfig()
        assert cfg.name == "주호님"

    def test_default_is_admin(self):
        cfg = UserConfig()
        assert cfg.is_admin is True


# ---------------------------------------------------------------------------
# TestGetDefaultUser
# ---------------------------------------------------------------------------

class TestGetDefaultUser:
    """get_default_user: 기본 사용자 반환."""

    def test_returns_user_config(self):
        user = get_default_user()
        assert isinstance(user, UserConfig)

    def test_default_name_juho(self):
        user = get_default_user()
        assert user.name == "주호님"

    def test_default_is_admin(self):
        user = get_default_user()
        assert user.is_admin is True


# ---------------------------------------------------------------------------
# TestCreateUser
# ---------------------------------------------------------------------------

class TestCreateUser:
    """create_user: 새 사용자 생성."""

    def test_creates_with_telegram_id(self):
        user = create_user(12345, "테스트님")
        assert isinstance(user, UserConfig)
        assert user.user_id == 12345
        assert user.name == "테스트님"

    def test_non_admin_by_default(self):
        user = create_user(12345, "테스트님")
        assert user.is_admin is False

    def test_duplicate_returns_existing(self):
        user1 = create_user(12345, "테스트님")
        user2 = create_user(12345, "다른이름")
        assert user2.name == "테스트님"  # 기존 이름 유지


# ---------------------------------------------------------------------------
# TestIsAuthorized
# ---------------------------------------------------------------------------

class TestIsAuthorized:
    """is_authorized: 등록된 사용자 확인."""

    def test_default_user_authorized(self):
        """기본 사용자(주호님)는 항상 True."""
        assert is_authorized(0) is True

    def test_unknown_user_not_authorized(self):
        """등록되지 않은 ID -> False."""
        assert is_authorized(99999) is False

    def test_created_user_authorized(self):
        """생성된 사용자 -> True."""
        create_user(12345, "테스트님")
        assert is_authorized(12345) is True


# ---------------------------------------------------------------------------
# TestFormatUserProfile
# ---------------------------------------------------------------------------

class TestFormatUserProfile:
    """format_user_profile: 사용자 프로필 포맷."""

    def test_no_bold_markers(self):
        user = get_default_user()
        profile = format_user_profile(user)
        assert "**" not in profile

    def test_contains_user_name(self):
        user = get_default_user()
        profile = format_user_profile(user)
        assert "주호님" in profile

    def test_contains_admin_label(self):
        user = get_default_user()
        profile = format_user_profile(user)
        assert "관리자" in profile


# ---------------------------------------------------------------------------
# TestDBNewTables
# ---------------------------------------------------------------------------

class TestDBNewTables:
    """SQLiteStore 신규 테이블 CRUD 검증 (tmp_path 활용)."""

    @pytest.fixture
    def db(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        return SQLiteStore(db_path=tmp_path / "test.db")

    # -- risk_violations -------------------------------------------------------

    def test_add_and_get_risk_violations(self, db):
        rid = db.add_risk_violation(
            "2026-02-23", "concentration", severity="high",
            description="단일종목 25% 초과",
            recommended_action="비중 축소",
        )
        assert rid > 0
        rows = db.get_risk_violations(days=7)
        assert len(rows) >= 1
        assert rows[0]["violation_type"] == "concentration"

    # -- portfolio_snapshots ---------------------------------------------------

    def test_add_and_get_portfolio_snapshots(self, db):
        sid = db.add_portfolio_snapshot(
            "2026-02-23", total_value=100_000_000, cash=20_000_000,
            holdings_count=5, daily_pnl_pct=1.5, total_pnl_pct=10.0,
            mdd=-5.0, peak_value=105_000_000,
        )
        assert sid > 0
        rows = db.get_portfolio_snapshots(limit=10)
        assert len(rows) >= 1
        assert rows[0]["total_value"] == 100_000_000

    def test_get_portfolio_peak(self, db):
        db.add_portfolio_snapshot("2026-02-21", total_value=90_000_000)
        db.add_portfolio_snapshot("2026-02-22", total_value=110_000_000)
        db.add_portfolio_snapshot("2026-02-23", total_value=100_000_000)
        peak = db.get_portfolio_peak()
        assert peak == 110_000_000

    def test_get_portfolio_peak_empty(self, db):
        """스냅샷 없으면 0.0."""
        peak = db.get_portfolio_peak()
        assert peak == 0.0

    # -- recommendation_tracking -----------------------------------------------

    def test_add_and_get_recommendation_tracks(self, db):
        tid = db.add_recommendation_track(
            "005930", "삼성전자", "A", 82.5, "2026-02-23", 76_500,
        )
        assert tid > 0
        rows = db.get_recommendation_tracks(limit=10)
        assert len(rows) >= 1
        assert rows[0]["ticker"] == "005930"

    def test_update_recommendation_track(self, db):
        tid = db.add_recommendation_track(
            "005930", "삼성전자", "A", 82.5, "2026-02-23", 76_500,
        )
        db.update_recommendation_track(tid, price_d1=77_000, return_d1=0.65, hit=1)
        rows = db.get_recommendation_tracks(limit=10)
        updated = [r for r in rows if r["id"] == tid][0]
        assert updated["price_d1"] == 77_000
        assert updated["hit"] == 1

    # -- ml_performance --------------------------------------------------------

    def test_add_and_get_ml_performance(self, db):
        mid = db.add_ml_performance(
            "2026-02-23", model_version="v3.0",
            train_score=0.85, val_score=0.78,
            overfit_gap=0.07, features_used=42,
        )
        assert mid > 0
        rows = db.get_ml_performance(limit=5)
        assert len(rows) >= 1
        assert rows[0]["model_version"] == "v3.0"

    # -- hallucination_log -----------------------------------------------------

    def test_add_and_get_hallucination_stats(self, db):
        hid = db.add_hallucination_log(
            "2026-02-23", query="삼성전자 전망",
            response_preview="좋습니다...",
            verified_count=3, unverified_count=1,
            unverified_claims="목표가 100,000원",
        )
        assert hid > 0
        stats = db.get_hallucination_stats(days=7)
        assert stats["total_responses"] >= 1
        assert stats["total_unverified"] >= 1

    # -- trade_executions ------------------------------------------------------

    def test_add_and_get_trade_executions(self, db):
        eid = db.add_trade_execution(
            "005930", "삼성전자", direction="buy",
            quantity=130, price=76_500, amount=9_945_000,
            commission=1_492, strategy="A", score=82.5,
        )
        assert eid > 0
        rows = db.get_trade_executions(limit=10)
        assert len(rows) >= 1
        assert rows[0]["ticker"] == "005930"
        assert rows[0]["quantity"] == 130

    # -- users -----------------------------------------------------------------

    def test_add_and_get_user(self, db):
        uid = db.add_user(12345, "주호님", is_admin=True, config_json='{"lang":"ko"}')
        assert uid > 0
        user = db.get_user(12345)
        assert user is not None
        assert user["name"] == "주호님"
        assert user["is_admin"] == 1

    def test_get_user_not_found(self, db):
        user = db.get_user(99999)
        assert user is None

    def test_update_user(self, db):
        db.add_user(12345, "주호님", is_admin=True)
        new_config = json.dumps({"lang": "en", "theme": "dark"})
        db.update_user(12345, config_json=new_config)
        user = db.get_user(12345)
        assert user is not None
        config = json.loads(user["config_json"])
        assert config["theme"] == "dark"


# ---------------------------------------------------------------------------
# TestBotNewCommands
# ---------------------------------------------------------------------------

class TestBotNewCommands:
    """KQuantBot에 신규 명령어 메서드 존재 여부."""

    def test_cmd_risk_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_risk")
        assert callable(getattr(KQuantBot, "cmd_risk"))

    def test_cmd_health_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_health")
        assert callable(getattr(KQuantBot, "cmd_health"))

    def test_cmd_performance_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_performance")
        assert callable(getattr(KQuantBot, "cmd_performance"))

    def test_cmd_scenario_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_scenario")
        assert callable(getattr(KQuantBot, "cmd_scenario"))

    def test_cmd_ml_exists(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_ml")
        assert callable(getattr(KQuantBot, "cmd_ml"))
