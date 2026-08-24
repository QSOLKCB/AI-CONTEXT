# AI-CONTEXT Roadmap

## Phase 0 — Protocol bootstrap

- [x] Define the public/private separation between framework and user workspace.
- [x] Define raw vault, staging, canonical memory, and task-bundle trust zones.
- [x] Define core record classes and sensitivity classes.
- [x] Define SOURCE != MEMORY and RESTORED_CONTEXT != ORIGINAL_MODEL invariants.
- [x] Define initial threat model.
- [x] Freeze initial protocol names and schema version at experimental v0.1.0.

## Phase 1 — Reference local workflow

- [x] Add dependency-light `ai_context.py` CLI.
- [x] `init`: create a safe private workspace with `.gitignore` and default policy.
- [x] `import`: hash source material and emit source observations plus an import receipt.
- [x] Safe ZIP handling with traversal, member-count, and byte limits.
- [x] Generic Markdown/text/JSON/JSONL import adapter.
- [x] ChatGPT-style export adapter.
- [x] Claude-style export adapter.
- [x] Local repository adapter with extension allowlist and `.git` exclusion.
- [x] Explicit promotion of a candidate record into canonical memory.
- [x] Secret-pattern screening before promotion.
- [x] Canonical-store validation.
- [x] Deterministic profile/tag bundle builder.
- [x] Bundle SHA-256 receipt.
- [x] Synthetic test data only; no real user data in repository.

## Phase 2 — Schemas and conformance

- [x] JSON Schema for workspace policy.
- [x] JSON Schema for import receipts.
- [x] JSON Schema for observations.
- [x] JSON Schema for canonical memory records.
- [x] JSON Schema for bundles.
- [x] Standalone conformance fixture files for valid/invalid records.
- [x] Determinism tests for repeated bundle builds.
- [x] Unknown adapter/schema rejection tests.
- [x] Duplicate import/idempotency tests.
- [x] Archive traversal test.
- [x] Oversized-input tests.
- [x] Secret rejection tests.
- [x] Privacy non-downgrade tests.
- [x] Tombstone/deletion propagation tests.

**Phase 2 complete.** Draft 2020-12 schema fixtures are validated independently of the runtime CLI, while protocol tests cover runtime invariants JSON Schema cannot express. Tombstones remain in canonical history but are excluded from newly built disclosure bundles; tombstone receipts themselves remain a Phase 5 responsibility.

## Phase 3 — Provider import adapters

Provider export formats are inputs, not stable APIs. Each adapter must carry an id/version and explicit parse status.

- [x] Synthetic ChatGPT-style export parser test.
- [x] Synthetic Claude-style export parser test.
- [x] Provider format notes and drift fixtures for ChatGPT.
- [x] Provider format notes and drift fixtures for Claude.
- [x] Gemini export research and adapter for the official Takeout/My Activity surface, with variable schema treated as partial/unstable.
- [x] Grok/xAI export research and adapter for the official account-data surface, with `prod-grok-backend.json` treated as undocumented/unstable.
- [x] Generic browser-chat HTML/text archive adapter.
- [x] Data-only adapter plugin interface for community JSON sources.
- [x] Migration fixtures and machine-readable manifest for provider format drift.

**Phase 3 complete.** ChatGPT and Claude retain the Phase 1 import path but are now protected by exact/partial/reject migration fixtures. Higher-churn providers use `tools/provider_import.py`, which normalizes Gemini Takeout, Grok account exports, browser chat archives, and data-only community plugin mappings into the same Phase 1 staging/receipt contract. Provider layout identity is recorded separately from adapter id/version. Unknown layouts fail closed; known lossy layouts remain explicitly `partial`. Provider-private reasoning-like fields are not silently promoted into ordinary staging observations.

## Phase 4 — Repository and document context

