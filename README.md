# Teach & Repeat · 示教回放

录下命名位姿、编排站位、执行回放。应用负责工作流与安全策略，[reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py) 负责机器人算法和传输。

界面只有位姿库、一个只读 MeshCat 反馈窗口和站位编辑器。默认仿真后端是 MuJoCo，不是浏览器动画。插件、快门、Agent 运行时已移除，未来接入契约保留为[设计说明](docs/PLUGINS.md)。

修改代码先读 [AGENTS](AGENTS.md)。真实硬件禁区和标定证据仍在 [HARDWARE_NOTES](docs/HARDWARE_NOTES.md)。

## 安装与启动

依赖 uv、Python 3.11、Node 22+、Git。只有 prod 需要 CAN 硬件。

```bash
git clone --recursive https://github.com/Love4yzp/rebot-copilot-camera.git
cd rebot-copilot-camera
./dev.sh sim
```

打开 http://127.0.0.1:18790。脚本启动完整后端、安装 physics extra、构建前端。后端只由人用 dev.sh 启动，agent 用 pytest 验证。

漏拉子模块时执行 `git submodule update --init`。dev.sh 自动准备锁定基线上的 SDK 扩展补丁。仅安装依赖：

```bash
cd app
python prepare_sdk.py
uv sync --frozen --extra physics
```

| 命令 | 含义 |
|---|---|
| `./dev.sh sim` | 全栈 + 力矩驱动 MuJoCo，不连接 CAN |
| `./dev.sh prod` | 真臂，连不上拒绝启动，不退回模拟 |
| `./dev.sh build` | 前端构建唯一入口，不启动后端 |
| `./dev.sh status` | 核对 mode 与 arm.backend |
| `./dev.sh sim --local` | 应用仅监听本机 |
| `./dev.sh sim --no-build` | 复用已有前端构建 |

`ui` / `mock` 和 `dev:mock` 已移除。前端热更新仍可用 `cd app/frontend && npm run dev`，但先由人启动后端；Vite 代理 API、控制 WS 和 /viewer，不提供第二套后端。端口预检不能关闭。

## 使用工作台

1. 点「+ 录位姿」。prod 先保持，推动后进入浮动，松手自动锁定。**真实标定限制解决前，只在近零位示教。**
2. sim 在监视器的「详细数据」中短按「− 推动 / ＋ 推动」，每次 0.15 秒；服务端限制为最多 0.2 秒、关节 effort 的 20%。这是仿真力矩输入，不是 CAN 命令。
3. 起名并点「保存」。点击卡片只选中，运动要点「移动到此位姿」；数字键、点击 3D 臂都不会命令运动。
4. 新建序列，点「＋追加」添加站位。编辑保持/过渡时长与等待标记。相邻不同位姿自动生成过渡。
5. sim 点「执行仿真」，prod 点「执行（臂会动）」。离首站较远时先「去起点」。等待时保持，点「继续」后续跑。执行中禁止编辑。
6. 模板只保存结构和位姿槽位，不保存关节角，实例化后是独立序列。

查看器只显示后端反馈。旋转、缩放、复位视角、收起窗口不改变物理状态；画面过期或断连会标注，不能据此认定臂在哪。新运动、示教、急停或断连都清空「已到位」，只有新的 done 反馈能重新点亮。

灰阶是底盘。琥珀表示运动/可推动，绿表示已确认到位保持，红表示急停；白色快门状态当前不使用。选中与装饰不占这些状态色。

## 物理仿真与换末端

仿真用于检查模型响应、跟踪误差、力矩饱和与粗略碰撞，**不替代**真臂、摩擦、减速器间隙、电机固件或新夹爪的标定。当前不做抓取仿真，不虚构夹爪电机到双指行程的映射。

SDK plant 接收与 prod 相同的 ArmSession MIT 位置/速度/kp/kd/重力前馈，按 URDF effort 限幅，1 ms 积分；应用控制循环 100 Hz。读取状态不推进物理。物理不依赖查看器，但控制客户端断连仍触发原有 SafeLock 策略。

用 `REBOT_END_EFFECTOR_FILE` 指向 JSON 文件替换原装固定末端：

```json
{
  "name": "example-tool",
  "mass": 0.2,
  "com": [0, 0, 0.05],
  "box": [0.04, 0.04, 0.10],
  "frame": "gripper_end"
}
```

以上仅是格式示例，**不是真实硬件标定值**。单位 kg、m、kg·m²。可选 `inertia` 为 [xx, yy, zz, xy, xz, yz]，在质心处且坐标轴平行安装框架；盒体中心位于质心。省略惯量时使用均匀盒体估算并标记 `box-estimate`，提供值时校验物理有效性。prod 替换夹爪前必须核实拆装，并在硬件 YAML 设 gripper: false；不能在电机仍配置在线时悄悄移除夹爪质量。更换末端要重启，重力、物理、碰撞和查看器不能各用一份不同模型。

