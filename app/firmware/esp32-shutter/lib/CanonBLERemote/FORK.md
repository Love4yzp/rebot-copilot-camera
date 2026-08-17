# 这份库为什么是 fork

上游是 [`maxmacstn/ESP32-Canon-BLE-Remote`](https://github.com/maxmacstn/ESP32-Canon-BLE-Remote)，
本副本基于 tag `1.0.2`（commit `e6e28f1`），在它之上打了补丁 —— 都是 R6 实机试出来的，
不是风格改动：

1. **所有 `writeValue` 改 write-with-response（第三个参数 `false` → `true`）。**
   write-without-response 时 R6 把两条命令都静默丢弃（FOCUS 也不动）；改过来之后
   `FOCUS` 开始生效。上游只在 6D2 一类老机身上试过，那边两种写都认，所以从没撞见过。

2. **写入从一字节改成两字节 `[cmd, 0x00]`。** nRF 抓包（pulsar-trigger 的研究日志，
   2026-06-30）里真 BR-E1 发的就是两字节。单字节写在链路层被接受、机身也回 indication
   确认收到，但 R 系机身**确认收到后拒绝执行** —— 正是我们在 R6 上看到的
   「FOCUS 有效、SHOOT 静默」。furble 虽然写单字节，它在 R6 II 上确认拍响走的
   是不是这条路存疑（furble 另有 smartphone-mode 实现）。
   **另一条同样必须满足的机身条件**：驱动模式设为「遥控」，否则连原厂 BR-E1
   都只对焦不拍照（同一份研究日志的原话：真遥控器在设置前同样失败）。

3. **新增 `printCharacteristics()`。** 诊断用：列出快门服务下所有特征的 UUID/handle/properties。
   `main.cpp` 的 `CHARS` 命令调它。

## 升级上游时的规矩

上游是一个人的业余项目，没有发布节奏。**不要**直接换 tag/commit —— 上面的补丁没有一处
进过上游，直接换等于把实机验证过的行为退回没验证过的。要升级：diff 新旧上游，人工把
补丁重新打上，在 R6 上重测 `FOCUS` / `SHOOT` / `PAIR`，再提交。
