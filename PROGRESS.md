# 做到哪了

现在在哪、下一步、什么卡住。铁律在 [`AGENTS.md`](./AGENTS.md)，交互约束在 [`docs/TIMELINE.md`](./docs/TIMELINE.md)，硬件事实在 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。为什么这么写：`git log`。

后端只由人用 `./dev.sh sim` / `./dev.sh prod` 起。Agent 不跑 `backend.app`、不占用 18790。状态变了才改本文件，changelog 不写在这。

---

## ▶ 当前

主线：**示教 → 编排 → 回放**在近零位安全范围跑通。不等相机。配件 / 插件全停，场景实地落地才拼。

| | |
|---|---|
| 入口 | `./dev.sh sim` 全栈模拟臂；`./dev.sh prod` 真臂（连不上就拒绝，不退回模拟器）；`./dev.sh ui` 只前端 mock；`./dev.sh status` 看 :18790。命令以 `./dev.sh --help` 为准 |
| 机上 | 夹爪已装回并接线（yaml `gripper: true`，7 关节，profile 锁 `gripper`）。开合不做。真臂连接 / 零位 / goto / 保持 / 急停冻结已通 |
| 内核 | 互斥活动表 `decide`；Latch 横切；idle/done/stop 持续 hold；接触残差默认关；客户端 2s 沉默 → SafeLock |
| 进行中 | 浮动手感标定——j2 重力前馈过补（松手冲到 47°），需加逐关节 k/c 重力修正；标定前勿展开姿态手掰（HARDWARE_NOTES #B2）。先用前端把「示教→编排→回放」闭环跑通（近零位）。五条结构性发现待裁决：急停后在途快门静默完成 / idle 不发令且 drift 看门狗关闭 / 示教浮动无软限位 / agent `command_joints` 绕路径预检 / 租约独占不约束 UI 端点 |
| 不要做 | 默认脸 / 移动端重写；真机打开接触观测；夹爪开合、快门 / 转台插件、相机 payload 标定 |

**展开姿态不要手掰示教。** j2 重力前馈过补，松手会自己上冲。k/c 标定流程见 HARDWARE_NOTES #B2。

---

## 🚧 阻塞 / 待验证

只挡验证，不挡代码。实现和测试都在，缺真机数值。

| # | 项 | 现状 |
|---|---|---|
| B2 | 挂相机后重力；j2 过补 | 相机未到。标定前勿在展开姿态示教 |
| B3 | R2x 上 500 Hz | 未测 |
| B4 | ESP32 板子 | 未确认在手 |
| B5 | 进站 0.25 rad/s | 挂相机后未标定 |
| B6 | `SETTLE_DRIFT_RAD` / `SETTLE_MIN_S` | 真机未标定 |

B1（CAN = XCAN-USB + MacCAN）已解，事实在 HARDWARE_NOTES。

---

## 环境

| | 含义 | 现状 |
|---|---|---|
| `L` | 开发机 + pytest + sim | 可用 |
| `H` | 真臂 | 可用；展开姿态示教不安全 |
| `E` | ESP32 烧录 | 未确认 |
