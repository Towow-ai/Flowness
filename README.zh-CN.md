<!-- SPDX-License-Identifier: CC-BY-4.0 -->

<div align="center">

# Flowness

### 把一句模糊的工程目标，逐步变成设计方案、工程决策、并行工作和真正通过独立验收的结果。

Flowness 不是让更多 Agent 同时写代码。它在执行前建立一条认知流水线；
发现问题时回到真正出错的上游层，而不是机械重试；最后用独立复查和证据定义“做完”。

[English](README.md) · [简体中文](README.zh-CN.md) · [运行演示](oss/flowness-oss-harness/docs/open-alpha-demo.md) · [架构总览](docs/architecture.zh-CN.md)

</div>

![Flowness 完整生命周期：采访、设计方案、工程方案、工程共识、计划、并行执行、独立复查、返工与验收](docs/assets/flowness-lifecycle.svg)

> **今天能运行什么：**公开 Alpha 已跑通“执行 → 复查 → 定向返工 → 验收”内核。
> 设计方案与工程方案是下一批开源重点；它们在私有运行时中仍是部分接线，尚无真实任务
> 端到端运行证据。

## 真正的问题不是 Agent 不够多

多 Agent 可以看起来很忙，却仍然以几种熟悉的方式失败：

- 一开始就没有把目标问清楚；
- 产品判断未经设计，直接漏进代码；
- 没有冻结工程契约，各 Agent 按不同假设实现；
- 复查发现了症状，但真正的错误其实发生在计划、工程方案甚至设计层；
- 生产者自己宣布完成，系统没有更强的“做完”定义。

Flowness 把这些看成不同层级的不同失败。它把模糊目标变成一组明确产物，
让不同 Agent 并行工作，并让可信的 `FAIL` 或关键 `UNKNOWN` 一直保留，
直到真正的 blocker 被修正并由新的独立评委重新验证。

## 不是一袋 Prompt，而是一条完整工作流

完整 Flowness 生命周期是：

`目标 → 采访澄清 → 设计方案 → 工程方案 → 工程共识 → 计划 → 多 Agent 并行执行 → 复查 → 修复/回流 → 可验收结果`

| 环节 | 产出 | 它防止什么 |
| --- | --- | --- |
| 采访 | 可确认的 brief、约束和未决信息 | 一开始就自信地做错问题 |
| 设计方案 | 备选方向、取舍、可证伪预测和冻结后的设计方案 | 代码正确、产品却错了 |
| 工程方案 | 组件、接口、状态、失败语义、参数和测试架构 | 各 Agent 理解不同、实现互相冲突 |
| 工程共识 | 版本化概念、不变量、状态机、消费方和 supersede 规则 | 共识跨会话悄悄漂移 |
| 计划 | 带依赖、设计引用和验收条件的任务图 | 并发很热闹，最后却合不起来 |
| 执行 | 隔离工位、声明的读写范围、产物与证据 | 冲突被隐藏、“做完”无法核验 |
| 复查与修复 | 独立 Finding、稳定问题、定向返工和由新评委重新复验 | 自审自批、平均分掩盖关键失败 |

并非每个任务都要走满全程。合理的路由规则是：小而可逆的工作走短路径，高影响任务
进入完整流程。私有运行时目前仍保留人工判断；自动分级还不是完全由机器强制的保证。

## 只需要记住三个概念

### 1. 认知编译

Flowness 把人的意图逐层编译成机器能执行、评委能证伪的对象：brief、设计、
工程规格、冻结共识、任务图、产物、Finding 和验收证据。重点不是文档变多，
而是每一层都必须真正改变下游行为。

继续展开：[《设计环 × 工程方案环：为什么 Flowness 不从写代码开始》](docs/design-engineering-rings.zh-CN.md)。

### 2. 分层回流

工作失败时，“再试一次”不一定是正确答案。Flowness 建模了六种回流深度，让问题可以
回到真正需要改变的最近一层：

