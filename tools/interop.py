#!/usr/bin/env python3
"""Phase 10 interoperability surface for AI-CONTEXT.

This module stabilizes a small, explicit Python reference API for language-neutral
consumers. Signed bundle receipts use Ed25519 from the maintained ``cryptography``
package, but signature validity is an integrity statement only and never disclosure
or epistemic authority.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import ai_context_legacy as core

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover
    InvalidSignature = Exception  # type: ignore[assignment]
    serialization = None  # type: ignore[assignment]
    Ed25519PrivateKey = None  # type: ignore[assignment]
    Ed25519PublicKey = None  # type: ignore[assignment]

INTEROP_VERSION = "0.1.0"
CAPABILITY_PROTOCOL = "AI-CONTEXT/CAPABILITY-MANIFEST"
SIGNED_RECEIPT_PROTOCOL = "AI-CONTEXT/SIGNED-BUNDLE-RECEIPT"
SIGNATURE_ALGORITHM = "Ed25519"
SIGNATURE_PREIMAGE = "ai-context-bundle-sha256-nul-v1"
SIGNATURE_DOMAIN = "AI-CONTEXT/SIGNED-BUNDLE-RECEIPT"
SIGNATURE_AUTHORITY = "integrity-attestation-only"
KEY_ID_PREFIX = "ed25519.sha256:"
PRIVATE_KEY_BYTES = 32
PUBLIC_KEY_BYTES = 32


class InteropError(core.ContextError):
    pass


def canonical_bytes(value: Any) -> bytes:
    """Stable v0.1 Python reference canonicalizer."""
    return core.canonical_bytes(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, value: dict[str, Any]) -> str:
    return f"{prefix}.sha256:{sha256_bytes(canonical_bytes(value))}"


def _require_crypto() -> None:
    if Ed25519PrivateKey is None or Ed25519PublicKey is None or serialization is None:
        raise InteropError(
            "signed bundle receipts require the maintained 'cryptography' package; "
            "install requirements-signing.txt"
        )


def _json_loads_no_duplicates(text: str, *, label: str) -> Any:
    """Parse strict JSON while rejecting duplicate object member names at every depth."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InteropError(f"duplicate JSON object member {key!r} in {label}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            parse_constant=core.reject_nonfinite_constant,
            object_pairs_hook=object_pairs,
        )
    except InteropError:
        raise
    except core.ContextError as exc:
        raise InteropError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise InteropError(f"cannot parse {label}: {exc}") from exc


def _strict_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InteropError(f"cannot read JSON file {path}: {exc}") from exc
    try:
        value = _json_loads_no_duplicates(data.decode("utf-8"), label=str(path))
    except UnicodeDecodeError as exc:
        raise InteropError(f"JSON file must be UTF-8: {path}") from exc
    if not isinstance(value, dict):
        raise InteropError(f"JSON file must contain an object: {path}")
    return data, value


def _validate_bundle_bytes(data: bytes, bundle: dict[str, Any]) -> dict[str, str]:
    if bundle.get("protocol") != "AI-CONTEXT/BUNDLE":
        raise InteropError("signed receipt requires an AI-CONTEXT/BUNDLE")
    if bundle.get("schema_version") != core.PROTOCOL_VERSION:
        raise InteropError("unsupported bundle schema version")
    if bundle.get("canonicalizer") != "python-json-v0.1":
        raise InteropError("unsupported bundle canonicalizer")
    payload_hash = bundle.get("canonical_payload_sha256")
    store_hash = bundle.get("canonical_store_sha256")
    if not isinstance(payload_hash, str) or len(payload_hash) != 64:
        raise InteropError("bundle missing canonical_payload_sha256")
    if not isinstance(store_hash, str) or len(store_hash) != 64:
        raise InteropError("bundle missing canonical_store_sha256")
    payload = {key: value for key, value in bundle.items() if key != "canonical_payload_sha256"}
    if sha256_bytes(canonical_bytes(payload)) != payload_hash:
        raise InteropError("bundle canonical_payload_sha256 mismatch")
    return {
        "bundle_sha256": sha256_bytes(data),
        "bundle_payload_sha256": payload_hash,
        "canonical_store_sha256": store_hash,
    }


