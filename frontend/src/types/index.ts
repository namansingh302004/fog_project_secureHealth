export interface Alert {
  id: number;
  beat_id: number;
  cloud_ts: number;
  true_label: number;
  label_name: string;
  anomaly_score: number;
  inference_ms: number;
}
