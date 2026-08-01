# 机械臂自动多视角拍摄

用手把机械臂拖到你想要的机位，松手，按一下记录。教完一圈之后按播放 —— 臂自己走遍所有机位，每到一处停稳、按快门。

```
教                        存                         拍
拖到位 → 松手 → 记录  →   有序点位 + 每点挂动作  →   到位 → 稳定 → 快门 → 下一点
```

reBot-RS 六轴臂夹佳能相机，被拍物体固定不动。照片留在相机 SD 卡里 —— 本项目只管把臂开到位、把快门按下去。

> 软件已完成，252 个测试；两项硬件实测待做。改代码前读 **[AGENTS.md](./AGENTS.md)**（四条违反了不报错、只让结果错的铁律）。

---

## 需要什么

| | | 没有的话 |
|---|---|---|
| reBot-RS 机械臂 | 6 关节 + 夹爪，RobStride，48V，CAN | `--sim` 跑模拟臂，除真运动外全部可用 |
| USB-CAN 适配器 | 主机 ↔ 臂 | 同上 |
| 佳能相机 | 机身要支持蓝牙遥控 | `SimShutter`，快门调用只记日志 |
| XIAO ESP32-S3 | 快门桥：USB 连主机，BLE 连相机 | 同上 |
| reComputer R2x | 部署目标 | 开发机直接跑 |

软件：**uv**、**Node 18+**、Python 3.11（uv 自己装）。
运动学、动力学、碰撞检查在 macOS 和 Linux 开发机上都跑得动 —— **只有 CAN 传输层需要真机**。

---

## 安装

```bash
git clone --recursive https://github.com/Love4yzp/rebot-copilot-camera.git
cd rebot-copilot-camera
uv sync
cd frontend && npm install && npm run build && cd ..
```

已经克隆过但漏了 `--recursive`：`git submodule update --init`。

**这步不能跳。** 臂控制库是 submodule 不是 pip 依赖（上游没有 `[build-system]`，装不成 git 依赖）。漏了的话 `uv sync` 照样成功，然后 import 失败。

---

## 试跑（不接任何硬件）

```bash
uv run -m backend.app --sim
```

开 **http://127.0.0.1:18790**。模拟臂会响应示教拖动、走点位、假装按快门 —— 整个流程都能走通。

改前端：`cd frontend && npm run dev`（热更新，自动 proxy 到 18790）。
跑测试：`uv run pytest`。

**不启动后端也能预览前端**：`cd frontend && npm run dev:mock`。API、WebSocket 状态流和 3D 臂全部由内存 mock 顶替 —— 列表 / 示教 / 录点 / 播放 / 急停都能走通，只是数据是临时的。3D 臂要读 vendor 里的 URDF，先 `git submodule update --init`；启动后开 http://localhost:5173。

---

## 拍一组

### 1 · 启动，然后确认它连上了真臂

臂接 CAN、ESP32 接 USB、相机装夹爪上。**不加 `--sim`**：

```bash
uv run -m backend.app
curl -s http://127.0.0.1:18790/api/health | grep simulated    # 必须是 false
```

**这一步别省。** 连不上真臂时服务会自动退回模拟器继续跑 —— 界面、日志、按钮全都正常，只有臂不动。

### 2 · 配对相机（一次性）

1. 机身菜单 `无线通信设置 > 蓝牙功能` 设成 **「遥控」**（不是「智能手机」）。不设这个配不上。
2. 机身选「配对」，进入等待。
3. `pio device monitor` 里手打 `#1 PAIR`（详见 [固件说明](./firmware/esp32-shutter/README.md)）。
4. 配对存在板子上，之后上电自动重连。

验证整条链路 —— **会真拍一张**：

```bash
curl -X POST 'http://127.0.0.1:18790/api/shutter/test?shoot=true'
```

不带 `?shoot=true` 只发 PING，那只证明主机和板子通，**不证明相机能拍**。

### 3 · 教机位

顶栏「+ 新建」建集合 —— 选「空白集合」自己录，或选「四方位向导」跟着提示依次录正面 / 右 45° / 侧面 / 俯拍。

进配置模式（底栏「编辑」）后按「+ 录锚点」，底部出现示教条：

1. 臂**先握持不动** —— 没人扶住就松劲的臂会垂下去。
2. **推它一下**，臂测到运动就放开，变成零力浮动，可以自由拖。
3. 拖到位，**松手**。停手约 0.25 秒后自动锁在那里。
4. 按「保存」。重复 2–4。

示教条自带一个急停按钮 —— 这个模式下你的手在臂上，不在键盘上。

