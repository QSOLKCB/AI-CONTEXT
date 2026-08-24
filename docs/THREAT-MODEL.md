# AI-CONTEXT Threat Model

## Scope

AI-CONTEXT processes unusually sensitive material: complete AI exports, personal notes, repository content, project history, email archives, long-lived preferences, canonical memory, and task-scoped bundles. Source data may contain secrets, incorrect information, prompt injection text, malicious archives, stale project state, and third-party content.

The framework must prevent untrusted source material from silently acquiring durable authority and prevent relevant memory from silently becoming disclosable.

## Assets

- raw private exports;
- staging observations and evidence objects;
- canonical memory records;
- import/provenance receipts;
- curation decisions and mutation history;
- routing profiles/policy and task-scoped bundles;
- source fingerprints;
- tombstones and lifecycle history;
- encrypted storage objects and storage receipts;
- encryption keys managed strictly outside AI-CONTEXT memory and store metadata.

## Adversaries and failure modes

### Accidental publication

A user commits a raw export or generated private bundle to a public repository.

Mitigations:

- generated `.gitignore` in private workspaces;
- raw vault outside the framework repository;
- conspicuous warnings in documentation;
- private curation/routing directories;
- future pre-commit private-data scanner.

### Secret ingestion

Exports and repositories commonly contain API keys, tokens, cookies, private keys, connection strings, recovery material, or encryption keys.

Mitigations:

- default secret-pattern screening;
- `secret` is forbidden from canonical AI memory;
- no credential-preservation feature;
- fail closed on known secret indicators;
- dedicated secret/key manager recommended;
- Phase 7 schemas contain key identifiers only, never key material.

Pattern scanning is defense-in-depth, not proof that content is safe.

### Prompt injection from historical content

An imported conversation or repository file may contain text such as “ignore all previous instructions.”

Mitigations:

- imported source text is data, not instruction authority;
- observations never execute or promote themselves;
- `instruction` records require explicit curation authority;
- task bundle consumers should label imported text as quoted context.

### Hallucination fossilisation

A previous model generated a plausible but false statement which later becomes permanent memory.

Mitigations:

- SOURCE != MEMORY;
- assistant output begins as observation only;
- record type and epistemic state are mandatory;
- promotion is governed by Phase 5 review/application;
- conflicts remain representable;
- confidence does not imply verification.

### Stale repository state

An old AI export says a feature is unfinished while the current repository says it is merged and released.

Mitigations:

- source precedence is explicit metadata/policy;
- exact Git identities are preserved where available;
- records carry verification metadata and can be superseded.

### Malicious archive

A ZIP attempts path traversal, decompression abuse, huge file counts, or oversized members.

Mitigations:

- never extract archive entries directly to arbitrary filesystem paths;
- reject absolute paths and `..` traversal;
- enforce member and byte limits;
- stream/read bounded content;
- skip unsupported binaries.

### Parser confusion

A provider changes its export format but the adapter treats the new structure as the old schema.

Mitigations:

- adapter id/version in every receipt;
- explicit parse status;
- structural checks before provider-specific parsing;
- unknown layouts fail or use labelled generic fallback;
- no silent upgrade from generic parse to provider-semantic claims.

### Cross-user contamination

Two users' stores or imports are mixed.

Mitigations:

- workspace-local identifiers;
- source receipts;
- storage `store_id` values;
- never infer identity from filenames alone.

### Over-disclosure

A model receives restricted context irrelevant or impermissible for the current task.

Mitigations:

- profile/tag/task selection;
- sensitivity ceilings;
- provider/local-model target separation;
- hard exclusions;
- fail-closed dependency routing;
- minimum-context diagnostics;
- secret records excluded unconditionally.

### Model exfiltration

A remote model may retain or process context according to its provider policy.

Mitigations:

- AI-CONTEXT cannot guarantee third-party provider behavior;
- bundles are intentionally minimal and inspectable;
- local models can receive a distinct policy class;
- provider routing requires explicitly configured disclosure policy.

