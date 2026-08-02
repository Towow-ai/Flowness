"""波2 T-L1-12: @ 引用 Typed-ID parser (AtReferenceLocator §4.5).

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §4.2 + §4.5
#
done_criteria (源表): 对样例 @ 串解析出 entity_type/id/field/state_hash 等 5 部分, 非法串被
validate 拒; 现整串当 raw string 存。本测试钉住 §4.2 全部样例 locator parse 出 5 部分 +
§4.5 validate fail-closed (未知 type / 缺冒号 / 非 @ 开头 / 尾部垃圾 被拒)。
"""

from __future__ import annotations

import pytest

from towow.schemas.payloads.at_reference_locator import (
    LOCATOR_TYPES,
    ParsedAtReference,
    parse,
    validate,
    validate_against_graph,
)


def _one(text: str) -> ParsedAtReference:
    parsed = parse(text)
    assert len(parsed) == 1, f"expected exactly 1 locator in {text!r}, got {len(parsed)}"
    return parsed[0]


# ─── §4.2 完整示例 — 每条 parse 出正确的 5 部分 ───────────────────────────────
def test_parse_concept_only() -> None:
    p = _one("@concept:runs")
    assert p.type == "concept"
    assert p.concept_name == "runs"
    assert p.field_path is None
    assert p.state is None
    assert p.lock_event_id is None


def test_parse_field_path() -> None:
    p = _one("@field:runs.status")
    assert p.type == "field"
    assert p.concept_name == "runs"
    assert p.field_path == "status"


def test_parse_nested_field_path() -> None:
    p = _one("@field:users.email.format")
    assert p.concept_name == "users"
    assert p.field_path == "email.format"


def test_parse_state_anchor() -> None:
    p = _one("@state:runs.status#queued")
    assert p.type == "state"
    assert p.concept_name == "runs"
    assert p.field_path == "status"
    assert p.state == "queued"
    assert p.lock_event_id is None


def test_parse_state_with_lock_event() -> None:
    p = _one("@state:runs.status#queued@event-abc123def")
    assert p.concept_name == "runs"
    assert p.field_path == "status"
    assert p.state == "queued"
    assert p.lock_event_id == "event-abc123def"


def test_parse_invariant_with_lock() -> None:
    p = _one("@invariant:runs.status.monotonic@event-xyz789")
    assert p.type == "invariant"
    assert p.concept_name == "runs"
    assert p.field_path == "status.monotonic"
    assert p.lock_event_id == "event-xyz789"


def test_parse_consumer_list() -> None:
    p = _one("@consumer_list:runs.status")
    assert p.type == "consumer_list"
    assert p.concept_name == "runs"
    assert p.field_path == "status"


def test_parse_brief_anchor() -> None:
    p = _one("@brief:BRIEF-2026-05-18-001#success_criteria")
    assert p.type == "brief"
    assert p.concept_name == "BRIEF-2026-05-18-001"
    assert p.state == "success_criteria"


def test_parse_event_with_lock() -> None:
    p = _one("@event:CommitAccepted@event-abc123")
    assert p.type == "event"
    assert p.concept_name == "CommitAccepted"
    assert p.lock_event_id == "event-abc123"


def test_all_nine_types_recognized() -> None:
    assert len(LOCATOR_TYPES) == 9
    for t in LOCATOR_TYPES:
        p = _one(f"@{t}:somename")
        assert p.type == t


def test_parse_multiple_in_one_text() -> None:
    text = "依赖 @concept:runs 和 @field:demands.status#matched 两个引用。"
    parsed = parse(text)
    assert len(parsed) == 2
    assert {p.type for p in parsed} == {"concept", "field"}


# ─── §4.5 validate — fail-closed on malformed ────────────────────────────────
def test_validate_legal() -> None:
    ok, errors = validate("@state:runs.status#queued@event-abc123def")
    assert ok, errors


@pytest.mark.parametrize(
    "bad",
    [
        "concept:runs",                # missing @
        "@runs.status",                # missing type:
        "@unknowntype:runs",           # unknown type prefix
        "@concept:",                   # empty concept_name
        "@concept:1runs",              # concept_name must start with a letter
        "@concept:runs extra garbage", # trailing garbage (not exactly one locator)
    ],
)
def test_validate_rejects_malformed(bad: str) -> None:
    ok, errors = validate(bad)
    assert not ok, f"malformed locator {bad!r} should be rejected"
    assert errors


def test_validate_against_graph_unknown_concept() -> None:
    p = _one("@concept:nonexistent")
    errors = validate_against_graph(p, concept_exists=lambda name: False)
    assert errors
    assert "Unknown concept" in errors[0]


def test_validate_against_graph_known_concept() -> None:
    p = _one("@concept:runs")
    errors = validate_against_graph(p, concept_exists=lambda name: name == "runs")
    assert errors == []
