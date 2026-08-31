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
- 每个 commit 结束时代码库必须能跑（`cd app && uv run pytest` 绿、`cd app && uv run -m backend.app --sim` 能起）。
- 需要硬件的工作：代码与测试在开发机写完，实测结果记进 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。不要因为跑不了就跳过写测试；实机验证推翻了实现就开新 commit 修，不回改历史。

---

## ▶ 当前

| 字段 | 值 |
|---|---|
| **Phase** | **参考方案叙事已立档 + 真臂核心先行**：架构锚点重写见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)（平台层 + 参考场景层，新增「出厂 vs 场景落地」排序）。当前主线：**用前端把「手掰示教 → 时间轴回放」闭环跑通**（bare / gripper，近零位安全范围）——不等相机；**配件/插件全停**（场景实地落地才拼）；j2 过补只做最小复现确认，排同日最后。时间轴编辑器三期路线仍见 [`docs/TIMELINE.md`](./docs/TIMELINE.md) |
| **进行中** | ① **真臂核心跑通（bare / gripper，不等相机）**：真臂全链路已跑通——连接✓ 零位✓ goto/运动✓ 到位保持✓ 急停冻结✓（三个真臂-only bug 已修：按组分片、模式锁定→MIT 斜坡、verify 止点假阳性）；夹爪已装回接线（yaml `gripper: true`、7 关节、profile 锁 `gripper`），开合不做。**下一步：浮动手感标定**——j2 重力前馈在展开姿态过度补偿、松手冲到 47°（HARDWARE_NOTES 已记），需加逐关节 k/c 重力修正；标定前勿在展开姿态手掰示教；先用前端把「示教 → 编排 → 回放」闭环跑通（近零位安全范围）。j2 过补只做 10 分钟最小复现确认，仿上游 `--k/--c` 加一次性标定值进 tuning（不重造动力学）。② **五条剩余发现待逐项裁决**：急停后在途快门静默完成（executor abandon 语义）/ idle 不发令且 drift 看门狗同时关闭 / 示教浮动无软限位 / agent `command_joints` 绕路径预检 / 租约独占不约束 UI 端点。③ **配件/插件全停**（出厂应用抛弃插件，场景实地落地才拼）；演示路径已落地（首启种子四方位 demo，`REBOT_SEED_DEMO=0` 可关）。④ 仓库整理（五档 + 收尾）已完成 |
| **上一个完成的** | 本 commit：**收敛 AGENTS.md**（按 writing-for-agents 技能）——① 披露 70 行代码地图到 `docs/CODEMAP.md`，AGENTS 留尖指针（省 always-loaded 上下文，超前模块 parked 标随之落地）；② 命令块裁环境缓存——留五条脚本不写的陷阱（`app/` 子目录约定 / `dev.sh build` 唯一所有者 / dev vs device 是哪台机执行 / mock 是 sim 旧名 / 仅 CAN 需真机），删 12 行脚本能自报的枚举；③ 三条已机器锁住的散文约成指针（层级边界 / 运动闸门 / 插件够不到臂——测试是唯一真相源，散文只留测试说不出的前门陷阱与设计理由）；④ no-op 狩猎（提交约定去「写正常英文散文」这类默认就做的事）。AGENTS ~253→157 行。代码未动，474 仍绿。 |
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
| ~~B1~~ ✅ | **CAN 形态已解（2026-08-14 实测）**：macOS 直连 = XCAN-USB 适配器 + MacCAN `libPCBUSB.dylib`（darwin 后端把 `channel: can0` 映射 PCAN_USBBUS1），yaml 零改动；零位巡检 7 电机全在线。**唯一坑**：直跑 `uv run`（零位示例、`verify_gravity.py`）必须 `export DYLD_FALLBACK_LIBRARY_PATH="$HOME/.local/lib:..."`——`dev.sh prod` 已自动注入，裸跑会报 `load PCBUSB failed` | — | 已解 |
| B2 | 末端挂佳能机身后重力补偿是否还准（上游标定是**空载**，误差 5–11%） | 挡挂相机后的浮动手感实测。`FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益都做成可配就是为了这里重调。裸臂状态已对齐（夹爪质量已剥离，#11） | 相机到货：称重 + 量质心，把等效 `<inertial>` 注入 `gripper_end` 所在 link（`effective_urdf_path` 就是 load-time 改 URDF 的机制，加负载是同一处扩展），然后用浮动漂移手感复核（lift-then-float 流程见 HARDWARE_NOTES #B2）；次选重调阈值 |
| B3 | R2x 上 500 Hz 控制频率能否稳住（yaml 默认值，上游没说在什么算力上测的） | 只影响频率取值。sim 下实测 100 Hz 稳，真机换成上游 `start_control_loop` | 跑控制循环测实际 tick 抖动，不稳就降频并记录 |
| B4 | XIAO ESP32-S3 板子是否在手 | 挡固件烧录验证。固件与主机侧协议都写完了，协议在内存管道上有 30 个测试 | 有板子就 `cd app/firmware/esp32-shutter && pio run -t upload` |
| B5 | 进站限速 0.25 rad/s 是按 demo 安全同步 15°/s 取的，挂相机后未标定 | 进站过快是安全风险，过慢拖拍摄节拍；execute 与 goto 的进站段都走它 | 真机跑进站观察过冲/共振，必要时再降 `executor.py` 的 `FIRST_APPROACH_MAX_SPEED` 并回填实测值 |
| B6 | 到位静止判定 `SETTLE_DRIFT_RAD=0.003` / `SETTLE_MIN_S=0.15` 未在真机标定 | 过紧则差分噪声让臂「永远不到位」、每个序列在进站 deadline abort；过松则糊片风险回升 | 真机跑序列，若见到进站 timeout abort 先放宽这两值到能稳定通过的最紧值并回填（HARDWARE_NOTES「其它待确认」有完整说明） |

---

## 决策速查

四条铁律（急停不能调 `estop()`／不许用上游默认资产解析／速度不能读 `mechVel`／不重造运动学）写在 [`AGENTS.md`](./AGENTS.md)，**只维护那一份** —— 两份副本会漂移，而漂移掉的正是这类救命细节。

源码级证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。
