import { useEffect, useRef, useState } from 'react';
import { useTerminalSession } from '../hooks/useTerminal';
import { api } from '../api/client';

interface Props {
  host: string;
  active: boolean;
}

export function TerminalView({ host, active }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wsUrl = api.getTerminalWsUrl(host);
  const { connect, disconnect, reconnect, termRef } = useTerminalSession(wsUrl, containerRef);
  const [reconnectKey, setReconnectKey] = useState(0);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect, reconnectKey]);

  // Focus terminal when tab becomes active
  useEffect(() => {
    if (active && termRef.current) {
      // Small delay to ensure the container is visible
      const timer = setTimeout(() => termRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [active, termRef]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '2px 8px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
        <button
          onClick={() => { reconnect(); setReconnectKey(k => k + 1); }}
          style={{ background: 'none', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '2px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem' }}
        >
          🔄 Reconnect
        </button>
      </div>
      <div ref={containerRef} className="terminal-container" />
    </div>
  );
}
