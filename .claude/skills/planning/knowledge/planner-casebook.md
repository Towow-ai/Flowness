# Planner Casebook — 12 个任务规划范例

> 用途：fork 在做分解 / 依赖判定 / 并行决策时的具体参考。
> 归属：M-1.3 planner skill 知识库

---

## 例 1: 简单线性计划（3 task）

**场景**：brief 说"给 EventLog 加 batch write API"。概念图有 EventLog entity。
**分解**：
- task-001: 实现 batch write API（write_set: event_log module）
- task-002: 单元测试（read_set: event_log module; 依赖 task-001）
- task-003: 文档更新（read_set: event_log API doc; 依赖 task-001）
**依赖**：001 → 002（data）; 001 → 003（data）。002 和 003 可并行。
**为什么不拆更细**：task-001 一个 fork 能做完（一个模块的 API 改动）。

## 例 2: 多文件并行计划（5 task）

**场景**：brief 说"迁移 5 个 writer 模块到新 batch API"。每个 writer 是独立模块。
**分解**：每个 writer 一个 task（垂直切片），各自含代码改 + 测试。
**依赖**：5 个 task 互相无依赖（各自 write 不同文件）。
**并行**：5 个全并行（write_set 不重叠）。实际并行度取决于 session 数。
**注意**：不要拆成"先做所有 schema 改动 → 再做所有 API 改动 → 再做所有测试"（水平切片陷阱）。

## 例 3: 有状态机约束的计划

**场景**：brief 说"Run 的状态机加 archived state"。Run 有 state_machine（created → running → completed → failed）。
**分解**：
- task-001: 更新 Run state_machine 定义（concept_update task; write: Run concept + state_machine）
- task-002: 实现 archived transition 逻辑（implementation; 依赖 001）
- task-003: 测试 transition guard + SAGA 补偿（test; 依赖 002）
**依赖**：001 → 002 → 003（state_machine_transition_dependency——必须先定义 state 才能实现 transition）。
**不能并行 002 和 001**：002 基于 001 的新 state 定义。

## 例 4: 有 @ 引用锁定的计划

**场景**：brief 说"CapsuleAssembler 的 assemble 接口要支持 shared_knowledge_required"。M-1.2 概念图有 @api_contract:CapsuleAssembler.assemble。
**分解**：
- task-001: 扩展 assemble 接口（implementation; concept_ref: @api_contract:CapsuleAssembler.assemble@lock_event_123; locking=pin_to_snapshot）
- task-002: 更新所有调用方（implementation; 依赖 001; concept_ref 同上）
- task-003: 集成测试
**@ 锁定**：task-001 和 002 都 pin_to_snapshot——执行期间 assemble 接口定义不能变。

## 例 5: 有 red-line obligation 的计划

**场景**：brief 说"添加 cold storage archive 功能"。有 obligation: "event log 是唯一事实源"（red_line）。
**分解**：
- task-001: 实现 prepare/commit/finalize 三段式（implementation; write_set: events/cold/ module）
- task-002: 实现 crash recovery（implementation; 依赖 001）
- task-003: review_prep（review_prep; review_scope: task-001+002; 标 review_required=true——因为涉及 red_line obligation）
**特殊**：task-001 和 002 的 active_obligations 含 "event_log_is_source_of_truth"。Task package 必须显式声明。
**model_tier**：opus（高风险——违反 obligation = 系统正确性问题）。

## 例 6: completion_condition 模糊时

**场景**：brief 说"改善用户体验"。completion_condition: "用户操作流程更顺畅"。
**问题**：不可机械验证——"更顺畅"不是 observable。
**正确做法**：不猜——产 PlanningUncertainty event，回 M-1.1 追问"顺畅具体指什么 observable"。
**错误做法**：自己编一个 "页面加载时间 < 2s" 当 completion_condition（那是 planner 猜的不是 Nature 说的）。

## 例 7: task 粒度太大

