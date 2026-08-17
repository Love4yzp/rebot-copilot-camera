# 10 三张文档图落地

Type: task
Status: open
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
