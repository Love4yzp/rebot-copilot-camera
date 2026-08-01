# 插件:第三层的落地

**这份文档写给要扩展这台机器的开发者。**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 的三个不变量里,插件是第三层 —— 内核是地基,交互骨架是承重结构,插件是装修。骨架已经立住了([`INTERACTION.md`](./INTERACTION.md)),这份讲怎么往上装。

概念定义在 ARCHITECTURE.md,交互约束在 INTERACTION.md,改代码的铁律在 [`AGENTS.md`](../AGENTS.md)。这里只讲扩展点。

---

## 先分岔:三个扩展点,不是一个

「插件系统」听起来是一件事,实际是三件,方向不同、生命周期不同、失败语义不同、安全等级不同。**合成一条通用 pre/post hook 链,等于让每个扩展点都拿到最危险那个的权限。**

| 你想干的事 | 写什么 | 装在哪 |
|---|---|---|
| 臂到位后做点事,**流程要等它做完** | **动作插件** | 进程内,`uv pip install` |
| 决定什么时候让臂去哪 | HTTP 客户端 | 进程外,任何语言 |
| 事情发生后被通知,**流程不等** | WebSocket 客户端 | 进程外,任何语言 |

只有第一种需要把代码装进这个进程。另外两种是普通客户端 —— 这是刻意的,见下面「为什么触发源不是插件」。

### 「前插一个、后插一个」大多已经有了

`waypoint.actions` 是**有序列表**,executor 按序执行。把继电器排在快门前面就是 pre,后面就是 post。零新机制,编辑面板里两个上下箭头就是全部界面。

真正没有的只有「臂动**之前**」那一种,而那一种正是不能做的。

---

## 一、动作插件

### 完整例子

转台,通过串口。四十行,没有第二个文件。

```python
# src/rebot_plugin_turntable/__init__.py
import os
from pydantic import BaseModel, Field
from backend.actions import ActionContext, ActionError, ActionUnavailable, FieldSpec


class TurntableParams(BaseModel):
    degrees: float = Field(default=45.0, ge=-180, le=180)


class TurntableProvider:
    id = "turntable"          # 存进 routine JSON。改名会让所有用到它的锚点变孤儿
    label = "转台"             # 界面上显示,可本地化
    params_model = TurntableParams
    retryable = True          # 失败后重跑安全吗?见下

    def __init__(self) -> None:
        self._port = os.environ.get("TURNTABLE_PORT", "/dev/rebot-turntable")

    def fields(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="degrees", kind="tiers", label="转角",
                      values=[15, 30, 45, 90], unit="°", default=45),
        ]

    def probe(self) -> None:
        """自检。启动时与刷新时会调 —— 要便宜、无副作用。"""
        try:
            self._open().write(b"PING\n")
        except OSError as exc:
            raise ActionUnavailable(f"turntable unreachable on {self._port}: {exc}") from exc

    def run(self, params: TurntableParams, ctx: ActionContext) -> None:
        link = self._open()
        link.write(f"ROT {params.degrees}\n".encode())
        reply = link.readline()          # 阻塞。没关系,这里是 worker 线程
        if not reply.startswith(b"OK"):
            raise ActionError(f"turntable refused: {reply!r}")
        ctx.emit("turntable.rotated", {"degrees": params.degrees})
```

```toml
# pyproject.toml
[project]
name = "rebot-plugin-turntable"
dependencies = ["pyserial", "pydantic"]

[project.entry-points."rebot.actions"]
turntable = "rebot_plugin_turntable:TurntableProvider"
```

装到设备上:

```bash
uv pip install ./rebot-plugin-turntable
sudo systemctl restart rebot-copilot-camera
```

完事。**宿主零改动,前端零改动。** 转台出现在 `GET /api/plugins`、出现在锚点编辑面板、可以和快门排先后。

entry point 指向的东西必须**无参可调**(类或工厂函数)。宿主不管插件配置 —— 一管就要为每个插件定义配置 schema,那是没边的;插件自己读环境变量或自己的文件。

### 无硬件开发循环

```bash
uv run -m backend.actions.check                          # 列出装了什么
uv run -m backend.actions.check turntable                # 看它的 manifest
uv run -m backend.actions.check turntable --probe        # 跑自检
uv run -m backend.actions.check turntable --run '{"degrees": 90}'
```