### Encrypted-store theft

An attacker obtains the Phase 7 encrypted-store directory but not the external active key.

Mitigations:

- AES-256-GCM authenticated encryption through the maintained `cryptography` package;
- random nonce per encrypted object write;
- additional authenticated data binds protocol, algorithm, key ID, and logical-path digest;
- logical paths live inside ciphertext; physical filenames are path digests;
- key material is forbidden from manifest/object/receipt schemas;
- external key files are required to be owner-only on POSIX.

Residual leakage includes store existence, protocol/algorithm, key identifiers, object count, ciphertext sizes, and stable logical-path digests. The backend is not metadata-hiding.

### Live-process compromise

Malware or another process controls the unlocked user session or can inspect the Python process while data is decrypted.

Phase 7 does **not** claim protection from this attacker. Application-level encryption protects data at rest, not plaintext after authorized decryption. Python also cannot guarantee reliable memory zeroization of immutable key bytes.

Possible defense-in-depth includes filesystem encryption, process isolation, hardware-backed key custody, minimal plaintext export lifetimes, and a hardened host environment.

### Key loss

The active encryption key is lost with no recovery copy.

Result: encrypted content may become permanently unavailable.

Mitigations:

- recovery material kept outside AI-CONTEXT memory and outside the encrypted store;
- documented offline/password-manager/hardware-backed custody options;
- key rotation metadata identifies which key protects current objects.

Availability is not recoverable from a key identifier alone.

### Interrupted key rotation

A process stops after some objects are re-encrypted under a replacement key but before all objects and manifest metadata are updated.

Mitigations:

- explicit `rotation.json` journal;
- normal access fails while a rotation journal exists;
- each encrypted object records its actual key identifier;
- rotation can resume using both external old/new keys;
- active manifest key changes only after every journalled object is readable with the replacement key.

### Deletion failure

A user believes a memory/object is erased but copies survive in indexes, bundles, backups, snapshots, or storage history.

Mitigations:

- canonical tombstones;
- derived artifacts must declare source fingerprints;
- routed bundle rebuild invalidates stale projections;
- Phase 7 storage deletion receipts prove only primary ciphertext removal;
- key-destruction receipts are explicitly self-attested external actions;
- documentation distinguishes deletion from cryptographic erasure.

A storage deletion receipt does not prove removal from backups, snapshots, cloud-sync history, exported plaintext, or other key copies.

## Non-goals

The reference implementation does not claim to provide:

- password management;
- hardware-backed secret isolation;
- secure-memory or guaranteed key zeroization;
- malware scanning;
- perfect secret or PII detection;
- legal compliance certification;
- provider-side deletion;
- secure multi-user tenancy;
- metadata-hiding encrypted storage;
- universal proof that deleted data or keys no longer exist anywhere;
- reconstruction of hidden chain of thought;
- reconstruction of an original model identity.

## Default policy

1. Private by default.
2. Raw sources remain outside canonical memory.
3. Canonical application requires explicit curation authority.
4. Secret-like material is rejected from canonical memory.
5. Unknown provider format is not guessed into authority.
6. Bundles disclose the minimum permitted selected set.
7. Missing provenance fails closed where provenance is required.
8. Routing dependencies never bypass disclosure permission.
9. Deletion/tombstones must invalidate derived projections.
10. Tool or execution claims require receipts, not narrative.
11. Privacy classification never decreases implicitly.
12. Encryption keys and recovery secrets never enter AI-CONTEXT memory or encrypted-store metadata.
13. Encryption at rest never changes epistemic or curation authority.
14. Cryptographic-erasure claims must state exactly what was actually deleted or externally attested.

See [`STORAGE.md`](STORAGE.md) and [`ENCRYPTION-THREAT-MODELS.md`](ENCRYPTION-THREAT-MODELS.md) for Phase 7 details.
