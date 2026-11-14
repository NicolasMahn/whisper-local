"""Keep every dictation on disk, so the transcription can be measured and tuned later.

Each dictation becomes one folder named after the moment recording started:

    2026-11-15T14:03:22.123456+01:00/
        audio.wav         what the recorder captured
        screenshot.png    the window dictated into, when one was sent
        dictation.json    the version, settings, answers, typed text and timings

`FORMAT` changes whenever dictation.json does; folders without it are format 1.
"""

import functools
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path

from whisper_local.config import Config

FORMAT = 2
ARCHIVE_PATH = (
    Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "whisper-local"
    / "dictations"
)
_REPOSITORY = Path(__file__).resolve().parents[2]


@dataclass
class Entry:
    """One dictation, filled in as it is transcribed and pasted."""

    recorded_at: datetime
    wav: bytes
    settings: Config
    # The model the speech went to: the server's model, or the built-in file.
    model: str
    screenshot: bytes | None = None
    silent: bool = False
    # The model's answer to the audio alone, and its answer with the screenshot.
    heard: str | None = None
    spelled: str | None = None
    spelled_error: str | None = None
    text: str | None = None
    error: str | None = None
    seconds: dict[str, float | None] = field(default_factory=dict)

    @property
    def audio_seconds(self) -> float:
        # Recorder's format: 16 kHz, mono, 16 bits, after a 44-byte header.
        return round(max(0, len(self.wav) - 44) / 32_000, 3)

    def to_json(self) -> dict:
        settings = self.settings
        return {
            "format": FORMAT,
            "recorded_at": self.recorded_at.isoformat(),
            "version": version(),
            # Never the keys: the folder may be shared for tuning.
            "settings": {
                "engine": settings.engine,
                "url": settings.url if settings.engine == "server" else None,
                "model": self.model,
                "screenshots": settings.screenshots,
                "language": settings.language,
                "vocabulary": settings.vocabulary,
            },
            "audio_seconds": self.audio_seconds,
            "screenshot_sent": self.screenshot is not None,
            "silent": self.silent,
            "heard": self.heard,
            "spelled": self.spelled,
            "spelled_error": self.spelled_error,
            "text": self.text,
            "error": self.error,
            "seconds": self.seconds,
        }


@functools.cache
def version() -> dict:
    """The release, and the commit when running from a checkout.

    The commit changes with every change to the code, so each dictation can be
    traced to the prompts and rules that produced it; `modified` means the code
    differed from that commit.
    """
    try:
        release = metadata.version("whisper-local")
    except metadata.PackageNotFoundError:
        release = None
    commit = modified = None
    if (_REPOSITORY / ".git").exists():
        try:
            commit = _git("rev-parse", "--short=12", "HEAD")
            modified = bool(_git("status", "--porcelain", "--untracked-files=no", "--", "src"))
        except (OSError, subprocess.CalledProcessError):
            pass
    return {"whisper_local": release, "commit": commit, "modified": modified}


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(_REPOSITORY), *arguments],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout.strip()


def keep(entry: Entry, root: Path = ARCHIVE_PATH) -> Path:
    """Write `entry` to its own folder under `root` and return that folder.

    The folder is written under a hidden name and renamed at the end, so a
    crash never leaves a dictation that looks complete but is not.
    """
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = entry.recorded_at.isoformat()
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
