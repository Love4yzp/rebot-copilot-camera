# Teach & Repeat · 可编程空间点位应用

[English](./README.md) | **中文**

> **教它走一遍，它替你走一万遍。**
> Teach it once, it walks it a thousand times.

用手把臂拖到一个位置，松手，按一下记录 —— 这就是一个点位。给点位挂上动作（快门是第一个），按播放：臂自己走遍全程，每到一处停稳、执行。

```
教                        存                         拍
拖到位 → 松手 → 记录  →   有序点位 + 每点挂动作  →   到位 → 稳定 → 快门 → 下一点
```

第一个落地场景是自动化多视角拍摄：reBot-RS 六轴臂夹佳能相机，被拍物体固定不动。照片留在相机 SD 卡里 —— 本项目只管把臂开到位、把快门按下去。

> 软件已完成，464 个测试；两项硬件实测待做。改代码前读 **[AGENTS.md](./AGENTS.md)**（四条违反了不报错、只让结果错的铁律）。

---

## 什么东西在哪

| 分区 | 目录 |
|---|---|
| 程序 | `app/backend/`（内核、编排引擎、插件层、API —— 分层见 `docs/ARCHITECTURE.md`）、`app/frontend/`（界面 + 开发 mock + 契约 runner）、`app/firmware/esp32-shutter/`、`app/vendor/reBotArm_control_py/`（锁版本的 submodule） |
| 配置与数据 | `app/config/`（硬件 yaml + 操作者调参）、`app/data/`（运行时位姿 / 序列 / 模板，不入 git） |
| 部署 | `app/deploy/`（systemd unit + udev 规则） |
| 知识 | `AGENTS.md`（agent 手册）、`docs/`（架构锚点、硬件事实）、`PROGRESS.md`、本 README |
| 验证 | `app/tests/`、`app/contract/cases/`（golden 契约用例） |
| 入口 | `./dev.sh`（全在本机跑）、`./device.sh`（每条命令经 ssh 落到设备） |

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
cd app && uv sync                       # 应用整体在 app/ 里，下面所有命令都在 app/ 下执行
cd app/frontend && npm install && npm run build
```

已经克隆过但漏了 `--recursive`：`git submodule update --init`。

**这步不能跳。** 臂控制库是 submodule 不是 pip 依赖（上游没有 `[build-system]`，装不成 git 依赖）。漏了的话 `uv sync` 照样成功，然后 import 失败。

---

## 试跑（不接任何硬件）

```bash
cd app && uv run -m backend.app --sim
```

开 **http://127.0.0.1:18790**。模拟臂会响应示教拖动、走点位、假装按快门 —— 整个流程都能走通。

改前端：`cd app/frontend && npm run dev`（热更新，自动 proxy 到 18790）。
跑测试：`cd app && uv run pytest`。

`./dev.sh` 把本机启动包成两种模式 —— `./dev.sh prod` 构建前端并起后端，同一个源（无硬件加 `--sim`）；`./dev.sh sim` 只起前端。API 联调不起前端：`./dev.sh prod --no-build`，/docs 即控制台（需已构建过一次前端）。旧名 `mock` 仍是 `sim` 的别名，过渡期后移除。**不管哪种模式，后端控臂的安全措施（急停闩锁 / 运动闸门 / 看门狗）都在。** 部署到设备是另一个脚本 `./device.sh`，见「部署到 R2x」一节，日常使用不需要它。

**不启动后端也能预览前端**：`./dev.sh sim`，或 `cd app/frontend && npm run dev:mock`。API、WebSocket 状态流和 3D 臂全部由内存 mock 顶替 —— 列表 / 示教 / 录点 / 播放 / 急停都能走通，只是数据是临时的。3D 臂要读 vendor 里的 URDF，先 `git submodule update --init`；启动后开 http://localhost:5173。

---

## 拍一组

### 1 · 启动，然后确认它连上了真臂

臂接 CAN、ESP32 接 USB、相机装夹爪上。**不加 `--sim`**：

```bash
cd app && uv run -m backend.app
curl -s http://127.0.0.1:18790/api/health | grep simulated    # 必须是 false
```

**这一步别省。** 不加 `--sim` 时连不上真臂会拒绝启动。`simulated: true` 表示你显式要了模拟器。

### 2 · 配对相机（一次性）

1. 机身菜单 `无线通信设置 > 蓝牙功能` 设成 **「遥控」**（不是「智能手机」）。不设这个配不上。
2. 机身选「配对」，进入等待。
3. `curl -X POST http://127.0.0.1:18790/api/shutter/pair`（详见 [固件说明](./app/firmware/esp32-shutter/README.md)）。
4. 配对存在板子上，之后上电自动重连。

