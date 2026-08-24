use base64::{engine::general_purpose::STANDARD, Engine as _};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{env, fs, path::Path, process};

const VERSION: &str = "0.1.0";
const RECEIPT_PROTOCOL: &str = "AI-CONTEXT/SIGNED-BUNDLE-RECEIPT";
const CAPABILITY_PROTOCOL: &str = "AI-CONTEXT/CAPABILITY-MANIFEST";
const INTEROP_PROTOCOL: &str = "AI-CONTEXT/INTEROP-CONFORMANCE";
const DOMAIN: &str = "AI-CONTEXT/SIGNED-BUNDLE-RECEIPT";
const ALGORITHM: &str = "Ed25519";
const AUTHORITY: &str = "integrity-attestation-only";

#[derive(Debug, Deserialize)]
struct SignedReceipt {
    protocol: String,
    schema_version: String,
    signature_algorithm: String,
    authority: String,
    bundle_sha256: String,
    bundle_payload_sha256: String,
    canonical_store_sha256: String,
    signer_key_id: String,
    public_key_b64: String,
    signature_b64: String,
}

#[derive(Debug, Deserialize)]
struct CanonicalVector {
    name: String,
    input: Value,
    python_json_v0_1_utf8_hex: String,
    sha256: String,
}

#[derive(Debug, Deserialize)]
struct PublicVector {
    bundle_sha256: String,
    bundle_payload_sha256: String,
    canonical_store_sha256: String,
    signer_key_id: String,
    public_key_b64: String,
    signature_b64: String,
    signature_preimage_hex: String,
    signature_preimage_sha256: String,
}

#[derive(Debug, Deserialize)]
struct Conformance {
    protocol: String,
    schema_version: String,
    canonicalizer: String,
    canonicalization_vectors: Vec<CanonicalVector>,
    signed_receipt_public_vector: PublicVector,
}

fn sha256_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

fn signature_preimage(
    bundle_sha256: &str,
    bundle_payload_sha256: &str,
    canonical_store_sha256: &str,
    signer_key_id: &str,
) -> Vec<u8> {
    format!(
        "{DOMAIN}\0{VERSION}\0{ALGORITHM}\0{bundle_sha256}\0{bundle_payload_sha256}\0{canonical_store_sha256}\0{signer_key_id}\0"
    )
    .into_bytes()
}

fn verify_signature(
    public_key_b64: &str,
    signature_b64: &str,
    preimage: &[u8],
    signer_key_id: &str,
) -> Result<(), String> {
    let public_raw = STANDARD
        .decode(public_key_b64)
        .map_err(|e| format!("invalid public-key base64: {e}"))?;
    let signature_raw = STANDARD
        .decode(signature_b64)
        .map_err(|e| format!("invalid signature base64: {e}"))?;
    let public_array: [u8; 32] = public_raw
        .as_slice()
        .try_into()
        .map_err(|_| "public key must be 32 bytes".to_string())?;
    let expected_key_id = format!("ed25519.sha256:{}", sha256_hex(&public_raw));
    if expected_key_id != signer_key_id {
        return Err("signer key id does not match public key".to_string());
    }
    let key = VerifyingKey::from_bytes(&public_array)
        .map_err(|e| format!("invalid Ed25519 public key: {e}"))?;
    let signature = Signature::from_slice(&signature_raw)
        .map_err(|e| format!("invalid Ed25519 signature: {e}"))?;
    key.verify(preimage, &signature)
        .map_err(|e| format!("signature verification failed: {e}"))
}

fn verify_receipt(bundle_path: &Path, receipt_path: &Path) -> Result<Value, String> {
    let bundle_bytes = fs::read(bundle_path).map_err(|e| format!("cannot read bundle: {e}"))?;
    let bundle: Value = serde_json::from_slice(&bundle_bytes)
        .map_err(|e| format!("bundle is not JSON: {e}"))?;
    if bundle.get("protocol").and_then(Value::as_str) != Some("AI-CONTEXT/BUNDLE") {
        return Err("bundle protocol is not AI-CONTEXT/BUNDLE".to_string());
    }
    let receipt_bytes = fs::read(receipt_path).map_err(|e| format!("cannot read receipt: {e}"))?;
    let receipt: SignedReceipt = serde_json::from_slice(&receipt_bytes)
        .map_err(|e| format!("receipt is not valid JSON: {e}"))?;
    if receipt.protocol != RECEIPT_PROTOCOL
        || receipt.schema_version != VERSION
        || receipt.signature_algorithm != ALGORITHM
        || receipt.authority != AUTHORITY
    {
        return Err("unsupported or authority-invalid signed receipt".to_string());
    }
    let bundle_hash = sha256_hex(&bundle_bytes);
    if bundle_hash != receipt.bundle_sha256 {
        return Err("bundle byte hash does not match signed receipt".to_string());
    }
    if bundle
        .get("canonical_payload_sha256")
        .and_then(Value::as_str)
        != Some(receipt.bundle_payload_sha256.as_str())
    {
        return Err("bundle payload hash field does not match signed receipt".to_string());
    }
    if bundle
        .get("canonical_store_sha256")
        .and_then(Value::as_str)
        != Some(receipt.canonical_store_sha256.as_str())
    {
        return Err("canonical store hash field does not match signed receipt".to_string());
    }
    let preimage = signature_preimage(
        &receipt.bundle_sha256,
        &receipt.bundle_payload_sha256,
        &receipt.canonical_store_sha256,
        &receipt.signer_key_id,
    );
    verify_signature(
        &receipt.public_key_b64,
        &receipt.signature_b64,
        &preimage,
        &receipt.signer_key_id,
    )?;
    Ok(serde_json::json!({
        "status": "ok",
        "cryptographically_valid": true,
        "signer_key_id": receipt.signer_key_id,
        "bundle_sha256": receipt.bundle_sha256,
        "authority": AUTHORITY,
        "disclosure_authority_granted": false,
        "epistemic_authority_granted": false
    }))
}

