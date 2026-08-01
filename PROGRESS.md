# PROGRESS — rebot-copilot-camera

**这是项目的状态机。任何 agent 接手前先读完本文件。**

完整设计与理由在 [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1)。
本文件只回答三个问题：**现在在哪 / 下一步做什么 / 什么被卡住了**。

---

## 交接协议

接手一个 session：

1. 读本文件 `▶ 当前` 段 —— 得到当前 commit 编号。
2. 读 issue #1 的 `## Commits` 段里对应编号的描述 —— 得到该 commit 的完整要求。
3. 检查 `🚧 阻塞` 段 —— 该 commit 是否依赖未解决的阻塞项。
4. 做完 → **在同一个 git commit 里把本文件的状态一起改掉**，不要分开提交，否则状态会和代码漂移。
5. 更新 `▶ 当前` 指向下一个 `TODO`。

状态值：`TODO` 未开始 / `WIP` 进行中 / `DONE` 已提交 / `BLOCKED` 被阻塞项挡住 / `SKIP` 明确跳过（写理由）。

**规则**：
- 一次只有一个 `WIP`。中断时 `WIP` 行的备注里写清楚"做到哪了"。
- 每个 commit 结束时代码库必须能跑（`uv run pytest` 绿、`uv run -m backend.app --sim` 能起）。
- `H` / `E` 标记的 commit 在开发机上做不完 —— 写代码可以，验证要等硬件。不要因为跑不了就跳过写测试。

---

## ▶ 当前

| 字段 | 值 |
|---|---|
| **当前 commit** | `#10` feat: SimArm |
| **状态** | `TODO` |
| **Phase** | Phase 2 — 臂层封装 |
| **上一个完成的** | `#8` docs: 硬件陷阱记录 |
| **备注** | Phase 0 全通（`uv sync` + `uv run pytest` 7 绿）。#6/#7 卡真机。**建议下一步不按编号走，直接跳 Phase 3（急停）和 Phase 4（数据模型）** —— 那 14 个 commit 全是纯逻辑零硬件依赖，且急停是全项目唯一"错了会撞坏硬件"的部分。#9 ArmSession 要连真臂才验证得了，先做 #10 SimArm 把地基垫上。 |

---

## 环境标记

| 标记 | 含义 | 现状 |
|---|---|---|
| `L` | 开发机就能做完（写码 + 单测 + sim） | 可用（macOS） |
| `H` | 需要真臂 / r2x / CAN 总线才能验证 | **暂不可用** |
| `E` | 需要 XIAO ESP32-S3 板子才能烧录验证 | 状态未知 |

`H` / `E` 的 commit 分两半：代码和测试在开发机写完并标 `DONE`，实机验证记进 `docs/HARDWARE_NOTES.md`。若实机验证推翻了实现，开新 commit 修，不回改状态。

---

## 🚧 阻塞 / 待验证

| # | 项 | 挡住哪些 commit | 怎么解 |
|---|---|---|---|
| B1 | CAN 接入形态未定：`config/rebotarm_rs.yaml` 写 `channel: can0`（socketcan），上游 README 又提到 USB2CAN 串口桥 `/dev/ttyACM0` | #6 #9 #59 | 在 r2x 上 `ip link show can0` + `ls /dev/ttyACM*`，跑上游 `example/2_zero_and_read.py` |
| B2 | 末端挂佳能机身后重力补偿是否还准（上游标定是**空载**，误差 5–11%） | #7 #28 | 挂机身实测浮动手感；不准则在 URDF 末端加相机等效质量/质心，或重调速度阈值 |
| B3 | R2x 上 500 Hz 控制频率能否稳住（yaml 默认值，上游没说在什么算力上测的） | #12 | 跑控制循环测实际 tick 抖动，不稳就降频并记录 |
| B4 | XIAO ESP32-S3 板子是否在手 | #37 #42 | 确认后开 Phase 7 |

阻塞项不挡纯逻辑 commit。Phase 3 / 4 / 8 全是纯逻辑，**没有硬件也能一路做完**，且是全项目最该先做的部分（急停 + 数据模型 + 执行器）。

---

## 决策速查（最容易做错的四条）

