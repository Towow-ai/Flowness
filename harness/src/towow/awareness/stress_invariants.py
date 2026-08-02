"""B-5 核 · 多车道压测四不变量校验 (层② done-real 判据, 设计 20-layer23 §6.3)。

owner 纪律: 不许"单车道点亮当 done-real"(单车道恰好绕开所有并发 bug)。层②的设计对不对, 只有 N 车道
真跑 + 四不变量全绿才证明。本模块是四不变量里**可静态查**的三条的纯校验核 (对账本快照查),
hermetic 可单测; 第四条"无锁死"需 runner 计时(跑到收敛 vs 超时), 不是静态快照可判, 由 stress runner
(B-5 的可执行 driver, 后续)持有, 本模块标出不冒充。

四不变量(每条对应一类层②腐蚀):
1. 无孪生  ← T2.3 原子认领: 任一 task_id 的 TaskRunCompleted(success) ≤1; 任一 plan_id PlanFreezed ≤1。
2. 无 finding 错挂 ← T2.4 provenance: review 语境 FindingCreated 的归属按 review_unit_id/
   provenance.session_id 反查一致 (F-1 修复; 原设计想读 trigger_event_id, 但该字段 schema
   从未定义过, 见 check_no_finding_misfire docstring)。
3. 无账本腐蚀 ← §二账本契约: SEQ 连续无断裂/无重号/无覆盖(同 seq 异内容)。对照三次账本回退指纹。
4. 无锁死  ← (runner 持有: 跑到收敛 vs 超时; 非静态可判, 本模块不冒充)。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

# F-1 (哨兵玻璃眼修复): 单一真值源复用, 不重新发明一份容易漂移的"finding 归属"派生逻辑 ——
# _event_review_unit_id 是 l0/projection/review_verdict.py 折叠 verdict 时实际在用的派生函数
# (payload.review_unit_id 优先, 回退 provenance.session_id; T-FIX-B2-06/T-LND-02 血缘证据),
# 本模块直接复用它做"这条 finding 有没有可反查的归属"判断, 而不是自己另读一个 schema 里不存在的
# 字段 (旧版正是栽在"自己发明一份归属信号"上 —— 见 check_no_finding_misfire 新 docstring)。
from towow.l0.projection.review_verdict import _event_review_unit_id


@dataclass(frozen=True)
class InvariantViolation:
    invariant: str   # "no_twin" / "no_finding_misfire" / "no_ledger_corruption"
    detail: str
    evidence: dict = field(default_factory=dict)


def _success(e: dict) -> bool:
    p = e.get("payload") or {}
    after = p.get("after_state") or {}
    return (after.get("outcome") or p.get("outcome")) == "success"


def check_no_twin(events: list[dict]) -> list[InvariantViolation]:
    """任一 task_id 的 TaskRunCompleted(success) >1 = 孪生; 任一 plan_id PlanFreezed >1 = 双 planner。"""
    task_success: Counter[str] = Counter()
    plan_freeze: Counter[str] = Counter()
    for e in events:
        et = e.get("event_type")
        p = e.get("payload") or {}
        after = p.get("after_state") or {}
        if et == "TaskRunCompleted" and _success(e):
            tid = after.get("task_id") or p.get("task_id")
            if tid:
                task_success[tid] += 1
        elif et == "PlanFreezed":
            pid = after.get("plan_id") or p.get("plan_id")
            if pid:
                plan_freeze[pid] += 1
    out = [InvariantViolation("no_twin", f"task {t} 有 {n} 条 success TaskRunCompleted (孪生)", {"task_id": t, "count": n})
           for t, n in task_success.items() if n > 1]
    out += [InvariantViolation("no_twin", f"plan {pid} 被冻结 {n} 次 (双 planner 孪生)", {"plan_id": pid, "count": n})
            for pid, n in plan_freeze.items() if n > 1]
    return out


def check_no_finding_misfire(events: list[dict]) -> list[InvariantViolation]:
    """review 语境 (source.type=model_review) 的 FindingCreated 必须能反查到产它的 review 会话。

    ── F-1 修复 (哨兵玻璃眼活血案, f-sentinel-1fd34afde707b3c9) ──
    旧版读 `payload.trigger_event_id` —— `FindingCreatedPayload` (schemas/payloads/finding.py,
    _STRICT/extra=forbid) **从未定义过这个字段**, 活账本实跑: 1592/1596 条真实 FindingCreated
    100% 被判"归属悬空", 又因 finding_signature 的锚点白名单不含任何本规则能命中的键 → 全塌成
    一个签名 f-sentinel-1fd34afde707b3c9, 被 owner accept 为 baseline 后整条检查永久静音
    (第一次睁眼即失明, 双镜头补验坐实)。

    真实归属信号今天是 `review_unit_id` (payload, 可能在 after_state 里) 优先、回退
    `provenance.session_id` —— 与 verdict 折叠时用的同一套派生 (`_event_review_unit_id`,
    l0/projection/review_verdict.py, T-FIX-B2-06/T-LND-02 血缘证据), 不重新发明一份。

    **非 review 语境不受此约束** (`source.type != "model_review"`, 含系统自查 / execution 越
    scope 上报等): `finding.py:328` docstring 明写这类 finding **允许** `review_unit_id=None`
    ——它们的归属模型根本不是"哪个 review 会话产的"。若不排除, 检测器只是换个方式继续瞎: 活账本
    验证过, 1596 条里 1316 条是这类系统 finding (如 daemon 的 cross_projection_inconsistency
    自查), "两者都缺 = 悬空" 这个朴素改法会把它们全部误判 (100% 误判 → 65% 误判, 仍不是真的
    归零)。280 条 review 语境 finding 用本函数校验, 实测 0 违例。

    两类真违例 (仅 review 语境):
      1. 归属悬空: review_unit_id 与 provenance.session_id 都缺 —— 无法反查是哪个 review 会话产的;
      2. 归属分裂: 两者都在但不相等 —— 写时应恒等 (cli/main.py 两条 FindingCreated emit 路径都用
         同一个 sid 同时盖 payload.review_unit_id 与 provenance_hint.session_id), 不等即真实的
         错挂信号 (esc-532866a8 那类 provenance 腐蚀若复发, 这条查得到)。
    """
    out: list[InvariantViolation] = []
    for e in events:
        if e.get("event_type") != "FindingCreated":
            continue
        p = e.get("payload") or {}
        after = p.get("after_state")
        src = after if isinstance(after, dict) else p
        source = src.get("source")
        if not isinstance(source, dict) or source.get("type") != "model_review":
            continue  # 非 review 语境不受此约束 (见上 docstring)
        if _event_review_unit_id(e) is None:
            out.append(InvariantViolation(
                "no_finding_misfire",
                f"FindingCreated {e.get('event_id')} 无 review_unit_id/provenance.session_id (归属悬空)",
                {"event_id": e.get("event_id")},
            ))
            continue
        ru_raw = src.get("review_unit_id")
        ru = ru_raw if isinstance(ru_raw, str) and ru_raw else None
        prov = e.get("provenance") or {}
        sess_raw = prov.get("session_id") if isinstance(prov, dict) else None
        sess = sess_raw if isinstance(sess_raw, str) and sess_raw else None
        if ru is not None and sess is not None and ru != sess:
            out.append(InvariantViolation(
                "no_finding_misfire",
                f"FindingCreated {e.get('event_id')} review_unit_id={ru!r} != "
                f"provenance.session_id={sess!r} (归属分裂/错挂)",
                {"event_id": e.get("event_id"), "review_unit_id": ru, "session_id": sess},
            ))
    return out


def check_no_ledger_corruption(events: list[dict]) -> list[InvariantViolation]:
    """SEQ 连续无断裂(gap)/无重号(同 seq 两条)/无覆盖(同 seq 异 event_id)。对照三次账本回退指纹。"""
    by_seq: dict[int, set] = defaultdict(set)
    for e in events:
        seq = e.get("sequence_number")
        if seq is not None:
            by_seq[seq].add(e.get("event_id"))
    out: list[InvariantViolation] = []
    for seq, eids in by_seq.items():
        if len(eids) > 1:
            out.append(InvariantViolation("no_ledger_corruption", f"seq {seq} 有 {len(eids)} 个不同 event (重号/覆盖)", {"seq": seq}))
    if by_seq:
        lo, hi = min(by_seq), max(by_seq)
        missing = [s for s in range(lo, hi + 1) if s not in by_seq]
        if missing:
            out.append(InvariantViolation("no_ledger_corruption", f"SEQ 断裂: 缺 {missing[:10]}{'...' if len(missing) > 10 else ''}", {"missing_count": len(missing)}))
    return out


def check_static_invariants(events: list[dict]) -> list[InvariantViolation]:
    """三条可静态查的不变量汇总 (无锁死那条由 stress runner 计时持有, 不在此冒充)。全绿=[] 。"""
    return check_no_twin(events) + check_no_finding_misfire(events) + check_no_ledger_corruption(events)
