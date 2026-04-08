"""
multi_edge_sim.py — Enterprise-Scale Load Tester for DA-3
===========================================================
Spawns N independent EdgeSensor processes using Python's 'multiprocessing'
library to bypass the GIL. Simulates a massive hospital ward with hundreds
of concurrent patients streaming to the Fog Gateway.

BUG FIXED IN THIS VERSION:
  [FIX] Unintended show_crypto on first sensor: The original logic was:
            show = True if args.show_crypto else (i == 0)
        This always forced show_crypto=True for the FIRST spawned node even
        when the user never passed --show-crypto. That node would flood the
        terminal with full hex traces of every AES operation regardless of
        user intent. Fixed to:
            show = args.show_crypto
        Now crypto traces are shown for ALL nodes only when --show-crypto is
        explicitly requested, and for NO nodes otherwise.
"""

import argparse
import time
import sys
import os
import random
import signal
import multiprocessing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from edge_sensor import EdgeSensorNode

# ANSI colour codes
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"


def run_sensor(device_id, bpm, is_critical, data_path, fog_host, fog_port,
               max_beats, show_crypto):
    """Process target: create and run one independent EdgeSensorNode."""
    # Introduce realistic +/- 5 BPM jitter
    actual_bpm = bpm + random.randint(-5, 5)
    actual_bpm = max(40, actual_bpm)  # clamp to a physiologically valid range

    try:
        node = EdgeSensorNode(
            data_path    = data_path,
            fog_host     = fog_host,
            fog_port     = fog_port,
            bpm          = actual_bpm,
            max_beats    = max_beats,
            device_id    = device_id,
            show_crypto  = show_crypto,
            anomaly_only = is_critical
        )
        node.run()
    except ConnectionRefusedError:
        print(f"\n[{RED}ERROR{RESET}] {device_id} could not connect. Is Fog Gateway running?")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n[{RED}SIM ERROR{RESET}] {device_id} crashed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Massive Scale ECG Simulation — DA-3")
    parser.add_argument("-n", "--num_sensors", type=int, default=10,
                        help="Number of concurrent sensor nodes to spawn")
    parser.add_argument("--fog_host",   default="127.0.0.1")
    parser.add_argument("--fog_port",   type=int, default=9000)
    parser.add_argument("--data_path",  default="data/")
    parser.add_argument("--max_beats",  type=int, default=500)
    parser.add_argument("--stagger_ms", type=int, default=150,
                        help="Milliseconds to wait between booting sensors")
    parser.add_argument("--bpm", type=int, default=None,
                        help="Base heart rate. If not set, randomizes 60-100 per node")
    parser.add_argument("--show-crypto", action="store_true",
                        help="Print AES encryption steps for ALL nodes (Warning: very spammy)")
    parser.add_argument("--anomaly_only", action="store_true",
                        help="Force ALL spawned sensors into anomaly_only mode")
    args = parser.parse_args()

    print(f"{CYAN}{BOLD}═" * 65)
    print("  MASSIVE FOG STRESS TEST INITIATED")
    print("═" * 65 + f"{RESET}")
    print(f"  Target        : {BOLD}{args.fog_host}:{args.fog_port}{RESET}")
    print(f"  Total Sensors : {BOLD}{args.num_sensors}{RESET} (Independent Processes)")
    print(f"  Stagger Delay : {args.stagger_ms}ms")
    print(f"  Anomaly Mode  : {BOLD}{'ENABLED (All Critical)' if args.anomaly_only else 'Standard (15% Critical)'}{RESET}")
    print(f"{CYAN}═" * 65 + f"{RESET}")

    # Generate patients
    patients = []
    critical_count = 0
    for i in range(args.num_sensors):
        patient_id = f"EDGE_NODE_{str(i + 1).zfill(3)}"
        base_bpm   = args.bpm if args.bpm else random.randint(60, 100)
        is_critical = True if args.anomaly_only else (random.random() < 0.15)
        if is_critical:
            critical_count += 1
        patients.append({"id": patient_id, "bpm": base_bpm, "critical": is_critical})

    # Spawn processes
    processes = []

    # Temporarily ignore SIGINT in parent so we can handle it manually
    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    for i, p in enumerate(patients):
        # FIX: Original was `show = True if args.show_crypto else (i == 0)`
        # which silently forced the first node to always dump crypto traces.
        # Now we only show crypto when the user explicitly asked for it.
        show = args.show_crypto

        proc = multiprocessing.Process(
            target=run_sensor,
            args=(p["id"], p["bpm"], p["critical"], args.data_path,
                  args.fog_host, args.fog_port, args.max_beats, show),
            name=p["id"]
        )
        processes.append(proc)
        proc.start()

        status = (f"{RED}{BOLD}[CRITICAL]{RESET}" if p["critical"]
                  else f"{GREEN}[NORMAL]{RESET}")
        print(f"[{YELLOW}BOOT{RESET}] Spawned {p['id']} | {p['bpm']} BPM | {status} | PID: {proc.pid}")
        time.sleep(args.stagger_ms / 1000.0)

    signal.signal(signal.SIGINT, original_sigint)

    print(f"\n{GREEN}{BOLD}[LIVE] All {args.num_sensors} sensors streaming concurrently.{RESET}")
    print(f"         Expecting ~{critical_count} automated alarms on ThingsBoard.")
    print(f"         Press {BOLD}Ctrl+C{RESET} to safely terminate the cluster.\n")

    try:
        for proc in processes:
            proc.join()
    except KeyboardInterrupt:
        print(f"\n{RED}{BOLD}[HALT] Emergency shutdown initiated... killing cluster.{RESET}")
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join()
        print(f"{GREEN}[OK] All edge processes safely terminated.{RESET}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
