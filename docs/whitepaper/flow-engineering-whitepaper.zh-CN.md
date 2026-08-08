# Flow 工程
## 面向 Agent 软件工程的 Flow 中心型基础设施

**Flowness 白皮书 v1.1 — 对外立场文件**  
**作者：** Nature / Towow Research  
**日期：** 2026-08-05  
**许可证：** 本文档 CC BY 4.0

> **工作持续存在，Agent 围绕它形成。**
>
> **水无常形，事有其脉；会话会散，工作继续。**

---

## 摘要

Agent 软件工程正在快速经历一连串抽象迁移。Prompt Engineering 改进一次指令；Context Engineering 改进模型此刻看到的局部世界；Harness Engineering 用工具、规则、仓库结构和反馈，把模型包进可行动的环境；Loop Engineering 让一次行动变成可反馈、可修正的时间过程；Graph Engineering 把多个执行单元的关系、分叉、合流和循环显式化。

本文提出下一种工程对象：**运动中的工作本身，即 Flow**。

一项严肃工程工作并不等于一个 Prompt、一个 Session、一个 Agent、一张 Graph、一个 Pull Request。它可能比这些临时载体都活得更久。它会等待、获得新事实、推翻第一版计划、换一个模型、分叉成多个任务、回到设计层、要求人的判断，最终才以现实中被接受的变化闭合。如果系统把某个临时执行容器当成工作本身，工作就容易随着会话消失、静默停流、在绿色表象下发生语义漂移，或在尚未真正进入现实之前被宣布完成。

我们把工程化解决这类问题的方向称为 **Flow Engineering / Flow 工程**：Flow 是一等运行对象，Work 是它的可寻址投影；这门工程研究持续存在的 Flow 如何穿过不断变化的执行结构。一个 Flow-centered 系统在 Session 之外维护 Work 的身份与状态；从权威事实投影当前世界；编译下一次行动需要的 Context、Capability、Graph、Policy 与 Validator；记录 Effect 与 Finding；在假设失效时，对受影响范围进行重流。

Flowness 是一套有明确立场的、以工作为中心的 Agent 软件工程参考运行时。它当前公开或 dogfood 的机制包括 append-only Event、Projection、受边界约束的 Context Capsule、Concept/Version 历史、Obligation、Envelope 与 Commit Gate、Dispatch 与 Reconcile、Liveness 与 Dead Letter、Finding 与 Closure、Activation Evidence、Owner Decision View 和分层 Reflow。当前公开 Open Alpha 只证明其中较窄的 Assurance Kernel；完整有机的公开端到端 Flow 仍是明确的证明边界。

本文的主要贡献是：

1. 给出 Harness、Loop、Graph 与 Flow 的直观关系；
2. 定义 Work、Flow、Execution、Graph Snapshot、World State、Reflow 与 Human Constitution；
3. 提出一套五层工作中心型运行时架构；
4. 用 Formation、Continuity、Integrity、Adaptation、Commitment、Closure、Learning 组织 Flow 的健康与失败；
5. 提出可证伪的 FlowBench 评估议程，使比较不再只看最终代码是否通过。

本文不主张 Flowness 首创“Flow Engineering”一词，也不主张当前实现已经成为通用框架。AlphaCodium 曾用这个词描述多阶段、测试驱动的代码生成 Flow，Graph 型 Agent 系统也有相近使用。Flowness 更窄、更具体的立场是：

> **Flow 是一等运行对象，Work 是它的可寻址投影。**

---

# 1. 抽象正在移动

AI 工程的发展，可以被理解为不断把可靠性从模型不可见的内部推理中拿出来，变成可观察、可实现、可批判的外部结构。

Prompt 给模型一句指令；Context 给它一个局部世界；Harness 给它工具、权限、仓库规则、测试、反馈与行动空间；Loop 让它观察结果并再次行动；Graph 让多个执行单元之间的组织关系显式化。

每一次迁移都源于前一个抽象没有覆盖的新失败：

- 更好的 Prompt 无法提供缺失的仓库状态；
- 更大的 Context 无法保证工具使用、权限和恢复正确；
- 更强的单 Agent Harness 无法说明多个 Agent 怎样组织；
- 更耐久的 Graph 仍可能在会话消失、世界变化和假完成时失去工作本身。

行业已经出现相同压力。OpenAI 的 Harness Engineering 文章把工程师角色描述为设计环境、表达意图和建立反馈循环，并提出 “Humans steer. Agents execute.” [1] 随后的 Symphony 进一步把 Work 从 Session 与 PR 中解耦，用 Issue Tracker 作为控制面，在 Agent 崩溃或停滞时围绕仍然开放的任务重新启动执行。[2] Anthropic 的 long-running harness 研究也把跨 Session 延续进度视为外部状态问题，而不是单纯的 Context Window 技巧。[3] LangGraph 则把循环、动态转移、Checkpoint 和 Human pause/resume 纳入 Graph Runtime 的核心。[4][5]

这些系统并不相同，也不能直接证明 Flowness 的主张。但它们共同说明了一件事：

> **耐久对象不能继续只是聊天会话。**

Flow 工程再向前问一步：

