# Review Mental Model — Context-Driven Falsification Judgement

> 用途：装在 review skill 脑子里的根本心智模型。读完这个文档 skill 应该有"啊，review 是这样的"的感觉，然后遇到具体情况自然推导。
> 归属：M-1.5 review skill 知识库 ★ 核心文档
> 来源：v3.1 review 转变 + Nature 多轮 challenge + O-12 + F-08 + Anthropic Code Review 实证 + Popper 哲学

---

## 一、Review 是什么——根本逻辑

跟 Execution 对偶：
- **Execution** = 把"做什么"变成"做出来"
- **Review** = 给已经做出来的东西评估：**它真的满足了"做什么"吗？**

所以 review 是**事实对意图的对照**：
- 事实 = patches + envelope（M-1.4 产出）
- 意图 = brief.completion_condition + done_criteria + active_obligations + 隐含 context（demo vs prod / 短期 vs 长期）

到这里是平凡的。**关键是怎么对照——这才是 review 的灵魂。**

---

## 二、Review 的灵魂判据（三层套娃）

```
表层：VoI（如果 author 按这个 finding 修复，任务完成度会改善多少？）
       ↓ 由什么决定？
中层：context-aware reasoning（基于什么 context 算 VoI）
       ↓ 由什么决定？
深层：reviewer 注入的差异化 context（V-02 prompt + 历史 + 风险面 + 方法论 lens）
```

**完整表述**：**"在我（reviewer）被注入的这份特定 context 下，这个 finding 是否能让 author 更接近意图"**。

VoI 是表层语言，**context-injection 是底层机制**。

### Demo 例子（来自 Nature）

同一句话"没并发"——

| Finding 表述 | 上下文 | VoI |
|---|---|---|
| "没并发" | 仅 demo | 0（demo 不需要）|
| "没并发" | demo + 用户说长期发展 + 现在预留比后续重做便宜 | > 0（影响 author 决策）|

**同一个事实观察，VoI 由 context-aware reasoning 决定**。Reviewer 没有 context-aware reasoning → 制造假问题。

---

## 三、Reviewer 的价值来源 = 差异化 context

> Nature 关键洞察："reviewer 为什么会比执行者本身更好地做这个事情呢？是因为 reviewer 被注入了那些只有在 review 的时候需要的，但是执行阶段并不需要的上下文。"

**反例**：reviewer 跟 executor 用**同样**的 capsule、同样的 prompt、同样的 model——它发现什么 executor 没发现的？理论上**没有**。reviewer 没有存在意义。

**正例**：reviewer 比 executor 多出来的 context：
- **历史失败模式**（F-08e）—— "类似变更过去踩过这些坑"，executor 干活时不看，reviewer 必须看
- **风险面定义**（F-08b）—— executor 盯 done_criteria，reviewer 盯风险面图（不同 lens）
- **跨 task 影响范围 / consumer lists**（O-11）—— executor 关本 task，reviewer 关 ripple
- **完整 obligation 邻域**（不只本 task scope 的）—— executor 看激活给本 task 的，reviewer 看可能被 patches 隐式破坏的相邻 obligation
- **红队 prompt**（F-08c 维度 3）—— executor 视角是"如何做对"，reviewer 视角是"如何破坏"
- **执行路径模拟 prompt**（F-08c 维度 1）—— executor 写代码，reviewer **模拟跑代码**追踪变量真实值
- **跨方法论的一致性 lens**（F-08c 维度 2）—— executor 改了一处，reviewer 看其他重复描述是否同步

**这就解释了**：
- **为什么 V-02 reviewer 物理隔离**——不只是"不让 reviewer 改代码"，是**保证 reviewer 的 context 干净**。Reviewer 有 Edit/Write 工具就会忍不住"我改一下试试"——这就**污染了 lens**（从"评估者"变成"再次执行者"）
- **为什么 F-08c 不按角色（业务/安全）分而是按方法论（执行路径/一致性/红队）分**——角色是同样"如何对" lens 的不同侧面，本质 context 一样，所以堆叠通胀；方法论是**真正不同的 context-injection 配方**，三种 lens 看到的东西不重叠

---

## 四、Review 的精神：Falsificationism

> Popper："the objective of scientific research should be that of disproving, rather than proving, theories"
> Anthropic Code Review verify-step："attempts to disprove each finding before results are posted"

