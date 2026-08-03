#!/usr/bin/env bash
# Deployment helper for rebot-copilot-camera on the reComputer R2x.
#
# Subcommand names are carried over from the previous generation of this
# service on purpose: the muscle memory is worth more than a tidier vocabulary.
#
#   ./manage.sh setup    one-time: uv, systemd units, CAN, permissions
#   ./manage.sh enable    start on boot
#   ./manage.sh push      build frontend, rsync, restart
#   ./manage.sh logs      tail journalctl
#   ./manage.sh open      SSH tunnel + browser
#   ./manage.sh run       foreground, for print/breakpoint debugging
#   ./manage.sh status    is it up, and is it on the real arm

set -euo pipefail

HOST="${REBOT_HOST_SSH:-recomputer@r2x}"
REMOTE_DIR="${REBOT_REMOTE_DIR:-/opt/rebot-copilot-camera}"
PORT="${REBOT_PORT:-18790}"
SERVICE=rebot-copilot-camera
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "error: $*" >&2; exit 1; }
step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

build_frontend() {
  step "building frontend"
  [ -d "$HERE/frontend/node_modules" ] || (cd "$HERE/frontend" && npm install)
  (cd "$HERE/frontend" && npm run build)
}

cmd_setup() {
  step "installing uv"
  ssh "$HOST" 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'

  step "creating $REMOTE_DIR"
  ssh "$HOST" "sudo mkdir -p $REMOTE_DIR && sudo chown \$(whoami): $REMOTE_DIR"

  step "installing systemd units"
  scp "$HERE/deploy/$SERVICE.service" "$HERE/deploy/rebot-can.service" "$HOST:/tmp/"
  ssh "$HOST" "sudo mv /tmp/$SERVICE.service /tmp/rebot-can.service /etc/systemd/system/ && sudo systemctl daemon-reload"

  step "installing udev rules"
  scp "$HERE/deploy/99-rebot-usb.rules" "$HOST:/tmp/"
  ssh "$HOST" 'sudo mv /tmp/99-rebot-usb.rules /etc/udev/rules.d/ && sudo udevadm control --reload'

  # Without systemd-journal membership /api/logs comes back empty, with no
  # error to explain why. Same trap as the previous service.
  step "granting journal and serial access"
  ssh "$HOST" 'sudo usermod -aG systemd-journal,dialout $(whoami)'

  echo
  echo "Done. Log out and back in for the group changes to apply, then:"
  echo "  ./manage.sh push && ./manage.sh enable"
}

cmd_push() {
  build_frontend

  step "syncing to $HOST:$REMOTE_DIR"
  # --delete keeps the remote clean, but routines/ is operator data that only
  # exists on the device and must never be deleted by a deploy. Patterns
  # without a leading / match at any depth, so '/routines/' is anchored —
  # otherwise backend/routines/ (a Python package) would be excluded too.
  rsync -az --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '/routines/' \
    --exclude '.pio/' \
    --filter 'protect /routines/' \
    "$HERE/" "$HOST:$REMOTE_DIR/"

  step "syncing the vendored arm layer"
  # rsync skips submodule contents, so it is sent explicitly rather than left
  # as an empty directory that fails at import.
  rsync -az --delete "$HERE/vendor/reBotArm_control_py/" \
    "$HOST:$REMOTE_DIR/vendor/reBotArm_control_py/"

  step "installing dependencies"
  ssh "$HOST" "cd $REMOTE_DIR && ~/.local/bin/uv sync"

  step "restarting"
  ssh "$HOST" "sudo systemctl restart $SERVICE"
  sleep 2
  cmd_status
}

cmd_enable() {
  ssh "$HOST" "sudo systemctl enable --now rebot-can.service $SERVICE"
  cmd_status
}

cmd_logs() {
  ssh -t "$HOST" "journalctl -u $SERVICE -f -n 200 --output=cat"
}

cmd_status() {
  step "service"
  ssh "$HOST" "systemctl is-active $SERVICE || true"

  step "health"
  # Reports whether the real arm was found. A service happily running on the
  # simulator is the failure this exists to catch.
  ssh "$HOST" "curl -s http://127.0.0.1:$PORT/api/health" | python3 -m json.tool 2>/dev/null || echo "(no response)"
}

cmd_open() {
  step "tunnelling $PORT and opening a browser"
  ssh -f -N -L "$PORT:127.0.0.1:$PORT" "$HOST"
  sleep 1
  case "$(uname)" in
    Darwin) open "http://127.0.0.1:$PORT" ;;
    *) xdg-open "http://127.0.0.1:$PORT" ;;
  esac
}

cmd_run() {
  step "stopping the service and running in the foreground"
  # LANG set explicitly: without it journalctl and the terminal disagree about
  # Chinese routine names and prints come out as ?.
  ssh -t "$HOST" "sudo systemctl stop $SERVICE; cd $REMOTE_DIR && LANG=zh_CN.UTF-8 ~/.local/bin/uv run -m backend.app ${*:-}"
}

case "${1:-}" in
  setup) cmd_setup ;;
  push) cmd_push ;;
  enable) cmd_enable ;;
  logs) cmd_logs ;;
  status) cmd_status ;;
  open) cmd_open ;;
  run) shift; cmd_run "$@" ;;
  *) die "usage: $0 {setup|push|enable|logs|status|open|run}" ;;
esac
