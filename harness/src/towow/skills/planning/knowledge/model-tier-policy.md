# Model Tier Assignment Policy — opus / sonnet 分配的完整决策规则

> 用途：planner fork 为每个 task 决定 model_tier 的判断依据。
> 归属：M-1.3 planner skill 知识库

---

## 核心原则

**Model tier 不是简单"复杂 → opus / 简单 → sonnet"。它反映的是"做错了的代价 vs 做对的难度"——代价高或难度高，用 opus。** 

opus 是高代价（更贵 + 更慢）。滥用 opus 浪费资源。但低估了复杂度用 sonnet 做错的代价（写错代码、违反 obligation、误判语义）比 opus 成本高得多。

## 因素分级

每个因素打"分量"——0/1/2/3。组合分数决定 tier。

### Opus 因素

| 因素 | 分量 | 描述 |
|---|---|---|
| **red-line obligation 涉及** | 3 | task 必须遵守 red-line obligation（违反 = 系统正确性问题）|
| **state_machine / invariant 变更** | 3 | 改 state_machine 或 invariant 的语义判断 |
| **关键路径上 + 复杂度中等以上** | 3 | 关键路径影响整体 makespan，错了代价大 |
| **high fan-out concept（被 ≥5 个消费方引用）** | 2 | 改这种 concept 影响广 |
| **write_set > 3 个 entity** | 2 | 影响范围大 |
| **模糊 completion_condition / 多解释** | 2 | 需要判断力选择正确解释 |
| **review_prep / investigation 类 task** | 2 | 判断类不是执行类 |
| **跨多个独立模块** | 1 | 需要全局视角 |
| **涉及 supersede 决策** | 2 | 必须正确判断 novelty |
| **task 描述本身复杂（>500 字）** | 1 | 复杂任务出错概率高 |
| **执行阶段 escalation（`execution_escalation`）** | 3 | task 在执行/escalation 阶段暴露"比 planner 估计更难"——这本身就是 weight=3 的强证据（§10.5 升级触发因素，一票通过 opus）|

### Sonnet 因素

| 因素 | 含义 |
|---|---|
| **标准 implementation（逻辑直接 / 模式清楚）** | 写代码按已知模式 |
| **标准 test（expected behavior 明确）** | 测试已定义行为 |
| **documentation（文字描述类）** | 主要写字 |
| **单一文件 / 单一模块** | 影响范围窄 |
| **done_criteria 完全可机械验证** | 不需要判断 |
| **关键路径上但简单** | 关键路径但任务本身没难度 |

## 决策算法

```python
opus_score = sum(opus_factors[factor].weight for factor in matched_opus_factors)

if opus_score >= 3:           # 任一 weight=3 因素 OR 多个低 weight 因素
  tier = "opus"
elif opus_score >= 2:         # 一个 weight=2 因素
  tier = "opus"               # 保守——bias toward opus
elif opus_score >= 1:
  if 关键路径 or red-line 相关:
    tier = "opus"
  else:
    tier = "sonnet"
else:                          # opus_score = 0
  if 任何 sonnet 因素:
    tier = "sonnet"
  else:                        # 完全没匹配
    tier = "opus"              # 不确定时 bias toward opus
```

## 组合规则

**Rule 1: 关键路径上 + opus 因素 → opus**
关键路径影响整体 makespan。即使单个 task 不严格需要 opus，关键路径上**不冒险用 sonnet**。

**Rule 2: 多个 weight=2 因素 → opus**
单一 weight=2 因素是 opus；多个累加更应该 opus。

**Rule 3: weight=3 因素任一命中 → opus**
red-line / state_machine / invariant 类——一票通过 opus。

**Rule 4: 完全无匹配因素 → opus（保守）**
如果 fork 看不出任何 opus 因素也看不出任何 sonnet 因素——说明它没充分理解 task。**默认 opus**——让能判断的模型来做。

## 升级机制（sonnet → opus）

执行阶段（M-1.4）发现 task 比预期复杂时，按更高 OPUS_FACTOR 重评 tier 并 re-emit 升级版分配。

**v3 实现（RUN-060）** —— 升级走现成 `score_tier` 算法，不另造平行升级逻辑：

- 触发入口：`uv run --directory harness python -m towow.cli.main plan model-tier-upgrade --task-id X --escalation-reason "..."`
  （执行/escalation 后由 planner 调；`--opus-factor` 可补执行阶段新发现的因素）。
- 重评：读 task 最新 `TaskModelTierAssigned` 拿当前 tier + 原 opus 因素 → `upgrade_tier()` 在其上
  强制叠加 `execution_escalation` 因素（weight=3）+ 新发现因素 → 经 `score_tier` 重算。
  escalation 因素 weight=3 ⇒ opus_score≥3 ⇒ opus（§10.4 Rule 3 一票通过），故任何 sonnet task
  escalation 后必升 opus，且升级分配天然携 matched factor evidence（满足 §10.6，非 evidence-less 自动升）。
- 接受 → re-emit 新 `TaskModelTierAssigned`（更高 tier）。projection 对 model_tier 是 latest-wins，
  新分配自然 supersede 旧的（无需重型 supersede_bridge）。
- 拒绝（已是最高 tier opus）→ push back，不 re-emit，解释为什么无法再升。

> spec §10.5 描述的"M-1.4 fork 产 `ModelTierUpgradeRequested` event"是设计语：v3 初版按 §10.5
> "sonnet task 中途失败 → 自动升 opus 重跑"简化，把 escalation 本身建模成一个 opus 因素，
> 复用 `TaskModelTierAssigned` re-emit，不单独注册 `ModelTierUpgradeRequested` 事件类型。

## Token Budget vs Tier 的 Tradeoff

不是"opus = 更多 token"——是"opus = 每 token 更值钱"。

| Tier | 每 token 成本 | 决策质量 |
|---|---|---|
| opus | ~5x sonnet | 高 |
| sonnet | 1x | 中 |

**关键判断**：task 错了的代价 > opus 跟 sonnet 的成本差时，用 opus。

例：
- 改 commit gate 核心逻辑 → 错了系统不能工作 → opus（值得）
- 改 README 措辞 → 错了重写也行 → sonnet

## Evidence 要求

每个 model_tier 分配必须带：

```yaml
model_tier_assignment:
  task_id: string
  tier: opus | sonnet
  matched_opus_factors: 
    - factor: string                    # e.g. "red_line_obligation"
      weight: int
      evidence: string                  # task 的什么部分触发了这个因素
  matched_sonnet_factors:
    - factor: string
      evidence: string
  opus_score: int
  rationale: string                     # 综合理由
  confidence: high | medium | low
```

## 常见误判

| 误判 | 实际 | 正确做法 |
|---|---|---|
| "implementation task → sonnet" | 看具体——改 commit gate 是 implementation 但是 opus | 按因素打分不是按 task_type |
| "documentation → sonnet" | 看具体——写 ADR 是 opus（设计决策记录）| 按内容判断 |
| "review_prep 简单——sonnet" | review_prep 是判断类——opus | review_prep 默认 opus |
| "关键路径 → 一律 opus" | 关键路径但任务简单可以 sonnet | 看任务本身 |
| "test task → sonnet 总没错" | 测试 state_machine transition 是 opus | 看测试什么 |
