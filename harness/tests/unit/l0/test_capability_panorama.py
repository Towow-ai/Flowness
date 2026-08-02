"""T-TRACK-01 — panorama → capability baseline parser (pure function)."""

from __future__ import annotations

from towow.l0.capability.panorama import parse_panorama_baseline

_SAMPLE = """\
# preamble row ignored

## M-0.1

| 能力 | 类别 |
|---|---|
| EventIntent two-layer schema | done-real |
| seq monotonic历史迁移 (debt-abc123def456 blocks) | fake-done |

## M-0.5

| ScopeDrift gate | fake-done |
| ScopeDrift gate | unplanned |
| not-a-bucket-row | something-else |
"""


def test_parses_rows_module_and_bucket() -> None:
    entries = parse_panorama_baseline(_SAMPLE)
    # 4 real capability rows (the header row + separator + non-bucket row are skipped)
    assert len(entries) == 4
    by_name = [(e["module"], e["name"], e["classification"]) for e in entries]
    assert ("M-0.1", "EventIntent two-layer schema", "done-real") in by_name
    assert ("M-0.5", "ScopeDrift gate", "fake-done") in by_name


def test_links_debts_from_row() -> None:
    entries = parse_panorama_baseline(_SAMPLE)
    seq = next(e for e in entries if e["name"].startswith("seq"))
    assert seq["linked_debts"] == ["debt-abc123def456"]


def test_duplicate_name_in_module_disambiguated() -> None:
    entries = parse_panorama_baseline(_SAMPLE)
    ids = [e["capability_id"] for e in entries]
    assert len(ids) == len(set(ids))  # all unique despite the two "ScopeDrift gate" rows
    scopedrift_ids = sorted(i for i in ids if i.startswith("M-0.5::ScopeDrift gate"))
    assert scopedrift_ids == ["M-0.5::ScopeDrift gate", "M-0.5::ScopeDrift gate #1"]
