# Phase 6 Selective Disclosure and Routing

Phase 6 controls **what approved canonical memory may leave the private store for one task and one target**.

The authority chain is:

```text
CANONICAL MEMORY
      ↓
PROFILE + TARGET POLICY
      ↓
TASK / TAG RELEVANCE
      ↓
REQUIRED DEPENDENCY CLOSURE
      ↓
ROUTED BUNDLE + DIAGNOSTICS
```

Normative boundaries:

```text
RELEVANT != PERMITTED
DEPENDENCY != PERMISSION BYPASS
LOCAL_MODEL != EXTERNAL_PROVIDER
WITHHELD_RECORD != DIAGNOSTIC OUTPUT
```

Routing is read-only. It never mutates canonical memory, curation records, confidence, lifecycle state, or provenance.

## Public bundle command

The public `tools/ai_context.py bundle` command is routed through Phase 6:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --profile general \
  --task "debug the Rust parser" \
  --target local-default \
  --output /tmp/context.json
```

The legacy `--tags` selector remains available:

```bash
python3 tools/ai_context.py bundle \
  ~/my-ai-context \
  --tags coding,alpha \
  --output /tmp/context.json
```

If neither a task nor positive tag selector is supplied, the profile baseline selects every otherwise eligible canonical record, preserving the original `general` profile behavior.

## Profiles

Profiles live in the private workspace under:

```text
profiles/<name>.json
```

Fresh workspaces receive an upgraded `general` profile. Existing profiles remain compatible because Phase 6 fields are additive and get conservative runtime defaults.

A Phase 6 profile can contain:

```json
{
  "protocol": "AI-CONTEXT/PROFILE",
  "schema_version": "0.1.0",
  "name": "general",
  "include_tags": [],
  "exclude_tags": [],
  "record_types": [],
  "max_sensitivity": "private",
  "task_selector": {
    "enabled": true,
    "min_matches": 1,
    "aliases": {
      "programming": ["rust", "python", "software"]
    },
    "search_notes": false
  },
  "hard_exclusions": {
    "record_ids": [],
    "record_types": [],
    "epistemic_states": [],
    "source_refs": [],
    "content_paths": []
  },
  "dependency_policy": {
    "enabled": true,
    "max_depth": 8,
    "fail_on_cycle": true,
    "include_relationship_records": false
  }
}
```

An empty `record_types` array means all record types are eligible subject to other gates.

## Task / semantic selector

Task routing is deterministic and dependency-free. It does **not** call an embedding model, local LLM, provider, or vector service.

The selector:

1. tokenizes the task;
2. removes a small fixed set of common function words;
3. applies only profile-declared semantic aliases;
4. searches canonical record type, tags, structured content keys/values, and optionally notes;
5. requires at least `min_matches` unique task terms.

For example:

```json
"aliases": {
  "programming": ["rust", "python", "software"]
}
```

allows the task `programming` to select a record containing `Rust`. The alias mapping is explicit policy, not guessed semantics.

If task selection is disabled for a profile but a task is supplied, routing fails closed.

## Provider and local-model targets

Private workspace target policy lives at:

```text
routing/policy.json
```

Fresh workspaces receive two targets:

- `local-default`: `kind=local_model`, sensitivity ceiling `private`;
- `provider-default`: `kind=provider`, sensitivity ceiling `public`, and requires a Phase 5 application receipt.

The provider default is deliberately conservative. A user may define named targets for a specific provider or local runtime, but the framework never assumes that an external provider is equivalent to a local model.

Target policy can constrain:

- sensitivity ceiling;
- minimum confidence;
- allowed/denied record types;
- denied tags;
- denied epistemic states;
- record types that must be `verified`;
- whether a Phase 5 curation application receipt is mandatory;
- target-specific hard exclusions.

The effective sensitivity ceiling is the stricter of the profile ceiling and target ceiling.

## Hard exclusions

Hard exclusions are evaluated after lifecycle/approval checks and before positive relevance selection. They are not aliases for tags.

They may block:

```text
exact memory record ids
record types
epistemic states
source observation ids
exact dotted content paths
```

Example:

```json
"content_paths": [
  "credentials",
  "contact.private_address"
]
```

A matching path blocks the entire canonical record. Phase 6 does not redact or partially rewrite canonical content during routing.

Global routing exclusions, profile exclusions, and target exclusions are combined monotonically. A lower layer cannot cancel a higher-level exclusion.

## Explicit dependency graph

Required memory dependencies are represented by active approved canonical records whose `record_type` is `relationship` and whose content uses:

```json
{
  "relation": "requires",
  "from_memory_id": "memory....",
  "to_memory_id": "memory...."
}
```

or:

```json
{
  "relation": "depends_on",
  "from_memory_id": "memory....",
  "to_semantic_key": "instruction:subject:Rollback procedure"
}
```

Recognized dependency relations are:

```text
requires
depends_on
```

Each endpoint must specify **exactly one** of:

```text
<side>_memory_id
<side>_semantic_key
```

Exact IDs must identify an active approved memory record. Semantic keys are resolved from Phase 5 application metadata plus the same deterministic structured-key derivation used by curation.

A semantic endpoint that resolves to zero records or more than one record is an error. The router does not choose the "best" match.

## Dependency disclosure gate

A required dependency may bypass only **positive relevance selectors** such as task terms or required include tags.

It may never bypass:

```text
approval
active lifecycle state
profile record-type policy
profile/target sensitivity ceiling
target confidence or verification policy
hard exclusions
target curation-application requirement
```

If a required dependency is blocked, bundle construction fails. Silently omitting it could hand the model incomplete context; silently including it could leak material the target is not allowed to receive.

Dependency cycles fail by default. Profiles may opt out of cycle failure, but deterministic visited-node handling still prevents unbounded recursion. Maximum dependency depth is profile-controlled.

Dependency relationship records are routing metadata by default and are not included in the output bundle unless `include_relationship_records` is explicitly enabled.

## Minimum-context diagnostics

Every routed record gets one diagnostic object containing only inclusion information:

```json
{
  "memory_id": "memory....",
  "included_by": ["task_selector"],
  "tag_matches": [],
  "task_matches": ["parser", "rust"],
  "dependency_of": [],
  "dependency_depth": null
}
```

A dependency may instead show:

```json
{
  "memory_id": "memory....",
  "included_by": ["dependency_expansion"],
  "dependency_of": ["memory.parent"],
  "dependency_depth": 1
}
```

Diagnostics deliberately do **not** enumerate rejected records. The bundle explains why included context entered without becoming a side-channel inventory of private material withheld by policy.

## Routed bundle identity

Phase 6 bundles bind:

- canonical memory-store hash;
- normalized profile hash;
- normalized routing-policy hash;
- target name and target kind;
- effective sensitivity ceiling;
- task and deterministic expanded task terms;
- selector tags;
- selected records;
- per-record diagnostics.

The existing `canonical_payload_sha256` covers the complete routed payload.

## Validation and stale-bundle rejection

Validate routing configuration:

```bash
python3 tools/validate_routing.py ~/my-ai-context
```

Validate a routed bundle against the **current** memory, profile, routing policy, task, tags, target, and dependency graph:

```bash
python3 tools/validate_routing.py \
  ~/my-ai-context \
  --bundle /tmp/context.json
```

The validator reruns the deterministic routing decision and requires byte-equivalent canonical JSON. Changing a profile, target policy, hard exclusion, dependency edge, or canonical memory record therefore makes an older bundle stale.

## Security summary

```text
TASK MATCH != DISCLOSURE AUTHORITY
TAG MATCH != DISCLOSURE AUTHORITY
DEPENDENCY != DISCLOSURE AUTHORITY
PROVIDER TARGET != LOCAL TARGET
DIAGNOSTICS != REJECTED-RECORD INVENTORY
ROUTED BUNDLE != CANONICAL MEMORY
```

Phase 6 decides what a consumer receives. It never changes what AI-CONTEXT remembers.
