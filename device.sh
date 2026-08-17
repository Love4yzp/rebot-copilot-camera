#!/usr/bin/env bash
# 设备部署与运维脚本 (device.sh)
# 作用：通过 SSH 远程把代码部署到机械臂工控机（如 reComputer）并进行运维管理。
#
# 前置条件：
#   使用前需先设置远程设备 SSH 连接地址：
#   export REBOT_HOST_SSH=recomputer@192.168.1.10
#
# 常用子命令：
#   ./device.sh push     一键部署：在电脑上打包前端 -> 增量同步代码到设备 -> 重启服务
#   ./device.sh setup    一次性初始化设备环境（安装 uv、systemd 服务、udev 硬件规则）
#   ./device.sh status   检查设备服务状态，并确认是否成功连上物理机械臂（非模拟器）
#   ./device.sh open     创建 SSH 端口转发隧道并自动打开本地浏览器访问设备界面
#   ./device.sh logs     实时查看设备上的后台服务日志 (journalctl)
#   ./device.sh run      临时停止后台服务并在终端前台运行，方便 print/breakpoint 调试
#   ./device.sh enable   设置设备开机自动启动机械臂服务

set -euo pipefail

# HOST 不设默认值：公有仓库中硬编码 SSH 别名只能在一台电脑生效。必须显式设 REBOT_HOST_SSH
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

# usage 提取文件开头的注释作为 --help 输出（遇到第一个空行截止），避免维护多份文档
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

	# 如果不把用户加入 systemd-journal 组，/api/logs 端点会静默返回空列表且无任何错误提示
	step "[r2x] 授予 journal 与串口权限"
	ssh "$HOST" 'sudo usermod -aG systemd-journal,dialout $(whoami)'

	echo
	echo "完成。权限组要退出重新登录才生效，然后："
	echo "  ./device.sh push && ./device.sh enable"
}

cmd_push() {
	# 前端构建属于开发机本地工作，统一调 ./dev.sh build，避免两端构建规则漂移
	"$HERE/dev.sh" build

	step "[r2x] 同步到 $HOST:$REMOTE_DIR"
	# --delete 保证远端代码干净；但必须保护数据目录（包含操作员现场示教出来的点位数据，绝不能删）。
	# 带前导斜杠挂在根路径锚点上，防止误杀路径中同名的 Python 包目录。
	rsync -az --delete \
		--exclude '.git/' \
		--exclude '.venv/' \
		--exclude 'node_modules/' \
		--exclude '__pycache__/' \
		--exclude '/data/' \
		--exclude '.pio/' \
		--filter 'protect /data/' \
		"$HERE/" "$HOST:$REMOTE_DIR/"

	step "[r2x] 同步 vendored 臂层"
	# rsync 默认跳过子模块内容，因此显式同步子模块，避免远端 import 报错
	rsync -az --delete "$HERE/vendor/reBotArm_control_py/" \
		"$HOST:$REMOTE_DIR/vendor/reBotArm_control_py/"

	step "[r2x] 安装依赖"
	ssh "$HOST" "cd $REMOTE_DIR && ~/.local/bin/uv sync"

	step "[r2x] 重启服务"
	ssh "$HOST" "sudo systemctl restart $SERVICE"
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
	# 轮询 /api/health 确认真实机械臂是否成功连接，专门拦截“硬件断连后静默退回模拟器”的隐蔽故障。
	# 设置 30 秒超时：重启后后端初始化需要时间，避免因服务尚未就绪而发生误报。
	local deadline=$((SECONDS + 30))
	local body=""
	while ((SECONDS < deadline)); do
		body="$(ssh "$HOST" "curl -s http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)"
		if [[ -n "$body" ]]; then
			echo "$body" | python3 -m json.tool 2>/dev/null && return 0
		fi
		sleep 1
	done
	echo "(no response after 30s)"
	return 1
}

cmd_open() {
	step "[r2x] 建隧道 $PORT 并打开浏览器"
	# ExitOnForwardFailure=yes：若本地端口被占用则立即退出，防止端口转发失败导致浏览器打开空白页
	ssh -f -N -o ExitOnForwardFailure=yes -L "$PORT:127.0.0.1:$PORT" "$HOST"
	sleep 1
	case "$(uname)" in
	Darwin) open "http://127.0.0.1:$PORT" ;;
	*) xdg-open "http://127.0.0.1:$PORT" ;;
	esac
}

cmd_run() {
	step "[r2x] 停服务并前台运行"
	# 显式设置 LANG=zh_CN.UTF-8：防止系统默认 LANG=C 导致终端与 journalctl 中文序列名打出 ? 乱码
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
