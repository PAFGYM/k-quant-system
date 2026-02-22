"""Multi-user foundation with single-user default (사용자 관리).

Provides user configuration, authorization, and notification settings.
Currently single-user (주호님), designed to be extensible for
multi-user support later.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# Default user ID (placeholder for 주호님's Telegram ID)
DEFAULT_USER_ID = 0
DEFAULT_USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class UserConfig:
    """사용자 설정."""

    user_id: int = 0                        # Telegram ID
    name: str = "주호님"
    is_admin: bool = True
    notification_settings: dict = field(default_factory=lambda: {
        "report": True,
        "supply": True,
        "earnings": True,
        "policy": True,
    })
    risk_limits: dict = field(default_factory=dict)
    created_at: str = ""


# ---------------------------------------------------------------------------
# In-memory user store
# ---------------------------------------------------------------------------

_user_store: dict[int, UserConfig] = {}


def _ensure_default_user() -> None:
    """기본 사용자가 없으면 생성합니다."""
    if DEFAULT_USER_ID not in _user_store:
        _user_store[DEFAULT_USER_ID] = UserConfig(
            user_id=DEFAULT_USER_ID,
            name=DEFAULT_USER_NAME,
            is_admin=True,
            notification_settings={
                "report": True,
                "supply": True,
                "earnings": True,
                "policy": True,
            },
            risk_limits={
                "max_single_position_pct": 0.20,
                "max_daily_loss_pct": 0.05,
                "max_leverage": 1.0,
            },
            created_at=datetime.now(tz=KST).strftime("%Y-%m-%d %H:%M:%S"),
        )


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def get_default_user() -> UserConfig:
    """기본 사용자(주호님) 설정을 반환합니다."""
    try:
        _ensure_default_user()
        return _user_store[DEFAULT_USER_ID]

    except Exception as e:
        logger.error("기본 사용자 조회 실패: %s", e, exc_info=True)
        return UserConfig()


def get_user(user_id: int) -> UserConfig | None:
    """사용자 설정을 조회합니다. 없으면 None을 반환합니다."""
    try:
        _ensure_default_user()
        return _user_store.get(user_id)

    except Exception as e:
        logger.error("사용자 조회 실패 (id=%d): %s", user_id, e, exc_info=True)
        return None


def create_user(user_id: int, name: str) -> UserConfig:
    """새 사용자를 생성합니다."""
    try:
        if user_id in _user_store:
            logger.info("사용자가 이미 존재합니다 (id=%d, name=%s)", user_id, name)
            return _user_store[user_id]

        now = datetime.now(tz=KST).strftime("%Y-%m-%d %H:%M:%S")
        user = UserConfig(
            user_id=user_id,
            name=name,
            is_admin=False,
            notification_settings={
                "report": True,
                "supply": True,
                "earnings": True,
                "policy": True,
            },
            risk_limits={},
            created_at=now,
        )

        _user_store[user_id] = user
        logger.info("사용자 생성: id=%d, name=%s", user_id, name)
        return user

    except Exception as e:
        logger.error("사용자 생성 실패 (id=%d): %s", user_id, e, exc_info=True)
        return UserConfig(user_id=user_id, name=name)


def update_user_settings(user_id: int, settings: dict) -> bool:
    """사용자 알림 설정을 업데이트합니다.

    Args:
        user_id: 텔레그램 사용자 ID
        settings: 업데이트할 설정 딕셔너리

    Returns:
        True if successful, False otherwise
    """
    try:
        _ensure_default_user()
        user = _user_store.get(user_id)

        if user is None:
            logger.warning("사용자를 찾을 수 없습니다 (id=%d)", user_id)
            return False

        # Update notification settings
        if "notification_settings" in settings:
            for key, value in settings["notification_settings"].items():
                if key in user.notification_settings:
                    user.notification_settings[key] = value

        # Update risk limits
        if "risk_limits" in settings:
            user.risk_limits.update(settings["risk_limits"])

        # Update name if provided
        if "name" in settings:
            user.name = settings["name"]

        logger.info("사용자 설정 업데이트: id=%d", user_id)
        return True

    except Exception as e:
        logger.error("사용자 설정 업데이트 실패 (id=%d): %s", user_id, e, exc_info=True)
        return False


def is_authorized(user_id: int) -> bool:
    """사용자가 등록된 사용자인지 확인합니다."""
    try:
        _ensure_default_user()
        return user_id in _user_store

    except Exception as e:
        logger.error("권한 확인 실패 (id=%d): %s", user_id, e, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_user_profile(user: UserConfig) -> str:
    """사용자 프로필을 텔레그램 형식으로 생성합니다."""
    try:
        lines = [
            "[사용자 프로필]",
            "",
            f"  이름: {user.name}",
            f"  ID: {user.user_id}",
            f"  권한: {'관리자' if user.is_admin else '일반'}",
            f"  등록일: {user.created_at or '알 수 없음'}",
            "",
            "-- 알림 설정 --",
        ]

        notification_labels = {
            "report": "리포트 알림",
            "supply": "수급 알림",
            "earnings": "실적 알림",
            "policy": "정책 알림",
        }

        for key, label in notification_labels.items():
            enabled = user.notification_settings.get(key, False)
            status = "켜짐" if enabled else "꺼짐"
            lines.append(f"  {label}: {status}")

        if user.risk_limits:
            lines.append("")
            lines.append("-- 위험 한도 --")

            limit_labels = {
                "max_single_position_pct": "단일 종목 최대 비중",
                "max_daily_loss_pct": "일일 최대 손실",
                "max_leverage": "최대 레버리지",
            }

            for key, label in limit_labels.items():
                if key in user.risk_limits:
                    value = user.risk_limits[key]
                    if "pct" in key:
                        lines.append(f"  {label}: {value * 100:.0f}%")
                    else:
                        lines.append(f"  {label}: {value:.1f}x")

        return "\n".join(lines)

    except Exception as e:
        logger.error("사용자 프로필 생성 실패: %s", e, exc_info=True)
        return "사용자 프로필 생성 중 오류가 발생했습니다."
