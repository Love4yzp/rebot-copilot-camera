# Teach & Repeat · 领域通用语言 (Ubiquitous Language)

A teach-and-repeat platform: named poses, sequences of holds and transitions, and an arm that goes and holds.
示教回放平台：命名的位姿素材库、由保持与过渡块排成的时间轴序列，以及一台精准前往并稳定保持的机械臂。

这份词典是全项目唯一的**领域通用语言 (Ubiquitous Language)** 基准。无论代码命名、界面文案、接口错误还是开发文档，凡同一概念必用同一最优词汇。架构实现详见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)。

---

## 领域词典 (Glossary)

### 1. 实体与结构 (Entities & Structure)

- **Pose（位姿）**
  - **定义**：保存在素材库中已命名的机械臂关节构型（含夹爪）。序列中的保持块通过 ID 链接引用它。
  - **English**: A named joint configuration in the library. Hold blocks link it by ID.
  - **禁忌 (Avoid)**: 点位 (waypoint)、锚点 (anchor)、机位 (camera spot)、坐标点。

- **Station（站位）**
  - **定义**：操作者任务流中的单个业务单元：包含在某位姿停留一段时间 + 站内触发的动作 + 出发去下一站的过渡。
  - **English**: The operator's task-flow unit: one hold block + its markers + outgoing transition.
  - **禁忌 (Avoid)**: 步骤、环节、工序、节点。

- **Hold Block（保持块）**
  - **定义**：时间轴数据模型中的静止区间：保持目标位姿力矩、等待站内动作执行完毕。
  - **English**: A block on the timeline where the arm holds torque at a target pose.
  - **禁忌 (Avoid)**: 停顿块、停留帧、固定段。

- **Transition Block（过渡块）**
  - **定义**：两相邻不同位姿之间由系统自动生成的运动过渡区间。不可手动删除，只可调节时长与缓动曲线。
  - **English**: The physical motion block between two different poses. Auto-generated, undeletable, editable in duration and easing.
  - **禁忌 (Avoid)**: 转场、路径块、移动段。

- **Sequence（序列）**
  - **定义**：由保持块与过渡块交替排列、并在块内钉有动作标记的一整条时间轴运动流程。
  - **English**: An ordered timeline of holds and transitions, with markers pinned inside blocks.
  - **禁忌 (Avoid)**: 播放列表 (playlist)、程序 (program)、任务 (routine)、脚本 (script)。

- **Template（模板）**
  - **定义**：序列的结构配方（站位顺序、时长、动作标记与过渡节奏），**不保存具体关节角度**。使用时逐站录制或选定位姿，实例化后脱钩独立。
  - **English**: A sequence recipe with placeholder slots (no joint angles). Instantiated via a guided walk to bind real poses.
  - **禁忌 (Avoid)**: 场景、模式、预设。

- **Action（动作）**
  - **定义**：机械臂停稳后在末端触发的操作（如按快门、旋转转台、延时等待）。
  - **English**: What the end-effector or external accessory does when triggered (shutter, turntable, wait).
  - **禁忌 (Avoid)**: 特效、事件步骤、脚本步骤。

- **Marker / Event Marker（动作标记 / 等待标记）**
  - **定义**：钉在保持块或过渡块内部具体时间位置的标记，随父块一起移动与修剪。
  - **English**: An event pinned to an offset inside a block, moving and trimming with its parent.
  - **禁忌 (Avoid)**: 钉子、事件点、节点。

---

### 2. 状态与控制 (States & Control)

- **Activity（活动）**
  - **定义**：机械臂当前唯一正在进行的活动：`idle`（待命）、`teaching`（示教）、`playing`（执行中）、`resting`（休息）、`safelock`（安全锁定）。各活动严格互斥。
  - **English**: The one thing the arm is doing: idle, teaching, playing, resting, or safelock. Mutually exclusive.
  - **禁忌 (Avoid)**: 模式乱炖 (mode soup)、重叠标志位、状态 (status)。

- **Latch / Estop（急停闩锁 / 急停）**
  - **定义**：独立于 Activity 的横切安全冻结机制。吸合时机械臂**全力保持力矩钉在原地**，拒绝一切运动意图。解除后原地待命，不自动续跑。
  - **English**: A cross-cutting freeze that outranks Activity. When engaged, torque holds the arm in place and all motion intents are refused.
  - **禁忌 (Avoid)**: 掉电急停、电机失能、自由落体、急停作为一种普通模式 (estop-as-mode)。

- **Hold（保持 / 停稳）**
  - **定义**：电机通电输出力矩，将机械臂姿态稳定固定在当前或目标位置。
  - **English**: Torque on, pose pinned.
  - **禁忌 (Avoid)**: 刹车、锁死、掉电。

- **Rest（休息 / 卸力）**
  - **定义**：机械臂回到零位机械止点后电机彻底卸力（kp=0/kd=0/tau=0），臂安全搁在止点上，电机不发热。
  - **English**: Zero torque at the mechanical stops. Idle holds with torque; rest drops torque onto the stops.
  - **禁忌 (Avoid)**: 休眠、待机、关机。

- **Goto（去这里 / 去起点）**
  - **定义**：单次命令机械臂前往一个指定位姿并保持在那里。再次发起 Goto 会平滑改道至新目的地。
  - **English**: Set the arm's destination to one library pose and hold there. A second Goto retargets.
  - **禁忌 (Avoid)**: 导航、单点运行、把 Goto 当作独立于 Play 的第二套运动系统。

- **Play / Execute（执行）**
  - **定义**：驱动真实机械臂按照已编排的序列物理走完全程。独占执行，运行中拒绝第二次执行指令。
  - **English**: Walk a stored sequence on the physical arm.
  - **禁忌 (Avoid)**: **控臂严禁用「播放」**（容易误导用户以为只是屏幕播动画，引发安全事故；界面一律用「执行（臂会动）」）。

- **Preview（预演）**
  - **定义**：仅在 3D 监视器中模拟走完全程，**机械臂物理静止不动**，界面全灰阶。
  - **English**: Walk the plan ruler in the 3D monitor without moving the physical arm.
  - **禁忌 (Avoid)**: 试跑、虚拟播放、与「执行」混淆。

- **SafeLock（安全锁定）**
  - **定义**：遇到外部接触阻力（碰撞残差）或客户端通信心跳断开时的保护性就地保持。就地保持力矩，不自动恢复、不自动进入示教。
  - **English**: A recoverable lock from contact residual or disconnect. Holds torque; does not auto-resume.
  - **禁忌 (Avoid)**: 与急停 (Estop) 混淆。

- **Intent（意图）**
  - **定义**：作用于 Activity 的操作意图命令（teach on/off, rest on/off, play, goto, stop, resume-wait, finish, fault, unlock）。HTTP 端点只是 Intent 的适配层。
  - **English**: A command named against Activity. HTTP is an adapter over Intent.
  - **禁忌 (Avoid)**: 每个 API 端点各自发明一套 409 冲突判断逻辑。
