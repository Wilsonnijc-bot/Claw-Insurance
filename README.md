# Nanobot WhatsApp Gateway

This repository is a WhatsApp-focused Nanobot workspace.

Its primary job is:

1. receive WhatsApp messages through Baileys,
2. keep local chat history and routing state on disk,
3. decide which direct chats are allowed to auto-reply,
4. generate a reply with Nanobot,
5. paste the final reply into the WhatsApp Web message box without sending it.

The default outbound mode is `draft`, not `send`.

That means the system prepares the reply in WhatsApp Web for manual review. It does not press Enter.

## What The System Is Made Of

There are two separate WhatsApp-facing layers:

- `Baileys bridge`
  - Receives inbound WhatsApp events.
  - Uses the linked-device auth stored in `~/.nanobot/whatsapp-auth` by default.
  - If the auth session is missing or expired, WhatsApp asks for a QR scan in the bridge terminal.

- `CDP WhatsApp Web browser`
  - Used for two browser tasks:
    - scraping direct-chat history from WhatsApp Web,
    - putting the final reply into the WhatsApp Web compose box.
  - Uses a Chrome remote-debugging endpoint such as `http://127.0.0.1:9222`.
  - Uses a persistent Chrome user-data directory, `~/.nanobot/whatsapp-web` by default.

These two layers are independent.

Baileys being connected does not automatically mean WhatsApp Web is logged in.

## Current Default Behavior

- WhatsApp delivery mode defaults to `draft`.
- Web browser mode defaults to `cdp`.
- Direct auto-reply targets are stored in `data/whatsapp_reply_targets.json`.
- Startup history sync runs for all enabled `direct_reply_targets`, even if you have not sent a new self control message in this session.
- A new self control message rewrites the reply-target JSON and immediately re-syncs history for the direct targets listed in that message.
- In `draft` mode:
  - direct chats can generate draft replies,
  - group chats are ignored,
  - the final reply is inserted into the WhatsApp Web message box and left unsent.

## Installation

### Prerequisites

- Python 3.11 or newer
- Node.js and `npm`
- Google Chrome or another Chromium-family browser

### Install from this repository

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
python3 -m pip install -e .
```

The editable install is important because it gives you global launcher commands that work from any directory.

## Main Commands

There are two operational commands, plus one simple alias:

### 1. Gateway

Recommended launcher:

```bash
whatsapp-web-nanobot-gateway
```

Canonical equivalent:

```bash
nanobot gateway
```

Both commands start the same gateway path.

### 2. Browser-only CDP launcher

```bash
nanobot channels whatsapp-web
```

This launches or reuses the debuggable Chrome browser used for WhatsApp Web.

Use it when you want to bring up the browser explicitly before starting the gateway.

## Minimal Config

Nanobot reads config from `~/.nanobot/config.json`.

A minimal WhatsApp-focused example looks like this:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "provider": "openrouter"
    }
  },
  "channels": {
    "whatsapp": {
      "enabled": true,
      "deliveryMode": "draft",
      "bridgeUrl": "ws://localhost:3001",
      "webBrowserMode": "cdp",
      "webCdpUrl": "http://127.0.0.1:9222",
      "webCdpChromePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "webProfileDir": "~/.nanobot/whatsapp-web",
      "contactsFile": "~/.nanobot/contacts/whatsapp.json",
      "replyTargetsFile": "data/whatsapp_reply_targets.json"
    }
  }
}
```

Important fields:

- `deliveryMode`
  - `draft`: paste the final reply into WhatsApp Web and do not send.
  - `send`: use the legacy Baileys auto-send path.

- `webBrowserMode`
  - `cdp`: default. Use a Chrome debugger endpoint and persistent browser profile.
  - `launch`: retained legacy mode. Not the default.

- `webCdpUrl`
  - The Chrome debugger endpoint Nanobot probes and attaches to.

- `webCdpChromePath`
  - Optional explicit path to Chrome.
  - Recommended on macOS if Chrome is not in a standard location.

- `replyTargetsFile`
  - The authoritative JSON file for direct and group reply targets.

## Startup Workflow

This is the exact high-level workflow the current code follows.

### Step 1. Start the gateway

