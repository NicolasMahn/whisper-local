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
    env = os.environ.copy()
    env.update(
        HOME=str(home),
        XDG_DATA_HOME=str(home / ".local/share"),
        XDG_CONFIG_HOME=str(home / ".config"),
        PATH=f"{bin_dir}:{env['PATH']}",
        STUB_LOG=str(log),
        STUB_ACL_UID="",
    )
    return env, log, home / ".local/share/gnome-shell/extensions" / UUID


def run_script(name, env, repo):
    return subprocess.run(
        ["bash", str(repo / name)], env=env, check=True, capture_output=True, text=True
    )


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
