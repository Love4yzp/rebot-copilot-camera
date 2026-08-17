#!/usr/bin/env bash
# 本机开发与启动脚本 (dev.sh)
# 作用：在开发机本地编译前端、启动前端模拟器或启动全栈服务。
#
# 常用命令：
#   ./dev.sh sim               只启动前端（纯内存 Mock，无需 Python 和机械臂，用于调界面）
#   ./dev.sh prod              全栈启动：自动构建前端并启动 Python 后端 (默认连接真实硬件)
#   ./dev.sh prod --sim        全栈启动：构建前端并启动 Python 后端 (强行使用模拟臂)
#   ./dev.sh prod --no-build   全栈启动：跳过前端构建，直接启动 Python 后端
#   ./dev.sh build             仅构建前端静态页面，产物输出至 app/backend/static/
#
# 常用参数 (用于 prod 模式)：
#   --host <ip>                指定监听地址 (默认: 127.0.0.1)
#   --port <port>              指定端口 (默认: 18790)
#   --sim                      强行以模拟模式运行 (不连接物理机械臂与快门)
#   --no-build                 跳过前端编译步骤
#
# 快捷示例：
#   ./dev.sh sim               # 前端 UI 开发
#   ./dev.sh prod --sim        # 本机无硬件全栈联调
#   ./dev.sh prod              # 本机连接真实机械臂运行

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

cmd_build() {
	step "[本机] 构建前端"
	npm_install
	# 构建产物输出到 app/backend/static/，由 backend/app.py 挂载托管
	(cd "$APP/frontend" && npm run build)
}

cmd_prod() {
	require_submodule
	command -v uv >/dev/null || die "uv not found — https://astral.sh/uv"

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

	# 从命令行参数中解析 --host / --port，确保启动提示打印的地址与实际绑定的端口一致
	local host="${REBOT_HOST:-127.0.0.1}" port="$PORT" prev=""
	for a in "$@"; do
		case "$prev" in
		--host) host="$a" ;;
		--port) port="$a" ;;
		esac
		case "$a" in
		--host=*) host="${a#--host=}" ;;
		--port=*) port="${a#--port=}" ;;
		esac
		prev="$a"
	done

	step "[本机] 启动后端：$host:$port"
	# 硬件连接机制：
	# 1. 未加 --sim 时，机械臂连接失败会自动退回模拟器，并在启动日志中说明；
	# 2. 快门连不上绝不退回模拟快门（防止假快门谎报成功）。启动后请核对日志或 /api/health。
	#
	# macOS 的 CAN 传输走 MacCAN 的 libPCBUSB.dylib（用户态驱动，装在 ~/.local/lib）。
	# dyld 的裸名搜索不含这个目录，而 DYLD_* 只在进程启动时读取，所以必须在 exec 前注入。
	if [ "$(uname)" = "Darwin" ] && [ -f "$HOME/.local/lib/libPCBUSB.dylib" ]; then
		export DYLD_FALLBACK_LIBRARY_PATH="$HOME/.local/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"
	fi
	echo "  http://$host:$port"
	# 注意不能用 `exec env LANG=... uv run ...`：/usr/bin/env 是 SIP 保护二进制，
	# exec 它时 dyld 会剥掉 DYLD_* 环境变量，上面的 PCBUSB 搜索路径就传不下去。
	export LANG="${LANG:-zh_CN.UTF-8}"
	# uv 项目根在 app/（pyproject.toml 所在），必须先 cd 进去再 exec
	cd "$APP"
	exec uv run -m backend.app "$@"
}

cmd_sim() {
	require_submodule

	step "[本机] 启动 sim 前端（内存 mock，无后端）"
	# `vite --mode mock` 关闭后端代理，由 app/frontend/mock/ 在内存中响应 API 和 WebSocket，
	# 并直接读取 submodule URDF。无需 Python 环境，亦不连接真实机械臂。
	npm_install
	cd "$APP/frontend"
	exec npm run dev:mock -- "$@"
}

deprecated() {
	echo "warning: './dev.sh $1' 已更名为 './dev.sh $2'，别名过渡期后移除" >&2
}

case "${1:-}" in
prod)
	shift
	NO_BUILD=0
	args=()
	for a in "$@"; do
		case "$a" in
		--no-build) NO_BUILD=1 ;;
		*) args+=("$a") ;;
		esac
	done
	export NO_BUILD
	cmd_prod ${args+"${args[@]}"}
	;;
sim | mock)
	[ "$1" = mock ] && deprecated mock sim
	shift
	cmd_sim "$@"
	;;
build) cmd_build ;;
help | -h | --help) usage ;;
*)
	usage >&2
	exit 1
	;;
esac
