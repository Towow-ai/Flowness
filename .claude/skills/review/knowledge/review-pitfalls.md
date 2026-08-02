# Review Pitfalls — 失败模式有名字

> 用途：review skill 知道哪些坑要避。每个失败模式有名字（不是抽象警告）。
> 归属：M-1.5 review skill 知识库 ★ 核心文档
> 来源：Nature 直接给的失败模式 + v3.1 实证数据症状（75 份旧 review 数据）+ M-1.4 教训迁移

---

## Nature 给的头号失败模式

### 1. 制造假问题（最痛的点）★★★★★

> Historical calibration: a reviewer can invent plausible but irrelevant
> problems and drive unnecessary implementation away from the original goal.

**症状**：reviewer 漫游找空子，提一堆抽象的"应该有 X / 应该考虑 Y"——但 author 按 finding 修了之后，任务完成度没改善。

**对治**：每个 finding 必须过 **VoI 测试**——"如果 author 按这个 finding 修复，任务完成度会改善多少？" 不改善 → 丢弃。

**判别尺**：
- Bad: "这个 demo 没有并发"（demo 不需要 → VoI = 0）
- Good: "用户明确说了长期发展，现在预留并发接口比后续重做便宜"（有具体 context → VoI > 0）

### 2. 偏离原始目的

> Nature："并没有让你更好的实现这个东西，反而让你实现了一大堆的其他的东西，结果没有实现最初的目的"

**症状**：reviewer 关注的方向跟 task 原始 intent 无关——找代码质量小毛病，但设计文稿明确要求的功能反而没检查有没有做。

**对治**：以 done_criteria + brief.completion_condition + active_obligations 为锚——每个 finding 必须明确指向**这些原始 intent 的某一条**或**它的隐含约束**。

**判别尺**：finding 描述里能不能找到"这件事跟 spec 第 X 条 / 隐含约束 Y 的关系"？能 → ok；不能 → 漫游。

### 3. 找代码质量小毛病但漏检设计要求

> Nature reflection（through external research）：reviewer 一直把"做了该做的吗"（spec compliance）和"做得好不好"（code quality）混在一起——结果去找一堆 quality 小毛病，但 spec 要求没检查

**对治**：明确两阶段优先级——
- **优先级 1**：对照 done_criteria + obligations 检查 spec compliance
- **优先级 2**：才是 code quality（且必须过 VoI——纯 stylistic 的不应 raise）

### 4. 漫游 vs 对照原始目的

**症状**：reviewer 没有 explicit review_plan 时，自由发挥——什么都看一眼，什么都提一句。

**对治**：**必须有 review_plan**（来自设计阶段或 M-1.5 design-time mode 自己产）。reviewer 按图索骥——dimensions 已规定关注范围，VoI criteria 已规定什么 finding 是有效的。

---

## v3.1 数据症状（75 份旧 review 反向校准）

### 5. Multi-perspective 通胀（PLAN-090 30+ density）

**症状**：4 个视角并行（silent-failure / redteam / contract / business）—— 4 视角都是"看什么"的不同侧面，同方向 lens 堆叠，同根因被多视角分别报。PLAN-090-v2 r1 出现 30+ finding density——大部分重复。

**根因**：按"看什么"角色分（业务/安全/契约/红队），全是同方法论的同方向。

**对治**：F-08c 按"怎么看"方法论正交分（执行路径 / 内部一致性 / 红队对抗）——三种方法论根本不同 context-injection 配方，看到的东西不重叠。

### 6. 设计阶段 review 空跑（48% 仪式）

**症状**：旧 75 份数据 48% 设计阶段 review 是仪式空跑——既不抓真问题也不影响设计。

**根因**：设计阶段没明确 review 标准（review_plan 没产）→ reviewer 不知道在评什么。

**对治**：design-time review_plan_creator mode——设计阶段就产 review_plan（含 dimensions + voi_criteria）。后续 review 按这个 plan 跑，不是凭感觉。

### 7. Reviewer 越界 = 高质量（35% paradox）

**症状**：旧数据 35% reviewer 在合同外越界且**越界 = 高质量**。

**这是反直觉的真相**——严格 boundary 让 reviewer 失去抓真问题的能力，但完全无 boundary 让 reviewer 制造假问题。

