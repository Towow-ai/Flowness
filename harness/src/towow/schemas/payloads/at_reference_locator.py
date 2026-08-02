"""M-1.2 §4.5 — @ 引用 Typed-ID parser (AtReferenceLocator) — T-L1-12.

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md
#     §4.2 完整 Typed ID 语法 (L401..L431):
#       @<type>:<concept_name>[.<field_path>][#<state>][@<lock_event_id>]
#       type ∈ concept|field|state|transition|edge|invariant|consumer_list|brief|event
#     §4.5 Parser 规则 (L484..L526): PATTERN 正则 / parse / validate
#
# Why this module (T-L1-12, 源表: NONE 全新模块):
#   The CLI stored each --at-reference as an opaque raw string (after_state.at_reference) — a
#   typed @ locator was never actually parsed into its parts, so the字段级引用 (§4 "v3 反工程
#   崩塌的核心机制") was inert. R2 拍板 = "@ 引用做最完整不偷懒". This module is the §4.5
#   parser定稿: the canonical PATTERN, parse() → 5 部分 (type / concept_name / field_path /
#   state / lock_event_id), and validate() that rejects malformed locators fail-closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# §4.2 type enum — the 9 typed-id prefixes.
LOCATOR_TYPES: tuple[str, ...] = (
    "concept",
    "field",
    "state",
    "transition",
    "edge",
    "invariant",
    "consumer_list",
    "brief",
    "event",
)

# §4.5 PATTERN — anchored single-locator form. concept_name may carry a dot field_path
# (e.g. runs.status.queued); #state and @lock_event_id are optional suffixes.
#
# We split concept_name vs field_path post-match: the head token (before the first '.') is the
# concept_name; the remainder (dot-joined) is the field_path. This matches §4.2 part 3 (人类可读
# concept name, 复用 ConceptCreated.concept_name) + part 4 (.<field_path> dot-separated 嵌套).
PATTERN = re.compile(
    r"@"
    r"(?P<type>concept|field|state|transition|edge|invariant|consumer_list|brief|event)"
    r":"
    r"(?P<name_path>[a-zA-Z][a-zA-Z0-9_.\-]*)"
    r"(?:#(?P<state>[a-zA-Z][a-zA-Z0-9_\-]*))?"
    r"(?:@(?P<lock_event_id>[a-zA-Z0-9\-]+))?",
)

# A strict full-string match (the whole text must be exactly one locator) — for validate().
_FULLMATCH = re.compile(PATTERN.pattern + r"\Z")


@dataclass(frozen=True)
class ParsedAtReference:
    """The 5 parts of a typed @ locator (§4.5 parse output)."""

    type: str
    concept_name: str
    field_path: str | None
    state: str | None
    lock_event_id: str | None
    raw: str
    position: tuple[int, int]


def _split_name_path(name_path: str) -> tuple[str, str | None]:
    """head token = concept_name; dot-joined remainder = field_path (None if no dot)."""
    head, sep, rest = name_path.partition(".")
    return head, (rest if sep else None)


def parse(text: str) -> list[ParsedAtReference]:
    """§4.5 parse — find every typed @ locator in ``text`` and split into 5 parts.

    Returns one ParsedAtReference per match (a doc / definition may embed many). Non-locator
    text is ignored. Use validate() to reject malformed single-locator strings.
    """
    out: list[ParsedAtReference] = []
    for m in PATTERN.finditer(text):
        name, field_path = _split_name_path(m.group("name_path"))
        out.append(
            ParsedAtReference(
                type=m.group("type"),
                concept_name=name,
                field_path=field_path,
                state=m.group("state"),
                lock_event_id=m.group("lock_event_id"),
                raw=m.group(0),
                position=m.span(),
            ),
        )
    return out


def validate(text: str) -> tuple[bool, list[str]]:
    """§4.5 validate — structural validity of a SINGLE typed @ locator string.

    fail-closed on: missing '@' start / unknown type prefix / missing ':' / empty or
    ill-formed concept_name / trailing garbage (the whole string must be exactly one locator).

    Returns (is_valid, errors). This is the syntax/structure gate; target-existence + lock_event
    realness (§4.5 graph lookup) is a separate online check (validate_against_graph), kept out
    of the pure parser so the parser stays dependency-free.
    """
    errors: list[str] = []
    if not text.startswith("@"):
        errors.append("locator must start with '@'")
        return False, errors
    if ":" not in text:
        errors.append("locator missing ':' type separator (@<type>:<concept_name>)")
        return False, errors
    type_token = text[1:].split(":", 1)[0]
    if type_token not in LOCATOR_TYPES:
        errors.append(
            f"unknown type {type_token!r}; must be one of {', '.join(LOCATOR_TYPES)}",
        )
    m = _FULLMATCH.match(text)
    if m is None:
        errors.append(
            f"malformed locator {text!r}: does not match "
            "@<type>:<concept_name>[.<field_path>][#<state>][@<lock_event_id>]",
        )
        return False, errors
    name, _field = _split_name_path(m.group("name_path"))
    if not name:
        errors.append("empty concept_name")
    return (not errors), errors


def validate_against_graph(
    parsed: ParsedAtReference,
    *,
    concept_exists: object,
    resolve_concept_id: object = None,
    resolve_lock_event: object = None,
) -> list[str]:
    """§4.5 validate (online part) — target concept exists + lock_event 真实 + target 等值。

    Spec §4.5 ``validate`` (L510..L525): the pinned ``@lock_event_id`` must resolve to a *real*
    committed event whose acted-on concept == the referenced concept
    (``if not event or event.target_entity_id != target.concept_id`` → "Invalid lock_event").
    Before RUN-092 (R2 件B) only the existence half was wired, so a **forged lock id passed
    cleanly** (REALITY-AUDIT-V2 §1.2-b probe). This adds the lock-realness + target-等值 half.

    Injected callables keep the pure parser projection-free (caller wires real resolvers):
      ``concept_exists(concept_name) -> bool``          — target existence (dangling guard)
      ``resolve_concept_id(concept_name) -> str | None`` — name → concept_id (for 等值 compare)
      ``resolve_lock_event(lock_event_id) -> (found: bool, target_concept_id: str | None)``
                                                         — the locked event's acted-on concept

    Returns a list of errors (empty = valid). The lock checks only fire when a lock is pinned
    *and* ``resolve_lock_event`` is injected — backward-compatible with existing-callers that
    pass only ``concept_exists`` (no lock resolution, no behaviour change).
    """
    errors: list[str] = []
    target_known = True
    if callable(concept_exists) and not concept_exists(parsed.concept_name):
        errors.append(f"Unknown concept: {parsed.concept_name}")
        target_known = False  # spec: 'continue' — skip 等值 compare when target is unknown

    # §4.5 lock_event realness + target 等值 — only when a lock_event_id is pinned.
    if parsed.lock_event_id and callable(resolve_lock_event):
        found, lock_target_id = resolve_lock_event(parsed.lock_event_id)
        if not found:
            errors.append(
                f"Invalid lock_event for {parsed.concept_name}: lock_event_id "
                f"{parsed.lock_event_id!r} resolves to no committed event "
                "(伪造/悬空 lock — §4.5 fail-closed)",
            )
        elif target_known and callable(resolve_concept_id):
            ref_concept_id = resolve_concept_id(parsed.concept_name)
            if ref_concept_id is not None and lock_target_id != ref_concept_id:
                errors.append(
                    f"Invalid lock_event for {parsed.concept_name}: lock_event "
                    f"{parsed.lock_event_id!r} targets concept {lock_target_id!r} ≠ referenced "
                    f"concept {ref_concept_id!r} (§4.5 target 等值校验)",
                )
    return errors


__all__ = [
    "LOCATOR_TYPES",
    "PATTERN",
    "ParsedAtReference",
    "parse",
    "validate",
    "validate_against_graph",
]
