"""波1 T-L0-03: record_hash 真承担完整性职责 (防篡改门, M-0.1 Patch1 §1.3).

owner-decided (RUN-038): record_hash 不只写端留痕, 要做防篡改门 — 审计路径重算 hash 与存储比对,
检出落盘后被改的 record。源表 done_criteria: 人为改一条已落盘 record 的字段, 审计路径重算 hash 与
存储 hash 不符并报完整性错。

现状(改前): record_hash 写时算(_stamp/_reseq), 读路径零引用 — 只写不验, 篡改不可检出。
本组证: verify_record_integrity 对完好 record 返 True、对篡改 record 返 False; audit_integrity
全量扫出被篡改的 event_id; 完好日志审计返空 (反 vacuous: 不是恒报)。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants

if TYPE_CHECKING:
    from pathlib import Path


def _bootstrapped(tmp_path: Path) -> Path:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    event_log = EventLog(towow / "events.log")
    CommitGate(event_log).bootstrap_commit(bootstrap_protocol_invariants())
    return towow / "events.log"


def _tamper_one_payload(log_path: Path) -> str:
    """改一条已落盘 record 的 payload (保留其旧 record_hash) → 返回被篡改的 event_id。"""
    lines = log_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        obj = json.loads(line)
        # 找一条带 payload 的 domain record (非 sentinel/envelope), 篡改其 payload
        if isinstance(obj.get("payload"), dict) and obj.get("event_id"):
            obj["payload"]["__tampered__"] = "injected-after-write"  # 改字段, record_hash 不动
            lines[i] = json.dumps(obj)
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return str(obj["event_id"])
    msg = "no tamperable record found"
    raise AssertionError(msg)


def test_verify_intact_record_true(tmp_path: Path) -> None:
    log_path = _bootstrapped(tmp_path)
    event_log = EventLog(log_path)
    recs = event_log.all_records()
    assert recs
    assert all(event_log.verify_record_integrity(r) for r in recs), "完好 record 全部校验通过"


def test_audit_clean_log_empty(tmp_path: Path) -> None:
    """反 vacuous: 未篡改日志审计返空 (不是恒报篡改)。"""
    log_path = _bootstrapped(tmp_path)
    assert EventLog(log_path).audit_integrity() == []


def test_audit_detects_tampered_record(tmp_path: Path) -> None:
    """改一条落盘 record 的字段(留旧 hash) → 审计重算不符, 报出该 event_id。"""
    log_path = _bootstrapped(tmp_path)
    tampered_id = _tamper_one_payload(log_path)

    fresh = EventLog(log_path)
    flagged = fresh.audit_integrity()
    assert tampered_id in flagged, f"被篡改的 {tampered_id} 须被审计检出, 实得 {flagged}"
    # 被篡改那条 verify 返 False
    tampered_rec = next(r for r in fresh.all_records() if r.event_id == tampered_id)
    assert fresh.verify_record_integrity(tampered_rec) is False
