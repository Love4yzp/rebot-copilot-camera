# 插件:第三层的落地

**这份文档写给要扩展这台机器的开发者。**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 的三个不变量里,插件是第三层 —— 内核是地基,交互骨架是承重结构,插件是装修。骨架正向时间轴编辑器演进(设计定稿见 [`TIMELINE.md`](./TIMELINE.md)),插件体系原样继承 —— 动作的家从 waypoint 的动作列表搬到时间轴的块内标记,扩展点不变。

概念定义在 ARCHITECTURE.md,交互约束在 TIMELINE.md,改代码的铁律在 [`AGENTS.md`](../AGENTS.md)。这里只讲扩展点。

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

块内的 `markers` 是**有序列表**,executor 按序触发。把继电器标记排在快门标记前面就是 pre,后面就是 post。零新机制,编辑面板里拖动标记就是全部界面。

真正没有的只有「臂动**之前**」那一种,而那一种正是不能做的。

---

## 一、动作插件

### 完整例子

**[`examples/rebot-plugin-turntable/`](../examples/rebot-plugin-turntable/) 是一个真的包,不是文档里的代码块** —— 它装在开发环境里(`pyproject.toml` 的 `[tool.uv.sources]`),`tests/test_plugin_packaging.py` 通过**真的 `importlib.metadata` 扫描**拿到它。只被引用在散文里的打包元数据是没人跑过的元数据:group 名拼错、工厂要参数、`module:Class` 解析不出来,三种写法都会让插件干脆不出现,而第一个发现的人会是照着这份文档在设备上装的人。

转台,一个文件,串口。骨架:

```python
# src/rebot_plugin_turntable/__init__.py
class TurntableParams(BaseModel):
    degrees: float = Field(default=45.0, ge=-180, le=180)


class TurntableProvider:
    id = "turntable"          # 存进序列 JSON(标记的 kind)。改名会让所有用到它的标记变孤儿
    label = "转台"             # 界面上显示,可本地化
    params_model = TurntableParams
    retryable = False         # 转角是相对的:重跑一次是另一个姿态,不是同一个重试

    def fields(self) -> list[FieldSpec]:
        return [FieldSpec(key="degrees", kind="tiers", label="转角",
                          values=[15, 30, 45, 90], unit="°", default=45)]

    def probe(self) -> None:
        """自检。启动时与刷新时会调 —— 要便宜、无副作用,且**不能挂死**(见契约表)。"""
        if not self._exchange("PING").startswith("OK"):
            raise ActionUnavailable(f"turntable on {self._port} did not answer")

    def run(self, params: TurntableParams, ctx: ActionContext) -> None:
        reply = self._exchange(f"ROT {params.degrees:g}")   # 阻塞。这里是 worker 线程
        if not reply.startswith("OK"):
            raise ActionError(f"turntable refused: {reply!r}")
        ctx.emit("turntable.rotated", {"degrees": params.degrees})
```

```toml
# pyproject.toml —— 让它成为插件的就是这一段
[project.entry-points."rebot.actions"]
turntable = "rebot_plugin_turntable:TurntableProvider"
```

entry point 指向的东西必须**无参可调**。`TURNTABLE_PORT=sim` 时例子走一个进程内的模拟转台 —— 跟 `SimArm` / `SimShutter` 同一套哲学,插件作者的第一天不该需要那个配件。

装到设备上:

```bash
uv pip install ./rebot-plugin-turntable
sudo systemctl restart rebot-copilot-camera
```

完事。**宿主零改动,前端零改动。** 转台出现在 `GET /api/plugins`、出现在标记检查器、可以和快门排先后。

宿主不管插件配置 —— 一管就要为每个插件定义配置 schema,那是没边的;插件自己读环境变量或自己的文件。

### 无硬件开发循环