**对治**：boundary 分两层——
- **方法论 boundary 严**（什么 lens 看——F-08c 三个方法论）
- **探查对象 boundary 松**（看什么——allowed: diff + adjacent code + concept graph neighborhood + obligation full neighborhood）

Reviewer 用方法论 lens 自由探查相关对象——既不漫游也不死板。

### 8. 0/75 读 proposals/inbox（过程足迹位置错位）

**症状**：旧 75 份 review 中 0 份读了 proposals/inbox——reviewer 没看过 task 形成的过程足迹。

**根因**：reviewer capsule 没注入历史 design context。

**对治**：M-0.3 §6.6 review template additional_event_reads 包含 PatchProposed / TaskRunCompleted / FindingCreated（历史 finding）。capsule assembly 时显式注入。

---

## 神谕化失败模式（双向）

### 9. Reviewer 神谕化（reviewer finding 当圣旨）

**症状**：author 看到 finding 就 "reviewer 说了我必须改"——失去自主判断。

**对治**：Author 反驳权——author 可以拒绝 finding，但必须写反驳理由留痕。

### 10. Author 神谕化（author 无视所有 finding）

**症状**：author 一律 dispute——"我是作者我说了算"，无视 reviewer 提的真问题。

**对治**：dispute supersede 走 M-0.5 NoveltyCheck——必须带 new_evidence / new_interpretation / new_patch / new_risk_assessment，否则 commit gate 拒。

**对称机制**：reviewer 不神谕化 author，author 也不神谕化 reviewer——都需要 novelty 支撑。

### 11. Reviewer 想越权改代码（V-02 防线）

**症状**：reviewer 看到 bug 想"我改一下试试"——从评估者变成再次执行者。

**对治**：V-02 物理隔离——reviewer fork 工具白名单只含 Read/Grep/Glob/Bash，schema-level 不可绕过。

---

## Finding 质量失败模式

### 12. Finding 不可定位（author 不知道改哪）

**症状**：finding 描述是 "X 有问题" / "Y 应该改进"——但没说改哪个文件 / 哪一层 / 怎么验证修对了。

**对治**：Finding schema 强制含——
- `target.artifact` + `target.location`（哪里）
- `suggested_fix_layer`（哪一层：code / sql / hook / config / concept / obligation / spec）
- `voi_rationale`（为什么过 VoI——能让 author 改善什么）

### 13. Verify-step over-filter（把真 bug 当假阳性丢）

**症状**：verify-step 过严——执行路径模拟没复现就标 false positive，但实际是 trigger condition 比测试更复杂。

**对治**：verify-step 输出三态（not 只是 verified / rejected）——
- `verified` —— 跑通模拟，confirmed
- `rejected_false_positive` —— 模拟无法复现，且证据足够
- `unverified_inconclusive` —— 模拟无法复现但不能排除——保留给 author 自己判断

### 14. Verify-step under-filter（不过滤假阳性）

**症状**：verify-step 走过场——所有 finding 都标 verified。

**对治**：verify-step 是独立 fork（独立性是核心价值）——不是方法论 fork 内嵌的自检（利益冲突）。

### 15. Severity 全标 critical（通胀）

**症状**：reviewer 把每个 finding 都标 critical / major——signal lost。

**对治**：Severity 校准（参考 Anthropic 数据）——
- red (critical)：会让任务无法完成 / 严重违反 obligation
- yellow (major)：会让任务部分失败 / 显著降低质量
- purple (preexisting)：跟 PR 不直接相关但相关 adjacent code 的 latent bug（不算 author scope creep）
- minor / observation：纯 quality 建议

跨方法论 finding 同根因 → aggregation subagent 去重 + 取最高 severity。

---

## Review Plan 失败模式

### 16. Review_plan 没产（设计阶段漏了）

**症状**：M-1.2 工程共识 freeze 后没产 review_plan → reviewer 拿不到 dimensions / voi_criteria → 漫游。

**对治**：M-1.5 design-time review_plan_creator 在工程共识 freeze 后**自动触发**（orchestrator 调用）——确保 review_plan 总是存在。

### 17. Review_plan VoI criteria 太泛（过 VoI 但还是漫游）

**症状**：voi_criteria 写"任何改善都算"——等于没限制。

