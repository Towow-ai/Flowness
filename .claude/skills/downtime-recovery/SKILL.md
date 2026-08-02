---
name: downtime-recovery
description: 停机后复工的标准安全流程（水位线追平/积压泄流/服务分批重启）。当系统经历过 daemon 停机、性能冲刺减负、事故停摆之后要恢复常驻服务时触发；即使 owner 只说"把服务开回来"、"复工"、"追平水位线"，也应触发。核心使命：绝不让"重启"变成"积压喷发"（2026-07-04 实锤：orchestrator 停机后水位线落后 3240 条，直接重启把机器负载打到 22+，owner 被迫立即紧急关停）。
---

# 停机复工（downtime-recovery）

## 我是谁

我是停机与复工之间的那道闸。系统停机（daemon 停摆/冲刺减负/事故）期间，账本还在被活跃会话写入、任务还在完工、升级还在堆积——**停机不是暂停，是欠债**。我的职责是：复工时先把债盘清楚，再按安全顺序泄流，让每个服务"温和接管"而不是"积压喷发"。

## 我相信什么

**重启不是恢复，是一次放大器。** 停机越久，水位线落得越远；直接重启，daemon 会把整段积压一口气处理掉——并发派发、并发抢锁、并发起会话，停机多久就炸多大（实锤：落后 3240 条 → load 22+ 秒炸；现在见过落后 22 万条的）。

**先测量，后动手。** 复工的第一动作永远是跑盘点（只读、零副作用），不是 launchctl load。不知道欠了多少债就还债，是赌博。

**一次一个，动完核实。** 每启动一个服务，先确认它的行为正常（日志/内存/负载）再动下一个。资源门看真信号：memory_pressure 等级 = normal 且换页速率 ≈0 且 load < 核数×1.5 才继续；不达标就停下等，不硬上。（macOS 的"空闲内存 MB"和 swap 占用量都是误导指标，别拿它们做依据——2026-07-05 内存诊断的教训。）

**积压的处理是决策，不是默认。** 22 万条积压里多数派发早已过期（对应任务可能已被别人做完）。"补派积压"还是"快进放弃、只管新事件"，是要看着报告做的判断——常常值得派一个排序 agent 专门研究，偶尔需要 owner 拍板（放弃积压=有任务永不被自动派发，属范围决定）。

**修好的代码不会自动进正在跑的旧进程。**（2026-07-04 教训）性能修复合并后，停机前启动的常驻进程内存里还是旧代码——复工清单必须包含"识别在跑旧代码的进程并重启之"，否则修了白修。

## 怎么做（六步 SOP）

### 第 1 步 · 盘点（只读）

```bash
python3 .claude/downtime-recovery/survey.py          # 人读（从仓库根运行）
python3 .claude/downtime-recovery/survey.py --json   # 喂排序 agent（从仓库根运行）
```

产出六组数字：A 各水位线落后量 / B 积压队列 / C 服务实况 vs 登记态 / D 卫生（stale 锁、死 pid 持有 commit.lock）/ E 在跑旧代码的进程 / F 当前资源水位。

（登记：survey.py 是单点脚本——只 track 在仓库根 `.claude/downtime-recovery/`，不随任何 skill 部署面分发；本 SKILL 三面与 RUNNING-SERVICES.md 顶部指针都指这同一个绝对路径。要挪它必须所有指针同改，owner 选向之前只登记、不挪。）

### 第 2 步 · 分诊排序（默认派 agent）

把 `--json` 报告喂给一个排序 agent（Sonnet 够用），让它交回：**安全复工顺序 + 每步理由 + 每步的验证方式 + 需要 owner 拍板的点**。派发信要点：报告全文 + 本 SKILL 的硬规则 + RUNNING-SERVICES.md 各服务登记（暂停原因/恢复命令/踩坑记录，尤其 §4 orchestrator 的 2026-07-04 积压喷发记录）。积压小（各水位线 behind < 几百、队列个位数）时可弃权自己排，但弃权留账：在第 6 步的复工记录里写明理由与当时的积压数字（埋掉的是一个独立的排序视角）。

### 第 3 步 · 卫生先行（低风险、腾地方）

- 死 pid 文件：python 内部 unlink（先 ps 验证进程真死；**绝不用 shell rm 碰 .towow 路径**——guard 会拦且拦得对）。
- stale 会话锁：用 `./tw plan/goal reap-stale-session` 系列（vitality 裁决），不手删。
- commit.lock 被死进程持有：新提交会经"诚实 holder 探测"（commit 50555a102）识破，一般无需手动；若探测未上线到在跑进程，按 RUNNING-SERVICES 处置。
- 在跑旧代码的进程（报告 E 组）：逐个按其登记的停/起方式重启（launchd 的 kickstart -k；非 launchd 的按登记命令）。

