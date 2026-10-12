"""CryptoJS-compatible AES-256-CBC helpers for Parsefile portal ``_encrypted`` payloads.

Ported from dealsbot ``internal/sources/parsefile/crypto.go``.
Passphrase is the portal ``SESSID`` cookie value.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any


def _evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    derived = b""
    block = b""
    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block
    return derived[:key_len], derived[key_len : key_len + iv_len]


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    padding = block_size - (len(data) % block_size)
    return data + bytes([padding] * padding)


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data or len(data) % block_size != 0:
        raise ValueError("invalid padded data")
    padding = data[-1]
    if padding == 0 or padding > block_size or padding > len(data):
        raise ValueError("invalid pkcs7 padding")
    if data[-padding:] != bytes([padding] * padding):
        raise ValueError("invalid pkcs7 padding")
    return data[:-padding]


def encrypt(passphrase: str, plaintext: str) -> str:
    """Return CryptoJS JSON wire string ``{\"ct\",\"iv\",\"s\"}`` (hex iv/s, b64 ct)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    salt = os.urandom(8)
    key, iv = _evp_bytes_to_key(passphrase.encode("utf-8"), salt, 32, 16)
    padded = _pkcs7_pad(plaintext.encode("utf-8"))
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    import base64

    payload = {
        "ct": base64.b64encode(ciphertext).decode("ascii"),
        "iv": iv.hex(),
        "s": salt.hex(),
    }
    return json.dumps(payload, separators=(",", ":"))


def decrypt(passphrase: str, payload: str) -> str:
    """Decrypt a CryptoJS JSON wire string using the session passphrase."""
    import base64

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    try:
        enc: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ValueError("parse encrypted payload") from e
    ct_b64 = enc.get("ct")
    if not isinstance(ct_b64, str) or not ct_b64:
        raise ValueError("encrypted payload missing ct")
    salt = bytes.fromhex(str(enc.get("s") or ""))
    iv = bytes.fromhex(str(enc.get("iv") or ""))
    ciphertext = base64.b64decode(ct_b64)
    key, _ = _evp_bytes_to_key(passphrase.encode("utf-8"), salt, 32, 16)
    if len(ciphertext) % 16 != 0:
        raise ValueError("ciphertext is not a multiple of block size")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(plain).decode("utf-8")
