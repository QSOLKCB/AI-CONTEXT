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

**Phase 3 complete.** ChatGPT and Claude retain the Phase 1 import path but are protected by exact/partial/reject migration fixtures. Higher-churn providers use `tools/provider_import.py`, which normalizes Gemini Takeout, Grok account exports, browser chat archives, and data-only community plugin mappings into the same staging/receipt contract. Unknown layouts fail closed; known lossy layouts remain explicitly `partial`.

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

**Phase 4 complete.** `tools/evidence_import.py` creates deterministic source snapshots, source-evidence authority metadata, Git identities, line-addressed chunks, Drive/Takeout evidence, RFC 5322/MIME email evidence, and representation-aware duplicate collapse. `tools/validate_evidence.py` validates receipt/snapshot/content/provenance relationships.

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

**Phase 5 complete.** `tools/curation.py` implements pending candidates, human/policy decisions, explicit application, advisory-only local-LLM envelopes, conflict detection, supersession, receipted mutations, retention, tombstones, and provenance explanations. `tools/validate_curation.py` validates the authority and mutation chains.

### Curation security gate

No automated semantic extractor or local LLM may write directly to canonical memory. It may only propose pending candidates or advisory suggestions. Canonical application requires a latest explicit `approve` decision from a `human` or `policy` actor.

## Phase 6 — Selective disclosure and routing

- [x] Profile format and default `general` profile.
- [x] Tag selector.
- [x] Task/semantic selector.
- [x] Sensitivity ceiling.
- [x] Provider/local-model disclosure policy.
- [x] Hard exclusions beyond sensitivity/tag policy.
- [x] Dependency expansion with fail-closed ambiguity handling.
- [x] Minimum-context diagnostics explaining why each record entered a bundle.

**Phase 6 complete.** `tools/routing.py` is a deterministic disclosure firewall over canonical memory. Relevance never grants disclosure permission; dependencies cannot bypass approval, lifecycle, target policy, sensitivity, verification, or hard exclusions.

### Disclosure security gate

Routing is a read-only projection over canonical memory. A required dependency that cannot legally be disclosed causes bundle construction to fail rather than silently leak or silently omit required context.

## Phase 7 — Encrypted storage boundary

- [x] Storage-backend interface.
- [x] Document that encryption-at-rest is a storage-backend responsibility in the initial implementation.
- [x] Document comparative threat assumptions for filesystem encryption, age, encrypted SQLite, and hardware-backed stores.
- [x] Reference encrypted backend using a maintained cryptographic library.
- [x] Key rotation metadata.
- [x] Cryptographic erasure/deletion receipt strategy.
- [x] Recovery-key guidance that does not place keys inside AI-CONTEXT memory.

**Phase 7 complete.** `tools/storage.py` defines plaintext and AES-256-GCM encrypted-directory backends using the maintained `cryptography` package. Store identity is authenticated, keys remain external, rotation is resumable/fail-closed, and deletion receipts state only the erasure scope actually established.

### Encryption gate

Do not invent custom cryptography. Keys and recovery material remain external capabilities and never become AI-CONTEXT canonical memory, curation state, routing data, storage metadata, or public fixtures.

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

**Phase 8 complete.** `tools/restore.py` exports deterministic `.aicr` archives with hashed restore/migration manifests and declared payload hashes. The minimum continuity set is dependency-closed: in addition to workspace policy, canonical memory, profiles, and routing policy, it preserves the observations, receipts, and Phase 5 authority history required to validate remembered records and preserve provider-disclosure eligibility. The full working set is a strict superset that additionally preserves the broader staging/content evidence graph and complete working history while deliberately excluding raw vault exports and historical generated bundles. Full re-exports preserve installed style/culture enrichment. Archive validation performs a throwaway semantic restore so a hash-consistent but semantically invalid third-party archive fails before publication. Unknown majors fail closed, non-authoritative migration `extensions` do not alter snapshot identity, and failed restores leave no partial destination.

