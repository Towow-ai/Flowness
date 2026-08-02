# Escalation Product Language — F-09b 用 Nature 能理解的话

> 用途：M-1.6 产 EscalationRaised event 时怎么写 nature_facing_summary 字段。F-09b 核心：用产品语言不工程黑话。
> 归属：M-1.6 fix skill 知识库
> Design constraint: after repeated failed iterations, escalation must explain
> the product consequence and the specific owner decision required.

---

## 为什么必须有这件事

Escalation 的读者是 Nature，不是工程师。Nature 需要决定的是产品 / 意图 / 优先级——不是 "this race condition in line 42 is unrecoverable"。

如果 escalation 用工程黑话写——
- Nature 不能直接判断要不要 / 怎么决策
- 必须先翻译一遍才能用
- 沟通成本 + 决策延迟

**正确姿态**：把 escalation 当作给"懂业务但不懂代码的产品经理"看的——他能直接判断"要这个 / 不要这个 / 折中"。

---

## EscalationRaised Event 结构（双层分离）

```yaml
EscalationRaised:
  payload:
    escalation_id: string
    fix_id: string
    finding_id: string
    
    # === Nature-facing 字段（产品语言）===
    nature_facing_summary: string         # 总结：发生了什么 + 我需要 Nature 决定什么
    what_was_tried: string                # 这轮 fix 试了什么
    why_it_did_not_close: string          # 为什么没闭合
    decision_needed_from_nature: string   # 需要 Nature 决定的具体问题
    options:                              # Nature 的选项
      - option: string                    # 这个选项是什么（产品语言）
        tradeoff: string                  # 这个选项的代价是什么
        product_impact: string            # 用户感受到什么影响
    
    # === 工程 detail（链接出去，不在主体）===
    engineering_detail_ref: string        # 链接到 git diff / event log / artifact path
```

**关键约束**：
- `nature_facing_*` 字段**禁止工程黑话**
- 工程 detail 放 `engineering_detail_ref`——单独链接，不污染 Nature 视野

---

## 怎么写 nature_facing_summary（具体例子）

### 反例 → 正例对照

#### 例 1: 并发问题

❌ **工程黑话**：
> "Race condition detected in async UserService.update_profile method at line 247. The mutex acquired in line 245 doesn't cover the entire critical section, allowing concurrent writes to interleave."

✅ **产品语言**：
> "当多个用户同时编辑同一个 profile 时，可能保存的不是各自最新的修改。原计划修复时发现需要先决定：要不要支持多设备同时编辑？如果支持，需要设计冲突解决；如果不支持，需要加'当前另一个设备正在编辑'提示。"

#### 例 2: schema migration

❌ **工程黑话**：
> "Schema migration ambiguity in concept 'UserProfile'. Adding non-nullable field 'verification_status' requires backfill strategy, but expand-migrate-contract pattern hasn't been agreed upon. SAGA state machine might enter inconsistent state during migration."

✅ **产品语言**：
> "我们要给所有用户加一个'认证状态'字段，但现有用户数据里没有这个字段。需要决定：(1) 默认所有现有用户设为'未认证'然后让他们手动认证；(2) 默认所有现有用户设为'已认证'（信任旧用户）；(3) 给历史用户单独设置一个'豁免'状态。每种选择对老用户体验影响不同。"

#### 例 3: API 兼容

❌ **工程黑话**：
> "Polymorphic dispatch mismatch in PaymentMethod hierarchy. The new field requires modification of base class, but downstream consumers (3 services) have type assertions that would break. Need to determine if breaking change is acceptable."

✅ **产品语言**：
> "我们要给支付方式加一个新功能，但这会影响 3 个其他系统（订单、退款、对账）当前的处理方式。这些系统需要更新才能继续工作。需要决定：(1) 一起更新所有系统（统一发布，工作量大）；(2) 加兼容层，分批更新（短期复杂，长期更稳）；(3) 暂时不加这个功能，先做其他的。"

---

## 5 个写作原则

### 1. 用业务实体名，不用代码实体名

| ❌ 代码 | ✅ 业务 |
|---|---|
| `UserService.update_profile` | "更新用户资料" |
| `async wrapper` | "异步处理" |
| `mutex` | "同时编辑保护" |
| `polymorphic dispatch` | "不同支付方式的处理" |

### 2. 描述用户感受到什么，不是代码做什么

❌ "Function returns wrong value when input X"
✅ "用户在 X 情况下看到的金额不对"

❌ "Database query timeout under load"
✅ "高峰时段用户提交订单可能看到加载圈，最长 30 秒"

### 3. Options 之间的 tradeoff 用具体例子

