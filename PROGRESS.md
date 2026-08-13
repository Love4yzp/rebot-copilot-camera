# 做到哪了

**项目状态机。接手一个 session 先读这里。**

本文件只回答三个问题：**现在在哪 / 下一步做什么 / 什么被卡住了**。
铁律与代码约定在 [`AGENTS.md`](./AGENTS.md)，当前设计模式在 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)。

**历史不在这。** 每次改动为什么这么做，写在 git commit message 里（`git log`），比任何文档都详细；原始计划在 [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1)，已归档，只为对照初衷。

---

## 交接协议

接手一个 session：

1. 读 [`AGENTS.md`](./AGENTS.md) —— 四条铁律，违反了不会报错只会让结果错。
2. 读本文件 `▶ 当前` 段 —— 现在在哪、下一步做什么。
3. 看 `🚧 阻塞` 段 —— 下一步是否被未解决的项挡住。
4. 想知道某处为什么这么写：`git log`。
5. 做完 → **在同一个 git commit 里把本文件的状态一起改掉**，不要分开提交，否则状态会和代码漂移。

**规则**：
- 一次只有一个进行中的项。中断时把「做到哪了」写进备注。
- 每个 commit 结束时代码库必须能跑（`uv run pytest` 绿、`uv run -m backend.app --sim` 能起）。
- 需要硬件的工作：代码与测试在开发机写完，实测结果记进 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。不要因为跑不了就跳过写测试；实机验证推翻了实现就开新 commit 修，不回改历史。

---

## ▶ 当前

