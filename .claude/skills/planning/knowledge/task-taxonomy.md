# Task Taxonomy — 任务包的类型与粒度判别

> 用途：planner fork 工具判断"这个交付物拆成什么类型的 task"的镜片。
> 归属：M-1.3 planner skill 知识库

---

## 什么是 task（任务包）

task 不是"做一件事"——task 是**一个零上下文 AI 能独立执行、独立 commit、独立 review 的价值交付单元**。

判别尺（F-06a）：一个没有上下文的 AI fork 拿到这个 task package，**不回头问任何人**，能直接产出可 commit 的 deliverable 吗？能 → 拆到位了。不能 → 要么 task 描述不够自包含，要么应该继续拆。

## 7 种 task 类型

### 1. implementation（实现类）

**什么时候产**：需要写/改代码、配置、schema。
**自包含要求**：task package 含 file_refs + concept_refs(@引用锁定) + 明确 write_set + done_criteria（可机械验证）。
**典型粒度**：一个功能模块 / 一组强耦合函数 / 一个完整 API endpoint（垂直切片，不是水平层）。
**model_tier 默认**：sonnet（实现逻辑直接）；涉及架构变更或高 fan-out 概念 → opus。

### 2. test（测试类）

**什么时候产**：需要写测试代码、验证覆盖率、回归测试。
**自包含要求**：task package 含被测对象 file_refs + expected_behavior + concept_refs(@ 引用被测概念) + done_criteria（测试通过 + 覆盖率达标）。
**与 implementation 的关系**：可以跟 implementation 同 task（垂直切片）或独立 task（如专门的 integration test）。**倾向同 task**——垂直切片更自包含。独立 test task 仅当 test scope 跨多个 implementation task 时。

### 3. documentation（文档类）

**什么时候产**：需要写/更新 ADR、API 文档、用户文档、设计日志。
**自包含要求**：task package 含 source_concept_refs + 文档目标 + done_criteria。
**model_tier 默认**：sonnet。

### 4. config_migration（配置/迁移类）

**什么时候产**：需要做数据迁移、配置变更、环境设置。
**自包含要求**：task package 含 before_state + after_state + rollback_plan + done_criteria。
**model_tier 默认**：opus（不可逆风险高）。
**特殊**：必须有 review plan（V-03 约束——高风险/不可逆动作）。

### 5. concept_update（概念更新类）

**什么时候产**：需要 supersede 概念、更新 @ 引用、更新消费方清单。
**自包含要求**：task package 含 old_concept_ref + new_definition + novelty_evidence + affected_consumers。
**与 M-1.2 的关系**：不是重做工程共识——是在执行过程中发现共识需要微调时产。
**model_tier 默认**：opus（语义决策）。

### 6. review_prep（review 准备类）

**什么时候产**：需要为 high-risk task 准备 review plan / review 上下文。
**与 M-1.5 的关系**：planner 产 review_prep task，执行后 M-1.5 review skill 消费。
**自包含要求**：task package 含 review_scope + risk_surface + invariants_to_check + concept_neighborhood。

### 7. investigation（调查类）

**什么时候产**：信息不足以直接实现——需要先调研、原型验证、可行性分析。
**自包含要求**：task package 含 question_to_answer + scope_constraint + done_criteria（产出是信息不是代码）。
**特殊**：investigation task 的产出可能触发 re-plan（F-14 动态重拆）。
**model_tier 默认**：opus（判断类）。

---

## 粒度判别

### 太大的信号

- fork 需要读 >5 个不同领域的 concept 才能开始
- write_set 涉及 >3 个独立模块
- 预估 token 消耗 >50K（fork 一次做不完）
- 需要中途问人（违反零上下文自包含）
- 单个 task 含多个独立 deliverable（违反"价值单元"）

### 太小的信号

- task 只改一行配置且不影响任何消费方
- task 的 output 不被任何下游 task 消费（那它可能不该独立存在）
- orchestration overhead > 实际工作量
- 两个 task 的 read_set/write_set 完全重叠（应该合并）

### 恰好的判别

- 一个 fork 一次性能做完
- 有明确 input（read_set）和 output（write_set + deliverable）
- 能独立 commit（不依赖同时 commit 的其他 task）
- 能独立 review
- done_criteria 可机械验证或有明确 observable

---

## 垂直切片 vs 水平切片

**水平切片**（按架构层拆）：一个 task 做 schema，一个做 API，一个做测试。

**问题**：
- 违反自包含（schema task 没法独立验证——要看 API 用它）
- 创造强制顺序依赖（schema → API → test 串行）
- 抵消并行价值

**垂直切片**（按功能/价值拆）：一个 task 做"用户能完成 X 操作"——含 schema + API + test。

**好处**：
- 自包含（完整可验证的价值单元）
- 独立 commit + review
- 依赖少（跟其他垂直切片只在共享 concept 上有依赖）

**v3 强烈倾向垂直切片。** 水平切片只在极少数情况合理（如全局 schema migration 必须一次性完成）。
