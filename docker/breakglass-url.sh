#!/usr/bin/env bash
# AccessPilot - prints the hidden Break-Glass emergency-access URL, generating one if it doesn't exist yet.
#
# Wraps `python -m app.cli emergency-url` (see backend/app/cli.py) inside the backend container, reconstructing
# DATABASE_URL the same way entrypoint.sh does at container startup - a plain `docker compose exec` does NOT
# inherit it, since it's only ever `export`ed inside entrypoint.sh's own one-time startup shell before it execs
# into uvicorn, never stored as a real container-level environment variable.
#
# Usage:
#   ./breakglass-url.sh                print the existing URL (generates one first if none exists yet)
#   ./breakglass-url.sh --regenerate   invalidate the existing URL and generate a new one

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if sudo -n docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  elif sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  fi
fi

COMPOSE_FILES=(-f docker-compose.yml)
[ -f Caddyfile ] && COMPOSE_FILES+=(-f docker-compose.https.yml)
compose() { $DOCKER compose "${COMPOSE_FILES[@]}" "$@"; }

CLI_ARGS=""
case "${1:-}" in
  "") ;;
  --regenerate)
    echo "This invalidates the existing Break-Glass URL - anyone relying on the old one loses access to it."
    read -rp "Type YES to continue: " CONFIRM
    if [ "$CONFIRM" != "YES" ]; then echo "Cancelled - nothing changed."; exit 0; fi
    CLI_ARGS="--regenerate"
    ;;
  *)
    echo "Unknown option: $1" >&2
    echo "Usage: $0 [--regenerate]" >&2
    exit 1
    ;;
esac

compose exec -T backend sh -c "export DATABASE_URL=\"postgresql+asyncpg://accesspilot:\$(cat /data/pg_password)@db:5432/accesspilot\"; python -m app.cli emergency-url ${CLI_ARGS}"
