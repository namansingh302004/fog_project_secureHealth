import { Row, Col } from 'react-bootstrap';
import type { Alert } from '../types';

interface StatCardProps {
  title: string;
  value: string | number;
  subtext: string;
  color: 'teal' | 'red' | 'amber' | 'blue' | 'green';
}

const StatCard = ({ title, value, subtext, color }: StatCardProps) => {
  return (
    <div className={`stat-card ${color}`}>
      <div className="stat-label">{title}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{subtext}</div>
    </div>
  );
};

interface StatsRowProps {
  alerts: Alert[];
  stats: { total_alerts: number; avg_inference_ms: number } | null;
}

const StatsRow = ({ alerts, stats }: StatsRowProps) => {
  const pvcCount = alerts.filter(a => a.true_label === 2).length;
  const svpCount = alerts.filter(a => a.true_label === 1).length;
  const otherCount = alerts.filter(a => a.true_label !== 1 && a.true_label !== 2).length;

  return (
    <Row className="stats-row">
      <Col><StatCard title="Total Alerts" value={stats?.total_alerts ?? 0} subtext="forwarded to cloud" color="teal" /></Col>
      <Col><StatCard title="PVC Events" value={pvcCount} subtext="premature ventricular" color="red" /></Col>
      <Col><StatCard title="SVP Events" value={svpCount} subtext="supraventricular" color="amber" /></Col>
      <Col><StatCard title="Fusion / Other" value={otherCount} subtext="fusion + unclassifiable" color="blue" /></Col>
      <Col><StatCard title="Avg Inference" value={stats?.avg_inference_ms?.toFixed(1) ?? '—'} subtext="milliseconds / beat" color="green" /></Col>
    </Row>
  );
};

export default StatsRow;
