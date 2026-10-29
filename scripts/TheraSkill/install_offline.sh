#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install_offline.sh [--target DIRECTORY] [--no-retrieval]

Installs the application and bundled Python runtime without network access.
Knowledge and simulation data are installed separately when needed.
EOF
}

BUNDLE_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${THERASKILL_OFFLINE_TARGET:-$HOME/TheraSkillAgent}"
INSTALL_RETRIEVAL=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --no-retrieval) INSTALL_RETRIEVAL=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -m)" != x86_64 ]]; then
  echo 'This bundle requires Linux x86_64' >&2
  exit 2
fi
if [[ ! -f /etc/os-release ]]; then
  echo 'Cannot identify the Linux distribution' >&2
  exit 2
fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 22.04 ]]; then
  echo "This bundle was built for Ubuntu 22.04 x86_64; found ${PRETTY_NAME:-unknown}" >&2
  exit 2
fi
if ! command -v tar >/dev/null 2>&1; then
  echo 'The system tar command is required to unpack the included Python runtime' >&2
  exit 2
fi

TARGET="$(realpath -m -- "$TARGET")"
mkdir -p "$TARGET"
RUNTIME="$TARGET/.runtime"
RUNTIME_ARCHIVE="$BUNDLE_DIR/runtime/python-3.12.7-linux-x86_64.tar.gz"
if [[ ! -f "$RUNTIME/.theraskill-ready" ]]; then
  if [[ -e "$RUNTIME" ]]; then
    echo "Incomplete runtime at $RUNTIME; remove it after checking its contents, then retry" >&2
    exit 2
  fi
  [[ -f "$RUNTIME_ARCHIVE" ]] || { echo 'Bundled Python runtime is missing' >&2; exit 2; }
  mkdir "$RUNTIME"
  tar -xzf "$RUNTIME_ARCHIVE" -C "$RUNTIME"
  "$RUNTIME/bin/python" "$RUNTIME/bin/conda-unpack"
  touch "$RUNTIME/.theraskill-ready"
fi
export LD_LIBRARY_PATH="$RUNTIME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
RUNTIME_PY="$RUNTIME/bin/python"
RUNTIME_BIN="$RUNTIME/bin"
WHEELHOUSE="$BUNDLE_DIR/wheelhouse"
PROJECT_WHEEL="$(find "$BUNDLE_DIR/packages" -maxdepth 1 -type f -name 'theraskill_agent-*.whl' -print -quit)"
if [[ -z "$PROJECT_WHEEL" ]]; then
  echo "The bundle does not contain the TheraSkillAgent wheel" >&2
  exit 2
fi

PIP_ARGS=("$RUNTIME_PY" -m pip install --no-index --disable-pip-version-check --find-links "$WHEELHOUSE")
if [[ "$INSTALL_RETRIEVAL" == 1 ]]; then
  "${PIP_ARGS[@]}" "${PROJECT_WHEEL}[retrieval]"
else
  "${PIP_ARGS[@]}" "$PROJECT_WHEEL"
fi
"$RUNTIME_PY" -m pip check

"$RUNTIME_BIN/theraskill-agent" --project "$TARGET" project init "$TARGET" >/dev/null
mkdir -p "$TARGET/docs"
cp -a "$BUNDLE_DIR/docs/." "$TARGET/docs/"
cp "$BUNDLE_DIR/docs/deployment_offline.en.md" "$TARGET/OFFLINE_DEPLOYMENT.en.md"
cp "$BUNDLE_DIR/docs/deployment_offline.zh.md" "$TARGET/OFFLINE_DEPLOYMENT.zh.md"
chmod 600 "$TARGET/configs/theraskill/providers.local.example.yaml"

cat <<EOF

Offline installation completed.
Project: $TARGET
Provider configuration: $TARGET/configs/theraskill/providers.local.yaml

Copy the template, edit the LLM and embedding blocks with the existing vLLM
OpenAI-compatible URLs and model names, then start the workbench:

  cp "$TARGET/configs/theraskill/providers.local.example.yaml" \
     "$TARGET/configs/theraskill/providers.local.yaml"
  chmod 600 "$TARGET/configs/theraskill/providers.local.yaml"
  "$BUNDLE_DIR/start_offline.sh" --target "$TARGET" --host 0.0.0.0 --port 5176

Knowledge and simulation archives are separate downloads. Install them later
with the knowledge/simulation --archive commands in OFFLINE_DEPLOYMENT.en.md.
Illustrated guides: $TARGET/docs/user_guide.en.md and user_guide.zh.md.
EOF
