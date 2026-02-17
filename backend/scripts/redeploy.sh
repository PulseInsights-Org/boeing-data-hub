#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# redeploy.sh — Restart all Boeing Data Hub services
#
# Usage (from anywhere on the server):
#   sudo bash /home/ubuntu/boeing-data-hub/backend/scripts/redeploy.sh
#
# This script does NOT pull code. Manual redeployment steps:
#   cd /home/ubuntu/boeing-data-hub
#   git pull origin main
#   source backend/venv/bin/activate
#   pip install -r backend/requirements.txt --quiet
#   sudo bash backend/scripts/redeploy.sh
# ──────────────────────────────────────────────────────────────

set -euo pipefail

SERVICES=(
  boeing-backend
  boeing-celery-extract
  boeing-celery-publish
  boeing-celery-sync
  boeing-celery-default
  boeing-celery-beat
)

echo "========================================"
echo "  Boeing Data Hub — Redeploying"
echo "========================================"

# ── Stop all services (reverse order: beat first, backend last) ──
echo ""
echo ">> Stopping services..."
for (( i=${#SERVICES[@]}-1; i>=0; i-- )); do
  svc="${SERVICES[$i]}"
  echo "   Stopping $svc..."
  systemctl stop "$svc" 2>/dev/null || true
done
echo "   All services stopped."

# ── Reload systemd in case service files changed ──
echo ""
echo ">> Reloading systemd daemon..."
systemctl daemon-reload

# ── Start all services (forward order: backend first, beat last) ──
echo ""
echo ">> Starting services..."
for svc in "${SERVICES[@]}"; do
  echo "   Starting $svc..."
  systemctl start "$svc"
done
echo "   All services started."

# ── Wait for startup ──
sleep 3

# ── Status check ──
echo ""
echo "========================================"
echo "  Service Status"
echo "========================================"
ALL_OK=true
for svc in "${SERVICES[@]}"; do
  status=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
  if [ "$status" = "active" ]; then
    echo "   OK  $svc"
  else
    echo "   FAIL  $svc  ($status)"
    ALL_OK=false
  fi
done

# ── Health check ──
echo ""
echo ">> Health check..."
for i in 1 2 3 4 5; do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "   Health check passed"
    break
  fi
  if [ "$i" -eq 5 ]; then
    echo "   Health check failed after 5 attempts"
    ALL_OK=false
    break
  fi
  echo "   Attempt $i failed, retrying in 3s..."
  sleep 3
done

echo ""
if [ "$ALL_OK" = true ]; then
  echo "========================================"
  echo "  Redeploy successful!"
  echo "========================================"
else
  echo "========================================"
  echo "  Redeploy completed with errors!"
  echo "  Check logs for failing services:"
  echo "========================================"
fi

echo ""
echo "  View logs:"
echo "    journalctl -u boeing-backend -f"
echo "    journalctl -u boeing-celery-extract -f"
echo "    journalctl -u boeing-celery-publish -f"
echo "    journalctl -u boeing-celery-sync -f"
echo "    journalctl -u boeing-celery-default -f"
echo "    journalctl -u boeing-celery-beat -f"
echo ""