验证整条链路 —— **会真拍一张**：

```bash
curl -X POST 'http://127.0.0.1:18790/api/shutter/test?shoot=true'
```

不带 `?shoot=true` 不烧帧，但仍然两段链路一起查：返回里 `connected` 是 USB 那一段，`camera` 是 BLE 那一段。**只有后者能回答「按下去会不会真的拍」** —— 板子好好的而相机根本没配对，是这台机器最贵的那种沉默故障。

### 3 · 录位姿

素材库底栏「+ 录位姿」，底部出现示教条：

1. 臂**先握持不动** —— 没人扶住就松劲的臂会垂下去。
2. **推它一下**，臂测到运动就放开，变成零力浮动，可以自由拖。
3. 拖到位，**松手**。停手约 0.25 秒后自动锁在那里。
4. 命名，按「保存位姿」。重复 2–4 录下一个。

位姿进素材库，被任意多条序列**链接**复用 —— 改一个位姿，所有引用一起变。日常的「点哪去哪」在每张位姿卡上：「去这里」。示教条自带一个急停按钮 —— 这个模式下你的手在臂上，不在键盘上。

### 4 · 排时间轴

把位姿卡从素材库**拖上时间轴**就是一个站位（保持块）。两个不同位姿之间自动生成过渡块 —— 臂必须物理地过去，这不是设置，是物理：过渡块不可删，只可改时长与缓动。

- 拖保持块右缘修剪时长；按住整块拖动重排。
- **双击块**钉事件标记：快门、等待、或任何已装插件（如转台）。标记钉在块内时间点上，随父块移动与修剪。
- 选中块或标记，右侧检查器改参数；`Delete` 删除选中。

当前序列可「存为模板」：只存结构（站位 / 时长 / 标记 / 过渡参数），**不存关节角**。模板卡的「用它」进**逐站位向导**：每一站把臂拖过去录一个新位姿、或从库里选已有的（可先把臂「去这里」开过去确认），最后生成一条脱钩的普通序列 —— 之后改模板、删模板，已生成的序列纹丝不动。

### 5 · 预演与执行

两个动词，不共用一个按钮：

- **▶ 预演**：播放头走计划尺，监视器播灰阶模拟（过渡的缓动肉眼可见），**臂一动不动**。预演不是机器状态，四个状态色一个都不亮。
- **执行（臂会动）**：真臂跑。播放头走真实进度，监视器翻为实况，琥珀色点亮，时间轴锁定到本轮结束。

等待标记对两者都生效：播到它停住，点「继续」才走。执行前对**整条序列**做限位与自碰撞预检，包括相邻两位姿之间的路径 —— 两个各自合法的位姿，中间的直线可能穿过臂自己的底座。不合法直接拒绝，**臂一动不动**。

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

状态字与颜色永远一起出现。**没亮绿就是界面不知道臂在哪** —— 被急停冻住或被人手动推过之后就是这样，这不是 bug。

---

## 急停

**顶栏红色大按钮，或按 `Esc`。** 播放中、示教中都能按。

- 臂**保持力矩钉在原地**，不掉电、不松劲。
- 所有会让臂动的请求返 409 并带原因。
- **解除后原地待命，不自动续跑** —— 到你解除时现场大概率已经变了（臂被拖开、样品拿走）。

除了人按，看门狗也会自动触发：控制循环持续迟到、连续 CAN 读失败、握持中关节持续漂移。原因显示在急停条上。

