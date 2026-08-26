import { useState, useEffect, useRef } from 'react';
import { FileTree } from './components/FileTree';
import { FileViewer } from './components/FileViewer';
import { TerminalView } from './components/TerminalView';
import { LocalConsoleView } from './components/LocalConsoleView';
import { StatusBar } from './components/StatusBar';
import {
  FileTextIcon, MonitorIcon, TerminalIcon, CloseIcon,
  PlusIcon, ChevronDownIcon,
} from './components/icons';
import { useConnection } from './hooks/useConnection';
import { useFileSystem } from './hooks/useFileSystem';
import { useLiveness } from './hooks/useLiveness';

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

function App() {
  const params = new URLSearchParams(window.location.search);
  const host = params.get('host') || '';
  const urlPath = params.get('path') || '';
  const [rootPath, setRootPath] = useState(urlPath || '/');
  const [homePath, setHomePath] = useState(urlPath || '/');

  const { status } = useConnection(host);
  const { tree, loading, selectedFile, error, loadChildren, selectFile, refreshAll } = useFileSystem(host);
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
  useEffect(() => {
    if (!host) return;
    if (!urlPath) {
      fetch(`/api/v1/hosts/${host}/home`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.home) {
            setRootPath(data.home);
            setHomePath(data.home);
          }
        })
        .catch(() => {});
    }
    fetch(`/api/v1/hosts/${host}/status`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.platform) setPlatform(data.platform);
      })
      .catch(() => {});
  }, [host, urlPath]);

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

  if (!host) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div style={{ textAlign: 'center' }}>
            <h1>🏄 surf ssh</h1>
          <p style={{ color: 'var(--text-secondary)', marginTop: '1rem' }}>
            No host specified. Usage: <code>surf-ssh open &lt;host-alias&gt;</code>
          </p>
        </div>
      </div>
    );
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
