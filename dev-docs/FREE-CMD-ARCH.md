# surf-ssh — Free-Form Command to Config Architecture

**Status:** Implemented
**Date:** 2026-09-15

---

## 1. Problem Framing

**What:** Extend `surf-ssh open` to accept an arbitrary SSH/SCP command string,
parse it into a `~/.ssh/config` entry under a generated (or user-specified)
name, and open the browser against that alias.

**Example:**
```bash
# Ephemeral — auto-generated phonetic name, removed on exit
surf-ssh open --cmd "ssh root@10.0.0.5 -i ~/.ssh/key -p 2222"

# Persistent — user-chosen name, stays in config forever
surf-ssh open --cmd "ssh root@10.0.0.5 -i ~/.ssh/key -p 2222" --save prod-jump
```

**Why:** The most common surf-ssh friction is "I have a one-off SSH command
from a runbook/ticket/colleague, but no config alias." Today the user must
manually translate `-i`, `-p`, `-J` into a config block. This feature
eliminates that translation step.

**Core tension:** writing to `~/.ssh/config` is a side effect on a file the
user considers theirs. Every other surf-ssh feature reads config; this one
mutates it. The design must make that mutation safe, reversible, and never
destructive to existing content.

**The `--save <name>` approach:** instead of a separate `--config-save`
boolean, persistence and naming are combined into one optional flag:

| Invocation | Name | Persistence |
|---|---|---|
| `--cmd "..."` (no `--save`) | Auto-generated phonetic (e.g., `quiet-river-stone`) | Ephemeral — removed on daemon exit |
| `--cmd "..." --save prod-jump` | User-specified (`prod-jump`) | Persistent — stays in config |

This is simpler (one flag, not two), more ergonomic (the user names what
they want to keep), and avoids the "ephemeral with a random name that I
then want to keep" trap (the user just re-runs with `--save`).

---

## 2. Component Breakdown

```
surf-ssh open --cmd "ssh root@host -i ~/.ssh/key -p 2222" [--save prod-jump]

  │
  ▼
┌─────────────────────────────────────────────────────┐
│  CmdParser (NEW)                                     │
│  shlex.split → map -i/-p/-l/-J/-o/user@host          │
│  → {hostname, user, port, identityfile, proxyjump}   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  NameGenerator (NEW)                                 │
│  --save present? → use user name (collision check)   │
│  --save absent?  → adjective-noun-noun from lists    │
│  → "quiet-river-stone" or "prod-jump"                │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  ConfigEditor (NEW)                                  │
│  Append marker-delimited block to ~/.ssh/config      │
│  ┌───────────────────────────────────────────────┐  │
│  │ # surf-ssh:quiet-river-stone:ephemeral          │  │
│  │ Host quiet-river-stone                          │  │
│  │     HostName 10.0.0.5                            │  │
│  │     User root                                    │  │
│  │     Port 2222                                    │  │
│  │     IdentityFile ~/.ssh/key                      │  │
│  │ # end:surf-ssh:quiet-river-stone                 │  │
│  └───────────────────────────────────────────────┘  │
│  remove_entry(name) — finds markers, deletes block   │
│  remove_all_ephemeral() — startup crash cleanup      │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
              Existing flow: open browser
              with ?host=quiet-river-stone

  On daemon exit (no --save):
    ConfigEditor.remove_entry("quiet-river-stone")
```

**New components:**

| Component | Location | Responsibility |
|---|---|---|
| `CmdParser` | `src/ssh/cmd_parser.py` (new) | `shlex.split` + arg mapping → config dict |
| `NameGenerator` | `src/ssh/naming.py` (new) | Phonetic generation + collision check; or user name validation |
| `ConfigEditor` | `src/ssh/config_editor.py` (new) | Marker-delimited append/remove in `~/.ssh/config` |

**Modified components:**

| Component | Change |
|---|---|
| `src/main.py` `open` command | New `--cmd` and `--save` flags; parse → name → write → open; cleanup hook in `finally` |
| `src/ssh/config_parser.py` | No change — reads the file fresh each call; new entries are picked up automatically |

---

## 3. Data Flows

### 3.1 Ephemeral (no `--save`)

```
1. surf-ssh open --cmd "ssh root@10.0.0.5 -i ~/.ssh/key -p 2222"
2. CmdParser.parse("ssh root@10.0.0.5 -i ~/.ssh/key -p 2222")
   → {hostname: "10.0.0.5", user: "root", port: 2222, identityfile: "~/.ssh/key"}
3. NameGenerator.generate() → "quiet-river-stone" (collision-checked)
4. ConfigEditor.append("quiet-river-stone", config_dict, tag="ephemeral")
   → writes marker-delimited block to ~/.ssh/config (atomic)
5. host = "quiet-river-stone" → existing open flow (token, browser, daemon)
6. Daemon runs; user browses, terminals, etc.
7. Daemon exits (auto-exit / Ctrl+C / crash)
   → finally: ConfigEditor.remove_entry("quiet-river-stone")
   → startup next time: remove_all_ephemeral() catches crash leftovers
```

