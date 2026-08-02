"""T-LND-03 (INV-B1-2 fail-monotone / INV-B1-5 verdict-deterministic): derived-review-verdict
折叠纯函数。

verdict = review-unit 全部 finding 生命周期事件按 sequence_number 左折叠出的派生结论 (4 态
pending/failed/passed/closed; closed 由 TaskRunCompleted 在 T-LND-04 加冕, 本块只折 pending/
failed/passed)。

钉住 state_machine (concept derived-review-verdict-projection@v1) 关键边:
  - pending→failed: 出现 verified-FAIL(sev>=major)未 resolved
  - failed→pending: 那条 fail 被真 resolved (**唯一**离开 failed 的路径; 禁 failed→passed 直达)
  - pending→passed: 所有 finding terminal 且无未解决 verified-FAIL
  - passed→failed: passed 后又冒 verified-FAIL (FAIL 单调反转)
  - INV-B1-5: 同一组 finding 事件 (canonical seq 序) 必折出同一 verdict (乱序输入同输出)
"""

from __future__ import annotations

from towow.l0.projection.review_verdict import (
    compute_review_verdict,
    fold_review_verdict,
)


def _created(fid: str, severity: str, seq: int) -> dict:
    return {"event_type": "FindingCreated", "finding_id": fid, "severity": severity, "sequence_number": seq}


def _verified(fid: str, vstate: str, seq: int) -> dict:
    return {"event_type": "FindingVerified", "finding_id": fid, "verification_state": vstate, "sequence_number": seq}


def _disputed(fid: str, seq: int) -> dict:
    return {"event_type": "FindingDisputed", "finding_id": fid, "sequence_number": seq}


def _resolved(fid: str, seq: int) -> dict:
    return {"event_type": "FindingResolved", "finding_id": fid, "sequence_number": seq}


# ── 基本态 ────────────────────────────────────────────────────────────────

def test_empty_unit_is_passed() -> None:
    """零 finding 的 review-unit = 干净 review → passed (all-terminal 真空真)."""
    assert fold_review_verdict([]) == "passed"


def test_open_created_finding_is_pending() -> None:
    """有未达 terminal 的 finding (created, 未 verify) → pending."""
    assert fold_review_verdict([_created("F1", "major", 1)]) == "pending"


def test_verified_major_fail_is_failed() -> None:
    """verified-FAIL(confirmed_real, major)未 resolved → failed."""
    evs = [_created("F1", "major", 1), _verified("F1", "verified", 2)]
    assert fold_review_verdict(evs) == "failed"


def test_dismissed_false_positive_passes() -> None:
    """finding 被独立 verify 判 rejected_false_positive (terminal-dismissed) → passed."""
    evs = [_created("F1", "major", 1), _verified("F1", "rejected_false_positive", 2)]
    assert fold_review_verdict(evs) == "passed"


def test_minor_unresolved_confirmed_is_pending_not_failed() -> None:
    """confirmed minor 未 resolved: 非 fail(只 major+才 fail) 但未 terminal → pending(不可 passed)."""
    evs = [_created("F1", "minor", 1), _verified("F1", "verified", 2)]
    assert fold_review_verdict(evs) == "pending"


def test_disputed_finding_is_non_terminal_pending() -> None:
    """disputed finding 未达 terminal → 阻止 verdict passed (pending)."""
    evs = [_created("F1", "minor", 1), _disputed("F1", 2)]
    assert fold_review_verdict(evs) == "pending"


# ── FAIL 单调 (INV-B1-2) ─────────────────────────────────────────────────

def test_failed_to_pending_only_via_resolve_not_direct_passed() -> None:
    """禁 failed→passed 直达: fail 被 resolved 后落 pending(待 re-review), 不自动 passed."""
    evs = [_created("F1", "major", 1), _verified("F1", "verified", 2), _resolved("F1", 3)]
    assert fold_review_verdict(evs) == "pending"


def test_failed_then_reresolve_confirmation_reaches_passed() -> None:
    """failed→pending→passed: fail resolved 后 re-review 的 closure 事件(再一条 Resolved)→ passed."""
    evs = [
        _created("F1", "major", 1),
        _verified("F1", "verified", 2),  # failed
        _resolved("F1", 3),                     # failed→pending
        _resolved("F1", 4),                     # re-review closure → pending→passed
    ]
    assert fold_review_verdict(evs) == "passed"


def test_passed_reverts_to_failed_on_new_verified_fail() -> None:
    """FAIL 单调反转: 已 passed 后冒出新 verified-FAIL → failed."""
    evs = [
        _created("F1", "minor", 1),
        _verified("F1", "rejected_false_positive", 2),  # passed
        _created("F2", "major", 3),
        _verified("F2", "verified", 4),           # passed→failed
    ]
    assert fold_review_verdict(evs) == "failed"


