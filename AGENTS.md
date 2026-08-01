# AGENTS.md

给在这个仓库里干活的 agent。人类看 [`README.md`](./README.md)。

**这个服务能让一条 48V 的机械臂动起来。** 下面「四条铁律」里的每一条，违反了都不会报错 —— 只是结果是错的，而错的方式是臂掉下来、算错力矩、或者拍了一整轮空片。

---

## 这是什么

reBot-RS 单臂末端夹佳能相机做**自动化多视角拍摄**。人零力拖动示教点位 → 臂沿点位序列走位 → 每点稳定后经 USB 通知 XIAO ESP32-S3 → ESP32 用 BLE 冒充佳能无线遥控器按快门。

跑在 reComputer R2x 上，systemd + uv，只监听 `127.0.0.1`，外部走 SSH 隧道，**没有认证层**。

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

上游那个方法就是一行 `self.disable_all()`（`vendor/reBotArm_control_py/reBotArm_control_py/actuator/rebotarm.py:687`），MotorBridge 文档也把 `disable_all()` 写成 "Emergency stop all motors"。**语义是电机失能、力矩归零、臂自由落体。** 一条 48V 的臂举着相机，掉电就是掉下来。

本项目的急停是**保持力矩钉在原地**：冻结姿态 + 继续 MIT + 重力补偿。

`tests/test_controller.py::test_nothing_in_the_backend_ever_disables_the_motors` 走 AST 扫 `backend/` 下每个模块，出现这两个名字的**属性访问**就失败。用 AST 不用文本匹配 —— 解释这条坑的注释必须提到这两个名字。

### 2. 不许用上游的默认资产解析

上游的 `vendor/reBotArm_control_py/config/rebotarm.yaml` 里 `hardware_yaml` 指向 `rebotarm_dm.yaml` —— **B601-DM 那条臂**，不同的 URDF（`reBot-DevArm_fixend.urdf`）、不同的末端 frame（`end_link`）。

`load_robot_model()` / `load_dynamics_model()` 不传参会返回一个**合法但属于错误机器人**的模型。文件存在，所以不报错。FK、重力补偿、碰撞全部算另一条臂 —— 而重力补偿正是零力拖动的地基。

一律走 `backend/assets.py`，显式传 `urdf_path=str(assets.urdf_path())`，启动调 `assets.assert_rs_model()`。

### 3. 速度不能读 `mechVel (0x701A)`

该固件上这个寄存器不是 rad/s。速度必须由**位置差分**算。浮动/锁定的判据正是速度，读错会锁不住或锁太早。`SimArm` 也差分算，这样对着模拟器开发的逻辑在真机上行为一致。

### 4. 不重造运动学/动力学