def capability_manifest() -> dict[str, Any]:
    core_value = {
        "protocol": CAPABILITY_PROTOCOL,
        "schema_version": INTEROP_VERSION,
        "implementation": {
            "id": "ai-context-python-reference",
            "language": "python",
            "protocol_version": core.PROTOCOL_VERSION,
        },
        "canonicalization": {
            "active": "python-json-v0.1",
            "rfc8785_jcs": "evaluated-not-adopted",
            "migration_required_for_change": True,
        },
        "features": [
            "source-import",
            "source-evidence",
            "human-policy-curation",
            "selective-routing",
            "encrypted-storage",
            "portable-restore",
            "derived-indexes",
            "signed-bundle-receipts",
        ],
        "signed_bundle_receipts": {
            "algorithm": SIGNATURE_ALGORITHM,
            "preimage": SIGNATURE_PREIMAGE,
            "authority": SIGNATURE_AUTHORITY,
            "embedded_public_key": True,
            "external_trust_anchor_optional": True,
        },
        "consumer_rules": {
            "provider_memory_dependency": "none",
            "index_results_require_routing": True,
            "signature_grants_disclosure": False,
            "signature_grants_epistemic_authority": False,
        },
        "adapter_examples": {
            "mcp": "examples/mcp/",
            "generic_tool": "examples/tool-adapter.json",
        },
    }
    return {"id": stable_id("capability", core_value), **core_value}


def validate_capability_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InteropError("capability manifest must be an object")
    required = {
        "id", "protocol", "schema_version", "implementation", "canonicalization",
        "features", "signed_bundle_receipts", "consumer_rules", "adapter_examples",
    }
    if set(value) != required:
        raise InteropError("capability manifest has missing/unknown fields")
    if value.get("protocol") != CAPABILITY_PROTOCOL or value.get("schema_version") != INTEROP_VERSION:
        raise InteropError("unsupported capability manifest protocol/schema")

    implementation = value.get("implementation")
    if not isinstance(implementation, dict) or set(implementation) != {"id", "language", "protocol_version"}:
        raise InteropError("capability manifest implementation fields invalid")
    if implementation.get("protocol_version") != core.PROTOCOL_VERSION:
        raise InteropError("capability manifest implementation protocol version unsupported")

    canonicalization = value.get("canonicalization")
    if not isinstance(canonicalization, dict) or set(canonicalization) != {
        "active", "rfc8785_jcs", "migration_required_for_change"
    }:
        raise InteropError("capability manifest canonicalization fields invalid")
    if canonicalization.get("active") != "python-json-v0.1":
        raise InteropError("capability manifest active canonicalizer unsupported")
    if canonicalization.get("rfc8785_jcs") != "evaluated-not-adopted":
        raise InteropError("capability manifest JCS status invalid")
    if canonicalization.get("migration_required_for_change") is not True:
        raise InteropError("capability manifest must require migration for canonicalizer change")

    signed = value.get("signed_bundle_receipts")
    if not isinstance(signed, dict) or set(signed) != {
        "algorithm", "preimage", "authority", "embedded_public_key", "external_trust_anchor_optional"
    }:
        raise InteropError("capability manifest signed-receipt fields invalid")
    if signed.get("algorithm") != SIGNATURE_ALGORITHM or signed.get("preimage") != SIGNATURE_PREIMAGE:
        raise InteropError("capability manifest signed-receipt algorithm/preimage unsupported")
    if signed.get("authority") != SIGNATURE_AUTHORITY:
        raise InteropError("capability manifest signed-receipt authority invalid")
    if signed.get("embedded_public_key") is not True or signed.get("external_trust_anchor_optional") is not True:
        raise InteropError("capability manifest signed-receipt key semantics invalid")

    rules = value.get("consumer_rules")
    if not isinstance(rules, dict) or set(rules) != {
        "provider_memory_dependency", "index_results_require_routing",
        "signature_grants_disclosure", "signature_grants_epistemic_authority",
    }:
        raise InteropError("capability manifest consumer_rules fields invalid")
    if rules.get("provider_memory_dependency") != "none":
        raise InteropError("capability manifest may not depend on provider-side memory")
    if rules.get("index_results_require_routing") is not True:
        raise InteropError("capability manifest must route derived-index results before disclosure")
    if rules.get("signature_grants_disclosure") is not False:
        raise InteropError("capability manifest may not grant disclosure through signatures")
    if rules.get("signature_grants_epistemic_authority") is not False:
        raise InteropError("capability manifest may not grant epistemic authority through signatures")

    core_value = {key: item for key, item in value.items() if key != "id"}
    if value.get("id") != stable_id("capability", core_value):
        raise InteropError("capability manifest id/hash mismatch")
    return value