```bash
uv run -m backend.actions.check                          # 列出装了什么
uv run -m backend.actions.check turntable                # 看它的 manifest
uv run -m backend.actions.check turntable --probe        # 跑自检
uv run -m backend.actions.check turntable --run '{"degrees": 90}'
```

最后一条最有用:按 provider 自己的模型校验参数、造一个真的 `ActionContext`、走**真的 `ThreadedRunner`** 带**真的超时**跑一次。挂死、乱抛异常、无视参数,都在这里暴露,而不是在机位上、被摄体等着的时候。

跟 `SimArm` / `SimShutter` 是同一套哲学 —— 无硬件循环是基础设施不是便利品。插件作者的第一天不该需要一条 48V 的臂。

### 契约:违约了会怎样

**宿主默认假设第三方代码会阻塞、会崩、会挂死。** 隔离是宿主的责任,不靠插件作者守规矩 —— 一份依赖第三方自觉的契约不是契约。

| 你干的事 | 宿主的反应 |
|---|---|
| `run()` 阻塞 6 秒 | 完全正常。控制循环一格没停 —— 每个 provider 一条 worker 线程,executor 每 tick 轮询 |
| `run()` 挂死不返回 | 到 `timeout_s` 报 `ActionTimeout`(**读侧**判定,Python 杀不掉线程);orphan 线程随它去;**该 provider 停用到线程返回**,其他 provider 不受影响 |
| 抛了不是 `ActionError` 的异常 | 原样传给 executor,日志带 traceback,照走 `on_failure` |
| `probe()` 崩 | 标 `available: false` + 原因。**服务照常起来**,插件在 `/api/plugins` 里列着,界面虚线灰显带原因 |
| `probe()` 挂死 | 跟 `run()` 一样走 worker 线程和读侧超时(`PROBE_TIMEOUT_S`,5 秒)。**自检不跑在请求线程上** —— 否则一个挂死的自检会连带卡住 `/api/plugins`、刷新端点和臂动之前的预检 |
| `probe()` 期间正在跑动作 | 跳过这次自检,保留上一次结论。接了活的 provider 按定义就是够得着的,不该因为忙而被标成坏 |
| import 时就崩 | 标 `installed: false` + 原因。一个坏插件不能让整台机器起不来 |
| 少写了 `id` / `params_model` / 某个方法 | 同上,**注册前就被拒**,原因写给插件作者看。宿主只看属性不调方法 —— 为了决定要不要接受第三方代码而先去跑第三方代码,正是一个拼错的属性变成起不来的服务的路径 |
| `id` 跟已有的重名 | **拒绝,并出声**。id 是存储的动作指认 provider 的方式,而 executor 把每个 `ShutterAction` 发给字面量 `shutter` —— 顶掉这个 id 的插件会静默变成那台相机 |
| `fields()` 崩 | 只赔上自己:该 provider `available: false` 带原因,**其余插件照常出现在编辑面板**。manifest 装着所有人,一个异常漏出去就是整张面板空白 |
| 想拿 `arm` 去动臂 | `ActionContext` 里没有。**拿不到** |
| 副作用不可重试 | 声明 `retryable = False`,宿主把 retry 降级为 abort 并出声 |

`installed` 与 `available` 是两件事:`installed: false` 是宿主**根本没拿到**这个 provider(加载失败、形状不对、重名被拒),没有 `params_model` 可校验,所以编辑面板不提供添加,但**仍然列出来带原因** —— 消失了操作员会以为是自己配错了。`available: false` 是拿到了但此刻不可用(自检没过),**可以先排班后插配件**,拦在播放前的预检。

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

三道,都为了让错误离开执行阶段(臂已到位、被摄体在等):

1. **写入时** —— `PATCH /api/sequences/{id}` 按 provider 的 `params_model` 校验每个标记,不合法 400。pydantic 自己做不到,只有 provider 知道形状
2. **播放前** —— `execute` / `goto` 检查 provider 装没装、可用不可用,不合法 400 且**臂一动没动**
3. **执行时** —— executor 再校验一次。序列会比写它的那版插件活得久

