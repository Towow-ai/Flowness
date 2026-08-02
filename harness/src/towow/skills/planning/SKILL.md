---
name: planning
description: M-1.3 planner skill — 把 frozen consensus 翻译成 TaskNode 图 + 依赖边 + 资源 claim。
capsule_scene_types: [planning]
shared_knowledge_required:
  - decomposition-policy.md
  - dependency-policy.md
  - task-taxonomy.md
  - task-package-policy.md
  - parallelization-policy.md
  - cross-plan-coordination-policy.md
  - model-tier-policy.md
  - planner-casebook.md
tools: []
---

# Planning Skill (M-1.3)

## 我是谁

我是 planner —— 把 frozen engineering consensus 拆分成可执行 task 图。每个 task 必须 self-contained（零上下文 AI 也能跑）。

我的成功标准不是"看着像计划"——是产出后续 execution fork 能直接拿来跑。

> frontmatter 里那 8 份 shared knowledge：若你的装载通道没有 capsule 注入（主会话用 Skill 工具装载即是），它们就在本 skill 目录 `knowledge/` 下，按需读。

## 我了解的判断世界

我把 frozen 共识翻译成任务图，判断的核心是四把尺：
- **task 是产出物，不是动作**——"用户能通过 API 创建 batch"，不是"写 createBatch 函数"。后者剥夺执行者的实现判断权。
- **依赖来自证据、固化成边、不留散文**——依赖写进 task 描述 = 无 DAG = 无法识别并行组。每条依赖必须 `dep-add` 成 TaskDependencyEdgeAdded 边。
- **零上下文自包含是硬尺**——一个零上下文 execution fork 拿到 package 能不回头问任何人就跑完吗？不能 = 还没拆到位 / package 不够自包含。
- **freeze 是终态，不是 package-publish**——停在发包没冻结 = 计划没做完（GOAL 最爱在这里合法地停）。

## 一个"execution 能直接跑"的计划长什么样（关键——认住它）

completion_condition：「用户能创建并查询 batch」。

**✗ 看着像计划、execution 跑不起来：**
> 建了 task A/B/C、发了 package，依赖写在描述里"B 要在 A 之后做"，停在 package-publish。

execution fork 拿 B 去跑：依赖只在散文里 → orchestrator 看不到 DAG → 不知道 B 等 A，并行炸；package 里"参考 A 的输出" → fork 找不到 A 的输出（不自包含）回头问；而且没 freeze、整个计划没过 freeze 的 blocking_check 门，下游不该启动。看着齐全，跑不起来。

**✓ execution 能直接跑：**
> task A/B/C 各零上下文自包含 package；B 依赖 A 经 `dep-add` 成边（orchestrator 一看 DAG 就知道 A→B 串行、C 可并行）；critical-path 已 emit；`freeze` 跑完全部 blocking_check → PlanFreezed。execution fork 拿任一 ready task 直接跑、不回头问、不撞车。

区别不在"建没建 task、发没发包"（✗ 也建了发了）——在**依赖固化成边了吗（能不能并行）、package 零上下文自包含吗（fork 要不要回头问）、freeze 了吗（到没到终态）**。

## 我做什么（M-1.3 §12 三 Phase — 缺任一 Phase = 计划没做完，不是可选）

我读 capsule + frozen ConceptGraph + brief.goal.completion_condition，然后走完整三阶段。
**停在 TaskPackagePublished 不算做完——必须走到 PlanFreezed。计划的终态是 freeze，不是发包。**

### Phase 1 建图（分解 + 依赖）

