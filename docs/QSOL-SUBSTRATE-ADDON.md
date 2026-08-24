# QSOL-SUBSTRATE as an optional companion

AI-CONTEXT does not depend on QSOL-SUBSTRATE.

If you are not working with QSOL public context or substrate-style delivery experiments, you can ignore QSOL-SUBSTRATE completely.

## What each project does

### AI-CONTEXT

AI-CONTEXT is the private-memory framework. It owns the authority path from user-controlled source material to governed canonical memory and then to selective task-scoped disclosure.

```text
private sources
  -> staging/evidence
  -> candidate
  -> review
  -> canonical memory
  -> routing
  -> selective bundle
```

### QSOL-SUBSTRATE

QSOL-SUBSTRATE is a separate public QSOL context substrate. Its repository provides a public canonical payload plus downstream delivery and evaluation machinery such as:

- model/runtime adapters;
- MICRO/STANDARD/FULL tool-less capsules;
- deterministic vector retrieval;
- model-specific prefix/latent experiment contracts;
- deterministic model-behaviour probes.

Its current canonical content is QSOL-specific. It is not a generic replacement for an AI-CONTEXT private workspace.

## The authority boundary

The safe relationship is:

```text
AI-CONTEXT canonical memory
        |
        | Phase 6 routing
        v
private task-scoped AI-CONTEXT bundle

QSOL-SUBSTRATE canonical public payload
        |
        | substrate adapter/capsule/retrieval tooling
        v
public QSOL context payload
```

A consumer may receive one or both payloads, but they retain separate provenance and authority.

Important rules:

```text
QSOL-SUBSTRATE != AI-CONTEXT CANONICAL MEMORY
PUBLIC SUBSTRATE FACT != PRIVATE MEMORY RECORD
SUBSTRATE RETRIEVAL HIT != AI-CONTEXT DISCLOSURE PERMISSION
SUBSTRATE ADAPTER != MEMORY AUTHORITY
```

Neither repository silently promotes records into the other.

## Pattern A: AI-CONTEXT only

Use this when you simply want portable private context memory for a model or agent.

```text
AI-CONTEXT
  -> selective routed bundle
  -> model / agent / local runtime / provider
```

Typical flow:

```bash
python3 tools/ai_context.py init ~/my-ai-context
python3 tools/ux.py tui ~/my-ai-context

python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --target local-default \
  --task "continue my project" \
  --output /tmp/private-context.json
```

Deliver `/tmp/private-context.json` to the target runtime using whatever transport that runtime supports.

No QSOL-SUBSTRATE checkout is required.

## Pattern B: AI-CONTEXT plus QSOL-SUBSTRATE

Use this when the same model also needs public QSOL context or you want to experiment with QSOL-SUBSTRATE delivery machinery.

Clone both projects separately:

```bash
git clone https://github.com/QSOLKCB/AI-CONTEXT.git
git clone https://github.com/QSOLKCB/QSOL-SUBSTRATE.git
```

### Step 1: build the private task bundle

From AI-CONTEXT:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --target local-default \
  --task "work on a QSOL-related task" \
  --output /tmp/private-ai-context.json
```

### Step 2: choose the public QSOL-SUBSTRATE delivery form

From the QSOL-SUBSTRATE checkout, choose one of its supported consumption modes.

For repository-aware consumers, start with:

```text
ai/bootstrap.json
```

For file-upload/no-tool consumers, build tool-less capsules:

```bash
python tools/build_toolless.py \
  --source-commit "$(git rev-parse HEAD)" \
  --output dist/toolless
```

For vendor/runtime transport adapters:

```bash
python tools/build_adapters.py \
  --source-commit "$(git rev-parse HEAD)" \
  --output dist/adapters
```

For deterministic vector retrieval:

```bash
python tools/build_vectors.py \
  --source-commit "$(git rev-parse HEAD)" \
  --output dist/vectors
```

### Step 3: give the consumer both inputs without pretending they are one store

Conceptually:

```text
private-ai-context.json       public QSOL substrate payload
          |                              |
          +--------------+---------------+
                         v
                    model / agent
```

Keep their provenance labels intact.

If the same claim appears in both places and they disagree, do not silently merge them. Apply the relevant source/authority rules and surface the conflict.

## What is not implemented

There is currently no documented command that automatically imports an arbitrary AI-CONTEXT routed bundle into QSOL-SUBSTRATE canonical storage.

That omission is intentional. The repositories have different roles and authority boundaries.

If a future bridge is added, it should remain an explicit projection/transport layer and must not allow a downstream substrate to mutate AI-CONTEXT canonical memory or bypass Phase 6 routing.

## For non-QSOL users

You probably do not need QSOL-SUBSTRATE itself.

You can still study or reuse its architectural ideas for adapters, capsules, vector retrieval, projection experiments, or evaluation protocols, but those are separate downstream concerns. AI-CONTEXT remains usable as a complete private-context framework without them.

## Short version

```text
Need private portable memory?        Use AI-CONTEXT.
Need public QSOL context too?        Add QSOL-SUBSTRATE.
Need neither public QSOL context
nor its delivery experiments?        Do not install QSOL-SUBSTRATE.
```
