# Getting started with AI-CONTEXT

This guide is the shortest practical path from an empty checkout to a governed context bundle.

AI-CONTEXT works **standalone**. You do not need QSOL-SUBSTRATE, a vector database, an external model provider, or provider-side memory to use it.

## 1. Clone the repository

```bash
git clone https://github.com/QSOLKCB/AI-CONTEXT.git
cd AI-CONTEXT
```

The core reference path is dependency-light. Optional features such as encrypted storage and archival report generation have separate requirement files.

## 2. Create a private workspace

Keep the workspace outside the public repository:

```bash
python3 tools/ai_context.py init ~/my-ai-context
```

This creates the private workspace structure, default profile, routing policy, and ignore rules.

A workspace is **not** the public AI-CONTEXT repository. Do not commit your real private workspace, exports, keys, or generated private bundles to this repository.

## 3. Open the local operator interface

```bash
python3 tools/ux.py tui ~/my-ai-context
```

The TUI provides a guided interface for imports, review/application, conflict inspection, provenance, bundle inspection, backup, and restore.

Important: importing a file does **not** automatically remember its contents.

The authority path is:

```text
source
  -> staging observation
  -> candidate
  -> explicit review
  -> explicit application
  -> canonical memory
```

## 4. Import source material

The TUI can import common sources, or use the CLI directly.

Example:

```bash
python3 tools/ux.py import \
  ~/my-ai-context \
  ~/Documents/project-notes.md \
  --mode core \
  --yes
```

Provider-specific and evidence-oriented importers are also available. See the root README and the provider/evidence documentation for those paths.

The import result contains observation identifiers such as:

```text
obs.sha256:...
```

These are staged evidence, not canonical memory.

## 5. Propose a memory candidate

Candidate creation is explicit. For example:

```bash
python3 tools/curation.py propose \
  ~/my-ai-context \
  --observation obs.sha256:... \
  --record-type project_state \
  --semantic-key project:example
```

The candidate remains pending. Candidate generation cannot approve or apply itself.

## 6. Review and apply

The easiest path is the TUI:

```bash
python3 tools/ux.py tui ~/my-ai-context
```

Choose the candidate-review option. Approval and application are separate authority events.

You can also use the CLI:

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

Only an explicitly approved and applied candidate becomes canonical memory.

## 7. Preview exactly what a model would receive

Previewing is read-only and does not create a disclosure bundle:

```bash
python3 tools/ux.py inspect-bundle \
  ~/my-ai-context \
  --profile general \
  --target local-default \
  --task "continue work on the example project"
```

The preview shows the exact routed payload under the current profile and disclosure policy.

## 8. Write a routed bundle

When you actually need a context file for a model or agent:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --target local-default \
  --task "continue work on the example project" \
  --output /tmp/ai-context-bundle.json
```

For an external provider, use the configured provider target instead. Default provider policy is intentionally stricter than the default local-model policy.

A routed bundle is a task-scoped projection. It is not a second canonical memory store.

## 9. Give the bundle to your model or agent

How you deliver `/tmp/ai-context-bundle.json` depends on the consumer:

- attach it to a chat that accepts files;
- put its contents into a system/developer context field;
- load it in a local agent/runtime;
- use a read-only tool/MCP integration;
- pass it through your own transport adapter.

AI-CONTEXT does not require a specific model vendor or runtime.

## 10. Back up and restore

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

Restore reconstructs governed context. It does not recreate an original model instance, hidden reasoning state, or provider-side memory.

## Optional: encrypted storage

Encrypted storage is optional and uses the maintained `cryptography` package:

```bash
python -m pip install -r requirements-storage.txt
```

Keys remain external to AI-CONTEXT memory and outside the encrypted store. See [`STORAGE.md`](STORAGE.md).

## Optional: QSOL-SUBSTRATE

QSOL-SUBSTRATE is **not required** for any step above.

It is a separate public QSOL context and delivery project. Use it only if you want its public QSOL knowledge substrate, model adapters, tool-less capsules, vector retrieval, projection experiments, or model-behaviour probe machinery.

See [`QSOL-SUBSTRATE-ADDON.md`](QSOL-SUBSTRATE-ADDON.md) for the exact relationship and two safe usage patterns.

## The three rules worth remembering

```text
SOURCE != MEMORY
RELEVANT != PERMITTED
RESTORE != MODEL IDENTITY
```

Everything else in the architecture follows from keeping evidence, authority, disclosure, and continuity separate.
