# surf-ssh — TOTP 2FA Architecture Design

**Status:** Proposed
**Date:** 2026-09-09
**Inspiration:** uhu-hub `dev-docs/2FA-ARCH.md` (same TOTP pattern, adapted for a local single-user tool)

---

## 1. Problem Framing

**What:** Add a TOTP (RFC 6238) challenge as an alternative authentication path for daemon mode. A user who opens `https://localhost:8443` directly in a browser — without running `surf-ssh url` — can authenticate by entering a 6-digit code from Google Authenticator (or any TOTP app).

**Why:** The control socket (`surf-ssh url`) requires a terminal round-trip. A bookmarked URL + 6 digits is a better daily UX for service-mode deployments (LaunchDaemon/systemd), where the daemon runs for weeks.

**The security problem this solves:** without 2FA, auto-issuing a token on page load is impossible to do safely — the daemon cannot distinguish the user's browser from any other local process (TCP to 127.0.0.1 carries no user identity; `SO_PEERCRED` works only on Unix domain sockets). A valid TOTP code is proof of possession of the authenticator secret, which no local process has. This converts "open localhost → get a shell" from security theater into a real boundary.

**Requirements:**
- Setup: `surf-ssh setup-2fa` CLI command prints QR + `otpauth://` URI, confirm with one code
- Login: browser opens `https://localhost:8443` → TOTP challenge page → session cookie
- Recovery: hashed backup codes + `surf-ssh disable-2fa` CLI escape hatch
- Rate limiting on the verify endpoint (non-negotiable — see §8)
- Control socket path remains, unchanged, for CLI users
- No password step (single-user localhost context — see §4)

**Constraints:**
- Single-user, localhost-only daemon
- No external dependencies beyond `pyotp` + `qrcode`
- Existing session/cookie machinery reused as-is
- Daemon mode only; `open` mode keeps its current one-shot token flow

---

## 2. Component Breakdown

```
┌────────────────────────────────────────────────────────────────────┐
│                         Browser (SPA)                              │
│                                                                    │
│   Direct visit: https://localhost:8443                             │
│        │  no valid session cookie                                  │
│        ▼                                                           │
│   ┌────────────────────┐      ┌──────────────────────────┐        │
│   │ TOTP Challenge View │─────▶│ Host list (existing UI) │        │
│   │ 6-digit input       │      │ + session cookie set     │        │
│   └────────────────────┘      └──────────────────────────┘        │
│        │  POST /api/v1/auth/verify-totp {code}                    │
└────────┼───────────────────────────────────────────────────────────┘
         │
┌────────▼───────────────────────────────────────────────────────────┐
│                     FastAPI Daemon                                 │
│                                                                    │
│  ┌──────────────────┐   ┌────────────────────────────────────┐   │
│  │ totp.py (NEW)    │   │ SessionAuthMiddleware (existing)    │   │
│  │ - gen secret     │   │ + exempt /auth/verify-totp           │   │
│  │ - verify code    │   │   from cookie requirement           │   │
│  │ - QR URI         │   │ + rate-limit verify endpoint        │   │
│  │ - backup codes   │   │   (global, 5/min, lockout)           │   │
│  │                  │   └────────────────────────────────────┘   │
│  └────────┬─────────┘                                              │
│           │                                                        │
│  ┌────────▼─────────────────────────────────────────────────────┐  │
│  │ ~/.surf-ssh/totp.json   (0600, plaintext secret)              │  │
│  │ {secret, enabled, backup_codes: [sha256 hashes]}             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  Control socket (unchanged): surf-ssh url → mint, no TOTP         │
└────────────────────────────────────────────────────────────────────┘
```

**New components:**

| Component | Location | Responsibility |
|---|---|---|
| `TotpManager` | `src/security/totp.py` (new) | Secret generation, code verification (±1 step), QR URI, backup code gen/hash/verify |
| TOTP challenge endpoint | `src/api/auth.py` (new) | `POST /api/v1/auth/verify-totp` — verify code or backup code, set session cookie |
| Rate limiter | `src/security/totp.py` or middleware | Global limiter on verify endpoint: 5 attempts/min, exponential lockout |
| Challenge view | `ui/src/components/TotpChallenge.tsx` (new) | 6-digit input, backup-code toggle, error/lockout states |
| CLI commands | `src/main.py` | `surf-ssh setup-2fa`, `surf-ssh disable-2fa` |

**Modified components:**

| Component | Change |
|---|---|
| `SessionAuthMiddleware` | Exempt `POST /api/v1/auth/verify-totp` from cookie auth (it *is* the auth); everything else unchanged |
| `App.tsx` | On 401, render `TotpChallenge` instead of the plain "Unauthorized" screen (when 2FA is enabled) |
| `daemon` command | Startup hint mentions both paths: `surf-ssh url` and direct browser + 2FA |

---

## 3. Data Flows

### 3.1 First-time setup (CLI)

```
1. surf-ssh setup-2fa
2. TotpManager generates secret (pyotp.random_base32())
3. CLI renders QR in terminal (qrcode lib, ASCII output) + prints otpauth:// URI
4. User scans with Google Authenticator (multiple devices can scan the same QR)
5. CLI prompts: "Enter the 6-digit code to confirm"
6. TotpManager.verify(code) → on success, persist:
     ~/.surf-ssh/totp.json  (0600)
     {secret, enabled: true, backup_codes: [10 × sha256]}
7. CLI prints the 10 backup codes (xxxx-xxxx format) — shown ONCE
```

### 3.2 Browser login (daily flow)

