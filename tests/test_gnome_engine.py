from dataclasses import replace
from types import SimpleNamespace

from whisper_local import config
from whisper_local.gnome import application as gnome_app
from whisper_local.gnome.window import ENGINE_STATUS, Window


def test_builtin_transcription_uses_local_url_without_server_credentials(monkeypatch):
    settings = config.Config(
        engine="builtin", url="https://remote.test/v1", api_key="private",
        model="remote-model", language="de", vocabulary=["Nicolas Mahn"],
    )
    monkeypatch.setattr(gnome_app.config_module, "load", lambda: settings)
    app = gnome_app.Application()
    app._engine = type("ReadyEngine", (), {
        "url": "http://127.0.0.1:45678/v1",
        "wait_ready": lambda self: True,
    })()
    calls = []
    monkeypatch.setattr(
        gnome_app, "transcribe", lambda wav, chosen: calls.append((wav, chosen)) or ""
    )

    app._transcribe(b"wav")

    wav, chosen = calls[0]
    assert wav == b"wav"
    assert chosen.url == "http://127.0.0.1:45678/v1"
    assert chosen.api_key == ""
    assert chosen.model == "whisper"
    assert chosen.language == "de"
    assert chosen.vocabulary == ["Nicolas Mahn"]
    assert app.config == settings


def test_server_transcription_preserves_model_and_credentials(monkeypatch):
    settings = config.Config(
        engine="server", url="https://remote.test/v1", api_key="private",
        model="remote-model",
    )
    monkeypatch.setattr(gnome_app.config_module, "load", lambda: settings)
    app = gnome_app.Application()
    calls = []
    monkeypatch.setattr(gnome_app, "transcribe", lambda wav, chosen: calls.append(chosen) or "")

    app._transcribe(b"wav")

    assert calls == [settings]


def test_engine_switch_stops_child_and_updates_lasting_problem(monkeypatch):
    monkeypatch.setattr(gnome_app.config_module, "load", config.Config)
    app = gnome_app.Application()
    engines = []

    class FakeEngine:
        def __init__(self, binary, model, language, on_state):
            self.started = False
            self.stopped = False
            engines.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(gnome_app, "Engine", FakeEngine)
    monkeypatch.setattr(gnome_app, "installed_paths", lambda: ("binary", "model"))
    monkeypatch.setattr(
        gnome_app.config_module, "save",
        lambda changes: replace(app.config, **changes),
    )

    app._configure_engine()
    first = engines[0]
    assert first.started
    app._engine_changed(first, "starting")
    assert app.problem == "Speech engine starting…"
    app._engine_changed(first, "not_installed")
    assert app.problem == "Speech engine not installed — run install.sh"
    app._engine_changed(first, "ready")
    assert app.problem == ""

    app.change_settings(engine="server")
    assert first.stopped
    assert app._engine is None
    assert app.engine_state == "server"
    assert app.problem == ""

    app.change_settings(engine="builtin")
    assert len(engines) == 2
    assert engines[1].started
    app._engine_changed(first, "failed")
    assert app.problem == ""
    app._engine_changed(engines[1], "failed")
    assert app.problem == "Speech engine stopped — check the journal"


def test_builtin_engine_status_labels():
    assert {ENGINE_STATUS[state] for state in ("ready", "starting", "not_installed")} == {
        "Ready", "Starting…", "Not installed"
    }


def test_window_hides_server_settings_for_builtin_engine():
    class Row:
        def set_visible(self, visible):
            self.visible = visible

        def set_subtitle(self, subtitle):
            self.subtitle = subtitle

    view = SimpleNamespace(
        _app=SimpleNamespace(config=config.Config(), engine_state="starting"),
        _server_row=Row(), _api_key_row=Row(), _engine_row=Row(),
    )

    Window._show_engine(view)
    assert not view._server_row.visible
    assert not view._api_key_row.visible
    assert view._engine_row.subtitle == "Starting…"

    view._app.engine_state = "ready"
    Window._show_engine(view)
    assert view._engine_row.subtitle == "Ready"

    view._app.config = replace(view._app.config, engine="server")
    Window._show_engine(view)
    assert view._server_row.visible
    assert view._api_key_row.visible
    assert view._engine_row.subtitle == ""
