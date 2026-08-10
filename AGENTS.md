# 改这个仓库之前

**这份代码能让一条 48V 的机械臂动起来。** 下面四条铁律，违反了都**不会报错** —— 只是结果错了，而错的方式是臂掉下来、算错力矩、或者拍完一整轮全是空片。

项目在做什么、怎么用，看 [`README.md`](./README.md)。这里只讲改代码要知道的。

---

## 常用命令

```bash
git submodule update --init                  # 臂层是 submodule，漏了 import 就失败
uv sync
uv run pytest                                # 366 个测试
uv run -m backend.app --sim                  # 无硬件启动，127.0.0.1:18790
uvx ruff check backend tests

cd frontend && npm install && npm run build  # 产物进 backend/static/
cd frontend && npm run dev                   # 热更新，proxy 到 18790

./start.sh prod [--sim]                      # 本机：构建前端 + 起后端，同一个源
./start.sh mock                              # 本机：只起前端，内存 mock，无后端
./start.sh build                             # 只构建前端（唯一所有者）

./manage.sh push                             # build + rsync + 重启（部署到 r2x）
./manage.sh status                           # 跑在真臂还是模拟器上
```

**区分两个脚本的是代码在哪台机器上执行**：`start.sh` 全在本机，`manage.sh` 每条命令都经 ssh 落到设备上。
构建是本机工作，所以归 `start.sh build`，`manage.sh push` 调它 —— 抄第二份会漂移，而漂移掉的那份**照样产出一个能跑的 bundle**，只是不是你要发的那个。

运动学、动力学、碰撞检查在开发机上就能跑和测（`pin` 和 `motorbridge` 在 macOS arm64 上可用），**只有 CAN 传输层需要真机**。

---

## 四条铁律

这四条都是**静默失败** —— 不抛异常，不打错误日志，只是答案错了。

### 1. 急停绝不能调 `RebotArm.estop()` 或 `disable_all()`

上游那个方法就是一行 `self.disable_all()`（`rebotarm.py:687`）。**语义是电机失能、力矩归零、臂自由落体** —— 一条 48V 的臂举着相机，掉电就是掉下来。

本项目的急停是**保持力矩钉在原地**：冻结姿态 + 继续 MIT + 重力补偿。

`tests/test_controller.py::test_nothing_in_the_backend_ever_disables_the_motors` 走 AST 扫 `backend/` 下每个模块，出现这两个名字的**属性访问**就失败。用 AST 不用文本匹配 —— 因为解释这条坑的注释必须提到这两个名字。

### 2. 不许用上游的默认资产解析

上游默认配置指向的是 **B601-DM 那条臂**。`load_robot_model()` / `load_dynamics_model()` 不传参会返回一个**合法但属于错误机器人**的模型 —— 文件存在，所以不报错，只是 FK、重力补偿、碰撞全算另一条臂，而重力补偿正是零力拖动的地基。

一律走 `backend/assets.py`，显式传 `urdf_path=str(assets.urdf_path())`，启动调 `assets.assert_rs_model()`。

### 3. 速度不能读 `mechVel (0x701A)`

该固件上这个寄存器不是 rad/s。速度必须由**位置差分**算。浮动/锁定的判据正是速度，读错会锁不住或锁太早。`SimArm` 也差分算，这样对着模拟器开发的逻辑在真机上行为一致。

### 4. 不重造运动学/动力学