You run one of:

```bash
whatsapp-web-nanobot-gateway
```

or:

```bash
nanobot gateway
```

### Step 2. Gateway checks the Baileys auth session

When the bridge starts, it checks `~/.nanobot/whatsapp-auth/creds.json`.

- If the auth session exists and is valid:
  - the bridge reuses it,
  - no QR appears.

- If the auth session is missing, expired, or logged out:
  - the bridge resets the stale Baileys auth state,
  - the bridge prints a fresh QR code,
  - you scan it from WhatsApp Linked Devices.

This auth check is for Baileys only.

### Step 3. Gateway rigorously checks whether a CDP WhatsApp Web browser exists

When `deliveryMode` is `draft` and `webBrowserMode` is `cdp`, the gateway probes the configured CDP endpoint before the bridge starts.

It checks the endpoint using Chrome DevTools Protocol discovery, not just a blind assumption.

- If a CDP browser already exists at `webCdpUrl`:
  - Nanobot reuses that browser.
  - If a `web.whatsapp.com` tab already exists there, the bridge uses it directly.
  - If the browser exists but no WhatsApp tab exists yet, the bridge opens a new WhatsApp Web tab in that same browser.

- If the CDP endpoint does not exist:
  - Nanobot launches a new Chrome instance itself with remote debugging enabled,
  - using the configured `webProfileDir`,
  - and opens `https://web.whatsapp.com/`.

The launch command is effectively built from:

```bash
<chrome> \
  --remote-debugging-port=<port> \
  --remote-debugging-address=<host> \
  --user-data-dir=<webProfileDir> \
  --no-first-run \
  --no-default-browser-check \
  --new-window \
  https://web.whatsapp.com/
```

If the browser was launched but WhatsApp Web is not logged in yet, the first scrape or draft operation waits for the page to become ready. You sign in in that browser window once, and the same browser profile can be reused later.

If the CDP browser disappears later during the session, the bridge repeats the same attach-or-launch logic the next time it needs to scrape history or prepare a draft.

Important separation:

- Baileys auth and WhatsApp Web auth are different sessions.
- Refreshing or re-linking Baileys does not, by itself, require clearing or re-linking the CDP WhatsApp Web browser profile.
- If WhatsApp Web is logged out, you sign in again in the browser profile.

### Step 4. After Baileys connects, Nanobot ensures WhatsApp Web is ready

After Baileys is connected, Nanobot uses the detected or launched CDP browser and checks whether WhatsApp Web is actually logged in and ready.

- If WhatsApp Web is already ready in the reused browser:
  - Nanobot proceeds immediately.

- If the browser exists but WhatsApp Web is not logged in yet:
  - Nanobot waits for you to sign in in that browser profile,
  - then continues once the page is ready.

- If no browser existed and Nanobot launched one:
  - you sign in in that browser if needed,
  - then Nanobot continues once the page is ready.

### Step 5. Nanobot syncs already stored direct-target history automatically

When the WhatsApp channel receives bridge status `connected`, it immediately triggers internal command `sync_direct_history`.

That command does two things:

1. replay any direct history already cached from Baileys, and
2. ask the browser layer to scrape WhatsApp Web history for all enabled `direct_reply_targets` in `data/whatsapp_reply_targets.json`.

This startup sync happens whether or not you have sent a new self control message in the current session.

### Step 6. During the session, a new self message rewrites the routing JSON and re-syncs history

If you send yourself a new self control message with an individuals block, Nanobot:

1. captures the self-chat message,
2. does not call the LLM for that message,
3. parses the routing block,
4. rewrites `data/whatsapp_reply_targets.json`,
5. updates local contact/group cache files,
6. triggers a scoped direct-history sync for the phones listed in that self message.

So the startup sync covers already stored direct targets, and the self message updates the target list during runtime.

## Self Control Message Format

Nanobot listens for self-chat messages in this exact marker format:

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

You can include both blocks in one self message:

```text
#chatbot reply to individuals#
+85212345678
+85255556666
#chatbot reply to individuals#

#chatbot reply to groups#
Group Name, +85212345678
Another Group, +85299990000
#chatbot reply to groups#
```

Rules:

