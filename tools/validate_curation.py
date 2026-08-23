#!/usr/bin/env python3
"""Validate AI-CONTEXT Phase 5 curation state and authority chains."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import ai_context
import curation


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ai_context.ContextError(message)


def rows_by_id(path: Path) -> dict[str, dict[str, Any]]:
    rows = ai_context.read_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("id")
        require(isinstance(rid, str) and rid, f"row missing id in {path}")
        require(rid not in result, f"duplicate id in {path}: {rid}")
        result[rid] = row
    return result


def candidate_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key not in {"id", "created_at"}}
    return curation.stable_id("candidate", core)


def conflict_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key not in {"id", "created_at"}}
    return curation.stable_id("conflict", core)


def decision_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key != "id"}
    return curation.stable_id("decision", core)


def mutation_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key != "id"}
    return curation.stable_id("mutation", core)


def application_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key not in {"id", "applied_at"}}
    return curation.stable_id("application", core)


def supersession_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key not in {"id", "created_at"}}
    return curation.stable_id("supersession", core)


def tombstone_expected_id(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key not in {"id", "created_at"}}
    return curation.stable_id("tombstone", core)


def cmd_validate(workspace: Path) -> dict[str, Any]:
    curation.ensure_curation(workspace)
    curation.load_curation_policy(workspace)

    candidates = rows_by_id(curation.curation_path(workspace, "candidates"))
    decisions = rows_by_id(curation.curation_path(workspace, "decisions"))
    conflicts = rows_by_id(curation.curation_path(workspace, "conflicts"))
    applications = rows_by_id(curation.curation_path(workspace, "applications"))
    mutations = rows_by_id(curation.curation_path(workspace, "mutations"))
    supersessions = rows_by_id(curation.curation_path(workspace, "supersession"))
    tombstones = rows_by_id(curation.curation_path(workspace, "tombstones"))
    suggestions = rows_by_id(curation.curation_path(workspace, "llm_suggestions"))
    memories = curation.record_map(workspace)
    observations = curation.observation_map(workspace)

    for candidate in candidates.values():
        require(candidate.get("protocol") == "AI-CONTEXT/CANDIDATE", f"invalid candidate protocol: {candidate['id']}")
        require(candidate.get("schema_version") == curation.CURATION_VERSION, f"unsupported candidate schema: {candidate['id']}")
        require(candidate["id"] == candidate_expected_id(candidate), f"candidate id/hash mismatch: {candidate['id']}")
        curation.validate_proposed_memory(candidate.get("proposed_memory"))
        for ref in candidate["proposed_memory"].get("source_refs", []):
            require(ref in observations, f"candidate references unknown observation: {candidate['id']} -> {ref}")
        parent = candidate.get("parent_candidate_id")
        if parent is not None:
            require(parent in candidates, f"candidate parent missing: {candidate['id']} -> {parent}")
        generator = candidate.get("generator")
        require(isinstance(generator, dict) and generator.get("type") in {"deterministic", "manual", "local_llm"}, f"invalid candidate generator: {candidate['id']}")

    decisions_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions.values():
        require(decision.get("protocol") == "AI-CONTEXT/REVIEW-DECISION", f"invalid decision protocol: {decision['id']}")
        require(decision.get("schema_version") == curation.CURATION_VERSION, f"unsupported decision schema: {decision['id']}")
        require(decision["id"] == decision_expected_id(decision), f"decision id/hash mismatch: {decision['id']}")
        candidate_id = decision.get("candidate_id")
        require(candidate_id in candidates, f"decision references missing candidate: {decision['id']}")
        require(decision.get("candidate_sha256") == curation.canonical_sha(candidates[candidate_id]), f"decision candidate hash mismatch: {decision['id']}")
        require(decision.get("decision") in curation.ALLOWED_DECISIONS, f"invalid decision value: {decision['id']}")
        actor = decision.get("actor")
        require(isinstance(actor, dict) and actor.get("type") in curation.ALLOWED_REVIEW_ACTORS, f"decision lacks human/policy authority: {decision['id']}")
        decisions_by_candidate.setdefault(candidate_id, []).append(decision)

    for candidate_id, chain in decisions_by_candidate.items():
        ordered = sorted(chain, key=lambda row: int(row.get("revision", 0)))
        for index, row in enumerate(ordered, 1):
            require(row.get("revision") == index, f"decision revision gap for candidate: {candidate_id}")
            expected_previous = ordered[index - 2]["id"] if index > 1 else None
            require(row.get("previous_decision_id") == expected_previous, f"decision chain mismatch: {row['id']}")

    for conflict in conflicts.values():
        require(conflict.get("protocol") == "AI-CONTEXT/CONFLICT", f"invalid conflict protocol: {conflict['id']}")
        require(conflict["id"] == conflict_expected_id(conflict), f"conflict id/hash mismatch: {conflict['id']}")
        require(conflict.get("candidate_id") in candidates, f"conflict candidate missing: {conflict['id']}")
        require(conflict.get("memory_id") in memories, f"conflict memory missing: {conflict['id']}")

    applications_by_memory: dict[str, list[dict[str, Any]]] = {}
    for application in applications.values():
        require(application.get("protocol") == "AI-CONTEXT/APPLICATION", f"invalid application protocol: {application['id']}")
        require(application["id"] == application_expected_id(application), f"application id/hash mismatch: {application['id']}")
        candidate_id = application.get("candidate_id")
        decision_id = application.get("decision_id")
        memory_id = application.get("memory_id")
        require(candidate_id in candidates, f"application candidate missing: {application['id']}")
        require(decision_id in decisions, f"application decision missing: {application['id']}")
        require(memory_id in memories, f"application memory missing: {application['id']}")
        decision = decisions[decision_id]
        require(decision.get("candidate_id") == candidate_id and decision.get("decision") == "approve", f"application lacks approved matching decision: {application['id']}")
        actor = decision.get("actor")
        require(isinstance(actor, dict) and actor.get("type") in curation.ALLOWED_REVIEW_ACTORS, f"application lacks human/policy approval: {application['id']}")
        require(application.get("actor") == actor, f"application actor does not match decision: {application['id']}")
        applications_by_memory.setdefault(memory_id, []).append(application)

    mutations_by_memory: dict[str, list[dict[str, Any]]] = {}
    for mutation in mutations.values():
        require(mutation.get("protocol") == "AI-CONTEXT/CURATION-MUTATION", f"invalid mutation protocol: {mutation['id']}")
        require(mutation["id"] == mutation_expected_id(mutation), f"mutation id/hash mismatch: {mutation['id']}")
        memory_id = mutation.get("memory_id")
        require(memory_id in memories, f"mutation memory missing: {mutation['id']}")
        require(set(mutation.get("patch", {})).issubset(curation.MUTABLE_FIELDS), f"mutation changes forbidden fields: {mutation['id']}")
        actor = mutation.get("actor")
        require(isinstance(actor, dict) and actor.get("type") in curation.ALLOWED_REVIEW_ACTORS, f"mutation actor invalid: {mutation['id']}")
        for ref in mutation.get("evidence_refs", []):
            require(ref in observations, f"mutation evidence missing: {mutation['id']} -> {ref}")
        mutations_by_memory.setdefault(memory_id, []).append(mutation)

    for memory_id, chain in mutations_by_memory.items():
        ordered = sorted(chain, key=lambda row: int(row.get("revision", 0)))
        for index, row in enumerate(ordered, 1):
            require(row.get("revision") == index, f"mutation revision gap for memory: {memory_id}")
            expected_previous = ordered[index - 2]["id"] if index > 1 else None
            require(row.get("previous_mutation_id") == expected_previous, f"mutation chain mismatch: {row['id']}")
            if index > 1:
                require(row.get("before_sha256") == ordered[index - 2].get("after_sha256"), f"mutation before/after chain hash mismatch: {row['id']}")
        require(ordered[-1].get("after_sha256") == curation.canonical_sha(memories[memory_id]), f"latest mutation does not match current memory: {memory_id}")
        apps = applications_by_memory.get(memory_id, [])
        if apps:
            earliest = ordered[0]
            require(any(app.get("memory_sha256") == earliest.get("before_sha256") for app in apps), f"first mutation does not chain from an application: {memory_id}")

    for memory_id, apps in applications_by_memory.items():
        if memory_id not in mutations_by_memory:
            require(any(app.get("memory_sha256") == curation.canonical_sha(memories[memory_id]) for app in apps), f"application memory hash mismatch: {memory_id}")

    for edge in supersessions.values():
        require(edge.get("protocol") == "AI-CONTEXT/SUPERSESSION", f"invalid supersession protocol: {edge['id']}")
        require(edge["id"] == supersession_expected_id(edge), f"supersession id/hash mismatch: {edge['id']}")
        require(edge.get("from_memory_id") in memories and edge.get("to_memory_id") in memories, f"supersession endpoint missing: {edge['id']}")
        mutation = mutations.get(edge.get("mutation_id"))
        require(mutation is not None and mutation.get("kind") == "supersede" and mutation.get("memory_id") == edge.get("from_memory_id"), f"supersession mutation mismatch: {edge['id']}")

    for receipt in tombstones.values():
        require(receipt.get("protocol") == "AI-CONTEXT/TOMBSTONE", f"invalid tombstone protocol: {receipt['id']}")
        require(receipt["id"] == tombstone_expected_id(receipt), f"tombstone id/hash mismatch: {receipt['id']}")
        mutation = mutations.get(receipt.get("mutation_id"))
        require(mutation is not None and mutation.get("kind") == "tombstone" and mutation.get("memory_id") == receipt.get("memory_id"), f"tombstone mutation mismatch: {receipt['id']}")

    for suggestion in suggestions.values():
        require(suggestion.get("protocol") == "AI-CONTEXT/LLM-SUGGESTION", f"invalid LLM suggestion protocol: {suggestion['id']}")
        require(suggestion.get("candidate_id") in candidates, f"LLM suggestion candidate missing: {suggestion['id']}")

    return {
        "status": "ok",
        "candidates": len(candidates),
        "decisions": len(decisions),
        "conflicts": len(conflicts),
        "applications": len(applications),
        "mutations": len(mutations),
        "supersession_edges": len(supersessions),
        "tombstones": len(tombstones),
        "llm_suggestions": len(suggestions),
        "pending_review": sum(1 for cid in candidates if curation.candidate_status(workspace, cid) == "pending"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="validate AI-CONTEXT Phase 5 curation state")
    parser.add_argument("workspace")
    args = parser.parse_args()
    try:
        result = cmd_validate(Path(args.workspace).expanduser().resolve())
    except (ai_context.ContextError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
