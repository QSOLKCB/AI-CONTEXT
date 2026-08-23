# Phase 5 Curation Engine

Phase 5 turns staged evidence into governed canonical memory without granting automated systems write authority over the canonical store.

The central security boundary is:

```text
SOURCE EVIDENCE
    ↓
CANDIDATE PROPOSAL
    ↓
HUMAN / POLICY REVIEW
    ↓
EXPLICIT APPLY
    ↓
CANONICAL MEMORY
```

The following invariants are normative:

```text
CANDIDATE != MEMORY
LLM_SUGGESTION != REVIEW_DECISION
REVIEW_APPROVAL != AUTOMATED_AUTHORITY
CONTENT_CORRECTION = NEW_RECORD + SUPERSESSION
TOMBSTONE != DELETE_HISTORY
```

## Initialize curation state

Existing AI-CONTEXT workspaces remain compatible. Phase 5 creates its own state lazily:

```bash
python3 tools/curation.py init ~/my-ai-context
```

This creates `curation/policy.json` and append-only queue/receipt files as they are needed.

## Candidate generation

A deterministic extractor can propose memory from one or more staging observations:

```bash
python3 tools/curation.py propose \
  ~/my-ai-context \
  --observation obs.sha256:... \
  --record-type claim \
  --semantic-key project:alpha \
  --tags research,alpha
```

Generated candidates always carry `approval: pending`. Proposal generation never calls the canonical-memory apply path.

A candidate may also be created from an explicitly supplied JSON content object using `--content-file`. Source-free non-user assertions will still fail canonical validation at apply time.

## Human review queue

```bash
python3 tools/curation.py queue ~/my-ai-context

python3 tools/curation.py review \
  ~/my-ai-context \
  --candidate candidate.sha256:... \
  --decision approve \
  --actor-type human \
  --actor-label local-user
```

Only `human` and `policy` actors can issue review decisions that authorize application.

Approval and application are separate operations:

```bash
python3 tools/curation.py apply \
  ~/my-ai-context \
  --candidate candidate.sha256:...
```

This separation is intentional. A candidate generator, local LLM, or parser cannot self-promote merely by emitting an approval-shaped object.

## Local-LLM curator interface

The optional local-LLM interface is transport-only and advisory.

Export a request:

```bash
python3 tools/curation.py llm-request \
  ~/my-ai-context \
  --candidate candidate.sha256:... \
  --output /tmp/curator-request.json
```

A local model/runtime may consume that request and produce an `AI-CONTEXT/CURATOR-RESPONSE` envelope.

Import the suggestion:

```bash
python3 tools/curation.py llm-import \
  ~/my-ai-context \
  --input /tmp/curator-response.json
```

Even a response whose recommendation is `approve` has zero canonical-memory authority. It is stored as `AI-CONTEXT/LLM-SUGGESTION` and still requires an independent human/policy review decision.

If the response contains a revised `proposed_memory`, `--create-candidate` may create a new pending candidate whose generator is recorded as `local_llm`.

## Conflict detection

Candidates may carry a `semantic_key`. If omitted, the reference implementation can derive one from structured fields such as `memory_key`, `key`, `subject`, `project`, `name`, or `id`.

A candidate conflicts with an active canonical record when:

1. record type matches;
2. semantic key matches; and
3. content differs.

Inspect conflicts with:

```bash
python3 tools/curation.py conflicts \
  ~/my-ai-context \
  --candidate candidate.sha256:...
```

An approval with open conflicts must explicitly choose a resolution such as `coexist`, `supersede_existing`, or `manual_merge`.

## Supersession graph

Content is not edited in place to correct meaning. A corrected memory is a new canonical record and the old record is retired through an explicit edge:

```text
old memory ──SUPERSEDED_BY──> new memory
```

The edge lives in `curation/supersession.jsonl` and references the mutation receipt that changed the old lifecycle state to `superseded`.

A standalone edge can be created with:

```bash
python3 tools/curation.py supersede \
  ~/my-ai-context \
  --old memory.old \
  --new memory.new \
  --reason "corrected project state"
```

## Confidence and verification workflow

Confidence can change without rewriting content:

```bash
python3 tools/curation.py confidence \
  ~/my-ai-context \
  --memory memory.id \
  --confidence 0.8 \
  --reason "new evidence changes calibration"
```

Verification requires explicit evidence references:

```bash
python3 tools/curation.py verify \
  ~/my-ai-context \
  --memory memory.id \
  --confidence 0.95 \
  --evidence obs.sha256:... \
  --reason "confirmed against primary evidence"
```

Curation mutations may change only:

```text
confidence
epistemic_state
last_verified
lifecycle
```

They cannot silently rewrite content, sensitivity, record type, tags, or provenance.

## Retention and expiry

Phase 5 maintains an independent curation policy:

```bash
python3 tools/curation.py retention-policy \
  ~/my-ai-context \
  --default-days 365 \
  --rule event=90 \
  --rule preference=none
```

Then enforce it:

```bash
python3 tools/curation.py enforce-retention ~/my-ai-context
```

Policy enforcement may assign missing `expires_at` values and transition active records to `expired` when their deadline is reached. Expiry is a lifecycle mutation, not deletion.

## Tombstones

```bash
python3 tools/curation.py tombstone \
  ~/my-ai-context \
  --memory memory.id \
  --reason "user requested removal from active memory"
```

The canonical record remains historically present with lifecycle state `tombstoned`, while new disclosure bundles exclude it. A separate `AI-CONTEXT/TOMBSTONE` receipt records the before/after hashes, mutation id, actor, and reason.

## Why is this remembered?

```bash
python3 tools/curation.py explain \
  ~/my-ai-context \
  --memory memory.id
```

For structured output:

```bash
python3 tools/curation.py explain \
  ~/my-ai-context \
  --memory memory.id \
  --format json
```

The explanation can walk through:

- the current canonical record;
- candidate and application receipts;
- human/policy review decisions;
- confidence/verification/lifecycle mutations;
- supersession and tombstone history;
- source observations;
- import receipts;
- Phase 4 source snapshots;
- content-addressed evidence objects.

## Validation

Run all relevant validators after curation work:

```bash
python3 tools/ai_context.py validate ~/my-ai-context
python3 tools/validate_evidence.py ~/my-ai-context
python3 tools/validate_curation.py ~/my-ai-context
```

The curation validator checks candidate identities, review chains, application authority, conflict references, mutation chains, supersession edges, tombstone receipts, LLM-suggestion references, and current-memory hashes.

## Security gate

No automated semantic extractor or local LLM response may write canonical memory directly.

The only reference path from candidate to canonical memory is an explicit `apply` operation backed by a latest `approve` decision whose actor type is `human` or `policy`.
