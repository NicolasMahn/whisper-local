#!/usr/bin/env bash
# Installs Whisper Local for the current user. Safe to run again after changes.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ ! "$repo" =~ ^[a-zA-Z0-9_./\ -]+$ ]]; then
    echo "Cannot install from this path: use only letters, numbers, spaces, _, -, . and /." >&2
    exit 1
fi
app_id=io.github.nicolasmahn.WhisperLocal
uuid=whisper-local@nicolasmahn.github.io
data="${XDG_DATA_HOME:-$HOME/.local/share}"
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
uv="$(command -v uv || echo "$HOME/.local/bin/uv")"

cd "$repo"
if [[ ! -d .venv ]]; then
    # PyGObject, GTK and libadwaita come from Fedora, not from PyPI.
    "$uv" venv -q -p /usr/bin/python3 --system-site-packages
fi
"$uv" sync -q

echo "Installing the built-in Whisper engine..."
"$repo/scripts/build-whisper-cpp.sh"
"$repo/scripts/download-model.sh"

# Desktop files need an absolute Exec path; quoted in case the repo path has spaces.
exec_path="\"$repo/.venv/bin/whisper-local\""
mkdir -p "$data/applications" "$data/icons/hicolor/scalable/apps" "$autostart"
sed "s|^Exec=whisper-local|Exec=$exec_path|" "data/$app_id.desktop" \
    > "$data/applications/$app_id.desktop"
sed "s|^Exec=whisper-local|Exec=$exec_path|" "data/$app_id-autostart.desktop" \
    > "$autostart/$app_id.desktop"
cp "data/$app_id.svg" "$data/icons/hicolor/scalable/apps/$app_id.svg"
if command -v update-desktop-database > /dev/null; then
    update-desktop-database -q "$data/applications"
fi

extension="$data/gnome-shell/extensions/$uuid"
extension_target="$(readlink -f -- "$repo/gnome-extension")"
extension_owned=true
if [[ -L "$extension" ]]; then
    if [[ "$(readlink -f -- "$extension")" != "$extension_target" ]]; then
        echo "Left $extension alone: it points elsewhere." >&2
        extension_owned=false
    fi
elif [[ -e "$extension" ]]; then
    echo "Left $extension alone: it is not a link to this repo." >&2
    extension_owned=false
else
    mkdir -p "$(dirname "$extension")"
    ln -s "$repo/gnome-extension" "$extension"
fi
# GNOME Shell only knows extensions that existed when it started, so
# `gnome-extensions enable` fails for a new one; the setting works either way.
if [[ "$extension_owned" == true ]] && ! gnome-extensions enable "$uuid" 2> /dev/null; then
    enabled="$(gsettings get org.gnome.shell enabled-extensions)"
    if [[ "$enabled" != *"'$uuid'"* ]]; then
        if [[ "$enabled" == "@as []" || "$enabled" == "[]" ]]; then
            enabled="['$uuid']"
        else
            enabled="${enabled%]}, '$uuid']"
        fi
        gsettings set org.gnome.shell enabled-extensions "$enabled"
    fi
fi

echo "Installed. Whisper Local is in the app grid and starts with every login."
echo "The top-bar icon appears after you log out and back in (Wayland loads new extensions only then)."
rule=/etc/udev/rules.d/60-whisper-local-keyboards.rules
if [[ ! -e "$rule" ]]; then
    echo "Keyboard access: open Whisper Local and press Allow."
fi
