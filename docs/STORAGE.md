# Phase 7 — Encrypted storage boundary

Phase 7 defines a storage interface below AI-CONTEXT canonical semantics and provides a reference encrypted directory backend.

The central rule is:

```text
ENCRYPTION AT REST != MEMORY AUTHORITY
KEY ID != KEY MATERIAL
DELETION RECEIPT != PROOF ALL COPIES ARE GONE
```

Encryption changes how bytes are persisted. It does not change record type, provenance, sensitivity, curation authority, routing policy, or epistemic state.

## Storage backend interface

`tools/storage.py` defines the `StorageBackend` protocol:

```text
put_bytes(logical_path, data)
get_bytes(logical_path)
delete(logical_path, reason)
list_objects()
validate()
```

The reference module includes:

- `FilesystemBackend`, a plaintext implementation of the logical contract;
- `EncryptedDirectoryBackend`, the Phase 7 reference encrypted backend.

Existing AI-CONTEXT canonical formats remain UTF-8 JSON/JSONL. A storage backend is a persistence boundary, not a replacement canonical format.

## Reference encrypted backend

The encrypted directory backend uses **AES-256-GCM** from the maintained Python `cryptography` package. AI-CONTEXT does not implement an encryption primitive.

Install the optional storage dependency:

```bash
python -m pip install -r requirements-storage.txt
```

Generate an external key file:

```bash
python3 tools/storage.py keygen ~/keys/ai-context.key
```

Initialize a store:

```bash
python3 tools/storage.py init ~/private/ai-context.secure \
  --key-file ~/keys/ai-context.key
```

Store and retrieve logical artifacts:

```bash
python3 tools/storage.py put \
  ~/private/ai-context.secure \
  memory/records.jsonl \
  ~/my-ai-context/memory/records.jsonl \
  --key-file ~/keys/ai-context.key

python3 tools/storage.py get \
  ~/private/ai-context.secure \
  memory/records.jsonl \
  /tmp/records.jsonl \
  --key-file ~/keys/ai-context.key
```

Validate and list:

```bash
python3 tools/storage.py validate ~/private/ai-context.secure \
  --key-file ~/keys/ai-context.key

python3 tools/storage.py list ~/private/ai-context.secure \
  --key-file ~/keys/ai-context.key
```

## Key boundary

A key file contains exactly one random 256-bit key encoded as hexadecimal.

The key file:

- must live **outside** the encrypted store;
- must not be placed in canonical memory, staging, curation data, bundles, repository fixtures, or storage metadata;
- is required to be owner-only (`0600`) on POSIX systems;
- is represented inside storage metadata only by a non-secret SHA-256-derived `key_id`.

The manifest contains:

```text
active_key_id
key_history[].key_id
key_history[].status
key_history[].activated_at
key_history[].retired_at
key_history[].replaced_by
```

It never contains raw key bytes, a wrapped key, a passphrase, a recovery key, or a secret-manager credential.

Python cannot guarantee reliable in-process memory zeroization of immutable key bytes. The reference backend therefore does **not** claim hardware-style secret isolation or secure-memory semantics.

## Object format

Logical paths are normalized POSIX-style relative paths. Absolute paths, `..`, backslash paths, and traversal forms fail closed.

Physical encrypted object filenames are derived from:

```text
SHA256(logical_path)
```

so plaintext logical paths do not appear as filenames.

Each object is an `AI-CONTEXT/ENCRYPTED-OBJECT` JSON envelope containing only:

```text
protocol/schema
algorithm
key_id
path_sha256
nonce
ciphertext
ciphertext_sha256
object id
```

The encrypted inner payload contains:

```text
logical_path
content bytes
content SHA-256
```

AES-GCM additional authenticated data binds the protocol version, algorithm, key ID, and path hash. A ciphertext moved to a different logical path or altered without the key fails authentication.

The design intentionally leaks some metadata at rest:

- store existence;
- algorithm and protocol version;
- non-secret key identifiers;
- object count and ciphertext sizes;
- equality of the same logical path digest across versions of the same store.

It does not claim metadata-hiding storage.

## Key rotation

Generate a replacement key outside the store:

```bash
python3 tools/storage.py keygen ~/keys/ai-context-2027.key
```