```
1. User opens https://localhost:8443 (bookmarked)
2. SPA loads; API calls return 401 (no cookie)
3. SPA renders TotpChallenge view
4. User types 6-digit code → POST /api/v1/auth/verify-totp {code}
5. Daemon: rate-limit check → TotpManager.verify(code)
6. Valid → create session (existing SessionManager) → set cookie → 200
7. SPA loads host list — identical to the socket-minted path from here on
```

### 3.3 Backup code usage

```
4b. Device lost: user clicks "Use backup code" → enters xxxx-xxxx
5b. Daemon: sha256(code) matches a stored hash → consume it (remove from list)
    → session cookie issued
```

### 3.4 Recovery (lost device + lost backup codes)

```
1. Any local terminal (same user): surf-ssh disable-2fa
2. Deletes ~/.surf-ssh/totp.json
3. Direct browser access now falls back to... nothing (401) —
   the control socket path still works (surf-ssh url)
4. Re-enroll anytime: surf-ssh setup-2fa
```

---

## 4. Decision Rationale

| Decision | Why |
|---|---|
| **TOTP as the only factor — no password** | uhu-hub is multi-user over the network; surf-ssh is single-user on localhost. There is nothing a password protects against that TOTP doesn't already cover. A password would be ceremony. |
| **Plaintext secret at 0600, not Fernet-encrypted** | uhu-hub encrypts because a DB leak there is remotely exploitable. Here, an attacker who can read `~/.surf-ssh/totp.json` can already read `~/.ssh/id_*` — encryption without a user-derived key is obfuscation, and there is no password to derive a key from. Documented, deliberate trade. |
| **Setup via CLI, not web UI** | Matches the tool's CLI-first ethos; avoids a chicken-and-egg web setup flow (the web UI requires auth, which requires 2FA being set up). |
| **Global rate limiter, not per-IP** | All traffic is 127.0.0.1 — per-IP limiting is meaningless. The limiter must be global on the verify endpoint. |
| **Control socket unchanged** | It already provides kernel-enforced same-user auth. Two paths, same security level, different UX. |
| **Backup codes hashed (sha256)** | File-read protection parity with session files. Single-user context: sha256 is sufficient (bcrypt adds latency for no threat-model benefit). |
| **±1 time step tolerance** | Standard TOTP practice; pyotp default. Handles minor clock drift. |
| **Daemon mode only** | `open` mode is short-lived with a one-shot token; adding 2FA there is friction without benefit. |

---

## 5. Technology Choices

| Component | Choice | Justification |
|---|---|---|
| TOTP | `pyotp` | De facto standard, 2KB, no deps — same as uhu-hub |
| QR (terminal) | `qrcode` (ASCII output) | Renders in any terminal; no PIL needed for ASCII mode |
| Secret storage | `~/.surf-ssh/totp.json`, 0600 | Consistent with session file hardening |
| Rate limiting | In-memory sliding window | Single-process daemon; no Redis needed |
| Backup codes | 10 × `xxxx-xxxx`, sha256-hashed | Same UX as uhu-hub |

**New dependencies:** `pyotp`, `qrcode` — both lightweight, well-established.

---

## 6. API Changes

```
POST /api/v1/auth/verify-totp          (NEW — exempt from cookie auth)
  Request:  {code: "123456"}                    -- TOTP code
  Request:  {backup_code: "xxxx-xxxx"}          -- backup code
  Response: 200 → session cookie set (surf_ssh_session)
  Response: 401 {detail: "invalid_code"}        -- wrong code, attempts remaining
  Response: 429 {detail: "locked", retry_after: N}  -- rate limit hit
```

No other API changes. The existing `/auth/exchange` (socket-minted tokens) is untouched.

---

## 7. File & Storage Changes

```
~/.surf-ssh/
├── totp.json        # NEW — 0600: {secret, enabled, backup_codes: [sha256]}
└── ...              # (existing files unchanged)
```

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Brute force of 6-digit code** | **Global rate limiter: 5 attempts/min, exponential lockout (1min → 2 → 4 → ... → 30min cap).** Without it, localhost brute force succeeds in <1 minute — this is the critical detail of the whole design. |
| Lost device + lost backup codes | `surf-ssh disable-2fa` (local, same-user) — the escape hatch |
| Time drift on server | ±1 step (30s) tolerance — pyotp default |
| Backup code reuse | Consumed on first use, removed from list |
| Secret file read by same-user malware | Out of scope — such malware already owns `~/.ssh` |
| Lockout UX | Clear "retry after N seconds" messaging; lockout is per-daemon (in-memory), resets on restart |
| User enrolls, loses phone same day | Backup codes shown once at setup — docs must emphasize saving them |

---

## 9. Operational Concerns

- **Rate limiter state is in-memory** — resets on daemon restart. Acceptable: restart requires local access, which is the same trust level as the socket path.
- **Security logging:** log verify success/failure (no code values) to daemon log.
- **Docs:** README security section + this doc; setup flow must emphasize backup codes.
- **Testing:** unit tests for TotpManager (verify, drift, backup codes, lockout); integration test for the endpoint (valid code, invalid code, rate limit, backup code); E2E: setup → challenge → cookie → host list.

---

## 10. Implementation Effort

| Item | Effort |
|---|---|
| `src/security/totp.py` — TotpManager (secret, verify, QR URI, backup codes, rate limiter) | 1.5 hr |
| `src/api/auth.py` — verify-totp endpoint + middleware exemption | 1 hr |
| CLI: `setup-2fa`, `disable-2fa` (QR render, confirm, backup codes) | 1 hr |
| Frontend: TotpChallenge view + 401 routing | 1 hr |
| Tests (unit + integration) | 1.5 hr |
| Docs (this file + README) | 0.5 hr |
| **Total** | **~6.5 hr** |
