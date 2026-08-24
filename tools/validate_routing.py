#!/usr/bin/env python3
"""Validate Phase 6 routing policy, profiles, dependency graph, and routed bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ai_context_legacy as core
import routing


def require(condition: bool, message: str) -> None:
    if not condition:
        raise routing.RoutingError(message)


def validate_bundle(workspace: Path, path: Path) -> dict[str, object]:
    bundle = core.load_json(path)
    require(isinstance(bundle, dict), "bundle must be an object")
    require(bundle.get("protocol") == "AI-CONTEXT/BUNDLE", "invalid bundle protocol")
    require(bundle.get("schema_version") == core.PROTOCOL_VERSION, "unsupported bundle schema version")
    require(isinstance(bundle.get("routing"), dict), "bundle is not a Phase 6 routed bundle")
    require(isinstance(bundle.get("diagnostics"), list), "routed bundle diagnostics must be an array")
    digest = bundle.get("canonical_payload_sha256")
    payload = {key: value for key, value in bundle.items() if key != "canonical_payload_sha256"}
    require(digest == routing.canonical_sha(payload), "bundle canonical payload hash mismatch")

    routing_meta = bundle["routing"]
    profile = bundle.get("profile")
    target = routing_meta.get("target")
    task = routing_meta.get("task")
    selector_tags = bundle.get("selector_tags")
    require(isinstance(profile, str) and profile, "bundle profile missing")
    require(isinstance(target, str) and target, "bundle routing target missing")
    require(task is None or isinstance(task, str), "bundle routing task must be string or null")
    require(isinstance(selector_tags, list) and all(isinstance(tag, str) for tag in selector_tags), "bundle selector_tags invalid")

    planned = routing.plan_bundle(
        workspace,
        profile_name=profile,
        extra_tags=set(selector_tags),
        task=task,
        target_name=target,
    )
    require(
        core.canonical_bytes(planned) == core.canonical_bytes(bundle),
        "bundle is stale or does not match the current deterministic routing decision",
    )
    return {
        "bundle_records": len(bundle.get("records", [])),
        "bundle_sha256": digest,
        "target": target,
    }


def validate_workspace(workspace: Path, bundle_path: Path | None = None) -> dict[str, object]:
    routing.ensure_routing(workspace)
    policy = routing.load_routing_policy(workspace)
    profiles_dir = workspace / "profiles"
    profile_names: list[str] = []
    for path in sorted(profiles_dir.glob("*.json")):
        routing.load_profile(workspace, path.stem)
        profile_names.append(path.stem)

    records_list = core.read_jsonl(workspace / "memory" / "records.jsonl")
    policy_core = core.load_policy(workspace)
    records: dict[str, dict[str, object]] = {}
    for record in records_list:
        core.validate_memory_record(record, workspace, policy_core)
        rid = record.get("id")
        require(isinstance(rid, str) and rid not in records, f"duplicate/invalid memory id: {rid}")
        records[rid] = record
    graph = routing.dependency_graph(workspace, records)

    result: dict[str, object] = {
        "status": "ok",
        "profiles": profile_names,
        "targets": sorted(policy["targets"]),
        "default_target": policy["default_target"],
        "memory_records": len(records),
        "dependency_sources": len(graph),
        "dependency_edges": sum(len(edges) for edges in graph.values()),
    }
    if bundle_path is not None:
        result.update(validate_bundle(workspace, bundle_path))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="validate AI-CONTEXT Phase 6 routing state")
    parser.add_argument("workspace")
    parser.add_argument("--bundle")
    args = parser.parse_args()
    try:
        result = validate_workspace(
            Path(args.workspace).expanduser().resolve(),
            Path(args.bundle).expanduser().resolve() if args.bundle else None,
        )
    except (core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
