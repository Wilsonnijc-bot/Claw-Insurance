"""Regression tests for cross-client data isolation.

Invariant under test:
    "Client-scoped operations must never access another client's
     conversation data."

These tests exercise every hardened boundary:
  - ClientKey normalisation and comparison
  - Per-client memory isolation (MemoryStore)
  - Per-client prompt assembly (ContextBuilder)
  - History import phone guard
  - Reply-target matching (direct and group)
  - Session key derivation and scoping
  - API session resolution
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.session.client_key import ClientKey, CrossClientError


# ──────────────────────────────────────────────────────────────────
#  1. ClientKey normalisation & comparison
# ──────────────────────────────────────────────────────────────────

class TestClientKeyNormalization:
    """Ensure phone formatting never causes identity confusion."""

    def test_digits_only(self):
        k = ClientKey.normalize("+852-6842-4658")
        assert k.phone == "85268424658"

    def test_jid_suffix_stripped(self):
        k = ClientKey.normalize("85268424658@s.whatsapp.net")
        assert k.phone == "85268424658"

    def test_same_phone_different_format(self):
        """'+852-1234-5678' and '85212345678' must resolve to the same key."""
        a = ClientKey.normalize("+852-1234-5678")
        b = ClientKey.normalize("85212345678")
        assert a == b
        assert hash(a) == hash(b)

    def test_similar_phones_not_equal(self):
        """85212345678 and 85212345679 differ by one digit — must be different."""
        a = ClientKey.normalize("85212345678")
        b = ClientKey.normalize("85212345679")
        assert a != b

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            ClientKey.normalize("")

    def test_non_digits_raises(self):
        with pytest.raises(ValueError):
            ClientKey.normalize("abc")

    def test_try_normalize_returns_none(self):
        assert ClientKey.try_normalize("") is None
        assert ClientKey.try_normalize("abc") is None

    def test_session_key_derivation_deterministic(self):
        a = ClientKey.normalize("852111")
        b = ClientKey.normalize("+852-111")
        assert a.session_key == b.session_key == "whatsapp:852111"
        c = ClientKey.normalize("852222")
        assert c.session_key != a.session_key

    def test_from_session_key_direct(self):
        k = ClientKey.from_session_key("whatsapp:85212345678")
        assert k.phone == "85212345678"

    def test_from_session_key_group(self):
        k = ClientKey.from_session_key("whatsapp:120363@g.us:85212345678")
        assert k.phone == "85212345678"

    def test_from_session_key_non_whatsapp_raises(self):
        with pytest.raises(ValueError):
            ClientKey.from_session_key("cli:direct")


# ──────────────────────────────────────────────────────────────────
#  2. Cross-client assertion
# ──────────────────────────────────────────────────────────────────

class TestCrossClientAssertion:
    def test_same_client_passes(self):
        a = ClientKey.normalize("852111")
        b = ClientKey.normalize("852111")
        ClientKey.assert_same_client(a, b)  # should not raise

    def test_different_client_raises(self):
        a = ClientKey.normalize("852111")
        b = ClientKey.normalize("852222")
        with pytest.raises(CrossClientError):
            ClientKey.assert_same_client(a, b)


# ──────────────────────────────────────────────────────────────────
#  3. Per-client memory isolation
# ──────────────────────────────────────────────────────────────────

class TestPerClientMemoryIsolation:
    """Memory consolidation must write to per-client dirs, not a shared global."""

    def _make_workspace(self, tmp: Path) -> Path:
        ws = tmp / "workspace"
        ws.mkdir()
        (ws / "memory").mkdir()
        return ws

    def test_memory_writes_to_per_client_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            from nanobot.agent.memory import MemoryStore

            store_a = MemoryStore(ws, ClientKey.normalize("852111"))
            store_b = MemoryStore(ws, ClientKey.normalize("852222"))

            store_a.write_long_term("Client A secret insurance facts")
            store_b.write_long_term("Client B private medical info")

            # Each writes to its own directory
            assert store_a.memory_file == ws / "memory" / "852111" / "MEMORY.md"
            assert store_b.memory_file == ws / "memory" / "852222" / "MEMORY.md"

            # Neither can read the other's data
            a_content = store_a.read_long_term()
            b_content = store_b.read_long_term()
            assert "Client A" in a_content
            assert "Client B" not in a_content
            assert "Client B" in b_content
            assert "Client A" not in b_content

    def test_memory_context_excludes_other_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            from nanobot.agent.memory import MemoryStore

            store_a = MemoryStore(ws, ClientKey.normalize("852111"))
            store_b = MemoryStore(ws, ClientKey.normalize("852222"))

            store_a.write_long_term("Alice has critical illness cover")
            store_b.write_long_term("Bob wants dental only")

            ctx_a = store_a.get_memory_context()
            ctx_b = store_b.get_memory_context()

            assert "Alice" in ctx_a
            assert "Bob" not in ctx_a
            assert "Bob" in ctx_b
            assert "Alice" not in ctx_b

    def test_global_knowledge_shared_read_only(self):
        """memory/GLOBAL.md is accessible to all clients (operator-curated)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            (ws / "memory" / "GLOBAL.md").write_text("Product catalog info", encoding="utf-8")

            from nanobot.agent.memory import MemoryStore

            store_a = MemoryStore(ws, ClientKey.normalize("852111"))
            ctx_a = store_a.get_memory_context()
            assert "Product catalog info" in ctx_a

    def test_consolidation_rejects_wrong_client(self):
        """MemoryStore for Client A must refuse to consolidate Client B's session."""
        import asyncio
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            from nanobot.agent.memory import MemoryStore
            from nanobot.session.manager import Session

            store_a = MemoryStore(ws, ClientKey.normalize("852111"))
            session_b = Session(key="whatsapp:852222")
            session_b.add_message("user", "Hello from client B")

            with pytest.raises(CrossClientError):
                asyncio.run(store_a.consolidate(session_b, MagicMock(), "test-model"))


