import io
import json
import urllib.error

import pytest

from whisper_local import server_model
from whisper_local.config import Config
from whisper_local.server_model import ServerModel

LOCAL = "http://localhost:8400/v1"


class FakeServer:
    """Answers by "METHOD path"; records what was asked and with which key."""

    def __init__(self, monkeypatch, answers):
        self.answers = answers
        self.requests = []
        monkeypatch.setattr(server_model, "open_same_origin", self.open)

    def open(self, request, timeout):
        route = f"{request.get_method()} {request.full_url.split('//')[1].split('/', 1)[1]}"
        self.requests.append((route, request.get_header("Authorization")))
        status, answer = self.answers[route]
        body = json.dumps(answer).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "", {}, io.BytesIO(body))
        response = io.BytesIO(body)
        response.status = status
        return response

    def routes(self):
        return [route for route, _key in self.requests]


def admin_models(state, **extra):
    return 200, {"models": [{"name": "omni", "state": state, "job": None, **extra}]}


def observed(config):
    model = ServerModel(config)
    model._observe()
    return model


def test_model_that_is_off_gets_started_once_without_stopping_others(monkeypatch):
    server = FakeServer(monkeypatch, {
        "GET admin/models": admin_models("off"),
        "POST admin/models/omni/start": (202, {"model": "omni"}),
    })
    model = observed(Config(url=LOCAL, admin_key="admin", model="omni"))

    assert model.state == "starting"
    assert server.requests[-1] == ("POST admin/models/omni/start", "Bearer admin")
    server.answers["GET admin/models"] = admin_models("starting")
    model._observe()
    server.answers["GET admin/models"] = admin_models("live")
    model._observe()

    assert model.state == "ready"
    assert server.routes().count("POST admin/models/omni/start") == 1


def test_model_that_does_not_fit_names_what_would_have_to_stop(monkeypatch):
    server = FakeServer(monkeypatch, {
        "GET admin/models": admin_models("off"),
        "POST admin/models/omni/start": (409, {"error": {"message": "", "would_stop": ["big"]}}),
    })
    model = observed(Config(url=LOCAL, admin_key="admin", model="omni"))

    assert model.state == "off"
    assert "Stop big" in model.problem
    model._observe()
    assert "Stop big" in model.problem
    assert server.routes().count("POST admin/models/omni/start") == 1


def test_model_stopped_by_someone_is_only_started_again_when_someone_dictates(monkeypatch):
    server = FakeServer(monkeypatch, {
        "GET admin/models": admin_models("live"),
        "POST admin/models/omni/start": (202, {}),
    })
    model = observed(Config(url=LOCAL, admin_key="admin", model="omni"))
    server.answers["GET admin/models"] = admin_models("off")

    model._observe()
    assert "POST admin/models/omni/start" not in server.routes()

    model.recheck()
    model._wake.clear()
    model._observe()
    assert "POST admin/models/omni/start" in server.routes()


def test_dictating_during_a_start_that_fails_does_not_start_it_again(monkeypatch):
    server = FakeServer(monkeypatch, {
        "GET admin/models": admin_models("off"),
        "POST admin/models/omni/start": (202, {}),
    })
    model = observed(Config(url=LOCAL, admin_key="admin", model="omni"))
    server.answers["GET admin/models"] = admin_models("starting")

    model.recheck()
    model._wake.clear()
    model._observe()
    server.answers["GET admin/models"] = admin_models("off", failed_job={"message": "no weights"})
    model._observe()

    assert model.state == "failed"
    assert "no weights" in model.problem
    assert server.routes().count("POST admin/models/omni/start") == 1


def test_server_down_at_login_does_not_start_the_model_hours_later(monkeypatch):
    server = FakeServer(monkeypatch, {"GET admin/models": admin_models("off")})
    model = ServerModel(Config(url=LOCAL, admin_key="admin", model="omni"))

    def down(request, timeout):
        raise OSError("down")

    monkeypatch.setattr(server_model, "open_same_origin", down)
    with pytest.raises(OSError):
        model._observe()
    monkeypatch.setattr(server_model, "open_same_origin", server.open)
    model._observe()

    assert "POST admin/models/omni/start" not in server.routes()


