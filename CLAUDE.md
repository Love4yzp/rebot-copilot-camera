# rebot-copilot-camera — CLAUDE.md

**开工前先读 [`PROGRESS.md`](./PROGRESS.md)。** 那是本项目的状态机：现在做到哪、下一步做什么、什么被卡住。
完整设计与理由在 [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1)，本文件不重复。

## 这是什么

reBot-RS 单臂 + 佳能相机的**自动化多视角拍摄**服务。人零力拖动示教点位，臂沿点位序列走位，每点稳定后经 USB 通知 XIAO ESP32-S3，ESP32 用 BLE 冒充佳能无线遥控器按快门。

跑在 reComputer R2x 上，systemd + uv，只监听 127.0.0.1，外部走 SSH 隧道。

## 四条铁律

1. **急停绝不能调 `RebotArm.estop()`** —— 上游那个方法实现就是一行 `self.disable_all()`（`vendor/reBotArm_control_py/reBotArm_control_py/actuator/rebotarm.py:687`），语义是电机失能、力矩归零、臂自由落体，与本项目"保持力矩钉在原地"完全相反。本项目急停 = 冻结 `q_target` + 继续 MIT + 重力补偿维持。
2. **不许用上游的默认资产解析。** 上游 `config/rebotarm.yaml` 的 `hardware_yaml` 指向 `rebotarm_dm.yaml` —— **B601-DM 那条臂**。`load_robot_model()` 不传参会静默返回一个合法但属于错误机器人的模型，不报错。所有路径走 `backend/assets.py`，显式传 `urdf_path=str(assets.urdf_path())`，启动时调 `assets.assert_rs_model()`。
3. **速度不能读 `mechVel (0x701A)`** —— 该固件上这个寄存器不是 rad/s，必须由位置差分算。浮动/锁定的判据正是末端速度。
4. **不重造运动学/动力学** —— FK / IK / 重力补偿 / 轨迹规划 / URDF 全部用 `Seeed-Projects/reBotArm_control_py`，只调不写。

细节与证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。

## 上游依赖

| 项目 | 关系 |
|---|---|
| [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py) | **硬依赖，git submodule 在 `vendor/`**，锁 `d540405`。Pinocchio + MotorBridge 的官方 RS 控制库。不在 PyPI，也**装不了 git 依赖**（无 `[build-system]`，flat-layout 撞 `urdf/`+`config/`），只能 vendor。克隆后先 `git submodule update --init` 再 `uv sync` |
| [`Seeed-Projects/rebot_arm_webui`](https://github.com/Seeed-Projects/rebot_arm_webui) | **借鉴**。官方 Web UI，有 URDF 3D 查看器和 systemd 部署脚本，但没有示教/录制/回放。不整合 |
| [`maxmacstn/ESP32-Canon-BLE-Remote`](https://github.com/maxmacstn/ESP32-Canon-BLE-Remote) | Arduino **库**，不是固件。只管 BLE 怎么跟佳能说话，何时按快门归调用方管，所以要自己写薄固件 |
| `../rebot-copilot/` | 旧项目 `rebot-simu`（B601-DM 主臂 + SO102 从臂遥操作）。**只读参考，不迁移代码** |

## 硬件

| 项 | 值 |
|---|---|
| 臂 | reBot-RS，6 关节 + 夹爪，RobStride 准直驱，48V |
| 通信 | CAN，`channel: can0`，`rate: 500`（USB2CAN 串口桥形态待确认，见 PROGRESS 阻塞 B1） |
| 关节映射 | `joint1..6` = motor_id `0x01..0x06`（1–3 型号 `rs-06`，4–6 型号 `rs-00`），`gripper` = `0x07`（`rs-00`），feedback_id 统一 `0xFD` |
| URDF | `00-arm-rs_asm-v3.urdf`，末端 frame `gripper_end`，30 个 STL（63 MB，留在 submodule 不复制）。`nq=8`：`joint1..6` + `joint_left`/`joint_right` 两个夹爪指关节，**和硬件的 7 关节不是 1:1**。`joint2`/`joint3` 下限就是 `0.0`，静止伸直姿态压在限位边界上 |
| 快门 | XIAO ESP32-S3，原生 USB CDC。PlatformIO **必须** `-D ARDUINO_USB_CDC_ON_BOOT=1`，否则 `Serial` 走 UART0 引脚，主机侧收不到数据**且不报错** |
| 相机 | 佳能，机身菜单 `无线通信设置 > 蓝牙功能` 要设成「遥控」才能配对 |

无硬件时用 `SimArm` + `SimShutter`，`--sim` 强制启用。

## 层级边界

- `backend/api/*` 只调 controller 和 store，**不**直接动模式内部状态
- `backend/core/executor.py` 纯逻辑，注入 clock / arm / shutter，**不**碰 FastAPI 与真实时间
- `backend/arm/*` 只薄封装上游 `RebotArm`，**不**实现运动学
- `SafetyLatch` 是横切闩锁，**不是**模式机里的模式
- 任何测试里不出现 `time.sleep`，时钟统一走可注入接口

## 测试

`uv run pytest`。只测外部可观察行为，不测实现细节 —— 测「急停后所有运动端点返 409」而不是「闩锁内部布尔值变了」。
不测：前端、`reBotArm_control_py` 本身、MotorBridge SDK、ESP32 固件、真实硬件在环。

## 提交约定

每个 commit 结束时代码库能跑（`uv run pytest` 绿、`uv run -m backend.app --sim` 能起）。
**改代码的 commit 里必须同时更新 `PROGRESS.md` 的状态**，不要分开提交，否则状态和代码漂移。
