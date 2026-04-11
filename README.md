# Nanobot WhatsApp

Nanobot is currently a project-local WhatsApp operator system with:

- a React/Vite operator UI in `Insurance frontend/`
- an aiohttp launcher/API server on port `3456`
- a full Python gateway that starts inside the launcher process after UI login
- a local Node.js WhatsApp bridge on `ws://127.0.0.1:3001`
- Baileys auth state in `whatsapp-auth/`
- a Chrome/Chromium CDP profile in `whatsapp-web/`
- per-client session bundles in `sessions/`

This README describes the code that exists today, not the intended architecture from older revisions.

## Install And Setup

### Prerequisites

- Python `>= 3.11`
- Node.js `>= 20`
- `npm`
- Chrome or Chromium available if you use CDP mode or history scraping

### Recommended Local Runtime

Use a project-local virtualenv in this checkout. Do not rely on random system Python installs.

### 1. Clone The Repo

```bash
git clone <your-repo-url> Nanobot-Whatsapp
cd Nanobot-Whatsapp
```

### 2. Run The Bootstrap Command

```bash
./bootstrap
```

This command:

- creates or reuses the repo-local `.venv`
- upgrades `pip` inside that `.venv`
- installs backend dependencies with `pip install -e .`
- installs frontend dependencies in `Insurance frontend/`
- installs the wrapper commands with `python -m nanobot install-ui-command`
- leaves config setup as a separate next step

The bootstrap flow is intended for macOS/Linux with a project-local `.venv`.
After `./bootstrap`, the wrapper commands are already installed for this checkout.

### 3. Activate The Project Venv

```bash
source .venv/bin/activate
```

### 4. Put The Google Credential File In Place If You Need STT

If you plan to use Google STT, put the real service-account JSON inside this checkout before running setup:

```bash
mkdir -p secrets
# place your real credential file at:
#   secrets/double-scholar-487115-b1-075776a1689b.json
```

If you do not need Google STT right now, skip this step.

### 5. Run The Guided Setup

The primary onboarding path is now a single guided command:

```bash
python -m nanobot setup
```

The setup flow:

- pre-fills the normal local defaults
- asks for the small set of values Nanobot cannot guess for you
- optionally asks for Supabase catalog settings
- optionally asks for Google STT settings
- validates obvious mistakes before writing files
- writes split config files automatically
- prints the next commands to run

For a normal local install, you usually just:

- press Enter to keep `3456`
- press Enter to keep the default LiteLLM model
- press Enter to keep the default LiteLLM base URL
- paste your LiteLLM proxy API key
- optionally enable Supabase and paste its backend-capable key
- optionally enable Google STT after placing `secrets/double-scholar-487115-b1-075776a1689b.json`

The guided setup does not install Python, Node, Chrome, Supabase resources, or Google credentials for you. It only validates inputs and writes Nanobot's local config files.

### 6. Sanity Check The Local Install

```bash
python -m nanobot status
```

Use this to confirm the config, workspace, sessions, memory, auth, and browser paths all resolve inside the repo.

### 7. Launch The App With The Wrapper

```bash
whatsapp-web-nanobot-ui
```

You do not need to rerun `python -m nanobot install-ui-command` here if you already used `./bootstrap`.

### 8. Stop The Local UI And Backend Processes

```bash
python -m nanobot stop-dev
```

## Current Reality

These are the most important implementation facts right now:

- The shipped config flow is LiteLLM-first and enables WhatsApp with `channels.whatsapp.deliveryMode` set to `"draft"` by default.
- The active UI/API port is `3456`, and the packaged Docker/compose assets now follow that same backend port.
- The UI login page is not real credential enforcement. `POST /api/login` uses the username for journaling and ignores the password.
- The frontend does not persist chat history in browser storage. The rendered transcript always comes from backend session JSONL files.
- With the UI connected, inbound WhatsApp DMs are forced into capture-only mode so the system saves them and optionally generates drafts, but does not auto-send replies.
- In backend-only mode, normal agent auto-reply behavior is still possible because the UI capture-only observer is absent.

## Architecture In Use Today

### Processes

1. `python -m nanobot ui` or `whatsapp-web-nanobot-ui`
   Starts the lightweight launcher on `3456` if it is not already running, then starts the Vite frontend.
