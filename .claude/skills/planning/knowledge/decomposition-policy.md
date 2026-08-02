# Decomposition Policy — 怎么拆、什么时候停、怎么检查覆盖

> 用途：planner fork 从 completion_condition + concept_graph 推导 task graph 的判断规则。
> 归属：M-1.3 planner skill 知识库

---

## 分解方法——HTN 递归 + WBS 100% 原则

### Step 1: 从 goal.completion_condition 识别顶层交付物

```
brief.goal.completion_condition 是一个可判定命题
→ 分解成 "要让这个命题为真需要哪些独立交付物"
→ 每个交付物是一个 compound task（需进一步分解）
→ 交付物是 "结果" 不是 "动作"（WBS 原则 1）
```

**例**：completion_condition = "EventLog 支持 TransactionBatch 原子写 + 所有 writer 已迁移"
→ 交付物 1: TransactionBatch 实现
→ 交付物 2: Writer 迁移
→ 交付物 3: 集成测试覆盖

### Step 2: 递归分解（HTN compound → primitive）

```
对每个 compound task:
  问："这个 task 一个零上下文 fork 能直接做吗？"
  
  能 → primitive task（停止分解）
  不能 → 继续分解：
    查 concept_graph 看这个交付物涉及哪些 concept
    按 concept 边界切分子交付物（low coupling / high cohesion）
    每个子交付物成为一个 child compound task
    递归
```

### Step 3: 停止标准（F-06a）

```
primitive task 的判定——所有必须满足：
□ 零上下文 fork 能执行（不回头问）
□ 有明确 read_set + write_set
□ 有可验证 done_criteria
□ 能独立 commit
□ 预估 token ≤ 50K（一次 fork 做完）
□ write_set 不跨 >3 个独立模块
```

### Step 4: 完整性检查（100% 原则）

```
分解完后反向检查：
  所有 primitive tasks 的 deliverable 合起来
  是否 100% 覆盖 completion_condition 的每条 observable？
  
  覆盖 → pass
  缺口 → 补 task 或标 PlanningUncertainty
  多余 → 检查是否过度分解（merge 或标 optional）
```

---

## 分解是设计决策——不是唯一正确答案

同一个 completion_condition 可以有多种拆法。Planner 选哪种基于人格偏好——

| 偏好 | 含义 |
|---|---|
| 模块化（low coupling / high cohesion）| 优先按概念边界切分，最小化 task 间依赖 |
| 垂直切片 | 优先产完整价值单元，而非按架构层切 |
| 关键路径优先 | 先拆关键路径上的 task（影响整体 makespan）|
| 渐进细化 | 近期 task 拆细，远期 task 粗留（progressive elaboration）|

**Planner 不需要解释为什么选这种拆法**——但选错了（如水平切片导致串行依赖过多）会被 consistency-verify 发现。

---

## completion_condition → task 的覆盖证明

### Coverage Matrix

```
每条 completion_condition.observable 对应至少一个 task 的 done_criteria：

| observable | covered_by_task | evidence |
|---|---|---|
| "EventLog 支持 batch" | task-001 | done_criteria: batch write API passes integration test |
| "所有 writer 迁移" | task-002, task-003 | done_criteria: each writer uses new API |
| ... | ... | ... |
```

### Coverage Gap

如果某条 observable 没有 task 覆盖：
- 可能是分解遗漏 → 补 task
- 可能是 brief 中该 observable 不清晰 → 产 PlanningUncertainty → 可能回 M-1.1 追问

### Coverage Redundancy

如果某条 observable 被 3+ task 覆盖：
- 可能是过度分解 → 合并
- 可能是必要冗余（如 integration test 跟 unit test 都覆盖同一 observable）→ 保留但标注

---

## 什么时候不该继续分解

| 情况 | 做法 |
|---|---|
| task 已满足所有停止标准 | 停止——不为"看起来更细"继续拆 |
| task 只能拆成 2 个完全依赖的子 task | 不拆——串行依赖 = 假并行 |
| task 涉及跨计划概念但当前信息不足 | 标 investigation task，先调研再决定拆不拆 |
| brief 的 completion_condition 太抽象 | 不猜——产 PlanningUncertainty，回 M-1.1 追问 |