> 这项工作现在是什么？哪些事实与义务约束它？什么已经 Ready，什么正在 Blocked，什么已经 Stale？下一次应该形成什么执行结构？什么证据才允许它进入下一个现实状态？

工程中心由执行者转向工作。

---

# 2. Harness、Loop、Graph 与 Flow

四个概念可以通过“结构 / 时间”和“单执行单元 / 整体系统”理解：

| | 单个执行单元 | 整个执行系统 |
|---|---:|---:|
| **结构与运行条件** | Harness | Graph |
| **跨时间的行为** | Loop | Flow |

这不是形式化的严格分区，而是一张帮助陌生人迅速建立直觉的地图。

## 2.1 Harness：一个行动者怎样获得工作条件

Harness 围绕模型提供它无法可靠自给的条件：

- 有边界的 Context；
- 工具与接口；
- 权限与 Sandbox；
- 对 Agent 可读的仓库结构；
- 状态与 Checkpoint；
- Skill、Policy 与约束；
- Validator 与反馈；
- Recovery 与 Observability。

Harness Engineering 问：**怎样的环境能让这个 Agent 做出有用、受约束的行动？**

## 2.2 Loop：一个行动者在时间中的展开

Loop 增加时间结构：

```text
行动 → 观察 → 判断 → 修正 → 再行动
```

它可以是 Tool Calling Loop、Verification Loop、事件驱动的 Daemon Loop，也可以是把 Trace 转成 Eval 与 Policy 的系统改进 Loop。共同直觉是“返回”和“反馈”。

Loop Engineering 问：**行动如何持续、纠错，并在时间中改进？**

## 2.3 Graph：多个执行单元在空间中的组织

Graph 显式描述 Node、Edge、State、Branch、Fan-out、Fan-in、Cycle 与 Human Gate。一个 Node 内部可以包含完整 Harness 和 Loop。现代 Graph Runtime 还允许在运行时动态产生工作，而不是提前写死所有 Edge。[4]

Graph Engineering 问：**多个执行单元怎样连接和协调？**

## 2.4 Flow：工作穿过不断变化的结构

Flow 更换了主语。

它不从一个预设 Agent 或预设 Graph 开始，而从一项具有身份、历史、状态、义务、证据和未完成未来的 Work 开始。系统读取这个状态，再围绕它现在的需要组织执行结构。

最容易传播的关系是：

> **Loop 是一个执行单元在时间中的展开。**  
> **Graph 是多个执行单元在空间中的组织。**  
> **Flow 是工作跨越一系列 Graph 的持续运动——并且有时会重新生成 Graph。**

某张 Graph Snapshot 在 seq=1000 时可以完全正确，在 seq=1040 时已经过时。新的 Finding 可能证明一个 Safety Path 没有真实 Consumer；Concept Supersede 可能让旧解释派生出的任务失效；Owner Decision 可能解锁一条原本不存在的路线。Flow 不是“再跑一次原 Graph”，而是让工作的当前状态决定现在应该存在什么 Graph、Context、Executor 和 Gate。

Flow Engineering 问：**当执行结构不断变化时，工作怎样保持活着、保持一致、适应现实并可信闭合？**

---

# 3. 工作中心型反转

大多数 Agent 系统都可以被描述为 execution-first：

```text
选一个 Agent 或 Workflow
→ 注入任务
→ 运行到成功、失败或超时
```

Flow-centered Runtime 反转依赖：

```text
持久化 Work
→ 读取当前世界
→ 编译 Work 现在需要什么
→ 组织执行
→ 观察结果
→ 更新世界与 Work
→ 继续、等待、重流或闭合
```

最短的公共表达是：

> **不是 Agent 拥有 Flow，而是 Flow 临时召集 Agent；Work 是你寻址它的地方。**

这不是一句哲学口号，而会改变系统设计。

## 3.1 没有 Agent 运行时，Work 仍然可以存在

Work 可能正在等待：

- 外部依赖；
- 时间窗口；
- 真实 Consumer 信号；
- 权限；
- Owner Decision；
- 尚未到达的 Evidence；
- 资源预算；
- Projection 追上事实源；
- Invalidation 后的 Reflow 决定。

此时可以真实出现：

```text
agents: none
flow: alive
```

Session-centered 系统容易把它理解为“系统没有在运行”。Flow-centered Runtime 把 Wait 视为明确状态，包含 Expectation、Timeout、Wake Condition 与 Escalation Route。

## 3.2 Execution 是一次尝试，不是 Work 身份

模型调用、Agent Session、Worktree、Run、Pull Request 都属于 **Execution**：在特定 World Cutoff 与 Context 下推进 Work 的一次尝试。

Execution 可以替换，Work Identity 不可以。

这使以下场景成为自然能力：

- Agent failover；
- 模型替换；
- Session restart；
- 跨 Repository 工作；
- 不产生代码的 Investigation；
- 一个 Work 产生多个 PR；
- Work 从 Analysis 转成 Implementation 再转成 Operation。

## 3.3 Graph 成为派生执行结构

