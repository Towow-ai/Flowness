# Dependency Policy — 6 种依赖类型的判定与建边规则

> 用途：planner fork 在 task graph 中建立依赖边的判断依据。
> 归属：M-1.3 planner skill 知识库

---

## 核心原则

依赖不是另起一套——**依赖来自 task 的 input/output 关系**（O-03 共同原则 4）。

```
如果 task B 的 read_set 包含 task A 的 write_set 中某个 entity
→ B depends_on A（data_dependency）
```

其他依赖类型是对这条基础规则的扩展。

## 6 种依赖类型

### 1. data_dependency（数据依赖）

**判定**：task B 读 task A 写的 entity。
**证据**：B.read_set ∩ A.write_set ≠ ∅
**强度**：hard（B 必须等 A commit 后才能开始）

### 2. ordering（顺序依赖）

**判定**：task B 不读 A 的产出，但 B 必须在 A 之后（如 deploy 必须在 test 之后）。
**证据**：来自 concept_graph 的 state_machine transition ordering 或 workflow 定义。
**强度**：hard（违反顺序 = 逻辑错误）

### 3. semantic_dependency（语义依赖）

**判定**：task B 依赖 task A 建立的概念理解（不是数据，是语义上下文）。
**证据**：B 的 concept_refs 指向 A 要 supersede / define 的概念——A 完成前概念定义不稳定。
**强度**：medium（可以基于旧概念先做，但 A 完成后可能要 re-check）

### 4. resource_conflict（资源/写冲突）

**判定**：task A 和 B 写同一个文件/entity——不能并行执行。
**证据**：A.write_set ∩ B.write_set ≠ ∅
**强度**：hard（并行写 = 冲突，commit gate 会 reject 一个）
**处理**：不是加边——是**不能并行调度**。标 conflict_group。

### 5. review_dependency（review 依赖）

**判定**：task B 是 task A 的 review / review_prep。
**证据**：B.task_type = review_prep AND B.review_scope includes A。
**强度**：hard（A 完成后 B 才有意义）
**特殊**：review task 跟 fix task 之间也有 review_dependency。

### 6. state_machine_transition_dependency（状态机转移依赖）

**判定**：task B 要把某 concept 从 state X 转到 state Y，但 task A 要先把它转到 state X。
**证据**：concept 的 state_machine.transitions 表 + B.guard 要求 current_state = X + A.action 产出 state = X。
**强度**：hard（状态机顺序不能违反）

---

## 建边规则

### 自动推导（从 read_set / write_set 机械推导）

```python
for A in all_tasks:
  for B in all_tasks:
    if A == B: continue
    
    # data_dependency
    if B.read_set & A.write_set:
      add_edge(A → B, type=data_dependency, evidence=B.read_set & A.write_set)
    
    # resource_conflict
    if A.write_set & B.write_set:
      mark_conflict_group(A, B, evidence=A.write_set & B.write_set)
```

### 需要 planner 判断的

- ordering：从 concept state_machine / workflow 推
- semantic_dependency：从 concept supersede 链推
- review_dependency：从 risk_surface 推（high-risk task → 有 review_prep）
- state_machine_transition_dependency：从 concept state_machine 推

### CLI 在 `dep-add` 兜哪些方向、哪些不兜（T-FIX-B6-03）

`uv run --directory harness python -m towow.cli.main plan dep-add` 建边时按 dep-type 做它**能确定性判定**的方向校验，其余靠 planner fork + plan-consistency-verify 的 Check 8 抽查 + 人判：

| dep-type | CLI 在 dep-add 的方向校验 | 为什么 |
|---|---|---|
| data_dependency | **硬拒反向**：forward overlap（B.read ∩ A.write）空且 reverse overlap（A.read ∩ B.write）非空 → reject | §1 的 read/write 判据是确定性的 |
| ordering | **自环硬拒** + 可疑反向 **warn**（不拒） | 自环（source==target）确定性错误；方向的权威来源是 ordering source，read/write 只是旁证 → warn |
| state_machine_transition_dependency | **自环硬拒** + 可疑反向 **warn**（不拒） | 自环确定性错误；方向权威来源是 concept transition chain，CLI 不持有 → 只能用 read/write 旁证 warn |
| resource_conflict | **CLI 不兜方向** | 本就是对称关系（A↔B 写冲突），不是有向边，应标 conflict_group |
| semantic_dependency | **CLI 不兜方向** | 方向在 concept supersede 链里，CLI 不持有 → 靠 fork + 人判 |
| review_dependency | **CLI 不兜方向** | 方向在 risk_surface / review_scope 里，CLI 不持有 → 靠 fork + 人判 |

> 「CLI 不兜」不等于无人兜：这 3 类（+ ordering/state_machine 那条只 warn 没拒的可疑反向）由
> plan-consistency-verify 的 Check 8 冻结前抽样复核 + planner fork 判断补上。

### 不该存在的依赖

| 假依赖 | 为什么不是真依赖 | 处理 |
|---|---|---|
| "B 看了 A 的代码风格" | 代码风格在 concept_graph 里（共享知识），不是 A 的产出 | 不建边 |
| "B 需要 A 完成的心理安全感" | 不是数据/语义/资源/状态机依赖 | 不建边 |
| "B 和 A 是同一个人做的" | v3 不假设"人"——是 fork | 不建边 |

---

## 循环依赖处理

如果 A → B → A 形成环路：
- 检查是否**假依赖**（去掉一条边）
- 如果真循环——合并 A+B 为一个 task（它们不能独立做）
- 合并后重新检查粒度（合并后是否太大）
