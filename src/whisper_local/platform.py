"""The contract a platform module (linux.py) fulfils.

A platform module exposes two functions:

    listen(key: str, handler: HotkeyHandler, hands_free_key: str | None = None) -> None
        Block forever, reporting the dictation key and optional dedicated
        hands-free key to `handler`. Both are in KEYS and distinct. Callbacks
        may arrive on any thread and must return quickly.

    insert_text(text: str) -> None
        Put `text` where the user is typing, as if they had typed it.
"""

from typing import Protocol

# Keys a user can pick for dictation. Modifiers on the right side are rarely
# used alone, so holding one does not collide with normal typing.
KEYS = ("right_ctrl", "right_alt", "right_cmd", "right_shift", "f13", "f14", "f15")


class HotkeyHandler(Protocol):
    def pressed(self) -> None:
        """The dictation key went down."""

    def released(self) -> None:
        """The dictation key came back up."""

    def interrupted(self) -> None:
        """Another key went down while the dictation key was held: the user is
        typing a shortcut such as Right Ctrl+C, not dictating."""

    def escaped(self) -> None:
        """Esc went down, with or without the dictation key held."""

    def hands_free_pressed(self) -> None:
        """The dedicated hands-free key went down."""

    def hands_free_released(self) -> None:
        """The dedicated hands-free key came back up."""
