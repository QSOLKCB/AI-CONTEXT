# AI-CONTEXT v1.0 Release Candidate

AI-CONTEXT architecture Phases 0–11 are complete. This document defines the release-candidate hardening boundary before the immutable `v1.0.0` tag.

## Release-candidate sequence

The release sequence is intentionally split into two stages.

### Stage A — tree freeze and audit

This pull request establishes:

1. the v1 public protocol/schema/invariant freeze declaration in `release/v1-freeze.json`;
2. the canonicalizer decision to retain `python-json-v0.1` for v1.0.0;
3. a tracked-tree release audit in `tools/release_audit.py`;
4. a CI gate that runs the complete Python/Rust/conformance suite and the release audit;
5. the v1.0.0 GitHub release notes.

### Stage B — immutable tag

After this release-candidate pull request is merged:

1. wait for the exact `main` commit CI run to succeed;
2. run the release audit on that exact `main` commit;
3. create `v1.0.0` pointing at that exact commit;
4. record the tag commit SHA as the immutable Phase 12 Lean formalization target.

The tag is deliberately not created from a pre-merge feature-branch SHA because a normal GitHub merge produces a different commit identity.

## Canonicalizer decision

For v1.0.0:

```text
active canonicalizer = python-json-v0.1
RFC 8785 JCS = evaluated-not-adopted
```

This preserves every existing deterministic identifier/hash contract established during Phases 0–11. Moving to JCS later requires an explicit versioned migration plus new language-neutral conformance fixtures.

See `docs/JCS-EVALUATION.md` and `release/v1-freeze.json`.

## Public-tree release audit

Run:

```bash
python3 tools/release_audit.py .
```

or preserve a machine-readable receipt outside the repository:

```bash
python3 tools/release_audit.py . --receipt /tmp/ai-context-release-audit.json
```

The audit starts from `git ls-files`, not from an unrestricted filesystem walk. Its claim is therefore about the exact **tracked public Git tree** intended for release.

It rejects tracked:

- top-level private workspace state such as `workspace/`, `vault/`, `staging/`, `memory/`, `curation/`, `routing/`, `receipts/`, and `indexes/`;
- `.aicr`, private-key, credential-store, private database, and private-suffix artifacts;
- raw/generated ZIPs outside the explicitly synthetic fixture tree;
- generated archives and local build/cache directories;
- tracked symlinks;
- common secret/token/private-key byte patterns.

Synthetic test/provider-drift fixtures remain allowed. Secret-shaped strings inside tests/fixtures are ignored only when the same line explicitly marks them as synthetic/example/dummy/fake/placeholder/redacted/test material.

## Audit claim

A successful receipt states:

```text
no-forbidden-private-or-runtime-artifacts-detected-in-tracked-tree
```

This is intentionally narrower than a universal deletion/privacy claim. It demonstrates that the release tree being tagged contains no detected private raw input, credentials, storage/signing keys, recovery material, user workspace state, or generated runtime artifacts under the declared audit policy.

It does **not** claim that:

- untracked files on a developer machine do not exist;
- old Git history never contained a removed value;
- remote backups or external copies do not exist;
- pattern scanning can mathematically prove the absence of every possible semantic private fact.

Those distinctions are part of the release receipt rather than hidden in prose.

## Freeze rule

Once `v1.0.0` is tagged, the tagged reference implementation and schemas become the immutable Phase 12 theorem target. Any later protocol/schema/canonicalizer/invariant change belongs to a new implementation version and, where applicable, an explicit migration contract.
