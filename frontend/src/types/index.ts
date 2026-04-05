export interface Alert {
  id: number;
  beat_id: number;
  cloud_ts: number;
  true_label: number;
  label_name: string;
  anomaly_score: number;
  inference_ms: number;
}

export interface EcgSignalFrame {
  device_id: string;
  beat_id: number;
  true_label: number;
  label_name: string;
  anomaly_score: number;
  inference_ms: number;
  captured_at: number;
  signal: number[];
}

export interface FogStats {
  total_beats: number;
  bandwidth_saved_pct: number;
  avg_inference_ms: number;
  latest_signal: EcgSignalFrame | null;
  recent_signals: EcgSignalFrame[];
}
