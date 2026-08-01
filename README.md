# rebot-copilot-camera

**自动化多视角拍摄** —— reBot-RS 机械臂夹着佳能相机绕固定物体走位，每到一个机位停稳后自动按快门。机位是人用手把臂拖到位「教」出来的，不写代码、不算坐标。

```
示教                      Routine                   播放
拖到位 → 松手 → 记录  →   有序点位 + 每点挂动作  →   到位 → 稳定 → 拍 → 下一点
```

产出是同一物体的多角度照片，留在相机 SD 卡里（本项目不下载、不做后期）。

**状态**：软件完成，252 个测试绿；两项硬件实测待做（见 [`PROGRESS.md`](./PROGRESS.md)）。
**改代码前先读 [`AGENTS.md`](./AGENTS.md)** —— 有四条违反了不会报错、只会让结果错的铁律。

---

## 需要什么

| | | 没有也能干什么 |
|---|---|---|
| **reBot-RS 机械臂** | 6 关节 + 夹爪，RobStride 电机，48V，CAN | 用 `--sim` 跑模拟臂，除了真运动之外全部功能可用 |
| **reComputer R2x** | 部署目标。开发机上跑也行 | 开发机直接跑 |
| **USB-CAN 适配器** | 主机 ↔ 臂 | 同上 |
| **佳能相机 + BLE 遥控** | 机身要支持蓝牙遥控 | 用 `SimShutter`，快门调用只记日志 |
| **XIAO ESP32-S3** | 快门桥，USB 连主机、BLE 连相机 | 同上 |

软件侧：**uv**、**Node 18+**（构建前端）、**Python 3.11**（uv 会自己装）。
运动学 / 动力学 / 碰撞检查在 macOS 和 Linux 开发机上都能跑 —— **只有 CAN 传输层需要真机**。

---

## 安装

```bash
git clone --recursive https://github.com/Love4yzp/rebot-copilot-camera.git
cd rebot-copilot-camera
```

已经克隆过但没带 `--recursive`：

```bash
git submodule update --init
```

**这一步不能跳。** 机械臂控制库是 submodule 不是 pip 依赖（上游没有 `[build-system]`，装不了 git 依赖）。漏了的话 `uv sync` 会成功，但 `import reBotArm_control_py` 失败。

```bash
uv sync                                   # Python 依赖
cd frontend && npm install && npm run build && cd ..   # 前端，产物进 backend/static/
```

---

## 试跑（不需要任何硬件）

```bash
uv run -m backend.app --sim
```

浏览器开 **http://127.0.0.1:18790**。模拟臂会响应示教拖动、走点位、假装按快门，整个工作流都能走通。

确认跑在模拟器上：

```bash
curl -s http://127.0.0.1:18790/api/health | grep simulated     # "simulated": true
```

改前端时用热更新：`cd frontend && npm run dev`（自动 proxy `/api`、`/ws`、`/assets/urdf` 到 18790）。

跑测试：`uv run pytest`（252 个）。

---

## 上机拍一组

### 1. 接线与启动

臂接 CAN、ESP32 接 USB、相机装到夹爪上。**不加 `--sim`** 启动：

```bash
uv run -m backend.app
```

启动日志会说清用的是真臂还是模拟器。**这一步一定要看** —— 一个开开心心跑在模拟器上的服务，看起来和正常的一模一样：

```bash
curl -s http://127.0.0.1:18790/api/health | python3 -m json.tool
# arm.simulated 必须是 false
```

### 2. 配对相机（只做一次）

1. 机身菜单 `无线通信设置 > 蓝牙功能` 设成 **「遥控」**（不是「智能手机」）。**不设这个配不上。**
2. 机身选「配对」，进入等待状态。
3. 给 ESP32 发 `PAIR`：`pio device monitor` 里手打 `#1 PAIR`。详见 [`firmware/esp32-shutter/README.md`](./firmware/esp32-shutter/README.md)。
4. 配对信息存在板子上，之后上电自动重连。

验证整条链路（**会真的拍一张**）：

```bash
curl -X POST 'http://127.0.0.1:18790/api/shutter/test?shoot=true'
```

不带 `?shoot=true` 只发 `PING` —— 那只证明主机和板子之间通，**不证明相机能拍**。

### 3. 示教机位

