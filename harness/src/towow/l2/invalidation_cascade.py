"""M-2.1 §5 F-15 InvalidationCascade 库层 — 触发条件 + cascade 计算 + response hint (纯逻辑)。

# spec source:
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md
#     §5.1 触发条件 (哪些 event 触发 cascade + 边类型过滤)
#     §5.2 cascade 计算流程 (compute_cascade — forward_slice 递归扩散)
#     §5.3 response_expectation hint 推导规则 (transition_type × entity_type 查表)
#     §8.2 物理目录 .towow/m21/{watermarks,dedup,logs}

RUN-078 边界 (只建库层 + 纯逻辑, 不启 daemon): 触发判定 / response hint / cascade 计算都是纯函数,
由未来 cascade daemon (§5 / §8.1) 在 owner 解冻后调用 → emit InvalidationCascade event。本模块
*绝不 emit event / 绝不启 daemon* —— emit InvalidationCascade 是 daemon-gated (legit-future + 债)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from towow.schemas.enums import ConceptEdgeType, EventType, TransitionType

if TYPE_CHECKING:
    from towow.l1.cia_query import CIAQueryService, SliceEntityResult

# ════════════════════════════════════════════════════════════════════════════════
#  §5.1 触发条件
# ════════════════════════════════════════════════════════════════════════════════

# §5.1 — ConceptStateTransition / ConceptGraphProposal 无条件触发 cascade。
_UNCONDITIONAL_TRIGGER_TYPES: frozenset[EventType] = frozenset(
    {EventType.CONCEPT_STATE_TRANSITION, EventType.CONCEPT_GRAPH_PROPOSAL},
)

# §5.1 — ConceptEdgeAdded / ConceptEdgeRemoved 仅在 edge_type ∈ {dependency, reference, contract} 触发。
_EDGE_TRIGGER_TYPES: frozenset[EventType] = frozenset(
    {EventType.CONCEPT_EDGE_ADDED, EventType.CONCEPT_EDGE_REMOVED},
)

# §5.1 + §5.2 — cascade 沿走的概念边类型 (forward_slice edge_types)。
CASCADE_TRIGGER_EDGE_TYPES: frozenset[str] = frozenset(
    {ConceptEdgeType.DEPENDENCY.value, ConceptEdgeType.REFERENCE.value, ConceptEdgeType.CONTRACT.value},
)

# §5.4 简化点 2 — max_depth 上限 5 (防图爆炸)。
CASCADE_MAX_DEPTH: int = 5


def is_cascade_trigger(event_type: EventType, edge_type: str | None = None) -> bool:
    """§5.1 — 该 event 是否触发 InvalidationCascade。

    - ConceptStateTransition / ConceptGraphProposal → 无条件触发 (True)。
    - ConceptEdgeAdded / ConceptEdgeRemoved → 仅 edge_type ∈ {dependency, reference, contract} 触发。
    - ConceptCreated (单纯创建无下游) / AtReference* (at_reference_graph reducer 直接处理) /
      InvalidationCascade 自身 (§5.4 防 cascade-of-cascade) → 不触发 (False)。
    """
    if event_type in _UNCONDITIONAL_TRIGGER_TYPES:
        return True
    if event_type in _EDGE_TRIGGER_TYPES:
        return edge_type in CASCADE_TRIGGER_EDGE_TYPES
    return False


# ════════════════════════════════════════════════════════════════════════════════
#  §5.3 response_expectation hint 推导
# ════════════════════════════════════════════════════════════════════════════════

# 4 类 suggested_response (§5.3) — 仅 hint, 下游 skill 各自决定 (F-04d SAGA 自决)。
RESPONSE_REVERIFY = "reverify"
RESPONSE_ACKNOWLEDGE = "acknowledge"
RESPONSE_RETRACT = "retract"
RESPONSE_IGNORE = "ignore"

# §5.3 查表 (trigger_transition_type, entity_type) → suggested_response。表外组合走 §5.3 保守默认。
_RESPONSE_HINT_TABLE: dict[tuple[str, str], str] = {
    (TransitionType.SUPERSEDED.value, "concept"): RESPONSE_REVERIFY,
    (TransitionType.SUPERSEDED.value, "task"): RESPONSE_REVERIFY,
    (TransitionType.SUPERSEDED.value, "at_reference"): RESPONSE_ACKNOWLEDGE,
    (TransitionType.SUPERSEDED.value, "obligation"): RESPONSE_REVERIFY,
    (TransitionType.SUPERSEDED.value, "review_plan"): RESPONSE_REVERIFY,
    (TransitionType.MODIFIED.value, "concept"): RESPONSE_ACKNOWLEDGE,
    (TransitionType.MODIFIED.value, "task"): RESPONSE_ACKNOWLEDGE,
    (TransitionType.REMOVED.value, "concept"): RESPONSE_RETRACT,
}


def derive_response_hint(
    trigger_transition_type: str,
    entity_type: str,
    relationship_path: list[str] | None = None,  # noqa: ARG001 — §5.3 签名保留 (本版按 transition×entity 查表)
) -> str:
    """§5.3 — 按 (trigger_transition_type, entity_type) 推导下游 suggested_response hint。

    🔴 关键 invariant (§5.3): suggested_response 仅 hint —— 下游 skill 各自决定 acknowledge /
    reverify / supersede / retract / ignore。M-2.1 不强制响应方式 (F-04d SAGA)。

    表外组合保守默认 (§5.3 精神): superseded → reverify (上游已超, 下游须复核); removed → retract
    (上游已删, 下游须撤引用); modified/created/其它 → acknowledge (知会, 下游自决)。
    """
    key = (trigger_transition_type, entity_type)
    if key in _RESPONSE_HINT_TABLE:
        return _RESPONSE_HINT_TABLE[key]
    if trigger_transition_type == TransitionType.SUPERSEDED.value:
        return RESPONSE_REVERIFY
    if trigger_transition_type == TransitionType.REMOVED.value:
        return RESPONSE_RETRACT
    return RESPONSE_ACKNOWLEDGE


# ════════════════════════════════════════════════════════════════════════════════
#  §5.2 cascade 计算 (纯函数, 不 emit event)
# ════════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CascadeAffectedEntity:
    """§3 InvalidationCascade.affected_entities[] — 一个受影响 entity + 推导的 suggested_response。"""

    entity_type: str
    entity_id: str
    depth: int
    path: list[str]
    suggested_response: str

    def as_dict(self) -> dict[str, object]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "depth": self.depth,
            "path": list(self.path),
            "suggested_response": self.suggested_response,
        }


@dataclass(frozen=True)
class CascadeResult:
    """§5.2 compute_cascade 返回 — 纯计算结果 (cascade daemon 据此 emit InvalidationCascade, daemon-gated)。"""

    source_concept_id: str
    triggered_by_event_id: str | None
    trigger_transition_type: str
    affected_entities: tuple[CascadeAffectedEntity, ...]
    cascade_depth: int
    projection_watermark: int

    def as_dict(self) -> dict[str, object]:
        return {
            "source_concept_id": self.source_concept_id,
            "triggered_by_event_id": self.triggered_by_event_id,
            "trigger_transition_type": self.trigger_transition_type,
            "affected_entities": [e.as_dict() for e in self.affected_entities],
            "cascade_depth": self.cascade_depth,
            "projection_watermark": self.projection_watermark,
        }


def compute_cascade(
    cia_service: CIAQueryService,
    source_concept_id: str,
    trigger_transition_type: str,
    *,
    triggered_by_event_id: str | None = None,
    max_depth: int = CASCADE_MAX_DEPTH,
) -> CascadeResult:
    """§5.2 — 从 source concept 沿 forward slice 递归扩散算受影响 entity + 推导 response hint。

    🔴 纯计算 (不 emit InvalidationCascade event / 不启 daemon) —— emit 是 cascade daemon 的职责
    (§5.5 / §8.1, owner 解冻后接, daemon-gated + 债)。本函数复用 M-2.1 CIA forward_slice
    (edge_types = {dependency, reference, contract}, §5.2 step2), 给每个受影响 entity 按 §5.3
    derive_response_hint 推导 suggested_response。

    去重: forward_slice 已是首达路径去重 (BFS), 不会重复同一 entity。
    """
    slice_result = cia_service.forward_slice(
        source_concept_id,
        max_depth=max_depth,
        edge_types=list(CASCADE_TRIGGER_EDGE_TYPES),
    )
    affected: list[CascadeAffectedEntity] = []
    for r in slice_result.results:  # type: SliceEntityResult
        hint = derive_response_hint(trigger_transition_type, r.entity.entity_type, r.path)
        affected.append(
            CascadeAffectedEntity(
                entity_type=r.entity.entity_type,
                entity_id=r.entity.entity_id,
                depth=r.depth,
                path=list(r.path),
                suggested_response=hint,
            ),
        )
    cascade_depth = max((e.depth for e in affected), default=0)
    return CascadeResult(
        source_concept_id=source_concept_id,
        triggered_by_event_id=triggered_by_event_id,
        trigger_transition_type=trigger_transition_type,
        affected_entities=tuple(affected),
        cascade_depth=cascade_depth,
        projection_watermark=slice_result.projection_watermark,
    )


# ════════════════════════════════════════════════════════════════════════════════
#  §8.2 物理目录 .towow/m21/{watermarks,dedup,logs}
# ════════════════════════════════════════════════════════════════════════════════


class M21Store:
    """§8.2 M-2.1 物理目录 store — .towow/m21/{watermarks,dedup,logs}。

    镜像 M-2.2 M22DedupStore / M-2.3 M23DedupStore 同模式 (家族平级目录, §8.2)。提供:
      - ensure_dirs: 建 watermarks/dedup/logs 三子目录
      - watermark read/write (per daemon): .towow/m21/watermarks/{daemon_name}.json
      - dedup marker (防同一异常重复产 finding): .towow/m21/dedup/{finding_hash}.marker
      - daemon run log path: .towow/m21/logs/{daemon_name}/{date}.log

    🔴 read/write 物理 state 是工作内部 state (非 canonical projection) —— 不绕 commit gate
    (canonical 事实仍走 event log); daemon 自动巡检/写 watermark 仍 gated (本 store 只提供存储原语)。
    """

    _SUBDIRS = ("watermarks", "dedup", "logs")

    def __init__(self, m21_dir: Path) -> None:
        self._m21_dir = Path(m21_dir)
        self._watermarks_dir = self._m21_dir / "watermarks"
        self._dedup_dir = self._m21_dir / "dedup"
        self._logs_dir = self._m21_dir / "logs"

    @property
    def watermarks_dir(self) -> Path:
        return self._watermarks_dir

    @property
    def dedup_dir(self) -> Path:
        return self._dedup_dir

    @property
    def logs_dir(self) -> Path:
        return self._logs_dir

    def ensure_dirs(self) -> None:
        """§8.2 物理目录: .towow/m21/{watermarks,dedup,logs}。"""
        for sub in self._SUBDIRS:
            (self._m21_dir / sub).mkdir(parents=True, exist_ok=True)

    # ── watermark (per daemon last_processed_seq) ──
    def _watermark_path(self, daemon_name: str) -> Path:
        return self._watermarks_dir / f"{daemon_name}.json"

    def read_watermark(self, daemon_name: str) -> int:
        """读 daemon 的 last_processed_seq watermark (无 → 0)。"""
        import json

        path = self._watermark_path(daemon_name)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return 0
        seq = data.get("last_processed_seq", 0) if isinstance(data, dict) else 0
        return int(seq) if isinstance(seq, int) else 0

    def write_watermark(self, daemon_name: str, last_processed_seq: int) -> Path:
        """写 daemon 的 last_processed_seq watermark。"""
        import json

        self.ensure_dirs()
        path = self._watermark_path(daemon_name)
        path.write_text(
            json.dumps({"daemon_name": daemon_name, "last_processed_seq": last_processed_seq}),
            encoding="utf-8",
        )
        return path

    # ── dedup marker (防同一异常重复产 finding, §8.2) ──
    @staticmethod
    def _hash_key(dedup_key: str) -> str:
        return hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

    def _marker_path(self, dedup_key: str) -> Path:
        return self._dedup_dir / f"{self._hash_key(dedup_key)}.marker"

    def is_duplicate(self, dedup_key: str) -> bool:
        """该 dedup key 是否已产过 finding (marker 存在)。"""
        return self._marker_path(dedup_key).exists()

    def mark(self, dedup_key: str, *, note: str = "") -> Path:
        """标记 dedup key 已产 (写 marker 文件; 内容存原始 key + note 供审计)。"""
        self.ensure_dirs()
        path = self._marker_path(dedup_key)
        path.write_text(f"dedup_key={dedup_key}\nnote={note}\n", encoding="utf-8")
        return path

    # ── daemon run log path (§8.2 logs/{daemon_name}/{date}.log) ──
    def log_path(self, daemon_name: str, date: str) -> Path:
        """daemon run log 路径 (.towow/m21/logs/{daemon_name}/{date}.log); 调用方自行写。"""
        return self._logs_dir / daemon_name / f"{date}.log"


__all__ = [
    "CASCADE_MAX_DEPTH",
    "CASCADE_TRIGGER_EDGE_TYPES",
    "CascadeAffectedEntity",
    "CascadeResult",
    "M21Store",
    "RESPONSE_ACKNOWLEDGE",
    "RESPONSE_IGNORE",
    "RESPONSE_RETRACT",
    "RESPONSE_REVERIFY",
    "compute_cascade",
    "derive_response_hint",
    "is_cascade_trigger",
]
