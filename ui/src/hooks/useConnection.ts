import { useState, useEffect, useCallback } from 'react';
import { api, HostInfo } from '../api/client';

export type ConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'disconnected';

export function useConnection(host: string) {
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [hosts, setHosts] = useState<HostInfo[]>([]);

  const refreshStatus = useCallback(async () => {
    try {
      const resp = await fetch(`/api/v1/hosts/${host}/status`, { credentials: 'include' });
      if (resp.ok) {
        const data = await resp.json();
        setStatus(data.status as ConnectionStatus);
      }
    } catch {
      setStatus('disconnected');
    }
  }, [host]);

  const loadHosts = useCallback(async () => {
    try {
      const data = await api.listHosts();
      setHosts(data.hosts);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadHosts();
    refreshStatus();
    const interval = setInterval(refreshStatus, 5000);
    return () => clearInterval(interval);
  }, [refreshStatus, loadHosts]);

  return { status, hosts, refreshStatus };
}