## 退出

**Ctrl+C（或 `systemctl stop`）不会立即退出**：先把臂慢速开回零位（全关节 q=0，约 14°/s，最长约 45 秒），到位后才停控制循环、终止进程。回零途中再按 Ctrl+C 不会加速也不会打断 —— 重复信号一律忽略。进程结束后电机保持上电，把臂钉在零位。

例外：**急停吸合时退出不回零**，臂保持冻结姿态结束进程 —— 急停意味着出了状况，此时规划新运动正是急停要防的事。

---

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `REBOT_HOST` | `127.0.0.1` | 监听地址。**改成 `0.0.0.0` 等于把机械臂控制权开放给整个网络，本项目没有认证层** |
| `REBOT_PORT` | `18790` | 端口 |
| `REBOT_DATA_DIR` | `./app/data` | 操作者数据根目录：`poses/`、`sequences/`、`templates/` 三个库都在它下面，一文档一 JSON |
| `REBOT_SHUTTER_PORT` | `/dev/rebot-shutter` | 快门板串口。udev 给的稳定名，别用 `/dev/ttyACM*`（插拔顺序会换号，指到别的 CDC 设备上看起来就是相机坏了）|
| `REBOT_SHUTTER_BAUD` | `115200` | 快门板波特率。改了要同步改固件的 `-D REBOT_SERIAL_BAUD` |
| `REBOT_TUNING_FILE` | `./app/config/tuning.yaml` | 调参面板的落盘文件。文件缺失 = 默认值 |

命令行：`--sim` / `--host` / `--port`。
`device.sh` 另需设置 `REBOT_HOST_SSH`（无默认值，指向你的设备，如 `recomputer@192.168.1.10`），另认 `REBOT_REMOTE_DIR`。

**调参面板**（监视器区右侧「调参」按钮，prod 下进入需确认）：浮动手感 kp/kd、浮动/锁定阈值、到位判定、进站限速、负载 profile（bare/camera/gripper）。改动立即热生效 —— 示教浮动中也可以边掰边调 kp/kd；但负载 profile 切换要求臂不在浮动，序列执行中拒绝一切写入。热改只进内存，点「保存到配置」才写进 `app/config/tuning.yaml`；「恢复已保存」随时回到上次保存。

**挂上相机后**：整机（机身+支架）称重，把质量填进面板的「负载 → 相机质量」，质心相对末端法兰的偏移填 com，切到 camera profile，然后用浮动漂移手感复核 —— 点「+ 录位姿」进零重力，臂应该原地不动；漂移就是重力前馈不准（逐关节修正流程见 `docs/HARDWARE_NOTES.md` #B2）。不再需要改代码里的常量。

---

## 部署到 R2x

先把 `device.sh` 指向你的设备 —— 仓库不含任何内置目标：

```bash
export REBOT_HOST_SSH=recomputer@<设备IP>   # recomputer 是 reComputer 的出厂默认用户

./device.sh setup     # 一次性：uv + systemd + CAN + udev + 权限组
./device.sh push      # 改完代码：build 前端 + rsync + 重启
./device.sh enable    # 开机自启
./device.sh status    # 在不在跑，跑在真臂还是模拟器上
./device.sh logs      # tail journalctl
./device.sh open      # SSH 隧道 + 开浏览器
./device.sh run       # 前台跑，调 print/breakpoint 用
```

**没有认证层**，而这个服务能让一条 48V 的臂动起来。两种部署方式：

**仅本机访问（默认，仓库里 unit 的配置）**

服务只听 `127.0.0.1`，外部走 SSH 隧道：`./device.sh open` 会建好隧道并打开浏览器。适合不信任所在网络的场景。

**局域网访问（reComputer 上的常见形态）**

设备装在 reComputer 上、同一局域网里其它主机要直接开界面时：把 `app/deploy/rebot-copilot-camera.service` 里的 `Environment=REBOT_HOST=127.0.0.1` 改成 `0.0.0.0`（或设备在局域网的固定 IP），`push` 之后网内访问 `http://<设备IP>:18790`。注意**任何能摸到这个端口的人都能让臂动** —— 只在自己可控的局域网里这么开；绑固定 IP 比 `0.0.0.0` 少一层「设备换了网络跟着暴露」的意外。

