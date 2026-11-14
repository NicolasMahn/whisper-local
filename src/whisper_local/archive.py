"""Keep every dictation on disk, so the transcription can be measured and tuned later.

Each dictation becomes one folder named after its start time:

    2026-11-15T14:03:22.123456+01:00/
        audio.wav         what the recorder captured
        screenshot.png    the window dictated into, when one was sent
        dictation.json    the settings, the model's answers and what was typed
"""

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ARCHIVE_PATH = (
    Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "whisper-local"
    / "dictations"
)


@dataclass
class Entry:
    """One dictation, filled in as it is transcribed."""

    started: datetime
    wav: bytes
    engine: str
    model: str
    language: str
    vocabulary: list[str]
    screenshot: bytes | None = None
    silent: bool = False
    # The model's answer to the audio alone, and its answer with the screenshot.
    heard: str | None = None
    spelled: str | None = None
    text: str | None = None
    error: str | None = None
    transcription_seconds: float | None = None
    audio_seconds: float = field(init=False)

    def __post_init__(self):
        # Recorder's format: 16 kHz, mono, 16 bits, after a 44-byte header.
        self.audio_seconds = round(max(0, len(self.wav) - 44) / 32_000, 3)

    def to_json(self) -> dict:
        return {
            "started": self.started.isoformat(),
            "audio_seconds": self.audio_seconds,
            "transcription_seconds": self.transcription_seconds,
            "engine": self.engine,
            "model": self.model,
            "language": self.language,
            "vocabulary": self.vocabulary,
            "screenshot": self.screenshot is not None,
            "silent": self.silent,
            "heard": self.heard,
            "spelled": self.spelled,
            "text": self.text,
            "error": self.error,
        }


def keep(entry: Entry, root: Path = ARCHIVE_PATH) -> Path:
    """Write `entry` to its own folder under `root` and return that folder.

    The folder is written under a hidden name and renamed at the end, so a
    crash never leaves a dictation that looks complete but is not.
    """
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = entry.started.isoformat()
    partial = root / f".{name}"
    partial.mkdir(mode=0o700)
    try:
        (partial / "audio.wav").write_bytes(entry.wav)
        if entry.screenshot is not None:
            (partial / "screenshot.png").write_bytes(entry.screenshot)
        (partial / "dictation.json").write_text(
            json.dumps(entry.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        folder = root / name
        partial.rename(folder)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return folder


def keep_quietly(entry: Entry, root: Path = ARCHIVE_PATH) -> None:
    """`keep`, for a background thread: a full disk must not break dictation."""
    try:
        keep(entry, root)
    except OSError:
        print("whisper-local: could not keep a dictation", file=sys.stderr)
