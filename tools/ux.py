#!/usr/bin/env python3
"""Hardened Phase 11 local operator UX for AI-CONTEXT.

The original Phase 11 implementation is retained in ``ux_legacy.py``. This public
entrypoint tightens the review findings around read-only governance logs, stable
candidate status names, explicit conflict resolution, resumable approved-candidate
application, and import-result observability while preserving the established
Phase 1-10 authority paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ux_legacy as legacy
from ux_legacy import *  # noqa: F401,F403


_ORIGINAL_READ_ROWS = legacy._read_rows
_ORIGINAL_CANDIDATE_STATUS_MAPS = legacy._candidate_status_maps
_ORIGINAL_LIST_CANDIDATES = legacy.list_candidates
_ORIGINAL_IMPORT_SOURCE = legacy.import_source


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Read an optional JSONL log, failing closed for unsafe filesystem objects."""
    if path.is_symlink():
        raise UXError(f"governance log must not be a symlink: {path}")
    if not path.exists():
        return []
    if not path.is_file():
        raise UXError(f"governance log must be a normal file: {path}")
    return legacy.core.read_jsonl(path)


def _candidate_status_maps(
    workspace: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    """Normalize stored review decision `approve` to the public UX status `approved`."""
    candidates, latest, applied = _ORIGINAL_CANDIDATE_STATUS_MAPS(workspace)
    normalized: dict[str, dict[str, Any]] = {}
    for candidate_id, decision in latest.items():
        row = dict(decision)
        if row.get("decision") == "approve":
            row["decision"] = "approved"
        normalized[candidate_id] = row
    return candidates, normalized, applied


def list_candidates(workspace: Path, *, status: str = "pending") -> list[dict[str, Any]]:
    """Return the stable UX queue; accept legacy `approve` only as an input alias."""
    requested = "approved" if status == "approve" else status
    return _ORIGINAL_LIST_CANDIDATES(workspace, status=requested)


def import_source(
    workspace: Path,
    source: Path,
    *,
    mode: str,
    adapter: str | None,
    plugin: Path | None,
    yes: bool,
) -> dict[str, Any]:
    """Import through the established write path and report newly staged observation IDs.

    Observation IDs are operator-visible handles for the next curation step. Reporting
    them does not create review, application, memory, or disclosure authority.
    """
    resolved = workspace.expanduser().resolve()
    before = {
        row.get("id")
        for row in _read_rows(resolved / "staging" / "observations.jsonl")
        if isinstance(row.get("id"), str)
    }
    result = _ORIGINAL_IMPORT_SOURCE(
        workspace,
        source,
        mode=mode,
        adapter=adapter,
        plugin=plugin,
        yes=yes,
    )
    after = {
        row.get("id")
        for row in _read_rows(resolved / "staging" / "observations.jsonl")
        if isinstance(row.get("id"), str)
    }
    rendered = dict(result)
    rendered["observation_ids"] = sorted(after - before)
    return rendered


def _tui_review(workspace: Path) -> None:
    """Review pending candidates or resume application of approved/unapplied ones."""
    queue = [
        row
        for row in list_candidates(workspace, status="all")
        if row.get("status") in {"pending", "approved"}
    ]
    if not queue:
        print("No pending or approved-unapplied candidates.")
        return

    for index, row in enumerate(queue, 1):
        print(
            f"{index}. {row['candidate_id']} [{row['record_type']}] "
            f"status={row['status']} key={row['semantic_key']}"
        )
    raw = input("Candidate number or ID: ").strip()
    try:
        candidate_id = queue[int(raw) - 1]["candidate_id"] if raw.isdigit() else raw
    except (IndexError, ValueError):
        print("Invalid candidate selection.")
        return

    selected = next((row for row in queue if row["candidate_id"] == candidate_id), None)
    if selected is None:
        print("Unknown candidate.")
        return
    candidate = legacy._map_rows(workspace / "curation" / "candidates.jsonl").get(candidate_id)
    if candidate is None:
        print("Unknown candidate.")
        return

    print("\nProposed memory:")
    legacy._print_json(candidate.get("proposed_memory"))
    conflicts = legacy.candidate_conflicts_read_only(workspace, candidate_id)["conflicts"]
    print(f"\nOpen conflicts: {len(conflicts)}")
    if conflicts:
        legacy._print_json(conflicts)

    if selected.get("status") == "approved":
        print("\nThis candidate is already approved and has not been applied.")
        if legacy._confirm_interactive(
            "Apply this existing approved candidate to canonical memory now? "
            "This does not create another review decision."
        ):
            legacy._print_json(legacy.apply_candidate(workspace, candidate_id, yes=True))
        else:
            print("Cancelled.")
        return

    decision = input("Decision [approve/reject/revise] (default revise): ").strip() or "revise"
    if decision not in {"approve", "reject", "revise"}:
        print("Unsupported decision.")
        return

    resolution = "none"
    if decision == "approve" and conflicts:
        while True:
            resolution = input(
                "Conflict resolution [coexist/supersede_existing/cancel] (required): "
            ).strip()
            if resolution == "cancel":
                print("Cancelled. No conflict-resolution decision was recorded.")
                return
            if resolution in {"coexist", "supersede_existing"}:
                break
            if not resolution:
                print("Explicit conflict resolution is required; blank input has no authority.")
            else:
                print("Unsupported conflict resolution.")

    notes = input("Review notes (optional): ").strip()
    if not legacy._confirm_interactive(
        f"Record HUMAN review decision '{decision}' for {candidate_id}?"
    ):
        print("Cancelled.")
        return

    result = legacy.review_candidate(
        workspace,
        candidate_id,
        decision=decision,
        actor_label="local-tui-user",
        conflict_resolution=resolution,
        notes=notes,
        yes=True,
    )
    legacy._print_json(result)
    if decision == "approve" and legacy._confirm_interactive(
        "Apply this approved candidate to canonical memory now? "
        "This is a separate authority event."
    ):
        legacy._print_json(legacy.apply_candidate(workspace, candidate_id, yes=True))


# Patch the compatibility module globals so its public commands and TUI dispatch all use
# the hardened semantics above.
legacy._read_rows = _read_rows
legacy._candidate_status_maps = _candidate_status_maps
legacy.list_candidates = list_candidates
legacy.import_source = import_source
legacy._tui_review = _tui_review

# Re-export the hardened functions for direct library callers.
workspace_status = legacy.workspace_status
candidate_status = legacy.candidate_status
candidate_conflicts_read_only = legacy.candidate_conflicts_read_only
provenance_read_only = legacy.provenance_read_only
inspect_bundle_read_only = legacy.inspect_bundle_read_only
review_candidate = legacy.review_candidate
apply_candidate = legacy.apply_candidate
backup_workspace = legacy.backup_workspace
restore_workspace = legacy.restore_workspace
run_tui = legacy.run_tui
build_parser = legacy.build_parser
main = legacy.main


if __name__ == "__main__":
    raise SystemExit(main())
