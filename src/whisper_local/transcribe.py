import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from whisper_local.config import Config


class TranscriptionError(Exception):
    def __init__(self, category: str, status_code: int | None = None):
        self.category = category
        self.status_code = status_code
        super().__init__(category)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port if parsed.port is not None else {
        "http": 80, "https": 443
    }.get(parsed.scheme)
    return parsed.scheme.lower(), parsed.hostname, port


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str | None, int | None]):
        self.origin = origin

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if _origin(newurl) != self.origin:
            raise TranscriptionError("redirect rejected")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(
        _SameOriginRedirect(_origin(request.full_url))
    ).open(request, timeout=timeout)


def _response_text(payload: bytes) -> str | None:
    try:
        return json.loads(payload)["text"].strip()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def transcribe(wav: bytes, config: Config, timeout: float = 30) -> str:
    fields = {"model": config.model}
    if config.language:
        fields["language"] = config.language
    if config.vocabulary:
        fields["prompt"] = ", ".join(config.vocabulary)
    body, content_type = _multipart(fields, wav)
    url = f"{config.url}/audio/transcriptions"
    headers = {"Content-Type": content_type}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    status_code = None
    try:
        request = urllib.request.Request(url, data=body, headers=headers)
        with _open(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        status_code = error.code
    # Timeouts while reading the response arrive as bare OSErrors, not URLError.
    except (OSError, ValueError, http.client.HTTPException):
        pass
    else:
        text = _response_text(payload)
        del payload
        if text is None:
            raise TranscriptionError("unexpected response")
        return text
    if status_code is not None:
        raise TranscriptionError("http error", status_code)
    raise TranscriptionError("unreachable")


def server_status(config: Config, timeout: float = 3) -> str:
    """Ask the server for its models: "ok", "unauthorized" or "unreachable"."""
    try:
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        request = urllib.request.Request(f"{config.url}/models", headers=headers)
        with _open(request, timeout=timeout):
            return "ok"
    except urllib.error.HTTPError as error:
        return "unauthorized" if error.code in (401, 403) else "unreachable"
    except (OSError, ValueError, TranscriptionError):
        return "unreachable"


def _multipart(fields: dict[str, str], wav: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
        + wav
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
