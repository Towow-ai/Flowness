<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# All gates were green. The delivery was still incomplete.

[中文](#中文) · [Back to README](../../README.md)

This is a sanitized account of a real Flowness dogfood run. It is evidence of
a failure and the mechanisms added in response, not a benchmark or a customer
success claim.

## The scene

The run delivered an automatic forward chain, fixed two defects, added safe
pause/resume, exercised real background sessions, and passed the existing test
and release gates. Structurally, it looked complete.

Then the owner asked three ordinary questions:

1. Where is the human-usable operating guide?
2. Will the safety path actually be called, or does it only exist as a command?
3. Has every consumer of the new concepts been identified?

All three answers exposed gaps. The code existed and the gates were green, but
the delivery was semantically hollow in three places.

## Why shallow checks missed it

The specification already assigned completeness checks to several layers:

- consensus should enumerate every downstream consumer before freezing;
- planning should map every completion condition to a task and observable proof;
- execution should prove every task condition;
- review should compare the patch with the actual contract, not only the named bugs.

Those rules were present, but some remained prompt-level instructions and were
not wired into the fail-closed path. The run followed the shape of the process
without enforcing its meaning.

## What changed

| Gap | Before | Correction |
| --- | --- | --- |
| Consumer coverage | A parent concept had consumers; four child concepts did not | Consumer completeness became a blocking consensus-freeze check |
| Safety escalation | Pause/resume existed as a parallel command | Escalation was connected to automatic pause and the existing owner notification path |
| Human operability | Machine events were traceable; no usable runbook existed | An operations guide became an explicit delivery artifact |
| Stale dispatch | The dispatcher saw an event and spawned work even when downstream work was already complete | Resume now advances the watermark; further semantic preflight remains an explicit design question |

The important change was **soft rule → executable gate**. Adding another Agent
would not have fixed this. The missing mechanism was a consumer that enforced
the existing contract at the correct layer.

## What this case proves — and does not

It proves that Flowness dogfood exposed a concrete mismatch between structural
completion and semantic completion, and that several identified gaps were
turned into executable checks or integrations. It does not prove that every
semantic gap can be automated, that all six reflow routes are mature, or that
the complete current lifecycle has passed an organic public end-to-end run.

The retained lesson is now part of the architecture: a green gate is meaningful
only when it reads the authoritative object and protects a real downstream
consumer.

---

## 中文

这是一次真实 Flowness dogfood 的脱敏记录。它证明的是一次失败和失败后留下的机制，
不是 benchmark，也不是客户成功案例。

## 事故现场

这次运行完成了自动前进链、两个缺陷修复、安全暂停/恢复、真实后台会话验证，并通过
当时已有的测试和发布门。从结构上看，它已经完成。

然后 owner 问了三个很普通的问题：

1. 给人看的运维说明在哪里？
2. 安全暂停真的会被自动调用，还是只存在一个没人调用的命令？
3. 新概念的所有消费方都梳理完整了吗？

三个问题同时暴露缺口：代码存在、门全绿，但交付在三个地方仍然只有“形”，没有“实”。

## 为什么浅层检查没有抓到

原有规范其实已经把完整性检查分给不同层级：

- 工程共识冻结前，应列出每个概念的全部下游消费方；
- 计划应把每个完成条件映射到任务和可观察证据；
- 执行应证明每一条任务完成条件；
- 复查应比较真实 patch 与完整契约，而不只是检查被点名的 bug。

问题不是“语义天生无法检查”，而是部分规则仍停留在 Agent 的文字提示里，没有接进
fail-closed 路径。运行走完了流程的外形，却没有真正执行流程的纪律。

## 最小产物变化

| 缺口 | 修复前 | 修复后 |
| --- | --- | --- |
| 消费方覆盖 | 父概念有列表，四个子概念无人认领 | 共识冻结前的消费方完整性变成阻断门 |
| 安全升级 | pause/resume 是平行命令 | escalation 接入自动暂停和既有 owner 通知链 |
| 人可用交付 | 机器事件可追溯，没有人能直接使用的说明 | 运维文档成为明确交付物 |
| 过期派发 | 只要看到旧事件就可能再起一组 Agent | resume 推进 watermark；更深的语义预检保留为显式设计问题 |

真正重要的变化是：**软规则 → 可执行门禁**。多增加一个 Agent 解决不了这个问题；
缺失的是在正确层级读取权威对象、保护真实消费方的机制。

## 这个案例能证明什么

它证明 Flowness 在 dogfood 中抓到了“结构完成不等于语义完成”的具体失败，并把几个缺口
变成了可执行检查或真实接线。它不证明所有语义都能自动检查，也不证明六级回流都已成熟，
更不代表完整生命周期已经通过公开的真实任务端到端运行。
