import json
from datetime import datetime, timedelta, timezone

import pytest

from whisper_local import archive, config
from whisper_local.gnome import application as gnome_app
from whisper_local.screenshots import Screenshots
from whisper_local.transcribe import ChatTranscript, TranscriptionError

STARTED = datetime(2026, 11, 15, 14, 3, 22, 123456, timezone(timedelta(hours=1)))


def entry(**changes) -> archive.Entry:
    values = dict(
        started=STARTED, wav=bytes(44 + 32_000 * 2), engine="server", model="omni",
        language="", vocabulary=["Qwen"],
    )
    return archive.Entry(**{**values, **changes})


def test_keeps_audio_screenshot_and_answers_in_one_private_folder(tmp_path):
    kept = entry(screenshot=b"png", heard="Ask Jeff", spelled="Ask Jev", text="Ask Jev")

    folder = archive.keep(kept, tmp_path / "dictations")

    assert folder.name == "2026-11-15T14:03:22.123456+01:00"
    assert (folder.parent.stat().st_mode & 0o777) == 0o700
    assert (folder / "audio.wav").read_bytes() == kept.wav
    assert (folder / "screenshot.png").read_bytes() == b"png"
    assert json.loads((folder / "dictation.json").read_text()) == {
        "started": "2026-11-15T14:03:22.123456+01:00",
        "audio_seconds": 2.0,
        "transcription_seconds": None,
        "engine": "server",
        "model": "omni",
        "language": "",
        "vocabulary": ["Qwen"],
        "screenshot": True,
        "silent": False,
        "heard": "Ask Jeff",
        "spelled": "Ask Jev",
        "text": "Ask Jev",
        "error": None,
    }


def server_app(monkeypatch, tmp_path, transcribe_chat, **settings):
    monkeypatch.setattr(gnome_app.config_module, "load", lambda: config.Config(
        engine="server", **settings,
    ))
    monkeypatch.setattr(gnome_app, "is_silent", lambda wav: False)
    monkeypatch.setattr(gnome_app, "transcribe_chat", transcribe_chat)
    app = gnome_app.Application()
    app._archive_root = tmp_path
    return app


def screenshot(png: bytes):
    screenshots = Screenshots()
    request_id, pending = screenshots.ask()
    screenshots.deliver(request_id, png)
    return pending


def kept(app, tmp_path) -> list[dict]:
    app._archive_writer.shutdown(wait=True)
    return [
        json.loads((folder / "dictation.json").read_text())
        for folder in sorted(tmp_path.iterdir())
    ]


def test_dictation_is_kept_with_both_answers(monkeypatch, tmp_path):
    app = server_app(
        monkeypatch, tmp_path,
        lambda wav, settings, png: ChatTranscript("Ask Jev", "Ask Jeff", "Ask Jev"),
        keep_dictations=True,
    )

    assert app._transcribe(b"wav", screenshot(b"png")) == "Ask Jev"

    [record] = kept(app, tmp_path)
    assert (record["heard"], record["spelled"], record["text"]) == ("Ask Jeff", "Ask Jev", "Ask Jev")
    assert record["screenshot"] is True
    assert record["error"] is None
    [folder] = tmp_path.iterdir()
    assert (folder / "screenshot.png").read_bytes() == b"png"


def test_failed_dictation_is_kept_with_its_reason(monkeypatch, tmp_path):
    def fail(wav, settings, png):
        raise TranscriptionError("unreachable")

    app = server_app(monkeypatch, tmp_path, fail, keep_dictations=True)

    with pytest.raises(TranscriptionError):
        app._transcribe(b"wav")

    [record] = kept(app, tmp_path)
    assert record["error"] == "Speech server unreachable"
    assert record["text"] is None


def test_nothing_is_kept_unless_asked(monkeypatch, tmp_path):
    app = server_app(monkeypatch, tmp_path, lambda wav, settings, png: ChatTranscript("hi", "hi"))

    app._transcribe(b"wav", screenshot(b"png"))

    assert kept(app, tmp_path) == []
