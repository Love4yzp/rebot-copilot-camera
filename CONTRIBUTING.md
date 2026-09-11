# 贡献指引

先读 [AGENTS](AGENTS.md)，再按 [CODEMAP](docs/CODEMAP.md) 找实现；硬件相关改动先读 [HARDWARE_NOTES](docs/HARDWARE_NOTES.md)。结构和复用边界在 [ARCHITECTURE](docs/ARCHITECTURE.md)，当前状态在 [PROGRESS](PROGRESS.md)。

## 工作流程

1. 明确外部可观察行为，再修改应用策略或 SDK 对应责任。
2. 保留真实数据与标定；不要启动后端或操作真实机械臂来“顺便验证”。后端只由人通过 dev.sh 启动。
3. 在 app/ 执行 pytest 和 Ruff；用仓库根 dev.sh build 构建前端。
4. API 变化同步 OpenAPI 生成类型；REST golden 与 TS/Python normalize 必须通过。
5. SDK 改动同步补丁并跑可复现检查；未经硬件回归，不升级锁定的 URDF/标定基线。

## 架构体检

- 算法是否只通过 SDK？应用不得直接导入 Pinocchio、MotorBridge、MuJoCo 或 MeshCat。
- 运动是否仍经过 Controller 预检、Activity/Intent 和横切闩锁？新运动路由必须显式挂 gate，递归路由扫描与 OpenAPI 交叉检查不能删。
- 查看器或第三方服务能否阻塞控制循环？反馈队列必须有界，查看器不续控制心跳。
- UI 是否仅根据后端反馈显示实际状态？位姿选择不能直接移动臂。
- 是否为尚不存在的配件留下空运行时框架？当前扩展只做设计，见 [PLUGINS](docs/PLUGINS.md)。

硬件准确性与软件测试分别报告。MuJoCo 的通过不等于真实末端已校准，不应以仿真结论覆盖原有禁区。
