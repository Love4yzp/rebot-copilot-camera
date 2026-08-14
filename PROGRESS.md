# 做到哪了

**项目状态机。接手一个 session 先读这里。**

本文件只回答三个问题：**现在在哪 / 下一步做什么 / 什么被卡住了**。
铁律与代码约定在 [`AGENTS.md`](./AGENTS.md)，当前设计模式在 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)。

**历史不在这。** 每次改动为什么这么做，写在 git commit message 里（`git log`），比任何文档都详细；原始计划在 [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1)，已归档，只为对照初衷。

---

## 交接协议

接手一个 session：

1. 读 [`AGENTS.md`](./AGENTS.md) —— 四条铁律，违反了不会报错只会让结果错。
2. 读本文件 `▶ 当前` 段 —— 现在在哪、下一步做什么。
3. 看 `🚧 阻塞` 段 —— 下一步是否被未解决的项挡住。
4. 想知道某处为什么这么写：`git log`。
5. 做完 → **在同一个 git commit 里把本文件的状态一起改掉**，不要分开提交，否则状态会和代码漂移。

**规则**：
- 一次只有一个进行中的项。中断时把「做到哪了」写进备注。
- 每个 commit 结束时代码库必须能跑（`uv run pytest` 绿、`uv run -m backend.app --sim` 能起）。
- 需要硬件的工作：代码与测试在开发机写完，实测结果记进 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。不要因为跑不了就跳过写测试；实机验证推翻了实现就开新 commit 修，不回改历史。

---

## ▶ 当前

