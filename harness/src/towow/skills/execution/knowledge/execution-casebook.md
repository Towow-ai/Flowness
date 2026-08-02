# Execution Casebook — 12 例：优雅化解 + 智能找路径

> 用途：execution skill 的 case study——遇到类似情况时拿这些例子做参考。
> 归属：M-1.4 execution skill 知识库
> 核心精神：不是"M5 mismatch → Path 2"标签化，是讲故事——executor 怎么理解系统，找到最高效化解路径

---

## 例 1: implementation 顺利完成 + 重用 utility

**Task**：给 EventLog 加 batch_write method。

**Executor 的思考**：
1. 读 capsule 看 task.read_set 包含 EventLog module + batch_write contract concept
2. **先查现有代码** — `grep -r "batch" event_log/` 找到 `event_log/internal.py` 已有 `_internal_batch_append()`
3. 决策：90% 现有实现可用——新 batch_write 是 public API wrapper，复用 internal
4. 写 wrapper + 测试 + 跑通
5. envelope 标 actual_read_set 含 internal.py（drift_reason="reuse existing _internal_batch_append"）
6. self-check pass → submit → accept

**核心**：不重复造轮子。actual_read_set 诚实记录读了 declared 外的 internal.py + 理由。

---

## 例 2: test task 发现 bug 但不擅自扩 scope

**Task**：为 batch_write 加 edge case 测试（empty batch / huge batch / concurrent batch）。

**Executor 的思考**：
1. 写 edge case 测试
2. 跑测试 → empty batch case fail（产品代码漏了 empty check）
3. 想清楚：本 task 是 `test`——不是 `fix bug in implementation`
4. 决策：完成 test task（测试代码已写、覆盖 edge case），把 bug 报给 fix skill
5. envelope.patches 含 test code diff + FindingCreated event 描述 bug
6. envelope.uncertainties 含 "本 task 完成测试代码，但发现 implementation bug 已 report"
7. submit → accept

**核心**：不擅自扩 scope。task scope 是 test，bug 走 finding → M-1.5 / M-1.6 接。

---

## 例 3: 读了 declared 外文件——优雅 self-heal

**Task**：实现 rate limiting middleware。

**Executor 的思考**：
1. 写 middleware 代码
2. 发现需要读 `config/redis_client.yaml` 看 redis URL（declared_read_set 没含）
3. 选最高效化解：直接读 + envelope 标 actual_read_set drift_reason="needed redis URL for client init"
4. 继续写代码完成
5. envelope.actual_read_set 含 redis_client.yaml（declared_in_package=false + drift_reason 具体）

**核心**：mismatch 不一定升级——很多时候 self-heal 是优雅的方式。envelope 诚实记录就够。

---

## 例 4: 写了 declared 外文件——必须升级 advisor

**Task**：给 user_service 加新 endpoint。declared write_set：`user_service/api.py + test_api.py`。

**Executor 的思考**：
1. 写 endpoint 代码
2. 发现要改 `user_service/schema.py`（declared 没含）
3. V-01 owner-guard 拒绝写
4. 这是 mismatch：实际 write_set 比 declared 大
5. 调 advisor：
   - tentative: "schema 改动是 endpoint 必需的——倾向扩 write_set 而非 RePlan"
   - uncertainty: "schema.py 跟 api.py 强耦合还是松耦合？"
6. advisor verdict: `custom_action`——"schema 改动小、强耦合，扩 write_set 合理。返回主 planner session 更新 TaskPackage 加 schema.py 到 write_set，然后重 attempt"
7. 触发更新 + 重 attempt → 顺利完成

**核心**：物理拦截（V-01）→ 必须经流程化解。advisor 帮你做"扩 boundary vs RePlan" 这个判断。

---

## 例 5: pin_to_snapshot stale——直接 RePlanTriggered

**Task**：基于 `@api_contract:CapsuleAssembler.assemble@event-123` 写 caller。locking_policy: pin_to_snapshot。

**Executor 的思考**：
1. Starting 检查 @ 引用：query CapsuleAssembler.assemble 当前 lock_event_id=event-456（已 supersede event-123）
2. 想清楚：pin 的意义就是锁定特定版本——破坏 pin = plan 假设破坏 = critical
3. 不调 advisor（critical 直接 RePlan，这是硬约束）
4. 产 RePlanTriggered event（trigger_source=concept_superseded, affected_tasks=[this_task]）
5. abort task
6. wrapper cleanup worktree

**核心**：唯一硬约束之一——pin stale = critical = RePlan。不浪费时间调 advisor。

---

## 例 6: write_set 锁冲突——优雅等待

**Task**：改 `utils/shared.py`。declared write_set: utils/shared.py。

**Executor 的思考**：
1. Starting 检查 V-01：shared.py 被另一 task lock 占着
2. 想：可能 race 也可能真冲突
3. 决策：先 retry once（等 30 秒）
4. 仍锁 → 调 advisor：
   - tentative: "可能 cross-plan-check 漏了——需要 wait 还是 RePlan？"
   - uncertainty: "另一 task 还要多久？"
5. advisor verdict: `custom_action`——"另一 task 是 small refactor，预期 < 5min，wait + 重 check"
6. wait 5 min → 重 check → 锁释放 → 继续执行

**核心**：transient 问题 retry / wait 是优雅化解，比立即 RePlan 高效。

---

## 例 7: done_criteria 跟 brief 矛盾——直接 RePlanTriggered

**Task**：implement pre-condition validator。done_criteria：3 条 validator 都实现 + 测试通过。