def _load_private_key(path: Path) -> tuple[Any, bytes, str]:
    _require_crypto()
    resolved = path.expanduser().resolve()
    try:
        text = resolved.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise InteropError(f"cannot read signing private key: {exc}") from exc
    if len(text) != PRIVATE_KEY_BYTES * 2:
        raise InteropError("signing private key must contain exactly 64 hexadecimal characters")
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise InteropError("signing private key is not valid hexadecimal") from exc
    if os.name != "nt":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise InteropError("signing private key permissions are too broad; require 0600")
    private = Ed25519PrivateKey.from_private_bytes(raw)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, public, KEY_ID_PREFIX + sha256_bytes(public)


def _load_public_key(path: Path) -> tuple[Any, bytes, str]:
    _require_crypto()
    resolved = path.expanduser().resolve()
    try:
        text = resolved.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise InteropError(f"cannot read signing public key: {exc}") from exc
    if len(text) != PUBLIC_KEY_BYTES * 2:
        raise InteropError("signing public key must contain exactly 64 hexadecimal characters")
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise InteropError("signing public key is not valid hexadecimal") from exc
    public = Ed25519PublicKey.from_public_bytes(raw)
    return public, raw, KEY_ID_PREFIX + sha256_bytes(raw)


def generate_signing_key(private_path: Path, public_path: Path) -> dict[str, str]:
    _require_crypto()
    private_path = private_path.expanduser().resolve()
    public_path = public_path.expanduser().resolve()
    if private_path == public_path:
        raise InteropError("private and public signing key paths must differ")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    private_fd: int | None = None
    public_fd: int | None = None
    private_created = False
    public_created = False
    try:
        private_fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        private_created = True
        with os.fdopen(private_fd, "wb") as handle:
            private_fd = None
            handle.write(private_raw.hex().encode("ascii") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        public_fd = os.open(public_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        public_created = True
        with os.fdopen(public_fd, "wb") as handle:
            public_fd = None
            handle.write(public_raw.hex().encode("ascii") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        if private_fd is not None:
            os.close(private_fd)
        if public_fd is not None:
            os.close(public_fd)
        if public_created:
            try:
                public_path.unlink()
            except FileNotFoundError:
                pass
        if private_created:
            try:
                private_path.unlink()
            except FileNotFoundError:
                pass
        if isinstance(exc, FileExistsError):
            raise InteropError("refusing to overwrite existing signing key file") from exc
        raise
    key_id = KEY_ID_PREFIX + sha256_bytes(public_raw)
    return {
        "key_id": key_id,
        "private_key_file": str(private_path),
        "public_key_file": str(public_path),
    }


def signature_preimage(
    *,
    bundle_sha256: str,
    bundle_payload_sha256: str,
    canonical_store_sha256: str,
    signer_key_id: str,
) -> bytes:
    for label, value in {
        "bundle_sha256": bundle_sha256,
        "bundle_payload_sha256": bundle_payload_sha256,
        "canonical_store_sha256": canonical_store_sha256,
    }.items():
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise InteropError(f"{label} must be a lowercase SHA-256 hex digest")
    if not isinstance(signer_key_id, str) or not signer_key_id.startswith(KEY_ID_PREFIX):
        raise InteropError("invalid signer_key_id")
    fields = [
        SIGNATURE_DOMAIN,
        INTEROP_VERSION,
        SIGNATURE_ALGORITHM,
        bundle_sha256,
        bundle_payload_sha256,
        canonical_store_sha256,
        signer_key_id,
    ]
    return ("\0".join(fields) + "\0").encode("ascii")


def sign_bundle(bundle_path: Path, private_key_path: Path) -> dict[str, Any]:
    data, bundle = _strict_json_bytes(bundle_path.expanduser().resolve())
    identity = _validate_bundle_bytes(data, bundle)
    private, public_raw, key_id = _load_private_key(private_key_path)
    preimage = signature_preimage(**identity, signer_key_id=key_id)
    signature = private.sign(preimage)
    core_value = {
        "protocol": SIGNED_RECEIPT_PROTOCOL,
        "schema_version": INTEROP_VERSION,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_preimage": SIGNATURE_PREIMAGE,
        "authority": SIGNATURE_AUTHORITY,
        **identity,
        "signer_key_id": key_id,
        "public_key_b64": base64.b64encode(public_raw).decode("ascii"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }
    return {"id": stable_id("signed-bundle-receipt", core_value), **core_value}


def validate_signed_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InteropError("signed bundle receipt must be an object")
    required = {
        "id", "protocol", "schema_version", "signature_algorithm", "signature_preimage",
        "authority", "bundle_sha256", "bundle_payload_sha256", "canonical_store_sha256",
        "signer_key_id", "public_key_b64", "signature_b64",
    }
    if set(value) != required:
        raise InteropError("signed bundle receipt has missing/unknown fields")
    if value.get("protocol") != SIGNED_RECEIPT_PROTOCOL or value.get("schema_version") != INTEROP_VERSION:
        raise InteropError("unsupported signed bundle receipt protocol/schema")
    if value.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        raise InteropError("unsupported signed bundle signature algorithm")
    if value.get("signature_preimage") != SIGNATURE_PREIMAGE:
        raise InteropError("unsupported signed bundle preimage")
    if value.get("authority") != SIGNATURE_AUTHORITY:
        raise InteropError("signed bundle receipt overstates authority")
    try:
        public_raw = base64.b64decode(value["public_key_b64"], validate=True)
        signature = base64.b64decode(value["signature_b64"], validate=True)
    except Exception as exc:
        raise InteropError("invalid signed bundle receipt base64") from exc
    if len(public_raw) != PUBLIC_KEY_BYTES or len(signature) != 64:
        raise InteropError("invalid signed bundle public key/signature length")
    expected_key_id = KEY_ID_PREFIX + sha256_bytes(public_raw)
    if value.get("signer_key_id") != expected_key_id:
        raise InteropError("signed bundle signer key id mismatch")
    signature_preimage(
        bundle_sha256=value.get("bundle_sha256"),
        bundle_payload_sha256=value.get("bundle_payload_sha256"),
        canonical_store_sha256=value.get("canonical_store_sha256"),
        signer_key_id=value.get("signer_key_id"),
    )
    core_value = {key: item for key, item in value.items() if key != "id"}
    if value.get("id") != stable_id("signed-bundle-receipt", core_value):
        raise InteropError("signed bundle receipt id/hash mismatch")
    return value


def verify_signed_receipt(
    bundle_path: Path,
    receipt_path: Path,
    *,
    public_key_path: Path | None = None,
) -> dict[str, Any]:
    _require_crypto()
    bundle_bytes, bundle = _strict_json_bytes(bundle_path.expanduser().resolve())
    identity = _validate_bundle_bytes(bundle_bytes, bundle)
    _receipt_bytes, receipt = _strict_json_bytes(receipt_path.expanduser().resolve())
    receipt = validate_signed_receipt(receipt)
    for field in ("bundle_sha256", "bundle_payload_sha256", "canonical_store_sha256"):
        if receipt[field] != identity[field]:
            raise InteropError(f"signed receipt {field} does not match bundle")
    embedded_raw = base64.b64decode(receipt["public_key_b64"])
    external_trust_anchor = False
    if public_key_path is not None:
        _public, external_raw, external_id = _load_public_key(public_key_path)
        if external_id != receipt["signer_key_id"] or external_raw != embedded_raw:
            raise InteropError("external public key does not match signed receipt signer")
        external_trust_anchor = True
    public = Ed25519PublicKey.from_public_bytes(embedded_raw)
    preimage = signature_preimage(
        bundle_sha256=receipt["bundle_sha256"],
        bundle_payload_sha256=receipt["bundle_payload_sha256"],
        canonical_store_sha256=receipt["canonical_store_sha256"],
        signer_key_id=receipt["signer_key_id"],
    )
    try:
        public.verify(base64.b64decode(receipt["signature_b64"]), preimage)
    except InvalidSignature as exc:
        raise InteropError("signed bundle receipt signature verification failed") from exc
    return {
        "status": "ok",
        "cryptographically_valid": True,
        "receipt_id": receipt["id"],
        "signer_key_id": receipt["signer_key_id"],
        "bundle_sha256": receipt["bundle_sha256"],
        "authority": SIGNATURE_AUTHORITY,
        "external_trust_anchor": external_trust_anchor,
        "disclosure_authority_granted": False,
        "epistemic_authority_granted": False,
    }


def validate_conformance_vectors(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InteropError("conformance vector file must be an object")
    if value.get("protocol") != "AI-CONTEXT/INTEROP-CONFORMANCE" or value.get("schema_version") != INTEROP_VERSION:
        raise InteropError("unsupported interoperability conformance protocol/schema")
    if value.get("canonicalizer") != "python-json-v0.1":
        raise InteropError("unsupported interoperability conformance canonicalizer")
    vectors = value.get("canonicalization_vectors")
    if not isinstance(vectors, list) or not vectors:
        raise InteropError("canonicalization_vectors must be a non-empty array")
    for vector in vectors:
        if not isinstance(vector, dict):
            raise InteropError("canonicalization vector must be an object")
        rendered = canonical_bytes(vector.get("input"))
        if rendered.hex() != vector.get("python_json_v0_1_utf8_hex"):
            raise InteropError(f"canonicalization vector byte mismatch: {vector.get('name')}")
        if sha256_bytes(rendered) != vector.get("sha256"):
            raise InteropError(f"canonicalization vector hash mismatch: {vector.get('name')}")
    signed = value.get("signed_receipt_public_vector")
    if not isinstance(signed, dict):
        raise InteropError("signed_receipt_public_vector must be an object")
    preimage = signature_preimage(
        bundle_sha256=signed.get("bundle_sha256"),
        bundle_payload_sha256=signed.get("bundle_payload_sha256"),
        canonical_store_sha256=signed.get("canonical_store_sha256"),
        signer_key_id=signed.get("signer_key_id"),
    )
    if preimage.hex() != signed.get("signature_preimage_hex"):
        raise InteropError("signed receipt public vector preimage mismatch")
    if sha256_bytes(preimage) != signed.get("signature_preimage_sha256"):
        raise InteropError("signed receipt public vector preimage hash mismatch")
    _require_crypto()
    public_raw = base64.b64decode(signed["public_key_b64"], validate=True)
    signature = base64.b64decode(signed["signature_b64"], validate=True)
    if KEY_ID_PREFIX + sha256_bytes(public_raw) != signed["signer_key_id"]:
        raise InteropError("signed receipt public vector key id mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, preimage)
    except InvalidSignature as exc:
        raise InteropError("signed receipt public vector signature invalid") from exc
    return {
        "status": "ok",
        "canonicalization_vectors": len(vectors),
        "signed_receipt_public_vector": "verified",
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise InteropError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 10 interoperability")
    sub = parser.add_subparsers(dest="command", required=True)

    caps = sub.add_parser("capabilities", help="emit the stable Python capability manifest")
    caps.add_argument("--output")

    keygen = sub.add_parser("sign-keygen", help="generate external Ed25519 signing keys")
    keygen.add_argument("private_key")
    keygen.add_argument("--public-key", required=True)

    sign = sub.add_parser("sign-bundle", help="sign exact AI-CONTEXT bundle bytes")
    sign.add_argument("bundle")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--receipt", required=True)

    verify = sub.add_parser("verify-receipt", help="verify signed receipt and exact bundle bytes")
    verify.add_argument("bundle")
    verify.add_argument("receipt")
    verify.add_argument("--public-key")

    fixture = sub.add_parser("validate-fixtures", help="verify language-neutral conformance vectors")
    fixture.add_argument("fixture")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "capabilities":
            result = capability_manifest()
            validate_capability_manifest(result)
            if args.output:
                _write_json(Path(args.output), result)
        elif args.command == "sign-keygen":
            result = generate_signing_key(Path(args.private_key), Path(args.public_key))
        elif args.command == "sign-bundle":
            receipt = sign_bundle(Path(args.bundle), Path(args.private_key))
            validate_signed_receipt(receipt)
            _write_json(Path(args.receipt), receipt)
            result = {
                "status": "ok",
                "receipt": str(Path(args.receipt).expanduser().resolve()),
                "receipt_id": receipt["id"],
                "signer_key_id": receipt["signer_key_id"],
                "authority": receipt["authority"],
            }
        elif args.command == "verify-receipt":
            result = verify_signed_receipt(
                Path(args.bundle),
                Path(args.receipt),
                public_key_path=Path(args.public_key) if args.public_key else None,
            )
        elif args.command == "validate-fixtures":
            _data, value = _strict_json_bytes(Path(args.fixture).expanduser().resolve())
            result = validate_conformance_vectors(value)
        else:
            raise InteropError(f"unsupported interoperability command: {args.command}")
    except (InteropError, core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
