import os

import pytest

from whisper_local import linux


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ([(1, 1)], ["escaped"]),
        ([(97, 1), (1, 1)], ["pressed", "escaped", "interrupted"]),
    ],
)
def test_escape_is_reported_with_or_without_dictation_key_held(monkeypatch, keys, expected):
    read_fd, write_fd = os.pipe()
    try:
        os.write(
            write_fd,
            b"".join(linux._EVENT.pack(0, 0, linux._EV_KEY, code, value) for code, value in keys),
        )
    finally:
        os.close(write_fd)

    monkeypatch.setattr(linux, "_scan", lambda devices, *_: devices.update({read_fd: "test"}))
    selects = iter([([read_fd], [], [])])
    monkeypatch.setattr(linux.select, "select", lambda *_: next(selects))

    class Handler:
        def __getattr__(self, name):
            return lambda: calls.append(name)

    calls = []
    with pytest.raises(StopIteration):
        linux.listen("right_ctrl", Handler())

    assert calls == expected


def test_listener_reports_dedicated_key_and_interrupts_normal_hold(monkeypatch):
    events = [
        (183, 1), (183, 2), (183, 0),
        (97, 1), (183, 1), (183, 0), (97, 0),
    ]
    read_fd, write_fd = os.pipe()
    try:
        os.write(
            write_fd,
            b"".join(linux._EVENT.pack(0, 0, linux._EV_KEY, code, value) for code, value in events),
        )
    finally:
        os.close(write_fd)

    monkeypatch.setattr(linux, "_scan", lambda devices, *_: devices.update({read_fd: "test"}))
    selects = iter([([read_fd], [], [])])
    monkeypatch.setattr(linux.select, "select", lambda *_: next(selects))

    class Handler:
        def __getattr__(self, name):
            return lambda: calls.append(name)

    calls = []
    with pytest.raises(StopIteration):
        linux.listen("right_ctrl", Handler(), "f13")

    assert calls == [
        "hands_free_pressed", "hands_free_released", "pressed",
        "interrupted", "hands_free_pressed", "hands_free_released", "released",
    ]