最后一条最有用:按 provider 自己的模型校验参数、造一个真的 `ActionContext`、走**真的 `ThreadedRunner`** 带**真的超时**跑一次。挂死、乱抛异常、无视参数,都在这里暴露,而不是在锚点前面、被摄体等着的时候。

跟 `SimArm` / `SimShutter` 是同一套哲学 —— 无硬件循环是基础设施不是便利品。插件作者的第一天不该需要一条 48V 的臂。

### 契约:违约了会怎样

**宿主默认假设第三方代码会阻塞、会崩、会挂死。** 隔离是宿主的责任,不靠插件作者守规矩 —— 一份依赖第三方自觉的契约不是契约。

| 你干的事 | 宿主的反应 |
|---|---|
| `run()` 阻塞 6 秒 | 完全正常。控制循环一格没停 —— 每个 provider 一条 worker 线程,executor 每 tick 轮询 |
| `run()` 挂死不返回 | 到 `timeout_s` 报 `ActionTimeout`(**读侧**判定,Python 杀不掉线程);orphan 线程随它去;**该 provider 停用到线程返回**,其他 provider 不受影响 |
| 抛了不是 `ActionError` 的异常 | 原样传给 executor,日志带 traceback,照走 `on_failure` |
| `probe()` 崩 | 标 `available: false` + 原因。**服务照常起来**,插件在 `/api/plugins` 里列着,界面虚线灰显带原因 |
| import 时就崩 | 同上。一个坏插件不能让整台机器起不来 |
| 想拿 `arm` 去动臂 | `ActionContext` 里没有。**拿不到** |
| 副作用不可重试 | 声明 `retryable = False`,宿主把 retry 降级为 abort 并出声 |

**为什么 `ActionContext` 里没有臂**:让错的事**够不到**,而不只是禁止。这和「闩锁不进 executor」是同一个手法 —— 执行器结构上不可能自己决定从急停恢复,插件结构上不可能绕过运动闸门。

**为什么连拍不在 provider 里循环**:provider 内部的循环打不断,整个连拍会在急停被注意到之前打完。重复次数与间隔由宿主编排(`_Dispatch`),急停因此落在**帧与帧之间**。

**`retryable = False` 用在哪**:闪光没回电、料已经放出去、一次性触发。一个被默默忽略的 retry 设置,比诚实拒绝或诚实执行都糟。

### 界面

**声明式 manifest,宿主渲染控件。不接受插件提供的 JS/HTML。**

`fields()` 返回 `FieldSpec` 列表,只有三种 `kind`:

| kind | 控件 | 参数 |
|---|---|---|
| `switch` | 开关 | — |
| `stepper` | 加减步进 | `min` / `max` |
| `tiers` | 档位单选 | `values` / `unit` |

只有三种,是契约的一部分:它们正好是编辑面板本来就实现的三个控件,已经过了 ≥44px 触控目标、`:focus-visible`、`prefers-reduced-motion` 那一轮。想要第四种 = 改宿主,**这是故意的门槛**。

条件显示用 `when={"key": "count", "min": 2}` —— 结构化,不是要解析的表达式串。让界面 eval 一个装上来的包给的表达式是麻烦的开始。

不让插件带 UI 的第二个理由更硬:**颜色在这台机器上是状态通道**,四个彩色各自独占一个机器状态(见 AGENTS.md)。插件一带样式,第一件事就是给自己按钮上个品牌色,而那正是要避免的 —— 颜色一旦兼职装饰,操作者就没法靠余光判断臂在不在动。

### 参数在哪校验

三道,都为了让错误离开 ACTING 阶段(臂已在锚点、被摄体在等):

1. **写入时** —— `POST/PATCH .../waypoints` 按 provider 的 `params_model` 校验,不合法 400。pydantic 自己做不到,只有 provider 知道形状
2. **播放前** —— `play` / `goto` 检查 provider 装没装、可用不可用,不合法 400 且**臂一动没动**
3. **执行时** —— executor 再校验一次。routine 会比写它的那版插件活得久

---

## 二、触发源(不是插件)

`POST /api/routines/{rid}/waypoints/{index}/goto` 就是触发端点。点卡片 = 前端打这个。别的触发源做同一件事:

```python
r = requests.post(
    f"{BASE}/api/routines/{rid}/waypoints/2/goto",
    json={"source": "footswitch"},
)
# 409 = 急停中,或臂正忙
```

