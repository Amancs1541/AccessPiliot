#!/bin/sh
# Backend container entrypoint: build config, generate one-time secrets, migrate, start.
set -e

# Postgres password created once by the `init` service.
DB_PASSWORD="$(cat /data/pg_password)"
export DATABASE_URL="postgresql+asyncpg://accesspilot:${DB_PASSWORD}@db:5432/accesspilot"

# Fernet key that encrypts provider credentials — generated once, then kept forever in the volume.
if ! grep -q '^PROVIDER_CREDENTIAL_KEY=' /data/.env 2>/dev/null; then
  echo "PROVIDER_CREDENTIAL_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> /data/.env
  echo "Generated PROVIDER_CREDENTIAL_KEY (stored in the appdata volume)."
fi

# Create/upgrade the schema; retry while Postgres finishes starting.
attempt=0
until alembic upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "Database migration failed after $attempt attempts." >&2
    exit 1
  fi
  echo "Migration attempt $attempt failed; retrying in 3s..."
  sleep 3
done

# One process, no --reload: background workers run inside it.
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
