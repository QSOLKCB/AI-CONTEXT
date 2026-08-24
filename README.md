# AI-CONTEXT

**A public framework for building private, portable AI memory from a user's own data.**

AI-CONTEXT is a vendor-neutral specification and reference implementation for turning private AI exports, repositories, notes, documents, Drive exports, email archives, and other user-controlled sources into curated context that can be selectively disclosed to compatible AI systems between sessions.

It is inspired by architecture used in `QSOLKCB/QSOL-CONTEXT`, but deliberately separates reusable machinery from any one person's private context.

## The core idea

```text
Private sources
  |
  |-- ChatGPT / Claude / Gemini / Grok / browser-chat exports
  |-- local or exported Git repositories
  |-- Markdown / text / JSON / JSONL / OOXML documents
  |-- Google Drive / Takeout exports
  |-- EML / MBOX email archives
  v
[ RAW VAULT ]            source material is not trusted as memory merely because it exists
  v
[ IMPORT + RECEIPTS ]    source identity, parser/layout, timestamps, provenance
  v
[ STAGING ]              observations, content objects, evidence graph
  v
[ CURATION ]             propose, review, resolve conflicts, verify, expire, tombstone
  v
[ CANONICAL MEMORY ]     typed approved records with provenance and lifecycle state
  v
[ ROUTING ]              profile + target policy + task/tag selection + dependency closure
  v
[ SELECTIVE BUNDLE ]     smallest permitted context for the current task and consumer
  v
AI / agent / local model / external provider / downstream substrate
```

Core boundaries:

```text
SOURCE MATERIAL != CANONICAL MEMORY
CANDIDATE != MEMORY
LLM SUGGESTION != REVIEW DECISION
RELEVANT != PERMITTED
DEPENDENCY != PERMISSION BYPASS
ROUTED BUNDLE != CANONICAL MEMORY
RESTORED CONTEXT != ORIGINAL MODEL INSTANCE
```

A prior AI response may contain hallucinations. A repository may be stale. An email can repeat a false claim. A task may be relevant to material that the selected provider is not allowed to receive. AI-CONTEXT therefore preserves evidence, authority, curation, and disclosure as separate layers rather than flattening everything into one retrieval store.

## Design goals

- **Private by default.** Raw exports, evidence state, curation state, routing policy, and generated bundles live in the private workspace and are ignored by Git by default.
- **User-owned.** The source of truth is the user's store, not provider-side memory.
- **Portable.** Core artifacts use UTF-8 JSON/JSONL plus deterministic hashes and receipts.
- **Vendor-neutral.** Provider export formats and model runtimes are replaceable inputs/consumers, not authorities.
- **Provenance-preserving.** Canonical memory can trace back to observations, import receipts, source snapshots, and content identities.
- **Typed memory.** Facts, preferences, project state, claims, hypotheses, instructions, relationships, publications, events, environment state, and provenance policy remain distinct classes.
- **Human/policy curation authority.** Automated extractors and local LLMs may propose. They may not self-promote.
- **Selective disclosure.** Build the smallest permitted task bundle rather than giving every model the complete private store.
- **Fail closed.** Unknown formats, ambiguous dependency references, blocked required context, malformed lifecycle state, and policy conflicts are rejected rather than guessed through.
- **Deterministic where practical.** Identical memory, profile, routing policy, target, task, and selectors produce identical routed bundle bytes under the declared canonicalizer.
- **Restorable, not mystical.** Restore reconstructs curated context. It does not recreate hidden provider state, a model identity, or private chain of thought.

## Trust zones

### 1. Raw vault

Original exports and source files. Treat as highly sensitive input. AI-CONTEXT never requires real private exports to be committed to this public framework repository.

### 2. Staging and source evidence

Parsed observations, import receipts, deterministic source snapshots, content-addressed text/binary references, and duplicate-provenance indexes. This layer is evidence, not memory.

Phase 4 documentation:

- [`docs/SOURCE-EVIDENCE.md`](docs/SOURCE-EVIDENCE.md)
- [`docs/GOOGLE-DRIVE-EXPORTS.md`](docs/GOOGLE-DRIVE-EXPORTS.md)
- [`docs/EMAIL-ARCHIVES.md`](docs/EMAIL-ARCHIVES.md)

### 3. Curation

Pending candidate records, human/policy review decisions, advisory local-LLM suggestions, conflict records, application receipts, confidence/verification mutations, supersession edges, retention policy, and tombstone receipts.

See [`docs/CURATION.md`](docs/CURATION.md).

### 4. Canonical memory

Only explicitly approved and applied records enter canonical memory. Canonical records retain type, content, sensitivity, epistemic state, confidence, provenance, tags, and lifecycle metadata.

### 5. Selective routed bundle

A deterministic task-scoped projection over canonical memory. The router decides which already-approved records a specific target may receive. It never mutates memory.

