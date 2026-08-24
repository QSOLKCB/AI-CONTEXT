# RFC 8785 JCS canonicalization evaluation

Phase 10 evaluates RFC 8785 JSON Canonicalization Scheme (JCS) as a possible future AI-CONTEXT canonicalizer. This document is a decision record, not an implementation claim.

## Current state

AI-CONTEXT v0.1 uses the reference identifier:

```text
python-json-v0.1
```

The reference bytes are produced by Python JSON serialization with sorted object keys, compact separators, UTF-8 output, `ensure_ascii = false`, and non-finite numbers rejected.

Those bytes already participate in canonical memory IDs, receipts, bundle hashes, source fingerprints, restore identities, index identities, and conformance fixtures. Changing them is therefore a protocol migration, not a formatting cleanup.

## Why JCS is attractive

RFC 8785 is specifically designed to make JSON data hashable/signable across implementations. A standards-based canonicalizer would reduce the amount of language-specific behavior a future Rust, JavaScript, Go, or other implementation must reproduce.

## Relevant differences

The current Python reference and RFC 8785 are not declared byte-equivalent.

Important differences include:

1. **Number serialization.** JCS uses ECMAScript-compatible JSON number serialization and I-JSON constraints. Python's general JSON number rendering and arbitrary-size integer model are not a protocol-level guarantee of identical JCS bytes for every accepted value.
2. **Object-key sorting.** JCS sorts property names by UTF-16 code units. Python `sort_keys=True` sorts Python Unicode strings by code point order. These orderings agree for many ordinary keys but are not declared equivalent for all Unicode strings.
3. **I-JSON domain constraints.** JCS assumes the I-JSON data model, including interoperable number/string constraints. Existing AI-CONTEXT schemas and runtime code were not originally defined as a complete I-JSON profile.
4. **Escaping/serialization details.** JCS fixes exact primitive serialization rules. AI-CONTEXT currently treats the Python reference bytes as normative for v0.1.

## Phase 10 decision

**JCS is evaluated but not adopted in the v0.1 protocol.**

Reason:

```text
canonicalizer change
    -> identity/hash change
    -> migration requirement
    -> must be explicit at protocol freeze
```

Phase 10 instead adds language-neutral byte/hash vectors for `python-json-v0.1` and deliberately defines signed-bundle signature input independently of JSON canonicalization.

## v1.0 freeze obligation

Before the v1.0 tag, the project must make one explicit choice:

### Option A: retain `python-json-v0.1`

Freeze the supplied conformance vectors and document the accepted cross-language JSON subset strongly enough for independent implementations.

### Option B: introduce a JCS canonicalizer version

Define a new canonicalizer identifier, constrain protocol values to the JCS/I-JSON domain, provide migration fixtures, and decide which existing identities are preserved versus regenerated.

No implementation may silently switch existing IDs/hashes to JCS while continuing to advertise `python-json-v0.1`.

## Signature design consequence

Phase 10 signed bundle receipts use a domain-separated NUL-delimited ASCII preimage containing exact SHA-256 digests and a signer key identifier. Verification therefore does not require Python and Rust to canonicalize the receipt JSON identically before Ed25519 verification.

The JSON receipt still has a normal AI-CONTEXT object identity, but the cryptographic signature itself is intentionally insulated from a future canonicalizer migration.