### 配件掉了又插回来

`available: false` 的行在编辑面板里灰显带原因,旁边有「重新检测配件」—— 打 `POST /api/plugins/probe`,重跑所有自检。没有它,从灰显恢复的唯一办法是重启服务,而那在棚里意味着去找一台终端。自检不动关节不烧帧,所以**不挂运动闸门**。

装了哪些插件不会在页面运行期间变(装包 + 重启),但配件答不答应会变 —— 所以刷新的是健康,不是清单。

---

## 二、触发源(不是插件)

`POST /api/poses/{id}/goto` 与 `POST /api/sequences/{id}/execute` 就是触发端点。点位姿卡 = 前端打 goto;点执行 = 前端打 execute。别的触发源做同一件事:

```python
r = requests.post(
    f"{BASE}/api/poses/{pose_id}/goto",
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
| `sequence.started` / `.done` / `.aborted` | 一轮的起止 |
| `pose.arrived` | 臂到位并保持 —— **集成方通常要的就是这个**,场景此刻就是位姿说的样子 |
| `action.started` / `.done` / `.failed` | 每个动作(连拍时每帧一次) |
| `estop.engaged` / `.cleared` | 闩锁跳变,不是每 tick |
| `teach.captured` | 手动录了一个位姿 |
| provider 自定义 | `ctx.emit(...)`,如 `shutter.fired` |

三条硬规则:

1. **单向,永不否决。** 没有返回值能改变流程。能否决的钩子就是第三方代码进了安全路径
2. **有界队列,丢旧包。** 慢订阅者丢消息,不反压控制线程 —— 因订阅者不读而停摆的循环,就是停止撑住臂的循环
3. **在知道事实的地方发。** sequence / pose / action 从 executor 发;急停从控制循环看闩锁跳变发(**不从 `SafetyLatch` 发** —— 它是纯逻辑,给它一个 broadcaster 就是在那堵墙上开第一个洞);`teach.captured` 从 HTTP 端点发,控制循环看不见手动录点

`/ws` 与 `/api/events` 是两条流,因为回答的是两个问题:屏幕要 20Hz 关节角,集成方不要,更不该为此在棚里的网线上吃一条位置流。

**不做 webhook。** 服务器主动发 HTTP 意味着重试、超时、投递语义,还是在一台离线设备上。要 webhook 就在进程外写个二十行的 WS→HTTP 桥 —— 那正好是想要的隔离。

---

## 刻意不做

- **pre-hook**(能否决运动的钩子 = 第三方代码进安全路径)
- **条件 / 分支规则引擎** —— 工作流是线性的:到位、稳定、触发、下一个。论证在 `backend/sequences/models.py` 开头
- **插件间通信** —— 两个插件要说话,说明它们该是一个插件
- **热重载** —— 现场设备重启服务,比调试半加载状态便宜

---

## 代码地图

```
backend/actions/
  base.py       ActionProvider Protocol / ActionContext / FieldSpec / 异常
  runner.py     ThreadedRunner(每 provider 一条 worker,action 与 probe 同一条队列)
                + InlineRunner(测试用)
  registry.py   entry_points 发现 + check_shape 形状闸门 + 健康状态 + manifest
  validate.py   写入时与播放前的两道校验
  shutter.py    ShutterProvider —— 第一个 provider,包 ShutterDriver
  check.py      插件作者的命令行
backend/core/events.py   事件名与信封
backend/api/plugins.py   GET /api/plugins、POST /api/plugins/probe
examples/rebot-plugin-turntable/   可安装的完整例子(装在开发环境里,被真实发现测试用到)
tests/test_plugin_packaging.py     唯一走真 entry point 的测试:证明打包元数据本身是对的
```

`backend/shutter/` 是**驱动**,不是插件层:`/api/shutter/test` 直接跟它对话检查链路,那和执行一个动作是两个问题。
