use base64::{engine::general_purpose::STANDARD, Engine as _};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{env, fmt, fs, path::Path, process};

const VERSION: &str = "0.1.0";
const RECEIPT_PROTOCOL: &str = "AI-CONTEXT/SIGNED-BUNDLE-RECEIPT";
const CAPABILITY_PROTOCOL: &str = "AI-CONTEXT/CAPABILITY-MANIFEST";
const INTEROP_PROTOCOL: &str = "AI-CONTEXT/INTEROP-CONFORMANCE";
const DOMAIN: &str = "AI-CONTEXT/SIGNED-BUNDLE-RECEIPT";
const ALGORITHM: &str = "Ed25519";
const PREIMAGE_ID: &str = "ai-context-bundle-sha256-nul-v1";
const AUTHORITY: &str = "integrity-attestation-only";
const CANONICALIZER: &str = "python-json-v0.1";

#[derive(Debug)]
struct StrictValue(Value);

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct StrictVisitor;

        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = StrictValue;

            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("valid JSON without duplicate object members")
            }

            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Bool(value)))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(value.into())))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(value.into())))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                let number = serde_json::Number::from_f64(value)
                    .ok_or_else(|| E::custom("non-finite JSON number"))?;
                Ok(StrictValue(Value::Number(number)))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                Ok(StrictValue(Value::String(value.to_owned())))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value)))
            }

            fn visit_none<E>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
            where
                D: Deserializer<'de>,
            {
                StrictValue::deserialize(deserializer)
            }

            fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut values = Vec::new();
                while let Some(StrictValue(value)) = seq.next_element::<StrictValue>()? {
                    values.push(value);
                }
                Ok(StrictValue(Value::Array(values)))
            }

            fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut values = serde_json::Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(de::Error::custom(format!(
                            "duplicate JSON object member: {key}"
                        )));
                    }
                    let StrictValue(value) = map.next_value::<StrictValue>()?;
                    values.insert(key, value);
                }
                Ok(StrictValue(Value::Object(values)))
            }
        }

        deserializer.deserialize_any(StrictVisitor)
    }
}

fn parse_strict_json(bytes: &[u8], label: &str) -> Result<Value, String> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let StrictValue(value) = StrictValue::deserialize(&mut deserializer)
        .map_err(|e| format!("{label} is not strict JSON: {e}"))?;
    deserializer
        .end()
        .map_err(|e| format!("{label} has trailing JSON data: {e}"))?;
    Ok(value)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SignedReceipt {
    id: String,
    protocol: String,
    schema_version: String,
    signature_algorithm: String,
    signature_preimage: String,
    authority: String,
    bundle_sha256: String,
    bundle_payload_sha256: String,
    canonical_store_sha256: String,
    signer_key_id: String,
    public_key_b64: String,
    signature_b64: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CanonicalVector {
    name: String,
    input: Value,
    python_json_v0_1_utf8_hex: String,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
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

fn validate_bundle_header(bundle: &Value) -> Result<(), String> {
    if bundle.get("protocol").and_then(Value::as_str) != Some("AI-CONTEXT/BUNDLE") {
        return Err("bundle protocol is not AI-CONTEXT/BUNDLE".to_string());
    }
    if bundle.get("schema_version").and_then(Value::as_str) != Some(VERSION) {
        return Err("unsupported bundle schema version".to_string());
    }
    if bundle.get("canonicalizer").and_then(Value::as_str) != Some(CANONICALIZER) {
        return Err("unsupported bundle canonicalizer".to_string());
    }
    Ok(())
}

fn verify_receipt(bundle_path: &Path, receipt_path: &Path) -> Result<Value, String> {
    let bundle_bytes = fs::read(bundle_path).map_err(|e| format!("cannot read bundle: {e}"))?;
    let bundle = parse_strict_json(&bundle_bytes, "bundle")?;
    validate_bundle_header(&bundle)?;

    let receipt_bytes = fs::read(receipt_path).map_err(|e| format!("cannot read receipt: {e}"))?;
    let receipt_value = parse_strict_json(&receipt_bytes, "receipt")?;
    let receipt: SignedReceipt = serde_json::from_value(receipt_value)
        .map_err(|e| format!("receipt does not match signed-receipt contract: {e}"))?;
    if receipt.protocol != RECEIPT_PROTOCOL
        || receipt.schema_version != VERSION
        || receipt.signature_algorithm != ALGORITHM
        || receipt.signature_preimage != PREIMAGE_ID
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
        "receipt_id": receipt.id,
        "signer_key_id": receipt.signer_key_id,
        "bundle_sha256": receipt.bundle_sha256,
        "authority": AUTHORITY,
        "disclosure_authority_granted": false,
        "epistemic_authority_granted": false
    }))
}