- Only the latest block of each type inside that message is used.
- Individuals block:
  - one phone number per line.
- Groups block:
  - one line per target as `Group Name, Phone Number`.
- Full-width comma `，` is accepted in the group block.
- If an individuals block is present, `direct_reply_targets` is rewritten from it.
- If a groups block is present, `group_reply_targets` is rewritten from it.
- A new direct individuals block also triggers direct history re-sync for those phones.

## How Replies Are Put Into The WhatsApp Web Message Box

This is the draft-reply workflow for a direct chat.

1. A direct inbound WhatsApp message arrives through Baileys.
2. The bridge normalizes the message.
3. The Python WhatsApp channel checks whether the sender is allowed.
4. In `draft` mode, the sender must also be present and enabled in `direct_reply_targets`.
5. Nanobot runs the agent and produces a final reply.
6. Progress messages are suppressed for WhatsApp draft mode.
7. Only the final reply is sent to the browser layer as a `prepare_draft` command.
8. The browser layer opens the target direct chat.
9. It finds the compose box in WhatsApp Web.
10. It inserts the final reply into that box.
11. It does not press Enter.

Safety rules:

- It inserts the final reply only once.
- It never auto-sends.
- If the compose box already contains a different unsent draft, Nanobot does not overwrite it.
- If the chat cannot be opened, the bridge returns `chat_not_found`.
- If WhatsApp Web is not logged in or not ready, the bridge returns `not_ready`.

Important result:

Nanobot helps you prepare the message, but the final send action is still manual.

## Inbound Message Flows

### Direct chat message

For a normal direct chat:

1. Baileys emits a live `notify` event.
2. The bridge normalizes the message into a stable payload:
   - `id`
   - `sender`
   - `pn`
   - `content`
   - `timestamp`
   - `pushName`
   - media paths if files were downloaded
3. The Python WhatsApp channel:
   - resolves phone and sender identifiers,
   - updates local direct storage metadata,
   - updates direct identification fields in `whatsapp_reply_targets.json`,
   - checks contact allowlist,
   - in `draft` mode, checks `direct_reply_targets`.

Direct-chat behavior by mode:

- `draft` mode
  - sender must pass the direct contact allowlist,
  - sender must also be an enabled direct reply target to trigger reply generation,
  - if not a reply target, the message is still captured and stored as history but no LLM reply is produced.

- `send` mode
  - the legacy Baileys outbound path remains available,
  - no draft insertion is used.

### Self manual message

A self-chat control message is treated specially.

Behavior:

- It is captured as a self-chat message.
- It is marked `capture_only`.
- It is stored in history.
- The LLM is not called for it.
- Its routing blocks can rewrite:
  - `direct_reply_targets`
  - `group_reply_targets`
  - local contacts cache
  - local group cache

If the message contains an individuals block, Nanobot also re-syncs direct history for those phones.

### Group chat message

Current behavior depends on delivery mode.

- In `draft` mode:
  - all group inbound messages are ignored before agent processing.

- In `send` mode:
  - group traffic is matched against enabled `group_reply_targets` in `data/whatsapp_reply_targets.json`,
  - matching uses group name or group id plus member phone or member id,
  - only matching rows are allowed through.

For matched group rows, Nanobot also updates group identification metadata and storage folders.

## History Synchronization

Nanobot uses two sources of historical direct-chat data:

### 1. Baileys full-history sync

The bridge enables Baileys full-history sync with a desktop browser profile.

It consumes:

- `messaging-history.set`
- non-`notify` `messages.upsert`

These historical messages are normalized and forwarded as `history` batches.

### 2. WhatsApp Web history scraping

The browser layer opens each enabled direct reply target in WhatsApp Web and scrapes visible history from the chat pane.

It scrolls upward, collects message DOM nodes by `data-id`, extracts:

- message id,
- text content,
- timestamp from WhatsApp meta text,
- whether the message is from you,
- push name when present.

### How imported history is filtered

Historical import only accepts direct chats that match enabled `direct_reply_targets`.

Group history is not imported into the direct history pipeline.

### How imported history is merged

Imported messages:

- are deduped by WhatsApp `message_id`,
- are mapped into the direct session for the canonical phone,
- are inserted into the session in chronological order,
- keep both sides of the conversation,
- refresh visible history exports after save.

The direct session key is:

```text
whatsapp:<phone>
```

## Message Cleaning And Normalization

### Identifier normalization

The bridge and channel normalize several forms of WhatsApp identity:

- old-style phone JID:
  - `85212345678@s.whatsapp.net`
- old-style c.us JID:
  - `85212345678@c.us`
- LID sender ids:
  - kept as sender ids until a phone is known
- `pn` values:
  - cleaned to a normalized phone form

### Media normalization

Live inbound media is handled like this:

- image without text:
  - `[Image]`
- document without text:
  - `[Document]`
- video without text:
  - `[Video]`

When media files are downloaded by the bridge, the Python side appends path tags to the message content:

- `[image: /path/to/file]`
- `[file: /path/to/file]`

Voice messages are currently stored as a placeholder:

```text
[Voice Message: Transcription not available for WhatsApp yet]
```

Historical media scraping does not download files. It records placeholders from the WhatsApp Web DOM when necessary.

### Deduplication

- Live inbound direct messages are deduped in memory by recent message id.
- Historical imports are deduped on disk by `message_id`.

## Storage Layout

There are three important storage layers.

### 1. Reply target state

File:

```text
data/whatsapp_reply_targets.json
```

This is the authoritative routing state used by the WhatsApp channel.

Example shape:

```json
{
  "version": 1,
  "updated_at": "2026-03-21T12:00:00+00:00",
  "source": "self_chat_command",
  "direct_reply_targets": [
    {
      "phone": "85212345678",
      "enabled": true,
      "label": "",
      "chat_id": "85212345678@s.whatsapp.net",
      "sender_id": "85212345678@s.whatsapp.net",
      "push_name": "Alice",
      "last_seen_at": "2026-03-21T12:00:00+00:00"
    }
  ],
  "group_reply_targets": [
    {
      "group_name": "My Group",
      "member_phone": "85299990000",
      "enabled": true,
      "group_id": "120363400000000000@g.us",
      "member_id": "123456789@lid",
      "member_label": "Bob",
      "last_seen_at": "2026-03-21T12:00:00+00:00"
    }
  ]
}
```

### 2. Canonical session files

Directory:

```text
<workspace>/sessions/
```

For a direct chat, the canonical session file is usually:

```text
<workspace>/sessions/whatsapp_85212345678.jsonl
```

The first line is session metadata.

Following lines are the actual stored messages.

For WhatsApp sessions, stored roles are:

- `client`
- `me`

Example:

```json
{"_type":"metadata","key":"whatsapp:85212345678","created_at":"2026-03-09T10:11:37.000000","updated_at":"2026-03-21T12:00:00.000000","metadata":{},"last_consolidated":0}
{"role":"client","content":"Hi","timestamp":"2026-03-09T10:11:37.718083","message_id":"3EB0...","from_me":false}
{"role":"me","content":"Hi Alice, how can I help?","timestamp":"2026-03-09T10:11:37.900000","message_id":"3A1A...","from_me":true}
```

Internally, Nanobot maps these back to model-standard roles only at the provider boundary:

- `client -> user`
- `me -> assistant`

This keeps the stored history human-readable without breaking LLM provider APIs.

### 3. Visible chat history exports

Nanobot also writes human-readable exports under:

```text
Chathistories/
```

Direct WhatsApp bundles use phone-derived names when the phone is known:

```text
Chathistories/whatsapp__85212345678/
```

Each bundle contains:

- `meta.json`
  - session metadata and path to the source session file
- `history.jsonl`
  - visible chat history with `client` / `me`

### 4. WhatsApp storage index

Workspace directory:

```text
<workspace>/whatsapp-storage/
```

This provides per-contact and per-group folders for operational inspection.

Direct example:

```text
<workspace>/whatsapp-storage/direct/alice__85212345678/
```

Contents:

- `meta.json`
- `history.jsonl`

Group example:

```text
<workspace>/whatsapp-storage/groups/row-001__my-group__bob/
```

Contents:

- `meta.json`
- `history.jsonl` or `history.path.txt`

## Sending Logic