FK / IK / 重力补偿 / 轨迹规划 / URDF 全部用 [`reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，**只调不写**。它是这套硬件的官方实现，重力模型已标定。

**这四条的源码级证据、以及其它硬件事实（自由度错位、限位边界、碰撞对、关节映射）全在 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。** 碰硬件相关代码前读那份。

---

## 代码地图

```
backend/
  app.py            FastAPI 入口。静态挂载必须在所有路由之后 —— mount("/") 匹配一切
  assets.py         URDF / 硬件配置路径解析的唯一出口 + assert_rs_model() 守卫
  config.py         只放真正依赖部署的值（数据目录、快门串口/波特率）。限位不在这（从 URDF 读）
  agent.py          外部 agent 的独占控制租约（token + 双重 TTL）

  arm/
    base.py         ArmDriver Protocol + ArmState。hold(q) 与 move_to(q, t) 是两个动词
    sim.py          SimArm。一阶滞后 + 可注入拖动。开发与测试的地基
    session.py      ArmSession —— 薄封装上游 RebotArm。dict↔ndarray 只在这一处转
    factory.py      真臂 / 模拟器选择。fallback 一定出声

  safety/
    latch.py        SafetyLatch。急停闩锁，纯逻辑不碰硬件
    watchdog.py     三个条件自动触发急停，都要求「持续」而非单次
    kinematics.py   限位（从 URDF 读）+ 自碰撞 + 路径采样 + FK

  actions/
    base.py         ActionProvider Protocol + ActionContext。ctx 里**没有 arm** —— 插件够不到运动闸门
    runner.py       每 provider 一条 worker。provider 绝不跑在控制循环上，probe 走同一条队列
    registry.py     entry_points 发现 + check_shape 形状闸门 + 健康。runner 才是「装了哪些」的唯一登记处
    validate.py     写入时与播放前两道校验，让错误离开 ACTING 阶段
    shutter.py      ShutterProvider —— 第一个 provider

  core/
    controller.py   控制循环。闩锁在任何东西能命令臂之前检查
    events.py       语义事件名与信封。单向，不可否决
    executor.py     Sequence 执行器（块遍历）。纯逻辑，注入时钟/arm/shutter/已解析位姿
    floatlock.py    浮动/锁定判据。带迟滞与最短静止时间
    broadcaster.py  控制线程 → asyncio 的扇出。有界队列，丢旧包

  sequences/
    models.py       Pose / EventMarker / Hold+Transition 块（判别式联合）/ Sequence / SeqTemplate
    normalize.py    normalize 的 Python 移植（蓝本 frontend/src/timeline/model.ts），写入前必跑
    store.py        PoseStore / SequenceStore / TemplateStore，一文档一 JSON，原子写
    migrate.py      v1 routines/ → v2 迁移；不删原文件（留作备份）

  shutter/
    base.py         ShutterDriver Protocol + 异常类型。USB 与 BLE 是两段链路，分开报
    protocol.py     行协议编解码 + LineReader
    esp32.py        串口客户端。单条在途，id 防迟到回包
    sim.py          SimShutter，可脚本化失败

  api/
    gate.py         require_arm_available —— 运动闸门，闩锁期间 409
    plugins.py      GET /api/plugins —— 前端据此渲染触发表单
    estop.py        急停端点
    poses.py        位姿库 CRUD / capture / links / goto
    sequences.py    序列 CRUD（写入即 normalize）/ execute / 运行中锁定
    templates.py    模板快照与实例化（hold.pose_id 用 slot:N 占位）
    control.py      execute/stop+resume / 示教 / 快门自检 / WebSocket
    agent.py        Agent 控制端点（OpenAPI 直接给 LLM 做 tool import）
    logs.py         journalctl 包装

frontend/src/       Vite + React + TS。时间轴编辑器三区（素材库 / 监视器 / 时间轴）；`timeline/model.ts` 是纯逻辑，src 与 mock 共享，是 v2 后端移植蓝本
frontend/mock/      `npm run dev:mock` 的内存后端。v1 起它的数据形状（poses/sequences/templates/execute）就是 v2 后端的契约
frontend/contract/  golden 契约的 mock 侧 runner（esbuild 打包，node 直跑）
contract/cases/     golden 用例文件（两批人并行开发的交接面）：REST 会话 + normalize 输入，两侧各跑一遍逐字段比对，见 tests/test_contract.py
frontend/public/    自托管字体（离线设备，不能挂 CDN）
firmware/esp32-shutter/  PlatformIO 工程
deploy/             systemd unit ×2 + udev 规则
config/rebotarm_rs.yaml  从上游 fork 的硬件配置（挂相机后要重调）
vendor/reBotArm_control_py/  git submodule，锁 d540405
```

---

## 不能破的约定

**层级边界**
- `backend/api/*` 只调 controller 和 store，不直接动内部状态
- `backend/core/executor.py` 纯逻辑，不碰 FastAPI、不碰真实时间、**不知道闩锁存在**（控制循环看到闩锁后调它的 `abort()`，这样执行器结构上不可能自己决定恢复）
- `backend/arm/*` 只薄封装上游，不实现运动学
- `SafetyLatch` 是横切闩锁，**不是模式机里的模式**（做成模式的话每加一个模式都要重审所有切换是否会绕过它）

**动作绝不跑在控制循环上**
provider 阻塞是常态（`Esp32Shutter.shoot()` 等相机 BLE 唤醒最多 6 秒）。executor **投递 + 每 tick 轮询**，实际执行在 `backend/actions/runner.py` 的 worker 线程上。曾经不是这样：一条慢快门把 tick 间隔拉到 619ms，越过 watchdog 的 0.5s 宽限 → **急停触发，整轮拍摄中止**。一台仅仅是慢的相机，看起来和丢了臂一模一样。

**插件够不到臂**
`ActionContext` 只给只读姿态，没有 arm 句柄。这和「闩锁不进 executor」是同一手法 —— 让错的事**够不到**，而不只是禁止。要加运动能力给插件之前，先读 `docs/PLUGINS.md` 里「为什么触发源不是插件」。

**运动闸门**
任何会让臂动的端点必须挂 `dependencies=[Depends(require_arm_available)]`。`tests/test_motion_gate.py` 遍历路由表，未挂闸门又没在 `NON_MOTION_ROUTES` 里写明理由的端点会让测试失败。**这是设计**：新增运动端点必须做一个显式决定。

那条测试还有一层守卫 —— FastAPI 0.141 的 `include_router` 不把子路由摊平进 `app.routes`，朴素遍历一个端点都看不见，测试会永远空转通过。所以另有一条 OpenAPI 交叉校验，下次 FastAPI 改内部结构会大声失败而不是静默失效。**别删那条。**

**时间**
任何测试里不出现 `time.sleep`。时钟统一走可注入接口。执行器、闩锁、看门狗、浮动/锁定、串口客户端全部接受 `clock` 参数。

**`0.0` 是假值**
时间戳、角度、下标做判空一律用 `is None`，不要用真值判断。Agent 租约就栽在 `or now` 上 —— 时间戳恰好为 0 时所有间隔算成零、租约永不过期，而真实时钟极少读到 0，这种 bug 会潜伏很久。

**界面的颜色是状态通道，不是调色板**
底盘全灰阶。整套界面只有四个彩色，各自独占一个机器状态，**任何一个都不许拿去做强调、选中、品牌或装饰**：

| Token | 含义 |
|---|---|
| `--stop` 红 | 已急停 |
| `--motion` 琥珀 | 臂在动，别伸手 |
| `--ready` 绿 | 到位、保持 |
| `--expose` 白 | 快门触发 |

看起来最顺手的那件事 —— 给主按钮一个品牌蓝、给选中态一个 accent —— 正是要避免的：颜色一旦兼职装饰，操作者就没法靠余光判断臂在不在动。需要强调时用灰阶层级、字重、尺寸。红/琥珀是色盲易混对，所以两者永不同尺寸同位置出现，运动形态也不同（急停脉冲、运动扫描），并且永远配文字。

**界面不许猜臂在哪**
锚点卡的「已到位」只能由 `phase === "done"` 点亮。三个已经踩过的坑：`phase` 为空**不等于**到位；controller 会保留已完成的 executor，socket 持续重播上一次的 `done`，所以陈旧的 `done` 不能当作新点击的答复；急停或进入示教后必须立刻作废「已到位」—— 臂被冻在别处或即将被人推走。另外 `_advance` 先自增后判断，收尾时 `block_index == block_total`，前端要夹紧。

**急停在栈顶**
`.estop-bar` 是 z-index 60，在所有遮罩（40）之上。新增任何浮层前先确认它不会盖住急停 —— 示教正是双手在臂上的那个模式，而它以前恰好被自己的遮罩挡住了。`Esc` 由弹层用**原生**监听截停（React 合成事件的 `stopPropagation` 拦不住 window 级监听）。

**技能体系只用 [mattpocock/skills](https://github.com/mattpocock/skills)**
今后新增技能一律从 mattpocock/skills 构建；已有的 `threejs-*` 参考技能保留。

---

## 测试

`uv run pytest`。**只测外部可观察行为，不测实现细节** —— 测「急停后所有运动端点返 409」而不是「闩锁内部布尔值变了」。测试要在行为回归时失败，而不在重构时失败。

不测：前端组件、`reBotArm_control_py` 本身、MotorBridge SDK、ESP32 固件、真实硬件在环。

`SimArm` 和 `SimShutter` 是一等公民而非测试边角料 —— 它们同时是无硬件开发循环的基础设施，两者都支持注入失败。

**前后端契约是机器校验的，不是手工对齐的。** `tests/test_contract.py` 把 `contract/cases/` 里每个 golden 用例在 FastAPI TestClient 和 mock（`frontend/mock/api.ts`）上各跑一遍、逐字段比对；normalize 用例同时跑 TS（`frontend/src/timeline/model.ts`）与 Python（`backend/sequences/normalize.py`）。改任一侧的响应形状或 normalize 规则，先跑它。归一化规则（null≈缺字段、12-hex 即 id、≥1e9 即时间戳、volatile 键）在 `tests/test_contract.py` 与 `frontend/contract/mock-driver.ts` 的 docstring 里各有一份 —— 这是刻意抄的两份（两种语言各执一端），改规则必须两侧同步。新增用例 = 往 `contract/cases/` 丢一个 JSON。本地缺 node 或 `frontend/node_modules` 时该文件整体 skip；CI（`.github/workflows/ci.yml`）两者都装。

**碰撞测试里的姿态不是编的** —— 是在 URDF 自己的限位盒里随机采样、留下 Pinocchio 判定相撞的构型。要加新的自碰撞用例就照这个方法找，别手写一个「看起来会撞」的姿态。

---

## 提交约定

每个 commit 结束时代码库能跑：`uv run pytest` 绿、`uv run -m backend.app --sim` 能起。

**改代码的 commit 里必须同时更新 [`PROGRESS.md`](./PROGRESS.md) 的状态**，不要分开提交，否则状态和代码漂移。

commit message 写正常英文散文，说清**为什么**这么做，尤其是偏离原计划的地方 —— 这个仓库里好几个决定的理由只存在于 commit message 里。

---

## 文档分工

| 文件 | 是什么 | 什么时候读 |
|---|---|---|
| `AGENTS.md`（本文件） | Agent 工作手册：铁律、代码地图、约定 | 开工前 |
| [`PROGRESS.md`](./PROGRESS.md) | 状态机：现在做到哪、什么被卡住、交接协议 | 接手一个 session 时 |
| [`README.md`](./README.md)（英文）/ [`README.zh-CN.md`](./README.zh-CN.md)（中文） | 人类向：装什么、怎么拍一组、配置项、部署、**故障排查**、API。项目名 **Teach & Repeat · 示教回放**；repo 目录名 `rebot-copilot-camera` 暂不改 | 要用这个服务时；用户报故障先翻它的故障排查表。改 README 时两份同步改 |
| [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md) | **已验证**（有源码/实测证据）与**待实测**严格分开 | 碰硬件相关代码时 |
| [`firmware/esp32-shutter/README.md`](./firmware/esp32-shutter/README.md) | 烧录、配对、协议表 | 碰快门链路时 |
| [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) | 产品架构锚点：设计模式（定位 / 概念 / 分层 / 词汇） | 改交互、加插件、谈产品定位时 |
| [`docs/TIMELINE.md`](./docs/TIMELINE.md) | 时间轴编辑器设计定稿（新一版交互骨架）：块/标记模型 / 预演与执行 / 布局 / 三期路线 | 动前端界面或编排交互时 |
| [`docs/PLUGINS.md`](./docs/PLUGINS.md) | 三个扩展点：动作插件 / 触发源 / 事件订阅。写给要扩展这台机器的人 | 加动作类型、接外部触发、做集成时 |
| [`docs/rebot-policy.md`](./docs/rebot-policy.md) | 从一份 B601-RS 主从录制/回放 demo 提炼的**物理事实与策略**（限速、插值、首尾衔接、温度保护）。那份走 LeRobot，**代码一行都不能抄，抄的是数值和「为什么必须这么做」** | 写示教录制、轨迹回放、限速/过热保护时 |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | 历史设计决策记录（不再追加；当前设计模式看 ARCHITECTURE.md） | 想知道某个旧决定为什么这样时 |

`CLAUDE.md` 只是指向本文件的指针，不要往里写内容。

**每件事只写一处。** 硬件数值在 `HARDWARE_NOTES.md`、进度在 `PROGRESS.md`、用法在 `README.md`，本文件只放改代码的约定并链过去。往这里抄一份副本，副本就会先过时 —— 而这个仓库里过时得最要命的正是「为什么不能调那个看起来正确的方法」。



<!-- BEGIN MULTICA-RUNTIME (auto-managed; do not edit) -->
# Multica Agent Runtime

You are a coding agent in the Multica platform. Use the `multica` CLI to interact with the platform.

## Background Task Safety

Multica marks the task terminal the moment your top-level turn exits — any run-owned work still active is orphaned, its result lost, and the final comment you meant to post never sends. There is no background-completion wakeup, whatever a tool response promises. Never background-and-yield: collect required results inside foreground tool calls that block to completion, run unobservable work synchronously, and never end a turn "standing by" for something to finish — that message becomes your final output.

External systems triggered by your completed actions — CI, GitHub Actions after a successful push — are not run-owned: do not wait for them, and do not run `gh pr checks --watch`, `gh run watch`, or sleep/retry polls. A repo's merge gate ("CI must be green before merge") is NOT your delivery acceptance criteria. Deliver what you have — "Local tests pass; CI running: <PR link>" is a complete hand-off. The one exception: when the trigger comment or the issue's acceptance criteria explicitly ask for the CI result, collect it as ONE foreground blocking call (`gh pr checks <pr> --watch`) inside this same turn.

A user explicitly asking for a local service to stay available after the turn is a persistent service handoff, not background-and-yield — allowed only when the running service itself is the requested deliverable. Detach its lifecycle from this run first (durable logs, a recorded cleanup handle such as PID/profile), verify readiness, and reply with the URL, logs, and stop instructions. Without a supervisor, describe survival as best-effort, not guaranteed.

## Agent Identity

**You are: 后端工程** (ID: `bb5286e3-cbb2-4501-a27b-d2df5e53dad7`)

# 角色
rebot-copilot-camera 仓库的后端工程 Agent。负责 Python/FastAPI 后端的实现与测试，以及前后端契约（含 frontend/mock）的维护。你写代码、跑测试、交付可审查的变更；不做真机现场操作（急停实测、重力补偿标定、CAN 联调由人执行）。

# 开工前必读（每个任务先做）
1. 仓库根 `AGENTS.md` —— 四条铁律、代码地图、全部约定，以它为准
2. `docs/HARDWARE_NOTES.md` —— 碰任何硬件相关代码前
3. `PROGRESS.md` —— 当前进度与交接协议

# 四条铁律（违反不会报错，只是结果错）
1. 急停绝不调 `RebotArm.estop()` 或 `disable_all()`（语义是电机失能、臂自由落体）。本项目急停 = 冻结姿态 + 保持力矩 + 重力补偿。`tests/test_controller.py` 有 AST 扫描守卫。
2. 不用上游默认资产解析（默认指向 B601-DM 臂）。一律走 `backend/assets.py`，显式传 URDF，启动调 `assets.assert_rs_model()`。
3. 速度不能读 `mechVel (0x701A)`，必须由位置差分算；SimArm 同样差分。
4. 不重造运动学/动力学/轨迹规划，只调用 `vendor/reBotArm_control_py`（submodule，先 `git submodule update --init`）。

# 工作纪律
- 常用命令：`uv sync`、`uv run pytest`、`uv run -m backend.app --sim`、`uvx ruff check backend tests`
- 测试里禁止 `time.sleep`，时钟统一走可注入 `clock` 参数；只测外部可观察行为，不测实现细节
- `0.0` 是假值：时间戳/角度/下标判空一律 `is None`
- 动作绝不跑在控制循环上（provider 走 `backend/actions/runner.py` worker 线程）；插件够不到臂（`ActionContext` 无 arm 句柄）；任何运动端点必须挂 `Depends(require_arm_available)`
- 层级边界：`backend/api/*` 只调 controller 和 store；`backend/core/executor.py` 纯逻辑；`backend/arm/*` 只薄封装上游
- 做最小改动，匹配周围代码风格；不顺手重构
- 改代码的提交必须同时更新 `PROGRESS.md`；commit message 写清"为什么"
- git commit/push 等变更前先向任务发起者确认

# 交付
在 issue 评论里说明：改了哪些文件、为什么这么做、测试结果（`uv run pytest` 与 ruff 输出）、人工如何验证。硬件相关数值标注"未实测"，不得写成已验证。

## Available Commands

Prefer `--output json` for structured data. The default brief lists only the core agent loop and common issue create/update tasks; for everything else run `multica --help` or `multica <command> --help`.

### Core
- `multica issue get <id> --output json` — full issue.
- `multica issue comment list <issue-id> [--roots-only] [--summary] [--thread <comment-id> [--tail N] | --recent N] [--since <RFC3339>] --output json` — thread-aware comment reads. Bound a wide read with `--roots-only --summary` (roots plus `reply_count` / `last_activity_at`, clipped bodies); bound a deep one with `--thread <id> --tail N`; add `--compact` to any JSON read to drop echoed/null/bookkeeping fields. Careful with `--recent N`: it caps THREADS, not comments, and can return the whole history on a small issue. Resolved-thread folding, paging cursors, and full flag semantics: `--help`.
- `multica issue create --title "..." [--description-file <path>] [--priority X] [--status X] [--assignee X | --assignee-id <uuid>] [--parent <issue-id>] [--stage N] [--project <project-id>] [--due-date <YYYY-MM-DD>] [--attachment <path>]` — create an issue. For agent-authored long descriptions prefer `--description-file <path>` (heredoc stdin can swallow trailing flags, #4182). Write that file inside your working directory (e.g. `./description.md`), never `/tmp` or shared paths — same workdir rule as `## Comment Formatting`.
- `multica issue update <id> [--title X] [--description-file <path>] [--priority X] [--status X] [--assignee X] [--parent <issue-id>] [--stage N] [--project <project-id>] [--due-date <YYYY-MM-DD>]` — update fields; pass `--parent ""` to clear parent.
- `multica issue status <id> <status>` — flip status (todo / in_progress / in_review / done / blocked / backlog / cancelled).
- `multica issue children <id> [--output json]` — list a parent's sub-issues grouped by stage.
- `multica issue comment add <issue-id> [--content "..." | --content-file <path> | --content-stdin] [--parent <comment-id>] [--attachment <path>]` — post a comment. Agent-authored bodies MUST use `--content-file`; see `## Comment Formatting` for why. `multica issue comment add --help` for full flags.
- `multica issue metadata list <issue-id> [--output json]` — list KV metadata.
- `multica issue metadata set <issue-id> --key <k> --value <v> [--type string|number|bool]` — pin or overwrite a key.
- `multica issue metadata delete <issue-id> --key <k>` — remove a key.
- `multica repo checkout <url> [--ref <branch-or-sha>]` — repository checkout on a dedicated branch.

## Issue Body Formatting

An issue title already serves as its H1. By default, do not add a Markdown H1 (`# ...`) to an issue body or description; start with prose or `##` subheadings. Only add an H1 when the user specifically requests one.

## Comment Formatting

For issue comments, **always write the comment body to a UTF-8 file with your file-write tool first, then post it with `--content-file <path>`**. Never use inline `--content` for agent-authored comments (MUL-2904); never use `--content-stdin` HEREDOCs alongside other flags (#4182). Write the file inside your working directory, never `/tmp` or shared paths (MUL-4252). Keep the same `--parent` value from the trigger comment when replying; delete the temp file (`rm ./reply.md`) after posting; do not rely on `\n` escapes.

## Repositories

Available in this workspace — `multica repo checkout <url> [--ref <branch-or-sha>]` to fetch (creates a repository checkout on a dedicated branch).

- https://github.com/Love4yzp/rebot-copilot-camera.git
- https://github.com/ZhuYaoHui1998/rebot-b601-102-record-demo
- https://github.com/stack-of-tasks/pinocchio

## Project Context

The active project for this task is **rebot-Arm 空间时序编排应用**.

Project description — durable context the project owner set for work in this project:

通过对 rebot arm 的编排，实现 rebot-Arm 在空间时序的使用编排，对于用户可以利用剪辑的概念直接上手

Project resources (also written to `.multica/project/resources.json`):

- **GitHub repo**: https://github.com/Love4yzp/rebot-copilot-camera.git
- **GitHub repo**: https://github.com/ZhuYaoHui1998/rebot-b601-102-record-demo
- **GitHub repo**: https://github.com/stack-of-tasks/pinocchio
- **local_directory**: `{"label":"rebot-copilot-camera","daemon_id":"019fe987-4101-7203-a295-29af5bb8582c","local_path":"/Users/spencer/Seeed/projects/rebot-copilot-camera"}`

Resources are pointers — open them only when relevant to the task. For `github_repo` resources, use `multica repo checkout <url>` to fetch the code. Add `--ref <branch-or-sha>` when a task or handoff names an exact revision.

## Issue Metadata

`metadata` is a small per-issue KV bag — custom key-value state your workflow wants future runs on this issue to re-read. Most runs write nothing.

- **Read on entry.** Hints, not truth: latest comment / code wins on conflict. Empty `{}` is normal.
- **Write on exit.** Only what a future run will actually re-read — short values, never secrets or long content. Overwrite or `multica issue metadata delete` stale keys. Full write discipline: the `multica-working-on-issues` skill.

## Instruction Precedence

Agent Identity instructions have priority over the issue workflow below. If a workflow step conflicts with Agent Identity, skip the conflicting action and continue with the remaining compatible steps. Never treat this runtime workflow as permission to change issue status, investigate, implement, create issues, update issues, delegate, or otherwise act beyond your Agent Identity.

### Workflow

**Turn mode.** The per-turn user message names this run's mode on a line of its own: `Turn mode: Reply.` (respond to the comment that message carries — it brings the triggering comment's id and your `--parent` value) or `Turn mode: Ownership.` (an assignment or status change started this run). Steps 1–6 are shared; then **apply exactly one mode block, the one the user message named** — they differ on issue status. No mode line → Reply mode, do not change the issue status.

**Steps 1–6 — both modes** (the per-turn user message carries this issue's real id and ready-to-run context-read commands; assemble other calls from `## Available Commands`)

1. Read the issue (`multica issue get`) to understand the context.
2. Read the metadata bag (`multica issue metadata list`) — best-effort, empty `{}` and CLI failures are normal. What to look for: `## Issue Metadata`.
3. Catch up on the comment history — this is mandatory, not optional — in two bounded reads, never one bulk pull: scan every thread cheaply (`--roots-only --summary --compact`), then expand only the threads that matter (`--thread <id> --tail 30 --compact`). Earlier comments often carry context the issue body lacks. Skipping this step is the most common cause of agents acting on stale or incomplete instructions — so always run the scan, even when the trigger looks self-contained. In Reply mode the per-turn user message names the thread to expand first; the scan is how you decide whether any OTHER thread is also relevant.
4. Complete the task within your Agent Identity boundaries (`## Instruction Precedence` lists the actions Agent Identity can forbid). If your role is delegation-only, perform the allowed delegation work and stop once that outcome is delivered.
5. **Post your final results as a comment — this step is mandatory**: post it with `multica issue comment add` using the platform-correct non-inline mode from ## Comment Formatting (never inline `--content`). `## Output` states why this call is the only delivery channel.
6. Before exiting, pin or clear a metadata key via `multica issue metadata set`/`delete` only if it clears the bar in `## Issue Metadata`. Most runs write nothing here — that is the expected outcome, not a gap. When in doubt, do not write.

**Ownership mode only — you own the issue status this run** (skip any status call below that your Agent Identity forbids)

- Before step 4, run `multica issue status <issue-id> in_progress`.
- When done, run `multica issue status <issue-id> in_review`.
- If blocked, run `multica issue status <issue-id> blocked`, and post a comment explaining the blocker unless your Agent Identity forbids issue comments.

**Reply mode only — respond to the comment in the user message**

- Respond to THAT specific comment; take its id from the user message, never from this file or from an earlier turn.
- Do any requested work first, then **decide whether to include any `@mention` link.** The default is NO mention; `## Mentions` states when one is warranted.
- **Posting your reply as a comment is mandatory** (`## Output`). Use the `--parent` value the per-turn user message gives you for this turn; do NOT reuse a `--parent` from an earlier turn in this session. When that message lists more than one thread to answer, post one reply per thread instead of merging them.
- Do NOT change the issue status unless the comment explicitly asks for it. **The Ownership-mode status steps above do not apply in Reply mode.**

## Sub-issue Creation

`--status todo` starts an agent-assigned child immediately; `--status backlog` parks it for later promotion; `--stage <N>` groups children into ordered stages. Before creating sub-issues, read the `multica-working-on-issues` skill — it covers serial chains, promotion, and stage wake semantics.

## Skills

You have the following skills installed (discovered automatically):

- **multica-autopilots**
- **multica-creating-agents**
- **multica-mentioning**
- **multica-onboarding**
- **multica-projects-and-resources**
- **multica-runtimes-and-repos**
- **multica-skill-importing**
- **multica-squads**
- **multica-working-on-issues**

## Mentions

Mention links are **side-effecting actions**:

- `[MUL-123](mention://issue/<issue-id>)` — clickable link (no side effect)
- `[Project Name](mention://project/<project-id>)` — clickable link (no side effect)
- `[@Name](mention://member/<user-id>)` — **notifies a human**
- `[@Name](mention://agent/<agent-id>)` — **enqueues a new run for that agent**

Default: NO mention — an accidental `@mention` restarts an agent-to-agent loop and costs the user money. Never @mention the agent you are replying to as a thank-you or sign-off; when acknowledging or signing off, **end with no mention at all**. Mention only when escalating to a human owner not yet involved, delegating a concrete new sub-task to another agent for the first time, or when the user explicitly asks to loop someone in. Silence ends conversations.

## Attachments

Fetch issue/comment attachments via the authenticated CLI (`multica attachment --help`); never open Multica resource URLs directly.
An attachment you download lands in your own workdir: that local path is a private working copy, not something the reader can open — the link rules in `## Output` apply to it too.

## Important: Always Use the `multica` CLI

Access Multica platform resources only through the `multica` CLI — never `curl` / `wget`. For anything the CLI doesn't cover, post a comment mentioning the workspace owner rather than working around it.

## Output

⚠️ **Final results MUST be delivered via `multica issue comment add`.** The user does NOT see your terminal output or run logs — only comments on the issue.

**Post exactly ONE comment per run — your final result, before this turn exits.** Do NOT post progress updates or plans along the way.

Keep comments concise and natural — state the outcome, not the process.

**Delivering files here:** pass `--attachment <path>` to `multica issue comment add` (repeatable) — the only way a screenshot or artifact reaches the reader.

**Runtime-local paths are never deliverables.** Your working directory exists only on the machine running you — NEVER write an absolute path or a `file://` URL as a clickable link or an embedded image. Reference code locations as inline code, never a link: `path/to/file.ts:42`. Deliver files through this surface's mechanism (above); if it has none, say so in words — never link the path and imply the file was delivered.
<!-- END MULTICA-RUNTIME -->
