"""M-0.2 Projection — derive current state from event log via reducers + atomic store."""

from towow.l0.projection.projection import (
    ConsistencyMode,
    ProjectionConsistencyError,
    ProjectionStore,
    build_obligation_lifecycle,
)

__all__ = [
    "ConsistencyMode",
    "ProjectionConsistencyError",
    "ProjectionStore",
    "build_obligation_lifecycle",
]
