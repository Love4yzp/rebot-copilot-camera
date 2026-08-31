# 硬件笔记

两类内容严格分开：**已验证**（有源码或实测证据，写明出处）和 **待实测**（还没上真机，别当事实用）。
上游指 [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，本仓库以 submodule 形式锁在 `d540405`（2026-07-28）。

---

## 已验证 —— 从上游源码读出来的

### 1. `estop()` 是掉电语义，本项目不能用

`app/vendor/reBotArm_control_py/reBotArm_control_py/actuator/rebotarm.py:687`

```python
def estop(self) -> None:
    self.disable_all()
```

一行转发到 `disable_all()`（同文件 `:597`），后者对每个 group 调 `disable()` —— 电机失能、力矩归零、**臂自由落体**。MotorBridge 文档同样把 `disable_all()` 描述成 "Emergency stop all motors"。

本项目的急停要求是**保持力矩钉在原地**，正好相反。实现走冻结 `q_target` + 继续 MIT + 重力补偿维持。代码里禁止出现 `estop()` / `disable_all()` 作为急停路径。

### 2. 上游默认配置指向的是**另一条臂**

上游 `app/vendor/reBotArm_control_py/config/rebotarm.yaml` 里 `hardware_yaml: "rebotarm_dm.yaml"` —— B601-**DM** 变体。

`kinematics/robot_model.py:40-53` 的 `_resolve_urdf(urdf_path=None)` 会走这条链，最终加载 `urdf/reBot-DevArm_fixend_description/urdf/reBot-DevArm_fixend.urdf`，末端 frame `end_link`。

**文件存在，所以不报错。** 任何用默认参数调上游 kinematics / dynamics 的代码会静默拿到一个合法但属于错误机器人的模型 —— FK、重力补偿、碰撞检查全错，而重力补偿正是零力拖动示教的地基。

对策：所有资产路径走 `app/backend/assets.py`，永远显式传 `urdf_path=str(assets.urdf_path())`。启动时调 `assets.assert_rs_model()` 让误配置在开机就炸，而不是在运动中表现为"力矩有点怪"。

### 3. URDF 自由度和硬件关节数对不上

| | 数量 | 名字 |
|---|---|---|
| URDF (`nq = 8`) | 8 | `joint1..joint6` + `joint_left` + `joint_right` |
| 硬件 yaml | 7 | `joint1..joint6` + `gripper` |

夹爪一个电机（motor_id `0x07`）驱动 URDF 里两个指关节。**不是 1:1 映射**，写限位校验和 FK 输入时必须显式转换，不能按下标对齐。

`joint_left` / `joint_right` 是平移关节，限位分别是 `0..0.05` m 和 `0..0.0715` m（单位是米，不是弧度）。

### 4. `joint2` / `joint3` 的下限就是 0.0

从 URDF 读出的限位（`joint1..joint6`，弧度）：

| 关节 | lower | upper | vel | effort |
|---|---|---|---|---|
| joint1 | -2.80 | 2.80 | 50 | 36 |
| joint2 | **0.00** | 3.14 | 50 | 36 |
| joint3 | **0.00** | 3.14 | 50 | 36 |
| joint4 | -1.57 | 1.57 | 40 | 14 |
| joint5 | -1.57 | 1.57 | 40 | 14 |
| joint6 | -3.14 | 3.14 | 40 | 14 |

joint2 / joint3 的下限恰好是 0，而静止伸直姿态就是 `q = 0` —— **rest pose 正好压在限位边界上**。朴素的 `lower <= q <= upper` 校验会被浮点噪声和编码器抖动误拒。限位校验（commit #33）要留容差，或对下界用开区间以外的处理。

### 5. 上游标定文档记录的两个陷阱

来源：`app/vendor/reBotArm_control_py/docs/gravity_calibration_rs_2026-07-17.md`

- **`mechVel (0x701A)` 在该固件上不是 rad/s。** 速度必须由位置差分算。浮动/锁定的判据正是末端速度，读错会导致锁不住或锁太早。
- **每关节 0.2–0.5 N·m 库仑摩擦**，重力模型补不掉，要靠位置环刚度撑。
- 测重力补偿前把臂摆到**无接触姿态**（肘朝上的「L」形）。碰桌面或连杆互相接触会产生假的标定误差。
- 该文档结论：当前 URDF 质量参数在所有承载关节上误差 5–11%，无零点偏移。**这是空载的臂。**

### 6. 上游不可 pip 安装

`pyproject.toml` 没有 `[build-system]`，flat-layout 下同级还有 `urdf/`、`config/` 目录，setuptools 自动发现直接中止：

```
error: Multiple top-level packages discovered in a flat-layout: ['urdf', 'config', 'reBotArm_control_py']
```

所以 git 依赖装不了，只能 vendor。本仓库用 **git submodule**（sha 锁在 git 里，升级是 checkout 而不是手工合并），并在 `pyproject.toml` 的 `[tool.hatch.build.targets.wheel]` 里把它映射进包。上游 `rebot_arm_webui` 也 vendor，同一个原因。

克隆后必须 `git submodule update --init`，否则 `uv sync` 装得上但 `import reBotArm_control_py` 失败。

### 7. 依赖在 macOS arm64 上可用

`pin` 4.1.0（Pinocchio）和 `motorbridge` 0.5.0 都装得上且 import 通过，RS 模型能在开发机上加载。也就是说**运动学、动力学、碰撞检查都能在没有臂的开发机上跑和测**，只有 CAN 传输层需要真机。

URDF + 30 个 STL 共 63 MB，留在 submodule 里，不复制进本仓库。

### 8. 碰撞对里有 8 对是结构性的

URDF 不带 SRDF，`addAllCollisionPairs()` 给出 **44 对**候选，其中 8 对在静止姿态下就相撞：

```
base_link↔link1  link1↔link2  link2↔link3  link3↔link4
link4↔link5      link5↔link6  gripper_end↔gripper_left  gripper_end↔gripper_right
```

全是相邻连杆 —— 拧在一起本来就贴着。不排掉的话**每一个姿态都是自碰撞**。

`app/backend/safety/kinematics.py` 用「静止姿态下相撞即为结构性」来排除，剩 **36 对**真实的。这个判据是自校准的：换 URDF 也不用手动维护排除表。

### 9. 重力向量是 URDF 的 8 维，不是硬件的 7 维

`compute_generalized_gravity()` 返回 `nv = 8` 维（`joint1..6` + 两个夹爪指关节），硬件是 7 关节。**只有 6 个臂关节对得上。**

`ArmSession._gravity_torque()` 只喂 6 个臂关节角进去（上游 `pad_q_for_model` 负责补齐到 8 维），取回前 6 维，**夹爪给 0** —— 没有标定过的「指关节行程 → 夹爪电机力矩」换算，编一个再塞进力矩指令比给 0 更糟。

实测值（开发机上算的，不是实机测的）：

| 姿态 | joint2 | joint3 | joint4 |
|---|---|---|---|
| 静止 q=0 | 1.545 | 6.764 | 2.001 |
| 肘展开 `j2=1.2, j3=0.6` | -3.834 | 6.796 | 1.775 |

单位 N·m。j2 从 +1.5 变 -3.8 符合物理直觉（重心越过支点）。**上机后要拿实际电流对一遍。**

### 10. MIT 模式执行最后一条指令 —— 浮动期间停发 = 保持陈旧设定值

真机实测现象（2026-08，录姿态时）：示教浮动一触发，臂就往最后锁定的位置回拉，操作者感觉是「一直往上抬」。

机制（源码级）：旧版 `_tick_teaching` 判定「手在动」后完全停止发令，`set_float(True)` 当时只设标志位。MIT 模式的电机持续执行**最后收到的那条指令** —— 即锁定阶段的 `hold(锁定位置, kp=50~150, tau=gravity)`，臂被位置环拉回锁定点。上游示例 `9_gravity_compensation.py` / `10_gravity_compensation_lock.py` 的做法是浮动期间**每 tick 流式发 MIT**：目标=当前位置、低增益（kp=2/kd=1）、按当前 q 实时算重力前馈。

对策：`ArmDriver.follow()` —— 浮动期间控制循环每 tick 必调（`ArmSession.follow()` 发低增益 MIT + 实时重力前馈；`SimArm.follow()` 只同步目标，保持 sim/真机语义对齐）。模拟器的 `set_float(True)` 原本是真释放，所以这条 bug 在 `--sim` 下完全隐形 —— sim 和真机的「释放」语义差异是这类 bug 的温床。

### 11. 夹爪不在机上时，它的 0.8 kg 也必须离开重力模型

真机配置（2026-08）：裸臂，无相机、**无夹爪**。`gripper: false` 开关（#见 `app/backend/assets.py`）原本只把夹爪**电机**从总线上剥掉，URDF 里的 `gripper_end`（0.65 kg，质心偏出 11.3 cm）+ 两个指节（0.0752 kg × 2）共 **0.8004 kg** 还留在重力模型里。

差分实测（开发机上对 URDF 算，有/无夹爪质量两个模型对比）—— 幻影力矩，单位 N·m：

| 姿态 | joint2 | joint3 | joint4 |
|---|---|---|---|
| 静止 q=0 | -1.404 | **+3.257** | +1.466 |
| `j2=1.2, j3=0.6` | -2.339 | +3.010 | +1.210 |
| `j2=1.57, j3=1.0` | -3.048 | +3.050 | +1.234 |

每关节库仑摩擦只有 0.2–0.5 N·m（#5），幻影力矩是它的 6–15 倍，什么都盖不住。浮动 kp=2 的位置环要对抗 3.26 N·m 需要偏出 1.6 rad —— 等于完全不设防。「点录制姿态臂就自己往上抬」的主嫌疑就是它（follow 修复见 #10，两者叠加）。

对策：`assets.effective_urdf_path()` —— `gripper: false` 时生成剥掉夹爪连杆 `<inertial>` 质量的临时 URDF（与 `effective_hardware_yaml()` 同款手法），只供动力学模型用；运动学/碰撞仍用 vendor 原文件（幻影几何是保守方向）。`ArmSession._dynamics_model()` 走它。负载配置已产品化为 `app/config/tuning.yaml` 的 payload profile（bare/camera/gripper）+ 调参面板（`app/backend/tuning.py`），camera 档位把称出的相机质量/质心注入 `gripper_end`。**profile 回答「挂什么质量」、yaml 开关回答「电机在不在总线」是正交的两件事**：夹爪装着但没接线 = profile `gripper` 死重态（`effective_urdf_path` 返回 vendor 原文件，0.8 kg 质量保留、执行器缺席）；无夹爪出厂态 = profile `bare`（质量剥离）；电机接上后 profile 被锁为 `gripper`。注意 joint2 的幻影是**反向**的（夹爪质心在 -x 侧，原本起着配重作用），摘掉后 j2 重力需求反而上升 —— 别凭直觉猜符号，以差分表为准。

### 12. 线性 MIT 斜坡在起停处猛冲，必须缓动

真臂实测（2026-08-14）：`move_to` 用**线性**斜坡（匀速插值 setpoint）时，kp=50 位置环把 setpoint 的速度阶跃直接传给臂 —— 起步瞬间加速、到位瞬间急停，操作者感觉「猛的一下过去、没有任何保护」。匀速斜坡的速度在两端不连续（无限 jerk）。

对策：`ArmSession.move_to` 改用 **smoothstep 缓动**（setpoint 两端速度为零），起停不再猛冲；其峰值速度是匀速平均值的 1.5×（`EASE_PEAK`，定义在 `app/backend/arm/base.py`）。因此执行器对「限速的首段进站 / goto」把时长乘 `EASE_PEAK` 补偿，峰值仍压在 `first_approach_max_speed`（0.25 rad/s）；用户自选的过渡时长不补偿 —— 时长是操作者定的，缓动只去掉起停猛冲。

### 13. 保持发热 = 真实重力矩；零位悬停是白烧 —— 休息态卸力

MIT 保持时电机电流 = 该姿态的**真实**重力矩（前馈错了只会让臂停在目标偏移处，总和不变）——j3 静止就扛 ~6.8 N·m。浪费在**零位**：臂被前馈悬停在止点上方，电机白烧。对策：`POST /api/rest` 在零位门限内发 kp=0 / kd=0 / tau=0 的 MIT（**不是 disable**），臂落止点；碰一下或任何运动 / 急停即醒。**待真机确认**：卸力后 j1/j4/j5/j6 是否都真被止点托住。

### 14. 本机 CAN 与控制模式

macOS 直连 = XCAN-USB + MacCAN `libPCBUSB.dylib`（`~/.local/lib`）。darwin 后端把 yaml `channel: can0` 映射 PCAN_USBBUS1，yaml 零改动。`./dev.sh prod` 注入 `DYLD_FALLBACK_LIBRARY_PATH`；裸 `uv run` 报 `load PCBUSB failed`。

固件在 **enable 时锁存控制模式**：必须先 `mode_mit()` 再 `enable_all()`，运行时切模式被无视。因此臂终生只走 MIT（斜坡 / 保持 / 浮动）。POS_VEL 在本机固件上不可用。急停冻结 = 停斜坡 + 钉当前位姿。

j2/j3 的 rest 是机械下止点。q≈0 处测重力「FLIPPED」是假阳性（止点扛走重力）。正确方法 = 上游 lift-then-float。

### 15. 断电顺序

臂在跑 / 回零时断臂电源，CAN 调用阻塞 ~2s → 看门狗误报急停。干净收工：点「休息」→ 停后端（休息态退出不回零）→ 断臂电源。开工程序相反：先臂上电，再 `./dev.sh prod`。prod 连不上真臂拒绝启动，不退回模拟器。

---

## 待实测 —— 上真机前不要当事实用

### B1. CAN 接入形态

已解，见已验证 #14。

### B2. 挂佳能机身后重力补偿；j2 过补

上游标定是**空载**的臂（误差 5–11%）。机上无相机；夹爪已装回并接线（yaml `gripper: true`，7 关节，profile 锁 `gripper`）。gripper 读数恒定不是串位（j2 一动即分开）。

**约束**：肘展开姿态（j2≈0.44 / j3≈0.85 rad）j2 前馈过补，松手会自己上冲到 ~47°，有冲顶 / 夹手风险。近零位（j2=0.35 / j3=0.3）可保持。**标定完成前不要在展开姿态手掰示教。** 调参模型已有 `gravity.scale/bias`（`tau = scale·g_model + bias`，只许 6 臂关节；浮动中拒改）。

**标定**（lift-then-float；UI 没有「示教」按钮）：

1. 点素材库底部「+ 录位姿」，底部绿条「零重力 · 臂可推动，松手自动锁定」即示教条。看关节角点「详细数据 ▾」，退出点「× 取消」。
2. 手掰到测试姿态（近零或展开），松手观察 3–5s：往上冲 → 降该关节 scale；往下掉 → 升 scale；方向固定量小 → 调 bias。
3. 每调一次先「× 取消」退出再改面板（浮动中改会 409）。直到松手 10s 零漂移，点「保存到配置」。
4. 至少标 j2/j3 两个姿态（近零 + 展开），验证回零到位不再超时、展开姿态浮动不再上冲。

相机到货后：整机称重填 `camera.mass`，质心相对末端法兰的偏移填 `camera.com`，切到 camera profile，再按上面的流程复核。camera 未填 mass 时后端拒绝切换（422）。

### B3. R2x 上 500 Hz 能否稳住

`rate: 500` 是上游 yaml 默认值，没说在什么算力上测的。

**已知**：sim 模式下自带的线程驱动在 macOS 上 100 Hz 稳（`/api/control` 的 `rate_hz` 实测 100–101）。真机上控制循环换成上游的 `start_control_loop`，它自己管 CAN 时序。上机后测实际 tick 抖动，不稳就降频并在这里记录实测值。

### B4. XIAO ESP32-S3 板子是否在手

未确认。

**已知**：固件（`app/firmware/esp32-shutter/`）和主机侧客户端（`app/backend/shutter/esp32.py`）都写完了，行协议在内存管道上有 30 个测试覆盖 —— 半行、粘包、二进制噪声、迟到回包、断开重连都测了。固件**已实测编译通过**（`espressif32@6.11.0` / Arduino core 2.0.17 / `seeed_xiao_esp32s3`，RAM 13.5%、Flash 27.0%）。缺的只是烧到板子上跑一遍：`cd app/firmware/esp32-shutter && pio run -t upload`，然后 `POST /api/shutter/pair`、`POST /api/shutter/test`。

**编译时踩到的两件事**（细节在 `app/firmware/esp32-shutter/README.md`）：

- `platform` 必须钉在 6.x 那条线。不钉的话解析到 55.x（Arduino core 3.x），佳能库里两处 `BLEDevice::setEncryptionLevel` 在 core 3.x 已经搬去 `BLESecurity`，编译错误报在库自己的源码里。
- **`isConnected()` 不能当 `SHOOT` 的闸门。** `CanonBLERemote::init()` 只从 NVS 读回相机地址，不建立连接；真正的连接由 `trigger()` / `focus()` 惰性发起。用 `isConnected()` 挡，等于把唯一能建立连接的调用挡在门外 —— 开机后永远连不上，每帧都回 `ERR camera not connected`。现在挡的是「有没有配对过」。**待实测**：这条是读上游源码推出来的，要在板子上确认「重启 → 直接 `SHOOT` → 第一帧成功但慢几秒」。

### B5. 急停保持力矩的正式验收

**现状**：2026-08-14 实测「先 mode_mit 再 enable」之后急停冻结「臂立刻硬」（见 B2 控制模式锁定段），但那是修复过程中的手感观察，没当过验收跑。急停是整个项目最要害的语义，值得一次正式验收。

**验法**：臂举负载停在半空 → 触发急停 → ① 手推各关节应推不动；② 闩锁吸合 10 分钟，读回的关节角不下滑（对比 `/api/control` 的 `positions` 前后值）；③ 期间界面「已急停」红色通道不闪断。**这条不过，后面都不用测。**

### B6. 解除急停进入零重力示教

**现状**：解除后自动进示教在真机上见过（B2），但 B2 实测到 j2 展开姿态浮动过补、臂会自己上抬 —— 在重力标定完成前，「解除即示教」对展开姿态是风险动作。

**验法**：近零姿态解除急停 → 臂可手推、松手自锁。展开姿态下臂若自己漂移，记到 B2 的标定进度里，**不算这条失败** —— 这条验的是「解除的去向是示教」，漂移是前馈精度问题。

### B7. relax 只在零位卸力

**现状**：休息态已在干净收工流程里用过（见「其它待确认·断电顺序」）。代码上非零位到不了 relax（`Controller.park_home` 的调用顺序保证），真机只需验证零位那一条。

**验法**：零位点「休息」→ 臂靠在机械限位上、电机电流降下来、姿态不变。若休息后姿态有可见下沉，说明零位不在机械止点上，回填实测零位。

### 其它待确认

- 七个关节的读数符号与零位是否和 URDF 一致（上游说 rest pose = 伸直 = q=0）。验证：lift-then-float + 看漂移方向，见 #B2。跑真臂前先退出所有 backend 实例（独占 CAN）。
- 夹爪 `0x07` 的行程与 URDF 里 `joint_left`/`joint_right` 的米制限位如何对应。
- 进站限速 `FIRST_APPROACH_MAX_SPEED = 0.25 rad/s`（`app/backend/core/executor.py`）按 demo 安全同步 15°/s 取的（`docs/rebot-policy.md` §1.3），挂相机后未标定。
- 到位静止判定 `SETTLE_DRIFT_RAD = 0.003` / `SETTLE_MIN_S = 0.15`。用位置漂移、不用差分速度 —— 100 Hz 差分会把 CAN 抖动放大。真机若「永远不到位」，先放宽这两值到能稳定通过的最紧值并回填。
