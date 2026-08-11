#!/usr/bin/env bash
# device.sh —— 部署脚本：每条命令都经 ssh 落到设备上执行。
# 在你坐着的这台机器上起服务是 ./dev.sh —— 两个脚本的分界不是「做什么」，
# 而是代码在哪台机器上执行。
#
# 子命令沿用上一代服务的名字：肌肉记忆比更整齐的词汇表值钱。
#
#   ./device.sh setup    一次性：uv、systemd、CAN、udev、权限组
#   ./device.sh enable   开机自启
#   ./device.sh push     构建前端 + rsync + 重启
#   ./device.sh logs     tail journalctl
#   ./device.sh open     SSH 隧道 + 开浏览器
#   ./device.sh run      设备上前台跑，调 print/breakpoint 用
#   ./device.sh status   在不在跑，跑在真臂还是模拟器上

set -euo pipefail

# No default for HOST: this is a public repo, and a baked-in SSH alias only
# resolves on one machine.
HOST="${REBOT_HOST_SSH:-}"
REMOTE_DIR="${REBOT_REMOTE_DIR:-/opt/rebot-copilot-camera}"
PORT="${REBOT_PORT:-18790}"
SERVICE=rebot-copilot-camera
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
	echo "error: $*" >&2
	exit 1
}
step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

# Help text is the header comment at the top of this file — a second copy here
# would drift, and help text that describes the wrong subcommands is worse
# than none.
usage() {
	awk 'NR > 1 && /^$/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"
}

cmd_setup() {
	step "[r2x] 安装 uv"
	ssh "$HOST" 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'

	step "[r2x] 创建 $REMOTE_DIR"
	ssh "$HOST" "sudo mkdir -p $REMOTE_DIR && sudo chown \$(whoami): $REMOTE_DIR"

	step "[r2x] 安装 systemd unit"
	scp "$HERE/deploy/$SERVICE.service" "$HERE/deploy/rebot-can.service" "$HOST:/tmp/"
	ssh "$HOST" "sudo mv /tmp/$SERVICE.service /tmp/rebot-can.service /etc/systemd/system/ && sudo systemctl daemon-reload"

	step "[r2x] 安装 udev 规则"
	scp "$HERE/deploy/99-rebot-usb.rules" "$HOST:/tmp/"
	ssh "$HOST" 'sudo mv /tmp/99-rebot-usb.rules /etc/udev/rules.d/ && sudo udevadm control --reload'

	# Without systemd-journal membership /api/logs comes back empty, with no
	# error to explain why. Same trap as the previous service.
	step "[r2x] 授予 journal 与串口权限"
	ssh "$HOST" 'sudo usermod -aG systemd-journal,dialout $(whoami)'

	echo
	echo "完成。权限组要退出重新登录才生效，然后："
	echo "  ./device.sh push && ./device.sh enable"
}

cmd_push() {
	# Building is local work, so it belongs to the local launcher. Keeping a
	# second copy here would drift from it, and a drifted build step still
	# produces a working bundle — just not the one you meant to deploy.
	"$HERE/dev.sh" build

	step "[r2x] 同步到 $HOST:$REMOTE_DIR"
	# --delete keeps the remote clean, but the data directories are operator
	# work that only exists on the device and must never be deleted by a deploy.
	# routines/ is the v1 migration source — kept as the backup. Patterns
	# without a leading / match at any depth, so each is anchored — otherwise
	# backend/routines/ (a Python package) would be excluded too.
	rsync -az --delete \
		--exclude '.git/' \
		--exclude '.venv/' \
		--exclude 'node_modules/' \
		--exclude '__pycache__/' \
		--exclude '/routines/' \
		--exclude '/poses/' \
		--exclude '/sequences/' \
		--exclude '/templates/' \
		--exclude '.pio/' \
		--filter 'protect /routines/' \
		--filter 'protect /poses/' \
		--filter 'protect /sequences/' \
		--filter 'protect /templates/' \
		"$HERE/" "$HOST:$REMOTE_DIR/"

	step "[r2x] 同步 vendored 臂层"
	# rsync skips submodule contents, so it is sent explicitly rather than left
	# as an empty directory that fails at import.
	rsync -az --delete "$HERE/vendor/reBotArm_control_py/" \
		"$HOST:$REMOTE_DIR/vendor/reBotArm_control_py/"

	step "[r2x] 安装依赖"
	ssh "$HOST" "cd $REMOTE_DIR && ~/.local/bin/uv sync"

	step "[r2x] 重启服务"
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
	step "[r2x] 服务"
	ssh "$HOST" "systemctl is-active $SERVICE || true"

	step "[r2x] 健康检查"
	# Reports whether the real arm was found. A service happily running on the
	# simulator is the failure this exists to catch.
	ssh "$HOST" "curl -s http://127.0.0.1:$PORT/api/health" | python3 -m json.tool 2>/dev/null || echo "(no response)"
}

cmd_open() {
	step "[r2x] 建隧道 $PORT 并打开浏览器"
	ssh -f -N -L "$PORT:127.0.0.1:$PORT" "$HOST"
	sleep 1
	case "$(uname)" in
	Darwin) open "http://127.0.0.1:$PORT" ;;
	*) xdg-open "http://127.0.0.1:$PORT" ;;
	esac
}

cmd_run() {
	step "[r2x] 停服务并前台运行"
	# LANG set explicitly: without it journalctl and the terminal disagree about
	# Chinese routine names and prints come out as ?.
	ssh -t "$HOST" "sudo systemctl stop $SERVICE; cd $REMOTE_DIR && LANG=zh_CN.UTF-8 ~/.local/bin/uv run -m backend.app ${*:-}"
}

case "${1:-}" in
help | -h | --help)
	usage
	exit 0
	;;
"")
	usage >&2
	exit 1
	;;
esac

[[ -n "$HOST" ]] || die "REBOT_HOST_SSH is not set — point it at your device (e.g. export REBOT_HOST_SSH=recomputer@192.168.1.10)"

case "$1" in
setup) cmd_setup ;;
push) cmd_push ;;
enable) cmd_enable ;;
logs) cmd_logs ;;
status) cmd_status ;;
open) cmd_open ;;
run)
	shift
	cmd_run "$@"
	;;
*)
	usage >&2
	exit 1
	;;
esac
