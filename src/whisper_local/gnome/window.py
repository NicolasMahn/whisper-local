import threading

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from whisper_local.platform import KEYS
from whisper_local.transcribe import server_status

KEYBOARD_PROBLEM = "Keyboard access needed"

KEY_NAMES = {
    "right_ctrl": "Right Ctrl",
    "right_alt": "Right Alt",
    "right_cmd": "Right Super",
    "right_shift": "Right Shift",
    "f13": "F13",
    "f14": "F14",
    "f15": "F15",
}
LANGUAGES = {
    "": "Detect automatically",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "zh": "Chinese",
    "ja": "Japanese",
}

# What each status looks like: icon and libadwaita style class. The theme
# owns the actual colours behind "success", "warning" and friends.
DICTATION_LOOK = {
    "ready": ("object-select-symbolic", "success"),
    "listening": ("audio-input-microphone-symbolic", "accent"),
    "hands_free": ("audio-input-microphone-symbolic", "accent"),
    "transcribing": ("content-loading-symbolic", "accent"),
    "problem": ("dialog-warning-symbolic", "warning"),
    "off": ("microphone-disabled-symbolic", "dimmed"),
}
SERVER_LOOK = {
    "checking": ("Checking…", "dimmed"),
    "ok": ("Reachable", "success"),
    "unauthorized": ("Key rejected", "error"),
    "unreachable": ("Unreachable", "error"),
}


def _copy_button(on_click) -> Gtk.Button:
    button = Gtk.Button(
        icon_name="edit-copy-symbolic", tooltip_text="Copy", valign=Gtk.Align.CENTER
    )
    button.add_css_class("flat")
    button.connect("clicked", lambda _button: on_click())
    return button


def _set_style(widget: Gtk.Widget, style: str, styles) -> None:
    for other in styles:
        widget.remove_css_class(other)
    widget.add_css_class(style)


