#!/usr/bin/env bash
set -euo pipefail

name=ggml-large-v3-turbo-q8_0.bin
url=https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3-turbo-q8_0.bin
expected_size=874188075
expected_sha256=317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1
directory="${XDG_DATA_HOME:-$HOME/.local/share}/whisper-local/models"
model="$directory/$name"
partial="$directory/.$name.part"

verify() {
    local size checksum
    size="$(wc -c < "$1")"
    [[ "$size" == "$expected_size" ]] || return 1
    checksum="$(sha256sum -- "$1")"
    [[ "${checksum%% *}" == "$expected_sha256" ]]
}

if [[ -f "$model" ]] && verify "$model"; then
    echo "Whisper model is already installed and verified."
    exit 0
fi

if ! command -v curl > /dev/null; then
    echo "curl is required to download the Whisper model." >&2
    exit 1
fi

mkdir -p "$directory"
echo "Downloading Whisper large-v3-turbo model (874188075 bytes)..."
if ! curl --fail --location --continue-at - --output "$partial" "$url"; then
    echo "Model download failed. Run install.sh again to resume it." >&2
    exit 1
fi

if ! verify "$partial"; then
    rm -f -- "$partial"
    echo "Model download failed size or SHA-256 verification; the model was not installed." >&2
    exit 1
fi

mv -f -- "$partial" "$model"
echo "Installed and verified Whisper model at $model."
