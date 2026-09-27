"""The Linux desktop app: a GNOME application with a settings window that
listens for the dictation key and reports its state over D-Bus."""

import gi

# Must run before any module of this package imports from gi.repository.
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from .application import main  # noqa: E402

__all__ = ["main"]