def test_look_overtaken_by_a_dictation_does_not_report(monkeypatch):
    FakeServer(monkeypatch, {"GET v1/models": (200, {"data": []})})
    model = ServerModel(Config(url=LOCAL, model="omni"))
    model.recheck()

    model._observe()

    assert model.state == "checking"


def test_worker_survives_a_url_it_cannot_open(monkeypatch):
    states = []
    model = ServerModel(
        Config(url="speech.test/v1", model="omni"),
        lambda state: states.append(state),
        poll_interval=0.01,
    )

    model.start()
    assert model.wait_ready(timeout=2) is False
    model.stop()

    assert "failed" in states
    assert model.state == "stopped"


def test_model_being_stopped_is_not_called_starting(monkeypatch):
    FakeServer(monkeypatch, {
        "GET admin/models": admin_models("live", job={"action": "stop"}),
    })

    assert observed(Config(url=LOCAL, admin_key="admin", model="omni")).state == "ready"


def test_without_the_monitor_the_list_decides(monkeypatch):
    server = FakeServer(monkeypatch, {
        "GET admin/models": admin_models("unknown"),
        "GET v1/models": (200, {"data": [{"id": "omni"}]}),
    })

    model = observed(Config(url=LOCAL, admin_key="admin", model="omni"))

    assert model.state == "ready"
    assert "POST admin/models/omni/start" not in server.routes()


@pytest.mark.parametrize("status, state", [(502, "unreachable"), (503, "unreachable"), (404, "ready")])
def test_list_that_fails_is_not_taken_for_a_ready_model(status, state, monkeypatch):
    FakeServer(monkeypatch, {"GET v1/models": (status, {})})

    assert observed(Config(url=LOCAL, model="omni")).state == state


def test_refused_admin_key_says_so_but_a_running_model_still_works(monkeypatch):
    server = FakeServer(monkeypatch, {
        "GET admin/models": (403, {}),
        "GET v1/models": (200, {"data": [{"id": "omni"}]}),
    })
    config = Config(url=LOCAL, api_key="key", admin_key="wrong", model="omni")

    assert observed(config).state == "ready"
    server.answers["GET v1/models"] = (200, {"data": []})
    assert "admin key was refused" in observed(config).problem


@pytest.mark.parametrize("url", ["http://192.168.1.5:8400/v1", "http://fedora.local:8400/v1"])
def test_admin_key_never_crosses_the_network_in_plain_http(url, monkeypatch):
    server = FakeServer(monkeypatch, {"GET v1/models": (200, {"data": [{"id": "omni"}]})})

    model = observed(Config(url=url, api_key="key", admin_key="admin", model="omni"))

    assert model.state == "ready"
    assert server.requests == [("GET v1/models", "Bearer key")]


def test_admin_key_goes_over_https(monkeypatch):
    server = FakeServer(monkeypatch, {"GET admin/models": admin_models("live")})

    observed(Config(url="https://llm.test/v1", admin_key="admin", model="omni"))

    assert server.requests == [("GET admin/models", "Bearer admin")]


def test_without_admin_key_a_stopped_model_is_reported_and_left_alone(monkeypatch):
    server = FakeServer(monkeypatch, {"GET v1/models": (200, {"data": [{"id": "other"}]})})

    model = observed(Config(url=LOCAL, api_key="key", model="omni"))

    assert model.state == "off"
    assert server.routes() == ["GET v1/models"]


def test_server_without_control_api_falls_back_to_its_list(monkeypatch):
    FakeServer(monkeypatch, {
        "GET admin/models": (404, {}),
        "GET v1/models": (200, {"data": [{"id": "omni"}]}),
    })

    assert observed(Config(url=LOCAL, admin_key="admin", model="omni")).state == "ready"


def test_rejected_key_is_reported(monkeypatch):
    FakeServer(monkeypatch, {"GET v1/models": (401, {})})

    model = observed(Config(url=LOCAL, api_key="wrong", model="omni"))

    assert model.state == "failed"
    assert model.problem == "Speech server rejected the key"


@pytest.mark.parametrize("state", ["off", "failed", "unreachable", "starting"])
def test_waiting_gives_up_at_once_on_a_model_that_is_not_there_yet(state):
    # A start takes minutes; text pasted that late could land in the wrong window.
    model = ServerModel(Config())
    model._set_state(state, "Not now")

    assert model.wait_ready(timeout=60) is False
