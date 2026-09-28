#!/usr/bin/env bash
set -euo pipefail

version=v1.9.4
destination="${XDG_DATA_HOME:-$HOME/.local/share}/whisper-local/whisper.cpp"

if [[ -x "$destination/whisper-server" && -f "$destination/VERSION" &&
      "$(<"$destination/VERSION")" == "$version" ]]; then
    echo "whisper.cpp $version is already installed."
    exit 0
fi

if ! command -v podman > /dev/null; then
    echo "podman is required to build whisper.cpp $version." >&2
    cat >&2 <<EOF
To build by hand, clone https://github.com/ggml-org/whisper.cpp.git at $version,
then run:
  cmake -S whisper.cpp -B whisper.cpp/build -DGGML_VULKAN=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build whisper.cpp/build --target whisper-server
Copy whisper.cpp/build/bin/whisper-server to $destination/whisper-server
and write $version to $destination/VERSION.
EOF
    exit 1
fi

mkdir -p "$destination"
staging="$(mktemp -d "$destination/.build.XXXXXXXX")"
trap 'rm -rf -- "$staging"' EXIT

echo "Building whisper.cpp $version with Vulkan in Fedora 43 (this may take a while)..."
if ! podman run --rm --volume "$staging:/output:Z" \
    registry.fedoraproject.org/fedora:43 bash -euc '
        dnf install -y --setopt=install_weak_deps=False \
            git cmake gcc-c++ make vulkan-headers vulkan-loader-devel \
            glslc glslang spirv-headers-devel
        git clone --depth 1 --branch v1.9.4 \
            https://github.com/ggml-org/whisper.cpp.git /tmp/whisper.cpp
        cmake -S /tmp/whisper.cpp -B /tmp/whisper.cpp/build \
            -DGGML_VULKAN=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release
        cmake --build /tmp/whisper.cpp/build --target whisper-server -j "$(nproc)"
        cp /tmp/whisper.cpp/build/bin/whisper-server /output/whisper-server
    '; then
    echo "whisper.cpp $version build failed. Check the podman output above." >&2
    exit 1
fi

if [[ ! -s "$staging/whisper-server" ]]; then
    echo "whisper.cpp $version build failed: whisper-server was not produced." >&2
    exit 1
fi
chmod +x "$staging/whisper-server"
mv -f -- "$staging/whisper-server" "$destination/whisper-server"
printf '%s\n' "$version" > "$staging/VERSION"
mv -f -- "$staging/VERSION" "$destination/VERSION"
echo "Installed whisper.cpp $version to $destination."