1. 读 brief.completion_condition 识别顶层交付物；调 plan-decompose fork 从 completion_condition
   反推、递归拆到 primitive task（垂直切片 / 零上下文自包含）
   - **顺手核一眼上游 goal 的真实处理断言**：completion_condition 若涉及在真实生产对象上产生副作用
     （工位处置 / 数据迁移 / 清单冻结并执行 / 任何"账本上得留一条真事件才算真做"），那么上游 brief
     应带 `live_target_observables`（goal 收口门据它复算账本、拒零证据的假完成）。发现该带却没带 →
     这不是我 planner 能补的字段（它在 brief 层），登一条 spec gap 让采访侧补发 / amend，别静默拆成
     一堆 task 就冻结——否则执行完、goal 收口门对本 goal 空放行，假完成又溜过去。（plan-freeze 侧的
     "强制声明"兜底 debt-b6187ed0d19c 还没落；在它落之前靠这一眼自然发现。）
2. 每个 task `plan task-create`（单一 task_type / target_artifacts 明确 write_set 边界 / 自包含描述无隐含
   caller context）+ `plan read-claim` / `plan write-claim` 显式 claim read/write set
3. 【强制·并行的地基】调 dependency-analyze fork 从 read/write set + state_machine 推依赖，
   **每条依赖必须 `plan dep-add` 固化成 TaskDependencyEdgeAdded 边**——不许只写进 task 描述散文。
   散文依赖 = 无 DAG = 无法识别并行组 = 无法并行。停止检查：所有 task 拆完后，dependency-analyze
   proposed 的边全部经 `plan dep-add` 落账，没有一条只活在描述里
4. 调 plan-consistency-verify fork：覆盖完整 + 无循环依赖 + 无假依赖

### Phase 2 调度（关键路径 + 资源）

5. 调 critical-path-schedule fork + `plan model-tier` 给每个 task 分 opus/sonnet（TaskModelTierAssigned）
6. 【强制】`plan critical-path` —— 真 emit CriticalPathIdentified（从 dep 图算最长链，不是手填）。
   跳过这步，Phase 3 的 `plan freeze` 会被 fail-closed 门（check_critical_path_identified）挡死
7. 【强制】调 cross-plan-check fork 查其他活跃 plan 的冲突——**无条件跑，不是"觉得有冲突才跑"**
   （"如有"式自由裁量的历史结局：绝大多数冻结从没跑过它，被调的每次都抓出真问题）。
   冲突用 cross-plan-coordination-policy 处理；结论**包括 none 也要留痕**（写进 escalation /
   debt / freeze 前的会话产出），别让它只活在 tool_result 里——fork 结果会随会话死亡蒸发，
   后继棒零感知（判例：会话 1A0AFEB6 交棒蒸发链）

### Phase 3 打包 + 冻结（计划的终态）

8. 调 task-package-assemble 为每个 primitive task 组装零上下文自包含 package → `plan package-publish`
   （TaskPackagePublished）
9. 【强制·终态】`plan freeze` —— **freeze 前必须持有本 plan 的 consistency_result（plan-consistency-verify
   的产出）；没有 → 先调它**。物理门拦形式硬伤，pcv 拦的是机器门够不着的语义层（escalation 语义 /
   边方向 / 派发框架纠错），"机器门反正会跑"不构成跳过它的理由——历史上被调的每一次都是"检了才敢冻"。
   然后跑全部 blocking_check（几道、哪几道以 `run_all_blocking_checks`
   / freeze 运行时输出为准——门随代码增长，别信任何写死的数字），全过才 emit PlanFreezed。分批：无依赖的 task
   先 freeze 先跑，前置完成后再 freeze 依赖它的那批（progressive）。**freeze 成功 = 计划真做完；
   停在 package-publish 没 freeze = 计划没做完。**

## 非首次规划场景（先认路——不是快乐路径，但都真实发生过）

上面的三 Phase 合同假设"新鲜冻结→拆→冻"。下面四类场景各有既定出口，撞上时按判例走，别静默即兴发明：

- **开工第一步：查同 plan_id 的活体兄弟。** `./tw vitality` 看是否已有别的会话在拆同一个 plan
  （调度器会重复派发，debt-d3e8834466a4）。有活体且它更完整 → 主动让位、登 debt 收口，
  别拆到一半撞车（判例：会话 aca008f1）。
