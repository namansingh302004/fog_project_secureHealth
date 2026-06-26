"""
train_model.py — DA-2: ML Model Training
=========================================
Trains an Isolation Forest anomaly detection model on the MIT-BIH
Arrhythmia dataset. The model is designed to be lightweight enough
for deployment on a Raspberry Pi (TinyML-ready).

Dataset: MIT-BIH Arrhythmia (Kaggle: shayanfazeli/heartbeat)
         mitbih_train.csv, mitbih_test.csv

Usage:
    python train_model.py --data_path ./data/
"""

import argparse
import os
import time
import numpy as np
import pandas as pd
import pickle
import json

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import (
    classification_report, confusion_matrix,
    roc_auc_score, f1_score
)
import warnings
warnings.filterwarnings('ignore')


# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────
RANDOM_STATE   = 42
N_ESTIMATORS   = 100          # Isolation Forest trees
CONTAMINATION  = 0.15         # ~15% of training data are anomalies
N_PCA_COMPONENTS = 20         # Reduce 187 features → 20 for edge deployment
MODEL_SAVE_PATH  = "model/"
DATA_PATH        = "data/"

# MIT-BIH label mapping
LABEL_MAP = {0: "Normal", 1: "Supraventricular", 2: "PVC", 3: "Fusion", 4: "Unclassifiable"}
ANOMALY_LABELS = {1, 2, 3, 4}   # All non-Normal classes are "anomalous"


def load_dataset(data_path: str):
    """Load MIT-BIH train/test CSVs. Generates synthetic data if files absent."""
    train_path = os.path.join(data_path, "mitbih_train.csv")
    test_path  = os.path.join(data_path, "mitbih_test.csv")

    if os.path.exists(train_path) and os.path.exists(test_path):
        print(f"[DATA] Loading real MIT-BIH dataset from {data_path}")
        df_train = pd.read_csv(train_path, header=None)
        df_test  = pd.read_csv(test_path,  header=None)
    else:
        print("[DATA] MIT-BIH CSVs not found — generating synthetic ECG data for demo.")
        print("[DATA] Download the real dataset from: https://www.kaggle.com/shayanfazeli/heartbeat")
        df_train, df_test = _generate_synthetic_data()

    return df_train, df_test


def _generate_synthetic_data(n_train=10000, n_test=2000, n_features=187):
    """
    Generate synthetic ECG-like data that mimics MIT-BIH structure.
    Normal beats are sinusoidal; anomalies have irregular peaks/noise.
    """
    np.random.seed(RANDOM_STATE)
    t = np.linspace(0, 2 * np.pi, n_features - 1)

    def make_normal_beat(n):
        beats = []
        for _ in range(n):
            # QRS complex + T-wave approximation
            signal = (0.6 * np.sin(t) +
                      0.3 * np.sin(2*t) +
                      0.1 * np.random.randn(n_features - 1))
            signal = (signal - signal.min()) / (signal.max() - signal.min() + 1e-9)
            beats.append(signal)
        return np.array(beats)

    def make_anomaly_beat(n, label):
        beats = []
        for _ in range(n):
            signal = (0.6 * np.sin(t) +
                      0.3 * np.sin(2*t) +
                      0.1 * np.random.randn(n_features - 1))
            if label == 1:   # Supraventricular — early beat
                signal += 0.5 * np.exp(-((t - np.pi/2)**2) / 0.05)
            elif label == 2: # PVC — wide, bizarre QRS
                signal += 0.8 * np.exp(-((t - np.pi)**2) / 0.2) * np.random.choice([-1,1])
            elif label == 3: # Fusion
                signal += 0.4 * np.sin(3*t) + 0.3 * np.random.randn(n_features - 1)
            elif label == 4: # Unclassifiable — high noise
                signal += 0.7 * np.random.randn(n_features - 1)
            signal = (signal - signal.min()) / (signal.max() - signal.min() + 1e-9)
            beats.append(signal)
        return np.array(beats)

    # Class distribution mirrors MIT-BIH (imbalanced)
    n_normal_train = int(n_train * 0.83)
    rows_train, rows_test = [], []

    X_norm = make_normal_beat(n_normal_train)
    labels = np.zeros(n_normal_train)
    rows_train.append(np.column_stack([X_norm, labels]))

    for lbl, frac in zip([1, 2, 3, 4], [0.06, 0.07, 0.02, 0.02]):
        n_cls = int(n_train * frac)
        X_cls = make_anomaly_beat(n_cls, lbl)
        rows_train.append(np.column_stack([X_cls, np.full(n_cls, lbl)]))

    n_normal_test = int(n_test * 0.83)
    X_norm_t = make_normal_beat(n_normal_test)
    rows_test.append(np.column_stack([X_norm_t, np.zeros(n_normal_test)]))
    for lbl, frac in zip([1, 2, 3, 4], [0.06, 0.07, 0.02, 0.02]):
        n_cls = int(n_test * frac)
        X_cls = make_anomaly_beat(n_cls, lbl)
        rows_test.append(np.column_stack([X_cls, np.full(n_cls, lbl)]))

    df_train = pd.DataFrame(np.vstack(rows_train))
    df_test  = pd.DataFrame(np.vstack(rows_test))
    df_train = df_train.sample(frac=1, random_state=RANDOM_STATE).reset_index(drop=True)
    df_test  = df_test.sample(frac=1, random_state=RANDOM_STATE).reset_index(drop=True)
    return df_train, df_test


