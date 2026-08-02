"""M-0.7 §8 Maintenance Agent Skill 接口契约 (RUN-070 AC2).

# spec source:
#   00-original-inputs/v3-design-bundle/05-l0-detailed-designs/
#     M-0.7-snapshot-consolidation-detailed-design.md
#     §8.1 Skill 定位 — L3 工程外壳层 agent driver; v3 初版只手动 `towow maintain`, v3.5+ 可接 cron
#     §8.2 Skill 能做的事 — 6 类能力 (propose consolidation/snapshot/archive + audit
#          reconstructability + inspect retention health + detect anomalies), 各调 M-0.7 §0 API
#     §8.3 Skill 不能做的事 (机制层硬约束) — 7 条禁止
#     §8.4 Skill 的 capsule 模板 — 复用 M-0.3 既有 maintenance scene_type (不新增)
#     §8.5 典型 Maintenance Agent Run 流程
#
# AC2 边界 (诚实): 本模块是 *接口契约定义* (schema / 调用约定) —— 把 §8 的能力清单、禁止清单、
# capsule 约定、auto-trigger 状态 codify 成机器可校验的一等对象, 并把它跟真实 maintenance SKILL.md
# 对齐 (verify_skill_md_conforms)。**不启动 daemon**: §8.1 v3 初版只手动 towow maintain, cron
# auto-trigger = MAINTENANCE_AUTO_TRIGGER_ENABLED False (gated)。实现归 M-3.1/L3 (本契约不替它实现)。
#
# 每条能力标 backing_interface (spec 命名的 M-0.7 §0 API) + implemented (是否已有真函数支撑):
# 契约忠实 codify §8 spec, 同时诚实标哪些 M-0.7 接口已落、哪些仍 spec-only (M-3.1 future), 不假装
# 全部已实现。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field

from towow.schemas.enums import MaintenanceMode, SceneType


@dataclass(frozen=True)
class MaintenanceCapability:
    """§8.2 一条 maintenance agent 能力 + 它调的 M-0.7 §0 接口。"""

    capability_id: str
    description: str
    input_desc: str
    output_desc: str
    backing_interface: str  # spec §8.2 命名的 M-0.7 API
    # 真支撑该接口的 import 路径 (module:fn); None = spec-only (实现归 M-3.1, 诚实未落)
    backing_symbol: str | None = None

    @property
    def implemented(self) -> bool:
        """backing_symbol 能 import 解析 → True; spec-only / 解析失败 → False (诚实)。"""
        if not self.backing_symbol:
            return False
        try:
            mod_name, _, sym = self.backing_symbol.partition(":")
            mod = importlib.import_module(mod_name)
        except (ImportError, ValueError):
            return False
        # 支持 module:Class.method 形态
        obj: object = mod
        for part in sym.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                return False
        return True


# ──────────────────────────────────────────────────────────────────────────────
#  §8.2 — Maintenance agent 能做的事 (6 类)
# ──────────────────────────────────────────────────────────────────────────────

MAINTENANCE_CAPABILITIES: tuple[MaintenanceCapability, ...] = (
    MaintenanceCapability(
        capability_id="propose_consolidation",
        description="提议 consolidation run (跨多 run 合并 + digest + snapshot + archive)",
        input_desc="lookback window 参数 (自动算或 Nature 给)",
        output_desc="ConsolidationEnvelope (含 read_set / write_set / digest 提议)",
        backing_interface="M-0.7.propose_consolidation() / run_consolidation()",
        backing_symbol="towow.l0.snapshot.consolidation_run:run_consolidation",
    ),
    MaintenanceCapability(
        capability_id="propose_snapshot",
        description="提议 snapshot (quick checkpoint)",
        input_desc="触发原因 (cutoff_seq=current_max_seq)",
        output_desc="SnapshotEnvelope / SnapshotResult",
        backing_interface="M-0.7.propose_snapshot(cutoff_seq=current_max_seq)",
        backing_symbol="towow.l0.snapshot.snapshot:create_standalone_snapshot",
    ),
    MaintenanceCapability(
        capability_id="propose_archive",
        description="提议 archive (manual GC)",
        input_desc="段范围或 GC verdict",
        output_desc="ArchiveEnvelope",
        backing_interface="M-0.7.propose_archive_segments(eligible_events)",
        backing_symbol="towow.l0.snapshot.gc:run_gc",
    ),
    MaintenanceCapability(
        capability_id="audit_consolidation_reconstructability",
        description="巡检 consolidation 可重建性 (read-only)",
        input_desc="历史 snapshot",
        output_desc="AuditResult (pass / fail) / ReconstructabilitySamplingResult",
        backing_interface="M-0.7.verify_consolidation_invariants (read-only) / §6.5 抽样",
        backing_symbol="towow.l0.snapshot.reconstructability:verify_reconstructability_sampling",
    ),
    MaintenanceCapability(
        capability_id="inspect_retention_health",
        description="巡检 retention 健康 (watermark 增长趋势 / 长时间 lease / hot 累积)",
        input_desc="(无)",
        output_desc="报告 (retention_watermark / active leases / hot segment 大小)",
        backing_interface="M-0.7.compute_retention_watermark() / list_active_leases()",
        backing_symbol="towow.l0.event_log.lease:RetentionLeaseStore.active_leases",
    ),
    MaintenanceCapability(
        capability_id="detect_anomalies",
        description="发现异常时产 FindingCreated (不自动处理, 留给 Nature/fix)",
        input_desc="(巡检派生)",
        output_desc="FindingCreated event",
        backing_interface="M-0.7 巡检派生 → Path B FindingCreated (经 commit gate)",
        backing_symbol=None,  # 巡检判断 + FindingCreated 组合, 归 maintenance skill 行为 (M-3.1)
    ),
)


# ──────────────────────────────────────────────────────────────────────────────
#  §8.3 — Maintenance agent 不能做的事 (机制层硬约束, 7 条)
# ──────────────────────────────────────────────────────────────────────────────

MAINTENANCE_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "不调 verify 直接写 archive event",
    "不查 GC 直接判断 event 可丢",
    "不写 SnapshotCreated (必经 commit gate)",
    "修改 retention_watermark (M-0.7 算出, 其他人只读)",
    "绕过 base_classification 三层语义",
    "删除已有 SnapshotCreated event",
    "修改已写入的 digest 内容 (错了走 DigestSuperseded)",
)

# §8.4 — capsule scene_type: 复用 M-0.3 既有 maintenance, 不新增。
MAINTENANCE_CAPSULE_SCENE_TYPE: str = SceneType.MAINTENANCE.value

# §8.1 — v3 初版只支持手动 `towow maintain`; cron auto-trigger gated (AC2 不启 daemon)。
MAINTENANCE_AUTO_TRIGGER_ENABLED: bool = False

# §8.5 / Patch M-2.2-D — maintenance run 的 4 个 mode (caller_params.maintenance_mode)。
MAINTENANCE_MODES: tuple[str, ...] = tuple(m.value for m in MaintenanceMode)


@dataclass(frozen=True)
class MaintenanceContract:
    """M-0.7 §8 maintenance agent skill 接口契约 (一等对象)。"""

    capabilities: tuple[MaintenanceCapability, ...] = MAINTENANCE_CAPABILITIES
    forbidden_actions: tuple[str, ...] = MAINTENANCE_FORBIDDEN_ACTIONS
    capsule_scene_type: str = MAINTENANCE_CAPSULE_SCENE_TYPE
    auto_trigger_enabled: bool = MAINTENANCE_AUTO_TRIGGER_ENABLED
    modes: tuple[str, ...] = MAINTENANCE_MODES


def get_maintenance_contract() -> MaintenanceContract:
    """M-0.7 §8 maintenance agent skill 接口契约。"""
    return MaintenanceContract()


def validate_maintenance_contract(contract: MaintenanceContract | None = None) -> list[str]:
    """契约内部自洽校验。返回违规列表 (空 = 通过)。

    - 能力非空且 id 唯一; 每条能力字段非空;
    - §8.3 禁止清单非空;
    - capsule scene_type 是真 SceneType (复用 M-0.3 maintenance, §8.4);
    - auto_trigger OFF (§8.1 v3 初版 + AC2 不启 daemon);
    - modes 是真 MaintenanceMode。
    """
    c = contract or get_maintenance_contract()
    violations: list[str] = []
    if not c.capabilities:
        violations.append("§8.2 能力清单为空")
    ids = [cap.capability_id for cap in c.capabilities]
    if len(ids) != len(set(ids)):
        violations.append(f"能力 id 重复: {ids}")
    for cap in c.capabilities:
        if not (cap.description and cap.input_desc and cap.output_desc and cap.backing_interface):
            violations.append(f"能力 {cap.capability_id} 字段不完整")
    if not c.forbidden_actions:
        violations.append("§8.3 禁止清单为空")
    valid_scenes = {s.value for s in SceneType}
    if c.capsule_scene_type not in valid_scenes:
        violations.append(f"§8.4 capsule scene_type {c.capsule_scene_type!r} 非合法 SceneType")
    if c.auto_trigger_enabled:
        violations.append("§8.1/AC2 红线: maintenance auto-trigger 必须 OFF (不启 daemon)")
    valid_modes = {m.value for m in MaintenanceMode}
    bad_modes = [m for m in c.modes if m not in valid_modes]
    if bad_modes:
        violations.append(f"非法 maintenance mode: {bad_modes}")
    return violations


def verify_skill_md_conforms(skill_md_text: str, contract: MaintenanceContract | None = None) -> list[str]:
    """把契约跟真实 maintenance SKILL.md 对齐 — SKILL.md 的 "我不做" 必须覆盖 §8.3 硬约束。

    返回缺失项 (空 = SKILL.md 完整反映了契约禁止清单)。这把契约从浮空定义钉进真实 skill:
    SKILL.md 若漏掉某条 §8.3 禁止 → maintenance agent 可能越界 → 契约校验抓出来。

    匹配用语义锚点 (非整句精确), 因 SKILL.md 用更口语化表达 (如 "我只读" vs "其他人只读")。
    """
    c = contract or get_maintenance_contract()
    text = skill_md_text
    missing: list[str] = []
    # 每条 §8.3 禁止的关键锚点 (在 SKILL.md 应能找到对应表达)
    anchors = {
        "不调 verify 直接写 archive event": ["verify", "archive"],
        "不查 GC 直接判断 event 可丢": ["GC", ""],
        "不写 SnapshotCreated (必经 commit gate)": ["SnapshotCreated", "commit gate"],
        "修改 retention_watermark (M-0.7 算出, 其他人只读)": ["retention_watermark"],
        "绕过 base_classification 三层语义": ["base_classification"],
        "删除已有 SnapshotCreated event": ["删除", "SnapshotCreated"],
        "修改已写入的 digest 内容 (错了走 DigestSuperseded)": ["DigestSuperseded"],
    }
    for action in c.forbidden_actions:
        needed = anchors.get(action, [action])
        if not all(tok in text for tok in needed if tok):
            missing.append(action)
    return missing


__all__ = [
    "MAINTENANCE_AUTO_TRIGGER_ENABLED",
    "MAINTENANCE_CAPABILITIES",
    "MAINTENANCE_CAPSULE_SCENE_TYPE",
    "MAINTENANCE_FORBIDDEN_ACTIONS",
    "MAINTENANCE_MODES",
    "MaintenanceCapability",
    "MaintenanceContract",
    "get_maintenance_contract",
    "validate_maintenance_contract",
    "verify_skill_md_conforms",
]
