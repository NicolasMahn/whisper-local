from gi.repository import Gio

from whisper_local import config
from whisper_local.gnome import application as gnome_app
from whisper_local.gnome import service
from whisper_local.gnome.window import Window


def test_dbus_interface_exposes_properties_and_level_signal():
    interface = Gio.DBusNodeInfo.new_for_xml(service._XML).interfaces[0]
    properties = {prop.name: prop.signature for prop in interface.properties}
    signals = {signal.name: [arg.signature for arg in signal.args] for signal in interface.signals}

    assert properties == {
        "State": "s",
        "Enabled": "b",
        "Key": "s",
        "HandsFreeKey": "s",
        "Recent": "as",
        "Problem": "s",
    }
    assert signals == {"Level": ["d"]}


def test_application_exposes_and_listens_for_configured_hands_free_key(monkeypatch):
    monkeypatch.setattr(
        gnome_app.config_module, "load",
        lambda: config.Config(key="right_ctrl", hands_free_key="f13"),
    )
    app = gnome_app.Application()
    calls = []
    app._dictation = object()
    monkeypatch.setattr(gnome_app.linux, "listen", lambda *args: calls.append(args))

    app._listen()

    assert app.get_property("hands-free-key") == "f13"
    assert service._PROPERTIES["HandsFreeKey"] == ("hands-free-key", "s")
    assert calls == [("right_ctrl", app._dictation, "f13")]


def test_recent_transcripts_keep_five_newest_in_memory(monkeypatch):
    monkeypatch.setattr(gnome_app.config_module, "load", config.Config)
    app = gnome_app.Application()

    for index in range(7):
        app._remember(f"text {index}")

    assert list(app.recent) == [f"text {index}" for index in range(6, 1, -1)]


def test_level_emission_is_throttled_and_only_when_recording(monkeypatch):
    monkeypatch.setattr(gnome_app.config_module, "load", config.Config)
    now = [0.0]
    monkeypatch.setattr(gnome_app.time, "monotonic", lambda: now[0])
    app = gnome_app.Application()
    levels = []
    app._service = type("ServiceStub", (), {"emit_level": lambda self, level: levels.append(level)})()

    app._emit_level(0.1)
    app.state = "listening"
    app._emit_level(0.2)
    now[0] = 0.039
    app._emit_level(0.3)
    now[0] = 0.04
    app._emit_level(0.4)
    app.state = "hands_free"
    now[0] = 0.08
    app._emit_level(0.5)
    app.state = "idle"
    now[0] = 0.12
    app._emit_level(0.6)

    assert levels == [0.2, 0.4, 0.5]


def test_known_words_edits_save_current_list_without_restarting(monkeypatch):
    class App:
        config = config.Config(vocabulary=["Qwen"])

        def change_settings(self, **changes):
            self.config = config.Config(vocabulary=changes["vocabulary"])
            saved.append(changes["vocabulary"])

    class Entry:
        text = " Nicolas Mahn "

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

    class View:
        _app = App()

        def _show_known_words(self):
            pass

    saved = []
    view = View()
    entry = Entry()

    Window._known_word_added(view, entry)
    assert saved == [["Qwen", "Nicolas Mahn"]]
    assert entry.text == ""
    Window._remove_known_word(view, 0)
    assert saved[-1] == ["Nicolas Mahn"]


def test_application_transcribes_with_current_vocabulary(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gnome_app, "transcribe", lambda wav, settings: calls.append(settings.vocabulary.copy()) or ""
    )
    app = type("App", (), {"config": config.Config(engine="server", vocabulary=["Qwen"])})()

    gnome_app.Application._transcribe(app, b"wav")
    app.config = config.Config(engine="server", vocabulary=["Nicolas Mahn"])
    gnome_app.Application._transcribe(app, b"wav")

    assert calls == [["Qwen"], ["Nicolas Mahn"]]


def test_failure_log_omits_exception_text(monkeypatch, capsys):
    monkeypatch.setattr(gnome_app.config_module, "load", config.Config)
    app = gnome_app.Application()
    monkeypatch.setattr(gnome_app.GLib, "timeout_add_seconds", lambda *_: 1)
    error = OSError("private transcript")

    app._failed(error)

    assert capsys.readouterr().err == "whisper-local: Could not paste the text\n"
