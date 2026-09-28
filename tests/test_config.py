import os
import stat

import pytest

import whisper_local.config as config


def test_load_reads_toml_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        'url = "http://speech.test/v1"\n'
        'api_key = "toml-key"\n'
        'model = "qwen-test"\n'
        'key = "f13"\n'
        'language = "de"\n'
        'vocabulary = ["Nicolas Mahn", "Qwen"]\n'
        "sounds = false\n"
    )
    monkeypatch.delenv("WHISPER_LOCAL_API_KEY", raising=False)

    loaded = config.load(path)

    assert loaded.url == "http://speech.test/v1"
    assert loaded.engine == "builtin"
    assert loaded.api_key == "toml-key"
    assert loaded.model == "qwen-test"
    assert loaded.key == "f13"
    assert loaded.language == "de"
    assert loaded.vocabulary == ["Nicolas Mahn", "Qwen"]
    assert loaded.sounds is False


def test_defaults_allow_an_unauthenticated_local_server(tmp_path, monkeypatch):
    monkeypatch.delenv("WHISPER_LOCAL_API_KEY", raising=False)

    loaded = config.load(tmp_path / "missing.toml")

    assert loaded.url == "http://localhost:8000/v1"
    assert loaded.engine == "builtin"
    assert loaded.api_key == ""
    assert loaded.vocabulary == []


@pytest.mark.parametrize("value", ['"Qwen"', '["Qwen", 3]'])
def test_vocabulary_requires_a_list_of_strings(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"vocabulary = {value}\n")

    with pytest.raises(TypeError, match="vocabulary must be a list of strings"):
        config.load(path)


def test_explicit_empty_api_key_overrides_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('api_key = ""\n')
    monkeypatch.setenv("WHISPER_LOCAL_API_KEY", "environment-key")

    assert config.load(path).api_key == ""


def test_empty_api_key_stays_empty_without_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('api_key = ""\n')
    monkeypatch.delenv("WHISPER_LOCAL_API_KEY", raising=False)

    assert config.load(path).api_key == ""


def test_save_round_trips_without_writing_the_fallback_key(tmp_path, monkeypatch):
    path = tmp_path / "whisper-local" / "config.toml"
    monkeypatch.setenv("WHISPER_LOCAL_API_KEY", "environment-key")

    config.save({
        "url": 'http://host:1/v1 "quoted" ü',
        "sounds": False,
        "vocabulary": ["Nicolas Mahn", "Qwen", "DRY"],
    }, path)
    saved = config.save({"language": "de"}, path)

    assert saved == config.load(path)
    assert saved.url == 'http://host:1/v1 "quoted" ü'
    assert saved.sounds is False
    assert saved.language == "de"
    assert saved.vocabulary == ["Nicolas Mahn", "Qwen", "DRY"]
    assert saved.api_key == "environment-key"
    assert "api_key" not in path.read_text()


def test_unknown_toml_settings_survive_load_and_save(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'api_key = "secret"\n'
        'future_setting = { enabled = true, languages = ["en", "de"] }\n'
    )

    assert config.load(path).api_key == "secret"
    saved = config.save({"vocabulary": ["Qwen"]}, path)

    assert saved.vocabulary == ["Qwen"]
    assert config._read(path)["future_setting"] == {
        "enabled": True, "languages": ["en", "de"]
    }


def test_save_replaces_config_with_private_file_even_under_open_umask(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('api_key = "old"\n')
    os.chmod(path, 0o644)

    old_umask = os.umask(0)
    try:
        config.save({"api_key": "secret", "vocabulary": ["Qwen"]}, path)
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert config.load(path).api_key == "secret"
    assert config.load(path).vocabulary == ["Qwen"]
    assert list(tmp_path.iterdir()) == [path]


def test_hands_free_key_round_trips_and_defaults_to_double_tap(tmp_path):
    path = tmp_path / "config.toml"
    assert config.load(path).hands_free_key == ""

    saved = config.save({"hands_free_key": "f13"}, path)
    assert saved.hands_free_key == "f13"
    assert config.load(path).hands_free_key == "f13"

    config.save({"hands_free_key": ""}, path)
    assert config.load(path).hands_free_key == ""


def test_engine_round_trips_and_rejects_unknown_values(tmp_path):
    path = tmp_path / "config.toml"
    assert config.save({"engine": "server"}, path).engine == "server"
    assert config.load(path).engine == "server"
    assert config.save({"engine": "builtin"}, path).engine == "builtin"

    before = path.read_text()
    with pytest.raises(ValueError, match="engine must be"):
        config.save({"engine": "unknown"}, path)
    assert path.read_text() == before

    path.write_text('engine = "unknown"\n')
    with pytest.raises(ValueError, match="engine must be"):
        config.load(path)