2. `python -m nanobot launcher`
   Starts only the lightweight pre-login API/WS server on `3456`.
3. `POST /api/login`
   Causes the launcher to start the full gateway in-process.
4. `python -m nanobot gateway` or `whatsapp-web-nanobot-gateway`
   Starts the full gateway directly without the launcher-first UI flow.
5. Node bridge
   Started by the Python CLI when WhatsApp is enabled. It runs from `.bridge-build/` and listens on `ws://127.0.0.1:3001` by default.
6. Optional privacy gateway
   Started automatically on `127.0.0.1:8787` when the active model/provider resolves to the configured LiteLLM endpoint and `privacy_gateway.enabled` is true.

### Components and Roles

| Component | Used for | Runs when |
| --- | --- | --- |
| React frontend | Operator dashboard | `nanobot ui` |
| Launcher server | Pre-login `/api/status`, `/api/login`, `/ws` | `nanobot ui` or `nanobot launcher` |
| ApiServer | Real REST and WebSocket API | after gateway start |
| AgentLoop | LLM calls, tools, history import, memory consolidation | after gateway start |
| ChannelManager | Starts WhatsApp and any other enabled channels | after gateway start |
| WhatsAppChannel | Python-side WhatsApp routing, sync orchestration, status caching | after gateway start |
| Node bridge | Baileys transport plus Playwright/CDP helper actions | after gateway start |
| DraftComposer | launch-mode WhatsApp Web draft placement only; disabled in `cdp` mode | inside bridge |
| HistoryParser | parse-only CDP history scraping with session reuse, one fresh-window retry, and first-result chat entry | inside bridge |

### Frontend Routes Actually Used

The current frontend uses these routes:

- `GET /api/status`
- `POST /api/login`
- `GET /api/clients`
- `GET /api/clients/{phone}`
- `GET /api/clients/{phone}/offline-meeting-notes`
- `GET /api/clients/{phone}/offline-meeting-notes/{noteId}`
- `POST /api/clients/{phone}/offline-meeting-note/save`
- `POST /api/clients/{phone}/offline-meeting-note/transcribe`
- `DELETE /api/clients/{phone}`
- `GET /api/messages/{phone}?format=html`
- `POST /api/messages/{phone}`
- `POST /api/ai-draft/{phone}`
- `POST /api/ai-send/{phone}`
- `PUT /api/auto-draft/{phone}`
- `POST /api/reply-targets`
- `POST /api/broadcast`
- `POST /api/sync/{phone}`
- `POST /api/bridge/restart`
- `GET /api/journal`
- `POST /api/journal`
- `DELETE /api/journal`
- `GET /ws`

Routes that exist but are not used by the current frontend:

- `PUT /api/auto-reply/{phone}`

## Source Of Truth And Storage

The system is intentionally project-local. Runtime state lives under this repository unless you explicitly override `NANOBOT_CONFIG_PATH`.

| Path | What it stores | Source-of-truth status |
| --- | --- | --- |
| `config.json` | core runtime configuration | canonical app config |
| `googleconfig.json` | Google STT feature settings only | canonical Google STT config |
| `supabaseconfig.json` | Supabase catalog settings only | canonical catalog config |
| `secrets/double-scholar-487115-b1-075776a1689b.json` | Google service-account credential loaded at runtime from disk | canonical Google credential path |
| `data/whatsapp_reply_targets.json` | direct/group reply targets, auto-draft flags, observed IDs, and migration markers | canonical operator target registry and inbound routing registry, but project-local and not intended for git |
| `sessions/whatsapp__{phone}/session.jsonl` | append-only persisted conversation history and saved `offline_meeting_note` records | canonical chat and note history |
| `sessions/whatsapp__{phone}/meta.json` | derived session metadata, pointers, and offline-meeting note index entries | derived from `session.jsonl` |
| `memory/{phone}/MEMORY.md` | per-client long-term memory | canonical per-client memory |
| `memory/{phone}/HISTORY.md` | per-client consolidated history log | canonical per-client memory log |
| `memory/GLOBAL.md` | shared operator-curated knowledge | canonical shared knowledge file |
| `whatsapp-auth/` | Baileys multi-file auth state | canonical bridge auth state |
| `whatsapp-web/` | Chrome/Chromium profile for CDP/WhatsApp Web | canonical web automation profile |
| `state/activity_journal.jsonl` | backend and UI activity journal | canonical journal |
| `cron/jobs.json` | scheduled jobs | canonical cron store |
| `media/` | downloaded WhatsApp media files | runtime artifact store |
| `.bridge-build/` | copied/built bridge bundle | disposable build cache |
| `test_words/` | local prompt/privacy debug artifacts | debug output, not business source of truth |

