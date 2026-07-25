"""Envelope encryption for sensitive datasource fields using KMS direct encrypt/decrypt.

Each sensitive field value is encrypted with a KMS Encrypt call and stored as
a base64-encoded ciphertext with a marker. Decryption calls KMS Decrypt.
No native crypto dependencies required — uses KMS for all cryptographic operations.

Stored format: {"__encrypted": true, "ciphertext": <base64 KMS ciphertext>}
"""

import base64
import os

import boto3

# Fields that must be encrypted in datasource configs — only user-provided long-lived secrets
SENSITIVE_FIELDS = frozenset({
    "pat", "clientSecret", "apiToken", "privateKey", "apiKey",
})

_kms = boto3.client("kms")
_KMS_KEY_ID = os.environ.get("DATASOURCE_ENCRYPTION_KEY_ID", "")


def _get_key_id():
    if not _KMS_KEY_ID:
        raise RuntimeError("DATASOURCE_ENCRYPTION_KEY_ID not configured")
    return _KMS_KEY_ID


def encrypt_value(plaintext: str) -> dict:
    """Encrypt a string value using KMS. Returns a marker dict."""
    if not plaintext:
        return plaintext
    resp = _kms.encrypt(
        KeyId=_get_key_id(),
        Plaintext=plaintext.encode("utf-8"),
    )
    return {
        "__encrypted": True,
        "ciphertext": base64.b64encode(resp["CiphertextBlob"]).decode(),
    }


def decrypt_value(envelope) -> str:
    """Decrypt a KMS-encrypted value. Returns the plaintext string."""
    if not isinstance(envelope, dict) or not envelope.get("__encrypted"):
        return envelope
    ciphertext = base64.b64decode(envelope["ciphertext"])
    resp = _kms.decrypt(CiphertextBlob=ciphertext)
    return resp["Plaintext"].decode("utf-8")


def encrypt_config(config: dict) -> dict:
    """Encrypt sensitive fields in a datasource config dict."""
    if not isinstance(config, dict):
        return config
    result = {}
    for k, v in config.items():
        if k in SENSITIVE_FIELDS and isinstance(v, str) and v:
            result[k] = encrypt_value(v)
        else:
            result[k] = v
    return result


def decrypt_config(config: dict) -> dict:
    """Decrypt sensitive fields in a datasource config dict. Backwards-compatible with plaintext values."""
    if not isinstance(config, dict):
        return config
    result = {}
    for k, v in config.items():
        if k in SENSITIVE_FIELDS and isinstance(v, dict) and v.get("__encrypted"):
            result[k] = decrypt_value(v)
        else:
            result[k] = v
    return result
