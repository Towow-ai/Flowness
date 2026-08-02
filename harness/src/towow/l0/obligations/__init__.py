"""M-0.6 Obligation System — bootstrap + lifecycle + conservative resolver."""

from towow.l0.obligations.bootstrap import (
    BOOTSTRAP_OBLIGATIONS_COUNT,
    ProtocolInvariantImmutabilityError,
    bootstrap_protocol_invariants,
    check_retire_allowed,
)
from towow.l0.obligations.resolver import conservative_fallback_verdict
from towow.l0.obligations.storage import (
    init_obligation_system_storage,
    load_bootstrap_obligation_entries,
    obligation_system_dir,
)

__all__ = [
    "BOOTSTRAP_OBLIGATIONS_COUNT",
    "ProtocolInvariantImmutabilityError",
    "bootstrap_protocol_invariants",
    "check_retire_allowed",
    "conservative_fallback_verdict",
    "init_obligation_system_storage",
    "load_bootstrap_obligation_entries",
    "obligation_system_dir",
]
