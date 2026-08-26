import { ConnectionStatus } from '../hooks/useConnection';

interface Props {
  host: string;
  status: ConnectionStatus;
}

export function StatusBar({ host, status }: Props) {
  const statusClass = `status-${status}`;
  const statusText = status.charAt(0).toUpperCase() + status.slice(1);

  return (
    <div className="status-bar">
      <span className={`status-indicator ${statusClass}`} />
      <span>{host}</span>
      <span style={{ color: 'var(--text-secondary)' }}>— {statusText}</span>
    </div>
  );
}
