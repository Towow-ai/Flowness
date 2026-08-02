from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .models import TranscriptExcerpt

WRAPPER_MARKERS = (
    "<system-reminder>",
    "<command-message>",
    "<command-name>",
    "<local-command",
    "<task-notification>",
    "<teammate-message",
    "<agent-",
)
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk-ant|sk-proj|gh[pousr])-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(?:authorization|cookie)\s*:\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.S),
)
PII_PATTERNS = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d[\d -]{7,}\d)(?!\d)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)
TOPIC_RULES = {
    "flowness": re.compile(r"(?i)\bflowness\b|流程感|自航"),
    "multi_agent": re.compile(r"(?i)multi[- ]?agent|subagent|多个.?agent|编排"),
    "truth_and_evidence": re.compile(r"(?i)eventlog|账本|证据|验收|真相源|provenance"),
    "drift": re.compile(r"(?i)drift|漂移|过期|不一致"),
    "open_source": re.compile(r"(?i)open.?source|开源|README|白皮书"),
    "architecture": re.compile(r"(?i)architecture|架构|机制|状态机"),
}


def _content_blocks(content: Any) -> list[tuple[str, int]]:
    if isinstance(content, str):
        return [(content, 0)]
    if isinstance(content, list):
        pieces: list[tuple[str, int]] = []
        for block_index, item in enumerate(content):
            if isinstance(item, dict) and item.get("type") == "text":
                pieces.append((str(item.get("text", "")), block_index))
        return pieces
    return []


def _hard_nonhuman(record: dict[str, Any], path: Path) -> bool:
    content = record.get("message", {}).get("content")
    return bool(
        record.get("type") != "user"
        or record.get("message", {}).get("role") != "user"
        or record.get("toolUseResult") is not None
        or record.get("sourceToolAssistantUUID") is not None
        or record.get("sourceToolUseID") is not None
        or record.get("isApiErrorMessage") is True
        or record.get("isCompactSummary") is True
        or record.get("synthetic") is True
        or record.get("isMeta") is True
        or record.get("isSidechain") is True
        or record.get("agentName")
        or record.get("teamName")
        or "subagents" in path.parts
        or (
            isinstance(content, list)
            and any(
                isinstance(item, dict) and item.get("type") == "tool_result"
                for item in content
            )
        )
    )


def _classify_origin(record: dict[str, Any], text: str, path: Path) -> str:
    if _hard_nonhuman(record, path):
        return "nonhuman"
    if any(marker in text for marker in WRAPPER_MARKERS):
        return "ambiguous"
    if record.get("promptSource") == "typed":
        return "human_confirmed"
    if record.get("origin", {}).get("kind") == "human":
        return "human_probable"
    if (
        record.get("entrypoint") == "cli"
        and record.get("sessionKind") in {None, ""}
        and record.get("promptSource") in {None, ""}
    ):
        return "human_probable"
    return "ambiguous"


def _redact(text: str) -> tuple[str, list[str]]:
    actions: list[str] = []
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.search(redacted):
            actions.append("secret-span-dropped")
            redacted = pattern.sub("[SECRET_DROPPED]", redacted)
    counters: dict[str, int] = {}
    for label, pattern in PII_PATTERNS:
        def replace(_: re.Match[str], key: str = label) -> str:
            counters[key] = counters.get(key, 0) + 1
            return f"[{key}_{counters[key]}]"

        if pattern.search(redacted):
            actions.append(f"{label.lower()}-aliased")
            redacted = pattern.sub(replace, redacted)
    return redacted, actions


def _topics(text: str) -> list[str]:
    return [name for name, pattern in TOPIC_RULES.items() if pattern.search(text)]


def _project_files(projects_root: Path, namespace: str) -> Iterable[Path]:
    for directory in sorted(projects_root.iterdir()):
        if not directory.is_dir() or not fnmatch.fnmatch(directory.name, namespace):
            continue
        for path in sorted(directory.glob("*.jsonl")):
            yield path


def extract_transcripts(
    projects_root: Path,
    namespace: str,
    snapshot_id: str,
    include_probable: bool = False,
) -> list[TranscriptExcerpt]:
    excerpts: list[TranscriptExcerpt] = []
    seen: set[tuple[str, str]] = set()
    for path in _project_files(projects_root.resolve(), namespace):
        session_alias = hashlib.sha256(path.stem.encode()).hexdigest()[:12]
        source_namespace = path.parent.name
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for text, block_index in _content_blocks(
                    record.get("message", {}).get("content")
                ):
                    if not text.strip():
                        continue
                    origin_class = _classify_origin(record, text, path)
                    if origin_class != "human_confirmed" and not (
                        include_probable and origin_class == "human_probable"
                    ):
                        continue
                    topics = _topics(text)
                    if not topics:
                        continue
                    raw_hash = hashlib.sha256(line.encode()).hexdigest()
                    normalized = " ".join(text.split())
                    text_hash = hashlib.sha256(normalized.encode()).hexdigest()
                    record_uuid = str(record.get("uuid", ""))
                    if record_uuid:
                        dedupe_key = (record_uuid, text_hash)
                    else:
                        dedupe_key = (
                            f"{source_namespace}/{session_alias}/"
                            f"{line_number}/{block_index}",
                            text_hash,
                        )
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    redacted, actions = _redact(text)
                    excerpt_material = (
                        f"{source_namespace}\0{session_alias}\0{raw_hash}\0{block_index}"
                    )
                    excerpt_id = "quote-" + hashlib.sha256(
                        excerpt_material.encode()
                    ).hexdigest()[:16]
                    excerpts.append(
                        TranscriptExcerpt(
                            excerpt_id=excerpt_id,
                            snapshot_id=snapshot_id,
                            source_namespace=source_namespace,
                            session_alias=session_alias,
                            record_uuid=record_uuid,
                            parent_uuid=record.get("parentUuid"),
                            timestamp=record.get("timestamp"),
                            line_number=line_number,
                            block_index=block_index,
                            char_span=[0, len(text)],
                            raw_record_sha256=raw_hash,
                            extracted_text_sha256=text_hash,
                            origin_class=origin_class,
                            topic_labels=topics,
                            redaction_actions=actions,
                            text=redacted,
                        )
                    )
    return excerpts


def write_excerpts(path: Path, excerpts: list[TranscriptExcerpt]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for excerpt in excerpts:
            handle.write(json.dumps(excerpt.to_dict(), ensure_ascii=False) + "\n")