当 Flow 是第一性的、Work 只是它可寻址的稳定投影时，Graph 就是当前 World State 上的 Projection 或 Compiled Plan。它应该带有 `as_of`、Version 与 Derivation Evidence。

Graph Snapshot 应能回答：

- 当时相信存在哪些 Node 与依赖；
- 使用了哪些 Concept / Requirement Version；
- 哪些 Obligation 生效；
- 哪些 Capability 和资源可用；
- 什么条件让 Node Ready 或 Blocked。

新的 World State 可以 Supersede Graph，而不否定它在历史 Cutoff 上曾经正确。

## 3.4 Context 成为 Compiler 输出

Handoff Summary 是文本产物；Runtime Context Capsule 是从以下内容编译出的执行合同：

```text
当前 Work State
+ Exact Object / Version
+ 权威事实
+ Active Obligation
+ 相关 Concept Neighborhood
+ 当前 Action 与 Scope
+ 可用 Capability
+ Evidence 与未解决 Finding
+ Reflow Condition
```

同一 Work 在 Investigation、Design、Implementation、Review、Deploy、Operation 时应编译出不同 Context。Context 需要绑定事实 Cutoff，避免来自不同时间点的材料被悄悄混合。

## 3.5 人类注意力从 Transcript 移向 Constitution

当人监督 Session 时，Autonomy 与注意力近似线性绑定。Work-centered 系统则让人定义自主执行合法成立的底层场地。

这就是 Flowness 的一句核心表达：

> **人可以退出会话，但不能退出系统的宪法。**

---

# 4. 核心对象

一门工程只有在对象足够明确、能够实现、比较和证伪时才有意义。

## 4.1 Work

Flow 是那个持续存在的因果过程；**Work** 是它稳定、可寻址的投影，表示对某个 Goal 或 Target 尚未完成的责任。

Work 不持有这些字段——它们是从底层事实投影出的最小视图：

```text
work_id
身份与 target reference
当前状态与状态原因
goal / anti-goal reference
exact object/version scope
active obligation 与 authority
finding 与 unresolved question
dependency 与 ready condition
execution history 与 active attempt
effect / activation / acceptance state
next-action candidate
```

Work 不必等于 Ticket。Ticket 可以是 Work 的外部 UI 或 Projection；Work 也可以来自 Finding、Event、Owner Decision、世界变化或周期性维护义务。

Work 可以 split、merge 或被 supersede；深层事实始终分布在 Object、Event、Obligation、Judgment、Evidence 与 History 之中；Work 是查询面，不是上帝对象（God Object）。

## 4.2 World State

**World State** 是与工作相关的权威、版本化状态。它不仅包括文件内容或数据库值，也包括活跃解释、义务、权限、依赖、Finding、Activation Evidence 与人的决策。

用命/运的语言说，World State 就是此刻之**运（yùn）**——此刻的事件、资源、可用能力、权限与暴露的风险。

Flowness 以 append-only Event 和确定性 Projection 为重要底座。此前形式化工作把 Interpretation History 提升为一等对象，因为仅观察当前 Data State 无法判断多个 Writer 在何时、为何发生意义漂移。[6]

## 4.3 Flow

**Flow** 是一个目标在事件驱动下，依当前世界状态不断被编译为可执行结构、执行、验证、提交的持续因果过程。Work 是 Flow 的稳定可寻址投影——人和程序定位它的界面。

Flow 不等于一次 Run。一个 Flow 可以包含许多 Run、Agent、Context、Plan、Graph Snapshot、Finding、Owner Decision、Effect 与 Reopen。

## 4.4 Execution Assembly

**Execution Assembly** 是某次尝试的临时运行结构：

```text
Executor
+ Context Capsule
+ Tool / Capability
+ Graph / Ready-set
+ Policy / Obligation
+ Validator / Gate
+ Resource / Authority Boundary
+ Evidence Contract
```

Execution Assembly 是临时的。Flow 持续存在，Work 提供可寻址的连续性。用命/运的语言说：命与运共同编译出 Execution Assembly；当这套有边界的 Assembly 实际行动时，一次临时 Agent 实例才随之成形。Agent 是这套结构所表达的一次 Agency，而不是其中任何一个 Executor。

## 4.5 Graph Snapshot

**Graph Snapshot** 是指定 Evidence/Watermark Cutoff 上，Work 的版本化结构投影。它可以包含 Task、Dependency、Concept、Impact、Consumer 或 Review Relation。

它不是唯一事实源，而是可以从事实源重建的结构。

## 4.6 Finding

**Finding** 是一个持久、可寻址的主张：某个 State、Output、Assumption、Route 或 Evidence Set 不充分、失效、有风险或仍 Unknown。

Finding 有稳定身份和 Lineage。新 Candidate 出现并不会让它自动消失；只有真正解决底层主张的 Evidence 才能 Discharge 它。

## 4.7 Reflow

**Reflow** 是定位失效层或失效假设、传播影响并重新编译受影响未来的过程。

```text
re_execute  — 暂态执行失败
repair      — 局部实现偏差
replan      — Task / Dependency 分解错误
re_engineer — 技术机制或工程假设错误
redesign    — 系统行为或结构设计错误
re_interview— Goal、Value、Scope 或 Owner 理解错误
```

