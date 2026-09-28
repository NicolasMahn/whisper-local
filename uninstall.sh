#!/usr/bin/env bash
# Reverses install.sh. Keeps ~/.config/whisper-local and the repo's .venv.
set -euo pipefail

if [[ $# -gt 1 || ( $# -eq 1 && $1 != --yes ) ]]; then
    echo "Usage: $0 [--yes]" >&2
    exit 2
fi
remove_engine=false
[[ ${1:-} == --yes ]] && remove_engine=true

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
app_id=io.github.nicolasmahn.WhisperLocal
uuid=whisper-local@nicolasmahn.github.io
data="${XDG_DATA_HOME:-$HOME/.local/share}"
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

# Quit a running instance through its own quit action.
gdbus call --session --dest "$app_id" --object-path /io/github/nicolasmahn/WhisperLocal \
    --method org.gtk.Actions.Activate quit '[]' '{}' > /dev/null 2>&1 || true

extension="$data/gnome-shell/extensions/$uuid"
extension_target="$(readlink -f -- "$repo/gnome-extension")"
if [[ -L "$extension" && "$(readlink -f -- "$extension")" != "$extension_target" ]]; then
    echo "Left $extension alone: it points elsewhere." >&2
elif [[ -e "$extension" && ! -L "$extension" ]]; then
    echo "Left $extension alone: it is not a link to this repo." >&2
else
    if ! gnome-extensions disable "$uuid" 2> /dev/null; then
        enabled="$(gsettings get org.gnome.shell enabled-extensions)"
        if [[ "$enabled" == *"'$uuid'"* ]]; then
            enabled="${enabled//", '$uuid'"/}"
            enabled="${enabled//"'$uuid', "/}"
            enabled="${enabled//"'$uuid'"/}"
            gsettings set org.gnome.shell enabled-extensions "$enabled"
        fi
    fi
    if [[ -L "$extension" ]]; then
        rm "$extension"
    fi
fi

rm -f "$data/applications/$app_id.desktop" \
    "$data/icons/hicolor/scalable/apps/$app_id.svg" \
    "$autostart/$app_id.desktop"

engine_dir="$data/whisper-local/whisper.cpp"
model="$data/whisper-local/models/ggml-large-v3-turbo-q8_0.bin"
if [[ -e "$engine_dir/whisper-server" || -e "$engine_dir/VERSION" || -e "$model" ]]; then
    if [[ "$remove_engine" != true ]]; then
        printf 'Remove the built-in Whisper binary and ~1 GB model? [y/N] ' >&2
        if IFS= read -r answer && [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]]; then
            remove_engine=true
        fi
    fi
    if [[ "$remove_engine" == true ]]; then
        rm -f -- "$engine_dir/whisper-server" "$engine_dir/VERSION" "$model"
        echo "Removed the built-in Whisper engine and model."
    else
        echo "Kept the built-in Whisper engine and model."
    fi
fi
if command -v update-desktop-database > /dev/null; then
    update-desktop-database -q "$data/applications"
fi

rule=/etc/udev/rules.d/60-whisper-local-keyboards.rules
if [[ -e "$rule" ]]; then
    echo "Removing keyboard access (asks for your password)."
    if pkexec /bin/sh -c \
        'rm -f -- "$1" && udevadm control --reload && udevadm trigger --subsystem-match=input --subsystem-match=misc --action=change && udevadm settle' \
        sh "$rule"; then
        # Another udev rule may grant access, so report any ACL that remains.
        device_root=/dev
        if command -v getfacl > /dev/null; then
            for device in "$device_root"/input/event* "$device_root"/uinput; do
                [[ -e "$device" ]] || continue
                if ! acl="$(getfacl -ncp -- "$device")"; then
                    echo "Could not verify device ACLs on $device." >&2
                elif grep -Eq "^user:${EUID}:(r|.w)" <<< "$acl"; then
                    echo "Keyboard access still granted on $device; check other udev rules or ACLs." >&2
                fi
            done
        else
            echo "Could not verify device ACLs: getfacl is unavailable." >&2
        fi
    else
        echo "Left $rule in place or could not refresh device access; remove it as root to revoke keyboard access." >&2
    fi
fi

echo "Uninstalled. Settings stay in ~/.config/whisper-local; delete that folder to forget them."
