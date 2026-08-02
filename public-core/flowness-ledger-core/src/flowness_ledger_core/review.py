from __future__ import annotations

"""Read-only verdict adapter over an already immutable proposal decision."""

import hashlib
import json
from typing import Any

from .ledger import Ledger, LedgerError


def _hash(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_review_verdict(ledger: Ledger, proposal_id: str) -> dict[str, Any]:
    """Describe one terminal proposal without changing ledger state.

    Pending proposals are deliberately refused: a reviewer cannot obtain a
    positive-looking verdict from an unfinished decision. Rejected proposals
    get an explicit negative verdict rather than disappearing from audit.
    """

    rows, incomplete = ledger._load()
    if incomplete:
        raise LedgerError("review verdict refuses incomplete ledger tail")
    proposals = ledger._index(rows)
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise LedgerError("review verdict proposal does not exist")
    decision = proposal["decision"]
    if decision is None:
        raise LedgerError("review verdict requires terminal decision")
    unsigned = {
        "format": "flowness-ledger-core/review-verdict/v1",
        "proposal_id": proposal_id,
        "proposal_record_hash": proposal["begin"]["record_hash"],
        "decision_record_hash": decision["record_hash"],
        "decision": decision["outcome"],
        "verdict": "accepted_committed" if decision["outcome"] == "accepted" else "rejected_not_committed",
        "proposed_record_hashes": [row["record_hash"] for row in proposal["records"]],
    }
    return {**unsigned, "verdict_hash": _hash(unsigned)}
