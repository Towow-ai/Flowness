# Harness 机制地图 —— 修治理系统自己时装这张图

> R07 治理环路 (plan-r07-govloop) 知识包。当 fix agent 收到一条治理 finding
> (`efficiency_regression` / `system_governance_defect`)、要去**修 harness 系统自己**的代码时，
> 装这张图：它告诉你这套系统怎么转、改哪里安全、哪条线碰不得。普通业务修复不用读。

## 这套系统的骨架（改之前先认清自己活在什么里）
- **事件日志是唯一真相源**。一切状态从 `harness/.towow/events/`（base `events.log` + `hot/*.jsonl` 多段）里的不可变事件派生。**读事件必须跨全部段**（`iter_raw_event_lines`），只读 base 会假阴性（dogfood 实证过的坑）。
- **投影是派生态**，从事件物化进 `.towow/graph/*.json`，由 L3 reducer 带 cursor 增量算。**例外**：`agent_efficiency` / `efficiency_boundary` 投影派生自 transcript（非账本、非 SoT），由 daemon 直接物化。
- **改判断/共识/决策必须经 CLI emit 过 commit gate**，绝不 Write markdown 当真相（没 provenance = 没发生）。
- **门是分阶段的物理机制**：PreToolUse guard、commit gate、fail-closed self-check。改它们=改安全地基，极慎。

## 治理环路自己（你可能在修的对象）
- **观测层**：`l2/transcript_efficiency.py`（四真口径指标共享纯函数，**全文件读**，不复用 session_vitality 尾读）→ `l2/daemon_run_once.py::_scan_transcript_efficiency`（daemon 物化 `agent_efficiency` 投影）→ dashboard `serve.py`/`app.js` 上手效率视图。
- **越界检测**：`l2/efficiency_boundary_scan.py`（纯检测返 report，**emit-by-caller**：库层绝不 emit finding）→ `_scan_efficiency_boundary`（surface-only，`findings_produced=0`）。
- **非 headless fork**：`l1/verification_fork.py` 的 `ForkMode.BG_POLLED` + `poll_verdict_dropfile`（轮询 verdict 落盘 + 超时 fail-closed，**不用** assess_vitality）+ `bg_polled_runner_factory`。本环路任何 agent **禁 `claude -p`**。
- **专属 finding_kind**：`FindingKind.EFFICIENCY_REGRESSION` / `SYSTEM_GOVERNANCE_DEFECT`，路由表 `orchestrator._DISPATCH_TABLE_FINDING_KIND` 里默认 → "Nature dashboard"（surface），**不劫持 anomaly**。severity/shadow gating = `efficiency_boundary_scan.governance_finding_dispatch_decision`。

## 碰不得的红线（违反 = 把这套系统弄坏）
1. **fail-safe-closed**：所有 daemon 默认 OFF；阶段3 自动派修 gated on owner 显式解冻 INF-003。绝不擅自把治理 finding 路由切 "fix"。
2. **emit-by-caller**：FindingCreated 经 commit gate Path A；daemon 纯检测返 report，绝不直写账本。
3. **一条共享指标代码路径**：观测/检测/验观测共用 `transcript_efficiency` 一份，绝不造第二套。
4. **改 live 治理代码走 src 再 deploy**：改 skill/glue 走 `src/towow/`（skills 改了跑 `deploy_skills`），绝不直改 `.claude/`（PreToolUse guard 会判自修改 deny）。
5. **改完 daemon 不会自动重 import**：跑着的 daemon 用旧代码直到受控重启（kill 旧 + start 新）。修了 orchestrator/dispatch 要重启才生效。
6. **删账本物理门**：任何含 `.towow`/`events.log` 的删除命令被 T-FIX-B4-06 guard 拦——别"删掉重跑"，那是 2026-06-10 丢 6 天账本的病程。

## 修之前
- 先 `vitality` 判相关会话死活（别凭沉默猜）。
- 先读事件跨全段，别信单投影/单 base 文件。
- 改 src + 写测试 + 过 commit gate；自检独立（运动员不当裁判）。