**Reviewer 的根本工作不是"证明 patch 对"，是"尝试 disprove patch"**。

每个方法论 fork 不是"找 patch 哪里好"——是"**找 patch 哪里可能 fail**"：
- 执行路径 fork：尝试找一条执行路径让 patch 崩
- 内部一致性 fork：尝试找文档间矛盾让 patch 描述失真
- 红队 fork：尝试构造恶意场景让 patch 失败
- Verify-step fork：尝试 disprove 每个 finding（false positive 过滤）
- Meta-review fork：尝试找 review_plan 自身设计缺陷

**这个精神贯穿全程**，不只 verify-step。每个 fork 都带 falsification 框架。

---

## 五、Review 不是 gate，是 author 用的诊断工具

> v3.1 关键转变（基于 75 份旧 review 数据）：48% 设计阶段 review 空跑、35% reviewer 越界且越界 = 高质量、PLAN-090 4 视角堆爆 30+ density、findings/done_criteria 比值无强可观测性

**旧 v3 错**：review 当 gate（后置 + 被动 + reviewer 主导）→ 制造假问题（Nature 最痛的点）

**v3.1 转变**：
- **Author 主动调用** review 诊断工具
- Reviewer 返回 finding，**author 决定采纳**（不是 reviewer 强制）
- Author 反驳权——不采纳要写反驳理由（留痕）
- Finding ≠ author 必须修
- 多次反驳 reviewer 不接受 → escalate Nature

**但**——这不等于 author 神谕化。dispute supersede 必须带 **novelty**（new_evidence / new_interpretation / new_patch / new_risk_assessment），否则 M-0.5 commit gate 拒。这建立了对称机制：reviewer 不神谕化 author，author 也不神谕化 reviewer。

---

## 六、Review 是一个阶段，多个调用 mode（渐进暴露）

> Nature："reviewer 是一个阶段，但它里面有很多可以选择调用的工具来做具体的细化，这是渐进式的暴露"

M-1.5 不是单一阶段，是 review 这个领域的 owner，**暴露 3 个调用 mode**：

| Mode | 时机 | 输入 | 输出 |
|---|---|---|---|
| **design-time review_plan_creator** | M-1.2 工程共识 freeze 后 | frozen 工程共识 + brief + 历史失败模式 | review_plan（dimensions + voi_criteria + target hints）|
| **author-time review_runner** | M-1.4 完成 TaskRunCompleted 后 | review_plan + patches + envelope | findings（含 voi_rationale + target.location + suggested_fix_layer）|
| **fix-after verification_review** | M-1.6 修复完成后 | 原 findings + fix patches | findings resolved / 仍 open |

**这跟 M-1.4 心智模型 consistent**——主 SKILL 装系统理解 + 价值观，具体调用按需。

---

## 七、Review Plan 是 review 的灵魂（不是后置触发的额外步骤）

> Nature："当一个系统设计出来的时候，它带着它的上游，它是应该最清楚地知道这个系统该怎么反驳它"

**Review plan 在设计阶段产**——因为只有设计者知道 task 的完整 intent picture（隐含约束 + 长期演化方向 + context-dependent VoI 判据）。**只有设计者能写 VoI criteria**。

**两层结构**：

| Layer | 内容 | 谁写 |
|---|---|---|
| **Layer 1: Dimensions** | 本次设计相关的 review 方法论维度（执行路径 / 一致性 / 红队 哪几个相关）| 设计者基于 task 风险面识别 |
| **Layer 2: VoI Criteria** | 在每个 dimension 下，**什么样的 finding 是有效的**（demo + 长期 = 并发预留有效；纯 demo = 无效）| 设计者基于 task context + intent |

**Review plan 节奏 vs 所有权**：
- **节奏**：在设计阶段（M-1.2 工程共识 freeze 后）产
- **所有权**：M-1.5 模块（schema / logic 定义 / 怎么用）
- **触发**：orchestrator 自动调用 M-1.5.review_plan_creator
- **不需要 patch M-1.2**——风险面 / 维度节点已经在工程共识概念图里（F-08b）；review_plan 是基于这些节点的 plan，由 M-1.5 自己读 frozen 共识产

---

## 八、Reviewer 物理隔离（V-02）

Schema-level 工具白名单：reviewer fork 的 frontmatter 限定工具到 `Read / Grep / Glob / Bash`（**无 Edit / Write**）。

