"""M-3.2 §7.1 auto-refresh — commit gate accept 时自动失效受影响 view.

# spec source:
#   06-l3-engineering-shell/M-3.2-ui-projection-detailed-design.md
#     §7.1 三种触发方式 — Auto-refresh: "commit gate accept 时自动 invalidate 受影响 view
#          —— next status 自动 re-render"
#     §7.2 Invalidation 规则表 (event_type → 失效 view 集)
#     判据 #11 (drift / 失效检测: view invalidation) + Inv2 (view 过期 → "加 invalidate_views()
#          必触发点 verification")
#
# 这是 §7.1 三触发里 auto-refresh 的**生产接线点**。机制本体 (event_type→view 集 + 标 stale)
# 在 l3 (views.INVALIDATION_RULES + ViewEngine.invalidate_views_for_event); 本模块只把它挂进
# commit gate 的 accept 路径: 一个 batch 真 accept 后, 按 batch 里出现的 canonical event_type
# 把受影响 view 标 stale (next `towow status --view` 自动 re-render —— spec §7.1 字面: re-render
# 是 lazy 的, accept 时只 invalidate)。
#
# 🔴 隔离 (硬要求): view 是 commit 的**下游只读副产物** (M-3.2 §13.2 Inv1: view layer 只读
# projection + event log, 不 emit / 不 catchup / 不改 canonical 状态)。commit 一旦 accept 即既成
# 事实; auto-refresh 失败**绝不能倒灌**——整段 try/except 包住, 任何异常只 log warning, 不向 gate
# 传播, 不影响 CommitAccepted sentinel / gate 返回。view 刷新失败 = owner 看到的 view 暂时过期
# (下次 render 自愈), 不是数据损坏。
#
# 无循环: l3 view layer 只读 projection 物化文件 + 写 .towow/views/*.md / .invalidation.json,
# 从不 emit event / 调 commit gate。gate → view_refresh → ViewEngine 是单向下游, 不回灌 event log
# (本模块不产生任何新 event —— 由 test_view_auto_refresh 的事件计数不变机械证明)。ViewEngine
# 走 lazy import (l0 不在模块顶层 import l3, 避免 l0↔l3 import cycle)。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord

_log = logging.getLogger(__name__)


def auto_refresh_views_after_accept(
    event_log: EventLog,
    accepted_records: Iterable[EventRecord],
) -> None:
    """M-3.2 §7.1 Auto-refresh — 一个 accepted batch 落地后, 按其 event_type 失效受影响 view.

    `accepted_records`: 本次 accept 真写进 log 的 record (含 domain + CommitAccepted sentinel)。
    对其中每个出现过的 canonical event_type, 查 §7.2 规则表把受影响 view 标 stale。

    🔴 fail-safe: 整段 try/except —— commit 已 accept 是既成事实, view 刷新出任何错只 log warning,
    绝不 raise / 绝不影响调用方 (gate accept 路径)。无返回值 (纯副作用: 标 stale)。
    """
    try:
        towow_dir = event_log.log_path.parent
        # batch 里出现过的 distinct canonical event_type (用 .value 对齐 INVALIDATION_RULES 的字符串键)。
        # 去重保序: 同 batch 多条同类型事件只失效一次。
        seen: list[str] = []
        for rec in accepted_records:
            etype = getattr(rec, "event_type", None)
            name = getattr(etype, "value", etype)
            if isinstance(name, str) and name not in seen:
                seen.append(name)
        if not seen:
            return
        # lazy import: 避免 l0 (gate) 在模块顶层 import l3 (view layer) 造成 import cycle。
        from towow.l3.view_engine import ViewEngine

        engine = ViewEngine(towow_dir)
        for event_type in seen:
            # §7.2: 未登记 event_type → 空失效集 → no-op (不动任何 view)。登记的 → 标受影响 view stale。
            engine.invalidate_views_for_event(
                event_type,
                reason=f"auto-refresh: commit gate accepted {event_type} (M-3.2 §7.1)",
            )
    except Exception:  # noqa: BLE001 — fail-safe: view 是 commit 下游副产物, 刷新失败绝不连累 commit。
        _log.warning(
            "view auto-refresh failed after commit accept (commit 不受影响; view 下次 render 自愈)",
            exc_info=True,
        )


__all__ = ["auto_refresh_views_after_accept"]
