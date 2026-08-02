# Verify-Step & Dispute — Falsification 收敛机制

> 用途：F-08d Verify-step 独立过滤假阳性 + D novelty-gated dispute loop 防振荡——两者一起是 review 的收敛机制。
> 归属：M-1.5 review skill 知识库
> 基础：Anthropic Code Review 验证（< 1% FP rate）+ O-12 D 设计 + Popper falsificationism 哲学

---

## Verify-Step 的独立性是核心价值

### 为什么是独立 fork（不是方法论 fork 内嵌）

如果方法论 fork（执行路径 / 一致性 / 红队）**自己 verify 自己产的 finding**——利益冲突。

人性化的类比：你让一个人写一段代码然后让他自己评 code quality——他会倾向 confirm 自己写得好。

**Verify-step 必须是独立 fork**——不知道是哪个方法论 fork 产的 finding，纯粹基于物理 evidence 评估：
- 这个 finding 描述的失败场景能否被实际触发？
- 能 → verified（true positive）
- 不能 → rejected_false_positive
- 不能但不能完全排除 → unverified_inconclusive（保留给 author）

### Context Injection 配方

- **Prompt lens**：你不是 reviewer，是 **falsifier**——尝试 disprove 这个 finding
- **Mental model**：用 physical-grounded context（实际跑代码 / 查 schema / 看数据）验证
- **不被允许的**：相信 finding 描述 / 相信 reviewer 的 attempt 已经够好

### Anthropic Code Review 的 < 1% FP rate

Anthropic 公开数据：verify-step 独立 fork "attempts to disprove each finding"——把 false positive rate 压到 < 1%。

这是 review 系统能让 author **真的相信** finding 是真问题的关键。没有 verify-step → finding 噪声大 → author 学会 ignore findings → review 失去意义。

### Falsification framing

**verify-step fork 的指令**：

```
对每个 received finding：

1. 读 finding.falsification_evidence（方法论 fork 怎么尝试 disprove patch 的）
2. 用 physical-grounded context 独立尝试 disprove finding：
   - finding 描述的失败场景能不能实际触发？
   - finding 指向的 target.location 真的有这个问题吗？
   - finding 的 voi_rationale 在你的独立判断下成立吗？
3. 输出：
   - confirmed → FindingVerified (verified)
   - able_to_disprove → FindingVerified (rejected_false_positive)
   - inconclusive → FindingVerified (unverified_inconclusive)
```

注意——verify-step 的输出是 FindingVerified event，跟 reviewer fork 的 FindingCreated 是两个 event，都进 event log。

### Verify-step 输出三态（不只是 verified / rejected）

```yaml
verification_state: enum [
  verified,                  # 跑通模拟，confirmed
  rejected_false_positive,   # 模拟无法复现且证据足够 → 丢弃
  unverified_inconclusive    # 模拟无法复现但不能排除 → 保留给 author 自己判断
]
```

**为什么不只两态**：

- 全二分（verified vs rejected）→ verify-step under-confidence 会标 verified（怕错杀真问题）→ noise 上升
- 全二分 + 严判 → verify-step over-filter 把真 bug 当 FP 丢
- 三态——给 verify-step 一个"我不确定"的诚实选项——author 看到 inconclusive 时自己判断

---

## Dispute Loop = Author 反驳权 + Novelty-Gated 收敛

### Author 反驳权（v3.1 核心）

Reviewer 提 finding ≠ author 必须修——

- **accept**：author 同意 → 触发 M-1.6 fix（自动）
- **accept_no_fix**：author 同意是真问题但不修（advisory finding 或写反驳 + fallback 论证）
- **dispute**：author 反驳——必须写 dispute_reason
- **多次反驳 reviewer 不接受** → escalate Nature

### Dispute 必须带 Novelty（防振荡）

**问题**：反驳无意义振荡（A→A'→A→A' N 轮没新信息）——浪费时间 + 永不收敛。

**O-12 D 解**：每一轮 dispute supersede 必须声明这轮相比上一轮新增了什么——

