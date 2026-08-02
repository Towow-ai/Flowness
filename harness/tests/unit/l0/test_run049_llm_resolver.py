"""RUN-049 (EPIC-2 头条): llm_resolver 真调 LLM 出非兜底 verdict —— 单元层 (注入 runner, 不起 claude)。

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
  §6.4 Resolver Prompt 系统 (§6.4.1 system / §6.4.2 user template / §6.4.3 schema 强制);
  §6.2/§6.3 ResolverInput/Verdict schema; §9 prompt 物理存储。
M-0.3 §3.1 error_handling: infra 失败 (timeout/unreachable) → 保守兜底; exit0 但 shape 不匹配 →
  schema_invalid abort。

锁死:
  (1) 注入 ForkRunner 返回固定 verdict JSON → llm_resolver 出 model != conservative_fallback +
      judgments fallback_applied=False (真 LLM 判断, 非机械兜底) —— AC1。
  (2) prompt 文件真被读且渲染进 argv (候选 obligation_id + template 段头都进 claude -p prompt) —— AC2。
  (3) infra 失败三类 (claude 非零退出 / spawn 失败 / 超时) → ResolverError 子类 → 上游保守兜底。
  (4) exit0 但输出无 judgments / 承载字段 shape 不匹配 → ResolverSchemaInvalid (abort 路径)。
  (5) prompt 文件缺失 / 为空 → ResolverUnreachable → 保守兜底 (部署期缺文件不 abort 整条 pipeline)。
  全程注入 runner / replay, 不真起 claude 跑 CI。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from towow.l0.obligations.llm_resolver import (
    MODE_REAL,
    MODE_REPLAY,
    bind_llm_resolver,
    llm_resolver,
    load_prompts,
    render_user_prompt,
)
from towow.l0.obligations.resolver import (
    ResolverSchemaInvalid,
    ResolverTimeout,
    ResolverUnreachable,
    call_resolver,
)
from towow.l1.verification_fork import (
    ForkRunResult,
    ForkSpawnError,
    ForkTimeoutError,
)
from towow.schemas.enums import (
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SceneType,
)
from towow.schemas.resolver import (
    ConceptGraphNeighborhood,
    ObligationCandidate,
    ProjectionSlice,
    ResolverConfig,
    ResolverInput,
    TaskScope,
)

if TYPE_CHECKING:
    from pathlib import Path

# §6.4.1 system + §6.4.2 user template 的精简物理副本 (单测自给 prompts_dir, 不依赖仓库 .towow)。
_SYSTEM_PROMPT = (
    "You are the ObligationActivationResolver for the ToWow horizontal runtime layer.\n"
    "Output ONLY valid JSON matching the ResolverVerdict schema.\n"
)
_USER_TEMPLATE = (
    "=== TASK SCOPE ===\n"
    "task_id: {{task_id}}\n"
    "task_type: {{task_type}}\n"
    "target_artifacts: {{target_artifacts}}\n"
    "declared_write_set: {{declared_write_set}}\n"
    "touched_nodes: {{touched_nodes}}\n"
    "{{reentry_block}}\n\n"
    "=== PROJECTION SLICE ===\n"
    "{{concept_graph_neighborhood}}\n"
    "{{optional_slices}}\n\n"
    "=== CANDIDATE OBLIGATIONS ===\n"
    "{{candidate_obligations}}\n\n"
    "=== TASK ===\nOutput strict JSON matching ResolverVerdict schema. No prose outside JSON.\n"
)


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "obligation-system" / "resolver-prompts"
    d.mkdir(parents=True)
    (d / "system_prompt.md").write_text(_SYSTEM_PROMPT, encoding="utf-8")
    (d / "user_prompt_template.md").write_text(_USER_TEMPLATE, encoding="utf-8")
    return d


def _resolver_input(*, obligation_ids: tuple[str, ...] = ("obl-A",)) -> ResolverInput:
    return ResolverInput(
        task_scope=TaskScope(
            task_id="task-run049",
            task_type=SceneType.EXECUTION,
            target_artifacts=["src/foo.py"],
            reentry=False,
        ),
        candidates=[
            ObligationCandidate(
                obligation_id=oid,
                nature=ObligationNature.CONTRACT,
                severity=ObligationSeverity.NORMAL,
                scope_type=ObligationScopeType.SEMANTIC_DEPENDENT,
                scope_rule="applies when modifying foo",
                definition=f"demo obligation {oid}",
            )
            for oid in obligation_ids
        ],
        projection_slice=ProjectionSlice(
            concept_graph_neighborhood=ConceptGraphNeighborhood(
                nodes=[{"concept_id": "concept:foo", "definition": "the foo concept"}],
                edges=[],
            ),
        ),
        resolver_config=ResolverConfig(),
    )


def _claude_envelope(verdict_judgments: list[dict[str, object]]) -> str:
    """模拟 `claude -p --output-format json` 外层 envelope: verdict 藏在 .result 文本里。"""
    inner = {"judgments": verdict_judgments}
    return json.dumps({"type": "result", "result": json.dumps(inner)})


# ── AC1: 真 LLM 判断 → 非兜底 verdict ──────────────────────────────────────────


def test_llm_resolver_produces_non_fallback_verdict(prompts_dir: Path) -> None:
    """注入 runner 返固定 verdict → model=sonnet (!= conservative_fallback), fallback_applied=False。"""
    captured: dict[str, object] = {}

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        captured["argv"] = list(argv)
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope(
                [{"obligation_id": "obl-A", "hit": True, "confidence": 0.92, "reason": "foo matched", "evidence": []}],
            ),
            stderr="",
        )

    ri = _resolver_input()
    verdict = llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)

    # 非机械兜底: model 真用 sonnet (ResolverConfig 默认), 不是 conservative_fallback。
    assert verdict.meta.model == "sonnet"
    assert verdict.meta.model != "conservative_fallback"
    assert verdict.meta.total_fallbacks == 0
    assert [j.fallback_applied for j in verdict.judgments] == [False]
    j = verdict.judgments[0]
    assert j.obligation_id == "obl-A"
    assert j.hit is True
    assert j.confidence == pytest.approx(0.92)


def test_prompt_files_really_read_and_rendered_into_argv(prompts_dir: Path) -> None:
    """AC2: prompt 文件真被读 + 渲染进 claude -p prompt (候选 id + template 段头都在 argv 里)。"""
    captured: dict[str, object] = {}

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        captured["argv"] = list(argv)
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope(
                [{"obligation_id": "obl-A", "hit": False, "confidence": 0.85, "reason": "no match", "evidence": []}],
            ),
            stderr="",
        )

    ri = _resolver_input()
    llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)
    prompt_text = " ".join(str(a) for a in captured["argv"])
    # system prompt 文本进了 (verbatim 角色框架)
    assert "ObligationActivationResolver" in prompt_text
    # user template 段头进了 + 候选 obligation 渲染进了
    assert "TASK SCOPE" in prompt_text
    assert "CANDIDATE OBLIGATIONS" in prompt_text
    assert "obl-A" in prompt_text
    assert "task-run049" in prompt_text
    assert "concept:foo" in prompt_text  # 邻域真渲染


def test_resolver_fork_prompt_free_of_gatefork_dropfile_instruction(prompts_dir: Path) -> None:
    """回归守卫 (finding f-r12-4-resolver-dropfile-shape-conflict).

    背景: R12_4 曾把 resolver 迁到 BG_POLLED, 让它复用 verification_fork._DROP_INSTRUCTION_TEMPLATE
    的 verdict 落盘指令。该模板的示例是 gate-fork 形态 `printf '%s' '{"passed": true, ...}'`
    (passed 字段), 而 resolver fork 必须输出 ResolverVerdict 形态 `{"judgments":[...]}`。两条信号
    冲突 → fork 若跟示例走 → shape-invalid → parse_resolver_verdict 抛 ResolverSchemaInvalid →
    pipeline ABORT (非保守兜底)。owner 2026-06-29 (commit 3430c1bbc) 复核撤回整个 R12 fork 层
    BG_POLLED 切换, resolver 生产路径回退 headless build_fork_command + _default_runner, drop-file
    回收链物理移除。此测钉死那次回退不被静默重引: resolver 送进 fork 的 prompt 里
    **不得**出现 gate-fork drop 落盘契约标记, 也不得出现暗示 `passed` shape 的示例; 同时正向确认
    prompt 硬钉了 judgments/ResolverVerdict schema。任何未来重迁移若再拼进冲突示例, 此测立即红。
    """
    captured_argv: list[str] = []

    def fake_runner(argv, cwd, timeout_s):
        captured_argv.extend(str(a) for a in argv)
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope(
                [{"obligation_id": "obl-A", "hit": False, "confidence": 0.85, "reason": "no match", "evidence": []}],
            ),
            stderr="",
        )

    ri = _resolver_input()
    llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)
    prompt_text = " ".join(captured_argv)

    # 负向: 不得出现 gate-fork drop-file 回收契约的任何痕迹 (标记 / 冲突的 passed 示例)。
    assert "fork-spawn-bg-subscription-contract" not in prompt_text
    assert "结果回收契约" not in prompt_text
    assert '"passed": true' not in prompt_text
    assert '{"passed"' not in prompt_text
    # 正向: prompt 硬钉 resolver 应输出的 ResolverVerdict schema (无歧义信号, 非 gate 的 passed)。
    assert "ResolverVerdict" in prompt_text


def test_render_user_prompt_substitutes_all_tokens(prompts_dir: Path) -> None:
    """渲染后不残留任何 {{...}} 注入占位 (避免漏填把占位喂给 LLM)。"""
    system_text, user_template = load_prompts(prompts_dir)
    assert system_text.strip()
    rendered = render_user_prompt(user_template, _resolver_input())
    assert "{{" not in rendered
    assert "}}" not in rendered


def test_call_resolver_full_contract_with_llm(prompts_dir: Path) -> None:
    """经 call_resolver 全 §3.1 合约 (含 check_covers 覆盖校验) → 真 LLM verdict 通过。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope(
                [
                    {"obligation_id": "obl-A", "hit": True, "confidence": 0.9, "reason": "r", "evidence": []},
                    {"obligation_id": "obl-B", "hit": False, "confidence": 0.95, "reason": "r2", "evidence": []},
                ],
            ),
            stderr="",
        )

    ri = _resolver_input(obligation_ids=("obl-A", "obl-B"))
    resolver = bind_llm_resolver(prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)
    verdict = call_resolver(ri, resolver=resolver)
    assert verdict.meta.model == "sonnet"
    assert verdict.meta.total_candidates == 2
    assert {j.obligation_id for j in verdict.judgments} == {"obl-A", "obl-B"}
    assert all(j.fallback_applied is False for j in verdict.judgments)


