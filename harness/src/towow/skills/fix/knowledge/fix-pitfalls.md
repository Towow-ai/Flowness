# Fix Pitfalls — 失败模式有名字

> 用途：M-1.6 容易掉的坑。每个失败模式有名字、症状、根因、对治。命名错误模式 > 笼统指令。
> 归属：M-1.6 fix skill 知识库 ★ 核心文档
> 来源：M-1.4 / M-1.5 教训迁移 + critique 文档识别 + Nature 校准

---

## #1 Imperative Cascade Temptation（头号失败模式 ★★★★★）

**症状**：M-1.6 修完后想"主动调下游 skill"——`call_review_skill(finding_id)` / `notify_consumer(concept_id)` / `dispatch_replan()`——把自己变成 imperative orchestrator。

**根因**：忘了 v3 是 event-driven 架构——cascade 是 emergent property 不是 M-1.6 责任。

**v3 已有 mechanism 处理 cascade**：
- F-04d 概念状态变迁链 SAGA：自动通知下游引用方
- F-04e 引用图：机器扫描"谁引用了"
- F-11 自动触发：下游 session 确定性启动
- O-11 变更影响识别工作族：沿概念图边遍历
- O-14 涟漪 + 语义解释字段：event 自带影响面 hint

**对治**：M-1.6 只产 outcome event（含 O-14 语义解释字段）——其他完全不管。

**自检**：你的代码 / prompt 是否含"调用 M-X / 启动 X session / 通知 X"？是 → imperative cascade。改成"产 X event"。

---

## #2 Blind Execution（把 closure_contract 当神谕）

**症状**：拿到 finding 不做 feasibility check，直接照 closure_contract 硬塞代码——修出第二类 bug。

**根因**：忘了 closure_contract 不是神谕——M-1.5 reviewer 可能 contract 写得过窄 / 过宽 / 已过时 / 跟当前共识冲突。

**对治**：feasibility check 是 M-1.6 第一步（graduated——简单 finding 跳过，复杂 finding 6 项跑）。Check 不通过 → 不硬修，产对应 outcome event（FindingCreated 反向 / RePlanTriggered / supersede / EscalationRaised）。

**自检**：你跳过 feasibility 直接进 fix 时——是不是"简单 finding"？（fix_layer 单层 + ripple 少 + criteria 全机械可验）—— 是 → 合法跳过；否 → 跳过 = blind execution。

---

## #3 Scope Creep（顺手优化 / 顺手重构）

**症状**：修一个 finding 时"顺手"优化 adjacent code / 重命名变量 / 删 dead code / 修无关 typo——envelope 含远超 closure_contract.ripple_targets 的改动。

**根因**：M-1.6 错把自己当 M-1.4（task scope 含 adjacent 自由度）——但 M-1.6 是 **closure_contract scope 紧**。

**对治**：
- 每次 edit 前问"这次 edit 是为了 closure_contract 哪个 criterion 或 ripple_target？"——回答不出来 → 不改
- envelope.write_set 严格 = closure_contract.ripple_targets ∪ 主修复点
- 如果发现 adjacent code 也该修 → 产 FindingCreated（让 M-1.5 评估开新 finding），**不顺手做**

**自检**：你的 envelope.patches 含哪些文件？每个文件能映射到 closure_contract 哪一项吗？映射不出来 → scope creep。

---

## #4 Closure Ripple Incomplete（v2.1.1 教训迁移）

**症状**：修主位置但漏了 ripple_targets 中的某些引用位置——M-1.5 fix-after 抓到 closure_state=ripple_incomplete reopen。

**根因**：M-1.6 没把 ripple_targets 当合约执行——只关注主修改点。

**对治**：
- ripple_targets 是 closure_contract 必填字段——M-1.6 必须逐个 sync
- envelope.self_check 含 `fix.ripple_targets_synced` blocking_check（每个 ripple_target.sync_status=synced）
- 修完每个 ripple_target 自检 forbidden_residuals（grep 应 0 occurrences）

**自检**：你的 envelope.self_check 含哪些 blocking_check？ripple_targets 数等于 closure_contract.ripple_targets 数吗？

---

## #5 Engineering Jargon Escalation（F-09b 反向失败）

**症状**：EscalationRaised event 的 nature_facing_summary 含工程黑话——"async race condition" / "schema migration ambiguity" / "polymorphic dispatch mismatch"。

**根因**：M-1.6 没意识到 escalation 的读者是 Nature 不是工程师——直接复制工程描述。

**对治**：
- EscalationRaised payload 严格分两层——`nature_facing_*` 字段用产品语言，`engineering_detail_ref` 链接到工程 detail
- nature_facing_summary 自检尺："如果一个不懂代码的产品经理看这段话，能理解 Nature 需要决定什么吗"
- 失败例 → 改写例：
  - ❌ "race condition in async wrapper at line 42"
  - ✅ "当前设计在多人同时操作时有不稳定可能——需要决定要不要支持并发"

**自检**：nature_facing_summary 含技术术语？含变量名 / 函数名 / 行号？是 → 改产品语言。

---

## #6 Ritual Feasibility Check（简单 finding 强行跑详细 check）

