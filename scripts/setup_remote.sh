#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K-Quant 맥미니 원격 관리 설정 스크립트
# 맥미니에서 sudo 권한으로 실행:
#   sudo bash scripts/setup_remote.sh
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}K-Quant 맥미니 원격 관리 설정${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Root 확인
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}sudo로 실행해주세요: sudo bash scripts/setup_remote.sh${NC}"
    exit 1
fi

ACTUAL_USER="${SUDO_USER:-botddol}"
HOME_DIR="/Users/$ACTUAL_USER"

# ── 1. SSH 활성화 ─────────────────────────
echo ""
echo -e "${BOLD}[1/4] SSH 활성화${NC}"
systemsetup -setremotelogin on 2>/dev/null || launchctl load -w /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
if systemsetup -getremotelogin 2>/dev/null | grep -q "On"; then
    echo -e "${GREEN}  SSH: 활성화됨${NC}"
else
    echo -e "${GREEN}  SSH: launchctl로 활성화 시도 완료${NC}"
fi

# SSH 키 기반 인증 설정 (비밀번호 없이 접속)
SSH_DIR="$HOME_DIR/.ssh"
if [ ! -d "$SSH_DIR" ]; then
    mkdir -p "$SSH_DIR"
    chown "$ACTUAL_USER:staff" "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    echo -e "${GREEN}  .ssh 디렉토리 생성됨${NC}"
fi

if [ ! -f "$SSH_DIR/authorized_keys" ]; then
    touch "$SSH_DIR/authorized_keys"
    chown "$ACTUAL_USER:staff" "$SSH_DIR/authorized_keys"
    chmod 600 "$SSH_DIR/authorized_keys"
    echo -e "${CYAN}  authorized_keys 생성됨 — 맥북 공개키를 추가해야 합니다${NC}"
    echo -e "  맥북에서 실행: ${BOLD}ssh-copy-id botddol@$(hostname -s).local${NC}"
fi

# ── 2. Tailscale 설치 ────────────────────
echo ""
echo -e "${BOLD}[2/4] Tailscale 설치${NC}"
if command -v tailscale &>/dev/null; then
    echo -e "${GREEN}  Tailscale: 이미 설치됨${NC}"
    tailscale status 2>/dev/null | head -3 || true
else
    if command -v brew &>/dev/null; then
        echo "  Homebrew로 Tailscale 설치 중..."
        sudo -u "$ACTUAL_USER" brew install --cask tailscale 2>/dev/null || true
        echo -e "${GREEN}  Tailscale 설치 완료 — 앱을 실행하고 로그인하세요${NC}"
    else
        echo -e "${CYAN}  Homebrew 없음. 수동 설치 필요:${NC}"
        echo "  https://tailscale.com/download/mac"
    fi
fi

# ── 3. 방화벽 SSH 허용 ───────────────────
echo ""
echo -e "${BOLD}[3/4] 방화벽 설정${NC}"
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -q "enabled" && {
    /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/libexec/sshd-keygen-wrapper 2>/dev/null || true
    echo -e "${GREEN}  방화벽: SSH 허용됨${NC}"
} || echo -e "${GREEN}  방화벽: 비활성 (설정 불필요)${NC}"

# ── 4. 맥북용 SSH config 출력 ────────────
echo ""
echo -e "${BOLD}[4/4] 맥북 설정 안내${NC}"
echo ""

LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "IP_확인필요")
HOSTNAME=$(hostname -s)

echo -e "${CYAN}맥북의 ~/.ssh/config에 추가:${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cat <<SSHCONF

# K-Quant 맥미니 (로컬 네트워크)
Host macmini
    HostName ${HOSTNAME}.local
    User $ACTUAL_USER
    Port 22

# K-Quant 맥미니 (Tailscale — 외부에서)
Host macmini-ts
    HostName ${HOSTNAME}
    User $ACTUAL_USER
    Port 22

SSHCONF
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "${CYAN}맥북에서 테스트:${NC}"
echo "  ssh macmini           # 로컬 네트워크"
echo "  ssh macmini-ts        # Tailscale (외부)"
echo ""
echo -e "${CYAN}맥북에서 봇 관리:${NC}"
echo "  ssh macmini 'cd ~/k-quant-system && ./kbot status'"
echo "  ssh macmini 'cd ~/k-quant-system && ./kbot restart'"
echo "  ssh macmini 'cd ~/k-quant-system && ./kbot logs 20'"
echo ""
echo -e "${GREEN}${BOLD}설정 완료!${NC}"
echo -e "맥미니 IP: ${BOLD}$LOCAL_IP${NC}"
echo -e "호스트명:  ${BOLD}${HOSTNAME}.local${NC}"
