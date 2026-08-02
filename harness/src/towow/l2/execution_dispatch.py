"""F-07 执行编排器 — 把规划好的 task_graph 变成并行的多个执行 session.

# spec source:
#   02-meta-and-requirements/SYSTEM-REQUIREMENTS.md F-07 (L240): 系统必须能同时运行多个执行
#     session, 每个处理一个任务包.
#   08-knowledge-packs/m13-planner/parallelization-policy.md: 并行度 = min(DAG width,
#     可用 session, 模型配额); v3 初版 OPUS≤2 / SONNET≤5 / 总≤6; 关键路径优先; 高 fan-out
#     concept (>3 task read 且有 task write) 不并行。
#   04-l1-intelligence/M-1.4-execution-skill-detailed-design.md L63: 跨 task 协调归 orchestrator.
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md §7.4 trigger contract.
#
# Why this module (PLAN-FIX 第二波 T3 + PARALLEL-EXEC-FIX B4):
#   纠察实证: orchestrator 的 FORWARD_CHAIN 把 PlanFreezed→execution 接成一对一 —— 一个
#   PlanFreezed 只 spawn 一个 execution, 做一个 task 就转 review, 链断了: 既不并行, 连后续
#   task 都不自动推进. 本模块: 读 task_graph, 算 ready-set (前置依赖都满足的未完成 task),
#   让 orchestrator 对每个 ready task fan-out 一个 execution session。
#
#   【B4 注释勘误 (PARALLEL-EXEC-FIX-DESIGN)】本模块初版引 O-03 §9 "并行上限 spec 真空" 自证
#   "不限量"。该引据已 stale: parallelization-policy.md (更晚、更具体的 planner 知识包) 明确
#   给了帽 — 并行度 = min(DAG width, 可用 session, 模型配额), OPUS≤2/SONNET≤5/总≤6。
#   parallelization-policy 胜过 O-03 §9 open question。帽由 select_dispatch_batch 实现,
#   orchestrator 配 backlog re-scan (被帽截断的 task 每轮重算捞回, 防 watermark 单调推进下的
#   永久饿死 — 红队 fatal)。注意: 帽只限【同时工位数】, 绝不加链长/打转上限 (owner E.5 硬决策)。
#
# 纯函数设计 (易测 + 低风险): 本模块只读 events 算 ready-set/选批, 不 emit / 不 spawn / 不碰 lock.
"""

from __future__ import annotations

from towow.schemas.enums import TaskType
from towow.schemas.payloads.plan_freezed import (
    _after_state,
    _event_type,
    _plan_dep_graph,
    closed_task_ids,
    compute_critical_path,
)

# parallelization-policy.md v3 初版默认帽
# owner 2026-06-26: 去掉灰度并行限制 — 真实环境全量并行, 该并行多少就并行多少。
# 唯一并行上界 = 熔断保险 TOWOW_SPAWN_RUNAWAY_THRESHOLD(默认12, 安全网, 不拆)。
#
# owner 2026-06-26 (同日修正): 999=无上限 在这台 24G 机器上把内存压垮 —— daemon 重启一口气
# 派 26 个 claude -p(opus), swap 飙到 24/26G, load 218, 机器窒息。"全量并行"的真实物理上界
# 是机器内存, 不是 999。改成机器扛得住的真上限: 同时 4 个工位(每个 opus claude -p 峰值约
# 0.5~1G), 仍是真并行(完一个补一个, backlog re-scan 捞回被帽截断的), 只是不雪崩。
# 要更快/更保守: 调这里或设 TOWOW_EXEC_MAX_PARALLEL。runaway 熔断(12)仍是最终安全网。
#
# owner 2026-07-16: 恢复 spec 原定总帽 6 (parallelization-policy 原文即"总≤6"; 4 是 06-26
# OOM 后临时压低的安全值)。实测决策依据: 满载 4/4 且 backlog 积压 132 件(84 review+23 fix+
# 19 plan+6 consensus)= 帽本身是吞吐瓶颈; daemon 口径内存空闲 59%(pause 阈值 20%), 每工位峰值
# ~1G, 6 个工位 ~6G 远在安全区。非 exec 帽(review/fix/plan)是扁平计数 = 本常量, 直接吃积压。
# 配套: _exec_cap_total 的 env 钳子改为可上调到 EXEC_CAP_HARD_CEIL(见 orchestrator.py), 以后
# 调并发只动环境变量、不再改 Harness。runaway 熔断(12)与内存 killswitch(20%)仍是最终安全网。
DEFAULT_CAP_TOTAL = 6
DEFAULT_CAP_OPUS = 4
DEFAULT_CAP_SONNET = 6
HIGH_FANOUT_READER_THRESHOLD = 3


