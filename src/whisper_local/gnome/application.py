import os
import subprocess
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime

import sounddevice as sd
from gi.repository import Adw, Gio, GLib, GObject

from whisper_local import archive, cues, linux
from whisper_local import config as config_module
from whisper_local.app import Dictation, Outcome, start_worker
from whisper_local.engine import MODEL_NAME, Engine, installed_paths
from whisper_local.platform import KEYS
from whisper_local.recorder import Recorder, is_silent
from whisper_local.screenshots import PendingScreenshot, Screenshots
from whisper_local.server_model import ServerModel
from whisper_local.transcribe import (
    ChatAnswer,
    ChatTranscript,
    TranscriptionError,
    transcribe,
    transcribe_chat,
)

from .service import Service
from .window import KEYBOARD_PROBLEM, Window

APP_ID = "io.github.nicolasmahn.WhisperLocal"
PROBLEM_SECONDS = 5


def _rounded(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds, 3)


def _on_main_loop(function, *args) -> None:
    """Run `function` on the GTK main loop; dictation calls in from its own threads."""

    def run():
        function(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(run)


@dataclass
class _Recording:
    """What a recording carries from its start to after its paste."""

    started: datetime
    screenshot: PendingScreenshot | None = None
    # Filled in during transcription when the dictation is to be kept.
    entry: archive.Entry | None = None


class _ModelUnavailable(Exception):
    """The server's model cannot take this dictation; says why."""


def _reason(error: Exception) -> str:
    """A short explanation safe to show in the UI and the journal."""
    if isinstance(error, _ModelUnavailable):
        return str(error)
    if isinstance(error, TranscriptionError):
        status = error.status_code
        if status == 503:
            return "Speech model not available"
        if status in (401, 403):
            return "Speech server rejected the key"
        if status is not None:
            return "Speech server failed"
        if error.category == "ran on":
            return "Speech model's answer ran too long"
        if error.category == "unexpected response":
            return "Speech server sent an unexpected response"
        if error.category == "redirect rejected":
            return "Speech server redirected outside its address"
        return "Speech server unreachable"
    if isinstance(error, sd.PortAudioError):
        return "Microphone unavailable"
    if isinstance(error, (OSError, subprocess.CalledProcessError)):
        return "Could not paste the text"
    return "Dictation failed"


class Application(Adw.Application):
    """Owns the dictation and its state, which the window and D-Bus show.

    The state lives in GObject properties so both views follow it through
    "notify" and nothing has to push updates to them.
    """

    state = GObject.Property(type=str, default="idle")
    enabled = GObject.Property(type=bool, default=True)
    key = GObject.Property(type=str, default="")
    hands_free_key = GObject.Property(type=str, default="")
    recent = GObject.Property(type=GObject.TYPE_STRV)
    problem = GObject.Property(type=str, default="")
    engine_state = GObject.Property(type=str, default="stopped")

    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.add_main_option(
            "background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Start without opening the window", None,
        )
        self._background = False
        self._service: Service | None = None
        self.recent = []
        self._last_level_at = float("-inf")
        # A problem that stops dictation outright stays; failures of single
        # dictations show over it for a few seconds.
        self._lasting_problem = ""
        self._engine_problem = ""
        self._engine: Engine | ServerModel | None = None
        self._screenshots = Screenshots()
        # Writing to disk must not hold up the paste.
        self._archive_writer = ThreadPoolExecutor(max_workers=1)
        self._archive_root = archive.ARCHIVE_PATH
        # The version of the code this process loaded, not of a later checkout.
        self._archive_writer.submit(archive.version)
        self._listening = False
        self._problem_timeout = 0
        try:
            self.config = config_module.load()
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
            print("whisper-local: settings file unreadable", file=sys.stderr)
            self.config = config_module.Config()
            self._lasting_problem = "Settings file unreadable"
        self.key = self.config.key
        self.hands_free_key = self.config.hands_free_key
        self.problem = self._lasting_problem
        self.connect("notify::enabled", self._enabled_changed)

    def do_handle_local_options(self, options):
        if options.contains("background"):
            self.register(None)
            if self.get_is_remote():
                return 0  # already running; there is nothing to show
            self._background = True
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Closing the window leaves dictation running; only Quit ends it.
        self.hold()
        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("window.close", ["<Control>w"])
        self._prepare_paste()
        self._configure_engine()
        self._start_dictation()

    def do_shutdown(self):
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.stop()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        if self._background:
            self._background = False
            return
        window = self.get_active_window() or Window(self)
        window.present()

    def do_dbus_register(self, connection, object_path):
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        self._service = Service(self, connection)
        return True

    def do_dbus_unregister(self, connection, object_path):
        if self._service is not None:
            self._service.close()
            self._service = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def change_settings(self, **changes) -> None:
        """Save changed settings and use them from the next dictation on."""
        previous = self.config
        self.config = config_module.save(changes)
        if "key" in changes or "hands_free_key" in changes:
            self._restart()
        elif self.config.engine != previous.engine or (
            self.config.engine == "builtin" and self.config.language != previous.language
        ) or (
            self.config.engine == "server" and any(
                getattr(self.config, name) != getattr(previous, name)
                for name in ("url", "api_key", "admin_key", "model")
            )
        ):
            self._configure_engine()

    def _restart(self) -> None:
        # The keyboard listener blocks in a thread that cannot be stopped, so
        # a new key takes a fresh process. It shows the window again.
        if self._engine is not None:
            self._engine.stop()
        os.execv(sys.executable, [sys.executable, "-m", "whisper_local"])

    def _configure_engine(self) -> None:
        previous, self._engine = self._engine, None
        if previous is not None:
            previous.stop()
        self._set_engine_problem("")
        def on_state(state: str) -> None:
            _on_main_loop(self._engine_changed, engine, state)

        if self.config.engine == "server":
            engine = ServerModel(self.config, on_state)
        else:
            binary, model = installed_paths()
            engine = Engine(binary, model, self.config.language, on_state)
        self._engine = engine
        engine.start()

    def _engine_changed(self, engine: Engine | ServerModel, state: str) -> None:
        if engine is not self._engine:
            return
        self._update("engine_state", state)
        if isinstance(engine, ServerModel):
            problem = engine.problem
        else:
            problem = {
                "starting": "Speech engine starting…",
                "not_installed": "Speech engine not installed — run install.sh",
                "failed": "Speech engine stopped — check the journal",
            }.get(state, "")
        self._set_engine_problem(problem)

    def _start_dictation(self) -> None:
        self._dictation = Dictation(
            Recorder(on_level=lambda level: _on_main_loop(self._emit_level, level)),
            self._transcribe,
            linux.insert_text,
            capture=self._capture,
            finished=self._finished,
            paste=start_worker(),
            cue=self._cue,
            hands_free_key=self.config.hands_free_key,
            on_state=lambda state: _on_main_loop(self._update, "state", state),
            on_error=lambda error: _on_main_loop(self._failed, error),
        )
        self._dictation.enabled = self.enabled
        if self.config.key not in KEYS:
            self._set_lasting_problem("Choose a dictation key")
            return
        if self.config.hands_free_key and (
            self.config.hands_free_key not in KEYS
            or self.config.hands_free_key == self.config.key
        ):
            self._set_lasting_problem("Choose a hands-free key")
            return
        self._listening = True
        threading.Thread(target=self._listen, daemon=True).start()

    def grant_keyboard_access(self, on_done) -> None:
        """Ask for the password off the main loop; on success, listen again at once."""

        def grant():
            granted = linux.grant_keyboard_access()
            _on_main_loop(finish, granted)

        def finish(granted: bool):
            if granted:
                self._set_lasting_problem("")
                self._prepare_paste()
                if not self._listening:
                    self._listening = True
                    threading.Thread(target=self._listen, daemon=True).start()
            on_done(granted)

        threading.Thread(target=grant, daemon=True).start()

    def _prepare_paste(self) -> None:
        try:
            linux.prepare_virtual_keyboard()
        except PermissionError:
            self._set_lasting_problem(KEYBOARD_PROBLEM)
        except Exception:
            print("whisper-local: virtual keyboard unavailable", file=sys.stderr)

    def _listen(self) -> None:
        try:
            linux.listen(self.config.key, self._dictation, self.config.hands_free_key or None)
        except PermissionError:
            _on_main_loop(self._set_lasting_problem, KEYBOARD_PROBLEM)
        except Exception:
            print("whisper-local: keyboard listener stopped", file=sys.stderr)
            _on_main_loop(self._set_lasting_problem, "Keyboard listening stopped")
        finally:
            _on_main_loop(setattr, self, "_listening", False)

    def _capture(self) -> _Recording:
        recording = _Recording(started=datetime.now().astimezone())
        if self.config.engine == "server" and self.config.screenshots:
            # The focused window now is the one dictated into.
            request_id, recording.screenshot = self._screenshots.ask()
            _on_main_loop(self._request_screenshot, request_id)
        return recording

    def _request_screenshot(self, request_id: int) -> None:
        if self._service is not None:
            self._service.request_screenshot(request_id)

    def screenshot_delivered(self, request_id: int, png: bytes) -> None:
        self._screenshots.deliver(request_id, png)

    def _transcribe(self, wav: bytes, recording: _Recording | None = None) -> str:
        # Reads self.config per call so changed settings apply without a restart.
        settings = self.config
        recording = recording or _Recording(started=datetime.now().astimezone())
        entry = archive.Entry(
            recorded_at=recording.started,
            wav=wav,
            settings=settings,
            model=settings.model if settings.engine == "server" else MODEL_NAME,
        )
        if settings.keep_dictations:
            recording.entry = entry
        began = time.monotonic()
        try:
            entry.text = self._transcribe_entry(entry, settings, recording.screenshot)
        except Exception as error:
            entry.error = _reason(error)
            raise
        finally:
            entry.seconds["transcription"] = round(time.monotonic() - began, 3)
        if entry.text:
            _on_main_loop(self._remember, entry.text)
        return entry.text

    def _finished(self, recording: _Recording, outcome: Outcome) -> None:
        """Keep the dictation, now that nothing waits for it any more."""
        entry = recording.entry
        if entry is None:
            return
        entry.pasted = outcome.pasted
        if outcome.error is not None and entry.error is None:
            entry.error = _reason(outcome.error)
        entry.seconds["queued"] = _rounded(outcome.queued)
        entry.seconds["key_release_wait"] = _rounded(outcome.key_release_wait)
        entry.seconds["paste"] = _rounded(outcome.paste)
        self._archive_writer.submit(archive.keep_quietly, entry, self._archive_root)

    def _transcribe_entry(
        self,
        entry: archive.Entry,
        settings: config_module.Config,
        screenshot: PendingScreenshot | None,
    ) -> str:
        wav = entry.wav
        if is_silent(wav):
            entry.silent = True
            return ""
        if settings.engine == "server":
            transcript = self._transcribe_on_server(wav, screenshot, entry)
            entry.heard, entry.spelled = transcript.heard, transcript.spelled
            return transcript.text
        engine = self._engine
        # The engine changes just after the settings when they are switched.
        if (
            engine is None
            or isinstance(engine, ServerModel)
            or not engine.wait_ready()
            or engine.url is None
        ):
            raise TranscriptionError("unreachable")
        settings = replace(settings, url=engine.url, api_key="", model="whisper")
        began = time.monotonic()
        text = transcribe(wav, settings)
        entry.heard = ChatAnswer(text, seconds=_rounded(time.monotonic() - began))
        return text

    def _transcribe_on_server(
        self, wav: bytes, pending: PendingScreenshot | None, entry: archive.Entry
    ) -> ChatTranscript:
        model = self._engine if isinstance(self._engine, ServerModel) else None
        if model is not None and model.state != "ready":
            model.recheck()
            if not model.wait_ready():
                raise _ModelUnavailable(model.problem or "Speech model not available")

        def screenshot() -> bytes | None:
            began = time.monotonic()
            png = pending.wait()
            entry.seconds["screenshot_wait"] = _rounded(time.monotonic() - began)
            # The setting may have been turned off during the wait.
            if not self.config.screenshots:
                return None
            entry.screenshot = png
            return png

        settings = self.config
        try:
            return transcribe_chat(
                wav, settings, screenshot if pending is not None and settings.screenshots else None
            )
        except TranscriptionError as error:
            # The model may have gone away since it was last seen.
            if model is not None and (
                error.status_code in (502, 503, 504) or error.category == "unreachable"
            ):
                model.recheck()
            raise

    def _remember(self, text: str) -> None:
        self.recent = [text, *self.recent[:4]]

    def _emit_level(self, level: float) -> None:
        if self.state not in ("listening", "hands_free"):
            return
        now = time.monotonic()
        if now - self._last_level_at < 1 / 25:
            return
        self._last_level_at = now
        if self._service is not None:
            self._service.emit_level(level)

    def _cue(self, name: str) -> None:
        if self.config.sounds:
            cues.play(name)

    def _update(self, name: str, value) -> None:
        if self.get_property(name) != value:
            self.set_property(name, value)

    def _enabled_changed(self, *_) -> None:
        self._dictation.enabled = self.enabled

    def _set_lasting_problem(self, problem: str) -> None:
        self._lasting_problem = problem
        if not self._problem_timeout:
            self._update("problem", self._lasting_problem or self._engine_problem)

    def _set_engine_problem(self, problem: str) -> None:
        self._engine_problem = problem
        if not self._problem_timeout:
            self._update("problem", self._lasting_problem or self._engine_problem)

    def _failed(self, error: Exception) -> None:
        print(f"whisper-local: {_reason(error)}", file=sys.stderr)
        if self._problem_timeout:
            GLib.source_remove(self._problem_timeout)
        self._update("problem", _reason(error))
        self._problem_timeout = GLib.timeout_add_seconds(PROBLEM_SECONDS, self._clear_problem)

    def _clear_problem(self) -> bool:
        self._problem_timeout = 0
        self._update("problem", self._lasting_problem or self._engine_problem)
        return GLib.SOURCE_REMOVE


def main() -> int:
    return Application().run(sys.argv)