- [x] Git tree snapshot receipt format including commit identity when available.
- [x] Repository authority/precedence metadata.
- [x] Commit/tag/release identity records.
- [x] Markdown/document chunk observations with source line/range metadata.
- [x] PDF ingestion boundary documented as external extraction rather than hidden parser assumptions.
- [x] Local source-tree adapter with extension policy.
- [x] Optional Google Drive/export adapters.
- [x] Optional email archive adapter.
- [x] Duplicate-content collapse without destroying provenance.

**Phase 4 complete.** `tools/evidence_import.py` creates deterministic source snapshots, source-evidence authority metadata, exact Git commit/tree/tag identities, conservative unverified release identities, line-addressed document chunks, Drive/Takeout evidence, RFC 5322/MIME email evidence, and content-addressed duplicate collapse. Unique payloads live once in `staging/content.jsonl`; independent source observations continue to preserve path/range/message provenance and are joined through the deterministic `staging/content-index.json`. `tools/validate_evidence.py` verifies snapshot/content identities, receipt cardinality, content references, and duplicate-provenance groups. Source authority ranks are precedence hints inside the source-evidence domain and never imply that a source claim is true.

## Phase 5 — Curation engine

- [x] Human review queue.
- [x] Candidate-memory generator that cannot self-promote.
- [x] Optional local-LLM curator interface.
- [x] Conflict detection between canonical records and new observations.
- [x] Explicit supersession graph.
- [x] Confidence and verification update workflow.
- [x] Retention/expiry policy enforcement.
- [x] Tombstone receipts.
- [x] “Why is this remembered?” provenance explanation.

**Phase 5 complete.** `tools/curation.py` implements a pending candidate queue, human/policy review decisions, explicit candidate application, advisory-only local-LLM request/response envelopes, semantic-key conflict detection, supersession edges, receipted confidence/verification/lifecycle mutations, retention policy enforcement, tombstone receipts, and provenance explanations that trace canonical memory back through curation, observations, import receipts, Phase 4 source snapshots, and content objects. `tools/validate_curation.py` validates candidate identities, review chains, application authority, mutation chains, supersession/tombstone receipts, and the rule that LLM suggestions never count as human/policy approval.

### Curation security gate

No automated semantic extractor or local LLM may write directly to canonical memory. It may only propose pending candidates or advisory suggestions. The reference `apply` path requires a latest explicit `approve` decision whose actor is `human` or `policy`. Content corrections create new canonical records plus supersession edges; curation mutations cannot silently rewrite content, sensitivity, record type, tags, or provenance.

## Phase 6 — Selective disclosure and routing

- [x] Profile format and default `general` profile.
- [x] Tag selector.
- [x] Task/semantic selector.
- [x] Sensitivity ceiling.
- [x] Provider/local-model disclosure policy.
- [x] Hard exclusions beyond sensitivity/tag policy.
- [x] Dependency expansion with fail-closed ambiguity handling.
- [x] Minimum-context diagnostics explaining why each record entered a bundle.

**Phase 6 complete.** `tools/routing.py` turns the public `bundle` command into a deterministic disclosure router. Profiles combine record-type/tag/sensitivity policy with lexical task selection and explicit semantic aliases. `routing/policy.json` defines separate provider and local-model targets, confidence/verification gates, optional Phase 5 application requirements, and hard exclusions for record ids/types, epistemic states, source observations, and exact content paths. Required dependencies are declared by active canonical `relationship` records using `depends_on` or `requires`; exact memory endpoints resolve directly while semantic-key endpoints must resolve to exactly one active record or routing fails closed. Dependency expansion bypasses only positive relevance selectors and never bypasses approval, lifecycle, sensitivity, target policy, or hard exclusions. Routed bundles carry deterministic per-record diagnostics explaining direct tag/task selection and dependency inclusion without listing withheld records. `tools/validate_routing.py` recomputes routing decisions and rejects stale or policy-divergent bundles.

### Disclosure security gate

