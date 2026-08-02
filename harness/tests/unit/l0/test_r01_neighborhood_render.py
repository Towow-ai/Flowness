"""R01 Part A — concept-neighborhood capsule renderer + per-session file write (unit).

# contract: 01-reconciliation/R01-BRAIN-DELIVERY-DESIGN-2026-06-17.md §2 Job 2 投递层 (入口层)
#
# 证 (机制层, 非"agent 真用" — 那归主 session 真 session 验):
#   - render_neighborhood_capsule_text 从 CapsulePipelineResult 抽 KNOWN_FACTS + FILES_TO_READ
#     渲成 markdown, 排除非邻域 section (TASK/MUST_RETURN 等), 空 content section 跳过。
#   - 全空 → 返回 "" (no-op 信号给 caller)。
#   - render_audit_capsule_text 重构后逐字节不变 (审计输出快照保护): 与提取前的原始实现等价。
#   - _write_session_neighborhood_file: 非空 → 原子写 per-session 文件且内容 == 渲染文本;
#     no-op → 不建文件、返回 None。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.l0.capsule.pipeline import CapsulePipelineResult
from towow.l0.capsule.render import render_neighborhood_capsule_text
from towow.l1.audit_fork import render_audit_capsule_text
from towow.schemas.capsule import CapsuleResult, CapsuleSectionContent
from towow.schemas.enums import CapsuleSection, SceneType

if TYPE_CHECKING:
    from pathlib import Path


# ─── helpers ────────────────────────────────────────────────────────────────


def _section(section: CapsuleSection, content: str) -> CapsuleSectionContent:
    return CapsuleSectionContent(section=section, content=content, estimated_tokens=max(1, len(content)))


def _capsule(*sections: CapsuleSectionContent) -> CapsuleResult:
    return CapsuleResult(
        input_hash="sha256:r01test",
        scene_type=SceneType.EXECUTION,
        target_fork_id="fork-r01",
        sections=list(sections) if sections else [_section(CapsuleSection.TASK, "t")],
        estimated_tokens=sum(s.estimated_tokens for s in sections) or 1,
        max_tokens=80000,
    )


def _pipeline_result(capsule: CapsuleResult) -> CapsulePipelineResult:
    return CapsulePipelineResult(
        capsule=capsule,
        capsule_compiled_event_id="evt-cap-r01",
        resolver_decision_event_id="evt-resolver-r01",
        scope_judged_event_ids=[],
        activated_event_ids=[],
        final_active_set=[],
    )


# original render impl (pre-refactor) — 快照基线, 证提取后逐字节等价。
def _legacy_audit_render(capsule: CapsuleResult) -> str:
    return "\n\n".join(f"### {sec.section.value}\n{sec.content}" for sec in capsule.sections)


# ─── renderer: 非空抽取 ────────────────────────────────────────────────────────


def test_render_neighborhood_extracts_known_facts_and_files_excludes_other_sections() -> None:
    cap = _capsule(
        _section(CapsuleSection.RED_LINE_OBLIGATIONS, "红线 X 不许碰"),
        _section(CapsuleSection.TASK, "做 task T"),
        _section(CapsuleSection.MUST_RETURN, "返回 envelope"),
        _section(CapsuleSection.KNOWN_FACTS, "- [c-foo] Foo — 一个概念定义"),
        _section(CapsuleSection.FILES_TO_READ, "- src/towow/foo.py"),
    )
    text = render_neighborhood_capsule_text(_pipeline_result(cap))

    # 概念邻域 section 进
    assert "### KNOWN_FACTS" in text
    assert "[c-foo] Foo — 一个概念定义" in text
    assert "### FILES_TO_READ" in text
    assert "src/towow/foo.py" in text
    # 非邻域 section 不进 (方法论/红线/task 不是概念上下文投递的内容)
    assert "RED_LINE_OBLIGATIONS" not in text
    assert "### TASK" not in text
    assert "MUST_RETURN" not in text


def test_render_neighborhood_keeps_capsule_section_order() -> None:
    """KNOWN_FACTS 在 capsule 优先级里排在 FILES_TO_READ 前 → 渲染保留该顺序。"""
    cap = _capsule(
        _section(CapsuleSection.FILES_TO_READ, "- a.py"),  # 故意先放 (capsule 实际不会, 但渲染按列表序)
        _section(CapsuleSection.KNOWN_FACTS, "- [c] K"),
    )
    text = render_neighborhood_capsule_text(_pipeline_result(cap))
    assert text.index("### FILES_TO_READ") < text.index("### KNOWN_FACTS")


def test_render_neighborhood_skips_empty_section_content() -> None:
    """KNOWN_FACTS 空 content (任务无概念锚) 跳过, 只剩 FILES_TO_READ。"""
    cap = _capsule(
        _section(CapsuleSection.KNOWN_FACTS, "   "),  # whitespace-only → 跳过
        _section(CapsuleSection.FILES_TO_READ, "- only.py"),
    )
    text = render_neighborhood_capsule_text(_pipeline_result(cap))
    assert "KNOWN_FACTS" not in text
    assert text == "### FILES_TO_READ\n- only.py"


# ─── renderer: no-op (全空) ────────────────────────────────────────────────────


def test_render_neighborhood_all_empty_returns_empty_string() -> None:
    """无任何邻域 section (只有 TASK/RED_LINE) → 返回 "" (caller 据此 no-op)。"""
    cap = _capsule(
        _section(CapsuleSection.RED_LINE_OBLIGATIONS, "红线"),
        _section(CapsuleSection.TASK, "做 task"),
    )
    assert render_neighborhood_capsule_text(_pipeline_result(cap)) == ""


def test_render_neighborhood_known_facts_empty_returns_empty_string() -> None:
    """KNOWN_FACTS 存在但 content 空白, 无 FILES_TO_READ → 返回 "" (任务无概念锚的常见态)。"""
    cap = _capsule(
        _section(CapsuleSection.TASK, "做 task"),
        _section(CapsuleSection.KNOWN_FACTS, ""),
    )
    assert render_neighborhood_capsule_text(_pipeline_result(cap)) == ""


# ─── audit 渲染逐字节不变 (重构回归护栏) ─────────────────────────────────────────


def test_audit_render_byte_identical_to_legacy_with_all_sections() -> None:
    cap = _capsule(
        _section(CapsuleSection.RED_LINE_OBLIGATIONS, "红线 A"),
        _section(CapsuleSection.TASK, "task body\n多行"),
        _section(CapsuleSection.MUST_RETURN, "AuditVerdict"),
        _section(CapsuleSection.KNOWN_FACTS, "- [c-1] def"),
        _section(CapsuleSection.FILES_TO_READ, "- f.py"),
    )
    assert render_audit_capsule_text(cap) == _legacy_audit_render(cap)


def test_audit_render_byte_identical_includes_empty_content_sections() -> None:
    """关键差异点: audit 渲全部 section (含空 content), 邻域渲染才 skip_empty。"""
    cap = _capsule(
        _section(CapsuleSection.TASK, "t"),
        _section(CapsuleSection.KNOWN_FACTS, ""),  # 空 — audit 仍渲 "### KNOWN_FACTS\n"
    )
    rendered = render_audit_capsule_text(cap)
    assert rendered == _legacy_audit_render(cap)
    assert "### KNOWN_FACTS\n" in rendered  # audit 保留空 section (与邻域渲染相反)


# ─── per-session 文件写 ────────────────────────────────────────────────────────


def test_write_session_neighborhood_file_writes_rendered_text(tmp_path: Path) -> None:
    from towow.cli.main import _write_session_neighborhood_file

    towow = tmp_path / ".towow"
    towow.mkdir()
    cap = _capsule(
        _section(CapsuleSection.KNOWN_FACTS, "- [c-bar] Bar — 概念 bar 的定义"),
        _section(CapsuleSection.FILES_TO_READ, "- src/bar.py"),
    )
    result = _pipeline_result(cap)

    path = _write_session_neighborhood_file(towow, "sess-work-abc123", result)

    assert path is not None
    assert path == towow / "capsules" / "sessions" / "sess-work-abc123-neighborhood.md"
    assert path.exists()
    written = path.read_text(encoding="utf-8")
    assert written == render_neighborhood_capsule_text(result)
    assert "[c-bar] Bar — 概念 bar 的定义" in written
    # 原子写不留 tmp
    assert not (towow / "capsules" / "sessions" / ".sess-work-abc123-neighborhood.md.tmp").exists()


def test_write_session_neighborhood_file_noop_when_empty(tmp_path: Path) -> None:
    from towow.cli.main import _write_session_neighborhood_file

    towow = tmp_path / ".towow"
    towow.mkdir()
    cap = _capsule(_section(CapsuleSection.TASK, "做 task"))  # 无邻域 section
    result = _pipeline_result(cap)

    path = _write_session_neighborhood_file(towow, "sess-work-empty", result)

    assert path is None
    # no-op: 不建文件、连 sessions 目录都不必残留无意义文件
    assert not (towow / "capsules" / "sessions" / "sess-work-empty-neighborhood.md").exists()


def test_write_session_neighborhood_file_per_session_no_crosstalk(tmp_path: Path) -> None:
    """两会话各写各的 per-session 文件 → 内容互不串味 (并发隔离的物理基础)。"""
    from towow.cli.main import _write_session_neighborhood_file

    towow = tmp_path / ".towow"
    towow.mkdir()
    cap_a = _capsule(_section(CapsuleSection.KNOWN_FACTS, "- [c-a] A 概念"))
    cap_b = _capsule(_section(CapsuleSection.KNOWN_FACTS, "- [c-b] B 概念"))

    pa = _write_session_neighborhood_file(towow, "sess-work-aaa", _pipeline_result(cap_a))
    pb = _write_session_neighborhood_file(towow, "sess-work-bbb", _pipeline_result(cap_b))

    assert pa is not None
    assert pb is not None
    assert pa != pb
    assert "[c-a] A 概念" in pa.read_text(encoding="utf-8")
    assert "[c-b] B 概念" in pb.read_text(encoding="utf-8")
    assert "c-b" not in pa.read_text(encoding="utf-8")
    assert "c-a" not in pb.read_text(encoding="utf-8")
