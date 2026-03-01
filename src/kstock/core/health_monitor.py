"""System health monitoring and auto-recovery (core/health_monitor.py).

Checks bot, DB, disk, memory, data freshness.
Auto-recovery attempts for common failures.
"""

from __future__ import annotations

import logging
import os
import platform
import resource
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class HealthCheck:
    """단일 헬스체크 결과."""

    name: str
    status: str = "ok"  # ok, warning, error
    message: str = ""
    checked_at: str = ""


@dataclass
class SystemReport:
    """시스템 전체 상태 보고서."""

    uptime_seconds: float = 0.0
    checks: list[HealthCheck] = field(default_factory=list)
    disk_usage_pct: float = 0.0
    memory_usage_pct: float = 0.0
    db_size_mb: float = 0.0
    error_count: int = 0
    recovery_attempts: int = 0


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_disk_usage(threshold_pct: float = 90.0) -> HealthCheck:
    """디스크 사용량 확인. threshold_pct 초과 시 warning/error 반환."""
    check = HealthCheck(
        name="disk_usage",
        checked_at=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        usage = shutil.disk_usage("/")
        used_pct = (usage.used / usage.total) * 100.0
        free_gb = usage.free / (1024 ** 3)

        if used_pct >= threshold_pct + 5:
            check.status = "error"
            check.message = (
                f"{USER_NAME}, 디스크 사용량이 {used_pct:.1f}%로 심각합니다. "
                f"남은 용량: {free_gb:.1f}GB. 즉시 정리가 필요합니다."
            )
            logger.error("디스크 사용량 심각: %.1f%%", used_pct)
        elif used_pct >= threshold_pct:
            check.status = "warning"
            check.message = (
                f"{USER_NAME}, 디스크 사용량이 {used_pct:.1f}%입니다. "
                f"남은 용량: {free_gb:.1f}GB. 불필요한 파일 정리를 권장합니다."
            )
            logger.warning("디스크 사용량 경고: %.1f%%", used_pct)
        else:
            check.status = "ok"
            check.message = f"디스크 사용량 {used_pct:.1f}% (남은 용량: {free_gb:.1f}GB)"
    except Exception as exc:
        check.status = "error"
        check.message = f"디스크 사용량 확인 실패: {exc}"
        logger.exception("디스크 사용량 확인 중 오류 발생")

    return check


def _get_memory_usage_darwin() -> float:
    """macOS에서 메모리 사용률(%) 반환. vm_stat 파싱."""
    try:
        result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return -1.0

        lines = result.stdout.strip().split("\n")
        page_size = 16384  # default for Apple Silicon
        first_line = lines[0] if lines else ""
        if "page size of" in first_line:
            try:
                page_size = int(first_line.split("page size of")[1].strip().rstrip(".").strip())
            except (ValueError, IndexError):
                page_size = 16384

        stats: dict[str, int] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip().rstrip(".")
            try:
                stats[key] = int(val)
            except ValueError:
                continue

        free_pages = stats.get("pages free", 0)
        inactive_pages = stats.get("pages inactive", 0)
        speculative_pages = stats.get("pages speculative", 0)
        active_pages = stats.get("pages active", 0)
        wired_pages = stats.get("pages wired down", 0)
        compressed_pages = stats.get("pages occupied by compressor", 0)

        total_pages = (
            free_pages + inactive_pages + speculative_pages
            + active_pages + wired_pages + compressed_pages
        )
        if total_pages == 0:
            return -1.0

        used_pages = active_pages + wired_pages + compressed_pages
        return (used_pages / total_pages) * 100.0
    except Exception:
        logger.debug("_get_memory_usage_darwin failed", exc_info=True)
        return -1.0


def _get_memory_usage_linux() -> float:
    """Linux에서 /proc/meminfo 파싱하여 메모리 사용률(%) 반환."""
    try:
        meminfo_path = Path("/proc/meminfo")
        if not meminfo_path.exists():
            return -1.0

        mem: dict[str, int] = {}
        text = meminfo_path.read_text(encoding="utf-8")
        for line in text.strip().split("\n"):
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            parts = val.strip().split()
            if parts:
                try:
                    mem[key.strip()] = int(parts[0])
                except ValueError:
                    continue

        total_kb = mem.get("MemTotal", 0)
        available_kb = mem.get("MemAvailable", 0)
        if total_kb == 0:
            return -1.0

        used_kb = total_kb - available_kb
        return (used_kb / total_kb) * 100.0
    except Exception:
        logger.debug("_get_memory_usage_linux failed", exc_info=True)
        return -1.0


def check_memory_usage(threshold_pct: float = 80.0) -> HealthCheck:
    """메모리 사용량 확인. macOS와 Linux 모두 지원."""
    check = HealthCheck(
        name="memory_usage",
        checked_at=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        system = platform.system()
        if system == "Darwin":
            used_pct = _get_memory_usage_darwin()
        elif system == "Linux":
            used_pct = _get_memory_usage_linux()
        else:
            # 기타 OS: resource 모듈로 현재 프로세스 RSS만 확인
            ru = resource.getrusage(resource.RUSAGE_SELF)
            rss_mb = ru.ru_maxrss / (1024 * 1024) if system != "Darwin" else ru.ru_maxrss / (1024 * 1024)
            check.status = "ok"
            check.message = f"메모리 사용량 직접 측정 불가 (프로세스 RSS: {rss_mb:.1f}MB)"
            return check

        if used_pct < 0:
            check.status = "warning"
            check.message = "메모리 사용량을 측정할 수 없습니다."
            logger.warning("메모리 사용량 측정 실패")
            return check

        if used_pct >= threshold_pct + 10:
            check.status = "error"
            check.message = (
                f"{USER_NAME}, 메모리 사용량이 {used_pct:.1f}%로 심각합니다. "
                f"불필요한 프로세스 종료를 권장합니다."
            )
            logger.error("메모리 사용량 심각: %.1f%%", used_pct)
        elif used_pct >= threshold_pct:
            check.status = "warning"
            check.message = (
                f"{USER_NAME}, 메모리 사용량이 {used_pct:.1f}%입니다. "
                f"여유 메모리가 부족해지고 있습니다."
            )
            logger.warning("메모리 사용량 경고: %.1f%%", used_pct)
        else:
            check.status = "ok"
            check.message = f"메모리 사용량 {used_pct:.1f}%"
    except Exception as exc:
        check.status = "error"
        check.message = f"메모리 사용량 확인 실패: {exc}"
        logger.exception("메모리 사용량 확인 중 오류 발생")

    return check


def check_db_accessible(db_path: str | Path) -> HealthCheck:
    """SQLite DB 접근 가능 여부 확인."""
    check = HealthCheck(
        name="db_accessible",
        checked_at=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        db_path = Path(db_path)
        if not db_path.exists():
            check.status = "error"
            check.message = f"DB 파일이 존재하지 않습니다: {db_path}"
            logger.error("DB 파일 없음: %s", db_path)
            return check

        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
            tables = cursor.fetchall()
            if not tables:
                check.status = "warning"
                check.message = "DB에 테이블이 없습니다. 초기화가 필요할 수 있습니다."
                logger.warning("DB 테이블 없음: %s", db_path)
            else:
                # integrity_check 실행
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                if integrity and integrity[0] == "ok":
                    check.status = "ok"
                    check.message = "DB 접근 정상, 무결성 확인 완료"
                else:
                    check.status = "warning"
                    check.message = (
                        f"DB 접근 가능하나 무결성 경고: {integrity[0] if integrity else '알 수 없음'}"
                    )
                    logger.warning("DB 무결성 경고: %s", db_path)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        check.status = "error"
        check.message = f"DB 손상 가능성: {exc}"
        logger.exception("DB 접근 오류: %s", db_path)
    except Exception as exc:
        check.status = "error"
        check.message = f"DB 확인 실패: {exc}"
        logger.exception("DB 확인 중 오류 발생: %s", db_path)

    return check


def check_data_staleness(db_path: str | Path, max_hours: int = 2) -> HealthCheck:
    """DB의 마지막 업데이트 시각 확인. max_hours 이상 지나면 warning.

    장 마감(평일 16:00 이후 ~ 익일 08:30) 및 주말에는 staleness를
    체크하지 않습니다 (정상적으로 데이터가 없는 시간대).
    """
    check = HealthCheck(
        name="data_staleness",
        checked_at=datetime.now().isoformat(timespec="seconds"),
    )

    # 비장시간에는 체크 스킵
    try:
        from kstock.core.tz import KST as KST_TZ
        now_kst = datetime.now(KST_TZ)
        weekday = now_kst.weekday()  # 0=Mon ... 6=Sun
        hour = now_kst.hour

        # 주말 또는 평일 16:00~08:30 → 스킵
        is_weekend = weekday >= 5
        is_off_hours = hour >= 16 or hour < 9
        if is_weekend or is_off_hours:
            check.status = "ok"
            check.message = "비장시간 — staleness 체크 스킵"
            return check
    except Exception:
        logger.debug("check_data_staleness: KST timezone import failed", exc_info=True)

    try:
        db_path = Path(db_path)
        if not db_path.exists():
            check.status = "error"
            check.message = f"DB 파일이 존재하지 않습니다: {db_path}"
            return check

        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            # v5.4: 여러 테이블에서 최신 타임스탬프 확인 (가장 최근 것 사용)
            candidates = []

            # 1. job_runs 성공
            try:
                cursor = conn.execute(
                    "SELECT MAX(ended_at) FROM job_runs WHERE status = 'success'"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    candidates.append(row[0])
            except Exception:
                logger.debug("check_data_staleness: job_runs query failed", exc_info=True)

            # 2. portfolio updated_at
            try:
                cursor = conn.execute("SELECT MAX(updated_at) FROM portfolio")
                row = cursor.fetchone()
                if row and row[0]:
                    candidates.append(row[0])
            except Exception:
                logger.debug("check_data_staleness: portfolio query failed", exc_info=True)

            # 3. chat_history (봇 활동 증거)
            try:
                cursor = conn.execute("SELECT MAX(created_at) FROM chat_history")
                row = cursor.fetchone()
                if row and row[0]:
                    candidates.append(row[0])
            except Exception:
                logger.debug("check_data_staleness: chat_history query failed", exc_info=True)

            # 4. holdings updated_at
            try:
                cursor = conn.execute("SELECT MAX(updated_at) FROM holdings WHERE active=1")
                row = cursor.fetchone()
                if row and row[0]:
                    candidates.append(row[0])
            except Exception:
                logger.debug("check_data_staleness: holdings query failed", exc_info=True)

            if not candidates:
                check.status = "warning"
                check.message = (
                    f"{USER_NAME}, DB에 업데이트 기록이 없습니다. "
                    f"데이터 수집이 아직 실행되지 않았을 수 있습니다."
                )
                logger.warning("DB 업데이트 기록 없음: %s", db_path)
                return check

            # 가장 최근 타임스탬프 사용
            last_run_str = max(candidates)
            last_run = datetime.fromisoformat(last_run_str)

            # v5.4: timezone-aware 비교 (DB가 UTC일 수 있음)
            now = datetime.now()
            if last_run.tzinfo is not None:
                now = datetime.now(last_run.tzinfo)

            age = now - last_run
            age_hours = age.total_seconds() / 3600.0

            if age_hours > max_hours * 3:
                check.status = "error"
                check.message = (
                    f"{USER_NAME}, 마지막 활동이 {age_hours:.1f}시간 전입니다. "
                    f"데이터 수집 파이프라인을 확인해주세요."
                )
                logger.error("데이터 심각하게 오래됨: %.1f시간", age_hours)
            elif age_hours > max_hours:
                check.status = "warning"
                check.message = (
                    f"마지막 활동: {age_hours:.1f}시간 전"
                )
                logger.warning("데이터 오래됨: %.1f시간", age_hours)
            else:
                check.status = "ok"
                check.message = f"마지막 활동: {age_hours:.1f}시간 전"
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        check.status = "warning"
        check.message = f"데이터 최신성 확인 중 테이블 접근 불가: {exc}"
        logger.warning("데이터 최신성 확인 테이블 없음: %s", exc)
    except Exception as exc:
        check.status = "error"
        check.message = f"데이터 최신성 확인 실패: {exc}"
        logger.exception("데이터 최신성 확인 중 오류 발생")

    return check


def check_db_size(db_path: str | Path) -> float:
    """DB 파일 크기를 MB 단위로 반환. 실패 시 -1.0."""
    try:
        db_path = Path(db_path)
        if not db_path.exists():
            return -1.0
        size_bytes = db_path.stat().st_size
        # WAL, SHM 파일도 포함
        wal_path = db_path.with_suffix(db_path.suffix + "-wal")
        shm_path = db_path.with_suffix(db_path.suffix + "-shm")
        if wal_path.exists():
            size_bytes += wal_path.stat().st_size
        if shm_path.exists():
            size_bytes += shm_path.stat().st_size
        return size_bytes / (1024 * 1024)
    except Exception as exc:
        logger.exception("DB 크기 확인 실패: %s", exc)
        return -1.0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_health_checks(db_path: Optional[str | Path] = None) -> list[HealthCheck]:
    """모든 헬스체크를 실행하고 결과 목록을 반환."""
    checks: list[HealthCheck] = []

    try:
        checks.append(check_disk_usage())
    except Exception as exc:
        logger.exception("디스크 체크 실행 실패: %s", exc)
        checks.append(HealthCheck(
            name="disk_usage", status="error",
            message=f"체크 실행 실패: {exc}",
            checked_at=datetime.now().isoformat(timespec="seconds"),
        ))

    try:
        checks.append(check_memory_usage())
    except Exception as exc:
        logger.exception("메모리 체크 실행 실패: %s", exc)
        checks.append(HealthCheck(
            name="memory_usage", status="error",
            message=f"체크 실행 실패: {exc}",
            checked_at=datetime.now().isoformat(timespec="seconds"),
        ))

    if db_path is not None:
        try:
            checks.append(check_db_accessible(db_path))
        except Exception as exc:
            logger.exception("DB 접근 체크 실행 실패: %s", exc)
            checks.append(HealthCheck(
                name="db_accessible", status="error",
                message=f"체크 실행 실패: {exc}",
                checked_at=datetime.now().isoformat(timespec="seconds"),
            ))

        try:
            checks.append(check_data_staleness(db_path))
        except Exception as exc:
            logger.exception("데이터 최신성 체크 실행 실패: %s", exc)
            checks.append(HealthCheck(
                name="data_staleness", status="error",
                message=f"체크 실행 실패: {exc}",
                checked_at=datetime.now().isoformat(timespec="seconds"),
            ))

    return checks


# ---------------------------------------------------------------------------
# Recovery & maintenance
# ---------------------------------------------------------------------------


def attempt_recovery(failed_check: HealthCheck) -> bool:
    """실패한 체크에 대해 자동 복구를 시도. 성공 시 True 반환."""
    try:
        logger.info("자동 복구 시도: %s (상태: %s)", failed_check.name, failed_check.status)

        if failed_check.name == "db_accessible":
            # WAL 체크포인트 시도 - DB 경로를 message에서 추출 어려우므로
            # 기본 경로 시도
            default_db = Path("data/kquant.db")
            if default_db.exists():
                conn = sqlite3.connect(str(default_db), timeout=10)
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.execute("PRAGMA integrity_check")
                    logger.info("DB WAL 체크포인트 완료")
                    return True
                finally:
                    conn.close()
            logger.warning("복구 대상 DB를 찾을 수 없습니다.")
            return False

        if failed_check.name == "disk_usage":
            # 임시 파일 정리 시도
            tmp_dirs = [Path("/tmp"), Path.home() / ".cache"]
            cleaned_count = 0
            for tmp_dir in tmp_dirs:
                if not tmp_dir.exists():
                    continue
                try:
                    for item in tmp_dir.iterdir():
                        if item.name.startswith("kquant_") and item.is_file():
                            age = time.time() - item.stat().st_mtime
                            if age > 86400:  # 24시간 이상 된 임시 파일
                                item.unlink()
                                cleaned_count += 1
                except PermissionError:
                    continue
            logger.info("임시 파일 %d개 정리 완료", cleaned_count)
            return cleaned_count > 0

        if failed_check.name == "data_staleness":
            # 데이터 오래됨은 직접 복구 불가, 로그만 남김
            logger.info(
                "데이터 최신성 문제는 파이프라인 재실행이 필요합니다. "
                "스케줄러 상태를 확인해 주세요."
            )
            return False

        if failed_check.name == "memory_usage":
            # 메모리 문제는 직접 복구 불가
            logger.info(
                "메모리 사용량 문제는 프로세스 재시작이 필요할 수 있습니다."
            )
            return False

        logger.warning("알 수 없는 체크 항목에 대한 복구 시도: %s", failed_check.name)
        return False
    except Exception as exc:
        logger.exception("자동 복구 실패 (%s): %s", failed_check.name, exc)
        return False


def backup_database(db_path: str | Path, backup_dir: str | Path) -> str:
    """DB 파일을 타임스탬프 포함 이름으로 백업. 백업 파일 경로 반환."""
    try:
        db_path = Path(db_path)
        backup_dir = Path(backup_dir)

        if not db_path.exists():
            raise FileNotFoundError(f"DB 파일이 존재하지 않습니다: {db_path}")

        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{db_path.stem}_{timestamp}{db_path.suffix}"
        backup_path = backup_dir / backup_name

        # SQLite online backup API 사용 (WAL 모드에서도 안전)
        source_conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            dest_conn = sqlite3.connect(str(backup_path))
            try:
                source_conn.backup(dest_conn)
                logger.info("DB 백업 완료: %s -> %s", db_path, backup_path)
            finally:
                dest_conn.close()
        finally:
            source_conn.close()

        return str(backup_path)
    except Exception as exc:
        logger.exception("DB 백업 실패: %s", exc)
        raise


def vacuum_database(db_path: str | Path) -> bool:
    """DB에 VACUUM 실행. 성공 시 True 반환."""
    try:
        db_path = Path(db_path)
        if not db_path.exists():
            logger.error("VACUUM 대상 DB 없음: %s", db_path)
            return False

        size_before = db_path.stat().st_size

        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            logger.info("DB VACUUM 완료: %s", db_path)
        finally:
            conn.close()

        size_after = db_path.stat().st_size
        saved_mb = (size_before - size_after) / (1024 * 1024)
        if saved_mb > 0:
            logger.info("VACUUM으로 %.2fMB 절약", saved_mb)

        return True
    except Exception as exc:
        logger.exception("DB VACUUM 실패: %s", exc)
        return False


def cleanup_old_data(db_path: str | Path, days: int = 30) -> int:
    """오래된 alerts, job_runs, trades 데이터 삭제. 삭제된 행 수 반환."""
    total_deleted = 0
    try:
        db_path = Path(db_path)
        if not db_path.exists():
            logger.error("정리 대상 DB 없음: %s", db_path)
            return 0

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            # alerts 테이블 정리
            try:
                cursor = conn.execute(
                    "DELETE FROM alerts WHERE created_at < ?", (cutoff,)
                )
                deleted = cursor.rowcount
                total_deleted += deleted
                logger.info("오래된 alerts %d건 삭제", deleted)
            except sqlite3.OperationalError as exc:
                logger.warning("alerts 테이블 정리 실패: %s", exc)

            # job_runs 테이블 정리
            try:
                cursor = conn.execute(
                    "DELETE FROM job_runs WHERE started_at < ?", (cutoff,)
                )
                deleted = cursor.rowcount
                total_deleted += deleted
                logger.info("오래된 job_runs %d건 삭제", deleted)
            except sqlite3.OperationalError as exc:
                logger.warning("job_runs 테이블 정리 실패: %s", exc)

            # trades 테이블 정리 (closed 상태만)
            try:
                cursor = conn.execute(
                    "DELETE FROM trades WHERE created_at < ?", (cutoff,)
                )
                deleted = cursor.rowcount
                total_deleted += deleted
                logger.info("오래된 trades %d건 삭제", deleted)
            except sqlite3.OperationalError as exc:
                logger.warning("trades 테이블 정리 실패: %s", exc)

            conn.commit()
            logger.info("총 %d건의 오래된 데이터 삭제 완료 (%d일 이전)", total_deleted, days)
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("오래된 데이터 정리 실패: %s", exc)

    return total_deleted


# ---------------------------------------------------------------------------
# launchd plist generation (macOS)
# ---------------------------------------------------------------------------


def generate_launchd_plist(
    bot_script_path: str | Path,
    plist_dir: Optional[str | Path] = None,
) -> str:
    """macOS launchd plist 파일 내용을 생성하여 반환.

    plist_dir이 지정되면 해당 디렉토리에 파일도 저장한다.
    """
    try:
        bot_script_path = Path(bot_script_path).resolve()
        label = "com.kquant.bot"
        python_path = shutil.which("python3") or "/usr/bin/python3"
        log_dir = bot_script_path.parent / "logs"
        working_dir = bot_script_path.parent

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{bot_script_path}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{working_dir}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>60</integer>

    <key>StandardOutPath</key>
    <string>{log_dir}/kquant_bot_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>{log_dir}/kquant_bot_stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>LANG</key>
        <string>ko_KR.UTF-8</string>
    </dict>
</dict>
</plist>
"""
        if plist_dir is not None:
            plist_dir = Path(plist_dir)
            plist_dir.mkdir(parents=True, exist_ok=True)
            plist_file = plist_dir / f"{label}.plist"
            plist_file.write_text(plist_content, encoding="utf-8")
            logger.info("launchd plist 파일 생성: %s", plist_file)

        return plist_content
    except Exception as exc:
        logger.exception("launchd plist 생성 실패: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Report formatting (Korean, no bold)
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "ok": "[정상]",
    "warning": "[주의]",
    "error": "[오류]",
}


def format_system_report(
    checks: list[HealthCheck],
    uptime: float = 0.0,
    db_path: Optional[str | Path] = None,
) -> str:
    """시스템 상태 보고서를 한국어 텍스트로 포맷. 볼드(**) 미사용."""
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = [
            f"[K-Quant 시스템 상태 보고서]",
            f"시각: {now_str}",
        ]

        if uptime > 0:
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            if hours > 0:
                lines.append(f"가동 시간: {hours}시간 {minutes}분")
            else:
                lines.append(f"가동 시간: {minutes}분")

        if db_path is not None:
            db_size = check_db_size(db_path)
            if db_size >= 0:
                lines.append(f"DB 크기: {db_size:.2f}MB")

        lines.append("")
        lines.append("--- 헬스체크 결과 ---")

        error_count = 0
        warning_count = 0
        for chk in checks:
            icon = _STATUS_ICONS.get(chk.status, "[?]")
            lines.append(f"  {icon} {chk.name}: {chk.message}")
            if chk.status == "error":
                error_count += 1
            elif chk.status == "warning":
                warning_count += 1

        lines.append("")
        if error_count > 0:
            lines.append(
                f"{USER_NAME}, 오류가 {error_count}건 발견되었습니다. "
                f"확인이 필요합니다."
            )
        elif warning_count > 0:
            lines.append(
                f"{USER_NAME}, 주의 항목이 {warning_count}건 있습니다. "
                f"참고해 주세요."
            )
        else:
            lines.append(f"{USER_NAME}, 모든 시스템이 정상입니다.")

        return "\n".join(lines)
    except Exception as exc:
        logger.exception("시스템 보고서 포맷 실패: %s", exc)
        return f"{USER_NAME}, 시스템 보고서 생성 중 오류가 발생했습니다: {exc}"


def format_health_alert(failed_checks: list[HealthCheck]) -> str:
    """실패한 체크 항목들에 대한 알림 메시지 포맷. 볼드(**) 미사용."""
    try:
        if not failed_checks:
            return f"{USER_NAME}, 현재 시스템에 이상이 없습니다."

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = [
            f"[K-Quant 시스템 알림] {now_str}",
            "",
            f"{USER_NAME}, 시스템 점검 중 아래 문제가 발견되었습니다.",
            "",
        ]

        errors = [c for c in failed_checks if c.status == "error"]
        warnings = [c for c in failed_checks if c.status == "warning"]

        if errors:
            lines.append(f"[오류] {len(errors)}건:")
            for chk in errors:
                lines.append(f"  - {chk.name}: {chk.message}")
            lines.append("")

        if warnings:
            lines.append(f"[주의] {len(warnings)}건:")
            for chk in warnings:
                lines.append(f"  - {chk.name}: {chk.message}")
            lines.append("")

        # 복구 안내
        lines.append("---")
        if errors:
            lines.append("오류 항목은 즉시 확인이 필요합니다.")
            lines.append("자동 복구를 시도하려면 attempt_recovery()를 실행해 주세요.")
        if warnings:
            lines.append("주의 항목은 모니터링을 계속하겠습니다.")

        return "\n".join(lines)
    except Exception as exc:
        logger.exception("헬스 알림 포맷 실패: %s", exc)
        return f"{USER_NAME}, 시스템 알림 생성 중 오류가 발생했습니다: {exc}"
