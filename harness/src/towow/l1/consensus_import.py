"""M-1.2 §13.12 — consensus-import wrapper: parse a human-edited consensus amendment markdown.

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md
#     §13.12 对 M-3.1: "import wrapper 复用 M-1.1 模式（full republish with supersede）"
#     §12 否决 markdown 作为 source of truth / 否决双向自动同步 — 人手编辑必走显式 import wrapper
#     §8.5 supersede protocol — 修改已冻结共识不重建, 走 supersede (带 novelty)
#   04-l1-intelligence/M-1.1-interview-skill-detailed-design.md §9.2 import wrapper /
#     §9.3 不做双向自动同步 (显式 import 而非文件监听)
#
# Why this module (T-L1 consensus-import, 源表: cli consensus-import was an E.2 stub):
#   event log is canonical / markdown is projection-export; a human who edits the consensus
#   markdown must route the change back through an explicit import wrapper → EventIntent → commit
#   gate (never a file-watch auto-sync, §12 / §9.3). This module is the parse half: it reads the
#   amendment markdown's YAML frontmatter `amends:` list into typed ConsensusAmend records. The CLI
#   half (cli.main consensus-import) turns each record into a ConceptGraphProposal supersede (full
#   republish with supersede — the same payload `consensus supersede` emits), inheriting the
#   superseded ConceptCreated's session_id for lineage. Pure parser → no event-log dependency here.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml


class ConsensusImportError(ValueError):
    """Malformed consensus amendment markdown (fail-closed — never silently no-op)."""


@dataclass(frozen=True)
class ConsensusAmend:
    """One concept amendment from the import markdown (§9.2 diff → EventIntent input)."""

    concept_id: str
    new_definition: str
    novelty: str
    novelty_type: str  # SupersedeNoveltyType value; default applied by parser


_DEFAULT_NOVELTY_TYPE = "corrected_error"
_VALID_NOVELTY_TYPES = frozenset(
    {"new_evidence", "corrected_error", "scope_change", "nature_override"},
)


def _split_frontmatter(text: str) -> str:
    """Return the YAML frontmatter block (between the leading `---` fences).

    fail-closed if there is no frontmatter — the amendment must declare `amends:` structurally,
    a body-only markdown carries nothing to re-import (§9.2 the wrapper translates a *diff*, not
    free prose).
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise ConsensusImportError(
            "amendment markdown has no YAML frontmatter — expected a leading '---' fence with an "
            "`amends:` list (consensus-import translates a structured diff, not free prose).",
        )
    # drop the opening fence line, then take up to the closing fence
    after_open = stripped[3:].lstrip("\n")
    end = after_open.find("\n---")
    if end == -1:
        raise ConsensusImportError("amendment frontmatter is not closed by a '---' fence.")
    return after_open[:end]


def parse_amendment(text: str) -> list[ConsensusAmend]:
    """Parse the amendment markdown into ConsensusAmend records (fail-closed on any malformed entry).

    Contract: frontmatter `amends:` is a non-empty list; each entry needs concept_id +
    new_definition + non-blank novelty; novelty_type defaults to corrected_error and must be a
    valid SupersedeNoveltyType. Raises ConsensusImportError otherwise — the CLI exits non-zero and
    emits nothing (no phantom / no vacuous supersede).
    """
    frontmatter = _split_frontmatter(text)
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ConsensusImportError(f"amendment frontmatter YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise ConsensusImportError("amendment frontmatter must be a YAML mapping.")
    raw_amends = data.get("amends")
    if not isinstance(raw_amends, list) or not raw_amends:
        raise ConsensusImportError(
            "amendment frontmatter has no non-empty `amends:` list — nothing to re-import.",
        )

    out: list[ConsensusAmend] = []
    for i, entry in enumerate(raw_amends):
        if not isinstance(entry, dict):
            raise ConsensusImportError(f"amends[{i}] is not a mapping.")
        concept_id = str(entry.get("concept_id", "")).strip()
        new_definition = str(entry.get("new_definition", "")).strip()
        novelty = str(entry.get("novelty", "")).strip()
        novelty_type = str(entry.get("novelty_type", _DEFAULT_NOVELTY_TYPE)).strip()
        if not concept_id:
            raise ConsensusImportError(f"amends[{i}] missing concept_id.")
        if not new_definition:
            raise ConsensusImportError(f"amends[{i}] ({concept_id}) missing new_definition.")
        if not novelty:
            raise ConsensusImportError(
                f"amends[{i}] ({concept_id}) missing novelty — M-0.5 NoveltyCheck requires a "
                "non-empty reason for every supersede.",
            )
        if novelty_type not in _VALID_NOVELTY_TYPES:
            raise ConsensusImportError(
                f"amends[{i}] ({concept_id}) novelty_type={novelty_type!r} invalid; must be one of "
                f"{', '.join(sorted(_VALID_NOVELTY_TYPES))}.",
            )
        out.append(
            ConsensusAmend(
                concept_id=concept_id,
                new_definition=new_definition,
                novelty=novelty,
                novelty_type=novelty_type,
            ),
        )
    return out


__all__ = ["ConsensusAmend", "ConsensusImportError", "parse_amendment"]
