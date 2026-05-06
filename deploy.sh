#!/usr/bin/env bash
#
# RoomMind – Deploy to Home Assistant via SSH
#
# Configuration (in order of priority):
#   1. Command-line args:  ./deploy.sh 192.168.1.100 22
#   2. Environment file:   .env (copy .env.example to get started)
#   3. Built-in defaults:  localhost:22
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env if present
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/.env"
fi

HA_IP="${1:-${HA_IP:-localhost}}"
SSH_PORT="${2:-${SSH_PORT:-22}}"
SSH_USER="${SSH_USER:-root}"
REMOTE_CONFIG="${REMOTE_CONFIG:-/config}"

# Suppress macOS resource fork files in tar
export COPYFILE_DISABLE=1

SSH_OPTS="-p ${SSH_PORT} -o StrictHostKeyChecking=no"
[[ -n "${SSH_KEY:-}" ]] && SSH_OPTS="-i ${SSH_KEY} ${SSH_OPTS}"
SSH_CMD="ssh ${SSH_OPTS}"

echo "==> Deploying RoomMind to ${SSH_USER}@${HA_IP}:${SSH_PORT}"

# 1. Build frontend
echo "--- Building frontend ---"
if ! command -v npm >/dev/null 2>&1; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck source=/dev/null
  [[ -s "${NVM_DIR}/nvm.sh" ]] && . "${NVM_DIR}/nvm.sh"
fi
(cd "${SCRIPT_DIR}/frontend" && npm run build --silent)
echo "    OK"

# 2. Deploy integration (backend + frontend bundle)
echo "--- Deploying integration ---"
${SSH_CMD} "${SSH_USER}@${HA_IP}" \
  "sudo mkdir -p ${REMOTE_CONFIG}/custom_components/roommind && \
   sudo find ${REMOTE_CONFIG}/custom_components/roommind -name '__pycache__' -exec rm -rf {} + 2>/dev/null; true"
tar czf - -C "${SCRIPT_DIR}/custom_components/roommind" . | \
  ${SSH_CMD} "${SSH_USER}@${HA_IP}" "sudo tar xzf - -C ${REMOTE_CONFIG}/custom_components/roommind/"
echo "    OK"

echo ""
echo "==> Done! Next steps:"
echo "    - Python changes:        Settings → Integrations → RoomMind → ⋮ → Reload"
echo "    - Frontend changes:      Hard-refresh browser (Cmd+Shift+R / Ctrl+Shift+R)"
echo "    - WS schema / manifest:  Full HA restart (Settings → System → Restart)"
