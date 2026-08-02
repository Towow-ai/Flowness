"""M-0.7 Snapshot + consolidation — standalone snapshot creation + historical retention.

RUN-027 block 5: real standalone snapshot (§5.1 Mode 1) + retention (§5.2) replace the
E.2 stub. Cold-storage event-log segment archival (§7) + consolidation-produced snapshots
(§5.1 Mode 2) remain tied to the consolidation run machinery.
"""

from towow.l0.snapshot.snapshot import (
    DEFAULT_RETENTION,
    RestoreResult,
    SnapshotIntegrityError,
    SnapshotResult,
    create_standalone_snapshot,
    enforce_retention,
    fetch_snapshot_from_cold,
    list_archived_snapshot_dirs,
    list_snapshot_dirs,
    restore_from_snapshot,
)
from towow.l0.snapshot.state import (  # T-L0-35 (波1) — §9 运行时状态文件
    SnapshotModuleConfig,
    effective_retention,
    load_config,
    load_gc_state,
    load_retention_watermark,
    pull_retention_watermark,
    push_retention_watermark,
    write_gc_state,
)

__all__ = [
    "DEFAULT_RETENTION",
    "RestoreResult",
    "SnapshotIntegrityError",
    "SnapshotModuleConfig",
    "SnapshotResult",
    "create_standalone_snapshot",
    "effective_retention",
    "enforce_retention",
    "fetch_snapshot_from_cold",
    "list_archived_snapshot_dirs",
    "list_snapshot_dirs",
    "load_config",
    "load_gc_state",
    "load_retention_watermark",
    "pull_retention_watermark",
    "push_retention_watermark",
    "restore_from_snapshot",
    "write_gc_state",
]