def test_weak_pass_cannot_revert_failed() -> None:
    """弱 PASS 不可翻回: failed 中即便又来一堆 dismissed/clean finding, 只要原 fail 未 resolved → 仍 failed."""
    evs = [
        _created("F1", "major", 1),
        _verified("F1", "verified", 2),           # failed
        _created("F2", "minor", 3),
        _verified("F2", "rejected_false_positive", 4),  # F2 dismissed, 但 F1 仍未 resolved
        _resolved("F2", 5),
    ]
    assert fold_review_verdict(evs) == "failed"


def test_unresolved_verified_fail_never_passed_property() -> None:
    """INV-B1-2 property: 含未解决 verified-FAIL 的 finding 集, verdict 必 != passed."""
    base = [_created("F1", "major", 1), _verified("F1", "verified", 2)]
    # 再叠加任意多 dismissed/resolved 的其他 finding, 只要 F1 未 resolved
    extra = [_created("F2", "minor", 3), _verified("F2", "rejected_false_positive", 4),
             _created("F3", "major", 5), _resolved("F3", 6)]
    assert fold_review_verdict(base + extra) != "passed"


# ── 确定性 (INV-B1-5) ────────────────────────────────────────────────────

def test_verdict_deterministic_under_input_shuffle() -> None:
    """INV-B1-5: 输入事件乱序 → 折叠按 sequence_number 排序 → 同 verdict (纯函数确定可重放)."""
    evs = [
        _created("F1", "major", 1),
        _verified("F1", "verified", 2),
        _resolved("F1", 3),
        _created("F2", "minor", 4),
        _verified("F2", "rejected_false_positive", 5),
    ]
    forward = fold_review_verdict(evs)
    reversed_in = fold_review_verdict(list(reversed(evs)))
    shuffled = fold_review_verdict([evs[2], evs[0], evs[4], evs[1], evs[3]])
    assert forward == reversed_in == shuffled


# ── compute_review_verdict: 从原始事件流抽 unit 的 finding 折 verdict (gate 入口) ──

def _record(et: str, uid: str, fid: str, seq: int, *, severity: str | None = None,
            vstate: str | None = None, via_provenance: bool = False) -> dict:
    """原始 event record 形态 (payload flat + provenance)。via_provenance: review_unit_id 不进
    payload, 靠 model_review source + provenance.session_id 派生 (T-LND-01 兜底路径)。"""
    payload: dict = {"finding_id": fid}
    if severity is not None:
        payload["severity"] = severity
    if vstate is not None:
        payload["verification_state"] = vstate
    prov: dict = {}
    if via_provenance:
        payload["source"] = {"type": "model_review", "agent_id": "m15.review"}
        prov["session_id"] = uid
    else:
        payload["review_unit_id"] = uid
    return {"event_type": et, "payload": payload, "provenance": prov, "sequence_number": seq}


def test_compute_review_verdict_filters_by_unit() -> None:
    """只折叠目标 review-unit 的 finding, 别的 unit/非 finding 事件不污染。"""
    events = [
        _record("FindingCreated", "RU-A", "F1", 1, severity="major"),
        _record("FindingVerified", "RU-A", "F1", 2, vstate="verified"),   # RU-A failed
        _record("FindingCreated", "RU-B", "F9", 3, severity="minor"),     # 别的 unit, 不影响 RU-A
        {"event_type": "TaskRunCompleted", "payload": {}, "provenance": {}, "sequence_number": 4},
    ]
    assert compute_review_verdict(events, "RU-A") == "failed"
    assert compute_review_verdict(events, "RU-B") == "pending"  # F9 created 未 terminal


def test_compute_review_verdict_via_provenance_fallback() -> None:
    """review_unit_id 不进 payload 时, 靠 model_review + provenance.session_id 派生归组 (T-LND-01 兜底)。"""
    events = [
        _record("FindingCreated", "sess-x", "F1", 1, severity="major", via_provenance=True),
        _record("FindingVerified", "sess-x", "F1", 2, vstate="rejected_false_positive", via_provenance=True),
    ]
    assert compute_review_verdict(events, "sess-x") == "passed"  # dismissed → terminal


def _record_explicit_unit_diff_prov(et: str, ru: str, prov_sid: str, fid: str, seq: int,
                                    *, severity: str | None = None, vstate: str | None = None) -> dict:
    """T-FIX-B2-03: payload 显式带 review_unit_id=ru, 但 provenance.session_id=prov_sid 不同
    (跨会话 fix_after resolve 形态)。"""
    payload: dict = {"finding_id": fid, "review_unit_id": ru}
    if severity is not None:
        payload["severity"] = severity
    if vstate is not None:
        payload["verification_state"] = vstate
    return {"event_type": et, "payload": payload,
            "provenance": {"session_id": prov_sid}, "sequence_number": seq}


