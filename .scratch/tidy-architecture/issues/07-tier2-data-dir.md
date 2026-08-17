# 07 档 2 数据归位

Type: task
Status: open
Blocked by: 01

## Question

执行档 2：运行时数据目录归入 `data/`。

**清单**：

1. `.gitignore`：`/poses/ /sequences/ /templates/ /routines/` 四条换成 `/data/`（routines 已在 06 删除）。
2. `backend/config.py`：`POSES_DIR` / `SEQUENCES_DIR` / `TEMPLATES_DIR` 默认值改为 `data/` 下；环境变量简化为单一 `REBOT_DATA_DIR`（未上线，不做旧名兼容）。README 与 deploy unit 的环境变量说明同步。
3. `deploy/rebot-copilot-camera.service` 与 `device.sh` 中的数据目录路径同步（设备未部署，直接改，无需迁移）。
4. 检查 `backend/sequences/store.py`、`seed_demo.py` 等所有消费方只走 config.py 默认值，无硬编码根目录路径。
5. 本地把 `poses/ sequences/` 现有数据 `mv` 进 `data/`（未跟踪文件，不入 commit）。
6. PROGRESS.md 同 commit 更新。

**验收**：`uv run pytest` 全绿；`./dev.sh prod --sim` 能起后，`data/{poses,sequences,templates}` 自动创建且 store 读写正常；根目录不再出现 `poses/ sequences/ templates/`。
