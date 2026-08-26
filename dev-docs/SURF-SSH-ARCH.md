# Surf SSH - System Architecture

## 1. Overview

**Surf SSH** is a local-first developer tool that provides a browser-based file system explorer for remote machines accessible via SSH. It leverages existing SSH configurations (including complex `ProxyJump` setups) without requiring any agent software on the remote machine.

**Core User Experience:**
- User runs: `surf-ssh open my-server`
- Browser opens: `https://localhost:8443/ui?host=my-server`
- User browses remote file tree, views text files, renders Markdown with images, renders HTML with images
- User opens an embedded terminal for the same host, with full PTY support (interactive commands, TUI apps, resize)

---

## 2. Component Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Local Machine                                │
│                                                                     │
│  ┌─────────┐         ┌──────────────────────────────────────────┐  │
│  │         │  HTTPS  │          Local Python Daemon             │  │
│  │ Browser │◄────────┤                                          │  │
│  │   SPA   │         │  ┌─────────────┐   ┌─────────────────┐  │  │
│  │         │─────────┤─►│   FastAPI    │   │  AsyncSSH Pool  │  │  │
│  │         │  WS     │  │  Web Server  │──►│  (per host)     │  │  │
│  └─────────┘─────────┤─►│  (REST + WS) │   └────────┬────────┘  │
│                        │  └─────────────┘            │           │  │
│                        │                               │           │  │
│                        │  ┌─────────────┐             │           │  │
│                        │  │  Static UI   │             │           │  │
│                        │  │  (React SPA) │             │           │  │
│                        │  └─────────────┘             │           │  │
│                        └──────────────────────────────┼──────────┘  │
│                                                     │              │
│                        ┌─────────────────────────────┘              │
│                        │  SSH/SFTP                                  │
└────────────────────────┼─────────────────────────────────────────────┘
                         │
                    ┌─────┴─────┐
                    │   Proxy   │  (Optional, per ~/.ssh/config)
                    │   Jump    │
                    └─────┬─────┘
                          │
                 ┌────────┴────────┐
                 │  Remote Machine  │
                  │  (my-server)   │
                 └─────────────────┘
