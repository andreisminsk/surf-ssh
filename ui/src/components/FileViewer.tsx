import { useState, useEffect, useRef } from 'react';
import { api, FileStat } from '../api/client';
import { MarkdownViewer } from './MarkdownViewer';
import { HtmlViewer } from './HtmlViewer';

const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico'];

const TEXT_EXTS = [
  'txt', 'md', 'markdown', 'log', 'csv', 'json', 'xml', 'yaml', 'yml',
  'ini', 'conf', 'cfg', 'toml', 'env', 'properties',
  'py', 'js', 'ts', 'tsx', 'jsx', 'css', 'scss', 'less',
  'html', 'htm', 'sh', 'bash', 'zsh', 'fish',
  'sql', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs', 'rb', 'php',
  'swift', 'kt', 'scala', 'lua', 'pl', 'r', 'dart', 'vue', 'svelte',
  'dockerfile', 'makefile', 'gitignore', 'gitattributes',
  'diff', 'patch', 'lock', 'gradle',
  'plist', 'bat', 'cmd', 'ps1', 'psm1',
  'service', 'socket', 'target', 'timer', 'mount', 'automount',
];

const VIEWABLE_EXTS = new Set([...IMAGE_EXTS, ...TEXT_EXTS]);

// Module-level cache for file content (survives across component remounts)
const fileContentCache = new Map<string, string>();

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDate(d: string | undefined): string {
  if (!d) return '—';
  const date = new Date(d);
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

interface Props {
  host: string;
  path: string;
}

export function FileViewer({ host, path }: Props) {
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [forceText, setForceText] = useState(false);
  const [stat, setStat] = useState<FileStat | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const ext = path.split('.').pop()?.toLowerCase() || '';
  const isImage = IMAGE_EXTS.includes(ext);
  const cacheKey = `${host}:${path}`;

  // Reset forceText when path changes
  useEffect(() => { setForceText(false); }, [path]);

  // Fetch file metadata immediately (independent of content loading)
  useEffect(() => {
    setStat(null);
    api.getStat(host, path)
      .then(s => setStat(s))
      .catch(() => setStat(null));
  }, [host, path, refreshKey]);

  // Load content
  useEffect(() => {
    // Cancel any in-flight request
    if (abortRef.current) {
      abortRef.current.abort();
    }

    if (isImage) {
      setLoading(true);
      setError(null);
      return;
    }

    // Check cache first (skip on refresh)
    if (refreshKey === 0) {
      const cached = fileContentCache.get(cacheKey);
      if (cached !== undefined) {
        setContent(cached);
        setLoading(false);
        return;
      }
    } else {
      fileContentCache.delete(cacheKey);
    }

    setLoading(true);
    setError(null);

    const controller = new AbortController();
    abortRef.current = controller;

    api.getFile(host, path, controller.signal)
      .then(text => {
        if (!controller.signal.aborted) {
          fileContentCache.set(cacheKey, text);
          setContent(text);
          setLoading(false);
        }
      })
      .catch(e => {
        if (e instanceof Error && e.name === 'AbortError') return;
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : 'Failed to load file');
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [host, path, isImage, cacheKey, refreshKey]);

  // Stop loading
  const stopLoading = () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    if (imgRef.current) {
      imgRef.current.src = '';
    }
    setLoading(false);
    setError('Loading cancelled');
  };

  const fileName = stat?.name || path.split('/').pop() || path;

  const metaBar = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 12px', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)', fontSize: '0.75rem', color: 'var(--text-secondary)', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        <span style={{ color: 'var(--text-primary)', fontWeight: 500, whiteSpace: 'nowrap' }}>{fileName}</span>
        {stat && <span style={{ whiteSpace: 'nowrap' }}>{formatSize(stat.size)}</span>}
        {stat && <span style={{ whiteSpace: 'nowrap' }}>Modified: {formatDate(stat.modified)}</span>}
        {!stat && <span style={{ color: 'var(--text-secondary)' }}>Loading metadata...</span>}
      </div>
      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          <button onClick={stopLoading} style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--red)', padding: '2px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem' }}>⏹ Stop</button>
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
          <a href={api.getDownloadUrl(host, path)} download style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--accent)', padding: '2px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', textDecoration: 'none' }}>⬇</a>
          <button onClick={() => setRefreshKey(k => k + 1)} style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '2px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem' }}>🔄</button>
        </div>
      )}
    </div>
  );

  if (error && !loading) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {metaBar}
        <div className="viewer-container">
          <div className="error-banner">{error}</div>
        </div>
      </div>
    );
  }

  if (isImage) {
    const imgUrl = api.getFileUrl(host, path) + (refreshKey > 0 ? `&_r=${refreshKey}` : '');
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {metaBar}
        <div className="viewer-container" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', position: 'relative', flex: 1 }}>
          {loading && (
            <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)' }}>
              <span className="loading-spinner" />
            </div>
          )}
          <img
            ref={imgRef}
            src={imgUrl}
            alt={path}
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', opacity: loading ? 0 : 1 }}
            onLoad={() => setLoading(false)}
            onError={() => { setError('Failed to load image'); setLoading(false); }}
          />
        </div>
      </div>
    );
  }

  if (ext === 'md' || ext === 'markdown') {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {metaBar}
        <div className="viewer-container" style={{ position: 'relative', flex: 1 }}>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
              <span className="loading-spinner" />
            </div>
          ) : (
            <MarkdownViewer content={content} host={host} remotePath={path} />
          )}
        </div>
      </div>
    );
  }

  if (ext === 'html' || ext === 'htm') {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {metaBar}
        <div className="viewer-container" style={{ position: 'relative', flex: 1 }}>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
              <span className="loading-spinner" />
            </div>
          ) : (
            <HtmlViewer content={content} />
          )}
        </div>
      </div>
    );
  }

  // Non-viewable binary files — show download option with "View as Text"
  if (!VIEWABLE_EXTS.has(ext) && !forceText) {
    const downloadUrl = api.getDownloadUrl(host, path);
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {metaBar}
        <div className="viewer-container" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: '1rem' }}>
          <span style={{ fontSize: '3rem' }}>📦</span>
          <span style={{ color: 'var(--text-secondary)' }}>Binary file — preview not available</span>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button onClick={() => setForceText(true)} style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '8px 16px', borderRadius: '4px', cursor: 'pointer' }}>📄 View as Text</button>
            <a href={downloadUrl} download style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--accent)', padding: '8px 16px', borderRadius: '4px', textDecoration: 'none', cursor: 'pointer' }}>⬇ Download</a>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {metaBar}
      <div className="viewer-container" style={{ position: 'relative', flex: 1 }}>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
            <span className="loading-spinner" />
          </div>
        ) : (
          <pre className="text-viewer">{content}</pre>
        )}
      </div>
    </div>
  );
}
