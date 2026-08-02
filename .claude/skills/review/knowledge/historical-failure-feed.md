# Historical Failure Feed — L3 Unknown Unknowns → L1 Known Unknowns

> 用途：F-08e 历史失败模式喂入——让 reviewer 不只看当前 patch，还带着"过去类似变更踩过的坑"的 lens。
> 归属：M-1.5 review skill 知识库
> 基础：F-08e + 三层盲区分析（L1/L2/L3）+ Nature feedback memory + ADR / issue 历史

---

## 三层盲区（L1 / L2 / L3）

| 层 | 含义 | 例 |
|---|---|---|
| **L1 known knowns** | author 知道并已处理 | "我知道要 escape SQL" |
| **L2 known unknowns** | author 知道有风险但没显式处理 | "我没想 race condition" |
| **L3 unknown unknowns** | author 完全不知道 | "我不知道这种历史 fail 模式存在" |

**对 L3 的对治**：把 L3 通过历史经验变成 L1（known unknowns），让 author 显式 address 每个坑——"这次会被红队覆盖" 或 "这次不适用因为 Y"。

---

## 历史失败模式来源

> v3.3 关于 lesson 来源的明确：F-08e 喂入的"过去类似变更踩过的坑清单"来源是 **Nature 手动维护**的 feedback memory + ADR + issue（不是系统自动 self-learning 产物）

| 来源 | 内容 |
|---|---|
| **Feedback memory** | Nature 沉淀的方法论 / 失败模式（如 review_methodology_orthogonal / review_driven_complexity_inflation）|
| **ADR**（Architecture Decision Record） | 历史架构决策 + 反例 + 否决项 |
| **Issue 历史** | 已 closed 的 issue 记录的真实 bug 模式 |
| **FAILURE-CATALOG** | 980 条失败模式清单（按主题分类）|
| **Cross-run consolidation**（O-06 产出）| 多次 audit 抓到的同类问题 → 提炼成 failure pattern |

**系统的角色 = surface**（按风险面分组归类）。**Nature 的角色 = 提炼**（决定哪些是真 pattern + 是不是要 capture）。

---

## Surface 机制

design-time review_plan_creator 在产 review_plan 时——

1. 查 task 触及的风险面（来自 F-08b）
2. 查这些风险面 attach 的 historical_failure_modes（按风险面分组归类的索引）
3. Surface 给 review_plan：

```yaml
review_plan:
  ...
  historical_failures_feed:
    risk_surface: data_persistence
    relevant_historical_failures:
      - failure_id: FAIL-001
        title: "schema 改了 migration 没改"
        observed_in: [PLAN-094, PLAN-101]
        pattern: "改 SQL 文件后未同步生成 migration script"
        author_must_address:
          - "本次会被内部一致性 review 维度覆盖"
          - "本次不适用因为 [Y]"
      - failure_id: FAIL-002
        ...
```

Author 在 review_plan 评审时必须显式回答每个 historical failure：
- "本次 review_plan 已 cover——X dimension 会抓到"
- 或 "本次不适用因为 Y"

**不回答 = review_plan 不通过 meta-review**。

---

## 按风险面分组归类（机械的）

**分组是机械的**——按 historical_failure.risk_surface 字段分组归类。

```
projection: historical_failure_by_risk_surface
   risk_surface_X:
     - FAIL-001
     - FAIL-002
     ...
   risk_surface_Y:
     - FAIL-007
     ...
```

这是 UI projection（O-12 E 分类）——给 Nature / review_plan_creator 看的视图，从 event log 派生。

---

## 提炼是 Nature 做的（不是系统自动）

> v3.3 关键：系统**不做**"转化为 skill Red Flags 或方法论改进"——这部分是 Nature 看 surface 后人工做的

转化路径（人工）：

```
Cross-run consolidation 发现 pattern
  → 系统 surface 给 Nature（按 pattern 分组、按频率排序、附事件链接）
  → Nature 看 → 决定是否真的是个 pattern
  → Nature 起 feedback memory / 改 skill / 加 ADR / 更新 v3 文档
```

**为什么不自动**：

- 自动提炼会产假 pattern（多个偶然相关的 finding 被算成 pattern）
- 提炼需要 Nature 的判断（哪些是真 pattern / 哪些是噪声 / 哪些值得做 skill / 哪些只是个例）
- 系统提供材料，不替 Nature 写

---

## Detection Rule Lifecycle 跟历史 failure 的关系

历史 failure 可以演化成 Detection Rule（F lifecycle）——

```
observed failure (multiple findings 同根因 → cross-run consolidation 识别为 pattern)
  → DetectionRuleProposed (M-1.5 / M-2.x 任一可触发)
  → shadow mode（运行但只记录 + 不强制 review_plan 改变）
  → active warning（warn 但不强制）
  → enforced hook（命中即触发对应 dimension review）
  → retired / revised（误报多 / 设计变化）
```

**M-1.5 owns DetectionRuleProposed** event（rule candidate 阶段）——但 rule 升级（shadow → enforced）属于 cross-run consolidation 工作，归 **M-2.x maintenance**。

---

## 已知薄弱点（F-08j 诚实自审）

- **风险面 enumeration 不全**——清单本身有盲区（unknown unknown 的另一种形态）
- **自动 detection 误报**——把不该触发的当触发 → 又过度审查
- **自动 detection 漏报**——关键风险面没识别
- **author 自报"我没触及 X 风险面"但实际触及**——H-09 形态变化
- **verify-step over-filter**——把真 bug 当假阳性过滤掉
- **meta-review subagent 自身有偏见**

**这些不是设计 bug，是设计的已知边界**。事后 audit 时这些薄弱点暴露出来 → 喂回 baseline 风险面清单和历史模式库的演化。

---

## 失败模式

| 失败模式 | 对治 |
|---|---|
| Historical feed 太长（surface 100 个 failure，author 看不完）| 按风险面过滤——只 surface task 触及的风险面对应的 failure |
| Historical feed 跟当前 task 无关（噪声）| 按 risk_surface 字段精确匹配，不模糊匹配 |
| Author 偷懒回答 "本次不适用"（不写理由）| Meta-review 审 review_plan 的 historical_failures_feed 是否每条都有具体理由 |
| 历史 failure 过期没退役（过去的设计早已变化） | Detection rule lifecycle 的 retired 状态——过期 rule 不再触发 |
| 系统自动提炼假 pattern | 不自动提炼——Nature 手动判断（v3.3 关键约束）|
