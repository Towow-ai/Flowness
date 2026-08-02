from __future__ import annotations

from pathlib import Path

from .models import utc_now
from .registry import atomic_write_json

ATLAS = [
    ("D0", "Why Flowness exists", "public"),
    ("D1", "Goal to accepted outcome", "public"),
    ("D2", "Lifecycle and state machine", "developer"),
    ("D3", "Control, execution, evidence, security planes", "developer"),
    ("D4", "Mechanism families and consumers", "developer"),
    ("D5", "Agent, worker, judge, store runtime sequences", "architect"),
    ("D6", "Deployment, failure domains, recovery", "architect"),
    ("D7", "Permissions, data, credentials, owner gates", "architect"),
    ("D8", "Event/task/artifact/finding/acceptance provenance", "architect"),
    ("D9", "Current, designed target, extensions", "architect"),
]

CHANNELS = [
    "github",
    "website",
    "wechat",
    "zhihu",
    "juejin",
    "video",
    "community",
]


def scaffold_assets(workspace: Path) -> dict:
    atlas = [
        {
            "diagram_id": key,
            "title": title,
            "audience": audience,
            "state": "open",
            "current_or_target": None,
            "truth_sources": [],
            "failure_paths": [],
            "cannot_prove": [],
            "version": None,
        }
        for key, title, audience in ATLAS
    ]
    channels = [
        {
            "channel": channel,
            "capabilities": ["draft"],
            "state": "not_started",
            "asset_ids": [],
            "publish_requires_owner_approval": True,
            "analytics": {
                "attention": None,
                "read": None,
                "install": None,
                "first_success": None,
                "retention": None,
                "issues": None,
                "external_contributions": None,
                "adoption": None,
            },
        }
        for channel in CHANNELS
    ]
    payload = {
        "schema_version": "oss-asset-scaffold/v1",
        "created_at": utc_now(),
        "architecture_atlas": atlas,
        "channel_staging": channels,
        "required_assets": [
            "README",
            "ten-minute-quickstart",
            "interactive-demo",
            "terminology",
            "FAQ",
            "success-and-failure-cases",
            "reproducible-benchmark",
            "technical-report",
            "whitepaper-after-evidence",
            "talk-and-video-kit",
        ],
        "publisher_implemented": False,
    }
    atomic_write_json(workspace / "channel-staging" / "asset-scaffold.json", payload)
    return payload