# ── AC: infra 失败 → 保守兜底 (ResolverError 子类) ──────────────────────────────


def test_nonzero_exit_raises_unreachable_then_fallback(prompts_dir: Path) -> None:
    """claude 非零退出 → ResolverUnreachable → call_resolver 转保守兜底 (fallback_applied=True)。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        return ForkRunResult(returncode=1, stdout="", stderr="boom")

    ri = _resolver_input()
    with pytest.raises(ResolverUnreachable):
        llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)

    # 经 call_resolver → 保守兜底, 不 abort
    resolver = bind_llm_resolver(prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)
    verdict = call_resolver(ri, resolver=resolver)
    assert verdict.meta.model == "conservative_fallback"
    assert all(j.fallback_applied for j in verdict.judgments)


def test_spawn_error_raises_unreachable(prompts_dir: Path) -> None:
    """spawn OSError (ForkSpawnError) → ResolverUnreachable → 保守兜底。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        raise ForkSpawnError("claude not found")

    ri = _resolver_input()
    with pytest.raises(ResolverUnreachable):
        llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)


def test_timeout_raises_resolver_timeout(prompts_dir: Path) -> None:
    """fork 超时 (ForkTimeoutError) → ResolverTimeout → 保守兜底。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        raise ForkTimeoutError("timed out")

    ri = _resolver_input()
    with pytest.raises(ResolverTimeout):
        llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)


# ── AC: exit0 但 shape 不匹配 → schema_invalid (abort) ─────────────────────────


def test_exit0_no_judgments_raises_schema_invalid(prompts_dir: Path) -> None:
    """exit0 但输出无 judgments 列表 (解析失败) → ResolverSchemaInvalid (§6.4.3 abort, 非兜底)。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        return ForkRunResult(returncode=0, stdout="totally not json, just prose", stderr="")

    ri = _resolver_input()
    with pytest.raises(ResolverSchemaInvalid):
        llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)


