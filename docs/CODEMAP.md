# 代码地图 · 哪个文件干什么

逐行地图，碰任何 `app/backend/` 文件前读它定位。设计层的「内核边界与部件关系」在 [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md)，这里不抄。

超前建成模块标了 `⏸ parked` 与唤醒条件——既不往里加东西，也不当死代码删。

```
app/backend/
  app.py            FastAPI 入口。静态挂载必须在所有路由之后 —— mount("/") 匹配一切
  assets.py         URDF / 硬件配置路径解析的唯一出口 + assert_rs_model() 守卫 + gripper 开关（has_gripper / effective_hardware_yaml 剥电机 / effective_urdf_path 剥质量）
  config.py         只放真正依赖部署的值（数据目录、快门串口/波特率）。限位不在这（从 URDF 读）
  tuning.py         调参模型与存取：payload profile / 浮动增益 / 阈值 + 服务端钳位；热改只进内存，显式保存才落 app/config/tuning.yaml
  agent.py          外部 agent 的独占控制租约（token + 双重 TTL）【⏸ parked：有外部 agent 接入才扩，出厂应用无人用】

  arm/
    base.py         ArmDriver Protocol + ArmState。hold(q) 与 move_to(q, t) 是两个动词
    sim.py          SimArm。一阶滞后 + 可注入拖动。开发与测试的地基
    session.py      ArmSession —— 薄封装上游 RebotArm。dict↔ndarray 只在这一处转
    factory.py      真臂 / 模拟器选择。fallback 一定出声

  safety/
    latch.py        SafetyLatch。急停闩锁，纯逻辑不碰硬件
    watchdog.py     三个条件自动触发急停，都要求「持续」而非单次
    kinematics.py   限位（从 URDF 读）+ 自碰撞 + 路径采样 + FK

  actions/
    base.py         ActionProvider Protocol + ActionContext。ctx 里**没有 arm** —— 插件够不到运动闸门
    runner.py       每 provider 一条 worker。provider 绝不跑在控制循环上，probe 走同一条队列
    registry.py     entry_points + app/plugins/ 目录(plugin.json)两条发现路径 + check_shape 形状闸门 + 健康。runner 才是「装了哪些」的唯一登记处
    validate.py     写入时与执行前两道校验，让错误离开 ACTING 阶段
    shutter.py      ShutterProvider —— 第一个 provider（名为「快门 provider」，文件名 `shoot.py`，与驱动包 `shutter/` 区分）
    check.py        插件作者的无硬件开发循环：列 manifest / 跑自检 / 真 runner 真超时跑一次

  core/
    controller.py   控制循环。闩锁在任何东西能命令臂之前检查
    events.py       语义事件名与信封。单向，不可否决
    executor.py     Sequence 执行器（块遍历）。纯逻辑，注入时钟/arm/shutter/已解析位姿
    floatlock.py    浮动/锁定判据。带迟滞与最短静止时间
    broadcaster.py  控制线程 → asyncio 的扇出。有界队列，丢旧包

  sequences/
    models.py       Pose / EventMarker / Hold+Transition 块（判别式联合）/ Sequence / SeqTemplate
    normalize.py    normalize 的 Python 实现（TS 端是 app/frontend/src/timeline/model.ts），写入前必跑
    store.py        PoseStore / SequenceStore / TemplateStore，一文档一 JSON，原子写
    seed_demo.py    首启演示数据（四方位：4 位姿 + 序列 + 模板）。与 mock 的镜像被契约用例 seeded-library 钉死

  shutter/          【⏸ parked：链路已建全 + 协议 30 测试，待 B4 板子 + 相机到位（HARDWARE_NOTES B4）】
    base.py         ShutterDriver Protocol + 异常类型。USB 与 BLE 是两段链路，分开报
    protocol.py     行协议编解码 + LineReader
    esp32.py        串口客户端。单条在途，id 防迟到回包
    factory.py      真板 / 模拟选择。**快门永不回落** —— 回落等于 SimShutter 把每一帧都谎报拍到；只有 --sim 拿到模拟快门
    sim.py          SimShutter，可脚本化失败

  api/
    preflight.py    序列 / agent 共用的执行预检（位姿解析 + 整序列校验，全经 Controller.preflight_* 那道门）
    gate.py         require_arm_available —— 运动闸门，闩锁期间 409
    plugins.py      GET /api/plugins —— 前端据此渲染触发表单
    estop.py        急停端点。解除不是回到僵硬原位，而是直接进入零重力示教（先锁定、手一动就浮动）—— 急停后正是最需要用手掰臂的时刻
    poses.py        位姿库 CRUD / capture / links / goto
    sequences.py    序列 CRUD（写入即 normalize）/ execute / 运行中锁定
    templates.py    模板快照与实例化（hold.pose_id 用 slot:N 占位）
    control.py      execute/stop+resume / 示教 / 快门自检 / WebSocket
    agent.py        Agent 控制端点（OpenAPI 直接给 LLM 做 tool import）【⏸ parked：随 agent.py】
    config.py       调参端点 GET/PUT/save/reset —— 闸门在 Controller.apply_tuning（执行中拒一切、浮动中拒负载切换），不挂运动闸门，但要在 NON_MOTION_ROUTES 写明理由
    logs.py         journalctl 包装

app/frontend/src/       Vite + React + TS。时间轴编辑器三区（素材库 / 监视器 / 时间轴）；`timeline/model.ts` 是纯逻辑，src 与 mock 共享，与 app/backend/sequences/normalize.py 互为双语言端。调参面板 `components/TuningPanel.tsx`（Tweakpane，停靠监视器区右侧，全灰阶，prod 进入需确认）
app/frontend/mock/      `npm run dev:mock` 的内存后端。数据形状与后端逐字段对齐，由 golden 契约测试守卫
app/frontend/contract/  golden 契约的 mock 侧 runner（esbuild 打包，node 直跑）
app/contract/cases/     golden 用例文件：REST 会话 + normalize 输入，两侧各跑一遍逐字段比对，见 app/tests/test_contract.py
app/frontend/public/    自托管字体（离线设备，不能挂 CDN）
app/firmware/esp32-shutter/  PlatformIO 工程
app/deploy/             systemd unit ×2 + udev 规则
app/config/rebotarm_rs.yaml  从上游 fork 的硬件配置（挂相机后要重调）
app/config/tuning.yaml       调参面板的落盘值（操作者标定）；文件缺失 = 代码默认值
app/vendor/reBotArm_control_py/  git submodule，锁 d540405

app/examples/rebot-plugin-turntable/  可安装的动作插件示例（pyproject [tool.uv.sources] 钉住路径，挪动要同步改）
app/data/               运行时数据根：poses/ sequences/ templates/ 三个库（gitignored；REBOT_DATA_DIR 可改家）
.agents/ + skills-lock.json   threejs-* 参考技能（git 跟踪，刻意保留，见「技能体系」）
app/tests/               测试自成「测试」章节，不在地图里复述
```
