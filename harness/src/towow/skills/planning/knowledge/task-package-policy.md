# Task Package Policy — 零上下文自包含 schema + 发布门槛

> 用途：planner fork 组装 TaskPackage 时的 schema 标准 + 发布前自检。
> 归属：M-1.3 planner skill 知识库

---

## TaskPackage 完整 Schema

```yaml
TaskPackage:
  # ─── 任务本体 ───
  task_id: string
  task_type: enum [implementation | test | documentation | config_migration | concept_update | review_prep | investigation]
  description: string                     # 人类可读的任务描述
  done_criteria:                          # 可验证的完成标准
    - criterion_id: string
      description: string
      verification_type: enum [test_passes | file_exists | concept_state_reached | manual_verify]
      verification_params: object
      evidence_ref:                       # ↓ M-1.3a 硬化——每条标准溯源到 brief
        source_type: brief.completion_condition | concept_definition | nature_judgment
        source_id: string
        quoted_claim: string
  
  # ─── 概念引用（@ 锁定）───
  concept_refs:
    - concept_id: string
      at_reference: string                # Typed ID（M-1.2 §4 语法）
      locking_policy: enum [pin_to_snapshot | auto_accept_latest | explicit_decision_required]
      why_locked: string                  # 为什么用这个锁定策略
  
  # ─── 文件引用 ───
  file_refs:
    - path: string
      purpose: enum [read_only | will_write | reference_doc]
  
  # ─── read/write 声明 ───
  read_set:
    - entity_type: string
      entity_id: string
      derived_from: string                # ↓ M-1.3a 硬化——从哪条 concept_ref 推的
  write_set:
    - entity_type: string
      entity_id: string
      derived_from: string                # 从哪条 done_criteria 推的
  
  # ─── 义务 ───
  active_obligations:
    - obligation_id: string
      from_event_id: string               # M-0.6 ObligationCaptured event_id
      why_active_for_this_task: string
  
  # ─── 约束 ───
  constraints:
    - constraint_id: string
      description: string
      evidence_ref:                       # 从 brief / nature_judgment / concept 哪来的
        source_type: string
        source_id: string
  
  # ─── 上下文 ───
  model_tier: enum [opus | sonnet]
  model_tier_rationale: string            # 为什么是这个 tier（model-tier-policy 决策依据）
  review_required: bool
  review_required_reason: string?
  estimated_token_budget: int?
  requires_owner_gate: bool               # fnd-r01-9: owner-gated 红线任务（完成判据=owner 批准，本无机器可判）
                                          # 标 true → manual_verify done_criterion 豁免 machine_check 强制。
                                          # 须与 canonical TaskNodeCreated.requires_owner_gate 一致（发布门交叉核对）。
  
  # ─── 完成验证（fork 执行完自检用）───
  completion_checks:
    - check_id: string
      check_type: enum [file_exists | test_passes | concept_state_reached | manual_verify]
      check_params: object
      maps_to_done_criterion: string      # 对应 done_criteria 的哪条
```

## TaskPackage 不能包含的（边界）

| 不包含 | 原因 |
|---|---|
| 完整代码实现 | task 是"做什么"不是"怎么做"——执行者有创造空间 |
| 其他 task 的内部细节 | 每个 task 独立——不该知道兄弟 task 的内部 |
| 调度信息（优先级 / 并行 slot）| 调度是 orchestrator 的事，不进 task package |
| 原始 brief 全文 | 只放 task 需要的 concept_refs / constraints |
| 历史决策过程 | 历史在 event log，不污染 task package |

## 发布门槛（所有满足才能 publish）

```
□ done_criteria 非空且每条都有 verification_type
□ done_criteria 每条可机器复算（带 gate-recomputable machine_check grep/test/git_diff，
   或 concept_state_reached）—— RUN-044 LB2 + finding-r05
□ done_criteria 每条都有 evidence_ref（M-1.3a 硬化要求）
□ read_set / write_set 非空
□ read_set 每条都有 derived_from 溯源
□ concept_refs 中无 stale reference
□ model_tier 已分配且有 rationale
□ review_required=true 的 task 有对应 review_prep task
□ active_obligations 每条有 from_event_id（不是凭空声明）
□ package_hash 已计算
□ 零上下文 fork 假装拿到 package 能直接做的自包含验证 passed
```

