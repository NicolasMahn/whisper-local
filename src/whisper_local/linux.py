"""Wayland hotkey listening and text insertion through Linux input devices."""

import errno
import fcntl
import glob
import os
import select
import shlex
import struct
import subprocess
import threading
import time

from .platform import HotkeyHandler


_KEY_CODES = {
    "right_ctrl": 97,
    "right_alt": 100,
    "right_cmd": 126,
    "right_shift": 54,
    "f13": 183,
    "f14": 184,
    "f15": 185,
}

_EV_SYN = 0
_EV_KEY = 1
_SYN_REPORT = 0
_SYN_DROPPED = 3
_KEY_A = 30
_KEY_Z = 44
_KEY_ESC = 1
_KEY_LEFTSHIFT = 42
_KEY_INSERT = 110
_EVENT = struct.Struct("llHHi")
_SETUP = struct.Struct("HHHH80sI")
_KEY_BITS_SIZE = (0x2FF + 8) // 8


def _ioc(direction: int, kind: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | number


def _ior(kind: str, number: int, size: int) -> int:
    return _ioc(2, kind, number, size)


def _iow(kind: str, number: int, size: int) -> int:
    return _ioc(1, kind, number, size)


_UI_SET_EVBIT = _iow("U", 100, struct.calcsize("i"))
_UI_SET_KEYBIT = _iow("U", 101, struct.calcsize("i"))
_UI_DEV_SETUP = _iow("U", 3, _SETUP.size)
_UI_DEV_CREATE = _ioc(0, "U", 1, 0)


def _has_bit(bits: bytearray, bit: int) -> bool:
    return bool(bits[bit // 8] & (1 << (bit % 8)))


def _is_keyboard(fd: int) -> bool:
    name = bytearray(256)
    fcntl.ioctl(fd, _ior("E", 0x06, len(name)), name, True)
    if name.split(b"\0", 1)[0] == b"whisper-local":
        return False

    events = bytearray(4)
    fcntl.ioctl(fd, _ior("E", 0x20, len(events)), events, True)
    if not _has_bit(events, _EV_KEY):
        return False

    keys = bytearray(_KEY_BITS_SIZE)
    fcntl.ioctl(fd, _ior("E", 0x20 + _EV_KEY, len(keys)), keys, True)
    return _has_bit(keys, _KEY_A) and _has_bit(keys, _KEY_Z)


def _forget(
    fd: int, devices: dict[int, str], held: set[int],
    hands_free_held: set[int], handler: HotkeyHandler,
) -> None:
    devices.pop(fd)
    os.close(fd)
    if fd in held:
        held.remove(fd)
        if not held:
            handler.released()
    if fd in hands_free_held:
        hands_free_held.remove(fd)
        if not hands_free_held:
            handler.hands_free_released()


def _scan(
    devices: dict[int, str], held: set[int], hands_free_held: set[int],
    handler: HotkeyHandler,
) -> None:
    paths = set(glob.glob("/dev/input/event*"))
    for fd, path in tuple(devices.items()):
        if path not in paths:
            _forget(fd, devices, held, hands_free_held, handler)

    for path in paths - set(devices.values()):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            if exc.errno in (
                errno.EACCES, errno.EPERM, errno.ENOENT,
                errno.ENODEV, errno.ENXIO, errno.EIO,
            ):
                continue
            raise
        try:
            keyboard = _is_keyboard(fd)
        except OSError as exc:
            os.close(fd)
            if exc.errno not in (
                errno.EACCES, errno.EPERM, errno.ENODEV,
                errno.ENXIO, errno.EIO, errno.ENOTTY,
            ):
                raise
            continue
        if keyboard:
            devices[fd] = path
        else:
            os.close(fd)


# Lets whoever sits at this computer read keyboards and create the virtual
# keyboard that pastes, through systemd's `uaccess` tag (Steam's rule does the
# same for /dev/uinput). Unlike the input group it takes effect at once,
# without logging out, and leaves mice and remote logins out.
KEYBOARD_RULE_PATH = "/etc/udev/rules.d/60-whisper-local-keyboards.rules"
_KEYBOARD_RULE = (
    'SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEYBOARD}=="1", TAG+="uaccess"\n'
    'SUBSYSTEM=="misc", KERNEL=="uinput", TAG+="uaccess", OPTIONS+="static_node=uinput"\n'
)


def grant_keyboard_access() -> bool:
    """Install the keyboard rule after asking for the admin password (polkit).

    Blocks while the password dialog is open. False if the user cancelled.
    """
    script = (
        f"printf %s {shlex.quote(_KEYBOARD_RULE)} > {KEYBOARD_RULE_PATH}"
        " && udevadm control --reload"
        " && udevadm trigger --subsystem-match=input --subsystem-match=misc --action=change"
        " && udevadm settle"
    )
    return subprocess.run(["pkexec", "/bin/sh", "-c", script]).returncode == 0


def listen(
    key: str, handler: HotkeyHandler, hands_free_key: str | None = None
) -> None:
    """Block while reporting trigger key events from readable keyboards."""
    keycode = _KEY_CODES[key]
    hands_free_code = _KEY_CODES[hands_free_key] if hands_free_key else None
    if hands_free_code == keycode:
        raise ValueError("Hands-free key must differ from the dictation key")
    devices: dict[int, str] = {}
    held: set[int] = set()
    hands_free_held: set[int] = set()
    interrupted = False

    try:
        _scan(devices, held, hands_free_held, handler)
        if not devices:
            raise PermissionError("No readable keyboards in /dev/input")

        next_scan = time.monotonic() + 1.0
        while True:
            readable, _, _ = select.select(
                tuple(devices), (), (), max(0.0, next_scan - time.monotonic())
            )
            for fd in readable:
                try:
                    data = os.read(fd, _EVENT.size * 64)
                except OSError as exc:
                    if exc.errno in (errno.ENODEV, errno.ENXIO, errno.EIO):
                        _forget(fd, devices, held, hands_free_held, handler)
                        continue
                    if exc.errno == errno.EAGAIN:
                        continue
                    raise
                if not data:
                    _forget(fd, devices, held, hands_free_held, handler)
                    continue

                for offset in range(0, len(data), _EVENT.size):
                    _, _, event_type, code, value = _EVENT.unpack_from(data, offset)
                    if event_type == _EV_SYN and code == _SYN_DROPPED:
                        # An overflow can lose a release; do not leave a
                        # trigger held indefinitely.
                        if fd in held:
                            held.remove(fd)
                            if not held:
                                handler.interrupted()
                                handler.released()
                        if fd in hands_free_held:
                            hands_free_held.remove(fd)
                            if not hands_free_held:
                                handler.hands_free_released()
                        continue
                    if event_type != _EV_KEY:
                        continue
                    if code == keycode:
                        if value == 1 and fd not in held:
                            first_press = not held
                            held.add(fd)
                            if first_press:
                                interrupted = False
                                handler.pressed()
                        elif value == 0 and fd in held:
                            held.remove(fd)
                            if not held:
                                handler.released()
                    elif code == hands_free_code:
                        if value == 1 and fd not in hands_free_held:
                            first_press = not hands_free_held
                            hands_free_held.add(fd)
                            if first_press:
                                if held and not interrupted:
                                    interrupted = True
                                    handler.interrupted()
                                handler.hands_free_pressed()
                        elif value == 0 and fd in hands_free_held:
                            hands_free_held.remove(fd)
                            if not hands_free_held:
                                handler.hands_free_released()
                    elif value == 1:
                        if code == _KEY_ESC:
                            handler.escaped()
                        if held and not interrupted:
                            interrupted = True
                            handler.interrupted()

            if time.monotonic() >= next_scan:
                _scan(devices, held, hands_free_held, handler)
                next_scan = time.monotonic() + 1.0
    finally:
        for fd in devices:
            os.close(fd)


_uinput_fd: int | None = None
_uinput_lock = threading.Lock()


def _virtual_keyboard() -> int:
    global _uinput_fd
    if _uinput_fd is not None:
        return _uinput_fd

    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_CLOEXEC)
    try:
        fcntl.ioctl(fd, _UI_SET_EVBIT, _EV_SYN)
        fcntl.ioctl(fd, _UI_SET_EVBIT, _EV_KEY)
        # udev recognizes a full keyboard when the first 31 key bits are set.
        for code in (*range(1, 32), _KEY_LEFTSHIFT, _KEY_Z, _KEY_INSERT):
            fcntl.ioctl(fd, _UI_SET_KEYBIT, code)
        setup = _SETUP.pack(3, 1, 1, 1, b"whisper-local", 0)
        fcntl.ioctl(fd, _UI_DEV_SETUP, setup)
        fcntl.ioctl(fd, _UI_DEV_CREATE)
    except BaseException:
        os.close(fd)
        raise

    _uinput_fd = fd
    time.sleep(0.5)  # Let GNOME discover the device before its first key event.
    return fd


def prepare_virtual_keyboard() -> None:
    with _uinput_lock:
        _virtual_keyboard()


def _key_frame(code: int, value: int) -> bytes:
    return _EVENT.pack(0, 0, _EV_KEY, code, value) + _EVENT.pack(
        0, 0, _EV_SYN, _SYN_REPORT, 0
    )


_PASTE_EVENTS = b"".join(
    _key_frame(code, value)
    for code, value in (
        (_KEY_LEFTSHIFT, 1),
        (_KEY_INSERT, 1),
        (_KEY_INSERT, 0),
        (_KEY_LEFTSHIFT, 0),
    )
)


def insert_text(text: str) -> None:
    """Paste text through both Wayland selections and Shift+Insert."""
    with _uinput_lock:
        fd = _virtual_keyboard()
        encoded = text.encode("utf-8")
        subprocess.run(("wl-copy",), input=encoded, check=True)
        subprocess.run(("wl-copy", "--primary"), input=encoded, check=True)
        time.sleep(0.1)
        # Shift+Insert pastes in terminals as well as graphical text fields.
        written = os.write(fd, _PASTE_EVENTS)
        if written != len(_PASTE_EVENTS):
            raise OSError(errno.EIO, "Short write to /dev/uinput")
