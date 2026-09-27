import threading

from whisper_local.app import Dictation, _print_error


def test_default_error_log_omits_exception_text(capsys):
    _print_error(RuntimeError("private transcript"))

    assert capsys.readouterr().err == "whisper-local: RuntimeError\n"


class FakeRecorder:
    def __init__(self, *, start_error=None, stop_error=None, cancel_error=None, wav=b"wav data"):
        self.calls = []
        self.start_error = start_error
        self.stop_error = stop_error
        self.cancel_error = cancel_error
        self.wav = wav

    def start(self):
        self.calls.append("start")
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.calls.append("stop")
        if self.stop_error is not None:
            raise self.stop_error
        return self.wav

    def cancel(self):
        self.calls.append("cancel")
        if self.cancel_error is not None:
            raise self.cancel_error


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_dictation(
    *, recorder=None, transcribe=None, insert_text=None, submit=None, on_state=None, on_error=None,
    hands_free_key="",
):
    recorder = recorder or FakeRecorder()
    clock = FakeClock()
    inserted = [] if insert_text is None else None
    cues = []
    transcriptions = []

    def transcribe_wav(wav):
        transcriptions.append(wav)
        return "hello there" if transcribe is None else transcribe(wav)

    def insert(text):
        if insert_text is None:
            inserted.append(text)
        else:
            insert_text(text)

    callbacks = {}
    if on_state is not None:
        callbacks["on_state"] = on_state
    if on_error is not None:
        callbacks["on_error"] = on_error

    dictation = Dictation(
        recorder,
        transcribe_wav,
        insert,
        cue=cues.append,
        submit=(lambda job: job()) if submit is None else submit,
        clock=clock,
        hands_free_key=hands_free_key,
        **callbacks,
    )
    return dictation, recorder, clock, inserted, cues, transcriptions


def test_normal_dictation_inserts_transcribed_text():
    dictation, recorder, clock, inserted, cues, transcriptions = make_dictation()

    dictation.pressed()
    clock.now = 0.5
    dictation.released()

    assert recorder.calls == ["start", "stop"]
    assert transcriptions == [b"wav data"]
    assert inserted == ["hello there"]
    assert cues == ["start", "stop"]


def test_too_short_press_is_dropped_silently():
    dictation, recorder, clock, inserted, cues, transcriptions = make_dictation()

    dictation.pressed()
    clock.now = 0.2
    dictation.released()

    assert recorder.calls == ["start", "stop"]
    assert transcriptions == []
    assert inserted == []
    assert cues == ["start"]


def test_interrupted_recording_is_cancelled_without_inserting():
    dictation, recorder, _, inserted, cues, transcriptions = make_dictation()

    dictation.pressed()
    dictation.interrupted()
    dictation.released()

    assert recorder.calls == ["start", "cancel"]
    assert transcriptions == []
    assert inserted == []
    assert cues == ["start"]


def test_auto_repeat_press_does_not_restart_recording():
    dictation, recorder, clock, inserted, _, _ = make_dictation()

    dictation.pressed()
    dictation.pressed()
    clock.now = 0.5
    dictation.released()

    assert recorder.calls == ["start", "stop"]
    assert inserted == ["hello there"]


def test_release_while_idle_is_a_no_op():
    dictation, recorder, _, inserted, cues, transcriptions = make_dictation()

    dictation.released()

    assert recorder.calls == []
    assert transcriptions == []
    assert inserted == []
    assert cues == []


def test_empty_transcript_inserts_nothing():
    dictation, _, clock, inserted, _, _ = make_dictation(transcribe=lambda wav: "")

    dictation.pressed()
    clock.now = 0.5
    dictation.released()

    assert inserted == []


def test_transcription_error_cues_without_escaping_or_inserting():
    def fail(_wav):
        raise RuntimeError("server unavailable")

    dictation, _, clock, inserted, cues, _ = make_dictation(transcribe=fail)

    dictation.pressed()
    clock.now = 0.5
    dictation.released()

    assert inserted == []
    assert cues == ["start", "stop", "error"]


