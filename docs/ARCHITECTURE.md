# 架构 · Teach & Repeat

应用只负责示教、位姿素材、序列编排和安全策略。机器人算法与通用基础设施复用 [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py)；摄影不是当前产品身份。

## 内核边界与部件关系

```text
React 工作台 ── HTTP / 控制 WS ── Controller + Activity + SafetyLatch
                                      │
                             序列执行 / 示教 / 保持
                                      │
                                 ArmSession
                                      │ 同一 MIT 指令
                          ┌───────────┴────────────┐
                       SDK RebotArm          SDK MuJoCo transport
                         真实 CAN             1 ms 物理积分
                          └───────────┬────────────┘
                                实际反馈状态
                                      │ 有界消息队列
                             SDK MeshCat worker
                                      │ 回环服务 + 只读代理
                                /viewer/ iframe
```

每个进程只选择一条运行路径：prod 真机或 sim 物理仿真；没有运行时切换、没有双臂会话。Vite 只是已有后端的热更新客户端，不是另一套模拟器。

| 层 | 拥有 | 不拥有 |
|---|---|---|
| React | 位姿选择、显式运动按钮、站位编辑、状态与调参呈现 | FK、IK、重力、轨迹插值、机器人资产加载、CAN |
| 应用内核 | Activity/Intent 表、横切闩锁、闸门、看门狗、示教判据、到位判定、MIT 会话 | Pinocchio/MotorBridge/MuJoCo/MeshCat 的直接实现 |
| 应用编排 | Pose、Sequence、Template、wait、存储、HTTP/WS | 插件发现、第三方执行线程、Agent 租约 |
| SDK | FK/IK/重力、轨迹数学、限位/碰撞查询、物理 transport、末端模型构造、MeshCat worker | 业务位姿、序列、HTTP 权限、Activity、真实标定策略 |
| Pinocchio / MotorBridge | 由 SDK 使用的算法与传输实现 | 应用的直接依赖入口 |

“移除底层两个库”指删除应用的直接依赖和直接调用，不是卸掉 SDK 需要的传递依赖。浏览器也不调用 Python SDK：它使用应用 API；MeshCat 自带 Three.js，但项目不再维护 Three.js 机器人查看器。

SDK 基线锁定 `d54040596faa94bdc4f8ad93f3f06b33dfe3a1bf`，不自动追踪上游。SDK 扩展由 `app/vendor-patches/rebot-sdk.patch` 分发，`prepare_sdk.py` 显式检查基线、拒绝重叠修改。后续可贡献给上游或迁至维护 fork；当前没有发布远端 fork。算法移入 SDK 不改变已有轨迹的行为。不要把补丁工作树当成可随意更新的上游 main。

## 复用与应用策略

`ArmSession` 负责字典与 SDK 数组之间的关节映射、有限差分速度、持续 MIT 会话、参考状态交接和应用标定系数。轨迹多项式与可行时长计算在 SDK；急停、保持、休息、预检的时序在应用。固件只能终身 MIT 的事实仍适用，见 [硬件记录](HARDWARE_NOTES.md)。

限位与碰撞查询由 SDK 提供。应用决定何时预检、如何解释拒绝原因。路径采样是粗筛，不是连续碰撞安全证明。换末端后，结构碰撞排除项仍来源于已验证的原装模型，不能把新工具在零位碰到基座也当成“结构接触”排掉。

`SimArm` 只用于注入时钟的快速行为测试；它不是 `./dev.sh sim` 的运行后端。不再提供纯前端 mock、预演插值或场景点击运动。

## MuJoCo 是什么输入

MuJoCo 是虚拟被控对象，不是替代真机的重力真值。相同 MIT 目标、速度、kp/kd 和重力前馈进入模型，施加力矩为 PD + 前馈，并按 URDF effort 限幅。物理按 1 ms 积分，应用控制循环 100 Hz；状态读取不推进物理。关闭/折叠浏览器查看器不停止仿真，但控制客户端断连仍按原看门狗进入 SafeLock。

它帮助观察负载变化、跟踪误差、饱和和模型碰撞；不证明实际硬件安全。质量、质心、惯量、摩擦、间隙、执行器动态的误差仍需真机标定；只填质量不是可信的动态模型。当前不模拟抓取接触任务，不虚构单个夹爪电机到两个手指的映射。

`GET /api/sim/state` 明示计算力矩、跟踪误差、时间落后与模型来源；`POST /api/sim/perturb` 只在仿真实例的未急停示教中接受输入，最多 200 ms、各关节 effort 的 20%，自动过期，退出示教或急停后清零。

## 末端模型与配置

基础 RS URDF + 一个固定末端描述生成选定模型，SDK 重力、MuJoCo、MeshCat 与碰撞查询复用该模型。质量 kg、质心 m、惯量 kg·m²，统一相对 `gripper_end`，惯量在质心处、坐标轴平行安装框架；盒体中心在质心。未提供惯量时按均匀盒体估算，并明确标注 `box-estimate`。这只是粗略近似。

sim 使用独立 `data/sim/` 数据与 tuning，不读取/写入真实标定文件。末端结构在一次运行期间固定，更换需停止并重启，不允许重力、碰撞和查看器各自热切不同模型。原有真机 camera profile 的质量/质心重力标定保留，但没有完整末端描述时不作为动态模拟输入，几何仍为原装保守占位。

## 查看器隔离

MeshCat 只接收测量/仿真反馈，不接收期望轨迹作为“实际位置”。独立 worker 持有 ZMQ，最新值邮箱有界，浏览器慢或查看器故障不能反压控制循环。内部 HTTP 与 ZMQ 显式绑定回环；同源 `/viewer/` 与 `/viewer/ws` 代理只向浏览器发送场景，浏览器入站数据被丢弃。

查看器 socket 不续控制看门狗；失联或超过 1.5 s 无更新时标记画面已过期。浏览器仅提供相机轨道控制与复位视角，不提供机器人运动控制。iframe 内 Esc 转交宿主急停端点。

## 数据与后续接入

Schema v3 保留 hold/transition 和内建 wait；非 wait 标记在写入时拒绝。v2 插件序列不自动迁移、不删除原文件；真实位姿和调参不作清理。模板只保存结构，不保存关节角，实例化后脱钩。

插件、快门、Agent 的运行时、路由、前端表单、固件示例、插件示例与摄影 seed 均不交付。未来能力接口只写在 [扩展设计](PLUGINS.md)，不留空 registry 或未使用 Protocol。普通受闸门约束的 REST 与单向事件流仍存在；没有认证、独占租约或自治 Agent 的隐含承诺。

交互见 [TIMELINE](TIMELINE.md)，文件定位见 [CODEMAP](CODEMAP.md)，硬件事实见 [HARDWARE_NOTES](HARDWARE_NOTES.md)。
