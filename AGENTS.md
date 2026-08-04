# 改这个仓库之前

**这份代码能让一条 48V 的机械臂动起来。** 下面四条铁律，违反了都**不会报错** —— 只是结果错了，而错的方式是臂掉下来、算错力矩、或者拍完一整轮全是空片。

项目在做什么、怎么用，看 [`README.md`](./README.md)。这里只讲改代码要知道的。

---

## 常用命令

```bash
git submodule update --init                  # 臂层是 submodule，漏了 import 就失败
uv sync
uv run pytest                                # 252 个测试
uv run -m backend.app --sim                  # 无硬件启动，127.0.0.1:18790
uvx ruff check backend tests

cd frontend && npm install && npm run build  # 产物进 backend/static/
cd frontend && npm run dev                   # 热更新，proxy 到 18790

./manage.sh push                             # build + rsync + 重启（部署）
./manage.sh status                           # 跑在真臂还是模拟器上
```

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
  config.py         只放真正依赖部署的值。限位不在这（从 URDF 读）
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
    executor.py     Routine 执行器。纯逻辑，注入时钟/arm/shutter
    floatlock.py    浮动/锁定判据。带迟滞与最短静止时间
    broadcaster.py  控制线程 → asyncio 的扇出。有界队列，丢旧包

  routines/
    models.py       Action（判别式联合）/ Waypoint / Routine
    store.py        一 routine 一 JSON，原子写

  shutter/
    base.py         ShutterDriver Protocol + 异常类型
    protocol.py     行协议编解码 + LineReader
    esp32.py        串口客户端。单条在途，id 防迟到回包
    sim.py          SimShutter，可脚本化失败

  api/
    gate.py         require_arm_available —— 运动闸门，闩锁期间 409
    plugins.py      GET /api/plugins —— 前端据此渲染触发表单
    estop.py        急停端点
    routines.py     序列与点位 CRUD
    control.py      播放 / 示教 / 录点 / 快门自检 / WebSocket
    agent.py        Agent 控制端点（OpenAPI 直接给 LLM 做 tool import）
    logs.py         journalctl 包装

frontend/src/       Vite + React + TS。围绕锚点卡片板（使用层 / 配置层双模式）
frontend/mock/      `npm run dev:mock` 的内存后端，形状必须跟着 backend 手工对齐
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
锚点卡的「已到位」只能由 `phase === "done"` 点亮。三个已经踩过的坑：`phase` 为空**不等于**到位；controller 会保留已完成的 executor，socket 持续重播上一次的 `done`，所以陈旧的 `done` 不能当作新点击的答复；急停或进入示教后必须立刻作废「已到位」—— 臂被冻在别处或即将被人推走。另外 `_advance_waypoint` 先自增后判断，收尾时 `waypoint_index == waypoint_total`，前端要夹紧。

**急停在栈顶**
`.estop-bar` 是 z-index 60，在所有遮罩（40）之上。新增任何浮层前先确认它不会盖住急停 —— 示教正是双手在臂上的那个模式，而它以前恰好被自己的遮罩挡住了。`Esc` 由弹层用**原生**监听截停（React 合成事件的 `stopPropagation` 拦不住 window 级监听）。

---

## 测试

`uv run pytest`。**只测外部可观察行为，不测实现细节** —— 测「急停后所有运动端点返 409」而不是「闩锁内部布尔值变了」。测试要在行为回归时失败，而不在重构时失败。

不测：前端组件、`reBotArm_control_py` 本身、MotorBridge SDK、ESP32 固件、真实硬件在环。

`SimArm` 和 `SimShutter` 是一等公民而非测试边角料 —— 它们同时是无硬件开发循环的基础设施，两者都支持注入失败。

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
| [`docs/INTERACTION.md`](./docs/INTERACTION.md) | 交互骨架详细设计：布局 / 流程 / 参数隐藏 / goto 接口 | 动前端界面或加运动端点时 |
| [`docs/PLUGINS.md`](./docs/PLUGINS.md) | 三个扩展点：动作插件 / 触发源 / 事件订阅。写给要扩展这台机器的人 | 加动作类型、接外部触发、做集成时 |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | 历史设计决策记录（不再追加；当前设计模式看 ARCHITECTURE.md） | 想知道某个旧决定为什么这样时 |

`CLAUDE.md` 只是指向本文件的指针，不要往里写内容。

**每件事只写一处。** 硬件数值在 `HARDWARE_NOTES.md`、进度在 `PROGRESS.md`、用法在 `README.md`，本文件只放改代码的约定并链过去。往这里抄一份副本，副本就会先过时 —— 而这个仓库里过时得最要命的正是「为什么不能调那个看起来正确的方法」。

