"""owner-confirm@v1 钉死测试 (组件6 — 红线 enforcement + 不可伪造 owner 授权)。

核心不变量 (anti-fake-done 的最后一块):
  1. 有效 owner-confirm (owner 私钥对本次退役的确定性挑战签的 Ed25519) → retire 闭合放行。
  2. agent 伪造的 owner-confirm (自己的私钥 / 无签名 / 挑战改绑到别的 task) → 拒。
  3. 无 owner-confirm 的退役 → 拒 (fail-closed, 与 R2 stub 一致)。
  4. 私钥托管未就位 (钉死公钥常量 None) → 一律拒 (安全默认)。

验签核用真 cryptography Ed25519 —— 测证明【数学成立】(测里自造密钥对), 【不】证明 agent 造不出:
不可伪造性锚在【私钥在 agent 信封外 + 钉死公钥不被换】, 那是 owner 托管选型 + monkeypatch-out-of-scope,
不是本测能覆盖的 (见 owner_confirm.py 模块头 residual)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from towow.l0.commit_gate import owner_confirm as oc
from towow.l0.commit_gate.closure_evidence_check import check_closure_evidence_verification
from towow.l0.event_log.index import EventIndex
from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    import pytest

_TASK = "T-RETIRE-Y"
_PLAN = "plan-x"
_FINDING = "f-premise-false-y"
_CLOSER = "sess-closer"
_VERIFIER = "sess-independent-verifier"


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    sk = Ed25519PrivateKey.generate()
    pub_hex = (
        sk.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    return sk, pub_hex


def _prov(session_id: str = "sess-x") -> Provenance:
    return Provenance(
        actor_type="agent_session", actor_id="m15", skill_id="M-1.5", session_id=session_id
    )


def _rec(
    *,
    event_type: EventType,
    category: EventCategory,
    payload: dict,
    subjects: list[Subject],
    seq: int,
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=category,
        payload=payload,
        provenance=_prov(),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )


def _premise_false_finding(seq: int = 1) -> EventRecord:
    return _rec(
        event_type=EventType.FINDING_CREATED,
        category=EventCategory.FINDING,
        payload={
            "finding_id": _FINDING,
            "severity": "major",
            "finding_kind": "premise_false",
            "description": "GOAL 前提为假",
        },
        subjects=[
            Subject(entity_type=SubjectEntityType.FINDING, entity_id=_FINDING, role=SubjectRole.PRIMARY),
            Subject(entity_type=SubjectEntityType.TASK, entity_id=_TASK, role=SubjectRole.AFFECTED),
        ],
        seq=seq,
    )


def _finding_verified(seq: int = 2) -> EventRecord:
    return _rec(
        event_type=EventType.FINDING_VERIFIED,
        category=EventCategory.FINDING,
        payload={
            "finding_id": _FINDING,
            "lifecycle_state": "verified",
            "verification_method": "verify-step 独立证伪确认前提为假",
            "false_positive_eliminated": True,
            "verification_state": "verified",
        },
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=_FINDING, role=SubjectRole.PRIMARY)],
        seq=seq,
    )


def _owner_confirm_record(
    *,
    signing_key: Ed25519PrivateKey,
    task_id: str = _TASK,
    finding_id: str = _FINDING,
    nonce: str = "nonce-001",
    sign_task_id: str | None = None,
    signature_override: str | None = None,
    seq: int = 3,
) -> EventRecord:
    """构造一条 OwnerConfirmationGranted (retire 类)。

    sign_task_id: 若给, 用它签挑战 (而 payload 仍写 task_id) —— 模拟"拿 A 的签名改绑到 B"的伪造。
    signature_override: 若给, 直接用它当签名 (模拟 agent 塞任意/自造签名)。
    """
    challenge = oc.canonical_challenge(
        oc.RED_LINE_RETIRE_TASK,
        task_id=sign_task_id if sign_task_id is not None else task_id,
        finding_id=finding_id,
        action_fingerprint=None,
        nonce=nonce,
    )
    signature = signature_override if signature_override is not None else signing_key.sign(challenge).hex()
    return _rec(
        event_type=EventType.OWNER_CONFIRMATION_GRANTED,
        category=EventCategory.COMMIT,
        payload={
            "red_line_class": oc.RED_LINE_RETIRE_TASK,
            "task_id": task_id,
            "finding_id": finding_id,
            "action_fingerprint": None,
            "nonce": nonce,
            "signature": signature,
            "key_id": "owner-primary",
            "granted_by": "nature",
            "granted_at": "2026-07-01T00:00:00Z",
        },
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        seq=seq,
    )


def _retired_close_intent(task_id: str = _TASK, ref_id: str = _FINDING) -> EventIntent:
    return EventIntent(
        local_intent_id=f"tnc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_NODE_CLOSED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": "task",
            "transition_type": "modified",
            "after_state": {
                "task_id": task_id,
                "plan_id": _PLAN,
                "reason": "retired",
                "superseded_by": {"ref_type": "finding", "ref_id": ref_id},
                "verification_verdict_ref": None,
                "closed_by": _CLOSER,
            },
        },
        provenance_hint=_prov(session_id=_CLOSER),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _pin(monkeypatch: pytest.MonkeyPatch, pub_hex: str | None) -> None:
    monkeypatch.setattr(oc, "_PINNED_OWNER_CONFIRM_PUBKEY_HEX", pub_hex)


# ── 验签原语层 ─────────────────────────────────────────────────────────────────


def test_verify_valid_signature_passes() -> None:
    sk, pub_hex = _keypair()
    ch = oc.canonical_challenge(
        oc.RED_LINE_RETIRE_TASK, task_id="T-1", finding_id="f-1", action_fingerprint=None, nonce="n"
    )
    assert oc.verify_detached_signature(ch, sk.sign(ch).hex(), pinned_pubkey_hex=pub_hex) is True


def test_verify_rejects_wrong_key() -> None:
    sk, _ = _keypair()
    _, other_pub = _keypair()
    ch = oc.canonical_challenge(
        oc.RED_LINE_RETIRE_TASK, task_id="T-1", finding_id="f-1", action_fingerprint=None, nonce="n"
    )
    # owner 的签名, 用【别的】公钥验 → 失败 (对应 agent 换成自己钥自签)。
    assert oc.verify_detached_signature(ch, sk.sign(ch).hex(), pinned_pubkey_hex=other_pub) is False


def test_verify_rejects_no_pinned_key() -> None:
    sk, _ = _keypair()
    ch = oc.canonical_challenge(
        oc.RED_LINE_RETIRE_TASK, task_id="T-1", finding_id="f-1", action_fingerprint=None, nonce="n"
    )
    assert oc.verify_detached_signature(ch, sk.sign(ch).hex(), pinned_pubkey_hex=None) is False


def test_verify_rejects_garbage_signature() -> None:
    _, pub_hex = _keypair()
    ch = oc.canonical_challenge(
        oc.RED_LINE_RETIRE_TASK, task_id="T-1", finding_id="f-1", action_fingerprint=None, nonce="n"
    )
    assert oc.verify_detached_signature(ch, "not-hex-zzzz", pinned_pubkey_hex=pub_hex) is False
    assert oc.verify_detached_signature(ch, "deadbeef", pinned_pubkey_hex=pub_hex) is False


# ── retire 闭合门集成层 (端到端) ───────────────────────────────────────────────


def test_retire_passes_with_valid_owner_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """有效 owner-confirm (owner 私钥签本次退役挑战) + 齐全 premise_false 链 → 放行。"""
    sk, pub_hex = _keypair()
    _pin(monkeypatch, pub_hex)
    records = [_premise_false_finding(), _finding_verified(), _owner_confirm_record(signing_key=sk)]
    res = check_closure_evidence_verification([_retired_close_intent()], EventIndex(records), records)
    assert res.passed is True
    assert res.applicable is True


def test_retire_rejected_forged_owner_confirm_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 用【自己的】私钥签 (不是 owner 钉死公钥对应的私钥) → 验签失败 → 退役被拒。"""
    _owner_sk, owner_pub = _keypair()
    agent_sk, _ = _keypair()  # agent 自造钥
    _pin(monkeypatch, owner_pub)  # 门只信 owner 公钥
    records = [_premise_false_finding(), _finding_verified(), _owner_confirm_record(signing_key=agent_sk)]
    res = check_closure_evidence_verification([_retired_close_intent()], EventIndex(records), records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_owner_confirm"


def test_retire_rejected_signature_rebound_to_other_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """owner 对 task-OTHER 的退役签名, payload 改绑到 _TASK → 挑战不同 → 验签失败 → 拒 (重放绑定)。"""
    sk, pub_hex = _keypair()
    _pin(monkeypatch, pub_hex)
    rebound = _owner_confirm_record(signing_key=sk, sign_task_id="T-SOME-OTHER")
    records = [_premise_false_finding(), _finding_verified(), rebound]
    res = check_closure_evidence_verification([_retired_close_intent()], EventIndex(records), records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_owner_confirm"


def test_retire_rejected_garbage_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 塞一段任意 hex 当签名 → 验签失败 → 拒。"""
    sk, pub_hex = _keypair()
    _pin(monkeypatch, pub_hex)
    forged = _owner_confirm_record(signing_key=sk, signature_override="deadbeef" * 16)
    records = [_premise_false_finding(), _finding_verified(), forged]
    res = check_closure_evidence_verification([_retired_close_intent()], EventIndex(records), records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_owner_confirm"


def test_retire_rejected_no_owner_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """齐全 premise_false 链但【无】OwnerConfirmationGranted → 拒 (fail-closed)。"""
    _, pub_hex = _keypair()
    _pin(monkeypatch, pub_hex)
    records = [_premise_false_finding(), _finding_verified()]
    res = check_closure_evidence_verification([_retired_close_intent()], EventIndex(records), records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_owner_confirm"


def test_retire_rejected_when_key_custody_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """钉死公钥常量 None (私钥托管未就位) → 即便有一条形式完整的 owner-confirm 也拒 (安全默认)。

    2026-07-03 起生产常量已由 Nature keygen 就位 (非 None), 本测试改为 monkeypatch 模拟
    "未就位"状态 —— 测的是 fail-closed 逻辑本身, 不是生产常量的当前值。"""
    sk, _ = _keypair()
    _pin(monkeypatch, None)
    assert oc._PINNED_OWNER_CONFIRM_PUBKEY_HEX is None
    records = [_premise_false_finding(), _finding_verified(), _owner_confirm_record(signing_key=sk)]
    res = check_closure_evidence_verification([_retired_close_intent()], EventIndex(records), records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_owner_confirm"


# ── 4 红线动作层 (action_fingerprint 锚定) ─────────────────────────────────────


def test_action_fingerprint_confirm_binds_to_specific_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """owner 对"删 A"的签名, 只对指纹相同的动作有效; 换到"删 B"(指纹不同) 验签失败。"""
    sk, pub_hex = _keypair()
    _pin(monkeypatch, pub_hex)
    fp_a = oc.action_fingerprint("rm -rf /data/A", ("/data/A",))
    fp_b = oc.action_fingerprint("rm -rf /data/B", ("/data/B",))
    nonce = "nonce-del-a"
    ch = oc.canonical_challenge(
        oc.RED_LINE_DELETE_DATA, task_id=None, finding_id=None, action_fingerprint=fp_a, nonce=nonce
    )
    rec = _rec(
        event_type=EventType.OWNER_CONFIRMATION_GRANTED,
        category=EventCategory.COMMIT,
        payload={
            "red_line_class": oc.RED_LINE_DELETE_DATA,
            "task_id": None,
            "finding_id": None,
            "action_fingerprint": fp_a,
            "nonce": nonce,
            "signature": sk.sign(ch).hex(),
            "key_id": "owner-primary",
            "granted_by": "nature",
            "granted_at": "2026-07-01T00:00:00Z",
        },
        subjects=[Subject(entity_type=SubjectEntityType.FILE, entity_id="/data/A", role=SubjectRole.PRIMARY)],
        seq=1,
    )
    index = EventIndex([rec])
    assert oc.owner_confirm_present_for_action(index, oc.RED_LINE_DELETE_DATA, fp_a) is True
    assert oc.owner_confirm_present_for_action(index, oc.RED_LINE_DELETE_DATA, fp_b) is False
    # 换类别 (outbound_publish) 也不认 —— red_line_class 是挑战第一字段。
    assert oc.owner_confirm_present_for_action(index, oc.RED_LINE_OUTBOUND_PUBLISH, fp_a) is False
