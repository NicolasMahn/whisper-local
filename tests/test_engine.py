import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

import whisper_local.engine as engine_module
from whisper_local.engine import Engine, installed_paths
from whisper_local.config import Config
from whisper_local.transcribe import transcribe


FAKE_SERVER = '''#!/usr/bin/env python3
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_ENGINE_LOG"]).open("a") as log:
    log.write(json.dumps({"pid": os.getpid(), "at": time.monotonic(), "args": args}) + "\\n")
time.sleep(float(os.environ.get("FAKE_ENGINE_DELAY", "0")))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/crash":
            os._exit(7)
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        with Path(os.environ["FAKE_ENGINE_REQUEST"]).open("wb") as request:
            request.write(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"text": " fake transcription\\\\n"}')

    def log_message(self, *args):
        pass

HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), Handler).serve_forever()
'''


def _until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _starts(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    binary = tmp_path / "whisper-server"
    binary.write_text(FAKE_SERVER)
    binary.chmod(0o755)
    model = tmp_path / "ggml-large-v3-turbo-q8_0.bin"
    model.write_bytes(b"fake model")
    log = tmp_path / "starts.jsonl"
    request = tmp_path / "request.bin"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    monkeypatch.setenv("FAKE_ENGINE_REQUEST", str(request))
    return binary, model, log, request


@pytest.fixture
def loopback_available():
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
    except PermissionError:
        pytest.skip("loopback sockets are blocked in this sandbox")


def test_installed_paths_obey_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    binary, model = installed_paths()
    assert binary == tmp_path / "whisper-local/whisper.cpp/whisper-server"
    assert model == tmp_path / "whisper-local/models/ggml-large-v3-turbo-q8_0.bin"


def test_port_failure_reports_problem_instead_of_crashing(fake_install, monkeypatch):
    binary, model, _, _ = fake_install
    monkeypatch.setattr(engine_module.socket, "socket", lambda: (_ for _ in ()).throw(PermissionError()))
    states = []
    engine = Engine(binary, model, on_state=states.append)

    engine.start()

    assert states == ["failed"]
    assert engine.url is None


def test_monitor_restarts_and_stops_process_without_loopback(fake_install, monkeypatch):
    binary, model, _, _ = fake_install

    class Reservation:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 0)

        def getsockname(self):
            return "127.0.0.1", 45678

    class Process:
        def __init__(self, args, **kwargs):
            self.args = args
            self.started_at = time.monotonic()
            self.exit_code = None
            self.exited = threading.Event()
            processes.append(self)

        def poll(self):
            return self.exit_code

        def wait(self, timeout=None):
            if not self.exited.wait(timeout):
                raise subprocess.TimeoutExpired(self.args, timeout)
            return self.exit_code

        def terminate(self):
            self.crash(-15)

        def kill(self):
            self.crash(-9)

        def crash(self, code=7):
            self.exit_code = code
            self.exited.set()

    processes = []
    monkeypatch.setattr(engine_module.socket, "socket", Reservation)
    monkeypatch.setattr(engine_module.subprocess, "Popen", Process)
    monkeypatch.setattr(Engine, "_healthy", lambda self: True)
    states = []
    engine = Engine(
        binary, model, on_state=states.append, poll_interval=0.01,
        restart_delay=0.05, max_restarts=1,
    )
    try:
        engine.start()
        assert engine.wait_ready(timeout=1)
        processes[0].crash()
        assert _until(lambda: len(processes) == 2)
        assert processes[1].started_at - processes[0].started_at >= 0.05
        assert _until(lambda: engine.state == "ready")
        processes[1].crash()
        assert _until(lambda: engine.state == "failed")
        assert states == ["starting", "ready", "starting", "ready", "failed"]
    finally:
        engine.stop()
    assert all(process.poll() is not None for process in processes)


def test_engine_starts_waits_for_http_and_stops(fake_install, monkeypatch, loopback_available):
    binary, model, log, request = fake_install
    monkeypatch.setenv("FAKE_ENGINE_DELAY", "0.15")
    states = []
    engine = Engine(binary, model, "de", states.append, ready_timeout=2, poll_interval=0.02)
    try:
        engine.start()
        assert engine.state == "starting"
        assert engine.wait_ready(timeout=3)
        assert states[:2] == ["starting", "ready"]
        assert transcribe(
            b"RIFF fake wav", Config(url=engine.url, vocabulary=["Nicolas Mahn"])
        ) == "fake transcription"
        assert b"Nicolas Mahn" in request.read_bytes()
        start = _starts(log)[0]
        assert start["args"] == [
            "-m", str(model), "--host", "127.0.0.1",
            "--port", engine.url.split(":")[-1].split("/")[0],
            "--inference-path", "/v1/audio/transcriptions", "-l", "de", "-nt",
        ]
    finally:
        engine.stop()
    assert engine.state == "stopped"
    assert not _alive(start["pid"])


def test_engine_restarts_crashes_with_backoff_then_reports_failure(fake_install, loopback_available):
    binary, model, log, _ = fake_install
    engine = Engine(
        binary, model, on_state=lambda _state: None,
        ready_timeout=2, poll_interval=0.02, restart_delay=0.15,
        max_restarts=1,
    )
    try:
        engine.start()
        assert engine.wait_ready(timeout=3)
        with pytest.raises((OSError, urllib.error.URLError)):
            urllib.request.urlopen(engine.url.replace("/v1", "") + "/crash", timeout=1)
        assert _until(lambda: len(_starts(log)) == 2)
        starts = _starts(log)
        assert starts[1]["at"] - starts[0]["at"] >= 0.14
        assert _until(lambda: engine.state == "ready")
        with pytest.raises((OSError, urllib.error.URLError)):
            urllib.request.urlopen(engine.url.replace("/v1", "") + "/crash", timeout=1)
        assert _until(lambda: engine.state == "failed")
        assert len(_starts(log)) == 2
    finally:
        engine.stop()
    assert all(not _alive(start["pid"]) for start in starts)


@pytest.mark.parametrize("missing", ["binary", "model"])
def test_missing_files_are_not_started(fake_install, missing):
    binary, model, log, _ = fake_install
    if missing == "binary":
        binary.unlink()
    else:
        model.unlink()
    states = []
    engine = Engine(binary, model, on_state=states.append)
    engine.start()
    assert states == ["not_installed"]
    assert not engine.wait_ready(timeout=0.1)
    assert not log.exists()
    engine.stop()
