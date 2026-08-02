# Outcome Events & Cascade — 4 种 outcome event + Event-driven cascade（v2.1.1）

> 用途：M-1.6 产 outcome event 时按这份文档定 event 形态 + O-14 语义解释字段。强调：M-1.6 产 event，不 cascade——cascade 是 v3 既有 mechanism 的 emergent property。
> 归属：M-1.6 fix skill 知识库
> 核心精神：existence → projection → circulation → response——M-1.6 只管 existence
> v2.1.1：5→4 outcome events（authority matrix + cascade_scope）

---

## V3 Cascade Mechanism 速览（M-1.6 不需要自造的）

| Mechanism | 作用 |
|---|---|
| **FindingCreated event 跨 skill 通信**（M-1.4 issue 机制）| 产 event 让其他 skill 接 |
| **F-04d 概念状态变迁链 SAGA** | 概念 state 变迁自动通知下游引用方 |
| **F-04e 字段级 @ 引用图** | 机器扫描"谁引用了" |
| **F-11 自动触发** | 任务完成后下游 session 确定性启动（智能合约逻辑）|
| **O-11 变更影响识别工作族** | 5 成员沿概念图边遍历找隐藏影响 |
| **O-14 涟漪 + 语义解释字段** | event 自带影响面 hint，下游 mechanism 精确识别 |

**M-1.6 只产 event + 填好 O-14 语义解释字段——其他完全不管**。

---

## 4 种 Outcome Event（v2.1.1）

### Event 1: FixCompleted（最常见）

**触发场景**：feasibility check 全通过 + 三段式执行 + 5 项 self-check passed

**Schema**（payload）：

```yaml
event_type: FixCompleted
target_entity_type: patch
event_category: state_transition

payload:
  fix_id: string
  finding_id: string                # 修复的 finding
  task_id: string
  fork_session_id: string
  
  # Closure contract execution 结果（双层 verification 的内层——M-1.6 self-check）
  self_verification:
    criteria_results:               # 逐条 closure_criteria 验证
      - criterion: string
        passed: bool
        verification_method: enum [grep | schema_check | projection_check | manual_reasoning | test | replay | git_diff]
        actual_result: string
        evidence_artifact: string   # command output / file path / test report
    ripple_results:
      - target_artifact: string
        target_location: string
        sync_status: enum [synced | not_applicable]
        diff_summary: string
    residual_check_results:
      - pattern: string
        found_occurrences: int      # 应该 = 0
        check_method: enum [grep | schema_check | manual_reasoning]
        evidence: string
  
  # F-09a 共识 + review plan 遵守 evidence
  consensus_compliance:
    new_concepts_introduced: bool       # 应该 = false（除非走了 supersede 协议）
    obligation_violations: [...]?       # 应该 empty
    review_plan_risk_surfaces_bypassed: [...]?  # 应该 empty

  # === O-14 语义解释字段（关键 ★）===
  semantic_upgrade_declaration:
    affected_concepts: [concept_id]       # 修复涉及的概念
    concept_state_changes: [               # 概念状态变迁（如有）
      - concept_id: string
        from_state: string
        to_state: string
    ]
    affected_consumers_hint: [consumer_id]?  # 影响的消费方 hint（不必全集——精确级联由 O-11 work-family 处理）
    affected_ripple_artifacts: [artifact_id]  # 实际同步更新的位置
    
  patch_summary: string             # 简短描述这次 fix 做了什么

provenance_hint:
  actor_type: agent_session
  actor_id: "m16.fix.{fix_id}"
  skill_id: "M-1.6"

base_classification: abstractable_process
```

**Cascade**（v3 既有 mechanism 自动接）：
- F-11 自动触发 M-1.5 fix-after mode 路径 B → bounded closure verification
- M-1.5 fix-after 输出 FindingResolved（closure_state ∈ {closed / fix_insufficient / ripple_incomplete / new_unrelated_finding_logged}）
- F-04e 引用图机器扫描——semantic_upgrade_declaration.affected_concepts 自动通知下游引用方
- O-11 change impact analysis 沿 affected_consumers_hint 自动跑

---

### Event 2: FindingCreated（issue 反向给 M-1.5）

**触发场景**：feasibility check #6 不通过——closure_contract 自身不可执行

**Schema**（复用 M-1.5 finding schema）：