**Executor 的思考**：
1. Starting 检查：对照 done_criteria 跟 brief.completion_condition
2. 发现 brief 实际要求 4 条 pre-condition——done_criteria 只覆盖 3 条
3. 想清楚：plan 设计本身错——不是我执行能解决的
4. 不调 advisor（critical 直接 RePlan，这是硬约束）
5. 产 RePlanTriggered event（trigger_source=done_criteria_brief_mismatch）
6. abort

**核心**：plan 设计错就是 plan 设计错——回去让 planner 改。

---

## 例 8: 信息不全调 advisor + advisor 决定继续

**Task**：concept_update task——supersede old_concept 到 new_concept。declared active_obligation O 必须保持。

**Executor 的思考**：
1. 启动 M-1.2 supersede 协议
2. mid-execution 发现 obligation O 表述跟 new_concept 微妙不一致——不确定算不算 violation
3. 调 advisor：
   - tentative: "倾向认为 obligation O 表述是 protocol-level（跟 new_concept 一致），不算 violation"
   - uncertainty: "措辞 ambiguity 让我不确定"
4. advisor verdict: `custom_action`——"是 protocol-level，跟 new_concept 一致。continue。但加一个 advisory FindingCreated 让 M-2.4 maintenance 评估是否需要 evolve obligation 表述更精确"
5. 继续 supersede + 加 FindingCreated
6. envelope.active_obligations_status[O].status = maintained + evidence
7. submit → accept

**核心**：拿不准调 advisor + advisor 给具体决策 + 留 finding 让后续 skill 跟进。

---

## 例 9: 实现中发现 task 边界错——advisor 决定 RePlan

**Task**：给 commit_gate 加 novelty check。declared write_set：commit_gate.py。

**Executor 的思考**：
1. mid-execution 发现 novelty check 跟现有 obligation_check 逻辑严重耦合——分开实现要 duplicate 80% 代码
2. 这违反"不重复造轮子"原则——拿不准怎么处理
3. 调 advisor：
   - tentative: "倾向 refactor obligation_check 让两者共享 logic——但这扩 write_set 多个文件"
   - uncertainty: "是 expand boundary 还是该 RePlan task？"
4. advisor verdict: `use_path_trigger_replan`——"这不是 implementation task，是 refactoring task——RePlan 把 obligation_check 也纳入 + 重新设计 task 边界"
5. 产 RePlanTriggered event
6. abort

**核心**：发现 task 本质不对——advisor 决定 RePlan 比硬扛优雅。

---

## 例 10: 并行 task 中一个失败——其他继续

**场景**：plan 有 5 个并行 implementation task，task-3 失败。

**Executor (task-3) 的思考**：
1. test 失败重复 3 次（已超 retry limit）
2. 调 advisor：
   - tentative: "可能代码逻辑有问题——但我反复 review 找不到具体 bug"
   - uncertainty: "可能是 concept 定义没说清楚 corner case"
3. advisor verdict: `use_path_trigger_replan`——"测试 fail pattern 显示 concept 定义模糊。本 task 失败，触发 RePlan 让 planner 补 investigation task 澄清 concept"
4. 产 RePlanTriggered event
5. abort task-3

**影响**：task-3 abort；其他 4 个 task 不受影响继续（除非依赖 task-3 产出）。

**核心**：abort 一个 task 不污染其他——并行执行的好处。

---

## 例 11: envelope reject 后 rebase + retry

**Task**：implementation 顺利完成 → envelope 提交 commit gate → reject "write_conflict: entity X has newer version event-789"。

**Executor 的思考**：
1. envelope reject + reason 是 write_conflict
2. 想清楚：执行期间另一 task 改了 entity X——我的 patch 基于 stale snapshot
3. 不立即 abort——这是常见 race，rebase 通常能解决
4. 调 advisor：
   - tentative: "看起来是 race，倾向 git rebase + 重 apply patches + retry submit"
   - uncertainty: "rebase 会不会有 semantic conflict？"
5. advisor verdict: `custom_action`——"先 git rebase；如果 semantic conflict 再升级。"
6. rebase + re-execute（更新基线）
7. re-submit envelope → accepted

**核心**：reject 不一定 = abort——多数 case rebase + retry 能解决。

---

## 例 12: Unrecoverable failure abort——干净退出

**Task**：implement client for external service。

**Executor 的思考**：
1. 实施代码
2. 跑测试——外部 service 持续返回 500（连续 10 分钟）
3. 调 advisor：
   - tentative: "外部 service 故障——不是我代码问题，abort 等服务恢复"
   - uncertainty: "要 retry 多久才合理？"
4. advisor verdict: `use_path_abort_task`——"外部 service 故障非本 task 范围；abort + report system finding"
5. 产 TaskRunCompleted(outcome=aborted_external, reason="external_service_unavailable for 10+ minutes")
6. 产 FindingCreated 描述 system finding
7. wrapper cleanup worktree

**核心**：abort 不是失败——是合适处理。留痕 + finding 让后续 skill / Nature 跟进。

---

## 12 例的共同精神

1. **不查表 X → Y** —— 每个 case 都先想"系统怎么 work / 这件事的本质是什么 / 最高效化解路径"
2. **诚实记录 actual** —— envelope 真实反映发生了什么
3. **不重复造轮子** —— 例 1 是核心示范
4. **不擅自扩 scope** —— 例 2 是核心示范
5. **优雅化解优先** —— 例 3 self-heal / 例 6 wait / 例 11 rebase 都是优雅化解
6. **该升级时升级** —— 例 5 / 例 7 critical 直接 RePlan / 例 4 / 例 9 advisor 决定
7. **abort 不是失败** —— 例 12 系统级故障 abort 是负责任处理
8. **留痕复盘** —— 每个 case 都有 envelope + event + finding 让后续可复盘
