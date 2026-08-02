# Fix vs Execution — 边界、复用、独有

> 用途：M-1.6 跟 M-1.4 是独立同辈不是子类——但有大量底层复用。讲清楚哪些是复用、哪些是独有、哪些容易混淆。
> 归属：M-1.6 fix skill 知识库
> 核心精神：M-1.6 不继承 M-1.4 的执行自由度——共享底层执行原则，不共享 scope policy

---

## 根本对偶

| 维度 | M-1.4 execution | M-1.6 fix |
|---|---|---|
| **入口** | task_package | finding + closure_contract |
| **目标** | task done_criteria 满足 | closure_state = closed |
| **scope** | task scope（含 adjacent 决策权）| closure_contract scope（紧）|
| **新代码** | 主要写新代码 | 修既有代码 + 局部新代码 |
| **session** | task 内同 session 可继续 | **必须 new session**（不继承原偏见）|
| **完成判据** | task done_criteria + envelope self_check | closure_criteria + ripple_targets + forbidden_residuals |
| **失败处理** | self-check fail → fix（M-1.6 接）| fix_insufficient → reopen 新轮 fix |
| **adjacent 处理** | 决策权（可顺手优化某些）| 不顺手做——产新 finding |
| **escalation 语言** | 通用 escalation | F-09b 产品语言（Nature 不工程师）|

---

## 复用 M-1.4 的 knowledge

M-1.6 capsule 投喂时引用 M-1.4 既有 knowledge 文件——

### `system-mental-model.md`（v3 整体精神）

包含 v3 cross-cutting 原则：
- Existence → Projection → Circulation → Response
- Event log canonical
- 每个产出是合约
- @ 引用 + locking policy
- 上下游 skill 分工
- Advisor / fork 关系
- "你不是机械执行者——你是智能 agent"

**M-1.6 完全复用**——v3 整体精神对 fix 同样适用。

### `code-quality-principles.md`

包含代码质量原则：
- 修代码的诚实性
- 不假装完成
- envelope honesty
- self-check 真跑不假装

**M-1.6 完全复用**——但 scope 限制不同（见下）

### `git-safety-and-queue.md`

包含 git 操作约束：
- worktree 物理隔离（P-04 + F-07e）
- V-01 owner-guard 写权限限制到 declared write_set
- commit 走 `uv run --directory harness python -m towow.cli.main submit`（M-3.1 submit wrapper 串行 mutex）
- accept → wrapper 自动 git push + cleanup
- reject → wrapper 保留 worktree + 返回 reject reason

**M-1.6 完全复用**——fix patches 也走同样的 git 协议。

### `mismatch-and-issue-handling.md`

包含 mismatch 化解原则：
- 不查表——按场景选最高效路径（自解 / 提 issue / 调 advisor / RePlan / Abort）
- "优雅化解，甚至绕过——但不是为绕过而绕过"
- 检测时机自然 surface
- 留痕标准

**M-1.6 完全复用**——fix 中也有 mismatch（如发现 ripple_target 不存在 / closure_criteria 跑不通）

### `advisor-collaboration.md`

包含 advisor 关系：
- advisor 是决策者（不是顾问）
- evidence scope binding
- verdict 在 capsule 范围绑定
- 不神谕化 advisor

**M-1.6 完全复用**——fix 中拿不准时同样 consult advisor（如不确定 escalation 怎么写 nature_facing_summary）

---

## M-1.6 独有的 knowledge

跟 closure_contract execution 直接相关的——

| 文件 | 内容 | 为什么独有 |
|---|---|---|
| `fix-mental-model.md` ★ | event-driven closure mental model | 跟 M-1.4 execution 心智不同 |
| `fix-pitfalls.md` ★ | 8 个 fix-specific 失败模式 | imperative cascade / scope creep / engineering jargon 等 fix 特有 |
| `closure-contract-execution.md` | 三段式执行（主/ripple/residual）| closure_contract execution 协议 |
| `feasibility-check.md` | graduated 6 项 feasibility check | M-1.4 没这个（M-1.4 拿到 task_package 就进 fix）|
| `outcome-events-and-cascade.md` | 4 种 outcome event + O-14 | M-1.4 outcome 不同（TaskRunCompleted 单一为主）|
| `escalation-product-language.md` | F-09b 产品语言 escalation | M-1.4 escalation 是通用 |
| `fix-casebook.md` | 12 fix 案例 | fix-specific 案例 |
| `fix-vs-execution-boundaries.md` | 这份 | 边界文档 |

---

## 容易混淆的边界

### 混淆 1: "我能不能像 M-1.4 那样顺手优化 adjacent code？"

**M-1.4** 在 task scope 内有 adjacent 决策权——可以顺手做。
**M-1.6** scope 严格 = closure_contract.ripple_targets ∪ 主修复点——adjacent 想改 → 产新 finding 不顺手做。

