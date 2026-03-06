import type { Alert } from '../types';

const AlertItem = ({ alert }: { alert: Alert }) => {
  const getAlertType = () => {
    switch (alert.true_label) {
      case 2: return 'pvc';
      case 1: return 'svp';
      case 3: return 'fusion';
      default: return 'other';
    }
  };

  const getAlertBadge = () => {
    switch (alert.true_label) {
      case 2: return <span className="alert-type-badge badge-pvc">PVC</span>;
      case 1: return <span className="alert-type-badge badge-svp">SVP</span>;
      case 3: return <span className="alert-type-badge badge-fusion">FUSION</span>;
      default: return <span className="alert-type-badge badge-other">UNCLASS</span>;
    }
  };

  return (
    <div className={`alert-item ${getAlertType()}`}>
      <span className="alert-time">{new Date(alert.cloud_ts * 1000).toLocaleTimeString('en-GB')}</span>
      {getAlertBadge()}
      <span className="alert-beat">#{alert.beat_id}</span>
      <span className="alert-score">IF:{alert.anomaly_score?.toFixed(3) ?? '?'}</span>
      <span className="alert-ms">{alert.inference_ms?.toFixed(1) ?? '?'}ms</span>
    </div>
  );
};

const AlertFeed = ({ alerts }: { alerts: Alert[] }) => {
  return (
    <div className="panel alert-panel">
      <div className="panel-title">⟨ <span>ANOMALY ALERT FEED</span> ⟩ — FOG → CLOUD</div>
      <div id="alert-feed">
        {alerts.length > 0 ? (
          alerts.map(alert => <AlertItem key={alert.id} alert={alert} />)
        ) : (
          <div style={{ color: 'var(--text-dim)', fontFamily: 'var(--mono)', fontSize: '11px', padding: '20px 0', textAlign: 'center' }}>
            Waiting for anomaly alerts from Fog Gateway...
          </div>
        )}
      </div>
    </div>
  );
};

export default AlertFeed;
