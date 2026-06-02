"""Domain: encryption of sensitive data at rest.

A platform engineer must tokenize PII before it lands in analytics storage. The
spec names a concrete, non-negotiable algorithm (AES-256-GCM) -- a hard
constraint a domain expert would never leave to chance -- and a concrete output
encoding. The function must be reversible by anyone holding the key.
"""
from __future__ import annotations

import os
import secrets

from semipy import configure, semiformal

configure(verbose=True, cache_dir=os.environ.get("DT_CACHE", "examples/.dt_cache"))


@semiformal
def encrypt_pii(record: dict, key: bytes, fields: list[str]) -> dict:
    out = None
    #< intent: Encrypt selected record fields with AES-256-GCM
    #< given: record may be None, treated as empty
    #< given: fields may be None, treated as no fields
    #< given: values coerced via bytes, None-to-empty, or UTF-8 string
    #< by: copying record, generating per-field 12-byte random nonces, AESGCM encrypting
    #< unless: missing or non-32-byte key raises ValueError
    #> return a shallow copy of {record} in which every field named in {fields} is
    #> encrypted with AES-256-GCM under {key}; replace each value with a base64 string
    #> encoding nonce(12 bytes) + ciphertext + tag; leave all other fields unchanged
    #< yields: object containing out shallow copy with selected fields replaced
    return out


if __name__ == "__main__":
    key = secrets.token_bytes(32)
    record = {
        "user_id": 88123,
        "ssn": "536-90-4412",
        "dob": "1991-04-17",
        "email": "dana@example.com",
    }
    enc = encrypt_pii(record, key, ["ssn", "dob"])
    print("\nENCRYPTED RECORD:")
    for k, v in enc.items():
        print(f"  {k}: {v!r}")

    # A domain expert verifies round-trip decryption with the real primitive.
    import base64
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(key)
    for f in ("ssn", "dob"):
        blob = base64.b64decode(enc[f])
        nonce, ct = blob[:12], blob[12:]
        pt = aes.decrypt(nonce, ct, None).decode()
        print(f"  ROUND-TRIP {f}: {pt!r}  (matches original: {pt == record[f]})")
    assert enc["user_id"] == record["user_id"] and enc["email"] == record["email"]
