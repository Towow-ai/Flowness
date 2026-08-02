# Risk-Surface-Driven Triggering — 风险面是 review 的入口

> 用途：F-08b 风险面识别——review_plan.dimensions 由什么决定。不是 author 自由选档，是变更内容的客观属性。
> 归属：M-1.5 review skill 知识库
> 基础：F-08b + 概念图查询 + 双保险防偷懒

---

## 风险面是变更的客观属性

不是分类（分类病——author 自报 tag）。**风险面 = 变更内容客观触及的领域**。

例：
- 改 SQL → 客观触及"数据持久化"风险面（不论 author 怎么说）
- 改 auth.py → 客观触及"安全"风险面
- 改 doc.md → 客观触及"文档"风险面（一致性 review 维度）
- 改 README.md typo → 客观属于"仅文档" → 触发维度 0（不 review，或仅最轻 review）

---

## 风险面 → 维度映射

| 风险面 | 触发维度 | 理由 |
|---|---|---|
| **数据持久化** | 内部一致性审计 | schema 改 + migration + ORM 跨多文件 |
| **安全 / 权限 / 认证** | 红队对抗 | 必须主动构造攻击场景 |
| **核心业务逻辑** | 执行路径模拟 | 主路径必须代码模拟跑通 |
| **不可逆动作**（deploy / delete / migrate）| 红队 + 内部一致性 | 出错代价高，双重保险 |
| **跨模块接缝**（API / interface）| 执行路径 + 内部一致性 | 数据流 + caller-callee 一致性 |
| **并发 / 异步** | 执行路径模拟（含竞态构造）| 状态时序 + 资源同步 |
| **用户输入处理** | 红队对抗 | 输入恶意构造 |
| **仅文档 / 配置** | 内部一致性 | 跨文档对照 |
| **仅 stylistic（rename / formatting）** | 0 维度 | 不 review（continuous improvement 接受）|

**触发的维度 = 变更触及的所有风险面对应维度的并集**。

---

## 风险面识别整体作为概念图查询

风险面**不是 review 系统自造的清单**——是工程共识概念图（M-1.2 产）的一部分。

```
M-1.2 工程共识概念图：
  - 概念节点（业务实体 / API / state machine / ...）
  - 风险面节点（数据持久化 / 安全 / 核心业务 / ...）
  - 维度节点（执行路径 / 内部一致性 / 红队 / ...）
  - 边：概念 → 风险面（这个概念属于哪些风险面）
  - 边：风险面 → 维度（这个风险面对应哪些维度）
```

**M-1.5 design-time review_plan_creator 怎么用**：
1. 读 frozen 工程共识概念图
2. 查 task 涉及的概念节点
3. 查这些概念的风险面 attach
4. 查风险面 → 维度映射
5. 取并集 → review_plan.dimensions

**这就是 Nature 说的"它带着它的上游，它是应该最清楚地知道这个系统该怎么反驳它"**——风险面 / 维度的 metadata 在设计阶段（M-1.2）就建立好，review_plan 只是查询和组合。

---

## 双保险防偷懒（自动 detection + author 自报）

**问题**：author 自报风险面时可能漏报——"我没触及安全风险面"但实际触及（v3 H-09 形态变化）。

**双保险**：

### 保险 1：自动 detection（基于 diff 模式）

物理规则——file path patterns + diff 模式自动识别——

| 检测规则 | 触发风险面 |
|---|---|
| 改 `*.sql` / `migrations/*` | 数据持久化 |
| 改 `auth/*` / `*.security.*` | 安全 |
| 改 `core/business_logic/*` | 核心业务 |
| 改 `api/*` / interface 定义 | 跨模块接缝 |
| 改 `async/*` / `*concurrent*` | 并发 |
| diff 含 `DROP / DELETE / TRUNCATE` | 不可逆动作 |
| diff 含 SQL string concatenation pattern | 安全（SQL injection risk）|
| 改 `Dockerfile / k8s/*` | 部署 / 不可逆 |

这些规则随 F-08i Repo 级 baseline 涌现演化——从历次 review_plan 沉淀出来。

### 保险 2：Author 自报

设计 review_plan 时，design-time fork 自动 surface 候选风险面给 author——

"基于自动检测，这次变更可能触及以下风险面：[X, Y]。你认为是否还触及其他风险面？"

Author 回答（自报）。

### 取并集

**最终触发的维度 = (自动 detection 触发的风险面 ∪ author 自报的风险面) 对应维度的并集**。

防偷懒——author 漏报 X，但自动 detection 触发了 X → 仍然跑 X 对应维度。
也防自动 detection 漏报——author 自报 Y，但 detection 没识别出 Y → 仍然跑 Y。

---

## F-08f 高风险动作 schema-level 强制

某些动作类型有强制 review 维度，**author 不能在 review_plan 里 opt out**——

| 强制类型 | 强制维度 |
|---|---|
| 数据库 migration | 内部一致性审计 + 红队对抗 |
| 不可逆动作（deploy / delete / drop） | 红队 + V-03 hook 物理拦截 |
| 跨模块接缝 | 执行路径 + 内部一致性 |
| Prod deploy | 红队 + 执行路径 + V-03 ASK Nature |
| 安全敏感 | 红队对抗 |

物理强制点 = 风险面命中后 hook 拦截放行（V-02 / V-03 的延伸）。

这是 F-08b 在最高风险面上的强制不可绕过版——author 不能写 "我不想 review red-team" 来跳过。

---

## 风险面 enumeration 不全是已知薄弱点（F-08j）

**已知**：风险面清单本身有盲区——unknown unknown 的另一种形态。

**对治**（部分）：
- F-08e 历史失败模式喂入——把历史 L3 盲区变 L1 known unknowns
- F-08g Meta-review 审 review_plan 是否完整
- O-06 cross-run consolidation 发现新 failure pattern → 新风险面 candidate

**根本性的**：风险面清单是涌现的（F-08i），不是预设——会随时间演化补全。M-1.5 不假装风险面清单已经完美。

---

## 自动 detection 的 lifecycle

每条自动 detection rule 走 Detection Rule Lifecycle（F lifecycle）—— 不是静态规则表，是可演化的 detector：

```
observed failure (multiple findings 同根因)
  → DetectionRuleProposed (M-1.5 / M-2.x 任一可触发)
  → shadow mode（运行但不触发 review_plan 更新，只记录会命中什么）
  → active warning（warn 但不强制改 review_plan）
  → enforced（命中即触发对应维度）
  → retired / revised（误报多 / 设计变化）
```

**M-1.5 only owns DetectionRuleProposed**——rule 升级（shadow → warning → enforced）是 cross-run consolidation 工作，归 M-2.x maintenance。

---

## 失败模式

| 失败模式 | 对治 |
|---|---|
| 风险面识别遗漏（dimension 漏跑）| Meta-review 审 review_plan.dimensions 是否覆盖完整 |
| 自动 detection 误报（触发不该有的维度）| Rule 走 lifecycle——shadow mode 先收集 FP rate，超阈值 retire / revise |
| Author 自报偷懒（明知触及但说没触及）| 自动 detection 兜底——双保险取并集 |
| 维度全跑变 review 通胀 | 维度 = 风险面映射的并集（不是默认全跑）——精确 scope |
| 风险面定义不清（仅 文档 vs 含 spec 的文档）| 工程共识概念图明确风险面定义；ambiguous case → meta-review 审 |
