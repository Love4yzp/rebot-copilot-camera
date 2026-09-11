# 30 分钟看懂 Teach & Repeat

这份文档只负责**建立心智模型和指路**。用法在 [`README.zh-CN.md`](../README.zh-CN.md)，改代码的硬约束在 [`AGENTS.md`](../AGENTS.md)，每个文件的职责在 [`CODEMAP.md`](./CODEMAP.md)。

## 0–5 分钟：它解决什么问题

Teach & Repeat 把机械臂的空间位姿编成可重复执行的时间序列：

```text
推臂示教 → 保存位姿 → 编排序列 → 预检整条路径 → 执行 → 到站触发动作
```

四个领域积木：

| 积木 | 含义 |
|---|---|
| 位姿 Pose | 素材库中的命名关节构型 |
| 序列 Sequence | 保持块与过渡块交替组成的时间轴 |
| 动作 Action | 到达或运动途中触发的外设操作 |
| 模板 Template | 不含关节角的序列结构配方 |

完整、唯一的词义在 [`CONTEXT.md`](../CONTEXT.md)。

## 5–10 分钟：代码分哪几层

```text
入口层       backend/api/                         HTTP / WebSocket 适配
                         │
                         ▼
内核         core/controller.py + activity.py     命令缝与控制循环
              │                     │
              ▼                     ▼
编排引擎     core/executor.py      safety/         序列推进 / 横切安全
              │
          ┌───┴───────────┐
          ▼               ▼
硬件边界 arm/            actions/ → shutter/      臂驱动 / 异步动作
```

依赖方向只有一条：入口调用内核，内核协调编排与硬件。插件拿不到臂；API 不直接下运动指令；`app.py` 是唯一组合根。

## 10–20 分钟：跟三条主链路

### 执行一条序列

```text
TransportBar
→ frontend/src/api.ts
→ backend/api/sequences.py
→ Controller.preflight_* / Controller.play
→ SequenceExecutor.tick
→ ArmDriver.move_to / hold
→ Broadcaster → /ws → useControlSocket
```

执行前先解析位姿、检查关节限位、自碰撞、相邻路径和动作 provider。执行器只消费准备好的输入，不读数据库、不依赖 FastAPI，也不拥有急停闩锁。

### 录制一个位姿

```text
「+ 录位姿」→ POST /api/teach
→ Controller 先保持
→ 推臂后进入浮动
→ 停手后 FloatLock 重新锁定
→ POST /api/poses/capture
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

急停不是 Activity 的一种。它横切所有活动，优先于执行、示教和待命。这里的急停是**保持力矩钉在原地**，不是电机失能。

## 20–25 分钟：认清运行时对象

[`backend/app.py`](../app/backend/app.py) 创建并连接整套对象：一个 latch、一个 controller、一个 arm、一个 action runner、三类 store 和全部 API router。服务启动后只有 `Controller.tick()` 持续与臂交互。

真实和模拟实现共用接口：

- `ArmDriver`：`ArmSession` / `SimArm`
- `ShutterDriver`：`Esp32Shutter` / `SimShutter`
- `ActionProvider`：由 `ThreadedRunner` 在控制循环外执行

运行时位姿、序列和模板位于 `app/data/`，一份资源一个 JSON；它们不是仓库源码。

## 25–30 分钟：知道改哪里

| 任务 | 先读 | 主要落点 |
|---|---|---|
| 使用、部署、排障 | `README.md` / `README.zh-CN.md` | `dev.sh`、`device.sh` |
| 改任何代码 | `AGENTS.md`、`PROGRESS.md` | 按 `CODEMAP.md` 定位 |
| 改活动或控制语义 | `CONTEXT.md`、ADR 0001 | `core/activity.py`、`controller.py` |
| 改序列执行 | `TIMELINE.md` | `core/executor.py`、`sequences/` |
| 改前端编排交互 | `TIMELINE.md` | `frontend/src/` |
| 加动作或外部集成 | `PLUGINS.md` | `actions/`、外部 HTTP/WS 客户端 |
| 碰真臂、限速、重力、碰撞 | `HARDWARE_NOTES.md` | `arm/`、`safety/`、`assets.py` |
| 看当前做到哪 | `PROGRESS.md` | 不从历史文档猜 |

## 读完后的自测

能回答下面六题，就已经足够开始定位代码：

1. 一次“执行”从哪个入口走到哪个硬件接口？
2. 为什么 SafetyLatch 不是 Activity？
3. 为什么动作 provider 不能直接拿到 arm？
4. 位姿、保持块和模板分别保存什么？
5. 真臂与模拟臂在哪里替换？
6. 改硬件行为、时间轴交互和当前状态分别读哪份文档？

准备修改前，再完整阅读 [`AGENTS.md`](../AGENTS.md)。
