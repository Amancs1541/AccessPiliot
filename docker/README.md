# AccessPilot — run it with Docker

Everything (database, backend, frontend) starts with one command. The code is **downloaded from GitHub during the build**, so you only need this folder and Docker — no Python, Node or Postgres, and no config files to edit.

## Quick start

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker Engine + Compose v2 (Linux).
2. In this folder run:
   ```bash
   docker compose up -d --build
   ```
   The first build takes a few minutes.
3. Open **http://localhost:5173**.
4. First-time login — get the one-time admin password:
   ```bash
   docker compose logs backend | grep -A2 "Bootstrap login"
   ```
   Sign in as `admin` with that password. It is shown **only once**; the setup wizard then walks you through connecting your own identity provider (Entra tenant / app registration). Everything you enter is saved in the database — you never edit code or environment files.
5. When registering the app in Entra, add `http://localhost:5173` as a **Single-page application** redirect URI (see `docs/06_ENTRA_SETUP.md` in the repo).

## What runs

| Container | Purpose | Address |
|---|---|---|
| `frontend` | The dashboard (nginx) | http://localhost:5173 |
| `backend` | API + background workers (FastAPI) | http://localhost:8000 (API docs at `/docs`) |
| `db` | PostgreSQL 16 (not exposed) | internal only |
| `init` | One-shot: creates the random DB password | exits immediately |

Secrets (DB password, credential-encryption key) are generated on first start and stored in the `appdata` volume. The database lives in `pgdata`.

## Everyday commands

```bash
docker compose ps                       # status / health
docker compose logs -f backend          # follow backend logs
docker compose down                     # stop, KEEP all data
docker compose down -v                  # stop and WIPE everything (new admin password on next start)
docker compose build --no-cache && docker compose up -d   # pull the latest code from GitHub
```

Pick a specific version instead of `main`: `GIT_REF=<branch|tag|commit> docker compose up -d --build`.
Use a fork: `GITHUB_REPO=<owner>/<repo> docker compose up -d --build`.

## Backup

```bash
docker compose exec db pg_dump -U accesspilot accesspilot > accesspilot.sql
```
Also back up the `appdata` volume — it holds the key that decrypts saved provider credentials. Without it, credentials stored in a restored database cannot be read.

## Things to know

- **Localhost defaults.** The dashboard expects the API at `http://localhost:8000` and is served at `http://localhost:5173`. To host it on another address, set before building:
  `VITE_API_BASE_URL=https://api.example.com FRONTEND_URL=https://app.example.com docker compose up -d --build`
  (the API address is compiled into the frontend, so changing it needs a rebuild). Put HTTPS in front for anything beyond your own machine.
- **Only one backend container.** Sync, expiry and activation workers run inside it; never scale it above 1.
- **The build uses whatever is on GitHub.** Push your latest commits (including database migrations) before building, or the container will run the older code.
- **Troubleshooting:** `docker compose logs backend` shows migration and startup errors. If the dashboard loads but requests fail, check that the address in your browser matches `FRONTEND_URL` (CORS).
