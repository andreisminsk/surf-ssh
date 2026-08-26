# Surf SSH - Agentic Coder Guidelines

## 1. Project Philosophy

This is a **local-first developer tool**. Prioritize:
- **Security:** SSH keys never leave the local machine. No cloud dependencies. No remote agents.
- **Simplicity:** Single binary distribution, zero configuration for basic usage.
- **Reliability:** Handle flaky SSH connections gracefully. Auto-reconnect. Never crash.
- **Cross-platform:** Must work identically on Windows 11 and macOS.

---

## 2. Project Structure

```
surf-ssh/
├── src/
│   ├── __init__.py
│   ├── main.py                 # CLI entry point (Typer)
│   ├── daemon/
│   │   ├── __init__.py
│   │   ├── server.py           # FastAPI app creation, startup, routes
│   │   ├── tls.py              # Self-signed CA/cert generation
│   │   └── static.py           # Static file serving for SPA
│   ├── ssh/
│   │   ├── __init__.py
│   │   ├── config_parser.py    # ~/.ssh/config parser
│   │   ├── connection_pool.py  # AsyncSSH connection manager
│   │   └── sftp_client.py      # SFTP operations wrapper
│   ├── api/
│   │   ├── __init__.py
│   │   ├── hosts.py            # /api/v1/hosts endpoints
│   │   ├── files.py            # /api/v1/hosts/{host}/file endpoints
│   │   ├── tree.py             # /api/v1/hosts/{host}/tree endpoints
│   │   ├── terminal.py         # WebSocket terminal endpoint (PTY relay)
│   │   └── models.py           # Pydantic request/response models
│   └── security/
│       ├── __init__.py
│       ├── path_validator.py   # Path traversal prevention
│       └── session_auth.py     # Local session token management
├── ui/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── FileTree.tsx
│   │   │   ├── FileViewer.tsx
│   │   │   ├── MarkdownViewer.tsx
│   │   │   ├── HtmlViewer.tsx
│   │   │   ├── TerminalView.tsx  # xterm.js terminal emulator
│   │   │   └── StatusBar.tsx
│   │   ├── hooks/
│   │   │   ├── useConnection.ts
│   │   │   ├── useFileSystem.ts
│   │   │   └── useTerminal.ts   # WebSocket terminal hook
│   │   └── api/
│   │       └── client.ts
│   ├── package.json
│   └── vite.config.ts
├── tests/
│   ├── unit/
│   └── integration/
├── pyproject.toml
└── README.md
```

---

## 3. Coding Standards

### 3.1 Python Style

- **Type hints everywhere.** All function signatures must include parameter and return types.
- **Async by default.** All I/O operations must be async. Never use synchronous SSH libraries (e.g., Paramiko) in the web server context.
- **No bare exceptions.** Catch specific exceptions; re-raise with context if swallowing.
- **No global state.** Use dependency injection via FastAPI's `Depends`.

```python
# GOOD
async def get_file(host: str, path: str, pool: ConnectionPool = Depends(get_pool)) -> StreamingResponse:
    ...

# BAD
async def get_file(host, path):
    conn = global_connections[host]  # No globals
    ...
```

### 3.2 Path Handling Rules

**This is the #1 source of bugs in this project. Follow these rules strictly.**

1. **Remote paths are ALWAYS POSIX.** Even when the local machine is Windows.
2. **Use `pathlib.PurePosixPath` for remote path manipulation.** Never use `os.path` for remote paths, as it uses local OS separators.
3. **Validate paths before SFTP calls.** Reject paths containing `..`. Resolve symlinks via `sftp.realpath()` on the remote server.
4. **URL-decode paths.** FastAPI path parameters containing `/` must be URL-decoded before passing to SFTP.

```python
# GOOD
from pathlib import PurePosixPath
from urllib.parse import unquote

async def safe_path(raw_path: str) -> str:
    decoded = unquote(raw_path)
    posix_path = PurePosixPath(decoded)
    if ".." in posix_path.parts:
        raise ValueError("Path traversal detected")
    return str(posix_path)

# BAD
import os
def safe_path(raw_path: str) -> str:
    return os.path.normpath(raw_path)  # Breaks on Windows!
```

### 3.3 Streaming Responses

**Never buffer entire files in memory.** Always use `StreamingResponse` for file content.

```python
# GOOD
async def file_iterator(sftp_file, chunk_size: int = 65536):
    try:
        while chunk := await sftp_file.read(chunk_size):
            yield chunk
    finally:
        await sftp_file.close()

@app.get("/file")
async def get_file(path: str):
    sftp_file = await sftp.open(path, 'rb')
    return StreamingResponse(file_iterator(sftp_file), media_type="application/octet-stream")

# BAD
@app.get("/file")
async def get_file(path: str):
    content = await sftp_file.read()  # OOM on large files!
    return Response(content=content)
```

