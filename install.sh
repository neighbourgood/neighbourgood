#!/usr/bin/env bash
# NeighbourGood one-line installer.
#   curl -fsSL https://raw.githubusercontent.com/neighbourgood/neighbourgood/main/install.sh | bash
#
# Uses Docker Compose when Docker is available. Otherwise falls back to a
# native install (Python venv + SQLite backend, npm dev server frontend) so
# people without Docker or git preinstalled can still get running.
set -euo pipefail

REPO_URL="https://github.com/neighbourgood/neighbourgood.git"
TARBALL_URL="https://github.com/neighbourgood/neighbourgood/archive/refs/heads/main.tar.gz"
INSTALL_DIR="${NG_INSTALL_DIR:-$HOME/neighbourgood}"

BOLD='\033[1m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
log()  { printf "${GREEN}==>${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}==>${RESET} %s\n" "$1"; }
err()  { printf "${RED}==>${RESET} %s\n" "$1" >&2; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

need_curl() {
  command_exists curl || { err "curl is required. Install curl and re-run."; exit 1; }
}

fetch_source() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Existing install found at $INSTALL_DIR — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi
  if [ -e "$INSTALL_DIR" ]; then
    err "$INSTALL_DIR already exists and isn't a NeighbourGood git checkout."
    err "Set NG_INSTALL_DIR to another path and re-run, e.g.:"
    err "  NG_INSTALL_DIR=\$HOME/neighbourgood-2 bash install.sh"
    exit 1
  fi

  if command_exists git; then
    log "Cloning into $INSTALL_DIR..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  else
    warn "git not found — downloading a source archive instead."
    need_curl
    mkdir -p "$INSTALL_DIR"
    curl -fsSL "$TARBALL_URL" | tar -xz -C "$INSTALL_DIR" --strip-components=1
  fi
}

prepare_env() {
  cd "$INSTALL_DIR"
  if [ ! -f .env ]; then
    cp .env.example .env
    if command_exists openssl; then
      SECRET="$(openssl rand -hex 32)"
    else
      SECRET="$(head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    echo "NG_SECRET_KEY=$SECRET" >> .env
  fi
}

# pydantic-settings resolves .env relative to the process's cwd (backend/),
# not the repo root where we write it — so the native path has to pass the
# secret explicitly rather than rely on auto-loading, or it silently falls
# back to the insecure default key.
read_env_value() {
  grep "^$1=" "$INSTALL_DIR/.env" | tail -1 | cut -d= -f2-
}

wait_for_status() {
  local url="$1" tries=0
  until curl -fsS "$url" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
      warn "Still waiting on $url — check the logs if this doesn't come up soon."
      return 1
    fi
    sleep 2
  done
}

setup_docker() {
  log "Docker detected — building and starting containers (this can take a few minutes on first run)..."
  cd "$INSTALL_DIR"
  docker compose up --build -d

  log "Waiting for the backend to come up..."
  wait_for_status "http://localhost:8300/status" || true

  print_summary
}

setup_native() {
  warn "Docker not found — setting up a native install instead (SQLite, no containers)."

  if ! command_exists python3; then
    err "python3 not found. Install Python 3.10+ (or Docker) and re-run this script."
    exit 1
  fi
  if ! command_exists npm; then
    err "npm/Node.js not found. Install Node.js 18+ (or Docker) and re-run this script."
    exit 1
  fi

  log "Setting up the backend (Python venv + SQLite)..."
  cd "$INSTALL_DIR/backend"
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --quiet --disable-pip-version-check -r requirements.txt
  NG_DEBUG=true \
    NG_DATABASE_URL="sqlite:///./neighbourgood.db" \
    NG_SECRET_KEY="$(read_env_value NG_SECRET_KEY)" \
    nohup uvicorn app.main:app --host 0.0.0.0 --port 8300 \
    > "$INSTALL_DIR/backend.log" 2>&1 &
  echo $! > "$INSTALL_DIR/.backend.pid"
  deactivate

  log "Setting up the frontend (npm)..."
  cd "$INSTALL_DIR/frontend"
  npm install --silent
  nohup npm run dev -- --host 0.0.0.0 --port 5173 \
    > "$INSTALL_DIR/frontend.log" 2>&1 &
  echo $! > "$INSTALL_DIR/.frontend.pid"

  log "Waiting for the backend to come up..."
  wait_for_status "http://localhost:8300/status" || true

  print_summary --native
}

print_summary() {
  local native=false
  [ "${1:-}" = "--native" ] && native=true

  echo
  printf "${BOLD}NeighbourGood is running.${RESET}\n"
  if $native; then
    echo "  Web app       http://localhost:5173"
    echo "  API           http://localhost:8300"
    echo "  API docs      http://localhost:8300/docs"
    echo
    echo "Logs: $INSTALL_DIR/backend.log, $INSTALL_DIR/frontend.log"
    echo "Stop: kill \$(cat $INSTALL_DIR/.backend.pid) \$(cat $INSTALL_DIR/.frontend.pid)"
    echo
    warn "SQLite + debug mode is meant for trying things out. For a durable or"
    warn "multi-user install, install Docker and re-run this script."
  else
    echo "  Web app       http://localhost:3800"
    echo "  API           http://localhost:8300"
    echo "  API docs      http://localhost:8300/docs"
    echo
    echo "Logs: docker compose logs -f   (run inside $INSTALL_DIR)"
    echo "Stop: docker compose down      (run inside $INSTALL_DIR)"
  fi
  echo
  echo "Installed to: $INSTALL_DIR"
}

main() {
  need_curl
  fetch_source
  prepare_env

  if command_exists docker && docker compose version >/dev/null 2>&1; then
    setup_docker
  else
    setup_native
  fi
}

main "$@"