See [`docs/ROUTING.md`](docs/ROUTING.md).

## Source adapters

Provider export formats are inputs, not stable APIs. Phase 3 separates the stable AI-CONTEXT observation/receipt contract from higher-churn provider parsing.

### Core import adapters

`tools/ai_context.py` retains dependency-light Phase 1 imports for:

- ChatGPT conversation exports;
- Claude conversation exports;
- generic JSON and JSONL;
- Markdown and plain text;
- local Git/source trees.

### Higher-churn provider adapters

`tools/provider_import.py` supports:

```bash
# Google Takeout -> My Activity -> Gemini Apps
python3 tools/provider_import.py \
  ~/my-ai-context \
  ~/Downloads/MyActivity.json \
  --adapter gemini

# xAI/Grok account export
python3 tools/provider_import.py \
  ~/my-ai-context \
  ~/Downloads/prod-grok-backend.json \
  --adapter grok

# Browser-saved or third-party HTML/text chat archives
python3 tools/provider_import.py \
  ~/my-ai-context \
  ~/Downloads/chat-export.html \
  --adapter browser-chat
```

Every provider receipt separates:

```text
adapter.id
adapter.version
adapter_layout
parse_status
```

Provider format notes and synthetic drift fixtures live under `docs/providers/` and `fixtures/provider-drift/`.

### Community adapter plugins

Phase 3 also defines a data-only JSON mapping plugin interface:

```bash
python3 tools/provider_import.py \
  ~/my-ai-context \
  export.json \
  --adapter plugin \
  --plugin my-adapter.json
```

Plugins map fields with constrained JSON descriptors. They do not execute provider code, Python hooks, shells, package installers, or arbitrary expressions.

## Phase 4 evidence ingestion

Use `tools/evidence_import.py` when repository/document provenance matters beyond the older generic import path:

```bash
# Git/source tree with snapshot identity and line-addressed chunks
python3 tools/evidence_import.py ~/my-ai-context ~/src/project --adapter repo

# Document tree or standalone text/OOXML/PDF reference
python3 tools/evidence_import.py ~/my-ai-context ~/Documents/research --adapter document

# Google Drive / Takeout export
python3 tools/evidence_import.py ~/my-ai-context ~/Downloads/Takeout --adapter drive-export

# EML / MBOX / email export directory
python3 tools/evidence_import.py ~/my-ai-context ~/Downloads/mail.mbox --adapter email
```

Phase 4 keeps duplicate content collapsed without destroying independent provenance paths.

## Phase 5 curation

The public legacy `promote` command is disabled. New canonical memory uses the governed curation path:

```bash
# Create a pending candidate from staged evidence
python3 tools/curation.py propose \
  ~/my-ai-context \
  --observation obs.sha256:... \
  --record-type project_state \
  --semantic-key project:alpha

# Inspect pending candidates
python3 tools/curation.py queue ~/my-ai-context

# Approve with explicit human or policy authority
python3 tools/curation.py review \
  ~/my-ai-context \
  --candidate candidate.sha256:... \
  --decision approve \
  --actor-type human \
  --actor-label local-user

# Apply the reviewed candidate to canonical memory
python3 tools/curation.py apply \
  ~/my-ai-context \
  --candidate candidate.sha256:...
```

A local LLM can receive/import curator envelopes, but even an LLM recommendation of `approve` is advisory only. Canonical application still requires a separate human/policy review decision.

Other Phase 5 commands cover conflicts, supersession, verification/confidence updates, retention, tombstones, and `explain` provenance tracing.

## Phase 6 selective disclosure and routing

Fresh workspaces receive:

```text
profiles/general.json
routing/policy.json
```

The default routing policy separates:

- `local-default`: local-model target with a `private` ceiling;
- `provider-default`: external-provider target with a `public` ceiling and Phase 5 application-receipt requirement.

Build a task-scoped local bundle:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --task "debug the Rust parser" \
  --target local-default \
  --output /tmp/local-context.json
```

Build a conservative provider bundle:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --task "summarize public release history" \
  --target provider-default \
  --output /tmp/provider-context.json
```

Legacy tag selection remains available:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --tags coding,alpha \
  --output /tmp/tag-context.json
