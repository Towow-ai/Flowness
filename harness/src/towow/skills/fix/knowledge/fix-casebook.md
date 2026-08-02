# Fix Casebook — 具体 fix 案例

> 用途：12 个具体例子，帮 M-1.6 看到 mental model 落地形态。不是查表"case X 对应 path Y"——是看到"啊这种情况按 mental model 自然推导是这样的"。
> 归属：M-1.6 fix skill 知识库
> 核心精神：emergence over enumeration——案例展示判断过程，不是规则

---

## Case 1: 简单 typo 修复（→ FixCompleted）

**Finding**：
- target.location: `src/api/user.py:42`
- description: "Error message typo: 'Invalid creentials' should be 'Invalid credentials'"
- suggested_fix_layer: code
- closure_contract:
  - criteria: [{condition: "src/api/user.py 不再出现 'creentials'", verification_method: grep, expected_result: "0 occurrences"}]
  - ripple_targets: []
  - forbidden_residuals: [{pattern: "creentials", check_method: grep}]

**M-1.6 判断**：
- 简单 finding（单 code 层 + 0 ripple + 全 grep 可验）→ 走 fast-path feasibility
- 直接进 fix
- 改 line 42 typo
- envelope.self_check：grep 'creentials' src/api/user.py = 0 ✓

**Outcome**: FixCompleted

**为什么这个例子重要**：演示 graduated feasibility——简单 finding 不 ritual。

---

## Case 2: API 签名变更（→ FixCompleted with ripple sync）

**Finding**：
- target.location: `src/api/payment.py:get_payment_method()`
- description: "Method signature missing required parameter `tenant_id` after multi-tenant refactor"
- suggested_fix_layer: code
- closure_contract:
  - criteria:
    - {condition: "get_payment_method 签名含 tenant_id 参数", verification_method: schema_check, expected_result: "tenant_id parameter exists"}
    - {condition: "所有 caller 已传 tenant_id", verification_method: grep, expected_result: "0 calls without tenant_id"}
  - ripple_targets:
    - {artifact: "src/services/order.py", location_hint: "place_order method", reason: "calls get_payment_method"}
    - {artifact: "src/services/refund.py", location_hint: "process_refund method", reason: "calls get_payment_method"}
    - {artifact: "tests/test_payment.py", location_hint: "test cases", reason: "asserts on signature"}
  - forbidden_residuals: [{pattern: "get_payment_method\\(\\s*[^t]", check_method: grep}]

**M-1.6 判断**：
- 复杂 finding（multi-file ripple）→ 跑详细 feasibility check
- Check 1 (scope): ripple_targets 全在本 task scope ✓
- Check 2-4 (consensus / concept / obligation): 不动 concept ✓
- Check 5 (escalate): 不需要产品判断 ✓
- Check 6 (closure_contract 可执行): 全部 schema_check / grep 可验 ✓
- 全通过 → 进 fix
- 三段式执行：
  - 主：改 get_payment_method 签名
  - Ripple：order.py / refund.py / test_payment.py 全 sync
  - Residual：grep 0 occurrences
- envelope.self_check 5 项全 passed

**Outcome**: FixCompleted + semantic_upgrade_declaration.affected_concepts: [payment_method_api]

**为什么这个例子重要**：演示 ripple_targets 逐个 sync + forbidden_residuals 清扫。

---

## Case 3: closure_contract 不可执行（→ FindingCreated 反向）

**Finding**：
- description: "Some performance issue somewhere in the payment flow"
- closure_contract:
  - criteria: [{condition: "performance is improved", verification_method: manual_reasoning, expected_result: "feels faster"}]
  - ripple_targets: [{artifact: "the entire payment system", location_hint: "everywhere", reason: "performance"}]

**M-1.6 判断**：
- 复杂 finding → 跑详细 feasibility check
- Check 6 (closure_contract 自身可执行):
  - criterion "performance is improved" 不能机械化验 (manual_reasoning + 抽象 "feels faster")
  - ripple_target "the entire payment system" 不可定位 ("everywhere")
  - 这个 closure_contract 不可执行
- 产 FindingCreated 反向给 M-1.5

**Outcome**: FindingCreated（issue 反向）
- description: "原 finding F-XXX 的 closure_contract 不可执行——criterion 是 manual_reasoning + 抽象描述 'feels faster'；ripple_targets 不可定位 'everywhere'"
- suggested_fix_layer: review_plan
- voi_rationale: "在重新设计的 closure_contract 出来前，无法 bounded execute 这个 finding——继续硬修会制造盲执行 fix"

**为什么这个例子重要**：演示 M-1.6 不盲执行——closure_contract 自身有问题就反向 issue 给 M-1.5。

---

## Case 4: 修复越过 task scope（→ RePlanTriggered）

