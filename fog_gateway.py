"""
fog_gateway.py — DA-2: Fog Intelligence Layer
==============================================
The Fog Node sits between the Edge sensor and Cloud server.
  - Listens for encrypted ECG packets from edge nodes (TCP)
  - Decrypts using AES-256-CBC + verifies HMAC-SHA256 integrity
  - Runs Isolation Forest ML model for real-time anomaly detection
  - Normal beats → logged locally only (bandwidth saving ~90%)
  - Anomalies   → forwarded immediately to Cloud (HTTP POST)
  - Triggers local alerts for critical cardiac events

TinyML Target: Raspberry Pi 4 (or RPi Zero 2W)
Inference latency target: < 100ms per beat

Usage:
    python fog_gateway.py --cloud_host 127.0.0.1 --cloud_port 8080
"""

import argparse
import hashlib
import hmac as hmac_lib
import json
import logging
import os
import pickle
import socket
import struct
import threading
import time
import numpy as np
from datetime import datetime
from http.client import HTTPConnection

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────
FOG_HOST    = "0.0.0.0"
FOG_PORT    = 9000
CLOUD_HOST  = "127.0.0.1"
CLOUD_PORT  = 8080
MODEL_DIR   = "model/"
LOG_FILE    = "logs/fog_gateway.log"
ALERT_THRESHOLD = -0.1   # IF decision function threshold; tune based on your data

# Labels
LABEL_MAP = {0: "Normal", 1: "Supraventricular", 2: "PVC",
             3: "Fusion", 4: "Unclassifiable"}
CRITICAL_LABELS = {1, 2, 3, 4}   # All non-Normal

# ─────────────────────────────────────────────
#  Logging setup
# ─────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FOG] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def load_crypto_keys():
    """Load pre-shared AES and HMAC keys (same keys as edge_sensor.py)."""
    aes_path  = os.path.join(MODEL_DIR, "shared_aes_key.bin")
    hmac_path = os.path.join(MODEL_DIR, "shared_hmac_key.bin")
    if not os.path.exists(aes_path):
        raise FileNotFoundError(
            "AES key not found. Run edge_sensor.py first to generate keys.")
    with open(aes_path, "rb") as f:
        aes_key = f.read()
    with open(hmac_path, "rb") as f:
        hmac_key = f.read()
    return aes_key, hmac_key


def load_ml_model():
    """Load the Isolation Forest pipeline (model + scaler + PCA)."""
    required = ["isolation_forest.pkl", "scaler.pkl", "pca.pkl"]
    for r in required:
        if not os.path.exists(os.path.join(MODEL_DIR, r)):
            raise FileNotFoundError(
                f"{r} not found. Run train_model.py first.")

    with open(os.path.join(MODEL_DIR, "isolation_forest.pkl"), "rb") as f:
        model = pickle.load(f)
    with open(os.path.join(MODEL_DIR, "scaler.pkl"), "rb") as f:
        scaler = pickle.load(f)
    with open(os.path.join(MODEL_DIR, "pca.pkl"), "rb") as f:
        pca = pickle.load(f)

    log.info(f"ML pipeline loaded: IsolationForest + StandardScaler + PCA")
    return model, scaler, pca


# ─────────────────────────────────────────────
#  Cryptography Helpers
# ─────────────────────────────────────────────
def verify_hmac(ciphertext: bytes, received_mac: bytes, hmac_key: bytes) -> bool:
    expected_mac = hmac_lib.new(hmac_key, ciphertext, hashlib.sha256).digest()
    return hmac_lib.compare_digest(expected_mac, received_mac)