### 新增能力任务强制（live-fire 默认门·INV-A）

被 `@new-capability-task-classifier@v1` 三信号判别为**新增能力**的 task（write_set 命中能力性路径 daemon/orchestrator/gate/hook/feeder/l2、或 done_criterion 引用账本零出现的新事件类型、或误标 task_type 但命中前两信号），其 done_criterion 的 machine_check 必须满足：

1. **必须 test 型**：`verification_method=test`（即 `verification_type=test_passes + machine_check.verification_method=test`），**不允许 grep/file_exists/git_diff 型**（grep 扫不到账本里的 live 事件签名，见 `@live-fire-machine-check-contract@v1` 层2 说明）。
2. **test_selector 必须读真账本**：所指集成测试须调用 `EventLog.all_records()`（event_log.py）直接读真账本，断言目标事件存在 + 事件类型对 + provenance 非交互（daemon/orchestrator 自动产出，非人手 CLI session）。
3. **证据必须 live 路径产出**：不是"函数写了/测试加了/接线了/freeze 落账"，是"目标 canonical 账本事件被 live 路径产出，且 provenance 非交互"。

**scope**：仅对被判新增能力的 task 触发，纯文档/纯重构/纯配置（无能力性 write_set、不引新事件类型）不触发。owner-gated 任务仍按上方豁免处理（两条规则不相交：owner-gated 的 `manual_verify` 豁免 machine_check，但 owner-gated 的能力任务极少，正常新增能力任务必须满足本条）。

**为什么这条是硬门**：plan-freeze 第10道 blocking_check（`check_new_capability_tasks_have_livefire_machine_check`）在冻结时扫每个被判新增能力的 task——若无一条 machine_check 是 test 型且 test_selector 读真账本则 freeze fail-closed，不得冻结。execution 侧 completeness 总闸在 work complete 时另行复算 live-fire 真跑过。这是三处咬合（修1/修2/修3）的 advice 层，物理牙齿在修2/修3。

### owner-gated 红线任务例外（fnd-r01-9）

撞 owner 五类不可逆动作的红线任务（上线 / 删生产数据 / 动钱 / 对外发布 / 改公开承诺），其"完成"判据本质是 **owner 显式批准**，没有任何机器可判的工件。这类任务（plan 阶段标了 `requires_owner_gate=True` 的 TaskNode）：

- package 标 `requires_owner_gate: true` → 它的 `manual_verify` done_criterion **豁免** machine_check 强制（别为凑门伪造假 test_selector/git_diff —— 假 machine_check 在 work complete 复算时永久 fail-closed，把红线任务变成做不完的埋雷）。
- 豁免**不是自报就算**：发布门把 package 的 `requires_owner_gate` 与 canonical `TaskNodeCreated.requires_owner_gate`（权威源）**交叉核对**，不一致则拒（防"谁都能塞 true 跳过验证"）。
- 豁免**只对 `manual_verify` 开**：同一任务里混的 `test_passes` / `file_exists` 判据仍须带 machine_check。

## @ 引用锁定策略表

| task 类型 | 默认 locking_policy | 理由 |
|---|---|---|
| implementation | pin_to_snapshot | 代码基于确定的概念定义 |
| test | pin_to_snapshot | 测试基于确定的 expected behavior |
| documentation | auto_accept_latest | 文档反映最新定义 |
| config_migration | pin_to_snapshot | 迁移脚本基于确定的 before/after |
| concept_update | explicit_decision_required | 更新概念本身需要明确基于哪个版本 |
| review_prep | auto_accept_latest | review 看最新状态 |
| investigation | auto_accept_latest | 调研需要最新信息 |

**Override 规则**：默认 policy 可以被 task 内部需求覆盖——比如某个 documentation task 写的是"v1 API 文档"，应该用 pin_to_snapshot 锁住 v1 定义。每次 override 必须在 `why_locked` 字段解释。
