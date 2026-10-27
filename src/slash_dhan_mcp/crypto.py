from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = "v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _aes_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def encrypt(secret: str, plaintext: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_aes_key(secret)).encrypt(nonce, plaintext.encode(), None)
    return f"{_VERSION}:{_b64(nonce)}:{_b64(sealed)}"


def decrypt(secret: str, stored: str) -> str:
    version, nonce, sealed = stored.split(":")
    if version != _VERSION:
        raise ValueError(f"unsupported ciphertext version {version}")
    return AESGCM(_aes_key(secret)).decrypt(_unb64(nonce), _unb64(sealed), None).decode()
