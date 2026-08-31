# 贡献指引 · CONTRIBUTING

这份文档干两件别处不干的事:**把贡献流程串成一条可跑的清单**,和**给一次架构体检(它够不够好、哪里会断)**。

规则、设计、状态不在重复——改代码前读 [`AGENTS.md`](./AGENTS.md)(铁律 + 代码地图 + 约定),设计意图读 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md),现在做到哪读 [`PROGRESS.md`](./PROGRESS.md)。本文件是指针 + 判断,不是副本。

> **命名说明:** 用 GitHub 约定的 `CONTRIBUTING.md`(不是 `CONTRIBUTION.md`),因为 GitHub 在开 PR/issue 时会自动把它链接到表单上方——这是实打实的好处,值得遵循约定。

---

## 一、贡献流程

一次改动走完这几步。每步都指向该读的地方,不抄规则。

1. **开工前读三份**: [`AGENTS.md`](./AGENTS.md)(四条铁律——违反了不报错只是结果错)、[`PROGRESS.md`](./PROGRESS.md) 的 `▶ 当前` 段、看 `git log` 想改的那块为什么这么写。这个仓库里**好几个决定的理由只存在于 commit message 里**。
2. **所有 `uv` 命令在 `app/` 下执行**(布局说明见 AGENTS)。`cd app && uv sync` 装依赖;`git submodule update --init` 拉臂层 submodule(漏了 import 就失败)。
3. **改完跑三道闸**:`cd app && uv run pytest` 绿、`cd app && uvx ruff check backend tests` 绿、`cd app/frontend && npm run build` 绿(改了前端才需要)。每个 commit 结束时代码库必须能跑。
4. **同 commit 更新 [`PROGRESS.md`](./PROGRESS.md) 的状态**——不要分开提交,否则状态和代码漂移。`进行中`/`上一个完成的` 两行说清现在在哪。
5. **commit message 写正常英文散文,说清为什么**,尤其是偏离原计划的地方。
6. **碰硬件相关代码前读 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)**——「已验证」与「待实测」严格分开;四条铁律的源码级证据在那。

**哪些不测**(AGENTS「测试」段有完整说明):前端组件、上游 `reBotArm_control_py`、MotorBridge SDK、ESP32 固件、真机在环。`SimArm`/`SimShutter` 是一等公民——它们同时是无硬件开发循环的基础设施,不是测试边角料。**前后端契约是机器校验的**(golden 用例两侧逐字段比对,normalize 双语言各跑一遍),改响应形状或 normalize 规则先跑 `test_contract.py`。

---

## 二、架构速览(30 秒)

**开源 AIoT 机械臂参考方案。核心能力是「时空编程序列」:手掰示教、时间轴编排、一键回放。** 摄影是第一个参考场景,不是产品身份。

单仓库、显式分层(设计模式的完整叙述在 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md),这里只给导航):

```
平台层(交付,下游继承)
  ① 内核 Kernel        arm/ + safety/ + core/controller.py —— 物理地基,永不动
  ② 时空编排引擎        core/(executor/floatlock/broadcaster/events) + sequences/
  ③ 插件系统           actions/ + shutter/(⏸ parked:待 B4 板子)
  ④ API + 租约         api/(agent.py ⏸ parked:待外部 agent 接入)

参考场景层(示范,下游替换)
  · 摄影(旗舰)        快门插件、演示数据、场景文案 —— ⏸ 配件期落地才拼
```

**时间维度**:出厂交付内核 + 空插座,场景内容一律在实地落地那天才拼。**不是「先做摄影再扩展」,是先把内核闭环跑通,场景一个都不提前做。**

跨包依赖是**无环 DAG**,`core` 是唯一枢纽(只有 `api` 依赖它)。`executor` 纯逻辑不碰闩锁;`ActionContext` 只给只读姿态没有 arm 句柄;`api` 不直连校验器(走 `Controller.preflight_*` 那道门);`arm` 层不实现运动学(只调上游 submodule)。**这四条边界现在是 AST 机器锁**(`app/tests/test_layer_boundaries.py`),不是散文。

---

## 三、架构体检:够不够好?

> **这是一份带日期的判断,不是事实。** 事实活在 ARCHITECTURE.md / PROGRESS.md / 测试里;判断会随迭代过期。下面每条「稳」都标了守住它的测试,可独立验证。

**体检日期:2026-08-31。测试基线:474 绿。**

### 稳的地方(有机器守着,改坏会大声失败)

