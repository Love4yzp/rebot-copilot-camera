# 06 档 1 v1 退役

Type: task
Status: open
Blocked by: 01

## Question

执行档 1：v1 routines 体系干净退役。

**清单**：

1. **先查后删**：grep 全仓 routines / migrate 相关代码 —— 确认 api 层是否仍挂载 v1 routines 端点、mock 与 contract/cases 是否覆盖它们（已知 tests/ 里有 `test_routine_store.py`、`test_routines_api.py`）。若端点仍在，一并删除 api 路由 + mock 实现 + 契约用例。
2. 删除 `backend/sequences/migrate.py` 与 `tests/test_migrate.py`（及第 1 步确认的 routines 测试）。
3. 拆 `backend/app.py` 的 `maybe_migrate` 接线（app.py:41、361 附近）。
4. 删 `backend/config.py` 的 `ROUTINES_DIR` / `REBOT_ROUTINES_DIR` 默认值与 `deploy/rebot-copilot-camera.service`、`device.sh` 中的相关行。
5. `git rm -r routines/`（含 2 个 v1 JSON；未上线，无数据要迁）。
6. PROGRESS.md 同 commit 更新。

**验收**：`uv run pytest` 全绿；`./dev.sh prod --sim` 能起；grep 无 routines/migrate 残留；启动日志无迁移输出。
