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
| **当前 commit** | `#40` feat: Esp32Shutter 串口客户端 |
| **状态** | `TODO` |
| **Phase** | Phase 7 — 快门链路（sim 侧，不需要板子） |
| **上一个完成的** | `#36` test: 自碰撞 |
| **备注** | 41/62 完成，180 个测试绿。**Phase 3 急停 / Phase 4 数据模型 / Phase 6 安全校验 / Phase 8 执行器全部完成。** 剩余纯逻辑：#40/#41 串口协议（不需要板子）、#29 浮动/锁定判据、#50 首点平滑、Phase 9 前端 7 个。等硬件：#6/#7 实测、#9 ArmSession、#28 teach 移植、#37/#42 固件、Phase 10 部署、#62 Agent API。 |

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
| 10 | L | feat: `ArmDriver` Protocol + `SimArm`（一阶滞后 + 可注入"人手拖动"） | DONE | 时间注入，不 sleep。滞后用指数精确解，结果与 tick 粒度无关。速度**位置差分**算（与真机同一约束）。拖动只在浮动时生效 —— 握持的臂会抵抗，测试不能拖动锁死的臂还信结果 |
| 11 | L | test: SimArm 行为 | DONE | 18 例，含"拖三个位置录三次"的示教循环、松手停在原地、步长无关性 |
| 12 | L+H | feat: 控制循环 `Controller.tick()` | DONE（sim 侧） | `tick()` 是纯的，测试可直接调。sim 下自带线程驱动（实测 100 Hz 稳）；**真机换成上游 `start_control_loop`**，它已处理 CAN 时序。tick 抛异常不杀循环 —— 循环正是撑住臂的东西，改成 engage 闩锁后继续 tick |
| 13 | L | feat: 状态广播 + WebSocket `/ws` | DONE | 广播队列**有界且丢旧包**，慢订阅者不能反压控制线程 —— 因为浏览器标签页卡住而停摆的控制循环，就是停止撑住臂的控制循环。`/ws` 只读，指令一律走 REST，否则绕过运动闸门 |

### Phase 3 — 急停（在任何东西快速运动之前先做）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 14 | L | feat: `SafetyLatch`（engage/clear/is_latched/snapshot/record_freeze_pose），纯逻辑不碰硬件 | DONE | 冻结姿态由控制循环下一 tick 回填（`record_freeze_pose`），latch 本身不碰硬件。`source` 在边界强制转 enum |
| 15 | L | test: 闩锁状态机 | DONE | 11 例，含并发 engage 只能有一个赢家 |
| 16 | L+H | feat: 控制循环尊重闩锁，冻结姿态并继续 hold | DONE | 闩锁在**任何东西能命令臂之前**检查。一条测试走 AST 扫 `backend/` 下每个模块，出现 `.estop` 或 `.disable_all` 属性访问就失败 —— 用 AST 不用文本匹配，这样必须提到这两个名字的注释不会误触 |
| 17 | L | feat: 急停 REST 端点（`POST /api/estop` / `/clear` / `GET`），状态进 health | DONE | engage 永远 200 不 409 —— 会跟你吵架的急停是坏急停；重复 engage 返 `changed: false` 并保留首因 |
| 18 | L | feat: API 运动闸门（FastAPI 依赖，闩锁期间 409 带原因） | DONE | `backend/api/gate.py`。409 而非 503：臂不是不可用，是调用方必须显式解除的状态 |
| 19 | L | test: 闸门覆盖，**反射路由表**遍历所有运动端点，不硬编码列表 | DONE | **差点成为空转测试**：FastAPI 0.141 的 `include_router` 不把子路由摊平进 `app.routes`，而是塞进一个不透明的 `_IncludedRouter`，朴素遍历一个端点都看不见。改用 `effective_candidates()` 递归，并加一条 **OpenAPI 交叉校验** —— 下次 FastAPI 改内部结构会大声失败而不是静默失效。已实测：临时加一个未挂闸门的端点，测试确实报错 |
| 20 | L | feat: 看门狗自动触发 | DONE | 三个条件都要求**持续**而非单次：抖动一下、丢一帧、移动中误差大都是正常的。跟踪误差**只在握持时判**，移动中误差大正是移动本身。读失败不杀循环 —— 用上一帧状态继续，由看门狗决定连续多少次算丢了臂。clear 后 reset，之前积累的怀疑是关于旧情况的 |
| 21 | L | test: 看门狗 | DONE | 16 例单测 + 4 例控制循环集成。含「手动急停在先时看门狗不覆盖原因」 |

