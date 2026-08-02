"""T-TRACK-01 — parse CAPABILITY-PANORAMA.md into the capability_status baseline.

One-time bootstrap concern: read the 2026-05-29 panorama (the reconciled 603-capability
truth-source) into baseline entries that `ingest_capability_baseline` event-sources. After
ingestion the projection is the source of truth and this parser is only re-run when the
panorama itself is revised. Mirrors the table-walk in 01-reconciliation/gen_status_603.py so
the event-sourced board reproduces the same 603 rows / buckets.
"""

from __future__ import annotations

import re

_BUCKETS = ("done-real", "fake-done", "unplanned", "legit-future", "never-specd", "uncertain")
_HEADER_RE = re.compile(r"^##+\s+(.+)")
_SEP_RE = re.compile(r"^\|[\s\-:|]+\|?\s*$")
_DEBT_RE = re.compile(r"debt-[0-9a-f]{6,}")


def _clean(s: str) -> str:
    return s.replace("**", "").replace("★", "").strip()


def parse_panorama_baseline(text: str) -> list[dict[str, object]]:
    """Parse panorama markdown text → list of baseline entry dicts.

    Each entry = {capability_id, module, name, classification, linked_debts}. capability_id is
    "{module}::{name}", disambiguated with a "#k" suffix on the rare in-file name collision so
    every entry is uniquely addressable by a CapabilityStatusAdvanced event.
    """
    entries: list[dict[str, object]] = []
    seen: dict[str, int] = {}
    module = "(前言)"
    for raw in text.splitlines():
        line = raw.rstrip()
        header = _HEADER_RE.match(line)
        if header:
            module = header.group(1).strip()
            continue
        if not line.startswith("|") or _SEP_RE.match(line):
            continue
        cells = [_clean(c) for c in line.split("|")[1:-1]]
        if not cells or cells[0] in ("能力", "类别"):
            continue
        bucket = next((c for c in cells if c in _BUCKETS), None)
        if bucket is None:
            continue
        name = cells[0]
        base_id = f"{module}::{name}"
        n = seen.get(base_id, 0)
        seen[base_id] = n + 1
        capability_id = base_id if n == 0 else f"{base_id} #{n}"
        entries.append(
            {
                "capability_id": capability_id,
                "module": module,
                "name": name,
                "classification": bucket,
                "linked_debts": _DEBT_RE.findall(line),
            },
        )
    return entries
