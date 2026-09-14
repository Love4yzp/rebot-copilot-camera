# 运动验证基线

经评审的基线是 [baseline-final-model.json](baseline-final-model.json)，由同一组确定性场景与最终校验通过的 MuJoCo 模型生成。对应的指令与实测曲线是 [baseline-final-model.csv](../../../app/data/validation/baseline-final-model.csv)。

命令：

```text
cd app && .venv/bin/python -m backend.validation \
  --output data/validation/baseline-final-model.json \
  --csv data/validation/baseline-final-model.csv
```

环境：git `176efb12d633f95bbabf982d13fd129d8c589061`，Python `3.11.15`，MuJoCo `3.3.7`，`implicitfast`，seed `0`，应用周期 `0.010 s`，物理步长 `0.001 s`。URDF SHA-256 为 `2012b5aa3b58878109cb9e3c5deef919a87bd09a67d561662b2904a30dd4397e`。CSV SHA-256 为 `f53173174e47271be8786dc44d9b55972b695faf6f0f0f0880ef0797de346e99`。

观测到的基线信号：

| 场景 | 观测 |
|---|---|
| later short transition | joint2 指令速度峰值 `2.3987 rad/s`；最终到位误差 `0.00018 rad`；阶段 `done` |
| 0.1 s 间隔 | 指令参考跳变 `0.0529 rad`；未触发闩锁 |
| 1.0 s 间隔 | 指令参考跳变 `0.5545 rad`；未触发闩锁 |
| 转场 WAIT 在 0.5、暂停 5 s | WAIT 期间零指令；恢复以超时中止结束；最终误差 `0.4000 rad` |
| 部分目标 + 反馈噪声 | joint3 参考变化 `0.2 rad` |
| 运动中改向 | 参考跳变 `-0.01125 rad`；实际边界 q 变化 `0.00154 rad`；实际 dv 单独记录 |

所有记录的物理采样均为有限值。接触与力矩饱和采样包含在 JSON 物理运行摘要中。