网络不可信、又需要远程访问时，不要把服务直接暴露出去：在 localhost 服务前面挡一个带认证的反向代理（Caddy / nginx 的 basic auth 即可），或走带 ACL 的私有网络（WireGuard / Tailscale 之类）。认证是部署层的职责，不是这个应用的 —— 这类配置属于部署现场，不进仓库。

`push` **不删设备上的 `app/data/`** —— 操作员现场示教出来的点位和序列都在那里，只存在于设备上。

---

## 故障排查

| 现象 | 多半是 | 怎么办 |
|---|---|---|
| 服务在跑，臂一动不动 | 用了 `--sim`，或连的是残留模拟进程 | `curl -s :18790/api/health \| grep simulated`。不加 `--sim` 时连不上真臂会拒绝启动 |
| macOS 上退回模拟器，日志 `load PCBUSB failed` | 缺 MacCAN 的 CAN 运行时 —— macOS 没有 SocketCAN，CAN 传输走 `libPCBUSB.dylib`（支持 PEAK 及 PEAK 兼容适配器，如 XCAN-USB） | 把 `libPCBUSB.dylib` 装进 `~/.local/lib/` 并建一个名为 `PCBUSB` 的软链指向它（motorbridge 仓库 `third_party/pcan/macos/` 有打包好的）。`./dev.sh prod` 会自动注入 dyld 搜索路径；直接 `cd app && uv run -m backend.app` 要自己设 `DYLD_FALLBACK_LIBRARY_PATH="$HOME/.local/lib:/usr/local/lib:/usr/lib"` |
| `import reBotArm_control_py` 失败 | submodule 没拉 | `git submodule update --init` |
| 按播放返 **400** | 有点位越限 / 自碰撞，或相邻两点之间路径穿模 | 看返回体 `detail.reasons`，会指到具体关节或路段。注意**录点时不拒绝只警告**（臂物理上就在那），检查发生在播放前 |
| 按播放返 **409** | 急停闩着，或已在播 / 在示教 | `detail` 里写了是哪种 |
| 臂拖不动 | 没开示教，或急停闩着 | 示教开着时臂**起手是握持的**，推一下才放开 —— 这是设计不是卡住 |
| 快门自检通过，播放时拍不到 | 相机睡了或拒绝了 —— `camera: true` 只说明问的那一刻它连着 | 用 `?shoot=true` 测整条链路。一般是相机睡了、蓝牙没设成「遥控」、或板子重启丢了配对（用 `POST /api/shutter/pair` 重配）|
| 主机完全收不到 ESP32 任何数据 | `platformio.ini` 少了 `-D ARDUINO_USB_CDC_ON_BOOT=1` | 加上重烧。少了它 `Serial` 走 UART0 引脚，板子照常枚举、端口能开、写入都成功，**全链路无一处报错** |
| `/api/logs` 是空的 | 服务账号不在 `systemd-journal` 组 | `./device.sh setup` 会加，加完要重新登录 |
| journalctl 里中文变 `?` | systemd 默认 `LANG=C` | unit 和 `device.sh run` 都已设 `LANG=zh_CN.UTF-8` |
| 臂突然自己停了 | 看门狗触发的急停 | 急停条上有原因。三个条件都要求**持续**，抖一下、丢一帧不会触发 |
| 前端 3D 空白 | URDF / mesh 没加载 | 抽屉里会写明是「加载失败」「网格缺失」还是「3D 无法初始化」，照着那句查。最常见是 submodule 没拉：`git submodule update --init`。自查 `curl -I :18790/assets/urdf/00-arm-rs_asm-v3/meshes/base_link.STL` 应返 200 —— 注意 mesh 在**包根**下，不在 `urdf/` 里 |
| 一直不亮绿（到位） | 臂被急停或示教动过 | 这是对的。臂被冻在别处或被人推走之后，界面不再声称知道它在哪 —— 对任意位姿「去这里」或重跑一次即可 |

