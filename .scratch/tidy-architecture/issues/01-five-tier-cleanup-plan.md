# 01 五档清理方案总批

Type: grilling
Status: resolved

## Question

批准或逐条修改下述「五档清理 + 确认单」；这是所有执行票（05–09、11）的前置。

**背景**（已探明事实）：仓库根目录 41 条目；AGENTS.md 代码地图与实际布局零漂移；真正「乱」的是地图未覆盖的边缘地带（运行时数据目录、scripts/、examples/、docs/ 下 4 个 HTML 原型、pnpm 双锁）与 api 层越过分层边界。设备未上线部署，无历史包袱 —— v1 数据与迁移代码可以干净退役。

**五档**：

- **档 0 纯删除**：`frontend/pnpm-lock.yaml` + `pnpm-workspace.yaml`（全仓用 npm）；`scripts/monitor_arm.py`（死代码，无人引用）+ `scripts/verify_gravity.py`（退出调 `disable_all()`，违反铁律 #1）+ 目录本身；`docs/` 下 4 个 HTML 原型（git 历史可捞，TIMELINE.md 是定稿）；空目录 `templates/`、`.kimi/`。
- **档 1 v1 退役**：`routines/` + `backend/sequences/migrate.py` + 相关测试 + `app.py` 的 `maybe_migrate` 接线 + `ROUTINES_DIR`/`REBOT_ROUTINES_DIR` 默认值与 deploy unit、device.sh 相关行。若仍有 v1 routines API 端点残留，一并删除并同步 mock 与契约用例。
- **档 2 数据归位**：`poses/ sequences/ templates/` → `data/{poses,sequences,templates}/`，消灭根目录 `sequences/`（数据）与 `backend/sequences/`（代码）撞名；环境变量简化为单一 `REBOT_DATA_DIR`（未上线，不做旧名兼容）。
- **档 3 架构手术**：api 层越界回正（验证收进 controller 背后、杀掉 `_progress_payload` 私偷、agent 与 sequences 播放管线合一、单一搬臂路径）；assets↔tuning 潜在环单向化；arm/session 的 `ARM_JOINTS` 越界；`__init__` 重导出不再级联拉入 pinocchio；无守卫镜像收编（契约加 `seed:true` 用例 + 常量单一来源）。
- **档 4 前端拆分**（后置）：`App.tsx`(899)/`TimelineView.tsx`(783) 沿既有目录缝拆分；无组件测试，需 `npm run dev` 人眼验收。

**预填确认单**（每行预填了推荐答案，回「按你的来」= 全盘接受）：

- R0 四条铁律 + 运动闸门测试 + 契约测试 + pytest 全绿 + `./dev.sh prod --sim` 能起 —— **不清**（开发期底线，不是历史遗留）
- R1 五档递进，每档独立 commit 全绿 —— **是**
- R2 档 4 在 0–3 落地后单独做 —— **是**
- R3 `.agents/` threejs-* 参考技能 + `skills-lock.json` —— **保留**（ArmView3D 活着的配套，git 跟踪、AGENTS.md 明文保留）
- R4 docs/ 4 个 HTML 原型 —— **删**（git 历史可捞）
- R5 数据目录名 `data/` —— **是**
- R6 无守卫镜像收编方式（契约 `seed:true` 用例 + 常量单一来源）—— **是**，归入档 3
- R8 `contract/` 分裂（根 cases + frontend runner）—— **保持**
- R9 README 两版顶部加分区速查表 —— **加**

**裁决要求**：逐档批「照单 / 修改（写明怎么改）」。此票解决后，05–09、11 解除阻塞。