```

---

## 3. Component Details

### 3.1 Local Python Daemon

**Responsibility:** Bridge between browser and remote SSH machine.

**Key Functions:**
- Parse `~/.ssh/config` to resolve host aliases and ProxyJump chains
- Maintain persistent AsyncSSH connections per remote host
- Expose REST API for file system operations
- Expose WebSocket endpoint for interactive terminal sessions (PTY)
- Serve static UI assets
- Handle TLS termination for local HTTPS

**Technology:** Python 3.10+ with FastAPI + Uvicorn (WebSocket support via Starlette)

### 3.2 AsyncSSH Connection Pool

**Responsibility:** Manage SSH connections efficiently.

**Key Functions:**
- Parse `~/.ssh/config` for host resolution
- Establish connections through ProxyJump chains
- Maintain connection health (keep-alive pings)
- Auto-reconnect on connection drops
- Clean up idle connections

**Technology:** AsyncSSH library

**Connection Map:**
```python
connections: Dict[str, asyncssh.SSHClientConnection]
# Key: hostname alias (e.g., "my-server")
# Value: Active SSH connection
```

### 3.3 Web UI (React SPA)

**Responsibility:** Provide interactive file browsing experience.

**Key Functions:**
- Lazy-loaded folder tree navigation
- Text file viewing with syntax highlighting
- Markdown rendering with relative image resolution
- HTML rendering in sandboxed iframe
- Binary file download trigger
- Embedded terminal emulator (xterm.js) with PTY resize support
- Connection status indicators

**Technology:** React + Vite, served as embedded static assets. Terminal via xterm.js + xterm-addon-fit + xterm-addon-web-links.

### 3.4 CLI Runner

**Responsibility:** User-facing command-line interface.

**Key Functions:**
- Start daemon process
- Open browser to correct URL
- Manage daemon lifecycle
- Port conflict detection

**Technology:** Typer + Rich

---

## 4. API Design

### 4.1 REST Endpoints

```
GET  /api/v1/hosts                                    # List connected/available hosts
GET  /api/v1/hosts/{host}/tree?path=/home/user&depth=1&limit=500  # Get directory tree (lazy, depth-limited, entry-capped)
GET  /api/v1/hosts/{host}/file?path=/home/user/doc.md # Get file content (streamed)
GET  /api/v1/hosts/{host}/stat?path=/home/user/doc.md # Get file metadata
GET  /api/v1/hosts/{host}/download?path=/home/user/file.zip  # Download binary file
WS   /api/v1/hosts/{host}/terminal                        # Interactive terminal session (PTY)
```

### 4.4 WebSocket Terminal Protocol

The terminal endpoint upgrades to a WebSocket. Messages are JSON-encoded with a `type` field:

**Client → Server:**
```json
{"type": "input", "data": "ls -la\r"}
{"type": "resize", "cols": 120, "rows": 40}
{"type": "ping"}
```

**Server → Client:**
```json
{"type": "output", "data": "total 48\r\ndrwxr-xr-x ..."}
{"type": "exited", "exit_code": 0}
{"type": "error", "message": "Connection lost"}
{"type": "pong"}
```

**Lifecycle:**
- On connect, the daemon opens a new SSH channel with `conn.create_session(term_type="xterm-256color", term_size=(80, 24))` — a **separate channel** from SFTP, so terminal and file browsing do not contend for the same channel pool.
- On `resize`, the daemon calls `term.set_size(rows, cols)` on the PTY.
- On `input`, the daemon writes to the PTY stdin.
- On disconnect or `exited`, the WebSocket closes and the SSH channel is cleaned up.
- Multiple terminal sessions per host are supported (each WebSocket = one SSH channel).

### 4.2 Tree Response Format

```json
{
  "path": "/home/user/project",
  "name": "project",
  "type": "directory",
  "truncated": false,
  "children": [
    {
      "path": "/home/user/project/README.md",
      "name": "README.md",
      "type": "file",
      "size": 2048,
      "modified": "2024-01-15T10:30:00Z"
    }
  ]
}
```

**Entry cap:** The `children` array is capped at `limit` entries (default 500). When the cap is reached, `truncated` is set to `true` and the frontend shows a "Load more" action. Depth is capped at 2 levels maximum to prevent massive responses from directories like `node_modules`.

### 4.3 File Response

- **Text files:** Return with appropriate `Content-Type` (text/plain, text/markdown, text/html)
- **Images:** Return with MIME type (image/png, image/jpeg, image/svg+xml)
- **Binary files:** Return with `Content-Disposition: attachment` header
- **All responses:** Streamed via `StreamingResponse` in 64KB chunks to prevent memory exhaustion

---

## 5. Virtual File System (VFS) Path Resolution

**The Critical Problem:** Markdown and HTML files reference images with relative paths (e.g., `![logo](./assets/logo.png)`). The browser must resolve these relative to the file's directory on the remote machine, not relative to the local web server route.

**The Solution: Path-Prefix VFS with `<base>` Tag**

URL structure mirrors the remote file system:

```
/api/v1/hosts/my-server/file?path=/Users/you/project/README.md
```

When rendering Markdown/HTML, the UI injects a `<base>` tag pointing to the file's remote directory:

```html
<base href="/api/v1/hosts/my-server/file?path=/Users/you/project/">
```

The browser automatically resolves `./assets/logo.png` to:

```
/api/v1/hosts/my-server/file?path=/Users/you/project/assets/logo.png
```

The daemon fetches the image via SFTP and returns it with the correct MIME type. This requires zero URL rewriting in the Markdown/HTML content itself.

---

## 6. Security Architecture

### 6.1 Authentication & Network Binding

- **Local only:** Daemon binds to `127.0.0.1` exclusively. No external network exposure.
- **Session token:** Generated on startup, passed via CLI to browser URL as query parameter (`?token=abc123`) for the **initial** request only.
- **Cookie exchange:** On first load, the daemon exchanges the URL token for an `HttpOnly` + `Secure` + `SameSite=Strict` cookie, then redirects to the clean URL (without the token). This prevents token leakage via browser history, referrer headers, and process listings.
- **Validation:** All subsequent API requests require the session token via cookie. The URL token is accepted only on the initial exchange endpoint.

### 6.2 Path Traversal Prevention

- **Input validation:** Reject paths containing `..` components.
- **Absolute path enforcement:** All paths resolved to absolute paths before SFTP calls.
- **Symlink resolution:** Use `sftp.realpath()` to resolve symlinks server-side.
- **Optional chroot:** Configuration option to restrict browsing to specific root directories.

### 6.3 SSRF Prevention for Rendered Content

**Important limitation:** The `<base>` tag only rewrites **relative** URLs (e.g., `./assets/logo.png`) into daemon API calls. **Absolute URLs** in Markdown/HTML (e.g., `![x](http://169.254.169.254/latest/meta-data/)`) are fetched by the **browser directly**, never touching the daemon. Backend-side private IP blocking is therefore **ineffective** for absolute URLs.

**Mitigation via Content-Security-Policy:**
- Inject a restrictive CSP into rendered Markdown/HTML containers: `Content-Security-Policy: img-src 'self'; default-src 'self'`
- This blocks all external image/resource loads from rendered content, preventing browser-side SSRF to cloud metadata services and internal IPs.
- The daemon's private-IP blocking remains as **defense-in-depth** for any image requests that do route through the API (relative paths resolving to daemon-served content), but it must not be relied upon as the primary SSRF control.

### 6.4 HTML/Markdown Rendering Security

- **Sandboxed iframe:** HTML rendered in `<iframe sandbox="" srcdoc={htmlContent}>`. The empty `sandbox` attribute (no `allow-same-origin`) gives the iframe a unique opaque origin, preventing access to parent cookies, session tokens, and the daemon API.
- **No `allow-same-origin`:** Never use `allow-same-origin` in the sandbox attribute. It would allow rendered HTML to call the daemon's API using the session token from cookies, enabling data exfiltration from the remote machine.
- **Content-Security-Policy:** Inject `script-src 'none'; img-src 'self'` into rendered HTML to block script execution and external image loads.

### 6.5 TLS Configuration

- **Self-signed CA:** Generated on first run, stored in `~/.surf-ssh/`.
- **Certificate per host:** Generated dynamically for `localhost`.
- **Trust store:** User must trust CA once per machine.

---

## 7. Data Flow: Viewing a Markdown File with Images

```
1. User clicks "README.md" in tree UI
2. Browser: GET /api/v1/hosts/my-server/file?path=/home/user/project/README.md
3. Daemon: Checks AsyncSSH pool for "my-server" connection
4. Daemon: sftp.open("/home/user/project/README.md") -> streams content
5. Daemon: Returns content with Content-Type: text/markdown
6. UI: Renders Markdown, injects <base href="/api/v1/hosts/my-server/file?path=/Users/you/project/">
7. Browser encounters: ![logo](./assets/logo.png)
8. Browser resolves: /api/v1/hosts/my-server/file?path=/Users/you/project/assets/logo.png
9. Daemon: sftp.open("/home/user/project/assets/logo.png") -> streams content
10. Daemon: Returns content with Content-Type: image/png
11. UI: Displays image inline
```

---

## 8. Connection Lifecycle Management

### 8.1 Connection States

```
DISCONNECTED -> CONNECTING -> CONNECTED -> IDLE -> RECONNECTING
                                    |
                                    v
                                 DROPPED
```

### 8.2 Health Monitoring

- **Keep-alive:** Send SSH keep-alive packets every 30 seconds.
- **Timeout:** Mark connection as DROPPED after 60 seconds of no response.
- **Auto-reconnect:** Exponential backoff (1s, 2s, 4s, 8s, max 30s).
- **UI notification:** Polling endpoint (`/api/v1/hosts/{host}/status`) to notify browser of connection state changes.

### 8.3 Connection Pool Concurrency & Resource Management

- **Per-host connection lock:** Use an `asyncio.Lock` per host key to prevent thundering herd — concurrent requests for a disconnected host await a single in-progress connection rather than each opening their own.
- **Idle eviction:** Close connections unused for more than 10 minutes. Track last-access timestamp per connection.
- **Max connection cap:** Limit total active connections (default: 20). Evict least-recently-used connections when the cap is reached.
- **SFTP channel semaphore:** Limit concurrent SFTP file reads per host (default: `asyncio.Semaphore(5)`) to avoid exhausting SSH channels (AsyncSSH typically supports ~10 concurrent channels per connection).

---

## 9. Technology Stack Summary

| Component | Technology | Justification |
|-----------|-----------|---------------|
| Language | Python 3.10+ | User preference, cross-platform support |
| Web Framework | FastAPI + Uvicorn | Async support, high performance, automatic API docs |
| SSH Client | AsyncSSH | Native async, ProxyJump support, SSH config parsing |
| CLI | Typer + Rich | Type-safe CLI definitions, beautiful terminal output |
| Frontend | React + Vite | Component model, fast dev/build, ecosystem size |
| Markdown | react-markdown | Extensible rendering, supports custom components |
| Syntax Highlighting | react-syntax-highlighter | Automatic language detection for code blocks |
| Tree UI | react-arborist | Lazy-loading tree structure, keyboard navigation |
| Terminal (frontend) | xterm.js + addons | De facto web terminal emulator, fit/resize/web-links addons |
| Terminal (backend) | AsyncSSH PTY sessions | Native async PTY via `create_session`, separate channel from SFTP |
| Packaging | PyInstaller | Single binary distribution for Win/Mac, no Python install required |
| TLS | cryptography + truststore | Local HTTPS, self-signed certificate generation |

---

## 10. Deployment & Distribution

### 10.1 Development Mode

```bash
# Install
pip install surf-ssh

# Run
surf-ssh open my-server
```

### 10.2 Production Binary

```bash
# Build
pyinstaller --onefile --name surf-ssh src/main.py

# Run (no Python installation required)
./surf-ssh open my-server
```

### 10.3 Configuration Storage

```
~/.surf-ssh/
  ├── ca.pem          # Self-signed CA certificate
  ├── ca-key.pem      # CA private key
  ├── config.json     # User preferences (port, theme, etc.)
  └── sessions/       # Active session tokens
```

---

## 11. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Large directory listing hangs UI | High | Medium | Lazy-load tree nodes, depth-limited API, pagination |
| SSH connection drops during read | Medium | Low | Auto-reconnect with exponential backoff, retry logic in UI |
| Memory exhaustion on large files | Medium | High | Streaming responses with 64KB chunks, never buffer entire file in RAM |
| Path traversal attack | Low | Critical | Input validation, realpath resolution, reject `..` components |
| Port conflict on 8443 | Medium | Low | Automatic port selection with fallback ranges |
| Windows path separator issues | Medium | Medium | Enforce POSIX paths in API, use `PurePosixPath` for remote paths |
| PyInstaller binary size | Low | Low | Acceptable for developer tool (~30-50MB), compression available |
| SSRF via image tags in MD/HTML | Medium | High | CSP `img-src 'self'` blocks external image loads; backend private-IP blocking as defense-in-depth for API-routed images only |
| Terminal SSH channel exhaustion | Low | Medium | Terminal uses separate SSH channels from SFTP; cap max concurrent terminals per host (default 3) |
| Unbounded terminal output memory | Medium | Medium | Ring buffer on backend (default 1MB per session); xterm scrollback limit on frontend (default 10k lines) |

---

## 12. Future Considerations

- **File upload/download:** Drag-and-drop file upload via SFTP write operations.
- **File editing:** In-browser text editor (Monaco) with save-back via SFTP.
- **Multi-host tabs:** Simultaneous browsing of multiple remote machines in tabs.
- **Search:** Remote file content search via SSH exec (`grep`/`find` commands).
- **Bookmarks:** Save frequently accessed directories for quick navigation.
- **Git integration:** Visual indicators for git-tracked directories and file statuses.
- **Terminal session persistence:** Reconnect to a detached terminal session (e.g., via `screen`/`tmux` on the remote).