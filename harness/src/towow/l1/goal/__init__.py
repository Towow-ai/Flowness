"""RUN-003 P-12 GOAL mode L1 modules.

# spec source:
#   02-meta-and-requirements/SYSTEM-REQUIREMENTS.md §四 P-12
#   harness/docs/dogfood-runs/DOGFOOD-RUN-003-launch/concept-list.md
"""

from towow.l1.goal.condition_builder import (
    GOAL_CONDITION_MAX_CHARS,
    build_condition,
    enforce_designer_style,
)

__all__ = [
    "GOAL_CONDITION_MAX_CHARS",
    "build_condition",
    "enforce_designer_style",
]
