"""RetentionLease — M-0.3 Patch 5 随机访问保护租约 (T-L0-20, 波1).

# spec source: 03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md Patch 5 (覆盖 §8)

M-0.3 是随机访问消费者, 不是顺序消费者。用 consumer cursor (= min(活跃任务起始 seq)) 当保护
水位有两个毛病: (a) 一个长任务卡住整段归档; (b) 不精确表达真正要保护的 event。改用 retention
lease 精确表达"哪些 event 还需被保护不归档", 且 expires_at 防永久卡住 (过期 lease 自动失效)。

Patch 5 明确: M-0.3 **不再注册为 consumer cursor** —— 它通过 lease 表达保护需求 (lease 是
cursor 的超集, 在 cursor 之上加而非替换)。lease min_seq 进 M-0.7 archive_seq_bound 的 Layer 1
(顺序保护): retention_watermark = min(consumer cursor, 活跃 lease 的保护下界) → 被 lease 引用段
不归档。

诚实边界 (与 spec 伪代码的边界校准): spec 伪代码写 `min(active leases 的 min_seq)`; 但 lease 语义是
"保护 seq ≥ min_seq 的所有 event", 而本仓 watermark 惯例是"最后可安全归档 seq (含端点, seq ≤ bound
即可归档)" (见 cursor consumer_watermark = cursor-1)。要让 seq==min_seq 那条真被保护, lease 对
watermark 的贡献须是 **min_seq - 1** (否则边界那条会被误归档)。本实现取 min_seq-1 (合约正确), 不照抄
伪代码的差一。input_hashes lease (resolver_cache) 是 abstractable_process, 走 M-0.7 digest /
§3.3 GC 语义根集保护 (§3.3 roots 未实现) —— 不进 watermark, 此处不假装已保护。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from pathlib import Path

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=False)


class LeaseReason(StrEnum):
    """M-0.3 Patch 5 — 保护原因枚举。"""

    ACTIVE_TASK = "active_task"
    RESOLVER_CACHE = "resolver_cache"
    CAPSULE_RECONSTRUCTION = "capsule_reconstruction"


class RetentionLease(BaseModel):
    """M-0.3 Patch 5 RetentionLease — 随机访问保护租约。

    保护范围三选一 (min_seq / event_ids / input_hashes); expires_at 防永久卡住。
    """

    model_config = _STRICT

    lease_id: str = Field(min_length=1)
    owner: str = "m03-capsule-assembler"
    reason: LeaseReason
    min_seq: int | None = Field(default=None, ge=0)
    event_ids: list[str] | None = None
    input_hashes: list[str] | None = None
    expires_at: str  # iso8601
    created_at: str  # iso8601


class RetentionLeaseStore:
    """File-backed retention lease store at .towow/events/leases/."""

    def __init__(self, leases_dir: Path) -> None:
        self._dir = leases_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        lease_id: str,
        reason: LeaseReason,
        expires_at: datetime,
        owner: str = "m03-capsule-assembler",
        min_seq: int | None = None,
        event_ids: list[str] | None = None,
        input_hashes: list[str] | None = None,
        now: datetime | None = None,
    ) -> RetentionLease:
        """创建一条 lease (任务开始 active_task / pipeline 后 capsule_reconstruction 等)。"""
        lease = RetentionLease(
            lease_id=lease_id,
            owner=owner,
            reason=reason,
            min_seq=min_seq,
            event_ids=event_ids,
            input_hashes=input_hashes,
            expires_at=expires_at.isoformat(),
            created_at=(now or datetime.now(tz=UTC)).isoformat(),
        )
        self._write(lease)
        return lease

    def release(self, lease_id: str) -> bool:
        """释放 (删除) 一条 lease (任务完成时)。返回是否真删了一条。"""
        path = self._dir / f"{lease_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def get(self, lease_id: str) -> RetentionLease | None:
        path = self._dir / f"{lease_id}.json"
        if not path.exists():
            return None
        return RetentionLease.model_validate_json(path.read_text(encoding="utf-8"))

    def all_leases(self) -> list[RetentionLease]:
        return [
            RetentionLease.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(self._dir.glob("*.json"))
        ]

    def active_leases(self, *, now: datetime | None = None) -> list[RetentionLease]:
        """未过期的 lease (expires_at > now)。过期 lease 自动失效 (不再保护, 防永久卡住归档)。"""
        moment = now or datetime.now(tz=UTC)
        active: list[RetentionLease] = []
        for lease in self.all_leases():
            try:
                expires = datetime.fromisoformat(lease.expires_at)
            except ValueError:
                continue  # 坏时间戳 → 当已过期 (保守: 不无限保护)
            if expires > moment:
                active.append(lease)
        return active

    def _write(self, lease: RetentionLease) -> None:
        path = self._dir / f"{lease.lease_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(lease.model_dump(), indent=2), encoding="utf-8")
        tmp.replace(path)


__all__ = ["LeaseReason", "RetentionLease", "RetentionLeaseStore"]
