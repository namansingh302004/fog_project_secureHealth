"""
multi_edge_sim.py — Multi-Sensor Fog Gateway Simulation
=========================================================
Spawns multiple EdgeSensorNode instances simultaneously, each with a
unique device ID, to demonstrate the Fog Gateway's ability to handle
concurrent sensor streams independently.

Each sensor node:
  - Performs its OWN Diffie-Hellman handshake (separate session keys)
  - Streams ECG data concurrently on separate threads
  - Is tracked separately in the Fog Gateway's per-device statistics

This addresses the DA-2 limitation:
  "Fog Gateway handles only a single TCP stream rather than
   concurrent streams from multiple patient sensors."

Usage:
    # Run fog_gateway.py first, then:
    python multi_edge_sim.py
    python multi_edge_sim.py --num_sensors 4 --bpm 120 --max_beats 100
    python multi_edge_sim.py --show-crypto

Architecture demonstrated:
    EDGE_NODE_001 ──┐
    EDGE_NODE_002 ──┤──► FOG_GATEWAY (port 9000) ──► CLOUD (port 8080)
    EDGE_NODE_003 ──┘
    (each with independent DH session keys)
"""

import argparse
import threading
import time
import sys
import os

# Ensure we can import from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from edge_sensor import EdgeSensorNode

# ─────────────────────────────────────────────────────────────────
#  Sensor Configuration
#  Each sensor gets a unique device ID, BPM, and data offset
#  to simulate different real-world patients.
# ─────────────────────────────────────────────────────────────────
DEFAULT_SENSOR_PROFILES = [
    {
        "device_id": "EDGE_NODE_001",
        "bpm":       60,
        "label":     "Patient A (Resting, 60 BPM)"
    },
    {
        "device_id": "EDGE_NODE_002",
        "bpm":       80,
        "label":     "Patient B (Active, 80 BPM)"
    },
    {
        "device_id": "EDGE_NODE_003",
        "bpm":       100,
        "label":     "Patient C (Elevated, 100 BPM)"
    },
]


def run_sensor(profile: dict, data_path: str, fog_host: str, fog_port: int,
               max_beats: int, show_crypto: bool):
    """Thread target: create and run one EdgeSensorNode."""
    print(f"\n[SIM] Starting {profile['device_id']} — {profile['label']}")
    node = EdgeSensorNode(
        data_path  = data_path,
        fog_host   = fog_host,
        fog_port   = fog_port,
        bpm        = profile["bpm"],
        max_beats  = max_beats,
        device_id  = profile["device_id"],
        show_crypto= show_crypto,
    )
    node.run()


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Sensor ECG Simulation — DA-3 Scalability Demo"
    )
    parser.add_argument("--num_sensors", type=int, default=3,
                        help="Number of concurrent sensor nodes (default: 3)")
    parser.add_argument("--fog_host",    default="127.0.0.1")
    parser.add_argument("--fog_port",    type=int, default=9000)
    parser.add_argument("--data_path",  default="data/")
    parser.add_argument("--bpm",        type=int, default=None,
                        help="Override BPM for all sensors")
    parser.add_argument("--max_beats",  type=int, default=200,
                        help="Max beats per sensor (default: 200)")
    parser.add_argument("--show-crypto", action="store_true",
                        help="Show encryption steps for sensor 001 only")
    parser.add_argument("--stagger_s",  type=float, default=1.0,
                        help="Seconds to wait between starting each sensor (default: 1.0)")
    args = parser.parse_args()

    num = min(args.num_sensors, 10)
    profiles = []
    for i in range(num):
        if i < len(DEFAULT_SENSOR_PROFILES):
            p = DEFAULT_SENSOR_PROFILES[i].copy()
        else:
            # Auto-generate profile for sensors beyond the default 3
            p = {
                "device_id": f"EDGE_NODE_{i+1:03d}",
                "bpm":       60 + i * 10,
                "label":     f"Patient {chr(65+i)} ({60+i*10} BPM)",
            }
        if args.bpm:
            p["bpm"] = args.bpm
        profiles.append(p)

    print("═" * 65)
    print("  MULTI-SENSOR FOG GATEWAY SIMULATION — DA-3")
    print("═" * 65)
    print(f"  Sensors to launch : {num}")
    print(f"  Fog target        : {args.fog_host}:{args.fog_port}")
    print(f"  Max beats/sensor  : {args.max_beats}")
    print(f"  Stagger delay     : {args.stagger_s}s between starts")
    print(f"  Key Exchange      : Diffie-Hellman (independent per sensor)")
    print("═" * 65)
    print()
    print("  Sensor profiles:")
    for p in profiles:
        print(f"    [{p['device_id']}] — {p['label']}")
    print()
    print("  NOTE: Each sensor performs its own DH handshake.")
    print("        The fog gateway derives separate session keys")
    print("        for every connection — demonstrating that key")
    print("        compromise of one node does NOT affect others.")
    print()

    threads = []
    for i, profile in enumerate(profiles):
        # Only show crypto for the first sensor to avoid log flooding
        show = args.show_crypto and i == 0
        t = threading.Thread(
            target=run_sensor,
            args=(profile, args.data_path, args.fog_host, args.fog_port,
                  args.max_beats, show),
            name=f"Sensor-{profile['device_id']}",
            daemon=True
        )
        threads.append(t)
        t.start()
        print(f"[SIM] ✓ Launched {profile['device_id']} (thread: {t.name})")
        if i < len(profiles) - 1:
            time.sleep(args.stagger_s)

    print(f"\n[SIM] All {num} sensors running. Press Ctrl+C to stop.\n")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[SIM] Multi-sensor simulation interrupted.")

    print("\n[SIM] ═══ SIMULATION COMPLETE ═══")
    print(f"[SIM] All {num} sensor threads finished.")
    print(f"[SIM] Check fog_gateway stats API: http://127.0.0.1:9001/stats")
    print(f"[SIM] Per-device breakdown available in 'per_device_stats' field.")


if __name__ == "__main__":
    main()
