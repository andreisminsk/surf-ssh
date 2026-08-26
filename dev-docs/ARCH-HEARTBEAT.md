# Heartbeat & Liveness Architecture — surf-ssh

## 1. Problem Framing

**What we're building:** A server-side liveness layer that detects dead browser clients within ~10–15s and proactively cleans up SSH connections when no clients remain for a host.

**Core requirements:**
- Detect dead clients fast (browser crash, tab close without close frame, laptop sleep)
- Cover both terminal WebSocket sessions and idle file-browsing sessions
- Trigger SSH connection cleanup when all clients for a host are gone
- Work on Windows ProactorEventLoop and macOS
- Integrate with existing `ConnectionPool`, `TerminalManager`, `LocalPtyManager`

**Key constraint:** The daemon is single-user, local-first. No horizontal scaling concerns. Simplicity and reliability matter more than throughput.

## 2. Component Breakdown

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser (SPA)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Terminal WS  │  │  Liveness WS │  │  HTTP file ops    │  │
│  │ (existing)   │  │   (NEW)      │  │  (existing)       │  │
│  └──────┬───────┘  └──────┬───────┘  └─────────┬─────────┘  │
│         │                 │                    │             │
│         │ app-level       │ app-level          │ registers    │
│         │ ping/pong       │ ping/pong          │ activity     │
└─────────┼─────────────────┼────────────────────┼────────────┘
          │                 │                    │
┌─────────┼─────────────────┼────────────────────┼────────────┐
│         ▼                 ▼                    ▼             │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │TerminalMgr  │  │LivenessMgr   │  │  HTTP endpoints    │  │
│  │(existing)   │  │  (NEW)       │  │  (files/tree/hosts)│  │
│  └──────┬──────┘  └──────┬───────┘  └─────────┬──────────┘  │
│         │                │                    │              │
│         │  ref acquired   │  ref acquired      │ ref acquired │
│         │  / released     │  / released        │ / released   │
│         ▼                 ▼                    ▼              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              ConnectionPool (extended)                  │ │
│  │  ┌──────────────────────────────────────────────────┐   │ │
│  │  │  ClientRegistry (NEW)                             │   │ │
│  │  │  host → {client_id, last_seen, type}              │   │ │
│  │  └──────────────────────────────────────────────────┘   │ │
│  │  ┌──────────────────────────────────────────────────┐   │ │
│  │  │  ReaperTask (NEW)                                 │   │ │
│  │  │  every 5s: evict stale clients, close host conns  │   │ │
│  │  │  with zero live clients                            │   │ │
│  │  └──────────────────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### New Components

| Component | Location | Responsibility |
|---|---|---|
| **ClientRegistry** | `src/ssh/connection_pool.py` (inside `ConnectionPool`) | Tracks active clients per host: `{client_id, last_seen, session_type}`. Reference counting. |
| **LivenessManager** | `src/api/liveness.py` (new file) | WebSocket endpoint `/api/v1/liveness`. Server-initiated ping every 10s, expects pong within 15s. |
| **ReaperTask** | `src/ssh/connection_pool.py` | Background asyncio task, runs every 5s. Evicts stale clients, closes SSH connections with zero live clients. |
| **ClientActivityMiddleware** | `src/security/session_auth.py` or `server.py` | Updates `last_seen` for the client on every HTTP request to `/api/v1/hosts/{host}/*`. |

### Modified Components

| Component | Change |
|---|---|
| `ConnectionPool` | Add `register_client(host, client_id, session_type)`, `unregister_client(host, client_id)`, `touch_client(host, client_id)`, `get_live_client_count(host)`. Start/stop reaper in lifespan. |
| `terminal.py` | On WS connect: `pool.register_client(host, client_id, "terminal")`. On disconnect: `pool.unregister_client(host, client_id)`. |
| `local_terminal.py` | No host association — local PTYs don't hold SSH connections. No change needed. |
| `server.py` lifespan | Start reaper task on startup, cancel on shutdown. |
| Frontend (`ui/src/`) | Open a `LivenessWebSocket` on app mount, reconnect on drop. Send pongs. |

