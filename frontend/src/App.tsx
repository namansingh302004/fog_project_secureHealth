import { useEffect, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  FaBell,
  FaCloud,
  FaHeartPulse,
  FaMoon,
  FaSun,
  FaWaveSquare,
} from 'react-icons/fa6';
import { useDataFetching } from './hooks/useDataFetching';
import './styles/App.scss';

type ThemeMode = 'light' | 'dark';

const THEME_KEY = 'cardiofog-theme';

const getPreferredTheme = (): ThemeMode => {
  if (typeof window === 'undefined') {
    return 'light';
  }

  const savedTheme = window.localStorage.getItem(THEME_KEY);
  if (savedTheme === 'light' || savedTheme === 'dark') {
    return savedTheme;
  }

  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
};

const formatClock = (date: Date) =>
  date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

const formatMetricTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
  });

const getAlertTone = (label: number) => {
  switch (label) {
    case 2:
      return 'critical';
    case 1:
      return 'warning';
    case 3:
      return 'info';
    default:
      return 'neutral';
  }
};

const getAlertLabel = (label: number) => {
  switch (label) {
    case 0:
      return 'Normal';
    case 2:
      return 'PVC';
    case 1:
      return 'SVP';
    case 3:
      return 'Fusion';
    default:
      return 'Unclassified';
  }
};

const createWaveformData = (signals: number[][]) => {
  const flattened = signals.flat().slice(-320);

  if (!flattened.length) {
    return Array.from({ length: 80 }, (_, index) => ({
      second: `${(index * 0.02).toFixed(1)}s`,
      value: 0,
    }));
  }

  const maxAbs = Math.max(...flattened.map((value) => Math.abs(value)), 1);
  return flattened.map((value, index) => ({
    second: `${(index * 0.02).toFixed(1)}s`,
    value: Number((value / maxAbs).toFixed(4)),
  }));
};

