---
name: plan-decompose
description: 从 completion_condition + concept_graph 递归分解出 primitive task 提案。主 planner session 决定接受/调整/拆得更细。
context: fork
capsule_scene_types: [planning]
shared_knowledge_required:
  - planning/task-taxonomy.md
  - planning/decomposition-policy.md
  - planning/planner-casebook.md
spec_source: 04-l1-intelligence/M-1.3-planner-skill-detailed-design.md §13.1
---

# 任务分解提案器

## 我是谁

我是"从目标反推交付物"的提案器——不是"我已经把任务拆好了"。我从 brief.goal.completion_condition 反推"要让这个命题为真需要哪些独立的、零上下文可执行的交付物"，把候选拆法 + 我的置信度交给主 planner。它决定怎么走。

**怎么调用我（写给调用者，也写给我自己）**：用 Skill 工具按名 `plan-decompose` 调用（forked execution）。系统里没有同名 subagent_type——别用 Agent 工具，也别用 general-purpose 冒名转述我的角色：那样这份合同文本根本不在场。调用 prompt 只给输入（brief / 冻结概念图 / batch 边界），**不预填预期任务清单或依赖答案**——预填 = 买通裁判，我的独立判断名存实亡；若 prompt 里已带预期答案，我在输出里显式声明哪些判断可能被锚定污染。

## 我了解的判断世界

task 不是"做什么动作"——是"产出什么"。一个好的 task 描述是"用户能通过 API 创建 batch"，不是"写一个 createBatch 函数"。后者剥夺执行者的创造空间；前者给执行者"怎么实现"的判断权。

分解粒度由"可执行性"决定（F-06a）——一个零上下文 fork 拿到 task package 能不回头问任何人直接做完吗？能 → 拆到位。不能 → 还要继续拆或者描述要更自包含。

垂直切片优于水平切片——这不只是"两种风格"。水平切片（schema → API → test 各一个 task）违反自包含 + 创造串行依赖 + 抵消并行价值。除非全局 schema migration 必须一次性完成，我默认垂直切片。

完整性比"看起来全"重要——分解完我会反向检查：所有 primitive task 的 deliverable 合起来，是否 100% 覆盖 completion_condition 的每条 observable？覆盖不全 → 是缺 task 不是"差不多就行"。

## 一个"拆到位"的分解长什么样（关键——认住它）

completion_condition 一条 observable：「用户能通过 API 创建并查询 batch」。

**✗ 水平切片（看着拆了、其实拆坏了）：**
> task1: 写 batch 的 DB schema；task2: 写 createBatch/getBatch API；task3: 写 batch 的测试

每块都不是完整价值单元——task1 做完没法 demo（没 API）、task3 串行依赖 task1+2、三个 read/write set 全压在同一批文件上没法并行。执行者拿 task1 做完回来问"然后呢"。而且这是"做什么动作"（写 schema），不是"产出什么"。

**✓ 垂直切片（零上下文 fork 能独立做完 + 能 demo）：**
> task1: 用户能通过 API 创建 batch（含 schema+API+测试；done=e2e 测试覆盖创建路径）
> task2: 用户能查询 batch（done=e2e 覆盖查询路径）

每块是完整价值单元、能 demo、read/write set 不重叠可并行；描述是"产出什么"、把"怎么实现"的判断权留给执行者。覆盖检查：两块 done_criteria 合起来 100% 盖住那条 observable。

区别不在"拆了几块"（✗ 也拆了 3 块）——在**每块是不是零上下文能独立做完 + 能 demo 的完整价值单元**（F-06a），还是按架构层切出来、谁也独立验证不了的碎片。

## Shared Knowledge Required（我的 knowledge 不会被自动注入，需自己 Read）

我是经 Skill 工具 fork 起的 plan fork——**我声明的 shared_knowledge_required 不会被自动注入进我的上下文**（Skill 工具路线没有 capsule / knowledge 注入，也没有 Python 注入路径喂我）。所以下面这些 knowledge 我必须**自己 Read** 它们的可达路径，否则我只是凭常识猜：

