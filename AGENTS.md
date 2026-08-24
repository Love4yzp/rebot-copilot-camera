# 改这个仓库之前

**这份代码能让一条 48V 的机械臂动起来。** 下面四条铁律，违反了都**不会报错** —— 只是结果错了，而错的方式是臂掉下来、算错力矩、或者拍完一整轮全是空片。

项目在做什么、怎么用，看 [`README.md`](./README.md)。这里只讲改代码要知道的。

---

## 常用命令

> 布局说明：具体应用整体住在 `app/` 二级目录（`pyproject.toml` / `uv.lock` / `.venv` 都在里面），
> 顶层只放 AI/人读文档与 `dev.sh` / `device.sh`。**所有 `uv` 命令都在 `app/` 下执行。**

```bash
git submodule update --init                  # 臂层是 submodule，漏了 import 就失败
(cd app && uv sync)
(cd app && uv run pytest)
(cd app && uv run -m backend.app --sim)      # 无硬件启动，127.0.0.1:18790
(cd app && uvx ruff check backend tests)

cd app/frontend && npm install && npm run build  # 产物进 app/backend/static/
cd app/frontend && npm run dev                   # 热更新，proxy 到 18790

./dev.sh prod [--sim]                        # 本机：构建前端 + 起后端，同一个源
./dev.sh sim                                 # 本机：只起前端，内存 mock，无后端
./dev.sh build                               # 只构建前端（唯一所有者）
# API 联调不起前端：./dev.sh prod --no-build，/docs 即控制台。旧名 mock 是 sim 的别名，过渡期后移除。

./device.sh push                             # build + rsync + 重启（部署到设备，需先设 REBOT_HOST_SSH）
./device.sh status                           # 跑在真臂还是模拟器上
```

**区分两个脚本的是代码在哪台机器上执行**：`dev.sh` 全在本机，`device.sh` 每条命令都经 ssh 落到设备上。
构建是本机工作，所以归 `dev.sh build`，`device.sh push` 调它 —— 抄第二份会漂移，而漂移掉的那份**照样产出一个能跑的 bundle**，只是不是你要发的那个。

运动学、动力学、碰撞检查在开发机上就能跑和测（`pin` 和 `motorbridge` 在 macOS arm64 上可用），**只有 CAN 传输层需要真机**。

---

## 四条铁律

这四条都是**静默失败** —— 不抛异常，不打错误日志，只是答案错了。

### 1. 急停绝不能调 `RebotArm.estop()` 或 `disable_all()`

上游那个方法就是一行 `self.disable_all()`（`rebotarm.py:687`）。**语义是电机失能、力矩归零、臂自由落体** —— 一条 48V 的臂举着相机，掉电就是掉下来。

本项目的急停是**保持力矩钉在原地**：冻结姿态 + 继续 MIT + 重力补偿。

`app/tests/test_controller.py::test_nothing_in_the_backend_ever_disables_the_motors` 走 AST 扫 `app/backend/` 下每个模块，出现这两个名字的**属性访问**就失败。用 AST 不用文本匹配 —— 因为解释这条坑的注释必须提到这两个名字。

### 2. 不许用上游的默认资产解析

上游默认配置指向的是 **B601-DM 那条臂**。`load_robot_model()` / `load_dynamics_model()` 不传参会返回一个**合法但属于错误机器人**的模型 —— 文件存在，所以不报错，只是 FK、重力补偿、碰撞全算另一条臂，而重力补偿正是零力拖动的地基。

一律走 `app/backend/assets.py`，显式传 `urdf_path=str(assets.urdf_path())`，启动调 `assets.assert_rs_model()`。

### 3. 速度不能读 `mechVel (0x701A)`

该固件上这个寄存器不是 rad/s。速度必须由**位置差分**算。浮动/锁定的判据正是速度，读错会锁不住或锁太早。`SimArm` 也差分算，这样对着模拟器开发的逻辑在真机上行为一致。

### 4. 不重造运动学/动力学

