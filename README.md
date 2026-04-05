# Nanobot WhatsApp Gateway

An AI-powered WhatsApp sales assistant for insurance workflows, with a Python gateway backend and a React operator UI.

This project now uses a **project-local runtime model**:

- config is read from this repo's [config.json](config.json) unless you explicitly override it
- sessions are stored in [sessions](sessions)
- reply-target and contact data are stored in [data](data)
- per-client memory is stored under [memory](memory) as `memory/{phone}/MEMORY.md` and `memory/{phone}/HISTORY.md`
- shared operator knowledge is stored in [memory/GLOBAL.md](memory/GLOBAL.md)
- auth/browser state are stored in [whatsapp-auth](whatsapp-auth) and [whatsapp-web](whatsapp-web)
- backend activity journal is stored in [state/activity_journal.jsonl](state/activity_journal.jsonl)

It does **not** intentionally route runtime state to legacy home-folder paths such as `~/.nanobot/workspace` during normal project usage.

## Workspace Confinement Update

Based on the current `git diff`, the container/runtime path model was changed to keep state inside this project checkout instead of falling back to home-directory storage.

### What changed in [Dockerfile](Dockerfile)

Before:

- the image created `/root/.nanobot`

Now:

- the image creates explicit project-local runtime directories under `/app`
- these include `/app/data`, `/app/sessions`, `/app/state`, `/app/memory`, `/app/whatsapp-auth`, `/app/whatsapp-web`, `/app/whatsapp-web-debug`, `/app/skills`, `/app/media`, and `/app/cron`

Why this changed:

- `/root/.nanobot` encouraged a hidden container-global storage pattern
- `/app/...` matches the repo layout and makes container behavior consistent with local development
- runtime state is easier to inspect, back up, mount, and verify from the project folder

### What changed in [docker-compose.yml](docker-compose.yml)

Before:

- one broad home-directory mount: `~/.nanobot:/root/.nanobot`

Now:

- explicit bind mounts for [config.json](config.json), [data](data), [sessions](sessions), [state](state), [memory](memory), [whatsapp-auth](whatsapp-auth), [whatsapp-web](whatsapp-web), [whatsapp-web-debug](whatsapp-web-debug), and [skills](skills)

Why this changed:

- it removes silent dependence on `~/.nanobot`
- it makes the active runtime files obvious
- it keeps Docker, the host checkout, and the path confinement rules aligned
- it reduces the chance of old laptop state leaking into a fresh project run

### Practical result

If you clone this repo onto a laptop and run it normally, the intended storage model is:

- config in [config.json](config.json)
- sessions in [sessions](sessions)
- contacts and reply targets in [data](data)
- memory in [memory](memory)
- auth/browser state in [whatsapp-auth](whatsapp-auth) and [whatsapp-web](whatsapp-web)

That same model now applies to Docker and Compose.

## How to keep everything inside one workspace

Use this checklist if you want the whole system restrained to this single repo directory.

1. Keep `agents.defaults.workspace` project-local.
  - recommended: `"."`
  - also fine: `"sessions"`, `"data"`, or another subdirectory inside this repo
  - do **not** use `~`, `/tmp/...`, or another folder outside the repo

2. Turn on tool restraint.
  - set `tools.restrictToWorkspace` to `true`
  - this keeps agent tool access scoped to the configured workspace directory

3. Keep path-like channel settings repo-relative.
  - use values like `whatsapp-web`, `data/whatsapp_reply_targets.json`, and `data/contacts/whatsapp.json`
  - do **not** use `~/.nanobot/...`

4. Prefer the installed wrapper commands.
  - `whatsapp-web-nanobot-ui`
  - `whatsapp-web-nanobot-gateway`
  - these point back to this checkout's [config.json](config.json)

5. In Docker, mount this repo's folders into `/app/...`.
  - do not reintroduce `~/.nanobot:/root/.nanobot`

6. Use `python3 -m nanobot status` after setup.
  - confirm the reported config path, workspace path, sessions path, memory path, data path, auth path, and browser path are all project-local

It also now enforces a **per-client data isolation model**:

- WhatsApp client identity is normalized through `ClientKey` before session or API lookup
- client memory is no longer shared across all chats
- history import and reply-target matching reject cross-client mismatches instead of trying to guess

---

## Recent Data-Flow Changes

This README has been updated to reflect the current code paths without rewriting the rest of the document.

### Added

- `ClientKey` as the canonical WhatsApp client identity model for phone normalization and session derivation
- per-client memory directories under `memory/{phone}/`
- shared read-only operator knowledge in [memory/GLOBAL.md](memory/GLOBAL.md)
- `SessionManager.get_for_client(...)` and API lookup paths that resolve sessions from normalized client identity
- stricter history-import guards: empty-phone rejection, normalized phone matching, and explicit cross-client assertion
- stricter reply-target matching rules for direct chats and group-name collisions
- regression coverage in `tests/test_client_isolation.py`
- dedicated isolation documentation in [ISOLATION.md](ISOLATION.md)

### Removed or retired

- shared client memory in legacy `memory/MEMORY.md` and `memory/HISTORY.md` as the active per-client store
- implicit raw-string session lookup for WhatsApp clients
- direct-chat routing that could rely on stale display names
- history import behavior that could accept entries without a validated client phone
- the old assumption that one visible WhatsApp name safely identifies one client

---

## Quick Start

### Recommended flow for every laptop

Run this once after cloning the repo:

```bash
cd /path/to/Nanobot-Whatsapp
python3 -m nanobot install-ui-command
```

That installs two stable wrapper commands for **this project checkout**:

- `whatsapp-web-nanobot-ui`
- `whatsapp-web-nanobot-gateway`

After that, the normal daily command is:

```bash
whatsapp-web-nanobot-ui
```

What it does:

1. uses this repo's [config.json](config.json)
2. uses this repo as the working directory
3. starts the lightweight launcher/API on port `3456` if needed
4. starts the React frontend from [Insurance frontend](Insurance%20frontend)
5. waits for you to log in from the UI
6. after UI login, `/api/login` boots the full gateway in-process

So the **correct normal operator flow** is:

1. run `whatsapp-web-nanobot-ui`
2. open the frontend URL shown by Vite
3. log in in the UI
4. if WhatsApp auth is needed, complete it in the UI / linked browser
5. work from the UI

If you only want the backend without the React UI, run:

```bash
whatsapp-web-nanobot-gateway
```

---

## What the wrapper commands really are

The wrapper commands are **small shell scripts installed on each laptop**. They are not magical global binaries shared across all machines.

Each teammate must run:

```bash
python3 -m nanobot install-ui-command
```

on their own clone.

The installed wrappers:

- point to this repo checkout
- export `NANOBOT_CONFIG_PATH` to this repo's [config.json](config.json)
- prefer this repo's `.venv/bin/python` when available
- run `python -m nanobot ui` or `python -m nanobot gateway` underneath

That gives everyone the same command names, while still keeping storage and config anchored to their local clone of this project.

---

## Command Reference

### Primary operator commands

| Command | Purpose | Effect |
|---|---|---|
| `python3 -m nanobot install-ui-command` | one-time installer | installs global wrapper commands for this checkout |
| `whatsapp-web-nanobot-ui` | normal daily start command | starts launcher if needed, then starts the frontend UI |
| `whatsapp-web-nanobot-gateway` | backend-only start | starts the full gateway stack without the React UI |
| `python3 -m nanobot ui` | non-wrapper UI start | starts the frontend and ensures launcher is reachable |
| `python3 -m nanobot launcher` | launcher-only start | starts lightweight pre-login API/WS server on `3456` |
| `python3 -m nanobot gateway` | full backend start | starts bridge, CDP, agent loop, channels, cron, heartbeat, API |

### Setup and inspection commands

| Command | Purpose |
|---|---|
| `python3 -m nanobot onboard` | initialize or refresh project config and workspace files |
| `python3 -m nanobot status` | print config path, workspace path, and provider status |
| `python3 -m nanobot agent -m "..."` | run the agent directly from terminal |
| `python3 -m nanobot provider login <provider>` | authenticate an OAuth-based provider |

### Channel management commands

| Command group | Purpose |
|---|---|
| `python3 -m nanobot channels status` | show enabled channel configuration |
| `python3 -m nanobot channels whatsapp-contacts init/list/add/remove` | manage local WhatsApp direct contacts |
| `python3 -m nanobot channels whatsapp-groups init/list/add/remove` | manage WhatsApp group allowlist rules |

---

## Correct Flows and Their Effects

### Flow A — Recommended daily UI workflow

```bash
whatsapp-web-nanobot-ui
```

Effect:

1. verifies or starts the launcher on `http://127.0.0.1:3456`
2. starts Vite for [Insurance frontend](Insurance%20frontend)
3. keeps the app in **UI-first** mode
4. after UI login, starts the full gateway
5. UI becomes the main control surface for sync, draft review, sending, and monitoring

Use this when a human agent is actively working from the dashboard.

### Flow B — Backend-only workflow

```bash
whatsapp-web-nanobot-gateway
```

Effect:

1. starts the full Python gateway immediately
2. starts the bridge and CDP browser path
3. starts the API server on `3456`
4. lets you operate without launching the React UI

Use this for backend debugging, service-style runs, or when you do not need the dashboard open.

### Flow C — Development without wrappers

```bash
python3 -m nanobot launcher
python3 -m nanobot ui
```

or

```bash
python3 -m nanobot gateway
```

Effect:

- same runtime pieces as the wrappers
- more explicit for development and debugging
- useful if you do not want the installed global commands

### Flow D — One-time machine setup

```bash
python3 -m nanobot install-ui-command
```

Effect:

1. installs `whatsapp-web-nanobot-ui`
2. installs `whatsapp-web-nanobot-gateway`
3. optionally adds the install directory to shell `PATH`

---

## Project-Local Storage Map

All important runtime files for this project should live under this repo:

| Purpose | Path |
|---|---|
| project config | [config.json](config.json) |
| contacts | [data/contacts/whatsapp.json](data/contacts/whatsapp.json) |
| group members | [data/whatsapp_groups.csv](data/whatsapp_groups.csv) |
| reply targets | [data/whatsapp_reply_targets.json](data/whatsapp_reply_targets.json) |
| per-client long-term memory | [memory](memory) → `memory/{phone}/MEMORY.md` |
| per-client summarized history | [memory](memory) → `memory/{phone}/HISTORY.md` |
| shared operator knowledge | [memory/GLOBAL.md](memory/GLOBAL.md) |
| session bundles | [sessions](sessions) |
| journal | [state/activity_journal.jsonl](state/activity_journal.jsonl) |
| WhatsApp web profile | [whatsapp-web](whatsapp-web) |
| Baileys auth | [whatsapp-auth](whatsapp-auth) |
| bridge debug/profile state | [whatsapp-web-debug](whatsapp-web-debug) |

If you see commands or ad-hoc scripts pointing at `~/.nanobot/workspace`, treat those as legacy/manual paths, not the intended project runtime path.

The same storage layout applies in containers: [docker-compose.yml](docker-compose.yml) bind-mounts these project folders into `/app/...` inside the container so runtime state stays tied to this repo checkout instead of any home-directory fallback.

### Path Confinement Invariant

All runtime state is strictly confined to this project directory. The codebase enforces this via a centralized path module (`nanobot/utils/paths.py`) that:

- Derives the project root from the package location — no hard-coded home-directory fallbacks.
- Provides `confine_path()` which raises `PathEscapeError` if any resolved path escapes the project tree.
- Removes all `expanduser()` calls from runtime path resolution so that `~` is never silently expanded to the user's home directory.

**What was removed:** The legacy `~/.nanobot` migration system and all `_HOME_ROOT = Path.home() / ".nanobot"` references. If you have state left in `~/.nanobot` from an older install, copy it manually into the project directories listed above.

Run `nanobot status` to see all active paths and verify they are project-local.

Regression tests in `tests/test_path_confinement.py` enforce this invariant.

### Per-client isolation invariant

For WhatsApp data, the rule is:

> one client chat may read only that client's session bundle, that client's memory files, and shared global knowledge.

The current implementation enforces this by:

- normalizing every client phone through `ClientKey`
- deriving `whatsapp:{phone}` session keys from that normalized identity
- scoping `ContextBuilder` and `MemoryStore` to the same client key
- rejecting history imports and reply-target matches that fail phone-level validation

See [ISOLATION.md](ISOLATION.md) for the full invariant and forbidden patterns.

---

## Architecture Overview

The system is two entirely separate processes that communicate via HTTP and WebSocket:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Process 1: InsureAI Frontend  (Vite dev server, Node.js)           │
│  http://localhost:5173                                               │
│                                                                     │
│  React 18 + TypeScript + Tailwind CSS                               │
│                                                                     │
│  LoginPage → App → ClientList ──────────────────────────────────┐  │
│                         │                                        │  │
│                         ▼                                        ▼  │
│                   ClientCard (blue toggle)          ClientProfile   │
│                         │                           MessageThread  │
│                         │                           Composer Box   │
│                         └────── useNanobot hook ──────────────────┘  │
│                                     │                               │
│                          api.ts  websocket.ts                       │
│                            │         │                              │
│              Vite proxy: /api → :3456, /ws → :3456                 │
└─────────────────────────────────────────────────────────────────────┘
                              │ REST + WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Process 2: Nanobot Gateway  (python3 -m nanobot gateway)           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  ApiServer (aiohttp)  http://localhost:3456                  │   │
