"""
dh_key_exchange.py — Diffie-Hellman Session Key Negotiation
============================================================
Implements Diffie-Hellman key exchange from scratch (pure Python)
to dynamically negotiate AES-256 and HMAC-256 session keys between
the Edge sensor and Fog Gateway at connection time.

This directly addresses the critical vulnerability named in DA-2:
  "AES and HMAC keys are pre-shared and stored statically
   (shared_aes_key.bin), creating a vulnerability if a single
   edge node is compromised."

With DH:
  - Fresh session keys are generated per connection
  - Compromise of one session does NOT expose other sessions
  - No static key files need to exist on disk
  - The fog and edge never transmit the actual keys — only
    public values from which both sides derive the same secret

Protocol:
  1. Edge connects to Fog over TCP
  2. Edge sends its DH public key (4 bytes length + raw bytes)
  3. Fog sends its DH public key back
  4. Both sides compute the shared secret independently
  5. HKDF (SHA-256) derives two 32-byte keys: AES key + HMAC key
  6. All subsequent packets use these session keys

Security Parameters:
  - Group: RFC 3526 Group 14 (2048-bit MODP) — standard, well-analysed
  - Generator: g = 2
  - Key derivation: HKDF with SHA-256, separate info strings for AES/HMAC
"""

import hashlib
import os
import socket
import struct

# ─────────────────────────────────────────────────────────────────
#  RFC 3526 Group 14 — 2048-bit MODP Prime
#  This is a standardized safe prime used widely in TLS/SSH.
#  Using a well-known prime avoids weak-group vulnerabilities.
# ─────────────────────────────────────────────────────────────────
DH_PRIME_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF"
)
DH_PRIME  = int(DH_PRIME_HEX, 16)
DH_GENERATOR = 2


# ─────────────────────────────────────────────────────────────────
#  HKDF (RFC 5869) — pure Python, SHA-256 based
#  Used to derive AES and HMAC keys from the DH shared secret.
# ─────────────────────────────────────────────────────────────────

