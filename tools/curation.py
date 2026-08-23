#!/usr/bin/env python3
"""Phase 5 hardened curation entrypoint.

The original Phase 5 implementation is retained in curation_legacy.py. This module wraps
that implementation with the security invariants discovered during Codex review: private
workspace state, timestamp validation, sensitivity non-downgrade, self-supersession
rejection, conflict re-review, and transaction-style candidate application.
"""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Any

import ai_context
import curation_legacy as legacy
from curation_legacy import *  # noqa: F401,F403


_ORIGINAL_VALIDATE_PROPOSED = legacy.validate_proposed_memory
_ORIGINAL_APPEND_SUPERSESSION = legacy.append_supersession


def _ensure_private_ignore(workspace: Path) -> None:
    path = workspace / ".gitignore"
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if "curation/" not in lines:
        lines.append("curation/")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def ensure_curation(workspace: Path) -> None:
    ai_context.ensure_workspace(workspace)
    root = workspace / "curation"
    root.mkdir(parents=True, exist_ok=True)
    policy_path = root / "policy.json"
    if not policy_path.exists():
        ai_context.write_json(policy_path, legacy.DEFAULT_CURATION_POLICY)
    _ensure_private_ignore(workspace)


def validate_proposed_memory(proposed: dict[str, Any]) -> None:
    _ORIGINAL_VALIDATE_PROPOSED(proposed)
    lifecycle = proposed.get("lifecycle")
    expires_at = lifecycle.get("expires_at") if isinstance(lifecycle, dict) else None
    if expires_at is not None:
        legacy.parse_iso(expires_at)
    created_at = proposed.get("created_at")
    if created_at is not None:
        legacy.parse_iso(created_at)
    last_verified = proposed.get("last_verified")
    if last_verified is not None:
        legacy.parse_iso(last_verified)


def apply_retention_to_proposed(candidate: dict[str, Any], proposed: dict[str, Any], policy: dict[str, Any]) -> None:
    lifecycle = copy.deepcopy(proposed.get("lifecycle") or {})
    expires_at = lifecycle.get("expires_at")
    if expires_at is not None:
        legacy.parse_iso(expires_at)
    else:
        days = legacy.retention_days_for(policy, proposed["record_type"])
        if days is not None:
            lifecycle["expires_at"] = legacy.format_utc(
                legacy.parse_iso(candidate["created_at"]) + legacy.timedelta(days=days)
            )
    proposed["lifecycle"] = lifecycle


def detect_conflicts(workspace: Path, candidate: dict[str, Any], *, persist: bool) -> list[dict[str, Any]]:
    proposed = candidate["proposed_memory"]
    semantic_key = candidate.get("semantic_key") or legacy.derive_semantic_key(proposed)
    if not semantic_key:
        return []
    proposed_sensitivity = proposed.get("sensitivity")
    if proposed_sensitivity is None:
        proposed_sensitivity = ai_context.load_policy(workspace)["default_sensitivity"]
    app_keys = legacy.application_semantic_keys(workspace)
    rows: list[dict[str, Any]] = []
    existing_conflicts = legacy.jsonl_map(legacy.curation_path(workspace, "conflicts"))
    for memory in ai_context.read_jsonl(workspace / "memory" / "records.jsonl"):
        if memory.get("record_type") != proposed.get("record_type"):
            continue
        if memory.get("lifecycle", {}).get("state") != "active":
            continue
        memory_key = app_keys.get(memory.get("id")) or legacy.derive_semantic_key(memory)
        if memory_key != semantic_key:
            continue
        same_content = (
            ai_context.canonical_bytes(memory.get("content"))
            == ai_context.canonical_bytes(proposed.get("content"))
        )
        existing_sensitivity = memory.get("sensitivity")
        safe_duplicate = (
            same_content
            and proposed_sensitivity in ai_context.SENSITIVITY_RANK
            and existing_sensitivity in ai_context.SENSITIVITY_RANK
            and ai_context.SENSITIVITY_RANK[proposed_sensitivity]
            >= ai_context.SENSITIVITY_RANK[existing_sensitivity]
        )
        if safe_duplicate:
            continue
        core = {
            "protocol": "AI-CONTEXT/CONFLICT",
            "schema_version": legacy.CURATION_VERSION,
            "candidate_id": candidate["id"],
            "memory_id": memory["id"],
            "semantic_key": semantic_key,
            "candidate_content_sha256": legacy.canonical_sha(proposed.get("content")),
            "memory_content_sha256": legacy.canonical_sha(memory.get("content")),
        }
        conflict_id = legacy.stable_id("conflict", core)
        rows.append(
            existing_conflicts.get(conflict_id)
            or {"id": conflict_id, **core, "created_at": ai_context.utc_now()}
        )
    if persist and rows:
        ai_context.append_jsonl_unique(legacy.curation_path(workspace, "conflicts"), rows)
    return rows