## 3. Decision Rationale

### Decision 1: Dedicated Liveness WebSocket (not piggybacking on terminal WS)

**Why:** Terminal WebSockets only exist when a terminal is open. File browsing uses plain HTTP — no persistent connection to piggyback on. A dedicated liveness WS covers all states: browsing, terminal open, idle.

**Alternative considered:** HTTP long-polling. Rejected — more complex, higher overhead, doesn't cover the "browser crash" case any better than WS.

### Decision 2: Server-initiated ping, not client-initiated

**Why:** The server is the authority that needs to know if the client is alive. Server-initiated ping puts the detection logic in one place. Client-initiated requires the server to track "last message received" and timeout — same complexity but distributed across all endpoints.

**How:** Server sends `{"type": "ping"}` every 10s. Client must respond `{"type": "pong"}` within 15s (3 missed pings = dead). On dead detection, server closes the WS, which triggers `WebSocketDisconnect` in any active terminal WS too.

### Decision 3: Application-level ping/pong, not WebSocket protocol-level ping

**Why:** Starlette/FastAPI's `WebSocket` API doesn't expose `websocket.ping()` / `pong()` control frames reliably across platforms. Application-level JSON messages are debuggable, work identically on Windows and macOS, and the frontend already handles JSON messages for terminals.

**Trade-off:** Slightly more overhead (JSON vs 2-byte control frame). Negligible for single-user local daemon.

### Decision 4: Per-host reference counting in ConnectionPool

**Why:** The pool already owns SSH connections and has `_terminal_counts`. Extending it with a `ClientRegistry` is the natural home. When `get_live_client_count(host) == 0`, the reaper closes that host's connection immediately rather than waiting 600s.

**Reference counting model:**
```
register_client(host, client_id, "terminal")   → count +1
unregister_client(host, client_id)              → count -1
touch_client(host, client_id)                   → update last_seen
```

Multiple session types (terminal, liveness, http-activity) can register for the same host. The connection stays alive as long as **any** client is live.

### Decision 5: HTTP activity tracking via middleware (lightweight)

**Why:** File browsing generates HTTP requests. Each request to `/api/v1/hosts/{host}/...` proves the client is alive and interacting. The middleware extracts the host from the path and calls `pool.touch_client(host, client_id)`.

**Client identity:** Use the session cookie value as `client_id`. One browser tab = one session = one `client_id`. The liveness WS and HTTP requests share the same cookie, so they're the same client.

### Decision 6: ReaperTask as asyncio background task, not a thread

**Why:** The reaper only does async operations (close SSH connections, update dicts). An asyncio task integrates with the event loop cleanly. On Windows ProactorEventLoop, asyncio tasks work fine — the signal handler issue was specific to `add_signal_handler`, not general asyncio.

**Lifecycle:** Started in `lifespan` startup, cancelled in shutdown. No `os._exit` concerns — it's cancelled before the force-exit safety net.

## 4. Recommended Approach (Sequence)

### Normal operation
```
1. Browser loads UI → opens Liveness WS → pool.register_client(host, session_id, "liveness")
2. User opens terminal → Terminal WS connects → pool.register_client(host, session_id, "terminal")
3. User browses files → HTTP request → middleware calls pool.touch_client(host, session_id)
4. Reaper runs every 5s → checks all clients → all alive → no action
5. Server sends ping every 10s → client responds pong → touch_client updates last_seen
```

### Tab close (clean)
```
1. Browser closes → Liveness WS close frame → WebSocketDisconnect
2. LivenessManager: pool.unregister_client(host, session_id)
3. Terminal WS (if open) close frame → WebSocketDisconnect → unregister
4. Reaper: get_live_client_count(host) == 0 → close SSH connection immediately
```

### Browser crash (no close frame)
```
1. Browser killed → no close frame sent
2. Server sends ping at t=10s → no pong
3. Server sends ping at t=20s → no pong
4. Server sends ping at t=30s → no pong (3 missed)
5. LivenessManager marks client dead → unregister_client
6. Reaper: get_live_client_count(host) == 0 → close SSH connection
7. Total detection time: ~30s (3 × 10s ping interval)
```