Reflow 不一定沿 Pipeline 向后。它可以形成 Investigation 支线、等待外部事件、替换 Capability、生成新 Work、退役旧 Route，或只重开局部 Slice。

## 4.8 Human Constitution

**Human Constitution** 是由人拥有、具有约束力的合法运行场地：

- Goal 与 Anti-goal；
- Ontology 与 Object Identity；
- Obligation 与 Red Line；
- Authority、Approval 与不可逆边界；
- JudgmentCase、Exception 与 Counterexample；
- Acceptance 与 Promotion Rule；
- Budget 与 Risk Posture。

用命/运的语言说，Human Constitution 是**命（mìng）的规范性核心**——语法中有作者的那部分。命整体还继承着这项工作不可逆的结构：已发生的 Effect、确切版本与关系、已接受的承诺、历史形成的义务。它不是剧本，而是生成合法路径的语法。

Constitution 可以演化，但它的演化也必须受治理。系统可以从 Trace 提出新规则，不应悄悄把规则提升为强制基础设施。

## 4.9 Cognitive Exoskeleton

**认知外骨骼**是一个人或组织的 Goal、Concept、Case、Obligation、Skill、Validator 和 Failure History 跨模型、跨项目持续存在后形成的、可以执行的判断系统。

它不同于普通 Memory，因为它记录 Applicability、Counterexample、Supersede、Authority 与 Validation，而不仅是文本相似性。

---

# 5. 五层架构

## 5.1 Human Constitution

```text
Goal · Ontology · Judgment · Obligation · Authority · Red Line
```

它回答：

- 我们究竟想让什么成为现实？
- 什么不允许被为了容易完成而牺牲？
- 谁能决定、行动、接受或豁免？
- 什么 Evidence 才足够？
- 哪些动作可逆，哪些不可逆？

人的控制在这里是基础设施级的。它影响所有下游 Context 与 Gate，却不要求人持续盯 Session。

## 5.2 Persistent Work State & Truth

```text
Event · Identity · Version · Projection · Causal History
```

这一层让 Work 独立于临时 Executor。理想属性包括：

- append-only 或可重建 Truth；
- 稳定 Identity 与 exact-version reference；
- committed-visible semantics；
- 可 replay 的 Projection；
- 明确 Freshness / Watermark Contract；
- 进程失败后的 Recoverability；
- Provenance 与 Producer Boundary。

现有 Flowness EventLog、Projection、Concept Supersede、Judgment、Finding 与 Obligation 机制主要位于这里。

## 5.3 Flow Compiler

```text
Current-world Projection · Context Capsule · Capability Selection
Graph Snapshot · Policy · Validator · Evidence Contract
```

Compiler 把持久 Work 与当前 World State 变成可执行的局部世界。

它最重要的职责是**行为相关性**：只有能够改变 Action、Constraint、Route 或 Validation 的对象，才值得进入 Context。更多 Context 不自动等于更好 Context。

Compiler 还必须显式保留 Unknown。未知事实应成为 Open Question、Blocked Condition 或 Evidence Requirement，而不是被模型悄悄补成确定答案。

## 5.4 Flow Runtime

```text
Ready-set · Dispatch · Reconcile · Liveness · Recovery · Reflow
```

Runtime 负责维持运动。

它必须区分：

- 当前没有 Ready Work；
- Work 正在合法等待；
- Work 因缺少 Producer 而停滞；
- Executor 已死亡，应当替换；
- Route 已失效，应当 Reflow；
- Task 重复，应当 Deduplicate；
- Escalation 没有 Consumer；
- 系统已经到达受界 Stop Condition。

Reconcile Loop 对比 Desired State 与 Observed State，并复用幂等执行路径弥合差距。Liveness 需要 Expectation、Timeout、Dead Letter、Wake Condition 与 Recovery，而不是只收集 Heartbeat。

## 5.5 Reality & Assurance

```text
Artifact · Consumer · Integration · Activation · Effect
Finding · Independent Review · Acceptance
```

这一层问：Work 是否到达了它试图改变的现实？

```text
Built      — 产物存在
Integrated — 目标系统会消费它
Activated  — 真实世界已经自然触发它
Accepted   — 负责主体接受结果
```

四个状态需要不同证据。Test 与 Demo 可以证明 Built 或部分 Integration，不能被偷换成 Organic Activation。Acceptance 需要绑定 Exact Artifact、Version、Finding 与 Readback。

Independent Review 是这一层的一个机制，不是 Flowness 的全部定义。当前公开 Open Alpha 聚焦这个 Assurance Kernel，并明确不声称完整架构已经公开闭合。

---

# 6. Flow 的健康与失败

## 6.1 Formation

Event、Goal、Finding 或 Obligation 已经存在，但没有形成可执行 Work。

典型问题：

- Incident 被记录，却没有形成有边界的 Investigation；
- Finding 没有 Owner 或 Completion Contract；
- Requirement 变化没有生成下游 Invalidation；
- Task 被创建，但缺少可以执行它的 Context。

## 6.2 Continuity

Work 存在，却沉默停止。

典型问题：

