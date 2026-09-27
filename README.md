# whisper-local

Hold a key, speak, let go, and the words appear where you are typing, like a
local Wispr Flow. The speech is recognised by Qwen3-ASR on your own machine or
another one on your network, not in someone else's cloud.

On Linux it is a GNOME app, a top-bar extension and a small pill at the bottom
of the screen that shows the sound level while you speak. On macOS it is a
native menu bar app with the same pill; see [macos/README.md](macos/README.md).
The macOS app is experimental: it has not yet been built on a Mac, only its
core logic is tested (on Linux).

## The speech server

whisper-local does not run the model itself. It sends each recording to any
OpenAI-compatible `/v1/audio/transcriptions` endpoint, for example vLLM:

    vllm serve Qwen/Qwen3-ASR-1.7B --served-model-name qwen3-asr

The default server is `http://localhost:8000/v1`. To use another machine, or a
server that wants a key, set `url` and `api_key` in the Linux window or in
`~/.config/whisper-local/config.toml`. Leave the API key field empty for a
server without a key. On the author's GPU, 15 s of speech comes back as text
in about a second.

## Linux

It needs GNOME Shell 49 on Wayland, GTK 4 and libadwaita with their Python
bindings, `wl-clipboard` and `portaudio`. It is developed on Fedora:

    sudo dnf install python3-gobject gtk4 libadwaita wl-clipboard portaudio
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ./install.sh

`install.sh` installs for you alone, without sudo. Whisper Local appears in the
app grid, starts in the background at every login and adds a microphone icon to
the top bar. Its virtual environment uses the system Python, because PyGObject,
GTK and libadwaita come from the distribution. Run it again after pulling
changes; `./uninstall.sh` removes it all and keeps your settings.

Log out and back in once, so GNOME Shell loads the new extension. On a
computer with only one user account, GNOME hides Log Out from the system menu;
run `gnome-session-quit --logout` instead.

Wayland lets no program watch global keys or type into other apps, so
whisper-local reads the keyboards from `/dev/input` and pastes through a
virtual keyboard on `/dev/uinput`. The first time, the window asks for this
with an Allow button. It takes your password and installs one udev rule file,
`/etc/udev/rules.d/60-whisper-local-keyboards.rules`, which gives whoever sits
at this computer access to keyboards and `/dev/uinput` through systemd's
`uaccess` tag. It works at once, without logging out. The price: every program
running as you can then read every keystroke, passwords included.
`./uninstall.sh` removes the rule.

The text is pasted by typing Shift+Insert, which works in terminals as well as
in normal text fields.

## Using it

- Hold the dictation key (Right Ctrl on Linux, Right Option on macOS) and
  speak. A rising tone means it is listening. Let go: a falling tone, and the
  text is pasted a moment later.
- Presses shorter than 0.3 s are ignored, so a tap sends nothing.
- Pressing any other key while holding cancels the recording, so Right Ctrl+C
  and other shortcuts keep working. Esc cancels too.
- For hands-free speech, double-tap the dictation key, or choose a separate
  hands-free key in settings (that turns double-tap off). Other keys do not
  cancel it, so you can type or walk away. Tap the key again to finish, or
  press Esc to throw it away.
- The pill shows the sound level while listening, a busy animation while the
  text is being prepared, and the reason when something fails. Failures also
  play two low tones.
- Text arrives in the order it was spoken and stays on the clipboard afterwards.
- The window (Linux) and the menu (macOS) keep the five most recent
  transcripts, so you can copy one again.
- The microphone is open only while recording, so the system's microphone
  indicator shows when it listens.

Opening the microphone takes about 80 ms, which can cut off the first
syllable; start speaking just after the tone.

## Settings

On Linux the window writes them to `~/.config/whisper-local/config.toml`,
creating it with mode `0600`. If you create the file by hand, run
`chmod 600 ~/.config/whisper-local/config.toml`. All settings are optional:

| Setting | Default | Meaning |
| --- | --- | --- |
| `url` | `"http://localhost:8000/v1"` | The speech server, up to and including `/v1` |
| `api_key` | `""` | Sent as a Bearer token; `WHISPER_LOCAL_API_KEY` is used when the setting is absent |
| `model` | `"qwen3-asr"` | The model name the server expects |
| `key` | `"right_ctrl"` | The dictation key |
| `hands_free_key` | `""` | Empty: double-tap the dictation key; or a different key |
| `language` | `""` | Empty: the model detects it; `"en"`, `"de"`, ... forces one |
| `sounds` | `true` | Play the tones |
| `vocabulary` | `[]` | Known names and terms sent as context, e.g. `["Nicolas Mahn", "Qwen", "DRY"]` |

Keys are `right_ctrl`, `right_alt`, `right_cmd` (Right Super), `right_shift`,
`f13`, `f14` and `f15`. Right-hand modifiers are rarely pressed alone, so
holding one does not get in the way of typing. Changing a key restarts the app.
Add or remove known words in the Linux window. They nudge the model toward
expected spelling; they do not force any word into the transcript. Changes
apply to the next recording without a restart.

## Privacy and security

- Audio goes only to the server URL you configure, and nowhere else.
- Over plain HTTP on a network, anyone else on that network can read the audio,
  the transcripts and the API key. Use HTTPS, or keep the server on the same
  machine, if that matters to you.
- On Linux, keyboard access lets every program running as you read your
  keystrokes (see above).
- Transcripts are kept in memory only and never written to disk. The last one
  stays on the clipboard, replacing what was there.

## Development

    uv sync
    uv run pytest

The tests cover press, release, cancel, hands-free, audio levels, WAV encoding,
the config and the request to the server. Check the GNOME window, extension
and pill by hand after changing them. `macos/check-on-linux.sh` compiles and
tests the macOS core in the Swift container with Podman.

## License

MIT, see [LICENSE](LICENSE).
