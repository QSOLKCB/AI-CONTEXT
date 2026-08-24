# Phase 11 local UX

AI-CONTEXT Phase 11 adds a dependency-free local operator shell over the existing Phase 1–10 protocol machinery.

The UX does **not** become a new authority layer.

```text
TUI ACTION != AUTHORITY
PREVIEW != DISCLOSURE
INSPECTOR != MUTATION
ONE-COMMAND FLOW != BYPASS
```

## Entry point

```bash
python3 tools/ux.py tui ~/my-ai-context
```

The terminal interface is intentionally line-oriented rather than dependent on curses, a browser, Electron, or a remote service. It works in ordinary terminals and over SSH and remains easy to test.

The same functions are available as non-interactive subcommands.

## Read-only operations

These operations never initialize missing governance state and never write workspace files:

```bash
python3 tools/ux.py status ~/my-ai-context
python3 tools/ux.py queue ~/my-ai-context --status pending
python3 tools/ux.py conflicts ~/my-ai-context --candidate candidate.sha256:...
python3 tools/ux.py provenance ~/my-ai-context --memory memory.sha256:...
python3 tools/ux.py inspect-bundle ~/my-ai-context \
  --profile general \
  --target provider-default \
  --task "summarize the release"
```

### Provenance explorer

`provenance` reconstructs the visible history for one canonical memory record from already-existing files:

- memory record;
- Phase 5 applications;
- candidate and review decisions;
- curation mutations;
- supersession edges;
- tombstone receipts;
- source observations;
- import receipts;
- Phase 4 source snapshots/content objects when present.

Missing evidence is reported as missing. The explorer does not repair, initialize, or rewrite anything.

### Exact bundle inspector

`inspect-bundle` uses the Phase 10 read-only routing planner and returns the exact deterministic bundle object that the selected target would receive under current policy.

It does not create a file under `bundles/`, initialize routing state, upgrade profiles, or weaken the routing policy.

The returned object contains:

```text
summary
exact_payload
```

`exact_payload` is the actual routed bundle, including record contents and diagnostics. This is deliberately more useful than a count-only preview: the operator can see exactly what would cross the boundary before choosing to send it anywhere.

## Mutating operations require confirmation

Non-interactive writes fail unless `--yes` is present.

For example:

```bash
python3 tools/ux.py import ~/my-ai-context ~/Downloads/export.zip --yes
```

Without `--yes`, the command exits without changing state.

The TUI asks the operator to type `YES` immediately before each write.

## Import helper

The UX preserves the existing importer boundaries instead of inventing a universal parser.

Core imports:

```bash
python3 tools/ux.py import ~/my-ai-context SOURCE --mode core --yes
```

This delegates to `tools/ai_context.py import` and supports the core ChatGPT/Claude/generic/repository adapters.

Provider-specific imports:

```bash
python3 tools/ux.py import ~/my-ai-context SOURCE \
  --mode provider \
  --adapter gemini \
  --yes
```

Provider adapter choices remain those of `tools/provider_import.py`: `gemini`, `grok`, `browser-chat`, and `plugin`.

Evidence imports:

```bash
python3 tools/ux.py import ~/my-ai-context SOURCE \
  --mode evidence \
  --adapter document \
  --yes
```

Evidence adapter choices remain `repo`, `document`, `drive-export`, and `email`.

## Review and canonical application

The UX keeps review and application as separate authority events.

Review:

```bash
python3 tools/ux.py review ~/my-ai-context \
  --candidate candidate.sha256:... \
  --decision approve \
  --yes
```

This records a human review decision. It does **not** write the candidate into canonical memory.

Application:

```bash
python3 tools/ux.py apply ~/my-ai-context \
  --candidate candidate.sha256:... \
  --yes
```

This delegates to the Phase 5 governed `apply` path. It requires a valid latest human/policy approval and all existing conflict/provenance rules.

In Phase 11 UX language, this application step may be described as “promotion,” but the disabled legacy `ai_context.py promote` command remains disabled.

When the TUI reviews a candidate, it can offer application immediately afterward, but it asks for a **second explicit confirmation**.

## Conflict inspection

The read-only conflict explorer calls the Phase 5 conflict detector with `persist=False`.

It may discover a conflict, but merely looking at the conflict does not append a conflict receipt or modify canonical memory. Recording a review decision still goes through the Phase 5 review path.

## One-command backup and restore

Create a portable Phase 8 archive:

```bash
python3 tools/ux.py backup ~/my-ai-context ~/backups/context.aicr \
  --mode minimum \
  --yes
```

Restore it into a new workspace:

```bash
python3 tools/ux.py restore ~/backups/context.aicr ~/restored-context --yes
```

These are UX aliases over the existing Phase 8 export/restore implementation. The UX does not define a second archive format or relaxed validation path.

The default backup mode is `minimum`, which is the dependency-closed continuity set. Users can choose `full` when they also want broader working evidence/curation history.

## Safe defaults for non-programmers

Phase 11 deliberately chooses conservative defaults:

- read-only inspection never writes or initializes state;
- non-interactive writes require `--yes`;
- interactive writes require typing `YES` at the point of action;
- review and application remain separate confirmations;
- bundle inspection defaults to the existing `general` profile and routing default target;
- bundle inspection writes no bundle file;
- backup defaults to `minimum` continuity;
- restore refuses to overwrite an existing destination;
- import choices map to existing adapters rather than guessing unknown formats;
- UX errors fail closed instead of silently repairing governance state.

## Trust boundary

The TUI is convenience code. It does not alter this authority order:

```text
source evidence
  < governed curation
  < canonical memory
  < Phase 6 disclosure policy
```

A button, menu selection, preview, signature, index hit, or tool call cannot skip the protocol rule that actually owns the operation.