def test_recorder_start_error_cues_and_stays_idle():
    recorder = FakeRecorder(start_error=RuntimeError("no microphone"))
    dictation, _, _, inserted, cues, transcriptions = make_dictation(recorder=recorder)

    dictation.pressed()
    dictation.released()

    assert recorder.calls == ["start"]
    assert inserted == []
    assert transcriptions == []
    assert cues == ["error"]


def test_paste_waits_until_the_dictation_key_is_up():
    # The key is a held modifier during a recording; pasting then would send
    # e.g. Ctrl+Shift+Insert instead of Shift+Insert.
    clock = FakeClock()
    jobs, inserted = [], []
    dictation = Dictation(
        FakeRecorder(), lambda wav: "first", inserted.append, submit=jobs.append, clock=clock
    )
    dictation.pressed()
    clock.now = 0.5
    dictation.released()
    dictation.pressed()  # a second recording starts before the first is pasted

    worker = threading.Thread(target=jobs[0])
    worker.start()
    worker.join(0.1)
    assert inserted == []

    clock.now = 1.5
    dictation.released()
    worker.join(1)
    assert inserted == ["first"]


def test_state_callback_reports_normal_dictation_changes_once():
    states = []
    dictation, _, clock, inserted, _, _ = make_dictation(on_state=states.append)

    dictation.pressed()
    dictation.pressed()
    clock.now = 0.5
    dictation.released()
    dictation.released()

    assert states == ["listening", "transcribing", "idle"]
    assert inserted == ["hello there"]


def test_too_short_hold_returns_to_idle():
    states = []
    dictation, _, clock, _, _, _ = make_dictation(on_state=states.append)

    dictation.pressed()
    clock.now = 0.2
    dictation.released()

    assert states == ["listening", "idle"]


def test_interrupted_hold_returns_to_idle():
    states = []
    dictation, _, _, _, _, _ = make_dictation(on_state=states.append)

    dictation.pressed()
    dictation.interrupted()

    assert states == ["listening", "idle"]


def test_pending_jobs_keep_transcribing_state_until_all_finish():
    jobs, states = [], []
    dictation, _, clock, inserted, _, _ = make_dictation(
        submit=jobs.append, on_state=states.append
    )

    dictation.pressed()
    clock.now = 0.5
    dictation.released()
    assert states == ["listening", "transcribing"]

    dictation.pressed()
    assert states == ["listening", "transcribing", "listening"]
    clock.now = 1.0
    dictation.released()
    assert states == ["listening", "transcribing", "listening", "transcribing"]

    jobs[0]()
    assert states[-1] == "transcribing"
    jobs[1]()
    assert states[-1] == "idle"
    assert inserted == ["hello there", "hello there"]


def test_transcription_error_is_reported_and_returns_to_idle():
    error = RuntimeError("server unavailable")
    errors, states = [], []

    def fail(_wav):
        raise error

    dictation, _, clock, _, _, _ = make_dictation(
        transcribe=fail, on_error=errors.append, on_state=states.append
    )
    dictation.pressed()
    clock.now = 0.5
    dictation.released()

    assert errors == [error]
    assert states == ["listening", "transcribing", "idle"]


def test_recorder_start_error_is_reported_while_remaining_idle():
    error = RuntimeError("no microphone")
    errors, states = [], []
    recorder = FakeRecorder(start_error=error)
    dictation, _, _, _, _, _ = make_dictation(
        recorder=recorder, on_error=errors.append, on_state=states.append
    )

    dictation.pressed()

    assert errors == [error]
    assert states == []
    assert recorder.calls == ["start"]


def test_recorder_stop_error_resets_state_and_allows_next_recording():
    error = RuntimeError("microphone stopped")
    errors, states = [], []
    recorder = FakeRecorder(stop_error=error)
    dictation, _, clock, inserted, cues, _ = make_dictation(
        recorder=recorder, on_error=errors.append, on_state=states.append
    )

    dictation.pressed()
    clock.now = 0.5
    dictation.released()

    assert errors == [error]
    assert states == ["listening", "idle"]
    assert dictation._key_up.is_set()
    assert cues == ["start", "error"]
    recorder.stop_error = None
    dictation.pressed()
    clock.now = 1.0
    dictation.released()
    assert inserted == ["hello there"]


