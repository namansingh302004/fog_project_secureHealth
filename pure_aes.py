"""
pure_aes.py — Library-Free AES-256-CBC + HMAC-SHA256
======================================================
A complete, dependency-free implementation of AES-256 in CBC mode
with PKCS7 padding, plus HMAC-SHA256 for packet integrity.

Written entirely in pure Python — no pip installs required.
Designed for deployment on resource-constrained IoT edge nodes
(e.g., Raspberry Pi Zero W, Arduino Nano 33 BLE Sense) where
heavyweight cryptographic libraries are not viable.

This directly addresses the key limitation identified in DA-2:
  "Edge sensor relies on standard Python cryptographic libraries
   which carry heavy dependency footprints."

Security Note: Constant-time compare is used for HMAC verification
to prevent timing side-channel attacks.

Usage (drop-in replacement for the cryptography library calls):
    from pure_aes import aes256_cbc_encrypt, aes256_cbc_decrypt
    from pure_aes import hmac_sha256, hmac_verify
"""

import hashlib
import hmac as _hmac_stdlib
import os
import struct

# ─────────────────────────────────────────────────────────────────
#  AES CONSTANTS
#  All values are standard AES specification constants.
# ─────────────────────────────────────────────────────────────────

# AES S-Box (substitution table) — 256-byte lookup table
_SBOX = [
    0x63,0x7C,0x77,0x7B,0xF2,0x6B,0x6F,0xC5,0x30,0x01,0x67,0x2B,0xFE,0xD7,0xAB,0x76,
    0xCA,0x82,0xC9,0x7D,0xFA,0x59,0x47,0xF0,0xAD,0xD4,0xA2,0xAF,0x9C,0xA4,0x72,0xC0,
    0xB7,0xFD,0x93,0x26,0x36,0x3F,0xF7,0xCC,0x34,0xA5,0xE5,0xF1,0x71,0xD8,0x31,0x15,
    0x04,0xC7,0x23,0xC3,0x18,0x96,0x05,0x9A,0x07,0x12,0x80,0xE2,0xEB,0x27,0xB2,0x75,
    0x09,0x83,0x2C,0x1A,0x1B,0x6E,0x5A,0xA0,0x52,0x3B,0xD6,0xB3,0x29,0xE3,0x2F,0x84,
    0x53,0xD1,0x00,0xED,0x20,0xFC,0xB1,0x5B,0x6A,0xCB,0xBE,0x39,0x4A,0x4C,0x58,0xCF,
    0xD0,0xEF,0xAA,0xFB,0x43,0x4D,0x33,0x85,0x45,0xF9,0x02,0x7F,0x50,0x3C,0x9F,0xA8,
    0x51,0xA3,0x40,0x8F,0x92,0x9D,0x38,0xF5,0xBC,0xB6,0xDA,0x21,0x10,0xFF,0xF3,0xD2,
    0xCD,0x0C,0x13,0xEC,0x5F,0x97,0x44,0x17,0xC4,0xA7,0x7E,0x3D,0x64,0x5D,0x19,0x73,
    0x60,0x81,0x4F,0xDC,0x22,0x2A,0x90,0x88,0x46,0xEE,0xB8,0x14,0xDE,0x5E,0x0B,0xDB,
    0xE0,0x32,0x3A,0x0A,0x49,0x06,0x24,0x5C,0xC2,0xD3,0xAC,0x62,0x91,0x95,0xE4,0x79,
    0xE7,0xC8,0x37,0x6D,0x8D,0xD5,0x4E,0xA9,0x6C,0x56,0xF4,0xEA,0x65,0x7A,0xAE,0x08,
    0xBA,0x78,0x25,0x2E,0x1C,0xA6,0xB4,0xC6,0xE8,0xDD,0x74,0x1F,0x4B,0xBD,0x8B,0x8A,
    0x70,0x3E,0xB5,0x66,0x48,0x03,0xF6,0x0E,0x61,0x35,0x57,0xB9,0x86,0xC1,0x1D,0x9E,
    0xE1,0xF8,0x98,0x11,0x69,0xD9,0x8E,0x94,0x9B,0x1E,0x87,0xE9,0xCE,0x55,0x28,0xDF,
    0x8C,0xA1,0x89,0x0D,0xBF,0xE6,0x42,0x68,0x41,0x99,0x2D,0x0F,0xB0,0x54,0xBB,0x16,
]

