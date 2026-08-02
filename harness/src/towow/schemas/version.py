"""Schema version tracking.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md §2.1 EventBase.schema_version (L99)
#     'schema_version: string # event schema 版本号，格式 "1.0.0"'
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

if TYPE_CHECKING:
    from towow.schemas.enums import BaseClassification

# Semantic version "MAJOR.MINOR.PATCH" per M-0.1 §2.1 EventBase.schema_version.
# Used as a constrained string type for all schema_version fields across event payloads.
SchemaVersion = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$", min_length=5)]

# Current v3 schema version baseline. Phase E.1 = 1.0.0. Bump on breaking schema changes.
CURRENT_SCHEMA_VERSION: str = "1.0.0"

# T-L0-05 (M-0.1 §5.2 Schema Evolution): 已知 schema 版本集。reader 遇不在此集的版本 = unknown,
# 按 base_classification 分级处理 (见 unknown_version_reader_action)。加新版本时扩此集 (+ upcast)。
KNOWN_SCHEMA_VERSIONS: frozenset[str] = frozenset({CURRENT_SCHEMA_VERSION})

# reader 对 unknown-version 事件的动作。
ReaderAction = Literal["fail_replay", "warn_and_skip", "skip"]


def is_known_schema_version(version: str) -> bool:
    return version in KNOWN_SCHEMA_VERSIONS


def unknown_version_reader_action(classification: BaseClassification) -> ReaderAction:
    """M-0.1 §5.2 reader_behavior — unknown schema 版本按 base_classification 分级。

    immutable_truth → fail_replay (停止重放, 不静默吞真相); abstractable_process → warn_and_skip;
    discardable_noise → skip。
    """
    from towow.schemas.enums import BaseClassification

    if classification is BaseClassification.IMMUTABLE_TRUTH:
        return "fail_replay"
    if classification is BaseClassification.ABSTRACTABLE_PROCESS:
        return "warn_and_skip"
    return "skip"


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "KNOWN_SCHEMA_VERSIONS",
    "ReaderAction",
    "SchemaVersion",
    "is_known_schema_version",
    "unknown_version_reader_action",
]
