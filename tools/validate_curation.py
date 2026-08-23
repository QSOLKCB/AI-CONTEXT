#!/usr/bin/env python3
"""Hardened Phase 5 curation validator.

Performs fail-closed schema-version checks for every curation record family before
running the original authority/hash-chain validator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ai_context
import curation
import validate_curation_legacy as legacy
from validate_curation_legacy import *  # noqa: F401,F403


_RECORD_FAMILIES = {
    "candidates": "AI-CONTEXT/CANDIDATE",
    "decisions": "AI-CONTEXT/REVIEW-DECISION",
    "conflicts": "AI-CONTEXT/CONFLICT",
    "applications": "AI-CONTEXT/APPLICATION",
    "mutations": "AI-CONTEXT/CURATION-MUTATION",
    "supersession": "AI-CONTEXT/SUPERSESSION",
    "tombstones": "AI-CONTEXT/TOMBSTONE",
    "llm_suggestions": "AI-CONTEXT/LLM-SUGGESTION",
}


def _preflight_versions(workspace: Path) -> None:
    for key, protocol in _RECORD_FAMILIES.items():
        for row in ai_context.read_jsonl(curation.curation_path(workspace, key)):
            rid = row.get("id", "<missing-id>")
            if row.get("protocol") != protocol:
                raise ai_context.ContextError(f"invalid {key} protocol: {rid}")
            if row.get("schema_version") != curation.CURATION_VERSION:
                raise ai_context.ContextError(f"unsupported {key} schema version: {rid}")


def cmd_validate(workspace: Path):
    curation.ensure_curation(workspace)
    _preflight_versions(workspace)
    return legacy.cmd_validate(workspace)


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
