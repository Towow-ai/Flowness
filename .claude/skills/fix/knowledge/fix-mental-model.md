# Fix Mental Model — Event-Driven Closure

> 用途：装在 fix skill 脑子里的根本心智模型。读完这个文档 skill 应该有"啊，fix 是这样的"的感觉，然后遇到具体情况自然推导。
> 归属：M-1.6 fix skill 知识库 ★ 核心文档
> 来源：v3 整体精神 + M-1.5 v2.1.2 Finding Closure Protocol + Nature 校准（"issue 机制自动触发图的下一个节点"）+ critique 文档收束

---

## 一、Fix 是什么——根本逻辑

跟 M-1.5 对偶：
- **Review** = 给已做出来的东西评估"它真的满足意图吗"——产 finding（含 closure_contract）
- **Fix** = **把 finding 的 closure_contract 物理闭合**——产 outcome event

但跟 M-1.4 execution 不是子类关系——是**独立同辈 skill**：

| Skill | 入口 | 目标 | scope |
|---|---|---|---|
| **M-1.4 execution** | task_package | task done_criteria 满足 | task scope（含 adjacent 自由度）|
| **M-1.5 review** | TaskRunCompleted / ReviewPlanCreated | 找出意图不满足 + 写 closure_contract | review_plan scope |
| **M-1.6 fix** | finding（含 closure_contract）| **closure_state = closed** | **closure_contract scope（紧）** |

**核心精神**：

> Fix 不是"修代码"——是**把 review 翻译过的可验证合约物理闭合**。

"修代码" 是 M-1.4 已经解决的事。M-1.6 的特殊性是**目标已经被 review skill 翻译成 closure_contract**——M-1.6 不需要决定"什么算修好"，那已经被 M-1.5 写进 contract（criteria + ripple_targets + forbidden_residuals）。

---

## 二、Fix 的灵魂判据（套娃式）

```
表层：fix 完整性（closure_state = closed）
       ↓ 由什么决定？
中层：严格按 closure_contract 执行（每条 criterion 可验 / 每个 ripple sync / 每个 residual 清）
       ↓ 由什么决定？
深层：new session + 不继承原偏见 + 遵守本次共识 + scope bounded
       ↓ 由什么决定？
精神：fix 是 closure contract 的物理化——不是"修了"是"修闭合了"
```

---

## 三、Fix = Event-Driven Closure（v3 整体精神最纯粹的应用）

**关键洞察**（Nature 校准）：

> "报修的话是不是有 issue 机制 会自动触发图的下一个节点来着？"

V3 早就设计过——**M-1.6 不需要自造 cascade 机制**：

| V3 已有 mechanism | 作用 |
|---|---|
| **FindingCreated event 跨 skill 通信** | "issue 机制"——产 event，让其他 skill 接 |
| **F-04d 概念状态变迁链 SAGA** | 概念状态变迁时下游所有引用方机器化感知 |
| **F-04e 字段级 @ 引用图** | 机器可扫描"谁引用了被修复涉及的概念" |
| **F-11 自动触发** | 任务完成后下游依赖满足→确定性机制自动启动下游 session |
| **O-11 变更影响识别工作族** | 5 个成员沿概念图边遍历找隐藏影响 |
| **O-14 涟漪 + 语义解释字段** | event 携带"这次产出涉及的概念变更"语义字段 |

**M-1.6 不"触发"任何下游**——它产 event；cascade 是 v3 event-driven architecture 的 **emergent property**。

跟 v3 整体精神 **existence → projection → circulation → response** 完全同构——

```
M-1.6 产 FixCompleted（含 O-14 语义解释字段）   ← existence
    ↓
fix_lifecycle / concept_evolution_chain 更新    ← projection
    ↓
F-11 自动触发 / F-04d 状态变迁通知下游引用方       ← circulation
    ↓
M-1.5 fix-after / M-1.2 概念引用方 / M-2.x change impact 自动反应  ← response
```

---

## 四、四种 Outcome Event（v2.1.1：authority boundary）（不是"5 个 exit"，是"4 种产出形态"）

M-1.6 不"决定走哪个下游 skill"——决定"产什么 outcome event"。v2.1.1 关键变更：M-1.6 不直接产 concept/obligation/review_plan authority events——改产 FindingCreated(finding_kind)。