**原因**：
- M-1.4 input 是 task_package（含相对自由 scope）
- M-1.6 input 是 closure_contract（明确 scope）
- 顺手做 = scope creep = 制造第二类 bug

### 混淆 2: "我可以继续用原 session 的 reasoning 吗？"

**M-1.4** 同一 task 内可以继续——session 的 thinking trace 是 asset。
**M-1.6** 必须 new session——不能继承原 M-1.4 执行偏见。

**原因**（Nature 强调）：
- 原 session 当时这样写有特定 reasoning（"为什么当时这样"）
- review 发现这件事不对——可能原 reasoning 本身有偏见
- new session 不带这个偏见
- 物理保证：M-0.3 fix capsule template 不投喂原 session reasoning trace

### 混淆 3: "我能不能 self-trigger 跑 review 验证我的 fix？"

**M-1.4** 完成后产 TaskRunCompleted——M-1.5 author_time review 由 F-11 自动触发。
**M-1.6** 完成后产 FixCompleted——M-1.5 fix-after mode 由 F-11 自动触发。

**两者都不主动 call M-1.5**——event-driven，不 imperative cascade。

但 M-1.6 跟 M-1.4 的 cascade 路径不同：
- M-1.4 → TaskRunCompleted → M-1.5 author_time（找新 finding）
- M-1.6 → FixCompleted → M-1.5 fix-after（bounded verify 原 closure_contract）

### 混淆 4: "我 self-check 的内容跟 M-1.4 是一样的吗？"

**M-1.4 self-check**（envelope blocking_checks）：
- task contract completion
- envelope truthfulness
- read_set / write_set drift
- active_obligations status
- claims accuracy

**M-1.6 self-check**（envelope blocking_checks）：
- `fix.closure_criteria_self_verified`
- `fix.ripple_targets_synced`
- `fix.forbidden_residuals_zero`
- `fix.consensus_respected`
- `fix.review_plan_respected`

**精神同**（都是 SkillArtifactSelfCheck generic）—— blocking_check 内容不同。

### 混淆 5: "我修的代码也要过 review 吗？"

是。但 review 类型不同：
- **M-1.4 产出 → author_time review**：自由找 finding（method-execution-path / method-consistency / method-red-team）
- **M-1.6 产出 → fix-after review（路径 B bounded）**：按 closure_contract 验证（不漫游）

M-1.6 不需要"再次过 author_time review"——它的 review 责任是 closure verification，由 M-1.5 fix-after 处理。

---

## Capsule 投喂差异

M-1.4 capsule（scene_type=execution）含：
- task_package
- concept_graph 邻域
- active_obligations
- 8 个 knowledge files（system-mental-model / code-quality / git-safety / mismatch / advisor / envelope-honesty / etc）

M-1.6 capsule（scene_type=execution + execution_mode=fix 或 scene_type=fix——查 M-0.3）含：
- finding（含 closure_contract）
- 当前代码状态（git）
- task spec（理解原意图）
- 本次共识 + review plan（F-09a 遵守）
- concept_graph 邻域
- active_obligations
- 复用的 M-1.4 knowledge files（system-mental-model / code-quality / git-safety / mismatch / advisor）
- M-1.6 独有 knowledge files（fix-mental-model / fix-pitfalls / closure-contract-execution / feasibility-check / outcome-events-and-cascade / escalation-product-language / fix-casebook / fix-vs-execution-boundaries）
- **不投喂 原 M-1.4 session reasoning trace**（防偏见）

---

## Fork 结构差异

### M-1.4 的 fork

- `advisor-consult` fork（OPUS 决策者）
- `execution-self-check` fork（envelope self-check 跑通）

### M-1.6 的 fork

- `advisor-consult` fork（**复用 M-1.4 既有**）
- `fix-self-check` fork（**类似 M-1.4 self-check fork 但 blocking_checks 不同**）

**不为 escalation 造独立 fork**（critique 同意）—— escalation 是 schema 化表达转换，main session 子操作。

**不为 feasibility check 造独立 fork**—— 6 项 checklist 是 main session 子操作。

---

## 元层：v3 中的"对偶 skill" pattern

M-1.4 + M-1.6 是 v3 第一对 "做 + 修做" 对偶 skill——

未来可能有的对偶：
- M-1.1 brief + brief-fix？（如果 brief 也需要 review-fix cycle）
- M-1.2 工程共识 + consensus-fix？（如果共识也需要）

**M-1.4 + M-1.6 的设计 pattern**：
- 独立同辈（不是子类继承）
- 复用底层 knowledge（v3 整体精神）
- 独有 mental model + pitfalls + casebook（specific domain）
- Capsule 不投喂原 session reasoning（new session 物理保证）
- 双层 verification（self + cross-check）

这个 pattern 可能成为后续"做 + 修做"对偶 skill 的 template——但 M-1.6 详设不强求这个 pattern 通用，只为当前 fix 场景设计。