Important source-of-truth rules:

- The operator UI client list is driven by `data/whatsapp_reply_targets.json`, not by scanning every persisted session.
- The operator transcript view is rendered from `sessions/.../session.jsonl`, not from WebSocket payloads or frontend cache.
- `sessions/.../meta.json` is a convenience index. If it disagrees with `session.jsonl`, `session.jsonl` wins.
- AI drafts are not persisted when they are only drafts. Only approved/sent content is written to session history.
- Offline meeting voice notes do not persist audio. Draft transcription persists nothing; only user-saved transcript text is appended as `offline_meeting_note` rows in the matching client's `session.jsonl`, while `meta.json` keeps only a lightweight `offline_meeting_note_index` with `note_id` and `created_at`.

## Advanced Config Files

The setup command keeps the internal split-config architecture but hides that complexity during onboarding.

| File | What it stores | When to edit manually |
| --- | --- | --- |
| `config.json` | core runtime config such as the LiteLLM model, LiteLLM base URL, and WhatsApp runtime settings | edit when you want to change the main model or other core runtime behavior |
| `supabaseconfig.json` | optional Supabase catalog settings only | edit when catalog URL, keys, tables, or restore behavior change |
| `googleconfig.json` | optional Google STT settings only | edit when project ID, location, language, or credential path changes |

Important notes:

- `python -m nanobot setup` writes these files separately on purpose. It does not collapse them back into one file.
- The primary onboarding path is `./bootstrap` followed by `python -m nanobot setup`. The example files are references, not the recommended first-run editing path.
- Google credentials stay outside the config payload itself. `googleconfig.json` stores only `credentialJsonPath`, and that path should point to a project-local JSON file under `secrets/`.
- Despite the field name `supabaseAnonKey`, Nanobot normally needs a backend-capable Supabase key here, typically a `service_role` key, not a publishable browser `anon` key.
- Supabase remains backward compatible with legacy `config.json -> catalog`, but the preferred productized layout is the separate `supabaseconfig.json`.
- Manual edits are still supported. If you rerun `python -m nanobot setup`, it will detect existing files and offer update, overwrite, skip, keep, or remove behavior instead of overwriting silently.

## Manual Dependency Install

If you do not want the bootstrap command, the equivalent manual install flow is:

```bash
git clone <your-repo-url> Nanobot-Whatsapp
cd Nanobot-Whatsapp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cd "Insurance frontend"
npm ci
cd ..
python -m nanobot install-ui-command
```

Use `npm install` instead of `npm ci` only when `Insurance frontend/package-lock.json` is absent.
Use `pip install -e ".[dev]"` only if you specifically need the repo's test and lint tooling.

The wrapper installer creates:

- `whatsapp-web-nanobot-ui`
- `whatsapp-web-nanobot-gateway`

Those wrapper scripts point back to this checkout, export `NANOBOT_CONFIG_PATH` for this repo's `config.json`, and prefer this repo's `.venv/bin/python`.
If the repo moves on disk, rerun `python -m nanobot install-ui-command` so the wrapper paths are refreshed.

### Chirp Transcript Meeting Notes

This system is the real implementation behind the existing `线下会面录音` entry in the client profile.

