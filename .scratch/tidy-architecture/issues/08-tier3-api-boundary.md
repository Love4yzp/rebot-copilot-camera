# 08 档 3 架构手术

Type: task
Status: open
Blocked by: 01

## Question

执行档 3 的五项架构手术。行为零变化：pytest 全绿 + 契约测试 + 运动闸门测试 + `./dev.sh prod --sim` 能起。

**清单**（落刀细节在此票被认领后细化，用 grep 全量定位）：

1. **api 层越界回正**：api/{poses,sequences,agent,estop,gate} 直接调用 `safety.kinematics.validate_*` / `actions.validate_*` / `SafetyLatch` 的地方，收编到 controller 背后的统一入口；`api/control.py` 对 core 的私有 `_progress_payload` 改为公开导出或经 controller 提供。
2. **播放管线合一**：`api/agent.py` 复制 `api/sequences.py` 的位姿解析 + `validate_sequence` + `validate_providers` 的部分收敛为一处；`command_joints` 的 `controller.arm.move_to` 改走与 `poses.py` 相同的 `controller.goto` —— 单一搬臂路径。
3. assets↔tuning 潜在环单向化（去掉相互延迟导入）；`arm/session.py` 对 `safety.kinematics.ARM_JOINTS` 的越界导入消除。
4. `__init__` 重导出级联：`import core.controller` 不再经 `actions/__init__` → `sequences/__init__` → `seed_demo` → `safety.kinematics` 拉入 pinocchio。
5. **无守卫镜像收编（R6）**：契约测试加 `seed:true` 用例钉住 `mock/state.ts` 与 `seed_demo.py` 的两份 seed 数据；`DEFAULT_APPROACH_S` / `FIRST_APPROACH_MAX_SPEED`（model.ts ↔ executor.py）、`WAIT_KIND`、`JOINTS` 等镜像常量改为单一来源或加守卫测试。

**验收**：`uv run pytest` 全绿（含契约、运动闸门）；`./dev.sh prod --sim` 能起；`ruff check backend tests` 干净；PROGRESS.md 同 commit。若 04 已通过，本票边界按新目录重写。
