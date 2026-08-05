# 做到哪了

**项目状态机。接手一个 session 先读这里。**

本文件只回答三个问题：**现在在哪 / 下一步做什么 / 什么被卡住了**。
铁律与代码约定在 [`AGENTS.md`](./AGENTS.md)，当前设计模式在 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)。
[issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) 是 2026-07-31 的原始计划，**已归档不再追加** —— 读它是为了看偏离了什么，不是为了知道现在该做什么。

---

## 交接协议

接手一个 session：

1. 读 [`AGENTS.md`](./AGENTS.md) —— 四条铁律，违反了不会报错只会让结果错。
2. 读本文件 `▶ 当前` 段 —— 得到当前 commit 编号。
3. 读 issue #1 的 `## Commits` 段里对应编号的描述 —— 得到该 commit 的完整要求。
4. 检查 `🚧 阻塞` 段 —— 该 commit 是否依赖未解决的阻塞项。
5. 做完 → **在同一个 git commit 里把本文件的状态一起改掉**，不要分开提交，否则状态会和代码漂移。

**原计划的 62 个 commit 已完成 60 个**，只剩两个硬件实测。后续新增工作在表末追加行，沿用同样的编号与状态约定。

状态值：`TODO` 未开始 / `WIP` 进行中 / `DONE` 已提交 / `BLOCKED` 被阻塞项挡住 / `SKIP` 明确跳过（写理由）。

**规则**：
- 一次只有一个 `WIP`。中断时 `WIP` 行的备注里写清楚"做到哪了"。
- 每个 commit 结束时代码库必须能跑（`uv run pytest` 绿、`uv run -m backend.app --sim` 能起）。
- `H` / `E` 标记的 commit 在开发机上做不完 —— 写代码可以，验证要等硬件。不要因为跑不了就跳过写测试。

---

## ▶ 当前