- Google Cloud Speech-to-Text V2 is used in one-shot mode with model `chirp_3`.
- Audio is recorded in the browser, uploaded once, processed in memory on the backend, and discarded after transcription.
- `POST /api/clients/{phone}/offline-meeting-note/transcribe` returns draft transcript text only and persists nothing.
- The operator may edit that draft in the UI; only `保存` writes anything locally.
- `POST /api/clients/{phone}/offline-meeting-note/save` stores the final confirmed transcript and returns the saved note body for immediate UI update.
- Canonical saved-note storage is append-only `offline_meeting_note` rows in `sessions/whatsapp__{phone}/session.jsonl`.
- `meta.json` is only a lightweight note index for browsing. It stores `offline_meeting_note_index` entries with `note_id` and `created_at`, not transcript content.
- `GET /api/clients/{phone}/offline-meeting-notes` reads the chronological note index from `meta.json`.
- `GET /api/clients/{phone}/offline-meeting-notes/{noteId}` resolves the selected note by scanning that client's canonical JSONL note rows and returns the actual transcript text.
- Nanobot context uses saved note transcripts from canonical JSONL note rows only. Unsaved drafts never enter context.

## Real End-To-End Operator Flow

### 1. Start The UI

Recommended daily command:

```bash
whatsapp-web-nanobot-ui
```

Stop the local UI/launcher/bridge dev processes:

```bash
python3 -m nanobot stop-dev
```

What this actually does:

1. Resolves the frontend directory at `Insurance frontend/`.
2. Checks whether something answering like Nanobot is already listening on `http://127.0.0.1:3456/api/status`.
3. If not, starts `python -m nanobot launcher --api-port 3456` in the background.
4. Runs `npm run dev` in the frontend directory.

At this point only the launcher is up. The full gateway is not running yet.

### 2. Open The UI And Log In

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

The login form calls `POST /api/login`.

Current behavior:

- the username is written to the activity journal as the login actor
- the password is ignored
- the launcher immediately starts the full gateway in the same Python process

So this login screen is a local startup gate, not real authentication.

### 3. Gateway Boot Sequence After Login

After `POST /api/login`, the launcher starts the full runtime in this order:

1. start the WhatsApp bridge
2. start the local privacy gateway if the active provider path needs it
3. create `MessageBus`, `SessionManager`, `CronService`, `AgentLoop`, `ChannelManager`, and `HeartbeatService`
4. create an `ApiServer` object, but keep using the existing launcher HTTP server on `3456`
5. start background mirror/observer tasks for outbound messages, inbound capture, persisted history, auth status, and bridge health
6. start the agent loop and channel manager
7. mark the gateway ready
8. kick off one background direct WhatsApp history parse for enabled direct reply targets

The WebSocket stays on `/ws` the whole time. Before readiness it is served by the launcher. After readiness the launcher proxies real API routes to `ApiServer`.

### 4. WhatsApp Login Is Split Across Two Surfaces

WhatsApp uses two separate states:

- `whatsapp-auth/` for Baileys transport login
- `whatsapp-web/` for parse-only CDP history scraping

The UI exposes the Baileys QR flow directly. WhatsApp Web login is separate, uses the `whatsapp-web/` browser profile, and affects only history parsing. During parsing, the bridge reuses an existing usable CDP session when possible and opens one fresh CDP window only if parsing cannot proceed.

Current CDP behavior is intentionally narrow:

- CDP mode is reserved for history parsing. WhatsApp Web draft placement is disabled in `cdp` mode.
- Both manual sync and login-time bulk parse use the same direct parse routine.
- History parsing reuses a logged-in usable WhatsApp Web tab when possible.
- If the attached CDP page is poisoned or unusable, parsing opens one fresh CDP window and retries once.
- True `chat_not_found` results do not trigger a fresh-window retry.
- The parse entry flow is: acquire a usable WhatsApp page, focus search input, clear old search state, type the normalized phone once, confirm the input value, wait 3 seconds for search to settle, collect visible real result rows from `#pane-side div[role="gridcell"][tabindex="0"]`, click the first/top real row, verify the main chat is open, then scrape history.
- History parsing requires a normalized phone. `searchTerms` can still be carried in payloads for metadata or launch-mode draft use, but the parse routine does not use them to choose a row.

### 5. Inbound Direct Message Flow

This is the current DM path when the UI is connected:

1. The bridge receives a WhatsApp event through Baileys.
2. `WhatsAppChannel` normalizes the sender and checks allowlists.
   Direct DMs use enabled `direct_reply_targets` in `data/whatsapp_reply_targets.json`, with `allowFrom` only as an explicit fallback.
   Group messages use `group_reply_targets` in `data/whatsapp_reply_targets.json`.
