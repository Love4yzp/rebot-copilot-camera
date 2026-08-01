# rebot-copilot-camera

自动化多视角拍摄。reBot-RS 单臂末端夹一台佳能相机，被拍物体固定不动 —— 人零力拖动示教点位，臂沿点位序列走位，每到一点稳定后经 USB 通知 XIAO ESP32-S3，ESP32 用 BLE 冒充佳能无线遥控器按下快门。产出是同一物体的多角度照片。

> 🚧 建设中。完整设计见 [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1)，当前进度见 [`PROGRESS.md`](./PROGRESS.md)。

## 快速上手

```bash
git submodule update --init      # 臂层是 submodule，漏了会 import 失败
uv sync                          # 装依赖
uv run -m backend.app --sim      # 无硬件启动（SimArm + SimShutter）
uv run pytest                    # 跑测试
```

Web 入口 `http://127.0.0.1:18790`。

运动学、动力学、碰撞检查在没有臂的开发机上也能跑（`pin` 和 `motorbridge` 在 macOS arm64 上可用），只有 CAN 传输层需要真机。

## 上游依赖

臂层不自己写。运动学、动力学、重力补偿、轨迹规划、URDF 全部来自 [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py) —— 这套硬件的官方 Pinocchio + MotorBridge 实现。本项目只做它没有的四件事：Routine 点位模型与 actions、急停闩锁、ESP32 快门链路、拍摄工作流 UI。

## 坑清单

- **急停不能用 `RebotArm.estop()`。** 它的实现就是一行 `self.disable_all()` —— 电机失能、力矩归零、臂自由落体。MotorBridge 文档也把 `disable_all()` 写成 "Emergency stop all motors"，但那是掉电语义。本项目的急停是**保持力矩钉在原地**，走冻结 `q_target` + MIT + 重力补偿的路线。
- **上游的默认资产解析指向另一条臂。** `config/rebotarm.yaml` 的 `hardware_yaml` 写的是 `rebotarm_dm.yaml`（B601-**DM**），`load_robot_model()` 不传参会静默加载错误机器人的 URDF —— 文件存在所以不报错，只是 FK、重力补偿、碰撞全算错。一律走 `backend/assets.py`。
- **URDF 的 8 个自由度和硬件的 7 个关节不是 1:1**（夹爪一个电机驱动 URDF 里两个指关节），且 `joint2`/`joint3` 的下限就是 `0.0`，静止伸直姿态压在限位边界上 —— 限位校验要留容差。
- **速度不能读 `mechVel (0x701A)`。** 该固件上这个寄存器不是 rad/s，必须由位置差分算。浮动/锁定的判据正是末端速度，读错会导致锁不住或锁太早。
- **每关节有 0.2–0.5 N·m 库仑摩擦**，重力模型补不掉，靠位置环刚度撑。
- **测重力补偿前把臂摆到无接触姿态**（肘朝上的「L」形）。碰到桌面或连杆互相接触会产生假的标定误差。
- **XIAO ESP32-S3 必须加 `-D ARDUINO_USB_CDC_ON_BOOT=1`**，否则 `Serial` 走 UART0 引脚而不是 USB，主机侧完全收不到数据**且不报错**。
- **佳能相机要先在机身菜单 `无线通信设置 > 蓝牙功能` 设成「遥控」** 才能配对。
- **systemd 默认 `LANG=C`** 会让 journalctl 里的中文变 `?`；服务账号要在 `systemd-journal` 组里 `/api/logs` 才有内容。

## License

MIT
