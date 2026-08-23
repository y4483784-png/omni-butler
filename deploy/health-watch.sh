#!/bin/sh
# Minimal host-side watch: disk, compose health, sandbox image, /health/ready.
# Install on the Linux VM (every 5 minutes):
#   chmod +x deploy/health-watch.sh
#   crontab -e
#   */5 * * * * COMPOSE_DIR=/mnt/hgfs/omni-butler /mnt/hgfs/omni-butler/deploy/health-watch.sh
# Nightly dump: see deploy/backup.sh and deploy/crontab.example.
#
# Alerts go to syslog (logger) and stderr (cron mail if MAILTO is set).
# The compose `sandbox` service is a one-shot image builder (restart: no) and
# is skipped; HEALTH_SKIP_SERVICES is a comma list.

set -eu

COMPOSE_DIR="${COMPOSE_DIR:-}"
if [ -z "$COMPOSE_DIR" ]; then
  COMPOSE_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
fi
DISK_PATH="${DISK_PATH:-/}"
DISK_LIMIT="${DISK_LIMIT:-80}"
READY_URL="${READY_URL:-http://127.0.0.1/health/ready}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-omni-sandbox:latest}"
# One-shot builders that are expected to exit (comma-separated service names).
HEALTH_SKIP_SERVICES="${HEALTH_SKIP_SERVICES:-sandbox}"

tag="omni-butler-health"
fail=0
msg=""

note() {
  msg="${msg}${1}
"
}

skip_svc() {
  printf '%s\n' "$HEALTH_SKIP_SERVICES" | tr ',' '\n' | grep -qx "$1"
}

pct="$(df -P "$DISK_PATH" | awk 'NR==2 { gsub(/%/, "", $5); print $5 }')"
if [ -n "$pct" ] && [ "$pct" -ge "$DISK_LIMIT" ]; then
  fail=1
  note "disk ${DISK_PATH} at ${pct}% (limit ${DISK_LIMIT}%)"
fi

if ! command -v docker >/dev/null 2>&1; then
  fail=1
  note "docker not found"
elif ! cd "$COMPOSE_DIR"; then
  fail=1
  note "cannot cd ${COMPOSE_DIR}"
else
  if ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
    fail=1
    note "missing image ${SANDBOX_IMAGE} (docker compose build sandbox)"
  fi
  if ! docker compose ps >/dev/null 2>&1; then
    fail=1
    note "docker compose ps failed in ${COMPOSE_DIR}"
  else
    bad=""
    while read -r svc status; do
      [ -n "$svc" ] || continue
      if skip_svc "$svc"; then
        continue
      fi
      st="$(printf '%s' "$status" | tr '[:upper:]' '[:lower:]')"
      case "$st" in
        *unhealthy*|*dead*|*restarting*|*exit*)
          bad="${bad}${svc} ${status}
"
          ;;
      esac
    done <<EOF
$(docker compose ps -a --format '{{.Service}} {{.Status}}')
EOF
    if [ -n "$bad" ]; then
      fail=1
      note "unhealthy/exited services:
${bad}"
    fi
    running="$(docker compose ps --status running --format '{{.Service}}' || true)"
    expected="$(docker compose config --services 2>/dev/null || true)"
    if [ -z "$expected" ]; then
      fail=1
      note "docker compose config --services failed"
    else
      for svc in $expected; do
        if skip_svc "$svc"; then
          continue
        fi
        if ! printf '%s\n' "$running" | grep -qx "$svc"; then
          fail=1
          note "service not running: ${svc}"
        fi
      done
    fi
  fi
fi

if command -v curl >/dev/null 2>&1; then
  code="$(curl -sS -o /tmp/omni-ready.json -w '%{http_code}' --max-time 5 "$READY_URL" || echo 000)"
  if [ "$code" != "200" ]; then
    fail=1
    note "/health/ready HTTP ${code} $(cat /tmp/omni-ready.json 2>/dev/null || true)"
  fi
fi

if [ "$fail" -ne 0 ]; then
  logger -t "$tag" "$msg" 2>/dev/null || true
  printf '%s\n' "$msg" >&2
  exit 1
fi

exit 0
