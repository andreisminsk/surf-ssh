# Surf SSH

A local-first developer tool that provides a browser-based file system explorer and terminal for remote machines accessible via SSH. It leverages existing SSH configurations (including complex `ProxyJump` setups) without requiring any agent software on the remote machine.

## Features

- **File browsing** — Lazy-loaded directory tree with depth and entry caps
- **File viewing** — Text files with syntax highlighting, Markdown with relative image resolution, HTML in sandboxed iframe
- **Remote terminal** — Interactive PTY terminal via WebSocket (xterm.js)
- **Local console** — Local PTY terminal (bash/zsh/sh on Unix, PowerShell/CMD/WSL on Windows) via WebSocket
- **Liveness tracking** — Server-initiated ping/pong heartbeat; a background reaper closes SSH connections for dead clients
- **Security** — Local-only binding, session token → HttpOnly cookie, path traversal prevention, SSRF mitigation via CSP
- **Zero config** — Reuses `~/.ssh/config` host aliases and keys
- **Cross-platform** — Works on macOS and Windows

## Quick Start

```bash
# Install
pip install -e .

# Build the UI
cd ui && npm install && npm run build && cd ..

# Open a remote host
surf-ssh open my-server

# Or without opening a browser
surf-ssh open my-server --no-browser

# List available hosts from ~/.ssh/config
surf-ssh hosts
```

The daemon starts on `https://localhost:8443` and opens a browser automatically. A self-signed CA certificate is generated on first run and stored in `~/.surf-ssh/`.

## Architecture

```
Browser (React SPA)  ←HTTPS→  Local Python Daemon  ←SSH/SFTP→  Remote Machine
                              (FastAPI + AsyncSSH)
```

- **Backend**: Python 3.10+, FastAPI, Uvicorn, AsyncSSH
- **Frontend**: React, Vite, xterm.js, react-markdown
- **CLI**: Typer + Rich

See [SURF-SSH-ARCH.md](SURF-SSH-ARCH.md) for the full architecture document and [SURF-SSH-CODER.md](SURF-SSH-CODER.md) for coding guidelines.

## Project Structure

```
surf-ssh/
├── src/
│   ├── main.py                 # CLI entry point
│   ├── daemon/                 # FastAPI server, TLS, static serving
│   ├── ssh/                     # Config parser, connection pool, SFTP client
│   ├── api/                     # REST + WebSocket endpoints
│   └── security/               # Path validation, session auth
├── ui/                         # React SPA
│   └── src/
│       ├── components/         # FileTree, FileViewer, TerminalView, etc.
│       ├── hooks/              # useConnection, useFileSystem, useTerminal
│       └── api/                # API client
├── tests/
│   ├── unit/                   # Path validator, session auth, config parser
│   └── integration/            # API endpoint tests
└── pyproject.toml
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/hosts` | List connected/available hosts |
| GET | `/api/v1/hosts/{host}/status` | Get connection status and platform |
| GET | `/api/v1/hosts/{host}/home` | Resolve the host's home directory |
| GET | `/api/v1/hosts/{host}/tree` | Get directory tree (lazy, depth-limited) |
| GET | `/api/v1/hosts/{host}/file` | Stream file content |
| GET | `/api/v1/hosts/{host}/stat` | Get file metadata |
| GET | `/api/v1/hosts/{host}/download` | Download binary file |
| WS | `/api/v1/hosts/{host}/terminal` | Interactive remote terminal session |
| WS | `/api/v1/local/terminal` | Interactive local terminal session |
| GET | `/api/v1/local/shells` | List available local shells |
| WS | `/api/v1/liveness` | Server-initiated ping/pong heartbeat |
| GET | `/api/v1/auth/exchange` | Exchange URL token for cookie |
| GET | `/api/v1/health` | Health check |

## Security

- **Local only**: Daemon binds to `127.0.0.1` exclusively
- **Session auth**: Token passed via URL on first load, exchanged for `HttpOnly` + `Secure` + `SameSite=Strict` cookie
- **Path traversal**: Rejects `..` components, resolves symlinks via `sftp.realpath()`
- **SSRF**: CSP `img-src 'self'` blocks external image loads from rendered content
- **HTML sandbox**: `sandbox=""` iframe prevents script execution and API access
- **Liveness reaper**: A background task periodically closes SSH connections whose clients have stopped responding to heartbeats, preventing connection leaks

## Killing a Stale Daemon

If port 8443 is occupied by a stale process, find and kill it:

**macOS / Linux:**
```bash
lsof -ti :8443 | xargs kill -9
# or
pkill -9 -f surf-ssh
```

**Windows (CMD):**
```cmd
for /f "tokens=5" %a in ('netstat -ano ^| findstr :8443 ^| findstr LISTENING') do taskkill /PID %a /F
```

**Windows (PowerShell):**
```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 8443).OwningProcess | Stop-Process -Force
```

## Testing

```bash
# Python tests
pytest tests/unit -v
pytest tests/integration -v

# React tests
cd ui && npm test
```

## Configuration

Configuration and certificates are stored in `~/.surf-ssh/`:

```
~/.surf-ssh/
├── ca.pem          # Self-signed CA certificate
├── ca-key.pem      # CA private key
├── localhost.pem   # Localhost certificate
├── localhost-key.pem
├── config.json     # User preferences
└── sessions/       # Active session tokens
```

## Feedback

We welcome feedback, bug reports, and suggestions:

- **Telegram:** [@MartiAi_Feedback_bot](https://t.me/MartiAi_Feedback_bot)
- **GitHub Issues:** [surf-ssh/issues](https://github.com/andreisminsk/surf-ssh/issues)

## License

Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)

Copyright © 2026 Andrei Suvorov

You are free to:
- **Share** — copy and redistribute the material in any medium or format
- **Adapt** — remix, transform, and build upon the material

Under the following terms:
- **Attribution** — You must give appropriate credit, provide a link to the license, and indicate if changes were made.
- **NonCommercial** — You may not use the material for commercial purposes.
- **ShareAlike** — If you remix, transform, or build upon the material, you must distribute your contributions under the same license as the original.
- **No additional restrictions** — You may not apply legal terms or technological measures that legally restrict others from doing anything the license permits.

Full license text: [https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode)

### Disclaimer of Warranties

THIS WORK IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE WORK OR THE USE OR OTHER DEALINGS IN THE WORK.