| 稳的地方 | 守它的测试 |
|---|---|
| **急停保持力矩、绝不掉电**——`estop()`/`disable_all()` 全仓 AST 扫描,属性访问就失败 | `test_controller.py::test_nothing_in_the_backend_ever_disables_the_motors` |
| **运动闸门**——每个会让臂动的端点必须挂 `require_arm_available`,遍历路由表 + OpenAPI 交叉校验,FastAPI 改内部结构会大声失败而非静默空转 | `test_motion_gate.py` |
| **四条层级边界**——executor 不碰闩锁 / ActionContext 无 arm 句柄 / api 不直连校验器 / arm 不实现运动学 | `test_layer_boundaries.py`(已 mutation 验证咬合) |
| **前后端契约**——REST 响应形状 + normalize 规则两侧逐字段比对 | `test_contract.py` + `test_cross_lang_constants.py`(钉 4 组双语言镜像常量) |
| **急停是横切闩锁,不是模式机的一态**——加新模式不用重审所有切换是否会绕过它 | `controller.py` 结构(`mode` 属性里 estop 永远最高优先) |
| **插件够不到臂**——`ActionContext` 字段集锁死,加 arm/latch/store 句柄测试就红 | `test_layer_boundaries.py::test_action_context_carries_no_arm_or_latch_handle` |
| **时间全可注入**——执行器/闩锁/看门狗/浮动锁定/串口客户端全接受 `clock` 参数,测试里无 `time.sleep` | `controller.py` / `floatlock.py` / `watchdog.py` / `esp32.py` |

### 是债,但已知、有 owner、不挡主线

这些是**已识别、已记录、有触发条件**的欠债,不是暗坑:

1. **五条结构性审查发现未裁决**(PROGRESS `进行中` 段挂着):急停后在途快门静默完成(executor abandon 语义)/ idle 不发令且 drift 看门狗同时关闭 / 示教浮动无软限位 / agent `command_joints` 绕路径预检 / 租约独占不约束 UI 端点。每条都需要一个裁决(修 / 接受并写理由 / 挂起写触发条件)。
2. **超前建成模块占后端约 31% 表面**(快门链 ~600 行 + 插件运行时 ~890 行 + agent 租约 ~400 行)——都是事故或场景驱动、有测试守着,不是投机抽象;但建在了硬件和场景到位前。代码地图已打 `⏸ parked` 标,未来 session 既不往里加东西也不当死代码删。
3. **`TimelineView.tsx` 783 行单实现**——刻意不拆(docstring 自述「每次编辑只有一处实现」),拆需人眼验收的 UI 重构,留待专门一轮。

### 会断的地方(真机验证才解,代码已写完)

这些**只挡验证,不挡代码**——实现都有测试,缺真机数值:

| # | 项 | 断在哪 | 怎么解 |
|---|---|---|---|
| B2 | 挂相机后重力补偿准不准(上游标定是空载,误差 5–11%) | 挡浮动手感实测;j2 过补在展开姿态会冲到 47° | 相机到货:称重 + 量质心注入 URDF,浮动漂移手感复核 |
| B5 | 进站限速 0.25 rad/s 挂相机后未标定 | 进站过快是安全风险 | 真机跑进站观察过冲/共振 |
| B6 | 到位静止判定阈值未真机标定 | 过紧则「永远不到位」、过松则糊片 | 真机跑序列,见进站 timeout 先放宽到能通过的最紧值 |

### 什么会真正打破它(四条铁律,三条有 AST 守,第四条刚补上)

| 铁律 | 后果 | 守它的测试 |
|---|---|---|
| 急停不能调 `estop()`/`disable_all()` | 48V 臂掉电=掉下来 | ✓ AST 全仓扫描 |
| 不许用上游默认资产解析 | 返回「合法但属于错误机器人」的模型,FK/重力全算另一条臂 | ✓ `assets.py` 强制传参 + `assert_rs_model()` |
| 速度不能读 `mechVel` | 该寄存器不是 rad/s,读错会锁不住或锁太早 | ✓ `SimArm` 也差分算(代码层) |
| 不重造运动学 | FK/IK/重力/轨迹必须调上游 submodule | ✓ `test_layer_boundaries.py`(刚加,arm 层禁 import pinocchio) |

**结论:架构够好、够用,且「够好」是可验证的——稳的地方有测试钉着,债的地方有 owner 和触发条件,会断的地方有真机清单。** 这个仓库最大的风险不是复杂,是被「感觉复杂」驱动的乱重构;遇到不确定,先读 `git log` 和对应的测试,不要凭文档自述改。

---

## 四、文档导航

完整分工表在 [`AGENTS.md`](./AGENTS.md) 的「文档分工」段。速查:

| 要做什么 | 读哪 |
|---|---|
| 改代码(任何) | [`AGENTS.md`](./AGENTS.md) |
| 接手一个 session | [`PROGRESS.md`](./PROGRESS.md) `▶ 当前` |
| 用这个服务 / 排故障 | [`README.md`](./README.md) / [`README.zh-CN.md`](./README.zh-CN.md) |
| 谈产品定位 / 改交互 / 加插件 | [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) / [`docs/TIMELINE.md`](./docs/TIMELINE.md) / [`docs/PLUGINS.md`](./docs/PLUGINS.md) |
| 碰硬件 | [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md) |
| 写示教录制/轨迹回放/限速 | [`docs/rebot-policy.md`](./docs/rebot-policy.md) |
| 碰快门链路 | [`app/firmware/esp32-shutter/README.md`](./app/firmware/esp32-shutter/README.md) |

**每件事只写一处。** 硬件数值在 HARDWARE_NOTES、进度在 PROGRESS、用法在 README、规则在 AGENTS。往这里抄一份副本,副本就会先时——而过时得最要命的正是「为什么不能调那个看起来正确的方法」。