│  │  REST: /api/login, /api/clients, /api/messages,             │   │
│  │        /api/ai-draft, /api/ai-send, /api/auto-draft,        │   │
│  │        /api/auto-reply, /api/broadcast, /api/sync,          │   │
│  │        /api/status                                          │   │
│  │  WS:   /ws  (new_message, ai_generating, ai_draft,          │   │
│  │              auto_draft, auto_draft_changed, pong)          │   │
│  └─────────────────────┬───────────────────────────────────────┘   │
│                         │                                           │
│  ┌──────────────────────▼────────────────────────────────────────┐  │
│  │  MessageBus (asyncio queues)                                  │  │
│  │  inbound queue ──► agent loop        outbound queue ◄─ agent  │  │
│  │  inbound observers → ApiServer       outbound observers       │  │
│  │                                                               │  │
│  │  ui_connected = len(inbound_observers) > 0                    │  │
│  │  ↳ True  → capture_only=True, _auto_draft_candidate=True      │  │
│  │  ↳ False → agent processes and auto-replies directly          │  │
│  └──────────────────────┬────────────────────────────────────────┘  │
│           ┌─────────────┴────────────────┐                         │
│           ▼                              ▼                         │
│  ┌────────────────────┐      ┌───────────────────────┐            │
│  │  AgentLoop (LLM)   │      │  ChannelManager        │            │
│  │  litellm/OpenRouter│      │  WhatsApp channel       │            │
│  │  process_direct()  │      │   ├─ Baileys bridge     │            │
│  │  session snapshot  │      │   │  ws://localhost:3001 │            │
│  │  rollback on draft │      │   └─ CDP browser        │            │
│  └────────────────────┘      │      http://127.0.0.1:  │            │
│                              │      9222 (remote debug)│            │
│  ┌────────────────────┐      └───────────────────────┘            │
│  │  SessionManager    │                                            │
│  │  JSONL per contact │  CronService   HeartbeatService           │
│  └────────────────────┘                                            │
└─────────────────────────────────────────────────────────────────────┘
```

### WhatsApp connection layers

There are three distinct layers that connect to WhatsApp, all started by the gateway:

| Layer | Transport | Purpose | Auth |
|---|---|---|---|
| **Baileys bridge** | WebSocket `ws://localhost:3001` | Receives all live inbound messages via WhatsApp linked-device protocol; sends approved outbound messages after the agent clicks send in the UI | `whatsapp-auth/creds.json` |
| **CDP browser** | Chrome DevTools Protocol `http://127.0.0.1:9222` | Scrapes WhatsApp Web DOM for chat history on demand only; not part of the normal send path | Chrome profile at `whatsapp-web/` |
| **Frontend UI** | HTTP + WebSocket via Vite proxy | Agent-facing dashboard — the only place the human reviews and sends messages | UI login triggers `/api/login` on the launcher |

Both the Baileys bridge and the CDP browser are started **automatically and simultaneously** when the gateway starts. Baileys auth and WhatsApp Web auth are independent sessions — refreshing one does not affect the other.

### Two operating modes

The gateway operates in one of two modes based on whether the frontend UI is connected:

| Mode | Condition | Behavior |
|---|---|---|
| **Auto-reply (standalone)** | No UI WebSocket observer connected | Agent processes inbound messages, generates replies, and sends them to WhatsApp automatically via the channel. Used when the agent is away from the desk. |
| **Auto-draft (UI connected)** | Frontend WebSocket is connected | Inbound WhatsApp messages get `capture_only=True`. Agent saves them to session history but sends no reply. `ApiServer` mirrors the message to the UI and, if the client has `auto_draft: true`, generates a draft into the composer instead. |

The switch is automatic and instantaneous — it happens the moment the first WebSocket client connects or disconnects.

---

## End-to-End Workflow

### Step 1. Start the UI wrapper or launcher/UI pair

```bash
whatsapp-web-nanobot-ui
```

This is the recommended path. It does **not** start the full gateway immediately.

What starts first:

1. the lightweight launcher on `http://localhost:3456`
2. the Vite frontend for [Insurance frontend](Insurance%20frontend)

At this stage the UI can load before WhatsApp services fully boot.

If you prefer explicit development commands, the equivalent flow is:

```bash
cd /path/to/Nanobot-Whatsapp
python3 -m nanobot launcher

cd /path/to/Nanobot-Whatsapp/Insurance\ frontend
npm run dev
```

### Step 2. Log in from the frontend

Open the frontend URL shown by Vite.

On submit:

1. the login page calls `POST /api/login`
2. the launcher records the login and starts the full gateway in-process
3. the UI stays connected to the launcher/API during bootstrap
4. once the gateway is ready, the UI loads clients and opens live WebSocket updates

This is the current **UI-first** runtime flow.

### Step 3. What the full gateway starts after login

After `/api/login`, the gateway performs startup in sequence:

1. **Loads config** from `config.json` (or `NANOBOT_APP_CONFIG_PATH` / `NANOBOT_CONFIG_PATH`).
2. **Starts the Baileys bridge** — spawns the Node.js process (`bridge/`) on `ws://localhost:3001`. This also attaches or launches the CDP browser for WhatsApp Web simultaneously.
3. **Initializes core services** — `MessageBus`, `SessionManager`, `AgentLoop` (LLM), `ChannelManager`, `CronService`, `HeartbeatService`.
4. **Starts the API server** on `http://localhost:3456` — REST + WebSocket, CORS-enabled, ready to accept frontend connections immediately.

Vite proxies:
- `/api/*` → `http://localhost:3456/api/*`
- `/ws` → `ws://localhost:3456/ws`

This keeps frontend API traffic relative and project-local.

### Step 4. Baileys authenticates with WhatsApp

The bridge checks `whatsapp-auth/creds.json` on startup (before the frontend is even opened):

- **Valid session found:** reused silently. No QR code. The terminal shows `connected`.
- **No session or expired:** the bridge prints a QR code in the terminal. Scan from WhatsApp → Linked Devices on your phone. The session is saved and reused on subsequent restarts.

### Step 5. CDP browser attaches (for history scraping)

When CDP mode is enabled, the gateway tries `http://127.0.0.1:9222`:

- **Chrome already running with remote debugging:** Nanobot reuses the existing instance. If it has no `web.whatsapp.com` tab, it opens one.
- **No Chrome found:** Nanobot launches Chrome with `--remote-debugging-port=9222` using the persistent profile at `whatsapp-web/`. Opens `https://web.whatsapp.com/`. Sign in once; the profile is reused.