### 4 · 配每个锚点

配置模式下点一张卡片：

| 字段 | 作用 |
|---|---|
| 名称 | 卡片上显示的字，比如「正面 45°」 |
| 速度 | 慢 / 标准 / 快，映射到底层的到位用时 |
| 触发 | 开关。开了之后设次数（连拍）、间隔、是否先对焦 |

卡片下方的「试跑」单独跑这一个锚点，不用退出配置模式就能验证录得对不对。`←` `→` 重排顺序。删掉的锚点有 8 秒撤销。

失败策略、稳定等待这些不出现在界面上 —— 固定为合理默认值。**臂停稳和照片不糊之间差几百毫秒**，稳定等待由后端统一保证。

### 5 · 用它拍

退出配置模式。**点一张卡片，臂就过去、稳定、按你配的次数拍、然后停在那里。** 一次点击是一个完整动作，这是日常唯一要做的事。键盘 `1`–`9` 对应卡片角上的编号，发数字的脚踏也能用。

底栏「播放全部」按顺序跑完整个集合。开始前对**整条序列**做限位与自碰撞预检，包括相邻两点之间的路径 —— 两个各自合法的点位，中间的直线可能穿过臂自己的底座。不合法直接拒绝，**臂一动不动**。

### 界面怎么读

屏幕本体是灰的。**出现任何颜色，都表示机器正在做某件事** —— 所以站在臂边用余光扫一眼就够，不用凑到屏幕前读字。最顶上那条贯穿整屏的光带是主要信号：

| 颜色 | 含义 |
|---|---|
| 暗 | 待命 |
| 琥珀色扫动 | 臂在移动，别伸手 |
| 琥珀色常亮 | 示教中，臂已卸力可以推 |
| 白色一闪 | 快门触发了 |
| 绿色 | 到位，臂保持在那里 |
| 红色脉冲 | 已急停 |

卡片上同时有文字，颜色和字永远一起出现。**卡片不亮绿就是界面不知道臂在哪** —— 被急停冻住或被人手动推过之后就是这样，这不是 bug。

---

## 急停

**顶栏红色大按钮，或按 `Esc`。** 播放中、示教中都能按。

- 臂**保持力矩钉在原地**，不掉电、不松劲。
- 所有会让臂动的请求返 409 并带原因。
- **解除后原地待命，不自动续跑** —— 到你解除时现场大概率已经变了（臂被拖开、样品拿走）。

除了人按，看门狗也会自动触发：控制循环持续迟到、连续 CAN 读失败、握持中关节持续漂移。原因显示在急停条上。

---

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `REBOT_HOST` | `127.0.0.1` | 监听地址。**改成 `0.0.0.0` 等于把机械臂控制权开放给整个网络，本项目没有认证层** |
| `REBOT_PORT` | `18790` | 端口 |
| `REBOT_ROUTINES_DIR` | `./routines` | 序列目录，一条一个 JSON |

命令行：`--sim` / `--host` / `--port`。
`manage.sh` 另认 `REBOT_HOST_SSH`（默认 `recomputer@r2x`）、`REBOT_REMOTE_DIR`。

**挂上相机后要重调的**（都在代码里，有测试兜着）：

| 常量 | 位置 | 默认 | 为什么 |
|---|---|---|---|
| `FloatLockConfig` 速度阈值 | `backend/core/floatlock.py` | 0.04 m/s | 上游标定是空载的臂，挂相机后手感会变 |
| `DEFAULT_HOLD_KP` / `KD` | `backend/arm/session.py` | 50 / 3 | 负载变了，握持刚度要跟 |
| `LIMIT_TOLERANCE_RAD` | `backend/safety/kinematics.py` | 0.02 | 编码器噪声大就加 |

---

## 部署到 R2x

```bash
./manage.sh setup     # 一次性：uv + systemd + CAN + udev + 权限组
./manage.sh push      # 改完代码：build 前端 + rsync + 重启
./manage.sh enable    # 开机自启
./manage.sh status    # 在不在跑，跑在真臂还是模拟器上
./manage.sh logs      # tail journalctl
./manage.sh open      # SSH 隧道 + 开浏览器
./manage.sh run       # 前台跑，调 print/breakpoint 用
```

只监听 `127.0.0.1`，外部走 SSH 隧道。**没有认证层**，而这个服务能让一条 48V 的臂动起来。

`push` **不删设备上的 `routines/`** —— 那是一个个拖出来的，只存在于设备上。

---

## 故障排查