❌ "Option A: stronger consistency; Option B: better performance"
✅ "选项 A：保证所有用户看到一致的数据，但每次操作慢 100-200ms（用户能感觉到延迟）；选项 B：操作快，但不同用户偶尔看到的数据可能有几秒差异（如评论数不一致）"

### 4. decision_needed_from_nature 是具体问题不是开放问

❌ "What should we do about this?"
✅ "我们应该支持多设备同时编辑吗？"

❌ "Please advise on the approach."
✅ "在我列的 3 个选项里，你倾向哪个？还是有第 4 种我没想到的？"

### 5. what_was_tried 解释为什么这些方法都不行

❌ "Tried approach A. Failed. Tried approach B. Failed."
✅ "我先试了 [A 方法] —— 但发现这样会让 [产品上的某后果]；又试了 [B 方法] —— 但这会需要 Nature 决定 [某个产品判断]。所以来找你。"

---

## 工程 detail 应该放哪

放 `engineering_detail_ref`——一个链接，指向：

- Git commit / branch
- Event log range
- Specific test failure log
- Code review diff
- Concept graph snapshot

Nature 不看这个——但工程团队 / 后续 fix run / audit 时可以追溯。

---

## Escalation 触发时机（什么时候产 EscalationRaised）

Feasibility check #5 触发场景 + 实际运行中：

### 立即 escalate（不进入 fix）
- 修复涉及产品语义判断（不只是工程实现）
- 修复涉及不可逆决策（数据 schema / 部署 / 安全策略）
- 修复明显需要 Nature 业务输入

### 多轮后 escalate
- 第 N 轮 fix 仍 closure_state ≠ closed
- novelty 已耗尽（M-0.5 NoveltyCheck 拒最新轮 supersede）
- advisor consult 也无法决定

**经验阈值**（不是硬约束）：
- 简单 finding 第 2 轮没闭合 + 无 novelty → escalate
- 复杂 finding 第 3 轮没闭合 + 无 novelty → escalate
- 任何轮发现涉及产品判断 → 立即 escalate（不等多轮）

---

## Escalation 不是失败——是诚实

Nature 强调："这一定是通过优雅的化解，甚至是绕过这个问题。但绕过这个问题不是为了绕过他而绕过他——而是说反正我们要解决这个事，这个方式是最高效的。"

Escalation 在 M-1.6 视角是"我修不了，需要 Nature 决策"——但本质是"这件事的最高效化解路径是 Nature 给个产品判断，不是我硬试技术方案"。

跟 abort 不一样：
- Abort = 这件事不能做了
- Escalation = 这件事可以做，但需要 Nature 选方向

---

## EscalationResolved（Nature 决策后）

Nature 看到 escalation 后做决策——产 EscalationResolved event（M-2.3 escalation 流 authority，不是 M-1.6 产）：

```yaml
EscalationResolved:
  payload:
    escalation_id: string
    nature_decision: string           # Nature 选了哪个 option（或第 4 种）
    rationale: string
    next_action: enum [
      retry_fix_with_decision,        # 新一轮 M-1.6 含 Nature decision
      defer,                          # 暂时不做
      retract_finding,                # finding 不修了
      replan                          # 触发 RePlan
    ]
```

M-1.6 看到 EscalationResolved 后——按 next_action 走（可能新一轮 fix，可能其他）。

---

## 自检尺

每次写 nature_facing_summary 前问自己：

1. **"一个不懂代码的产品经理看这段话能懂吗？"** → 否 → 改产品语言
2. **"我用了变量名 / 函数名 / 文件路径 / 行号？"** → 是 → 移到 engineering_detail_ref
3. **"decision_needed_from_nature 是具体问题吗？"** → 开放问 → 改成可选答的具体问题
4. **"options 的 tradeoff 用了用户能感受到的例子吗？"** → 抽象描述 → 改具体例子

---

## 跟普通工程沟通的区别

| 维度 | 工程师之间 | 给 Nature |
|---|---|---|
| 实体名 | code 实体（class / method）| 业务实体（用户 / 订单）|
| 描述对象 | 代码做什么 | 用户感受到什么 |
| Tradeoff | 性能 / 一致性 / 复杂度术语 | 具体场景 + 时间 / 频率 |
| 决策粒度 | 实现细节 | 产品方向 |
| 长度 | 详尽精确 | 短 + 关键决策点 |

---

## 不是 fork，是 main session 子操作

EscalationRaised 的产生不需要独立 fork（critique 同意）—— 是 schema 化的表达转换：

- 简单 case：M-1.6 main session 按 schema 填字段
- 复杂 case（不确定怎么写 nature_facing）：consult advisor（OPUS 决策者）—— advisor 帮判断产品视角

**不要为 escalation 造独立 fork**——增加架构复杂度而无独立判断价值。