### 第 4 步 · 水位线决策（本 SOP 的核心判断）

⚠ 先记住一个代码实锤（orchestrator.py 主循环的 E.5 安全暂停块，搜 `is_orchestrator_paused`）：**paused 状态下 daemon 不 scan、不派发、也不推进水位线，纯 idle**——不存在"暂停着慢慢追平"这条路。真实可走的是：

- **快进放弃（fast-forward，大积压默认）**：daemon 停着时把 watermark.json 直接写到账本头（原子写，格式同 save_watermark_atomic）+ 留痕。已派未完的任务不会丢（dispatched/ 标记有独立于水位线的重扫通道）；丢的是积压区间里"从未派出的新触发"。⚠ 范围决定，需 owner 点头。
- **离线错过清单 + 手动补派（快进的兜底，两者配合用）**：派一次性 agent/脚本只读扫 [watermark, head] 区间，只提取会触发派发的事件类型，产出"错过的触发清单"给人审；重要的用 `orchestrator dispatch-one --while-paused` 逐个补派（这是官方支持的暂停期单发通道，orchestrator.py `dispatch_one_task` 的裁决注释明说"暂停期手动单发恰恰是合法核心场景"）。补完再快进水位线、起 daemon——此时积压=0，温和接管。
- **直接重启硬追（仅小积压）**：积压 < 几百条且机器资源门全绿时，直接起 daemon 让它自己扫完。超过千条禁用——就是 2026-07-04 的炸法。

投影水位线（graph/.cursor）例外：它每次提交都自动追平且已增量化（2026-07-04 批次合并修复），落后大时跑一次任意只读 CLI 即追平，无需决策。

### 第 5 步 · 服务分批重启（顺序固定，每步验证）

按"只读→事件驱动→会动手"的风险递增序：

1. **substrate-monitor**（只读巡检）→ 起后看一轮日志有 `⚑ scanned` 输出。
2. **feishu-gateway / feishu-adapter**（事件驱动，若停过）→ 起后 launchctl 有 PID。
3. **wake-watcher + watchdog / session-reattach**（会动手但有多重刹车）→ 起后各看一轮扫描日志无异常动作。
4. **orchestrator-daemon（最后，且必须先完成第 4 步的水位线决策）**→ 起后头 5 分钟盯 `orchestrator status` 与负载；派发数异常上冲立即 `pkill -9 -f "towow.cli.main orchestrator"`（连它派出的 dispatch 残留一起清，见 RUNNING-SERVICES §4 关停手法）。

**每步之间**：重跑一次 survey.py 看 F 组（memory_pressure / 换页速率 / load），不达标就等；服务行为异常就停在这一步排查，不带病继续。

### 第 5.5 步 · 审计 resume 保留的延迟派发队列（2026-07-11 实锤补步）

resume 的 T-FIX-COST-DROP 守卫会保留"暂停前已 deferred 的真 spawn 决策"（nonexec backlog marker）——但保留时**不检查该触发器的阶段产出是否已被别人消化**。实锤：brief-512d61a9 的共识触发器 7-3 被 deferred、当天已被另一会话冻结消化，7-10 复工重放白烧一个 Opus 共识席（debt-986ba644fc44）。复工起 daemon 之前（或之后第一时间）：逐个审 `orchestrator/nonexec_backlog/` 标记，按 dispatch_to 查阶段产出（consensus→已冻结？planning→PlanFreezed？fix→FindingResolved？）已存在的清掉留痕，别让 daemon 重放过期触发器。

### 第 6 步 · 复工验证与记录

- 跑一次 survey 对比前后（水位线应追平/快进、卫生项清零、服务实况=登记态）。
- session-reattach 的哨兵脉搏检查下一轮应全绿。
- 工位积压若有完工待 promote 的，按既有 promote 机制逐个处理（参照 01-reconciliation/perf-sprint/worktree-backlog-cleanup-2026-07-04.md 的死规则：有完工证据+测试绿才晋升、冲突留人裁）。
- RUNNING-SERVICES.md 状态行更新（🔴/🟡 → 🟢，注明复工时间与本次水位线决策）。

## 我不做什么

- 不在盘点前启动任何服务（先测量后动手是底线）。
- 不并发启动多个服务（一次一个，动完核实）。
- 不替 owner 决定"放弃大额积压"（范围决定，surface 给她选项+推荐）。
- 不用 shell 删除形命令碰 .towow 任何路径（python unlink + 既有 reap 机制）。

## 退出条件

- 系统本就在跑、无停机债 → 不需要我，别为了走流程而走流程。
- 单个服务的日常重启（无停机期积压）→ 直接按 RUNNING-SERVICES 该服务的登记命令即可。
