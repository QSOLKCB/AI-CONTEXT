# AI-CONTEXT v1.0.0 — Release Notes

## Release identity

- Release: `v1.0.0`
- Reference protocol version: `0.1.0`
- Canonicalizer: `python-json-v0.1`
- RFC 8785 JCS: evaluated, not adopted for v1.0.0
- License: Apache-2.0
- Release commit: the exact commit resolved by the immutable `v1.0.0` tag
- Formalization target: that same `v1.0.0` tag commit after the final exact-main CI/audit gate

## What v1.0.0 is

AI-CONTEXT is a vendor-neutral framework and reference implementation for building private, portable, governed AI context memory from user-controlled sources.

It separates source evidence, curation authority, canonical memory, disclosure policy, persistence, restore, derived retrieval, interoperability, and operator UX rather than treating them as one undifferentiated memory feature.

## Architecture status

Architecture Phases 0–11 are complete:

1. protocol bootstrap;
2. reference local workflow;
3. schemas and conformance;
4. provider import adapters;
5. repository/document evidence context;
6. curation engine;
7. selective disclosure and routing;
8. encrypted storage boundary;
9. restore and migration;
10. derived indexes;
11. interoperability;
12. local operator UX.

(The roadmap numbers these as Phases 0 through 11.)

## Core authority invariants

```text
SOURCE MATERIAL != CANONICAL MEMORY
CANDIDATE != MEMORY
LLM SUGGESTION != REVIEW DECISION
RELEVANT != PERMITTED
DEPENDENCY != PERMISSION BYPASS
ROUTED BUNDLE != CANONICAL MEMORY
ENCRYPTION AT REST != MEMORY AUTHORITY
KEY ID != KEY MATERIAL
RESTORE != MODEL IDENTITY
STYLE/CULTURE != FACTUAL AUTHORITY
INDEX HIT != MEMORY AUTHORITY
SIGNATURE != DISCLOSURE AUTHORITY
SIGNATURE != EPISTEMIC AUTHORITY
CAPABILITY CLAIM != PERMISSION
MCP TOOL != MEMORY AUTHORITY
TUI ACTION != AUTHORITY
PREVIEW != DISCLOSURE
INSPECTOR != MUTATION
```

## Major capabilities

### Import and evidence

- ChatGPT- and Claude-style export ingestion.
- Gemini Takeout/My Activity, Grok account export, browser-chat, and data-only community adapter support.
- Repository/document, Drive/export, and email evidence ingestion.
- Content-addressed observations, receipts, source snapshots, document ranges, and duplicate provenance preservation.

### Curation

- Candidate generation cannot self-promote.
- Human/policy review and application remain separate authority events.
- Local LLM curation is advisory only.
- Conflict detection, supersession, verification/confidence mutation, retention, expiry, tombstones, and provenance explanations are receipted.

### Selective disclosure

- Profiles, tag/task selectors, sensitivity ceilings, provider/local-target policies, hard exclusions, and dependency closure.
- Dependencies may bypass relevance only, never disclosure permission.
- Bundle diagnostics explain why included records entered a bundle without publishing an inventory of withheld private memory.

### Persistence and restore

- Optional AES-256-GCM encrypted-directory storage using the maintained `cryptography` package.
- External key custody and resumable rotation.
- Conservative deletion/key-destruction receipts that do not overclaim erasure.
- Deterministic `.aicr` minimum/full continuity archives.
- Cold-start restore with no provider-memory dependency.
- Restore continuity is context reconstruction, not model identity reconstruction.

### Derived retrieval

- Deterministic vector, graph, and lexical search projections.
- Source fingerprints make stale projections unusable.
- Tombstoned/ineligible records disappear from rebuilt retrieval projections while canonical history remains.
- Index hits remain candidate retrieval only and return to Phase 6 routing before disclosure.

### Interoperability

- Stabilized Python reference surface.
- Language-neutral conformance corpus.
- Independent Rust verifier.
- Ed25519 signed bundle integrity receipts.
- Capability manifests for model/agent/tool consumers.
- Read-only MCP/generic tool adapter examples.

### Local UX

- Dependency-free terminal operator shell.
- Read-only status, provenance, conflict, and exact bundle inspection.
- Explicit review/application confirmations.
- One-command backup and restore delegating to the Phase 8 format.

## Canonicalization decision

v1.0.0 deliberately retains `python-json-v0.1`.

RFC 8785 JCS was evaluated during Phase 10 but is not introduced in this release because changing canonicalization would change existing identifiers and hashes. A future JCS adoption requires an explicitly versioned migration and new language-neutral conformance vectors.

See `docs/JCS-EVALUATION.md` and `release/v1-freeze.json`.

## Release-candidate public-tree audit

The release tree is gated by:

```bash
python3 tools/release_audit.py .
```

The audit examines the exact Git-tracked tree and rejects detected private workspace paths, raw/generated release artifacts, credentials/private keys, recovery/key-store material, tracked symlinks, common secret-shaped text, and generated cache/build debris.

A passing audit makes the bounded claim:

```text
no-forbidden-private-or-runtime-artifacts-detected-in-tracked-tree
```

See `docs/RELEASE-CANDIDATE.md` for scope and limitations.

## Reproduction / conformance gates

The release candidate must pass:

```bash
python -m unittest discover -s tests -v
python tools/interop.py validate-fixtures fixtures/interoperability/conformance-v0.1.0.json
cargo test --manifest-path rust/ai-context-interop/Cargo.toml
cargo run --quiet --manifest-path rust/ai-context-interop/Cargo.toml -- capabilities fixtures/interoperability/capability-manifest.python.json
cargo run --quiet --manifest-path rust/ai-context-interop/Cargo.toml -- validate-fixtures fixtures/interoperability/conformance-v0.1.0.json
python tools/release_audit.py .
```

## Known limits

AI-CONTEXT does not claim to:

- recreate an original assistant/model instance, hidden reasoning state, or provider-side memory;
- prove truth merely because a record is remembered, indexed, signed, encrypted, or restored;
- function as a password manager or secret vault;
- provide universal deletion proof across backups/external copies;
- provide perfect PII/secret detection;
- prove the cryptographic primitives supplied by maintained libraries;
- make an embedded signing public key an independent identity/trust proof;
- make a TUI, MCP tool, capability manifest, or index result a new protocol authority.

## Post-tag scholarly work

After `v1.0.0` is frozen, Phase 12 formalizes selected protocol invariants in Lean 4 against that exact immutable tag commit. The later Zenodo record will contain three clean public uploads:

1. `AI-CONTEXT-1.0.0-source.zip`
2. `AI-CONTEXT-v1.0.0-Overview.pdf`
3. `RELEASE-NOTES.md`

The archival source ZIP will distinguish the exact tagged reference tree from the post-tag Lean formalization while binding both to the same immutable v1.0.0 target.
