import { useState, useCallback, useRef } from 'react';
import { api, TreeResponse, TreeNode } from '../api/client';

export function useFileSystem(host: string) {
  const [tree, setTree] = useState<Record<string, TreeNode[]>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const treeCacheRef = useRef<Record<string, TreeNode[]>>({});

  const loadChildren = useCallback(async (path: string) => {
    // Return cached tree if available
    if (treeCacheRef.current[path]) {
      setTree(prev => ({ ...prev, [path]: treeCacheRef.current[path] }));
      return;
    }
    setLoading(prev => ({ ...prev, [path]: true }));
    setError(null);
    try {
      const data: TreeResponse = await api.getTree(host, path, 1, 500);
      treeCacheRef.current[path] = data.children;
      setTree(prev => ({ ...prev, [path]: data.children }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load directory');
    } finally {
      setLoading(prev => ({ ...prev, [path]: false }));
    }
  }, [host]);

  const selectFile = useCallback((path: string) => {
    setSelectedFile(path);
  }, []);

  const refreshTree = useCallback(async (path: string) => {
    // Clear cache for this path and all descendants, then reload
    const keysToDelete = Object.keys(treeCacheRef.current).filter(k => k === path || k.startsWith(path + '/'));
    for (const k of keysToDelete) {
      delete treeCacheRef.current[k];
    }
    setTree(prev => {
      const next: Record<string, TreeNode[]> = {};
      for (const [k, v] of Object.entries(prev)) {
        if (k !== path && !k.startsWith(path + '/')) {
          next[k] = v;
        }
      }
      return next;
    });
    setLoading(prev => ({ ...prev, [path]: true }));
    setError(null);
    try {
      const data: TreeResponse = await api.getTree(host, path, 1, 500);
      treeCacheRef.current[path] = data.children;
      setTree(prev => ({ ...prev, [path]: data.children }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load directory');
    } finally {
      setLoading(prev => ({ ...prev, [path]: false }));
    }
  }, [host]);

  const refreshAll = useCallback(async (paths: string[]) => {
    // Clear cache for all given paths
    for (const p of paths) {
      delete treeCacheRef.current[p];
    }
    setTree(prev => {
      const next: Record<string, TreeNode[]> = {};
      for (const [k, v] of Object.entries(prev)) {
        if (!paths.includes(k)) {
          next[k] = v;
        }
      }
      return next;
    });
    // Reload each path sequentially to avoid race conditions
    for (const p of paths) {
      setLoading(prev => ({ ...prev, [p]: true }));
      try {
        const data: TreeResponse = await api.getTree(host, p, 1, 500);
        treeCacheRef.current[p] = data.children;
        setTree(prev => ({ ...prev, [p]: data.children }));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load directory');
      } finally {
        setLoading(prev => ({ ...prev, [p]: false }));
      }
    }
  }, [host]);

  return { tree, loading, selectedFile, error, loadChildren, selectFile, setSelectedFile, refreshTree, refreshAll };
}