1. 左栏「+ 新建」建一条序列。
2. 底栏「开始示教」。臂**先保持握持不动** —— 没人扶住就松劲的臂会垂下去。
3. **用手推它**。臂测到运动就放开，变成零力浮动，可以自由拖。
4. 拖到想要的机位，**松手**。停手约 0.25 秒后臂自动锁在那里。
5. 按「记录当前位置」存成一个点。
6. 重复 3–5，把一圈机位都教一遍。

### 4. 配置每个点

点开列表里某一行：

| 字段 | 作用 |
|---|---|
| **到位用时** | 从上一个点移动到这里花多久 |
| **稳定等待** | 到位后静止多久再拍。**臂停稳和照片不糊之间差几百毫秒** —— 这个值太小照片会糊 |
| **到位后** | 拍照 / 等待。拍照可选先对焦、可选失败策略（失败即停 / 重试 / 跳过） |
| **备注** | 比如「正面 45°」 |

拖左侧 `⠿` 可以重排点位顺序。

### 5. 播放

退出示教，按「播放」。

开始前会对**整条序列**做限位与自碰撞预检 —— 包括相邻两点之间的路径。不合法直接拒绝，**臂一动不动**。两个各自合法的点位之间的直线路径可能穿过臂自己的底座，这个检查就是为它存在的。

播放中随时可以「停止」。

---

## 急停

**顶栏红色大按钮，或按 `Esc`。** 随时可按，包括播放中、示教中。

- 臂**保持力矩钉在原地**，不掉电、不松劲。
- 所有会让臂动的请求返 409 并带上原因。
- **解除后原地待命，不自动续跑** —— 到你解除的时候现场状态大概率已经变了（有人把臂拖开了、样品拿走了）。

除了人按，看门狗也会自动触发：控制循环持续迟到、连续 CAN 读失败、握持中关节持续漂移。原因会显示在急停条上。

---

## 配置

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `REBOT_HOST` | `127.0.0.1` | 监听地址。**改成 `0.0.0.0` 等于把机械臂控制权开放给整个网络，没有认证层** |
| `REBOT_PORT` | `18790` | 监听端口 |
| `REBOT_ROUTINES_DIR` | `./routines` | 序列存放目录，一条序列一个 JSON |

命令行：`--sim` 强制模拟、`--host`、`--port`。

`manage.sh` 认 `REBOT_HOST_SSH`（默认 `recomputer@r2x`）、`REBOT_REMOTE_DIR`（默认 `/opt/rebot-copilot-camera`）。

**挂上相机后需要重调的常量**（都在代码里，有测试覆盖）：

| 常量 | 位置 | 默认 | 为什么要调 |
|---|---|---|---|
| `FloatLockConfig` 的速度阈值 | `backend/core/floatlock.py` | 线速度 0.04 m/s | 上游标定是空载的臂，挂相机后手感会变 |
| `DEFAULT_HOLD_KP` / `KD` | `backend/arm/session.py` | 50 / 3 | 负载变了，握持刚度要跟 |
| `LIMIT_TOLERANCE_RAD` | `backend/safety/kinematics.py` | 0.02 | 编码器噪声大就加 |

---

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

`push` **不会删除设备上的 `routines/`** —— 那是操作员一个个拖出来的劳动成果，只存在于设备上。

---

## 故障排查

### 服务在跑，但臂一动不动

多半是静默 fallback 到了模拟器。

```bash
curl -s http://127.0.0.1:18790/api/health | grep simulated
./manage.sh status                     # 部署环境用这个
```

`"simulated": true` 就是没连上真臂。启动日志里有 fallback 的具体原因（CAN 接口不存在、臂没上电、权限不够）。

### `import reBotArm_control_py` 失败

submodule 没拉：`git submodule update --init`。

### 按播放返 400

序列里有点位越限或自碰撞，或者相邻两点之间的**路径**穿模。返回体的 `detail.reasons` 会指出是哪个点位、哪个关节、或哪一段路径。

注意：**录点时不拒绝、只警告** —— 臂物理上就在那个姿态，拒绝记录它是荒谬的。检查发生在播放前。

### 按播放返 409

急停闩着，或者已经在播 / 在示教中。`detail` 里写了是哪种。

### 臂拖不动

没开示教，或者急停闩着。示教开着时臂**起手是握持的**，要推一下它才放开 —— 这是设计，不是卡住。

