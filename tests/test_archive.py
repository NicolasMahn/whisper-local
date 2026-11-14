import json
from datetime import datetime, timedelta, timezone

import pytest

from whisper_local import archive, config
from whisper_local.gnome import application as gnome_app
from whisper_local.screenshots import Screenshots
from whisper_local.transcribe import ChatTranscript, TranscriptionError

STARTED = datetime(2026, 11, 15, 14, 3, 22, 123456, timezone(timedelta(hours=1)))


def test_keeps_audio_screenshot_and_record_in_one_private_folder(tmp_path):
    settings = config.Config(engine="server", api_key="secret", admin_key="admin")
    entry = archive.Entry(
        recorded_at=STARTED, wav=bytes(44 + 32_000 * 2), settings=settings, model="omni",
        screenshot=b"png", heard="Ask Jeff", spelled="Ask Jev", text="Ask Jev",
    )

    folder = archive.keep(entry, tmp_path / "dictations")

    assert folder.name == "2026-11-15T14:03:22.123456+01:00"
    assert (folder.parent.stat().st_mode & 0o777) == 0o700
    assert (folder / "audio.wav").read_bytes() == entry.wav
    assert (folder / "screenshot.png").read_bytes() == b"png"
    text = (folder / "dictation.json").read_text()
    assert "secret" not in text and "admin" not in text
    record = json.loads(text)
    assert record["format"] == archive.FORMAT
    assert record["version"] == archive.version()
    assert record["settings"]["model"] == "omni"
    assert record["settings"]["screenshots"] is True
    assert record["audio_seconds"] == 2.0
    assert (record["heard"], record["spelled"], record["text"]) == ("Ask Jeff", "Ask Jev", "Ask Jev")


def test_version_names_the_commit_of_a_checkout():
    found = archive.version()

    assert found["whisper_local"]
    assert found["commit"] is None or len(found["commit"]) == 12


def server_app(monkeypatch, tmp_path, transcribe_chat, **settings):
    monkeypatch.setattr(gnome_app.config_module, "load", lambda: config.Config(
        engine="server", **settings,
    ))
    monkeypatch.setattr(gnome_app, "is_silent", lambda wav: False)
    monkeypatch.setattr(gnome_app, "transcribe_chat", transcribe_chat)
    app = gnome_app.Application()
    app._archive_root = tmp_path
    return app


def recording(png: bytes | None = None):
    screenshots = Screenshots()
    request_id, pending = screenshots.ask()
    if png is not None:
        screenshots.deliver(request_id, png)
    return gnome_app._Recording(STARTED, pending if png is not None else None)


def kept(app, tmp_path) -> list[dict]:
    app._archive_writer.shutdown(wait=True)
    return [
        json.loads((folder / "dictation.json").read_text())
        for folder in sorted(tmp_path.iterdir())
    ]


def test_dictation_is_kept_only_after_its_paste(monkeypatch, tmp_path):
    app = server_app(
        monkeypatch, tmp_path,
        lambda wav, settings, png: ChatTranscript(
            "Ask Jev", "Ask Jeff", "Ask Jev", heard_seconds=1.5, spelled_seconds=1.7,
        ),
        keep_dictations=True,
    )
    started = recording(b"png")

    assert app._transcribe(b"wav", started) == "Ask Jev"
    assert list(tmp_path.iterdir()) == []
    app._finished(started, 0.25, 0.05)

    [record] = kept(app, tmp_path)
    assert (record["heard"], record["spelled"], record["text"]) == ("Ask Jeff", "Ask Jev", "Ask Jev")
    assert record["screenshot_sent"] is True
    assert record["seconds"]["heard"] == 1.5
    assert record["seconds"]["spelled"] == 1.7
    assert (record["seconds"]["key_release_wait"], record["seconds"]["paste"]) == (0.25, 0.05)
    [folder] = tmp_path.iterdir()
    assert (folder / "screenshot.png").read_bytes() == b"png"


def test_failed_dictation_is_kept_with_its_reason(monkeypatch, tmp_path):
    def fail(wav, settings, png):
        raise TranscriptionError("unreachable")

    app = server_app(monkeypatch, tmp_path, fail, keep_dictations=True)
    started = recording()

    with pytest.raises(TranscriptionError):
        app._transcribe(b"wav", started)
    app._finished(started, 0, 0)

    [record] = kept(app, tmp_path)
    assert record["error"] == "Speech server unreachable"
    assert record["text"] is None


def test_nothing_is_kept_unless_asked(monkeypatch, tmp_path):
    app = server_app(monkeypatch, tmp_path, lambda wav, settings, png: ChatTranscript("hi", "hi"))
    started = recording(b"png")

    app._transcribe(b"wav", started)
    app._finished(started, 0, 0)

    assert kept(app, tmp_path) == []
