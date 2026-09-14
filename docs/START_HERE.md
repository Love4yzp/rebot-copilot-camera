# 30 分钟看懂 Teach & Repeat

这份文档只负责**建立心智模型和指路**。用法在 [`README.md`](../README.md)，改代码的硬约束在 [`AGENTS.md`](../AGENTS.md)，每个文件的职责在 [`CODEMAP.md`](./CODEMAP.md)。

**现状**:插件、快门、Agent 运行时、固件、独立 mock 已经移除——应用收敛为「录位姿 → 编排站位 → 执行回放」，机器人算法和传输统一由 [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py) 提供。未来接入契约只作为设计说明留在 [`PLUGINS.md`](./PLUGINS.md)，不是已装好的空框架。

## 0–5 分钟:它解决什么问题

Teach & Repeat 把机械臂的空间位姿编成可重复执行的时间序列:

```text
推臂示教 → 保存位姿 → 编排序列 → 预检整条路径 → 执行 → 到站保持
```

四个领域积木:

| 积木 | 含义 |
|---|---|
| 位姿 Pose | 素材库中的命名关节构型 |
| 序列 Sequence | 保持块与过渡块交替组成的时间轴,v3 只支持等待标记 |
| 模板 Template | 不含关节角的序列结构配方 |

完整、唯一的词义在 [`CONTEXT.md`](./CONTEXT.md)。

## 5–10 分钟:代码分哪几层

```text
入口层       backend/api/                          HTTP / WebSocket 适配
                         │
                         ▼
命令缝       core/controller.py + activity.py       闩锁优先、预检、控制 tick
              │
              ▼
编排         core/executor.py + sequences/          hold/transition/wait 遍历,无插件依赖
              │
              ▼
硬件边界     arm/(ArmSession/SimArm)+ safety/       SDK 会话适配、碰撞/限位/FK 包装
```

依赖方向只有一条:入口调用控制器,控制器协调编排与硬件。API 不直接下运动指令;`app.py` 是唯一组合根,`runtime.py` 决定这一个实例是 prod 还是 sim、接哪个末端模型。

## 10–20 分钟:跟三条主链路

### 执行一条序列

```text
frontend/src/App.tsx(单工作台)
→ frontend/src/generated/api.ts(OpenAPI 生成客户端)
→ backend/api/sequences.py
→ Controller.preflight_* / Controller.play
→ SequenceExecutor.tick
→ ArmDriver.move_to / hold
→ Broadcaster → /ws → useControlSocket
```

执行前先解析位姿、检查关节限位、自碰撞、相邻路径。执行器只消费准备好的输入,不读数据库、不依赖 FastAPI,也不拥有急停闩锁。

### 录制一个位姿

```text
「+ 录位姿」→ backend/api/control.py 的 teach 端点
→ Controller 先保持
→ 推臂后进入浮动
→ 停手后 FloatLock 重新锁定
→ backend/api/poses.py 的 capture 端点
→ PoseStore 原子写入 JSON
```

### 急停

```text
按钮 / Esc → POST /api/estop → SafetyLatch 吸合
                                  │
API 运动闸门立即拒绝新命令 ←──────┤
                                  ▼
Controller 每 tick 冻结当前姿态并持续 hold
```

急停不是 Activity 的一种。它横切所有活动,优先于执行、示教和待命。这里的急停是**保持力矩钉在原地**,不是电机失能。

### sim 专属:往模拟臂上戳一下

```text
监视器「详细数据」「− 推动 / ＋ 推动」
→ backend/api/simulation.py 的 /api/sim/perturb
→ Controller.perturb
→ SDK MuJoCo plant 注入有界力矩脉冲(≤0.2s、≤20% effort)
```

这是仿真力矩输入,不是 CAN 命令;prod 不接受这个端点。查看器(MeshCat,经 `backend/api/viewer.py`)只读,旋转/缩放/复位视角不改变物理状态。

## 20–25 分钟:认清运行时对象

[`backend/app.py`](../app/backend/app.py) 创建并连接整套对象;[`backend/runtime.py`](../app/backend/runtime.py) 决定单个 prod/sim 实例选哪个末端模型、要不要起 MuJoCo 与查看器。服务启动后只有 `Controller.tick()` 持续与臂交互,100 Hz。

真实和模拟实现共用 `ArmDriver` 接口:

- prod:`ArmSession`(SDK 的 MIT 会话适配)
- sim:运行时走 SDK MuJoCo plant(经 `runtime.py`),不是 `arm/sim.py` 的 `SimArm`——那个 `SimArm` 现在只是**可注入时钟和故障的测试替身**,给 pytest 用,不是 `./dev.sh sim` 背后跑的东西。

运行时位姿、序列和模板位于 `app/data/`,一份资源一个 JSON;sim 数据在 `app/data/sim/` 下隔离(gitignored)。它们不是仓库源码。

## 25–30 分钟:知道改哪里

| 任务 | 先读 | 主要落点 |
|---|---|---|
| 使用、部署、排障 | `README.md` | `dev.sh`、`device.sh` |
| 改任何代码 | `AGENTS.md`、`PROGRESS.md` | 按 `CODEMAP.md` 定位 |
| 改活动或控制语义 | `CONTEXT.md`、ADR 0001 | `core/activity.py`、`controller.py` |
| 改序列执行 | `TIMELINE.md` | `core/executor.py`、`sequences/` |
| 改前端编排交互 | `TIMELINE.md` | `frontend/src/` |
| 碰真臂、限速、重力、碰撞 | `HARDWARE_NOTES.md` | `arm/`、`safety/`、`assets.py` |
| 想加外部能力 | `PLUGINS.md`(设计说明,非已装插件) | 目前无落点,先读为什么现在不做 |
| 看当前做到哪 | `PROGRESS.md` | 不从历史文档猜 |

## 读完后的自测

能回答下面六题,就已经足够开始定位代码:

1. 一次"执行"从哪个入口走到哪个硬件接口?
2. 为什么 SafetyLatch 不是 Activity?
3. sim 的 `/api/sim/perturb` 和 prod 的真实控制路径,共用了哪一段、又在哪里分岔?
4. 位姿、保持块和模板分别保存什么?
5. `arm/sim.py` 的 `SimArm` 现在是什么角色,`./dev.sh sim` 背后真正跑的是什么?
6. 改硬件行为、时间轴交互和当前状态分别读哪份文档?

准备修改前,再完整阅读 [`AGENTS.md`](../AGENTS.md)。
