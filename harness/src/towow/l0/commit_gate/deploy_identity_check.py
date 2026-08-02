"""Byte-identity deploy gate — work complete 相位的 src↔live 字节漂移门 (T-RMD-S4-BYTE-IDENTITY).

# 根治 f-sub-byte-identity-gate-missing
#
# 接进点: work complete 的 run_all_execution_blocking_checks (commit gate 落地相位)。当本 run 的
# actual_write_set 命中 src/towow/{skills,glue} 前缀 → 对每个被碰 src 文件比 sha256(render(src))
# == sha256(deployed live)。任一现存部署面 live ≠ src → 门 failed → work complete fail-closed
# (TaskRunCompleted 不 emit, 改动不落地)。堵 "改 src 不 redeploy" 的 src↔live 漂移 (像 L4-F1 MCP
# matcher 那种漂移再也进不了'已完成')。
#
# 分层 (l0 不依赖 shell): render_fn 由 CLI 组合点注入 (work_md_render) —— work.md 的 live = render
# _work_command(src, layout="harness_dev") (实测: 该渲染逐字节等于本仓部署的 .claude/commands/work
# .md)。逐字节类胶水/skills 用 identity (render=None 即可)。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from towow.l0.commit_gate.deploy_manifest import (
    DeployPair,
    deploy_surface_roots,
    enumerate_pairs,
    src_tail,
)
from towow.schemas.payloads.task_run_completed_checks import (
    ExecutionBlockingCheck,
    execution_evidence,
)

# work.md 渲染器签名: src 文本 → 期望 live 文本。CLI 注入 partial(render_work_command,
# layout="harness_dev")。None = 不可用 (渲染对就当无法核 → fail-closed, 不静默放行)。
WorkMdRender = Callable[[str], str]

_CHECK_ID = "execution.deploy_byte_identity"
_MAX_EVIDENCE_ITEMS = 8


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_live_bytes(pair: DeployPair, work_md_render: WorkMdRender | None) -> tuple[bytes | None, str | None]:
    """期望的 live 字节 (render 后)。返回 (bytes, None) 或 (None, 错误描述)。"""
    raw = pair.source.read_bytes()
    if not pair.rendered:
        return raw, None
    if work_md_render is None:
        return None, f"{pair.live_rel}: 渲染类部署对但未注入 render_fn (无法核 → fail-closed)"
    try:
        rendered = work_md_render(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:  # pragma: no cover - 渲染异常防御
        return None, f"{pair.live_rel}: render 失败 {exc!r}"
    return rendered.encode("utf-8"), None


def check_deploy_identity(
    touched_paths: list[str],
    *,
    work_md_render: WorkMdRender | None = None,
    skills_src: Path | None = None,
    glue_src: Path | None = None,
    surface_roots: list[Path] | None = None,
) -> tuple[bool, str]:
    """对本 run 碰过的 src 部署源, 比 sha256(render(src)) == sha256(live)。

    touched_paths: 本 run 的 actual write set 文件路径 (git-root-relative)。
    work_md_render: work.md 渲染器 (CLI 注入)。skills_src/glue_src/surface_roots 可注入 (测试用)。

    返回 (passed, evidence):
      - 无 touched 命中部署源 → (True, vacuous pass)
      - 某现存 live 面 ≠ src (render 后) / 渲染类缺 render_fn → (False, 漂移清单)
      - 全部现存面逐字节相等 → (True, 已核面计数)
    诚实边界: live 面物理不存在 = 该面未部署 (非漂移), 跳过该面; 该 src 文件无任何现存 live 面 →
    不计入漂移 (缺部署面属 deploy-surface-byte-identity 表面检的 scope, 非本内容漂移门)。
    """
    pairs = enumerate_pairs(skills_src=skills_src, glue_src=glue_src)
    roots = surface_roots if surface_roots is not None else deploy_surface_roots()
    existing_roots = [r for r in roots if r.exists()]
    touched_tails = {src_tail(t) for t in touched_paths}

    touched_pairs = [p for p in pairs if src_tail(str(p.source)) in touched_tails]
    if not touched_pairs:
        return True, (
            "no src/towow/{skills,glue} files in actual write set "
            f"(vacuous pass; {len(touched_paths)} touched file(s))"
        )

    drifted: list[str] = []
    checked = 0
    for pair in touched_pairs:
        expected, err = _expected_live_bytes(pair, work_md_render)
        if err is not None or expected is None:
            if err is not None:
                drifted.append(err)
            continue
        expected_sha = _sha256(expected)
        for root in existing_roots:
            live = root / pair.live_rel
            if not live.exists():
                continue  # 该面未部署 → 非漂移
            checked += 1
            if _sha256(live.read_bytes()) != expected_sha:
                kind = "live≠render(src)" if pair.rendered else "live≠src"
                drifted.append(f"{live}: {kind}")

    if drifted:
        shown = "; ".join(drifted[:_MAX_EVIDENCE_ITEMS])
        more = "" if len(drifted) <= _MAX_EVIDENCE_ITEMS else f" (+{len(drifted) - _MAX_EVIDENCE_ITEMS} more)"
        return False, (
            f"{len(drifted)} src↔live byte-identity drift(s) — 改 src 未 redeploy: {shown}{more}"
        )
    return True, (
        f"{checked} live face(s) byte-identical to src for {len(touched_pairs)} touched deploy pair(s)"
    )


def build_deploy_identity_check(
    actual_write_set: list[dict[str, object]] | None,
    *,
    work_md_render: WorkMdRender | None = None,
    skills_src: Path | None = None,
    glue_src: Path | None = None,
    surface_roots: list[Path] | None = None,
) -> ExecutionBlockingCheck:
    """从 run 的 actual_write_set 产 execution.deploy_byte_identity blocking check。

    actual_write_set: [{entity_type: "file", entity_id: <path>}, ...] (system-derived,
    _derive_run_actual_write_set)。只取 FILE 项的路径喂给 check_deploy_identity。
    """
    paths: list[str] = []
    for entry in actual_write_set or []:
        if str(entry.get("entity_type", "")).lower() != "file":
            continue
        eid = entry.get("entity_id")
        if isinstance(eid, str) and eid:
            paths.append(eid)
    passed, evidence = check_deploy_identity(
        paths,
        work_md_render=work_md_render,
        skills_src=skills_src,
        glue_src=glue_src,
        surface_roots=surface_roots,
    )
    return ExecutionBlockingCheck(
        check_id=_CHECK_ID,
        status="passed" if passed else "failed",
        evidence=execution_evidence(_CHECK_ID, evidence),
    )
