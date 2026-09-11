# 代码地图

修改 backend 前从这里定位；职责边界见 [ARCHITECTURE](ARCHITECTURE.md)。

| 路径（app/ 下） | 责任 |
|---|---|
| backend/app.py | FastAPI 装配、端口预检、运行时选择、控制循环生命周期、退出回零；静态入口最后挂载 |
| backend/runtime.py | 单个 prod/sim 实例、选定末端模型、MuJoCo/查看器启停、反馈转发 |
| backend/assets.py | 显式 RS 资产与硬件配置、gripper 总线开关、遗留真实负载模型 |
| backend/config.py | 部署路径、端口 |
| backend/tuning.py | 调参模型、范围和独立 sim/真实存取 |
| backend/arm/base.py | ArmDriver、ArmState 与会话契约 |
| backend/arm/session.py | SDK 的 MIT 会话适配；真机/仿真共用，关节映射、差分速度、保持/示教/运动交接 |
| backend/arm/profile.py | SDK 轨迹类型的薄重导出，不实现算法 |
| backend/arm/limits.py | URDF 关节界限与应用容差 |
| backend/arm/sim.py | 可注入时钟和故障的快速测试替身，不是运行时 sim |
| backend/safety/kinematics.py | SDK 碰撞/限位/FK 的显式 RS 模型包装与预检策略 |
| backend/safety/latch.py | 横切急停闩锁 |
| backend/safety/watchdog.py | 控制间隔、读失败、保持漂移的持续故障判据 |
| backend/safety/client_watchdog.py | 控制客户端沉默 → SafeLock |
| backend/safety/contact.py | 接触残差观测，默认关闭 |
| backend/core/activity.py | Activity / Intent 唯一决策表 |
| backend/core/controller.py | 闩锁优先、预检、控制 tick、持续保持、调参/仿真外力闸门 |
| backend/core/executor.py | hold/transition/wait 遍历、到位与续跑；无插件/快门依赖 |
| backend/core/floatlock.py | 浮动/锁定迟滞和最短静止判据 |
| backend/core/broadcaster.py | 线程到 asyncio 的有界消息队列 |
| backend/core/events.py | 单向语义事件名与信封 |
| backend/sequences/models.py | v3 Pose / Sequence / Block / wait / Template |
| backend/sequences/normalize.py | 序列自动过渡与结构归一化 |
| backend/sequences/store.py | 每文档一 JSON，原子写；不迁移旧插件数据 |
| backend/api/gate.py、estop.py | 运动闸门和立即吸合闩锁的入口 |
| backend/api/poses.py、sequences.py、templates.py | CRUD、显式 goto/execute、结构实例化 |
| backend/api/preflight.py | 位姿解析，校验统一通过 Controller |
| backend/api/control.py | teach/rest/stop/resume、控制与事件 WS |
| backend/api/config.py、logs.py | 调参、日志 |
| backend/api/simulation.py | sim 外力和计算反馈，prod 不接受 |
| backend/api/viewer.py、viewer.html | 同源 MeshCat 页面、静态脚本、只读 WS 代理、过期提示 |
| backend/export_contract.py | 无服务启动的 OpenAPI TypeScript 导出/检查 |
| backend/validation/ | 可选无界面物理场景、报告与判定，plant 来自 SDK |
| frontend/src/App.tsx | 单工作台、部署身份、显式运动、到位认领 |
| frontend/src/library/ | 位姿选择/录制、序列管理、模板向导 |
| frontend/src/timeline/SequenceEditor.tsx | 固定站卡与等待编辑 |
| frontend/src/timeline/model.ts | 纯结构归一化，与 Python 做 golden 对比；无浏览器物理 |
| frontend/src/monitor/FeedbackDetails.tsx | 计算力矩/误差/饱和/外力输入 |
| frontend/src/components/ | 急停、状态、调参、日志、对话框 |
| frontend/src/generated/api.ts | OpenAPI 生成的类型，CI 防漂移 |
| frontend/contract/ | Node 归一化与类型生成工具，无 mock API |
| contract/cases/、contract/expected/ | 归一化输入与 REST golden 结果 |
| tests/ | 行为、安全、契约、物理与查看器回归 |
| vendor/reBotArm_control_py/ | 锁定 SDK 子模块；算法/模型/transport/查看器 worker |
| vendor-patches/rebot-sdk.patch、prepare_sdk.py | 可复现 SDK 扩展；拒绝不匹配基线及覆盖本地修改 |
| config/rebotarm_rs.yaml、config/tuning.yaml | 真实硬件与操作者标定，不随 sim 改写 |
| data/ | 真机位姿/序列/模板；sim 在 data/sim/ 下隔离（gitignored） |
| deploy/ | CAN 与应用 systemd 服务；无快门 udev |

actions/、shutter/、agent、对应 API、固件、插件示例、摄影 seed、frontend/mock 已移除。未来接口仅在 [PLUGINS](PLUGINS.md) 设计说明中，不保留空框架。

仓库根的 dev.sh 是本机入口和前端构建唯一所有者；device.sh 只供人明确部署时使用。既有 threejs-* 参考技能保留。