**对治**：voi_criteria 必须**具体化到 task 内禀**——结合 task context（demo / prod / 长期 / 短期 / 用户群 / 等）+ 隐含约束。Meta-review fork 审 voi_criteria 是否具体。

### 18. 风险面识别不全（dimensions 缺）

**症状**：review_plan 只跑某个 dimension，漏了某个风险面对应的 dimension（如改 SQL 没触发数据持久化的一致性审计）。

**对治**：F-08b 双保险——
- 自动 detection（file path patterns + diff 模式触发）
- author 自报
- 取并集决定最终 dimensions

---

## Meta-review 失败模式

### 19. Review_plan 自身有缺陷但没人审

**症状**：review_plan 写得不好（dimensions 漏 / voi_criteria 泛 / 历史失败模式没喂入）—— 但没人审 review_plan 本身。

**对治**：F-08g Meta-review fork——独立 fork 审 review_plan 够不够（用 named error patterns + 历史比对）。

### 20. Meta-review 也通胀

**症状**：meta-review 提一堆 "review_plan 应该再加 X / Y / Z"—— 自己也变漫游。

**对治**：meta-review 的产出本身也过 VoI——"加这条 dimension 能抓到本次设计的真问题吗？" 不能 → 丢弃。

---

## 反驳收敛失败模式

### 21. Dispute 无意义振荡（A→A'→A→A'）

**症状**：reviewer 提 finding → author dispute → reviewer 维持 → author 再 dispute → 重复 N 轮没新信息。

**对治**：D novelty-gated dispute loop——每轮 dispute 必须带 new_evidence / new_interpretation / new_patch / new_risk_assessment / no_new_information=true。`no_new_information=true` 时 commit gate 拒，强制终止：
- reviewer withdraws finding
- author accepts / fixes
- escalate Nature 仲裁

---

## Closure 失败模式（M-1.5b v2.1.2 新增）

### 22. Finding 是 criticism 不是合约（元层失败 ★★★★★）

**症状**：reviewer 给出"X 有问题"+ target.location + suggested_fix_layer——但**没规定"修到什么程度算闭合"**。Author 修了主位置 → 漏了引用位置 → 第二轮 review 又找到 ripple → 反复 review。

**根因**：Finding schema 停留在描述层（criticism），没升合约层（closure_contract）——跟 v3 体系里其他产出（brief.completion_condition / task 自包含 / envelope 合约 / obligation lifecycle / novelty-gated）不同构。

**对治**：Finding schema 加 closure_contract（M-1.5b v2.1.2 Patch g）——
- `closure_criteria`：必填，含 verification_method enum（grep/schema_check/projection_check/manual_reasoning/test/replay）+ expected_result
- `ripple_targets`：必填（除非无 ripple）——修主位置时必须同步更新的相关位置
- `forbidden_residuals`：什么"残留模式"说明没修干净（如旧术语 grep 应 0 occurrences）

每个 finding 不只是问题描述，是可执行修复合约——回答"为什么这值得修 / 修哪里 / 改哪一层 / 怎么证明修好了 / 哪些相关位置必须同步 / 什么残留说明没修干净 / 第二轮只检查什么"。

### 23. Cleanup Ripple Incomplete（v2.1.1 second-round verification 实战发现 ★★★★）

**症状**："此处改了彼处忘了"在 review 自己 cleanup 时的复现——主 section 修了 cleanup patch，但引用主 section 的其他 sections / 附录 / 表格没同步更新，留下不一致的旧表述。

**v2.1.1 实战案例**（5 处 ripple——成为这个失败模式的具体证据）：
- §6 头部澄清了 "aggregation 是 subagent 不是 fork"——但 §6.4 / §6.5 内部还有 "aggregation fork" 旧表述
- §2.1 caller_params 改成双字段——但 §9 / 附录 B.3 / 附录 C.3 仍说单字段
- §3.1 加了 batch_info schema——但 §7.1 design-time procedure 没说每 batch 触发
- §6.5 加了 verify-step 路径 A/B——但 §7.3 fix-after procedure 没明说调用路径 B

**根因（双层）**：
- 浅层：cleanup 执行者只关注主修改点，没做 cross-doc consistency sweep
- 深层（元层）：finding 没有 closure_contract.ripple_targets——执行者不知道应该 sweep 哪些位置