| 字段 | 值 |
|---|---|
| **当前 commit** | 软件侧无待办；硬件实测见 [#2](https://github.com/Love4yzp/rebot-copilot-camera/issues/2) / [#3](https://github.com/Love4yzp/rebot-copilot-camera/issues/3) |
| **状态** | `BLOCKED` — 等真臂 |
| **Phase** | Phase 1 — 硬件对表（**唯一剩下的**） |
| **上一个完成的** | `#86` fix: 自检假红（`disconnected` 不再报「没配对」）+ `shutter_count` 改 `action_count` |
| **备注** | **84/86 完成，326 个测试绿，ruff 干净，前端 TypeScript 编译通过。** 软件侧全部完成（自检假红修了、`action_count` 改名了、`check.py` 决定记了）。只剩 #6/#7 两个硬件实测 —— 没有臂就是做不了，不是没做。**上机第一件事**：`./manage.sh setup && ./manage.sh push`，然后看 `./manage.sh status` 报的是真臂还是模拟器；接着按 `docs/HARDWARE_NOTES.md` 的「待实测」段逐条填。挂相机后重点重调 `FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益。 |

---

## 环境标记

| 标记 | 含义 | 现状 |
|---|---|---|
| `L` | 开发机就能做完（写码 + 单测 + sim） | 可用（macOS） |
| `H` | 需要真臂 / r2x / CAN 总线才能验证 | **暂不可用** |
| `E` | 需要 XIAO ESP32-S3 板子才能烧录验证 | 未确认是否在手（B4） |

`H` / `E` 的 commit 分两半：代码和测试在开发机写完并标 `DONE`，实机验证记进 `docs/HARDWARE_NOTES.md`。若实机验证推翻了实现，开新 commit 修，不回改状态。

---

## 🚧 阻塞 / 待验证

这四项**只挡验证，不挡代码** —— 相关实现都已写完并有测试，缺的是在真机上确认数值。

| # | 项 | 影响 | 怎么解 |
|---|---|---|---|
| B1 | CAN 接入形态未定：`config/rebotarm_rs.yaml` 写 `channel: can0`（socketcan），上游 README 又提到 USB2CAN 串口桥 `/dev/ttyACM0` | 挡 #6 验证。`ArmSession` 与 `rebot-can.service` 按 socketcan 写的，若实际是串口桥要改这两处 | 在 r2x 上 `ip link show can0` + `ls /dev/ttyACM*`，跑上游 `example/2_zero_and_read.py` |
| B2 | 末端挂佳能机身后重力补偿是否还准（上游标定是**空载**，误差 5–11%） | 挡 #7 验证。`FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益都做成可配就是为了这里重调 | 挂机身实测浮动手感；不准则在 URDF 末端加相机等效质量/质心，或重调阈值 |
| B3 | R2x 上 500 Hz 控制频率能否稳住（yaml 默认值，上游没说在什么算力上测的） | 只影响频率取值。sim 下实测 100 Hz 稳，真机换成上游 `start_control_loop` | 跑控制循环测实际 tick 抖动，不稳就降频并记录 |
| B4 | XIAO ESP32-S3 板子是否在手 | 挡固件烧录验证。固件与主机侧协议都写完了，协议在内存管道上有 30 个测试 | 有板子就 `cd firmware/esp32-shutter && pio run -t upload` |

---

## 决策速查

四条铁律（急停不能调 `estop()`／不许用上游默认资产解析／速度不能读 `mechVel`／不重造运动学）写在 [`AGENTS.md`](./AGENTS.md)，**只维护那一份** —— 两份副本会漂移，而漂移掉的正是这类救命细节。

源码级证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。

---

## 计划表（归档）

下面 62 行是最初规划的 commit，60 行已完成。**留着是为了对照原计划看偏离了什么**，不是待办清单 —— 待办只有 `▶ 当前` 那一格。

每行的备注只记**偏离原计划的地方和原因**；完整理由在对应的 git commit message 里，那里比这里详细。

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
| 6 | H | spike: 跑上游 example 2/9/10，实测 CAN 通道 / 零位 / 浮动手感 / 500Hz | BLOCKED | **没有臂，做不了。** 代码侧全部就绪：`ArmSession` 写好了，`./manage.sh status` 会报告跑在真臂还是模拟器上。上机后把实测值填进 `docs/HARDWARE_NOTES.md` 的「待实测」段。跟踪见 [issue #2](https://github.com/Love4yzp/rebot-copilot-camera/issues/2) |
| 7 | H | spike: 验证末端夹相机后的重力补偿 | BLOCKED | **没有臂和相机，做不了。** 浮动/锁定阈值已经做成可配（`FloatLockConfig`）并有测试覆盖，就是为了挂上相机后能安全重调。跟踪见 [issue #3](https://github.com/Love4yzp/rebot-copilot-camera/issues/3) |
| 8 | L | docs: `docs/HARDWARE_NOTES.md` —— 已验证 / 待实测严格分开 | DONE | 记了 7 条已验证（含 DM 默认配置、URDF 8 自由度 vs 硬件 7 关节、j2/j3 下限为 0）+ 4 条待实测 |

### Phase 2 — 臂层封装

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 9 | L+H | feat: `ArmSession` 薄封装 `RebotArm`（load yaml / connect / enable_all / get_state），**不实现任何运动学** | DONE（待实机验证） | 薄封装上游 `RebotArm`。dict↔ndarray 转换只在这一处，构造时对着臂自报的关节名校验 —— 这里错一位就是命令错关节。重力前馈只喂 6 个臂关节，夹爪给 0（没有标定过的行程→力矩换算，编一个塞进力矩指令更糟） |
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
| 28 | L+H | feat: teach 模式接进控制循环 | DONE（待实机调阈值） | 每 tick 跑浮动/锁定判据：**起手是锁的**（一开示教就松劲的臂，在有人扶住之前就会垂下去），测到运动才放开，停下再锁回。末端速度用最大关节速度近似而不是雅可比 —— 这是「手停没停」的判据不是测量，挂相机后本来就要重调，500Hz 每 tick 算雅可比买不到这个决定用得上的精度。**顺带修了 SimArm 的失真**：原来握持时忽略 drag，导致示教在模拟器里永远起不来 —— 真机上「握持」是有限刚度的 MIT，人推得动，而推动一条**握持中**的臂正是示教的起点 |
| 29 | L | feat+test: 浮动/锁定判据 `FloatLock`（纯逻辑） | DONE | 在上游基础上加了两样，都因为朴素版本在手上会出问题：**迟滞**（手搭在静止的臂上，速度正好在阈值附近来回，臂会一秒抖好几次，像在跟你较劲）和**最短静止时间**（拖动中每次换方向都会经过零速，在那里锁死会把臂停在半路）。阈值可配 —— 挂了相机就得重调，有测试才敢调 |
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
| 37 | E | feat: ESP32 固件 `firmware/esp32-shutter/`，PlatformIO，行协议 `#<id> <CMD>` → `#<id> OK/ERR` | DONE（待烧录验证） | PlatformIO 工程 + 40 行固件。`platformio.ini` 带 `-D ARDUINO_USB_CDC_ON_BOOT=1` 及原因注释。超长行整条丢弃不截断 —— 截断后的命令可能正好解析成另一条合法命令 |
| 38 | L | docs: 固件 README（烧录 / 相机菜单 `无线通信设置 > 蓝牙功能` 设"遥控" / 协议表 / 故障） | DONE | 烧录 / 相机菜单路径 / 协议表 / 坑，全在 `firmware/esp32-shutter/README.md` |
| 39 | L | feat: `ShutterDriver` Protocol + `SimShutter`（可注入失败） | DONE | 失败**抛异常不返 bool** —— bool 返回值最容易在调用点被丢掉，而执行器必须区分「继续」和「停拍」。`ShutterNotConnected` 与 `ShutterTimeout` 分开：前者意味着剩下每一帧都会同样失败 |
| 40 | L | feat: `Esp32Shutter` 串口客户端 + 行协议 | DONE | transport 注入，整套协议在内存管道上测。**id 是核心**：没有它，超时后迟到的回包会被当成下一条请求的成功回执 —— 表现是「偶尔少一帧」，现场几乎查不出来。收到 `READY` 说明板子重启、BLE 配对丢了，在途命令是**作废**而不只是迟到 |
| 41 | L | test: 协议编解码 | DONE | 30 例。含半行、粘包、二进制噪声、固件往同一串口打日志不能搞挂链路、写失败后下次调用自动重连 |
| 42 | L | feat: 快门自检端点 `POST /api/shutter/test` | DONE | 默认只 ping 不开枪，`?shoot=true` 才真拍 —— 可以确认链路而不浪费一帧。**失败返 200 带 error 而不是抛异常**，现场架机器时要的是一句话诊断。真板子上再验一次 |

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
| 50 | L | feat: 首点平滑接入 | DONE | 后面每个点都是从上一个点出发的，时长是对着已知起点定的；第一个点没这个保证 —— 臂在示教留下的任意位置，可能隔半个工作空间。到首点的时长按最大关节速度拉长 |

### Phase 9 — 前端

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 51 | L | feat: 前端骨架 Vite + React + TS，WS 接线，**常驻急停条**（大红按钮 + 快捷键 + 闩锁状态） | DONE | 常驻急停条 + Esc 快捷键。WS 自动重连并显示连接状态 —— 操作员站在臂边不在浏览器边，断线不能留一个看起来是实时的冻结姿态 |
| 52 | L | feat: Routine 列表（建/改名/删/选中） | DONE | 双击重命名、右键删除（带确认 —— 每条序列都是人站在臂边一个个拖出来的） |
| 53 | L | feat: Waypoint 编辑器主界面（列表 + 拖拽重排 + 删除 + 大号"记录当前位置"） | DONE | 拖拽重排在前端拼完整置换后再发，后端只接受置换 |
| 54 | L | feat: 单点详情编辑（settle_ms / 运动参数 / 增删 actions，shutter 独立 UI） | DONE | settle / 到位用时 / 备注 / actions 增删改，shutter 有独立的对焦与失败策略 UI |
| 55 | L | feat: 播放控制与进度条 | DONE | 播放/停止/示教/测快门 + 进度条与阶段 |
| 56 | L | feat: 3D 预览（参考 `rebot_arm_webui` 的 URDF 查看器与资产组织） | DONE | three.js + urdf-loader，URDF 走新加的 `/assets/urdf` 挂载读 submodule，不打包 63 MB。选中点位时预览该姿态而不是实时姿态 —— 那才是按播放前想问的问题 |
| 57 | L | feat: 日志抽屉与 toast（从老项目移植，这两块老代码行为是对的） | DONE | toast + 日志抽屉。`/api/logs` 读 journalctl；开发机上没有 journal 会明说，而不是显示成空日志。journalctl 非零退出会提示查 `systemd-journal` 组 —— 那是最常见的原因且本身没有报错 |

### Phase 10 — 部署

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 58 | H | chore: systemd unit，开机自启，只监听 127.0.0.1，依赖 CAN 就绪 | DONE（待实机验证） | `deploy/rebot-copilot-camera.service`，依赖 `rebot-can.service` —— CAN 没起来会让服务静默 fallback 到模拟器，看起来和正常一模一样 |
| 59 | H | chore: CAN 拉起 + 设备权限（udev 规则 + `ip link set can0 up type can bitrate 1000000` oneshot unit + 权限组） | DONE（待实机验证） | `rebot-can.service` oneshot + `99-rebot-usb.rules`。USB2CAN 和 ESP32 都是通用 CDC，插拔顺序会换号，符号链接防止快门驱动指到 CAN 桥上 |
| 60 | H | chore: `manage.sh`（setup/enable/push/logs/open/run，沿用老项目子命令语义） | DONE | 子命令语义沿用老项目。`push` 保护 `routines/` —— 那是只存在于设备上的操作员劳动成果。`status` 报告跑在真臂还是模拟器上 |
| 61 | L | docs: README（快速上手/部署/烧录/协议/接线/坑清单） | DONE | 快速上手 / 操作流程 / 部署 / 架构 / 坑清单 / API 表。四条会静默出错的坑单独列在最前 |

### Phase 11 — Agent API（可选，优先级最低）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 62 | L | feat: Agent 控制权与端点（token 独占 / TTL / 看门狗，全部尊重急停闩锁） | DONE | token 独占 + 双重 TTL + UI 强制收回。测试逮到一个 bug：耗时用 `or now` 算，而 `0.0` 是假值，所有间隔都成了零、租约永不过期 —— 真实时钟极少读到 0，会一直潜伏 |

### 后续新增（原计划之外）

| # | 环境 | 描述 | 状态 | 备注 |
|---|---|---|---|---|
| 63 | L | feat: 前端无后端预览 `npm run dev:mock`（内存 mock API + WS 状态流 + 本地 URDF 静态） | DONE | **偏离原计划**：为了不启动后端也能看前端界面。vite 插件只在 `--mode mock` 挂载，`npm run dev` 行为不变；3D 臂读 vendor submodule 的 URDF/STL。播放/示教/急停在 mock 里走同一状态机，行为对齐后端（estop 时运动端点 409、空 routine play 400） |
| 64 | L | docs: `docs/ARCHITECTURE.md` 产品架构锚点（设计模式定稿） | DONE | 重新定位：通用可编程空间定位平台，不是「摄影臂」。内核/交互骨架/插件三层不变量，锚点/动作/集合/编排四个无领域积木，配置与使用分层，模板机制，界面词汇中性化为「锚点」。issue #1 标为历史记录不再追加，新设计写 ARCHITECTURE.md |
| 65 | L | feat: goto 端点 + ShutterAction 连拍（count/interval_s） | DONE | 使用层原子操作的后端：`POST /api/routines/{rid}/waypoints/{index}/goto` 构造单锚点临时 Routine 喂给现有 executor —— 到位判定/settle/首段限速/急停 abort 全复用，不新增控制逻辑。挂运动闸门，goto 前用 `validate_sequence([当前位, 目标位])` 预检路径。ShutterAction 加 count/interval_s（带默认值，旧 JSON 兼容），executor 内循环连拍、每帧重新对焦（连拍就是因为被摄体在动）。+8 测试 |
| 66 | L | feat: 前端「锚点卡片板」重构（交互骨架落地，设计见 `docs/INTERACTION.md`） | DONE | 从「脚本编辑器」到「锚点卡片板」：使用/配置双模式，使用模式点卡即 goto；新增 AnchorBoard/AnchorCard/CollectionBar/ControlBar/TeachSheet/AnchorEditSheet，删除 WaypointEditor/RoutineList/PlaybackBar/JointReadout。参数隐藏：速度三档映射 duration_s，settle/on_failure/retries/timeout 不再暴露；杀光 prompt/confirm/双击/右键/HTML5 拖拽，触屏 ≥44px。四方位模板做成示教向导，录完即溶解成普通锚点。mock 同步支持 goto |
| 67 | L | fix: `.gitignore` 的 `routines/` 改为 `/routines/` | DONE | **pre-existing bug**：未锚定的 `routines/` 模式把 `backend/routines/`（models.py、store.py，核心源码）也吞了 —— 这两个文件从未进过 git，全新 clone 直接起不来。锚定到根后恢复可见；下次 commit 需 `git add backend/routines/` |
| 68 | L | feat: 急停按钮升为视觉一等公民（大触控目标 + 自适应 + 锁定态脉冲） | DONE | 之前结构上是一等（常驻顶栏、独占红色、Esc）但尺寸只是普通工具栏按钮。改为 64px 高、`clamp()` 随视口放大字号与宽度、`touch-action: manipulation` 免双击缩放延迟；窄屏先藏提示文字和 Hz，按钮不缩。锁定态给「解除急停」加 1.6s 呼吸脉冲 —— 演播室另一头也要能看见臂已停；常态不闪（暗室里常闪是干扰）。`.app` 高度补 `100dvh` 修 iPad Safari 工具栏算进 vh 的问题。硬件物理按钮不需后端改动：`POST /api/estop` 已带 `source` 字段，插件直接打这个端点 |
| 69 | L | feat: 锚点卡片撑满卡片板（launchpad 式主控件，不再是指甲盖磁贴） | DONE | 用户截图反馈：3 张 96px 小卡片缩在巨大空白左上角。卡片是使用层唯一动词，改成 `auto-fit` + `grid-auto-rows: minmax(160px, 1fr)` + `min-height: 100%` —— 锚点少（常见 4 个）时卡片摊满整板，多了每行至少 160px 溢出滚动；`auto-fit` 让空列坍塌，3 张卡平分整行宽。卡名 `clamp(20px, 1.2vw+12px, 28px)`。纯 CSS 改动 |
| 70 | L | fix: 界面对臂的位置说谎（卡片状态机 + 急停被遮罩吞点击） | DONE | **安全**：弹层遮罩 z-index 40 盖在急停条上，示教（双手在臂上）恰好是最需要急停的模式，而屏幕急停按钮点不动 —— 急停条提到 60。弹层补 Esc/点背景关闭、焦点陷阱、`aria-modal`；Esc 用**原生**监听在面板上截停（React 合成事件的 `stopPropagation` 拦不住 window 级急停监听）。**状态可信度**：`arrived` 原本由 `phase == null` 推出，点卡瞬间就亮「已到位」；controller 保留 finished executor 会持续重播上一次的 `done`，所以 `pending` 只被*运行中*的 phase 解除、超时则丢弃声明而非升级；急停/示教立即作废 `target`。`_advance_waypoint` 先自增后判断导致收尾时 index 越界一位，前端夹紧。急停/忙时卡片不再静默失效，分别给出不同原因 |
| 71 | L | feat: 颜色改为状态通道 + tally 条（视觉系统重做） | DONE | 原配色是 GitHub 深色主题套在机械臂上：蓝色 accent 同时是按钮/进度/选中/链接，而「臂正朝人移动」只配到柔和蓝呼吸。改为底盘全灰阶、**删除品牌色**，四个彩色各自独占一个机器状态（红=急停 / 琥珀=在动 / 绿=到位 / 白=快门），借用 tally 灯与机械警示灯约定。签名元素 **tally 条**：顶端 4px 满幅光带报机器状态 —— 操作者站在臂旁读不到 12px 状态字，但两米外余光能读一条横贯屏幕的光带。单锚点进度移进卡片内部（反馈回到手指落下的地方）；配置模式换成工作台底纹而非虚线边框（前注意级别区分「点了臂会不会动」）；示教弹层改底部横条并自带急停；3D 收进抽屉，预览由 hover 改选中语义；数字键 1–9 触发锚点；删除锚点改 8 秒撤销（复用 `POST /waypoints` 的 `index`）。无障碍补 `:focus-visible` / `prefers-reduced-motion` / `--mark-dim` 提到 7:1。自托管 Saira Condensed latin 子集（设备离线，不能挂 CDN）。顺手修：mock 结束时把 playback 置 null，与真 controller 不一致，导致「已到位」在预览里永远出不来 |
| 72 | L | fix: 3D 视图从来没画出过臂（三个叠在一起的 bug） | DONE | 一直以为是 submodule 没拉 —— 网格文件其实在。真因三层：① mesh 路径用 `packages` 解析，但那只管 `package://` URI，这个 URDF 写的是相对包根的普通相对路径，要用 `workingPath`；② `workingPath` 是**拼接**不是 join，少个尾斜杠就拼成 `…-v3meshes/`；③ 最隐蔽的一个 —— `{status && <div/>}` 在 `status === ""` 时求值为**空字符串**而非 `false`，React 把它当文本子节点走 `setTextContent(node, '')` 快路径，**把命令式 append 的 canvas 一起抹掉**，所以模型加载成功了却连画布都没有。改成显式 `? :` 三元。另外抽屉原本 `position: fixed; top: 0` 盖住急停条和底栏右端，改为与卡片板共享一个定位行，结构上只能盖住卡片。renderer 构造加 try/catch —— 黑框必须自己说明为什么黑 |
| 73 | L | fix: 动作离开控制循环（插件系统的地基） | DONE | **pre-existing bug，可复现**：executor 在 `Controller.tick()` 里直接调快门驱动，而 `Esp32Shutter.shoot()` 等相机 BLE 唤醒最多 6 秒 —— 控制循环正是撑住 48V 臂的东西。实测基线：5 连拍配上每帧阻塞 600ms 的驱动，最坏 tick 间隔 **619ms**，越过 watchdog 的 `late_tick_grace_s=0.5` → **急停触发，5 帧只打出 2 帧，整轮中止**。一台仅仅是慢的相机，看起来和丢了臂一模一样。改后同场景最坏间隔 **13ms**，5 帧全中，不触发。新增 `backend/actions/`：`ActionProvider` Protocol（`ActionContext` 里**没有 arm 句柄** —— 插件结构上够不到运动闸门，与「闩锁不进 executor」同一手法）、`ThreadedRunner`（每 provider 一条 worker，一次一个任务；超时在**读侧**判定，因为 Python 杀不掉线程，超时后该 provider 停用到线程返回，而不是让下个锚点排在尸体后面）、`ShutterProvider`。executor 由「直接调用」改为「投递 + 每 tick 轮询」；连拍分帧仍留在 executor，好让急停落在**帧与帧之间**。`Job.error` 也结算截止时间 —— 只读 error 会拿到 `None`，读起来像成功。runner 的时钟**不跟控制循环的时钟**：动作截止时间量的是 provider 真跑了多久，那是墙钟。既有测试**断言一行没改**，只换了 fixture 接线（假时钟的 fixture 用 `InlineRunner`；线程隔离由 `test_action_runner.py` 单独证明）。+13 测试 |
| 74 | L | fix: `--sim` 里的模拟臂从来没动过 | DONE | **pre-existing bug**：`Controller.tick()` 只**读**臂，而 `SimArm.step(dt)` 要调用方驱动 —— 服务里没有任何东西调它。后果：`uv run -m backend.app --sim` 起来后每一次 play/goto 都以 `waypoint 0 not reached within 6.0s` 收场，示教/录点/播放这条主流程在真服务上一步也走不通（PROGRESS 里「全程实测过」那句因此复现不出来）。加 `SimArm(self_driven=True)`：**有人读它时它自己追上时钟**。为什么不用线程 —— 线程版没法用假时钟测，而 read_state 版可以，于是「服务能播完一条 routine」变成一条确定性测试（只 tick controller，不碰 `arm.step()`）。默认仍是 `False`：测试自己拿着时钟，一个会自走的臂会让所有时序断言取决于谁最后读过它。`create_arm` 的两条 sim 路径都走 `_sim_arm()`，服务拿到的一定是自走的。+5 测试 |
| 75 | L | fix: `Esp32Shutter` 从来没接进服务 + `create_shutter()` 工厂 | DONE | **pre-existing bug**：`app.py` 只构造 `SimShutter()`，没有对应 `create_arm()` 的工厂 —— 真板子插上也用不到，走的还是模拟快门。加 `backend/shutter/factory.py`（`SerialTransport` 包 pyserial，非阻塞打开，deadline 归驱动管）。**与臂工厂刻意不同：快门永不回落。** 臂回落是对的（整个服务需要一条臂）；快门回落等于 SimShutter 把每一帧都报成拍到了，操作员走完整轮才在卡上发现空的 —— 这正是仓库里说的「最贵的失败模式」。所以只有 `--sim` 能拿到模拟快门；否则返回未打开的真驱动，`Esp32Shutter` 懒连接，板子不在时第一条命令抛 `ShutterNotConnected`，表现为动作失败和自检报红，而不是沉默。`Controller.set_shutter()` 把 driver 和 provider **一起换** —— 只换 `self.shutter` 会留下 runner 里包着旧 driver 的 provider：自检打真板子、routine 却还在往模拟器开枪，而且什么都不会抛。health 加 `shutter.simulated`。+3 测试 |
| 76 | L | feat: 插件注册表 + `PluginAction` + `GET /api/plugins` + 两道预检 | DONE | 三层里的第三层落地（`docs/ARCHITECTURE.md`）。`ActionRegistry` 只管**发现与健康**，「装了哪些 provider」的唯一登记处仍是 runner —— 两份清单会漂移，而漂移之后操作员读的那份不是真正会跑的那份。`entry_points` group `rebot.actions`：`uv pip install` 加重启即可，宿主与前端零改动。**坏插件不挡启动**（少一个配件不等于少一台机器）但**绝不静默消失** —— 加载失败/自检失败都留在 manifest 里带原因，消失了操作员会以为自己配错了。`register()` 不再 probe：probe 会 ping 板子，藏在接线里的副作用没人预测得到；改成启动显式 `probe_all()`、`POST /api/plugins/probe` 刷新、其余按需惰性并缓存（预检每点一次锚点就跑一遍，那里放一个串口往返等于把板子的超时挡在操作员手指前面）。`PluginAction` 进判别式联合，`ShutterAction` **保持具体类型**不被吞掉。**两道预检**，都是为了让错误离开 ACTING 阶段（臂已在锚点、被摄体在等）：写入时按 provider 自己的 `params_model` 校验 → 400；播放/goto 前查 provider 装没装、可用不可用 → 400 且臂一动没动。executor 的 `_Dispatch` 把 repeat/interval 留在外面 —— provider 内部循环会变得不可打断，整个连拍会在急停被注意到之前打完。provider 可声明 `retryable = False`（闪光没回电、料已放出），宿主把 retry 降级为 abort 并出声，而不是默默忽略设置。加 `uv run -m backend.actions.check` —— 插件作者的无硬件开发循环（列 manifest / 跑自检 / 真 runner 真超时跑一次）。+14 测试 |
| 77 | L | feat: 语义事件流 `/api/events` + 触发来源 `source` | DONE | 三个扩展点的后两个。**事件订阅**：`/ws` 流的是*状态*（20Hz 关节角 + 播放进度），屏幕要的是那个；集成方不要。归档照片、驱灯、记流水的进程想知道「拍了一帧」，不想从位置流里反推，更不该为此在棚里的网线上吃 20Hz 关节角。于是 `Broadcaster` 加 topic 过滤，`/ws` 订 `{state, playback}`，新的 `/api/events` 订 `{event}` 并**脱掉信封**（每条都是 event 时 `type` 字段是第三方客户端要跳过的噪音）。三条硬规则写在 `backend/core/events.py`：单向不可否决（能否决的钩子就是第三方代码进了「臂动不动」的决策路径）、有界丢旧包（因订阅者不读而停摆的循环就是停止撑住臂的循环）、**在知道事实的地方发**（routine/action 从 executor 发，急停从控制循环的闩锁跳变发 —— 不从 `SafetyLatch` 发，它是纯逻辑，给它一个 broadcaster 就是在那堵墙上开第一个洞；`teach.captured` 从 HTTP 端点发，控制循环看不见手动录点）。provider 可用 `ctx.emit` 报自己的事实（`shutter.fired`），宿主不必知道那是什么。**触发来源**：`play`/`goto` 收可选 `source`，进日志、进 `/api/control`、进 WS。它**不授予任何权限**（急停照样 409），只为回答「臂刚才为什么动了」—— 一台能被卡片/agent/脚踏/脚本多路触发的机器上，这是第一个被问到、事后又最答不出来的问题。+10 测试 |
| 78 | L | feat: 锚点编辑面板改由 manifest 渲染 + 动作可排序 | DONE | 触发那半边不再为快门硬编码，改从 `GET /api/plugins` 画。新增 `ProviderFields.tsx`：三种控件 `switch` / `stepper` / `tiers` —— **正好是这张面板本来就有的三种**，所以不是新造 UI 框架，是把已有控件参数化，每个 provider 因此白拿 ≥44px 触控目标、`:focus-visible`、`prefers-reduced-motion`。**不接受插件提供的 JS/HTML**：插件带样式的第一件事就是给按钮上品牌色，而这里颜色是状态通道（四色各占一个机器状态），不是调色板。`when` 用 `{key, min}` 结构而不是表达式串 —— 让界面 eval 一个装上来的包给的表达式是麻烦的开始。动作从「只有一个快门」变成**有序列表**：顺序即执行顺序，把继电器排在快门前面就是「先做这个」，不需要 pre-hook 机制。缺失/不可用的 provider **虚线灰显带原因，不隐藏** —— 锚点确实带着这个动作，行消失了操作员会以为配置丢了；仍允许添加（现场常常先排好班再插配件），拦在播放前的预检那里，因为臂是在那里才有风险的。`play`/`goto` 带 `source`。mock 同步补 `/api/plugins`（形状必须跟后端对齐，否则预览的是一个不存在的应用）。 |
| 79 | L | docs: `docs/PLUGINS.md` + 三份指南对齐 | DONE | 插件层落地后把「怎么扩展这台机器」写成一份，写给第三方开发者而不是写给自己：三个扩展点为什么是三个不是一个 hook 链、四十行的转台插件完整例子、`uv run -m backend.actions.check` 无硬件开发循环、契约违约对照表（挂死/乱抛/probe 崩/想拿臂）、为什么 `ActionContext` 里没有 arm、为什么连拍不在 provider 里循环、为什么触发源不能是进程内回调。`AGENTS.md` 加两条约定（动作绝不跑在控制循环上、插件够不到臂）+ 代码地图 + 文档分工表；`ARCHITECTURE.md` 把插件从「后做」改成已落地并指过去；`README.md` 补 goto/plugins/events 三行 API 与扩展入口。每件事仍只写一处 —— PLUGINS.md 只讲扩展点，概念在 ARCHITECTURE，交互在 INTERACTION，铁律在 AGENTS。 |

| 80 | L | fix: 三个部署层 bug（CLI 默认、CAN unit 退出码、rsync 排除规则） | DONE | ① `app.py` 的 `--host/--port` 硬编码默认 127.0.0.1/18790，**把 `REBOT_HOST`/`REBOT_PORT` 环境变量整个遮蔽了** —— README 配置表写了它们，实际不生效；默认改从 `config.HOST/PORT` 取。② `rebot-can.service` 的 `SuccessExitStatus` 只认 0/2，但 `ip link` 找不到设备时退 **1**（USB2CAN 串口桥的机器没有 can0）—— 那种机器上 boot 会因 CAN unit 报 failed。③ `manage.sh` 的 rsync `--exclude 'routines/'` 未锚定，把 `backend/routines/`（Python 包，核心源码）也排掉了 —— push 到设备上的代码缺包；锚定为 `/routines/`（与 #67 的 `.gitignore` 是同一个坑的另一半） |
| 81 | L | docs: 项目定名 **Teach & Repeat · 示教回放**，README 双语 | DONE | 旧标题「机械臂自动多视角拍摄」描述的是第一个落地场景而不是产品本身（#64 已重新定位为通用可编程空间定位平台）。定名栈：口号「教它走一遍，它替你走一万遍」对外，Teach & Repeat（示教回放，机器人领域标准术语）作术语名。`README.md` 改英文主版 + 新增 `README.zh-CN.md` 中文全文，顶部互链；`AGENTS.md` 登记双语结构（两份同步改）。**repo 目录名 `rebot-copilot-camera` 暂不改**，service 文件名、frontend 标题等跟随项留到改名时一起处理 |
| 82 | L | fix+feat: 插件层契约补完（宿主加固 + 可安装的例子插件 + 配件重检） | DONE | **汇报先于动手**：三个扩展点都通了，但契约声称的隔离只覆盖了 import + 构造 + `run()`。四个洞，都是「宿主假设第三方代码会崩」在实现里不成立的地方：① `discover()` 里 `provider.id` 读在 try **外面** —— 一个属性拼错的插件让 `AttributeError` 冒出 `discover()`，**服务起不来**，而契约表写着「import 时就崩 → 服务照常起来」；② `runner.register` 是裸 dict 赋值，插件声明 `id="shutter"` 就**静默顶掉内置快门**，而 executor 把每个 `ShutterAction` 发给字面量 `shutter` —— 走完整轮、什么都不抛、素材在别处；③ `probe()` 跑在调用线程上，挂死的自检会卡住 `GET /api/plugins`、刷新端点和**臂动之前的预检** —— runner 整个模块就是为了防这种失败，却在自己头顶漏了一层；④ `manifest()` 里 `fields()` 无保护，一个插件抛异常 → 500 → **整张编辑面板空白**。修法：`check_shape()` 在注册前只看属性不调方法（为了决定是否接受第三方代码而先跑第三方代码，正是①的成因），重名一律拒绝并列出原因（`replace=True` 只留给宿主换自己的内置快门），probe 走 provider 自己的 worker 和读侧超时（`PROBE_TIMEOUT_S=5`，`probe_all` 并行提交；忙则 `ProviderBusy`，保留上次结论而不是记成坏），`fields()` 单独兜住。manifest 加 `installed` 区分「宿主根本没拿到」与「拿到了但此刻不可用」—— 前者不给添加（没有 `params_model` 可校验，写入必被拒），后者可以先排班后插配件。**例子插件从散文变成真包**：`examples/rebot-plugin-turntable/` 进开发环境，`tests/test_plugin_packaging.py` 走**真的 `importlib.metadata`** —— 打包元数据（group 名、`module:Class`、必须无参可调）此前一次都没被跑过，第一个踩的人会是照文档在设备上装的人。前端：`POST /api/plugins/probe` 后端做了但**前端零调用点**，配件插回来只能重启服务，补「重新检测配件」。+9 测试（314） |
| 83 | L+E | feat: 快门的 BLE 那一半接进服务（配对端点 + 相机状态），并把写死的接线解耦 | DONE | **检查结论**：拍摄链路（固件 / 行协议 / 驱动 / provider / 自检端点 / 30 个协议测试）早就完整，**缺的是蓝牙那一半** —— `PAIR` 与 `STATUS` 固件有、`Esp32Shutter` 也有，但没有端点、没有界面、`SimShutter` 里根本不存在。三个后果：① 配对只能开串口终端，而固件 README 里「配对是一次性动作」这个前提是错的 —— 板子一重启就丢配对，`READY` 横幅存在的理由正是这个，于是**唯一的恢复手段偏偏是报告故障的那块屏幕干不了的事**；② `/api/shutter/test` 的 `connected` 是 **USB 链路**，而 `PING` 按设计不碰相机（要让「相机睡了」和「板子没了」可区分），所以**什么都没配对的机器上自检报绿** —— 正是这个端点要抓的那种沉默故障；③ 模拟器没有相机这一层，整条配对流程无法在无硬件下开发或测试。修：`ShutterDriver` 补 `pair()` / `camera_connected()`（BLE 是接口的一部分，不是 Esp32 的 extra），`SimShutter` 跟上并支持「相机掉了」「配不上」两种脚本；`POST /api/shutter/pair`（播放中 409 —— 板子一次答一条命令，30 秒扫描会把后面的帧堵住）；自检改为 `PING` + `STATUS` 两段分开报，没相机就 `ok: false`；前端补「配对相机」按钮，mock 同步。**解耦**：`SHUTTER_PROVIDER_ID` 取代 executor 里两处字面量 `"shutter"`；`ShutterParams.from_action()` 把快门的字段名收回 provider 自己那一侧，executor 不再知道快门有哪些设置；串口/波特率进 `config.py`（`REBOT_SHUTTER_PORT` / `_BAUD`），驱动层不再自己读环境变量；固件的 BLE 名、扫描秒数、波特率改成 `platformio.ini` 的 build flag —— 同一个房间放两台机器时，两块板子叫同一个名字，相机会跟先看见的那块配上。+7 测试（321）|
| 84 | E | fix: 固件第一次真正编译过，三个只有编译/读源码才看得见的 bug | DONE | 固件此前**从未编译过**，`pio run` 一跑就现三件事。① **`platform` 没钉版本**：解析到 55.x（Arduino core 3.3.9），而佳能库 `pair()` 里两处 `BLEDevice::setEncryptionLevel` 在 core 3.x 已搬去 `BLESecurity` —— 编译错误报在库自己的源码里。钉 `espressif32@6.11.0`（2.0.x core 那条线的最后一版），库依赖的 tag 也钉成 `#1.0.2`。② **`isConnected()` 当 `SHOOT` 的闸门是死锁**：`CanonBLERemote::init()` 只从 NVS 读回相机地址、不建立连接，连接由 `trigger()` / `focus()` **惰性**发起 —— 用它挡，等于把唯一能建立连接的那次调用挡在门外，开机后板子**永远**连不上，每帧都回 `ERR camera not connected`。改成挡「有没有配对过」（`getPairedAddressString()`），连接交给 `trigger()`。③ **`reply(id, camera.pair(...) ? "OK" : "ERR", camera.isConnected() ? ... )`**：C++ 不规定函数实参求值顺序，编译器可以在 `pair()` 之前读连接状态，于是报出来的理由是一次扫描之前的事实；拆成局部变量。顺带：成功也带 detail（`#7 OK focus rejected by camera`）改成只在 ERR 带；失败文案 `rejected by camera` 是这条链路**观测不到**的状态（库写完特征值就返回真，不等机身回话），改成 `camera unreachable`；`STATUS` 改三态（`connected` / `disconnected` / `unpaired`，主机侧 `CAMERA_CONNECTED` 比较不变），因为「要人拿着相机走菜单」和「下一帧自己会好」不是一回事；扫描秒数 30 → 20，扫满 30 秒会让成功回执正好落在主机 `PAIR_TIMEOUT_S` 放弃之后。**实测编译通过**：RAM 13.5%、Flash 27.0%。**已知未修**：板子刚重启时 `/api/shutter/test` 会把 `disconnected` 报成红且文案写「没配对相机」—— 修法是自检读到 `disconnected` 时补一条 `FOCUS`（不烧帧但强制建链）再复读，需要 `SimShutter` 也分清「配对过」与「连着」，记在 `firmware/esp32-shutter/README.md` |
| 85 | L | chore: `start.sh` 本机启动脚本，构建步骤收归一个所有者 | DONE | 新增 `./start.sh {prod\|mock\|build}` —— 本机起服务此前只有裸命令，而「构建前端 + 起后端 + 无硬件加 `--sim`」是每天要打的三件事。**两个脚本的分界不是「做什么」而是代码在哪台机器上执行**：`start.sh` 全在本机，`manage.sh` 每条命令都经 ssh 落到设备上。所以 `manage.sh` 里的 `build_frontend()` 是越界的（本机工作埋在 ssh 编排里），`start.sh` 写出来之后更是**两个调用方零个所有者** —— 下次改构建步骤只会改到一个，而漂移掉的那份照样产出一个能跑的 bundle，只是不是你要发的那个。收成 `start.sh build`，`manage.sh push` 调它。**没有拆 manage.sh**：七个子命令里五个是「ssh 过去一行」（共 31 行），它们是同一个职能的七个动词；按动词拆会把 `HOST`/`REMOTE_DIR`/`step()` 样板抄三遍，再换来三个更长的名字要记。顺带修了自己引入的一个 bug：banner 从 `REBOT_PORT` 打印地址，`--port` 在命令行覆盖时会**打印一个服务并没有绑的端口**，改成从参数里读回（两种写法都认）。**已知未改**：`manage.sh run`（在 r2x 前台跑，会先 `systemctl stop`）与 `start.sh prod`（本机）行为接近而名字不提示机器，改名会打断刻意保护的肌肉记忆，单独决定 |
| 86 | L+E | fix: 自检假红（`disconnected` 不再报「没配对」）+ `shutter_count` 改 `action_count` | DONE | **自检假红**：#84 的已知未修，现在修了。`SimShutter` 分清 `_paired` 和 `_camera`（三个状态：connected / disconnected / unpaired），`ShutterDriver` 协议加 `camera_status()` 方法。自检端点（`/api/shutter/test`）读到 `disconnected` 时补发一条 `FOCUS`（不让相机 b 烧帧，半按强制建链），然后重读 `STATUS`；`unpaired` 直接判红。`camera_connected()` 在 `Esp32Shutter` 上改为委托 `camera_status()`，`CAMERA_CONNECTED` 常量从 `protocol.py` 迁入 `base.py` 并拆成三态常量。`pair()` 在 Sim 上重置 `_unreachable` 标志。**`action_count` 重命名**：`RoutineSummary.shutter_count` 只数 `ShutterAction` 实例数，插件驱动的相机拍了多少帧都记 0 —— 改名 `action_count` 并计所有动作（`len(w.actions)`），对 API 消费者诚实。**`check.py` 决定**：`backend/actions/check.py` 直接构造 `ShutterProvider` 是正确行为（镜像宿主，注释已有说明），唯一改动的必要是宿主构造模式变化时同步更新，记在此处不必再改。+3 测试（326），firmware README 已知未修段更新为已修。 |

---

## 统计

| Phase | 总数 | DONE | 可在开发机做完 |
|---|---|---|---|
| 0 骨架 | 5 | **5** | 5 |
| 1 硬件对表 | 3 | **1** | 1 |
| 2 臂层 | 5 | **5** | 3 |
| 3 急停 | 8 | **8** | 7 |
| 4 数据模型 | 6 | **6** | 6 |
| 5 示教 | 5 | **5** | 4 |
| 6 安全校验 | 4 | **4** | 4 |
| 7 快门 | 6 | **6** | 4 |
| 8 执行器 | 8 | **8** | 8 |
| 9 前端 | 7 | **7** | 7 |
| 10 部署 | 4 | **4** | 1 |
| 11 Agent API | 1 | **1** | 1 |
| 后续新增 | 24 | **24** | 23 |
| **合计** | **86** | **84** | **74** |
