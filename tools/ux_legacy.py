#!/usr/bin/env python3
"""Phase 11 local operator UX for AI-CONTEXT.

The UX layer is an operator shell over existing protocol authorities. Read-only actions
never initialize or upgrade workspace state. Mutating CLI actions require ``--yes``;
the interactive terminal UI requires explicit confirmation immediately before writes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import ai_context_legacy as core
import curation
import curation_legacy
import restore
import validate_bundle_readonly

UX_VERSION = "0.1.0"
TOOLS = Path(__file__).resolve().parent


class UXError(core.ContextError):
    pass


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return core.read_jsonl(path) if path.is_file() and not path.is_symlink() else []


def _map_rows(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _read_rows(path):
        row_id = row.get("id")
        if isinstance(row_id, str) and row_id:
            if row_id in result:
                raise UXError(f"duplicate id in {path}: {row_id}")
            result[row_id] = row
    return result


def _require_yes(yes: bool, action: str) -> None:
    if not yes:
        raise UXError(f"{action} changes local state; rerun with --yes after reviewing the plan")


def _run_tool(script: str, *args: str) -> dict[str, Any]:
    command = [sys.executable, str(TOOLS / script), *args]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"{script} failed"
        raise UXError(message)
    text = result.stdout.strip()
    if not text:
        return {"status": "ok"}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UXError(f"{script} returned non-JSON output") from exc
    if not isinstance(value, dict):
        raise UXError(f"{script} returned a non-object result")
    return value


def _candidate_status_maps(workspace: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    candidates = _map_rows(workspace / "curation" / "candidates.jsonl")
    latest: dict[str, dict[str, Any]] = {}
    for decision in _read_rows(workspace / "curation" / "decisions.jsonl"):
        candidate_id = decision.get("candidate_id")
        revision = decision.get("revision")
        if not isinstance(candidate_id, str) or not isinstance(revision, int):
            continue
        previous = latest.get(candidate_id)
        if previous is None or revision > int(previous.get("revision", 0)):
            latest[candidate_id] = decision
    applied = {
        row["candidate_id"]
        for row in _read_rows(workspace / "curation" / "applications.jsonl")
        if isinstance(row.get("candidate_id"), str)
    }
    return candidates, latest, applied


def candidate_status(workspace: Path, candidate_id: str) -> str:
    candidates, latest, applied = _candidate_status_maps(workspace)
    if candidate_id not in candidates:
        raise UXError(f"unknown candidate: {candidate_id}")
    if candidate_id in applied:
        return "applied"
    decision = latest.get(candidate_id)
    return str(decision.get("decision")) if decision else "pending"


def list_candidates(workspace: Path, *, status: str = "pending") -> list[dict[str, Any]]:
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    candidates, latest, applied = _candidate_status_maps(workspace)
    rows: list[dict[str, Any]] = []
    for candidate_id, candidate in sorted(candidates.items()):
        current = "applied" if candidate_id in applied else (
            str(latest[candidate_id].get("decision")) if candidate_id in latest else "pending"
        )
        if status != "all" and current != status:
            continue
        proposed = candidate.get("proposed_memory") if isinstance(candidate.get("proposed_memory"), dict) else {}
        rows.append({
            "candidate_id": candidate_id,
            "status": current,
            "record_type": proposed.get("record_type"),
            "sensitivity": proposed.get("sensitivity"),
            "semantic_key": candidate.get("semantic_key") or curation_legacy.derive_semantic_key(proposed),
            "source_refs": proposed.get("source_refs", []),
            "created_at": candidate.get("created_at"),
        })
    return rows


def workspace_status(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    policy = core.load_policy(workspace)
    memories = _read_rows(workspace / "memory" / "records.jsonl")
    lifecycle_counts: dict[str, int] = {}
    for memory in memories:
        core.validate_memory_record(memory, workspace, policy)
        state = str(memory.get("lifecycle", {}).get("state", "unknown"))
        lifecycle_counts[state] = lifecycle_counts.get(state, 0) + 1

    candidates, latest, applied = _candidate_status_maps(workspace)
    candidate_counts: dict[str, int] = {"pending": 0, "approved": 0, "reject": 0, "revise": 0, "applied": 0}
    for candidate_id in candidates:
        if candidate_id in applied:
            candidate_counts["applied"] += 1
        elif candidate_id in latest:
            decision = str(latest[candidate_id].get("decision"))
            candidate_counts[decision] = candidate_counts.get(decision, 0) + 1
        else:
            candidate_counts["pending"] += 1

    profiles = []
    profiles_dir = workspace / "profiles"
    if profiles_dir.is_dir() and not profiles_dir.is_symlink():
        profiles = [path.stem for path in sorted(profiles_dir.glob("*.json")) if path.is_file() and not path.is_symlink()]

    return {
        "status": "ok",
        "workspace": str(workspace),
        "protocol_version": core.PROTOCOL_VERSION,
        "default_sensitivity": policy.get("default_sensitivity"),
        "memory_records": len(memories),
        "memory_lifecycle": lifecycle_counts,
        "candidates": candidate_counts,
        "profiles": profiles,
        "routing_policy_present": (workspace / "routing" / "policy.json").is_file(),
        "curation_policy_present": (workspace / "curation" / "policy.json").is_file(),
        "derived_indexes_present": (workspace / "indexes" / "manifest.json").is_file(),
        "safe_defaults": {
            "read_only_inspection": True,
            "cli_mutations_require_yes": True,
            "review_and_application_are_separate": True,
        },
    }


def candidate_conflicts_read_only(workspace: Path, candidate_id: str) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    candidate = _map_rows(workspace / "curation" / "candidates.jsonl").get(candidate_id)
    if candidate is None:
        raise UXError(f"unknown candidate: {candidate_id}")
    conflicts = curation.detect_conflicts(workspace, candidate, persist=False)
    return {"status": "ok", "candidate_id": candidate_id, "conflicts": conflicts, "read_only": True}


def provenance_read_only(workspace: Path, memory_id: str) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    memories = _map_rows(workspace / "memory" / "records.jsonl")
    memory = memories.get(memory_id)
    if memory is None:
        raise UXError(f"unknown memory record: {memory_id}")

    applications = [
        row for row in _read_rows(workspace / "curation" / "applications.jsonl")
        if row.get("memory_id") == memory_id
    ]
    candidate_ids = {
        row["candidate_id"] for row in applications if isinstance(row.get("candidate_id"), str)
    }
    decision_ids = {
        row["decision_id"] for row in applications if isinstance(row.get("decision_id"), str)
    }
    candidates = [
        row for row in _read_rows(workspace / "curation" / "candidates.jsonl")
        if row.get("id") in candidate_ids
    ]
    decisions = [
        row for row in _read_rows(workspace / "curation" / "decisions.jsonl")
        if row.get("id") in decision_ids
    ]
    observations = _map_rows(workspace / "staging" / "observations.jsonl")
    receipts = _map_rows(workspace / "receipts" / "imports.jsonl")
    snapshots = _map_rows(workspace / "receipts" / "source-snapshots.jsonl")
    contents = _map_rows(workspace / "staging" / "content.jsonl")
    source_chain: list[dict[str, Any]] = []
    for ref in memory.get("source_refs", []):
        obs = observations.get(ref)
        if obs is None:
            source_chain.append({"observation_ref": ref, "status": "missing"})
            continue
        receipt_id = obs.get("source_receipt_id")
        receipt = receipts.get(receipt_id) if isinstance(receipt_id, str) else None
        snapshot_id = receipt.get("source_snapshot_id") if isinstance(receipt, dict) else None
        snapshot = snapshots.get(snapshot_id) if isinstance(snapshot_id, str) else None
        content_id = None
        if isinstance(obs.get("content"), dict):
            content_id = obs["content"].get("content_id")
        content_obj = contents.get(content_id) if isinstance(content_id, str) else None
        source_chain.append({
            "observation": obs,
            "import_receipt": receipt,
            "source_snapshot": snapshot,
            "content_object": content_obj,
        })

    return {
        "status": "ok",
        "read_only": True,
        "memory": memory,
        "applications": applications,
        "candidates": candidates,
        "decisions": decisions,
        "mutations": [
            row for row in _read_rows(workspace / "curation" / "mutations.jsonl")
            if row.get("memory_id") == memory_id
        ],
        "supersession_in": [
            row for row in _read_rows(workspace / "curation" / "supersession.jsonl")
            if row.get("to_memory_id") == memory_id
        ],
        "supersession_out": [
            row for row in _read_rows(workspace / "curation" / "supersession.jsonl")
            if row.get("from_memory_id") == memory_id
        ],
        "tombstones": [
            row for row in _read_rows(workspace / "curation" / "tombstones.jsonl")
            if row.get("memory_id") == memory_id
        ],
        "source_chain": source_chain,
    }


def inspect_bundle_read_only(
    workspace: Path,
    *,
    profile: str = "general",
    target: str | None = None,
    task: str | None = None,
    tags: set[str] | None = None,
) -> dict[str, Any]:
    bundle = validate_bundle_readonly.plan_bundle_read_only(
        workspace,
        profile_name=profile,
        target_name=target,
        task=task,
        extra_tags=tags or set(),
    )
    return {
        "status": "ok",
        "read_only": True,
        "exact_payload": bundle,
        "summary": {
            "records": len(bundle["records"]),
            "target": bundle["routing"]["target"],
            "target_kind": bundle["routing"]["target_kind"],
            "effective_sensitivity_ceiling": bundle["routing"]["effective_sensitivity_ceiling"],
            "canonical_payload_sha256": bundle["canonical_payload_sha256"],
        },
    }


def import_source(
    workspace: Path,
    source: Path,
    *,
    mode: str,
    adapter: str | None,
    plugin: Path | None,
    yes: bool,
) -> dict[str, Any]:
    _require_yes(yes, "source import")
    workspace = workspace.expanduser().resolve()
    source = source.expanduser().resolve()
    if mode == "core":
        args = ["import", str(workspace), str(source)]
        if adapter:
            args += ["--adapter", adapter]
        return _run_tool("ai_context.py", *args)
    if mode == "provider":
        if not adapter:
            raise UXError("provider import requires --adapter gemini|grok|browser-chat|plugin")
        args = [str(workspace), str(source), "--adapter", adapter]
        if adapter == "plugin":
            if plugin is None:
                raise UXError("provider plugin import requires --plugin")
            args += ["--plugin", str(plugin.expanduser().resolve())]
        return _run_tool("provider_import.py", *args)
    if mode == "evidence":
        if not adapter:
            raise UXError("evidence import requires --adapter repo|document|drive-export|email")
        return _run_tool("evidence_import.py", str(workspace), str(source), "--adapter", adapter)
    raise UXError(f"unknown import mode: {mode}")


def review_candidate(
    workspace: Path,
    candidate_id: str,
    *,
    decision: str,
    actor_label: str,
    conflict_resolution: str,
    notes: str,
    yes: bool,
) -> dict[str, Any]:
    _require_yes(yes, "candidate review")
    conflicts = candidate_conflicts_read_only(workspace, candidate_id)["conflicts"]
    if decision == "approve" and conflicts and conflict_resolution == "none":
        raise UXError("approval with open conflicts requires an explicit conflict resolution")
    return _run_tool(
        "curation.py", "review", str(workspace.expanduser().resolve()),
        "--candidate", candidate_id,
        "--decision", decision,
        "--actor-type", "human",
        "--actor-label", actor_label,
        "--conflict-resolution", conflict_resolution,
        "--notes", notes,
    )


def apply_candidate(workspace: Path, candidate_id: str, *, yes: bool) -> dict[str, Any]:
    _require_yes(yes, "canonical candidate application")
    return _run_tool(
        "curation.py", "apply", str(workspace.expanduser().resolve()), "--candidate", candidate_id
    )


def backup_workspace(
    workspace: Path,
    output: Path,
    *,
    mode: str,
    enrichment: Path | None,
    yes: bool,
) -> dict[str, Any]:
    _require_yes(yes, "restore-archive export")
    return restore.export_restore(
        workspace,
        output,
        mode=mode,
        enrichment_path=enrichment,
    )


def restore_workspace(archive: Path, destination: Path, *, yes: bool) -> dict[str, Any]:
    _require_yes(yes, "workspace restore")
    return restore.restore_archive(archive, destination)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))


def _confirm_interactive(label: str) -> bool:
    print(f"\n{label}")
    return input("Type YES to continue: ").strip() == "YES"


def _pause() -> None:
    input("\nPress Enter to continue...")


def _tui_review(workspace: Path) -> None:
    queue = list_candidates(workspace, status="pending")
    if not queue:
        print("No pending candidates.")
        return
    for index, row in enumerate(queue, 1):
        print(f"{index}. {row['candidate_id']} [{row['record_type']}] key={row['semantic_key']}")
    raw = input("Candidate number or ID: ").strip()
    try:
        candidate_id = queue[int(raw) - 1]["candidate_id"] if raw.isdigit() else raw
    except (IndexError, ValueError):
        print("Invalid candidate selection.")
        return
    candidate = _map_rows(workspace / "curation" / "candidates.jsonl").get(candidate_id)
    if candidate is None:
        print("Unknown candidate.")
        return
    print("\nProposed memory:")
    _print_json(candidate.get("proposed_memory"))
    conflicts = candidate_conflicts_read_only(workspace, candidate_id)["conflicts"]
    print(f"\nOpen conflicts: {len(conflicts)}")
    if conflicts:
        _print_json(conflicts)
    decision = input("Decision [approve/reject/revise] (default revise): ").strip() or "revise"
    if decision not in {"approve", "reject", "revise"}:
        print("Unsupported decision.")
        return
    resolution = "none"
    if decision == "approve" and conflicts:
        resolution = input("Conflict resolution [coexist/supersede_existing] (default coexist): ").strip() or "coexist"
        if resolution not in {"coexist", "supersede_existing"}:
            print("Unsupported conflict resolution.")
            return
    notes = input("Review notes (optional): ").strip()
    if not _confirm_interactive(f"Record HUMAN review decision '{decision}' for {candidate_id}?"):
        print("Cancelled.")
        return
    result = review_candidate(
        workspace,
        candidate_id,
        decision=decision,
        actor_label="local-tui-user",
        conflict_resolution=resolution,
        notes=notes,
        yes=True,
    )
    _print_json(result)
    if decision == "approve" and _confirm_interactive(
        "Apply this approved candidate to canonical memory now? This is a separate authority event."
    ):
        _print_json(apply_candidate(workspace, candidate_id, yes=True))


def run_tui(workspace: Path) -> None:
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    while True:
        print("\n=== AI-CONTEXT Local UX ===")
        print("1. Workspace status")
        print("2. Import private source")
        print("3. Review pending candidates / apply approved candidate")
        print("4. Inspect candidate conflicts (read-only)")
        print("5. Explore memory provenance (read-only)")
        print("6. Show exactly what an AI target would receive (read-only)")
        print("7. Create portable backup archive")
        print("8. Restore portable archive to a new workspace")
        print("q. Quit")
        choice = input("Choice: ").strip().casefold()
        try:
            if choice == "1":
                _print_json(workspace_status(workspace)); _pause()
            elif choice == "2":
                source = Path(input("Source path: ").strip()).expanduser()
                mode = input("Mode [core/provider/evidence] (default core): ").strip() or "core"
                adapter = input("Adapter (blank = core auto): ").strip() or None
                plugin = Path(input("Plugin descriptor path: ").strip()).expanduser() if mode == "provider" and adapter == "plugin" else None
                if _confirm_interactive(f"Import {source} into private staging using mode={mode} adapter={adapter or 'auto'}?"):
                    _print_json(import_source(workspace, source, mode=mode, adapter=adapter, plugin=plugin, yes=True))
                else:
                    print("Cancelled.")
                _pause()
            elif choice == "3":
                _tui_review(workspace); _pause()
            elif choice == "4":
                candidate_id = input("Candidate ID: ").strip()
                _print_json(candidate_conflicts_read_only(workspace, candidate_id)); _pause()
            elif choice == "5":
                memory_id = input("Memory ID: ").strip()
                _print_json(provenance_read_only(workspace, memory_id)); _pause()
            elif choice == "6":
                profile = input("Profile (default general): ").strip() or "general"
                target = input("Target (blank = routing default): ").strip() or None
                task = input("Task (optional): ").strip() or None
                tags = {tag.strip() for tag in input("Extra tags, comma-separated (optional): ").split(",") if tag.strip()}
                _print_json(inspect_bundle_read_only(workspace, profile=profile, target=target, task=task, tags=tags)); _pause()
            elif choice == "7":
                output = Path(input("Backup path (.aicr): ").strip()).expanduser()
                mode = input("Continuity mode [minimum/full] (default minimum): ").strip() or "minimum"
                if _confirm_interactive(f"Create {mode} portable archive at {output}?"):
                    _print_json(backup_workspace(workspace, output, mode=mode, enrichment=None, yes=True))
                else:
                    print("Cancelled.")
                _pause()
            elif choice == "8":
                archive = Path(input("Archive path: ").strip()).expanduser()
                destination = Path(input("New workspace destination: ").strip()).expanduser()
                if _confirm_interactive(f"Restore {archive} into NEW workspace {destination}?"):
                    _print_json(restore_workspace(archive, destination, yes=True))
                else:
                    print("Cancelled.")
                _pause()
            elif choice in {"q", "quit", "exit"}:
                return
            else:
                print("Unknown choice.")
        except (UXError, core.ContextError, OSError, ValueError) as exc:
            print(f"error: {exc}")
            _pause()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 11 safe local UX")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="read-only workspace summary")
    status.add_argument("workspace")

    queue = sub.add_parser("queue", help="read-only curation candidate queue")
    queue.add_argument("workspace")
    queue.add_argument("--status", default="pending", choices=["pending", "approve", "approved", "reject", "revise", "applied", "all"])

    conflicts = sub.add_parser("conflicts", help="read-only candidate conflict inspection")
    conflicts.add_argument("workspace"); conflicts.add_argument("--candidate", required=True)

    provenance = sub.add_parser("provenance", help="read-only provenance explorer")
    provenance.add_argument("workspace"); provenance.add_argument("--memory", required=True)

    inspect = sub.add_parser("inspect-bundle", help="show the exact routed payload without writing a bundle")
    inspect.add_argument("workspace"); inspect.add_argument("--profile", default="general"); inspect.add_argument("--target"); inspect.add_argument("--task"); inspect.add_argument("--tags", default="")

    imp = sub.add_parser("import", help="import private source material with explicit write confirmation")
    imp.add_argument("workspace"); imp.add_argument("source"); imp.add_argument("--mode", choices=["core", "provider", "evidence"], default="core"); imp.add_argument("--adapter"); imp.add_argument("--plugin"); imp.add_argument("--yes", action="store_true")

    review = sub.add_parser("review", help="record an explicit human candidate review")
    review.add_argument("workspace"); review.add_argument("--candidate", required=True); review.add_argument("--decision", required=True, choices=["approve", "reject", "revise"]); review.add_argument("--actor-label", default="local-ux-user"); review.add_argument("--conflict-resolution", default="none", choices=["none", "coexist", "supersede_existing"]); review.add_argument("--notes", default=""); review.add_argument("--yes", action="store_true")

    apply = sub.add_parser("apply", help="apply an already approved candidate to canonical memory")
    apply.add_argument("workspace"); apply.add_argument("--candidate", required=True); apply.add_argument("--yes", action="store_true")

    backup = sub.add_parser("backup", help="one-command portable .aicr export")
    backup.add_argument("workspace"); backup.add_argument("output"); backup.add_argument("--mode", choices=["minimum", "full"], default="minimum"); backup.add_argument("--enrichment"); backup.add_argument("--yes", action="store_true")

    recover = sub.add_parser("restore", help="one-command restore into a new workspace")
    recover.add_argument("archive"); recover.add_argument("destination"); recover.add_argument("--yes", action="store_true")

    tui = sub.add_parser("tui", help="interactive local terminal interface")
    tui.add_argument("workspace")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "status":
            result = workspace_status(Path(args.workspace))
        elif args.command == "queue":
            requested = "approve" if args.status == "approved" else args.status
            rows = list_candidates(Path(args.workspace), status=requested)
            result = {"status": "ok", "read_only": True, "count": len(rows), "candidates": rows}
        elif args.command == "conflicts":
            result = candidate_conflicts_read_only(Path(args.workspace), args.candidate)
        elif args.command == "provenance":
            result = provenance_read_only(Path(args.workspace), args.memory)
        elif args.command == "inspect-bundle":
            tags = {tag.strip() for tag in args.tags.split(",") if tag.strip()}
            result = inspect_bundle_read_only(Path(args.workspace), profile=args.profile, target=args.target, task=args.task, tags=tags)
        elif args.command == "import":
            result = import_source(Path(args.workspace), Path(args.source), mode=args.mode, adapter=args.adapter, plugin=Path(args.plugin) if args.plugin else None, yes=args.yes)
        elif args.command == "review":
            result = review_candidate(Path(args.workspace), args.candidate, decision=args.decision, actor_label=args.actor_label, conflict_resolution=args.conflict_resolution, notes=args.notes, yes=args.yes)
        elif args.command == "apply":
            result = apply_candidate(Path(args.workspace), args.candidate, yes=args.yes)
        elif args.command == "backup":
            result = backup_workspace(Path(args.workspace), Path(args.output), mode=args.mode, enrichment=Path(args.enrichment) if args.enrichment else None, yes=args.yes)
        elif args.command == "restore":
            result = restore_workspace(Path(args.archive), Path(args.destination), yes=args.yes)
        elif args.command == "tui":
            run_tui(Path(args.workspace)); return 0
        else:
            raise UXError(f"unsupported UX command: {args.command}")
    except (UXError, core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