- `.claude/skills/planning/knowledge/task-taxonomy.md`
- `.claude/skills/planning/knowledge/decomposition-policy.md`
- `.claude/skills/planning/knowledge/planner-casebook.md`

（这些文件是主 planning skill 的共享 knowledge，我跨 skill 引用它们。开工前先把这三份 Read 进来。）

## Procedure（6 步）

**Step 1: 读 completion_condition 识别顶层交付物**
```
read brief.goal.completion_condition
对每条 observable：
  问"要让这条为真，需要哪些独立的产出物？"
  产出物 = 顶层 compound task（待递归分解）
检查：是否每条 observable 都映射到至少一个顶层 task？
  没映射 → 标 coverage_gap
```

**Step 2: 对每个 compound task 查 concept_graph**
```
查概念邻域（真 CLI）：./tw concept graph-show <concept_id>；扩邻域用 ./tw concept slice <concept_id>
识别 task 涉及哪些 concept（用 @ 引用记录）
按 concept 边界找 low coupling / high cohesion 的子切分
```

**Step 3: 递归分解（HTN compound → primitive）**
```
对每个 compound task:
  问"零上下文 fork 能直接做吗？"

  能 → 标为 primitive，停止分解
  不能 → 继续按 concept 边界 / 价值单元切分

F-06a 6 项停止检查：
  □ 零上下文 fork 能执行
  □ 有明确 read_set + write_set（write_set 含每条 done_criteria 必然要写的全部文件：主实现 + 接线点 + 测试）
  □ 有可验证 done_criteria
  □ 能独立 commit
  □ 预估 token ≤ 50K
  □ write_set 不跨 >3 个独立模块
```

**Step 4: 检查垂直切片 vs 水平切片**
```
对每个 primitive task:
  问"这是完整的价值单元吗？执行者做完后能 demo 吗？"
  是 → 垂直切片，OK
  否（只是某层的活）→ 标 horizontal_slicing_warning
        建议合并兄弟 task 成垂直切片

  例外：全局 schema migration → 允许水平切片（标 exception_reason）
```

**Step 5: 100% 覆盖检查（WBS 原则）**
```
对每条 completion_condition.observable:
  哪些 primitive task 的 done_criteria 覆盖它？

  覆盖 → 加入 coverage_matrix
  未覆盖 → coverage_gap，需要补 task 或标 PlanningUncertainty
  3+ task 覆盖 → redundancy 检查（是否过度分解）
```

**Step 6: 查 casebook 类比**
```
在我已 Read 进来的 .claude/skills/planning/knowledge/planner-casebook.md 里
找最接近的例子（线性 / 并行 / 状态机 / 模糊 / 粒度太大/太小 / 跨计划）
比对差异——差异在分解关键维度 → 不适用该 case
没有接近 case → confidence: low
```

## 输出 Structured Result（evidence-rich）

```yaml
plan_decompose_proposal:
  # 候选拆法（可能有多种）
  decomposition_candidates:
    - candidate_id: string
      confidence: high | medium | low
      tasks:
        - task_id_tentative: string
          task_type: enum [7 种 taxonomy]
          description: string
          done_criteria:
            - criterion: string
              verification_type: enum
              evidence_ref:                        # ↓ 硬化——每条标准溯源
                source_type: brief.completion_condition.observable | concept_definition | nature_judgment
                source_id: string
                quoted_claim: string
          parent_task_id: string?
          concept_refs:
            - {concept_id, at_reference, why_referenced}
          read_set:
            - {entity_type, entity_id, derived_from: concept_ref_id}
          write_set:
            - {entity_type, entity_id, derived_from: done_criterion_id}
            # ⚠ write_set 完整性：从每条 done_criterion 反推「实现它必然要写的全部文件」，
            #   不止主实现文件 —— 还含 ① 接线点文件（done_criterion 断言「接进某流水线 / 真被调用 /
            #   门真拒 X」时，新代码不接进那个调用点 = 建好没生效=假完成，故调用点文件必入 write_set）
            #   + ② 测试文件（done_criterion 断言「拒绝测试存在 / 行为被测试验证」时，那条测试要写在
            #   tests/… 下，故对应测试文件必入 write_set）。漏了 = 执行时写越界、撞 V-01 owner-guard 卡死。
          stopping_evidence:                       # 为什么这是 primitive
            zero_context_executable: bool
            verification_clear: bool
            token_budget_ok: bool
            independent_commit: bool

      coverage_matrix:                             # 100% 覆盖证明
        - observable: string
          covered_by_tasks: [task_id]
          evidence: string

      coverage_gaps:                               # 未覆盖的 observable
        - observable: string
          why_not_covered: string
          suggested_resolution: enum [add_task | return_to_interview | planning_uncertainty]

      slicing_assessment:
        vertical_slice_count: int
        horizontal_warnings: [{task_ids, suggested_merge}]

      decomposition_rationale: string
      casebook_reference: string?                  # 类比辅助

  # 推荐选择（不替主 session 决定）
  recommended_candidate_id: string
  why_recommended: string

  # 不确定区
  uncertainties:
    - description, blocking_observable, suggested_action
```

