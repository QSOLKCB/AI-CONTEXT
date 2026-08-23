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

## Status

Early reference implementation. The protocol should be treated as experimental until the conformance suite and migration rules reach v1.0.

See [`ROADMAP.md`](ROADMAP.md) for the staged implementation plan.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