`re_execute → repair → replan → re_engineer → redesign → re_interview`

越往上游回退，成本越高，因此路由契约要求给出排除链：为什么更轻的修正不够。
六级分类、CLI 和部分执行路线已经存在，但成熟度不同：`replan` 有真实历史，设计与
工程方案回流仍在收口并等待真实任务验证。

### 3. 证据化验收

Agent 说“做完了”不是终态。候选产物绑定内容身份，生产者与评委隔离，Finding
跨返工保持同一个问题身份，后继候选必须交给未参与前轮的新评委复验。一个可信的关键失败会
阻断结果，不能被平均分或漂亮总结抹掉。

## 一个 60 秒真实案例：我们发现自己缺了两个上游环节

Flowness 的真实重构，暴露了 Flowness 自己的结构问题：系统能把工作分给多个 Agent，
却不能可靠地把“为什么选这个方向”和“所有 Agent 必须共同遵守什么”传下去。于是我们
把两个问题分开处理：设计方案记录备选方向、取舍、场景和可证伪预测；工程方案则在计划
之前明确组件、接口、状态、失败语义和测试架构。

这是真实实现工作，但还不是一个已经完成的成功故事。私有运行时已有主要对象、CLI、门、
测试和部分正向与回流路线；自动分级和“工程方案 → 共识”的发布链尚未闭环，也没有真实任务
端到端证据。下面的公开 demo 只证明更窄的验收内核。

