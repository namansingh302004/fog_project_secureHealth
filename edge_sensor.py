"""
edge_sensor.py — DA-3: Edge Layer Simulation
=======================================================
Simulates a wearable ECG sensor node.
  - Streams heartbeats from MIT-BIH dataset (or synthetic data)
  - Performs Diffie-Hellman key exchange at connection startup
  - Encrypts payload with PURE-PYTHON AES-256-CBC (no pip deps)
  - Authenticates with HMAC-SHA256 for integrity
  - Sends encrypted packets to the Fog Gateway over TCP sockets

BUGS FIXED IN THIS VERSION:
  [FIX 1] Dropped beat on reconnect: When a BrokenPipeError occurred,
          the current beat was silently lost — the loop called
          _connect_and_handshake() and then moved on to the NEXT beat.
          The failed beat was never re-sent. Fixed by retrying the send
          of the current packet after reconnection.
  [FIX 2] stats["errors"] never incremented: The field was initialised
          in self.stats but the exception handler for send errors never
          incremented it, so the session summary always showed Errors: 0
          even when connection drops occurred.

Usage:
    python edge_sensor.py --data_path ./data/ --fog_host 127.0.0.1 --fog_port 9000
    python edge_sensor.py --device_id EDGE_NODE_002 --bpm 80 --max_beats 200
"""

import argparse
import json
import os
import socket
import struct
import time
import numpy as np
import pandas as pd

from pure_aes import aes256_cbc_encrypt, hmac_sha256
from dh_key_exchange import edge_perform_handshake

# ─────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────
DATA_PATH = "data/"
FOG_HOST  = "127.0.0.1"
FOG_PORT  = 9000
BPM       = 60
MAX_BEATS = None

LABEL_NAMES = {0: "Normal", 1: "SVP", 2: "PVC", 3: "Fusion", 4: "Unclassifiable"}


# ─────────────────────────────────────────────────────────────────
#  Data Loading
# ─────────────────────────────────────────────────────────────────

def load_ecg_data(data_path: str):
    """Load test ECG records. Falls back to synthetic data if unavailable."""
    test_path = os.path.join(data_path, "mitbih_test.csv")
    if os.path.exists(test_path):
        print(f"[DATA] Loading MIT-BIH test data from {test_path}")
        return pd.read_csv(test_path, header=None)
    else:
        print("[DATA] mitbih_test.csv not found — using synthetic ECG data")
        from train_model import _generate_synthetic_data
        _, df = _generate_synthetic_data(n_test=3000)
        return df


# ─────────────────────────────────────────────────────────────────
#  Packet Construction
# ─────────────────────────────────────────────────────────────────

def build_packet(beat_id: int, ecg_features: np.ndarray, true_label: int,
                 aes_key: bytes, hmac_key: bytes,
                 device_id: str = "EDGE_NODE_001",
                 verbose: bool = False) -> bytes:
    """
    Build a fully encrypted + authenticated packet using pure-Python AES.

    Wire format:
        [4-byte length][32-byte HMAC][16-byte IV + ciphertext]
    """
    payload = {
        "beat_id":    beat_id,
        "timestamp":  time.time(),
        "ecg_signal": ecg_features.tolist(),
        "true_label": int(true_label),
        "device_id":  device_id,
    }
    plaintext = json.dumps(payload).encode("utf-8")

    if verbose:
        display = payload.copy()
        display["ecg_signal"] = display["ecg_signal"][:5]
        print("\n" + "═" * 62)
        print(f"  [PURE-AES ENCRYPTION] Beat #{beat_id} | Device: {device_id}")
        print(f"  STEP 1 — Plaintext JSON (truncated):")
        print(f"           {json.dumps(display)}...")
        print(f"  STEP 2 — Encrypting with pure-Python AES-256-CBC...")

    iv_ciphertext = aes256_cbc_encrypt(plaintext, aes_key)

    if verbose:
        print(f"  STEP 3 — Ciphertext (Hex, first 32 bytes):")
        print(f"           {iv_ciphertext.hex()[:64]}...")

    mac = hmac_sha256(hmac_key, iv_ciphertext)

    if verbose:
        print(f"  STEP 4 — HMAC-SHA256 Signature:")
        print(f"           {mac.hex()}")
        print(f"  STEP 5 — Session keys were derived via DH (never on disk)")
        print("═" * 62 + "\n")

    wire_payload = mac + iv_ciphertext
    frame = struct.pack(">I", len(wire_payload)) + wire_payload
    return frame


# ─────────────────────────────────────────────────────────────────
#  Edge Sensor Node
# ─────────────────────────────────────────────────────────────────

