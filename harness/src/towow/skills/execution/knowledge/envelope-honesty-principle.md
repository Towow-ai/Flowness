# Envelope Honesty Principle — 宪法级原则

> 用途：execution skill 提交 envelope 时怎么填字段。
> 归属：M-1.4 execution skill 知识库
> 核心精神：不规定无数情况——告诉模型 envelope 在 commit gate 怎么被消费，模型自然知道怎么诚实填

---

## Envelope 是什么

Envelope 是你跟系统沟通的契约——你向 commit gate 提交它，gate 据此判 accept / reject。

**Envelope 的两个角色**：
1. **Commit gate 的判决依据** —— gate 检查 envelope 字段是否符合系统约束
2. **后续审计的真相源** —— review skill / maintenance / audit fork 都从 envelope 反推"这次 commit 发生了什么"

## Commit Gate 怎么消费你的 envelope

读一遍 M-0.5 §3 你会看到，gate 大致这样想：

```
对每个 envelope.patches[i]:
  → 看 patch_type
  → 如果 code_diff：核对 git diff 真实存在
  → 如果 concept_event / task_event / obligation_event：核对对应 EventIntent 在 batch 里
  → 核对 affected_entities 跟 envelope.write_set 一致

对 envelope.write_set:
  → 检查每个 entity 没被其他 in-flight envelope 锁住
  → 不冲突 → 加锁 → 通过
  → 冲突 → reject (write_conflict)

对 envelope.active_obligations_status:
  → 全 maintained + patches 不违反 → 通过
  → 任一 violated → 产 ObligationViolated event + reject
  → not_applicable + evidence 充分 → 通过 + advisory finding

对 envelope.self_check:
  → 看 self_check.passed
  → passed → 通过该 check
  → not passed → reject (self_check_failed)

对 envelope.uncertainties:
  → 不参与判决（M-0.4 §7.2 原则）
  → 但记录下来，review 用
```

### Commit gate 跟 self-check 是两层 check，不互相替代

**重要边界**——别把它俩当一回事：

| 层 | 谁做 | check 什么 |
|---|---|---|
| **Skill semantic self-check** | execution-self-check fork（OPUS）| Task contract 是否完成 + envelope 是否诚实反映 actual——这是 skill 内部语义判断 |
| **Commit gate mechanical/protocol check** | M-0.5 commit gate | Protocol-level 校验：envelope schema / write_set 冲突 / freshness drift / obligation declaration / novelty / batch 完整性 |

**commit gate 不重跑 execution-self-check**——它接受 envelope.self_check.passed 作为 skill semantic 校验通过的声明（generic SkillArtifactSelfCheck 设计）。

**但 commit gate 仍然跑自己的 mechanical/protocol checks**——这些跟 skill semantic 无关，是 protocol 边界。self-check passed ≠ commit gate accept；commit gate 仍可能 reject（write_conflict / freshness_drift / etc）。

**判别**：
- self-check 验证 "我做完了 task contract + envelope 诚实" → skill semantic 层
- commit gate 验证 "envelope 符合 protocol 约束 + 不破坏全局不变量" → protocol 层
- 两层都通过才算真 commit

**自然推导**：
- 你不诚实标 actual → patches 跟 git diff 对不上 → reject
- 你假装 violated 的 obligation 是 maintained → audit fork 之后会抓 → severe finding（commit 已 accept 但 audit reject）
- 你 self_check 造假 status=passed → audit fork 抓 → severe finding
- 你不报 uncertainties → review 不知道该重点看哪——你的代码出 bug 时 review 漏掉 → 责任在你

**所以**：诚实填 envelope 不是道德要求——是物理上对你最有利的选择。

## 这意味着你怎么填

### actual_read_set / actual_write_set

**原则**：填你 _真正_ 读了 / 写了什么——declared 是 planner 的预期，actual 是事实。

- declared 范围内的 → 标 declared_in_package=true
- declared 范围外的 → 标 declared_in_package=false + 必须 drift_reason
- drift_reason 必须具体（"为读 schema 文件确定 field name"），不是"for context"

**推论**：
- 读了 declared 外文件 → 必须补 actual_read_set entry + drift_reason
- 写了 declared 外文件 → 这是 V-01 owner-guard 物理阻止的——必须先调 advisor 决定扩 write_set 还是 RePlan，再继续

#### actual_set 的运行时来源（v3 初版的物理现实）

write_set 跟 read_set 的可观测性不一样：

| | 怎么知道 | v3 初版可靠度 |
|---|---|---|
| **actual_write_set** | 从 git diff + event log 机械派生（M-0.4 §3.3）| 高——git diff 是 ground truth |
| **actual_read_set** | 需要组合多个 source 拼出来 | 中——需要诚实声明 |

**actual_read_set 的 4 个来源（v3 初版组合策略）**：
1. **Capsule declared read_set**（必有）—— 上界，capsule 注入的概念图邻域、@ refs 锁定版本
2. **Tool access log**（M-3.1 提供时）—— bash / read tool 调用的文件路径序列
3. **Executor declared extra reads**（你诚实补）—— 实施中读了 declared 外文件，标 drift_reason
4. **Self-check reconciliation**（execution-self-check fork 检查）—— 对照 git diff 反推可能读了的文件（修改 X 文件 → 你大概率读了 X / X 的 caller）