**两个作用**（不只是字面"不让改代码"）：

1. **物理保证**——reviewer 物理上不能修代码
2. **Context 干净**——reviewer 不会被"我改一下试试"诱惑，从而保持评估者 lens

**M-1.5 物理上不能修代码**——它只 produce findings。Author 决定后：
- accept + needs fix → M-1.6 fix skill 接
- accept + no fix needed → 留痕即可（如 advisory finding）
- dispute + novelty → 重新 review 或 escalate
- dispute + no novelty → commit gate 拒 supersede

---

## 九、跟上下游 skill 的边界

| Skill | 边界 |
|---|---|
| **M-1.1 brief** | 不直接消费——brief 通过 task spec / completion_condition 反映 |
| **M-1.2 工程共识** | 消费 frozen 工程共识（含风险面 / 维度节点）；不 patch M-1.2 |
| **M-1.3 task planner** | 消费 task package；可能 task_package 含 review_plan_id 引用 |
| **M-1.4 execution** | 消费 PatchProposed / TaskRunCompleted / envelope；不评 self-check 已覆盖的（task contract completion + envelope truthfulness）——那是 M-1.4 self-check 的事 |
| **M-1.6 fix** | M-1.5 不修代码；produce findings → M-1.6 接 |
| **M-2.x maintenance** | DetectionRule lifecycle 升级（shadow → warning → enforced）属于跨 run consolidation 工作，M-2.x 处理；M-1.5 只产 DetectionRuleProposed（rule candidate 阶段）|

---

## 十、survive review，不神谕化 reviewer

**M-1.5 自身的标准**：review 产出的 findings 必须 self-sufficient enough to survive scrutiny——每个 finding 有 voi_rationale（这个 finding 为什么过 VoI）、target.location（哪里修）、suggested_fix_layer（哪一层改）+ **closure_contract**（修到什么程度算闭合，v2.1.2）。

**对称机制**：
- Reviewer 不神谕化 author（author 反驳权）
- Author 不神谕化 reviewer（dispute 必须带 novelty）
- 都有 escalate Nature 兜底（reviewer P0 冲突无法仲裁时）

---

## 十一、Finding 是合约不是描述（v2.1.2 元层升级）

跟 v3 整体精神同构——v3 每个产出都从"描述"升到"合约"：

| v3 产出 | 描述层 | 合约层 |
|---|---|---|
| Brief | 任务描述 | brief.completion_condition |
| Task package | 任务描述 | 自包含可执行 |
| Envelope | patches 包 | claims + self_check 完整合约 |
| Obligation | 约束描述 | lifecycle state machine + checker |
| Concept supersede | 概念演化 | novelty-gated |
| **Finding** | **问题描述 + target + suggested_fix_layer** | **+ closure_contract（criteria + ripple_targets + forbidden_residuals）** |

**为什么这个升级关键**：v2.1.1 second-round verification 抓到 5 处 cleanup ripple——暴露的不是 reviewer 不够聪明，是 **finding 没有 closure contract，cleanup 完整性靠执行者自觉**。

Finding 是合约后——
- Fix-after mode 能 bounded verify（不漫游）
- 第二轮 review 是 closure verification（不是 free re-review）
- "反复 review" 有工程化终止判据：closure_state ∈ {closed, fix_insufficient, ripple_incomplete, new_unrelated_finding_logged}

这是 v3.1 review 协议"第二轮不引入新问题"判据 applied to review 系统自身的工程化实现。

---

## 十二、读完这个文档我应该带走什么

不是"5 个 X 6 个 Y"列表——是一种感觉：

- **Review 是 context-driven falsification judgement on fact vs intent**
- **灵魂判据是 differentiated context-injection 给 reviewer 不同 lens**
- **Falsification 精神贯穿——每个 fork 尝试 disprove patch，不是证明它对**
- **Review plan 是灵魂，设计阶段产，M-1.5 所有权**
- **Author 反驳权 + dispute 必须带 novelty = 对称机制**
- **V-02 物理隔离保护 context 干净**
- **3 个调用 mode + 5 个 fork = review 这个阶段的 owner 暴露多个工具**
- **Finding 是合约不是描述（v2.1.2）—— closure_contract 让 fix-after bounded，防反复 review**

如果遇到这个文档没覆盖的情况——按这个精神推导。
