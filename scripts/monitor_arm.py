#!/usr/bin/env python3
"""实时监听臂在干什么 —— 接 /ws，把「哪个电机在动、多快、往哪」翻译成行。

用法::

    uv run scripts/monitor_arm.py [--port 18790]

打印三类事件：

- 状态切换：mode / estop / playback 变化（谁进入了示教、谁触发了急停、谁在跑序列）
- 运动：任一关节 |速度| 超过 0.03 rad/s（谁在动、往哪、多快），节流每秒 2 行
- 跳变：相邻两帧间某关节位置突变 > 0.05 rad 而速度读数很小 —— 命令层跳变，
  是「电机突然动了一下」的指纹

其余每 5 秒一行心跳（模式 + 各关节角度），证明监听还活着。
电机级「使能/失能/错误码」不在 /ws 广播里 —— 那些要独占 CAN 直问驱动，
和 backend 不能同时跑，需要时用 scripts/verify_gravity.py 那种独占脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets

VEL_ALERT = 0.03  # rad/s，超过就算「在动」
JUMP_ALERT = 0.05  # rad，帧间位置突变超过这个而速度读数小 = 命令跳变
HEARTBEAT_S = 5.0


def fmt_q(positions: dict[str, float]) -> str:
    return " ".join(f"{n[5:]}={v:+.2f}" for n, v in sorted(positions.items()))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18790)
    args = parser.parse_args()

    uri = f"ws://127.0.0.1:{args.port}/ws"
    print(f"连接 {uri} …", flush=True)
    async with websockets.connect(uri) as ws:
        print("已连接。动臂吧，我看着。", flush=True)
        prev_positions: dict[str, float] | None = None
        prev_meta: tuple | None = None
        last_motion_print = 0.0
        last_heartbeat = time.monotonic()

        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") != "state":
                continue
            data = msg["data"]
            positions = data.get("positions", {})
            velocities = data.get("velocities", {})
            meta = (data.get("mode"), data.get("estop"), bool(data.get("playback")))
            now = time.monotonic()
            stamp = time.strftime("%H:%M:%S")

            if meta != prev_meta:
                print(f"[{stamp}] 状态 → mode={meta[0]} estop={meta[1]} playback={meta[2]} | {fmt_q(positions)}", flush=True)
                prev_meta = meta

            moving = {n: v for n, v in velocities.items() if abs(v) > VEL_ALERT}
            if moving and now - last_motion_print > 0.5:
                parts = " ".join(f"{n[5:]}={v:+.2f}rad/s(q={positions.get(n, 0):+.2f})" for n, v in sorted(moving.items()))
                print(f"[{stamp}] 在动 {parts}", flush=True)
                last_motion_print = now

            if prev_positions:
                jumps = {
                    n: positions[n] - prev_positions[n]
                    for n in positions
                    if n in prev_positions
                    and abs(positions[n] - prev_positions[n]) > JUMP_ALERT
                    and abs(velocities.get(n, 0.0)) < VEL_ALERT * 2
                }
                if jumps:
                    parts = " ".join(f"{n[5:]} Δ{d:+.3f}rad" for n, d in sorted(jumps.items()))
                    print(f"[{stamp}] ⚠ 跳变 {parts} | {fmt_q(positions)}", flush=True)

            prev_positions = positions

            if now - last_heartbeat > HEARTBEAT_S:
                print(f"[{stamp}] … mode={data.get('mode')} rate={data.get('rate_hz', 0):.0f}Hz | {fmt_q(positions)}", flush=True)
                last_heartbeat = now


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
