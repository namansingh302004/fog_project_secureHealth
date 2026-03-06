import type { Alert } from '../types';

interface DistributionChartProps {
  alerts: Alert[];
  fogStats: { bandwidth_saved_pct: number } | null;
}

const DistributionChart = ({ alerts, fogStats }: DistributionChartProps) => {
  const pvcCount = alerts.filter(a => a.true_label === 2).length;
  const svpCount = alerts.filter(a => a.true_label === 1).length;
  const fusionCount = alerts.filter(a => a.true_label === 3).length;
  const unclassCount = alerts.filter(a => a.true_label !== 1 && a.true_label !== 2 && a.true_label !== 3).length;

  const total = alerts.length || 1;
  const pvcWidth = (pvcCount / total) * 100;
  const svpWidth = (svpCount / total) * 100;
  const fusionWidth = (fusionCount / total) * 100;
  const unclassWidth = (unclassCount / total) * 100;

  return (
    <div className="panel dist-panel">
      <div className="panel-title">⟨ <span>ALERT DISTRIBUTION</span> ⟩</div>
      <div className="dist-bar-container" id="dist-bars">
        <div className="dist-row">
          <div className="dist-label">PVC</div>
          <div className="dist-bar-bg"><div className="dist-bar-fill" style={{ width: `${pvcWidth}%`, background: 'var(--red)' }}></div></div>
          <div className="dist-count">{pvcCount}</div>
        </div>
        <div className="dist-row">
          <div className="dist-label">Supravent.</div>
          <div className="dist-bar-bg"><div className="dist-bar-fill" style={{ width: `${svpWidth}%`, background: 'var(--amber)' }}></div></div>
          <div className="dist-count">{svpCount}</div>
        </div>
        <div className="dist-row">
          <div className="dist-label">Fusion</div>
          <div className="dist-bar-bg"><div className="dist-bar-fill" style={{ width: `${fusionWidth}%`, background: 'var(--blue)' }}></div></div>
          <div className="dist-count">{fusionCount}</div>
        </div>
        <div className="dist-row">
          <div className="dist-label">Unclass.</div>
          <div className="dist-bar-bg"><div className="dist-bar-fill" style={{ width: `${unclassWidth}%`, background: 'var(--text-dim)' }}></div></div>
          <div className="dist-count">{unclassCount}</div>
        </div>
      </div>
      <div style={{ marginTop: '16px', fontFamily: 'var(--mono)', fontSize: '10px', color: 'var(--text-dim)', lineHeight: 1.8 }}>
        <div>BANDWIDTH REDUCTION</div>
        <div style={{ fontSize: '22px', fontFamily: 'var(--cond)', fontWeight: 700, color: 'var(--green)' }}>{fogStats?.bandwidth_saved_pct ?? '—'}%</div>
        <div style={{ marginTop: '4px' }}>Normal beats filtered at fog layer</div>
      </div>
    </div>
  );
};

export default DistributionChart;
