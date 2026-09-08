# Motion validation comparison

Both runs use the same headless scenarios, RS model and physics settings.
The baseline is the pre-fix recorded run; fixed-final is the post-fix run.

| Check | Baseline | Fixed-final |
|---|---:|---:|
| Later transition peak command speed | `2.398725 rad/s` | `0.249998 rad/s` |
| Later transition final arrival error | `0.000176 rad` | `0.000000414 rad` |
| 1 s gap reference jump | `0.554471 rad` | `0.001484 rad` |
| 1 s gap safety response | no latch | latch + hold + aborted executor |
| WAIT commands during 5 s pause | `0` | `500 / 500` arm commands / control ticks |
| WAIT final arrival error | `0.400000 rad`, deadline abort | `0.00000132 rad`, done |
| Partial feedback reference change | `0.2 rad` | `0 rad` |
| Retarget reference jump | `-0.011250 rad` | `0 rad` |
| Retarget velocity jump | not recorded | `0 rad/s` |

Reproduce the fixed run with:

```text
cd app && .venv/bin/python -m backend.validation \
  --output data/validation/fixed-final.json \
  --csv data/validation/fixed-final.csv --check
```

Evidence files:

- [baseline-final-model.json](./baseline-final-model.json)
- [fixed-final.json](./fixed-final.json)
- Final CSV SHA-256: `2957458b30079263417c808c719efd323fbedc5d72a47bb7e288ecdb5175efcd`

This validates the software command path and the optional physics model. It is
not a real-arm or firmware-in-the-loop result. The final default suite completed
with **537 passed, 2 skipped** because MuJoCo is not installed in that
environment; all 21 frontend contract cases ran. The opt-in physics suite
completed with **25 passed**.
