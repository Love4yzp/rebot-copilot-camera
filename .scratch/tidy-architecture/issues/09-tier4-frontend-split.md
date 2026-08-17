# 09 档 4 前端拆分

Type: task
Status: open
Blocked by: 08

## Question

执行档 4：拆分 `App.tsx`（899 行）与 `TimelineView.tsx`（783 行）。

**约束**：src/ 对 mock/、contract/、vendor/ 的依赖方向已是干净的（mock→src，无反向），拆分不得破坏这个方向。既有目录（components/ library/ monitor/ preview/ timeline/ transport/）就是现成的落点。行为零变化 —— 仓库约定不写前端组件测试，验收靠人眼：`npm run dev` 走一遍 示教 → 录位姿 → 时间轴 → 预演 → 执行。

**清单**：认领后先读两个文件，把拆分方案要点贴在票内评论区，再动手。拆分一个 commit，构建一个 commit。`npm run build` 必须绿。PROGRESS.md 同 commit 更新。
