#!/usr/bin/env bash
# AccessPilot - one-shot health check. Read-only: runs `docker compose ps`, a couple of HTTP probes, and a disk
# check; never starts, stops or changes anything. Exits 0 if everything looks healthy, 1 if something needs
# attention - safe to wire into cron/monitoring (`./health-check.sh --quiet || mail -s "AccessPilot unhealthy" ...`).
#
# Usage:
#   ./health-check.sh            full report
#   ./health-check.sh --quiet    only prints problems (silent, exit 0) when everything is healthy

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

QUIET=0
case "${1:-}" in
  --quiet|-q) QUIET=1 ;;
esac

PROBLEMS=0
step()  { [ "$QUIET" = "1" ] || { echo; echo "==> $1"; }; }
ok()    { [ "$QUIET" = "1" ] || echo "    [OK] $1"; }
warn()  { echo "    [!]  $1"; PROBLEMS=$((PROBLEMS + 1)); }
err()   { echo "    [FAIL] $1" >&2; PROBLEMS=$((PROBLEMS + 1)); }

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if sudo -n docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  elif sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  fi
fi

step "Docker daemon"
if ! $DOCKER info >/dev/null 2>&1; then
  err "Docker is not running or not reachable (tried with and without sudo)."
  echo
  echo "$PROBLEMS problem(s) found."
  exit 1
fi
ok "Docker is running"

HTTPS_ENABLED=0
COMPOSE_FILES=(-f docker-compose.yml)
if [ -f Caddyfile ]; then
  HTTPS_ENABLED=1
  COMPOSE_FILES+=(-f docker-compose.https.yml)
fi
compose() { $DOCKER compose "${COMPOSE_FILES[@]}" "$@"; }

ACCESS_HOST=""
if [ -f .accesspilot-host.env ]; then
  # shellcheck disable=SC1091
  source .accesspilot-host.env
fi

step "Container status"
PS_OUTPUT="$(compose ps 2>/dev/null || true)"
check_service() {
  local name="$1" line
  line="$(echo "$PS_OUTPUT" | grep -i "accesspilot-${name}-1" || true)"
  if [ -z "$line" ]; then
    err "$name: no container found - is the stack started? (run ./run.sh)"
  elif echo "$line" | grep -qi "unhealthy\|restarting\|exit"; then
    err "$name: $line"
  elif echo "$line" | grep -qi "up"; then
    ok "$name: up"
  else
    warn "$name: unrecognized status - check manually: docker compose ps  --  $line"
  fi
}
check_service "db"
check_service "backend"
check_service "frontend"
[ "$HTTPS_ENABLED" = "1" ] && check_service "caddy"

step "Backend API"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS -m 5 "http://localhost:8000/health" >/dev/null 2>&1; then
    ok "http://localhost:8000/health responds"
  else
    err "http://localhost:8000/health did not respond within 5s - check: $DOCKER compose logs backend"
  fi
else
  warn "curl not available - cannot check the backend API directly."
fi

step "Frontend"
if command -v curl >/dev/null 2>&1; then
  CODE="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://localhost:5173/" 2>/dev/null || echo "000")"
  if [ "$CODE" = "200" ]; then
    ok "http://localhost:5173/ responds (200)"
  else
    err "http://localhost:5173/ returned '$CODE' instead of 200 - check: $DOCKER compose logs frontend"
  fi
fi

if [ "$HTTPS_ENABLED" = "1" ] && [ -n "$ACCESS_HOST" ] && command -v curl >/dev/null 2>&1; then
  step "HTTPS (Caddy)"
  CODE="$(curl -sk -o /dev/null -m 8 -w '%{http_code}' "https://${ACCESS_HOST}/" 2>/dev/null || echo "000")"
  if [ "$CODE" = "200" ]; then
    ok "https://${ACCESS_HOST}/ responds (200) - certificate may still be self-signed, that's expected for a bare IP"
  else
    err "https://${ACCESS_HOST}/ returned '$CODE' instead of 200 - check: $DOCKER compose logs caddy"
  fi
fi

step "Database"
if compose exec -T db pg_isready -U accesspilot -d accesspilot >/dev/null 2>&1; then
  ok "Postgres accepts connections"
else
  err "Postgres is not accepting connections - check: $DOCKER compose logs db"
fi

step "Disk space"
DISK_LINE="$(df -h / 2>/dev/null | tail -1)"
USE_PCT="$(echo "$DISK_LINE" | awk '{print $5}' | tr -d '%')"
if [ -n "$USE_PCT" ] && [ "$USE_PCT" -ge 85 ] 2>/dev/null; then
  warn "Root filesystem is ${USE_PCT}% full - Docker images/volumes live here too. Consider: docker system prune"
else
  ok "Root filesystem usage: ${USE_PCT:-unknown}%"
fi

echo
if [ "$PROBLEMS" -eq 0 ]; then
  [ "$QUIET" = "1" ] || echo "All checks passed."
  exit 0
else
  echo "$PROBLEMS problem(s) found - see [FAIL]/[!] lines above."
  exit 1
fi