The bridge can reuse an existing logged-in WhatsApp Web browser, but CDP is only checked when a history scrape is requested.

### Step 5b. CDP & Bridge health protection

After startup the system continuously monitors the **Node bridge / Baileys path**, but CDP readiness for history scraping is checked **only on demand**:

| Check | Interval | Behaviour |
|---|---|---|
| **On-demand CDP scrape check** | Only when `POST /api/sync/{phone}` or `POST /api/bridge/check` is called | Python asks the bridge for a fresh one-shot CDP status. The bridge checks whether it can connect to CDP, whether a WhatsApp Web tab exists, and whether that tab is logged in and ready. If no CDP browser is reachable, it tries to launch the dedicated Chrome profile automatically. |
| **Bridge process monitor** | Every 5 s | `ApiServer` calls `bridge_proc.poll()`. This monitors the **Node.js bridge process itself** (which hosts Baileys and CDP helpers). If the process has exited, a `whatsapp_browser_status` WS event with `severity: "error"` is broadcast immediately. The frontend shows a **重启 Bridge** button. |
| **WS reconnect escalation** | After 12 failures (60 s) | `WhatsAppChannel` monitors the **Python ↔ Node bridge WebSocket connection**. If reconnecting to `ws://localhost:3001` fails 12 consecutive times, the status is escalated from `warning` → `error` severity and the message tells the user the bridge may have crashed or become unreachable. |

The on-demand CDP status returns precise scrape-readiness states:

| State | Meaning |
|---|---|
| `ready` | Existing WhatsApp Web session is reusable for history sync right now |
| `cdp_launch_failed` | Nanobot could not attach to CDP and also could not launch the dedicated Chrome profile |
| `whatsapp_web_login_required` | CDP browser is reachable (or was launched), but WhatsApp Web must be logged in before scraping |
| `scrape_not_ready` | CDP is reachable, but no usable WhatsApp Web tab is ready for history sync |

**Frontend notifications** are severity-aware:

| Severity | Colour | Title | Action button |
|---|---|---|---|
| `warning` | Yellow | 历史同步需要 WhatsApp Web | 检查 WhatsApp Web (`POST /api/bridge/check`) |
| `error` | Red | Bridge 异常 | 重启 Bridge (`POST /api/bridge/restart`) |

`POST /api/bridge/restart` performs a full kill → `rm -rf .bridge-build/` → rebuild from `bridge/` source → `npm start` → wait for WS readiness (up to 15 s).  
`POST /api/bridge/check` triggers the same one-shot scrape-readiness check used by `POST /api/sync/{phone}` and returns the fresh result immediately.

### Step 6. Startup history sync

Once Baileys reports `connected`, the gateway triggers a history sync for all enabled targets in `data/whatsapp_reply_targets.json`:

1. **Baileys history replay:** any direct-message history that Baileys already knows about is normalized and published into the `InboundHistoryBatch` queue.
2. **WhatsApp Web DOM scrape:** the CDP browser builds a target list from enabled `direct_reply_targets`, opens each target chat in WhatsApp Web, scrolls upward through the timeline, and extracts message text + timestamps from the DOM.

The sync path is intentionally strict:

1. The backend builds each scrape target from the allowlist row in [data/whatsapp_reply_targets.json](data/whatsapp_reply_targets.json).
2. Target matching prefers exact identifiers first: `phone` → bare `chat_id` → bare `sender_id` → normalized contact phone → names/labels only as fallback search terms.
3. The bridge stamps each scraped message batch with the requested target's `chatId` and `phone` before sending it back to Python.
4. Python re-matches every history message against the direct-reply allowlist using normalized phone and chat identifiers.
5. The importer writes only into the matching canonical session key derived from normalized client identity: `whatsapp:{phone}`.
6. Entries with an empty `phone` field are dropped before import.
7. The final history import layer verifies that the entry phone matches the destination session phone before writing, so one client's messages cannot be written into another client's JSONL bundle.
8. A final `ClientKey` assertion blocks any edge case where formatting differences still hide a client mismatch.

Client naming is also strict during history import:

- `push_name` from **client-authored** messages can update identity hints.
- `push_name` from `fromMe` messages is **not** allowed to rename the client, because that value is the operator's own WhatsApp display name.
- `push_name` and `label` are treated as hints for search and display, not as trusted direct-chat identity keys.

Imported messages are:
- Deduplicated by WhatsApp `message_id`.
- Sorted chronologically before merge.
- Written into the canonical bundled session JSONL at [sessions/whatsapp__{phone}/session.jsonl](sessions).
- Reflected in [sessions/whatsapp__{phone}/meta.json](sessions) for display metadata.
- Immediately queryable via `GET /api/messages/{phone}`.

The agent can also trigger a manual sync for a single contact from the UI — the "一键同步 WhatsApp 聊天记录" button in `ClientProfile.tsx` calls `POST /api/sync/{phone}`.

Manual sync now follows this exact CDP flow:

1. UI calls `POST /api/sync/{phone}`
2. backend asks the bridge for a **fresh one-shot CDP status**
3. bridge checks:
  - can attach to the CDP endpoint?
  - is there a WhatsApp Web tab?
  - is WhatsApp Web logged in and scrape-ready?
4. if CDP is not reachable, the bridge tries to launch the dedicated Chrome profile automatically
5. if WhatsApp Web still is not logged in, the API returns `409` with a precise state such as `whatsapp_web_login_required`
6. after the operator logs in to WhatsApp Web, they click sync again to retry

### Step 7. UI WebSocket connects — capture-only mode activates

When `NanobotWebSocket.connect()` succeeds, the `useNanobot` hook registers itself as an observer on the `MessageBus` inbound queue.

From this point, `MessageBus.ui_connected` is `True`. Every inbound WhatsApp message that is **not** a self-chat command now receives:

```python
msg.metadata["capture_only"] = True
msg.metadata["_auto_draft_candidate"] = True
```

- `capture_only=True` tells the `AgentLoop` to save the message to session history but **not** generate or send a reply.
- `_auto_draft_candidate=True` signals `ApiServer`'s inbound mirror task to consider auto-draft generation.

The moment the WebSocket disconnects (agent closes the browser), `ui_connected` drops to `False` and the gateway reverts to autonomous auto-reply mode.

### Step 8. A client sends a WhatsApp message

Message flow from client → agent's UI:

```
Client's phone
    │  (WhatsApp message)
    ▼
Baileys bridge  (ws://localhost:3001)
    │  normalizes to InboundMessage{channel="whatsapp", chat_id=..., content=...}
    ▼
WhatsApp channel  (checks allowlist, updates metadata in reply targets JSON)
    │
    ▼
MessageBus.publish_inbound()
    │  capture_only=True → agent loop saves to JSONL, no reply generated
    │  also fans out to all inbound observers
    ▼
ApiServer inbound mirror task
    ├──► broadcasts new_message WS event → message appears in UI thread instantly
    └──► if auto_draft == true for this client → spawns _auto_generate_draft()
```