---

## 4. Frontend (React) Standards

### 4.1 The VFS Base Tag (Critical Architecture)

Markdown and HTML files use relative paths for images (e.g., `./img.png`). The browser must resolve these relative to the remote directory, not the local route.

**Implementation Rule:**
- When rendering Markdown or HTML, always inject a `<base>` tag pointing to the file's directory via the API.

```tsx
// GOOD
const MarkdownViewer = ({ content, remotePath }: Props) => {
  // remotePath = "/home/user/project/README.md"
  const dirPath = remotePath.substring(0, remotePath.lastIndexOf('/'));
  const basePath = `/api/v1/hosts/${host}/file?path=${dirPath}/`;

  return (
    <div>
      <base href={basePath} />
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
};
```

### 4.2 Tree Lazy Loading

Do not fetch the entire file tree at once. Remote directories like `node_modules` will hang the UI.

- Fetch only the children of the expanded directory node.
- API must support depth-limited queries.
- Show loading spinners per tree node during fetches.

### 4.3 Connection Status UI

SSH connections drop. The UI must reflect reality:
- **Connecting:** Yellow indicator.
- **Connected:** Green indicator.
- **Reconnecting:** Pulsing red indicator with "Reconnecting..." text.
- Use WebSockets or polling (`/api/v1/hosts/{host}/status`) to notify the UI of state changes.

### 4.4 Terminal Emulator (xterm.js)

The terminal provides an interactive PTY session to the remote host via WebSocket.

**Implementation rules:**
- Use `xterm.js` with `@xterm/addon-fit` (auto-resize to container) and `@xterm/addon-web-links` (clickable URLs).
- Open a WebSocket to `/api/v1/hosts/{host}/terminal` on mount; close on unmount.
- On `resize` events (window/container resize), send `{"type": "resize", "cols": N, "rows": N}` to the backend.
- On terminal `onData` (user input), send `{"type": "input", "data": "..."}` to the WebSocket.
- On `{"type": "output"}` messages, write `data` to the terminal via `term.write()`.
- On `{"type": "exited"}`, show exit message and disable input.
- On `{"type": "error"}`, show error banner and offer reconnect button.
- Send `{"type": "ping"}` every 30s; if no `pong` within 10s, show "Connection lost" and attempt reconnect.

```tsx
// GOOD
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';

const TerminalView = ({ host }: { host: string }) => {
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const term = new Terminal({ scrollback: 10000 });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(termContainerRef.current!);
    fitAddon.fit();

    const ws = new WebSocket(`wss://localhost:8443/api/v1/hosts/${host}/terminal`);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
    };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'output') term.write(msg.data);
      else if (msg.type === 'exited') term.write(`\r\n[Process exited with code ${msg.exit_code}]\r\n`);
    };
    term.onData((data) => ws.send(JSON.stringify({ type: 'input', data })));
    term.onResize(({ cols, rows }) => ws.send(JSON.stringify({ type: 'resize', cols, rows })));

    wsRef.current = ws;
    termRef.current = term;
    return () => { ws.close(); term.dispose(); };
  }, [host]);

  return <div ref={termContainerRef} style={{ height: '100%' }} />;
};
```

**Do NOT buffer terminal output in React state.** Write directly to the xterm instance — React re-renders on every byte of output will destroy performance.

---

## 5. Security Implementation Details

### 5.1 Path Traversal Prevention

All API endpoints accepting file paths must pass through the `path_validator.py` module:

1. URL-decode the path.
2. Reject paths containing `..` components.
3. Ask the remote SFTP server to `realpath()` the path to resolve symlinks.
4. Verify the resulting absolute path is still within an allowed boundary (optional config).

### 5.2 SSRF Prevention for Rendered Content

HTML/Markdown files might contain `<img src="http://169.254.169.254/latest/meta-data/">` targeting local cloud metadata.

**Critical limitation:** The `<base>` tag only rewrites **relative** URLs into daemon API calls. **Absolute URLs** are fetched by the browser directly and never pass through the daemon. Backend-side private-IP blocking is ineffective for absolute URLs.

**Primary mitigation — Content-Security-Policy:**
- Inject `Content-Security-Policy: img-src 'self'; default-src 'self'` into rendered Markdown/HTML containers.
- This blocks all external image/resource loads from rendered content at the browser level.

**Defense-in-depth — backend private-IP blocking:**
- When resolving image URLs that **do** route through the daemon API (relative paths), block requests to private IP ranges (RFC 1918, 169.254.x.x, localhost).
- This is a secondary control only; CSP is the primary SSRF mitigation.

### 5.3 HTML Sandboxing

Rendered HTML must be placed in a sandboxed iframe to prevent malicious script execution:

```html
<iframe sandbox="" srcdoc={htmlContent}></iframe>
```

**Never use `allow-same-origin` in the sandbox attribute.** It would give the iframe the same origin as the parent page, allowing rendered HTML to access session cookies and call the daemon API directly — enabling data exfiltration from the remote machine. The empty `sandbox=""` attribute assigns a unique opaque origin that cannot access parent cookies or the API.

Additionally, inject a CSP into the rendered content: `script-src 'none'; img-src 'self'` to block script execution and external image loads.

### 5.4 Local Authentication

The daemon binds to `127.0.0.1`. To prevent other local applications from accessing the API:
- Generate a random session token on startup.
- Pass this token via the CLI when opening the browser: `https://localhost:8443/ui?token=abc123` — for the **initial request only**.
- On first load, exchange the URL token for an `HttpOnly` + `Secure` + `SameSite=Strict` cookie via a dedicated exchange endpoint, then redirect to the clean URL (without the token).
- All subsequent API calls authenticate via the cookie. This prevents token leakage via browser history, referrer headers, and process listings.
- The URL token is accepted only on the exchange endpoint; all other endpoints require the cookie.

