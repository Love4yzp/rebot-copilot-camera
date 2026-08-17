# 10 三张文档图落地

Type: task
Status: resolved
Blocked by: 02, 03

## Question

把 02、03 的裁决写进 `docs/ARCHITECTURE.md`（架构锚点，唯一家）与 AGENTS.md 代码地图。

**清单**：

1. **内核边界表**：arm/ + safety/ + core/controller.py = 内核（动作集 = hold 即急停路径 / move_to / settle / relax / set_float / follow；前置件 = SafetyLatch + 运动闸门）；executor/floatlock/broadcaster/events + sequences/ = 编排引擎；actions/ + shutter/ = 插件层；api/ = 入口层。
2. **接口三件套表**：ArmDriver（内核行，按 03 裁决）、ShutterDriver、ActionProvider、ActionContext 各填「动作集 / 实现 / 使用者」。
3. **事件生产/消费映射**：events.py → broadcaster → 消费方全表。
4. **「新行为归属位置」表**：新增运动端点 → `require_arm_available` + 闸门测试；新插件点 → docs/PLUGINS.md；新硬件事实 → docs/HARDWARE_NOTES.md；新调参 → backend/tuning.py + apply_tuning 分级。
5. AGENTS.md 代码地图补「内核」标签，与 ARCHITECTURE.md 一词不差。
6. 每件事只写一处：三张表只进 ARCHITECTURE.md，AGENTS.md 只链接。

**验收**：两文档措辞一致；`uv run pytest` 全绿（文档改动不应影响代码）；PROGRESS.md 同 commit。

## Answer

（2026-08-17，commit c796532）三张表 + 内核边界表已写进 `docs/ARCHITECTURE.md` 新章节「内核边界与部件关系」（唯一家）；AGENTS.md 代码地图顶部加分层速览一行、只链不抄。事件表以 `events.py` 与各 emit 调用点核实（executor 发序列/动作/到位，controller 发急停，capture 端点发 teach.captured）。接口表核实了 ShutterDriver 的方法集（is_connected/ping/focus/shoot/pair/pair_smart/camera_connected/camera_status）与 ActionProvider 的 fields/probe/run。
