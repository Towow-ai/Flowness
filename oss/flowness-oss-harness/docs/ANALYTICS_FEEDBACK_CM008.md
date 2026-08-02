# CM-008：私有聚合反馈与复审契约

CM-008 为未来的内容反馈闭环预留了一个很窄的、可校验的入口。它不是
analytics 接入、归因系统、自动化发布器，也不将阅读、安装或留存数字变成
“产品有效”的结论。

## 输入：只允许已聚合的观察

`analytics-feedback-observations/v1` 为每一组观察固定绑定：

- 已验证的 Content Graph v3 身份；
- 已验证的手工 channel package 身份与 channel / locale；
- collector、来源修订、UTC capture time 与 UTC observation window；
- eight metric families：attention、read、install、first success、retention、
  issue、contribution、adoption；
- 每项的计数或聚合分母，以及 minimum group size。

它明确禁止 raw personal data、raw event records 与逐人身份键。小于最小群组
的 event count、或分母不足的 rate 都会失败；因此不会以“聚合”之名泄露小样本。
来源只能标记为 `synthetic_fixture` 或 `private_aggregate_export`，后者也只是
手工导出的私有聚合物，不代表系统访问了任何外部平台。

合成、无外部数据的样例在
`tests/fixtures/analytics-feedback-synthetic-v1.json`。它只用来测试边界和
绑定，不是渠道成效、真实安装或外部采用的证据。

## 输出：只能接到既有复审义务

`analytics-feedback-interpretation/v1` 是 `analytics.interpreter` 的专用
typed output。它必须同时绑定：已验证 graph、已验证 feedback bundle、以及
已经存在的 Content Impact Review Plan。

每个 review link 必须精确引用该 plan 中已有的 obligation，并且 target 必须
等于这些 obligation 的 target。它无法凭一个指标新建 claim、扩大 scope、
发明待更新材料，或将一个 channel package 变为可发布。

解释只可标记为 `descriptive_only` / `review_context_only`。contract 会拒绝
诸如 “proves product value” 的产品价值推断，并固定 `claim_registry`、
`evidence_registry`、candidate、approval 与原始 analytics 为 `not_mutated`；
publish、network、credential use、schedule 与 external send 也固定为
`not_attempted`。

## 这能做什么，不能做什么

它可以让未来的团队在发现“某个已绑定渠道包的 aggregate install 观察变化”时，
把该观察作为 README、案例或渠道包的**复审上下文**，而不是静默改文案。

它不能证明用户价值、产品留存、因果归因、真实外部采用、benchmark 胜负或发布
授权。上述结论仍需要各自的独立证据、复现实验、jury 与 owner gate。
