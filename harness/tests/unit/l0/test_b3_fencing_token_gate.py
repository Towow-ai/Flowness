"""B-3 (substrate 4, K5 fencing / Kleppmann): commit gate 资源侧 fencing token 校验 — 三 case 边界证明.

★ 边界 = done 判据 (advisor 钉死三 case, 缺 (c) 是灾难):
  (a) no-token (never-claimed) → PASS untouched — 绝大多数 producer (review/fix/legacy/未认领) 无 token,
      必须 verify 之前就放行 (None ≠ stale; 把 None 当 stale 拒 = 拒所有合法写 = autopilot 死)。
  (b) stale (token < 当前最高 .fence) → flag-on REJECT / flag-off would_reject 留痕不拒 (旧会话复活迟到写)。
  (c) current/valid (token == 当前最高) → PASS — happy path, 误拒 = 每个合法 execution 完工被拒 = 灾难。

+ enforcement 默认 OFF (设计需独立审计): flag-off 时 (b) 不真拒只观测; flag-on 供测试验三 case。
+ 端到端 token-flow 链: 认领发 token → 注入 env → 完工携带 → gate 校验。

合成 tempdir, 绝不触碰 live。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.awareness.claim import claim_task
from towow.l0.commit_gate.fencing_check import check_fencing_token
from towow.schemas.enums import (
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    TargetEntityType,
    TransitionType,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _trc_intent(task_id: str, *, fencing_token: int | None, outcome: str = "success") -> EventIntent:
    """合成一个 TaskRunCompleted EventIntent (gate 检查的输入形态)。"""
    after: dict[str, object] = {
        "task_id": task_id, "run_id": "run-1", "outcome": outcome,
        "envelope_event_id": "local:envelope",
    }
    if fencing_token is not None:
        after["fencing_token"] = fencing_token
    return EventIntent(
        local_intent_id=f"trc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_RUN_COMPLETED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": TargetEntityType.TASK.value,
            "transition_type": TransitionType.MODIFIED.value,
            "after_state": after,
        },
        provenance_hint=ProvenanceHint(
            actor_type="agent_session", actor_id="m14", session_id="s1", skill_id="M-1.4",
        ),
        base_classification=__import__(
            "towow.schemas.enums", fromlist=["BaseClassification"],
        ).BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _claim_dir(tmp_path: Path) -> Path:
    """gate 从 log_path (.towow/events.log) 推 .towow/orchestrator/exec_claims/ — 这里建好该路径。"""
    d = tmp_path / ".towow" / "orchestrator" / "exec_claims"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path(tmp_path: Path) -> Path:
    p = tmp_path / ".towow" / "events.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


# ─── 三 case 边界 (flag-on 真拒, 验逻辑对) ─────────────────────────────────────────────


def test_case_a_no_token_passes_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) no-token (never-claimed) → PASS, 即便 enforce-on (None 绝不当 stale 拒)。"""
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "1")
    _claim_dir(tmp_path)
    res = check_fencing_token([_trc_intent("t-1", fencing_token=None)], _log_path(tmp_path))
    assert res.passed is True
    assert res.would_reject is False


def test_case_a_no_token_passes_even_with_fence_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ (a) 关键变体: task 有 .fence (被认领过) 但本次写【不带 token】→ 仍 PASS (不同 producer 路径)。

    cross-producer: 一个对该 task 的 no-token 写 (如手动/legacy 路径) 不该因 .fence 存在被拒。
    """
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "1")
    cd = _claim_dir(tmp_path)
    claim_task(cd, "t-1", "claimant-A")  # 建 .fence=1
    res = check_fencing_token([_trc_intent("t-1", fencing_token=None)], _log_path(tmp_path))
    assert res.passed is True, "no-token 写即便 task 有 .fence 也必须放行 (None≠stale)"


def test_case_c_current_token_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ (c) happy path: token == 当前最高 .fence → PASS (误拒=每个合法完工被拒=灾难)。"""
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "1")
    cd = _claim_dir(tmp_path)
    r = claim_task(cd, "t-1", "claimant-A")  # token=1, .fence=1
    res = check_fencing_token([_trc_intent("t-1", fencing_token=r.fencing_token)], _log_path(tmp_path))
    assert res.passed is True, "current token 必须放行 (happy path)"
    assert res.would_reject is False
    assert res.enforce_enabled is True, "enforce-on 时 happy path 结果也带 enforce_enabled=True (可观测)"