# ──────────────────────────────────────────────────────────────────
#  4. Prompt assembly isolation
# ──────────────────────────────────────────────────────────────────

class TestPromptAssemblyIsolation:
    """ContextBuilder for Client A must only include Client A's memory."""

    def test_prompt_excludes_other_client_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir()
            (ws / "memory").mkdir()
            # Write per-client memories
            (ws / "memory" / "852111").mkdir()
            (ws / "memory" / "852111" / "MEMORY.md").write_text("Alice has cancer history", encoding="utf-8")
            (ws / "memory" / "852222").mkdir()
            (ws / "memory" / "852222" / "MEMORY.md").write_text("Bob is 25 healthy", encoding="utf-8")

            from nanobot.agent.context import ContextBuilder

            ctx_a = ContextBuilder(ws, client_key=ClientKey.normalize("852111"))
            ctx_b = ContextBuilder(ws, client_key=ClientKey.normalize("852222"))

            prompt_a = ctx_a.build_system_prompt()
            prompt_b = ctx_b.build_system_prompt()

            assert "cancer history" in prompt_a
            assert "25 healthy" not in prompt_a
            assert "25 healthy" in prompt_b
            assert "cancer history" not in prompt_b

    def test_same_push_name_different_clients(self):
        """Two clients both named 'John' must get separate memory."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir()
            (ws / "memory").mkdir()
            (ws / "memory" / "852111").mkdir()
            (ws / "memory" / "852111" / "MEMORY.md").write_text("John in Kowloon wants dental", encoding="utf-8")
            (ws / "memory" / "852222").mkdir()
            (ws / "memory" / "852222" / "MEMORY.md").write_text("John in Central wants life cover", encoding="utf-8")

            from nanobot.agent.context import ContextBuilder

            ctx_a = ContextBuilder(ws, client_key=ClientKey.normalize("852111"))
            ctx_b = ContextBuilder(ws, client_key=ClientKey.normalize("852222"))

            prompt_a = ctx_a.build_system_prompt()
            prompt_b = ctx_b.build_system_prompt()

            assert "dental" in prompt_a
            assert "life cover" not in prompt_a
            assert "life cover" in prompt_b
            assert "dental" not in prompt_b


# ──────────────────────────────────────────────────────────────────
#  5. History import isolation
# ──────────────────────────────────────────────────────────────────

class TestHistoryImportIsolation:
    """History import must reject entries for the wrong client."""

    def _make_loop(self, ws: Path):
        """Create a minimal AgentLoop for testing _import_history_batch."""
        from nanobot.session.manager import SessionManager
        from nanobot.bus.events import InboundHistoryBatch

        sessions = SessionManager(ws)
        loop = MagicMock()
        loop.sessions = sessions
        loop.workspace = ws
        loop._save_session = lambda s, **_: sessions.save(s)
        loop._refresh_whatsapp_history_exports = lambda s: None
        # Bind the real methods
        from nanobot.agent.loop import AgentLoop
        loop._import_history_batch = AgentLoop._import_history_batch.__get__(loop)
        loop._merge_history_entries = AgentLoop._merge_history_entries.__get__(loop)
        loop._history_timestamp_iso = AgentLoop._history_timestamp_iso
        loop._history_sort_value = AgentLoop._history_sort_value
        loop._history_epoch_seconds = AgentLoop._history_epoch_seconds
        return loop, sessions, InboundHistoryBatch

    def test_rejects_empty_phone(self):
        """History entries without a phone field must be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            loop, sessions, InboundHistoryBatch = self._make_loop(ws)

            batch = InboundHistoryBatch(
                channel="whatsapp",
                entries=[
                    {
                        "session_key": "whatsapp:852111",
                        "message_id": "msg1",
                        "chat_id": "852111@s.whatsapp.net",
                        "phone": "",  # Empty phone — must be rejected
                        "content": "Leaked message",
                        "timestamp": "2026-01-01T00:00:00",
                    }
                ],
            )
            loop._import_history_batch(batch)
            session = sessions.get_or_create("whatsapp:852111")
            assert len(session.messages) == 0

    def test_rejects_wrong_phone(self):
        """Phone 852222 into session whatsapp:852111 must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            loop, sessions, InboundHistoryBatch = self._make_loop(ws)

            batch = InboundHistoryBatch(
                channel="whatsapp",
                entries=[
                    {
                        "session_key": "whatsapp:852111",
                        "message_id": "msg1",
                        "chat_id": "852222@s.whatsapp.net",
                        "phone": "852222",  # Wrong phone
                        "content": "Wrong client's message",
                        "timestamp": "2026-01-01T00:00:00",
                    }
                ],
            )
            loop._import_history_batch(batch)
            session = sessions.get_or_create("whatsapp:852111")
            assert len(session.messages) == 0

    def test_accepts_correct_phone(self):
        """Phone 852111 into session whatsapp:852111 must be accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            loop, sessions, InboundHistoryBatch = self._make_loop(ws)

            batch = InboundHistoryBatch(
                channel="whatsapp",
                entries=[
                    {
                        "session_key": "whatsapp:852111",
                        "message_id": "msg1",
                        "chat_id": "852111@s.whatsapp.net",
                        "phone": "852111",
                        "content": "Correct client's message",
                        "timestamp": "2026-01-01T00:00:00",
                    }
                ],
            )
            loop._import_history_batch(batch)
            session = sessions.get_or_create("whatsapp:852111")
            assert len(session.messages) == 1
            assert session.messages[0]["content"] == "Correct client's message"

    def test_phone_normalization_in_import(self):
        """+852-1234-5678 and 85212345678 should match when importing."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            loop, sessions, InboundHistoryBatch = self._make_loop(ws)

            batch = InboundHistoryBatch(
                channel="whatsapp",
                entries=[
                    {
                        "session_key": "whatsapp:85212345678",
                        "message_id": "msg1",
                        "chat_id": "85212345678@s.whatsapp.net",
                        "phone": "+852-1234-5678",  # Different format, same phone
                        "content": "Formatted phone message",
                        "timestamp": "2026-01-01T00:00:00",
                    }
                ],
            )
            loop._import_history_batch(batch)
            session = sessions.get_or_create("whatsapp:85212345678")
            # The normalised comparison should let it through
            assert len(session.messages) == 1

    def test_partial_phone_overlap_rejected(self):
        """85212345679 into session whatsapp:85212345678 (one digit off) must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            loop, sessions, InboundHistoryBatch = self._make_loop(ws)

            batch = InboundHistoryBatch(
                channel="whatsapp",
                entries=[
                    {
                        "session_key": "whatsapp:85212345678",
                        "message_id": "msg1",
                        "chat_id": "85212345679@s.whatsapp.net",
                        "phone": "85212345679",  # One digit off
                        "content": "Wrong client's message",
                        "timestamp": "2026-01-01T00:00:00",
                    }
                ],
            )
            loop._import_history_batch(batch)
            session = sessions.get_or_create("whatsapp:85212345678")
            assert len(session.messages) == 0


