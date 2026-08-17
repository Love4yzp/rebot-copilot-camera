# 09 档 4 前端拆分

Type: task
Status: resolved
Blocked by: 08

## Question

执行档 4：拆分 `App.tsx`（899 行）与 `TimelineView.tsx`（783 行）。

**约束**：src/ 对 mock/、contract/、vendor/ 的依赖方向已是干净的（mock→src，无反向），拆分不得破坏这个方向。既有目录（components/ library/ monitor/ preview/ timeline/ transport/）就是现成的落点。行为零变化 —— 仓库约定不写前端组件测试，验收靠人眼：`npm run dev` 走一遍 示教 → 录位姿 → 时间轴 → 预演 → 执行。

**清单**：认领后先读两个文件，把拆分方案要点贴在票内评论区，再动手。拆分一个 commit，构建一个 commit。`npm run build` 必须绿。PROGRESS.md 同 commit 更新。

## Answer

（2026-08-17，commit 1176f34）档 4 完成（一半刻意不动，记录如下）。

- 拆分 `App.tsx`（899 → ~600 行）：`library/useLibrary.ts`（数据加载/选中/持久化一个 hook）、`library/SequenceDialogs.tsx`（新建/改名/存模板/删除对话框状态机，ref 句柄由顶栏调用）、`useNumberKeys.ts`（数字键快捷位姿）。三个提取都是纯搬迁，行为零变化。
- `TimelineView.tsx`（783 行）**刻意不拆**：它自己的 docstring 写明「每次编辑只有一处实现，在本文件」，且 TrackBlock/StationCard/StationConnector/markers/selection/easing 早已拆出；剩下的拖拽/缩放编排没有组件测试兜底，拆它 = 静默 UI 破损，需要操作者 `npm run dev` 人眼验收的专门一轮，不在本档硬做。
- 验证：`npm run build`（tsc + vite）绿。**留给操作者**：下次打开界面把「示教 → 录位姿 → 时间轴 → 预演 → 执行」走一遍。