### 快门 `test` 返 ok，但播放时拍不到

`POST /api/shutter/test` 不带参数只发 `PING`，那只测主机↔板子。用 `?shoot=true` 测整条链路。链路问题一般是：相机睡了、蓝牙功能没设成「遥控」、或者板子重启后配对丢了（板子重启会主动发 `READY`，后端会把在途命令判为作废）。

### 主机完全收不到 ESP32 的任何数据

`platformio.ini` 里少了 `-D ARDUINO_USB_CDC_ON_BOOT=1`。板子会枚举、端口能打开、每次写入都成功，然后什么都收不到，**整条链路上没有任何一处报错**。

### `/api/logs` 是空的

服务账号不在 `systemd-journal` 组里。`./manage.sh setup` 会加，加完要重新登录才生效。

### journalctl 里中文变 `?`

systemd 默认 `LANG=C`。unit 文件里已经设了 `LANG=zh_CN.UTF-8`；用 `./manage.sh run` 前台跑时也带了。

### 臂突然自己停了

看门狗触发的急停。急停条上会显示原因（tick 持续迟到 / 连续 CAN 读失败 / 关节握持中漂移）。三个条件都要求**持续**而非单次 —— 抖动一下、丢一帧不会触发。

### 前端 3D 视图空白

URDF 或 mesh 没加载到。submodule 是否 init 过？`curl -I http://127.0.0.1:18790/assets/urdf/00-arm-rs_asm-v3/urdf/00-arm-rs_asm-v3.urdf` 应该返 200。

---

## 架构

从 `backend/core/controller.py` 开始读 —— 那是控制循环，一屏之内能看完，急停路径也在里面。

| 层 | 职责 | 边界 |
|---|---|---|
| `backend/arm/` | 薄封装上游 `RebotArm`；`SimArm` 同接口 | **不实现运动学** |
| `backend/safety/` | 急停闩锁、看门狗、限位与自碰撞 | 闩锁**不碰硬件** |
| `backend/core/` | 控制循环、执行器、浮动/锁定、广播 | 执行器纯逻辑，注入时钟/arm/shutter |
| `backend/routines/` | Routine 数据模型 + 一文件一 JSON 存储 | |
| `backend/shutter/` | ESP32 行协议客户端；`SimShutter` 同接口 | |
| `backend/api/` | REST + WebSocket | 只调 controller 和 store |
| `backend/agent.py` | 外部 agent 的独占控制租约 | 给控制权，不给安全豁免 |
| `frontend/` | Vite + React + TS | 指令走 REST，`/ws` 只读 |
| `firmware/esp32-shutter/` | PlatformIO 工程 | |
| `deploy/` · `config/` | systemd unit + udev 规则；从上游 fork 的硬件配置 | |

任何测试里不出现 `time.sleep`，时钟统一走可注入接口。完整代码地图与不能破的约定见 [`AGENTS.md`](./AGENTS.md)。

---

## API

交互式文档 `http://127.0.0.1:18790/docs`，OpenAPI 在 `/openapi.json`。

| | |
|---|---|
| `GET/POST /api/estop` · `POST /api/estop/clear` | 急停。engage 永远 200，重复 engage 保留首因 |
| `GET/POST /api/routines` · `GET/PATCH/DELETE /api/routines/{id}` | 序列 CRUD |
| `POST /api/routines/{id}/waypoints` · `…/capture` · `…/reorder` · `PATCH/DELETE …/{index}` | 点位编辑与录点 |
| `POST /api/routines/{id}/play` · `POST /api/playback/stop` | 播放。play 前对整条序列做限位与碰撞预检 |
| `POST /api/teach` | 零力示教开关 |
| `POST /api/shutter/test` | 快门链路自检。默认只 ping，`?shoot=true` 才真拍 |
| `GET /api/control` · `GET /api/health` · `GET /api/logs` · `WS /ws` | 状态与日志 |

**所有会让臂动的端点在急停期间返 409 并带原因。** 有一条测试遍历路由表，新增端点忘挂闸门就会失败。

### Agent API

外部 LLM / 脚本控制机械臂。OpenAPI 直接给 OpenClaw / LangChain 做 tool import。

