# Password Auth Implementation Plan — surf-ssh

**Date:** 2026-09-09
**Input:** `dev-docs/PASSWORD-AUTH-ARCH.md`
**Goal:** Ad-hoc hosts with password auth, browser-collected, memory-held, disk-never — behind existing session auth.

**Guiding rules:**
- Password never touches disk, logs, or tracebacks — events only, never values.
- Auth/trust endpoints stay behind session middleware (opposite of verify-totp).
- Ad-hoc hosts are synthetic pool aliases — zero changes to tree/file/terminal/reaper.
- Every phase ends green: `pytest tests/unit tests/integration` + UI build.

---

## Phase 0 — Baseline (15 min)

- Run full suite (142 expected green), `cd ui && npm run build`.
- Confirm no regressions before touching the pool.

---

## Phase 1 — AdHocHostRegistry + pool password path (2h)

**New `src/ssh/adhoc.py`:**
- `AdHocHostRegistry`: in-memory `dict[alias → {hostname, user, port}]`
- `make_alias(user, hostname, port) -> "adhoc:user@host[:port]"` — canonical, daemon-generated
- `resolve(alias) -> HostConfig | None`; `register(...) -> alias`; `list_hosts() -> [alias]`
- Validation: hostname must be non-empty, no `/`, no whitespace; port 1–65535; user optional

**Modify `src/ssh/connection_pool.py`:**
- `_passwords: dict[str, str]` (memory only)
- `set_password(alias, pw)` / `clear_password(alias)`; `close_all()` clears all
- `get_connection()`: if alias in ad-hoc registry → build connect params from registry entry; pass `password=self._passwords.get(alias)` to `asyncssh.connect()`
- `get_host_config()` path: registry lookup first, config parser fallback (existing behavior unchanged for config aliases)

**Tests:** `tests/unit/test_adhoc.py` — alias generation/canonicalization, validation rejections, register/resolve/list, registry isolation from config parser. Pool: password set/clear, close_all wipes.

**Risk:** pool currently assumes `cfg["hostname"]`/`cfg["port"]` always exist — registry entries must satisfy the same shape.

---

## Phase 2 — Host key trust, TOFU (1.5h)

**New `src/ssh/host_keys.py`:**
- `HostKeyTrust(config_dir)`: reads/writes `~/.surf-ssh/known_hosts` (0600)
- `check(alias, hostname, port) -> Trusted | Unknown(fingerprint) | Changed(fingerprint)`
- `trust(alias, hostname, port, fingerprint)` — appends entry
- Use `asyncssh.known_hosts` integration: pass our file to `connect(known_hosts=...)`; catch `HostKeyNotKnown`/`HostKeyChanged` in the pool and translate to typed exceptions

**Modify pool:** wrap connect in host-key check; on Unknown/Changed raise typed `HostKeyError(fingerprint, kind)` instead of generic ConnectionError.

**Tests:** `tests/unit/test_host_keys.py` — empty file → Unknown; trust → Trusted; mismatched key → Changed; file perms 0600; survives registry restart (file-backed, unlike passwords).

**Risk:** AsyncSSH exception taxonomy — verify exact exception types for unknown/changed keys against the installed version before coding the translation.

---

## Phase 3 — API endpoints + failure taxonomy (1.5h)

**Modify `src/api/hosts.py`:**

```
POST /api/v1/hosts/adhoc        {hostname, user?, port?}  → {alias}     (session-protected)
POST /api/v1/hosts/{alias}/auth {password}                 → 200 | 401  (session-protected)
POST /api/v1/hosts/{alias}/trust {fingerprint}             → 200        (session-protected)
```

**Failure taxonomy (all JSON bodies, no password values ever):**
- `401 {"detail": "password_required"}` — key auth failed, password auth available
- `401 {"detail": "invalid_password"}` — wrong password
- `419 {"detail": "host_key_unknown", "fingerprint": "SHA256:..."}` 
- `419 {"detail": "host_key_changed", "fingerprint": "SHA256:..."}` — UI must render as a warning, not an error
- `423 {"detail": "locked", "retry_after": N}` — per-host re-prompt rate limit