---

## API

交互式文档 `http://127.0.0.1:18790/docs`，OpenAPI 在 `/openapi.json`。

| | |
|---|---|
| `GET/POST /api/estop` · `POST /api/estop/clear` | 急停。engage 永远 200，重复 engage 保留首因 |
| `GET/POST /api/poses` · `PATCH/DELETE /api/poses/{id}` | 位姿库。`POST /api/poses/capture` 录下臂当前姿态 |
| `GET /api/poses/{id}/links` | 哪些序列引用了这个位姿 —— 删除/覆盖前先问 |
| `POST /api/poses/{id}/goto` | 单位姿：过去、保持。可带 `{"source": "..."}` 记录是谁触发的 |
| `GET/POST /api/sequences` · `GET/PATCH/DELETE /api/sequences/{id}` | 序列 CRUD。块写入即归一化（过渡块自动生成）；运行中的序列锁定不可改 |
| `POST /api/sequences/{id}/execute` · `POST /api/execute/stop` · `POST /api/execute/resume` | 执行。execute 前做整条预检（路径 + 位姿引用 + 插件可用性）；resume 从等待标记继续 |
| `GET/POST /api/templates` · `DELETE /api/templates/{id}` · `POST /api/templates/{id}/instantiate` | 结构配方（位姿槽位）；实例化 = 把每个槽位绑到库位姿上复印一份 |
| `POST /api/teach` | 零力示教开关 |
| `POST /api/shutter/test` | 快门自检。查 USB 与 BLE 两段链路，`?shoot=true` 才真拍 |
| `POST /api/shutter/pair` | 让板子进入 BLE 配对模式并等相机（30 秒）。播放中返 409 |
| `GET /api/plugins` · `POST /api/app/plugins/probe` | 装了哪些动作插件、可不可用。前端据此渲染触发表单 |
| `GET /api/control` · `/api/health` · `/api/logs` · `WS /ws` | 状态与日志 |
| `WS /api/events` | 语义事件流：到位 / 动作 / 急停。给集成方用，不含 20Hz 关节角 |

**所有会让臂动的端点在急停期间返 409 并带原因。**

**扩展这台机器**：动作插件（进程内 —— 把带 `plugin.json` 的文件夹丢进 `app/plugins/`，或 `uv pip install` 一个声明了 `rebot.actions` entry point 的包）、触发源（打 `goto` 的 HTTP 客户端）、事件订阅（连 `/api/events` 的 WS 客户端）。三个扩展点的完整契约与无硬件开发循环 `uv run -m backend.actions.check` 在 [`docs/PLUGINS.md`](./docs/PLUGINS.md)；完整例子是一个**可安装的包** [`app/examples/rebot-plugin-turntable/`](./app/examples/rebot-plugin-turntable/)，不是文档里的代码块 —— 打包元数据本身有测试覆盖。

**Agent API**（`/api/agent/*`）给外部 LLM / 脚本用：`acquire` 拿独占 token，`control/joints` 和 `control/play/{id}` 下指令，`release` 交还（`?force=true` 让 Web UI 强制收回）。租约空闲 5 分钟或持有满 30 分钟自动过期。**给的是控制权不是安全豁免** —— 急停期间拒绝 agent，和拒绝人一模一样。完整参数看 `/docs`。

---

## 更多

| | |
|---|---|
| [AGENTS.md](./AGENTS.md) | 改代码前读：四条铁律、代码地图、不能破的约定、架构分层 |
| [docs/HARDWARE_NOTES.md](./docs/HARDWARE_NOTES.md) | 硬件事实与坑，**已验证**和**待实测**严格分开 |
| [PROGRESS.md](./PROGRESS.md) | 进度、阻塞项、交接协议 |
| [app/firmware/esp32-shutter/](./app/firmware/esp32-shutter/README.md) | 烧录、配对、串口协议 |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | 原始设计与决策记录（已归档，不再追加） |

臂层不自己写 —— 运动学、动力学、重力补偿、轨迹规划、URDF 全部来自 [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py)。

MIT