---

## 6. SSH Connection Management

### 6.1 Connection Pooling

The `connection_pool.py` must maintain a dictionary of active SSH connections with per-host locking to prevent thundering herd, idle eviction, and a max-connection cap.

```python
class ConnectionPool:
    def __init__(self, max_connections: int = 20, idle_timeout: int = 600):
        self._connections: Dict[str, asyncssh.SSHClientConnection] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_access: Dict[str, float] = {}
        self._max_connections = max_connections
        self._idle_timeout = idle_timeout

    async def get_connection(self, host: str) -> asyncssh.SSHClientConnection:
        # Per-host lock prevents thundering herd: concurrent callers
        # for the same disconnected host await a single in-progress connection.
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()

        async with self._locks[host]:
            if host not in self._connections or self._connections[host].is_closed():
                # Evict LRU connection if at cap before opening a new one
                self._evict_if_needed()
                # Connect using config from ~/.ssh/config
                # Handle ProxyJump automatically
                self._connections[host] = await self._connect(host)
            self._last_access[host] = time.monotonic()
            return self._connections[host]

    def _evict_if_needed(self) -> None:
        """Evict idle or LRU connections when at capacity."""
        now = time.monotonic()
        # First pass: evict idle connections
        for h in list(self._connections.keys()):
            if now - self._last_access[h] > self._idle_timeout:
                asyncio.create_task(self._connections[h].close())
                del self._connections[h]
                del self._last_access[h]
        # Second pass: if still at cap, evict LRU
        while len(self._connections) >= self._max_connections:
            lru_host = min(self._last_access, key=self._last_access.get)
            asyncio.create_task(self._connections[lru_host].close())
            del self._connections[lru_host]
            del self._last_access[lru_host]
```

### 6.2 Keep-Alive and Reconnection

- Set `keepalive_interval=30` on AsyncSSH connections.
- If a connection drops, catch `asyncssh.DisconnectError` and trigger exponential backoff reconnection (1s, 2s, 4s, 8s, max 30s).
- Do not crash the web server on SSH disconnects; return HTTP 503 and let the UI poll for reconnection.

### 6.3 SFTP Channel Concurrency Limit

AsyncSSH supports a limited number of concurrent channels per connection (~10). To avoid exhausting channels under concurrent file reads, use a per-host semaphore:

```python
class ConnectionPool:
    def __init__(self, max_sftp_channels: int = 5):
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._max_sftp_channels = max_sftp_channels

    def get_semaphore(self, host: str) -> asyncio.Semaphore:
        if host not in self._semaphores:
            self._semaphores[host] = asyncio.Semaphore(self._max_sftp_channels)
        return self._semaphores[host]

# Usage in file endpoint:
async def get_file(host: str, path: str, pool: ConnectionPool = Depends(get_pool)):
    conn = await pool.get_connection(host)
    sem = pool.get_semaphore(host)
    async with sem:
        sftp = await conn.start_sftp_client()
        try:
            sftp_file = await sftp.open(path, 'rb')
            return StreamingResponse(file_iterator(sftp_file), media_type="application/octet-stream")
        finally:
            await sftp.exit()
```

### 6.4 Terminal Session Management

Terminal sessions use **separate SSH channels** from SFTP — they do not contend for the SFTP channel semaphore. However, they still count against the SSH connection's total channel limit (~10). Cap concurrent terminals per host.