fn verify_capabilities(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| format!("cannot read capability manifest: {e}"))?;
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|e| format!("capability manifest is not valid JSON: {e}"))?;
    if value.get("protocol").and_then(Value::as_str) != Some(CAPABILITY_PROTOCOL)
        || value.get("schema_version").and_then(Value::as_str) != Some(VERSION)
    {
        return Err("unsupported capability manifest protocol/schema".to_string());
    }
    let rules = value
        .get("consumer_rules")
        .and_then(Value::as_object)
        .ok_or_else(|| "capability manifest missing consumer_rules".to_string())?;
    if rules.get("signature_grants_disclosure").and_then(Value::as_bool) != Some(false)
        || rules
            .get("signature_grants_epistemic_authority")
            .and_then(Value::as_bool)
            != Some(false)
    {
        return Err("capability manifest grants forbidden signature authority".to_string());
    }
    Ok(serde_json::json!({"status":"ok","protocol":CAPABILITY_PROTOCOL,"schema_version":VERSION}))
}

fn verify_fixture(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| format!("cannot read conformance fixture: {e}"))?;
    let fixture: Conformance = serde_json::from_slice(&bytes)
        .map_err(|e| format!("conformance fixture is not valid JSON: {e}"))?;
    if fixture.protocol != INTEROP_PROTOCOL
        || fixture.schema_version != VERSION
        || fixture.canonicalizer != "python-json-v0.1"
    {
        return Err("unsupported conformance fixture protocol/schema".to_string());
    }
    for vector in &fixture.canonicalization_vectors {
        let rendered = serde_json::to_vec(&vector.input)
            .map_err(|e| format!("cannot render vector {}: {e}", vector.name))?;
        if hex::encode(&rendered) != vector.python_json_v0_1_utf8_hex {
            return Err(format!(
                "Rust JSON bytes differ from declared v0.1 vector: {}",
                vector.name
            ));
        }
        if sha256_hex(&rendered) != vector.sha256 {
            return Err(format!("canonicalization vector hash mismatch: {}", vector.name));
        }
    }
    let signed = &fixture.signed_receipt_public_vector;
    let preimage = signature_preimage(
        &signed.bundle_sha256,
        &signed.bundle_payload_sha256,
        &signed.canonical_store_sha256,
        &signed.signer_key_id,
    );
    if hex::encode(&preimage) != signed.signature_preimage_hex {
        return Err("signed public vector preimage mismatch".to_string());
    }
    if sha256_hex(&preimage) != signed.signature_preimage_sha256 {
        return Err("signed public vector preimage hash mismatch".to_string());
    }
    verify_signature(
        &signed.public_key_b64,
        &signed.signature_b64,
        &preimage,
        &signed.signer_key_id,
    )?;
    Ok(serde_json::json!({
        "status":"ok",
        "canonicalization_vectors": fixture.canonicalization_vectors.len(),
        "signed_receipt_public_vector":"verified"
    }))
}

fn usage() -> ! {
    eprintln!("usage:\n  ai-context-interop capabilities <manifest.json>\n  ai-context-interop verify-receipt <bundle.json> <receipt.json>\n  ai-context-interop validate-fixtures <conformance.json>");
    process::exit(2);
}

fn run() -> Result<Value, String> {
    let args: Vec<String> = env::args().collect();
    match args.as_slice() {
        [_, command, path] if command == "capabilities" => verify_capabilities(Path::new(path)),
        [_, command, path] if command == "validate-fixtures" => verify_fixture(Path::new(path)),
        [_, command, bundle, receipt] if command == "verify-receipt" => {
            verify_receipt(Path::new(bundle), Path::new(receipt))
        }
        _ => usage(),
    }
}

fn main() {
    match run() {
        Ok(value) => println!("{}", serde_json::to_string_pretty(&value).unwrap()),
        Err(error) => {
            eprintln!("error: {error}");
            process::exit(2);
        }
    }
}