FK / IK / 重力补偿 / 轨迹规划 / URDF 全部用 [`reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，**只调不写**。它是这套硬件的官方实现，重力模型已标定。

**这四条的源码级证据、以及其它硬件事实（自由度错位、限位边界、碰撞对、关节映射）全在 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。** 碰硬件相关代码前读那份。

---

## 代码地图

**分层速览（四张表的细节只在 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)「内核边界与部件关系」，这里不抄）：内核 = arm/ + safety/ + core/controller.py；编排引擎 = core/（executor/floatlock/broadcaster/events）+ sequences/；插件层 = actions/ + shutter/；入口层 = api/。**

```
app/backend/
  app.py            FastAPI 入口。静态挂载必须在所有路由之后 —— mount("/") 匹配一切
  assets.py         URDF / 硬件配置路径解析的唯一出口 + assert_rs_model() 守卫 + gripper 开关（has_gripper / effective_hardware_yaml 剥电机 / effective_urdf_path 剥质量）
  config.py         只放真正依赖部署的值（数据目录、快门串口/波特率）。限位不在这（从 URDF 读）
  tuning.py         调参模型与存取：payload profile / 浮动增益 / 阈值 + 服务端钳位；热改只进内存，显式保存才落 app/config/tuning.yaml
  agent.py          外部 agent 的独占控制租约（token + 双重 TTL）

  arm/
    base.py         ArmDriver Protocol + ArmState。hold(q) 与 move_to(q, t) 是两个动词
    sim.py          SimArm。一阶滞后 + 可注入拖动。开发与测试的地基
    session.py      ArmSession —— 薄封装上游 RebotArm。dict↔ndarray 只在这一处转
    factory.py      真臂 / 模拟器选择。fallback 一定出声

  safety/
    latch.py        SafetyLatch。急停闩锁，纯逻辑不碰硬件
    watchdog.py     三个条件自动触发急停，都要求「持续」而非单次
    contact.py      接触残差观测器。默认关；只在 playing 采样
    client_watchdog.py  回放/示教中客户端沉默 → SafeLock
    kinematics.py   限位（从 URDF 读）+ 自碰撞 + 路径采样 + FK

  actions/
    base.py         ActionProvider Protocol + ActionContext。ctx 里**没有 arm** —— 插件够不到运动闸门
    runner.py       每 provider 一条 worker。provider 绝不跑在控制循环上，probe 走同一条队列
    registry.py     entry_points + app/plugins/ 目录(plugin.json)两条发现路径 + check_shape 形状闸门 + 健康。runner 才是「装了哪些」的唯一登记处
    validate.py     写入时与播放前两道校验，让错误离开 ACTING 阶段
    shutter.py      ShutterProvider —— 第一个 provider
    check.py        插件作者的无硬件开发循环：列 manifest / 跑自检 / 真 runner 真超时跑一次

  core/
    activity.py     互斥活动表。decide(activity, intent) 是命令缝；闩锁不在这张表里
    controller.py   控制循环。闩锁在任何东西能命令臂之前检查；idle/done/stop 持续 hold
    events.py       语义事件名与信封。单向，不可否决
    executor.py     Sequence 执行器（块遍历）。纯逻辑，注入时钟/arm/shutter/已解析位姿
    floatlock.py    浮动/锁定判据。带迟滞与最短静止时间
    broadcaster.py  控制线程 → asyncio 的扇出。有界队列，丢旧包

  sequences/
    models.py       Pose / EventMarker / Hold+Transition 块（判别式联合）/ Sequence / SeqTemplate
    normalize.py    normalize 的 Python 实现（TS 端是 app/frontend/src/timeline/model.ts），写入前必跑
    store.py        PoseStore / SequenceStore / TemplateStore，一文档一 JSON，原子写
    seed_demo.py    首启演示数据（四方位：4 位姿 + 序列 + 模板）。与 mock 的镜像被契约用例 seeded-library 钉死

  shutter/
    base.py         ShutterDriver Protocol + 异常类型。USB 与 BLE 是两段链路，分开报
    protocol.py     行协议编解码 + LineReader
    esp32.py        串口客户端。单条在途，id 防迟到回包
    factory.py      真板 / 模拟选择。**快门永不回落** —— 回落等于 SimShutter 把每一帧都谎报拍到；只有 --sim 拿到模拟快门
    sim.py          SimShutter，可脚本化失败

  api/
    preflight.py    序列 / agent 共用的播放预检（位姿解析 + 整序列校验，全经 Controller.preflight_* 那道门）
    gate.py         require_arm_available —— 运动闸门，闩锁期间 409
    plugins.py      GET /api/plugins —— 前端据此渲染触发表单
    estop.py        急停端点。解除不是回到僵硬原位，而是直接进入零重力示教（先锁定、手一动就浮动）—— 急停后正是最需要用手掰臂的时刻
    poses.py        位姿库 CRUD / capture / links / goto
    sequences.py    序列 CRUD（写入即 normalize）/ execute / 运行中锁定
    templates.py    模板快照与实例化（hold.pose_id 用 slot:N 占位）
    control.py      execute/stop+resume / 示教 / 快门自检 / WebSocket
    agent.py        Agent 控制端点（OpenAPI 直接给 LLM 做 tool import）
    config.py       调参端点 GET/PUT/save/reset —— 闸门在 Controller.apply_tuning（执行中拒一切、浮动中拒负载切换），不挂运动闸门，但要在 NON_MOTION_ROUTES 写明理由
    logs.py         journalctl 包装

app/frontend/src/       Vite + React + TS。时间轴编辑器三区（素材库 / 监视器 / 时间轴）；`timeline/model.ts` 是纯逻辑，src 与 mock 共享，与 app/backend/sequences/normalize.py 互为双语言端。调参面板 `components/TuningPanel.tsx`（Tweakpane，停靠监视器区右侧，全灰阶，prod 进入需确认）
app/frontend/mock/      `npm run dev:mock` 的内存后端。数据形状与后端逐字段对齐，由 golden 契约测试守卫
app/frontend/contract/  golden 契约的 mock 侧 runner（esbuild 打包，node 直跑）
app/contract/cases/     golden 用例文件：REST 会话 + normalize 输入，两侧各跑一遍逐字段比对，见 app/tests/test_contract.py
app/frontend/public/    自托管字体（离线设备，不能挂 CDN）
app/firmware/esp32-shutter/  PlatformIO 工程
app/deploy/             systemd unit ×2 + udev 规则
app/config/rebotarm_rs.yaml  从上游 fork 的硬件配置（挂相机后要重调）
app/config/tuning.yaml       调参面板的落盘值（操作者标定）；文件缺失 = 代码默认值
app/vendor/reBotArm_control_py/  git submodule，锁 d540405

app/examples/rebot-plugin-turntable/  可安装的动作插件示例（pyproject [tool.uv.sources] 钉住路径，挪动要同步改）
app/data/               运行时数据根：poses/ sequences/ templates/ 三个库（gitignored；REBOT_DATA_DIR 可改家）
.agents/ + skills-lock.json   threejs-* 参考技能（git 跟踪，刻意保留，见「技能体系」）
app/tests/               测试自成「测试」章节，不在地图里复述
```

---

## 不能破的约定

**层级边界**
- `app/backend/api/*` 只调 controller 和 store，不直接动内部状态。所有运动前校验走 `Controller.preflight_*` 那道门（`app/backend/api/preflight.py` 是共用预检），不许直接 import `safety.kinematics.validate_*` / `actions.validate_*`
- **例外**：`api/gate.py` 与 `api/estop.py` 直接碰 `app.state.latch` —— 闩锁是横切件，急停必须从 HTTP 线程立刻吸合，不能排队进控制循环；`api/plugins.py` 只读 `ActionRegistry` —— 插件登记处的自述端点。都是刻意的前门，不是越界
- `app/backend/core/executor.py` 纯逻辑，不碰 FastAPI、不碰真实时间、**不知道闩锁存在**（控制循环看到闩锁后调它的 `abort()`，这样执行器结构上不可能自己决定恢复）
- `app/backend/arm/*` 只薄封装上游，不实现运动学
- `SafetyLatch` 是横切闩锁，**不是模式机里的模式**（做成模式的话每加一个模式都要重审所有切换是否会绕过它）

**动作绝不跑在控制循环上**
provider 阻塞是常态（`Esp32Shutter.shoot()` 等相机 BLE 唤醒最多 6 秒），而控制循环正是撑住臂的东西。executor **投递 + 每 tick 轮询**，实际执行在 `app/backend/actions/runner.py` 的 worker 线程上。一条慢快门曾把 tick 间隔拖过 watchdog 宽限触发急停 —— 一台仅仅是慢的相机，看起来和丢了臂一模一样。

**插件够不到臂**
`ActionContext` 只给只读姿态，没有 arm 句柄。这和「闩锁不进 executor」是同一手法 —— 让错的事**够不到**，而不只是禁止。要加运动能力给插件之前，先读 `docs/PLUGINS.md` 里「为什么触发源不是插件」。

**运动闸门**
任何会让臂动的端点必须挂 `dependencies=[Depends(require_arm_available)]`。`app/tests/test_motion_gate.py` 遍历路由表，未挂闸门又没在 `NON_MOTION_ROUTES` 里写明理由的端点会让测试失败。**这是设计**：新增运动端点必须做一个显式决定。

FastAPI 的 `include_router` 不把子路由摊平进 `app.routes`，朴素遍历一个端点都看不见，测试会永远空转通过 —— 所以那条测试有递归候选加 OpenAPI 交叉校验两层守卫，下次 FastAPI 改内部结构会大声失败而不是静默失效。**别删交叉校验。**

**调参闸门**
调参写入不走运动闸门，走 `Controller.apply_tuning` 的分级：**执行中拒一切写入**（executor 的值是构造时捕获的）；**负载 profile 切换额外拒于浮动中**（前馈一次跳几 N·m，浮动没有位置环接得住）；**float kp/kd 浮动中可改**（follow 目标=当前位置，跳变为零 —— 边掰边调就靠这条）。服务端钳位在 `app/backend/tuning.py` 的 pydantic 模型里，面板的滑块范围只是 UX，可以被绕过，端点不能。

**时间**
任何测试里不出现 `time.sleep`。时钟统一走可注入接口。执行器、闩锁、看门狗、浮动/锁定、串口客户端全部接受 `clock` 参数。

**`0.0` 是假值**
时间戳、角度、下标做判空一律用 `is None`，不要用真值判断。Agent 租约就栽在 `or now` 上：时间戳恰好为 0 时所有间隔算成零、租约永不过期。

**界面的颜色是状态通道，不是调色板**
底盘全灰阶。整套界面只有四个彩色，各自独占一个机器状态，**任何一个都不许拿去做强调、选中、品牌或装饰**：

| Token | 含义 |
|---|---|
| `--stop` 红 | 已急停 |
| `--motion` 琥珀 | 臂在动，别伸手 |
| `--ready` 绿 | 到位、保持 |
| `--expose` 白 | 快门触发 |

需要强调时用灰阶层级、字重、尺寸 —— 颜色一旦兼职装饰，操作者就没法靠余光判断臂在不在动。红/琥珀是色盲易混对，所以两者永不同尺寸同位置出现，运动形态也不同（急停脉冲、运动扫描），并且永远配文字。

**给操作者的界面指令用按钮上的真实字样**
界面没有「示教」按钮：示教入口是素材库底部「+ 录位姿」（点开底部示教条「零重力 · 臂可推动，松手自动锁定」，看关节角用条上「详细数据」，退出用「× 取消」）。「示教 / 浮动」是内部术语，转述成操作指令前必须映射成按钮真名，否则会被「页面上没有这个按钮」顶回来。

**界面不许猜臂在哪**
「已到位」只能由 `phase === "done"` 点亮，且只在 done 的上升沿认领 —— controller 会保留已完成的 executor，socket 持续重播上一次的 `done`，陈旧的 `done` 不能当作新点击的答复。急停、进入示教、开始新一次执行都必须立刻作废「已到位」—— 臂被冻在别处、即将被人推走、或已在路上。另外 `_advance` 先自增后判断，收尾时 `block_index == block_total`，前端要夹紧。

**急停在栈顶**
`.estop-bar` 是 z-index 60，在所有遮罩（40）之上。新增任何浮层前先确认它不会盖住急停 —— 示教正是双手在臂上的那个模式。`Esc` 由弹层用**原生**监听截停（React 合成事件的 `stopPropagation` 拦不住 window 级监听）。

**退出路径先回零**
Ctrl+C / SIGTERM 不直接退：`Controller.park_home()` 把臂慢速开回零位（复用 goto 的进站限速与到位检测），到位后才停控制循环 —— 停循环永远是退出的最后一步。闩锁吸合时例外：原地冻结保持退出，不回零（闩锁吸合意味着出了状况，不再规划新运动）。信号归 `app/backend/app.py` 的 `ParkOnExitServer` 管，**别换回 `uvicorn.run`**：原版第二次 Ctrl+C 置 `force_exit` 并**跳过 lifespan shutdown**，回零整段就没了。systemd 的 `TimeoutStopSec=60` 是按最坏回零 ~37s 留的，别调小。配套：`main()` 在碰 CAN 之前先做端口预检（`_ensure_port_free`）—— 一个绑不上端口的实例若连了臂，它的退出回零就会去动一台归别的进程管的臂。

**技能体系只用 [mattpocock/skills](https://github.com/mattpocock/skills)**
今后新增技能一律从 mattpocock/skills 构建；已有的 `threejs-*` 参考技能保留。

---

## 测试

`cd app && uv run pytest`。**只测外部可观察行为，不测实现细节** —— 测「急停后所有运动端点返 409」而不是「闩锁内部布尔值变了」。测试要在行为回归时失败，而不在重构时失败。

不测：前端组件、`reBotArm_control_py` 本身、MotorBridge SDK、ESP32 固件、真实硬件在环。

`SimArm` 和 `SimShutter` 是一等公民而非测试边角料 —— 它们同时是无硬件开发循环的基础设施，两者都支持注入失败。

**前后端契约是机器校验的，不是手工对齐的。** `app/tests/test_contract.py` 把 `app/contract/cases/` 里每个 golden 用例在 FastAPI TestClient 和 mock（`app/frontend/mock/api.ts`）上各跑一遍、逐字段比对；normalize 用例同时跑 TS（`app/frontend/src/timeline/model.ts`）与 Python（`app/backend/sequences/normalize.py`）。改任一侧的响应形状或 normalize 规则，先跑它。归一化规则在 `app/tests/test_contract.py` 与 `app/frontend/contract/mock-driver.ts` 的 docstring 里各有一份 —— 这是刻意抄的两份（两种语言各执一端），改规则必须两侧同步。新增用例 = 往 `app/contract/cases/` 丢一个 JSON。本地缺 node 或 `app/frontend/node_modules` 时该文件整体 skip；CI（`.github/workflows/ci.yml`）两者都装。

**碰撞测试里的姿态不是编的** —— 是在 URDF 自己的限位盒里随机采样、留下 Pinocchio 判定相撞的构型。要加新的自碰撞用例就照这个方法找，别手写一个「看起来会撞」的姿态。

---

## 提交约定

每个 commit 结束时代码库能跑：`cd app && uv run pytest` 绿、`cd app && uv run -m backend.app --sim` 能起。

**改代码的 commit 里必须同时更新 [`PROGRESS.md`](./PROGRESS.md) 的状态**，不要分开提交，否则状态和代码漂移。

commit message 写正常英文散文，说清**为什么**这么做，尤其是偏离原计划的地方 —— 这个仓库里好几个决定的理由只存在于 commit message 里。

---

## 文档分工

| 文件 | 是什么 | 什么时候读 |
|---|---|---|
| `AGENTS.md`（本文件） | Agent 工作手册：铁律、代码地图、约定 | 开工前 |
| [`PROGRESS.md`](./PROGRESS.md) | 状态机：现在做到哪、什么被卡住、交接协议 | 接手一个 session 时 |
| [`README.md`](./README.md)（英文）/ [`README.zh-CN.md`](./README.zh-CN.md)（中文） | 人类向：装什么、怎么拍一组、配置项、部署、**故障排查**、API。项目名 **Teach & Repeat · 示教回放**（目录名不改） | 要用这个服务时；用户报故障先翻它的故障排查表。改 README 时两份同步改 |
| [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md) | **已验证**（有源码/实测证据）与**待实测**严格分开 | 碰硬件相关代码时 |
| [`app/firmware/esp32-shutter/README.md`](./app/firmware/esp32-shutter/README.md) | 烧录、配对、协议表 | 碰快门链路时 |
| [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) | 产品架构锚点：设计模式（定位 / 概念 / 分层 / 词汇） | 改交互、加插件、谈产品定位时 |
| [`docs/TIMELINE.md`](./docs/TIMELINE.md) | 时间轴编辑器设计定稿（新一版交互骨架）：块/标记模型 / 预演与执行 / 布局 / 三期路线 | 动前端界面或编排交互时 |
| [`docs/PLUGINS.md`](./docs/PLUGINS.md) | 三个扩展点：动作插件 / 触发源 / 事件订阅。写给要扩展这台机器的人 | 加动作类型、接外部触发、做集成时 |
| [`docs/rebot-policy.md`](./docs/rebot-policy.md) | 从一份 B601-RS 主从录制/回放 demo 提炼的**物理事实与策略**（限速、插值、首尾衔接、温度保护）。那份走 LeRobot，**代码一行都不能抄，抄的是数值和「为什么必须这么做」** | 写示教录制、轨迹回放、限速/过热保护时 |

`CLAUDE.md` 只是指向本文件的指针，不要往里写内容。

**每件事只写一处。** 硬件数值在 `HARDWARE_NOTES.md`、进度在 `PROGRESS.md`、用法在 `README.md`，本文件只放改代码的约定并链过去。往这里抄一份副本，副本就会先过时 —— 而这个仓库里过时得最要命的正是「为什么不能调那个看起来正确的方法」。
