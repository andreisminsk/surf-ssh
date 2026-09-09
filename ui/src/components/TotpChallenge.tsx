import { useState } from 'react';

interface Props {
  onAuthenticated: () => void;
}

export function TotpChallenge({ onAuthenticated }: Props) {
  const [code, setCode] = useState('');
  const [useBackup, setUseBackup] = useState(false);
  const [error, setError] = useState('');
  const [locked, setLocked] = useState(0);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || locked) return;
    setBusy(true);
    setError('');
    try {
      const body = useBackup
        ? { backup_code: code.trim() }
        : { code: code.trim() };
      const resp = await fetch('/api/v1/auth/verify-totp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        onAuthenticated();
        return;
      }
      if (resp.status === 429) {
        const data = await resp.json().catch(() => ({ retry_after: 60 }));
        setLocked(data.retry_after || 60);
        setError(`Too many attempts — retry in ${data.retry_after || 60}s`);
      } else if (resp.status === 404) {
        setError('2FA is not enabled on this daemon — use surf-ssh url');
      } else {
        setError('Invalid code');
        setCode('');
      }
    } catch {
      setError('Connection failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <form onSubmit={submit} style={{ textAlign: 'center', maxWidth: 360, width: '90%' }}>
        <h1>🔐 surf ssh</h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
          {useBackup ? 'Enter a backup code' : 'Enter the 6-digit code from your authenticator app'}
        </p>
        <input
          autoFocus
          value={code}
          onChange={(e) => {
            const v = e.target.value;
            setCode(useBackup ? v : v.replace(/\D/g, '').slice(0, 6));
            setError('');
          }}
          placeholder={useBackup ? 'xxxx-xxxx' : '••••••'}
          inputMode={useBackup ? 'text' : 'numeric'}
          style={{
            width: '100%',
            padding: '0.75rem',
            fontSize: '1.4rem',
            textAlign: 'center',
            letterSpacing: useBackup ? '0.1em' : '0.5em',
            background: 'var(--bg-secondary, #1e1e2e)',
            border: '1px solid var(--border, #333346)',
            borderRadius: '6px',
            color: 'var(--text, #cdd6f4)',
            marginBottom: '0.75rem',
          }}
        />
        {error && <p style={{ color: 'var(--error, #e06c75)', marginBottom: '0.75rem' }}>{error}</p>}
        <button
          type="submit"
          disabled={busy || locked > 0 || code.length < (useBackup ? 9 : 6)}
          style={{
            width: '100%',
            padding: '0.75rem',
            background: 'var(--accent, #89b4fa)',
            border: 'none',
            borderRadius: '6px',
            color: '#1e1e2e',
            fontSize: '1rem',
            fontWeight: 600,
            cursor: locked || busy ? 'wait' : 'pointer',
          }}
        >
          {busy ? 'Verifying…' : locked > 0 ? `Locked (${locked}s)` : 'Verify'}
        </button>
        <button
          type="button"
          onClick={() => { setUseBackup(!useBackup); setCode(''); setError(''); }}
          style={{
            marginTop: '0.75rem',
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '0.85rem',
            textDecoration: 'underline',
          }}
        >
          {useBackup ? 'Use authenticator code' : 'Use backup code'}
        </button>
      </form>
    </div>
  );
}
