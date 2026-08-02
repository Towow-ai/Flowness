# Mismatch & Issue Handling — 原则导向，不查表

> 用途：execution skill 遇到"实际跟期望不符"或"任何 issue"时怎么处理。
> 归属：M-1.4 execution skill 知识库
> 核心精神：智能体能自动找到处理路径——不是查表"问题 A 对应 path B"，是基于系统理解自然推导

---

## 不符不是错——是新发现

TaskPackage 是 planner 基于当时已知信息做的合约。你执行时：
- concept 可能已被 supersede（M-1.2 推进了）
- entity 可能已被 retire（cleanup 做了）
- obligation 可能已被 evolve（M-2.x 演化了）
- 物理 lock 可能冲突（另一 plan 的 task 同时在跑）
- capsule 可能信息不全（M-0.3 template 没覆盖到你需要的）

**这些都是常态——系统在动**。你的工作不是"假装这些都不发生"，是"诚实识别 + 找到最高效化解路径"。

## 化解的精神

**优雅化解，甚至绕过**——但不是为绕过而绕过，是因为这是最高效满足要求的方式。

你不是孤独的执行者：
- 系统理解告诉你这个东西是怎么 work 的——你能自然推导处理方式
- 有合适的 skill 接你接不住的事——这不是推卸是分工
- advisor 是决策者——你拿不准就调
- RePlanTriggered 是兜底——plan 假设错了就回到 planner

## 检测时机与来源（参考，不强制）

不规定"何时必须做 X 检测"——但作为系统理解的一部分，告诉你 mismatch 自然会从哪些地方 surface：

**自然 surface 的时机**：
- **Starting**：读 capsule + TaskPackage 时——如果 capsule 注入的 concept 跟 task.concept_refs 不一致 / read_set entity query 不存在 / write_set 有 lock_holder / @ ref pin_to_snapshot.lock_event_id 跟 concept 当前 superseded_by 不一致——这些都是 starting 时显式 query M-0.2 能拿到的信号
- **Mid-execution**：写代码时遇到需要读 declared_read_set 外文件 / 改 declared_write_set 外文件（V-01 拒 = 物理信号）/ 实际行为跟 done_criteria 验证矛盾 / 实施期间发现的 concept 状态变化
- **Pre-submit**：execution-self-check fork 跑 5 项 blocking_checks 时
- **Post-commit reject**：commit gate 给的 reject reason 本身就是 mismatch 信号

**自然 surface 的来源**：
- M-0.2 查询 API（entity_exists / current_lock_holder / current_version / obligation_state）
- V-01 owner-guard hook 的拒绝（物理信号）
- Capsule 注入内容跟你判断 task 需要的对照
- Git diff / file system / test output（actual 观察）
- Commit gate reject reason

**v3 初版承诺**：M-1.4 不规定"必须每 N 步做完整 check"——是当你**自然观察到**上述信号时立即报。 starting 时做基本 query 是合理动作（你装上系统理解就会自然做），但不要为追求完备性变成 starting checklist 仪式。

## 化解路径有哪些（不是按表查，是按场景选最高效）

### 自己解决

适用场景：你理解了系统，看清这件事的最高效处理方式就是自己处理。

例子：
- @ 引用的 concept 已 supersede，但 locking_policy=auto_accept_latest——直接用最新版本继续（系统设计就是允许的）
- 执行中需要读 declared 外的辅助文件（如读 schema 看字段名）——直接读，envelope 标 actual_read_set drift_reason
- 临时需要个 shell 脚本帮忙——自己写，跑完丢掉（造工具是允许的）
- transient lock 冲突——等几秒重试

**关键**：自己解决不是绕过流程——是 envelope 仍然诚实记录（actual_read_set 标了 drift / 工具用了什么）。

### 提 issue 让别的 skill 接

适用场景：这件事不属于你的 task scope，但需要有人接。

例子：
- 你执行中发现某个 utility module 设计不合理——不是你 task scope 改它——产 FindingCreated 让 M-1.5 / M-1.6 接
- 你执行中发现某个 concept 真的设计错了——产 issue（FindingCreated）+ 继续按现状执行你的 task（除非你的 task 受影响）
- 你执行中发现某个 obligation 表述含糊——产 advisory finding + 按你的理解继续

**关键**：不是把所有问题丢出来——是按"这件事是不是我 task scope"判断。Nature 失败模式："他没有充分去思考而是把问题都丢出来了"。

### 调 advisor（OPUS 决策者）

适用场景：你拿不准这件事怎么处理；多个化解路径之间的权衡你不清楚。

例子：
- 实际需要扩 write_set——是扩 boundary 还是 RePlan？拿不准 → advisor 决定
- 一段代码该怎么写——按 X 还是 Y 风格？拿不准 → advisor 决定
- done_criteria 这条怎么 verify——单元测试够吗？拿不准 → advisor 决定