def decrypt_aes256_cbc(ciphertext_with_iv: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    try:
        if len(ciphertext_with_iv) < 16:
            raise ValueError("Ciphertext too short (missing IV)")

        iv         = ciphertext_with_iv[:16]
        ciphertext = ciphertext_with_iv[16:]

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove PKCS7 padding with validation
        pad_len = padded[-1]
        if pad_len < 1 or pad_len > 16:
            raise ValueError("Invalid PKCS7 padding length")
        
        # Verify all padding bytes are correct
        if padded[-pad_len:] != bytes([pad_len] * pad_len):
            raise ValueError("Invalid PKCS7 padding content")

        return padded[:-pad_len]
    except Exception as e:
        log.error(f"❌ DECRYPTION FAILURE: {str(e)}")
        raise ValueError("Decryption failed - possible key mismatch or data corruption")


def recv_exact(sock, n):
    """Receive exactly n bytes from socket."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Socket closed mid-read")
        data += chunk
    return data


# ─────────────────────────────────────────────
#  ML Inference
# ─────────────────────────────────────────────
def classify_beat(ecg_features: np.ndarray, model, scaler, pca) -> dict:
    """
    Run the Isolation Forest pipeline on a single ECG beat.
    Returns classification result with timing.
    """
    t_start = time.perf_counter()

    X = ecg_features.reshape(1, -1)
    X_scaled = scaler.transform(X)
    X_pca    = pca.transform(X_scaled)

    prediction    = model.predict(X_pca)[0]          # +1 = normal, -1 = anomaly
    anomaly_score = model.decision_function(X_pca)[0] # lower = more anomalous

    is_anomaly = (prediction == -1)
    elapsed_ms = (time.perf_counter() - t_start) * 1000

    return {
        "is_anomaly":    is_anomaly,
        "if_prediction": int(prediction),
        "anomaly_score": float(anomaly_score),
        "inference_ms":  round(elapsed_ms, 3)
    }


# ─────────────────────────────────────────────
#  Cloud Communication
# ─────────────────────────────────────────────
def forward_to_cloud(alert_payload: dict, cloud_host: str, cloud_port: int):
    """
    Send anomaly alert to Cloud server via HTTP POST.
    Only anomalies are forwarded (bandwidth optimization: ~90% reduction).
    """
    try:
        body = json.dumps(alert_payload).encode("utf-8")
        conn = HTTPConnection(cloud_host, cloud_port, timeout=5)
        conn.request("POST", "/alert",
                     body=body,
                     headers={"Content-Type": "application/json",
                               "X-Source": "FOG_GATEWAY_001"})
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception as e:
        log.warning(f"Cloud forwarding failed: {e}")
        return False


# ─────────────────────────────────────────────
#  Statistics Tracker
# ─────────────────────────────────────────────
class FogStats:
    def __init__(self):
        self.lock          = threading.Lock()
        self.total         = 0
        self.normal        = 0
        self.anomaly       = 0
        self.forwarded     = 0
        self.hmac_failures = 0
        self.avg_latency_ms = 0.0
        self._latencies    = []
        self.start_time    = time.time()

    def record(self, is_anomaly: bool, forwarded: bool, latency_ms: float):
        with self.lock:
            self.total += 1
            if is_anomaly:
                self.anomaly += 1
            else:
                self.normal += 1
            if forwarded:
                self.forwarded += 1
            self._latencies.append(latency_ms)
            self.avg_latency_ms = sum(self._latencies[-100:]) / len(self._latencies[-100:])

    def report(self) -> dict:
        uptime = time.time() - self.start_time
        with self.lock:
            bandwidth_saved = (self.normal / max(self.total, 1)) * 100
            return {
                "uptime_s":          round(uptime, 1),
                "total_beats":       self.total,
                "normal_beats":      self.normal,
                "anomaly_beats":     self.anomaly,
                "forwarded_to_cloud": self.forwarded,
                "bandwidth_saved_pct": round(bandwidth_saved, 1),
                "hmac_failures":     self.hmac_failures,
                "avg_inference_ms":  round(self.avg_latency_ms, 3),
                "beats_per_sec":     round(self.total / max(uptime, 1), 2)
            }


# ─────────────────────────────────────────────
#  Client Handler (per connection thread)
# ─────────────────────────────────────────────
def handle_client(conn, addr, aes_key, hmac_key, model, scaler, pca,
                   stats: FogStats, cloud_host: str, cloud_port: int, show_crypto: bool = False):
    log.info(f"Edge node connected: {addr}")
    beat_count = 0
    try:
        while True:
            # Read 4-byte length prefix
            raw_len = recv_exact(conn, 4)
            if not raw_len: break
            payload_len = struct.unpack(">I", raw_len)[0]

            # Read full payload
            wire_payload = recv_exact(conn, payload_len)

            # Split MAC (first 32 bytes) + ciphertext (rest)
            received_mac  = wire_payload[:32]
            ciphertext    = wire_payload[32:]

            if show_crypto:
                log.info("\n" + "═"*60)
                log.info(f"  [DECRYPTION DEBUG] Packet from {addr}")
                log.info(f"  STEP 1: Received Ciphertext (Hex snippet):")
                log.info(f"          {ciphertext.hex()[:64]}...")
                log.info(f"  STEP 2: Verifying HMAC-SHA256 Signature...")

            # 1. Verify HMAC integrity
            if not verify_hmac(ciphertext, received_mac, hmac_key):
                stats.hmac_failures += 1
                log.warning(f"HMAC verification FAILED from {addr} — packet discarded")
                continue

            if show_crypto:
                log.info(f"          ✓ HMAC Verified (Integrity & Authenticity OK)")
                log.info(f"  STEP 3: Decrypting with AES-256-CBC...")

            # 2. Decrypt
            plaintext = decrypt_aes256_cbc(ciphertext, aes_key)
            pkt       = json.loads(plaintext.decode("utf-8"))

            if show_crypto:
                # Create a snippet for display
                display_pkt = pkt.copy()
                display_pkt["ecg_signal"] = display_pkt["ecg_signal"][:5]
                log.info(f"  STEP 4: Recovered Plaintext (JSON snippet):")
                log.info(f"          {json.dumps(display_pkt)}...")
                log.info("═"*60 + "\n")

            ecg_features = np.array(pkt["ecg_signal"], dtype=np.float32)
            beat_id      = pkt["beat_id"]
            true_label   = pkt["true_label"]
            timestamp    = pkt["timestamp"]

            # 3. ML Inference (Isolation Forest)
            result = classify_beat(ecg_features, model, scaler, pca)
            is_anomaly   = result["is_anomaly"]
            inference_ms = result["inference_ms"]
            anomaly_score = result["anomaly_score"]

            # 4. Routing decision
            forwarded = False
            if is_anomaly:
                # CRITICAL PATH: Forward immediately to cloud
                alert = {
                    "beat_id":       beat_id,
                    "timestamp":     timestamp,
                    "fog_timestamp": time.time(),
                    "device_id":     pkt.get("device_id", "EDGE_001"),
                    "true_label":    true_label,
                    "label_name":    LABEL_MAP.get(true_label, "Unknown"),
                    "anomaly_score": anomaly_score,
                    "inference_ms":  inference_ms,
                    "alert_type":    "CARDIAC_ANOMALY"
                }
                forwarded = forward_to_cloud(alert, cloud_host, cloud_port)
                fw_str = "→ CLOUD" if forwarded else "→ CLOUD FAILED"
                log.warning(
                    f"⚠ ANOMALY | Beat #{beat_id} | "
                    f"Label: {true_label} ({LABEL_MAP.get(true_label,'?')}) | "
                    f"Score: {anomaly_score:.4f} | "
                    f"Latency: {inference_ms:.2f}ms | {fw_str}"
                )
            else:
                # NORMAL PATH: Log locally only, do NOT forward (bandwidth saving)
                if beat_count % 20 == 0:
                    log.info(
                        f"✓ NORMAL | Beat #{beat_id} | "
                        f"Score: {anomaly_score:.4f} | "
                        f"Latency: {inference_ms:.2f}ms | [Filtered — not forwarded]"
                    )

            stats.record(is_anomaly, forwarded, inference_ms)
            beat_count += 1

            # Print stats summary every 100 beats
            if beat_count % 100 == 0:
                r = stats.report()
                log.info(
                    f"[STATS] Processed: {r['total_beats']} | "
                    f"Anomalies: {r['anomaly_beats']} | "
                    f"Bandwidth saved: {r['bandwidth_saved_pct']}% | "
                    f"Avg inference: {r['avg_inference_ms']}ms"
                )

    except ConnectionError:
        log.info(f"Edge node disconnected: {addr}")
    except Exception as e:
        log.error(f"Error handling client {addr}: {e}", exc_info=True)
    finally:
        conn.close()


# ─────────────────────────────────────────────
#  Stats API (HTTP endpoint for dashboard)
# ─────────────────────────────────────────────
def run_stats_server(stats: FogStats, port=9001):
    """Minimal HTTP server to expose fog stats to the dashboard."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class StatsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/stats", "/stats/"):
                data = json.dumps(stats.report()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass  # Suppress HTTP access logs

    server = HTTPServer(("0.0.0.0", port), StatsHandler)
    log.info(f"Fog stats API: http://0.0.0.0:{port}/stats")
    server.serve_forever()


# ─────────────────────────────────────────────
#  Main Fog Gateway
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fog Gateway — ECG Anomaly Detection")
    parser.add_argument("--fog_host",   default=FOG_HOST)
    parser.add_argument("--fog_port",   type=int, default=FOG_PORT)
    parser.add_argument("--cloud_host", default=CLOUD_HOST)
    parser.add_argument("--cloud_port", type=int, default=CLOUD_PORT)
    parser.add_argument("--stats_port", type=int, default=9001)
    parser.add_argument("--show-crypto", action="store_true",
                        help="Show decryption and HMAC steps (Security Demo)")
    args = parser.parse_args()

    log.info("═══════════════════════════════════════════════")
    log.info("  FOG GATEWAY NODE STARTED")
    log.info("  Secure Fog Computing — Cardiac Monitoring")
    log.info("  Model: Isolation Forest (TinyML-Ready)")
    log.info(f"  Listening: {args.fog_host}:{args.fog_port}")
    log.info(f"  Cloud target: {args.cloud_host}:{args.cloud_port}")
    log.info("═══════════════════════════════════════════════")

    # Load keys and model
    aes_key, hmac_key = load_crypto_keys()
    model, scaler, pca = load_ml_model()
    stats = FogStats()

    # Start stats API in background thread
    stats_thread = threading.Thread(
        target=run_stats_server, args=(stats, args.stats_port), daemon=True)
    stats_thread.start()

    # Start main TCP listener
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((args.fog_host, args.fog_port))
    server_sock.listen(10)
    log.info(f"Fog Gateway ready — awaiting edge nodes...")

    try:
        while True:
            conn, addr = server_sock.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(conn, addr, aes_key, hmac_key, model, scaler, pca,
                       stats, args.cloud_host, args.cloud_port, args.show_crypto),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        log.info("Fog Gateway shutting down.")
    finally:
        server_sock.close()
        r = stats.report()
        log.info(f"Final stats: {json.dumps(r, indent=2)}")


if __name__ == "__main__":
    main()