| 出口名 | 产什么 event | V3 既有 mechanism 接 |
|---|---|---|
| **fixed** | `FixCompleted` event（含 O-14 语义解释）| F-11 自动触发 M-1.5 fix-after mode 路径 B（bounded verify）|
| **closure_contract 不可执行** | `FindingCreated` event（issue 反向给 M-1.5）| Issue 机制——M-1.5 接 finding 重新设计 closure_contract |
| **需 replan** | `RePlanTriggered` event | F-11 / F-14 自动接，M-1.3 启动 |
| **需改 concept / obligation / review_plan** | `FindingCreated`(finding_kind=concept_issue / obligation_issue / review_plan_issue) | Authority owner (M-1.2 / M-0.6 / M-1.5) 接管，由 authority owner 决定是否 supersede |
| 3. 是否需要 supersede concept | 进 fix | supersede event |
| 4. 是否需要 supersede obligation | 进 fix | obligation supersede event（M-0.6）|
| 5. 是否需要 escalate Nature | 进 fix | EscalationRaised |
| 6. closure_contract 自身可执行（criteria 清楚？ripple 可定位？residual 可验？） | 进 fix | FindingCreated 反向给 M-1.5 |

**注意**：Check 6 跟 closure_contract well-formed self-check（M-1.5 应做的）是双层 verification——M-1.5 应已做但 M-1.6 复查（critique 文档遗漏的层次分离）。

### 防 ritual

feasibility check 是 **bounded checklist 不是 free reasoning**：
- 简单 finding 走 fast-path（防 over-engineering）
- 复杂 finding 按 6 项跑（防 scope creep / 防 free 漫游）
- 每项 check 用具体证据回答（不是抽象判断）

---

## 六、M-1.6 跟 M-1.4 的关系——独立同辈，复用部分 knowledge

不是 specialization（子类）——独立同辈，共享部分底层 knowledge：

| 维度 | M-1.4 execution | M-1.6 fix |
|---|---|---|
| **入口** | task_package | finding + closure_contract |
| **目标** | task done_criteria 满足 | closure_state = closed |
| **scope** | task scope（含 adjacent 决策权）| closure_contract scope（紧）|
| **新代码** | 主要写新代码 | 修既有代码 + 局部新代码 |
| **完成判据** | task done_criteria + envelope self_check | criteria + ripple_targets + forbidden_residuals |
| **失败处理** | self-check fail → fix（M-1.6 接）| fix_insufficient → reopen 新轮 fix |
| **session 关系** | task 内同 session 可继续 | **必须 new session**（Nature 强调，不继承原 M-1.4 偏见）|
| **escalation 语言** | 通用 escalation | **F-09b 产品语言**（给 Nature 看不是工程师）|

**复用 M-1.4 的 knowledge**（v3 整体精神不变）：
- `system-mental-model.md`（v3 整体精神）
- `code-quality-principles.md`
- `git-safety-and-queue.md`（M-3.1 submit wrapper 复用）
- `mismatch-and-issue-handling.md`（M-1.6 也有 mismatch）
- `advisor-collaboration.md`

**M-1.6 独有 knowledge**：
- `fix-mental-model.md`（这份，核心 ★）
- `fix-pitfalls.md`（核心 ★）
- `closure-contract-execution.md`
- `feasibility-check.md`
- `outcome-events-and-cascade.md`
- `escalation-product-language.md`
- `fix-casebook.md`

---

## 七、F-09a 遵守本次共识 + 本次 review plan

> 修复过程中，新引入的代码必须符合本次计划已建立的工程共识（@ 引用了哪些概念、概念的最新状态是什么），并且必须遵守本次 review plan 声明的风险面 + 触发的维度——修复不能让本应被审查的风险面悄悄绕过审查。

**实际操作**：
- 修复中如果发现需要改 concept → 不能默默改，必须产 supersede event（feasibility check #3）
- 修复中如果发现修复涉及新的风险面 → 不能默默修复，必须产新的 finding 或更新 review_plan（feasibility check #2）

**这件事 envelope.self_check 强制**：
- `fix.consensus_respected` blocking_check
- `fix.review_plan_respected` blocking_check

---

## 八、F-09b Escalation 用产品语言（不工程黑话）

> Design constraint: repeated failed iterations terminate in a clear,
> product-language escalation with explicit decision options.