def test_case_b_stale_token_rejected_when_enforce_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ (b) stale: 旧 token < 当前最高 .fence → enforce-on REJECT (堵旧会话复活迟到写)。"""
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "1")
    cd = _claim_dir(tmp_path)
    r1 = claim_task(cd, "t-1", "claimant-A")  # token=1
    from towow.awareness.claim import release_claim

    release_claim(cd, "t-1", "claimant-A")  # .fence 保留=1
    r2 = claim_task(cd, "t-1", "claimant-B")  # token=2 (易主, .fence=2)
    assert r2.fencing_token == 2
    # 旧会话 A 复活, 带旧 token=1 写 → 当前最高=2 → stale → 拒。
    res = check_fencing_token([_trc_intent("t-1", fencing_token=r1.fencing_token)], _log_path(tmp_path))
    assert res.passed is False
    assert res.failure_reason == "fencing_token_stale"
    assert res.would_reject is True
    assert res.enforce_enabled is True, "真拒路径 enforce_enabled=True (与 enforce-on 一致)"


def test_case_b_stale_observed_only_when_enforce_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 enforce-OFF (显式回滚口; 默认已 owner 2026-06-30 turn-on 翻 on): 显式关后 stale 写【不真拒】
    只 would_reject 留痕。off 行为仍是 owner 受控回滚通道 (TOWOW_FENCING_ENFORCE=off), 只是不再是默认。"""
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "off")
    cd = _claim_dir(tmp_path)
    claim_task(cd, "t-1", "claimant-A")  # token=1
    from towow.awareness.claim import release_claim

    release_claim(cd, "t-1", "claimant-A")
    claim_task(cd, "t-1", "claimant-B")  # token=2
    res = check_fencing_token([_trc_intent("t-1", fencing_token=1)], _log_path(tmp_path))
    assert res.passed is True, "enforce-off (显式回滚) → 不真拒"
    assert res.would_reject is True, "但留痕 would_reject (观测到该拒)"
    assert res.enforce_enabled is False, (
        "🔴 f-rp-autopilot-fencing-enforce-silently-off: enforce-off 时结果带 enforce_enabled=False → "
        "gate 记进 CommitAccepted → enforce=off 可观测 (此前静默不可见)"
    )


def test_enforce_state_observable_on_no_token_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 f-rp-autopilot-fencing-enforce-silently-off: 即便本批无 stale (no-token, 全放行), 结果也带
    本进程真实 enforce 态 → gate 每次运行都把 enforce 记进 canonical 账本 (不只在撞 stale 时才可见)。"""
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "off")
    _claim_dir(tmp_path)
    res = check_fencing_token([_trc_intent("t-1", fencing_token=None)], _log_path(tmp_path))
    assert res.passed is True
    assert res.would_reject is False
    assert res.enforce_enabled is False, "enforce-off gate 进程即使无 stale 也在结果里暴露 off (可观测)"


def test_non_task_run_completed_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非 TaskRunCompleted intent → 不适用, PASS (本门只管 TaskRunCompleted)。"""
    monkeypatch.setenv("TOWOW_FENCING_ENFORCE", "1")
    _claim_dir(tmp_path)
    intent = _trc_intent("t-1", fencing_token=999)
    object.__setattr__(intent, "event_type", EventType.NODE_TOUCHED)
    res = check_fencing_token([intent], _log_path(tmp_path))
    assert res.passed is True


# ─── 端到端 token-flow 链 (认领 → env 注入 → 完工携带) ─────────────────────────────────


def test_claim_returns_token_and_env_injection(tmp_path: Path) -> None:
    """★ 链: claim_exec_spawn 返 token → _provision_spawn_env 注入 TOWOW_FENCING_TOKEN。"""
    from towow.l2.claude_bg_helper import _provision_spawn_env
    from towow.l2.orchestrator import claim_exec_spawn

    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "orchestrator").mkdir()
    token = claim_exec_spawn(towow, "t-1", "orch")
    assert isinstance(token, int) and token >= 1, "认领成功返单调 token (非 bool)"

    env = _provision_spawn_env(tmp_path, task_id="t-1", fencing_token=token)
    assert env["TOWOW_FENCING_TOKEN"] == str(token), "token 注入子会话 env"


def test_no_token_env_not_injected(tmp_path: Path) -> None:
    """未认领 spawn (fencing_token=None) → 不注入 env (零回归; 完工无 token → gate 放行)。"""
    from towow.l2.claude_bg_helper import _provision_spawn_env

    env = _provision_spawn_env(tmp_path, task_id="t-1", fencing_token=None)
    assert "TOWOW_FENCING_TOKEN" not in env


def test_claim_failed_returns_none(tmp_path: Path) -> None:
    """认领失败 (别人已认领) → claim_exec_spawn 返 None (不 spawn 转盯场)。"""
    from towow.l2.orchestrator import claim_exec_spawn

    towow = tmp_path / ".towow"
    (towow / "orchestrator").mkdir(parents=True)
    assert isinstance(claim_exec_spawn(towow, "t-1", "A"), int)  # A 认领成功
    assert claim_exec_spawn(towow, "t-1", "B") is None, "B 认领失败 (A 持有) → None"
