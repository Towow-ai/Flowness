# Finding as First-Class Object — Typed Semantic Claim

> 用途：Finding 不是自由文本，是结构化的 typed semantic claim——理解它是 review skill 的产出物形态。
> 归属：M-1.5 review skill 知识库
> 基础：M-0.1 §2.3.8 FindingFields + §3.8 Finding 4 events 已注册 + O-12 设计

---

## Finding 不是自由文本

旧 v3 的 finding 是 reviewer 写的一段自然语言段落——"我觉得这里有点问题，可能 X、可能 Y"。这导致：
- 不可定位（author 不知道改哪）
- 不可状态机化（不知道什么是"resolved"）
- 不可 projection（按风险面归类靠人工）
- 不可 aggregate（多 fork 同根因 finding 难合并）

**O-12 转变**：Finding 是 **typed semantic claim**——结构化的、有状态机的、可机械索引的一等对象。

**v2.1.2 元层升级（M-1.5b Patch g）**：Finding 不只是 typed claim，是 **可执行的修复合约（closure_contract）**——跟 v3 整体精神同构：

| v3 产出 | 描述层（不够）| 合约层（v3 真精神）|
|---|---|---|
| Brief | 任务描述 | brief.completion_condition（可执行完成条件）|
| Task package | 任务描述 | 自包含可执行（零上下文可跑）|
| Envelope | patches 包 | claims + active_obligations + self_check 完整合约 |
| Obligation | 约束描述 | lifecycle state machine + checker（可机械执行）|
| Concept supersede | 概念演化 | novelty-gated（合约式收敛判据）|
| **Finding (v2.1.2)** | **问题描述 + voi_rationale + target** | **+ closure_contract（"修到什么程度算完"）** |

Finding 应该回答："为什么这值得修 / 修哪里 / 改哪一层 / **怎么证明修好了 / 哪些相关位置必须同步 / 什么残留模式说明没修干净 / 第二轮只检查什么**"。

最后四个问题就是 closure_contract 的灵魂。

---

## Finding Schema（M-0.1 §2.3.8 + M-1.5 扩展）

### 基础字段（M-0.1 已有）

```yaml
finding_id: string                          # 全局唯一
severity: enum [critical | major | minor | observation]
risk_surface: string                        # 风险面标签
lifecycle_state: enum [created | verified | disputed | resolved]
related_patch_event_id: string?             # 关联的 patch
related_obligation_ids: [string]?
detection_method: enum [manual_review | automated_rule | ad_hoc]
```

### M-1.5 扩展字段（review-specific）

