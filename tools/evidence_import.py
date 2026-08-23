#!/usr/bin/env python3
"""Import Phase 4 repository/document/Drive/email evidence into AI-CONTEXT staging."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import zipfile
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

import ai_context
from evidence_adapters import (
    AdapterResult,
    EvidenceError,
    EvidenceItem,
    email_item,
    items_for_bytes,
    parse_drive_directory,
    parse_email_file,
    parse_repository,
)

EVIDENCE_VERSION = "0.1.0"
DOCUMENT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".csv", ".tsv", ".py", ".rs", ".js", ".mjs",
    ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".sh", ".bash",
    ".zsh", ".fish", ".sql", ".xml", ".go", ".java", ".kt", ".swift", ".c",
    ".h", ".cpp", ".hpp", ".wgsl", ".glsl", ".docx", ".pptx", ".xlsx", ".pdf",
}
EMAIL_SUFFIXES = {".eml", ".mbox", ".mbx"}


def dedup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str):
            raise ai_context.ContextError("generated row missing string id")
        previous = by_id.get(row_id)
        if previous is not None and ai_context.canonical_bytes(previous) != ai_context.canonical_bytes(row):
            raise ai_context.ContextError(f"generated id collision: {row_id}")
        by_id[row_id] = row
    return [by_id[row_id] for row_id in sorted(by_id)]


def merge_results(
    results: list[AdapterResult],
    *,
    source_type: str,
    adapter_id: str,
    layout_id: str,
    authority_class: str,
    authority_rank: int,
    snapshot_metadata: dict[str, Any],
    extra_warnings: list[str] | None = None,
) -> AdapterResult:
    if not results:
        raise ai_context.ContextError(f"{adapter_id} found no supported evidence")
    items: list[EvidenceItem] = []
    warnings = list(extra_warnings or [])
    for result in results:
        items.extend(result.items)
        warnings.extend(result.warnings)
    return AdapterResult(
        source_type=source_type,
        adapter_id=adapter_id,
        adapter_version=EVIDENCE_VERSION,
        layout_id=layout_id,
        parse_status="partial" if warnings else "exact",
        authority_class=authority_class,
        authority_rank=authority_rank,
        items=items,
        warnings=warnings,
        snapshot_metadata=snapshot_metadata,
    )


def parse_document_file(path: Path, *, source_path: str | None = None, max_lines: int = 80) -> AdapterResult:
    rel = source_path or path.name
    items, warnings = items_for_bytes(path.read_bytes(), source_path=rel, max_lines=max_lines)
    return AdapterResult(
        source_type="document-file",
        adapter_id="evidence-document",
        adapter_version=EVIDENCE_VERSION,
        layout_id="document-chunks-v1",
        parse_status="partial" if warnings else "exact",
        authority_class="document_snapshot",
        authority_rank=50,
        items=items,
        warnings=warnings,
        snapshot_metadata={},
    )


def parse_document_directory(root: Path, *, max_file_bytes: int, max_lines: int) -> AdapterResult:
    results: list[AdapterResult] = []
    warnings: list[str] = []
    for child in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
        if child.is_symlink() or not child.is_file() or ".git" in child.relative_to(root).parts:
            continue
        if child.suffix.casefold() not in DOCUMENT_SUFFIXES:
            continue
        rel = child.relative_to(root).as_posix()
        if child.stat().st_size > max_file_bytes:
            warnings.append(f"skipped oversized document: {rel}")
            continue
        results.append(parse_document_file(child, source_path=rel, max_lines=max_lines))
    return merge_results(
        results,
        source_type="document-tree",
        adapter_id="evidence-document-tree",
        layout_id="document-tree-chunks-v1",
        authority_class="document_snapshot",
        authority_rank=50,
        snapshot_metadata={},
        extra_warnings=warnings,
    )


def parse_document_zip(source: Path, policy: dict[str, Any], max_lines: int) -> AdapterResult:
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    with zipfile.ZipFile(source) as archive:
        for info in sorted(ai_context.safe_zip_infos(archive, policy), key=lambda entry: entry.filename):
            if PurePosixPath(info.filename).suffix.casefold() not in DOCUMENT_SUFFIXES:
                continue
            file_items, extra = items_for_bytes(archive.read(info), source_path=info.filename, max_lines=max_lines)
            items.extend(file_items)
            warnings.extend(extra)
    if not items:
        raise ai_context.ContextError("document ZIP contained no supported evidence")
    return AdapterResult(
        source_type="document-archive",
        adapter_id="evidence-document-archive",
        adapter_version=EVIDENCE_VERSION,
        layout_id="document-zip-chunks-v1",
        parse_status="partial" if warnings else "exact",
        authority_class="document_snapshot",
        authority_rank=50,
        items=items,
        warnings=warnings,
        snapshot_metadata={},
    )


def parse_drive_zip(source: Path, policy: dict[str, Any], max_lines: int) -> AdapterResult:
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    with zipfile.ZipFile(source) as archive:
        infos = ai_context.safe_zip_infos(archive, policy)
        drive_infos = [
            info for info in infos
            if "/drive/" in f"/{info.filename.casefold()}" or info.filename.casefold().startswith("drive/")
        ]
        for info in sorted(drive_infos or infos, key=lambda entry: entry.filename):
            path = PurePosixPath(info.filename)
            if path.name == "archive_browser.html" or path.suffix.casefold() not in DOCUMENT_SUFFIXES:
                continue
            file_items, extra = items_for_bytes(
                archive.read(info),
                source_path=info.filename,
                max_lines=max_lines,
                metadata={"drive_export": True},
            )
            items.extend(file_items)
            warnings.extend(extra)
    if not items:
        raise ai_context.ContextError("Drive export contained no supported evidence")
    return AdapterResult(
        source_type="google-drive-export",
        adapter_id="evidence-google-drive-export",
        adapter_version=EVIDENCE_VERSION,
        layout_id="google-takeout-drive-zip-v1" if drive_infos else "drive-export-zip-v1",
        parse_status="partial" if warnings else "exact",
        authority_class="drive_export_snapshot",
        authority_rank=60,
        items=items,
        warnings=warnings,
        snapshot_metadata={
            "official_export_surface": "Google Takeout/Drive",
            "drive_root_detected": bool(drive_infos),
        },
    )


def parse_email_directory(root: Path, *, max_file_bytes: int) -> AdapterResult:
    results: list[AdapterResult] = []
    warnings: list[str] = []
    for child in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
        if child.is_symlink() or not child.is_file() or ".git" in child.relative_to(root).parts:
            continue
        if child.suffix.casefold() not in EMAIL_SUFFIXES:
            continue
        if child.stat().st_size > max_file_bytes:
            warnings.append(f"skipped oversized email archive file: {child.relative_to(root).as_posix()}")
            continue
        results.append(parse_email_file(child))
    return merge_results(
        results,
        source_type="email-archive",
        adapter_id="evidence-email-archive",
        layout_id="email-directory-v1",
        authority_class="email_archive_message",
        authority_rank=65,
        snapshot_metadata={"standards": ["RFC 5322/MIME"], "gmail_label_header": "X-Gmail-Labels"},
        extra_warnings=warnings,
    )


def parse_mbox_bytes(raw: bytes, label: str) -> AdapterResult:
    fd, name = tempfile.mkstemp(prefix="ai-context-mail-", suffix=".mbox")
    os.close(fd)
    temp = Path(name)
    try:
        temp.write_bytes(raw)
        result = parse_email_file(temp)
        items = [EvidenceItem(**{**item.__dict__, "source_path": label}) for item in result.items]
        return AdapterResult(**{**result.__dict__, "items": items})
    finally:
        temp.unlink(missing_ok=True)


def parse_email_zip(source: Path, policy: dict[str, Any]) -> AdapterResult:
    results: list[AdapterResult] = []
    with zipfile.ZipFile(source) as archive:
        for info in sorted(ai_context.safe_zip_infos(archive, policy), key=lambda entry: entry.filename):
            suffix = PurePosixPath(info.filename).suffix.casefold()
            if suffix == ".eml":
                message = BytesParser(policy=email_policy.default).parsebytes(archive.read(info))
                item, warnings = email_item(message, source_path=info.filename, index=0)
                if item:
                    results.append(AdapterResult(
                        source_type="email-archive",
                        adapter_id="evidence-email-archive",
                        adapter_version=EVIDENCE_VERSION,
                        layout_id="rfc822-eml-v1",
                        parse_status="partial" if warnings else "exact",
                        authority_class="email_archive_message",
                        authority_rank=65,
                        items=[item],
                        warnings=warnings,
                        snapshot_metadata={},
                    ))
            elif suffix in {".mbox", ".mbx"}:
                results.append(parse_mbox_bytes(archive.read(info), info.filename))
    return merge_results(
        results,
        source_type="email-archive",
        adapter_id="evidence-email-archive",
        layout_id="email-zip-v1",
        authority_class="email_archive_message",
        authority_rank=65,
        snapshot_metadata={"standards": ["RFC 5322/MIME"], "gmail_label_header": "X-Gmail-Labels"},
    )


def snapshot_core(result: AdapterResult, source_hash: str) -> dict[str, Any]:
    return {
        "protocol": "AI-CONTEXT/SOURCE-SNAPSHOT",
        "schema_version": ai_context.PROTOCOL_VERSION,
        "source_type": result.source_type,
        "source_identity_sha256": source_hash,
        "authority": {
            "class": result.authority_class,
            "rank": result.authority_rank,
            "domain": "source_evidence",
        },
        "adapter": {
            "id": result.adapter_id,
            "version": result.adapter_version,
            "layout": result.layout_id,
        },
        "metadata": result.snapshot_metadata,
    }


def content_object(item: EvidenceItem) -> dict[str, Any]:
    if item.content_kind == "text":
        text = item.text or ""
        raw = text.encode("utf-8")
        digest = ai_context.sha256_bytes(raw)
        return {
            "id": f"content.sha256:{digest}",
            "protocol": "AI-CONTEXT/CONTENT",
            "schema_version": ai_context.PROTOCOL_VERSION,
            "content_kind": "text",
            "sha256": digest,
            "byte_length": len(raw),
            "text": text,
        }
    digest = item.raw_sha256
    if not isinstance(digest, str):
        raise ai_context.ContextError(f"binary evidence missing raw sha256: {item.source_path}")
    return {
        "id": f"content.sha256:{digest}",
        "protocol": "AI-CONTEXT/CONTENT",
        "schema_version": ai_context.PROTOCOL_VERSION,
        "content_kind": "binary_ref",
        "sha256": digest,
        "byte_length": item.byte_length,
    }


def rows_for_result(result: AdapterResult, receipt_id: str, snapshot_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contents: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for item in result.items:
        content = content_object(item)
        contents.append(content)
        source_range = None
        if item.line_start is not None and item.line_end is not None:
            source_range = {
                "kind": item.range_kind or "line",
                "start": item.line_start,
                "end": item.line_end,
            }
        observations.append(ai_context.observation(
            receipt_id,
            item.kind,
            {"content_id": content["id"]},
            source_local_id=item.source_local_id,
            actor=item.actor,
            timestamp=item.timestamp,
            metadata={
                **item.metadata,
                "source_path": item.source_path,
                "source_range": source_range,
                "media_type": item.media_type,
                "title": item.title,
                "source_snapshot_id": snapshot_id,
                "authority_class": result.authority_class,
                "authority_rank": result.authority_rank,
            },
        ))
    return dedup_rows(contents), dedup_rows(observations)


def rebuild_content_index(workspace: Path) -> str:
    groups: dict[str, set[str]] = {}
    for obs in ai_context.read_jsonl(workspace / "staging" / "observations.jsonl"):
        content = obs.get("content")
        content_id = content.get("content_id") if isinstance(content, dict) else None
        obs_id = obs.get("id")
        if isinstance(content_id, str) and isinstance(obs_id, str):
            groups.setdefault(content_id, set()).add(obs_id)
    payload = {
        "protocol": "AI-CONTEXT/CONTENT-INDEX",
        "schema_version": ai_context.PROTOCOL_VERSION,
        "groups": [
            {"content_id": content_id, "observation_ids": sorted(ids)}
            for content_id, ids in sorted(groups.items())
        ],
    }
    digest = ai_context.sha256_bytes(ai_context.canonical_bytes(payload))
    ai_context.write_json(
        workspace / "staging" / "content-index.json",
        {**payload, "canonical_payload_sha256": digest},
    )
    return digest


def parse_source(args: argparse.Namespace, source: Path, policy: dict[str, Any]) -> AdapterResult:
    max_file_bytes = int(policy["max_repo_file_bytes"])
    if args.adapter == "repo":
        return parse_repository(source, max_file_bytes=max_file_bytes, max_lines=args.chunk_lines)
    if args.adapter == "drive-export":
        if source.is_dir():
            return parse_drive_directory(source, max_file_bytes=max_file_bytes, max_lines=args.chunk_lines)
        if zipfile.is_zipfile(source):
            return parse_drive_zip(source, policy, args.chunk_lines)
        raise ai_context.ContextError("drive-export requires a Takeout/export directory or ZIP")
    if args.adapter == "email":
        if source.is_dir():
            return parse_email_directory(source, max_file_bytes=max_file_bytes)
        if zipfile.is_zipfile(source):
            return parse_email_zip(source, policy)
        return parse_email_file(source)
    if args.adapter == "document":
        if source.is_dir():
            return parse_document_directory(source, max_file_bytes=max_file_bytes, max_lines=args.chunk_lines)
        if zipfile.is_zipfile(source):
            return parse_document_zip(source, policy, args.chunk_lines)
        return parse_document_file(source, max_lines=args.chunk_lines)
    raise ai_context.ContextError(f"unknown evidence adapter: {args.adapter}")


def preflight(path: Path, rows: list[dict[str, Any]]) -> None:
    existing = {row.get("id"): row for row in ai_context.read_jsonl(path)}
    for row in rows:
        previous = existing.get(row["id"])
        if previous is not None and ai_context.canonical_bytes(previous) != ai_context.canonical_bytes(row):
            raise ai_context.ContextError(f"id collision with different evidence payload: {row['id']}")


def cmd_import(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ai_context.ensure_workspace(workspace)
    policy = ai_context.load_policy(workspace)
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise ai_context.ContextError(f"source does not exist: {source}")

    source_hash = ai_context.source_identity(source, policy)
    result = parse_source(args, source, policy)
    core = snapshot_core(result, source_hash)
    snapshot_id = f"snapshot.sha256:{ai_context.sha256_bytes(ai_context.canonical_bytes(core))}"
    snapshot = {"id": snapshot_id, **core}
    requested_adapter = f"evidence:{args.adapter}:{snapshot_id}"
    receipt_id = ai_context.receipt_id_for(source_hash, requested_adapter)
    contents, observations = rows_for_result(result, receipt_id, snapshot_id)

    if ai_context.source_identity(source, policy) != source_hash:
        raise ai_context.ContextError("source changed during evidence import; no state was written")

    receipts_path = workspace / "receipts" / "imports.jsonl"
    previous_receipts = {row.get("id"): row for row in ai_context.read_jsonl(receipts_path)}
    receipt = previous_receipts.get(receipt_id) or {
        "id": receipt_id,
        "protocol": "AI-CONTEXT/IMPORT",
        "schema_version": ai_context.PROTOCOL_VERSION,
        "source_type": result.source_type,
        "source_name": source.name,
        "source_identity_sha256": source_hash,
        "requested_adapter": requested_adapter,
        "adapter": {"id": result.adapter_id, "version": result.adapter_version},
        "adapter_layout": result.layout_id,
        "parse_status": result.parse_status,
        "observation_count": len(observations),
        "warnings": result.warnings,
        "imported_at": ai_context.utc_now(),
        "source_snapshot_id": snapshot_id,
        "content_object_count": len(contents),
    }

    targets = [
        (workspace / "receipts" / "source-snapshots.jsonl", [snapshot]),
        (workspace / "staging" / "content.jsonl", contents),
        (workspace / "staging" / "observations.jsonl", observations),
        (receipts_path, [receipt]),
    ]
    for path, rows in targets:
        preflight(path, rows)

    ai_context.append_jsonl_unique(targets[0][0], targets[0][1])
    ai_context.append_jsonl_unique(targets[1][0], targets[1][1])
    appended_observations = ai_context.append_jsonl_unique(targets[2][0], targets[2][1])
    appended_receipt = ai_context.append_jsonl_unique(targets[3][0], targets[3][1])
    index_sha = rebuild_content_index(workspace)

    print(json.dumps({
        "receipt_id": receipt_id,
        "source_snapshot_id": snapshot_id,
        "source_type": result.source_type,
        "adapter": result.adapter_id,
        "adapter_layout": result.layout_id,
        "parse_status": result.parse_status,
        "content_objects": len(contents),
        "observations_total": len(observations),
        "observations_appended": appended_observations,
        "receipt_appended": bool(appended_receipt),
        "content_index_sha256": index_sha,
        "warnings": result.warnings,
    }, indent=2, sort_keys=True, allow_nan=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 4 source-evidence importer")
    parser.add_argument("workspace")
    parser.add_argument("source")
    parser.add_argument("--adapter", required=True, choices=["repo", "document", "drive-export", "email"])
    parser.add_argument("--chunk-lines", type=int, default=80)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.chunk_lines < 1 or args.chunk_lines > 1000:
        print("error: --chunk-lines must be between 1 and 1000", file=os.sys.stderr)
        return 2
    try:
        cmd_import(args)
    except (ai_context.ContextError, EvidenceError, OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