fn validate_capability_value(value: &Value) -> Result<(), String> {
    if value.get("protocol").and_then(Value::as_str) != Some(CAPABILITY_PROTOCOL)
        || value.get("schema_version").and_then(Value::as_str) != Some(VERSION)
    {
        return Err("unsupported capability manifest protocol/schema".to_string());
    }

    let implementation = value
        .get("implementation")
        .and_then(Value::as_object)
        .ok_or_else(|| "capability manifest missing implementation".to_string())?;
    if implementation.get("protocol_version").and_then(Value::as_str) != Some(VERSION) {
        return Err("capability manifest implementation protocol version unsupported".to_string());
    }

    let canonicalization = value
        .get("canonicalization")
        .and_then(Value::as_object)
        .ok_or_else(|| "capability manifest missing canonicalization".to_string())?;
    if canonicalization.get("active").and_then(Value::as_str) != Some(CANONICALIZER)
        || canonicalization.get("rfc8785_jcs").and_then(Value::as_str)
            != Some("evaluated-not-adopted")
        || canonicalization
            .get("migration_required_for_change")
            .and_then(Value::as_bool)
            != Some(true)
    {
        return Err("capability manifest canonicalization contract invalid".to_string());
    }

    let signed = value
        .get("signed_bundle_receipts")
        .and_then(Value::as_object)
        .ok_or_else(|| "capability manifest missing signed_bundle_receipts".to_string())?;
    if signed.get("algorithm").and_then(Value::as_str) != Some(ALGORITHM)
        || signed.get("preimage").and_then(Value::as_str) != Some(PREIMAGE_ID)
        || signed.get("authority").and_then(Value::as_str) != Some(AUTHORITY)
        || signed.get("embedded_public_key").and_then(Value::as_bool) != Some(true)
        || signed
            .get("external_trust_anchor_optional")
            .and_then(Value::as_bool)
            != Some(true)
    {
        return Err("capability manifest signed-receipt contract invalid".to_string());
    }

    let rules = value
        .get("consumer_rules")
        .and_then(Value::as_object)
        .ok_or_else(|| "capability manifest missing consumer_rules".to_string())?;
    if rules.get("provider_memory_dependency").and_then(Value::as_str) != Some("none")
        || rules.get("index_results_require_routing").and_then(Value::as_bool) != Some(true)
        || rules.get("signature_grants_disclosure").and_then(Value::as_bool) != Some(false)
        || rules
            .get("signature_grants_epistemic_authority")
            .and_then(Value::as_bool)
            != Some(false)
    {
        return Err("capability manifest consumer authority rules invalid".to_string());
    }
    Ok(())
}

fn verify_capabilities(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| format!("cannot read capability manifest: {e}"))?;
    let value = parse_strict_json(&bytes, "capability manifest")?;
    validate_capability_value(&value)?;
    Ok(serde_json::json!({"status":"ok","protocol":CAPABILITY_PROTOCOL,"schema_version":VERSION}))
}

fn verify_fixture(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| format!("cannot read conformance fixture: {e}"))?;
    let fixture_value = parse_strict_json(&bytes, "conformance fixture")?;
    let fixture: Conformance = serde_json::from_value(fixture_value)
        .map_err(|e| format!("conformance fixture contract invalid: {e}"))?;
    if fixture.protocol != INTEROP_PROTOCOL
        || fixture.schema_version != VERSION
        || fixture.canonicalizer != CANONICALIZER
    {
        return Err("unsupported conformance fixture protocol/schema/canonicalizer".to_string());
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

#[cfg(test)]
mod tests {
    use super::*;

    fn capability_value() -> Value {
        serde_json::json!({
            "id": "capability.sha256:placeholder",
            "protocol": CAPABILITY_PROTOCOL,
            "schema_version": VERSION,
            "implementation": {"id":"test","language":"rust","protocol_version":VERSION},
            "canonicalization": {
                "active": CANONICALIZER,
                "rfc8785_jcs": "evaluated-not-adopted",
                "migration_required_for_change": true
            },
            "features": [],
            "signed_bundle_receipts": {
                "algorithm": ALGORITHM,
                "preimage": PREIMAGE_ID,
                "authority": AUTHORITY,
                "embedded_public_key": true,
                "external_trust_anchor_optional": true
            },
            "consumer_rules": {
                "provider_memory_dependency": "none",
                "index_results_require_routing": true,
                "signature_grants_disclosure": false,
                "signature_grants_epistemic_authority": false
            },
            "adapter_examples": {"mcp":"examples/mcp/","generic_tool":"examples/tool-adapter.json"}
        })
    }

    #[test]
    fn rejects_capability_that_disables_index_routing() {
        let mut value = capability_value();
        value["consumer_rules"]["index_results_require_routing"] = Value::Bool(false);
        assert!(validate_capability_value(&value).is_err());
    }

    #[test]
    fn rejects_bundle_with_unsupported_schema_version() {
        let value = serde_json::json!({
            "protocol":"AI-CONTEXT/BUNDLE",
            "schema_version":"9.0.0",
            "canonicalizer":CANONICALIZER
        });
        assert!(validate_bundle_header(&value).is_err());
    }

    #[test]
    fn rejects_duplicate_json_members() {
        assert!(parse_strict_json(br#"{"a":1,"a":2}"#, "test").is_err());
    }
}
