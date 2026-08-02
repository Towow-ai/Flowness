from __future__ import annotations

"""Verifier for explicitly unsealed, local static mechanism chains."""

import hashlib
from pathlib import Path
from typing import Any

from .registry import ValidationError


def verify_static_chain_candidate(manifest: dict[str, Any], root: Path | str) -> dict[str, Any]:
    if set(manifest) != {"schema_version", "boundary", "chains"} or manifest.get("schema_version") != "unsealed-static-chain-candidate/v1":
        raise ValidationError("STATIC-CHAIN-MANIFEST-INVALID")
    if manifest.get("boundary") != "UNSEALED-LOCAL-SOURCE;RUNTIME-UNAVAILABLE":
        raise ValidationError("STATIC-CHAIN-BOUNDARY-INVALID")
    if not isinstance(manifest["chains"], list) or not manifest["chains"]:
        raise ValidationError("STATIC-CHAIN-MANIFEST-INVALID")
    base = Path(root).resolve(strict=True)
    verified: list[str] = []
    seen: set[str] = set()
    for chain in manifest["chains"]:
        if not isinstance(chain, dict) or set(chain) != {"mechanism_id", "status", "nodes", "unknowns"}:
            raise ValidationError("STATIC-CHAIN-INVALID")
        mechanism_id = chain.get("mechanism_id")
        if not isinstance(mechanism_id, str) or not mechanism_id or mechanism_id in seen:
            raise ValidationError("STATIC-CHAIN-INVALID")
        seen.add(mechanism_id)
        if chain.get("status") not in {"candidate_mapped", "declared_only"} or not isinstance(chain.get("unknowns"), list):
            raise ValidationError("STATIC-CHAIN-INVALID")
        if chain["status"] == "candidate_mapped" and not chain.get("nodes"):
            raise ValidationError("STATIC-CHAIN-MISSING-NODES")
        for node in chain.get("nodes", []):
            if not isinstance(node, dict) or set(node) != {"role", "path", "start_line", "end_line", "excerpt_sha256"}:
                raise ValidationError("STATIC-CHAIN-NODE-INVALID")
            if node["role"] not in {"definition", "caller", "consumer", "test", "failure", "recovery"}:
                raise ValidationError("STATIC-CHAIN-NODE-INVALID")
            if not isinstance(node["start_line"], int) or not isinstance(node["end_line"], int) or node["start_line"] < 1 or node["end_line"] < node["start_line"]:
                raise ValidationError("STATIC-CHAIN-NODE-INVALID")
            path = (base / node["path"]).resolve(strict=True)
            try:
                path.relative_to(base)
            except ValueError as exc:
                raise ValidationError("STATIC-CHAIN-PATH-ESCAPES-ROOT") from exc
            if path.is_symlink() or not path.is_file():
                raise ValidationError("STATIC-CHAIN-PATH-UNSAFE")
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            excerpt = "".join(lines[node["start_line"] - 1:node["end_line"]]).encode()
            actual = "sha256:" + hashlib.sha256(excerpt).hexdigest()
            if actual != node["excerpt_sha256"]:
                raise ValidationError(f"STATIC-CHAIN-EXCERPT-MISMATCH:{mechanism_id}:{node['role']}")
        verified.append(mechanism_id)
    return {"schema_version": "unsealed-static-chain-verification/v1", "verified_mechanism_ids": sorted(verified), "ceiling": "candidate_mapped_only"}
