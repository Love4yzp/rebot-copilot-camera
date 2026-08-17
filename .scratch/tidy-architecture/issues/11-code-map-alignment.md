# 11 代码地图对齐与 README 分区速查表

Type: task
Status: resolved
Blocked by: 01, 10

## Question

收尾对齐：AGENTS.md 代码地图补全 + README 两版顶部分区速查表。

**清单**：

1. AGENTS.md 代码地图补全（审计漂移清单）：`backend/sequences/seed_demo.py`、`examples/rebot-plugin-turntable/`、`data/` 四个数据目录、`.agents/` + `skills-lock.json`；删除已消失的 `scripts/` 等条目（05/06 之后）。内核标签沿用 10。
2. README.md 与 README.zh-CN.md 同步：顶部加分区速查表（程序 / 配置与数据 / 部署 / 知识 / 验证 / 入口 六行，每行列出目录与一句话）。
3. CLAUDE.md 保持指针不动。
4. PROGRESS.md 同 commit 更新。

**验收**：grep AGENTS.md 提到的每个路径都存在；README 两版分区表一致；`uv run pytest` 全绿。

## Answer

（2026-08-17，commit 3643b56）收尾完成。

- AGENTS.md 代码地图补：`sequences/seed_demo.py`、`api/preflight.py`、根目录 `examples/rebot-plugin-turntable/`（含 pyproject 钉路径提示）、`data/`（含 REBOT_DATA_DIR 说明）、`.agents/` + `skills-lock.json`、tests 自成一章不重复；05/06 之后消失的条目（scripts/、migrate.py）已清。
- README 两版顶部加「Where everything lives / 什么东西在哪」六行分区表（程序 / 配置与数据 / 部署 / 知识 / 验证 / 入口），并把陈旧的「386 个测试」更新为 464。
- CLAUDE.md 保持指针。
- 验证：pytest 464 绿；两版 README 分区表一致。