```yaml
dispute_supersede_event:
  is_supersede: true
  superseded_event_id: <previous_finding_event_id>
  novelty:
    novelty_type: enum [new_evidence | new_interpretation | new_patch | new_risk_assessment | no_new_information]
    detail: string                            # 描述具体新增什么
  
  # 物理强制（M-0.5 NoveltyCheck）：
  #   - novelty_type=null 或空 → reject (novelty_missing)
  #   - novelty_type=no_new_information=true → reject (novelty_explicitly_absent)
  #     表示 loop 终止——必须三选一退出（withdraw / accept / escalate）
```

**循环终止条件**：

1. **reviewer withdraws finding**（看了 author 的 novelty 认同）→ FindingResolved(retracted)
2. **author accepts / fixes**（看了 reviewer 维持 + 新 novelty 认同）→ FindingResolved(confirmed_and_fixed) → M-1.6
3. **no_new_information = true**（双方没新东西）→ escalate Nature → FindingResolved(escalated)

### 为什么用 VoI 思想（不是几轮拍脑袋数字）

旧设计："最多 3 轮"—— 这是 arbitrary 数字。

**正确判据是 VoI**：这一轮 dispute 值不值得继续看它是否会**改变 finding 判断 / 修复方案 / 风险评估**。novelty-gated 就是这个 VoI 思想的物理实现——有新信息（VoI > 0）才继续，没新信息（VoI = 0）就终止。

### M-0.5 commit gate 怎么参与

dispute 走的是 envelope.patches[].patch_type=`finding_event` 路径——

```
1. Author 想 dispute finding F
2. 组装 envelope.patches:
     patch_type: finding_event
     target: F.finding_id
     is_supersede: true
     superseded_entity_ref: F.event_id
     novelty_type: ...
3. 同 envelope 提交 FindingDisputed EventIntent
4. M-0.5 NoveltyCheck §3.5.1 物理检查：
     - novelty_type 非空
     - 不是 no_new_information=true
5. 通过 → accepted → FindingDisputed 入 event log
6. Reviewer fork 看到 FindingDisputed → 决定是否维持 / withdraw
```

**这是为什么不需要 patch M-0.5**——NoveltyCheck 已经 cross-cutting（不只为 review，也为概念 supersede / 任务 supersede 等），review dispute 自然走它。

### Substantive novelty check（语义层）

物理 check 通过后，**可选抽样 audit**——"novelty 字段的内容是否真的有 substance"。这是 M-0.5 §3.5.2 设计——5% 抽样 + 24h 内振荡历史 100% 抽样。

这进一步防"novelty 字段填了但是假 novelty"（如反复说"我有新理解"但 detail 空泛）。

---

## Verify-step 跟 Dispute 的关系

```
方法论 fork → FindingCreated → Verify-step fork (path A, author_time):
                                    ├→ verified → 给 author 看
                                    │              ├→ author accept → M-1.6 fix → ★ Verify-step fork (path B, fix_after) ★
                                    │              ├→ author dispute → dispute loop
                                    │              └→ author accept_no_fix → advisory
                                    │
                                    ├→ rejected_false_positive → 丢弃（FindingVerified(false_positive_eliminated=true))
                                    │
                                    └→ unverified_inconclusive → 给 author 自己判断
```

**Verify-step 是"reviewer 内部的 disprove"**——降低 noise 给 author。
**Dispute loop 是"author 跟 reviewer 之间的对话"**——novelty-gated 防振荡。
**Bounded closure verification 是"fix 完整性验证"**（v2.1.2 新增）——防 cleanup ripple 漏修。

三者一起 = 完整的 review 收敛机制。

---

## Bounded Closure Verification（v2.1.2 元层升级，Patch h）

> 这是 fix-after mode 的核心协议——路径 B 不是 "free re-review"，是按 finding.closure_contract 的 **bounded verification + ripple scan**。

### 为什么需要 bounded

