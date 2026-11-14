import threading

from whisper_local.screenshots import Screenshots


def test_each_recording_gets_its_own_screenshot():
    screenshots = Screenshots()
    first_id, first = screenshots.ask()
    second_id, second = screenshots.ask()

    screenshots.deliver(second_id, b"second")
    screenshots.deliver(first_id, b"first")

    assert first.wait() == b"first"
    assert second.wait() == b"second"


def test_waiting_ends_when_the_screenshot_arrives():
    screenshots = Screenshots(patience=30)
    request_id, pending = screenshots.ask()

    threading.Timer(0.01, screenshots.deliver, (request_id, b"png")).start()

    assert pending.wait() == b"png"


def test_nothing_arriving_does_not_hold_up_the_dictation():
    # What the extension answers when no window has focus, or no extension at all.
    screenshots = Screenshots(patience=0)
    request_id, empty = screenshots.ask()
    screenshots.deliver(request_id, b"")
    _, missing = screenshots.ask()

    assert empty.wait() is None
    assert missing.wait() is None


def test_unknown_or_late_delivery_is_ignored():
    screenshots = Screenshots(remembered=1)
    first_id, _ = screenshots.ask()
    screenshots.ask()

    screenshots.deliver(first_id, b"late")
    screenshots.deliver(999, b"unknown")
