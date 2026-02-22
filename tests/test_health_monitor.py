"""Tests for the health monitor module (core/health_monitor.py)."""

from __future__ import annotations

import sqlite3

import pytest

from kstock.core.health_monitor import (
    HealthCheck,
    run_health_checks,
    check_disk_usage,
    check_db_accessible,
    check_db_size,
    backup_database,
    vacuum_database,
    generate_launchd_plist,
    format_system_report,
    format_health_alert,
)


# =========================================================================
# TestHealthCheck
# =========================================================================

class TestHealthCheck:
    """HealthCheck 데이터클래스 기본 동작 테스트."""

    def test_defaults(self):
        hc = HealthCheck(name="test")
        assert hc.name == "test"
        assert hc.status == "ok"
        assert hc.message == ""
        assert hc.checked_at == ""

    def test_status_ok(self):
        hc = HealthCheck(name="disk", status="ok", message="정상")
        assert hc.status == "ok"

    def test_status_warning(self):
        hc = HealthCheck(name="mem", status="warning", message="주의")
        assert hc.status == "warning"

    def test_status_error(self):
        hc = HealthCheck(name="db", status="error", message="오류")
        assert hc.status == "error"


# =========================================================================
# TestCheckDiskUsage
# =========================================================================

class TestCheckDiskUsage:
    """check_disk_usage 함수 테스트."""

    def test_returns_healthcheck(self):
        result = check_disk_usage()
        assert isinstance(result, HealthCheck)
        assert result.name == "disk_usage"

    def test_status_is_ok_on_normal_machine(self):
        """테스트 머신에서는 디스크 사용량이 심각하지 않을 것."""
        result = check_disk_usage(threshold_pct=99.0)
        assert result.status == "ok"

    def test_checked_at_set(self):
        result = check_disk_usage()
        assert result.checked_at != ""


# =========================================================================
# TestCheckDbAccessible
# =========================================================================

class TestCheckDbAccessible:
    """check_db_accessible 함수 테스트."""

    def test_valid_db(self, tmp_path):
        """유효한 SQLite DB → ok."""
        db_file = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE test_tbl (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        result = check_db_accessible(db_file)
        assert result.status == "ok"
        assert "정상" in result.message or "무결성" in result.message

    def test_nonexistent_db(self, tmp_path):
        """존재하지 않는 파일 → error."""
        missing = tmp_path / "missing.db"
        result = check_db_accessible(missing)
        assert result.status == "error"

    def test_empty_db_warning(self, tmp_path):
        """테이블 없는 DB → warning."""
        db_file = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_file))
        conn.close()

        result = check_db_accessible(db_file)
        assert result.status == "warning"


# =========================================================================
# TestCheckDbSize
# =========================================================================

class TestCheckDbSize:
    """check_db_size 함수 테스트."""

    def test_returns_positive_float(self, tmp_path):
        """실제 DB 파일 → 크기 > 0."""
        db_file = tmp_path / "size_test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

        size = check_db_size(db_file)
        assert size > 0

    def test_nonexistent_returns_negative(self, tmp_path):
        """존재하지 않는 파일 → -1.0."""
        missing = tmp_path / "not_here.db"
        assert check_db_size(missing) == -1.0


# =========================================================================
# TestRunHealthChecks
# =========================================================================

class TestRunHealthChecks:
    """run_health_checks 함수 테스트."""

    def test_returns_list_of_healthcheck(self):
        checks = run_health_checks()
        assert isinstance(checks, list)
        for chk in checks:
            assert isinstance(chk, HealthCheck)

    def test_all_have_checked_at(self):
        checks = run_health_checks()
        for chk in checks:
            assert chk.checked_at != ""

    def test_with_db_path(self, tmp_path):
        """DB 경로 전달 시 DB 관련 체크 포함."""
        db_file = tmp_path / "hc_test.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE test_tbl (id INTEGER)")
        conn.commit()
        conn.close()

        checks = run_health_checks(db_path=db_file)
        check_names = [c.name for c in checks]
        assert "db_accessible" in check_names


# =========================================================================
# TestBackupDatabase
# =========================================================================