class Window(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(
            application=application,
            title="Whisper Local",
            default_width=460,
            default_height=560,
            hide_on_close=True,
        )
        self._app = application

        menu = Gio.Menu()
        menu.append("Quit", "app.quit")
        header = Adw.HeaderBar()
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        page = Adw.PreferencesPage()
        page.add(self._status_group())
        page.add(self._recent_group)
        page.add(self._dictation_group())
        page.add(self._server_group())

        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        self._toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self._toasts)

        for name in ("state", "enabled", "problem"):
            application.connect(f"notify::{name}", lambda *_: self._show_status())
        application.connect("notify::recent", lambda *_: self._show_recent())
        self._show_status()
        self._show_recent()
        # The server may have come up or gone away while the window was hidden.
        self.connect("map", lambda _window: self._check_server())

    def _status_group(self) -> Adw.PreferencesGroup:
        self._status_icon = Gtk.Image()
        self._dictation_row = Adw.SwitchRow(title="Dictation", use_markup=False)
        self._dictation_row.add_prefix(self._status_icon)
        self._app.bind_property(
            "enabled", self._dictation_row, "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )

        self._keyboard_row = Adw.ActionRow(
            title="Let Whisper Local see the dictation key",
            subtitle="Keyboards only, for whoever uses this computer. Asks for your password.",
        )
        self._allow_button = Gtk.Button(label="Allow", valign=Gtk.Align.CENTER)
        self._allow_button.add_css_class("suggested-action")
        self._allow_button.connect("clicked", self._allow_keyboard)
        self._keyboard_row.add_suffix(self._allow_button)

        group = Adw.PreferencesGroup()
        for row in (self._dictation_row, self._keyboard_row):
            group.add(row)
        self._recent_group = Adw.PreferencesGroup()
        self._recent_rows = []
        return group

    def _dictation_group(self) -> Adw.PreferencesGroup:
        config = self._app.config
        self._keys = list(KEYS)
        key_row = Adw.ComboRow(
            title="Dictation key",
            model=Gtk.StringList.new([KEY_NAMES[key] for key in self._keys]),
        )
        if config.key in self._keys:
            key_row.set_selected(self._keys.index(config.key))
        key_row.connect("notify::selected", self._key_selected)

        self._hands_free_keys = ["", *(key for key in KEYS if key != config.key)]
        hands_free_row = Adw.ComboRow(
            title="Hands-free",
            model=Gtk.StringList.new([
                f"Double-tap {KEY_NAMES.get(config.key, config.key)}",
                *(KEY_NAMES[key] for key in self._hands_free_keys[1:]),
            ]),
        )
        if config.hands_free_key in self._hands_free_keys:
            hands_free_row.set_selected(self._hands_free_keys.index(config.hands_free_key))
        hands_free_row.connect("notify::selected", self._hands_free_selected)

        # Keep a language set by hand in the file selectable instead of losing it.
        self._languages = list(LANGUAGES)
        if config.language not in LANGUAGES:
            self._languages.append(config.language)
        language_row = Adw.ComboRow(
            title="Language",
            model=Gtk.StringList.new([LANGUAGES.get(code, code) for code in self._languages]),
            selected=self._languages.index(config.language),
        )
        language_row.connect("notify::selected", self._language_selected)

        sounds_row = Adw.SwitchRow(title="Sounds", active=config.sounds)
        sounds_row.connect(
            "notify::active", lambda row, _: self._app.change_settings(sounds=row.get_active())
        )

        group = Adw.PreferencesGroup()
        for row in (key_row, hands_free_row, language_row, sounds_row):
            group.add(row)
        return group

    def _server_group(self) -> Adw.PreferencesGroup:
        self._server_row = Adw.EntryRow(
            title="Speech server", text=self._app.config.url, show_apply_button=True
        )
        self._server_row.connect("apply", self._server_applied)
        self._server_label = Gtk.Label(valign=Gtk.Align.CENTER)
        self._server_label.add_css_class("caption")
        self._server_row.add_suffix(self._server_label)

        group = Adw.PreferencesGroup()
        group.add(self._server_row)
        self._api_key_row = Adw.PasswordEntryRow(
            title="API key", text=self._app.config.api_key, show_apply_button=True
        )
        self._api_key_row.connect("apply", self._api_key_applied)
        group.add(self._api_key_row)

        self._known_row = Adw.ActionRow(
            title="Known words",
            subtitle="Names and terms the transcription should expect, like DRY or Qwen",
        )
        group.add(self._known_row)
        self._known_entry = Adw.EntryRow(title="Add a name or term")
        self._known_entry.connect("entry-activated", self._known_word_added)
        group.add(self._known_entry)
        self._known_group = group
        self._known_rows = []
        self._show_known_words()
        return group

    def _show_known_words(self) -> None:
        for row in self._known_rows:
            self._known_group.remove(row)
        self._known_rows = []
        for index, word in enumerate(self._app.config.vocabulary):
            row = Adw.ActionRow(title=word, use_markup=False)
            remove = Gtk.Button(
                icon_name="list-remove-symbolic",
                tooltip_text=f"Remove {word}",
                valign=Gtk.Align.CENTER,
            )
            remove.add_css_class("flat")
            remove.connect("clicked", lambda _button, index=index: self._remove_known_word(index))
            row.add_suffix(remove)
            self._known_group.add(row)
            self._known_rows.append(row)

    def _known_word_added(self, row) -> None:
        word = row.get_text().strip()
        if word and word not in self._app.config.vocabulary:
            self._app.change_settings(vocabulary=[*self._app.config.vocabulary, word])
            self._show_known_words()
        row.set_text("")

    def _remove_known_word(self, index: int) -> None:
        words = self._app.config.vocabulary.copy()
        words.pop(index)
        self._app.change_settings(vocabulary=words)
        self._show_known_words()

    def _show_status(self) -> None:
        app = self._app
        if not app.enabled:
            look, text = "off", None
        elif app.problem:
            look, text = "problem", app.problem
        elif app.state == "listening":
            look, text = "listening", "Listening…"
        elif app.state == "hands_free":
            finish_key = KEY_NAMES.get(
                app.config.hands_free_key or app.key, app.config.hands_free_key or app.key
            )
            look, text = "hands_free", f"Hands-free listening… Tap {finish_key} to finish · Esc to cancel"
        elif app.state == "transcribing":
            look, text = "transcribing", "Transcribing…"
        else:
            key = KEY_NAMES.get(app.key, app.key)
            hands_free = app.config.hands_free_key
            hint = (
                f"tap {KEY_NAMES.get(hands_free, hands_free)} for hands-free"
                if hands_free else "double-tap for hands-free"
            )
            look, text = "ready", f"Hold {key} and speak · {hint}"
        icon, style = DICTATION_LOOK[look]
        self._status_icon.set_from_icon_name(icon)
        _set_style(self._status_icon, style, {style for _, style in DICTATION_LOOK.values()})
        self._dictation_row.set_subtitle(text or "")

        self._keyboard_row.set_visible(app.problem == KEYBOARD_PROBLEM)

    def _show_recent(self) -> None:
        for row in self._recent_rows:
            self._recent_group.remove(row)
        self._recent_rows = []
        for text in self._app.recent:
            row = Adw.ActionRow(title=text, title_lines=1, use_markup=False)
            row.set_tooltip_text(text)
            row.add_suffix(_copy_button(lambda text=text: self._copy(text)))
            self._recent_group.add(row)
            self._recent_rows.append(row)
        self._recent_group.set_visible(bool(self._recent_rows))

    def _key_selected(self, row, _pspec) -> None:
        key = self._keys[row.get_selected()]
        if key != self._app.config.key:
            changes = {"key": key}
            if key == self._app.config.hands_free_key:
                changes["hands_free_key"] = ""
            self._app.change_settings(**changes)

    def _hands_free_selected(self, row, _pspec) -> None:
        key = self._hands_free_keys[row.get_selected()]
        if key != self._app.config.hands_free_key:
            self._app.change_settings(hands_free_key=key)

    def _language_selected(self, row, _pspec) -> None:
        self._app.change_settings(language=self._languages[row.get_selected()])

    def _server_applied(self, row) -> None:
        self._app.change_settings(url=row.get_text().strip().rstrip("/"))
        self._check_server()

    def _api_key_applied(self, row) -> None:
        self._app.change_settings(api_key=row.get_text())
        self._check_server()

    def _check_server(self) -> None:
        config = self._app.config
        self._show_server("checking")

        def check():
            status = server_status(config)
            GLib.idle_add(self._show_server, status, config)

        threading.Thread(target=check, daemon=True).start()

    def _show_server(self, status: str, config=None) -> bool:
        # A slow answer about settings the user has since replaced is stale.
        if config is None or (config.url, config.api_key) == (
            self._app.config.url, self._app.config.api_key
        ):
            text, style = SERVER_LOOK[status]
            self._server_label.set_label(text)
            _set_style(self._server_label, style, {style for _, style in SERVER_LOOK.values()})
        return GLib.SOURCE_REMOVE

    def _allow_keyboard(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)

        def done(granted: bool) -> None:
            button.set_sensitive(True)
            if not granted:
                self._toasts.add_toast(Adw.Toast(title="Keyboard access not granted", timeout=3))

        self._app.grant_keyboard_access(done)

    def _copy(self, text: str) -> None:
        self.get_clipboard().set(text)
        self._toasts.add_toast(Adw.Toast(title="Copied", timeout=2))
