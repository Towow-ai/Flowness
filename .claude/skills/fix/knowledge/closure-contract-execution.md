# Closure Contract Execution — 怎么把 finding 的合约物理闭合

> 用途：M-1.6 拿到 closure_contract 后怎么一步步把它执行掉。不是查表"contract X 对应 step Y"，是按 contract 字段自然推导。
> 归属：M-1.6 fix skill 知识库
> 核心精神：closure_contract 是合约，M-1.6 是合约执行者——按字段走，每个字段对应具体动作

---

## Closure Contract 三个核心字段

复习 M-1.5 v2.1.2 给的 finding.closure_contract schema：

```yaml
closure_contract:
  closure_criteria:                # "什么样才算 finding 修完了"
    - condition: string            # 可验证条件
      verification_method: enum [grep | schema_check | projection_check | manual_reasoning | test | replay | git_diff]
      expected_result: string

  ripple_targets:                  # "修哪一处必须同步更新哪些位置"
    - artifact: string
      location_hint: string
      reason: string

  forbidden_residuals:             # "什么残留模式说明没修干净"
    - pattern: string
      rationale: string
      check_method: enum [grep | schema_check | manual_reasoning]
```

M-1.6 不需要"理解 finding 是什么 bug"——只需要按这三个字段走。

---

## 三段式执行

### 第一段：主修复点修复

1. 读 finding.target.location（主修改位置）+ finding.suggested_fix_layer（哪一层改）
2. 按 suggested_fix_layer 实施修复——具体改法是 M-1.6 的判断（intelligent agent，不是查表）
3. 自检：修复后 finding 描述的失败场景能否再触发？

**关键约束**：
- 主修复点改动严格在 finding.target.location 附近（不漫游）
- 改法可以跟 suggested_fix_layer.alternative_layers 之一不同——但必须有 evidence + rationale
- 不"顺手"改 adjacent code（防 #3 scope creep）

### 第二段：Ripple Targets 逐个 sync

对 closure_contract.ripple_targets 每一项：

1. 读 ripple_target.artifact + location_hint
2. 按 ripple_target.reason 理解为什么这里要同步
3. 实施同步更新
4. 标 sync_status=synced（envelope.self_check 数据）

**关键约束**：
- ripple_targets 是必填合约——不能漏（防 #4 closure ripple incomplete）
- 不在 scope 外漫游找新 ripple——只 sync closure_contract 列出的（防 scope creep）
- 如果某个 ripple_target 不适用（reason 已经被其他改动解决）→ 标 not_applicable + 必须有 evidence

### 第三段：Forbidden Residuals 清扫

对 closure_contract.forbidden_residuals 每一项：

1. 按 check_method 跑（grep / schema_check / manual_reasoning）
2. 验证 occurrences=0
3. 任一发现残留 → 回到第一段或第二段补修

**关键约束**：
- forbidden_residuals 是 "最后一道防线"——确认主修 + ripple 后没遗留旧模式
- check_method 必须实跑——不能假装跑

---

## 每条 closure_criterion 怎么 verify

按 verification_method enum 分类：

### grep
```bash
grep -n "<pattern>" <files>
# 验证 occurrences = expected_result
```
适用：旧术语清除 / 旧 API call 替换 / specific string presence

### schema_check
```bash
# 读 schema 文件，检查字段是否存在 / 类型正确
```
适用：API contract / DB schema / config schema 变更

### projection_check
```bash
# 查询 M-0.2 projection
# 验证 projection state 满足 expected_result
```
适用：concept lifecycle state / obligation state 变更

### test
```bash
# 跑 test case
# 验证 pass / fail / specific assertion
```
适用：行为正确性 / 回归 / 性能

### git_diff
```bash
# 门对"本任务 commit 集"跑 git show --format= --name-only 拿改动文件清单
# 验证 verification_pattern 对清单逐路径命中数 == expected_occurrences
```
适用：'文件 X 在本任务 commit 零改动' 类 INV 不变量（改动面控制）

⚠ commit 集是 run 上下文（合约签订时不可能知道未来 commit sha）——**别把
`git diff <commit>^..<commit>` 这类命令文本塞进 search_scope**（T-SL-A1 反模式，
发布门 + 复算门都会拒）。当前只在 M-1.4 execution done_criteria 复算链接线；
M-1.5 fix 闭合环未接线（合约里用了会 fail-closed 明拒，不是降级）。
（finding-machine-check-encoding-gap-1780563112 / Ledger Conflict 19）

### replay
```bash
# event log replay 重现
# 验证 replay 结果符合 expected_result
```
适用：复杂状态机变更 / 多 event sequence 验证

