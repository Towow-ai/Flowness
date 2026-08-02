"""RUN-038 L0增强 M-0.6 Cap3: ObligationEvolved novelty 强制 (§4.1.2).

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §4.1.2
  payload.novelty:
    novelty_type: enum [new_constraint | new_evidence | new_resolution | scope_change | nature_override]
    no_new_information: bool   # 必须为 false (commit gate 拒绝 true)
    new_constraint / new_evidence / new_resolution / scope_change / nature_override  # 至少一个非空

要证真: 缺 novelty 的 evolve 被拒 (novelty 必填); no_new_information=true 被拒;
所有 new_* 字段全空被拒 (至少一个非空). 负对照确认。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import ObligationSeverity, PatchNoveltyType
from towow.schemas.payloads.obligation import (
    ObligationEvolvedBeforeAfter,
    ObligationEvolvedNovelty,
    ObligationEvolvedPayload,
)


def _before_after() -> ObligationEvolvedBeforeAfter:
    return ObligationEvolvedBeforeAfter(
        scope_rule="applies to all patches",
        severity=ObligationSeverity.RED_LINE,
        definition="every patch must trace to a goal",
    )


def _payload(**novelty_overrides: object) -> ObligationEvolvedPayload:
    novelty_kwargs: dict[str, object] = {
        "novelty_type": PatchNoveltyType.NEW_CONSTRAINT,
        "no_new_information": False,
        "new_constraint": "now also forbids X",
    }
    novelty_kwargs.update(novelty_overrides)
    return ObligationEvolvedPayload(
        obligation_id="obl-new-1",
        before_state=_before_after(),
        after_state=_before_after(),
        novelty=ObligationEvolvedNovelty(**novelty_kwargs),  # type: ignore[arg-type]
    )


def test_valid_novelty_accepted() -> None:
    p = _payload()
    assert p.novelty.novelty_type is PatchNoveltyType.NEW_CONSTRAINT
    assert p.novelty.no_new_information is False
    assert p.novelty.new_constraint == "now also forbids X"


def test_novelty_is_required_on_evolve() -> None:
    """payload 缺 novelty → 拒 (novelty 必填, 非可选)。"""
    with pytest.raises(ValidationError):
        ObligationEvolvedPayload(
            obligation_id="obl-new-1",
            before_state=_before_after(),
            after_state=_before_after(),
        )  # type: ignore[call-arg]


def test_no_new_information_true_rejected() -> None:
    """no_new_information=true 被拒 (防 A→A'→A 振荡)。"""
    with pytest.raises(ValidationError):
        _payload(no_new_information=True)


def test_all_new_fields_empty_rejected() -> None:
    """所有 new_* 字段全空 → 拒 (至少一个非空)。"""
    with pytest.raises(ValidationError):
        ObligationEvolvedNovelty(
            novelty_type=PatchNoveltyType.NEW_CONSTRAINT,
            no_new_information=False,
        )


def test_each_novelty_type_with_its_field() -> None:
    """5 个 novelty_type 各配对应非空 new_* 字段均合法。"""
    cases: list[tuple[PatchNoveltyType, dict[str, object]]] = [
        (PatchNoveltyType.NEW_CONSTRAINT, {"new_constraint": "c"}),
        (PatchNoveltyType.NEW_EVIDENCE, {"new_evidence": "e"}),
        (PatchNoveltyType.NEW_RESOLUTION, {"new_resolution": "r"}),
        (PatchNoveltyType.SCOPE_CHANGE, {"scope_change": {"from": "a", "to": "b"}}),
        (PatchNoveltyType.NATURE_OVERRIDE, {"nature_override": {"reason": "owner"}}),
    ]
    for nt, field in cases:
        n = ObligationEvolvedNovelty(novelty_type=nt, no_new_information=False, **field)  # type: ignore[arg-type]
        assert n.novelty_type is nt


def test_blank_string_new_field_does_not_satisfy() -> None:
    """纯空白 new_* 字符串不算"非空"(防 vacuous 满足)。"""
    with pytest.raises(ValidationError):
        ObligationEvolvedNovelty(
            novelty_type=PatchNoveltyType.NEW_CONSTRAINT,
            no_new_information=False,
            new_constraint="   ",
        )