def test_recorder_cancel_error_resets_state_and_waits_for_key_up():
    error = RuntimeError("microphone cancelled")
    errors, states = [], []
    recorder = FakeRecorder(cancel_error=error)
    dictation, _, _, _, cues, _ = make_dictation(
        recorder=recorder, on_error=errors.append, on_state=states.append
    )

    dictation.pressed()
    dictation.interrupted()

    assert errors == [error]
    assert states == ["listening", "idle"]
    assert not dictation._key_up.is_set()
    dictation.released()
    assert dictation._key_up.is_set()
    assert cues == ["start", "error"]


def test_disabled_dictation_ignores_press():
    states = []
    dictation, recorder, _, inserted, cues, _ = make_dictation(on_state=states.append)
    dictation.enabled = False

    dictation.pressed()

    assert recorder.calls == []
    assert inserted == []
    assert cues == []
    assert states == []


def start_hands_free(dictation, clock):
    dictation.pressed()
    clock.now = 0.1
    dictation.released()
    clock.now = 0.35
    dictation.pressed()


def test_double_tap_starts_hands_free_and_stopping_press_waits_for_release():
    jobs, states = [], []
    transcribe_started = threading.Event()

    def transcribe(_wav):
        transcribe_started.set()
        return "hello there"

    dictation, recorder, clock, inserted, _, transcriptions = make_dictation(
        submit=jobs.append, on_state=states.append, transcribe=transcribe
    )

    dictation.pressed()
    clock.now = 0.1
    dictation.released()
    assert recorder.calls == ["start", "stop"]
    assert transcriptions == []

    clock.now = 0.35  # 0.25 seconds after the tap's release
    dictation.pressed()
    assert states[-1] == "hands_free"
    assert recorder.calls == ["start", "stop", "start"]

    clock.now = 0.5
    dictation.released()
    assert recorder.calls == ["start", "stop", "start"]
    assert states[-1] == "hands_free"
    assert jobs == []

    clock.now = 0.6
    dictation.pressed()  # The next press stops hands-free recording.
    assert recorder.calls == ["start", "stop", "start", "stop"]
    assert len(jobs) == 1

    worker = threading.Thread(target=jobs[0])
    worker.start()
    assert transcribe_started.wait(1)
    assert inserted == []

    dictation.released()
    worker.join(1)
    assert not worker.is_alive()
    assert transcriptions == [b"wav data"]
    assert inserted == ["hello there"]


def test_second_press_after_double_tap_window_is_a_normal_hold():
    jobs, states = [], []
    dictation, recorder, clock, inserted, _, transcriptions = make_dictation(
        submit=jobs.append, on_state=states.append
    )

    dictation.pressed()
    clock.now = 0.1
    dictation.released()
    clock.now = 0.51  # 0.41 seconds after the tap's release
    dictation.pressed()

    assert states[-1] == "listening"
    assert "hands_free" not in states
    clock.now = 0.9
    dictation.released()

    assert recorder.calls == ["start", "stop", "start", "stop"]
    assert len(jobs) == 1
    jobs[0]()
    assert transcriptions == [b"wav data"]
    assert inserted == ["hello there"]


def test_interruption_does_not_cancel_hands_free_recording():
    dictation, recorder, clock, _, _, _ = make_dictation()
    start_hands_free(dictation, clock)

    dictation.interrupted()

    assert recorder.calls == ["start", "stop", "start"]
    assert dictation._state == "hands_free"


def test_escape_cancels_hands_free_without_transcribing():
    states = []
    dictation, recorder, clock, inserted, _, transcriptions = make_dictation(
        on_state=states.append
    )
    start_hands_free(dictation, clock)

    dictation.escaped()

    assert recorder.calls == ["start", "stop", "start", "cancel"]
    assert states[-1] == "idle"
    assert transcriptions == []
    assert inserted == []