### Step 9. AI draft generation (automatic or manual)

There are two independent paths that both land a draft in the composer textarea:

#### Path A — Automatic (blue toggle is ON)

Triggered automatically when a client with `auto_draft: true` sends a message and the UI is connected:

1. `ApiServer._auto_generate_draft(phone)` starts asynchronously.
2. Takes a **session snapshot** (saves current JSONL state).
3. Calls `agent.process_direct(message, ...)` — runs the full LLM pipeline including tool calls.
4. **Rolls back the session** to the snapshot — the draft is not committed to history.
5. Sends `{ type: "auto_draft", phone: ..., content: "..." }` via WebSocket to all connected clients.
6. `useNanobot` receives the event and dispatches it to `App.tsx` → sets the composer textarea value for the matching client.

#### Path B — Manual (✨ AI button)

The agent clicks the ✨ button in the MessageInput toolbar at any time:

1. `POST /api/ai-draft/{phone}` is called.
2. Same generate → snapshot → rollback flow.
3. Response `{ draft: "..." }` returned to the frontend.
4. `useAIGeneration` hook loads it into the composer textarea.
5. A `new_message` WebSocket event with `status: "completed"` is also broadcast.

In both cases the draft arrives **in the Nanobot UI composer box as editable text** — not in the WhatsApp Web browser. There is no separate Approve/Discard step; the agent just edits and sends.

### Step 10. Agent reviews, edits, and sends

The composer textarea (`MessageInput.tsx`) is pre-populated with the draft text. The agent can:
- Edit any part of the text.
- Delete it all and write something fresh.
- Send as-is immediately.

**Sending a message:**

- Press **Cmd+Enter** or click **发送**.
- `MessageThread` / `MessageInput` determines if the content came from an AI draft (tracked via a local ref).

| Content type | API call | Backend action |
|---|---|---|
| AI draft (auto or manual) | `POST /api/ai-send/{phone}` | Persists `{ role: "me", is_ai_approved: true }` to JSONL, publishes `OutboundMessage` to the bus |
| Manually typed | `POST /api/messages/{phone}` | Persists `{ role: "me" }` to JSONL, publishes `OutboundMessage` to the bus |

Both paths result in:
1. `ChannelManager` routes the `OutboundMessage` to the WhatsApp channel.
2. WhatsApp channel sends the approved message through **Baileys** via the linked-device protocol.
3. A `new_message` WS event is broadcast → the sent message appears in the UI thread.

### Step 11. Session and history update

After each sent message:
- `sessions/whatsapp__{phone}/session.jsonl` is updated with the new turn.
- `sessions/whatsapp__{phone}/meta.json` is refreshed.
- The next `GET /api/messages/{phone}` and the next AI draft generation will include the new message as conversation context.

---

## Feature Reference

### Auto-draft toggle (blue button, per client)

Each contact card in the left sidebar has a blue toggle. This controls `auto_draft` in `data/whatsapp_reply_targets.json`.

| State | Label | Behavior |
|---|---|---|
| ON (blue) | AI自动草稿已开启 | Next inbound message from this client auto-generates a draft into the composer |
| OFF (gray) | AI自动草稿已关闭 | No automatic draft; agent can still use ✨ AI manually |

The toggle calls `PUT /api/auto-draft/{phone}` → backend updates the JSON file → broadcasts `auto_draft_changed` WS event to sync all open tabs.

### Auto-reply (nanobot native, no UI toggle)

Separately from the UI auto-draft system, the backend has its own auto-reply mechanism used when the UI is **not** connected. This is controlled by `PUT /api/auto-reply/{phone}` which sets a per-client `enabled`/`auto_reply` flag in the reply targets JSON. When enabled and the UI is disconnected, the full agent pipeline runs and sends replies autonomously. This path has no corresponding frontend toggle — it is managed via the API or self-chat commands.

### Voice recording

`ClientProfile.tsx` includes a `VoiceRecorder` component. Recorded audio is transcribed (via the browser `MediaRecorder` API) and the text is inserted into the composer box as a draft starting point.

### Broadcast

`BroadcastModal.tsx` calls `POST /api/broadcast` with a list of phone numbers and a message body. The gateway sends the message to each target sequentially using the same outbound path.

### Manual sync

The "一键同步 WhatsApp 聊天记录" button in `ClientProfile.tsx` calls `triggerSync(phone)` → `POST /api/sync/{phone}`.

Manual sync is a **scoped** history sync for one phone only:

1. the backend normalizes the requested phone through `ClientKey`
2. it limits the scrape target list to that phone's allowlist row
3. the bridge opens only that chat in WhatsApp Web
4. scraped messages are stamped with that target's identifiers
5. Python re-checks the phone/chat match before import
6. only that client's session bundle and per-client prompt context are updated

If the CDP browser cannot open WhatsApp Web or cannot focus the chat search box, the sync returns `not_ready` instead of silently writing to the wrong client.

### Activity journal

The backend keeps a concise append-only activity journal in [state/activity_journal.jsonl](state/activity_journal.jsonl).

It is the authoritative operator activity log and is streamed to the frontend Logs panel.

Typical journal events include:
- login
- inbound message observed
- AI draft generated
- message sent
- broadcast sent
- manual sync triggered
- reply-target updates

This log is backend-owned, not browser-local, so it survives page refreshes and keeps the UI aligned with the real runtime state.

### AI thinking indicator

While a draft is being generated, `ApiServer` sends a sequence of `ai_generating` WebSocket events:
- `{ type: "ai_generating", status: "started", phone: ... }` — triggered at the start
- `{ type: "ai_generating", status: "completed", phone: ... }` — on success
- `{ type: "ai_generating", status: "error", phone: ..., content: "..." }` — on failure

The `AIThinkingLoader` component and `useAIGeneration` hook consume these events to show a spinner in the MessageThread.

---

## Frontend Architecture

