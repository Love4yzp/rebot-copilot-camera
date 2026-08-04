# 硬件笔记

两类内容严格分开：**已验证**（有源码或实测证据，写明出处）和 **待实测**（还没上真机，别当事实用）。
上游指 [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，本仓库以 submodule 形式锁在 `d540405`（2026-07-28）。

---

## 已验证 —— 从上游源码读出来的

### 1. `estop()` 是掉电语义，本项目不能用

`vendor/reBotArm_control_py/reBotArm_control_py/actuator/rebotarm.py:687`

```python
def estop(self) -> None:
    self.disable_all()
```

一行转发到 `disable_all()`（同文件 `:597`），后者对每个 group 调 `disable()` —— 电机失能、力矩归零、**臂自由落体**。MotorBridge 文档同样把 `disable_all()` 描述成 "Emergency stop all motors"。

本项目的急停要求是**保持力矩钉在原地**，正好相反。实现走冻结 `q_target` + 继续 MIT + 重力补偿维持。代码里禁止出现 `estop()` / `disable_all()` 作为急停路径。

### 2. 上游默认配置指向的是**另一条臂**

上游 `vendor/reBotArm_control_py/config/rebotarm.yaml` 里 `hardware_yaml: "rebotarm_dm.yaml"` —— B601-**DM** 变体。

`kinematics/robot_model.py:40-53` 的 `_resolve_urdf(urdf_path=None)` 会走这条链，最终加载 `urdf/reBot-DevArm_fixend_description/urdf/reBot-DevArm_fixend.urdf`，末端 frame `end_link`。

**文件存在，所以不报错。** 任何用默认参数调上游 kinematics / dynamics 的代码会静默拿到一个合法但属于错误机器人的模型 —— FK、重力补偿、碰撞检查全错，而重力补偿正是零力拖动示教的地基。

对策：所有资产路径走 `backend/assets.py`，永远显式传 `urdf_path=str(assets.urdf_path())`。启动时调 `assets.assert_rs_model()` 让误配置在开机就炸，而不是在运动中表现为"力矩有点怪"。

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

来源：`vendor/reBotArm_control_py/docs/gravity_calibration_rs_2026-07-17.md`

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

`backend/safety/kinematics.py` 用「静止姿态下相撞即为结构性」来排除，剩 **36 对**真实的。这个判据是自校准的：换 URDF 也不用手动维护排除表。

### 9. 重力向量是 URDF 的 8 维，不是硬件的 7 维

`compute_generalized_gravity()` 返回 `nv = 8` 维（`joint1..6` + 两个夹爪指关节），硬件是 7 关节。**只有 6 个臂关节对得上。**

`ArmSession._gravity_torque()` 只喂 6 个臂关节角进去（上游 `pad_q_for_model` 负责补齐到 8 维），取回前 6 维，**夹爪给 0** —— 没有标定过的「指关节行程 → 夹爪电机力矩」换算，编一个再塞进力矩指令比给 0 更糟。

实测值（开发机上算的，不是实机测的）：

| 姿态 | joint2 | joint3 | joint4 |
|---|---|---|---|
| 静止 q=0 | 1.545 | 6.764 | 2.001 |
| 肘展开 `j2=1.2, j3=0.6` | -3.834 | 6.796 | 1.775 |

单位 N·m。j2 从 +1.5 变 -3.8 符合物理直觉（重心越过支点）。**上机后要拿实际电流对一遍。**

---

## 待实测 —— 上真机前不要当事实用

### B1. CAN 接入形态

`config/rebotarm_rs.yaml` 写 `channel: can0`（socketcan），但上游 README 的"通信接口"一栏写的是 "USB2CAN Serial Bridge or CAN Interface"。这台设备走哪条没确认。

查法：`ip link show can0`、`ls /dev/ttyACM*`，然后跑上游 `example/2_zero_and_read.py`。

### B2. 挂佳能机身后重力补偿是否还准

上游标定是**空载**的臂（误差 5–11%）。末端挂一台机身后负载变了，浮动手感和保持精度都会变。

可能的对策：在 URDF 末端加相机的等效质量/质心，或接受手感变差并靠位置环刚度补，或重调浮动/锁定的速度阈值（上游默认线速度 `0.04 m/s`、角速度 `0.08 rad/s`）。

### B3. R2x 上 500 Hz 能否稳住

`rate: 500` 是上游 yaml 默认值，没说在什么算力上测的。

**已知**：sim 模式下自带的线程驱动在 macOS 上 100 Hz 稳（`/api/control` 的 `rate_hz` 实测 100–101）。真机上控制循环换成上游的 `start_control_loop`，它自己管 CAN 时序。上机后测实际 tick 抖动，不稳就降频并在这里记录实测值。

### B4. XIAO ESP32-S3 板子是否在手

未确认。

**已知**：固件（`firmware/esp32-shutter/`）和主机侧客户端（`backend/shutter/esp32.py`）都写完了，行协议在内存管道上有 30 个测试覆盖 —— 半行、粘包、二进制噪声、迟到回包、断开重连都测了。固件**已实测编译通过**（`espressif32@6.11.0` / Arduino core 2.0.17 / `seeed_xiao_esp32s3`，RAM 13.5%、Flash 27.0%）。缺的只是烧到板子上跑一遍：`cd firmware/esp32-shutter && pio run -t upload`，然后 `POST /api/shutter/pair`、`POST /api/shutter/test`。

**编译时踩到的两件事**（细节在 `firmware/esp32-shutter/README.md`）：

- `platform` 必须钉在 6.x 那条线。不钉的话解析到 55.x（Arduino core 3.x），佳能库里两处 `BLEDevice::setEncryptionLevel` 在 core 3.x 已经搬去 `BLESecurity`，编译错误报在库自己的源码里。
- **`isConnected()` 不能当 `SHOOT` 的闸门。** `CanonBLERemote::init()` 只从 NVS 读回相机地址，不建立连接；真正的连接由 `trigger()` / `focus()` 惰性发起。用 `isConnected()` 挡，等于把唯一能建立连接的调用挡在门外 —— 开机后永远连不上，每帧都回 `ERR camera not connected`。现在挡的是「有没有配对过」。**待实测**：这条是读上游源码推出来的，要在板子上确认「重启 → 直接 `SHOOT` → 第一帧成功但慢几秒」。

### 其它待确认

- 七个关节的读数符号与零位是否和 URDF 一致（上游说 rest pose = 伸直 = q=0，未实测）。
- 夹爪 `0x07` 的行程与 URDF 里 `joint_left`/`joint_right` 的米制限位如何对应。