def test_exit0_malformed_judgment_raises_schema_invalid(prompts_dir: Path) -> None:
    """exit0 但 judgment 承载字段缺 (无 hit/confidence) → ResolverSchemaInvalid (abort)。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        # judgments 存在但项缺 hit/confidence → 承载字段 shape 不匹配
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope([{"obligation_id": "obl-A", "reason": "missing hit/confidence"}]),
            stderr="",
        )

    ri = _resolver_input()
    with pytest.raises(ResolverSchemaInvalid):
        llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)


def test_low_confidence_without_uncertainty_is_schema_invalid(prompts_dir: Path) -> None:
    """confidence<0.7 但没填 uncertainty (违反 §6.3 不变量) → ResolverSchemaInvalid。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope(
                [{"obligation_id": "obl-A", "hit": True, "confidence": 0.3, "reason": "unsure", "evidence": []}],
            ),
            stderr="",
        )

    ri = _resolver_input()
    with pytest.raises(ResolverSchemaInvalid):
        llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)


def test_low_confidence_with_uncertainty_is_valid(prompts_dir: Path) -> None:
    """confidence<0.7 + 填了 uncertainty → 合法非兜底 verdict (保守注入但不裸跑, §6.5 精神)。"""

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        return ForkRunResult(
            returncode=0,
            stdout=_claude_envelope(
                [
                    {
                        "obligation_id": "obl-A",
                        "hit": True,
                        "confidence": 0.4,
                        "reason": "weak signal, red-line so include",
                        "uncertainty": "scope_rule partially matches",
                        "evidence": [],
                    },
                ],
            ),
            stderr="",
        )

    ri = _resolver_input()
    verdict = llm_resolver(ri, prompts_dir=prompts_dir, repo_dir=prompts_dir, mode=MODE_REAL, runner=fake_runner)
    j = verdict.judgments[0]
    assert j.confidence == pytest.approx(0.4)
    assert j.uncertainty == "scope_rule partially matches"
    assert j.fallback_applied is False  # 低置信但仍是真 LLM 判断, 非机械兜底


