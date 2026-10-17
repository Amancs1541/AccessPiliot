# AccessPilot — Install & Operate (Docker)

Everything needed to run AccessPilot on a server with Docker: first install, day-to-day
operations, and emergency recovery access.

## Requirements

- Ubuntu/Debian-family Linux server (the install script uses `apt` and Docker's official
  install script).
- A way to reach the server: `localhost` for local-only use, or a real IP/domain for remote
  access (HTTPS is set up automatically via Caddy for remote access, since browsers require it
  for Microsoft sign-in off localhost).

## First install

```bash
git clone https://github.com/Amancs1541/AccessPiliot.git
cd AccessPiliot/docker
./run.sh
```

On first run, `run.sh`:

1. Installs Docker if it isn't already present (via Docker's official convenience script).
2. Asks how the app will be reached (localhost, or a real IP/domain — setting up HTTPS
   automatically if so).
3. Builds everything fresh and starts the containers.
4. Waits for the backend to come up, then prints the **one-time admin password** — save it,
   it is only shown once.

## Day-to-day operations

Run these from the `docker/` directory.

```bash
./run.sh                        # start the app (fast on every run after the first)
./run.sh --rebuild               # pull the latest code from GitHub and rebuild
./run.sh --host=NEW_ADDRESS      # change the address this is reached at (forces a rebuild)
./run.sh --stop                  # stop the app, keep all data
./run.sh --reset                 # stop the app and WIPE all data (new admin password next start)
```

### Useful Docker Compose commands

```bash
docker compose ps                     # container status
docker compose logs -f backend        # follow backend logs
docker compose logs -f frontend       # follow frontend logs
docker compose restart backend        # restart just the backend
docker compose down                   # stop all containers (keeps data volumes)
```

## Break-Glass emergency access

If sign-in is ever misconfigured and locks everyone out, the Break-Glass URL gives access to a
minimal recovery dashboard (identity provider configuration + Break-Glass password rotation
only — nothing else in AccessPilot is reachable from it). It is never linked anywhere in the
app itself.

```bash
cd docker
./breakglass-url.sh                # print the existing URL (generates one first if none exists)
./breakglass-url.sh --regenerate    # invalidate the existing URL and generate a new one
```

Keep that URL somewhere safe outside the app.

## Updating after a code change

```bash
cd docker
./run.sh --rebuild
```

This pulls the latest code from GitHub and rebuilds the containers in place; existing data is
untouched.