v2.1.1 self-review 教训：second-round verification 抓到 5 处 cleanup ripple（aggregation 旧表述 / caller_params 单字段 / batch 区分缺失 / 路径 B 调用未明）—— 暴露的不是 reviewer 不够聪明，是 **finding 没有 closure_contract，cleanup 完整性靠执行者自觉**。

修复路径不是无限递归 review，是给每个 finding 强制 closure 合约——fix-after mode 严格按合约验证，scope bounded。类比软件工程 regression test——不是重新测试整个宇宙，是 bug fix 真的解决了原 bug + 相关模块没引入新失败。

### Bounded verification 协议（路径 B 4 步）

1. **Closure criteria check**：逐条按 closure_contract.closure_criteria.verification_method 跑
   - `grep` → 实际跑 grep 命令，看 occurrences 是否符合 expected_result
   - `schema_check` → 验 schema 字段存在 / 类型正确
   - `projection_check` → M-0.2 projection state 满足
   - `manual_reasoning` → 需要语义判断（最弱，应少用）
   - `test` → 跑 test case
   - `replay` → event log replay 重现

2. **Ripple scan**（bounded scope = closure_contract.ripple_targets）
   - 对每个 ripple_target 检查 sync_status (synced / not_applicable / pending)
   - scope 严格限制——不扩到全文找新问题

3. **Forbidden residual check**（grep closure_contract.forbidden_residuals）
   - 每个 pattern 应 0 occurrences
   - 任一发现残留 → ripple_incomplete

4. **Scope 外发现规则**
   - 默认 new_unrelated_finding_logged 不阻塞
   - 例外：red-line / data loss / correctness-critical 才阻塞

### 输出 ReviewClosureState

```yaml
closure_state: enum [
  closed,                          # 全部 closure_criteria 满足 + ripple_targets synced + forbidden_residuals 0【终态】
  fix_insufficient,                # 原 finding 没修好 → reopen
  ripple_incomplete,               # ripple_targets 未全部 synced → reopen (scope bounded)
  new_unrelated_finding_logged     # scope 外问题 → 新 finding_id backlog【终态，不阻塞】
]
```

### 这就是反复 review 的工程化终止判据

v3.1 判据"第二轮不引入新问题" applied to review 系统自身 = bounded closure verification 协议。

**反复 review 的根源不是 reviewer 不够聪明，是 finding 没有 closure contract**——这是 v2.1.2 元层洞察。

---

## Falsificationism 贯穿全程

```
方法论 fork：     尝试 disprove patch         → FindingCreated
Verify-step fork：尝试 disprove finding       → FindingVerified
Author：          dispute（尝试 disprove finding 的判断）→ FindingDisputed
Reviewer 维持：    尝试 disprove author 的 dispute → 新 FindingVerified（novelty-gated）
Meta-review fork：尝试 disprove review_plan    → review_plan 的 finding
```

每一层都是 falsification——尝试 disprove 上一步的结论。这是 Popper 哲学在 review 系统的完整应用。

---

## 失败模式

| 失败模式 | 对治 |
|---|---|
| Verify-step over-filter（把真 bug 当 FP 丢）| 三态输出（含 unverified_inconclusive）+ 抽样 audit 反查 |
| Verify-step under-filter（走过场，全标 verified）| 独立 fork（不知道哪个方法论 fork 产的，纯凭物理 evidence）|
| Author 拒绝接受任何 finding（神谕化 author） | Dispute novelty-gated——dispute 必须带新证据 |
| Reviewer 不接受任何 dispute（神谕化 reviewer）| Reviewer 维持 finding 也必须带 novelty（反 dispute novelty 不充分）|
| Dispute loop 无意义振荡 | M-0.5 NoveltyCheck 物理拒 no_new_information |
| Dispute novelty 是假 novelty（说有但 detail 空泛）| Substantive audit（M-0.5 §3.5.2 抽样 5%）|
| Escalate Nature 阈值不清 | no_new_information=true 触发即 escalate（不需要 arbitrary 轮数）|