### 3.2 Persistent (`--save <name>`)

```
1. surf-ssh open --cmd "ssh root@10.0.0.5 -i ~/.ssh/key -p 2222" --save prod-jump
2. CmdParser.parse(...) → same config dict
3. NameGenerator: user provided "prod-jump"
   → validate (alphanumeric + hyphens, no spaces, not an existing alias)
   → collision check against SSHConfigParser.list_hosts()
4. ConfigEditor.append("prod-jump", config_dict, tag="persistent")
5. host = "prod-jump" → existing open flow
6. Daemon exits → finally: skip persistent entries (tag check)
7. "prod-jump" remains in ~/.ssh/config — usable by plain ssh too
```

### 3.3 Startup crash cleanup

```
1. surf-ssh open (or daemon) starts
2. ConfigEditor.remove_all_ephemeral()
   → scan ~/.ssh/config for "# surf-ssh:*:ephemeral" markers
   → remove any blocks found (orphaned by a previous crash)
3. Proceed with normal startup
```

---

## 4. Decision Rationale

| Decision | Why |
|---|---|
| **`--save <name>` instead of `--config-save`** | Combines naming + persistence in one flag. Simpler UX: the user names what they want to keep. Avoids the "ephemeral with random name that I now want to keep" trap. |
| **Marker-delimited blocks** | Cleanup must be surgical. Markers (`# surf-ssh:{name}:ephemeral` / `# end:surf-ssh:{name}`) make removal a simple find-and-delete — no parsing of user content, no risk of deleting hand-written blocks. |
| **shlex + manual arg mapping** | We're parsing command-line args, not config syntax. `shlex.split` handles quoting; a ~40-line mapper covers common flags. Full SSH option parsers (paramiko) are overkill and don't handle `user@host` positional syntax. |
| **Phonetic names (adjective-noun-noun)** | Memorable, self-documenting ("I created this from a command"). ~200×200×200 = 8M combinations. Collision-checked against config + in-memory set. |
| **Write to real `~/.ssh/config`, not a separate file** | Existing parser reads it directly. Bonus: the alias is usable by plain `ssh`. Side effect is mitigated by markers + atomic writes + cleanup. |
| **Ephemeral cleanup in `finally` + startup pass** | `finally` covers normal exits; startup `remove_all_ephemeral()` covers crashes. Belt and suspenders. |
| **Atomic write (temp + `os.replace`)** | Prevents config corruption if the process is killed mid-write. |
| **`scp` support via same parser** | `scp -P 2222 file user@host:/path` differs only in `-P` (capital port) and source/dest positionals (ignored). Parser checks first token to map flags correctly. |

---

## 5. SSH Flag Mapping

| SSH flag | Config key | Notes |
|---|---|---|
| `user@host` (positional) | `HostName` + `User` | Split on `@` |
| `host` (bare positional) | `HostName` | User from `-l` or absent |
| `-i <path>` | `IdentityFile` | Expand `~` |
| `-p <port>` (ssh) | `Port` | |
| `-P <port>` (scp) | `Port` | Capital P in scp |
| `-l <user>` | `User` | Overrides `user@` |
| `-J <jump>` | `ProxyJump` | |
| `-o Key=Value` | passthrough | Each `-o` becomes a config line |

**Rejected:** `ssh -G` (dump resolved config). Spawns a subprocess, produces 100+ lines of defaults, not consistently available. Manual mapping is simpler and sufficient.

---

## 6. Marker Format

```
# surf-ssh:quiet-river-stone:ephemeral
Host quiet-river-stone
    HostName 10.0.0.5
    User root
    Port 2222
    IdentityFile ~/.ssh/key
# end:surf-ssh:quiet-river-stone
```

- `:ephemeral` — removed on daemon exit + startup cleanup
- `:persistent` — kept forever (created with `--save`)
- Comments are invisible to SSH, greppable by us: `grep surf-ssh ~/.ssh/config`
- `ConfigEditor.remove_entry(name)` finds start/end markers, deletes all lines between (inclusive)

---

## 7. Name Generation

### Phonetic (no `--save`)

**Format:** `adjective-noun-noun` (e.g., `quiet-river-stone`, `violet-forest-path`)

**Word lists:** ~200 adjectives, ~200 nouns (embedded in `naming.py`). Three words → ~8M combinations.

