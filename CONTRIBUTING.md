# 贡献指引

这份文档只负责**一次改动如何完成**。第一次接手先读 [`docs/START_HERE.md`](./docs/START_HERE.md)；修改约束以 [`AGENTS.md`](./AGENTS.md) 为准；当前状态以 [`PROGRESS.md`](./PROGRESS.md) 为准。

## 开工

1. 读 `AGENTS.md`，再按其中的触发条件读取专项文档。
2. 读 `PROGRESS.md` 的当前状态。
3. 用 `git log -- <path>` 和相关测试确认现有决定的理由。
4. 检查 `git status --short`，保留不属于本次工作的改动。
5. 子模块缺失时运行 `git submodule update --init`。

所有 Python 和 `uv` 命令都在 `app/` 下执行。开发机后端由人通过 `./dev.sh` 启动；agent 用测试验证，不启动服务、不占用 18790。

## 修改

- 一次提交只做一种变化：结构搬移与行为变化分开。
- 外部行为或契约变化先补会失败的行为测试；测试观察结果，不绑定内部实现。
- 修改 API 响应或 normalize 规则时，同步更新 Python、TypeScript mock 和 `app/contract/cases/`。
- 状态确实变化时，同一提交更新 `PROGRESS.md`；历史留给 git，不写 changelog 文档。
- README 的用户操作发生变化时，同步更新中英文版本。

## 验证

后端改动：

```bash
cd app
uv run pytest
uvx ruff check backend tests
```

前端改动再运行：

```bash
cd app/frontend
npm run build
```

前后端契约改动优先运行：

```bash
cd app
uv run pytest tests/test_contract.py tests/test_cross_lang_constants.py
```

每个提交结束时，适用的验证必须通过。无法运行的检查在交接中写清原因和未验证范围。

## 提交

commit message 说明**为什么改**，特别是偏离原设计或接受风险的理由。提交前复查：

- 没有带入无关工作树改动；
- 没有把运行数据、密钥或设备地址提交进仓库；
- 文档事实仍各自在唯一来源中；
- `PROGRESS.md` 只记录现在，不记录本轮过程。
