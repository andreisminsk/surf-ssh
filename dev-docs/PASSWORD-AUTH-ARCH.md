# surf-ssh — Password-Based SSH Authentication Architecture

**Status:** Implemented
**Date:** 2026-09-09

---

## 1. Problem Framing

**What:** Support SSH hosts that have no key in `~/.ssh/config` — the user
supplies a hostname (optionally username/port) and a password, then browses
files and opens terminals exactly as with any config host.

**Why it's architecturally interesting:** everything downstream of
`ConnectionPool.get_connection()` assumes a config alias with pre-resolved
auth. Password auth introduces three new problems:

1. A **credential lifecycle** the pool never had (retain? expire? re-prompt?)
2. An **interactive prompt** in a flow that is currently non-interactive
3. **Host key trust** for machines not in `known_hosts`

**Threat-model anchor:** the existing boundary is "same local user =
trusted" (control socket 0600, session files 0600, TOTP for browser). The
password design must not weaken that boundary. Within it, an attacker who
can scrape daemon memory already owns `~/.ssh/id_*` — so in-memory password
retention is *not* a regression. That single fact licenses the design below.

**Non-goals:**
- No password persistence to disk (v1 — see §4, keychain is future work)
- No changes to `SSHConfigParser` (stays read-only)
- No changes to tree/file/terminal/reaper — they key off the host string

---

## 2. Component Breakdown

```
Browser UI                          Daemon
─────────────                       ──────
HostPicker                          SSHConfigParser (untouched)
  └─ "Add host" form                  └─ config aliases only
      hostname/user/port
                                    AdHocHostRegistry (NEW, in-memory)
On auth failure →                     "adhoc:user@host:port" → {hostname, user, port}
  PasswordPrompt view                ConnectionPool (extended)
      │                                connections[alias]   (synthetic alias)
      │ POST /api/v1/hosts/{alias}/auth  _passwords[alias]   (memory only)
      ▼                              asyncssh.connect(..., password=pw)
  retry connect → tree renders      HostKeyTrust (NEW)
                                       ~/.surf-ssh/known_hosts (separate file)
```

**New components:**

| Component | Location | Responsibility |
|---|---|---|
| `AdHocHostRegistry` | `src/ssh/adhoc.py` (new) | In-memory map of synthetic alias → {hostname, user, port}; dies with daemon |
| Password path in pool | `src/ssh/connection_pool.py` | `_passwords: dict[alias, str]`; passed to `asyncssh.connect()`; cleared on `close_all()` |
| Auth endpoint | `src/api/hosts.py` | `POST /api/v1/hosts/{alias}/auth` — session-protected; stores password, triggers connect |
| Host key trust | `src/ssh/host_keys.py` (new) | TOFU against `~/.surf-ssh/known_hosts`; fingerprint surfaced to UI |
| Add-host + password UI | `ui/src/components/` | HostPicker "Add host" form; PasswordPrompt view; fingerprint confirm dialog |

**Modified components:**

| Component | Change |
|---|---|
| `ConnectionPool.get_connection()` | Look up ad-hoc registry; pass `password=` when present |
| `/api/v1/hosts` list | Merge config aliases + live ad-hoc registry entries |
| `HostPicker` | "Add host" button + form (hostname, user, port) |

**Unchanged (by design):** tree, file, terminal, liveness, reaper, client
registry — ad-hoc hosts are just pool aliases to them.

---

## 3. Data Flows

### 3.1 Add host (daemon mode, browser)

```
1. HostPicker → "Add host" → form: hostname, username (opt), port (opt)
2. POST /api/v1/hosts/adhoc {hostname, user, port}
   → daemon creates synthetic alias "adhoc:user@host:port"
   → registry entry (no connection yet)
   → returns {alias}
3. UI navigates to /ui?host=adhoc:user@host:port
4. First tree/status request → pool tries to connect
5. AsyncSSH raises AuthError (no key, password required)
   → API returns 401 {detail: "password_required", host: alias}
6. UI renders PasswordPrompt
7. POST /api/v1/hosts/{alias}/auth {password}
   → pool stores password in memory → retries connect
8. Success → tree renders; password retained for reconnects
```

### 3.2 CLI path (`surf-ssh open user@host`)

```
1. CLI detects "user@host" pattern (not a config alias)
2. getpass() prompt at startup (terminal, not browser)
3. Same pool path: synthetic alias, memory-held password
4. Browser opens with ?host=adhoc:user@host:port
```

### 3.3 Reconnect after idle eviction

```
1. Reaper closes idle connection (600s, no live clients)
2. User clicks a file → get_connection() → reconnect with retained password
3. No re-prompt — retention is what makes browsing usable
```

### 3.4 Wrong password

```
7a. AsyncSSH AuthError (wrong password)
    → 401 {detail: "invalid_password"}
    → UI re-prompts (max 5/min per host — see §5)
```

### 3.5 Host key unknown (TOFU)

```
5a. AsyncSSH raises HostKeyNotKnown (fingerprint X)
    → 419 {detail: "host_key_unknown", fingerprint: "SHA256:..."}
    → UI shows fingerprint dialog → user confirms
    → POST /api/v1/hosts/{alias}/trust {fingerprint}
    → appended to ~/.surf-ssh/known_hosts (0600)
    → connect retried
```

