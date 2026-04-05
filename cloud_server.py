"""
cloud_server.py — DA-2: Cloud Layer (Storage & API)
====================================================
Lightweight cloud server (runs locally for simulation).
  - Receives anomaly alerts forwarded by the Fog Gateway
  - Stores all records in SQLite (simulates cloud database)
  - Exposes REST API consumed by the monitoring dashboard
  - Provides historical analytics

In production: replace with AWS/Azure IoT Hub + TimescaleDB

Usage:
    python cloud_server.py --port 8080
"""

import argparse
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────
DB_PATH  = "logs/cloud_alerts.db"
LOG_FILE = "logs/cloud_server.log"
PORT     = 8080

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CLOUD] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

LABEL_MAP = {0: "Normal", 1: "Supraventricular", 2: "PVC",
             3: "Fusion", 4: "Unclassifiable"}

# ─────────────────────────────────────────────
#  Database (SQLite simulates cloud DB)
# ─────────────────────────────────────────────
db_lock = threading.Lock()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                beat_id      INTEGER,
                edge_ts      REAL,
                fog_ts       REAL,
                cloud_ts     REAL DEFAULT (strftime('%s','now')),
                device_id    TEXT,
                true_label   INTEGER,
                label_name   TEXT,
                anomaly_score REAL,
                inference_ms REAL,
                alert_type   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_stats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    REAL,
                total_alerts INTEGER,
                label_counts TEXT
            )
        """)
        conn.commit()
    log.info(f"Database initialised: {DB_PATH}")


def insert_alert(data: dict):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO alerts
                  (beat_id, edge_ts, fog_ts, cloud_ts, device_id,
                   true_label, label_name, anomaly_score, inference_ms, alert_type)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("beat_id"),
                data.get("timestamp"),
                data.get("fog_timestamp"),
                time.time(),
                data.get("device_id", "EDGE_001"),
                data.get("true_label", -1),
                data.get("label_name", "Unknown"),
                data.get("anomaly_score"),
                data.get("inference_ms"),
                data.get("alert_type", "CARDIAC_ANOMALY")
            ))
            conn.commit()


def get_recent_alerts(limit=50):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM alerts ORDER BY cloud_ts DESC LIMIT ?
            """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_summary_stats():
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            label_counts_raw = conn.execute(
                "SELECT true_label, label_name, COUNT(*) as cnt FROM alerts GROUP BY true_label"
            ).fetchall()
            recent_10 = conn.execute(
                "SELECT AVG(inference_ms) FROM (SELECT inference_ms FROM alerts ORDER BY id DESC LIMIT 10)"
            ).fetchone()[0]
            hourly = conn.execute("""
                SELECT strftime('%H:%M', datetime(cloud_ts, 'unixepoch')) as minute,
                       COUNT(*) as count
                FROM alerts
                WHERE cloud_ts > strftime('%s','now') - 3600
                GROUP BY minute ORDER BY minute
            """).fetchall()

    label_counts = {
        row[1]: row[2] for row in label_counts_raw
    }
    hourly_data = [{"time": r[0], "count": r[1]} for r in hourly]

    return {
        "total_alerts":      total,
        "label_distribution": label_counts,
        "avg_inference_ms":  round(recent_10 or 0, 3),
        "hourly_trend":      hourly_data,
        "db_path":           DB_PATH
    }


# ─────────────────────────────────────────────
#  HTTP Request Handler
# ─────────────────────────────────────────────
class CloudHandler(BaseHTTPRequestHandler):

    def _send_json(self, data: dict, status=200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Source")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Source")
        self.end_headers()

    def do_POST(self):
        if self.path == "/alert":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length)
                data   = json.loads(body.decode("utf-8"))
                insert_alert(data)
                label_name = data.get("label_name", "?")
                score      = data.get("anomaly_score", 0)
                ms         = data.get("inference_ms", 0)
                log.warning(
                    f"[ALERT] stored | Beat #{data.get('beat_id')} | "
                    f"{label_name} | Score: {score:.4f} | {ms:.2f}ms"
                )
                self._send_json({"status": "ok"})
            except Exception as e:
                log.error(f"POST /alert error: {e}")
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        if path == "" or path == "/dashboard":
            # Serve dashboard HTML file if it exists
            if os.path.exists("dashboard.html"):
                with open("dashboard.html", "r") as f:
                    self._send_html(f.read())
            else:
                self._send_json({"message": "Place dashboard.html in the project root"})

        elif path == "/api/alerts":
            qs = parse_qs(parsed.query)
            limit = int(qs.get("limit", ["50"])[0])
            self._send_json(get_recent_alerts(limit))

        elif path == "/api/stats":
            self._send_json(get_summary_stats())

        elif path == "/api/health":
            self._send_json({"status": "ok", "timestamp": time.time(),
                              "node": "CLOUD_SERVER_001"})

        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, fmt, *args):
        # Only log errors and important requests
        if "POST" in fmt % args:
            pass  # Already logged above
        elif "200" not in (fmt % args):
            log.info(fmt % args)


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Cloud Server — Alert Storage & Dashboard API")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    init_db()

    log.info("===============================================")
    log.info("  CLOUD SERVER STARTED")
    log.info(f"  Listening: http://0.0.0.0:{args.port}")
    log.info(f"  Dashboard: http://127.0.0.1:{args.port}/dashboard")
    log.info(f"  API:       http://127.0.0.1:{args.port}/api/stats")
    log.info("  Waiting for anomaly alerts from Fog Gateway...")
    log.info("===============================================")

    server = HTTPServer(("0.0.0.0", args.port), CloudHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Cloud server shutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
