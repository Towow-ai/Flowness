"""波1 T-L0-26: PatchEntry.detail_ref 按 patch_type 解析 (M-0.4 §5.1-5.5).

源表 done_criteria: 提交后各 patch 的 detail_ref 按 patch_type 解析 (code→git hash, concept/task→
真 event_id), 非自由文本。spec §5: code_diff/doc_change/config_change→"git:<hash>";
concept_event/task_event/obligation_event→"evt:<id>"(逗号分隔多个)。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import PatchType
from towow.schemas.envelope import PatchEntry


def _patch(patch_type: PatchType, detail_ref: str | None) -> PatchEntry:
    return PatchEntry(patch_id="p-1", patch_type=patch_type, target="t", summary="s", detail_ref=detail_ref)


def test_code_diff_accepts_git_ref() -> None:
    p = _patch(PatchType.CODE_DIFF, "git:abc123")
    assert p.detail_ref == "git:abc123"


def test_concept_event_accepts_evt_refs() -> None:
    p = _patch(PatchType.CONCEPT_EVENT, "evt:concept-created-001,evt:edge-added-002")
    assert p.detail_ref is not None


def test_code_diff_rejects_free_text() -> None:
    with pytest.raises(ValidationError, match="git:"):
        _patch(PatchType.CODE_DIFF, "I changed the loop bound")


def test_concept_event_rejects_git_prefix() -> None:
    # evt-type 用 git: 前缀 → 拒 (patch_type↔ref 形不匹配)
    with pytest.raises(ValidationError, match="evt:"):
        _patch(PatchType.CONCEPT_EVENT, "git:abc")


def test_none_detail_ref_allowed() -> None:
    # detail_ref 可选; None 不触发格式校验
    assert _patch(PatchType.CODE_DIFF, None).detail_ref is None


@pytest.mark.parametrize("pt", [PatchType.DOC_CHANGE, PatchType.CONFIG_CHANGE])
def test_doc_config_use_git(pt: PatchType) -> None:
    assert _patch(pt, "git:def456").detail_ref == "git:def456"
    with pytest.raises(ValidationError):
        _patch(pt, "evt:wrong-form")


def test_obligation_event_uses_evt() -> None:
    pt = PatchType.OBLIGATION_EVENT
    assert _patch(pt, "evt:obl-1").detail_ref == "evt:obl-1"
    with pytest.raises(ValidationError):
        _patch(pt, "free text")
