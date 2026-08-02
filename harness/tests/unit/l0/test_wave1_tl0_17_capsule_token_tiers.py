"""波1 T-L0-17: MAX_CAPSULE_TOKENS 按 model tier 分级 + config 可调 (M-0.3 §7.1).

源表 done_criteria: 按 caller model tier 装配 capsule 时 token 上限取对应分级值, 且可经 config 调整。
现状曾硬编码 8000。spec §7.1: opus_task 80000 / sonnet_task 60000 / resolver 30000。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.capsule.pipeline import _max_capsule_tokens
from towow.l0.projection import ProjectionStore

if TYPE_CHECKING:
    from pathlib import Path


def test_tiered_defaults(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    assert _max_capsule_tokens("opus_task", store) == 80000
    assert _max_capsule_tokens("sonnet_task", store) == 60000
    assert _max_capsule_tokens("resolver", store) == 30000
    # 未知 tier 回退 opus_task
    assert _max_capsule_tokens("unknown", store) == 80000


def test_config_json_override(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    # .towow/config.json (store._dir.parent) 覆盖
    (tmp_path / "config.json").write_text(
        json.dumps({"MAX_CAPSULE_TOKENS": {"opus_task": 99999}}), encoding="utf-8",
    )
    assert _max_capsule_tokens("opus_task", store) == 99999  # config 覆盖生效
    assert _max_capsule_tokens("sonnet_task", store) == 60000  # 未覆盖的回退分级默认


def test_config_json_malformed_falls_back_to_default(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    (tmp_path / "config.json").write_text("{not valid", encoding="utf-8")
    assert _max_capsule_tokens("opus_task", store) == 80000  # 坏 config 不崩, 回退默认
