# whisper-local

Hold a key, speak, let go, and the words appear where you are typing, like a
local Wispr Flow. The speech is recognised by Whisper large-v3-turbo on your
own computer, not in someone else's cloud.

On Linux it is a GNOME app, a top-bar extension and a small pill at the bottom
of the screen that shows the sound level while you speak.

## The speech engine

The app runs the model itself with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
v1.9.4, built with Vulkan. It starts `whisper-server` on 127.0.0.1 when it
launches and stops it when it quits, so the model stays loaded between
dictations. On the author's Radeon 8060S a dictation comes back in about
0.5 s, and the model takes about 0.9 GB of GPU memory. The first start can
take up to a minute while Vulkan compiles its shaders.

A Vulkan-capable GPU is recommended. Without one, whisper.cpp runs on the CPU,
which is much slower: a CPU engine took about 3 s per dictation on the same
machine.

Instead of the built-in engine you can choose "Server" and send each
recording to any OpenAI-compatible `/v1/audio/transcriptions` endpoint, on this
computer or another one. For example, Qwen3-ASR with vLLM:

    vllm serve Qwen/Qwen3-ASR-1.7B --served-model-name qwen3-asr

Set its URL and optional API key in the window, the model name in the settings.

## Installing

It needs GNOME Shell 49 on Wayland, GTK 4 and libadwaita with their Python
bindings, `wl-clipboard`, `portaudio`, `curl` and `podman` (Fedora Workstation
ships podman). It is developed on Fedora:

    sudo dnf install python3-gobject gtk4 libadwaita wl-clipboard portaudio
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ./install.sh

`install.sh` installs for you alone, without sudo. It builds whisper.cpp in a
throwaway Fedora 43 podman container (about 4 minutes) and downloads the model
(about 870 MB, a pinned revision, checked against its SHA-256). Both go into
`~/.local/share/whisper-local`. Whisper Local then appears in the app grid,
starts in the background at every login and adds a microphone icon to the top
bar. Its virtual environment uses the system Python, because PyGObject, GTK
and libadwaita come from the distribution. Run it again after pulling changes;
it skips the build and download when they are already done.

Without podman, build whisper.cpp by hand. You need `git`, `cmake`, a C++
compiler and the Vulkan headers, loader and shader compiler (`glslc`):

    git clone --depth 1 --branch v1.9.4 https://github.com/ggml-org/whisper.cpp.git
    cmake -S whisper.cpp -B whisper.cpp/build -DGGML_VULKAN=ON \
        -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release
    cmake --build whisper.cpp/build --target whisper-server
    mkdir -p ~/.local/share/whisper-local/whisper.cpp
    cp whisper.cpp/build/bin/whisper-server ~/.local/share/whisper-local/whisper.cpp/
    echo v1.9.4 > ~/.local/share/whisper-local/whisper.cpp/VERSION

Then run `./install.sh`; it sees the binary and does the rest.
`./uninstall.sh` removes it all and keeps your settings. It asks before
deleting the model and binary; `--yes` deletes them without asking.

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

- Hold the dictation key (Right Ctrl) and speak. A rising tone means it is
  listening. Let go: a falling tone, and the text is pasted a moment later.
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
- The window keeps the five most recent transcripts, so you can copy one again.
- The microphone is open only while recording, so the system's microphone
  indicator shows when it listens.

Opening the microphone takes about 80 ms, which can cut off the first
syllable; start speaking just after the tone.

## Settings

The window writes them to `~/.config/whisper-local/config.toml`,
creating it with mode `0600`. If you create the file by hand, run
`chmod 600 ~/.config/whisper-local/config.toml`. All settings are optional:

| Setting | Default | Meaning |
| --- | --- | --- |
| `engine` | `"builtin"` | `"builtin"` runs Whisper here; `"server"` uses the three settings below |
| `url` | `"http://localhost:8000/v1"` | Server only: the speech server, up to and including `/v1` |
| `api_key` | `""` | Server only: sent as a Bearer token; `WHISPER_LOCAL_API_KEY` is used when the setting is absent |
| `model` | `"qwen3-asr"` | Server only: the model name the server expects |
| `key` | `"right_ctrl"` | The dictation key |
| `hands_free_key` | `""` | Empty: double-tap the dictation key; or a different key |
| `language` | `""` | Empty: the model detects it; `"en"`, `"de"`, ... forces one |
| `sounds` | `true` | Play the tones |
| `vocabulary` | `[]` | Known names and terms sent as context, e.g. `["Nicolas Mahn", "Qwen", "DRY"]` |

Keys are `right_ctrl`, `right_alt`, `right_cmd` (Right Super), `right_shift`,
`f13`, `f14` and `f15`. Right-hand modifiers are rarely pressed alone, so
holding one does not get in the way of typing. Changing a key restarts the app.
Add or remove known words in the window. Both engines get them as context
for the recording. They nudge the model toward expected spelling; they do not
force any word into the transcript. Changes apply to the next recording
without a restart.

## Privacy and security

- With the built-in engine, audio never leaves your computer. whisper-server
  listens only on 127.0.0.1.
- With a server, audio goes only to the URL you configure, and nowhere else.
  Over plain HTTP on a network, anyone else on that network can read the audio,
  the transcripts and the API key. Use HTTPS, or keep the server on the same
  machine, if that matters to you.
- Keyboard access lets every program running as you read your
  keystrokes (see above).
- Transcripts are kept in memory only and never written to disk. The last one
  stays on the clipboard, replacing what was there.

## Development

    uv sync
    uv run pytest

The tests cover press, release, cancel, hands-free, audio levels, WAV encoding,
the config, the request to the server and the engine's start, restart and
stop. Check the GNOME window, extension
and pill by hand after changing them.

## License

MIT, see [LICENSE](LICENSE).