| 字段 | 值 |
|---|---|
| **Phase** | 时间轴编辑器（三期路线见 [`docs/TIMELINE.md`](./docs/TIMELINE.md)）：v1 前端 / v2 后端迁移已落地；v3 模板向导已落地，剩真机验证 |
| **进行中** | ⓪ **展示模型 v2 前端实施（本次会话）**：HCI 走查（`dev.sh sim` 全流程实测）→ 十项交互改动已落地并构建通过、447 测试全绿、mock 走查逐项验证：① 3D 视图 = 空间素材库——已存位姿渲染为半透明幽灵臂 + 名字标签（数据驱动，`ArmView3D` 按位姿库加载/卸载/重摆），点幽灵 = 与卡片同一条 goto 链路；相机契约六条落地（轨道相机禁平移禁翻滚、俯仰 0.15–π/2、缩放 0.5–3.5、复位/操作者视角双预设、跟随目标默认关且手动拖动即让位；琥珀高亮仅执行态，预演保持灰阶）。② 位姿卡 = 目的地（去「去这里」标签，卡片点击即 goto），常驻按钮只剩「＋追加」，改名/删除进「⋯」菜单，链接数改徽标；素材库移除模板页签与教程段，顶部一行「＋追加 → 排到「序列」末尾」。③ 模板并入「新建序列」对话框三选一：空白 / 从模板（直接进逐站位向导）/ 复制现有。④ 示教条：自动命名预填（位姿 N）、按钮改「保存 / 保存并追加到序列 / × 取消」、关节读数默认折叠、文案「零重力」。⑤ 急停解除 → 自动打开示教条（仅示教上升沿，取消流程不被拉回）；急停理由用户语言（operator pressed stop [ui] → 操作者按下急停）。⑥ 时长去重：总长只留走带条时间码，banner 只报站位数，监视器空闲给行动引导；空序列三步教程改一行提示；调参面板「会被后端拒绝」改操作语言。**待办**：真浏览器人工验收 3D 点击手感与相机预设；配件区入口仍未设计；两个设计原型留档 `docs/presentation-model-v2.html`（2D 整屏骨架）与 `docs/3d-ghost-prototype.html`（真 3D 验证，独立于应用代码）；提交时连同本次改动一起落 commit。① 真机重力前馈验证与手感标定：操作者实测浮动时臂「自己抬起来」。**主嫌疑已定位并修复**：真机无夹爪，但 URDF 留了 0.8004 kg 夹爪质量，幻影力矩 q=0 时 j3 +3.26 N·m（库仑摩擦的 6–15 倍，浮动 kp=2 完全不设防），见 HARDWARE_NOTES #11 的差分表；修复 = `assets.effective_urdf_path()` 随 `gripper: false` 剥掉夹爪连杆质量。**调参面板已落地**（见「上一个完成的」），真机标定路径 = 面板热调 float 增益 + `scripts/verify_gravity.py` 复核（脚本已走修正后的模型）。待操作者在真机上跑 verify 脚本确认裸臂模型落地，再边掰边调 kp/kd。② 结构性审查剩余发现待逐项处置：急停后在途快门静默完成（executor abandon 语义）、idle 模式不发令且 drift 看门狗同时关闭、示教浮动无软限位、agent `command_joints` 绕过路径预检、租约独占不约束 UI 端点。③ 配件区 UI 回归：配对相机 / 测快门 / 重新检测配件的按钮在前端替换时掉了（API 端点还在，组件无调用），配件区入口待设计 |
| **上一个完成的** | **调参面板（Tweakpane）+ 负载 profile 机制**：负载三态枚举 bare（裸臂）/ camera（夹爪位挂相机，须先称重填 mass 否则 422）/ gripper（电机在总线上才存在，不能热加）；链路 = `backend/tuning.py`（pydantic 钳位 + TuningStore）→ `Controller.apply_tuning` 分级闸门（执行中拒一切写入；负载切换额外拒于浮动中——前馈跳几 N·m；float kp/kd 浮动中可改——follow 目标=当前位置跳变为零，边掰边调就靠这条）→ `api/config.py` GET/PUT/save/reset；camera 把 mass+com 注入 `gripper_end` 的 `<inertial>`（`effective_urdf_path` 扩展）；热改只进内存、显式保存落 `config/tuning.yaml`（独立文件——硬件 yaml 是带注释的上游 fork，yaml 往返会吃掉注释）；前端面板停靠监视器区右侧（Tweakpane 全灰阶、prod 进入弹确认、脏段「● 未保存」标记、409/422 原样显示服务器原因、Tweakpane 只在 ev.last 发 PUT）；mock 全镜像 + 契约 case 09-tuning。测试：test_tuning.py（默认值镜像代码常量等 10 条）+ test_config_api.py（闸门/持久化 11 条）。此前完成：**无夹爪时重力模型剥离夹爪质量**（「臂自己往上抬」的主嫌疑）：`gripper: false` 原本只剥电机，URDF 里 0.8004 kg 夹爪质量还在重力模型里，幻影力矩 q=0 时 j3 +3.26 N·m；新增 `assets.effective_urdf_path()`（`effective_hardware_yaml` 同款 load-time 生成），`ArmSession._dynamics_model()` 与 verify 脚本切换，碰撞/运动学仍用 vendor 原 URDF（幻影几何是保守方向）；test_assets.py 三条新测（质量剥离 / 开关行为 / 重力差分）。此前完成：插件「丢文件夹」安装机制（方案 A 落地）：`plugins/<名字>/` 放代码 + `plugin.json`（`module`/`provider`/`enabled`)，启动时 `ActionRegistry.discover_dir()` 扫描，过同一个 `check_shape` 闸门，加载失败/停用都灰显带原因（复用 `_broken`，manifest 形状不变、契约零改动）；`enabled: false` 即开关，状态随插件文件走。动机：手动 `uv pip install` 的插件会被 `uv sync` 清掉（`device.sh push` 每次都 sync)，重启就报模块缺失 —— 丢文件夹不进 lockfile 所以清不掉；`plugins/` 已 gitignore 但随 push 同步。限制写进 PLUGINS.md：只能用宿主环境已有依赖、模块名不能撞、加载仍需重启（「刻意不做」的热重载条目改写为「运行时加载/卸载」并给出理由）。`backend/actions/check` 与 `backend.app` 都扫两条路径。tests/test_plugin_dir.py 9 条（含模块名撞名 → 先加载者赢 → 重复 id 被拒出声）。此前又完成：**急停解除→自动进零重力示教**（操作者实测「解除后臂僵死掰不动」；clear 成功后 `set_teaching(True)`，先锁定、手一动即浮动；后端 + mock + 契约同步，`test_estop_api.py` 两条新测，`test_events_api.py` 一处流程适配）。上一条（浮动沉默 `follow()` + 到位=静止驻留判定）随本批一起待提交 |
| **备注** | `dev.sh` 两种模式：`sim`（仅前端，旧名 `mock` 保留为带警告的别名）/ `prod`（完整启动）；API 联调写法 `prod --no-build`；安全措施与启动模式无关。模式徽标：sim=蓝 / prod=灰阶加粗+✓ / 臂动扫琥珀 / 断连灰阶脉冲，红绿独占不动（规格见 #rebot-arm thread fdf6a140）。模式徽标与连接状态是两个独立维度：断连显示为徽标旁灰阶脉冲「已断连」，不覆盖模式徽标。进入 prod（或 sim→prod 切换）时弹阻断式全屏警告，需点「我已了解」确认；警告层 z-55，低于急停栏（60），Escape 仍是急停快捷键；警告 ⚠ 图标灰白中性（四色纪律：琥珀=臂在动）。插件 `retryable` 无默认值，`check_shape` 注册前拒绝未声明的插件。`device.sh`：`open` 加 `ExitOnForwardFailure`（端口被占立即失败不开死页）；`push` 后 `status` 改为轮询健康检查（30s 超时），冷启动慢不再误报 no response。退出回零已落地：Ctrl+C / SIGTERM 先 `Controller.park_home()` 慢速回零（复用 goto 进站限速）再停循环退出，闩锁吸合时原地冻结退出不回零；信号归 `backend/app.py` 的 `ParkOnExitServer`（uvicorn 原版二次 Ctrl+C 会跳过 lifespan shutdown）；systemd `TimeoutStopSec` 20 → 60；`main()` 碰 CAN 前做端口预检（实测到双实例抢占：绑不上端口的实例已连真臂，退出回零会动别人管的臂）。真机验证退出回零待做（H）。终端日志已着色：`backend/app.py` 的 `_configure_logging()`，TTY 时级别名上 ANSI 色（配色与 uvicorn 一致：WARNING 黄 / ERROR 红），`NO_COLOR` 或重定向/journalctl 时纯文本。udev 规则已审计重写：删掉 CAN 占位规则（臂走 socketcan `can0`，全仓库无代码引用 `/dev/rebot-can`，且 CAN 网卡不是 tty、`SUBSYSTEM=="tty"` 永远匹配不上）；turntable 无规则（插件默认 `/dev/rebot-turntable` 但硬件 VID/PID 未知，到位后补规则或先在设备上设 `TURNTABLE_PORT`）；shutter 规则保留（303a:1001 = 固件 `ARDUINO_USB_CDC_ON_BOOT=1` 的 Espressif TinyUSB 默认，设备上 `lsusb` 确认） |