- Heartbeat 进入 EventLog，但没有 Consumer；
- Escalation 没有 Expectation、Timeout 或 Dead-letter；
- Gate 等待一种系统中没有任何组件能产生的 Evidence；
- Executor 死亡，但 Reconcile 没有发现；
- 所有 Task 都被阻塞，因为前置条件的 Producer 从未实例化。

Continuity 是 Liveness 问题。更多日志不是答案，除非系统能把“预期进展未发生”转成 Finding 或 Action。

## 6.3 Integrity

Flow 仍在运动，但 Identity、Version、Evidence、Interpretation、Authority 或信息边界发生漂移。

典型问题：

- Certificate 对旧 Draft 签发；
- Stale Concept Version 进入 Implementation；
- 私有坐标泄漏到公开材料；
- Producer 与所谓独立 Reviewer 实际上是同一个主体；
- 通过改写 Finding 文案而不是解决根因来“关闭”问题。

## 6.4 Adaptation

世界已经变化，Flow 仍在旧假设上继续。

典型问题：

- Design 变化没有让 Engineering Plan 失效；
- Dependency 退役后，下游 Task 仍然 Ready；
- Owner Decision 已记录，但没有改变任何 Capsule；
- Benchmark 失败本应触发 Re-engineer，系统却只 Retry Execution。

## 6.5 Commitment

Output 已经生成，但没有进入真实 Consumer Path。

典型问题：

- 五份有效产物没有进入 Release Manifest；
- Safety Function 存在，但没有生产路径调用；
- Runbook 写好了，却没有进入 Operation Control Surface；
- Patch 通过 Test，却没有进入 Target Branch 或 Service。

## 6.6 Closure

系统宣布完成，却没有证明目标现实。

典型问题：

- “Tests passed” 替代“Feature is used”；
- Producer Final Answer 替代独立 Acceptance；
- Aggregate Green 隐藏 Mandatory Failure；
- Synthetic Drill 被计为 Organic Activation；
- UNKNOWN 被平均进 PASS。

## 6.7 Learning

同一种结构性失败反复出现，未来行为没有变化。

这才是认知外骨骼真正累积的地方。治理式改进路径应是：

```text
Trace / Owner Correction
→ Reviewed Finding
→ Bounded Failure Fixture / Eval
→ Candidate Mechanism / Skill / Policy
→ Regression + Counterexample
→ Shadow / Warning
→ Human-approved Promotion
→ Rollback
```

---

# 7. 受治理的自组织

“自组织、自生长、自发展”很有吸引力，也很危险。没有边界时，它容易掩盖目标是谁设定的、Effect 谁拥有、系统改变自己时谁负责。

Flowness 使用更严格的表达：**Governed Self-organization / 受治理的自组织。**

## 7.1 自组织

当前 Work State 决定此刻需要什么 Capability、Agent、Context 与 Graph Node。

在以下条件下可以自主进行：

- Work Identity 与 Target 稳定；
- Authority 可用；
- Action 在 Policy 内；
- Evidence 与 Rollback Requirement 清楚；
- Unknown 可以被安全表达。

## 7.2 自生长

新的 Finding、Dependency 与 Goal 可以生成新的 Work 与 Relation。Graph 可以因为现实暴露了缺失 Consumer 或未建模 Operation Path 而增长。

增长必须能追溯到使它合理的 Event 或 Judgment。

## 7.3 自发展

重复经验可以提出对 Harness 自身的改变：新 Skill、Validator、Obligation、Default Route、Detection Rule 或 Context Policy。

局部成功可能造成全局回归，因此 Promotion 需要：

- 清楚的 Source Finding；
- 有边界的 Hypothesis；
- Direct Test 与 Counterexample；
- 和当前 Policy 的对照；
- Impact 与 Rollback；
- 对强制规则具有 Authority 的人批准。

理想分工是：

> **人定义场地，工作召集能力，Agent 临时形成并行动。**

---

# 8. 软件工程是第一套 Flow Profile

Flow Engineering 可以扩展到其他领域，但 Flowness 应先在状态、Artifact、Test、Version 与 Effect 都较容易检查的软件工程中证明。

高可信软件工程 Profile：

```text
goal
→ investigation / interview
→ Problem IR / Requirement IR
→ Design Alternative / Object / Decision
→ Engineering Component / Contract / Decision
→ stable engineering consensus
→ dependency-aware task graph
→ isolated execution / envelope
→ independent validation / finding
→ layer-aware reflow
→ integration / activation / acceptance
```

## 8.1 Design 与 Engineering 为什么仍然重要

Work-centered 立场不会取消新增的 Design 与 Engineering 层，而是重新解释它们：

- **Design：** 系统应当具有什么行为、对象、Authority、State 与 Mechanism；
- **Engineering：** 设计如何映射到 Component、Interface、Data、Concurrency、Failure Semantics、Operation、Migration 与 Test Architecture；
- **Consensus：** 哪些 Accepted Engineering Fact 应成为下游稳定、版本化的共同前提。

它们是软件工程领域 Profile，不是 Flow 的普遍本体。

## 8.2 Cognitive Compilation 有两层含义