**Tuning for 10–15s target:** Use ping interval = 5s, timeout = 15s (3 missed). This detects dead clients in 15s while keeping overhead low (1 ping/5s = 0.2 msg/s).

### Multiple tabs
```
- Each tab has its own Liveness WS but shares the session cookie
- Option A: Use WebSocket.id as client_id (per-connection) → each tab counted separately
- Option B: Use session cookie as client_id → one tab closing doesn't drop count
- RECOMMENDED: Option A (per-connection) — more accurate, prevents one tab masking another
```

## 5. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Reaper closes SSH connection while SFTP operation in flight | File operation fails mid-transfer | Check `get_live_client_count` but also check active SFTP semaphores. Don't close if `sftp_semaphore` has active acquisitions. Add a 5s grace period after last client leaves before closing. |
| Liveness WS reconnects after brief network blip | Unnecessary SSH close + reconnect | Add 10s grace period in reaper: don't close connection until `now - last_client_left > 10s`. Handles reconnect window. |
| Client sends pong but server doesn't process in time (event loop busy) | False positive dead detection | Use 15s timeout (3× the 5s ping interval). Only mark dead after 3 consecutive missed pongs, not 1. |
| Frontend doesn't implement pong handler | All clients marked dead immediately | Frontend must handle `{"type": "ping"}` → respond `{"type": "pong"}`. Add fallback: if no liveness WS after 30s, rely on existing 600s idle timeout. Graceful degradation. |
| Reaper task crashes | Connections never cleaned up | Wrap reaper body in try/except, log errors, reschedule on next interval. Don't let one failure kill the loop. |
| Windows ProactorEventLoop quirks with WS close | `WebSocketDisconnect` not raised cleanly | Already handled by existing `_silenced_call_connection_lost` patch in `server.py`. The liveness WS uses the same Starlette WS implementation. |
| `client_id` collision (two tabs, same cookie) | One tab's disconnect drops the other's count | Use `id(websocket)` or a UUID per connection, not the cookie. Cookie is only for auth. |

## 6. Operational Concerns

**Tuning knobs (all configurable, with sensible defaults):**
```python
PING_INTERVAL = 5          # seconds between pings
PING_TIMEOUT = 15          # seconds before marking client dead (3 missed)
REAPER_INTERVAL = 5        # seconds between reaper runs
GRACE_PERIOD = 10          # seconds after last client leaves before closing SSH
```

**Observability:** Log at INFO level: client registered/unregistered, connection closed by reaper, client marked dead. These are infrequent events — no log spam.

**Graceful degradation:** If the frontend doesn't open a liveness WS (old cached UI, JS error), the system falls back to the existing 600s idle timeout. No regression.

**Testing strategy:**
- Unit test `ClientRegistry` register/unregister/touch/evict
- Unit test reaper logic with mock timestamps
- Integration test: open liveness WS, kill it without close frame, verify SSH connection closed within ~20s
- Integration test: open terminal + liveness, close liveness only, verify SSH stays alive (terminal still active)

## 7. Implementation Order

1. **`ClientRegistry` + reaper in `ConnectionPool`** — core logic, no I/O, unit testable
2. **`LivenessManager` WebSocket endpoint** — new file, simple ping/pong loop
3. **Wire into `server.py` lifespan** — start/stop reaper
4. **Register/unregister in `terminal.py`** — add 2 calls in existing try/finally
5. **`ClientActivityMiddleware`** — touch on HTTP requests to host-scoped routes
6. **Frontend liveness WS** — open on app mount, handle ping→pong, reconnect on drop
7. **Grace period + SFTP safety check** — final polish

---

**Bottom line:** A dedicated liveness WebSocket with server-initiated pings (5s interval, 15s timeout) + per-host reference counting in the existing `ConnectionPool` + a background reaper task. This detects dead clients in ~15s, covers all session types, and degrades gracefully to the existing 600s timeout if the frontend doesn't cooperate. Six small components, no new dependencies, works on both Windows and macOS.
