"""Deploy manifest — src ↔ live 部署对的单一真源 (T-RMD-S4-BYTE-IDENTITY).

# 根治 f-sub-byte-identity-gate-missing
#
# 病灶 (substrate 审计 L4-F4): 改 src(glue 源)不 redeploy → src↔live 字节漂移永远进得了
# "已完成"。无 deploy_manifest 单一真源、无 src↔deployed sha256 比对、commit gate 不挡。
# 像 L4-F1 的 MCP matcher 那种 live↔src 漂移就这么活下来 (autopilot 自更新自部署时切断自修
# 复链都查不出)。本模块 + deploy_identity_check.py 把 byte-identity 门接进 work complete 的
# run_all_execution_blocking_checks (commit gate 落地相位)。
#
# 单一真源契约 (deploy 与 gate 共用):
#   部署映射规则镜像 shell.distribution.deploy_glue / shell.layout.deploy_skills (RUN-098 两层
#   切分铁律): src/towow/skills/<rel> → .claude/skills/<rel> (逐字节); src/towow/glue/<rel> →
#   .claude/<rel> (逐字节, 但 CLAUDE.md 不镜像 — 它是项目根灵魂层种子; commands/work.md 是模板,
#   live = render(src))。本模块**不 import shell** (l0 不依赖 shell — 避免分层倒置): 路径解析用
#   importlib.resources 自取 (与 deploy 同策略), render_fn 由 CLI 组合点注入 (见 deploy_identity_
#   check.build_deploy_identity_check 的 work_md_render 参数)。
#
# scope (per-task-vs-production-real-boundary@v1): 本门检的是 dogfood 仓 (harness 自我开发, src
# 与 .claude 同仓共编) 的 src↔live 漂移 —— 这正是 finding 指的 "autopilot 自更新自部署" 场景。
# 装到扁平客户端的内核 src 是只读不可编辑的 (客户端只动自己的 .towow), 故客户端 touched 永不命中
# src/towow/{skills,glue} → 门 vacuous pass (诚实: 不越权声称生产已发生)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath


# 项目根 CLAUDE.md / AGENTS.md 由 marker-aware seed/sync 管理；agents/ 与 codex/
# 分别映射到 `.agents/` / `.codex/`，不是本门检查的 `.claude/` 部署面。
def _glue_is_outside_claude_surface(rel_posix: str) -> bool:
    return rel_posix in {"CLAUDE.md", "AGENTS.template.md"} or rel_posix.startswith(
        ("agents/", "codex/")
    )


# 渲染类胶水 (live = render(src), 非逐字节): commands/work.md 是带 {{占位符}} 的模板。
_RENDERED_GLUE: frozenset[str] = frozenset({"commands/work.md"})
_SRC_MARKER = "src/towow/"


@dataclass(frozen=True)
class DeployPair:
    """一对 src↔live 部署关系。

    source: src 侧文件绝对路径 (真源)。
    live_rel: 相对某个 `.claude` 根的目标相对路径 (如 skills/foo/SKILL.md, commands/work.md)。
    rendered: True → live 内容 = render_fn(source 文本) (work.md); False → live = source 原字节。
    """

    source: Path
    live_rel: PurePosixPath
    rendered: bool


def skills_source_root() -> Path:
    """src/towow/skills 真源 (与 deploy_skills 同策略: importlib.resources 优先, editable fallback)。"""
    try:
        return Path(str(files("towow") / "skills"))
    except Exception:  # pragma: no cover - editable fallback
        return Path(__file__).resolve().parents[2] / "skills"


def glue_source_root() -> Path:
    """src/towow/glue 真源 (镜像 shell.distribution.glue_source_root, 但不 import shell)。"""
    try:
        return Path(str(files("towow") / "glue"))
    except Exception:  # pragma: no cover - editable fallback
        return Path(__file__).resolve().parents[2] / "glue"


def harness_root() -> Path:
    """harness/ 子目录 (= src/towow 的上两级)。dogfood: <repo-root>/harness。"""
    try:
        return Path(str(files("towow"))).parents[1]
    except Exception:  # pragma: no cover - editable fallback
        return Path(__file__).resolve().parents[3]


def deploy_surface_roots() -> list[Path]:
    """本仓可能存在的 `.claude` 部署面 (deploy-surface-byte-identity@v2 双面)。

    面1 <repo-root>/.claude         —— 主 session(/work 等)装人格 + 三块胶水落地面
    面2 <repo-root>/harness/.claude —— execution/fix 等 fork 运行时真加载的面 (skills/settings)
    只返回路径; 调用方按"该面物理存在"再比对 (面缺失 = 该面未部署, 非漂移)。
    """
    hroot = harness_root()
    return [hroot.parent / ".claude", hroot / ".claude"]


def _iter_source_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store")


def enumerate_pairs(
    *,
    skills_src: Path | None = None,
    glue_src: Path | None = None,
) -> list[DeployPair]:
    """枚举所有 src↔live 部署对 (走源树, 与 deploy 同款相对路径镜像 = 单一真源)。

    skills_src / glue_src 可注入 (测试用); 缺省走 importlib 真源。
    """
    sroot = skills_src if skills_src is not None else skills_source_root()
    groot = glue_src if glue_src is not None else glue_source_root()
    pairs: list[DeployPair] = []
    for f in _iter_source_files(sroot):
        rel = PurePosixPath("skills") / f.relative_to(sroot).as_posix()
        pairs.append(DeployPair(source=f, live_rel=rel, rendered=False))
    for f in _iter_source_files(groot):
        rel_posix = f.relative_to(groot).as_posix()
        if _glue_is_outside_claude_surface(rel_posix):
            continue
        pairs.append(
            DeployPair(
                source=f,
                live_rel=PurePosixPath(rel_posix),
                rendered=rel_posix in _RENDERED_GLUE,
            ),
        )
    return pairs


def src_tail(path_str: str) -> str:
    """把任意路径归一到 `src/towow/...` 尾段, 用于跨 (绝对 / git-relative / harness 前缀) 匹配。

    actual_write_set 的 entity_id 是 git-root-relative (如 harness/src/towow/skills/foo/SKILL.md);
    DeployPair.source 是绝对路径。两者都含 `src/towow/` 段 → 取该段起的尾巴比对即可稳健匹配。
    不含该 marker → 原样归一 (不会匹配到任何部署源)。
    """
    posix = PurePosixPath(path_str.replace(os.sep, "/")).as_posix()
    idx = posix.rfind(_SRC_MARKER)
    return posix[idx:] if idx >= 0 else posix
