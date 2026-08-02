# AGENT DECISION OWNERSHIP RUBRIC

> 这是 single source of truth。CLAUDE.md 和 `.claude/skills/*/SKILL.md` 引用此文件，不复制内容。
>
> 起源：DOGFOOD-RUN-001-FINDINGS DOGFOOD-001-C agent-escalation-pattern-defect。
>
> 适用范围：所有 v3 L1 skill（interview / engineering_consensus / planning / execution / review / fix / maintenance），以及任何在 v3 framework 内运行的 agent。

---

## 关键运行时规则

> **问 Nature 前，先判断这个问题是否真的只有 Nature 能回答。**

如果 agent 在 surface 问题前没做这一步自判别，就违反 Nature attention 稀缺原则和 v3 agent-first 审查链。

判别用下面三类 rubric。

---

## Class A — 必须问 Nature（owner-level decision）

这些是 owner-level decision，**只有 Nature 能定**：

- 产品策略 / 系统哲学 / 风险偏好
- scope 边界（什么算这次任务、什么不算）
- 多个合理方案之间的价值取舍
- 是否接受某个用户体验或可见性代价
- spec 与现实约束硬冲突，且需要 owner 选择牺牲哪条原则
- 跨任务范围的资源 / 节奏决策

### Class A 例子

例 1：
> "/work 应该只在 harness repo 生效，还是作为 user-level 全局命令？"

判断：**Class A**。影响使用边界 + 推广策略 + 跨项目影响面 → Nature 问题。

例 2：
> "self-hosting 完成判定到哪一步？plan freeze / commit accepted / 走完 review fix 闭环？"

判断：**Class A**。影响 scope 大小 + 时间成本 + 风险偏好 → Nature 问题。

例 3：
> "这次 dogfood 暴露的 spec gap 是 v3 设计错（type A）还是设计缺（type B）？"

判断：**Class A**（当涉及修改 v3 核心 spec / 触发 LEDGER 新 entry 时）。spec 修订是 owner-level decision。

---

## Class B — Agent 必须自决并修复（不问 Nature）

这些不是 Nature 选择题。agent 必须自决并修复。**surface 可以，escalate 不可以**：

- provenance 断链 / session_id 缺失 / causation_id 未传
- raw prompt / 关键 input 进不了 canonical event
- capsule fail-closed 未落实
- commit gate / self_check / owner comprehension gate 物理机制缺失
- 当前任务的直接前置能力不存在
- 违反 v3 核心不变量（event-log canonical / single source of truth / V-01 owner-guard / NoveltyCheck cross-cutting / fail-closed 等）
- 已经 surface 过且未发生新约束 → 不能反复问 Nature

### Class B 例子

例 1：
> "interview answer session_id=null 要不要修？"

判断：**Class B**。违反 provenance invariant。必须修，不需要问 Nature。

例 2：
> "towow interview start --raw-prompt 没实现要不要修？"

判断：**Class B**。/work entry gate 物理前提不成立。必须修。

例 3：
> "SkillArtifactSelfCheck 当前 commit gate 没实现 blocking_check 评估，要不要补？"

判断：**Class B**。owner comprehension gate 物理强制依赖此机制。必须修。

例 4：
> "M-1.2 consensus_start 当前不检查 brief 是否 confirmed，要不要补 precondition？"

判断：**Class B**。"M-1.2 不消费未确认 brief" 这个 invariant 没物理强制就是 paper。必须修。

---

## Class C — 可 surface 但默认给推荐（不要求 Nature 拍板）

这些可以告诉 Nature，但 agent 默认给推荐路线 + 理由：

- 实现落点（文件归位 / module 组织）
- 测试覆盖方式
- schema 字段命名
- backward-compatible parse 细节
- ledger / patch 文档放哪里
- task 拆分粒度（在已 approved 的 plan scope 内）

### Class C 正确写法

✅ "我发现 X。它违反 Y invariant，所以我会纳入 T0 修复。**除非你反对，我继续。**"

✅ "我倾向把 LEDGER 加新 section（选项 C）而不是新建文件（选项 B），理由是 single source。**除非你反对，按 C 走。**"

### Class C 错误写法

❌ "要不要修 X？"

❌ "schema 字段命名用 A 还是 B？"

❌ "测试放 tests/unit 还是 tests/integration？"

❌ "T0 是 6 个子任务还是 7 个子任务？"

---

## 判别尺执行流

agent surface 问题前，必走 4 步：

```
Step 1: 这个问题是否涉及 owner-level decision (Class A 范围)？
  YES → 走 surface flow，使用 owner-facing-render-style-profile，
        显式解释 "为什么这是你来拍板"
  NO  → Step 2

Step 2: 这个问题是否违反 v3 核心不变量 / 阻塞当前任务前置能力 (Class B 范围)？
  YES → agent 自决并修复，不问 Nature。可在 progress 汇报中 surface "已自决"
  NO  → Step 3

Step 3: 这个问题是否需要 Nature 知道但 agent 能给推荐路线 (Class C 范围)？
  YES → 走 surface flow，但 "除非反对，我继续 X" 而不是 "要不要 X?"
  NO  → Step 4

Step 4: agent 自决，progress 中可选 surface 决策结果
```

---

## 跟 owner-facing-render-style-profile 的关系

当 agent surface 一个 Class A 问题时，必须用 `owner-facing-render-style-profile@v1` 渲染：

- 不堆 INF id / task # / schema 内部细节
- 先讲本质，再讲选项
- 给推荐 + 理由（Class C 也适用）
- 解释 "为什么这是你来拍板"（Class A 强制）

详 `.towow/events.log` 中 owner-facing-render-style-profile@v1 concept event。

---

## 维护规则

- 本 rubric 默认对所有 v3 skill 适用
- 单个 skill 可以在 `.claude/skills/*/SKILL.md` 加 skill-specific 例子（不复制 rubric 内容）
- 发现新 escalation defect 模式 → 更新本文件 + 同步 review CLAUDE.md / SKILL.md 是否需要补例子
- rubric 自身修订走 NatureJudgmentCaptured + ConceptCreated supersede 路径（rubric 本身是 v3 内部 concept）
