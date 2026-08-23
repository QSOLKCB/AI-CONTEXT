#!/usr/bin/env python3
"""Dependency-light AI-CONTEXT reference CLI.

Imported material becomes provenance-preserving observations first. It does not become
canonical memory until an explicit promotion step succeeds under workspace policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

PROTOCOL_VERSION = "0.1.0"
ADAPTER_VERSION = "0.1.0"

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".csv", ".tsv", ".py", ".rs", ".js", ".mjs",
    ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".bash", ".zsh",
    ".fish", ".sql", ".xml", ".go", ".java", ".kt", ".swift", ".c", ".h",
    ".cpp", ".hpp", ".wgsl", ".glsl",
}

DEFAULT_POLICY = {
    "protocol": "AI-CONTEXT/POLICY",
    "schema_version": PROTOCOL_VERSION,
    "default_sensitivity": "private",
    "forbid_secret_memory": True,
    "allow_source_free_user_assertions": True,
    "max_archive_members": 5000,
    "max_archive_member_bytes": 64 * 1024 * 1024,
    "max_archive_total_bytes": 512 * 1024 * 1024,
    "max_repo_files": 10000,
    "max_repo_file_bytes": 4 * 1024 * 1024,
    "max_repo_total_bytes": 256 * 1024 * 1024,
}

SENSITIVITY_RANK = {"public": 0, "private": 1, "restricted": 2, "secret": 3}
RECORD_TYPES = {
    "fact", "preference", "project_state", "decision", "claim", "hypothesis",
    "instruction", "relationship", "publication", "event", "environment",
    "provenance_policy",
}
EPISTEMIC_STATES = {
    "observed", "retrieved", "parsed", "inferred", "remembered", "user_asserted",
    "verified", "unknown",
}
SECRET_PATTERNS = {
    "pem_private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "openai_style_token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b", re.IGNORECASE),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}


class ContextError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextError(f"cannot parse JSON {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContextError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
        if not isinstance(value, dict):
            raise ContextError(f"invalid JSONL object {path}:{line_no}")
        rows.append(value)
    return rows


def append_jsonl_unique(path: Path, rows: Iterable[dict[str, Any]], key: str = "id") -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = read_jsonl(path)
    existing = {row.get(key): canonical_bytes(row) for row in existing_rows if row.get(key)}
    appended = 0
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            row_id = row.get(key)
            if not row_id:
                raise ContextError(f"row missing {key}")
            rendered = canonical_bytes(row)
            if row_id in existing:
                if existing[row_id] != rendered:
                    raise ContextError(f"id collision with different payload: {row_id}")
                continue
            handle.write(rendered.decode("utf-8") + "\n")
            existing[row_id] = rendered
            appended += 1
    return appended


def load_policy(workspace: Path) -> dict[str, Any]:
    policy_path = workspace / "policy.json"
    if not policy_path.exists():
        raise ContextError(f"workspace missing policy.json: {workspace}")
    policy = load_json(policy_path)
    if not isinstance(policy, dict) or policy.get("protocol") != "AI-CONTEXT/POLICY":
        raise ContextError("invalid workspace policy")
    if policy.get("schema_version") != PROTOCOL_VERSION:
        raise ContextError("unsupported workspace policy schema version")
    return {**DEFAULT_POLICY, **policy}


def ensure_workspace(workspace: Path) -> None:
    required = ["policy.json", "receipts", "staging", "memory", "profiles", "bundles"]
    missing = [name for name in required if not (workspace / name).exists()]
    if missing:
        raise ContextError(f"not an AI-CONTEXT workspace; missing: {', '.join(missing)}")


def cmd_init(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise ContextError(f"refusing to initialize non-empty directory: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    for rel in ["vault/raw", "receipts", "staging", "memory", "profiles", "bundles"]:
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    write_json(workspace / "policy.json", DEFAULT_POLICY)
    write_json(workspace / "profiles" / "general.json", {
        "protocol": "AI-CONTEXT/PROFILE",
        "schema_version": PROTOCOL_VERSION,
        "name": "general",
        "include_tags": [],
        "exclude_tags": [],
        "record_types": sorted(RECORD_TYPES),
        "max_sensitivity": "private",
    })
    (workspace / ".gitignore").write_text(
        "vault/\nstaging/\nreceipts/\nmemory/\nbundles/\n*.private.*\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "README.txt").write_text(
        "PRIVATE AI-CONTEXT WORKSPACE\n\n"
        "Do not publish this directory. Raw imports are evidence, not canonical memory.\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"initialized private workspace: {workspace}")


def safe_zip_infos(archive: zipfile.ZipFile, policy: dict[str, Any]) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > int(policy["max_archive_members"]):
        raise ContextError("archive member-count limit exceeded")
    total = 0
    safe: list[zipfile.ZipInfo] = []
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or ".." in pure.parts:
            raise ContextError(f"unsafe archive path: {info.filename}")
        if info.is_dir():
            continue
        if info.file_size > int(policy["max_archive_member_bytes"]):
            raise ContextError(
                f"archive member too large: {info.filename}; adjust private workspace policy only if intentional"
            )
        total += info.file_size
        if total > int(policy["max_archive_total_bytes"]):
            raise ContextError("archive expanded-byte limit exceeded")
        safe.append(info)
    return safe


def source_identity(path: Path, policy: dict[str, Any]) -> str:
    if path.is_file():
        return hash_file(path)
    if not path.is_dir():
        raise ContextError(f"source does not exist: {path}")

    digest = hashlib.sha256()
    count = 0
    total = 0
    for child in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix()):
        rel_parts = child.relative_to(path).parts
        if child.is_symlink() or ".git" in rel_parts or not child.is_file():
            continue
        count += 1
        if count > int(policy["max_repo_files"]):
            raise ContextError("directory file-count limit exceeded")
        size = child.stat().st_size
        total += size
        if total > int(policy["max_repo_total_bytes"]):
            raise ContextError("directory byte limit exceeded")
        rel = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        with child.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def observation(
    receipt_id: str,
    kind: str,
    content: dict[str, Any],
    *,
    source_local_id: str | None = None,
    actor: str | None = None,
    timestamp: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content_hash = sha256_bytes(canonical_bytes(content))
    core = {
        "protocol": "AI-CONTEXT/OBSERVATION",
        "schema_version": PROTOCOL_VERSION,
        "source_receipt_id": receipt_id,
        "kind": kind,
        "source_local_id": source_local_id,
        "actor": actor,
        "timestamp": timestamp,
        "content": content,
        "content_sha256": content_hash,
        "metadata": metadata or {},
    }
    obs_hash = sha256_bytes(canonical_bytes(core))
    return {"id": f"obs.sha256:{obs_hash}", **core}


def stringify_content_part(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        if isinstance(part.get("text"), str):
            return part["text"]
        return json.dumps(part, sort_keys=True, ensure_ascii=False)
    if part is None:
        return ""
    return str(part)


def parse_chatgpt(data: Any, receipt_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(data, list) or not data:
        raise ContextError("ChatGPT adapter expected a non-empty conversations list")
    observations: list[dict[str, Any]] = []
    warnings: list[str] = []
    recognized = 0
    for conv_index, conv in enumerate(data):
        if not isinstance(conv, dict) or not isinstance(conv.get("mapping"), dict):
            continue
        recognized += 1
        conv_id = str(conv.get("id") or f"conversation-{conv_index}")
        title = str(conv.get("title") or "")
        nodes = []
        for node_id, node in conv["mapping"].items():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            nodes.append((message.get("create_time") is None, message.get("create_time") or 0, str(node_id), message))
        for _, _, node_id, message in sorted(nodes, key=lambda item: (item[0], item[1], item[2])):
            author = message.get("author") if isinstance(message.get("author"), dict) else {}
            actor = author.get("role") if isinstance(author.get("role"), str) else None
            msg_content = message.get("content") if isinstance(message.get("content"), dict) else {}
            parts = msg_content.get("parts") if isinstance(msg_content.get("parts"), list) else []
            text = "\n".join(filter(None, (stringify_content_part(part) for part in parts))).strip()
            if not text:
                continue
            timestamp = str(message.get("create_time")) if message.get("create_time") is not None else None
            observations.append(observation(
                receipt_id,
                "conversation_message",
                {"text": text, "conversation_title": title},
                source_local_id=str(message.get("id") or node_id),
                actor=actor,
                timestamp=timestamp,
                metadata={"conversation_id": conv_id, "provider_layout": "chatgpt-conversations-mapping"},
            ))
    if recognized == 0:
        raise ContextError("ChatGPT layout not recognized")
    if recognized != len(data):
        warnings.append("some top-level entries did not match the ChatGPT conversation layout")
    return observations, warnings


def parse_claude(data: Any, receipt_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(data, list) or not data:
        raise ContextError("Claude adapter expected a non-empty conversations list")
    observations: list[dict[str, Any]] = []
    warnings: list[str] = []
    recognized = 0
    for conv_index, conv in enumerate(data):
        if not isinstance(conv, dict) or not isinstance(conv.get("chat_messages"), list):
            continue
        recognized += 1
        conv_id = str(conv.get("uuid") or conv.get("id") or f"conversation-{conv_index}")
        title = str(conv.get("name") or conv.get("title") or "")
        for msg_index, message in enumerate(conv["chat_messages"]):
            if not isinstance(message, dict):
                continue
            raw_text = message.get("text")
            if isinstance(raw_text, list):
                text = "\n".join(stringify_content_part(part) for part in raw_text).strip()
            else:
                text = stringify_content_part(raw_text).strip()
            if not text:
                continue
            observations.append(observation(
                receipt_id,
                "conversation_message",
                {"text": text, "conversation_title": title},
                source_local_id=str(message.get("uuid") or message.get("id") or f"{conv_id}:{msg_index}"),
                actor=str(message.get("sender")) if message.get("sender") is not None else None,
                timestamp=str(message.get("created_at")) if message.get("created_at") is not None else None,
                metadata={"conversation_id": conv_id, "provider_layout": "claude-chat-messages"},
            ))
    if recognized == 0:
        raise ContextError("Claude layout not recognized")
    if recognized != len(data):
        warnings.append("some top-level entries did not match the Claude conversation layout")
    return observations, warnings


def parse_json_value(value: Any, receipt_id: str, source_local_id: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            observation(receipt_id, "json_value", {"value": item}, source_local_id=f"{source_local_id}#{index}")
            for index, item in enumerate(value)
        ]
    return [observation(receipt_id, "json_value", {"value": value}, source_local_id=source_local_id)]


def parse_text_bytes(
    data: bytes,
    receipt_id: str,
    source_local_id: str,
    kind: str = "document",
) -> dict[str, Any] | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return observation(receipt_id, kind, {"text": text}, source_local_id=source_local_id)


def detect_json_adapter(data: Any) -> str:
    if isinstance(data, list) and any(
        isinstance(item, dict) and isinstance(item.get("mapping"), dict) for item in data[:10]
    ):
        return "chatgpt"
    if isinstance(data, list) and any(
        isinstance(item, dict) and isinstance(item.get("chat_messages"), list) for item in data[:10]
    ):
        return "claude"
    return "generic-json"


def find_zip_conversations(infos: list[zipfile.ZipInfo]) -> zipfile.ZipInfo | None:
    candidates = [
        info for info in infos if PurePosixPath(info.filename).name.lower() == "conversations.json"
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda info: (len(PurePosixPath(info.filename).parts), info.filename))[0]


def provider_status(warnings: list[str]) -> str:
    return "partial" if warnings else "exact"


def import_zip(
    path: Path,
    adapter: str,
    receipt_id: str,
    policy: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        infos = safe_zip_infos(archive, policy)
        conversations = find_zip_conversations(infos)

        if adapter in {"chatgpt", "claude"} and conversations is None:
            raise ContextError(f"{adapter} adapter expected conversations.json in archive")

        if conversations is not None and adapter in {"auto", "chatgpt", "claude"}:
            try:
                with archive.open(conversations, "r") as handle:
                    data = json.load(handle)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContextError(f"cannot parse {conversations.filename}: {exc}") from exc
            chosen = detect_json_adapter(data) if adapter == "auto" else adapter
            if chosen == "chatgpt":
                obs, extra = parse_chatgpt(data, receipt_id)
                return "chatgpt-export", provider_status(extra), obs, warnings + extra
            if chosen == "claude":
                obs, extra = parse_claude(data, receipt_id)
                return "claude-export", provider_status(extra), obs, warnings + extra

        observations: list[dict[str, Any]] = []
        for info in sorted(infos, key=lambda item: item.filename):
            suffix = Path(info.filename).suffix.lower()
            if suffix not in TEXT_EXTENSIONS:
                continue
            raw = archive.read(info)
            if suffix == ".json":
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    item = parse_text_bytes(raw, receipt_id, info.filename)
                    if item:
                        observations.append(item)
                    continue
                observations.extend(parse_json_value(value, receipt_id, info.filename))
            elif suffix == ".jsonl":
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    warnings.append(f"skipped non-UTF-8 JSONL member: {info.filename}")
                    continue
                for line_no, line in enumerate(text.splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        warnings.append(f"skipped invalid JSONL line {info.filename}:{line_no}")
                        continue
                    observations.extend(parse_json_value(value, receipt_id, f"{info.filename}:{line_no}"))
            else:
                item = parse_text_bytes(raw, receipt_id, info.filename)
                if item:
                    observations.append(item)
                else:
                    warnings.append(f"skipped non-UTF-8 text member: {info.filename}")
        return "generic-zip", "generic", observations, warnings


def import_file(
    path: Path,
    adapter: str,
    receipt_id: str,
) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    warnings: list[str] = []

    if adapter == "repo":
        raise ContextError("repo adapter requires a directory")

    if suffix == ".json":
        data = load_json(path)
        chosen = detect_json_adapter(data) if adapter == "auto" else adapter
        if chosen == "chatgpt":
            obs, extra = parse_chatgpt(data, receipt_id)
            return "chatgpt-export", provider_status(extra), obs, extra
        if chosen == "claude":
            obs, extra = parse_claude(data, receipt_id)
            return "claude-export", provider_status(extra), obs, extra
        return "json", "generic", parse_json_value(data, receipt_id, path.name), warnings

    if adapter in {"chatgpt", "claude", "generic-json"}:
        raise ContextError(f"{adapter} adapter requires JSON or an appropriate ZIP export")

    if suffix == ".jsonl":
        observations: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContextError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
            observations.extend(parse_json_value(value, receipt_id, f"{path.name}:{line_no}"))
        return "jsonl", "generic", observations, warnings

    raw = path.read_bytes()
    item = parse_text_bytes(raw, receipt_id, path.name)
    if item is None:
        raise ContextError("unsupported binary file; use an external extractor and import text/JSON output")
    return "document", "generic", [item], warnings


def import_repo(
    path: Path,
    receipt_id: str,
    policy: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    observations: list[dict[str, Any]] = []
    warnings: list[str] = []
    count = 0
    total = 0
    for child in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix()):
        rel = child.relative_to(path).as_posix()
        if child.is_symlink():
            warnings.append(f"skipped repository symlink: {rel}")
            continue
        if not child.is_file():
            continue
        rel_parts = child.relative_to(path).parts
        if ".git" in rel_parts:
            continue
        if child.suffix.lower() not in TEXT_EXTENSIONS and child.name not in {
            "LICENSE", "README", "Makefile", "Dockerfile"
        }:
            continue
        count += 1
        if count > int(policy["max_repo_files"]):
            raise ContextError("repository text-file limit exceeded")
        size = child.stat().st_size
        if size > int(policy["max_repo_file_bytes"]):
            warnings.append(f"skipped oversized repository file: {rel}")
            continue
        total += size
        if total > int(policy["max_repo_total_bytes"]):
            raise ContextError("repository text-byte limit exceeded")
        try:
            text = child.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            warnings.append(f"skipped non-UTF-8 repository file: {rel}")
            continue
        observations.append(observation(
            receipt_id,
            "repository_file",
            {"path": rel, "text": text},
            source_local_id=rel,
            metadata={"repository_root": path.name},
        ))
    source_type = "git-repository" if (path / ".git").exists() else "source-tree"
    return source_type, provider_status(warnings), observations, warnings


def receipt_id_for(source_hash: str, requested_adapter: str) -> str:
    seed = {
        "source_sha256": source_hash,
        "requested_adapter": requested_adapter,
        "adapter_version": ADAPTER_VERSION,
    }
    return f"import.sha256:{sha256_bytes(canonical_bytes(seed))}"


def cmd_import(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    policy = load_policy(workspace)
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise ContextError(f"source does not exist: {source}")

    source_hash = source_identity(source, policy)
    chosen_adapter = args.adapter
    receipt_id = receipt_id_for(source_hash, chosen_adapter)

    if source.is_dir():
        if chosen_adapter not in {"auto", "repo"}:
            raise ContextError("directory imports currently require --adapter auto or repo")
        source_type, parse_status, observations, warnings = import_repo(source, receipt_id, policy)
        adapter_id = "repo"
    elif zipfile.is_zipfile(source):
        source_type, parse_status, observations, warnings = import_zip(source, chosen_adapter, receipt_id, policy)
        adapter_id = source_type.split("-")[0] if source_type.endswith("-export") else "generic-zip"
    else:
        source_type, parse_status, observations, warnings = import_file(source, chosen_adapter, receipt_id)
        adapter_id = source_type.split("-")[0] if source_type.endswith("-export") else source_type

    receipt = {
        "id": receipt_id,
        "protocol": "AI-CONTEXT/IMPORT",
        "schema_version": PROTOCOL_VERSION,
        "source_type": source_type,
        "source_name": source.name,
        "source_identity_sha256": source_hash,
        "requested_adapter": chosen_adapter,
        "adapter": {"id": adapter_id, "version": ADAPTER_VERSION},
        "parse_status": parse_status,
        "observation_count": len(observations),
        "warnings": warnings,
        "imported_at": utc_now(),
    }

    # imported_at is operational metadata. Idempotent re-imports preserve the first receipt.
    receipts_path = workspace / "receipts" / "imports.jsonl"
    existing = {row.get("id"): row for row in read_jsonl(receipts_path)}
    if receipt_id in existing:
        receipt = existing[receipt_id]

    appended_obs = append_jsonl_unique(workspace / "staging" / "observations.jsonl", observations)
    appended_receipt = append_jsonl_unique(receipts_path, [receipt])
    print(json.dumps({
        "receipt_id": receipt_id,
        "source_type": source_type,
        "parse_status": parse_status,
        "observations_total": len(observations),
        "observations_appended": appended_obs,
        "receipt_appended": bool(appended_receipt),
        "warnings": warnings,
    }, indent=2, sort_keys=True))


def secret_hits(value: Any) -> list[str]:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return sorted(name for name, pattern in SECRET_PATTERNS.items() if pattern.search(rendered))


def validate_memory_record(
    record: dict[str, Any],
    workspace: Path,
    policy: dict[str, Any],
) -> None:
    required = {
        "id", "protocol", "schema_version", "record_type", "content", "sensitivity",
        "epistemic_state", "confidence", "approval", "source_refs", "tags", "lifecycle",
    }
    missing = sorted(required - set(record))
    if missing:
        raise ContextError(f"memory record missing: {', '.join(missing)}")
    if record["protocol"] != "AI-CONTEXT/MEMORY" or record["schema_version"] != PROTOCOL_VERSION:
        raise ContextError("unsupported memory protocol/schema version")
    if record["record_type"] not in RECORD_TYPES:
        raise ContextError(f"unknown record_type: {record['record_type']}")
    if record["sensitivity"] not in SENSITIVITY_RANK:
        raise ContextError(f"unknown sensitivity: {record['sensitivity']}")
    if record["epistemic_state"] not in EPISTEMIC_STATES:
        raise ContextError(f"unknown epistemic_state: {record['epistemic_state']}")
    if record["approval"] not in {"approved", "rejected", "pending"}:
        raise ContextError("invalid approval state")
    if (
        not isinstance(record["confidence"], (int, float))
        or isinstance(record["confidence"], bool)
        or not 0 <= record["confidence"] <= 1
    ):
        raise ContextError("confidence must be a number from 0 to 1")
    if not isinstance(record["content"], dict):
        raise ContextError("content must be an object")
    if not isinstance(record["source_refs"], list) or not all(
        isinstance(ref, str) for ref in record["source_refs"]
    ):
        raise ContextError("source_refs must be a string array")
    if not isinstance(record["tags"], list) or not all(
        isinstance(tag, str) and tag for tag in record["tags"]
    ):
        raise ContextError("tags must be a string array")
    lifecycle = record["lifecycle"]
    if not isinstance(lifecycle, dict) or lifecycle.get("state") not in {
        "active", "expired", "superseded", "tombstoned"
    }:
        raise ContextError("invalid lifecycle state")
    if policy.get("forbid_secret_memory", True) and record["sensitivity"] == "secret":
        raise ContextError("secret-class records are forbidden from canonical AI memory")
    hits = secret_hits(record["content"])
    if hits:
        raise ContextError(f"candidate contains secret-like material: {', '.join(hits)}")

    observations = {row.get("id") for row in read_jsonl(workspace / "staging" / "observations.jsonl")}
    missing_refs = sorted(set(record["source_refs"]) - observations)
    source_free_assertion = (
        record["epistemic_state"] == "user_asserted"
        and policy.get("allow_source_free_user_assertions", True)
    )
    if missing_refs:
        raise ContextError(f"unknown source_refs: {', '.join(missing_refs)}")
    if not record["source_refs"] and not source_free_assertion:
        raise ContextError("memory promotion requires provenance unless explicitly user_asserted by policy")


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    record = dict(candidate)
    record.setdefault("protocol", "AI-CONTEXT/MEMORY")
    record.setdefault("schema_version", PROTOCOL_VERSION)
    record.setdefault("sensitivity", "private")
    record.setdefault("epistemic_state", "user_asserted")
    record.setdefault("confidence", 1.0 if record["epistemic_state"] == "user_asserted" else 0.5)
    record.setdefault("approval", "approved")
    record.setdefault("source_refs", [])
    record.setdefault("tags", [])
    record.setdefault("lifecycle", {"state": "active", "expires_at": None, "supersedes": None})
    record.setdefault("created_at", utc_now())
    record.setdefault("last_verified", None)
    record.setdefault("notes", "")
    if "id" not in record:
        identity_payload = {
            key: value
            for key, value in record.items()
            if key not in {"created_at", "last_verified", "notes"}
        }
        record["id"] = f"memory.sha256:{sha256_bytes(canonical_bytes(identity_payload))}"
    return record


def cmd_promote(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    policy = load_policy(workspace)
    candidate = load_json(Path(args.input).expanduser().resolve())
    candidates = candidate if isinstance(candidate, list) else [candidate]
    records: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            raise ContextError("promotion input must be an object or array of objects")
        record = normalize_candidate(item)
        validate_memory_record(record, workspace, policy)
        if record["approval"] != "approved":
            raise ContextError(f"refusing to promote non-approved record: {record['id']}")
        records.append(record)
    count = append_jsonl_unique(workspace / "memory" / "records.jsonl", records)
    print(json.dumps({
        "promoted": count,
        "records": [record["id"] for record in records],
    }, indent=2, sort_keys=True))


def record_is_disclosable(
    record: dict[str, Any],
    profile: dict[str, Any],
    extra_tags: set[str],
) -> bool:
    if record.get("approval") != "approved":
        return False
    if record.get("lifecycle", {}).get("state") != "active":
        return False
    sensitivity = record.get("sensitivity")
    if sensitivity == "secret" or sensitivity not in SENSITIVITY_RANK:
        return False
    ceiling = profile.get("max_sensitivity", "private")
    if ceiling not in SENSITIVITY_RANK:
        return False
    if SENSITIVITY_RANK[sensitivity] > SENSITIVITY_RANK[ceiling]:
        return False
    allowed_types = set(profile.get("record_types", []))
    if allowed_types and record.get("record_type") not in allowed_types:
        return False
    tags = set(record.get("tags", []))
    include_tags = set(profile.get("include_tags", [])) | extra_tags
    exclude_tags = set(profile.get("exclude_tags", []))
    if tags & exclude_tags:
        return False
    if include_tags and not (tags & include_tags):
        return False
    return True


def cmd_bundle(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    policy = load_policy(workspace)
    profile_path = workspace / "profiles" / f"{args.profile}.json"
    if not profile_path.exists():
        raise ContextError(f"unknown profile: {args.profile}")
    profile = load_json(profile_path)
    if (
        not isinstance(profile, dict)
        or profile.get("protocol") != "AI-CONTEXT/PROFILE"
        or profile.get("schema_version") != PROTOCOL_VERSION
    ):
        raise ContextError("invalid or unsupported profile")
    records = read_jsonl(workspace / "memory" / "records.jsonl")
    for record in records:
        validate_memory_record(record, workspace, policy)
    extra_tags = {tag.strip() for tag in args.tags.split(",") if tag.strip()} if args.tags else set()
    selected = sorted(
        (record for record in records if record_is_disclosable(record, profile, extra_tags)),
        key=lambda record: record["id"],
    )
    store_hash = sha256_bytes(canonical_bytes(sorted(records, key=lambda record: record["id"])))
    payload = {
        "type": "ai-context-bundle",
        "protocol": "AI-CONTEXT/BUNDLE",
        "schema_version": PROTOCOL_VERSION,
        "canonicalizer": "python-json-v0.1",
        "profile": args.profile,
        "selector_tags": sorted(extra_tags),
        "canonical_store_sha256": store_hash,
        "records": selected,
    }
    payload_hash = sha256_bytes(canonical_bytes(payload))
    bundle = {**payload, "canonical_payload_sha256": payload_hash}
    rendered = canonical_bytes(bundle).decode("utf-8") + "\n"
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(json.dumps({
        "output": str(output),
        "records": len(selected),
        "sha256": payload_hash,
    }, indent=2, sort_keys=True))


def validate_receipt(receipt: dict[str, Any]) -> None:
    rid = receipt.get("id")
    if not isinstance(rid, str) or not re.fullmatch(r"import\.sha256:[0-9a-f]{64}", rid):
        raise ContextError(f"invalid receipt id: {rid}")
    if receipt.get("protocol") != "AI-CONTEXT/IMPORT" or receipt.get("schema_version") != PROTOCOL_VERSION:
        raise ContextError(f"unsupported receipt protocol/schema: {rid}")
    source_hash = receipt.get("source_identity_sha256")
    requested_adapter = receipt.get("requested_adapter")
    adapter = receipt.get("adapter")
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ContextError(f"invalid receipt source hash: {rid}")
    if not isinstance(requested_adapter, str):
        raise ContextError(f"receipt missing requested_adapter: {rid}")
    if not isinstance(adapter, dict) or adapter.get("version") != ADAPTER_VERSION:
        raise ContextError(f"unsupported receipt adapter version: {rid}")
    expected = receipt_id_for(source_hash, requested_adapter)
    if rid != expected:
        raise ContextError(f"receipt id/hash mismatch: {rid}")


def validate_observation(obs: dict[str, Any], receipt_ids: set[str]) -> None:
    oid = obs.get("id")
    if not isinstance(oid, str) or not re.fullmatch(r"obs\.sha256:[0-9a-f]{64}", oid):
        raise ContextError(f"invalid observation id: {oid}")
    if obs.get("protocol") != "AI-CONTEXT/OBSERVATION" or obs.get("schema_version") != PROTOCOL_VERSION:
        raise ContextError(f"unsupported observation protocol/schema: {oid}")
    if obs.get("source_receipt_id") not in receipt_ids:
        raise ContextError(f"orphan observation receipt: {oid}")
    expected_content_hash = sha256_bytes(canonical_bytes(obs.get("content")))
    if obs.get("content_sha256") != expected_content_hash:
        raise ContextError(f"observation content hash mismatch: {oid}")
    core = {key: value for key, value in obs.items() if key != "id"}
    expected_id = f"obs.sha256:{sha256_bytes(canonical_bytes(core))}"
    if oid != expected_id:
        raise ContextError(f"observation id/hash mismatch: {oid}")


def cmd_validate(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    policy = load_policy(workspace)
    receipts = read_jsonl(workspace / "receipts" / "imports.jsonl")
    observations = read_jsonl(workspace / "staging" / "observations.jsonl")
    records = read_jsonl(workspace / "memory" / "records.jsonl")

    receipt_ids: set[str] = set()
    receipts_by_id: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        validate_receipt(receipt)
        rid = receipt["id"]
        if rid in receipt_ids:
            raise ContextError(f"duplicate receipt id: {rid}")
        receipt_ids.add(rid)
        receipts_by_id[rid] = receipt

    observation_ids: set[str] = set()
    observation_counts = {rid: 0 for rid in receipt_ids}
    for obs in observations:
        validate_observation(obs, receipt_ids)
        oid = obs["id"]
        if oid in observation_ids:
            raise ContextError(f"duplicate observation id: {oid}")
        observation_ids.add(oid)
        observation_counts[obs["source_receipt_id"]] += 1

    for rid, receipt in receipts_by_id.items():
        if receipt.get("observation_count") != observation_counts[rid]:
            raise ContextError(f"receipt observation_count mismatch: {rid}")

    record_ids: set[str] = set()
    for record in records:
        rid = record.get("id")
        if rid in record_ids:
            raise ContextError(f"duplicate memory id: {rid}")
        record_ids.add(rid)
        validate_memory_record(record, workspace, policy)

    print(json.dumps({
        "status": "ok",
        "receipts": len(receipts),
        "observations": len(observations),
        "memory_records": len(records),
        "canonical_store_sha256": sha256_bytes(
            canonical_bytes(sorted(records, key=lambda record: record["id"]))
        ),
    }, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT private memory reference CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a private workspace")
    init.add_argument("workspace")
    init.set_defaults(func=cmd_init)

    imp = sub.add_parser("import", help="import private source material into staging")
    imp.add_argument("workspace")
    imp.add_argument("source")
    imp.add_argument(
        "--adapter",
        default="auto",
        choices=["auto", "repo", "chatgpt", "claude", "generic-json"],
    )
    imp.set_defaults(func=cmd_import)

    promote = sub.add_parser(
        "promote",
        help="explicitly promote candidate records into canonical memory",
    )
    promote.add_argument("workspace")
    promote.add_argument("--input", required=True)
    promote.set_defaults(func=cmd_promote)

    validate = sub.add_parser(
        "validate",
        help="validate receipts, observations, provenance, and canonical memory",
    )
    validate.add_argument("workspace")
    validate.set_defaults(func=cmd_validate)

    bundle = sub.add_parser("bundle", help="build a deterministic task/profile bundle")
    bundle.add_argument("workspace")
    bundle.add_argument("--profile", default="general")
    bundle.add_argument("--tags", default="")
    bundle.add_argument("--output", required=True)
    bundle.set_defaults(func=cmd_bundle)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except ContextError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
