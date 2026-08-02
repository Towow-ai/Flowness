"""T-AWARE-05 断线会话自动恢复器 (disconnected-session-recovery-daemon) 单测.

# concept source:
#   disconnected-session-recovery-daemon (supersede wake-watcher-transient-resume-mechanism@v1)
#     — 6 态 9 转移 SAGA 状态机: 只管 towow vitality=stuck_waiting 的会话, 每 10 分钟注入一次,
#       最多 3 次, 3 次仍未确认响应经 neutral-feishu-owner-gateway 上报 owner。
#   debt-a67d723e282f 落点 — 恢复动作 (判定/注入/重试/升级) 一律 emit canonical 事件进 .towow
#     EventLog (不再只写外层 ledger); 本测试正是"可被治理账本观测/可被 live-fire 复算"的载体:
#     读真临时 .towow 账本断真 canonical 事件, 非空心。
#
# ── 为什么直接驱动 drive_recovery_for_session ──────────────────────────────────
# 恢复器每 --recovery-once 对一个会话把 SAGA 推进【至多一步】; 10 分钟节奏由 launchd 复调提供。
# 本测试注入 `now` (墙钟时刻可控) 逐步驱动状态机, 不真 sleep、不真调 claude/飞书 (env 测试缝
# FAKE_VITALITY / FAKE_DELIVER + 临时 inbox 目录), 断每一步 emit 的 canonical 事件与最终态。
#
# ── message_id 非循环 ─────────────────────────────────────────────────────────
# T5-DC2 的"飞书 message_id 非空"绝不由 daemon 自造: daemon 只往 gateway inbox 投递一条上报,
# message_id 由 gateway 侧回写 return_address。本测试【扮 gateway】预写含 message_id 的 ack 到
# return_address, daemon 有界回读捞得 —— 走真实往返接线, message_id 源自测试侧非 daemon 侧。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from towow.l0.event_log import EventLog

if TYPE_CHECKING:
    from types import ModuleType


def _find_wake_watcher_dir() -> Path | None:
    """从本测试文件向上找 .claude/wake-watcher/wake_watcher.py (它在 harness/ 子树之外、同一 repo)。"""
    for parent in Path(__file__).resolve().parents:
        cand = parent / ".claude" / "wake-watcher"
        if (cand / "classify.py").is_file() and (cand / "wake_watcher.py").is_file():
            return cand
    return None


_WW_DIR = _find_wake_watcher_dir()

if _WW_DIR is None:
    pytest.skip(
        "wake-watcher dir (.claude/wake-watcher) 不存在 — sparse checkout 时不可达, 跳过",
        allow_module_level=True,
    )

WW_DIR: Path = _WW_DIR


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    if str(WW_DIR) not in sys.path:
        sys.path.insert(0, str(WW_DIR))  # 供 wake_watcher.py 内部 `import classify` 解析
    spec.loader.exec_module(mod)
    return mod


_ww = _load_module("ww_recovery_wake_watcher", WW_DIR / "wake_watcher.py")


# ── 帮手 ────────────────────────────────────────────────────────────────────────
def _iso(sec: int) -> str:
    """把一个小偏移量渲染成合法 ISO UTC 时间戳 (transcript 记录用)。"""
    mm, ss = divmod(sec, 60)
    return f"2026-07-12T10:{mm:02d}:{ss:02d}Z"


def _write_transcript(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _setup_towow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """建临时 .towow/events.log, 指恢复器往它 emit canonical 事件。返回 events.log 路径。"""
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    events_log = towow / "events.log"
    events_log.write_text("", encoding="utf-8")
    monkeypatch.setenv("WAKE_WATCHER_TOWOW_EVENTLOG", str(events_log))
    monkeypatch.setenv("WAKE_WATCHER_FAKE_DELIVER", "1")  # 不真起 claude, 注入天然判成功
    return events_log


def _kinds(events_log: Path, session_id: str) -> list[str]:
    """读回该会话相关的恢复 canonical 事件 kind 序列 (走 subjects[] 实体索引, 非全量扫)。"""
    el = EventLog(events_log)
    out: list[str] = []
    for rec in el.get_events_by_entity("task", session_id):
        kind = rec.payload.get("kind")
        if isinstance(kind, str) and kind in _ww._RECOVERY_EVENT_KINDS:
            out.append(kind)
    return out


def _last_of_kind(events_log: Path, session_id: str, kind: str) -> dict[str, Any] | None:
    el = EventLog(events_log)
    found: dict[str, Any] | None = None
    for rec in el.get_events_by_entity("task", session_id):
        if rec.payload.get("kind") == kind:
            found = _ww._unwrap_stub(rec.payload)
    return found


def _seed_goal_terminated(events_log: Path, session_id: str) -> None:
    """播一条 GoalSessionTerminated (NodeTouched stub-rewrap, subject=task+session_id) —— 测试只能
    经 path-B 写它 (write_direct 发不了 state_transition 类真事件); 恢复器 _session_terminated
    同认 stub 形态。"""
    from towow.schemas.enums import (
        ActorType,
        BaseClassification,
        EventCategory,
        EventType,
        SubjectEntityType,
        SubjectRole,
    )
    from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

    el = EventLog(events_log)
    after = {"goal_session_id": session_id, "termination_reason": "completion"}
    intent = EventIntent(
        local_intent_id=f"gst-{session_id}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": session_id,
            "touch_type": "write",
            "kind": "GoalSessionTerminated",
            "stub_original_payload": {"kind": "GoalSessionTerminated", "after_state": after, **after},
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-emitter"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=session_id,
                          role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    el.write_direct(intent)


# ══════════════════════════════════════════════════════════════════════════════════════
# T5-DC1: 真实断线冻结会话在 10 分钟内被自动注入唤醒并续跑至收尾
# ══════════════════════════════════════════════════════════════════════════════════════
def test_stuck_waiting_session_reinjected_within_10min_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = _setup_towow(tmp_path, monkeypatch)
    session_id = "sess-dc1"
    # vitality 裁决 = stuck_waiting (进入本机制的唯一前提)。
    monkeypatch.setenv("WAKE_WATCHER_FAKE_VITALITY", f"{session_id}=stuck_waiting")

    transcript = tmp_path / "dc1-transcript.jsonl"
    # 基线: 注入前已有的记录 (baseline_len 快照防历史旧 turn 误判)。
    _write_transcript(transcript, [
        {"type": "assistant", "timestamp": _iso(0), "message": {"content": "被瞬态错误打断前的产出"}},
    ])

    interval = float(_ww.RECOVERY_INTERVAL_SEC)  # 10 分钟观察窗
    t0 = 1_000_000.0

    # ── 步1: 检测到 stuck_waiting → 首次注入 (freeze_confirmed → injecting) ──
    r1 = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0)
    assert r1["action"] == "inject"
    assert r1["attempt_count"] == 1
    assert r1["state"] == "injecting"
    run_id = r1["recovery_run_id"]
    inject_at = t0

    # ── 会话真续跑: transcript 落一条主对话 user turn (送达) + 一条非 API-error 主对话 assistant turn (续跑) ──
    _write_transcript(transcript, [
        {"type": "assistant", "timestamp": _iso(0), "message": {"content": "被瞬态错误打断前的产出"}},
        {"type": "user", "timestamp": _iso(30), "message": {"content": _ww.WAKE_MESSAGE}},
        {"type": "assistant", "timestamp": _iso(40), "message": {"content": "好的, 我从被打断处继续做完了这一步。"}},
    ])

    # ── 步2: 观察窗内 (< 10 分钟) 观测到真实响应 → resumed ──
    r2 = _ww.drive_recovery_for_session(session_id, str(transcript), None, inject_at + interval / 2)
    assert r2["action"] == "resumed"
    assert r2["state"] == "resumed"
    assert r2["recovery_run_id"] == run_id

    # ── 会话续跑至收尾 (GoalSessionTerminated 落账) ──
    _seed_goal_terminated(events_log, session_id)

    # ── 步3: 观测到收尾 → completed (终态) ──
    r3 = _ww.drive_recovery_for_session(session_id, str(transcript), None, inject_at + interval)
    assert r3["action"] == "completed"

    # ── 断言: 恢复全链路 emit canonical 事件, 且注入发生在 10 分钟内 ──
    kinds = _kinds(events_log, session_id)
    assert _ww.RECOVERY_INJECTION_ATTEMPTED in kinds
    assert _ww.SESSION_REAL_OUTPUT_OBSERVED in kinds
    inj = _last_of_kind(events_log, session_id, _ww.RECOVERY_INJECTION_ATTEMPTED)
    assert inj is not None
    assert inj["session_id"] == session_id
    # "10 分钟内被自动注入": 注入事件的 at 与检测时刻 (freeze_confirmed) 同轮, 天然 < 观察窗。
    resumed_ev = _last_of_kind(events_log, session_id, _ww.SESSION_REAL_OUTPUT_OBSERVED)
    assert resumed_ev is not None
    assert (float(resumed_ev["at"]) - inject_at) < interval


# ══════════════════════════════════════════════════════════════════════════════════════
# T5-DC2: 3 次注入失败后停手, 经飞书通道真实送达一条上报 (message_id 非空); 恢复全链路 emit canonical 事件
# ══════════════════════════════════════════════════════════════════════════════════════
def test_three_retries_exhausted_then_feishu_escalation_and_canonical_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = _setup_towow(tmp_path, monkeypatch)
    session_id = "sess-dc2"
    # 全程 stuck_waiting (逐次重核零误戳红线时也仍 stuck, 故会真重试到耗尽)。
    monkeypatch.setenv("WAKE_WATCHER_FAKE_VITALITY", f"{session_id}=stuck_waiting")

    feishu_inbox = tmp_path / "feishu_inbox"
    monkeypatch.setenv("WAKE_WATCHER_FEISHU_INBOX", str(feishu_inbox))
    monkeypatch.setattr(_ww, "RECOVERY_FEISHU_ACK_TIMEOUT_SEC", 5.0)  # 有界回读窗 (ack 已预写, 首读即得)

    transcript = tmp_path / "dc2-transcript.jsonl"
    # 注入后【永不】产生真实响应 (只有静默): 观察窗次次到期, 逼出 3 次重试→耗尽→升级。
    _write_transcript(transcript, [
        {"type": "assistant", "timestamp": _iso(0), "message": {"content": "卡在瞬态错误上停滞"}},
    ])

    interval = float(_ww.RECOVERY_INTERVAL_SEC)
    max_attempts = int(_ww.RECOVERY_MAX_ATTEMPTS)
    assert max_attempts == 3  # concept 契约: 同一会话最多 3 次
    t0 = 2_000_000.0

    # ── 步1: 首次注入 (attempt 1) ──
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0)
    assert r["action"] == "inject"
    assert r["attempt_count"] == 1
    run_id = r["recovery_run_id"]

    # ── 步2..6: 观察窗到期 → 重试, 循环到 attempt==3 用尽 ──
    step = 1
    # 观察窗到期 (attempt1) → injection_unconfirmed
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0 + interval * step)
    assert r["action"] == "window_expired"
    step += 1
    # 重试 attempt2
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0 + interval * step)
    assert r["action"] == "retry_inject"
    assert r["attempt_count"] == 2
    step += 1
    # 观察窗到期 (attempt2)
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0 + interval * step)
    assert r["action"] == "window_expired"
    step += 1
    # 重试 attempt3
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0 + interval * step)
    assert r["action"] == "retry_inject"
    assert r["attempt_count"] == 3
    step += 1
    # 观察窗到期 (attempt3)
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0 + interval * step)
    assert r["action"] == "window_expired"
    step += 1

    # ── 测试【扮 gateway】: 预写含 message_id 的 ack 到 daemon 将回读的 return_address ──
    # (message_id 源自 gateway/测试侧, 绝非 daemon 自造 —— 非循环)。
    return_address = feishu_inbox / "_replies" / f"recovery-escalation-{run_id}.json"
    return_address.parent.mkdir(parents=True, exist_ok=True)
    return_address.write_text(json.dumps({
        "source_id": f"recovery-escalation-{run_id}",
        "reply_text": "收到, 我来看",
        "sender_id": "ou_owner_nature",
        "message_id": "om_feishu_msg_dc2_0001",
    }), encoding="utf-8")

    # ── 步7: attempt 用尽 → 经飞书上报 → escalated (终态) ──
    r = _ww.drive_recovery_for_session(session_id, str(transcript), None, t0 + interval * step)
    assert r["action"] == "escalated"
    assert r["state"] == "escalated"
    assert r["feishu_message_id"] == "om_feishu_msg_dc2_0001"  # 非空 = 真实送达

    # ── 断言①: 飞书 inbox 真被投递了一条上报 (gateway-source-inbox-protocol 文件) ──
    inbox_files = [p for p in feishu_inbox.iterdir() if p.is_file() and p.suffix == ".json"]
    assert len(inbox_files) == 1
    inbox_msg = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert inbox_msg["source_id"] == f"recovery-escalation-{run_id}"
    assert session_id in inbox_msg["to_owner"]
    assert inbox_msg["return_address"]  # 回信地址非空

    # ── 断言②: 3 次注入 (1 首注 + 2 重试) + 3 次观察窗到期 + 1 次升级, 全链路 canonical ──
    kinds = _kinds(events_log, session_id)
    assert kinds.count(_ww.RECOVERY_INJECTION_ATTEMPTED) == 1
    assert kinds.count(_ww.RECOVERY_RETRY_TRIGGERED) == 2  # 3 次注入 = 1 首注 + 2 重试
    assert kinds.count(_ww.INJECTION_OBSERVATION_WINDOW_EXPIRED) == 3
    assert kinds.count(_ww.RECOVERY_BUDGET_EXHAUSTED) == 1

    # ── 断言③: 升级事件承载 escalated 标记 + message_id (下游 unified-awareness 恢复戳数要读) ──
    exhausted = _last_of_kind(events_log, session_id, _ww.RECOVERY_BUDGET_EXHAUSTED)
    assert exhausted is not None
    assert exhausted["escalated"] is True
    assert exhausted["attempt_count"] == max_attempts
    assert exhausted["feishu_message_id"] == "om_feishu_msg_dc2_0001"
