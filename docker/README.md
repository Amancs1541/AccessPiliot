# AccessPilot — run it with Docker

Everything (database, backend, frontend) starts with one command. The code is **downloaded from GitHub during the build**, so you only need this folder and Docker — no Python, Node or Postgres, and no config files to edit.

## Easiest: one script

Download just this `docker/` folder (or the whole repo) and run it.

**Windows:**
```powershell
./run.ps1
```
(or double-click `run.cmd` in File Explorer). Installs Docker Desktop for you if missing (via `winget`), waits for
it to start, builds and starts AccessPilot, then prints your one-time admin password and opens the dashboard.
Options: `./run.ps1 -Rebuild`, `./run.ps1 -Stop`, `./run.ps1 -Reset`.

**Ubuntu / Debian-family Linux (including a headless server):**
```bash
chmod +x run.sh    # first time only
./run.sh
```
Installs Docker via the official convenience script if missing. On a real server (not your own desktop), it asks
once for the IP address or domain name you'll reach it at — the frontend needs that baked in at build time, since
it can't be changed afterward without rebuilding. Leave it blank only if you'll browse to the server from itself.

Give it a real address and it **also sets up HTTPS automatically**, via [Caddy](https://caddyserver.com/) as a
reverse proxy in front of everything on that one address:
- A **domain name** (DNS already pointing at this server, ports 80+443 reachable from the internet) gets a real,
  trusted, free certificate from Let's Encrypt — fully automatic, nothing to generate or renew yourself.
- A **bare IP address** gets a self-signed certificate instead (Let's Encrypt cannot issue a trusted certificate
  for an IP) — still fully encrypted, just with a one-time "not trusted" browser warning to click through.

This isn't optional cosmetics: browsers only allow the cryptographic functions Microsoft sign-in needs
(`crypto.subtle`, used for PKCE) on `https://` or `http://localhost` — sign-in fails with *"The crypto object or
function is not available"* on a plain `http://` IP or domain, by browser design, not an AccessPilot bug.

Options: `./run.sh --rebuild`, `./run.sh --host=NEW_ADDRESS` (change the address later, rebuilds), `./run.sh --stop`,
`./run.sh --reset`.

**Either script**: the first run does the full install + build; every run after that just starts the already-built
app — fast, no rebuild, unless you explicitly ask for one.

macOS: use the manual steps below — Docker Desktop's installer isn't scriptable the same way there.

## Quick start (manual, any OS)

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
| `caddy` *(only when given a real address)* | Reverse proxy + automatic HTTPS in front of `frontend`/`backend` | https://your-address |

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

## Checking on it

```bash
./health-check.sh           full report: containers, backend/frontend/HTTPS reachability, database, disk space
./health-check.sh --quiet   prints nothing and exits 0 if healthy - only speaks up on a real problem (cron-friendly)
```
Entirely read-only — it only runs `docker compose ps` and a couple of HTTP/`pg_isready` probes, never starts, stops or changes anything.

```bash
./db-query.sh                              interactive read-only database shell
./db-query.sh "SELECT ... ;"               run one read-only query
./db-query.sh --list                       list built-in common queries (providers, users, recent-audit, ...)
./db-query.sh --common providers           run one of those by name
```
Every connection this opens has Postgres's own `default_transaction_read_only` switched on first — an `INSERT`/`UPDATE`/`DELETE`, typed by hand or pasted from somewhere untrusted, is rejected by Postgres itself before it touches a row. Use this to look something up during troubleshooting; use the admin UI (or a real migration) to actually change anything.

```bash
./breakglass-url.sh                print the hidden Break-Glass emergency-access URL (generates one first if none exists)
./breakglass-url.sh --regenerate   invalidate the old one and generate a fresh URL
```
This is the only way to get that URL — it's never shown or linked anywhere in the app itself, by design (see `backend/app/cli.py`). The URL is shown once in the terminal output; store it securely and don't share or link it anywhere.

## Things to know

- **Localhost defaults.** The dashboard expects the API at `http://localhost:8000` and is served at `http://localhost:5173`. `run.sh` handles another address (and HTTPS) for you automatically — see above. Doing it by hand instead: set, before building,
  `VITE_API_BASE_URL=https://your-address FRONTEND_URL=https://your-address docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build`
  (the API address is compiled into the frontend, so changing it needs a rebuild), and write `docker/Caddyfile` yourself from `Caddyfile.template` (replace `{{HOST}}` with your address, `{{TLS_DIRECTIVE}}` with `tls internal` for a bare IP or leave it blank for a real domain). Put HTTPS in front for anything beyond your own machine — see the crypto-availability note above for why it's not optional.
- **Only one backend container.** Sync, expiry and activation workers run inside it; never scale it above 1.
- **The build uses whatever is on GitHub.** Push your latest commits (including database migrations) before building, or the container will run the older code.
- **Troubleshooting:** `docker compose logs backend` shows migration and startup errors. If the dashboard loads but requests fail, check that the address in your browser matches `FRONTEND_URL` (CORS).