**场景**：初始分解产出一个 task "重构整个 commit gate 模块"。
**太大信号**：write_set 涉及 M-0.5 commit gate 的所有子组件（5+）；预估 token > 100K；需要中途决策。
**正确拆法**：按 commit gate 的检查 pipeline 各阶段拆——
- task-001: VersionCheck 重构
- task-002: WriteConflictCheck 重构
- task-003: ObligationCheck 重构（依赖 001 和 002 的新接口）
- task-004: 集成测试
**拆法依据**：每个 check 是独立子模块（high cohesion），check 之间通过 pipeline 接口连接（low coupling）。

## 例 8: task 粒度太小

**场景**：planner 拆出 20 个 task，每个只改一个函数签名。
**太小信号**：orchestration overhead > 实际工作量；这 20 个函数签名变更是同一个 concept 变更的连锁反应。
**正确做法**：合并为 2-3 个 task，按文件/模块边界分组。
**合并依据**：read_set/write_set 高度重叠的 task 应该合并。

## 例 9: dependency edge 判定（data vs semantic）

**场景**：task A 定义 RetentionPolicy concept。task B 实现 archive 逻辑（要读 RetentionPolicy）。
**data_dependency**：B.read_set 含 RetentionPolicy → B depends_on A。
**另一个 task C**：实现 GC 逻辑。C 不直接读 RetentionPolicy，但 GC 的语义假设"retention policy 已经定义好了"。
**semantic_dependency**：C 对 A 有 semantic_dependency——如果 A 的 RetentionPolicy 定义变了，C 的逻辑可能需要 re-check。
**区别**：data_dependency 是硬的（C 不能在 A 之前开始）；semantic_dependency 是 medium（C 可以先做但 A 完成后要 re-check）。

## 例 10: 哪些情况必须 defer / ask Nature

| 情况 | 做法 |
|---|---|
| completion_condition 不可机械验证 | 产 PlanningUncertainty → 回 M-1.1 追问 |
| 两种拆法都合理但 trade-off 不同 | 给 Nature 两个方案 + tradeoff，不替他选 |
| task 涉及跨计划概念但另一个 plan 还没完成 | 标 cross_plan_dependency → planner 决定等还是先做 |
| brief 中某个约束跟当前实现矛盾 | 产 PlanningUncertainty → 不猜"Nature 是什么意思" |
| 资源约束下关键路径太长 | 报告给 Nature——"按当前资源预估需要 X 时间，是否接受或调整 scope" |

## 例 11: 跨并行计划协调

**场景**：plan A 在加 batch write API；plan B 在加 cold storage archive。两者都改 EventLog 模块。
**协调点**：
- 共享 concept: EventLog
- write_set 冲突: 两个 plan 的 implementation task 可能改同一文件
**planner 行为**：
- 查 task_graph projection 看 plan B 的进度
- 如果 plan B 已完成 EventLog 相关 task → plan A 基于 plan B 的产出
- 如果 plan B 还在进行 → 标 cross_plan_conflict → 建议：plan A 的 EventLog task 安排在 plan B 之后，或合并
**注意**：planner 不能替另一个 plan 做决策——只能建议协调方案，由 Nature 决定。

## 例 12: investigation task 触发 re-plan

**场景**：plan 需要"支持 gRPC 接口"。Planner 不确定当前框架是否支持 gRPC。
**正确做法**：
- task-001: investigation（调研当前框架 gRPC 支持情况）
- task-002-N: 待 task-001 完成后根据结果拆（conditional decomposition）
**task-001 产出**："框架支持但需要插件" → planner re-plan：加 task-002 安装插件 + task-003 实现接口。
**task-001 产出**："框架不支持 gRPC" → planner re-plan：用 REST 替代 + 回 M-1.1 更新 completion_condition。
**关键**：investigation 的 done_criteria 是"产出信息让 planner 能继续拆"，不是"写代码"。
