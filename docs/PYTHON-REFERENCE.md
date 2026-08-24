# Python reference implementation stabilisation

Phase 10 defines which Python surfaces are public reference entrypoints and which files are internal compatibility machinery.

## Public command surfaces

The v0.1 reference CLI family is:

```text
tools/ai_context.py
tools/provider_import.py
tools/evidence_import.py
tools/validate_evidence.py
tools/curation.py
tools/validate_curation.py
tools/routing.py
tools/validate_routing.py
tools/storage.py
tools/restore.py
tools/indexes.py
tools/interop.py
```

These entrypoints may gain additive commands/options when the protocol permits, but existing protocol-affecting behavior may not silently change.

## Internal compatibility modules

Files whose names end in `_legacy.py` are implementation details retained for auditability and compatibility between phases. They are **not** a stable public Python SDK.

Consumers should use documented CLIs, schemas, fixtures, and capability manifests rather than importing legacy internals.

## Stable Phase 10 programmatic surface

For consumers that deliberately import Python, `tools/interop.py` exposes the small pure/reference functions used by the interoperability tests:

```text
canonical_bytes
capability_manifest
validate_capability_manifest
signature_preimage
sign_bundle
validate_signed_receipt
verify_signed_receipt
validate_conformance_vectors
```

Key-generation functions are explicit side-effecting operations and require external key paths.

## Compatibility classes

A change is **additive-compatible** when it does not alter existing IDs, hashes, authority semantics, required fields, signature preimages, or rejection behavior for previously valid/invalid conformance vectors.

The following require protocol/migration review:

- canonicalizer changes;
- identifier/hash preimage changes;
- sensitivity or lifecycle semantics;
- curation authority changes;
- disclosure/routing authority changes;
- restore continuity semantics;
- index authority/freshness semantics;
- signed-receipt preimage or algorithm changes;
- capability fields that change security interpretation.

## Error posture

Security-sensitive public entrypoints fail closed with a non-zero exit status. Unknown protocol/schema majors are not guessed through. Tools must not silently rewrite private workspace authority state merely to make validation succeed.

## Cross-language rule

Python remains the executable reference implementation for v0.1, but Python object behavior is not itself the interoperability specification. Cross-language consumers use:

1. JSON Schemas;
2. language-neutral fixtures;
3. exact byte/hash vectors;
4. explicit signature preimage rules;
5. capability manifests.

This separation is what allows a Rust verifier to agree with Python without becoming a second canonical-memory authority.