### manual_reasoning
- 需要语义判断——最弱的 verification method
- 应少用——M-1.5 reviewer 写 closure_criteria 时 manual_reasoning 应该是 last resort
- 如果 closure_criteria 全是 manual_reasoning → feasibility check #6 应该 catch：closure_contract 本身可执行性弱，可能要 FindingCreated 反向

---

## Envelope 怎么记录

envelope.self_check 含 closure-execution 5 个 blocking_check（产 FixCompleted 前必 passed）：

```yaml
self_check:
  blocking_checks:
    - id: fix.closure_criteria_self_verified
      status: passed
      evidence:
        criteria_results:
          - criterion: "<closure_criterion.condition>"
            passed: true
            verification_method: <method>
            actual_result: "<跑出的结果>"
            expected_result: "<合约要求>"
            evidence_artifact: "<command output / file path / test report>"
    
    - id: fix.ripple_targets_synced
      status: passed
      evidence:
        ripple_results:
          - target_artifact: <artifact>
            target_location: <location>
            sync_status: synced  # or not_applicable + evidence
            diff_summary: "<改了什么>"
    
    - id: fix.forbidden_residuals_zero
      status: passed
      evidence:
        residual_check_results:
          - pattern: <pattern>
            found_occurrences: 0
            check_method: <method>
            command_evidence: "<actual grep / check command + output>"
    
    - id: fix.consensus_respected
      status: passed
      evidence:
        - 修复中未引入新概念（或 consensed concept @ ref list）
        - 未打破 obligation（或 obligation update event ref）
    
    - id: fix.review_plan_respected
      status: passed
      evidence:
        - 修复未引入新风险面（或 review_plan supersede event ref）
        - 触发的 review dimension 仍覆盖
```

**任一 blocking_check status != passed → M-0.5 SkillArtifactSelfCheck 物理拒，不能产 FixCompleted**。

---

## "诚实修"不"假装修"

修复有两个层次：
- **修动作**——改了代码 / 改了文件
- **修闭合**——closure_contract 真的满足

**M-1.6 的目标是修闭合，不只是修动作**。这意味着：

- 不能"我改了 line 42 但没跑 grep 验 forbidden_residuals"
- 不能"ripple_target 跑了一遍但没仔细看是否真的同步"
- 不能"closure_criterion 是 test method 但我没真跑 test"

**判别尺**：你的 envelope.self_check.blocking_checks 每条 evidence 是 specific artifact（command output / file path / test report）还是抽象描述？前者诚实，后者假装。

---

## 跟 M-1.5 fix-after mode 路径 B 的关系

M-1.6 self-check 跟 M-1.5 fix-after 独立 verify 是 **双层 verification**：

```
M-1.6 产 FixCompleted 前
   ↓ envelope.self_check（含 5 blocking_check）
   ↓ M-0.5 SkillArtifactSelfCheck 物理拒不合格的
   ↓
M-0.5 accept → FixCompleted event 入 log
   ↓
F-11 自动触发 M-1.5 fix-after mode
   ↓ M-1.5 verify-step fork 路径 B（bounded closure verification）
   ↓ 独立按 closure_contract 验证（不依赖 M-1.6 self-check 结果）
   ↓
closure_state: closed / fix_insufficient / ripple_incomplete / new_unrelated_finding_logged
```

**两层为什么都要**：
- M-1.6 self-check 是必要条件——避免提交垃圾 fix
- M-1.5 fix-after 是独立 cross-check——避免 M-1.6 self-check 自欺欺人

类比 M-1.4 self-check + M-1.5 author_time review——同精神。

---

## 多轮 fix 的收敛（fix_insufficient / ripple_incomplete reopen 后）

如果 M-1.5 fix-after 返回 fix_insufficient 或 ripple_incomplete → FindingCreated reopen → 新一轮 M-1.6 fix：

- **scope 还是 bounded**——ripple_incomplete reopen 时 scope 限定到 ripple_targets，不全文重做
- **必须带 novelty**（M-0.5 NoveltyCheck cross-cutting）—— 每轮 fix 必须有新尝试 / 新 evidence
- **多轮无 novelty → escalate**（EscalationRaised + nature_facing 语言）

这跟 M-1.5 v2.1.2 的 closure cycle 状态机完全对齐——novelty-gated 收敛防振荡 + bounded scope 防漫游。

---

## 关键退出条件

- 5 个 blocking_check 全 passed → 产 FixCompleted
- feasibility check 不通过 → 产对应 outcome event（FindingCreated 反向 / RePlanTriggered / supersede / EscalationRaised）
- 多轮 fix 仍不闭合 + 无 novelty → 产 EscalationRaised

**M-1.6 不在没产任何 outcome event 的情况下退出**——会留 zombie state。