**Collision avoidance:**
1. Check `SSHConfigParser.list_hosts()` — reject if alias exists
2. Check in-memory `_generated_names` set — reject if used this session
3. Regenerate on collision (max 10 attempts, then append a digit)

### User-specified (`--save <name>`)

**Validation:**
- Alphanumeric + hyphens only (`^[a-z0-9][a-z0-9-]*$`)
- 1–63 characters
- Not an existing alias (collision check)
- Not a reserved word (`Host`, `Match`, `Include`)

**On collision:** refuse with a clear error: `"prod-jump" already exists in ~/.ssh/config. Choose a different name or remove the existing entry.`

---

## 8. Config Editor Safety

| Concern | Mitigation |
|---|---|
| **Corrupting user's config** | Atomic write: read full file → append block → write to temp → `os.replace`. Never modify existing lines. |
| **Stale ephemeral entries after crash** | Startup cleanup: `remove_all_ephemeral()` on every `surf-ssh open` and `surf-ssh daemon` start. |
| **Concurrent writes** | `os.replace` is atomic per-write. Single-user context makes true concurrency unlikely. |
| **File permissions** | Preserve existing mode/owner. If file doesn't exist, create with `0600`. |
| **`Include` directives** | Appended block is at end of main file — `Include` doesn't affect it. |
| **Large config files** | Read-append-write is O(n) in file size; typical config is <10KB. Non-issue. |

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Corrupting `~/.ssh/config`** | Atomic write; never modify existing content; only append/remove marked blocks |
| **Stale ephemeral entries after crash** | Startup cleanup pass removes all `:ephemeral` markers |
| **Name collision** | Check `list_hosts()` + in-memory set; regenerate (phonetic) or refuse (user-specified) |
| **Malformed command string** | `CmdParseError` with clear message; CLI prints it, exits 1, never touches config |
| **`ProxyJump` to a bare hostname** | Written as-is; SSH resolves it. If the jump host is also ad-hoc, the user can chain two `--cmd` invocations. |
| **`-o` options conflicting with mapped fields** | Both lines written; SSH's "first match wins" applies. Document, don't reconcile. |
| **User dislikes config mutation** | Ephemeral-by-default (removed on exit); `--save` is opt-in; markers are visible and greppable |

---

## 10. Operational Concerns

- **Startup cleanup:** every `surf-ssh open` and `surf-ssh daemon` start calls `remove_all_ephemeral()`. Fast (one file read, grep for markers).
- **Logging:** `"created config entry {name} from command ({tag})"` and `"removed ephemeral entry {name} on exit"`.
- **`--save` feedback:** after writing a persistent entry, print: `Config saved as 'prod-jump'. Find it in ~/.ssh/config or remove it manually.`
- **Daemon mode:** `--cmd` doesn't apply to `surf-ssh daemon` (no specific host). A future `surf-ssh add-host --cmd "..."` could serve daemon mode. Out of scope for v1.
- **Testing:** unit (CmdParser for each flag/scp/malformed, NameGenerator uniqueness/validation, ConfigEditor append/remove/atomic/cleanup/preserve), integration (full `--cmd` → config written → parser reads it → connection works).

---

## 11. Implementation Effort

| Item | Effort |
|---|---|
| `src/ssh/cmd_parser.py` — shlex parse + arg mapping | 1.5 hr |
| `src/ssh/naming.py` — word lists + generator + validation | 1 hr |
| `src/ssh/config_editor.py` — marker append/remove + atomic write + startup cleanup | 2 hr |
| `src/main.py` — `--cmd` / `--save` flags + cleanup hook | 1 hr |
| Tests (unit + integration) | 1.5 hr |
| Docs (this file + README) | 0.5 hr |
| **Total** | **~7.5 hr** |

---

## 12. Honest Counterpoints

- **Writing to `~/.ssh/config` is the most opinionated thing surf-ssh does.** Every other feature is read-only or writes to `~/.surf-ssh/`. Some users will dislike it. The alternative (separate `~/.surf-ssh/config` merged at read time) avoids the side effect but breaks the "usable by plain `ssh`" bonus and adds parser complexity. Markers + atomic writes + ephemeral-by-default mitigate the risk; user feedback will tell if it's enough.
- **The parser will never be 100% SSH-compatible.** Edge cases (`ProxyCommand`, `Match` blocks, unusual `~` expansion, Windows paths) will surface. Handle the 95% case well; fail loudly (not silently) on the rest.
- **Phonetic names add embedded word lists.** Alternative: `surf-<hash>` (zero-maintenance, less memorable). Phonetic wins on UX; the lists are ~5KB total.
