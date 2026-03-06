import { useState, useEffect } from 'react';
import type { Alert } from '../types';

const CLOUD_API = 'http://127.0.0.1:8080/api';
const FOG_STATS = 'http://127.0.0.1:9001/stats';
const REFRESH_MS = 2000;

interface Stats {
  total_alerts: number;
  avg_inference_ms: number;
}

interface FogStats {
  total_beats: number;
  bandwidth_saved_pct: number;
  avg_inference_ms: number;
}

export const useDataFetching = () => {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [fogStats, setFogStats] = useState<FogStats | null>(null);
  const [cloudOnline, setCloudOnline] = useState(false);
  const [fogOnline, setFogOnline] = useState(false);

  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const res = await fetch(`${CLOUD_API}/alerts?limit=30`, { signal: AbortSignal.timeout(3000) });
        const data = await res.json();
        setCloudOnline(true);
        if (data.length) {
          setAlerts(data);
        }
      } catch (e) {
        setCloudOnline(false);
      }
    };

    const fetchStats = async () => {
      try {
        const res = await fetch(`${CLOUD_API}/stats`, { signal: AbortSignal.timeout(3000) });
        const data = await res.json();
        setCloudOnline(true);
        setStats(data);
      } catch (e) {
        // cloudOnline is already set by fetchAlerts
      }
    };

    const fetchFogStats = async () => {
      try {
        const res = await fetch(FOG_STATS, { signal: AbortSignal.timeout(2000) });
        const data = await res.json();
        setFogOnline(true);
        setFogStats(data);
      } catch (e) {
        setFogOnline(false);
      }
    };

    const tick = () => {
      fetchAlerts();
      fetchStats();
      fetchFogStats();
    };

    tick();
    const intervalId = setInterval(tick, REFRESH_MS);

    return () => clearInterval(intervalId);
  }, []);

  return { alerts, stats, fogStats, cloudOnline, fogOnline };
};
