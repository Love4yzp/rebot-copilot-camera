# Motion validation baseline

The reviewed baseline is [baseline-final-model.json](baseline-final-model.json),
generated from the same deterministic scenarios and the final validated MuJoCo
model. The corresponding command and measured curves are
[baseline-final-model.csv](../../../app/data/validation/baseline-final-model.csv).

Command:

```text
cd app && .venv/bin/python -m backend.validation \
  --output data/validation/baseline-final-model.json \
  --csv data/validation/baseline-final-model.csv
```

Environment: git `176efb12d633f95bbabf982d13fd129d8c589061`, Python `3.11.15`,
MuJoCo `3.3.7`, `implicitfast`, seed `0`, application period `0.010 s`, and
physics step `0.001 s`. URDF SHA-256 is
`2012b5aa3b58878109cb9e3c5deef919a87bd09a67d561662b2904a30dd4397e`.
The CSV SHA-256 is
`f53173174e47271be8786dc44d9b55972b695faf6f0f0f0880ef0797de346e99`.

Observed baseline signals:

| Scenario | Observation |
|---|---|
| later short transition | peak sampled joint2 command speed `2.3987 rad/s`; final arrival error `0.00018 rad`; phase `done` |
| 0.1 s gap | command reference jump `0.0529 rad`; no latch |
| 1.0 s gap | command reference jump `0.5545 rad`; no latch |
| transition WAIT at 0.5, pause 5 s | zero commands during WAIT; resume ends with deadline abort; final error `0.4000 rad` |
| partial target + feedback noise | joint3 reference changed `0.2 rad` |
| mid-move retarget | reference jump `-0.01125 rad`; actual boundary q delta `0.00154 rad`; actual dv is recorded separately |

All recorded physical samples were finite. Contact and torque saturation
samples are included in the JSON physical run summaries.
