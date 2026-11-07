#!/usr/bin/env bash
# ==============================================================================
# AURON Production Rollback Script
# Source of truth: docs/PRD.md §16, §21
# ==============================================================================
set -euo pipefail

APP_DIR="/home/auron/auron"
BACKUP_BASE="/home/auron/backups"
SERVICE_NAME="auron"

echo "=== [AURON] Rollback Procedure ==="

# Find latest backup
LATEST_BACKUP=$(find "${BACKUP_BASE}" -mindepth 1 -maxdepth 1 -type d -name "auron_*" | sort -r | head -n 1 || true)

if [ -z "${LATEST_BACKUP}" ]; then
    echo "ERROR: No backup found in ${BACKUP_BASE}!" >&2
    exit 1
fi

echo "Rolling back to: ${LATEST_BACKUP}"

# Stop service
sudo systemctl stop "${SERVICE_NAME}"

# Restore data directory
if [ -d "${LATEST_BACKUP}/data" ]; then
    echo "Restoring database and artifacts from backup..."
    rm -rf "${APP_DIR}/data"
    cp -r "${LATEST_BACKUP}/data" "${APP_DIR}/"
fi

# Restart service
sudo systemctl start "${SERVICE_NAME}"
sleep 2

# Health check
HEALTH=$(curl -s http://127.0.0.1:8000/api/health || echo "FAILED")
echo "Health status after rollback: ${HEALTH}"
echo "=== [AURON] Rollback completed ==="
