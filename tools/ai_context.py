#!/usr/bin/env python3
"""Phase 5-gated public AI-CONTEXT CLI.

The Phase 1 implementation is retained in ai_context_legacy.py for internal compatibility
and conformance tests. New canonical promotion through the public CLI is disabled; use
tools/curation.py propose/review/apply instead.
"""

from __future__ import annotations

from pathlib import Path

import ai_context_legacy as legacy
from ai_context_legacy import *  # noqa: F401,F403


def _ensure_curation_ignored(workspace: Path) -> None:
    path = workspace / ".gitignore"
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if "curation/" not in lines:
        lines.append("curation/")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def cmd_init(args) -> None:
    legacy._PHASE5_ORIGINAL_CMD_INIT(args)
    workspace = Path(args.workspace).expanduser().resolve()
    _ensure_curation_ignored(workspace)


def cmd_promote(args) -> None:
    raise ContextError(
        "legacy promote is disabled by the Phase 5 curation security gate; "
        "use tools/curation.py propose, review, and apply"
    )


legacy._PHASE5_ORIGINAL_CMD_INIT = legacy.cmd_init
legacy.cmd_init = cmd_init
legacy.cmd_promote = cmd_promote


def main() -> int:
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
