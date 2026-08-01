# rebot-copilot-camera

自动化多视角拍摄。reBot-RS 单臂末端夹一台佳能相机，被拍物体固定不动 —— 人零力拖动示教点位，臂沿点位序列走位，每到一点稳定后经 USB 通知 XIAO ESP32-S3，ESP32 用 BLE 冒充佳能无线遥控器按下快门。产出是同一物体的多角度照片。

完整设计与理由见 [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1)，当前进度见 [`PROGRESS.md`](./PROGRESS.md)。

```
示教（拖动 → 松手 → 记录）  →  Routine（有序点位 + 每点挂 actions）  →  播放（到位 → 稳定 → 拍 → 下一点）
```

## 快速上手

```bash
git submodule update --init      # 臂层是 submodule，漏了会 import 失败
uv sync
cd frontend && npm install && npm run build && cd ..

uv run -m backend.app --sim      # 无硬件启动
uv run pytest                    # 228 个测试
```

浏览器开 `http://127.0.0.1:18790`。前端热更新开发用 `cd frontend && npm run dev`（自动 proxy `/api`、`/ws`、`/assets/urdf` 到 18790）。

运动学、动力学、碰撞检查在没有臂的开发机上也能跑 —— `pin` 和 `motorbridge` 在 macOS arm64 上可用，只有 CAN 传输层需要真机。

## 操作流程

1. **新建序列** —— 左栏「+ 新建」。
2. **开示教** —— 底栏「开始示教」。臂进入零力浮动，可以用手拖。
3. **拖到位、松手、按一下** —— 松手臂停在原地，按「记录当前位置」存成一个点。重复。
4. **配每个点** —— 点开某一行：到位用时、稳定等待（消振，臂停稳和照片不糊之间差几百毫秒）、到位后做什么（拍照 / 等待）。
5. **播放** —— 退出示教后按「播放」。开始前会对整条序列做限位与自碰撞预检，不合法直接拒绝，臂一动不动。

**急停随时可按**（顶栏红键或 `Esc`）。臂**保持力矩钉在原地**，不掉电。解除后原地待命，**不自动续跑** —— 现场状态大概率已经变了。

## 部署到 R2x

```bash
./manage.sh setup     # 一次性：uv + systemd unit + CAN + udev + 权限组
./manage.sh push      # 改完代码：build 前端 + rsync + 重启
./manage.sh enable    # 开机自启
./manage.sh status    # 是否在跑，以及跑在真臂还是模拟器上
./manage.sh logs      # tail journalctl
./manage.sh open      # SSH 隧道 + 开浏览器
./manage.sh run       # 前台跑，调 print/breakpoint 用
```

只监听 `127.0.0.1`，外部走 SSH 隧道。**没有认证层**，而这个服务能让一条 48V 的臂动起来。

## 架构

| 层 | 职责 | 边界 |
|---|---|---|
| `backend/arm/` | 薄封装上游 `RebotArm`；`SimArm` 同接口 | **不实现运动学** |
| `backend/safety/` | 急停闩锁、看门狗、限位与自碰撞 | 闩锁**不碰硬件** |
| `backend/core/` | 控制循环、执行器、浮动/锁定、广播 | 执行器纯逻辑，注入时钟/arm/shutter |
| `backend/routines/` | Routine 数据模型 + 一文件一 JSON 存储 | |
| `backend/shutter/` | ESP32 行协议客户端；`SimShutter` 同接口 | |
| `backend/api/` | REST + WebSocket | 只调 controller 和 store |
| `frontend/` | Vite + React + TS | 指令走 REST，`/ws` 只读 |
| `firmware/esp32-shutter/` | PlatformIO 工程 | |

任何测试里不出现 `time.sleep`，时钟统一走可注入接口。

## 上游依赖