**Finding**：
- target.location: `src/api/user.py`（在原 task scope 内）
- description: "User auth flow has security gap"
- closure_contract:
  - ripple_targets:
    - {artifact: "src/api/user.py", reason: "main auth fix"}
    - {artifact: "src/services/admin.py", reason: "admin auth uses same flow"}  ← 不在原 task scope
    - {artifact: "src/middleware/auth.py", reason: "middleware needs update"}    ← 不在原 task scope

**M-1.6 判断**：
- Check 1 (scope): ripple_targets 中 2 个不在 task.write_set——scope 越界
- 不进 fix
- 产 RePlanTriggered

**Outcome**: RePlanTriggered
- trigger_source: fix_scope_violation
- actual_scope_required: [admin.py, middleware/auth.py]
- declared_scope: [user.py]
- semantic_upgrade_declaration.requires_replanning_dimensions: [auth_consistency]

**为什么这个例子重要**：演示越界不硬修——产 event 让 M-1.3 重新规划。

---

## Case 5: 修复需打破工程共识（→ FindingCreated(concept_issue)）

**Finding**：
- description: "User profile validation rejects valid emails with + character"
- closure_contract:
  - criteria: [{condition: "email validator 接受含 + 的 email", verification_method: test}]
  - ripple_targets: [{artifact: "concept:Email", reason: "concept definition needs update"}]

**M-1.6 判断**：
- Check 3 (需 supersede concept): closure_contract 指 concept:Email 需更新
- 不能默默改 concept 定义——必须走 supersede 协议
- M-1.6 不直接 supersede concept（v2.1.1 authority matrix）——产 FindingCreated(concept_issue) 让 M-1.2 接管

**Outcome**: FindingCreated(finding_kind=concept_issue)
- novelty.novelty_type: new_constraint
- novelty.detail: "email standard RFC 5322 allows + character; original concept was overly restrictive"
- new_definition: 更新后的 Email concept
- semantic_upgrade_declaration.affected_consumers: [user_profile / signup / login / ...]

下游消费方通过 F-04d 状态变迁链机器化感知——各自决定 accept_new / pin_old。

**为什么这个例子重要**：演示 F-09a 共识遵守 + 不默默改——通过 supersede event 走标准协议。

---

## Case 6: 多轮 fix 仍不闭合（→ EscalationRaised）

**Finding**：第 3 轮 fix
- 前两轮试了不同 approach，第 3 轮 M-1.5 fix-after 仍 closure_state=fix_insufficient
- 当前轮无新 novelty——M-1.6 已尝试 advisor consult，advisor 也建议 escalate

**M-1.6 判断**：
- 多轮无 novelty → escalate
- 但发现这件事涉及产品判断（要不要支持某 edge case）
- 产 EscalationRaised（产品语言）

**Outcome**: EscalationRaised
- nature_facing_summary: "用户在使用国际信用卡（如带特殊字符的姓名）支付时会失败。我们试了 3 种方法都不行——核心问题是：要不要支持非 ASCII 字符的姓名？支持会让支付流程多一步验证；不支持要明确拒绝并告诉用户原因。需要决定要哪种用户体验。"
- options:
  - {option: "支持非 ASCII 姓名", tradeoff: "支付流程加 1 步验证，约 +200ms", product_impact: "国际用户可用，所有用户多 1 步"}
  - {option: "明确拒绝并提示", tradeoff: "国际用户需用拼音/英文", product_impact: "明确告知，不会失败"}
  - {option: "默认拒绝 + 提供 manual review 通道", tradeoff: "增加客服工作量", product_impact: "国际用户慢但能用"}
- decision_needed_from_nature: "在 3 个选项中你倾向哪个？还是有第 4 种？"

**为什么这个例子重要**：演示 F-09b 产品语言——nature_facing_summary 没有"unicode encoding" / "validation function" 等工程黑话。

---

## Case 7: Adjacent code 想顺手修（→ 产新 finding 不顺手做）

**Finding**：修 src/api/user.py 的 typo
M-1.6 改 typo 时发现：同文件 line 87 有个明显的 SQL injection 风险（不是 finding 的 closure_contract 范围）

**M-1.6 判断**：
- closure_contract.ripple_targets 没列 line 87
- 不顺手修（防 scope creep #3）—— 这件事不在我 closure scope
- 产 FindingCreated（让 M-1.5 / author 评估开新 finding）+ 继续完成原 closure_contract

**Outcome**:
- 主 fix: FixCompleted（按 closure_contract）
- Adjacent 发现: FindingCreated（separate finding for line 87 SQL injection）

**为什么这个例子重要**：演示 scope 紧 + 通过 issue 机制处理 adjacent 发现——不是漫游修。

---

## Case 8: Self-check 不 passed（不能产 FixCompleted）

