import os
import shlex
import shutil
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
UUID = "whisper-local@nicolasmahn.github.io"
APP_ID = "io.github.nicolasmahn.WhisperLocal"
RULE = "/etc/udev/rules.d/60-whisper-local-keyboards.rules"


def staged_scripts(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (repo / "gnome-extension").mkdir()
    for name in (f"{APP_ID}.desktop", f"{APP_ID}-autostart.desktop", f"{APP_ID}.svg"):
        shutil.copy(REPO / "data" / name, data / name)

    rule = tmp_path / "etc" / "60-whisper-local-keyboards.rules"
    device_root = tmp_path / "dev"
    device_root.mkdir()
    for name in ("install.sh", "uninstall.sh"):
        source = (REPO / name).read_text()
        assert source.count(RULE) == 1
        source = source.replace(RULE, shlex.quote(str(rule)))
        if name == "uninstall.sh":
            assert source.count("device_root=/dev") == 1
            source = source.replace(
                "device_root=/dev", f"device_root={shlex.quote(str(device_root))}"
            )
        (repo / name).write_text(source)
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in ("build-whisper-cpp.sh", "download-model.sh"):
        shutil.copy(REPO / "scripts" / name, scripts / name)
    return repo, rule, device_root


def script_environment(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    stub = (
        "#!/bin/sh\n"
        'printf "%s %s\\n" "${0##*/}" "$*" >> "$STUB_LOG"\n'
        'case "${0##*/}" in\n'
        '  gnome-extensions) exit 1 ;;\n'
        '  gsettings) if [ "$1" = get ]; then echo "@as []"; fi ;;\n'
        '  getfacl) if [ -n "$STUB_ACL_UID" ]; then printf "user:%s:rw-\\n" "$STUB_ACL_UID"; fi ;;\n'
        "esac\n"
    )
    for name in (
        "uv", "gnome-extensions", "gsettings", "gdbus", "update-desktop-database",
        "pkexec", "getfacl",
    ):
        command = bin_dir / name
        command.write_text(stub)
        command.chmod(0o755)
    podman = bin_dir / "podman"
    podman.write_text(
        "#!/bin/sh\n"
        'printf "podman %s\\n" "$*" >> "$STUB_LOG"\n'
        '[ "$STUB_PODMAN_FAIL" = 1 ] && exit 1\n'
        'for arg do case "$arg" in *:/output:Z) stage=${arg%:/output:Z} ;; esac; done\n'
        'printf "#!/bin/sh\\n" > "$stage/whisper-server"\n'
        'chmod +x "$stage/whisper-server"\n'
    )
    podman.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'printf "curl %s\\n" "$*" >> "$STUB_LOG"\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --output ]; then shift; output=$1; fi\n'
        '  shift\n'
        'done\n'
        'truncate -s 874188075 "$output"\n'
    )
    curl.chmod(0o755)
    sha256sum = bin_dir / "sha256sum"
    sha256sum.write_text(
        "#!/bin/sh\n"
        'printf "sha256sum %s\\n" "$*" >> "$STUB_LOG"\n'
        'for arg do file=$arg; done\n'
        'printf "%s  %s\\n" "$STUB_MODEL_SHA" "$file"\n'
    )
    sha256sum.chmod(0o755)
    env = os.environ.copy()
    env.update(
        HOME=str(home),
        XDG_DATA_HOME=str(home / ".local/share"),
        XDG_CONFIG_HOME=str(home / ".config"),
        PATH=f"{bin_dir}:{env['PATH']}",
        STUB_LOG=str(log),
        STUB_ACL_UID="",
        STUB_PODMAN_FAIL="0",
        STUB_MODEL_SHA="317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1",
    )
    return env, log, home / ".local/share/gnome-shell/extensions" / UUID


def run_script(name, env, repo, *, args=(), input=None, check=True):
    return subprocess.run(
        ["bash", str(repo / name), *args], env=env, check=check,
        capture_output=True, text=True, input=input,
    )


def test_builtin_engine_install_is_idempotent(tmp_path):
    env, log, _ = script_environment(tmp_path)
    repo, _, _ = staged_scripts(tmp_path)
    engine = Path(env["XDG_DATA_HOME"]) / "whisper-local"

    run_script("install.sh", env, repo)
    run_script("install.sh", env, repo)

    assert (engine / "whisper.cpp/whisper-server").is_file()
    assert (engine / "whisper.cpp/VERSION").read_text() == "v1.9.4\n"
    assert (engine / "models/ggml-large-v3-turbo-q8_0.bin").stat().st_size == 874188075
    commands = log.read_text()
    assert commands.count("podman run ") == 1
    assert commands.count("curl --fail") == 1
    assert "--continue-at -" in commands
    assert "registry.fedoraproject.org/fedora:43" in commands


def test_model_checksum_mismatch_refuses_install(tmp_path):
    env, _, _ = script_environment(tmp_path)
    repo, _, _ = staged_scripts(tmp_path)
    env["STUB_MODEL_SHA"] = "0" * 64

    result = run_script("scripts/download-model.sh", env, repo, check=False)

    assert result.returncode != 0
    assert "SHA-256 verification" in result.stderr
    models = Path(env["XDG_DATA_HOME"]) / "whisper-local/models"
    assert not (models / "ggml-large-v3-turbo-q8_0.bin").exists()
    assert not (models / ".ggml-large-v3-turbo-q8_0.bin.part").exists()


def test_build_failure_does_not_install_version(tmp_path):
    env, _, _ = script_environment(tmp_path)
    repo, _, _ = staged_scripts(tmp_path)
    env["STUB_PODMAN_FAIL"] = "1"

    result = run_script("scripts/build-whisper-cpp.sh", env, repo, check=False)

    assert result.returncode != 0
    assert "build failed" in result.stderr
    engine = Path(env["XDG_DATA_HOME"]) / "whisper-local/whisper.cpp"
    assert not (engine / "VERSION").exists()
    assert not list(engine.glob(".build.*"))


def test_uninstall_keeps_engine_without_consent(tmp_path):
    env, _, _ = script_environment(tmp_path)
    repo, _, _ = staged_scripts(tmp_path)
    run_script("install.sh", env, repo)
    engine = Path(env["XDG_DATA_HOME"]) / "whisper-local"
    binary = engine / "whisper.cpp/whisper-server"
    model = engine / "models/ggml-large-v3-turbo-q8_0.bin"

    declined = run_script("uninstall.sh", env, repo, input="no\n")
    assert "Kept the built-in Whisper engine" in declined.stdout
    assert binary.exists()
    assert model.exists()

    run_script("uninstall.sh", env, repo, args=("--yes",))
    assert not binary.exists()
    assert not (engine / "whisper.cpp/VERSION").exists()
    assert not model.exists()


def test_install_and_uninstall_manage_own_extension_link(tmp_path):
    env, log, extension = script_environment(tmp_path)
    repo, _, _ = staged_scripts(tmp_path)

    run_script("install.sh", env, repo)
    assert extension.is_symlink()
    assert extension.resolve() == repo / "gnome-extension"
    assert (Path(env["XDG_DATA_HOME"]) / "applications" / f"{APP_ID}.desktop").exists()

    run_script("uninstall.sh", env, repo)
    assert not extension.is_symlink()
    assert not (Path(env["XDG_DATA_HOME"]) / "applications" / f"{APP_ID}.desktop").exists()
    assert "gnome-extensions enable" in log.read_text()
    assert "gnome-extensions disable" in log.read_text()


def test_foreign_extension_link_is_left_alone(tmp_path):
    env, log, extension = script_environment(tmp_path)
    repo, _, _ = staged_scripts(tmp_path)
    extension.parent.mkdir(parents=True)
    foreign = tmp_path / "foreign-extension"
    foreign.mkdir()
    extension.symlink_to(foreign)

    installed = run_script("install.sh", env, repo)
    removed = run_script("uninstall.sh", env, repo)

    assert extension.is_symlink()
    assert extension.resolve() == foreign
    assert "points elsewhere" in installed.stderr
    assert "points elsewhere" in removed.stderr
    assert "gnome-extensions" not in log.read_text()


def test_uninstall_refreshes_both_device_subsystems_and_checks_acls(tmp_path):
    env, log, _ = script_environment(tmp_path)
    repo, rule, device_root = staged_scripts(tmp_path)
    rule.parent.mkdir()
    rule.write_text("test rule")
    (device_root / "input").mkdir()
    (device_root / "input" / "event0").touch()
    (device_root / "uinput").touch()
    env["STUB_ACL_UID"] = str(os.geteuid())

    result = run_script("uninstall.sh", env, repo)

    commands = log.read_text()
    assert "pkexec /bin/sh -c" in commands
    assert "--subsystem-match=input --subsystem-match=misc --action=change" in commands
    assert "udevadm settle" in commands
    assert commands.count("getfacl -ncp") == 2
    assert "Keyboard access still granted" in result.stderr
    assert rule.exists()  # The pkexec stub never removes even the temporary rule.


def test_install_rejects_unsupported_repo_path_before_running_commands(tmp_path):
    env, log, _ = script_environment(tmp_path)
    unusual_repo = tmp_path / "repo&path"
    unusual_repo.mkdir()
    source = (REPO / "install.sh").read_text()
    assert source.count(RULE) == 1
    (unusual_repo / "install.sh").write_text(
        source.replace(RULE, shlex.quote(str(tmp_path / "rule")))
    )

    result = subprocess.run(
        ["bash", str(unusual_repo / "install.sh")],
        env=env, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "Cannot install from this path" in result.stderr
    assert not log.exists()
