#!/usr/bin/env python3
"""逐关节对比「电机实测力矩」与「模型重力前馈」—— 验证浮动手感的地基。

为什么需要它：示教浮动时臂「自己往上抬」，首要嫌疑是重力前馈在真机上
符号或幅值不对（docs/HARDWARE_NOTES.md #9 标注了「上机后要拿实际电流
对一遍」）。这个脚本就是那个对比。

物理依据：静态平衡下，电机输出的力矩必然等于真实重力负载 —— 所以实测
力矩就是「正确答案」。模型算的 g(q) 若与实测符号相反，浮动时的前馈就是
在**推**臂而不是**托**臂；幅值差出一倍以上，手感就是「飘着」或「坠着」。

它做什么、不做什么：

- 连接后以纯位置环刚性保持**当前姿态**（kp=50, kd=3, 不带任何重力前馈，
  位置环独自扛重力），采样电机实际输出力矩，与模型值逐关节对比。
- 全程只发「保持当前位置」，不发任何运动指令。退出时不失能电机
  （臂保持原位，不会掉）。
- 跑之前**先退出所有 backend 实例**（它们独占 CAN 总线；退出会触发
  回零，q≈0 姿态恰好有很强的重力信号，适合本测试）。

用法::

    uv run scripts/verify_gravity.py                # 采样 2 秒
    uv run scripts/verify_gravity.py --seconds 5    # 更长的平均窗口

读结果：每个关节一行，verdict 列 —— OK 表示符号一致且幅值在 0.5~2 倍内；
FLIPPED 表示符号相反（前馈在推臂）；WEAK 表示该关节当前姿态下重力信号太弱，
看不出结论（把臂停在肘更展开的姿态再测）；SCALE 表示符号对但幅值差得远。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

# 仓库根上 sys.path，才能 import backend（上游示例也是这样处理 vendor 的）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import assets  # noqa: E402
from backend.safety.kinematics import ARM_JOINTS  # noqa: E402

HOLD_KP = 50.0
HOLD_KD = 3.0

#: 实测力矩低于这个值，说明该关节在当前姿态几乎不扛重力，判不出符号。
MIN_SIGNAL_NM = 0.15
#: 幅值比值落在这个区间外就算 SCALE 可疑。
RATIO_LO, RATIO_HI = 0.5, 2.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=2.0, help="力矩采样窗口长度")
    parser.add_argument("--rate", type=float, default=50.0, help="采样频率 Hz")
    args = parser.parse_args()

    from reBotArm_control_py.actuator.rebotarm import RebotArm
    from reBotArm_control_py.dynamics.inverse_dynamics import (
        compute_generalized_gravity,
        create_data,
    )
    from reBotArm_control_py.dynamics.robot_model import load_dynamics_model

    # 铁律 2：显式传 RS 的 URDF，永远不走上游默认解析（那是另一条臂）。
    # 走 effective_urdf_path + 落盘的负载 profile：bare 剥掉夹爪质量，
    # camera 把称出的末端负载（相机/支架）注入 gripper_end —— 模型必须和
    # 机上实物一致，这个对比才有意义。
    from backend import config as app_config
    from backend.tuning import TuningStore

    assets.assert_rs_model()
    payload = TuningStore(app_config.TUNING_FILE).load().payload
    model = load_dynamics_model(str(assets.effective_urdf_path(payload)))
    print(f"负载 profile：{payload.profile.value}"
          + (f"（mass={payload.camera.mass} kg, com={payload.camera.com}）"
             if payload.profile.value == "camera" else ""))
    data = create_data(model)

    arm = RebotArm(str(assets.effective_hardware_yaml()))
    names = list(arm.joint_names)
    index = {name: i for i, name in enumerate(names)}

    print(f"连接机械臂（{len(names)} 关节）…")
    arm.connect()
    arm.enable_all()
    try:
        # 刚性保持当前姿态，不带重力前馈：位置环独自扛重力，实测力矩即
        # 真实负载。臂本来在哪就保持在哪，不动。
        q, _, _ = arm.get_state()
        print("当前姿态 (deg):", "  ".join(f"{n}={np.rad2deg(q[index[n]]):+.1f}" for n in ARM_JOINTS))
        print(f"刚性保持中，采样 {args.seconds:.1f}s …（臂不会动；Ctrl+C 中止）")

        samples: list[np.ndarray] = []
        deadline = time.monotonic() + args.seconds
        period = 1.0 / args.rate
        while time.monotonic() < deadline:
            tick = time.monotonic()
            q, _, torq = arm.get_state()
            samples.append(np.array(torq, dtype=float))
            for group in arm.groups.values():
                group.send_mit(
                    np.asarray(q, dtype=float),
                    vel=np.zeros(len(q)),
                    kp=np.full(len(q), HOLD_KP),
                    kd=np.full(len(q), HOLD_KD),
                    tau=np.zeros(len(q)),
                )
            elapsed = time.monotonic() - tick
            if elapsed < period:
                time.sleep(period - elapsed)

        if not samples:
            print("一帧都没采到 —— 检查 CAN 连接。")
            return 1

        measured = np.mean(samples, axis=0)
        noise = np.std(samples, axis=0)
        arm_q = np.array([q[index[n]] for n in ARM_JOINTS], dtype=float)
        tau_g = compute_generalized_gravity(model, arm_q, data)

        print(f"\n{'joint':<8} {'实测 N·m':>16} {'模型 g(q)':>10} {'噪声':>6}  verdict")
        print("-" * 60)
        worst = 0
        for pos, name in enumerate(ARM_JOINTS):
            m = float(measured[index[name]])
            g = float(tau_g[pos])
            sd = float(noise[index[name]])
            if abs(m) < MIN_SIGNAL_NM and abs(g) < MIN_SIGNAL_NM:
                verdict = "WEAK"
            elif m * g < 0:
                verdict = "FLIPPED"
                worst = 2
            elif abs(m) < MIN_SIGNAL_NM or not (RATIO_LO <= abs(g / m) <= RATIO_HI):
                verdict = "SCALE"
                worst = max(worst, 1)
            else:
                verdict = "OK"
            print(f"{name:<8} {m:>+16.3f} {g:>+10.3f} {sd:>6.3f}  {verdict}")

        print()
        if worst == 2:
            print("结论：有符号相反的关节 —— 浮动前馈在推臂。把本输出贴回开发会话。")
        elif worst == 1:
            print("结论：符号全对，但有幅值可疑的关节 —— 手感会偏「飘」或「坠」。")
        else:
            print("结论：所有有信号的关节都一致 —— 重力模型在真机上可信。")
        return worst
    finally:
        # 不失能电机：断开总线后臂保持最后指令。要放下臂，用位姿库 goto。
        arm.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
