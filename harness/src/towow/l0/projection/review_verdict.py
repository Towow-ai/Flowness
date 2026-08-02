"""T-LND-03 derived-review-verdict-projection@v1 — verdict 折叠纯函数 (INV-B1-2 / INV-B1-5)。

一个 review-unit 是否通过, 是从它**全部 finding 生命周期事件**按 sequence_number 左折叠出的
派生结论 —— 非独立可写真相源, 是 finding-lifecycle 的纯函数 (确定可重放: 同一组 finding 事件
必折出同一 verdict)。

4 态 (concept state_machine):
  - pending  : 还有 finding 未达 terminal, 结论未定 (初始态)
  - failed   : 存在 verified-FAIL(sev>=major)未 resolved —— FAIL 单调否决态
  - passed   : 所有 finding terminal 且无未解决 verified-FAIL
  - closed   : review 任务以 verdict=passed 合法落账冻结 (由 TaskRunCompleted 在 T-LND-04 加冕,
               不由本折叠产出 —— 本函数只折 pending/failed/passed)

FAIL 单调 (INV-B1-2): 一旦 failed, **唯一**离开路径是那条 fail 被真 resolved → pending (禁
failed→passed 直达边)；弱 PASS (dismiss 别的 finding / 再多 clean finding) 不可把 failed 翻回。
passed 可被后来的 verified-FAIL 反转为 failed (success 可被真 FAIL 推翻)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# severity rank (镜像 l1/review_aggregation._SEVERITY_RANK; 单一阈值定义防漂移)。
# "sev>=major" = fail-eligible = rank>=4 = {critical, major}。
_SEVERITY_RANK: dict[str, int] = {
    "critical": 5,
    "major": 4,
    "minor": 3,
    "purple": 2,
    "observation": 1,
}
_FAIL_SEVERITY_FLOOR = _SEVERITY_RANK["major"]


def _sev_rank(severity: object) -> int:
    """severity → rank。**未知 severity 保守按 fail-eligible(floor) 处理** —— fail-closed: 异常
    severity 不应静默降级成非 fail 而把本该否决的问题放过 (审计洞1)。"""
    return _SEVERITY_RANK.get(str(severity), _FAIL_SEVERITY_FLOOR)

# verification_state canonical 值 (FindingVerificationState): verified=确认真问题 /
# rejected_false_positive=独立 verify 判假驳回 (dismiss) / unverified_inconclusive=未决。
_VS_CONFIRMED = "verified"
_VS_DISMISSED = "rejected_false_positive"

_VERDICT_PENDING = "pending"
_VERDICT_FAILED = "failed"
_VERDICT_PASSED = "passed"


def _is_unresolved_verified_fail(state: Mapping[str, Any]) -> bool:
    """该 finding 当前是"已独立确认为真 + sev>=major + 未 resolved"的否决性 fail。"""
    if state.get("lifecycle") == "resolved":
        return False
    if state.get("verification_state") != _VS_CONFIRMED:
        return False
    return _sev_rank(state.get("severity")) >= _FAIL_SEVERITY_FLOOR


def _is_terminal(state: Mapping[str, Any]) -> bool:
    """finding 已达终态: resolved(已修) 或 verified-rejected_false_positive(独立判假驳回/dismiss)。

    T-LND-06 (INV-B1-6): resolved 只有在该 finding **经过 FindingVerified** (ever_verified) 后才算
    真 terminal —— verify-step 不可跳过, 喂给 verdict 折叠的须是真 terminal 的 finding。未经 verify
    就 resolved (如 --verify-fork-mode inline 降级却跳 verify) → 非 terminal → 挡住 verdict passed。
    """
    if state.get("lifecycle") == "resolved":
        return bool(state.get("ever_verified"))
    return state.get("lifecycle") == "verified" and state.get("verification_state") == _VS_DISMISSED


def _apply_event(states: dict[str, dict[str, Any]], ev: Mapping[str, Any]) -> None:
    fid = ev.get("finding_id")
    if not isinstance(fid, str) or not fid:
        return
    existing = fid in states
    s = states.setdefault(
        fid, {"severity": None, "verification_state": None, "lifecycle": "created", "ever_verified": False}
    )
    et = ev.get("event_type")
    if et == "FindingCreated":
        # severity 单调取最高 + re-Created 不回退 lifecycle —— 防"重新创建并降级 severity / 重置
        # lifecycle"把已确认的大 fail 洗白 (审计洞2)。同一 finding_id 二次 Created 不降级、不退态。
        new_sev = ev.get("severity")
        if new_sev is not None and (s["severity"] is None or _sev_rank(new_sev) > _sev_rank(s["severity"])):
            s["severity"] = new_sev
        if not existing:
            s["lifecycle"] = "created"
    elif et == "FindingVerified":
        s["lifecycle"] = "verified"
        s["verification_state"] = ev.get("verification_state")
        s["ever_verified"] = True  # T-LND-06: 标记 verify-step 真发生过 (resolved 真 terminal 的前提)
    elif et == "FindingDisputed":
        s["lifecycle"] = "disputed"
    elif et == "FindingResolved":
        s["lifecycle"] = "resolved"


def fold_review_verdict(events: Iterable[Mapping[str, Any]]) -> str:
    """把一个 review-unit 的 finding 事件 (FindingCreated/Verified/Disputed/Resolved) 折成 verdict。

    每个 event 是含 event_type / finding_id / sequence_number (+ FindingCreated.severity /
    FindingVerified.verification_state) 的 mapping。按 sequence_number 排序后左折叠 → 确定可重放
    (INV-B1-5: 输入顺序无关, 只认 canonical seq 序)。

    前提: canonical event_log 保证 sequence_number 跨进程唯一 (event_log.py 文件锁), 故同一组
    finding 事件有唯一全序 → 折叠确定。撞 seq 在真实流不可达 (tie-break 用 finding_id 仅作防御)。

    返回 pending | failed | passed。零 finding 事件 = 干净 review = passed。
    """
    evs = sorted(events, key=lambda e: (e.get("sequence_number", 0), str(e.get("finding_id", ""))))
    states: dict[str, dict[str, Any]] = {}
    verdict = _VERDICT_PENDING
    for ev in evs:
        _apply_event(states, ev)
        has_fail = any(_is_unresolved_verified_fail(s) for s in states.values())
        all_terminal = all(_is_terminal(s) for s in states.values())  # 空集真空真
        if has_fail:
            verdict = _VERDICT_FAILED
        elif verdict == _VERDICT_FAILED:
            # failed→pending 是离开 failed 的唯一边 (那条 fail 刚被 resolved); 禁 failed→passed 直达,
            # 待 re-review 的 closure 事件在 pending 态再驱动 →passed。
            verdict = _VERDICT_PENDING
        elif all_terminal:
            verdict = _VERDICT_PASSED
        else:
            verdict = _VERDICT_PENDING
    if not states:
        return _VERDICT_PASSED  # 零 finding = 干净 review = passed
    return verdict


_FINDING_EVENT_TYPES = frozenset(
    # ⚠ FindingClosureContractAmended 刻意不在此集 (f-stale-closure-contract-permanently-unclosable):
    # verdict 折叠只看生命周期事件; 修订闭合合约不改任何 finding 的确认/驳回/闭合结论。
    {"FindingCreated", "FindingVerified", "FindingDisputed", "FindingResolved"}
)


def _event_review_unit_id(ev: Mapping[str, Any]) -> str | None:
    """从一条原始事件 record 取它归属的 review_unit_id: payload 显式 review_unit_id 优先, 回退
    provenance.session_id (一次 review 的全部 finding 事件 —— Created/Verified/Disputed/Resolved
    —— 共享同一 review 会话 session_id)。

    显式字段覆盖面 (T-FIX-B2-06 审查确认 INV-B2-6 provenance 链): FindingCreated 一直显式带
    (=sid); FindingResolved 经 T-FIX-B2-03 后也显式带 (反查原 FindingCreated 落的 review_unit_id,
    跨会话 fix_after resolve 时 prov.session_id=B≠原 unit A, 显式字段保证折进 A 不回退到 B)。
    FindingVerified 的 schema 仍不带, 靠 provenance 归组 —— 它由产 finding 的同一 review 会话盖,
    prov.session_id 即原 review_unit_id, 故无跨会话错挂窗口。回退路径只对"显式缺失"生效, 显式优先
    的次序保证有显式字段时绝不被隐式 provenance 认错邻居。

    不 gate on model_review source: 一是 Verified/Resolved 不带 source, gate 了就归不进 unit
    (折叠少看 verify/resolve = verdict 错, e2e 实证); 二是 compute_review_verdict 已按目标
    review_unit_id 精确过滤 —— execution/系统 finding 的 session_id 不会等于被查的 review 会话,
    不会误纳。"""
    payload = ev.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    after = payload.get("after_state")
    src = after if isinstance(after, Mapping) else payload
    ru = src.get("review_unit_id")
    if isinstance(ru, str) and ru:
        return ru
    prov = ev.get("provenance")
    prov = prov if isinstance(prov, Mapping) else {}
    sess = prov.get("session_id")
    return sess if isinstance(sess, str) and sess else None


def compute_review_verdict(events: Iterable[Mapping[str, Any]], review_unit_id: str) -> str:
    """从原始事件流抽出某**单个** review-unit (= 一个 review 会话) 的 finding 折出 verdict。

    back-compat 薄包装 (单 review_unit_id)。review-target 级跨会话折叠走
    compute_review_verdict_over_set —— gate (T-LND-04) 已切到那条按 review-target 折叠
    (finding-review-verdict-gate-session-fold-false-pass)。

    events: 原始 event record (含 event_type / payload / provenance / sequence_number)。
    review_unit_id 空 → passed (无 review-unit 约束)。无匹配 finding 事件 → passed (干净)。
    """
    if not review_unit_id:
        return _VERDICT_PASSED
    return compute_review_verdict_over_set(events, frozenset({review_unit_id}))


def compute_review_verdict_over_set(
    events: Iterable[Mapping[str, Any]], review_unit_ids: frozenset[str] | set[str],
) -> str:
    """按**一组** review_unit_id 跨会话折出一个 review-target 的 verdict —— gate (T-LND-04) 入口。

    finding-review-verdict-gate-session-fold-false-pass 修: 旧口径按【完成本任务的会话 session_id】
    单键折叠, double-drive 下空会话先 conclude(0 finding)→passed→REVIEW task false-pass, 而同一
    review-target 另一会话产的 verified 阻塞 finding 因 session_id 不同永不进折叠 → proof-of-work
    verdict 门在它最该生效的并行场景被旁路。新口径: review_unit_ids = 所有【canonically 申明 review
    过同一 review-target R】的会话 (= 对 R 落过 task-scoped envelope 的会话 ∪ 完成会话; 由 gate
    从 committed envelope 解析), 任一会话的 verified-未闭合 blocking finding 都把 R 的 verdict
    钉成非 passed —— 空会话的 pass 盖不过 co-reviewer 的 blocking finding。

    每个 finding 仍按它自己的 review_unit_id (= 产它的会话) 精确归组 (血缘不变, honoring T-RCL-01
    的 review-unit session_id 血缘); 改的只是"哪些会话的 finding 该进同一个 R 的折叠"这个边界 ——
    从'仅完成会话'放宽到'所有申明 review R 的会话'。

    events: 原始 event record。review_unit_ids 空 → passed (无约束)。集内无匹配 finding → passed
    (干净 review)。folding 仍是 fold_review_verdict 纯函数 (确定可重放, INV-B1-5)。
    """
    if not review_unit_ids:
        return _VERDICT_PASSED
    fold_input: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("event_type") not in _FINDING_EVENT_TYPES:
            continue
        if _event_review_unit_id(ev) not in review_unit_ids:
            continue
        payload = ev.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        after = payload.get("after_state")
        src = after if isinstance(after, Mapping) else payload
        fold_input.append(
            {
                "event_type": ev.get("event_type"),
                "finding_id": src.get("finding_id"),
                "severity": src.get("severity"),
                "verification_state": src.get("verification_state"),
                "sequence_number": ev.get("sequence_number", 0),
            }
        )
    return fold_review_verdict(fold_input)