# ── AC: prompt 文件缺失 / 为空 → ResolverUnreachable → 保守兜底 ──────────────────


def test_missing_prompt_files_raises_unreachable(tmp_path: Path) -> None:
    """prompt 目录不存在 → load_prompts → ResolverUnreachable → 保守兜底 (不 abort pipeline)。"""
    empty_dir = tmp_path / "no-prompts"

    def fake_runner(argv, cwd, timeout_s):  # noqa: ANN001, ANN202
        msg = "should not be called when prompts missing"
        raise AssertionError(msg)

    ri = _resolver_input()
    with pytest.raises(ResolverUnreachable):
        llm_resolver(ri, prompts_dir=empty_dir, repo_dir=tmp_path, mode=MODE_REAL, runner=fake_runner)


def test_empty_prompt_file_raises_unreachable(tmp_path: Path) -> None:
    """prompt 文件存在但为空 → ResolverUnreachable (防"列了目录不写文件"也能优雅降级)。"""
    d = tmp_path / "obligation-system" / "resolver-prompts"
    d.mkdir(parents=True)
    (d / "system_prompt.md").write_text("", encoding="utf-8")
    (d / "user_prompt_template.md").write_text("", encoding="utf-8")
    ri = _resolver_input()
    with pytest.raises(ResolverUnreachable):
        load_prompts(d)
    with pytest.raises(ResolverUnreachable):
        llm_resolver(ri, prompts_dir=d, repo_dir=tmp_path, mode=MODE_REAL, runner=None)


# ── replay 模式 (集成测试注入真 verdict 的底座) ────────────────────────────────


def test_replay_mode_reads_verdict_from_file(tmp_path: Path) -> None:
    """replay 模式: 从文件读 verdict JSON → 非兜底 verdict (不起 claude, CI 安全)。"""
    replay = tmp_path / "verdict.json"
    replay.write_text(
        _claude_envelope(
            [{"obligation_id": "obl-A", "hit": True, "confidence": 0.88, "reason": "replayed", "evidence": []}],
        ),
        encoding="utf-8",
    )
    ri = _resolver_input()
    verdict = llm_resolver(ri, prompts_dir=tmp_path, repo_dir=tmp_path, mode=MODE_REPLAY, replay_file=replay)
    assert verdict.meta.model == "sonnet"
    assert verdict.judgments[0].fallback_applied is False
    assert verdict.judgments[0].confidence == pytest.approx(0.88)


def test_replay_mode_missing_file_raises_unreachable(tmp_path: Path) -> None:
    """replay 文件缺 → ResolverUnreachable → 保守兜底。"""
    ri = _resolver_input()
    with pytest.raises(ResolverUnreachable):
        llm_resolver(
            ri,
            prompts_dir=tmp_path,
            repo_dir=tmp_path,
            mode=MODE_REPLAY,
            replay_file=tmp_path / "nope.json",
        )