```
src/
├── App.tsx                  Root component — auth state, routing, log handler
├── services/
│   ├── api.ts               Typed REST wrappers (fetch-based, relative URLs via Vite proxy)
│   └── websocket.ts         Singleton WebSocket client with auto-reconnect and event emitter
├── hooks/
│   ├── useNanobot.ts        Master hook — fetches clients/messages, connects WS, handles all events
│   ├── useAIGeneration.ts   Tracks ai_generating WS events → loading state in thread
│   ├── useRecording.ts      MediaRecorder wrapper for voice input
│   └── useLogger.ts         Backend journal client for the Logs panel (`/api/journal` + WS journal events)
├── components/
│   ├── Auth/LoginPage.tsx   Login form → POST /api/login → onLogin()
│   ├── ClientList/
│   │   ├── ClientList.tsx   Left sidebar list of contacts
│   │   └── ClientCard.tsx   Single contact row — name, last message, blue auto-draft toggle
│   ├── ClientDetail/
│   │   ├── ClientProfile.tsx  Right panel — client info, sync button, voice recorder
│   │   └── VoiceRecorder.tsx  MediaRecorder → transcription → composer pre-fill
│   ├── MessageCenter/
│   │   ├── MessageThread.tsx  Chat bubble list for selected client
│   │   ├── MessageInput.tsx   Composer textarea + send button + ✨ AI button
│   │   ├── AIThinkingLoader.tsx  Animated spinner shown during AI generation
│   │   └── BroadcastModal.tsx  Multi-client broadcast form
│   ├── Logs/LogViewer.tsx   Developer log panel (toggled from header)
│   └── common/
│       ├── AnimatedEllipsis.tsx  Typing animation
│       └── PrivacyBadge.tsx      PII redaction badge
├── store/mockData.ts        5 demo clients used in offline/demo mode
└── types/
    ├── index.ts             Client, Message, and other shared types
    └── log.ts               LogEntry and LogAction union type
```

### Key data flow in the frontend

```
useNanobot(isAuthenticated)
    ├── GET /api/clients → clients[]
    ├── GET /api/messages/{phone} → messages[] (on client select)
    ├── WS new_message → append to messages[]
    ├── WS auto_draft → set composer value for matching client
    ├── WS auto_draft_changed → update client.autoDraftEnabled
    └── WS ai_generating → forward to useAIGeneration

App.tsx
    ├── handleToggleAutoDraft(phone, enabled) → PUT /api/auto-draft/{phone}
    ├── handleSelectClient(client) → set selectedClient
    └── handleLog(action, ...) → useLogger
```

---

## Data Structures

### Reply targets: `data/whatsapp_reply_targets.json`

The authoritative record of which contacts the gateway watches and how to handle their messages.

```json
{
  "version": 1,
  "updated_at": "2026-03-30T00:49:41.108824+00:00",
  "source": "self_chat_command",
  "direct_reply_targets": [
    {
      "phone": "85268424658",
      "enabled": true,
      "auto_draft": true,
      "label": "",
      "chat_id": "85268424658@s.whatsapp.net",
      "sender_id": "85268424658@s.whatsapp.net",
      "push_name": "+852 6842 4658",
      "last_seen_at": "2026-03-30T00:49:41.108748+00:00"
    }
  ],
  "group_reply_targets": []
}
```

| Field | Type | Description |
|---|---|---|
| `enabled` | bool | Gateway captures and processes messages from this client |
| `auto_draft` | bool | UI automatically generates AI drafts when this client messages |
| `chat_id` | string | WhatsApp JID used by Baileys internally |
| `sender_id` | string | JID of the message sender (same as chat_id for DMs) |
| `push_name` | string | Display name from WhatsApp |
| `last_seen_at` | ISO datetime | Timestamp of last inbound message |

Important note:

- `push_name` is only a WhatsApp display-name hint.
- `phone`, `chat_id`, and `sender_id` are the authoritative routing identifiers.
- If `push_name` is wrong or stale, Nanobot should still route history by phone/chat identifiers, not by the visible name.
- Direct chats do not use name-only fallback for final routing or session ownership.
- Group rows matched by `group_name` alone must still match the member phone before they are accepted.

### Client identity: `nanobot.session.client_key.ClientKey`

WhatsApp client data now flows through one normalized identity type:

```python
from nanobot.session.client_key import ClientKey

key = ClientKey.normalize("+852-6842-4658")
key.phone        # "85268424658"
key.session_key  # "whatsapp:85268424658"
```

What this changes:

- different phone formats now map to the same client deterministically
- similar but different numbers stay isolated
- API lookup, session lookup, prompt assembly, and memory storage all use the same normalized identifier

### Per-client memory: `memory/{phone}/`

Client-specific memory is no longer shared across the whole workspace.

Current layout:

```text
memory/
├── GLOBAL.md
├── 85268424658/
│   ├── MEMORY.md
│   └── HISTORY.md
└── 85295119020/
    ├── MEMORY.md
    └── HISTORY.md
```

Rules:

- `memory/{phone}/MEMORY.md` stores long-term facts for one client only
- `memory/{phone}/HISTORY.md` stores summarized history for that client only
- [memory/GLOBAL.md](memory/GLOBAL.md) is shared operator-curated knowledge and is the only intended shared memory layer
- the legacy top-level `memory/MEMORY.md` is only treated as fallback global knowledge before per-client memory dirs exist

### Session bundles: `sessions/whatsapp__{phone}/`

Each contact now has a bundle directory with:

- `meta.json` — readable summary and identity hints
- `session.jsonl` — authoritative chat history

The JSONL file still uses a metadata header on line 1, followed by one message turn per line.

```jsonl
{"_type":"metadata","key":"whatsapp:85268424658","created_at":"2026-03-15T...","updated_at":"2026-03-30T..."}
{"role":"client","content":"Hi, I'd like to know about dental plans","timestamp":"2026-03-30T10:00:00Z","message_id":"3EB0ABC..."}
{"role":"me","content":"Sure! We have several dental plans...","timestamp":"2026-03-30T10:01:00Z","message_id":"ai_send_1234","is_ai_approved":true}
```

| Field | Values | Description |
|---|---|---|
| `role` | `client` / `me` | `client` = them, `me` = agent. Mapped to `user`/`assistant` at the LLM boundary. |
| `is_ai_approved` | bool | Present and `true` when the message was originally an AI draft that the agent approved and sent |
| `message_id` | string | WhatsApp message ID for deduplication; `ai_send_*` prefix for sent drafts |

### Filters and ownership boundaries

There are now several separate filter layers in the runtime, each with a different job:

| Layer | File(s) | What it filters |
|---|---|---|
| direct contact allowlist | [data/contacts/whatsapp.json](data/contacts/whatsapp.json), [nanobot/channels/whatsapp_contacts.py](nanobot/channels/whatsapp_contacts.py) | which direct senders are locally allowed |
| group member allowlist | [data/whatsapp_groups.csv](data/whatsapp_groups.csv), [nanobot/channels/whatsapp_group_members.py](nanobot/channels/whatsapp_group_members.py) | which group/member combinations are allowed |
| reply-target routing filter | [data/whatsapp_reply_targets.json](data/whatsapp_reply_targets.json), [nanobot/channels/whatsapp_reply_targets.py](nanobot/channels/whatsapp_reply_targets.py) | which chats are tracked, drafted, and auto-replied |
| per-client session ownership | [sessions](sessions), [nanobot/session/client_key.py](nanobot/session/client_key.py), [nanobot/session/manager.py](nanobot/session/manager.py) | which session bundle belongs to which client |
| history import guard | [nanobot/agent/loop.py](nanobot/agent/loop.py) | whether imported history can be written into a session |
| privacy masking filter | [PRIVACY_PIPELINE.md](PRIVACY_PIPELINE.md), [nanobot/privacy](nanobot/privacy) | what text can leave the machine toward a cloud LLM |

