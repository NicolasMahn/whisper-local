"""Manage the whisper.cpp server owned by the GNOME application."""

import ctypes
import http.client
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

MODEL_NAME = "ggml-large-v3-turbo-q8_0.bin"


def installed_paths() -> tuple[Path, Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    root = data_home / "whisper-local"
    return root / "whisper.cpp/whisper-server", root / "models" / MODEL_NAME


def _die_with_parent(parent_pid: int) -> None:
    # A detached child must not keep the microphone service alive after a crash.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        os._exit(1)


class Engine:
    """Start, monitor, and stop one local whisper-server process.

    ``on_state`` receives starting, ready, not_installed, failed, or stopped.
    It can run on a worker thread. ``url`` is available after ``start`` and
    points to the local OpenAI-compatible /v1 endpoint.
    """

    def __init__(
        self,
        binary: Path,
        model: Path,
        language: str = "",
        on_state: Callable[[str], None] = lambda _state: None,
        *,
        ready_timeout: float = 60,
        poll_interval: float = 0.2,
        restart_delay: float = 1,
        max_restarts: int = 3,
        quick_failure_seconds: float = 30,
    ):
        self.binary = Path(binary)
        self.model = Path(model)
        self.language = language or "auto"
        self.on_state = on_state
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts
        self.quick_failure_seconds = quick_failure_seconds
        self.state = "stopped"
        self.url: str | None = None
        self._port: int | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        if not self.binary.is_file() or not self.model.is_file():
            self._set_state("not_installed")
            return
        try:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                self._port = reservation.getsockname()[1]
        except OSError:
            self._set_state("failed")
            return
        self.url = f"http://127.0.0.1:{self._port}/v1"
        self._stop = threading.Event()
        self._set_state("starting")
        self._worker = threading.Thread(target=self._run, daemon=True, name="whisper-engine")
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None:
            self._terminate(process)
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=5)
        self._set_state("stopped")

    def wait_ready(self, timeout: float = 60) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state == "ready":
                return True
            if self.state in ("not_installed", "failed", "stopped"):
                return False
            if self._stop.wait(min(self.poll_interval, max(0, deadline - time.monotonic()))):
                return False
        return self.state == "ready"

    def _set_state(self, state: str) -> None:
        if self.state != state:
            self.state = state
            self.on_state(state)

    def _run(self) -> None:
        quick_restarts = 0
        while not self._stop.is_set():
            started_at = time.monotonic()
            parent_pid = os.getpid()
            try:
                process = subprocess.Popen(
                    [
                        str(self.binary), "-m", str(self.model),
                        "--host", "127.0.0.1", "--port", str(self._port),
                        "--inference-path", "/v1/audio/transcriptions",
                        "-l", self.language, "-nt",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=sys.stderr,
                    stderr=sys.stderr,
                    preexec_fn=lambda: _die_with_parent(parent_pid),
                )
            except OSError:
                self._set_state("failed")
                return
            with self._lock:
                self._process = process
            if self._stop.is_set():
                self._terminate(process)
                break

            deadline = time.monotonic() + self.ready_timeout
            while not self._stop.is_set() and process.poll() is None:
                if self._healthy() and not self._stop.is_set():
                    self._set_state("ready")
                    break
                if time.monotonic() >= deadline:
                    self._terminate(process)
                    break
                self._stop.wait(self.poll_interval)

            while not self._stop.is_set() and process.poll() is None and self.state == "ready":
                self._stop.wait(self.poll_interval)

            if self._stop.is_set():
                self._terminate(process)
                break
            process.wait()
            with self._lock:
                self._process = None
            if time.monotonic() - started_at >= self.quick_failure_seconds:
                quick_restarts = 0
            quick_restarts += 1
            if quick_restarts > self.max_restarts:
                self._set_state("failed")
                return
            self._set_state("starting")
            if self._stop.wait(self.restart_delay * 2 ** (quick_restarts - 1)):
                break
        with self._lock:
            self._process = None

    def _healthy(self) -> bool:
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=0.5)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            response.read()
            return 200 <= response.status < 400
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
