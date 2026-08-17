# 概念对账表

**这是一次性快照：把三份概念来源（`ARCHITECTURE.md` / `TIMELINE.md` / `AGENTS.md`）逐条对到代码，标注三态。只列不修 —— 漂移的定夺权在产品所有者。**

三态定义：

- **一致** —— 文档说的和代码做的是一回事
- **漂移** —— 文档与代码说的不是一回事（含文档间互相矛盾、禁词残留）
- **文档超前** —— 文档写了、代码还没有（文档自己如实标注的不算问题，记录在案即可）

对完一条销一条：拍板后改文档或改代码，改完从本表划掉。本表不长期维护，销完即删。

---

## 已销账（首轮漂移 8 条，已全部处置）

| # | 漂移 | 处置 |
|---|---|---|
| D1 | `settle` 误列内核动词集，实为 executor 到位判据 | 采纳修法 b：`ARCHITECTURE.md` 内核行保留 settle 概念但注明「不是臂动词，是编排引擎层的驻留判据」 |
| D2 | ArmDriver 使用者「唯一：控制循环」过强 | 采纳修法 a：接口三件套使用者列补注「API 层仅只读状态，不下运动指令」 |
| D3 | 禁词「播放」残留 5 处 | 全改述：`AGENTS.md` ×2（执行前校验 / 执行预检）、`TIMELINE.md` v2 行（执行语义对接）、`Inspector.tsx` 与 `TrackBlock.tsx` 两处 UI 文案 |
| D4 | 铁律 4 例外未写明 + `base.py:93` docstring 撒谎 | 铁律 4 补「唯一例外」段（MIT 斜坡，指向 HARDWARE_NOTES #12）；`base.py` docstring 改为描述真实的 smoothstep MIT 斜坡 |
| D5 | 事件标记 `at` 范围无服务端校验 | 采纳修法 a：`api/sequences.py` PATCH 时按块类型校验（hold ≤ duration_s，transition ≤ 1），400 `marker_out_of_range`；mock 侧同检查保持逐字段对齐；3 条新测试钉住 |
| D6 | 进站公式文档/前端与后端差 1.5× | 采纳修法 a：前端 `usePreview.ts` 乘 `EASE_PEAK`（新增 `model.ts` 镜像常量，由 `test_cross_lang_constants.py` 钉住）；`TIMELINE.md` 进站公式同步注明 |
| D7 | 事件消费方写成 `/ws` | `ARCHITECTURE.md` 事件表消费方改 `/api/events` |
| D8 | 「executor 不出现 latch 字样」严格为假 | `AGENTS.md` 措辞改「不 import、不引用闩锁（docstring 解释性提及除外）」 |

---

## 文档超前（如实标注，记录在案）

### A1. 操作 ops 扩展点：设计已定、实现排队

- `ARCHITECTURE.md:184` 自标「实现排队」，代码确认无 ops 框架：快门配对/自检是 `api/control.py:206-319` 的裸路由直调 ShutterDriver。文档没有谎称已实现，不算漂移。未来落地时把三个端点迁入 ops 层。

### A2. 位姿「覆盖保存」的确认机制没有承载体

- `TIMELINE.md` 规则 3 要求「覆盖保存或删除被 N 条序列链接的位姿时拦一句」。**删除**已实现（后端 `GET /api/poses/{id}/links` `poses.py:158-175` + 前端确认 `LibraryPanel.tsx:270-288`）；**覆盖**无拦截（`poses.py:124-145` 直接改写 joints）——但前端目前也没有关节覆盖入口（`LibraryPanel.tsx:75` 只 patch 名称），即该风险路径**当前不可达**。列为未来义务：哪天开出关节编辑入口，必须复用同款 links 确认，最好后端 PATCH joints 时也拦。

---

## 一致项（抽查通过，证据在案）

