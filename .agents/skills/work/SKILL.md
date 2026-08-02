---
name: work
description: Harness V3 Flowness 的正式工作入口。当用户要求在 Harness 中实现、修复、设计、整理或发布时使用；不要改用全局旧 lead 流程。
---

# ChatGPT / `$work`

本文件只是 ChatGPT 的加载桥，不是第二套工作流。

1. 完整读取并执行仓库根目录 `.claude/commands/work.md`。
2. 把用户当前的正式工作请求当作其中的 `$ARGUMENTS`。
3. 按该入口指向装载 `.claude/skills/<stage>/`；`.claude` 是 Harness 共享资产目录，
   不是 Claude 专属边界。
4. 不调用全局的 `lead`、`arch`、`plan-lock`、`task-arch`、`towow-dev`；
   Harness V3 Flowness 已经取代它们。

如果是闲聊、查状态或纯只读调查，按 `AGENTS.md` / `CLAUDE.md` 的边界判断，
不新起正式工作。