These filters are complementary. The allowlists decide whether a chat is in scope; `ClientKey` and session guards decide where data is allowed to land.

---

## API Reference

### REST Endpoints

All endpoints served on `http://localhost:3456`. The Vite proxy exposes them to the frontend at `/api`.

| Method | Path | Body / Params | Description |
|---|---|---|---|
| POST | `/api/login` | `{ username, password }` | Connectivity check — always returns `{ status: "ok", gateway_ready: true }` |
| GET | `/api/clients` | — | List all clients from reply targets |
| GET | `/api/clients/{phone}` | — | Get a single client |
| GET | `/api/messages/{phone}` | — | Get full message history for a client |
| GET | `/api/journal` | `?limit=` | Get recent backend activity journal entries |
| POST | `/api/journal` | `{ action, phone?, clientName?, details? }` | Append a manual journal entry |
| DELETE | `/api/journal` | — | Clear the backend activity journal |
| POST | `/api/messages/{phone}` | `{ content }` | Send a manual (non-AI) message |
| POST | `/api/ai-draft/{phone}` | — | Generate AI draft (snapshot → LLM → rollback); returns `{ draft }` |
| POST | `/api/ai-send/{phone}` | `{ content }` | Persist and send an approved AI draft |
| PUT | `/api/auto-draft/{phone}` | `{ enabled }` | Toggle UI auto-draft for a client |
| PUT | `/api/auto-reply/{phone}` | `{ enabled }` | Toggle backend autonomous auto-reply (no-UI mode) |
| POST | `/api/broadcast` | `{ phones[], content }` | Send one message to multiple clients |
| POST | `/api/sync/{phone}` | — | Trigger CDP WhatsApp Web history scrape for a client |
| POST | `/api/bridge/check` | — | Fire an immediate one-shot CDP scrape-readiness check |
| POST | `/api/bridge/restart` | — | Kill → rebuild → restart the bridge process (15 s timeout) |
| GET | `/api/status` | — | Gateway status, session count, WS client count, browser severity |

### WebSocket Events

Connect to `ws://localhost:3456/ws`. The Vite proxy exposes this at `ws://localhost:5173/ws`.

| Event type | Direction | Key fields | Description |
|---|---|---|---|
| `new_message` | Server → Client | `phone`, `content`, `sender`, `timestamp`, `metadata` | A new inbound or outbound message for a client |
| `journal_entry` | Server → Client | `entry` | New backend journal entry appended |
| `journal_cleared` | Server → Client | — | Backend journal was cleared |
| `ai_generating` | Server → Client | `phone`, `status` (`started`/`completed`/`error`), `content` | AI generation lifecycle notification |
| `ai_draft` | Server → Client | `phone`, `content` | Manual ✨ AI draft completed — load into composer |
| `auto_draft` | Server → Client | `phone`, `content` | Automatic draft (auto-draft toggle) — load into composer |
| `whatsapp_browser_status` | Server → Client | `mode`, `reusable`, `message`, `severity` | Bridge or CDP scrape-readiness change — severity is `warning` (yellow, usually login/retry needed) or `error` (red, bridge restart needed) |
| `auto_draft_changed` | Server → Client | `phone`, `enabled` | Auto-draft toggle state changed — update client card |
| `pong` | Server → Client | — | WebSocket keepalive response; also emitted locally on connect |

The frontend sends `{ type: "ping" }` every 30 seconds as a keepalive. The server responds with `pong`.

---

## Self Control Message Format

Configure reply targets by sending a WhatsApp message **to yourself** (your own number):

```text
#chatbot reply to individuals#
85212345678
85255556666
#chatbot reply to individuals#
```

Optional group block:

```text
#chatbot reply to groups#
Group Name, +85212345678
Another Group, +85299990000
#chatbot reply to groups#
```

Rules:
- One phone number per line in the individuals block.
- Group format: `Group Name, Phone Number` per line.
- Sending a new individuals block **replaces** `direct_reply_targets` entirely and triggers a history re-sync for all new targets.
- Sending a new groups block replaces `group_reply_targets`.

---

## Storage Layout

| Path | Contents |
|---|---|
| `data/whatsapp_reply_targets.json` | Authoritative routing config — which clients get replies, auto-draft settings |
| `data/contacts/whatsapp.json` | Direct-message allowlist / local contact cache |
| `data/whatsapp_groups.csv` | Group/member allowlist |
| `sessions/whatsapp__{phone}/session.jsonl` | Per-client conversation history in JSONL format |
| `sessions/whatsapp__{phone}/meta.json` | Per-client bundle summary and identity hints |
| `memory/{phone}/MEMORY.md` | Per-client long-term memory |
| `memory/{phone}/HISTORY.md` | Per-client summarized history log |
| `memory/GLOBAL.md` | Shared operator-curated knowledge |
| `state/activity_journal.jsonl` | Append-only backend activity journal shown in the frontend Logs panel |
| `state/wechat-toggle.json` | Local toggle/state file used by the project runtime |
| `whatsapp-auth/` | Baileys linked-device credentials (QR scan result) |
| `whatsapp-web/` | Chrome user data profile for WhatsApp Web (CDP browser) |
| `config.json` | Nanobot configuration (LLM keys, WhatsApp settings, tool config) |
| `.cli-history` | CLI prompt history |

For deeper detail on the two most sensitive pipelines, see [ISOLATION.md](ISOLATION.md) and [PRIVACY_PIPELINE.md](PRIVACY_PIPELINE.md).

---

## Installation

### Prerequisites

- Python 3.11+
- Node.js 18+ and `npm`
- Google Chrome (for WhatsApp Web CDP in `draft` mode)

### Backend

```bash
git clone <repo-url>
cd Nanobot-Whatsapp
python3 -m pip install -e .
python3 -m nanobot onboard        # creates config.json
```

Edit `config.json` to add your LLM API key.

### Frontend

```bash
cd "Insurance frontend"
npm install
```

### Docker

The container setup is for the **Nanobot backend/gateway stack**. It does not start the React UI from [Insurance frontend](Insurance%20frontend).

[Dockerfile](Dockerfile) builds a single image that:

- installs Python dependencies
- installs and builds the WhatsApp bridge from [bridge](bridge)
- creates project-local runtime directories under `/app`
- runs `nanobot` as the entrypoint