| | |
|---|---|
| `POST /api/agent/acquire` | 取得独占控制权，返 token。已被占用返 409（**不排队** —— 两个调用方交错下指令会产生谁都没要的运动） |
| `POST /api/agent/control/joints` | 关节指令。头带 `X-Agent-Token`。做限位、自碰撞、单次最大 1.5 rad 校验 |
| `POST /api/agent/control/play/{id}` · `/stop` | 播放 / 停止序列 |
| `POST /api/agent/release` · `?force=true` | 交还控制权。`force` 免 token，给 Web UI 强制收回用 |
| `GET /api/agent` | 谁持有控制权，还剩多久 |

**租约会自己过期** —— 空闲 5 分钟，或持有满 30 分钟（不管有没有活动）。崩掉的 agent 不会一直占着臂。

**租约给的是控制权，不是安全豁免。** agent 的运动端点挂着和 Web UI 同一个急停闸门 —— 急停期间拒绝 agent，和拒绝人一模一样。

---

## 坑清单

**这四条会静默出错 —— 不报错，只是结果是错的。**

- **急停不能用 `RebotArm.estop()`。** 它的实现就是一行 `self.disable_all()`（`rebotarm.py:687`）—— 电机失能、力矩归零、臂自由落体。MotorBridge 文档也把 `disable_all()` 写成 "Emergency stop all motors"，但那是掉电语义。本项目的急停是**保持力矩**。有一条测试走 AST 扫 `backend/` 下每个模块，出现这两个名字的属性访问就失败。
- **上游默认资产解析指向另一条臂。** 上游 `vendor/reBotArm_control_py/config/rebotarm.yaml` 的 `hardware_yaml` 写的是 `rebotarm_dm.yaml`（B601-**DM**）。`load_robot_model()` 不传参会静默加载错误机器人的 URDF —— 文件存在所以不报错，只是 FK、重力补偿、碰撞全算错。一律走 `backend/assets.py`。
- **`mechVel (0x701A)` 不是 rad/s。** 速度必须位置差分算。浮动/锁定的判据正是速度，读错会锁不住或锁太早。
- **XIAO ESP32-S3 必须加 `-D ARDUINO_USB_CDC_ON_BOOT=1`。** 否则 `Serial` 走 UART0 引脚而不是 USB。板子会枚举、主机能打开端口、每次写入都成功 —— 然后什么都收不到，整条链路上没有任何一处报错。

**其它：**

- **URDF 是 8 自由度，硬件是 7 关节**（夹爪一个电机驱两个米制平移指关节），**不是 1:1**。夹爪不做限位校验、不给重力前馈 —— 没有标定过的换算，编一个再去信它更糟。
- **`joint2` / `joint3` 下限恰好是 `0.0`**，而静止伸直姿态就是 q=0 —— rest pose 压在限位边界上，限位校验必须留容差（当前 0.02 rad），否则臂会因为「站着不动」被拒。
- **每关节 0.2–0.5 N·m 库仑摩擦**，重力模型补不掉，靠位置环刚度撑。
- **测重力补偿前把臂摆到无接触姿态**（肘朝上的「L」形）。碰桌面或连杆互相接触会产生假的标定误差。

完整清单与源码级证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。

---

## 上游依赖

臂层不自己写。运动学、动力学、重力补偿、轨迹规划、URDF 全部来自 [`Seeed-Projects/reBotArm_control_py`](https://github.com/Seeed-Projects/reBotArm_control_py)，以 **git submodule** 锁在 `vendor/`（它没有 `[build-system]`，装不了 git 依赖）。3D 查看器的资产组织参考 [`Seeed-Projects/rebot_arm_webui`](https://github.com/Seeed-Projects/rebot_arm_webui)。快门用 [`maxmacstn/ESP32-Canon-BLE-Remote`](https://github.com/maxmacstn/ESP32-Canon-BLE-Remote)（是**库不是固件**，何时按快门归调用方管，所以本仓库自己写了一层薄固件）。

## 文档

| | |
|---|---|
| [`AGENTS.md`](./AGENTS.md) | 改代码前读。铁律、代码地图、不能破的约定 |
| [`PROGRESS.md`](./PROGRESS.md) | 进度状态机、阻塞项、交接协议 |
| [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md) | 硬件事实。**已验证**与**待实测**严格分开 |
| [`firmware/esp32-shutter/README.md`](./firmware/esp32-shutter/README.md) | 烧录、配对、串口协议 |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | 原始设计与决策记录 |

## License

MIT