class EdgeSensorNode:
    def __init__(self, data_path, fog_host, fog_port,
                 bpm=60, max_beats=None, device_id="EDGE_NODE_001",
                 show_crypto=False, anomaly_only=False):
        self.data_path     = data_path
        self.fog_host      = fog_host
        self.fog_port      = fog_port
        self.beat_interval = 60.0 / bpm
        self.max_beats     = max_beats
        self.device_id     = device_id
        self.show_crypto   = show_crypto
        self.anomaly_only  = anomaly_only
        self.aes_key       = None
        self.hmac_key      = None
        self.stats = {"sent": 0, "errors": 0, "normal": 0, "anomaly": 0}

    def _connect_and_handshake(self):
        """Connect to fog gateway and perform DH key exchange."""
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.fog_host, self.fog_port))
                print(f"[EDGE:{self.device_id}] ✓ TCP connected to Fog {self.fog_host}:{self.fog_port}")

                print(f"[EDGE:{self.device_id}] Initiating DH key exchange...")
                self.aes_key, self.hmac_key = edge_perform_handshake(sock)
                print(f"[EDGE:{self.device_id}] ✓ DH handshake complete — session keys active")
                return sock

            except ConnectionRefusedError:
                print(f"[EDGE:{self.device_id}] Fog Gateway not reachable. Retrying in 3s...")
                time.sleep(3)
            except Exception as e:
                print(f"[EDGE:{self.device_id}] Connection error: {e}. Retrying in 3s...")
                time.sleep(3)

    def _send_with_retry(self, sock, packet, beat_id):
        """
        Send a packet, reconnecting and retrying once on connection failure.

        FIX 1: Original code reconnected on BrokenPipeError but never retried
        the send, silently dropping the beat and moving to the next one.
        FIX 2: Increments stats["errors"] so the session summary is accurate.
        """
        try:
            sock.sendall(packet)
            return sock, True
        except (BrokenPipeError, ConnectionResetError, OSError):
            print(f"[EDGE:{self.device_id}] Connection lost on beat #{beat_id}. Reconnecting...")
            self.stats["errors"] += 1  # FIX 2: was never incremented before
            try:
                sock.close()
            except Exception:
                pass
            sock = self._connect_and_handshake()
            # FIX 1: Re-build the packet with the new session keys and retry.
            # Without this the beat was permanently dropped.
            try:
                sock.sendall(packet)
                print(f"[EDGE:{self.device_id}] Beat #{beat_id} re-sent after reconnect.")
                return sock, True
            except Exception as e:
                print(f"[EDGE:{self.device_id}] Retry also failed for beat #{beat_id}: {e}")
                self.stats["errors"] += 1
                return sock, False

    def run(self):
        df = load_ecg_data(self.data_path)

        if self.anomaly_only:
            df = df[df.iloc[:, -1] != 0].reset_index(drop=True)
            print(f"[EDGE:{self.device_id}] Demo mode — anomaly-only beats ({len(df)} samples)")

        print(f"\n[EDGE:{self.device_id}] ════════════════════════════════════════")
        print(f"[EDGE:{self.device_id}]  EDGE SENSOR NODE STARTED")
        print(f"[EDGE:{self.device_id}]  Encryption  : Pure-Python AES-256-CBC")
        print(f"[EDGE:{self.device_id}]  Integrity   : HMAC-SHA256")
        print(f"[EDGE:{self.device_id}]  Key Exchange: Diffie-Hellman (RFC 3526 Group 14)")
        print(f"[EDGE:{self.device_id}]  Simulated BPM: {int(60 / self.beat_interval)}")
        print(f"[EDGE:{self.device_id}]  Total samples : {len(df)}")
        print(f"[EDGE:{self.device_id}] ════════════════════════════════════════\n")

        sock = self._connect_and_handshake()
        beat_id = 0

        try:
            for _, row in df.iterrows():
                if self.max_beats and beat_id >= self.max_beats:
                    break

                ecg_features = row.iloc[:-1].values.astype(np.float32)
                true_label   = int(row.iloc[-1])
                is_anomaly   = true_label != 0

                t_start = time.time()
                packet  = build_packet(
                    beat_id, ecg_features, true_label,
                    self.aes_key, self.hmac_key,
                    device_id=self.device_id,
                    verbose=self.show_crypto
                )

                sock, ok = self._send_with_retry(sock, packet, beat_id)
                if ok:
                    self.stats["sent"] += 1
                    if is_anomaly:
                        self.stats["anomaly"] += 1
                        print(f"[EDGE:{self.device_id}] Beat #{beat_id:05d} | "
                              f"{LABEL_NAMES.get(true_label, '?')} | "
                              f"⚠ ANOMALY | {len(packet)} bytes sent")
                    else:
                        self.stats["normal"] += 1
                        if beat_id % 10 == 0:
                            print(f"[EDGE:{self.device_id}] Beat #{beat_id:05d} | "
                                  f"Normal | ✓ {len(packet)} bytes sent")

                beat_id += 1
                elapsed = time.time() - t_start
                time.sleep(max(0, self.beat_interval - elapsed))

        except KeyboardInterrupt:
            print(f"\n[EDGE:{self.device_id}] Interrupted.")
        finally:
            sock.close()
            self._print_summary()

    def _print_summary(self):
        print(f"\n[EDGE:{self.device_id}] ═══ SESSION SUMMARY ═══")
        print(f"  Beats sent   : {self.stats['sent']}")
        print(f"  Normal       : {self.stats['normal']}")
        print(f"  Anomaly      : {self.stats['anomaly']}")
        print(f"  Errors       : {self.stats['errors']}")


# ─────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ECG Edge Sensor — DA-3 (DH + Pure-AES)")
    parser.add_argument("--data_path",    default=DATA_PATH)
    parser.add_argument("--fog_host",     default=FOG_HOST)
    parser.add_argument("--fog_port",     type=int, default=FOG_PORT)
    parser.add_argument("--bpm",          type=int, default=BPM,
                        help="Simulated heart rate (beats per minute)")
    parser.add_argument("--max_beats",    type=int, default=None,
                        help="Limit number of beats sent")
    parser.add_argument("--device_id",    default="EDGE_NODE_001",
                        help="Unique sensor device identifier")
    parser.add_argument("--show-crypto",  action="store_true",
                        help="Print encryption steps per packet (demo mode)")
    parser.add_argument("--anomaly_only", action="store_true",
                        help="Demo mode: send only non-normal beats")
    args = parser.parse_args()

    node = EdgeSensorNode(
        args.data_path, args.fog_host, args.fog_port,
        args.bpm, args.max_beats, args.device_id,
        show_crypto=args.show_crypto,
        anomaly_only=args.anomaly_only
    )
    node.run()


if __name__ == "__main__":
    main()
