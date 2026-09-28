import json
import os
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "whisper-local" / "config.toml"


@dataclass(frozen=True)
class Config:
    engine: str = "builtin"
    url: str = "http://localhost:8000/v1"
    api_key: str = ""
    model: str = "qwen3-asr"
    key: str = "right_alt" if sys.platform == "darwin" else "right_ctrl"
    hands_free_key: str = ""
    # Empty lets the model detect the language; "en", "de", ... forces one.
    language: str = ""
    sounds: bool = True
    vocabulary: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.engine not in ("builtin", "server"):
            raise ValueError("engine must be 'builtin' or 'server'")
        if not isinstance(self.vocabulary, list) or any(
            not isinstance(word, str) for word in self.vocabulary
        ):
            raise TypeError("vocabulary must be a list of strings")


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text()) if path.exists() else {}


_FIELD_NAMES = {field.name for field in fields(Config)}


def load(path: Path = CONFIG_PATH) -> Config:
    values = {name: value for name, value in _read(path).items() if name in _FIELD_NAMES}
    config = Config(**values)
    if "api_key" not in values:
        config = Config(
            **{**values, "api_key": os.environ.get("WHISPER_LOCAL_API_KEY", "")}
        )
    return config


def _toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)} = {_toml_value(item)}"
            for key, item in value.items()
        ) + " }"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def save(changes: dict[str, str | bool | list[str]], path: Path = CONFIG_PATH) -> Config:
    """Write `changes` over the settings in the file and return the result.

    Only the file's own values are kept, so a key found through the
    environment never ends up in the file.
    """
    unknown_changes = changes.keys() - _FIELD_NAMES
    if unknown_changes:
        raise TypeError(f"Unknown setting: {', '.join(sorted(unknown_changes))}")
    values = {**_read(path), **changes}
    Config(**{name: value for name, value in values.items() if name in _FIELD_NAMES})
    names = [field.name for field in fields(Config) if field.name in values]
    names.extend(sorted(values.keys() - _FIELD_NAMES))
    text = "".join(
        f"{name if name in _FIELD_NAMES else json.dumps(name, ensure_ascii=False)}"
        f" = {_toml_value(values[name])}\n"
        for name in names
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(text)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return load(path)
