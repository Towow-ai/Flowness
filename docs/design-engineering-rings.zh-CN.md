<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 设计环 × 工程方案环：为什么 Flowness 不从写代码开始

[返回中文 README](../README.zh-CN.md) · [Architecture overview](architecture.md)

> **English summary:** The design ring turns a brief into competing,
> falsifiable design choices. The engineering ring turns the frozen design into
> implementable contracts, failure semantics, tests, migration, and operations.
> The private runtime contains their main objects, CLI, gates, skills, tests,
> and partial routes; automatic tier routing and engineering-spec → consensus
> publishing are not closed, and no organic public E2E has been demonstrated.

普通多 Agent 框架往往从“把任务拆给谁”开始。Flowness 把这个问题后移，因为角色分配
之前还有两个更承重的问题：**我们究竟选择了什么？这个选择怎样才能被不同 Agent 可靠实现？**

```mermaid
flowchart LR
    B["可确认的 Brief"] --> D["设计环"]
    D --> DB["冻结后的设计方案"]
    DB --> E["工程方案环"]
    E --> ED["工程方案卷宗"]
    ED --> C["工程共识蒸馏"]
    C --> P["计划与任务包"]
```

## 设计环：先让选择可以被攻击

设计环不是派一个名叫 Designer 的 Agent 写方案。它把设计变成五个不同的认知动作：

1. **建模**：把目标、约束、参与者、成功标准和信息缺口展开成 Design IR；
2. **发散**：由隔离的参谋提出真正不同的候选，而不是同一答案的措辞变体；
3. **推演**：把候选放进关键场景，检查行为、状态和用户结果；
4. **对抗**：做错位扫描、红队、事前验尸和反例攻击，尝试证明它不应该被实现；
5. **收敛**：记录被排除的方向、剩余取舍和可证伪预测，再冻结设计方案。

| 输入 | 核心问题 | 输出 |
| --- | --- | --- |
| Brief、约束、owner 判断、历史失败 | 哪个结构最可能产生目标结果？什么证据会推翻这个判断？ | 候选、取舍、场景、攻击结果、预测、冻结设计 |

只有当设计真正改变后续工程决策，它才不是装饰性文档。

## 工程方案环：把设计翻译成所有 Agent 都不能随意改写的契约

设计回答“要形成什么结构”，工程方案回答“它怎样被可靠地实现和运行”。完整工程模型
覆盖的不是一份 API 清单，而是一组彼此约束的切面：

- 组件边界、接口与数据契约；
- 状态机、不变量、并发与幂等；
- 失败语义、恢复、回滚和 FMEA；
- 参数来源、默认值、测量值与未知值；
- 测试架构、哨兵测试和不能由测试替代的运行门；
- 性能、容量、迁移、可观测性和运维；
- 从设计对象到实现组件与测试证据的追溯映射。

研究结论不能直接变成一个看似精确的参数。参数必须区分：**已测量、暂定默认、仍未知**。
冷启动探针和熵探针用来检查：陌生执行者能否只靠工程方案开始工作，以及关键信息是否
在压缩和交接中丢失。

| 输入 | 核心问题 | 输出 |
| --- | --- | --- |
| 冻结设计、现有代码、运行事实、研究证据 | 不同 Agent 怎样实现同一设计，而不会各自发明接口、状态和失败处理？ | 组件/接口/状态/失败/测试/迁移/运行契约及追溯关系 |

## 工程共识不是第三份大文档

工程方案可以很大，但每个任务不应该吞下整份卷宗。工程共识从中蒸馏三类稳定事实：

- 所有任务共同遵守的不变量；
- 与某个任务有关的最小上下文切片；
- 被新版本 supersede 时必须通知的消费方。

这样，计划引用的是版本钉住的事实，不是 Agent 对一份长文档的临时理解。

## 当前真实状态

| 状态层 | 结论 |
| --- | --- |
| 设计/工程方法与对象 | 已有冻结卷宗、CLI、Skill、schema、门和测试 |
| 内部接线 | 有部分正向与回流路线，也完成过内部穿越和正反探针 |
| 仍未闭环 | 自动 tier routing；工程方案到共识的正式发布链 |
| 仍未证明 | 一个全新的真实任务从采访经过双环到最终验收；与旧流程的公平效能对比 |
| 当前公开包 | 尚未开放双环的可运行 E2E；只运行执行 → 复查 → 返工 → 验收内核 |

因此，这篇文档描述的是**已经开始成为软件的机制，以及它尚未闭环的地方**。它不是把
理想流程写成现有产品，也不会用结构存在替代真实任务效果。
