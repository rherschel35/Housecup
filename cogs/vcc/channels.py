"""Where /vcc is allowed to run.

One list. A thread counts when its parent channel is on the list.
"""

from __future__ import annotations

import discord

# Text channels (and any thread opened inside them).
VCC_CHANNEL_IDS: tuple[int, ...] = (
    1560675344485318676,
    1555035151208153209,
    1555701358814826677,
    1558611215054282822,
    1560280552303624252,
)

_THREAD_TYPES = frozenset(
    {
        discord.ChannelType.public_thread,
        discord.ChannelType.private_thread,
        discord.ChannelType.news_thread,
    }
)


def vcc_channel_allowed(channel: object | None) -> bool:
    """True for an allowed channel, or a thread whose parent is allowed."""
    if channel is None:
        return False
    channel_id = getattr(channel, "id", None)
    if channel_id in VCC_CHANNEL_IDS:
        return True
    kind = getattr(channel, "type", None)
    if isinstance(channel, discord.Thread) or kind in _THREAD_TYPES:
        return getattr(channel, "parent_id", None) in VCC_CHANNEL_IDS
    return False


def vcc_channel_refusal() -> str:
    """Ephemeral reply. Channel ids are mentions, not bare numbers."""
    rooms = ", ".join(f"<#{channel_id}>" for channel_id in VCC_CHANNEL_IDS)
    return f"Curse & Crown only runs in {rooms}."
