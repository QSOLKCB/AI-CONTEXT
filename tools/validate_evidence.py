#!/usr/bin/env python3
"""Validate Phase 4 source snapshots, content objects, and duplicate-content index."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import ai_context


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ai_context.ContextError(message)


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    sid = snapshot.get("id")
    require(isinstance(sid, str) and re.fullmatch(r"snapshot\.sha256:[0-9a-f]{64}", sid) is not None, f"invalid source snapshot id: {sid}")
    require(snapshot.get("protocol") == "AI-CONTEXT/SOURCE-SNAPSHOT", f"invalid source snapshot protocol: {sid}")
    require(snapshot.get("schema_version") == ai_context.PROTOCOL_VERSION, f"unsupported source snapshot schema: {sid}")
    authority = snapshot.get("authority")
    require(isinstance(authority, dict), f"source snapshot missing authority: {sid}")
    require(isinstance(authority.get("class"), str) and authority["class"], f"source snapshot authority class invalid: {sid}")
    require(isinstance(authority.get("rank"), int) and 0 <= authority["rank"] <= 100, f"source snapshot authority rank invalid: {sid}")
    core = {key: value for key, value in snapshot.items() if key != "id"}
    expected = f"snapshot.sha256:{ai_context.sha256_bytes(ai_context.canonical_bytes(core))}"
    require(sid == expected, f"source snapshot id/hash mismatch: {sid}")

    metadata = snapshot.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("git"), dict):
        git = metadata["git"]
        head = git.get("head_commit")
        if head is not None:
            require(isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40,64}", head) is not None, f"invalid git head commit: {sid}")
        identities = git.get("identity_records", [])
        require(isinstance(identities, list), f"git identity_records must be an array: {sid}")
        for identity in identities:
            require(isinstance(identity, dict), f"invalid repository identity record: {sid}")
            require(identity.get("kind") in {"commit", "tag", "release"}, f"unknown repository identity kind: {sid}")
            require(isinstance(identity.get("id"), str) and identity["id"], f"repository identity missing id: {sid}")
            if identity.get("kind") == "release":
                require(identity.get("publication_state") in {"unverified", "published", "unpublished"}, f"release identity missing publication state: {sid}")


def validate_content(content: dict[str, Any]) -> None:
    cid = content.get("id")
    require(isinstance(cid, str) and re.fullmatch(r"content\.sha256:[0-9a-f]{64}", cid) is not None, f"invalid content id: {cid}")
    require(content.get("protocol") == "AI-CONTEXT/CONTENT", f"invalid content protocol: {cid}")
    require(content.get("schema_version") == ai_context.PROTOCOL_VERSION, f"unsupported content schema: {cid}")
    digest = content.get("sha256")
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, f"invalid content sha256: {cid}")
    require(cid == f"content.sha256:{digest}", f"content id/hash mismatch: {cid}")
    kind = content.get("content_kind")
    require(kind in {"text", "binary_ref"}, f"unknown content kind: {cid}")
    require(isinstance(content.get("byte_length"), int) and content["byte_length"] >= 0, f"invalid content byte_length: {cid}")
    if kind == "text":
        text = content.get("text")
        require(isinstance(text, str), f"text content missing text: {cid}")
        raw = text.encode("utf-8")
        require(ai_context.sha256_bytes(raw) == digest, f"text content hash mismatch: {cid}")
        require(len(raw) == content["byte_length"], f"text content byte_length mismatch: {cid}")
    else:
        require("text" not in content, f"binary_ref must not embed bytes/text: {cid}")


def expected_index(observations: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, set[str]] = {}
    for obs in observations:
        content = obs.get("content")
        content_id = content.get("content_id") if isinstance(content, dict) else None
        obs_id = obs.get("id")
        if isinstance(content_id, str) and isinstance(obs_id, str):
            groups.setdefault(content_id, set()).add(obs_id)
    return {
        "protocol": "AI-CONTEXT/CONTENT-INDEX",
        "schema_version": ai_context.PROTOCOL_VERSION,
        "groups": [
            {"content_id": cid, "observation_ids": sorted(ids)}
            for cid, ids in sorted(groups.items())
        ],
    }


def cmd_validate(workspace: Path) -> dict[str, Any]:
    ai_context.ensure_workspace(workspace)
    snapshots = ai_context.read_jsonl(workspace / "receipts" / "source-snapshots.jsonl")
    contents = ai_context.read_jsonl(workspace / "staging" / "content.jsonl")
    observations = ai_context.read_jsonl(workspace / "staging" / "observations.jsonl")
    receipts = ai_context.read_jsonl(workspace / "receipts" / "imports.jsonl")

    snapshot_ids: set[str] = set()
    for snapshot in snapshots:
        validate_snapshot(snapshot)
        require(snapshot["id"] not in snapshot_ids, f"duplicate source snapshot: {snapshot['id']}")
        snapshot_ids.add(snapshot["id"])

    content_ids: set[str] = set()
    for content in contents:
        validate_content(content)
        require(content["id"] not in content_ids, f"duplicate content object: {content['id']}")
        content_ids.add(content["id"])

    evidence_counts: dict[str, int] = {}
    content_by_receipt: dict[str, set[str]] = {}
    for obs in observations:
        content = obs.get("content")
        content_id = content.get("content_id") if isinstance(content, dict) else None
        if not isinstance(content_id, str):
            continue
        require(content_id in content_ids, f"observation references missing content object: {obs.get('id')}")
        metadata = obs.get("metadata")
        require(isinstance(metadata, dict), f"evidence observation missing metadata: {obs.get('id')}")
        snapshot_id = metadata.get("source_snapshot_id")
        require(snapshot_id in snapshot_ids, f"evidence observation references missing source snapshot: {obs.get('id')}")
        receipt_id = obs.get("source_receipt_id")
        require(isinstance(receipt_id, str), f"evidence observation missing receipt: {obs.get('id')}")
        evidence_counts[receipt_id] = evidence_counts.get(receipt_id, 0) + 1
        content_by_receipt.setdefault(receipt_id, set()).add(content_id)

    for receipt in receipts:
        snapshot_id = receipt.get("source_snapshot_id")
        if snapshot_id is None:
            continue
        rid = receipt.get("id")
        require(snapshot_id in snapshot_ids, f"receipt references missing source snapshot: {rid}")
        require(receipt.get("observation_count") == evidence_counts.get(rid, 0), f"evidence receipt observation_count mismatch: {rid}")
        require(receipt.get("content_object_count") == len(content_by_receipt.get(rid, set())), f"evidence receipt content_object_count mismatch: {rid}")

    index_path = workspace / "staging" / "content-index.json"
    if content_ids:
        require(index_path.exists(), "content registry exists but content-index.json is missing")
        index = ai_context.load_json(index_path)
        require(isinstance(index, dict), "content-index.json must be an object")
        digest = index.get("canonical_payload_sha256")
        payload = {key: value for key, value in index.items() if key != "canonical_payload_sha256"}
        expected_digest = ai_context.sha256_bytes(ai_context.canonical_bytes(payload))
        require(digest == expected_digest, "content index hash mismatch")
        require(payload == expected_index(observations), "content index does not match observation provenance graph")

    duplicate_groups = sum(1 for group in expected_index(observations)["groups"] if len(group["observation_ids"]) > 1)
    return {
        "status": "ok",
        "source_snapshots": len(snapshots),
        "content_objects": len(contents),
        "evidence_observations": sum(evidence_counts.values()),
        "duplicate_content_groups": duplicate_groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="validate AI-CONTEXT Phase 4 source evidence")
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
