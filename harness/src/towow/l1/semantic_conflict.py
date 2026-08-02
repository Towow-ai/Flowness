"""RUN-080 漏洞2 — 语义冲突检测 (detection 真做; 仲裁 verdict 挂 deferral 债).

# spec source:
#   harness/docs/SPEC-PATCH-CONCURRENCY-RUN080.md §2 (语义冲突检测 + 解决协议设计)
#   harness/docs/SPEC-GAP-CONCURRENCY-FINDINGS.md 漏洞2
#   M-0.5 §3.2/§3.3.2 物理冲突检测 (本模块是其语义层补充)

为什么存在:
  物理冲突(两会话改同一文件)由 M-0.5 WriteConflictCheck/FreshnessDriftCheck 挡。但**语义冲突**
  ——两个并行会话对同一 obligation 给**相反判定**(一个 IN_SCOPE、一个 OUT_OF_SCOPE)——系统此前
  完全看不见(全仓 0 检测)。owner 要的"产出打架有专门机制处理"的第一步是**先能看见冲突**。

  本模块做"检测"这一硬半: 扫 ObligationScopeJudged 事件, 找同一 obligation 被不同 session 给了
  相反 scope 判定, 把双方证据迹(event_id / session_id / judgment / confidence)都留下来。

降级边界 (owner 授权 RUN-080):
  **仲裁 verdict(谁赢 / commit-gate enforcement reject / resolver 裁决策略)本轮 SKIP**, 挂
  deferral 债 debt-run080-semantic-arbitration。本模块只产 SemanticConflict 记录供后续仲裁层消费;
  不替任一方下结论、不阻断提交。这样"血缘不串"先有了'看得见冲突'的地基, 仲裁策略后续再上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 相反判定对: IN_SCOPE vs OUT_OF_SCOPE 是真冲突; UNCERTAIN 不与任何判定冲突(非承诺性表态)。
_OPPOSING = frozenset({frozenset({"in_scope", "out_of_scope"})})


@dataclass(frozen=True)
class JudgmentSide:
    """一条参与冲突的 scope 判定(留全证据迹)。"""

    event_id: str
    session_id: str | None
    judgment: str
    confidence: float | None
    actor_id: str | None = None


@dataclass(frozen=True)
class SemanticConflict:
    """同一 obligation 被不同 session 给了相反判定 — 待后续仲裁层裁。"""

    obligation_id: str
    sides: list[JudgmentSide] = field(default_factory=list)

    @property
    def session_ids(self) -> set[str]:
        return {s.session_id for s in self.sides if s.session_id is not None}


def _provenance_session(ev: dict[str, Any]) -> str | None:
    prov = ev.get("provenance")
    if isinstance(prov, dict):
        sid = prov.get("session_id")
        return str(sid) if sid else None
    return None


def _provenance_actor(ev: dict[str, Any]) -> str | None:
    prov = ev.get("provenance")
    if isinstance(prov, dict):
        actor = prov.get("actor_id")
        return str(actor) if actor else None
    return None


def _extract_scope_judgment(ev: dict[str, Any]) -> tuple[JudgmentSide, str] | None:
    """从一条 ObligationScopeJudged 事件抽 (JudgmentSide, obligation_id)。非该类型 → None。

    容 canonical(event_type=ObligationScopeJudged) 与 payload.judgment_type=obligation_scope 两形态。
    """
    payload = ev.get("payload")
    if not isinstance(payload, dict):
        return None
    is_scope = ev.get("event_type") == "ObligationScopeJudged" or payload.get(
        "judgment_type",
    ) == "obligation_scope"
    if not is_scope:
        return None
    after = payload.get("after_state")
    if not isinstance(after, dict):
        return None
    obligation_id = after.get("obligation_id")
    judgment = after.get("judgment")
    if not obligation_id or not judgment:
        return None
    conf = payload.get("confidence")
    side = JudgmentSide(
        event_id=str(ev.get("event_id", "")),
        session_id=_provenance_session(ev),
        judgment=str(judgment),
        confidence=float(conf) if isinstance(conf, (int, float)) else None,
        actor_id=_provenance_actor(ev),
    )
    return side, str(obligation_id)


def detect_semantic_conflicts(events: list[dict[str, Any]]) -> list[SemanticConflict]:
    """检测同一 obligation 被不同 session 给相反 scope 判定的语义冲突。

    判据(保守, 只报真冲突):
      - 同一 obligation_id;
      - 至少两条判定来自**不同 session**(同一 session 自己迭代改判不算并发冲突);
      - 判定相反(IN_SCOPE vs OUT_OF_SCOPE)。UNCERTAIN 不参与冲突。

    返回 SemanticConflict 列表(按 obligation_id 排序, 确定性), 每条带 sides 全证据迹。
    不裁谁赢(仲裁 deferred, debt-run080-semantic-arbitration)。
    """
    by_obligation: dict[str, list[JudgmentSide]] = {}
    for ev in events:
        extracted = _extract_scope_judgment(ev)
        if extracted is None:
            continue
        side, obligation_id = extracted
        by_obligation.setdefault(str(obligation_id), []).append(side)

    conflicts: list[SemanticConflict] = []
    for obligation_id in sorted(by_obligation):
        sides = by_obligation[obligation_id]
        # 收集出现过的(非 UNCERTAIN)判定值; 只看跨 session 的相反
        committal = [s for s in sides if s.judgment in ("in_scope", "out_of_scope")]
        judgments_present = frozenset(s.judgment for s in committal)
        if judgments_present not in _OPPOSING:
            continue
        # 必须跨不同 session 才算并发冲突(同 session 自己改判=正常 supersede)
        sessions = {s.session_id for s in committal if s.session_id is not None}
        # session_id 缺失(None)时无法证明同源, 保守仍当不同源参与判定
        none_sided = any(s.session_id is None for s in committal)
        if len(sessions) < 2 and not (none_sided and len(committal) >= 2):
            continue
        conflicts.append(SemanticConflict(obligation_id=obligation_id, sides=list(committal)))
    return conflicts


__all__ = [
    "JudgmentSide",
    "SemanticConflict",
    "detect_semantic_conflicts",
]
