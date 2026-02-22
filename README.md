# DA-2: ML / TinyML Model Design & Evaluation
## Secure Fog Computing System — ECG Anomaly Detection
**Course:** BCSE313L – Fundamentals of FOG and Edge Computing  
**Team:** Kiran Biju (23BCE1313) · Abel Dan Alex (23BCE1335) · Naman Kumar Singh (23BCE1354)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  EDGE LAYER (edge_sensor.py)                                    │
│  • Reads MIT-BIH ECG heartbeats (187 features @ 125Hz)         │
│  • AES-256-CBC encryption + HMAC-SHA256 integrity signing       │
│  • Sends encrypted packets → Fog Gateway (TCP :9000)           │
└────────────────────────────┬────────────────────────────────────┘
                             │ Encrypted ECG Packets
┌────────────────────────────▼────────────────────────────────────┐
│  FOG LAYER (fog_gateway.py)                                     │
│  • Decrypts + verifies HMAC integrity                           │
│  • Runs Isolation Forest inference (<10ms per beat)             │
│  ├─ Normal beat  → Log locally, DO NOT forward (90% BW saving) │
│  └─ Anomaly beat → Forward alert → Cloud (HTTP :8080)          │
└────────────────────────────┬────────────────────────────────────┘
                             │ Anomaly Alerts Only (~10% traffic)
┌────────────────────────────▼────────────────────────────────────┐
│  CLOUD LAYER (cloud_server.py)                                  │
│  • Stores alerts in SQLite                                      │
│  • REST API → Dashboard (HTTP :8080)                            │
│  • Historical analytics + doctor dashboard                      │
└─────────────────────────────────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  dashboard.html  │
                    │  Live monitor   │
                    └─────────────────┘
```

---

## ML Algorithm: Isolation Forest

### Why Isolation Forest?
| Property | Detail |
|----------|--------|
| **Type** | Unsupervised anomaly detection |
| **How it works** | Builds random trees; anomalies are isolated in fewer splits |
| **Training data** | Normal beats ONLY (labels 0) |
| **Output** | Anomaly score: lower = more anomalous |
| **Threshold** | Tunable via `contamination` parameter |

### Feature Selection
- **Input:** 187 ECG amplitude values per heartbeat (125Hz, 1.5s window)
- **Preprocessing:** StandardScaler → PCA(20 components)
- **PCA justification:** Reduces 187 → 20 features, preserving ~95% variance
  - Dramatically reduces model size and inference latency
  - Critical for TinyML deployment on Raspberry Pi

### Performance Metrics
After running `train_model.py`, the output will show:
- **F1 Score** (anomaly detection)
- **ROC-AUC Score**
- **Confusion Matrix** (TP, FP, FN, TN)
- **Inference latency per beat** (target: <100ms)

---

## TinyML Suitability

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Model size (total) | ~200–400 KB | <1 MB | ✓ |
| Inference latency | ~2–10 ms | <100 ms | ✓ |
| RAM required | <50 MB | <500 MB (RPi4) | ✓ |
| GPU required | No | — | ✓ |
| Framework | scikit-learn | portable | ✓ |

### Raspberry Pi Deployment Path
1. **Current (simulation):** scikit-learn `.pkl` on PC
2. **Next step:** Export to ONNX via `skl2onnx` → run with `onnxruntime` on RPi 4
3. **Future:** Quantize to INT8 → deploy on Arduino Nano 33 BLE Sense

---

## Setup & Running

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. (Optional) Download MIT-BIH dataset
Download from https://www.kaggle.com/shayanfazeli/heartbeat  
Place `mitbih_train.csv` and `mitbih_test.csv` in `./data/`  
Without this, synthetic ECG data is used automatically.

### 3. Train the model
```bash
python train_model.py
# Outputs: model/isolation_forest.pkl, scaler.pkl, pca.pkl, model_metadata.json
```

### 4. Start Cloud Server (Terminal 1)
```bash
python cloud_server.py
# Starts HTTP API on port 8080
```

### 5. Start Fog Gateway (Terminal 2)
```bash
python fog_gateway.py
# Listens on TCP 9000, stats API on 9001
```

### 6. Start Edge Sensor (Terminal 3)
```bash
python edge_sensor.py
# Streams encrypted ECG beats to fog at 60 BPM
```

### 7. Open Dashboard
Open `dashboard.html` in a browser, or navigate to:  
`http://127.0.0.1:8080/dashboard`

> **Note:** The dashboard also works in demo mode when servers are offline,
> showing simulated alerts to visualize the system behavior.

---

## File Structure
```
fog_cardiac_system/
├── train_model.py      # ML training: Isolation Forest + PCA + Scaler
├── edge_sensor.py      # Edge layer: AES-256 encrypt + stream ECG data
├── fog_gateway.py      # Fog layer: decrypt + ML inference + route alerts
├── cloud_server.py     # Cloud layer: store alerts + serve REST API
├── dashboard.html      # Live monitoring dashboard (browser)
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── data/               # Place MIT-BIH CSVs here
│   ├── mitbih_train.csv
│   └── mitbih_test.csv
├── model/              # Auto-created by train_model.py
│   ├── isolation_forest.pkl
│   ├── scaler.pkl
│   ├── pca.pkl
│   └── model_metadata.json
└── logs/               # Auto-created at runtime
    ├── fog_gateway.log
    ├── cloud_server.log
    └── cloud_alerts.db
```

---

## Security Architecture
- **AES-256-CBC:** Payload encryption at the Edge layer before transmission
- **HMAC-SHA256:** Packet integrity verification at the Fog layer
- **Pre-shared keys:** Simulates PKI key exchange (in production: RSA/ECDH)
- **Length-prefix framing:** Prevents TCP stream fragmentation attacks
- **Local processing:** Raw ECG never leaves the hospital LAN (Fog filters first)
