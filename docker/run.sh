#!/usr/bin/env bash
# AccessPilot - one script to install, start, and (on later runs) just start the app again.
# Works on Ubuntu/Debian-family Linux (uses Docker's official install script + apt-family package names).
#
# First run:  checks for Docker (installs it via the official convenience script if missing), asks how you'll
#             reach the app (its own localhost, or a real IP/domain for remote access - setting up HTTPS
#             automatically via Caddy if so, since browsers require it for Microsoft sign-in off localhost),
#             builds everything fresh from GitHub, waits for the backend, then prints the one-time admin password.
# Later runs: skips all of that and just starts the already-built containers - fast.
#
# Usage:
#   ./run.sh                 start (first time: full install+build; after that: fast start)
#   ./run.sh --rebuild        pull the latest code from GitHub and rebuild, even on a later run
#   ./run.sh --host=IP_OR_DOMAIN   set/replace the address AccessPilot will be reached at (forces a rebuild)
#   ./run.sh --stop           stop the app, keep all data
#   ./run.sh --reset          stop the app and WIPE all data (new admin password next start)

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

MARKER_FILE=".accesspilot-installed"
CONFIG_FILE=".accesspilot-host.env"

REBUILD=0
DO_STOP=0
DO_RESET=0
HOST_ARG=""
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    --stop) DO_STOP=1 ;;
    --reset) DO_RESET=1 ;;
    --host=*) HOST_ARG="${arg#*=}"; REBUILD=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

step() { echo; echo "==> $1"; }
info() { echo "    $1"; }
ok()   { echo "    [OK] $1"; }
warn() { echo "    [!]  $1"; }
err()  { echo "    [ERROR] $1" >&2; }

# ---------------------------------------------------------------- 0. Remember the address from last time

