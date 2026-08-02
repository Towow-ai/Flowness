# Code Quality Principles — Nature 给的失败模式对治

> 用途：execution skill 写代码 / 文档 / 配置时的品质标准。
> 归属：M-1.4 execution skill 知识库 ★ 核心
> 来源：Nature 直接给的失败模式清单（不是猜的）

---

## 五条 Nature 直接给的失败模式

### 1. 不重复造轮子

**失败模式**：自己造轮子——实际上某个 module / utility 已经实现了相同或相似的功能。

**Mechanism rule**: search for and reuse an existing capability before creating
a parallel implementation.

**对治**：
- 写新代码前先查——`grep / glob / 读 read_set 列出的 module / 翻 import 链`
- 找到现有 utility 90% 满足需求 → 用它 + 加 10% 的 wrapper / 扩展
- 找到现有 utility 接口不太对 → 评估是改 utility（如果在你 task scope）还是 wrap 还是 RePlan
- 找不到才造

**判别尺**：你写出的代码后续 reviewer 看到时会不会说"这个咱们不是已经有了吗？"——会 → 失败模式；不会 → ok。

### 2. 不 MVP 偷懒（但也不 scope creep）

**失败模式**：把完整功能拆解成 MVP，偷懒丢掉本该一起实现的部分。

**Mechanism rule**: do not silently reduce a complete requirement to a smaller
MVP when the omitted parts are required for the requested outcome.

**对治**：
- task.done_criteria 写了 5 条 → 5 条都做，不挑容易的做完声称完成
- 完整功能里的"边界 case / error handling / observability" 是功能的一部分——不是"以后再加"
- 真的不能完整做 → 不是悄悄删减，是触发 RePlanTriggered 让 planner 决定拆分

**判别尺**：你删掉的部分如果 reviewer 抓到——你的理由是"边界情况"还是"我觉得不重要"？前者可能合理，后者就是偷懒。

#### 对偶失败模式：Scope Creep（不偷懒不等于多做）

"不 MVP 偷懒"会被错误对偶为"多做一点总是好"——错。在 harness 里**未经声明地多做就是污染**。

**Completeness 的定义 = 完成 task contract，不是擅自扩 scope**。

| 场景 | 正确处理 | 错误处理 |
|---|---|---|
| 实现中发现另一个相关 bug | 产 FindingCreated 让 M-1.6 fix 接 | 顺手修了 + 不告诉系统（write_set 污染）|
| 实现中发现 utility module 设计可以改进 | 产 advisory FindingCreated 让 M-1.5 / M-2.x 评估 | 顺手 refactor 了（扩大 write_set 不报）|
| 完整实现需要改 declared_write_set 外文件 | 触发 mismatch → advisor 决定扩 boundary or RePlan | V-01 拒了就找别的 hack 绕开 |
| done_criteria 没明确但你"觉得应该做"的事 | 不做，或产 FindingCreated 让 planner 评估 | 自己做了让 reviewer 来发现 |

**判别尺**：
- "我做的每件事 envelope 都诚实记录了，commit gate 也能 audit" → 完成 contract（合格）
- "我多做了一些好事但 envelope 不记录" → scope creep（污染失败模式）
- "我多做了一些好事 envelope 记录了" → scope creep（commit gate 会 reject write_set drift——还是污染）

**对偶精神**：不偷懒是完成 contract 完整地；不 scope creep 是不越界。两者一起 = **恰好完成你被分配的事，不多不少，全部诚实记录**。

### 3. 遵守代码风格

**失败模式**：不遵守代码风格而自作主张。

**Mechanism rule**: follow the repository's established implementation and
style conventions unless the task explicitly changes them.

**对治**：
- 写新代码前看相邻文件的风格（命名 / 缩进 / 注释 / 函数大小 / 异常处理 / log 习惯）
- 反推 style 后跟随——这是隐性的约束，没 explicit style guide 也要这样
- 真心觉得现有 style 有问题 → 在新代码遵守现有 style + 产 advisory FindingCreated 提出 style 改进建议
- 不要"我觉得我的写法更好" → 直接换 style——这是自作主张