`source` **不授予任何权限**,只是个标签,进日志、进 `/api/control`、进 WS。存在的唯一理由:一台能被卡片 / agent / 脚踏 / 脚本多路触发的机器上,「臂刚才为什么动了」是第一个被问、事后又最答不出来的问题。

现成的触发路径:

| 来源 | 走什么 |
|---|---|
| 点卡片 | 前端打 goto |
| 数字键 1–9 / 脚踏 | 已映射,HID 发数字即可 |
| Agent / LLM | `/api/agent/*`,租约 + token,OpenAPI 可直接导成 tool |
| 硬件急停按钮 | `POST /api/estop`,带 `source` 字段 |
| 外部系统 | 同一套 REST |

### 为什么触发源不是插件

**触发源永远是运动闸门的客户端,不在闸门之上。**

一个能否决或改写运动的 pre-hook,等于把第三方代码放进「臂动不动」的决策路径,`require_arm_available` 就绕过去了 —— 而 `tests/test_motion_gate.py` 遍历路由表的全部意义,就是让「新增运动入口」必须是一个显式决定。

所以触发源的能力上限就是 REST 的能力上限:急停拒绝界面,就同样拒绝它。要独占控制走 `/api/agent/acquire`,而站在臂边的人可以 `force` 收回。

---

## 三、事件订阅(也不是插件)

```python
async with websockets.connect("ws://127.0.0.1:18790/api/events") as ws:
    async for raw in ws:
        e = json.loads(raw)
        if e["event"] == "action.done" and e["data"]["provider"] == "shutter":
            push_to_asset_manager(e["data"])
```

事件表(权威定义在 `backend/core/events.py`):

| 事件 | 什么时候 |
|---|---|
| `routine.started` / `.done` / `.aborted` | 一轮的起止 |
| `anchor.arrived` | 臂到位并保持 —— **集成方通常要的就是这个**,场景此刻就是锚点说的样子 |
| `action.started` / `.done` / `.failed` | 每个动作(连拍时每帧一次) |
| `estop.engaged` / `.cleared` | 闩锁跳变,不是每 tick |
| `teach.captured` | 手动录了一个点 |
| provider 自定义 | `ctx.emit(...)`,如 `shutter.fired` |

三条硬规则:

1. **单向,永不否决。** 没有返回值能改变流程。能否决的钩子就是第三方代码进了安全路径
2. **有界队列,丢旧包。** 慢订阅者丢消息,不反压控制线程 —— 因订阅者不读而停摆的循环,就是停止撑住臂的循环
3. **在知道事实的地方发。** routine / anchor / action 从 executor 发;急停从控制循环看闩锁跳变发(**不从 `SafetyLatch` 发** —— 它是纯逻辑,给它一个 broadcaster 就是在那堵墙上开第一个洞);`teach.captured` 从 HTTP 端点发,控制循环看不见手动录点

`/ws` 与 `/api/events` 是两条流,因为回答的是两个问题:屏幕要 20Hz 关节角,集成方不要,更不该为此在棚里的网线上吃一条位置流。

**不做 webhook。** 服务器主动发 HTTP 意味着重试、超时、投递语义,还是在一台离线设备上。要 webhook 就在进程外写个二十行的 WS→HTTP 桥 —— 那正好是想要的隔离。

---

## 刻意不做

- **pre-hook**(能否决运动的钩子 = 第三方代码进安全路径)
- **条件 / 分支规则引擎** —— 工作流是线性的:到位、稳定、触发、下一个。论证在 `backend/routines/models.py` 开头
- **插件间通信** —— 两个插件要说话,说明它们该是一个插件
- **热重载** —— 现场设备重启服务,比调试半加载状态便宜

---

## 代码地图

```
backend/actions/
  base.py       ActionProvider Protocol / ActionContext / FieldSpec / 异常
  runner.py     ThreadedRunner(每 provider 一条 worker)+ InlineRunner(测试用)
  registry.py   entry_points 发现 + 健康状态 + manifest
  validate.py   写入时与播放前的两道校验
  shutter.py    ShutterProvider —— 第一个 provider,包 ShutterDriver
  check.py      插件作者的命令行
backend/core/events.py   事件名与信封
backend/api/plugins.py   GET /api/plugins、POST /api/plugins/probe
```

`backend/shutter/` 是**驱动**,不是插件层:`/api/shutter/test` 直接跟它对话检查链路,那和执行一个动作是两个问题。