Rotate:

```bash
python3 tools/storage.py rotate-key ~/private/ai-context.secure \
  --old-key-file ~/keys/ai-context.key \
  --new-key-file ~/keys/ai-context-2027.key
```

Rotation uses a fail-closed `rotation.json` journal. While the journal exists, normal encrypted-store access is blocked. This prevents a partially rotated directory containing mixed active keys from being treated as healthy.

If a process is interrupted, resume with both external keys:

```bash
python3 tools/storage.py resume-rotation ~/private/ai-context.secure \
  --old-key-file ~/keys/ai-context.key \
  --new-key-file ~/keys/ai-context-2027.key
```

Each object envelope carries its actual `key_id`, so resume can recognize objects already re-encrypted before an interruption.

The manifest changes the active key only after every journalled object is readable under the new key. The previous key then becomes `retired` and records `replaced_by`.

## Deletion and cryptographic erasure receipts

Object deletion:

```bash
python3 tools/storage.py delete ~/private/ai-context.secure \
  memory/records.jsonl \
  --key-file ~/keys/ai-context-2027.key \
  --reason "user-requested removal"
```

emits an `AI-CONTEXT/STORAGE-DELETION` receipt before removing the primary ciphertext object.

The receipt contains ciphertext/object/path hashes but deliberately omits the plaintext logical path. Its claim is exactly:

```text
primary-store-ciphertext-removed-key-destruction-not-claimed
```

This is **not** proof of cryptographic erasure. The same ciphertext may survive in backups, snapshots, filesystem journals, cloud sync history, or forensic storage. The encryption key may also still exist.

### Key-destruction attestation

After key rotation, a retired key can be externally destroyed according to the user's key-management process. AI-CONTEXT can record that assertion:

```bash
python3 tools/storage.py attest-key-destruction ~/private/ai-context.secure \
  --key-id key.sha256:... \
  --actor local-user \
  --reason "all controlled copies removed"
```

The receipt is explicitly labelled:

```text
claim_strength = self-attested-external-action
```

AI-CONTEXT does not pretend it can prove that every backup, printed recovery code, hardware token, password-manager entry, or off-site copy has been destroyed.

Cryptographic erasure is strongest when all ciphertext that matters is encrypted only under the destroyed key and all usable copies of that key are actually eliminated.

## Recovery keys

Recovery material belongs outside AI-CONTEXT memory.

Recommended options include:

- an offline recovery medium stored separately from the encrypted store;
- an operating-system or hardware-backed secret store;
- a dedicated password/secret manager;
- an age/recipient key held separately from an exported encrypted archive;
- organization-managed key custody with documented recovery procedure.

Do **not** create a canonical memory record saying the recovery secret, key bytes, passphrase, seed phrase, or private key. A memory system that remembers its own decryption key has folded the vault door into the welcome mat.

AI-CONTEXT memory may safely record a non-secret operational pointer such as:

```text
key_id = key.sha256:...
recovery_location = "offline key custody procedure STORAGE-01"
```

provided the pointer itself contains no secret material.

## What this backend protects

With a strong external key and an uncompromised implementation, the reference backend protects stored payload contents against an attacker who obtains only the encrypted store directory.

It does not protect against:

- malware or another process that can read the live key and decrypted data;
- a compromised Python runtime;
- disclosure after the application decrypts data;
- plaintext files deliberately exported with `get`;
- operating-system swap/core dumps or application memory capture;
- metadata leakage described above;
- backups containing old ciphertext or keys;
- loss of availability when the key is lost;
- side channels or attacks outside the guarantees of the underlying cryptographic library.

## Schemas

Phase 7 adds Draft 2020-12 schemas for:

- `storage-manifest.schema.json`;
- `storage-object.schema.json`;
- `storage-rotation.schema.json`;
- `storage-deletion-receipt.schema.json`;
- `storage-key-event.schema.json`.

The schemas intentionally have no key-material fields.

## Relationship to Phase 8

Phase 7 defines secure persistence mechanics. Phase 8 will define portable restore/migration semantics.

An encrypted storage backend must not redefine restore authority. Decrypting bytes recreates stored artifacts; it does not recreate a previous model instance or provider-private state.