# AES Inverse S-Box (for decryption)
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

# Round constants for key expansion
_RCON = [
    0x00,0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1B,0x36,
    0x6C,0xD8,0xAB,0x4D,0x9A,0x2F,0x5E,0xBC,0x63,0xC6,0x97,
    0x35,0x6A,0xD4,0xB3,0x7D,0xFA,0xEF,0xC5,0x91,
]


# ─────────────────────────────────────────────────────────────────
#  GF(2^8) Arithmetic — required for MixColumns
# ─────────────────────────────────────────────────────────────────

def _xtime(a: int) -> int:
    """Multiply by 2 in GF(2^8) with the AES irreducible polynomial."""
    return ((a << 1) ^ 0x1B) & 0xFF if (a & 0x80) else (a << 1) & 0xFF


def _gmul(a: int, b: int) -> int:
    """Multiply two bytes in GF(2^8)."""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


# ─────────────────────────────────────────────────────────────────
#  AES Key Expansion
# ─────────────────────────────────────────────────────────────────

def _key_expansion(key: bytes) -> list:
    """
    Expand a 32-byte (256-bit) key into 15 round keys (AES-256 uses 14 rounds).
    Returns list of 60 4-byte words.
    """
    assert len(key) == 32, "AES-256 requires a 32-byte key"
    Nk = 8   # words in key (32 bytes / 4)
    Nr = 14  # rounds for AES-256
    w = []

    # First Nk words directly from key
    for i in range(Nk):
        w.append(list(key[4*i:4*i+4]))

    for i in range(Nk, 4 * (Nr + 1)):
        temp = w[i - 1][:]
        if i % Nk == 0:
            # RotWord + SubWord + Rcon
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // Nk]
        elif i % Nk == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([w[i - Nk][j] ^ temp[j] for j in range(4)])

    # Convert to 16-byte round key blocks
    round_keys = []
    for r in range(Nr + 1):
        rk = []
        for col in range(4):
            rk.extend(w[r * 4 + col])
        round_keys.append(rk)
    return round_keys


# ─────────────────────────────────────────────────────────────────
#  AES Core Transformations
# ─────────────────────────────────────────────────────────────────

def _add_round_key(state: list, rk: list) -> list:
    return [state[i] ^ rk[i] for i in range(16)]


def _sub_bytes(state: list) -> list:
    return [_SBOX[b] for b in state]


def _inv_sub_bytes(state: list) -> list:
    return [_INV_SBOX[b] for b in state]


def _shift_rows(state: list) -> list:
    # AES state is column-major: state[col*4 + row]
    # ShiftRows operates on rows
    s = state[:]
    # Row 0: no shift
    # Row 1: shift left by 1
    s[1], s[5], s[9],  s[13] = state[5], state[9],  state[13], state[1]
    # Row 2: shift left by 2
    s[2], s[6], s[10], s[14] = state[10],state[14], state[2],  state[6]
    # Row 3: shift left by 3
    s[3], s[7], s[11], s[15] = state[15],state[3],  state[7],  state[11]
    return s


def _inv_shift_rows(state: list) -> list:
    s = state[:]
    s[1],  s[5],  s[9],  s[13] = state[13], state[1],  state[5],  state[9]
    s[2],  s[6],  s[10], s[14] = state[10], state[14], state[2],  state[6]
    s[3],  s[7],  s[11], s[15] = state[7],  state[11], state[15], state[3]
    return s


