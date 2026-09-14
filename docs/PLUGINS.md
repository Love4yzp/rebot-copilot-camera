# 后续能力接入 · 设计接口（未实现）

本文是设计参考，不是当前可安装的插件 SDK。当前无插件发现、动态加载、快门、Agent 租约、provider 路由或动作 worker；Sequence v3 只接受 wait。

## 分开四种职责

| 扩展 | 输入 → 输出 | 可触及的边界 |
|---|---|---|
| 固定末端描述 | 质量、质心、惯量、安装框架、碰撞几何 → 选定 URDF | SDK 模型构造；当前已实现，重启生效 |
| 动作能力（未来） | 已校验参数 + 只读执行上下文 → 异步任务结果 | 不能拿到 arm、latch、store；不能直接发 CAN |
| 触发源 | 业务意图 → 既有受闸门约束的 HTTP 命令 | 与人工按钮同权；source 只是审计标签 |
| 事件订阅 | 单向事件 → 下游记录/展示 | 不可否决、不反压、不作为安全证明 |

不要把它们合成可修改运动的 pre/post hook 链。Agent 若回来，先是普通受约束的外部客户端；增加独占控制、认证、撤权、TTL 前必须重新设计并测试完整权限边界。

## 动作能力的候选契约

只有出现真实配件需求时才实现以下设计，不预建运行时接口：

```text
CapabilityDescriptor
  id, api_version, label, parameter_schema
  effect: observe | accessory
  retry: never | idempotent
  cancellation: supported | unsupported

submit(parameters, ReadOnlyContext, request_id) -> JobHandle
poll(job_id) -> queued | running | succeeded | failed | cancelled | unknown
cancel(job_id) -> cancellation_requested | unsupported

ReadOnlyContext
  sequence_id, block_id, measured_joints, timestamp
```

参数与 UI 字段由声明式 schema 提供，不接受第三方 JS/HTML。权限、安装状态、实时健康状态和正在执行的任务分别建模。加载失败、健康未知、协议不兼容不能伪装成功。

动作必须在隔离 worker/进程执行；控制循环只投递和非阻塞读取结果。取消超时不能等价于副作用已取消：快门可能已拍、转台可能仍在转，应报告 unknown 并由宿主保持/终止流程。非幂等动作不得自动重试。急停先保持臂，再请求取消配件；不能为等待配件响应而延迟保持。

动态接入建议先采用进程外协议；待在途任务清空、能力版本验证通过后再更新可用能力。不要承诺 Python 模块可安全热卸载。

## 当前可以使用的接口

- `POST /api/poses/{id}/goto`、`POST /api/sequences/{id}/execute`：显式运动，走同一 Controller 预检、Activity/Intent 和闩锁；source 只用于说明谁发起。
- `POST /api/execute/stop`、`POST /api/estop`：停止与急停；清除不自动续跑。
- `WS /api/events`：sequence.started/done/aborted、pose.arrived、estop.engaged/cleared、teach.captured。有界队列可能丢包，不是可靠投递的业务账本。

当前没有认证或独占租约。需要第三方/不可信网络接入时，先在部署边界建立认证与网络隔离；不要直接把模型输出接成自动运动命令。本文不授权任何配件或真实机械臂操作。
