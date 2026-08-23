# AI-CONTEXT

**A public framework for building private, portable AI memory from a user's own data.**

AI-CONTEXT is a vendor-neutral specification and reference implementation for turning private AI exports, repositories, notes, documents, and other user-controlled sources into a curated context store that can be supplied to compatible AI systems between sessions.

It is inspired by the architecture used in `QSOLKCB/QSOL-CONTEXT`, but deliberately separates the reusable machinery from any one person's private context.

## The core idea

```text
Private sources
  |
  |-- ChatGPT / Claude / Gemini / Grok / other AI exports
  |-- local or exported Git repositories
  |-- Markdown / text / JSON / JSONL notes
  |-- project records, publications, preferences, chronology
  v
[ RAW VAULT ]            never trusted as memory merely because it exists
  v
[ IMPORT + RECEIPTS ]    hash, source identity, parser, timestamp, provenance
  v
[ STAGING ]              observations and candidate records
  v
[ CURATION ]             classify, redact, approve, correct, expire, tombstone
  v
[ CANONICAL MEMORY ]     typed records with provenance and privacy policy
  v
[ SELECTIVE BUNDLES ]    smallest sufficient context for the current task
  v
AI / agent / local model / future provider
```

The important boundary is:

> **SOURCE MATERIAL != CANONICAL MEMORY**

A prior AI response may contain hallucinations. A repository may contain stale state. A note may be speculative. AI-CONTEXT therefore preserves the difference between imported evidence and curated memory instead of flattening everything into a vector database and hoping for the best.

## Design goals

- **Private by default.** Raw exports and generated private bundles are excluded from Git by default.
- **User-owned.** The source of truth is the user's store, not a provider account or model-side memory feature.
- **Portable.** Context is plain UTF-8 JSON/JSONL plus deterministic receipts and bundles.
- **Vendor-neutral.** Provider export adapters are inputs, not authorities.
- **Provenance-preserving.** Every promoted memory can retain references to the source observations that support it.
- **Typed memory.** Facts, preferences, project state, claims, hypotheses, instructions, events, relationships, and provenance are distinct record classes.
- **Selective disclosure.** Build the smallest context bundle required for a task rather than dumping a complete life archive into every prompt.
- **Fail closed.** Secrets, ambiguous provenance, unsupported promotion, malformed imports, and policy violations should be rejected rather than guessed through.
- **Deterministic where practical.** Identical canonical records plus identical profile selection should produce identical bundle bytes under the declared canonicalizer.
- **Restorable, not mystical.** A restore reconstructs curated working context. It does not recreate a previous model instance, hidden chain of thought, or provider-private state.

## Trust zones

### 1. Raw vault

Original exports and source files. Treat this as highly sensitive input. AI-CONTEXT never requires raw exports to be committed to Git.

### 2. Staging

Parsed observations plus import receipts. Staged data is still not canonical memory. It may include wrong AI answers, duplicate conversations, transient details, or sensitive material.

### 3. Canonical memory

Only records deliberately promoted by the user or an explicitly configured curator enter this layer. Canonical records carry a record type, sensitivity, confidence, provenance references, and lifecycle metadata.

### 4. Task bundle

A deterministic, task-scoped projection of approved memory. A bundle can be handed to a model without handing it the entire private store.

## Source adapters

The reference CLI is designed around adapters rather than one fixed export format. Initial targets are:

- ChatGPT-style exports containing conversation JSON;
- Claude-style conversation exports;
- generic JSON and JSONL archives;
- Markdown and plain-text collections;
- local Git working trees and exported repositories;
- future adapters for Gemini, Grok, NotebookLM, email archives, cloud-drive exports, and user-defined sources.

Provider export formats change. Adapters therefore emit explicit parser/version receipts and must never silently reinterpret an unknown format as a known one.

## Memory record model

A durable record answers more than “what text should the AI remember?” It records:

- stable `id`;
- `record_type`;
- structured `content`;
- `sensitivity`;
- `confidence`;
- `epistemic_state`;
- source observation references;
- user approval state;
- tags / routing hints;
- retention or expiry policy;
- creation and verification metadata.

This allows a system to preserve distinctions such as:

```text
PREFERENCE != FACT
PROJECT_STATE != SCIENTIFIC CLAIM
AI SAID IT != VERIFIED
PRIVATE != SAFE TO DISCLOSE
SOURCE IMPORTED != MEMORY APPROVED
DELETED != MERELY HIDDEN
```

## Repository layout

```text
spec/                 protocol and JSON schemas
docs/                 architecture, threat model and format notes
tools/                dependency-light reference CLI
fixtures/conformance/ standalone valid/invalid protocol fixtures
examples/             synthetic examples only
tests/                conformance and security tests
workspace.example/    safe example private-workspace structure
```

A real user workspace should live outside this public framework repository or in a separately controlled private location.

## Intended workflow

```bash
# Create a private workspace
python3 tools/ai_context.py init ~/my-ai-context

# Import an AI export into staging
python3 tools/ai_context.py import ~/my-ai-context ~/Downloads/ai-export.zip --adapter auto

# Import a local repository as another source
python3 tools/ai_context.py import ~/my-ai-context ~/src/my-project --adapter repo

# Inspect staged observations and explicitly promote selected material
python3 tools/ai_context.py promote ~/my-ai-context --input candidate.json

# Validate the canonical store
python3 tools/ai_context.py validate ~/my-ai-context

# Build the smallest approved bundle for a profile/task
python3 tools/ai_context.py bundle ~/my-ai-context --profile coding --output /tmp/context.json
```

