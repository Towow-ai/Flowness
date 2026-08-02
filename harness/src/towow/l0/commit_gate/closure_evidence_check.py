"""T-DEC-2 closure-evidence-verification-gate@v1 (anti-fake-done 铁约束): commit gate 对
TaskNodeClosed 事件加一道 blocking_check —— 防止 task 关闭沦为"假装做完"后门 (owner 铁约束:
"这个关闭绝不能变成新的假装做完后门——必须带可核证据并经独立核验")。

按 reason 分派两条证据契约 (fail-closed on unknown reason):

════ reason=done_elsewhere ════
被派 task 其实已在别处做完。凭证不是 "TaskNodeClosed 自报"、更不是 "superseded_by 非空", 而是
一个【由独立审计 fork 产出、可追溯到锚定被关 task 的 AuditTriggered】的正向 verdict:

(1) superseded_by 结构解析 (necessary but insufficient): superseded_by.ref_id 解析到一个真实、
    canonical 可查的 commit 或 finding。空 / 不可解析 → 拒。
    ref_type=commit 的可解析性有两条 OR 路径 (audit-grounded-commit-resolvability@v1, 只加不减、独立性
    不松): 原路径 = commit 作为 worktree_commit_sha/integration_commit_sha 出现在某已提交事件
    (_commit_ref_resolvable, 逐字不变); OR 新增 audit-grounded 路径 = 存在一条锚定本次关闭 (task_id,
    commit) 的 sanctioned 闭合审计链 (sanctioned+affirmative AuditVerdictReceived → traceable
    AuditTriggered → task-anchor → envelope_event_id==commit), 复用与检查 (2b)/(2c)/(2d)/(2e) 完全同一
    helper —— 即 owner 直接 git commit 未走官方注册路径, 也凭审计背书认它 canonical 可解析
    (_audit_grounded_commit_resolvable)。

(2) verification_verdict_ref 解析到真实、正向、sanctioned、可追溯、锚定本 task、对账本交付物的 verdict:
    - resolvable + affirmative: 已提交事件, 且是被识别的 verify/audit verdict 且裁决为正向;
    - sanctioned: verdict 由 sanctioned 独立审计 fork 产 (provenance actor_type=commit_gate,
      actor_id=m05_audit_fork, skill_id=audit) —— 手写非 sanctioned verdict 一律拒;
    - traceable: verdict.after_state.trigger_event_id 反查到一条真 AuditTriggered;
    - task-anchor: 那条 AuditTriggered 的 subjects 锚定被关 task (entity_type=task, entity_id=task_id)
      —— 这才是真"covers-task": 锚在 AuditTriggered (task-scoped 审计原语产) 而非 verdict.subjects
      (真 audit-fork verdict 的 subjects=[CONCEPT commit_gate], 从不含 task);
    - artifact-match: 那条 AuditTriggered 审的 envelope_event_id == 本次关闭声明的 superseded_by.ref_id
      (审的正是被声明为交付物的那件东西; closer 自洽, 非防伪 —— 防伪权重在写边界锁 + 多事件交叉引用)。

    独立性不再靠"verdict.session != closer"钉 (真 audit-fork verdict 的 provenance.session_id=None,
    旧检查只能被伪造假 session id 满足 → 方向反了)。改由结构保证: verdict 必须是 sanctioned
    audit-fork (独立 claude -p 产) + 可追溯到锚被关 task 的 AuditTriggered。

(2f) actor != fixer (functional-equivalence-closure-criterion@v1 独立性扩展): 在 (2) 的独立性判断上
    再补一层显式 session 排除 —— verdict.actor_session 既 != closer(closed_by) 也 != fixer(产出被引作
    superseded_by 内容的 session, 经 resolve_superseding_content_author_session 反查)。sanctioned
    audit-fork 的 verdict_actor_session 恒 None, 本检查对它天然通过 (由 (2) 的结构性保证兜底); 该检查
    真正的着力点是未来可能接入的"soft evidence"verdict 路径 (evidence_strength=soft, agent 实查
    结论经 agent_verdict_ref 持久化) —— 那类 verdict 的 actor_session 是真实会话, 必须显式排除
    closer/fixer 才不被自证。可复用 helper: verdict_actor_session_excludes_closer_and_fixer (BATCH-MECH
    的 WorktreeClosed 会复用同一判据, 工位级与任务级两道门须一致不打架)。merge-base 祖先关系单独
    (无以上任一独立 verdict) 从不构成合法闭合证据 (非必要非充分)。

════ reason=retired ════
GOAL 前提为假、无 delivering envelope 的诚实终态。证据契约完全不同 (不要 delivering commit /
不要 audit verdict): superseded_by 必为 finding, 且该 finding 须 (a) finding_kind=premise_false 的
FindingCreated 且 subjects 锚定被退役 task (kind 挡"代码 bug finding 冒充", task-subject 挡"跨 task
复用一个已 verified 的 premise_false finding 去关无关 task" —— 二者缺一不可); (b) 有一个独立
(session != closer) 的 verification_state=verified 的 FindingVerified (经 verify-step 证伪确认前提确假)。

════ 其余 reason ════
未识别 reason → fail-closed 拒 (anti-fake-done 门里 unknown reason 绝不 fall-through 当 done_elsewhere)。

无证据、不可核验、或自核的关闭一律被拒。拒绝 = 整批 envelope 被拒 → TaskNodeClosed 不落账 →
被关闭 task 留 open。门是 readyset-closure-exclusion-contract 的承重前提。

实现是 schema-independent 的 (镜像 review_verdict_check): 用字符串事件类型 + dict payload 访问,
不 import T-DEC-1 的 TaskNodeClosed EventType/Payload —— 本门自包含, dormant 到 schema 跟上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex
    from towow.schemas.event_intent import EventIntent
    from towow.schemas.event_record import EventRecord

_TASK_NODE_CLOSED = "TaskNodeClosed"
_FINDING_CREATED = "FindingCreated"
_FINDING_VERIFIED = "FindingVerified"
_NODE_TOUCHED = "NodeTouched"
_AUDIT_VERDICT_RECEIVED = "AuditVerdictReceived"
_AUDIT_TRIGGERED = "AuditTriggered"

_REF_TYPE_COMMIT = "commit"
_REF_TYPE_FINDING = "finding"
_TASK_SUBJECT_ENTITY_TYPE = "task"
_COMMIT_REF_KEYS = ("worktree_commit_sha", "integration_commit_sha")

_RETIRED = "retired"
_DONE_ELSEWHERE = "done_elsewhere"
_PREMISE_FALSE_KIND = "premise_false"

# sanctioned 独立审计 fork 的 provenance 三元 (build_audit_verdict_intent, l1/audit_fork.py):
# actor_type=commit_gate / actor_id=m05_audit_fork / skill_id=audit。手写非 sanctioned verdict 不满足。
_SANCTIONED_AUDIT_ACTOR_TYPE = "commit_gate"
_SANCTIONED_AUDIT_ACTOR_ID = "m05_audit_fork"
_SANCTIONED_AUDIT_SKILL_ID = "audit"

# 正向裁决口径 (镜像 enums.AuditVerdictValue / FindingVerified.verification_state)。
_AFFIRMATIVE_AUDIT_VERDICTS = frozenset({"pass", "conditional_pass"})
_AFFIRMATIVE_VERIFICATION_STATE = "verified"


@dataclass(frozen=True)
class ClosureEvidenceResult:
    """gate closure-evidence 检查结果 (镜像 review_verdict_check.ReviewVerdictResult 形态)。"""

    passed: bool
    applicable: bool = False  # 本批是否含 TaskNodeClosed (供 checks_performed 标 not_applicable)
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)


def _unwrap_stub_rewrap(rec: EventRecord) -> tuple[str, dict[str, Any]]:
    """(effective_event_type, payload), 还原 NodeTouched stub-rewrap (镜像 review_verdict_check)。

    canonical verdict / PatchProposed / FindingCreated 不走 stub-rewrap (生产实证), 此 unwrap 仅为
    防御性兜底 —— 万一某路径把事件包成 NodeTouched(kind=...), 仍能取到真 event_type + payload。
    """
    payload = rec.payload if isinstance(rec.payload, dict) else {}
    if rec.event_type.value == _NODE_TOUCHED:
        kind = payload.get("kind")
        orig = payload.get("stub_original_payload")
        if isinstance(kind, str) and isinstance(orig, dict):
            return kind, orig
    return rec.event_type.value, payload


def _commit_ref_resolvable(committed_records: list[EventRecord], commit_sha: str) -> bool:
    """commit_sha 是否在已提交账本里作为真实 commit ref 出现 (event-sourced 解析, 不碰 git)。

    扫已提交事件的 payload (含 after_state 与 stub-rewrap 还原): 任一事件把 commit_sha 作为
    worktree_commit_sha / integration_commit_sha 携带 → 解析为真 (该 commit 是系统真产出/接受过的)。
    """
    for rec in committed_records:
        _etype, payload = _unwrap_stub_rewrap(rec)
        after = payload.get("after_state")
        sources = [payload, after if isinstance(after, dict) else {}]
        for src in sources:
            for key in _COMMIT_REF_KEYS:
                val = src.get(key)
                if isinstance(val, str) and val and val == commit_sha:
                    return True
    return False


def _finding_resolvable(index: EventIndex, finding_id: str) -> bool:
    """finding_id 是否有一个已提交的 FindingCreated 承接它 (经 entity 索引解析)。"""
    for rec in index.lookup_entity("finding", finding_id):
        etype, _payload = _unwrap_stub_rewrap(rec)
        if etype == _FINDING_CREATED:
            return True
    return False


def _verdict_is_affirmative(event_type: str, payload: dict[str, Any]) -> bool | None:
    """识别的 verify/audit verdict 是否正向。返回 None = 不可识别的 verdict 类型 (fail-closed)。"""
    after = payload.get("after_state")
    after = after if isinstance(after, dict) else {}
    if event_type == _AUDIT_VERDICT_RECEIVED:
        return after.get("verdict") in _AFFIRMATIVE_AUDIT_VERDICTS
    if event_type == _FINDING_VERIFIED:
        # verification_state 在 FindingVerified payload【顶层】(FindingVerifiedPayload, 非 after_state);
        # 先读顶层, 再兜底 after (历史/stub 形态)。旧代码只读 after → 恒 None → done_elsewhere 若拿
        # FindingVerified 当 verdict 静默死角; 此处收敛与 retired 分支同一读法。
        state = payload.get("verification_state")
        if state is None:
            state = after.get("verification_state")
        return state == _AFFIRMATIVE_VERIFICATION_STATE
    return None


def resolve_superseding_content_author_session(
    committed_records: list[EventRecord], index: EventIndex, ref_type: str, ref_id: str,
) -> str | None:
    """"fixer"(受益方 — 产出被引作 superseded_by 内容的 session) 的 session_id, 从 ref 反查其 provenance。

    functional-equivalence-closure-criterion@v1 的独立性扩展 (在既有 actor != closer 上补 actor !=
    fixer): 修复者与闭合者不必是同一 session, 只挡 closer 挡不住修复者自证"我的修复已等价落地"。

    ref_type=commit: 找到把 ref_id 作为 worktree_commit_sha/integration_commit_sha 携带的已提交事件
    (镜像 _commit_ref_resolvable 的扫法), 读其 provenance.session_id。
    ref_type=finding: 找到该 finding_id 的 FindingCreated, 读其 provenance.session_id。
    查不到 / session_id 缺失 → None (无法解析 fixer, 调用方对 None 不做排除比较, 不因缺失而误拒)。
    """
    if ref_type == _REF_TYPE_COMMIT:
        for rec in committed_records:
            _etype, payload = _unwrap_stub_rewrap(rec)
            after = payload.get("after_state")
            sources = [payload, after if isinstance(after, dict) else {}]
            for src in sources:
                for key in _COMMIT_REF_KEYS:
                    val = src.get(key)
                    if isinstance(val, str) and val and val == ref_id:
                        sess = getattr(rec.provenance, "session_id", None)
                        return sess if isinstance(sess, str) and sess else None
        return None
    if ref_type == _REF_TYPE_FINDING:
        for rec in index.lookup_entity("finding", ref_id):
            etype, _payload = _unwrap_stub_rewrap(rec)
            if etype == _FINDING_CREATED:
                sess = getattr(rec.provenance, "session_id", None)
                return sess if isinstance(sess, str) and sess else None
        return None
    return None


def verdict_actor_session_excludes_closer_and_fixer(
    verdict_actor_session: str | None, *, closer_session: str | None, fixer_session: str | None,
) -> bool:
    """actor≠fixer 独立性判定 (可复用 helper — BATCH-MECH 的 WorktreeClosed 会复用同一判据, 工位级与
    任务级两道门对同一等价证据 ref 判定须一致、不打架)。

    verdict 的 actor_session 必须【同时】≠ closer_session (关闭者, 既有约束) 且 ≠ fixer_session (受益方,
    本契约新增约束) 才算独立。verdict_actor_session=None (sanctioned audit fork 的结构性独立标志——真
    audit-fork verdict 的 provenance.session_id 恒 None, 由其 provenance 结构本身保证独立, 非靠 session
    比较) 或 closer_session/fixer_session=None (反查不到、无可比较对象) 均不判否——None 从不等于任何真实
    session 字符串, 不能因缺失比较对象而误拒。

    ⚠️ 消费点契约 (finding f-debt-gate-fixer-exclusion-failopen-on-test-ledger-reftype):
    本 helper 对 verdict_actor_session=None / fixer_session=None 的"不判否"只在 verdict 的 session
    【结构性】为 None (sanctioned audit-fork, 由 provenance 结构保证独立) 时才安全。任何喂进【携带自报
    会话身份】verdict (如 FindingVerified, session 可自报) 的消费点, 必须在调用本 helper 之前自行加一道
    fail-closed 前检: verdict 非 sanctioned audit-fork 时, actor_session=None 或 fixer_session=None
    都要拒 (unknown-fixer != safe) —— 否则修复者可用自产 verdict + 反查不到的 fixer 自证闭合。
    event_log.write_debt_resolved 已按此分流; 未来接入 soft-evidence 的 (2f) 路径会继承同隐患, 须同办。
    """
    if verdict_actor_session is None:
        return True
    if closer_session is not None and verdict_actor_session == closer_session:
        return False
    return not (fixer_session is not None and verdict_actor_session == fixer_session)


def _verdict_is_sanctioned_audit_fork(rec: EventRecord) -> bool:
    """verdict 是否由 sanctioned 独立审计 fork 产 (provenance 三元 == build_audit_verdict_intent)。

    真 audit-fork verdict: actor_type=commit_gate / actor_id=m05_audit_fork / skill_id=audit。
    手写非 sanctioned verdict (35 个实证形态) actor 不是这三值 → 拒。

    R1 封 (stub-rewrap 换壳伪造): 还要求 rec 的【原始】event_type 是扁平 AuditVerdictReceived —— 真
    audit-fork verdict 经 EventLog._write_audit_event 写成扁平, **从不** stub-rewrap 成
    NodeTouched(kind=AuditVerdictReceived)。攻击者把伪造 verdict 包成 NodeTouched 经 envelope 提交
    (写边界按 event_type=NodeTouched 放行), 此处按原始 event_type 拒之: 不采信 stub-rewrap 还原出来的
    审计 verdict。故绝不在此 _unwrap_stub_rewrap —— 一 unwrap 就等于信了伪造外壳。
    """
    if rec.event_type.value != _AUDIT_VERDICT_RECEIVED:
        return False
    p = rec.provenance
    return (
        getattr(p, "actor_type", None) == _SANCTIONED_AUDIT_ACTOR_TYPE
        and getattr(p, "actor_id", None) == _SANCTIONED_AUDIT_ACTOR_ID
        and getattr(p, "skill_id", None) == _SANCTIONED_AUDIT_SKILL_ID
    )


def _resolve_traceable_audit_triggered(
    index: EventIndex, verdict_payload: dict[str, Any],
) -> tuple[EventRecord, dict[str, Any]] | None:
    """从 verdict.after_state.trigger_event_id 反查真 AuditTriggered, 返回 (trig_rec, trig_after)。

    verdict.after_state.trigger_event_id 指向触发本次审计的 AuditTriggered (audit_fork.py 契约);
    该事件不存在 / 不是 AuditTriggered → None (不可追溯 → fail-closed)。
    """
    after = verdict_payload.get("after_state")
    after = after if isinstance(after, dict) else {}
    trig_id = after.get("trigger_event_id")
    if not isinstance(trig_id, str) or not trig_id:
        return None
    rec = index.lookup_event_id(trig_id)
    if rec is None:
        return None
    # R1 封: 要求【原始】扁平 AuditTriggered, 不 _unwrap_stub_rewrap —— 真 audit fork 的 AuditTriggered
    # 经 commit-gate / _write_audit_event 写成扁平; 只有伪造者把它包成 NodeTouched(kind=AuditTriggered)
    # 塞进 envelope。按原始 event_type 拒 stub-rewrap 还原出来的 trigger。
    if rec.event_type.value != _AUDIT_TRIGGERED:
        return None
    tpayload = rec.payload if isinstance(rec.payload, dict) else {}
    trig_after = tpayload.get("after_state")
    return rec, trig_after if isinstance(trig_after, dict) else {}


def _audit_triggered_anchors_task(trig_rec: EventRecord, task_id: str) -> bool:
    """AuditTriggered 的 subjects 是否锚定被关 task (entity_type=task, entity_id=task_id, role 不限)。

    envelope-scoped 审计的 TASK subject 是被审 envelope 自己的 task (必 != 被关 task) → 天然排除;
    只有 closure-scoped 审计原语 (towow audit-closure) 产的 AuditTriggered 才锚被关 task。
    """
    return any(
        s.entity_type.value == _TASK_SUBJECT_ENTITY_TYPE and s.entity_id == task_id
        for s in trig_rec.subjects
    )


def _verdict_subjects_task(rec: EventRecord, task_id: str) -> bool:
    """事件是否在 subjects 里引用被关闭的 task (entity_type=task, entity_id=task_id)。

    retired 分支复用它做 premise-false FindingCreated 的 task 锚定检查 (防跨 task 复用)。
    """
    for subject in rec.subjects:
        if subject.entity_type.value == _TASK_SUBJECT_ENTITY_TYPE and subject.entity_id == task_id:
            return True
    return False


def _retired_premise_false_verified(
    index: EventIndex, ref_id: str, task_id: str, closed_by: str,
) -> tuple[bool, str, dict[str, str]]:
    """retired 证据链核验。返回 (ok, failure_reason, failure_evidence)。

    ref_id 指向的 finding 须:
      (a) finding_kind=premise_false 的 FindingCreated, 且 subjects 锚定被退役 task (防跨 task 复用);
      (b) 有一个 verification_state=verified 的 FindingVerified;
      (c) 该 FindingVerified 的 session != closer (退役者不能自证前提为假)。
    """
    premise_false_anchored = False
    verified_session: str | None = None
    for rec in index.lookup_entity("finding", ref_id):
        etype, payload = _unwrap_stub_rewrap(rec)
        if (
            etype == _FINDING_CREATED
            and payload.get("finding_kind") == _PREMISE_FALSE_KIND
            and _verdict_subjects_task(rec, task_id)
        ):
            premise_false_anchored = True
        if (
            etype == _FINDING_VERIFIED
            and payload.get("verification_state") == _AFFIRMATIVE_VERIFICATION_STATE
        ):
            sess = getattr(rec.provenance, "session_id", None)
            if isinstance(sess, str) and sess:
                verified_session = sess
    if not premise_false_anchored:
        return False, "closure_retired_finding_not_premise_false", {
            "task_id": task_id, "ref_id": ref_id,
            "reason": "superseded_by 指向的 finding 不是 premise_false FindingCreated,"
            " 或其 subjects 未锚定被退役 task (防跨 task 复用一个 premise_false finding 去关无关 task)",
        }
    if verified_session is None:
        return False, "closure_retired_not_verify_confirmed", {
            "task_id": task_id, "ref_id": ref_id,
            "reason": "premise-false finding 无 verified 的 FindingVerified (未经 verify-step 证伪确认前提为假)",
        }
    if verified_session == closed_by:
        return False, "closure_retired_self_verified", {
            "task_id": task_id, "verified_session": verified_session, "closed_by": closed_by,
            "reason": "verify-step 会话 == closer, 退役者自证前提为假, 违独立性",
        }
    return True, "", {}


def _closed_after_states(domain_intents: list[EventIntent]) -> list[dict[str, Any]]:
    """本批 TaskNodeClosed intent 的 after_state dict 列表 (schema-independent: 字符串 + dict)。

    f-stub-rewrap-close-bypasses-closure-evidence-gate-1: 也 unwrap stub-rewrap
    NodeTouched(kind=TaskNodeClosed) intent —— 以 stub 形式提交的关闭必须面对**同一**门, 不能因
    event_type=NodeTouched 就被跳过 (否则它逃过验证)。派发层 closed_task_ids 已用「原始 event_type
    只认扁平关闭」拒绝把 stub 关闭折入 satisfied; 此处补另一半, 让 stub 关闭连门都过不去。
    """
    out: list[dict[str, Any]] = []
    for intent in domain_intents:
        etype = intent.event_type.value
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        if etype == _NODE_TOUCHED:
            kind = payload.get("kind")
            orig = payload.get("stub_original_payload")
            if isinstance(kind, str) and isinstance(orig, dict):
                etype = kind
                payload = orig
        if etype != _TASK_NODE_CLOSED:
            continue
        after = payload.get("after_state")
        out.append(after if isinstance(after, dict) else {})
    return out


def _audit_grounded_commit_resolvable(index: EventIndex, task_id: str, commit_ref: str) -> bool:
    """audit-grounded-commit-resolvability@v1 的 OR 路径: 一条锚定 (task_id, commit_ref) 的 sanctioned
    闭合审计链是否把该 commit 判为 canonical 可解析 (owner 直接 git commit 未走官方 worktree/integration
    注册路径时的兜底)。仅当 _commit_ref_resolvable 直命中失败后, 才在检查 (1) 作为 OR 路径求值——只加不减。

    独立性一分不松、谓词与门检查 (2b)/(2c)/(2d)/(2e) 复用【完全同一】helper (非旁路更松实现)。审计链
    谓词四条全中才判可解析:
      (a) sanctioned + affirmative: 一条【原始扁平】AuditVerdictReceived, provenance 三元 == sanctioned
          audit-fork (_verdict_is_sanctioned_audit_fork —— 内含 R1: 拒 stub-rewrap 换壳、不 _unwrap),
          且 verdict ∈ {pass, conditional_pass} (_verdict_is_affirmative);
      (b) traceable: verdict.after_state.trigger_event_id 反查到一条真扁平 AuditTriggered
          (_resolve_traceable_audit_triggered);
      (c) task-anchor: 该 AuditTriggered.subjects 含 (task, task_id) (_audit_triggered_anchors_task) ——
          真 covers-task 锚在 AuditTriggered, 非 verdict.subjects;
      (d) artifact-match: 该 AuditTriggered.after_state.envelope_event_id == commit_ref (审的正是这个 commit)。
    任一不中 → False (该 done_elsewhere 裸 commit 仍走 (1) 层 closure_superseded_by_unresolvable 拒)。

    单腿 grounding (共识 caveat same-audit-dual-leg-collapse): 走本路径时检查 (1) 与门检查 (2) 共同依赖
    同一条 audit 事件, 证据模型由"双腿"收敛为"单腿(audit)"。不破 anti-fake-done —— 禁自核靠 (2f)
    actor!=closer/fixer + sanctioned audit-fork 结构性 provenance.session_id 恒 None (fork 结构本身保证
    独立); artifact-match + task-anchor 又挡"复用无关 audit 关无关 task/commit"。
    """
    for verdict_rec in index.lookup_type(_AUDIT_VERDICT_RECEIVED):
        # (a) sanctioned audit-fork (按【原始】event_type 判, 绝不 _unwrap_stub_rewrap) + affirmative
        if not _verdict_is_sanctioned_audit_fork(verdict_rec):
            continue
        verdict_payload = verdict_rec.payload if isinstance(verdict_rec.payload, dict) else {}
        if _verdict_is_affirmative(_AUDIT_VERDICT_RECEIVED, verdict_payload) is not True:
            continue
        # (b) traceable → 真扁平 AuditTriggered
        resolved = _resolve_traceable_audit_triggered(index, verdict_payload)
        if resolved is None:
            continue
        trig_rec, trig_after = resolved
        # (c) task-anchor + (d) artifact-match
        if _audit_triggered_anchors_task(trig_rec, task_id) and trig_after.get("envelope_event_id") == commit_ref:
            return True
    return False


def _check_done_elsewhere(
    after: dict[str, Any],
    task_id: str,
    index: EventIndex,
    committed_records: list[EventRecord],
) -> ClosureEvidenceResult | None:
    """done_elsewhere 关闭的证据契约。满足 → None (放行); 任一不满足 → fail-closed ClosureEvidenceResult。"""
    # ── (1) superseded_by 结构解析 (necessary but insufficient) ──
    superseded_by = after.get("superseded_by")
    superseded_by = superseded_by if isinstance(superseded_by, dict) else {}
    ref_type = superseded_by.get("ref_type")
    ref_id = superseded_by.get("ref_id")
    if not isinstance(ref_id, str) or not ref_id or not isinstance(ref_type, str):
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_superseded_by_unresolvable",
            failure_evidence={
                "task_id": task_id,
                "reason": "superseded_by.ref_id / ref_type 缺失 — 关闭必须带可核取代物 (commit/finding)",
            },
        )
    if ref_type == _REF_TYPE_COMMIT:
        # audit-grounded-commit-resolvability@v1: 原路径 (worktree/integration 注册, _commit_ref_resolvable
        # 逐字不变) 直命中失败时, OR 一条锚定 (task, commit) 的 sanctioned 闭合审计链 —— 只加不减、三重锁不松。
        resolvable = _commit_ref_resolvable(committed_records, ref_id) or _audit_grounded_commit_resolvable(
            index, task_id, ref_id,
        )
    elif ref_type == _REF_TYPE_FINDING:
        resolvable = _finding_resolvable(index, ref_id)
    else:
        resolvable = False
    if not resolvable:
        # 拒绝须【可操作】(finding f-closure-gate-canonical-sha-only-blocks-worktrees-20260718): 门只认
        # 被 canonical 事件记录过的 commit SHA (event-sourced, 不碰 git —— 这是 anti-fake-done 铁约束, 见
        # _commit_ref_resolvable), 但历史上多个工位 (S2-M12/S3-REAPER/S4-DEADLETTER/S4-PHYS-MATCHER) 拿
        # 看板自标的 git/工位 SHA 收口、连撞这句不可解析拒绝、反复重关 (账本实证同一 task 关 3 次)。语义
        # 不改 (什么该拒仍原样), 只把源 finding 手写进 finding 文本的补救指引搬到失败发生的这一刻, 让 closer
        # 自助改法而非再靠人读 finding。
        if ref_type == _REF_TYPE_COMMIT:
            reason = (
                "superseded_by 的 commit SHA 在 canonical 账本里不可解析 — 结构层 fail-closed。本门只认被系统"
                "事件记录过的 canonical commit SHA (某已提交事件把它作为 worktree_commit_sha /"
                " integration_commit_sha 携带), 不解析 git 层 SHA (event-sourced, 不碰 git —— anti-fake-done"
                " 铁约束: 看板自标的 git/工位 SHA、乃至 main 祖先 SHA 都证明不了本 task 已做完)。"
            )
            remediation = (
                "改用真 canonical 事件记录过的 worktree_commit_sha/integration_commit_sha 重收口, 或用"
                " --superseded-by-ref-type finding 指向承接本 task 的 FindingCreated 收口。"
            )
        elif ref_type == _REF_TYPE_FINDING:
            reason = (
                "superseded_by 的 finding 在 canonical 账本里没有承接它的 FindingCreated — 结构层 fail-closed。"
            )
            remediation = (
                "确认 finding_id 正确且该 finding 已有一条已提交 FindingCreated; 若尚未立案, 先 emit"
                " FindingCreated 再收口。"
            )
        else:
            reason = (
                f"superseded_by.ref_type={ref_type!r} 不是可解析类型 (仅认 commit / finding) — fail-closed。"
            )
            remediation = "把 ref_type 改成 commit 或 finding 之一, 并指向真 canonical 取代物。"
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_superseded_by_unresolvable",
            failure_evidence={
                "task_id": task_id, "ref_type": ref_type, "ref_id": ref_id,
                "reason": reason, "remediation": remediation,
            },
        )

    # ── (2) verification_verdict_ref: resolvable + affirmative ──
    verdict_ref = after.get("verification_verdict_ref")
    if not isinstance(verdict_ref, str) or not verdict_ref:
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_verdict_unresolvable",
            failure_evidence={
                "task_id": task_id,
                "reason": "verification_verdict_ref 缺失 — 关闭必须经独立核验 (owner 铁约束)",
            },
        )
    verdict_rec = index.lookup_event_id(verdict_ref)
    if verdict_rec is None:
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_verdict_unresolvable",
            failure_evidence={
                "task_id": task_id, "verification_verdict_ref": verdict_ref,
                "reason": "verification_verdict_ref 不指向任何已提交事件 — 不可解析的 verdict, fail-closed",
            },
        )
    verdict_type, verdict_payload = _unwrap_stub_rewrap(verdict_rec)
    affirmative = _verdict_is_affirmative(verdict_type, verdict_payload)
    if affirmative is not True:
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_verdict_not_affirmative",
            failure_evidence={
                "task_id": task_id, "verification_verdict_ref": verdict_ref, "verdict_type": verdict_type,
                "reason": "verification_verdict_ref 指向的事件不是正向的 verify/audit verdict — fail-closed",
            },
        )

    # ── (2b) sanctioned: verdict 由独立审计 fork 产 (非手写伪造) ──
    if not _verdict_is_sanctioned_audit_fork(verdict_rec):
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_verdict_not_sanctioned_audit_fork",
            failure_evidence={
                "task_id": task_id, "verification_verdict_ref": verdict_ref,
                "reason": "verdict 不是 sanctioned 独立审计 fork 产 (provenance actor 不是 "
                "commit_gate/m05_audit_fork/skill=audit) — 手写非 sanctioned verdict 一律拒",
            },
        )

    # ── (2c) traceable: verdict 反查到真 AuditTriggered ──
    resolved = _resolve_traceable_audit_triggered(index, verdict_payload)
    if resolved is None:
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_verdict_no_traceable_audit_triggered",
            failure_evidence={
                "task_id": task_id, "verification_verdict_ref": verdict_ref,
                "reason": "verdict.after_state.trigger_event_id 不指向任何真 AuditTriggered — 不可追溯, fail-closed",
            },
        )
    trig_rec, trig_after = resolved

    # ── (2d) task-anchor: AuditTriggered 的 TASK subject == 被关 task (真 covers-task) ──
    if not _audit_triggered_anchors_task(trig_rec, task_id):
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_audit_triggered_does_not_anchor_task",
            failure_evidence={
                "task_id": task_id, "verification_verdict_ref": verdict_ref,
                "audit_triggered_event_id": trig_rec.event_id,
                "reason": "verdict 追溯到的 AuditTriggered 未在 subjects 锚定被关 task — 它审的是别处的 "
                "envelope, 不是本 task 的 closure (真 covers-task 锚在 AuditTriggered, 非 verdict.subjects)",
            },
        )

    # ── (2e) artifact-match: AuditTriggered 审的 envelope_event_id == superseded_by.ref_id ──
    # closer 自洽 (closer 同时供两值), 非防伪 — 防伪权重在写边界锁 + 上面多事件交叉引用一致性。
    trig_envelope = trig_after.get("envelope_event_id")
    if trig_envelope != ref_id:
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_audit_triggered_artifact_mismatch",
            failure_evidence={
                "task_id": task_id, "ref_id": ref_id,
                "audit_triggered_envelope_event_id": str(trig_envelope),
                "reason": "AuditTriggered 审的 envelope_event_id != closure 声明的 superseded_by.ref_id "
                "(审的不是被声明为交付物的那件东西) — fail-closed",
            },
        )

    # ── (2f) actor != fixer: functional-equivalence-closure-criterion@v1 在既有 actor != closer 上
    # 补的独立性扩展 —— 挡 closer 挡不住修复者自证"我的修复已等价落地"。sanctioned audit-fork verdict
    # 的 provenance.session_id 恒 None (由 fork 结构本身保证独立), 本检查对它天然通过; 它为未来可能
    # 接入的"soft evidence / agent 实查"verdict 路径 (evidence_strength=soft, agent_verdict_ref) 预先
    # 钉死同一独立性判据, 两类 verdict 不因来源不同而独立性标准打架。
    # ⚠️ done_elsewhere 这里的 verdict【结构性】session=None (sanctioned audit-fork), 所以直接复用
    # helper 的 None 短路是安全的, 无需 fail-closed-on-unresolvable-fixer 前检。但这不是 helper 的通用
    # 契约: 携带【自报会话身份】verdict 的消费点 (event_log.write_debt_resolved 喂 FindingVerified)
    # 必须在调 helper 前加 fail-closed 前检 (finding
    # f-debt-gate-fixer-exclusion-failopen-on-test-ledger-reftype)。未来这条 (2f) 若真接入 soft-evidence
    # (agent_verdict_ref 带真实 agent session, 非结构性 None), 会继承同隐患, 届时须比照 debt 门加同款前检。
    verdict_actor_session = getattr(verdict_rec.provenance, "session_id", None)
    closer_session = after.get("closed_by")
    closer_session = closer_session if isinstance(closer_session, str) and closer_session else None
    fixer_session = resolve_superseding_content_author_session(committed_records, index, ref_type, ref_id)
    if not verdict_actor_session_excludes_closer_and_fixer(
        verdict_actor_session, closer_session=closer_session, fixer_session=fixer_session,
    ):
        return ClosureEvidenceResult(
            passed=False, applicable=True,
            failure_reason="closure_verdict_authored_by_closer_or_fixer",
            failure_evidence={
                "task_id": task_id, "verification_verdict_ref": verdict_ref,
                "verdict_actor_session": str(verdict_actor_session),
                "reason": "verdict 的 actor_session 与 closer 或 fixer(产出被引作等价证据内容的 session) "
                "相同 — 违独立性 (functional-equivalence-closure-criterion@v1 actor != fixer 扩展), fail-closed",
            },
        )
    return None


def _owner_confirm_present(after: dict[str, Any], index: EventIndex) -> bool:
    """retired 闭合的 owner-confirm 是否在场且【验签通过、锚定本 (task, finding)】(组件6)。

    退役一个冻结 task 的【前提判定】= Class A owner 决定 (owner 红线: "只有我说的算", autopilot
    撬不开)。真 owner-confirm 不能是可自报 provenance 的普通事件 (红队实证: 自报串可被单人一手伪造),
    必须带一个 agent 算不出来的东西 —— owner 用私钥对【本次退役的具体挑战】(task_id + finding_id +
    red_line_class + nonce) 做的 Ed25519 detached 签名, 由 owner_confirm 模块的钉死公钥验证。

    公钥就位后 (2026-07-03, owner_confirm.py 钉死公钥常量非 None) 这是【活签名门】: 账本里有一条
    验签通过、锚定本 (task, finding) 的 retire owner-confirm → True (真 Ed25519 签名放行); 没有 → False。
    仅当私钥托管未就位 (公钥常量 None) 时才退化为对 agent 全 fail-closed —— 堵死自助退役后门
    (create premise_false → 自报 verified → close retired), 与本函数原 stub 行为一致。
    诚实边界: 不可伪造 ⟺ 私钥在 agent 信封外 + 钉死公钥常量不被换, 见 owner_confirm.py 模块头。
    """
    from towow.l0.commit_gate.owner_confirm import owner_confirm_present_for_retire

    task_id = after.get("task_id")
    sb = after.get("superseded_by")
    finding_id = sb.get("ref_id") if isinstance(sb, dict) else None
    if not isinstance(task_id, str) or not task_id:
        return False
    if not isinstance(finding_id, str) or not finding_id:
        return False
    return owner_confirm_present_for_retire(index, task_id, finding_id)


def check_closure_evidence_verification(
    domain_intents: list[EventIntent],
    index: EventIndex,
    committed_records: list[EventRecord],
) -> ClosureEvidenceResult:
    """TaskNodeClosed 合法 ⟺ 按 reason 满足对应证据契约 (anti-fake-done, fail-closed on unknown)。

    本批无 TaskNodeClosed → applicable=False, passed=True。
    任一 TaskNodeClosed 不满足其 reason 契约 → fail-closed 拒 (整批 envelope 被拒 → task 留 open)。
    """
    after_states = _closed_after_states(domain_intents)
    if not after_states:
        return ClosureEvidenceResult(passed=True, applicable=False)

    for after in after_states:
        task_id = after.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return ClosureEvidenceResult(
                passed=False, applicable=True,
                failure_reason="closure_task_id_missing",
                failure_evidence={"reason": "TaskNodeClosed.after_state 无 task_id, 无法核验关闭"},
            )

        reason = after.get("reason")
        if reason == _RETIRED:
            sb = after.get("superseded_by")
            sb = sb if isinstance(sb, dict) else {}
            ref_type = sb.get("ref_type")
            ref_id = sb.get("ref_id")
            if ref_type != _REF_TYPE_FINDING or not isinstance(ref_id, str) or not ref_id:
                return ClosureEvidenceResult(
                    passed=False, applicable=True,
                    failure_reason="closure_retired_requires_finding",
                    failure_evidence={
                        "task_id": task_id,
                        "reason": "retired 关闭的 superseded_by 必为可解析 finding (premise-false FindingCreated),"
                        " 不要 delivering commit",
                    },
                )
            closed_by = after.get("closed_by")
            if not isinstance(closed_by, str) or not closed_by:
                return ClosureEvidenceResult(
                    passed=False, applicable=True,
                    failure_reason="closure_closed_by_missing",
                    failure_evidence={
                        "task_id": task_id,
                        "reason": "TaskNodeClosed.after_state 无 closed_by — 独立性无对照锚, fail-closed",
                    },
                )
            # R2 封 (红队实证 retired 可被单人零验证伪造: create premise_false → 自报 finding-verify
            # verified → close, 三条 CLI 零真实证伪就把不想做的 task 关成退役; 独立性判据 review≠plan
            # 天然满足): 退役一个冻结 task 的【前提判定】是 Class A owner 决定 (owner 红线: "只有我说的算",
            # autopilot 撬不开)。retired 闭合必须带 owner-confirm 物理凭证 (agent 伪造不了)。owner-confirm
            # 机制 (组件6) 公钥就位后 (2026-07-03) 是活签名门: 有验签通过、锚定本 (task, finding) 的真签名才放行,
            # 无则拒 —— premise_false 疑点经路由 surface 到 Nature dashboard, 由 owner 决定并 owner-confirm 后才能
            # 真退役 (仅私钥托管未就位时才退化为一律 fail-closed)。先前的 _retired_premise_false_
            # verified (finding_kind + FindingVerified + session!=closer) 全是可自造的自报串, 不作数。
            ok, freason, fev = _retired_premise_false_verified(index, ref_id, task_id, closed_by)
            if not ok:
                return ClosureEvidenceResult(
                    passed=False, applicable=True, failure_reason=freason, failure_evidence=fev,
                )
            if not _owner_confirm_present(after, index):
                return ClosureEvidenceResult(
                    passed=False, applicable=True,
                    failure_reason="closure_retired_requires_owner_confirm",
                    failure_evidence={
                        "task_id": task_id,
                        "reason": "退役 = Class A owner 决定 (owner 红线, autopilot 撬不开)。retired 闭合必须带一条"
                        " 验签通过、锚定本 (task, finding) 的 owner-confirm 物理凭证 (真 Ed25519 签名, agent 伪造不了);"
                        " 账本无此凭证 → 拒 (公钥就位后由真签名放行, 仅私钥托管未就位时才退化为一律 fail-closed)。"
                        " premise_false 疑点应 surface 到 Nature dashboard 由 owner 决定+确认。",
                    },
                )
            continue

        if reason != _DONE_ELSEWHERE:
            return ClosureEvidenceResult(
                passed=False, applicable=True,
                failure_reason="closure_unknown_reason",
                failure_evidence={
                    "task_id": task_id,
                    "reason": f"未识别的 closure reason={reason!r} — anti-fake-done 门 fail-closed 拒",
                },
            )

        rejection = _check_done_elsewhere(after, task_id, index, committed_records)
        if rejection is not None:
            return rejection

    return ClosureEvidenceResult(passed=True, applicable=True)


__all__ = [
    "ClosureEvidenceResult",
    "check_closure_evidence_verification",
    "resolve_superseding_content_author_session",
    "verdict_actor_session_excludes_closer_and_fixer",
]
