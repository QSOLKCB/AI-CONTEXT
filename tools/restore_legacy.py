#!/usr/bin/env python3
"""Phase 8 portable restore and migration support for AI-CONTEXT.

A restore reconstructs portable governed context. It never claims to recreate a provider
session, model identity, hidden provider memory, or chain of thought.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import ai_context as core
import curation
import routing

RESTORE_VERSION = "0.1.0"
RESTORE_PROTOCOL = "AI-CONTEXT/RESTORE-MANIFEST"
MIGRATION_PROTOCOL = "AI-CONTEXT/MIGRATION-MANIFEST"
ENRICHMENT_PROTOCOL = "AI-CONTEXT/STYLE-CULTURE-ENRICHMENT"
RESTORE_CLAIM = "context-continuity-not-model-identity"
PROVIDER_DEPENDENCY = "none"

MAX_ARCHIVE_MEMBERS = 20000
MAX_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024

MINIMUM_REQUIRED = {
    "policy.json",
    "memory/records.jsonl",
    "routing/policy.json",
}
FULL_ROOTS = ("receipts", "staging", "memory", "profiles", "routing", "curation")
ARTIFACT_CLASSES = {
    "workspace_policy",
    "canonical_memory",
    "profile",
    "routing_policy",
    "curation_state",
    "source_evidence",
    "receipt",
    "style_culture_enrichment",
}


class RestoreError(core.ContextError):
    pass


def canonical_sha(value: Any) -> str:
    return core.sha256_bytes(core.canonical_bytes(value))


def stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}.sha256:{canonical_sha(value)}"


def _parse_version(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise RestoreError(f"{label} must be a semantic version string")
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise RestoreError(f"{label} must use major.minor.patch")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _require_known_major(value: str, supported: str, label: str) -> None:
    incoming = _parse_version(value, label)
    current = _parse_version(supported, "supported version")
    if incoming[0] != current[0]:
        raise RestoreError(
            f"unknown {label} major version {incoming[0]}; supported major is {current[0]}"
        )


def _validate_extensions(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RestoreError(f"{label}.extensions must be an object")
    # Extensions are deliberately non-authoritative opaque metadata. They are not used by
    # snapshot identity, artifact hashes, curation authority, or routing.
    core.canonical_bytes(value)
    return value


def _validate_migration(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RestoreError("migration manifest must be an object")
    allowed = {
        "id", "protocol", "schema_version", "source_workspace_version",
        "target_workspace_version", "compatibility", "unknown_major_policy",
        "additive_metadata_policy", "transformations", "extensions",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RestoreError(f"unknown migration manifest fields: {', '.join(unknown)}")
    required = allowed - {"extensions"}
    missing = sorted(required - set(value))
    if missing:
        raise RestoreError(f"migration manifest missing: {', '.join(missing)}")
    if value.get("protocol") != MIGRATION_PROTOCOL:
        raise RestoreError("invalid migration manifest protocol")
    schema_version = value.get("schema_version")
    _require_known_major(schema_version, RESTORE_VERSION, "migration schema")
    if schema_version != RESTORE_VERSION:
        raise RestoreError("unsupported migration manifest schema version")
    source = value.get("source_workspace_version")
    target = value.get("target_workspace_version")
    _require_known_major(source, core.PROTOCOL_VERSION, "source workspace")
    _require_known_major(target, core.PROTOCOL_VERSION, "target workspace")
    # The Phase 8 reference migrator is intentionally conservative: it records the plan and
    # rejects unknown workspace versions rather than pretending a byte-copy is a migration.
    if source != core.PROTOCOL_VERSION or target != core.PROTOCOL_VERSION:
        raise RestoreError(
            f"workspace migration is not implemented for {source} -> {target}; "
            f"reference runtime is {core.PROTOCOL_VERSION}"
        )
    if value.get("compatibility") not in {"exact", "additive_metadata_only"}:
        raise RestoreError("unsupported migration compatibility class")
    if value.get("unknown_major_policy") != "reject":
        raise RestoreError("migration manifest must reject unknown major versions")
    if value.get("additive_metadata_policy") != "extensions_only_non_authoritative":
        raise RestoreError("unsupported additive metadata policy")
    transformations = value.get("transformations")
    if not isinstance(transformations, list) or transformations:
        raise RestoreError("Phase 8 reference migration supports no payload transformations")
    _validate_extensions(value.get("extensions"), "migration")
    core_value = {key: item for key, item in value.items() if key not in {"id", "extensions"}}
    if value.get("id") != stable_id("migration", core_value):
        raise RestoreError("migration manifest id/hash mismatch")
    return value


def _validate_artifact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RestoreError("restore artifact must be an object")
    required = {"path", "class", "sha256", "bytes", "required"}
    if set(value) != required:
        raise RestoreError("restore artifact has missing/unknown fields")
    path = normalize_archive_relative_path(value.get("path"), label="artifact path")
    if path.startswith("payload/"):
        raise RestoreError("artifact path is logical and must not include payload/ prefix")
    artifact_class = value.get("class")
    if artifact_class not in ARTIFACT_CLASSES:
        raise RestoreError(f"unknown restore artifact class: {artifact_class}")
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RestoreError(f"invalid restore artifact sha256: {path}")
    size = value.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_ARCHIVE_MEMBER_BYTES:
        raise RestoreError(f"invalid restore artifact size: {path}")
    if value.get("required") is not True:
        raise RestoreError("Phase 8 manifest artifacts must be required once declared")
    return value


def _manifest_identity_core(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {"snapshot_id", "created_at", "extensions"}
    }


def _validate_manifest(value: Any, migration: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RestoreError("restore manifest must be an object")
    allowed = {
        "snapshot_id", "protocol", "schema_version", "workspace_protocol_version",
        "continuity_class", "restore_claim", "provider_memory_dependency", "created_at",
        "migration_manifest_sha256", "artifacts", "extensions",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RestoreError(f"unknown restore manifest fields: {', '.join(unknown)}")
    required = allowed - {"extensions"}
    missing = sorted(required - set(value))
    if missing:
        raise RestoreError(f"restore manifest missing: {', '.join(missing)}")
    if value.get("protocol") != RESTORE_PROTOCOL:
        raise RestoreError("invalid restore manifest protocol")
    schema_version = value.get("schema_version")
    _require_known_major(schema_version, RESTORE_VERSION, "restore schema")
    if schema_version != RESTORE_VERSION:
        raise RestoreError("unsupported restore manifest schema version")
    workspace_version = value.get("workspace_protocol_version")
    _require_known_major(workspace_version, core.PROTOCOL_VERSION, "workspace")
    if workspace_version != core.PROTOCOL_VERSION:
        raise RestoreError(f"unsupported workspace protocol version: {workspace_version}")
    continuity_class = value.get("continuity_class")
    if continuity_class not in {"minimum", "full"}:
        raise RestoreError("continuity_class must be minimum or full")
    if value.get("restore_claim") != RESTORE_CLAIM:
        raise RestoreError("restore manifest overstates or changes restore claim")
    if value.get("provider_memory_dependency") != PROVIDER_DEPENDENCY:
        raise RestoreError("restore archive must not depend on provider-side memory")
    if value.get("migration_manifest_sha256") != canonical_sha(migration):
        raise RestoreError("restore manifest migration hash mismatch")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RestoreError("restore manifest artifacts must be a non-empty array")
    parsed = [_validate_artifact(item) for item in artifacts]
    paths = [item["path"] for item in parsed]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RestoreError("restore artifacts must have unique lexicographically sorted paths")
    if continuity_class == "minimum":
        missing_min = sorted(MINIMUM_REQUIRED - set(paths))
        if missing_min:
            raise RestoreError(f"minimum continuity set missing: {', '.join(missing_min)}")
    _validate_extensions(value.get("extensions"), "restore")
    if value.get("snapshot_id") != stable_id("restore", _manifest_identity_core(value)):
        raise RestoreError("restore snapshot id/hash mismatch")
    return value


def validate_enrichment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RestoreError("style/culture enrichment must be an object")
    allowed = {
        "protocol", "schema_version", "class", "factual_authority", "apply_scope",
        "entries", "extensions",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RestoreError(f"unknown enrichment fields: {', '.join(unknown)}")
    required = allowed - {"extensions"}
    if required - set(value):
        raise RestoreError("style/culture enrichment missing required fields")
    if value.get("protocol") != ENRICHMENT_PROTOCOL or value.get("schema_version") != RESTORE_VERSION:
        raise RestoreError("unsupported style/culture enrichment protocol/schema")
    if value.get("class") != "style_culture" or value.get("factual_authority") != "none":
        raise RestoreError("style/culture enrichment must have zero factual authority")
    if value.get("apply_scope") != "presentation_only":
        raise RestoreError("style/culture enrichment may apply only to presentation")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise RestoreError("style/culture enrichment entries must be an array")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"label", "value", "tags"}:
            raise RestoreError("style/culture enrichment entry fields invalid")
        if not isinstance(entry.get("label"), str) or not entry["label"].strip():
            raise RestoreError("style/culture enrichment label must be non-empty")
        if not isinstance(entry.get("value"), str):
            raise RestoreError("style/culture enrichment value must be a string")
        tags = entry.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
            raise RestoreError("style/culture enrichment tags must be strings")
    _validate_extensions(value.get("extensions"), "enrichment")
    return value


def normalize_archive_relative_path(value: Any, *, label: str = "archive path") -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RestoreError(f"{label} must be a non-empty POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RestoreError(f"unsafe {label}: {value}")
    return pure.as_posix()


def _path_has_symlink(workspace: Path, path: Path) -> bool:
    relative = path.relative_to(workspace)
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _artifact_class(path: str) -> str:
    if path == "policy.json":
        return "workspace_policy"
    if path == "memory/records.jsonl":
        return "canonical_memory"
    if path.startswith("profiles/"):
        return "profile"
    if path == "routing/policy.json":
        return "routing_policy"
    if path.startswith("curation/"):
        return "curation_state"
    if path.startswith("receipts/"):
        return "receipt"
    if path.startswith("staging/"):
        return "source_evidence"
    if path == "enrichment/style-culture.json":
        return "style_culture_enrichment"
    raise RestoreError(f"cannot classify restore artifact: {path}")


def _read_workspace_file(workspace: Path, logical_path: str) -> bytes:
    logical = normalize_archive_relative_path(logical_path, label="workspace artifact path")
    path = workspace / PurePosixPath(logical)
    if _path_has_symlink(workspace, path):
        raise RestoreError(f"restore export refuses symlinked artifact: {logical}")
    if not path.is_file():
        raise RestoreError(f"restore artifact missing or not a regular file: {logical}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RestoreError(f"cannot read restore artifact {logical}: {exc}") from exc
    if len(data) > MAX_ARCHIVE_MEMBER_BYTES:
        raise RestoreError(f"restore artifact exceeds per-file limit: {logical}")
    return data


def _collect_workspace_artifacts(workspace: Path, mode: str) -> dict[str, bytes]:
    core.ensure_workspace(workspace)
    paths: set[str] = {"policy.json", "routing/policy.json"}
    records = workspace / "memory" / "records.jsonl"
    if records.exists():
        paths.add("memory/records.jsonl")
    else:
        # Empty canonical memory is valid continuity state. The restore archive makes the
        # empty store explicit without mutating the source workspace.
        paths.add("memory/records.jsonl")
    profiles = workspace / "profiles"
    for path in sorted(profiles.glob("*.json")):
        if path.is_symlink():
            raise RestoreError(f"restore export refuses symlinked profile: {path.name}")
        paths.add(path.relative_to(workspace).as_posix())
    if not any(path.startswith("profiles/") for path in paths):
        raise RestoreError("restore export requires at least one profile")
    curation_policy = workspace / "curation" / "policy.json"
    if curation_policy.exists():
        paths.add("curation/policy.json")

    if mode == "full":
        for root_name in FULL_ROOTS:
            root = workspace / root_name
            if not root.exists():
                continue
            if root.is_symlink():
                raise RestoreError(f"restore export refuses symlinked root: {root_name}")
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise RestoreError(
                        f"restore export refuses symlinked full-set artifact: "
                        f"{path.relative_to(workspace).as_posix()}"
                    )
                if path.is_file():
                    paths.add(path.relative_to(workspace).as_posix())

    artifacts: dict[str, bytes] = {}
    for logical in sorted(paths):
        path = workspace / PurePosixPath(logical)
        if logical == "memory/records.jsonl" and not path.exists():
            artifacts[logical] = b""
        else:
            artifacts[logical] = _read_workspace_file(workspace, logical)
    return artifacts


def _preflight_workspace(workspace: Path, mode: str) -> None:
    core.ensure_workspace(workspace)
    policy = core.load_policy(workspace)
    seen: set[str] = set()
    for record in core.read_jsonl(workspace / "memory" / "records.jsonl"):
        core.validate_memory_record(record, workspace, policy)
        rid = record.get("id")
        if not isinstance(rid, str) or rid in seen:
            raise RestoreError(f"duplicate/invalid canonical memory id: {rid}")
        seen.add(rid)
    for path in sorted((workspace / "profiles").glob("*.json")):
        routing.normalize_profile(core.load_json(path), expected_name=path.stem)
    routing.normalize_routing_policy(core.load_json(workspace / "routing" / "policy.json"))
    curation_policy = workspace / "curation" / "policy.json"
    if curation_policy.exists():
        curation.load_curation_policy(workspace)
    if mode == "full":
        import validate_curation
        import validate_evidence
        validate_evidence.cmd_validate(workspace)
        if curation_policy.exists():
            validate_curation.cmd_validate(workspace)


def _make_migration() -> dict[str, Any]:
    core_value = {
        "protocol": MIGRATION_PROTOCOL,
        "schema_version": RESTORE_VERSION,
        "source_workspace_version": core.PROTOCOL_VERSION,
        "target_workspace_version": core.PROTOCOL_VERSION,
        "compatibility": "exact",
        "unknown_major_policy": "reject",
        "additive_metadata_policy": "extensions_only_non_authoritative",
        "transformations": [],
    }
    return {"id": stable_id("migration", core_value), **core_value}


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def export_restore(
    workspace: Path,
    output: Path,
    *,
    mode: str,
    enrichment_path: Path | None = None,
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    if mode not in {"minimum", "full"}:
        raise RestoreError("restore export mode must be minimum or full")
    _preflight_workspace(workspace, mode)
    payloads = _collect_workspace_artifacts(workspace, mode)

    if enrichment_path is not None:
        enrichment = core.load_json(enrichment_path.expanduser().resolve())
        validate_enrichment(enrichment)
        payloads["enrichment/style-culture.json"] = core.canonical_bytes(enrichment) + b"\n"

    total = sum(len(data) for data in payloads.values())
    if total > MAX_ARCHIVE_TOTAL_BYTES:
        raise RestoreError("restore payload exceeds total byte limit")

    artifacts = [
        {
            "path": path,
            "class": _artifact_class(path),
            "sha256": core.sha256_bytes(data),
            "bytes": len(data),
            "required": True,
        }
        for path, data in sorted(payloads.items())
    ]
    migration = _make_migration()
    manifest_core = {
        "protocol": RESTORE_PROTOCOL,
        "schema_version": RESTORE_VERSION,
        "workspace_protocol_version": core.PROTOCOL_VERSION,
        "continuity_class": mode,
        "restore_claim": RESTORE_CLAIM,
        "provider_memory_dependency": PROVIDER_DEPENDENCY,
        "migration_manifest_sha256": canonical_sha(migration),
        "artifacts": artifacts,
    }
    manifest = {
        "snapshot_id": stable_id("restore", manifest_core),
        **manifest_core,
        "created_at": core.utc_now(),
    }
    # Recompute using the normative identity function to protect against accidental drift.
    manifest["snapshot_id"] = stable_id("restore", _manifest_identity_core(manifest))
    _validate_migration(migration)
    _validate_manifest(manifest, migration)

    output = output.expanduser().resolve()
    if output.exists():
        raise RestoreError(f"refusing to overwrite existing restore archive: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr(_zip_info("manifest.json"), core.canonical_bytes(manifest) + b"\n")
            archive.writestr(_zip_info("migration.json"), core.canonical_bytes(migration) + b"\n")
            for path, data in sorted(payloads.items()):
                archive.writestr(_zip_info(f"payload/{path}"), data)
        os.replace(temp, output)
    except Exception:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise
    return {
        "status": "ok",
        "archive": str(output),
        "snapshot_id": manifest["snapshot_id"],
        "continuity_class": mode,
        "artifacts": len(artifacts),
        "payload_bytes": total,
    }


def _safe_zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise RestoreError("restore archive member-count limit exceeded")
    total = 0
    result: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = normalize_archive_relative_path(info.filename)
        if name in result:
            raise RestoreError(f"duplicate restore archive member: {name}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise RestoreError(f"restore archive contains symlink member: {name}")
        if info.is_dir():
            raise RestoreError(f"restore archive must not contain directory members: {name}")
        if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RestoreError(f"restore archive member exceeds byte limit: {name}")
        total += info.file_size
        if total > MAX_ARCHIVE_TOTAL_BYTES:
            raise RestoreError("restore archive total byte limit exceeded")
        result[name] = info
    return result


def _load_archive_metadata(
    archive: zipfile.ZipFile,
    infos: dict[str, zipfile.ZipInfo],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if "manifest.json" not in infos or "migration.json" not in infos:
        raise RestoreError("restore archive missing manifest.json or migration.json")
    try:
        manifest = core.json_loads_strict(archive.read(infos["manifest.json"]).decode("utf-8"), label="manifest.json")
        migration = core.json_loads_strict(archive.read(infos["migration.json"]).decode("utf-8"), label="migration.json")
    except UnicodeDecodeError as exc:
        raise RestoreError("restore metadata must be UTF-8 JSON") from exc
    migration = _validate_migration(migration)
    manifest = _validate_manifest(manifest, migration)
    expected_members = {"manifest.json", "migration.json"} | {
        f"payload/{item['path']}" for item in manifest["artifacts"]
    }
    actual_members = set(infos)
    if actual_members != expected_members:
        extra = sorted(actual_members - expected_members)
        missing = sorted(expected_members - actual_members)
        raise RestoreError(f"restore archive member set mismatch; extra={extra} missing={missing}")
    return manifest, migration


def validate_archive(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = _safe_zip_members(archive)
            manifest, migration = _load_archive_metadata(archive, infos)
            total = 0
            for artifact in manifest["artifacts"]:
                member = infos[f"payload/{artifact['path']}"]
                data = archive.read(member)
                if len(data) != artifact["bytes"]:
                    raise RestoreError(f"restore artifact size mismatch: {artifact['path']}")
                if core.sha256_bytes(data) != artifact["sha256"]:
                    raise RestoreError(f"restore artifact hash mismatch: {artifact['path']}")
                total += len(data)
            return {
                "status": "ok",
                "archive": str(path),
                "snapshot_id": manifest["snapshot_id"],
                "continuity_class": manifest["continuity_class"],
                "artifacts": len(manifest["artifacts"]),
                "payload_bytes": total,
                "migration_id": migration["id"],
                "provider_memory_dependency": manifest["provider_memory_dependency"],
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"cannot read restore archive: {exc}") from exc


def _safe_destination_path(root: Path, logical_path: str) -> Path:
    logical = normalize_archive_relative_path(logical_path)
    candidate = root / PurePosixPath(logical)
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RestoreError(f"restore destination escapes workspace: {logical}") from exc
    return candidate


def _write_private_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        os.chmod(path, 0o600)


def _create_operational_shell(workspace: Path) -> None:
    for rel in (
        "vault/raw", "receipts", "staging", "memory", "profiles", "bundles",
        "curation", "routing", "enrichment",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    (workspace / ".gitignore").write_text(
        "vault/\nstaging/\nreceipts/\nmemory/\nbundles/\ncuration/\nrouting/\nenrichment/\n*.private.*\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "README.txt").write_text(
        "RESTORED PRIVATE AI-CONTEXT WORKSPACE\n\n"
        "This is reconstructed portable context, not an original provider/model instance.\n",
        encoding="utf-8",
        newline="\n",
    )


def validate_restored_workspace(workspace: Path, *, mode: str) -> dict[str, Any]:
    core.ensure_workspace(workspace)
    policy = core.load_policy(workspace)
    records = core.read_jsonl(workspace / "memory" / "records.jsonl")
    seen: set[str] = set()
    for record in records:
        core.validate_memory_record(record, workspace, policy)
        rid = record.get("id")
        if not isinstance(rid, str) or rid in seen:
            raise RestoreError(f"restored canonical memory has duplicate/invalid id: {rid}")
        seen.add(rid)
    profiles = sorted((workspace / "profiles").glob("*.json"))
    if not profiles:
        raise RestoreError("restored workspace has no profiles")
    for path in profiles:
        routing.normalize_profile(core.load_json(path), expected_name=path.stem)
    routing.normalize_routing_policy(core.load_json(workspace / "routing" / "policy.json"))
    curation_policy = workspace / "curation" / "policy.json"
    if curation_policy.exists():
        curation.load_curation_policy(workspace)
    if mode == "full":
        import validate_curation
        import validate_evidence
        validate_evidence.cmd_validate(workspace)
        if curation_policy.exists():
            validate_curation.cmd_validate(workspace)
    return {
        "status": "ok",
        "continuity_class": mode,
        "memory_records": len(records),
        "profiles": [path.stem for path in profiles],
        "style_culture_enrichment": (workspace / "enrichment" / "style-culture.json").exists(),
    }


def restore_archive(archive_path: Path, destination: Path) -> dict[str, Any]:
    archive_path = archive_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise RestoreError(f"refusing to restore over existing path: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    committed = False
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = _safe_zip_members(archive)
            manifest, _migration = _load_archive_metadata(archive, infos)
            _create_operational_shell(temp)
            for artifact in manifest["artifacts"]:
                data = archive.read(infos[f"payload/{artifact['path']}"])
                if len(data) != artifact["bytes"] or core.sha256_bytes(data) != artifact["sha256"]:
                    raise RestoreError(f"restore artifact changed during restore: {artifact['path']}")
                target = _safe_destination_path(temp, artifact["path"])
                if target.exists():
                    raise RestoreError(f"restore artifact collides with operational shell: {artifact['path']}")
                _write_private_file(target, data)
            validation = validate_restored_workspace(temp, mode=manifest["continuity_class"])
            os.replace(temp, destination)
            committed = True
            return {
                **validation,
                "workspace": str(destination),
                "snapshot_id": manifest["snapshot_id"],
                "restore_claim": manifest["restore_claim"],
                "provider_memory_dependency": manifest["provider_memory_dependency"],
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"restore failed: {exc}") from exc
    finally:
        if not committed and temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 8 restore and migration")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="create a portable restore archive")
    export.add_argument("workspace")
    export.add_argument("output")
    export.add_argument("--mode", choices=("minimum", "full"), default="minimum")
    export.add_argument("--enrichment")

    validate = sub.add_parser("validate", help="validate an archive without restoring")
    validate.add_argument("archive")

    restore = sub.add_parser("restore", help="restore into a new workspace path")
    restore.add_argument("archive")
    restore.add_argument("destination")

    inspect = sub.add_parser("inspect", help="show portable restore metadata")
    inspect.add_argument("archive")
    return parser


def inspect_archive(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = _safe_zip_members(archive)
            manifest, migration = _load_archive_metadata(archive, infos)
            return {"manifest": manifest, "migration": migration}
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"cannot inspect restore archive: {exc}") from exc


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "export":
            result = export_restore(
                Path(args.workspace),
                Path(args.output),
                mode=args.mode,
                enrichment_path=Path(args.enrichment) if args.enrichment else None,
            )
        elif args.command == "validate":
            result = validate_archive(Path(args.archive))
        elif args.command == "restore":
            result = restore_archive(Path(args.archive), Path(args.destination))
        elif args.command == "inspect":
            result = inspect_archive(Path(args.archive))
        else:
            raise RestoreError(f"unsupported restore command: {args.command}")
    except (RestoreError, core.ContextError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