| 字段 | 值 |
|---|---|
| **Phase** | **参考方案叙事已立档 + 真臂核心先行**：架构锚点重写见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)（平台层 + 参考场景层，新增「出厂 vs 场景落地」排序）。当前主线：**用前端把「手掰示教 → 时间轴回放」闭环跑通**（bare / gripper，近零位安全范围）——不等相机；**配件/插件全停**（场景实地落地才拼）；j2 过补只做最小复现确认，排同日最后。时间轴编辑器三期路线仍见 [`docs/TIMELINE.md`](./docs/TIMELINE.md) |
| **进行中** | ① **真臂核心跑通（bare / gripper，不等相机）**：演示载体是真臂——3D 数字孪生是标配不是卖点。机上现状：**夹爪已装回并接线（官方一整套）**——yaml `gripper: true`、7 关节、profile 锁 `gripper`；**开合操作不做**（操作者明确：不必要，先跑通全链路）。**B1 已解**：传输 = XCAN-USB 适配器 + MacCAN libPCBUSB（darwin 后端把 `can0` 映射 PCAN_USBBUS1），yaml 的 `channel: can0` 在这台 Mac 上正确、零代码改动；剩物理层验证（臂上电、总线通）。真臂全链路已跑通：连接✓ 零位✓ goto/运动✓ 到位保持✓ 急停冻结✓（三个真臂-only bug 已修：按组分片、模式锁定→MIT 斜坡、verify 止点假阳性）。**浮动手感待标定**：j2 重力前馈在展开姿态过度补偿、松手会自己往上冲到 47°（HARDWARE_NOTES 已记，量级远超标定误差）——需加逐关节 k/c 重力修正，标定前勿在展开姿态手掰示教。路径：**先用前端把「示教 → 编排 → 回放」闭环跑通（近零位安全范围）**；j2 过补只做 10 分钟最小复现确认（避免 verify 假阳性重演），确认后仿上游 `--k/--c` 加一次性标定值进 tuning（不重造动力学），排同日最后。相机与 ESP32 到位后摄影场景（快门、payload camera 标定）自然接上，不阻塞主线。② **结构性审查剩余发现**待逐项处置：急停后在途快门静默完成（executor abandon 语义）、idle 模式不发令且 drift 看门狗同时关闭、示教浮动无软限位、agent `command_joints` 绕过路径预检、租约独占不约束 UI 端点。③ **平台补件全停**（操作者明确：出厂应用抛弃插件，场景实地落地才拼）——配件档案化、配件区 ops 扩展点、夹爪开合、快门/转台一律挂起，一个都不提前做；演示路径已落地（首次启动种入与 mock 同款的四方位 demo：4 位姿 + 序列 + 模板；原 mock 姿态被后端校验拒绝——joint3 越限 + 自碰撞，已换成限位内无碰撞姿态并回改 mock；`REBOT_SEED_DEMO=0` 可关） |
| **上一个完成的** | **休息态收尾：退出时不再回零**：臂已在休息态（搁在止点上、力矩已卸）时，park_home 直接跳过——回零反而要先唤醒再重停一次，休息态本身就是最好的停机状态。464 绿。**休息态(卸力) + 发热诊断**：臂停哪都是热——稳态保持电流=该姿态真实重力矩(前馈错只挪臂的位置、不增大电流),j3 静止就扛 ~6.8 N·m;真浪费在零位:臂被前馈悬停在止点上方白烧。新增 `POST /api/rest` 休息态:零位门限内发 kp/kd/tau=0 的 MIT(非 disable,铁律不动),臂落止点、电机近零电流;循环逐 tick 看漂移,被碰即醒(重新保持),任何运动/急停自动醒;前端监视器「休息/唤醒」按钮 + 休息横幅。4 条 controller 测试 + 门闸测试,463 绿。待真机确认各关节止点。**模板库归位 + 模式切换更显眼**：模板原来埋在「新建序列 → 从模板」里、没有独立展示(实现偏离了 TIMELINE.md 的「素材库 位姿|模板 两页签」);剪辑模式下左栏恢复「位姿 | 模板」页签——模板卡带「用它 → 逐站位绑定位姿」直接进向导,空状态说明「存为模板」入口;点哪去哪模式左栏仍只有位姿。顶栏模式切换放大成实心药丸(选中=白底黑字,全屏最强灰阶对比),简单模式顶栏加「点一张位姿卡 → 臂开过去」引导。构建通过、459 绿。**双模式:点哪去哪(默认)/ 剪辑**：把「使用层」从完整编辑器里拆出来当默认屏——简单模式只有监视器 + 位姿库 + 急停(无序列 / 时间轴 / 走带条,追加按钮隐藏),点一张卡 = 臂开过去 + toast;顶栏「点哪去哪 | 剪辑」页签切换,完整三区编辑器归剪辑模式;模式记忆在 localStorage,切回简单模式自动停预演(真执行不停,那是臂的运动不是视图)。构建通过、459 绿。**位姿卡点击修复 + goto 反馈**：上一轮把卡名从按钮改成 span 后，`.lib__pose-top` 的 stopPropagation 把整个名字区变成了「点了没反应」的死区——点卡身会 goto、点名字什么都不会发生；去掉顶层 stopPropagation、只在改名输入框拦截，整卡任意处点=臂开过去；goto 被接受后加「臂开往「X」…」toast，处理中有了明确提示。**位姿卡交互显性化 + 首启引导 + 素材库折叠（剪映式快赢）**：删除/改名原来藏在 hover 才出现的 `⋯` 菜单里（`opacity:0` 直到 hover，触屏/新手根本看不见 = 「怎么删位姿」的根因），改成卡上常驻「＋追加 / 改名 / 删除」三按钮；去掉「被 N 条序列链接」chip（删除确认框已展示受影响序列）；素材库空状态引导「还没有位姿 → +录位姿」；素材库可收起；幽灵位姿默认关。构建通过。**MIT 斜坡缓动（去起停猛冲）**：线性斜坡在起停处速度不连续，kp=50 把速度阶跃传给臂，goto 时操作者感觉「猛的一下、没有保护」；`move_to` 改 smoothstep 缓动（峰值=匀速 1.5×），限速的首段进站/goto 时长乘 `EASE_PEAK` 补偿、峰值仍压 0.25 rad/s；用户自选过渡时长不补偿。459 绿。**前端 3D 监视器：显示平滑 + 幽灵位姿开关**：实况 20 Hz 直设关节让监视器一卡一卡，改成 60 Hz 单极点跟随（时间常数 60 ms）在样本间平滑、预演/切姿态也自然过渡；幽灵位姿加「开/关」开关（默认开），位姿一多可关掉。构建通过。**真臂运动路径重写：MIT 斜坡**：真臂实测发现固件在 enable 时锁存控制模式、运行时切换被无视（急停冻结软绵绵 + POS_VEL goto 零位移两次超时，对照实验「先 mode_mit 再 enable」后 kp=50 保持立刻硬）——`connect()` 改为 enable 前设 MIT；`move_to` 从单发 POS_VEL 改为按时钟插值的 MIT 斜坡（执行器每 tick 重发，`hold`/`set_float` 清斜坡）；删运行时模式切换。急停冻结由此天然成立。459 全绿。**真臂-only 修复：按组分片命令**：上游 `JointGroup` 按组内关节表索引数组——全 7 维数组让 `send_pos_vel` 在 arm 组第 7 位越界（IndexError，真臂首次 goto 即炸），`send_mit` 则让 gripper 静默读走 joint1 的值；`ArmSession.move_to`/`_send_mit` 改为按组切片（tau 先算一次再切），`tests/test_arm_session.py` 加 `_FakeArm`/`_FakeGroup` 钉住组尺寸与取值（459 全绿）。模拟器永远测不出这条——SimArm 没有组。**出厂配置切到官方一整套（gripper: true）**：yaml 开关翻正 + 16 处测试从「出厂无夹爪」改为编码新真相（7 关节、profile 锁 gripper、options=[gripper]、gripper_motor=true），mock/契约同步，`test_contract` rig 补 `tuning=store.load()`（458 全绿、构建通过）；B1 传输形态经探测确认（XCAN-USB + MacCAN）。**接线态一致性补丁**：夹爪电机在总线时 profile 被锁为 `gripper`——`TuningStore.load()` 对陈旧文件强转（带日志）、`Controller.apply_tuning` 拒绝非 gripper（服务端闸门，UI 选项只是 UX）；2 条新测试（458 全绿）。**负载模型解耦（质量 vs 总线）**：profile 回答「挂什么质量」、yaml 开关回答「电机在不在总线」——两个正交事实。`gripper` profile 在电机 off 时合法 = 死重态（`effective_urdf_path` 返回 vendor 原文件，0.8 kg 质量保留、执行器缺席）；controller 拒绝移除、`payload_options` 恒含 gripper、mock/契约同步、`test_assets`/`test_config_api` 更新。动机：操作者把夹爪装回但未接线做功能测试，旧模型无法表达此物理态（质量被剥 = 重力前馈少补 0.8 kg，浮动手感下坠）。**演示路径（首启种子）**：`backend/sequences/seed_demo.py`——空 store 首次启动种入与 mock 同款的「四方位」demo（4 位姿 + 四方位拍摄序列 20.5s + 模板），每条数据过 `validate_pose`/`validate_sequence`（mock 原姿态被真校验拒绝：joint3 越限 + 自碰撞，已换成限位内无碰撞姿态，mock 同步回改）；每次部署只种一次（marker 文件），`REBOT_SEED_DEMO=0` 关闭；挂在 `main()` 的 `maybe_migrate` 之后（先迁后种，非空即跳过）。8 条新测试 + 真启动冒烟验证。**参考方案共识落档 + 展示模型 v2 前端实施**：架构锚点重写为「开源 AIoT 机械臂参考方案」——平台层 + 参考场景层、时空编程序列、夹爪 = 插件 + 中介能力、感知不进内核、真臂为演示载体、三平台文档化、零兼容承诺（`docs/ARCHITECTURE.md`，同 commit 更新本文件）。前端十项交互已提交（447 测试全绿、构建通过）：幽灵位姿 + 相机契约六条、位姿卡即目的地、模板并入新建序列、示教条改造、急停解除→零重力示教、时长去重；真浏览器人工验收 3D 点击手感与相机预设待做。此前完成：**调参面板（Tweakpane）+ 负载 profile 机制**：负载三态枚举 bare（裸臂）/ camera（夹爪位挂相机，须先称重填 mass 否则 422）/ gripper（电机在总线上才存在，不能热加）；链路 = `backend/tuning.py`（pydantic 钳位 + TuningStore）→ `Controller.apply_tuning` 分级闸门（执行中拒一切写入；负载切换额外拒于浮动中——前馈跳几 N·m；float kp/kd 浮动中可改——follow 目标=当前位置跳变为零，边掰边调就靠这条）→ `api/config.py` GET/PUT/save/reset；camera 把 mass+com 注入 `gripper_end` 的 `<inertial>`（`effective_urdf_path` 扩展）；热改只进内存、显式保存落 `config/tuning.yaml`（独立文件——硬件 yaml 是带注释的上游 fork，yaml 往返会吃掉注释）；前端面板停靠监视器区右侧（Tweakpane 全灰阶、prod 进入弹确认、脏段「● 未保存」标记、409/422 原样显示服务器原因、Tweakpane 只在 ev.last 发 PUT）；mock 全镜像 + 契约 case 09-tuning。测试：test_tuning.py（默认值镜像代码常量等 10 条）+ test_config_api.py（闸门/持久化 11 条）。此前完成：**无夹爪时重力模型剥离夹爪质量**（「臂自己往上抬」的主嫌疑）：`gripper: false` 原本只剥电机，URDF 里 0.8004 kg 夹爪质量还在重力模型里，幻影力矩 q=0 时 j3 +3.26 N·m；新增 `assets.effective_urdf_path()`（`effective_hardware_yaml` 同款 load-time 生成），`ArmSession._dynamics_model()` 与 verify 脚本切换，碰撞/运动学仍用 vendor 原 URDF（幻影几何是保守方向）；test_assets.py 三条新测（质量剥离 / 开关行为 / 重力差分）。此前完成：插件「丢文件夹」安装机制（方案 A 落地）：`plugins/<名字>/` 放代码 + `plugin.json`（`module`/`provider`/`enabled`)，启动时 `ActionRegistry.discover_dir()` 扫描，过同一个 `check_shape` 闸门，加载失败/停用都灰显带原因（复用 `_broken`，manifest 形状不变、契约零改动）；`enabled: false` 即开关，状态随插件文件走。动机：手动 `uv pip install` 的插件会被 `uv sync` 清掉（`device.sh push` 每次都 sync)，重启就报模块缺失 —— 丢文件夹不进 lockfile 所以清不掉；`plugins/` 已 gitignore 但随 push 同步。限制写进 PLUGINS.md：只能用宿主环境已有依赖、模块名不能撞、加载仍需重启（「刻意不做」的热重载条目改写为「运行时加载/卸载」并给出理由）。`backend/actions/check` 与 `backend.app` 都扫两条路径。tests/test_plugin_dir.py 9 条（含模块名撞名 → 先加载者赢 → 重复 id 被拒出声）。此前又完成：**急停解除→自动进零重力示教**（操作者实测「解除后臂僵死掰不动」；clear 成功后 `set_teaching(True)`，先锁定、手一动即浮动；后端 + mock + 契约同步，`test_estop_api.py` 两条新测，`test_events_api.py` 一处流程适配）。上一条（浮动沉默 `follow()` + 到位=静止驻留判定）随本批一起待提交 |
| **备注** | `dev.sh` 两种模式：`sim`（仅前端，旧名 `mock` 保留为带警告的别名）/ `prod`（完整启动）；API 联调写法 `prod --no-build`；安全措施与启动模式无关。模式徽标：sim=蓝 / prod=灰阶加粗+✓ / 臂动扫琥珀 / 断连灰阶脉冲，红绿独占不动（规格见 #rebot-arm thread fdf6a140）。模式徽标与连接状态是两个独立维度：断连显示为徽标旁灰阶脉冲「已断连」，不覆盖模式徽标。进入 prod（或 sim→prod 切换）时弹阻断式全屏警告，需点「我已了解」确认；警告层 z-55，低于急停栏（60），Escape 仍是急停快捷键；警告 ⚠ 图标灰白中性（四色纪律：琥珀=臂在动）。插件 `retryable` 无默认值，`check_shape` 注册前拒绝未声明的插件。`device.sh`：`open` 加 `ExitOnForwardFailure`（端口被占立即失败不开死页）；`push` 后 `status` 改为轮询健康检查（30s 超时），冷启动慢不再误报 no response。退出回零已落地：Ctrl+C / SIGTERM 先 `Controller.park_home()` 慢速回零（复用 goto 进站限速）再停循环退出，闩锁吸合时原地冻结退出不回零；信号归 `backend/app.py` 的 `ParkOnExitServer`（uvicorn 原版二次 Ctrl+C 会跳过 lifespan shutdown）；systemd `TimeoutStopSec` 20 → 60；`main()` 碰 CAN 前做端口预检（实测到双实例抢占：绑不上端口的实例已连真臂，退出回零会动别人管的臂）。真机验证退出回零待做（H）。终端日志已着色：`backend/app.py` 的 `_configure_logging()`，TTY 时级别名上 ANSI 色（配色与 uvicorn 一致：WARNING 黄 / ERROR 红），`NO_COLOR` 或重定向/journalctl 时纯文本。udev 规则已审计重写：删掉 CAN 占位规则（臂走 socketcan `can0`，全仓库无代码引用 `/dev/rebot-can`，且 CAN 网卡不是 tty、`SUBSYSTEM=="tty"` 永远匹配不上）；turntable 无规则（插件默认 `/dev/rebot-turntable` 但硬件 VID/PID 未知，到位后补规则或先在设备上设 `TURNTABLE_PORT`）；shutter 规则保留（303a:1001 = 固件 `ARDUINO_USB_CDC_ON_BOOT=1` 的 Espressif TinyUSB 默认，设备上 `lsusb` 确认） |