The outbound logic is intentionally simple.

### In `draft` mode

- progress updates are suppressed for WhatsApp,
- only the final LLM reply is used,
- the bridge receives `prepare_draft`,
- the reply is placed into the WhatsApp Web compose box,
- sending remains manual.

### In `send` mode

- the bridge receives `send`,
- Baileys sends the message directly,
- the draft/browser insertion path is bypassed.

## Auth Sessions And What They Mean

### Baileys auth

Path:

```text
~/.nanobot/whatsapp-auth
```

Purpose:

- linked-device auth for inbound WhatsApp events,
- QR appears only when this session is missing or invalid.

### WhatsApp Web CDP browser profile

Path:

```text
~/.nanobot/whatsapp-web
```

Purpose:

- WhatsApp Web login for:
  - history scraping,
  - draft insertion into the compose box.

These sessions are separate.

You can have:

- valid Baileys auth but logged-out WhatsApp Web,
- or valid WhatsApp Web but invalid Baileys auth.

Refreshing or re-linking Baileys does not automatically require refreshing the CDP browser profile.

In normal operation:

- if Baileys auth becomes invalid:
  - the gateway re-links Baileys and shows a fresh QR code,
  - the existing WhatsApp Web browser profile can still remain usable.

- if the CDP browser profile is logged out:
  - you sign in again in WhatsApp Web,
  - the Baileys auth session can still remain usable.

Both matter in `draft` mode.

## Recommended Daily Usage

### First-time setup

1. Install the repo in editable mode.
2. Configure `~/.nanobot/config.json`.
3. Run:

```bash
nanobot channels whatsapp-web
```

4. In the opened Chrome window, log in to WhatsApp Web.
5. Run:

```bash
whatsapp-web-nanobot-gateway
```

6. If Baileys asks for a QR, scan it.
7. Send yourself a control message to define direct reply targets.

### Normal daily usage

If both sessions are still valid, usually you only need:

```bash
whatsapp-web-nanobot-gateway
```

The gateway will:

- reuse Baileys auth if possible,
- reuse the existing CDP browser if possible,
- otherwise launch a new debuggable Chrome,
- sync history for already stored direct reply targets,
- wait for new inbound traffic.

## Troubleshooting

### No Baileys QR appears

That usually means the existing Baileys auth session is still valid.

No QR is expected in that case.

If the saved Baileys auth is detected as invalid or logged out, the bridge now clears the stale Baileys auth state and requests a fresh QR automatically.

### WhatsApp Web does not seem to be reused

Nanobot only reuses a browser that is available through the configured CDP endpoint.

A normal Chrome window that was not started with remote debugging is not considered a reusable CDP browser.

### The browser opens but history scrape or draft says `not_ready`

That usually means WhatsApp Web is not logged in yet in the CDP browser profile.

Log in in that browser window and retry.

This does not necessarily mean Baileys auth is invalid.

### A direct message was stored but did not trigger a reply

In `draft` mode, that usually means one of these is true:

- the sender was not allowed by contacts or `allowFrom`,
- the sender was not in enabled `direct_reply_targets`,
- the message was a self-chat command,
- the message was a group chat.

### The reply was not inserted because the compose box was busy

Nanobot will not overwrite a different unsent draft already present in the WhatsApp Web message box.

Clear the compose box manually, then retry.

## Summary

The working model is:

1. `whatsapp-web-nanobot-gateway` starts the gateway.
2. Baileys auth is checked first and reused or re-linked with QR.
3. The CDP browser is then rigorously detected and reused if present.
4. If no CDP browser exists, Nanobot launches one and opens WhatsApp Web.
5. Nanobot ensures WhatsApp Web is logged in and ready in that browser before web tasks proceed.
6. Startup history sync runs for already stored enabled `direct_reply_targets`, regardless of whether you sent a new self message in the current session.
7. A new self message can rewrite the target JSON and trigger scoped history updates during runtime.
8. Baileys auth and WhatsApp Web auth remain separate; refreshing one does not automatically require refreshing the other.
9. Direct reply-target messages generate a final reply that is pasted into the WhatsApp Web message box and left unsent.

That is the current default WhatsApp workflow in this repository.
