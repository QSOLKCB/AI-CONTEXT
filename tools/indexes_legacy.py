#!/usr/bin/env python3
"""Phase 9 deterministic derived indexes for AI-CONTEXT.

Indexes are rebuildable retrieval accelerators over canonical memory. They never grant
memory authority or disclosure permission. Every usable projection is bound to a source
fingerprint of the complete canonical store and must validate fresh before query use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import ai_context_legacy as core
import routing

INDEX_VERSION = "0.1.0"
VECTOR_DIMENSIONS = 256
INDEX_DIR = "indexes"
INDEX_FILES = {
    "vector": "vector.json",
    "graph": "graph.json",
    "search": "search.json",
    "manifest": "manifest.json",
}
RETRIEVAL_POLICY = "approved-active-v1"
TOKENIZATION = "unicode-casefold-v1"
VECTOR_GENERATOR = "sha256-token-bucket-count-v1"


class IndexError(core.ContextError):
    pass


def canonical_sha(value: Any) -> str:
    return core.sha256_bytes(core.canonical_bytes(value))


def stable_id(prefix: str, core_value: dict[str, Any]) -> str:
    return f"{prefix}.sha256:{canonical_sha(core_value)}"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _write_json(path: Path, value: Any) -> None:
    _atomic_write(path, core.canonical_bytes(value) + b"\n")


def _read_json(path: Path) -> dict[str, Any]:
    value = core.load_json(path)
    if not isinstance(value, dict):
        raise IndexError(f"index artifact must be an object: {path}")
    return value


def _ensure_private_index_path(workspace: Path) -> None:
    gitignore = workspace / ".gitignore"
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if "indexes/" not in lines:
            lines.append("indexes/")
            gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    index_dir = workspace / INDEX_DIR
    if index_dir.is_symlink():
        raise IndexError("indexes/ must not be a symlink")


def _validated_records(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    core.ensure_workspace(workspace)
    policy = core.load_policy(workspace)
    records = core.read_jsonl(workspace / "memory" / "records.jsonl")
    seen: set[str] = set()
    for record in records:
        core.validate_memory_record(record, workspace, policy)
        rid = record.get("id")
        if not isinstance(rid, str) or not rid:
            raise IndexError("canonical memory row missing id")
        if rid in seen:
            raise IndexError(f"duplicate canonical memory id: {rid}")
        seen.add(rid)
    ordered = sorted(records, key=lambda row: row["id"])
    active = [
        row for row in ordered
        if row.get("approval") == "approved"
        and isinstance(row.get("lifecycle"), dict)
        and row["lifecycle"].get("state") == "active"
    ]
    return ordered, active


def source_fingerprint(workspace: Path) -> dict[str, Any]:
    records, active = _validated_records(workspace)
    core_value = {
        "protocol": "AI-CONTEXT/DERIVED-SOURCE-FINGERPRINT",
        "schema_version": INDEX_VERSION,
        "canonicalizer": "python-json-v0.1",
        "retrieval_policy": RETRIEVAL_POLICY,
        "canonical_store_sha256": canonical_sha(records),
        "canonical_record_count": len(records),
        "retrieval_records_sha256": canonical_sha(active),
        "retrieval_record_count": len(active),
    }
    return {"id": stable_id("index-source", core_value), **core_value}


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    if isinstance(value, dict):
        result = []
        for key in sorted(value):
            result.append(str(key))
            result.extend(_flatten_text(value[key]))
        return result
    return []


def record_text(record: dict[str, Any]) -> str:
    parts: list[str] = [str(record.get("record_type", ""))]
    parts.extend(str(tag) for tag in record.get("tags", []) if isinstance(tag, str))
    parts.extend(_flatten_text(record.get("content")))
    notes = record.get("notes")
    if isinstance(notes, str) and notes:
        parts.append(notes)
    return "\n".join(parts)


def token_sequence(value: str) -> list[str]:
    return [
        token.casefold()
        for token in routing.TOKEN_RE.findall(value)
        if token.casefold() not in routing.STOPWORDS
    ]


def _token_dimension(token: str) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % VECTOR_DIMENSIONS


def _sparse_vector(tokens: list[str]) -> list[dict[str, int]]:
    counts: Counter[int] = Counter(_token_dimension(token) for token in tokens)
    return [
        {"dimension": dimension, "weight": counts[dimension]}
        for dimension in sorted(counts)
    ]


def build_vector_index(workspace: Path, fingerprint: dict[str, Any] | None = None) -> dict[str, Any]:
    _all, active = _validated_records(workspace)
    fp = fingerprint or source_fingerprint(workspace)
    records = []
    for record in active:
        tokens = token_sequence(record_text(record))
        records.append({
            "memory_id": record["id"],
            "token_count": len(tokens),
            "vector": _sparse_vector(tokens),
        })
    core_value = {
        "protocol": "AI-CONTEXT/VECTOR-INDEX",
        "schema_version": INDEX_VERSION,
        "index_kind": "vector",
        "authority": "derived-retrieval-only",
        "source_fingerprint": fp,
        "generator": {
            "id": VECTOR_GENERATOR,
            "dimensions": VECTOR_DIMENSIONS,
            "tokenization": TOKENIZATION,
            "weights": "integer-token-counts",
        },
        "records": records,
    }
    return {"id": stable_id("vector-index", core_value), **core_value}


def _relationship_endpoint(content: dict[str, Any], side: str) -> tuple[str, str] | None:
    memory_key = f"{side}_memory_id"
    semantic_key = f"{side}_semantic_key"
    memory = content.get(memory_key)
    semantic = content.get(semantic_key)
    if isinstance(memory, str) and memory and not semantic:
        return "memory", memory
    if isinstance(semantic, str) and semantic and not memory:
        return "semantic_key", semantic
    return None


def build_graph_index(workspace: Path, fingerprint: dict[str, Any] | None = None) -> dict[str, Any]:
    _all, active = _validated_records(workspace)
    fp = fingerprint or source_fingerprint(workspace)
    active_ids = {record["id"] for record in active}
    node_map: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []

    def add_node(node_id: str, node: dict[str, Any]) -> None:
        current = node_map.get(node_id)
        if current is not None and core.canonical_bytes(current) != core.canonical_bytes(node):
            raise IndexError(f"graph node collision: {node_id}")
        node_map[node_id] = node

    for record in active:
        memory_node = f"memory:{record['id']}"
        add_node(memory_node, {
            "id": memory_node,
            "kind": "memory",
            "memory_id": record["id"],
            "record_type": record["record_type"],
            "sensitivity": record["sensitivity"],
        })
        for source_ref in sorted(record.get("source_refs", [])):
            node_id = f"observation:{source_ref}"
            add_node(node_id, {"id": node_id, "kind": "observation_ref", "ref": source_ref})
            edges.append({
                "from": memory_node,
                "to": node_id,
                "relation": "source_ref",
                "relationship_memory_id": None,
            })
        lifecycle = record.get("lifecycle", {})
        supersedes = lifecycle.get("supersedes") if isinstance(lifecycle, dict) else None
        if isinstance(supersedes, str) and supersedes in active_ids:
            edges.append({
                "from": memory_node,
                "to": f"memory:{supersedes}",
                "relation": "supersedes",
                "relationship_memory_id": None,
            })

    for record in active:
        if record.get("record_type") != "relationship":
            continue
        content = record.get("content")
        if not isinstance(content, dict):
            unresolved.append({"memory_id": record["id"], "reason": "relationship-content-not-object"})
            continue
        relation = content.get("relation")
        if not isinstance(relation, str) or not relation:
            unresolved.append({"memory_id": record["id"], "reason": "relationship-name-missing"})
            continue
        left = _relationship_endpoint(content, "from")
        right = _relationship_endpoint(content, "to")
        if left is None or right is None:
            unresolved.append({"memory_id": record["id"], "reason": "relationship-endpoint-ambiguous-or-missing"})
            continue
        endpoint_nodes: list[str] = []
        blocked = False
        for kind, value in (left, right):
            if kind == "memory":
                if value not in active_ids:
                    blocked = True
                    break
                endpoint_nodes.append(f"memory:{value}")
            else:
                node_id = f"semantic:{value}"
                add_node(node_id, {"id": node_id, "kind": "semantic_key", "ref": value})
                endpoint_nodes.append(node_id)
        if blocked:
            unresolved.append({"memory_id": record["id"], "reason": "relationship-memory-endpoint-not-active"})
            continue
        edges.append({
            "from": endpoint_nodes[0],
            "to": endpoint_nodes[1],
            "relation": relation,
            "relationship_memory_id": record["id"],
        })

    nodes = [node_map[key] for key in sorted(node_map)]
    edges = sorted(edges, key=lambda row: (row["from"], row["relation"], row["to"], row["relationship_memory_id"] or ""))
    unresolved = sorted(unresolved, key=lambda row: (row["memory_id"], row["reason"]))
    core_value = {
        "protocol": "AI-CONTEXT/GRAPH-INDEX",
        "schema_version": INDEX_VERSION,
        "index_kind": "graph",
        "authority": "derived-retrieval-only",
        "source_fingerprint": fp,
        "nodes": nodes,
        "edges": edges,
        "unresolved_relationships": unresolved,
    }
    return {"id": stable_id("graph-index", core_value), **core_value}


def build_search_cache(workspace: Path, fingerprint: dict[str, Any] | None = None) -> dict[str, Any]:
    _all, active = _validated_records(workspace)
    fp = fingerprint or source_fingerprint(workspace)
    postings: dict[str, set[str]] = {}
    record_stats = []
    for record in active:
        tokens = token_sequence(record_text(record))
        unique = sorted(set(tokens))
        for token in unique:
            postings.setdefault(token, set()).add(record["id"])
        record_stats.append({
            "memory_id": record["id"],
            "token_count": len(tokens),
            "unique_token_count": len(unique),
        })
    posting_rows = [
        {"token": token, "memory_ids": sorted(postings[token])}
        for token in sorted(postings)
    ]
    core_value = {
        "protocol": "AI-CONTEXT/SEARCH-CACHE",
        "schema_version": INDEX_VERSION,
        "index_kind": "search",
        "authority": "derived-retrieval-only",
        "source_fingerprint": fp,
        "tokenization": TOKENIZATION,
        "postings": posting_rows,
        "records": record_stats,
    }
    return {"id": stable_id("search-cache", core_value), **core_value}


def _artifact_bytes(value: dict[str, Any]) -> bytes:
    return core.canonical_bytes(value) + b"\n"


def build_manifest(fingerprint: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for kind in ("vector", "graph", "search"):
        value = artifacts[kind]
        rows.append({
            "kind": kind,
            "path": INDEX_FILES[kind],
            "artifact_id": value["id"],
            "sha256": core.sha256_bytes(_artifact_bytes(value)),
            "bytes": len(_artifact_bytes(value)),
        })
    core_value = {
        "protocol": "AI-CONTEXT/INDEX-MANIFEST",
        "schema_version": INDEX_VERSION,
        "authority": "derived-retrieval-only",
        "source_fingerprint": fingerprint,
        "artifacts": rows,
    }
    return {"id": stable_id("index-manifest", core_value), **core_value}


def _build_all(workspace: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    fp = source_fingerprint(workspace)
    artifacts = {
        "vector": build_vector_index(workspace, fp),
        "graph": build_graph_index(workspace, fp),
        "search": build_search_cache(workspace, fp),
    }
    manifest = build_manifest(fp, artifacts)
    return manifest, artifacts


def build_indexes(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    _ensure_private_index_path(workspace)
    manifest, artifacts = _build_all(workspace)
    temp = Path(tempfile.mkdtemp(prefix=".indexes-build-", dir=workspace))
    backup: Path | None = None
    index_dir = workspace / INDEX_DIR
    try:
        for kind in ("vector", "graph", "search"):
            _write_json(temp / INDEX_FILES[kind], artifacts[kind])
        _write_json(temp / INDEX_FILES["manifest"], manifest)
        if index_dir.exists():
            if not index_dir.is_dir() or index_dir.is_symlink():
                raise IndexError("indexes path must be a normal directory")
            backup = Path(tempfile.mkdtemp(prefix=".indexes-old-", dir=workspace))
            backup.rmdir()
            os.replace(index_dir, backup)
        try:
            os.replace(temp, index_dir)
        except Exception:
            if backup is not None and backup.exists() and not index_dir.exists():
                os.replace(backup, index_dir)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
        return {
            "status": "ok",
            "manifest_id": manifest["id"],
            "source_fingerprint_id": manifest["source_fingerprint"]["id"],
            "retrieval_records": manifest["source_fingerprint"]["retrieval_record_count"],
            "vector_records": len(artifacts["vector"]["records"]),
            "graph_nodes": len(artifacts["graph"]["nodes"]),
            "graph_edges": len(artifacts["graph"]["edges"]),
            "search_terms": len(artifacts["search"]["postings"]),
        }
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def _require_exact_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise IndexError(f"{label} has missing/unknown fields")


def _validate_source_fingerprint(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IndexError("source_fingerprint must be an object")
    fields = {
        "id", "protocol", "schema_version", "canonicalizer", "retrieval_policy",
        "canonical_store_sha256", "canonical_record_count", "retrieval_records_sha256",
        "retrieval_record_count",
    }
    _require_exact_fields(value, fields, "source_fingerprint")
    if value.get("protocol") != "AI-CONTEXT/DERIVED-SOURCE-FINGERPRINT":
        raise IndexError("invalid source fingerprint protocol")
    if value.get("schema_version") != INDEX_VERSION:
        raise IndexError("unsupported source fingerprint schema")
    if value.get("canonicalizer") != "python-json-v0.1" or value.get("retrieval_policy") != RETRIEVAL_POLICY:
        raise IndexError("unsupported source fingerprint policy")
    core_value = {key: item for key, item in value.items() if key != "id"}
    if value.get("id") != stable_id("index-source", core_value):
        raise IndexError("source fingerprint id/hash mismatch")
    return value


def _validate_artifact_identity(value: dict[str, Any], protocol: str, prefix: str, kind: str) -> None:
    if value.get("protocol") != protocol or value.get("schema_version") != INDEX_VERSION:
        raise IndexError(f"invalid {kind} index protocol/schema")
    if value.get("index_kind") != kind or value.get("authority") != "derived-retrieval-only":
        raise IndexError(f"invalid {kind} index authority/kind")
    _validate_source_fingerprint(value.get("source_fingerprint"))
    core_value = {key: item for key, item in value.items() if key != "id"}
    if value.get("id") != stable_id(prefix, core_value):
        raise IndexError(f"{kind} index id/hash mismatch")


def validate_indexes(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    index_dir = workspace / INDEX_DIR
    if not index_dir.is_dir() or index_dir.is_symlink():
        raise IndexError("derived indexes are missing or unsafe; rebuild them")
    actual_files = {path.name for path in index_dir.iterdir() if path.is_file()}
    expected_files = set(INDEX_FILES.values())
    if actual_files != expected_files:
        raise IndexError(f"derived index file set mismatch: expected={sorted(expected_files)} actual={sorted(actual_files)}")

    vector = _read_json(index_dir / INDEX_FILES["vector"])
    graph = _read_json(index_dir / INDEX_FILES["graph"])
    search = _read_json(index_dir / INDEX_FILES["search"])
    manifest = _read_json(index_dir / INDEX_FILES["manifest"])

    _validate_artifact_identity(vector, "AI-CONTEXT/VECTOR-INDEX", "vector-index", "vector")
    _validate_artifact_identity(graph, "AI-CONTEXT/GRAPH-INDEX", "graph-index", "graph")
    _validate_artifact_identity(search, "AI-CONTEXT/SEARCH-CACHE", "search-cache", "search")
    if manifest.get("protocol") != "AI-CONTEXT/INDEX-MANIFEST" or manifest.get("schema_version") != INDEX_VERSION:
        raise IndexError("invalid index manifest protocol/schema")
    if manifest.get("authority") != "derived-retrieval-only":
        raise IndexError("index manifest claims invalid authority")
    _validate_source_fingerprint(manifest.get("source_fingerprint"))
    manifest_core = {key: item for key, item in manifest.items() if key != "id"}
    if manifest.get("id") != stable_id("index-manifest", manifest_core):
        raise IndexError("index manifest id/hash mismatch")

    artifacts = {"vector": vector, "graph": graph, "search": search}
    expected_manifest = build_manifest(manifest["source_fingerprint"], artifacts)
    if core.canonical_bytes(expected_manifest) != core.canonical_bytes(manifest):
        raise IndexError("index manifest does not match artifact identities/hashes")

    fresh_manifest, expected = _build_all(workspace)
    if core.canonical_bytes(fresh_manifest["source_fingerprint"]) != core.canonical_bytes(manifest["source_fingerprint"]):
        raise IndexError("derived indexes are stale for the current canonical memory store")
    for kind in ("vector", "graph", "search"):
        if core.canonical_bytes(expected[kind]) != core.canonical_bytes(artifacts[kind]):
            raise IndexError(f"{kind} index does not match the deterministic current projection")
    if core.canonical_bytes(fresh_manifest) != core.canonical_bytes(manifest):
        raise IndexError("index manifest is stale or inconsistent")

    return {
        "status": "ok",
        "manifest_id": manifest["id"],
        "source_fingerprint_id": manifest["source_fingerprint"]["id"],
        "retrieval_records": manifest["source_fingerprint"]["retrieval_record_count"],
        "vector_records": len(vector["records"]),
        "graph_nodes": len(graph["nodes"]),
        "graph_edges": len(graph["edges"]),
        "search_terms": len(search["postings"]),
    }


def search_cache(workspace: Path, query: str, *, limit: int = 10) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise IndexError("search query must be non-empty")
    if limit < 1 or limit > 100:
        raise IndexError("search limit must be from 1 to 100")
    validation = validate_indexes(workspace)
    cache = _read_json(workspace.expanduser().resolve() / INDEX_DIR / INDEX_FILES["search"])
    terms = sorted(set(token_sequence(query)))
    if not terms:
        raise IndexError("search query produced no meaningful terms")
    posting_map = {row["token"]: row["memory_ids"] for row in cache["postings"]}
    scores: Counter[str] = Counter()
    matches: dict[str, list[str]] = {}
    for term in terms:
        for memory_id in posting_map.get(term, []):
            scores[memory_id] += 1
            matches.setdefault(memory_id, []).append(term)
    ranked = sorted(scores, key=lambda memory_id: (-scores[memory_id], memory_id))[:limit]
    return {
        "status": "ok",
        "query_terms": terms,
        "results": [
            {"memory_id": memory_id, "score": scores[memory_id], "matched_terms": sorted(matches[memory_id])}
            for memory_id in ranked
        ],
        "source_fingerprint_id": validation["source_fingerprint_id"],
        "authority": "candidate-retrieval-only",
        "disclosure_requires_routing": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 9 derived indexes")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild deterministic vector/graph/search projections")
    build.add_argument("workspace")
    validate = sub.add_parser("validate", help="validate freshness and deterministic projection equality")
    validate.add_argument("workspace")
    search = sub.add_parser("search", help="query the validated lexical search cache")
    search.add_argument("workspace")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        workspace = Path(args.workspace).expanduser().resolve()
        if args.command == "build":
            result = build_indexes(workspace)
        elif args.command == "validate":
            result = validate_indexes(workspace)
        elif args.command == "search":
            result = search_cache(workspace, args.query, limit=args.limit)
        else:
            raise IndexError(f"unsupported index command: {args.command}")
    except (IndexError, core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
