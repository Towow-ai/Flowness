"""RUN-049 (EPIC-2 头条): ObligationActivationResolver 的真 LLM 实现 —— 终结机械兜底。

# spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#   §6.4 Resolver Prompt 系统 (L848..L966) — §6.4.1 system prompt / §6.4.2 user prompt template /
#     §6.4.3 输出 schema 强制 (解析失败 / shape 不匹配 → schema_invalid → pipeline abort)
#   §6.2/§6.3 ResolverInput / ResolverVerdict schema (schemas.resolver 已钉死)
#   §9 物理存储 (L1185..L1202) — fork 读 `.towow/obligation-system/resolver-prompts/` 加载 prompt
#   §6.7 Resolver stateless (无独立事实源；不读/写 event log / projection / 文件系统)
#   line ~1444 / ResolverConfig.model 默认 sonnet — 用 Sonnet, 留 config 升 Opus 位

这是 M-0.6 §6.5 公开承认的过渡 (resolver.py:conservative_fallback_verdict 一直是 resolver 路径)
的**终结**: 把"语义充分性判断"那层真接上。**复用 l1.verification_fork 的 `claude -p` 骨架**
(build_fork_command / _default_runner / ForkRunner 注入缝 / robust JSON 抽取 / fail-closed),
不重造 spawn 引擎。

错误语义 (M-0.3 §3.1 / M-0.6 §6.4.3) ——
  - 子进程基础设施失败 (claude 不可用 / spawn OSError / 超时 / 非零退出 / prompt 文件缺):
    → 抛 ResolverUnreachable / ResolverTimeout (ResolverError 子类) → call_resolver 转**保守兜底**
    (每候选 hit=true confidence=0 fallback_applied=true, "宁可多注入也不能让 agent 裸跑")。
  - 子进程 exit 0 但输出无法解析成合法 ResolverVerdict (无 JSON / parse 失败 / pydantic 校验失败):
    → 抛 ResolverSchemaInvalid (**非** ResolverError) → call_resolver 不兜底 → pipeline abort
    (§6.4.3 "解析失败 / shape 不匹配 → schema_invalid"; resolver 不能产违约输出还被静默信任)。

为什么 infra 失败兜底而 shape 不匹配 abort: infra 失败是瞬态外部条件 (claude 暂时不可达),
保守多注入即可让 agent 安全跑; 而 exit 0 还吐不出合法 verdict 说明 resolver 合约被破坏,
静默兜底会掩盖一个真 bug —— 两者风险性质不同, spec §3.1/§6.4.3 据此分流。
"""

from __future__ import annotations

