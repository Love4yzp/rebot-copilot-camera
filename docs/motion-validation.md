# 无界面运动验证

> 以下运动验证证据早于 SDK/MeshCat 重构，记录的测试计数描述的是当时的版本，不是当前套件。当前运行时与命令见 [README](../README.md)。

最终验证：默认环境未装 MuJoCo，`pytest -q -rs` 以 **537 passed, 2 skipped** 完成，两个 skip 是可选的物理模块。全部 21 个前端契约用例在已安装 Node 依赖下运行。可选物理环境完成了两个物理测试文件，**25 passed**，场景 CLI 返回退出码 0。本地 API 测试在沙箱外运行，因为沙箱的 socket 限制会挡住 TestClient 的跨线程事件循环唤醒。

可选物理测试台通过注入的 RebotArm 形状 transport 驱动真实的 `ArmSession`。它记录 MIT 设定点、期望速度、增益与重力前馈，然后用最后收到的指令推进 MuJoCo plant。plant 不插值应用层的轨迹。

## 回放改动的实现说明

运动参考是静止到静止的五次位置曲线（现已在 SDK motion_profiles 模块中实现）。曲线解析计算位置、速度、加速度与加加速度，并在接受一次移动前检查它们的极值。默认软件限位是 `v=0.25 rad/s`、`a=0.5 rad/s²`、`jerk=2 rad/s³`。执行器发出的时长是驱动返回的已接受物理时长；当某限位要求拉伸时，它可能长于请求的时长。

执行器把名义时间轴时间与物理段时间分开。标记与转场使用名义进度，而已接受的物理时长按比例映射剩余段。WAIT 捕获一个实测位姿，并必须在每个控制 tick 继续发送相同的保持参考。恢复时，剩余运动从该冻结参考重新规划，带新的到位截止时间。

每次移动都绑定到最后实际发送的参考，并携带一代 token。连续的指令流延续该参考。准备阶段无执行器副作用；安全预检在提交前运行，被拒绝的改向保持现有运动不变。首个接近段由同一组曲线边界检查，而不是只靠独立的首块估计。

间隔场景保留历史故障注入：`gap_s` 是物理推进量，而观测到的控制 tick 间隔是 `gap_s + 0.01 s`，所以记录的用例是 `0.11 s` 与 `1.01 s`。指令参考跳变与实测 plant 移动分开报告。当前边界策略使用现有的 `0.02 rad` 限位容差，分析曲线与安全路径检查通过 `backend.arm.limits` 共享它。

prod 与基础导入不依赖 MuJoCo；运行时 sim 需要 physics extra。安装验证环境：

```bash
cd app
UV_CACHE_DIR=/tmp/rebot-uv-cache uv sync --frozen --extra physics
```

运行确定性验证场景：

```bash
cd app
uv run --frozen --extra physics python -m backend.validation \
  --output data/validation/current.json \
  --csv data/validation/current.csv --check
```

测试台把 RS URDF 复制到临时目录，因为其 mesh 路径相对于 vendor 布局。它注入 `fusestatic=false` 并验证 `base_link` 与 `gripper_end` 仍是命名构件。运行 plant 场景前，它把 MuJoCo 静态重力矢量与 Pinocchio 对照检查。

基线终版模型证据记录在 [`docs/evidence/motion/baseline-final-model.json`](./evidence/motion/baseline-final-model.json) 及其 CSV 工件。在被记录的版本上，后续转场发出了 `2.398725 rad/s` 的指令速度峰值。一秒间隔产生 `0.554471 rad` 参考跳变。五秒 WAIT 暂停以执行器到位截止时间超时而中止结束。指令参考与实测 plant 状态在证据中是分开的字段。JSON 记录了复现该基线所需的 git 版本、URDF 哈希、源哈希与运行时设置。修复后的结果记录在 [`docs/evidence/motion/fixed-final.json`](./evidence/motion/fixed-final.json)；其 CSV SHA-256 记录在 [`docs/evidence/motion/comparison.md`](./evidence/motion/comparison.md)。修复后的运行达成了该对比中的速度、间隔、WAIT 与改向检查。这些是软件与可选物理的结果，不是真机验证。默认全套件验收另述。

plant 使用 1 ms 时间步与 MuJoCo `implicitfast` 以支撑刚性的 MIT 速度反馈。模型保持 RS 固定手指质量，不虚构从夹爪电机角度到手指行程的映射。相机负载惯量不由该测试台标定。

可选测试仅在安装了 extra 时运行：

```bash
uv run --frozen --extra physics pytest -q \
  tests/test_physics_model.py tests/test_physics_playback.py
```

默认 `uv sync --frozen` 路径不安装 MuJoCo；导入正常后端与可选包无关。测试台是无界面、纯 CPU 的。它验证指令连续性、plant 响应、运动学与动力学一致性，但不标定真实摩擦、电机固件响应、相机惯量或未标定的夹爪齿轮传动。