3. For direct DMs, the channel publishes an `InboundMessage` to the bus.
4. Because the UI is connected, `MessageBus.publish_inbound()` forces that WhatsApp DM into:
   - `capture_only = true`
   - `_auto_draft_candidate = true`
5. `AgentLoop._process_message()` writes the inbound message to the correct `sessions/whatsapp__{phone}/session.jsonl` bundle and does not send a reply.
6. `ApiServer._mirror_inbound()` appends a journal entry and, if the client's reply-target row has `auto_draft = true`, starts background AI draft generation.
7. The frontend receives `new_message`, refreshes the client list, and reloads the transcript iframe.

Important consequences:

- UI-connected mode is save-and-draft mode, not auto-send mode.
- A DM is accepted when the sender is an enabled direct reply target, or when the sender is explicitly listed in `allowFrom`.
- The UI client list only shows phones present in `data/whatsapp_reply_targets.json`, so non-target sessions can exist on disk without appearing in the sidebar.

### 6. Session JSONL Persistence

WhatsApp direct sessions use one canonical bundle per client:

- `sessions/whatsapp__{phone}/session.jsonl`
- `sessions/whatsapp__{phone}/meta.json`

`session.jsonl` format:

- first line: metadata record (`_type = "metadata"`)
- following lines: append-only message records and saved `offline_meeting_note` records

Persistence behavior:

- inbound capture-only messages are persisted immediately
- deleted-message events mark existing records as deleted without removing them
- manual human sends are persisted before outbound delivery
- approved AI sends are persisted before outbound delivery
- unsent AI drafts are not persisted
- history sync imports both client-side and from-me messages without calling the LLM
- imported inbound `你` reply-with-quote history blocks may be normalized into `message_type = imported_client_reply_with_quote`; `content` stays the actual reply text and `quoted_text` / `quoted_message_id` may also be stored

The frontend transcript is always rebuilt from persisted JSONL. It does not trust unsaved in-memory session cache.

### 7. History Loading In The UI

The current frontend does not render the message list from JSON API data.

Instead:

1. `MessageThread.tsx` loads an iframe.
2. The iframe URL is `GET /api/messages/{phone}?format=html`.
3. `ApiServer` reads persisted session history from disk.
4. `ApiServer` renders a standalone HTML transcript document.

There is no `localStorage`, `sessionStorage`, or IndexedDB history cache in the frontend.

### 8. AI Draft Generation

There are two current draft paths.

Automatic draft path:

1. inbound DM is captured
2. `ApiServer._mirror_inbound()` sees `_auto_draft_candidate`
3. if the direct reply target has `auto_draft = true` and a UI client is connected, the API server calls `agent.process_direct(..., persist_history=False)`
4. the generated draft is sent to the UI over WebSocket as `auto_draft`
5. the draft is placed in the operator composer
6. the draft is not written to session history yet

Manual draft path:

1. operator clicks the AI action in the UI
2. frontend calls `POST /api/ai-draft/{phone}`
3. `ApiServer` finds the latest persisted client message in that session
4. it calls `agent.process_direct(..., persist_history=False)`
5. progress is streamed as `ai_progress`
6. final draft is returned in the HTTP response and also broadcast as `ai_draft`

Both paths are draft-only until the operator explicitly sends.

### 9. Send Flow

Manual send:

1. frontend calls `POST /api/messages/{phone}`
2. `ApiServer` appends a `"me"` message to the session JSONL
3. `ApiServer` publishes an outbound WhatsApp bus event
4. `ChannelManager` dispatches it to `WhatsAppChannel.send()`
5. `WhatsAppChannel` builds the bridge payload

Approved AI send:

1. frontend calls `POST /api/ai-send/{phone}` with the final text
2. `ApiServer` persists the approved message first, with `is_ai_approved = true`
3. `ApiServer` publishes the outbound bus event
4. the same WhatsApp send path runs

Because the shipped config flow currently uses `deliveryMode: "draft"` by default:

- outbound bridge commands are `{"type":"prepare_draft", ...}`
- the bridge writes the draft into WhatsApp instead of sending immediately
- the operator remains in the approval loop before anything is sent

