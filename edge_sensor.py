"""
edge_sensor.py — DA-2: Edge Layer Simulation
=============================================
Simulates a wearable ECG sensor node.
  - Streams heartbeats from MIT-BIH dataset (or synthetic data)
  - Encrypts payload with AES-256 (CBC mode + HMAC-SHA256 integrity)
  - Sends encrypted packets to the Fog Gateway over TCP sockets

Designed to run on: PC (simulation) or Raspberry Pi Zero W (TinyML edge)

Usage:
    python edge_sensor.py --data_path ./data/ --fog_host 127.0.0.1 --fog_port 9000
"""

import argparse
import json
import os
import socket
import struct
import time
import hashlib
import hmac
import numpy as np
import pandas as pd
import base64
import threading

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import os as _os

# ─────────────────────────────────────────────
#  Shared secret key (in production: exchanged via RSA/ECDH)
#  For simulation: pre-shared symmetric key stored in key file
# ─────────────────────────────────────────────
KEY_FILE   = "model/shared_aes_key.bin"
HMAC_FILE  = "model/shared_hmac_key.bin"
DATA_PATH  = "data/"
FOG_HOST   = "127.0.0.1"
FOG_PORT   = 9000
BPM        = 60          # Simulated heart rate (1 beat/sec)
MAX_BEATS  = None        # None = stream entire dataset


def _ensure_keys():
    """Generate and save AES-256 + HMAC keys if they don't exist."""
    os.makedirs("model", exist_ok=True)
    if not os.path.exists(KEY_FILE):
        key = _os.urandom(32)   # 256-bit AES key
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        print(f"[KEY]  Generated new AES-256 key → {KEY_FILE}")
    if not os.path.exists(HMAC_FILE):
        hkey = _os.urandom(32)  # 256-bit HMAC key
        with open(HMAC_FILE, "wb") as f:
            f.write(hkey)
        print(f"[KEY]  Generated new HMAC-256 key → {HMAC_FILE}")

    with open(KEY_FILE, "rb") as f:
        aes_key = f.read()
    with open(HMAC_FILE, "rb") as f:
        hmac_key = f.read()

    # Security Check: Ensure keys are 256-bit
    if len(aes_key) != 32 or len(hmac_key) != 32:
        raise ValueError("Security Error: Invalid key length. Keys must be 32 bytes for AES-256/HMAC-256.")

    return aes_key, hmac_key


def encrypt_aes256_cbc(plaintext_bytes: bytes, key: bytes):
    """
    AES-256-CBC encryption with PKCS7 padding.
    Returns: IV (16 bytes) + ciphertext
    """
    iv = _os.urandom(16)
    # PKCS7 padding
    pad_len = 16 - (len(plaintext_bytes) % 16)
    padded  = plaintext_bytes + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return iv + ciphertext


def compute_hmac(data: bytes, hmac_key: bytes) -> bytes:
    """HMAC-SHA256 for packet integrity verification at Fog node."""
    return hmac.new(hmac_key, data, hashlib.sha256).digest()


def load_ecg_data(data_path: str):
    """Load test ECG records. Falls back to synthetic data if unavailable."""
    test_path = os.path.join(data_path, "mitbih_test.csv")
    if os.path.exists(test_path):
        print(f"[DATA] Loading MIT-BIH test data from {test_path}")
        df = pd.read_csv(test_path, header=None)
    else:
        print("[DATA] mitbih_test.csv not found — using synthetic ECG data")
        from train_model import _generate_synthetic_data
        _, df = _generate_synthetic_data(n_test=3000)
    return df


def build_packet(beat_id: int, ecg_features: np.ndarray, true_label: int,
                  aes_key: bytes, hmac_key: bytes, verbose: bool = False) -> bytes:
    """
    Build a full encrypted + authenticated packet.
    """
    payload_dict = {
        "beat_id":    beat_id,
        "timestamp":  time.time(),
        "ecg_signal": ecg_features.tolist()[:5],  # Truncate for display if verbose
        "true_label": int(true_label),
        "device_id":  "EDGE_NODE_001",
    }
    
    # Full payload for actual transmission
    full_payload = payload_dict.copy()
    full_payload["ecg_signal"] = ecg_features.tolist()
    
    plaintext = json.dumps(full_payload).encode("utf-8")
    
    if verbose:
        print("\n" + "═"*60)
        print(f"  [ENCRYPTION DEBUG] Beat #{beat_id}")
        print(f"  STEP 1: Plaintext (JSON snippet):")
        print(f"          {json.dumps(payload_dict)}...")
        print(f"  STEP 2: Encrypting with AES-256-CBC...")
    
    ciphertext = encrypt_aes256_cbc(plaintext, aes_key)
    
    if verbose:
        print(f"  STEP 3: Ciphertext (Hex snippet):")
        print(f"          {ciphertext.hex()[:64]}...")
    
    mac = compute_hmac(ciphertext, hmac_key)
    
    if verbose:
        print(f"  STEP 4: HMAC-SHA256 Signature:")
        print(f"          {mac.hex()}")
        print("═"*60 + "\n")

    wire_payload = mac + ciphertext
    frame = struct.pack(">I", len(wire_payload)) + wire_payload
    return frame


