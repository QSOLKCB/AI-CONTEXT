# AI-CONTEXT Roadmap

## Phase 0 — Protocol bootstrap

- [x] Define the public/private separation between framework and user workspace.
- [x] Define raw vault, staging, canonical memory, and task-bundle trust zones.
- [x] Define core record classes and sensitivity classes.
- [x] Define SOURCE != MEMORY and RESTORED_CONTEXT != ORIGINAL_MODEL invariants.
- [x] Define initial threat model.
- [ ] Freeze protocol names and versioning rules for v0.1.

## Phase 1 — Reference local workflow

- [ ] Add dependency-light `ai_context.py` CLI.
- [ ] `init`: create a safe private workspace with `.gitignore` and default policy.
- [ ] `import`: hash source material and emit deterministic source observations plus an import receipt.
- [ ] Safe ZIP handling with traversal, member-count, and byte limits.
- [ ] Generic Markdown/text/JSON/JSONL import adapter.
- [ ] ChatGPT-style export adapter.
- [ ] Claude-style export adapter.
- [ ] Local repository adapter with extension allowlist and `.git` exclusion.
- [ ] Explicit promotion of a candidate record into canonical memory.
- [ ] Secret-pattern screening before promotion.
- [ ] Canonical-store validation.
- [ ] Deterministic profile/tag bundle builder.
- [ ] Bundle SHA-256 receipt.
- [ ] Synthetic examples only; no real user data in repository.

## Phase 2 — Schemas and conformance

- [ ] JSON Schema for workspace policy.
- [ ] JSON Schema for import receipts.
- [ ] JSON Schema for observations.
- [ ] JSON Schema for canonical memory records.
- [ ] JSON Schema for bundles.
- [ ] Conformance fixtures for valid/invalid records.
- [ ] Determinism tests for repeated bundle builds.
- [ ] Unknown adapter/schema rejection tests.
- [ ] Duplicate import/idempotency tests.
- [ ] Archive traversal and oversized-input tests.
- [ ] Secret rejection tests.
- [ ] Privacy non-downgrade tests.
- [ ] Tombstone/deletion propagation tests.

## Phase 3 — Provider import adapters

Provider export formats are inputs, not stable APIs. Each adapter must carry an id/version and explicit parse status.

- [ ] ChatGPT export fixtures and parser notes.
- [ ] Claude export fixtures and parser notes.
- [ ] Gemini export research and adapter if a stable export surface is available.
- [ ] Grok/xAI export research and adapter if a stable export surface is available.
- [ ] Generic browser-chat HTML/text archive adapter.
- [ ] Adapter plugin interface for community sources.
- [ ] Migration fixtures for provider format drift.

## Phase 4 — Repository and document context

- [ ] Git tree snapshot receipt format.
- [ ] Repository authority/precedence metadata.
- [ ] Commit/tag/release identity records.
- [ ] Markdown/document chunk observations with source line/range metadata.
- [ ] PDF ingestion boundary through an external extractor rather than hidden parser assumptions.
- [ ] Local folder adapter with MIME/extension policy.
- [ ] Optional Google Drive/export adapters.
- [ ] Optional email archive adapter.
- [ ] Duplicate-content collapse without destroying provenance.

## Phase 5 — Curation engine

- [ ] Human review queue.
- [ ] Candidate-memory generator that cannot self-promote.
- [ ] Optional local-LLM curator interface.
- [ ] Conflict detection between canonical records and new observations.
- [ ] Explicit supersession graph.
- [ ] Confidence and verification update workflow.
- [ ] Retention/expiry policy.
- [ ] Tombstone receipts.
- [ ] “Why is this remembered?” provenance explanation.

### Curation security gate

No automated semantic extractor may write directly to canonical memory. It may only propose candidate records. Promotion remains governed by explicit user or policy authority.

## Phase 6 — Selective disclosure and routing

- [ ] Named profiles such as `general`, `coding`, `research`, and user-defined profiles.
- [ ] Tag selector.
- [ ] Task selector.
- [ ] Sensitivity ceiling.
- [ ] Provider/local-model disclosure policy.
- [ ] Hard exclusions.
- [ ] Dependency expansion with fail-closed ambiguity handling.
- [ ] Minimum-context diagnostics explaining why each record entered a bundle.

## Phase 7 — Encrypted storage boundary

- [ ] Storage-backend interface.
- [ ] Document threat assumptions for filesystem encryption, age, encrypted SQLite, and hardware-backed stores.
- [ ] Reference encrypted backend using a maintained cryptographic library.
- [ ] Key rotation metadata.
- [ ] Cryptographic erasure/deletion receipt strategy.
- [ ] Recovery-key guidance that does not place keys inside AI-CONTEXT memory.

### Encryption gate

Do not invent custom cryptography. The protocol may define envelopes and key identifiers, but encryption implementations must use maintained, reviewed primitives/libraries.

## Phase 8 — Restore and migration

- [ ] Portable restore manifest.
- [ ] Minimum continuity set.
- [ ] Full working set.
- [ ] Optional style/culture enrichment class with no factual authority.
- [ ] Version migration manifest.
- [ ] Unknown-major rejection.
- [ ] Additive-compatible metadata rules.
- [ ] Cold-start restore test with no provider memory dependency.
- [ ] Cross-provider restore demonstration.

## Phase 9 — Derived indexes

- [ ] Vector index projection format.
- [ ] Graph index projection format.
- [ ] Search cache projection format.
- [ ] Derived-artifact source fingerprints.
- [ ] Stale projection rejection.
- [ ] Rebuild-after-tombstone conformance.

### Authority gate

Indexes are retrieval accelerators. They never outrank canonical memory or source provenance.

## Phase 10 — Interoperability

- [ ] Python reference implementation stabilisation.
- [ ] Rust implementation if useful for hardened local stores.
- [ ] Language-neutral conformance fixtures.
- [ ] JSON canonicalization upgrade evaluation, including RFC 8785 JCS.
- [ ] Signed bundle receipts.
- [ ] Capability manifest for model/agent consumers.
- [ ] MCP/tool adapter examples.

## Phase 11 — UX

- [ ] Local TUI for imports, review, promotion, conflicts, and bundle previews.
- [ ] Read-only provenance explorer.
- [ ] “Show me exactly what this AI will receive” bundle inspector.
- [ ] One-command export/restore flow.
- [ ] Safe defaults for non-programmers.

## v1.0 release gate

AI-CONTEXT v1.0 should not be declared until:

1. canonical schemas are versioned;
2. import receipts and canonical records pass conformance tests;
3. archive and secret-ingestion security tests pass;
4. deterministic bundle fixtures pass byte-for-byte;
5. unknown-major protocol versions fail closed;
6. deletion/tombstone propagation is tested;
7. at least two independent AI-export formats and one repository/document source can round-trip through staging;
8. a cold-start restore succeeds without provider-side memory;
9. private raw input is demonstrably absent from all public test fixtures;
10. the documentation clearly distinguishes context continuity from AI/model identity.