If you change the config to `deliveryMode: "draft"`, the same outbound bus events become `prepare_draft` bridge commands and the bridge writes text into the WhatsApp Web compose box instead of sending immediately.

### 10. Sync Flow

There are two real sync paths today.

Automatic login-time sync:

- after the gateway becomes ready, the launcher starts one background direct history parse
- it only targets enabled direct reply targets from `data/whatsapp_reply_targets.json` that have normalized phones
- it uses the same phone-first CDP parse routine as manual sync
- it does not block UI readiness

Manual per-client sync:

1. operator clicks sync in the client profile
2. frontend calls `POST /api/sync/{phone}`
3. `ApiServer` calls `WhatsAppChannel.sync_direct_history([phone])`
4. the channel replays matching cached history, then sends `scrape_direct_history` to the bridge
5. `HistoryParser` reuses an existing usable CDP session or opens one fresh CDP window and retries once
6. the unified direct parse routine clears search, types the normalized phone once, waits for settle, clicks the first real visible row, verifies chat open, then scrapes history
7. scraped history is published back through the bridge and imported into `session.jsonl`
8. backend success remains JSONL-confirmed truth: the requested phone only succeeds when its `sessions/whatsapp__{phone}/session.jsonl` contains the intended messages for that sync attempt
9. after backend success, the frontend follows the already-chosen transcript refresh/load semantics; that policy is not defined by the CDP layer

The current direct CDP parse entry sequence is:

1. locate the exact WhatsApp sidebar search input
2. clear and verify the old search state is empty
3. type the target phone number once
4. verify the input contains that exact query
5. wait 3 seconds for the WhatsApp search view to settle
6. collect visible real rows under `#pane-side div[role="gridcell"][tabindex="0"]`
7. click the first/top real row only
8. verify the open chat from the main chat area, not the left app header
9. scrape history from the opened chat

Important sync constraints:

- sync works only for phones that already exist as enabled direct reply targets
- parse requests without a normalized phone are dropped before CDP parse begins
- sync depends on the bridge being reachable
- sync depends on the WhatsApp Web browser session being logged in and ready
- sync imports history silently and does not invoke the LLM
- sync updates `data/whatsapp_reply_targets.json` with observed `chat_id`, `sender_id`, `push_name`, and `last_seen_at` for existing target rows
- sync retry is narrow: `session_unusable` can trigger one fresh CDP window retry, but `chat_not_found` remains a final non-retriable parse result

### 11. Health And Status Behavior

Before login, `GET /api/status` returns launcher state:

- `status: "launcher"`
- `gateway_ready`
- `gateway_starting`
- `gateway_error`

After the gateway is ready, `GET /api/status` returns:

- `status: "running"`
- session count
- direct/group target counts
- connected WebSocket client count
- enabled channel names
- bridge error state
- Baileys auth-required state and QR payload

WebSocket behavior:

- `/ws` is available before and after login
- pre-login it mainly handles keepalive and gateway progress
- post-login it also carries `new_message`, `ai_generating`, `ai_draft`, `auto_draft`, `journal_entry`, `whatsapp_bridge_status`, and `whatsapp_auth_status`

Bridge health behavior:

- `ApiServer` polls the bridge subprocess every 5 seconds
- if it exits, the frontend gets a bridge error notice
- `POST /api/bridge/restart` deletes `.bridge-build/`, rebuilds the bridge, restarts it, and broadcasts the new state

## On-Demand Vs Automatic

| Behavior | Automatic | On-demand |
| --- | --- | --- |
| launcher startup from `nanobot ui` | yes | no |
| gateway startup after UI login | no | yes, via `POST /api/login` |
| bridge startup | yes, during gateway boot | no |
| CDP browser/session reuse for history parsing | no | yes, during login bulk parse or manual per-client sync |
| fresh CDP browser window launch in `cdp` mode | no | yes, only when parsing cannot proceed with the existing session |
| Baileys QR login | no | yes, when auth is missing/expired |
| WhatsApp Web browser login | no | yes, manual in the CDP browser profile |
| login-time bulk history parse | yes | no |
| manual per-client sync | no | yes |
| inbound message persistence | yes | no |
| auto-draft generation for enabled targets with UI connected | yes | no |
| manual AI draft | no | yes |
| final send | no | yes |
| journal writes | yes | no |
| per-client memory consolidation | yes, after the session crosses the memory window | no |
| bridge rebuild/restart | no | yes |

