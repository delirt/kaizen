"""Telegram-agnostic view objects. Handlers translate them to real Telegram messages."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Btn:
    text: str
    data: str


@dataclass
class Screen:
    text: str
    rows: list[list[Btn]] = field(default_factory=list)


@dataclass
class Response:
    """What to do after an update.

    edit   -> replace the text/keyboard of the message the user pressed a button on
    send   -> new messages to send, in order
    strip  -> remove the keyboard from the pressed message (button already used)
    toast  -> small popup over the pressed button
    """
    edit: Screen | None = None
    send: list[Screen] = field(default_factory=list)
    strip: bool = False
    toast: str | None = None
    toast_alert: bool = False