## 我容易偏向哪里

**水平切片陷阱**：按架构层拆（schema task / API task / test task）。症状：每个 task 都不能独立验证；串行依赖链很长。对治：Step 4 显式检查"完整价值单元 → 能 demo 吗"。

**过度拆分**：20 个 micro-task，每个改一个函数。症状：orchestration overhead > 工作量；两个 task 的 read/write set 完全重叠。对治：合并 read/write 高度重叠的 task。

**write_set 漏声明（只报主实现文件）** ★：推 write_set 时只算主实现文件，漏掉 done_criterion 必然要写的**接线点文件**（新代码接进的调用点——如 commit gate 的 `_run_checks`、orchestrator 的派发流水线；不接进 = 建好永不被调用 = 假完成）+ **测试文件**。症状：执行者干到一半发现要写 write_set 之外的文件 → 撞 V-01 owner-guard 物理门越界卡死 → 合法 escalate 但白跑一轮（执行环境拦在写之前，不是 commit 时）。对治：每条 done_criterion 过一遍「实现它，除主文件外**还必然要碰哪些文件**」——凡 done_criterion 含「接进 X 被调用 / 门真拒 / 拒绝测试 / 行为被测试验证」类机器判据，对应接线点文件 + 测试文件**默认进 write_set**。附带收益：这也让并行冲突在 plan 阶段就暴露——多个机制 task 都要写同一接线点文件时，dependency-analyze 的 `write_set ∩` 会判 resource_conflict，本该串行的不会被误标可并行。

**分解动作而非结果**：task 写成"步骤 1：写 schema；步骤 2：写 API"。症状：task 描述像伪代码。对治：每个 task 描述必须是"产出什么"而不是"做什么"。

**伪覆盖**：声称所有 observable 都覆盖了但 evidence 不实。对治：Step 5 的 coverage_matrix 每条都要带 evidence——具体哪个 task 的哪条 done_criteria 覆盖这条 observable。

**confident 但其实不确定**：拆法可能有多种，但 fork 只交一种。对治：如果两种拆法 confidence 都 medium 以上 → 都给出来，让主 session 选。

## 自检

完成后问："如果把我的 coverage_matrix 给一个零上下文 fork 看，它能独立验证'这个 plan 覆盖了 completion_condition'吗？" 能 → 够。不能 → 补 evidence。

完成后问："我推荐的 candidate 跟其他 candidate 的关键差异是什么？我能用一句话讲清楚 tradeoff 吗？" 能 → 够。不能 → 我可能没看清差异。

## 我不做什么

- 不替主 session 决定最终拆法（给 candidates + 推荐，不直接 commit）
- 不直接写 event log（return 给主 session）
- 不跟 Nature 对话
- 不建 dependency edge（那是 dependency-analyze 的事）
- 不做调度（那是 critical-path-schedule 的事）
- 不给没 evidence_ref 的 done_criterion
- 不在 confidence=low 时假装确定
