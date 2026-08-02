# CM-007 内容机器角色契约（私有静态能力）

这不是一个已运行的发布流水线。CM-007 只把未来多 Agent 内容工作中最
容易越界的部分写成可校验契约；当前执行策略仍为 `planning_freeze`，因此
不得据此启动任何角色、创建渠道内容、收集数据或发送内容。

## 输入、输出与隔离

五个 producer 都只能读取两个已经独立验证的身份：

1. sealed/verified Content Graph v3；
2. sealed Content Impact Review Plan。

它们分别写入自己的 `role-private/<role_id>` 输出边界，且只可返回
`content-role-output/v1`：

| Role | Typed output |
| --- | --- |
| `content.compiler` | `content_draft` |
| `visual_demo.compiler` | `visual_demo_draft` |
| `channel.adapter` | `channel_adapter_draft` |
| `publisher.stager` | `package_review` |
| `analytics.interpreter` | `analytics_interpretation` |

`judge.channel-distribution-a` 与 `judge.channel-distribution-b` 保留为彼此
隔离的 jury roles。它们只能审阅 blind sealed channel package，并继续只能
输出 `jury-report.schema.json`；不能阅读其他 producer 或 judge 的输出。

## 不可越过的边界

所有七个角色都被配置和加载器同时约束为：

- 不得修改 claim/evidence registry、candidate state、approval state 或原始
  analytics data；
- 不得提升 claim、删除 limitation，或将草稿解释为认可；
- 不具有 publish、network、credential use、external send 或 schedule 能力；
- 输出中的相应 attestation 必须为 `not_mutated` / `not_attempted`；
- role registry 漂移（错误 schema、输出种类、输入约束、边界或禁令）会在
  controller 读取角色前被拒绝。

`validate_content_role_output` 还要求调用方传入已验证 graph 与 review-plan
的精确 identity；模型自报的 candidate、graph、claim、evidence 或 approval
不能成为可信输入。

`analytics.interpreter` 还受 CM-008 的专用输入/输出契约约束：它只能消费
脱敏的 aggregate observations，并且只能把它们链接到既有 impact review plan
中的复审义务。详见 [ANALYTICS_FEEDBACK_CM008.md](ANALYTICS_FEEDBACK_CM008.md)。
这个扩展不会把 attention、read、install、first success、retention、issue、
contribution 或 adoption 直接解释为产品价值，也不会赋予数据采集能力。

## 尚未证明的事情

CM-007 不证明 Content Graph 已对外 sealed、不证明任何渠道可发布、不证明
真实 analytics、角色隔离的主机边界、或独立 jury 复测。运行时进入这些角色
前仍需要解除 execution freeze，并以实际 sealed inputs、admission cards、
role-private mount/output roots 和完整 channel jury bundle 再验收。