---

## 环境标记

工作按哪里能验证完划分：

| 标记 | 含义 | 现状 |
|---|---|---|
| `L` | 开发机就能做完（写码 + 单测 + sim） | 可用（macOS） |
| `H` | 需要真臂 / R2x / CAN 总线才能验证 | **暂不可用** |
| `E` | 需要 XIAO ESP32-S3 板子才能烧录验证 | 未确认是否在手 |

`H` / `E` 的工作分两半：代码与测试在开发机写完跑通，实测结果填进 `docs/HARDWARE_NOTES.md` 的「待实测」段。

---

## 🚧 阻塞 / 待验证

这六项**只挡验证，不挡代码** —— 相关实现都已写完并有测试，缺的是在真机上确认数值。

| # | 项 | 影响 | 怎么解 |
|---|---|---|---|
| ~~B1~~ ✅ | **CAN 形态已解（2026-08-14 实测）**：macOS 直连 = XCAN-USB 适配器 + MacCAN `libPCBUSB.dylib`（darwin 后端把 `channel: can0` 映射 PCAN_USBBUS1），yaml 零改动；零位巡检 7 电机全在线。**唯一坑**：直跑 `uv run`（零位示例、`verify_gravity.py`）必须 `export DYLD_FALLBACK_LIBRARY_PATH="$HOME/.local/lib:..."`——`dev.sh prod` 已自动注入，裸跑会报 `load PCBUSB failed` | — | 已解 |
| B2 | 末端挂佳能机身后重力补偿是否还准（上游标定是**空载**，误差 5–11%） | 挡挂相机后的浮动手感实测。`FloatLockConfig` 的速度阈值和 `ArmSession` 的 MIT 增益都做成可配就是为了这里重调。裸臂状态已对齐（夹爪质量已剥离，#11） | 相机到货：称重 + 量质心，把等效 `<inertial>` 注入 `gripper_end` 所在 link（`effective_urdf_path` 就是 load-time 改 URDF 的机制，加负载是同一处扩展），然后跑 `scripts/verify_gravity.py` 复核；次选重调阈值 |
| B3 | R2x 上 500 Hz 控制频率能否稳住（yaml 默认值，上游没说在什么算力上测的） | 只影响频率取值。sim 下实测 100 Hz 稳，真机换成上游 `start_control_loop` | 跑控制循环测实际 tick 抖动，不稳就降频并记录 |
| B4 | XIAO ESP32-S3 板子是否在手 | 挡固件烧录验证。固件与主机侧协议都写完了，协议在内存管道上有 30 个测试 | 有板子就 `cd firmware/esp32-shutter && pio run -t upload` |
| B5 | 进站限速 0.25 rad/s 是按 demo 安全同步 15°/s 取的，挂相机后未标定 | 进站过快是安全风险，过慢拖拍摄节拍；execute 与 goto 的进站段都走它 | 真机跑进站观察过冲/共振，必要时再降 `executor.py` 的 `FIRST_APPROACH_MAX_SPEED` 并回填实测值 |
| B6 | 到位静止判定 `SETTLE_DRIFT_RAD=0.003` / `SETTLE_MIN_S=0.15` 未在真机标定 | 过紧则差分噪声让臂「永远不到位」、每个序列在进站 deadline abort；过松则糊片风险回升 | 真机跑序列，若见到进站 timeout abort 先放宽这两值到能稳定通过的最紧值并回填（HARDWARE_NOTES「其它待确认」有完整说明） |

---

## 决策速查

四条铁律（急停不能调 `estop()`／不许用上游默认资产解析／速度不能读 `mechVel`／不重造运动学）写在 [`AGENTS.md`](./AGENTS.md)，**只维护那一份** —— 两份副本会漂移，而漂移掉的正是这类救命细节。

源码级证据见 [`docs/HARDWARE_NOTES.md`](./docs/HARDWARE_NOTES.md)。
