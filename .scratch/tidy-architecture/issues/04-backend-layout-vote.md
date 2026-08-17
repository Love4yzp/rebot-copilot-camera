# 04 backend 物理重排投票

Type: grilling
Status: resolved
Blocked by: 10

## Question

看完 10 票画出的内核边界表后投票：`backend/` 目录是否物理重排为与分层对齐的四区。

**选项**：

- a) **保持现状**：目录不动，分层只活在文档（内核边界表 + AGENTS.md 标签）。零路径改动。
- b) **物理重排**：`backend/{kernel(arm, safety, controller), engine(executor, floatlock, broadcaster, events, sequences), api, plugins(actions, shutter)}` —— 50+ 文件搬家，全仓 import、AST 安全测试扫描范围、pyproject 打包路径同步改；护栏 = pytest 全绿 + 契约测试 + 运动闸门测试 + `./dev.sh prod --sim` 能起。

**裁决要求**：选 a 或 b。若选 b：08 票（档 3 架构手术）的落刀边界按新目录重写，且 08 重新入队。

## Answer

（2026-08-17，操作者「一步到位」委托，采纳推荐）选 **a：保持现状**。内核边界表已落地（10 票），分层显形在文档、目录不动 —— 零路径改动，50+ 文件的物理搬家不值得为命名一致性冒静默失败的风险。若日后真要物理重排，重开本票即可。
