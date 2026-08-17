# 08 档 3 架构手术

Type: task
Status: resolved
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

## Answer

（2026-08-17，commit b41908c）档 3 完成，五项全做。

1. api 越界回正：`Controller.preflight_*` 四方法是唯一校验门；`backend/api/preflight.py`（新）承载位姿解析 + 整序列预检，`/api/sequences/execute` 与 `/api/agent/control/play` 共用（agent 侧的预检从此**也含当前位姿到首站的路径**—— 与 sequences 端点一致，此前它漏了这一条）；`api/control.py` 对 `_progress_payload` 的私偷改为公开 `progress_payload`；`lease._idle_ttl` 改为公开属性 `idle_timeout_s`。
2. 单一搬臂路径：`Controller.move_joints` 是 goto 与 agent 关节指令共用的临时序列路径（到位检测/进站限速/急停中止全生效）；`goto` 变成它的一个壳。
3. 环与越界：assets 不再运行时 import tuning（按 `profile.value` 字符串消费）；`arm/session` 不再 import safety.kinematics —— 六臂关节名唯一家在 `assets.arm_joint_names()`，`kinematics.ARM_JOINTS` 也从它派生。
4. import 级联：`sequences/__init__` 不再重导出 seed_demo —— `import core.controller` 不再拉入 pinocchio。
5. 无守卫镜像收编（R6）：契约新增 `10-seeded-library`（seed:true 两侧同跑首启演示数据）——**当场抓出 mock 与后端种子已漂移**（id 不同、mock 多一条空序列、多无 provider 的装饰标记），已把 `mock/state.ts` 种子对齐到 `seed_demo.py` 并被用例钉死；新增 `tests/test_cross_lang_constants.py` 守卫 4 组双语言常量（进站时长/限速、WAIT_KIND、mock JOINTS↔硬件 yaml）。

AGENTS.md 层级边界写明 preflight 门 + estop/gate/plugins 三个刻意前门。验证：pytest 464 绿、ruff 全过、--sim 起停正常。