| 概念 | 关键证据 |
|---|---|
| 四积木（位姿/序列/动作/模板） | `sequences/models.py:52-78,81-97,100-123,175-185`；链接复用 `:105-106` |
| 过渡块自动生成/不可删/同位姿不生成 | `normalize.py:25-37,56-62`；前端镜像 `model.ts:80-91`；写侧强制 `api/sequences.py:96-98` |
| 执行中时间轴锁定（规则 5） | `api/sequences.py:92-95,118-121`（PATCH/DELETE 均 409） |
| 进站 approaching 标志 | `executor.py:139-150,263-267`；测试 `test_executor.py:605-691`；前端消费 `TransportBar.tsx:65` |
| 「去起点」0.3 rad 分级 | 前端 `App.tsx:39,148`、`TransportBar.tsx:86-90`；goto 同吃首段限速 `executor.py:457` |
| 模板：存为/实例化/复印脱钩 | `api/templates.py:86-115,126-160,62-68`；测试 `test_templates_api.py:126`；契约 `cases/04-templates.json` |
| api 层不直连 safety/actions 校验器 | `app/backend/api/` 全量 grep 零违规；运动前校验全走 `Controller.preflight_*`（`poses.py:50,115,193`、`sequences.py:147`、`agent.py:20`） |
| 前后端契约机器校验 | `tests/test_contract.py`；`contract/cases/` 21 个用例；normalize 双端同吃 n0x 用例 |
| 集合不是独立积木 | `sequences/store.py` 仅 PoseStore/SequenceStore/TemplateStore（`:131,139,151`） |
| 首启演示数据与 seeded-library 契约 | `seed_demo.py:61-167`；`contract/cases/10-seeded-library.json`；`test_seed_demo.py` |
| 铁律 1（禁 estop/disable_all） | AST 测试 `tests/test_controller.py:299-327`；急停 = hold `controller.py:590-607`、`latch.py:8-13` |
| 铁律 2（assets.py 唯一出口） | `session.py:338`、`kinematics.py:83-84`、`app.py:320`；backend 内无裸调用 |
| 铁律 3（速度差分） | 真臂 `session.py:147-151`；模拟臂 `sim.py:231-233`；0x701A 仅存于注释 |
| executor 纯逻辑（时钟注入/无 FastAPI） | `executor.py:40-63` imports；全文件 `self._clock()` |
| 「0.0 是假值」 | 示范在 `agent.py:120-124`（含解释注释）；floatlock/executor 全部 `is None` 判空，无反例 |
| 退出先回零 + 端口预检 | `ParkOnExitServer` `app.py:240-260,369`；`_park_arm` 先于停循环 `app.py:82-109,128-137`；`_ensure_port_free` `app.py:61-79` |
| agent 租约（token + 双重 TTL + 独占） | `agent.py:36-38,71-101`；端点 `api/agent.py:67-225`（运动端点双闸） |
| ShutterDriver 抛异常 + 永不回落 | `shutter/base.py:7-11,44-54,69-71`；`factory.py:3-16,71-83` |
| ActionProvider 三件套 + 双发现路径 + ctx 无 arm | `actions/base.py:138-152,102-120`；`registry.py:53,170,190-254` |
| 动作不跑控制循环 | 每 provider 一 worker `runner.py:17-20,319-331`；executor 投递+轮询 `executor.py:765-767,643-647` |
| 事件三铁律 + 生产方逐行对 | `events.py:9-24`；`broadcaster.py:25,88-98`；sequence.*/pose.arrived/action.* 全由 executor 发；estop.* 控制循环发（`controller.py:569-580`）；teach.captured 经 `emit_event`（`poses.py:120`） |
| 触发源 = 进程外 HTTP（`TriggerRequest.source`） | `api/control.py:46-55`；execute/goto 端点 |
| 事件订阅 = `/api/events` WS | `api/control.py:154-168` |
| 调参闸门分级 | `controller.py:157-198`（执行中拒一切/浮动中拒负载与重力/float kp·kd 放行）；`test_motion_gate.py:71-73` 写明理由 |
| 负载三态 + 双开关正交 | `tuning.py:50-66`；`assets.py:113-120,129-156,159-232` |
| 运动闸门 + 两层守卫测试 | `api/gate.py:29-47`；递归+OpenAPI 交叉校验 `test_motion_gate.py:77-85,88-111,122-144` |
| 急停解除进零重力示教 | `api/estop.py:80-99`；`controller.py:466-485` |
| 调参热改内存 / 显式保存落盘 | `api/config.py:89-101,104-113`；`tuning.py:199-239` |
| 预演/执行双动词（前端） | `TransportBar.tsx:34-43,61-88`；预演纯前端 `usePreview.ts:37-48`；「臂未动」`MonitorPanel.tsx:143`；灰阶 `styles.css:2244-2247` |
| 预演从当前位姿起播 + 前摇文案 | `App.tsx:88-91`；`usePreview.ts:107-125`；`TransportBar.tsx:66` |
| 监视器单视图翻转 | `MonitorPanel.tsx:43-51,179-190`；`ArmView3D.tsx:56-58` |
| 编排轨道双密度同模型 | `TimelineView.tsx:24-25,608-747`；同一 PATCH 通道 `App.tsx:590-602` |
| 颜色四通道纪律 | token 定义 `styles.css:72-76`；唯一例外 sim 徽章蓝 `#5b9aff` 有注释声明是刻意的模式色（`styles.css:487-494`） |
| 界面不许猜臂在哪 | done 上升沿认领 `App.tsx:161-171`；block_index 夹紧 `model.ts:305-307`、`MonitorPanel.tsx:130` |
| 急停栈顶 z-index 60 + Esc 原生监听 | `styles.css:325`（遮罩族均 <60）；`Dialog.tsx:54-77`、`EstopBar.tsx:55-64` |
| 测试无 `time.sleep` | `app/tests/` 全目录 grep 零命中 |
| 示教入口文案（「+ 录位姿」/示教条） | `LibraryPanel.tsx:259-266`；`TeachBar.tsx:79`；`MonitorPanel.tsx:121-123` |
| 旧卡片板不并行 | AnchorBoard/AnchorCard 已删除（commit `3840c4e`），前端零残留 |

---

## 附带观察（不算漂移，记录在案）

- `test_sequences_api.py` 缺「执行中 PATCH/DELETE 返回 409」的专门用例 —— 实现存在（`api/sequences.py:92-95,118-121`），测试覆盖偏薄。
- `sequences/seed_demo.py:35` 直接 import `safety.kinematics.validate_sequence` —— 在「api 层不直连」约定范围之外，属正常使用，仅记录。