ACCESS_HOST=""
if [ -f "$CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi
if [ -n "$HOST_ARG" ]; then
  ACCESS_HOST="$HOST_ARG"
fi

COMPOSE_FILES=(-f docker-compose.yml)
if [ -n "$ACCESS_HOST" ]; then
  COMPOSE_FILES+=(-f docker-compose.https.yml)
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if sudo -n docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  elif sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  fi
fi
compose() { $DOCKER compose "${COMPOSE_FILES[@]}" "$@"; }

# ---------------------------------------------------------------- shortcuts: stop / reset

if [ "$DO_STOP" = "1" ]; then
  step "Stopping AccessPilot (data kept)"
  compose stop
  exit 0
fi
if [ "$DO_RESET" = "1" ]; then
  warn "This deletes the database and the stored credential-encryption key."
  warn "You will get a NEW one-time admin password next time you start it."
  read -rp "Type YES to continue: " CONFIRM
  if [ "$CONFIRM" != "YES" ]; then info "Cancelled - nothing changed."; exit 0; fi
  compose down -v
  rm -f "$MARKER_FILE" "$CONFIG_FILE" Caddyfile
  ok "Stopped and wiped. Run this script again to start fresh."
  exit 0
fi

# ---------------------------------------------------------------- 1. Docker installed?

step "Checking for Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker is not installed."
  if command -v curl >/dev/null 2>&1; then
    info "Installing Docker via the official convenience script (needs sudo)..."
    curl -fsSL https://get.docker.com | sudo sh
    info "Adding $(whoami) to the docker group so sudo isn't needed next time..."
    sudo usermod -aG docker "$(whoami)" || true
    warn "Log out and back in (or run: newgrp docker) so that takes effect, then run this script again."
    exit 0
  else
    err "curl isn't available. Install Docker by hand: https://docs.docker.com/engine/install/ubuntu/"
    exit 1
  fi
fi
ok "Docker is installed ($(docker --version))"

# ---------------------------------------------------------------- 2. Docker daemon + compose

step "Checking Docker is running"
if ! $DOCKER info >/dev/null 2>&1; then
  err "Docker is installed but not responding. Try: sudo systemctl start docker"
  exit 1
fi
ok "Docker is running"

if ! compose version >/dev/null 2>&1; then
  err "Docker Compose isn't available. Install the plugin: sudo apt install docker-compose-plugin"
  exit 1
fi
ok "Docker Compose is available"

# ---------------------------------------------------------------- 3. How will this be reached?

if [ -z "$ACCESS_HOST" ] && [ ! -f "$CONFIG_FILE" ]; then
  step "How will you reach AccessPilot?"
  info "This is a server, so the frontend needs to know its own real address up front - it gets baked in"
  info "when it's built and can't be changed later without rebuilding."
  info "Enter the IP address or domain name you'll type into your browser (e.g. 192.168.1.50 or"
  info "accesspilot.example.com). Leave this blank ONLY if you'll browse to this server from itself."
  read -rp "Server address (blank = localhost only): " ACCESS_HOST
  if [ -n "$ACCESS_HOST" ]; then
    COMPOSE_FILES+=(-f docker-compose.https.yml)
  fi
fi
printf 'ACCESS_HOST="%s"\n' "$ACCESS_HOST" > "$CONFIG_FILE"

if [ -n "$ACCESS_HOST" ]; then
  export VITE_API_BASE_URL="https://${ACCESS_HOST}"
  export FRONTEND_URL="https://${ACCESS_HOST}"

  step "Setting up HTTPS for $ACCESS_HOST"
  if [[ "$ACCESS_HOST" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    TLS_DIRECTIVE="tls internal"
    info "That's a bare IP address - Let's Encrypt cannot issue a trusted certificate for an IP, so a"
    info "self-signed one will be used instead. Your browser will show a one-time warning to accept it;"
    info "the connection is still fully encrypted, just not from a publicly trusted certificate authority."
  else
    TLS_DIRECTIVE=""
    info "Caddy will automatically obtain a free, trusted HTTPS certificate from Let's Encrypt for"
    info "$ACCESS_HOST. This needs: its DNS already pointing at this server's public IP, and ports 80"
    info "and 443 reachable from the internet (check any cloud firewall / security group too). If that"
    info "isn't ready yet, Caddy serves a temporary self-signed certificate and keeps retrying in the"
    info "background - nothing else is blocked meanwhile."
  fi
  sed -e "s|{{HOST}}|$ACCESS_HOST|" -e "s|{{TLS_DIRECTIVE}}|$TLS_DIRECTIVE|" Caddyfile.template > Caddyfile
  ok "Caddyfile generated"
else
  export VITE_API_BASE_URL="http://localhost:8000"
  export FRONTEND_URL="http://localhost:5173"
fi
info "Will be reachable at: $FRONTEND_URL"

# ---------------------------------------------------------------- 4. First run vs. already installed

FIRST_RUN=0
[ -f "$MARKER_FILE" ] || FIRST_RUN=1

if [ "$FIRST_RUN" = "1" ] || [ "$REBUILD" = "1" ]; then
  if [ "$FIRST_RUN" = "1" ]; then
    step "First time - downloading and building AccessPilot from GitHub"
  else
    step "Pulling the latest code and rebuilding"
  fi
  compose up -d --build
else
  step "Starting AccessPilot"
  compose up -d
fi

# ---------------------------------------------------------------- 5. Wait for the backend

step "Waiting for the backend to be ready"
READY=0
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:8000/health" >/dev/null 2>&1; then READY=1; break; fi
  sleep 3
  printf "."
done
echo
if [ "$READY" = "1" ]; then
  ok "Backend is up"
else
  warn "Still not responding after 3 minutes - check: $DOCKER compose logs -f backend"
fi

# ---------------------------------------------------------------- 6. First-time onboarding

if [ "$FIRST_RUN" = "1" ]; then
  touch "$MARKER_FILE"
  step "First-time setup"
  echo
  echo "  AccessPilot is ready at:  $FRONTEND_URL"
  if [ -n "$ACCESS_HOST" ]; then
    info "(give Caddy a minute to finish obtaining/loading its certificate on the very first request)"
  fi

  BOOTSTRAP_LINE=$(compose logs backend 2>/dev/null | grep "Bootstrap login" || true)
  if [ -n "$BOOTSTRAP_LINE" ]; then
    PASSWORD=$(echo "$BOOTSTRAP_LINE" | grep -oP 'password:\s*\K\S+' || true)
    if [ -n "$PASSWORD" ]; then
      echo "  First sign-in  ->  username: admin   password: $PASSWORD"
      warn "That password is shown ONLY ONCE - copy it now."
    fi
  else
    warn "Could not find the one-time password yet. Run: $DOCKER compose logs backend | grep Bootstrap"
  fi

  echo
  echo "  After signing in, AccessPilot's own setup wizard walks you through connecting your"
  echo "  Microsoft Entra tenant. When registering the app in Entra, add this as a Single-page"
  echo "  application redirect URI:  $FRONTEND_URL"
  echo
else
  ok "AccessPilot is running at $FRONTEND_URL"
fi

echo
info "Useful commands:"
info "  ./run.sh                     start it again any time (fast - no rebuild)"
info "  ./run.sh --rebuild           pull the latest code from GitHub and rebuild"
info "  ./run.sh --host=NEW_ADDRESS  change the address this is reached at (rebuilds)"
info "  ./run.sh --stop              stop it, keep all data"
info "  ./run.sh --reset             wipe everything and start over"
info "  $DOCKER compose logs -f backend   watch backend logs"
if [ -n "$ACCESS_HOST" ]; then
  info "  $DOCKER compose logs -f caddy     watch certificate/proxy logs"
fi