```

Task selection is deterministic lexical matching plus **explicit profile-declared semantic aliases**. No hidden embedding or model call decides whether private memory should be disclosed.

Hard exclusions can block exact memory IDs, record types, epistemic states, source observation IDs, and exact dotted content paths.

Required memory dependencies are declared by canonical `relationship` records using `requires` or `depends_on`. Semantic dependency endpoints must resolve to exactly one active memory. Missing or ambiguous endpoints fail closed.

Dependencies may bypass positive task/tag relevance only. They never bypass target policy, sensitivity ceilings, lifecycle state, approval, or hard exclusions.

Every routed record includes minimum-context diagnostics such as:

```json
{
  "memory_id": "memory....",
  "included_by": ["task_selector"],
  "task_matches": ["parser", "rust"],
  "tag_matches": [],
  "dependency_of": [],
  "dependency_depth": null
}
```

Diagnostics explain included records only. Withheld records are not enumerated into the bundle.

## Validation

Validate the core workspace:

```bash
python3 tools/ai_context.py validate ~/my-ai-context
```

Validate Phase 4 evidence:

```bash
python3 tools/validate_evidence.py ~/my-ai-context
```

Validate Phase 5 curation authority/history:

```bash
python3 tools/validate_curation.py ~/my-ai-context
```

Validate Phase 6 routing configuration:

```bash
python3 tools/validate_routing.py ~/my-ai-context
```

Validate that a previously built bundle still exactly matches current memory/profile/policy/task/target state:

```bash
python3 tools/validate_routing.py \
  ~/my-ai-context \
  --bundle /tmp/local-context.json
```

## Memory record model

A durable record carries more than text:

- stable `id`;
- `record_type`;
- structured `content`;
- `sensitivity`;
- `confidence`;
- `epistemic_state`;
- source observation references;
- approval state;
- tags;
- lifecycle/expiry state;
- creation and verification metadata.

This preserves distinctions such as:

```text
PREFERENCE != FACT
PROJECT_STATE != SCIENTIFIC CLAIM
AI SAID IT != VERIFIED
PRIVATE != SAFE TO DISCLOSE
SOURCE IMPORTED != MEMORY APPROVED
RELEVANT != PERMITTED
DELETED != MERELY HIDDEN
```

## Repository layout

```text
spec/                    protocol and JSON schemas
docs/                    architecture, threat model, curation/routing, provider notes
tools/                   dependency-light reference CLIs
fixtures/conformance/    standalone valid/invalid protocol fixtures
fixtures/provider-drift/ synthetic provider migration fixtures
tests/                   conformance, security, evidence, curation, routing tests
```

A real user workspace should live outside this public framework repository or in a separately controlled private location.

## Security posture

AI-CONTEXT is **not** a password manager. Do not intentionally preserve credentials, private keys, session cookies, recovery codes, bearer tokens, or similar secrets as AI memory.

Secret-like material is rejected from canonical memory by policy. Routing adds a separate disclosure boundary and does not treat relevance as permission.

Encryption-at-rest remains a storage-backend responsibility in the current reference implementation. Phase 7 tracks the hardened encrypted-storage boundary.

See [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md).

## Relationship to QSOL-CONTEXT

`QSOLKCB/QSOL-CONTEXT` is an opinionated private implementation containing a specific user's identity, projects, research, chronology, restore machinery, provenance decisions, and routing policy.

AI-CONTEXT extracts transferable structures such as:

- selective loading;
- deterministic bundles;
- typed epistemic records;
- provenance and honesty boundaries;
- restore/context continuity without model-identity claims;
- source precedence;
- explicit exclusions;
- profile and target routing;
- receipts and fingerprints.

It intentionally does **not** inherit QSOL-specific identity, ontology, project names, cultural artifacts, or private data.

## Using AI-CONTEXT with QSOL-SUBSTRATE

[`QSOLKCB/QSOL-SUBSTRATE`](https://github.com/QSOLKCB/QSOL-SUBSTRATE) remains an optional downstream companion for substrate-style delivery machinery such as model adapters, tool-less capsules, deterministic vector projections, model-specific prefix/latent experiments, and model-behaviour probes.

The scope split is intentional:

```text
AI-CONTEXT
  private ingestion
  evidence + provenance
  curation / approval
  canonical memory
  selective disclosure / routing
        |
        | explicit routed bundle only
        v
QSOL-SUBSTRATE-style downstream layer
  transport adapters
  tool-less capsules
  vector/retrieval projections
  prefix / latent experiments
  probe/evaluation machinery
```

Authority rule:

> **AI-CONTEXT CANONICAL MEMORY > ROUTED BUNDLE > DOWNSTREAM SUBSTRATE PROJECTION**

A downstream vector index, adapter, latent prefix, capsule, or probe result is derived. It must not silently rewrite, reclassify, promote, or outrank AI-CONTEXT canonical memory.

QSOL-SUBSTRATE is currently a QSOL-specific public implementation, not a generic drop-in AI-CONTEXT backend. Use a purpose-built private bridge and hand off only an explicitly routed bundle. Never point a public-export workflow at the AI-CONTEXT vault, staging, curation, or canonical-memory directories.

## Status

Early reference implementation. **Phases 0-6 are complete.** The protocol remains experimental until restore/migration, encrypted-storage, derived-index, and broader interoperability gates reach the v1.0 release criteria in [`ROADMAP.md`](ROADMAP.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).
