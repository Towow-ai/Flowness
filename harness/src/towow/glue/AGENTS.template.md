# AGENTS.md — ChatGPT 进入 Harness V3 Flowness 的入口

本项目已安装 Harness V3 Flowness。先完整读取项目根目录 `CLAUDE.md`；它是
ChatGPT 和 Claude 共用的工作规约，不是 Claude 专属人格。

- 正式工作使用 `$work`，它会转入唯一真入口 `.claude/commands/work.md`。
- 本项目内禁用全局旧 `lead -> arch -> plan-lock -> task-arch -> towow-dev`
  流程；当前 V3 Flowness 已取代它们。
- `.claude/commands`、`.claude/skills`、`.claude/docs` 是 Harness 共享资产；
  `.claude` 只是历史目录名。ChatGPT 按 V3 指向直接读取，不复制第二份。
- Markdown、HTML、JSON、图片、日志和证据包按 Harness 语义、provenance 和验收
  标准处理，不按产出它的模型或文件扩展名区别对待。
- `.codex/hooks.json` 把 ChatGPT 事件翻译给共享 `.claude/hooks/`。新会话
  如提示 hook 待审核，用 `/hooks` 检查并信任本项目定义。
- 其余真相源、`./tw` 入口、活跃会话/工位和不可逆动作边界，与
  `CLAUDE.md` 完全一致。

<!-- harness-managed-chatgpt-entry -->