**v3 初版承诺**：M-1.4 **不要求**完美的低层 read tracing。低层 trace（每个 read tool call 自动入 envelope）是 M-3.1 工程化外壳的事，v3 初版可能没有。

**M-1.4 v3 初版要求**：
- **语义上相关的 extra reads 必须诚实声明**（你为某个判断读了某文件就报）
- 不需要把无意义的 ls / find / grep 全部记录
- 不知道是不是 semantically relevant → 默认诚实报（"我为 X 目的读了 Y"），让 self-check fork 判断重要性

**为什么这样设计**：
- 强制完美 read tracing → 模型负担过重 / envelope 膨胀
- 完全不要求 → 模型会偷懒漏报
- 折中：语义相关的诚实报，琐碎的不强求——靠模型智能判断"这次读对 task 决策有影响吗"

### active_obligations_status

**原则**：每条 declared active_obligation 都要标 maintained / violated / not_applicable + evidence。

- maintained → evidence 是"这条 obligation 要求 X，我的 patches 没违反 X，因为..."
- violated → evidence 是具体哪个 patch 哪段违反；commit gate 会 reject + 产 ObligationViolated event
- not_applicable → evidence 是"本 task 实际场景跟 obligation 假设不符，原因..."；advisory finding

**关键**：你不主动产 ObligationViolated event——M-0.5 commit gate 的 authority。

### patches

**原则**：每个 patch 是你实际产出的一项改动。

- code_diff：git diff hash + 简短 summary + affected_entities
- concept_event / task_event / obligation_event：domain event 列表（commit gate 接受后跟 envelope 同 batch 写入）
- doc_change / config_change：文件路径 + summary

**推论**：
- patch 的 affected_entities 跟 envelope.write_set 必须一致——gate 会核对
- code_diff 的 git commit 必须存在 worktree 中——gate 会查

### claims

**原则**：从 actual_write_set / active_obligations / read_set 派生——表明你需要什么"权利"。

实际上 submit wrapper 帮你派生（M-0.4 §7.1 设计）——你不需要手动填全。你需要做的是确保 actual_set 准确，claims 自动跟上。

### uncertainties

**原则**：诚实报"我执行完后仍不确定的点"。

- uncertainties 不参与 commit 判决——commit gate 不会因为你报了 uncertainties 就 reject（M-0.4 §7.2 原则）
- 反向：不报 uncertainties 不会让你过 gate 更容易——反而 review 漏看导致 bug 时责任在你

**报什么**：
- "X edge case 我没完全测到"
- "Y 实现这种写法可能在 Z 场景有副作用，我不确定"
- "concept A 跟 concept B 的边界我按 X 理解，可能跟设计者意图有偏差"

**不报什么**：
- "我感觉可能有问题"（没具体 affected_aspect）
- "整个东西都没把握"（太泛化）

### self_check

**原则**：必须跑 execution-self-check fork（OPUS）独立判断。

你（execution main session）不能 self-assess——必须 OPUS fork 跑 5 项 blocking_checks：
1. `execution.done_criteria_satisfied`
2. `execution.actual_set_recorded`
3. `execution.obligations_maintained`
4. `execution.no_unhandled_mismatch`
5. `execution.git_committed`

fork 返回的 status 直接放 envelope.self_check——commit gate 接受不复查（M-0.5 generic SkillArtifactSelfCheck 设计）。

**关键**：status=passed 但实际没检查 = 严重失败模式。后续 audit fork 抓到 → severe finding。

## 为什么这样设计

理解了 commit gate 怎么消费 + audit fork 怎么抽查——你自然知道：

1. 诚实是物理最优——欺骗短期通过 gate，长期被 audit 抓
2. 字段精确度有意义——drift_reason 写"for context"vs 写"为读 schema 确定 field name"，前者审计时无法复盘，后者一目了然
3. 留 evidence 不是仪式——是让零上下文 reviewer 能复盘

## 自然推导的核心 mental check

提交前问自己：

**"如果一个零上下文 reviewer 拿到我的 envelope，他能不能独立验证 'task 已完成且没破坏什么'？"**

能 → envelope 够诚实。
不能 → 补 evidence。

**"任何我没解决的 mismatch / uncertainty 都已经记录了吗？"**

是 → ok 提交。
否 → 不能提交。

## 不要做的（Nature 失败模式映射）

| 误做法 | 为什么错 |
|---|---|
| 把 declared 复制到 actual 假装一致 | 欺骗系统——commit gate / audit 会抓 |
| drift_reason 写 "for context" | 审计时无法复盘——等于没记 |
| obligation 偷偷标 maintained | audit fork 抓到 = severe finding |
| self_check 主 session self-assess | 必须独立 OPUS fork——这是 design |
| uncertainties 不报 | review 漏看 → bug → 责任在你 |
| 多个 task 累积提交一个 envelope | 一 task 一 envelope（task_id 必填）|

这些不是规则——是从 commit gate / audit 怎么消费 envelope 自然推导出的判断。
