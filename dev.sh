#!/usr/bin/env bash
# 本机开发与启动脚本 (dev.sh)
# 作用：本机唯一启动入口。后端由人起，不要另开一份 uv run -m backend.app。
#
# 启动后端（无子命令的 flag 直接进这里）：
#   ./dev.sh sim               全栈 + 模拟臂（日常联调；旧写法 ./dev.sh --sim 仍可用）
#   ./dev.sh prod              全栈 + 真臂（连不上就拒绝启动，不会静默退回模拟器）
#   ./dev.sh --no-build        跳过前端构建，直接起后端（需已构建过一次）
#   ./dev.sh --local           只绑 127.0.0.1，不对局域网开放
#   ./dev.sh --host <ip>       监听地址（默认 0.0.0.0，启动横幅会列出可访问地址）
#   ./dev.sh --port <port>     端口（默认 18790，可用 REBOT_PORT）
#
# 其它：
#   ./dev.sh ui                只起前端 mock（无 Python、无后端，调界面）
#   ./dev.sh build             只构建前端，产物进 app/backend/static/
#   ./dev.sh status            看 :18790 上是谁（simulated 与否）
#   ./dev.sh --help
#
# 快捷示例：
#   ./dev.sh sim               # 本机无硬件全栈联调
#   ./dev.sh prod              # 本机连接真实机械臂
#   ./dev.sh ui                # 只调前端
#   ./dev.sh status            # 端口被占时先看这个

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 应用代码整体住在 app/ 二级目录，顶层只留脚本与 AI/人读文档
APP="$HERE/app"
PORT="${REBOT_PORT:-18790}"

die() {
	echo "error: $*" >&2
	exit 1
}
step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
note() { echo "note: $*" >&2; }

# usage 提取文件开头的注释作为 --help 输出（遇到第一个空行截止），避免重复维护帮助文本
usage() {
	awk 'NR > 1 && /^$/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"
}

# 检查机械臂控制库 Submodule。如果 app/vendor/ 为空，clone 时不会报错，但在 Python import 时会失败；
# 在 sim 模式下表现为 3D 视图中没有臂（看起来像渲染 Bug，实际上是未拉取子模块）。
require_submodule() {
	[ -f "$APP/vendor/reBotArm_control_py/pyproject.toml" ] ||
		die "app/vendor/reBotArm_control_py is empty — run: git submodule update --init"
}

npm_install() {
	[ -d "$APP/frontend/node_modules" ] || (cd "$APP/frontend" && npm install)
}

# 与 backend.app 一致：显式 --host > --local > REBOT_HOST / 默认 0.0.0.0
parse_listen() {
	LISTEN_HOST="${REBOT_HOST:-0.0.0.0}"
	LISTEN_PORT="$PORT"
	local prev="" host_set=0 local_only=0
	for a in "$@"; do
		case "$prev" in
		--host)
			LISTEN_HOST="$a"
			host_set=1
			;;
		--port) LISTEN_PORT="$a" ;;
		esac
		case "$a" in
		--host=*)
			LISTEN_HOST="${a#--host=}"
			host_set=1
			;;
		--port=*) LISTEN_PORT="${a#--port=}" ;;
		--local) local_only=1 ;;
		esac
		prev="$a"
	done
	if [ "$host_set" = 0 ] && [ "$local_only" = 1 ]; then
		LISTEN_HOST=127.0.0.1
	fi
}

# 在 uv sync / 前端构建之前先占口失败：慢步骤跑完再报「端口被占」是在浪费时间。
# Python 的 _ensure_port_free 仍会在连 CAN 前再检一次，这里不能替代、也不能关掉。
ensure_listen_free() {
	command -v python3 >/dev/null || return 0
	if python3 - "$LISTEN_HOST" "$LISTEN_PORT" <<'PY'
import socket, sys

host, port = sys.argv[1], int(sys.argv[2])
probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    probe.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    probe.close()
PY
	then
		return 0
	fi
	die "$LISTEN_HOST:$LISTEN_PORT 已被占用 — 另一个实例已经在管这条臂。先 ./dev.sh status，不要起第二个。"
}

cmd_build() {
	step "[本机] 构建前端"
	npm_install
	# 构建产物输出到 app/backend/static/，由 backend/app.py 挂载托管
	(cd "$APP/frontend" && npm run build)
}

