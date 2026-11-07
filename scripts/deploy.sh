#!/usr/bin/env bash
# ==============================================================================
# AURON Production Deployment Script
# Source of truth: docs/PRD.md §16, §21
# ==============================================================================
set -euo pipefail

APP_DIR="/home/auron/auron"
VENV_DIR="/home/auron/venv"
SERVICE_NAME="auron"
CADDY_SERVICE="caddy"

echo "=== [AURON] Starting Production Deployment ==="

# 1. Check user
if [ "$(id -u)" -eq 0 ]; then
    echo "ERROR: Do not run this script directly as root. Run as 'auron' with sudo privileges." >&2
    exit 1
fi

# 2. Backup current state
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/home/auron/backups/auron_${TIMESTAMP}"
echo "[1/7] Creating backup at ${BACKUP_DIR}..."
mkdir -p "${BACKUP_DIR}"
if [ -d "${APP_DIR}/data" ]; then
    cp -r "${APP_DIR}/data" "${BACKUP_DIR}/" 2>/dev/null || true
fi

# 3. Pull / sync latest files
echo "[2/7] Checking application directory..."
cd "${APP_DIR}"

# 4. Update Python dependencies
echo "[3/7] Updating virtualenv dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

# 5. Build/verify Docker sandbox image
echo "[4/7] Ensuring sandbox image is up to date..."
docker build -t auron-sandbox:latest -f "${APP_DIR}/sandbox/Dockerfile" "${APP_DIR}/sandbox"

# 6. Ensure Caddy configuration
echo "[5/7] Validating and applying Caddy configuration..."
sudo cp "${APP_DIR}/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo systemctl reload "${CADDY_SERVICE}"

# 7. Restart AURON backend
echo "[6/7] Restarting ${SERVICE_NAME} service..."
sudo systemctl restart "${SERVICE_NAME}"
sleep 2

# 8. Health check
echo "[7/7] Running post-deploy health check..."
HEALTH_STATUS=$(curl -s -f http://127.0.0.1:8000/api/health || echo "FAILED")
echo "Health Response: ${HEALTH_STATUS}"

if echo "${HEALTH_STATUS}" | grep -q '"status":"ok"'; then
    echo "=== [AURON] Deployment SUCCEEDED at $(date) ==="
    exit 0
else
    echo "=== [AURON] Deployment FAILED health check! ===" >&2
    exit 1
fi

