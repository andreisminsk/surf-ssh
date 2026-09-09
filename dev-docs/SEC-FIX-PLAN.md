# Security Fix Plan — surf-ssh

**Date:** 2026-09-09
**Input:** `SEC-ASMT-REVIEW.md` (verified findings A, B, C, 1–10)
**Goal:** Close all confirmed vulnerabilities, criticals first, with regression tests and PoC re-verification for every fix.

**Guiding principles:**
- One finding = one work item = one commit + tests.
- Every fix must be verified by re-running the exact PoC that demonstrated the bug.
- No behavior change beyond the security fix (single-user local tool; don't over-engineer).

---

## Phase 0 — Baseline (30 min)

- Run `pytest tests/unit tests/integration` with `venv/bin/python`; record pass/fail baseline.
- Save the PoC script from `SEC-ASMT-REVIEW.md` as `tests/poc/regression_poc.sh` (health 200, /hosts 401 without cookie, WS handshakes, traversal URLs) — it must FAIL on findings A/B now and PASS after Phases 1–2.

**Files:** new `tests/poc/regression_poc.sh`

---

## Phase 1 — Finding A: WebSocket authentication (Critical, ~2h)

**Problem:** `BaseHTTPMiddleware` never sees WS scopes; all three WS endpoints accept unauthenticated connections (local shell = RCE surface).

**Changes:**
1. `src/security/session_auth.py` — add `validate_ws(websocket) -> str | None`: checks `websocket.cookies.get("surf_ssh_session")`, falls back to `token` query param; returns client identity or `None`.
2. New `src/api/ws_auth.py` — `require_ws_auth(websocket) -> str | None`: calls `validate_ws` against `websocket.app.state.session_manager`; on failure sends `{"type":"error","message":"Unauthorized"}` and `close(code=4401)` **before** `accept()` (Starlette rejects the handshake with HTTP 403).
3. `src/api/terminal.py`, `src/api/local_terminal.py`, `src/api/liveness.py` — first statement of each handler: `if require_ws_auth(websocket) is None: return`.
4. `src/daemon/server.py` — delete the dead WS-token branch in `SessionAuthMiddleware` (never executes); add comment that middleware is HTTP-only by design.
5. `src/daemon/server.py` — ensure `app.state.session_manager` is set before routers (already done).

**Tests:** integration — WS handshake without cookie → 403/4401, not 101; with valid cookie → 101. Cover all three endpoints.

**Risks:** browsers send cookies on WS same-origin — no frontend change needed. `token` query param kept for non-browser clients.

---

## Phase 2 — Finding B: Static path traversal (Critical, ~30 min)

**Problem:** `static.py:serve_spa_asset` joins unvalidated path; encoded `..%2f` reads arbitrary files (`/etc/passwd` PoC).

**Changes:**
1. `src/daemon/static.py` — in `serve_spa_asset`:
   ```python
   file_path = (_STATIC_DIR / path).resolve()
   if not file_path.is_relative_to(_STATIC_DIR.resolve()):
       raise HTTPException(404)
   ```
2. Same containment for `serve_spa_root` (trivial — fixed path, just verify).

**Tests:** unit — `..%2f`, `%2e%2e`, absolute path, symlink-in-dist edge cases all → 404; normal asset → 200.

**Risk:** `is_relative_to` needs 3.9+ (project requires 3.10 — OK). Symlinks inside `dist` would break; none exist.

---

## Phase 3 — Findings C, 1, 2: Session hardening (Medium, ~2h)

**Problem:** tokens never expire, filename = token, files world-readable; 28 stale files observed.

**Changes — all in `src/security/session_auth.py`:**
1. **Hashed filenames:** store as `sha256(token).json`; `create/validate/destroy` all hash the token before building the path. File content keeps `{"token": ..., "created": ...}` (content no longer needs the token — store only `created`; simplify to `{"created": ...}`).
2. **TTL:** `SESSION_TTL = 24h`; `validate_session` rejects expired files and unlinks them.
3. **Permissions:** write with `os.open(..., O_CREAT|O_WRONLY, 0o600)` or `write_text` + `os.chmod(0o600)`.
4. **Startup cleanup:** in `__init__`, purge (a) legacy `{token}.json` files (old format — invalidates stale tabs, acceptable), (b) expired hashed files.
5. `src/daemon/tls.py` — `os.chmod(key_path, 0o600)` after each key write in `_generate_ca` and `_generate_localhost_cert`; also chmod pre-existing key files in `ensure_tls_certificates` (fixes already-world-readable keys on upgrade).

**Tests:** unit — TTL expiry, hashed filename (filename ≠ token), file mode 0600, legacy purge, validate/destroy round-trip.

**Risk:** existing logged-in tabs from previous daemon runs are logged out — intended.

---

## Phase 4 — Finding 4: Symlink traversal enforcement (Medium, ~1h)

**Problem:** `realpath()` exists in `SFTPClient` but is never enforced in file/tree/download handlers.

**Change:** `src/api/files.py` + `src/api/tree.py` — after `validate_path`, call `sftp.realpath(host, validated)` and re-run `validate_path` on the result (rejects `..` in resolved paths, guarantees absolute). Note: this tool intentionally browses the whole remote FS (user has terminal access anyway) — the fix closes `..`-resolution through symlinks, not root confinement.

**Tests:** unit — validator on resolved paths; integration optional (needs live SSH — mark skipif no host).

**Risk:** extra SFTP round-trip per request (~ms). Cache per (host, path) with small TTL if it shows in profiling — don't pre-optimize.

---

## Phase 5 — Finding 5: Daemon mode auth UX (Low, ~1h)

**Changes:**
1. `src/main.py:daemon` — generate a session token at startup; print `https://localhost:{port}/api/v1/auth/exchange?token=...` to console/log (LaunchDaemon log). `--browser` opens this URL instead of bare `/ui`.
2. `ui/src/App.tsx` (or `StatusBar.tsx`) — on 401 from any API call, show explicit "Unauthorized — restart surf-ssh to get a fresh URL" state instead of rendering the shell chrome.

**Tests:** manual + integration: daemon-mode `/ui` still serves SPA (static), API without cookie → 401 (already true).

---

## Phase 6 — Finding 6: OpenAPI docs (Low, ~15 min)

**Change:** `src/daemon/server.py` — `docs_url=None, openapi_url=None` unless env `SURF_SSH_DEV=1`.

**Test:** integration — `/api/v1/docs` → 404 in default mode.

---

## Phase 7 — Finding 7: Per-tab client ID (Low, ~1h)

**Problem:** `f"http:{token}"` collapses multiple tabs into one liveness client → premature auto-exit.

**Changes:**
1. `ui/src/api/client.ts` — generate `crypto.randomUUID()` per tab (sessionStorage), send as `X-Client-ID` header in `apiFetch`.
2. `src/daemon/server.py` middleware — `client_id = request.headers.get("X-Client-ID") or f"http:{token}"`.

**Tests:** unit-level middleware test with/without header; manual multi-tab auto-exit check.

---

## Phase 8 — Finding 8: Terminal slot leak (Low, ~1.5h)

**Problem:** reaper skips SSH close while `terminal_counts > 0`; frozen WS never releases its slot.

**Changes — `src/ssh/connection_pool.py`:**
1. Convert `_terminal_counts: dict[str, int]` → `_terminal_slots: dict[str, set[str]]` (host → set of client_ids). `acquire_terminal_slot(host, client_id)`, `release_terminal_slot(host, client_id)` become idempotent per client_id.
2. `src/api/terminal.py` — pass `terminal_client_id` to acquire/release.
3. Reaper: when evicting a stale client with `session_type == "terminal"`, release its slot, then re-evaluate zero-client close.

**Tests:** unit — acquire/release/reaper-eviction sequences; no negative counts, no cross-session release.

---

## Phase 9 — Finding 9: Download size cap (Low, ~30 min)

**Change:** `src/api/files.py:download_file` — `stat` first; if `size > MAX_DOWNLOAD_SIZE` (2 GiB constant), return 413 with clear message. `/file` endpoint already caps in-memory reads at 50 MB; streaming branch gets the same stat check.

**Tests:** unit with mocked SFTP stat.

---

## Phase 10 — Finding 10: Update check privacy (Informational, ~30 min)

**Changes:**
1. `src/main.py:open` — add `--no-update-check` flag.
2. `src/update_check.py` — cache last check timestamp in `~/.surf-ssh/config.json`; check at most once per 24h.

**Test:** unit — cache suppresses repeat calls.

---

## Verification (after all phases)

1. `pytest tests/unit tests/integration` — green.
2. `tests/poc/regression_poc.sh` — all PoCs now fail to exploit: WS → 403, traversal → 404, sessions dir shows hashed names with 0600, keys 0600.
3. Manual: `surf-ssh open <host>` full flow (browse, terminal, markdown images), multi-tab, Ctrl+C shutdown, auto-exit.
4. Update `README.md` Security section + bump `surf-ssh-ver.txt` (0.2.2 → 0.3.0 — security release).

## Effort Summary

| Phase | Finding | Complexity | Est. |
|---|---|---|---|
| 1 | A — WS auth | Medium | 2h |
| 2 | B — traversal | Trivial | 0.5h |
| 3 | C/1/2 — sessions | Medium | 2h |
| 4 | 4 — symlink | Low | 1h |
| 5 | 5 — daemon UX | Low | 1h |
| 6 | 6 — docs | Trivial | 0.25h |
| 7 | 7 — client ID | Low | 1h |
| 8 | 8 — slot leak | Medium | 1.5h |
| 9 | 9 — download cap | Trivial | 0.5h |
| 10 | 10 — update check | Trivial | 0.5h |
| — | Tests + PoC + docs | — | 2h |

**Total: ~12.5h.** Phases 1–3 (criticals + session hardening) are one day of work and should ship first as v0.3.0.