1. **Artifact Compilation：** 模糊 Intent 成为 Problem、Design、Engineering、Plan 与 Evidence-bearing Execution Artifact；
2. **Runtime Compilation：** 当前 Work 加当前 World，成为下一次 Action 的 Local Context 与 Execution Assembly。

第二层是更一般的 Flow Primitive；第一层是构建在其上的软件工程 Profile。

## 8.3 不是每个 Task 都支付全部流程成本

Flow Profile 应对 Risk 与 Reversibility 敏感。

小型文档修改可以直接进入 Execution 与 Review；安全敏感的 State Machine 改动则可能需要 Investigation、多个 Design Alternative、工程证据、Authority、Staged Activation 与 Fresh Acceptance。

正确原则不是最大流程，而是：

> **使用能够保留真实风险、意义和完成合同的最浅路径。**

---

# 9. 当前实现映射与诚实边界

## 9.1 `[RUNNABLE]`

当前 Open Alpha 提供确定性的 Execution → Review → Targeted Rework → Fresh Acceptance 证明和独立 Inspector。它展示 Candidate Binding、Judge Separation、Finding Persistence、Mandatory Failure、Targeted Repair 与 Fresh Evaluation。

## 9.2 `[INSPECTABLE]`

公开仓库包含：

- append-only EventLog、Transaction、Replay、Committed Visibility 与 Producer Boundary；
- 确定性 Projection 与 Freshness/Watermark；
- Context Capsule Compilation 与 Obligation Activation；
- Envelope 与 Commit Gate；
- Goal、Consensus、Finding、Judgment、Closure、Activation、Consumer Mechanism；
- Ready-set Dispatch、Reconcile、Liveness、Dead Letter、Reflow、Invalidation；
- Owner Inbox 与 View；
- Conformance 与 Public Core。

可检查不等于已形成完整公开集成路径。

## 9.3 `[DOGFOOD]`

项目报告了数月持续内部运行、较大事件语料与数十条结构性审计 Finding，也报告了 Problem / Design / Engineering 对象和部分 Route 的私有实现。

这些数字在承担正式实证主张前，需要公开：

- Event 与 Finding 计数口径；
- Dedup 与 Sampling；
- Privacy / Sanitization；
- 时间与仓库边界；
- Immutable Evidence Manifest；
- Limitations 与 Missing Data。

## 9.4 `[DESIGNED]`

WorkView、Work Outlives Agents Hero Demo、部分 Invalidation/Reflow Daemon Path 和部分 Design/Engineering Publishing Chain 已有规格或纯逻辑，但尚未成为公开端到端能力。

## 9.5 `[OPEN QUESTION]`

- 通用跨领域 Flow Runtime；
- 完整公开 Organic Goal → Accepted Outcome；
- 在固定 Budget 下减少 Human Attention 或提高质量的比较证据；
- Model-tier Semantic Check 的误差边界；
- Event/Graph 机制相对简单文档与 Workflow 的工程经济阈值。

这不是叙事弱点，而是研究计划与不可证伪营销之间的区别。

---

# 10. FlowBench：让类别可证伪

FlowBench 不应成为另一个只比较最终代码通过率的榜单。它要测量推动 Flow 概念出现的系统属性。

## 10.1 实验单元

一个 Case 应包含：

- 持久 Work Identity；
- 一个或多个 Execution Attempt；
- 会改变的 World Event；
- Authority 与 Irreversible Action Constraint；
- Reality Readback；
- 不提供 Expected Internal Graph 或唯一解决路径。

## 10.2 最小失败通道

1. Executor 在部分进展后死亡；
2. Ready-set 因缺失 Producer 被阻塞；
3. Escalation 没有 Consumer 或 Timeout；
4. 上游 Concept 变化使下游 Context 失效；
5. Stale Execution 尝试 Commit；
6. Artifact Built 但未 Integrated；
7. Synthetic Activation 不能计为 Organic Use；
8. Value / Scope Conflict 必须由 Owner Decision；
9. 重复 Failure 应产生 Candidate Regression Fixture。

## 10.3 Baseline

- 强单 Agent + 成熟 Harness；
- Issue Tracker Orchestrator；
- 具有 Persistence 与 Human Pause/Resume 的 Graph Runtime；
- Durable Workflow Engine + Agent Activity；
- Flowness Ablation：无 Reconcile、无 Version Binding、无 Activation Distinction、无 Reflow Classification、无 Human Constitution。

Fairness 不得消除 Baseline 的原生形态，也不能赋予它本来没有的 Authority 与信息。

## 10.4 指标

- Executor Failure 后的 Work Survival Rate；
- Correct Resumption Time；
- Silent-stall Rate 与 Detection Latency；
- Stale-context / Stale-write Acceptance；
- Reflow Localization Accuracy；
- Orphan Output Rate；
- False Activation / False Closure；
- 正确 Unknown / Defer / Reject；
- Human Attention Minutes 与 Decision Count；
- Token、Wall-clock 与 Infrastructure Cost；
- Final Task Success 与 Regression；
- Harness 自身维护复杂度。

## 10.5 证据纪律

