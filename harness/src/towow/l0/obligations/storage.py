"""M-0.6 §9 obligation-system 物理存储 `.towow/obligation-system/` (RUN-038 Cap4).

# spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#   §9 物理存储 (L1185..L1202):
#     .towow/obligation-system/
#     ├── config.json              # resolver model / timeout / sampling defaults / contract_version
#     ├── resolver-prompts/
#     │   ├── system_prompt.md     # §6.4.1 system prompt 文本
#     │   └── user_prompt_template.md  # §6.4.2 user prompt 模板
#     └── bootstrap_obligations.yaml   # §5.2 protocol-level invariant 定义清单
#   §6.4.1 / §6.4.2 — resolver prompt 文本 (本模块物化为 md 文件)
#   §5.2 — 4 条 bootstrap obligation 定义 (本模块物化为 yaml; bootstrap.py 读它)
#
# 复用 T-L0-35 (M-0.7 §9 snapshot-module state.py) 的物理存储模式: 一个 init 函数物化
# `.towow/<module>/` 下的可调文件 + fail-safe 默认。
#
# 诚实边界 (LLM-gated): resolver-prompts/ 下的两个 md 是给 LLM resolver fork 读的 (§9 L1202:
# "fork 进程读 .towow/obligation-system/resolver-prompts/ 加载 prompt")。LLM resolver fork 的
# 真执行落 E.3 (M-0.6 §6.1; 当前 pipeline 走 conservative_fallback, 见 resolver.py)。本模块只
# 负责把 prompt 文本物化 + 目录布局到位 (daemon-independent); LLM 真用 prompts 那部分 gated 在 E.3。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from towow.l0.obligations.bootstrap_specs import (
    BOOTSTRAP_OBLIGATION_SPECS,
    DEFAULT_RESOLVER_CONTRACT_VERSION,
    DEFAULT_RESOLVER_MODEL,
    DEFAULT_RESOLVER_TIMEOUT_MS,
    SYSTEM_PROMPT_TEXT,
    USER_PROMPT_TEMPLATE_TEXT,
    spec_to_yaml_entry,
)
from towow.l0.projection.projection import os_replace_with_fsync

if TYPE_CHECKING:
    from pathlib import Path

_MODULE_DIRNAME = "obligation-system"


def obligation_system_dir(towow_dir: Path) -> Path:
    """`.towow/obligation-system/` directory path (not created here — see init)."""
    return towow_dir / _MODULE_DIRNAME


def _default_config() -> dict[str, object]:
    """§9 config.json defaults — resolver model / timeout / contract_version / sampling.

    Defaults match schemas.resolver.ResolverConfig + the prior hardcoded values so the system
    behaves identically whether or not config.json exists (the file makes them tunable).
    """
    return {
        "resolver_model": DEFAULT_RESOLVER_MODEL,
        "resolver_timeout_ms": DEFAULT_RESOLVER_TIMEOUT_MS,
        "resolver_contract_version": DEFAULT_RESOLVER_CONTRACT_VERSION,
        "audit_sampling_rate": 0.05,
    }


def _bootstrap_obligations_yaml() -> str:
    """Materialize the §5.2 bootstrap obligation definitions as the yaml bootstrap.py reads.

    The single source of truth for the literals is BOOTSTRAP_OBLIGATION_SPECS; the yaml is its
    on-disk realization (so `towow init` can ship + Nature can inspect the protocol invariants),
    and bootstrap_protocol_invariants reads it back when present.
    """
    doc = {"obligations": [spec_to_yaml_entry(s) for s in BOOTSTRAP_OBLIGATION_SPECS]}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _write_if_absent(path: Path, content: str) -> None:
    """Atomically write content only if the file does not already exist (idempotent init).

    Re-running `towow init` must not clobber a config a user has tuned (§9 config is tunable).
    """
    if path.exists():
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os_replace_with_fsync(tmp, path, path.parent)


def init_obligation_system_storage(towow_dir: Path) -> Path:
    """M-0.6 §9 — materialize `.towow/obligation-system/` layout. Idempotent.

    Creates (if absent):
      - config.json                          (tunable resolver model / timeout / contract_version)
      - resolver-prompts/system_prompt.md    (§6.4.1)
      - resolver-prompts/user_prompt_template.md (§6.4.2)
      - bootstrap_obligations.yaml           (§5.2 protocol invariant definitions)

    Returns the obligation-system dir. Called by `towow init` (M-3.1) after `.towow/` exists,
    before bootstrap_protocol_invariants reads the yaml.
    """
    base = obligation_system_dir(towow_dir)
    prompts = base / "resolver-prompts"
    prompts.mkdir(parents=True, exist_ok=True)

    _write_if_absent(base / "config.json", json.dumps(_default_config(), indent=2, ensure_ascii=False))
    _write_if_absent(prompts / "system_prompt.md", SYSTEM_PROMPT_TEXT)
    _write_if_absent(prompts / "user_prompt_template.md", USER_PROMPT_TEMPLATE_TEXT)
    _write_if_absent(base / "bootstrap_obligations.yaml", _bootstrap_obligations_yaml())
    return base


def load_bootstrap_obligation_entries(towow_dir: Path) -> list[dict[str, object]] | None:
    """Read `.towow/obligation-system/bootstrap_obligations.yaml`; None if absent / malformed.

    Returns the list of obligation entry dicts (slug / nature / severity / scope_type / scope_rule
    / definition / attached_to / mechanically_checkable / checker_scope_hint). None → caller falls
    back to the built-in BOOTSTRAP_OBLIGATION_SPECS (byte-for-byte compatible).
    """
    path = obligation_system_dir(towow_dir) / "bootstrap_obligations.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    entries = data.get("obligations")
    if not isinstance(entries, list) or not entries:
        return None
    out: list[dict[str, object]] = [e for e in entries if isinstance(e, dict)]
    return out or None


__all__ = [
    "init_obligation_system_storage",
    "load_bootstrap_obligation_entries",
    "obligation_system_dir",
]
