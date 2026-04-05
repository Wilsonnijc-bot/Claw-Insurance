"""Async message queue for decoupled channel-agent communication."""

import asyncio

from nanobot.bus.events import (
    HistoryImportResult,
    InboundHistoryBatch,
    InboundMessage,
    OutboundMessage,
)


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.history: asyncio.Queue[InboundHistoryBatch] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_observers: list[asyncio.Queue[OutboundMessage]] = []
        self._inbound_observers: list[asyncio.Queue[InboundMessage]] = []
        self._history_result_observers: list[asyncio.Queue[HistoryImportResult]] = []

    @property
    def ui_connected(self) -> bool:
        """True when at least one UI observer (API server) is listening."""
        return len(self._inbound_observers) > 0

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent.

        When the frontend UI is connected, WhatsApp messages that would
        normally trigger auto-reply are converted to capture_only so the
        agent just saves them.  The API server's inbound observer then
        handles auto-draft generation for clients with auto_draft enabled.
        """
        if (
            self._inbound_observers
            and msg.channel == "whatsapp"
            and not msg.metadata.get("capture_only")
            and not msg.metadata.get("is_self_chat")
        ):
            msg.metadata["capture_only"] = True
            msg.metadata["_auto_draft_candidate"] = True

        await self.inbound.put(msg)
        for obs in self._inbound_observers:
            try:
                obs.put_nowait(msg)
            except Exception:
                pass

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_history(self, batch: InboundHistoryBatch) -> None:
        """Publish a historical batch for silent import."""
        await self.history.put(batch)

    async def consume_history(self) -> InboundHistoryBatch:
        """Consume the next historical batch (blocks until available)."""
        return await self.history.get()

    async def publish_history_result(self, result: HistoryImportResult) -> None:
        """Publish the result of a completed historical import."""
        for obs in self._history_result_observers:
            try:
                obs.put_nowait(result)
            except Exception:
                pass

    def add_history_result_observer(self) -> asyncio.Queue[HistoryImportResult]:
        """Register a passive observer for history import results."""
        observer: asyncio.Queue[HistoryImportResult] = asyncio.Queue()
        self._history_result_observers.append(observer)
        return observer

    def remove_history_result_observer(self, observer: asyncio.Queue[HistoryImportResult]) -> None:
        """Remove a previously registered history result observer."""
        try:
            self._history_result_observers.remove(observer)
        except ValueError:
            pass

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)
        for obs in self._outbound_observers:
            try:
                obs.put_nowait(msg)
            except Exception:
                pass

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()

    @property
    def history_size(self) -> int:
        """Number of pending history batches."""
        return self.history.qsize()
