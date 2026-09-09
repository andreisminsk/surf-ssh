import { useState, useEffect, useRef, useCallback } from 'react';
import { FileTree } from './components/FileTree';
import { FileViewer } from './components/FileViewer';
import { TerminalView } from './components/TerminalView';
import { LocalConsoleView } from './components/LocalConsoleView';
import { StatusBar } from './components/StatusBar';
import {
  FileTextIcon, MonitorIcon, TerminalIcon, CloseIcon,
  PlusIcon, ChevronDownIcon,
} from './components/icons';
import { PasswordPrompt } from './components/PasswordPrompt';
import { TotpChallenge } from './components/TotpChallenge';
import { useConnection } from './hooks/useConnection';
import { useFileSystem } from './hooks/useFileSystem';
import { useLiveness } from './hooks/useLiveness';
import { api, HostInfo, setAuthFlowErrorHandler } from './api/client';

interface Tab {
  id: string;
  type: 'files' | 'remote' | 'local';
}

const MAX_REMOTE = 3;
const MAX_LOCAL = 3;

let tabCounter = 0;
function nextId() {
  tabCounter += 1;
  return `tab-${tabCounter}`;
}

function tabTitle(tab: Tab, indexAmongType: number): string {
  const suffix = indexAmongType > 0 ? ` #${indexAmongType + 1}` : '';
  if (tab.type === 'files') return 'File Preview';
  if (tab.type === 'remote') return `Remote Terminal${suffix}`;
  return `Local Console${suffix}`;
}

function tabIcon(tab: Tab) {
  if (tab.type === 'files') return <FileTextIcon color="var(--accent)" size={14} />;
  if (tab.type === 'remote') return <TerminalIcon color="var(--green)" size={14} />;
  return <MonitorIcon color="var(--yellow)" size={14} />;
}

function AddHostForm({ onAdded }: { onAdded: (alias: string) => void }) {
  const [hostname, setHostname] = useState('');
  const [user, setUser] = useState('');
  const [port, setPort] = useState('22');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !hostname.trim()) return;
    setBusy(true);
    setError('');
    try {
      const resp = await fetch('/api/v1/hosts/adhoc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          hostname: hostname.trim(),
          user: user.trim() || null,
          port: parseInt(port, 10) || 22,
        }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(data.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      onAdded(data.alias);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    padding: '0.5rem',
    background: 'var(--bg-secondary, #1e1e2e)',
    border: '1px solid var(--border, #333346)',
    borderRadius: '6px',
    color: 'var(--text, #cdd6f4)',
    width: '100%',
  };

  return (
    <form onSubmit={submit} style={{ marginTop: '1.5rem', textAlign: 'left' }}>
      <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: '0.5rem' }}>
        Add a host without ~/.ssh/config (password auth):
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input style={inputStyle} placeholder="user (optional)" value={user} onChange={e => setUser(e.target.value)} />
        <input style={inputStyle} placeholder="hostname" value={hostname} onChange={e => setHostname(e.target.value)} required />
        <input style={{ ...inputStyle, width: 80 }} placeholder="port" value={port} onChange={e => setPort(e.target.value.replace(/\D/g, ''))} />
      </div>
      {error && <div style={{ color: 'var(--error, #e06c75)', fontSize: '0.85rem', marginBottom: '0.5rem' }}>{error}</div>}
      <button type="submit" disabled={busy || !hostname.trim()} style={{
        padding: '0.5rem 1rem',
        background: 'var(--accent, #89b4fa)',
        border: 'none',
        borderRadius: '6px',
        color: '#1e1e2e',
        cursor: busy ? 'wait' : 'pointer',
        width: '100%',
      }}>
        {busy ? 'Adding…' : 'Add host'}
      </button>
    </form>
  );
}

