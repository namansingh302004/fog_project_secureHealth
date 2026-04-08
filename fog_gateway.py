"""
fog_gateway.py — DA-3: Fog Intelligence Layer (UPDATED)
========================================================
The Fog Node sits between Edge sensors and the Cloud server.
  - Performs Diffie-Hellman key exchange per client connection
    (replaces static pre-shared key files — DA-2 vulnerability fixed)
  - Decrypts with pure-Python AES-256-CBC (no pip crypto deps)
  - Verifies HMAC-SHA256 integrity before ML inference
  - Runs Isolation Forest for real-time anomaly detection (<100ms)
  - Normal beats → logged locally only (bandwidth saving ~90%)
  - Anomalies   → forwarded immediately to Cloud via HTTP POST
  - Handles multiple simultaneous sensor connections (multi-sensor)
  - Tracks per-device statistics for the monitoring dashboard

Improvements over DA-2:
  [+] DH handshake — fresh session keys per connection, no .bin files
  [+] Pure-Python AES — library-free decryption path
  [+] Per-device tracking — separate stats for each sensor node
  [+] Multi-sensor support — concurrent clients with isolated keys

TinyML Target: Raspberry Pi 4 / RPi Zero 2W
Inference latency target: < 100ms per beat

Usage:
    python fog_gateway.py
    python fog_gateway.py --cloud_host 127.0.0.1 --cloud_port 8080
    python fog_gateway.py --show-crypto
"""

import argparse
import json
import logging
import os
import pickle
import socket
import struct
import threading
import time
from dotenv import load_dotenv
from collections import deque
from datetime import UTC, datetime
from http.client import HTTPConnection


import paho.mqtt.client as mqtt #Thingsboard

load_dotenv()

# --- ThingsBoard MQTT Configuration ---
THINGSBOARD_HOST = "mqtt.thingsboard.cloud" # Or your specific host
ACCESS_TOKEN = os.environ.get("THINGSBOARD_TOKEN")
if not ACCESS_TOKEN:
    print("[ERROR] THINGSBOARD_TOKEN not found in .env file!")
    exit(1)
TELEMETRY_TOPIC = "v1/devices/me/telemetry"

# Global MQTT Client
tb_client = None

import numpy as np

# ── DA-3 modules ─────────────────────────────────────────────────
from pure_aes import aes256_cbc_decrypt, hmac_verify
from dh_key_exchange import fog_perform_handshake

# ─────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────
FOG_HOST   = "0.0.0.0"
FOG_PORT   = 9000
CLOUD_HOST = "127.0.0.1"
CLOUD_PORT = 8080
MODEL_DIR  = "model/"
LOG_FILE   = "logs/fog_gateway.log"

LABEL_MAP      = {0: "Normal", 1: "Supraventricular", 2: "PVC",
                  3: "Fusion", 4: "Unclassifiable"}
CRITICAL_LABELS = {1, 2, 3, 4}

# ─────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────
#  Model Loading
# ─────────────────────────────────────────────────────────────────

def load_ml_model():
    """Load the Isolation Forest pipeline (model + scaler + PCA)."""
    required = ["isolation_forest.pkl", "scaler.pkl", "pca.pkl"]
    for r in required:
        path = os.path.join(MODEL_DIR, r)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{r} not found. Run train_model.py first.")

    with open(os.path.join(MODEL_DIR, "isolation_forest.pkl"), "rb") as f:
        model = pickle.load(f)
    with open(os.path.join(MODEL_DIR, "scaler.pkl"), "rb") as f:
        scaler = pickle.load(f)
    with open(os.path.join(MODEL_DIR, "pca.pkl"), "rb") as f:
        pca = pickle.load(f)

    log.info("ML pipeline loaded: IsolationForest + StandardScaler + PCA")
    return model, scaler, pca


# ─────────────────────────────────────────────────────────────────
#  Socket Helper
# ─────────────────────────────────────────────────────────────────

def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Socket closed mid-read")
        data += chunk
    return data


# ─────────────────────────────────────────────────────────────────
#  ML Inference
# ─────────────────────────────────────────────────────────────────

def classify_beat(ecg_features: np.ndarray, model, scaler, pca) -> dict:
    """
    Run the Isolation Forest pipeline on a single ECG beat.
    Returns classification result dict with timing in ms.
    """
    t_start = time.perf_counter()
    X = ecg_features.reshape(1, -1)
    X_scaled  = scaler.transform(X)
    X_pca     = pca.transform(X_scaled)
    prediction    = model.predict(X_pca)[0]       # +1 normal, -1 anomaly
    anomaly_score = model.decision_function(X_pca)[0]
    elapsed_ms    = (time.perf_counter() - t_start) * 1000

    return {
        "is_anomaly":    prediction == -1,
        "anomaly_score": float(anomaly_score),
        "inference_ms":  round(elapsed_ms, 3),
    }


