"""Security Module — 보안 검증 + 키 마스킹 + 환경변수 검증.

K-Quant v3.6: 보안 강화.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 필수 환경변수 ────────────────────────────────────────────────────────────

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "텔레그램 봇 토큰",
    "TELEGRAM_CHAT_ID": "텔레그램 채팅 ID",
    "ANTHROPIC_API_KEY": "Claude API 키",
}

OPTIONAL_VARS = {
    "KIS_APP_KEY": "한국투자증권 앱 키",
    "KIS_APP_SECRET": "한국투자증권 시크릿",
    "KIS_ACCOUNT_NO": "계좌번호",
    "KIS_HTS_ID": "HTS ID",
    "OPENAI_API_KEY": "OpenAI API 키",
    "GEMINI_API_KEY": "Gemini API 키",
}


def mask_key(key: str, show: int = 4) -> str:
    """API 키를 마스킹 (앞 4자리만 표시)."""
    if not key:
        return "(미설정)"
    if len(key) <= show:
        return "****"
    return key[:show] + "*" * min(len(key) - show, 20)


def validate_environment() -> tuple[bool, list[str]]:
    """환경변수 검증.

    Returns:
        (all_required_ok, messages)
    """
    messages: list[str] = []
    all_ok = True

    messages.append("🔐 환경변수 보안 검증")
    messages.append("─" * 30)

    # 필수 변수 확인
    for var, desc in REQUIRED_VARS.items():
        val = os.getenv(var, "")
        if val:
            messages.append(f"  ✅ {desc}: {mask_key(val)}")
        else:
            messages.append(f"  ❌ {desc}: 미설정!")
            all_ok = False

    messages.append("")
    messages.append("📦 선택 환경변수")

    # 선택 변수 확인
    optional_count = 0
    for var, desc in OPTIONAL_VARS.items():
        val = os.getenv(var, "")
        if val:
            messages.append(f"  ✅ {desc}: {mask_key(val)}")
            optional_count += 1
        else:
            messages.append(f"  ⬜ {desc}: 미설정")

    messages.append("")

    # AI 엔진 상태
    ai_count = sum(1 for v in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]
                   if os.getenv(v, ""))
    messages.append(f"🤖 AI 엔진: {ai_count}/3 활성")
    messages.append(f"📡 KIS 연동: {'활성' if os.getenv('KIS_APP_KEY') else '미설정'}")

    return all_ok, messages


def check_gitignore() -> list[str]:
    """보안 파일이 .gitignore에 포함되어 있는지 확인."""
    warnings = []
    gitignore_path = Path(".gitignore")

    sensitive_patterns = [".env", "*.db", "data/", "config/kis_config.yaml"]

    if not gitignore_path.exists():
        warnings.append("⚠️ .gitignore 파일이 없습니다!")
        return warnings

    content = gitignore_path.read_text()

    for pattern in sensitive_patterns:
        # 간단한 체크: 패턴이 .gitignore에 있는지
        if pattern not in content and pattern.replace("*", "") not in content:
            warnings.append(f"⚠️ '{pattern}'이 .gitignore에 없습니다!")

    if not warnings:
        warnings.append("✅ .gitignore 보안 설정 정상")

    return warnings


def check_env_file_safety() -> list[str]:
    """'.env' 파일이 git 추적되지 않는지 확인."""
    warnings = []
    env_path = Path(".env")

    if env_path.exists():
        # git ls-files로 추적 여부 확인
        try:
            import subprocess
            result = subprocess.run(
                ["git", "ls-files", ".env"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                warnings.append("🚨 .env가 git에 추적되고 있습니다! 'git rm --cached .env' 실행 필요!")
            else:
                warnings.append("✅ .env 파일 git 미추적 (안전)")
        except Exception:
            warnings.append("⚠️ git 상태 확인 불가")
    else:
        warnings.append("ℹ️ .env 파일 없음 (환경변수로 직접 설정)")

    return warnings


def security_audit() -> str:
    """전체 보안 감사 실행."""
    lines = ["🔒 K-Quant 보안 감사", "═" * 30, ""]

    # 1. 환경변수
    ok, env_msgs = validate_environment()
    lines.extend(env_msgs)
    lines.append("")

    # 2. .gitignore
    gi_msgs = check_gitignore()
    lines.extend(gi_msgs)
    lines.append("")

    # 3. .env 파일 안전성
    ef_msgs = check_env_file_safety()
    lines.extend(ef_msgs)
    lines.append("")

    # 4. 설정 파일 보안
    config_path = Path("config/kis_config.yaml")
    if config_path.exists():
        content = config_path.read_text()
        if "app_secret" in content and len(content) > 100:
            # 시크릿이 실제로 설정되어 있는지 확인
            import yaml
            try:
                cfg = yaml.safe_load(content)
                kis = cfg.get("kis", {})
                for mode in ("virtual", "real"):
                    secret = kis.get(mode, {}).get("app_secret", "")
                    if secret and len(secret) > 10:
                        lines.append(f"⚠️ kis_config.yaml {mode} 모드에 app_secret 포함")
            except Exception:
                pass
    lines.append("")

    # 종합 점수
    warnings_count = sum(1 for l in lines if "⚠️" in l or "🚨" in l)
    if warnings_count == 0:
        lines.append("🏆 보안 상태: 양호 (경고 0건)")
    else:
        lines.append(f"⚠️ 보안 경고: {warnings_count}건 확인 필요")

    return "\n".join(lines)


def startup_security_check() -> None:
    """봇 시작 시 보안 검증 (필수 변수 누락 시 경고)."""
    ok, msgs = validate_environment()
    for m in msgs:
        if "❌" in m:
            logger.error(m)
        elif "⚠️" in m:
            logger.warning(m)
        else:
            logger.info(m)

    if not ok:
        logger.error("=" * 40)
        logger.error("필수 환경변수 누락! 봇이 정상 작동하지 않을 수 있습니다.")
        logger.error("=" * 40)

    # .env git 추적 체크
    for msg in check_env_file_safety():
        if "🚨" in msg:
            logger.error(msg)