cmd_status() {
	parse_listen "$@"
	local url="http://127.0.0.1:$LISTEN_PORT/api/health"
	local body=""
	body="$(curl -sS --max-time 2 "$url" 2>/dev/null || true)"
	if [ -z "$body" ] && [ "$LISTEN_HOST" != "0.0.0.0" ] && [ "$LISTEN_HOST" != "127.0.0.1" ]; then
		url="http://$LISTEN_HOST:$LISTEN_PORT/api/health"
		body="$(curl -sS --max-time 2 "$url" 2>/dev/null || true)"
	fi
	if [ -z "$body" ]; then
		echo "nothing on :$LISTEN_PORT"
		echo "  start with:  ./dev.sh sim"
		echo "  real arm:    ./dev.sh prod"
		return 1
	fi
	if command -v python3 >/dev/null; then
		python3 -c '
import json, sys
body = json.load(sys.stdin)
mode = body.get("mode", "?")
arm = (body.get("arm") or {}).get("simulated")
shutter = (body.get("shutter") or {}).get("simulated")
print(f"listening :{sys.argv[1]}  mode={mode}  arm.simulated={arm}  shutter.simulated={shutter}")
print(json.dumps(body, indent=2, ensure_ascii=False))
' "$LISTEN_PORT" <<<"$body"
	else
		echo "$body"
	fi
}

cmd_serve() {
	require_submodule
	command -v uv >/dev/null || die "uv not found — https://astral.sh/uv"

	parse_listen "$@"
	ensure_listen_free

	step "[本机] 安装 Python 依赖"
	(cd "$APP" && uv sync)

	if [ "${NO_BUILD:-}" = "1" ]; then
		[ -f "$APP/backend/static/index.html" ] ||
			die "--no-build given but app/backend/static/ is empty"
		step "[本机] 跳过前端构建（--no-build）"
	else
		# 默认每次启动都重新构建前端：防止出现“后端更新了 API，但前端展示旧网页”的情况，
		# 那样接口正确而页面显示错误，极难排查。
		cmd_build
	fi

	step "[本机] 启动后端：$LISTEN_HOST:$LISTEN_PORT"
	# 硬件连接机制：
	# 1. 未加 --sim 时，机械臂连不上就拒绝启动（ArmUnavailable），不会静默退回模拟器；
	# 2. 快门连不上绝不退回模拟快门（防止假快门谎报成功）。启动后请核对日志或 ./dev.sh status。
	#
	# macOS 的 CAN 传输走 MacCAN 的 libPCBUSB.dylib（用户态驱动，装在 ~/.local/lib）。
	# dyld 的裸名搜索不含这个目录，而 DYLD_* 只在进程启动时读取，所以必须在 exec 前注入。
	if [ "$(uname)" = "Darwin" ] && [ -f "$HOME/.local/lib/libPCBUSB.dylib" ]; then
		export DYLD_FALLBACK_LIBRARY_PATH="$HOME/.local/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"
	fi
	echo "  本地访问 http://127.0.0.1:$LISTEN_PORT （局域网/NetBird 地址见下方启动横幅）"
	# 注意不能用 `exec env LANG=... uv run ...`：/usr/bin/env 是 SIP 保护二进制，
	# exec 它时 dyld 会剥掉 DYLD_* 环境变量，上面的 PCBUSB 搜索路径就传不下去。
	export LANG="${LANG:-zh_CN.UTF-8}"
	# uv 项目根在 app/（pyproject.toml 所在），必须先 cd 进去再 exec
	cd "$APP"
	exec uv run -m backend.app "$@"
}

serve_from_cli() {
	NO_BUILD=0
	local args=()
	local a
	for a in "$@"; do
		case "$a" in
		--no-build) NO_BUILD=1 ;;
		*) args+=("$a") ;;
		esac
	done
	export NO_BUILD
	cmd_serve ${args+"${args[@]}"}
}

cmd_ui() {
	require_submodule

	step "[本机] 启动前端 mock（内存 API，无后端）"
	# `vite --mode mock` 关闭后端代理，由 app/frontend/mock/ 在内存中响应 API 和 WebSocket，
	# 并直接读取 submodule URDF。无需 Python 环境，亦不连接真实机械臂。
	npm_install
	cd "$APP/frontend"
	exec npm run dev:mock -- "$@"
}

case "${1:-}" in
help | -h | --help)
	usage
	exit 0
	;;
"")
	usage >&2
	echo >&2
	echo "hint: ./dev.sh sim" >&2
	exit 1
	;;
build) cmd_build ;;
status)
	shift
	cmd_status "$@"
	;;
ui)
	shift
	cmd_ui "$@"
	;;
mock)
	note "'./dev.sh mock' → './dev.sh ui'"
	shift
	cmd_ui "$@"
	;;
sim)
	# 全栈 + 模拟臂。历史上 `sim` 曾被刻意指到前端 mock（防手滑少打横杠起了个没后端的东西），
	# 现在 sim 正式成为子命令，那个陷阱别名删除；flag 写法 --sim 仍透传可用。
	shift
	serve_from_cli "$@" --sim
	;;
prod)
	shift
	serve_from_cli "$@"
	;;
-*)
	serve_from_cli "$@"
	;;
*)
	usage >&2
	exit 1
	;;
esac