```yaml
event_type: FindingCreated
target_entity_type: finding
event_category: state_transition

payload:
  finding_id: string
  severity: major                   # closure_contract 不可执行是严重问题
  source:
    type: model_review              # 复用既有 finding type
    agent_id: "m16.fix.{fix_id}"    # 注明这个 finding 来自 fix skill 反向
  description: string               # "原 finding F-XXX 的 closure_contract 不可执行——具体哪一项"
  target:
    artifact: "finding:{F-XXX}"     # 反向指原 finding
    location: "closure_contract.<具体字段>"
  suggested_fix_layer:
    primary: review_plan            # 让 M-1.5 改 closure_contract
    rationale: string
  
  voi_rationale: string             # 为什么这件事值得 review skill 重新设计
  falsification_evidence:
    attempt: "M-1.6 尝试 verify closure_criteria.X 时发现 verification_method 不可执行——具体 evidence"
    result: confirmed
  
  # M-1.6 反向 finding 必须有自己的 closure_contract（合约同构）
  closure_contract:
    closure_criteria:
      - condition: "M-1.5 重新设计的 closure_contract 已满足可执行性要求"
        verification_method: manual_reasoning  # M-1.5 reviewer 自审
        expected_result: "新 closure_contract 通过 well-formed self-check"
    ripple_targets: []              # 反向 finding 通常无 ripple
    forbidden_residuals: []

provenance_hint:
  actor_type: agent_session
  actor_id: "m16.fix.{fix_id}"
  skill_id: "M-1.6"
```

**Cascade**（v3 既有 mechanism 自动接）：
- M-1.5 issue 机制接 finding——M-1.5 重新跑 review_plan 修订（design-time mode）
- 输出新的 closure_contract → 重启 fix cycle

---

### Event 3: RePlanTriggered

**触发场景**：feasibility check #1 不通过——修复越过 task scope

**Schema**（复用 M-1.4 既有 RePlanTriggered）：

```yaml
event_type: RePlanTriggered
target_entity_type: task
event_category: state_transition

payload:
  task_id: string                   # 原 task
  trigger_source: fix_scope_violation
  trigger_evidence:
    fix_id: string
    finding_id: string
    actual_scope_required: [...]    # 实际需要的 scope
    declared_scope: [...]           # 原 task declared scope
    scope_violation_detail: string
  
  affected_tasks: [task_id]         # 影响的其他 task（如有）
  
  # === O-14 语义解释字段 ===
  semantic_upgrade_declaration:
    affected_concepts: [concept_id]
    requires_replanning_dimensions: [dimension]  # planner 需要重新评估的维度

provenance_hint:
  actor_type: agent_session
  actor_id: "m16.fix.{fix_id}"
  skill_id: "M-1.6"
```

**Cascade**（v3 既有 mechanism 自动接）：
- F-11 自动触发 M-1.3 / F-14 replan flow
- M-1.3 重新规划 → 产新 TaskPackagePublished
- 原 task 可能被 supersede 或拆分

---

### ~~Event 4: Supersede Events~~ → v2.1.1 Authority Matrix（M-1.6 不直接产）

> **v2.1.1 Patch B**：M-1.6 不直接产 ConceptCreated(supersede) / ObligationEvolved / ObligationRetired / ReviewPlanSuperseded。
> 原 Event 4 由 FindingCreated(finding_kind=concept_issue / obligation_issue / review_plan_issue) 取代——authority owner 接管后自己决定是否 supersede。
> 详见主文档 §3.5 Authority Matrix。

**触发场景不变**：feasibility check #2/#3/#4 不通过——修复需要改 concept / obligation / review_plan

**但 M-1.6 的动作变了**：
- 旧（v2.1）：M-1.6 直接产 ConceptSuperseded event
- **新（v2.1.1）：M-1.6 产 FindingCreated(finding_kind=concept_issue) → M-1.2 接管 → M-1.2 决定是否 supersede → M-1.2 产 ConceptCreated(is_supersede=true)**

authority owner 收到 FindingCreated 后走自己的 supersede 协议——M-1.6 不参与。

---

### Event 4: EscalationRaised

**触发场景**：feasibility check #5 不通过 OR 多轮 fix 仍不闭合 + 无 novelty

**Schema**（详见 escalation-product-language.md）：