The CLI in the first reference implementation intentionally keeps promotion explicit. Automated semantic extraction can be added later behind a policy boundary without changing the trust model.

## Security posture

AI-CONTEXT is **not** a password manager and must not be used to preserve credentials, private keys, session cookies, recovery codes, or bearer tokens as AI memory. Secret-like material is rejected from canonical memory by policy.

Encryption-at-rest is a storage-backend responsibility in the initial reference implementation. The protocol is designed so encrypted vault implementations can be swapped in without changing canonical record semantics.

See [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md).

## Relationship to QSOL-CONTEXT

`QSOLKCB/QSOL-CONTEXT` is an opinionated private implementation containing a specific user's identity, projects, research, chronology, restore machinery, provenance decisions, and routing policy.

AI-CONTEXT extracts the transferable ideas:

- selective loading;
- deterministic canonical bundles;
- typed epistemic records;
- provenance and honesty boundaries;
- restore/context continuity without claiming model identity;
- source precedence;
- explicit exclusions;
- profile-based routing;
- receipts and fingerprints.

It intentionally does **not** inherit QSOL-specific identity, ontology, project names, cultural artifacts, or private data.

## Using AI-CONTEXT with QSOL-SUBSTRATE

[`QSOLKCB/QSOL-SUBSTRATE`](https://github.com/QSOLKCB/QSOL-SUBSTRATE) is a useful optional downstream companion when a project needs substrate-style delivery machinery such as model adapters, tool-less capsules, deterministic vector projections, model-specific prefix/latent experiments, or model-behaviour probes.

The scope split is intentional:

```text
AI-CONTEXT
  private ingestion
  provenance + receipts
  curation / approval
  canonical personal memory
  privacy / lifecycle policy
  selective task bundles
        |
        | explicit approved handoff only
        v
QSOL-SUBSTRATE-style downstream layer
  transport adapters
  tool-less capsules
  vector/retrieval projections
  prefix / latent experiments
  probe and evaluation machinery
        |
        v
AI / agent / local model / provider
```

The authority rule is:

> **AI-CONTEXT CANONICAL MEMORY > DOWNSTREAM SUBSTRATE PROJECTION**

A downstream adapter, vector index, latent prefix, KV/prefix state, capsule, or probe result is a derived artifact. It may change delivery format or retrieval strategy, but it must not silently rewrite, reclassify, promote, or outrank the canonical AI-CONTEXT records that produced it.

### Recommended handoff

1. Import and curate private material in AI-CONTEXT.
2. Build the smallest approved bundle for the downstream use case:

   ```bash
   python3 tools/ai_context.py bundle \
     ~/my-ai-context \
     --profile coding \
     --output /tmp/ai-context-coding.json
   ```

3. Use a **separate bridge/adapter or private substrate workspace** to map only the approved bundle records into the canonical record layout expected by the substrate implementation.
4. Run the desired QSOL-SUBSTRATE-derived projection tooling against that downstream substrate snapshot.
5. Preserve source IDs, sensitivity, epistemic state, provenance references, AI-CONTEXT bundle hash, and substrate/projection hashes in the bridge receipt so the path can be audited in both directions.

### Important compatibility note

QSOL-SUBSTRATE is currently a **QSOL-specific public substrate implementation**, not a generic drop-in AI-CONTEXT backend. Its existing builders expect the QSOL-SUBSTRATE canonical repository layout; there is intentionally no fake `ai-context bundle -> qsol-substrate` one-command converter in this repository.

For private personal context, do **not** point QSOL-SUBSTRATE's public-export workflow at an AI-CONTEXT vault, staging area, or canonical store and assume that makes the result safe. Use a private fork/worktree or purpose-built bridge with an explicit allowlist and privacy policy. Raw vault material and staging observations should never cross this boundary automatically.

The reusable QSOL-SUBSTRATE architecture already demonstrates downstream patterns for:

```bash
python tools/validate_substrate.py --json-report validation-report.json
python tools/fingerprint_substrate.py --output substrate-fingerprint.json
python tools/build_adapters.py --source-commit "$(git rev-parse HEAD)" --output dist/adapters
python tools/build_toolless.py --source-commit "$(git rev-parse HEAD)" --output dist/toolless
python tools/build_vectors.py --source-commit "$(git rev-parse HEAD)" --output dist/vectors
python tools/build_projections.py --source-commit "$(git rev-parse HEAD)" --output dist/projections
python tools/build_probes.py --source-commit "$(git rev-parse HEAD)" --output dist/probes
```

Those commands belong to QSOL-SUBSTRATE and operate on its substrate format. AI-CONTEXT deliberately does not reimplement them. This keeps the projects cleanly separated:

```text
AI-CONTEXT = private memory protocol and authority
QSOL-SUBSTRATE = downstream substrate/projection technology
bridge = explicit policy-controlled translation boundary
```

## Status

Early reference implementation. Phases 0–2 are complete; the protocol should still be treated as experimental until the migration and broader interoperability rules reach v1.0.

See [`ROADMAP.md`](ROADMAP.md) for the staged implementation plan.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
