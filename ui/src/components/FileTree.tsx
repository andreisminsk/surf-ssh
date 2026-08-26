import { useState, useEffect } from 'react';
import { TreeNode } from '../api/client';
import {
  FolderOpenIcon, FolderIcon, FileIcon,
  EyeIcon, EyeOffIcon, HomeIcon, GlobeIcon, RefreshIcon, SurfIcon,
} from './icons';

interface Props {
  host: string;
  tree: Record<string, TreeNode[]>;
  loading: Record<string, boolean>;
  selectedFile: string | null;
  onToggle: (path: string) => void;
  onSelectFile: (path: string) => void;
  onRefreshAll: (paths: string[]) => void;
  rootPath: string;
  homePath: string;
  onRootChange: (path: string) => void;
  platform: string;
  width?: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/** Convert an SFTP path to the native format for the remote platform. */
function toNativePath(path: string, platform: string): string {
  if (platform !== 'windows') return path;
  // SFTP on Windows uses /C:/Users/... or C:/Users/... — convert to C:\Users\...
  let native = path;
  // Remove leading slash before a drive letter: /C:/ → C:/
  native = native.replace(/^\/([A-Za-z]:)/, '$1');
  // Replace forward slashes with backslashes
  native = native.replace(/\//g, '\\');
  return native;
}

/**
 * Legacy synchronous clipboard write: hidden textarea + execCommand('copy').
 * MUST be invoked while the user-gesture activation is still fresh (no prior
 * await), or Safari will refuse it.
 */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.left = '0';
    ta.style.opacity = '0';
    ta.style.pointerEvents = 'none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Copy text to the clipboard, with a legacy fallback.
 *
 * Safari on macOS treats a self-signed-HTTPS page as a NON-secure context,
 * so `navigator.clipboard.writeText` is unavailable/rejected there. We
 * therefore pick the path SYNCHRONOUSLY based on `window.isSecureContext`:
 *
 *  - Secure context + Clipboard API available  → use the modern API. The
 *    `.writeText()` call itself captures the user activation at call time, so
 *    awaiting its result afterwards is fine.
 *  - Non-secure (e.g. Safari + self-signed HTTPS) → go straight to the
 *    synchronous `execCommand('copy')` path. Crucially there is NO await before
 *    it, so the gesture activation is still valid when execCommand runs.
 *
 * Returns true only when the copy actually succeeded.
 */
function copyToClipboard(text: string): Promise<boolean> {
  const secure = typeof window !== 'undefined' && window.isSecureContext;
  const hasModern =
    secure &&
    !!navigator.clipboard &&
    typeof navigator.clipboard.writeText === 'function';

  if (hasModern) {
    return navigator.clipboard
      .writeText(text)
      .then(() => true)
      // Rare double-failure: activation may be expired by now, but try anyway.
      .catch(() => legacyCopy(text));
  }

  // Non-secure context — run the legacy path synchronously (fresh activation).
  return Promise.resolve(legacyCopy(text));
}

export function FileTree({ host, tree, loading, selectedFile, onToggle, onSelectFile, onRefreshAll, rootPath, homePath, onRootChange, platform, width }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set([rootPath]));
  const [showHidden, setShowHidden] = useState(false);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);
  const [copiedPos, setCopiedPos] = useState<{ x: number; y: number } | null>(null);
  const [copyFailed, setCopyFailed] = useState<{ x: number; y: number } | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; path: string } | null>(null);

  const doCopy = async (path: string) => {
    const text = toNativePath(path, platform);
    const ok = await copyToClipboard(text);
    if (!ok) {
      setCopyFailed(ctxMenu ? { x: ctxMenu.x, y: ctxMenu.y } : { x: 0, y: 0 });
      setTimeout(() => setCopyFailed(null), 2500);
    } else {
      setCopiedPath(path);
      setCopiedPos(ctxMenu ? { x: ctxMenu.x, y: ctxMenu.y } : { x: 0, y: 0 });
      setTimeout(() => { setCopiedPath(null); setCopiedPos(null); }, 1500);
    }
    setCtxMenu(null);
  };
  const isFullTree = rootPath === '/';

  const switchToFull = () => {
    onRootChange('/');
    // Expand path to selected file so it remains visible
    if (selectedFile) {
      const parts = selectedFile.split('/').filter(Boolean);
      const pathsToExpand: string[] = [];
      let current = '';
      for (const part of parts) {
        current += '/' + part;
        pathsToExpand.push(current);
      }
      setExpanded(new Set(['/root', ...pathsToExpand]));
      // Load each ancestor
      for (const p of pathsToExpand) {
        onToggle(p);
      }
    } else {
      setExpanded(new Set(['/']));
    }
  };

  const switchToHome = () => {
    onRootChange(homePath);
    setExpanded(new Set([homePath]));
  };

  useEffect(() => {
    setExpanded(new Set([rootPath]));
  }, [rootPath]);

  const toggle = (path: string) => {
    const next = new Set(expanded);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
      onToggle(path);
    }
    setExpanded(next);
  };

  const renderNode = (node: TreeNode, depth: number = 0) => {
    // Filter hidden files unless showHidden is on
    if (!showHidden && node.name.startsWith('.')) return null;

    const isDir = node.type === 'directory';
    const isExpanded = expanded.has(node.path);
    const isSelected = selectedFile === node.path;

    return (
      <div key={node.path}>
        <div
          className={`tree-node ${isSelected ? 'selected' : ''}`}
          style={{ paddingLeft: `${0.5 + depth * 1}rem`, position: 'relative' }}
          onClick={() => isDir ? toggle(node.path) : onSelectFile(node.path)}
          onContextMenu={(e) => { e.preventDefault(); setCtxMenu({ x: e.clientX, y: e.clientY, path: node.path }); }}
          title={node.size != null ? `Size: ${formatSize(node.size)} · Right-click to copy path` : 'Right-click to copy path'}
        >
            <span className="tree-icon">
              {isDir
                ? (isExpanded ? <FolderOpenIcon color="var(--yellow)" /> : <FolderIcon color="var(--yellow)" />)
                : <FileIcon color="var(--accent)" />}
            </span>
          <span>{node.name}</span>
        </div>
        {isDir && isExpanded && (
          <div className="tree-children">
            {loading[node.path] && (
              <div className="tree-node" style={{ paddingLeft: '1.5rem' }}>
                <span className="loading-spinner" />
                <span style={{ color: 'var(--text-secondary)' }}>Loading...</span>
              </div>
            )}
            {tree[node.path]?.map(child => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="sidebar" style={width ? { width: `${width}px` } : undefined}>
      <div style={{ padding: '0.5rem', borderBottom: '1px solid var(--border)', fontWeight: 'bold', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
          <SurfIcon color="var(--accent)" size={16} />
          {host}
        </span>
        <div style={{ display: 'flex', gap: '4px' }}>
          <button
           onClick={() => setShowHidden(v => !v)}
           style={{ background: showHidden ? 'var(--bg-tertiary)' : 'none', border: '1px solid var(--border)', color: showHidden ? 'var(--accent)' : 'var(--text-primary)', padding: '2px 6px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center' }}
           title="Show hidden files"
          >
            {showHidden ? <EyeIcon color="var(--accent)" size={14} /> : <EyeOffIcon color="var(--text-primary)" size={14} />}
          </button>
          <button
           onClick={isFullTree ? switchToHome : switchToFull}
           style={{ background: isFullTree ? 'var(--bg-tertiary)' : 'none', border: '1px solid var(--border)', color: isFullTree ? 'var(--accent)' : 'var(--text-primary)', padding: '2px 6px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center' }}
           title={isFullTree ? 'Show home directory' : 'Show full filesystem'}
          >
            {isFullTree ? <HomeIcon color="var(--accent)" size={14} /> : <GlobeIcon color="var(--text-primary)" size={14} />}
          </button>
          <button
           onClick={() => onRefreshAll(Array.from(expanded))}
           style={{ background: 'none', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '2px 6px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center' }}
           title="Refresh tree"
          >
            <RefreshIcon color="var(--text-primary)" size={14} />
          </button>
        </div>
      </div>
      {copiedPath && copiedPos && (
        <div style={{
          position: 'fixed', left: copiedPos.x + 8, top: copiedPos.y - 8,
          background: 'var(--accent)', color: 'var(--bg-primary)',
          padding: '2px 8px', borderRadius: '4px', fontSize: '0.8rem',
          whiteSpace: 'nowrap', zIndex: 9999, pointerEvents: 'none',
          boxShadow: '0 2px 6px rgba(0,0,0,0.3)',
        }}>✓ copied</div>
      )}
      {copyFailed && (
        <div style={{
          position: 'fixed', left: copyFailed.x + 8, top: copyFailed.y - 8,
          background: 'var(--red)', color: 'var(--bg-primary)',
          padding: '2px 8px', borderRadius: '4px', fontSize: '0.8rem',
          whiteSpace: 'nowrap', zIndex: 9999, pointerEvents: 'none',
          boxShadow: '0 2px 6px rgba(0,0,0,0.3)',
        }}>⚠ copy failed</div>
      )}
      {ctxMenu && (
        <div
          style={{ position: 'fixed', inset: 0, zIndex: 9998 }}
          onClick={() => setCtxMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null); }}
        />
      )}
      {ctxMenu && (
        <div style={{
          position: 'fixed', left: ctxMenu.x, top: ctxMenu.y,
          background: 'var(--bg-secondary)', border: '1px solid var(--border)',
          borderRadius: '6px', padding: '4px 0', zIndex: 9999,
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)', minWidth: '140px',
        }}>
          <button
            onClick={() => doCopy(ctxMenu.path)}
            style={{
              display: 'block', width: '100%', padding: '6px 12px',
              background: 'none', border: 'none', color: 'var(--text-primary)',
              cursor: 'pointer', textAlign: 'left', fontSize: '0.85rem',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
          >
            📋 Copy path
          </button>
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {tree[rootPath]?.map(node => renderNode(node))}
        {loading[rootPath] && !tree[rootPath] && (
          <div className="tree-node">
            <span className="loading-spinner" />
            <span>Loading...</span>
          </div>
        )}
      </div>
    </div>
  );
}