Routing is a read-only projection over canonical memory. Task selection and dependency expansion may establish relevance, but they never grant disclosure permission. A required dependency that violates the profile, target, sensitivity, lifecycle, approval, or hard-exclusion boundary causes bundle construction to fail instead of silently leaking or silently omitting required context.

## Phase 7 — Encrypted storage boundary

- [x] Storage-backend interface.
- [x] Document that encryption-at-rest is a storage-backend responsibility in the initial implementation.
- [x] Document comparative threat assumptions for filesystem encryption, age, encrypted SQLite, and hardware-backed stores.
- [x] Reference encrypted backend using a maintained cryptographic library.
- [x] Key rotation metadata.
- [x] Cryptographic erasure/deletion receipt strategy.
- [x] Recovery-key guidance that does not place keys inside AI-CONTEXT memory.

**Phase 7 complete.** `tools/storage.py` defines a storage-backend protocol plus plaintext filesystem and encrypted-directory implementations. The encrypted reference backend uses AES-256-GCM from the maintained `cryptography` package, stores only non-secret SHA-256-derived key identifiers, keeps key files outside the store, authenticates protocol/algorithm/key/path/store metadata as AEAD associated data, and hides plaintext logical paths inside ciphertext. Rotation is journalled and normal access fails closed while a rotation is incomplete; object envelopes carry their actual key id so interrupted rotations can resume using the old and new external keys. The storage manifest records active/retired key history and replacement relationships. Primary ciphertext deletion emits a conservative receipt only after durable object removal and explicitly does not claim key destruction; optional key-destruction receipts are labelled self-attested external actions rather than cryptographic proof. `docs/STORAGE.md` and `docs/ENCRYPTION-THREAT-MODELS.md` document filesystem/full-disk encryption, age, encrypted SQLite, hardware-backed custody, recovery guidance, metadata leakage, live-process limits, backup semantics, and cryptographic-erasure boundaries.

### Encryption gate

Do not invent custom cryptography. The protocol may define envelopes and key identifiers, but encryption implementations must use maintained, reviewed primitives/libraries. Keys and recovery material remain external capabilities and never become AI-CONTEXT canonical memory, curation state, routing data, storage metadata, or public fixtures.

## Phase 8 — Restore and migration

- [x] Portable restore manifest.
- [x] Minimum continuity set.
- [x] Full working set.
- [x] Optional style/culture enrichment class with no factual authority.
- [x] Version migration manifest.
- [x] Unknown-major rejection.
- [x] Additive-compatible metadata rules.
- [x] Cold-start restore test with no provider memory dependency.
- [x] Cross-provider restore demonstration.

**Phase 8 complete.** `tools/restore.py` exports deterministic portable `.aicr` archives with a hashed `AI-CONTEXT/RESTORE-MANIFEST`, explicit `AI-CONTEXT/MIGRATION-MANIFEST`, and declared payload hashes/byte lengths. The minimum continuity class restores workspace policy, canonical memory, profiles, routing policy, and existing curation policy; the full working set additionally preserves receipts, source evidence/staging, source snapshots/content indexes, and curation history while deliberately excluding raw vault exports and historical generated bundles. Restore validates archive paths, duplicates, symlinks, member limits, manifest/migration identity, artifact sizes and SHA-256 before publishing an atomically assembled destination workspace. Optional style/culture enrichment is presentation-only with `factual_authority = none` and restores outside canonical memory. Unknown major versions fail closed; additive metadata is restricted to non-authoritative `extensions`. Synthetic cold-start and cross-provider tests prove restored context requires no provider-side memory and can route the same canonical record set to distinct provider targets.

### Restore security gate

Restore reconstructs governed context; it never recreates a model identity, provider-side memory, hidden reasoning state, or chain of thought. Failed restores leave no partial destination. Migration transformations must be explicit, and additive metadata must not change artifact hashes, snapshot identity, curation authority, or disclosure permission.

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
