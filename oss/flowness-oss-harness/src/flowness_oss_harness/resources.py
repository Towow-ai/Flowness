from __future__ import annotations

import sys
from pathlib import Path


_CORE_SENTINELS = (
    # These three public schemas are the complete resource dependency of the
    # Open Alpha demo.  Root discovery must identify a real Flowness bundle,
    # but must not turn unrelated private/release-preparation resources into
    # process-wide import requirements.
    "open-alpha-demo-producer-result.schema.json",
    "open-alpha-demo-jury-report.schema.json",
    "open-alpha-demo-trace.schema.json",
)


def _is_resource_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    schemas = path / "schemas"
    if not schemas.is_dir():
        return False
    return all(
        (schemas / name).is_file() and not (schemas / name).is_symlink()
        for name in _CORE_SENTINELS
    )


def locate_resource_root() -> Path:
    """Locate a source checkout or installed public runtime bundle.

    This check intentionally proves only bundle identity.  A consumer that
    needs a policy, role registry, or additional schema remains responsible
    for reading and validating that resource, and therefore fails closed when
    its own dependency is absent.
    """

    source_root = Path(__file__).resolve().parents[2]
    installed_root = Path(sys.prefix).resolve() / "flowness-oss-harness"
    for candidate in dict.fromkeys((source_root, installed_root)):
        if _is_resource_root(candidate):
            return candidate
    raise RuntimeError(
        "Flowness public runtime resources are missing; expected the Open Alpha "
        "schema sentinels in schemas/ beside the source tree or in the "
        "flowness-oss-harness data directory under the install prefix"
    )


PACKAGE_ROOT = locate_resource_root()
CONFIG_ROOT = PACKAGE_ROOT / "config"
SCHEMAS_ROOT = PACKAGE_ROOT / "schemas"