FlowBench 必须发布负结果。通过 Qualification 只允许一个有边界的比较，不证明普遍优越；失败应指出精确 Disqualifying Channel，而不是被一个 Aggregate Score 淹没。

---

# 11. 开源路径：Learn、Borrow、Build

Flowness 很可能不是最适合作为一套所有团队原样安装的万能产品。Harness 越能编码当地领域、判断、风险与运行习惯，它往往越强，也越难完全通用。

因此开源应支持三条路径。

## 11.1 Learn

使用 Failure Atlas、Concept Kit、Design Question 与 Mechanism Contract 审计自己的 Agent 系统。

即使不安装 Flowness，也能获得价值。

## 11.2 Borrow

单独借用一个机制：

- Event Truth / Projection；
- Bounded Context Compilation；
- Obligation Lifecycle；
- Commit Gate；
- Finding Lineage；
- Reconcile / Dead Letter；
- Activation Evidence；
- Owner Decision Inbox；
- JudgmentCase / Regression。

把机制接入 Temporal、LangGraph、Symphony-like Orchestrator、OpenHands 或本地 Coding Agent，可能比强迫采用完整 Runtime 更有生态价值。

## 11.3 Build

把 Flowness 当成 Reference Runtime，用来搭建个人或组织自己的 Harness。

最终沉淀的是认知外骨骼：即使模型、Agent 与框架变化，判断系统仍然存在。

> **模型可以替换，你的判断应当累积。**

---

# 12. 既有使用与定位边界

“Flow Engineering”已有使用历史。

AlphaCodium 2024 年论文用它描述测试驱动、多阶段、迭代的 Code Generation Flow。[7] AgentKit 使用 “Flow Engineering with Graphs” 描述 Graph 结构化 Agent 构建。[8] 传统软件与组织实践也使用 Flow 来描述 Value Stream 与 Delivery System。

因此，Flowness 不声称首创或独占这个词。

本文的具体立场是：

1. Flow 是第一性的，Work 是它的可寻址投影；
2. Agent、Context 与 Graph 是围绕当前 Work State 形成的 Runtime Assembly；
3. Flow 包含 Wait、Invalidation、Reflow、Reality Activation 与 Human Constitution；
4. Graph 是 Flow State 的版本化 Projection，不一定是永久 Truth；
5. Judgment 应跨模型持续积累。

这是一个待实证的类别边界。判断它是否成立，应看它能否带来更清晰的系统设计、更有解释力的 Failure Taxonomy 和可证伪的工程结果。

---

# 13. 限制与开放问题

## 13.1 命名本身不会创造新学科

只有当 Flow 能比 Durable Workflow、Task Orchestration、Graph Runtime 与 Harness Design 更好地组织碎片化问题，并产生更有用的实现与评估，类别才算成立。

## 13.2 公开 Work 对象仍不够可见

当前公共代码把 Work Semantics 分布在 Goal、Task、Finding、Obligation、Session、Event、Projection、Closure 与 Activation 中。需要一个只读 WorkView，让对象可观察，同时避免过早重构内核。

## 13.3 丰富语义会产生维护成本

每新增一个 Object、Event、Relation、Gate 与 Projection，都会增加 Schema、Migration、Docs 与 Operation 负担。一些团队更应该使用简单 Issue Tracker + Durable Workflow。Flow 工程必须包含机制复杂度的停止规则。

## 13.4 Model-tier Check 仍然会错

Semantic Equivalence、Novelty、Applicability 与 Design Quality 无法完全决定。Flowness 区分 Physical、Model 与 Human/Environment Tier，但 Model-tier 的误差与 Escalation Load 需要实证。[6]

## 13.5 Human Constitution 可能变成官僚系统

把人的控制移动到基础设施，不会消除人的成本。糟糕的 Obligation 与 Gate 会造成 Hidden Queue、False Reject 与仪式化 Review。Human Attention 必须被测量。

## 13.6 Self-improvement 可能造成全局回归

局部修正可能过拟合、压制创造力或制造更严重的问题。Harness 自我修改需要 Counterexample、Regression、Staged Promotion 与 Rollback。

---

# 14. 路线图

1. **让 Work 可见：** WorkView Projection 与 `flowness work ...` CLI；
2. **让 Flow 可见：** 出现 `agents: none / flow: alive` 的确定性 Hero Demo；
3. **让一个 Organic Flow 公开：** 新目标从 Goal 到 Accepted Outcome；
4. **让 Failure 可移植：** Mechanism-off / Mechanism-on Failure Clinic；
5. **让 Claim 可证伪：** FlowBench Qualification 与 Ablation；
6. **让 Judgment 可累积：** JudgmentCase 从 Owner Correction 进入 Retrieval、Counterexample、Regression 与可回滚 Promotion。

---

# 15. 结论

Flow Engineering 的中心主张很简单：

> **一项严肃工作，比当前承载它的 Agent、Session、Context、Plan、Graph 与 Pull Request 更耐久。**

接受这一点以后，架构会发生变化。

