# Per-Client Data Isolation

## Invariant

> **Client-scoped operations must never access another client's conversation data.**

When the system handles a message from Client A, it can only read Client A's
chat history, Client A's memory, and shared global knowledge. It must never
leak Client B's data into Client A's context, prompts, or responses.

## Architecture

### ClientKey — single source of truth

Every data path resolves through `nanobot.session.client_key.ClientKey`.
A `ClientKey` is constructed from a raw phone string and normalises it to
digits only, stripping `+`, `-`, spaces, and `@s.whatsapp.net` suffixes.

```python
from nanobot.session.client_key import ClientKey

key = ClientKey.normalize("+852-6842-4658")
key.phone          # "85268424658"
key.session_key    # "whatsapp:85268424658"
key.memory_dir(ws) # <workspace>/memory/85268424658/
```

Two `ClientKey` objects are equal if and only if their normalised digits match.
`ClientKey.assert_same_client(other)` raises `CrossClientError` on mismatch.

### Per-client memory

Each client's knowledge is stored in its own directory:

```
memory/
  85268424658/
    MEMORY.md    ← Client-specific long-term notes
    HISTORY.md   ← Client-specific conversation summary
  85295119020/
    MEMORY.md
    HISTORY.md
  GLOBAL.md      ← Operator-curated product knowledge (read by all clients)
```

`MemoryStore(workspace, client_key)` reads only from the client's directory
plus the shared `GLOBAL.md`. It refuses to consolidate a session belonging
to a different client, raising `CrossClientError`.

### Scoped context assembly

`ContextBuilder(workspace, client_key=key)` builds a system prompt
containing only the target client's memory and global knowledge. The
`client_key` parameter is mandatory for all message-processing paths.

### Session scoping

`SessionManager.get_for_client(client_key)` derives the session key
internally from the `ClientKey`, preventing callers from constructing
arbitrary session keys.

### API layer

`_resolve_client_key(phone)` validates and normalises phone input before
any data lookup. `_get_session_messages()` uses `get_for_client()` so the
API can only return messages belonging to the resolved client.

## Hardened Data Flows

### History import

`_import_history_batch()` enforces:
1. Entries with empty `phone` are silently dropped.
2. The entry's normalised phone must match the session key's normalised
   phone. Mismatches are logged and dropped.
3. A belt-and-suspenders `ClientKey.assert_same_client()` check catches
   any edge case where normalisation diverges.

### Reply-target matching

- **Direct chats**: When a `chat_id` / `sender_id` match is found, the
  matched row's phone is cross-validated against the incoming message
  phone. Mismatches are rejected.
- **Group chats**: A group matched by display-name only (no `group_id`)
  additionally requires a member-phone match to avoid name-collision
  cross-client leakage.

### Debug snapshots

Debug turn-snapshot filenames embed the client phone so artefacts for
different clients never overwrite each other. Snapshot content reads
only the target client's per-client memory files.

## Forbidden Patterns

| Pattern | Why it's dangerous |
|---|---|
| Reading `memory/MEMORY.md` as client-specific data | That's the legacy global path — now `GLOBAL.md` |
| Constructing session keys from raw strings | Always go through `ClientKey.normalize()` |
| Matching reply targets by `push_name` alone | Push names are mutable and have duplicates |
| Importing history entries without phone validation | Creates cross-client leakage vector |
| Using `MemoryStore` without a `ClientKey` | Would default to global memory only |

## Testing

Run the isolation regression suite:

```bash
python -m pytest tests/test_client_isolation.py -v
```

37 tests cover: `ClientKey` normalisation, cross-client assertions,
per-client memory isolation, prompt assembly scoping, history import
guards, reply-target hardening, session key derivation, AI draft
scoping, and edge cases (legacy migration, path derivation).
