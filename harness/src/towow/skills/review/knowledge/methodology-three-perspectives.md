# Methodology Three Perspectives — Differentiated Context-Injection Recipes

> 用途：F-08c 三个方法论维度是 review 的核心工具。理解它们不只是"三种 review 方式"，是**三种不同的 context-injection 配方**——每个给 reviewer fork 不同的 lens。
> 归属：M-1.5 review skill 知识库
> 基础：F-08c + Nature 的 context-driven 洞察 + Anthropic Code Review 多 agent 实证

---

## 为什么按"怎么看"分而不是按"看什么"分

旧 v3 错（PLAN-090 30+ density）：按"看什么"角色分——
- silent-failure（找隐藏失败）
- redteam（找攻击面）
- contract（找契约违反）
- business（找业务问题）

**这 4 视角全是同方法论的同方向**——都是"如何对" lens 的不同侧面。同 context 堆叠 → 同根因被多视角分别报 → finding 通胀。

**v3.1 转变**：按"怎么看"方法论正交分——
- 执行路径模拟（runtime simulation lens）
- 内部一致性审计（cross-doc consistency lens）
- 红队对抗（adversarial scenario lens）

**三种方法论根本不同 context-injection 配方**——看到的东西不重叠。

---

## 维度 1：执行路径模拟（Runtime Simulation Context）

### Context Injection 配方

- **Prompt lens**：你不是写代码的，是"代码模拟执行器"
- **Mental model**：不读自然语言描述，**只看代码段**——一行一行模拟执行，追踪每个变量的真实值
- **不被允许的**：相信注释 / 相信文档说"应该这样" / 相信测试 case 覆盖了

### 探查方向

- 数据流断裂——签名匹配但数据语义不通（例：函数返回 `Optional<User>` 但 caller 当 `User` 处理）
- 状态机非法转移——代码执行后 state 转到一个非法 state
- 资源生命周期错——资源 acquire 但某条 path 没 release
- 异常路径——主路径通过但 exception 路径有 gap
- 并发安全——多线程下变量真实值非确定

### 适用风险面

- 核心业务逻辑
- 跨模块接缝
- 并发 / 异步代码
- 复杂状态机

### Falsification framing

**"尝试找一条执行路径让 patch 崩"**——不是证明 patch 对，是构造 input 让 patch fail。

例：
- 输入是空集合 → 看代码会不会 NPE
- 输入是非常大集合 → 看会不会 OOM
- 中间状态有外部 race → 看会不会读到不一致 state

### 输出 finding 形态

```yaml
voi_rationale: "构造 input X → 执行路径在第 Y 行变量 Z 取到 nil → 任务 spec 要求处理空集合"
target.location: "src/foo.py:42-58"
suggested_fix_layer:
  primary: code
  rationale: "增加 nil guard"
falsification_evidence:
  attempt: "模拟 input=[] 执行 foo() → bar() 调用 → bar() 内 zoo() 返回 nil"
  result: confirmed
review_dimension: method-execution-path
```

---

## 维度 2:  内部一致性审计（Cross-Doc Consistency Context）

### Context Injection 配方

- **Prompt lens**：你不是看单一文件的，是"跨文档对比员"——看相同事实在不同位置是否一致
- **Mental model**：交叉对比 doc / code / config 中所有重复描述
- **不被允许的**：只看单文件 / 假设其他文件跟当前一致

### 探查方向

- 此处改了彼处忘了——签名改了 caller 没改 / schema 改了 migration 没改 / API 改了 doc 没改
- 依赖图 vs Phase 表 vs WP 详细规格不一致
- 文档说 X 但代码做 Y
- 配置文件之间矛盾
- Concept 定义跟 implementation 字段不一致

### 适用风险面

- 文档密集型变更
- 跨多文件的 refactor
- API 变更
- Schema migration
- Concept 演化

### Falsification framing

**"尝试找文档间矛盾让 patch 描述失真"**——不是相信 author 的描述，是找出"author 描述 X 但代码做 Y"的地方。

例：
- spec 说"`foo()` 返回 User"——但 implementation 返回 `Optional<User>`
- migration script 改了 column 类型——但 model 类没改
- doc 说"所有 path 走 auth"——但代码里 admin path 跳过 auth

### 输出 finding 形态

```yaml
voi_rationale: "API doc 写 'returns User' 但代码返回 Optional<User>——caller 拿到 doc 后会写错"
target:
  artifact: "docs/api/user.md"
  location: "section getUser() return type"
  (cross-references: "src/user_service.py:42 actual return type")
suggested_fix_layer:
  primary: spec
  rationale: "doc 跟 code 不一致，需要决定哪个对——按 author intent 调整 doc 或 code"
falsification_evidence:
  attempt: "对比 doc / code / test 三处对 getUser() 返回值的描述"
  result: confirmed
review_dimension: method-consistency
```

