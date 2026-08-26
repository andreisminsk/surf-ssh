import { useEffect, useRef, useState } from 'react';
import { useTerminalSession } from '../hooks/useTerminal';
import { api } from '../api/client';

interface Props {
  active: boolean;
}

export function LocalConsoleView({ active }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [shells, setShells] = useState<{ id: string; name: string }[]>([]);
  const [selectedShell, setSelectedShell] = useState('default');
  const [reconnectKey, setReconnectKey] = useState(0);

  // Fetch available shells on mount
  useEffect(() => {
    api.getLocalShells()
      .then(data => {
        setShells(data.shells);
        setSelectedShell(data.default);
      })
      .catch(() => {
        setShells([{ id: 'default', name: 'Default' }]);
      });
  }, []);

  const wsUrl = api.getLocalTerminalWsUrl(selectedShell);
  const { connect, disconnect, reconnect, termRef } = useTerminalSession(wsUrl, containerRef, false);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect, reconnectKey, selectedShell]);

  // Focus terminal when tab becomes active
  useEffect(() => {
    if (active && termRef.current) {
      const timer = setTimeout(() => termRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [active, termRef]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '8px', padding: '2px 8px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Shell:</label>
        <select
          value={selectedShell}
          onChange={(e) => { setSelectedShell(e.target.value); setReconnectKey(k => k + 1); }}
          style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '2px 6px', borderRadius: '4px', fontSize: '0.8rem', cursor: 'pointer' }}
        >
          {shells.map(s => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
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
