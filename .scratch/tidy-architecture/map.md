# 整理仓库架构 · Wayfinder 地图

## Destination

「理一下」真正做完：五档清理全部落地（根目录 41 → 约 16 个可见条目）、三张文档图（内核边界 / 接口三件套 / 事件与归属）写进 `docs/ARCHITECTURE.md`、AGENTS.md 代码地图与 README 分区速查表对齐。每档独立 commit，`uv run pytest` 全绿，`./dev.sh prod --sim` 能起，PROGRESS.md 同步更新。

## Notes

- **领域**：48V 机械臂示教回放仓库。开工前必读 AGENTS.md（四条铁律、层级边界、运动闸门测试、契约测试）；碰硬件相关读 `docs/HARDWARE_NOTES.md`；产品架构锚点是 `docs/ARCHITECTURE.md`。
- **每会话技能**：决策票用 grilling + domain-modeling；执行票动模块边界时用 codebase-design。
- **事实已探明**（两次只读审计：顶层布局与路径依赖表、后端/前端耦合图）—— 结论已嵌入各票正文，无需重跑审计。
- **文风**：中文交流；禁用词 link:{delve, landscape, tapestry, robust, seam, seamless, cutting-edge, transformative, pioneering, leverage, in today's world, it's important to note, ultimately, moreover, furthermore}；「seam」一律说「接口」。
- **执行纪律**：每档一个 commit；每 commit 后 `uv run pytest` 绿 + `./dev.sh prod --sim` 能起；PROGRESS.md 与代码同 commit；README 两版同步改；commit message 英文散文（仓库约定）。
- **仓库现状**：git 工作树干净；vendor submodule 锁 d540405；设备未上线部署，无在线迁移包袱；`/api/logs` 是活功能（LogDrawer 在调），别删。

## Decisions so far

- [01 五档清理方案总批](issues/01-five-tier-cleanup-plan.md) — 全部照单：五档 + R0-R9 按推荐接受；05–09、11 解锁。
- [02 DSH 借/不借矩阵](issues/02-dsh-borrow-matrix.md) — 7 借 5 不借照单；三张图写进 ARCHITECTURE.md 一个锚点；10 解锁，04 经 10。
- [03 臂接口内核行与安全姿势](issues/03-arm-kernel-row-safety.md) — Q-F=a（接口表内核行）；Q-G=a（外置闩锁、单点耦合）。
- [05 档 0 纯删除](issues/05-tier0-deletions.md) — 完成，commit 135e13e；两处历史记录保留提及（票内 Answer 有偏差记录）。

## Not yet specified

- 档 3 的落刀级清单（08 认领时细化；若 04 通过则按新目录重写）。
- 档 4 的拆分方案（09 认领时在票内评论区贴方案要点）。
- 契约 `seed:true` 用例的字段级设计（08 内的子任务）。

## Out of scope

- DSH「无特权内核 / 全插件化」哲学 —— 与 48V 臂安全模型冲突（02 票判定）。
- `apps/` + `packages/` + `python/` + `native/` 顶层骨架 —— 多包 monorepo 产物，本仓库不适用（02 票判定）。
- OS 线程优先级 —— 驱动无线程，控制循环独占，provider 不上控制循环（03 票 Q-Fc 判定）。
- `docs/subsystems/` 每模块一文档 —— 60 包 monorepo 的仪式，本仓库三张表进一个锚点（02 票判定）。
- 新功能开发（触发源、新插件实例、工业场景、VLM）。
- 已部署设备的在线迁移与回滚 —— 设备未上线，无对象。
- 前端组件测试体系 —— 仓库约定不测前端组件。
