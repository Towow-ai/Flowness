"""M-3.1 §8 skill packaging + capsule injection fail-closed.

# spec source:
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md
#     §8.1 skill 物理部署 (L724..L761)
#     §8.2 knowledge pack 注册 (L763..L770)  # M-1.2a Patch B
#     §8.3 capsule injection fail-closed (L772..L803)
#     §8.4 knowledge pack drift detection (L805..L838)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=False)

# T-L1-66 (M-1.5 §1.2 / §5 / §6 V-02 物理隔离): reviewer fork 工具白名单 —— 只读集。
# reviewer 的价值来自被注入的差异化 context (V-02 prompt + 风险面 + 方法论 lens), 它的工作是
# "尝试 disprove patch" (Popper), 不是改代码。给 reviewer Edit/Write 会污染评估者 lens
# ("我改一下试试" 的诱惑) + 破坏 review↔fix 分离 (改代码是 M-1.6 fix 的 authority)。
# 这把白名单是显式集合, 被 enforce_reviewer_tool_whitelist 机械 enforce —— 不是靠 fork 文件
# 碰巧写对 (那只是静态现状), 而是任何 reviewer fork 声明白名单外工具 (尤其 Edit/Write) 时
# parse/discover **机械拒绝** (fail-closed)。
REVIEWER_READONLY_TOOL_WHITELIST: frozenset[str] = frozenset({"Read", "Grep", "Glob", "Bash"})


class SkillManifest(BaseModel):
    """Parsed skill manifest (frontmatter + knowledge file registry)."""

    model_config = _STRICT

    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    context: str = ""  # "fork" | "main" (Claude Code skill schema) — used by V-02 role detection
    capsule_scene_types: list[str] = Field(default_factory=list)
    shared_knowledge_required: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    registered_at: str  # ISO-8601


class CapsuleInjectionFailed(Exception):
    """M-3.1 §8.3: capsule injection failed — skill session must not start.

    Carries skill_id + missing_file + recovery_hint per spec §8.3.
    """

    def __init__(self, skill_id: str, missing_file: str, recovery_hint: str) -> None:
        self.skill_id = skill_id
        self.missing_file = missing_file
        self.recovery_hint = recovery_hint
        super().__init__(
            f"capsule injection failed for skill_id={skill_id!r}: "
            f"missing {missing_file!r}. {recovery_hint}",
        )


class SkillKnowledgePathTraversal(Exception):
    """T-LC-07: a SKR knowledge reference resolves outside knowledge_root (fail-closed).

    `shared_knowledge_required` entries are package-author-controlled frontmatter strings
    concatenated straight into a filesystem path. A '..'-bearing owner/file segment (e.g.
    ``'concept-definition/../../../../CLAUDE.md'``) escapes the knowledge root and would pull
    an arbitrary repository file into the capsule context. Raised — never silently swallowed —
    so capsule injection ABORTS rather than reading an out-of-tree file.

    Deliberately NOT a SkillPackagingError subclass: `SkillRegistry.discover` swallows that base
    with ``except SkillPackagingError: continue`` to skip malformed manifests; a traversal must
    never be skipped, it must propagate and fail the session.
    """

    def __init__(self, *, skill_id: str, ref: str, resolved: Path, detail: str) -> None:
        self.skill_id = skill_id
        self.ref = ref
        self.resolved = resolved
        self.detail = detail
        super().__init__(
            f"SKR path traversal blocked for skill_id={skill_id!r} ref={ref!r}: "
            f"{detail} (resolved={resolved})",
        )


class SkillPackagingError(Exception):
    """Generic skill manifest parsing / registration error."""


class SkillToolWhitelistViolation(SkillPackagingError):
    """T-L1-66 (M-1.5 §1.2 V-02): a reviewer fork declared a tool outside the read-only whitelist.

    fail-closed —— reviewer fork 声明 Edit/Write (或任何白名单外工具) 时 parse/discover 拒绝注册,
    物理保证 reviewer session 不能带写工具起跑 (评估者 lens 干净 + review↔fix 分离)。
    """

    def __init__(self, skill_id: str, offending_tools: list[str]) -> None:
        self.skill_id = skill_id
        self.offending_tools = offending_tools
        allowed = ", ".join(sorted(REVIEWER_READONLY_TOOL_WHITELIST))
        super().__init__(
            f"V-02 工具白名单 violation: reviewer fork skill_id={skill_id!r} 声明了白名单外工具 "
            f"{offending_tools!r} (reviewer 不能改代码 — 只读白名单 [{allowed}], 无 Edit/Write)。"
            "reviewer 越权改代码是 M-1.5 §5 #11 失败模式; 改代码是 M-1.6 fix authority。",
        )


def _is_reviewer_fork(manifest: SkillManifest) -> bool:
    """A skill is subject to the V-02 read-only whitelist iff it is a review-scene fork.

    判据: capsule_scene_types 含 "review" 且 context == "fork" —— 即 M-1.5 §6 的 5+1 review fork
    (review-plan-creator / meta-review / method-* / verify-step)。主 review orchestrator skill
    (context: main, tools: []) 不是 fork, 不受此约束 (它本就不声明写工具); fix/execution 等
    非 review 角色更不受约束。这样白名单只机械约束真正该被隔离的 reviewer lens, 不误伤别的 skill。
    """
    return manifest.context == "fork" and "review" in manifest.capsule_scene_types


def enforce_reviewer_tool_whitelist(manifest: SkillManifest) -> None:
    """T-L1-66: 对 reviewer fork 机械 enforce V-02 只读工具白名单。

    若 manifest 是 reviewer fork (见 _is_reviewer_fork) 且其 tools 含白名单外工具 (尤其 Edit/Write),
    raise SkillToolWhitelistViolation。非 reviewer 角色 (fix main / execution / interview 等) 不约束。
    no-op when compliant —— 这是 enforce 机制本身, 不是对现有文件的快照断言 (区别于 T-L1-59 测试)。
    """
    if not _is_reviewer_fork(manifest):
        return
    offending = [t for t in manifest.tools if t not in REVIEWER_READONLY_TOOL_WHITELIST]
    if offending:
        raise SkillToolWhitelistViolation(skill_id=manifest.skill_id, offending_tools=offending)


def parse_skill_md(skill_md_path: Path) -> SkillManifest:
    """Parse SKILL.md frontmatter (per M-3.1 §8.2 + Claude Code skill schema).

    SKILL.md expected format::

        ---
        name: Interview
        description: M-1.1 采访 fork
        capsule_scene_types: [interview]
        shared_knowledge_required:
          - concept-graph-principles.md
        tools: []
        ---

        # SKILL.md prose body...

    Returns SkillManifest with parsed fields. Raises SkillPackagingError on malformed
    frontmatter.
    """
    if not skill_md_path.exists():
        raise SkillPackagingError(f"SKILL.md not found: {skill_md_path}")
    text = skill_md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillPackagingError(f"SKILL.md missing frontmatter: {skill_md_path}")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillPackagingError(f"SKILL.md malformed frontmatter: {skill_md_path}")
    frontmatter_str = parts[1]
    try:
        data = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError as exc:
        raise SkillPackagingError(f"SKILL.md frontmatter YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillPackagingError(f"SKILL.md frontmatter must be a mapping: {skill_md_path}")
    # skill_id derives from parent dir name (.claude/skills/<skill_id>/SKILL.md)
    skill_id = skill_md_path.parent.name
    manifest = SkillManifest(
        skill_id=skill_id,
        name=str(data.get("name", skill_id)),
        description=str(data.get("description", "")),
        context=str(data.get("context", "")),
        capsule_scene_types=list(data.get("capsule_scene_types", []) or []),
        shared_knowledge_required=list(data.get("shared_knowledge_required", []) or []),
        tools=list(data.get("tools", []) or []),
        registered_at=datetime.now(tz=UTC).isoformat(),
    )
    # T-L1-66 V-02: reviewer fork 工具白名单 fail-closed —— 越权声明 (Edit/Write) 在 parse 期即拒,
    # 不可能进 registry / capsule, reviewer session 物理不可能带写工具起跑。
    enforce_reviewer_tool_whitelist(manifest)
    return manifest


class SkillRegistry:
    """In-memory registry of deployed skills (M-3.1 §8.1 deployment).

    Scan `.claude/skills/<skill_id>/SKILL.md` files and register manifests for
    capsule injection lookup.
    """

    def __init__(self, claude_skills_dir: Path) -> None:
        self._root = claude_skills_dir
        self._manifests: dict[str, SkillManifest] = {}

    def discover(self) -> dict[str, SkillManifest]:
        """Scan claude_skills_dir for `<skill_id>/SKILL.md` and parse each."""
        if not self._root.exists():
            return {}
        self._manifests.clear()
        for skill_dir in self._root.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                manifest = parse_skill_md(skill_md)
            except SkillToolWhitelistViolation:
                # T-L1-66 V-02 fail-closed: 越权 reviewer fork 不可静默跳过 —— propagate 让
                # discover/init 整体失败, 而不是悄悄不注册该 fork (静默不注册 = reviewer 仍可能
                # 带越权声明被别处加载)。这正是 enforce 机制与 T-L1-59 静态快照的区别。
                raise
            except SkillPackagingError:
                continue  # skip malformed (E.3 minimal — strict mode in E.5)
            self._manifests[manifest.skill_id] = manifest
        return dict(self._manifests)

    def get(self, skill_id: str) -> SkillManifest | None:
        if not self._manifests:
            self.discover()
        return self._manifests.get(skill_id)


def resolve_knowledge_path(
    skill_id: str,
    filename: str,
    *,
    knowledge_root: Path,
) -> Path:
    """SKR 单一真源路径解析 — shared_knowledge_required 一项 → 真实文件路径。

    跨 skill 引用: "<other-skill>/<file>" → 去那个 skill 的 knowledge/ 找。advisor-consult 等
    真独立 fork 复用 execution scene 的 system-mental-model / execution-casebook —— 单一真源,
    不复制文件。无 "/" 照旧本 skill 目录 (向后兼容: 主 skill 与多数 fork 的 SKR 都是本目录文件名)。

    finding-validation-2-6-skr-crossref-path-noop-1: 此解析逻辑曾被 M-3.4 validation_runner
    _drive_2_6 按旧版(每 skill 独立目录)复制一份, SKR 跨 skill 形态落地后 drift → 驱动器删除
    不存在的路径 no-op → fail-closed 探针失真。提取成公共 helper 让产品与验证驱动器物理共用
    同一段逻辑, 杜绝再次 drift。

    T-LC-07 path-traversal 守卫: filename 是包作者可控 frontmatter 字符串, 直接拼进文件路径。
    owner 段或文件段含 '..' (如 'concept-definition/../../../../CLAUDE.md') 会逃出 knowledge_root,
    把任意仓库文件读进 capsule context。复用 fs_guard 的 resolve()+containment idiom 做 fail-closed
    守卫: 拒任何含 '..' 段 + 断言 resolve 后路径落在 knowledge_root 子树内, 违反则 raise
    SkillKnowledgePathTraversal (绝不静默)。守卫只 resolve 一份临时路径做 containment 判定 ——
    返回值仍是未 resolve 的字面拼接路径, 保持既有 helper 契约 (调用方按字面路径做删除 / 存在性检查)。
    """
    if "/" in filename:
        _other, _fname = filename.split("/", 1)
        candidate = knowledge_root / _other / "knowledge" / _fname
        author_segments: tuple[str, ...] = (_other, _fname)
    else:
        candidate = knowledge_root / skill_id / "knowledge" / filename
        author_segments = (filename,)
    _assert_knowledge_path_contained(
        skill_id=skill_id,
        ref=filename,
        candidate=candidate,
        knowledge_root=knowledge_root,
        author_segments=author_segments,
    )
    return candidate


def _assert_knowledge_path_contained(
    *,
    skill_id: str,
    ref: str,
    candidate: Path,
    knowledge_root: Path,
    author_segments: tuple[str, ...],
) -> None:
    """T-LC-07 fail-closed containment guard for SKR knowledge path resolution.

    Two independent checks (defense in depth — either one alone would catch the documented
    attack, but a symlinked knowledge_root could defeat the segment check while containment
    catches it, and vice-versa for non-normalising filesystems):

    1. reject any author-controlled segment that contains a ``..`` path component;
    2. assert ``candidate.resolve()`` stays under ``knowledge_root.resolve()``.

    Raises SkillKnowledgePathTraversal (never swallowed) on violation.
    """
    for seg in author_segments:
        if ".." in Path(seg).parts:
            raise SkillKnowledgePathTraversal(
                skill_id=skill_id,
                ref=ref,
                resolved=candidate,
                detail=f"reference segment {seg!r} contains a '..' path component",
            )
    root = knowledge_root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise SkillKnowledgePathTraversal(
            skill_id=skill_id,
            ref=ref,
            resolved=resolved,
            detail=f"resolved path escapes knowledge_root {root}",
        )


def capsule_inject_knowledge(
    skill_id: str,
    *,
    registry: SkillRegistry,
    knowledge_root: Path,
) -> list[Path]:
    """M-3.1 §8.3 capsule injection with fail-closed semantics.

    For each `shared_knowledge_required` entry in the skill manifest, verify the
    knowledge file exists at `.claude/skills/<skill_id>/knowledge/<file>`. If any file
    is missing → raise CapsuleInjectionFailed (caller must produce CapsuleAssemblyFailed
    event and ABORT session startup, per spec §8.3 fail-closed).

    Returns list of resolved knowledge file paths if all present.
    """
    manifest = registry.get(skill_id)
    if manifest is None:
        raise CapsuleInjectionFailed(
            skill_id=skill_id,
            missing_file="SKILL.md (manifest not registered)",
            recovery_hint="rerun towow init or check `.claude/skills/<skill_id>/SKILL.md`",
        )
    resolved: list[Path] = []
    for filename in manifest.shared_knowledge_required:
        # SKR 路径解析走单一真源 resolve_knowledge_path (跨 skill "<other>/<file>" 与本目录两形态)。
        knowledge_path = resolve_knowledge_path(skill_id, filename, knowledge_root=knowledge_root)
        if not knowledge_path.exists():
            raise CapsuleInjectionFailed(
                skill_id=skill_id,
                missing_file=filename,
                recovery_hint=(
                    f"missing knowledge file {filename!r} at {knowledge_path}; "
                    "rerun towow init or verify knowledge pack deployment"
                ),
            )
        resolved.append(knowledge_path)
    return resolved


def build_capsule_knowledge_context(
    skill_id: str,
    *,
    registry: SkillRegistry,
    knowledge_root: Path,
) -> str:
    """M-1.2 §9.2 / M-3.1 §8.3 — assemble the ACTUAL knowledge-pack content for a skill's capsule.

    T-L1-22: capsule_inject_knowledge only verifies the files exist (fail-closed) and returns their
    paths — the knowledge was never read into the capsule, so a session started with a path
    reference but no content (空壳引用). This reads each shared_knowledge_required file's full text
    and concatenates it into a single capsule knowledge context block the agent actually sees.

    Reuses capsule_inject_knowledge for the fail-closed existence guarantee (raises
    CapsuleInjectionFailed if any required file is missing), then reads the resolved files' bytes.
    Returns the assembled context string (empty string only when the skill declares no
    shared_knowledge_required).
    """
    resolved = capsule_inject_knowledge(
        skill_id,
        registry=registry,
        knowledge_root=knowledge_root,
    )
    if not resolved:
        return ""
    blocks: list[str] = [
        f"# Capsule Knowledge Pack — skill: {skill_id} (M-1.2 §9.2 injected content)",
    ]
    for path in resolved:
        blocks.append(f"\n## knowledge/{path.name}\n")
        blocks.append(path.read_text(encoding="utf-8"))
    return "\n".join(blocks)


__all__ = [
    "REVIEWER_READONLY_TOOL_WHITELIST",
    "CapsuleInjectionFailed",
    "SkillKnowledgePathTraversal",
    "SkillManifest",
    "SkillPackagingError",
    "SkillRegistry",
    "SkillToolWhitelistViolation",
    "build_capsule_knowledge_context",
    "capsule_inject_knowledge",
    "enforce_reviewer_tool_whitelist",
    "parse_skill_md",
    "resolve_knowledge_path",
]