import functools
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from towow.l0.obligations.resolver import (
    ResolverSchemaInvalid,
    ResolverTimeout,
    ResolverUnreachable,
)
from towow.l1.verification_fork import (
    ForkRunResult,
    ForkSpawnError,
    ForkTimeoutError,
    _default_runner,
    _gather_candidates,
    build_fork_command,
    claude_headless_available,
    command_text,
)
from towow.schemas.enums import VerdictEvidenceRefType
from towow.schemas.resolver import (
    ObligationJudgment,
    ResolverVerdict,
    VerdictEvidence,
    VerdictMeta,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from towow.l1.verification_fork import ForkRunner
    from towow.schemas.resolver import (
        ObligationCandidate,
        ResolverInput,
        TaskScope,
    )

# 物理 prompt 文件名 (M-0.6 §9 — fork 从 `.towow/obligation-system/resolver-prompts/` 读)。
SYSTEM_PROMPT_FILENAME = "system_prompt.md"
USER_PROMPT_TEMPLATE_FILENAME = "user_prompt_template.md"

# 模式: real = 真起 claude -p; replay = 从文件读 verdict JSON (集成测试注入真 verdict, 不起 claude)。
MODE_REAL = "real"
MODE_REPLAY = "replay"

# resolver fork 无需任何工具 (§6.7 stateless: 纯 text-in JSON-out, 不读/写 fs / event log / projection)。
_RESOLVER_ALLOWED_TOOLS: tuple[str, ...] = ()


# ────────────────────────────────────────────────────────────────────────────────
#  Prompt loading + rendering (§6.4)
# ────────────────────────────────────────────────────────────────────────────────


def load_prompts(prompts_dir: Path) -> tuple[str, str]:
    """读 §6.4.1 system prompt + §6.4.2 user prompt template (raw 文本).

    Raises:
        ResolverUnreachable: 任一 prompt 文件缺失 / 为空 —— 当作 resolver 不可用 → 上游保守兜底
            (部署期缺文件不该让 capsule pipeline abort; 保守多注入仍让 agent 安全跑)。
    """
    system_path = prompts_dir / SYSTEM_PROMPT_FILENAME
    user_path = prompts_dir / USER_PROMPT_TEMPLATE_FILENAME
    try:
        system_text = system_path.read_text(encoding="utf-8")
        user_text = user_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        msg = f"resolver prompt 文件缺失 ({prompts_dir}): {exc!r} — 保守兜底"
        raise ResolverUnreachable(msg) from exc
    if not system_text.strip() or not user_text.strip():
        msg = f"resolver prompt 文件为空 ({prompts_dir}) — 保守兜底"
        raise ResolverUnreachable(msg)
    return system_text, user_text


def _render_entity_refs(refs: Sequence[object]) -> str:
    """渲染 declared_write_set / touched_nodes / attached_to 列表 (EntityRefShort)。"""
    if not refs:
        return "(none)"
    parts = []
    for r in refs:
        etype = getattr(r, "entity_type", None)
        eid = getattr(r, "entity_id", None)
        etype_v = getattr(etype, "value", etype)
        parts.append(f"{etype_v}:{eid}")
    return ", ".join(parts)


def _render_task_scope(task_scope: TaskScope) -> str:
    """渲染 === TASK SCOPE === 数据块 (§6.4.2)。"""
    tt = getattr(task_scope.task_type, "value", task_scope.task_type)
    target = ", ".join(task_scope.target_artifacts) if task_scope.target_artifacts else "(none)"
    lines = [
        f"task_id: {task_scope.task_id}",
        f"task_type: {tt}",
        f"target_artifacts: {target}",
        f"declared_write_set: {_render_entity_refs(task_scope.declared_write_set)}",
        f"touched_nodes: {_render_entity_refs(task_scope.touched_nodes)}",
    ]
    if task_scope.reentry and task_scope.reentry_context is not None:
        lines.append(
            "\nREENTRY: This is a re-evaluation. Previous run was "
            f"{task_scope.reentry_context.previous_run_id}.\n"
            f"Previous verdict event: {task_scope.reentry_context.previous_verdict_event_id}.\n"
            "Consider whether new touched_nodes change prior judgments.",
        )
    return "\n".join(lines)


def _render_neighborhood(nodes: Sequence[dict[str, object]], edges: Sequence[dict[str, object]]) -> str:
    """渲染概念图 N-hop 邻域 (concept_id → definition + edges)。"""
    if not nodes and not edges:
        return "(empty neighborhood — no concept-graph nodes within N hops of touched_nodes)"
    out: list[str] = []
    for n in nodes:
        cid = n.get("concept_id") or n.get("id") or n.get("node_id") or "?"
        definition = n.get("definition") or n.get("name") or ""
        out.append(f"- {cid}: {definition}")
    if edges:
        out.append("Edges:")
        for e in edges:
            src = e.get("from") or e.get("source") or e.get("src") or "?"
            dst = e.get("to") or e.get("target") or e.get("dst") or "?"
            etype = e.get("edge_type") or e.get("type") or "edge"
            out.append(f"  {src} --{etype}--> {dst}")
    return "\n".join(out)


def _render_candidates(candidates: Sequence[ObligationCandidate]) -> str:
    """渲染 === CANDIDATE OBLIGATIONS === 每候选块 (§6.4.2)。"""
    blocks: list[str] = []
    for c in candidates:
        nature = getattr(c.nature, "value", c.nature)
        severity = getattr(c.severity, "value", c.severity)
        scope_type = getattr(c.scope_type, "value", c.scope_type)
        block = [
            "---",
            f"obligation_id: {c.obligation_id}",
            f"nature: {nature}",
            f"severity: {severity}",
            f"scope_type: {scope_type}",
            f"scope_rule: {c.scope_rule}",
            f"definition: {c.definition}",
            f"attached_to: {_render_entity_refs(c.attached_to)}",
        ]
        if c.scope_hint is not None:
            hint_bits = []
            if c.scope_hint.target_artifacts:
                hint_bits.append(f"target_artifacts={c.scope_hint.target_artifacts}")
            if c.scope_hint.concept_anchors:
                hint_bits.append(f"concept_anchors={c.scope_hint.concept_anchors}")
            if c.scope_hint.task_type_filter:
                hint_bits.append(f"task_type_filter={c.scope_hint.task_type_filter}")
            if hint_bits:
                block.append(f"scope_hint: {'; '.join(hint_bits)}")
        block.append("---")
        blocks.append("\n".join(block))
    return "\n".join(blocks)


def render_user_prompt(user_template: str, resolver_input: ResolverInput) -> str:
    """把 §6.4.2 user_prompt_template 的 {{...}} 注入点用 resolver_input 渲染填实。

    模板里的静态框架 (=== 段头 / === TASK === 指令) 来自文件 verbatim; 只有 {{task_id}} 等
    数据注入点被替换。故"pipeline 真读到 prompt"非空: 文件缺/空 → load_prompts 抛 → 兜底。
    """
    ts = resolver_input.task_scope
    slice_ = resolver_input.projection_slice
    neighborhood = slice_.concept_graph_neighborhood
    optional_bits: list[str] = []
    if slice_.at_reference_graph_slice:
        at_ref = json.dumps(slice_.at_reference_graph_slice, ensure_ascii=False)
        optional_bits.append(f"@ reference graph slice: {at_ref}")
    if slice_.task_graph_slice:
        tg = json.dumps(slice_.task_graph_slice, ensure_ascii=False)
        optional_bits.append(f"task graph slice: {tg}")
    substitutions = {
        "{{task_id}}": ts.task_id,
        "{{task_type}}": str(getattr(ts.task_type, "value", ts.task_type)),
        "{{target_artifacts}}": ", ".join(ts.target_artifacts) if ts.target_artifacts else "(none)",
        "{{declared_write_set}}": _render_entity_refs(ts.declared_write_set),
        "{{touched_nodes}}": _render_entity_refs(ts.touched_nodes),
        "{{reentry_block}}": (
            _render_task_scope(ts).split("touched_nodes:", 1)[-1].split("\n", 1)[1]
            if (ts.reentry and ts.reentry_context is not None)
            else ""
        ),
        "{{concept_graph_neighborhood}}": _render_neighborhood(neighborhood.nodes, neighborhood.edges),
        "{{optional_slices}}": "\n".join(optional_bits),
        "{{candidate_obligations}}": _render_candidates(resolver_input.candidates),
    }
    rendered = user_template
    for token, value in substitutions.items():
        rendered = rendered.replace(token, value)
    return rendered


# ────────────────────────────────────────────────────────────────────────────────
#  Verdict parsing — LLM JSON → ResolverVerdict
# ────────────────────────────────────────────────────────────────────────────────


def _coerce_evidence(raw: object) -> list[VerdictEvidence]:
    """把 LLM 给的 evidence 列表宽松归一成 VerdictEvidence[].

    evidence 是补充字段 (非承载判断的 hit/confidence/reason); LLM 容易给松散形态, 故宽松:
    无法干净构造的项**丢弃**而非 abort (abort 留给承载字段的真 shape 不匹配)。
    """
    if not isinstance(raw, list):
        return []
    out: list[VerdictEvidence] = []
    valid_ref_types = {e.value for e in VerdictEvidenceRefType}
    for item in raw:
        if isinstance(item, dict):
            ref_type = item.get("ref_type")
            ref = item.get("ref")
            if isinstance(ref_type, str) and ref_type in valid_ref_types and isinstance(ref, str) and ref:
                try:
                    out.append(VerdictEvidence(ref_type=VerdictEvidenceRefType(ref_type), ref=ref))
                except (ValidationError, ValueError):
                    continue
    return out


def parse_resolver_verdict(
    stdout: str,
    resolver_input: ResolverInput,
    *,
    model: str,
) -> ResolverVerdict:
    """从 `claude -p` stdout 抽出 LLM verdict JSON → 构造合法 ResolverVerdict.

    复用 verification_fork._gather_candidates 的稳健抽取 (claude envelope `.result` + 散文 + fence
    + 多块) —— 找含 "judgments" 列表的候选块。meta 由我们构造保证一致性不变量 (§6.3); LLM 的
    每条判断 → ObligationJudgment(fallback_applied=False)。

    Raises:
        ResolverSchemaInvalid: 无 JSON / 无 judgments 键 / 承载字段 pydantic 校验失败 (§6.4.3
            "解析失败 / shape 不匹配 → schema_invalid → abort")。覆盖完整性 (judgments 集 ==
            candidates 集) 由 call_resolver 的 check_covers 再验, 这里只保证单条结构合法。
    """
    candidates = _gather_candidates(stdout)
    verdict_obj: dict[str, object] | None = None
    for obj in reversed(candidates):
        js = obj.get("judgments")
        if isinstance(js, list) and js:
            verdict_obj = obj
            break
    if verdict_obj is None:
        excerpt = stdout.strip()[:300]
        msg = f"LLM resolver 输出无 `judgments` 列表 (解析失败/shape 不匹配) — stdout 摘要: {excerpt!r}"
        raise ResolverSchemaInvalid(msg)

    raw_judgments = verdict_obj["judgments"]
    judgments: list[ObligationJudgment] = []
    try:
        for rj in raw_judgments:  # type: ignore[attr-defined]  # 验证过是 list (line ~281)
            if not isinstance(rj, dict):
                msg = f"judgment 项不是对象: {rj!r}"
                raise ResolverSchemaInvalid(msg)
            judgments.append(
                ObligationJudgment(
                    obligation_id=str(rj["obligation_id"]),
                    hit=bool(rj["hit"]),
                    confidence=float(rj["confidence"]),
                    reason=str(rj.get("reason") or "(no reason given)"),
                    evidence=_coerce_evidence(rj.get("evidence")),
                    uncertainty=(str(rj["uncertainty"]) if rj.get("uncertainty") not in (None, "") else None),
                    fallback_applied=False,  # 真 LLM 判断 —— 非兜底
                ),
            )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        msg = f"LLM resolver judgment 承载字段 shape 不匹配 (schema_invalid): {exc!r}"
        raise ResolverSchemaInvalid(msg) from exc

    if not judgments:
        msg = "LLM resolver judgments 为空 — schema_invalid"
        raise ResolverSchemaInvalid(msg)

    meta = VerdictMeta(
        verdict_id=f"vid-llm-{uuid.uuid4().hex[:12]}",
        resolver_runtime_ms=0,  # 由 llm_resolver 包一层后覆盖真实耗时
        model=model,
        contract_version=resolver_input.resolver_config.contract_version,
        total_candidates=len(judgments),
        total_hits=sum(1 for j in judgments if j.hit),
        total_fallbacks=0,
    )
    try:
        return ResolverVerdict(judgments=judgments, meta=meta)
    except ValidationError as exc:
        msg = f"ResolverVerdict 构造校验失败 (schema_invalid): {exc!r}"
        raise ResolverSchemaInvalid(msg) from exc


# ────────────────────────────────────────────────────────────────────────────────
#  The resolver callable
# ────────────────────────────────────────────────────────────────────────────────


def llm_resolver(
    resolver_input: ResolverInput,
    *,
    prompts_dir: Path,
    repo_dir: Path,
    mode: str = MODE_REAL,
    runner: ForkRunner | None = None,
    replay_file: Path | None = None,
    model: str | None = None,
) -> ResolverVerdict:
    """M-0.6 §6.4 真 LLM resolver — 起 `claude -p` 喂 §6.4 prompt, 解析出真 verdict.

    被 pipeline 通过 call_resolver(resolver_input, resolver=...) 调 (functools.partial 绑定
    prompts_dir/repo_dir/mode/runner — call_resolver 只传 resolver_input 一个位置参数)。

    Args:
        resolver_input: M-0.6 §6.2 ResolverInput (真候选 + 真 N-hop 邻域, RUN-048 已喂真 bundle)。
        prompts_dir: `.towow/obligation-system/resolver-prompts/` (§9 — fork 读 prompt 处)。
        repo_dir: claude -p 子进程 cwd (resolver 无需工具, 仅 cwd 占位)。
        mode: real (真起 claude -p) | replay (从 replay_file 读 verdict JSON, 集成测试注入)。
        runner: ForkRunner 注入缝 (单测假 claude -p 输出, 不真起进程跑 CI)。
        replay_file: replay 模式读 verdict 的文件。
        model: 覆盖模型 (默认 resolver_config.model, §6.4 Sonnet; config 可升 Opus)。

    Raises:
        ResolverUnreachable / ResolverTimeout: 基础设施失败 → call_resolver 转保守兜底。
        ResolverSchemaInvalid: exit 0 但输出非合法 verdict → call_resolver 不兜底 → pipeline abort。
    """
    resolved_model = model or resolver_input.resolver_config.model
    start = datetime.now(tz=UTC)

    if mode == MODE_REPLAY:
        if replay_file is None or not replay_file.is_file():
            msg = f"replay 模式缺 verdict 文件: {replay_file}"
            raise ResolverUnreachable(msg)
        stdout = replay_file.read_text(encoding="utf-8")
    else:
        system_text, user_template = load_prompts(prompts_dir)
        user_prompt = render_user_prompt(user_template, resolver_input)
        full_prompt = f"{system_text.strip()}\n\n{user_prompt}"
        argv = build_fork_command(
            full_prompt,
            model=resolved_model,
            allowed_tools=_RESOLVER_ALLOWED_TOOLS,
            output_format="json",
        )
        run = runner or _default_runner
        # 真起进程前确认 claude 可用 (注入 runner 时跳过 —— 测试假进程)。
        if runner is None and not claude_headless_available():
            msg = "claude headless (`claude -p`) 不可用 — resolver 保守兜底"
            raise ResolverUnreachable(msg)
        timeout_s = max(1, resolver_input.resolver_config.timeout_ms // 1000)
        try:
            result: ForkRunResult = run(argv, repo_dir, timeout_s)
        except ForkTimeoutError as exc:
            msg = f"resolver fork 超时 ({timeout_s}s) — 保守兜底: {exc}"
            raise ResolverTimeout(msg) from exc
        except ForkSpawnError as exc:
            msg = f"resolver fork spawn 失败 — 保守兜底: {exc}"
            raise ResolverUnreachable(msg) from exc
        if result.returncode != 0:
            msg = (
                f"resolver fork 非零退出 (exit={result.returncode}) — 保守兜底: "
                f"{result.stderr[:200]}"
            )
            raise ResolverUnreachable(msg)
        stdout = result.stdout
        _ = command_text(argv)  # 审计痕迹 (保留 build_fork_command 复用语义)

    verdict = parse_resolver_verdict(stdout, resolver_input, model=resolved_model)
    runtime_ms = int((datetime.now(tz=UTC) - start).total_seconds() * 1000)
    # meta.resolver_runtime_ms 覆盖真实耗时 (VerdictMeta frozen → model_copy)。
    new_meta = verdict.meta.model_copy(update={"resolver_runtime_ms": runtime_ms})
    return verdict.model_copy(update={"meta": new_meta})


def bind_llm_resolver(
    *,
    prompts_dir: Path,
    repo_dir: Path,
    mode: str = MODE_REAL,
    runner: ForkRunner | None = None,
    replay_file: Path | None = None,
    model: str | None = None,
) -> functools.partial[ResolverVerdict]:
    """把 llm_resolver 绑成 call_resolver 要的单参 callable (resolver_input -> ResolverVerdict)。"""
    return functools.partial(
        llm_resolver,
        prompts_dir=prompts_dir,
        repo_dir=repo_dir,
        mode=mode,
        runner=runner,
        replay_file=replay_file,
        model=model,
    )


__all__ = [
    "MODE_REAL",
    "MODE_REPLAY",
    "SYSTEM_PROMPT_FILENAME",
    "USER_PROMPT_TEMPLATE_FILENAME",
    "bind_llm_resolver",
    "llm_resolver",
    "load_prompts",
    "parse_resolver_verdict",
    "render_user_prompt",
]