Build the image:

```bash
docker build -t nanobot .
```

Check the container wiring with the default status command:

```bash
docker run --rm -it nanobot
```

Run the backend gateway directly:

```bash
docker run --rm -it \
  -p 18790:18790 \
  -v "$(pwd)/config.json:/app/config.json" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/sessions:/app/sessions" \
  -v "$(pwd)/state:/app/state" \
  -v "$(pwd)/memory:/app/memory" \
  -v "$(pwd)/whatsapp-auth:/app/whatsapp-auth" \
  -v "$(pwd)/whatsapp-web:/app/whatsapp-web" \
  -v "$(pwd)/whatsapp-web-debug:/app/whatsapp-web-debug" \
  -v "$(pwd)/skills:/app/skills" \
  nanobot gateway
```

Important Docker notes:

- There is **no** `~/.nanobot` bind mount anymore.
- Runtime state is expected to come from this repo's folders and [config.json](config.json).
- The container exposes port `18790` for `nanobot gateway`.
- If you want the React operator UI, run it separately on the host from [Insurance frontend](Insurance%20frontend).

### Docker Compose

[docker-compose.yml](docker-compose.yml) provides two services:

- `nanobot-gateway` — long-running backend service on port `18790`
- `nanobot-cli` — ad-hoc CLI container behind the `cli` profile

Start the gateway service:

```bash
docker compose up --build nanobot-gateway
```

Run a one-off CLI container:

```bash
docker compose run --rm nanobot-cli
```

Compose keeps runtime state project-local by mounting these host paths into `/app`:

| Host path | Container path | Purpose |
|---|---|---|
| [config.json](config.json) | `/app/config.json` | active config |
| [data](data) | `/app/data` | contacts, reply targets, imported data |
| [sessions](sessions) | `/app/sessions` | session bundles |
| [state](state) | `/app/state` | journal and runtime toggles |
| [memory](memory) | `/app/memory` | memory files |
| [whatsapp-auth](whatsapp-auth) | `/app/whatsapp-auth` | Baileys auth |
| [whatsapp-web](whatsapp-web) | `/app/whatsapp-web` | Chrome WhatsApp profile |
| [whatsapp-web-debug](whatsapp-web-debug) | `/app/whatsapp-web-debug` | debug browser state |
| [skills](skills) | `/app/skills` | custom project skills |

### Configuration

`config.json` is project-local by default. Only explicit overrides via `NANOBOT_APP_CONFIG_PATH` or `NANOBOT_CONFIG_PATH` can point elsewhere. In Docker and Compose, [config.json](config.json) is bind-mounted to `/app/config.json`, so the container uses the same project-local configuration and runtime folders as the host checkout.

Example `config.json`:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "workspace": ".",
      "model": "anthropic/claude-opus-4-5",
      "provider": "openrouter"
    }
  },
  "tools": {
    "restrictToWorkspace": true
  },
  "channels": {
    "whatsapp": {
      "enabled": true,
      "deliveryMode": "draft",
      "bridgeUrl": "ws://localhost:3001",
      "webBrowserMode": "cdp",
      "webCdpUrl": "http://127.0.0.1:9222",
      "webCdpChromePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "webProfileDir": "whatsapp-web",
      "contactsFile": "data/contacts/whatsapp.json",
      "replyTargetsFile": "data/whatsapp_reply_targets.json"
    }
  }
}
```

| Config key | Values | Description |
|---|---|---|
| `deliveryMode` | `draft` / `send` | `send` is the normal UI workflow: after the agent clicks send, Baileys sends the approved message. `draft` is a legacy/manual browser-compose mode. |
| `agents.defaults.workspace` | `.` or repo-relative subdir | The agent workspace root. Keep it inside this repo. |
| `tools.restrictToWorkspace` | `true` / `false` | When `true`, shell/tool access is restricted to the configured workspace directory. |
| `webBrowserMode` | `cdp` | Use Chrome DevTools Protocol for history scraping and draft insertion. |
| `webCdpUrl` | `http://127.0.0.1:9222` | Remote debugging endpoint for Chrome. |
| `webCdpChromePath` | fs path | Chrome executable. Used only when launching Chrome from scratch. |
| `webProfileDir` | `whatsapp-web` | Persistent Chrome profile. Keeps WhatsApp Web logged in between restarts. |
| `contactsFile` | `data/contacts/whatsapp.json` | Local direct-contact store. Keep it repo-relative. |
| `replyTargetsFile` | `data/whatsapp_reply_targets.json` | Routing config file path (relative to workspace). |

---

## Quick Start

```bash
# Terminal 1: gateway (WhatsApp + AI + API server)
cd Nanobot-Whatsapp
python3 -m nanobot gateway

# Terminal 2: frontend (React UI)
python3 -m nanobot ui
```

Open `http://localhost:5173`, enter any username and password, and you're live.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No QR code in terminal | Baileys auth is still valid | Expected — `connected` will appear. No action needed. |
| `whatsapp_web_login_required` on sync | WhatsApp Web session is not logged in for scraping | Open the CDP browser, sign in to `web.whatsapp.com`, then click sync again. |
| Message received but no reply | UI is connected → capture-only mode | Expected. AI drafts go to the composer, not direct WhatsApp. |
| Draft not appearing in composer | `auto_draft` is `false` for this client | Turn on the blue toggle in the sidebar, or click ✨ AI manually. |
| `cdp_launch_failed` on sync | Chrome could not be attached/launched for scraping | Verify Chrome exists at `webCdpChromePath`, then retry sync. |
| Frontend shows "演示模式" | Backend is unreachable | Verify `python3 -m nanobot gateway` is running on port 3456. |
| `sessions/` is empty | No history sync has run | Send yourself the `#chatbot reply to individuals#` message, or trigger manual sync from the UI. |
| AI draft is slow | LLM API latency or long conversation | Expected for long contexts. Progress is indicated by the `ai_generating` thinking spinner. |

---

## Summary

```
python3 -m nanobot gateway   ←  WhatsApp + LLM + API, port 3456
python3 -m nanobot ui        ←  UI at port 5173, proxies to 3456

Login → connects WS → MessageBus enters capture-only mode
Client texts WhatsApp → Baileys → MessageBus → ApiServer
  → new_message WS event (UI thread updates instantly)
  → if auto_draft ON → agent.process_direct() + snapshot rollback
  → auto_draft WS event → composer textarea fills with AI text
Agent edits → Cmd+Enter
  → POST /api/ai-send → JSONL persisted + OutboundMessage → WhatsApp
  → new_message WS event (sent bubble appears in UI)
```

The core invariant: **nothing is sent to WhatsApp without the agent pressing the send button.** The AI generates and suggests; the human approves and sends.
