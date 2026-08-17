# 05 档 0 纯删除

Type: task
Status: resolved
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

## Answer

（2026-08-17，commit 135e13e）档 0 完成。

- 删除：pnpm 双锁、`scripts/`（monitor_arm.py + verify_gravity.py）、docs/ 4 个 HTML 原型、空目录 `templates/` 与 `.kimi/`。
- 文档改写：README ×2、PROGRESS B2、HARDWARE_NOTES 三处 —— verify_gravity.py 的复核流程全部改成浮动漂移手感（lift-then-float，#B2 已有步骤）。
- 验证：pytest 467 绿；npm run build 绿；后端 `--sim` 在 18791 端口起停正常、SIGTERM 走回零收尾；本机 ruff 全过。
- 偏差记录：① PROGRESS.md:60（B1 已解历史条目）与 HARDWARE_NOTES:164（2026-08-14 实测叙事）保留对 verify_gravity.py 的**历史性提及**（历史记录不改写，只清了可执行指引）；② uvx 拉到的 ruff 版本报 67 个预存在错误（版本规则差，非本档引入），档 3 处理代码级 lint。另：首次 commit 误收操作者的 `docs/i18n-zh-en-wordlist.md` 草稿，已 amend 移出、保留为未跟踪文件。