### Restore security gate

Restore reconstructs governed context; it never recreates a model identity, provider-side memory, hidden reasoning state, or chain of thought. Style/culture enrichment is presentation-only with zero factual authority. Full archives must contain the minimum base set, and migration transformations must be explicit.

## Phase 9 — Derived indexes

- [x] Vector index projection format.
- [x] Graph index projection format.
- [x] Search cache projection format.
- [x] Derived-artifact source fingerprints.
- [x] Stale projection rejection.
- [x] Rebuild-after-tombstone conformance.

**Phase 9 complete.** `tools/indexes.py` builds deterministic private `indexes/` projections from approved active canonical memory. `vector.json` uses a dependency-free Unicode tokenization plus SHA-256 bucketed sparse integer vector reference format; `graph.json` projects active memory, provenance references, explicit relationships, and unresolved relationship diagnostics without guessing semantic-key authority; `search.json` is an inverted lexical candidate cache; and `manifest.json` binds all three artifacts to exact bytes and one `AI-CONTEXT/DERIVED-SOURCE-FINGERPRINT`. The fingerprint includes the complete canonical-store SHA-256 plus the active-approved retrieval-input SHA-256, so any canonical mutation makes prior indexes stale. Validation recomputes the current fingerprint and every deterministic projection before use. Search refuses stale indexes and returns candidate IDs only with `disclosure_requires_routing = true`. Tombstoning immediately stales the prior set; rebuild removes the tombstoned record from vector/search/active graph retrieval while preserving authoritative canonical and tombstone history. Derived indexes remain outside Phase 8 continuity archives and are rebuilt after restore.

### Authority gate

Indexes are retrieval accelerators only. `INDEX HIT != MEMORY AUTHORITY`, `INDEX MEMBERSHIP != DISCLOSURE PERMISSION`, and `STALE INDEX != USABLE INDEX`. A vector, graph edge, or search hit may identify a candidate but can never promote, verify, reclassify, resurrect, mutate, or disclose canonical memory. Phase 6 routing remains the disclosure authority.

## Phase 10 — Interoperability

- [x] Python reference implementation stabilisation.
- [x] Rust implementation if useful for hardened local stores.
- [x] Language-neutral conformance fixtures.
- [x] JSON canonicalization upgrade evaluation, including RFC 8785 JCS.
- [x] Signed bundle receipts.
- [x] Capability manifest for model/agent consumers.
- [x] MCP/tool adapter examples.

**Phase 10 complete.** `tools/interop.py` defines the stabilized Python interoperability façade for capability discovery, language-neutral fixture validation, external Ed25519 signing-key generation, bundle signing, and signed-receipt verification. `fixtures/interoperability/` freezes exact `python-json-v0.1` byte/hash vectors plus a public-only Ed25519 signature vector, and `rust/ai-context-interop/` independently verifies the same capability/signature corpus and exact bundle-byte receipts without gaining memory or routing write authority. Signed receipts bind exact bundle bytes, canonical payload/store hashes, signer key identity, and a domain-separated byte preimage while explicitly remaining `integrity-attestation-only`. `AI-CONTEXT/CAPABILITY-MANIFEST` advertises supported features and authority limits to model/agent/tool consumers. RFC 8785 JCS is evaluated in `docs/JCS-EVALUATION.md` but is not silently adopted because canonicalizer changes alter existing identities and require an explicit migration/freeze decision. Read-only MCP/generic tool examples expose discovery, candidate retrieval, bundle validation, and receipt verification without creating a new authority layer.

### Interoperability security gate

`SIGNATURE != DISCLOSURE AUTHORITY`, `SIGNATURE != EPISTEMIC AUTHORITY`, `CAPABILITY CLAIM != PERMISSION`, and `MCP TOOL != MEMORY AUTHORITY`. Private signing keys remain external and never enter fixtures, canonical memory, restore archives, or capability manifests. Independent implementations may verify wire contracts but do not become competing canonical-memory authorities.

