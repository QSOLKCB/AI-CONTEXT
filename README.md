# AI-CONTEXT

**A public framework for building private, portable AI memory from a user's own data.**

AI-CONTEXT is a vendor-neutral specification and reference implementation for turning private AI exports, repositories, notes, documents, Drive exports, email archives, and other user-controlled sources into curated context that can be selectively disclosed to compatible AI systems between sessions.

It is inspired by architecture used in `QSOLKCB/QSOL-CONTEXT`, but deliberately separates reusable machinery from any one person's private context.

## The core idea

```text
Private sources
  |
  |-- ChatGPT / Claude / Gemini / Grok / browser-chat exports
  |-- local or exported Git repositories
  |-- Markdown / text / JSON / JSONL / OOXML documents
  |-- Google Drive / Takeout exports
  |-- EML / MBOX email archives
  v
[ RAW VAULT ]            source material is not trusted as memory merely because it exists
  v
[ IMPORT + RECEIPTS ]    source identity, parser/layout, timestamps, provenance
  v
[ STAGING ]              observations, content objects, evidence graph
  v
[ CURATION ]             propose, review, resolve conflicts, verify, expire, tombstone
  v
[ CANONICAL MEMORY ]     typed approved records with provenance and lifecycle state
  v
[ ROUTING ]              profile + target policy + task/tag selection + dependency closure
  v
[ SELECTIVE BUNDLE ]     smallest permitted context for the current task and consumer
  v
AI / agent / local model / external provider / downstream substrate

storage boundary (optional)
  plaintext filesystem backend
        or
  encrypted directory backend + external key custody
```

Core boundaries:

```text
SOURCE MATERIAL != CANONICAL MEMORY
CANDIDATE != MEMORY
LLM SUGGESTION != REVIEW DECISION
RELEVANT != PERMITTED
DEPENDENCY != PERMISSION BYPASS
ROUTED BUNDLE != CANONICAL MEMORY
ENCRYPTION AT REST != MEMORY AUTHORITY
KEY ID != KEY MATERIAL
RESTORED CONTEXT != ORIGINAL MODEL INSTANCE
```

A prior AI response may contain hallucinations. A repository may be stale. An email can repeat a false claim. A task may be relevant to material that the selected provider is not allowed to receive. Encryption can protect persisted bytes while doing nothing to make those bytes true or authorized. AI-CONTEXT therefore keeps evidence, authority, curation, disclosure, and storage as distinct layers.

## Design goals

- **Private by default.** Raw exports, evidence state, curation state, routing policy, and generated bundles live in the private workspace and are ignored by Git by default.
- **User-owned.** The source of truth is the user's store, not provider-side memory.
- **Portable.** Core artifacts use UTF-8 JSON/JSONL plus deterministic hashes and receipts.
- **Vendor-neutral.** Provider export formats, model runtimes, and storage backends are replaceable edges, not authorities.
- **Provenance-preserving.** Canonical memory can trace back to observations, import receipts, source snapshots, and content identities.
- **Typed memory.** Facts, preferences, project state, claims, hypotheses, instructions, relationships, publications, events, environment state, and provenance policy remain distinct classes.
- **Human/policy curation authority.** Automated extractors and local LLMs may propose. They may not self-promote.
- **Selective disclosure.** Build the smallest permitted task bundle rather than giving every model the complete private store.
- **Fail closed.** Unknown formats, ambiguous dependencies, blocked required context, malformed authority state, interrupted key rotation, and policy conflicts are rejected rather than guessed through.
- **Deterministic where practical.** Identical memory, profile, routing policy, target, task, and selectors produce identical routed bundle bytes under the declared canonicalizer.
- **Restorable, not mystical.** Restore reconstructs curated context. It does not recreate hidden provider state, a model identity, or private chain of thought.

## Trust zones

### 1. Raw vault

Original exports and source files. Treat as highly sensitive input. AI-CONTEXT never requires real private exports to be committed to this public framework repository.

### 2. Staging and source evidence

Parsed observations, import receipts, deterministic source snapshots, content-addressed text/binary references, and duplicate-provenance indexes. This layer is evidence, not memory.

See:

- [`docs/SOURCE-EVIDENCE.md`](docs/SOURCE-EVIDENCE.md)
- [`docs/GOOGLE-DRIVE-EXPORTS.md`](docs/GOOGLE-DRIVE-EXPORTS.md)
- [`docs/EMAIL-ARCHIVES.md`](docs/EMAIL-ARCHIVES.md)

### 3. Curation

Pending candidates, human/policy review decisions, advisory local-LLM suggestions, conflicts, application receipts, verification/confidence mutations, supersession edges, retention policy, and tombstone receipts.

See [`docs/CURATION.md`](docs/CURATION.md).

### 4. Canonical memory

Only explicitly approved and applied records enter canonical memory. Canonical records retain type, content, sensitivity, epistemic state, confidence, provenance, tags, and lifecycle metadata.

