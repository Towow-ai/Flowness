"""M-0.3 Capsule assembler — 9-stage pipeline + scene_type templates.

E.1.5 (RUN-005 fix bucket A): stages 1-8 真实现 → real CapsuleCompiled +
ResolverDecisionMade + ObligationScopeJudged + ObligationActivated events
replacing evt-stub-capsule-* sentinels.
"""

from towow.l0.capsule.pipeline import (
    CapsulePipelineResult,
    PipelineError,
    assemble_capsule,
)

__all__ = [
    "CapsulePipelineResult",
    "PipelineError",
    "assemble_capsule",
]