function HostPicker({ onPick }: { onPick: (host: string) => void }) {
  const [hosts, setHosts] = useState<HostInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);

  useEffect(() => {
    api.listHosts()
      .then(data => { setHosts(data.hosts); setLoading(false); })
      .catch(e => {
        setError(
          e.message.includes('Unauthorized')
            ? 'Unauthorized — run surf-ssh url to connect.'
            : e.message
        );
        setLoading(false);
      });
  }, []);

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <div style={{ textAlign: 'center', maxWidth: 500, width: '90%' }}>
        <h1>🏄 surf ssh</h1>
        <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem', marginBottom: '1.5rem' }}>
          Select a host to connect
        </p>
        {loading && <p style={{ color: 'var(--text-secondary)' }}>Loading hosts…</p>}
        {error && <p style={{ color: 'var(--error, #e06c75)' }}>Error: {error}</p>}
        {!loading && !error && hosts.length === 0 && (
          <p style={{ color: 'var(--text-secondary)' }}>
            No hosts found in ~/.ssh/config
          </p>
        )}
        {!loading && !error && hosts.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {hosts.map(h => (
              <button
                key={h.host}
                onClick={() => onPick(h.host)}
                style={{
                  padding: '0.75rem 1rem',
                  background: 'var(--bg-secondary, #1e1e2e)',
                  border: '1px solid var(--border, #333346)',
                  borderRadius: '6px',
                  color: 'var(--text, #cdd6f4)',
                  cursor: 'pointer',
                  fontSize: '0.95rem',
                  textAlign: 'left',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span style={{ color: 'var(--accent, #89b4fa)' }}>{h.host}</span>
                <span style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
                  {h.status === 'connected' ? '🟢 connected' : '⚪ disconnected'}
                </span>
              </button>
            ))}
          </div>
        )}
        <div style={{ marginTop: '1rem' }}>
          <button onClick={() => setShowAdd(s => !s)} style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '0.85rem',
            textDecoration: 'underline',
          }}>
            {showAdd ? 'Cancel' : '+ Add host without config…'}
          </button>
          {showAdd && (
            <AddHostForm onAdded={(alias) => {
              const params = new URLSearchParams(window.location.search);
              params.set('host', alias);
              window.location.search = params.toString();
            }} />
          )}
        </div>
      </div>
    </div>
  );
}

function UnauthorizedScreen() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <div style={{ textAlign: 'center', maxWidth: 500, width: '90%' }}>
        <h1>🔒 Unauthorized</h1>
        <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
          This browser has no valid surf-ssh session.
        </p>
        <p style={{ color: 'var(--text-secondary)' }}>
          Run <code>surf-ssh url</code> in a terminal and open the URL it prints.
        </p>
      </div>
    </div>
  );
}