1. **急停绝不能调 `RebotArm.estop()`。** 上游那个方法实现就是一行 `self.disable_all()`（`vendor/reBotArm_control_py/reBotArm_control_py/actuator/rebotarm.py:687`），语义是电机失能、力矩归零、臂自由落体，跟本项目"保持力矩钉在原地"的需求完全相反。MotorBridge 文档同样把 `disable_all()` 写成 "Emergency stop all motors"。本项目急停 = 冻结 `q_target` + 继续 MIT + 重力补偿维持。代码里必须留注释说明为什么不用这个同名方法。
2. **不许用上游的默认资产解析。** 上游 `config/rebotarm.yaml` 的 `hardware_yaml` 指向 `rebotarm_dm.yaml` —— **B601-DM 那条臂**，URDF 和末端 frame 都不同。`load_robot_model()` 不传参会静默返回一个合法但属于错误机器人的模型，**文件存在所以不报错**，只是 FK / 重力补偿 / 碰撞全算错。一律走 `backend/assets.py`，显式传 `urdf_path=str(assets.urdf_path())`。
3. **速度不能读 `mechVel (0x701A)`。** 该固件上这个寄存器不是 rad/s。速度必须由位置差分算。浮动/锁定的判据正是末端速度，读错会导致锁不住或锁太早。
4. **不重造运动学/动力学。** FK / IK / 重力补偿 / 轨迹规划 / URDF 全部用 `Seeed-Projects/reBotArm_control_py`，本项目只调不写。

证据与更多细节见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。

---

## 全部 commit

### Phase 0 — 仓库骨架与上游依赖

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 1 | L | chore: 初始化仓库骨架（`.gitignore` / LICENSE / README / `pyproject.toml` / `.python-version`） | DONE | `uv sync` 通。Python 钉 3.11（上游 `>=3.10,<3.12`） |
| 2 | L | chore: 接入 `reBotArm_control_py` | DONE | **偏离原计划**：git 依赖装不了（无 `[build-system]`，flat-layout 撞 `urdf/`+`config/`），改用 **git submodule** 锁 `d540405` + hatchling 包映射。`pin` 4.1.0 / `motorbridge` 0.5.0 在 macOS arm64 上可用 |
| 3 | L | chore: 资产纳入 —— fork `config/rebotarm_rs.yaml`，路径解析集中到 `backend/assets.py` | DONE | **偏离原计划**：URDF 63 MB/30 STL 不复制，留 submodule。发现上游默认配置指向 DM 臂，加了 `assert_rs_model()` 守卫 |
| 4 | L | chore: FastAPI 骨架，`GET /api/health` | DONE | health 里带 URDF 路径与末端 frame，用于分辨部署 |
| 5 | L | test: 接上 pytest | DONE | 7 绿。含"health 报的是 RS 不是 DM"和 `assert_rs_model` 拒 DM 配置两个守卫测试 |

### Phase 1 — 硬件对表（验证，不是探索）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 6 | H | spike: 跑上游 example 2/9/10，记录 CAN 通道、七关节零位与符号、浮动手感、500Hz 稳定性 → `docs/HARDWARE_NOTES.md` | BLOCKED | B1 |
| 7 | H | spike: 验证末端夹相机后的重力补偿 | BLOCKED | B2 |
| 8 | L | docs: `docs/HARDWARE_NOTES.md` —— 已验证 / 待实测严格分开 | DONE | 记了 7 条已验证（含 DM 默认配置、URDF 8 自由度 vs 硬件 7 关节、j2/j3 下限为 0）+ 4 条待实测 |

### Phase 2 — 臂层封装

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 9 | L+H | feat: `ArmSession` 薄封装 `RebotArm`（load yaml / connect / enable_all / get_state），**不实现任何运动学** | TODO | 实机连通性依赖 B1 |
| 10 | L | feat: `SimArm`，同接口，一阶滞后跟随目标，可外部注入"人手拖动"量 | TODO | 无硬件开发的地基 |
| 11 | L | test: SimArm 行为（送目标位收敛 / 浮动不主动动 / 冻结保持） | TODO | |
| 12 | L+H | feat: 控制循环，用上游 `start_control_loop(control_fn, rate)`，不自己起线程 | TODO | 频率依赖 B3 |
| 13 | L | feat: 状态广播 + WebSocket `/ws`（关节角/速度/力矩、模式、实际循环频率） | TODO | |

