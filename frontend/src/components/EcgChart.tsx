import { LineChart, Line, ResponsiveContainer } from 'recharts';
import type { Alert } from '../types';

interface EcgChartProps {
  alerts: Alert[];
}

const EcgChart = ({ alerts }: EcgChartProps) => {
  const latestAlert = alerts.length > 0 ? alerts[0] : null;

  const simulateEcgData = () => {
    if (!latestAlert) {
      return Array.from({ length: 400 }, (_, i) => ({
        name: i,
        uv: 0.5 + Math.sin(i / 20) * 0.1 + (Math.random() - 0.5) * 0.05,
      }));
    }

    const pts = 40;
    const t = Array.from({ length: pts }, (_, i) => (i / pts) * Math.PI * 4);
    let signal;
    if (latestAlert.true_label === 0) {
      signal = t.map(v => 0.5 + 0.3 * Math.sin(v) + 0.05 * (Math.random() - 0.5));
    } else if (latestAlert.true_label === 2) {
      // PVC — wide bizarre
      signal = t.map(
        (v, i) => 0.5 + 0.5 * Math.sin(v * 0.7) * (i < pts / 2 ? 1 : -0.3) + 0.1 * (Math.random() - 0.5)
      );
    } else {
      signal = t.map(v => 0.5 + 0.35 * Math.sin(v) + 0.2 * Math.sin(3 * v) + 0.08 * (Math.random() - 0.5));
    }

    return signal.map((s, i) => ({ name: i, uv: s }));
  };

  const data = simulateEcgData();
  const isAnomaly = latestAlert ? latestAlert.true_label !== 0 : false;

  return (
    <div className="panel ecg-panel">
      <div className="panel-title">⟨ <span>ECG WAVEFORM</span> ⟩ — REAL-TIME SIMULATION</div>
      <ResponsiveContainer width="100%" height={90}>
        <LineChart data={data}>
          <Line type="monotone" dataKey="uv" stroke={isAnomaly ? 'var(--red)' : 'var(--teal)'} strokeWidth={1.5} dot={false} />
        </LineChart>
      </ResponsiveContainer>
      <div className="ecg-meta">
        <div>BEAT <span className="val">{latestAlert?.beat_id ?? '—'}</span></div>
        <div>LABEL <span className="val">{latestAlert?.label_name ?? '—'}</span></div>
        <div>IF SCORE <span className="val">{latestAlert?.anomaly_score?.toFixed(4) ?? '—'}</span></div>
        <div>LATENCY <span className="val">{latestAlert?.inference_ms?.toFixed(2) ?? '—'}ms</span></div>
        <div>DEVICE <span className="val">EDGE_NODE_001</span></div>
        <div>SAMPLING <span className="val">125 Hz</span></div>
      </div>
    </div>
  );
};

export default EcgChart;
