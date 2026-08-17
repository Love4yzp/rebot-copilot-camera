# 05 档 0 纯删除

Type: task
Status: claimed
Blocked by: 01

## Question

执行档 0 纯删除，一个 commit。

**清单**：

1. `git rm frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml`（全仓 CI/dev.sh 用 npm，pnpm 是残留）。
2. `git rm scripts/monitor_arm.py scripts/verify_gravity.py`，`scripts/` 目录随之消失。
3. `git rm docs/{3d-ghost-prototype,editor-layout-skeleton,presentation-model-v2,timeline-model-comparison}.html`。
4. 删除本地空目录 `templates/`、`.kimi/`（未跟踪，不进 commit）。
5. 清引用：README.md、README.zh-CN.md、PROGRESS.md、docs/HARDWARE_NOTES.md 中提到 monitor_arm / verify_gravity 的段落删除或改写（HARDWARE_NOTES 里关于 verify_gravity 调 `disable_all()` 的警示随文件一起消失）。
6. PROGRESS.md 状态与本次删除同 commit 更新。

**验收**：`uv run pytest` 全绿；`./dev.sh prod --sim` 能起；grep 全仓无 monitor_arm / verify_gravity / pnpm-workspace 残留引用；git status 干净。

**注意**：`/api/logs` 是活功能（LogDrawer.tsx:39 在调），不是死代码，别删。