### Phase 3 — 急停（在任何东西快速运动之前先做）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 14 | L | feat: `SafetyLatch`（engage/clear/is_latched/snapshot），纯逻辑不碰硬件 | TODO | |
| 15 | L | test: 闩锁状态机（重复 engage 保留首因 / clear 后可再 engage / 未锁时 clear 幂等） | TODO | |
| 16 | L+H | feat: 控制循环尊重闩锁，冻结 `q_target` 并继续 MIT + 重力补偿维持 | TODO | **见决策速查 #1** |
| 17 | L | feat: 急停 REST 端点（`POST /api/estop` / `/clear` / `GET`），状态进 WS 和 health | TODO | |
| 18 | L | feat: API 运动闸门（FastAPI 依赖，闩锁期间 409 带原因） | TODO | |
| 19 | L | test: 闸门覆盖，**反射路由表**遍历所有运动端点，不硬编码列表 | TODO | 新增端点漏挂时要失败 |
| 20 | L | feat: 看门狗自动触发（tick 超时 / 连续 CAN 读失败 / 跟踪误差超阈值） | TODO | |
| 21 | L | test: 看门狗，假时钟 + 会报错的假臂，三条件各一例 | TODO | |

### Phase 4 — Routine 数据模型与存储

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 22 | L | feat: Pydantic 模型 `Action`(判别式联合) / `Waypoint` / `Routine`，第一版就带 `schema_version` | TODO | |
| 23 | L | feat: `RoutineStore`，一 routine 一 JSON，原子写（tmp + rename），损坏文件跳过不挂整个列表 | TODO | |
| 24 | L | test: store 往返（CRUD / schema 版本 / 损坏容错 / 原子写不产生半截文件） | TODO | |
| 25 | L | feat: Routine REST CRUD | TODO | |
| 26 | L | feat: Waypoint 编辑端点（插入/删除/改 settle 与 actions/重排序） | TODO | |
| 27 | L | test: waypoint 编辑（重排序 / 删中间点 / 非法索引 404） | TODO | |

### Phase 5 — 示教模式（移植上游浮动/锁定）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 28 | L+H | feat: teach 模式，移植 `example/10_gravity_compensation_lock.py`（MIT + 重力前馈 + 位置闭环，雅可比算末端速度，阈值 0.04 m/s / 0.08 rad/s 进配置） | TODO | **见决策速查 #2**；阈值依赖 B2 |
| 29 | L | test: 浮动/锁定状态机纯逻辑单测（注入速度序列，验证跟随/冻结/抖动不反复解锁 → 要迟滞或最短保持） | TODO | |
| 30 | L | feat: 录点端点 `POST /api/routines/{id}/waypoints/capture` | TODO | "拖到位、松手、按一下"里的按一下 |
| 31 | L | test: 示教录点（SimArm 注入式拖动，三个位置各录一次） | TODO | |
| 32 | L | feat: 示教期间急停 —— 浮动下 engage 必须立刻夹住臂 | TODO | 专门一个测试 |

### Phase 6 — 安全校验（复用上游运动学）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 33 | L | feat: 关节限位校验，**从 URDF 读**，不手抄进 config | TODO | 手抄必然和硬件漂移。**两个坑**：URDF 8 自由度 vs 硬件 7 关节不是 1:1（夹爪一电机驱两指关节）；`joint2`/`joint3` 下限就是 `0.0` 而静止姿态正是 q=0，校验必须留容差否则误拒 |
| 34 | L | test: 限位校验（越界拒 / 边界值接受 / 错误含关节名） | TODO | |
| 35 | L | feat: 自碰撞检查（Pinocchio 碰撞模型），写入时查 + 相邻点插值路径粗采样 | TODO | 依赖 #3 的 URDF |
| 36 | L | test: 自碰撞（已知自碰姿态拒 / "两端合法中间穿模"拒） | TODO | |

### Phase 7 — 快门链路

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 37 | E | feat: ESP32 固件 `firmware/esp32-shutter/`，PlatformIO，行协议 `#<id> <CMD>` → `#<id> OK/ERR` | BLOCKED | B4；**必须 `-D ARDUINO_USB_CDC_ON_BOOT=1`** |
| 38 | L | docs: 固件 README（烧录 / 相机菜单 `无线通信设置 > 蓝牙功能` 设"遥控" / 协议表 / 故障） | TODO | |
| 39 | L | feat: `ShutterDriver` Protocol + `SimShutter`（可注入失败） | TODO | |
| 40 | L | feat: `Esp32Shutter` 串口客户端（自增 id、单条在途、超时可配、id 不匹配丢弃、自动重连） | TODO | |
| 41 | L | test: 协议编解码（内存双向管道当假串口：往返/超时/ERR/id 不匹配/粘包/重连） | TODO | |
| 42 | E | feat: 快门自检端点 `POST /api/shutter/test`，health 含连接状态与固件版本 | BLOCKED | B4 |