不提供自定义文件时，sim 使用自己的 bare/gripper profile。旧 camera 的质量/质心调参不足以进行动态模拟，需要完整末端描述。真实标定和真实位姿不被改写。运行期间拒绝 payload 切换，应停止后准备新配置再重启。

## 急停与退出

顶部「急停」或 Esc 冻结当前位置，**持续 MIT 力矩与重力补偿**，绝不调用上游失能式停止。闩锁吸合时运动端点拒绝请求，解除后保持、不自动续跑。查看器获得焦点时 Esc 也有效。

Ctrl+C / SIGTERM 先在控制循环运行期间慢速回零，再退出；重复信号不能跳过回零。闩锁吸合时不新发回零运动，原地保持退出。systemd 停止超时保留 60 秒。退出保持与近零位示教的硬件限制见硬件记录。

## 配置与部署

| 配置 | 默认 / 范围 |
|---|---|
| `REBOT_HOST` | 0.0.0.0；不可信网络用 --local 或 127.0.0.1 |
| `REBOT_PORT` | 18790 |
| `REBOT_DATA_DIR` | app/data；真实库为 poses/sequences/templates，sim 在 sim/ 下 |
| `REBOT_TUNING_FILE` | app/config/tuning.yaml，**仅 prod** |
| `REBOT_END_EFFECTOR_FILE` | 可选固定末端 JSON，启动时读取 |
| 仿真调参 | app/data/sim/tuning.yaml，与真实标定隔离 |

「调参」保留浮动增益和阈值热改。执行中拒绝所有调参写入，浮动中还拒绝重力修正；显式保存才写入当前实例的调参文件。

本服务**没有认证**。能访问应用端口的人就能命令臂。内部 MeshCat HTTP/ZMQ 仅绑定回环，同源代理只读，但这不等于应用运动 API 有认证。

明确需要部署设备时：

```bash
export REBOT_HOST_SSH=recomputer@<device-ip>
./device.sh setup
./device.sh push
./device.sh enable
./device.sh status
./device.sh open
```

setup 安装 CAN/应用服务及权限，不再安装快门 udev 规则。push 调 dev.sh build，保护远端数据和真实调参，再重启服务。首次安装需在设备上显式准备和验证真实标定，push 不复制开发机 tuning.yaml。随仓库提供的 unit 仅监听本机，远程通过 SSH 隧道或部署层认证代理访问。不要把未认证的运动 API 暴露公网。

## API 与验证

`/docs`、`/openapi.json` 是当前路由说明：位姿、序列、模板、teach/rest、stop/resume、急停、调参、健康/日志、`/ws` 和 `/api/events`。`GET /api/health` 的 arm.backend 为 hardware 或 mujoco。`GET /api/sim/state` 返回模型计算反馈；`POST /api/sim/perturb` 只在 sim、未急停且示教中可用。

`/api/plugins/*`、`/api/shutter/*`、`/api/agent/*` 不再注册。Sequence schema 为 v3，标记只接受 wait。v2 插件序列不迁移也不删除；用户位姿与标定文件保留。删除的旧源码/示例可从 Git 恢复。

```bash
cd app
uv run --frozen --extra physics pytest
uv run --extra physics ruff check backend tests
uv run --extra physics python -m backend.export_contract --check
uv run --frozen --extra physics python -m backend.validation --output data/validation/current.json --csv data/validation/current.csv --check
cd ..
./dev.sh build
```

REST 对比已提交 golden，normalize 在 Python/TypeScript 双端执行。前端类型来自 OpenAPI，CI 检查漂移。`python -m backend.export_contract` 不启动服务，把 TypeScript 输出到 stdout；审阅后再更新生成文件。

## 排障

| 现象 | 检查 |
|---|---|
| 模式不对 / 运动拒绝 | dev.sh status；prod 不回退，读取 400/409 原因 |
| SDK 导入/补丁失败 | 拉子模块，运行 prepare_sdk.py；保留重叠修改，不要强制 reset |
| 缺 MuJoCo | 使用 dev.sh sim，或安装 physics extra |
| 查看器空白/过期 | 后端是否运行、模型是否有效、/viewer/ws 是否连接；查看器不是控制心跳 |
| macOS PCBUSB 加载失败 | MacCAN libPCBUSB.dylib 放 ~/.local/lib，建立 PCBUSB 链接；dev.sh 注入 dyld 路径 |
| 意外停止 | 看急停/SafeLock 原因，不要关闭看门狗 |
| 日志为空 | 服务用户需要 systemd-journal 组权限 |
| 没有绿色到位 | 旧 done、示教、停止、断连已使认领失效，需要明确的新运动 |

[架构与复用边界](docs/ARCHITECTURE.md) · [代码地图](docs/CODEMAP.md) · [交互](docs/TIMELINE.md) · [未来接口](docs/PLUGINS.md) · [当前状态](docs/PROGRESS.md)