```python
class ConnectionPool:
    def __init__(self, max_terminals_per_host: int = 3):
        self._terminal_counts: Dict[str, int] = {}
        self._max_terminals = max_terminals_per_host

    def acquire_terminal_slot(self, host: str) -> bool:
        """Returns True if a terminal slot is available, False if at cap."""
        count = self._terminal_counts.get(host, 0)
        if count >= self._max_terminals:
            return False
        self._terminal_counts[host] = count + 1
        return True

    def release_terminal_slot(self, host: str) -> None:
        count = self._terminal_counts.get(host, 0)
        if count > 0:
            self._terminal_counts[host] = count - 1
```

**WebSocket terminal endpoint:**

```python
@app.websocket("/api/v1/hosts/{host}/terminal")
async def terminal_ws(websocket: WebSocket, host: str, pool: ConnectionPool = Depends(get_pool)):
    await websocket.accept()
    if not pool.acquire_terminal_slot(host):
        await websocket.send_json({"type": "error", "message": "Too many terminal sessions"})
        await websocket.close()
        return

    conn = await pool.get_connection(host)
    term_manager = TerminalManager(conn)

    try:
        await termManager.start()
        await websocket.send_json({"type": "ready"})

        async def forward_output():
            async for chunk in termManager.read():
                await websocket.send_json({"type": "output", "data": chunk})

        output_task = asyncio.create_task(forward_output())

        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "input":
                await termManager.write(msg["data"])
            elif msg["type"] == "resize":
                await termManager.resize(msg["cols"], msg["rows"])
            elif msg["type"] == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except asyncssh.DisconnectError:
        await websocket.send_json({"type": "error", "message": "SSH connection lost"})
    finally:
        output_task.cancel()
        await termManager.close()
        pool.release_terminal_slot(host)
        await websocket.close()
```

**TerminalManager wraps AsyncSSH PTY:**

```python
class TerminalManager:
    def __init__(self, conn: asyncssh.SSHClientConnection, term_type: str = "xterm-256color"):
        self._conn = conn
        self._term_type = term_type
        self._process: asyncssh.SSHClientProcess | None = None
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def start(self, cols: int = 80, rows: int = 24) -> None:
        self._process = await self._conn.create_process(
            term_type=self._term_type,
            term_size=(cols, rows),
            stdin=asyncssh.PIPE,
            stdout=asyncssh.PIPE,
            stderr=asyncssh.STDOUT,
        )
        asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._process is not None
        try:
            while not self._process.stdout.at_eof():
                chunk = await self._process.stdout.read(4096)
                if chunk:
                    await self._output_queue.put(chunk)
        except asyncssh.DisconnectError:
            pass

    async def read(self) -> AsyncGenerator[str, None]:
        while True:
            chunk = await self._output_queue.get()
            yield chunk

    async def write(self, data: str) -> None:
        assert self._process is not None
        self._process.stdin.write(data.encode())

    async def resize(self, cols: int, rows: int) -> None:
        assert self._process is not None
        self._process.set_terminal_size(cols, rows)

    async def close(self) -> None:
        if self._process and not self._process.is_closing():
            self._process.close()
```

---

## 7. Error Handling Conventions

### 7.1 Backend (FastAPI)

Map SSH/SFTP errors to appropriate HTTP status codes:

| SSH/SFTP Error | HTTP Status |
|-----------------|-------------|
| File not found (NO_SUCH_FILE) | 404 Not Found |
| Permission denied (PERMISSION_DENIED) | 403 Forbidden |
| Connection lost | 503 Service Unavailable |
| Path traversal detected | 400 Bad Request |
| Terminal slot exhausted | 503 Service Unavailable (WebSocket: `{"type": "error"}`) |
| Terminal SSH channel open failed | 502 Bad Gateway (WebSocket: `{"type": "error"}`) |

### 7.2 Frontend (React)

- **404:** Show "File not found" in viewer.
- **403:** Show "Permission denied" in viewer.
- **503:** Show "Connection lost. Reconnecting..." banner at top of UI.

---

## 8. Testing Requirements

### 8.1 Unit Tests

- Test `path_validator.py` extensively. It is the primary security boundary.
- Test SSH config parsing with various `ProxyJump` scenarios.
- Test VFS base tag URL generation in React.

### 8.2 Integration Tests

- Use Docker containers with SSH servers for integration testing.
- Test file streaming with large files (>100MB) to verify no memory leaks.
- Test reconnection logic by killing the SSH server during active transfers.
- Test terminal: open session, send input, verify output, resize, close.
- Test terminal cap: open `max_terminals_per_host + 1` sessions, verify last is rejected.
- Test terminal cleanup: disconnect WebSocket mid-session, verify SSH channel is closed.

### 8.3 Test Commands

```bash
# Python
pytest tests/unit -v
pytest tests/integration -v

# React
cd ui && npm test
```

---

## 9. Git Commit Guidelines

- Use conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`.
- Keep commits atomic.
- Never commit directly to `main`; use pull requests.