臂层不自己写。运动学、动力学、重力补偿、轨迹规划、URDF 全部来自 [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，以 **git submodule** 锁在 `vendor/`（它没有 `[build-system]`，装不了 git 依赖）。3D 查看器的资产组织参考 [`Seeed-Projects/rebot_arm_webui`](https://github.com/Seeed-Projects/rebot_arm_webui)。快门用 [`maxmacstn/ESP32-Canon-BLE-Remote`](https://github.com/maxmacstn/ESP32-Canon-BLE-Remote)（是**库不是固件**）。

## 坑清单

**这四条会静默出错 —— 不报错，只是结果是错的。**

- **急停不能用 `RebotArm.estop()`。** 它的实现就是一行 `self.disable_all()`（`rebotarm.py:687`）—— 电机失能、力矩归零、臂自由落体。MotorBridge 文档也把 `disable_all()` 写成 "Emergency stop all motors"，但那是掉电语义。本项目的急停是**保持力矩**。有一条测试走 AST 扫 `backend/` 下每个模块，出现这两个名字的属性访问就失败。
- **上游默认资产解析指向另一条臂。** `config/rebotarm.yaml` 的 `hardware_yaml` 写的是 `rebotarm_dm.yaml`（B601-**DM**）。`load_robot_model()` 不传参会静默加载错误机器人的 URDF —— 文件存在所以不报错，只是 FK、重力补偿、碰撞全算错。一律走 `backend/assets.py`。
- **`mechVel (0x701A)` 不是 rad/s。** 速度必须位置差分算。浮动/锁定的判据正是末端速度，读错会锁不住或锁太早。
- **XIAO ESP32-S3 必须加 `-D ARDUINO_USB_CDC_ON_BOOT=1`。** 否则 `Serial` 走 UART0 引脚而不是 USB。板子会枚举、主机能打开端口、每次写入都成功 —— 然后什么都收不到，整条链路上没有任何一处报错。

**其它：**

- **URDF 是 8 自由度，硬件是 7 关节**（夹爪一个电机驱两个米制平移指关节），**不是 1:1**。夹爪不做限位校验、不给重力前馈 —— 没有标定过的换算，编一个再去信它更糟。
- **`joint2` / `joint3` 下限恰好是 `0.0`**，而静止伸直姿态就是 q=0 —— rest pose 压在限位边界上，限位校验必须留容差（当前 0.02 rad），否则臂会因为「站着不动」被拒。
- **每关节 0.2–0.5 N·m 库仑摩擦**，重力模型补不掉，靠位置环刚度撑。
- **测重力补偿前把臂摆到无接触姿态**（肘朝上的「L」形）。碰桌面或连杆互相接触会产生假的标定误差。
- **佳能相机要先在机身菜单 `无线通信设置 > 蓝牙功能` 设成「遥控」**（不是「智能手机」）才能配对。
- **`PING` 通不代表能拍** —— 它只证明主机和板子之间通。整条链路用 `POST /api/shutter/test?shoot=true` 验。
- **systemd 默认 `LANG=C`** 会让 journalctl 里的中文变 `?`；服务账号要在 `systemd-journal` 组里 `/api/logs` 才有内容。unit 和 `manage.sh setup` 都处理了。
- **`manage.sh push` 不会删除 `routines/`** —— 那是操作员的劳动成果，只存在于设备上。

## API

`http://127.0.0.1:18790/docs`，OpenAPI 在 `/openapi.json`。

| | |
|---|---|
| `POST /api/estop` `/clear` `GET` | 急停。engage 永远 200，重复 engage 保留首因 |
| `GET/POST /api/routines` `…/{id}` | 序列 CRUD |
| `POST /api/routines/{id}/waypoints` `…/capture` `…/reorder` | 点位编辑与录点 |
| `POST /api/routines/{id}/play` · `POST /api/playback/stop` | 播放。play 前做整条预检 |
| `POST /api/teach` | 零力示教开关 |
| `POST /api/shutter/test` | 快门链路自检 |
| `GET /api/control` · `GET /api/health` · `WS /ws` | 状态 |

**所有会让臂动的端点在急停期间返 409 并带原因。** 有一条测试遍历路由表，新增端点忘挂闸门就会失败。

## License

MIT
