"""RUN-038 L0增强 M-0.7 §7.1 — Cold Storage 物理布局.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §7.1 Cold Storage 物理布局:
#       .towow/events/cold/archive-NNNNNN.jsonl.gz  (一个 archive ≈ 1 个 hot segment, gzip)
#       .towow/events/cold/staging/                  (prepare 阶段 staging tmp)
#       cold index (event_id → segment, location 路由)
#     关键设计决策: archive 文件大小跟 hot segment 一致 (1:1 映射); gzip 压缩; index 平铺 location 字段路由.
#
# Done 判据 (§17 #6 "Cold storage 协议"): 布局建出 + 格式对 spec —
#   archive 路径格式 archive-{seg:06d}.jsonl.gz, staging 路径格式 archive-{seg:06d}.jsonl.gz.tmp,
#   gzip 真压缩可解压, cold index event_id→segment 可建可查.
"""

from __future__ import annotations

import gzip
import json
from typing import TYPE_CHECKING

from towow.l0.snapshot.cold_storage import (
    cold_archive_path,
    cold_dir,
    cold_index_path,
    cold_staging_dir,
    cold_staging_path,
    load_cold_index,
    update_cold_index,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_cold_dir_layout_paths_match_spec(tmp_path: Path) -> None:
    """§7.1 — cold/ + cold/staging/ under .towow/events/; 1:1 segment→archive naming."""
    towow = tmp_path / ".towow"
    assert cold_dir(towow) == towow / "events" / "cold"
    assert cold_staging_dir(towow) == towow / "events" / "cold" / "staging"
    # archive-NNNNNN.jsonl.gz — 6-digit zero-padded, gzip suffix
    assert cold_archive_path(towow, 1) == towow / "events" / "cold" / "archive-000001.jsonl.gz"
    assert cold_archive_path(towow, 42) == towow / "events" / "cold" / "archive-000042.jsonl.gz"
    # staging tmp is the same name + .tmp under staging/
    assert (
        cold_staging_path(towow, 1)
        == towow / "events" / "cold" / "staging" / "archive-000001.jsonl.gz.tmp"
    )
    assert cold_index_path(towow) == towow / "events" / "cold" / "cold-index.json"


def test_cold_archive_gzip_round_trips_jsonl(tmp_path: Path) -> None:
    """§7.1 gzip 压缩 — 写出的 archive 是真 gzip 的 jsonl, 可解压逐行读回."""
    towow = tmp_path / ".towow"
    path = cold_archive_path(towow, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [{"event_id": "e1", "x": 1}, {"event_id": "e2", "x": 2}]
    with gzip.open(path, "wb") as f:
        for ev in events:
            f.write(json.dumps(ev).encode() + b"\n")
    # the file must be a real gzip member (magic 0x1f 0x8b), not plain text
    assert path.read_bytes()[:2] == b"\x1f\x8b"
    with gzip.open(path, "rb") as f:
        read_back = [json.loads(line) for line in f if line.strip()]
    assert read_back == events


def test_cold_index_update_and_load_round_trip(tmp_path: Path) -> None:
    """§7.1 cold index 平铺 — event_id → segment, location=cold; 可写可读可路由."""
    towow = tmp_path / ".towow"
    # empty / missing index loads as empty dict (fail-safe)
    assert load_cold_index(towow) == {}
    update_cold_index(towow, event_ids=["e1", "e2"], segment_id=5)
    update_cold_index(towow, event_ids=["e3"], segment_id=6)
    idx = load_cold_index(towow)
    assert idx == {
        "e1": {"location": "cold", "segment": 5},
        "e2": {"location": "cold", "segment": 5},
        "e3": {"location": "cold", "segment": 6},
    }


def test_cold_index_persists_across_loads(tmp_path: Path) -> None:
    """§7.1 — index 落盘后再 load 仍在 (不是内存即弃)."""
    towow = tmp_path / ".towow"
    update_cold_index(towow, event_ids=["a"], segment_id=1)
    # fresh load reads from disk
    assert load_cold_index(towow)["a"] == {"location": "cold", "segment": 1}
    assert cold_index_path(towow).exists()