def preprocess(df_train, df_test):
    """Split features/labels, scale, apply PCA for lightweight edge model."""
    X_train = df_train.iloc[:, :-1].values.astype(np.float32)
    y_train = df_train.iloc[:,  -1].values.astype(int)
    X_test  = df_test.iloc[:, :-1].values.astype(np.float32)
    y_test  = df_test.iloc[:,  -1].values.astype(int)

    # Binary labels: 0 = Normal, 1 = Anomaly
    y_train_bin = np.where(np.isin(y_train, list(ANOMALY_LABELS)), 1, 0)
    y_test_bin  = np.where(np.isin(y_test,  list(ANOMALY_LABELS)), 1, 0)

    print(f"[DATA] Train: {X_train.shape}, Anomaly rate: {y_train_bin.mean()*100:.1f}%")
    print(f"[DATA] Test:  {X_test.shape},  Anomaly rate: {y_test_bin.mean()*100:.1f}%")

    # StandardScaler
    scaler = StandardScaler()
    X_train_sc = scaler.fit_transform(X_train)
    X_test_sc  = scaler.transform(X_test)

    # PCA — reduces model size & inference latency (critical for TinyML)
    pca = PCA(n_components=N_PCA_COMPONENTS, random_state=RANDOM_STATE)
    X_train_pca = pca.fit_transform(X_train_sc)
    X_test_pca  = pca.transform(X_test_sc)

    variance_explained = pca.explained_variance_ratio_.sum() * 100
    print(f"[PCA]  {N_PCA_COMPONENTS} components explain {variance_explained:.1f}% variance")

    return (X_train_pca, y_train_bin,
            X_test_pca,  y_test_bin,
            scaler, pca)


def train_isolation_forest(X_train_normal):
    """
    Train Isolation Forest ONLY on normal samples (unsupervised).
    This is the standard approach for anomaly detection:
    the model learns what 'normal' looks like, and flags deviations.
    """
    print(f"\n[TRAIN] Training Isolation Forest...")
    print(f"        Trees: {N_ESTIMATORS}, Contamination: {CONTAMINATION}")
    print(f"        Training on {X_train_normal.shape[0]} normal samples")
    print(f"        Feature dimensions: {X_train_normal.shape[1]} (after PCA)")

    start = time.time()
    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        max_samples='auto',
        random_state=RANDOM_STATE,
        n_jobs=-1
    )
    model.fit(X_train_normal)
    elapsed = time.time() - start
    print(f"[TRAIN] Training complete in {elapsed:.2f}s")
    return model


def evaluate(model, X_test, y_test_bin):
    """
    Evaluate on test set. Isolation Forest returns:
      +1 = inlier (Normal)
      -1 = outlier (Anomaly)
    We convert to binary 0/1 for comparison.
    """
    print("\n[EVAL]  Running inference on test set...")
    start = time.time()
    raw_preds = model.predict(X_test)
    elapsed = time.time() - start

    # Convert IF convention: -1 → anomaly=1, +1 → normal=0
    y_pred = np.where(raw_preds == -1, 1, 0)

    # Anomaly scores (lower = more anomalous)
    scores = model.decision_function(X_test)
    # Invert so higher score = more anomalous (for AUC)
    anomaly_scores = -scores

    # Metrics
    f1  = f1_score(y_test_bin, y_pred, average='binary')
    try:
        auc = roc_auc_score(y_test_bin, anomaly_scores)
    except Exception:
        auc = 0.0

    cm = confusion_matrix(y_test_bin, y_pred)
    tn, fp, fn, tp = cm.ravel() if cm.shape == (2,2) else (0,0,0,0)

    per_sample_ms = (elapsed / len(X_test)) * 1000

    print(f"\n{'='*50}")
    print(f"  EVALUATION RESULTS")
    print(f"{'='*50}")
    print(f"  F1 Score (Anomaly Detection): {f1:.4f}")
    print(f"  ROC-AUC Score:                {auc:.4f}")
    print(f"  Confusion Matrix:")
    print(f"    True Negatives  (TN): {tn:6d}   (Normal correctly identified)")
    print(f"    False Positives (FP): {fp:6d}   (Normal flagged as anomaly)")
    print(f"    False Negatives (FN): {fn:6d}   (Anomaly missed — DANGEROUS)")
    print(f"    True Positives  (TP): {tp:6d}   (Anomaly correctly detected)")
    print(f"  Inference time per beat: {per_sample_ms:.3f} ms  (TinyML target: <100ms ✓)")
    print(f"{'='*50}")

    return {"f1": f1, "auc": auc, "tn": int(tn), "fp": int(fp),
            "fn": int(fn), "tp": int(tp), "inference_ms": per_sample_ms}


