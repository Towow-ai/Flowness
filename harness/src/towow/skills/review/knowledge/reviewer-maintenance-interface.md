# Reviewer 维护接口（Nature 怎么改 reviewer）

> spec source: 04-l1-intelligence/M-1.5-review-skill-detailed-design.md §8（维护接口）
>
> 这是 Nature 第五点要求 —— reviewer 自身要 **modular + diagnosable**。用了几轮 review 后发现 reviewer
> 行为不对，Nature 想去改 reviewer 时，他必须清楚：改哪些地方，影响哪些。每个症状都有**明确的物理位置**
> ——这是 F-20 机制生命周期的精神在 reviewer 自身的应用。
>
> 读法：左列是你观察到的「reviewer 行为不对」的症状，中列告诉你该改哪一层（layer），右列是这一层的
> 物理位置（文件 / 字段 / 协议点）。不要从主 SKILL 人格下手猜——按这张表定位到层再改。

## 症状 → 改哪个 layer → 物理位置

| 症状（reviewer 行为不对）| 改哪个 layer | 物理位置 |
|---|---|---|
| Reviewer 漫游找假问题（VoI 没过滤住）| Review_plan 的 voi_criteria | M-1.5 design-time fork 的 prompt + 实际 voi_criteria 写法 |
| Reviewer 漏了某个 dimension（覆盖不全）| Review_plan 的 dimensions | M-1.5 design-time fork + 风险面 → 维度映射（F-08b）|
| 某个方法论 fork 探查方式不对 | 对应 fork SKILL.md | `.claude/skills/review/methodology-{name}.md` |
| 整体 review 思路偏了 | Knowledge file `review-mental-model.md` | `.claude/skills/review/knowledge/review-mental-model.md` |
| 历史失败模式没喂入 | Historical feed 上游源 | Nature 手动 feedback memory / ADR / issue（F-08e）|
| Verify-step 过滤太严/太松 | Verify-step fork SKILL.md | `.claude/skills/review/verify-step.md` |
| Meta-review 没抓住 review_plan 设计缺陷 | Meta-review fork SKILL.md | `.claude/skills/review/meta-review.md` |
| 风险面识别错（自动 detection 漏 / author 漏报）| 概念图风险面节点 + 自动 detection rule | M-1.2 工程共识概念图 + Detection Rule Lifecycle（M-2.x）|
| Dispute 总是无意义振荡 | NoveltyCheck 在 commit gate 的判定 | M-0.5 §3.5 NoveltyCheck（修 substantive audit 阈值）|
| Finding 不可定位（target/suggested_fix_layer 字段空）| Finding schema enforcement | M-1.5 主 SKILL 自检 + commit gate fields validation |
| Multi-perspective 通胀（多 fork 重复）| Aggregation fork prompt | M-1.5 aggregation subagent SKILL（去重逻辑）|
| Reviewer 想越权改代码 | V-02 工具白名单 | Fork SKILL.md frontmatter `tools:` 字段 |
| Severity 通胀 | Fork SKILL.md severity 校准 prompt | 每个 method-* fork SKILL |
| Falsification 精神缺失（漫游赞美 patch）| Fork SKILL.md falsification framing | 每个 fork SKILL "我是谁" 段 |

**每个症状有明确改的物理位置** —— 这是 F-20 机制生命周期的精神在 reviewer 自身的应用。改一处只动一层，不会
让你「不知道改这里会不会连带改坏别的」。

## 为什么这张表存在（设计意图）

Reviewer 不是黑箱。它的行为由几层正交的物理层决定：

- **Review_plan 层**（voi_criteria / dimensions）—— 决定 review「看什么、过滤什么」，design-time fork 产出。
- **方法论 fork 层**（method-* SKILL.md）—— 决定每个 lens「怎么查」，含 severity 校准、falsification framing。
- **Knowledge pack 层**（review-mental-model.md 等）—— 决定 reviewer「整体思路」。
- **Verify-step / Meta-review 层**—— 独立 falsifier + review_plan 自审。
- **Commit gate 层**（NoveltyCheck / Finding fields validation）—— 决定 dispute 闭环、Finding 可定位性。
- **V-02 工具白名单**（fork frontmatter `tools:`）—— 物理隔离 reviewer 不能越权改代码（保护 context 干净）。

定位到层之后，改动是 surgical 的：你知道这一处影响什么，不影响什么。这正是「reviewer modular + diagnosable」
的兑现 —— Nature 第五点要求的落地。