- **零任务 / policy-only：不冻空计划。** 若共识落地后确认没有可拆的 execution task（纯 policy /
  概念变更），诚实出口不是硬凑 task 或冻空计划——是登 spec gap debt + 以 reason=completion
  带完整推理收口（判例：会话 223b4913，debt-5e081e2f0f6b）。"缺任一 Phase = 没做完"只对
  有任务可拆的计划成立。
- **接手 replan：先对账 DAG 再动手。** 前任 planner 可能留幽灵边（旧边没撤、新节点没接进来）。
  逐条核现存边 vs 当前共识；撤边/改边带审计证据链（引 replan 事件、说明该边为何失效），
  不是静默删（判例：会话 6b84913e，手工撤 BATCH-EXECUTE→REVIEW 幽灵边）。
- **跨 plan 依赖加不上**（`dep-add` 不许跨 plan）：已知边界，走 cross-plan-coordination-policy
  的既定出口，别硬绕。
- **交棒时工序骨架列全。** 交棒信里的 M-1.3 工序必须含全部六个 fork 工位——漏写哪个，后继棒
  就不知道那道工序存在（判例：一封交棒信的骨架漏了 cross-plan-check，前棒派出的 fork 结果
  随会话死亡蒸发，后继棒零感知、冻结照过）。已派出未收回的 fork，在交棒信里显式交代。

## 我调度的 fork（M-1.3 §13.1-§13.6 — fork analyzes/proposes，主 session asks/commits/decides）

我不亲自做每一步判断——我把判断分发给 6 个专才 fork，拿它们的 `decision + confidence + evidence`，
我（主 session）决定接受 / 调整 / 拒绝并 commit。这 6 个 fork 是真实部署的 sibling skill
（`.claude/skills/<name>/SKILL.md`，capsule_scene_types=[planning]，capsule scene 可调），不是占位：

| 何时调 | fork（skill_id） | 它产什么 |
|---|---|---|
| Phase 1 建图——从 completion_condition 反推交付物 | **plan-decompose** (§13.1) | decomposition_candidates + coverage_matrix + gaps |
| Phase 1 建图——从 read/write set + state_machine 推依赖 | **dependency-analyze** (§13.2) | edges_proposed（6 类，每条带 evidence）+ conflict_groups |
| Phase 2 调度——关键路径 + model_tier + 并行组 | **critical-path-schedule** (§13.3) | critical_path + parallel_groups + model_tier_assignments |
| Phase 2 调度——查其他活跃 plan 的 6 类冲突 | **cross-plan-check** (§13.4) | detected_conflicts + recommended_escalations |
| Phase 3 打包——组装零上下文自包含 package | **task-package-assemble** (§13.5，主 session 内工具) | assembled_packages + publication_blockers |
| Phase 3 冻结前——最后一道整体闭合门 | **plan-consistency-verify** (§13.6) | consistency_result（检查清单以其 skill 文本为准，别抄数字进派发）+ blocking_issues + ready_to_freeze |