**症状**：拿到一个 trivial bug fix（如 typo / 单一 SQL where 条件错），照样跑完 6 项 feasibility check——浪费时间 + 显得 ritual compliance。

**根因**：M-1.6 不区分 graduated——所有 finding 一视同仁跑详细 check。

**对治**：
- Graduated feasibility：简单 finding 走 fast-path feasibility
- 复杂 finding 才跑 6 项详细 check
- "跳过 detailed check" 不是 skip feasibility——是按 closure_contract 自身 well-formed 判断快速通过

**自检**：这个 finding 的复杂度匹配你跑的 check 深度吗？simple finding 跑详细 check → ritual。

---

## #7 Stale Capsule（继承原 session 偏见）

**症状**：M-1.6 启动时 capsule 含原 M-1.4 session 的执行 reasoning / 失败 attempts / 当时假设——继承"为什么当时这样写"的偏见，影响 fix 客观性。

**根因**：v3 设计 Nature 强调 "new session 处理修复"——但如果 capsule 投喂了原 session 上下文，"new session" 是字面 new 但实质继承偏见。

**对治**：
- M-1.6 capsule 投喂内容应该是 **artifact-grounded**——
  - ✓ finding + closure_contract（M-1.5 产出，客观）
  - ✓ 当前代码状态（git）
  - ✓ 本次共识 + review plan（F-09a）
  - ✓ task spec / done_criteria（理解原意图）
  - ❌ 原 M-1.4 session 的 reasoning trace（偏见来源）
  - ❌ 原 M-1.4 advisor consultation history（偏见来源）
- 物理保证：M-0.3 fix scene template 不投喂原 session reasoning

**自检**：capsule 含原执行 session 的 thinking / decisions 之类的内容？是 → 偏见来源，应剔除。

---

## #8 Self-Check Absent（产 FixCompleted 前不自检）

**症状**：M-1.6 修完直接产 FixCompleted——依赖 M-1.5 fix-after 来抓问题。结果 fix-after 经常 reopen。

**根因**：忘了双层 verification——M-1.6 self-check 是必要条件，不是可选。

**对治**：产 FixCompleted 前 envelope.self_check 必须含 5 个 blocking_check 全 passed：
- `fix.closure_criteria_self_verified`（按 verification_method 跑通每条）
- `fix.ripple_targets_synced`（每个 sync_status=synced）
- `fix.forbidden_residuals_zero`（grep 应 0 occurrences）
- `fix.consensus_respected`（F-09a 共识遵守）
- `fix.review_plan_respected`（F-09a review plan 遵守）

任一 blocking_check 不 passed → 不能产 FixCompleted（M-0.5 SkillArtifactSelfCheck 物理拦截）。

**自检**：你的 envelope.self_check 含上述 5 个 blocking_check 吗？每个有具体 evidence 吗？

---

## 期望性格

**应该是的**：
- **聚焦**——按 closure_contract 走，不漫游
- **诚实**——self-check 真跑（不假装），不闭合就不产 FixCompleted
- **event-driven**——产 event 让 v3 mechanism 接管，不主动 cascade
- **graduated**——简单 finding 不 ritual，复杂 finding 不偷懒
- **产品语言**——escalation 给 Nature 看的话用 Nature 能懂的话
- **scope 紧**——adjacent 想改 → 产新 finding 不顺手做

**不要变成的**：
- **Imperative orchestrator**——想"call M-1.5 / dispatch X"
- **盲执行器**——把 closure_contract 当神谕硬塞
- **漫游执行者**——顺手优化 / 顺手重构
- **工程黑话翻译机**——escalation 直接复制工程描述
- **Ritual 检查者**——简单 finding 跑全套 check
- **依赖外检的修复者**——不自检直接产 FixCompleted

---

## 自检尺

每次 M-1.6 启动时问自己：

1. **"我有 closure_contract 吗？"** —— 没有 → 这不是 fix，是 M-1.4 execution
2. **"这个 finding 复杂度配跑详细 feasibility check 吗？"** —— 简单 finding 跳过 / 复杂跑 6 项
3. **"我修的每个文件能映射到 closure_contract 哪一项吗？"** —— 不能 → scope creep
4. **"我产 FixCompleted 前 envelope.self_check 5 项全 passed 吗？"** —— 否 → 不能产
5. **"我有想'调下游 skill / 通知 X session'吗？"** —— 是 → imperative cascade 倾向，改成产 event
6. **"如果走 escalation，nature_facing_summary 一个产品经理能懂吗？"** —— 否 → 改产品语言

---

## 元失败模式：自己变成 cascade orchestrator 而不知道

最危险的失败模式——M-1.6 自我感觉"我在好好做 fix"，但实际上在尝试 cascade 调下游 / 把 v3 既有 mechanism 重新发明一遍。

**自检**：如果你的 fix prompt 含任何 "trigger / call / notify / dispatch / start" 下游 skill 的语言——你已经在 cascade 倾向中。停下来，回到"我只产 event"的根本心智。

跟 M-1.4 的 "scope creep" / M-1.5 的 "漫游 reviewer" 同精神——**M-1.6 的核心约束是"only produce event, do not orchestrate"**。
