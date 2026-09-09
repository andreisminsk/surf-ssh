# Review of External Security Assessment — surf-ssh

**Reviewed:** `SEC-ASMT-CLAUDE.md` (Claude, 2026-09-09)
**Method:** Every claim verified against source; critical paths verified with live PoCs against a running daemon (`surf-ssh daemon -p 9555`).
**Verdict:** All 10 reported claims are **confirmed and accurate**. However, the assessment **missed the two most severe vulnerabilities in the codebase** — both authentication bypasses that invalidate its severity ranking and fix order.

---

## 1. Verification of Reported Claims

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Session tokens never expire | ✅ Confirmed | `validate_session` has no TTL check; 28 stale session files accumulated in `~/.surf-ssh/sessions/` (oldest Aug 26) |
| 2 | Token filename leaks token | ✅ Confirmed | `session_auth.py:23` — `{token}.json`; live `ls` shows plaintext tokens as filenames |
| 3 | TLS keys world-readable | ✅ Confirmed | Live check: `ca-key.pem`, `localhost-key.pem` are `-rw-r--r--` (umask 022) |
| 4 | Symlink traversal unenforced | ✅ Confirmed | `SFTPClient.realpath()` exists but is only called for home-dir detection (`hosts.py:65`, `connection_pool.py:239`); never enforced in file/tree/download handlers |
| 5 | Daemon mode `/ui` renders unauthenticated | ✅ Confirmed | Middleware exempts `/ui` unconditionally; daemon URL is bare `/ui` with no token exchange |
| 6 | OpenAPI docs exposed | ✅ Confirmed | `server.py:98-99` — `docs_url="/api/v1/docs"` |
| 7 | HTTP liveness keyed on token | ✅ Confirmed | `server.py` — `f"http:{token}"` as client ID; multi-tab miscount |
| 8 | Frozen terminal WS leaks SSH conn | ✅ Confirmed | `_reap()` skips close when `terminal_counts > 0` (`connection_pool.py:342-343`) |
| 9 | No download size cap | ✅ Confirmed | `files.py` download streams unbounded |
| 10 | Update check phones home | ✅ Confirmed | `main.py` — outbound GitHub request on every `open` |

**Assessment quality:** precise line references, no false positives. Weakness: no dynamic testing, which is why the criticals below were missed.

---

## 2. Critical Findings Missed by the Assessment

### A. All WebSocket endpoints are completely unauthenticated — CRITICAL

`SessionAuthMiddleware` extends `BaseHTTPMiddleware`, which in Starlette **only processes HTTP scopes**. WebSocket handshakes bypass it entirely. The token check in the middleware for `/hosts/{host}/terminal` (`server.py`) is **dead code** — it never executes.

**PoC (live, no cookie, no token):**
```
liveness WS:              HTTP/1.1 101 Switching Protocols
local terminal WS:        HTTP/1.1 101 Switching Protocols
remote terminal WS:       HTTP/1.1 101 Switching Protocols
```

**Impact:** Any local process or user can open:
- `/api/v1/local/terminal` → **full interactive shell on the user's machine** (unauthenticated RCE surface)
- `/api/v1/hosts/{any-alias}/terminal` → shell on any host in `~/.ssh/config`, riding the daemon's SSH agent/keys

The session auth model protects only HTTP file browsing; the terminals — the most powerful features — are wide open. This is the single worst issue in the codebase.

**Fix:** Add an explicit auth check inside each WS endpoint (or a pure-ASGI middleware that handles `scope["type"] == "websocket"`): validate the session cookie (WS cookies are available in `websocket.cookies`) or a `token` query param before `websocket.accept()`; otherwise close with 4401.

### B. Unauthenticated path traversal in static file serving — CRITICAL

`static.py:serve_spa_asset` joins user input to `_STATIC_DIR` with no normalization or containment check, and the middleware exempts `/ui` from auth. URL-encoded `%2f` decodes **after** uvicorn's path normalization, so `..%2f` sequences bypass the router's dot-segment stripping.

**PoC (live, no cookie):**
```
/ui/..%2f..%2fREADME.md                          → README.md content (project root)
/ui/..%2f..%2f..%2f..%2f.surf-ssh%2fca.pem       → CA certificate (home dir)
/ui/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd   → /etc/passwd
```
(Raw `../` is normalized by the HTTP layer and safely falls back to index.html — only the encoded variant escapes.)

**Impact:** Any local user/process can read **any file readable by the daemon process**: `~/.surf-ssh/sessions/*.json` (all live session tokens → full HTTP API access), `~/.ssh/id_*` private keys, shell history, etc. Chains with Finding A into complete compromise.

**Fix:** In `serve_spa_asset`, resolve and contain:
```python
file_path = (_STATIC_DIR / path).resolve()
if not file_path.is_relative_to(_STATIC_DIR.resolve()):
    raise HTTPException(404)
```
(`Path.is_relative_to` is 3.9+; project requires 3.10+.)

### C. Session token files are world-readable — Medium (aggravates claim #2)

Live check: session `.json` files are `-rw-r--r--` (umask 022). The token is not only the filename but also readable in file content by any local user. Same root cause as claim #3 (no explicit `chmod 0600`); fix together.

---

## 3. Corrected Severity Ranking

| # | Issue | Assessed | Corrected |
|---|-------|----------|-----------|
| A | WS endpoints unauthenticated (local + remote shell) | *missed* | **Critical** |
| B | Static path traversal → arbitrary file read | *missed* | **Critical** |
| 3/6 | TLS keys world-readable | Medium | Medium |
| 1/2/C | Session tokens: no expiry, filename leak, world-readable files | Medium | Medium |
| 4 | Symlink traversal unenforced | Medium | Medium |
| 5 | Daemon `/ui` renders unauthenticated | Low | Low |
| 8 | Frozen terminal leaks SSH conn | Low | Low |
| 9 | No download cap | Low | Low |
| 6 | OpenAPI docs exposed | Low | Low |
| 10 | Update check phones home | Informational | Informational |

Context note: single-user local machine lowers exploitability (attacker must be local), but "any local process/user" includes malware, other accounts on shared machines, and containerized escape scenarios. The terminals make A worse than B in impact.

## 4. Corrected Fix Order

1. **WS auth (A)** — auth check inside each WS endpoint before `accept()`. ~1 hour, closes the RCE surface.
2. **Static traversal (B)** — `resolve()` + containment check in `serve_spa_asset`. ~15 minutes.
3. **File permissions (3 + C)** — `os.chmod(0o600)` for TLS keys and session files after write. Trivial.
4. **Session hardening (1 + 2)** — TTL check in `validate_session`, startup cleanup, `sha256(token).json` filenames.
5. **Symlink enforcement (4)** — `realpath()` post-validation in file/tree/download handlers.
6. Remaining Low items (5, 6, 8, 9, 10) — as time permits.

## 5. Recommendations for Future Assessments

- Require dynamic verification (run the daemon, send raw requests) — both criticals are invisible to static reading alone; the WS bypass hinges on a Starlette framework behavior, and the traversal on an HTTP-layer decoding order.
- Test the auth boundary as a system: the assessment checked each endpoint's *intended* protection but never attempted an unauthenticated request against the highest-privilege surfaces (terminals).
