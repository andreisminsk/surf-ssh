# Security Assessment — surf-ssh

**Scope:** Single-machine, single-user deployment  
**Assessed by:** Claude (claude-sonnet-4-6)  
**Date:** 2026-09-09  

---

## Architecture Drawbacks

### 1. Session tokens are never expired

`src/security/session_auth.py:21-24` — Sessions are created with a `created` timestamp, but
`validate_session` never checks it. A session token on disk persists forever until
`destroy_session` is called explicitly. The token is also never cleaned up on normal daemon
exit — old `.json` files accumulate in `~/.surf-ssh/sessions/`.

### 2. Session file naming leaks the token value

`src/security/session_auth.py:23` — The session file is named `{token}.json`, so anyone who
can `ls ~/.surf-ssh/sessions/` learns all valid session tokens. A better approach stores a
hash of the token as the filename.

### 3. Path traversal prevention is incomplete

`src/security/path_validator.py:27` — The check only rejects literal `..` components. It
does not handle:

- **URL double-encoding:** `%252e%252e` (double-encoded) would not be caught by a single
  `unquote()` pass.
- **Null bytes in paths:** `"/etc/passwd\x00.jpg"` — `PurePosixPath` may strip the null, but
  it is not explicitly rejected.
- **Symlink traversal on the remote:** The validator works on the *string* path only.
  `sftp.realpath()` is available in `SFTPClient` but is not enforced as a post-validation
  step at the API layer, so a symlink on the remote can still escape the intended directory.

### 4. Swagger UI / OpenAPI docs are exposed without hardening

`src/daemon/server.py:98-99` — `docs_url="/api/v1/docs"` and
`openapi_url="/api/v1/openapi.json"` are served by FastAPI by default. These fall under the
`/api/v1/` prefix so the session middleware applies, but they fully document every endpoint
and parameter — effectively a ready-made attack surface map. Consider disabling them in
production (`docs_url=None, openapi_url=None`).

### 5. Daemon mode: `/ui` loads without a session cookie

`src/main.py:239` and `src/daemon/server.py:55` — In daemon mode the URL is just
`https://localhost:{port}/ui` with no initial token exchange. The `SessionAuthMiddleware`
unconditionally allows all `/ui` routes without any cookie. Any local process or user that
visits `https://localhost:8443/ui` gets the full UI rendered — they only encounter an auth
wall when making API calls. There is no visible indication that they are unauthenticated.

### 6. TLS private key files have no filesystem permission hardening

`src/daemon/tls.py:57-62` — Private keys (CA key and localhost key) are written with
`Path.write_bytes()`, which inherits the process umask. On a default Linux/macOS umask of
`022` the key files are created as world-readable (`-rw-r--r--`). No explicit `chmod 0600`
is applied after writing.

### 7. HTTP liveness uses the session token as the client ID

`src/daemon/server.py:79,82` — `f"http:{token}"` is used as the client ID for HTTP-only
sessions. This ties the liveness mechanism to the auth token: multiple open tabs share the
same client ID and are counted as a single client, which can cause premature auto-exit when
one tab closes.

### 8. Frozen terminal WebSockets can leak SSH connections indefinitely

`src/ssh/connection_pool.py:342-343` — The reaper skips closing SSH connections when
`terminal_counts > 0`. If a terminal WebSocket is left open due to a frozen or crashed
browser tab (without a clean WebSocket close handshake), the terminal slot counter never
decrements and the SSH connection is held open until the next full daemon restart.

### 9. No file size limit on the `/download` endpoint

`src/api/files.py:135-156` — The download endpoint streams with no upper size cap. A request
for a very large remote file (e.g. a disk image) will stream until the connection drops,
holding an SFTP channel slot open for the duration and potentially exhausting memory or
bandwidth.

### 10. Outbound network request on every `open` invocation

`src/main.py:63-77` — An update check runs in a daemon thread on every `surf-ssh open`
call. This makes an outbound HTTPS request to the GitHub API, leaking the current version
string and platform information to an external server on every invocation. It can also stall
startup output if the network is slow.

---

## Security Risks Summary

| # | Risk | Location | Severity |
|---|---|---|---|
| 1 | Session tokens never expire | `session_auth.py:26-37` | Medium |
| 2 | Token filename equals token value | `session_auth.py:23` | Medium |
| 3 | TLS private keys world-readable (default umask) | `tls.py:57-62` | Medium |
| 4 | Symlink traversal not blocked at API layer | `path_validator.py` | Medium |
| 5 | Daemon mode: `/ui` renders without auth | `server.py:55` | Low |
| 6 | OpenAPI docs expose full attack surface | `server.py:98-99` | Low |
| 7 | No file download size cap | `files.py:148-152` | Low |
| 8 | Frozen terminal leaks SSH connection | `connection_pool.py:342-343` | Low |
| 9 | Update check leaks version to GitHub | `main.py:63-77` | Informational |

---

## Most Impactful Issues (Recommended Fix Order)

1. **TLS private key permissions** — Add `os.chmod(key_path, 0o600)` immediately after
   writing each key file in `tls.py`. Trivial fix, high impact.

2. **Session token expiry** — Add a TTL check (e.g. 24 hours) in `validate_session` and
   a startup cleanup pass that removes expired session files.

3. **Token filename hashing** — Store sessions as `sha256(token).json` so the filename does
   not reveal the token to anyone who can read the directory listing.

4. **Symlink traversal** — Enforce `sftp.realpath()` as a post-validation step in the file
   and tree API handlers, and reject paths that resolve outside an expected root.

5. **Daemon mode auth** — Either require a token exchange in daemon mode too (generate a
   token, print the URL), or at minimum show a clear unauthenticated state in the UI rather
   than rendering the full shell.