**关键**：advisor 是决策者，不是顾问——你按 verdict 执行（实施 quality 仍是你的责任）。

### 触发 RePlanTriggered

适用场景：plan 的基础假设破坏——继续执行无意义。

**唯一硬约束**：以下情况必须 RePlanTriggered，不要尝试自解或调 advisor：
- pin_to_snapshot 的 @ 引用 stale（pin 的意义就是锁定，破坏 pin = plan 假设破坏）
- task.done_criteria 跟 brief.completion_condition 矛盾（plan 设计本身错）

其他场景**可能**触发 RePlanTriggered——但应该先尝试其他化解。

### Abort task

适用场景：unrecoverable / advisor 判断要 abort / 外部系统不可用。

- 产 TaskRunCompleted(outcome=aborted, abort_reason)
- cleanup worktree
- 不浪费时间硬扛

## 这不是查表——是判断

注意上面没有"M1 类型 → Path X" 这种映射。原因：

**真实情况是连续的、组合的、有上下文的**——查表会让你机械化，错过明显的化解机会。例：

- "我读了 declared 外的文件"
  - 大部分时候：自己解决（envelope 标 drift_reason 继续）
  - 但如果发现这件事说明 task 边界设错——可能调 advisor 评估扩 write_set
  - 如果发现读的那个文件已被另一 plan supersede——可能 M-1.2 接概念演化
  - 同一现象，不同上下文，不同最优路径

**你的判断流程**：

1. **理解了发生什么** —— 不只是字面观察"declared_set 跟 actual 不一致"，要理解 _为什么_ 不一致
2. **判断这件事的本质归属** —— 是我 task scope 内的 minor adaptation？还是涉及更广的概念问题？还是 plan 本身有错？
3. **选最高效化解路径** —— 自解 / 提 issue / 调 advisor / RePlan
4. **记录** —— 不管走哪条路径，留痕（事件、envelope 字段、log）

## 失败处理（execution_failure / test_failure / etc）

跟 mismatch 同精神——按场景选最高效路径：

- **Transient 错（race / 临时网络 / 临时 lock）**：retry once；不行就升级
- **逻辑错（test fail / runtime error）**：分析是不是自己代码错——是 → 修 → 重跑；不是 → 调 advisor
- **系统级失败（OOM / 外部 service 故障）**：retry once → abort + report system finding（advisor 解决不了）
- **timeout**：调 advisor 决定（可能 abort / extend / split）

**关键约束**：
- retry 不超过 2 次 per failure type per task（无限 retry 是病态）
- 累积升级 5+ 次 → 这个 task 本身有问题，调 advisor 评估是否 RePlan
- abort 必须留痕（TaskRunCompleted + reason + git worktree cleanup）

## 关于"绕过问题"

Resolution rule: choose the smallest route that still satisfies the actual
outcome, constraints, and verification contract; a workaround is valid only
when it resolves the task rather than hiding the mismatch.

**正例**：
- 任务要计算某个统计值，发现现有 utility 已经实现 90%——直接用，加 10% 的 wrapper，而不是从头写
- 任务要测某个 edge case，但当前代码逻辑不支持——产 FindingCreated 让 fix skill 改逻辑，本 task 完成"测试代码已写"的部分
- 任务的 done_criteria 字面要求"X，Y，Z 都验证"，但 Y 已经被另一 task 验证完了——envelope 标 Y 的 done_criteria=satisfied 且 evidence=cite 另一 task 的 envelope

**反例**：
- 任务难写就只写 MVP "先这样"（MVP 偷懒）
- 测试报 fail 就改 expected value 让它 pass（绕过测试不解决问题）
- envelope 标 maintained 但实际 violated（假装一致）
- **正规命令缺能力就 import 编排内部函数手动 spawn 会话**（2026-07-01 夜 OOM 崩机实证：游离
  orchestrator 不带 R11 守卫/并发帽/审计事件——这不是化解，是拆掉了系统的自我保护；正解：
  `uv run --directory harness python -m towow.cli.main orchestrator dispatch <task_id>`，
  或产 FindingCreated 报告命令缺口）

**判别尺**：化解后系统的真相是否仍然准确？**且**：化解走的是系统正规入口，还是拆了门走的墙洞？
前者都是 → 优雅化解；任一否 → 不诚实绕过。

## 你的 evidence 怎么留

不管哪条路径，留痕都是必要：

- **自解** → envelope 字段（actual_read_set drift_reason / claims / etc）+ 必要时 advisory FindingCreated
- **提 issue** → FindingCreated event with full context
- **调 advisor** → AdvisorConsultRequested + AdvisorVerdictDelivered event
- **RePlanTriggered** → 该 event payload 含 trigger_source + affected_tasks + evidence
- **Abort** → TaskRunCompleted(aborted, reason)

留痕的标准：**一个零上下文的 reviewer 能不能从 event log 复盘"为什么这么处理"**——能 → 留痕够；不能 → 补 evidence。