def completed_success_task_ids(events: list[dict[str, object]]) -> set[str]:
    """所有 outcome=success 的 TaskRunCompleted 的 task_id (= 真正跑完的 task).

    只认 success: aborted_for_replan / aborted_* 不算完成 (那个 task 还没产出, 不解锁下游).
    """
    done: set[str] = set()
    for e in events:
        if _event_type(e) != "TaskRunCompleted":
            continue
        a = _after_state(e)
        if a.get("outcome") == "success":
            tid = str(a.get("task_id", ""))
            if tid:
                done.add(tid)
    return done


def task_type_from_events(task_id: str, events: list[dict[str, object]]) -> str | None:
    """反查 task_id 的 task_type (扫 TaskNodeCreated)。查不到 → None。"""
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        a = _after_state(e)
        if a.get("task_id") == task_id:
            tt = a.get("task_type")
            return str(tt) if tt else None
    return None


def _task_phase_from_events(task_id: str, events: list[dict[str, object]]) -> str | None:
    """反查 task_id 的 phase (扫 TaskNodeCreated)。查不到 / 历史 event 无 phase → None。"""
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        a = _after_state(e)
        if a.get("task_id") == task_id:
            ph = a.get("phase")
            return str(ph) if ph else None
    return None


def _plan_has_design_time_review_plan(events: list[dict[str, object]], plan_id: str) -> bool:
    """T-LND-10 (INV-B2-3): 本 plan 的 design-time review_plan 是否已存在。

    ReviewPlanCreated 强制 design_time mode (main.py review_plan_create), 故任一本 plan 的
    ReviewPlanCreated 即"design 标准已先于实现产出"。plan 与 review_plan 经上游共识/brief 关联:
    plan_start 从 EngineeringConsensusFreezed 继承 plan_id(= 共识 brief), review_plan.
    associated_brief_id/associated_consensus_id 指同一 brief → 匹配 plan_id。
    """
    for e in events:
        if _event_type(e) != "ReviewPlanCreated":
            continue
        # R05 live dispatch deadlock fix: ReviewPlanCreated is a flat "created" event —
        # associated_brief_id/associated_consensus_id live at payload TOP-LEVEL, not under
        # after_state. The prior _after_state(e) read returned {} for every real event →
        # has_review_plan always False → every implementation-phase task 永久 blocked from the
        # ready-set even when the plan's design-time review_plan exists (e.g. rp-landing-rootfix-b1).
        # Worse, stuck_implementation_plans reused this → reported "review_plan missing" when it
        # existed. Read payload top-level first; fall back to after_state for any wrapped/stub form.
        payload = e.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("stub_original_payload"), dict):
            payload = payload["stub_original_payload"]
        inner = payload.get("after_state")
        inner = inner if isinstance(inner, dict) else {}
        brief = payload.get("associated_brief_id") or inner.get("associated_brief_id")
        consensus = payload.get("associated_consensus_id") or inner.get("associated_consensus_id")
        if brief == plan_id or consensus == plan_id:
            return True
    return False


def dispatch_target_for_task(
    task_id: str, events: list[dict[str, object]]
) -> tuple[str, str | None]:
    """T-LND-05 (INV-B1-4): 按 task.task_type 选派发目标, 返回 (dispatch_to, review_mode)。

    task_type=REVIEW → ('review', 'author_time') —— 派给 review skill 走 finding 生命周期+verdict,
    **绝不**派给 execution skill (派错则它永产不出 verdict, review 完成永不发生 = 危机1根)。
    其余 task_type / 查不到 → ('execution', None)。review_mode 默认 author_time (REVIEW task 审
    已产出的实现; design_time review_plan 前置归 T-LND-10)。
    """
    if task_type_from_events(task_id, events) == TaskType.REVIEW.value:
        return "review", "author_time"
    return "execution", None


