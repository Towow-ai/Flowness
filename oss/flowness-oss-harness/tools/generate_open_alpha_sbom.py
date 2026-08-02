#!/usr/bin/env python3
"""Generate the deterministic CycloneDX candidate from the exact RC0 pins."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path


LICENSES = {
    "annotated-types": "MIT",
    "attrs": "MIT",
    "cffi": "MIT-0",
    "click": "BSD-3-Clause",
    "colorama": "BSD-3-Clause",
    "cryptography": "Apache-2.0 OR BSD-3-Clause",
    "iniconfig": "MIT",
    "jsonschema": "MIT",
    "jsonschema-specifications": "MIT",
    "packaging": "Apache-2.0 OR BSD-2-Clause",
    "pluggy": "MIT",
    "pycparser": "BSD-3-Clause",
    "pydantic": "MIT",
    "pydantic-core": "MIT",
    "pygments": "BSD-2-Clause",
    "pytest": "MIT",
    "pyyaml": "MIT",
    "referencing": "MIT",
    "rpds-py": "MIT",
    "setuptools": "MIT",
    "typing-extensions": "PSF-2.0",
    "typing-inspection": "MIT",
}

DEPENDENCIES = {
    "flowness-harness": ["click", "pydantic", "pyyaml"],
    "flowness-oss-harness": ["cryptography", "jsonschema"],
    "flowness-ledger-core": [],
    "pydantic": ["annotated-types", "pydantic-core", "typing-extensions", "typing-inspection"],
    "pydantic-core": ["typing-extensions"],
    "typing-inspection": ["typing-extensions"],
    "click": ["colorama"],
    "cryptography": ["cffi"],
    "cffi": ["pycparser"],
    "jsonschema": ["attrs", "jsonschema-specifications", "referencing", "rpds-py"],
    "jsonschema-specifications": ["referencing"],
    "referencing": ["attrs", "rpds-py", "typing-extensions"],
    "pytest": ["colorama", "iniconfig", "packaging", "pluggy", "pygments"],
}

PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ;]+)")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build(root: Path) -> dict[str, object]:
    lock = root / "harness/open-alpha-requirements.lock"
    pins: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line)
        if match:
            pins[_normal(match.group(1))] = match.group(2)
    if set(pins) != set(LICENSES):
        missing = sorted(set(LICENSES) - set(pins))
        extra = sorted(set(pins) - set(LICENSES))
        raise SystemExit(f"pin/license mismatch missing={missing} extra={extra}")

    refs = {name: f"pkg:pypi/{name}@{version}" for name, version in pins.items()}
    first_party = {
        "flowness-harness": "pkg:pypi/flowness-harness@1.0.0a1",
        "flowness-oss-harness": "pkg:pypi/flowness-oss-harness@1.0.0a1",
        "flowness-ledger-core": "pkg:pypi/flowness-ledger-core@1.0.0a1",
    }
    seed = "|".join(
        _digest(root / path)
        for path in (
            "harness/open-alpha-requirements.lock",
            "harness/uv.lock",
            "oss/flowness-oss-harness/uv.lock",
            "harness/build-system-requirements.lock",
        )
    )
    components = []
    for name in sorted(pins):
        scope = "optional" if name in {"pytest", "iniconfig", "packaging", "pluggy", "pygments"} else "required"
        components.append(
            {
                "type": "library",
                "bom-ref": refs[name],
                "name": name,
                "version": pins[name],
                "purl": refs[name],
                "scope": scope,
                "licenses": [{"expression": LICENSES[name]}],
                "properties": [
                    {"name": "flowness:resolution", "value": "exact-unified-pin"},
                    {"name": "flowness:license-evidence", "value": "installed-metadata-observation"},
                ],
            }
        )
    dependency_rows = [
        {
            "ref": "flowness-open-alpha@1.0.0a1",
            "dependsOn": sorted([*first_party.values(), refs["setuptools"], refs["pytest"]]),
        }
    ]
    for name, ref in sorted(first_party.items()):
        dependency_rows.append(
            {"ref": ref, "dependsOn": sorted(refs[item] for item in DEPENDENCIES[name])}
        )
    for name in sorted(pins):
        dependency_rows.append(
            {
                "ref": refs[name],
                "dependsOn": sorted(refs[item] for item in DEPENDENCIES.get(name, [])),
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed)),
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "flowness-open-alpha@1.0.0a1",
                "name": "flowness-open-alpha",
                "version": "1.0.0a1",
            },
            "properties": [
                {"name": "flowness:sbom-state", "value": "locked-transitive-candidate"},
                {"name": "flowness:release-authorized", "value": "false"},
                {"name": "flowness:offline-wheelhouse", "value": "not-sealed-cross-platform"},
                {"name": "flowness:harness-uv-lock-sha256", "value": _digest(root / "harness/uv.lock")},
                {"name": "flowness:oss-uv-lock-sha256", "value": _digest(root / "oss/flowness-oss-harness/uv.lock")},
                {"name": "flowness:build-system-lock-sha256", "value": _digest(root / "harness/build-system-requirements.lock")},
            ],
        },
        "components": components,
        "dependencies": dependency_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.repo.resolve())
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