FK / IK / 重力补偿 / 轨迹规划 / URDF 全部用 [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，**只调不写**。它是这套硬件的官方实现，重力模型已标定（承载关节误差 5–11%）。

证据与更多细节见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。

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

  core/
    controller.py   控制循环。闩锁在任何东西能命令臂之前检查
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
    estop.py        急停端点
    routines.py     序列与点位 CRUD
    control.py      播放 / 示教 / 录点 / 快门自检 / WebSocket
    agent.py        Agent 控制端点（OpenAPI 直接给 LLM 做 tool import）
    logs.py         journalctl 包装

frontend/src/       Vite + React + TS。围绕点位编辑器
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

**运动闸门**
任何会让臂动的端点必须挂 `dependencies=[Depends(require_arm_available)]`。`tests/test_motion_gate.py` 遍历路由表，未挂闸门又没在 `NON_MOTION_ROUTES` 里写明理由的端点会让测试失败。**这是设计**：新增运动端点必须做一个显式决定。

那条测试还有一层守卫 —— FastAPI 0.141 的 `include_router` 不把子路由摊平进 `app.routes`，朴素遍历一个端点都看不见，测试会永远空转通过。所以另有一条 OpenAPI 交叉校验，下次 FastAPI 改内部结构会大声失败而不是静默失效。**别删那条。**

**时间**
任何测试里不出现 `time.sleep`。时钟统一走可注入接口。执行器、闩锁、看门狗、浮动/锁定、串口客户端全部接受 `clock` 参数。

**`0.0` 是假值**
时间戳、角度、下标做判空一律用 `is None`，不要用真值判断。Agent 租约就栽在 `or now` 上 —— 时间戳恰好为 0 时所有间隔算成零、租约永不过期，而真实时钟极少读到 0，这种 bug 会潜伏很久。

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
| [`PROGRESS.md`](./PROGRESS.md) | 状态机：62 个 commit 的进度、阻塞项、交接协议 | 接手一个 session 时 |
| [`README.md`](./README.md) | 人类向：装什么、怎么拍一组、配置项、部署、**故障排查**、API、坑清单 | 要用这个服务时；用户报故障时先翻它的故障排查段 |
| [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md) | **已验证**（有源码/实测证据）与**待实测**严格分开 | 碰硬件相关代码时 |
| [`firmware/esp32-shutter/README.md`](./firmware/esp32-shutter/README.md) | 烧录、配对、协议表 | 碰快门链路时 |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | 原始设计文档与决策记录 | 想知道某个设计为什么这样时 |

`CLAUDE.md` 只是指向本文件的指针，不要往里写内容。

---

## 硬件事实

| 项 | 值 |
|---|---|
| 臂 | reBot-RS，6 关节 + 夹爪，RobStride 准直驱，48V |
| 通信 | CAN，`channel: can0`，`rate: 500`。**socketcan 还是 USB2CAN 串口桥未实测**（见 PROGRESS 阻塞 B1） |
| 关节映射 | `joint1..6` = motor_id `0x01..0x06`（1–3 型号 `rs-06`，4–6 型号 `rs-00`），`gripper` = `0x07`（`rs-00`），feedback_id 统一 `0xFD` |
| URDF | `00-arm-rs_asm-v3.urdf`，末端 frame `gripper_end`，30 个 STL 共 63 MB（留 submodule，不复制） |
| **自由度错位** | URDF `nq=8`（`joint1..6` + `joint_left`/`joint_right` 两个米制平移指关节），硬件 7 关节（夹爪一个电机）。**不是 1:1。** 夹爪不做限位校验、不给重力前馈 —— 没有标定过的换算，编一个再去信它更糟 |
| **限位边界** | `joint2`/`joint3` 下限恰好是 `0.0`，而静止伸直姿态就是 q=0。限位校验必须留容差（当前 0.02 rad），否则臂会因为「站着不动」被拒 |
| 碰撞对 | 44 对候选，8 对是相邻连杆（拧在一起本来就贴着，用「静止姿态下相撞即为结构性」排除），剩 36 对 |
| 快门 | XIAO ESP32-S3 原生 USB CDC。PlatformIO **必须** `-D ARDUINO_USB_CDC_ON_BOOT=1`，否则 `Serial` 走 UART0 引脚，主机侧收不到数据**且不报错** |
| 相机 | 佳能，机身菜单 `无线通信设置 > 蓝牙功能` 要设成「遥控」（不是「智能手机」） |

无硬件时自动 fallback 到 `SimArm` + `SimShutter`，**fallback 一定会打日志说明原因** —— 一个开开心心跑在模拟器上的服务，看起来和正常的一模一样。

---

## 当前状态

62 个计划 commit 里 **60 个已实现**，252 个测试绿。只剩 `#6` / `#7` 两个硬件实测（跑上游 example、验证挂相机后的重力补偿）—— **没有臂就是做不了，不是没做**。

上机第一步：`./manage.sh setup && ./manage.sh push`，然后看 `./manage.sh status` 报的是真臂还是模拟器，再按 `docs/HARDWARE_NOTES.md` 的「待实测」段逐条填。挂相机后重点重调 `FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益 —— 这两处做成可配就是为了这一刻。
