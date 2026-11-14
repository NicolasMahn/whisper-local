"""Watch the speech model on a server, and start it when it is off."""

import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from whisper_local.config import Config
from whisper_local.transcribe import (
    LOCAL_HOSTS,
    TranscriptionError,
    auth_headers,
    open_same_origin,
)


class ServerModel:
    """Keep track of one model, like Engine does for the local whisper-server.

    ``on_state`` hears checking, starting, ready, off, failed, unreachable or
    stopped, and can run on a worker thread; ``problem`` then says what to tell
    the user. Starting takes the admin key and local_llm's control API
    (/admin/models). Other servers, or no admin key, only get watched.

    A model is started only at the first look after ``start`` or ``recheck``,
    never while merely watching: someone may have stopped it on purpose. Nor
    does it stop other models to make room, because those are someone's work.
    """

    def __init__(
        self,
        config: Config,
        on_state: Callable[[str], None] = lambda _state: None,
        *,
        poll_interval: float = 5,
    ):
        self._config = config
        self._on_state = on_state
        self._poll_interval = poll_interval
        self._base = config.url.removesuffix("/").removesuffix("/v1")
        self.state = "stopped"
        self.problem = ""
        self._may_start = True
        self._admin_key_refused = False
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        self._set_state("checking")
        threading.Thread(target=self._run, daemon=True, name="server-model").start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._set_state("stopped")

    def recheck(self) -> None:
        """Look again, and start the model if it is off: someone wants to dictate."""
        self._may_start = True
        if self.state != "starting":
            self._set_state("checking")
        self._wake.set()

    def wait_ready(self, timeout: float = 15) -> bool:
        """Wait for the answer to a look at the server, not for the model to start.

        A start takes minutes. Text arriving that late would land in whatever
        window has the focus by then.
        """
        deadline = time.monotonic() + timeout
        while self.state == "checking" and time.monotonic() < deadline:
            if self._stop.wait(0.05):
                break
        return self.state == "ready"

    def _set_state(self, state: str, problem: str = "") -> None:
        changed = (self.state, self.problem) != (state, problem)
        self.state, self.problem = state, problem
        if changed:
            self._on_state(state)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self._observe()
            except (OSError, http.client.HTTPException, TranscriptionError):
                self._report("unreachable", "Speech server unreachable")
            except Exception as error:
                # Dying here would leave every dictation waiting for this thread.
                print(f"whisper-local: {type(error).__name__} checking model", file=sys.stderr)
                self._report("failed", "Could not check the speech model")
            # Once ready there is nothing to poll for; a failed dictation wakes this.
            self._wake.wait(None if self.state == "ready" else self._poll_interval)

    def _report(self, state: str, problem: str = "") -> None:
        # A set _wake means this look is out of date: a new one is about to run.
        if not self._wake.is_set():
            self._set_state(state, problem)

    def _observe(self) -> None:
        may_start, self._may_start = self._may_start, False
        models = self._admin_models()
        name = self._config.model
        entry = next((model for model in models or [] if model.get("name") == name), None)
        # Without a recent reading from its monitor, the server cannot say what runs.
        if models is None or (entry is not None and entry.get("state") == "unknown"):
            self._observe_listing()
        elif entry is None:
            self._report("failed", f"The server has no model {name}")
        elif entry.get("state") == "live":
            self._report("ready")
        elif entry.get("state") == "starting" or _is_starting(entry.get("job")):
            self._report("starting", f"{name} is starting…")
        elif may_start:
            self._start_model()
        elif self.state in ("starting", "checking", "unreachable"):
            reason = entry.get("reason") or (entry.get("failed_job") or {}).get("message")
            if reason:
                self._report("failed", f"{name} failed: {reason}")
            else:
                self._report("off", f"{name} is not running on the speech server")

    def _start_model(self) -> None:
        name = self._config.model
        path = f"/admin/models/{urllib.parse.quote(name, safe='')}/start"
        status, answer = self._admin_request(path, method="POST")
        if status == 202:
            self._report("starting", f"{name} is starting…")
        elif status == 409 and (would_stop := answer.get("error", {}).get("would_stop")):
            self._report("off", f"{name} does not fit. Stop {', '.join(would_stop)} to make room")
        else:
            self._report("failed", f"Could not start {name}")

    def _observe_listing(self) -> None:
        request = urllib.request.Request(
            f"{self._config.url}/models", headers=auth_headers(self._config)
        )
        try:
            with open_same_origin(request, timeout=5) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                self._report("failed", "Speech server rejected the key")
            elif error.code in (404, 405):
                self._report("ready")  # It lists no models; dictating will tell.
            else:
                self._report("unreachable", "Speech server unreachable")
            return
        try:
            names = {model["id"] for model in json.loads(payload)["data"]}
        except (ValueError, KeyError, TypeError):
            self._report("ready")
            return
        if self._config.model in names:
            self._report("ready")
        else:
            hint = "; the admin key was refused" if self._admin_key_refused else ""
            self._report("off", f"{self._config.model} is not running on the speech server{hint}")

    def _admin_models(self) -> list[dict] | None:
        """The server's models, or None when this key or server has no such API."""
        if not self._may_send_admin_key():
            return None
        status, answer = self._admin_request("/admin/models")
        self._admin_key_refused = status in (401, 403)
        models = answer.get("models")
        return models if status == 200 and isinstance(models, list) else None

    def _may_send_admin_key(self) -> bool:
        # Plain HTTP would show the key to everyone on the network.
        parsed = urllib.parse.urlsplit(self._base)
        return bool(self._config.admin_key) and (
            parsed.scheme == "https" or parsed.hostname in LOCAL_HOSTS
        )

    def _admin_request(self, path: str, method: str = "GET") -> tuple[int, dict]:
        request = urllib.request.Request(
            self._base + path,
            method=method,
            headers={"Authorization": f"Bearer {self._config.admin_key}"},
        )
        try:
            with open_same_origin(request, timeout=10) as response:
                return response.status, _json_object(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _json_object(error.read())


def _is_starting(job: dict | None) -> bool:
    return job is not None and job.get("action") in ("start", "restart")


def _json_object(payload: bytes) -> dict:
    try:
        answer = json.loads(payload)
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}
