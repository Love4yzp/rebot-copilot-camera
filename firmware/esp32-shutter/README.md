# esp32-shutter

XIAO ESP32-S3 固件。从 USB CDC 收命令行，用 BLE 冒充佳能无线遥控器按快门。

依赖 [`maxmacstn/ESP32-Canon-BLE-Remote`](https://github.com/maxmacstn/ESP32-Canon-BLE-Remote) —— 它是个**库不是固件**，只管怎么用 BLE 跟佳能说话，什么时候按快门归调用方管（它自带的 example 里调用方是一个物理按钮的 GPIO 中断）。本固件把那个决定权交给主机。

主机侧对应实现在 `backend/shutter/esp32.py` 和 `backend/shutter/protocol.py`，两边协议必须一致。

## 烧录

```bash
cd firmware/esp32-shutter
pio run -t upload
pio device monitor        # 应立刻看到 READY 1.0.0
```

## 相机配对

1. 机身菜单 `无线通信设置 > 蓝牙功能` 设成 **「遥控」**（不是「智能手机」）。**不设这个配不上。**
2. 选「配对」，相机进入等待状态。
3. 主机发 `PAIR`（板子扫 30 秒）。后端没有暴露这一步的端点 —— 配对是一次性动作，直接串口发就行：
   ```bash
   pio device monitor          # 然后手打： #1 PAIR
   ```
4. 配对信息存在板子上，之后上电自动重连。
5. 验证整条链路：`curl -X POST 'http://127.0.0.1:18790/api/shutter/test?shoot=true'`。
   **注意 `/api/shutter/test` 不带 `?shoot=true` 时只发 `PING`** —— 那只证明主机和板子之间通，不证明相机能拍。

## 协议

```
主机 -> 板子:  #<id> <COMMAND>\n
板子 -> 主机:  #<id> OK\n
              #<id> ERR <reason>\n
板子 -> 主机:  READY <version>\n     ← 开机主动发，不需要请求
```

| 命令 | 含义 | 失败情形 |
|---|---|---|
| `PING` | 主机↔板子链路是否活着。**不查相机** —— 相机睡着时这个问题仍要能回答 | 不会失败 |
| `STATUS` | 回 `connected` / `disconnected` | 不会失败 |
| `PAIR` | 进入 BLE 配对，扫 30 秒 | 没找到处于配对模式的相机 |
| `FOCUS` | 半按 | 相机未连接 / 相机拒绝 |
| `SHOOT` | 全按 | 相机未连接 / 相机拒绝 |

**id 回显是必须的。** 没有它，主机超时放弃后迟到的回包会被当成下一条命令的成功回执 —— 表现是「偶尔少一帧」，现场几乎查不出来。

**`READY` 是主动发的。** 它告诉主机板子重启过、BLE 配对已经丢了，主机不用轮询就知道。收到 `READY` 时若有在途命令，那条命令是**作废**而不只是迟到。

## 坑

- **`-D ARDUINO_USB_CDC_ON_BOOT=1` 不能少。** 没有它，`Serial` 走 UART0 的物理引脚而不是原生 USB 口。板子会枚举、主机能打开端口、每次写入都成功 —— 然后什么都收不到，整条链路上**没有任何一处报错**。这是最贵的调试。
- **`PING` 通不代表能拍。** 它只证明主机和板子之间通，相机可能睡着或没配对。要验证整条链路用 `POST /api/shutter/test?shoot=true`。
- **佳能机身睡眠后重连要几秒。** 所以 `SHOOT` 的超时比 `PING` 长（6s vs 3s）。
- **超长的行整条丢弃**，不做截断处理 —— 截断后的命令可能正好解析成另一条合法命令。
- 相机拒绝（`ERR ... rejected by camera`）和未连接（`ERR camera not connected`）是两回事：前者相机在但不肯拍（比如对焦没合上），后者整条 BLE 链路断了，接下来每一帧都会同样失败。
