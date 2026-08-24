# Phase 10 interoperability

Phase 10 stabilizes the wire-facing reference surfaces needed for independent consumers without granting those consumers new memory, curation, or disclosure authority.

Core invariants:

```text
SIGNATURE != DISCLOSURE AUTHORITY
SIGNATURE != EPISTEMIC AUTHORITY
CAPABILITY CLAIM != PERMISSION
MCP TOOL != MEMORY AUTHORITY
LANGUAGE INTEROP != CANONICAL AUTHORITY DUPLICATION
```

## Stabilized Python reference surface

The public Phase 10 interoperability entrypoint is:

```bash
python3 tools/interop.py ...
```

Its stable v0.1 operations are:

- `capabilities`: emit the machine-readable implementation capability manifest;
- `sign-keygen`: generate an external Ed25519 signing key pair;
- `sign-bundle`: create an integrity-only signed receipt for exact bundle bytes;
- `verify-receipt`: verify exact bundle bytes and an Ed25519 receipt;
- `validate-fixtures`: validate the language-neutral interoperability corpus.

Earlier phase CLIs remain the authorities for their own domains:

```text
tools/ai_context.py       import / core validation / routed bundle entrypoint
tools/curation.py         curation authority writes
tools/routing.py          disclosure planning
tools/storage.py          encrypted persistence
tools/restore.py          continuity reconstruction
tools/indexes.py          candidate retrieval projections
tools/interop.py          capability + wire-integrity interoperability
```

`tools/interop.py` does not expose promotion, review, mutation, routing-policy mutation, or provider disclosure commands.

## Capability manifest

`AI-CONTEXT/CAPABILITY-MANIFEST` tells a model, agent, tool host, or alternate implementation which reference surfaces exist and which authority limits apply.

Generate it with:

```bash
python3 tools/interop.py capabilities
```

or write the canonical JSON artifact:

```bash
python3 tools/interop.py capabilities --output /tmp/capabilities.json
```

A consumer must treat the manifest as a capability declaration, not permission to access arbitrary workspace state.

## Signed bundle receipts

Signed receipts use Ed25519 from the maintained `cryptography` package.

Install the optional signing dependency:

```bash
python -m pip install -r requirements-signing.txt
```

Generate signing keys outside the AI-CONTEXT workspace:

```bash
python3 tools/interop.py sign-keygen \
  ~/keys/ai-context-signing.key \
  --public-key ~/keys/ai-context-signing.pub
```

Sign an already-built AI-CONTEXT bundle:

```bash
python3 tools/interop.py sign-bundle \
  /tmp/context.json \
  --private-key ~/keys/ai-context-signing.key \
  --receipt /tmp/context.receipt.json
```

Verify using the embedded public key only:

```bash
python3 tools/interop.py verify-receipt \
  /tmp/context.json \
  /tmp/context.receipt.json
```

Or additionally require an independently supplied public key as a trust anchor:

```bash
python3 tools/interop.py verify-receipt \
  /tmp/context.json \
  /tmp/context.receipt.json \
  --public-key ~/keys/ai-context-signing.pub
```

The receipt binds:

- exact bundle file SHA-256;
- bundle `canonical_payload_sha256`;
- bundle `canonical_store_sha256`;
- signer public-key identifier;
- receipt/signature protocol version.

The signature preimage is domain-separated ASCII rather than receipt JSON canonical bytes:

```text
AI-CONTEXT/SIGNED-BUNDLE-RECEIPT\0
0.1.0\0
Ed25519\0
<bundle_sha256>\0
<bundle_payload_sha256>\0
<canonical_store_sha256>\0
<signer_key_id>\0
```

This makes signature verification portable even if a future protocol version changes JSON canonicalization.

A valid signature proves only that the corresponding private key signed those bound hashes. It does not prove the bundle is true, currently permitted, complete, fresh, or endorsed by a trusted human unless the verifier separately trusts the signer identity and re-applies relevant protocol checks.

## Language-neutral fixtures

The canonical interoperability corpus is:

```text
fixtures/interoperability/conformance-v0.1.0.json
```

It contains:

- JSON input objects;
- exact `python-json-v0.1` UTF-8 bytes encoded as hex;
- SHA-256 digests of those bytes;
- a public-only Ed25519 signature vector;
- exact signature preimage bytes and SHA-256.

Validate it in Python:

```bash
python3 tools/interop.py validate-fixtures \
  fixtures/interoperability/conformance-v0.1.0.json
```

The fixtures are data-only and contain no private signing key.

## Rust verifier

`rust/ai-context-interop/` is a deliberately small independent verifier suitable for hardened local consumers.

It can:

- validate core capability-manifest authority rules;
- verify the language-neutral canonicalization/signature corpus;
- verify exact bundle bytes against a signed receipt;
- verify Ed25519 signatures and signer-key identifiers.

It cannot mutate canonical memory, approve candidates, alter routing policy, or infer disclosure permission.

Example:

```bash
cargo run --manifest-path rust/ai-context-interop/Cargo.toml -- \
  validate-fixtures fixtures/interoperability/conformance-v0.1.0.json
```

The Rust verifier intentionally does not claim complete cross-language reproduction of every historical Python object identity. The conformance corpus defines the tested v0.1 overlap while `docs/JCS-EVALUATION.md` records the canonicalization migration question.

## MCP and generic tool adapters

Phase 10 ships read-only adapter examples under:

```text
examples/mcp/
examples/tool-adapter.json
```

The examples expose capability discovery, candidate retrieval, bundle validation, and signature verification. They intentionally do not expose direct canonical-memory writes or curation approval.

MCP/tool hosts remain transport layers. Tool availability never changes the underlying authority graph.

## Canonicalization decision

See `docs/JCS-EVALUATION.md`.

Phase 10 status:

```text
python-json-v0.1 = active
RFC 8785 JCS = evaluated-not-adopted
canonicalizer change = explicit migration/freeze decision
```
