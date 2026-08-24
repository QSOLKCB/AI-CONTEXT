# Formalization instructions

These rules apply to the post-tag `formal/` tree.

- The theorem subject is exactly AI-CONTEXT `v1.0.0` at commit `53d7d69dfacecf6f8605f5b6a51b2c68ee66572a` and Git tree `2c0592cbd074d7596e70681cc5ed869d6b9b00e4`.
- Do not change the theorem subject to later `main` behavior without creating an explicitly new formalization target/version.
- Lean is a selected-invariant proof layer, not a replacement protocol or a new implementation authority.
- No `sorry`, `admit`, or `axiom` declarations are permitted in the archival theorem set.
- Every named theorem must have exactly one entry in `theorem-inventory.json` mapping it to a frozen invariant, reference implementation surface, and adversarial/conformance evidence.
- Finite counterexamples must remain checked Lean terms, not prose-only examples.
- Do not claim that proofs about authority structure prove AES-GCM, Ed25519, SHA-256, Python, Rust, Git, GitHub Actions, or provider behavior.
- Do not reimplement cryptographic primitives in Lean for Phase 12.
- Keep the formal layer dependency-light. Adding Mathlib or another package requires an explicit archival/reproducibility justification and a newly reviewed dependency pin.
- Keep the pinned `lean-toolchain` stable for the archival theorem set unless a separate reproducibility migration is intentionally performed.
- `lake build` and `python3 tools/validate_formalization.py` must both pass before formalization changes are considered valid.
- The frozen `v1.0.0` tag must never be modified by Phase 12 work.
