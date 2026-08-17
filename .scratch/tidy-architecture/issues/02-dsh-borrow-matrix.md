# 02 DeepSeek Harness 借/不借矩阵与文档图落点

Type: grilling
Status: resolved

## Question

决定从 DeepSeek Harness（DSH）架构借什么、文档图写在哪。

**背景**（已探明事实）：DSH 是 Cordis 插件树架构，其文档明确「不存在特权内核：一切皆插件、注册可撤销」；其可借鉴的是呈现机制：能力图（capability-seams）、事件生产/消费映射（event-producer-consumer）、「新行为归属位置」表、AGENTS.md 的布局说明风格。本仓库 `docs/ARCHITECTURE.md` 已定义特权内核（move_to / hold / settle / 急停 / 安全闸门 —— 物理地基，永不动），但 AGENTS.md 代码地图从未使用「内核」一词，且目录边界（arm/ safety/ core/ actions/ sequences/ shutter/）与文档分层（内核 / 编排引擎 / 插件 / API）不对齐。

**拟借（7 件）**：

1. **内核显形**：ARCHITECTURE.md 画「内核边界表」（arm/ + safety/ + core/controller.py = 内核；executor/floatlock/broadcaster/events = 编排引擎）；AGENTS.md 代码地图补「内核」标签。先画后动，物理重排另开 04 票。
2. **接口三件套表**（DSH 的 capability-seams）：ArmDriver / ShutterDriver / ActionProvider / ActionContext 四个 Protocol 各填「动作集 / 实现 / 使用者」。
3. **事件生产/消费映射**：events.py 语义事件 → broadcaster → 消费方。
4. **「新行为归属位置」表**：新增运动端点→挂运动闸门；新插件点→扩展点；新硬件事实→HARDWARE_NOTES；新调参→apply_tuning。
5. 一条目一职责的布局说明（已有，补内核标签与 data/）。
6. 「每件事只写一处」（已有，保持）。
7. 文档锚点与代码地图 100% 对应（已有，保持）。

**不借（5 件）**：

1. 无特权内核 / 全插件化 —— **安全红线**（闩锁在任何东西命令臂之前、插件够不到臂是物理安全）。
2. 事件域 waterfall/serial 与可撤销注册 —— 本仓库事件单向不可否决，是急停地基。
3. `docs/subsystems/` 每模块一文档 —— DSH 60 个 package 才拆；本仓库 8.8k 行后端，三张表写进 ARCHITECTURE.md 一个锚点。
4. `apps/` + `packages/` + `python/` + `native/` 顶层骨架 —— pnpm monorepo 产物；本仓库 1 后端 + 1 前端 + 1 固件，照搬是仪式。顶层维持约 16 条目形态（程序 / 配置与数据 / 部署 / 知识 / 验证 / 入口）。
5. profile/patch 组装与 Cordis 插件树 —— 不适用。

**裁决要求**：批「照单 / 修改（写明改哪件）」。此票解决后，10（写文档图）解除阻塞；04（物理重排投票）经 10 再解除；11 经 01+10 解除。
