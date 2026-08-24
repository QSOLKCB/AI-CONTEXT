# AI-CONTEXT Agent Instructions

These rules are normative for AI-assisted changes to this repository.

## Core invariants

- Never place real user exports, private context, credentials, encryption keys, recovery material, or private workspace artifacts in this public repository.
- Imported source material is evidence/staging data, not canonical memory.
- No adapter may promote memory directly.
- No candidate generator or local LLM curator may write canonical memory directly.
- Candidate proposals must remain `approval: pending` until an explicit review decision exists.
- A local-LLM recommendation is advisory evidence only; it is never a human/policy review decision.
- Canonical application requires the latest explicit `approve` decision from a `human` or `policy` actor.
- Content corrections must create a new canonical record plus an explicit supersession edge; do not silently rewrite remembered content in place.
- Curation mutations may change only confidence, epistemic verification metadata, and lifecycle state. They must not silently rewrite content, sensitivity, record type, tags, or provenance.
- Tombstoning preserves historical identity and requires a tombstone receipt; it is not deletion of history.
- Canonical promotion requires explicit approval under workspace policy.
- Never silently lower sensitivity classification.
- Never store credentials, API keys, private keys, cookies, recovery codes, bearer tokens, storage keys, or recovery secrets as canonical AI memory.
- Unknown provider/export layouts must fail explicitly or be labelled generic/partial. Never pretend an unknown schema was parsed exactly.
- Preserve provenance identifiers and source hashes.
- Phase 6 routing is a read-only disclosure projection. It must never mutate canonical memory or curation authority state.
- Task/tag relevance is not disclosure authority. A selector match may never bypass sensitivity, target policy, lifecycle, approval, or hard exclusions.
- A required dependency may bypass positive relevance selectors only. `DEPENDENCY != PERMISSION BYPASS`.
- Ambiguous or missing semantic dependency endpoints must fail closed. Do not rank or guess among multiple possible memories.
- Provider and local-model disclosure targets are distinct policy classes. Never silently treat an external provider as equivalent to a local target.
- Routing diagnostics explain included records only; do not turn a bundle into an inventory of records withheld by policy.
- Phase 7 encryption is a storage property only. `ENCRYPTION_AT_REST != MEMORY_AUTHORITY`.
- Storage metadata may contain non-secret key identifiers only. Raw key bytes, passphrases, wrapped keys, recovery keys, seed phrases, and secret-manager credentials are forbidden from AI-CONTEXT memory, fixtures, schemas, manifests, and receipts.
- Storage keys must remain external to encrypted stores. Reference code must fail closed when a key file is located inside the store.
- Key rotation must be resumable or fail closed; partially rotated stores must never be treated as healthy active stores.
- Deletion receipts must state the exact erasure scope. Never claim backup/key destruction from primary ciphertext deletion alone.
- Key-destruction receipts are external-action attestations, not cryptographic proof that every usable key copy is gone.
- Phase 8 restore reconstructs portable governed context only. `RESTORE != MODEL IDENTITY` and `RESTORED_CONTEXT != ORIGINAL_MODEL_INSTANCE`.
- Restore archives must declare `provider_memory_dependency = none`; provider-side memory/session state is never a continuity prerequisite.
- Style/culture enrichment has `factual_authority = none` and may influence presentation only. It must never enter canonical memory, evidence authority, curation approval, or disclosure permission.
- Raw vault/provider exports and generated historical bundles are not silently promoted into restore continuity requirements.
- Unknown restore/workspace major versions must fail closed. Never guess through a major-version authority or canonicalization change.
- Additive restore/migration metadata is permitted only inside the explicit non-authoritative `extensions` container.
- Derived vector/graph/search indexes are projections, never authority.
- Do not claim cross-runtime canonical byte equivalence unless a future canonicalizer contract proves it.
- Restore means reconstruction of curated context, not recreation of a model identity or hidden provider state.

## Code expectations

- Keep the reference path dependency-light and local-first unless a dependency materially improves security.
- For cryptography, never invent primitives. Use maintained reviewed libraries and document the security boundary.
- Do not implement custom block modes, KDFs, MAC constructions, nonce schemes, key wrapping, or password encryption.
- Archive readers must reject path traversal, duplicate members, unsafe symlinks, and resource-limit violations.
- Storage logical paths must reject traversal and absolute-path escape.
- Restore export must reject symlinked source artifacts; restore destinations must be assembled off-path and published only after complete validation.
- New import adapters require synthetic fixtures and format-drift failure tests.
- Changes to canonicalization, ids, schemas, sensitivity semantics, promotion rules, curation authority, mutation semantics, routing policy, dependency expansion, disclosure diagnostics, storage envelopes, key metadata, deletion/erasure semantics, restore manifests, continuity classes, migration rules, or enrichment authority require protocol/migration review.
- Curation write paths must be explicit and fail closed when approval, provenance, conflict resolution, or actor authority is ambiguous.
- Routing must fail closed when a required dependency cannot be disclosed to the selected target.
- Routing policy/profile normalization must reject unknown fields rather than ignoring policy typos.
- Encrypted storage must authenticate ciphertext and relevant metadata before plaintext is accepted.
- Encrypted-store normal access must fail while a rotation journal is present.
- Restore must validate declared artifact hashes and byte lengths before the destination workspace becomes visible.
- Migration manifests must record explicit source/target versions and may not hide payload transformations.
- Security-sensitive behavior must fail closed.

## Test expectations

At minimum preserve tests for:

- SOURCE != MEMORY;
- provenance-required promotion;
- secret rejection;
- archive traversal rejection;
- deterministic repeated bundle generation;
- provider-format detection/fallback;
- repository `.git` exclusion;
- candidate generation cannot self-promote;
- local-LLM curator output cannot authorize canonical writes;
- apply requires a human/policy approval decision;
- unresolved conflicts block application unless explicitly resolved;
- supersession preserves both old and new memory identities;
- confidence/verification mutations are receipted;
- retention expiry affects disclosure without deleting history;
- tombstone receipts and bundle exclusion;
- provenance explanation links memory back to review and source evidence;
- task and tag selectors produce deterministic minimum-context bundles;
- provider/local-model targets enforce different disclosure ceilings when configured;
- hard exclusions cannot be bypassed by positive selectors;
- dependency expansion cannot bypass disclosure gates;
- ambiguous semantic dependency references fail closed;
- routed diagnostics explain every included record;
- stale routed bundles fail validation after profile/policy changes;
- encrypted storage round-trip without plaintext leakage in object filenames/envelopes;
- wrong-key and ciphertext-tamper rejection;
- storage logical-path traversal rejection;
- external key-file boundary and restrictive key permissions;
- key rotation re-encrypts objects and records active/retired key metadata;
- rotation journal blocks normal access until completion/resume;
- storage deletion receipts do not overclaim cryptographic erasure;
- active keys cannot be attested as destroyed;
- storage schemas contain key identifiers only, never key material;
- minimum cold-start restore with no provider-side memory dependency;
- full restore preserves curation/provenance working state;
- failed or corrupt restore leaves no partial destination;
- style/culture enrichment cannot alter canonical memory or factual authority;
- unknown restore/workspace major-version rejection;
- additive `extensions` metadata does not change restore snapshot authority;
- cross-provider restored routing preserves the same canonical record set;
- deletion/tombstone propagation once lifecycle mutation lands.
