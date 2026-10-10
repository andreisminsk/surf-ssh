import { useState, useCallback, useRef } from 'react';
import { api, TreeResponse, TreeNode } from '../api/client';

const DEFAULT_LIMIT = 500;
const MAX_LIMIT = 5000;

export function useFileSystem(host: string) {
  const [tree, setTree] = useState<Record<string, TreeNode[]>>({});
  const [truncated, setTruncated] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const treeCacheRef = useRef<Record<string, TreeNode[]>>({});
  const truncatedCacheRef = useRef<Record<string, boolean>>({});
  const inflightRef = useRef<Map<string, Promise<void>>>(new Map());

  const fetchDir = useCallback(async (path: string, limit: number) => {
    // Dedup concurrent fetches for the same path (rapid toggles, refresh
    // during an in-flight load) — one request per path at a time.
    const existing = inflightRef.current.get(path);
    if (existing) return existing;
    const p = (async () => {
      setLoading(prev => ({ ...prev, [path]: true }));
      setError(null);
      try {
        const data: TreeResponse = await api.getTree(host, path, 1, limit);
        treeCacheRef.current[path] = data.children;
        truncatedCacheRef.current[path] = data.truncated;
        setTree(prev => ({ ...prev, [path]: data.children }));
        setTruncated(prev => ({ ...prev, [path]: data.truncated }));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load directory');
      } finally {
        setLoading(prev => ({ ...prev, [path]: false }));
      }
    })();
    inflightRef.current.set(path, p);
    p.then(
      () => { inflightRef.current.delete(path); },
      () => { inflightRef.current.delete(path); },
    );
    return p;
  }, [host]);

  const loadChildren = useCallback((path: string) => {
    // Cached → paint instantly (explicit refresh goes through refreshTree/refreshAll)
    if (treeCacheRef.current[path]) {
      setTree(prev => ({ ...prev, [path]: treeCacheRef.current[path] }));
      setTruncated(prev => ({ ...prev, [path]: truncatedCacheRef.current[path] ?? false }));
      return Promise.resolve();
    }
    return fetchDir(path, DEFAULT_LIMIT);
  }, [fetchDir]);

  const loadMore = useCallback((path: string) => {
    // Re-fetch past the default cap with the backend max limit
    return fetchDir(path, MAX_LIMIT);
  }, [fetchDir]);

  const selectFile = useCallback((path: string) => {
    setSelectedFile(path);
  }, []);

  const invalidate = useCallback((paths: string[]) => {
    for (const p of paths) {
      delete treeCacheRef.current[p];
      delete truncatedCacheRef.current[p];
    }
    setTree(prev => {
      const next: Record<string, TreeNode[]> = {};
      for (const [k, v] of Object.entries(prev)) {
        if (!paths.includes(k)) next[k] = v;
      }
      return next;
    });
    setTruncated(prev => {
      const next: Record<string, boolean> = {};
      for (const [k, v] of Object.entries(prev)) {
        if (!paths.includes(k)) next[k] = v;
      }
      return next;
    });
  }, []);

  const refreshTree = useCallback(async (path: string, expandedDescendants: string[] = []) => {
    // Invalidate the folder and everything below it, then reload the folder
    // plus any expanded descendants — visible (expanded) content must not
    // go blank or stale after the refresh.
    const keysToDelete = Object.keys(treeCacheRef.current).filter(k => k === path || k.startsWith(path + '/'));
    invalidate(keysToDelete);
    const targets = new Set<string>([path]);
    for (const d of expandedDescendants) {
      if (d !== path && d.startsWith(path + '/')) targets.add(d);
    }
    await Promise.all([...targets].map(p => fetchDir(p, DEFAULT_LIMIT)));
  }, [fetchDir, invalidate]);

  const refreshAll = useCallback(async (paths: string[]) => {
    invalidate(paths);
    // Parallel: each path is independent and setState updates are functional
    // (race-free); the backend SFTP semaphore bounds per-host concurrency.
    await Promise.all(paths.map(p => fetchDir(p, DEFAULT_LIMIT)));
  }, [fetchDir, invalidate]);

  const clearError = useCallback(() => setError(null), []);

  return { tree, truncated, loading, selectedFile, error, clearError, loadChildren, loadMore, selectFile, setSelectedFile, refreshTree, refreshAll };
}