## Backend-Only Flow

Use this when you want the full runtime without the React UI:

```bash
whatsapp-web-nanobot-gateway
```

Equivalent:

```bash
python3 -m nanobot gateway
```

Current behavior differences versus UI mode:

- the full gateway starts immediately
- there is no launcher-first login gate
- there is no frontend WebSocket observer
- inbound WhatsApp DMs are not forcibly converted to capture-only just because a UI is connected
- normal agent auto-reply behavior can therefore happen in this mode, subject to the usual allowlist and target rules

## Current Developer Notes

### Reply Targets Drive The Operator UI

`GET /api/clients` is built from `data/whatsapp_reply_targets.json`, not from every session bundle on disk.

That means:

- adding a reply target in the UI immediately makes a client visible
- self-chat control messages can also rewrite the target file
- a persisted session without a reply-target row is real data, but it will not appear in the current sidebar

### Per-Client Isolation

WhatsApp client identity is normalized through `ClientKey`.

Effects:

- per-client memory lives under `memory/{phone}/`
- per-client session bundles live under `sessions/whatsapp__{phone}/`
- history imports reject mismatched or missing client phones
- direct-target matching cross-checks phone and chat identifiers to avoid cross-client leakage

### Docker And Compose

Project-local `.venv` plus `pip install -e .` remains the primary runtime.
Docker support is optional packaging on top of that same runtime contract.

Current packaged behavior:

- `nanobot gateway` still exposes the UI/API on `3456`
- the backend container exposes and publishes `3456`
- the frontend container serves Nginx on container port `80` and is published on host `8080`
- the frontend proxies same-origin `/api` and `/ws` to `nanobot-gateway:3456`
- compose bind-mounts project-local `config.json`, `googleconfig.json`, `supabaseconfig.json`, `secrets/`, and runtime state directories into `/app`

This keeps the containerized package aligned with the live local runtime instead of inventing a separate port model.

### `whatsapp-web-debug/`

`whatsapp-web-debug/` still exists in the repo layout and Docker mounts, but the active runtime code paths do not currently use it.

## Quick Command Reference

| Command | What it really does today |
| --- | --- |
| `./bootstrap` | creates or reuses `.venv`, installs backend and frontend dependencies, and installs wrapper commands |
| `python3 -m nanobot setup` | guided onboarding that writes `config.json`, optional `supabaseconfig.json`, and optional `googleconfig.json` |
| `python3 -m nanobot install-ui-command` | installs per-checkout wrapper scripts |
| `whatsapp-web-nanobot-ui` | starts launcher if needed, then runs Vite |
| `whatsapp-web-nanobot-gateway` | starts the full backend immediately |
| `python3 -m nanobot ui` | non-wrapper form of the UI start |
| `python3 -m nanobot stop-dev` | stops local dev processes on ports `5173`, `5174`, `3456`, and `3001` |
| `python3 -m nanobot launcher` | pre-login launcher only |
| `python3 -m nanobot gateway` | full backend only |
| `python3 -m nanobot status` | prints current path and provider summary |
| `python3 -m nanobot channels login` | runs the bridge in the foreground for QR linking |
| `python3 -m nanobot channels whatsapp-web` | prints that standalone CDP launch is disabled because parsing manages CDP lazily |

## Short Usage Summary

For normal operator use, the real flow is:

1. run `whatsapp-web-nanobot-ui`
2. open the Vite URL
3. log in through the local launcher screen
4. wait for the gateway to start
5. if needed, scan the Baileys QR shown by the UI
6. if sync is needed, make sure the WhatsApp Web browser profile is also logged in
7. work from the UI, where inbound messages are persisted first, drafts are generated on demand or automatically, and only approved sends are written and delivered

For developers, the most important mental model is:

- `data/whatsapp_reply_targets.json` is the operator target registry
- `sessions/.../session.jsonl` is the canonical transcript store
- `memory/{phone}/` is the per-client memory store
- WebSocket events are notifications, not source of truth
- the launcher and full gateway share the same `3456` HTTP surface