# ──────────────────────────────────────────────────────────────────
#  6. Reply-target matching isolation
# ──────────────────────────────────────────────────────────────────

class TestReplyTargetIsolation:
    """Reply target matching must never match by display name."""

    def test_direct_no_name_fallback(self):
        """A direct target lookup never matches by push_name or label alone."""
        from nanobot.channels.whatsapp_reply_targets import (
            DirectReplyTarget,
            match_direct_reply_target,
        )

        rows = [
            DirectReplyTarget(phone="852111", push_name="Alice", label="Alice Wong"),
            DirectReplyTarget(phone="852222", push_name="Bob", label="Bob Chan"),
        ]
        # Searching by phone should work
        assert match_direct_reply_target(rows, phone="852111") is not None
        assert match_direct_reply_target(rows, phone="852111").phone == "852111"

        # There is no name-based matching — a mismatched phone should NOT find Alice
        result = match_direct_reply_target(rows, phone="852999")
        assert result is None  # No fallback to name

    def test_direct_chat_id_cross_validated(self):
        """chat_id match must cross-validate against phone when available."""
        from nanobot.channels.whatsapp_reply_targets import (
            DirectReplyTarget,
            match_direct_reply_target,
        )

        rows = [
            DirectReplyTarget(
                phone="852111",
                chat_id="852111@s.whatsapp.net",
                sender_id="852111@s.whatsapp.net",
            ),
        ]
        # Correct phone + chat_id -> match
        result = match_direct_reply_target(rows, phone="852111", chat_id="852111@s.whatsapp.net")
        assert result is not None

        # Wrong phone but matching stale chat_id -> must NOT match
        result = match_direct_reply_target(rows, phone="852222", chat_id="852111@s.whatsapp.net")
        assert result is None

    def test_stale_push_name_no_cross_match(self):
        """Client A's old push_name matching Client B's name causes no mismatch."""
        from nanobot.channels.whatsapp_reply_targets import (
            DirectReplyTarget,
            match_direct_reply_target,
        )

        rows = [
            DirectReplyTarget(phone="852111", push_name="John"),
            DirectReplyTarget(phone="852222", push_name="John"),  # Same push_name!
        ]
        # Lookup by phone "852111" should only return the first
        result = match_direct_reply_target(rows, phone="852111")
        assert result is not None
        assert result.phone == "852111"

        # Lookup by phone "852222" should only return the second
        result = match_direct_reply_target(rows, phone="852222")
        assert result is not None
        assert result.phone == "852222"

    def test_group_name_collision_requires_phone(self):
        """Two groups with the same name but different group_ids must not be confused."""
        from nanobot.channels.whatsapp_reply_targets import (
            GroupReplyTarget,
            match_group_reply_target,
        )

        rows = [
            GroupReplyTarget(
                group_name="Insurance Team",
                member_phone="852111",
                group_id="",  # No group_id yet — only name
                member_id="",
            ),
            GroupReplyTarget(
                group_name="Insurance Team",
                member_phone="852222",
                group_id="",  # Same name, different member
                member_id="",
            ),
        ]
        # Match by name should require member phone
        result = match_group_reply_target(
            rows,
            group_name="Insurance Team",
            member_phone="852111",
        )
        assert result is not None
        assert result[1].member_phone == "852111"

        result = match_group_reply_target(
            rows,
            group_name="Insurance Team",
            member_phone="852222",
        )
        assert result is not None
        assert result[1].member_phone == "852222"

        # Unknown member phone should NOT match either
        result = match_group_reply_target(
            rows,
            group_name="Insurance Team",
            member_phone="852999",
        )
        assert result is None

    def test_group_name_only_match_requires_member_phone(self):
        """When matched by group name only (no group_id), member phone must match."""
        from nanobot.channels.whatsapp_reply_targets import (
            GroupReplyTarget,
            match_group_reply_target,
        )

        rows = [
            GroupReplyTarget(
                group_name="Sales",
                member_phone="852111",
                group_id="",  # No group_id
                member_id="member1@s.whatsapp.net",
            ),
        ]
        # member_id only (no phone) should fail when group_id is missing
        result = match_group_reply_target(
            rows,
            group_name="Sales",
            member_id="member1@s.whatsapp.net",
            member_phone="",  # No phone provided
        )
        assert result is None

        # With correct member phone it should succeed
        result = match_group_reply_target(
            rows,
            group_name="Sales",
            member_id="member1@s.whatsapp.net",
            member_phone="852111",
        )
        assert result is not None