def append_supersession(
    workspace: Path,
    old_memory_id: str,
    new_memory_id: str,
    *,
    candidate_id: str | None,
    decision_id: str | None,
    actor_type: str,
    actor_label: str,
    reason: str,
) -> dict[str, Any]:
    if old_memory_id == new_memory_id:
        raise ai_context.ContextError("memory record cannot supersede itself")
    return _ORIGINAL_APPEND_SUPERSESSION(
        workspace,
        old_memory_id,
        new_memory_id,
        candidate_id=candidate_id,
        decision_id=decision_id,
        actor_type=actor_type,
        actor_label=actor_label,
        reason=reason,
    )


def _merge_unique(existing: list[dict[str, Any]], additions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_id: dict[str, bytes] = {}
    for row in existing:
        row_id = row.get("id")
        if isinstance(row_id, str):
            by_id[row_id] = ai_context.canonical_bytes(row)
    merged = list(existing)
    added = 0
    for row in additions:
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise ai_context.ContextError("transaction row missing id")
        rendered = ai_context.canonical_bytes(row)
        if row_id in by_id:
            if by_id[row_id] != rendered:
                raise ai_context.ContextError(f"id collision with different payload: {row_id}")
            continue
        by_id[row_id] = rendered
        merged.append(row)
        added += 1
    return merged, added


def _write_temp_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".txn", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(ai_context.canonical_bytes(row).decode("utf-8") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return temp_name


def _atomic_jsonl_transaction(changes: dict[Path, list[dict[str, Any]]]) -> None:
    backups: dict[Path, bytes | None] = {
        path: path.read_bytes() if path.exists() else None for path in changes
    }
    temps: dict[Path, str] = {}
    replaced: list[Path] = []
    try:
        for path, rows in changes.items():
            temps[path] = _write_temp_jsonl(path, rows)
        for path, temp_name in temps.items():
            os.replace(temp_name, path)
            replaced.append(path)
        temps.clear()
    except Exception:
        for path in reversed(replaced):
            original = backups[path]
            if original is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                fd, restore_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".rollback", dir=path.parent)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(restore_name, path)
        raise
    finally:
        for temp_name in temps.values():
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _plan_supersession(
    workspace: Path,
    memory: dict[str, Any],
    old_record: dict[str, Any],
    existing_mutations: list[dict[str, Any]],
    *,
    candidate_id: str,
    decision_id: str,
    actor: dict[str, Any],
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    old_id = old_record["id"]
    if old_id == memory["id"]:
        raise ai_context.ContextError("memory record cannot supersede itself")
    lifecycle = copy.deepcopy(old_record.get("lifecycle") or {})
    lifecycle["state"] = "superseded"
    updated = legacy.apply_patch(old_record, {"lifecycle": lifecycle})
    ai_context.validate_memory_record(updated, workspace, ai_context.load_policy(workspace))
    chain = [row for row in existing_mutations if row.get("memory_id") == old_id]
    previous = max(chain, key=lambda row: int(row.get("revision", 0))) if chain else None
    revision = 1 if previous is None else int(previous["revision"]) + 1
    core = {
        "protocol": "AI-CONTEXT/CURATION-MUTATION",
        "schema_version": legacy.CURATION_VERSION,
        "memory_id": old_id,
        "revision": revision,
        "previous_mutation_id": previous["id"] if previous else None,
        "kind": "supersede",
        "patch": {"lifecycle": lifecycle},
        "before_sha256": legacy.canonical_sha(old_record),
        "after_sha256": legacy.canonical_sha(updated),
        "actor": actor,
        "reason": reason,
        "evidence_refs": [],
        "created_at": ai_context.utc_now(),
    }
    mutation = {"id": legacy.stable_id("mutation", core), **core}
    edge_core = {
        "protocol": "AI-CONTEXT/SUPERSESSION",
        "schema_version": legacy.CURATION_VERSION,
        "from_memory_id": old_id,
        "to_memory_id": memory["id"],
        "candidate_id": candidate_id,
        "decision_id": decision_id,
        "mutation_id": mutation["id"],
        "reason": reason,
    }
    edge = {"id": legacy.stable_id("supersession", edge_core), **edge_core, "created_at": ai_context.utc_now()}
    return updated, mutation, edge


def cmd_apply(args) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    candidate = legacy.jsonl_map(legacy.curation_path(workspace, "candidates")).get(args.candidate)
    if not candidate:
        raise ai_context.ContextError(f"unknown candidate: {args.candidate}")
    decision = legacy.latest_decision(workspace, candidate["id"])
    if not decision or decision.get("decision") != "approve":
        raise ai_context.ContextError("candidate requires a latest explicit approve decision")
    actor = decision.get("actor")
    if not isinstance(actor, dict) or actor.get("type") not in legacy.ALLOWED_REVIEW_ACTORS:
        raise ai_context.ContextError("approved candidate lacks human/policy authority")

    applications_path = legacy.curation_path(workspace, "applications")
    existing_applications = ai_context.read_jsonl(applications_path)
    prior = next(
        (
            row for row in existing_applications
            if row.get("candidate_id") == candidate["id"] and row.get("decision_id") == decision["id"]
        ),
        None,
    )
    if prior is not None:
        if prior.get("memory_id") not in legacy.record_map(workspace):
            raise ai_context.ContextError("application receipt references missing canonical memory")
        print(legacy.json.dumps({
            "candidate_id": candidate["id"],
            "decision_id": decision["id"],
            "memory_id": prior["memory_id"],
            "memory_appended": False,
            "application_id": prior["id"],
            "superseded": decision.get("supersede_memory_ids", []),
        }, indent=2, sort_keys=True))
        return

    current_conflicts = detect_conflicts(workspace, candidate, persist=True)
    current_ids = {row["id"] for row in current_conflicts}
    resolved = set(decision.get("resolved_conflict_ids", []))
    if current_ids - resolved:
        raise ai_context.ContextError("new or unresolved conflicts require another review decision")

    proposed = copy.deepcopy(candidate["proposed_memory"])
    proposed["approval"] = "approved"
    apply_retention_to_proposed(candidate, proposed, legacy.load_curation_policy(workspace))
    supersedes = sorted(set(candidate.get("supersedes", [])) | set(decision.get("supersede_memory_ids", [])))
    if supersedes and proposed.get("lifecycle", {}).get("supersedes") is None:
        proposed["lifecycle"]["supersedes"] = supersedes[0]

    memory_path = workspace / "memory" / "records.jsonl"
    existing_memory_rows = ai_context.read_jsonl(memory_path)
    existing_by_id = {
        row["id"]: row for row in existing_memory_rows if isinstance(row.get("id"), str)
    }
    memory = ai_context.normalize_candidate(proposed, ai_context.load_policy(workspace), existing_by_id)
    ai_context.validate_memory_record(memory, workspace, ai_context.load_policy(workspace))

    missing_targets = [old_id for old_id in supersedes if old_id not in existing_by_id]
    if missing_targets:
        raise ai_context.ContextError(f"superseded memory does not exist: {', '.join(missing_targets)}")
    if memory["id"] in supersedes:
        raise ai_context.ContextError("memory record cannot supersede itself")

    staged_memory, memory_added = _merge_unique(existing_memory_rows, [memory])
    memory_index = {row["id"]: index for index, row in enumerate(staged_memory)}
    mutations_path = legacy.curation_path(workspace, "mutations")
    supersession_path = legacy.curation_path(workspace, "supersession")
    existing_mutations = ai_context.read_jsonl(mutations_path)
    existing_edges = ai_context.read_jsonl(supersession_path)
    new_mutations: list[dict[str, Any]] = []
    new_edges: list[dict[str, Any]] = []
    reason = f"candidate application: {candidate['id']}"
    for old_id in supersedes:
        old_record = existing_by_id[old_id]
        updated, mutation, edge = _plan_supersession(
            workspace,
            memory,
            old_record,
            [*existing_mutations, *new_mutations],
            candidate_id=candidate["id"],
            decision_id=decision["id"],
            actor=actor,
            reason=reason,
        )
        staged_memory[memory_index[old_id]] = updated
        new_mutations.append(mutation)
        new_edges.append(edge)

    staged_mutations, _ = _merge_unique(existing_mutations, new_mutations)
    staged_edges, _ = _merge_unique(existing_edges, new_edges)
    semantic_key = candidate.get("semantic_key") or legacy.derive_semantic_key(proposed)
    app_core = {
        "protocol": "AI-CONTEXT/APPLICATION",
        "schema_version": legacy.CURATION_VERSION,
        "candidate_id": candidate["id"],
        "decision_id": decision["id"],
        "memory_id": memory["id"],
        "memory_sha256": legacy.canonical_sha(memory),
        "semantic_key": semantic_key,
        "source_refs": sorted(set(memory.get("source_refs", []))),
        "actor": actor,
    }
    application = {
        "id": legacy.stable_id("application", app_core),
        **app_core,
        "applied_at": ai_context.utc_now(),
    }
    staged_applications, _ = _merge_unique(existing_applications, [application])

    _atomic_jsonl_transaction({
        memory_path: staged_memory,
        mutations_path: staged_mutations,
        supersession_path: staged_edges,
        applications_path: staged_applications,
    })
    print(legacy.json.dumps({
        "candidate_id": candidate["id"],
        "decision_id": decision["id"],
        "memory_id": memory["id"],
        "memory_appended": bool(memory_added),
        "application_id": application["id"],
        "superseded": supersedes,
    }, indent=2, sort_keys=True))


legacy.ensure_curation = ensure_curation
legacy.validate_proposed_memory = validate_proposed_memory
legacy.apply_retention_to_proposed = apply_retention_to_proposed
legacy.detect_conflicts = detect_conflicts
legacy.append_supersession = append_supersession
legacy.cmd_apply = cmd_apply


def main() -> int:
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