def compute_ready_tasks(
    events: list[dict[str, object]],
    plan_id: str,
    completed_task_ids: set[str],
) -> list[str]:
    """plan_id 的 task_graph 里此刻 ready (可立即并行执行) 的 task_id 列表.

    "前置已满足" 集 = completed_task_ids (success) ∪ closed_task_ids(events) —— 见
    readyset-closure-exclusion-contract@v1 (T-DEC-3). closed = done-elsewhere 诚实终态
    (TaskNodeClosed, 已过 closure-evidence-verification-gate), 它和 success 在 *同一层*
    (completed-set 层) 折进 "前置已满足", 不在 caller 的 dedup 层。

    task T ready iff:
      - T 自己还没 completed (success) 也没 closed (done-elsewhere), 且
      - T 的所有【前置】(dep 图中 src→T 的每个 src) 都已 completed (success) 或 closed.

    这给 closed 终态双重效果 (concept point 2): 被关闭 task (a) 自己落进 satisfied → 永远进不了
    ready (永不重派), (b) 作为前置落进 satisfied → 其下游解锁 (交付物已存在 → 下游可继续)。
    在 completed-set 层折入 (而非 dedup 层) 还让它对 exec-stamp 清除 / 熔断 / pending_replan 等
    运行态免疫 (concept point 3): closed task 在这里就被剔, 根本到不了 caller 的那些运行态过滤。

    无依赖的 task 一开始就全 ready (→ 一上来就能并行铺开). 完成一批后, 被它们解锁的下一批
    自动变 ready (orchestrator 重算补派). 不设并行上限 —— ready 多少就返回多少 (owner: 不限量).

    依赖边方向沿用 _plan_dep_graph: adj[src] = [tgt...] 表示 src must precede tgt.
    所以 T 的前置 = 所有满足 T ∈ adj[src] 的 src.

    write-set 冲突不在这里挡 (initial 版): planner 该用 dep 边 / conflict_group 把真冲突表达成
    依赖; 残余的并发写冲突由 commit gate 物理 reject 兜底 (M-0.5) —— 不在调度层重复一套。
    """
    task_ids, adj, _fan_in = _plan_dep_graph(events, plan_id)
    preds: dict[str, list[str]] = {t: [] for t in task_ids}
    for src, tgts in adj.items():
        for tgt in tgts:
            if tgt in preds:
                preds[tgt].append(src)
    # readyset-closure-exclusion-contract@v1 (T-DEC-3): fold closed_task_ids into the SAME
    # "prerequisite satisfied" set as completed (success). Every dispatch path funnels through this
    # one function, so this single union is the unified exclusion (concept point 4 — 非 task-m04
    # 专用 hack). Derived from events here (not a caller param) so even callers that pass a bare
    # completed set inherit the closure exclusion.
    satisfied = set(completed_task_ids) | closed_task_ids(events)
    # T-LND-10 (INV-B2-3): implementation-phase task 不 ready, 除非本 plan 的 design-time
    # review_plan 已存在 (review 标准必先于实现, 防危机4 时序倒置)。design/review/phase=None
    # (legacy) task 不受此门挡。整 plan 算一次 (review_plan 存在性与具体 task 无关)。
    has_review_plan = _plan_has_design_time_review_plan(events, plan_id)
    ready: list[str] = []
    for t in task_ids:
        if t in satisfied:
            continue
        if not all(p in satisfied for p in preds.get(t, [])):
            continue
        if not has_review_plan and _task_phase_from_events(t, events) == "implementation":
            continue  # implementation 前置: 本 plan design-time review_plan 未存在 → 不 ready
        ready.append(t)
    return ready


def _plan_has_implementation_task(events: list[dict[str, object]], plan_id: str) -> list[str]:
    """plan_id 下 phase=implementation 的 task_id 列表 (保序去重)。

    用于 T-FIX-B3-02 stuck 巡检: 只有含 implementation task 的 plan 才会被 T-LND-10 的门
    挡在 ready-set 外, 故只对这类 plan 巡检 never-ready 死锁。
    """
    task_ids, _adj, _fi = _plan_dep_graph(events, plan_id)
    out: list[str] = []
    for t in task_ids:
        if _task_phase_from_events(t, events) == "implementation" and t not in out:
            out.append(t)
    return out


