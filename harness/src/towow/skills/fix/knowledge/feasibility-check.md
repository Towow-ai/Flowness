# Feasibility Check — Graduated, fix 视角

> 用途：M-1.6 启动时第一步——判断 closure_contract 在当前现实下可执行吗。graduated（简单 finding 走 fast-path）。
> 归属：M-1.6 fix skill 知识库
> 核心精神：M-1.6 不是 closure_contract 盲执行器——但也不是漫游分析器。Bounded checklist 不是 free reasoning。

---

## 为什么 feasibility check 必须有（critique 的真贡献）

如果 M-1.6 拿到 finding 直接进 fix——会变成 closure_contract 盲执行器。Closure_contract 不是神谕：

1. **closure_contract 很清楚**——直接修
2. **closure_contract criteria 彼此冲突**——硬修制造矛盾
3. **closure_contract 指向位置过窄**——真实问题在更上层（concept / spec）
4. **修复会破坏工程共识**（F-09a 反向）—— 不能默默改
5. **closure_contract 已过时**——代码 / concept / obligation 已变化
6. **修复成本明显超过原 task scope**——应 RePlanTriggered 不是硬修

所以 M-1.6 第一步是 feasibility check——判断"这件事我能不能在当前 scope 内闭合"，不能就升级，不是硬修。

---

## Graduated（不是每次都跑详细 check）

### 简单 finding：走 fast-path feasibility，然后进 fix

判别标志（**全部满足**才算简单）：

- ✓ `suggested_fix_layer.primary` ∈ {code, sql, config}（单层 + 局部）
- ✓ `closure_contract.ripple_targets.length` ≤ 2 且全在同一 doc / module
- ✓ `closure_contract.closure_criteria` 全部 `verification_method` ∈ {grep, schema_check, test}（机械可验，无 manual_reasoning）
- ✓ 不涉及 `concept` / `obligation` / `spec` / `review_plan` 任何 supersede

→ **直接进 fix**——闭合可行性由 closure_contract 自身 well-formed 保证（M-1.5 已 ensure）

**简单案例**：
- typo 修正
- 单一 SQL where 条件错
- config 字段类型错
- 单 module 内 API 签名 fix

### 复杂 finding：跑详细 feasibility check（6 项）

任一标志命中算复杂：

- `suggested_fix_layer.primary` ∈ {concept, obligation, spec, review_plan}
- `ripple_targets` 跨多 doc / multi-module
- `closure_criteria` 含 `manual_reasoning`（语义判断）
- 不确定是否在 task scope 内
- finding 触及核心不变量 / 高风险面

→ **跑 6 项详细 feasibility check**

---

## 6 项详细 Feasibility Check

每项有具体决策出口——通过 → 进 fix；不通过 → 产对应 outcome event。

### Check 1: 修复是否在 task scope 内

**怎么 check**：
- closure_contract.ripple_targets 是否全在 task.write_set ∪ task.read_set 内？
- 修复涉及的 module / doc 是否超出 task 的隐含 scope（task spec + concept_refs）？

**通过判据**：所有 ripple_targets 在 task scope 内 + 主修复点在 task scope 内

**不通过 → outcome event**: `RePlanTriggered`
- payload.trigger_source: fix_scope_violation
- payload.evidence: 哪些 ripple_target 超出 task scope
- payload.suggested_new_scope: 实际需要的 scope hint

### Check 2: 是否违反本次工程共识（F-09a）

**怎么 check**：
- 修复中是否需要引入新概念（不在本次 concept_graph）？
- 修复中是否需要打破 concept 的 state machine 约束（F-04d）？
- 修复中是否引入新的 @ ref（不在本次 ref graph）？

**通过判据**：修复 staying within frozen concept_graph + state machine + ref graph

**不通过 → outcome event**: `SemanticUpgradeDeclaration` + 对应 supersede event
- 不能默默改——必须按 F-04d / F-04e 走 supersede 协议
- supersede event 走 M-0.5 NoveltyCheck（必须带 novelty）

### Check 3: 是否需要 supersede concept

**怎么 check**：
- 修复是否需要修改概念定义？修改 state machine 形态？
- closure_criteria 是否涉及概念语义变化？

**通过判据**：修复不触动 concept 定义本身

**不通过 → outcome event**: `FindingCreated(concept_issue)` → M-1.2 authority owner 接管
- 通过 M-0.6 obligation supersede 协议
- 走 F-04d 状态变迁链 → 自动通知下游引用方（v3 既有 mechanism）

### Check 4: 是否需要 supersede obligation

**怎么 check**：
- 修复是否破坏 active_obligations（capsule 注入的）？
- closure_criteria 是否要求 obligation 不再 maintained？

**通过判据**：所有 active_obligations 仍 maintained

