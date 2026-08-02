# Cross-Plan Coordination Policy — 跨并行计划的协调决策

> 用途：planner fork 在面对多个并行 plan 时的协调判断规则。
> 归属：M-1.3 planner skill 知识库
> 性质：v3 独有维度——传统 PM/OR 假设单一项目，没有现成方法论。

---

## 冲突类型分级

| 类型 | 严重度 | 例子 |
|---|---|---|
| **shared_concept_read** | none | plan A 和 plan B 都 @ 引用 concept X（都只读）→ 正常共享，不冲突 |
| **shared_concept_one_writer** | low | plan A 改 concept X，plan B 读 concept X → B 需要锁定版本 |
| **shared_concept_concurrent_write** | medium | plan A 和 plan B 都要 supersede concept X → 必须协调（哪个先） |
| **write_set_overlap** | high | plan A 和 plan B 的 task 写同一文件 → commit gate 会 reject |
| **obligation_conflict** | high | plan A 满足 obligation Y，plan B 违反 obligation Y → 互斥 |
| **state_machine_race** | critical | plan A 和 plan B 同时触发同一 concept 的不同 transition → 状态机不确定 |

## 决策规则

### 规则 1: shared_concept_read → 不需协调

两个 plan 都只读 concept X。X 不变，两个 plan 都基于同一版本。**默认允许并行，不需协调动作。**

### 规则 2: shared_concept_one_writer → 读者 pin 版本

```
if plan_A.write_set contains concept_X 
   AND plan_B.read_set contains concept_X:
  plan_B.task.concept_refs[X].locking_policy = pin_to_snapshot
  plan_B 基于 concept_X 在 plan_B 启动时的版本工作
  plan_A 完成 supersede 后，plan_B 的 stale_reference 由 M-2.1 识别
```

### 规则 3: shared_concept_concurrent_write → 必须串行或合并

```
if plan_A 和 plan_B 都要 supersede concept_X:
  
  分支 A: 改动目标兼容（都是加字段 / 加状态）
    → 建议合并这两个 supersede 到一个 plan
    → 由 Nature 拍板
  
  分支 B: 改动目标矛盾（plan_A 改定义 + plan_B 改 scope）
    → 必须串行：先 commit 哪个？
    → 触发 PlanningUncertainty.resolution_action=ask_nature
    → Nature 决定优先级
```

### 规则 4: write_set_overlap → 触发 PlanningUncertainty

```
if plan_A.task.write_set ∩ plan_B.task.write_set ≠ ∅:
  
  分支 A: 写不同字段（粒度足够细）
    → 检查 envelope 粒度——M-0.5 commit gate 是按 entity 还是按 field 判断冲突？
    → 按 entity → 视为冲突（保守）
    → 按 field → 允许并行（需 M-0.5 支持）
  
  分支 B: 真正写同一资源
    → 不能并行
    → 决策：plan A 等 plan B / plan B 等 plan A / 合并 plan
    → 触发 PlanningUncertainty.resolution_action=ask_nature
```

### 规则 5: obligation_conflict → 阻塞 + 升级

```
if plan_A 的 task 显式 maintain obligation_Y 
   AND plan_B 的 task 显式 violate obligation_Y (or vice versa):
  
  这是设计层冲突，不是规划层能解决的
  → 阻塞两个 plan
  → 升级到 Nature
  → 触发 PlanningUncertainty.resolution_action=ask_nature + severity=blocking
```

### 规则 6: state_machine_race → 必须串行

```
if plan_A.task 和 plan_B.task 触发同一 concept 的不同 state transition:
  → 必须串行（按状态机定义的合法顺序）
  → 如果两个 transition 都从同一 state 出发但去不同目标 → 互斥决策
  → 触发 PlanningUncertainty.resolution_action=ask_nature
```

---

## 协调决策矩阵

```
检测到冲突 → 按类型走规则 → 4 种可能动作：

1. 不需协调（shared_concept_read）—— 默认并行
2. 自动协调（shared_concept_one_writer）—— pin 版本即可
3. Planner 建议合并/串行（write_set_overlap with field-level)—— 标 advisory
4. 升级 Nature（concurrent_write / obligation_conflict / state_machine_race / 严重 write_set_overlap）—— 必须 ask
```

## Cross-Plan 查询接口（消费 M-0.2）

```
list_active_plans() → [plan_id]
get_plan_status(plan_id) → {tasks, current_progress, write_set, read_set}
get_plan_concept_writers(concept_id) → [plan_id]（谁在写这个 concept）
get_plan_concept_readers(concept_id) → [plan_id]
detect_write_set_overlap(this_plan_tasks, other_plan_id) → [overlap_entity]
```

## Evidence 要求

每个 cross-plan 协调判断必须带：

```yaml
cross_plan_assessment:
  - this_plan_id: string
    other_plan_id: string
    conflict_type: enum [...6 种]
    severity: enum [none | low | medium | high | critical]
    evidence_refs:
      - source_type: plan_event_query | concept_writer_overlap | obligation_check | state_machine_inspection
        source_id: string
        finding: string
    suggested_action: enum [
      proceed_in_parallel,
      pin_concept_version,
      serialize_after_other_plan,
      serialize_before_other_plan,
      suggest_plan_merge,
      escalate_to_nature
    ]
    rationale: string
```

---

## 不该做的

| 误做法 | 正确做法 |
|---|---|
| Planner 自己决定哪个 plan 先做 | shared_concept_concurrent_write 等高严重度冲突——必须 ask_nature |
| 假设其他 plan 会完成 | 不预测——基于当前已 commit 的状态决策 |
| 强行让 plan A 等 plan B（A 是关键路径）| 升级到 Nature——这是优先级决策不是规划决策 |
| 忽略 shared_concept_read（"反正都只读"）| 仍要标在 cross_plan_assessment——审计需要看到读了什么 |