| 现象 | 多半是 | 怎么办 |
|---|---|---|
| 服务在跑，臂一动不动 | 静默退回了模拟器 | `curl -s :18790/api/health \| grep simulated`。`true` 就是没连上，启动日志里有具体原因 |
| `import reBotArm_control_py` 失败 | submodule 没拉 | `git submodule update --init` |
| 按播放返 **400** | 有点位越限 / 自碰撞，或相邻两点之间路径穿模 | 看返回体 `detail.reasons`，会指到具体关节或路段。注意**录点时不拒绝只警告**（臂物理上就在那），检查发生在播放前 |
| 按播放返 **409** | 急停闩着，或已在播 / 在示教 | `detail` 里写了是哪种 |
| 臂拖不动 | 没开示教，或急停闩着 | 示教开着时臂**起手是握持的**，推一下才放开 —— 这是设计不是卡住 |
| 快门自检通过，播放时拍不到 | 不带 `?shoot=true` 只测了主机↔板子 | 用 `?shoot=true` 测整条链路。链路问题一般是相机睡了、蓝牙没设成「遥控」、或板子重启丢了配对 |
| 主机完全收不到 ESP32 任何数据 | `platformio.ini` 少了 `-D ARDUINO_USB_CDC_ON_BOOT=1` | 加上重烧。少了它 `Serial` 走 UART0 引脚，板子照常枚举、端口能开、写入都成功，**全链路无一处报错** |
| `/api/logs` 是空的 | 服务账号不在 `systemd-journal` 组 | `./manage.sh setup` 会加，加完要重新登录 |
| journalctl 里中文变 `?` | systemd 默认 `LANG=C` | unit 和 `manage.sh run` 都已设 `LANG=zh_CN.UTF-8` |
| 臂突然自己停了 | 看门狗触发的急停 | 急停条上有原因。三个条件都要求**持续**，抖一下、丢一帧不会触发 |
| 前端 3D 空白 | URDF / mesh 没加载 | 3D 抽屉里会写明是「加载失败」还是「网格缺失」。后者最常见 —— URDF 本身返 200 但 `.STL` 全 404，`git submodule update --init` 即可。自查：`curl -I :18790/assets/urdf/00-arm-rs_asm-v3/urdf/meshes/base_link.STL` 应返 200 |
| 卡片一直不亮「已到位」 | 臂被急停或示教动过 | 这是对的。臂被冻在别处或被人推走之后，界面不再声称知道它在哪 —— 重新点一张卡即可 |

---

## API

交互式文档 `http://127.0.0.1:18790/docs`，OpenAPI 在 `/openapi.json`。

| | |
|---|---|
| `GET/POST /api/estop` · `POST /api/estop/clear` | 急停。engage 永远 200，重复 engage 保留首因 |
| `GET/POST /api/routines` · `GET/PATCH/DELETE /api/routines/{id}` | 序列 CRUD |
| `POST /api/routines/{id}/waypoints` · `…/capture` · `…/reorder` · `PATCH/DELETE …/{index}` | 点位编辑与录点 |
| `POST /api/routines/{id}/play` · `POST /api/playback/stop` | 播放。play 前做整条预检 |
| `POST /api/teach` | 零力示教开关 |
| `POST /api/shutter/test` | 快门自检。默认只 ping，`?shoot=true` 才真拍 |
| `GET /api/control` · `/api/health` · `/api/logs` · `WS /ws` | 状态与日志 |

**所有会让臂动的端点在急停期间返 409 并带原因。**

**Agent API**（`/api/agent/*`）给外部 LLM / 脚本用：`acquire` 拿独占 token，`control/joints` 和 `control/play/{id}` 下指令，`release` 交还（`?force=true` 让 Web UI 强制收回）。租约空闲 5 分钟或持有满 30 分钟自动过期。**给的是控制权不是安全豁免** —— 急停期间拒绝 agent，和拒绝人一模一样。完整参数看 `/docs`。

---

## 更多

| | |
|---|---|
| [AGENTS.md](./AGENTS.md) | 改代码前读：四条铁律、代码地图、不能破的约定、架构分层 |
| [docs/HARDWARE_NOTES.md](./docs/HARDWARE_NOTES.md) | 硬件事实与坑，**已验证**和**待实测**严格分开 |
| [PROGRESS.md](./PROGRESS.md) | 进度、阻塞项、交接协议 |
| [firmware/esp32-shutter/](./firmware/esp32-shutter/README.md) | 烧录、配对、串口协议 |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | 原始设计与决策记录 |

臂层不自己写 —— 运动学、动力学、重力补偿、轨迹规划、URDF 全部来自 [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py)。

MIT