### Phase 8 — 序列执行器

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 43 | L | feat: `RoutineExecutor` 纯逻辑（注入时钟/arm/shutter）：移动 → 等到位 → settle → 依次 actions → 下一点 | TODO | 点间运动优先调上游 `trajectory/` |
| 44 | L | test: 执行器时序（假时钟：按序走完 / settle 等够 / 到位超时 fault / 空 routine） | TODO | |
| 45 | L | feat: action 分发 + 失败策略（`sleep` / `shutter`；**shutter 默认失败即中止**） | TODO | 静默失败会整轮素材废掉才发现 |
| 46 | L | test: action 失败分支（报错中止 / 重试第二次成功 / 超时按失败处理） | TODO | |
| 47 | L | feat: playback 模式接入控制循环，进度经 WS 广播（第几点/总数/当前阶段） | TODO | |
| 48 | L | feat: 播放控制端点，开始前对整条序列做限位与碰撞**预检** | TODO | 不合法直接 400，别让臂动起来才发现 |
| 49 | L | test: **播放期间急停**（立刻冻结 / 执行器中止 / 不自动恢复 / clear 后是 idle） | TODO | **全项目最重要的集成测试** |
| 50 | L | feat: 首点平滑接入（限速过渡，不直接下发目标位） | TODO | 继承老项目 `Transition` 模式 |

### Phase 9 — 前端

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 51 | L | feat: 前端骨架 Vite + React + TS，WS 接线，**常驻急停条**（大红按钮 + 快捷键 + 闩锁状态） | TODO | 急停是第一个做的 UI 元素 |
| 52 | L | feat: Routine 列表（建/改名/删/选中） | TODO | |
| 53 | L | feat: Waypoint 编辑器主界面（列表 + 拖拽重排 + 删除 + 大号"记录当前位置"） | TODO | |
| 54 | L | feat: 单点详情编辑（settle_ms / 运动参数 / 增删 actions，shutter 独立 UI） | TODO | |
| 55 | L | feat: 播放控制与进度条 | TODO | |
| 56 | L | feat: 3D 预览（参考 `rebot_arm_webui` 的 URDF 查看器与资产组织） | TODO | |
| 57 | L | feat: 日志抽屉与 toast（从老项目移植，这两块老代码行为是对的） | TODO | |

### Phase 10 — 部署

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 58 | H | chore: systemd unit，开机自启，只监听 127.0.0.1，依赖 CAN 就绪 | TODO | |
| 59 | H | chore: CAN 拉起 + 设备权限（udev 规则 + `ip link set can0 up type can bitrate 1000000` oneshot unit + 权限组） | BLOCKED | B1 |
| 60 | H | chore: `manage.sh`（setup/enable/push/logs/open/run，沿用老项目子命令语义） | TODO | |
| 61 | L | docs: README（快速上手/部署/烧录/协议/接线/坑清单） | TODO | |

### Phase 11 — Agent API（可选，优先级最低）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 62 | L | feat: Agent 控制权与端点（token 独占 / TTL / 看门狗，全部尊重急停闩锁） | TODO | 老项目这套设计是好的 |

---

## 统计

| Phase | 总数 | DONE | 可在开发机做完 |
|---|---|---|---|
| 0 骨架 | 5 | **5** | 5 |
| 1 硬件对表 | 3 | **1** | 1 |
| 2 臂层 | 5 | 0 | 3 |
| 3 急停 | 8 | 0 | 7 |
| 4 数据模型 | 6 | 0 | 6 |
| 5 示教 | 5 | 0 | 4 |
| 6 安全校验 | 4 | 0 | 4 |
| 7 快门 | 6 | 0 | 4 |
| 8 执行器 | 8 | 0 | 8 |
| 9 前端 | 7 | 0 | 7 |
| 10 部署 | 4 | 0 | 1 |
| 11 Agent API | 1 | 0 | 1 |
| **合计** | **62** | **6** | **51** |