---

## 环境标记

工作按哪里能验证完划分：

| 标记 | 含义 | 现状 |
|---|---|---|
| `L` | 开发机就能做完（写码 + 单测 + sim） | 可用（macOS） |
| `H` | 需要真臂 / R2x / CAN 总线才能验证 | **暂不可用** |
| `E` | 需要 XIAO ESP32-S3 板子才能烧录验证 | 未确认是否在手 |

`H` / `E` 的工作分两半：代码与测试在开发机写完跑通，实测结果填进 `docs/HARDWARE_NOTES.md` 的「待实测」段。

---

## 🚧 阻塞 / 待验证

这六项**只挡验证，不挡代码** —— 相关实现都已写完并有测试，缺的是在真机上确认数值。

| # | 项 | 影响 | 怎么解 |
|---|---|---|---|
| B1 | CAN 接入形态未定：`config/rebotarm_rs.yaml` 写 `channel: can0`（socketcan），上游 README 又提到 USB2CAN 串口桥 `/dev/ttyACM0` | 挡真臂验证。`ArmSession` 与 `rebot-can.service` 按 socketcan 写的，若实际是串口桥要改这两处 | 在 R2x 上 `ip link show can0` + `ls /dev/ttyACM*`，跑上游 `example/2_zero_and_read.py` |
| B2 | 末端挂佳能机身后重力补偿是否还准（上游标定是**空载**，误差 5–11%） | 挡挂相机后的浮动手感实测。`FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益都做成可配就是为了这里重调。裸臂状态已对齐（夹爪质量已剥离，#11） | 相机到货：称重 + 量质心，把等效 `<inertial>` 注入 `gripper_end` 所在 link（`effective_urdf_path` 就是 load-time 改 URDF 的机制，加负载是同一处扩展），然后跑 `scripts/verify_gravity.py` 复核；次选重调阈值 |
| B3 | R2x 上 500 Hz 控制频率能否稳住（yaml 默认值，上游没说在什么算力上测的） | 只影响频率取值。sim 下实测 100 Hz 稳，真机换成上游 `start_control_loop` | 跑控制循环测实际 tick 抖动，不稳就降频并记录 |
| B4 | XIAO ESP32-S3 板子是否在手 | 挡固件烧录验证。固件与主机侧协议都写完了，协议在内存管道上有 30 个测试 | 有板子就 `cd firmware/esp32-shutter && pio run -t upload` |
| B5 | 进站限速 0.25 rad/s 是按 demo 安全同步 15°/s 取的，挂相机后未标定 | 进站过快是安全风险，过慢拖拍摄节拍；execute 与 goto 的进站段都走它 | 真机跑进站观察过冲/共振，必要时再降 `executor.py` 的 `FIRST_APPROACH_MAX_SPEED` 并回填实测值 |
| B6 | 到位静止判定 `SETTLE_DRIFT_RAD=0.003` / `SETTLE_MIN_S=0.15` 未在真机标定 | 过紧则差分噪声让臂「永远不到位」、每个序列在进站 deadline abort；过松则糊片风险回升 | 真机跑序列，若见到进站 timeout abort 先放宽这两值到能稳定通过的最紧值并回填（HARDWARE_NOTES「其它待确认」有完整说明） |

---

## 决策速查

四条铁律（急停不能调 `estop()`／不许用上游默认资产解析／速度不能读 `mechVel`／不重造运动学）写在 [`AGENTS.md`](./AGENTS.md)，**只维护那一份** —— 两份副本会漂移，而漂移掉的正是这类救命细节。

源码级证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。