def test_escape_cancels_a_normal_hold_once_with_interruption():
    for events in (
        ("escaped", "interrupted"),
        ("interrupted", "escaped"),
    ):
        states = []
        dictation, recorder, _, inserted, _, transcriptions = make_dictation(
            on_state=states.append
        )
        dictation.pressed()
        for event in events:
            getattr(dictation, event)()
        dictation.released()

        assert recorder.calls == ["start", "cancel"]
        assert states == ["listening", "idle"]
        assert transcriptions == []
        assert inserted == []


def test_escape_while_idle_does_nothing():
    dictation, recorder, _, inserted, cues, transcriptions = make_dictation()

    dictation.escaped()

    assert recorder.calls == []
    assert transcriptions == []
    assert inserted == []
    assert cues == []


def test_dedicated_key_starts_and_stops_hands_free_on_press():
    jobs, states = [], []
    dictation, recorder, _, inserted, cues, transcriptions = make_dictation(
        hands_free_key="f13", submit=jobs.append, on_state=states.append
    )

    dictation.hands_free_pressed()
    dictation.hands_free_pressed()  # key repeat
    assert states == ["hands_free"]
    assert recorder.calls == ["start"]
    dictation.hands_free_released()
    assert jobs == []

    dictation.hands_free_pressed()
    assert recorder.calls == ["start", "stop"]
    assert states == ["hands_free", "transcribing"]
    assert cues == ["start", "stop"]
    assert len(jobs) == 1
    dictation.hands_free_released()
    jobs[0]()
    assert transcriptions == [b"wav data"]
    assert inserted == ["hello there"]
    assert states[-1] == "idle"


def test_dedicated_key_stopping_press_blocks_paste_until_its_release():
    jobs, inserted = [], []
    dictation, _, _, _, _, _ = make_dictation(
        hands_free_key="f13", submit=jobs.append, insert_text=inserted.append
    )
    dictation.hands_free_pressed()
    dictation.hands_free_released()
    dictation.hands_free_pressed()

    worker = threading.Thread(target=jobs[0])
    worker.start()
    worker.join(0.1)
    assert inserted == []

    dictation.hands_free_released()
    worker.join(1)
    assert not worker.is_alive()
    assert inserted == ["hello there"]


def test_escape_cancels_dedicated_hands_free():
    states = []
    dictation, recorder, _, inserted, _, transcriptions = make_dictation(
        hands_free_key="f13", on_state=states.append
    )
    dictation.hands_free_pressed()
    dictation.hands_free_released()
    dictation.escaped()
    dictation.interrupted()

    assert recorder.calls == ["start", "cancel"]
    assert states == ["hands_free", "idle"]
    assert transcriptions == []
    assert inserted == []


def test_dictation_key_does_not_stop_dedicated_hands_free():
    dictation, recorder, _, _, _, _ = make_dictation(hands_free_key="f13")
    dictation.hands_free_pressed()
    dictation.hands_free_released()

    dictation.pressed()
    dictation.interrupted()
    dictation.released()

    assert dictation._state == "hands_free"
    assert recorder.calls == ["start"]


def test_dedicated_key_disables_dictation_key_double_tap():
    jobs, states = [], []
    dictation, recorder, clock, _, _, _ = make_dictation(
        hands_free_key="f13", submit=jobs.append, on_state=states.append
    )
    dictation.pressed()
    clock.now = 0.1
    dictation.released()
    clock.now = 0.35
    dictation.pressed()

    assert states[-1] == "listening"
    assert "hands_free" not in states
    clock.now = 0.8
    dictation.released()
    assert recorder.calls == ["start", "stop", "start", "stop"]
    assert len(jobs) == 1


def test_dedicated_key_during_normal_hold_only_interrupts():
    states = []
    dictation, recorder, _, inserted, _, transcriptions = make_dictation(
        hands_free_key="f13", on_state=states.append
    )
    dictation.pressed()
    dictation.hands_free_pressed()
    dictation.interrupted()  # Linux reports both events for this key press.
    dictation.hands_free_released()
    dictation.released()

    assert recorder.calls == ["start", "cancel"]
    assert states == ["listening", "idle"]
    assert transcriptions == []
    assert inserted == []