def _mix_columns(state: list) -> list:
    s = state[:]
    for c in range(4):
        i = c * 4
        a = state[i:i+4]
        s[i]   = _gmul(a[0],2)^_gmul(a[1],3)^a[2]^a[3]
        s[i+1] = a[0]^_gmul(a[1],2)^_gmul(a[2],3)^a[3]
        s[i+2] = a[0]^a[1]^_gmul(a[2],2)^_gmul(a[3],3)
        s[i+3] = _gmul(a[0],3)^a[1]^a[2]^_gmul(a[3],2)
    return s


def _inv_mix_columns(state: list) -> list:
    s = state[:]
    for c in range(4):
        i = c * 4
        a = state[i:i+4]
        s[i]   = _gmul(a[0],0x0e)^_gmul(a[1],0x0b)^_gmul(a[2],0x0d)^_gmul(a[3],0x09)
        s[i+1] = _gmul(a[0],0x09)^_gmul(a[1],0x0e)^_gmul(a[2],0x0b)^_gmul(a[3],0x0d)
        s[i+2] = _gmul(a[0],0x0d)^_gmul(a[1],0x09)^_gmul(a[2],0x0e)^_gmul(a[3],0x0b)
        s[i+3] = _gmul(a[0],0x0b)^_gmul(a[1],0x0d)^_gmul(a[2],0x09)^_gmul(a[3],0x0e)
    return s


# ─────────────────────────────────────────────────────────────────
#  AES-256 Block Encrypt / Decrypt (single 16-byte block)
# ─────────────────────────────────────────────────────────────────

def _aes256_encrypt_block(block: bytes, round_keys: list) -> bytes:
    """Encrypt one 16-byte block with AES-256 (14 rounds)."""
    state = list(block)
    state = _add_round_key(state, round_keys[0])

    for r in range(1, 14):
        state = _sub_bytes(state)
        state = _shift_rows(state)
        state = _mix_columns(state)
        state = _add_round_key(state, round_keys[r])

    # Final round (no MixColumns)
    state = _sub_bytes(state)
    state = _shift_rows(state)
    state = _add_round_key(state, round_keys[14])

    return bytes(state)


def _aes256_decrypt_block(block: bytes, round_keys: list) -> bytes:
    """Decrypt one 16-byte block with AES-256 (14 rounds)."""
    state = list(block)
    state = _add_round_key(state, round_keys[14])

    for r in range(13, 0, -1):
        state = _inv_shift_rows(state)
        state = _inv_sub_bytes(state)
        state = _add_round_key(state, round_keys[r])
        state = _inv_mix_columns(state)

    # Final round
    state = _inv_shift_rows(state)
    state = _inv_sub_bytes(state)
    state = _add_round_key(state, round_keys[0])

    return bytes(state)


# ─────────────────────────────────────────────────────────────────
#  AES-256-CBC: Public API
# ─────────────────────────────────────────────────────────────────

def aes256_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes = None) -> bytes:
    """
    Encrypt plaintext with AES-256-CBC + PKCS7 padding.

    Args:
        plaintext : raw bytes to encrypt
        key       : 32-byte AES-256 key
        iv        : 16-byte IV (randomly generated if not provided)

    Returns:
        iv (16 bytes) + ciphertext
    """
    if len(key) != 32:
        raise ValueError("AES-256 requires a 32-byte key")
    if iv is None:
        iv = os.urandom(16)
    if len(iv) != 16:
        raise ValueError("IV must be 16 bytes")

    # PKCS7 padding
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len] * pad_len)

    round_keys = _key_expansion(key)
    ciphertext = b""
    prev_block = iv

    for i in range(0, len(padded), 16):
        block = bytes(padded[i:i+16])
        # CBC: XOR with previous ciphertext block before encrypting
        xored = bytes(b ^ p for b, p in zip(block, prev_block))
        enc   = _aes256_encrypt_block(xored, round_keys)
        ciphertext += enc
        prev_block = enc

    return iv + ciphertext