### 5. Selective routed bundle

A deterministic task-scoped projection over canonical memory. The router decides which already-approved records a specific target may receive. It never mutates memory.

See [`docs/ROUTING.md`](docs/ROUTING.md).

### 6. Storage boundary

A storage backend changes how logical artifact bytes are persisted. It never grants epistemic, curation, or disclosure authority.

Phase 7 provides:

- a plaintext `FilesystemBackend` implementing the storage contract;
- an `EncryptedDirectoryBackend` using AES-256-GCM from the maintained `cryptography` package;
- external key-file custody;
- key rotation metadata and resumable rotation journal;
- conservative ciphertext deletion receipts;
- explicit external key-destruction attestations.

See [`docs/STORAGE.md`](docs/STORAGE.md) and [`docs/ENCRYPTION-THREAT-MODELS.md`](docs/ENCRYPTION-THREAT-MODELS.md).

## Source adapters

### Core import adapters

`tools/ai_context.py` retains dependency-light imports for ChatGPT, Claude, generic JSON/JSONL, Markdown/text, and local Git/source trees.

### Higher-churn provider adapters

`tools/provider_import.py` supports Gemini Takeout/My Activity, Grok account exports, browser-chat archives, and data-only community mappings.

```bash
python3 tools/provider_import.py ~/my-ai-context ~/Downloads/MyActivity.json --adapter gemini
python3 tools/provider_import.py ~/my-ai-context ~/Downloads/prod-grok-backend.json --adapter grok
python3 tools/provider_import.py ~/my-ai-context ~/Downloads/chat-export.html --adapter browser-chat
```

Every provider receipt separates adapter identity/version, observed layout, and parse status. Provider-private reasoning-like fields are not silently promoted into ordinary observations.

Provider notes and drift fixtures live under `docs/providers/` and `fixtures/provider-drift/`.

## Phase 4 evidence ingestion

Use `tools/evidence_import.py` when repository/document provenance matters beyond the older generic import path:

```bash
python3 tools/evidence_import.py ~/my-ai-context ~/src/project --adapter repo
python3 tools/evidence_import.py ~/my-ai-context ~/Documents/research --adapter document
python3 tools/evidence_import.py ~/my-ai-context ~/Downloads/Takeout --adapter drive-export
python3 tools/evidence_import.py ~/my-ai-context ~/Downloads/mail.mbox --adapter email
```

Phase 4 keeps duplicate content collapsed without destroying independent provenance paths.

## Phase 5 curation

The public legacy `promote` command is disabled. New canonical memory uses the governed curation path:

```bash
python3 tools/curation.py propose \
  ~/my-ai-context \
  --observation obs.sha256:... \
  --record-type project_state \
  --semantic-key project:alpha

python3 tools/curation.py queue ~/my-ai-context

python3 tools/curation.py review \
  ~/my-ai-context \
  --candidate candidate.sha256:... \
  --decision approve \
  --actor-type human \
  --actor-label local-user

python3 tools/curation.py apply \
  ~/my-ai-context \
  --candidate candidate.sha256:...
```

A local LLM can produce advisory curator envelopes, but even an LLM recommendation of `approve` cannot authorize canonical application.

## Phase 6 selective disclosure and routing

Fresh workspaces receive `profiles/general.json` and private `routing/policy.json`.

Default targets:

- `local-default`: local model, maximum sensitivity `private`;
- `provider-default`: external provider, maximum sensitivity `public`, Phase 5 application authority required.

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --task "debug the Rust parser" \
  --target local-default \
  --output /tmp/local-context.json

python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --task "summarize public release history" \
  --target provider-default \
  --output /tmp/provider-context.json
```

Task selection is deterministic lexical matching plus explicit profile-declared semantic aliases. No hidden embedding/model call decides disclosure.

Hard exclusions can block exact memory IDs, record types, epistemic states, source observations, and exact content paths. Required dependency relationships may bypass positive task/tag relevance only. They never bypass disclosure permission.

## Phase 7 encrypted storage

The core reference path remains dependency-light. Encrypted storage is an optional backend with its own dependency file:

```bash
python -m pip install -r requirements-storage.txt
```

Generate a key **outside** the encrypted store:

```bash
python3 tools/storage.py keygen ~/keys/ai-context.key
```

Initialize a store:

```bash
python3 tools/storage.py init ~/private/ai-context.secure \
  --key-file ~/keys/ai-context.key
```

Persist and recover a logical artifact:

```bash
python3 tools/storage.py put \
  ~/private/ai-context.secure \
  memory/records.jsonl \
  ~/my-ai-context/memory/records.jsonl \
  --key-file ~/keys/ai-context.key

python3 tools/storage.py get \
  ~/private/ai-context.secure \
  memory/records.jsonl \
  /tmp/records.jsonl \
  --key-file ~/keys/ai-context.key
