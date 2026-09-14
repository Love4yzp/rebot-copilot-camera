# 运动验证对比

两次运行使用同一组无界面场景、RS 模型与物理设置。基线是修复前的记录运行；fixed-final 是修复后的运行。

| 检查项 | 基线 | 修复后最终版 |
|---|---:|---:|
| 后续转场指令速度峰值 | `2.398725 rad/s` | `0.249998 rad/s` |
| 后续转场最终到位误差 | `0.000176 rad` | `0.000000414 rad` |
| 1 s 间隔参考跳变 | `0.554471 rad` | `0.001484 rad` |
| 1 s 间隔安全响应 | 未触发闩锁 | 闩锁 + 保持 + 中止执行器 |
| 5 s 暂停期间 WAIT 指令 | `0` | `500 / 500` 条臂指令 / 控制 tick |
| WAIT 最终到位误差 | `0.400000 rad`，超时中止 | `0.00000132 rad`，done |
| 部分反馈参考变化 | `0.2 rad` | `0 rad` |
| 改向参考跳变 | `-0.011250 rad` | `0 rad` |
| 改向速度跳变 | 未记录 | `0 rad/s` |

复现修复后的运行：

```text
cd app && .venv/bin/python -m backend.validation \
  --output data/validation/fixed-final.json \
  --csv data/validation/fixed-final.csv --check
```

证据文件：

- [baseline-final-model.json](./baseline-final-model.json)
- [fixed-final.json](./fixed-final.json)
- 最终版 CSV SHA-256：`2957458b30079263417c808c719efd323fbedc5d72a47bb7e288ecdb5175efcd`

这验证的是软件指令路径与可选物理模型，不是真机或固件在环的结果。当时默认测试套件以 **537 passed, 2 skipped** 完成（该环境未安装 MuJoCo），全部 21 个前端契约用例都已运行；可选物理套件以 **25 passed** 完成。