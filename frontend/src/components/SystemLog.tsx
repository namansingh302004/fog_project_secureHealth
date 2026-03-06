interface SystemLogProps {
  fogStats: { total_beats: number; bandwidth_saved_pct: number; avg_inference_ms: number } | null;
}

const SystemLog = ({ fogStats }: SystemLogProps) => {
  return (
    <div className="panel log-panel">
      <div className="panel-title">⟨ <span>SYSTEM LOG</span> ⟩ — FOG NODE</div>
      <div id="sys-log">
        <div className="log-line">
          <span className="log-ts">{new Date().toLocaleTimeString('en-GB')}</span>
          <span className="log-msg-info">Dashboard initialised</span>
        </div>
        <div className="log-line">
          <span className="log-ts">{new Date().toLocaleTimeString('en-GB')}</span>
          <span className="log-msg-info">Connecting to Fog Gateway (9001) & Cloud (8080)...</span>
        </div>
        {fogStats && (
          <div className="log-line">
            <span className="log-ts">{new Date().toLocaleTimeString('en-GB')}</span>
            <span className="log-msg-ok">{`Fog: ${fogStats.total_beats} beats | ${fogStats.bandwidth_saved_pct}% BW saved | avg ${fogStats.avg_inference_ms}ms`}</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default SystemLog;
