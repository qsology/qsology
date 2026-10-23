#!/usr/bin/env bash
# Install the pinned uv and task binaries for CI, checking their hashes.
# With --deploy, also kustomize, kubeconform, helm, promtool and alloy for
# checking deploy/
#
# Used by both the GitHub and Forgejo workflows, so they run the same tools
# without needing forge specific setup actions. To upgrade, change the
# versions and hashes below, the hashes are on each project's release page.
set -euo pipefail

UV_VERSION="0.13.0"
TASK_VERSION="3.54.0"
KUSTOMIZE_VERSION="5.8.2"
KUBECONFORM_VERSION="0.8.0"
HELM_VERSION="4.3.0"
PROMETHEUS_VERSION="3.15.0"
ALLOY_VERSION="1.20.1"

BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

case "$(uname -m)" in
    x86_64 | amd64)
        UV_TARGET="x86_64-unknown-linux-gnu"
        UV_SHA256="1468ebd5a5541121837c5a2817b9972ba6090fa6caa3d142620850a47fb75154"
        TASK_ARCH="amd64"
        TASK_SHA256="680859dbb4d881a9c72d4d9a8f510825450849af8567deacd7302c01124416fb"
        KUSTOMIZE_SHA256="06af0a202c2b831207d0173f9c9cdb1b30abceca0747cb3fbb72792d26055c95"
        KUBECONFORM_SHA256="9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883"
        HELM_SHA256="86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb"
        PROMETHEUS_SHA256="2a542df32eac02ee17b9d844fb2aa1de00dafa5476579ba8a3ba862e9d572ea0"
        ALLOY_SHA256="451fe650e8277d22d69cb8db50bba809f581fe78decba7fce4027ef185457be9"
        ;;
    aarch64 | arm64)
        UV_TARGET="aarch64-unknown-linux-gnu"
        UV_SHA256="3ccfb6af6e242433eb552f7d9676abd5412c6595c497e990d9c8cb7b5bd4d2c3"
        TASK_ARCH="arm64"
        TASK_SHA256="d7189d439d4a6058e39e1b2acd7c8b27acd4856691b408f23b242e4294e1df5b"
        KUSTOMIZE_SHA256="0991957191951cb7dddd142403b5bb98a1fcd6378ba0079dddc1e2c309080a7f"
        KUBECONFORM_SHA256="1f53fc8e81258197a35e8603054162a5af1de8c5af13746c71ab680d9534ed87"
        HELM_SHA256="31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08"
        PROMETHEUS_SHA256="f1f90ec08e849d494ca66c611470afc50192f0355f1a61c33f2cbde02d067823"
        ALLOY_SHA256="396827d58158f181c98d5776c908348b38b4099f1d18a1e7554f2fda0fcb9203"
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

if [[ "${1:-}" == "--deploy" ]]; then
    fetch "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_${TASK_ARCH}.tar.gz" \
        "$KUSTOMIZE_SHA256" kustomize.tar.gz
    fetch "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-${TASK_ARCH}.tar.gz" \
        "$KUBECONFORM_SHA256" kubeconform.tar.gz
    fetch "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TASK_ARCH}.tar.gz" \
        "$HELM_SHA256" helm.tar.gz
    fetch "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-${TASK_ARCH}.tar.gz" \
        "$PROMETHEUS_SHA256" prometheus.tar.gz
    fetch "https://github.com/grafana/alloy/releases/download/v${ALLOY_VERSION}/alloy-linux-${TASK_ARCH}.zip" \
        "$ALLOY_SHA256" alloy.zip
    tar -xzf "$work/kustomize.tar.gz" -C "$work" kustomize
    tar -xzf "$work/kubeconform.tar.gz" -C "$work" kubeconform
    tar -xzf "$work/helm.tar.gz" -C "$work" "linux-${TASK_ARCH}/helm"
    tar -xzf "$work/prometheus.tar.gz" -C "$work" \
        "prometheus-${PROMETHEUS_VERSION}.linux-${TASK_ARCH}/promtool"
    # No unzip on every runner, Python's zipfile does the same
    python3 -m zipfile -e "$work/alloy.zip" "$work"
    install -m 0755 "$work/alloy-linux-${TASK_ARCH}" "$work/alloy"
    install -m 0755 "$work/kustomize" "$work/kubeconform" \
        "$work/linux-${TASK_ARCH}/helm" \
        "$work/prometheus-${PROMETHEUS_VERSION}.linux-${TASK_ARCH}/promtool" \
        "$work/alloy" "$BIN_DIR/"
fi

# Later steps find them on the PATH, on GitHub and Forgejo alike
if [[ -n "${GITHUB_PATH:-}" ]]; then
    echo "$BIN_DIR" >> "$GITHUB_PATH"
fi
"$BIN_DIR/uv" --version
"$BIN_DIR/task" --version
