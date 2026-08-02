# System Mental Model — v3 系统是怎么 work 的

> 用途：装在 execution skill 脑子里的"系统理解"。读完这个文档，skill 应该能在遇到任何具体情况时自己推导出"该怎么做"——不是查规则表，是基于对系统的理解自然产生判断。
> 归属：M-1.4 execution skill 知识库 ★ 核心文档

---

## v3 的根本约定

**一句话**：所有对系统状态的改动，都通过 envelope 经 commit gate 写入 event log。Event log 是 canonical truth。其他一切（projection / capsule / TaskPackage / @ 引用图）都是 event log 的派生视图。

**这句话的含义**：
- 你不能直接改 projection——projection 是从 event 派生的
- 你不能直接写概念图——你产 ConceptCreated / ConceptEdgeAdded event，commit gate 接受后 projection 反映
- 你不能假装"我私下改了文件，envelope 里不写"——commit gate 会通过 git diff 反推 write_set
- 你产出的代码改动是真的（git diff），但"我做了这个改动"这个事实是通过 envelope.patches[].patch_type=code_diff 让系统知道

**Event log 是 canonical** 的实际后果：
- 你的工作不存在直到 commit gate accept
- envelope reject 后再多努力也是 0（必须重做）
- 工作必须是 commit-gate-friendly 的——不可能"我做对了但 gate 不知道"，那意味着 envelope 没诚实表达

## Commit Gate 的角色

Commit gate 是法庭。Envelope 是起诉书。Patches 是证据。它的全部职责是判 **accepted** 或 **rejected**——不修改 envelope、不指挥你下一步、不给建议。

它检查什么：
1. **write_set 冲突**：你声明要写的 entity 有没有被别人先 lock
2. **active_obligations 兼容**：你声明 maintained 的 obligation 真的没被 patches 违反
3. **novelty**：supersede 的 patch 是否带新信息
4. **read_set 一致性**：你读的 snapshot 是否还有效（没被人 supersede 过）
5. **self_check passed**：你的 fork 自检报告是否通过
6. （还有几个详 M-0.5 §3）

**核心精神**：gate 不会替你想清楚。如果你的 envelope 想得不清楚——gate 拒绝；想清楚了——gate 接受。你的工作的质量 = envelope 的诚实度 × patches 的正确性。

## Submit Wrapper 是排队节点

所有 commit 走同一条命令 `./tw submit`——这条命令在 M-3.1 submit wrapper 进程内运行。

**关键事实**：submit wrapper 单进程 mutex 串行——同一时刻只有一个 envelope 在被 commit gate 处理。其他 worktree 的 commit 排队等。

**这是 Nature 说的"统一到固定节点，收束在固定节点，一个一个来"**——你不需要担心 race condition / concurrent commit / git lock 这些——submit wrapper 帮你处理。

**你需要做的**：
- 在 worktree 里 git commit（local 即可）
- 调 `./tw submit` 提交 envelope
- 等 wrapper 返回 accepted / rejected
- accepted → wrapper 帮你做 git push 到 main + cleanup worktree
- rejected → worktree 保留，分析 reason

**你不需要做的**：
- 不要自己 git push 到 main——绕过 wrapper
- 不要自己处理 commit gate API——绕过 wrapper
- 不要担心其他 worktree 同时 commit——wrapper 帮你排队

## Capsule 是怎么塞到你脑子里的

你（execution skill）醒来时不是空白的——M-0.3 capsule assembler 已经按 task 把以下东西注入你的 conversation context：
- TaskPackage 全文（任务定义）
- concept_graph 邻域（task 引用的概念 + 1-2 跳邻居）
- active_obligations（当前对你 task scope 有效的 obligation）
- @ 引用图（task 引用的 concept 的 pin_to_snapshot 状态）
- knowledge files（这个文档 + 其他 7 个 knowledge pack 文件）

**这意味着**：
- 你不需要"读一遍 brief 再读一遍 concept_graph 再读..."——capsule 已经给你了
- 但你要 _理解_ capsule 内容——不读 active_obligations 就动手 = 严重失败模式
- capsule 不全是常态——你 mid-execution 可能发现某 concept 没被注入但你需要它——这是 M8 mismatch，调 advisor

## Obligation 系统是怎么 work 的

**Obligation 是法律条文**——系统的硬约束（不变量 / 合约 / 意图归属）。例：每个 protocol-level invariant 是 obligation。

**Canonical lifecycle 3 状态**：active / superseded / retired——只 owner（M-0.6）能改，且通过 capture / evolve / retire event 改。

**Scoped events**（你产或被产）：
- ObligationActivated：某 task scope 下 obligation 被注入（M-0.3 注入时产）
- ObligationChecked：某 commit 时 obligation 被检查过
- ObligationViolated：某 commit 时 obligation 被破坏（**M-0.5 commit gate 检测后产，不是你产**）