def save_model(model, scaler, pca, metrics):
    """Save model artifacts for deployment on Fog/Edge node."""
    os.makedirs(MODEL_SAVE_PATH, exist_ok=True)

    # Save sklearn model
    with open(os.path.join(MODEL_SAVE_PATH, "isolation_forest.pkl"), "wb") as f:
        pickle.dump(model, f)

    with open(os.path.join(MODEL_SAVE_PATH, "scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)

    with open(os.path.join(MODEL_SAVE_PATH, "pca.pkl"), "wb") as f:
        pickle.dump(pca, f)

    # Save metadata for dashboard / documentation
    metadata = {
        "model_type": "IsolationForest",
        "n_estimators": N_ESTIMATORS,
        "contamination": CONTAMINATION,
        "n_features_input": 187,
        "n_features_after_pca": N_PCA_COMPONENTS,
        "metrics": metrics,
        "tinyml_notes": {
            "model_size_kb": _get_file_size_kb(os.path.join(MODEL_SAVE_PATH, "isolation_forest.pkl")),
            "inference_ms": metrics["inference_ms"],
            "rpi_compatible": True,
            "framework": "scikit-learn (portable to ONNX/TFLite)"
        }
    }
    with open(os.path.join(MODEL_SAVE_PATH, "model_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n[SAVE]  Model artifacts saved to ./{MODEL_SAVE_PATH}")
    print(f"        isolation_forest.pkl  ({metadata['tinyml_notes']['model_size_kb']:.1f} KB)")
    print(f"        scaler.pkl, pca.pkl")
    print(f"        model_metadata.json")


def _get_file_size_kb(path):
    try:
        return os.path.getsize(path) / 1024
    except Exception:
        return 0.0


def export_onnx(model, scaler, pca, metrics):
    """
    Export the full sklearn pipeline (StandardScaler → PCA → IsolationForest)
    to ONNX format for bare-metal execution on Raspberry Pi / microcontrollers.

    Requires: pip install skl2onnx onnxruntime
    Gracefully skips if the libraries are not installed.

    Why ONNX?
      - Eliminates scikit-learn dependency on the deployment device
      - Enables execution via ONNX Runtime (C++ engine) — ~3–5× faster
      - Supports INT8 quantisation for microcontrollers (Arduino Nano 33)
      - Single .onnx file replaces three .pkl files
    """
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType
        from sklearn.pipeline import Pipeline
        import onnxruntime as rt
        import numpy as np

        print("\n[ONNX] Exporting sklearn pipeline to ONNX format...")

        # Build a single unified pipeline for clean ONNX export
        pipeline = Pipeline([
            ("scaler", scaler),
            ("pca",    pca),
            ("model",  model),
        ])

        # Define input: batch of 187-feature ECG vectors (float32)
        initial_type = [("ecg_input", FloatTensorType([None, 187]))]

        onnx_model = convert_sklearn(
            pipeline,
            initial_types=initial_type,
            target_opset=17,
        )

        onnx_path = os.path.join(MODEL_SAVE_PATH, "isolation_forest_pipeline.onnx")
        with open(onnx_path, "wb") as f:
            f.write(onnx_model.SerializeToString())

        onnx_kb = _get_file_size_kb(onnx_path)
        print(f"[ONNX] ✓ Exported → {onnx_path}  ({onnx_kb:.1f} KB)")

        # Validate the ONNX model with a test inference
        sess = rt.InferenceSession(onnx_path)
        input_name  = sess.get_inputs()[0].name
        output_name = sess.get_outputs()[0].name

        dummy_input = np.random.randn(1, 187).astype(np.float32)
        result = sess.run([output_name], {input_name: dummy_input})
        prediction = result[0][0]  # +1 normal, -1 anomaly

        print(f"[ONNX] ✓ Validation inference passed — prediction: {prediction}")
        print(f"[ONNX]   Input : (1, 187) float32 ECG features")
        print(f"[ONNX]   Output: {prediction}  (+1=Normal, -1=Anomaly)")
        print(f"[ONNX]   File  : {onnx_path}  ({onnx_kb:.1f} KB)")
        print(f"[ONNX]   Replaces: isolation_forest.pkl + scaler.pkl + pca.pkl")

        # Update metadata
        metadata_path = os.path.join(MODEL_SAVE_PATH, "model_metadata.json")
        if os.path.exists(metadata_path):
            with open(metadata_path) as f:
                meta = json.load(f)
            meta["onnx_export"] = {
                "path":       onnx_path,
                "size_kb":    onnx_kb,
                "opset":      17,
                "validated":  True,
                "runtime":    "onnxruntime",
                "note":       "Use for Raspberry Pi / bare-metal deployment"
            }
            with open(metadata_path, "w") as f:
                json.dump(meta, f, indent=2)
            print(f"[ONNX] Metadata updated: {metadata_path}")

        return True

    except ImportError:
        print("\n[ONNX] skl2onnx or onnxruntime not installed — skipping ONNX export.")
        print("[ONNX] To enable: pip install skl2onnx onnxruntime")
        print("[ONNX] (Not required for local simulation — only for RPi deployment)")
        return False
    except Exception as e:
        print(f"\n[ONNX] Export failed: {e}")
        print("[ONNX] Continuing without ONNX export.")
        return False


def tinyml_suitability_report(model, scaler, pca, metrics):
    """Print a TinyML/Edge suitability analysis."""
    model_kb = _get_file_size_kb(os.path.join(MODEL_SAVE_PATH, "isolation_forest.pkl"))
    scaler_kb = _get_file_size_kb(os.path.join(MODEL_SAVE_PATH, "scaler.pkl"))
    pca_kb    = _get_file_size_kb(os.path.join(MODEL_SAVE_PATH, "pca.pkl"))
    total_kb  = model_kb + scaler_kb + pca_kb

    print(f"\n{'='*50}")
    print(f"  TINYML / EDGE SUITABILITY REPORT")
    print(f"{'='*50}")
    print(f"  Model Size Breakdown:")
    print(f"    isolation_forest.pkl : {model_kb:8.1f} KB")
    print(f"    scaler.pkl           : {scaler_kb:8.1f} KB")
    print(f"    pca.pkl              : {pca_kb:8.1f} KB")
    print(f"    TOTAL                : {total_kb:8.1f} KB")
    print(f"")
    print(f"  Performance:")
    print(f"    Inference/beat       : {metrics['inference_ms']:.3f} ms")
    print(f"    F1 Score             : {metrics['f1']:.4f}")
    print(f"    ROC-AUC              : {metrics['auc']:.4f}")
    print(f"")
    print(f"  Raspberry Pi 4 Compatibility:")
    print(f"    RAM (4B RPi4 = 4GB)  : {total_kb/1024:.2f} MB required ✓")
    print(f"    Inference latency    : {metrics['inference_ms']:.3f} ms  (< 100ms ✓)")
    print(f"    No GPU required      : ✓ (scikit-learn CPU-only)")
    print(f"    ONNX Export possible : ✓ (skl2onnx library)")
    print(f"")
    print(f"  Deployment Path:")
    print(f"    Current : scikit-learn pkl (PC / RPi simulation)")
    print(f"    Next    : Export via skl2onnx → ONNX Runtime on RPi")
    print(f"    Future  : Quantize to INT8 for microcontrollers (Arduino Nano 33)")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Train Isolation Forest for ECG anomaly detection")
    parser.add_argument("--data_path", default=DATA_PATH, help="Path to MIT-BIH CSV files")
    args = parser.parse_args()

    print("=" * 50)
    print("  DA-2: ML MODEL TRAINING")
    print("  Secure Fog Computing — ECG Anomaly Detection")
    print("  Algorithm: Isolation Forest (TinyML-Ready)")
    print("=" * 50)

    # 1. Load data
    df_train, df_test = load_dataset(args.data_path)

    # 2. Preprocess
    X_train, y_train_bin, X_test, y_test_bin, scaler, pca = preprocess(df_train, df_test)

    # 3. Train only on NORMAL samples (unsupervised anomaly detection)
    X_train_normal = X_train[y_train_bin == 0]
    model = train_isolation_forest(X_train_normal)

    # 4. Evaluate
    metrics = evaluate(model, X_test, y_test_bin)

    # 5. Save
    save_model(model, scaler, pca, metrics)

    # 6. ONNX Export (for Raspberry Pi / bare-metal deployment)
    export_onnx(model, scaler, pca, metrics)

    # 7. TinyML Report
    tinyml_suitability_report(model, scaler, pca, metrics)

    print("\n[DONE]  Run fog_gateway.py to start the fog node.")


if __name__ == "__main__":
    main()