# ──────────────────────────────────────────────────────────────────
#  7. Session key scoping
# ──────────────────────────────────────────────────────────────────

class TestSessionKeyScoping:
    """Session access must be deterministically scoped by ClientKey."""

    def test_similar_phones_isolated_sessions(self):
        """85212345678 and 85212345679 get completely separate sessions."""
        with tempfile.TemporaryDirectory() as tmp:
            from nanobot.session.manager import SessionManager

            sm = SessionManager(Path(tmp))
            key_a = ClientKey.normalize("85212345678")
            key_b = ClientKey.normalize("85212345679")

            session_a = sm.get_for_client(key_a)
            session_a.add_message("user", "Message from client A")
            sm.save(session_a)

            session_b = sm.get_for_client(key_b)
            assert len(session_b.messages) == 0  # Must be empty — different client!

            session_b.add_message("user", "Message from client B")
            sm.save(session_b)

            # Reload and verify isolation
            sm.invalidate(key_a.session_key)
            sm.invalidate(key_b.session_key)
            sa = sm.get_for_client(key_a)
            sb = sm.get_for_client(key_b)
            assert len(sa.messages) == 1
            assert sa.messages[0]["content"] == "Message from client A"
            assert len(sb.messages) == 1
            assert sb.messages[0]["content"] == "Message from client B"

    def test_api_session_key_uses_client_key(self):
        """_phone_to_session_key should use ClientKey normalisation."""
        # Simulate the API helper
        from nanobot.session.client_key import ClientKey

        phone_raw = "+852-1234-5678"
        key = ClientKey.normalize(phone_raw).session_key
        assert key == "whatsapp:85212345678"

        # Different format, same result
        key2 = ClientKey.normalize("85212345678").session_key
        assert key == key2