另一次 dogfood 发生了更具体的失败：所有门禁规则都显示绿色，但 owner 一追问，就同时
暴露了缺失的运维说明、从未被调用的安全路径和四个无人认领的消费方。详见
[《所有门都绿了，为什么仍然没有真正做完？》](docs/cases/green-gates-empty-delivery.md#中文)。

## 10 分钟运行公开证明

Open Alpha 当前公开并可运行的是“执行 → 复查 → 返工 → 验收”内核：三个隔离的
producer 并行产出；第一轮因一个强制失败被阻断；系统定向返工；两名新的确定性策略评委复验；
最后验证整条 trace。

```bash
git clone https://github.com/Towow-ai/Flowness.git
cd Flowness
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ./oss/flowness-oss-harness
.venv/bin/flowness-oss open-alpha-demo \
  --output /tmp/flowness-open-alpha-demo
.venv/bin/flowness-oss open-alpha-demo-inspect \
  --run-root /tmp/flowness-open-alpha-demo
```

包声明支持 Python 3.11+；Python 3.12 是已保留完整发布证据的坐标。成功检查后会看到：

```json
{"state":"verified","producer_agents":3,"round_1":"blocked","targeted_rework":"verified","round_2":"accepted"}
```

默认演示不需要模型账号。可选的 Codex CLI producer 模式见
[演示指南](oss/flowness-oss-harness/docs/open-alpha-demo.md)。

## 今天真正开源了什么

Flowness 正在分层开放。当前仓库让验收内核可运行，同时公开事件、投影、编排、
复查、恢复、锁和 worktree 等部分机制。

| 能力 | 当前公开状态 |
| --- | --- |
| FAIL → 定向返工 → 由新评委复验后 PASS 的演示 | Open Alpha 可运行 |
| 候选密封、评委隔离、问题与返工链路、trace 检查 | 可运行 / experimental |
| Ledger Core：追加式判断、投影新鲜度、终态 verdict、有限尾部恢复 | experimental 窄核心 |
| 部分编排、复查、恢复、锁和 worktree 机制 | 可审阅的实验性源码 |
| 采访 → 设计 → 工程方案 → 共识认知流水线 | 本文给出生命周期模型和当前缺口。私有运行时已有主要对象、CLI、门、测试和部分路线；自动分级及“工程方案 → 共识”发布链尚未闭环，没有真实任务端到端证据，也不在公开可运行切片中 |
| 生产账号、凭据、私有 Transcript、服务器和 fleet 控制 | 不在公开源码内 |

这个边界很重要：上面的图说明 Flowness 正在建造的完整产品；当前 demo 只证明它
真正运行的公开切片。下一项最有价值的开源里程碑，不是再做一个合成 jury demo，
而是开放并跑通一个“模糊目标 → 设计/工程方案 → 可验收结果”的真实案例。

## 按你的时间选择入口

- **只有 10 分钟：**运行 [FAIL → 返工 → PASS 演示](oss/flowness-oss-harness/docs/open-alpha-demo.md)。
- **想看全貌：**阅读最新的 [架构总览](docs/architecture.zh-CN.md)。
- **想看写代码之前发生什么：**阅读 [设计环 × 工程方案环](docs/design-engineering-rings.zh-CN.md)。
- **想挑战我们的说法：**检查 [Mechanism Registry](oss/flowness-oss-harness/registries/mechanism-registry-seed-v0.json)、[Drift Atlas](oss/flowness-oss-harness/docs/drift-atlas-seed-v0.md) 和 [benchmark protocol](oss/flowness-oss-harness/docs/benchmark-protocol.md)。
- **想下钻 Alpha 底层机制：**打开 [D0–D9 架构图集](oss/flowness-oss-harness/docs/architecture-atlas.md)。
- **用过 Wow-Harness：**从 [v0 → v1 迁移说明](MIGRATION.md) 开始。

## 递进架构

- **D0–D2：**问题、目标到结果的旅程、完整生命周期；
- **D3–D5：**控制、执行、证据、安全、机制族和运行时序；
- **D6–D8：**部署与故障域、权限边界、provenance；
- **D9：**当前、experimental、设计目标和外部边界。

最新的 [架构总览](docs/architecture.zh-CN.md) 是产品入口，并明确分开完整生命周期模型、
私有部分接线阶段和公开可运行切片。较早的 D0–D9 Atlas 仍适合查看 Open Alpha 底层机制
证据，但它的 D1/D2 早于设计环和工程方案环重建，不能再作为完整生命周期的主图。

## 仓库地图

- `oss/flowness-oss-harness/`：可运行的 Open Alpha 包、demo、测试与证据契约；
- `harness/`：部分实验性编排、复查、恢复和运行时机制；
- `public-core/flowness-ledger-core/`：窄范围 Ledger Core 包；
- `docs/`：面向读者的架构与案例；
- `MIGRATION.md`：Wow-Harness v0 到 Flowness v1 的历史与迁移边界；
- `LICENSE-MATRIX.md`：版本和素材许可证说明。

## 从 Wow-Harness 到 Flowness

Flowness 是 Wow-Harness 的完全重构式 major-version 演进，不是借用旧历史的无关项目。
仓库保留了 v0 历史、legacy tag、贡献者、issues、stars 与 forks；它们说明项目演进历史，
但不能被解释成对重写后 v1 实现的验证。

## 成熟度与边界

`v1.0.0-alpha` 是 Open Alpha：可以运行、检查和挑战，但不代表生产可靠性、规模、
benchmark 领先、安全硬化或外部采用已经成立。架构节点和公开 claim 会区分 current、
experimental、designed target、blocked 与 unknown，避免把未来设计写成现有能力。

公开树不包含凭据、客户材料、原始 Transcript、私有运行账本和服务器/fleet 控制。
详见 [公开范围](oss/flowness-oss-harness/docs/open-alpha-package-scope-v0.md)、
[安全策略](SECURITY.md) 与 [许可证矩阵](LICENSE-MATRIX.md)。

## 贡献

当前最有价值的贡献不是更多 slogan 或 framework wrapper，而是可复现真实案例、
对抗性 eval、断裂的证据边、清晰的解释、clean-room 安装结果，以及真正通过独立复查
的小机制。

见 [CONTRIBUTING.md](CONTRIBUTING.md)、[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
和 [SECURITY.md](SECURITY.md)。
