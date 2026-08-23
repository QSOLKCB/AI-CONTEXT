# AI-CONTEXT Architecture

## 1. Purpose

AI-CONTEXT defines a portable private-memory pipeline for user-controlled AI context. The framework accepts heterogeneous personal sources while preserving provenance, privacy class, uncertainty, and source authority.

The framework is deliberately split into layers so an imported conversation can never become authoritative memory merely because a parser saw it.

## 2. Architectural invariants

These invariants are protocol-level design constraints.

1. **RAW_SOURCE != CANONICAL_MEMORY**
2. **IMPORTED != APPROVED**
3. **AI_OUTPUT != VERIFIED_FACT**
4. **PREFERENCE != FACT**
5. **PROJECT_STATE != CLAIM_OF_TRUTH**
6. **PRIVATE != DISCLOSABLE**
7. **DELETED != HIDDEN**
8. **RESTORED_CONTEXT != ORIGINAL_MODEL_INSTANCE**
9. **MODEL_SELF_REPORT != EXECUTION_RECEIPT**
10. **SECRET != MEMORY**
11. **PROVENANCE_MISSING => FAIL_CLOSED** for durable promotion when policy requires provenance
12. **TASK_BUNDLE <= APPROVED_DISCLOSURE_SET**

## 3. Trust zones

### Zone A: raw vault

Contains original exports and source files. It may contain credentials, deleted chats, attachments, personal data, hallucinations, third-party material, and provider-specific metadata.

The framework treats the raw vault as hostile-sensitive input:

- do not commit it;
- do not assume provider schema stability;
- hash before interpretation;
- apply archive traversal and size limits;
- never expose the whole vault to a model by default.

### Zone B: import receipts and staging

An adapter converts source material into observations. Every import has a receipt containing at least:

- source type;
- source identifier supplied by the operator;
- SHA-256 identity of imported bytes or deterministic tree digest;
- adapter id/version;
- import time as operational metadata;
- warning/error state;
- observation count.

Observations are evidence objects, not memory records.

### Zone C: canonical memory

Canonical memory contains explicitly promoted records. Promotion requires a record type and approval state. A record may reference one or more observations.

The initial record classes are:

- `fact`
- `preference`
- `project_state`
- `decision`
- `claim`
- `hypothesis`
- `instruction`
- `relationship`
- `publication`
- `event`
- `environment`
- `provenance_policy`

Applications may add extension classes under a namespaced profile, but must not silently reinterpret core classes.

### Zone D: task bundles

A bundle is a deterministic projection of canonical memory selected by profile, tag, task, and disclosure policy.

A model should receive the bundle, not the vault.

## 4. Source adapter contract

An adapter consumes one source and emits zero or more observations plus exactly one import receipt.

Adapters must:

- identify themselves and their version;
- report whether parsing was exact, partial, or generic fallback;
- preserve source-local identifiers when available;
- never claim semantic correctness beyond parsing;
- never promote memory directly;
- reject unsafe archive paths;
- respect file-count and byte limits;
- record skipped binary or oversized files;
- avoid reading `.git` object storage for repository imports;
- pass potentially secret-bearing content through secret screening before canonical promotion.

Provider-specific adapters are allowed to be best-effort because private export formats are not stable APIs. Unknown layouts must fall back to generic import or fail explicitly.

## 5. Observation model

An observation is a normalized statement that something existed in an imported source. It does not assert that the content is true.

Example:

```json
{
  "id": "obs.sha256:...",
  "source_receipt_id": "import.sha256:...",
  "kind": "conversation_message",
  "source_local_id": "node-123",
  "actor": "assistant",
  "timestamp": "2026-01-01T01:02:03Z",
  "content": {"text": "..."},
  "content_sha256": "..."
}
```

The observation id is derived from canonical observation content wherever possible so duplicate imports can be detected.

## 6. Promotion contract

Promotion is an explicit trust transition.

A promoted record must declare:

- stable id;
- core record type;
- content;
- sensitivity class;
- epistemic state;
- confidence;
- approval state;
- source references unless the record is explicitly `user_asserted` and policy permits source-free assertions;
- lifecycle metadata.

Initial sensitivity classes:

- `public`
- `private`
- `restricted`
- `secret`

`secret` records are forbidden from canonical AI memory by the default policy. Secret-like material should live in a credential manager, encrypted secret store, or equivalent system designed for secrets.

Initial epistemic states:

- `observed`
- `retrieved`
- `parsed`
- `inferred`
- `remembered`
- `user_asserted`
- `verified`
- `unknown`

The framework does not equate high confidence with verification.

## 7. Source precedence

Precedence is policy, not universal truth. A typical profile may prefer:

1. live authoritative repository state for software/project facts;
2. signed or hashed release/publication receipts;
3. user-curated canonical memory;
4. imported notes;
5. prior AI conversation output.

Conflicts should be preserved as conflicts unless a policy resolves them using declared authority.

## 8. Privacy and disclosure

Every bundle build evaluates:

- record approval;
- sensitivity ceiling;
- selected profile/tags;
- expiry/tombstone state;
- explicit exclusions;
- optional task selector.

The default profile should disclose as little as possible.

A system may maintain broad private memory while exposing only a coding profile to a coding agent, only publication metadata to a citation tool, or only stable preferences to a general assistant.

## 9. Determinism

The reference implementation uses canonical UTF-8 JSON with:

- lexicographically sorted object keys;
- no insignificant whitespace for canonical hashing;
- preserved semantic array order;
- LF line endings;
- SHA-256 content identities.

The initial Python canonicalizer makes no claim of cross-runtime number-rendering equivalence. A future protocol version may adopt RFC 8785 JCS or another fully specified canonicalizer.

## 10. Restore and portability

A portable restore package contains canonical records, profile policy, provenance receipts required to validate them, and deterministic fingerprints.

It must not require:

- provider-side memory;
- hidden chain of thought;
- original model weights;
- original account session;
- live provider credentials.

A successful restore means a compatible system received the intended curated context. It does not mean the same AI identity was recreated.

## 11. Deletion and lifecycle

Canonical records may be:

- active;
- expired;
- superseded;
- tombstoned.

A tombstone preserves the fact that a canonical id was deliberately removed without retaining the deleted sensitive payload in active memory. Implementations should support cryptographic deletion receipts for encrypted stores in later phases.

## 12. Extension points

The protocol intentionally leaves room for:

- encrypted vault backends;
- vector or graph indexes as derived projections;
- local LLM curators;
- human review UIs;
- GitHub / Drive / email adapters;
- differential privacy or redaction modules;
- signed receipts;
- hardware-backed keys;
- append-only audit logs;
- deterministic restore capsules.

Derived indexes never become canonical authority merely because retrieval is convenient.
