# Advisor Collaboration — OPUS 是决策者

> 用途：execution skill 调 advisor 的协议。
> 归属：M-1.4 execution skill 知识库
> Design constraint: the OPUS-tier advisor is the decision authority for the
> configured high-judgment path, not a source of optional suggestions.

---

## OPUS 不是顾问——是决策者

The role contract therefore treats an advisor response as a decision within its
declared authority and evidence boundary, while still allowing new physical
evidence to reopen that decision.

**这意味着**：
- 你（execution session，按 task.model_tier 跑 SONNET 或 OPUS）遇到判断困难时调 advisor-consult fork（**永远是 OPUS**）
- advisor 给的是 **决策（verdict）**——你按 verdict 实施，不是"考虑 advisor 的建议然后自己决定"
- 你仍然负责 **实施 quality**——advisor 说"用 self-heal 路径"，你仍要正确实施 self-heal

## 上下文相关命名

- **SONNET 主导的执行环境**（默认 implementation / test / documentation task）：你是 SONNET 执行者，OPUS 是 advisor
- **OPUS 主导的判断环境**（high-risk task / 设计判断 / 复杂决策）：你本身就是 OPUS，不需要"调 advisor"——你自己做决策

所以"调 advisor"是 SONNET 执行场景下的事。OPUS 主导的 task 不存在"主 session 调 advisor"——主 session 就是 OPUS。

## 何时必须调

- 拿不准这件事怎么处理（mismatch resolution 路径选择 / 实现方式选择 / done_criteria 验证方式）
- 多个化解路径之间的 tradeoff 你不清楚
- mid-execution 遇到判断困难（"这段代码这么写是不是对的"）
- pre-submit self-check 有 doubt
- 累积出现多次"不知道该怎么处理"的小决策

## 何时不必调

- 化解路径明确（系统理解告诉你怎么做）
- 文档格式 / 命名细节（不影响逻辑）
- 测试输出明确（pass / fail 不需要判断）

## 调 advisor 的 capsule 内容

advisor fork 只看你给它的 capsule——不看全 TaskPackage、不看全 brief。你必须提供：

```yaml
advisor_consult_capsule:
  task_summary: string                    # 任务摘要（不是全文）
  current_phase: enum [starting | mid_execution | pre_submit | post_reject]
  specific_question: string               # 必须明确具体——"我应该 X 还是 Y？"
  executor_tentative_answer: string       # 你倾向哪个 + 为什么
  executor_uncertainty: string            # 你不确定的是什么
  mismatch_evidence?:                     # 如适用
    - {mismatch_type, severity, evidence}
  relevant_concept_refs: [string]         # 最多 5 个相关概念——capsule 不能膨胀
  
  # NOT 包括（capsule 膨胀失败模式）:
  # - 整个 TaskPackage（advisor 看 summary 够）
  # - 整个 brief
  # - 已 resolved 的历史 mismatch
```

## advisor verdict 长什么样

```yaml
advisor_verdict:
  decision: enum [
    use_path_self_heal,
    use_path_consult_again,                # 罕见——表示信息不足，重问
    use_path_trigger_replan,
    use_path_abort_task,
    custom_action                          # 给具体执行步骤
  ]
  rationale: string                       # 必填
  specific_steps: [string]                # custom_action 时填
  confidence: high | medium | low
  evidence_scope_summary: string          # advisor 基于什么证据/capsule 做的判断（必填）
```

最常见的是 `custom_action`——advisor 给你具体执行步骤，你按步骤实施。

## Evidence Scope Binding —— advisor 不是神谕

OPUS 是决策者——但**verdict 的绑定范围 = advisor 看到的 capsule + evidence**。这不是软话术，是硬边界。

**executor 实施 advisor verdict 时如果遇到任何以下情况，不能机械执行**：

| 情况 | 做什么 |
|---|---|
| 新证据出现，跟 verdict 的 evidence base 不一致 | 重新触发 AdvisorConsultRequested（带新 evidence）|
| 物理检查跟 verdict 矛盾（V-01 owner-guard 拒、commit gate reject reason 跟 advisor 预期不符） | 重新 AdvisorConsultRequested |
| 实施步骤途中发现 verdict 假设错（比如 advisor 说 "self-heal 即可" 但实施时发现是 critical mismatch） | self-heal 路径终止 → AdvisorConsultRequested 升级，或直接 RePlanTriggered |
| Verdict 跟你已知的某个 hard 约束冲突（pin stale / done_criteria vs brief 矛盾这两类） | 不机械执行 verdict → RePlanTriggered（critical 硬约束优先于 advisor verdict）|

**为什么这条很重要**：Nature 强调 OPUS 是决策者——这建立了对 advisor 的尊重。但尊重不等于神谕化。如果 executor 因为"advisor 说了"而无视新证据/物理现实——advisor 就从"高阶判断"变成"有限上下文 verdict 被绝对化"——这是 v3 整体精神的反面。

**判别尺**：
- "我按 verdict 干没问题，结果就是 verdict 预期的" → 健康
- "我按 verdict 干，但中间发现 verdict 假设错了，我还是继续干完" → 神谕化失败模式
- "我按 verdict 干，途中新 evidence → 重新 consult" → 健康

**留痕**：每次因为新证据重新 consult，AdvisorConsultRequested.triggered_by 标 `verdict_evidence_scope_breach`——advisor session 能看到上一次 verdict + 新 evidence，给新 verdict。

## 我容易偏向哪里

**神谕化 advisor**：advisor verdict 当圣旨，新证据出来也不重新 consult。对治：evidence scope binding 硬规则——遇新 evidence / 物理冲突 → 重新 consult。

**不调 advisor 硬扛复杂判断**：sonnet 拿不准时硬扛 = 决策不可靠 = 后续 bug。

**每个小决策都调 advisor**：调用成本高，advisor 注意力分散。

**advisor verdict 不采纳**（除新 evidence 场景）：advisor 是决策者，按 verdict 执行。

**advisor capsule 塞全文**：capsule 膨胀 → advisor 失焦 → verdict 质量下降。

**反复 consult_again 不补 capsule**：advisor 没说什么是缺的——你也不知道——卡住。

## 调用频率健康度

- **健康**：一个 task 调 advisor 0-3 次
- **0 次**：task 简单清楚，sonnet 直接搞定
- **5+ 次**：这个 task 不该让 sonnet 主导——task.model_tier 应该是 opus，重新跑

如果你（SONNET）在一个 task 里调 advisor 5+ 次——这是信号告诉 planner / 你自己："这件事的判断密度超出 sonnet 能力，应该 opus 主导"。可以触发 RePlanTriggered 让 planner 升级 model_tier。

## 失败模式

| 误做法 | 为什么错 |
|---|---|
| 神谕化 advisor verdict（新 evidence / 物理冲突也机械执行）| 见 "Evidence Scope Binding" 段——advisor 不是神谕 |
| 不调 advisor 硬扛复杂判断 | sonnet 拿不准时硬扛 = 决策不可靠 = 后续 bug |
| 每个小决策都调 advisor | 调用成本高，advisor 注意力分散 |
| advisor verdict 不采纳（除新 evidence 场景）| advisor 是决策者，按 verdict 执行 |
| advisor capsule 塞全文 | capsule 膨胀 → advisor 失焦 → verdict 质量下降 |
| 反复 consult_again 不补 capsule | advisor 没说什么是缺的——你也不知道——卡住 |
