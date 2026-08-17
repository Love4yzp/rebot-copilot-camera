# 06 档 1 v1 退役

Type: task
Status: resolved
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

## Answer

（2026-08-17，commit 64946c1）档 1 完成。

- 删除：`routines/`（未跟踪数据目录，本地 rm）+ `backend/sequences/migrate.py` + `tests/test_migrate.py` + `app.py` 的 `maybe_migrate` 导入与调用块 + `config.py` 的 `ROUTINES_DIR` + `deploy/rebot-copilot-camera.service` 的 `REBOT_ROUTINES_DIR` 行 + `device.sh` 的 `/routines/` 排除/保护两行 + `.gitignore` 的 `/routines/` 行 + README 两版环境变量行与 push 保护段 + AGENTS.md 代码地图 migrate.py 行。
- 先查后删结论：无残留 v1 API 端点（audit 提到的 routines 测试只剩 pycache 幽灵，源码早已删过）。
- 验证：pytest 460 绿（467−7 = 删掉的迁移测试数，账平）；`--sim` 启动无迁移日志、SIGTERM 回零正常；grep 无 maybe_migrate / ROUTINES_DIR / REBOT_ROUTINES / migrate_routine 残留。
- 遗留记录：`ActionContext.routine_id/routine_name`（executor.py:314-315、check.py:58-59 填入的是 sequence id/name）是 v1 词残留 —— 改名属插件 API 变更，不在本票范围，已记入 PROGRESS，留档 3 再议。