```yaml
# Review 相关（M-1.5 详设新增 finding payload 内）
voi_rationale: string                       # 必填——这个 finding 为什么过 VoI 测试
                                            # 例："如果 author 加 batch_size 上限，可以避免 prod 环境 OOM——这跟任务的 prod 部署目标直接相关"

target:                                     # 必填——可定位
  artifact: string                          # 哪个文件 / entity
  location: string                          # 哪一行 / 哪个函数 / 哪段代码
  
suggested_fix_layer:                        # 必填——告诉 author 改哪一层
  primary: enum [code | sql | hook | config | concept | obligation | spec]
  rationale: string                         # 为什么是这一层
  alternative_layers:                       # 可选——其他可能改法
    - layer: enum
      condition: string                     # 什么条件下用这一层
      
review_dimension: enum [                    # 来自哪个方法论 fork（F-08c）
  method-execution-path,
  method-consistency,
  method-red-team,
  verify-step,
  meta-review
]

is_preexisting: bool                        # 关键字段——参考 Anthropic purple severity
                                            # true = adjacent code latent bug，不算 author scope creep
                                            # false = PR 引入的问题

falsification_evidence:                     # 这个 finding 怎么 disprove 的（如果有）
  attempt: string                           # 尝试 disprove 的过程
  result: enum [confirmed | unable_to_disprove | partially_confirmed]
  
review_plan_dimension_ref: string?          # review_plan 里哪个 dimension 触发的这个 finding
voi_criterion_ref: string?                  # review_plan 里哪条 voi_criterion 过的

# === Closure Contract (v2.1.2 Patch g)===
# Finding 升级合约层——回答"修到什么程度算闭合"
closure_contract:
  closure_criteria:                         # 必填——"什么样才算 finding 修完"
    - condition: string                     # 可验证条件
      verification_method: enum [grep | schema_check | projection_check | manual_reasoning | test | replay | git_diff]
      expected_result: string               # "0 occurrences" / "field exists" / etc
      # ── RUN-037 P6: grep/test criterion 我（reviewer 签合约时）必须绑结构化复算 spec ──
      # 因为 closure 门用合约自带 pattern 自己 grep（不信被审者给的 pattern），pattern 由我定死。
      verification_pattern: string          # grep 必填: 门用这个 pattern grep（被审者改不了）
      expected_occurrences: int             # grep 必填: 门 grep 出的真 count 须 == 这个值（通常 0）
      search_scope: string                  # grep 可选: 相对 repo 的搜索范围; 缺省全域
      #   ⚠ search_scope 必须是路径形态——占位符 (<...>)/命令文本/绝对路径/.. 段会被
      #   发布门 + 复算门双重拒（T-SL-A1 反模式: 把 'git diff --name-only <本任务commit>^..'
      #   命令模板塞进 search_scope → 复算永久 fail-closed）。
      test_selector: string                 # test 必填: pytest nodeid（门自己跑, exit 0 才 pass）
      # ⚠ verification_method=grep 却不绑 verification_pattern（或 =test 不绑 test_selector）=
      #   合约自相矛盾 → 门 fail-closed 拒（不是降级）。本质要靠人判断的 → 用 manual_reasoning
      #   （门标 not_recomputable 降级，禁走 confirmed_and_fixed 强门，该走 confirmed_and_accepted）。
      # ⚠ git_diff（'文件 X 在本任务 commit 零改动' 类 INV）当前只接线在 M-1.4 execution
      #   done_criteria 复算链；reviewer 闭合合约里暂别用——fix complete 门无 commit 上下文,
      #   会 fail-closed 明拒（finding-machine-check-encoding-gap-1780563112）。
  
  ripple_targets:                           # 必填（除非无 ripple）——同步更新位置
    - artifact: string                      # 文件名
      location_hint: string                 # section / 段落（不要求精确行——修复后行号变）
      reason: string                        # 为什么这里要同步
  
  forbidden_residuals:                      # "什么残留说明没修干净"
    - pattern: string                       # 旧术语 / 旧 enum / 旧 schema
      rationale: string                     # 这个残留为什么致命
      check_method: enum [grep | schema_check | manual_reasoning]
      search_scope: string                  # grep 强烈建议: 门 grep 的范围 (相对 repo_dir 的路径)
      # ⚠ check_method=grep 不绑 search_scope → CLI finding-create 会**警告** (不硬拒发布)：
      #   无 scope 时门复算退化 repo_dir 全域 grep, 扫描面大/复算偏慢。显式圈定残留该在哪清零
      #   (通常 = 主修 artifact 所在目录); 真要全域显式写 "."。scope 若**写了但非路径形态**
      #   (占位符/命令文本/绝对路径/越界) = 发布即拒 (同 criterion 的 scope 形态门)。
      #   (f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ①)
```

### 为什么这些字段是 review 灵魂

| 字段 | 解决的失败模式 |
|---|---|
| `voi_rationale` | 防"制造假问题"——必须能解释这个 finding 为什么过 VoI |
| `target.location` | 防"finding 不可定位" |
| `suggested_fix_layer` | 防"author 不知道改哪一层" |
| `review_dimension` | 防 multi-perspective 通胀（按 dimension 去重 + aggregate）|
| `is_preexisting` | 区分 PR 引入 vs latent，不算 author scope creep |
| `falsification_evidence` | 反映 falsification 精神——finding 不是凭想象，是尝试 disprove patch 找到的 |
| `review_plan_dimension_ref / voi_criterion_ref` | 让 finding 可追溯到 review_plan——审计时能复盘 |
| **`closure_contract.closure_criteria`** | **防"修了不知道算不算完"——可验证终止条件** |
| **`closure_contract.ripple_targets`** | **防 Cleanup Ripple Incomplete（review-pitfalls #23）——主修改点改了引用位置同步** |
| **`closure_contract.forbidden_residuals`** | **防"旧表述漏改"——grep 残留模式应 0 occurrences** |

---

## Finding 状态机（v2.1.2 升级——含 closure cycle）

```
[fork 提交]
    ↓
created (FindingCreated)
    ↓
[verify-step fork 验证——author_time 路径 A]
    ↓
verified (FindingVerified) — true positive
    or
rejected_false_positive — 丢弃，不进入下游
    or
unverified_inconclusive — 保留给 author 自己判断
    ↓
[author resolution]
    ↓
disputed (FindingDisputed) — author 反驳，必须带 dispute_reason + novelty
    ↓ novelty-gated dispute loop
    ↓ M-0.5 NoveltyCheck 强制 novelty
    ↓
accepted (隐式状态：author 同意 → 触发 M-1.6 fix)
    ↓ FixCompleted event
    ↓
[★ closure verification (fix_after mode 路径 B, bounded)]
    ↓ 严格按 finding.closure_contract 验证：
    ↓   1. closure_criteria check (每条按 verification_method)
    ↓   2. ripple scan (bounded scope = ripple_targets)
    ↓   3. forbidden residual check (grep)
    ↓   4. scope 外发现 default new_unrelated_finding_logged 不阻塞
    ↓
[closure_state]
    ├── closed → resolved(confirmed_and_fixed) 【终态】
    ├── fix_insufficient → reopen FindingCreated (新轮 fix)
    ├── ripple_incomplete → reopen FindingCreated (scope bounded 到 ripple_targets)
    └── new_unrelated_finding_logged → resolved + 新 finding_id 进 backlog 【不阻塞】
    
resolved (FindingResolved)
    resolution: enum [
      confirmed_and_fixed,        # closure_state = closed
      confirmed_and_accepted,     # author 接受但不修
      retracted,                  # reviewer 撤回（dispute 后认同）
      escalated,                  # multi-round dispute 不达成共识
      unresolved_risk             # 留作 known risk
    ]
    closure_verification:         # v2.1.2 新增
      closure_state: enum [closed | fix_insufficient | ripple_incomplete | new_unrelated_finding_logged]
      criteria_results: [...]
      ripple_results: [...]
      residual_check_results: [...]
      unrelated_findings_logged: [string]?
```