### Phase 4 — Routine 数据模型与存储

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 22 | L | feat: Pydantic 模型 `Action`(判别式联合) / `Waypoint` / `Routine` | DONE | `shutter` 默认 `on_failure=abort`。关节**名与限位不在模型里校验** —— 限位从 URDF 读（#33），模型只保证形状（非空 + 有限值，NaN 会活着穿过 JSON 然后被下发） |
| 23 | L | feat: `RoutineStore`，一 routine 一 JSON，原子写，损坏文件跳过 | DONE | tmp 文件建在同目录 —— `os.replace` 只在同一文件系统内原子，`/tmp` 经常不是。id 走白名单正则，防路径逃逸 |
| 24 | L | test: store 往返 | DONE | 21 例，含"写失败后旧版本完好且不留 tmp 残骸" |
| 25 | L | feat: Routine REST CRUD | DONE | |
| 26 | L | feat: Waypoint 编辑端点（插入/删除/改 settle 与 actions/重排序） | DONE | 按**列表下标**寻址（编辑器就是可重排列表）。重排序要求是完整置换，否则静默丢点或重复，操作员拍到一半才发现。合并用"dump + update + 重新校验"，不用 `model_copy(update=)` —— 后者跳过校验器，非法值直接落盘 |
| 27 | L | test: waypoint 编辑 | DONE | 20 例，含"急停期间仍可编辑"（这正是操作员停下来要干的事） |

### Phase 5 — 示教模式（移植上游浮动/锁定）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 28 | L+H | feat: teach 模式，移植 `example/10_gravity_compensation_lock.py`（MIT + 重力前馈 + 位置闭环，雅可比算末端速度，阈值 0.04 m/s / 0.08 rad/s 进配置） | TODO | **见决策速查 #2**；阈值依赖 B2 |
| 29 | L | test: 浮动/锁定状态机纯逻辑单测（注入速度序列，验证跟随/冻结/抖动不反复解锁 → 要迟滞或最短保持） | TODO | |
| 30 | L | feat: 录点端点 `POST /api/routines/{id}/waypoints/capture` | DONE | 不挂闸门 —— 读当前姿态写条记录，急停期间做这事无害，而且刚按下急停的人多半正想要那个姿态 |
| 31 | L | test: 示教录点 | DONE | HTTP 层拖三次录三次，顺序与角度都对 |
| 32 | L | feat: 示教期间急停 —— 浮动下 engage 立刻夹住臂 | DONE | 浮动状态下挂着急停就是一条掉下来的臂。engage 后先撤 float 再 hold |

### Phase 6 — 安全校验（复用上游运动学）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 33 | L | feat: 关节限位校验，从 URDF 读 | DONE | 容差 0.02 rad，专治 j2/j3 下限恰为 0 而静止姿态正是 q=0。**夹爪明确不校验** —— 一个电机驱两个米制平移指关节，没有标定过的角度→行程映射，硬校验等于自己编一个换算再去信它 |
| 34 | L | test: 限位校验 | DONE | 含「静止姿态扛得住编码器噪声」 |
| 35 | L | feat: 自碰撞检查 + 相邻点路径粗采样 | DONE | URDF 没有 SRDF，44 对候选里 8 对是相邻连杆（拧在一起本来就贴着）。用**静止姿态下相撞的即为结构性**来排除，剩 36 对真实的。写入时查单点，播放前查整条含中间路径 |
| 36 | L | test: 自碰撞 | DONE | 测试里的姿态**不是编的** —— 在 URDF 自己的限位盒里随机采样、留下 Pinocchio 判定相撞的构型。找到 j2≈2.87 时 link3 撞底座（臂折回自己身上），以及一对两端合法、中间穿过底座的点 |