## Phase 11 — UX

- [x] Local TUI for imports, review, promotion, conflicts, and bundle previews.
- [x] Read-only provenance explorer.
- [x] “Show me exactly what this AI will receive” bundle inspector.
- [x] One-command export/restore flow.
- [x] Safe defaults for non-programmers.

**Phase 11 complete.** `tools/ux.py` provides a dependency-free line-oriented local TUI plus scriptable operator commands for status, imports, candidate queue/review/application, conflict inspection, provenance, exact bundle inspection, backup, and restore. Read-only UX paths do not initialize or upgrade governance state and do not create preview bundles. The exact bundle inspector uses the Phase 10 read-only planner and exposes the deterministic payload a selected target would receive under current Phase 6 policy. Non-interactive writes require `--yes`; the TUI requires typing `YES` immediately before writes. Human review and canonical application remain separate authority events with separate confirmations. `backup` and `restore` delegate to the Phase 8 `.aicr` implementation rather than defining a UX-specific continuity format. In operator-facing language, “promotion” means governed Phase 5 application of an already approved candidate; the legacy direct promote path remains disabled.

### UX security gate

`TUI ACTION != AUTHORITY`, `PREVIEW != DISCLOSURE`, `INSPECTOR != MUTATION`, and `ONE-COMMAND FLOW != BYPASS`. UX convenience code may orchestrate existing protocol actions but may not create a second approval, routing, restore, or memory authority path.

## v1.0 architecture completion sequence

**Architecture Phases 0–11 are complete.** The project is now in v1.0 release-candidate hardening. The implementation architecture is frozen before formalization; Lean 4 must describe the released protocol rather than become another place to change it.

- [ ] Run the complete conformance/security/adversarial suite on the intended release commit.
- [ ] Freeze public protocol names, schemas, canonical invariants, and migration rules for v1.0.
- [ ] Freeze the v1 canonicalizer choice: retain `python-json-v0.1` with the Phase 10 conformance corpus or introduce an explicitly versioned JCS migration.
- [ ] Perform a final public-tree audit for private data, credentials, keys, generated workspaces, caches, and accidental artifacts.
- [ ] Produce final `RELEASE-NOTES.md` for the GitHub release candidate.
- [ ] Tag the exact frozen commit as `v1.0.0`.
- [ ] Record the v1.0.0 tag commit SHA as the immutable formalization target.

### v1.0 release gate status

AI-CONTEXT v1.0 must not be declared until every item is checked:

- [x] Canonical schemas are versioned.
- [x] Import receipts and canonical records pass conformance tests.
- [x] Archive and secret-ingestion security tests pass.
- [x] Deterministic bundle fixtures pass byte-for-byte.
- [x] Unknown-major protocol/workspace restore versions fail closed.
- [x] Deletion/tombstone propagation is tested.
- [x] At least two independent AI-export formats and one repository/document source round-trip through staging.
- [x] A cold-start restore succeeds without provider-side memory.
- [ ] Final release-candidate audit demonstrates that private raw input, credentials, storage keys, recovery material, and user workspace artifacts are absent from the public release tree.
- [x] Documentation clearly distinguishes context continuity from AI/model identity.

## Phase 12 — Lean 4 formalization of frozen v1.0.0

**Starts only after the `v1.0.0` tag exists.** Formalization targets that exact immutable commit. Later implementation changes require a new formalization target/version rather than silently changing the theorem subject.

