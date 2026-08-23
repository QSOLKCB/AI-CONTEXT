# AI-CONTEXT Agent Instructions

These rules are normative for AI-assisted changes to this repository.

## Core invariants

- Never place real user exports, private context, credentials, or private workspace artifacts in this public repository.
- Imported source material is evidence/staging data, not canonical memory.
- No adapter may promote memory directly.
- Canonical promotion requires explicit approval under workspace policy.
- Never silently lower sensitivity classification.
- Never store credentials, API keys, private keys, cookies, recovery codes, or bearer tokens as canonical AI memory.
- Unknown provider/export layouts must fail explicitly or be labelled generic/partial. Never pretend an unknown schema was parsed exactly.
- Preserve provenance identifiers and source hashes.
- Derived vector/graph/search indexes are projections, never authority.
- Do not claim cross-runtime canonical byte equivalence unless a future canonicalizer contract proves it.
- Restore means reconstruction of curated context, not recreation of a model identity or hidden provider state.

## Code expectations

- Keep the reference path dependency-light and local-first unless a dependency materially improves security.
- For cryptography, never invent primitives. Use maintained reviewed libraries and document the security boundary.
- Archive readers must reject path traversal and enforce resource bounds.
- New import adapters require synthetic fixtures and format-drift failure tests.
- Changes to canonicalization, ids, schemas, sensitivity semantics, or promotion rules require protocol/migration review.
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
- unknown-major/version rejection once migration support lands;
- deletion/tombstone propagation once lifecycle mutation lands.
