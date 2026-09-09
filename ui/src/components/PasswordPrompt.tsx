import { useState, useEffect } from 'react';

interface Props {
  host: string;
  onConnected: () => void;
}

/** Renders when the API reports password_required / invalid_password,
 *  or a host key needs TOFU confirmation. */
export function PasswordPrompt({ host, onConnected }: Props) {
  const [password, setPassword] = useState('');
  const [username, setUsername] = useState('');
  const [needsUsername, setNeedsUsername] = useState(false);
  const [error, setError] = useState('');
  const [locked, setLocked] = useState(0);
  const [busy, setBusy] = useState(false);
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [keyChanged, setKeyChanged] = useState(false);

  // Show a username field when the host has no User line in ~/.ssh/config
  useEffect(() => {
    fetch(`/api/v1/hosts/${encodeURIComponent(host)}/auth-info`, { credentials: 'include' })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (data) setNeedsUsername(!!data.needs_username); })
      .catch(() => {});
  }, [host]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || locked) return;
    setBusy(true);
    setError('');
    try {
      const resp = await fetch(`/api/v1/hosts/${encodeURIComponent(host)}/auth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          password,
          ...(needsUsername && username.trim() ? { username: username.trim() } : {}),
        }),
      });
      if (resp.ok) {
        onConnected();
        return;
      }
      if (resp.status === 419) {
        const detail = resp.headers.get('X-Fingerprint') || '';
        setFingerprint(detail);
        setKeyChanged(resp.json && (await resp.json().catch(() => ({}))).detail === 'host_key_changed');
        return;
      }
      if (resp.status === 423) {
        const data = await resp.json().catch(() => ({}));
        setLocked(data.detail ? 60 : 60);
        setError('Too many attempts — wait before retrying');
        return;
      }
      if (resp.status === 401) {
        setError('Invalid password');
        setPassword('');
        return;
      }
      const data = await resp.json().catch(() => ({ detail: resp.statusText }));
      setError(data.detail || `HTTP ${resp.status}`);
    } catch {
      setError('Connection failed');
    } finally {
      setBusy(false);
    }
  }

  async function trustKey() {
    if (!fingerprint) return;
    setBusy(true);
    try {
      const resp = await fetch(`/api/v1/hosts/${encodeURIComponent(host)}/trust`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ key_data: fingerprint }),
      });
      if (resp.ok) {
        setFingerprint(null);
        onConnected();
      } else {
        setError('Trust failed');
      }
    } finally {
      setBusy(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '0.75rem',
    background: 'var(--bg-secondary, #1e1e2e)',
    border: '1px solid var(--border, #333346)',
    borderRadius: '6px',
    color: 'var(--text, #cdd6f4)',
    marginBottom: '0.75rem',
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <div style={{ textAlign: 'center', maxWidth: 420, width: '90%' }}>
        <h1>🔑 {host}</h1>
        {fingerprint ? (
          <div>
            <p style={{ color: keyChanged ? 'var(--error, #e06c75)' : 'var(--text-secondary)' }}>
              {keyChanged
                ? '⚠️ HOST KEY CHANGED — possible man-in-the-middle attack!'
                : 'Host key unknown. Verify the fingerprint out-of-band:'}
            </p>
            <code style={{ display: 'block', margin: '0.75rem 0', wordBreak: 'break-all', fontSize: '0.85rem' }}>
              {fingerprint}
            </code>
            <button onClick={trustKey} disabled={busy} style={{
              padding: '0.75rem 1.5rem',
              background: keyChanged ? 'var(--error, #e06c75)' : 'var(--accent, #89b4fa)',
              border: 'none',
              borderRadius: '6px',
              color: '#1e1e2e',
              cursor: busy ? 'wait' : 'pointer',
              fontWeight: 600,
            }}>
              {keyChanged ? 'I understand the risk — trust new key' : 'Trust this key'}
            </button>
          </div>
        ) : (
          <form onSubmit={submit}>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
              This host requires a password:
            </p>
            {needsUsername && (
              <input
                type="text"
                value={username}
                onChange={(e) => { setUsername(e.target.value); setError(''); }}
                style={{ ...inputStyle, fontSize: '1rem' }}
                placeholder="Username"
                autoComplete="username"
              />
            )}
            <input
              autoFocus
              type="password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(''); }}
              style={{ ...inputStyle, fontSize: '1rem' }}
              placeholder="Password"
              autoComplete="current-password"
            />
            {error && <p style={{ color: 'var(--error, #e06c75)', marginBottom: '0.75rem' }}>{error}</p>}
            <button
              type="submit"
              disabled={busy || locked > 0 || !password || (needsUsername && !username.trim())}
              style={{
              width: '100%',
              padding: '0.75rem',
              background: 'var(--accent, #89b4fa)',
              border: 'none',
              borderRadius: '6px',
              color: '#1e1e2e',
              fontSize: '1rem',
              fontWeight: 600,
              cursor: busy || locked ? 'wait' : 'pointer',
            }}>
              {busy ? 'Connecting…' : locked > 0 ? `Locked (${locked}s)` : 'Connect'}
            </button>
            <p style={{ color: 'var(--text-secondary)', fontSize: '1rem', marginTop: '1rem' }}>
              Tip: to avoid password entry generate a key (<code>ssh-keygen</code>) and use{' '}
              <code>ssh-copy-id {host.replace('adhoc:', '')}</code> to enable it on the remote.
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
