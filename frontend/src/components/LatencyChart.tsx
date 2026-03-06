import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import type { Alert } from '../types';

interface LatencyChartProps {
  alerts: Alert[];
}

const LatencyChart = ({ alerts }: LatencyChartProps) => {
  const latencies = alerts.map(a => ({ name: a.beat_id, latency: a.inference_ms }));
  const avgLatency = latencies.reduce((acc, val) => acc + val.latency, 0) / (latencies.length || 1);
  const minLatency = Math.min(...latencies.map(l => l.latency));
  const maxLatency = Math.max(...latencies.map(l => l.latency));

  return (
    <div className="panel lat-panel">
      <div className="panel-title">⟨ <span>INFERENCE LATENCY TREND</span> ⟩</div>
      <ResponsiveContainer width="100%" height={90}>
        <LineChart data={latencies}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(26,58,82,0.6)" />
          <XAxis dataKey="name" tick={{ fill: 'var(--text-dim)' }} />
          <YAxis tick={{ fill: 'var(--text-dim)' }} />
          <Tooltip />
          <Line type="monotone" dataKey="latency" stroke="var(--teal)" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
      <div className="lat-stats">
        <div><span className="lat-stat-label">AVG </span><span className="lat-stat-val">{avgLatency.toFixed(2)}</span> ms</div>
        <div><span className="lat-stat-label">MIN </span><span className="lat-stat-val">{minLatency.toFixed(2)}</span> ms</div>
        <div><span className="lat-stat-label">MAX </span><span className="lat-stat-val">{maxLatency.toFixed(2)}</span> ms</div>
        <div><span className="lat-stat-label">TARGET </span><span className="lat-stat-val" style={{ color: 'var(--green)' }}>100</span> ms</div>
      </div>
    </div>
  );
};

export default LatencyChart;
