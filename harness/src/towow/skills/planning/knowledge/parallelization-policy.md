# Parallelization Policy — 并行决策规则

> 用途：planner fork 判断哪些 task 能并行、哪些不能、怎么调度并行组。
> 归属：M-1.3 planner skill 知识库

---

## 核心原则

**并行不是目标——正确性是目标。** Commit gate 会 reject 写冲突。Planner 的工作是在计划阶段提前识别冲突，避免执行阶段 reject 浪费工作。

## 并行 7 项硬条件（全部满足才能并行）

```
task A 和 task B 可以并行 iff:
□ dependency graph 中 A 和 B 之间无路径（无直接/间接依赖）
□ A.write_set ∩ B.write_set = ∅（无写冲突）
□ A.write_set ∩ B.read_set = ∅（B 不读 A 的产出）
□ B.write_set ∩ A.read_set = ∅（A 不读 B 的产出）
□ 不在同一 conflict_group 里
□ A 和 B 引用的 concept 没有互相 supersede 关系
□ 不同时触发同一 state_machine 的不同 transition
```

## 不能并行的情况

| 情况 | 原因 | 处理 |
|---|---|---|
| 写同一文件 | commit gate 会 reject 一个 | 串行或合并 |
| 一个读另一个写的 entity | 数据依赖 | 按依赖顺序 |
| 同时 supersede 同一 concept | 语义冲突 | 串行（先定哪个 supersede 胜出）|
| 同一 state_machine 的不同 transition | 状态冲突 | 按 state_machine 顺序 |
| 共享 obligation 且 obligation check 结果依赖执行顺序 | 义务冲突 | 串行或问 Nature |

## 并行度决策

```
DAG width = 同一"层"可并行的最大 task 数
实际并行度 = min(DAG width, 可用 session 数, 模型配额)

v3 初版默认：
  OPUS session: 通常 1-2 并行
  SONNET session: 通常 3-5 并行
  总并行: ≤ 6 fork 同时跑

关键路径上的 task 优先占 session。
```

## 并行风险检测

| 风险 | 检测 | 缓解 |
|---|---|---|
| Shared file 隐含依赖 | 检查 task 涉及的文件是否有隐含共享 | 补 resource_conflict 边 |
| Concept neighborhood 重叠 | 两个 task 修改同一 concept 的不同 field | 通常 OK（field 级不冲突）；如果 concept 级语义联动 → 加 semantic_dependency |
| 高 fan-out concept 被多 task 引用 | concept 被 >3 task 同时 read 且某 task 在 write | 不能并行——保护正在 write 的 task |
| Investigation task 结果未知 | 后续 task 依赖 investigation 产出 | 后续 task 等 investigation 完成才能并行执行 |

## Evidence 要求

并行决策输出必须含：

```yaml
parallel_assessment:
  - task_pair: [task_a_id, task_b_id]
    can_parallel: bool
    evidence_refs:                          # 必填——可追溯依据
      - source_type: read_write_set_analysis | concept_relationship | state_machine | dependency_graph
        source_id: string
        finding: string
    confidence: high | medium | low
    if_cannot_parallel:
      blocking_reason: string
      suggested_serialization_order: [task_id]
```