def stuck_implementation_plans(
    events: list[dict[str, object]],
) -> dict[str, list[str]]:
    """T-FIX-B3-02 (CON-content#2 / AUTOPILOT-core#1): phase 门 never-ready 静默死锁的探测.

    返回 {plan_id: [卡住的 implementation task_id...]} —— 满足全部条件的 plan 即"卡住":
      - 已 PlanFreezed, 且
      - 含至少一个 phase=implementation 的 task, 且
      - 此刻 compute_ready_tasks 为空 (无任一 task 进 ready-set), 且
      - 本 plan 无 design-time ReviewPlanCreated (= implementation 门的解锁前置缺失)。

    这正是 T-LND-10 的门按设计正确挡住、但解锁前置 (design-time review_plan) 永不到来时的
    死锁形态: 门没错, 错在没人推进、又无人知道。本函数只**探测**, 不改门拦截语义; 告警与
    轮次/dedup 由 orchestrator 侧的 sweep_phase_stuck_plans 负责 (本模块保持纯函数: 只读 events)。

    判据说明:
      - "无 ready" 用 compute_ready_tasks (与真实派发判 ready 同一函数, 杜绝判据漂移): 若有任一
        task ready, 说明 plan 在动 (哪怕 implementation task 被挡, design/review task 在跑) → 不卡。
      - "无 review_plan" 复用 _plan_has_design_time_review_plan: 它在 = 门会放行 implementation
        task → compute_ready_tasks 非空 → 上一条已排除; 显式查它是为了把"死锁根因"标进语义。
    """
    out: dict[str, list[str]] = {}
    completed = completed_success_task_ids(events)
    # readyset-closure-exclusion-contract@v1 (T-DEC-3): a closed (done-elsewhere) task is a terminal
    # state, not a stuck one — fold it into "已满足" so an isolated closed impl-task (no downstream to
    # make compute_ready_tasks non-empty) is not falsely reported stuck. compute_ready_tasks itself
    # already unions closed internally; this keeps the stuck_tasks filter consistent with it.
    satisfied = completed | closed_task_ids(events)
    for plan_id in all_freezed_plan_ids(events):
        impl_tasks = _plan_has_implementation_task(events, plan_id)
        if not impl_tasks:
            continue  # 无 implementation task → 门不挡它 → 无此死锁风险
        if compute_ready_tasks(events, plan_id, completed):
            continue  # 有 task 在 ready-set → plan 在推进 → 不卡
        if _plan_has_design_time_review_plan(events, plan_id):
            continue  # review_plan 在 (门会放行) → 不是 never-ready 死锁, 是别的原因
        # 只报尚未完成且未关闭的 implementation task (已完成 / 已关闭的不算卡)
        stuck_tasks = [t for t in impl_tasks if t not in satisfied]
        if stuck_tasks:
            out[plan_id] = stuck_tasks
    return out


def ready_tasks_to_dispatch(
    events: list[dict[str, object]],
    plan_id: str,
    already_dispatched_task_ids: set[str],
) -> list[str]:
    """ready 且尚未派过 execution 的 task_id (orchestrator fan-out 的最终列表).

    = compute_ready_tasks(基于当前 completed) 减去【已经派过 execution 的】(dedup, 防同一 task
    被 PlanFreezed 和某个 TaskRunCompleted 两次算 ready 而重复 spawn). 一个 task 一生只派一次
    execution (completed 后也不再 ready, 这里再加 dispatched 去重双保险)。
    """
    completed = completed_success_task_ids(events)
    ready = compute_ready_tasks(events, plan_id, completed)
    return [t for t in ready if t not in already_dispatched_task_ids]


def tasks_pending_replan(events: list[dict[str, object]]) -> set[str]:
    """有【未消费的重排请求】的 task_id —— 已触发 RePlanTriggered 但 re-decompose 还没产出新节点。

    owner 2026-06-26 (esc T-RMD-PROV-01): 任务因冻结判据不可满足而 aborted_for_replan 时, B2 会清掉
    它的 exec 戳 (好让重排后能重派)。但清戳后 ready-set 的 dedup 失效 → orchestrator 又把它当就绪
    重派 execution → 在 planner 把判据重排修好之前会**循环空烧会话 + 堆重复锚点**。

    护栏: 一个 task 一旦有 RePlanTriggered, 在对应的 re-decompose 完成 (产出新的 TaskNodeCreated /
    新 PlanFreezed 把它重新纳入) 之前, 不该再被当 execution 就绪重派 —— 等 planner 先把它重排好。

    判据 (保守, level-triggered): 收集所有 RePlanTriggered 的 task_id; 若该 task 之后又出现过同
    task_id 的 TaskPackagePublished (re-decompose 真产出新包了) 则视为已消费、不再 pending。其余
    仍 pending。

    T-TWU-B1/T-R01-9 回归修复: 消费锚点原为 "outcome=success 的 TaskRunCompleted" —— 但这个锚点
    自锁死: 只堵住"排除逻辑没接主派发路径"的漏洞、不换锚点的话, task 永远进不了派发池, 而
    "success TaskRunCompleted" 恰恰只有派发成功之后才会出现 = 一旦漏洞被单独修掉, 该 task 从
    "被误派" 变成 "永久卡死"。改锚为 TaskPackagePublished (re-decompose 产出新包这一步) 才是真正
    "重排已消费" 的时点, 让 task 在新包发布后即可正常进入派发。

    假设前提: re-decompose 复用同一个 task_id 重发新包。若 partial_replan 给它换了新 task_id,
    旧 task_id 上的 pending 状态永久保留是预期行为 (视为被替代, 不是卡死 —— 新 task_id 走自己的
    ready-set, 不受此函数影响)。
    """
    replan_at: dict[str, str] = {}  # task_id → 最近一次 RePlanTriggered 的 timestamp
    republished_at: dict[str, str] = {}  # task_id → 最近一次同 task_id TaskPackagePublished 的 timestamp
    for e in events:
        et = _event_type(e)
        if et == "RePlanTriggered":
            a = _after_state(e)
            tid = str(a.get("task_id", ""))
            if tid:
                ts = str(e.get("timestamp", ""))
                if ts >= replan_at.get(tid, ""):
                    replan_at[tid] = ts
        elif et == "TaskPackagePublished":
            a = _after_state(e)
            tid = str(a.get("task_id", ""))
            if tid:
                ts = str(e.get("timestamp", ""))
                if ts >= republished_at.get(tid, ""):
                    republished_at[tid] = ts
    # pending = 有 replan 触发, 且该 replan 之后没有更晚的同 task_id 新包发布 (重排尚未真正产出)。
    return {
        tid
        for tid, rts in replan_at.items()
        if republished_at.get(tid, "") < rts
    }


