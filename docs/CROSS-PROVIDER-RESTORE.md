# Cross-Provider Restore Demonstration

Phase 8 includes an executable synthetic test showing that restored AI-CONTEXT memory is not tied to a provider-side memory system.

The demonstration intentionally does **not** call real provider APIs. Its purpose is to test the protocol boundary, not third-party network behavior.

## Setup

A synthetic workspace contains one approved public canonical memory record and two routing targets:

```text
provider-alpha
provider-beta
```

Both targets are declared as external-provider policy classes with the same public disclosure ceiling.

The workspace is exported as a full `.aicr` archive and restored into a new cold-start path.

## Assertion

After restore, the router independently builds a bundle for each provider target:

```text
restored canonical memory
      |             |
      v             v
provider-alpha   provider-beta
```

The bundle metadata differs because the target name is part of routing identity, but the selected canonical record IDs are identical.

The restore manifest also declares:

```text
provider_memory_dependency = none
restore_claim = context-continuity-not-model-identity
```

Therefore the demonstrated continuity comes from the portable AI-CONTEXT workspace, not from either provider remembering the prior session.

## What this establishes

The demonstration establishes that:

- canonical memory survives a cold-start restore;
- routing can target different provider policy objects after restore;
- the restored canonical record set does not depend on a provider-side memory database;
- target-specific bundle identity remains separate from canonical memory identity.

## What this does not establish

It does not claim that:

- two commercial models will produce identical prose;
- provider APIs have identical privacy behavior;
- hidden provider state can be exported or reconstructed;
- model weights, chain of thought, session caches, or model identity are portable;
- a routed bundle makes one model become another model.

The invariant remains:

```text
RESTORED CONTEXT != ORIGINAL MODEL INSTANCE
```

The executable conformance case is:

```text
tests/test_phase8_restore.py::Phase8RestoreTests::test_cross_provider_restored_context_is_provider_neutral
```