**Finding**：复杂 ripple
M-1.6 修了主位置 + 3 个 ripple_targets 中的 2 个——剩 1 个 ripple_target 没 sync

**M-1.6 判断**：
- 准备产 FixCompleted
- envelope.self_check.fix.ripple_targets_synced：3 ripple_targets 中只 2 个 synced
- blocking_check status != passed
- M-0.5 SkillArtifactSelfCheck 物理拒

**Outcome**: M-1.6 不能产 FixCompleted——回去补 sync 第 3 个 ripple_target

**为什么这个例子重要**：演示 envelope.self_check 物理强制——不能假装 closed。

---

## Case 9: 想 cascade 调下游 skill（imperative cascade temptation）

**Finding**：修完后 M-1.6 想"主动启动 M-1.5 fix-after"

**M-1.6 判断**：
- "我应该 call M-1.5.start_fix_after_mode(finding_id)" ← 错——imperative cascade temptation
- v3 是 event-driven——FixCompleted event 入 log 后 F-11 自动触发 M-1.5
- 我只产 FixCompleted，不主动 call

**Outcome**: 产 FixCompleted（含 O-14 语义解释）——M-1.5 fix-after 由 F-11 自动启动

**为什么这个例子重要**：演示 #1 失败模式——M-1.6 不 cascade，只产 event。

---

## Case 10: Obligation 修复（→ FindingCreated(obligation_issue)）

**Finding**：
- description: "Active obligation 'all writes must be audited' too strict—blocks legitimate batch operations"
- closure_contract.ripple_targets: [{artifact: "obligation:audit_all_writes", reason: "needs evolution"}]

**M-1.6 判断**：
- Check 4 (需 supersede obligation): 是
- 不能默默改 obligation——走 M-0.6 obligation lifecycle
- M-1.6 不直接 evolve obligation——产 FindingCreated(obligation_issue) 让 M-0.6 接管

**Outcome**: FindingCreated(finding_kind=obligation_issue)
- novelty.novelty_type: new_evidence
- new_obligation_definition: "writes to user tables must be audited; batch system writes exempt"
- semantic_upgrade_declaration.affected_scope: [all skills with active_obligations including original]

**为什么这个例子重要**：演示 obligation supersede 走 M-0.6 协议——M-1.6 不直接改 obligation projection。

---

## Case 11: ripple_target 标 not_applicable

**Finding**：
- closure_contract.ripple_targets:
  - {artifact: "docs/api/user.md", location_hint: "API reference section", reason: "API doc sync"}
  - {artifact: "docs/api/legacy_user.md", location_hint: "old API doc", reason: "legacy compat note"}

M-1.6 修复时发现：legacy_user.md 已在另一 task 中被删除（M-1.4 已 cleanup）—— 不存在了

**M-1.6 判断**：
- ripple_target #2 不适用——但要明确标记 + evidence
- envelope.self_check.fix.ripple_targets_synced:
  - {target: docs/api/user.md, sync_status: synced}
  - {target: docs/api/legacy_user.md, sync_status: not_applicable, evidence: "file deleted in commit abc123 by task T-456"}

**Outcome**: FixCompleted（含 not_applicable evidence）

**为什么这个例子重要**：演示 not_applicable 必须有具体 evidence——不能凭感觉跳过 ripple_target。

---

## Case 12: 双重 outcome event（concept supersede + escalate）

**Finding**：修复需要改 concept + 涉及产品判断

**M-1.6 判断**：
- Check 3 (需 supersede concept) + Check 5 (需 Nature 决策) 都不通过
- 出口可叠加——但选最 upstream 的：先 escalate（Nature 决策后再决定 concept 怎么改）

**Outcome**: EscalationRaised
- nature_facing_summary 含 "需要改 X 概念定义" + 给 Nature options
- 等 Nature 决策后，新一轮 M-1.6 拿到 EscalationResolved + 决策——产 FindingCreated(concept_issue)

**为什么这个例子重要**：演示出口叠加时选 upstream——避免过早 commit 到具体方案。

---

## 案例不覆盖的怎么办

如果遇到这 12 例没覆盖的 fix 场景——按 mental model 推导：

1. **这是什么类型的 finding？** simple / complex
2. **closure_contract 可执行吗？** → feasibility check #6
3. **修复在 task scope 内吗？** → feasibility check #1
4. **修复违反共识吗？** → feasibility check #2
5. **需 supersede concept / obligation 吗？** → feasibility check #3 / #4
6. **需 Nature 产品判断吗？** → feasibility check #5
7. **如果全通过——三段式执行（主 / ripple / residual）+ 5 项 self-check + FixCompleted**

**emergence over enumeration**——12 例展示判断方式，不是穷尽所有 case type。
