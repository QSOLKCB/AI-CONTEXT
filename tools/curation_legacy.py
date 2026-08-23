#!/usr/bin/env python3
"""Phase 5 curation engine for AI-CONTEXT.

Automated extractors and local LLMs may create candidate records only. Canonical memory
changes require an explicit human or policy-authority review/application command.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import ai_context

CURATION_VERSION = "0.1.0"
CURATION_FILES = {
    "candidates": "candidates.jsonl",
    "decisions": "decisions.jsonl",
    "conflicts": "conflicts.jsonl",
    "applications": "applications.jsonl",
    "mutations": "mutations.jsonl",
    "supersession": "supersession.jsonl",
    "tombstones": "tombstones.jsonl",
    "llm_suggestions": "llm-suggestions.jsonl",
}
DEFAULT_CURATION_POLICY = {
    "protocol": "AI-CONTEXT/CURATION-POLICY",
    "schema_version": CURATION_VERSION,
    "default_retention_days": None,
    "record_type_retention_days": {},
    "allow_policy_auto_expire": True,
}

ALLOWED_REVIEW_ACTORS = {"human", "policy"}
ALLOWED_DECISIONS = {"approve", "reject", "revise"}
CONFLICT_RESOLUTIONS = {"none", "coexist", "supersede_existing", "manual_merge"}
MUTABLE_FIELDS = {"confidence", "epistemic_state", "last_verified", "lifecycle"}


def curation_dir(workspace: Path) -> Path:
    return workspace / "curation"


def curation_path(workspace: Path, key: str) -> Path:
    return curation_dir(workspace) / CURATION_FILES[key]


def ensure_curation(workspace: Path) -> None:
    ai_context.ensure_workspace(workspace)
    root = curation_dir(workspace)
    root.mkdir(parents=True, exist_ok=True)
    policy_path = root / "policy.json"
    if not policy_path.exists():
        ai_context.write_json(policy_path, DEFAULT_CURATION_POLICY)


def load_curation_policy(workspace: Path) -> dict[str, Any]:
    ensure_curation(workspace)
    value = ai_context.load_json(curation_dir(workspace) / "policy.json")
    if not isinstance(value, dict):
        raise ai_context.ContextError("curation policy must be an object")
    if value.get("protocol") != "AI-CONTEXT/CURATION-POLICY":
        raise ai_context.ContextError("invalid curation policy protocol")
    if value.get("schema_version") != CURATION_VERSION:
        raise ai_context.ContextError("unsupported curation policy schema version")
    allowed = {
        "protocol", "schema_version", "default_retention_days",
        "record_type_retention_days", "allow_policy_auto_expire",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ai_context.ContextError(f"unknown curation policy fields: {', '.join(unknown)}")
    default_days = value.get("default_retention_days")
    if default_days is not None and (
        not isinstance(default_days, int) or isinstance(default_days, bool) or default_days < 0
    ):
        raise ai_context.ContextError("default_retention_days must be null or a non-negative integer")
    rules = value.get("record_type_retention_days")
    if not isinstance(rules, dict):
        raise ai_context.ContextError("record_type_retention_days must be an object")
    for record_type, days in rules.items():
        if record_type not in ai_context.RECORD_TYPES:
            raise ai_context.ContextError(f"unknown retention record_type: {record_type}")
        if days is not None and (
            not isinstance(days, int) or isinstance(days, bool) or days < 0
        ):
            raise ai_context.ContextError(f"retention for {record_type} must be null or non-negative integer")
    if not isinstance(value.get("allow_policy_auto_expire"), bool):
        raise ai_context.ContextError("allow_policy_auto_expire must be boolean")
    return value


def jsonl_map(path: Path) -> dict[str, dict[str, Any]]:
    return {
        row["id"]: row
        for row in ai_context.read_jsonl(path)
        if isinstance(row.get("id"), str)
    }


def canonical_sha(value: Any) -> str:
    return ai_context.sha256_bytes(ai_context.canonical_bytes(value))


def stable_id(prefix: str, core: dict[str, Any]) -> str:
    return f"{prefix}.sha256:{canonical_sha(core)}"


def atomic_replace_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(ai_context.canonical_bytes(row).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def parse_iso(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ai_context.ContextError("expected ISO-8601 timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise ai_context.ContextError(f"invalid timestamp: {value}") from exc


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def observation_map(workspace: Path) -> dict[str, dict[str, Any]]:
    return jsonl_map(workspace / "staging" / "observations.jsonl")


def content_map(workspace: Path) -> dict[str, dict[str, Any]]:
    return jsonl_map(workspace / "staging" / "content.jsonl")


def resolve_observation_payload(workspace: Path, obs: dict[str, Any]) -> str:
    content = obs.get("content")
    if not isinstance(content, dict):
        return ai_context.canonical_bytes(content).decode("utf-8")
    if isinstance(content.get("text"), str):
        return content["text"]
    content_id = content.get("content_id")
    if isinstance(content_id, str):
        stored = content_map(workspace).get(content_id)
        if stored and stored.get("content_kind") == "text" and isinstance(stored.get("text"), str):
            return stored["text"]
        return f"[binary/source content reference: {content_id}]"
    if "value" in content:
        return json.dumps(content["value"], sort_keys=True, ensure_ascii=False, allow_nan=False)
    return ai_context.canonical_bytes(content).decode("utf-8")


def proposed_memory_from_observations(
    workspace: Path,
    observation_ids: list[str],
    *,
    record_type: str,
    sensitivity: str | None,
    epistemic_state: str,
    confidence: float,
    tags: list[str],
    notes: str,
) -> dict[str, Any]:
    observations = observation_map(workspace)
    missing = [oid for oid in observation_ids if oid not in observations]
    if missing:
        raise ai_context.ContextError(f"unknown observation ids: {', '.join(missing)}")
    excerpts = [resolve_observation_payload(workspace, observations[oid]).strip() for oid in observation_ids]
    excerpts = [text for text in excerpts if text]
    content = {"text": "\n\n".join(excerpts)} if excerpts else {"source_observations": observation_ids}
    return {
        "record_type": record_type,
        "content": content,
        "sensitivity": sensitivity,
        "epistemic_state": epistemic_state,
        "confidence": confidence,
        "approval": "pending",
        "source_refs": observation_ids,
        "tags": tags,
        "lifecycle": {"state": "active", "expires_at": None, "supersedes": None},
        "notes": notes,
    }


def validate_proposed_memory(proposed: dict[str, Any]) -> None:
    if not isinstance(proposed, dict):
        raise ai_context.ContextError("proposed_memory must be an object")
    allowed = {
        "id", "record_type", "content", "sensitivity", "epistemic_state", "confidence",
        "approval", "source_refs", "tags", "lifecycle", "notes", "created_at", "last_verified",
    }
    unknown = sorted(set(proposed) - allowed)
    if unknown:
        raise ai_context.ContextError(f"unknown proposed memory fields: {', '.join(unknown)}")
    if proposed.get("record_type") not in ai_context.RECORD_TYPES:
        raise ai_context.ContextError(f"unknown proposed record_type: {proposed.get('record_type')}")
    if not isinstance(proposed.get("content"), dict):
        raise ai_context.ContextError("proposed memory content must be an object")
    sensitivity = proposed.get("sensitivity")
    if sensitivity is not None and sensitivity not in ai_context.SENSITIVITY_RANK:
        raise ai_context.ContextError(f"unknown proposed sensitivity: {sensitivity}")
    if proposed.get("epistemic_state") not in ai_context.EPISTEMIC_STATES:
        raise ai_context.ContextError(f"unknown proposed epistemic state: {proposed.get('epistemic_state')}")
    confidence = proposed.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise ai_context.ContextError("proposed confidence must be from 0 to 1")
    if proposed.get("approval") != "pending":
        raise ai_context.ContextError("candidate proposed memory approval must remain pending")
    refs = proposed.get("source_refs")
    if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
        raise ai_context.ContextError("candidate source_refs must be a string array")
    tags = proposed.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
        raise ai_context.ContextError("candidate tags must be a non-empty-string array")
    lifecycle = proposed.get("lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("state") != "active":
        raise ai_context.ContextError("candidate lifecycle must begin active")
    if ai_context.secret_hits(proposed):
        raise ai_context.ContextError("candidate contains secret-like material")


def candidate_core(
    proposed_memory: dict[str, Any],
    *,
    generator: dict[str, Any],
    semantic_key: str | None,
    supersedes: list[str],
    parent_candidate_id: str | None = None,
) -> dict[str, Any]:
    return {
        "protocol": "AI-CONTEXT/CANDIDATE",
        "schema_version": CURATION_VERSION,
        "generator": generator,
        "proposed_memory": proposed_memory,
        "semantic_key": semantic_key,
        "supersedes": sorted(set(supersedes)),
        "parent_candidate_id": parent_candidate_id,
    }


def create_candidate(
    workspace: Path,
    proposed_memory: dict[str, Any],
    *,
    generator: dict[str, Any],
    semantic_key: str | None,
    supersedes: list[str],
    parent_candidate_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    ensure_curation(workspace)
    validate_proposed_memory(proposed_memory)
    if semantic_key is not None and (not isinstance(semantic_key, str) or not semantic_key.strip()):
        raise ai_context.ContextError("semantic_key must be null or a non-empty string")
    core = candidate_core(
        proposed_memory,
        generator=generator,
        semantic_key=semantic_key,
        supersedes=supersedes,
        parent_candidate_id=parent_candidate_id,
    )
    cid = stable_id("candidate", core)
    path = curation_path(workspace, "candidates")
    existing = jsonl_map(path).get(cid)
    if existing:
        return existing, False
    row = {"id": cid, **core, "created_at": ai_context.utc_now()}
    ai_context.append_jsonl_unique(path, [row])
    detect_conflicts(workspace, row, persist=True)
    return row, True


def derive_semantic_key(record_or_proposed: dict[str, Any]) -> str | None:
    content = record_or_proposed.get("content")
    record_type = record_or_proposed.get("record_type")
    if not isinstance(content, dict) or not isinstance(record_type, str):
        return None
    for key in ("memory_key", "key", "subject", "project", "name", "id"):
        value = content.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value):
            return f"{record_type}:{key}:{value}"
    return None


def application_semantic_keys(workspace: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in ai_context.read_jsonl(curation_path(workspace, "applications")):
        memory_id = row.get("memory_id")
        semantic_key = row.get("semantic_key")
        if isinstance(memory_id, str) and isinstance(semantic_key, str) and semantic_key:
            result[memory_id] = semantic_key
    return result


def detect_conflicts(workspace: Path, candidate: dict[str, Any], *, persist: bool) -> list[dict[str, Any]]:
    proposed = candidate["proposed_memory"]
    semantic_key = candidate.get("semantic_key") or derive_semantic_key(proposed)
    if not semantic_key:
        return []
    app_keys = application_semantic_keys(workspace)
    rows: list[dict[str, Any]] = []
    existing_conflicts = jsonl_map(curation_path(workspace, "conflicts"))
    for memory in ai_context.read_jsonl(workspace / "memory" / "records.jsonl"):
        if memory.get("record_type") != proposed.get("record_type"):
            continue
        if memory.get("lifecycle", {}).get("state") != "active":
            continue
        memory_key = app_keys.get(memory.get("id")) or derive_semantic_key(memory)
        if memory_key != semantic_key:
            continue
        if ai_context.canonical_bytes(memory.get("content")) == ai_context.canonical_bytes(proposed.get("content")):
            continue
        core = {
            "protocol": "AI-CONTEXT/CONFLICT",
            "schema_version": CURATION_VERSION,
            "candidate_id": candidate["id"],
            "memory_id": memory["id"],
            "semantic_key": semantic_key,
            "candidate_content_sha256": canonical_sha(proposed.get("content")),
            "memory_content_sha256": canonical_sha(memory.get("content")),
        }
        conflict_id = stable_id("conflict", core)
        rows.append(existing_conflicts.get(conflict_id) or {"id": conflict_id, **core, "created_at": ai_context.utc_now()})
    if persist and rows:
        ai_context.append_jsonl_unique(curation_path(workspace, "conflicts"), rows)
    return rows


def decision_rows(workspace: Path, candidate_id: str | None = None) -> list[dict[str, Any]]:
    rows = ai_context.read_jsonl(curation_path(workspace, "decisions"))
    return [row for row in rows if row.get("candidate_id") == candidate_id] if candidate_id else rows


def latest_decision(workspace: Path, candidate_id: str) -> dict[str, Any] | None:
    rows = decision_rows(workspace, candidate_id)
    return max(rows, key=lambda row: int(row.get("revision", 0))) if rows else None


def candidate_status(workspace: Path, candidate_id: str) -> str:
    decision = latest_decision(workspace, candidate_id)
    if decision is None:
        return "pending"
    if decision["decision"] == "approve":
        apps = [
            row for row in ai_context.read_jsonl(curation_path(workspace, "applications"))
            if row.get("candidate_id") == candidate_id and row.get("decision_id") == decision["id"]
        ]
        return "applied" if apps else "approved"
    return decision["decision"]


def retention_days_for(policy: dict[str, Any], record_type: str) -> int | None:
    rules = policy.get("record_type_retention_days", {})
    return rules[record_type] if record_type in rules else policy.get("default_retention_days")


def apply_retention_to_proposed(candidate: dict[str, Any], proposed: dict[str, Any], policy: dict[str, Any]) -> None:
    lifecycle = copy.deepcopy(proposed.get("lifecycle") or {})
    if lifecycle.get("expires_at") is None:
        days = retention_days_for(policy, proposed["record_type"])
        if days is not None:
            lifecycle["expires_at"] = format_utc(parse_iso(candidate["created_at"]) + timedelta(days=days))
    proposed["lifecycle"] = lifecycle


def record_map(workspace: Path) -> dict[str, dict[str, Any]]:
    return jsonl_map(workspace / "memory" / "records.jsonl")


def mutation_rows(workspace: Path, memory_id: str | None = None) -> list[dict[str, Any]]:
    rows = ai_context.read_jsonl(curation_path(workspace, "mutations"))
    return [row for row in rows if row.get("memory_id") == memory_id] if memory_id else rows


def mutation_satisfies(record: dict[str, Any], patch: dict[str, Any]) -> bool:
    return all(ai_context.canonical_bytes(record.get(key)) == ai_context.canonical_bytes(value) for key, value in patch.items())


def apply_patch(record: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(patch) - MUTABLE_FIELDS)
    if unknown:
        raise ai_context.ContextError(f"curation mutation cannot change fields: {', '.join(unknown)}")
    updated = copy.deepcopy(record)
    for key, value in patch.items():
        updated[key] = copy.deepcopy(value)
    return updated


def append_mutation(
    workspace: Path,
    memory_id: str,
    *,
    kind: str,
    patch: dict[str, Any],
    actor_type: str,
    actor_label: str,
    reason: str,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    ensure_curation(workspace)
    if actor_type not in ALLOWED_REVIEW_ACTORS:
        raise ai_context.ContextError("canonical mutation actor must be human or policy")
    memory_path = workspace / "memory" / "records.jsonl"
    rows = ai_context.read_jsonl(memory_path)
    index = next((i for i, row in enumerate(rows) if row.get("id") == memory_id), None)
    if index is None:
        raise ai_context.ContextError(f"unknown memory record: {memory_id}")
    current = rows[index]
    if mutation_satisfies(current, patch):
        matches = [
            row for row in mutation_rows(workspace, memory_id)
            if row.get("kind") == kind and row.get("after_sha256") == canonical_sha(current)
        ]
        if matches:
            return max(matches, key=lambda row: int(row.get("revision", 0)))
    updated = apply_patch(current, patch)
    ai_context.validate_memory_record(updated, workspace, ai_context.load_policy(workspace))
    refs = sorted(set(evidence_refs or []))
    observations = observation_map(workspace)
    missing = [ref for ref in refs if ref not in observations]
    if missing:
        raise ai_context.ContextError(f"mutation evidence refs not found: {', '.join(missing)}")
    previous_rows = mutation_rows(workspace, memory_id)
    previous = max(previous_rows, key=lambda row: int(row.get("revision", 0))) if previous_rows else None
    revision = 1 if previous is None else int(previous["revision"]) + 1
    core = {
        "protocol": "AI-CONTEXT/CURATION-MUTATION",
        "schema_version": CURATION_VERSION,
        "memory_id": memory_id,
        "revision": revision,
        "previous_mutation_id": previous["id"] if previous else None,
        "kind": kind,
        "patch": patch,
        "before_sha256": canonical_sha(current),
        "after_sha256": canonical_sha(updated),
        "actor": {"type": actor_type, "label": actor_label},
        "reason": reason,
        "evidence_refs": refs,
        "created_at": ai_context.utc_now(),
    }
    mutation = {"id": stable_id("mutation", core), **core}
    rows[index] = updated
    atomic_replace_jsonl(memory_path, rows)
    ai_context.append_jsonl_unique(curation_path(workspace, "mutations"), [mutation])
    return mutation


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
    records = record_map(workspace)
    if new_memory_id not in records:
        raise ai_context.ContextError(f"superseding memory does not exist: {new_memory_id}")
    old = records.get(old_memory_id)
    if not old:
        raise ai_context.ContextError(f"superseded memory does not exist: {old_memory_id}")
    lifecycle = copy.deepcopy(old.get("lifecycle") or {})
    lifecycle["state"] = "superseded"
    mutation = append_mutation(
        workspace, old_memory_id, kind="supersede", patch={"lifecycle": lifecycle},
        actor_type=actor_type, actor_label=actor_label, reason=reason,
    )
    core = {
        "protocol": "AI-CONTEXT/SUPERSESSION",
        "schema_version": CURATION_VERSION,
        "from_memory_id": old_memory_id,
        "to_memory_id": new_memory_id,
        "candidate_id": candidate_id,
        "decision_id": decision_id,
        "mutation_id": mutation["id"],
        "reason": reason,
    }
    edge_id = stable_id("supersession", core)
    existing = jsonl_map(curation_path(workspace, "supersession")).get(edge_id)
    if existing:
        return existing
    edge = {"id": edge_id, **core, "created_at": ai_context.utc_now()}
    ai_context.append_jsonl_unique(curation_path(workspace, "supersession"), [edge])
    return edge


def cmd_init(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    print(json.dumps({"status": "ok", "curation_dir": str(curation_dir(workspace))}, indent=2, sort_keys=True))


def cmd_propose(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    tags = sorted({tag.strip() for tag in args.tags.split(",") if tag.strip()})
    observation_ids = list(dict.fromkeys(args.observation or []))
    if args.content_file:
        content = ai_context.load_json(Path(args.content_file).expanduser().resolve())
        if not isinstance(content, dict):
            raise ai_context.ContextError("--content-file must contain a JSON object")
        proposed = {
            "record_type": args.record_type, "content": content, "sensitivity": args.sensitivity,
            "epistemic_state": args.epistemic_state, "confidence": args.confidence,
            "approval": "pending", "source_refs": observation_ids, "tags": tags,
            "lifecycle": {"state": "active", "expires_at": None, "supersedes": None},
            "notes": args.notes,
        }
    else:
        if not observation_ids:
            raise ai_context.ContextError("proposal requires --observation or --content-file")
        proposed = proposed_memory_from_observations(
            workspace, observation_ids, record_type=args.record_type, sensitivity=args.sensitivity,
            epistemic_state=args.epistemic_state, confidence=args.confidence, tags=tags, notes=args.notes,
        )
    if proposed["sensitivity"] is None:
        proposed["sensitivity"] = ai_context.load_policy(workspace)["default_sensitivity"]
    candidate, appended = create_candidate(
        workspace, proposed,
        generator={"type": "deterministic", "id": "observation-proposal-v1", "version": CURATION_VERSION},
        semantic_key=args.semantic_key, supersedes=args.supersedes or [],
    )
    print(json.dumps({
        "candidate_id": candidate["id"], "appended": appended,
        "status": candidate_status(workspace, candidate["id"]),
        "conflicts": [row["id"] for row in detect_conflicts(workspace, candidate, persist=True)],
        "canonical_memory_changed": False,
    }, indent=2, sort_keys=True))


def cmd_queue(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    out = []
    for candidate in ai_context.read_jsonl(curation_path(workspace, "candidates")):
        status = candidate_status(workspace, candidate["id"])
        if args.status != "all" and status != args.status:
            continue
        out.append({
            "candidate_id": candidate["id"], "status": status,
            "record_type": candidate["proposed_memory"]["record_type"],
            "semantic_key": candidate.get("semantic_key") or derive_semantic_key(candidate["proposed_memory"]),
            "source_refs": candidate["proposed_memory"].get("source_refs", []),
            "created_at": candidate.get("created_at"), "generator": candidate.get("generator"),
        })
    print(json.dumps({"count": len(out), "candidates": out}, indent=2, sort_keys=True))


def cmd_review(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    candidate = jsonl_map(curation_path(workspace, "candidates")).get(args.candidate)
    if not candidate:
        raise ai_context.ContextError(f"unknown candidate: {args.candidate}")
    conflicts = detect_conflicts(workspace, candidate, persist=True)
    if args.decision == "approve" and conflicts and args.conflict_resolution == "none":
        raise ai_context.ContextError("approval with open conflicts requires an explicit conflict resolution")
    previous = latest_decision(workspace, candidate["id"])
    revision = 1 if previous is None else int(previous["revision"]) + 1
    supersede_ids = sorted(set(args.supersede or []))
    if args.conflict_resolution == "supersede_existing":
        supersede_ids = sorted(set(supersede_ids) | {row["memory_id"] for row in conflicts})
    core = {
        "protocol": "AI-CONTEXT/REVIEW-DECISION", "schema_version": CURATION_VERSION,
        "candidate_id": candidate["id"], "candidate_sha256": canonical_sha(candidate),
        "revision": revision, "previous_decision_id": previous["id"] if previous else None,
        "decision": args.decision, "actor": {"type": args.actor_type, "label": args.actor_label},
        "conflict_resolution": args.conflict_resolution,
        "resolved_conflict_ids": sorted(row["id"] for row in conflicts) if args.conflict_resolution != "none" else [],
        "supersede_memory_ids": supersede_ids, "notes": args.notes, "created_at": ai_context.utc_now(),
    }
    row = {"id": stable_id("decision", core), **core}
    ai_context.append_jsonl_unique(curation_path(workspace, "decisions"), [row])
    print(json.dumps({"decision_id": row["id"], "candidate_id": candidate["id"], "decision": args.decision, "revision": revision}, indent=2, sort_keys=True))


def cmd_apply(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    candidate = jsonl_map(curation_path(workspace, "candidates")).get(args.candidate)
    if not candidate:
        raise ai_context.ContextError(f"unknown candidate: {args.candidate}")
    decision = latest_decision(workspace, candidate["id"])
    if not decision or decision.get("decision") != "approve":
        raise ai_context.ContextError("candidate requires a latest explicit approve decision")
    actor = decision.get("actor")
    if not isinstance(actor, dict) or actor.get("type") not in ALLOWED_REVIEW_ACTORS:
        raise ai_context.ContextError("approved candidate lacks human/policy authority")
    current_conflicts = detect_conflicts(workspace, candidate, persist=True)
    current_ids = {row["id"] for row in current_conflicts}
    resolved = set(decision.get("resolved_conflict_ids", []))
    if current_ids - resolved and decision.get("conflict_resolution") != "coexist":
        raise ai_context.ContextError("new or unresolved conflicts require another review decision")
    proposed = copy.deepcopy(candidate["proposed_memory"])
    proposed["approval"] = "approved"
    apply_retention_to_proposed(candidate, proposed, load_curation_policy(workspace))
    supersedes = sorted(set(candidate.get("supersedes", [])) | set(decision.get("supersede_memory_ids", [])))
    if supersedes and proposed.get("lifecycle", {}).get("supersedes") is None:
        proposed["lifecycle"]["supersedes"] = supersedes[0]
    memory_path = workspace / "memory" / "records.jsonl"
    existing_rows = ai_context.read_jsonl(memory_path)
    existing_by_id = {row["id"]: row for row in existing_rows if isinstance(row.get("id"), str)}
    memory = ai_context.normalize_candidate(proposed, ai_context.load_policy(workspace), existing_by_id)
    ai_context.validate_memory_record(memory, workspace, ai_context.load_policy(workspace))
    appended = ai_context.append_jsonl_unique(memory_path, [memory])
    for old_id in supersedes:
        if old_id == memory["id"]:
            raise ai_context.ContextError("memory record cannot supersede itself")
        append_supersession(
            workspace, old_id, memory["id"], candidate_id=candidate["id"], decision_id=decision["id"],
            actor_type=actor["type"], actor_label=actor["label"], reason=f"candidate application: {candidate['id']}",
        )
    semantic_key = candidate.get("semantic_key") or derive_semantic_key(proposed)
    core = {
        "protocol": "AI-CONTEXT/APPLICATION", "schema_version": CURATION_VERSION,
        "candidate_id": candidate["id"], "decision_id": decision["id"], "memory_id": memory["id"],
        "memory_sha256": canonical_sha(record_map(workspace)[memory["id"]]), "semantic_key": semantic_key,
        "source_refs": sorted(set(memory.get("source_refs", []))), "actor": actor,
    }
    app_id = stable_id("application", core)
    application = jsonl_map(curation_path(workspace, "applications")).get(app_id) or {"id": app_id, **core, "applied_at": ai_context.utc_now()}
    ai_context.append_jsonl_unique(curation_path(workspace, "applications"), [application])
    print(json.dumps({
        "candidate_id": candidate["id"], "decision_id": decision["id"], "memory_id": memory["id"],
        "memory_appended": bool(appended), "application_id": application["id"], "superseded": supersedes,
    }, indent=2, sort_keys=True))


def cmd_conflicts(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    candidate = jsonl_map(curation_path(workspace, "candidates")).get(args.candidate)
    if not candidate:
        raise ai_context.ContextError(f"unknown candidate: {args.candidate}")
    print(json.dumps({"candidate_id": args.candidate, "conflicts": detect_conflicts(workspace, candidate, persist=True)}, indent=2, sort_keys=True))


def cmd_confidence(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    if not 0 <= args.confidence <= 1:
        raise ai_context.ContextError("confidence must be from 0 to 1")
    patch: dict[str, Any] = {"confidence": args.confidence}
    if args.epistemic_state:
        patch["epistemic_state"] = args.epistemic_state
    mutation = append_mutation(
        workspace, args.memory, kind="confidence_update", patch=patch,
        actor_type=args.actor_type, actor_label=args.actor_label, reason=args.reason,
        evidence_refs=args.evidence or [],
    )
    print(json.dumps({"mutation_id": mutation["id"], "memory_id": args.memory}, indent=2, sort_keys=True))


def cmd_verify(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    if not args.evidence:
        raise ai_context.ContextError("verification requires at least one --evidence observation")
    if not 0 <= args.confidence <= 1:
        raise ai_context.ContextError("confidence must be from 0 to 1")
    mutation = append_mutation(
        workspace, args.memory, kind="verification",
        patch={"confidence": args.confidence, "epistemic_state": "verified", "last_verified": ai_context.utc_now()},
        actor_type=args.actor_type, actor_label=args.actor_label, reason=args.reason, evidence_refs=args.evidence,
    )
    print(json.dumps({"mutation_id": mutation["id"], "memory_id": args.memory}, indent=2, sort_keys=True))


def cmd_supersede(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    edge = append_supersession(
        workspace, args.old, args.new, candidate_id=None, decision_id=None,
        actor_type=args.actor_type, actor_label=args.actor_label, reason=args.reason,
    )
    print(json.dumps({"supersession_id": edge["id"], "from": args.old, "to": args.new}, indent=2, sort_keys=True))


def cmd_tombstone(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    record = record_map(workspace).get(args.memory)
    if not record:
        raise ai_context.ContextError(f"unknown memory record: {args.memory}")
    existing_receipts = [row for row in ai_context.read_jsonl(curation_path(workspace, "tombstones")) if row.get("memory_id") == args.memory]
    if record.get("lifecycle", {}).get("state") == "tombstoned" and existing_receipts:
        receipt = max(existing_receipts, key=lambda row: row.get("created_at", ""))
        print(json.dumps({"tombstone_id": receipt["id"], "memory_id": args.memory, "already_tombstoned": True}, indent=2))
        return
    lifecycle = copy.deepcopy(record.get("lifecycle") or {})
    lifecycle["state"] = "tombstoned"
    mutation = append_mutation(
        workspace, args.memory, kind="tombstone", patch={"lifecycle": lifecycle},
        actor_type=args.actor_type, actor_label=args.actor_label, reason=args.reason,
        evidence_refs=args.evidence or [],
    )
    core = {
        "protocol": "AI-CONTEXT/TOMBSTONE", "schema_version": CURATION_VERSION,
        "memory_id": args.memory, "mutation_id": mutation["id"], "reason": args.reason,
        "actor": {"type": args.actor_type, "label": args.actor_label},
        "before_sha256": mutation["before_sha256"], "after_sha256": mutation["after_sha256"],
    }
    tid = stable_id("tombstone", core)
    receipt = jsonl_map(curation_path(workspace, "tombstones")).get(tid) or {"id": tid, **core, "created_at": ai_context.utc_now()}
    ai_context.append_jsonl_unique(curation_path(workspace, "tombstones"), [receipt])
    print(json.dumps({"tombstone_id": receipt["id"], "memory_id": args.memory}, indent=2, sort_keys=True))


def cmd_set_retention(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    policy = load_curation_policy(workspace)
    if args.default_days is not None:
        policy["default_retention_days"] = None if args.default_days == "none" else int(args.default_days)
        if policy["default_retention_days"] is not None and policy["default_retention_days"] < 0:
            raise ai_context.ContextError("retention days cannot be negative")
    rules = dict(policy.get("record_type_retention_days", {}))
    for rule in args.rule or []:
        if "=" not in rule:
            raise ai_context.ContextError("retention --rule must be record_type=days|none")
        record_type, raw = rule.split("=", 1)
        if record_type not in ai_context.RECORD_TYPES:
            raise ai_context.ContextError(f"unknown record_type in retention rule: {record_type}")
        days = None if raw == "none" else int(raw)
        if days is not None and days < 0:
            raise ai_context.ContextError("retention days cannot be negative")
        rules[record_type] = days
    policy["record_type_retention_days"] = rules
    ai_context.write_json(curation_dir(workspace) / "policy.json", policy)
    print(json.dumps(policy, indent=2, sort_keys=True))


def cmd_enforce_retention(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    policy = load_curation_policy(workspace)
    if not policy.get("allow_policy_auto_expire"):
        raise ai_context.ContextError("curation policy forbids automatic expiry enforcement")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assigned = 0
    expired = 0
    for memory in list(record_map(workspace).values()):
        if memory.get("lifecycle", {}).get("state") != "active":
            continue
        lifecycle = copy.deepcopy(memory.get("lifecycle") or {})
        expiry = lifecycle.get("expires_at")
        if expiry is None:
            days = retention_days_for(policy, memory["record_type"])
            if days is not None:
                created = parse_iso(memory.get("created_at") or ai_context.utc_now())
                expiry = format_utc(created + timedelta(days=days))
                lifecycle["expires_at"] = expiry
                append_mutation(
                    workspace, memory["id"], kind="retention_set", patch={"lifecycle": lifecycle},
                    actor_type="policy", actor_label="retention-enforcer", reason="curation retention policy",
                )
                assigned += 1
        if expiry is not None and parse_iso(expiry) <= now:
            current = record_map(workspace)[memory["id"]]
            current_lifecycle = copy.deepcopy(current.get("lifecycle") or {})
            if current_lifecycle.get("state") == "active":
                current_lifecycle["state"] = "expired"
                append_mutation(
                    workspace, memory["id"], kind="expire", patch={"lifecycle": current_lifecycle},
                    actor_type="policy", actor_label="retention-enforcer", reason="retention expiry reached",
                )
                expired += 1
    print(json.dumps({"retention_assigned": assigned, "expired": expired}, indent=2, sort_keys=True))


def cmd_llm_request(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    candidate = jsonl_map(curation_path(workspace, "candidates")).get(args.candidate)
    if not candidate:
        raise ai_context.ContextError(f"unknown candidate: {args.candidate}")
    observations = observation_map(workspace)
    evidence = [observations[ref] for ref in candidate["proposed_memory"].get("source_refs", []) if ref in observations]
    request = {
        "protocol": "AI-CONTEXT/CURATOR-REQUEST", "schema_version": CURATION_VERSION,
        "candidate_id": candidate["id"], "candidate": candidate, "evidence": evidence,
        "constraints": [
            "advisory_only", "no_canonical_memory_write_authority",
            "do_not_invent_provenance", "unknown_is_preferred_to_plausible_invention",
        ],
    }
    output = Path(args.output).expanduser().resolve()
    ai_context.write_json(output, request)
    print(json.dumps({"output": str(output), "candidate_id": candidate["id"]}, indent=2, sort_keys=True))


def validate_llm_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ai_context.ContextError("curator response must be an object")
    allowed = {
        "protocol", "schema_version", "candidate_id", "recommendation", "rationale",
        "model", "proposed_memory", "semantic_key",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ai_context.ContextError(f"unknown curator response fields: {', '.join(unknown)}")
    if value.get("protocol") != "AI-CONTEXT/CURATOR-RESPONSE" or value.get("schema_version") != CURATION_VERSION:
        raise ai_context.ContextError("unsupported curator response protocol/schema")
    if value.get("recommendation") not in {"approve", "reject", "revise", "unknown"}:
        raise ai_context.ContextError("invalid curator recommendation")
    if not isinstance(value.get("candidate_id"), str) or not isinstance(value.get("rationale"), str):
        raise ai_context.ContextError("curator response requires candidate_id and rationale")
    model = value.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not isinstance(model.get("runtime"), str):
        raise ai_context.ContextError("curator response model must contain id and runtime")
    if "proposed_memory" in value:
        validate_proposed_memory(value["proposed_memory"])
    if value.get("semantic_key") is not None and not isinstance(value.get("semantic_key"), str):
        raise ai_context.ContextError("curator semantic_key must be string or null")
    return value


def cmd_llm_import(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_curation(workspace)
    response = validate_llm_response(ai_context.load_json(Path(args.input).expanduser().resolve()))
    base = jsonl_map(curation_path(workspace, "candidates")).get(response["candidate_id"])
    if not base:
        raise ai_context.ContextError(f"curator response references unknown candidate: {response['candidate_id']}")
    core = {
        "protocol": "AI-CONTEXT/LLM-SUGGESTION", "schema_version": CURATION_VERSION,
        "candidate_id": base["id"], "candidate_sha256": canonical_sha(base),
        "recommendation": response["recommendation"], "rationale": response["rationale"],
        "model": response["model"], "semantic_key": response.get("semantic_key"),
        "proposed_memory_sha256": canonical_sha(response["proposed_memory"]) if "proposed_memory" in response else None,
    }
    sid = stable_id("llm-suggestion", core)
    suggestion = jsonl_map(curation_path(workspace, "llm_suggestions")).get(sid) or {"id": sid, **core, "created_at": ai_context.utc_now()}
    ai_context.append_jsonl_unique(curation_path(workspace, "llm_suggestions"), [suggestion])
    revised_id = None
    if args.create_candidate and "proposed_memory" in response:
        revised, _ = create_candidate(
            workspace, response["proposed_memory"],
            generator={"type": "local_llm", "id": response["model"]["id"], "version": CURATION_VERSION, "runtime": response["model"]["runtime"]},
            semantic_key=response.get("semantic_key"), supersedes=base.get("supersedes", []), parent_candidate_id=base["id"],
        )
        revised_id = revised["id"]
    print(json.dumps({
        "suggestion_id": suggestion["id"], "recommendation": suggestion["recommendation"],
        "revised_candidate_id": revised_id, "canonical_memory_changed": False,
    }, indent=2, sort_keys=True))


def gather_explanation(workspace: Path, memory_id: str) -> dict[str, Any]:
    ensure_curation(workspace)
    memory = record_map(workspace).get(memory_id)
    if not memory:
        raise ai_context.ContextError(f"unknown memory record: {memory_id}")
    applications = [row for row in ai_context.read_jsonl(curation_path(workspace, "applications")) if row.get("memory_id") == memory_id]
    candidate_ids = {row.get("candidate_id") for row in applications if isinstance(row.get("candidate_id"), str)}
    candidates = [row for row in ai_context.read_jsonl(curation_path(workspace, "candidates")) if row.get("id") in candidate_ids]
    decision_ids = {row.get("decision_id") for row in applications if isinstance(row.get("decision_id"), str)}
    decisions = [row for row in ai_context.read_jsonl(curation_path(workspace, "decisions")) if row.get("id") in decision_ids]
    observations = observation_map(workspace)
    receipts = jsonl_map(workspace / "receipts" / "imports.jsonl")
    snapshots = jsonl_map(workspace / "receipts" / "source-snapshots.jsonl")
    contents = content_map(workspace)
    source_chain = []
    for ref in memory.get("source_refs", []):
        obs = observations.get(ref)
        if not obs:
            continue
        receipt = receipts.get(obs.get("source_receipt_id"))
        snapshot = snapshots.get(receipt.get("source_snapshot_id")) if receipt and isinstance(receipt.get("source_snapshot_id"), str) else None
        content_obj = contents.get(obs.get("content", {}).get("content_id")) if isinstance(obs.get("content"), dict) else None
        source_chain.append({"observation": obs, "import_receipt": receipt, "source_snapshot": snapshot, "content_object": content_obj})
    return {
        "memory": memory, "applications": applications, "candidates": candidates, "decisions": decisions,
        "mutations": mutation_rows(workspace, memory_id),
        "supersession_in": [row for row in ai_context.read_jsonl(curation_path(workspace, "supersession")) if row.get("to_memory_id") == memory_id],
        "supersession_out": [row for row in ai_context.read_jsonl(curation_path(workspace, "supersession")) if row.get("from_memory_id") == memory_id],
        "tombstones": [row for row in ai_context.read_jsonl(curation_path(workspace, "tombstones")) if row.get("memory_id") == memory_id],
        "source_chain": source_chain,
    }


def cmd_explain(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    explanation = gather_explanation(workspace, args.memory)
    if args.format == "json":
        print(json.dumps(explanation, indent=2, sort_keys=True, ensure_ascii=False))
        return
    memory = explanation["memory"]
    lines = [
        f"Memory: {memory['id']}", f"Type: {memory['record_type']}", f"State: {memory['lifecycle']['state']}",
        f"Epistemic state: {memory['epistemic_state']}", f"Confidence: {memory['confidence']}",
        f"Source observations: {len(memory.get('source_refs', []))}", f"Curation applications: {len(explanation['applications'])}",
        f"Mutations: {len(explanation['mutations'])}",
    ]
    if explanation["applications"]:
        app = explanation["applications"][-1]
        lines.append(f"Remembered through candidate {app['candidate_id']} approved by decision {app['decision_id']}.")
    else:
        lines.append("No Phase 5 application receipt exists; this memory predates or bypasses the curation application path.")
    for chain in explanation["source_chain"]:
        obs = chain["observation"]
        path = obs.get("metadata", {}).get("source_path")
        lines.append(f"- Evidence {obs['id']} ({obs.get('kind')})" + (f" at {path}" if path else ""))
    print("\n".join(lines))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 5 curation engine")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("workspace"); init.set_defaults(func=cmd_init)
    propose = sub.add_parser("propose"); propose.add_argument("workspace"); propose.add_argument("--observation", action="append"); propose.add_argument("--content-file"); propose.add_argument("--record-type", default="claim", choices=sorted(ai_context.RECORD_TYPES)); propose.add_argument("--sensitivity", choices=["public", "private", "restricted"]); propose.add_argument("--epistemic-state", default="parsed", choices=sorted(ai_context.EPISTEMIC_STATES)); propose.add_argument("--confidence", type=float, default=0.5); propose.add_argument("--tags", default=""); propose.add_argument("--semantic-key"); propose.add_argument("--supersedes", action="append"); propose.add_argument("--notes", default=""); propose.set_defaults(func=cmd_propose)
    queue = sub.add_parser("queue"); queue.add_argument("workspace"); queue.add_argument("--status", default="pending", choices=["pending", "approved", "applied", "reject", "revise", "all"]); queue.set_defaults(func=cmd_queue)
    review = sub.add_parser("review"); review.add_argument("workspace"); review.add_argument("--candidate", required=True); review.add_argument("--decision", required=True, choices=sorted(ALLOWED_DECISIONS)); review.add_argument("--actor-type", default="human", choices=sorted(ALLOWED_REVIEW_ACTORS)); review.add_argument("--actor-label", default="local-user"); review.add_argument("--conflict-resolution", default="none", choices=sorted(CONFLICT_RESOLUTIONS)); review.add_argument("--supersede", action="append"); review.add_argument("--notes", default=""); review.set_defaults(func=cmd_review)
    apply = sub.add_parser("apply"); apply.add_argument("workspace"); apply.add_argument("--candidate", required=True); apply.set_defaults(func=cmd_apply)
    conflicts = sub.add_parser("conflicts"); conflicts.add_argument("workspace"); conflicts.add_argument("--candidate", required=True); conflicts.set_defaults(func=cmd_conflicts)
    confidence = sub.add_parser("confidence"); confidence.add_argument("workspace"); confidence.add_argument("--memory", required=True); confidence.add_argument("--confidence", type=float, required=True); confidence.add_argument("--epistemic-state", choices=sorted(ai_context.EPISTEMIC_STATES)); confidence.add_argument("--evidence", action="append"); confidence.add_argument("--actor-type", default="human", choices=sorted(ALLOWED_REVIEW_ACTORS)); confidence.add_argument("--actor-label", default="local-user"); confidence.add_argument("--reason", required=True); confidence.set_defaults(func=cmd_confidence)
    verify = sub.add_parser("verify"); verify.add_argument("workspace"); verify.add_argument("--memory", required=True); verify.add_argument("--confidence", type=float, required=True); verify.add_argument("--evidence", action="append", required=True); verify.add_argument("--actor-type", default="human", choices=sorted(ALLOWED_REVIEW_ACTORS)); verify.add_argument("--actor-label", default="local-user"); verify.add_argument("--reason", required=True); verify.set_defaults(func=cmd_verify)
    supersede = sub.add_parser("supersede"); supersede.add_argument("workspace"); supersede.add_argument("--old", required=True); supersede.add_argument("--new", required=True); supersede.add_argument("--actor-type", default="human", choices=sorted(ALLOWED_REVIEW_ACTORS)); supersede.add_argument("--actor-label", default="local-user"); supersede.add_argument("--reason", required=True); supersede.set_defaults(func=cmd_supersede)
    tombstone = sub.add_parser("tombstone"); tombstone.add_argument("workspace"); tombstone.add_argument("--memory", required=True); tombstone.add_argument("--reason", required=True); tombstone.add_argument("--evidence", action="append"); tombstone.add_argument("--actor-type", default="human", choices=sorted(ALLOWED_REVIEW_ACTORS)); tombstone.add_argument("--actor-label", default="local-user"); tombstone.set_defaults(func=cmd_tombstone)
    retention = sub.add_parser("retention-policy"); retention.add_argument("workspace"); retention.add_argument("--default-days"); retention.add_argument("--rule", action="append"); retention.set_defaults(func=cmd_set_retention)
    enforce = sub.add_parser("enforce-retention"); enforce.add_argument("workspace"); enforce.set_defaults(func=cmd_enforce_retention)
    llm_request = sub.add_parser("llm-request"); llm_request.add_argument("workspace"); llm_request.add_argument("--candidate", required=True); llm_request.add_argument("--output", required=True); llm_request.set_defaults(func=cmd_llm_request)
    llm_import = sub.add_parser("llm-import"); llm_import.add_argument("workspace"); llm_import.add_argument("--input", required=True); llm_import.add_argument("--create-candidate", action="store_true"); llm_import.set_defaults(func=cmd_llm_import)
    explain = sub.add_parser("explain"); explain.add_argument("workspace"); explain.add_argument("--memory", required=True); explain.add_argument("--format", default="text", choices=["text", "json"]); explain.set_defaults(func=cmd_explain)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ai_context.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
