# 快门桥固件（XIAO ESP32-S3）

从 USB CDC 收命令行，用 BLE 冒充佳能无线遥控器按快门。

依赖 [`maxmacstn/ESP32-Canon-BLE-Remote`](https://github.com/maxmacstn/ESP32-Canon-BLE-Remote) —— 它是个**库不是固件**，只管怎么用 BLE 跟佳能说话，什么时候按快门归调用方管（它自带的 example 里调用方是一个物理按钮的 GPIO 中断）。本固件把那个决定权交给主机。

主机侧对应实现在 `backend/shutter/esp32.py` 和 `backend/shutter/protocol.py`，两边协议必须一致。

## 烧录

```bash
cd firmware/esp32-shutter
pio run                   # 已实测通过：RAM 13.5%、Flash 27.0%
pio run -t upload
pio device monitor        # 应立刻看到 READY 1.0.0
```

第一次 `pio run` 要拉 250 MB 的 Arduino core 2.0.17。**如果 `Downloading` 卡在个位数百分比不动**，那是 `dl.registry.platformio.org` 从这边只有 ~10 kB/s（GitHub 大约 850 kB/s）。绕过去：

```bash
curl -L -C - -o esp32-2.0.17.zip \
  https://github.com/espressif/arduino-esp32/releases/download/2.0.17/esp32-2.0.17.zip
unzip -q esp32-2.0.17.zip
# PlatformIO 认的版本号是 3.20017.0，不是 2.0.17；两个文件都要改/写
python3 -c "import json,pathlib; p=pathlib.Path('esp32-2.0.17/package.json'); d=json.loads(p.read_text()); d['version']='3.20017.0'; p.write_text(json.dumps(d))"
python3 -c "import json,pathlib; pathlib.Path('esp32-2.0.17/.piopm').write_text(json.dumps({'type':'tool','name':'framework-arduinoespressif32','version':'3.20017.0','spec':{'owner':'platformio','id':5495,'name':'framework-arduinoespressif32','requirements':None,'uri':None}}))"
mv esp32-2.0.17 ~/.platformio/packages/framework-arduinoespressif32@3.20017.0
```

`.piopm` 不能省 —— 只放 `package.json` 的话 PlatformIO 不认这个目录是已装包，会照旧去下载。

## 相机配对

1. 机身菜单 `无线通信设置 > 蓝牙功能` 设成 **「遥控」**（不是「智能手机」）。**不设这个配不上。**
2. 选「配对」，相机进入等待状态。
3. 界面（配置模式）按「配对相机」，或 `curl -X POST http://127.0.0.1:18790/api/shutter/pair`。板子扫 20 秒 —— 比主机的 30 秒超时短，因为扫完还要连接和写 NVS，扫满 30 秒会让成功的回执正好落在主机放弃之后。
   ```bash
   pio device monitor          # 也可以手打： #1 PAIR
   ```
   曾经这里写的是「后端没有暴露这一步的端点 —— 配对是一次性动作」。**那个前提是错的**：板子一重启就丢配对，`READY` 横幅存在的理由正是这个。当时的结果是唯一的恢复手段要开串口终端，而报告故障的那块屏幕自己干不了这件事。播放中会被 409 拒绝 —— 板子一次只答一条命令，30 秒的扫描会把后面的帧堵在那儿。
4. 配对信息（相机 MAC）存在板子的 NVS 里，掉电不丢。但**开机并不会自动连上** —— `init()` 只把地址读出来，真正的 BLE 连接由第一条 `FOCUS` / `SHOOT` 惰性建立。所以重启后的第一帧比之后的慢几秒，`STATUS` 在那之前如实回 `disconnected`。
5. 验证整条链路：`curl -X POST http://127.0.0.1:18790/api/shutter/test`。
   返回里 **`connected` 是 USB 那一段，`camera` 是 BLE 那一段** —— 只有后者能回答「按下去会不会真的拍」。没配对相机时 `ok: false`，不用烧一帧就知道。要连快门动作一起验就加 `?shoot=true`。

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
| `STATUS` | 回 `connected` / `disconnected` / `unpaired`，**三态**。主机侧是 `camera_connected()`（只有 `connected` 算真），`/api/shutter/test` 的 `camera` 字段 | 不会失败 |
| `PAIR` | 进入 BLE 配对，扫 20 秒，成功时把相机 MAC 一起回给主机。主机侧是 `POST /api/shutter/pair` | 没找到处于配对模式的相机 |
| `FOCUS` | 半按。没连上就先连 | 没配对过 / 连不上（睡着、关机、超距） |
| `SHOOT` | 全按。没连上就先连 | 同上 |

**三态不能压成两态。** `unpaired` 要一个人拿着相机走菜单，`disconnected` 是配好的板子在两次拍摄之间的常态、下一帧自己会好 —— 合成一句「没连上」会把人指到错的地方。

**id 回显是必须的。** 没有它，主机超时放弃后迟到的回包会被当成下一条命令的成功回执 —— 表现是「偶尔少一帧」，现场几乎查不出来。

**`READY` 是主动发的。** 它告诉主机板子重启过、BLE 配对已经丢了，主机不用轮询就知道。收到 `READY` 时若有在途命令，那条命令是**作废**而不只是迟到。

## 坑

- **`-D ARDUINO_USB_CDC_ON_BOOT=1` 不能少。** 没有它，`Serial` 走 UART0 的物理引脚而不是原生 USB 口。板子会枚举、主机能打开端口、每次写入都成功 —— 然后什么都收不到，整条链路上**没有任何一处报错**。这是最贵的调试。
- **`PING` 通不代表能拍。** 它只证明主机和板子之间通，相机可能睡着或没配对。`/api/shutter/test` 因此在 `PING` 之后还发一条 `STATUS`，两段链路分开报 —— 只查 `PING` 的自检会在什么都没配对的机器上报绿，而那正是它要抓的故障。
- **刚重启的板子做自检在 `disconnected` 分支不再报红。** 板子惰性建链，所以配好的相机在第一帧之前 `STATUS` 就是 `disconnected`。主机侧 `/api/shutter/test` 现在读到 `disconnected` 会补发一条 `FOCUS`（半按不烧帧，强制建链）再复读 `STATUS`；`unpaired` 才直接判红。这需要 `SimShutter` 也分清「配对过」和「连着」，所以没有顺手改 —— 现在改了。
- **同一个房间放两台机器，BLE 名字要改。** `platformio.ini` 的 `-D REBOT_BLE_NAME`（还有 `-D REBOT_PAIR_SCAN_SECONDS`、`-D REBOT_SERIAL_BAUD`）—— 两块板子叫同一个名字，相机会跟先看见的那块配上。波特率跟后端的 `REBOT_SHUTTER_BAUD` 必须一致，这一侧读不到环境变量，所以写在编译期。
- **佳能机身睡眠后重连要几秒。** 所以 `SHOOT` 的超时比 `PING` 长（6s vs 3s）。
- **超长的行整条丢弃**，不做截断处理 —— 截断后的命令可能正好解析成另一条合法命令。
- **`platform` 必须钉住。** 平台的 55.x 线上是 Arduino core 3.x，`BLEDevice::setEncryptionLevel` 在那里搬去了 `BLESecurity`，而佳能库的 `pair()` 里还调着旧的那个 —— 不钉版本就编不过，而且报错报在库自己的源码里，看上去像库坏了而不像 core 挪了位置。钉在 `espressif32@6.11.0`（2.0.x core 那条线的最后一版，对应 Arduino core 2.0.17，也是这个库写的时候面对的版本）。库本身也钉在 tag `#1.0.2` —— 上游是一个人的业余项目、没有发布节奏，BLE 写序列悄悄改一次，在这边的表现是帧突然不再被拍下来。要升级平台就得自己 vendor 一份打过补丁的库。
- **不要按 `isConnected()` 去挡 `SHOOT`。** 这曾经是这份固件里最贵的一个 bug：`init()` 不连接，只有 `trigger()` / `focus()` 才惰性连接，所以开机后 `isConnected()` 恒为假 —— 用它当闸门，等于把唯一能建立连接的那次调用挡在门外，板子于是**永远**连不上，每一帧都回 `ERR camera not connected`。现在挡的是「有没有配对过」（`getPairedAddressString()`），连接留给 `trigger()` 自己去做。
- **`SHOOT` 回 `OK` 不等于卡上多了一张。** 库把特征值写下去就返回真，不等机身回话；返回假只意味着**连不上**（睡着、关机、超距），不意味着相机拒绝。所以固件的失败文案是 `camera unreachable` 而不是「相机拒绝」—— 后者是这条链路根本观测不到的状态。真要确认，只能看卡。
