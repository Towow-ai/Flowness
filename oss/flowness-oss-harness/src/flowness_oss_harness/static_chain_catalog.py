"""Aggregate unsealed static-chain manifests without promoting their claims.

This is deliberately an index for the mechanism excavation program, not a
second verifier and not a readiness signal.  It makes the negative surface
machine-readable: every seed mechanism is either bound to an exact static
chain or emitted as an explicit Unknown, while every bound chain retains the
unsealed/runtime-unavailable ceiling from :mod:`static_chain`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .registry import ValidationError, atomic_write_json
from .static_chain import verify_static_chain_candidate


BOUNDARY = "UNSEALED-LOCAL-SOURCE;RUNTIME-UNAVAILABLE"


def _file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError("STATIC-CHAIN-CATALOG-MANIFEST-UNSAFE")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_mechanisms(seed: dict[str, Any]) -> list[dict[str, Any]]:
    if (
        seed.get("schema_version") != "mechanism-registry-seed/v0"
        or not isinstance(seed.get("mechanisms"), list)
    ):
        raise ValidationError("STATIC-CHAIN-CATALOG-SEED-INVALID")
    mechanisms = seed["mechanisms"]
    ids = [item.get("mechanism_id") for item in mechanisms if isinstance(item, dict)]
    if (
        len(ids) != len(mechanisms)
        or any(not isinstance(item_id, str) or not item_id for item_id in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValidationError("STATIC-CHAIN-CATALOG-SEED-INVALID")
    return mechanisms


def build_static_chain_catalog(
    seed_path: Path,
    manifest_paths: list[Path],
    source_root: Path,
) -> dict[str, Any]:
    """Return a complete seed-to-static-chain map with all absences explicit."""

    if not manifest_paths:
        raise ValidationError("STATIC-CHAIN-CATALOG-MANIFESTS-REQUIRED")
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("STATIC-CHAIN-CATALOG-SEED-INVALID") from exc
    mechanisms = _seed_mechanisms(seed)
    known_ids = {item["mechanism_id"] for item in mechanisms}
    seen_paths: set[Path] = set()
    chains_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    manifest_refs: list[dict[str, Any]] = []
    for raw_path in manifest_paths:
        path = raw_path.resolve()
        if path in seen_paths:
            raise ValidationError("STATIC-CHAIN-CATALOG-DUPLICATE-MANIFEST")
        seen_paths.add(path)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError("STATIC-CHAIN-CATALOG-MANIFEST-INVALID") from exc
        verification = verify_static_chain_candidate(manifest, source_root)
        reference = {
            "path": str(path),
            "sha256": _file_hash(path),
            "verified_mechanism_ids": verification["verified_mechanism_ids"],
        }
        manifest_refs.append(reference)
        for chain in manifest["chains"]:
            mechanism_id = chain["mechanism_id"]
            if mechanism_id not in known_ids:
                raise ValidationError(
                    f"STATIC-CHAIN-CATALOG-UNKNOWN-MECHANISM:{mechanism_id}"
                )
            if mechanism_id in chains_by_id:
                raise ValidationError(
                    f"STATIC-CHAIN-CATALOG-DUPLICATE-MECHANISM:{mechanism_id}"
                )
            chains_by_id[mechanism_id] = (chain, reference)

    records: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for seed_mechanism in mechanisms:
        mechanism_id = seed_mechanism["mechanism_id"]
        chain_pair = chains_by_id.get(mechanism_id)
        seed_questions = list(seed_mechanism.get("unresolved_questions", []))
        if chain_pair is None:
            state = "unmapped"
            chain_ref = None
            chain_questions = [
                "No verified unsealed static chain currently binds this seed "
                "mechanism to source excerpts."
            ]
        else:
            chain, reference = chain_pair
            state = chain["status"]
            chain_ref = {"path": reference["path"], "sha256": reference["sha256"]}
            chain_questions = list(chain["unknowns"])
        questions = list(dict.fromkeys(seed_questions + chain_questions))
        records.append(
            {
                "mechanism_id": mechanism_id,
                "public_name": seed_mechanism["public_name"],
                "static_state": state,
                "chain_manifest": chain_ref,
                "unresolved_questions": questions,
                "ceiling": "candidate_mapped_only",
            }
        )
        unknowns.append(
            {
                "unknown_id": f"UNKNOWN-STATIC-CHAIN-{mechanism_id}",
                "mechanism_id": mechanism_id,
                "blocking": True,
                "question": (
                    "Can this mechanism be promoted beyond local static mapping, "
                    "and if not, which exact source or runtime link is absent?"
                ),
                "next_check": "sealed source snapshot plus runtime Evidence Seal",
                "reasons": questions,
            }
        )
    return {
        "schema_version": "static-chain-catalog/v1",
        "seed": {"path": str(seed_path.resolve()), "sha256": _file_hash(seed_path)},
        "boundary": BOUNDARY,
        "ceiling": "candidate_mapped_only",
        "manifests": sorted(manifest_refs, key=lambda item: item["path"]),
        "mechanisms": records,
        "unknowns": unknowns,
        "counts": {
            "seed_mechanisms": len(records),
            "candidate_mapped": sum(row["static_state"] == "candidate_mapped" for row in records),
            "declared_only": sum(row["static_state"] == "declared_only" for row in records),
            "unmapped": sum(row["static_state"] == "unmapped" for row in records),
            "blocking_unknowns": len(unknowns),
        },
    }


def write_static_chain_catalog(
    seed_path: Path, manifest_paths: list[Path], source_root: Path, output: Path
) -> dict[str, Any]:
    catalog = build_static_chain_catalog(seed_path, manifest_paths, source_root)
    atomic_write_json(output, catalog)
    return catalog
