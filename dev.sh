#!/usr/bin/env bash
# dev.sh —— 本机开发启动脚本：所有命令都在你坐着的这台机器上执行。
# 部署到设备是另一个脚本 ./device.sh —— 两个脚本的分界不是「做什么」，
# 而是代码在哪台机器上执行。
#
# 日常只有两种模式：
#   ./dev.sh sim   只起前端，内存 mock，无后端 —— 看界面、前端开发
#   ./dev.sh prod [--sim] [--no-build] [--host H] [--port N]
#                  production：构建前端 + 起后端，同一个源；无硬件加 --sim（模拟臂）。
#                  后端的安全措施（急停闩锁 / 运动闸门 / 看门狗）与启动方式
#                  无关，任何模式都在。
#   ./dev.sh build 只构建前端，产物进 backend/static/
#
# API 联调不起前端：./dev.sh prod --no-build（需已 build 过一次），/docs 即控制台。
# sim 和 prod 跑在前台，Ctrl-C 停。不带参数只打印本说明，不默认启动。
# 旧名别名（过渡期，择机移除）：mock → sim。
#
# build 归这里而不是 device.sh：构建是本机工作。device.sh push 调它，
# 构建步骤就只有一个所有者 —— 抄第二份会漂移，而漂移掉的那份照样产出
# 一个能跑的 bundle，只是不是你要发的那个。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${REBOT_PORT:-18790}"

die() {
	echo "error: $*" >&2
	exit 1
}
step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

# Help text is the header comment at the top of this file — a second copy here
# would drift, and help text that describes the wrong flags is worse than none.
usage() {
	awk 'NR > 1 && /^$/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"
}

# The arm layer is a git submodule. An empty vendor/ does not fail at clone
# time, it fails at import — and in mock mode it fails as a 3D view with no
# arm in it, which reads like a rendering bug rather than a missing checkout.
require_submodule() {
	[ -f "$HERE/vendor/reBotArm_control_py/pyproject.toml" ] ||
		die "vendor/reBotArm_control_py is empty — run: git submodule update --init"
}

npm_install() {
	[ -d "$HERE/frontend/node_modules" ] || (cd "$HERE/frontend" && npm install)
}

cmd_build() {
	step "[本机] 构建前端"
	npm_install
	# Output goes to backend/static/, which app.py mounts last.
	(cd "$HERE/frontend" && npm run build)
}

cmd_prod() {
	require_submodule
	command -v uv >/dev/null || die "uv not found — https://astral.sh/uv"

	step "[本机] 安装 Python 依赖"
	(cd "$HERE" && uv sync)

	if [ "${NO_BUILD:-}" = "1" ]; then
		[ -f "$HERE/backend/static/index.html" ] ||
			die "--no-build given but backend/static/ is empty"
		step "[本机] 跳过前端构建（--no-build）"
	else
		# Rebuild every time: a stale bundle served by a fresh backend is the
		# confusing half of the two, because the API answers correctly and the
		# screen still lies.
		cmd_build
	fi

	# --host/--port on the command line beat the environment, so read them back
	# out of the arguments before announcing an address. A banner that prints one
	# port while the server binds another is worse than no banner: you spend the
	# confusion budget on curl instead of on whatever you were actually doing.
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
	# Without --sim the factories look for real hardware. The arm falls back to
	# the simulator and says so in the log; the shutter never falls back. Watch
	# the startup lines, or check /api/health, before believing a frame was shot.
	echo "  http://$host:$port"
	exec env LANG="${LANG:-zh_CN.UTF-8}" uv run -m backend.app "$@"
}

cmd_sim() {
	require_submodule

	step "[本机] 启动 sim 前端（内存 mock，无后端）"
	# `vite --mode mock` drops the backend proxy and mounts mock/plugin.ts, which
	# answers /api and /ws from in-memory state and serves the URDF out of the
	# submodule. No backend, no python, no arm. The mock is hand-aligned with the
	# backend's shapes — when they drift, this previews an app that does not exist.
	npm_install
	cd "$HERE/frontend"
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