**对治**：
- **Finding-level**：finding.closure_contract.ripple_targets 必填——明确"修哪里同步更新哪些位置"
- **Fix-after mode**：bounded verification（M-1.5b v2.1.2 Patch h）—— 按 closure_contract.ripple_targets 验证 sync_status；任一 ripple_target 未 synced → closure_state=ripple_incomplete
- **Cleanup 协议**：每次 narrow cleanup 完成时主 session 跑 cross-doc consistency sweep（自带 ripple-aware）；envelope.self_check 含 `cleanup.ripple_sweep_complete` blocking check

### 24. Fix-after mode 漫游找新问题（违反"第二轮不引入新问题"判据）

**症状**：fix-after mode 没限定 scope——verify-step fork 不只验"原 finding 解决了吗"，还自由发散找全新问题——反复 review 复现。

**对治**：fix-after mode = **bounded closure verification + ripple scan**（M-1.5b v2.1.2 Patch h）—— 严格按 finding.closure_contract 验证，**scope 外发现的新问题默认 new_unrelated_finding_logged 不阻塞当前 closure**（除非 red-line / data loss / correctness-critical）。

类比软件工程 regression test——不是重新测试整个宇宙，是验证 bug fix 真的解决了原 bug + 相关模块没有引入新失败。

### 25. ReviewClosureState 状态不显式（cycle 终止条件不清）

**症状**：finding 走 created → verified → resolved 状态机——但 resolved 后没区分 "全部 closure_criteria 满足" vs "只是 author 觉得修了"——反复 review 找到漏的。

**对治**：FindingResolved.closure_verification.closure_state 显式（M-1.5b v2.1.2 Patch i）——
- `closed`：全部 closure_criteria 满足 + ripple_targets 同步 + forbidden_residuals 0 occurrences【终态】
- `fix_insufficient`：原 finding 没修好——reopen
- `ripple_incomplete`：closure_criteria 满足但 ripple_targets 未全部同步——reopen 但 scope bounded 到 ripple_targets
- `new_unrelated_finding_logged`：发现 scope 外新问题——logged 到 backlog 不阻塞当前 closure【终态】

**这就是反复 review 的工程化终止判据**——novelty-gated 收敛在 dispute 层（对话振荡），closure_contract 收敛在 fix 层（修复完整性）；两者一起完整 model review cycle 的所有收敛点。

---

## 期望性格（M-1.4 教训迁移）

**应该是的**：
- **聚焦**——按 review_plan 跑，不漫游
- **falsification 精神**——尝试 disprove patch，不是证明它对
- **诚实**——VoI 不过的 finding 自己丢弃，不上报
- **谦逊**——author 反驳权是合法的；不神谕化自己
- **善良感**——finding 写得让 author 能直接 follow（含 target + suggested_fix_layer）

**不要变成的**：
- **漫游者**——什么都看一眼什么都提
- **完美主义者**——追求 0 bug 不追求 continuous improvement
- **争论狂**——dispute 无 novelty 还坚持
- **神谕**——以为 finding 一出就要被采纳
- **越权**——想"我改一下试试"

---

## 自检尺

每个 finding 提交前 reviewer fork 必须问自己：

1. **"如果 author 按这个 finding 修复，任务完成度会改善多少？"** 0 → 丢弃；>0 → 提
2. **"这个 finding 指向哪个 spec / obligation / 隐含约束？"** 不能指向 → 漫游，丢弃
3. **"author 能从我的 finding 描述里知道改哪个文件 / 哪一层吗？"** 不能 → 补 target + suggested_fix_layer
4. **"我是不是被 review_plan VoI criteria 之外的东西吸引了？"** 是 → 偏离了，丢弃
5. **"这个 finding 是我尝试 disprove patch 找到的，还是凭抽象想象？"** 后者 → 没基础，丢弃

---

## 元失败模式：自己变成漫游 reviewer 而不知道

最危险的失败模式——reviewer 自我感觉"我在好好做 review"，但实际上在制造假问题。

**自检**：如果一次 review 产出 30+ findings 且大部分跟 spec 无直接关联——你已经在漫游。停下来，回去看 review_plan。
