"""Transports deliver rendered bytes to a physical display."""
from __future__ import annotations

from typing import Protocol


class Transport(Protocol):
    """Push rendered frames somewhere visible."""

    def push(self, frames: list[bytes]) -> int:
        """Send the frames (one for a static card, several for animation).
        Return bytes-on-wire for logging."""


VALID_TRANSPORTS = ("geekmagic", "geekmagic-ultra")


def get(name: str, **kwargs) -> Transport:
    if name == "geekmagic":
        from claude_meter.transports.geekmagic import GeekmagicTransport
        return GeekmagicTransport(**kwargs)
    if name == "geekmagic-ultra":
        from claude_meter.transports.geekmagic_ultra import GeekmagicUltraTransport
        return GeekmagicUltraTransport(**kwargs)
    raise ValueError(f"unknown transport: {name!r}")
