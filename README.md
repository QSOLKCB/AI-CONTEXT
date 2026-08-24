# AI-CONTEXT

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22081189.svg)](https://doi.org/10.5281/zenodo.22081189)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**A vendor-neutral framework for private, portable, governed AI context memory.**

AI-CONTEXT turns user-controlled sources such as AI exports, repositories, notes, documents, Drive exports, and email archives into **reviewed canonical memory** that can be selectively disclosed to a model or agent between sessions.

> **AI-CONTEXT works by itself. QSOL-SUBSTRATE is optional.**

If you want the shortest practical path, read [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md).

## The 30-second explanation

AI-CONTEXT keeps four questions separate:

1. **What source material do I have?**
2. **What have I explicitly chosen to remember?**
3. **What is this particular model allowed to receive?**
4. **How do I carry that governed context to another session or system?**

That becomes:

```text
PRIVATE SOURCES
      |
      v
[ IMPORT + RECEIPTS ]
      |
      v
[ STAGING / EVIDENCE ]       source material is not memory yet
      |
      v
[ CURATION ]                 propose -> review -> apply
      |
      v
[ CANONICAL MEMORY ]         approved, typed, provenance-linked records
      |
      v
[ ROUTING ]                  target + task + policy + sensitivity
      |
      v
[ SELECTIVE BUNDLE ]         smallest permitted context for this consumer
      |
      v
MODEL / AGENT / LOCAL RUNTIME / EXTERNAL PROVIDER
```

Optional encrypted storage sits beneath the logical artifacts. Portable `.aicr` restore archives carry governed continuity between machines or sessions.

## Start here

### 1. Clone AI-CONTEXT

```bash
git clone https://github.com/QSOLKCB/AI-CONTEXT.git
cd AI-CONTEXT
```

The core path is dependency-light.

### 2. Create a private workspace

Keep real private data outside this public repository:

```bash
python3 tools/ai_context.py init ~/my-ai-context
```

### 3. Use the guided local interface

```bash
python3 tools/ux.py tui ~/my-ai-context
```

The TUI covers imports, candidate review/application, conflicts, provenance, exact bundle inspection, backup, and restore.

### 4. Import something

Example:

```bash
python3 tools/ux.py import \
  ~/my-ai-context \
  ~/Documents/project-notes.md \
  --mode core \
  --yes
```

**Importing does not automatically create memory.** It creates staged evidence and observations.

The import result now exposes the newly staged handles directly:

```json
{
  "observation_ids": [
    "obs.sha256:..."
  ]
}
```

Copy the relevant observation ID into the next step. If an idempotent re-import adds no new row, `observation_ids` can be empty.

### 5. Propose, review, and apply a candidate

```bash
python3 tools/curation.py propose \
  ~/my-ai-context \
  --observation obs.sha256:... \
  --record-type project_state \
  --semantic-key project:example
```

Then review/apply through the TUI, or explicitly:

```bash
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

Only the final approved-and-separately-applied record becomes canonical memory.

### 6. Preview exactly what a local model would receive

```bash
python3 tools/ux.py inspect-bundle \
  ~/my-ai-context \
  --profile general \
  --target local-default \
  --task "continue work on the example project"
```

This is read-only. A preview is not a disclosure event and does not create a bundle file.

`local-default` is a local-consumer policy and can permit private records. A preview or bundle built for `local-default` must **not** be forwarded to an external provider.

### 7. Build the task bundle for the actual consumer

For a local model or local agent:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --target local-default \
  --task "continue work on the example project" \
  --output /tmp/local-ai-context-bundle.json
```

Give that file only to the matching local consumer.

For an external provider, route again using the provider target:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --target provider-default \
  --task "continue work on the example project" \
  --output /tmp/provider-ai-context-bundle.json
```

Transport does not re-run routing. The target used to build the bundle is therefore part of the disclosure decision.

For the detailed walkthrough, including backup and restore, see [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md).

---

# Two ways to use AI-CONTEXT

## Option A: AI-CONTEXT by itself

This is the normal path for users who just want private portable context memory.

```text
AI-CONTEXT
  private source ingestion
  evidence + provenance
  curation proposal
  explicit review
  explicit application
  canonical memory
  selective routing for the actual consumer
  optional encrypted persistence
  portable restore
        |
        v
  task-scoped context bundle
        |
        v
  matching local model / agent / provider target
```

You do **not** need:

- QSOL-SUBSTRATE;
- QSOL-CONTEXT;
- a vector database;
- provider-side memory;
- a particular model vendor;
- a cloud service;
- an embedding model for disclosure decisions.

## Option B: AI-CONTEXT + optional QSOL-SUBSTRATE

[`QSOLKCB/QSOL-SUBSTRATE`](https://github.com/QSOLKCB/QSOL-SUBSTRATE) is a separate public QSOL context and delivery project. It can be useful when the same model also needs public QSOL context or when you want its adapter, capsule, vector, projection, or model-evaluation machinery.

It is **not an AI-CONTEXT dependency**.

A safe combined setup looks like this:

```text
PRIVATE CONTEXT                         PUBLIC QSOL CONTEXT

AI-CONTEXT                              QSOL-SUBSTRATE
    |                                         |
    | Phase 6 routing                         | adapter / capsule / retrieval
    | for actual consumer                     |
    v                                         v
private task bundle                   public substrate payload
    |                                         |
    +-------------------+---------------------+
                        v
                   model / agent
```

The two inputs keep separate provenance and authority.

There is no documented command that silently imports an arbitrary AI-CONTEXT bundle into QSOL-SUBSTRATE canonical storage, and QSOL-SUBSTRATE cannot mutate AI-CONTEXT canonical memory.

If you are not working with QSOL context, you can ignore QSOL-SUBSTRATE entirely.

See [`docs/QSOL-SUBSTRATE-ADDON.md`](docs/QSOL-SUBSTRATE-ADDON.md) for both usage patterns and the exact authority boundary.

## Why keep them separate?

AI-CONTEXT answers:

> What private context has this user governed, and what may this target receive?

QSOL-SUBSTRATE answers:

> What public QSOL context and delivery/evaluation machinery is available to this consumer?

Those are related problems, but they are not the same authority domain.

---

# Core rules

These are invariants, not slogans:

```text
SOURCE MATERIAL != CANONICAL MEMORY
CANDIDATE != MEMORY
LLM SUGGESTION != REVIEW DECISION
REVIEW APPROVAL != CANONICAL APPLICATION
RELEVANT != PERMITTED
DEPENDENCY != PERMISSION BYPASS
ROUTED BUNDLE != CANONICAL MEMORY
LOCAL TARGET != EXTERNAL PROVIDER TARGET
ENCRYPTION AT REST != MEMORY AUTHORITY
KEY ID != KEY MATERIAL
RESTORE != MODEL IDENTITY
STYLE/CULTURE != FACTUAL AUTHORITY
INDEX HIT != MEMORY AUTHORITY
SIGNATURE != DISCLOSURE AUTHORITY
CAPABILITY CLAIM != PERMISSION
TUI ACTION != AUTHORITY
PREVIEW != DISCLOSURE
```

The practical result is simple: **useful context does not automatically acquire authority merely because it exists, matches a query, is encrypted, is signed, or came from an AI.**

# What AI-CONTEXT is not

This section exists to prevent accidental over-claims.

AI-CONTEXT is **not**:

- an AI model;
- a replacement for model weights or provider APIs;
- a claim that an AI literally remembers prior sessions internally;
- a mechanism for recreating an original model instance;
- a password manager or secret vault;
- a truth oracle;
- a claim that imported text is correct merely because it was imported;
- a claim that signatures make content true or permitted;
- a claim that vector similarity grants disclosure authority;
- a claim that every line of Python/Rust or every cryptographic primitive has been formally proved;
- dependent on QSOL-SUBSTRATE;
- a public copy of anyone's private AI context.

Restore means **context reconstruction**, not identity reconstruction.

Formal verification means **selected protocol invariants were proved in Lean 4**, not that every implementation detail, cryptographic library, runtime, provider, or external system has been mathematically verified.

# What happens to your data?

A real user's workspace should be kept in a private location outside this public repository.

The public AI-CONTEXT repo ships synthetic fixtures and protocol machinery. It does not require real private exports to be committed publicly.

Typical private workspace material includes:

```text
raw source material
staging observations
receipts and source evidence
curation state
canonical memory
routing policy and profiles
generated bundles
derived indexes
restore archives
```

Credentials, private keys, recovery codes, bearer tokens, storage keys, session cookies, and similar secrets should **not** become canonical AI memory.

See [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md).

# Curation: importing is not remembering

A source can be useful and still be wrong.

Examples:

- an old AI response can contain a hallucination;
- an email can repeat a false claim;
- a repository can be stale;
- two sources can conflict;
- a preference should not silently become a fact.

AI-CONTEXT therefore uses:

```text
SOURCE EVIDENCE
      |
      v
PENDING CANDIDATE
      |
      v
HUMAN / POLICY REVIEW
      |
      v
EXPLICIT APPLICATION
      |
      v
CANONICAL MEMORY
```

Automated semantic extractors and local LLM curators may propose. They may not self-promote.

See [`docs/CURATION.md`](docs/CURATION.md).

# Selective disclosure: relevance is not permission

A record can be relevant to a task and still be forbidden for the selected consumer.

Routing considers:

- profile;
- task and tags;
- target class;
- sensitivity ceiling;
- lifecycle state;
- approval/application authority;
- hard exclusions;
- dependency closure.

Default target classes distinguish local models from external providers. A required dependency may bypass positive relevance selection only. It may **not** bypass disclosure permission.

A routed bundle is valid for the target policy used to build it. Do not build with a local target and then forward the resulting bytes to an external provider. Re-route for the actual consumer.

See [`docs/ROUTING.md`](docs/ROUTING.md).

# Source adapters

Core imports support ChatGPT-style and Claude-style exports, generic JSON/JSONL, Markdown/text, and local source trees.

Higher-churn provider adapters cover Gemini export surfaces, Grok/xAI account exports, browser-chat archives, and data-only community mappings.

Examples:

```bash
python3 tools/provider_import.py ~/my-ai-context ~/Downloads/MyActivity.json --adapter gemini
python3 tools/provider_import.py ~/my-ai-context ~/Downloads/prod-grok-backend.json --adapter grok
python3 tools/provider_import.py ~/my-ai-context ~/Downloads/chat-export.html --adapter browser-chat
```

Provider layouts are treated as versioned/unstable inputs, not timeless APIs. Unknown layouts fail explicitly or remain labelled partial/generic rather than being presented as exact parses.

# Repository, document, Drive, and email evidence

Use the evidence path when source identity and provenance matter beyond a basic text import:

```bash
python3 tools/evidence_import.py ~/my-ai-context ~/src/project --adapter repo
python3 tools/evidence_import.py ~/my-ai-context ~/Documents/research --adapter document
python3 tools/evidence_import.py ~/my-ai-context ~/Downloads/Takeout --adapter drive-export
python3 tools/evidence_import.py ~/my-ai-context ~/Downloads/mail.mbox --adapter email
```

See:

- [`docs/SOURCE-EVIDENCE.md`](docs/SOURCE-EVIDENCE.md)
- [`docs/GOOGLE-DRIVE-EXPORTS.md`](docs/GOOGLE-DRIVE-EXPORTS.md)
- [`docs/EMAIL-ARCHIVES.md`](docs/EMAIL-ARCHIVES.md)

# Optional encrypted storage

Encryption is a persistence property. It does not make content true, approved, or disclosable.

Install the optional maintained dependency:

```bash
python -m pip install -r requirements-storage.txt
```

Generate keys **outside** the encrypted store:

```bash
python3 tools/storage.py keygen ~/keys/ai-context.key
```

Initialize an encrypted directory store:

```bash
python3 tools/storage.py init ~/private/ai-context.secure \
  --key-file ~/keys/ai-context.key
```

See [`docs/STORAGE.md`](docs/STORAGE.md) and [`docs/ENCRYPTION-THREAT-MODELS.md`](docs/ENCRYPTION-THREAT-MODELS.md).

# Portable backup and restore

Create a minimum continuity archive:

```bash
python3 tools/ux.py backup \
  ~/my-ai-context \
  ~/backups/context.aicr \
  --mode minimum \
  --yes
```

Restore into a new workspace:

```bash
python3 tools/ux.py restore \
  ~/backups/context.aicr \
  ~/restored-ai-context \
  --yes
```

Portable archives declare that provider-side memory is not a prerequisite.

```text
provider_memory_dependency = none
restore_claim = context-continuity-not-model-identity
```

See [`docs/RESTORE.md`](docs/RESTORE.md) and [`docs/CROSS-PROVIDER-RESTORE.md`](docs/CROSS-PROVIDER-RESTORE.md).

# Derived indexes

AI-CONTEXT can build deterministic vector, graph, and lexical search projections over approved active canonical memory.

They are caches and retrieval accelerators only.

```text
INDEX HIT != MEMORY AUTHORITY
INDEX MEMBERSHIP != DISCLOSURE PERMISSION
STALE INDEX != USABLE INDEX
```

A retrieved candidate returns to canonical validation and routing before disclosure.

See [`docs/INDEXES.md`](docs/INDEXES.md).

# Interoperability

The v1.0.0 interoperability layer includes:

- stabilized Python reference surfaces;
- language-neutral conformance vectors;
- an independent Rust verifier;
- Ed25519 signed bundle receipts;
- capability manifests;
- read-only MCP/tool examples.

A valid signature establishes byte integrity under a key. It does not create factual truth, identity trust, memory authority, or disclosure permission.

See [`docs/INTEROPERABILITY.md`](docs/INTEROPERABILITY.md).

# Lean 4 formalization

The Lean 4 formalization was created **after** the immutable `v1.0.0` tag and targets that exact frozen release.

Reference target:

```text
tag:      v1.0.0
commit:   53d7d69dfacecf6f8605f5b6a51b2c68ee66572a
Git tree: 2c0592cbd074d7596e70681cc5ed869d6b9b00e4
```

The proof layer establishes selected structural invariants around authority, curation, sensitivity, disclosure, restore, storage, indexes, signatures/tools, UX, and migration.

It deliberately does **not** re-prove AES-GCM, Ed25519, SHA-256, Python, Rust, Git, GitHub Actions, provider behavior, or every implementation detail.

See [`docs/FORMALIZATION.md`](docs/FORMALIZATION.md).

# Validation and tests

Useful validation commands include:

```bash
python3 tools/ai_context.py validate ~/my-ai-context
python3 tools/validate_evidence.py ~/my-ai-context
python3 tools/validate_curation.py ~/my-ai-context
python3 tools/validate_routing.py ~/my-ai-context
python3 tools/validate_formalization.py
```

The release and archival CI also exercises Python conformance/security/adversarial tests, Python/Rust interoperability, the tracked-public-tree release audit, Lean builds, deterministic archival generation, independent archive verification, and reproduction from the generated scholarly source ZIP.

Passing finite tests does not mean every possible future input has been proved safe. The project states test and proof scope explicitly rather than converting finite evidence into universal claims.

# Documentation map

If you want to...

| Goal | Read |
|---|---|
| get running quickly | [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md) |
| understand the full architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| understand threat assumptions | [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md) |
| understand evidence/provenance | [`docs/SOURCE-EVIDENCE.md`](docs/SOURCE-EVIDENCE.md) |
| understand review and memory authority | [`docs/CURATION.md`](docs/CURATION.md) |
| understand selective disclosure | [`docs/ROUTING.md`](docs/ROUTING.md) |
| use encrypted storage | [`docs/STORAGE.md`](docs/STORAGE.md) |
| back up / restore context | [`docs/RESTORE.md`](docs/RESTORE.md) |
| understand derived retrieval | [`docs/INDEXES.md`](docs/INDEXES.md) |
| integrate another runtime/language | [`docs/INTEROPERABILITY.md`](docs/INTEROPERABILITY.md) |
| understand Lean proof scope | [`docs/FORMALIZATION.md`](docs/FORMALIZATION.md) |
| use QSOL-SUBSTRATE optionally | [`docs/QSOL-SUBSTRATE-ADDON.md`](docs/QSOL-SUBSTRATE-ADDON.md) |
| inspect the scholarly archive | [`docs/ARCHIVAL-RECORD.md`](docs/ARCHIVAL-RECORD.md) |

# Relationship to QSOL-CONTEXT

`QSOLKCB/QSOL-CONTEXT` is an opinionated private implementation containing one user's private identity/context/project state and associated policies.

AI-CONTEXT extracts the reusable architecture without inheriting that private content or QSOL-specific personal ontology.

In other words:

```text
QSOL-CONTEXT = one private deployment / design ancestor
AI-CONTEXT   = reusable public framework
```

# Release identity and scholarly record

**AI-CONTEXT v1.0.0 is frozen and published.**

- Version: `1.0.0`
- License: Apache-2.0
- Frozen tag: `v1.0.0`
- Frozen implementation commit: `53d7d69dfacecf6f8605f5b6a51b2c68ee66572a`
- DOI: [`10.5281/zenodo.22081189`](https://doi.org/10.5281/zenodo.22081189)
- Resource type: Software

Canonical citation:

> Slade, T. (2026). *AI-CONTEXT v1.0.0: A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory* (Version 1.0.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.22081189

The Zenodo record contains the reproducible three-file scholarly surface:

```text
AI-CONTEXT-1.0.0-source.zip
AI-CONTEXT-v1.0.0-Overview.pdf
RELEASE-NOTES.md
```

The source archive explicitly separates the exact frozen `v1.0.0` implementation tree from the later Lean formalization so archival provenance does not pretend that post-tag proof files existed in the original release.

# Repository layout

```text
spec/                    protocol and JSON schemas
docs/                    human-facing architecture and usage documentation
tools/                   reference CLIs, validators, storage/restore/archive tooling
fixtures/                 synthetic conformance/provider/interoperability fixtures
tests/                    conformance, security and adversarial tests
rust/                     independent interoperability verifier
formal/                   post-tag Lean 4 selected-invariant formalization
release/                  release freeze/formalization target metadata
```

# License

Apache-2.0. See [`LICENSE`](LICENSE).

---

**The shortest useful summary:** AI-CONTEXT lets you keep the memory store yours, make remembering an explicit governed act, and decide what each AI actually gets to see.
