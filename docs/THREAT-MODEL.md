# AI-CONTEXT Threat Model

## Scope

AI-CONTEXT processes unusually sensitive material: complete AI exports, personal notes, repository content, project history, and long-lived preferences. The security model assumes source data may contain secrets, incorrect information, prompt injection text, malicious archives, stale project state, and third-party content.

The framework's job is not merely to store context. It must prevent untrusted source material from silently acquiring durable authority.

## Assets

- raw private exports;
- canonical memory records;
- import/provenance receipts;
- task-scoped bundles;
- user privacy policy;
- source fingerprints;
- tombstones and lifecycle history;
- optional encryption keys managed outside the initial reference implementation.

## Adversaries and failure modes

### Accidental publication

A user commits a raw export or generated private bundle to a public repository.

Mitigations:

- generated `.gitignore` in private workspaces;
- raw vault outside the framework repository;
- conspicuous warnings in documentation;
- future pre-commit private-data scanner.

### Secret ingestion

Exports and repositories commonly contain API keys, tokens, cookies, private keys, connection strings, or recovery material.

Mitigations:

- default secret-pattern screening;
- `secret` is forbidden from canonical AI memory;
- no credential-preservation feature;
- fail closed on known secret indicators;
- recommend a dedicated secret manager for credentials.

Pattern scanning is defense-in-depth, not proof that content is safe.

### Prompt injection from historical content

An imported conversation or repository file may contain text such as “ignore all previous instructions.”

Mitigations:

- imported source text is data, not instruction authority;
- observations never execute or promote themselves;
- `instruction` records require explicit promotion and policy approval;
- task bundle consumers should label imported text as quoted context.

### Hallucination fossilisation

A previous model generated a plausible but false statement which later becomes permanent memory.

Mitigations:

- SOURCE != MEMORY;
- assistant output begins as observation only;
- record type and epistemic state are mandatory;
- promotion is explicit;
- conflicts remain representable;
- confidence does not imply verification.

### Stale repository state

An old AI export says a feature is unfinished while the current repository says it is merged and released.

Mitigations:

- source precedence is explicit policy;
- live authoritative sources can outrank cached observations;
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
- future store UUID and signing support;
- never infer identity from filenames alone.

### Over-disclosure

A general assistant receives restricted context irrelevant to the current task.

Mitigations:

- profile/tag selection;
- sensitivity ceiling;
- explicit bundle build;
- smallest-sufficient-context principle;
- secret records excluded unconditionally by default.

### Model exfiltration

A remote model may retain or process context according to its provider policy.

Mitigations:

- AI-CONTEXT cannot guarantee third-party provider behavior;
- bundles are intentionally minimal and inspectable before disclosure;
- users may select local models for more sensitive profiles;
- provider routing policy is an implementation concern and should be explicit.

### Deletion failure

A user believes a memory is deleted but it survives in derived indexes or bundles.

Mitigations:

- tombstone canonical ids;
- derived artifacts must declare their source fingerprint;
- bundle rebuild invalidates stale projections;
- future conformance tests for deletion propagation;
- encrypted stores should support key destruction / cryptographic erasure where appropriate.

## Non-goals

The initial reference implementation does not claim to provide:

- hardened encrypted storage;
- password management;
- malware scanning;
- perfect secret detection;
- perfect PII detection;
- legal compliance certification;
- provider-side deletion;
- secure multi-user tenancy;
- reconstruction of hidden chain of thought;
- reconstruction of an original model identity.

## Default policy

1. Private by default.
2. Raw sources remain outside canonical memory.
3. Promotion is explicit.
4. Secret-like material is rejected.
5. Unknown provider format is not guessed into authority.
6. Bundles disclose the minimum selected set.
7. Missing provenance fails closed where provenance is required.
8. Deletion must invalidate derived projections.
9. Tool or execution claims require receipts, not narrative.
10. Privacy classification never decreases implicitly.