- [ ] Pin Lean toolchain and Lake project metadata.
- [ ] Define formal core datatypes for trust zones, record classes, sensitivity, epistemic state, lifecycle, authority, and disclosure targets.
- [ ] Formalize `SOURCE != MEMORY` and candidate/application authority boundaries.
- [ ] Formalize sensitivity non-downgrade and secret-memory exclusions.
- [ ] Formalize curation review/application, supersession, tombstone, and mutation invariants.
- [ ] Formalize `RELEVANT != PERMITTED` and `DEPENDENCY != PERMISSION BYPASS`.
- [ ] Formalize restore continuity without model-identity claims.
- [ ] Formalize style/culture enrichment as having zero factual authority.
- [ ] Formalize storage/encryption as persistence properties that confer no epistemic authority; do not attempt to re-prove AES-GCM itself.
- [ ] Formalize derived indexes as non-authoritative projections and stale-source fingerprints as unusable retrieval state.
- [ ] Formalize signed receipts as byte-integrity attestations that confer neither disclosure nor epistemic authority.
- [ ] Formalize capability/tool transport metadata as non-authoritative with respect to canonical memory and routing permission.
- [ ] Formalize UX/operator actions as orchestration only: confirmations, previews, and menu actions do not create protocol authority.
- [ ] Formalize migration unknown-major rejection and extensions-only non-authoritative metadata.
- [ ] Add finite reference models and counterexamples for invalid promotion, disclosure, migration, restore, derived-index, signature-authority, tool-authority, and UX-authority states.
- [ ] Map each formal theorem to the corresponding protocol invariant, reference implementation behavior, and adversarial test.
- [ ] Add Lean CI and require the archival theorem set to build without unresolved proof placeholders.
- [ ] Produce a machine-readable theorem inventory for the archival record.

### Formalization authority rule

```text
v1.0.0 tagged implementation + schemas = formalization target
Lean proofs = selected invariant proofs about that target
Lean proof != proof of every implementation detail
cryptographic library use != reimplementation of cryptographic proofs
```

## Phase 13 — Scholarly report and Zenodo archival record

The archival surface should remain intentionally small and easy to cite: **three public uploads**.

- [ ] Produce `AI-CONTEXT-v1.0.0-Overview.pdf`, a front-facing human technical report covering motivation, architecture, threat model, evidence, curation, routing, storage, restore/migration, derived indexes, interoperability/signed receipts, UX, conformance, Lean results, limitations, reproducibility, and citation.
- [ ] Produce `RELEASE-NOTES.md` as the machine-readable/human-readable release report containing tag, commit SHA, protocol/schema versions, phase status, theorem inventory summary, tests, limitations, artifact hashes, and reproduction commands.
- [ ] Produce `AI-CONTEXT-1.0.0-source.zip` as an archival source bundle containing:
  - `reference-v1.0.0/` — exact source tree of the immutable GitHub `v1.0.0` tag;
  - `formal/` — Lean 4 formalization of that exact tag;
  - `ARCHIVE-MANIFEST` — binds both components to the v1.0.0 tag/commit and records hashes/toolchain metadata.
- [ ] Verify the source ZIP contains no private workspaces, raw exports, credentials, keys, recovery material, build caches, virtual environments, or unrelated generated artifacts.
- [ ] Put SHA-256 hashes for all three Zenodo uploads inside `RELEASE-NOTES.md` so a fourth checksum file is unnecessary.
- [ ] Reproduce the reference test suite and Lean theorem build from the clean archival source bundle.
- [ ] Create a Zenodo **Software** record for AI-CONTEXT v1.0.0 with exactly:
  1. `AI-CONTEXT-1.0.0-source.zip`
  2. `AI-CONTEXT-v1.0.0-Overview.pdf`
  3. `RELEASE-NOTES.md`
- [ ] Record repository URL, exact tag, exact commit SHA, license, creators/contributors, keywords, related identifiers, and formalization scope in Zenodo metadata.
- [ ] Publish the Zenodo record and obtain the version DOI/concept DOI.
- [ ] Add the DOI and canonical citation back to the GitHub release/README without modifying the already frozen v1.0.0 source tag.

### Archival provenance rule

The Lean formalization is created **after** the v1.0.0 tag, so the archival source ZIP is a compound scholarly source bundle, not a claim that Lean files existed in the original tag. `reference-v1.0.0/` must be byte-for-byte derived from the tag, while `formal/` explicitly identifies that tag as its theorem target.