EscalationRaised event 含 nature-facing 字段——

```yaml
EscalationRaised:
  payload:
    nature_facing_summary: string           # 产品语言——用 Nature 能理解的话
    what_was_tried: string                  # 这轮 fix 试了什么
    why_it_did_not_close: string            # 为什么没闭合
    decision_needed_from_nature: string     # 需要 Nature 决定什么
    options:
      - option: string
        tradeoff: string
    engineering_detail_ref: string          # 工程 detail 链接（不在 nature_facing 里）
```

**关键约束**：nature_facing_* 字段**禁止工程黑话**——
- ❌ "race condition in async wrapper at line 42"
- ✅ "我们当前的设计在多人同时操作时有不稳定可能，原计划这次修复，但发现需要先决定：要不要支持并发？"

**这件事不需要独立 fork**（critique 同意）——是 schema 化的表达转换，main session 子操作。

---

## 九、M-1.6 内部 self-verification（双层 verification）

> Critique 漏了这件事——产 FixCompleted 前 M-1.6 自己也要 verify，不能等 M-1.5 fix-after 来抓。

**两层 verification**：
- **M-1.6 internal self-check**（产 FixCompleted 前）：envelope.self_check 含——
  - `fix.closure_criteria_self_verified`（每条 criterion 已按 verification_method 跑通）
  - `fix.ripple_targets_synced`（每个 ripple_target 已 sync）
  - `fix.forbidden_residuals_zero`（grep 残留模式 0 occurrences）
  - `fix.consensus_respected`（F-09a）
  - `fix.review_plan_respected`（F-09a）
- **M-1.5 fix-after independent verify**（FixCompleted 后）：bounded closure verification 路径 B

**类比 M-1.4**：M-1.4 也有 self-check + M-1.5 review 独立 cross-check。同精神。

**为什么需要两层**：
- M-1.6 self-check 是必要条件——没自检的 fix 不应该产 FixCompleted
- M-1.5 fix-after 是独立 verify——避免 M-1.6 self-check 自欺欺人（同样防"reviewer 自己 verify 自己 finding" 的利益冲突）

---

## 十、跟 v3 整体精神的同构

**M-1.6 是 v3 event-driven 设计最纯粹的应用**——所有"修完后怎么办"都不是它的责任，它只产正确的 event。

每个产出从描述升合约（v3 精神 + v2.1.2 Finding closure_contract 例证）—— M-1.6 的 outcome events 也都是合约：

| Outcome event | 合约内容 |
|---|---|
| `FixCompleted` | "我已按 closure_contract 闭合了 finding"——含 self_verification + O-14 语义解释 |
| `FindingCreated` (issue 反向) | "closure_contract 不可执行——这里是为什么"——含具体 ungenerable criterion |
| `RePlanTriggered` | "修复越过 task scope——这里是新 scope 需求" |
| supersede event | "需要打破共识——这里是 novelty"（走 M-0.5 NoveltyCheck）|
| `EscalationRaised` | "需要 Nature 决策——这里是产品语言描述 + options" |

---

## 十一、读完这个文档我应该带走什么

不是"5 个 X 6 个 Y"列表——是一种感觉：

- **Fix = 把 finding 的 closure_contract 物理闭合**
- **M-1.6 不 cascade——产 event 让 v3 mechanism 接管**
- **4 种 outcome event 不是 5 个 exit——是 4 种合约化产出**
- **feasibility check graduated**（简单 finding 走 fast-path，复杂 finding 6 项）
- **F-09a 共识 + review plan 遵守**（envelope.self_check 强制）
- **F-09b escalation 产品语言**（Nature 看不是工程师）
- **双层 verification**（M-1.6 self-check + M-1.5 fix-after）
- **New session 不继承原偏见**（v3 capsule 机制自动保证）

如果遇到这个文档没覆盖的情况——按这个精神推导。

---

## 十二、退出条件

- closure_state = closed → done（FixCompleted 已产）
- closure_contract 不可执行 → done（FindingCreated 已产，issue 反向给 M-1.5）
- 越界 → done（RePlanTriggered 已产）
- 需 supersede → done（supersede event 已产）
- 需 Nature 决策 → done（EscalationRaised 已产）

**M-1.6 不在没产任何 outcome event 的情况下退出**——会留 zombie state。
