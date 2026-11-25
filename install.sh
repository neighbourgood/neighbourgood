#!/usr/bin/env bash
# NeighbourGood one-line installer.
#   curl -fsSL https://raw.githubusercontent.com/neighbourgood/neighbourgood/main/install.sh | bash
#
# Optional WhatsApp bot (asked separately at the end, or added later):
#   curl -fsSL https://raw.githubusercontent.com/neighbourgood/neighbourgood/main/install.sh | bash -s -- --whatsapp
#   NG_WHATSAPP=0 skips the question, NG_WHATSAPP=1 says yes without asking.
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

# Only needed when the images can't be pulled and are built on the server
# instead: the frontend build alone peaks at about 1 GB, and on a small VPS
# without swap the kernel kills it halfway. Below 2 GB of RAM + swap, add a
# 2 GB swap file and keep it across reboots. Opt out with NG_NO_SWAP=1.
SWAP_FILE="/swapfile"
SWAP_MIN_KB=$((2 * 1024 * 1024))
SWAP_SIZE_MB=2048

ensure_swap() {
  [ "${NG_NO_SWAP:-}" = "1" ] && return
  [ -r /proc/meminfo ] || return  # not Linux

  local mem_kb swap_kb
  mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  swap_kb="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"
  [ $((mem_kb + swap_kb)) -ge "$SWAP_MIN_KB" ] && return

  local manual="Add 1–2 GB of swap yourself before updating (see docs/self-hosting.md), or the build may be stopped halfway."
  if [ -e "$SWAP_FILE" ]; then
    warn "Less than 2 GB of memory + swap, and $SWAP_FILE already exists, so it is left alone."
    warn "$manual"
    return
  fi

  local sudo=""
  if [ "$(id -u)" -ne 0 ]; then
    if ! command_exists sudo; then
      warn "Less than 2 GB of memory + swap, and no sudo to add a swap file. $manual"
      return
    fi
    sudo="sudo"
  fi

  local avail_kb
  avail_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
  if [ "$avail_kb" -lt $(((SWAP_SIZE_MB + 1024) * 1024)) ]; then
    warn "Less than 2 GB of memory + swap, and not enough free disk for a swap file. $manual"
    return
  fi

  log "Less than 2 GB of memory + swap — adding a 2 GB swap file at $SWAP_FILE so the build doesn't run out of memory..."
  if ! { $sudo fallocate -l "${SWAP_SIZE_MB}M" "$SWAP_FILE" 2>/dev/null \
           || $sudo dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$SWAP_SIZE_MB" status=none; } \
     || ! $sudo chmod 600 "$SWAP_FILE" \
     || ! $sudo mkswap "$SWAP_FILE" >/dev/null \
     || ! $sudo swapon "$SWAP_FILE"; then
    # Some containers and VPS types (OpenVZ, LXC) don't allow swap at all
    $sudo rm -f "$SWAP_FILE"
    warn "Couldn't enable a swap file here. $manual"
    return
  fi
  if ! grep -qs "^$SWAP_FILE " /etc/fstab; then
    echo "$SWAP_FILE none swap sw 0 0" | $sudo tee -a /etc/fstab >/dev/null
  fi
  log "Swap file active and kept across reboots (remove it with: swapoff $SWAP_FILE, delete its line in /etc/fstab, rm $SWAP_FILE)."
}

setup_docker() {
  cd "$INSTALL_DIR"
  log "Docker detected — downloading the ready-made images..."
  if docker compose pull; then
    docker compose up -d
  else
    # No published images reachable (no release yet, a fork, no access to
    # ghcr.io): build them from this checkout instead
    warn "Couldn't download the images — building them on this server instead (this can take a few minutes)."
    ensure_swap
    docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
  fi

  log "Waiting for the backend to come up..."
  wait_for_status "http://localhost:8300/status" || true

  print_summary
  setup_whatsapp docker
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
  setup_whatsapp native
}

random_hex() {
  if command_exists openssl; then
    openssl rand -hex 32
  else
    head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# Set KEY=VALUE in .env, replacing an existing line (portable: no sed -i)
set_env_value() {
  local key="$1" value="$2" env_file="$INSTALL_DIR/.env"
  if grep -q "^$key=" "$env_file"; then
    grep -v "^$key=" "$env_file" > "$env_file.tmp"
    mv "$env_file.tmp" "$env_file"
  fi
  echo "$key=$value" >> "$env_file"
}

# ── Optional WhatsApp bot ─────────────────────────────────────────────
# A separate step on purpose: the bridge is unofficial, needs a spare phone
# number, and WhatsApp may block that number. Nothing changes unless the
# person says yes here (or passes --whatsapp / NG_WHATSAPP=1).
want_whatsapp() {
  case "${NG_WHATSAPP:-}" in
    1|y|yes) return 0 ;;
    0|n|no) return 1 ;;
  esac
  # Already enabled by an earlier run: keep it
  [ "$(read_env_value COMPOSE_PROFILES)" = "whatsapp" ] && return 0
  # curl | bash has no stdin to answer with; ask on the terminal if there is one
  { : < /dev/tty; } 2>/dev/null || return 1

  echo
  printf "${BOLD}Optional: WhatsApp bot${RESET}\n"
  echo "  Lets neighbours share, borrow and ask for help from their WhatsApp group,"
  echo "  without opening the app. It runs an unofficial WhatsApp Web bridge (WAHA)"
  echo "  next to NeighbourGood:"
  echo "   - you need a spare phone number for the bot, not your personal one"
  echo "   - this is against WhatsApp's terms of service; the number can get blocked"
  echo "   - you pair the number afterwards in the app: Settings > WhatsApp bridge"
  echo "  Details: docs/whatsapp-setup.md"
  printf "Set up the WhatsApp bot now? [y/N] "
  local answer=""
  read -r answer < /dev/tty || return 1
  case "$answer" in
    y|Y|yes|Yes|j|J|ja|Ja) return 0 ;;
  esac
  return 1
}

