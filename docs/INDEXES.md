# Phase 9 Derived Indexes

Phase 9 adds rebuildable retrieval projections over canonical memory.

Core invariants:

```text
INDEX HIT != MEMORY AUTHORITY
INDEX MEMBERSHIP != DISCLOSURE PERMISSION
STALE INDEX != USABLE INDEX
DERIVED INDEX != CANONICAL MEMORY
```

Indexes are private workspace artifacts under:

```text
indexes/
  vector.json
  graph.json
  search.json
  manifest.json
```

The directory is added to the private workspace `.gitignore` by the index builder.

## Retrieval input policy

The reference projections include canonical records only when:

```text
approval == approved
lifecycle.state == active
```

Expired, superseded, tombstoned, rejected, and pending records are excluded from the default retrieval projection.

This rule is deterministic and does not evaluate wall-clock expiry itself. Phase 5 retention enforcement is responsible for changing lifecycle state when expiry becomes authoritative.

Index membership still says nothing about whether a target may receive a record. Phase 6 routing remains the disclosure authority.

## Source fingerprint

Every index carries the same `AI-CONTEXT/DERIVED-SOURCE-FINGERPRINT` object.

It binds:

- canonicalizer identity;
- the complete sorted canonical-memory store SHA-256;
- complete canonical record count;
- the active-approved retrieval-input SHA-256;
- active-approved retrieval record count;
- retrieval policy identity.

The complete canonical store is fingerprinted even if a changed record would not alter the current search terms. This intentionally makes freshness conservative: any canonical mutation invalidates the old projection set.

## Vector projection

`vector.json` uses a dependency-free deterministic reference vectorizer:

```text
Unicode-aware tokenization
  -> case folding
  -> fixed stopword filtering
  -> SHA-256(token) bucket modulo 256
  -> sparse integer token counts
```

The reference vector format deliberately avoids:

- external embedding APIs;
- model-specific latent spaces;
- floating-point normalization requirements;
- hidden remote processing;
- any claim that vector proximity establishes truth or authority.

Future embedding/vector backends may define additional generator identities, but must remain derived projections and carry exact source fingerprints.

## Graph projection

`graph.json` contains deterministic typed nodes and edges derived from active approved canonical records.

It may include:

- active canonical-memory nodes;
- observation-reference nodes named by canonical `source_refs`;
- presentation-neutral semantic-key reference nodes;
- provenance edges from memory to source observations;
- active-memory supersession edges when both endpoints remain active;
- explicit canonical relationship-record edges when endpoints are structurally unambiguous.

The graph projector does **not** guess semantic-key resolution. Semantic endpoints remain semantic reference nodes rather than being silently converted into memory identities.

Relationship records with missing, ambiguous, or inactive exact-memory endpoints are listed under `unresolved_relationships` without fabricating an edge.

## Search cache

`search.json` is a deterministic inverted lexical cache over the same active-approved records.

`tools/indexes.py search` returns candidate memory IDs and matched terms only:

```bash
python3 tools/indexes.py search ~/my-ai-context "rust parser"
```

Search output explicitly declares:

```text
authority = candidate-retrieval-only
disclosure_requires_routing = true
```

Consumers must pass retrieved memories back through normal canonical validation and Phase 6 routing before disclosure.

## Index set manifest

`manifest.json` binds the three derived artifacts by:

- artifact kind;
- path;
- deterministic artifact id;
- exact canonical artifact bytes SHA-256;
- exact byte length;
- common source fingerprint.

The manifest is written only after the three projections have been built.

## Build

```bash
python3 tools/indexes.py build ~/my-ai-context
```

The builder computes all three projections from current canonical memory and replaces the prior index directory as one generated set.

Repeated builds from unchanged canonical state produce byte-identical artifacts.

## Freshness validation

```bash
python3 tools/indexes.py validate ~/my-ai-context
```

Validation:

1. rejects missing/extra index-set files;
2. validates artifact and source-fingerprint identities;
3. verifies manifest artifact hashes;
4. recomputes the current canonical source fingerprint;
5. rebuilds all three projections in memory;
6. requires exact deterministic equality with the stored projection set.

Any canonical mutation therefore makes the old indexes stale.

Search refuses to run on stale indexes.

## Tombstone propagation

Tombstoning a canonical memory record changes the canonical source fingerprint immediately.

The expected sequence is:

```text
build indexes
  -> tombstone canonical record
  -> old indexes fail freshness validation
  -> rebuild indexes
  -> tombstoned record disappears from vector/search/active graph projections
```

The tombstoned canonical record and Phase 5 tombstone receipt still exist in authoritative history. Rebuilding an index removes retrieval eligibility, not historical identity.

## Restore boundary

Derived indexes are not continuity authority and are intentionally excluded from Phase 8 restore archives.

After restore:

```bash
python3 tools/indexes.py build RESTORED_WORKSPACE
```

This guarantees the restored machine derives indexes from the restored canonical source of truth instead of trusting an old cache.

## Authority rule

```text
CANONICAL MEMORY + PROVENANCE
        > ROUTING / DISCLOSURE DECISION
        > DERIVED RETRIEVAL INDEX
```

An index may accelerate finding a candidate. It may never promote, verify, reclassify, resurrect, supersede, tombstone, or disclose that candidate by itself.