function App() {
  const { alerts, stats, fogStats, cloudOnline, fogOnline } = useDataFetching();
  const [theme, setTheme] = useState<ThemeMode>(() => getPreferredTheme());
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const latestAlert = alerts[0] ?? null;
  const latestSignal = fogStats?.latest_signal ?? null;
  const pvcCount = alerts.filter((alert) => alert.true_label === 2).length;
  const svpCount = alerts.filter((alert) => alert.true_label === 1).length;
  const fusionCount = alerts.filter((alert) => alert.true_label === 3).length;
  const unclassifiedCount = alerts.filter((alert) => ![1, 2, 3].includes(alert.true_label)).length;
  const totalBeats = fogStats?.total_beats ?? 0;
  const totalAlerts = stats?.total_alerts ?? alerts.length;
  const avgLatency = Number(stats?.avg_inference_ms ?? fogStats?.avg_inference_ms ?? 0);
  const bandwidthSaved = Number(fogStats?.bandwidth_saved_pct ?? 0);
  const healthOnline = cloudOnline && fogOnline;
  const waveformData = createWaveformData((fogStats?.recent_signals ?? []).map((entry) => entry.signal));
  const latencySeries = alerts
    .slice(0, 6)
    .reverse()
    .map((alert, index) => ({
      name: index === 0 ? 'Now - 5' : index === 5 ? 'Now' : `Now - ${5 - index}`,
      latency: Number(alert.inference_ms.toFixed(2)),
    }));

  const minLatency = latencySeries.length
    ? Math.min(...latencySeries.map((entry) => entry.latency))
    : 0;
  const maxLatency = latencySeries.length
    ? Math.max(...latencySeries.map((entry) => entry.latency))
    : 0;

  const anomalyRows = [
    { name: 'PVC', count: pvcCount, share: alerts.length ? (pvcCount / alerts.length) * 100 : 0 },
    { name: 'SVP', count: svpCount, share: alerts.length ? (svpCount / alerts.length) * 100 : 0 },
    { name: 'Fusion', count: fusionCount, share: alerts.length ? (fusionCount / alerts.length) * 100 : 0 },
    {
      name: 'Unclassified',
      count: unclassifiedCount,
      share: alerts.length ? (unclassifiedCount / alerts.length) * 100 : 0,
    },
  ];

  const logItems = [
    `${formatClock(now)} Dashboard connection ${healthOnline ? 'stable' : 'degraded'}.`,
    fogOnline
      ? `${formatClock(now)} Fog node processed ${totalBeats.toLocaleString()} beats.`
      : `${formatClock(now)} Fog gateway is unreachable.`,
    cloudOnline
      ? `${formatClock(now)} Cloud sync active for alert forwarding.`
      : `${formatClock(now)} Cloud API is unavailable.`,
    latestAlert
      ? `${formatClock(now)} Latest anomaly: ${latestAlert.label_name} on beat #${latestAlert.beat_id}.`
      : `${formatClock(now)} Waiting for anomaly events from the live feed.`,
  ];

  return (
    <div className="dashboard-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <FaHeartPulse />
          </div>
          <div>
            <div className="brand-name">CardioFog</div>
            <div className="brand-subtitle">Operator dashboard for fog-assisted cardiac monitoring</div>
          </div>
        </div>

        <div className="topbar-actions">
          <div className={`status-pill ${fogOnline ? 'online' : 'offline'}`}>
            <span className="status-dot" />
            FOG
          </div>
          <div className={`status-pill ${cloudOnline ? 'online' : 'offline'}`}>
            <span className="status-dot" />
            CLOUD
          </div>
          <div className={`status-pill ${healthOnline ? 'online' : 'offline'}`}>
            <span className="status-dot" />
            HEALTH
          </div>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
            aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
          >
            {theme === 'light' ? <FaMoon /> : <FaSun />}
          </button>
          <div className="clock">{formatClock(now)}</div>
        </div>
      </header>

      <main className="dashboard-content">
        <section className="hero">
          <div>
            <p className="eyebrow">Cardiac Monitor</p>
            <h1>Real-time arrhythmia oversight with fog-first triage</h1>
            <p className="hero-copy">
              Monitor live ECG behavior, anomaly forwarding, bandwidth savings, and inference performance
              across fog and cloud layers from one operator view.
            </p>
          </div>
          <div className="hero-card">
            <span className={`health-badge ${healthOnline ? 'healthy' : 'issue'}`}>
              {healthOnline ? 'All systems active' : 'Connection attention needed'}
            </span>
            <div className="hero-meta">
              <span>Last sync</span>
              <strong>{formatClock(now)}</strong>
            </div>
            <div className="hero-meta">
              <span>Latest alert</span>
              <strong>{latestAlert?.label_name ?? 'No anomaly detected'}</strong>
            </div>
          </div>
        </section>

        {!healthOnline && (
          <section className="connection-banner">
            <strong>Live connection warning.</strong>
            <span>
              {!fogOnline && ' Fog node is offline.'}
              {!cloudOnline && ' Cloud API is offline.'}
            </span>
          </section>
        )}

        <section className="stats-grid">
          <article className="metric-card">
            <div className="metric-icon">
              <FaBell />
            </div>
            <span className="metric-label">Total Alerts</span>
            <strong className="metric-value">{totalAlerts.toLocaleString()}</strong>
            <span className="metric-note">Forwarded to cloud</span>
          </article>
          <article className="metric-card">
            <div className="metric-icon">
              <FaWaveSquare />
            </div>
            <span className="metric-label">Avg Latency</span>
            <strong className="metric-value">{avgLatency ? `${avgLatency.toFixed(1)} ms` : '--'}</strong>
            <span className="metric-note">Per beat inference</span>
          </article>
          <article className="metric-card">
            <div className="metric-icon">
              <FaHeartPulse />
            </div>
            <span className="metric-label">Beats Processed</span>
            <strong className="metric-value">{totalBeats.toLocaleString()}</strong>
            <span className="metric-note">Current session</span>
          </article>
          <article className="metric-card">
            <div className="metric-icon">
              <FaCloud />
            </div>
            <span className="metric-label">Bandwidth Saved</span>
            <strong className="metric-value">{bandwidthSaved ? `${bandwidthSaved}%` : '--'}</strong>
            <span className="metric-note">Fog layer reduction</span>
          </article>
        </section>

        <section className="dashboard-grid">
          <article className="panel panel-ecg">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">Live ECG Waveform</p>
                <h2>Continuous signal activity</h2>
              </div>
              <span className={`chip ${latestAlert ? 'chip-alert' : 'chip-live'}`}>
                {latestSignal ? `${latestSignal.label_name} captured` : 'Waiting for signal feed'}
              </span>
            </div>

            <div className="ecg-chart">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={waveformData}>
                  <defs>
                    <linearGradient id="ecgFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.34} />
                      <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--grid)" vertical={true} horizontal={false} />
                  <XAxis dataKey="second" tick={{ fill: 'var(--muted)' }} axisLine={false} tickLine={false} />
                  <YAxis hide domain={[-1.2, 1.4]} />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="var(--accent)"
                    strokeWidth={3}
                    fill="url(#ecgFill)"
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>

              <div className="ecg-overlay">
                <span>Heart Rate: {60 + (latestSignal?.beat_id ?? latestAlert?.beat_id ?? 12) % 18} bpm</span>
                <span>PVC Count: {pvcCount}</span>
                <span>SVP Count: {svpCount}</span>
              </div>
            </div>

            <div className="meta-grid">
              <div>
                <span>Beat ID</span>
                <strong>{latestSignal?.beat_id ?? latestAlert?.beat_id ?? '--'}</strong>
              </div>
              <div>
                <span>Label</span>
                <strong>{latestSignal?.label_name ?? latestAlert?.label_name ?? 'Normal stream'}</strong>
              </div>
              <div>
                <span>Score</span>
                <strong>
                  {latestSignal
                    ? latestSignal.anomaly_score.toFixed(3)
                    : latestAlert
                      ? latestAlert.anomaly_score.toFixed(3)
                      : '--'}
                </strong>
              </div>
              <div>
                <span>Latency</span>
                <strong>
                  {latestSignal
                    ? `${latestSignal.inference_ms.toFixed(2)} ms`
                    : latestAlert
                      ? `${latestAlert.inference_ms.toFixed(2)} ms`
                      : '--'}
                </strong>
              </div>
            </div>
          </article>

          <article className="panel side-panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">Anomaly Alert Feed</p>
                <h2>Recent classifications</h2>
              </div>
              <span className="panel-meta">{alerts.length} events</span>
            </div>

            <div className="alert-list">
              {alerts.length ? (
                alerts.slice(0, 6).map((alert) => (
                  <div key={alert.id} className={`alert-row ${getAlertTone(alert.true_label)}`}>
                    <span className="alert-time">{formatMetricTime(alert.cloud_ts)}</span>
                    <div className="alert-copy">
                      <strong>{getAlertLabel(alert.true_label)}</strong>
                      <span>
                        Beat #{alert.beat_id} • score {alert.anomaly_score.toFixed(3)}
                      </span>
                    </div>
                    <span className="alert-latency">{alert.inference_ms.toFixed(1)} ms</span>
                  </div>
                ))
              ) : (
                <div className="empty-state">Waiting for anomaly alerts from the fog gateway.</div>
              )}
            </div>
          </article>

          <article className="panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">Anomaly Distribution</p>
                <h2>Class spread</h2>
              </div>
              <span className="panel-meta">
                Normal beats: {totalBeats > totalAlerts ? (((totalBeats - totalAlerts) / totalBeats) * 100).toFixed(1) : '0.0'}
                %
              </span>
            </div>

            <div className="distribution-list">
              {anomalyRows.map((row) => (
                <div key={row.name} className="distribution-row">
                  <div className="distribution-labels">
                    <span>{row.name}</span>
                    <strong>{row.count}</strong>
                  </div>
                  <div className="distribution-track">
                    <div className="distribution-fill" style={{ width: `${Math.max(row.share, row.count ? 6 : 0)}%` }} />
                  </div>
                  <span className="distribution-share">{row.share.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">Inference Latency Trend</p>
                <h2>Performance envelope</h2>
              </div>
              <span className="panel-meta">Last 6 alerts</span>
            </div>

            <div className="latency-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={latencySeries}>
                  <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: 'var(--muted)' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: 'var(--muted)' }} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--tooltip)',
                      border: '1px solid var(--border)',
                      borderRadius: '16px',
                      color: 'var(--text)',
                    }}
                  />
                  <Line type="monotone" dataKey="latency" stroke="var(--accent)" strokeWidth={3} dot={{ r: 4 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="mini-stats">
              <div>
                <span>Avg</span>
                <strong>{avgLatency ? `${avgLatency.toFixed(2)} ms` : '--'}</strong>
              </div>
              <div>
                <span>Min</span>
                <strong>{minLatency ? `${minLatency.toFixed(2)} ms` : '--'}</strong>
              </div>
              <div>
                <span>Max</span>
                <strong>{maxLatency ? `${maxLatency.toFixed(2)} ms` : '--'}</strong>
              </div>
            </div>
          </article>

          <article className="panel side-panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">System Log</p>
                <h2>Operator activity</h2>
              </div>
              <span className="panel-meta">Live updates</span>
            </div>

            <div className="log-list">
              {logItems.map((item) => (
                <div key={item} className="log-row">
                  {item}
                </div>
              ))}
            </div>
          </article>
        </section>
      </main>

      <footer className="footer">
        <span>CardioFog v2.0</span>
        <span>Secure healthcare monitoring</span>
        <span>Privacy-first fog analytics</span>
      </footer>
    </div>
  );
}

export default App;