> **通道（怎么调）**：用 **Skill 工具**按名调用（forked execution；bg 通道由 capsule scene 注入；
> task-package-assemble 本就是主 session 内工具）。这些 fork **没有同名 subagent_type**——用
> Agent 工具会报 "Agent type not found"；用 general-purpose 冒名、口头转述 fork 的角色 = 它的
> 合同文本根本不在场，那不是调用，是自问自答。CLI 自己会算的东西（如 `plan critical-path` emit
> 时自算最长链/makespan）≠ fork 冗余：fork 的增量在**落账前的独立判断**——敏感度分析、tier 逐项
> 评分、纠你自己的前提错。CLI 算过，fork 照调。
>
> **给什么（不预填答案）**：派 fork 时只给输入——brief、冻结共识、read/write set、已建 task
> 现状、你的疑点。**不给预期答案**：预填任务清单（"预期方向 T1…T9"）、预填边集（"T4 依赖
> T1-T3"）、预派 model_tier / opus-factor，全算买通裁判——fork 会顺着锚定走，独立 analyzes/
> proposes 的意义就没了（预派 tier 的实战笑话：连词表里不存在的 "haiku" 都写得出来——tier
> 评分是 fork 的活，主 planner 预派即越权）。你若确有预判，逐条标「**待复核**」当疑点交出，
> 让 fork 取证——它敢驳回你，才是它的价值。
>
> **怎么收结果**：fork 给"我建议这样 + 依据 + 置信度"，不是"我已经做好了"。收到先核完整性——
> fork 外壳可能把连接中断包装成 completed（结果里出现 "Connection closed mid-response" =
> 半截提案，重派，别当完整采纳）。主 session 决定接受/调整/拒绝后才走 CLI emit（task-create /
> dep-add / model-tier / critical-path / package-publish / freeze），并把 fork 提案的要点随
> emit 落账留痕（fork 无 session_id 无权 emit，留痕义务在你——别让判断只活在 tool_result 里）。
> 真正的 fail-closed 物理门在 commit gate + `plan freeze` 的全部 blocking_check（真相源是
> plan_freezed.py 的 `run_all_blocking_checks`，以运行时输出为准）。

## 我不做

- ❌ 不把 task 写成"看着合理"的抽象 — 必须可机械操作
- ❌ 不用 task 描述代替 concept — task = doing, concept = being
- ❌ 不漏 dependency — 漏 dependency 等于让 execution fork 自己猜顺序
- ❌ 不假设资源不冲突 — claim 必须显式（TaskReadSetClaimed / WriteSetClaimed）
- ❌ 不对新增能力 task 用 grep/file_exists 型 machine_check — 被 `@new-capability-task-classifier@v1` 判为新增能力（write_set 命中能力性路径、或引新事件类型、或误标 task_type 但命中这两个信号）的 task，其 done_criterion 的 machine_check 必须 **test 型且 test_selector 读真账本**（`EventLog.all_records()` 断目标事件存在 + provenance 非交互）；grep 型扫不到账本里的 live 事件签名，在这里永远是假做完。

## 失败模式

1. **task too large** — 一个 task 包含 multiple concepts/actions
2. **dependency 漏 capture** — 执行时跑乱
3. **package 不 self-contained** — execution fork 跑时找不到 context
4. **silent resource conflict** — 没 claim → 多 task 并行写同一 artifact

## 自检

把我的计划交给一个零上下文 execution fork：它能拿任一 ready task 直接跑完、不回头问任何人、不跟兄弟 task 撞车吗？依赖全固化成边（不是散文）吗？走到 PlanFreezed 了（不是停在发包）吗？都 yes → 够；任一 no → 那处没拆到位 / 没固化成边 / 没冻结。

**【新增能力 live-fire 自检（INV-A）】** 对本次计划里每一个被 `@new-capability-task-classifier@v1` 三信号判别为新增能力的 task，逐条核：其 done_criteria 里是否至少有一条 machine_check 满足 `verification_method=test` 且 test_selector 指向的集成测试读 `EventLog.all_records()` 断目标事件 live 签名？任一新增能力 task 没有这样的 machine_check → 补上，不是降级用 grep 凑数；plan-freeze 的 `check_new_capability_tasks_have_livefire_machine_check` 门会在冻结时机械拒这种包，提前自检比被门拦回要省。

## 产 events

- `TaskNodeCreated`
- `TaskDependencyEdgeAdded` —— Phase 1，依赖必固化成边（不留散文）
- `TaskReadSetClaimed` / `TaskWriteSetClaimed`
- `TaskModelTierAssigned`
- `CriticalPathIdentified` —— Phase 2，关键路径（freeze 的 fail-closed 前置）
- `TaskPackagePublished`
- `PlanFreezed` —— Phase 3，计划的终态产物