class EdgeSensorNode:
    def __init__(self, data_path, fog_host, fog_port, bpm=60, max_beats=None, show_crypto=False):
        self.data_path  = data_path
        self.fog_host   = fog_host
        self.fog_port   = fog_port
        self.beat_interval = 60.0 / bpm
        self.max_beats  = max_beats
        self.show_crypto = show_crypto
        self.aes_key, self.hmac_key = _ensure_keys()
        self.stats = {"sent": 0, "errors": 0, "normal": 0, "anomaly": 0}
        self._running = False

    def _connect(self):
        """Connect to fog gateway with retry logic."""
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.fog_host, self.fog_port))
                print(f"[EDGE] ✓ Connected to Fog Gateway {self.fog_host}:{self.fog_port}")
                return sock
            except ConnectionRefusedError:
                print(f"[EDGE] Fog Gateway not reachable. Retrying in 3s...")
                time.sleep(3)

    def run(self):
        df = load_ecg_data(self.data_path)
        self._running = True
        beat_id = 0

        print(f"\n[EDGE] ═══════════════════════════════════════")
        print(f"[EDGE]  EDGE SENSOR NODE STARTED")
        print(f"[EDGE]  Encryption: AES-256-CBC + HMAC-SHA256")
        print(f"[EDGE]  Simulated BPM: {int(60/self.beat_interval)}")
        print(f"[EDGE]  Total samples: {len(df)}")
        print(f"[EDGE] ═══════════════════════════════════════\n")

        sock = self._connect()

        try:
            for idx, row in df.iterrows():
                if not self._running:
                    break
                if self.max_beats and beat_id >= self.max_beats:
                    break

                ecg_features = row.iloc[:-1].values.astype(np.float32)
                true_label   = int(row.iloc[-1])
                is_anomaly   = true_label != 0

                # Build & send encrypted packet
                t_start = time.time()
                packet  = build_packet(beat_id, ecg_features, true_label,
                                        self.aes_key, self.hmac_key, verbose=self.show_crypto)
                try:
                    sock.sendall(packet)
                    self.stats["sent"] += 1
                    if is_anomaly:
                        self.stats["anomaly"] += 1
                        print(f"[EDGE] Beat #{beat_id:05d} | "
                              f"Label: {true_label} ({_label_name(true_label)}) | "
                              f"⚠ ANOMALY | Encrypted & Sent ({len(packet)} bytes)")
                    else:
                        self.stats["normal"] += 1
                        if beat_id % 10 == 0:   # Print every 10th normal beat
                            print(f"[EDGE] Beat #{beat_id:05d} | "
                                  f"Label: 0 (Normal) | ✓ Encrypted & Sent ({len(packet)} bytes)")
                except (BrokenPipeError, ConnectionResetError):
                    print("[EDGE] Connection lost. Reconnecting...")
                    sock = self._connect()

                beat_id += 1
                # Sleep to simulate real-time heartbeat rate
                elapsed = time.time() - t_start
                sleep_t = max(0, self.beat_interval - elapsed)
                time.sleep(sleep_t)

        except KeyboardInterrupt:
            print("\n[EDGE] Interrupted by user.")
        finally:
            sock.close()
            self._print_summary()

    def _print_summary(self):
        print(f"\n[EDGE] ═══ SESSION SUMMARY ═══")
        print(f"[EDGE]  Beats sent      : {self.stats['sent']}")
        print(f"[EDGE]  Normal beats    : {self.stats['normal']}")
        print(f"[EDGE]  Anomaly beats   : {self.stats['anomaly']}")
        print(f"[EDGE]  Errors          : {self.stats['errors']}")


def _label_name(label):
    return {0: "Normal", 1: "SVP", 2: "PVC", 3: "Fusion", 4: "Unclassifiable"}.get(label, "?")


def main():
    parser = argparse.ArgumentParser(description="ECG Edge Sensor Simulator")
    parser.add_argument("--data_path", default=DATA_PATH)
    parser.add_argument("--fog_host",  default=FOG_HOST)
    parser.add_argument("--fog_port",  type=int, default=FOG_PORT)
    parser.add_argument("--bpm",       type=int, default=BPM,
                        help="Simulated heart rate (beats per minute)")
    parser.add_argument("--max_beats", type=int, default=None,
                        help="Limit number of beats (default: full dataset)")
    parser.add_argument("--show-crypto", action="store_true",
                        help="Show encryption steps for each packet (Security Demo)")
    args = parser.parse_args()

    node = EdgeSensorNode(args.data_path, args.fog_host, args.fog_port,
                          args.bpm, args.max_beats, show_crypto=args.show_crypto)
    node.run()


if __name__ == "__main__":
    main()