### 3.6 Host key CHANGED (danger)

```
5b. AsyncSSH raises HostKeyChanged
    → 419 {detail: "host_key_changed", fingerprint: "SHA256:..."}
    → UI shows scary warning (possible MITM) — explicit re-confirm required
    → same trust endpoint, but UI must not make it a one-click habit
```

---

## 4. Decision Rationale

| Decision | Why |
|---|---|
| **Retain password in daemon memory for its lifetime** | The reaper closes idle connections; without retention every reconnect (common while browsing) re-prompts — unusable. Memory retention is safe *within the existing threat model*: same-user malware already reads `~/.ssh` keys directly. |
| **Never persist to disk — no config.json, no "remember" file** | A password on disk outlives the daemon and the user's intent; it's the one thing in this system with no recovery story. Plaintext disk storage would be the first real regression of the session. |
| **No OS keychain in v1** | Keychain is the only *correct* persistence (macOS Keychain / Windows Credential Manager), but it's platform-specific code for marginal benefit in a single-user tool. Defer; document as future work. |
| **Separate `~/.surf-ssh/known_hosts`** | Don't write to the user's `~/.ssh/known_hosts` (surprising side effect); don't skip host key checking (MITM hole). TOFU with explicit fingerprint confirm in the UI, stored in our own file. |
| **Prompt on auth failure, not upfront** | Try key auth first (the host may have a key the user didn't know about); only prompt when AsyncSSH reports password auth is required. Fewer prompts; the prompt context ("host X needs a password") is self-explanatory. |
| **Auth endpoint behind session auth** | The opposite of `verify-totp`: submitting a password *requires* an already-authenticated session (cookie/2FA). Never exempt it from middleware — a password endpoint reachable pre-auth would be a remote-host brute-force oracle. |
| **Synthetic aliases, not raw host strings** | Ad-hoc hosts get daemon-generated aliases (`adhoc:user@host:port`); `{host}` path params map to registry entries only. No user-controlled string flows into pool keys unvalidated. |
| **Suggest `ssh-copy-id` in UX** | Password SSH is a fallback; keys remain the better path. A one-line hint nudges users toward the secure steady state without blocking them. |

---

## 8. Host Key Validation for Config Hosts (post-implementation fix)

**Found during E2E testing:** `get_connect_options()` passed
`known_hosts=None` for config aliases — **disabling host key validation
entirely** for every host in `~/.ssh/config`. A MITM between the daemon
and any config host would go undetected. Ironic given the careful TOFU
built for ad-hoc hosts.

**Fix:** config aliases validate against the user's real
`~/.ssh/known_hosts` (via the sanitized config file, which AsyncSSH
loads). Ad-hoc hosts keep the TOFU flow (`~/.surf-ssh/known_hosts`).
Unknown/changed keys for config hosts surface the same 419 taxonomy —
the UI fingerprint dialog works for both host types.

| Host type | Validation | Trust file |
|---|---|---|
| Config alias | Full known_hosts validation | `~/.ssh/known_hosts` (user's own) |
| Ad-hoc | TOFU with fingerprint confirm | `~/.surf-ssh/known_hosts` (ours, 0600) |

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Password in swap / crash dumps** | Acknowledged limitation of Python (no mlock). Single copy, held only by the pool; never in tracebacks (log auth *events*, never values). Same trust tier as `~/.ssh` keys. |
| **Daemon becomes a password-spray tool against remote hosts** | Endpoint requires session auth; log per-host auth-failure counts; the only attacker who can reach it is the one who already owns the machine. |
| **Keylogging in browser** | Equivalent to typing the password into any terminal; out of scope for a local tool. |
| **User locks remote account (MaxAuthTries)** | Surface the remote error verbatim; don't auto-retry; rate-limit our own re-prompt attempts per host (5/min) to prevent accidental lockouts. |
| **Ad-hoc alias injection into pool keys** | Aliases are daemon-generated; `{host}` path params resolve against the registry, never raw. |
| **Password survives daemon "restart" confusion** | Registry + passwords are memory-only by design: restart = clean slate, browser re-prompts. Document this as a feature. |
| **MITM via host key change** | TOFU with explicit fingerprint confirmation; changed keys get a scary warning, not a one-click accept. |

---

## 6. Operational Concerns

- **Reaper interaction:** unchanged — password-backed connections are just
  pool entries; zero-client → close, next request → reconnect with retained
  password.
- **Observability:** log "password auth succeeded/failed for adhoc host X"
  (no values); count consecutive failures per host.
- **Host list:** `/api/v1/hosts` merges config aliases + live ad-hoc
  registry entries, so the picker shows what you connected to this session.
- **Testing:** unit (registry, alias generation, password retention/clear),
  integration (auth endpoint behind session auth, failure taxonomy, TOFU
  flow), E2E (add host → prompt → tree).

---

## 7. Implementation Effort

| Item | Effort |
|---|---|
| `AdHocHostRegistry` + pool password path | 2 hr |
| Auth endpoint + failure taxonomy (401/419 codes) | 1.5 hr |
| UI: add-host form, password prompt, fingerprint dialog | 2.5 hr |
| Host key trust (`~/.surf-ssh/known_hosts`, TOFU) | 1 hr |
| Tests (unit + integration) | 1.5 hr |
| **Total** | **~8.5 hr** |
