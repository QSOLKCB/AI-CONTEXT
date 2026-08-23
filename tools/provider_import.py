#!/usr/bin/env python3
"""Import unstable provider exports into AI-CONTEXT staging.

This command is intentionally separate from the Phase 1 core CLI. Provider formats churn;
normalized observations and receipts remain the stable boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import ai_context
from provider_adapters import (
    ADAPTER_PROTOCOL_VERSION,
    AdapterError,
    ParseResult,
    ParsedMessage,
    parse_browser_html,
    parse_gemini_takeout,
    parse_grok_export,
    parse_plugin_json,
    parse_role_prefixed_text,
    validate_plugin_descriptor,
)

BUILTIN_ADAPTERS = {"gemini", "grok", "browser-chat"}


def _strict_json_bytes(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ai_context.ContextError(f"cannot decode {label}: {exc}") from exc
    return ai_context.json_loads_strict(text, label=label)


def _merge(results: list[ParseResult], *, source_type: str, adapter_id: str) -> ParseResult:
    if not results:
        raise ai_context.ContextError(f"{adapter_id} adapter found no supported input files")
    messages: list[ParsedMessage] = []
    warnings: list[str] = []
    layouts: list[str] = []
    for result in results:
        messages.extend(result.messages)
        warnings.extend(result.warnings)
        if result.layout_id not in layouts:
            layouts.append(result.layout_id)
    parse_status = "partial" if warnings or any(result.parse_status == "partial" for result in results) else "exact"
    return ParseResult(
        source_type=source_type,
        adapter_id=adapter_id,
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id=layouts[0] if len(layouts) == 1 else "mixed:" + ",".join(layouts),
        parse_status=parse_status,
        messages=messages,
        warnings=warnings,
    )


def _find_directory_files(root: Path, names: set[str]) -> list[Path]:
    matches: list[Path] = []
    lowered = {name.casefold() for name in names}
    for child in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
        if child.is_symlink() or not child.is_file():
            continue
        if child.name.casefold() in lowered:
            matches.append(child)
    return matches


def _select_single_file(matches: list[Path], label: str) -> Path:
    if not matches:
        raise ai_context.ContextError(f"{label} file not found")
    if len(matches) > 1:
        raise ai_context.ContextError(f"multiple {label} files found; import a narrower directory or the exact file")
    return matches[0]


def _zip_member_by_basename(infos: list[zipfile.ZipInfo], basenames: set[str]) -> zipfile.ZipInfo:
    lowered = {name.casefold() for name in basenames}
    matches = [info for info in infos if PurePosixPath(info.filename).name.casefold() in lowered]
    if not matches:
        raise ai_context.ContextError(f"archive does not contain any of: {', '.join(sorted(basenames))}")
    if len(matches) > 1:
        preferred = [
            info for info in matches
            if "gemini apps" in info.filename.casefold() or "export_data" in info.filename.casefold()
        ]
        if len(preferred) == 1:
            return preferred[0]
        raise ai_context.ContextError("archive contains multiple candidate provider data files")
    return matches[0]


def _load_plugin(path: Path) -> tuple[dict[str, Any], str]:
    descriptor = validate_plugin_descriptor(ai_context.load_json(path))
    digest = ai_context.sha256_bytes(ai_context.canonical_bytes(descriptor))
    return descriptor, digest


def _plugin_json_from_source(
    source: Path,
    policy: dict[str, Any],
    descriptor: dict[str, Any],
) -> tuple[Any, str]:
    filename = descriptor.get("input", {}).get("filename")
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            infos = ai_context.safe_zip_infos(archive, policy)
            if filename:
                info = _zip_member_by_basename(infos, {str(filename)})
            else:
                json_infos = [info for info in infos if PurePosixPath(info.filename).suffix.casefold() == ".json"]
                if len(json_infos) != 1:
                    raise ai_context.ContextError("plugin ZIP import requires input.filename unless the archive has exactly one JSON file")
                info = json_infos[0]
            return _strict_json_bytes(archive.read(info), info.filename), info.filename
    if source.is_dir():
        if filename:
            target = _select_single_file(_find_directory_files(source, {str(filename)}), f"plugin {filename}")
        else:
            candidates = [
                child for child in sorted(source.rglob("*.json"))
                if child.is_file() and not child.is_symlink()
            ]
            target = _select_single_file(candidates, "plugin JSON")
        return ai_context.load_json(target), target.relative_to(source).as_posix()
    if source.suffix.casefold() != ".json":
        raise ai_context.ContextError("plugin adapter currently requires JSON input or a ZIP/directory containing JSON")
    return ai_context.load_json(source), source.name


def _parse_gemini(source: Path, policy: dict[str, Any]) -> ParseResult:
    names = {"MyActivity.json", "My Activity.json"}
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            infos = ai_context.safe_zip_infos(archive, policy)
            info = _zip_member_by_basename(infos, names)
            data = _strict_json_bytes(archive.read(info), info.filename)
        return parse_gemini_takeout(data)
    if source.is_dir():
        target = _select_single_file(_find_directory_files(source, names), "Gemini MyActivity.json")
        return parse_gemini_takeout(ai_context.load_json(target))
    if source.suffix.casefold() != ".json":
        raise ai_context.ContextError("Gemini adapter requires MyActivity.json or a Takeout ZIP/directory")
    return parse_gemini_takeout(ai_context.load_json(source))


def _parse_grok(source: Path, policy: dict[str, Any]) -> ParseResult:
    name = "prod-grok-backend.json"
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            infos = ai_context.safe_zip_infos(archive, policy)
            info = _zip_member_by_basename(infos, {name})
            data = _strict_json_bytes(archive.read(info), info.filename)
        return parse_grok_export(data)
    if source.is_dir():
        target = _select_single_file(_find_directory_files(source, {name}), name)
        return parse_grok_export(ai_context.load_json(target))
    if source.name.casefold() != name:
        raise ai_context.ContextError("Grok adapter requires prod-grok-backend.json or an account export ZIP/directory")
    return parse_grok_export(ai_context.load_json(source))


def _browser_result_for_bytes(raw: bytes, name: str) -> ParseResult:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ai_context.ContextError(f"browser-chat member is not UTF-8: {name}: {exc}") from exc
    source_id = name.replace("\\", "/")
    suffix = Path(name).suffix.casefold()
    if suffix in {".html", ".htm"}:
        return parse_browser_html(text, source_id=source_id)
    return parse_role_prefixed_text(text, source_id=source_id)


def _parse_browser_chat(source: Path, policy: dict[str, Any]) -> ParseResult:
    supported = {".html", ".htm", ".txt", ".md", ".markdown"}
    results: list[ParseResult] = []
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            infos = ai_context.safe_zip_infos(archive, policy)
            for info in sorted(infos, key=lambda item: item.filename):
                if Path(info.filename).suffix.casefold() in supported:
                    results.append(_browser_result_for_bytes(archive.read(info), info.filename))
        return _merge(results, source_type="browser-chat-archive", adapter_id="browser-chat")
    if source.is_dir():
        for child in sorted(source.rglob("*"), key=lambda path: path.relative_to(source).as_posix()):
            if child.is_symlink() or not child.is_file() or child.suffix.casefold() not in supported:
                continue
            results.append(_browser_result_for_bytes(child.read_bytes(), child.relative_to(source).as_posix()))
        return _merge(results, source_type="browser-chat-archive", adapter_id="browser-chat")
    if source.suffix.casefold() not in supported:
        raise ai_context.ContextError("browser-chat adapter supports HTML, HTM, TXT, Markdown, ZIP, or directories containing them")
    return _browser_result_for_bytes(source.read_bytes(), source.name)


def _to_observations(result: ParseResult, receipt_id: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for item in result.messages:
        observations.append(ai_context.observation(
            receipt_id,
            item.kind,
            {"text": item.text, "conversation_title": item.title},
            source_local_id=item.source_local_id,
            actor=item.actor,
            timestamp=item.timestamp,
            metadata={
                **item.metadata,
                "conversation_id": item.conversation_id,
                "adapter_id": result.adapter_id,
                "adapter_version": result.adapter_version,
                "adapter_layout": result.layout_id,
            },
        ))
    return observations


def cmd_import(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    ai_context.ensure_workspace(workspace)
    policy = ai_context.load_policy(workspace)
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise ai_context.ContextError(f"source does not exist: {source}")

    plugin_descriptor = None
    plugin_digest = None
    requested_adapter = args.adapter
    if args.adapter == "plugin":
        if not args.plugin:
            raise ai_context.ContextError("--plugin descriptor is required with --adapter plugin")
        plugin_descriptor, plugin_digest = _load_plugin(Path(args.plugin).expanduser().resolve())
        requested_adapter = f"plugin:{plugin_digest}"

    source_hash = ai_context.source_identity(source, policy)
    receipt_id = ai_context.receipt_id_for(source_hash, requested_adapter)

    if args.adapter == "gemini":
        result = _parse_gemini(source, policy)
    elif args.adapter == "grok":
        result = _parse_grok(source, policy)
    elif args.adapter == "browser-chat":
        result = _parse_browser_chat(source, policy)
    elif args.adapter == "plugin":
        data, source_member = _plugin_json_from_source(source, policy, plugin_descriptor)
        result = parse_plugin_json(data, plugin_descriptor)
        result = ParseResult(
            source_type=result.source_type,
            adapter_id=result.adapter_id,
            adapter_version=result.adapter_version,
            layout_id=result.layout_id,
            parse_status=result.parse_status,
            messages=[
                ParsedMessage(
                    text=item.text,
                    actor=item.actor,
                    conversation_id=item.conversation_id,
                    source_local_id=item.source_local_id,
                    timestamp=item.timestamp,
                    title=item.title,
                    kind=item.kind,
                    metadata={**item.metadata, "plugin_source_member": source_member},
                ) for item in result.messages
            ],
            warnings=result.warnings,
        )
    else:
        raise ai_context.ContextError(f"unknown provider adapter: {args.adapter}")

    post_parse_hash = ai_context.source_identity(source, policy)
    if post_parse_hash != source_hash:
        raise ai_context.ContextError("source changed during provider import; no observations or receipt were written")

    observations = _to_observations(result, receipt_id)
    receipt = {
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
    }

    receipts_path = workspace / "receipts" / "imports.jsonl"
    existing = {row.get("id"): row for row in ai_context.read_jsonl(receipts_path)}
    if receipt_id in existing:
        receipt = existing[receipt_id]

    appended_obs = ai_context.append_jsonl_unique(workspace / "staging" / "observations.jsonl", observations)
    appended_receipt = ai_context.append_jsonl_unique(receipts_path, [receipt])
    print(json.dumps({
        "receipt_id": receipt_id,
        "source_type": result.source_type,
        "adapter": result.adapter_id,
        "adapter_version": result.adapter_version,
        "adapter_layout": result.layout_id,
        "parse_status": result.parse_status,
        "observations_total": len(observations),
        "observations_appended": appended_obs,
        "receipt_appended": bool(appended_receipt),
        "warnings": result.warnings,
    }, indent=2, sort_keys=True, allow_nan=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT unstable provider export importer")
    parser.add_argument("workspace")
    parser.add_argument("source")
    parser.add_argument("--adapter", required=True, choices=["gemini", "grok", "browser-chat", "plugin"])
    parser.add_argument("--plugin", help="data-only AI-CONTEXT adapter plugin descriptor")
    parser.set_defaults(func=cmd_import)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ai_context.ContextError, AdapterError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