**两层收敛机制（v2.1.2）**：
- **Dispute 层 novelty-gated 收敛**（M-0.5 NoveltyCheck）—— 防对话振荡
- **Fix 层 closure_contract 收敛**（v2.1.2 新增）—— 防 cleanup ripple 漏修

两者一起 = M-1.5 review cycle 的完整工程化终止条件。

**状态转移触发方**：

| Transition | 触发方 | Event |
|---|---|---|
| created → verified | verify-step fork | FindingVerified |
| created → rejected_false_positive | verify-step fork | FindingVerified (with false_positive_eliminated=true) |
| verified → disputed | author（M-1.4）| FindingDisputed |
| verified → accepted | author 不 dispute → 隐式 | （不产 event；走 fix）|
| disputed → verified | reviewer 维持 + 新 novelty | 新 FindingVerified （novelty-gated）|
| * → resolved | 最终 author 决策 | FindingResolved |

---

## 谁能产 Finding（actor_type）

- **reviewer fork**（agent_fork，actor_id = `m15.review.{fork_name}`）—— 主要来源
- **automated hook**（按 F-08i 涌现的 detection rule 触发）—— rule lifecycle 升级后才能产
- **physical_hook**（V-01 / V-02 / V-03 物理拦截触发）—— 罕见
- **nature_learning**（Nature 手动 capture）—— 罕见

source 字段映射：

```yaml
source:
  type: enum [model_review | physical_hook | historical_pattern | nature_learning]
  agent_id: string?                         # 如果是 model_review
  hook_id: string?                          # 如果是 physical_hook
  rule_id: string?                          # 如果是 historical_pattern（从 detection rule 触发）
```

---

## Aggregation 与 Deduplication

多个方法论 fork 可能在同根因下产同 finding（如：执行路径 fork 发现 race；红队 fork 同根因构造攻击）——需要 aggregation。

**Aggregation 规则**：

1. **Severity 取最高**——red > yellow > purple > minor > observation
2. **Risk_surface 取并集**——多 fork 标的 risk_surface 都保留
3. **Review_dimension 列表**——记录被几个 dimension 发现过
4. **Voi_rationale 合并**——多 fork 的 rationale 合并成更强的论证
5. **Target 取交集**——多 fork 都指向同一 location → 高信心；不同 location → 不去重

**Aggregation fork**（subagent 调用，不是 M-1.5 主 session 自己做）—— 防止 main session 失焦。

---

## 为什么 Finding 是 event log 一等对象

- Finding 不是被独立系统维护的——是 event log 推导的
- Finding 的所有 view（按风险面归类的索引 / Review inbox / 历史 finding 库）都是 projection
- Finding 的修正必须通过新 event supersede 旧 event，不能原地改
- O-06 cross-run consolidation 把多个 finding 提炼成 failure pattern → 喂给 F-08e 历史失败模式 feed

这跟 v3 整体精神一致——"一切都是 event，一切 view 都是 projection"。

---

## Finding 的下游消费

| 下游 | 消费什么 |
|---|---|
| **M-1.4 execution（author）** | 看 finding → 决定 accept / dispute |
| **M-1.6 fix skill** | 看 FindingResolved(confirmed_and_fixed) → 产 FixProposed |
| **M-2.x maintenance** | 看 finding patterns → 喂入 detection rule lifecycle（升级 candidate → shadow → ...）|
| **O-06 cross-run consolidation** | 多 finding → failure pattern → 喂入 F-08e historical feed |
| **UI projection** | review inbox 按 finding lifecycle_state 过滤展示给 Nature |

---

## Finding 不做什么

- **不修代码** —— V-02 物理隔离
- **不自动 supersede 旧 concept / obligation** —— 这是 M-1.2 / M-0.6 的 authority
- **不直接产 ObligationViolated event** —— M-0.5 commit gate authority
- **不主动升级 detection rule lifecycle** —— M-2.x maintenance 处理
- **不批准 PR / commit** —— author 决策 + commit gate 物理把关
