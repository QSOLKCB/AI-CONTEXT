# AI-CONTEXT Conformance Fixtures

These fixtures are synthetic, public, and contain no real user data.

Each protocol schema in `spec/` has a standalone valid and invalid fixture. CI validates the fixtures with JSON Schema Draft 2020-12 using the pinned development-only `jsonschema` dependency in `requirements-dev.txt`.

The fixture layer is intentionally separate from the runtime CLI. `tools/ai_context.py` remains Python-standard-library-only; JSON Schema tooling is a conformance/test dependency, not a runtime requirement.

Files under `valid/` must validate against their named schema. Files under `invalid/` must fail that schema. Runtime/security tests in `tests/test_ai_context.py` additionally cover invariants that JSON Schema alone cannot express, including derived hashes, provenance linkage, privacy ceilings, source mutation, secret screening, idempotency, and tombstone propagation.
