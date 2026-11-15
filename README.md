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

Instead of the built-in engine you can choose "Server" and send each recording
to a chat model that hears, on this computer or another one, through an
OpenAI-compatible `/v1/chat/completions`. The author runs `qwen3-omni-30b` on
[local_llm](https://github.com/NicolasMahn/local_llm). A dictation comes back in
about 1 to 2 s, 87 s of speech in 8 to 20 s. Set the URL and API key in the
window, the model name in the settings. A server that only offers
`/v1/audio/transcriptions` no longer works here.

The model also gets a screenshot of the window you dictate into, taken as you
press the key, so it can spell what is on your screen: it heard a spoken "Jev"
as "Jeff" until it could read the name. The top-bar extension takes it, because
on Wayland only GNOME Shell can capture a window without asking each time.

Text on a screen can be written by anyone, and the answer gets typed into your
window. In tests, a web page's text could otherwise make the model write out a
command. So the recording is also sent without the screenshot, which no picture
can influence, and that answer is what gets typed. From the screenshot's answer,
only words it writes the same or nearly the same way are swapped in: "Jeff"
becomes "Jev", "whisper local" becomes "whisper-local". Added words and symbols
are dropped. A word that changes letters is swapped only for a name (capitalized
or a known word), and never when that would change its meaning: digits stay
("644" never becomes "664"), opposite prefixes stay ("enable" never becomes
"disable"), and short words stay ("now" never becomes "not"). What remains is
that a picture can change a name into a similar one. Line breaks and control
characters are removed, so an answer cannot press Enter in a terminal.

Both requests share the model, so the screenshot's is kept out of the way: the
audio's request starts at once, without waiting for the screenshot; the
screenshot's answer is cut off when it grows far longer than the audio's, which
happens when the model copies text off the screen; and the paste waits for it
only briefly after the audio's answer. Answers are streamed, so a request cut
off this way stops on the server too.

With an admin key, the app starts a model that is off on a local_llm server: when
the app starts and when you dictate. Starting takes minutes; the pill says so,
and what you dictate in that time is not kept, because pasting it minutes later
could put it in the wrong window. If the model only fits after stopping others, the app names them and leaves them
running, because they may be someone's work. Without an admin key it only reports
that the model is off.

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

Log out and back in once, so GNOME Shell loads the new extension. The same goes
after pulling changes to it: until then, dictation works without screenshots. On a
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

A recording without sound worth hearing types nothing, and neither does an answer
made only of your known words: models tend to invent those from silence. So saying
just "Qwen" and nothing else types nothing.

Opening the microphone takes about 80 ms, which can cut off the first
syllable; start speaking just after the tone.

## Settings

The window writes them to `~/.config/whisper-local/config.toml`,
creating it with mode `0600`. If you create the file by hand, run
`chmod 600 ~/.config/whisper-local/config.toml`. All settings are optional:

| Setting | Default | Meaning |
| --- | --- | --- |
| `engine` | `"builtin"` | `"builtin"` runs Whisper here; `"server"` uses the settings below |
| `url` | `"http://localhost:8000/v1"` | Server only: the speech server, up to and including `/v1` |
| `api_key` | `""` | Server only: sent as a Bearer token; `WHISPER_LOCAL_API_KEY` is used when the setting is absent |
| `admin_key` | `""` | Server only: lets the app start the model on a local_llm server. Sent only to `/admin/`, and only over HTTPS or on this computer |
| `model` | `"qwen3-omni-30b"` | Server only: the model name the server expects |
| `screenshots` | `true` | Server only: send a screenshot of the window you dictate into |
| `key` | `"right_ctrl"` | The dictation key |
| `hands_free_key` | `""` | Empty: double-tap the dictation key; or a different key |
| `language` | `""` | Empty: the model detects it; `"en"`, `"de"`, ... forces one |
| `sounds` | `true` | Play the tones |
| `keep_dictations` | `false` | Save every recording, its screenshot and the transcripts, for measuring and tuning later |
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
  the screenshots, the transcripts and the API key. Use HTTPS, or keep the server on the same
  machine, if that matters to you.
- With a server and `screenshots` on, each dictation also sends a picture of the
  focused window to that URL, whatever it shows: a password manager, a bank, a
  private chat. Turn it off in the window when that matters. Screenshots are
  kept in memory only, unless `keep_dictations` is on. Only GNOME Shell may
  deliver one to the app; another
  program pretending to be the app could ask the extension for one, but programs
  running as you can already read your keystrokes (see above).
- Keyboard access lets every program running as you read your
  keystrokes (see above).
- Unless `keep_dictations` is on, recordings, screenshots and transcripts are
  kept in memory only and never written to disk. The last transcript stays on
  the clipboard, replacing what was there.
- With `keep_dictations` on, each dictation gets its own folder in
  `~/.local/share/whisper-local/dictations`, readable only by you:
  `audio.wav`, `screenshot.png` when one was sent, and `dictation.json` with
  the app's version and commit, the settings (never the keys), the model's
  answers with and without the screenshot, what was typed, any error and how
  long each step took. Folders are written after the paste, so keeping them
  never delays the text. Those screenshots show whatever was on
  screen, so treat the folder like the passwords and chats it may contain.
  Nothing is ever deleted; remove old folders yourself.

## Development

    uv sync
    uv run pytest

The tests cover press, release, cancel, hands-free, audio levels, WAV encoding,
the config, the requests to the server, starting a model on it, and the engine's
start, restart and stop. Check the GNOME window, extension
and pill by hand after changing them.

## License

MIT, see [LICENSE](LICENSE).
