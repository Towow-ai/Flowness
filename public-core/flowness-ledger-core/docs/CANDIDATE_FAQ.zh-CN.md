# Flowness Ledger Core 候选 FAQ

状态：**公开 Open Alpha；本地、实验性、非生产。**

## 它解决什么问题？

当一个变更需要先收集记录、再决定是否采纳时，最容易出错的是“还没决定的
半成品已经被下游当作事实”。Ledger candidate 把这两步分开：提案记录会被保存，
但只有出现明确的 `accepted` decision 后，committed reader 才会显示整组记录。

可以把它理解为：草稿先放进档案袋；盖了“接受”章才进入正式卷宗。盖“拒绝”章
也会保留审计历史，但不会进入正式卷宗。

## 已经能亲自验证什么？

用候选 wheel 的 `flowness-ledger-demo --demo-dir <新目录>` 可以生成一个
`demo-run.json`。它在同一次可检查运行中展示：

- pending records 在 committed view 中不可见；
- accepted 后两条提案记录一起可见；
- rejected record 仍不可见；
- 相互冲突的第二个 decision 被拒绝；
- 最后一行 JSONL 被中断时，读取拒绝继续，恢复只截断不完整尾部。

这些是本地候选语义证据，不是性能、分布式一致性、生产可靠性或真实 Agent
编排证据。

## 它目前不是什么？

它不是完整 Flowness 平台，不运行 Agent，不处理凭据、任务图、服务器、worktree
或客户数据。它已有一个 committed-type projection：watermark 覆盖完整 audit head，
旧投影会拒答直到 rebuild。其只读 review-verdict adapter 必须消费不可变的 terminal
decision。Linux aarch64 / CPython 3.12 是计划中的首个独立 clean-room
坐标，但当前精确候选仍需保留非作者 receipt 并经过新一轮 jury。本包目前不声称
任何已独立复现的平台兼容坐标。

## Open Alpha 标签意味着什么？

这个标签说明计划公开的范围和成熟度，不代表外部验收已经完成。正式发布前，未来的
精确 release record 必须绑定源码与许可证边界、密封 export、版本化 artifact、
负例 E2E、非作者 clean-room receipt、新一轮 jury 和 owner gate。运行本地 demo
不会完成这些要求，也不会把该切片提升为 Beta 或生产级。

## 如果 demo 相关证据变化，会发生什么？

候选 Harness 已把 demo 证据、机制、claim、解释段和 README 放进 Content Graph。
证据或机制变化后，相关材料必须回到 `evidence_bound`，重新审核后才可继续向
staged 或更高状态推进。
