#!/usr/bin/env bash
# AccessPilot - read-only database inspection tool. Safe to hand to anyone debugging live: every connection this
# script opens has default_transaction_read_only switched on at the Postgres session level (via PGOPTIONS, not
# just a convention), so an INSERT/UPDATE/DELETE - typed by hand or pasted - is rejected by Postgres itself with
# "cannot execute ... in a read-only transaction", before it ever touches a row. This is not a substitute for the
# real admin UI; it exists purely to answer "what's actually in the database right now" during troubleshooting.
#
# Usage:
#   ./db-query.sh                         interactive read-only psql shell
#   ./db-query.sh "SELECT ...;"           run one read-only query, non-interactively
#   ./db-query.sh --list                  list the built-in common queries
#   ./db-query.sh --common NAME           run a built-in common query by name

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

# Picks up -f docker-compose.https.yml automatically if that stack is in use, same convention as run.sh - the
# `db` service is identical either way, but staying consistent avoids "no such service" confusion.
COMPOSE_FILES=(-f docker-compose.yml)
if [ -f Caddyfile ]; then
  COMPOSE_FILES+=(-f docker-compose.https.yml)
fi
compose() { $DOCKER compose "${COMPOSE_FILES[@]}" "$@"; }

common_query() {
  case "$1" in
    providers)        echo "SELECT id, name, type, status, tenant_id, client_id, last_sync_at FROM identity_providers ORDER BY name;" ;;
    portal-auth)      echo "SELECT id, idp_type, tenant_id, client_id, authority, issuer, audience, scope, is_active FROM portal_auth_configs ORDER BY created_at DESC;" ;;
    users)            echo "SELECT id, display_name, email, external_id, status FROM users ORDER BY created_at DESC LIMIT 20;" ;;
    recent-audit)     echo "SELECT timestamp, action, target_type, result, request_id FROM audit_logs ORDER BY timestamp DESC LIMIT 20;" ;;
    sync-runs)        echo "SELECT id, provider_id, status, started_at, completed_at, users_processed, groups_processed, errors_count FROM sync_runs ORDER BY started_at DESC LIMIT 10;" ;;
    lifecycle)        echo "SELECT * FROM lifecycle_settings;" ;;
    breakglass)       echo "SELECT id, username, is_active, created_at FROM breakglass_accounts;" ;;
    *) echo ""; return 1 ;;
  esac
}

if [ "${1:-}" = "--list" ]; then
  echo "Built-in common queries (./db-query.sh --common NAME):"
  echo "  providers      configured identity providers (Entra/AD/etc.) and their sync status"
  echo "  portal-auth    the portal sign-in IDP configuration row(s)"
  echo "  users          the 20 most recently created users"
  echo "  recent-audit   the 20 most recent audit log entries"
  echo "  sync-runs      the 10 most recent directory sync runs"
  echo "  lifecycle      lifecycle (Joiner/Mover/Leaver) settings"
  echo "  breakglass     break-glass emergency accounts (no password data - just existence/status)"
  exit 0
fi

if [ "${1:-}" = "--common" ]; then
  QUERY="$(common_query "${2:-}")" || { echo "Unknown common query: '${2:-}'. Run with --list to see available names." >&2; exit 1; }
  echo "-- ${QUERY}"
  compose exec -T -e PGOPTIONS="-c default_transaction_read_only=on" db psql -U accesspilot -d accesspilot -c "$QUERY"
  exit 0
fi

if [ $# -eq 0 ]; then
  echo "Interactive read-only session - any write statement will be rejected by Postgres itself."
  echo "Type \\q to quit."
  compose exec -e PGOPTIONS="-c default_transaction_read_only=on" db psql -U accesspilot -d accesspilot
else
  compose exec -T -e PGOPTIONS="-c default_transaction_read_only=on" db psql -U accesspilot -d accesspilot -c "$1"
fi