function App() {
  const params = new URLSearchParams(window.location.search);
  const host = params.get('host') || '';
  const urlPath = params.get('path') || '';
  const [unauthorized, setUnauthorized] = useState(false);
  const [totpEnabled, setTotpEnabled] = useState<boolean | null>(null);
  const [passwordHost, setPasswordHost] = useState<string | null>(null);

  // Check whether 2FA is available on this daemon (drives the 401 screen)
  useEffect(() => {
    fetch('/api/v1/auth/verify-totp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(r => setTotpEnabled(r.status !== 404))
      .catch(() => setTotpEnabled(false));
  }, []);

  // Route auth-flow taxonomy codes (password_required, host key) from any
  // host-scoped API call to the PasswordPrompt view
  useEffect(() => {
    setAuthFlowErrorHandler((host, detail) => {
      if (detail === 'password_required' || detail === 'invalid_password') {
        setPasswordHost(host);
      }
    });
    return () => setAuthFlowErrorHandler(null);
  }, []);

  // Daemon mode (no ?host= param): the host-scoped 401 probes below
  // early-return on empty host, so probe auth here — otherwise HostPicker
  // renders and swallows the 401 as an inline error instead of routing
  // to the TOTP challenge.
  useEffect(() => {
    if (host) return;
    fetch('/api/v1/hosts', { credentials: 'include' })
      .then(r => { if (r.status === 401) setUnauthorized(true); })
      .catch(() => {});
  }, [host]);
  const [rootPath, setRootPath] = useState(urlPath || '/');
  const [homePath, setHomePath] = useState(urlPath || '/');

  const { status } = useConnection(host);
  const { tree, loading, selectedFile, error, clearError, loadChildren, selectFile, refreshAll } = useFileSystem(host);
  useLiveness(host);
  const [platform, setPlatform] = useState('unix');
  const [sidebarWidth, setSidebarWidth] = useState(300);

  // --- Tab state ---
  const initialTabs: Tab[] = [
    { id: nextId(), type: 'files' },
    { id: nextId(), type: 'remote' },
    { id: nextId(), type: 'local' },
  ];
  const [tabs, setTabs] = useState<Tab[]>(initialTabs);
  const [activeTabId, setActiveTabId] = useState(initialTabs[0].id);
  const [mountedTabs, setMountedTabs] = useState<Set<string>>(new Set([initialTabs[0].id]));
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const newMenuRef = useRef<HTMLDivElement>(null);

  const remoteCount = tabs.filter(t => t.type === 'remote').length;
  const localCount = tabs.filter(t => t.type === 'local').length;

  // Update document title with host
  useEffect(() => {
    document.title = host ? `🏄 surf ssh - ${host}` : '🏄 surf ssh';
  }, [host]);

  // Fetch home directory and platform
  const fetchHome = useCallback(() => {
    if (!host) return;
    fetch(`/api/v1/hosts/${host}/home`, { credentials: 'include' })
      .then(r => {
        if (r.status === 401) setUnauthorized(true);
        return r.ok ? r.json() : null;
      })
      .then(data => {
        if (data?.home) {
          setRootPath(data.home);
          setHomePath(data.home);
        }
      })
      .catch(() => {});
  }, [host]);

  useEffect(() => {
    if (!host) return;
    if (!urlPath) {
      fetchHome();
    }
    fetch(`/api/v1/hosts/${host}/status`, { credentials: 'include' })
      .then(r => {
        if (r.status === 401) setUnauthorized(true);
        return r.ok ? r.json() : null;
      })
      .then(data => {
        if (data?.platform) setPlatform(data.platform);
      })
      .catch(() => {});
  }, [host, urlPath, fetchHome]);

  useEffect(() => {
    if (host && rootPath) {
      loadChildren(rootPath);
    }
  }, [host, rootPath, loadChildren]);

  // Close new-tab dropdown on outside click
  useEffect(() => {
    if (!newMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (newMenuRef.current && !newMenuRef.current.contains(e.target as Node)) {
        setNewMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [newMenuOpen]);

  // Sidebar resize
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      const newWidth = Math.max(200, Math.min(600, startWidth + ev.clientX - startX));
      setSidebarWidth(newWidth);
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // --- Tab actions ---
  function activateTab(id: string) {
    setActiveTabId(id);
    setMountedTabs(prev => new Set([...prev, id]));
  }

  function closeTab(id: string) {
    const tab = tabs.find(t => t.id === id);
    if (!tab) return;
    if (tab.type === 'files') return;
    if (tab.type === 'remote' && remoteCount <= 1) return;
    if (tab.type === 'local' && localCount <= 1) return;

    const idx = tabs.findIndex(t => t.id === id);
    const newTabs = tabs.filter(t => t.id !== id);
    setTabs(newTabs);
    setMountedTabs(prev => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    if (activeTabId === id) {
      const neighbor = newTabs[Math.min(idx, newTabs.length - 1)];
      setActiveTabId(neighbor.id);
    }
  }

  function addRemoteTab() {
    if (remoteCount >= MAX_REMOTE) return;
    const id = nextId();
    setTabs([...tabs, { id, type: 'remote' }]);
    activateTab(id);
    setNewMenuOpen(false);
  }

  function addLocalTab() {
    if (localCount >= MAX_LOCAL) return;
    const id = nextId();
    setTabs([...tabs, { id, type: 'local' }]);
    activateTab(id);
    setNewMenuOpen(false);
  }

  if (unauthorized) {
    if (totpEnabled) {
      return <TotpChallenge onAuthenticated={() => { setUnauthorized(false); refreshAll([]); }} />;
    }
    return <UnauthorizedScreen />;
  }

  if (passwordHost) {
    return (
      <PasswordPrompt
        host={passwordHost}
        onConnected={() => {
          setPasswordHost(null);
          clearError();
          // Re-fetch home (failed during the prompt) and reload the tree.
          // fetchHome sets rootPath, which retriggers the tree load effect.
          fetchHome();
        }}
      />
    );
  }

  if (!host) {
    return <HostPicker onPick={(h) => {
      const params = new URLSearchParams(window.location.search);
      params.set('host', h);
      window.location.search = params.toString();
    }} />;
  }

  // Index maps for title computation
  let remoteIdx = 0;
  let localIdx = 0;

  return (
    <div className="app-layout">
      <div className="app-body">
        <FileTree
          host={host}
          tree={tree}
          loading={loading}
          selectedFile={selectedFile}
          onToggle={loadChildren}
          onSelectFile={(p) => { selectFile(p); activateTab(tabs.find(t => t.type === 'files')!.id); }}
          onRefreshAll={refreshAll}
          rootPath={rootPath}
          homePath={homePath}
          onRootChange={setRootPath}
          platform={platform}
          width={sidebarWidth}
        />
        <div className="resize-handle" onMouseDown={startResize} />
        <div className="main-content">
          <div className="tab-bar">
            {tabs.map(tab => {
              const typeIdx = tab.type === 'remote' ? remoteIdx++ : tab.type === 'local' ? localIdx++ : 0;
              const canClose =
                (tab.type === 'remote' && remoteCount > 1) ||
                (tab.type === 'local' && localCount > 1);
              return (
                <div
                  key={tab.id}
                  className={`tab ${activeTabId === tab.id ? 'active' : ''}`}
                  onClick={() => activateTab(tab.id)}
                  style={{ display: 'flex', alignItems: 'center', gap: '4px' }}
                >
                   <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                     {tabIcon(tab)}
                     {tabTitle(tab, typeIdx)}
                   </span>
                   {canClose && (
                     <span
                      onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
                      style={{
                        cursor: 'pointer',
                        opacity: 0.6,
                        padding: '0 2px',
                        borderRadius: '3px',
                        display: 'inline-flex',
                        alignItems: 'center',
                       }}
                      onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
                      onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.6')}
                     >
                       <CloseIcon color="var(--text-secondary)" size={12} />
                     </span>
                   )}
                </div>
              );
            })}
            {/* New tab dropdown */}
            <div ref={newMenuRef} style={{ position: 'relative' }}>
              <div
                className={`tab ${newMenuOpen ? 'active' : ''}`}
                onClick={() => setNewMenuOpen(o => !o)}
                style={{ opacity: (remoteCount < MAX_REMOTE || localCount < MAX_LOCAL) ? 1 : 0.4, display: 'inline-flex', alignItems: 'center', gap: '4px' }}
               >
                 <PlusIcon color="var(--text-primary)" size={14} />
                 New
                 <ChevronDownIcon color="var(--text-secondary)" size={14} />
               </div>
              {newMenuOpen && (
                 <div style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  borderRadius: '4px',
                  zIndex: 1000,
                  minWidth: '200px',
                  whiteSpace: 'nowrap',
                  boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                 }}>
                  <div
                    onClick={addRemoteTab}
                    style={{
                      padding: '6px 12px',
                      cursor: remoteCount < MAX_REMOTE ? 'pointer' : 'not-allowed',
                      opacity: remoteCount < MAX_REMOTE ? 1 : 0.4,
                      fontSize: '0.85rem',
                      borderBottom: '1px solid var(--border)',
                      whiteSpace: 'nowrap',
                     }}
                    onMouseEnter={(e) => remoteCount < MAX_REMOTE && (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
                   >
                     <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                       <TerminalIcon color="var(--green)" size={14} />
                       Remote Terminal {remoteCount < MAX_REMOTE ? `(${remoteCount}/${MAX_REMOTE})` : '(max)'}
                     </span>
                   </div>
                  <div
                    onClick={addLocalTab}
                    style={{
                      padding: '6px 12px',
                      cursor: localCount < MAX_LOCAL ? 'pointer' : 'not-allowed',
                      opacity: localCount < MAX_LOCAL ? 1 : 0.4,
                      fontSize: '0.85rem',
                      whiteSpace: 'nowrap',
                     }}
                    onMouseEnter={(e) => localCount < MAX_LOCAL && (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
                   >
                     <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                       <MonitorIcon color="var(--yellow)" size={14} />
                       Local Console {localCount < MAX_LOCAL ? `(${localCount}/${MAX_LOCAL})` : '(max)'}
                     </span>
                   </div>
                </div>
              )}
            </div>
          </div>
          {error && <div className="error-banner">{error}</div>}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative' }}>
            {tabs.map(tab => {
              if (!mountedTabs.has(tab.id)) return null;
              const isActive = activeTabId === tab.id;
              return (
                <div
                  key={tab.id}
                  style={{
                    position: 'absolute',
                    inset: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    visibility: isActive ? 'visible' : 'hidden',
                  }}
                >
                  {tab.type === 'files' && (
                    selectedFile ? (
                      <FileViewer host={host} path={selectedFile} />
                    ) : (
                      <div className="viewer-container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
                        Select a file to view
                      </div>
                    )
                  )}
                  {tab.type === 'remote' && (
                    <TerminalView host={host} active={isActive} />
                  )}
                  {tab.type === 'local' && (
                    <LocalConsoleView active={isActive} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <StatusBar host={host} status={status} />
      <div style={{ position: 'fixed', bottom: '0.5rem', right: '1rem', fontSize: '0.85rem', color: 'rgba(205,214,244,0.4)', fontStyle: 'italic', pointerEvents: 'none', userSelect: 'none', zIndex: 9999 }}>
        Copyright (c) Andrei Suvorov 2026
      </div>
    </div>
  );
}

export default App;
