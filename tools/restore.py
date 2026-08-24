#!/usr/bin/env python3
"""Hardened Phase 8 portable restore and migration entrypoint.

The original Phase 8 implementation is retained in ``restore_legacy.py`` for audit and
compatibility. This module closes review findings around continuity authority closure,
manifest compatibility, enrichment semantics, archive limits, and lossless re-export.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import restore_legacy as legacy
from restore_legacy import *  # noqa: F401,F403
import ai_context as core


# Preserve reviewed Phase 8 functions before patching compatibility-module globals.
_ORIGINAL_VALIDATE_MANIFEST = legacy._validate_manifest
_ORIGINAL_COLLECT = legacy._collect_workspace_artifacts
_ORIGINAL_PREFLIGHT = legacy._preflight_workspace
_ORIGINAL_VALIDATE_RESTORED = legacy.validate_restored_workspace
_ORIGINAL_VALIDATE_ARCHIVE = legacy.validate_archive
_ORIGINAL_EXPORT = legacy.export_restore

STANDARD_ENRICHMENT_PATH = "enrichment/style-culture.json"
MINIMUM_OBSERVATIONS_PATH = "staging/observations.jsonl"


def _migration_authoritative_view(value: dict[str, Any]) -> dict[str, Any]:
    """Return migration metadata that participates in restore snapshot authority.

    ``extensions`` is intentionally excluded. The stable migration id remains included and
    is itself validated by the migration contract, so adding opaque non-authoritative
    extension metadata cannot perturb the restore snapshot identity.
    """
    return {key: item for key, item in value.items() if key != "extensions"}


def migration_manifest_sha256(value: dict[str, Any]) -> str:
    return legacy.canonical_sha(_migration_authoritative_view(value))


def _validate_manifest(value: Any, migration: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RestoreError("restore manifest must be an object")

    # Validate the full migration object first, including extension-container semantics.
    legacy._validate_migration(migration)
    expected_migration_sha = migration_manifest_sha256(migration)
    if value.get("migration_manifest_sha256") != expected_migration_sha:
        raise RestoreError("restore manifest migration hash mismatch")

    # The reviewed validator hashed the full migration object. Supplying its authoritative
    # view preserves every other structural check while making extensions identity-neutral.
    parsed = _ORIGINAL_VALIDATE_MANIFEST(value, _migration_authoritative_view(migration))
    paths = {item["path"] for item in parsed["artifacts"]}

    # Every continuity class contains the base governed workspace. Full is a strict
    # superset, never an alternate archive class that may omit the foundation.
    missing_min = sorted(legacy.MINIMUM_REQUIRED - paths)
    if missing_min:
        raise RestoreError(
            f"{parsed['continuity_class']} continuity set missing: {', '.join(missing_min)}"
        )
    if not any(path.startswith("profiles/") for path in paths):
        raise RestoreError(f"{parsed['continuity_class']} continuity set has no profile")
    return parsed


def _add_tree(
    payloads: dict[str, bytes],
    workspace: Path,
    root_name: str,
) -> None:
    root = workspace / root_name
    if not root.exists():
        return
    if root.is_symlink():
        raise RestoreError(f"restore export refuses symlinked root: {root_name}")
    for path in sorted(root.rglob("*")):
        logical = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            raise RestoreError(f"restore export refuses symlinked artifact: {logical}")
        if path.is_file():
            payloads[logical] = legacy._read_workspace_file(workspace, logical)


def _collect_workspace_artifacts(workspace: Path, mode: str) -> dict[str, bytes]:
    payloads = _ORIGINAL_COLLECT(workspace, mode)

    if mode == "minimum":
        # Minimum continuity is dependency-closed, not merely small. Preserve the complete
        # curation authority log and import/source receipts, plus the staging observations
        # referenced by canonical memory and curation records. Large content payloads and
        # indexes remain a full-working-set concern.
        _add_tree(payloads, workspace, "curation")
        _add_tree(payloads, workspace, "receipts")
        observations = workspace / MINIMUM_OBSERVATIONS_PATH
        if observations.exists():
            if observations.is_symlink():
                raise RestoreError("restore export refuses symlinked staging observations")
            payloads[MINIMUM_OBSERVATIONS_PATH] = legacy._read_workspace_file(
                workspace, MINIMUM_OBSERVATIONS_PATH
            )

    # A full restore-export-restore cycle must not silently discard installed presentation
    # enrichment. Only the single standard enrichment artifact is portable authority-free
    # state; arbitrary files beneath enrichment/ are not swept in.
    if mode == "full":
        enrichment = workspace / STANDARD_ENRICHMENT_PATH
        if enrichment.exists():
            if enrichment.is_symlink():
                raise RestoreError("restore export refuses symlinked style/culture enrichment")
            value = core.load_json(enrichment)
            legacy.validate_enrichment(value)
            payloads[STANDARD_ENRICHMENT_PATH] = legacy._read_workspace_file(
                workspace, STANDARD_ENRICHMENT_PATH
            )

    return dict(sorted(payloads.items()))


def _preflight_workspace(workspace: Path, mode: str) -> None:
    _ORIGINAL_PREFLIGHT(workspace, mode)

    # Minimum archives now carry approval authority, so validate that authority before
    # exporting it just as full working-set exports do.
    curation_policy = workspace / "curation" / "policy.json"
    if mode == "minimum" and curation_policy.exists():
        import validate_curation

        validate_curation.cmd_validate(workspace)

    enrichment = workspace / STANDARD_ENRICHMENT_PATH
    if enrichment.exists():
        legacy.validate_enrichment(core.load_json(enrichment))


def validate_restored_workspace(workspace: Path, *, mode: str) -> dict[str, Any]:
    result = _ORIGINAL_VALIDATE_RESTORED(workspace, mode=mode)

    # Provider disclosure may depend on Phase 5 application authority even for a minimum
    # restore. Validate the restored approval chain instead of merely carrying its bytes.
    curation_policy = workspace / "curation" / "policy.json"
    if mode == "minimum" and curation_policy.exists():
        import validate_curation

        validate_curation.cmd_validate(workspace)

    enrichment = workspace / STANDARD_ENRICHMENT_PATH
    if enrichment.exists():
        legacy.validate_enrichment(core.load_json(enrichment))
    result["style_culture_enrichment"] = enrichment.exists()
    return result


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

    # Mirror the reader's exact member bound before writing anything. Two members are
    # always reserved for manifest.json and migration.json; explicit enrichment may add one
    # payload member when it is not already part of a full working set.
    _preflight_workspace(workspace, mode)
    payloads = _collect_workspace_artifacts(workspace, mode)
    explicit_extra = 0
    if enrichment_path is not None:
        enrichment = core.load_json(enrichment_path.expanduser().resolve())
        legacy.validate_enrichment(enrichment)
        if STANDARD_ENRICHMENT_PATH not in payloads:
            explicit_extra = 1
    if len(payloads) + explicit_extra + 2 > legacy.MAX_ARCHIVE_MEMBERS:
        raise RestoreError("restore export would exceed archive member-count limit")

    return _ORIGINAL_EXPORT(
        workspace,
        output,
        mode=mode,
        enrichment_path=enrichment_path,
    )


def validate_archive(path: Path) -> dict[str, Any]:
    """Validate bytes *and* prove that they reconstruct a governed workspace.

    Hash-only validation can accept a self-consistent but semantically invalid third-party
    archive. A throwaway restore exercises canonical, curation, routing, and enrichment
    semantics without publishing a user-visible destination.
    """
    result = _ORIGINAL_VALIDATE_ARCHIVE(path)
    with tempfile.TemporaryDirectory(prefix="ai-context-restore-validate-") as temp_root:
        destination = Path(temp_root) / "workspace"
        legacy.restore_archive(path, destination)
    return result


# Patch globals used by the reviewed implementation's public command paths.
legacy._validate_manifest = _validate_manifest
legacy._collect_workspace_artifacts = _collect_workspace_artifacts
legacy._preflight_workspace = _preflight_workspace
legacy.validate_restored_workspace = validate_restored_workspace
legacy.export_restore = export_restore
legacy.validate_archive = validate_archive

# Explicitly expose underscore-prefixed helpers used by conformance tests and future
# protocol review. ``import *`` intentionally skips these names.
_make_migration = legacy._make_migration
_validate_migration = legacy._validate_migration
_manifest_identity_core = legacy._manifest_identity_core
_safe_zip_members = legacy._safe_zip_members
_load_archive_metadata = legacy._load_archive_metadata


def main() -> int:
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
