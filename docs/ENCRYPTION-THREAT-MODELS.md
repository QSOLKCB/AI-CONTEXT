# Encryption-at-rest threat-model comparison

AI-CONTEXT treats encryption-at-rest as a replaceable storage-backend concern. Different storage choices protect against different attackers and operational failures.

No option below changes canonical-memory authority, provenance, curation, or routing semantics.

## Comparison

| Approach | Strongest use case | Main protections | Important limitations |
| --- | --- | --- | --- |
| Filesystem / full-disk encryption | Protecting a powered-off or stolen device | Encrypts broad local storage transparently; low application complexity | Usually transparent after login/unlock; a compromised live session can read plaintext; backup/snapshot protection depends on how those copies are encrypted |
| `age` encrypted files/archives | Portable encrypted exports and offline copies | Mature recipient/passphrase model; simple file-level encryption; good separation between payload and recipient key | Whole-file workflow is less convenient for frequent random updates; key/identity custody remains external; plaintext exists while explicitly decrypted |
| Encrypted SQLite / SQLCipher-style store | Application-level structured persistence | Database-shaped transactions, indexes, and centralized encrypted state | Adds database/runtime dependency; WAL/journal/backup configuration matters; application process still sees plaintext after unlock; exact threat model depends on the selected maintained implementation |
| Hardware-backed key store | Protecting/using a master key without routinely exporting it | Can reduce exposure of raw long-term key bytes and bind use to platform/hardware policy | Platform-specific; recovery and portability become operational concerns; hardware protection does not stop an authorized compromised process from asking the device to decrypt data |
| AI-CONTEXT reference encrypted directory | Portable per-object encrypted persistence with explicit external-key boundary | AES-256-GCM authenticated encryption; path-digest filenames; resumable key rotation; deletion/key-event receipts | Raw key file is exportable; metadata is not fully hidden; Python cannot promise secure-memory zeroization; not a substitute for hardware-backed custody or whole-device encryption |

## Filesystem and full-disk encryption

Examples include OS-level encrypted volumes, encrypted home directories, and full-disk encryption.

Useful assumptions:

- attacker obtains a powered-off device or detached disk;
- user authentication and boot-chain protections remain trustworthy;
- storage remains encrypted before the user unlocks it.

It is not sufficient against an attacker who controls the live unlocked account or can read the application process. For a private AI memory store, filesystem encryption is excellent baseline protection but does not replace application-level disclosure controls or key separation.

## `age`

`age` is well suited to encrypted archives, backups, or portable export bundles where recipient identity is managed outside the payload.

Its strengths are simplicity and a clear encryption boundary. Its trade-off is that a complete file/archive is often the unit of encryption, so a frequently mutated object store may require repeated re-encryption or an additional container design.

AI-CONTEXT can use `age` as a future or external backend without changing canonical record semantics. The recipient private key or passphrase must remain outside AI-CONTEXT memory.

## Encrypted SQLite

An encrypted SQLite-compatible backend can be attractive when an implementation wants:

- transactions across many logical artifacts;
- indexed metadata;
- one database file instead of many encrypted objects;
- mature database backup/recovery tooling.

The threat model must explicitly account for temporary files, WAL/journal behavior, backups, key handoff, and the selected maintained encryption extension or library. Merely naming a file `.db` does not make SQLite encrypted.

The Phase 7 reference implementation does not ship a custom encrypted-SQLite construction.

## Hardware-backed storage

Hardware-backed key custody can include TPM-backed keys, OS keychains/keystores, secure elements, smart cards, or hardware security modules.

These systems can improve the key boundary because the long-term master key may be non-exportable or access-controlled by hardware/OS policy. They can also make portability and disaster recovery harder.

A hardware-backed backend should expose a non-secret key identifier or handle to AI-CONTEXT, not copy the hardware-protected secret into canonical memory.

## Layering is allowed

These approaches are not mutually exclusive. A strong deployment may use:

```text
full-disk encryption
    +
AI-CONTEXT encrypted object backend
    +
hardware-backed custody of the master key
    +
encrypted offline backups
```

Each layer addresses a different failure domain. Adding more layers does not justify weaker curation, routing, or provenance rules.

## Backup and deletion caution

Deletion semantics are hardest when backups exist.

Removing one primary ciphertext object does not remove:

- filesystem snapshots;
- cloud-sync history;
- backup archives;
- copied encrypted stores;
- exported plaintext;
- external copies of the key.

Key destruction can support cryptographic erasure only for ciphertext exclusively protected by that destroyed key, and only when all usable key copies are truly gone.

AI-CONTEXT receipts therefore distinguish:

```text
primary ciphertext removed
```

from:

```text
external key destruction self-attested
```

and never claim universal erasure from either event alone.