---

## 维度 3：红队对抗（Adversarial Scenario Context）

### Context Injection 配方

- **Prompt lens**：你不是 user，是 attacker——意图找到攻击面 / 滥用面
- **Mental model**：尝试构造**恶意场景** / 边界 / 异常 / 竞态让计划崩
- **不被允许的**：假设输入正常 / 假设用户合作

### 探查方向

- 输入恶意构造——SQL injection / XSS / command injection / path traversal
- 边界 / 异常——超长输入 / 负数 / 非法 unicode / 时区边界 / 跨午夜
- 竞态 / TOCTOU——check 后 use 之间被人修改
- 资源耗尽——发请求耗尽 quota / 触发 DoS
- 权限提升——构造 input 绕过权限检查
- 数据泄露——边界条件下查到不该看的数据

### 适用风险面

- 安全敏感
- 权限 / 认证
- 不可逆动作
- 用户输入处理
- 跨租户 / 多租户

### Falsification framing

**"尝试构造攻击场景让 patch 失败"**——不是相信 spec 已经处理了所有情况，是主动构造 spec 没想到的恶意场景。

例：
- input contains `'; DROP TABLE users;--` → SQL escape 处理对吗？
- user_id 是另一租户的 → 查询会泄露吗？
- 同时两个请求都过 check_balance → 会不会双花？

### 输出 finding 形态

```yaml
voi_rationale: "用户输入字段没 escape；构造 input `' OR '1'='1` 可绕过 auth"
target.location: "src/auth/login.py:23 SQL query construction"
suggested_fix_layer:
  primary: code
  rationale: "改成参数化 query"
  alternative_layers:
    - layer: hook
      condition: "如果不能改 query，加 WAF hook 拦截 known SQL injection patterns"
falsification_evidence:
  attempt: "尝试构造 username=`' OR '1'='1'--` → SQL 变成 `SELECT * FROM users WHERE name='' OR '1'='1'--' AND password=...`"
  result: confirmed
review_dimension: method-red-team
```

---

## 怎么选哪些维度跑（review_plan 决定）

不是"必跑三个"——是按 review_plan.dimensions 跑。

**风险面 → 维度的映射**（F-08b）：

| 风险面 | 触发维度 |
|---|---|
| 数据持久化 | 内部一致性审计 |
| 安全 | 红队对抗 |
| 核心业务 | 执行路径模拟 |
| 不可逆动作 | 红队 + 内部一致性 |
| 跨模块接缝 | 执行路径 + 内部一致性 |
| 仅文档 / 配置 | 内部一致性 |
| 仅 stylistic 改动 | 0 维度（不 review） |

最终触发的维度 = **变更触及的所有风险面对应维度的并集**。

review_plan 在设计阶段就规定了——reviewer 按 plan 跑，不漫游。

---

## 三个 fork 都装 falsification 精神

每个 fork 的 prompt 都含 falsification framing——

```
你的根本工作不是"证明 patch 对"，是"尝试 disprove patch"——找出 patch 哪里可能 fail。

每个 finding 都必须 attempt to disprove 过——记录 falsification_evidence:
- attempt: 你尝试 disprove 的过程
- result: confirmed (找到反例) / partially_confirmed / unable_to_disprove
```

**这跟 Anthropic Code Review verify-step 同精神**——但 Anthropic 把 falsification 只放在 verify-step。M-1.5 把这个精神贯穿全程：
- 方法论 fork：尝试 disprove patch
- Verify-step fork：尝试 disprove finding（独立性是核心）
- Meta-review fork：尝试 disprove review_plan

每一层都是 falsification——降低假阳性，提高真信号。

---

## 失败模式

| 失败模式 | 对治 |
|---|---|
| 三个 fork 重复发现同 finding（multi-perspective 通胀复现）| Aggregation fork 去重——同 risk_surface + 同 target.location + 同根因 → merge |
| 方法论 fork 不带 falsification 精神（变成漫游赞美 patch）| Fork prompt 强制每个 finding 含 falsification_evidence |
| 选错维度跑（漏关键 risk surface）| review_plan VoI 校准——meta-review 审 dimensions 是否完整 |
| 维度 boundary 不清——执行路径 fork 也开始 cross-doc 看 | Fork SKILL.md frontmatter 明确——三个方法论各跑各的，不窜 |
