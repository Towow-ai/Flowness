"""M-0.3 §10 capsule-assembler 物理存储: `.towow/capsule-assembler/config.json` 可调参数。

# spec source: 03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#   §10 物理存储 (L701..L715): `.towow/capsule-assembler/config.json` — MAX_CAPSULE_TOKENS /
#     resolver timeout 等可调参数; cursor.json (consumer cursor)。
#   §2.1 / §2.10 row1 — Stage1 fresh_required catch-up 的 timeout (fail-closed)。
#   §3.4 — resolver 调用 timeout (默认 30s, "可通过 .towow/config 调整但不建议低于 10s")。

T-L0-21 (波2) 把 M-0.3 自身的可调参数收进一个真模块 (此前散落: T-L0-17 把 MAX_CAPSULE_TOKENS
读 `.towow/config.json`, 无 timeout 配置, 无 capsule-assembler 目录)。本模块按 spec §10 把 config
落在 `.towow/capsule-assembler/config.json`, 覆盖 **token + 两类 timeout** 三组参数, 让 "改
config.json 后 pipeline 行为随之变" 真成立。

诚实边界 (cursor.json): spec §10 列了 `cursor.json` (m03 consumer cursor)。但 M-0.3 Patch 5
(T-L0-20 已落 RetentionLeaseStore) 明确把 M-0.3 从 "consumer cursor" 改成 "retention lease" 表达
保护需求 (lease 落盘 `.towow/events/leases/`, 重启可读 — T-L0-20 覆盖)。故 cursor.json 被 Patch 5
的 lease 机制 **取代**, 不再单独物化一个会永远停在初值的死 cursor 文件 (假 done)。lease 持久化 +
重启恢复 = T-L0-20 已覆盖; 此处不重做。

向后兼容: config.json 缺失 / 坏 / 字段类型不符 → 逐项回退默认 (fail-safe, 不崩 pipeline)。
DEFAULT 值与改前硬编码一致 (token 分级 T-L0-17 旧值; catchup/resolver timeout = read_consistent /
ResolverConfig 旧默认), 故无 config.json 时行为 byte-for-byte 不变。
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_MODULE_DIRNAME = "capsule-assembler"
_CONFIG_FILENAME = "config.json"

# RUN-049 (M-0.6 §6.4): obligation resolver 运行模式 —— 默认真起 LLM (与 verification fork 惯例
# 一致: "默认安全=在岗、显式才关")。env opt-out 给测试/CI 降级到机械保守兜底, 不真起 claude。
#   llm      → 真起 claude --bg 喂 §6.4 prompt 出真 verdict (生产默认)
#   fallback → 跳过 LLM, 用 conservative_fallback_verdict (机械保守兜底; CI/测试缺省经 conftest)
#   replay   → 从 replay 文件读 verdict JSON (集成测试注入真 verdict, 不起 claude)
_ENV_RESOLVER_MODE = "TOWOW_OBLIGATION_RESOLVER_MODE"
_ENV_RESOLVER_REPLAY_FILE = "TOWOW_OBLIGATION_RESOLVER_REPLAY_FILE"
_RESOLVER_MODE_LLM = "llm"
_RESOLVER_MODE_FALLBACK = "fallback"
_RESOLVER_MODE_REPLAY = "replay"
_RESOLVER_MODES = frozenset({_RESOLVER_MODE_LLM, _RESOLVER_MODE_FALLBACK, _RESOLVER_MODE_REPLAY})
_DEFAULT_RESOLVER_MODE = _RESOLVER_MODE_LLM

# Stage1 fresh_required catch-up 超时 (ms) — 与 ProjectionStore.read_consistent 旧默认 2000 一致。
_DEFAULT_CATCHUP_TIMEOUT_MS = 2000
# Resolver 调用超时 (ms) — 与 schemas.resolver.ResolverConfig.timeout_ms 旧默认 30000 一致 (§3.4)。
_DEFAULT_RESOLVER_TIMEOUT_MS = 30000

# Obligation-system 物理存储 (M-0.6 §9) — resolver_contract_version 归 M-0.6 config, 读
# `.towow/obligation-system/config.json`。默认与 schemas.resolver.ResolverConfig.contract_version
# (1.0.0) 一致, 故无 config 时 cache key 行为不变 (M-0.6 §6.6 "合约变 cache miss" 的合约值入口)。
_OBLIGATION_MODULE_DIRNAME = "obligation-system"
_DEFAULT_RESOLVER_CONTRACT_VERSION = "1.0.0"
_CONTRACT_VERSION_PATTERN = r"^\d+\.\d+\.\d+$"


def _config_path(towow_dir: Path) -> Path:
    return towow_dir / _MODULE_DIRNAME / _CONFIG_FILENAME


def _load(towow_dir: Path) -> dict[str, object]:
    """读 capsule-assembler/config.json; 缺/坏/非 dict → {} (上层逐字段回退默认)。"""
    path = _config_path(towow_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _positive_int(value: object, fallback: int) -> int:
    """正整数否则回退 (bool 是 int 子类, 显式排除; ≤0 视为坏值)。"""
    if isinstance(value, bool):
        return fallback
    return value if isinstance(value, int) and value > 0 else fallback


def capsule_catchup_timeout_ms(towow_dir: Path) -> int:
    """Stage1 fresh_required catch-up 超时 (ms)。config.json 的 CATCHUP_TIMEOUT_MS 可覆盖。

    改 config.json 调小此值 → fresh catch-up 更快 fail-closed (超时 abort, T-L0-14)。
    """
    return _positive_int(_load(towow_dir).get("CATCHUP_TIMEOUT_MS"), _DEFAULT_CATCHUP_TIMEOUT_MS)


def resolver_timeout_ms(towow_dir: Path) -> int:
    """Resolver 调用超时 (ms, §3.4)。config.json 的 RESOLVER_TIMEOUT_MS 可覆盖。"""
    return _positive_int(_load(towow_dir).get("RESOLVER_TIMEOUT_MS"), _DEFAULT_RESOLVER_TIMEOUT_MS)


def resolver_contract_version(towow_dir: Path) -> str:
    """M-0.6 §6.6 resolver_contract_version — folded into the cache key (input_hash).

    Read from `.towow/obligation-system/config.json` (M-0.6 §9 obligation-system config, key
    `resolver_contract_version`). When M-0.6 bumps the resolver prompt / output schema / decision
    principles it bumps this value, so old cache entries auto-invalidate under the new contract
    ("prompt / schema 版本变更 cache miss"). Missing / malformed / non-semver → default 1.0.0
    (matches schemas.resolver.ResolverConfig.contract_version, so the cache key is byte-for-byte
    unchanged when unconfigured).
    """
    import re

    value = _load_obligation_config(towow_dir).get("resolver_contract_version")
    if isinstance(value, str) and re.fullmatch(_CONTRACT_VERSION_PATTERN, value):
        return value
    return _DEFAULT_RESOLVER_CONTRACT_VERSION


def _load_obligation_config(towow_dir: Path) -> dict[str, object]:
    """读 obligation-system/config.json; 缺/坏/非 dict → {} (逐字段回退默认)。"""
    path = towow_dir / _OBLIGATION_MODULE_DIRNAME / _CONFIG_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_obligation_resolver_mode(towow_dir: Path) -> str:
    """RUN-049 (M-0.6 §6.4): resolver 运行模式 —— env > obligation-system/config.json > 默认 llm.

    优先级 (同 verification fork resolve_fork_mode 惯例): env TOWOW_OBLIGATION_RESOLVER_MODE >
    config.json `resolver_mode` > 默认 "llm" (生产默认真起 LLM, "默认安全=在岗")。未知值 → 回退
    默认 llm (不让坏配置静默关掉 resolver 判断力)。CI/测试经 conftest 显式 env=fallback 降级。
    """
    env_val = os.environ.get(_ENV_RESOLVER_MODE)
    if env_val is not None:
        v = env_val.strip().lower()
        if v in _RESOLVER_MODES:
            return v
    cfg_val = _load_obligation_config(towow_dir).get("resolver_mode")
    if isinstance(cfg_val, str) and cfg_val.strip().lower() in _RESOLVER_MODES:
        return cfg_val.strip().lower()
    return _DEFAULT_RESOLVER_MODE


def obligation_resolver_replay_file(towow_dir: Path) -> str | None:
    """replay 模式读 verdict 的文件路径 — env TOWOW_OBLIGATION_RESOLVER_REPLAY_FILE >
    obligation-system/config.json `resolver_replay_file`。None = 未配置 (replay 模式将兜底)。"""
    env_val = os.environ.get(_ENV_RESOLVER_REPLAY_FILE)
    if env_val and env_val.strip():
        return env_val.strip()
    cfg_val = _load_obligation_config(towow_dir).get("resolver_replay_file")
    if isinstance(cfg_val, str) and cfg_val.strip():
        return cfg_val.strip()
    return None


def obligation_resolver_model_override(towow_dir: Path) -> str | None:
    """resolver 模型覆盖 — obligation-system/config.json `resolver_model` (§6.4 默认 sonnet,
    config 可升 opus)。None = 用 ResolverConfig.model 默认 (sonnet)。"""
    cfg_val = _load_obligation_config(towow_dir).get("resolver_model")
    if isinstance(cfg_val, str) and cfg_val.strip():
        return cfg_val.strip()
    return None


def capsule_max_tokens_override(towow_dir: Path, model_tier: str) -> int | None:
    """MAX_CAPSULE_TOKENS[model_tier] 覆盖值 (T-L0-17 的分级 token 上限)。

    config.json 形: {"MAX_CAPSULE_TOKENS": {"opus_task": 80000, ...}}。无覆盖 → None
    (caller 用其分级默认)。改 config.json 调小 → capsule 截断阈值随之变 (truncation 行为变)。
    """
    override = _load(towow_dir).get("MAX_CAPSULE_TOKENS")
    if isinstance(override, dict):
        value = override.get(model_tier)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


__all__ = [
    "capsule_catchup_timeout_ms",
    "capsule_max_tokens_override",
    "obligation_resolver_model_override",
    "obligation_resolver_replay_file",
    "resolve_obligation_resolver_mode",
    "resolver_contract_version",
    "resolver_timeout_ms",
]