# ──────────────────────────────────────────────────────────────────
#  8. AI draft isolation — prompt uses only target client's memory
# ──────────────────────────────────────────────────────────────────

class TestAIDraftIsolation:
    """AI draft for Client A must use only Client A's memory + global."""

    def test_ai_draft_uses_only_client_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir()
            mem = ws / "memory"
            mem.mkdir()
            (mem / "852111").mkdir()
            (mem / "852111" / "MEMORY.md").write_text("Alice is 45, needs CI cover", encoding="utf-8")
            (mem / "852222").mkdir()
            (mem / "852222" / "MEMORY.md").write_text("Bob is 22, wants savings plan", encoding="utf-8")
            (mem / "GLOBAL.md").write_text("Company XYZ product info", encoding="utf-8")

            from nanobot.agent.context import ContextBuilder

            # Simulate what _context_for_session does for Client A
            ctx = ContextBuilder(ws, client_key=ClientKey.normalize("852111"))
            prompt = ctx.build_system_prompt()

            # Client A's memory present
            assert "Alice" in prompt
            assert "CI cover" in prompt
            # Client B's memory absent
            assert "Bob" not in prompt
            assert "savings plan" not in prompt
            # Global knowledge present
            assert "Company XYZ" in prompt


# ──────────────────────────────────────────────────────────────────
#  9. Edge cases
# ──────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases that could cause cross-client contamination."""

    def test_memory_dir_path(self):
        ws = Path("/tmp/test_workspace")
        key = ClientKey.normalize("852111")
        assert key.memory_dir(ws) == ws / "memory" / "852111"

    def test_bundle_dir_name(self):
        key = ClientKey.normalize("852111")
        assert key.bundle_dir_name == "whatsapp__852111"

    def test_legacy_global_memory_used_when_no_per_client_dirs(self):
        """When no per-client memory dirs exist, legacy MEMORY.md serves as global."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            mem = ws / "memory"
            mem.mkdir()
            (mem / "MEMORY.md").write_text("Legacy global facts", encoding="utf-8")

            from nanobot.agent.memory import _read_global_knowledge

            assert "Legacy global facts" in _read_global_knowledge(ws)

    def test_legacy_global_memory_ignored_when_per_client_dirs_exist(self):
        """When per-client dirs exist, legacy MEMORY.md is no longer treated as global."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            mem = ws / "memory"
            mem.mkdir()
            (mem / "MEMORY.md").write_text("Old mixed global data", encoding="utf-8")
            # Create a per-client dir to signal migration has happened
            (mem / "852111").mkdir()

            from nanobot.agent.memory import _read_global_knowledge

            # Should not return legacy data since per-client dirs exist
            assert _read_global_knowledge(ws) == ""

    def test_history_append_location(self):
        """MemoryStore.append_history writes to per-client HISTORY.md."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "memory").mkdir()

            from nanobot.agent.memory import MemoryStore

            store = MemoryStore(ws, ClientKey.normalize("852111"))
            store.append_history("[2026-01-01] Test entry for client 852111")

            hist_file = ws / "memory" / "852111" / "HISTORY.md"
            assert hist_file.exists()
            assert "852111" in hist_file.read_text(encoding="utf-8")

            # Verify it didn't write to global HISTORY.md
            global_hist = ws / "memory" / "HISTORY.md"
            assert not global_hist.exists()
