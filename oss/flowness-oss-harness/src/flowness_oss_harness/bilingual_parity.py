"""Fail-closed EN/ZH claim-unit parity checks for private staging.

This contract deliberately proves only that both candidate source documents
retain named, bounded units.  It cannot determine translation quality or grant
review/publishing authority.  Two human reviewer slots are represented as
pending so a machine-generated "approved" result cannot masquerade as review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .content_graph_v3 import verify_content_graph_v3_files
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA = SCHEMAS_ROOT / "bilingual-parity-contract.schema.json"
NO_PUBLISH_BOUNDARY = (
    "private staging parity verification only; this result cannot approve a "
    "translation, promote a candidate, publish, schedule, or send content."
)
_LOCALES = {"en", "zh-CN"}
_PRODUCTION_PROMOTION_PHRASES = {
    "en": ("production-ready", "production release", "generally available"),
    "zh-CN": ("生产就绪", "生产发布", "正式商用"),
}


def _safe_file(root: Path, relative: str, code: str) -> Path:
    try:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValidationError(code) from exc
    if path.is_symlink() or not path.is_file():
        raise ValidationError(code)
    return path


def _documents(contract: dict[str, Any], root: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    records = contract["source_documents"]
    by_locale = {record["locale"]: record for record in records}
    if set(by_locale) != _LOCALES or len(by_locale) != 2:
        raise ValidationError("BILINGUAL-PARITY-DOCUMENT-LOCALES-INVALID")
    texts: dict[str, str] = {}
    for locale, record in by_locale.items():
        path = _safe_file(root, record["source_path"], "BILINGUAL-PARITY-DOCUMENT-PATH-INVALID")
        actual_hash = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != record["content_hash"]:
            raise ValidationError(f"BILINGUAL-PARITY-DOCUMENT-HASH-MISMATCH:{locale}")
        texts[locale] = path.read_text(encoding="utf-8")
    return texts, by_locale


def _require_in_both(
    values: dict[str, Any], texts: dict[str, str], code: str
) -> None:
    if set(values) != _LOCALES or any(
        not isinstance(values[locale], str) or not values[locale].strip()
        for locale in _LOCALES
    ):
        raise ValidationError(code)
    for locale in sorted(_LOCALES):
        if values[locale] not in texts[locale]:
            raise ValidationError(f"{code}:{locale}")


def _validate_review(review: dict[str, Any]) -> None:
    # Schema fixes status to unreviewed/pending.  The explicit checks make a
    # caller that bypasses schema validation fail closed too.
    if review["state"] != "unreviewed":
        raise ValidationError("BILINGUAL-PARITY-REVIEW-NOT-PENDING")
    slots = review["reviewer_slots"]
    roles = {slot["reviewer_role"] for slot in slots}
    if roles != {"bilingual_semantics", "claim_boundary"} or len(slots) != 2:
        raise ValidationError("BILINGUAL-PARITY-REVIEWER-SLOTS-INVALID")
    if any(slot["status"] != "pending" or slot["review_id"] is not None for slot in slots):
        raise ValidationError("BILINGUAL-PARITY-REVIEW-NOT-PENDING")


def _validate_graph_binding(
    contract: dict[str, Any], *, root: Path, documents: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    binding = contract["content_graph_binding"]
    graph_path = _safe_file(root, binding["graph_path"], "BILINGUAL-PARITY-GRAPH-PATH-INVALID")
    graph_hash = "sha256:" + hashlib.sha256(graph_path.read_bytes()).hexdigest()
    if graph_hash != binding["content_hash"]:
        raise ValidationError("BILINGUAL-PARITY-GRAPH-HASH-MISMATCH")
    result = verify_content_graph_v3_files(graph_path, root=root)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    candidate = contract["candidate"]
    if any(
        graph["candidate"][field] != candidate[field]
        for field in ("candidate_id", "snapshot_id", "version_id")
    ):
        raise ValidationError("BILINGUAL-PARITY-GRAPH-CANDIDATE-MISMATCH")
    source_path = _safe_file(root, graph["source_graph"]["source_path"], "BILINGUAL-PARITY-GRAPH-SOURCE-INVALID")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    pair = next(
        (item for item in graph["localization_pairs"] if item["pair_id"] == binding["localization_pair_id"]),
        None,
    )
    assets = binding["asset_ids"]
    if pair is None or set(pair["asset_ids"]) != set(assets):
        raise ValidationError("BILINGUAL-PARITY-GRAPH-PAIR-INVALID")
    if pair["status"] != "unreviewed" or pair["review_ids"]:
        raise ValidationError("BILINGUAL-PARITY-GRAPH-PAIR-NOT-PENDING")
    source_assets = {node["node_id"]: node for node in source["nodes"] if node["type"] == "asset"}
    relations = {item["asset_id"]: item for item in graph["asset_relations"]}
    if len(assets) != 2 or set(assets) != set(source_assets).intersection(assets):
        raise ValidationError("BILINGUAL-PARITY-GRAPH-ASSETS-INVALID")
    expected = {
        "en": documents["en"],
        "zh-CN": documents["zh-CN"],
    }
    for locale, document in expected.items():
        candidates = [
            asset_id for asset_id in assets
            if relations.get(asset_id, {}).get("locale") == locale
            and source_assets[asset_id].get("source_path") == document["source_path"]
            and source_assets[asset_id].get("content_hash") == document["content_hash"]
        ]
        if len(candidates) != 1:
            raise ValidationError(f"BILINGUAL-PARITY-GRAPH-ASSET-MISMATCH:{locale}")
    return {
        **result,
        "pair_claim_ids": sorted(
            {
                claim_id
                for asset_id in assets
                for claim_id in relations[asset_id]["scope"]["claim_ids"]
            }
        ),
    }


def validate_bilingual_parity_contract(contract: dict[str, Any], *, root: Path | str) -> dict[str, Any]:
    """Verify preservation of bounded EN/ZH claim units in private staging."""

    validate_payload(contract, SCHEMA, "bilingual parity contract")
    base = Path(root).resolve(strict=True)
    texts, documents = _documents(contract, base)
    graph_result = _validate_graph_binding(contract, root=base, documents=documents)
    _validate_review(contract["parity_review"])

    seen_units: set[str] = set()
    seen_claims: set[str] = set()
    for unit in contract["claim_units"]:
        if unit["claim_unit_id"] in seen_units or unit["claim_id"] in seen_claims:
            raise ValidationError("BILINGUAL-PARITY-CLAIM-UNIT-DUPLICATE")
        seen_units.add(unit["claim_unit_id"])
        seen_claims.add(unit["claim_id"])
        if unit["state_label"] != "local_unsealed_candidate":
            raise ValidationError("BILINGUAL-PARITY-STATE-PROMOTION")
        _require_in_both(unit["claim_text"], texts, "BILINGUAL-PARITY-CLAIM-DROPPED")
        _require_in_both(unit["no_fit"], texts, "BILINGUAL-PARITY-NO-FIT-DROPPED")
        _require_in_both(unit["cta"], texts, "BILINGUAL-PARITY-CTA-DROPPED")
        limitations = unit["limitations"]
        ids = [item["limitation_id"] for item in limitations]
        if len(ids) != len(set(ids)) or "limitation.local-unsealed" not in ids:
            raise ValidationError("BILINGUAL-PARITY-LOCAL-UNSEALED-DROPPED")
        for limitation in limitations:
            _require_in_both(
                limitation["required_text"], texts,
                f"BILINGUAL-PARITY-LIMITATION-DROPPED:{limitation['limitation_id']}",
            )

    if sorted(seen_claims) != graph_result["pair_claim_ids"]:
        raise ValidationError("BILINGUAL-PARITY-GRAPH-CLAIM-SCOPE-MISMATCH")

    for locale, phrases in _PRODUCTION_PROMOTION_PHRASES.items():
        if any(phrase in texts[locale] for phrase in phrases):
            raise ValidationError(f"BILINGUAL-PARITY-PRODUCTION-PROMOTION:{locale}")
    return {
        "schema_version": "bilingual-parity-verification/v1",
        "contract_id": contract["contract_id"],
        "candidate_id": contract["candidate"]["candidate_id"],
        "claim_unit_count": len(seen_units),
        "parity_review_state": "unreviewed",
        "content_graph_localization_pair_id": contract["content_graph_binding"]["localization_pair_id"],
        "content_graph_hash": contract["content_graph_binding"]["content_hash"],
        "content_graph_verification": graph_result["schema_version"],
        "boundary": NO_PUBLISH_BOUNDARY,
    }


def verify_bilingual_parity_contract_file(path: Path | str, *, root: Path | str) -> dict[str, Any]:
    base = Path(root).resolve(strict=True)
    contract_path = _safe_file(base, str(path), "BILINGUAL-PARITY-CONTRACT-PATH-INVALID")
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("BILINGUAL-PARITY-CONTRACT-INVALID") from exc
    if not isinstance(contract, dict):
        raise ValidationError("BILINGUAL-PARITY-CONTRACT-INVALID")
    return validate_bilingual_parity_contract(contract, root=base)
