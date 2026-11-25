"""Encryption at rest (AES-256-GCM) for the servers' keys, and hashing of API keys."""
from __future__ import annotations

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_NONCE = 12


def encrypt(data: bytes) -> bytes:
    nonce = os.urandom(_NONCE)
    return nonce + AESGCM(get_settings().secret_key_bytes).encrypt(nonce, data, None)


def decrypt(blob: bytes) -> bytes:
    return AESGCM(get_settings().secret_key_bytes).decrypt(blob[:_NONCE], blob[_NONCE:], None)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