### Phase 7 — 快门链路

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 37 | E | feat: ESP32 固件 `firmware/esp32-shutter/`，PlatformIO，行协议 `#<id> <CMD>` → `#<id> OK/ERR` | BLOCKED | B4；**必须 `-D ARDUINO_USB_CDC_ON_BOOT=1`** |
| 38 | L | docs: 固件 README（烧录 / 相机菜单 `无线通信设置 > 蓝牙功能` 设"遥控" / 协议表 / 故障） | TODO | |
| 39 | L | feat: `ShutterDriver` Protocol + `SimShutter`（可注入失败） | DONE | 失败**抛异常不返 bool** —— bool 返回值最容易在调用点被丢掉，而执行器必须区分「继续」和「停拍」。`ShutterNotConnected` 与 `ShutterTimeout` 分开：前者意味着剩下每一帧都会同样失败 |
| 40 | L | feat: `Esp32Shutter` 串口客户端（自增 id、单条在途、超时可配、id 不匹配丢弃、自动重连） | TODO | |
| 41 | L | test: 协议编解码（内存双向管道当假串口：往返/超时/ERR/id 不匹配/粘包/重连） | TODO | |
| 42 | E | feat: 快门自检端点 `POST /api/shutter/test`，health 含连接状态与固件版本 | BLOCKED | B4 |

### Phase 8 — 序列执行器

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 43 | L | feat: `RoutineExecutor` 纯逻辑（注入时钟/arm/shutter） | DONE | **急停不接进来** —— 执行器只暴露 `abort()`，由控制循环在看到闩锁时调用，这样它结构上就不可能自己决定恢复。给 `ArmDriver` 加了 `move_to(q, duration)` 与 `hold(q)` 的区分：前者播放用，后者急停用，合并会让急停和一次极快的移动无法区分。点间轨迹规划待接上游 `trajectory/`（#50） |
| 44 | L | test: 执行器时序 | DONE | 20 例，假时钟驱动零 sleep。settle 的断言写在循环内 —— 写在循环后会漏掉「settle 刚结束那一 tick 就开枪」是正确行为这件事 |
| 45 | L | feat: action 分发 + 失败策略（abort / skip / retry N） | DONE | `shutter` 默认 abort |
| 46 | L | test: action 失败分支 | DONE | 含「BLE 断链时臂走完整轮而素材全空」这个最贵失败模式 |
| 47 | L | feat: playback 接入控制循环，进度经 WS 广播 | DONE | |
| 48 | L | feat: 播放控制端点 + 播放前预检 | DONE | `play` 前跑 `validate_sequence`（每点 + 相邻点路径），不合法 400 且臂一动没动 |
| 49 | L | test: 播放期间急停 | DONE | **端到端跑通**：播到一半 engage → 执行器 abort + 臂冻结 → 再 tick 一千次纹丝不动 → clear → 再 tick 一千次仍是 idle 且停在冻结姿态。真机上要另跑一遍 |
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
| 2 臂层 | 5 | **4** | 3 |
| 3 急停 | 8 | **8** | 7 |
| 4 数据模型 | 6 | **6** | 6 |
| 5 示教 | 5 | **3** | 4 |
| 6 安全校验 | 4 | **4** | 4 |
| 7 快门 | 6 | **1** | 4 |
| 8 执行器 | 8 | **8** | 8 |
| 9 前端 | 7 | 0 | 7 |
| 10 部署 | 4 | 0 | 1 |
| 11 Agent API | 1 | 0 | 1 |
| **合计** | **62** | **41** | **51** |