```

Rotate keys:

```bash
python3 tools/storage.py keygen ~/keys/ai-context-next.key
python3 tools/storage.py rotate-key ~/private/ai-context.secure \
  --old-key-file ~/keys/ai-context.key \
  --new-key-file ~/keys/ai-context-next.key
```

If rotation is interrupted, normal access fails closed until `resume-rotation` completes with both external keys.

Delete primary ciphertext with an explicit receipt:

```bash
python3 tools/storage.py delete ~/private/ai-context.secure \
  memory/records.jsonl \
  --key-file ~/keys/ai-context-next.key \
  --reason "user-requested removal"
```

The receipt deliberately claims only that the primary ciphertext object was removed. It does not claim backups, snapshots, old exports, or key copies vanished.

Recovery keys/passphrases/private keys must never be stored in AI-CONTEXT canonical memory, staging, curation state, bundles, storage manifests, or receipts.

Phase 7 defines the storage contract and reference backend. It does **not** yet transparently replace every Phase 1–6 filesystem operation with encrypted virtual I/O. That larger integration can happen without changing canonical semantics.

## Validation

```bash
python3 tools/ai_context.py validate ~/my-ai-context
python3 tools/validate_evidence.py ~/my-ai-context
python3 tools/validate_curation.py ~/my-ai-context
python3 tools/validate_routing.py ~/my-ai-context
python3 tools/validate_routing.py ~/my-ai-context --bundle /tmp/local-context.json
python3 tools/storage.py validate ~/private/ai-context.secure --key-file ~/keys/ai-context-next.key
```

## Memory record model

A durable record carries stable identity, type, structured content, sensitivity, confidence, epistemic state, provenance references, approval, tags, lifecycle/expiry state, and creation/verification metadata.

Important distinctions:

```text
PREFERENCE != FACT
PROJECT_STATE != SCIENTIFIC CLAIM
AI SAID IT != VERIFIED
PRIVATE != SAFE TO DISCLOSE
SOURCE IMPORTED != MEMORY APPROVED
RELEVANT != PERMITTED
ENCRYPTED != AUTHORIZED
DELETED != MERELY HIDDEN
```

## Repository layout

```text
spec/                    protocol and JSON schemas
docs/                    architecture, threat model, curation/routing/storage, provider notes
tools/                   dependency-light reference CLIs + optional storage backend
fixtures/conformance/    standalone valid/invalid protocol fixtures
fixtures/provider-drift/ synthetic provider migration fixtures
tests/                   conformance, security, evidence, curation, routing, storage tests
requirements-storage.txt optional maintained cryptography dependency
```

A real user workspace and encrypted store should live outside this public framework repository or in a separately controlled private location. Key files must live outside the encrypted store.

## Security posture

AI-CONTEXT is **not** a password manager. Do not intentionally preserve credentials, private keys, storage keys, session cookies, recovery codes, bearer tokens, passphrases, or similar secrets as AI memory.

Phase 7 protects stored payload contents against an attacker who gets the encrypted store without the external key. It does not protect plaintext after authorized decryption, a compromised live process, insecure exported plaintext, or every backup/snapshot by itself.

See:

- [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md)
- [`docs/STORAGE.md`](docs/STORAGE.md)
- [`docs/ENCRYPTION-THREAT-MODELS.md`](docs/ENCRYPTION-THREAT-MODELS.md)

## Relationship to QSOL-CONTEXT

`QSOLKCB/QSOL-CONTEXT` is an opinionated private implementation containing a specific user's identity, projects, research, chronology, restore machinery, provenance decisions, and routing policy.

AI-CONTEXT extracts transferable structures such as selective loading, deterministic bundles, typed epistemic records, provenance/honesty boundaries, restore/context continuity without model-identity claims, source precedence, explicit exclusions, routing, receipts, fingerprints, and now a vendor-neutral storage boundary.

It intentionally does **not** inherit QSOL-specific identity, ontology, project names, cultural artifacts, or private data.

## Using AI-CONTEXT with QSOL-SUBSTRATE

`QSOLKCB/QSOL-SUBSTRATE` remains an optional downstream companion for substrate-style delivery machinery such as model adapters, tool-less capsules, deterministic vector projections, model-specific prefix/latent experiments, and model-behaviour probes.

```text
AI-CONTEXT
  private ingestion
  evidence + provenance
  curation / approval
  canonical memory
  selective disclosure / routing
  optional encrypted persistence
        |
        | explicit routed bundle only
        v
QSOL-SUBSTRATE-style downstream layer
  transport adapters
  tool-less capsules
  vector/retrieval projections
  prefix / latent experiments
  probe/evaluation machinery
```

Authority rule:

> **AI-CONTEXT CANONICAL MEMORY > ROUTED BUNDLE > DOWNSTREAM SUBSTRATE PROJECTION**

Encryption does not alter that order.

## Status

Early reference implementation. **Phases 0–7 are complete.** The protocol remains experimental until restore/migration, derived-index, and broader interoperability gates reach the v1.0 release criteria in [`ROADMAP.md`](ROADMAP.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).