```yaml
event_type: EscalationRaised
target_entity_type: escalation
event_category: state_transition

payload:
  escalation_id: string
  fix_id: string
  finding_id: string
  
  # === Nature-facing 字段（产品语言，F-09b）===
  nature_facing_summary: string         # 产品语言总结
  what_was_tried: string                # 试过什么
  why_it_did_not_close: string          # 为什么没闭合
  decision_needed_from_nature: string   # 需要 Nature 决定什么
  options:                              # Nature 的选项
    - option: string
      tradeoff: string
      product_impact: string
  
  engineering_detail_ref: string        # 工程 detail 单独链接

provenance_hint:
  actor_type: agent_session
  actor_id: "m16.fix.{fix_id}"
  skill_id: "M-1.6"
```

**Cascade**（v3 既有 mechanism 自动接）：
- UI projection 自动呈现给 Nature（O-12 E）
- Nature 决策后产 EscalationResolved event（M-2.3 escalation 流处理）

---

## O-14 语义解释字段——为什么必须有

O-14 涟漪原则：所有阶段 skill 产出事件必须附带"语义解释附件"——声明这次产出涉及的概念图边 / 关系类型等。

**M-1.6 outcome event 的 O-14 字段 = `semantic_upgrade_declaration`**：

```yaml
semantic_upgrade_declaration:
  affected_concepts: [concept_id]       # 这次产出涉及的概念
  affected_consumers_hint: [...]?       # 影响的消费方 hint（非全集）
  concept_state_changes: [...]?         # 概念状态变迁（如有）
  affected_ripple_artifacts: [...]      # 实际改动的位置
```

**为什么是 hint 不是全集**：
- 精确级联由 O-11 change impact analysis 处理（沿概念图边遍历）
- O-14 字段是给下游 mechanism 的 starting point hint——加速但不替代
- M-1.6 不强求列全所有 consumer——可能遗漏，由 F-04e 引用图扫描补全

**v3 整体精神同构**：
- M-1.6 产 event（existence）
- O-14 字段是 projection 增量推导的 hint
- F-04d / F-11 cascade（circulation）
- 下游 skill 反应（response）

---

## 关键约束：M-1.6 不主动 cascade

**M-1.6 不允许做的事**：
- ❌ 直接 call M-1.5 fix-after（"trigger review again"）
- ❌ 直接 notify consumer skill（"tell consumer X about this change"）
- ❌ 直接 dispatch RePlan skill（"start replan"）
- ❌ 直接 send Nature notification（"alert Nature"）

**M-1.6 允许做的事**：
- ✅ 产 outcome event（含 O-14 语义解释）
- ✅ envelope.self_check 含完整 self-verification evidence
- ✅ envelope.uncertainties 标 "下游 cascade 我无法预测的部分"

让 v3 既有 mechanism 自动接管——这是 v3 event-driven 设计的核心精神。

---

## 失败模式自检

每次产 outcome event 前问自己：

1. **"我有 cascade 想法吗（call / trigger / dispatch）？"** → 是 → imperative cascade 倾向，改成产 event
2. **"我的 O-14 语义解释字段填了吗？"** → 必填 affected_concepts / affected_ripple_artifacts
3. **"我的 outcome event 是 4 种之一吗？"** → 不是 → 检查是不是 zombie state
4. **"我 self-check 5 项全 passed 吗？（FixCompleted 特有）"** → 否 → 不能产 FixCompleted

---

## 跟 M-1.5 v2.1.2 fix-after mode 的双层 verification 链路

```
M-1.6 feasibility check 全通过
  ↓
M-1.6 三段式执行（主修 / ripple / residual）
  ↓
M-1.6 envelope.self_check 5 项 blocking_check 全 passed
  ↓
M-0.5 commit gate accept → FixCompleted event 入 log
  ↓
F-11 自动触发 M-1.5 fix-after mode（无需 M-1.6 主动 call）
  ↓
M-1.5 verify-step fork 路径 B（bounded closure verification）
  ↓
M-1.5 输出 FindingResolved（含 closure_verification.closure_state）
  ├── closed → 终态
  ├── fix_insufficient → FindingCreated reopen → 新一轮 M-1.6
  ├── ripple_incomplete → FindingCreated reopen（scope bounded 到 ripple_targets）→ 新一轮 M-1.6
  └── new_unrelated_finding_logged → 终态 + 新 finding backlog
```

**M-1.6 不知道（也不需要知道）M-1.5 fix-after 怎么跑**——只管产 FixCompleted。F-11 是 mechanism。