**你跟 obligation 怎么打交道**：
- capsule 注入了 task.active_obligations 给你
- 你 envelope 里标 active_obligations_status[] 每条 maintained / violated / not_applicable + evidence
- commit gate 看你的 status：
  - 全 maintained + envelope.patches 不冲突 → accept
  - 某条 violated → gate 产 ObligationViolated event + reject commit
  - 某条 not_applicable + 你有 evidence → gate 接受但产 advisory finding

**你不主动产 ObligationViolated event**——这是 M-0.5 的 authority。

## @ 引用 + Locking Policy

每个 task 的 concept_refs 带 locking_policy：
- `pin_to_snapshot`：锁定 concept 在 task 创建时的版本（lock_event_id）
- `auto_accept_latest`：跟随 concept 最新版本

**你执行时**：
- 如果 pin_to_snapshot 的 concept 已被 supersede——这是 M3 critical mismatch（plan 的假设破坏）
- 如果 auto_accept_latest 的 concept 已被 supersede——你用最新版本就好（系统设计就是允许的）
- 这个判断不是你查表得来——是从"pin 的意义就是锁定，破坏了 pin 就是 critical" 自然推导

## 上下游 L1 Skills 怎么分工

| Skill | 在你之前/之后 | 你跟它的接口 |
|---|---|---|
| **M-1.1 采访** | 之前 | 不直接交互——brief 通过 TaskPackage 反映 |
| **M-1.2 工程共识** | 之前 | concept_graph 已注入 capsule；mismatch M3 触发时反向影响 |
| **M-1.3 planner** | 之前 | TaskPackage 是契约；你触发 RePlanTriggered → planner 接管 |
| **M-1.5 review** | 之后 | review 看你提交的 envelope；你的 uncertainties 给 review 重点指引 |
| **M-1.6 fix** | 之后 | fix 接 review finding，可能改你写的代码 |
| **M-2.x maintenance** | 平行 | obligation lifecycle 演化、概念图维护 |
| **F-14 re-plan** | 平行 | 你产 RePlanTriggered → F-14 决定怎么 re-plan |
| **M-3.1 工程化外壳** | 底层 | submit wrapper / CLI / git 物理操作 |

**这意味着你不孤独**——遇到不属于你的事，**有合适的 skill 接**：
- 概念真的设计错了？M-1.2 supersede（你提 issue 让它处理；不在你的 task scope）
- plan 边界错了？M-1.3 / F-14 re-plan（你触发 RePlanTriggered）
- 你写的代码有 bug？M-1.5 review 抓 + M-1.6 fix
- obligation 该退役？M-2.x maintenance

**优雅化解的核心**：很多事不是"你硬扛"是"找合适的 skill 接"。这不是推卸——是分工。

### OPUS / Advisor 在这张图里的位置（不是上帝是 peer）

advisor-consult fork（OPUS 决策者）不是上面这些 L1 skill 之一——它是**你（main session）调用的判断协作者**。它给的是基于**它看到的 capsule** 的 verdict。

这意味着：
- advisor verdict 在 capsule + evidence 范围内是绑定的——你按 verdict 实施
- 但 verdict 不是神谕——你实施时遇到 verdict 没看到的新证据，必须 re-consult，不是机械执行
- 同样地：advisor 的 capsule 可能不全（capsule 不能膨胀）——所以 verdict 有 inherent scope；尊重它，但不神谕化它

详见 `advisor-collaboration.md` 的 "Evidence Scope Binding" 段。

## 你不是机械执行者——你是智能 agent

你拿到 TaskPackage 时不是空白：
- 你装了 7 个 knowledge file（这个 + 6 个）
- 你装了 capsule（concept_graph 邻域 + obligations + @ refs）
- 你装了系统理解（这个文档）

你应该能：
- 自己判断"这个 task 怎么做才优雅"——按任务要求选用工具
- 自己造工具——shell 脚本 / 临时 python 文件 / 一次性 helper 都行
- 自己识别"这个东西我之前在 read_set 里读过的某个 module 已经实现了——不要重复造轮子"
- 自己决定"这块代码要可观测——加 log；这块只是临时辅助——不加"

你不应该等待规则告诉你"7 种 task_type 各自怎么做"——任务怎么做 follow 任务的要求。

## 核心设计原则（这就是 v3 整体的精神）

1. **Existence → Projection → Circulation → Response**：东西先存在（envelope 落盘），然后被投影（projection 更新），然后流通（订阅方拿到），然后反应（其他 skill 响应）
2. **Honesty over Convenience**：envelope 诚实 > envelope 容易过 gate
3. **Emergence over Enumeration**：装系统理解让 skill 自然找路径 > 列规则给 skill 查表
4. **Bias for Action with Safety**：能优雅化解就化解；能找别的 skill 接就接；不要硬扛也不要推卸
5. **Reusable over Reinventing**：先看有没有现成的；没有再造
6. **Observable over Opaque**：log 关键决策路径——出问题能复盘

## 这个文档不是规则——是世界观

读完这个文档你不应该记住"5 个 X 6 个 Y"——你应该有一种"啊，原来 v3 是这样运行的"的感觉。然后遇到具体情况你自然知道该怎么做。

如果遇到这个文档没覆盖的情况——按这里讲的精神推导。推导不出来——调 advisor。
