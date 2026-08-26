const API_BASE = '/api/v1';

export interface TreeNode {
  path: string;
  name: string;
  type: 'file' | 'directory';
  size?: number;
  modified?: string;
}

export interface TreeResponse {
  path: string;
  name: string;
  type: 'directory';
  truncated: boolean;
  children: TreeNode[];
}

export interface HostInfo {
  host: string;
  status: string;
  platform: string;
}

export interface HostsResponse {
  hosts: HostInfo[];
}

export interface FileStat {
  path: string;
  name: string;
  type: 'file' | 'directory';
  size: number;
  modified?: string;
  mode?: string;
}

async function apiFetch(path: string): Promise<Response> {
  const resp = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(detail.detail || `HTTP ${resp.status}`);
  }
  return resp;
}

export const api = {
  async listHosts(): Promise<HostsResponse> {
    const resp = await apiFetch('/hosts');
    return resp.json();
  },

  async getTree(host: string, path: string, depth = 1, limit = 500): Promise<TreeResponse> {
    const encoded = encodeURIComponent(path);
    const resp = await apiFetch(`/hosts/${host}/tree?path=${encoded}&depth=${depth}&limit=${limit}`);
    return resp.json();
  },

  getFileUrl(host: string, path: string): string {
    return `${API_BASE}/hosts/${host}/file?path=${encodeURIComponent(path)}`;
  },

  async getFile(host: string, path: string, signal?: AbortSignal): Promise<string> {
    const url = this.getFileUrl(host, path);
    const resp = await fetch(url, { credentials: 'include', signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.text();
  },

  async getStat(host: string, path: string): Promise<FileStat> {
    const encoded = encodeURIComponent(path);
    const resp = await apiFetch(`/hosts/${host}/stat?path=${encoded}`);
    return resp.json();
  },

  getDownloadUrl(host: string, path: string): string {
    return `${API_BASE}/hosts/${host}/download?path=${encodeURIComponent(path)}`;
  },

  getTerminalWsUrl(host: string): string {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}${API_BASE}/hosts/${host}/terminal`;
  },

  getLocalTerminalWsUrl(shell: string): string {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}${API_BASE}/local/terminal?shell=${encodeURIComponent(shell)}`;
  },

  getLivenessWsUrl(host: string): string {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}${API_BASE}/liveness?host=${encodeURIComponent(host)}`;
  },

  async getLocalShells(): Promise<{ shells: { id: string; name: string }[]; default: string }> {
    const resp = await apiFetch('/local/shells');
    return resp.json();
  },
};