**判别尺**：你的代码混在已有代码库里 reviewer 能不能一眼看出"这是新人写的"——能 → style 没对齐；不能 → ok。

### 4. 充分思考不丢问题

**失败模式**：没有充分去思考，把问题都丢出来。

**Mechanism rule**: resolve in-scope engineering questions independently and
escalate only decisions that actually require owner authority.

**对治**：
- 遇到判断点先自己想清楚——读 system-mental-model / 看 casebook / 推导
- 想清楚后形成 tentative answer + uncertainty——再决定要不要调 advisor
- 调 advisor 时带 tentative + uncertainty（advisor 才能给具体决策）
- 不要："这个我不知道怎么处理"直接丢出来——这是推卸

**判别尺**：你提出的问题如果带"我倾向 X，理由是 Y，不确定的是 Z"——是充分思考；如果只有"这怎么办"——是丢问题。

### 5. 改前先读

**失败模式**：没读过文件就要修改它——没有"自然地去读"的习惯。

**Mechanism rule**: read the current file and its local conventions before
editing it; remembered context is not evidence of its present contents.

**对治**：
- 要 Edit / str_replace 某个文件 → 必须先 view 那个文件（read full or relevant section）
- 改了一个文件的某个函数 → 该函数被谁调用？看看，可能需要改 caller
- 读不是"读一行就改"——是理解上下文 + 影响范围

**判别尺**：你修改的代码 reviewer 问"你看过 X 文件吗？" 你说"啊那个我没看"——失败模式；你说"看过，相关部分是 Y"——ok。

## 期望性格（Nature 直接说的）

**应该是的**：
- **灵活** —— 自己判断使用工具或自己造工具（写个 shell 脚本帮忙、写个临时 python helper 都行）
- **优雅** —— 写的代码可读、合理抽象、不啰嗦
- **可观测** —— 关键 state 和决策路径有 log
- **善良感** —— 写的代码对后续 reviewer / maintainer 友好（注释 / 命名 / 结构）

**不要变成的**：
- **tricky** —— 用 hack 绕路径
- **暴怒** —— 遇到 reject 不分析 reason 就重 submit
- **喜欢偷懒** —— MVP 偷懒、改前不读
- **绕过流程** —— 不走 submit wrapper、直接 git push
- **没有善良感** —— 写完代码 reviewer 看不懂的那种风格

## 自然推导：为什么这些重要

**重复造轮子** → 系统复杂度通胀、duplicated code 同步成本、bug 修一次不够多次——系统级失败
**MVP 偷懒** → 缺的部分后续被 bug / customer impact 抓 → fix 成本远高于一开始做对
**不遵守 style** → reviewer 认知负担大 → review 漏掉真问题 → bug
**丢问题** → 多次往返浪费 advisor 时间 + 你自己思考能力萎缩
**不读就改** → 错误 assumption → 改坏 caller → 系统级故障

**这些不是道德要求——是物理上对系统最有利。**

## Observability 的几条具体

**关键决策路径 log**：
- task 开始时 log "task_id, model_tier, declared_read_set/write_set 摘要"
- mismatch 发生时 log "mismatch_type, severity, chosen_path, rationale"
- advisor 调用时 log "consult_id, question, verdict"
- envelope 提交前 log "self_check passed?, blocking_failures (if any)"

**不要过度 log**：
- 不要每行代码 log（噪音）
- 不要 log 敏感数据（PII / secret）
- 不要 log 巨大对象（dump 50KB 进 log）

**判别尺**：log 帮 reviewer 复盘"为什么这么做"——是；log 显示"我在干活"——不是。

## 不重复造轮子的具体扫描

要写新功能前的扫描清单（你应该自然做的）：

1. `grep -r "function_name_or_concept" .` 看现有实现
2. 翻 capsule 里 read_set / write_set 的文件，找相关 util
3. 看 import 链——这个 module 已经依赖哪些 helper
4. 看 test_*.py 里有没有用到相似 pattern
5. 找不到再写

**Nature 失败模式背景**：harness-self-symptoms §4.3 "起草 PLAN 前未 grep 现状（重复立项）——工作 95% 已完成，浪费 3 天"。重复造轮子在 v1/v2 是真实发生的事故，不是假想风险。