**Auth flow:** store password in pool → attempt connect → map exceptions to taxonomy → on success return `{status: "connected"}`.

**Modify `/api/v1/hosts` list:** merge config aliases + `registry.list_hosts()` (mark ad-hoc entries, e.g. `"source": "adhoc"`).

**Tests:** `tests/integration/test_adhoc_api.py` — endpoints 401 without session cookie (the brute-force-oracle guard), full add→auth→list flow with mocked pool, taxonomy mapping, trust flow.

**Risk:** `{alias}` contains `@` and `:` — verify FastAPI path param encoding handles it (it does, but test it).

---

## Phase 4 — Per-host rate limit + logging (1h)

**In `hosts.py` (or small `src/security/prompt_limit.py`):**
- Sliding window per alias: 5 auth attempts/min → `423` with `retry_after`
- Reset on success (same pattern as TotpManager — reuse the shape, don't over-abstract)
- Logging: `logger.info("password auth failed for %s (attempt %d)")` — host alias only, never the password; consecutive-failure counter per host

**Tests:** limit trips at 5, resets on success, per-host isolation.

---

## Phase 5 — UI (2.5h)

**Modify `ui/src/App.tsx` (HostPicker):**
- "Add host" button → inline form (hostname, user, port) → `POST /hosts/adhoc` → navigate `?host={alias}`

**New `ui/src/components/PasswordPrompt.tsx`:**
- Rendered when API returns `password_required` or `invalid_password`
- Password input (type=password), submit → `POST /hosts/{alias}/auth`
- On `423` show countdown; on success reload tree
- Footer hint: `ssh-copy-id user@host` to switch to key auth

**New fingerprint dialog (in App or `HostKeyDialog.tsx`):**
- `host_key_unknown`: show fingerprint, "Trust" button → trust endpoint → retry
- `host_key_changed`: red warning ("possible MITM"), explicit confirm, not one-click

**Wiring:** centralize API-error interception — a small `useAuthFlow` hook or error-mapping in `useConnection`/`useFileSystem` that routes taxonomy codes to the right view.

**Verify:** `npm run build` clean; manual flow against a real password host if available.

**Risk:** error taxonomy must reach the UI from *every* host-scoped call (tree, file, status) — not just the auth endpoint. Intercept at the fetch layer, not per-component.

---

## Phase 6 — CLI path (1h)

**Modify `src/main.py` `open` command:**
- Detect `user@host` or bare hostname that is NOT a config alias
- `getpass()` prompt (terminal); register ad-hoc alias; `pool.set_password()`
- Browser opens with `?host={alias}` — everything else unchanged
- Host key unknown in CLI mode: print fingerprint, `typer.confirm()` TOFU prompt

**Tests:** unit — alias detection logic (config alias vs user@host vs bare host); CLI flow manual.

---

## Phase 7 — E2E + docs (1h)

- E2E against a real password-only host (user provides): add host → prompt → tree → terminal → idle-eviction reconnect (no re-prompt) → wrong password → lockout
- README: features + security sections (password auth, memory-only, TOFU file)
- `dev-docs/PASSWORD-AUTH-ARCH.md`: status → Implemented
- Version bump 0.4.0 → 0.5.0

---

## Effort Summary

| Phase | Item | Est. |
|---|---|---|
| 0 | Baseline | 0.25h |
| 1 | Registry + pool password path | 2h |
| 2 | Host key TOFU | 1.5h |
| 3 | Endpoints + taxonomy | 1.5h |
| 4 | Rate limit + logging | 1h |
| 5 | UI | 2.5h |
| 6 | CLI path | 1h |
| 7 | E2E + docs | 1h |
| — | **Total** | **~10.75h** |

**Hard invariants (checked in review):**
1. No password in any file write, log line, or exception message
2. Auth/trust endpoints return 401 without a session cookie
3. `close_all()` and daemon exit leave zero password copies
4. Config-alias flow byte-identical to before (regression suite green)