def ready_execution_tasks_to_dispatch(
    events: list[dict[str, object]],
    plan_id: str,
    already_dispatched_task_ids: set[str],
) -> list[str]:
    """T-FIX-B2-02 (补 T-LND-05 在 re-scan 路径的漏): ready 且尚未派、且 task_type 真属
    execution 的 task_id —— REVIEW-typed task 被排除出 execution 候选池。

    = ready_tasks_to_dispatch 再用 dispatch_target_for_task 过滤, 只保留 target=='execution'。
    REVIEW-typed task (target=='review') 不进 execution 批 —— 它由 orchestrator 的
    _ready_execution_decisions(主路径)按 dispatch_target_for_task 路由产 dispatch_to=review
    的 decision (经 INV-B2-1 单飞门排队), 故被这里排除 ≠ 被漏派, 只是改道。

    背景: backlog re-scan 的 _dispatch_execution_batch 候选池过去直接吃 ready_tasks_to_dispatch
    (不过滤 task_type), REVIEW task 进池被 _spawn_one_execution(hardcoded dispatch_to=execution)
    当 execution 派 → 它永产不出 verdict → review 完成永不发生 (危机1根)。本函数堵住这条 re-scan 漏。

    纯函数 (只读 events 算列表), 与 ready_tasks_to_dispatch 同签名, 易测可替换。

    owner 2026-06-26 (esc T-RMD-PROV-01): 再排除【有未消费重排请求】的 task —— abort_for_replan
    清戳后会被反复当就绪重派、空烧会话 + 堆重复锚点。等 planner 把它重排好 (re-decompose) 再放行。
    """
    pending_replan = tasks_pending_replan(events)
    return [
        t
        for t in ready_tasks_to_dispatch(events, plan_id, already_dispatched_task_ids)
        if dispatch_target_for_task(t, events)[0] == "execution"
        and t not in pending_replan
    ]


# ════════════════════════════════════════════════════════════════════════════════
#  B4 (PARALLEL-EXEC-FIX-DESIGN) — 并发帽 + 关键路径优先 + model 分层 + 高 fan-out 串行
#  全部纯函数; orchestrator 拿 select_dispatch_batch 的结果去 spawn。
# ════════════════════════════════════════════════════════════════════════════════


def all_freezed_plan_ids(events: list[dict[str, object]]) -> list[str]:
    """所有出现过 PlanFreezed 的 plan_id (backlog re-scan 的扫描对象, 去重保序)。"""
    seen: list[str] = []
    for e in events:
        if _event_type(e) != "PlanFreezed":
            continue
        pid = str(_after_state(e).get("plan_id", ""))
        if pid and pid not in seen:
            seen.append(pid)
    return seen


def task_model_tiers(events: list[dict[str, object]]) -> dict[str, str]:
    """task_id → model_tier (TaskModelTierAssigned latest-wins; 无分配 → 'opus' 保守默认).

    M-1.3 §10.2: 完全没匹配 opus 因素也判 opus (保守); 缺分配按同一保守原则。
    """
    tiers: dict[str, str] = {}
    for e in events:
        if _event_type(e) != "TaskModelTierAssigned":
            continue
        ast = _after_state(e)
        tid = str(ast.get("task_id", ""))
        tier = str(ast.get("model_tier", ""))
        if tid and tier:
            tiers[tid] = tier  # latest-wins (事件按 log 序)
    return tiers


def tier_of(task_id: str, tiers: dict[str, str]) -> str:
    """该 task 的派发 tier; 未分配 → opus (保守, M-1.3 §10.2)。"""
    return tiers.get(task_id, "opus")