# ─────────────────────────────────────────────────────────────────
#  Cloud Forwarding
# ─────────────────────────────────────────────────────────────────

def forward_to_cloud(alert: dict, cloud_host: str, cloud_port: int) -> bool:
    """HTTP POST an anomaly alert to the cloud server."""
    try:
        body = json.dumps(alert).encode("utf-8")
        conn = HTTPConnection(cloud_host, cloud_port, timeout=5)
        conn.request("POST", "/alert", body=body,
                     headers={"Content-Type": "application/json",
                               "X-Source": "FOG_GATEWAY_001"})
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception as e:
        log.warning(f"Cloud forwarding failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────
#  Statistics Tracker (global + per-device)
# ─────────────────────────────────────────────────────────────────

class FogStats:
    def __init__(self):
        self._lock       = threading.Lock()
        self.total       = 0
        self.normal      = 0
        self.anomaly     = 0
        self.forwarded   = 0
        self.hmac_fails  = 0
        self._latencies  = []
        self.start_time  = time.time()
        self.devices: dict = {}   # device_id → per-device counter dict
        self.recent_signals = deque(maxlen=8)

    def record(self, device_id: str, is_anomaly: bool,
               forwarded: bool, latency_ms: float):
        with self._lock:
            self.total += 1
            if is_anomaly:
                self.anomaly += 1
            else:
                self.normal += 1
            if forwarded:
                self.forwarded += 1
            self._latencies.append(latency_ms)

            # Per-device tracking
            if device_id not in self.devices:
                self.devices[device_id] = {
                    "total": 0, "normal": 0, "anomaly": 0,
                    "forwarded": 0, "first_seen": datetime.now(UTC).isoformat()
                }
            d = self.devices[device_id]
            d["total"] += 1
            d["anomaly" if is_anomaly else "normal"] += 1
            if forwarded:
                d["forwarded"] += 1

    def record_waveform(self, device_id: str, beat_id: int, true_label: int,
                        anomaly_score: float, inference_ms: float,
                        ecg_signal: list[float]):
        with self._lock:
            self.recent_signals.append({
                "device_id": device_id,
                "beat_id": beat_id,
                "true_label": true_label,
                "label_name": LABEL_MAP.get(true_label, "Unknown"),
                "anomaly_score": round(float(anomaly_score), 6),
                "inference_ms": round(float(inference_ms), 3),
                "captured_at": time.time(),
                "signal": [float(value) for value in ecg_signal],
            })

    def report(self) -> dict:
        with self._lock:
            uptime  = time.time() - self.start_time
            avg_lat = (sum(self._latencies[-100:]) /
                       max(len(self._latencies[-100:]), 1))
            bw_saved = (self.normal / max(self.total, 1)) * 100
            recent_signals = list(self.recent_signals)
            return {
                "uptime_s":           round(uptime, 1),
                "total_beats":        self.total,
                "normal_beats":       self.normal,
                "anomaly_beats":      self.anomaly,
                "forwarded_to_cloud": self.forwarded,
                "bandwidth_saved_pct": round(bw_saved, 1),
                "hmac_failures":      self.hmac_fails,
                "avg_inference_ms":   round(avg_lat, 3),
                "active_devices":     len(self.devices),
                "per_device_stats":   dict(self.devices),
                "beats_per_sec":      round(self.total / max(uptime, 1), 2),
                "latest_signal":      recent_signals[-1] if recent_signals else None,
                "recent_signals":     recent_signals,
            }


# ─────────────────────────────────────────────────────────────────
#  Client Handler (one thread per edge connection)
# ─────────────────────────────────────────────────────────────────

def handle_client(conn, addr, model, scaler, pca,
                  stats: FogStats, cloud_host: str, cloud_port: int,
                  show_crypto: bool = False):
    """
    Handle a single edge sensor connection:
      1. DH key exchange → derive session AES + HMAC keys
      2. Receive encrypted packets in a loop
      3. Verify HMAC → Decrypt → ML inference → Route
    """
    log.info(f"Edge node connected: {addr}")
    beat_count = 0

    try:
        # ── STEP 1: DH Handshake — derive session keys ──────────
        log.info(f"[DH] Starting handshake with {addr}...")
        aes_key, hmac_key = fog_perform_handshake(conn, addr)
        log.info(f"[DH] Session keys established for {addr}")

        # ── STEP 2: Packet processing loop ──────────────────────
        while True:
            # Read 4-byte length prefix
            raw_len     = recv_exact(conn, 4)
            payload_len = struct.unpack(">I", raw_len)[0]
            wire        = recv_exact(conn, payload_len)

            received_mac  = wire[:32]
            iv_ciphertext = wire[32:]

            if show_crypto:
                log.info("\n" + "═" * 62)
                log.info(f"  [PURE-AES DECRYPTION] Packet from {addr}")
                log.info(f"  STEP 1 — Received ciphertext (first 32 bytes hex):")
                log.info(f"           {iv_ciphertext.hex()[:64]}...")
                log.info(f"  STEP 2 — Verifying HMAC-SHA256...")

            # ── Verify HMAC (pure Python, constant-time) ────────
            if not hmac_verify(hmac_key, iv_ciphertext, received_mac):
                stats.hmac_fails += 1
                log.warning(f"[WARN] HMAC FAILED from {addr} - packet discarded (possible tampering)")
                continue

            if show_crypto:
                log.info("           HMAC verified (integrity OK)")
                log.info(f"  STEP 3 — Decrypting with pure-Python AES-256-CBC...")

            # ── Decrypt (pure Python AES) ────────────────────────
            try:
                plaintext = aes256_cbc_decrypt(iv_ciphertext, aes_key)
            except ValueError as e:
                log.error(f"Decryption error from {addr}: {e}")
                continue

            pkt = json.loads(plaintext.decode("utf-8"))

            if show_crypto:
                display = pkt.copy()
                display["ecg_signal"] = display["ecg_signal"][:5]
                log.info(f"  STEP 4 — Recovered plaintext (truncated):")
                log.info(f"           {json.dumps(display)}...")
                log.info(f"  STEP 5 — Keys derived from DH (never stored on disk)")
                log.info("═" * 62 + "\n")

            ecg_features = np.array(pkt["ecg_signal"], dtype=np.float32)
            beat_id      = pkt["beat_id"]
            true_label   = pkt.get("true_label", -1)
            device_id    = pkt.get("device_id", str(addr))

            # ── ML Inference ─────────────────────────────────────
            result       = classify_beat(ecg_features, model, scaler, pca)
            
            # REAL WORLD: We rely entirely on the ML model's prediction
            model_is_anomaly = bool(result["is_anomaly"])
            inference_ms = result["inference_ms"]
            score        = result["anomaly_score"]
            
            # Extract true label ONLY for your local terminal debugging. 
            # We will NOT use this to route the packet or label the cloud alert.
            true_label   = pkt.get("true_label", -1) 

            # ── Routing Decision ─────────────────────────────────
            forwarded = False
            
            # REAL WORLD ROUTING: Alert triggers ONLY if the model flags it
            if model_is_anomaly: 
                alert = {
                    "beat_id":       beat_id,
                    "timestamp":     pkt.get("timestamp", time.time()),
                    "fog_timestamp": time.time(),
                    "device_id":     device_id,
                    "anomaly_score": score,
                    "inference_ms":  inference_ms,
                    "alert_type":    "CARDIAC_ANOMALY"
                }
                forwarded = forward_to_cloud(alert, cloud_host, cloud_port)
                fw_str = "TO CLOUD OK" if forwarded else "TO CLOUD FAILED"

                # THINGSBOARD: Send a realistic, unclassified alert
                tb_alert = {
                    "critical_alert": True,
                    "anomaly_score": float(score),
                    "label": "Potential Arrhythmia Detected", # Cannot specify exact PVC/Fusion etc.
                    "device_id": device_id
                }
                publish_telemetry(tb_alert)

                # Local terminal log (keeps true label so you can verify if the model was right)
                log.warning(
                    f"[ALERT] [{device_id}] Beat #{beat_id} | "
                    f"Ground Truth: {LABEL_MAP.get(true_label,'?')} | "
                    f"Score: {score:.4f} | "
                    f"{inference_ms:.2f}ms | {fw_str}"
                )
            else:
                if beat_count % 20 == 0:
                    log.info(
                        f"[NORMAL] [{device_id}] Beat #{beat_id} | "
                        f"Score: {score:.4f} | {inference_ms:.2f}ms | [filtered]"
                    )

            stats.record_waveform(
                device_id=device_id,
                beat_id=beat_id,
                true_label=true_label,
                anomaly_score=score,
                inference_ms=inference_ms,
                ecg_signal=ecg_features.tolist(),
            )
            # Record the stats using the model's unclassified decision
            stats.record(device_id, model_is_anomaly, forwarded, inference_ms)
            beat_count += 1

            if beat_count % 100 == 0:
                r = stats.report()
                log.info(
                    f"[STATS] Devices: {r['active_devices']} | "
                    f"Total: {r['total_beats']} | Anomalies: {r['anomaly_beats']} | "
                    f"BW saved: {r['bandwidth_saved_pct']}% | "
                    f"Avg latency: {r['avg_inference_ms']}ms"
                )

    except ConnectionError:
        log.info(f"Edge node disconnected: {addr}")
    except Exception as e:
        log.error(f"Error handling {addr}: {e}", exc_info=True)
    finally:
        conn.close()
        log.info(f"Connection closed: {addr} | processed {beat_count} beats")


# ─────────────────────────────────────────────────────────────────
#  Fog Stats HTTP API (for dashboard)
# ─────────────────────────────────────────────────────────────────

def run_stats_server(stats: FogStats, port: int = 9001):
    """Expose fog stats as a JSON API on port 9001."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse

    class StatsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/")
            if path in ("/stats", ""):
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
            pass  # suppress HTTP logs

    server = HTTPServer(("0.0.0.0", port), StatsHandler)
    log.info(f"Fog stats API: http://0.0.0.0:{port}/stats")
    server.serve_forever()

# ─────────────────────────────────────────────────────────────────
#  ThingsBoard
# ─────────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Successfully connected to ThingsBoard!")
    else:
        print(f"[MQTT] Connection failed with code {rc}")

def setup_mqtt():
    global tb_client
    tb_client = mqtt.Client()
    # ThingsBoard uses the Access Token as the MQTT username
    tb_client.username_pw_set(ACCESS_TOKEN) 
    tb_client.on_connect = on_connect
    
    try:
        # Port 1883 is standard MQTT. (Use 8883 for TLS/SSL if you want extra security points)
        tb_client.connect(THINGSBOARD_HOST, 1883, 60)
        # Starts a background thread to handle network traffic and pinging
        tb_client.loop_start() 
    except Exception as e:
        print(f"[MQTT] Failed to connect: {e}")

def publish_telemetry(data: dict):
    """Pushes a JSON payload to ThingsBoard via MQTT."""
    if tb_client and tb_client.is_connected():
        tb_client.publish(TELEMETRY_TOPIC, json.dumps(data), qos=1)


def run_tb_telemetry_loop(stats):
    """Background thread to push aggregate gateway stats to ThingsBoard."""
    while True:
        # Pull the latest calculated metrics from your existing FogStats class
        r = stats.report() 
        
        tb_stats = {
            "uptime_seconds": r["uptime_s"],
            "bandwidth_saved_pct": r["bandwidth_saved_pct"],
            "avg_inference_ms": r["avg_inference_ms"],
            "active_edge_nodes": r["active_devices"],
            "total_beats_processed": r["total_beats"]
        }
        publish_telemetry(tb_stats)
        time.sleep(5)  # Push every 5 seconds
# ─────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fog Gateway — DA-3 (DH Key Exchange + Pure-AES + Multi-Sensor)")
    parser.add_argument("--fog_host",    default=FOG_HOST)
    parser.add_argument("--fog_port",    type=int, default=FOG_PORT)
    parser.add_argument("--cloud_host",  default=CLOUD_HOST)
    parser.add_argument("--cloud_port",  type=int, default=CLOUD_PORT)
    parser.add_argument("--stats_port",  type=int, default=9001)
    parser.add_argument("--show-crypto", action="store_true",
                        help="Print decryption steps per packet (demo mode)")
    args = parser.parse_args()

    log.info("===================================================")
    log.info("  FOG GATEWAY NODE STARTED (DA-3)")
    log.info("  Key Exchange : Diffie-Hellman (RFC 3526 Group 14)")
    log.info("  Encryption   : Pure-Python AES-256-CBC (no deps)")
    log.info("  Integrity    : HMAC-SHA256")
    log.info("  Model        : Isolation Forest (TinyML-Ready)")
    log.info(f"  Listening    : {args.fog_host}:{args.fog_port}")
    log.info(f"  Cloud target : {args.cloud_host}:{args.cloud_port}")
    log.info("===================================================")

    model, scaler, pca = load_ml_model()
    stats = FogStats()

# --- START MQTT & THINGSBOARD TELEMETRY ---
    setup_mqtt()
    threading.Thread(
        target=run_tb_telemetry_loop, args=(stats,), daemon=True
    ).start()

    # Stats API in background thread (Your existing React Cloud API)
    threading.Thread(
        target=run_stats_server, args=(stats, args.stats_port), daemon=True
    ).start()

    
    # Main TCP listener
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((args.fog_host, args.fog_port))
    server_sock.listen(20)
    log.info("Fog Gateway ready — awaiting edge nodes (multi-sensor enabled)...")

    try:
        while True:
            conn, addr = server_sock.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, addr, model, scaler, pca, stats,
                      args.cloud_host, args.cloud_port, args.show_crypto),
                daemon=True
            ).start()
    except KeyboardInterrupt:
        log.info("Fog Gateway shutting down.")
    finally:
        server_sock.close()
        log.info(f"Final stats:\n{json.dumps(stats.report(), indent=2)}")



if __name__ == "__main__":
    main()