class TestBackupDatabase:
    """backup_database 함수 테스트."""

    def test_creates_backup_file(self, tmp_path):
        """백업 생성 확인."""
        db_file = tmp_path / "source.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_path = backup_database(db_file, backup_dir)

        from pathlib import Path
        assert Path(backup_path).exists()
        assert "source_" in backup_path

    def test_backup_is_valid_db(self, tmp_path):
        """백업 파일이 유효한 SQLite DB 인지 확인."""
        db_file = tmp_path / "src.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE items (val TEXT)")
        conn.execute("INSERT INTO items VALUES ('hello')")
        conn.commit()
        conn.close()

        backup_dir = tmp_path / "bk"
        backup_path = backup_database(db_file, backup_dir)

        bk_conn = sqlite3.connect(backup_path)
        rows = bk_conn.execute("SELECT val FROM items").fetchall()
        bk_conn.close()
        assert rows == [("hello",)]


# =========================================================================
# TestVacuumDatabase
# =========================================================================

class TestVacuumDatabase:
    """vacuum_database 함수 테스트."""

    def test_vacuum_returns_true(self, tmp_path):
        db_file = tmp_path / "vac.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE t (id INTEGER)")
        for i in range(100):
            conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.commit()
        conn.execute("DELETE FROM t WHERE id > 10")
        conn.commit()
        conn.close()

        assert vacuum_database(db_file) is True

    def test_vacuum_nonexistent_returns_false(self, tmp_path):
        missing = tmp_path / "no.db"
        assert vacuum_database(missing) is False


# =========================================================================
# TestGenerateLaunchdPlist
# =========================================================================

class TestGenerateLaunchdPlist:
    """generate_launchd_plist 함수 테스트."""

    def test_contains_com_kquant(self, tmp_path):
        script = tmp_path / "bot.py"
        script.write_text("# bot", encoding="utf-8")
        plist = generate_launchd_plist(script)
        assert "com.kquant" in plist

    def test_contains_program_arguments(self, tmp_path):
        script = tmp_path / "bot.py"
        script.write_text("# bot", encoding="utf-8")
        plist = generate_launchd_plist(script)
        assert "ProgramArguments" in plist

    def test_contains_keep_alive(self, tmp_path):
        script = tmp_path / "bot.py"
        script.write_text("# bot", encoding="utf-8")
        plist = generate_launchd_plist(script)
        assert "KeepAlive" in plist

    def test_saves_file_when_dir_given(self, tmp_path):
        script = tmp_path / "bot.py"
        script.write_text("# bot", encoding="utf-8")
        plist_dir = tmp_path / "plists"
        generate_launchd_plist(script, plist_dir=plist_dir)
        files = list(plist_dir.glob("*.plist"))
        assert len(files) == 1


# =========================================================================
# TestFormatSystemReport
# =========================================================================

class TestFormatSystemReport:
    """format_system_report 함수 테스트."""

    def test_no_bold(self):
        checks = [HealthCheck(name="disk", status="ok", message="정상")]
        text = format_system_report(checks)
        assert "**" not in text

    def test_contains_username(self):
        checks = [HealthCheck(name="disk", status="ok", message="정상")]
        text = format_system_report(checks)
        assert "주호님" in text

    def test_contains_system_keyword(self):
        checks = [HealthCheck(name="disk", status="ok", message="정상")]
        text = format_system_report(checks)
        assert "시스템" in text

    def test_with_errors(self):
        checks = [
            HealthCheck(name="db", status="error", message="DB 접근 불가"),
        ]
        text = format_system_report(checks)
        assert "오류" in text


# =========================================================================
# TestFormatHealthAlert
# =========================================================================

class TestFormatHealthAlert:
    """format_health_alert 함수 테스트."""

    def test_no_bold(self):
        failed = [HealthCheck(name="db", status="error", message="DB 손상")]
        text = format_health_alert(failed)
        assert "**" not in text

    def test_contains_problem_keyword(self):
        """'장애' 대신 '문제' 키워드 확인 (실제 코드 메시지 기준)."""
        failed = [HealthCheck(name="db", status="error", message="DB 손상")]
        text = format_health_alert(failed)
        assert "문제" in text

    def test_no_failures_message(self):
        """실패 없음 → 이상 없음 메시지."""
        text = format_health_alert([])
        assert "주호님" in text
        assert "이상" in text

    def test_error_and_warning_sections(self):
        """오류와 주의 항목 모두 표시."""
        failed = [
            HealthCheck(name="db", status="error", message="DB 오류"),
            HealthCheck(name="disk", status="warning", message="디스크 경고"),
        ]
        text = format_health_alert(failed)
        assert "[오류]" in text
        assert "[주의]" in text