def critical_path_task_ids(events: list[dict[str, object]], plan_id: str) -> set[str]:
    """plan 关键路径上的 task 集 (调度优先占配额, parallelization-policy + M-1.3 §3.5)。"""
    try:
        result = compute_critical_path(events, plan_id)
    except Exception:
        return set()
    out: set[str] = set(result.primary_path)
    for p in result.alternative_paths:
        out.update(p)
    return out


def _entity_keys(raw: object) -> set[str]:
    """claims 条目规范化成 'entity_type:entity_id' 键集 (兼容 dict 条目与裸 str)。"""
    keys: set[str] = set()
    if isinstance(raw, list):
        for w in raw:
            if isinstance(w, dict):
                keys.add(f"{w.get('entity_type')}:{w.get('entity_id')}")
            elif isinstance(w, str) and w:
                keys.add(w)
    return keys


def _plan_claims(
    events: list[dict[str, object]], plan_id: str,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """plan 内每 task 的 (read_set, write_set) entity 键集 (latest-wins)。"""
    task_ids, _adj, _fi = _plan_dep_graph(events, plan_id)
    id_set = set(task_ids)
    reads: dict[str, set[str]] = {}
    writes: dict[str, set[str]] = {}
    for e in events:
        et = _event_type(e)
        if et not in ("TaskReadSetClaimed", "TaskWriteSetClaimed"):
            continue
        ast = _after_state(e)
        tid = str(ast.get("task_id", ""))
        if tid not in id_set:
            continue
        if et == "TaskReadSetClaimed":
            reads[tid] = _entity_keys(ast.get("read_set"))
        else:
            writes[tid] = _entity_keys(ast.get("write_set"))
    return reads, writes


def uncovered_mandated_new_test_files(
    content: object,
    events: list[dict[str, object]],
    plan_id: str | None,
    task_id: str,
    repo_root: object,
) -> list[str]:
    """f-tdec-plan-writeset-omits-mandated-tests-no-owner-expand-port (M-1.6 fix, 预防层 #1)。

    一个自包含 package 的 done_criteria 用 verification_method=test 的 machine_check.test_selector
    钉死某测试文件, 但该文件 (a) 当前仓库里**不存在** (= 本 task 必须新建) 且 (b) 不在本 task 的
    **CLAIMS write_set** 里 —— executor 物理无法在隔离工位内创建它 = 自相矛盾的自包含包 (跨
    T-DEC-1..4 系统性复发; T-DEC-5 正确声明=对照, 不报)。返回 blocker 字符串列表 (空 = 全覆盖)。

    设计要点 (advisor 把关):
    - 检 **CLAIMS** write_set (TaskWriteSetClaimed, 经 canonical `_plan_claims` latest-wins) 而非
      package write_set —— .owner 由 claims 派生, 是 V-01 物理 enforce 的**真实**写边界。只检 package
      write_set 会"测试绿、真 .owner 仍拦死 executor"(built+tested≠works)。
    - 文件**已存在** → executor 只跑不写, 豁免 (避免对"跑既有回归测试"型 done_criteria 误报;
      实证 8/45 既有文件不该被强求进 write_set)。"改既有测试却没声明 write_set" 的残余面归 RC2 债。
    - normalize 用 V-01 同一个 `normalize_guard_target`, 故预测与 V-01 实际拦截**完全一致**。
    - plan_id is None (legacy/standalone caller, 无法 scope claims) → 返 [] (degraded, 不误报)。
    """
    from pathlib import Path

    from towow.l1.closure_verification import _split_pytest_selector
    from towow.shell.worktree import normalize_guard_target

    if plan_id is None:
        return []
    _reads, writes = _plan_claims(events, plan_id)
    claimed = {normalize_guard_target(k) for k in writes.get(task_id, set())}

    root = Path(str(repo_root))
    bases = (root, root / "harness")

    defects: list[str] = []
    done_criteria = getattr(content, "done_criteria", None) or []
    for dc in done_criteria:
        mc = getattr(dc, "machine_check", None)
        if mc is None or getattr(mc, "verification_method", None) != "test":
            continue
        selector = (getattr(mc, "test_selector", None) or "").strip()
        if not selector:
            continue
        file_part = _split_pytest_selector(selector)[0].strip()
        if not file_part:
            continue
        # V-01 同一 normalize (strip file:/harness/ 前缀 + normpath) —— 存在性与归属性必须用**同一**
        # 规范化键, 否则 harness/ 前缀的 selector (实证 21/71 真包用此前缀) 存在性查错路径 → 既有测试
        # 被误判"必须新建" → 生产 false-positive。两处都用 norm_file 对齐 (advisor 把关)。
        norm_file = normalize_guard_target(file_part)
        if not norm_file:
            continue
        # 已存在 → executor 只跑不写, 不强求 write_set (避免对既有测试误报)。两个 base 兼容
        # harness 包根 (repo_root/tests/...) 与 outer-repo 布局 (repo_root/harness/tests/...)。
        if any((b / norm_file).exists() for b in bases):
            continue
        if norm_file in claimed:
            continue
        defects.append(
            f"done_criterion {getattr(dc, 'criterion_id', '?')} test_selector 钉死必须**新建**的测试文件 "
            f"'{file_part}' 但它不在本 task CLAIMS write_set (TaskWriteSetClaimed → .owner → V-01 物理写边界) "
            "— executor 无法在隔离工位创建它 (自相矛盾的自包含包, f-tdec-plan-writeset-omits-mandated-tests)。"
            f"修: `plan write-set --task-id {task_id} --write-set file:{file_part} ...` 把它纳入 write_set 后 retry"
        )
    return defects


def _has_py_write_product(content: object) -> bool:
    """content 是否声明了任何 .py 文件写产出 (= 有可测代码/测试)。

    两个源都看 (任一有 .py 即算有可测产出):
    - write_set entry = {entity_type, entity_id}; entity_type=='file' 才是文件路径 (=='concept'
      是概念 claim, 非文件)。镜像 plan_freezed._task_write_set_paths 的 file-only 取路径范式。
    - file_refs entry = {path, purpose}; purpose=='will_write' 才是写 (read_only/reference_doc 不算)。
    """
    write_set = getattr(content, "write_set", None) or []
    for w in write_set:
        if getattr(w, "entity_type", None) == "file":
            eid = getattr(w, "entity_id", "") or ""
            if eid.endswith(".py"):
                return True
    file_refs = getattr(content, "file_refs", None) or []
    for f in file_refs:
        if getattr(f, "purpose", None) == "will_write":
            path = getattr(f, "path", "") or ""
            if path.endswith(".py"):
                return True
    return False


def doc_only_test_machine_check_defects(
    content: object,
    events: list[dict[str, object]],
    plan_id: str | None,
    task_id: str,
) -> list[str]:
    """fnd-r01-9-assembler-fake-test-selector (M-1.6 fix): doc-only/无代码产出的 task 套 test 型
    machine_check 是无源伪门 — 拒发布, 逼 assembler 改 grep 验交付物存在。

    症结: 一个 task 的 write_set/file_refs(will_write) 里没有任何 .py 写产出 (纯文档/配置交付物,
    如 docs/*.md) 却给 done_criterion 配 verification_method=test 的 machine_check.test_selector ——
    该 test 指向的测试要么不存在 (success 门 pytest fail-closed), 要么逼 executor 凭空造空壳测试
    过门 (=fake-done), 与 canonical done_criteria (交付物存在类) 直接矛盾。正确形态是 file_exists +
    machine_check.verification_method=grep 验交付物文档存在 (活样例: 兄弟任务 T-R01-7-DOC-1 —
    grep verification_pattern/expected_occurrences/search_scope, test_selector=null)。实证: T-R01-9
    (write_set 只含 docs/R01-PRODUCTION-ROLLOUT.md) 被此伪门反复重派空转 ≥5 次自主会话。返回 blocker
    列表 (空 = 无此缺陷)。

    与 new-capability-task-classifier@v1 第10道门 (check_new_capability_tasks_have_livefire_machine_check)
    **严格互斥, 防冻结死锁**: 被三信号判为'新增能力/接线'的 task **必须** live-fire test 型 machine_check;
    本门只对**非新增能力**的 doc-only task 触发。signal① 是子串命中 write_set 路径 (marker 含
    daemon/orchestrator/dispatch 等) —— 一个 doc 文件名恰含该子串的 task 会被判新增能力, 复用同一
    判别器 (new_capability_task_ids, PUBLIC bridge) 把它排除, 不制造'一门要 test / 一门拒 test'的
    死锁。判定顺序: 有 .py 产出先放过 (最常见的真代码任务, 不查判别器)。

    plan_id is None (legacy/standalone caller, 无法判别新增能力) → 返 [] (degraded, 不误报, 与
    uncovered_mandated_new_test_files 同保守策略: 宁可漏放不误拦)。
    """
    if plan_id is None:
        return []
    # 有任何 .py 写产出 → 有可测代码/测试, test 型 machine_check 合法 → 不触发 (也不必查判别器)。
    if _has_py_write_product(content):
        return []
    # 与新增能力第10道门严格互斥: 被判新增能力的 task 归它管 (它强制 test 型), 本门不碰 —— 否则
    # 死锁 (它要 test / 本门拒 test)。复用同一判别器 (single source of truth, 无漂移)。
    from towow.schemas.payloads.plan_freezed import new_capability_task_ids

    if task_id in new_capability_task_ids(events, plan_id):
        return []
    defects: list[str] = []
    done_criteria = getattr(content, "done_criteria", None) or []
    for dc in done_criteria:
        mc = getattr(dc, "machine_check", None)
        # verification_method 是 ClosureVerificationMethod (StrEnum), == "test" 成立 (镜像
        # uncovered_mandated_new_test_files 的字符串比较范式)。
        if mc is None or getattr(mc, "verification_method", None) != "test":
            continue
        selector = (getattr(mc, "test_selector", None) or "").strip()
        defects.append(
            f"done_criterion {getattr(dc, 'criterion_id', '?')} 用 test 型 machine_check "
            f"(test_selector={selector!r}) 但本 task 无任何 .py 代码/测试写产出 "
            "(write_set/file_refs will_write 全非 .py = 纯文档/配置交付物) — test 型是无源伪门: "
            "success 门跑 pytest 无被测代码, executor 要么 fail 要么造空壳测试过门 (=fake-done)。"
            "doc-only/maintain 交付物类任务改用 verification_type=file_exists + "
            "machine_check.verification_method=grep 验交付物存在 (照 T-R01-7-DOC-1: verification_pattern "
            "+ expected_occurrences + search_scope, test_selector=null)。"
            "fnd-r01-9-assembler-fake-test-selector"
        )
    return defects


def high_fanout_blocked(
    events: list[dict[str, object]],
    plan_id: str,
    candidates: list[str],
    active_task_ids: set[str],
) -> set[str]:
    """S13 (parallelization-policy 风险表): 高 fan-out concept 保护 — 返回本轮该缓派的 task 集.

    规则: concept 被 >3 个 plan task read 且【当前活跃工位里有 task 在 write 它】→ 触碰该
    concept (读或写) 的候选 task 本轮不派 (保护正在 write 的 task, 等它完工再放)。
    """
    if not candidates or not active_task_ids:
        return set()
    reads, writes = _plan_claims(events, plan_id)
    # concept → reader 数 (plan 全体 task)
    reader_count: dict[str, int] = {}
    for rs in reads.values():
        for k in rs:
            reader_count[k] = reader_count.get(k, 0) + 1
    # 活跃工位正在 write 的高 fan-out concept
    hot: set[str] = set()
    for a in active_task_ids:
        for k in writes.get(a, set()):
            if reader_count.get(k, 0) > HIGH_FANOUT_READER_THRESHOLD:
                hot.add(k)
    if not hot:
        return set()
    blocked: set[str] = set()
    for c in candidates:
        touch = reads.get(c, set()) | writes.get(c, set())
        if touch & hot:
            blocked.add(c)
    return blocked


def select_dispatch_batch(
    events: list[dict[str, object]],
    plan_id: str,
    candidates: list[str],
    *,
    active_total: int,
    active_by_tier: dict[str, int],
    active_task_ids: set[str],
    cap_total: int = DEFAULT_CAP_TOTAL,
    cap_opus: int = DEFAULT_CAP_OPUS,
    cap_sonnet: int = DEFAULT_CAP_SONNET,
) -> list[tuple[str, str]]:
    """从候选 ready task 里按 parallelization-policy 选出本轮真派的 [(task_id, tier)...].

    次序与配额:
      1. 高 fan-out 串行保护先剔除 (S13)。
      2. 关键路径 task 优先占配额 (S15)。
      3. 逐个入选: 总活跃 < cap_total 且该 tier 活跃 < tier 帽。
    被截断的不丢 — orchestrator 的 backlog re-scan 每轮重算捞回 (红队 fatal 的配套, 缺它
    加帽=永久饿死)。注意这帽只限同时工位数, 不限链长 (owner E.5)。
    """
    if not candidates:
        return []
    blocked = high_fanout_blocked(events, plan_id, candidates, active_task_ids)
    eligible = [c for c in candidates if c not in blocked]
    critical = critical_path_task_ids(events, plan_id)
    eligible.sort(key=lambda t: (t not in critical, t))  # 关键路径在前, 同级按名稳定排序
    tiers = task_model_tiers(events)
    tier_caps = {"opus": cap_opus, "sonnet": cap_sonnet}
    total = active_total
    by_tier = dict(active_by_tier)
    picked: list[tuple[str, str]] = []
    for t in eligible:
        if total >= cap_total:
            break
        tier = tier_of(t, tiers)
        cap_t = tier_caps.get(tier, cap_opus)  # 未知 tier 按 opus 帽保守
        if by_tier.get(tier, 0) >= cap_t:
            continue
        picked.append((t, tier))
        total += 1
        by_tier[tier] = by_tier.get(tier, 0) + 1
    return picked