def test_finding_resolved_carries_review_unit_id() -> None:
    """T-FIX-B2-03 (REVIEW-verdict#2): FindingResolved 显式带 review_unit_id=A 但 prov.session_id=B
    时, 折叠仍按显式字段归进 unit A (不靠 provenance 回退到 B)。"""
    events = [
        # 会话 A: FindingCreated + verified-FAIL (review_unit_id=A, prov=A)
        _record("FindingCreated", "A", "F1", 1, severity="major"),
        _record("FindingVerified", "A", "F1", 2, vstate="verified"),
        # 跨会话 fix_after 会话 B: FindingResolved 显式带 review_unit_id=A, 但 prov.session_id=B
        _record_explicit_unit_diff_prov("FindingResolved", "A", "B", "F1", 3),
    ]
    # 显式锚定 → resolve 归进 A → 那条 fail 被认成 resolved → verdict 离开 failed (pending, 待 re-review)。
    assert compute_review_verdict(events, "A") == "pending"
    # 反证: 这条 resolve 不属于 unit B (空 unit, 干净 passed)。
    assert compute_review_verdict(events, "B") == "passed"


def test_cross_session_resolve_folds_into_origin_unit() -> None:
    """T-FIX-B2-03 红测命名 (规格 §TDD): 若 FindingResolved 缺显式 review_unit_id 而靠 prov.session_id=B
    回退, resolve 会折进 B → unit A 永远 failed。本测试钉住: 显式带 review_unit_id=A 后, A 的 fail 被
    resolve 认到 → A verdict 离开 failed (非永久 failed)。"""
    events = [
        _record("FindingCreated", "A", "F1", 1, severity="major"),
        _record("FindingVerified", "A", "F1", 2, vstate="verified"),  # A failed
        _record_explicit_unit_diff_prov("FindingResolved", "A", "B", "F1", 3),  # 跨会话 resolve 归 A
    ]
    assert compute_review_verdict(events, "A") != "failed"
    # 对照: 若不带显式字段 (回退 prov=B), 这条 resolve 落 B, A 仍 failed。
    fallback_events = [
        _record("FindingCreated", "A", "F1", 1, severity="major"),
        _record("FindingVerified", "A", "F1", 2, vstate="verified"),
        # resolve 不带 review_unit_id, 无 model_review source → 回退 prov.session_id=B
        {"event_type": "FindingResolved", "payload": {"finding_id": "F1"},
         "provenance": {"session_id": "B"}, "sequence_number": 3},
    ]
    assert compute_review_verdict(fallback_events, "A") == "failed", (
        "对照组: 缺显式 review_unit_id 时 resolve 回退到 prov=B, A 仍 failed (这正是 T-FIX-B2-03 修的 bug)"
    )


def test_compute_review_verdict_empty_unit_passed() -> None:
    assert compute_review_verdict([], "RU-none") == "passed"
    assert compute_review_verdict([], "") == "passed"


# ── 审计洞回归: severity fail-open + 重复 Created 洗白 ──────────────────────

def test_unknown_severity_treated_as_fail_eligible_not_open() -> None:
    """洞1 回归: 未知 severity 的 verified finding 不被静默降级成非 fail (fail-closed 保守)。"""
    evs = [_created("F1", "blocker", 1), _verified("F1", "verified", 2)]  # "blocker" 不在 5 档
    assert fold_review_verdict(evs) == "failed"


def test_lnd06_resolved_without_verify_is_not_terminal() -> None:
    """T-LND-06 (INV-B1-6): finding 未经 FindingVerified 就 FindingResolved → 非真 terminal →
    其 review-unit verdict 不可 passed (verify-step 不可跳过, 喂 verdict 折叠的须是真 terminal)。"""
    evs = [_created("F1", "minor", 1), _resolved("F1", 2)]  # 跳过 verify 直接 resolve
    assert fold_review_verdict(evs) == "pending"  # 非 passed


def test_lnd06_verified_then_resolved_is_terminal_passed() -> None:
    """正常路径: created→verified→resolved (verify 真发生过) → 真 terminal → passed。"""
    evs = [_created("F1", "minor", 1), _verified("F1", "unverified_inconclusive", 2), _resolved("F1", 3)]
    assert fold_review_verdict(evs) == "passed"


def test_recreate_cannot_downgrade_severity_to_wash_fail() -> None:
    """洞2 回归: 对已确认大 fail 的 finding 二次 FindingCreated 降级 severity 不能洗白成 pending/passed."""
    evs = [
        _created("F1", "major", 1),
        _verified("F1", "verified", 2),   # failed
        _created("F1", "minor", 3),        # 二次 Created 降级 → 不可洗白
    ]
    assert fold_review_verdict(evs) == "failed"
    # 即便再 dismiss, 原 fail 未真 resolved → 仍不可 passed
    evs.append(_verified("F1", "rejected_false_positive", 4))
    assert fold_review_verdict(evs) != "passed"
