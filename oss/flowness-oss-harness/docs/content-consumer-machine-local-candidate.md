# Content Consumer Machine：本地确定性候选闭环

这份闭环专门返工 Round 3 的 `CMM-001` / `CMM-002`。它把原有的
“影响计划 + 待派发角色”推进为可复核的**本地确定性候选执行**，但不把该结果
包装成实时 watcher、真实 Agent 协作或发布能力。

## 生成链

```text
sealed predecessor/current Content Graph
  → deterministic impact plan
  → update receipt / bounded role route
  → package-instance registry (every JSON package in declared scope)
  → local deterministic producer records
  → two separately sealed reviewer-role records per work item
  → work closure
  → every package instance: retain, or rebuild + withdrawal plan
```

`content-package-instance-registry/v1` 列举声明目录里的每一个 JSON package，
并绑定 package ID、package hash、图版本、channel、locale、直接及 audience-track
资产和限制。目录中新出现但未在密封 registry 中的 package 会导致重算不一致而
fail closed；因此不能只看一个 channel slot 就假设所有版本包都被找到了。

`content-consumer-machine-run/v1` 只可在空的本地输出目录中创建。它真正写出：

- 每个 routed producer 的 typed private output；
- 每个 work item 的两份分别绑定 reviewer role 的 review record；
- 输入、输出和 closure 的 hash；
- registry 中**每一个** package instance 的行动项。

受影响实例得到“先使私有候选失效、仅从 current verified graph 重建并重新验证”的
withdrawal/rebuild plan；任一 reviewer result 为 fail 时该实例保持
`rebuild_blocked_unresolved_review`。不受本次变更影响的实例也显式为
`not_affected_current_instance`，不是被遗漏。

## 重要边界

执行器不启动 Agent 进程或模型，不作人类/模型语义判断，不修改 Content Graph、
claim、evidence 或源材料；也不联网、使用凭据、排期、发布、撤回或发送。两份 review
record 的“独立”仅指不同的 deterministic evaluator identity 和独立密封文件，**不是**
独立人类或模型评审的证据。

所以它关闭的是私有候选的机械可追溯链：
`change → affected package instance → evidence records → paired result → retest/closure`。
它仍不能关闭真实的多 Agent runtime、语义质量、外部 clean-room、owner approval 或
任一渠道发布门槛。