Work 需要持久 Identity 与 State；世界需要权威 Fact 与 Version；Context 成为 Compiler Output；Graph 成为版本化 Execution Projection；Agent 成为可替换 Capability；Wait 成为明确状态；Reflow 成为 Invalidation + Local Recompilation；Completion 成为从 Artifact 到 Consumer、Organic Activation 与 Responsible Acceptance 的证据链；人的参与从盯 Transcript 转向定义 Constitution 与处理真实例外；经验不再只是 Prompt 堆积，而成为受治理的认知外骨骼。

> Agent 无名、无形、无姓。
> 它不是组织中等待被管理的一个人，而是行动能力在某一时刻的一次成形。
> 留下的不是人格，而是路径、证据、状态，以及能够被下一次行动继承的判断。

Flowness 是在软件工程中实现这套架构的一次尝试。它同时来自形式化的一致性工作与长期 dogfood 的失败分析。项目不应以机制数量最大来证明自己，而应以是否让工作更不容易丢失、更不容易静默变质、更能精准修复、更诚实闭合来接受检验。

> **智慧不属于某个节点。它在工作中流动，在制度中沉淀。**

---

# 参考资料

1. OpenAI. “Harness engineering: leveraging Codex in an agent-first world.” 2026. https://openai.com/index/harness-engineering/
2. OpenAI. “An open-source spec for Codex orchestration: Symphony.” 2026. https://openai.com/index/open-source-codex-orchestration-symphony/
3. Anthropic. “Effective harnesses for long-running agents.” 2025. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
4. LangChain. “3 Years of Graph Engineering with LangGraph.” 2026. https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph
5. LangChain. “The Art of Loop Engineering.” 2026. https://www.langchain.com/blog/the-art-of-loop-engineering
6. Nature / Towow Research. “Flowness: A Distributed-Systems Position on Multi-Agent AI Software Engineering” 与 “Flowness: Event-Sourced Interpretation Consistency for Multi-Agent Software Engineering.” 2026.
7. Ridnik, T., Kredo, D., and Friedman, I. “Code Generation with AlphaCodium: From Prompt Engineering to Flow Engineering.” arXiv:2401.08500, 2024. https://arxiv.org/abs/2401.08500
8. Wu, Y. et al. “AgentKit: Flow Engineering with Graphs, not Coding.” arXiv:2404.11483, 2024. https://arxiv.org/abs/2404.11483
9. Temporal Technologies. Durable Execution documentation and SDK. https://temporal.io/ and https://github.com/temporalio
10. Towow-ai. Flowness public repository. https://github.com/Towow-ai/Flowness

---

# 附录 A：公开 Claim 标签

| 标签 | 对外含义 |
|---|---|
| `[RUNNABLE]` | 读者可以从公开 Release 复现 |
| `[INSPECTABLE]` | 有公开代码、测试、合同或 Artifact，但没有完整公开路径 |
| `[DOGFOOD]` | 在持续私有运行中使用或观察；证据可能脱敏或不完整 |
| `[DESIGNED]` | 已有规格，或只有未接线/纯组件实现 |
| `[OPEN QUESTION]` | 研究目标，不是能力声明 |

# 附录 B：最小 Flow Contract 示例

```yaml
work:
  id: W-42
  target_ref: repo://service/safety-path@commit
  state: blocked
  as_of_seq: 18420

  goals:
    - safe_pause_is_organically_reachable
  anti_goals:
    - synthetic_drill_counted_as_activation

  active_obligations:
    - production_consumer_required
    - independent_activation_readback

  blocked_on:
    - owner_decision: OD-7

  graph_snapshot:
    version: 7
    supersedes: 6

  executions:
    active: []
    recoverable:
      - EX-103

  reality:
    built: true
    integrated: true
    activated: unknown
    accepted: false

  next_candidates:
    - compile_repair_capsule_after_owner_decision
```

该 Schema 只是解释性示例，不是冻结的 Public API。

下面这份 Execution Assembly 编译 provenance 记录与上例配对：命（ming，来自 Human Constitution 的引用）与运（yun，当前世界的截面）作为编译输入，一次临时 Agent 及其溯源作为编译输出。

```yaml
execution_assembly:
  id: EA-2201
  compiled_for: W-42
  as_of_seq: 18420

  ming:            # 命：来自 Human Constitution 的引用，不是这些值本身
    constitution_refs:
      goals: [safe_pause_is_organically_reachable]
      obligations: [production_consumer_required, independent_activation_readback]
      red_lines: [no_synthetic_activation_counted_as_organic]
      acceptance: [independent_activation_readback_required]

  yun:             # 运：此刻的世界截面
    world_cutoff_seq: 18420
    events: [OD-7_recorded, EV-90213]
    resources: [staging_capacity_slot_3]
    capabilities: [repair_capsule_compiler@v2]

  agent:           # 编译产物：这一次临时形成的 Agent 及其溯源
    executor: ephemeral-session-8831
    capsule_hash: sha256:9f2a1c...c71e
    graph_snapshot: { version: 7, supersedes: 6 }
    authority:
      scope: repo://service/safety-path
      cannot: [merge_to_main, force_push]
    evidence_contract:
      required: [independent_activation_readback]
      discharges_on: [OD-7, EV-90213]
```

该 Schema 同样只是解释性示例，不是冻结的 Public API。
