import io
import urllib.error
import urllib.request
import urllib.response
from email import policy
from email.message import Message
from email.parser import BytesParser

import pytest

import whisper_local.transcribe as transcribe_module
from whisper_local.config import Config
from whisper_local.transcribe import TranscriptionError, server_status, transcribe


def stub_response(monkeypatch, *, response_body=b'{"text": " recognized words  "}'):
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(response_body)

    monkeypatch.setattr(transcribe_module, "_open", urlopen)
    return requests


def multipart_fields(content_type, body):
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode("ascii")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message.iter_parts()
    }


@pytest.mark.parametrize("language", ["de", ""])
def test_request_sends_auth_model_file_and_optional_language(language, monkeypatch):
    wav = b"RIFF\x00sample wav bytes"
    requests = stub_response(monkeypatch)
    result = transcribe(
        wav,
        Config(
            url="http://speech.test/v1",
            api_key="secret-key",
            model="qwen-test",
            language=language,
        ),
    )

    request, timeout = requests[0]
    fields = multipart_fields(request.get_header("Content-type"), request.data)
    assert request.full_url == "http://speech.test/v1/audio/transcriptions"
    assert request.get_header("Authorization") == "Bearer secret-key"
    assert timeout == 30
    assert fields["model"] == b"qwen-test"
    assert fields["file"] == wav
    assert "prompt" not in fields
    if language:
        assert fields["language"] == language.encode()
    else:
        assert "language" not in fields
    assert result == "recognized words"


def test_empty_key_sends_no_authorization_header(monkeypatch):
    requests = stub_response(monkeypatch)

    assert transcribe(b"wav", Config()) == "recognized words"
    assert server_status(Config()) == "ok"

    transcription_request = requests[0][0]
    status_request = requests[1][0]
    assert transcription_request.full_url == (
        "http://localhost:8000/v1/audio/transcriptions"
    )
    assert status_request.full_url == "http://localhost:8000/v1/models"
    assert transcription_request.get_header("Authorization") is None
    assert status_request.get_header("Authorization") is None


def test_vocabulary_is_sent_as_prompt(monkeypatch):
    requests = stub_response(monkeypatch)

    transcribe(b"wav", Config(vocabulary=["Nicolas Mahn", "Qwen", "Strix Halo"]))

    request = requests[0][0]
    fields = multipart_fields(request.get_header("Content-type"), request.data)
    assert fields["prompt"] == b"Nicolas Mahn, Qwen, Strix Halo"


@pytest.mark.parametrize(
    "destination",
    [
        "http://other.test/v1/audio/transcriptions",
        "http://speech.test:8080/v1/audio/transcriptions",
        "http://speech.test/v1/audio/transcriptions",
    ],
)
def test_redirect_handler_rejects_other_origins_and_https_downgrade(destination):
    original = urllib.request.Request(
        "https://speech.test/v1/audio/transcriptions",
        data=b"audio",
        headers={"Authorization": "Bearer private-key"},
    )
    handler = transcribe_module._SameOriginRedirect(("https", "speech.test", 443))

    with pytest.raises(TranscriptionError, match="redirect rejected"):
        handler.redirect_request(original, None, 302, "Found", {}, destination)


def test_redirect_handler_allows_same_origin_and_keeps_authorization():
    original = urllib.request.Request(
        "https://speech.test/v1/audio/transcriptions",
        data=b"audio",
        headers={"Authorization": "Bearer private-key"},
    )
    handler = transcribe_module._SameOriginRedirect(("https", "speech.test", 443))

    redirected = handler.redirect_request(
        original, None, 302, "Found", {}, "https://speech.test/other"
    )

    assert redirected.full_url == "https://speech.test/other"
    assert redirected.get_header("Authorization") == "Bearer private-key"


@pytest.mark.parametrize(
    ("origin", "destination"),
    [
        ("http://speech.test", "http://other.test"),
        ("https://speech.test", "http://speech.test"),
        ("https://speech.test", "https://other.test"),
    ],
)
def test_requests_never_follow_a_redirect_with_auth_to_another_origin(
    monkeypatch, origin, destination
):
    seen = []

    def redirect(request):
        seen.append(request)
        headers = Message()
        headers["Location"] = f"{destination}/leak"
        response = urllib.response.addinfourl(
            io.BytesIO(), headers, request.full_url, 302
        )
        response.msg = "Found"
        return response

    class StubHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return redirect(request)

    class StubHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return redirect(request)

    build_opener = urllib.request.build_opener
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: build_opener(*handlers, StubHTTPHandler, StubHTTPSHandler),
    )
    settings = Config(url=f"{origin}/v1", api_key="private-key")

    with pytest.raises(TranscriptionError, match="redirect rejected"):
        transcribe(b"wav", settings)
    assert server_status(settings) == "unreachable"

    assert len(seen) == 2
    assert all(request.full_url.startswith(origin + "/") for request in seen)
    assert all(request.get_header("Authorization") == "Bearer private-key" for request in seen)


def test_unauthorized_response_raises_transcription_error(monkeypatch):
    def unauthorized(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"error": "secret transcript"}'),
        )

    monkeypatch.setattr(transcribe_module, "_open", unauthorized)
    with pytest.raises(TranscriptionError) as raised:
        transcribe(b"wav", Config(url="http://speech.test/v1", api_key="wrong"))
    assert raised.value.status_code == 401
    assert raised.value.__context__ is None
    assert "secret transcript" not in str(raised.value)
    assert "speech.test" not in str(raised.value)


def test_unreachable_server_raises_transcription_error(monkeypatch):
    def unreachable(request, timeout):
        raise urllib.error.URLError("secret transcript at private URL")

    monkeypatch.setattr(transcribe_module, "_open", unreachable)
    config = Config(url="http://speech.test/v1", api_key="secret")
    with pytest.raises(TranscriptionError) as raised:
        transcribe(b"wav", config, timeout=0.1)
    assert "secret transcript" not in str(raised.value)
    assert raised.value.__context__ is None


def test_malformed_json_response_raises_transcription_error(monkeypatch):
    stub_response(monkeypatch, response_body=b"private transcript, not json")
    with pytest.raises(TranscriptionError) as raised:
        transcribe(b"wav", Config(url="http://speech.test/v1", api_key="secret"))
    assert "private transcript" not in str(raised.value)
    assert raised.value.__context__ is None