**不通过 → outcome event**: `FindingCreated(obligation_issue)` → M-0.6 authority owner 接管
- 走 M-0.6 obligation lifecycle 协议（capture/evolve/retire）
- 必须带 novelty（NoveltyCheck）

### Check 5: 是否需要 escalate Nature

**怎么 check**：
- 修复涉及产品语义判断（不只是工程实现）？
- 修复涉及不可逆决策（数据 schema / 部署 / 安全策略）？
- 多轮 fix 仍不闭合 + 无 novelty？

**通过判据**：纯工程实现 + 无产品判断 + 可逆

**不通过 → outcome event**: `EscalationRaised`（含 nature_facing_summary + options）
- 用产品语言（不工程黑话）—— 见 escalation-product-language.md

### Check 6: closure_contract 自身可执行

**怎么 check**（fix 视角复查 M-1.5 应已 ensure 的）：
- closure_criteria 每条 condition 是否清楚（能写成具体动作）？
- closure_criteria 每条 verification_method 是否能跑（grep 能跑通？test case 存在？projection 字段存在？）
- ripple_targets 每个 location_hint 能定位（文件 + section 真实存在）？
- forbidden_residuals 每个 pattern 能 check（pattern 不空且 check_method 可跑）？

**通过判据**：closure_contract 三个字段全部可机械化执行

**不通过 → outcome event**: `FindingCreated`（issue 反向给 M-1.5）
- payload.description: "closure_contract 不可执行——具体哪一项"
- payload.target.location: 反向指 finding.finding_id
- payload.suggested_fix_layer.primary: review_plan（让 M-1.5 改 closure_contract）
- 这件事 M-1.5 应该已自检了——但 M-1.6 复查（双层 verification 防遗漏）

---

## 6 项之间的优先级

按上面顺序——前面的 check 先做：
- Check 1 (scope) 是 task-level 边界——最早判
- Check 2-4 (consensus / concept / obligation) 是 design-level 完整性
- Check 5 (escalation) 是 product-level 判断
- Check 6 (closure_contract 自身) 是 contract-level 验证

如果 Check 1 不通过 → 直接产 RePlanTriggered，**不继续跑后面 5 项**（scope 都不对了，下面 check 没意义）。

---

## 出口可叠加

- Check 3 + Check 2: 既需 supersede concept 又涉及共识打破 → 产 FindingCreated(concept_issue) + FindingCreated(obligation_issue)
- Check 4 + Check 5: 既需 supersede obligation 又需 Nature 决策 → 产 EscalationRaised（含 obligation evolution proposal）

不强制每个 finding 只走一条出口——但每个 outcome event 应该有明确的 trigger reason。

---

## Check 完成后下一步

| Check 结果 | 下一步 |
|---|---|
| 6 项全通过 + 简单 finding 也允许 | 进入 closure-contract-execution（三段式执行）|
| 任一 check 不通过 | 产对应 outcome event → 退出 M-1.6 cycle |
| 多个 check 不通过 | 选最 upstream 的 outcome event 产出（如 scope 都不对就不需要再产 supersede event）|

**关键**：feasibility check 不通过不是失败——是诚实识别"这件事不该在 M-1.6 单独闭合"。产对应 event 让 v3 既有 mechanism 接管。

---

## 防 ritual / 防漫游（dual failure mode）

| 失败方向 | 症状 | 对治 |
|---|---|---|
| **Ritual compliance** | 简单 finding 走 fast-path 时强行跑 6 项详细 | Graduated——简单 finding 走 fast-path |
| **Free reasoning 漫游** | feasibility check 借机做 free analysis "这个 finding 真的对吗 / 改这里会不会有 X 风险" | Bounded checklist——按 6 项跑，不发散；novelty/dispute 走 author dispute flow 不是 M-1.6 |

**正确姿态**：feasibility check 是 **6 项 checklist 不是 free reasoning**——每项用具体 evidence 回答（不抽象判断），check 完进 fix 或退到出口。

---

## 跟 M-1.5 author dispute 的边界

| Skill | 视角 | 时机 |
|---|---|---|
| **Author dispute** | author 视角——"这个 finding 是不是真问题" | M-1.5 author_time mode 后，accept 之前 |
| **M-1.6 feasibility check** | fixer 视角——"closure_contract 能不能在我 scope 内执行" | author accept 后，M-1.6 启动第一步 |

两者**不重叠**：
- author 判断"真问题吗"
- M-1.6 判断"修得了吗"

如果 closure_contract 既不是真问题（author 应 dispute）又不可执行（M-1.6 应 reverse）—— 是 M-1.5 + M-1.4 author 协作的失败模式，应在 author_time mode 解决，不在 M-1.6。