def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """HKDF Extract step: PRK = HMAC-SHA256(salt, IKM)."""
    import hmac
    if not salt:
        salt = bytes(32)
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF Expand step: derive `length` bytes of keying material."""
    import hmac
    okm = b""
    t   = b""
    i   = 1
    while len(okm) < length:
        t    = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i   += 1
    return okm[:length]


def hkdf_derive(shared_secret: int, salt: bytes = None) -> tuple:
    """
    Derive AES-256 key and HMAC-256 key from a DH shared secret integer.

    Returns:
        (aes_key: bytes[32], hmac_key: bytes[32])
    """
    # Convert shared secret integer to bytes (big-endian, 256 bytes for 2048-bit)
    ikm = shared_secret.to_bytes(256, "big")

    if salt is None:
        salt = b"SecureFogECG-DH-Salt-v1"

    prk = _hkdf_extract(salt, ikm)

    aes_key  = _hkdf_expand(prk, b"aes-256-cbc-key",  32)
    hmac_key = _hkdf_expand(prk, b"hmac-sha256-key",  32)

    return aes_key, hmac_key


# ─────────────────────────────────────────────────────────────────
#  DH Key Exchange — Core Math
# ─────────────────────────────────────────────────────────────────

class DHParty:
    """
    One side of a Diffie-Hellman key exchange.

    Usage:
        dh = DHParty()
        my_public = dh.public_key       # send this to the other party
        shared    = dh.compute_shared(their_public)
        aes_key, hmac_key = hkdf_derive(shared)
    """

    def __init__(self):
        # Private key: cryptographically random 256-byte integer
        self._private = int.from_bytes(os.urandom(256), "big") % (DH_PRIME - 2) + 2
        # Public key: g^private mod p
        self.public_key = pow(DH_GENERATOR, self._private, DH_PRIME)

    def compute_shared(self, their_public: int) -> int:
        """
        Compute the DH shared secret from the other party's public key.
        Result: their_public^private mod p
        Both parties arrive at the same value independently.
        """
        if not (2 <= their_public <= DH_PRIME - 2):
            raise ValueError("Received invalid DH public key — possible MitM attack")
        return pow(their_public, self._private, DH_PRIME)


# ─────────────────────────────────────────────────────────────────
#  Wire Protocol Helpers
#  Public key is sent as: [4-byte big-endian length][raw bytes]
# ─────────────────────────────────────────────────────────────────

def send_pubkey(sock: socket.socket, public_key: int):
    """Serialise and send a DH public key over a socket."""
    key_bytes = public_key.to_bytes(256, "big")
    sock.sendall(struct.pack(">I", len(key_bytes)) + key_bytes)


def recv_pubkey(sock: socket.socket) -> int:
    """Receive and deserialise a DH public key from a socket."""
    raw_len = _recv_exact(sock, 4)
    length  = struct.unpack(">I", raw_len)[0]
    if length > 512:
        raise ValueError(f"DH public key length suspiciously large: {length}")
    key_bytes = _recv_exact(sock, length)
    return int.from_bytes(key_bytes, "big")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Socket closed during DH handshake")
        data += chunk
    return data


# ─────────────────────────────────────────────────────────────────
#  High-Level Handshake Functions
# ─────────────────────────────────────────────────────────────────

def edge_perform_handshake(sock: socket.socket) -> tuple:
    """
    Called by the Edge sensor immediately after TCP connect.
    Initiates the DH handshake as the 'client' side.

    Returns:
        (aes_key: bytes[32], hmac_key: bytes[32])
    """
    dh = DHParty()
    print(f"[DH] Edge sending public key ({dh.public_key.bit_length()} bits)...")
    send_pubkey(sock, dh.public_key)

    fog_pubkey = recv_pubkey(sock)
    print(f"[DH] Received Fog public key ({fog_pubkey.bit_length()} bits)")

    shared = dh.compute_shared(fog_pubkey)
    aes_key, hmac_key = hkdf_derive(shared)

    print("[DH] Session keys derived via HKDF-SHA256")
    print(f"[DH]   AES-256 key  : {aes_key.hex()[:16]}... (32 bytes)")
    print(f"[DH]   HMAC-256 key : {hmac_key.hex()[:16]}... (32 bytes)")
    print("[DH]   (These keys exist only in memory - never written to disk)")
    return aes_key, hmac_key


def fog_perform_handshake(conn: socket.socket, addr) -> tuple:
    """
    Called by the Fog Gateway for each new client connection.
    Responds to the DH handshake as the 'server' side.

    Returns:
        (aes_key: bytes[32], hmac_key: bytes[32])
    """
    import logging
    log = logging.getLogger(__name__)

    dh = DHParty()
    edge_pubkey = recv_pubkey(conn)
    log.info(f"[DH] Received Edge public key from {addr} ({edge_pubkey.bit_length()} bits)")

    send_pubkey(conn, dh.public_key)
    log.info(f"[DH] Sent Fog public key to {addr}")

    shared = dh.compute_shared(edge_pubkey)
    aes_key, hmac_key = hkdf_derive(shared)

    log.info(f"[DH] Session keys derived for {addr} - AES: {aes_key.hex()[:8]}...")
    return aes_key, hmac_key


# ─────────────────────────────────────────────────────────────────
#  Self-Test
# ─────────────────────────────────────────────────────────────────

def _self_test():
    print("[dh_key_exchange] Running self-test...")

    alice = DHParty()
    bob   = DHParty()

    alice_shared = alice.compute_shared(bob.public_key)
    bob_shared   = bob.compute_shared(alice.public_key)

    assert alice_shared == bob_shared, "DH shared secrets do not match!"
    print(f"  Alice public : {alice.public_key.bit_length()} bits")
    print(f"  Bob public   : {bob.public_key.bit_length()} bits")
    print(f"  Shared secret: {hex(alice_shared)[:20]}... (match ✓)")

    aes_a, hmac_a = hkdf_derive(alice_shared)
    aes_b, hmac_b = hkdf_derive(bob_shared)

    assert aes_a == aes_b,  "AES keys do not match!"
    assert hmac_a == hmac_b, "HMAC keys do not match!"
    print(f"  AES key  : {aes_a.hex()[:16]}... ✓")
    print(f"  HMAC key : {hmac_a.hex()[:16]}... ✓")
    print("[dh_key_exchange] All tests passed ✓\n")


if __name__ == "__main__":
    _self_test()