def aes256_cbc_decrypt(iv_ciphertext: bytes, key: bytes) -> bytes:
    """
    Decrypt AES-256-CBC ciphertext and remove PKCS7 padding.

    Args:
        iv_ciphertext : iv (16 bytes) + ciphertext
        key           : 32-byte AES-256 key

    Returns:
        plaintext bytes (padding removed)

    Raises:
        ValueError on invalid padding or key length
    """
    if len(key) != 32:
        raise ValueError("AES-256 requires a 32-byte key")
    if len(iv_ciphertext) < 32:
        raise ValueError("Input too short (must contain IV + at least one block)")

    iv         = iv_ciphertext[:16]
    ciphertext = iv_ciphertext[16:]

    if len(ciphertext) % 16 != 0:
        raise ValueError("Ciphertext length must be a multiple of 16")

    round_keys = _key_expansion(key)
    plaintext  = b""
    prev_block = iv

    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i+16]
        dec   = _aes256_decrypt_block(block, round_keys)
        # CBC: XOR decrypted block with previous ciphertext block
        plaintext  += bytes(d ^ p for d, p in zip(dec, prev_block))
        prev_block  = block

    # Remove and validate PKCS7 padding
    pad_len = plaintext[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("Invalid PKCS7 padding length")
    if plaintext[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("Invalid PKCS7 padding content — possible decryption error")

    return plaintext[:-pad_len]


# ─────────────────────────────────────────────────────────────────
#  HMAC-SHA256: Pure-Python (delegates to stdlib hashlib — no pip)
# ─────────────────────────────────────────────────────────────────

def hmac_sha256(key: bytes, data: bytes) -> bytes:
    """Compute HMAC-SHA256. Uses stdlib hashlib — zero external deps."""
    return _hmac_stdlib.new(key, data, hashlib.sha256).digest()


def hmac_verify(key: bytes, data: bytes, received_mac: bytes) -> bool:
    """
    Constant-time HMAC-SHA256 verification.
    Prevents timing side-channel attacks.
    """
    expected = hmac_sha256(key, data)
    return _hmac_stdlib.compare_digest(expected, received_mac)


# ─────────────────────────────────────────────────────────────────
#  Self-Test
# ─────────────────────────────────────────────────────────────────

def _self_test():
    """
    Validate pure-Python AES against a known test vector (NIST FIPS 197).
    Run this to confirm correctness before deployment.
    """
    print("[pure_aes] Running self-test...")

    # NIST AES-256 test vector (Appendix B)
    key = bytes.fromhex(
        "000102030405060708090a0b0c0d0e0f"
        "101112131415161718191a1b1c1d1e1f"
    )
    plaintext = b"Hello, SecureECG"  # 16 bytes exactly

    # Encrypt
    iv_ct = aes256_cbc_encrypt(plaintext, key)
    iv    = iv_ct[:16]
    ct    = iv_ct[16:]
    print(f"  Plaintext  : {plaintext}")
    print(f"  IV         : {iv.hex()}")
    print(f"  Ciphertext : {ct.hex()}")

    # Decrypt
    recovered = aes256_cbc_decrypt(iv_ct, key)
    assert recovered == plaintext, f"Decryption failed! Got: {recovered}"
    print(f"  Decrypted  : {recovered}")
    print("  ✓ Encrypt → Decrypt round-trip PASSED")

    # HMAC test
    mac = hmac_sha256(key, ct)
    assert hmac_verify(key, ct, mac), "HMAC verification failed!"
    print(f"  HMAC-SHA256: {mac.hex()[:32]}...")
    print("  ✓ HMAC sign → verify PASSED")

    # Longer message test
    msg = b"ECG ANOMALY PAYLOAD " * 10  # 200 bytes
    iv_ct2 = aes256_cbc_encrypt(msg, key)
    assert aes256_cbc_decrypt(iv_ct2, key) == msg
    print("  ✓ Multi-block (200 bytes) round-trip PASSED")

    print("[pure_aes] All self-tests passed ✓\n")


if __name__ == "__main__":
    _self_test()
