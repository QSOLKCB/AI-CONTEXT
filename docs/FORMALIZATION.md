# Phase 12 Lean 4 formalization

Phase 12 is a post-release formal layer over the immutable AI-CONTEXT v1.0.0 reference target.

## Immutable theorem subject

```text
tag:        v1.0.0
commit:     53d7d69dfacecf6f8605f5b6a51b2c68ee66572a
Git tree:   2c0592cbd074d7596e70681cc5ed869d6b9b00e4
protocol:   0.1.0
canonical:  python-json-v0.1
```

The Lean sources were created after the tag. They therefore describe selected invariants of the frozen release; they are not evidence that the Lean files existed in the tagged tree and they do not modify the v1.0.0 protocol.

## Trust and scope

The formal layer proves structural protocol properties such as authority separation, permission gates, lifecycle rules, stale-index rejection, restore claims, and transport/UX non-authority.

It deliberately does **not** claim to prove:

- the Python or Rust implementations line by line;
- AES-GCM, Ed25519, SHA-256, Git, or GitHub Actions implementations;
- semantic truth of remembered content;
- privacy outside the bounded release-audit claim;
- model identity or provider-side state reconstruction.

The maintained cryptographic libraries and language runtimes remain implementation dependencies, not theorem subjects reimplemented in Lean.

## Build

The archival toolchain is pinned in `lean-toolchain` and the project is described by `lakefile.lean`.

```bash
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y
source "$HOME/.elan/env"
lake build
python3 tools/validate_formalization.py
```

No Mathlib dependency is required. The formalization uses Lean 4 core plus Lake only.

## Source layout

```text
formal/
  AIContextFormal.lean
  AIContextFormal/
    Core.lean
    Authority.lean
    Curation.lean
    Disclosure.lean
    Restore.lean
    Storage.lean
    Indexes.lean
    Interop.lean
    UX.lean
    Migration.lean
    Counterexamples.lean
    Theorems.lean
  theorem-inventory.json
```

`Core.lean` defines trust zones, record classes, sensitivity, epistemic state, lifecycle, authority and disclosure targets. The domain modules define the small reference relations used by the theorem set. `Counterexamples.lean` defines finite invalid states. `Theorems.lean` proves the archival theorem set and checks those counterexamples.

## Proof discipline

The archival Lean tree must contain no `sorry`, `admit`, or `axiom` declarations. CI runs `lake build` and `tools/validate_formalization.py`, which checks the proof-placeholder ban, the exact v1.0.0 target binding, the finite counterexample floor, and one-to-one coverage between named Lean theorems and `formal/theorem-inventory.json`.

## Theorem inventory

`formal/theorem-inventory.json` is the machine-readable bridge between four evidence layers:

```text
frozen v1 protocol invariant
        ↓
Lean theorem
        ↓
reference implementation surface
        ↓
adversarial / conformance test
```

This mapping prevents a theorem name from floating free of the behavior it is intended to describe. It also makes the later Zenodo archive able to state precisely what was formalized and what remained executable or library-trust evidence instead.

## Formalization authority rule

```text
v1.0.0 tagged implementation + schemas = formalization target
Lean proofs = selected invariant proofs about that target
Lean proof != proof of every implementation detail
cryptographic library use != reimplementation of cryptographic proofs
```
