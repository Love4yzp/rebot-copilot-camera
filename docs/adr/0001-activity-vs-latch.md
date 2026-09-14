# ADR 0001: Activity 状态互斥与横切急停闩锁

## 背景

控制器早期使用重叠的标志位（`_teaching`、`_resting`、`_executor`）按优先级推导 `mode`。每增加一种行为（休息、断连锁定、接触、改向）就要新增一个标志位，所有调用方都必须重新审计状态组合。

## 决策

1. **Activity 为互斥集合**：机械臂运行状态定义为闭集，`Intent` 是改变它的唯一途径。`decide(activity, intent) -> Decision` 决策表即接口——增加 SafeLock 或 Goto 改向是加一行，不是加一个标志位。Effect 描述控制循环要对臂做什么，表本身不碰硬件。
2. **闩锁不是 Activity**：「某些 Activity 可能忘记进入」的冻结状态，正是 48V 的臂在急停之下还会动的原因。调用方先查闩锁；`mode == "estop"` 是视图，不是表状态。
3. **HTTP 保持资源形状**（`/api/poses/{id}/goto`、`/api/teach` 等）：处理器解析后调用 `Controller.intend`，不建第二套命令总线。
4. **Goto 与 Play 是不同 Intent**：第二次 Goto 平滑改向，第二次 Play 拒绝。这是运动模型（设定目的地 vs 跑这盘带子），不是界面偏好。
