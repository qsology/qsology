#!/usr/bin/env bash
# Install the pinned uv and task binaries for CI, checking their hashes
#
# Used by both the GitHub and Forgejo workflows, so they run the same tools
# without needing forge specific setup actions. To upgrade, change the
# versions and hashes below, the hashes are on each project's release page.
set -euo pipefail

UV_VERSION="0.13.0"
TASK_VERSION="3.54.0"

BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

case "$(uname -m)" in
    x86_64 | amd64)
        UV_TARGET="x86_64-unknown-linux-gnu"
        UV_SHA256="1468ebd5a5541121837c5a2817b9972ba6090fa6caa3d142620850a47fb75154"
        TASK_ARCH="amd64"
        TASK_SHA256="680859dbb4d881a9c72d4d9a8f510825450849af8567deacd7302c01124416fb"
        ;;
    aarch64 | arm64)
        UV_TARGET="aarch64-unknown-linux-gnu"
        UV_SHA256="3ccfb6af6e242433eb552f7d9676abd5412c6595c497e990d9c8cb7b5bd4d2c3"
        TASK_ARCH="arm64"
        TASK_SHA256="d7189d439d4a6058e39e1b2acd7c8b27acd4856691b408f23b242e4294e1df5b"
        ;;
    *)
        echo "Unsupported architecture $(uname -m)" >&2
        exit 1
        ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fetch() {
    # fetch URL SHA256 FILE
    curl --proto '=https' --tlsv1.2 -sSfL --retry 3 -o "$work/$3" "$1"
    echo "$2  $work/$3" | sha256sum --check --strict -
}

fetch "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TARGET}.tar.gz" \
    "$UV_SHA256" uv.tar.gz
fetch "https://github.com/go-task/task/releases/download/v${TASK_VERSION}/task_linux_${TASK_ARCH}.tar.gz" \
    "$TASK_SHA256" task.tar.gz

mkdir -p "$BIN_DIR"
tar -xzf "$work/uv.tar.gz" -C "$work"
install -m 0755 "$work/uv-${UV_TARGET}/uv" "$work/uv-${UV_TARGET}/uvx" "$BIN_DIR/"
tar -xzf "$work/task.tar.gz" -C "$work" task
install -m 0755 "$work/task" "$BIN_DIR/"

# Later steps find them on the PATH, on GitHub and Forgejo alike
if [[ -n "${GITHUB_PATH:-}" ]]; then
    echo "$BIN_DIR" >> "$GITHUB_PATH"
fi
"$BIN_DIR/uv" --version
"$BIN_DIR/task" --version