setup_whatsapp() {
  local mode="$1"
  if ! want_whatsapp; then
    log "WhatsApp bot not set up. Add it any time with: bash install.sh --whatsapp"
    return
  fi
  if [ "$mode" != "docker" ]; then
    warn "The WhatsApp bridge runs as a Docker container. Install Docker and run: bash install.sh --whatsapp"
    warn "Or run WAHA yourself and set NG_WHATSAPP_* in .env (see docs/whatsapp-setup.md)."
    return
  fi

  cd "$INSTALL_DIR"
  log "Setting up the WhatsApp bridge..."
  [ -n "$(read_env_value NG_WHATSAPP_API_KEY)" ] || set_env_value NG_WHATSAPP_API_KEY "$(random_hex)"
  [ -n "$(read_env_value NG_WHATSAPP_WEBHOOK_SECRET)" ] || set_env_value NG_WHATSAPP_WEBHOOK_SECRET "$(random_hex)"
  set_env_value NG_WHATSAPP_BRIDGE_URL "http://waha:3000"
  set_env_value COMPOSE_PROFILES "whatsapp"
  # Starts the bridge and recreates the backend so it picks up the new settings
  docker compose up -d

  echo
  printf "${BOLD}WhatsApp bridge is running.${RESET}\n"
  echo "  1. Log in to NeighbourGood with an admin account (NG_ADMIN_EMAILS in .env)."
  echo "  2. Settings > WhatsApp bridge > Start pairing, then scan the QR code with"
  echo "     WhatsApp on the bot's phone (Linked devices)."
  echo "  3. In a community's Admin tab, create a link code and post it in your group."
  echo "  Turn it off again: remove COMPOSE_PROFILES and NG_WHATSAPP_BRIDGE_URL from .env,"
  echo "  then run: docker compose --profile whatsapp rm -sf waha && docker compose up -d"
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
  for arg in "$@"; do
    case "$arg" in
      --whatsapp) NG_WHATSAPP=1 ;;
      --no-whatsapp) NG_WHATSAPP=0 ;;
    esac
  done
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
