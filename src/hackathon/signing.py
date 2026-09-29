"""DSSE-style Ed25519 signing for evidence manifests.

This is a local, offline signing layer built on the ``cryptography`` package. It
is NOT Sigstore/Cosign keyless signing and carries no transparency-log entry;
Cosign can replace this layer later without changing the manifest. It is
internally tested and has not been validated by a third party.

The verifier must be handed the signer's public key out of band. A public key
shipped inside the bundle would let an attacker re-sign with their own key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

PAYLOAD_TYPE = "application/vnd.sensebeen.evidence-manifest+json"
ENVELOPE_FILE = "manifest.dsse.json"


def _pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE v1 pre-authentication encoding."""
    type_bytes = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(type_bytes), type_bytes, len(payload), payload)


def key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(raw).hexdigest()


def generate_keypair(directory: Path, name: str = "signer") -> tuple[Path, Path]:
    """Write ``<name>.key`` (private, mode 0600) and ``<name>.pub``; never overwrite."""
    directory.mkdir(parents=True, exist_ok=True)
    private_path = directory / f"{name}.key"
    public_path = directory / f"{name}.pub"
    if private_path.exists() or public_path.exists():
        raise FileExistsError(f"refusing to overwrite existing key files in {directory}")
    private = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def load_public(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 public key")
    return key


def sign(payload: bytes, private_key: Ed25519PrivateKey) -> dict[str, Any]:
    signature = private_key.sign(_pae(PAYLOAD_TYPE, payload))
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode(),
        "signatures": [
            {
                "keyid": key_id(private_key.public_key()),
                "sig": base64.b64encode(signature).decode(),
            }
        ],
    }


def verify_envelope(
    envelope_text: str, public_key: Ed25519PublicKey
) -> tuple[bool, str, bytes]:
    """Return (ok, reason_code, payload). ``payload`` is empty unless ok."""
    try:
        envelope = json.loads(envelope_text)
        if envelope["payloadType"] != PAYLOAD_TYPE:
            return False, "WRONG_PAYLOAD_TYPE", b""
        payload = base64.b64decode(envelope["payload"], validate=True)
        signatures = envelope["signatures"]
    except (json.JSONDecodeError, KeyError, TypeError, binascii.Error):
        return False, "ENVELOPE_MALFORMED", b""

    expected_id = key_id(public_key)
    for entry in signatures:
        try:
            if entry["keyid"] != expected_id:
                continue
            signature = base64.b64decode(entry["sig"], validate=True)
            public_key.verify(signature, _pae(PAYLOAD_TYPE, payload))
            return True, "", payload
        except (InvalidSignature, KeyError, TypeError, binascii.Error):
            continue
    return False, "SIGNATURE_INVALID", b""
