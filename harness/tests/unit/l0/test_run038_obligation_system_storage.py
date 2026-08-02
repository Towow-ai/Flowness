"""RUN-038 L0增强 M-0.6 Cap4: §9 物理存储 .towow/obligation-system/.

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §9 (L1185..L1202):
  .towow/obligation-system/
  ├── config.json              # resolver model / timeout / sampling defaults / contract_version
  ├── resolver-prompts/
  │   ├── system_prompt.md     # §6.4.1 system prompt 文本
  │   └── user_prompt_template.md  # §6.4.2 user prompt 模板
  └── bootstrap_obligations.yaml   # §5.2 protocol-level invariant 定义清单

daemon-independent 范围(本 cap 做): 存储布局 + config.json 可调 + bootstrap_obligations.yaml
真被 bootstrap 读(非硬编码) + resolver-prompts 目录布局在。LLM resolver 真用 prompts 那部分
LLM-gated(resolver fork 落 E.3) —— 本 cap 只物化 prompt 文本, 不执行 LLM。

锁死: (1) init 后四件物理文件/目录在; (2) config.json 可调(改 resolver_contract_version → reader
读到新值); (3) bootstrap_obligations.yaml 真被 bootstrap_protocol_invariants 读(改 yaml definition
→ 产出的 ObligationCaptured definition 跟着变, 证非硬编码); (4) resolver-prompts 两 md 非空且含
§6.4 关键文本。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from towow.l0.capsule.config import resolver_contract_version
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.obligations.storage import (
    init_obligation_system_storage,
    obligation_system_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


def _towow(tmp_path: Path) -> Path:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    return towow


def test_init_materializes_four_physical_artifacts(tmp_path: Path) -> None:
    """§9 布局: init 后 config.json + resolver-prompts/{system,user} + bootstrap_obligations.yaml 在。"""
    towow = _towow(tmp_path)
    init_obligation_system_storage(towow)
    base = obligation_system_dir(towow)
    assert (base / "config.json").is_file()
    assert (base / "resolver-prompts" / "system_prompt.md").is_file()
    assert (base / "resolver-prompts" / "user_prompt_template.md").is_file()
    assert (base / "bootstrap_obligations.yaml").is_file()


def test_config_json_is_tunable(tmp_path: Path) -> None:
    """config.json 可调: 改 resolver_contract_version → reader 读到新值(非死配置)。"""
    towow = _towow(tmp_path)
    init_obligation_system_storage(towow)
    # default materialized value matches the resolver default 1.0.0
    assert resolver_contract_version(towow) == "1.0.0"
    cfg_path = obligation_system_dir(towow) / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["resolver_contract_version"] = "2.3.0"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert resolver_contract_version(towow) == "2.3.0", "config.json must drive the reader (tunable)"


def test_bootstrap_reads_yaml_not_hardcoded(tmp_path: Path) -> None:
    """bootstrap_obligations.yaml 真被 bootstrap 读: 改 yaml 里某条 definition → 产出的
    ObligationCaptured intent 的 definition 跟着变(证非 Python 硬编码字面量)。"""
    towow = _towow(tmp_path)
    init_obligation_system_storage(towow)
    yaml_path = obligation_system_dir(towow) / "bootstrap_obligations.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    # mutate the first bootstrap obligation's definition
    sentinel = "SENTINEL-DEFINITION-EDITED-BY-TEST"
    data["obligations"][0]["definition"] = sentinel
    # also change its slug so we can find it by obligation_id prefix
    data["obligations"][0]["slug"] = "obl-edited-probe"
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    intents = bootstrap_protocol_invariants(towow_dir=towow)
    edited = next(i for i in intents if i.payload["obligation_id"].startswith("obl-edited-probe-"))
    assert edited.payload["definition"] == sentinel, (
        "bootstrap must read the yaml definition, not a hardcoded literal"
    )


def test_bootstrap_default_byte_compatible_without_yaml(tmp_path: Path) -> None:
    """向后兼容: 无 .towow/obligation-system/(未 init) → bootstrap 回退内置 4 条种子, 数量/slug 不变。"""
    towow = _towow(tmp_path)  # no init → no yaml
    intents = bootstrap_protocol_invariants(towow_dir=towow)
    assert len(intents) == 4
    slugs = {i.payload["obligation_id"].rsplit("-", 1)[0] for i in intents}
    assert slugs == {
        "obl-intent-attribution",
        "obl-contract-preservation",
        "obl-novelty-required-for-supersede",
        "obl-capsule-required-before-run",
    }
    # all are system_bootstrap (un-retireable protocol invariants)
    assert all(i.payload["capture_source"] == "system_bootstrap" for i in intents)


def test_bootstrap_no_arg_still_works_byte_compatible(tmp_path: Path) -> None:
    """无参数调用(旧 caller 不传 towow_dir)仍产 4 条内置种子 —— 向后兼容旧 init 调用点。"""
    intents = bootstrap_protocol_invariants()
    assert len(intents) == 4
    assert all(i.payload["capture_source"] == "system_bootstrap" for i in intents)


def test_resolver_prompts_carry_spec_6_4_text(tmp_path: Path) -> None:
    """resolver-prompts 两 md 非空且含 §6.4 关键文本(LLM resolver 之后真读这俩文件加载 prompt,
    §9 L1202; 本 cap 只物化文本, 不执行 LLM —— LLM-gated 部分留 E.3)。"""
    towow = _towow(tmp_path)
    init_obligation_system_storage(towow)
    base = obligation_system_dir(towow)
    system_md = (base / "resolver-prompts" / "system_prompt.md").read_text(encoding="utf-8")
    user_md = (base / "resolver-prompts" / "user_prompt_template.md").read_text(encoding="utf-8")
    assert "ObligationActivationResolver" in system_md  # §6.4.1 framing
    assert "RED-LINE" in system_md  # §6.4.1 decision principle 1
    assert "=== TASK SCOPE ===" in user_md  # §6.4.2 template section
    assert "=== CANDIDATE OBLIGATIONS ===" in user_md  # §6.4.2 template section


def test_real_cli_init_materializes_storage_and_bootstraps_from_yaml(tmp_path: Path) -> None:
    """端到端验对合约: 真跑 CLI `init` → obligation-system 四件物化 + bootstrap 从 yaml 读
    (committed ObligationCaptured 的 definition == yaml 里的 definition, 证 init 真用 yaml)。"""
    from click.testing import CliRunner

    from towow.cli.main import cli
    from towow.l0.event_log import EventLog
    from towow.schemas.enums import EventType

    project = tmp_path / "proj"
    project.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0, result.output

    base = obligation_system_dir(project / ".towow")
    assert (base / "config.json").is_file()
    assert (base / "bootstrap_obligations.yaml").is_file()
    assert (base / "resolver-prompts" / "system_prompt.md").is_file()

    yaml_doc = yaml.safe_load((base / "bootstrap_obligations.yaml").read_text(encoding="utf-8"))
    yaml_defs = {e["slug"]: e["definition"] for e in yaml_doc["obligations"]}

    log = EventLog(project / ".towow" / "events.log")
    captured = [
        r for r in log.all_records() if r.event_type is EventType.OBLIGATION_CAPTURED
    ]
    assert len(captured) == 4
    for rec in captured:
        slug = str(rec.payload["obligation_id"]).rsplit("-", 1)[0]
        assert rec.payload["definition"] == yaml_defs[slug], (
            "the committed bootstrap obligation must match the yaml definition (init read the yaml)"
        )


def test_init_is_idempotent(tmp_path: Path) -> None:
    """重复 init 不崩、不重复写坏已有内容(可被 `towow init` 安全多次调用)。"""
    towow = _towow(tmp_path)
    init_obligation_system_storage(towow)
    # user edits config, then init runs again — must not clobber existing files
    cfg_path = obligation_system_dir(towow) / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["resolver_contract_version"] = "9.9.9"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    init_obligation_system_storage(towow)
    assert resolver_contract_version(towow) == "9.9.9", "re-init must not clobber an existing config"
