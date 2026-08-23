# AI-CONTEXT Agent Instructions

These rules are normative for AI-assisted changes to this repository.

## Core invariants

- Never place real user exports, private context, credentials, or private workspace artifacts in this public repository.
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
- Changes to canonicalization, ids, schemas, sensitivity semantics, promotion rules, curation authority, or mutation semantics require protocol/migration review.
- Curation write paths must be explicit and fail closed when approval, provenance, conflict resolution, or actor authority is ambiguous.
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
- unknown-major/version rejection once migration support lands;
- deletion/tombstone propagation once lifecycle mutation lands.
