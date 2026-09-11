# 做到哪了

现在在哪、下一步、什么卡住。铁律在 [`AGENTS.md`](../AGENTS.md)，交互约束在 [`docs/TIMELINE.md`](./TIMELINE.md)，硬件事实在 [`docs/HARDWARE_NOTES.md`](./HARDWARE_NOTES.md)。为什么这么写：`git log`。

后端只由人用 `./dev.sh sim` / `./dev.sh prod` 起。Agent 不跑 `backend.app`、不占用 18790。状态变了才改本文件，changelog 不写在这。

---

## ▶ 当前

主线：**示教 → 编排 → 回放**。应用只负责工作流和安全策略，机器人算法、MuJoCo 物理 transport 和 MeshCat 查看器统一经 SDK 接入。插件、快门、Agent 运行时已移除，后续接入只保留设计契约。

| | |
|---|---|
| 入口 | `./dev.sh sim` 全栈 + SDK MuJoCo；`./dev.sh prod` 真臂（连接失败不回退）；独立 ui/mock 已移除，Vite 仅代理完整后端。命令以 `./dev.sh --help` 为准 |
| 工作台 | 位姿库、只读 MeshCat 实际反馈、站位编辑器；选择不运动，必须点明确运动按钮；无浏览器插值预演 |
| SDK | 保留已验证上游基线，由仓库内可重放补丁交付扩展；尚未发布维护 fork。应用不直接依赖或调用 Pinocchio / MotorBridge |
| 仿真 | 相同 MIT 指令驱动物理，独立线程积分；固定末端质量/质心/惯量/碰撞模型共享，sim 数据与真实标定隔离。只允许示教中有界短时扰动，不模拟夹爪开合 |
| 机上 | 上次实测为夹爪已装回并接线（yaml `gripper: true`，7 关节）。原有连接 / 零位 / goto / 保持 / 急停冻结证据保留；此次未连接真臂，未改配置或标定 |
| 内核 | 互斥活动表 `decide`；Latch 横切；idle/done/stop 持续 hold；接触残差默认关；客户端 2s 沉默 → SafeLock |
| 验证 | 440 项 pytest 通过，含物理、SDK 补丁重放、只读查看器、运动闸门和双语言契约；前端构建、Ruff、OpenAPI 漂移检查通过。独立物理验证 CLI 的 `--check` 通过，报告输出至临时目录。SDK wheel 已构建并核对内含模块与资产 |
| 下一步 | 人工运行 `./dev.sh sim` 验收工作台；新末端需测量参数并做真机受控验证。MuJoCo 反馈不等于真实标定，旧硬件限制仍有效 |
| 不要做 | 无标定的展开姿态手掰；真机打开接触观测；恢复插件 / 快门 / Agent 运行时；直接暴露未认证运动 API |

**展开姿态不要手掰示教。** j2 重力前馈过补，松手会自己上冲。k/c 标定流程见 HARDWARE_NOTES #B2。

---

## 🚧 阻塞 / 待验证

自动化验证已通过；浏览器人工验收和重构后的真机回归尚未进行。以下硬件事项仍需实测。

| # | 项 | 现状 |
|---|---|---|
| B2 | 挂相机后重力；j2 过补 | 相机未到。标定前勿在展开姿态示教 |
| B3 | R2x 上 500 Hz | 未测 |
| B5 | 进站 0.25 rad/s | 挂相机后未标定 |
| B6 | `SETTLE_DRIFT_RAD` / `SETTLE_MIN_S` | 真机未标定 |

B1（CAN = XCAN-USB + MacCAN）已解，事实在 HARDWARE_NOTES。B4 快门硬件不属于当前交付范围。

---

## 环境

| | 含义 | 现状 |
|---|---|---|
| `L` | 开发机 + pytest + sim | 可用 |
| `H` | 真臂 | 上次记录可用；此次未连接，展开姿态示教仍不安全 |
