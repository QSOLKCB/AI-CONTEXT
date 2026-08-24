# Phase 8 Portable Restore and Migration

Phase 8 defines how AI-CONTEXT can move governed context between machines, runtimes, and AI-provider environments without claiming to recreate a previous model instance or provider-private state.

Core invariants:

```text
RESTORE = CONTEXT RECONSTRUCTION
RESTORE != MODEL IDENTITY
STYLE/CULTURE != FACTUAL AUTHORITY
RESTORE PORTABILITY != RAW ARCHIVE REPLICATION
```

## Archive format

The reference portable archive uses the suggested `.aicr` extension and is a deterministic ZIP container with stored, sorted members and fixed ZIP timestamps:

```text
manifest.json
migration.json
payload/policy.json
payload/memory/records.jsonl
payload/profiles/...
payload/routing/...
...
```

Every declared payload artifact carries:

- logical path;
- artifact class;
- SHA-256;
- exact byte length;
- required status.

The manifest binds the complete sorted artifact list and the migration manifest hash into a stable `restore.sha256:...` snapshot identity. Operational `created_at` and non-authoritative `extensions` metadata do not change that snapshot identity.

## Continuity classes

### Minimum continuity

The minimum set is intended to recover governed canonical context and build a local routed context without any provider-side memory dependency.

It contains:

- `policy.json`;
- `memory/records.jsonl` (explicitly empty when canonical memory is empty);
- all profile JSON files;
- `routing/policy.json`;
- `curation/policy.json` when the source workspace already has one.

The minimum class deliberately does **not** promise that all historical provenance or curation explanations remain available. For that, use the full working set.

### Full working set

The full class adds portable research/governance state under:

- `receipts/`;
- `staging/`;
- `memory/`;
- `profiles/`;
- `routing/`;
- `curation/`.

This preserves source snapshots, content-addressed evidence, import receipts, candidate/review/application history, mutations, supersession/tombstone records, and routing state where present.

Neither continuity class automatically includes:

- `vault/raw/` provider/source exports;
- generated historical `bundles/`;
- encryption keys or recovery secrets;
- provider-side memory/session state;
- hidden chain of thought or model internals.

Those exclusions are intentional. Raw archives can be preserved separately when the user chooses, but they are not required for context continuity.

## Optional style/culture enrichment

A restore may include one validated `AI-CONTEXT/STYLE-CULTURE-ENRICHMENT` artifact. It is required to declare:

```text
class = style_culture
factual_authority = none
apply_scope = presentation_only
```

It restores to:

```text
enrichment/style-culture.json
```

It never enters `memory/records.jsonl`, never counts as curation evidence, and never changes confidence, epistemic state, sensitivity, or lifecycle.

Examples of suitable enrichment:

- preferred explanatory tone;
- formatting conventions;
- humour/style preferences;
- terminology preferences;
- cultural references useful for presentation.

Unsuitable enrichment includes any attempt to smuggle factual claims, credentials, review authority, or disclosure permission around the canonical-memory and routing layers.

## Migration manifest

Every archive contains `migration.json` using protocol:

```text
AI-CONTEXT/MIGRATION-MANIFEST
```

It records:

- source workspace version;
- target workspace version;
- compatibility class;
- `unknown_major_policy = reject`;
- `additive_metadata_policy = extensions_only_non_authoritative`;
- explicit transformation list.

The Phase 8 reference implementation performs **no hidden payload transformation**. Current exports therefore use exact `0.1.0 -> 0.1.0` migration with an empty transformation list.

If a future runtime needs a semantic migration, it must be represented explicitly rather than pretending a raw byte copy changed protocol meaning.

## Unknown-major rejection

Restore and migration versions use `major.minor.patch` syntax. A manifest or workspace whose major version differs from the supported runtime major fails closed.

This rule exists because a major version can change authority, identity, canonicalization, sensitivity, or lifecycle semantics. Guessing through such a change would make a portable archive look valid while changing what its records mean.

## Additive-compatible metadata

The only generic additive metadata escape hatch in Phase 8 is an `extensions` object.

Rules:

1. unknown top-level fields are rejected;
2. `extensions` is opaque metadata only;
3. extensions do not affect restore snapshot identity;
4. extensions cannot alter artifact hashes;
5. extensions cannot grant factual, curation, migration, routing, or disclosure authority.

This permits harmless future annotations without creating a second hidden protocol inside the manifest.

## Atomic restore

`tools/restore.py restore` refuses to overwrite an existing destination.

The reference workflow is:

1. validate archive structure and metadata;
2. create a temporary sibling workspace;
3. write only declared payload artifacts using safe logical paths;
4. validate canonical records, profiles, routing policy, and, for full restores, evidence/curation state;
5. atomically rename the temporary workspace to the requested destination.

If any step fails, the temporary directory is removed and the destination remains absent.

## Archive safety

The restore reader rejects:

- absolute paths;
- `..` traversal;
- backslash path ambiguity;
- duplicate ZIP member names;
- ZIP symlink members;
- undeclared members;
- missing declared members;
- oversized members;
- oversized aggregate payloads;
- artifact byte-length mismatches;
- artifact SHA-256 mismatches.

The exporter also refuses symlinked workspace artifacts so a full backup cannot silently follow a link into unrelated filesystem content.

## Commands

Create minimum continuity:

```bash
python3 tools/restore.py export \
  ~/my-ai-context \
  ~/backups/context-minimum.aicr \
  --mode minimum
```

Create a full working set:

```bash
python3 tools/restore.py export \
  ~/my-ai-context \
  ~/backups/context-full.aicr \
  --mode full
```

Optionally attach non-authoritative presentation enrichment:

```bash
python3 tools/restore.py export \
  ~/my-ai-context \
  ~/backups/context.aicr \
  --mode minimum \
  --enrichment ~/private/style-culture.json
```

Validate without restoring:

```bash
python3 tools/restore.py validate ~/backups/context.aicr
```

Inspect manifest/migration metadata:

```bash
python3 tools/restore.py inspect ~/backups/context.aicr
```

Cold-start restore:

```bash
python3 tools/restore.py restore \
  ~/backups/context.aicr \
  ~/restored-ai-context
```

The destination must not already exist.

## Encrypted storage interaction

A `.aicr` archive can itself be placed behind the Phase 7 encrypted storage backend or other protected storage. Phase 8 does not embed encryption keys or recovery material into restore manifests.

Encryption and restore remain separate responsibilities:

```text
encryption protects persisted bytes
restore reconstructs portable logical context
neither creates epistemic authority
```

## Provider neutrality

A restore manifest declares:

```text
provider_memory_dependency = none
```

The archive contains AI-CONTEXT state, not ChatGPT memory, Claude memory, Gemini memory, Grok memory, or any other provider-side session database.

See [`CROSS-PROVIDER-RESTORE.md`](CROSS-PROVIDER-RESTORE.md) for the synthetic conformance demonstration.
