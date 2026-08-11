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
| **进行中** | ① 配件区 UI 回归：配对相机 / 测快门 / 重新检测配件的按钮在前端替换时掉了（API 端点还在，组件无调用），配件区入口待设计。② 真机验证 + `docs/HARDWARE_NOTES.md` 待实测项回填（新增进站限速标定，见 🚧 B5） |
| **上一个完成的** | 进站（臂当前位姿 → 首站）后端硬化：execute 碰撞预检补上「当前位姿 → 首站」段（此前只查相邻位姿，goto 早有、execute 漏了）；进站限速 0.5 → 0.25 rad/s（对齐 demo 安全同步 15°/s，未标定记入待实测）；executor 新增 `approaching` 标志贯通 wire / mock / 契约（前端 UX 半在下一个 commit） |
| **备注** | `dev.sh` 两种模式：`sim`（仅前端，旧名 `mock` 保留为带警告的别名）/ `prod`（完整启动）；API 联调写法 `prod --no-build`；安全措施与启动模式无关。模式徽标：sim=蓝 / prod=灰阶加粗+✓ / 臂动扫琥珀 / 断连灰阶脉冲，红绿独占不动（规格见 #rebot-arm thread fdf6a140）。模式徽标与连接状态是两个独立维度：断连显示为徽标旁灰阶脉冲「已断连」，不覆盖模式徽标。进入 prod（或 sim→prod 切换）时弹阻断式全屏警告，需点「我已了解」确认；警告层 z-55，低于急停栏（60），Escape 仍是急停快捷键；警告 ⚠ 图标灰白中性（四色纪律：琥珀=臂在动）。插件 `retryable` 无默认值，`check_shape` 注册前拒绝未声明的插件。`device.sh`：`open` 加 `ExitOnForwardFailure`（端口被占立即失败不开死页）；`push` 后 `status` 改为轮询健康检查（30s 超时），冷启动慢不再误报 no response |

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

这四项**只挡验证，不挡代码** —— 相关实现都已写完并有测试，缺的是在真机上确认数值。

| # | 项 | 影响 | 怎么解 |
|---|---|---|---|
| B1 | CAN 接入形态未定：`config/rebotarm_rs.yaml` 写 `channel: can0`（socketcan），上游 README 又提到 USB2CAN 串口桥 `/dev/ttyACM0` | 挡真臂验证。`ArmSession` 与 `rebot-can.service` 按 socketcan 写的，若实际是串口桥要改这两处 | 在 R2x 上 `ip link show can0` + `ls /dev/ttyACM*`，跑上游 `example/2_zero_and_read.py` |
| B2 | 末端挂佳能机身后重力补偿是否还准（上游标定是**空载**，误差 5–11%） | 挡挂相机后的浮动手感实测。`FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益都做成可配就是为了这里重调 | 挂机身实测浮动手感；不准则在 URDF 末端加相机等效质量/质心，或重调阈值 |
| B3 | R2x 上 500 Hz 控制频率能否稳住（yaml 默认值，上游没说在什么算力上测的） | 只影响频率取值。sim 下实测 100 Hz 稳，真机换成上游 `start_control_loop` | 跑控制循环测实际 tick 抖动，不稳就降频并记录 |
| B4 | XIAO ESP32-S3 板子是否在手 | 挡固件烧录验证。固件与主机侧协议都写完了，协议在内存管道上有 30 个测试 | 有板子就 `cd firmware/esp32-shutter && pio run -t upload` |
| B5 | 进站限速 0.25 rad/s 是按 demo 安全同步 15°/s 取的，挂相机后未标定 | 进站过快是安全风险，过慢拖拍摄节拍；execute 与 goto 的进站段都走它 | 真机跑进站观察过冲/共振，必要时再降 `executor.py` 的 `FIRST_APPROACH_MAX_SPEED` 并回填实测值 |

---

## 决策速查

四条铁律（急停不能调 `estop()`／不许用上游默认资产解析／速度不能读 `mechVel`／不重造运动学）写在 [`AGENTS.md`](./AGENTS.md)，**只维护那一份** —— 两份副本会漂移，而漂移掉的正是这类救命细节。

源码级证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。
