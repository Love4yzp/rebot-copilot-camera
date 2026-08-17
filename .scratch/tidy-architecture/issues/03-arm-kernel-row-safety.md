# 03 臂接口「内核行」与安全耦合姿势

Type: grilling
Status: resolved

## Question

回答 Q-F 与 Q-G；答案写进 10 票的接口表「内核行」。

**背景**（已探明事实）：

- `backend/arm/base.py`：ArmDriver 是「everything else programs against」的接口，刻意极小；`hold()` 明文是急停路径（「the emergency-stop path」，冻结姿态 + MIT + 重力补偿），`move_to` 与 `hold` 刻意分开 ——「合并它们会让急停与一次非常快的移动不可区分」。
- 全仓库只有 2 个文件消费 ArmDriver：`core/controller.py`、`core/executor.py`（executor 的 arm 是 controller 注入的）。api 与插件摸不到臂。
- 安全耦合只有一点：控制循环每 tick 第一件事查 SafetyLatch（controller.py「The latch is checked **before** anything else gets to command」）；锁住时对臂调 `hold(freeze_pose)`。
- `backend/safety/latch.py`：纯逻辑，「never imports the arm layer」—— 急停不认识臂、臂不认识急停。
- `backend/arm/factory.py`：真臂打不开时回落 SimArm 且「一定出声」；前端有 SIM 徽章并轮询检测 sim↔prod 切换（App.tsx:216）。
- 夹爪电机与臂在同一条 CAN 总线（id 0x07），安全性质相同。

**Q-F**「提高优先级」指哪层：

- a) **架构地位显形** —— 在 10 票的接口表里给 ArmDriver 标「内核接口」行：动作集（hold 即急停路径 / move_to / relax / set_float / follow）、实现 ×2（ArmSession / SimArm）、使用者唯一 = 控制循环、前置件 = 急停闩锁 + 运动闸门、失败语义 = 出声回落 + SIM 徽章。**（推荐）**
- b) 运行期保障 —— 已存在（唯一消费者 + 出声回落 + SIM 徽章），无需新代码。
- c) OS 线程优先级 —— 不适用（驱动无线程，控制循环才有线程，provider 绝不跑在它上面，watchdog 兜底）。

**Q-G** 安全与臂的结合姿势：

- a) **外置闩锁、单点耦合**（现状：安全不认识臂、臂不认识安全，只在控制循环见面）。**（推荐）**
- b) 安全下沉进 ArmDriver —— 反对：破「arm 只薄封装上游」铁律；闩锁必须纯逻辑才能不带机器人测试；夹爪同总线、同安全性质，下沉 = 每个驱动复制一份急停权威，最后「急停」有两个权威，正是让急停与快速移动不可区分的死法。

**裁决要求**：Q-F 选 a/b/c，Q-G 选 a/b，并说明是否接受推荐。

## Answer

（2026-08-17，操作者裁决：「按你的来」）Q-F = a：架构地位显形 —— 10 票的接口表给 ArmDriver 标「内核接口」行（动作集 / 实现 ×2 / 使用者唯一 = 控制循环 / 前置件 = 急停闩锁 + 运动闸门 / 失败语义 = 出声回落 + SIM 徽章）。Q-G = a：外置闩锁、单点耦合，维持现状（安全不认识臂、臂不认识安全，只在控制循环见面）。
